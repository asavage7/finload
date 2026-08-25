"""Radio: bridges discovery.py's recommendation engine to the live queue,
generating and topping up ``QueueItem`` rows with ``queue_type=2`` ("mix").

The seam between discovery.py (a pure, DB-read-only algorithm module) and
playback_manager.py (which owns mpv and broadcast mechanics): it picks a seed for
an album/artist, reads what has actually happened this session back out of the
real queue, and turns that into a batch of new track IDs. It never touches mpv or
QueueItem writes itself.
"""
import random

from core.database import PlayHistory, PlaylistTrack, QueueItem, Track, track_scope_clause
from services import discovery
from services.audio_analysis import tracks_with_features

MIX_BATCH_SIZE = 3          # tracks generated per top-up, small on purpose so real
                             # user behavior steers the next batch quickly
MIX_LOW_WATER_MARK = 3      # top up once fewer than this many un-played mix tracks remain.
                             # The user-facing "autoplay_queue_length" setting overrides this;
                             # it stands in when that value is missing or unreadable
SESSION_CONTEXT_WINDOW = 20 # most recent queue items fed back as session context; also
                             # bounds how far back in-session skip feedback reaches
ELAPSED_SUM_LIMIT = 200     # how many queue items back the true-elapsed-time sum looks;
                             # discovery's time ramps all saturate well before this
SEED_SAMPLE_SIZE = 4        # tracks sampled off an album/artist to seed its radio
                             # (discovery.build_queue's extra_seed_ids)


def pick_seed_tracks(entity_type: str, entity_id: str, n: int = SEED_SAMPLE_SIZE,
                      library_ids: list[str] | None = None) -> list[str]:
    """Picks up to n random tracks to seed an album/artist/playlist radio from.
    Blending several tracks' feat/tags (discovery.build_queue's extra_seed_ids)
    describes "this album" or "this artist" far more steadily than one random
    pick. Returns [] if the entity has no tracks at all.

    Tracks that already have cached audio features are preferred, so a
    part-analyzed library seeds from something the DSP scorer can actually use
    instead of paying for an on-demand analysis (see
    audio_analysis.ensure_features). Falls back to the full set when none are
    analyzed yet.

    ``library_ids`` scopes seeding to the caller's current Jellyfin library
    selection (see database.track_scope_clause) -- this module stays state-free,
    so the caller resolves the setting and passes it in.
    """
    if entity_type == "album":
        query = Track.select(Track.id).where(Track.album == entity_id)
    elif entity_type == "artist":
        query = Track.select(Track.id).where(Track.artist == entity_id)
    elif entity_type == "playlist":
        query = Track.select(Track.id).join(PlaylistTrack).where(PlaylistTrack.playlist == entity_id)
    else:
        raise ValueError(f"unknown entity_type {entity_type!r}")
    scope = track_scope_clause(library_ids)
    if scope is not None:
        query = query.where(scope)
    ids = [row[0] for row in query.tuples()]
    if not ids:
        return []
    analyzed = tracks_with_features(ids)
    pool = [tid for tid in ids if tid in analyzed] or ids
    return random.sample(pool, min(n, len(pool)))


def session_context(current_position: float) -> tuple[list[str], dict[str, float], float, set[str]]:
    """The already-played-or-queued history a top-up should continue from:
    ``(track_ids, feedback, elapsed_ms, manual_ids)``.

    track_ids are the last SESSION_CONTEXT_WINDOW queue items at or before
    current_position, oldest first. feedback maps track IDs to the completion
    fraction (0-1) of their most recent play; tracks with no play record yet (the
    currently playing one, say) are absent and count as full listens downstream.
    elapsed_ms is the completion-weighted listening time across the whole session
    (up to ELAPSED_SUM_LIMIT items), which the context window alone would
    under-report. manual_ids marks which of track_ids the user queued by hand
    (queue_type != 2)."""
    items = list(QueueItem.select()
                 .where(QueueItem.position <= current_position)
                 .order_by(QueueItem.position.desc())
                 .limit(ELAPSED_SUM_LIMIT))
    ordered = list(reversed(items))
    all_ids = [i.track_id for i in ordered]
    ids = all_ids[-SESSION_CONTEXT_WINDOW:]
    manual_ids = {i.track_id for i in ordered[-SESSION_CONTEXT_WINDOW:] if i.queue_type != 2}

    feedback: dict[str, float] = {}
    durations: dict[str, int] = {}
    if all_ids:
        rows = (PlayHistory
                .select(PlayHistory.track, PlayHistory.completion_pct)
                .where((PlayHistory.track << all_ids) & (PlayHistory.in_progress == False))
                .order_by(PlayHistory.played_at))
        for row in rows:  # oldest first; the last write per track wins
            feedback[row.track_id] = row.completion_pct / 100.0
        durations = {t.id: t.duration_ms or 0
                     for t in Track.select(Track.id, Track.duration_ms)
                     .where(Track.id << all_ids)}
    elapsed_ms = sum(durations.get(tid, 0) * min(max(feedback.get(tid, 1.0), 0.0), 1.0)
                     for tid in all_ids)
    return ids, feedback, elapsed_ms, manual_ids


def generate_batch(seed_track_id: str, context: list[str], exclude_ids: set[str],
                    size: int = MIX_BATCH_SIZE,
                    extra_seed_ids: list[str] | None = None,
                    feedback: dict[str, float] | None = None,
                    manual_ids: set[str] | None = None,
                    elapsed_ms: float | None = None,
                    reroll: bool = False,
                    library_ids: list[str] | None = None) -> list[str]:
    """Returns up to `size` new track IDs for the mix. extra_seed_ids only
    matters on the first batch of an album/artist radio (see pick_seed_tracks);
    later top-ups drop it, since session_context then carries the real
    already-played signal. manual_ids marks which of `context` the user queued by
    hand, so a manual add steers the mix rather than counting as an algorithm
    pick. reroll is an explicit "give me a different mix". library_ids threads the
    caller's Jellyfin library selection into the candidate pool."""
    entries, _richness = discovery.build_queue(
        seed_track_id, queue_length=size, session_context=context, exclude_ids=exclude_ids,
        extra_seed_ids=extra_seed_ids, feedback=feedback, manual_ids=manual_ids,
        session_elapsed_ms=elapsed_ms, reroll=reroll, library_ids=library_ids,
    )
    return [e.track_id for e in entries]
