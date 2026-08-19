"""Anchor-based discovery queue builder.

    score(candidate) = (relevance - skip_repel) * repeat_penalty * fatigue
                        * rating * duration_factor

Candidates are scored against real tracks (the seed plus what the session
has actually played). Relevance blends seed
similarity with a recency-weighted average over the played tracks, newest
weighted highest; the seed's share of that blend decays over listening time
toward a floor (SEED_FLOOR*), so a session starts tight and drifts without
ever leaving the seed behind. Manual queue adds anchor harder and fade
slower than algorithm picks (MANUAL_*); a skipped algorithm pick repels
similar candidates (SKIP_REPEL), a skipped manual pick just stops counting.
Artist/album repeats are discouraged, not banned: recency-decayed penalties
that ease only when the neighborhood is thin (richness) AND the candidate
itself is a close match (ARTIST_QUALITY_SPAN). Fatigue fades recently
played and often-skipped tracks, recovering as they age. Short tracks are
derated toward zero, not excluded (SHORT_TRACK_FULL_S).

A multi-track seed (album/artist radio) drops outliers so the target is one
coherent cluster (_coherent_seed) and takes each tag's peak weight across
the sample (_blend_seed_tags). A fresh session's opener draws from the best
track of a few closest-fitting artists (OPENER_*), not the single top score.

Wired into the live queue by radio.py. _bulk_load_features through
_blend_profile is the shared DB-read-only scoring layer; build_queue on top
is the only part that decides which candidates get picked.
"""
import datetime
import json
import math
import random

from database import Album, AlbumGenre, Artist, ArtistGenre, Genre, PlayHistory, Track, TrackFeatures

# --- similarity scoring (shared: how two tracks compare) -------------------

ALBUM_TAG_WEIGHT = 1.0     # specific to this release, trusted fully
ARTIST_TAG_WEIGHT = 0.75   # artists span genres their releases don't all share
TAG_EVIDENCE_MAX = 0.6     # ceiling on tag agreement's score contribution
DSP_EVIDENCE_MAX = 0.4    # ceiling on DSP agreement alone: a hard cap, not a
                           # proportional discount, so an untagged track (corrupted
                           # metadata junk) structurally can't out-score corroborated
                           # tag evidence on raw DSP. Kept close to TAG_EVIDENCE_MAX:
                           # tags can be thin even when present, and DSP is what
                           # actually captures tempo and timbre.
CORROBORATION_BONUS_MAX = 0.15  # tags and DSP agreeing independently beats either alone
CORROBORATION_THRESHOLD = 0.6   # gate both signals must clear for the bonus; doubles as
                                 # the line below which tag overlap counts as disagreement
                                 # for DSP_DISAGREEMENT_DISCOUNT
DSP_DISAGREEMENT_DISCOUNT = 0.7  # mutual proximity can read two mutual outliers as close
                                 # at a vector distance calibrated "different genre";
                                 # tags have no such blind spot, so well-tagged tracks
                                 # that barely overlap are real negative evidence, and
                                 # dsp_evidence scales toward this fraction of itself as
                                 # confidence and disagreement rise. Untagged pairs and
                                 # agreeing pairs are untouched.

BPM_DELTA_NORM = 60.0  # divisor mapping a BPM delta (see _bpm_distance) to [0, 1]
# Fallback divisor for the standardized-vector distance, used only when a track
# has no hubness stats yet (freshly analyzed, before compute_hubness_stats).
# Normally Mutual Proximity normalizes the distance per-track instead. ~sqrt(2 *
# vector length) is the typical distance between two standardized vectors.
VEC_DIST_NORM = 8.0


def _bulk_load_features() -> dict[str, dict]:
    """Feature dict for every track with current-version cached features, in
    one query (per-track round trips were the dominant cost of building a
    queue). Each track's timbre-mean, timbre-variance and spectral-contrast are
    concatenated into one vector, then standardized per dimension across the
    library so no single dimension (raw MFCC[0] energy, say) dominates the
    distance. Standardization happens here rather than in the DB so it stays
    correct as the library grows; it matches audio_analysis.compute_hubness_stats
    so the stored hubness stats describe these same distances."""
    from audio_analysis import FEATURE_VERSION

    raw: dict[str, tuple] = {}
    query = TrackFeatures.select(
        TrackFeatures.track, TrackFeatures.bpm, TrackFeatures.features,
        TrackFeatures.dist_center, TrackFeatures.dist_scale,
    ).where(TrackFeatures.feature_version == FEATURE_VERSION)
    for row in query:
        if not row.features:
            continue
        f = json.loads(row.features)
        vec = f["mfcc_mean"] + f["mfcc_std"] + f["contrast_mean"]
        raw[row.track_id] = (vec, row.bpm, row.dist_center, row.dist_scale)

    if not raw:
        return {}

    dim = len(next(iter(raw.values()))[0])
    n = len(raw)
    means = [0.0] * dim
    for vec, *_ in raw.values():
        for i, v in enumerate(vec):
            means[i] += v
    means = [m / n for m in means]
    sq = [0.0] * dim
    for vec, *_ in raw.values():
        for i, v in enumerate(vec):
            sq[i] += (v - means[i]) ** 2
    inv_std = [1.0 / math.sqrt(s / n) if s > 1e-12 else 0.0 for s in sq]

    features: dict[str, dict] = {}
    for track_id, (vec, bpm, dist_center, dist_scale) in raw.items():
        features[track_id] = {
            "vec": tuple((v - means[i]) * inv_std[i] for i, v in enumerate(vec)),
            "bpm": bpm,
            "dist_center": dist_center,
            "dist_scale": dist_scale,
        }
    return features


