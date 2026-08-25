"""Anchor-based discovery queue builder.

    score(candidate) = (relevance - skip_repel) * repeat_penalty * fatigue
                        * rating * duration_factor

Relevance blends similarity to the seed with a recency-weighted average over
what the session has actually played, the seed's share decaying toward
SEED_FLOOR* as the session runs. Manual queue adds anchor harder and fade slower
than algorithm picks; a skipped algorithm pick repels similar candidates.
Artist/album repeats, recently played tracks and short tracks are penalized
rather than banned.

Similarity is genre-tag agreement plus DSP closeness. DSP evidence scales with
how much of the library has been analyzed (DSP_FULL_COVERAGE), and a track with
no cached features scores on tags alone rather than dropping out of the pool, so
a part-analyzed library still builds a full queue.

Wired into the live queue by radio.py. Everything above build_queue is a
DB-read-only scoring layer; build_queue decides which candidates get picked.
"""
import datetime
import heapq
import json
import math
import random
from operator import mul

from core.database import (Album, AlbumGenre, Artist, ArtistGenre, Genre, PlayHistory, Track,
                      TrackFeatures, track_scope_clause)

# --- similarity scoring (shared: how two tracks compare) -------------------

ALBUM_TAG_WEIGHT = 1.0     # album tags are release-specific, trusted fully
ARTIST_TAG_WEIGHT = 0.75   # artists span genres their releases don't all share
TAG_EVIDENCE_MAX = 0.6     # ceiling on tag agreement's score contribution
DSP_EVIDENCE_MAX = 0.4     # ceiling on DSP agreement's; a hard cap, so an untagged track
                           # can't out-score corroborated tag evidence on raw DSP
CORROBORATION_BONUS_MAX = 0.15  # bonus when tags and DSP agree independently
CORROBORATION_THRESHOLD = 0.6   # gate both signals clear for that bonus, and the line
                                # below which tag overlap counts as disagreement
DSP_DISAGREEMENT_DISCOUNT = 0.7  # dsp evidence scales toward this fraction of itself as
                                 # well-tagged tracks disagree; untagged pairs untouched
SCORE_CEILING = TAG_EVIDENCE_MAX + DSP_EVIDENCE_MAX + CORROBORATION_BONUS_MAX
DSP_FULL_COVERAGE = 0.9    # analyzed fraction at which DSP evidence carries full weight;
                           # below it, evidence scales linearly with coverage

BPM_DELTA_NORM = 60.0  # divisor mapping a BPM delta (see _bpm_distance) to [0, 1]
VEC_DIST_NORM = 8.0    # fallback distance divisor for a track with no hubness stats yet;
                       # ~sqrt(2 * vector length), typical for two standardized vectors


def _bulk_load_features() -> dict[str, dict]:
    """Feature dict for every track with current-version cached features, in one
    query. Each track's timbre-mean, timbre-variance and spectral-contrast are
    concatenated into one vector, then standardized per dimension across the
    library so no single dimension (raw MFCC[0] energy, say) dominates the
    distance. Matches audio_analysis.compute_hubness_stats, so the stored hubness
    stats describe these same distances. ``sq`` is the vector's squared norm,
    cached for _sq_distance."""
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
        return {}

    matrix = np.asarray(vectors, dtype=float)
    std = matrix.std(axis=0)
    std[std < 1e-12] = 1.0  # a constant dimension centers to zero and carries no signal
    matrix = (matrix - matrix.mean(axis=0)) / std
    norms = (matrix * matrix).sum(axis=1).tolist()

    # The three stored scalars are coerced here: a row written before the hubness
    # columns existed carries NULL, which every comparison below would reject.
    return {
        track_id: {"vec": tuple(vec), "sq": sq, "bpm": bpm or 0.0,
                   "dist_center": dist_center or 0.0, "dist_scale": dist_scale or 0.0}
        for track_id, vec, sq, (bpm, dist_center, dist_scale)
        in zip(track_ids, matrix.tolist(), norms, meta)
    }


def _canon_tag(name: str) -> str:
    """Canonical similarity key for a genre name: lowercased, hyphens and
    underscores folded to single spaces, so "Post-Hardcore" and "Post Hardcore"
    stay one tag."""
    return " ".join(name.lower().replace("-", " ").replace("_", " ").split())


