from time import time
from fastapi import FastAPI, WebSocket, HTTPException, WebSocketDisconnect, Body, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import uuid
from database import DatabaseManager, Artist, Album, Track, TrackLyrics, QueueItem, Playlist, PlaylistTrack, db as peewee_db, switch_database
from providers import create_provider
from settings_manager import SettingsManager
from peewee import fn, JOIN
from playback_manager import PlaybackManager
from sync_manager import SyncManager
from metadata_manager import MetadataManager
from config import get_backend_host, get_backend_port, get_cors_origins
from collections import defaultdict
import datetime
import json
import re
import random
import threading
import colorsys
from PIL import Image
import uvicorn
import asyncio
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)

db = DatabaseManager()
settings = SettingsManager()
provider = create_provider(settings)
# PlaybackManager creates the mpv core (and opens an audio output). Defer it to
# startup so it's built only in the worker that actually serves requests — not in
# uvicorn's --reload supervisor, which imports this module but never runs startup
# events. That avoids a second, idle mpv instance during development.
playback: PlaybackManager = None  # type: ignore[assignment]
sync = SyncManager(db)
metadata = MetadataManager(settings)
sync.metadata = metadata  # SyncManager triggers enrichment after successful sync

_accent_color_cache: dict[str, list] = {}


def _get_env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _exit_when_orphaned():
    """Exit the process if our parent (the Tauri app) dies.

    Tauri kills this sidecar on a normal quit, but if the app is force-killed the
    sidecar would be reparented and linger as an orphaned uvicorn. Detect that by
    watching for a change in our parent PID and hard-exit when it happens.
    """
    initial_ppid = os.getppid()
    stop = threading.Event()
    while not stop.wait(2.0):
        if os.getppid() != initial_ppid:
            os._exit(0)


@app.on_event("startup")
def startup_sync_library():
    global playback
    if playback is None:
        playback = PlaybackManager(provider, settings)
    threading.Thread(target=_exit_when_orphaned, daemon=True).start()
    if _get_env_flag("INITIAL_FULL_SYNC", False):
        sync.start(provider)


# --- LIBRARY ROUTES ---

_ARTIST_SORT_FIELDS = {"name", "album_count", "duration_ms"}

def _artist_order_expr(sort_by: str, sort_order: str):
    asc = sort_order == "asc"
    secondary = Artist.name.collate("NOCASE").asc()
    if sort_by == "album_count":
        primary = fn.COUNT(Album.id.distinct()).asc() if asc else fn.COUNT(Album.id.distinct()).desc()
    elif sort_by == "duration_ms":
        primary = fn.SUM(Track.duration_ms).asc() if asc else fn.SUM(Track.duration_ms).desc()
    else:
        primary = Artist.name.collate("NOCASE").asc() if asc else Artist.name.collate("NOCASE").desc()
    return primary, secondary

@app.get("/api/artists/count")
def get_artists_count():
    return {"count": Artist.select().count()}

@app.get("/api/artists")
def get_artists(sort_by: str = "name", sort_order: str = "asc",
                start_index: int | None = None, end_index: int | None = None):
    if sort_by not in _ARTIST_SORT_FIELDS:
        sort_by = "name"
    if sort_order not in ("asc", "desc"):
        sort_order = "asc"
    primary, secondary = _artist_order_expr(sort_by, sort_order)
    artists = (Artist.select(
                   Artist,
                   fn.COUNT(Album.id.distinct()).alias("album_count"),
                   fn.SUM(Track.duration_ms).alias("total_ms"),
               )
               .join(Album, JOIN.LEFT_OUTER, on=(Album.artist == Artist.id))
               .join(Track, JOIN.LEFT_OUTER, on=(Track.album == Album.id))
               .group_by(Artist.id)
               .order_by(primary, secondary))
    if start_index is not None and end_index is not None:
        artists = artists.offset(start_index).limit(end_index - start_index + 1)
    return [
        {
            "id": a.id,
            "name": a.name,
            "album_count": a.album_count or 0,
            "duration_ms": a.total_ms or 0,
        }
        for a in artists
    ]

_ALBUM_SORT_FIELDS = {"title", "artist", "release_year", "rating", "track_count", "duration_ms"}

def _album_order_expr(sort_by: str, sort_order: str):
    asc = sort_order == "asc"
    secondary = Album.title.collate("NOCASE").asc()
    if sort_by == "title":
        primary = Album.title.collate("NOCASE").asc() if asc else Album.title.collate("NOCASE").desc()
    elif sort_by == "artist":
        primary = Artist.name.collate("NOCASE").asc() if asc else Artist.name.collate("NOCASE").desc()
    elif sort_by == "release_year":
        primary = Album.release_year.asc() if asc else Album.release_year.desc()
    elif sort_by == "rating":
        primary = Album.rating.asc() if asc else Album.rating.desc()
    elif sort_by == "track_count":
        primary = fn.COUNT(Track.id).asc() if asc else fn.COUNT(Track.id).desc()
    else:  # duration_ms
        primary = fn.SUM(Track.duration_ms).asc() if asc else fn.SUM(Track.duration_ms).desc()
    return primary, secondary

@app.get("/api/albums/count")
def get_albums_count():
    return {"count": Album.select().count()}

