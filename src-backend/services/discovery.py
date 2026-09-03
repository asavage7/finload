"""Anchor-based discovery queue builder.
Takes into account the following factors:
1. Genre tag similarity
2. DSP feature similarity (timbre, tempo)
3. BPM similarity
4. Recency of play for track/album/artist (fatigue)
5. Play history (skips repel, repeats penalize, full plays reward similar tracks)
6. Track rating
7. Manual queue adds (anchor harder, fade slower than algorithm picks)
There's probably more I'm forgetting.

An initial pool of tracks is picked based on a "seed" track or list of seed tracks.
The first track is a weighted random pick based on relevance to avoid queues being the same.
After the first track, the next track is picked based on relevance to the last track played,
with a recency-weighted average of all tracks played in the session as a secondary anchor.
After a while, the seed track's influence decays so the queue can drift away from the seed and explore the library.

Tag similarity, BPM similarity, and DSP similarity are all combined into a single "similarity" score.
All 3 report values from [0-1] so that in the future the UI can give a user-friendly score breakdown.

Certain values (genre, DSP, etc.) that run as background jobs are weighted less if the library is only partially analyzed.

Wired into the live queue by radio.py. Everything above build_queue is a DB-read-only scoring layer.
build_queue decides which candidates get picked.
"""
import datetime
import heapq
import json
import logging
import math
import random
from operator import mul

from core.database import (Album, AlbumGenre, Artist, ArtistGenre, Genre, PlayHistory, Track,
                      TrackFeatures, track_scope_clause)

logger = logging.getLogger(__name__)

# Similarity scoring tunables.

# These 3 must sum to 1, and automatically adjust when there's poor data on one or more.
GENRE_WEIGHT = 0.25    
TIMBRE_WEIGHT = 0.5   
TEMPO_WEIGHT = 0.25    

DSP_FULL_COVERAGE = 0.9         # Fraction of library analyzed where DSP is full strength. If below this, it's a % of the library analyzed.
ARTIST_TAG_WEIGHT = 0.75        # Fraction of album tag weights to use for artist tags when creating a tag profile. Default is 0.75 (artist tags are 75% as important as album tags).
                                # This is because artists change styles over time, so artist tags are more of a general profile compared to the album, but still important.
BPM_DELTA_NORM = 60.0           # Standard divisor to normalize BPM differences to [0-1] for scoring.
VEC_DIST_NORM = 8.0             # Fallback distance divisor for a track with no hubness stats yet: ~sqrt(2 * vector length), typical for two standardized vectors.

# Queue building tuneables.

SKIP_THRESHOLD = 0.3            # Fraction of a track that needs to be played to avoid a skip penalty.
RECENCY_DECAY = 0.85            # Per-round fade multiplier for a track's influence. Tracks start at full importance so the queue flows well.
ANCHOR_WEIGHT_FLOOR = 0.02      # Floor before a track's influence weight is set to zero.
SEED_FLOOR = 0.15               # Seed (initial track/tracks) relevance floor, to avoid forgetting about it entirely.
SEED_FLOOR_RELAXATION = 0.353   # In a thin neighborhood, raises the seed floor higher to lean on the seed longer. Has no effect in a rich neighborhood.
CLOSE_MATCH_BASELINE = 0.5      # Baseline score before a track is seen as "similar", used to determine how thin/rich a neighborhood of songs is.
RICH_MATCH_SOFTNESS = 0.05      # How soft the line between "rich" and "thin" neighborhoods is. Rich neighborhood = many good song picks.
RICH_MATCH_FRACTION = 0.01      # Fraction of the candidate pool, weighted by closeness (a near-perfect match counts fully, a borderline one barely), that reads a neighborhood as halfway between "rich" and "thin".
SEED_DECAY_MINUTES = 60         # Listening time in minutes for the seed to drop to SEED_FLOOR, considered maximum drift.
SEED_CLUSTER_RADIUS = 0.5       # Multi-track seeds discard any tracks more distant than this from the medoid, to avoid badly averaged data.
MANUAL_BOOST = 3.0              # A manual queue add is worth this times as much as an algorithm pick.
MANUAL_RECENCY_DECAY = 0.92     # Manual queue adds fade slower than algorithm picks, so a user can "lock in" a track for a while.
SKIP_REPEL = 0.4                # A skip is multiplied to similarity by this amount, so a skipped track's neighbors are suppressed in the next pick.
SKIP_FATIGUE_DECAY = 0.8        # Per-play falloff of skip recency, walked backward through a track's play history (most recent skip counts full, older ones fade).
ARTIST_REPEAT_PENALTY = 0.55    # Decaying penalty used when an artist's song plays, to avoid a queue being only one artist
ARTIST_REPEAT_RELAXATION = 0.289# How much the repeat penalty is relaxed over time, to not just ban an artist.
ALBUM_REPEAT_PENALTY = 0.50     # Decaying penalty used when an album's song plays, to avoid a queue being only one album. Stricter than artist since similar mastering makes songs appear much more similar.
ALBUM_REPEAT_RELAXATION = 0.48  # How much the repeat penalty is relaxed over time, to not just ban an album.
LENIENCY_BASELINE = 0.62        # Relevance floor where repeat-penalty leniency starts, above CLOSE_MATCH_BASELINE because same-album DSP similarity clusters near 0.8.
ARTIST_QUALITY_SPAN = 0.3       # relevance above LENIENCY_BASELINE needed for full leniency
LENIENCY_SUSTAIN_DECAY = 0.9    # Raises leniency to this power per unit of artist load, so sustained repetition converges back to the full penalty. 1.0 disables.
REPEAT_DECAY = 0.8              # Repeat load counts past picks by recency, not a window. Multiplier per-round.
PRESEED_SEED_ALBUM = True       # Automatically penalize a seed's album to avoid the next song being the same album. Always off for albums with "Various Artists" credited.
SEED_ALBUM_OPENER_PENALTY = 0.15# Extra multipler on a seed's album to avoid the next song being the same album. Double penalized with ALBUM_REPEAT_PENALTY
SEED_ALBUM_PENALTY_DECAY = 0.4  # Per-pick fade on the seed's album opener penalty, so it can recur after a while.
CONFIDENCE_RATIO = 0.8          # Picks draw from a pool of candidates scoring within this fraction of the best, so a single strong match doesn't dominate the draw.
ABSOLUTE_SCORE_FLOOR = 0.18     # Floor for a song to not be worth even considering. The relative window above would open the draw to the whole library, so build_queue takes the single best outright instead.
CONFIDENCE_POOL_MAX = 10        # Max amount of tracks to use in a pool for each pick.
OPENER_MAX_ARTISTS = 10         # Max amount of unique artists in the opener pool, so a single artist doesn't dominate the draw.
OPENER_RATIO = 0.7              # All opener candidates have to be good picks, so a thin neighborhood doesn't pool terrible tracks.
OPENER_WEIGHT_POWER = 2.0       # Opener draw weight = score**this: the best fit usually opens the queue, but a few other good fits are still possible.
RATING_NUDGE = 0.1              # Per-star score change around a neutral 3 stars
SHORT_TRACK_FULL_S = 60         # Score ramps linearly with track length up to this in seconds, penalizing intros/interludes. Set low thanks to Minor Threat.
FATIGUE_HALF_LIFE_DAYS = 12.0   # Recovery rate for a recently played track. Default: Track stops being penalized after 12 days. Tracks can still appear during the time, just not as likely.
POOL_CAP = 1000                  # candidates the per-pick loop scores, ranked by relevance.

