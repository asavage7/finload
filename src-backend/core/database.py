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
from core.config import get_database_path
from core.migrations import SCHEMA_VERSION, ensure_model_columns, run_migrations, stable_genre_id

# WAL allows reads during writes, and each worker needs its own connection.
_DB_PRAGMAS = {'foreign_keys': 1, 'journal_mode': 'wal', 'busy_timeout': 30000}

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
    bio = TextField(default="") # optional enriched field for artist page
    bio_source = CharField(default="") # currently "theaudiodb" or "" if not fetched yet
    tadb_id = CharField(null=True) # TheAudioDB idArtist for direct future lookups
    enriched_at = DateTimeField(null=True) # Timestamp for genre enrichment and bio fetches
    provider = CharField(default="jellyfin")
    mbid = CharField(null=True) # MusicBrainz artist ID


class Album(BaseModel):
    id = CharField(primary_key=True)
    title = CharField()
    artist = ForeignKeyField(Artist, backref='albums')
    release_year = IntegerField(default=0)
    rating = IntegerField(default=0)
    provider = CharField(default="jellyfin")
    mbid = CharField(null=True)  # MusicBrainz release group ID


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
    mbid = CharField(null=True)  # MusicBrainz recording ID
    library_id = CharField(null=True) # Which library the track is in, used for separating jellyfin libraries.
    added_at = DateTimeField(null=True)


# Don't overwrite user data on conflict
_TRACK_PRESERVE_ON_CONFLICT = [
    Track.title, Track.artist, Track.album, Track.track_number,
    Track.disc_number, Track.duration_ms, Track.file_path,
    Track.provider, Track.mbid, Track.library_id,
]


def track_scope_clause(selected_library_ids):
    """Scope track queries to active libraries in Jellyfin."""
    if not selected_library_ids:
        return None
    return (Track.provider != "jellyfin") | (Track.library_id.in_(selected_library_ids))


class Genre(BaseModel):
    id = CharField(primary_key=True) # Deterministic based on name (see stable_genre_id in migrations.py)
    name = CharField()


class AlbumGenre(BaseModel):
    album = ForeignKeyField(Album, backref='genre_links', on_delete='CASCADE')
    genre = ForeignKeyField(Genre, backref='albums', on_delete='CASCADE')
    source = CharField(default="")    # "jellyfin" | "local" | "lastfm" | "musicbrainz"
    weight = IntegerField(default=0)  # used to rank genres by relevance, higher = more important

    class Meta:
        indexes = (
            (('album', 'genre', 'source'), True),
        )


class ArtistGenre(BaseModel):
    artist = ForeignKeyField(Artist, backref='genre_links', on_delete='CASCADE')
    genre = ForeignKeyField(Genre, backref='artists', on_delete='CASCADE')
    source = CharField(default="")    # "jellyfin" | "local" | "lastfm" | "musicbrainz"
    weight = IntegerField(default=0)  # used to rank genres by relevance, higher = more important

    class Meta:
        indexes = (
            (('artist', 'genre', 'source'), True),
        )


class TrackLyrics(BaseModel):
    track = ForeignKeyField(Track, primary_key=True, backref='lyrics', on_delete='CASCADE')
    lyrics_type = CharField()         # "synced" | "unsynced" | "none"
    content = TextField(null=True)    # JSON list for synced, plain str for unsynced, NULL for none
    source = CharField(default="")    # "lrclib" | "jellyfin" | "embedded", etc.
    fetched_at = DateTimeField(default=datetime.datetime.now)


class TrackFeatures(BaseModel):
    """Cached librosa DSP features for a track (see audio_analysis.py),"""
    track = ForeignKeyField(Track, primary_key=True, backref='features', on_delete='CASCADE')
    bpm = FloatField(default=0.0)
    features = TextField(default="")
    feature_version = IntegerField(default=0) # lets the background service know when to re-analyze
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
    visible = BooleanField(default=True) # Used for skipped radio tracks
    in_progress = BooleanField(default=False)

    class Meta:
        indexes = (
            (('track',), False),
            (('played_at',), False),
        )


class QueueItem(BaseModel):
    track = ForeignKeyField(Track, backref='queue')
    position = FloatField(default=0.0) # allows midpoint insertion to avoid re-organizing the entire list
    queue_type = IntegerField(default=1)  # 0: next up, 1: queue, 2: autoplay
    added_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('queue_type', 'position'), False),
        )


