"""Genre enrichment: MusicBrainz (via existing MBIDs) + Last.fm fallback.

Per album/artist, preference order is:
  1. MusicBrainz's curated genre relations, when an MBID is already known.
     Jellyfin resolves these for most well-tagged libraries (see
     ``providers/jellyfin.py``'s ``ProviderIds`` fetch); local files don't
     have one yet since there's no audio-fingerprinting step. MusicBrainz
     genres need no noise filtering — they're editor-curated, not folksonomy.
  2. Last.fm's crowd tags, filtered to drop noise (self-references, decades,
     nationalities, rating/list artifacts) and thresholded by tag count. Used
     whenever MusicBrainz had no MBID or returned nothing.

Both write into the same Genre/AlbumGenre/ArtistGenre tables via
``database.link_genres``, tagged with source "musicbrainz" / "lastfm" so the
two never collide or overwrite each other. Tracks inherit their album's
genres rather than being enriched individually.
"""
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from background import BackgroundJob
from config import USER_AGENT
from database import Album, Artist, Track

_REQUEST_TIMEOUT = 10

_LASTFM_BASE_URL = "https://ws.audioscrobbler.com/2.0/"
_MB_BASE_URL = "https://musicbrainz.org/ws/2"

MIN_LASTFM_COUNT = 10

_DECADE_YEAR_RE = re.compile(r"^(19|20)\d{2}$|^\d{2}0?s$|^\d{4}s$")

_NATIONALITY_DENYLIST = {
    "american", "americain", "british", "english", "canadian", "australian", "german",
    "french", "japanese", "swedish", "norwegian", "finnish", "irish",
    "scottish", "welsh", "dutch", "italian", "spanish", "brazilian",
    "mexican", "russian", "korean", "chinese", "polish", "danish",
    "icelandic", "belgian", "austrian", "uk", "usa", "new zealand",
}

# Exact (not substring) matches: tags that are junk on their own but would be
# unsafe to match as a substring (e.g. "test" inside a real genre name).
_EXACT_JUNK_NAMES = {"test", "testing", "tests", "aoty"}

# Substrings matched case-insensitively anywhere in the tag: personal-list,
# rating, and reaction artifacts, not genre descriptors.
_JUNK_SUBSTRINGS = (
    "stars", "star rating", "5/5", "10/10", "favo",  # favourite/favorite/favoritos
    "seen live", "concert", "live show",
    "albums i", "artists i", "own", "wishlist", "to check", "to listen",
    "check out", "playlist", "love at first", "guilty pleasure",
    "best of", "best albums", "top 100", "top 10", "amazing", "awesome", "beautiful",
    "<3", "smoke weed", "weed", "male vocalist", "female vocalist", "scrobbl",
    "1001 albums", "must hear" "must listen",
)


_LOOKUP_PUNCTUATION = str.maketrans({
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",  # dash variants
    "‘": "'", "’": "'", "“": '"', "”": '"',                # curly quotes
    " ": " ",                                                            # non-breaking space
    "…": "...",                                                          # horizontal ellipsis
})


_FEATURING_SPLIT = re.compile(r"\s+(?:feat\.|ft\.|&)\s+", re.IGNORECASE)


def _normalize_for_lookup(name: str) -> str:
    """Builds the artist name actually sent to Last.fm — stripped of
    featuring/collaboration credits, normalized to plain ASCII punctuation,
    and with any literal "+" pre-escaped, since Last.fm's own catalog
    doesn't match on any of those forms. Only used for the outgoing query
    string; the stored (full, nicer-looking) name is untouched everywhere
    else.

    The "+" case is a genuine quirk on Last.fm's side, confirmed
    empirically: a normally-percent-encoded "+" (-> "%2B" via urlencode)
    gets rejected as "artist not found," but a *double*-encoded one
    ("%252B", i.e. urlencode'ing the already-escaped "%2B") is accepted —
    matching Last.fm's own artist page for "+44", which is literally
    last.fm/music/%252B44.
    """
    primary = _FEATURING_SPLIT.split(name, maxsplit=1)[0].strip()
    normalized = primary.translate(_LOOKUP_PUNCTUATION)
    return normalized.replace("+", "%2B")


def _is_self_reference(tag: str, artist_name: str) -> bool:
    norm_tag = re.sub(r"[^a-z0-9]", "", tag.lower())
    norm_artist = re.sub(r"[^a-z0-9]", "", artist_name.lower())
    if norm_tag == norm_artist:
        return True
    words = re.findall(r"[A-Za-z0-9]+", artist_name)
    if len(words) >= 2:
        acronym = "".join(w[0] for w in words).lower()
        if norm_tag == acronym:
            return True
    return False


def _is_year_or_decade(tag: str) -> bool:
    t = tag.lower().strip().replace("’", "'").replace("'", "")
    return bool(_DECADE_YEAR_RE.match(t))


def _is_junk(tag: str) -> bool:
    t = tag.lower()
    return any(sub in t for sub in _JUNK_SUBSTRINGS)


def filter_lastfm_tags(tags: list[tuple[str, int]], artist_name: str,
                        min_count: int = MIN_LASTFM_COUNT) -> list[tuple[str, int]]:
    """Drop noise from raw Last.fm tags, keeping (name, count) pairs that look
    like genuine genres. See the module docstring / PLANNING.md for the
    real-data validation behind these rules."""
    out = []
    for name, count in tags:
        if count < min_count:
            continue
        if _is_year_or_decade(name):
            continue
        if name.strip().lower() in _NATIONALITY_DENYLIST:
            continue
        if name.strip().lower() in _EXACT_JUNK_NAMES:
            continue
        if _is_self_reference(name, artist_name):
            continue
        if _is_junk(name):
            continue
        out.append((name, count))
    return out


