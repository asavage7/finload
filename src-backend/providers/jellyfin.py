"""Jellyfin media provider. Contains all Jellyfin-specific behavior."""
import datetime
import json
import logging
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, Iterator, List, Optional, Set

from core.config import APP_NAME, APP_VERSION, USER_AGENT, get_device_id
from core.database import Track
from core.http import fetch_bytes
from .base import MediaProvider, atomic_write_bytes
from .lyrics import NO_LYRICS, fetch_lrclib

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 120
AUTH_TIMEOUT = 15
SYNC_FETCH_WORKERS = 8 # Used for fetching ID chunks during sync.
_ITEMS_PAGE_SIZE = 2000 # Items per page when sweeping /Items for all/changed IDs.


def _env_or_setting(env_name: str, settings, settings_key: str) -> str:
    """Resolve a value: env var wins, else the saved setting."""
    value = os.getenv(env_name, "").strip()
    if value:
        return value
    return (settings.get(settings_key) or "").strip()

def _device_auth_header() -> str:
    """Creates a per-device auth header to use for all Jellyfin requests."""
    return (
        f'MediaBrowser Client="{APP_NAME}", Device="Desktop", '
        f'DeviceId="{get_device_id()}", Version="{APP_VERSION}"'
    )

def _authenticate_by_name(server_url: str, username: str, password: str) -> dict:
    """Exchanges a username/password for an access token + user id."""
    body = json.dumps({"Username": username, "Pw": password}).encode("utf-8")
    req = urllib.request.Request(
        f"{server_url}/Users/AuthenticateByName",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Emby-Authorization": _device_auth_header(),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=AUTH_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))

def test_connection(server_url: str, username: str, password: str) -> dict:
    """Lightweight reachability + auth check used by onboarding."""
    server_url = server_url.rstrip("/")
    try:
        req = urllib.request.Request(
            f"{server_url}/System/Info/Public",
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        )
        urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as e:
        return {"ok": False, "message": f"Server responded with an error (HTTP {e.code})"}
    except urllib.error.URLError:
        return {"ok": False, "message": "Could not reach server at this URL"}
    except Exception:
        return {"ok": False, "message": "Unexpected error contacting server"}

    try:
        data = _authenticate_by_name(server_url, username, password)
        if not data.get("AccessToken"):
            return {"ok": False, "message": "Invalid username or password"}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"ok": False, "message": "Invalid username or password"}
        return {"ok": False, "message": f"Server responded with an error (HTTP {e.code})"}
    except urllib.error.URLError:
        return {"ok": False, "message": "Connection timed out"}
    except Exception:
        return {"ok": False, "message": "Unexpected error contacting server"}

    display_name = data.get("User", {}).get("Name", username)
    return {"ok": True, "message": f"Connected as {display_name}"}