class PlaybackState(BaseModel):
    id = IntegerField(primary_key=True)
    current_queue_item = ForeignKeyField(QueueItem, null=True, backref='+', on_delete='SET NULL')
    shuffle = BooleanField(default=False)
    repeat_mode = IntegerField(default=0)  # 0=off, 1=repeat_all, 2=repeat_one
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

    Peewee's Meta.indexes can't express COLLATE NOCASE on an index, so
    this is created directly rather than declared on the model.
    """
    db.execute_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS genre_name_nocase ON genre (name COLLATE NOCASE)"
    )


def switch_database(source: str | None = None) -> "DatabaseManager":
    """Change databases when a user switches library sources or when DATABASE_PATH is overridden.
    Likely to be replaced by an app restart in the future.
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
            db.create_tables(ALL_MODELS)
            _ensure_genre_indexes(db)
            SchemaVersion.create(id=1, version=SCHEMA_VERSION)
            return

        db.create_tables([SchemaVersion], safe=True)
        version_row = SchemaVersion.get_or_none(SchemaVersion.id == 1)
        current_version = version_row.version if version_row else 0
        
        # Run pending migrations if necessary
        if current_version < SCHEMA_VERSION:
            with db.atomic():
                run_migrations(db, current_version)
            if version_row:
                version_row.version = SCHEMA_VERSION
                version_row.save()
            else:
                SchemaVersion.create(id=1, version=SCHEMA_VERSION)

        # Pick up new tables/columns automatically to reduce needed migrations
        db.create_tables(ALL_MODELS, safe=True)
        ensure_model_columns(db, ALL_MODELS)
        _ensure_genre_indexes(db)

    def find_artist_id_by_name(self, name: str) -> str | None:
        """Jellyfin can use different IDs for the same artist, sync looks up by name to avoid duplicates."""
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
        """Avoids tracks being marked as added_at=now() when the provider didn't supply a timestamp, but the track already exists in the DB."""
        data.setdefault("added_at", datetime.datetime.now())
        Track.insert(**data).on_conflict(
            conflict_target=[Track.id],
            preserve=_TRACK_PRESERVE_ON_CONFLICT,
            update={Track.added_at: fn.COALESCE(Track.added_at, EXCLUDED.added_at)}
        ).execute()

    def save_track_features(self, track_id: str, bpm: float, mfcc_mean: list,
                             mfcc_std: list, contrast_mean: list) -> None:
        from services.audio_analysis import FEATURE_VERSION
        TrackFeatures.insert(
            track=track_id, bpm=bpm, feature_version=FEATURE_VERSION,
            features=json.dumps({"mfcc_mean": mfcc_mean, "mfcc_std": mfcc_std,
                                 "contrast_mean": contrast_mean}),
        ).on_conflict_replace().execute()

    def _genre_ids_by_name(self, names: set) -> dict:
        """Returns a dictionary mapping genre names to their IDs."""
        genre_ids = {}
        for name in names:
            genre = Genre.get_or_none(Genre.name.collate("NOCASE") == name)
            if genre is None:
                genre = Genre.create(id=stable_genre_id(name), name=name.title())
            genre_ids[name.lower()] = genre.id
        return genre_ids

    def link_genres(self, album_genres: list | None = None, artist_genres: list | None = None):
        """Attach genres to albums/artists."""
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
        """Delete tracks by ID."""
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
        """Delete albums with no tracks, then artists with no tracks or albums."""
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
        """Faster syncing by batch updating in chunks."""
        with db.atomic():
            for batch in _chunks(artists, 100):
                Artist.insert_many(batch).on_conflict(
                    conflict_target=[Artist.id],
                    preserve=[Artist.name, Artist.mbid]
                ).execute()
            for batch in _chunks(albums, 100):
                Album.insert_many(batch).on_conflict(
                    conflict_target=[Album.id],
                    preserve=[Album.title, Album.artist, Album.release_year, Album.provider, Album.mbid]
                ).execute()
            for batch in _chunks(tracks, 100):
                now = datetime.datetime.now()
                for t in batch:
                    t.setdefault("added_at", now)
                Track.insert_many(batch).on_conflict(
                    conflict_target=[Track.id],
                    preserve=_TRACK_PRESERVE_ON_CONFLICT,
                    update={Track.added_at: fn.COALESCE(Track.added_at, EXCLUDED.added_at)}
                ).execute()

        if album_genres:
            self.link_genres(album_genres=album_genres)
