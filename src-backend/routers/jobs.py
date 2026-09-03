"""Generic background-job routes: list / start / live progress."""
import asyncio

from fastapi import APIRouter, Body, HTTPException, WebSocket, WebSocketDisconnect

from core import state

router = APIRouter()


def _get_job(name: str):
    job = state.jobs.get(name)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown job '{name}'")
    return job


def _start(job, name: str, force: bool):
    # Sync also needs the active provider threaded through
    if name == "sync":
        return job.start(state.provider, force=force)
    return job.start(force=force)


@router.get("/api/jobs")
def list_jobs():
    """Every registered job's name, live state, and force-rerun capability.
    Display metadata lives in /src/lib/settings-schema.json."""
    return {"jobs": [
        {"name": name, "supports_force": job.supports_force, "state": job.state}
        for name, job in state.jobs.items()
    ]}


@router.post("/api/jobs/{name}/start")
def start_job(name: str, force: bool = Body(False, embed=True)):
    job = _get_job(name)
    started = _start(job, name, force)
    return {"started": started, "status": job.state["status"]}

@router.post("/api/jobs/{name}/stop")
def stop_job(name: str):
    job = _get_job(name)
    job.stop()
    return {"status": job.state["status"]}


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
        # The socket is push-only but disconnects are sent by the client
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        job.remove_listener(on_update)
