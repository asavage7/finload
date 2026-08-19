from peewee import (
    EXCLUDED,
    BooleanField,
    CharField,
    DateTimeField,
    FloatField,
    ForeignKeyField,
    IntegerField,
    Model,
    SqliteDatabase,
    TextField,
    fn,
)
import datetime
import json
import os
from config import get_database_path
from migrations import SCHEMA_VERSION, ensure_model_columns, run_migrations, stable_genre_id

# WAL mode lets API reads proceed while a library sync writes, avoiding "database
# is locked" stalls during sync — but that only works if each thread has its own
# connection (thread_safe=True, peewee's default). thread_safe=False forces every
# request thread onto one shared sqlite3.Connection with no locking, which is
# unsafe: concurrent queries on it can return wrong/missing rows or raise
# "bad parameter or other API misuse".
_DB_PRAGMAS = {'foreign_keys': 1, 'journal_mode': 'wal'}

db = SqliteDatabase(
    str(get_database_path()),
    pragmas=_DB_PRAGMAS,
)


def _chunks(seq, size):
    """Yield non-empty slices of `seq` of at most `size` items."""
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


class BaseModel(Model):
    class Meta:
        database = db


class SchemaVersion(BaseModel):
    id = IntegerField(primary_key=True)
    version = IntegerField(default=0)


class Artist(BaseModel):
    id = CharField(primary_key=True)
    name = CharField()
    bio = TextField(default="")           # ""=not enriched OR enriched+empty; use enriched_at to distinguish
    bio_source = CharField(default="")    # "theaudiodb"
    tadb_id = CharField(null=True)        # TheAudioDB idArtist for direct future lookups
    enriched_at = DateTimeField(null=True)
    provider = CharField(default="jellyfin")
    mbid = CharField(null=True)           # MusicBrainz Artist ID, when the provider exposes one


class Album(BaseModel):
    id = CharField(primary_key=True)
    title = CharField()
    artist = ForeignKeyField(Artist, backref='albums')
    release_year = IntegerField(default=0)
    rating = IntegerField(default=0)
    provider = CharField(default="jellyfin")
    mbid = CharField(null=True)  # MusicBrainz Release Group ID (aggregates genres across pressings)


class Track(BaseModel):
    id = CharField(primary_key=True)
    title = CharField()
    artist = ForeignKeyField(Artist, backref='tracks')
    album = ForeignKeyField(Album, backref='tracks')
    track_number = IntegerField(default=0)
    disc_number = IntegerField(default=1)
    duration_ms = IntegerField(default=0)
    rating = IntegerField(default=0)
    file_path = CharField(default="")  # absolute path on disk; only set for provider="local"
    provider = CharField(default="jellyfin")
    mbid = CharField(null=True)  # MusicBrainz Recording ID
    # First-seen time, powering "Recently Added". No Python-level default:
    # the column's SQL-level DEFAULT CURRENT_TIMESTAMP (added in _migrate_14)
    # is what makes this populate correctly on insert while staying excluded
    # from _TRACK_PRESERVE_ON_CONFLICT below.
    added_at = DateTimeField(null=True)


# Columns provider sync data actually carries and should overwrite on
# conflict. rating (user data the provider never supplies) and added_at
# (first-seen time) are intentionally excluded so they survive a "changed"
# resync untouched instead of resetting to their schema default.
_TRACK_PRESERVE_ON_CONFLICT = [
    Track.title, Track.artist, Track.album, Track.track_number,
    Track.disc_number, Track.duration_ms, Track.file_path,
    Track.provider, Track.mbid,
]


class Genre(BaseModel):
    """A deduplicated genre/tag name, shared across tracks/albums/artists and sources.

    ``id`` is a deterministic content hash of the (lowercased) name — same
    convention as local-provider Track/Album/Artist ids (see
    ``providers/local.py``'s ``_stable_hash``) — rather than an autoincrement
    integer, so the same genre resolves to the same id on every install.

    Name uniqueness is case-insensitive (see the ``genre_name_nocase`` index
    created in ``_ensure_genre_indexes``) so "Rock" from Jellyfin and "rock"
    from Last.fm resolve to the same row.
    """
    id = CharField(primary_key=True)
    name = CharField()


