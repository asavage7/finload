from peewee import (
    BooleanField,
    CharField,
    DateTimeField,
    FloatField,
    ForeignKeyField,
    IntegerField,
    Model,
    SqliteDatabase,
    TextField,
)
import datetime
import os
from config import get_database_path

# check_same_thread=False is required because the backend serves concurrent requests.
# WAL mode lets API reads proceed while a library sync writes, avoiding "database
# is locked" stalls during sync.
_DB_PRAGMAS = {'foreign_keys': 1, 'journal_mode': 'wal'}

db = SqliteDatabase(
    str(get_database_path()),
    pragmas=_DB_PRAGMAS,
    check_same_thread=False,
)

# Bump this when adding a new migration to _MIGRATIONS below.
SCHEMA_VERSION = 7


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


class Album(BaseModel):
    id = CharField(primary_key=True)
    title = CharField()
    artist = ForeignKeyField(Artist, backref='albums')
    release_year = IntegerField(default=0)
    genre = CharField(default="Unknown")
    rating = IntegerField(default=0)
    description = TextField(default="")  # ""=not enriched OR enriched+empty; use enriched_at to distinguish
    tadb_id = CharField(null=True)        # TheAudioDB idAlbum for direct future lookups
    enriched_at = DateTimeField(null=True)
    provider = CharField(default="jellyfin")


class Track(BaseModel):
    id = CharField(primary_key=True)
    title = CharField()
    artist = ForeignKeyField(Artist, backref='tracks')
    album = ForeignKeyField(Album, backref='tracks')
    track_number = IntegerField(default=0)
    disc_number = IntegerField(default=1)
    duration_ms = IntegerField(default=0)
    rating = IntegerField(default=0)
    has_artwork = BooleanField(default=False)
    file_path = CharField(default="")  # absolute path on disk; only set for provider="local"
    provider = CharField(default="jellyfin")


class TrackLyrics(BaseModel):
    track = ForeignKeyField(Track, primary_key=True, backref='lyrics', on_delete='CASCADE')
    lyrics_type = CharField()         # "synced" | "unsynced" | "none"
    content = TextField(null=True)    # JSON list for synced, plain str for unsynced, NULL for none
    source = CharField(default="")    # "lrclib" | "jellyfin" | "embedded" | "sidecar"
    fetched_at = DateTimeField(default=datetime.datetime.now)


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
    seek_pos = FloatField(default=0.0)


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------

def _migrate_1():
    """v0→v1: Add provider column to artist/album/track."""
    db.execute_sql("ALTER TABLE artist ADD COLUMN provider VARCHAR(255) NOT NULL DEFAULT 'jellyfin'")
    db.execute_sql("ALTER TABLE album ADD COLUMN provider VARCHAR(255) NOT NULL DEFAULT 'jellyfin'")
    db.execute_sql("ALTER TABLE track ADD COLUMN provider VARCHAR(255) NOT NULL DEFAULT 'jellyfin'")


def _migrate_2():
    """v1→v2: Recreate QueueItem (drop is_current, REAL position); create PlaybackState singleton."""
    db.execute_sql("PRAGMA foreign_keys = OFF")
    try:
        # Capture current-playing item before we drop the column.
        db.execute_sql("""
            CREATE TABLE IF NOT EXISTS playbackstate (
                "id" INTEGER NOT NULL PRIMARY KEY,
                "current_queue_item_id" INTEGER,
                "shuffle" INTEGER NOT NULL DEFAULT 0,
                "repeat_mode" INTEGER NOT NULL DEFAULT 0,
                "seek_pos" REAL NOT NULL DEFAULT 0.0
            )
        """)
        db.execute_sql("""
            INSERT OR IGNORE INTO playbackstate (id, current_queue_item_id, shuffle, repeat_mode, seek_pos)
            SELECT 1, id, 0, 0, 0.0 FROM queueitem WHERE is_current = 1 LIMIT 1
        """)
        db.execute_sql("""
            INSERT OR IGNORE INTO playbackstate (id, current_queue_item_id, shuffle, repeat_mode, seek_pos)
            VALUES (1, NULL, 0, 0, 0.0)
        """)
        # Recreate queueitem without is_current, with REAL position.
        db.execute_sql("""
            CREATE TABLE queueitem_new (
                "id" INTEGER NOT NULL PRIMARY KEY,
                "track_id" VARCHAR(255) NOT NULL REFERENCES "track" ("id"),
                "position" REAL NOT NULL DEFAULT 0.0,
                "queue_type" INTEGER NOT NULL DEFAULT 1,
                "added_at" DATETIME NOT NULL
            )
        """)
        db.execute_sql("""
            INSERT INTO queueitem_new (id, track_id, position, queue_type, added_at)
            SELECT id, track_id, CAST(position AS REAL), queue_type, added_at FROM queueitem
        """)
        db.execute_sql("DROP TABLE queueitem")
        db.execute_sql("ALTER TABLE queueitem_new RENAME TO queueitem")
        db.execute_sql("CREATE INDEX IF NOT EXISTS queueitem_queue_type_position ON queueitem (queue_type, position)")
    finally:
        db.execute_sql("PRAGMA foreign_keys = ON")


