"""Shared base for long-running background jobs (sync, enrichment, ...).

Every job in this app follows the same shape: run once in a background
thread, track idle/running/complete/error progress, and let any number of
listeners (a websocket handler, typically) observe that progress live.
Subclasses implement only ``_run(self, *args, **kwargs)`` and call
``self._emit(...)`` to report progress; everything else (thread spawning,
re-entrancy guarding, exception handling, listener broadcasting) lives here
once instead of being copy-pasted per job.
"""
import logging
import threading

logger = logging.getLogger(__name__)


class BackgroundJob:
    # Extra state keys a subclass wants in its progress dict beyond the
    # common status/message/processed/total (e.g. SyncManager's "added").
    EXTRA_STATE: dict = {}

    # Whether this job's start() accepts a force flag to widen its unit of
    # work (re-process everything, not just what's unprocessed). Exposed via
    # /api/jobs so the frontend knows whether to offer a "re-run all" action.
    supports_force: bool = False

    def __init__(self):
        self.listeners = []
        self._lock = threading.Lock()
        # Set = free to run, cleared = paused. Lets one job (sync) tell
        # others to idle rather than run concurrently with it -- see
        # pause()/resume()/wait_if_paused().
        self._pause_event = threading.Event()
        self._pause_event.set()
        self.state = {
            "status": "idle",   # idle | running | complete | error
            "message": "",
            "processed": 0,
            "total": 0,
            **self.EXTRA_STATE,
        }

    @property
    def is_running(self) -> bool:
        return self.state["status"] == "running"

    # --- cooperative pause -------------------------------------------------
    # No hard cancellation exists for a running job (see _run_wrapper) -- this
    # is a softer "yield to someone with priority" signal a long-running job
    # opts into by calling wait_if_paused() between work items, so a pause
    # only ever lands at a clean item boundary rather than mid-item.
    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    def wait_if_paused(self):
        """Call between work items in a long-running _run loop. Blocks here
        for as long as another job holds this one paused."""
        if not self._pause_event.is_set():
            self._emit(message="Paused for library sync...")
            self._pause_event.wait()

    # --- listeners -----------------------------------------------------
    def add_listener(self, callback):
        self.listeners.append(callback)
        callback(dict(self.state))  # new subscriber renders the current state immediately

    def remove_listener(self, callback):
        if callback in self.listeners:
            self.listeners.remove(callback)

    def _emit(self, **changes):
        self.state.update(changes)
        snapshot = dict(self.state)
        # Snapshot the list: a websocket disconnecting can remove_listener from
        # another thread mid-broadcast, which would break the iteration itself.
        for listener in list(self.listeners):
            try:
                listener(snapshot)
            except Exception:
                pass

    # --- control ---------------------------------------------------------
    def start(self, *args, **kwargs) -> bool:
        """Kick off ``_run`` in a background thread. False if already running."""
        with self._lock:
            if self.is_running:
                return False
            self.state.update(status="running", message="Starting...",
                              processed=0, total=0, **self.EXTRA_STATE)
        threading.Thread(target=self._run_wrapper, args=args, kwargs=kwargs, daemon=True).start()
        return True

    def _run_wrapper(self, *args, **kwargs):
        try:
            self._run(*args, **kwargs)
        except Exception as exc:
            logger.exception("Background job failed")
            self._emit(status="error", message=str(exc) or type(exc).__name__)
        finally:
            # A _run that returns without emitting a terminal status would
            # otherwise leave the job stuck "running" and unstartable forever.
            if self.is_running:
                self._emit(status="complete")

    def _run(self, *args, **kwargs):
        raise NotImplementedError