class AlbumGenre(BaseModel):
    album = ForeignKeyField(Album, backref='genre_links', on_delete='CASCADE')
    genre = ForeignKeyField(Genre, backref='albums', on_delete='CASCADE')
    source = CharField(default="")    # "jellyfin" | "local" | "lastfm" | "musicbrainz"
    weight = IntegerField(default=0)  # e.g. Last.fm tag count; 0 for server/file-tag genres

    class Meta:
        indexes = (
            (('album', 'genre', 'source'), True),
        )


class ArtistGenre(BaseModel):
    """Genres attached directly to an artist (e.g. Last.fm artist-level tags).

    Deliberately not auto-populated from the artist's albums/tracks — an
    artist can span genres that don't apply to every release, so the two stay
    separate at write time. Callers that want "everything this artist touches"
    (e.g. genre browsing) should union this with the artist's album/track
    genres at query time instead of merging storage.
    """
    artist = ForeignKeyField(Artist, backref='genre_links', on_delete='CASCADE')
    genre = ForeignKeyField(Genre, backref='artists', on_delete='CASCADE')
    source = CharField(default="")
    weight = IntegerField(default=0)

    class Meta:
        indexes = (
            (('artist', 'genre', 'source'), True),
        )


class TrackLyrics(BaseModel):
    track = ForeignKeyField(Track, primary_key=True, backref='lyrics', on_delete='CASCADE')
    lyrics_type = CharField()         # "synced" | "unsynced" | "none"
    content = TextField(null=True)    # JSON list for synced, plain str for unsynced, NULL for none
    source = CharField(default="")    # "lrclib" | "jellyfin" | "embedded" | "sidecar"
    fetched_at = DateTimeField(default=datetime.datetime.now)


class TrackFeatures(BaseModel):
    """Cached librosa DSP features for a track (see audio_analysis.py),
    computed once during library analysis and reused by the discovery queue
    builder, so no audio decoding happens on the playback hot path.
    """
    track = ForeignKeyField(Track, primary_key=True, backref='features', on_delete='CASCADE')
    bpm = FloatField(default=0.0)  # its own column: a separate octave-corrected
                                   # scoring term (see discovery.py) and handy for UI
    # JSON: {"mfcc_mean": [13], "mfcc_std": [13], "contrast_mean": [7]}. Timbre
    # mean + variance + spectral contrast; discovery.py concatenates and
    # standardizes these into one distance vector.
    features = TextField(default="")
    # Bumped when the feature set or extractor changes; a row whose version
    # doesn't match audio_analysis.FEATURE_VERSION is treated as stale and
    # re-analyzed. 0 = pre-migration/never-analyzed.
    feature_version = IntegerField(default=0)
    # Mutual Proximity hubness stats (see discovery.py's _mutual_proximity):
    # median/MAD (median absolute deviation) of this track's own
    # distance-to-the-rest-of-the-library distribution, over the standardized
    # feature vector, computed once in a lightweight second pass (pure vector
    # math over cached features, no audio decoding). Tells a genuinely close
    # match apart from a "hub" track that sits deceptively close to a huge
    # fraction of the library regardless of genre. Median/MAD rather than
    # mean/std: a track's real distance distribution is right-skewed, not
    # Gaussian, and mean/std let the long right tail of "far" distances inflate
    # the estimated spread enough to under-correct the very hubs this catches.
    dist_center = FloatField(default=0.0)
    dist_scale = FloatField(default=0.0)
    analyzed_at = DateTimeField(default=datetime.datetime.now)


class Playlist(BaseModel):
    id = CharField(primary_key=True)
    name = CharField()
    description = CharField(default="")
    created_at = DateTimeField(default=datetime.datetime.now)


class PlaylistTrack(BaseModel):
    playlist = ForeignKeyField(Playlist, backref='items', on_delete='CASCADE')
    track = ForeignKeyField(Track, backref='playlists', on_delete='CASCADE')
    position = FloatField()

    class Meta:
        indexes = (
            (('playlist', 'position'), True),
        )


class SearchHistory(BaseModel):
    query = CharField()
    timestamp = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('timestamp',), False),
        )


