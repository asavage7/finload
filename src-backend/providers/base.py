"""Abstract media-source provider interface.

API talks to a generic MediaProvider, which is implemented by each library source separately.

Normalized item schema:

    {
        "artists": [ {"id", "name", "mbid"}, ... ],
        "album_data": {"id", "title", "artist", "release_year", "mbid"},
        "track_data": {"id", "title", "artist", "album", "track_number",
                       "disc_number", "duration_ms", "mbid"},
        "genres": ["Rock", "Alternative Rock", ...],
    }

See database.py for more information on what each field means.
"""
from abc import ABC, abstractmethod
from typing import Iterator, List, Optional, Set
import io
import os
import threading

from PIL import Image
from platformdirs import user_cache_dir


def get_cache_dir() -> str:
    """Gets the image cache directory."""
    cache_dir = user_cache_dir("finload")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir

def cached_image_path(item_id: str, size_px: int = 0) -> str:
    """Cache path for an item's primary image at a given width."""
    suffix = str(size_px) if size_px > 0 else "original"
    return os.path.join(get_cache_dir(), f"{item_id}_{suffix}.jpg")


def atomic_write_bytes(data: bytes, dest_path: str) -> None:
    """Write bytes to dest_path via a per-call temp file + rename."""
    tmp_path = f"{dest_path}.{os.getpid()}-{threading.get_ident()}.tmp"
    try:
        with open(tmp_path, "wb") as f:
            f.write(data)
        os.replace(tmp_path, dest_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def resize_and_save_jpeg(data: bytes, dest_path: str, max_width: int = 0, quality: int = 88) -> None:
    """Decode image bytes, optionally downscale to max_width, and atomically
    save as JPEG at dest_path. Raises on failure."""
    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGB")
        if max_width and img.width > max_width:
            ratio = max_width / img.width
            img = img.resize((max_width, max(1, round(img.height * ratio))), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
    atomic_write_bytes(buf.getvalue(), dest_path)


class MediaProvider(ABC):
    """Common interface every library source must implement."""

    # Settings keys this provider reads. Used to reconfigure the provider when settings change.
    SETTINGS_KEYS: tuple = ()

    def __init__(self) -> None:
        self.cache_dir = get_cache_dir()

    # Configuration
    @abstractmethod
    def configure(self, settings) -> None:
        """Read settings and configure the provider."""

    def is_configured(self) -> bool:
        """Whether the provider has enough config to reach its source."""
        return True

    # Library Sync
    @abstractmethod
    def fetch_all_ids(self) -> Set[str]:
        """Return the set of all track IDs currently available from the source."""

    @abstractmethod
    def fetch_items_by_ids(self, item_ids: List[str]) -> Iterator[dict]:
        """Return normalized item dicts (see docstring) for the given IDs."""

    @abstractmethod
    def fetch_changed_ids(self, since: str) -> Optional[Set[str]]:
        """Return IDs of tracks added or modified on the source since last sync."""
        return None

    # Playback
    @abstractmethod
    def get_stream_url(self, track_id: str) -> str:
        """Return a URL/path that mpv can play for the given track."""

    def get_seeked_stream(self, track_id: str, start_seconds: float) -> tuple[str, float]:
        """A stream for starting a track midway.

        Returns (url, remaining_seek). Either keep the same URL and set remaining seek,
        or set a new URL and set remiaining seek to 0.
        """
        return self.get_stream_url(track_id), start_seconds

    # Analysis
    def get_analysis_stream_url(self, track_id: str) -> str:
        """Returns a URL/path to read for audio analysis.
        For remote sources this can be a lower bitrate version to save resources."""
        return self.get_stream_url(track_id)

    def reauthenticate(self) -> bool:
        """Re-exchange stored credentials for a fresh token, for callers that read
        a stream URL directly instead of through the provider's own request
        helper. False when the provider has no credentials to refresh."""
        return False

    # Images
    def get_cached_image_path(self, item_id: str, size_px: int = 0) -> str | None:
        return cached_image_path(item_id, size_px)

    def get_closest_image_path(self, item_id: str, size_px: int = 0) -> str | None:
        """Return the path to the closest cached image size for an item, or None if none exist."""
        original_path = os.path.join(get_cache_dir(), f"{item_id}_original.jpg")
        if os.path.exists(original_path):
            return original_path
        cached_sizes = []
        for filename in os.listdir(get_cache_dir()):
            if filename.startswith(f"{item_id}_") and filename.endswith(".jpg"):
                size_str = filename[len(item_id) + 1:-4]
                if size_str.isdigit():
                    cached_sizes.append(int(size_str))
        if cached_sizes:
            nearest_size = min(cached_sizes, key=lambda s: abs(s - size_px))
            return os.path.join(get_cache_dir(), f"{item_id}_{nearest_size}.jpg")
        return None
    
    @abstractmethod
    def download_image_to_cache(self, item_id: str, size_px: int = 0) -> bool:
        """Fetch an item's primary image into the cache. Returns True on success."""

    # Lyrics
    def get_lyrics(self, track_id: str, lrclib_enabled: bool = True,
                   synced_enabled: bool = True) -> dict:
        """Return lyrics for a track. Default: none found."""
        return {"type": "none"}

    # Playback reporting
    def report_play(self, track_id: str) -> None:
        """Report a completed play (scrobble) back to the source so its play
        count and last-played date update and sync to other clients."""
        return None
