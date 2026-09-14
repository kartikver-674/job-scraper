"""The one serving wrapper every Modal inference app runs.

Benchmark and production are the SAME wrapper by construction: both
apps subclass ServingEndpoint and take their image, their model pins and
their GPU/snapshot options from here. The 52-document answer-key result
was measured on this code; production may differ from it only in the
settings each app states for itself — its name, its Secret, its warm
window and its container cap — and a test runs the full WSGI contract
against both apps to hold that.

Only synthetic text ever enters a GPU snapshot. No résumé primes one.
"""

import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import urllib.request
import uuid

import modal
from ollama_probe_lib import (
    EXPECTED_DIGEST_PREFIX, MODEL, OLLAMA_URL, OLLAMA_VERSION, start_ollama,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SOURCE_FILES = ("inference.py", "inference_service.py", "local_extract.py")
MODEL_VOLUME = "sweep-ollama-models"


def serving_image():
    """Ollama 0.34.0, the production modules, and this file — pinned."""
    image = (
        modal.Image.debian_slim(python_version="3.12")
        .apt_install("curl", "ca-certificates", "zstd")
        .pip_install("flask>=3.0.0")
        .run_commands(
            "curl -fsSL 'https://ollama.com/download/ollama-linux-amd64.tar.zst"
            f"?version={OLLAMA_VERSION}' | zstd -d | tar -xf - -C /usr"
        )
        .env({"PYTHONPATH": "/app", "OLLAMA_HOST": OLLAMA_URL,
              "OLLAMA_MODEL": MODEL, "SWEEP_MODEL_KEEP_ALIVE": "30s",
              "SWEEP_INFERENCE_WORKERS": "1", "SWEEP_INFERENCE_QUEUE_WAIT": "30"})
    )
    for filename in SOURCE_FILES:
        image = image.add_local_file(ROOT / filename, f"/app/{filename}", copy=True)
    # Shipped deliberately: Modal copies only the ENTRYPOINT module into the
    # container, so every module the container imports must travel here.
    for filename in ("ollama_probe_lib.py", "modal_serving.py"):
        image = image.add_local_file(HERE / filename, f"/app/{filename}", copy=True)
    return image


def serving_options(secret_name, scaledown_window, max_containers):
    """The @app.cls options both apps share, plus the three they state."""
    return dict(
        image=serving_image(), gpu="T4",
        volumes={"/root/.ollama": modal.Volume.from_name(MODEL_VOLUME)},
        secrets=[modal.Secret.from_name(secret_name)],
        max_containers=max_containers, min_containers=0,
        scaledown_window=scaledown_window,
        timeout=360, startup_timeout=600,
        enable_memory_snapshot=True,
        experimental_options={"enable_gpu_snapshot": True},
    )


def ollama_json(path):
    with urllib.request.urlopen(OLLAMA_URL + path, timeout=5) as reply:
        return json.load(reply)


def model_identity(require_vram=False):
    version = ollama_json("/api/version")["version"]
    tag = next((m for m in ollama_json("/api/tags")["models"]
                if m.get("name") == MODEL), {})
    digest = tag.get("digest", "")
    quant = tag.get("details", {}).get("quantization_level")
    if (version != OLLAMA_VERSION or not digest.startswith(EXPECTED_DIGEST_PREFIX)
            or len(digest) != 64 or quant != "Q4_K_M"):
        raise RuntimeError("pinned Ollama/model identity check failed; no model pull attempted")
    resident = next((m for m in ollama_json("/api/ps")["models"]
                     if m.get("digest") == digest), {})
    vram = resident.get("size_vram", 0)
    if require_vram and vram <= 0:
        raise RuntimeError("the pinned model is not resident in GPU memory")
    return {"ollama_version": version, "model": MODEL, "digest": digest,
            "quantization": quant, "size_vram": vram,
            "resident_size": resident.get("size", 0),
            "context_length": resident.get("context_length")}


class ServingEndpoint:
    """Lifecycle bodies. Each app's decorated class delegates to these."""

    def _boot(self):
        import inference
        import inference_service
        import local_extract

        inference_service.require_tokens()
        self.boot_id = uuid.uuid4().hex
        # Avoid both leaking raw Ollama diagnostics and filling an unread PIPE
        # after a long run. The WSGI app supplies the operational log.
        self.process = start_ollama(output=subprocess.DEVNULL)
        model_identity()
        os.environ[inference.KEEP_ALIVE_ENV] = "-1"
        try:
            inference.LocalOllama().generate(
                MODEL, local_extract.FIELDS_PROMPT.format(text="warmup"),
                local_extract.FIELDS_SCHEMA, 300)
            model_identity(require_vram=True)
        finally:
            os.environ[inference.KEEP_ALIVE_ENV] = inference.DEFAULT_KEEP_ALIVE

    def _ready(self):
        import inference
        import inference_service

        # Fresh after EVERY restore: distinguishes it from container reuse.
        self.instance_id = uuid.uuid4().hex
        os.environ[inference.KEEP_ALIVE_ENV] = inference.DEFAULT_KEEP_ALIVE
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        # Auth and synchronization state are built after restore, never
        # carried inside the snapshot.
        self.wsgi = inference_service.create_app()

    def _diagnostics(self):
        """Modal SDK credentials required; this is NOT a public HTTP route."""
        import inference
        import inference_service
        import local_extract

        return {"boot_id": self.boot_id, "instance_id": self.instance_id,
                "keep_alive": inference.keep_alive(), **model_identity(require_vram=True),
                "sources": {Path(module.__file__).name: hashlib.sha256(
                    Path(module.__file__).read_bytes()).hexdigest()
                    for module in (inference, inference_service, local_extract)}}

    def _stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