class PlayHistory(BaseModel):
    track = ForeignKeyField(Track, backref='history')
    played_at = DateTimeField(default=datetime.datetime.now)
    completion_pct = FloatField(default=0.0)
    # False for synthetic rows that don't represent a real listen (e.g.
    # dismissing a not-yet-played radio suggestion, recorded as a soft
    # negative signal for future recommendations — see
    # PlaybackManager.remove_from_queue). Fatigue scoring in discovery.py
    # reads every row regardless; only the user-facing History view filters
    # on this, so a track the user never actually heard never shows up as
    # something they "listened to."
    visible = BooleanField(default=True)
    # True from the moment a track starts playing until it's superseded by
    # the next one (see PlaybackManager._start_history/_finalize_history).
    # completion_pct is only meaningful once this flips to False; recommen-
    # dation code (radio.py's session_context, discovery.py's fatigue scan)
    # excludes in-progress rows so the currently-playing track isn't misread
    # as a 0%-completion skip while it's still being listened to.
    in_progress = BooleanField(default=False)

    class Meta:
        indexes = (
            (('track',), False),
            (('played_at',), False),
        )


class QueueItem(BaseModel):
    # position is a float so reordering uses midpoint insertion (no cascade shifts).
    track = ForeignKeyField(Track, backref='queue')
    position = FloatField(default=0.0)
    queue_type = IntegerField(default=1)  # 0: priority, 1: standard, 2: mix (auto-generated)
    added_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('queue_type', 'position'), False),
        )


class PlaybackState(BaseModel):
    """Singleton (id=1) that tracks what is currently playing and global playback settings."""
    id = IntegerField(primary_key=True)
    current_queue_item = ForeignKeyField(QueueItem, null=True, backref='+', on_delete='SET NULL')
    shuffle = BooleanField(default=False)
    repeat_mode = IntegerField(default=0)  # 0=off, 1=repeat_all, 2=repeat_one
    # Set while a "Start Radio" mix is active; cleared by play_now/clear_queue
    # (anything that replaces or empties the queue ends the radio session).
    # NULL means no radio session is active, so top-ups never fire.
    radio_seed_track = ForeignKeyField(Track, null=True, backref='+', on_delete='SET NULL')


ALL_MODELS = [
    SchemaVersion, Artist, Album, Track, TrackLyrics, TrackFeatures,
    Genre, AlbumGenre, ArtistGenre,
    Playlist, PlaylistTrack,
    SearchHistory, PlayHistory,
    QueueItem, PlaybackState,
]


def _ensure_genre_indexes(db):
    """Case-insensitive uniqueness on Genre.name.

    Peewee's ``Meta.indexes`` can't express ``COLLATE NOCASE`` on an index, so
    this is created directly rather than declared on the model.
    """
    db.execute_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS genre_name_nocase ON genre (name COLLATE NOCASE)"
    )


def switch_database(source: str | None = None) -> "DatabaseManager":
    """Re-point the SQLite connection at the database file for ``source``.

    All Peewee models are bound to the module-level ``db`` object, so calling
    ``db.init`` with a new path swaps the underlying file without touching the
    models. Returns a fresh ``DatabaseManager`` with the schema ensured on the
    new file. No-op (beyond ensuring schema) if already pointed there.
    """
    new_path = str(get_database_path(source))
    current = getattr(db, 'database', None)
    if current and os.path.abspath(current) == os.path.abspath(new_path):
        return DatabaseManager()

    if not db.is_closed():
        db.close()
    db.init(new_path, pragmas=_DB_PRAGMAS)
    return DatabaseManager()


