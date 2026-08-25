"""Library sync orchestration.

Provider-agnostic: drives any MediaProvider through fetch_all_ids /
fetch_changed_ids / fetch_items_by_ids, normalizes what comes back, and writes
it to the DB in bulk. Progress is broadcast via BackgroundJob.

Two modes. Quick path: ask the provider what changed since the last checkpoint
and fetch only that -- cheap, but it can't see removals. Full path: sweep every
id; used on the first sync and whenever force=True, and it's the only path that
reconciles removals or re-fetches every known track.
"""
from datetime import datetime, timezone

from core.database import Track, track_scope_clause
from services.background import BackgroundJob

# library_source values that get their own incremental-sync checkpoint (see
# settings_manager.py's last_synced_at_<source> defaults).
_CHECKPOINT_SOURCES = ("jellyfin", "local")

# Items accumulated before a write. Bounds peak memory on a large library and
# means a mid-sync failure keeps everything already flushed.
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

        # Sync gets priority: the follow-up jobs hit the same server and the
        # same SQLite file, so let it run alone rather than piling on load. The
        # finally guarantees resume() even if this raises, so a failed sync can
        # never leave them paused forever.
        for job in self.follow_up_jobs:
            job.pause()
        try:
            self._sync(provider, force)
        finally:
            for job in self.follow_up_jobs:
                job.resume()

    def _sync(self, provider, force: bool):
        # Captured before any request goes out, so a track saved mid-sync is
        # picked up next time rather than missed.
        sync_started_at = datetime.now(timezone.utc).isoformat()
        checkpoint_key = self._checkpoint_key()
        since = None if force else (self.settings.get(checkpoint_key) if checkpoint_key else None)

        # Diffing against the target (not applied) scope is what keeps a
        # narrowing selection safe: a deselected track is absent from both
        # sides, so it is never mistaken for one deleted on the server.
        pending_library_ids = self.settings.get("jellyfin_library_ids_pending")
        local_query = Track.select(Track.id)
        scope = track_scope_clause(self._sync_library_ids())
        if scope is not None:
            local_query = local_query.where(scope)
        local_ids = set(local_query.scalars())

        # None means the provider can't report changes at all (the local
        # provider never can), so fall back to the full sweep.
        reported = provider.fetch_changed_ids(since) if since else None
        full_sweep = reported is None

        if full_sweep:
            self._emit(status="running", message="Comparing with server...")
            server_ids = provider.fetch_all_ids()
            stale_ids = local_ids - server_ids
            new_ids = server_ids - local_ids
            # A forced full re-sync also re-fetches every already-known track.
            changed_ids = (server_ids & local_ids) if force else set()
        else:
            self._emit(status="running", message="Checking for changes...")
            new_ids = reported - local_ids
            changed_ids = reported & local_ids
            stale_ids = set()

        ids_to_fetch = new_ids | changed_ids
        self._emit(total=len(ids_to_fetch), removed=len(stale_ids), updated=len(changed_ids),
                   message="Syncing library...")

        if stale_ids:
            self.db.delete_tracks(stale_ids)

        processed = self._fetch_and_store(provider, ids_to_fetch)

        # Removing tracks strands the albums/artists they belonged to, and older
        # libraries may already carry such orphans. Prune every sync.
        self.db.prune_orphans()

        # Promote the pending selection now that the data it needs is in place,
        # atomically with that data becoming visible. Only after a full sweep:
        # the quick path only ever backfills library_id for changed tracks, so
        # promoting there would hide every track it didn't touch.
        if (full_sweep and pending_library_ids is not None
                and self.settings.get("library_source") == "jellyfin"):
            self.settings.set({"jellyfin_library_ids": pending_library_ids,
                               "jellyfin_library_ids_pending": None})

        if checkpoint_key:
            self.settings.set({checkpoint_key: sync_started_at})

        self._emit(status="complete", processed=processed, added=len(new_ids),
                   updated=len(changed_ids),
                   message=f"Added {len(new_ids)}, updated {len(changed_ids)}, removed {len(stale_ids)}")

        # Started while still paused (each blocks at its first wait_if_paused)
        # so they don't begin real work before _run's finally resumes them.
        for job in self.follow_up_jobs:
            job.start(force=False)

    def _fetch_and_store(self, provider, ids_to_fetch) -> int:
        """Stream normalized items from the provider into the DB, flushing every
        _FLUSH_EVERY items. Returns the number of items processed."""
        # Seeded from the DB in one query so no per-artist lookup is needed.
        artist_ids_by_name = self.db.artist_ids_by_name()
        artists, albums, tracks, album_genres = {}, {}, [], set()
        processed = 0

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
            if processed % 100 == 0:
                self._emit(processed=processed)
            if len(tracks) >= _FLUSH_EVERY:
                flush()

        self._emit(processed=processed, message="Saving...")
        flush()
        return processed
