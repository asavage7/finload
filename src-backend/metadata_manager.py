"""Online metadata enrichment via TheAudioDB.

Fetches artist bios and profile images. Runs as a background task triggered
after library sync; individual artists can also be enriched on demand (the
artist page requests this the first time it renders an artist with no bio).
Progress is exposed as a plain state dict that the frontend polls.

TheAudioDB free key: "123" (personal use). Configurable via settings.
"""
import datetime
import io
import json
import threading
import urllib.parse
import urllib.request
from typing import Optional

from PIL import Image

from background import BackgroundJob
from config import USER_AGENT
from database import Artist
from providers.base import cached_image_path

_REQUEST_TIMEOUT = 10

# Sizes to cache for the artist profile thumb (matches the image cache convention).
_THUMB_SIZES = (240, 800)


class MetadataManager(BackgroundJob):
    def __init__(self, settings):
        super().__init__()
        self._settings = settings

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

        Fire-and-forget for one item, not tracked via ``self.state`` — a
        separate concern from the "enrich everything" job above.
        """
        if not self._settings.get("enable_online_metadata"):
            return False
        artist = Artist.get_or_none(Artist.id == artist_id)
        if not artist or artist.enriched_at is not None:
            return False
        threading.Thread(target=self._enrich_artist, args=(artist,), daemon=True).start()
        return True

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------

    def _run(self, force: bool = False) -> None:
        self._emit(message="Gathering artists to enrich…")

        if force:
            artists = list(Artist.select())
        else:
            artists = list(Artist.select().where(Artist.enriched_at.is_null()))

        self._emit(total=len(artists))

        for processed, artist in enumerate(artists, start=1):
            self._enrich_artist(artist)
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
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            print(f"[metadata] TheAudioDB request failed ({path}): {exc}")
            return None

    def _download_image(self, url: str, dest_path: str, max_width: int = 0) -> bool:
        """Download an image URL to dest_path, optionally resizing to max_width."""
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                data = resp.read()
            with Image.open(io.BytesIO(data)) as img:
                img = img.convert("RGB")
                if max_width and img.width > max_width:
                    ratio = max_width / img.width
                    img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)
                img.save(dest_path, "JPEG", quality=88)
            return True
        except Exception as exc:
            print(f"[metadata] Image download failed ({url}): {exc}")
            return False

    # ------------------------------------------------------------------
    # Enrichment logic
    # ------------------------------------------------------------------

    def _enrich_artist(self, artist: Artist) -> None:
        """Fetch bio + profile images for one artist from TheAudioDB."""
        now = datetime.datetime.now()
        data = self._tadb_get("search.php", {"s": artist.name})
        artists_list = (data or {}).get("artists") or []

        bio = ""
        bio_source = ""
        tadb_id = None

        if artists_list:
            entry = artists_list[0]
            tadb_id = str(entry.get("idArtist") or "")
            bio = entry.get("strBiographyEN") or entry.get("strBiography") or ""
            bio_source = "theaudiodb" if bio else ""

            # Profile thumb, cached at the same sizes the image API serves.
            thumb_url = entry.get("strArtistThumb") or ""
            if thumb_url:
                for size in _THUMB_SIZES:
                    self._download_image(thumb_url, cached_image_path(artist.id, size), max_width=size)

        Artist.update(
            bio=bio,
            bio_source=bio_source,
            tadb_id=tadb_id or None,
            enriched_at=now,
        ).where(Artist.id == artist.id).execute()
