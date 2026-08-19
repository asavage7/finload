"""Library search: normalization, relevance scoring, and the /api/search route."""
import datetime
import re

from fastapi import APIRouter
from peewee import fn

from database import Album, Artist, SearchHistory, Track, db as peewee_db

router = APIRouter()

# Normalised search text: lowercase, keep only word characters and whitespace
# (an allowlist — so any punctuation, even exotic Unicode like the hyphen in
# "blink‐182", is dropped), then collapse whitespace. Applied to both the query
# and the indexed text so neither side's punctuation can disqualify a match.
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)


def _normalize(text: str) -> str:
    return " ".join(_NON_WORD.sub("", (text or "").lower()).split())


# Expose the exact same normalisation to SQL, so candidate filtering and Python
# scoring agree byte-for-byte. (Applies to the live connection and is re-applied
# to any future ones.)
peewee_db.register_function(_normalize, "finload_normalize", 1)


def _normalized_field(field):
    """SQL counterpart of _normalize, via the registered SQLite function."""
    return fn.finload_normalize(field)


def _search_score(text: str, q: str, tokens: list[str]) -> int:
    """Relevance of a single field value against the query.

    Tiered so better matches always outrank weaker ones regardless of length:
    exact > prefix > word-start > substring > all-tokens-present. A small penalty
    for how deep into the string the match starts breaks ties toward the front.
    `q` and `tokens` are expected pre-normalized; the field text is normalized
    here so punctuation/case never affects the score.
    """
    if not text:
        return 0
    t = _normalize(text)
    if t == q:
        score = 1000
    elif t.startswith(q):
        score = 700
    elif any(word.startswith(q) for word in t.split()):
        score = 500
    elif q in t:
        score = 300
    elif len(tokens) > 1 and all(tok in t for tok in tokens):
        score = 200
    else:
        return 0
    pos = t.find(q)
    if pos > 0:
        score -= min(pos, 50)
    return score


def _item_score(title: str, artist: str, q: str, tokens: list[str]) -> int:
    """Score a title/artist pair."""
    s = max(_search_score(title, q, tokens),
            _search_score(artist, q, tokens) - 150)
    if len(tokens) > 1:
        combined = _normalize(f"{title} {artist}")
        if all(tok in combined for tok in tokens):
            s = max(s, 150)
    return s


def _match_all_tokens(fields, tokens):
    """Candidate filter: every token must appear in at least one of `fields`.

    AND across tokens (so unrelated rows that merely share one common word are
    excluded), OR across fields per token (so a query can span title + artist,
    e.g. "flyleaf sick"). Without the AND, common tokens flood the candidate
    limit with junk and bury the real match before it can be scored.
    """
    norm = [_normalized_field(f) for f in fields]
    clause = None
    for tok in tokens:
        per_token = None
        for nf in norm:
            c = nf.contains(tok)
            per_token = c if per_token is None else (per_token | c)
        clause = per_token if clause is None else (clause & per_token)
    return clause


def _record_search(q: str):
    """Store the query in SearchHistory.

    Typing produces a burst of prefix queries ("f", "fl", "fly", ...), so if
    the newest history row is a prefix of this query (or the other way round,
    for backspacing) it's updated in place instead of adding a new row.
    """
    try:
        last = SearchHistory.select().order_by(SearchHistory.timestamp.desc()).first()
        if last and (q.startswith(last.query) or last.query.startswith(q)):
            last.query = q
            last.timestamp = datetime.datetime.now()
            last.save()
        else:
            SearchHistory.create(query=q)
    except Exception:
        pass


# How many DB candidates to score per entity type. Bounds work while leaving
# plenty of headroom above the handful of results actually returned.
_SEARCH_CANDIDATES = 40


@router.get("/api/search")
def search(q: str = "", limit: int = 5):
    q = _normalize(q)
    if not q:
        return {"results": []}
    _record_search(q)
    limit = max(1, min(limit, 20))
    tokens = q.split()
    scored: list[tuple[int, dict]] = []

    artists = (Artist.select()
               .where(_match_all_tokens([Artist.name], tokens))
               .limit(_SEARCH_CANDIDATES))
    for a in artists:
        s = _search_score(a.name, q, tokens)
        if s > 0:
            scored.append((s, {
                "type": "artist",
                "id": str(a.id),
                "title": str(a.name),
                "subtitle": "Artist",
                "image_id": str(a.id),
                "album_id": None,
            }))

    albums = (Album.select(Album, Artist).join(Artist)
              .where(_match_all_tokens([Album.title, Artist.name], tokens))
              .limit(_SEARCH_CANDIDATES))
    for al in albums:
        s = _item_score(al.title, al.artist.name, q, tokens)
        if s > 0:
            scored.append((s, {
                "type": "album",
                "id": str(al.id),
                "title": str(al.title),
                "subtitle": f"Album ∙ {al.artist.name}",
                "image_id": str(al.id),
                "album_id": str(al.id),
            }))

    tracks = (Track.select(Track, Album, Artist).join(Album).join(Artist)
              .where(_match_all_tokens([Track.title, Artist.name], tokens))
              .limit(_SEARCH_CANDIDATES))
    for t in tracks:
        s = _item_score(t.title, t.artist.name, q, tokens)
        if s > 0:
            scored.append((s, {
                "type": "track",
                "id": str(t.id),
                "title": str(t.title),
                "subtitle": f"Track ∙ {t.artist.name}",
                "image_id": str(t.album.id),
                "album_id": str(t.album.id),
            }))

    # Tiebreak equal scores by type (artist > album > track), then shorter title.
    type_rank = {"artist": 2, "album": 1, "track": 0}
    scored.sort(key=lambda r: (r[0], type_rank[r[1]["type"]], -len(r[1]["title"])),
                reverse=True)
    return {"results": [item for _, item in scored[:limit]]}
