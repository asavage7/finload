"""Shared lyrics helpers used by every provider.

Lyric results use one shape everywhere:

    {"type": "synced",   "lines": [{"time_ms": float, "text": str}, ...]}
    {"type": "unsynced", "text": str}
    {"type": "none"}
"""
import json
import re
import urllib.parse
import urllib.request

from config import USER_AGENT

_REQUEST_TIMEOUT = 5

# [mm:ss] or [mm:ss.xx]; a line can carry several timestamps.
_LRC_TIMESTAMP = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
_LRC_TAG = re.compile(r"\[[^\]]*\]")

NO_LYRICS = {"type": "none"}


def parse_lrc(text: str, synced_enabled: bool = True) -> dict:
    """Parse LRC text into a lyrics result.

    Returns synced lines when timestamps are present (and allowed), otherwise
    falls back to the plain text content.
    """
    lines = []
    plain = []
    for raw in text.splitlines():
        stamps = _LRC_TIMESTAMP.findall(raw)
        content = _LRC_TAG.sub("", raw).strip()
        if not content:
            continue
        for mins, secs in stamps:
            lines.append({"time_ms": int(mins) * 60000 + float(secs) * 1000, "text": content})
        plain.append(content)
    if lines and synced_enabled:
        lines.sort(key=lambda entry: entry["time_ms"])
        return {"type": "synced", "lines": lines}
    if plain:
        return {"type": "unsynced", "text": "\n".join(plain)}
    return dict(NO_LYRICS)


def fetch_lrclib(track, synced_enabled: bool = True) -> tuple[dict, str | None]:
    """Look a track up on lrclib.net.

    Returns (result, raw_synced_lrc). The raw LRC text is provided so callers
    that can push lyrics back to their source (e.g. a Jellyfin server) have the
    original file content, not just the parsed lines.
    """
    try:
        query = urllib.parse.urlencode({
            "track_name": track.title,
            "artist_name": track.artist.name,
            "album_name": track.album.title,
            "duration": int(track.duration_ms) / 1000,
        })
        req = urllib.request.Request(
            f"https://lrclib.net/api/get?{query}",
            headers={"User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as response:
            data = json.loads(response.read().decode())

        raw_synced = data.get("syncedLyrics")
        if raw_synced and synced_enabled:
            result = parse_lrc(raw_synced, synced_enabled)
            if result["type"] == "synced":
                return result, raw_synced
        if data.get("plainLyrics"):
            return {"type": "unsynced", "text": data["plainLyrics"]}, None
    except Exception:
        pass
    return dict(NO_LYRICS), None
