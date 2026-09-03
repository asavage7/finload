"""Online metadata enrichment via TheAudioDB.

Fetches artist bios and profile images. Runs as a background task triggered
after library sync; individual artists can also be enriched on demand (the
artist page requests this the first time it renders an artist with no bio).
Progress is exposed as a plain state dict that the frontend polls.

TheAudioDB free key: "123" (personal use). Configurable via settings.
"""
import datetime
import logging
import threading
import urllib.parse
from typing import Optional

from core.database import Artist
from core.http import RateLimiter, fetch_bytes, fetch_json
from services.background import BackgroundJob
from providers.base import cached_image_path, resize_and_save_jpeg

logger = logging.getLogger(__name__)

# Sizes to cache for the artist profile thumb (matches the image cache convention).
_THUMB_SIZES = (240, 800)

# TheAudioDB's free key is shared and rate limited; a full library would
# otherwise fire thousands of requests back to back.
_tadb_limiter = RateLimiter(0.5)


class MetadataManager(BackgroundJob):
    supports_force = True

    def __init__(self, settings):
        super().__init__()
        self._settings = settings
        # Artist ids with a one-off enrichment thread in flight, so repeated
        # visits to the same artist page don't spawn duplicate lookups.
        self._in_flight: set = set()
        self._in_flight_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, force: bool = False) -> bool:
        """Enrich all un-enriched artists in a background thread.

        force=True re-enriches artists regardless of enriched_at.
        Returns False if already running or if online metadata is disabled.
        """
        if not self._settings.get("enable_online_metadata"):
            return False
        return super().start(force=force)

    def enrich_artist_async(self, artist_id: str) -> bool:
        """Enrich a single artist in the background, skipping already-enriched ones.

        Fire-and-forget for one item, not tracked via ``self.state`` - a
        separate concern from the "enrich everything" job above.
        """
        if not self._settings.get("enable_online_metadata"):
            return False
        artist = Artist.get_or_none(Artist.id == artist_id)
        if not artist or artist.enriched_at is not None:
            return False
        with self._in_flight_lock:
            if artist_id in self._in_flight:
                return False
            self._in_flight.add(artist_id)
        threading.Thread(target=self._enrich_one_off, args=(artist,), daemon=True).start()
        return True

    def _enrich_one_off(self, artist: Artist) -> None:
        try:
            self._enrich_artist(artist)
        except Exception as exc:
            logger.warning("Enrichment failed for %s: %s", artist.name, exc)
        finally:
            with self._in_flight_lock:
                self._in_flight.discard(artist.id)

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------

    def _run(self, force: bool = False) -> None:
        self._emit(message="Gathering artists to enrich...")

        if force:
            artists = list(Artist.select())
        else:
            artists = list(Artist.select().where(Artist.enriched_at.is_null()))

        self._emit(total=len(artists))

        for processed, artist in enumerate(artists, start=1):
            if not self._settings.get("enable_online_metadata"):
                # Setting turned off mid-run (same gate start() checks) --
                # stop rather than keep working on a disabled feature.
                self._emit(status="idle", message="Stopped - disabled in settings")
                return
            if self.should_stop():
                self._emit(status="idle", message="Stopped")
                return
            try:
                self._enrich_artist(artist)
            except Exception as exc:
                # One artist failing must not abort every artist queued behind
                # it; enriched_at stays null so the next run retries it.
                logger.warning("Enrichment failed for %s: %s", artist.name, exc)
            self._emit(processed=processed, message=f"Enriching: {artist.name}")

        self._emit(status="complete", message=f"Enriched {len(artists)} artists")

    # ------------------------------------------------------------------
    # TheAudioDB API helpers
    # ------------------------------------------------------------------

    def _api_key(self) -> str:
        return self._settings.get("theaudiodb_api_key") or "123"

    def _tadb_get(self, path: str, params: dict) -> Optional[dict]:
        """Make a GET request to TheAudioDB and return parsed JSON, or None on failure."""
        key = self._api_key()
        url = f"https://www.theaudiodb.com/api/v1/json/{key}/{path}?" + urllib.parse.urlencode(params)
        _tadb_limiter.wait()
        return fetch_json(url)

    def _cache_thumb(self, url: str, artist_id: str) -> None:
        """Download the profile thumb once and write every cached size from it."""
        data = fetch_bytes(url)
        if data is None:
            return
        for size in _THUMB_SIZES:
            try:
                resize_and_save_jpeg(data, cached_image_path(artist_id, size), max_width=size)
            except Exception as exc:
                logger.warning("Image processing failed (%s): %s", url, exc)
                return

    # ------------------------------------------------------------------
    # Enrichment logic
    # ------------------------------------------------------------------

    def _enrich_artist(self, artist: Artist) -> None:
        """Fetch bio + profile images for one artist from TheAudioDB."""
        data = self._tadb_get("search.php", {"s": artist.name})
        if data is None:
            # The request itself failed (offline, rate limited, 5xx). Leave
            # enriched_at null so the next run retries instead of writing this
            # artist off as permanently having no bio.
            logger.warning("Metadata lookup failed for %s; will retry", artist.name)
            return

        fields = {"enriched_at": datetime.datetime.now()}
        entry = (data.get("artists") or [None])[0]
        if entry:
            fields["tadb_id"] = str(entry.get("idArtist") or "") or None
            bio = entry.get("strBiographyEN") or entry.get("strBiography") or ""
            # Only overwrite when there's something to write: a force re-run
            # that comes back thin must not blank out a bio already on file.
            if bio:
                fields.update(bio=bio, bio_source="theaudiodb")
            thumb_url = entry.get("strArtistThumb") or ""
            if thumb_url:
                self._cache_thumb(thumb_url, artist.id)

        Artist.update(**fields).where(Artist.id == artist.id).execute()
