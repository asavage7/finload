import os
import threading

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.logging_config import setup_logging

# Set up logging before importing other app modules
setup_logging()

from core import state
from core.config import get_backend_host, get_backend_port, get_cors_origins
from routers import (
    accent_colors,
    feature_transfer,
    history,
    images,
    jobs,
    library,
    playback,
    playlists,
    quiz,
    search,
    settings,
    sync,
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)

for module in (library, search, images, accent_colors, playlists,
               playback, settings, sync, jobs, history, quiz, feature_transfer):
    app.include_router(module.router)


@app.get("/api/health")
def health():
    """Checks if the backend is available."""
    return {"status": "ok"}


def _exit_when_orphaned():
    """Exit the proces if the app is closed to avoid zombie processes."""
    initial_ppid = os.getppid()
    stop = threading.Event()
    while not stop.wait(2.0):
        if os.getppid() != initial_ppid:
            os._exit(0)


@app.on_event("startup")
def on_startup():
    """Initialize the app and sync on open if onboarding is complete."""
    state.init_playback()
    threading.Thread(target=_exit_when_orphaned, daemon=True).start()
    if state.settings.get("onboarding_complete"):
        state.sync.start(state.provider)


if __name__ == "__main__":
    # Required first call for a frozen (PyInstaller) executable that uses
    # multiprocessing (services/audio_analysis.py's worker pool). Windows has
    # no fork(), so spawning a worker re-executes this frozen exe from
    # scratch; without freeze_support() intercepting that re-exec, the worker
    # falls through to uvicorn.run() below just like the real process did,
    # starting a second full server that can itself spawn more workers --
    # each doing the same. Harmless/required no-op everywhere else.
    import multiprocessing

    multiprocessing.freeze_support()

    uvicorn.run(app, host=get_backend_host(), port=get_backend_port())
