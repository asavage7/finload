"""Genre enrichment: MusicBrainz (via existing MBIDs) + Last.fm fallback.

Per album/artist, preference order is:
  1. MusicBrainz's curated genre relations, when an MBID is already known.
     Jellyfin resolves these for most well-tagged libraries (see
     ``providers/jellyfin.py``'s ``ProviderIds`` fetch); local files don't
     have one yet since there's no audio-fingerprinting step. MusicBrainz
     genres need no noise filtering - they're editor-curated, not folksonomy.
  2. Last.fm's crowd tags, filtered to drop noise (self-references, decades,
     nationalities, rating/list artifacts) and thresholded by tag count. Used
     whenever MusicBrainz had no MBID or returned nothing.

Both write into the same Genre/AlbumGenre/ArtistGenre tables via
``database.link_genres``, tagged with source "musicbrainz" / "lastfm" so the
two never collide or overwrite each other. Tracks inherit their album's
genres rather than being enriched individually.
"""
import logging
import re
import urllib.parse

from core.database import Album, AlbumGenre, Artist, ArtistGenre, Track
from core.http import RateLimiter, fetch_json
from services.background import BackgroundJob

logger = logging.getLogger(__name__)

_LASTFM_BASE_URL = "https://ws.audioscrobbler.com/2.0/"
_MB_BASE_URL = "https://musicbrainz.org/ws/2"

MIN_LASTFM_COUNT = 10

# Consecutive unreachable-source failures before a run gives up for now.
_UNREACHABLE_LIMIT = 5

_DECADE_YEAR_RE = re.compile(r"^(19|20)\d{2}$|^\d{2}0?s$|^\d{4}s$")

_NATIONALITY_DENYLIST = {
    "american", "americain", "british", "english", "canadian", "australian", "german",
    "french", "japanese", "swedish", "norwegian", "finnish", "irish",
    "scottish", "welsh", "dutch", "italian", "spanish", "brazilian",
    "mexican", "russian", "korean", "chinese", "polish", "danish",
    "icelandic", "belgian", "austrian", "uk", "usa", "new zealand",
}

# Exact (not substring) matches: tags that are junk on their own but appear
# inside real genre names ("own" in "motown", "concert" in "concerto").
_EXACT_JUNK_NAMES = {
    "test", "testing", "tests", "aoty", "own", "concert", "amazing", "awesome",
    "beautiful", "weed", "playlist", "wishlist", "favorites", "favourites",
}

# Matched on word boundaries anywhere in the tag: personal-list, rating, and
# reaction artifacts, not genre descriptors.
_JUNK_PHRASES = (
    "stars", "star rating", "5/5", "10/10", "favo",  # favourite/favorite/favoritos
    "seen live", "live show",
    "albums i", "artists i", "i own", "wishlist", "to check", "to listen",
    "check out", "playlist", "love at first", "guilty pleasure",
    "best of", "best albums", "top 100", "top 10",
    "<3", "smoke weed", "male vocalist", "female vocalist", "scrobbl",
    "1001 albums", "must hear", "must listen",
)

# Leading \b only (and only where the phrase starts with a word character), so
# "favo" still catches "favourites" while "own" can no longer eat "motown".
_JUNK_RE = re.compile("|".join(
    (rf"\b{re.escape(p)}" if p[0].isalnum() else re.escape(p))
    for p in _JUNK_PHRASES
), re.IGNORECASE)


_LOOKUP_PUNCTUATION = str.maketrans({
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",  # dash variants
    "‘": "'", "’": "'", "“": '"', "”": '"',                # curly quotes
    " ": " ",                                                            # non-breaking space
    "…": "...",                                                          # horizontal ellipsis
})


_FEATURING_SPLIT = re.compile(r"\s+(?:feat\.|ft\.|&)\s+", re.IGNORECASE)


