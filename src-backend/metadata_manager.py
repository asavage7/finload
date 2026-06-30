"""Online metadata enrichment via TheAudioDB.

Fetches artist bios, profile images, fanart, and album descriptions.
Runs as a background task triggered after library sync. Uses the same
listener/state pattern as SyncManager so the frontend can poll progress.

TheAudioDB free key: "123" (personal use). Configurable via settings.
"""
import datetime
import io
import json
import threading
import time
import urllib.parse
import urllib.request
from typing import Optional

from PIL import Image

from database import Artist, Album, db

_REQUEST_TIMEOUT = 10
_USER_AGENT = "finload/1.0"

# Sizes to cache for the artist profile thumb (matches existing image cache convention).
_THUMB_SIZES = (220, 800)


class MetadataManager:
    def __init__(self, settings):
        self._settings = settings
        self._lock = threading.Lock()
        self.state = {
            "status": "idle",   # idle | running | complete | error
            "message": "",
            "processed": 0,
            "total": 0,
        }
        self._listeners: list = []

    # ------------------------------------------------------------------
    # Listener pattern (mirrors SyncManager / PlaybackManager)
    # ------------------------------------------------------------------

    def add_listener(self, callback) -> None:
        self._listeners.append(callback)
        callback(dict(self.state))

    def remove_listener(self, callback) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _emit(self, **changes) -> None:
        self.state.update(changes)
        snapshot = dict(self.state)
        for listener in self._listeners:
            try:
                listener(snapshot)
            except Exception:
                pass

    @property
    def is_running(self) -> bool:
        return self.state["status"] == "running"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_background_enrichment(self, force: bool = False) -> bool:
        """Enrich all un-enriched artists/albums in a background thread.

        force=True re-enriches items regardless of enriched_at.
        Returns False if already running or if online metadata is disabled.
        """
        if not self._settings.get("enable_online_metadata"):
            return False
        with self._lock:
            if self.is_running:
                return False
        threading.Thread(target=self._run, args=(force,), daemon=True).start()
        return True

    def enrich_artist_now(self, artist_id: str) -> bool:
        """Synchronously enrich a single artist (called on-demand from API)."""
        artist = Artist.get_or_none(Artist.id == artist_id)
        if not artist:
            return False
        self._enrich_artist(artist)
        return True

    def enrich_album_now(self, album_id: str) -> bool:
        """Synchronously enrich a single album (called on-demand from API)."""
        album = Album.get_or_none(Album.id == album_id)
        if not album:
            return False
        self._enrich_album(album)
        return True

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------

    def _run(self, force: bool) -> None:
        try:
            self._emit(status="running", message="Gathering items to enrich…", processed=0, total=0)

            if force:
                artists = list(Artist.select())
                albums = list(Album.select().join(Artist))
            else:
                artists = list(Artist.select().where(Artist.enriched_at.is_null()))
                albums = list(Album.select().join(Artist).where(Album.enriched_at.is_null()))

            total = len(artists) + len(albums)
            self._emit(total=total)
            processed = 0

            for artist in artists:
                self._enrich_artist(artist)
                processed += 1
                self._emit(processed=processed, message=f"Enriching: {artist.name}")

            for album in albums:
                self._enrich_album(album)
                processed += 1
                self._emit(processed=processed, message=f"Enriching: {album.title}")

            self._emit(status="complete", processed=processed,
                       message=f"Enriched {len(artists)} artists, {len(albums)} albums")
        except Exception as exc:
            self._emit(status="error", message=str(exc))

    # ------------------------------------------------------------------
    # TheAudioDB API helpers
    # ------------------------------------------------------------------

    def _api_key(self) -> str:
        return self._settings.get("theaudiodb_api_key") or "123"

    def _tadb_get(self, path: str, params: dict) -> Optional[dict]:
        """Make a GET request to TheAudioDB and return parsed JSON, or None on failure."""
        key = self._api_key()
        url = f"https://www.theaudiodb.com/api/v1/json/{key}/{path}?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            print(f"[metadata] TheAudioDB request failed ({path}): {exc}")
            return None

    def _download_image(self, url: str, dest_path: str, max_width: int = 0) -> bool:
        """Download an image URL to dest_path, optionally resizing to max_width."""
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
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

    def _cache_dir(self) -> str:
        """Return the image cache directory (matches BaseMediaProvider.cache_dir)."""
        import os
        from platformdirs import user_cache_dir
        cache_dir = user_cache_dir("finload")
        os.makedirs(cache_dir, exist_ok=True)
        return cache_dir

    def _thumb_path(self, artist_id: str, size: int) -> str:
        import os
        suffix = str(size) if size > 0 else "original"
        return os.path.join(self._cache_dir(), f"{artist_id}_{suffix}.jpg")

    def _fanart_path(self, artist_id: str) -> str:
        import os
        return os.path.join(self._cache_dir(), f"{artist_id}_fanart.jpg")

    # ------------------------------------------------------------------
    # Enrichment logic
    # ------------------------------------------------------------------

    def _enrich_artist(self, artist: Artist) -> None:
        """Fetch bio + images for one artist from TheAudioDB."""
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

            cache_dir = self._cache_dir()

            # Profile thumb — cached at multiple sizes for the circular artist image.
            thumb_url = entry.get("strArtistThumb") or ""
            if thumb_url:
                for size in _THUMB_SIZES:
                    self._download_image(thumb_url, self._thumb_path(artist.id, size), max_width=size)

            # Fanart — wide landscape image for the artist page banner.
            fanart_url = (entry.get("strArtistFanart")
                          or entry.get("strArtistFanart2")
                          or entry.get("strArtistFanart3")
                          or "")
            if fanart_url:
                self._download_image(fanart_url, self._fanart_path(artist.id), max_width=1280)

        Artist.update(
            bio=bio,
            bio_source=bio_source,
            tadb_id=tadb_id or None,
            enriched_at=now,
        ).where(Artist.id == artist.id).execute()

    def _enrich_album(self, album: Album) -> None:
        """Fetch description for one album from TheAudioDB."""
        now = datetime.datetime.now()
        artist_name = album.artist.name if album.artist else ""
        data = self._tadb_get("searchalbum.php", {"s": artist_name, "a": album.title})
        albums_list = (data or {}).get("album") or []

        description = ""
        tadb_id = None

        if albums_list:
            entry = albums_list[0]
            tadb_id = str(entry.get("idAlbum") or "")
            description = entry.get("strDescriptionEN") or entry.get("strDescription") or ""

        Album.update(
            description=description,
            tadb_id=tadb_id or None,
            enriched_at=now,
        ).where(Album.id == album.id).execute()
