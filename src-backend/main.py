# src-backend/main.py
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
def get_albums():
    albums = Album.select(Album, Artist).join(Artist)
    return [
        {
            "id": str(a.id), 
            "title": str(a.title), 
            "artist_name": str(a.artist.name)
        } 
        for a in albums
    ]
    
def calculate_brightness(rgb) -> float:
    return (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255.0

def calculate_saturation(rgb) -> float:
    r, g, b = [c / 255.0 for c in rgb]
    mx = max(r, g, b)
    mn = min(r, g, b)
    if mx == mn:
        return 0
    return (mx - mn) / (mx)

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
                "release_year": album.release_year,
            },
            "discs": discs_list
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.get("/api/album/{album_id}/accent-colors")
def get_album_accent_colors(album_id: str):
        cache_path = os.path.join(bridge.cache_dir, f"{album_id}_400.jpg")
        accent_hex, light_primary, dark_primary = None, None, None
        
        if os.path.exists(cache_path):
            try:
                color_thief = ColorThief(cache_path)
                pallete = color_thief.get_palette(color_count=15)
                for rgb in pallete:
                    brightness = calculate_brightness(rgb)
                    saturation = calculate_saturation(rgb)
                    if (brightness > 0.25 and brightness < 0.6 and saturation > 0.5) or (brightness > 0.3 and brightness < 0.5 and saturation > 0.4) and not accent_hex:
                        accent_hex = rgb_to_hex(rgb)
                    if brightness > 0.7 and brightness < 0.9 and not light_primary:
                        light_primary = rgb_to_hex(rgb)
                    elif brightness < 0.4 and brightness > 0.05 and not dark_primary:
                        dark_primary = rgb_to_hex(rgb)
                    if accent_hex and light_primary and dark_primary:
                        break
                    
                def adjust_color(hex_color, factor):
                    hex_color = hex_color.lstrip('#')
                    rgb = [int(hex_color[i:i+2], 16) for i in (0, 2, 4)]
                    new_rgb = [max(0, min(255, int(c * factor))) for c in rgb]
                    return rgb_to_hex(new_rgb)
                
                def flatten_color(rgb):
                    brightness = calculate_brightness(rgb)
                    if brightness > 0.6:
                        return adjust_color(rgb_to_hex(rgb), 0.7)
                    elif brightness < 0.2:
                        return adjust_color(rgb_to_hex(rgb), 1.2)
                    return rgb_to_hex(rgb)

                if not accent_hex:
                    for rgb in pallete:
                        if calculate_brightness(rgb) > 0.2 and calculate_brightness(rgb) < 0.6:
                            accent_hex = rgb_to_hex(rgb)
                            break
                    accent_hex = accent_hex or flatten_color(pallete[0]) # Fallback to the first color if no suitable accent found

                if not light_primary:
                    light_primary = adjust_color(accent_hex, 1.5)

                if not dark_primary:
                    dark_primary = adjust_color(accent_hex, 0.5)

                return [accent_hex, light_primary, dark_primary]
                
            except Exception as e:
                print(f"Color extraction skipped: {e}")
    
@app.get("/api/image/{item_id}")
async def get_image(item_id: str, size: int = 0):
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
        
    success = await bridge.download_image_to_cache(item_id, size)
    
    if success and os.path.exists(cache_path):
        return FileResponse(cache_path)
        
    raise HTTPException(status_code=404, detail="Image not found")


@app.post("/api/playback/play_track/{track_id}")
def play_track(track_id: str):
    playback.play_now(track_id)
    return {"status": "success"}

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