@app.get("/api/albums")
def get_albums(sort_by: str = "title", sort_order: str = "asc",
               start_index: int | None = None, end_index: int | None = None):
    if sort_by not in _ALBUM_SORT_FIELDS:
        sort_by = "title"
    if sort_order not in ("asc", "desc"):
        sort_order = "asc"
    primary, secondary = _album_order_expr(sort_by, sort_order)
    albums = (Album.select(
                  Album, Artist,
                  fn.COUNT(Track.id).alias("track_count"),
                  fn.SUM(Track.duration_ms).alias("total_ms"),
              )
              .join(Artist)
              .switch(Album)
              .join(Track, JOIN.LEFT_OUTER, on=(Track.album == Album.id))
              .group_by(Album.id)
              .order_by(primary, secondary))
    if start_index is not None and end_index is not None:
        albums = albums.offset(start_index).limit(end_index - start_index + 1)
    return [
        {
            "id": str(a.id),
            "title": str(a.title),
            "artist_name": str(a.artist.name),
            "artist_id": str(a.artist.id) if a.artist else None,
            "release_year": a.release_year,
            "rating": a.rating,
            "track_count": a.track_count or 0,
            "duration_ms": a.total_ms or 0,
        }
        for a in albums
    ]

_TRACK_SORT_FIELDS = {"title", "artist", "duration_ms", "rating"}

def _track_order_expr(sort_by: str, sort_order: str):
    asc = sort_order == "asc"
    secondary = Track.title.collate("NOCASE").asc()
    if sort_by == "title":
        primary = Track.title.collate("NOCASE").asc() if asc else Track.title.collate("NOCASE").desc()
    elif sort_by == "artist":
        primary = Artist.name.collate("NOCASE").asc() if asc else Artist.name.collate("NOCASE").desc()
    elif sort_by == "duration_ms":
        primary = Track.duration_ms.asc() if asc else Track.duration_ms.desc()
    else:  # rating
        primary = Track.rating.asc() if asc else Track.rating.desc()
    return primary, secondary

@app.get("/api/tracks/count")
def get_tracks_count():
    return {"count": Track.select().count()}

@app.get("/api/tracks")
def get_tracks(sort_by: str = "title", sort_order: str = "asc",
               start_index: int | None = None, end_index: int | None = None):
    if sort_by not in _TRACK_SORT_FIELDS:
        sort_by = "title"
    if sort_order not in ("asc", "desc"):
        sort_order = "asc"
    primary, secondary = _track_order_expr(sort_by, sort_order)
    tracks = (Track.select(Track, Album, Artist)
              .join(Album)
              .join(Artist)
              .order_by(primary, secondary))
    if start_index is not None and end_index is not None:
        tracks = tracks.offset(start_index).limit(end_index - start_index + 1)
    return [
        {
            "id": str(t.id),
            "album_id": str(t.album.id),
            "title": str(t.title),
            "artist_name": str(t.artist.name),
            "album_title": str(t.album.title),
            "rating": t.rating,
            "duration_ms": t.duration_ms,
        }
        for t in tracks
    ]

# Normalised search text: lowercase, keep only word characters and whitespace
# (an allowlist — so any punctuation, even exotic Unicode like the hyphen in
# "blink‐182", is dropped), then collapse whitespace. Applied to both the query
# and the indexed text so neither side's punctuation can disqualify a match.
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)

def _normalize(text: str) -> str:
    return " ".join(_NON_WORD.sub("", (text or "").lower()).split())

# Expose the exact same normalisation to SQL, so candidate filtering and Python
# scoring agree byte-for-byte. (Applies to the live connection and is re-applied
# to any future ones.)
peewee_db.register_function(_normalize, "finload_normalize", 1)

def _normalized_field(field):
    """SQL counterpart of _normalize, via the registered SQLite function."""
    return fn.finload_normalize(field)

def _search_score(text: str, q: str, tokens: list[str]) -> int:
    """Relevance of a single field value against the query.

    Tiered so better matches always outrank weaker ones regardless of length:
    exact > prefix > word-start > substring > all-tokens-present. A small penalty
    for how deep into the string the match starts breaks ties toward the front.
    `q` and `tokens` are expected pre-normalized; the field text is normalized
    here so punctuation/case never affects the score.
    """
    if not text:
        return 0
    t = _normalize(text)
    if t == q:
        score = 1000
    elif t.startswith(q):
        score = 700
    elif any(word.startswith(q) for word in t.split()):
        score = 500
    elif q in t:
        score = 300
    elif len(tokens) > 1 and all(tok in t for tok in tokens):
        score = 200
    else:
        return 0
    pos = t.find(q)
    if pos > 0:
        score -= min(pos, 50)
    return score

def _item_score(title: str, artist: str, q: str, tokens: list[str]) -> int:
    """Score a title/artist pair. A title hit dominates, an artist-only hit is
    weaker, and a multi-token query that's fully covered across the combined
    text gets a baseline (so cross-field matches like "flyleaf sick" rank)."""
    s = max(_search_score(title, q, tokens),
            _search_score(artist, q, tokens) - 150)
    if len(tokens) > 1:
        combined = _normalize(f"{title} {artist}")
        if all(tok in combined for tok in tokens):
            s = max(s, 150)
    return s

def _match_all_tokens(fields, tokens):
    """Candidate filter: every token must appear in at least one of `fields`.

    AND across tokens (so unrelated rows that merely share one common word are
    excluded), OR across fields per token (so a query can span title + artist,
    e.g. "flyleaf sick"). Without the AND, common tokens flood the candidate
    limit with junk and bury the real match before it can be scored.
    """
    norm = [_normalized_field(f) for f in fields]
    clause = None
    for tok in tokens:
        per_token = None
        for nf in norm:
            c = nf.contains(tok)
            per_token = c if per_token is None else (per_token | c)
        clause = per_token if clause is None else (clause & per_token)
    return clause

