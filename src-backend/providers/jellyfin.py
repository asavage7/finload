"""Jellyfin media provider.

All Jellyfin-specific behaviour (the Emby/Jellyfin REST API, its metadata
shapes, auth header, stream-URL scheme) is contained here.
"""
import datetime
import json
import logging
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterator, List, Optional, Set

from config import APP_NAME, APP_VERSION, USER_AGENT
from database import Track
from .base import MediaProvider
from .lyrics import NO_LYRICS, fetch_lrclib

logger = logging.getLogger(__name__)

# Default timeout (seconds)
REQUEST_TIMEOUT = 15

# How many ID chunks to request from Jellyfin in parallel during sync.
SYNC_FETCH_WORKERS = 8


def _env_or_setting(env_name: str, settings, settings_key: str) -> str:
    """Resolve a value: env var wins (handy for dev/.env), else the saved setting."""
    value = os.getenv(env_name, "").strip()
    if value:
        return value
    return (settings.get(settings_key) or "").strip()


def _device_auth_header() -> str:
    """The X-Emby-Authorization header Jellyfin requires on the (otherwise
    unauthenticated) AuthenticateByName call, identifying this client/device."""
    return (
        f'MediaBrowser Client="{APP_NAME}", Device="Desktop", '
        f'DeviceId="{APP_NAME.lower()}-desktop", Version="{APP_VERSION}"'
    )


