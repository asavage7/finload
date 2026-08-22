"""Image serving: cover art resolution, caching headers, playlist covers."""
import os
import threading

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from core import state
from core.database import Track, db as peewee_db

router = APIRouter()

# Concurrent requests for different tracks on the same album all resolve to the
# same cache path (the album cover). Without a lock, simultaneous cache misses
# each start their own download and write to that path independently, and the
# interleaved writes corrupt whichever file loses the race. One lock per path
# serializes that so only the first request downloads; the rest just wait and
# then hit the now-populated cache.
_download_locks: dict[str, threading.Lock] = {}
_download_locks_guard = threading.Lock()


def _lock_for(path: str) -> threading.Lock:
    with _download_locks_guard:
        lock = _download_locks.get(path)
        if lock is None:
            lock = threading.Lock()
            _download_locks[path] = lock
        return lock

# Mutable images (user-uploaded playlist covers) must not be cached by the
# browser; they're re-fetched with a cache-busting param when they change.
MUTABLE_IMAGE_HEADERS = {"Cache-Control": "no-store"}
# Album/track/artist art is content-addressed by {item_id}_{size}, so it's
# effectively immutable. Let the browser cache it hard to avoid re-fetching
# every cover on each virtualized scroll.
IMMUTABLE_IMAGE_HEADERS = {"Cache-Control": "public, max-age=604800, immutable"}

MAX_IMAGE_SIZE = 2000


def playlist_image_dir() -> str:
    image_dir = os.path.join(os.path.dirname(peewee_db.database), "playlist_images")
    os.makedirs(image_dir, exist_ok=True)
    return image_dir


def playlist_image_path(playlist_id: str) -> str:
    return os.path.join(playlist_image_dir(), f"{playlist_id}.jpg")


def _art_candidates(item_id: str, type: str) -> list[str]:
    """IDs to try, in order, when resolving art for an item.

    Tracks normally just show their album's cover. With the
    use_album_art_for_tracks setting off, per-track art is tried first and the
    album cover stays as the fallback.
    """
    if type != "track":
        return [item_id]
    track = Track.get_or_none(Track.id == item_id)
    if not track:
        return []
    album_id = str(track.album_id) if track.album_id else None
    if state.settings.get("use_album_art_for_tracks"):
        return [album_id] if album_id else [item_id]
    return [item_id] + ([album_id] if album_id else [])


def resolve_image_path(item_id: str, size: int, type: str = "album") -> str | None:
    """Return a local file path for an item's art, downloading it on a cache miss."""
    size = min(size, MAX_IMAGE_SIZE)
    for candidate in _art_candidates(item_id, type):
        path = state.provider.get_cached_image_path(candidate, size)
        if os.path.exists(path):
            return path
        with _lock_for(path):
            if os.path.exists(path):  # another request already filled it in while we waited
                return path
            if state.provider.download_image_to_cache(candidate, size):
                return path
    return None


@router.get("/api/image/{item_id}")
def get_image(item_id: str, size: int = 240, type: str = "album"):
    if type == "playlist":
        image_path = playlist_image_path(item_id)
        if os.path.exists(image_path):
            return FileResponse(image_path, headers=MUTABLE_IMAGE_HEADERS)
        raise HTTPException(status_code=404, detail="Playlist image not found")

    path = resolve_image_path(item_id, size, type)
    if path:
        return FileResponse(path, headers=IMMUTABLE_IMAGE_HEADERS)
    raise HTTPException(status_code=404, detail="Image not found")
