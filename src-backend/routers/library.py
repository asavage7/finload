"""Library browsing: artist/album/track listings, detail pages, ratings, lyrics."""
import datetime
import json
import random
from collections import Counter, defaultdict

from fastapi import APIRouter, Body, HTTPException
from peewee import JOIN, fn

from core import state
from core.database import (Album, AlbumGenre, Artist, ArtistGenre, Genre, PlayHistory, Track,
                      TrackLyrics, track_scope_clause)
from services import discovery

router = APIRouter()

# Similar-artist candidates ranked before the guest-credit/duplicate-name filters
# in _similar_artists thin the list down to the caller's cap.
_SIMILAR_ARTIST_POOL = 60


def _library_scope():
    """Current library scope when using Jellyfin."""
    return track_scope_clause(state.settings.get("jellyfin_library_ids"))


# Genre Lookups

def _best_genres(rows: list[dict], cap: int) -> list[dict]:
    """Dedup (by genre id) to the highest weight row per genre."""
    best: dict[str, dict] = {}
    for row in rows:
        gid = row["id"]
        if gid not in best or row["weight"] > best[gid]["weight"]:
            best[gid] = row
    ordered = sorted(best.values(), key=lambda g: (-g["weight"], g["name"]))
    return [{"id": g["id"], "name": g["name"]} for g in ordered[:cap]]


def _album_genres(album_id: str, cap: int = 8) -> list[dict]:
    rows = list(AlbumGenre
                .select(Genre.id.alias("id"), Genre.name.alias("name"), AlbumGenre.weight.alias("weight"))
                .join(Genre, on=(AlbumGenre.genre == Genre.id))
                .where(AlbumGenre.album == album_id)
                .dicts())
    return _best_genres(rows, cap)


def _artist_genres(artist_id: str, cap: int = 8) -> list[dict]:
    """Combine genres from artist page and their albums for discovery."""
    rows = list(ArtistGenre
                .select(Genre.id.alias("id"), Genre.name.alias("name"), ArtistGenre.weight.alias("weight"))
                .join(Genre, on=(ArtistGenre.genre == Genre.id))
                .where(ArtistGenre.artist == artist_id)
                .dicts())

    album_ids = [a.id for a in Album.select(Album.id).where(Album.artist == artist_id)]
    if album_ids:
        rows += list(AlbumGenre
                     .select(Genre.id.alias("id"), Genre.name.alias("name"), AlbumGenre.weight.alias("weight"))
                     .join(Genre, on=(AlbumGenre.genre == Genre.id))
                     .where(AlbumGenre.album << album_ids)
                     .dicts())

    return _best_genres(rows, cap)


def _weighted_ids(rows: list[dict]) -> list[str]:
    """rows: [{'eid': ..., 'w': ...}, ...] -> distinct ids, best weight first."""
    best: dict[str, int] = {}
    for row in rows:
        best[row["eid"]] = max(best.get(row["eid"], -1), row["w"])
    return [eid for eid, _ in sorted(best.items(), key=lambda kv: -kv[1])]


def _all_album_ids_for_genre(genre_id: str) -> list[str]:
    rows = list(AlbumGenre
                .select(AlbumGenre.album.alias("eid"), AlbumGenre.weight.alias("w"))
                .where(AlbumGenre.genre == genre_id)
                .dicts())
    return _weighted_ids(rows)


def _track_ids_for_genre(genre_id: str, cap: int | None = None) -> list[str]:
    """Returns all tracks for a genre in random order."""
    album_ids = _all_album_ids_for_genre(genre_id)
    if not album_ids:
        return []

    query = Track.select(Track.id, Track.album, Track.disc_number, Track.track_number).where(Track.album << album_ids)
    scope = _library_scope()
    if scope is not None:
        query = query.where(scope)
    track_ids: list[str] = []
    for t in query:
        track_ids.append(t.id)

    random.shuffle(track_ids)
    return track_ids[:cap] if cap is not None else track_ids


def _all_artist_ids_for_genre(genre_id: str) -> list[str]:
    """Find all artists/album artists for a given genre."""
    rows = list(ArtistGenre
                .select(ArtistGenre.artist.alias("eid"), ArtistGenre.weight.alias("w"))
                .where(ArtistGenre.genre == genre_id)
                .dicts())
    ordered = _weighted_ids(rows)

    album_ids = _all_album_ids_for_genre(genre_id)
    if album_ids:
        seen = set(ordered)
        for row in Album.select(Album.id, Album.artist).where(Album.id << album_ids):
            if row.artist_id not in seen:
                ordered.append(row.artist_id)
                seen.add(row.artist_id)
    return ordered


