"""Radio: bridges discovery.py's pure recommendation engine to the live
queue — generating and topping up ``QueueItem`` rows with ``queue_type=2``
("mix", auto-generated) as the user actually listens.

Kept separate from ``discovery.py`` (which stays a pure, DB-read-only
algorithm module — see its own docstring) and from ``playback_manager.py``
(which owns mpv/broadcast mechanics and shouldn't need to know how a mix
gets chosen). This module is the seam between the two: it knows how to pick
a seed for an album/artist, how to read "what's actually happened in this
session" back out of the real queue, and how to turn that into a batch of
new track IDs — but never touches mpv or QueueItem writes itself, so it
stays trivially testable on its own.
"""
import random

from core.database import PlayHistory, PlaylistTrack, QueueItem, Track, track_scope_clause
from services import discovery

MIX_BATCH_SIZE = 3          # tracks generated per top-up (small on purpose — lets
                             # real user behavior steer the *next* batch quickly
                             # rather than committing far ahead of time)
MIX_LOW_WATER_MARK = 3      # top up once fewer than this many un-played mix tracks remain
SESSION_CONTEXT_WINDOW = 20 # queue items (by position, most recent) fed back as
                             # session context — see discovery.build_queue's docstring.
                             # Also bounds how far back in-session skip feedback
                             # reaches, so it's wider than the drift strictly needs
ELAPSED_SUM_LIMIT = 200     # how many queue items back the true-elapsed-time sum looks;
                             # every time-based ramp in discovery saturates well before
                             # this much listening, so anything older can't matter


SEED_SAMPLE_SIZE = 4  # how many tracks to sample off an album/artist to seed its
                       # radio -- see pick_seed_tracks and discovery.build_queue's
                       # extra_seed_ids param


def pick_seed_tracks(entity_type: str, entity_id: str, n: int = SEED_SAMPLE_SIZE,
                      library_ids: list[str] | None = None) -> list[str]:
    """Picks up to n random tracks to seed an album/artist/playlist radio
    from, most representative track first (well, first in whatever order
    random.sample returns -- see below). Blending several tracks' feat/tags
    (see discovery.build_queue's extra_seed_ids) gives a much steadier
    picture of "this album", "this artist", or "this playlist" than a single
    random pick did, and keeping the pick itself cheap means starting a
    radio never has to wait on it -- see
    PlaybackManager.start_radio_from_reference. Returns [] if the
    album/artist/playlist has no tracks at all.

    ``library_ids`` scopes seeding to the caller's current Jellyfin library
    selection (see database.track_scope_clause) -- this module stays
    state-free, so the caller resolves the setting and passes it in.
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
    tracks = list(query)
    if not tracks:
        return []
    ids = [t.id for t in tracks]
    sample = random.sample(ids, min(n, len(ids)))
    return sample


def session_context(current_position: float) -> tuple[list[str], dict[str, float], float, set[str]]:
    """The real, already-played-or-queued history a top-up should continue
    from: ``(track_ids, feedback, elapsed_ms, manual_ids)``.

    track_ids are the last SESSION_CONTEXT_WINDOW queue items at or before
    current_position, oldest first. feedback maps track IDs to the
    completion fraction (0-1) of their most recent play — how the user
    actually responded (see discovery.build_queue's session_context/
    feedback params); tracks with no play record yet (e.g. the currently
    playing one) are simply absent and count as full listens downstream.
    elapsed_ms is the completion-weighted listening time across the whole
    session (up to ELAPSED_SUM_LIMIT items), not just the context window —
    discovery's time-based ramps need the real session age, which the
    window alone under-reports. manual_ids is which of track_ids the user
    queued by hand rather than the mix picking (queue_type != 2), scoped to
    the same window — see discovery.build_queue's manual_ids param."""
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
    """Returns up to `size` new track IDs for the mix. extra_seed_ids is
    only meaningful on the first batch of an album/artist radio (see
    pick_seed_tracks) -- later top-ups don't re-pass it, since by then
    session_context carries the real, already-played signal instead.
    manual_ids marks which of `context` the user queued by hand (see
    session_context) so a manual add steers the mix instead of being
    treated as just another algorithm pick. reroll marks an explicit
    "give me a different mix" regeneration (see discovery.build_queue's
    reroll param). library_ids is the caller's current Jellyfin library
    selection, threaded through to discovery.build_queue's candidate pool --
    see pick_seed_tracks."""
    entries, _richness = discovery.build_queue(
        seed_track_id, queue_length=size, session_context=context, exclude_ids=exclude_ids,
        extra_seed_ids=extra_seed_ids, feedback=feedback, manual_ids=manual_ids,
        session_elapsed_ms=elapsed_ms, reroll=reroll, library_ids=library_ids,
    )
    return [e.track.id for e in entries]