def _authenticate_by_name(server_url: str, username: str, password: str) -> dict:
    """Exchanges a username/password for an access token + user id, the way
    Jellyfin's own apps do. Raises urllib.error.HTTPError/URLError on failure."""
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
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def test_connection(server_url: str, username: str, password: str) -> dict:
    """Lightweight reachability + auth check used by onboarding, for candidate
    values that haven't been saved to settings yet."""
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
        return {"ok": False, "message": "Could not reach server at this URL"}

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
        self.configure(settings)

    def configure(self, settings) -> None:
        self.server_url = _env_or_setting("JELLYFIN_URL", settings, "jellyfin_url").rstrip("/")
        self.username = _env_or_setting("JELLYFIN_USERNAME", settings, "jellyfin_username")
        self.password = _env_or_setting("JELLYFIN_PASSWORD", settings, "jellyfin_password")
        self.access_token = ""
        self.user_id = ""
        if self.server_url and self.username and self.password:
            try:
                data = _authenticate_by_name(self.server_url, self.username, self.password)
                self.access_token = data.get("AccessToken", "")
                self.user_id = data.get("User", {}).get("Id", "")
            except Exception as e:
                logger.error("Jellyfin authentication failed: %s", e)

    def is_configured(self) -> bool:
        return bool(self.server_url and self.access_token and self.user_id)

    def _request(self, method: str, path: str, query: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.server_url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)

        headers = {
            "X-Emby-Token": self.access_token,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }

        req = urllib.request.Request(url, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post_no_body(self, path: str, query: Optional[Dict[str, Any]] = None) -> bool:
        """POST with an empty body (Content-Length: 0) and ignore the response
        payload — for fire-and-forget endpoints like PlayedItems that may reply
        200 with a body or 204 with none."""
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
        """Mark a track played on the Jellyfin server: increments its play
        count and sets LastPlayedDate, which then syncs to other clients."""
        if not self.is_configured():
            return
        try:
            self._post_no_body(f"/Users/{self.user_id}/PlayedItems/{track_id}")
        except Exception as e:
            logger.warning("Failed to report play to Jellyfin: %s", e)

    def _parse_jellyfin_date(self, raw: Optional[str]) -> Optional[datetime.datetime]:
        """Jellyfin's DateCreated is the item's real "added to library" time
        — a genuine signal for the home page's Recently Added row, unlike a
        migration backfill. Defensive: a missing/malformed value just means
        the caller falls back to "now" at insert time, not a sync failure."""
        if not raw:
            return None
        try:
            return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _yield_items(self, items) -> Iterator[dict]:
        for track in items:
            # Extract artist names and Jellyfin UUIDs. AlbumArtists is the
            # album-level credit (e.g. "Various Artists" for a compilation) —
            # it must take priority over ArtistItems, which is this specific
            # track's own performer(s) and varies from song to song within the
            # same album.
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
            # Fall back to a slug if Jellyfin didn't return a UUID (unusual).
            album_artist_id = jellyfin_album_artist_id or album_artist_name.lower().replace(" ", "_")
            track_artist_name = album_artist_name
            track_artist_id = album_artist_id
            if track.get("ArtistItems"):
                item_name = track["ArtistItems"][0].get("Name", album_artist_name)
                if item_name.strip().lower() != album_artist_name.strip().lower():
                    track_artist_name = item_name
                    jellyfin_track_artist_id = track["ArtistItems"][0].get("Id")
                    track_artist_id = jellyfin_track_artist_id or track_artist_name.lower().replace(" ", "_")

            # MusicBrainz IDs, when Jellyfin's own metadata scraper resolved
            # them — lets genre enrichment skip audio fingerprinting entirely
            # for libraries that are already MusicBrainz-tagged.
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
                    **({"added_at": added_at} if added_at else {}),
                },
                "genres": track.get("Genres", []),
            }

    def fetch_all_ids(self) -> Set[str]:
        query = {
            "Recursive": "true",
            "IncludeItemTypes": "Audio",
            "Fields": "None",
            "EnableImages": "false",
            "EnableUserData": "false",
            "EnableTotalRecordCount": "false"
        }
        data = self._request("GET", f"/Users/{self.user_id}/Items", query=query)
        return {item["Id"] for item in data.get("Items", [])}

    def fetch_changed_ids(self, since: str) -> Set[str]:
        """IDs of tracks Jellyfin has saved (added or edited) since ``since``.

        Jellyfin bumps an item's DateLastSaved on any edit — a retagged genre,
        a metadata refresh — not just on creation, so this catches in-place
        changes to already-synced tracks that a plain ID diff would miss.
        """
        query = {
            "Recursive": "true",
            "IncludeItemTypes": "Audio",
            "Fields": "None",
            "EnableImages": "false",
            "EnableUserData": "false",
            "EnableTotalRecordCount": "false",
            "MinDateLastSaved": since,
        }
        data = self._request("GET", f"/Users/{self.user_id}/Items", query=query)
        return {item["Id"] for item in data.get("Items", [])}

    def _fetch_chunk(self, chunk: List[str]) -> List[dict]:
        query = {
            "IncludeItemTypes": "Audio",
            "Recursive": "true",
            # Genres must be requested explicitly; Jellyfin omits it from the
            # default field set, and genre enrichment has no other source for it.
            "Fields": "Genres,ProductionYear,ArtistItems,AlbumArtists,ProviderIds,DateCreated",
            "Ids": ",".join(chunk)
        }
        data = self._request("GET", f"/Users/{self.user_id}/Items", query=query)
        return list(self._yield_items(data.get("Items", [])))

    def fetch_items_by_ids(self, item_ids: List[str], chunk_size: int = 100) -> Iterator[dict]:
        if not item_ids:
            return

        chunks = [item_ids[i:i + chunk_size] for i in range(0, len(item_ids), chunk_size)]

        # Fetch chunks concurrently and yield items as each chunk completes.
        with ThreadPoolExecutor(max_workers=SYNC_FETCH_WORKERS) as executor:
            futures = [executor.submit(self._fetch_chunk, chunk) for chunk in chunks]
            for future in as_completed(futures):
                yield from future.result()

    def download_image_to_cache(self, item_id: str, size_px: int = 0) -> bool:
        """
        Downloads a specific pixel-width of an image to the local cache.
        If size_px is 0, downloads the original resolution.
        """
        cache_path = self.get_cached_image_path(item_id, size_px)

        url = f"{self.server_url}/Items/{item_id}/Images/Primary"

        if size_px > 0:
            url += f"?maxWidth={size_px}"

        headers = {
            "X-Emby-Token": self.access_token,
            "User-Agent": USER_AGENT,
        }

        # Write to a per-call temp file and rename into place atomically, so a
        # killed/restarted process (or any caller outside the images router's
        # per-path lock) can never leave a truncated file sitting in the cache
        # looking like a valid, complete image.
        tmp_path = f"{cache_path}.{os.getpid()}-{threading.get_ident()}.tmp"
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    with open(tmp_path, 'wb') as f:
                        f.write(response.read())
                    os.replace(tmp_path, cache_path)
                    return True
        except Exception as e:
            logger.warning("Error downloading image %s (size %spx): %s", item_id, size_px, e)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        return False

    def get_stream_url(self, track_id: str) -> str:
        """Returns the direct stream URL for a track."""
        return f"{self.server_url}/Audio/{track_id}/stream?api_key={self.access_token}&static=true"

    def get_seeked_stream(self, track_id: str, start_seconds: float) -> tuple[str, float]:
        """A stream that already begins ``start_seconds`` into the track.

        Jellyfin only honours ``startTimeTicks`` on the transcoding path, so
        this drops the ``static=true`` direct-play that ``get_stream_url`` uses
        and names a codec explicitly (same shape as the analysis stream, which
        is the proven transcode request here). In return the server delivers
        audio that starts at the offset, so the player has nothing to seek and
        no leading bytes to fetch and throw away. A zero offset needs none of
        this and takes the plain direct stream.
        """
        if start_seconds <= 0:
            return self.get_stream_url(track_id), 0.0
        ticks = int(start_seconds * 10_000_000)  # Jellyfin counts 100ns ticks
        return (f"{self.server_url}/Audio/{track_id}/stream.mp3"
                f"?audioCodec=mp3&audioBitRate=192000"
                f"&static=false&startTimeTicks={ticks}"
                f"&api_key={self.access_token}"), 0.0

    def get_analysis_stream_url(self, track_id: str) -> str:
        """A small transcoded mono stream for offline feature extraction — a
        few hundred KB instead of a full FLAC, since Essentia downmixes to
        mono and resamples to 44.1kHz on load anyway, so a lossless original
        buys nothing here but transfer time. ``static=false`` forces
        server-side transcoding instead of the static-if-compatible path
        ``get_stream_url`` uses for real playback.
        """
        return (f"{self.server_url}/Audio/{track_id}/stream.mp3"
                f"?audioCodec=mp3&audioBitRate=64000&maxAudioChannels=1"
                f"&static=false&api_key={self.access_token}")

    def _server_lyrics(self, track_id: str, synced_enabled: bool) -> tuple[dict | None, str | None]:
        """Lyrics stored on the Jellyfin server itself.

        Returns (synced_result_or_None, unsynced_text_or_None).
        """
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