# Library Sorting

_ARTIST_SORTS = {
    "name": Artist.name.collate("NOCASE"),
    "album_count": fn.COUNT(Album.id.distinct()),
    "duration_ms": fn.SUM(Track.duration_ms),
}
_ALBUM_SORTS = {
    "title": Album.title.collate("NOCASE"),
    "artist": Artist.name.collate("NOCASE"),
    "release_year": Album.release_year,
    "rating": Album.rating,
    "track_count": fn.COUNT(Track.id),
    "duration_ms": fn.SUM(Track.duration_ms),
}
_TRACK_SORTS = {
    "title": Track.title.collate("NOCASE"),
    "artist": Artist.name.collate("NOCASE"),
    "duration_ms": Track.duration_ms,
    "rating": Track.rating,
}


def apply_sort_and_page(query, sorts: dict, sort_by: str, sort_order: str,
                        default: str, secondary,
                        start_index: int | None, end_index: int | None):
    """Order a listing query by a sort mode and page it for virtualization."""
    expr = sorts.get(sort_by, sorts[default])
    primary = expr.asc() if sort_order == "asc" else expr.desc()
    query = query.order_by(primary, secondary.asc())
    if start_index is not None and end_index is not None:
        query = query.offset(start_index).limit(end_index - start_index + 1)
    return query


@router.get("/api/artists/count")
def get_artists_count():
    return {"count": _artists_with_aggregates().count()}


def _serialize_artist_row(a) -> dict:
    """Common artist-list-item shape."""
    return {
        "id": a.id,
        "name": a.name,
        "album_count": a.album_count or 0,
        "duration_ms": a.total_ms or 0,
    }


def _artists_with_aggregates(where_clause=None, limit: int | None = None, order_by=None):
    """Artist query joined with Album/Track count/duration aggregates."""
    query = (Artist.select(
                 Artist,
                 fn.COUNT(Album.id.distinct()).alias("album_count"),
                 fn.SUM(Track.duration_ms).alias("total_ms"),
             )
             .join(Album, JOIN.LEFT_OUTER, on=(Album.artist == Artist.id))
             .join(Track, JOIN.LEFT_OUTER, on=(Track.album == Album.id))
             .group_by(Artist.id))
    scope = _library_scope()
    if scope is not None:
        where_clause = scope if where_clause is None else (where_clause & scope)
    if where_clause is not None:
        query = query.where(where_clause)
    if order_by is not None:
        query = query.order_by(order_by)
    if limit is not None:
        query = query.limit(limit)
    return query


@router.get("/api/artists")
def get_artists(sort_by: str = "name", sort_order: str = "asc",
                start_index: int | None = None, end_index: int | None = None):
    artists = _artists_with_aggregates()
    artists = apply_sort_and_page(artists, _ARTIST_SORTS, sort_by, sort_order,
                                  "name", Artist.name.collate("NOCASE"),
                                  start_index, end_index)
    return [_serialize_artist_row(a) for a in artists]


@router.get("/api/albums/count")
def get_albums_count():
    return {"count": _albums_with_aggregates().count()}


def _serialize_album_row(a) -> dict:
    """Common album-list-item shape.Expects the album/track aggregates already selected/joined (track_count, total_ms).
    """
    return {
        "id": str(a.id),
        "title": str(a.title),
        "artist_name": str(a.artist.name) if a.artist else "Unknown Artist",
        "artist_id": str(a.artist.id) if a.artist else None,
        "release_year": a.release_year,
        "rating": a.rating,
        "track_count": a.track_count or 0,
        "duration_ms": a.total_ms or 0,
    }


def _serialize_track_row(t) -> dict:
    """Common track-list-item shape. Expects the track pre-joined with Album and Artist."""
    return {
        "id": str(t.id),
        "album_id": str(t.album.id),
        "title": str(t.title),
        "artist_name": str(t.artist.name),
        "album_title": str(t.album.title),
        "rating": t.rating,
        "duration_ms": t.duration_ms,
    }


def _tracks_with_relations(where_clause=None, limit: int | None = None):
    """Track query joined with Album and Artist."""
    query = Track.select(Track, Album, Artist).join(Album).switch(Track).join(Artist)
    scope = _library_scope()
    if scope is not None:
        where_clause = scope if where_clause is None else (where_clause & scope)
    if where_clause is not None:
        query = query.where(where_clause)
    if limit is not None:
        query = query.limit(limit)
    return query


