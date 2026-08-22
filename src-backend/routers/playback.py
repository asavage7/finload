"""Playback control routes and the /ws/playback websocket."""
import asyncio
import random

from fastapi import APIRouter, Body, HTTPException, WebSocket, WebSocketDisconnect

from core import state
from core.database import Album, Artist, Playlist, PlaylistTrack, QueueItem, Track, track_scope_clause
from services import radio

router = APIRouter()


def _library_scope():
    return track_scope_clause(state.settings.get("jellyfin_library_ids"))


@router.post("/api/playback/play_track/{track_id}")
def play_track(track_id: str):
    state.playback.play_now(track_id, context_ids=[track_id])
    return {"status": "success"}


@router.post("/api/playback/play_album/{album_id}")
def play_album(album_id: str, track_id: str | None = None, shuffle: bool = False):
    album = Album.get_or_none(Album.id == album_id)
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")
    album_query = Track.select().where(Track.album == album_id)
    scope = _library_scope()
    if scope is not None:
        album_query = album_query.where(scope)
    album_tracks = list(album_query.order_by(Track.disc_number, Track.track_number))
    track_ids = [t.id for t in album_tracks]
    if shuffle:
        random.shuffle(track_ids)
    try:
        state.playback.play_now(track_id or track_ids[0], track_ids)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "success"}


@router.post("/api/playback/play_artist/{artist_id}")
def play_artist(artist_id: str, track_id: str | None = None, shuffle: bool = False):
    artist = Artist.get_or_none(Artist.id == artist_id)
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")
    artist_tracks = (Track.select()
                     .join(Album)
                     .where(Track.artist == artist.id))
    scope = _library_scope()
    if scope is not None:
        artist_tracks = artist_tracks.where(scope)
    artist_tracks = artist_tracks.order_by(Album.release_year, Track.disc_number, Track.track_number)
    track_ids = [t.id for t in artist_tracks]
    if not track_ids:
        raise HTTPException(status_code=404, detail="Artist has no tracks")
    if shuffle:
        random.shuffle(track_ids)
    try:
        state.playback.play_now(track_id or track_ids[0], track_ids)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "success"}


@router.post("/api/playback/play_playlist/{playlist_id}")
def play_playlist(playlist_id: str, track_id: str | None = None, shuffle: bool = False):
    playlist = Playlist.get_or_none(Playlist.id == playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    items = (PlaylistTrack.select(PlaylistTrack, Track)
             .join(Track)
             .where(PlaylistTrack.playlist == playlist_id)
             .order_by(PlaylistTrack.position))
    track_ids = [item.track.id for item in items]
    if not track_ids:
        raise HTTPException(status_code=400, detail="Playlist is empty")
    if shuffle:
        random.shuffle(track_ids)
    state.playback.play_now(track_id or track_ids[0], track_ids)
    return {"status": "success"}


@router.post("/api/playback/play")
def play(track_id: str | list[str] = Body(..., embed=True), shuffle: bool = Body(False, embed=True),
          start_track_id: str | None = Body(None, embed=True)):
    tracks = [track_id] if isinstance(track_id, str) else track_id
    if not tracks:
        raise HTTPException(status_code=400, detail="No tracks provided")
    if shuffle:
        random.shuffle(tracks)
    state.playback.play_now(start_track_id or tracks[0], tracks)
    return {"status": "success"}


@router.post("/api/playback/start_radio/track/{track_id}")
def start_radio_track(track_id: str):
    try:
        state.playback.start_radio(track_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "success"}


@router.post("/api/playback/start_radio/album/{album_id}")
def start_radio_album(album_id: str):
    album = Album.get_or_none(Album.id == album_id)
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")
    seed_ids = radio.pick_seed_tracks("album", album_id, library_ids=state.settings.get("jellyfin_library_ids"))
    if not seed_ids:
        raise HTTPException(status_code=404, detail="Album has no tracks")
    try:
        state.playback.start_radio_from_reference(seed_ids[0], extra_seed_ids=seed_ids[1:])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "success"}


@router.post("/api/playback/start_radio/artist/{artist_id}")
def start_radio_artist(artist_id: str):
    artist = Artist.get_or_none(Artist.id == artist_id)
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")
    seed_ids = radio.pick_seed_tracks("artist", artist_id, library_ids=state.settings.get("jellyfin_library_ids"))
    if not seed_ids:
        raise HTTPException(status_code=404, detail="Artist has no tracks")
    try:
        state.playback.start_radio_from_reference(seed_ids[0], extra_seed_ids=seed_ids[1:])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "success"}


