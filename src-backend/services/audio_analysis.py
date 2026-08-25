"""Audio feature extraction: per-track librosa DSP features (BPM, plus a timbre
vector of 13 MFCC means, 13 MFCC standard deviations, and 7 spectral contrast
means), cached once and reused by the discovery queue builder (see discovery.py)
so no audio decoding happens on the playback hot path.

librosa runs here with numba/llvmlite replaced by a pure-Python shim (see
_numba_shim and _ensure_librosa) to keep ~200 MB of native LLVM out of the
PyInstaller bundle. The MFCC variance and spectral contrast were chosen by
ablation; chroma was tested and dropped.

AudioFeatureManager runs the library-wide pass as a BackgroundJob, fanning the
CPU-bound analysis across a multiprocessing pool that is throttled two ways so
it stays out of the user's way: a hard duty-cycle cap per worker
(_throttled_analyze) and a lowered scheduler niceness (POSIX only). Both, plus
the worker count, come from settings and are read only when a pool starts -- a
Pool can't be resized in place -- so a change mid-run asks _run to tear the pool
down and start a fresh pass. That is lossless: the un-analyzed-tracks query
excludes whatever the previous pass already wrote.

ensure_features is the foreground counterpart, for the handful of tracks radio
needs right now (see PlaybackManager.start_radio). It runs unthrottled in the
caller's thread rather than through the pool, since someone is waiting on it.
"""
import concurrent.futures as cf
import json
import logging
import multiprocessing as mp
import os
import threading
import time
import urllib.error
import urllib.request

from core.config import get_data_dir
from core.database import Track, TrackFeatures
from services.background import BackgroundJob


logger = logging.getLogger(__name__)

# Bumped when the feature set or extractor changes: a TrackFeatures row on an
# older version is stale, re-analyzed by _run and ignored by the discovery
# scorer. 3 was the mp3 -> vorbis analysis-stream switch; 4 is the sample-rate
# cap that stops hi-res sources failing their transcode entirely (see
# jellyfin.py's _ANALYSIS_MAX_SAMPLE_RATE), measured to move vectors 2-6% --
# too far for old and new to share one similarity space.
FEATURE_VERSION = 4

# 22050 halves the STFT's cost versus 44100, discarding content above ~11kHz.
# N_FFT/HOP are halved alongside it so the window (~46ms) and hop (~23ms) in
# seconds are unchanged, keeping the tradeoff to frequency range alone.
SR = 22050
N_FFT = 1024
HOP = 512
N_MFCC = 13

_DOWNLOAD_TIMEOUT = 30
# Pause before the single retry of an empty transcode body (see _download_track).
_EMPTY_BODY_RETRY_DELAY = 1.0

try:
    _POOL_CONTEXT = mp.get_context("fork")
except ValueError:
    _POOL_CONTEXT = mp

# Above this many tracks, hubness stats are computed against a random sample
# rather than the full library, trading slightly noisier median/MAD estimates
# for an O(N^2) pass that stays cheap.
HUBNESS_FULL_PASS_LIMIT = 5000
HUBNESS_SAMPLE_SIZE = 2000
# Rows of the pairwise-distance matrix built at a time; bounds peak memory.
_HUBNESS_BLOCK = 1000

# Scales MAD up to a std-equivalent under approximate normality (1/Phi^-1(0.75)).
# Raw MAD runs ~32% low, which would make discovery._normal_sf read every
# distance as more extreme than it is.
_MAD_TO_STD = 1.4826


def _feature_vector(features: dict) -> list:
    """Concatenate a stored features dict into the vector the discovery scorer
    standardizes and measures distance over, so hubness stats describe the same
    distances that scorer sees."""
    return features["mfcc_mean"] + features["mfcc_std"] + features["contrast_mean"]


_librosa = None