# How many DB candidates to score per entity type. Bounds work while leaving
# plenty of headroom above the handful of results actually returned.
_SEARCH_CANDIDATES = 40

@app.get("/api/search")
def search(q: str = "", limit: int = 5):
    q = _normalize(q)
    if not q:
        return {"results": []}
    limit = max(1, min(limit, 20))
    tokens = q.split()
    scored: list[tuple[int, dict]] = []

    artists = (Artist.select()
               .where(_match_all_tokens([Artist.name], tokens))
               .limit(_SEARCH_CANDIDATES))
    for a in artists:
        s = _search_score(a.name, q, tokens)
        if s > 0:
            scored.append((s, {
                "type": "artist",
                "id": str(a.id),
                "title": str(a.name),
                "subtitle": "Artist",
                "image_id": str(a.id),
                "album_id": None,
            }))

    albums = (Album.select(Album, Artist).join(Artist)
              .where(_match_all_tokens([Album.title, Artist.name], tokens))
              .limit(_SEARCH_CANDIDATES))
    for al in albums:
        s = _item_score(al.title, al.artist.name, q, tokens)
        if s > 0:
            scored.append((s, {
                "type": "album",
                "id": str(al.id),
                "title": str(al.title),
                "subtitle": f"Album ∙ {al.artist.name}",
                "image_id": str(al.id),
                "album_id": str(al.id),
            }))

    tracks = (Track.select(Track, Album, Artist).join(Album).join(Artist)
              .where(_match_all_tokens([Track.title, Artist.name], tokens))
              .limit(_SEARCH_CANDIDATES))
    for t in tracks:
        s = _item_score(t.title, t.artist.name, q, tokens)
        if s > 0:
            scored.append((s, {
                "type": "track",
                "id": str(t.id),
                "title": str(t.title),
                "subtitle": f"Track ∙ {t.artist.name}",
                "image_id": str(t.album.id),
                "album_id": str(t.album.id),
            }))

    # Tiebreak equal scores by type (artist > album > track), then shorter title.
    type_rank = {"artist": 2, "album": 1, "track": 0}
    scored.sort(key=lambda r: (r[0], type_rank[r[1]["type"]], -len(r[1]["title"])),
                reverse=True)
    return {"results": [item for _, item in scored[:limit]]}

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
                "artist_name": t.artist.name if t.artist else "Unknown Artist",
                "rating": t.rating,
            })

        discs_list = [{"disc_number": d_num, "tracks": discs_map[d_num]} for d_num in sorted(discs_map.keys())]

        return {
            "album": {
                "id": album.id,
                "title": album.title,
                "artist_name": album.artist.name if album.artist else "Unknown Artist",
                "artist_id": album.artist.id if album.artist else None,
                "release_year": album.release_year,
                "rating": album.rating,
                "description": album.description,
            },
            "discs": discs_list
        }
    except HTTPException:
        raise  # Don't let the 404 below get re-wrapped as a 500.
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/album/{album_id}/tracks")
def get_album_tracks(album_id: str):
    tracks = (Track.select(Track, Artist)
              .join(Artist, on=(Track.artist == Artist.id))
              .where(Track.album == album_id)
              .order_by(Track.disc_number, Track.track_number))
    return [
        {
            "id": t.id,
            "title": t.title,
            "track_number": t.track_number,
            "disc_number": t.disc_number,
            "duration_ms": t.duration_ms,
            "rating": t.rating,
            "artist_name": t.artist.name if t.artist else "Unknown Artist",
        }
        for t in tracks
    ]

@app.get("/api/artist/{artist_id}")
def get_artist_details(artist_id: str):
    artist = Artist.get_or_none(Artist.id == artist_id)
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")

    artist_albums = list(Album.select().where(Album.artist == artist.id).order_by(Album.release_year.desc()))
    album_ids = [a.id for a in artist_albums]

    duration_map = {
        row.album: row.total
        for row in Track.select(Track.album, fn.SUM(Track.duration_ms).alias("total"))
                        .where(Track.album << album_ids)
                        .group_by(Track.album)
                        .namedtuples()
    } if album_ids else {}

    tracks_count = Track.select(fn.COUNT(Track.id)).where(Track.artist == artist.id).scalar() or 0

    return {
        "artist": {
            "id": artist.id,
            "name": artist.name,
            "bio": artist.bio,
            "albums_count": len(artist_albums),
            "tracks_count": tracks_count,
            "total_duration_ms": sum(duration_map.values())
        },
        "albums": [
            {
                "id": str(a.id),
                "title": str(a.title),
                "duration_ms": duration_map.get(a.id, 0),
                "release_year": a.release_year,
                "rating": a.rating,
            }
            for a in artist_albums
        ]
    }

@app.get("/api/artist/{artist_id}/tracks")
def get_artist_tracks(artist_id: str):
    artist = Artist.get_or_none(Artist.id == artist_id)
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")
    tracks = (Track.select(Track, Album, Artist)
              .join(Album).switch(Track).join(Artist)
              .where(Track.artist == artist.id)
              .order_by(Album.release_year, Track.disc_number, Track.track_number))
    return [
        {
            "id": t.id,
            "title": t.title,
            "album_title": t.album.title if t.album else "Unknown Album",
            "duration_ms": t.duration_ms,
            "rating": t.rating,
            "artist_name": t.artist.name if t.artist else "Unknown Artist",
        }
        for t in tracks
    ]

