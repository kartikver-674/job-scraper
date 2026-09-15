"""Render's side of the link to the Oracle sweep worker.

Server-side only. The browser never learns this URL, never learns the
bearer token, and never talks to Oracle: it polls Render, and Render polls
the worker.

Two credentials, with different lifetimes on purpose:

  the worker token   Render's own, from the environment, the same for
                     every visitor, and never rendered into a page.
  the owner          per visitor, derived from their signed cookie by
                     public.owner_for_session(), so a restarted Render
                     process can still prove a run is theirs.

A visitor's Apify token is neither of those. It is an argument to
create_run and nothing else — not stored here, not logged, not put in a
URL — and the worker is equally careful with it on the far side.
"""

import json
import os
import urllib.error
import urllib.request

from sweep import public

URL_ENV = "SWEEP_WORKER_URL"
TOKEN_ENV = "SWEEP_WORKER_TOKEN"

# A sweep is long, but these calls are not: they create, poll or stop.
TIMEOUT = 20


class WorkerError(RuntimeError):
    """The worker could not be reached, or refused. Safe to show: this
    module composes the text, so it never carries urllib's URL."""


class RunNotFound(WorkerError):
    """No such run — or not this visitor's. The worker does not
    distinguish the two, and neither should anything here."""


def base_url(url=None):
    return (url or os.environ.get(URL_ENV) or "").strip().rstrip("/")


def _token(token=None):
    return (token if token is not None
            else os.environ.get(TOKEN_ENV) or "").strip()


def _call(method, path, body=None, url=None, token=None, owner=None):
    root = base_url(url)
    if not root:
        raise WorkerError("the sweep worker is not configured")
    headers = {"Authorization": f"Bearer {_token(token)}",
               # Whose run this is, re-derived from the cookie on every
               # request rather than remembered in this process.
               "X-Sweep-Owner": owner if owner is not None
               else public.owner_for_session()}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(root + path, data=data, headers=headers,
                                     method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RunNotFound("that sweep is not available") from None
        raise WorkerError(f"the sweep worker refused the request "
                          f"({exc.code})") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise WorkerError("the sweep worker did not answer") from None


def create_run(profile, prefs, free_only=True, apify_token=None, **kw):
    """Start a sweep. Returns the opaque run id.

    `apify_token` passes straight through to the worker and is not held
    here — not in a variable that outlives this call, not in a log line.
    """
    body = {"profile": profile, "prefs": prefs, "free_only": free_only,
            "owner": kw.pop("owner", None) or public.owner_for_session()}
    if apify_token:
        body["apify_token"] = apify_token
    answer = _call("POST", "/v1/runs", body, **kw)
    return answer["run_id"]


def run_status(run_id, **kw):
    return _call("GET", f"/v1/runs/{run_id}", **kw)


def run_rows(run_id, since=0, **kw):
    return _call("GET", f"/v1/runs/{run_id}/rows?since={int(since)}", **kw)


def stop_run(run_id, **kw):
    return _call("POST", f"/v1/runs/{run_id}/stop", **kw)
