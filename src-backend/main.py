# src-backend/main.py
from time import time

from fastapi import FastAPI, WebSocket, HTTPException, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from database import DatabaseManager, Artist, Album, Track, QueueItem
from jellyfin_bridge import JellyfinBridge
from playback_manager import PlaybackManager
from config import get_backend_host, get_backend_port, get_cors_origins, get_jellyfin_config
from collections import defaultdict
from colorthief import ColorThief
import uvicorn
import asyncio
import os
import threading

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

db = DatabaseManager()
server_url, api_key, user_id = get_jellyfin_config()
bridge = JellyfinBridge(
    server_url=server_url,
    api_key=api_key,
    user_id=user_id,
)
playback = PlaybackManager(bridge)


def _get_env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def sync_library(db_manager: DatabaseManager, bridge_client: JellyfinBridge):
    try:
        print("--- Starting library sync ---")
        local_ids = set(Track.select(Track.id).scalars())
        server_ids = bridge_client.fetch_all_ids()

        stale_ids = local_ids - server_ids
        new_ids = server_ids - local_ids

        print(f"Local tracks: {len(local_ids)}")
        print(f"Remote tracks: {len(server_ids)}")
        print(f"Sync: {len(stale_ids)} to remove, {len(new_ids)} to add")

        if stale_ids:
            Track.delete().where(Track.id << list(stale_ids)).execute()

        if new_ids:
            for item in bridge_client.fetch_audio_by_ids(list(new_ids)):
                for artist in item["artists"]:
                    db_manager.upsert_artist(**artist)

                db_manager.upsert_album(**item["album_data"])
                db_manager.upsert_track(**item["track_data"])

        print("--- Library sync complete ---")
    except Exception as exc:
        print(f"Library sync failed: {exc}")


@app.on_event("startup")
def startup_sync_library():
    if _get_env_flag("INITIAL_FULL_SYNC", False):
        threading.Thread(target=sync_library, args=(db, bridge), daemon=True).start()

# --- CORE DATA ROUTES ---
@app.get("/api/artists")
def get_artists():
    artists = Artist.select()
    return [{"id": a.secondary_id, "name": a.name} for a in artists]

@app.get("/api/albums")
def get_albums(sort_by: str = "title"):
    albums = Album.select(Album, Artist).join(Artist).order_by(getattr(Album, sort_by).asc())
    return [
        {
            "id": str(a.id), 
            "title": str(a.title), 
            "artist_name": str(a.artist.name),
            "artist_id": str(a.artist.secondary_id) if a.artist else None,
            "release_year": a.release_year,
            "duration_ms": sum(t.duration_ms for t in Track.select().where(Track.album == a.id))
        } 
        for a in albums
    ]
    
@app.get("/api/tracks")
def get_tracks(sort_by: str = "title"):
    start_time = time()
    tracks = Track.select(Track, Album, Artist).join(Album).join(Artist).order_by(getattr(Track, sort_by).asc())
    return [
        {
            "id": str(t.id), 
            "album_id": str(t.album.id),
            "title": str(t.title), 
            "artist_name": str(t.artist.name),
            "album_title": str(t.album.title),
            "duration_ms": t.duration_ms
        } 
        for t in tracks
    ]
    print(f"Fetched tracks in {time() - start_time:.2f} seconds")
    