def _ensure_librosa():
    """Import librosa with numba replaced by the pure-Python shim, patching the
    functions whose numba bodies don't run as plain Python. Done once per process
    and lazily, so importing this module never pays librosa's import cost."""
    global _librosa
    if _librosa is not None:
        return _librosa

    import sys
    import numpy as np

    from core import _numba_shim
    # Must precede the first `import librosa` so librosa's `import numba` resolves
    # to the shim. setdefault (not force) so a real numba, if somehow already
    # imported, still wins - the overrides below make both paths identical anyway.
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

    # Vendored from librosa.beat.__beat_track_dp: numba coerces range(float) to int,
    # plain Python rejects it. The outer DP recurrence stays sequential; the inner
    # search window reads only already-computed cumscore entries, so it gathers into
    # one numpy op. locs runs descending so argmax ties break as the original `>` did.
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

    # Vendored from librosa.beat.__beat_local_score. Its static-tempo branch (the
    # only one finload reaches) is a same-mode convolution against a Gaussian
    # window, kept as a manual loop upstream only because old numba lacked
    # np.convolve. The loop's half-open k-range excludes boundary terms a plain
    # zero-padded convolution includes, so the first/last K frames are recomputed
    # with the original loop. The dynamic-tempo branch keeps that loop throughout.
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

    # One STFT shared across beat tracking, MFCC and spectral contrast, which
    # would otherwise each build their own. All four librosa calls take a
    # caller-supplied S as-is, and melspectrogram's default mel-filter norm
    # matches mfcc's own S=None branch, so the reuse is bit-identical.
    stft_mag = np.abs(librosa.stft(y, n_fft=N_FFT, hop_length=HOP))
    mel_db = librosa.power_to_db(
        librosa.feature.melspectrogram(S=stft_mag ** 2, sr=sr, n_fft=N_FFT, hop_length=HOP)
    )

    # aggregate=np.median matches beat_track's own non-default internal call.
    onset_env = librosa.onset.onset_strength(S=mel_db, sr=sr, hop_length=HOP, aggregate=np.median)
    # beat_track() would also run its DP beat-placement pass for individual beat
    # locations, which nothing here reads; tempo() returns the same scalar, which
    # is estimated before that pass, without paying for it.
    tempo = librosa.feature.rhythm.tempo(onset_envelope=onset_env, sr=sr, hop_length=HOP)

    mfcc = librosa.feature.mfcc(S=mel_db, n_mfcc=N_MFCC)
    contrast = librosa.feature.spectral_contrast(S=stft_mag, sr=sr, n_fft=N_FFT, hop_length=HOP)
    return {
        "bpm": float(np.atleast_1d(tempo)[0]),
        "mfcc_mean": [float(x) for x in mfcc.mean(axis=1)],
        "mfcc_std": [float(x) for x in mfcc.std(axis=1)],
        "contrast_mean": [float(x) for x in contrast.mean(axis=1)],
    }


# Per-process, set once by _worker_init before any task runs (see there for
# why this can't just be a plain argument to _throttled_analyze).
_cpu_fraction = 0.25

# Held for the worker's lifetime: dropping the handle restores the BLAS thread
# limits _worker_init narrowed.
_blas_limits = None


def _throttled_analyze(path: str) -> dict:
    """Runs ``_analyze`` then sleeps so busy-time / wall-time stays at or below
    _cpu_fraction: at 0.25, a 2s analysis is followed by a 6s sleep."""
    t0 = time.monotonic()
    result = _analyze(path)
    busy = time.monotonic() - t0
    idle = busy * (1.0 / _cpu_fraction - 1.0)
    if idle > 0:
        time.sleep(idle)
    return result