def _albums_with_aggregates(where_clause=None, limit: int | None = None, order_by=None):
    """Album query joined with Artist and Track count/duration
    aggregates."""
    query = (Album.select(
                 Album, Artist,
                 fn.COUNT(Track.id).alias("track_count"),
                 fn.SUM(Track.duration_ms).alias("total_ms"),
             )
             .join(Artist)
             .switch(Album)
             .join(Track, JOIN.LEFT_OUTER, on=(Track.album == Album.id))
             .group_by(Album.id))
    scope = _library_scope()
    if scope is not None:
        where_clause = scope if where_clause is None else (where_clause & scope)
    if where_clause is not None:
        query = query.where(where_clause)
    if order_by is not None:
        query = query.order_by(order_by)
    if limit is not None:
        query = query.limit(limit)
    return query


def _serialize_albums_in_order(album_ids: list[str]) -> list[dict]:
    """Bulk-fetch + serialize albums, preserving the album_ids order."""
    if not album_ids:
        return []
    by_id = {a.id: a for a in _albums_with_aggregates(where_clause=(Album.id << album_ids))}
    return [_serialize_album_row(by_id[aid]) for aid in album_ids if aid in by_id]


def _serialize_artists_in_order(artist_ids: list[str]) -> list[dict]:
    """Same as _serialize_albums_in_order, for artists."""
    if not artist_ids:
        return []
    by_id = {a.id: a for a in _artists_with_aggregates(where_clause=(Artist.id << artist_ids))}
    return [_serialize_artist_row(by_id[aid]) for aid in artist_ids if aid in by_id]


@router.get("/api/albums")
def get_albums(sort_by: str = "title", sort_order: str = "asc",
               start_index: int | None = None, end_index: int | None = None):
    albums = _albums_with_aggregates()
    albums = apply_sort_and_page(albums, _ALBUM_SORTS, sort_by, sort_order,
                                 "title", Album.title.collate("NOCASE"),
                                 start_index, end_index)
    return [_serialize_album_row(a) for a in albums]


def _more_by_artist(album_id: str, artist_id: str | None, cap: int = 20) -> list[dict]:
    """Other albums by the same artist, most recent first."""
    if not artist_id:
        return []
    albums = _albums_with_aggregates(
        where_clause=(Album.artist == artist_id) & (Album.id != album_id),
        order_by=Album.release_year.desc(),
        limit=cap,
    )
    return [_serialize_album_row(a) for a in albums]


def _similar_albums(album_id: str, exclude_artist_id: str | None, cap: int = 20,
                     index=None) -> list[dict]:
    """Albums most similar to this one, best first (see discovery.similar_albums)."""
    ordered_ids = discovery.similar_albums(
        album_id, cap=cap, exclude_artist_id=exclude_artist_id,
        library_ids=state.settings.get("jellyfin_library_ids"), index=index,
    )
    return _serialize_albums_in_order(ordered_ids)


def _appears_on_albums(artist_id: str, cap: int = 20) -> list[dict]:
    """Albums where this artist is credited on at least one track, mostly compilations."""
    query = (Track.select(Track.album)
             .join(Album, on=(Track.album == Album.id))
             .where(Track.artist == artist_id, Album.artist != artist_id))
    scope = _library_scope()
    if scope is not None:
        query = query.where(scope)
    album_ids = list(query.distinct().scalars())
    if not album_ids:
        return []
    albums = _albums_with_aggregates(
        where_clause=(Album.id << album_ids), order_by=Album.release_year.desc(), limit=cap
    )
    return [_serialize_album_row(a) for a in albums]


def _similar_artists(artist_id: str, cap: int = 20, index=None) -> list[dict]:
    """Artists most similar to this one, best first (see discovery.similar_artists).

    Ranked over the whole library rather than capped up front, because the two
    filters below reject candidates after scoring: an artist credited only as a
    guest (no albums of their own) isn't really a similar artist, and providers
    mint several ids for one name often enough that the row would otherwise
    repeat the same artist.
    """
    ordered_ids = discovery.similar_artists(
        artist_id, cap=_SIMILAR_ARTIST_POOL,
        library_ids=state.settings.get("jellyfin_library_ids"), index=index,
    )
    if not ordered_ids:
        return []
    artists_by_id = {a.id: a for a in _artists_with_aggregates(where_clause=(Artist.id << ordered_ids))}

    results: list[dict] = []
    seen_names: set[str] = set()
    for aid in ordered_ids:
        artist = artists_by_id.get(aid)
        if not artist or not artist.album_count:
            continue
        key = artist.name.strip().lower()
        if key in seen_names:
            continue
        seen_names.add(key)
        results.append(_serialize_artist_row(artist))
        if len(results) >= cap:
            break
    return results


