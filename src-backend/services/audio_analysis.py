"""Main service for DSP audio analysis via librosa.

Analyzing audio happens once in a large library-wide pass and then is stored
in the DB for use with the recommendation rows and radio/autoplay.

librosa runs here with numba/llvmlite replaced by a python shim (caution: shim
is AI generated mostly)

Runs as a background job alongside sync, genre discovery, and artist enrichment.

ensure_features is the foreground counterpart, for the handful of tracks radio
needs right now (see PlaybackManager.start_radio). It skips the job entirely
since it's needed basically immediately and analyzes 4 tracks at most.
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
from collections import deque

from core.config import get_data_dir
from core.database import Track, TrackFeatures
from services.background import BackgroundJob


logger = logging.getLogger(__name__)

# Bumped when the feature set or extractor changes
FEATURE_VERSION = 5

# 22050 is the default, above doesn't produce better results and is slower.
SR = 22050
N_FFT = 1024
HOP = 512
N_MFCC = 13

WINDOW_BASE_S = 60.0 # Minimum analysis time
WINDOW_FRAC = 0.20 # Minimum amount of a song to analyze (WINDOW_BASE_S * 1/WINDOW_FRAC), defualt is 5 minutes
WINDOW_MARGIN = 1.5 # Run full analysis on songs less than WINDOW_MARGIN * WINDOW_BASE_S, otherwise use trim
WINDOW_SEGMENT_TARGET_S = 10.0  # segment length to analyze
WINDOW_SEGMENTS_MIN = 6 # 6 x 10 = 60s
WINDOW_SEGMENTS_MAX = 20 # Cap to avoid ridiculous processing time/memory usage on super long tracks (which aren't going to give good data anyway)
WINDOW_SILENCE_TOP_DB = 40  # trim leading/trailing silence before budgeting/slicing
STATS_BLOCK_S = WINDOW_SEGMENT_TARGET_S  # block length mfcc_std is measured within

_DOWNLOAD_TIMEOUT = 30

_EMPTY_BODY_RETRY_DELAY = 1.0 # If a transcode fails, wait this long before retrying. Usually indicates a server that's too stressed.

try:
    _POOL_CONTEXT = mp.get_context("fork")
except ValueError:
    _POOL_CONTEXT = mp

# Above this many tracks, hubness stats are computed against a random sample
# rather than the full library, slightly worse results but much faster.
HUBNESS_FULL_PASS_LIMIT = 5000
HUBNESS_SAMPLE_SIZE = 2000
_HUBNESS_BLOCK = 1000 # Rows of matrix built at once, capping saves memory.

# Scales MAD up to a std-equivalent under approximate normality (1/Phi^-1(0.75)).
_MAD_TO_STD = 1.4826


def _feature_vector(features: dict) -> list:
    """Converts a features dict into a feature vector."""
    return features["mfcc_mean"] + features["mfcc_std"] + features["contrast_mean"]


_librosa = None


def _ensure_librosa():
    """Import librosa with numba replaced by the pure-Python shim, patching the
    functions whose numba bodies don't run as plain Python."""
    global _librosa
    if _librosa is not None:
        return _librosa # If librosa is already imported, return the cached module

    import sys
    import numpy as np

    from core import _numba_shim
    sys.modules.setdefault("numba", _numba_shim) # Must import first to trick librosa

    import librosa
    import librosa.feature.rhythm
    import librosa.util.utils as _lru

    # ! CAUTION: This section of the code to bypass librosa features is mostly AI generated, proceed with caution.

    # numpy replacements for the stencil-backed local-extrema finders
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

    # Replacement for librosa.beat.__beat_track_dp and __beat_local_score, which are the only
    #numba functions that are actually called by the code in this module.
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

# End AI-generated section.

def _plan_segments(total_s: float):
    """Returns a list of tuples (start_s, length_s) pairs
    corresponding to the segments of audio that need analysis.
    """
    import numpy as np

    if total_s <= WINDOW_BASE_S * WINDOW_MARGIN:
        return None
    budget_s = max(WINDOW_BASE_S, WINDOW_FRAC * total_s)
    if budget_s >= total_s:
        return None

    n_segments = int(np.clip(round(budget_s / WINDOW_SEGMENT_TARGET_S),
                             WINDOW_SEGMENTS_MIN, WINDOW_SEGMENTS_MAX))
    seg_s = budget_s / n_segments
    starts = np.linspace(0, total_s - seg_s, n_segments)
    return [(float(s), seg_s) for s in starts]