def _worker_init(cpu_fraction: float):
    # Each pool worker is its own process, so this is set through the pool's
    # initializer rather than read from a settings object the worker can't see.
    global _cpu_fraction
    _cpu_fraction = cpu_fraction

    try:
        os.nice(15)  # POSIX only; deprioritize so foreground apps win any contention
    except (AttributeError, OSError):
        pass

    # numpy and scipy each vendor their own OpenBLAS, both defaulting to a thread
    # pool sized to os.cpu_count(), so one librosa call can spin up 50-70 BLAS
    # threads on top of the process-level parallelism the pool already provides.
    # Single-threaded BLAS per worker removes that contention. Must run before
    # numpy/scipy are first imported in this process, which _worker_init does --
    # except in a fork whose parent had already initialized BLAS.
    for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                 "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ[_var] = "1"

    # Those only bind at BLAS init, which a forked worker is already past whenever
    # the parent has touched numpy (discovery's feature loader and
    # compute_hubness_stats both do). threadpoolctl retunes an already-loaded BLAS.
    global _blas_limits
    try:
        import threadpoolctl
        _blas_limits = threadpoolctl.threadpool_limits(limits=1)
    except Exception:
        pass


def _analyze_one(track_id_and_path: tuple[str, str]) -> tuple[str, dict | None, str | None]:
    track_id, path = track_id_and_path
    try:
        return track_id, _throttled_analyze(path), None
    except Exception as exc:
        # str(exc) is empty for some decode failures; fall back to the class name.
        return track_id, None, str(exc) or type(exc).__name__


class _EmptyResponse(OSError):
    """A 200 with an empty body: the server declined to start a transcode."""


def _fetch_to_temp(track: Track, url: str) -> str:
    """Stream ``url`` to a temp file and return its path, failing loudly on an
    empty body rather than handing librosa something it can't decode."""
    ext = os.path.splitext(url.split("?")[0])[1] or ".audio"
    dest = os.path.join(_temp_audio_dir(), f"{track.id}.{os.getpid()}-{threading.get_ident()}{ext}")
    with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT) as response, open(dest, "wb") as fh:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
    if os.path.getsize(dest) == 0:
        # Jellyfin answers a transcode it can't start with a 200 and an empty
        # body; librosa would turn that into an opaque decode failure seconds
        # later, so it fails here where the cause is still obvious.
        os.remove(dest)
        raise _EmptyResponse("empty response from server")
    return dest


def _download_track(track: Track, provider) -> str:
    """Fetch a remote track's analysis transcode to a temp file and return its
    path. The caller owns the file and is responsible for removing it.

    The stream URL carries the access token in its query string, so a token
    revoked server-side (another client claiming the same device id, say) turns
    every download in the pass into a 401 with nothing to recover it -- the
    provider's own 401-retry only covers its JSON endpoints. Re-authenticating
    and rebuilding the URL once is what keeps a stale token from failing a whole
    library's analysis. An empty body gets the same single retry: it means the
    server declined to start a transcode, which is usually transient.
    """
    for attempt in (0, 1):
        url = provider.get_analysis_stream_url(track.id)
        try:
            return _fetch_to_temp(track, url)
        except urllib.error.HTTPError as exc:
            if exc.code != 401 or attempt or not provider.reauthenticate():
                raise
            logger.info("Re-authenticated after a 401 fetching %s", track.title)
        except _EmptyResponse:
            if attempt:
                raise
            time.sleep(_EMPTY_BODY_RETRY_DELAY)
    raise _EmptyResponse("empty response from server")  # unreachable; the loop returns or raises


def _temp_audio_dir() -> str:
    path = os.path.join(str(get_data_dir()), "tmp_audio_analysis")
    os.makedirs(path, exist_ok=True)
    return path


def _purge_temp_audio() -> None:
    """Clear transcodes a previous run left behind: nothing survives a run by
    design, so anything here is from a crash or a hard kill."""
    directory = _temp_audio_dir()
    for name in os.listdir(directory):
        try:
            os.remove(os.path.join(directory, name))
        except OSError:
            pass


ON_DEMAND_WORKERS = 4  # threads ensure_features analyzes across
ON_DEMAND_LIMIT = 6    # most tracks one ensure_features call will analyze
# Serializes foreground analysis so two radio starts in a row don't race each
# other onto the same tracks; the second finds the first's rows already written.
_on_demand_lock = threading.Lock()