def _bulk_load_features() -> dict[str, dict]:
    """Returns a feature dict for every track with current-version cached features.
    Timbre-mean, timbre-variance and spectral-contrast are concatenated into one vector
    and standardized per dimension across the library so one doesn't outweight the others."""
    import numpy as np

    from services.audio_analysis import FEATURE_VERSION

    track_ids: list[str] = []
    vectors: list[list[float]] = []
    meta: list[tuple] = []
    query = TrackFeatures.select(
        TrackFeatures.track, TrackFeatures.bpm, TrackFeatures.features,
        TrackFeatures.dist_center, TrackFeatures.dist_scale,
    ).where(TrackFeatures.feature_version == FEATURE_VERSION).tuples()
    for track_id, bpm, raw, dist_center, dist_scale in query:
        if not raw:
            continue
        f = json.loads(raw)
        track_ids.append(track_id)
        vectors.append(f["mfcc_mean"] + f["mfcc_std"] + f["contrast_mean"])
        meta.append((bpm, dist_center, dist_scale))

    if not track_ids:
        # If the library is analyzed under an old FEATURE_VERSION, warn about missing DSP
        # info to avoid odd behavior from prevous versions.
        stale = (TrackFeatures.select()
                 .where(TrackFeatures.feature_version != FEATURE_VERSION).count())
        if stale:
            logger.warning(
                "No cached features at version %s, but %d track(s) are cached at an "
                "older version. Discovery is running without any DSP evidence until "
                "the library is re-analyzed.", FEATURE_VERSION, stale)
        return {}

    matrix = np.asarray(vectors, dtype=float)
    std = matrix.std(axis=0)
    std[std < 1e-12] = 1.0  # a constant dimension centers to zero and carries no signal
    matrix = (matrix - matrix.mean(axis=0)) / std
    norms = (matrix * matrix).sum(axis=1).tolist()

    # Returns a dict of track_id -> {"vec": (vector), "sq": squared_norm, "bpm": bpm, "dist_center": dist_center, "dist_scale": dist_scale}
    return {
        track_id: {"vec": tuple(vec), "sq": sq, "bpm": bpm or 0.0,
                   "dist_center": dist_center or 0.0, "dist_scale": dist_scale or 0.0}
        for track_id, vec, sq, (bpm, dist_center, dist_scale)
        in zip(track_ids, matrix.tolist(), norms, meta)
    }

def _canon_tag(name: str) -> str:
    """Canonicalize a tag name. Replaces "-" and "_" with spaces, lowercases, and collapses whitespace."""
    return " ".join(name.lower().replace("-", " ").replace("_", " ").split())

def _normalize_group(rows: list[tuple[str, int]]) -> dict[str, float]:
    """Reurns a scaled dict of canonical tag -> weight in [0, 1] for one entity's raw tag rows.
    MusicBrainz/Jellyfin are fully trusted, so weight=0 is presence-only and counts as a tag.
    Scaled with the top tag at 1.0, square-rooted so secondary tags survive rather than being crushed by the top one."""
    if not rows:
        return {}
    max_w = max(w for _, w in rows)
    scaled: dict[str, float] = {}
    for name, w in rows:
        # Still add with weight 0, just means not Last.fm
        v = 1.0 if max_w <= 0 else math.sqrt(w / max_w)
        prev = scaled.get(name)
        if prev is None or v > prev:
            scaled[name] = v
    return scaled