def _windowed_clip(librosa, np, y, sr: int):
    """Trims silence and slices the audio into segments for analysis."""
    if len(y) / sr <= WINDOW_BASE_S * WINDOW_MARGIN:
        return y
    y, _ = librosa.effects.trim(y, top_db=WINDOW_SILENCE_TOP_DB, hop_length=2048)
    if y.size == 0:
        return y
    segments = _plan_segments(len(y) / sr)
    if segments is None:
        return y
    parts = []
    for start_s, seg_s in segments:
        start = int(start_s * sr)
        end = min(len(y), start + int(seg_s * sr))
        parts.append(y[start:end])
    return np.concatenate(parts)

def _read_resampled(f, librosa, native_sr: int, frames: int = -1):
    """Reads frames, downmixes to mono, and resamples to SR."""
    raw = f.read(frames, dtype="float32", always_2d=True)
    mono = raw.mean(axis=1) if raw.shape[1] > 1 else raw[:, 0]
    return librosa.resample(mono, orig_sr=native_sr, target_sr=SR)

def _decode_windowed(librosa, path: str):
    """Seeks throughout the audio file instead of analyzing the whole track at once,
    this is much more efficient for most tracks.
    """
    import numpy as np
    import soundfile as sf

    with sf.SoundFile(path) as f:
        native_sr = f.samplerate
        segments = _plan_segments(f.frames / native_sr)
        if segments is None:
            return _read_resampled(f, librosa, native_sr), SR
        parts = []
        for start_s, seg_s in segments:
            f.seek(int(start_s * native_sr))
            parts.append(_read_resampled(f, librosa, native_sr, int(seg_s * native_sr)))
        return np.concatenate(parts), SR

def _refine_tempo(librosa, np, onset_env, sr: int, coarse_bpm: float) -> float:
    """Continuous BPM from librosa's grid-quantized estimate."""
    if coarse_bpm <= 0:
        return 0.0
    strength = librosa.feature.tempogram(onset_envelope=onset_env, sr=sr,
                                         hop_length=HOP).mean(axis=1)
    lag = int(round(60.0 * sr / (HOP * coarse_bpm)))
    if not 1 <= lag < len(strength) - 1:
        return coarse_bpm
    prev, mid, nxt = strength[lag - 1], strength[lag], strength[lag + 1]
    curvature = prev - 2.0 * mid + nxt
    if curvature >= 0:  # not a peak (flat or a trough): keep the binned value
        return coarse_bpm
    # Vertex offset of the parabola through the three points, clamped to the bin.
    offset = float(np.clip(0.5 * (prev - nxt) / curvature, -0.5, 0.5))
    return 60.0 * sr / (HOP * (lag + offset))


def _mfcc_stats(np, mfcc):
    """Returns per-coefficient mean, and std measured within blocks."""
    frames = mfcc.shape[1]
    per_block = max(1, int(round(STATS_BLOCK_S * SR / HOP)))
    n_blocks = max(1, int(round(frames / per_block)))
    if n_blocks < 2:
        return mfcc.mean(axis=1), mfcc.std(axis=1)
    blocks = np.array_split(mfcc, n_blocks, axis=1)
    return mfcc.mean(axis=1), np.mean([b.std(axis=1) for b in blocks], axis=0)


