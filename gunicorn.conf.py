"""gunicorn settings for inference_service.

    python -m inference_service                     # validates, then execs this
    gunicorn -c gunicorn.conf.py "inference_service:create_app()"

Every number here is DERIVED from inference_service's own limits rather
than typed in. Two of them are load-bearing in a way gunicorn's defaults
are not, and both would fail silently if they drifted:

  timeout           gunicorn KILLS a worker that has not touched the
                    arbiter in `timeout` seconds. The default is 30. A
                    qwen3:8b extraction over a three-page résumé measured
                    92s. On the defaults this service would kill itself,
                    mid-generation, on its ordinary workload — and the
                    client would see a dropped socket rather than an
                    error with a category on it.

  graceful_timeout  on SIGTERM, how long in-flight requests get to finish
                    before the worker is killed. Shorter than the longest
                    generation means a deploy or a Ctrl-C throws away
                    whatever the model was in the middle of.

Both are set above MAX_TIMEOUT, which is the longest a request may ask us
to wait, so the process-level deadline can never fire before the
application-level one does. The application deadline is the one that
produces a `model_timeout` category the caller can act on.
"""

import multiprocessing  # noqa: F401  -- see the `workers` note below
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import inference_service as svc

_host, _port = svc.bind()
bind = f"{_host}:{_port}"

# ONE process, always — not multiprocessing.cpu_count(), the usual recipe.
# The model slot that serialises generations is a threading.Semaphore, so
# it is per-process: two workers would mean two concurrent generations
# against one Ollama, which is the exact thing the semaphore exists to
# prevent. Concurrency here is threads, and the model slot is what bounds
# the expensive part.
workers = 1
worker_class = "gthread"

# Enough threads that /healthz, an auth failure and a malformed request
# all still answer immediately while a generation holds the model slot.
# They are cheap: all but one are parked on the semaphore or doing I/O.
threads = 8

# The two that matter. See the module docstring.
timeout = svc.MAX_TIMEOUT + 60
graceful_timeout = svc.MAX_TIMEOUT + 30

# Refuse a request line or headers larger than we would ever send. The
# body is bounded separately, by Flask's MAX_CONTENT_LENGTH.
limit_request_line = 4094
limit_request_fields = 50
limit_request_field_size = 8190

# Connections wait here rather than being refused at the TCP level, which
# is what makes a queued request show up as a `model_busy` with a
# Retry-After instead of a connection reset nobody can categorise.
backlog = 64

# Slow clients are the load balancer's problem when there is one, and
# there is not one yet. keepalive short so a dead client's socket goes.
keepalive = 5

accesslog = None        # the app logs every request itself, with a
                        # request id and without the prompt. gunicorn's
                        # access log would be a second, redundant copy.
errorlog = "-"
loglevel = os.environ.get("SWEEP_INFERENCE_LOGLEVEL", "info")
capture_output = True

proc_name = "sweep-inference"


def on_starting(server):
    """Once, in the arbiter, before anything binds."""
    server.log.info("sweep inference service starting")
    for line in svc.startup_report():
        server.log.info("  %s", line)


def when_ready(server):
    server.log.info("listening on %s — %d thread(s), %d model slot(s), "
                    "worker timeout %ds", bind, threads, svc.workers(),
                    timeout)


def worker_exit(server, worker):
    server.log.info("worker %s gone", worker.pid)


def on_exit(server):
    """After the graceful period: every in-flight generation has either
    finished or been given `graceful_timeout` to try."""
    server.log.info("sweep inference service stopped")
