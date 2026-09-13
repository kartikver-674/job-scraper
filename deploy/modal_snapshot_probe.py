"""Can Modal's GPU memory snapshot skip Ollama's 86 seconds of init?

WHY THIS EXPERIMENT EXISTS
--------------------------
The T4 probe measured a 105.60s cold profile for the Sarthak résumé, and
the breakdown says almost all of it is one-time initialisation:

    reading 5.23GB off the Volume          0.98s   (5.34 GB/s)
    weights -> VRAM (warm-cache reload)    4.46s
    ollama serve ready                     7.14s
    real prefill, 1100 tok @ 1145 tok/s    0.96s
    real generation, 359 tok               9.65s
    employment call (all of it)            4.33s
    -------------------------------------------
    one-time init inside load_duration    52.43s
    one-time init billed to prompt_eval   33.73s
                                        = 86.16s   <- 81.6% of the cold run

The tell is that the FIRST call prefills 1100 tokens at 31.7 tok/s and the
SECOND prefills 1156 at 1143.6 tok/s — 36x, same GPU, same container,
neither cached, no shared prefix. The first prompt_eval is not measuring
prefill; it is measuring CUDA kernel/JIT warmup.

Disk is not the problem and the VRAM transfer is not the problem. Strip
the init and the profile is ~15s, which beats the M1's 37.4s outright.

So: exactly the workload Modal says snapshots are for — "skipping past
work like imports and JIT compilation" — and exactly the workload they do
NOT claim to have tested.

WHAT IS GENUINELY UNCERTAIN, AND WHY WE MEASURE RATHER THAN ASSUME
------------------------------------------------------------------
Every published Modal GPU-snapshot success is a PyTorch process that owns
its own CUDA context in-process. Ollama is neither:

  1. The CUDA context lives in a CHILD process. `ollama serve` (Go) spawns
     `llama-server` (C++), and that child holds the GPU. Modal's own
     write-up says it enumerates "all active CUDA sessions and their
     associated PIDs", which is promising — but promising is not tested.
  2. Ollama is a SERVER holding a listening socket on 127.0.0.1:11434.
     Restoring a listening socket is the part of checkpoint/restore that
     most often does not survive.
  3. It is not PyTorch. modal-labs/modal-client#4132 reports restore
     segfaulting on ~60% of attempts with CTranslate2 — a C++ CUDA library,
     which is the closest published analogue to llama.cpp.
  4. GPU snapshots need the CUDA checkpoint/restore API on driver branch
     570/575. The documented example uses an A10; whether Modal's T4 fleet
     carries a supporting driver is unstated.

Any of those can sink it. This probe is built so that each one FAILS
LOUDLY AND SEPARATELY instead of producing a confusing number.

DEPLOY IT — DO NOT `modal run` IT
---------------------------------
Modal refuses memory snapshots on ephemeral apps:

    Memory snapshots are disabled for ephemeral apps.
    Deploy your app with `modal deploy` to enable memory snapshots.

`modal run` creates an ephemeral app, so running this experiment that way
silently disables the thing it measures — the snapshot arm degrades into a
second control and the verdict reads "no difference" for a reason that has
nothing to do with Ollama. A wrong number that looks right is worse than an
error, so there is no `local_entrypoint` here on purpose.

    modal deploy deploy/modal_snapshot_probe.py
    python deploy/run_snapshot_probe.py

Nothing in production changes. Same T4, same Ollama 0.34.0, same qwen3:8b
digest, same prompts, schema and options — all imported, never restated.
"""

import json
import os
import time
import urllib.request

import modal

# Container-side helpers only, from a module with no Modal objects in it.
# NOT imported from modal_gpu_probe: Modal ships only the ENTRYPOINT module
# into the container, so that import died there with ModuleNotFoundError and
# every container crash-looped. See ollama_probe_lib's docstring.
from ollama_probe_lib import (
    EXPECTED_DIGEST_PREFIX,
    MODEL,
    OLLAMA_VERSION,
    metrics_row as _metrics_row,
    ollama_alive as _ollama_alive,
    resident as _resident,
    show as _show,
    start_ollama,
)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

