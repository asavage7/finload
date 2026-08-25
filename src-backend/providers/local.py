"""Local-files media provider.

Scans a folder of audio files and exposes them through the same
``MediaProvider`` interface the rest of the app uses, so local libraries behave
exactly like a Jellyfin server: the API, sync and playback layers never learn
that the source is the local disk.

Identity & persistence
-----------------------
Track / album / artist IDs are derived deterministically from tags (and, for
tracks, the absolute file path) so they stay stable across rescans. The
``file_path`` column on the ``Track`` DB row maps each track ID back to a file
on disk, so playback and artwork work after app restarts without a re-scan.
"""
import base64
import datetime
import hashlib
import logging
import os
import re
from typing import Iterator, List, Optional, Set

import mutagen
from mutagen.flac import Picture

from core.database import Track
from .base import MediaProvider, resize_and_save_jpeg
from .lyrics import NO_LYRICS, fetch_lrclib, parse_lrc

logger = logging.getLogger(__name__)

# File extensions we treat as playable audio.
AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".m4a", ".aac", ".alac", ".ogg", ".oga", ".opus",
    ".wav", ".aiff", ".aif", ".wma", ".ape", ".mpc", ".wv",
}

# Cover-art filenames looked for in a track's directory (case-insensitive),
# in priority order, when a file has no embedded artwork.
COVER_BASENAMES = ("cover", "folder", "front", "album", "albumart", "thumb")
COVER_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")


def _stable_hash(*parts: str) -> str:
    """A short, stable hex digest of the given strings."""
    joined = "\x00".join(parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:20]


def _first(value, default: str = "") -> str:
    """Mutagen easy-tags return lists; take the first non-empty value."""
    if isinstance(value, (list, tuple)):
        return str(value[0]).strip() if value else default
    if value is None:
        return default
    return str(value).strip() or default


def _split_combined_genre(value: str) -> list[str]:
    """Some taggers store multiple genres in one field joined by ';'"""
    return [p.strip() for p in value.split(";") if p.strip()]


def _parse_int(value: str) -> int:
    """Parse leading digits from tag values like '3', '03/12' or 'Disc 1'."""
    match = re.search(r"\d+", value or "")
    return int(match.group()) if match else 0


def _parse_year(value: str) -> int:
    match = re.search(r"\d{4}", value or "")
    return int(match.group()) if match else 0


def _file_mtime(path: str) -> Optional[datetime.datetime]:
    try:
        return datetime.datetime.fromtimestamp(os.stat(path).st_mtime)
    except OSError:
        return None