@router.get("/api/tracks/count")
def get_tracks_count():
    query = Track.select()
    scope = _library_scope()
    if scope is not None:
        query = query.where(scope)
    return {"count": query.count()}


@router.get("/api/tracks")
def get_tracks(sort_by: str = "title", sort_order: str = "asc",
               start_index: int | None = None, end_index: int | None = None):
    tracks = Track.select(Track, Album, Artist).join(Album).join(Artist)
    scope = _library_scope()
    if scope is not None:
        tracks = tracks.where(scope)
    tracks = apply_sort_and_page(tracks, _TRACK_SORTS, sort_by, sort_order,
                                 "title", Track.title.collate("NOCASE"),
                                 start_index, end_index)
    return [
        {
            "id": str(t.id),
            "album_id": str(t.album.id),
            "title": str(t.title),
            "artist_name": str(t.artist.name),
            "album_title": str(t.album.title),
            "rating": t.rating,
            "duration_ms": t.duration_ms,
        }
        for t in tracks
    ]


# Homepage

_HOME_ROW_LIMIT = 20
_MIN_SMART_ROW_ITEMS = 3 # A pool must have at least this many items to become a row
_MIN_GENRE_ROW_ARTISTS = 4 # A genre_affinity track row must span more than this many artists to be considered "diverse" enough to show
_AFFINITY_WINDOW = 200 # How many plays to look back when creating history-based rows.
_AFFINITY_MIN_LIBRARY_TRACKS = 125  # Suppress discovery UI when the library doesn't have a lot of tracks.
_HERO_CANDIDATE_MIN = 5
_HERO_CANDIDATE_MAX = 8


def _recent_play_ids(group_field) -> list[str]:
    """Distinct ids for group_field."""
    query = (PlayHistory
             .select(group_field.alias("eid"), fn.MAX(PlayHistory.played_at).alias("w"))
             .join(Track, on=(PlayHistory.track == Track.id)))
    scope = _library_scope()
    if scope is not None:
        query = query.where(scope)
    rows = list(query
                .group_by(group_field)
                .order_by(fn.MAX(PlayHistory.played_at).desc())
                .limit(_HOME_ROW_LIMIT)
                .dicts())
    return [row["eid"] for row in rows]


def _recently_added_album_ids(limit: int = _HOME_ROW_LIMIT) -> list[str]:
    """Album ids ordered by MAX(Track.added_at) descending."""
    query = Track.select(Track.album.alias("eid"), fn.MAX(Track.added_at).alias("w")).where(
        Track.added_at.is_null(False))
    scope = _library_scope()
    if scope is not None:
        query = query.where(scope)
    rows = list(query
                .group_by(Track.album)
                .order_by(fn.MAX(Track.added_at).desc(), Track.album)
                .limit(limit)
                .dicts())
    return [row["eid"] for row in rows]


def _weighted_recent_ids(id_field, k: int, window: int = _AFFINITY_WINDOW) -> list[str]:
    """Up to k distinct ids (artist or album, via id_field), weighted based on number of plays."""
    recent = list(PlayHistory
                  .select(id_field.alias("eid"))
                  .join(Track, on=(PlayHistory.track == Track.id))
                  .where(PlayHistory.visible == True)
                  .order_by(PlayHistory.played_at.desc())
                  .limit(window)
                  .dicts())
    if not recent:
        return []
    counts = Counter(r["eid"] for r in recent)
    ids, weights = list(counts.keys()), list(counts.values())
    picks: list[str] = []
    for _ in range(min(k, len(ids))):
        chosen = random.choices(ids, weights=weights, k=1)[0]
        i = ids.index(chosen)
        ids.pop(i)
        weights.pop(i)
        picks.append(chosen)
    return picks


