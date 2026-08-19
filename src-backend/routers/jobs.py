"""Generic background-job routes: start / status / live progress, keyed by
job name (see the ``state.jobs`` registry). One set of routes for every
``BackgroundJob`` instead of bespoke start/status/websocket endpoints per job.
"""
import asyncio

from fastapi import APIRouter, Body, HTTPException, WebSocket, WebSocketDisconnect

import state

router = APIRouter()

# Display metadata for the Tasks UI, keyed by the state.jobs registry name.
# `gate` is the settings key that must be truthy for the job to run (None =
# always runnable); `disabled_reason` is shown when that gate is off. Edit this
# table to add, relabel, or re-describe a task; the frontend renders whatever
# this returns, in this order.
_JOB_META = {
    "sync": {
        "label": "Library sync",
        "description": "Import new tracks and remove deleted ones from your library source.",
        "gate": None,
        "disabled_reason": None,
    },
    "audio_features": {
        "label": "Audio analysis",
        "description": "Extract per-track tempo and timbre features used by radio and recommendations.",
        "gate": "enable_discovery",
        "disabled_reason": "Turn on Discovery in settings to analyze audio.",
    },
    "genre_enrichment": {
        "label": "Genre matching",
        "description": "Look up genres for your albums and artists from MusicBrainz and Last.fm.",
        "gate": "enable_genre_enrichment",
        "disabled_reason": "Turn on genre enrichment in settings.",
    },
    "metadata": {
        "label": "Artist info",
        "description": "Fetch artist bios and images.",
        "gate": "enable_online_metadata",
        "disabled_reason": "Turn on online metadata in settings.",
    },
}


def _get_job(name: str):
    job = state.jobs.get(name)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown job '{name}'")
    return job


def _start(job, name: str, force: bool):
    # Sync's unit of work is "the active provider", not a force flag — every
    # other job just re-enriches everything un-enriched (or, with force,
    # everything already enriched too).
    if name == "sync":
        return job.start(state.provider)
    return job.start(force=force)


@router.get("/api/jobs")
def list_jobs():
    """Every known job with its display metadata, current state, and whether a
    settings gate has it disabled. Drives the Tasks UI; order follows _JOB_META."""
    jobs = []
    for name, meta in _JOB_META.items():
        job = state.jobs.get(name)
        if job is None:
            continue
        enabled = meta["gate"] is None or bool(state.settings.get(meta["gate"]))
        jobs.append({
            "name": name,
            "label": meta["label"],
            "description": meta["description"],
            "enabled": enabled,
            "disabled_reason": None if enabled else meta["disabled_reason"],
            "state": job.state,
        })
    return {"jobs": jobs}


@router.post("/api/jobs/{name}/start")
def start_job(name: str, force: bool = Body(False, embed=True)):
    job = _get_job(name)
    started = _start(job, name, force)
    return {"started": started, "status": job.state["status"]}


@router.get("/api/jobs/{name}/status")
def job_status(name: str):
    return _get_job(name).state


@router.websocket("/ws/jobs/{name}")
async def job_ws(websocket: WebSocket, name: str):
    job = state.jobs.get(name)
    if job is None:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    loop = asyncio.get_running_loop()

    def on_update(job_state):
        asyncio.run_coroutine_threadsafe(websocket.send_json(job_state), loop)

    job.add_listener(on_update)
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("action") == "start":
                _start(job, name, bool(data.get("force", False)))
    except WebSocketDisconnect:
        pass
    finally:
        job.remove_listener(on_update)
