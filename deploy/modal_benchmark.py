"""Temporary authenticated equivalence endpoint. No normal Sweep traffic.

Deploy: modal deploy deploy/modal_benchmark.py
Operate: python deploy/run_modal_benchmark.py {prime,smoke}
The serving wrapper itself lives in modal_serving.py and is shared with
production; only the settings below belong to this app.
"""

import modal
from modal_serving import ServingEndpoint, serving_options

APP_NAME = "sweep-inference-benchmark"
SECRET_NAME = "sweep-inference-benchmark"
# Two seconds: the equivalence gate needs every document to start cold.
SCALEDOWN_WINDOW = 2
MAX_CONTAINERS = 1

app = modal.App(APP_NAME)


@app.cls(**serving_options(SECRET_NAME, SCALEDOWN_WINDOW, MAX_CONTAINERS))
@modal.concurrent(max_inputs=8)
class BenchmarkEndpoint(ServingEndpoint):
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