def _canon_tag(name: str) -> str:
    """Canonical similarity key for a genre name: lowercased, hyphens and
    underscores folded to single spaces. Sources disagree on punctuation
    ("Post-Hardcore" vs "Post Hardcore", "Hip Hop" vs "Hip-Hop"), and the
    Genre table only dedupes case-insensitively, so without this the same
    genre splits into two tags that dilute every overlap comparison."""
    return " ".join(name.lower().replace("-", " ").replace("_", " ").split())


def _normalize_group(rows: list[tuple[str, int]]) -> dict[str, float]:
    """Scales raw tag weights within one entity's rows to [0, 1] against the
    top tag, square-rooted: a straight w / max_w ratio crushed real secondary
    tags ({"Nu Metal": 1.0, "Rock": 0.67, "Metal": 0.09} left one tag that
    counted for anything in _weighted_overlap); sqrt keeps the ordering but
    pulls the smaller tags up.

    Curated sources (MusicBrainz/Jellyfin) store weight=0 -- presence, no
    count. An all-curated group is fully trusted (every tag 1.0). When
    counted and curated tags mix, the curated ones scale to 0 and drop out.
    Deliberate: counted tags are the more discriminative signal, and
    restoring mixed-in curated tags to full trust measurably hurt relevance
    against an external judge -- broad curated genres ("Rock") inflate
    overlap between loosely related tracks.
    """
    if not rows:
        return {}
    max_w = max(w for _, w in rows)
    scaled: dict[str, float] = {}
    for name, w in rows:
        # Punctuation variants of one genre canonicalize to the same key
        # (see _canon_tag); keep the stronger reading rather than the last.
        v = 1.0 if max_w <= 0 else math.sqrt(w / max_w)
        scaled[name] = max(scaled.get(name, 0.0), v)
    return scaled


def _blend_tags(album_rows: list[tuple[str, int]], artist_rows: list[tuple[str, int]]) -> dict[str, float]:
    """Per-tag weight in [0, 1] from one track's album- and artist-level
    genre rows (see the _bulk_genre_rows_* loaders). Album tags are release-
    specific and trusted fully; artist tags are scaled by ARTIST_TAG_WEIGHT
    since an artist spans genres that don't apply to every release -- they
    fill gaps in thinly tagged albums without outranking the album's own.
    """
    weights: dict[str, float] = {}
    for name, w in _normalize_group(album_rows).items():
        weights[name] = max(weights.get(name, 0.0), ALBUM_TAG_WEIGHT * w)
    for name, w in _normalize_group(artist_rows).items():
        weights[name] = max(weights.get(name, 0.0), ARTIST_TAG_WEIGHT * w)
    return weights


def _bulk_genre_rows_by_album() -> dict[str, list[tuple[str, int]]]:
    rows: dict[str, list[tuple[str, int]]] = {}
    for row in (AlbumGenre.select(AlbumGenre.album, AlbumGenre.weight, Genre.name)
                .join(Genre)):
        rows.setdefault(row.album_id, []).append((_canon_tag(row.genre.name), row.weight))
    return rows


def _bulk_genre_rows_by_artist() -> dict[str, list[tuple[str, int]]]:
    rows: dict[str, list[tuple[str, int]]] = {}
    for row in (ArtistGenre.select(ArtistGenre.artist, ArtistGenre.weight, Genre.name)
                .join(Genre)):
        rows.setdefault(row.artist_id, []).append((_canon_tag(row.genre.name), row.weight))
    return rows


def _weighted_overlap(a: dict[str, float], b: dict[str, float]) -> float:
    """Weighted Jaccard (Ruzicka similarity) over tag strengths. The fused
    min/max loop halves the dict lookups of two separate sum() expressions;
    worth it at tens of thousands of calls per queue build."""
    if not a and not b:
        return 0.0
    smaller, larger = (a, b) if len(a) <= len(b) else (b, a)
    num = 0.0
    den = 0.0
    for k in set(smaller) | set(larger):
        va = smaller.get(k, 0.0)
        vb = larger.get(k, 0.0)
        if va < vb:
            num += va
            den += vb
        else:
            num += vb
            den += va
    return num / den if den else 0.0


def _vec_distance(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _bpm_distance(bpm_a: float, bpm_b: float) -> float:
    """Absolute BPM difference, octave-corrected: beat trackers commonly report
    half or double the perceptible tempo (160 BPM detected as 80), so compare
    against the closest of the direct, doubled, and halved readings."""
    return min(abs(bpm_a - bpm_b), abs(bpm_a - 2 * bpm_b), abs(bpm_a - bpm_b / 2))


def _normal_sf(x: float, mean: float, std: float) -> float:
    """Survival function of Normal(mean, std) at x: P(X > x)."""
    if std <= 1e-9:
        return 0.0 if x >= mean else 1.0
    z = (x - mean) / std
    return 1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2)))


