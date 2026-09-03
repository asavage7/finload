"""Shared base class for background jobs.

A job in finload is an asynchronous task that runs in a background thread.
All jobs track their own progress and can be observed live via a websocket.
"""
import logging
import threading
from time import monotonic

logger = logging.getLogger(__name__)


class BackgroundJob:
    EXTRA_STATE: dict = {} # Any extra information to include in the job's state dict, beyond the standard totals
    supports_force: bool = False # Can the job be forced to re-run for every track?

    def __init__(self):
        self.listeners = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event() # Allows the job to be stopped
        self.run_started_at: float | None = None
        self.state = {
            "status": "idle",   # idle | running | complete | error
            "eta_seconds": None,  # Estimated time remaining for the job to complete
            "message": "",
            "processed": 0,
            "total": 0,
            **self.EXTRA_STATE,
        }

    @property
    def is_running(self) -> bool:
        return self.state["status"] == "running"

    def stop(self):
        self._stop_event.set()

    def should_stop(self) -> bool:
        return self._stop_event.is_set()

    # listener management and state broadcasting
    def add_listener(self, callback):
        self.listeners.append(callback)
        callback(dict(self.state))  # new subscriber renders the current state immediately

    def remove_listener(self, callback):
        if callback in self.listeners:
            self.listeners.remove(callback)

    def _emit(self, **changes):
        self.state.update(changes)
        if "processed" in changes or "total" in changes:
            # Update ETA if we have enough information to do so
            processed = self.state.get("processed")
            total = self.state.get("total")
            if processed is not None and total:
                elapsed = monotonic() - (self.run_started_at or 0)
                if processed > 0 and elapsed > 0:
                    eta_seconds = int(elapsed * (total - processed) / processed)
                    self.state["eta_seconds"] = eta_seconds
        snapshot = dict(self.state)
        for listener in list(self.listeners):
            try:
                listener(snapshot)
            except Exception:
                pass

    # job lifecycle
    def start(self, *args, **kwargs) -> bool:
        """Start the job in a background thread. Returns False if already running."""
        with self._lock:
            if self.is_running:
                return False
            self.run_started_at = monotonic()
            self._stop_event.clear()
            self.state.update(status="running", message="Starting...",
                              processed=0, total=0, eta_seconds=None, **self.EXTRA_STATE)
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