@router.post("/api/playback/start_radio/playlist/{playlist_id}")
def start_radio_playlist(playlist_id: str):
    playlist = Playlist.get_or_none(Playlist.id == playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    seed_ids = radio.pick_seed_tracks("playlist", playlist_id)
    if not seed_ids:
        raise HTTPException(status_code=404, detail="Playlist has no tracks")
    try:
        state.playback.start_radio_from_reference(seed_ids[0], extra_seed_ids=seed_ids[1:])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "success"}


@router.post("/api/playback/toggle_pause")
def toggle_pause():
    state.playback.toggle_pause()
    return {"is_paused": state.playback.is_paused}


@router.post("/api/playback/next")
def skip_next():
    state.playback.skip_next()
    return {"status": "success"}


@router.post("/api/playback/prev")
def skip_prev():
    state.playback.skip_prev()
    return {"status": "success"}


@router.post("/api/playback/seek/{seconds}")
def seek(seconds: float):
    state.playback.seek(seconds)
    return {"status": "success"}


@router.post("/api/playback/add_to_queue")
def add_to_queue(
    track_id: str | list[str] = Body(...),
    index: int = Body(-1)
):
    tracks = [track_id] if isinstance(track_id, str) else track_id
    state.playback.add_to_queue(tracks, index)
    return {"status": "success"}


@router.post("/api/playback/play_next")
def play_next(
    track_id: str | list[str] = Body(...),
    top: bool = Body(True)
):
    tracks = [track_id] if isinstance(track_id, str) else track_id
    state.playback.add_to_play_next(tracks, top=top)
    return {"status": "success"}


@router.post("/api/playback/remove_from_queue/{queue_item_id}")
def remove_from_queue(queue_item_id: str):
    state.playback.remove_from_queue(queue_item_id)
    return {"status": "success"}


@router.post("/api/playback/jump_to_queue_item/{queue_item_id}")
def jump_to_queue_item(queue_item_id: str):
    state.playback.jump_to_queue_item(queue_item_id)
    return {"status": "success"}


@router.get("/api/playback/queue")
def get_queue():
    playback = state.playback
    current = playback._get_current()
    current_id = current.id if current else None
    queue = QueueItem.select(QueueItem, Track, Artist).join(Track).join(Artist).order_by(QueueItem.position)
    return [
        {
            "id": q.id,
            "track_id": q.track.id,
            "title": q.track.title,
            "artist_name": q.track.artist.name if q.track.artist else "Unknown Artist",
            "duration_ms": q.track.duration_ms,
            "is_current": q.id == current_id,
        }
        for q in queue
    ]


def _move_queue_item(item_id, position):
    """Move a queue item relative to the current track ("next" or "end")."""
    playback = state.playback
    current = playback._get_current()
    if not current:
        return
    sorted_others = list(
        QueueItem.select()
        .where(QueueItem.id != item_id)
        .order_by(QueueItem.position)
    )
    current_idx = next(
        (i for i, q in enumerate(sorted_others) if q.id == current.id), 0
    )
    target_idx = current_idx + 1 if position == "next" else len(sorted_others)
    playback.reorder_queue(item_id, target_idx)


@router.websocket("/ws/playback")
async def playback_ws(websocket: WebSocket):
    await websocket.accept()

    loop = asyncio.get_running_loop()

    def on_state_update(new_state):
        asyncio.run_coroutine_threadsafe(websocket.send_json(new_state), loop)

    playback = state.playback
    playback.add_listener(on_state_update)

    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")

            if action == "toggle_pause":
                playback.toggle_pause()
            elif action == "skip_next":
                playback.skip_next()
            elif action == "skip_prev":
                playback.skip_prev()
            elif action == "seek":
                playback.seek(data.get("value"))
            elif action == "jump_to_queue_item":
                playback.jump_to_queue_item(data.get("value"))
            elif action == "remove_from_queue":
                playback.remove_from_queue(data.get("value"))
            elif action == "set_volume":
                playback.set_volume(data.get("value", 100))
            elif action == "clear_queue":
                playback.clear_queue()
            elif action == "set_radio_enabled":
                playback.set_radio_enabled(bool(data.get("value", False)))
            elif action == "set_repeat":
                playback.set_repeat(int(data.get("value", 0)))
                await websocket.send_json({"repeat_mode": playback.repeat_mode})
            elif action == "set_shuffle":
                playback.set_shuffle(bool(data.get("value", False)))
                await websocket.send_json({
                    "shuffle": playback.shuffle,
                    "queue": playback.build_queue_state(),
                })
            elif action == "move_queue_item":
                val = data.get("value") or {}
                if val.get("id") and val.get("position"):
                    _move_queue_item(val["id"], val["position"])

    except WebSocketDisconnect:
        pass
    finally:
        playback.remove_listener(on_state_update)