class DatabaseManager:
    """Ensures the schema is current and provides the bulk write path for sync."""

    def __init__(self):
        if db.is_closed():
            db.connect()

        existing_tables = set(db.get_tables())

        if 'artist' not in existing_tables:
            # Fresh database, create everything at the current schema version.
            db.create_tables(ALL_MODELS)
            _ensure_genre_indexes(db)
            SchemaVersion.create(id=1, version=SCHEMA_VERSION)
            return

        # Existing database: run any pending manual migrations first.
        db.create_tables([SchemaVersion], safe=True)
        version_row = SchemaVersion.get_or_none(SchemaVersion.id == 1)
        current_version = version_row.version if version_row else 0

        if current_version < SCHEMA_VERSION:
            with db.atomic():
                run_migrations(db, current_version)
            if version_row:
                version_row.version = SCHEMA_VERSION
                version_row.save()
            else:
                SchemaVersion.create(id=1, version=SCHEMA_VERSION)

        # New tables and new columns on existing models are picked up
        # automatically, so most schema changes need no migration at all.
        db.create_tables(ALL_MODELS, safe=True)
        ensure_model_columns(db, ALL_MODELS)
        _ensure_genre_indexes(db)

    def find_artist_id_by_name(self, name: str) -> str | None:
        """Existing Artist.id whose name matches case-insensitively, or None.

        Used by sync to reconcile an artist's id by name before writing: a
        provider can hand back a different id for the same real-world artist
        depending on whether it was resolved as an album artist or a track
        artist (see providers/jellyfin.py's _yield_items docstring), and
        without this check that becomes a second permanent Artist row for
        the same person.
        """
        row = Artist.get_or_none(Artist.name.collate("NOCASE") == name)
        return row.id if row else None

    def upsert_artist(self, **data):
        Artist.insert(**data).on_conflict(
            conflict_target=[Artist.id],
            preserve=[Artist.name, Artist.mbid]
        ).execute()

    def upsert_album(self, **data):
        Album.insert(**data).on_conflict(
            conflict_target=[Album.id],
            preserve=[Album.title, Album.artist, Album.release_year, Album.provider, Album.mbid]
        ).execute()

    def upsert_track(self, **data):
        # A provider that knows a real "date added" (Jellyfin's DateCreated,
        # a local file's mtime) sets added_at itself; this is just the
        # fallback for one that doesn't. Either way, on conflict (an
        # existing track being resynced) the COALESCE below means: keep the
        # row's existing added_at if it already has one, otherwise take this
        # insert's value — so a legacy row with no first-seen time gets
        # backfilled the next time it's touched by any sync, instead of
        # being reset every time (which plain on_conflict_replace() used to
        # do — see the git history for the rating-reset bug that fixed). No
        # SQL-level column default is used (see migrations.py's _migrate_14)
        # since SQLite rejects a non-constant ADD COLUMN default like
        # CURRENT_TIMESTAMP.
        data.setdefault("added_at", datetime.datetime.now())
        Track.insert(**data).on_conflict(
            conflict_target=[Track.id],
            preserve=_TRACK_PRESERVE_ON_CONFLICT,
            update={Track.added_at: fn.COALESCE(Track.added_at, EXCLUDED.added_at)}
        ).execute()

    def save_track_features(self, track_id: str, bpm: float, mfcc_mean: list,
                             mfcc_std: list, contrast_mean: list) -> None:
        from audio_analysis import FEATURE_VERSION
        TrackFeatures.insert(
            track=track_id, bpm=bpm, feature_version=FEATURE_VERSION,
            features=json.dumps({"mfcc_mean": mfcc_mean, "mfcc_std": mfcc_std,
                                 "contrast_mean": contrast_mean}),
        ).on_conflict_replace().execute()

    def _genre_ids_by_name(self, names: set) -> dict:
        """Resolve (case-insensitively, creating as needed) each name to a Genre id.

        New genres are stored title-cased for consistent display — sources
        are inconsistent (Last.fm/MusicBrainz often return lowercase tags
        like "sludge metal"). An existing row's casing is left as-is. The id
        is a deterministic hash of the lowercased name (see stable_genre_id
        in migrations.py), so it doesn't depend on insertion order/casing.
        """
        genre_ids = {}
        for name in names:
            genre = Genre.get_or_none(Genre.name.collate("NOCASE") == name)
            if genre is None:
                genre = Genre.create(id=stable_genre_id(name), name=name.title())
            genre_ids[name.lower()] = genre.id
        return genre_ids

    def link_genres(self, album_genres: list | None = None, artist_genres: list | None = None):
        """Attach genres to albums/artists.

        Each argument is a list of ``(entity_id, genre_name, source, weight)``
        tuples. Existing (entity, genre, source) links are left alone — this
        only adds new ones, so it's safe to call repeatedly (e.g. once per
        sync, or from genre enrichment) without duplicating or losing another
        source's tags. Note: re-running with a different weight for an
        already-linked (entity, genre, source) triple does not update the
        stored weight (insert-or-ignore, not upsert).
        """
        album_genres = album_genres or []
        artist_genres = artist_genres or []
        names = ({name for _, name, _, _ in album_genres}
                 | {name for _, name, _, _ in artist_genres})
        if not names:
            return
        with db.atomic():
            genre_ids = self._genre_ids_by_name(names)
            for batch in _chunks(album_genres, 200):
                rows = [
                    {"album": eid, "genre": genre_ids[name.lower()], "source": source, "weight": weight}
                    for eid, name, source, weight in batch
                ]
                AlbumGenre.insert_many(rows).on_conflict_ignore().execute()
            for batch in _chunks(artist_genres, 200):
                rows = [
                    {"artist": eid, "genre": genre_ids[name.lower()], "source": source, "weight": weight}
                    for eid, name, source, weight in batch
                ]
                ArtistGenre.insert_many(rows).on_conflict_ignore().execute()

    def delete_tracks(self, track_ids):
        """Delete tracks by id, first clearing the dependents that don't cascade.
        """
        track_ids = list(track_ids)
        if not track_ids:
            return 0
        with db.atomic():
            for batch in _chunks(track_ids, 200):
                PlayHistory.delete().where(PlayHistory.track << batch).execute()
                QueueItem.delete().where(QueueItem.track << batch).execute()
                Track.delete().where(Track.id << batch).execute()
        return len(track_ids)

    def prune_orphans(self):
        """Delete albums with no tracks, then artists with no tracks or albums.

        Removing tracks (e.g. stale tracks during sync) can strand the albums
        and artists that only those tracks pointed at. Order matters: albums are
        pruned first so an artist whose sole album just became empty is caught
        by the artist pass in the same transaction. Genre links fall away on
        their own (AlbumGenre/ArtistGenre use on_delete='CASCADE'). Returns
        ``(albums_removed, artists_removed)``.
        """
        with db.atomic():
            albums_removed = Album.delete().where(
                Album.id.not_in(Track.select(Track.album))
            ).execute()
            artists_removed = Artist.delete().where(
                Artist.id.not_in(Track.select(Track.artist))
                & Artist.id.not_in(Album.select(Album.artist))
            ).execute()
        return albums_removed, artists_removed

    def bulk_upsert(self, artists: list, albums: list, tracks: list,
                     album_genres: list | None = None):
        """Insert/replace many rows in one transaction — the fast path for sync.

        ``album_genres``: optional list of ``(entity_id, genre_name, source,
        weight)`` tuples, see ``link_genres``.
        """
        with db.atomic():
            for batch in _chunks(artists, 100):
                Artist.insert_many(batch).on_conflict(
                    conflict_target=[Artist.id],
                    preserve=[Artist.name, Artist.mbid]
                    # bio, bio_source, tadb_id, enriched_at intentionally not updated
                ).execute()
            for batch in _chunks(albums, 100):
                Album.insert_many(batch).on_conflict(
                    conflict_target=[Album.id],
                    preserve=[Album.title, Album.artist, Album.release_year, Album.provider, Album.mbid]
                ).execute()
            for batch in _chunks(tracks, 100):
                # Same COALESCE first-seen semantics as upsert_track,
                # applied per-batch rather than per-row: tracks synced
                # together (e.g. a newly added album) share one fallback
                # timestamp when the provider didn't supply its own real
                # added_at per track.
                now = datetime.datetime.now()
                for t in batch:
                    t.setdefault("added_at", now)
                Track.insert_many(batch).on_conflict(
                    conflict_target=[Track.id],
                    preserve=_TRACK_PRESERVE_ON_CONFLICT,
                    # rating not updated; added_at backfills only if the
                    # existing row doesn't already have one (see upsert_track).
                    update={Track.added_at: fn.COALESCE(Track.added_at, EXCLUDED.added_at)}
                ).execute()

        if album_genres:
            self.link_genres(album_genres=album_genres)
