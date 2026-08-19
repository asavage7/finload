"""Schema migrations.

Two layers:

1. Automatic column reconciliation (``ensure_model_columns``): compares each
   model against the live table and adds any missing columns. This covers the
   common "new field on a model" case with no migration code at all.

2. Numbered manual migrations (``run_migrations``): for anything the automatic
   layer can't do, like table rebuilds, renames, data transforms, or dropping
   columns. Add a function, append it to ``_MIGRATIONS``, and bump
   ``SCHEMA_VERSION``.

Nothing here imports the models module. Everything takes the database handle
as an argument so this module stays import-cycle free.
"""
import hashlib

from playhouse.migrate import SqliteMigrator, migrate as _apply

# Bump when appending to _MIGRATIONS below.
SCHEMA_VERSION = 19


def stable_genre_id(name: str) -> str:
    """Deterministic id for a genre name — same convention as local-provider
    Track/Album/Artist ids (``providers/local.py``'s ``_stable_hash``): the
    same genre resolves to the same id on every install, regardless of
    insertion order, instead of an autoincrement integer that's meaningless
    outside one database.
    """
    return hashlib.sha1(name.strip().lower().encode("utf-8")).hexdigest()[:20]


def ensure_model_columns(db, models):
    """Add any columns that exist on the models but not in the database.

    New columns must be nullable or have a default. Anything more involved
    (NOT NULL without default, type changes, drops) needs a manual migration.
    """
    migrator = SqliteMigrator(db)
    ops = []
    existing_tables = set(db.get_tables())
    for model in models:
        table = model._meta.table_name
        if table not in existing_tables:
            continue
        existing = {col.name for col in db.get_columns(table)}
        for field in model._meta.sorted_fields:
            if field.column_name not in existing:
                ops.append(migrator.add_column(table, field.column_name, field))
    if ops:
        _apply(*ops)


def _migrate_1(db):
    """v0 to v1: Add provider column to artist/album/track."""
    db.execute_sql("ALTER TABLE artist ADD COLUMN provider VARCHAR(255) NOT NULL DEFAULT 'jellyfin'")
    db.execute_sql("ALTER TABLE album ADD COLUMN provider VARCHAR(255) NOT NULL DEFAULT 'jellyfin'")
    db.execute_sql("ALTER TABLE track ADD COLUMN provider VARCHAR(255) NOT NULL DEFAULT 'jellyfin'")


def _migrate_2(db):
    """v1 to v2: Recreate QueueItem (drop is_current, REAL position); create PlaybackState singleton."""
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


def _migrate_3(db):
    """v2 to v3: Add missing indexes on PlayHistory and SearchHistory."""
    db.execute_sql("CREATE INDEX IF NOT EXISTS playhistory_track ON playhistory (track_id)")
    db.execute_sql("CREATE INDEX IF NOT EXISTS playhistory_played_at ON playhistory (played_at)")
    db.execute_sql("CREATE INDEX IF NOT EXISTS searchhistory_timestamp ON searchhistory (timestamp)")


def _migrate_4(db):
    """v3 to v4: Change PlaylistTrack.position from INTEGER to REAL for midpoint reorder."""
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


def _migrate_5(db):
    """v4 to v5: Consolidate artist to a single ID; strip local_a_ / local_t_ prefixes.

    Jellyfin artists: primary key becomes the Jellyfin UUID (was secondary_id).
    Local artists:    primary key becomes a stable 20-char SHA-1 hex hash of the
                      lowercase artist name (matches what LocalProvider now generates).
    Local album IDs:  'local_a_' prefix is stripped.
    Local track IDs:  'local_t_' prefix is stripped.
    secondary_id column is then dropped from the artist table.
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
    finally:
        db.execute_sql("PRAGMA foreign_keys = ON")


def _migrate_6(db):
    """v5 to v6: Add lyrics cache and online-enrichment columns to artist/album.

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


def _migrate_7(db):
    """v6 to v7: Add file_path to track for local provider; backfill from local_index.json."""
    import json
    import os
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