class _RateLimiter:
    """Serializes calls so they're spaced at least ``min_interval`` seconds apart."""

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


# MusicBrainz mandates >=1 req/sec; Last.fm has no hard limit but this keeps
# enrichment polite under sustained background use.
_mb_limiter = _RateLimiter(1.05)
_lastfm_limiter = _RateLimiter(0.25)


def _lastfm_get(api_key: str, method: str, **params) -> dict:
    query = {"method": method, "api_key": api_key, "format": "json", **params}
    url = f"{_LASTFM_BASE_URL}?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    _lastfm_limiter.wait()
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"[genre] Last.fm request failed ({method}): {exc}")
        return {}


def _lastfm_top_tags(data: dict) -> list[tuple[str, int]]:
    tags = ((data.get("toptags") or {}).get("tag")) or []
    if isinstance(tags, dict):
        tags = [tags]
    return [(t["name"], int(t.get("count", 0))) for t in tags if t.get("name")]


def _mb_genres(entity_type: str, mbid: str) -> list[tuple[str, int]]:
    """Curated genres for a MusicBrainz entity ("recording" | "release-group" | "artist")."""
    url = f"{_MB_BASE_URL}/{entity_type}/{mbid}?inc=genres&fmt=json"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    _mb_limiter.wait()
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"[genre] MusicBrainz request failed ({entity_type}/{mbid}): {exc}")
        return []
    return [(g["name"], int(g.get("count", 0))) for g in (data.get("genres") or []) if g.get("name")]


class GenreEnrichmentManager(BackgroundJob):
    def __init__(self, settings, db_manager):
        super().__init__()
        self._settings = settings
        self.db = db_manager
        self._artist_cache: dict[str, tuple[list, str | None]] = {}

    def start(self, force: bool = False) -> bool:
        if not self._settings.get("enable_genre_enrichment"):
            return False
        return super().start(force=force)

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------

    def _run(self, force: bool = False) -> None:
        self._artist_cache = {}
        api_key = (self._settings.get("lastfm_api_key") or "").strip()

        if force:
            albums = list(Album.select())
        else:
            already_enriched = self._enriched_album_ids()
            albums = [a for a in Album.select() if a.id not in already_enriched]

        self._emit(total=len(albums), message="Gathering albums to enrich…")

        for processed, album in enumerate(albums, start=1):
            self._enrich_album(album, api_key)
            self._emit(processed=processed, message=f"Enriching genres: {album.title}")

        self._emit(status="complete", message=f"Enriched genres for {len(albums)} albums")

    @staticmethod
    def _enriched_album_ids() -> set:
        """Albums that already have at least one musicbrainz/lastfm genre link."""
        from database import AlbumGenre
        rows = (AlbumGenre
                .select(AlbumGenre.album)
                .where(AlbumGenre.source << ("musicbrainz", "lastfm"))
                .distinct())
        return {row.album_id for row in rows}

    # ------------------------------------------------------------------
    # Enrichment logic
    # ------------------------------------------------------------------

    def _enrich_album(self, album: Album, api_key: str) -> None:
        artist = album.artist

        album_genres, album_source = [], None
        if album.mbid:
            mb = _mb_genres("release-group", album.mbid)
            if mb:
                album_genres, album_source = mb, "musicbrainz"

        if not album_genres and api_key:
            raw = _lastfm_top_tags(_lastfm_get(
                api_key, "album.gettoptags",
                artist=_normalize_for_lookup(artist.name),
                album=_normalize_for_lookup(album.title), autocorrect=1))
            filtered = filter_lastfm_tags(raw, artist.name)
            if filtered:
                album_genres, album_source = filtered, "lastfm"

        if album_genres:
            self._link_album(album.id, album_genres, album_source)

        self._get_artist_genres(artist, api_key)

        # Compilation albums (album.artist == "Various Artists") have tracks
        # whose *own* artist differs from the album's nominal one — those
        # artists never get processed above, since we only ever looked at
        # album.artist. Without this, every track on a various-artists
        # compilation is permanently stuck with zero artist-level genre
        # data no matter how many times enrichment runs, because there's no
        # other album in the library that would ever trigger their lookup.
        track_artist_ids = {
            t.artist_id for t in Track.select(Track.artist).where(Track.album == album.id)
        }
        track_artist_ids.discard(artist.id)
        for track_artist_id in track_artist_ids:
            track_artist = Artist.get_by_id(track_artist_id)
            self._get_artist_genres(track_artist, api_key)

    def _get_artist_genres(self, artist: Artist, api_key: str) -> tuple[list, str | None]:
        if artist.id in self._artist_cache:
            return self._artist_cache[artist.id]

        genres, source = [], None
        if artist.mbid:
            mb = _mb_genres("artist", artist.mbid)
            if mb:
                genres, source = mb, "musicbrainz"

        if not genres and api_key:
            raw = _lastfm_top_tags(_lastfm_get(
                api_key, "artist.gettoptags",
                artist=_normalize_for_lookup(artist.name), autocorrect=1))
            filtered = filter_lastfm_tags(raw, artist.name)
            if filtered:
                genres, source = filtered, "lastfm"

        if genres:
            self._link_artist(artist.id, genres, source)

        self._artist_cache[artist.id] = (genres, source)
        return genres, source

    # ------------------------------------------------------------------
    # DB write helpers
    # ------------------------------------------------------------------

    def _link_album(self, album_id: str, genres: list[tuple[str, int]], source: str) -> None:
        self.db.link_genres(album_genres=[(album_id, name, source, weight) for name, weight in genres])

    def _link_artist(self, artist_id: str, genres: list[tuple[str, int]], source: str) -> None:
        self.db.link_genres(artist_genres=[(artist_id, name, source, weight) for name, weight in genres])