def _migrate_3():
    """v2→v3: Add missing indexes on PlayHistory and SearchHistory."""
    db.execute_sql("CREATE INDEX IF NOT EXISTS playhistory_track ON playhistory (track_id)")
    db.execute_sql("CREATE INDEX IF NOT EXISTS playhistory_played_at ON playhistory (played_at)")
    db.execute_sql("CREATE INDEX IF NOT EXISTS searchhistory_timestamp ON searchhistory (timestamp)")


def _migrate_5():
    """v4→v5: Consolidate artist to a single ID; strip local_a_ / local_t_ prefixes.

    Jellyfin artists: primary key becomes the Jellyfin UUID (was secondary_id).
    Local artists:    primary key becomes a stable 20-char SHA-1 hex hash of the
                      lowercase artist name (matches what LocalProvider now generates).
    Local album IDs:  'local_a_' prefix is stripped.
    Local track IDs:  'local_t_' prefix is stripped.
    secondary_id column is then dropped from the artist table.
    The FTS index is cleared (rebuilt on next sync).
    """
    import hashlib

    db.execute_sql("PRAGMA foreign_keys = OFF")
    try:
        artists = db.execute_sql(
            "SELECT id, name, secondary_id, provider FROM artist"
        ).fetchall()

        for old_id, name, secondary_id, provider in artists:
            if provider == "jellyfin" and secondary_id:
                new_id = secondary_id
            elif provider == "local":
                new_id = hashlib.sha1(name.lower().encode("utf-8")).hexdigest()[:20]
            else:
                continue  # Jellyfin artist without UUID; left as-is, fixed on re-sync
            if new_id == old_id:
                continue
            try:
                db.execute_sql("UPDATE album SET artist_id = ? WHERE artist_id = ?", (new_id, old_id))
                db.execute_sql("UPDATE track SET artist_id = ? WHERE artist_id = ?", (new_id, old_id))
                db.execute_sql("UPDATE artist SET id = ? WHERE id = ?", (new_id, old_id))
            except Exception:
                pass  # skip on the rare PK collision

        # Strip 'local_a_' prefix from local album IDs.
        for (album_id,) in db.execute_sql("SELECT id FROM album WHERE id LIKE 'local_a_%'").fetchall():
            new_id = album_id[len("local_a_"):]
            try:
                db.execute_sql("UPDATE track SET album_id = ? WHERE album_id = ?", (new_id, album_id))
                db.execute_sql("UPDATE album SET id = ? WHERE id = ?", (new_id, album_id))
            except Exception:
                pass

        # Strip 'local_t_' prefix from local track IDs.
        for (track_id,) in db.execute_sql("SELECT id FROM track WHERE id LIKE 'local_t_%'").fetchall():
            new_id = track_id[len("local_t_"):]
            try:
                db.execute_sql("UPDATE queueitem SET track_id = ? WHERE track_id = ?", (new_id, track_id))
                db.execute_sql("UPDATE playlisttrack SET track_id = ? WHERE track_id = ?", (new_id, track_id))
                db.execute_sql("UPDATE playhistory SET track_id = ? WHERE track_id = ?", (new_id, track_id))
                db.execute_sql("UPDATE track SET id = ? WHERE id = ?", (new_id, track_id))
            except Exception:
                pass

        # Drop secondary_id from artist by recreating the table.
        db.execute_sql("""
            CREATE TABLE artist_new (
                "id" VARCHAR(255) NOT NULL PRIMARY KEY,
                "name" VARCHAR(255) NOT NULL,
                "bio" VARCHAR(255) NOT NULL DEFAULT '',
                "provider" VARCHAR(255) NOT NULL DEFAULT 'jellyfin'
            )
        """)
        db.execute_sql("INSERT INTO artist_new SELECT id, name, bio, provider FROM artist")
        db.execute_sql("DROP TABLE artist")
        db.execute_sql("ALTER TABLE artist_new RENAME TO artist")

        # Clear stale FTS entries; rebuilt automatically on next sync.
        try:
            db.execute_sql("DELETE FROM search_fts")
        except Exception:
            pass
    finally:
        db.execute_sql("PRAGMA foreign_keys = ON")


