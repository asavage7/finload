"""Audio feature extraction: per-track librosa DSP features (BPM, plus a
timbre vector of 13 MFCC means, 13 MFCC standard deviations, and 7 spectral
contrast means), cached once and reused by the discovery queue builder (see
discovery.py) so no audio decoding happens on the playback hot path.

librosa runs here with numba/llvmlite replaced by a pure-Python shim (see
_numba_shim and _ensure_librosa) to keep ~200 MB of native LLVM out of the
PyInstaller bundle; the shimmed path was validated bit-for-bit identical to
real-numba librosa across the library. The MFCC variance and spectral contrast
were chosen by ablation: variance roughly doubles the timbral signal the mean
alone throws away, and contrast separates clean from distorted texture the
MFCCs blur. Chroma was tested and dropped (it groups by musical key, not by
similarity, and diluted the vector).

Runs as a BackgroundJob like sync/genre_enrichment, but the analysis itself
is CPU-bound, so it fans out across a small multiprocessing pool rather than
running inline in the job's own thread. Two independent throttles keep this
from bogging down whatever else the user's machine is doing at the time:
  - a hard duty-cycle cap per worker (see ``_throttled_analyze``): each
    worker sleeps after every track so its busy time never exceeds
    MAX_CPU_FRACTION_PER_CORE of one core. This is a portable, pure-Python
    substitute for cgroups/cpulimit, which aren't available on Windows/macOS.
  - a lowered scheduler niceness (POSIX only; no-op on Windows) so the OS
    also deprioritizes these workers the instant something else wants the CPU.
"""
import concurrent.futures as cf
import json
import logging
import multiprocessing as mp
import os
import threading
import time
import urllib.request

from core.config import get_data_dir
from core.database import Track, TrackFeatures, db
from services.background import BackgroundJob

logger = logging.getLogger(__name__)

# Bumped when the feature set or extractor changes. A TrackFeatures row whose
# feature_version differs is treated as stale and re-analyzed (see _run), and
# the discovery scorer ignores non-matching rows.
FEATURE_VERSION = 2

# 22050 halves the STFT's sample count (and therefore its cost) versus the
# previous 44100, at the cost of discarding content above ~11kHz -- acceptable
# for a timbre-similarity vector, not an audio-quality signal, and it's
# librosa's own out-of-the-box default rate. N_FFT/HOP are halved alongside it
# (rather than left at their old sample counts) so the STFT's time window
# (~46ms) and hop (~23ms) in *seconds* stay exactly what they were at 44100 --
# isolating the tradeoff to "less frequency range" rather than also
# introducing coarser time resolution as an unplanned side effect.
SR = 22050
N_FFT = 1024
HOP = 512
N_MFCC = 13

MAX_WORKERS = 4
MAX_CPU_FRACTION_PER_CORE = 0.25
DOWNLOAD_WORKERS = 8  # I/O-bound, so this is independent of MAX_WORKERS (CPU-bound analysis)
_DOWNLOAD_TIMEOUT = 30

# Above this many tracks, hubness stats are computed against a random sample
# rather than the full library, keeping the O(N^2) pairwise-distance pass cheap
# even for very large libraries, at the cost of slightly noisier per-track
# median/MAD estimates.
HUBNESS_FULL_PASS_LIMIT = 5000
HUBNESS_SAMPLE_SIZE = 2000

# Scales MAD (median absolute deviation) up to a std-equivalent under
# approximate normality (1 / Phi^-1(0.75)). MAD alone runs ~32% low against a
# true std, which would make discovery.py's _normal_sf survival function read
# every distance as more extreme than it really is, not just a hub's. Kept
# alongside dist_scale rather than folded into the raw MAD so the stored number
# stays on the same footing _normal_sf already expects.
_MAD_TO_STD = 1.4826


def _feature_vector(features: dict) -> list:
    """Concatenate a stored features dict into the single vector the discovery
    scorer standardizes and measures distance over. Kept here (with
    discovery.py's identical loader) so hubness stats describe the same
    distances the scorer sees."""
    return features["mfcc_mean"] + features["mfcc_std"] + features["contrast_mean"]


_librosa = None


