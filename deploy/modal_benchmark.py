"""Temporary authenticated equivalence endpoint. No normal Sweep traffic.

Deploy: modal deploy deploy/modal_benchmark.py
Operate: python deploy/run_modal_benchmark.py {prime,smoke}
Only synthetic warmup enters the GPU snapshot; no resume is used to prime it.
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
APP_NAME = "sweep-inference-benchmark"
SOURCE_FILES = ("inference.py", "inference_service.py", "local_extract.py")
app = modal.App(APP_NAME)
model_volume = modal.Volume.from_name("sweep-ollama-models")
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
image = image.add_local_file(HERE / "ollama_probe_lib.py", "/app/ollama_probe_lib.py", copy=True)


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


@app.cls(
    image=image, gpu="T4", volumes={"/root/.ollama": model_volume},
    secrets=[modal.Secret.from_name("sweep-inference-benchmark")],
    max_containers=1, min_containers=0, scaledown_window=2,
    timeout=360, startup_timeout=600,
    enable_memory_snapshot=True,
    experimental_options={"enable_gpu_snapshot": True},
)
@modal.concurrent(max_inputs=8)
class BenchmarkEndpoint:
    @modal.enter(snap=True)
    def boot(self):
        import inference
        import inference_service
        import local_extract

        inference_service.require_tokens()
        self.boot_id = uuid.uuid4().hex
        # Avoid both leaking raw Ollama diagnostics and filling an unread PIPE
        # after a long corpus run. Our WSGI app supplies the operational log.
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

    @modal.enter(snap=False)
    def ready(self):
        import inference
        import inference_service

        # Fresh after EVERY restore: distinguishes it from container reuse.
        self.instance_id = uuid.uuid4().hex
        os.environ[inference.KEEP_ALIVE_ENV] = inference.DEFAULT_KEEP_ALIVE
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        # Construct auth and synchronization state after restore. No shared
        # global provider patches from the historical measurement probes.
        self.wsgi = inference_service.create_app()

    @modal.wsgi_app()
    def web(self):
        return self.wsgi

    @modal.method()
    def diagnostics(self):
        """Modal SDK credentials required; this is NOT a public HTTP route."""
        import inference
        import inference_service
        import local_extract

        return {"boot_id": self.boot_id, "instance_id": self.instance_id,
                "keep_alive": inference.keep_alive(), **model_identity(require_vram=True),
                "sources": {Path(module.__file__).name: hashlib.sha256(
                    Path(module.__file__).read_bytes()).hexdigest()
                    for module in (inference, inference_service, local_extract)}}

    @modal.exit()
    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