def _mutual_proximity(dist: float, feat_a: dict, feat_b: dict) -> float:
    """Mutual Proximity (Schnitzer et al.): how surprising a raw distance is
    from both tracks' own points of view, MP(a,b) = P(dist > d | a) *
    P(dist > d | b), each against that track's cached distance distribution
    (dist_center/dist_scale, median/MAD, from
    audio_analysis.compute_hubness_stats). This corrects for "hub" tracks
    whose feature vectors sit near the center of the space and read
    deceptively close to a huge fraction of the library. Falls back to plain
    normalized closeness for a track with no hubness stats yet (dist_scale
    == 0, freshly analyzed) so it never divides by zero.
    """
    if feat_a.get("dist_scale", 0.0) <= 1e-9 or feat_b.get("dist_scale", 0.0) <= 1e-9:
        return 1.0 - min(dist / VEC_DIST_NORM, 1.0)
    p_a = _normal_sf(dist, feat_a["dist_center"], feat_a["dist_scale"])
    p_b = _normal_sf(dist, feat_b["dist_center"], feat_b["dist_scale"])
    return p_a * p_b


def _similarity_detail(feat_a: dict, feat_b: dict, tags_a: dict[str, float],
                        tags_b: dict[str, float], detail: bool = True) -> tuple[float, dict | None]:
    """Confidence that two tracks are related, built up from positive
    evidence, each signal under its own hard ceiling: tag agreement (up to
    TAG_EVIDENCE_MAX, scaled by tag_confidence), DSP closeness (up to
    DSP_EVIDENCE_MAX; see that constant for why it's a hard cap), and a
    corroboration bonus when both agree strongly. Discounting a raw blend
    instead let no-identity tracks ("Unknown Artist" files with corrupted
    metadata) climb the rankings on DSP alone across unrelated genres; the
    cap means they structurally can't.

    DSP closeness blends timbre-vector proximity (Mutual Proximity over the
    standardized mfcc-mean/mfcc-std/contrast vector) with octave-corrected
    tempo agreement. The vector carries the bulk of the signal, so it takes
    the larger weight.

    Returns ``(score, detail_dict)`` with every term broken out;
    ``similarity()`` is the score-only wrapper."""
    n_tags = min(len(tags_a), len(tags_b))
    tag_confidence = min(n_tags, 4) / 4.0

    tag_overlap = _weighted_overlap(tags_a, tags_b)

    vec_dist = _vec_distance(feat_a["vec"], feat_b["vec"])
    bpm_delta = _bpm_distance(feat_a["bpm"], feat_b["bpm"])

    vec_closeness = _mutual_proximity(vec_dist, feat_a, feat_b)
    norm_bpm = min(bpm_delta / BPM_DELTA_NORM, 1.0)
    dsp_score = 0.7 * vec_closeness + 0.3 * (1.0 - norm_bpm)

    tag_evidence = tag_confidence * tag_overlap * TAG_EVIDENCE_MAX

    disagreement = tag_confidence * max(0.0, CORROBORATION_THRESHOLD - tag_overlap) / CORROBORATION_THRESHOLD
    dsp_evidence = dsp_score * DSP_EVIDENCE_MAX * (1.0 - DSP_DISAGREEMENT_DISCOUNT * disagreement)

    corroboration = 0.0
    if tag_overlap > CORROBORATION_THRESHOLD and dsp_score > CORROBORATION_THRESHOLD:
        tag_excess = (tag_overlap - CORROBORATION_THRESHOLD) / (1.0 - CORROBORATION_THRESHOLD)
        dsp_excess = (dsp_score - CORROBORATION_THRESHOLD) / (1.0 - CORROBORATION_THRESHOLD)
        corroboration = CORROBORATION_BONUS_MAX * min(tag_excess, dsp_excess)

    score = tag_evidence + dsp_evidence + corroboration

    if not detail:
        return score, None
    return score, {
        "tag_confidence": tag_confidence, "tag_overlap": tag_overlap,
        "tag_evidence": tag_evidence, "dsp_score": dsp_score, "dsp_evidence": dsp_evidence,
        "corroboration": corroboration,
        "vec_dist": vec_dist, "vec_closeness": vec_closeness, "bpm_delta": bpm_delta,
    }


def similarity(feat_a: dict, feat_b: dict, tags_a: dict[str, float], tags_b: dict[str, float]) -> float:
    """Score-only fast path: skips the per-term breakdown dict (thousands
    of calls per queue build)."""
    return _similarity_detail(feat_a, feat_b, tags_a, tags_b, detail=False)[0]


def _blend_profile(weighted_track_ids: dict[str, float], features_by_id: dict[str, dict],
                    tags_by_id: dict[str, dict[str, float]]) -> tuple[dict | None, dict[str, float]]:
    """Weighted blend of N tracks' features + tags into one synthetic
    feat/tags pair (build_queue's extra_seed_ids). Returns ``(None, {})``
    if none of the track_ids have cached features."""
    vec_acc: list[float] | None = None
    bpm_acc = total_weight = 0.0
    tag_acc: dict[str, float] = {}
    for track_id, weight in weighted_track_ids.items():
        feat = features_by_id.get(track_id)
        if feat is None:
            continue
        if vec_acc is None:
            vec_acc = [0.0] * len(feat["vec"])
        for i, v in enumerate(feat["vec"]):
            vec_acc[i] += v * weight
        bpm_acc += feat["bpm"] * weight
        for name, w in tags_by_id.get(track_id, {}).items():
            tag_acc[name] = tag_acc.get(name, 0.0) + w * weight
        total_weight += weight

    if vec_acc is None or total_weight <= 0:
        return None, {}

    blended_feat = {
        "vec": tuple(v / total_weight for v in vec_acc),
        "bpm": bpm_acc / total_weight,
        "dist_center": 0.0, "dist_scale": 0.0,  # a synthetic blend is never itself
                                                 # the subject of a hubness lookup
    }
    blended_tags = {name: min(w / total_weight, 1.0) for name, w in tag_acc.items()}
    return blended_feat, blended_tags


