"""Library sync orchestration.

Provider-agnostic: it drives any MediaProvider through the generic
fetch_all_ids / fetch_items_by_ids interface, accumulates normalized rows, and
writes them to the DB in a single bulk transaction. Progress is broadcast to
listeners (the /ws/jobs/sync WebSocket) via BackgroundJob.

Sync is incremental where the provider supports it (see
``MediaProvider.fetch_changed_ids``): a full ID sweep still runs every time to
catch removals (there's no cheaper way to notice something is gone), but
existing tracks only get their metadata re-fetched if the provider reports
them as changed since the last successful sync, instead of never being
re-fetched at all once they're first synced. Passing ``force=True`` skips
that gating and re-fetches every already-known track as well, for a full
re-sync.
"""
from datetime import datetime, timezone

from core.database import Track, track_scope_clause
from services.background import BackgroundJob

# library_source values that get their own incremental-sync checkpoint (see
# settings_manager.py's last_synced_at_<source> defaults).
_CHECKPOINT_SOURCES = ("jellyfin", "local")


class SyncManager(BackgroundJob):
    EXTRA_STATE = {"added": 0, "updated": 0, "removed": 0}
    supports_force = True

    def __init__(self, db_manager, settings_manager):
        super().__init__()
        self.db = db_manager
        self.settings = settings_manager
        # Set by state.py once the other jobs exist, so a completed sync can
        # kick off follow-up enrichment without SyncManager knowing about them
        # up front.
        self.follow_up_jobs = []

    def _checkpoint_key(self) -> str | None:
        """The settings key that stores this source's last-synced timestamp,
        or None for an unrecognized source (no incremental checkpoint kept)."""
        source = self.settings.get("library_source")
        return f"last_synced_at_{source}" if source in _CHECKPOINT_SOURCES else None

    def _canonical_artist_id(self, name_to_id: dict, artist_id: str, name: str) -> str:
        """Resolve ``artist_id`` to the one id this artist name should use.

        A provider can resolve the "same" artist to different ids depending
        on whether it's credited as an album artist or a track artist (e.g.
        Jellyfin minting a separate MusicArtist entity for a compilation
        track's performer credit — see providers/jellyfin.py). Reconciling by
        case-insensitive name here, against both this sync's own
        already-seen artists and the DB, means only the first id ever seen
        for a name gets written, so a second, differently-resolved id for
        the same person never becomes its own Artist row.
        """
        key = name.strip().lower()
        canonical = name_to_id.get(key)
        if canonical is None:
            canonical = self.db.find_artist_id_by_name(name) or artist_id
            name_to_id[key] = canonical
        return canonical

    def _run(self, provider, force: bool = False):
        if not provider.is_configured():
            self._emit(status="error", message="Library source is not configured")
            return

        # Sync gets priority over the other background jobs -- they can hit
        # Jellyfin (audio_features' stream downloads) and the shared SQLite
        # file just as hard as sync itself, so let a sync run alone rather
        # than piling concurrent load on either. Each paused job idles at
        # its own next wait_if_paused() checkpoint (a clean per-item
        # boundary, not mid-item) and picks back up right after resume() --
        # see BackgroundJob.pause(). The try/finally guarantees resume()
        # still runs if anything below raises, so a failed sync can never
        # leave the others stuck paused forever.
        for job in self.follow_up_jobs:
            job.pause()
        try:
            # Captured before any requests go out, so a track saved mid-sync is
            # simply picked up again next time rather than possibly missed.
            sync_started_at = datetime.now(timezone.utc).isoformat()
            checkpoint_key = self._checkpoint_key()
            since = None if force else (self.settings.get(checkpoint_key) if checkpoint_key else None)

            self._emit(status="running", message="Comparing with server…")
            # A pending library selection (still being backfilled -- see
            # routers/settings.py's select endpoint) is the target scope this
            # sync diffs against, same as provider.fetch_all_ids() below reads
            # via _selected_library_ids(); falls back to the applied selection
            # when no change is in flight. Using the target (not applied) scope
            # here is what keeps a narrowing selection safe: a track being
            # deselected is simply absent from both local_ids and server_ids
            # (the provider never fetches it either), so it's never mistaken for
            # one deleted on the server -- see track_scope_clause.
            pending_library_ids = self.settings.get("jellyfin_library_ids_pending")
            sync_library_ids = (pending_library_ids if pending_library_ids is not None
                               else self.settings.get("jellyfin_library_ids"))
            local_query = Track.select(Track.id)
            scope = track_scope_clause(sync_library_ids)
            if scope is not None:
                local_query = local_query.where(scope)
            local_ids = set(local_query.scalars())
            server_ids = provider.fetch_all_ids()

            stale_ids = local_ids - server_ids
            new_ids = server_ids - local_ids

            if force:
                # Full re-sync: re-fetch every already-known track too, not just
                # ones the provider reports as changed.
                changed_ids = server_ids & local_ids
            else:
                changed_ids = set()
                if since:
                    reported = provider.fetch_changed_ids(since)
                    if reported:
                        # Tracks we already have that were edited since last time.
                        # new_ids get a full fetch anyway, no need to ask for them twice.
                        changed_ids = (reported & server_ids) - new_ids

            ids_to_fetch = new_ids | changed_ids

            self._emit(total=len(ids_to_fetch), removed=len(stale_ids), updated=len(changed_ids),
                       message="Syncing library…")

            if stale_ids:
                self.db.delete_tracks(stale_ids)

            artists, albums, tracks = {}, {}, []
            # Name (lowercased) -> canonical id, memoized across this whole sync
            # so every item pays for at most one DB lookup per distinct artist
            # name. See _canonical_artist_id.
            artist_ids_by_name: dict[str, str] = {}
            album_genres = []
            processed = 0
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

                for name in item.get("genres", []):
                    album_genres.append((album_data["id"], name, album_data["provider"], 0))

                processed += 1
                if processed % 100 == 0:
                    self._emit(processed=processed)

            self._emit(processed=processed, message="Saving…")

            if tracks:
                self.db.bulk_upsert(list(artists.values()), list(albums.values()), tracks,
                                    album_genres)

            # Removing tracks can strand the albums/artists they belonged to, and
            # older libraries may already carry such orphans from before this ran
            # (or from a sync that failed to delete tracks). Prune every sync so
            # empty entries never linger, even when nothing was removed this pass.
            self.db.prune_orphans()

            # This sync just finished successfully against sync_library_ids (the
            # pending selection, if one was in flight) -- promote it to applied
            # now, atomically with the data it backfilled becoming visible, so
            # browsing never has a window where the new selection is "on" but
            # library_id backfill isn't done yet (see routers/settings.py's
            # select endpoint and track_scope_clause). Left untouched if this
            # sync raises before reaching here, so a failed resync keeps
            # browsing on the last-known-good selection instead of a half
            # applied one.
            if pending_library_ids is not None and self.settings.get("library_source") == "jellyfin":
                self.settings.set({"jellyfin_library_ids": pending_library_ids,
                                   "jellyfin_library_ids_pending": None})

            self._emit(status="complete", processed=processed, added=len(new_ids),
                       updated=len(changed_ids),
                       message=f"Added {len(new_ids)}, updated {len(changed_ids)}, removed {len(stale_ids)}")

            if checkpoint_key:
                self.settings.set({checkpoint_key: sync_started_at})

            # Kick off any registered follow-up jobs (artist bio enrichment, genre
            # enrichment, ...) for anything left un-enriched. Started while still
            # paused (each blocks at its first wait_if_paused()) so they don't
            # slip in ahead of the finally below and start doing real work a
            # moment before resume() actually lets them.
            for job in self.follow_up_jobs:
                job.start(force=False)
        finally:
            for job in self.follow_up_jobs:
                job.resume()