def _blend_tags(album_tags: dict[str, float], artist_tags: dict[str, float]) -> dict[str, float]:
    """Returns a per-track dict of canonical tag -> weight in [0, 1] for one track from its album's and artist's normalized tag groups."""
    weights = dict(album_tags)
    for name, w in artist_tags.items():
        w *= ARTIST_TAG_WEIGHT
        prev = weights.get(name)
        if prev is None or w > prev:
            weights[name] = w
    return weights

def _bulk_genre_rows(model, entity_field) -> dict[str, list[tuple[str, int]]]:
    """Returns (canonical tag, weight) rows grouped by album or artist id, in one query."""
    rows: dict[str, list[tuple[str, int]]] = {}
    for entity_id, weight, name in model.select(entity_field, model.weight, Genre.name).join(Genre).tuples():
        rows.setdefault(entity_id, []).append((_canon_tag(name), weight))
    return rows


def _bulk_track_tags(tracks: list[tuple]) -> dict[str, dict[str, float]]:
    """Blended tag weights per track id. Tracks sharing an (album, artist) pair
    share one dict, so a large library normalizes a few thousand groups rather
    than one per track. The dicts are read-only to every caller."""
    album_tags = {aid: _normalize_group(rows)
                  for aid, rows in _bulk_genre_rows(AlbumGenre, AlbumGenre.album).items()}
    artist_tags = {aid: _normalize_group(rows)
                   for aid, rows in _bulk_genre_rows(ArtistGenre, ArtistGenre.artist).items()}
    empty: dict[str, float] = {}
    by_group: dict[tuple[str, str], dict[str, float]] = {}
    tags: dict[str, dict[str, float]] = {}
    for track_id, artist_id, album_id, _rating, _duration in tracks:
        key = (album_id, artist_id)
        group = by_group.get(key)
        if group is None:
            group = by_group[key] = _blend_tags(album_tags.get(album_id, empty),
                                                artist_tags.get(artist_id, empty))
        tags[track_id] = group
    return tags

def _weighted_overlap(a: dict[str, float], b: dict[str, float]) -> float:
    """Weighted Jaccard (Ruzicka similarity) over tag strengths. Walks both dicts directly."""
    if not a or not b:
        return 0.0
    num = 0.0
    den = 0.0
    for key, va in a.items():
        vb = b.get(key)
        if vb is None:
            den += va
        elif va < vb:
            num += va
            den += vb
        else:
            num += vb
            den += va
    for key, vb in b.items():
        if key not in a:
            den += vb
    return num / den if den else 0.0

def _sq_distance(feat_a: dict, feat_b: dict) -> float:
    """Squared euclidean distance between two feature vectors, via
    ||a-b||^2 = ||a||^2 + ||b||^2 - 2 a.b so the inner loop is one C-level
    map/sum over the cached norms rather than a Python generator."""
    dot = sum(map(mul, feat_a["vec"], feat_b["vec"]))
    return max(feat_a["sq"] + feat_b["sq"] - 2.0 * dot, 0.0)

def _bpm_distance(bpm_a: float, bpm_b: float) -> float:
    """Absolute BPM difference. No octave correction, causes false positives."""
    return abs(bpm_a - bpm_b)

def _normal_sf(x: float, mean: float, std: float) -> float:
    """Survival function of Normal(mean, std) at x: P(X > x)."""
    if std <= 1e-9:
        return 0.0 if x >= mean else 1.0
    z = (x - mean) / std
    return 1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2)))

# Caution: This code was AI-Generated in its entirety.

def _mutual_proximity(dist: float, feat_a: dict, feat_b: dict) -> float:
    """Mutual Proximity (Schnitzer et al.): MP(a,b) = P(dist > d | a) *
    P(dist > d | b), each against that track's own cached distance distribution
    (dist_center/dist_scale, from audio_analysis.compute_hubness_stats). Corrects
    for "hub" tracks that read deceptively close to much of the library. Falls
    back to plain normalized closeness when a track has no hubness stats yet."""
    if feat_a["dist_scale"] <= 1e-9 or feat_b["dist_scale"] <= 1e-9:
        return 1.0 - min(dist / VEC_DIST_NORM, 1.0)
    p_a = _normal_sf(dist, feat_a["dist_center"], feat_a["dist_scale"])
    p_b = _normal_sf(dist, feat_b["dist_center"], feat_b["dist_scale"])
    return p_a * p_b

# End AI-Generated code.