class LocalProvider(MediaProvider):
    SETTINGS_KEYS = ("local_music_path",)

    def __init__(self, settings) -> None:
        super().__init__()
        # track_id -> normalized item dict, populated during a full scan so
        # ``fetch_items_by_ids`` doesn't have to re-read files it just parsed.
        self._scan_cache: dict = {}
        self.configure(settings)

    # -- configuration ------------------------------------------------------
    def configure(self, settings) -> None:
        path = (settings.get("local_music_path") or "").strip()
        self.music_path = os.path.abspath(os.path.expanduser(path)) if path else ""

    def is_configured(self) -> bool:
        return bool(self.music_path and os.path.isdir(self.music_path))

    # -- file walking & tag parsing ----------------------------------------
    def _iter_audio_files(self) -> Iterator[str]:
        for root, _dirs, files in os.walk(self.music_path):
            for name in files:
                if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS:
                    yield os.path.join(root, name)

    def _parse_file(self, path: str) -> Optional[dict]:
        """Read tags from one file and build a normalized item dict.

        Returns ``None`` if the file can't be read as audio.
        """
        try:
            easy = mutagen.File(path, easy=True)
        except Exception:
            easy = None
        if easy is None:
            return None

        tags = easy.tags or {}
        info = getattr(easy, "info", None)
        filename = os.path.splitext(os.path.basename(path))[0]

        title = _first(tags.get("title"), filename)
        track_artist_name = _first(tags.get("artist"), "Unknown Artist")
        album_artist_name = _first(tags.get("albumartist"), track_artist_name)
        album_title = _first(tags.get("album"), "Unknown Album")
        genres_raw = tags.get("genre")
        if isinstance(genres_raw, (list, tuple)):
            genres_list = []
            for g in genres_raw:
                genres_list.extend(_split_combined_genre(str(g)))
        else:
            single = _first(genres_raw, "")
            genres_list = _split_combined_genre(single) if single else []

        track_number = _parse_int(_first(tags.get("tracknumber")))
        disc_number = _parse_int(_first(tags.get("discnumber"))) or 1
        release_year = _parse_year(_first(tags.get("date")) or _first(tags.get("year")))
        duration_ms = int(getattr(info, "length", 0) * 1000) if info else 0

        track_id = _stable_hash(path)
        album_id = _stable_hash(album_artist_name.lower(), album_title.lower())
        album_artist_id = _stable_hash(album_artist_name.lower())
        track_artist_id = _stable_hash(track_artist_name.lower())

        return {
            "artists": [
                {"id": album_artist_id, "name": album_artist_name, "provider": "local", "mbid": None},
                {"id": track_artist_id, "name": track_artist_name, "provider": "local", "mbid": None},
            ],
            "album_data": {
                "id": album_id,
                "title": album_title,
                "artist": album_artist_id,
                "release_year": release_year,
                "provider": "local",
                "mbid": None,  # no fingerprinting yet - local files aren't MusicBrainz-matched
            },
            "track_data": {
                "id": track_id,
                "title": title,
                "artist": track_artist_id,
                "album": album_id,
                "track_number": track_number,
                "disc_number": disc_number,
                "duration_ms": duration_ms,
                "file_path": path,
                "provider": "local",
                "mbid": None,
                "library_id": self.music_path,
                # File mtime as a proxy "added to library" time - no better
                # signal exists for local files (no server to ask). Omitted
                # entirely rather than set to None on a failed stat, so the
                # DB layer's own "now" fallback applies instead.
                **({"added_at": added_at} if (added_at := _file_mtime(path)) else {}),
            },
            "genres": genres_list,
        }

    # -- sync ---------------------------------------------------------------
    def fetch_all_ids(self) -> Set[str]:
        """Full scan of the library folder."""
        self._scan_cache = {}

        for path in self._iter_audio_files():
            item = self._parse_file(path)
            if item:
                self._scan_cache[item["track_data"]["id"]] = item

        return set(self._scan_cache.keys())

    def fetch_items_by_ids(self, item_ids: List[str]) -> Iterator[dict]:
        try:
            for track_id in item_ids:
                # pop, not get: nothing re-reads an item, and holding a whole
                # library of parsed tags after sync is pure resident memory.
                item = self._scan_cache.pop(track_id, None)
                if item is None:
                    # Not in the scan cache; re-parse from the DB-stored path.
                    path = self._resolve_track_path(track_id)
                    if path and os.path.exists(path):
                        item = self._parse_file(path)
                if item:
                    yield item
        finally:
            self._scan_cache.clear()

    def fetch_changed_ids(self, since: str) -> Optional[Set[str]]:
        """No incremental change detection for local files yet."""
        return None

    # -- playback -----------------------------------------------------------
    def _resolve_track_path(self, track_id: str) -> Optional[str]:
        track = Track.get_or_none(Track.id == track_id)
        return track.file_path if track and track.file_path else None

    def get_stream_url(self, track_id: str) -> str:
        """Local files play straight off disk - mpv accepts the path as-is."""
        path = self._resolve_track_path(track_id)
        if not path:
            raise FileNotFoundError(f"No local file for track {track_id}")
        return path

    # -- artwork ------------------------------------------------------------
    def _find_cover_file(self, directory: str) -> Optional[str]:
        try:
            entries = os.listdir(directory)
        except OSError:
            return None
        lookup = {name.lower(): name for name in entries}
        for base in COVER_BASENAMES:
            for ext in COVER_EXTENSIONS:
                actual = lookup.get(base + ext)
                if actual:
                    return os.path.join(directory, actual)
        return None

    def _extract_embedded_art(self, audio) -> Optional[bytes]:
        """Pull embedded cover bytes from a parsed mutagen file, across formats."""
        if audio is None:
            return None
        # FLAC / WavPack expose .pictures directly.
        pictures = getattr(audio, "pictures", None)
        if pictures:
            return pictures[0].data

        tags = getattr(audio, "tags", None)
        if not tags:
            return None

        # ID3 (MP3): APIC frames.
        if hasattr(tags, "getall"):
            try:
                apics = tags.getall("APIC")
                if apics:
                    return apics[0].data
            except Exception:
                pass

        # MP4 / M4A: 'covr' atom.
        try:
            if "covr" in tags:
                covers = tags["covr"]
                if covers:
                    return bytes(covers[0])
        except Exception:
            pass

        # Vorbis comments (OGG/Opus): base64 FLAC Picture block.
        try:
            block = tags.get("metadata_block_picture")
            if block:
                raw = block[0] if isinstance(block, (list, tuple)) else block
                return Picture(base64.b64decode(raw)).data
        except Exception:
            pass

        return None

    def _load_art_bytes(self, item_id: str) -> Optional[bytes]:
        """Find cover-art bytes for a track or album ID."""
        track = Track.get_or_none(Track.id == item_id)
        if track and track.file_path:
            path = track.file_path
        else:
            # item_id is an album ID - pick any track from that album.
            track = Track.get_or_none(Track.album == item_id)
            path = track.file_path if track and track.file_path else None
        if not path or not os.path.exists(path):
            return None

        # Prefer an embedded picture; fall back to a cover file in the folder.
        try:
            audio = mutagen.File(path)
        except Exception:
            audio = None
        data = self._extract_embedded_art(audio)
        if data:
            return data

        cover = self._find_cover_file(os.path.dirname(path))
        if cover:
            try:
                with open(cover, "rb") as fh:
                    return fh.read()
            except OSError:
                return None
        return None

    def download_image_to_cache(self, item_id: str, size_px: int = 0) -> bool:
        data = self._load_art_bytes(item_id)
        if not data:
            return False
        try:
            resize_and_save_jpeg(data, self.get_cached_image_path(item_id, size_px), max_width=size_px)
            return True
        except Exception as exc:
            logger.warning("Failed to write local artwork for %s: %s", item_id, exc)
            return False

    # -- lyrics -------------------------------------------------------------
    def get_lyrics(self, track_id: str, lrclib_enabled: bool = True,
                   synced_enabled: bool = True) -> dict:
        path = self._resolve_track_path(track_id)

        # 1. Sidecar .lrc file next to the track (synced if it has timestamps).
        if path:
            lrc_path = os.path.splitext(path)[0] + ".lrc"
            if os.path.exists(lrc_path):
                try:
                    with open(lrc_path, "r", encoding="utf-8", errors="ignore") as fh:
                        result = parse_lrc(fh.read(), synced_enabled)
                    if result["type"] != "none":
                        return result
                except OSError:
                    pass

        # 2. Embedded lyrics tag (unsynced).
        embedded = self._embedded_lyrics(path) if path else None

        # 3. lrclib.net lookup.
        if lrclib_enabled:
            track = Track.get_or_none(Track.id == track_id)
            if track:
                result, _raw = fetch_lrclib(track, synced_enabled)
                if result["type"] != "none":
                    return result

        if embedded:
            return {"type": "unsynced", "text": embedded}
        return dict(NO_LYRICS)

    def _embedded_lyrics(self, path: str) -> Optional[str]:
        try:
            easy = mutagen.File(path, easy=True)
            if easy and easy.tags and "lyrics" in easy.tags:
                return _first(easy.tags.get("lyrics")) or None
        except Exception:
            pass
        try:
            audio = mutagen.File(path)
            tags = getattr(audio, "tags", None)
            if tags and hasattr(tags, "getall"):
                uslt = tags.getall("USLT")
                if uslt:
                    return str(uslt[0].text)
        except Exception:
            pass
        return None