def tracks_with_features(track_ids) -> set[str]:
    """Which of ``track_ids`` already have current-version cached features."""
    ids = list(track_ids)
    if not ids:
        return set()
    rows = (TrackFeatures.select(TrackFeatures.track)
            .where((TrackFeatures.track << ids)
                   & (TrackFeatures.feature_version == FEATURE_VERSION))
            .tuples())
    return {row[0] for row in rows}


def _analyze_track(track: Track, provider) -> tuple[str, dict | None]:
    """Analyze one track, downloading it first if it isn't local."""
    temp_path = None
    try:
        path = track.file_path if track.provider == "local" and track.file_path else None
        if path is None:
            path = temp_path = _download_track(track, provider)
        return track.id, _analyze(path)
    except Exception as exc:
        logger.warning("On-demand analysis of %s failed: %s", track.title, exc)
        return track.id, None
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass


def ensure_features(track_ids, db_manager, provider) -> set[str]:
    """Analyze up to ON_DEMAND_LIMIT of ``track_ids`` that have no current-version
    features, and return the ids that have them afterwards.

    Radio needs features for the track it is seeding from, which the background
    pass may not have reached yet on a fresh install. This runs in the caller's
    thread at full speed rather than through the throttled pool -- the user is
    waiting on it -- and analyzes at most a handful of tracks, so a radio start
    costs seconds rather than failing outright.
    """
    ids = list(dict.fromkeys(track_ids))
    if not ids:
        return set()
    with _on_demand_lock:
        have = tracks_with_features(ids)
        missing = [tid for tid in ids if tid not in have][:ON_DEMAND_LIMIT]
        if not missing:
            return have
        tracks = list(Track.select(Track.id, Track.provider, Track.file_path, Track.title)
                      .where(Track.id << missing))
        if not tracks:
            return have
        workers = min(ON_DEMAND_WORKERS, len(tracks))
        with cf.ThreadPoolExecutor(max_workers=workers) as pool:
            for track_id, features in pool.map(lambda t: _analyze_track(t, provider), tracks):
                if features is not None:
                    db_manager.save_track_features(track_id, **features)
                    have.add(track_id)
    return have


def compute_hubness_stats(db_manager) -> int:
    """Cache each track's median/MAD distance to the rest of the library, which
    discovery._mutual_proximity uses to spot "hub" tracks sitting close to
    everything regardless of genre. Median/MAD rather than mean/std: the real
    distribution is right-skewed enough that mean/std under-corrects hubs.
    Vectors are standardized exactly as discovery._bulk_load_features does."""
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

    # self_col[i] is track i's own column, or -1 when sampling left it out.
    self_col = np.full(len(vectors), -1)
    if len(rows) > HUBNESS_FULL_PASS_LIMIT:
        sample_idx = np.random.choice(len(rows), size=HUBNESS_SAMPLE_SIZE, replace=False)
        sample_vectors = vectors[sample_idx]
        self_col[sample_idx] = np.arange(len(sample_idx))
    else:
        sample_vectors = vectors
        self_col[:] = np.arange(len(vectors))

    # ||a-b||^2 = ||a||^2 + ||b||^2 - 2 a.b, avoiding an O(N^2) Python loop, and
    # blocked over rows so the full matrix never materializes.
    b_sq = np.sum(sample_vectors ** 2, axis=1)
    stats = []
    for start in range(0, len(vectors), _HUBNESS_BLOCK):
        block = vectors[start:start + _HUBNESS_BLOCK]
        a_sq = np.sum(block ** 2, axis=1, keepdims=True)
        dist = np.sqrt(np.maximum(a_sq + b_sq - 2 * (block @ sample_vectors.T), 0.0))
        for offset, row_dists in enumerate(dist):
            # Dropped by index, not by "distance near zero": the expansion above
            # only cancels to ~1e-7, so no threshold separates a self-pair from a
            # genuine duplicate reliably.
            column = self_col[start + offset]
            if column >= 0:
                row_dists = np.delete(row_dists, column)
            if len(row_dists) == 0:
                continue
            median = float(np.median(row_dists))
            mad = float(np.median(np.abs(row_dists - median)))
            stats.append((track_ids[start + offset], median,
                          max(mad * _MAD_TO_STD, 1e-6)))

    db_manager.save_hubness_stats(stats)
    return len(stats)