# --- queue selection ---------------------------------------------------------

SKIP_THRESHOLD = 0.3          # completion fraction below this counts as a skip
RECENCY_DECAY = 0.85          # per-step fade of a played track's anchor weight; the newest
                              # anchor is full weight, so each pick flows into the next
SEED_FLOOR = 0.15             # seed's minimum share of the relevance blend, rich neighborhood
SEED_FLOOR_THIN = 0.45        # ...much higher in a thin one: with few real neighbors the
                              # queue hits a similarity cliff fast, and holding to the seed
                              # beats drifting into poor matches
CLOSE_MATCH_BASELINE = 0.5    # similarity above this counts as a genuinely close match --
                              # the line richness's excess mass sums above. The repeat-
                              # penalty leniency gate is deliberately NOT tied to this
                              # (see LENIENCY_BASELINE): a higher leniency floor must not
                              # feed back into richness, or raising it would read every
                              # neighborhood as thinner and ease penalties instead of
                              # tightening them.
RICH_MASS_TARGET = 8.0        # richness = the seed's summed excess similarity to OTHER
                              # artists, relative to this target. Excess mass rather than a
                              # neighbor count, so a small but confidently close cluster
                              # reads richer than a pile of so-so matches. The seed's own
                              # artist is excluded: a deep, internally consistent catalogue
                              # would otherwise read as "rich" in exactly the isolated case
                              # the *_THIN constants exist to catch
SEED_DECAY_MINUTES = 60       # listening time for seed weight to fall 1.0 -> floor;
                              # targets a 30-45min session before the queue leans mostly
                              # on where it has actually gone
SEED_CLUSTER_RADIUS = 0.5     # multi-track seeds keep only tracks within this similarity
                              # of the most central one (see _coherent_seed)
MANUAL_BOOST = 3.0            # a manually queued track anchors this much harder than an
                              # algorithm pick of the same completion
MANUAL_RECENCY_DECAY = 0.92   # ...and fades far slower than RECENCY_DECAY, steering the
                              # next several picks instead of one; manual adds stack
SKIP_REPEL = 0.4              # push-down from an algorithm-skipped track, scaled by how
                              # similar the candidate is to it
ARTIST_REPEAT_PENALTY = 0.55  # score multiplier per recent same-artist pick, rich
                              # neighborhood: drops a repeat below an unrelated track's score.
                              # Tightened from 0.6 when the timbre-variance/contrast vector
                              # landed: it ranks same-artist tracks noticeably closer, so a
                              # firmer penalty holds queue diversity to the pre-change level.
ARTIST_REPEAT_PENALTY_THIN = 0.68  # ...eased in a thin neighborhood, but only for a candidate
                              # that earns it via its own relevance (ARTIST_QUALITY_SPAN):
                              # when only a couple of artists genuinely fit, lean on them
                              # rather than reach for a poor fit by track three, while a
                              # mediocre candidate that merely looks best because everything
                              # nearby is weak earns no ease
ALBUM_REPEAT_PENALTY = 0.50   # same shape per same-album pick; a repeated album is more
                              # redundant than a repeated artist. Firmer than the artist
                              # penalty because the librosa timbre vector's album effect is
                              # strongest within a single release (shared mastering makes
                              # same-album tracks read near-identical on DSP), so same-album
                              # relevance overstates musical distinctness the most.
ALBUM_REPEAT_PENALTY_THIN = 0.74  # a seed whose only strong matches are its own album should
                              # play several of them before spreading out
LENIENCY_BASELINE = 0.62      # relevance floor at which repeat-penalty leniency starts,
                              # decoupled from CLOSE_MATCH_BASELINE (which also drives
                              # richness -- raising that to gate leniency would perversely
                              # read every neighborhood as thinner and ease penalties more).
                              # Anchored above CLOSE_MATCH_BASELINE because the librosa
                              # timbre vector carries a strong album effect (same-album
                              # DSP similarity clusters near 0.8, well above the 0.5 a
                              # genuine cross-artist match reaches): without lifting the
                              # floor, essentially every same-album/same-artist candidate
                              # clears the old 0.5 gate and the leniency fires far more
                              # freely than it did on essentia's flatter relevance scale.
ARTIST_QUALITY_SPAN = 0.3     # relevance above LENIENCY_BASELINE needed for full
                              # repeat-penalty leniency; none below the baseline
LENIENCY_SUSTAIN_DECAY = 0.9  # raises leniency to this power per unit of artist load, so
                              # sustained repetition converges the penalty back toward
                              # ARTIST_REPEAT_PENALTY instead of staying gentle and then
                              # crossing the next-best alternative all at once; 1.0 disables.
                              # Gentlest value that let an isolated seed reclaim the mix
                              # after a forced similarity gap, with no regressions across
                              # the richness range