def _normalize_group(rows: list[tuple[str, int]]) -> dict[str, float]:
    """Scales one entity's raw tag weights to [0, 1] against its top tag, square
    rooted so real secondary tags survive rather than being crushed by the top one.

    Curated sources (MusicBrainz/Jellyfin) store weight=0 -- presence, no count --
    so an all-curated group is fully trusted, and curated tags mixed in with
    counted ones scale to 0 and drop out: broad curated genres ("Rock") inflate
    overlap between loosely related tracks.
    """
    if not rows:
        return {}
    max_w = max(w for _, w in rows)
    scaled: dict[str, float] = {}
    for name, w in rows:
        # Punctuation variants canonicalize to one key; keep the stronger reading.
        # A weight-0 tag still lands as a key: it contributes nothing to overlap,
        # but it is evidence the entity is tagged at all (see tag_confidence).
        v = 1.0 if max_w <= 0 else math.sqrt(w / max_w)
        prev = scaled.get(name)
        if prev is None or v > prev:
            scaled[name] = v
    return scaled


def _blend_tags(album_tags: dict[str, float], artist_tags: dict[str, float]) -> dict[str, float]:
    """Per-tag weight in [0, 1] for one track from its album's and artist's
    normalized tag groups. Album tags are release-specific and trusted fully;
    artist tags are scaled by ARTIST_TAG_WEIGHT so they fill gaps in thinly
    tagged albums without outranking the album's own."""
    weights = {name: ALBUM_TAG_WEIGHT * w for name, w in album_tags.items()}
    for name, w in artist_tags.items():
        w *= ARTIST_TAG_WEIGHT
        prev = weights.get(name)
        if prev is None or w > prev:
            weights[name] = w
    return weights


def _bulk_genre_rows(model, entity_field) -> dict[str, list[tuple[str, int]]]:
    """(canonical tag, weight) rows grouped by album or artist id, in one query."""
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
    """Weighted Jaccard (Ruzicka similarity) over tag strengths. Walks both dicts
    directly instead of building a key union -- this runs hundreds of thousands
    of times per queue build, and the set allocations dominated it."""
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
    """Absolute BPM difference, octave-corrected: beat trackers commonly report
    half or double the perceptible tempo (160 BPM detected as 80), so compare
    against the closest octave alignment. Halving either side keeps this
    symmetric -- halving only one made similarity(a, b) != similarity(b, a)."""
    return min(abs(bpm_a - bpm_b), abs(bpm_a - bpm_b / 2), abs(bpm_a / 2 - bpm_b))


def _normal_sf(x: float, mean: float, std: float) -> float:
    """Survival function of Normal(mean, std) at x: P(X > x)."""
    if std <= 1e-9:
        return 0.0 if x >= mean else 1.0
    z = (x - mean) / std
    return 1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2)))


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