@app.get("/api/album/{album_id}/rating")
def get_album_rating(album_id: str):
    album = Album.get_or_none(Album.id == album_id)
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")
    return {"rating": album.rating}

@app.patch("/api/album/{album_id}/rating")
def update_album_rating(album_id: str, rating: int = Body(..., embed=True)):
    album = Album.get_or_none(Album.id == album_id)
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")
    if not (0 <= rating <= 5):
        raise HTTPException(status_code=422, detail="Rating must be between 0 and 5")
    album.rating = rating
    album.save()
    return {"rating": album.rating}

@app.get("/api/track/{track_id}/rating")
def get_track_rating(track_id: str):
    track = Track.get_or_none(Track.id == track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    return {"rating": track.rating}

@app.patch("/api/track/{track_id}/rating")
def update_track_rating(track_id: str, rating: int = Body(..., embed=True)):
    track = Track.get_or_none(Track.id == track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    if not (0 <= rating <= 5):
        raise HTTPException(status_code=422, detail="Rating must be between 0 and 5")
    track.rating = rating
    track.save()
    return {"rating": track.rating}

@app.get("/api/track/{track_id}/lyrics")
def get_track_lyrics(track_id: str, force: bool = False):
    if not force:
        cached = TrackLyrics.get_or_none(TrackLyrics.track == track_id)
        if cached:
            if cached.lyrics_type == "synced":
                return {"type": "synced", "lines": json.loads(cached.content)}
            if cached.lyrics_type == "unsynced":
                return {"type": "unsynced", "text": cached.content}
            return {"type": "none"}

    result = provider.get_lyrics(
        track_id,
        lrclib_enabled=settings.get("enable_lrclib_lyrics"),
        synced_enabled=settings.get("enable_synced_lyrics"),
    )

    content = None
    if result["type"] == "synced":
        content = json.dumps(result["lines"])
    elif result["type"] == "unsynced":
        content = result.get("text")

    (TrackLyrics.insert(
        track=track_id,
        lyrics_type=result["type"],
        content=content,
        fetched_at=datetime.datetime.now(),
    ).on_conflict(
        conflict_target=[TrackLyrics.track],
        update={
            TrackLyrics.lyrics_type: result["type"],
            TrackLyrics.content: content,
            TrackLyrics.fetched_at: datetime.datetime.now(),
        },
    ).execute())

    return result


# --- IMAGE ROUTES ---

def _get_playlist_image_dir() -> str:
    image_dir = os.path.join(os.path.dirname(peewee_db.database), "playlist_images")
    os.makedirs(image_dir, exist_ok=True)
    return image_dir

def _get_playlist_image_path(playlist_id: str) -> str:
    return os.path.join(_get_playlist_image_dir(), f"{playlist_id}.jpg")

# Mutable images (user-uploaded playlist covers, enrichment fanart) must not be
# cached by the browser — they're re-fetched with a cache-busting param when they
# change. The frontend bumps that param via playlistCoverTimestamps.
_IMAGE_CACHE_HEADERS = {"Cache-Control": "no-store"}
# Album/track/artist art is content-addressed by {item_id}_{size}, so it's
# effectively immutable — let the browser cache it hard to avoid re-fetching
# every cover on each virtualized scroll.
_IMMUTABLE_IMAGE_HEADERS = {"Cache-Control": "public, max-age=604800, immutable"}

@app.get("/api/image/{item_id}")
def get_image(item_id: str, size: int = 0, type: str = "album", variant: str = ""):
    if variant == "fanart":
        from platformdirs import user_cache_dir
        fanart_path = os.path.join(user_cache_dir("finload"), f"{item_id}_fanart.jpg")
        if os.path.exists(fanart_path):
            return FileResponse(fanart_path, headers=_IMAGE_CACHE_HEADERS)
        raise HTTPException(status_code=404, detail="Fanart not found")

    if type == "playlist":
        image_path = _get_playlist_image_path(item_id)
        if os.path.exists(image_path):
            return FileResponse(image_path, headers=_IMAGE_CACHE_HEADERS)
        raise HTTPException(status_code=404, detail="Playlist image not found")
    
    if type == "track":
        item_id = Track.get_or_none(Track.id == item_id).album_id

    if size > 2000:
        size = 2000

    cache_path = provider.get_cached_image_path(item_id, size)

    if os.path.exists(cache_path):
        return FileResponse(cache_path, headers=_IMMUTABLE_IMAGE_HEADERS)

    success = provider.download_image_to_cache(item_id, size)
    if success:
        _accent_color_cache.pop(item_id, None)

    if success and os.path.exists(cache_path):
        return FileResponse(cache_path, headers=_IMMUTABLE_IMAGE_HEADERS)
    if not success and type == "track":
        track = Track.get_or_none(Track.id == item_id)
        if not track:
            raise HTTPException(status_code=404, detail="Image not found")
        album_id = track.album_id
        cache_path = provider.get_cached_image_path(album_id, size)

        if os.path.exists(cache_path):
            return FileResponse(cache_path, headers=_IMMUTABLE_IMAGE_HEADERS)

        success = provider.download_image_to_cache(album_id, size)
        if success:
            _accent_color_cache.pop(str(album_id), None)
        if success and os.path.exists(cache_path):
            return FileResponse(cache_path, headers=_IMMUTABLE_IMAGE_HEADERS)

    raise HTTPException(status_code=404, detail="Image not found")


# --- ACCENT COLOR ROUTES ---

_ACCENT_CHROMA_EXP = 1.5
_ACCENT_CONTRAST_EXP = 2.0
_ACCENT_MIN_STANDOUT = 3.0
_ACCENT_DARK_FLOOR_L = 0.15
_SECONDARY_MIN_HUE_DIST = 10  # degrees from the accent hue to count as a distinct color
_SECONDARY_MIN_CHROMA = 0.12  # must be a real color, not a near-grey
_SECONDARY_MIN_SHARE = 0.02   # min fraction of pixels to be "prevalent"
_LIGHT_TARGET_L = 0.80        # lightness the secondary color is raised to for text use
_TEXT_CONTRAST = 4.5          # WCAG AA: white-on-accent and light-on-dark

def _to_hls(rgb):
    """RGB 0-255 -> (hue, lightness, saturation), each 0..1."""
    return colorsys.rgb_to_hls(*[c / 255 for c in rgb])

def _to_rgb(h, l, s):
    """(hue, lightness, saturation) 0..1 -> clamped RGB ints 0-255."""
    return [max(0, min(255, round(c * 255))) for c in colorsys.hls_to_rgb(h, l, s)]

def rgb_to_hex(rgb):
    return '#{:02x}{:02x}{:02x}'.format(*rgb)

def _luminance(rgb):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b

def _contrast(a, b):
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)

def _chroma(l, s):
    """Colorfulness: 0 at pure black/white, peaks at mid lightness."""
    return s * (1 - abs(2 * l - 1))

def _hue_dist(h1, h2):
    """Shortest distance between two hues, in degrees (0..180)."""
    d = abs(h1 - h2) * 360
    return min(d, 360 - d)

def _extract_palette(path, size=220, colors=16):
    """Return [(rgb, share)] ordered by pixel prevalence, where share is the
    fraction of pixels that color represents."""
    im = Image.open(path)
    im.draft("RGB", (size, size))   # fast JPEG downscale during decode
    im = im.convert("RGB")
    im.thumbnail((size, size))
    quant = im.quantize(colors=colors, method=Image.Quantize.FASTOCTREE)
    pal = quant.getpalette()
    total = im.width * im.height
    counts = quant.getcolors(maxcolors=colors) or []
    return [((pal[idx * 3], pal[idx * 3 + 1], pal[idx * 3 + 2]), n / total)
            for n, idx in sorted(counts, reverse=True)]

def _get_accent_colors(item_id: str, debug: bool = False, image_path: str = None):
    if item_id in _accent_color_cache:
        return _accent_color_cache[item_id]

    cache_path = image_path or provider.get_cached_image_path(item_id, 220)
    if not os.path.exists(cache_path):
        return {"error": "Image not found"}

    start_time = time() if debug else 0.0

    try:
        palette = _extract_palette(cache_path)

        def accent_fitness(rgb, share):
            _, l, s = _to_hls(rgb)
            white_fit = min(_contrast((255, 255, 255), rgb) / _TEXT_CONTRAST, 1.0)
            dark_fade = min(1.0, l / _ACCENT_DARK_FLOOR_L)   # near-black reads as black, not hue
            return (_chroma(l, s) ** _ACCENT_CHROMA_EXP) * share * (white_fit ** _ACCENT_CONTRAST_EXP) * dark_fade

        best_rgb = max(palette, key=lambda entry: accent_fitness(*entry))[0]
        ah, al, as_ = _to_hls(best_rgb)

        dark_primary = _to_rgb(ah, 0.12, min(as_, 0.5))

        while _contrast((255, 255, 255), _to_rgb(ah, al, as_)) < _TEXT_CONTRAST and al > 0.05:
            al -= 0.02
        while (_contrast(_to_rgb(ah, al, as_), dark_primary) < _ACCENT_MIN_STANDOUT
               and _contrast((255, 255, 255), _to_rgb(ah, al + 0.02, as_)) >= _TEXT_CONTRAST
               and al < 0.95):
            al += 0.02
        accent = _to_rgb(ah, al, as_)
        
        secondary, best_secondary = None, 0.0
        for rgb, share in palette:
            h, l, s = _to_hls(rgb)
            if share < _SECONDARY_MIN_SHARE or _chroma(l, s) < _SECONDARY_MIN_CHROMA:
                continue
            if _hue_dist(h, ah) < _SECONDARY_MIN_HUE_DIST:
                continue
            score = share * (0.2 + _chroma(l, s))       # prevalence weighted by vividness
            if score > best_secondary:
                best_secondary, secondary = score, rgb

        if secondary is not None:
            sh, sl, ss = _to_hls(secondary)
            light_primary = _to_rgb(sh, max(sl, _LIGHT_TARGET_L), ss)
        else:
            light_primary = _to_rgb(ah, 0.85, min(as_, 0.45))

        lh, ll, ls = _to_hls(light_primary)
        while _contrast(light_primary, dark_primary) < _TEXT_CONTRAST and ll < 0.96:
            ll += 0.02
            light_primary = _to_rgb(lh, ll, ls)

        result = [rgb_to_hex(accent), rgb_to_hex(light_primary), rgb_to_hex(dark_primary)]
        _accent_color_cache[item_id] = result
        if debug:
            print(f"Accent colors for {item_id}: {result} "
                  f"in {(time() - start_time) * 1000:.1f}ms")
        return result

    except Exception as e:
        print(f"Color extraction skipped: {e}")
        return {"error": str(e)}

@app.get("/api/album/{album_id}/accent-colors")
def get_album_accent_colors(album_id: str):
    return _get_accent_colors(album_id)

@app.get("/api/track/{track_id}/accent-colors")
def get_track_accent_colors(track_id: str):
    return _get_accent_colors(track_id)

@app.get("/api/artist/{artist_id}/accent-colors")
def get_artist_accent_colors(artist_id: str):
    return _get_accent_colors(artist_id)

@app.get("/api/playlist/{playlist_id}/accent-colors")
def get_playlist_accent_colors(playlist_id: str):
    custom_image = _get_playlist_image_path(playlist_id)
    if os.path.exists(custom_image):
        return _get_accent_colors(playlist_id, image_path=custom_image)
    first = (PlaylistTrack.select(PlaylistTrack, Track)
             .join(Track)
             .where(PlaylistTrack.playlist == playlist_id)
             .order_by(PlaylistTrack.position)
             .first())
    if first:
        return _get_accent_colors(str(first.track.album_id))
    return {"error": "No image available"}


# --- PLAYLIST ROUTES ---

_PLAYLIST_SORT_FIELDS = {"name", "track_count", "duration_ms"}

def _playlist_order_expr(sort_by: str, sort_order: str):
    asc = sort_order == "asc"
    secondary = Playlist.name.collate("NOCASE").asc()
    if sort_by == "track_count":
        primary = fn.COUNT(PlaylistTrack.id).asc() if asc else fn.COUNT(PlaylistTrack.id).desc()
    elif sort_by == "duration_ms":
        primary = fn.SUM(Track.duration_ms).asc() if asc else fn.SUM(Track.duration_ms).desc()
    else:  # name
        primary = Playlist.name.collate("NOCASE").asc() if asc else Playlist.name.collate("NOCASE").desc()
    return primary, secondary

@app.get("/api/playlists/count")
def get_playlists_count():
    return {"count": Playlist.select().count()}

@app.get("/api/playlists")
def get_playlists(sort_by: str = "name", sort_order: str = "asc",
                  start_index: int | None = None, end_index: int | None = None):
    if sort_by not in _PLAYLIST_SORT_FIELDS:
        sort_by = "name"
    if sort_order not in ("asc", "desc"):
        sort_order = "asc"
    primary, secondary = _playlist_order_expr(sort_by, sort_order)
    playlists = (Playlist.select(
                     Playlist,
                     fn.COUNT(PlaylistTrack.id).alias("track_count"),
                     fn.SUM(Track.duration_ms).alias("total_ms"),
                 )
                 .join(PlaylistTrack, JOIN.LEFT_OUTER)
                 .join(Track, JOIN.LEFT_OUTER)
                 .group_by(Playlist.id)
                 .order_by(primary, secondary))
    if start_index is not None and end_index is not None:
        playlists = playlists.offset(start_index).limit(end_index - start_index + 1)
    # Gather up to 4 unique album IDs per playlist for cover art (preserving position order)
    all_playlist_ids = [p.id for p in playlists]
    album_ids_map: dict[str, list[str]] = {pid: [] for pid in all_playlist_ids}
    for item in (PlaylistTrack.select(PlaylistTrack, Track)
                 .join(Track)
                 .where(PlaylistTrack.playlist << all_playlist_ids)
                 .order_by(PlaylistTrack.playlist, PlaylistTrack.position)):
        pid = str(item.playlist_id)
        album_id = str(item.track.album_id)
        ids = album_ids_map.get(pid, [])
        if len(ids) < 4 and album_id not in ids:
            ids.append(album_id)
    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "track_count": p.track_count or 0,
            "duration_ms": p.total_ms or 0,
            "first_album_ids": album_ids_map.get(p.id, []),
        }
        for p in playlists
    ]