def calculate_brightness(rgb) -> float:
    return (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255.0

def calculate_saturation(rgb) -> float:
    r, g, b = [c / 255.0 for c in rgb]
    mx = max(r, g, b)
    mn = min(r, g, b)
    if mx == mn:
        return 0
    return (mx - mn) / (mx) * max(1, calculate_brightness(rgb) * 1.25)

def rgb_to_hex(rgb):
    return '#{:02x}{:02x}{:02x}'.format(*rgb)

@app.get("/api/album/{album_id}")
def get_album_details(album_id: str):
    try:
        album = Album.get_or_none(Album.id == album_id)
        if not album:
            raise HTTPException(status_code=404, detail="Album not found")

        tracks_query = (Track.select(Track, Artist)
                        .join(Artist, on=(Track.artist == Artist.id))
                        .where(Track.album == album_id)
                        .order_by(Track.disc_number, Track.track_number))
        
        discs_map = defaultdict(list)
        for t in tracks_query:
            d_num = t.disc_number if (t.disc_number and t.disc_number > 0) else 1
            discs_map[d_num].append({
                "id": t.id,
                "title": t.title,
                "track_number": t.track_number,
                "duration_ms": t.duration_ms,
                "artist_name": t.artist.name if t.artist else "Unknown Artist"
            })
            
        discs_list = [{"disc_number": d_num, "tracks": discs_map[d_num]} for d_num in sorted(discs_map.keys())]
        
        return {
            "album": {
                "id": album.id,
                "title": album.title,
                "artist_name": album.artist.name if album.artist else "Unknown Artist",
                "artist_id": album.artist.secondary_id if album.artist else None,
                "release_year": album.release_year,
            },
            "discs": discs_list
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.get("/api/{item_id}/accent-colors")
def get_accent_colors(item_id: str, debug: bool = False):
    if debug:
        start_time = time()
    
    cache_path = os.path.join(bridge.cache_dir, f"{item_id}_400.jpg")
    to_hex = lambda rgb: "#{:02x}{:02x}{:02x}".format(*rgb)
    
    if not os.path.exists(cache_path):
        return {"error": "Image not found"}
        
    try:
        palette = ColorThief(cache_path).get_palette(color_count=15, quality=25)
        if debug:
            print(f"Extracted palette for album {item_id}: {palette}")
        
        def calculate_score(rgb, index):
            saturation = calculate_saturation(rgb)
            brightness_penalty = calculate_brightness(rgb) < 0.3
            score = (15 / ((index + 1))) + (60 * saturation) - (brightness_penalty * 20)
            if debug:
                print(f"Color: {str(rgb):>20}, Saturation: {saturation:.2f}, Brightness: {calculate_brightness(rgb):.2f},  Brightness Penalty: {brightness_penalty}, Score: {score:.2f}")
            return score
        
        accent = list(max(palette, key=lambda c: calculate_score(c, palette.index(c))))
        
        def get_contrast(rgb):
            lum = sum(w * (c/3294.6 if c <= 10 else ((c/255 + 0.055)/1.055)**2.4) 
                      for c, w in zip(rgb, [0.2126, 0.7152, 0.0722]))
            return 1.05 / (lum + 0.05)
        
        while get_contrast(accent) < 4.5:
            accent = [int(c * 0.9) for c in accent]
            
        # Find the most prominent light and dark colors for primary/background use, ensuring good contrast with the accent
        light_candidates = [c for c in sorted(palette, key=lambda c: calculate_score(c, palette.index(c)), reverse=True) if calculate_brightness(c) > 0.75]
        if light_candidates:
            light_primary = [int(c1) for c1 in light_candidates[0]]
        else:
            light_primary = [int(c1 * 1.5) for c1 in accent]
        
        dark_primary = [int(c1 * 0.2) for c1 in accent]
        if debug:
            print(f"Accent: {accent}, Light Primary: {light_primary}, Dark Primary: {dark_primary}")
            print(f"Color extraction for {item_id} took {time() - start_time:.2f} seconds")
        return [to_hex(accent), to_hex(light_primary), to_hex(dark_primary)]
        
    except Exception as e:
        print(f"Color extraction skipped: {e}")
        return {"error": str(e)}
    
@app.get("/api/album/{album_id}/accent-colors")
def get_album_accent_colors(album_id: str):
    return get_accent_colors(album_id)

@app.get("/api/track/{track_id}/accent-colors")
def get_track_accent_colors(track_id: str):
    return get_accent_colors(track_id)

@app.get("/api/artist/{artist_id}/accent-colors")
def get_artist_accent_colors(artist_id: str):
    return get_accent_colors(artist_id)

@app.get("/api/artist/{artist_id}")
def get_artist_details(artist_id: str):
    artist = Artist.get_or_none(Artist.secondary_id == artist_id)
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")
    return {
        "artist": {
            "id": artist.secondary_id,
            "name": artist.name,
            "albums_count": Album.select().where(Album.artist == artist.id).count(),
            "tracks_count": Track.select().where(Track.artist == artist.id).count(),
            "total_duration_ms": sum(t.duration_ms for t in Track.select().where(Track.artist == artist.id))
        },
        "albums": [
            {
                "id": str(a.id), 
                "title": str(a.title), 
                "duration_ms": sum(t.duration_ms for t in Track.select().where(Track.album == a.id)),
                "release_year": a.release_year
            } 
            for a in Album.select().where(Album.artist == artist.id).order_by(Album.release_year.desc())
        ]
    }
    
@app.get("/api/image/{item_id}")
def get_image(item_id: str, size: int = 0, type: str = "album"):
    """
    Returns an image. Accepts an optional ?size=400 query parameter for width in pixels.
    size=0 returns the original image.
    """
    if size > 2000:
        size = 2000

    suffix = str(size) if size > 0 else "original"
    cache_path = os.path.join(bridge.cache_dir, f"{item_id}_{suffix}.jpg")
    
    if os.path.exists(cache_path):
        return FileResponse(cache_path)
        
    success = bridge.download_image_to_cache(item_id, size)
    
    if success and os.path.exists(cache_path):
        return FileResponse(cache_path)
    if not success and type == "track":
        album_id = Track.get_or_none(Track.id == item_id).album.id
        cache_path = os.path.join(bridge.cache_dir, f"{album_id}_{suffix}.jpg")
        
        if os.path.exists(cache_path):
            return FileResponse(cache_path)
        
        success = bridge.download_image_to_cache(album_id, size)
        if success and os.path.exists(cache_path):
            return FileResponse(cache_path)
        
    raise HTTPException(status_code=404, detail="Image not found")

@app.post("/api/playback/play_track/{track_id}")
def play_track(track_id: str):
    playback.play_now(track_id, context_ids=[track_id])
    return {"status": "success"}

@app.get("/api/album/{track_id}/lyrics")
def get_album_lyrics(track_id: str):
    return bridge.get_lyrics(track_id)

@app.post("/api/playback/play_album/{album_id}")
def play_album(album_id: str, track_id: str | None = None):
    album = Album.get_or_none(Album.id == album_id)
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")
    album_tracks = Track.select().where(Track.album == album_id).order_by(Track.disc_number, Track.track_number)
    try:
        playback.play_now(track_id or album_tracks[0].id, [t.id for t in album_tracks])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "success"}

@app.post("/api/playback/toggle_pause")
def toggle_pause():
    playback.toggle_pause()
    return {"is_paused": playback.is_paused}

@app.post("/api/playback/next")
def skip_next():
    playback.skip_next()
    return {"status": "success"}

@app.post("/api/playback/prev")
def skip_prev():
    playback.skip_prev()
    return {"status": "success"}

@app.post("/api/playback/jump_to_queue_item/{queue_item_id}")
def jump_to_track(queue_item_id: str):
    playback.jump_to_queue_item(queue_item_id)
    return {"status": "success"}

@app.post("/api/playback/seek/{seconds}")
def seek_track(seconds: float):
    playback.seek(seconds)
    return {"status": "success"}

@app.get("/api/playback/get_queue")
def get_queue():
    queue = QueueItem.select(QueueItem, Track, Artist).join(Track).join(Artist).order_by(QueueItem.position)
    return [
        {
            "id": q.id,
            "track_id": q.track.id,
            "title": q.track.title,
            "artist_name": q.track.artist.name if q.track.artist else "Unknown Artist",
            "duration_ms": q.track.duration_ms
        }
        for q in queue
    ]

# Global set to keep track of active WebSocket connections
connected_websockets = set()

# Event notifier hook setup
update_event = asyncio.Event()

def on_playback_engine_state_change():
    """Called instantly from MPV properties / track mutations."""
    # Signal the async loop that a state event occurred immediately
    asyncio.run_coroutine_threadsafe(trigger_immediate_broadcast(), asyncio.get_event_loop())

async def trigger_immediate_broadcast():
    update_event.set() # Wakes up streaming loops early


@app.websocket("/ws/playback")
async def playback_ws(websocket: WebSocket):
    await websocket.accept()
    
    loop = asyncio.get_event_loop()
    
    def on_state_update(state):
        asyncio.run_coroutine_threadsafe(websocket.send_json(state), loop)

    playback.add_listener(on_state_update)
    
    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")
            
            if action == "toggle_pause":
                playback.toggle_pause()
            elif action == "skip_next":
                playback.skip_next()
            elif action == "skip_prev":
                playback.skip_prev()
            elif action == "seek":
                playback.seek(data.get("value"))
            elif action == "jump_to_queue_item":
                playback.jump_to_queue_item(data.get("value"))
            elif action == "remove_from_queue":
                playback.remove_from_queue(data.get("value"))
                
    except WebSocketDisconnect:
        pass
    finally:
        playback.remove_listener(on_state_update)
        
if __name__ == "__main__":
    uvicorn.run("main:app", host=get_backend_host(), port=get_backend_port(), reload=True)