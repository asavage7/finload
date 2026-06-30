"""Jellyfin media provider.

All Jellyfin-specific behaviour (the Emby/Jellyfin REST API, its metadata
shapes, auth header, stream-URL scheme) is contained here.
"""
import json
import os
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterator, List, Optional, Set

from database import Track
from .base import MediaProvider

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


class JellyfinProvider(MediaProvider):
    SETTINGS_KEYS = ("jellyfin_url", "jellyfin_api_key", "jellyfin_user_id")

    def __init__(self, settings) -> None:
        super().__init__()
        self.configure(settings)

    def configure(self, settings) -> None:
        self.server_url = _env_or_setting("JELLYFIN_URL", settings, "jellyfin_url").rstrip("/")
        self.api_key = _env_or_setting("JELLYFIN_API_KEY", settings, "jellyfin_api_key")
        self.user_id = _env_or_setting("JELLYFIN_USER_ID", settings, "jellyfin_user_id")

    def is_configured(self) -> bool:
        return bool(self.server_url and self.api_key and self.user_id)

    def _request(self, method: str, path: str, query: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.server_url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)

        headers = {
            "X-Emby-Token": self.api_key,
            "Accept": "application/json",
            "User-Agent": "JellyfinPythonBridge/1.0"
        }

        req = urllib.request.Request(url, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))

    def _yield_items(self, items) -> Iterator[dict]:
        for track in items:
            # Extract artist names and Jellyfin UUIDs.
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
                track_artist_name = track["ArtistItems"][0].get("Name", album_artist_name)
                jellyfin_track_artist_id = track["ArtistItems"][0].get("Id")
                track_artist_id = jellyfin_track_artist_id or track_artist_name.lower().replace(" ", "_")

            genres_list = track.get("Genres", [])
            genre_string = ", ".join(genres_list) if genres_list else "Unknown"

            yield {
                "artists": [
                    {"id": album_artist_id, "name": album_artist_name, "provider": "jellyfin"},
                    {"id": track_artist_id, "name": track_artist_name, "provider": "jellyfin"},
                ],
                "album_data": {
                    "id": track.get("AlbumId") or "unknown_album",
                    "title": track.get("Album", "Unknown Album"),
                    "artist": album_artist_id,
                    "release_year": track.get("ProductionYear", 0),
                    "genre": genre_string,
                    "provider": "jellyfin",
                },
                "track_data": {
                    "id": track.get("Id"),
                    "title": track.get("Name", "Unknown Track"),
                    "artist": track_artist_id,
                    "album": track.get("AlbumId"),
                    "track_number": track.get("IndexNumber", 0),
                    "disc_number": track.get("ParentIndexNumber", 1),
                    "duration_ms": int(track.get("RunTimeTicks", 0) / 10000),
                    "has_artwork": track.get("HasPrimaryImage", False),
                    "provider": "jellyfin",
                },
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

    def _fetch_chunk(self, chunk: List[str]) -> List[dict]:
        query = {
            "IncludeItemTypes": "Audio",
            "Recursive": "true",
            "Fields": "Genres,ProductionYear,ArtistItems",  # CRITICAL: Genres needed here
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
            "X-Emby-Token": self.api_key,
            "User-Agent": "JellyfinPythonBridge/1.0",
        }

        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    with open(cache_path, 'wb') as f:
                        f.write(response.read())
                    return True
        except Exception as e:
            print(f"Error downloading image {item_id} (Size: {size_px}px): {e}")

        return False

    def get_stream_url(self, track_id: str) -> str:
        """Returns the direct stream URL for a track."""
        return f"{self.server_url}/Audio/{track_id}/stream?api_key={self.api_key}&static=true"

    def get_lyrics(self, track_id: str, lrclib_enabled: bool = True, synced_enabled: bool = True) -> dict:
        jf_unsynced = None
        try:
            res = self._request("GET", f"/Audio/{track_id}/Lyrics")
            if res and res.get("Lyrics"):
                has_timestamps = any("Start" in line for line in res["Lyrics"])
                if has_timestamps and synced_enabled:
                    parsed = []
                    for line in res["Lyrics"]:
                        if not line.get("Text", "").strip():
                            continue
                        start_ms = line.get("Start", 0) / 10000.0
                        parsed.append({"time_ms": start_ms, "text": line.get("Text", "")})
                    if parsed:
                        return {"type": "synced", "lines": parsed}
                else:
                    # Either no timestamps, or synced disabled — keep as plain fallback
                    jf_unsynced = "\n".join(l.get("Text", "") for l in res["Lyrics"] if l.get("Text"))
        except Exception:
            pass

        if lrclib_enabled:
            try:
                track = Track.get_by_id(track_id)
                query = urllib.parse.urlencode({
                    "track_name": track.title,
                    "artist_name": track.artist.name,
                    "album_name": track.album.title,
                    "duration": int(track.duration_ms) / 1000
                })
                req = urllib.request.Request(f"https://lrclib.net/api/get?{query}", headers={"User-Agent": "NiceGUI-MusicPlayer/1.0"})

                with urllib.request.urlopen(req, timeout=5) as response:
                    lrc_data = json.loads(response.read().decode())

                    if lrc_data.get("syncedLyrics") and synced_enabled:
                        try:
                            self.post_lyrics(track.id, lrc_data["syncedLyrics"])
                        except Exception:
                            pass

                        parsed = []
                        for line in lrc_data["syncedLyrics"].split('\n'):
                            match = re.match(r'\[(\d+):(\d+\.\d+)\](.*)', line.strip())
                            if match:
                                mins = int(match.group(1))
                                secs = float(match.group(2))
                                text = match.group(3).strip()
                                if text:
                                    parsed.append({"time_ms": mins * 60000 + secs * 1000, "text": text})
                        if parsed:
                            return {"type": "synced", "lines": parsed}

                    if lrc_data.get("plainLyrics"):
                        return {"type": "unsynced", "text": lrc_data["plainLyrics"]}
            except Exception:
                pass

        if jf_unsynced:
            return {"type": "unsynced", "text": jf_unsynced}

        # 4. Nothing found
        return {"type": "none"}

    def post_lyrics(self, track_id: str, lyrics_text: str):
        """Uploads external synced lyrics back to the Jellyfin server."""
        # The fileName query param tells Jellyfin how to parse the file
        query = urllib.parse.urlencode({"fileName": "lyrics.lrc"})
        url = f"{self.server_url}/Audio/{track_id}/Lyrics?{query}"

        headers = {
            "X-Emby-Token": self.api_key,
            "Content-Type": "text/plain",
            "User-Agent": "NiceGUI-MusicPlayer/1.0"
        }

        # Send the raw string encoded as UTF-8 in the body of a POST request
        req = urllib.request.Request(url, data=lyrics_text.encode('utf-8'), headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status in (200, 204)
        except Exception as e:
            print(f"Failed to upload lyrics to Jellyfin: {e}")
            return False