def _migrate_8(db):
    """v7 to v8: Drop unused columns (track artwork flag, album enrichment) and the FTS table."""
    db.execute_sql("ALTER TABLE track DROP COLUMN has_artwork")
    db.execute_sql("ALTER TABLE album DROP COLUMN description")
    db.execute_sql("ALTER TABLE album DROP COLUMN tadb_id")
    db.execute_sql("ALTER TABLE album DROP COLUMN enriched_at")
    db.execute_sql("ALTER TABLE playbackstate DROP COLUMN seek_pos")
    db.execute_sql("DROP TABLE IF EXISTS search_fts")


def _backfill_album_genre_strings(db):
    """Split the old comma-joined ``album.genre`` string into Genre/AlbumGenre
    rows, tagged with each album's own ``provider`` (jellyfin/local) since
    that's who supplied it. Shared by ``_migrate_9`` and ``_migrate_10``."""
    rows = db.execute_sql(
        "SELECT id, genre, provider FROM album WHERE genre IS NOT NULL AND genre != ''"
    ).fetchall()
    for album_id, genre_str, provider in rows:
        names = {n.strip() for n in genre_str.split(",") if n.strip() and n.strip() != "Unknown"}
        for name in names:
            existing = db.execute_sql(
                "SELECT id FROM genre WHERE name = ? COLLATE NOCASE", (name,)
            ).fetchone()
            if existing:
                genre_id = existing[0]
            else:
                db.execute_sql("INSERT INTO genre (name) VALUES (?)", (name,))
                genre_id = db.execute_sql("SELECT last_insert_rowid()").fetchone()[0]
            db.execute_sql(
                "INSERT OR IGNORE INTO albumgenre (album_id, genre_id, source, weight) VALUES (?, ?, ?, 0)",
                (album_id, genre_id, provider),
            )