def similarity(feat_a: dict | None, feat_b: dict | None, tags_a: dict[str, float],
                tags_b: dict[str, float], dsp_weight: float = 1.0) -> float:
    """Returns a confidence from [0-1] that a pair of tracks are related.
    Confidence is a weighted blend of genre tag overlap, DSP feature similarity, and BPM similarity.
    If one of these signals is missing, the others gain more weight to make up the difference. If all signals are missing, returns 0.0."""
    tag_overlap = _weighted_overlap(tags_a, tags_b)
    tag_confidence = min(len(tags_a), len(tags_b), 4) / 4.0
    genre_pct = tag_confidence * tag_overlap
    w_genre = GENRE_WEIGHT if (tags_a and tags_b) else 0.0

    if dsp_weight <= 0.0 or feat_a is None or feat_b is None:
        return genre_pct

    vec_closeness = _mutual_proximity(math.sqrt(_sq_distance(feat_a, feat_b)), feat_a, feat_b)
    norm_bpm = min(_bpm_distance(feat_a["bpm"], feat_b["bpm"]) / BPM_DELTA_NORM, 1.0)
    timbre_pct = vec_closeness
    tempo_pct = 1.0 - norm_bpm

    w_timbre = TIMBRE_WEIGHT * dsp_weight
    w_tempo = TEMPO_WEIGHT * dsp_weight
    total_w = w_genre + w_timbre + w_tempo
    return (w_genre * genre_pct + w_timbre * timbre_pct + w_tempo * tempo_pct) / total_w

def _blend_profile(track_ids: list[str], features_by_id: dict[str, dict]) -> dict | None:
    """Average N tracks' feature vectors into one synthetic feat (build_queue's
    extra_seed_ids). None if none of the ids have cached features."""
    vec_acc: list[float] | None = None
    bpm_acc = 0.0
    count = 0
    for track_id in track_ids:
        feat = features_by_id.get(track_id)
        if feat is None:
            continue
        if vec_acc is None:
            vec_acc = [0.0] * len(feat["vec"])
        for i, v in enumerate(feat["vec"]):
            vec_acc[i] += v
        bpm_acc += feat["bpm"]
        count += 1

    if vec_acc is None:
        return None
    vec = tuple(v / count for v in vec_acc)
    # Hubness is a property of one track's real distance distribution, not something to average
    return {"vec": vec, "sq": sum(v * v for v in vec), "bpm": bpm_acc / count,
            "dist_center": 0.0, "dist_scale": 0.0}

def _dsp_weight(analyzed: int, total: int) -> float:
    """Automatically scales DSP's weight in scoring if a library doesn't have full analysis."""
    coverage = analyzed / total if total else 0.0
    return min(coverage / DSP_FULL_COVERAGE, 1.0)

# Entity Similarity, used to populate dynamic but not live sections in home/detail pages.

class _EntityIndex:
    """Album and artist profiles for one comparison pass, loaded together
    because both come off the same track scan."""

    __slots__ = ("album_feats", "album_tags", "artist_feats", "artist_tags",
                 "album_artist", "dsp_weight")

def _entity_tag_profiles() -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    """Normalized tag weights per album id and per artist id."""
    album_groups = {aid: _normalize_group(rows)
                    for aid, rows in _bulk_genre_rows(AlbumGenre, AlbumGenre.album).items()}
    artist_groups = {aid: _normalize_group(rows)
                     for aid, rows in _bulk_genre_rows(ArtistGenre, ArtistGenre.artist).items()}
    empty: dict[str, float] = {}

    album_tags: dict[str, dict[str, float]] = {}
    peak_by_artist: dict[str, dict[str, float]] = {}
    for album_id, artist_id in Album.select(Album.id, Album.artist).tuples():
        own = album_groups.get(album_id, empty)
        album_tags[album_id] = _blend_tags(own, artist_groups.get(artist_id, empty))
        if own:
            peak = peak_by_artist.setdefault(artist_id, {})
            for name, w in own.items():
                prev = peak.get(name)
                if prev is None or w > prev:
                    peak[name] = w

    artist_tags = {
        artist_id: _blend_tags(peak_by_artist.get(artist_id, empty),
                               artist_groups.get(artist_id, empty))
        for artist_id in set(artist_groups) | set(peak_by_artist)
    }
    return album_tags, artist_tags

def load_entity_index(library_ids: list[str] | None = None) -> _EntityIndex:
    """Build every album's and artist's feature/tag profile in one pass over the
    library. Main cost of analysis, so this should stay loaded in memory."""
    query = Track.select(Track.id, Track.artist, Track.album)
    scope = track_scope_clause(library_ids)
    if scope is not None:
        query = query.where(scope)
    rows = list(query.tuples())

    features_by_id = _bulk_load_features()
    album_track_ids: dict[str, list[str]] = {}
    artist_track_ids: dict[str, list[str]] = {}
    analyzed = 0
    album_artist: dict[str, str] = {}
    for track_id, artist_id, album_id in rows:
        album_artist[album_id] = artist_id
        if track_id in features_by_id:
            analyzed += 1
            album_track_ids.setdefault(album_id, []).append(track_id)
            artist_track_ids.setdefault(artist_id, []).append(track_id)

    index = _EntityIndex()
    index.album_feats = {eid: _blend_profile(ids, features_by_id)
                         for eid, ids in album_track_ids.items()}
    index.artist_feats = {eid: _blend_profile(ids, features_by_id)
                          for eid, ids in artist_track_ids.items()}
    index.album_tags, index.artist_tags = _entity_tag_profiles()
    index.album_artist = album_artist
    index.dsp_weight = _dsp_weight(analyzed, len(rows))
    return index