def _migrate_4():
    """v3→v4: Change PlaylistTrack.position from INTEGER to REAL for midpoint reorder."""
    db.execute_sql("PRAGMA foreign_keys = OFF")
    try:
        db.execute_sql("""
            CREATE TABLE playlisttrack_new (
                "id" INTEGER NOT NULL PRIMARY KEY,
                "playlist_id" VARCHAR(255) NOT NULL REFERENCES "playlist" ("id") ON DELETE CASCADE,
                "track_id" VARCHAR(255) NOT NULL REFERENCES "track" ("id") ON DELETE CASCADE,
                "position" REAL NOT NULL,
                UNIQUE ("playlist_id", "position")
            )
        """)
        db.execute_sql("""
            INSERT OR IGNORE INTO playlisttrack_new (id, playlist_id, track_id, position)
            SELECT id, playlist_id, track_id, CAST(position AS REAL) FROM playlisttrack
        """)
        db.execute_sql("DROP TABLE IF EXISTS playlisttrack")
        db.execute_sql("ALTER TABLE playlisttrack_new RENAME TO playlisttrack")
    finally:
        db.execute_sql("PRAGMA foreign_keys = ON")


def _migrate_6():
    """v5→v6: Add lyrics cache and online-enrichment columns to artist/album.

    Uses ALTER TABLE ADD COLUMN instead of a table rebuild so this migration
    is safe to run inside the existing db.atomic() transaction wrapper (SQLite
    ignores PRAGMA foreign_keys = OFF inside a transaction, making table-rebuild
    migrations unreliable when FK constraints reference the rebuilt table).
    """
    # Artist: add enrichment-tracking columns.
    db.execute_sql("ALTER TABLE artist ADD COLUMN bio_source VARCHAR(255) NOT NULL DEFAULT ''")
    db.execute_sql("ALTER TABLE artist ADD COLUMN tadb_id VARCHAR(255)")
    db.execute_sql("ALTER TABLE artist ADD COLUMN enriched_at DATETIME")

    # Album: add enrichment-tracking columns.
    db.execute_sql("ALTER TABLE album ADD COLUMN tadb_id VARCHAR(255)")
    db.execute_sql("ALTER TABLE album ADD COLUMN enriched_at DATETIME")

    # Lyrics cache table.
    db.execute_sql("""
        CREATE TABLE IF NOT EXISTS tracklyrics (
            "track_id" VARCHAR(255) NOT NULL PRIMARY KEY
                REFERENCES "track" ("id") ON DELETE CASCADE,
            "lyrics_type" VARCHAR(32) NOT NULL,
            "content" TEXT,
            "source" VARCHAR(64) NOT NULL DEFAULT '',
            "fetched_at" DATETIME NOT NULL
        )
    """)


