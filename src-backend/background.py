"""Shared base for long-running background jobs (sync, enrichment, ...).

Every job in this app follows the same shape: run once in a background
thread, track idle/running/complete/error progress, and let any number of
listeners (a websocket handler, typically) observe that progress live.
Subclasses implement only ``_run(self, *args, **kwargs)`` and call
``self._emit(...)`` to report progress; everything else (thread spawning,
re-entrancy guarding, exception handling, listener broadcasting) lives here
once instead of being copy-pasted per job.
"""
import threading


class BackgroundJob:
    # Extra state keys a subclass wants in its progress dict beyond the
    # common status/message/processed/total (e.g. SyncManager's "added").
    EXTRA_STATE: dict = {}

    def __init__(self):
        self.listeners = []
        self._lock = threading.Lock()
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
        for listener in self.listeners:
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
            self.state.update(status="running", message="Starting…",
                              processed=0, total=0, **self.EXTRA_STATE)
        threading.Thread(target=self._run_wrapper, args=args, kwargs=kwargs, daemon=True).start()
        return True

    def _run_wrapper(self, *args, **kwargs):
        try:
            self._run(*args, **kwargs)
        except Exception as exc:
            self._emit(status="error", message=str(exc))

    def _run(self, *args, **kwargs):
        raise NotImplementedError