def _rank_similar(target_id: str, feats: dict, tags: dict, exclude_ids: set[str],
                   cap: int, dsp_weight: float) -> list[str]:
    """Returns a list of entity ids most similar to target_id, best first. Scores every entity that
    has a profile of either kind, so an untagged album can still place."""
    target_feat = feats.get(target_id)
    target_tags = tags.get(target_id, {})
    if target_feat is None and not target_tags:
        return []
    scored: list[tuple[float, str]] = []
    for entity_id in feats.keys() | tags.keys():
        if entity_id == target_id or entity_id in exclude_ids:
            continue
        score = similarity(target_feat, feats.get(entity_id), target_tags,
                           tags.get(entity_id, {}), dsp_weight)
        if score > 0.0:
            scored.append((score, entity_id))
    return [entity_id for _, entity_id in heapq.nlargest(cap, scored)]

def similar_albums(album_id: str, cap: int = 20, exclude_artist_id: str | None = None,
                    library_ids: list[str] | None = None,
                    index: _EntityIndex | None = None) -> list[str]:
    """Album ids most similar to album_id, best first. Excludes an artist's own releases."""
    if index is None:
        index = load_entity_index(library_ids)
    exclude: set[str] = set()
    if exclude_artist_id is not None:
        exclude = {aid for aid, artist_id in index.album_artist.items()
                   if artist_id == exclude_artist_id}
    return _rank_similar(album_id, index.album_feats, index.album_tags,
                         exclude, cap, index.dsp_weight)

def similar_artists(artist_id: str, cap: int = 20,
                     library_ids: list[str] | None = None,
                     index: _EntityIndex | None = None) -> list[str]:
    """Artist ids most similar to artist_id, best first."""
    if index is None:
        index = load_entity_index(library_ids)
    return _rank_similar(artist_id, index.artist_feats, index.artist_tags,
                         set(), cap, index.dsp_weight)

def _duration_factor(duration_ms: int | None) -> float:
    """Returns a penalty multiplier for short tracks, from 0 to 1.0."""
    if not duration_ms:
        return 1.0
    return min((duration_ms / 1000.0) / SHORT_TRACK_FULL_S, 1.0)

def _coherent_seed(seed_ids: list[str], features_by_id: dict[str, dict],
                    tags_by_id: dict[str, dict[str, float]], dsp_weight: float,
                    radius: float) -> list[str]:
    """Returns a subset of seed_ids that are all mutually similar,
    so the seed is one coherent cluster rather than a varied catalogue.
    Uses the medoid as the center of the cluster and keeps only tracks within radius of it."""
    ids = [i for i in seed_ids if i in features_by_id]
    if len(ids) <= 2:
        return ids
    def s(a: str, b: str) -> float:
        return similarity(features_by_id[a], features_by_id[b],
                          tags_by_id.get(a, {}), tags_by_id.get(b, {}), dsp_weight)
    medoid = max(ids, key=lambda a: sum(s(a, b) for b in ids if b != a))
    return [i for i in ids if i == medoid or s(medoid, i) >= radius]

def _is_compilation(album_id: str) -> bool:
    """True when the album's credited artist is a "Various Artists" form."""
    row = Album.select(Artist.name).join(Artist).where(Album.id == album_id).tuples().first()
    return bool(row and row[0]) and "various artist" in row[0].lower()

def _repeat_load(recent: list[str]) -> dict[str, float]:
    """Recency-decayed tally per id: the last entry contributes 1.0, older ones
    fade by REPEAT_DECAY per step and sum."""
    load: dict[str, float] = {}
    weight = 1.0
    for key in reversed(recent):
        load[key] = load.get(key, 0.0) + weight
        weight *= REPEAT_DECAY
    return load

def _blend_seed_tags(seed_ids: list[str], tags_by_id: dict[str, dict[str, float]]) -> dict[str, float]:
    """Multi-track blended seed for album/artist radios. Averages out the tags of all seed tracks,
    but keeps the strongest weight for each instead of a true average."""
    blended: dict[str, float] = {}
    for tid in seed_ids:
        for name, w in tags_by_id.get(tid, {}).items():
            prev = blended.get(name)
            if prev is None or w > prev:
                blended[name] = w
    return blended

def _bulk_load_fatigue() -> dict[str, float]:
    """Returns a per-track playback-fatigue multiplier for every track with play history, in one query.
    Recency suppresses a recently played track, recovering over FATIGUE_HALF_LIFE_DAYS."""
    plays: dict[str, list[tuple[datetime.datetime, float]]] = {}
    query = (PlayHistory
             .select(PlayHistory.track, PlayHistory.played_at, PlayHistory.completion_pct)
             .where(PlayHistory.in_progress == False)
             .order_by(PlayHistory.played_at)
             .tuples())
    for track_id, played_at, completion_pct in query:
        plays.setdefault(track_id, []).append((played_at, completion_pct))

    now = datetime.datetime.now()
    skip_pct = SKIP_THRESHOLD * 100.0
    fatigue: dict[str, float] = {}
    for track_id, history in plays.items():
        days = (now - history[-1][0]).total_seconds() / 86400.0
        recency = 1.0 - math.exp(-max(days, 0.0) / FATIGUE_HALF_LIFE_DAYS)
        skip_load = 0.0
        weight = 1.0
        for _, pct in reversed(history):
            if pct < skip_pct:
                skip_load += weight
            weight *= SKIP_FATIGUE_DECAY
        fatigue[track_id] = recency * (0.5 ** skip_load)
    return fatigue

