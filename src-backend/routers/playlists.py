"""Playlist CRUD, track management, reordering, and cover uploads."""
import os
import uuid

from fastapi import APIRouter, Body, File, HTTPException, UploadFile
from peewee import JOIN, fn

from database import Artist, Album, Playlist, PlaylistTrack, Track, db as peewee_db
from routers import accent_colors
from routers.images import playlist_image_path
from routers.library import apply_sort_and_page

router = APIRouter()

_PLAYLIST_SORTS = {
    "name": Playlist.name.collate("NOCASE"),
    "track_count": fn.COUNT(PlaylistTrack.id),
    "duration_ms": fn.SUM(Track.duration_ms),
}


def _require_playlist(playlist_id: str) -> Playlist:
    playlist = Playlist.get_or_none(Playlist.id == playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    return playlist


@router.get("/api/playlists/count")
def get_playlists_count():
    return {"count": Playlist.select().count()}


@router.get("/api/playlists")
def get_playlists(sort_by: str = "name", sort_order: str = "asc",
                  start_index: int | None = None, end_index: int | None = None):
    playlists = (Playlist.select(
                     Playlist,
                     fn.COUNT(PlaylistTrack.id).alias("track_count"),
                     fn.SUM(Track.duration_ms).alias("total_ms"),
                 )
                 .join(PlaylistTrack, JOIN.LEFT_OUTER)
                 .join(Track, JOIN.LEFT_OUTER)
                 .group_by(Playlist.id))
    playlists = apply_sort_and_page(playlists, _PLAYLIST_SORTS, sort_by, sort_order,
                                    "name", Playlist.name.collate("NOCASE"),
                                    start_index, end_index)
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


@router.post("/api/playlists")
def create_playlist(name: str = Body(..., embed=True), description: str = Body("", embed=True)):
    playlist = Playlist.create(id=str(uuid.uuid4()), name=name, description=description)
    return {"id": playlist.id, "name": playlist.name, "description": playlist.description}


@router.get("/api/playlist/{playlist_id}")
def get_playlist_details(playlist_id: str):
    playlist = _require_playlist(playlist_id)
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


@router.patch("/api/playlist/{playlist_id}")
def update_playlist(playlist_id: str, name: str = Body(None, embed=True), description: str = Body(None, embed=True)):
    playlist = _require_playlist(playlist_id)
    if name is not None:
        playlist.name = name
    if description is not None:
        playlist.description = description
    playlist.save()
    return {"id": playlist.id, "name": playlist.name, "description": playlist.description}


@router.delete("/api/playlist/{playlist_id}")
def delete_playlist(playlist_id: str):
    playlist = _require_playlist(playlist_id)
    playlist.delete_instance(recursive=True)
    image_path = playlist_image_path(playlist_id)
    if os.path.exists(image_path):
        os.remove(image_path)
    return {"status": "deleted"}


@router.post("/api/playlist/{playlist_id}/tracks")
def add_tracks_to_playlist(playlist_id: str, track_ids: list[str] = Body(..., embed=True)):
    _require_playlist(playlist_id)
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


@router.delete("/api/playlist/{playlist_id}/tracks")
def remove_tracks_from_playlist(playlist_id: str, item_ids: list[int] = Body(..., embed=True)):
    PlaylistTrack.delete().where(
        (PlaylistTrack.playlist == playlist_id) & (PlaylistTrack.id << item_ids)
    ).execute()
    return {"status": "ok"}


@router.patch("/api/playlist/{playlist_id}/tracks/reorder")
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


@router.get("/api/playlist/{playlist_id}/tracks")
def get_playlist_tracks(playlist_id: str):
    items = (PlaylistTrack.select(PlaylistTrack, Track)
             .join(Track)
             .where(PlaylistTrack.playlist == playlist_id)
             .order_by(PlaylistTrack.position))
    return [{"id": item.track.id, "album_id": str(item.track.album_id) if item.track.album_id else None} for item in items]


@router.post("/api/playlist/{playlist_id}/image")
async def upload_playlist_image(playlist_id: str, file: UploadFile = File(...)):
    _require_playlist(playlist_id)
    content = await file.read()
    with open(playlist_image_path(playlist_id), "wb") as f:
        f.write(content)
    accent_colors.invalidate(playlist_id)
    return {"status": "ok"}
