"""Library sync orchestration.

Provider-agnostic: it drives any MediaProvider through the generic
fetch_all_ids / fetch_items_by_ids interface, accumulates normalized rows, and
writes them to the DB in a single bulk transaction. Progress is broadcast to
listeners (the /ws/sync WebSocket) using the same listener pattern as
PlaybackManager.
"""
import threading

from database import Track


class SyncManager:
    def __init__(self, db_manager):
        self.db = db_manager
        self.metadata = None  # Set by main.py after MetadataManager is created
        self.listeners = []
        self._lock = threading.Lock()
        self.state = {
            "status": "idle",   # idle | running | complete | error
            "message": "",
            "processed": 0,
            "total": 0,
            "added": 0,
            "removed": 0,
        }

    # --- listeners ---------------------------------------------------------
    def add_listener(self, callback):
        self.listeners.append(callback)
        # Send current state immediately so a new client renders the right thing.
        callback(dict(self.state))

    def remove_listener(self, callback):
        if callback in self.listeners:
            self.listeners.remove(callback)

    def _emit(self, **changes):
        self.state.update(changes)
        snapshot = dict(self.state)
        for listener in self.listeners:
            try:
                listener(snapshot)
            except Exception:
                pass

    # --- control -----------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self.state["status"] == "running"

    def start(self, provider) -> bool:
        """Kick off a sync in a background thread. Returns False if already running."""
        with self._lock:
            if self.is_running:
                return False
            self.state.update(status="running", message="Starting…",
                              processed=0, total=0, added=0, removed=0)
        threading.Thread(target=self._run, args=(provider,), daemon=True).start()
        return True

    # --- worker ------------------------------------------------------------
    def _run(self, provider):
        try:
            if not provider.is_configured():
                self._emit(status="error", message="Library source is not configured")
                return

            self._emit(status="running", message="Comparing with server…")
            local_ids = set(Track.select(Track.id).scalars())
            server_ids = provider.fetch_all_ids()

            stale_ids = local_ids - server_ids
            new_ids = server_ids - local_ids

            self._emit(total=len(new_ids), removed=len(stale_ids),
                       message="Syncing library…")

            if stale_ids:
                Track.delete().where(Track.id << list(stale_ids)).execute()

            artists, albums, tracks = {}, {}, []
            processed = 0
            for item in provider.fetch_items_by_ids(list(new_ids)):
                for artist in item["artists"]:
                    artists[artist["id"]] = artist
                albums[item["album_data"]["id"]] = item["album_data"]
                tracks.append(item["track_data"])

                processed += 1
                if processed % 100 == 0:
                    self._emit(processed=processed)

            self._emit(processed=processed, message="Saving…")

            if tracks:
                self.db.bulk_upsert(list(artists.values()), list(albums.values()), tracks)
                self._emit(message="Indexing for search…")
                self.db.rebuild_fts()

            self._emit(status="complete", processed=processed, added=len(tracks),
                       message=f"Added {len(tracks)}, removed {len(stale_ids)}")

            # Kick off online metadata enrichment for any un-enriched items.
            if self.metadata:
                self.metadata.start_background_enrichment(force=False)
        except Exception as exc:
            self._emit(status="error", message=str(exc))