class QueueEntry:
    """One scored candidate."""

    __slots__ = ("track_id", "artist_id", "album_id", "rating", "duration_ms",
                 "feat", "tags", "fatigue", "sim_to_seed", "skip_repel", "anchor_sims")

    def __init__(self, row: tuple, feat, tags, fatigue):
        (self.track_id, self.artist_id, self.album_id,
         self.rating, self.duration_ms) = row
        self.feat = feat
        self.tags = tags
        self.fatigue = fatigue
        self.sim_to_seed = 0.0
        self.skip_repel = 0.0
        self.anchor_sims: list[float | None] = []

class _Anchors:
    """The session's played tracks, newest last, with the recency weights the
    relevance blend averages over."""

    __slots__ = ("feats", "tags", "base", "decay", "weights", "total", "start")

    def __init__(self):
        self.feats: list = []
        self.tags: list = []
        self.base: list[float] = []
        self.decay: list[float] = []
        self.weights: list[float] = []
        self.total = 1.0
        self.start = 0

    def __len__(self) -> int:
        return len(self.feats)

    def add(self, feat, tags, base: float, decay: float) -> None:
        self.feats.append(feat)
        self.tags.append(tags)
        self.base.append(base)
        self.decay.append(decay)

    def refresh(self) -> None:
        """Fold each anchor's base weight together with its own recency decay: the
        newest keeps its full base weight, older ones fall off by their per-anchor
        rate per step (manual adds fade slower). Leading anchors below
        ANCHOR_WEIGHT_FLOOR of the total drop out of the blend entirely."""
        n = len(self.base)
        weights = [b * d ** (n - 1 - i) for i, (b, d) in enumerate(zip(self.base, self.decay))]
        floor = sum(weights) * ANCHOR_WEIGHT_FLOOR
        start = 0
        while start < n and weights[start] < floor:
            start += 1
        self.weights = weights
        self.start = start
        self.total = sum(weights[start:]) or 1.0

class _Scorer:
    """Scores candidates at one point in a session. build_queue updates the
    mutable fields between picks."""

    __slots__ = ("anchors", "artist_load", "album_load", "seed_weight",
                 "richness", "dsp_weight", "quality_ref")

    def __init__(self, anchors: _Anchors, richness: float, dsp_weight: float,
                 best_sim: float):
        self.anchors = anchors
        self.artist_load: dict[str, float] = {}
        self.album_load: dict[str, float] = {}
        self.seed_weight = 1.0
        self.richness = richness
        self.dsp_weight = dsp_weight
        self.quality_ref = best_sim or 1.0 # Leniency is graded against the best match this seed actually has, not an absolute scale

    def score(self, e: QueueEntry) -> float:
        """Full score for one candidate, including repeat/skip penalties."""
        anchors = self.anchors
        n = len(anchors.feats)
        if n:
            sims = e.anchor_sims
            if len(sims) < n:
                sims.extend([None] * (n - len(sims)))
            feat, tags, dsp_weight = e.feat, e.tags, self.dsp_weight
            feats, anchor_tags, weights = anchors.feats, anchors.tags, anchors.weights
            acc = 0.0
            for i in range(anchors.start, n):
                s = sims[i]
                if s is None:
                    s = sims[i] = similarity(feats[i], feat, anchor_tags[i], tags, dsp_weight)
                acc += weights[i] * s
            session_rel = acc / anchors.total
            relevance = self.seed_weight * e.sim_to_seed + (1.0 - self.seed_weight) * session_rel
        else:
            relevance = e.sim_to_seed

        a_load = self.artist_load.get(e.artist_id, 0.0)
        b_load = self.album_load.get(e.album_id, 0.0)
        if a_load or b_load:
            ref = self.quality_ref
            quality = min(max(0.0, relevance - LENIENCY_BASELINE * ref)
                          / (ARTIST_QUALITY_SPAN * ref), 1.0)
            leniency = (1.0 - self.richness) * quality * LENIENCY_SUSTAIN_DECAY ** a_load
            artist_penalty = _relax(ARTIST_REPEAT_PENALTY, ARTIST_REPEAT_RELAXATION * leniency)
            album_penalty = _relax(ALBUM_REPEAT_PENALTY, ALBUM_REPEAT_RELAXATION * leniency)
            repeat_penalty = artist_penalty ** a_load * album_penalty ** b_load
        else:
            repeat_penalty = 1.0

        rating = 1.0 + RATING_NUDGE * (e.rating - 3) if e.rating else 1.0
        return ((relevance - e.skip_repel) * repeat_penalty * e.fatigue * rating
                * _duration_factor(e.duration_ms))

def _soft_hinge(x: float, softness: float) -> float:
    """max(0.0, x) with a rounded corner over softness.
    Used so a near-match still contributes a little to richness, instead of dropping to zero all at once.
    """
    z = x / softness
    if z > 30.0:      # exp overflows out here, and the ramp is linear anyway
        return x
    if z < -30.0:
        return 0.0
    return softness * math.log1p(math.exp(z))

def _relax(base: float, amount: float) -> float:
    """Eases base towards 1.0. Used to relax repeat penalties when the session is rich and the candidate is high-quality."""
    return base + (1.0 - base) * amount