@app.post("/api/playlists")
def create_playlist(name: str = Body(..., embed=True), description: str = Body("", embed=True)):
    playlist = Playlist.create(id=str(uuid.uuid4()), name=name, description=description)
    return {"id": playlist.id, "name": playlist.name, "description": playlist.description}

@app.get("/api/playlist/{playlist_id}")
def get_playlist_details(playlist_id: str):
    playlist = Playlist.get_or_none(Playlist.id == playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    items = (PlaylistTrack.select(PlaylistTrack, Track, Artist, Album)
             .join(Track)
             .join(Artist, on=(Track.artist == Artist.id))
             .switch(Track)
             .join(Album, on=(Track.album == Album.id))
             .where(PlaylistTrack.playlist == playlist_id)
             .order_by(PlaylistTrack.position))
    tracks = [
        {
            "item_id": item.id,
            "id": item.track.id,
            "title": item.track.title,
            "artist_name": item.track.artist.name if item.track.artist else "Unknown",
            "album_name": item.track.album.title if item.track.album else "Unknown",
            "album_id": str(item.track.album.id) if item.track.album else None,
            "duration_ms": item.track.duration_ms,
            "rating": item.track.rating,
        }
        for item in items
    ]
    return {
        "playlist": {"id": playlist.id, "name": playlist.name, "description": playlist.description},
        "tracks": tracks,
    }

@app.patch("/api/playlist/{playlist_id}")
def update_playlist(playlist_id: str, name: str = Body(None, embed=True), description: str = Body(None, embed=True)):
    playlist = Playlist.get_or_none(Playlist.id == playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    if name is not None:
        playlist.name = name
    if description is not None:
        playlist.description = description
    playlist.save()
    return {"id": playlist.id, "name": playlist.name, "description": playlist.description}

@app.delete("/api/playlist/{playlist_id}")
def delete_playlist(playlist_id: str):
    playlist = Playlist.get_or_none(Playlist.id == playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    playlist.delete_instance(recursive=True)
    image_path = _get_playlist_image_path(playlist_id)
    if os.path.exists(image_path):
        os.remove(image_path)
    return {"status": "deleted"}

@app.post("/api/playlist/{playlist_id}/tracks")
def add_tracks_to_playlist(playlist_id: str, track_ids: list[str] = Body(..., embed=True)):
    playlist = Playlist.get_or_none(Playlist.id == playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    last = (PlaylistTrack.select()
            .where(PlaylistTrack.playlist == playlist_id)
            .order_by(PlaylistTrack.position.desc())
            .first())
    start_pos = (last.position + 1.0) if last else 0.0
    with peewee_db.atomic():
        for i, tid in enumerate(track_ids):
            if Track.get_or_none(Track.id == tid):
                PlaylistTrack.create(playlist=playlist_id, track=tid, position=start_pos + i)
    return {"status": "ok"}

@app.delete("/api/playlist/{playlist_id}/tracks")
def remove_tracks_from_playlist(playlist_id: str, item_ids: list[int] = Body(..., embed=True)):
    PlaylistTrack.delete().where(
        (PlaylistTrack.playlist == playlist_id) & (PlaylistTrack.id << item_ids)
    ).execute()
    return {"status": "ok"}

@app.patch("/api/playlist/{playlist_id}/tracks/reorder")
def reorder_playlist_track(playlist_id: str, item_id: int = Body(..., embed=True), new_index: int = Body(..., embed=True)):
    dragged = PlaylistTrack.get_or_none(
        (PlaylistTrack.id == item_id) & (PlaylistTrack.playlist == playlist_id)
    )
    if not dragged:
        raise HTTPException(status_code=404, detail="Item not found")
    sorted_items = list(
        PlaylistTrack.select()
        .where((PlaylistTrack.playlist == playlist_id) & (PlaylistTrack.id != item_id))
        .order_by(PlaylistTrack.position)
    )
    if not sorted_items or new_index <= 0:
        new_pos = (sorted_items[0].position - 1.0) if sorted_items else 0.0
    elif new_index >= len(sorted_items):
        new_pos = sorted_items[-1].position + 1.0
    else:
        new_pos = (sorted_items[new_index - 1].position + sorted_items[new_index].position) / 2.0
    dragged.position = new_pos
    dragged.save()
    return {"status": "ok"}

@app.get("/api/playlist/{playlist_id}/tracks")
def get_playlist_tracks(playlist_id: str):
    items = (PlaylistTrack.select(PlaylistTrack, Track)
             .join(Track)
             .where(PlaylistTrack.playlist == playlist_id)
             .order_by(PlaylistTrack.position))
    return [{"id": item.track.id, "album_id": str(item.track.album_id) if item.track.album_id else None} for item in items]

@app.post("/api/playlist/{playlist_id}/image")
async def upload_playlist_image(playlist_id: str, file: UploadFile = File(...)):
    playlist = Playlist.get_or_none(Playlist.id == playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    content = await file.read()
    with open(_get_playlist_image_path(playlist_id), "wb") as f:
        f.write(content)
    _accent_color_cache.pop(playlist_id, None)
    return {"status": "ok"}


# --- SETTINGS ROUTES ---

@app.get("/api/settings")
def get_settings():
    return settings.settings

@app.patch("/api/settings")
def update_settings(data: dict = Body(...)):
    global provider, db

    for key, value in data.items():
        if key in settings.defaults:
            settings.set(key, value)

    # Switching library source swaps in a different provider *and* its own
    # database, so the two libraries stay independent. Stop playback first since
    # the current queue/track lives in the database we're about to swap out.
    if "library_source" in data:
        playback.stop_for_source_switch()
        db = switch_database(settings.get("library_source"))
        provider = create_provider(settings)
        playback.provider = provider
    # Otherwise, if any of the active provider's own settings changed,
    # reconfigure it live so the user doesn't have to restart the app.
    elif any(key in data for key in provider.SETTINGS_KEYS):
        provider.configure(settings)

    return settings.settings


# --- SYNC ROUTES ---

@app.post("/api/sync")
def start_sync():
    started = sync.start(provider)
    return {"started": started, "status": sync.state["status"]}

@app.get("/api/sync/status")
def sync_status():
    return sync.state


# --- METADATA ROUTES ---

@app.get("/api/metadata/status")
def metadata_status():
    return metadata.state

@app.post("/api/metadata/enrich")
def start_enrichment(force: bool = False):
    """Manually trigger metadata enrichment for all artists/albums."""
    if not settings.get("enable_online_metadata"):
        return {"started": False, "status": "disabled"}
    started = metadata.start_background_enrichment(force=force)
    return {"started": started, "status": metadata.state["status"]}


@app.websocket("/ws/sync")
async def sync_ws(websocket: WebSocket):
    await websocket.accept()

    loop = asyncio.get_running_loop()

    def on_update(state):
        asyncio.run_coroutine_threadsafe(websocket.send_json(state), loop)

    sync.add_listener(on_update)

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("action") == "start":
                sync.start(provider)
    except WebSocketDisconnect:
        pass
    finally:
        sync.remove_listener(on_update)


# --- PLAYBACK ROUTES ---

@app.post("/api/playback/play_track/{track_id}")
def play_track(track_id: str):
    playback.play_now(track_id, context_ids=[track_id])
    return {"status": "success"}

@app.post("/api/playback/play_album/{album_id}")
def play_album(album_id: str, track_id: str | None = None, shuffle: bool = False):
    album = Album.get_or_none(Album.id == album_id)
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")
    album_tracks = list(Track.select().where(Track.album == album_id).order_by(Track.disc_number, Track.track_number))
    track_ids = [t.id for t in album_tracks]
    if shuffle:
        random.shuffle(track_ids)
    try:
        playback.play_now(track_id or track_ids[0], track_ids)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "success"}

@app.post("/api/playback/play_artist/{artist_id}")
def play_artist(artist_id: str, track_id: str | None = None, shuffle: bool = False):
    artist = Artist.get_or_none(Artist.id == artist_id)
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")
    artist_tracks = (Track.select()
                     .join(Album)
                     .where(Track.artist == artist.id)
                     .order_by(Album.release_year, Track.disc_number, Track.track_number))
    track_ids = [t.id for t in artist_tracks]
    if not track_ids:
        raise HTTPException(status_code=404, detail="Artist has no tracks")
    if shuffle:
        random.shuffle(track_ids)
    try:
        playback.play_now(track_id or track_ids[0], track_ids)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "success"}

@app.post("/api/playback/play_playlist/{playlist_id}")
def play_playlist(playlist_id: str, track_id: str | None = None, shuffle: bool = False):
    playlist = Playlist.get_or_none(Playlist.id == playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    items = (PlaylistTrack.select(PlaylistTrack, Track)
             .join(Track)
             .where(PlaylistTrack.playlist == playlist_id)
             .order_by(PlaylistTrack.position))
    track_ids = [item.track.id for item in items]
    if not track_ids:
        raise HTTPException(status_code=400, detail="Playlist is empty")
    if shuffle:
        random.shuffle(track_ids)
    playback.play_now(track_id or track_ids[0], track_ids)
    return {"status": "success"}

@app.post("/api/playback/play")
def play(track_id: str | list[str] = Body(..., embed=True), shuffle: bool = Body(False, embed=True)):
    tracks = [track_id] if isinstance(track_id, str) else track_id
    if not tracks:
        raise HTTPException(status_code=400, detail="No tracks provided")
    if shuffle:
        random.shuffle(tracks)
    playback.play_now(tracks[0], tracks)
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

@app.post("/api/playback/seek/{seconds}")
def seek(seconds: float):
    playback.seek(seconds)
    return {"status": "success"}

@app.post("/api/playback/add_to_queue")
def add_to_queue(
    track_id: str | list[str] = Body(...),
    index: int = Body(-1)
):
    tracks = [track_id] if isinstance(track_id, str) else track_id
    playback.add_to_queue(tracks, index)
    return {"status": "success"}

@app.post("/api/playback/play_next")
def play_next(
    track_id: str | list[str] = Body(...),
    top: bool = Body(True)
):
    tracks = [track_id] if isinstance(track_id, str) else track_id
    playback.add_to_play_next(tracks, top=top)
    return {"status": "success"}

@app.post("/api/playback/remove_from_queue/{queue_item_id}")
def remove_from_queue(queue_item_id: str):
    playback.remove_from_queue(queue_item_id)
    return {"status": "success"}

@app.post("/api/playback/jump_to_queue_item/{queue_item_id}")
def jump_to_queue_item(queue_item_id: str):
    playback.jump_to_queue_item(queue_item_id)
    return {"status": "success"}

@app.get("/api/playback/queue")
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

@app.websocket("/ws/playback")
async def playback_ws(websocket: WebSocket):
    await websocket.accept()

    loop = asyncio.get_running_loop()

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
            elif action == "set_volume":
                playback.set_volume(data.get("value", 100))
            elif action == "clear_queue":
                playback.clear_queue()
            elif action == "set_repeat":
                playback.set_repeat(int(data.get("value", 0)))
                await websocket.send_json({"repeat_mode": playback.repeat_mode})
            elif action == "set_shuffle":
                playback.set_shuffle(bool(data.get("value", False)))
                await websocket.send_json({
                    "shuffle": playback.shuffle,
                    "queue": playback.build_queue_state(),
                })
            elif action == "move_queue_item":
                val = data.get("value") or {}
                item_id = val.get("id")
                position = val.get("position")
                if item_id and position:
                    current = playback._get_current()
                    if current:
                        sorted_others = list(
                            QueueItem.select()
                            .where(QueueItem.id != item_id)
                            .order_by(QueueItem.position)
                        )
                        current_idx = next(
                            (i for i, q in enumerate(sorted_others) if q.id == current.id), 0
                        )
                        if position == "next":
                            target_idx = current_idx + 1
                        else:
                            target_idx = len(sorted_others)
                        playback.reorder_queue(item_id, target_idx)

    except WebSocketDisconnect:
        pass
    finally:
        playback.remove_listener(on_state_update)


if __name__ == "__main__":
    # This runs only for the PyInstaller-frozen sidecar (production). A frozen
    # bundle has no importable "main" module on disk and no source files to
    # watch, so pass the app object directly and never enable reload here.
    # (Dev uses `uvicorn main:app --reload` via scripts/run-backend.cjs instead.)
    uvicorn.run(app, host=get_backend_host(), port=get_backend_port())