REPEAT_DECAY = 0.8            # repeat load counts past picks by recency, not a window:
                              # the last pick weighs 1, older ones fade by this per step
PRESEED_SEED_ARTIST = False   # seed's own artist may open and recur
PRESEED_SEED_ALBUM = True     # ...but radio shouldn't just replay the seed's own album.
                              # Skipped for compilations (_bulk_compilation_albums), where
                              # same album doesn't mean same act
SEED_ALBUM_OPENER_PENALTY = 0.15  # extra multiplier on the seed's OWN album at the opener,
                              # on top of the ordinary album repeat penalty. The album
                              # penalty alone can't overcome the librosa timbre vector's
                              # album effect (same-album DSP similarity clusters near 0.8,
                              # above the ~0.5 the best genuine cross-artist match reaches in
                              # a thin library), so a same-album track surfaced in slot 1-2
                              # about half the time -- radio handing back the very album it
                              # started from. Aggressive but not a ban: it decays over the
                              # first picks (SEED_ALBUM_PENALTY_DECAY) and stays multiplicative,
                              # so when genuinely nothing else fits the seed album can still
                              # win. Same compilation exception as PRESEED_SEED_ALBUM.
SEED_ALBUM_PENALTY_DECAY = 0.4  # per-pick geometric fade of the exponent above: the penalty
                              # is SEED_ALBUM_OPENER_PENALTY ** (this ** pick_index), so full
                              # strength at the opener and nearly gone within a few tracks,
                              # by when the queue has moved on and the ordinary penalty
                              # suffices. 1.0 would hold it flat forever; 0 confines it to the
                              # opener alone.
CONFIDENCE_RATIO = 0.8        # picks draw from candidates within this fraction of the best
                              # score, weighted toward the top. A hard floor, deliberately:
                              # a fully open score-weighted draw measurably hurt flow and
                              # mean fit without improving the worst pick -- the floor
                              # concentrates probability mass on close matches instead of
                              # diluting it across the long tail
ABSOLUTE_SCORE_FLOOR = 0.18   # below this a candidate isn't a weak match, it's no match.
                              # The relative window can't tell "the best available is a
                              # repeat-penalized own-artist track" from "everything left is
                              # unrelated"; once penalties push the best near the noise
                              # floor, 80% of it would open the draw to the whole library
                              # and one unrelated winner drags the session into its own
                              # neighborhood (see _score's session_rel). When nothing clears
                              # this floor, build_queue takes the single best candidate
                              # outright rather than widening the draw
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
SKIP_FATIGUE_DECAY = 0.8      # falloff for that skip recency; same curve as REPEAT_DECAY
                              # but its own timescale (plays across history, not picks
                              # within one queue), so the two tune independently
POOL_CAP = 250                # candidates the per-pick loop scores, ranked by relevance


def _duration_factor(duration_ms: int | None) -> float:
    """Ramps 0 -> 1.0 over SHORT_TRACK_FULL_S, flat after. Unknown durations
    count as full length so missing data never suppresses a track."""
    if not duration_ms:
        return 1.0
    return min((duration_ms / 1000.0) / SHORT_TRACK_FULL_S, 1.0)


def _coherent_seed(seed_ids: list[str], features_by_id: dict[str, dict],
                    tags_by_id: dict[str, dict[str, float]]) -> list[str]:
    """Drop outliers from a multi-track seed: keep tracks within
    SEED_CLUSTER_RADIUS of the medoid so the target is one coherent cluster,
    not the average of a varied catalogue. A cohesive album keeps every
    track; a punk-and-acoustic artist sample keeps the dominant side."""
    ids = [i for i in seed_ids if i in features_by_id]
    if len(ids) <= 2:
        return ids

    def s(a: str, b: str) -> float:
        return similarity(features_by_id[a], features_by_id[b],
                          tags_by_id.get(a, {}), tags_by_id.get(b, {}))

    medoid = max(ids, key=lambda a: sum(s(a, b) for b in ids if b != a))
    return [i for i in ids if i == medoid or s(medoid, i) >= SEED_CLUSTER_RADIUS]


def _bulk_compilation_albums() -> set[str]:
    """Album ids whose album-artist credit is a "Various Artists" form, so
    build_queue can skip PRESEED_SEED_ALBUM for them ("same album" on a
    compilation doesn't mean "same act"). Trusts curation (Album.artist)
    rather than counting distinct per-track artists, which guest features
    ("... feat. Kellin Quinn") would false-positive."""
    return {row.id for row in Album.select(Album.id, Artist.name).join(Artist)
            if "various artist" in row.artist.name.lower()}


def _repeat_load(recent: list[str]) -> dict[str, float]:
    """Recency-decayed tally per id: the last entry contributes 1.0, older
    ones fade by REPEAT_DECAY per step and sum."""
    load: dict[str, float] = {}
    n = len(recent)
    for idx, key in enumerate(recent):
        load[key] = load.get(key, 0.0) + REPEAT_DECAY ** (n - 1 - idx)
    return load


