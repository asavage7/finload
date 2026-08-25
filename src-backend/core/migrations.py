"""Schema migrations.

Two layers:

1. Automatic column reconciliation (ensure_model_columns): compares each model
   against the live table and adds any missing columns. This covers the common
   "new field on a model" case with no migration code at all.

2. Numbered manual migrations (run_migrations): for anything the automatic layer
   can't do, like table rebuilds, renames, data transforms, or dropping columns.
   Add a function and append it to _MIGRATIONS; SCHEMA_VERSION is derived from
   the list, so there is no second place to keep in step.

Nothing here imports the models module. Everything takes the database handle
as an argument so this module stays import-cycle free.
"""
import hashlib

from playhouse.migrate import SqliteMigrator, migrate as _apply

# The oldest schema this build can start from. Versions 1-19 were squashed away:
# every tagged release has shipped 19, so only a pre-release database can sit
# below it, and the steps that walked 0 to 19 did table rebuilds and id remaps
# that the automatic layer cannot reproduce. Applying today's models over such a
# database would corrupt it quietly, so it is rejected instead.
BASELINE_VERSION = 19


class UnsupportedSchemaError(RuntimeError):
    """The database predates the oldest schema this build can migrate from."""


def stable_genre_id(name: str) -> str:
    """Deterministic id for a genre name, the same convention as local-provider
    Track/Album/Artist ids (providers/local.py's _stable_hash): the same genre
    resolves to the same id on every install, regardless of insertion order,
    instead of an autoincrement integer that's meaningless outside one database.
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


def _migrate_20(db):
    """v19 to v20: drop the search-history table. Every search wrote a row to it
    and nothing ever read one back, so the feature was removed; the table would
    otherwise sit in the schema with no model describing it."""
    db.execute_sql("DROP TABLE IF EXISTS searchhistory")


# Numbered from BASELINE_VERSION + 1, so _MIGRATIONS[0] runs on a database still
# at BASELINE_VERSION. Append here for anything the automatic column
# reconciliation above can't do; SCHEMA_VERSION follows on its own.
_MIGRATIONS = [_migrate_20]

SCHEMA_VERSION = BASELINE_VERSION + len(_MIGRATIONS)


def run_migrations(db, current_version: int):
    """Run every migration newer than current_version, in order.

    NOTE: the caller wraps this in one transaction. SQLite makes
    `PRAGMA foreign_keys = OFF` a no-op inside a transaction, so table-rebuild
    migrations do NOT actually disable FK enforcement there. Any future rebuild
    migration must not rely on that pragma; prefer ADD COLUMN style changes or
    do the FK-sensitive work outside the transaction.
    """
    if current_version < BASELINE_VERSION:
        raise UnsupportedSchemaError(
            f"This database is at schema version {current_version}, older than the "
            f"oldest supported version ({BASELINE_VERSION}). It was created by a "
            f"pre-release build. Delete the database file and let Finload re-sync."
        )
    for offset, migrate_fn in enumerate(_MIGRATIONS):
        if current_version < BASELINE_VERSION + 1 + offset:
            migrate_fn(db)
