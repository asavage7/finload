"""Library sync orchestration for all libraries."""

from datetime import datetime, timezone

from core.database import Track, track_scope_clause
from services.background import BackgroundJob

_CHECKPOINT_SOURCES = ("jellyfin", "local")

_FLUSH_EVERY = 2000


class SyncManager(BackgroundJob):
    EXTRA_STATE = {"added": 0, "updated": 0, "removed": 0}
    supports_force = True

    def __init__(self, db_manager, settings_manager):
        super().__init__()
        self.db = db_manager
        self.settings = settings_manager
        # Set by state.py once the other jobs exist, so a completed sync can
        # kick off follow-up enrichment without SyncManager knowing about them.
        self.follow_up_jobs = []

    def _checkpoint_key(self) -> str | None:
        """The settings key holding this source's last-synced timestamp, or
        None for a source that keeps no incremental checkpoint."""
        source = self.settings.get("library_source")
        return f"last_synced_at_{source}" if source in _CHECKPOINT_SOURCES else None

    def _sync_library_ids(self):
        """The library scope this sync targets: a pending selection if one is
        in flight (see routers/settings.py), otherwise the applied one."""
        pending = self.settings.get("jellyfin_library_ids_pending")
        return pending if pending is not None else self.settings.get("jellyfin_library_ids")

    @staticmethod
    def _canonical_artist_id(name_to_id: dict, artist_id: str, name: str) -> str:
        """Resolve artist_id to the one id this artist name should use.

        A provider can hand back different ids for the same artist depending on
        whether it's credited at album or track level, which would otherwise
        create a duplicate Artist row per credit. name_to_id starts seeded from
        the DB, so the first id ever seen for a name is the only one written.
        """
        key = name.strip().lower()
        canonical = name_to_id.get(key)
        if canonical is None:
            canonical = artist_id
            name_to_id[key] = canonical
        return canonical

    def _run(self, provider, force: bool = False):
        if not provider.is_configured():
            self._emit(status="error", message="Library source is not configured")
            return

        # Sync gets priority over any other jobs, so stop them while it runs and start when finished.
        for job in self.follow_up_jobs:
            job.stop()
        try:
            self._sync(provider, force)
        finally:
            for job in self.follow_up_jobs:
                job.start(force=False)

    def _sync(self, provider, force: bool):
        # Captured before any request goes out, so a track saved mid-sync is
        # picked up next time rather than missed.
        sync_started_at = datetime.now(timezone.utc).isoformat()
        checkpoint_key = self._checkpoint_key()
        since = None if force else (self.settings.get(checkpoint_key) if checkpoint_key else None)

        pending_library_ids = self.settings.get("jellyfin_library_ids_pending")
        local_query = Track.select(Track.id)
        scope = track_scope_clause(self._sync_library_ids())
        if scope is not None:
            local_query = local_query.where(scope)
        local_ids = set(local_query.scalars())

        reported = provider.fetch_changed_ids(since) if since else None
        full_sweep = reported is None

        if full_sweep:
            server_ids = provider.fetch_all_ids()
            stale_ids = local_ids - server_ids
            new_ids = server_ids - local_ids
            changed_ids = (server_ids & local_ids) if force else set()
        else:
            new_ids = reported - local_ids
            changed_ids = reported & local_ids
            stale_ids = set()

        ids_to_fetch = new_ids | changed_ids
        self._emit(total=len(ids_to_fetch), processed=0, added=len(new_ids), removed=len(stale_ids), updated=len(changed_ids),
                   message="Syncing library...")

        if stale_ids:
            self.db.delete_tracks(stale_ids)

        processed = self._fetch_and_store(provider, ids_to_fetch)
        self.db.prune_orphans() # Remove empty albums/artists/genres after the track deletions and upserts.

        if (full_sweep and pending_library_ids is not None
                and self.settings.get("library_source") == "jellyfin"):
            self.settings.set({"jellyfin_library_ids": pending_library_ids,
                               "jellyfin_library_ids_pending": None})

        if checkpoint_key:
            self.settings.set({checkpoint_key: sync_started_at})

        self._emit(status="complete", processed=processed, added=len(new_ids),
                   updated=len(changed_ids),
                   message=f"Added {len(new_ids)}, updated {len(changed_ids)}, removed {len(stale_ids)}")

    def _fetch_and_store(self, provider, ids_to_fetch) -> int:
        """Stream normalized items from the provider into the DB, flushing every
        _FLUSH_EVERY items. Returns the number of items processed."""
        # Seeded from the DB in one query so no per-artist lookup is needed.
        artist_ids_by_name = self.db.artist_ids_by_name()
        artists, albums, tracks, album_genres = {}, {}, [], set()
        processed = 0
        
        total_to_fetch = len(ids_to_fetch)

        def flush():
            if tracks:
                self.db.bulk_upsert(list(artists.values()), list(albums.values()),
                                    tracks, list(album_genres))
            artists.clear()
            albums.clear()
            tracks.clear()
            album_genres.clear()

        for item in provider.fetch_items_by_ids(list(ids_to_fetch)):
            id_remap = {}
            for artist in item["artists"]:
                canonical_id = self._canonical_artist_id(
                    artist_ids_by_name, artist["id"], artist["name"])
                id_remap[artist["id"]] = canonical_id
                if canonical_id == artist["id"]:
                    artists[canonical_id] = artist

            album_data, track_data = item["album_data"], item["track_data"]
            album_data["artist"] = id_remap.get(album_data["artist"], album_data["artist"])
            track_data["artist"] = id_remap.get(track_data["artist"], track_data["artist"])
            albums[album_data["id"]] = album_data
            tracks.append(track_data)

            # A set: every track on an album repeats that album's genres.
            for name in item.get("genres", []):
                album_genres.add((album_data["id"], name, album_data["provider"], 0))

            processed += 1
            if processed % 10 == 0:
                self._emit(
                    processed=processed, 
                    message=f"Syncing library... {processed} of {total_to_fetch} items"
                )
                
            if len(tracks) >= _FLUSH_EVERY:
                flush()

        self._emit(processed=processed, message="Saving...")
        flush()
        return processed
