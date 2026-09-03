"""Image serving: cover art resolution, caching headers, playlist covers."""
import os
import threading

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from core import state
from core.database import Track, db as peewee_db

router = APIRouter()

# Locks to avoid file corruption on concurrent downloads of the same image.
_download_locks: dict[str, threading.Lock] = {}
_download_locks_guard = threading.Lock()


def _lock_for(path: str) -> threading.Lock:
    with _download_locks_guard:
        lock = _download_locks.get(path)
        if lock is None:
            lock = threading.Lock()
            _download_locks[path] = lock
        return lock

# Mutable images (mainly playlist covers) must not be cached by the browser
MUTABLE_IMAGE_HEADERS = {"Cache-Control": "no-store"}
# Alternatively, immutable images (cover art) can be cached for a long time
IMMUTABLE_IMAGE_HEADERS = {"Cache-Control": "public, max-age=604800, immutable"}

MAX_IMAGE_SIZE = 2000


def playlist_image_dir() -> str:
    image_dir = os.path.join(os.path.dirname(peewee_db.database), "playlist_images")
    os.makedirs(image_dir, exist_ok=True)
    return image_dir


def playlist_image_path(playlist_id: str) -> str:
    return os.path.join(playlist_image_dir(), f"{playlist_id}.jpg")


def _art_candidates(item_id: str, type: str) -> list[str]:
    """IDs to try, in order, when resolving art for an item."""
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
    size = min(size, MAX_IMAGE_SIZE)
    candidates = _art_candidates(item_id, type)

    # 1. Try to find cached image at correct size for all candidates
    for candidate in candidates:
        path = state.provider.get_cached_image_path(candidate, size)
        if path is not None and os.path.exists(path):
            return path

    # 2. Try to download image at correct size
    for candidate in candidates:
        with _lock_for(f"{candidate}_{size}"):
            # Double-check cache in case another thread downloaded it
            path = state.provider.get_cached_image_path(candidate, size)
            if path is not None and os.path.exists(path): 
                return path
            
            if state.provider.download_image_to_cache(candidate, size):
                path = state.provider.get_cached_image_path(candidate, size)
                if path is not None and os.path.exists(path):
                    return path

    # 3. Find the closest size image
    for candidate in candidates:
        path = state.provider.get_closest_image_path(candidate, size)
        if path is not None:
            return path

    return None


@router.get("/api/image/{item_id}")
def get_image(item_id: str, size: int = 240, type: str = "album"):
    type = type.lower()
    if type == "playlist":
        image_path = playlist_image_path(item_id)
        if os.path.exists(image_path):
            return FileResponse(image_path, headers=MUTABLE_IMAGE_HEADERS)
        raise HTTPException(status_code=404, detail="Playlist image not found")

    path = resolve_image_path(item_id, size, type)
    if path:
        return FileResponse(path, headers=IMMUTABLE_IMAGE_HEADERS)
    raise HTTPException(status_code=404, detail="Image not found")