def _migrate_9(db):
    """v8 to v9: Replace the single Album.genre string with a many-to-many
    Genre model shared by albums/tracks/artists, so a title can carry an
    unlimited number of genres tagged by source (server, file tags, Last.fm...).

    Tables are created here (bare, without the unique indexes Peewee's Meta
    declares — those get added by the ``create_tables(safe=True)`` call that
    runs right after migrations, once the models exist) so the backfill below
    has somewhere to write.
    """
    db.execute_sql("""
        CREATE TABLE IF NOT EXISTS genre (
            "id" INTEGER NOT NULL PRIMARY KEY,
            "name" VARCHAR(255) NOT NULL
        )
    """)
    for table, fk_table in (("albumgenre", "album"), ("trackgenre", "track"), ("artistgenre", "artist")):
        db.execute_sql(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                "id" INTEGER NOT NULL PRIMARY KEY,
                "{fk_table}_id" VARCHAR(255) NOT NULL REFERENCES "{fk_table}" ("id") ON DELETE CASCADE,
                "genre_id" INTEGER NOT NULL REFERENCES "genre" ("id") ON DELETE CASCADE,
                "source" VARCHAR(255) NOT NULL DEFAULT '',
                "weight" INTEGER NOT NULL DEFAULT 0
            )
        """)

    _backfill_album_genre_strings(db)
    db.execute_sql("ALTER TABLE album DROP COLUMN genre")


def _migrate_10(db):
    """v9 to v10: corrective follow-up to v9.

    A live ``--reload`` dev server restarted mid-edit while v9 was being
    written: it applied the ``SCHEMA_VERSION = 9`` bump and auto-created the
    new (empty) Genre/AlbumGenre/TrackGenre/ArtistGenre tables via
    ``create_tables(safe=True)`` before ``_migrate_9``'s backfill-and-drop body
    existed, so the version marker recorded v9 as applied without it actually
    running. This finishes that interrupted job. No-op (and harmless to ship
    to every other install) once ``album.genre`` is already gone.
    """
    columns = {col.name for col in db.get_columns("album")}
    if "genre" not in columns:
        return
    _backfill_album_genre_strings(db)
    db.execute_sql("ALTER TABLE album DROP COLUMN genre")


def _migrate_11(db):
    """v10 to v11: Normalize existing Genre names to title case (Last.fm and
    MusicBrainz often return lowercase tags, e.g. "sludge metal"), and purge
    known-junk genre entries that slipped through an early version of the
    Last.fm noise filter (e.g. "test"/"testing" — see genre_enrichment.py's
    ``_EXACT_JUNK_NAMES``).
    """
    junk_names = ("test", "testing", "tests")
    placeholders = ",".join("?" for _ in junk_names)
    junk_ids = [r[0] for r in db.execute_sql(
        f"SELECT id FROM genre WHERE lower(name) IN ({placeholders})", junk_names
    ).fetchall()]
    for genre_id in junk_ids:
        db.execute_sql("DELETE FROM albumgenre WHERE genre_id = ?", (genre_id,))
        db.execute_sql("DELETE FROM trackgenre WHERE genre_id = ?", (genre_id,))
        db.execute_sql("DELETE FROM artistgenre WHERE genre_id = ?", (genre_id,))
        db.execute_sql("DELETE FROM genre WHERE id = ?", (genre_id,))

    for genre_id, name in db.execute_sql("SELECT id, name FROM genre").fetchall():
        titled = name.title()
        if titled != name:
            db.execute_sql("UPDATE genre SET name = ? WHERE id = ?", (titled, genre_id))


def _remap_genre_ids_to_hash(db):
    """Rebuilds genre and the three link tables (SQLite can't change a primary
    key's type in place), remapping old integer ids to new hash ids via an
    in-memory map — same table-rebuild pattern as earlier migrations
    (_migrate_2, _migrate_4, _migrate_5). Shared by _migrate_12 and its
    corrective follow-up _migrate_13.
    """
    rows = db.execute_sql("SELECT id, name FROM genre").fetchall()
    id_map: dict[int, str] = {}
    used_ids: set[str] = set()
    for old_id, name in rows:
        new_id = stable_genre_id(name)
        # Guard against a hash collision between two distinct names — should
        # be astronomically unlikely, but silent id collisions would merge
        # two different genres.
        suffix = 0
        while new_id in used_ids:
            suffix += 1
            new_id = stable_genre_id(f"{name}#{suffix}")
        used_ids.add(new_id)
        id_map[old_id] = new_id

    db.execute_sql("""
        CREATE TABLE genre_new (
            "id" VARCHAR(255) NOT NULL PRIMARY KEY,
            "name" VARCHAR(255) NOT NULL
        )
    """)
    for old_id, name in rows:
        db.execute_sql("INSERT INTO genre_new (id, name) VALUES (?, ?)", (id_map[old_id], name))

    for table, fk_table in (("albumgenre", "album"), ("trackgenre", "track"), ("artistgenre", "artist")):
        db.execute_sql(f"""
            CREATE TABLE {table}_new (
                "id" INTEGER NOT NULL PRIMARY KEY,
                "{fk_table}_id" VARCHAR(255) NOT NULL REFERENCES "{fk_table}" ("id") ON DELETE CASCADE,
                "genre_id" VARCHAR(255) NOT NULL REFERENCES "genre_new" ("id") ON DELETE CASCADE,
                "source" VARCHAR(255) NOT NULL DEFAULT '',
                "weight" INTEGER NOT NULL DEFAULT 0
            )
        """)
        link_rows = db.execute_sql(
            f'SELECT id, {fk_table}_id, genre_id, source, weight FROM {table}'
        ).fetchall()
        for row_id, entity_id, old_genre_id, source, weight in link_rows:
            new_genre_id = id_map.get(old_genre_id)
            if new_genre_id is None:
                continue  # shouldn't happen — FK guarantees genre_id existed
            db.execute_sql(
                f'INSERT INTO {table}_new (id, {fk_table}_id, genre_id, source, weight) '
                f'VALUES (?, ?, ?, ?, ?)',
                (row_id, entity_id, new_genre_id, source, weight),
            )
        db.execute_sql(f"DROP TABLE {table}")
        db.execute_sql(f"ALTER TABLE {table}_new RENAME TO {table}")

    db.execute_sql("DROP TABLE genre")
    db.execute_sql("ALTER TABLE genre_new RENAME TO genre")


def _migrate_12(db):
    """v11 to v12: Genre.id becomes a deterministic content hash (see
    ``stable_genre_id``) instead of an autoincrement integer, so the same
    genre resolves to the same id on every install and matches the string-id
    convention every other entity (Track/Album/Artist) already uses.
    """
    _remap_genre_ids_to_hash(db)


def _migrate_13(db):
    """v12 to v13: corrective follow-up to v12 — same live-``--reload`` race
    as ``_migrate_10``/``_migrate_9``: ``SCHEMA_VERSION`` got bumped to 12
    before ``_migrate_12``'s actual remap body existed, so ``genre.id`` was
    still the old autoincrement integer despite the version marker recording
    v12 as applied. Finishes the remap. No-op once ``genre.id`` is already
    the VARCHAR hash id.
    """
    cols = {col.name: col for col in db.get_columns("genre")}
    id_col = cols.get("id")
    if id_col is None or (id_col.data_type or "").upper() != "INTEGER":
        return
    _remap_genre_ids_to_hash(db)


def _migrate_14(db):
    """v13 to v14: Add added_at to track — first-seen timestamp, powering the
    home page's "Recently Added" row (see routers/library.py).

    Deliberately no SQL-level DEFAULT: SQLite rejects a non-constant ADD
    COLUMN default (confirmed empirically — "Cannot add a column with
    non-constant default" — CURRENT_TIMESTAMP is not the documented
    exception it might look like). Instead, DatabaseManager.upsert_track /
    bulk_upsert set added_at in Python at insert time, relying on the
    on_conflict/preserve upsert below (added alongside this migration,
    replacing the old on_conflict_replace() which silently reset any column
    missing from a resync's insert dict, including rating, back to its
    schema default) to leave it untouched on every later resync of the same
    track. Existing rows stay NULL until their next resync.
    """
    db.execute_sql("ALTER TABLE track ADD COLUMN added_at DATETIME")


def _migrate_15(db):
    """v14 to v15: Replace TrackFeatures.dist_mean/dist_std (Normal mean/std)
    with dist_center/dist_scale (median/MAD) for hub detection.

    A track's real MFCC-distance-to-the-rest-of-the-library distribution is
    right-skewed, not Gaussian, so discovery.py's _mutual_proximity (a Normal
    survival function over mean/std) under-corrected exactly the most
    extreme hub tracks it exists to catch: their own long right tail of
    "far" distances inflates std enough that a hub's characteristic close
    distances no longer read as surprising. Median/MAD isn't dragged around
    by that tail the way mean/std is.

    Recomputes in place (same math as
    audio_analysis.compute_hubness_stats, duplicated here since migrations.py
    can't import it without an import cycle through database.py -- see this
    module's docstring) rather than just dropping the old columns, so
    existing libraries don't silently lose hub correction until their next
    audio analysis pass, which may be a long time coming since it only runs
    over tracks that don't already have cached features.
    """
    import json

    import numpy as np

    db.execute_sql("ALTER TABLE trackfeatures ADD COLUMN dist_center REAL NOT NULL DEFAULT 0.0")
    db.execute_sql("ALTER TABLE trackfeatures ADD COLUMN dist_scale REAL NOT NULL DEFAULT 0.0")

    rows = db.execute_sql("SELECT track_id, mfcc_mean FROM trackfeatures").fetchall()
    if len(rows) >= 2:
        track_ids = [r[0] for r in rows]
        vectors = np.array([json.loads(r[1]) for r in rows])

        # Same full-pass/sample-pass split as
        # audio_analysis.HUBNESS_FULL_PASS_LIMIT/HUBNESS_SAMPLE_SIZE, inlined
        # rather than imported for the same reason as above.
        if len(rows) > 5000:
            sample_idx = np.random.choice(len(rows), size=2000, replace=False)
            sample_vectors = vectors[sample_idx]
        else:
            sample_vectors = vectors

        # ||a-b||^2 = ||a||^2 + ||b||^2 - 2 a.b -- avoids an O(N^2) Python loop.
        a_sq = np.sum(vectors ** 2, axis=1, keepdims=True)
        b_sq = np.sum(sample_vectors ** 2, axis=1)
        cross = vectors @ sample_vectors.T
        dist = np.sqrt(np.maximum(a_sq + b_sq - 2 * cross, 0.0))

        for i, track_id in enumerate(track_ids):
            row_dists = dist[i]
            row_dists = row_dists[row_dists > 1e-9]  # exclude self (0 distance)
            if len(row_dists) == 0:
                continue
            median = float(np.median(row_dists))
            mad = float(np.median(np.abs(row_dists - median)))
            # 1.4826 scales MAD up to a std-equivalent under approximate
            # normality -- see audio_analysis._MAD_TO_STD.
            scale = max(mad * 1.4826, 1e-6)
            db.execute_sql(
                "UPDATE trackfeatures SET dist_center = ?, dist_scale = ? WHERE track_id = ?",
                (median, scale, track_id),
            )

    db.execute_sql("ALTER TABLE trackfeatures DROP COLUMN dist_mean")
    db.execute_sql("ALTER TABLE trackfeatures DROP COLUMN dist_std")


def _migrate_16(db):
    """v15 to v16: corrective follow-up to v15 -- same live-``--reload`` race
    as ``_migrate_10``/``_migrate_13``: the ``SCHEMA_VERSION = 15`` bump and
    ``_migrate_15``'s actual body were written in two separate edits, and a
    dev server watching this file reloaded in between. At that point
    ``current_version(14) < SCHEMA_VERSION(15)`` was already true, so
    ``run_migrations`` ran and fell straight through the then-14-entry
    ``_MIGRATIONS`` list (none of whose guards matched a version of 14 with
    ``SCHEMA_VERSION`` already bumped) and the version marker was recorded
    as v15 applied -- while separately, ``ensure_model_columns`` (unrelated,
    runs on every startup) had already added ``dist_center``/``dist_scale``
    at their bare 0.0 default just from the model class picking up its new
    field names, so the missing-column check that gates the rest of this
    function couldn't have caught it either. Net effect: dist_center/
    dist_scale exist but were never actually computed, and dist_mean/
    dist_std were never dropped. Finishes the job. No-op once dist_mean is
    already gone.
    """
    import json

    import numpy as np

    existing = {col.name for col in db.get_columns("trackfeatures")}
    if "dist_mean" not in existing:
        return  # already fully migrated

    if "dist_center" not in existing:
        db.execute_sql("ALTER TABLE trackfeatures ADD COLUMN dist_center REAL NOT NULL DEFAULT 0.0")
    if "dist_scale" not in existing:
        db.execute_sql("ALTER TABLE trackfeatures ADD COLUMN dist_scale REAL NOT NULL DEFAULT 0.0")

    rows = db.execute_sql("SELECT track_id, mfcc_mean FROM trackfeatures").fetchall()
    if len(rows) >= 2:
        track_ids = [r[0] for r in rows]
        vectors = np.array([json.loads(r[1]) for r in rows])

        if len(rows) > 5000:
            sample_idx = np.random.choice(len(rows), size=2000, replace=False)
            sample_vectors = vectors[sample_idx]
        else:
            sample_vectors = vectors

        a_sq = np.sum(vectors ** 2, axis=1, keepdims=True)
        b_sq = np.sum(sample_vectors ** 2, axis=1)
        cross = vectors @ sample_vectors.T
        dist = np.sqrt(np.maximum(a_sq + b_sq - 2 * cross, 0.0))

        for i, track_id in enumerate(track_ids):
            row_dists = dist[i]
            row_dists = row_dists[row_dists > 1e-9]
            if len(row_dists) == 0:
                continue
            median = float(np.median(row_dists))
            mad = float(np.median(np.abs(row_dists - median)))
            scale = max(mad * 1.4826, 1e-6)
            db.execute_sql(
                "UPDATE trackfeatures SET dist_center = ?, dist_scale = ? WHERE track_id = ?",
                (median, scale, track_id),
            )

    db.execute_sql("ALTER TABLE trackfeatures DROP COLUMN dist_mean")
    db.execute_sql("ALTER TABLE trackfeatures DROP COLUMN dist_std")


def _migrate_17(db):
    """Switch of DSP feature representation from essentia to librosa.

    The feature set changed shape (timbre mean + variance + spectral contrast,
    scored over a standardized vector; see audio_analysis.py and discovery.py),
    so the old per-feature columns are dropped. The replacement ``features``
    (JSON) and ``feature_version`` columns are added automatically by
    ``ensure_model_columns`` from the model. feature_version defaults to 0, so
    AudioFeatureManager re-analyzes every track (0 != FEATURE_VERSION) and the
    hubness pass repopulates dist_center/dist_scale; no data is carried over.
    """
    for col in ("mfcc_mean", "dyn_complexity", "brightness"):
        try:
            db.execute_sql(f"ALTER TABLE trackfeatures DROP COLUMN {col}")
        except Exception:
            pass  # fresh install (table built from the current model) or a
                  # partially-applied prior run: column already absent.


def _migrate_18(db):
    """v17 to v18: merge duplicate Artist rows that share the same
    case-insensitive name into one.

    Jellyfin can resolve a track-level artist credit (ArtistItems) to a
    different MusicArtist entity than the one used for that same person's
    own albums (AlbumArtists) -- see providers/jellyfin.py's _yield_items.
    Before this migration, each distinct id became its own permanent Artist
    row, so anyone credited both as an album artist and a differently-
    resolved track artist (typically on a compilation) showed up twice on
    the Artists page. Sync no longer creates new duplicates (SyncManager
    now reconciles an artist's id by name against the DB before writing --
    see _canonical_artist_id); this cleans up rows that already diverged
    under the old behavior.
    """
    rows = db.execute_sql("SELECT id, name, enriched_at FROM artist").fetchall()
    groups: dict[str, list[tuple[str, str, object]]] = {}
    for artist_id, name, enriched_at in rows:
        groups.setdefault(name.strip().lower(), []).append((artist_id, name, enriched_at))

    for group in groups.values():
        if len(group) < 2:
            continue
        album_counts = {
            artist_id: db.execute_sql(
                "SELECT COUNT(*) FROM album WHERE artist_id = ?", (artist_id,)
            ).fetchone()[0]
            for artist_id, _, _ in group
        }
        # Prefer the row that owns albums of its own (the canonical library
        # entity) and has already been enriched, over a guest-credit-only
        # stand-in; id is the final, fully deterministic tiebreak.
        keep_id, _, _ = max(
            group, key=lambda row: (album_counts[row[0]], row[2] is not None, row[0])
        )
        for artist_id, _, _ in group:
            if artist_id == keep_id:
                continue
            db.execute_sql("UPDATE album SET artist_id = ? WHERE artist_id = ?", (keep_id, artist_id))
            db.execute_sql("UPDATE track SET artist_id = ? WHERE artist_id = ?", (keep_id, artist_id))
            # artistgenre has a UNIQUE (artist, genre, source) index; "OR
            # IGNORE" skips just the individual rows that would collide with
            # a link the surviving artist already has, and the DELETE right
            # after clears out exactly those leftovers.
            db.execute_sql(
                "UPDATE OR IGNORE artistgenre SET artist_id = ? WHERE artist_id = ?",
                (keep_id, artist_id),
            )
            db.execute_sql("DELETE FROM artistgenre WHERE artist_id = ?", (artist_id,))
            db.execute_sql("DELETE FROM artist WHERE id = ?", (artist_id,))


def _migrate_19(db):
    """v18 to v19: corrective follow-up to v18 -- same live-``--reload`` race
    as ``_migrate_10``/``_migrate_13``/``_migrate_16``: ``SCHEMA_VERSION``
    was bumped to 18 in one save before ``_migrate_18``'s body existed in a
    second save, and a dev server watching this file reloaded in between.
    The version marker got recorded as v18 applied without the merge ever
    running, so duplicate Artist rows (same case-insensitive name) are still
    present. Just re-runs ``_migrate_18``'s body -- harmless (a no-op) on
    any install where it already merged cleanly.
    """
    _migrate_18(db)


_MIGRATIONS = [_migrate_1, _migrate_2, _migrate_3, _migrate_4,
               _migrate_5, _migrate_6, _migrate_7, _migrate_8, _migrate_9,
               _migrate_10, _migrate_11, _migrate_12, _migrate_13, _migrate_14,
               _migrate_15, _migrate_16, _migrate_17, _migrate_18, _migrate_19]


def run_migrations(db, current_version: int):
    """Run every migration newer than current_version, in order.

    NOTE: the caller wraps this in one transaction. SQLite makes
    `PRAGMA foreign_keys = OFF` a no-op inside a transaction, so table-rebuild
    migrations do NOT actually disable FK enforcement there. Any future rebuild
    migration must not rely on that pragma; prefer ADD COLUMN style changes
    (see _migrate_6) or do the FK-sensitive work outside the transaction.
    """
    for i, migrate_fn in enumerate(_MIGRATIONS):
        if current_version < i + 1:
            migrate_fn(db)