class AudioFeatureManager(BackgroundJob):
    """Extracts and caches librosa DSP features for every track that lacks
    current-version features. A completed sync starts this like any other
    follow-up job (state.py's ``sync.follow_up_jobs``), as
    ``job.start(force=False)`` with no provider argument -- hence the provider
    getter taken at construction, which also survives ``switch_source()``."""

    supports_force = True

    def __init__(self, settings, db_manager, provider_getter):
        super().__init__()
        self._settings = settings
        self.db = db_manager
        self._get_provider = provider_getter
        # Set when analysis_worker_count/analysis_worker_usage change while a
        # run is in progress -- see _on_setting_changed and _run.
        self._restart_requested = threading.Event()
        settings.add_listener(self._on_setting_changed)

    def start(self, force: bool = False) -> bool:
        if not self._settings.get("enable_radio"):
            return False
        return super().start(force=force)

    def _on_setting_changed(self, key, value):
        if key in ("analysis_worker_count", "analysis_worker_usage") and self.is_running:
            self._restart_requested.set()

    def ensure_features(self, track_ids) -> set[str]:
        """Foreground analysis for tracks radio needs right now, bound to this
        manager's db and provider (see the module-level ensure_features). With
        radio disabled it only reports what is already cached: the user turned
        analysis off, so it isn't run behind their back."""
        if not self._settings.get("enable_radio"):
            return tracks_with_features(track_ids)
        return ensure_features(track_ids, self.db, self._get_provider())

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------

    def _run(self, force: bool = False) -> None:
        provider = self._get_provider()
        self._restart_requested.clear()
        _purge_temp_audio()
        # Only the first pass honors force=True; a restart just keeps going.
        pass_force = force
        analyzed = failed = 0

        while True:
            tracks = self._pending_tracks(pass_force)
            self._emit(total=len(tracks), message="Preparing audio analysis...")
            if not tracks:
                break

            processed, errors, restarted = self._analyze_batch(tracks, provider)
            analyzed += processed - errors
            failed += errors

            if not self._settings.get("enable_radio"):
                return  # _analyze_batch already emitted the "Stopped" status.
            if not restarted:
                break
            pass_force = False

        if not analyzed:
            self._emit(status="complete", message="No new tracks to analyze")
            return

        # Only when something new was written: the stats are a function of the
        # whole feature set, so an empty pass recomputes an identical answer.
        self._emit(message="Computing hubness stats...")
        compute_hubness_stats(self.db)

        self._emit(status="complete",
                   message=f"Analyzed {analyzed} tracks"
                           + (f", {failed} failed" if failed else ""))

    def _pending_tracks(self, force: bool) -> list[Track]:
        """Tracks needing analysis. Only id/provider/file_path/title are read
        downstream, so the query stays narrow."""
        query = Track.select(Track.id, Track.provider, Track.file_path, Track.title)
        if not force:
            # Skip only tracks whose cached features are the current version;
            # a FEATURE_VERSION bump re-analyzes everything else.
            current = (TrackFeatures.select(TrackFeatures.track)
                       .where(TrackFeatures.feature_version == FEATURE_VERSION))
            query = query.where(Track.id.not_in(current))
        return list(query)

    def _analyze_batch(self, tracks: list[Track], provider) -> tuple[int, int, bool]:
        """Runs one pool pass over ``tracks``, returning (processed, errors,
        restarted). ``restarted`` means a worker setting changed mid-pass and the
        caller should re-query and call again; rows already written stay written,
        so stopping early loses no work."""
        n_workers = self._configured_worker_count()
        cpu_fraction = self._configured_cpu_fraction()
        temp_paths: dict[str, str] = {}
        processed = 0
        errors = 0
        restarted = False

        # Tells _resolve_paths to stop feeding the pool. Every exit from the
        # results loop must set this before the `with` block calls terminate(),
        # hence the inner finally: terminate() busy-waits for the task-handler
        # thread, which is the thread parked inside _resolve_paths.
        stop = threading.Event()
        try:
            with _POOL_CONTEXT.Pool(n_workers, initializer=_worker_init,
                                    initargs=(cpu_fraction,)) as pool:
                try:
                    for track_id, features, error in pool.imap_unordered(
                        _analyze_one,
                        self._resolve_paths(tracks, provider, temp_paths, stop),
                    ):
                        if not self._settings.get("enable_radio"):
                            # Turned off mid-run; stop rather than keep working
                            # on a disabled feature.
                            self._emit(status="idle",
                                       message="Stopped - disabled in settings")
                            return processed, errors, False
                        if self._restart_requested.is_set():
                            self._restart_requested.clear()
                            restarted = True
                            break
                        # Pausing here stops new analysis and DB writes; the
                        # downloads already queued in _resolve_paths still finish.
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
                finally:
                    stop.set()
        finally:
            # Anything downloaded but never analyzed would otherwise be left
            # behind in the temp dir.
            for leftover in temp_paths.values():
                try:
                    os.remove(leftover)
                except OSError:
                    pass
            temp_paths.clear()

        return processed, errors, restarted

    def _configured_worker_count(self) -> int:
        configured = int(self._settings.get("analysis_worker_count") or 4)
        return min(max(1, configured), os.cpu_count() or 1)

    def _configured_cpu_fraction(self) -> float:
        # Percent (1-100) in settings -> fraction of a core per worker.
        pct = float(self._settings.get("analysis_worker_usage") or 25)
        return min(max(pct, 1.0), 100.0) / 100.0

    def _wait_while_paused(self, stop: threading.Event) -> None:
        """Like wait_if_paused, but also returns once ``stop`` is set -- this
        runs on the pool's task-handler thread, which teardown waits on."""
        while not self._pause_event.is_set() and not stop.is_set():
            self._pause_event.wait(0.5)

    def _resolve_paths(self, tracks: list[Track], provider, temp_paths: dict[str, str],
                       stop: threading.Event):
        """Yields (track_id, local_path) for the pool. Local tracks already have a
        path; a remote one is transcoded down and fetched here, one at a time.

        Serial on purpose: the analysis pool is duty-cycle throttled, so it
        consumes tracks slowly enough that one download keeps ahead of it, and
        each concurrent download would be another transcode asked of the server.
        It also bounds the temp directory, since the Pool's task-handler thread
        drains this generator eagerly rather than in step with the workers.

        That thread is also what terminate() busy-waits on, so every step checks
        ``stop`` -- an abandoned pass has to be able to tear down.
        """
        local, remote = [], []
        for track in tracks:
            (local if track.provider == "local" and track.file_path else remote).append(track)

        for track in local:
            self._wait_while_paused(stop)
            if stop.is_set():
                return
            yield track.id, track.file_path

        for track in remote:
            # Stop feeding while a sync holds this job paused, so the pause halts
            # downloads and CPU work, not just the DB writes.
            self._wait_while_paused(stop)
            if stop.is_set():
                return
            try:
                local_path = _download_track(track, provider)
            except Exception as exc:
                logger.warning("Skipping %s: download failed (%s)", track.title, exc)
                continue
            # Recorded before the stop check so an abandoned pass still cleans
            # this file up (see _analyze_batch's finally).
            temp_paths[track.id] = local_path
            if stop.is_set():
                return
            yield track.id, local_path

