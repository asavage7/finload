"""Abstract media-source provider interface.

The API and playback layers talk only to ``MediaProvider``, never to a concrete
source. All source-specific logic (auth, URL schemes, metadata mapping) lives in
a concrete provider (e.g. ``JellyfinProvider``), so adding a new library source
— local files, another media server — means writing a new provider and
registering it in the factory, with no changes to the API or playback code.

Normalized item schema yielded by ``fetch_items_by_ids`` (keys map directly onto
the Peewee models in ``database.py``)::

    {
        "artists": [ {"id", "name", "mbid"}, ... ],
        "album_data": {"id", "title", "artist", "release_year", "mbid"},
        "track_data": {"id", "title", "artist", "album", "track_number",
                       "disc_number", "duration_ms", "mbid"},
        "genres": ["Rock", "Alternative Rock", ...],
    }

``mbid`` is the MusicBrainz ID for that entity (Recording for a track, Release
Group for an album, Artist for an artist) if the provider can supply one, else
``None`` — genre enrichment uses it to query MusicBrainz directly instead of
falling back to fuzzier by-name lookups.

``genres`` is a flat list of raw genre/tag name strings for this track, applied
to both the track and its album by the sync layer (tagged with this item's own
``provider`` as the source — see ``database.DatabaseManager.link_genres``).
Providers don't dedupe, normalize casing, or join them into a string; that's
the sync/DB layer's job so genres stay queryable per-entity instead of baked
into one opaque field.
"""
from abc import ABC, abstractmethod
from typing import Iterator, List, Optional, Set
import os

from platformdirs import user_cache_dir


def get_cache_dir() -> str:
    """The shared on-disk image cache directory."""
    cache_dir = user_cache_dir("finload")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def cached_image_path(item_id: str, size_px: int = 0) -> str:
    """Cache path for an item's primary image at a given width.

    The layout is shared by every provider and by metadata enrichment; only the
    download of an image is source-specific.
    """
    suffix = str(size_px) if size_px > 0 else "original"
    return os.path.join(get_cache_dir(), f"{item_id}_{suffix}.jpg")


class MediaProvider(ABC):
    """Common interface every library source must implement."""

    # Settings keys this provider reads. The API layer uses this to decide when
    # a settings change should trigger a reconfigure without knowing which
    # keys belong to which source.
    SETTINGS_KEYS: tuple = ()

    def __init__(self) -> None:
        self.cache_dir = get_cache_dir()

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

    def fetch_changed_ids(self, since: str) -> Optional[Set[str]]:
        """Return IDs of tracks added or modified on the source since ``since``
        (an ISO-8601 UTC timestamp), so a sync can catch in-place edits
        (retagged genre, corrected metadata, ...) on tracks it already knows
        about without re-fetching the whole library. ``None`` means the source
        can't report deltas any cheaper than a full re-sync, so the sync layer
        only fetches IDs it doesn't already have.
        """
        return None

    # Playback
    @abstractmethod
    def get_stream_url(self, track_id: str) -> str:
        """Return a URL/path that mpv can play for the given track."""

    def get_seeked_stream(self, track_id: str, start_seconds: float) -> tuple[str, float]:
        """A stream for playing a track from ``start_seconds`` in.

        Returns ``(url, remaining_seek)``. The default leaves the whole seek to
        the player, which for a local file or a byte-range-capable server costs
        nothing worth avoiding. Providers that can position the stream
        themselves (e.g. Jellyfin, see its override) should return a URL that
        already starts at the offset and a remaining seek of 0, so playback
        begins immediately instead of after the player has sought there.
        """
        return self.get_stream_url(track_id), start_seconds

    # Analysis (offline feature extraction, see audio_analysis.py)
    def get_analysis_stream_url(self, track_id: str) -> str:
        """A cheap-to-transfer URL for offline feature extraction — doesn't
        need to preserve audio quality, just needs to decode to the same
        perceptual content. Defaults to get_stream_url; providers that can
        transcode server-side (e.g. Jellyfin) should override this to request
        a small low-bitrate mono stream instead of the full-quality original,
        since downloading full FLACs just to throw away the audio after
        feature extraction is pure wasted bandwidth/time.
        """
        return self.get_stream_url(track_id)

    # Images
    def get_cached_image_path(self, item_id: str, size_px: int = 0) -> str:
        return cached_image_path(item_id, size_px)

    @abstractmethod
    def download_image_to_cache(self, item_id: str, size_px: int = 0) -> bool:
        """Fetch an item's primary image into the cache; return True on success."""

    # Lyrics
    def get_lyrics(self, track_id: str, lrclib_enabled: bool = True,
                   synced_enabled: bool = True) -> dict:
        """Return lyrics for a track. Default: none found."""
        return {"type": "none"}

    # Playback reporting
    def report_play(self, track_id: str) -> None:
        """Report a completed play (scrobble) back to the source so its play
        count and last-played date update and sync to other clients. Sources
        with no server to report to (e.g. local files) do nothing."""
        return None