def _weighted_recent_genres(k: int, window: int = _AFFINITY_WINDOW) -> list[dict]:
    """Up to k distinct genres, weighted based on the number of plays."""
    recent_album_ids = list(dict.fromkeys(
        r["aid"] for r in (PlayHistory
                           .select(Track.album.alias("aid"))
                           .join(Track, on=(PlayHistory.track == Track.id))
                           .where(PlayHistory.visible == True)
                           .order_by(PlayHistory.played_at.desc())
                           .limit(window)
                           .dicts())
    ))
    if not recent_album_ids:
        return []
    rows = list(AlbumGenre
                .select(Genre.id.alias("id"), Genre.name.alias("name"), AlbumGenre.weight.alias("weight"))
                .join(Genre, on=(AlbumGenre.genre == Genre.id))
                .where(AlbumGenre.album << recent_album_ids)
                .dicts())
    if not rows:
        return []
    weight_by_id: dict[str, int] = {}
    name_by_id: dict[str, str] = {}
    for row in rows:
        weight_by_id[row["id"]] = weight_by_id.get(row["id"], 0) + max(row["weight"], 1)
        name_by_id[row["id"]] = row["name"]
    ids, weights = list(weight_by_id.keys()), list(weight_by_id.values())
    picks: list[dict] = []
    for _ in range(min(k, len(ids))):
        chosen = random.choices(ids, weights=weights, k=1)[0]
        i = ids.index(chosen)
        ids.pop(i)
        weights.pop(i)
        picks.append({"id": chosen, "name": name_by_id[chosen]})
    return picks


def _similar_artist_albums(seed_artist_id: str, cap: int, index=None) -> list[dict]:
    """One representative album per similar artist."""
    albums: list[dict] = []
    for artist_row in _similar_artists(seed_artist_id, cap=cap, index=index):
        candidates = _albums_with_aggregates(
            where_clause=(Album.artist == artist_row["id"]), order_by=fn.Random(), limit=1
        )
        for a in candidates:
            albums.append(_serialize_album_row(a))
        if len(albums) >= cap:
            break
    return albums


def _artist_affinity_seed_album(seed_artist_id: str, exclude_ids: set, index=None) -> dict | None:
    """Select a seed album for an artist, same as above."""
    for artist_row in _similar_artists(seed_artist_id, cap=5, index=index):
        albums = _albums_with_aggregates(
            where_clause=(Album.artist == artist_row["id"]), order_by=fn.Random(), limit=1
        )
        for a in albums:
            if a.id not in exclude_ids:
                return _serialize_album_row(a)
    return None


def _row(reason_kind: str, title: str, item_type: str, items: list[dict]) -> dict:
    return {"reason_kind": reason_kind, "title": title, "item_type": item_type, "items": items}


def _hero_candidate(reason_kind: str, reason_label: str, album: dict) -> dict:
    return {"reason_kind": reason_kind, "reason_label": reason_label, "item": album}


