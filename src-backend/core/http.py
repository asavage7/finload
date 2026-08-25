"""Shared GET helper and rate limiter for external API clients
(Last.fm, MusicBrainz, TheAudioDB, ...)."""

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

from core.config import USER_AGENT

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10

# Transient server-side conditions. MusicBrainz in particular answers 503 when
# its rate limiter trips or it is simply busy, and asks clients to retry.
_RETRY_STATUSES = frozenset({429, 502, 503, 504})
_MAX_RETRIES = 3
# Retry-After is honored but capped; these run on background jobs that should
# stay responsive to pause and shutdown.
_MAX_RETRY_WAIT = 30


class RateLimiter:
    """Serializes calls so they're spaced at least min_interval seconds apart."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last_call = 0.0

    def wait(self):
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self._last_call = time.monotonic()


def fetch_json(url: str, timeout: int = _DEFAULT_TIMEOUT) -> Optional[dict]:
    """GET a URL and parse its JSON body. None (with a logged warning) on any failure."""
    data = fetch_bytes(url, timeout=timeout)
    if data is None:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except Exception as exc:
        logger.warning("Failed to parse JSON from %s: %s", url, exc)
        return None


def _retry_delay(exc: urllib.error.HTTPError, attempt: int) -> float:
    """Seconds to wait before retrying: the server's Retry-After if it sent a
    usable one, else exponential backoff."""
    raw = exc.headers.get("Retry-After") if exc.headers else None
    try:
        return min(float(raw), _MAX_RETRY_WAIT)
    except (TypeError, ValueError):
        return min(2.0 ** attempt, _MAX_RETRY_WAIT)


def fetch_bytes(
    url: str, headers: Optional[dict] = None, timeout: int = _DEFAULT_TIMEOUT,
    retries: int = _MAX_RETRIES
) -> Optional[bytes]:
    """GET a URL and return its raw response body, retrying transient server
    errors. None (with a logged warning) on any other failure."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in _RETRY_STATUSES or attempt == retries:
                logger.warning("HTTP GET failed (%s): %s", url, exc)
                return None
            delay = _retry_delay(exc, attempt)
            logger.info("HTTP %s from %s; retrying in %.1fs", exc.code, url, delay)
            time.sleep(delay)
        except Exception as exc:
            logger.warning("HTTP GET failed (%s): %s", url, exc)
            return None
    return None