def similarity(feat_a: dict | None, feat_b: dict | None, tags_a: dict[str, float],
                tags_b: dict[str, float], dsp_weight: float = 1.0) -> float:
    """Confidence in [0, SCORE_CEILING] that two tracks are related: tag agreement
    (up to TAG_EVIDENCE_MAX, scaled by how many tags back it), DSP closeness (up
    to DSP_EVIDENCE_MAX * dsp_weight), and a bonus when both agree strongly. The
    per-signal ceilings are hard caps, so a track with no genre identity can't
    climb the rankings on DSP alone.

    ``dsp_weight`` scales every DSP-derived term with library analysis coverage.
    Either feat may be None, which scores that pair on tags alone."""
    tag_overlap = _weighted_overlap(tags_a, tags_b)
    tag_confidence = min(len(tags_a), len(tags_b), 4) / 4.0
    score = tag_confidence * tag_overlap * TAG_EVIDENCE_MAX

    if dsp_weight <= 0.0 or feat_a is None or feat_b is None:
        return score

    # Timbre-vector proximity carries the bulk of the DSP signal, so it takes the
    # larger weight; tempo agreement is octave-corrected.
    vec_closeness = _mutual_proximity(math.sqrt(_sq_distance(feat_a, feat_b)), feat_a, feat_b)
    norm_bpm = min(_bpm_distance(feat_a["bpm"], feat_b["bpm"]) / BPM_DELTA_NORM, 1.0)
    dsp_score = 0.7 * vec_closeness + 0.3 * (1.0 - norm_bpm)

    disagreement = (tag_confidence * max(0.0, CORROBORATION_THRESHOLD - tag_overlap)
                    / CORROBORATION_THRESHOLD)
    score += (dsp_score * DSP_EVIDENCE_MAX * dsp_weight
              * (1.0 - DSP_DISAGREEMENT_DISCOUNT * disagreement))

    if tag_overlap > CORROBORATION_THRESHOLD and dsp_score > CORROBORATION_THRESHOLD:
        tag_excess = (tag_overlap - CORROBORATION_THRESHOLD) / (1.0 - CORROBORATION_THRESHOLD)
        dsp_excess = (dsp_score - CORROBORATION_THRESHOLD) / (1.0 - CORROBORATION_THRESHOLD)
        score += CORROBORATION_BONUS_MAX * dsp_weight * min(tag_excess, dsp_excess)
    return score


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
    # Hubness is a property of one track's real distance distribution, not
    # something meaningful to average; build_queue copies the primary seed's in.
    return {"vec": vec, "sq": sum(v * v for v in vec), "bpm": bpm_acc / count,
            "dist_center": 0.0, "dist_scale": 0.0}


def _dsp_weight(analyzed: int, total: int) -> float:
    """DSP evidence is only as trustworthy as the analysis is complete: with a
    fraction of the library analyzed, lean on tags instead of on whichever tracks
    happen to have features."""
    coverage = analyzed / total if total else 0.0
    return min(coverage / DSP_FULL_COVERAGE, 1.0)


# --- entity similarity (how two albums or two artists compare) ---------------
#
# The same scoring as tracks, over profiles aggregated from an entity's tracks.
# This backs the "similar albums"/"similar artists" browse surfaces, which used
# to carry their own genre-overlap ranking; sharing the scorer means tuning it
# once moves both the radio queue and what the detail pages recommend.


class _EntityIndex:
    """Album and artist profiles for one comparison pass, loaded together
    because both come off the same track scan."""

    __slots__ = ("album_feats", "album_tags", "artist_feats", "artist_tags",
                 "album_artist", "dsp_weight")