def _resolve_seed(seed_track_id: str, extra_seed_ids: list[str] | None,
                   features_by_id: dict, tags_by_id: dict,
                   dsp_weight: float) -> tuple[dict | None, dict[str, float]]:
    """The seed's feat/tags, blended with extra_seed_ids (an album/artist
    description) when given. Hubness describes one track's real distance
    distribution and doesn't average, so a blend keeps the primary seed's."""
    seed_feat = features_by_id.get(seed_track_id)
    seed_tags = tags_by_id.get(seed_track_id, {})
    if not extra_seed_ids:
        return seed_feat, seed_tags

    seed_pool = _coherent_seed([seed_track_id, *extra_seed_ids], features_by_id,
                               tags_by_id, dsp_weight, SEED_CLUSTER_RADIUS)
    blended_feat = _blend_profile(seed_pool, features_by_id)
    if blended_feat is None:
        return seed_feat, seed_tags
    if seed_feat is not None:
        blended_feat["dist_center"] = seed_feat["dist_center"]
        blended_feat["dist_scale"] = seed_feat["dist_scale"]
    return blended_feat, _blend_seed_tags(seed_pool, tags_by_id)


def _session_anchors(session_context, feedback: dict[str, float], manual_ids: set[str],
                     track_by_id: dict, features_by_id: dict, tags_by_id: dict,
                     seed: Track, preseed_album: bool):
    """Builds session anchors from real playback history before scoring.
    Fully/mostly played tracks reinforce similar candidates. Skipped
    algorithm picks repel neighbors, while skipped manual picks are treated as
    a change of mind and ignored. Tracks missing from feedback are treated as
    unheard and only influence repeat penalties."""
    anchors = _Anchors()
    skips: list[tuple[dict | None, dict[str, float]]] = []
    recent_artists: list[str] = []
    recent_albums: list[str] = [seed.album_id] if preseed_album else []
    elapsed_ms = float(seed.duration_ms or 0)
    for ctx_id in (session_context or ()):
        ctx_row = track_by_id.get(ctx_id)
        if ctx_row is None:
            continue
        ctx_feat = features_by_id.get(ctx_id)
        ctx_tags = tags_by_id.get(ctx_id, {})
        if ctx_feat is None and not ctx_tags:
            continue
        recent_artists.append(ctx_row[1])
        recent_albums.append(ctx_row[2])
        if ctx_id not in feedback:
            continue
        completion = min(max(feedback[ctx_id], 0.0), 1.0)
        if completion < SKIP_THRESHOLD:
            if ctx_id not in manual_ids:
                skips.append((ctx_feat, ctx_tags))
        else:
            manual = ctx_id in manual_ids
            anchors.add(ctx_feat, ctx_tags, completion * (MANUAL_BOOST if manual else 1.0),
                        MANUAL_RECENCY_DECAY if manual else RECENCY_DECAY)
        elapsed_ms += (ctx_row[4] or 0) * completion
    return anchors, skips, recent_artists, recent_albums, elapsed_ms

def _candidate_pool(rows: list[tuple], exclude_ids: set[str], features_by_id: dict,
                    tags_by_id: dict, fatigue_by_id: dict) -> list[QueueEntry]:
    """QueueEntry for every candidate with real tag/DSP evidence."""
    candidates = []
    for row in rows:
        track_id = row[0]
        if track_id in exclude_ids:
            continue
        feat = features_by_id.get(track_id)
        tags = tags_by_id.get(track_id, {})
        if feat is None and not tags:
            continue
        candidates.append(QueueEntry(row, feat, tags, fatigue_by_id.get(track_id, 1.0)))
    return candidates

