"""Local-files media provider.

Scans local files and exposes them to the app through the generic MediaProvider interface,
so the rest of the app doesn't need to know where the audio came from.

Track / album / artist IDs are derived deterministically from tags (and, for
tracks, the absolute file path) so they stay stable across rescans.
"""
import base64
import datetime
import hashlib
import logging
import os
import re
from typing import Callable, Iterator, List, Optional, Set

import mutagen
from mutagen.flac import Picture

from core.database import Track
from .base import MediaProvider, resize_and_save_jpeg
from .lyrics import NO_LYRICS, fetch_lrclib, parse_lrc

logger = logging.getLogger(__name__)

# Supported file extensions for local audio files. Case-insensitive.
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
        self._id_to_path: dict = {}
        self.configure(settings)

    def configure(self, settings) -> None:
        path = (settings.get("local_music_path") or "").strip()
        self.music_path = os.path.abspath(os.path.expanduser(path)) if path else ""

    def is_configured(self) -> bool:
        return bool(self.music_path and os.path.isdir(self.music_path))

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
        track_artist_mbid = _first(tags.get("musicbrainz_artistid")) or None
        album_artist_mbid = _first(tags.get("musicbrainz_albumartistid")) or None
        album_title = _first(tags.get("album"), "Unknown Album")
        genres_raw = tags.get("genre")
        mbid = _first(tags.get("musicbrainz_trackid")) or None
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
        album_mbid = _first(tags.get("musicbrainz_releasegroupid")) or None
        album_artist_id = _stable_hash(album_artist_name.lower())
        track_artist_id = _stable_hash(track_artist_name.lower())

        return {
            "artists": [
                {"id": album_artist_id, "name": album_artist_name, "provider": "local", "mbid": album_artist_mbid},
                {"id": track_artist_id, "name": track_artist_name, "provider": "local", "mbid": track_artist_mbid},
            ],
            "album_data": {
                "id": album_id,
                "title": album_title,
                "artist": album_artist_id,
                "release_year": release_year,
                "provider": "local",
                "mbid": album_mbid,
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
                "mbid": mbid,
                "library_id": self.music_path,
                # File mtime as a proxy "added to library" time.
                **({"added_at": added_at} if (added_at := _file_mtime(path)) else {}),
            },
            "genres": genres_list,
        }

    # Sync
    def _populate_paths(self, since_ts: Optional[float] = None) -> Set[str]:
        """Sweeps the local folder to detect changed files."""
        self._id_to_path.clear()
        changed = set()
        
        for path in self._iter_audio_files():
            try:
                stat_result = os.stat(path)
                latest_time = max(stat_result.st_mtime, stat_result.st_ctime)
                
                # Only include if no timestamp is provided, or if file is newer
                if since_ts is None or latest_time > since_ts:
                    track_id = _stable_hash(path)
                    self._id_to_path[track_id] = path
                    changed.add(track_id)
            except OSError:
                pass
            
        return changed
    
    def fetch_all_ids(self) -> Set[str]:
        """Full scan of the library folder."""
        self._id_to_path.clear()
        for path in self._iter_audio_files():
            track_id = _stable_hash(path)
            self._id_to_path[track_id] = path
        return set(self._id_to_path.keys())

    def fetch_changed_ids(self, since: str) -> Optional[Set[str]]:
        try:
            since_dt = datetime.datetime.fromisoformat(since.replace("Z", "+00:00"))
            return self._populate_paths(since_ts=since_dt.timestamp())
        except (ValueError, TypeError):
            return None

    def fetch_items_by_ids(self, item_ids: List[str]) -> Iterator[dict]:
        try:
            for track_id in item_ids:
                path = self._id_to_path.get(track_id)
                if not path:
                    path = self._resolve_track_path(track_id)
                    
                if path and os.path.exists(path):
                    item = self._parse_file(path)
                    if item:
                        yield item
        finally:
            self._id_to_path.clear()

    # Playback
    def _resolve_track_path(self, track_id: str) -> Optional[str]:
        track = Track.get_or_none(Track.id == track_id)
        return track.file_path if track and track.file_path else None

    def get_stream_url(self, track_id: str) -> str:
        """Local files play straight off disk - mpv accepts the path as-is."""
        path = self._resolve_track_path(track_id)
        if not path:
            raise FileNotFoundError(f"No local file for track {track_id}")
        return path

    # Artwork
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

    # Lyrics
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