app = modal.App("sweep-ollama-snapshot-probe")

# Same volume NAME as the GPU probe, so the already-pulled qwen3:8b is
# reused rather than downloaded again.
model_volume = modal.Volume.from_name("sweep-ollama-models",
                                      create_if_missing=True)

# Defined here rather than imported, for the same reason as the helpers: a
# module that builds Modal objects at import time is a module that can fail
# to import inside the container.
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("curl", "ca-certificates", "zstd")
    .run_commands(
        "curl -fsSL "
        "'https://ollama.com/download/ollama-linux-amd64.tar.zst"
        f"?version={OLLAMA_VERSION}' "
        "| zstd -d | tar -xf - -C /usr"
    )
    # The production modules and the shared helpers, all under one path on
    # PYTHONPATH. The prompts, schema and options are imported from these,
    # never restated.
    .add_local_file(os.path.join(REPO_ROOT, "inference.py"),
                    "/app/inference.py", copy=True)
    .add_local_file(os.path.join(REPO_ROOT, "local_extract.py"),
                    "/app/local_extract.py", copy=True)
    .add_local_file(os.path.join(HERE, "ollama_probe_lib.py"),
                    "/app/ollama_probe_lib.py", copy=True)
    .env({"PYTHONPATH": "/app"})
)

# The measured no-snapshot baseline this experiment is trying to beat.
BASELINE_COLD_S = 105.60
BASELINE_LOAD_S = 56.89
BASELINE_PREFILL_S = 34.69
BASELINE_ONE_TIME_S = 86.16