def _entity_tag_profiles() -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    """Normalized tag weights per album id and per artist id.

    An album's profile is its own tags filled in by its artist's, the same blend
    a track gets. An artist's is their own tags filled in by the peak across
    their albums', so an artist tagged only at the release level still has one.
    """
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
    library. This is the whole cost of a comparison -- ranking against a loaded
    index is trivial -- so a caller making several comparisons should build one
    index and pass it to each, rather than letting each rebuild its own."""
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
    """Entity ids most similar to target_id, best first. Scores every entity that
    has a profile of either kind, so an untagged-but-analyzed album can still
    place; anything scoring zero has no evidence at all and is dropped."""
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
    """Album ids most similar to album_id, best first. exclude_artist_id drops
    that artist's own releases, which a "similar albums" row wants separated from
    a "more by this artist" one."""
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


# --- queue selection ---------------------------------------------------------

SKIP_THRESHOLD = 0.3          # completion fraction below this counts as a skip
RECENCY_DECAY = 0.85          # per-step fade of a played track's anchor weight; the newest
                              # anchor is full weight, so each pick flows into the next
ANCHOR_WEIGHT_FLOOR = 0.02    # anchors holding less than this share of the total weight
                              # drop out of the blend instead of being scored for a
                              # rounding error's worth of influence
SEED_FLOOR = 0.15             # seed's minimum share of the relevance blend, rich neighborhood
SEED_FLOOR_THIN = 0.45        # ...much higher in a thin one, where drifting off the seed
                              # hits a similarity cliff within a few picks
CLOSE_MATCH_BASELINE = 0.5    # similarity above this counts as a genuinely close match: the
                              # line richness's excess mass sums above. Kept separate from
                              # LENIENCY_BASELINE, which must not feed back into richness.
RICH_MASS_TARGET = 8.0        # richness = the seed's summed excess similarity to OTHER
                              # artists over this target, so a small confidently close
                              # cluster reads richer than a pile of so-so matches
SEED_DECAY_MINUTES = 60       # listening time for seed weight to fall 1.0 -> floor
SEED_CLUSTER_RADIUS = 0.5     # multi-track seeds keep only tracks within this similarity
                              # of the most central one (see _coherent_seed)
MANUAL_BOOST = 3.0            # a manually queued track anchors this much harder than an
                              # algorithm pick of the same completion
MANUAL_RECENCY_DECAY = 0.92   # ...and fades far slower than RECENCY_DECAY, steering the
                              # next several picks instead of one; manual adds stack
SKIP_REPEL = 0.4              # push-down from an algorithm-skipped track, scaled by how
                              # similar the candidate is to it
ARTIST_REPEAT_PENALTY = 0.55  # score multiplier per recent same-artist pick, rich
                              # neighborhood: drops a repeat below an unrelated track
ARTIST_REPEAT_PENALTY_THIN = 0.68  # ...eased in a thin neighborhood, but only for a candidate
                              # whose own relevance earns it (ARTIST_QUALITY_SPAN)
ALBUM_REPEAT_PENALTY = 0.50   # same shape per same-album pick, firmer than the artist one:
                              # shared mastering makes same-album tracks read near-identical
                              # on DSP, overstating how distinct they are
ALBUM_REPEAT_PENALTY_THIN = 0.74  # a seed whose only strong matches are its own album should
                              # play several of them before spreading out
LENIENCY_BASELINE = 0.62      # relevance floor at which repeat-penalty leniency starts, above
                              # CLOSE_MATCH_BASELINE because same-album DSP similarity
                              # clusters near 0.8 and would otherwise always clear the gate
ARTIST_QUALITY_SPAN = 0.3     # relevance above LENIENCY_BASELINE needed for full
                              # repeat-penalty leniency; none below the baseline
LENIENCY_SUSTAIN_DECAY = 0.9  # raises leniency to this power per unit of artist load, so
                              # sustained repetition converges back toward the full
                              # penalty rather than crossing it all at once; 1.0 disables
REPEAT_DECAY = 0.8            # repeat load counts past picks by recency, not a window:
                              # the last pick weighs 1, older ones fade by this per step
PRESEED_SEED_ARTIST = False   # seed's own artist may open and recur
PRESEED_SEED_ALBUM = True     # ...but radio shouldn't just replay the seed's own album.
                              # Skipped for compilations (_is_compilation).
SEED_ALBUM_OPENER_PENALTY = 0.15  # extra multiplier on the seed's own album at the opener,
                              # where the ordinary album penalty loses to the timbre
                              # vector's album effect. Multiplicative, so not a ban.
SEED_ALBUM_PENALTY_DECAY = 0.4  # per-pick fade of the exponent above: the penalty is
                              # SEED_ALBUM_OPENER_PENALTY ** (this ** pick_index), full
                              # strength at the opener and nearly gone a few tracks later
CONFIDENCE_RATIO = 0.8        # picks draw from candidates within this fraction of the best
                              # score, weighted toward the top. A hard floor: a fully open
                              # score-weighted draw measurably hurt flow and mean fit.
ABSOLUTE_SCORE_FLOOR = 0.18   # below this a candidate isn't a weak match, it's no match, and
                              # the relative window above would open the draw to the whole
                              # library. build_queue then takes the single best outright.
OPENER_MAX_ARTISTS = 5        # opener pool: the best track of up to this many closest-
                              # fitting artists, the seed's own always included
OPENER_RATIO = 0.7            # ...qualifying only within this fraction of the top score,
                              # so one-real-match seeds don't pad with weak openers
OPENER_WEIGHT_POWER = 2.0     # opener draw weight = score**this: the best fit usually
                              # opens, strong cross-artist fits get a turn across reruns
RATING_NUDGE = 0.1            # per-star score change around a neutral 3 stars
SHORT_TRACK_FULL_S = 100      # score ramps linearly with track length up to this, then
                              # flat: interludes and intros are unlikely picks, not banned
FATIGUE_HALF_LIFE_DAYS = 12.0 # recovery rate for a recently played track
SKIP_FIZZLE = 0.5             # suppression per unit of recency-weighted skip load
SKIP_FATIGUE_DECAY = 0.8      # falloff for that skip recency; same curve as REPEAT_DECAY on
                              # its own timescale (plays across history, not picks in a queue)
POOL_CAP = 250                # candidates the per-pick loop scores, ranked by relevance


def _duration_factor(duration_ms: int | None) -> float:
    """Ramps 0 -> 1.0 over SHORT_TRACK_FULL_S, flat after. Unknown durations
    count as full length so missing data never suppresses a track."""
    if not duration_ms:
        return 1.0
    return min((duration_ms / 1000.0) / SHORT_TRACK_FULL_S, 1.0)


def _coherent_seed(seed_ids: list[str], features_by_id: dict[str, dict],
                    tags_by_id: dict[str, dict[str, float]], dsp_weight: float,
                    radius: float) -> list[str]:
    """Drop outliers from a multi-track seed: keep tracks within ``radius`` of the
    medoid so the target is one coherent cluster, not the average of a varied
    catalogue. A cohesive album keeps every track; a punk-and-acoustic artist
    sample keeps the dominant side."""
    ids = [i for i in seed_ids if i in features_by_id]
    if len(ids) <= 2:
        return ids

    def s(a: str, b: str) -> float:
        return similarity(features_by_id[a], features_by_id[b],
                          tags_by_id.get(a, {}), tags_by_id.get(b, {}), dsp_weight)

    medoid = max(ids, key=lambda a: sum(s(a, b) for b in ids if b != a))
    return [i for i in ids if i == medoid or s(medoid, i) >= radius]


def _is_compilation(album_id: str) -> bool:
    """True when the album's credited artist is a "Various Artists" form, so
    build_queue can skip PRESEED_SEED_ALBUM for it. Trusts curation
    (Album.artist) rather than counting distinct per-track artists, which guest
    features ("... feat. Kellin Quinn") would false-positive."""
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
    """Multi-track seed tag profile: each tag's peak weight across the seed
    tracks, not the mean -- averaging dilutes a distinctive genre tagged on only
    some releases into the generic tags every release shares."""
    blended: dict[str, float] = {}
    for tid in seed_ids:
        for name, w in tags_by_id.get(tid, {}).items():
            prev = blended.get(name)
            if prev is None or w > prev:
                blended[name] = w
    return blended


def _bulk_load_fatigue() -> dict[str, float]:
    """Per-track playback-fatigue multiplier (1.0 = unsuppressed) for every track
    with play history, in one query. Recency suppresses a recently played track,
    recovering over FATIGUE_HALF_LIFE_DAYS; each play below SKIP_THRESHOLD adds
    recency-weighted skip load that multiplies in SKIP_FIZZLE. Tracks absent from
    the result mean 1.0 to the caller."""
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
        fatigue[track_id] = recency * (SKIP_FIZZLE ** skip_load)
    return fatigue


class QueueEntry:
    """One scored candidate. ``sim_to_seed`` and ``skip_repel`` are fixed for a
    whole build and computed once; ``anchor_sims`` caches similarity to each
    anchor by index, since anchors are only ever appended."""

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
    mutable fields between picks; ``scale`` shrinks every threshold compared
    against a raw score in step with the DSP-weighted score ceiling."""

    __slots__ = ("anchors", "artist_load", "album_load", "seed_weight",
                 "richness", "scale", "dsp_weight")

    def __init__(self, anchors: _Anchors, richness: float, scale: float, dsp_weight: float):
        self.anchors = anchors
        self.artist_load: dict[str, float] = {}
        self.album_load: dict[str, float] = {}
        self.seed_weight = 1.0
        self.richness = richness
        self.scale = scale
        self.dsp_weight = dsp_weight

    def score(self, e: QueueEntry) -> float:
        """Full score for one candidate: relevance to the seed blended with
        relevance to where the session has actually gone, less skip repulsion,
        times the repeat/fatigue/rating/duration multipliers."""
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
            scale = self.scale
            quality = min(max(0.0, relevance - LENIENCY_BASELINE * scale)
                          / (ARTIST_QUALITY_SPAN * scale), 1.0)
            leniency = (1.0 - self.richness) * quality * LENIENCY_SUSTAIN_DECAY ** a_load
            artist_penalty = (ARTIST_REPEAT_PENALTY
                              + (ARTIST_REPEAT_PENALTY_THIN - ARTIST_REPEAT_PENALTY) * leniency)
            album_penalty = (ALBUM_REPEAT_PENALTY
                             + (ALBUM_REPEAT_PENALTY_THIN - ALBUM_REPEAT_PENALTY) * leniency)
            repeat_penalty = artist_penalty ** a_load * album_penalty ** b_load
        else:
            repeat_penalty = 1.0

        rating = 1.0 + RATING_NUDGE * (e.rating - 3) if e.rating else 1.0
        return ((relevance - e.skip_repel) * repeat_penalty * e.fatigue * rating
                * _duration_factor(e.duration_ms))


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
    """Build a queue from ``seed_track_id``. Returns ``(entries, richness)``;
    richness 0..1 says how much genuinely strong material surrounds the seed (see
    RICH_MASS_TARGET) -- when it's low the queue already holds tighter to what
    works (SEED_FLOOR_THIN), and a caller can threshold it to warn.

    ``session_context``: track ids already played/queued, oldest first;
    ``feedback`` maps them to completion fractions in [0, 1]; ``manual_ids`` marks
    the hand-queued ones. Together they seed the anchors so a top-up continues the
    real session. ``extra_seed_ids`` blend into the seed to describe an
    album/artist. ``session_elapsed_ms`` floors the seed-decay clock. ``reroll``
    draws the first pick flat over its pool so an explicit re-roll actually
    changes the front of the queue. ``library_ids`` scopes the candidate pool to
    the caller's Jellyfin library selection (see database.track_scope_clause) --
    this module stays state-free, so the caller resolves the setting and passes it
    in.

    Per-track data is bulk-loaded once; the per-pick loop scores at most POOL_CAP
    candidates. Returns an empty queue if the seed has neither cached features nor
    genre tags, since there is then nothing to recommend from.
    """
    if rng is None:
        rng = random.Random()
    seed = Track.get_by_id(seed_track_id)

    # id, artist_id, album_id, rating, duration_ms -- the only columns scoring
    # reads, kept as tuples so a large library skips model hydration entirely.
    query = Track.select(Track.id, Track.artist, Track.album, Track.rating, Track.duration_ms)
    scope = track_scope_clause(library_ids)
    if scope is not None:
        query = query.where(scope)
    rows = list(query.tuples())
    track_by_id = {row[0]: row for row in rows}
    features_by_id = _bulk_load_features()
    tags_by_id = _bulk_track_tags(rows)
    fatigue_by_id = _bulk_load_fatigue()

    # Every threshold compared against a raw score scales with the DSP-weighted
    # ceiling, or a part-analyzed library would read as one huge thin neighborhood.
    dsp_weight = _dsp_weight(sum(1 for row in rows if row[0] in features_by_id), len(rows))
    scale = (TAG_EVIDENCE_MAX
             + dsp_weight * (DSP_EVIDENCE_MAX + CORROBORATION_BONUS_MAX)) / SCORE_CEILING

    seed_feat = features_by_id.get(seed_track_id)
    seed_tags = tags_by_id.get(seed_track_id, {})
    if seed_feat is None and not seed_tags:
        return [], 0.0

    if extra_seed_ids:
        seed_pool = _coherent_seed([seed_track_id, *extra_seed_ids], features_by_id,
                                   tags_by_id, dsp_weight, SEED_CLUSTER_RADIUS * scale)
        blended_feat = _blend_profile(seed_pool, features_by_id)
        if blended_feat is not None:
            if seed_feat is not None:
                # Hubness describes one track's real distance distribution and
                # doesn't average, so the primary seed's carries over.
                blended_feat["dist_center"] = seed_feat["dist_center"]
                blended_feat["dist_scale"] = seed_feat["dist_scale"]
            seed_feat = blended_feat
            # Peak-blended, not the mean, so a distinctive genre survives.
            seed_tags = _blend_seed_tags(seed_pool, tags_by_id)

    if exclude_ids is None:
        exclude_ids = set(session_context or ())
    exclude_ids = exclude_ids | {seed_track_id} | set(extra_seed_ids or ())
    feedback = feedback or {}
    manual_ids = manual_ids or set()

    # Walk the real session history into anchors before scoring anything, so the
    # candidate pool and first pick see where the session has actually gone. The
    # seed's album pre-counts as played unless it's a compilation.
    preseed_album = PRESEED_SEED_ALBUM and not _is_compilation(seed.album_id)
    anchors = _Anchors()
    skips: list[tuple[dict | None, dict[str, float]]] = []
    recent_artists: list[str] = [seed.artist_id] if PRESEED_SEED_ARTIST else []
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
        completion = min(max(feedback.get(ctx_id, 1.0), 0.0), 1.0)
        if completion < SKIP_THRESHOLD:
            # A skipped manual track is a change of mind and stops informing
            # anything; a skipped algorithm track repels similar candidates.
            if ctx_id not in manual_ids:
                skips.append((ctx_feat, ctx_tags))
        else:
            manual = ctx_id in manual_ids
            anchors.add(ctx_feat, ctx_tags, completion * (MANUAL_BOOST if manual else 1.0),
                        MANUAL_RECENCY_DECAY if manual else RECENCY_DECAY)
        recent_artists.append(ctx_row[1])
        recent_albums.append(ctx_row[2])
        elapsed_ms += (ctx_row[4] or 0) * completion
    if session_elapsed_ms is not None:
        elapsed_ms = max(elapsed_ms, session_elapsed_ms)

    # A track with neither features nor tags can never score above zero, so it
    # never reaches the pool.
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

    # Both are fixed for the whole build (the seed profile and the skip set never
    # change), so they are computed once here instead of per pick.
    for e in candidates:
        e.sim_to_seed = similarity(seed_feat, e.feat, seed_tags, e.tags, dsp_weight)
        if skips:
            e.skip_repel = SKIP_REPEL * max(similarity(f, e.feat, t, e.tags, dsp_weight)
                                            for f, t in skips)

    # Seed floor and repeat penalties scale continuously with richness, no hard
    # cutoff; the seed's own artist is excluded (see RICH_MASS_TARGET).
    close_match = CLOSE_MATCH_BASELINE * scale
    excess_mass = sum(e.sim_to_seed - close_match for e in candidates
                      if e.sim_to_seed > close_match and e.artist_id != seed.artist_id)
    richness = min(excess_mass / (RICH_MASS_TARGET * scale), 1.0)
    seed_floor = SEED_FLOOR_THIN + (SEED_FLOOR - SEED_FLOOR_THIN) * richness
    score_floor = ABSOLUTE_SCORE_FLOOR * scale

    anchors.refresh()
    scorer = _Scorer(anchors, richness, scale, dsp_weight)
    scorer.artist_load = _repeat_load(recent_artists)
    scorer.album_load = _repeat_load(recent_albums)

    # Rank the whole library once to pick the POOL_CAP candidates the loop scores
    # -- by full relevance, not seed similarity alone, so tracks that match where
    # the session has drifted survive the cut.
    remaining = heapq.nlargest(POOL_CAP, candidates, key=scorer.score)

    queue: list[QueueEntry] = []
    for i in range(queue_length):
        # The scorer still reflects the session as of the start of this position;
        # it is refreshed at the end of each iteration for the next one.
        elapsed_minutes = elapsed_ms / 60000.0
        scorer.seed_weight = (max(seed_floor, 1.0 - elapsed_minutes / SEED_DECAY_MINUTES)
                              if len(anchors) else 1.0)

        # Aggressive-but-decaying suppression of the seed's own album at the front
        # of the queue: full strength at the opener, fading over the first picks.
        album_holdoff = (SEED_ALBUM_OPENER_PENALTY ** (SEED_ALBUM_PENALTY_DECAY ** i)
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
            floor = max(best * CONFIDENCE_RATIO, score_floor)
            pool = [(s, e) for s, e in scored if s >= floor]
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
