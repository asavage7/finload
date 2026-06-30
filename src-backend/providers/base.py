"""Abstract media-source provider interface.

The API and playback layers talk only to ``MediaProvider``, never to a concrete
source. All source-specific logic (auth, URL schemes, metadata mapping) lives in
a concrete provider (e.g. ``JellyfinProvider``), so adding a new library source
— local files, another media server — means writing a new provider and
registering it in the factory, with no changes to the API or playback code.

Normalized item schema yielded by ``fetch_items_by_ids`` (keys map directly onto
the Peewee models in ``database.py``)::

    {
        "artists": [ {"id", "name"}, ... ],
        "album_data": {"id", "title", "artist", "release_year", "genre"},
        "track_data": {"id", "title", "artist", "album", "track_number",
                       "disc_number", "duration_ms", "has_artwork"},
    }
"""
from abc import ABC, abstractmethod
from typing import Iterator, List, Set
import os

from platformdirs import user_cache_dir


class MediaProvider(ABC):
    """Common interface every library source must implement."""

    # Settings keys this provider reads. The API layer uses this to decide when
    # a settings change should trigger a reconfigure without knowing which
    # keys belong to which source.
    SETTINGS_KEYS: tuple = ()

    def __init__(self) -> None:
        self.cache_dir = user_cache_dir("finload")
        os.makedirs(self.cache_dir, exist_ok=True)

    # Configuration
    @abstractmethod
    def configure(self, settings) -> None:
        """(Re)read configuration from the settings manager, in place.

        Mutating the existing instance keeps references held elsewhere (e.g.
        ``PlaybackManager.provider``) valid, so settings changes take effect
        without an app restart.
        """

    def is_configured(self) -> bool:
        """Whether the provider has enough config to reach its source."""
        return True

    # Library Sync
    @abstractmethod
    def fetch_all_ids(self) -> Set[str]:
        """Return the set of all track IDs currently available from the source."""

    @abstractmethod
    def fetch_items_by_ids(self, item_ids: List[str]) -> Iterator[dict]:
        """Yield normalized item dicts (see module docstring) for the given IDs."""

    # Playback
    @abstractmethod
    def get_stream_url(self, track_id: str) -> str:
        """Return a URL/path that mpv can play for the given track."""

    # Images
    def get_cached_image_path(self, item_id: str, size_px: int = 0) -> str:
        """Local cache path for an item's primary image at a given width.

        The cache layout is shared across providers; only the *download* of an
        image is source-specific.
        """
        suffix = str(size_px) if size_px > 0 else "original"
        return os.path.join(self.cache_dir, f"{item_id}_{suffix}.jpg")

    @abstractmethod
    def download_image_to_cache(self, item_id: str, size_px: int = 0) -> bool:
        """Fetch an item's primary image into the cache; return True on success."""

    # Lyrics
    def get_lyrics(self, track_id: str, lrclib_enabled: bool = True,
                   synced_enabled: bool = True) -> dict:
        """Return lyrics for a track. Default: none found."""
        return {"type": "none"}