class _Probe:
    """Shared body for both arms, so the only difference is the snapshot.

    Subclassed twice below because Modal's snapshot setting lives on the
    decorator: a control arm that shares this code is the only way to know
    the comparison is measuring snapshots and not some other difference.
    """

    def boot(self):
        """Start Ollama and force qwen3:8b into VRAM.

        In the snapshot arm this runs in @modal.enter(snap=True), so
        everything it does is what we are asking Modal to freeze: the
        server, the weights in VRAM, and — via one real generation — the
        CUDA kernels that cost 33.73s the first time they are used.
        """
        import uuid

        # A fingerprint of THIS boot. If a later container reports the same
        # id, its state came from the snapshot rather than from running
        # boot() again — which is the only way to tell a genuine restore
        # from Modal simply handing back a container that is still warm.
        self.boot_id = uuid.uuid4().hex[:12]
        self.boot_epoch = time.time()

        started = time.perf_counter()
        self.process = start_ollama()
        self.ollama_ready_s = time.perf_counter() - started

        os.environ["SWEEP_MODEL_KEEP_ALIVE"] = "-1"
        os.environ["OLLAMA_HOST"] = "http://127.0.0.1:11434"

        import sys

        sys.path.insert(0, "/app")
        import inference
        import local_extract as le

        # One real production call, so the JIT/kernel warmup happens BEFORE
        # the snapshot rather than in the user's first request. Deliberately
        # the real prompt through the real transport, not a toy string —
        # a toy prompt warms different kernels.
        warm_started = time.perf_counter()
        inference.LocalOllama().generate(
            le.model_name(), le.FIELDS_PROMPT.format(text="warmup"),
            le.FIELDS_SCHEMA, 300)
        self.warmup_s = time.perf_counter() - warm_started
        self.booted_vram = _resident().get("size_vram", 0)
        # THE NUMBER THE EXPERIMENT IS ABOUT. This is the one-time cost a
        # snapshot would let us skip: server start plus the CUDA/kernel
        # warmup that the T4 probe measured at ~86s.
        self.boot_cost_s = self.ollama_ready_s + self.warmup_s
        print(f"[boot {self.boot_id}] ollama ready {self.ollama_ready_s:.2f}s, "
              f"warmup generation {self.warmup_s:.2f}s, "
              f"TOTAL BOOT COST {self.boot_cost_s:.2f}s, "
              f"size_vram {self.booted_vram:,}", flush=True)

    def measure(self, resume_text, arm):
        """The real profile, plus what survived the restore."""
        import sys

        sys.path.insert(0, "/app")
        import inference
        import local_extract as le

        first_touch = time.perf_counter()

        # --- what actually came back? -------------------------------------
        alive = _ollama_alive()
        resident = _resident()
        vram = resident.get("size_vram", 0)
        restarted = False
        if not alive:
            # The informative failure: CRIU did not bring the listener back.
            print("[restore] ollama is NOT answering — restarting it. The "
                  "snapshot did not preserve the server.", flush=True)
            self.process = start_ollama()
            restarted = True
            resident = _resident()
            vram = resident.get("size_vram", 0)

        readiness_s = time.perf_counter() - first_touch
        print(f"[restore] ollama_alive={alive} restarted={restarted} "
              f"size_vram={vram:,} readiness={readiness_s:.2f}s", flush=True)

        captured = []

        class Recording(inference.LocalOllama):
            def generate(self, model, prompt, schema, timeout):
                started = time.perf_counter()
                try:
                    return super().generate(model, prompt, schema, timeout)
                finally:
                    captured.append({
                        "wall": time.perf_counter() - started,
                        "num_ctx": inference.ctx_for(prompt),
                        "prompt_chars": len(prompt),
                        "metrics": self.last_metrics,
                    })

        inference.provider = lambda backend=None, **kw: Recording(**kw)
        model = inference.model_name()

        started = time.perf_counter()
        checked, rows, decision = le.read(model, resume_text)
        profile_s = time.perf_counter() - started

        calls = [_metrics_row(name, captured[i]["wall"], captured[i]["num_ctx"],
                              captured[i]["prompt_chars"],
                              captured[i]["metrics"])
                 for i, name in enumerate(("fields", "employment"))]
        print(f"\n{arm.upper()} — first profile in a restored container")
        for row in calls:
            _show(row)
        print(f"  {'PROFILE':11} {profile_s:8.2f}s total")
        print(f"  decision={decision['decision']}  "
              f"years={(checked or {}).get('years_experience')}  "
              f"rows={len((rows or {}).get('employment') or [])}", flush=True)

        return {
            "arm": arm,
            # The experiment's actual subject: what boot() cost, and whether
            # this container paid it or inherited it from a snapshot.
            "boot_id": getattr(self, "boot_id", None),
            "boot_cost_s": round(getattr(self, "boot_cost_s", 0), 2),
            "seconds_since_boot": round(time.time() - getattr(
                self, "boot_epoch", time.time()), 2),
            "ollama_alive_after_restore": alive,
            "ollama_restarted": restarted,
            "readiness_s": round(readiness_s, 2),
            "ollama_ready_s": round(getattr(self, "ollama_ready_s", 0), 2),
            "size_vram": vram,
            "context_length": resident.get("context_length"),
            "calls": calls,
            "profile_s": round(profile_s, 2),
            "first_call_load_s": calls[0]["load_s"],
            "first_call_prefill_s": calls[0]["prompt_eval_s"],
            "first_call_prefill_tok_s": calls[0]["prompt_tok_s"],
            "first_call_gen_s": calls[0]["eval_s"],
            "first_call_gen_tok_s": calls[0]["eval_tok_s"],
        }


@app.cls(
    image=image,
    gpu="T4",
    volumes={"/root/.ollama": model_volume},
    timeout=1800,
    enable_memory_snapshot=True,
    experimental_options={"enable_gpu_snapshot": True},
    max_containers=1,
    # Two seconds, the minimum. The default 60s window let the second
    # invocation land on the container that had just served the first —
    # so "restore" was really "reuse", and the prompt cache came with it
    # (25,501 tok/s prefill, an 18x tell). A restore has to start from a
    # dead container to mean anything.
    scaledown_window=2,
)
class SnapshotArm(_Probe):
    """Ollama booted and warmed INSIDE the snapshot."""

    @modal.enter(snap=True)
    def enter(self):
        self.boot()

    @modal.method()
    def run(self, resume_text: str):
        return self.measure(resume_text, "snapshot")


