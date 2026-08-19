"""Play history: what was listened to, when, and how much of it."""
from fastapi import APIRouter

from database import Album, Artist, PlayHistory, Track

router = APIRouter()


@router.get("/api/history")
def get_history(limit: int = 100):
    limit = max(1, min(limit, 500))
    entries = (PlayHistory.select(PlayHistory, Track, Artist, Album)
               .join(Track)
               .join(Artist)
               .switch(Track)
               .join(Album)
               .where(PlayHistory.visible == True)
               .order_by(PlayHistory.played_at.desc())
               .limit(limit))
    return [
        {
            "id": e.id,
            "track_id": e.track.id,
            "title": e.track.title,
            "artist_id": str(e.track.artist.id) if e.track.artist else None,
            "artist_name": e.track.artist.name if e.track.artist else "Unknown Artist",
            "album_name": e.track.album.title if e.track.album else "Unknown Album",
            "album_id": str(e.track.album.id) if e.track.album else None,
            "duration_ms": e.track.duration_ms,
            "played_at": e.played_at.isoformat(),
            "completion_pct": e.completion_pct,
        }
        for e in entries
    ]


@router.delete("/api/history/{entry_id}")
def delete_history_entry(entry_id: int):
    PlayHistory.delete().where(PlayHistory.id == entry_id).execute()
    return {"status": "ok"}


@router.delete("/api/history")
def clear_history():
    PlayHistory.delete().execute()
    return {"status": "ok"}