def _analyze(path: str) -> dict:
    """Main analysis pipeline. Extracts features from audio using
    librosa and returns them as a dictionary."""
    import numpy as np
    librosa = _ensure_librosa()

    try:
        y, sr = _decode_windowed(librosa, path)
    except Exception as exc:
        # fall back to full decode.
        logger.debug("Windowed decode failed for %s (%s), falling back to full decode", path, exc)
        y, sr = librosa.load(path, sr=SR, mono=True)
        if y.size == 0:
            raise ValueError("empty audio")
        y = _windowed_clip(librosa, np, y, sr)
    if y.size == 0:
        raise ValueError("empty audio")

    # Calculate STFT once
    stft_mag = np.abs(librosa.stft(y, n_fft=N_FFT, hop_length=HOP))
    # Use the STFT magnitude for both the mel spectrogram and spectral contrast
    mel_basis = librosa.filters.mel(sr=sr, n_fft=N_FFT)
    mel_db = librosa.power_to_db(mel_basis @ (stft_mag ** 2))

    # aggregate=np.median matches beat_track's own non-default internal call.
    onset_env = librosa.onset.onset_strength(S=mel_db, sr=sr, hop_length=HOP, aggregate=np.median)
    tempo = librosa.feature.rhythm.tempo(onset_envelope=onset_env, sr=sr, hop_length=HOP)
    bpm = _refine_tempo(librosa, np, onset_env, sr, float(np.atleast_1d(tempo)[0]))

    mfcc = librosa.feature.mfcc(S=mel_db, n_mfcc=N_MFCC)
    contrast = librosa.feature.spectral_contrast(S=stft_mag, sr=sr, n_fft=N_FFT, hop_length=HOP)
    mfcc_mean, mfcc_std = _mfcc_stats(np, mfcc)
    return {
        "bpm": bpm,
        "mfcc_mean": [float(x) for x in mfcc_mean],
        "mfcc_std": [float(x) for x in mfcc_std],
        "contrast_mean": [float(x) for x in contrast.mean(axis=1)],
    }

_cpu_fraction = 0.25 #% of CPU time each worker can use, limited to 1 core per worker.

_blas_limits = None

def _throttled_analyze(path: str) -> dict:
    """Analyzes audio and sleeps to throttle CPU usage."""
    t0 = time.monotonic()
    result = _analyze(path)
    busy = time.monotonic() - t0
    idle = busy * (1.0 / _cpu_fraction - 1.0)
    if idle > 0:
        time.sleep(idle)
    return result

def _worker_init(cpu_fraction: float):
    """Pool worker initializer. Sets the process nice level and limits BLAS to one thread."""
    global _cpu_fraction
    _cpu_fraction = cpu_fraction

    try:
        os.nice(15)  # POSIX only; deprioritize so foreground apps win any contention
    except (AttributeError, OSError):
        pass

    # By defualt, BLAS threads are unbounded and can saturate all cores, so limit them to 1.
    for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                 "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ[_var] = "1"
    global _blas_limits
    try:
        import threadpoolctl
        _blas_limits = threadpoolctl.threadpool_limits(limits=1)
    except Exception:
        pass

def _analyze_one(track_id_and_path: tuple[str, str]) -> tuple[str, dict | None, str | None]:
    """Pool worker: analyze one track and return (track_id, features, error)."""
    track_id, path = track_id_and_path
    try:
        return track_id, _throttled_analyze(path), None
    except Exception as exc:
        # str(exc) is empty for some decode failures; fall back to the class name.
        return track_id, None, str(exc) or type(exc).__name__

class _EmptyResponse(OSError):
    """A 200 with an empty body: the server declined to start a transcode."""

def _fetch_to_temp(track: Track, url: str) -> str:
    """Stream audio from a URL to a temp file and return its path."""
    ext = os.path.splitext(url.split("?")[0])[1] or ".audio"
    dest = os.path.join(_temp_audio_dir(), f"{track.id}.{os.getpid()}-{threading.get_ident()}{ext}")
    with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT) as response, open(dest, "wb") as fh:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
    if os.path.getsize(dest) == 0: # Empty request failed to start transcode
        os.remove(dest)
        raise _EmptyResponse("empty response from server")
    return dest

def _download_track(track: Track, provider) -> str:
    """Fetch a remote track's analysis transcode to a temp file and return its
    path. The caller owns the file and is responsible for removing it."""
    for attempt in (0, 1):
        token_at_request = provider.access_token
        url = provider.get_analysis_stream_url(track.id)
        try:
            return _fetch_to_temp(track, url)
        except urllib.error.HTTPError as exc:
            if exc.code != 401 or attempt or not provider.reauthenticate(token_at_request):
                raise
            logger.info("Re-authenticated after a 401 fetching %s", track.title)
        except _EmptyResponse:
            if attempt:
                raise
            time.sleep(_EMPTY_BODY_RETRY_DELAY)
    raise _EmptyResponse("empty response from server")  # unreachable