def _blend_seed_tags(seed_ids: list[str], tags_by_id: dict[str, dict[str, float]]) -> dict[str, float]:
    """Multi-track seed tag profile: each tag's peak weight across the seed
    tracks, not the mean -- averaging dilutes a distinctive genre tagged on
    only some releases into the generic tags every release shares."""
    blended: dict[str, float] = {}
    for tid in seed_ids:
        for name, w in tags_by_id.get(tid, {}).items():
            blended[name] = max(blended.get(name, 0.0), w)
    return blended


def _bulk_load_fatigue() -> dict[str, float]:
    """Per-track playback-fatigue multiplier (1.0 = unsuppressed) for every
    track with play history, in one query. Recency suppresses a recently
    played track, recovering over FATIGUE_HALF_LIFE_DAYS; each play below
    SKIP_THRESHOLD adds recency-weighted skip load that multiplies in
    SKIP_FIZZLE. Tracks absent from the result mean 1.0 to the caller."""
    plays: dict[str, list[tuple[datetime.datetime, float]]] = {}
    query = (PlayHistory
             .select(PlayHistory.track, PlayHistory.played_at, PlayHistory.completion_pct)
             .where(PlayHistory.in_progress == False)
             .order_by(PlayHistory.played_at))
    for row in query:
        plays.setdefault(row.track_id, []).append((row.played_at, row.completion_pct))

    now = datetime.datetime.now()
    fatigue: dict[str, float] = {}
    for track_id, history in plays.items():
        played_at, _ = history[-1]
        days = (now - played_at).total_seconds() / 86400.0
        recency = 1.0 - math.exp(-days / FATIGUE_HALF_LIFE_DAYS)
        n = len(history)
        skip_load = sum(SKIP_FATIGUE_DECAY ** (n - 1 - idx)
                        for idx, (_, pct) in enumerate(history)
                        if pct / 100.0 < SKIP_THRESHOLD)
        fatigue[track_id] = recency * (SKIP_FIZZLE ** skip_load)
    return fatigue


class QueueEntry:
    __slots__ = ("track", "feat", "tags", "fatigue", "sim_to_seed",
                 "seed_rel", "session_rel", "relevance", "skip_repel",
                 "repeat_penalty", "score", "seed_weight", "elapsed_ms")

    def __init__(self, track, feat, tags, fatigue):
        self.track = track
        self.feat = feat
        self.tags = tags
        self.fatigue = fatigue
        # Filled in at scoring time; None until a candidate is actually scored.
        self.sim_to_seed = self.seed_rel = self.session_rel = self.relevance = None
        self.skip_repel = self.repeat_penalty = self.score = None
        self.seed_weight = self.elapsed_ms = None


def _score(e: QueueEntry, seed_feat: dict, seed_tags: dict[str, float], seed_weight: float,
            anchors: list[tuple[dict, dict[str, float], float]], total_weight: float,
            skips: list[tuple[dict, dict[str, float]]],
            artist_load: dict[str, float], album_load: dict[str, float],
            richness: float = 1.0, detail: bool = False) -> float:
    """Full score for one candidate at the current point in the session.

    ``anchors``: (feat, tags, weight) for the session's played tracks,
    weight already folding recency, completion and manual boost;
    ``total_weight`` is their sum. ``skips``: (feat, tags) of algorithm-
    skipped tracks. ``artist_load``/``album_load``: recency-decayed repeat
    tallies (_repeat_load). ``richness`` gates how much a thin neighborhood
    *can* ease the repeat penalty; the candidate's own relevance
    (ARTIST_QUALITY_SPAN) decides whether it actually does."""
    seed_rel = similarity(seed_feat, e.feat, seed_tags, e.tags)
    if anchors and total_weight > 0:
        session_rel = sum(w * similarity(f, e.feat, t, e.tags)
                          for f, t, w in anchors) / total_weight
        relevance = seed_weight * seed_rel + (1.0 - seed_weight) * session_rel
    else:
        session_rel = seed_rel
        relevance = seed_rel

    skip_repel = SKIP_REPEL * max((similarity(f, e.feat, t, e.tags) for f, t in skips),
                                  default=0.0)

    a_load = artist_load.get(e.track.artist_id, 0.0)
    b_load = album_load.get(e.track.album_id, 0.0)

    quality = min(max(0.0, relevance - LENIENCY_BASELINE) / ARTIST_QUALITY_SPAN, 1.0)
    leniency = (1.0 - richness) * quality * LENIENCY_SUSTAIN_DECAY ** a_load
    artist_penalty = ARTIST_REPEAT_PENALTY + (ARTIST_REPEAT_PENALTY_THIN - ARTIST_REPEAT_PENALTY) * leniency
    album_penalty = ALBUM_REPEAT_PENALTY + (ALBUM_REPEAT_PENALTY_THIN - ALBUM_REPEAT_PENALTY) * leniency

    repeat_penalty = artist_penalty ** a_load * album_penalty ** b_load

    rating = 1.0 + RATING_NUDGE * (e.track.rating - 3) if e.track.rating else 1.0
    score = ((relevance - skip_repel) * repeat_penalty * e.fatigue * rating
             * _duration_factor(e.track.duration_ms))

    if detail:
        e.seed_rel, e.session_rel, e.relevance = seed_rel, session_rel, relevance
        e.skip_repel, e.repeat_penalty, e.score = skip_repel, repeat_penalty, score
    return score


