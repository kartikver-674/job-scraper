"""Sweep's PRODUCTION inference endpoint on Modal.

    modal deploy deploy/modal_production.py
    python deploy/run_modal_benchmark.py --target production prime
    python deploy/run_modal_benchmark.py --target production smoke
    python deploy/run_modal_benchmark.py --target production contract

The same serving wrapper the 52-document answer-key gate accepted
(modal_serving.py): Ollama 0.34.0, qwen3:8b Q4_K_M digest 500a1f067a9f,
the same prompts, schema and request options, the same /healthz and
/v1/generate contract, GPU memory snapshots. Only these differ from the
benchmark app, on purpose:

  APP_NAME / SECRET_NAME   its own app and its own bearer token — a
                           benchmark token cannot reach production
  SCALEDOWN_WINDOW = 60    a container stays warm for a minute after the
                           last request, so the second call of a profile
                           and a quick follow-up do not pay a restore.
                           NOT tuned further until real request-arrival
                           and spend data exist.
  MAX_CONTAINERS = 1       one T4 at a time: the spend rate is bounded by
                           construction; the workspace budget bounds the
                           total.

Sweep never learns Modal exists: it is pointed here with
SWEEP_INFERENCE_URL and SWEEP_INFERENCE_TOKEN, and pointed back at Oracle
the same way.
"""

import modal
from modal_serving import ServingEndpoint, serving_options

APP_NAME = "sweep-inference-production"
SECRET_NAME = "sweep-inference-production"
SCALEDOWN_WINDOW = 60
MAX_CONTAINERS = 1

app = modal.App(APP_NAME)


@app.cls(**serving_options(SECRET_NAME, SCALEDOWN_WINDOW, MAX_CONTAINERS))
@modal.concurrent(max_inputs=8)
class ProductionEndpoint(ServingEndpoint):
    @modal.enter(snap=True)
    def boot(self):
        self._boot()

    @modal.enter(snap=False)
    def ready(self):
        self._ready()

    @modal.wsgi_app()
    def web(self):
        return self.wsgi

    @modal.method()
    def diagnostics(self):
        return self._diagnostics()

    @modal.exit()
    def stop(self):
        self._stop()