class JellyfinProvider(MediaProvider):
    SETTINGS_KEYS = ("jellyfin_url", "jellyfin_username", "jellyfin_password")

    def __init__(self, settings) -> None:
        super().__init__()
        self._id_to_library: Dict[str, str] = {}
        self._auth_lock = threading.Lock()
        self.configure(settings)

    def configure(self, settings) -> None:
        self._settings = settings
        self.server_url = _env_or_setting("JELLYFIN_URL", settings, "jellyfin_url").rstrip("/")
        self.username = _env_or_setting("JELLYFIN_USERNAME", settings, "jellyfin_username")
        self.password = _env_or_setting("JELLYFIN_PASSWORD", settings, "jellyfin_password")
        self.access_token = ""
        self.user_id = ""
        self._authenticate()

    def _authenticate(self, stale_token: str | None = None) -> bool:
        """Convert saved credentials into an access token."""
        if not (self.server_url and self.username and self.password):
            return False
        with self._auth_lock:
            if stale_token is not None and self.access_token != stale_token:
                return bool(self.access_token and self.user_id)
            try:
                data = _authenticate_by_name(self.server_url, self.username, self.password)
            except Exception as e:
                logger.error("Jellyfin authentication failed: %s", e)
                return False
            self.access_token = data.get("AccessToken", "")
            self.user_id = data.get("User", {}).get("Id", "")
            return bool(self.access_token and self.user_id)

    def reauthenticate(self, stale_token: str | None = None) -> bool:
        return self._authenticate(stale_token)

    def is_configured(self) -> bool:
        return bool(self.server_url and self.access_token and self.user_id)

    def _request(self, method: str, path: str, query: Optional[Dict[str, Any]] = None) -> Any:
        """GET/POST a JSON endpoint. A 401 means the token expired or was
        revoked server-side, so re-authenticate once and replay the request."""
        for attempt in (0, 1):
            url = f"{self.server_url}{path}"
            if query:
                url += "?" + urllib.parse.urlencode(query)
            req = urllib.request.Request(url, method=method, headers={
                "X-Emby-Token": self.access_token,
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            })
            try:
                with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code != 401 or attempt or not self._authenticate():
                    raise

    def _post_no_body(self, path: str, query: Optional[Dict[str, Any]] = None) -> bool:
        """POST with an empty body (Content-Length: 0) and ignore the response
        payload for fire-and-forget endpoints."""
        url = f"{self.server_url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {
            "X-Emby-Token": self.access_token,
            "User-Agent": USER_AGENT,
        }
        req = urllib.request.Request(url, data=b"", headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
            return response.status in (200, 204)

    def report_play(self, track_id: str) -> None:
        """Mark a track played on the Jellyfin server."""
        if not self.is_configured():
            return
        try:
            self._post_no_body(f"/Users/{self.user_id}/PlayedItems/{track_id}")
        except Exception as e:
            logger.warning("Failed to report play to Jellyfin: %s", str(e))

    def _parse_jellyfin_date(self, raw: Optional[str]) -> Optional[datetime.datetime]:
        """Poll Jellyfin's DateCreated to use for "Recently Added" section."""
        if not raw:
            return None
        try:
            return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    def _yield_items(self, items) -> Iterator[dict]:
        """Yield normalized track dictionaries from Jellyfin items."""
        for track in items:
            album_artists = track.get("AlbumArtists") or []
            if album_artists:
                album_artist_name = album_artists[0].get("Name", "Unknown Artist")
                jellyfin_album_artist_id = album_artists[0].get("Id")
            else:
                album_artist_name = track.get("AlbumArtist") or (
                    track.get("ArtistItems", [{}])[0].get("Name", "Unknown Artist")
                )
                jellyfin_album_artist_id = (
                    track.get("ArtistItems", [{}])[0].get("Id")
                    if track.get("ArtistItems") else None
                )
            # Fall back to a slug if Jellyfin didn't return a UUID (odd).
            album_artist_id = jellyfin_album_artist_id or album_artist_name.lower().replace(" ", "_")
            track_artist_name = album_artist_name
            track_artist_id = album_artist_id
            if track.get("ArtistItems"):
                item_name = track["ArtistItems"][0].get("Name", album_artist_name)
                if item_name.strip().lower() != album_artist_name.strip().lower():
                    track_artist_name = item_name
                    jellyfin_track_artist_id = track["ArtistItems"][0].get("Id")
                    track_artist_id = jellyfin_track_artist_id or track_artist_name.lower().replace(" ", "_")

            # Collect MusicBrainz IDs from Jellyfin to use for metadata enrichment.
            provider_ids = track.get("ProviderIds") or {}

            added_at = self._parse_jellyfin_date(track.get("DateCreated"))

            yield {
                "artists": [
                    {"id": album_artist_id, "name": album_artist_name, "provider": "jellyfin",
                     "mbid": provider_ids.get("MusicBrainzAlbumArtist")},
                    {"id": track_artist_id, "name": track_artist_name, "provider": "jellyfin",
                     "mbid": provider_ids.get("MusicBrainzArtist")},
                ],
                "album_data": {
                    "id": track.get("AlbumId") or "unknown_album",
                    "title": track.get("Album", "Unknown Album"),
                    "artist": album_artist_id,
                    "release_year": track.get("ProductionYear", 0),
                    "provider": "jellyfin",
                    "mbid": provider_ids.get("MusicBrainzReleaseGroup"),
                },
                "track_data": {
                    "id": track.get("Id"),
                    "title": track.get("Name", "Unknown Track"),
                    "artist": track_artist_id,
                    "album": track.get("AlbumId"),
                    "track_number": track.get("IndexNumber", 0),
                    "disc_number": track.get("ParentIndexNumber", 1),
                    "duration_ms": int(track.get("RunTimeTicks", 0) / 10000),
                    "provider": "jellyfin",
                    "mbid": provider_ids.get("MusicBrainzRecording"),
                    "library_id": self._id_to_library.get(track.get("Id")),
                    **({"added_at": added_at} if added_at else {}),
                },
                "genres": track.get("Genres", []),
            }

    def fetch_libraries(self) -> List[Dict[str, str]]:
        """Music libraries (Views) on the server, for the library-selection
        settings modal. Not called anywhere in the sync path itself."""
        data = self._request("GET", f"/Users/{self.user_id}/Views")
        return [
            {"id": item["Id"], "name": item.get("Name", "Library")}
            for item in data.get("Items", [])
            if item.get("CollectionType") == "music"
        ]

    def _selected_library_ids(self) -> List[str]:
        pending = self._settings.get("jellyfin_library_ids_pending")
        ids = pending if pending is not None else self._settings.get("jellyfin_library_ids")
        return [lib_id for lib_id in (ids or []) if lib_id]

    def _iter_items_pages(self, base_query: Dict[str, Any]) -> Iterator[dict]:
        """Yields item dictionaries matching base_query, paginating via StartIndex/Limit."""
        start = 0
        while True:
            query = {**base_query, "StartIndex": start, "Limit": _ITEMS_PAGE_SIZE}
            data = self._request("GET", f"/Users/{self.user_id}/Items", query=query)
            items = data.get("Items", [])
            yield from items
            if len(items) < _ITEMS_PAGE_SIZE:
                return
            start += _ITEMS_PAGE_SIZE

    def _fetch_ids_scoped(self, base_query: Dict[str, Any], progress_callback: Callable[[int], None] | None = None) -> Set[str]:
        """Returns a set of item IDs matching base_query, scoped to selected libraries."""
        selected = self._selected_library_ids()
        if not selected:
            return {item["Id"] for item in self._iter_items_pages(base_query)}

        ids: Set[str] = set()
        for library_id in selected:
            query = {**base_query, "ParentId": library_id}
            for item in self._iter_items_pages(query):
                item_id = item["Id"]
                ids.add(item_id)
                self._id_to_library[item_id] = library_id
        return ids

    # Shared by the all-ids and changed-ids sweeps
    _ID_SWEEP_QUERY = {
        "Recursive": "true",
        "IncludeItemTypes": "Audio",
        "Fields": "None",
        "EnableImages": "false",
        "EnableUserData": "false",
        "EnableTotalRecordCount": "false",
    }

    def fetch_all_ids(self) -> Set[str]:
        self._id_to_library = {}
        return self._fetch_ids_scoped(dict(self._ID_SWEEP_QUERY))

    def fetch_changed_ids(self, since: str) -> Set[str]:
        """IDs of tracks Jellyfin has saved (added or edited) since a given date."""
        # Reset here too, or the map grows across every incremental sync.
        self._id_to_library = {}
        return self._fetch_ids_scoped({**self._ID_SWEEP_QUERY, "MinDateLastSaved": since})

    def _fetch_chunk(self, chunk: List[str]) -> List[dict]:
        query = {
            "IncludeItemTypes": "Audio",
            "Recursive": "true",
            "Fields": "Genres,ProductionYear,ArtistItems,AlbumArtists,ProviderIds,DateCreated",
            "Ids": ",".join(chunk)
        }
        data = self._request("GET", f"/Users/{self.user_id}/Items", query=query)
        return list(self._yield_items(data.get("Items", [])))

    def fetch_items_by_ids(self, item_ids: List[str], chunk_size: int = 100) -> Iterator[dict]:
        if not item_ids:
            return

        chunks = [item_ids[i:i + chunk_size] for i in range(0, len(item_ids), chunk_size)]

        # Failures are deferred rather than raised inline
        with ThreadPoolExecutor(max_workers=SYNC_FETCH_WORKERS) as executor:
            failed = yield from self._drain(executor, chunks)
            if failed:
                logger.warning("Retrying %s failed chunk(s)", len(failed))
                failed = yield from self._drain(executor, failed)
        if failed:
            raise RuntimeError(f"{len(failed)} chunk(s) failed to fetch after a retry")

    def _drain(self, executor, chunks: List[List[str]]) -> Iterator[dict]:
        """Yield every item from chunks, returning the chunks that failed."""
        futures = {executor.submit(self._fetch_chunk, chunk): chunk for chunk in chunks}
        failed = []
        for future in as_completed(futures):
            try:
                yield from future.result()
            except Exception as e:
                logger.warning("Chunk fetch failed: %s", e)
                failed.append(futures[future])
        return failed

    def download_image_to_cache(self, item_id: str, size_px: int = 0) -> bool:
        """Downloads an image for the given item to the local cache."""
        url = f"{self.server_url}/Items/{item_id}/Images/Primary"
        if size_px > 0:
            url += f"?maxWidth={size_px}"

        data = fetch_bytes(url, headers={"X-Emby-Token": self.access_token})
        if data is None:
            return False
        try:
            atomic_write_bytes(data, self.get_cached_image_path(item_id, size_px))
            return True
        except OSError as e:
            logger.warning("Error caching image %s (size %spx): %s", item_id, size_px, e)
            return False

    def _transcode_preference(self) -> Optional[tuple[str, int]]:
        """Pulls transcode preferences from Settings."""
        if not self._settings.get("enable_transcoding"):
            return None
        raw_bitrate = (self._settings.get("transcode_bitrate") or "").strip()
        if not raw_bitrate.isdigit():
            return None
        codec = (self._settings.get("transcode_format") or "mp3").strip() or "mp3"
        return codec, int(raw_bitrate)

    _DIRECT_PLAY_CONTAINERS = "flac,mp3,ogg,opus,m4a,aac,wav,alac,wma,ape,wv"

    def _universal_url(self, track_id: str, *, audio_codec: Optional[str] = None,
                        audio_bitrate: Optional[int] = None, container: Optional[str] = None,
                        max_channels: Optional[int] = None, max_sample_rate: Optional[int] = None,
                        start_seconds: float = 0.0) -> str:
        """Returns the universal stream URL for the given track. """
        params = {
            "deviceId": get_device_id(),
            "userId": self.user_id,
            "api_key": self.access_token,
        }
        if audio_codec and audio_bitrate:
            params.update({
                "audioCodec": audio_codec,
                "audioBitRate": audio_bitrate,
                "container": container or audio_codec,
                "transcodingContainer": container or audio_codec,
            })
        else:
            params["container"] = self._DIRECT_PLAY_CONTAINERS
        if max_channels:
            params["maxAudioChannels"] = max_channels
        if max_sample_rate:
            params["maxAudioSampleRate"] = max_sample_rate
        if start_seconds > 0:
            params["startTimeTicks"] = int(start_seconds * 10_000_000)  # 100ns ticks
        return f"{self.server_url}/Audio/{track_id}/universal?" + urllib.parse.urlencode(params)

    def get_stream_url(self, track_id: str) -> str:
        """Returns the correct stream URL based on a user's settings."""
        preference = self._transcode_preference()
        if not preference:
            return self._universal_url(track_id)
        codec, bitrate = preference
        return self._universal_url(track_id, audio_codec=codec, audio_bitrate=bitrate)

    def get_seeked_stream(self, track_id: str, start_seconds: float) -> tuple[str, float]:
        """Returns an audio stream URL starting at start_seconds. """
        if start_seconds <= 0:
            return self.get_stream_url(track_id), 0.0
        codec, bitrate = self._transcode_preference() or ("mp3", 192000)
        return self._universal_url(
            track_id, audio_codec=codec, audio_bitrate=bitrate, start_seconds=start_seconds
        ), 0.0

    def get_analysis_stream_url(self, track_id: str) -> str:
        """Returns the stream URL for audio analysis.
        This seems redundant but it's because I used to support transcoding,
        but it seems like it's slower than direct even on lossless now, and
        really stresses the Jellyfin server."""
        return self._universal_url(track_id)

    def _server_lyrics(self, track_id: str, synced_enabled: bool) -> tuple[dict | None, str | None]:
        """Returns a tuple (synced_result_or_None, unsynced_text_or_None) for song lyrics."""
        try:
            res = self._request("GET", f"/Audio/{track_id}/Lyrics")
            lines = (res or {}).get("Lyrics")
            if not lines:
                return None, None
            has_timestamps = any("Start" in line for line in lines)
            if has_timestamps and synced_enabled:
                parsed = [
                    {"time_ms": line.get("Start", 0) / 10000.0, "text": line["Text"]}
                    for line in lines
                    if line.get("Text", "").strip()
                ]
                if parsed:
                    return {"type": "synced", "lines": parsed}, None
            text = "\n".join(l.get("Text", "") for l in lines if l.get("Text"))
            return None, (text or None)
        except Exception:
            return None, None

    def get_lyrics(self, track_id: str, lrclib_enabled: bool = True, synced_enabled: bool = True) -> dict:
        """Returns a dictionary containing song lyrics."""
        synced, jf_unsynced = self._server_lyrics(track_id, synced_enabled)
        if synced:
            return synced

        if lrclib_enabled:
            track = Track.get_or_none(Track.id == track_id)
            if track:
                result, raw_synced = fetch_lrclib(track, synced_enabled)
                if raw_synced:
                    # Store the found lyrics on the server so other clients get them too.
                    try:
                        self.post_lyrics(track_id, raw_synced)
                    except Exception:
                        pass
                if result["type"] != "none":
                    return result

        if jf_unsynced:
            return {"type": "unsynced", "text": jf_unsynced}
        return dict(NO_LYRICS)

    def post_lyrics(self, track_id: str, lyrics_text: str):
        """Uploads external synced lyrics back to the Jellyfin server."""
        # The fileName query param tells Jellyfin how to parse the file
        query = urllib.parse.urlencode({"fileName": "lyrics.lrc"})
        url = f"{self.server_url}/Audio/{track_id}/Lyrics?{query}"
        headers = {
            "X-Emby-Token": self.access_token,
            "Content-Type": "text/plain",
            "User-Agent": USER_AGENT,
        }
        req = urllib.request.Request(url, data=lyrics_text.encode('utf-8'), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status in (200, 204)
        except Exception as e:
            logger.warning("Failed to upload lyrics to Jellyfin: %s", e)
            return False