def build_queue(seed_track_id: str, queue_length: int = 20,
                 rng: random.Random | None = None,
                 session_context: list[str] | None = None,
                 exclude_ids: set[str] | None = None,
                 extra_seed_ids: list[str] | None = None,
                 feedback: dict[str, float] | None = None,
                 manual_ids: set[str] | None = None,
                 session_elapsed_ms: float | None = None,
                 reroll: bool = False) -> tuple[list[QueueEntry], float]:
    """Build a queue from ``seed_track_id``. Returns ``(entries, richness)``;
    richness 0..1 says how much genuinely strong material surrounds the seed
    (see RICH_MASS_TARGET) -- when it's low the queue already holds tighter
    to what works (SEED_FLOOR_THIN), and a caller can threshold it to warn.

    ``session_context``: track ids already played/queued, oldest first;
    ``feedback`` maps them to completion fractions in [0, 1]; ``manual_ids``
    marks the hand-queued ones. Together they seed the anchors so a top-up
    continues the real session. ``extra_seed_ids`` blend into the seed to
    describe an album/artist. ``session_elapsed_ms`` floors the seed-decay
    clock. ``reroll`` draws the first pick flat over its pool so an explicit
    re-roll actually changes the front of the queue.

    Per-track data is bulk-loaded once; the per-pick loop scores at most
    POOL_CAP candidates."""
    if rng is None:
        rng = random.Random()
    seed = Track.get_by_id(seed_track_id)

    all_tracks = list(Track.select())
    track_by_id = {t.id: t for t in all_tracks}
    features_by_id = _bulk_load_features()
    rows_by_album = _bulk_genre_rows_by_album()
    rows_by_artist = _bulk_genre_rows_by_artist()
    tags_by_id = {t.id: _blend_tags(rows_by_album.get(t.album_id, []),
                                    rows_by_artist.get(t.artist_id, []))
                  for t in all_tracks}
    fatigue_by_id = _bulk_load_fatigue()

    seed_feat = features_by_id.get(seed_track_id)
    if seed_feat is None:
        raise ValueError(f"No cached audio features for seed track {seed_track_id!r}. "
                          f"Run the audio_features job first.")
    seed_tags = tags_by_id.get(seed_track_id, {})

    if extra_seed_ids:
        seed_pool = _coherent_seed([seed_track_id, *extra_seed_ids], features_by_id, tags_by_id)
        blended_feat, _ = _blend_profile({tid: 1.0 for tid in seed_pool}, features_by_id, tags_by_id)
        if blended_feat is not None:
            # Hubness is a property of one track's real distance distribution,
            # not something meaningful to average, so keep the primary seed's.
            blended_feat["dist_center"] = seed_feat["dist_center"]
            blended_feat["dist_scale"] = seed_feat["dist_scale"]
            # Peak-blend the tags (not the averaged ones _blend_profile returns)
            # so the seed's distinctive genre survives (see _blend_seed_tags).
            seed_feat = blended_feat
            seed_tags = _blend_seed_tags(seed_pool, tags_by_id)

    if exclude_ids is None:
        exclude_ids = set(session_context or ())
    exclude_ids = exclude_ids | {seed_track_id} | set(extra_seed_ids or ())
    feedback = feedback or {}
    manual_ids = manual_ids or set()

    # Walk the real session history into anchors before scoring anything, so
    # the candidate pool and first pick see where the session has actually
    # gone. The seed's album pre-counts as played (PRESEED_SEED_ALBUM)
    # unless it's a compilation.
    preseed_album = PRESEED_SEED_ALBUM and seed.album_id not in _bulk_compilation_albums()
    anchors: list[tuple[dict, dict[str, float], float, float]] = []  # (feat, tags, weight, decay)
    skips: list[tuple[dict, dict[str, float]]] = []
    recent_artists: list[str] = [seed.artist_id] if PRESEED_SEED_ARTIST else []
    recent_albums: list[str] = [seed.album_id] if preseed_album else []
    elapsed_ms = seed.duration_ms
    for ctx_id in (session_context or ()):
        ctx_track = track_by_id.get(ctx_id)
        ctx_feat = features_by_id.get(ctx_id)
        if ctx_track is None or ctx_feat is None:
            continue
        ctx_tags = tags_by_id.get(ctx_id, {})
        completion = min(max(feedback.get(ctx_id, 1.0), 0.0), 1.0)
        if completion < SKIP_THRESHOLD:
            # A skipped manual track is a change of mind and stops informing
            # anything; a skipped algorithm track repels similar candidates.
            if ctx_id not in manual_ids:
                skips.append((ctx_feat, ctx_tags))
        else:
            manual = ctx_id in manual_ids
            base = completion * (MANUAL_BOOST if manual else 1.0)
            decay = MANUAL_RECENCY_DECAY if manual else RECENCY_DECAY
            anchors.append((ctx_feat, ctx_tags, base, decay))
        recent_artists.append(ctx_track.artist_id)
        recent_albums.append(ctx_track.album_id)
        elapsed_ms += ctx_track.duration_ms * completion
    if session_elapsed_ms is not None:
        elapsed_ms = max(elapsed_ms, session_elapsed_ms)

    candidates = [QueueEntry(t, features_by_id[t.id], tags_by_id.get(t.id, {}),
                             fatigue_by_id.get(t.id, 1.0))
                  for t in all_tracks
                  if t.id not in exclude_ids and t.id in features_by_id]
    for e in candidates:
        e.sim_to_seed = similarity(seed_feat, e.feat, seed_tags, e.tags)

    # Seed floor and repeat penalties scale continuously with richness, no
    # hard cutoff; the seed's own artist is excluded (see RICH_MASS_TARGET).
    excess_mass = sum(max(0.0, e.sim_to_seed - CLOSE_MATCH_BASELINE) for e in candidates
                       if e.track.artist_id != seed.artist_id)
    richness = min(excess_mass / RICH_MASS_TARGET, 1.0)
    seed_floor = SEED_FLOOR_THIN + (SEED_FLOOR - SEED_FLOOR_THIN) * richness

    def recency_weights(n: int) -> tuple[list[float], float]:
        """Fold each anchor's base weight together with its own recency decay:
        the most recent anchor keeps its full base weight, older ones fall off
        by their per-anchor rate per step (manual adds fade slower)."""
        weights = [base * decay ** (n - 1 - i)
                   for i, (_, _, base, decay) in enumerate(anchors)]
        return weights, sum(weights) or 1.0

    # Rank the whole library once to pick the POOL_CAP candidates the loop
    # scores -- by full relevance, not seed similarity alone, so tracks that
    # match where the session has drifted survive the cut.
    rw, total_rw = recency_weights(len(anchors))
    weighted_anchors = [(f, t, w) for (f, t, _, _), w in zip(anchors, rw)]
    artist_load, album_load = _repeat_load(recent_artists), _repeat_load(recent_albums)
    ranked = sorted(
        candidates,
        key=lambda e: _score(e, seed_feat, seed_tags, 1.0, weighted_anchors, total_rw,
                             skips, artist_load, album_load, richness),
        reverse=True,
    )
    remaining = ranked[:POOL_CAP]

    queue: list[QueueEntry] = []
    for i in range(queue_length):
        # weighted_anchors/artist_load/album_load reflect the session as of the
        # start of this position -- computed above the loop for i=0, then
        # refreshed at the end of each iteration below for the next one.
        elapsed_minutes = elapsed_ms / 60000.0
        seed_weight = max(seed_floor, 1.0 - elapsed_minutes / SEED_DECAY_MINUTES) if anchors else 1.0

        # Aggressive-but-decaying suppression of the seed's own album at the
        # front of the queue (see SEED_ALBUM_OPENER_PENALTY): full strength at
        # the opener, fading over the first picks. Multiplicative, so it only
        # loses when nothing else genuinely fits.
        album_holdoff = (SEED_ALBUM_OPENER_PENALTY ** (SEED_ALBUM_PENALTY_DECAY ** i)
                         if preseed_album and SEED_ALBUM_OPENER_PENALTY < 1.0 else 1.0)

        scored = []
        for e in remaining:
            s = _score(e, seed_feat, seed_tags, seed_weight, weighted_anchors, total_rw,
                       skips, artist_load, album_load, richness, detail=True)
            if album_holdoff < 1.0 and e.track.album_id == seed.album_id:
                s *= album_holdoff
            scored.append((s, e))
        if not scored:
            break

        best = max(s for s, _ in scored)
        if best <= 0:
            break
        if i == 0 and (reroll or not anchors):
            # Opener: best track per closest-fitting artist, weighted by
            # score**power -- see the OPENER_* constants.
            best_by_artist: dict[str, tuple[float, QueueEntry]] = {}
            for s, e in sorted(scored, key=lambda c: c[0], reverse=True):
                best_by_artist.setdefault(e.track.artist_id, (s, e))
            artist_ranked = sorted(best_by_artist.values(), key=lambda c: c[0], reverse=True)
            pool = [c for c in artist_ranked[:OPENER_MAX_ARTISTS] if c[0] >= best * OPENER_RATIO]
            seed_best = best_by_artist.get(seed.artist_id)
            if seed_best is not None and seed_best not in pool:
                pool.append(seed_best)
            weights = [s ** OPENER_WEIGHT_POWER for s, _ in pool]
        else:
            floor = max(best * CONFIDENCE_RATIO, ABSOLUTE_SCORE_FLOOR)
            pool = [(s, e) for s, e in scored if s >= floor]
            if not pool:
                # Best candidate is below ABSOLUTE_SCORE_FLOOR: nothing left
                # is a real match. Take it outright rather than reopening the
                # relative window to the unrelated tail.
                pool = [max(scored, key=lambda c: c[0])]
            pool.sort(key=lambda c: c[0], reverse=True)
            weights = [s for s, _ in pool]
        best_entry = rng.choices(pool, weights=weights, k=1)[0][1]

        best_entry.seed_weight = seed_weight
        best_entry.elapsed_ms = elapsed_ms
        queue.append(best_entry)
        remaining.remove(best_entry)

        # The pick becomes the newest, full-weight anchor for the next one.
        anchors.append((best_entry.feat, best_entry.tags, 1.0, RECENCY_DECAY))
        recent_artists.append(best_entry.track.artist_id)
        recent_albums.append(best_entry.track.album_id)
        elapsed_ms += best_entry.track.duration_ms

        rw, total_rw = recency_weights(len(anchors))
        weighted_anchors = [(f, t, w) for (f, t, _, _), w in zip(anchors, rw)]
        artist_load, album_load = _repeat_load(recent_artists), _repeat_load(recent_albums)

    return queue, richness