def build_queue(seed_track_id: str, queue_length: int = 20,
                 rng: random.Random | None = None,
                 session_context: list[str] | None = None,
                 exclude_ids: set[str] | None = None,
                 extra_seed_ids: list[str] | None = None,
                 feedback: dict[str, float] | None = None,
                 manual_ids: set[str] | None = None,
                 session_elapsed_ms: float | None = None,
                 reroll: bool = False,
                 library_ids: list[str] | None = None) -> tuple[list[QueueEntry], float]:
    """Returns a queue built from seed_track_id and a richness score.

    Returns (entries, richness) where richness is [0, 1] and estimates how much
    genuinely strong material surrounds the seed (RICH_MATCH_FRACTION), so callers
    can threshold and warn in thin neighborhoods.
    session_context/feedback/manual_ids describe the real session so top-ups
    continue naturally.

    Returns an empty queue when the seed has neither cached DSP features nor genre
    tags.
    """
    if rng is None:
        rng = random.Random()
    seed = Track.get_by_id(seed_track_id)

    # id, artist_id, album_id, rating, duration_ms
    query = Track.select(Track.id, Track.artist, Track.album, Track.rating, Track.duration_ms)
    scope = track_scope_clause(library_ids)
    if scope is not None:
        query = query.where(scope)
    rows = list(query.tuples())
    track_by_id = {row[0]: row for row in rows}
    features_by_id = _bulk_load_features()
    tags_by_id = _bulk_track_tags(rows)
    fatigue_by_id = _bulk_load_fatigue()

    dsp_weight = _dsp_weight(sum(1 for row in rows if row[0] in features_by_id), len(rows))

    seed_feat, seed_tags = _resolve_seed(seed_track_id, extra_seed_ids, features_by_id,
                                         tags_by_id, dsp_weight)
    if seed_feat is None and not seed_tags:
        return [], 0.0

    if exclude_ids is None:
        exclude_ids = set(session_context or ())
    exclude_ids = exclude_ids | {seed_track_id} | set(extra_seed_ids or ())
    feedback = feedback or {}
    manual_ids = manual_ids or set()

    # The seed's album pre-counts as played unless it's a compilation.
    preseed_album = PRESEED_SEED_ALBUM and not _is_compilation(seed.album_id)
    anchors, skips, recent_artists, recent_albums, elapsed_ms = _session_anchors(
        session_context, feedback, manual_ids, track_by_id, features_by_id, tags_by_id,
        seed, preseed_album)
    if session_elapsed_ms is not None:
        elapsed_ms = max(elapsed_ms, session_elapsed_ms)

    candidates = _candidate_pool(rows, exclude_ids, features_by_id, tags_by_id, fatigue_by_id)

    # Both are fixed for the whole build, so they are computed once here instead of per pick.
    for e in candidates:
        e.sim_to_seed = similarity(seed_feat, e.feat, seed_tags, e.tags, dsp_weight)
        if skips:
            e.skip_repel = SKIP_REPEL * max(similarity(f, e.feat, t, e.tags, dsp_weight)
                                            for f, t in skips)

    # Seed floor and repeat penalties scale continuously with richness
    excess_mass = sum(_soft_hinge(e.sim_to_seed - CLOSE_MATCH_BASELINE, RICH_MATCH_SOFTNESS)
                      for e in candidates if e.artist_id != seed.artist_id)
    max_excess = 1.0 - CLOSE_MATCH_BASELINE
    density = excess_mass / (max(len(candidates), 1) * RICH_MATCH_FRACTION * max_excess)
    richness = density / (1.0 + density)
    seed_floor = _relax(SEED_FLOOR, SEED_FLOOR_RELAXATION * (1.0 - richness))

    anchors.refresh()
    best_sim = max((e.sim_to_seed for e in candidates), default=0.0)
    scorer = _Scorer(anchors, richness, dsp_weight, best_sim)
    scorer.artist_load = _repeat_load(recent_artists)
    scorer.album_load = _repeat_load(recent_albums)

    # Rank the whole library once to pick the POOL_CAP candidates the loop scores
    remaining = heapq.nlargest(POOL_CAP, candidates, key=scorer.score)

    queue: list[QueueEntry] = []
    for i in range(queue_length):
        # The scorer still reflects the session as of the start of this position
        elapsed_minutes = elapsed_ms / 60000.0
        scorer.seed_weight = (max(seed_floor, 1.0 - max(0.0, elapsed_minutes) / (SEED_DECAY_MINUTES / 2.0))
                              if len(anchors) else 1.0)

        # Penalize the opener's album so the queue doesn't just play one album, since technically it *is* the most similar
        album_holdoff = (_relax(SEED_ALBUM_OPENER_PENALTY, 1.0 - SEED_ALBUM_PENALTY_DECAY ** i)
                         if preseed_album and SEED_ALBUM_OPENER_PENALTY < 1.0 else 1.0)
        scored = []
        for e in remaining:
            s = scorer.score(e)
            if album_holdoff < 1.0 and e.album_id == seed.album_id:
                s *= album_holdoff
            scored.append((s, e))
        if not scored:
            break

        best = max(s for s, _ in scored)
        if best <= 0:
            break
        if i == 0 and (reroll or not len(anchors)):
            # Opener: best track per closest-fitting artist, weighted by
            # score**power -- see the OPENER_* constants.
            best_by_artist: dict[str, tuple[float, QueueEntry]] = {}
            for s, e in sorted(scored, key=lambda c: c[0], reverse=True):
                best_by_artist.setdefault(e.artist_id, (s, e))
            artist_ranked = sorted(best_by_artist.values(), key=lambda c: c[0], reverse=True)
            pool = [c for c in artist_ranked[:OPENER_MAX_ARTISTS] if c[0] >= best * OPENER_RATIO]
            seed_best = best_by_artist.get(seed.artist_id)
            if seed_best is not None and seed_best not in pool:
                pool.append(seed_best)
            weights = [s ** OPENER_WEIGHT_POWER for s, _ in pool]
        else:
            floor = max(best * CONFIDENCE_RATIO, ABSOLUTE_SCORE_FLOOR)
            pool = [(s, e) for s, e in scored if s >= floor]
            if len(pool) > CONFIDENCE_POOL_MAX:
                pool = heapq.nlargest(CONFIDENCE_POOL_MAX, pool, key=lambda c: c[0])
            if not pool:
                # Best candidate is below the absolute floor: nothing left is a
                # real match. Take it outright rather than reopening the relative
                # window to the unrelated tail.
                pool = [max(scored, key=lambda c: c[0])]
            weights = [s for s, _ in pool]
        best_entry = rng.choices(pool, weights=weights, k=1)[0][1]

        queue.append(best_entry)
        remaining.remove(best_entry)

        # The pick becomes the newest, full-weight anchor for the next one.
        anchors.add(best_entry.feat, best_entry.tags, 1.0, RECENCY_DECAY)
        anchors.refresh()
        recent_artists.append(best_entry.artist_id)
        recent_albums.append(best_entry.album_id)
        elapsed_ms += best_entry.duration_ms or 0
        scorer.artist_load = _repeat_load(recent_artists)
        scorer.album_load = _repeat_load(recent_albums)

    return queue, richness