def _normalize_for_lookup(name: str) -> str:
    """Builds the artist name actually sent to Last.fm - stripped of
    featuring/collaboration credits, normalized to plain ASCII punctuation,
    and with any literal "+" pre-escaped, since Last.fm's own catalog
    doesn't match on any of those forms. Only used for the outgoing query
    string; the stored (full, nicer-looking) name is untouched everywhere
    else.

    The "+" case is a genuine quirk on Last.fm's side, confirmed
    empirically: a normally-percent-encoded "+" (-> "%2B" via urlencode)
    gets rejected as "artist not found," but a *double*-encoded one
    ("%252B", i.e. urlencode'ing the already-escaped "%2B") is accepted -
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


def filter_lastfm_tags(tags: list[tuple[str, int]], artist_name: str,
                        min_count: int = MIN_LASTFM_COUNT) -> list[tuple[str, int]]:
    """Drop noise from raw Last.fm tags, keeping (name, count) pairs that look
    like genuine genres."""
    out = []
    for name, count in tags:
        exact = name.strip().lower()
        if (count < min_count
                or _is_year_or_decade(name)
                or exact in _NATIONALITY_DENYLIST
                or exact in _EXACT_JUNK_NAMES
                or _is_self_reference(name, artist_name)
                or _JUNK_RE.search(name)):
            continue
        out.append((name, count))
    return out


# MusicBrainz caps at 1 req/sec and answers 503 on a burst, so this sits just
# clear of the limit rather than on it. Last.fm has no hard limit, but this
# keeps enrichment polite under sustained background use.
_mb_limiter = RateLimiter(1.2)
_lastfm_limiter = RateLimiter(0.25)


def _lastfm_get(api_key: str, method: str, **params) -> dict:
    query = {"method": method, "api_key": api_key, "format": "json", **params}
    url = f"{_LASTFM_BASE_URL}?" + urllib.parse.urlencode(query)
    _lastfm_limiter.wait()
    return fetch_json(url) or {}


def _lastfm_top_tags(data: dict) -> list[tuple[str, int]]:
    tags = ((data.get("toptags") or {}).get("tag")) or []
    if isinstance(tags, dict):
        tags = [tags]
    return [(t["name"], int(t.get("count", 0))) for t in tags if t.get("name")]


class LookupFailed(Exception):
    """A source was unreachable, as opposed to having nothing for this entity.
    Raised so the caller leaves the entity un-enriched and retries it later."""


def _mb_genres(entity_type: str, mbid: str) -> list[tuple[str, int]]:
    """Curated genres for a MusicBrainz entity ("recording" | "release-group" |
    "artist"). Raises LookupFailed if MusicBrainz could not be reached."""
    url = f"{_MB_BASE_URL}/{entity_type}/{mbid}?inc=genres&fmt=json"
    _mb_limiter.wait()
    data = fetch_json(url)
    if data is None:
        # An empty list here would be indistinguishable from "no genres" and
        # would fall through to Last.fm, permanently downgrading this entity.
        raise LookupFailed(f"musicbrainz {entity_type} {mbid}")
    return [(g["name"], int(g.get("count", 0))) for g in (data.get("genres") or []) if g.get("name")]


class GenreEnrichmentManager(BackgroundJob):
    supports_force = True

    def __init__(self, settings, db_manager):
        super().__init__()
        self._settings = settings
        self.db = db_manager
        # artist id -> (genres, source) for artists handled in this run, plus
        # (seeded from the DB unless forcing) ones a previous run already did.
        self._artist_cache: dict[str, tuple[list, str | None]] = {}
        # album id -> ids of artists credited on its tracks (compilations).
        self._track_artists: dict[str, set] = {}

    def start(self, force: bool = False) -> bool:
        if not self._settings.get("enable_genre_enrichment"):
            return False
        return super().start(force=force)

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------

    def _run(self, force: bool = False) -> None:
        api_key = (self._settings.get("lastfm_api_key") or "").strip()
        # Joined so album.artist is already loaded; otherwise each album costs
        # an extra query just to read its artist's name and mbid.
        query = Album.select(Album, Artist).join(Artist)
        if force:
            self._artist_cache = {}
        else:
            # A subquery, not a materialized id list: the latter would hit
            # SQLite's bound-parameter limit on a large library.
            query = query.where(Album.id.not_in(self._enriched(AlbumGenre, AlbumGenre.album)))
            # Without this, every run re-queries MusicBrainz/Last.fm for artists
            # a previous run already resolved.
            self._artist_cache = {row[0]: ([], None) for row in
                                  self._enriched(ArtistGenre, ArtistGenre.artist).tuples()}
        albums = list(query)
        self._track_artists = self._track_artists_by_album()

        self._emit(total=len(albums), message="Gathering albums to enrich...")

        unreachable = 0
        for processed, album in enumerate(albums, start=1):
            if not self._settings.get("enable_genre_enrichment"):
                # Setting turned off mid-run; stop rather than keep working on
                # a feature the user just disabled.
                self._emit(status="idle", message="Stopped - disabled in settings")
                return
            if self.should_stop():
                self._emit(status="idle", message="Stopped")
                return
            try:
                self._enrich_album(album, api_key)
                unreachable = 0
            except LookupFailed as e:
                # The source is down, not this album's fault. It stays
                # un-enriched, so the next run picks it up again.
                unreachable += 1
                logger.info("Deferring album %s: %s unreachable", album.id, e)
                if unreachable >= _UNREACHABLE_LIMIT:
                    # Everything is failing; stop rather than grind through the
                    # library hammering a source that is already struggling.
                    self._emit(status="error",
                               message="Genre source unreachable - will retry later")
                    return
            except Exception as e:
                # One album failing (a bad response or a locked DB) must not
                # abort the rest; it stays un-enriched and the next run retries.
                logger.warning("Genre enrichment failed for album %s: %s", album.id, e)
            self._emit(processed=processed, message=f"Enriching genres: {album.title}")

        self._artist_cache.clear()
        self._track_artists.clear()
        self._emit(status="complete", message=f"Enriched genres for {len(albums)} albums")

    @staticmethod
    def _enriched(model, column):
        """Query selecting entity ids that already carry a musicbrainz/lastfm
        genre link."""
        return model.select(column).where(model.source << ("musicbrainz", "lastfm")).distinct()

    @staticmethod
    def _track_artists_by_album() -> dict[str, set]:
        """album id -> artist ids credited on its tracks, in one query rather
        than one per album."""
        by_album: dict[str, set] = {}
        for album_id, artist_id in (Track
                                    .select(Track.album, Track.artist)
                                    .distinct().tuples()):
            by_album.setdefault(album_id, set()).add(artist_id)
        return by_album

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

        self._get_artist_genres(artist, api_key)

        # On a compilation the tracks' own artists differ from the album's
        # nominal one, so nothing else in the library would ever look them up.
        track_artist_ids = self._track_artists.get(album.id, set()) - {artist.id}
        pending = [aid for aid in track_artist_ids if aid not in self._artist_cache]
        if pending:
            for track_artist in Artist.select().where(Artist.id << pending):
                self._get_artist_genres(track_artist, api_key)

        # Written last, and only once every lookup above succeeded: the album's
        # genre links are what marks it enriched, so writing them earlier would
        # strand its artists un-enriched if a later lookup failed.
        if album_genres:
            self._link_album(album.id, album_genres, album_source)

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
