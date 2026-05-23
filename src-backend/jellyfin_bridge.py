import urllib.parse
import urllib.request
import json
import time
import re
from typing import Dict, Any, Optional
from database import Artist, Track, Album
import os
import asyncio
import httpx
from platformdirs import user_cache_dir
from nicegui import app


class JellyfinBridge:
    def __init__(self, server_url: str, api_key: str, user_id: str):
        self.server_url = server_url.rstrip("/")
        self.api_key = api_key
        self.user_id = user_id
        self.cache_dir = user_cache_dir("finload")
        os.makedirs(self.cache_dir, exist_ok=True)
        app.add_static_files("/cache", self.cache_dir)
        self.client = httpx.AsyncClient(timeout=10)

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
        
        # --- Start Timing ---
        start_time = time.perf_counter()
        
        # Phase 1: Connection & Headers (TTFB)
        # This measures how long it takes to open the socket and get the first response back.
        with urllib.request.urlopen(req) as response:
            conn_done = time.perf_counter()
            
            # Phase 2: Data Transfer
            # This measures how long it takes to stream the actual bytes over the wire.
            raw_data = response.read()
            transfer_done = time.perf_counter()
            
            # Phase 3: JSON Parsing
            # This measures the local CPU time to turn text into a Python dictionary.
            json_data = json.loads(raw_data.decode("utf-8"))
            parse_done = time.perf_counter()

        # Log the breakdown
        ttfb = conn_done - start_time
        transfer = transfer_done - conn_done
        parsing = parse_done - transfer_done
        
        print(f"  [Request Log] {path}")
        print(f"    Connect/Wait: {ttfb:.4f}s")
        print(f"    Transfer:     {transfer:.4f}s")
        print(f"    JSON Parse:   {parsing:.4f}s")
        
        return json_data

    def yield_audio(self, items):
        for track in items:
            # 1. Extract Artists
            album_artist_name = track.get("AlbumArtist") or (track.get("ArtistItems", [{}])[0].get("Name", "Unknown Artist"))
            album_artist_id = album_artist_name.lower().replace(" ", "_")
            jellyfin_artist_id = track.get("ArtistItems", [{}])[0].get("Id") if track.get("ArtistItems") else None

            # Default track artist to album artist, then check for specific performers
            track_artist_name = album_artist_name
            jellyfin_track_artist_id = jellyfin_artist_id
            if track.get("ArtistItems"):
                track_artist_name = track.get("ArtistItems")[0].get("Name")
                jellyfin_track_artist_id = track.get("ArtistItems")[0].get("Id")
            track_artist_id = track_artist_name.lower().replace(" ", "_")

            # 2. Handle Multiple Genres
            # We join them into a single string for the Album.genre field
            genres_list = track.get("Genres", [])
            genre_string = ", ".join(genres_list) if genres_list else "Unknown"
            
            # 3. Structure for "Dictionary Pass-Through"
            # The keys here must exactly match your Peewee Model field names
            yield {
                "artists": [
                    {"id": album_artist_id, "name": album_artist_name, "secondary_id": jellyfin_artist_id},
                    {"id": track_artist_id, "name": track_artist_name, "secondary_id": jellyfin_track_artist_id}
                ],
                "album_data": {
                    "id": track.get("AlbumId") or "unknown_album",
                    "title": track.get("Album", "Unknown Album"),
                    "artist": album_artist_id, # Peewee handles ID mapping
                    "release_year": track.get("ProductionYear", 0),
                    "genre": genre_string
                },
                "track_data": {
                    "id": track.get("Id"),
                    "title": track.get("Name", "Unknown Track"),
                    "artist": track_artist_id,
                    "album": track.get("AlbumId"),
                    "track_number": track.get("IndexNumber", 0),
                    "disc_number": track.get("ParentIndexNumber", 1),
                    "duration_ms": int(track.get("RunTimeTicks", 0) / 10000),
                    "has_artwork": track.get("HasPrimaryImage", False)
                }
            }

    def fetch_all_ids(self) -> set:
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

    def fetch_audio_by_ids(self, item_ids: list, chunk_size: int = 100):
        if not item_ids:
            return
            
        for i in range(0, len(item_ids), chunk_size):
            chunk = item_ids[i:i + chunk_size]
            query = {
                "IncludeItemTypes": "Audio",
                "Recursive": "true",
                "Fields": "Genres,ProductionYear,ArtistItems", # CRITICAL: Added Genres here
                "Ids": ",".join(chunk) 
            }
            data = self._request("GET", f"/Users/{self.user_id}/Items", query=query)
            yield from self.yield_audio(data.get("Items", []))
            
    # Change size to size_px as an integer
    async def download_image_to_cache(self, item_id: str, size_px: int = 0) -> bool:
        """
        Downloads a specific pixel-width of an image to the local cache.
        If size_px is 0, downloads the original resolution.
        """
        suffix = str(size_px) if size_px > 0 else "original"
        cache_path = os.path.join(self.cache_dir, f"{item_id}_{suffix}.jpg")
        
        url = f"{self.server_url}/Items/{item_id}/Images/Primary"
        
        if size_px > 0:
            url += f"?maxWidth={size_px}"
            
        headers = {"X-Emby-Token": self.api_key}
        
        try:
            async with self.client.stream("GET", url, headers=headers) as response:
                if response.status_code == 200:
                    with open(cache_path, 'wb') as f:
                        async for chunk in response.aiter_bytes():
                            f.write(chunk)
                    return True
        except Exception as e:
            print(f"Error downloading image {item_id} (Size: {size_px}px): {e}")
            
        return False
    
    def get_stream_url(self, track_id: str) -> str:
        """Returns the direct stream URL for a track."""
        return f"{self.server_url}/Audio/{track_id}/stream?api_key={self.api_key}&static=true"
    
    def get_lyrics(self, track: Track) -> dict:
        jf_unsynced = None
        try:
            res = self._request("GET", f"/Audio/{track.id}/Lyrics")
            if res and res.get("Lyrics"):
                # Check if it has timestamps
                if any("Start" in line for line in res["Lyrics"]):
                    parsed = []
                    for line in res["Lyrics"]:
                        if not line.get("Text", "").strip():
                            continue
                        # Jellyfin Start is in ticks (10,000,000 ticks = 1 second)
                        start_ms = line.get("Start", 0) / 10000.0
                        parsed.append((start_ms, line.get("Text", "")))
                    if parsed:
                        return {"type": "synced", "lines": parsed}
                else:
                    jf_unsynced = "\n".join(l.get("Text", "") for l in res["Lyrics"] if l.get("Text"))
        except Exception:
            pass
        
        try:
            query = urllib.parse.urlencode({
                "track_name": track.title,
                "artist_name": track.artist.name,
                "album_name": track.album.title,
                "duration": int(track.duration_ms) / 1000
            })
            req = urllib.request.Request(f"https://lrclib.net/api/get?{query}", headers={"User-Agent": "NiceGUI-MusicPlayer/1.0"})
            
            with urllib.request.urlopen(req, timeout=5) as response:
                lrc_data = json.loads(response.read().decode())
                
                # Parse LRC timestamps if synced
                if lrc_data.get("syncedLyrics"):      
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
                                parsed.append((mins * 60000 + secs * 1000, text))
                    if parsed:
                        return {"type": "synced", "lines": parsed}
                        
                # Use plain text if unsynced
                if lrc_data.get("plainLyrics"):
                    return {"type": "unsynced", "text": lrc_data["plainLyrics"]}
        except Exception:
            pass

        # 3. Fallback to Jellyfin unsynced (if lrclib failed entirely)
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