def _temp_audio_dir() -> str:
    """Returns a temp directory for downloaded audio, creating it if needed."""
    path = os.path.join(str(get_data_dir()), "tmp_audio_analysis")
    os.makedirs(path, exist_ok=True)
    return path

def _purge_temp_audio() -> None:
    """Removes all temporary audio files."""
    directory = _temp_audio_dir()
    for name in os.listdir(directory):
        try:
            os.remove(os.path.join(directory, name))
        except OSError:
            pass

ON_DEMAND_WORKERS = 4  # threads to use for foreground audio analysis in ensure_features
ON_DEMAND_LIMIT = 6    # most tracks one ensure_features call will analyze
_on_demand_lock = threading.Lock()

DOWNLOAD_CONCURRENCY = 1 # Entirely bandwidth-limited, so more workers doesn't speed things up.

def tracks_with_features(track_ids) -> set[str]:
    """Returns the IDs of tracks that have current-version cached features."""
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
    """Analyzes tracks immediately that don't have current-version features, bypassing the background job.
    Returns the set of track IDs that now have features, including those that already did."""
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
    """Compute hubness statistics for the current library. Prevents tracks from being nearest_neighbor
    to too many other tracks, which otherwise makes them over-represented."""
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

    self_col = np.full(len(vectors), -1)
    if len(rows) > HUBNESS_FULL_PASS_LIMIT:
        sample_idx = np.random.choice(len(rows), size=HUBNESS_SAMPLE_SIZE, replace=False)
        sample_vectors = vectors[sample_idx]
        self_col[sample_idx] = np.arange(len(sample_idx))
    else:
        sample_vectors = vectors
        self_col[:] = np.arange(len(vectors))

    b_sq = np.sum(sample_vectors ** 2, axis=1)
    stats = []
    for start in range(0, len(vectors), _HUBNESS_BLOCK):
        block = vectors[start:start + _HUBNESS_BLOCK]
        a_sq = np.sum(block ** 2, axis=1, keepdims=True)
        dist = np.sqrt(np.maximum(a_sq + b_sq - 2 * (block @ sample_vectors.T), 0.0))
        for offset, row_dists in enumerate(dist):
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
    """Extracts and caches librosa DSP features for every track that lacks current-version features."""
    supports_force = True

    def __init__(self, settings, db_manager, provider_getter):
        super().__init__()
        self._settings = settings
        self.db = db_manager
        self._get_provider = provider_getter
        # Set by _on_setting_changed, consumed once the current pass stops
        self._pending_restart = False
        settings.add_listener(self._on_setting_changed)

    def start(self, force: bool = False) -> bool:
        if not self._settings.get("enable_radio"):
            return False
        return super().start(force=force)

    def _on_setting_changed(self, key, value):
        if key in ("analysis_worker_count", "analysis_worker_usage") and self.is_running:
            self._pending_restart = True
            self.stop()

    def ensure_features(self, track_ids) -> set[str]:
        """Foreground analysis for tracks radio needs right now."""
        if not self._settings.get("enable_radio"):
            return tracks_with_features(track_ids)
        return ensure_features(track_ids, self.db, self._get_provider())

    def _run(self, force: bool = False) -> None:
        provider = self._get_provider()

        while True:
            _purge_temp_audio()
            tracks = self._pending_tracks(force)
            self._emit(total=len(tracks), message="Preparing audio analysis...")
            if not tracks:
                self._emit(status="complete", message="All tracks already analyzed!")
                return

            processed, errors, outcome = self._analyze_batch(tracks, provider)
            if outcome == "disabled":
                return
            if outcome == "stopped":
                if self._pending_restart:
                    self._pending_restart = False
                    self._stop_event.clear()
                    self._emit(status="running", message="Restarting with new settings...",
                               processed=0, total=0)
                    force = False
                    continue
                self._emit(status="idle", message="Stopped")
                return

            analyzed = processed - errors
            if not analyzed:
                self._emit(status="complete", message="All tracks already analyzed!")
                return

            self._emit(message="Computing hubness stats...")
            compute_hubness_stats(self.db)
            self._emit(status="complete",
                       message=f"Analyzed {analyzed} tracks"
                               + (f", {errors} failed." if errors else "."))
            return

    def _pending_tracks(self, force: bool) -> list[Track]:
        """Returns a list of tracks that need analysis. If force=True, returns all tracks."""
        query = Track.select(Track.id, Track.provider, Track.file_path, Track.title)
        if not force:
            # Skip only tracks whose cached features are the current version
            current = (TrackFeatures.select(TrackFeatures.track)
                       .where(TrackFeatures.feature_version == FEATURE_VERSION))
            query = query.where(Track.id.not_in(current))
        return list(query)

    def _analyze_batch(self, tracks: list[Track], provider) -> tuple[int, int, str]:
        """Runs one pool pass over the list of tracks."""
        n_workers = self._configured_worker_count()
        cpu_fraction = self._configured_cpu_fraction()
        temp_paths: dict[str, str] = {}
        processed = 0
        errors = 0
        outcome = "done"
        stop = threading.Event()
        gate = threading.Semaphore(n_workers)

        try:
            with _POOL_CONTEXT.Pool(n_workers, initializer=_worker_init,
                                    initargs=(cpu_fraction,)) as pool:
                for track_id, features, error in pool.imap_unordered(
                    _analyze_one,
                    self._resolve_paths(tracks, provider, temp_paths, stop, gate),
                ):
                    gate.release()
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

                    if outcome == "done":
                        if not self._settings.get("enable_radio"):
                            outcome = "disabled"
                            stop.set()
                        elif self.should_stop():
                            outcome = "stopped"
                            stop.set()
        finally:
            # Clean up leftover temp files on sudden restart or error
            for leftover in temp_paths.values():
                try:
                    os.remove(leftover)
                except OSError:
                    pass
            temp_paths.clear()

        if outcome == "disabled":
            self._emit(status="idle", message="Stopped. Disabled in settings.")
        return processed, errors, outcome

    def _configured_worker_count(self) -> int:
        configured = int(self._settings.get("analysis_worker_count") or 4)
        return min(max(1, configured), os.cpu_count() or 1)

    def _configured_cpu_fraction(self) -> float:
        pct = float(self._settings.get("analysis_worker_usage") or 25)
        return min(max(pct, 1.0), 100.0) / 100.0

    def _resolve_paths(self, tracks: list[Track], provider, temp_paths: dict[str, str],
                       stop: threading.Event, gate: threading.Semaphore):
        """Yields (track_id, local_path) for the pool."""
        local, remote = [], []
        for track in tracks:
            (local if track.provider == "local" and track.file_path else remote).append(track)

        for track in local:
            if stop.is_set() or self.should_stop():
                return
            gate.acquire()
            yield track.id, track.file_path

        if not remote:
            return
        yield from self._resolve_remote(remote, provider, temp_paths, stop, DOWNLOAD_CONCURRENCY, gate)

    def _resolve_remote(self, remote: list[Track], provider, temp_paths: dict[str, str],
                        stop: threading.Event, concurrency: int, gate: threading.Semaphore):
        """Downloads a remote track and yields its ID and local path. Supports concurrency."""
        it = iter(remote)

        def submit_next(pool):
            if stop.is_set() or self.should_stop():
                return None
            track = next(it, None)
            return (pool.submit(_download_track, track, provider), track) if track else None

        with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
            window = deque()
            for _ in range(concurrency):
                item = submit_next(pool)
                if item is None:
                    break
                window.append(item)

            while window:
                future, track = window.popleft()
                try:
                    local_path = future.result()
                except Exception as exc:
                    logger.warning("Skipping %s: download failed (%s)", track.title, exc)
                else:
                    # Recorded before the stop check so an abandoned pass still cleans this file up
                    temp_paths[track.id] = local_path
                    if stop.is_set() or self.should_stop():
                        return
                    gate.acquire()
                    yield track.id, local_path
                if stop.is_set() or self.should_stop():
                    return
                item = submit_next(pool)
                if item is not None:
                    window.append(item)