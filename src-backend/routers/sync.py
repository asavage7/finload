"""Single-item, fire-and-forget enrichment routes.

Bulk sync and the "enrich everything" jobs (metadata, genre enrichment) are
generic ``BackgroundJob``s served by ``routers/jobs.py``. This route is
different in kind: enriching one already-synced artist on demand, not tracked
via a job's progress state.
"""
from fastapi import APIRouter

from core import state

router = APIRouter()


@router.post("/api/artist/{artist_id}/enrich")
def enrich_artist(artist_id: str):
    """Enrich a single artist on demand (the artist page requests this when it
    renders an artist that has never been enriched)."""
    started = state.metadata.enrich_artist_async(artist_id)
    return {"started": started}