def _ensure_librosa():
    """Import librosa with numba replaced by the pure-Python shim, and patch the
    two functions whose numba bodies don't run as plain Python (localmax/localmin
    stencils and beat.__beat_track_dp's range(float) loop). Done once per process;
    pool workers each call it on their first analysis. See _numba_shim for why.

    Imported lazily so importing this module (e.g. at app startup, long before any
    analysis runs) never pays librosa's import cost, and so each pool worker builds
    its own state rather than sharing across a fork.
    """
    global _librosa
    if _librosa is not None:
        return _librosa

    import sys
    import numpy as np

    from core import _numba_shim
    # Must precede the first `import librosa` so librosa's `import numba` resolves
    # to the shim. setdefault (not force) so a real numba, if somehow already
    # imported, still wins — the overrides below make both paths identical anyway.
    sys.modules.setdefault("numba", _numba_shim)

    import librosa
    import librosa.feature.rhythm  # tempo() moved out of librosa.beat in 0.10
    import librosa.util.utils as _lru

    # numpy replacements for the stencil-backed local-extrema finders, matching
    # librosa's documented semantics (first element never an extremum; interior
    # by strict/loose neighbour comparison; last element handled explicitly).
    def _localmax(x, *, axis=0):
        xi = x.swapaxes(-1, axis)
        m = np.empty_like(x, dtype=bool)
        mi = m.swapaxes(-1, axis)
        mi[..., 0] = False
        mi[..., 1:-1] = (xi[..., 1:-1] > xi[..., :-2]) & (xi[..., 1:-1] >= xi[..., 2:])
        mi[..., -1] = xi[..., -1] > xi[..., -2]
        return m

    def _localmin(x, *, axis=0):
        xi = x.swapaxes(-1, axis)
        m = np.empty_like(x, dtype=bool)
        mi = m.swapaxes(-1, axis)
        mi[..., 0] = False
        mi[..., 1:-1] = (xi[..., 1:-1] < xi[..., :-2]) & (xi[..., 1:-1] <= xi[..., 2:])
        mi[..., -1] = xi[..., -1] < xi[..., -2]
        return m

    _lru.localmax = librosa.util.localmax = _localmax
    _lru.localmin = librosa.util.localmin = _localmin

    # Vendored from librosa.beat.__beat_track_dp: numba coerces range(float) to
    # int, plain Python rejects it — cast explicitly. The outer loop is a genuine
    # sequential DP recurrence (each step reads cumscore entries written by earlier
    # steps) and can't be vectorized, but for a fixed i every candidate `loc` in the
    # inner search window reads only already-computed cumscore entries, so that
    # window can be gathered and scored as one numpy op instead of a Python-level
    # inner loop -- this is what numba's JIT was actually buying on this function.
    # locs is built descending (matching the original loop's traversal order) so
    # argmax's first-occurrence tie-break matches the original strict `>` compare.
    # Verified bit-for-bit identical to the pure-Python version across randomized
    # inputs (both constant- and variable-tempo cases) and measured ~5x faster.
    def _beat_track_dp(localscore, frames_per_beat, tightness):
        N = len(localscore)
        backlink = np.zeros(N, dtype=np.int32)
        cumscore = np.zeros(N, dtype=localscore.dtype)
        score_thresh = 0.01 * localscore.max()
        first_beat = True
        backlink[0] = -1
        cumscore[0] = localscore[0]
        tv = int(len(frames_per_beat) > 1)
        for i in range(N):
            score_i = localscore[i]
            fpb = frames_per_beat[tv * i]
            hi = int(i - np.round(fpb / 2))
            lo = max(0, int(i - 2 * fpb - 1) + 1)
            if hi >= lo:
                locs = np.arange(hi, lo - 1, -1)
                scores = cumscore[locs] - tightness * (np.log(i - locs) - np.log(fpb)) ** 2
                best_idx = np.argmax(scores)
                best_score = scores[best_idx]
                beat_location = int(locs[best_idx])
            else:
                best_score = -np.inf
                beat_location = -1
            cumscore[i] = score_i + best_score if beat_location >= 0 else score_i
            if first_beat and score_i < score_thresh:
                backlink[i] = -1
            else:
                backlink[i] = beat_location
                first_beat = False
        return backlink, cumscore

    # Vendored from librosa.beat.__beat_local_score. Its own comment notes the
    # static-tempo branch (len(frames_per_beat) == 1 -- always true for finload,
    # which never passes an explicit per-frame bpm curve) is "essentially a
    # same-mode convolution" of the onset envelope against a Gaussian window;
    # librosa keeps the manual loop only because their minimum numba version
    # doesn't support np.convolve inside a jitted function. We have no such
    # constraint, so we can just call it. The loop's k-range is a half-open
    # Python range, which turns out to exclude a couple of boundary terms a
    # textbook zero-padded convolution would include -- rather than reverse
    # engineer that by hand, the fast convolution covers the interior and the
    # first/last K frames are recomputed with the exact original loop, which
    # is the only part where the two can differ. Verified bit-for-bit identical
    # to the pure-Python version across 30 randomized (length, tempo) trials
    # and measured ~160x faster on the static-tempo path. The dynamic-tempo
    # branch (per-frame tempo curve) isn't reachable from finload's usage, so
    # it keeps the original slow-but-correct loop rather than risk an unverified
    # fast path for code that never runs.
    def _beat_local_score(onset_envelope, frames_per_beat):
        N = len(onset_envelope)
        localscore = np.zeros_like(onset_envelope)

        if len(frames_per_beat) != 1:
            # Dynamic-tempo path: not exercised by finload, keep it correct.
            for i in range(N):
                fpb_i = frames_per_beat[i]
                window = np.exp(-0.5 * (np.arange(-fpb_i, fpb_i + 1) * 32.0 / fpb_i) ** 2)
                K = 2 * int(fpb_i) + 1
                for k in range(max(0, i + K // 2 - N + 1), min(i + K // 2, K)):
                    localscore[i] += window[k] * onset_envelope[i + K // 2 - k]
            return localscore

        fpb = frames_per_beat[0]
        window = np.exp(-0.5 * (np.arange(-fpb, fpb + 1) * 32.0 / fpb) ** 2)
        K = len(window)

        full = np.convolve(window, onset_envelope, mode="full")
        localscore[:] = full[K // 2: K // 2 + N]

        boundary = K
        for i in list(range(0, min(boundary, N))) + list(range(max(0, N - boundary), N)):
            s = 0.0
            for k in range(max(0, i + K // 2 - N + 1), min(i + K // 2, K)):
                s += window[k] * onset_envelope[i + K // 2 - k]
            localscore[i] = s

        return localscore

    # Name-mangled module globals that beat.__beat_tracker looks up dynamically.
    _key = next(k for k in vars(librosa.beat) if k.endswith("beat_track_dp"))
    setattr(librosa.beat, _key, _beat_track_dp)
    _key = next(k for k in vars(librosa.beat) if k.endswith("beat_local_score"))
    setattr(librosa.beat, _key, _beat_local_score)

    _librosa = librosa
    return librosa


def _analyze(path: str) -> dict:
    import numpy as np
    librosa = _ensure_librosa()

    y, sr = librosa.load(path, sr=SR, mono=True)
    if y.size == 0:
        raise ValueError("empty audio")

    # One STFT shared across beat tracking, MFCC, and spectral contrast, which
    # would otherwise each build their own from scratch (three FFT passes over
    # the same audio for no numerical difference). Verified against librosa's
    # source that mfcc/onset_strength/spectral_contrast/melspectrogram all take
    # a caller-supplied S as-is rather than recomputing it internally, and that
    # melspectrogram's default mel-filter norm ('slaney') matches what mfcc's
    # own S=None branch would have used, so this reuse is bit-identical to the
    # three-separate-STFT version, not an approximation of it.
    stft_mag = np.abs(librosa.stft(y, n_fft=N_FFT, hop_length=HOP))
    mel_db = librosa.power_to_db(
        librosa.feature.melspectrogram(S=stft_mag ** 2, sr=sr, n_fft=N_FFT, hop_length=HOP)
    )

    # aggregate=np.median matches beat_track's own (non-default) internal call,
    # so the onset envelope here is identical to the one it would have built.
    onset_env = librosa.onset.onset_strength(S=mel_db, sr=sr, hop_length=HOP, aggregate=np.median)
    # beat_track()[0] would also run its dynamic-programming beat-placement
    # pass to get individual beat locations, which nothing here ever reads --
    # only the tempo scalar (estimated from the onset envelope *before* that
    # DP pass runs) is used. librosa.beat.tempo() gets the same scalar without
    # paying for the unused DP.
    tempo = librosa.feature.rhythm.tempo(onset_envelope=onset_env, sr=sr, hop_length=HOP)

    mfcc = librosa.feature.mfcc(S=mel_db, n_mfcc=N_MFCC)
    contrast = librosa.feature.spectral_contrast(S=stft_mag, sr=sr, n_fft=N_FFT, hop_length=HOP)
    return {
        "bpm": float(np.atleast_1d(tempo)[0]),
        "mfcc_mean": [float(x) for x in mfcc.mean(axis=1)],
        "mfcc_std": [float(x) for x in mfcc.std(axis=1)],
        "contrast_mean": [float(x) for x in contrast.mean(axis=1)],
    }


def _throttled_analyze(path: str) -> dict:
    """Runs ``_analyze`` then sleeps so busy-time / wall-time stays at or
    below MAX_CPU_FRACTION_PER_CORE — e.g. at 0.25, a 2s analysis is followed
    by a 6s sleep, so this worker's core sits at ~25% utilization instead of
    100% for the duration of the scan."""
    t0 = time.monotonic()
    result = _analyze(path)
    busy = time.monotonic() - t0
    idle = busy * (1.0 / MAX_CPU_FRACTION_PER_CORE - 1.0)
    if idle > 0:
        time.sleep(idle)
    return result


def _worker_init():
    try:
        os.nice(15)  # POSIX only; deprioritize so foreground apps win any contention
    except (AttributeError, OSError):
        pass

    # numpy and scipy each vendor their own independent OpenBLAS build, and both
    # default to a thread pool sized to os.cpu_count() -- so a single librosa call
    # can spin up 50-70 BLAS threads. With MAX_WORKERS processes already giving
    # process-level parallelism, that's severe oversubscription (measured ~857%
    # total CPU and heavy contention across 4 concurrent workers on a 24-core
    # machine). Restricting each worker to single-threaded BLAS removes that
    # contention entirely -- measured ~23% faster wall-clock per track *and*
    # under half the total CPU, not a throughput/CPU trade-off in either direction.
    # Must happen before numpy/scipy are first imported in this process (they read
    # these once, at BLAS init time) -- _worker_init runs before any task, and
    # nothing at this module's import time touches numpy, so this is early enough
    # for a fresh worker. The one gap: multiprocessing forks from the main process,
    # so if numpy was already imported and used there before this pool was created
    # (e.g. a prior compute_hubness_stats() call earlier in the same process
    # lifetime), the forked worker inherits an already-initialized BLAS thread pool
    # and these env vars arrive too late to matter for it.
    for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                 "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ[_var] = "1"


def _analyze_one(track_id_and_path: tuple[str, str]) -> tuple[str, dict | None, str | None]:
    track_id, path = track_id_and_path
    try:
        return track_id, _throttled_analyze(path), None
    except Exception as exc:
        return track_id, None, str(exc)


def _temp_audio_dir() -> str:
    path = os.path.join(str(get_data_dir()), "tmp_audio_analysis")
    os.makedirs(path, exist_ok=True)
    return path


def compute_hubness_stats() -> int:
    """Second pass over the cached feature vectors: for each track, computes
    the median/MAD (median absolute deviation) of its distance to the rest of
    the library and caches the result on TrackFeatures (see discovery.py's
    ``_mutual_proximity``, which uses these to tell a genuinely close match
    apart from a "hub" track that sits deceptively close to a huge fraction of
    the library regardless of genre). Median/MAD rather than mean/std: a
    track's real distance distribution is right-skewed, and mean/std let that
    tail inflate the estimated spread enough to under-correct the very hub
    tracks this exists to catch. Pure vector math over data already in the DB
    (no audio decoding), so it's cheap enough to run after every analysis pass.
    Returns the number of tracks updated.

    Standardizes each vector dimension the same way discovery._bulk_load_features
    does, so the stored stats describe the distances the scorer actually sees.
    """
    import numpy as np

    rows = [r for r in TrackFeatures.select(TrackFeatures.track, TrackFeatures.features)
            .where(TrackFeatures.feature_version == FEATURE_VERSION) if r.features]
    if len(rows) < 2:
        return 0

    track_ids = [r.track_id for r in rows]
    vectors = np.array([_feature_vector(json.loads(r.features)) for r in rows])
    std = vectors.std(axis=0)
    std[std < 1e-12] = 1.0
    vectors = (vectors - vectors.mean(axis=0)) / std

    if len(rows) > HUBNESS_FULL_PASS_LIMIT:
        sample_idx = np.random.choice(len(rows), size=HUBNESS_SAMPLE_SIZE, replace=False)
        sample_vectors = vectors[sample_idx]
    else:
        sample_vectors = vectors

    # ||a-b||^2 = ||a||^2 + ||b||^2 - 2 a.b, avoiding an O(N^2) Python loop.
    a_sq = np.sum(vectors ** 2, axis=1, keepdims=True)
    b_sq = np.sum(sample_vectors ** 2, axis=1)
    cross = vectors @ sample_vectors.T
    dist = np.sqrt(np.maximum(a_sq + b_sq - 2 * cross, 0.0))

    with db.atomic():
        for i, track_id in enumerate(track_ids):
            row_dists = dist[i]
            row_dists = row_dists[row_dists > 1e-9]  # exclude self (0 distance)
            if len(row_dists) == 0:
                continue
            median = float(np.median(row_dists))
            mad = float(np.median(np.abs(row_dists - median)))
            TrackFeatures.update(
                dist_center=median,
                dist_scale=max(mad * _MAD_TO_STD, 1e-6),
            ).where(TrackFeatures.track == track_id).execute()

    return len(track_ids)


class AudioFeatureManager(BackgroundJob):
    """Extracts and caches librosa DSP features for every track that lacks
    current-version features. A completed sync feeds this the same way it feeds
    genre_enrichment (see state.py's ``sync.follow_up_jobs``), which calls
    every follow-up job as ``job.start(force=False)`` with no provider arg, so
    the provider is supplied as a getter at construction time instead
    (``lambda: provider``, closing over state.py's module-level name), which
    also survives ``switch_source()`` reassigning it at runtime.
    """

    supports_force = True

    def __init__(self, settings, db_manager, provider_getter):
        super().__init__()
        self._settings = settings
        self.db = db_manager
        self._get_provider = provider_getter

    def start(self, force: bool = False) -> bool:
        if not self._settings.get("enable_radio"):
            return False
        return super().start(force=force)

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------

    def _run(self, force: bool = False) -> None:
        provider = self._get_provider()
        if force:
            tracks = list(Track.select())
        else:
            # Skip only tracks whose cached features are the current version;
            # a FEATURE_VERSION bump re-analyzes everything else.
            current = {tf.track_id for tf in TrackFeatures.select(TrackFeatures.track)
                       .where(TrackFeatures.feature_version == FEATURE_VERSION)}
            tracks = [t for t in Track.select() if t.id not in current]

        self._emit(total=len(tracks), message="Preparing audio analysis…")
        if not tracks:
            self._emit(status="complete", message="No new tracks to analyze")
            return

        n_workers = min(MAX_WORKERS, os.cpu_count() or 1)
        temp_paths: dict[str, str] = {}
        processed = 0
        errors = 0

        with mp.Pool(n_workers, initializer=_worker_init) as pool:
            for track_id, features, error in pool.imap_unordered(
                _analyze_one, self._resolve_paths(tracks, provider, temp_paths)
            ):
                if not self._settings.get("enable_radio"):
                    # Setting turned off mid-run (same gate start() checks) --
                    # stop rather than keep working on a disabled feature.
                    # Leaving the `with mp.Pool` block here still lets it
                    # clean up already-dispatched workers normally.
                    self._emit(status="idle", message="Stopped — disabled in settings")
                    return
                # Note: _resolve_paths submits every remote track's download
                # to its own thread pool up front, not lazily per iteration,
                # so a pause here stops new CPU-bound analysis/DB writes from
                # starting but can't recall downloads already in flight to
                # Jellyfin for the current batch -- still meaningfully less
                # concurrent load than letting the whole batch keep going.
                self.wait_if_paused()
                temp_path = temp_paths.pop(track_id, None)
                if temp_path:
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass

                processed += 1
                if error:
                    errors += 1
                else:
                    self.db.save_track_features(track_id, **features)

                self._emit(processed=processed,
                            message=f"Analyzing audio: {processed}/{len(tracks)}"
                                    + (f" ({errors} failed)" if errors else ""))

        self._emit(message="Computing hubness stats…")
        compute_hubness_stats()

        self._emit(status="complete",
                    message=f"Analyzed {processed - errors}/{len(tracks)} tracks"
                            + (f", {errors} failed" if errors else ""))

    def _resolve_paths(self, tracks: list[Track], provider, temp_paths: dict[str, str]):
        """Yields (track_id, local_path) for the pool. Local-provider tracks
        already have a path on disk and need no download; remote tracks are
        downloaded (as small transcodes, see get_analysis_stream_url) across
        a thread pool run alongside the CPU-bound analysis pool, so slow I/O
        for one track doesn't stall workers that could be analyzing another.
        """
        local, remote = [], []
        for track in tracks:
            (local if track.provider == "local" and track.file_path else remote).append(track)

        for track in local:
            yield track.id, track.file_path

        if not remote:
            return

        with cf.ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
            futures = {pool.submit(self._download, track, provider): track for track in remote}
            for future in cf.as_completed(futures):
                track = futures[future]
                try:
                    local_path = future.result()
                except Exception as exc:
                    logger.warning("Skipping %s: download failed (%s)", track.title, exc)
                    continue
                temp_paths[track.id] = local_path
                yield track.id, local_path

    def _download(self, track: Track, provider) -> str:
        url = provider.get_analysis_stream_url(track.id)
        ext = os.path.splitext(url.split("?")[0])[1] or ".audio"
        dest = os.path.join(_temp_audio_dir(), f"{track.id}.{os.getpid()}-{threading.get_ident()}{ext}")
        with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT) as response, open(dest, "wb") as fh:
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
        return dest