@router.get("/api/home")
def get_home():
    """Generate the home page content.
    Recently Played and Recently Added always first, then as many "smart" rows as the library can support.
    """
    _home_tracks_query = Track.select()
    _home_scope = _library_scope()
    if _home_scope is not None:
        _home_tracks_query = _home_tracks_query.where(_home_scope)
    total_tracks = _home_tracks_query.count()

    recently_played_albums = _serialize_albums_in_order(_recent_play_ids(Track.album))
    recently_played_artists = _serialize_artists_in_order(_recent_play_ids(Track.artist))
    recently_added_albums = _serialize_albums_in_order(_recently_added_album_ids())

    top_rated_albums = [
        _serialize_album_row(a) for a in _albums_with_aggregates(
            where_clause=(Album.rating > 0), order_by=Album.rating.desc(), limit=_HOME_ROW_LIMIT
        )
    ]

    random_albums_query = Album.select(Album.id)
    if _home_scope is not None:
        random_albums_query = random_albums_query.join(Track, JOIN.LEFT_OUTER).where(_home_scope).distinct()
    random_album_ids = [a.id for a in random_albums_query.order_by(fn.Random()).limit(_HOME_ROW_LIMIT)]
    random_albums = _serialize_albums_in_order(random_album_ids)

    # Affinity pools
    artist_affinity_rows: list[dict] = []
    artist_affinity_names: dict[str, str] = {}
    genre_affinity_rows: list[dict] = []
    genre_hero_albums: list[tuple[str, str, dict]] = []
    entity_index = None
    if total_tracks >= _AFFINITY_MIN_LIBRARY_TRACKS:
        # One index for every affinity comparison below (see
        # discovery.load_entity_index): building it is the whole cost, and this
        # endpoint would otherwise rebuild it once per seed artist.
        entity_index = discovery.load_entity_index(state.settings.get("jellyfin_library_ids"))
        for seed_artist_id in _weighted_recent_ids(Track.artist, k=3):
            similar_albums = _similar_artist_albums(seed_artist_id, cap=_HOME_ROW_LIMIT,
                                                    index=entity_index)
            if len(similar_albums) < _MIN_SMART_ROW_ITEMS:
                continue
            seed_artist = Artist.get_or_none(Artist.id == seed_artist_id)
            if not seed_artist:
                continue
            artist_affinity_names[seed_artist_id] = seed_artist.name
            artist_affinity_rows.append(_row(
                "artist_affinity", f"Because You've Listened to {seed_artist.name}", "album", similar_albums
            ))

        for genre in _weighted_recent_genres(k=2):
            track_ids = _track_ids_for_genre(genre["id"], cap=_HOME_ROW_LIMIT)
            if len(track_ids) < _MIN_SMART_ROW_ITEMS:
                continue
            tracks_by_id = {t.id: t for t in _tracks_with_relations(where_clause=(Track.id << track_ids))}
            genre_tracks = [
                _serialize_track_row(tracks_by_id[tid]) for tid in track_ids if tid in tracks_by_id
            ]
            if len(genre_tracks) < _MIN_SMART_ROW_ITEMS:
                continue
            if len({t["artist_name"] for t in genre_tracks}) <= _MIN_GENRE_ROW_ARTISTS:
                continue
            title = f"Because You've Listened to {genre['name']}"
            genre_affinity_rows.append(_row("genre_affinity", title, "track", genre_tracks))

            genre_album_ids = _all_album_ids_for_genre(genre["id"])
            if genre_album_ids:
                top_album = _albums_with_aggregates(where_clause=(Album.id == genre_album_ids[0]), limit=1)
                for a in top_album:
                    genre_hero_albums.append(("genre_affinity", title, _serialize_album_row(a)))

    rows = [
        _row("recently_played", "Recently Played", "album", recently_played_albums),
        _row("recently_added", "Recently Added", "album", recently_added_albums),
    ]

    candidate_rows = list(artist_affinity_rows) + list(genre_affinity_rows)
    if len(top_rated_albums) >= _MIN_SMART_ROW_ITEMS:
        candidate_rows.append(_row("top_rated", "Top Rated", "album", top_rated_albums))
    if len(recently_played_artists) >= _MIN_SMART_ROW_ITEMS:
        candidate_rows.append(_row("artist_activity", "Artists You've Been Playing", "artist", recently_played_artists))
    if len(random_albums) >= _MIN_SMART_ROW_ITEMS:
        candidate_rows.append(_row("random", "Random Picks", "album", random_albums))

    random.shuffle(candidate_rows)
    smart_row_count = max(3, min(8, 3 + total_tracks // 300))
    rows.extend(candidate_rows[:smart_row_count])

    hero_pool: list[tuple[str, str, dict]] = []
    for a in recently_played_albums[:2]:
        hero_pool.append(("recently_played", "Recently Played", a))
    for a in recently_added_albums[:2]:
        hero_pool.append(("recently_added", "Recently Added", a))
    for a in top_rated_albums[:2]:
        stars = a["rating"]
        hero_pool.append(("top_rated", f"Rated {stars} Star{'s' if stars != 1 else ''}", a))

    seen_so_far = {a["id"] for _, _, a in hero_pool}
    for seed_artist_id, seed_name in artist_affinity_names.items():
        seed_album = _artist_affinity_seed_album(seed_artist_id, seen_so_far, index=entity_index)
        if seed_album:
            hero_pool.append(("artist_affinity", f"Because You've Listened to {seed_name}", seed_album))
            seen_so_far.add(seed_album["id"])
    for kind, label, album in genre_hero_albums:
        if album["id"] not in seen_so_far:
            hero_pool.append((kind, label, album))
            seen_so_far.add(album["id"])
    for a in random_albums[:2]:
        hero_pool.append(("random", "Random Picks", a))

    random.shuffle(hero_pool)
    hero_candidates: list[dict] = []
    seen_ids: set = set()
    for kind, label, album in hero_pool:
        if album["id"] in seen_ids or len(hero_candidates) >= _HERO_CANDIDATE_MAX:
            continue
        seen_ids.add(album["id"])
        hero_candidates.append(_hero_candidate(kind, label, album))

    # Pad to the minimum with further random picks if the reasoned pools were sparse.
    if len(hero_candidates) < _HERO_CANDIDATE_MIN:
        for a in random_albums:
            if len(hero_candidates) >= _HERO_CANDIDATE_MIN or a["id"] in seen_ids:
                continue
            seen_ids.add(a["id"])
            hero_candidates.append(_hero_candidate("random", "Random Pick", a))

    return {"hero_candidates": hero_candidates, "rows": rows}


# Detail Pages

@router.get("/api/album/{album_id}")
def get_album_details(album_id: str):
    album = Album.get_or_none(Album.id == album_id)
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    tracks_query = (Track.select(Track, Artist)
                    .join(Artist, on=(Track.artist == Artist.id))
                    .where(Track.album == album_id))
    scope = _library_scope()
    if scope is not None:
        tracks_query = tracks_query.where(scope)
    tracks_query = tracks_query.order_by(Track.disc_number, Track.track_number)

    discs_map = defaultdict(list)
    for t in tracks_query:
        d_num = t.disc_number if (t.disc_number and t.disc_number > 0) else 1
        discs_map[d_num].append({
            "id": t.id,
            "title": t.title,
            "track_number": t.track_number,
            "duration_ms": t.duration_ms,
            "artist_name": t.artist.name if t.artist else "Unknown Artist",
            "rating": t.rating,
        })

    artist_id = album.artist.id if album.artist else None

    return {
        "album": {
            "id": album.id,
            "title": album.title,
            "artist_name": album.artist.name if album.artist else "Unknown Artist",
            "artist_id": artist_id,
            "release_year": album.release_year,
            "rating": album.rating,
            "genres": _album_genres(album.id),
        },
        "discs": [{"disc_number": d, "tracks": discs_map[d]} for d in sorted(discs_map)],
        "more_by_artist": _more_by_artist(album.id, artist_id),
        "similar_albums": _similar_albums(album.id, artist_id),
    }


@router.get("/api/album/{album_id}/tracks")
def get_album_tracks(album_id: str):
    tracks = (Track.select(Track, Artist)
              .join(Artist, on=(Track.artist == Artist.id))
              .where(Track.album == album_id))
    scope = _library_scope()
    if scope is not None:
        tracks = tracks.where(scope)
    tracks = tracks.order_by(Track.disc_number, Track.track_number)
    return [
        {
            "id": t.id,
            "title": t.title,
            "track_number": t.track_number,
            "disc_number": t.disc_number,
            "duration_ms": t.duration_ms,
            "rating": t.rating,
            "artist_name": t.artist.name if t.artist else "Unknown Artist",
        }
        for t in tracks
    ]


@router.get("/api/artist/{artist_id}")
def get_artist_details(artist_id: str):
    artist = Artist.get_or_none(Artist.id == artist_id)
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")
    
    artist_albums = list(_albums_with_aggregates(
        where_clause=(Album.artist == artist.id), order_by=Album.release_year.desc()
    ))

    scope = _library_scope()
    tracks_count_query = Track.select(fn.COUNT(Track.id)).where(Track.artist == artist.id)
    total_duration_query = Track.select(fn.SUM(Track.duration_ms)).where(Track.artist == artist.id)
    if scope is not None:
        tracks_count_query = tracks_count_query.where(scope)
        total_duration_query = total_duration_query.where(scope)
    tracks_count = tracks_count_query.scalar() or 0
    total_duration_ms = total_duration_query.scalar() or 0

    return {
        "artist": {
            "id": artist.id,
            "name": artist.name,
            "bio": artist.bio,
            "albums_count": len(artist_albums),
            "tracks_count": tracks_count,
            "total_duration_ms": total_duration_ms,
            "genres": _artist_genres(artist.id),
        },
        "albums": [
            {
                "id": str(a.id),
                "title": str(a.title),
                "duration_ms": a.total_ms or 0,
                "release_year": a.release_year,
                "rating": a.rating,
            }
            for a in artist_albums
        ],
        "appears_on": _appears_on_albums(artist.id),
        "similar_artists": _similar_artists(artist.id),
    }


@router.get("/api/artist/{artist_id}/tracks")
def get_artist_tracks(artist_id: str):
    artist = Artist.get_or_none(Artist.id == artist_id)
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")
    tracks = (Track.select(Track, Album, Artist)
              .join(Album).switch(Track).join(Artist)
              .where(Track.artist == artist.id))
    scope = _library_scope()
    if scope is not None:
        tracks = tracks.where(scope)
    tracks = tracks.order_by(Album.release_year, Track.disc_number, Track.track_number)
    return [
        {
            "id": t.id,
            "title": t.title,
            "album_title": t.album.title if t.album else "Unknown Album",
            "duration_ms": t.duration_ms,
            "rating": t.rating,
            "artist_name": t.artist.name if t.artist else "Unknown Artist",
        }
        for t in tracks
    ]
    
@router.get("/api/genre/{genre_id}")
def get_genre_details(genre_id: str):
    """Everything tagged with one genre: albums/artists/tracks, best (highest
    tag weight) first. Capped per section — this feeds carousels, not a full
    paginated browse list."""
    genre = Genre.get_or_none(Genre.id == genre_id)
    if not genre:
        raise HTTPException(status_code=404, detail="Genre not found")

    album_ids = _all_album_ids_for_genre(genre_id)
    artist_ids = _all_artist_ids_for_genre(genre_id)
    track_ids = _track_ids_for_genre(genre_id, cap=30)

    albums_by_id = {
        a.id: a for a in _albums_with_aggregates(where_clause=(Album.id << album_ids))
    } if album_ids else {}

    artists_by_id = {
        a.id: a for a in _artists_with_aggregates(where_clause=(Artist.id << artist_ids))
    } if artist_ids else {}

    tracks_by_id = {
        t.id: t for t in _tracks_with_relations(where_clause=(Track.id << track_ids))
    } if track_ids else {}

    return {
        "genre": {"id": genre.id, "name": genre.name},
        "albums": [
            _serialize_album_row(a)
            for a in (albums_by_id[aid] for aid in album_ids if aid in albums_by_id)
        ],
        "artists": [
            {
                "id": a.id,
                "name": a.name,
                "album_count": a.album_count or 0,
                "duration_ms": a.total_ms or 0,
            }
            for a in (artists_by_id[aid] for aid in artist_ids if aid in artists_by_id)
        ],
        "tracks": [
            _serialize_track_row(tracks_by_id[tid]) for tid in track_ids if tid in tracks_by_id
        ],
        "queue_track_ids": _track_ids_for_genre(genre_id, cap=50),
        "all_track_ids": _track_ids_for_genre(genre_id),
    }


# Ratings

def _get_rated_item(model, item_id: str, label: str):
    item = model.get_or_none(model.id == item_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return item


def _set_rating(item, rating: int):
    if not (0 <= rating <= 5):
        raise HTTPException(status_code=422, detail="Rating must be between 0 and 5")
    item.rating = rating
    item.save()
    return {"rating": item.rating}


@router.get("/api/album/{album_id}/rating")
def get_album_rating(album_id: str):
    return {"rating": _get_rated_item(Album, album_id, "Album").rating}


@router.patch("/api/album/{album_id}/rating")
def update_album_rating(album_id: str, rating: int = Body(..., embed=True)):
    return _set_rating(_get_rated_item(Album, album_id, "Album"), rating)


@router.get("/api/track/{track_id}/rating")
def get_track_rating(track_id: str):
    return {"rating": _get_rated_item(Track, track_id, "Track").rating}


@router.patch("/api/track/{track_id}/rating")
def update_track_rating(track_id: str, rating: int = Body(..., embed=True)):
    return _set_rating(_get_rated_item(Track, track_id, "Track"), rating)


# Lyrics

@router.get("/api/track/{track_id}/lyrics")
def get_track_lyrics(track_id: str, force: bool = False):
    if not force:
        cached = TrackLyrics.get_or_none(TrackLyrics.track == track_id)
        if cached:
            if cached.lyrics_type == "synced":
                return {"type": "synced", "lines": json.loads(cached.content)}
            if cached.lyrics_type == "unsynced":
                return {"type": "unsynced", "text": cached.content}
            return {"type": "none"}

    result = state.provider.get_lyrics(
        track_id,
        lrclib_enabled=state.settings.get("enable_lrclib_lyrics"),
        synced_enabled=state.settings.get("enable_synced_lyrics"),
    )

    content = None
    if result["type"] == "synced":
        content = json.dumps(result["lines"])
    elif result["type"] == "unsynced":
        content = result.get("text")

    (TrackLyrics.insert(
        track=track_id,
        lyrics_type=result["type"],
        content=content,
        fetched_at=datetime.datetime.now(),
    ).on_conflict(
        conflict_target=[TrackLyrics.track],
        update={
            TrackLyrics.lyrics_type: result["type"],
            TrackLyrics.content: content,
            TrackLyrics.fetched_at: datetime.datetime.now(),
        },
    ).execute())

    return result