@app.cls(
    image=image,
    gpu="T4",
    volumes={"/root/.ollama": model_volume},
    timeout=1800,
    max_containers=1,
    scaledown_window=2,
)
class ControlArm(_Probe):
    """Identical, with snapshots off. The honest comparison."""

    @modal.enter()
    def enter(self):
        self.boot()

    @modal.method()
    def run(self, resume_text: str):
        return self.measure(resume_text, "control")


def _verdict(control, snapshot):
    """Say plainly what happened, using the boot fingerprints.

    The first version of this compared profile totals and concluded "the
    snapshot saved little". That was wrong: boot() runs in @modal.enter()
    in BOTH arms, so both had already paid the one-time init before
    measure() started its stopwatch. It was comparing warm against warm.
    The numbers below are the ones that can actually tell the difference.
    """
    print("\n" + "=" * 78)
    print("  SNAPSHOT vs CONTROL — T4, Ollama 0.34.0, qwen3:8b Q4_K_M")
    print("=" * 78)
    rows = [
        ("boot id (same => restored)", control["boot_id"], snapshot["boot_id"]),
        ("BOOT COST paid (s)", control["boot_cost_s"], snapshot["boot_cost_s"]),
        ("age of that boot (s)", control["seconds_since_boot"],
         snapshot["seconds_since_boot"]),
        ("ollama alive after restore", control["ollama_alive_after_restore"],
         snapshot["ollama_alive_after_restore"]),
        ("ollama had to be restarted", control["ollama_restarted"],
         snapshot["ollama_restarted"]),
        ("size_vram", f"{control['size_vram']:,}", f"{snapshot['size_vram']:,}"),
        ("1st call load_duration (s)", control["first_call_load_s"],
         snapshot["first_call_load_s"]),
        ("1st call prefill (tok/s)", control["first_call_prefill_tok_s"],
         snapshot["first_call_prefill_tok_s"]),
        ("generation rate (tok/s)", control["first_call_gen_tok_s"],
         snapshot["first_call_gen_tok_s"]),
        ("profile, model already warm (s)", control["profile_s"],
         snapshot["profile_s"]),
    ]
    print(f"  {'':32} {'control':>14} {'snapshot':>14}")
    for label, a, b in rows:
        print(f"  {label:32} {str(a):>14} {str(b):>14}")

    for key, label in (("cold_end_to_end_s", "COLD END-TO-END, client-side"),):
        a, b = control.get(key), snapshot.get(key)
        if a and b:
            print(f"  {label:32} {a:>14.2f} {b:>14.2f}")
            print(f"\n  snapshot saves {a - b:+.2f}s end to end "
                  f"({(a - b) / a * 100:+.1f}%)")

    print(f"\n  reference: the function probe measured a {BASELINE_COLD_S}s "
          f"cold profile,\n  {BASELINE_ONE_TIME_S}s of it one-time init.")

    if snapshot["ollama_restarted"]:
        print("\n  VERDICT: the snapshot did NOT preserve Ollama — it had to be "
              "restarted,\n           so its initialisation was paid again. "
              "Incompatible as deployed.")
    elif snapshot["size_vram"] <= 0:
        print("\n  VERDICT: Ollama survived but the model is NOT in VRAM. "
              "Inference would\n           silently run on CPU.")
    elif snapshot["boot_cost_s"] > 30:
        print(f"\n  VERDICT: the snapshot arm PAID {snapshot['boot_cost_s']:.1f}s "
              f"of boot cost in this\n           container, so it did not "
              f"restore — it booted. Check whether the\n           app is "
              f"deployed rather than ephemeral.")
    else:
        print("\n  VERDICT: restored cleanly and skipped the boot cost. "
              "Compare the\n           COLD END-TO-END row for what it is "
              "worth in wall clock.")
    print("=" * 78, flush=True)


# No @app.local_entrypoint() on purpose — see "DEPLOY IT" above. The driver
# is deploy/run_snapshot_probe.py, which talks to the DEPLOYED app.