def _migrate_7():
    """v6→v7: Add file_path to track for local provider; backfill from local_index.json."""
    import json
    from config import get_data_dir

    db.execute_sql("ALTER TABLE track ADD COLUMN file_path VARCHAR(255) NOT NULL DEFAULT ''")

    # Backfill existing local tracks from the JSON index written by the old code.
    index_path = os.path.join(str(get_data_dir()), "local_index.json")
    try:
        with open(index_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        for track_id, path in (data.get("tracks") or {}).items():
            db.execute_sql("UPDATE track SET file_path = ? WHERE id = ?", (path, track_id))
    except (OSError, ValueError):
        pass  # No index file; file_path will be populated on the next local sync.


_MIGRATIONS = [_migrate_1, _migrate_2, _migrate_3, _migrate_4, _migrate_5, _migrate_6, _migrate_7]


# ---------------------------------------------------------------------------
# DatabaseManager
# ---------------------------------------------------------------------------

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
    db.init(new_path, pragmas=_DB_PRAGMAS, check_same_thread=False)
    return DatabaseManager()


class DatabaseManager:
    def __init__(self):
        if db.is_closed():
            db.connect()

        existing_tables = set(db.get_tables())

        if 'artist' not in existing_tables:
            # Fresh database — create everything at the current schema version.
            db.create_tables([
                SchemaVersion, Artist, Album, Track, TrackLyrics,
                Playlist, PlaylistTrack,
                SearchHistory, PlayHistory,
                QueueItem, PlaybackState,
            ])
            SchemaVersion.create(id=1, version=SCHEMA_VERSION)
            self._setup_fts()
            return

        # Existing database — ensure the version table exists.
        db.create_tables([SchemaVersion], safe=True)
        version_row = SchemaVersion.get_or_none(SchemaVersion.id == 1)
        current_version = version_row.version if version_row else 0

        if current_version < SCHEMA_VERSION:
            # NOTE: this runs every migration inside one transaction. SQLite makes
            # `PRAGMA foreign_keys = OFF` a no-op inside a transaction, so the
            # table-rebuild migrations (_migrate_2/4/5) do NOT actually disable FK
            # enforcement — they only worked because nothing else referenced the
            # rebuilt tables at the time. Any *future* rebuild migration must not
            # rely on that pragma; do its FK-sensitive work outside this atomic
            # block (commit first) or it will misbehave. See _migrate_6's docstring.
            with db.atomic():
                for i, migrate_fn in enumerate(_MIGRATIONS):
                    target = i + 1
                    if current_version < target:
                        migrate_fn()
            if version_row:
                version_row.version = SCHEMA_VERSION
                version_row.save()
            else:
                SchemaVersion.create(id=1, version=SCHEMA_VERSION)

        # Ensure tables added in later schema versions exist for older DBs.
        db.create_tables([Playlist, PlaylistTrack, SearchHistory, PlaybackState, TrackLyrics], safe=True)
        self._setup_fts()

    def _setup_fts(self):
        """Create the FTS5 virtual table for fast full-text search."""
        try:
            db.execute_sql("""
                CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
                    type, item_id UNINDEXED, name, artist_name, album_title,
                    tokenize='unicode61'
                )
            """)
        except Exception:
            pass  # FTS5 not available in this SQLite build (very unlikely)

    def rebuild_fts(self):
        """Rebuild the full-text search index from current library data. Called after sync."""
        try:
            with db.atomic():
                db.execute_sql("DELETE FROM search_fts")
                for artist in Artist.select():
                    db.execute_sql(
                        "INSERT INTO search_fts(type, item_id, name, artist_name, album_title) VALUES (?,?,?,?,?)",
                        ('artist', artist.id, artist.name, '', '')
                    )
                # Select the joined models so artist/album hydrate from the row
                # instead of triggering a lazy FK query per album/track.
                for album in Album.select(Album, Artist).join(Artist):
                    db.execute_sql(
                        "INSERT INTO search_fts(type, item_id, name, artist_name, album_title) VALUES (?,?,?,?,?)",
                        ('album', album.id, album.title, album.artist.name, '')
                    )
                for track in Track.select(Track, Album, Artist).join(Album).switch(Track).join(Artist):
                    db.execute_sql(
                        "INSERT INTO search_fts(type, item_id, name, artist_name, album_title) VALUES (?,?,?,?,?)",
                        ('track', track.id, track.title, track.artist.name, track.album.title)
                    )
        except Exception:
            pass

    def upsert_artist(self, **data):
        Artist.insert(**data).on_conflict(
            conflict_target=[Artist.id],
            preserve=[Artist.name]
        ).execute()

    def upsert_album(self, **data):
        Album.insert(**data).on_conflict(
            conflict_target=[Album.id],
            preserve=[Album.title, Album.artist, Album.release_year, Album.genre, Album.provider]
        ).execute()

    def upsert_track(self, **data):
        Track.insert(**data).on_conflict_replace().execute()

    def bulk_upsert(self, artists: list, albums: list, tracks: list):
        """Insert/replace many rows in one transaction — the fast path for sync."""
        with db.atomic():
            for batch in _chunks(artists, 100):
                Artist.insert_many(batch).on_conflict(
                    conflict_target=[Artist.id],
                    preserve=[Artist.name]
                    # bio, bio_source, tadb_id, enriched_at intentionally not updated
                ).execute()
            for batch in _chunks(albums, 100):
                Album.insert_many(batch).on_conflict(
                    conflict_target=[Album.id],
                    preserve=[Album.title, Album.artist, Album.release_year, Album.genre, Album.provider]
                    # description, tadb_id, enriched_at intentionally not updated
                ).execute()
            for batch in _chunks(tracks, 100):
                Track.insert_many(batch).on_conflict_replace().execute()

    def _fts_search(self, query: str, limit: int):
        """Return {type: [item_id, ...]} via FTS5, or None on failure."""
        safe_query = query.replace('"', '').strip()
        if not safe_query:
            return None
        try:
            cursor = db.execute_sql(
                "SELECT type, item_id FROM search_fts WHERE search_fts MATCH ? ORDER BY rank LIMIT ?",
                (safe_query + '*', limit * 3)
            )
            result = {"artist": [], "album": [], "track": []}
            for row_type, item_id in cursor.fetchall():
                if row_type in result:
                    result[row_type].append(item_id)
            return result
        except Exception:
            return None

    def search(self, query: str, limit: int = 5, unified: bool = False):
        """Returns a dictionary of matches across all categories."""
        if not query or len(query) < 2:
            return [] if unified else {"artists": [], "albums": [], "tracks": []}

        fts = self._fts_search(query, limit)
        if fts is not None:
            results = {
                "artists": list(Artist.select().where(Artist.id << fts["artist"])) if fts["artist"] else [],
                "albums": list(Album.select().join(Artist).where(Album.id << fts["album"])) if fts["album"] else [],
                "tracks": list(
                    Track.select().join(Artist).switch(Track).join(Album)
                    .where(Track.id << fts["track"])
                ) if fts["track"] else [],
            }
        else:
            # Fallback: LIKE-based search (no index, used only if FTS5 is unavailable)
            results = {
                "artists": list(Artist.select().where(Artist.name.contains(query)).limit(limit)),
                "albums": list(Album.select().join(Artist).where(
                    Album.title.contains(query) | Artist.name.contains(query)
                ).limit(limit)),
                "tracks": list(Track.select().join(Artist).switch(Track).join(Album).where(
                    Track.title.contains(query) | Artist.name.contains(query) | Album.title.contains(query)
                ).limit(limit)),
            }

        if not unified:
            return results

        unified_results = {}
        for category, items in results.items():
            unified_results[category] = {}
            for item in items:
                unified_results[category][item] = 0
                item_name = item.name if hasattr(item, 'name') else item.title
                rating = item.rating if hasattr(item, 'rating') else 0
                if item_name.lower().startswith(query.lower()):
                    unified_results[category][item] += 10
                    if isinstance(item, Album):
                        unified_results[category][item] += 5
                unified_results[category][item] += rating * 2

        flat_results = []
        for category, items in unified_results.items():
            flat_results.extend(items.items())
        flat_results.sort(key=lambda x: x[1], reverse=True)
        return [item for item, score in flat_results[:limit]]
