"""App entry point: builds the FastAPI app and wires the route modules together."""
import os
import threading

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import state
from config import get_backend_host, get_backend_port, get_cors_origins
from routers import (
    accent_colors,
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
               playback, settings, sync, jobs, history, quiz):
    app.include_router(module.router)


def _exit_when_orphaned():
    """Exit the process if our parent (the Tauri app) dies.

    Tauri kills this sidecar on a normal quit, but if the app is force-killed the
    sidecar would be reparented and linger as an orphaned uvicorn. Detect that by
    watching for a change in our parent PID and hard-exit when it happens.
    """
    initial_ppid = os.getppid()
    stop = threading.Event()
    while not stop.wait(2.0):
        if os.getppid() != initial_ppid:
            os._exit(0)


@app.on_event("startup")
def on_startup():
    state.init_playback()
    threading.Thread(target=_exit_when_orphaned, daemon=True).start()
    # Sync is incremental once a source has a checkpoint (see SyncManager),
    # so running it on every launch is cheap after the first one. Skipped pre-
    # onboarding since there's no configured source to sync yet.
    if state.settings.get("onboarding_complete"):
        state.sync.start(state.provider)


if __name__ == "__main__":
    # This runs only for the PyInstaller-frozen sidecar (production). A frozen
    # bundle has no importable "main" module on disk and no source files to
    # watch, so pass the app object directly and never enable reload here.
    # (Dev uses `uvicorn main:app --reload` via scripts/run-backend.cjs instead.)
    uvicorn.run(app, host=get_backend_host(), port=get_backend_port())
