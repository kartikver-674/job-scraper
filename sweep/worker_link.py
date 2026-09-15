"""Public mode's sweep backend: the console's own screens, run on Oracle.

The local console already knows how to run a free sweep. Its Configure,
Confirm, Running and Results screens have carried `free_only` branches
since long before any of this — "Free sources only", a zero meter, no key
pills — and those screens have had far more iteration than anything a
beta would grow in a week.

So public mode does not get its own pages. It gets the same routes, with
the four things they reach for replaced:

    fetch_plan   a free-only plan, costed locally, no subprocess
    start_sweep  a run created on the Oracle worker
    read_live    what that run has found so far
    read_rows    what it found, for Results and the exports
    read_done    nothing: a free sweep bills no combos

`app.state["proc"]` normally holds a Popen. Here it holds a RemoteRun,
which answers poll() and terminate() from the worker instead — so
_sweep_in_flight(), the running screen and POST /stop keep working
without knowing the sweep is on another machine.

State is memory, and Render's memory does not survive a redeploy. The run
id does, in the signed cookie, so `rehydrate` puts back the few facts the
screens need to show a sweep this process never started.
"""

import time

from sweep import public, worker_client

# What a free sweep's plan looks like: no paid searches at all. The shape
# is plan.fetch()'s, so plan.cost() prices it the same way it prices a
# real one — at zero, because there is nothing billable in it.
def free_plan(profile, free_sources=0):
    return {"profile": profile, "sites": {}, "max_results": {},
            "free_sources": free_sources}


class RemoteRun:
    """A Popen, as far as the console's screens are concerned.

    They ask a sweep two things — are you still going, and stop — and a
    run on another machine can answer both. Nothing else about the local
    subprocess is relied on.
    """

    FINISHED = {"done", "failed", "stopped", "interrupted"}

    def __init__(self, run_id):
        self.run_id = run_id
        self._code = None

    def poll(self):
        """None while the sweep is alive, an exit code once it is not."""
        if self._code is not None:
            return self._code
        try:
            status = worker_client.run_status(self.run_id)
        except worker_client.RunNotFound:
            self._code = 1
            return self._code
        except worker_client.WorkerError:
            # Unreachable for a moment is not finished: saying otherwise
            # would send the watcher to a results screen mid-sweep.
            return None
        if status.get("state") in self.FINISHED:
            self._code = 0 if status.get("state") == "done" else 1
        return self._code

    def terminate(self):
        try:
            worker_client.stop_run(self.run_id)
        except worker_client.WorkerError:
            pass

    def send_signal(self, _sig):
        """runs.stop() interrupts the engine with SIGINT. There is no
        local process to signal, so the worker is asked to stop the run —
        the same outcome by the only means available across a wire."""
        self.terminate()

    # The console calls this on a child it has already terminated.
    def wait(self, timeout=None):
        return self.poll()


def _rows(run_id):
    if not run_id:
        return []
    try:
        return worker_client.all_rows(run_id)
    except worker_client.WorkerError:
        return []


def injections(app):
    """The functions create_app would otherwise resolve locally.

    Takes the app and reads `app.state` per call, never capturing it:
    public mode swaps that attribute for a per-session view after these
    are built, and a closure over the old object would serve one
    visitor's sweep to everybody.
    """

    def fetch_plan(profile):
        # No subprocess: a free sweep has nothing to price, and Render has
        # neither the corpus nor the CPU to spend on a dry run that can
        # only ever answer zero.
        return free_plan(profile)

    def start_sweep(profile):
        run_id = worker_client.create_run(app.state.get("derived") or {},
                                          _prefs_of(app.state), free_only=True)
        public.remember_run(run_id)
        return RemoteRun(run_id)

    def read_live(profile, since):
        return _rows(public.current_run_id())

    def read_rows(profile):
        return _rows(public.current_run_id())

    def read_done(profile, day):
        # Combos are paid searches. A free sweep runs none, so the ledger
        # is empty and the progress tiles are empty with it — which is
        # what the free path already renders locally.
        return []

    def read_spend():
        # Never called on the free path (snapshot() returns early), and
        # there is no account to poll if it were.
        return None

    def list_sweeps(profile):
        return []

    return {"fetch_plan": fetch_plan, "start_sweep": start_sweep,
            "read_live": read_live, "read_rows": read_rows,
            "read_done": read_done, "read_spend": read_spend,
            "list_sweeps": list_sweeps}


def _prefs_of(state):
    from sweep.app import _prefs
    return _prefs(state)


def rehydrate(app):
    """Put back what a returning visitor's screens need after a redeploy.

    Oracle keeps sweeping while Render restarts, and the cookie keeps the
    run id — but state is memory, so /running would bounce a visitor all
    the way back to the upload screen for a sweep that is still going.

    Only the facts the screens read, and only from the worker: the
    profile name it is running, when it started, and an empty free plan.
    The derived résumé is gone for good, which is why this does not try to
    make Configure work — starting a NEW sweep needs the résumé again.
    """
    run_id = public.current_run_id()
    if not run_id or app.state.get("raw_plan"):
        return None
    try:
        status = worker_client.run_status(run_id)
    except worker_client.WorkerError:
        return None

    profile = status.get("profile_name") or "sweep"
    app.state["profile"] = profile
    app.state["free_only"] = True
    app.state["raw_plan"] = free_plan(profile)
    app.state["plan"] = _costed(app.state["raw_plan"])
    app.state["run_started_at"] = status.get("started_at") or time.time()
    if status.get("state") not in RemoteRun.FINISHED:
        app.state["proc"] = RemoteRun(run_id)
    return status


def _costed(raw):
    import config

    from sweep import plan as plan_mod
    return plan_mod.cost(raw, config.SITE_RATES, config.SITE_RATE_BASIS)
