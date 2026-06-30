import mpv
from database import Track, Album, Artist, QueueItem, PlaybackState, PlayHistory, db, _chunks
import datetime
import random
import threading

class PlaybackManager:
    def __init__(self, provider, settings):
        self.provider = provider
        self.settings = settings
        self.player = mpv.MPV(
            ytdl=False,
            osc=False,
            vid='no',
            config='no',
            profile='low-latency',
        )

        self.player['gapless-audio'] = 'yes'
        self.player['audio-buffer'] = 0.2
        self.player['demuxer-max-bytes'] = settings.get('mpv_buffer_size') or '25M'

        settings.add_listener(self._on_setting_changed)

        self.listeners = []
        self.queue_state = []
        self.queue_dirty = True
        self.repeat_mode = 0  # 0=off, 1=all, 2=one
        self.shuffle = False
        self._intentional_stop = False  # suppresses queue-end handling for clear/source-switch
        self.shuffle_original_positions: dict = {}
        self.current_state = {
            "time_pos": 0,
            "duration": 0,
            "is_paused": True,
            "volume": 100,
            "current_track": None,
            "queue": [],
            "lyrics": None,
            "repeat_mode": 0,
            "shuffle": False,
        }

        self.prev_state = {}
        self.last_broadcast_time = 0
        self._broadcast_lock = threading.Lock()

        @self.player.property_observer('volume')
        def on_volume_change(name, value):
            if value is not None:
                self.current_state["volume"] = int(value)
                self.broadcast_state()

        @self.player.property_observer('time-pos')
        def on_time_pos_change(name, value):
            if value is not None:
                if abs(int(value) - int(self.last_broadcast_time)) >= 1:
                    self.current_state["time_pos"] = value
                    self.last_broadcast_time = value
                    self.broadcast_state()

        @self.player.property_observer('pause')
        def on_pause_change(name, value):
            if value is not None:
                self.current_state["is_paused"] = value
                self.broadcast_state()

        @self.player.property_observer('duration')
        def on_duration_change(name, value):
            if value is not None:
                old = self.current_state.get("duration", 0)
                # Ignore small updates (< 2 s) to prevent VBR estimation jitter.
                # The initial value per track is seeded from the DB in refresh_track_cache,
                # so old == 0 only when there was no DB metadata.
                if old == 0 or abs(value - old) > 2.0:
                    self.current_state["duration"] = value
                    self.broadcast_state()

        @self.player.property_observer('playlist-pos')
        def on_playlist_pos_change(name, value):
            if value is not None and value > 0:
                self.advance_queue()
                self.prepare_next()
                self.refresh_track_cache()
                self.broadcast_state()
                try:
                    self.player.command('playlist-remove', 0)
                except Exception:
                    pass
            elif value is None or value < 0:
                # mpv signals exhausted playlist as -1 (python-mpv may pass it as -1 or None)
                self._handle_queue_end()

        @self.player.property_observer('idle-active')
        def on_idle_active(name, value):
            # Belt-and-suspenders: fires when mpv goes idle after playlist exhaustion,
            # covering cases where playlist-pos doesn't transition through -1.
            if value:
                self._handle_queue_end()

        self.refresh_track_cache()

    # -----------------------------------------------------------------------
    # PlaybackState helpers — single source of truth for "current" item
    # -----------------------------------------------------------------------

    def _get_current(self):
        """Return the current QueueItem via PlaybackState, or None."""
        state = PlaybackState.get_or_none(PlaybackState.id == 1)
        if state is None or state.current_queue_item_id is None:
            return None
        try:
            return state.current_queue_item
        except QueueItem.DoesNotExist:
            return None

    def _set_current(self, queue_item):
        """Update PlaybackState to point to queue_item (may be None)."""
        rows = PlaybackState.update(current_queue_item=queue_item).where(PlaybackState.id == 1).execute()
        if rows == 0:
            PlaybackState.create(id=1, current_queue_item=queue_item)

    # -----------------------------------------------------------------------
    # Settings
    # -----------------------------------------------------------------------

    def _on_setting_changed(self, key, value):
        if key == 'mpv_buffer_size':
            self.player['demuxer-max-bytes'] = value

    # -----------------------------------------------------------------------
    # Listeners / state broadcasting
    # -----------------------------------------------------------------------

    def add_listener(self, callback):
        self.listeners.append(callback)
        if self.queue_dirty or not self.queue_state:
            self.queue_state = self.build_queue_state()
            self.queue_dirty = False
        callback({**self.current_state, "queue": self.queue_state})

    def remove_listener(self, callback):
        if callback in self.listeners:
            self.listeners.remove(callback)

    def refresh_track_cache(self):
        """Fetches the track from the DB and caches it as a dictionary."""
        current_item = self._get_current()
        if current_item and current_item.track:
            track = current_item.track
            try:
                album_id = str(track.album.id)
                album_name = str(track.album.title)
            except Exception:
                album_id = None
                album_name = "Unknown Album"

            self.current_state["current_track"] = {
                "id": getattr(track, 'id', None),
                "album_id": album_id,
                "artist_id": str(track.artist.id) if track.artist else None,
                "album_name": album_name,
                "title": getattr(track, 'title', "Unknown Title"),
                "artist_name": track.artist.name if track.artist else "Unknown Artist",
                "rating": getattr(track, 'rating', 0),
            }
            self.current_state["is_paused"] = self.player.pause
            # Seed duration from DB so on_duration_change ignores small VBR jitter.
            db_duration = track.duration_ms / 1000.0 if track.duration_ms else 0
            self.current_state["duration"] = db_duration
        else:
            self.current_state["current_track"] = None
            self.current_state["is_paused"] = True

    def build_queue_state(self):
        """Builds a list of queue items with their associated track info."""
        current = self._get_current()
        current_id = current.id if current else None

        queue_state = []
        # Eager-join Track/Album/Artist so the per-item field access below doesn't
        # trigger a lazy FK load (≈3 queries per row) on every broadcast.
        query = (QueueItem.select(QueueItem, Track, Artist, Album)
                 .join(Track).join(Artist).switch(Track).join(Album)
                 .order_by(QueueItem.position.asc()))
        for item in query:
            track_info = {
                "id": getattr(item.track, 'id', None),
                "album_id": str(item.track.album.id) if item.track else None,
                "album_name": str(item.track.album.title) if item.track else "Unknown Album",
                "title": getattr(item.track, 'title', "Unknown Title") if item.track else "Unknown Title",
                "artist_name": item.track.artist.name if item.track and item.track.artist else "Unknown Artist",
                "runtime": getattr(item.track, 'duration_ms', 0) if item.track else 0,
            }
            queue_state.append({
                "id": item.id,
                "track": track_info,
                "queue_type": item.queue_type,
                "position": item.position,
                "is_current": item.id == current_id
            })
        return queue_state

    def broadcast_state(self):
        """Sends the current playback state to all connected listeners. Only sends diffs.

        Serialized with a lock: mpv property observers fire from mpv's thread while
        API handlers run on uvicorn threads. Without the lock, two concurrent calls
        can both read queue_dirty=True, both rebuild queue_state, and one can clear
        prev_state with stale data — causing the queue to appear empty on the client.
        """
        with self._broadcast_lock:
            queue_changed = self.queue_dirty or not self.prev_state
            if queue_changed:
                self.queue_state = self.build_queue_state()
                self.queue_dirty = False

            snapshot = {**self.current_state, "queue": self.queue_state}
            diff = {
                key: value
                for key, value in self.current_state.items()
                if key != "queue" and value != self.prev_state.get(key)
            }

            if queue_changed:
                diff["queue"] = self.queue_state

            if diff:
                for listener in self.listeners:
                    try:
                        listener(diff)
                    except Exception:
                        pass
                self.prev_state = snapshot.copy()

    # -----------------------------------------------------------------------
    # Queue navigation helpers
    # -----------------------------------------------------------------------

    def _restore_state(self):
        """Loads the current queue item if app was restarted."""
        current = self._get_current()
        if current:
            self.player.command('loadfile', self.provider.get_stream_url(current.track_id))
            self.player.pause = True
            self.prepare_next()

    def _get_next_track(self, current_position):
        return QueueItem.select().where(
            QueueItem.position > current_position
        ).order_by(QueueItem.position.asc()).first()

    def _get_prev_track(self, current_position):
        return QueueItem.select().where(
            QueueItem.position < current_position
        ).order_by(QueueItem.position.desc()).first()

    # -----------------------------------------------------------------------
    # Playback control
    # -----------------------------------------------------------------------

    def play_now(self, track_id, context_ids=None):
        """Clears the queue, loads the context, and starts playing track_id."""
        self._intentional_stop = False
        new_current = None
        current_pos = None
        with db.atomic():
            QueueItem.delete().execute()
            if context_ids:
                rows = []
                for i, tid in enumerate(context_ids):
                    rows.append({"track": tid, "queue_type": 1, "position": float(i)})
                    if current_pos is None and tid == track_id:
                        current_pos = float(i)
                # Batched insert instead of one INSERT per track — a "play artist"
                # can enqueue a whole discography in a single round-trip per chunk.
                for batch in _chunks(rows, 100):
                    QueueItem.insert_many(batch).execute()
            if current_pos is not None:
                new_current = QueueItem.get_or_none(QueueItem.position == current_pos)
            self._set_current(new_current)
        # Set queue_dirty AFTER the transaction commits so any concurrent
        # broadcast_state() call triggered by mpv observers sees a fully
        # populated queue, not the briefly-empty state between delete and inserts.
        self.queue_dirty = True

        self.shuffle_original_positions = {}
        if self.shuffle and new_current:
            self._apply_shuffle(new_current)

        self.player.play(self.provider.get_stream_url(track_id))
        self.player.pause = False
        self.prepare_next()
        self.refresh_track_cache()
        self.broadcast_state()

    def add_to_play_next(self, track_ids, top=False):
        """Inserts songs directly after the currently playing song."""
        current = self._get_current()
        if not current:
            return

        amt = len(track_ids)

        with db.atomic():
            if top:
                insert_pos = current.position + 1.0
            else:
                last_priority = QueueItem.select().where(
                    (QueueItem.queue_type == 0) &
                    (QueueItem.position > current.position)
                ).order_by(QueueItem.position.desc()).first()
                insert_pos = (last_priority.position + 1.0) if last_priority else (current.position + 1.0)

            QueueItem.update(position=QueueItem.position + amt).where(QueueItem.position >= insert_pos).execute()

            for i, tid in enumerate(track_ids):
                QueueItem.create(track_id=tid, queue_type=0, position=insert_pos + i)

        self.queue_dirty = True
        try:
            self.player.command('playlist-clear')
        except Exception:
            pass
        self.prepare_next()
        self.broadcast_state()

    def add_to_queue(self, track_ids, index=-1):
        """Inserts songs at the end of the user-curated queue (before the Mix)."""
        last_curated = QueueItem.select().where(QueueItem.queue_type <= 1).order_by(QueueItem.position.desc()).first()
        insert_pos = float(index) if index != -1 else ((last_curated.position + 1.0) if last_curated else 0.0)
        amt = len(track_ids)

        with db.atomic():
            QueueItem.update(position=QueueItem.position + amt).where(QueueItem.position >= insert_pos).execute()
            for i, tid in enumerate(track_ids):
                QueueItem.create(track=tid, queue_type=1, position=insert_pos + i)

        self.queue_dirty = True
        self.prepare_next()
        self.broadcast_state()

    def prepare_next(self):
        current = self._get_current()
        if not current:
            return
        if self.repeat_mode == 2:
            # Repeat one is handled natively by mpv's loop-file; no preload needed.
            return
        next_track = self._get_next_track(current.position)
        if next_track is None and self.repeat_mode == 1:
            next_track = QueueItem.select().order_by(QueueItem.position.asc()).first()
        if next_track:
            self.player.command('loadfile', self.provider.get_stream_url(next_track.track_id), 'append')

    def _replace_current_track(self, queue_item):
        try:
            self.player.command('playlist-clear')
        except Exception:
            pass
        self.player.play(self.provider.get_stream_url(queue_item.track_id))

    def _handle_queue_end(self):
        """Called when mpv's playlist is exhausted (last track finished naturally).
        Resets the current position to the first track at time 0 so the user can replay."""
        if self._intentional_stop:
            self._intentional_stop = False
            return
        current = self._get_current()
        if not current:
            return
        if not QueueItem.select().exists():
            return
        first_track = QueueItem.select().order_by(QueueItem.position.asc()).first()
        if first_track:
            self._set_current(first_track)
            self.queue_dirty = True
        self.last_broadcast_time = 0
        self.refresh_track_cache()
        # Set AFTER refresh_track_cache, which reads self.player.pause and would
        # overwrite these: mpv reports pause=False when idle after playlist exhaustion.
        self.current_state["time_pos"] = 0
        self.current_state["is_paused"] = True
        self.broadcast_state()

    def advance_queue(self):
        """Called automatically by MPV when a track finishes."""
        current = self._get_current()
        self.last_broadcast_time = 0
        if not current:
            return

        # Repeat one never reaches here: mpv's loop-file keeps playlist-pos at 0,
        # so the track simply restarts without advancing the queue.
        next_track = self._get_next_track(current.position)
        if next_track is None and self.repeat_mode == 1:
            next_track = QueueItem.select().order_by(QueueItem.position.asc()).first()
        if next_track:
            self._set_current(next_track)
            self.queue_dirty = True

        self.refresh_track_cache()
        self.broadcast_state()

    def skip_prev(self):
        if float(self.time_pos or 0) > 3.0:
            self.seek(0)
            return

        current = self._get_current()
        if not current:
            return

        prev_track = self._get_prev_track(current.position)
        if prev_track:
            self._set_current(prev_track)
            self.queue_dirty = True
            self._replace_current_track(prev_track)
            self.prepare_next()
            self.refresh_track_cache()
            self.broadcast_state()
        else:
            self.seek(0)

    def skip_next(self):
        current = self._get_current()
        if not current:
            return
        PlayHistory.create(
            track=current.track,
            played_at=datetime.datetime.now() - datetime.timedelta(seconds=float(self.time_pos)),
            completion_pct=(float(self.time_pos) / float(self.duration)) * 100.0
        )

        next_track = self._get_next_track(current.position)
        if next_track:
            self._set_current(next_track)
            self.queue_dirty = True
            self._replace_current_track(next_track)
            self.prepare_next()
            self.refresh_track_cache()
            self.broadcast_state()
        else:
            self.seek(current.track.duration_ms / 1000.0)
            self.toggle_pause()
            self.refresh_track_cache()
            self.broadcast_state()

    def jump_to_queue_item(self, item_id):
        """Forces playback to jump immediately to a specific queue item."""
        target_item = QueueItem.get_or_none(QueueItem.id == item_id)
        current = self._get_current()

        if not target_item or (current and current.id == target_item.id):
            return

        self._set_current(target_item)
        self.queue_dirty = True
        self._replace_current_track(target_item)
        self.prepare_next()
        self.refresh_track_cache()
        self.broadcast_state()

    def remove_from_queue(self, item_id):
        """Removes a specific item from the queue. Skips to next if it was current."""
        target_item = QueueItem.get_or_none(QueueItem.id == item_id)
        if not target_item:
            return

        current = self._get_current()
        is_current = current is not None and current.id == target_item.id

        with db.atomic():
            if is_current:
                next_item = self._get_next_track(target_item.position)
                self._set_current(next_item)
            target_item.delete_instance()
            # Float positions: no renormalization needed — gaps are fine.
        self.shuffle_original_positions.pop(target_item.id, None)

        self.queue_dirty = True

        if is_current:
            self.refresh_track_cache()
            self.player.play(self.provider.get_stream_url(next_item.track_id) if next_item else None)
            self.prepare_next()
            self.broadcast_state()
        else:
            self.prepare_next()
            self.refresh_track_cache()
            self.broadcast_state()

    def clear_queue(self):
        """Removes all items from the queue and stops playback."""
        self._intentional_stop = True
        with db.atomic():
            QueueItem.delete().where(True).execute()
            # ON DELETE SET NULL wires PlaybackState.current_queue_item_id → NULL automatically.
        self.queue_dirty = True
        try:
            self.player.command('playlist-clear')
            self.player.stop()
        except Exception:
            pass
        self.refresh_track_cache()
        self.broadcast_state()

    def stop_for_source_switch(self):
        """Halt playback and drop cached queue state ahead of a DB swap.

        The library source (and its database) is about to change, so anything
        loaded from the old database — the mpv playlist, the cached queue — is
        no longer valid. State is re-read lazily from the new DB on next access.
        """
        self._intentional_stop = True
        try:
            self.player.command('playlist-clear')
            self.player.stop()
        except Exception:
            pass
        self.queue_dirty = True
        self.queue_state = []
        self.current_state["current_track"] = None
        self.current_state["is_paused"] = True
        self.broadcast_state()

    def reorder_queue(self, item_id: int, target_index: int):
        """Moves an item to target_index (0-based) using float midpoint — O(1), no cascade shifts."""
        dragged_item = QueueItem.get_or_none(QueueItem.id == item_id)
        if not dragged_item:
            return

        sorted_items = list(
            QueueItem.select()
            .where(QueueItem.id != item_id)
            .order_by(QueueItem.position)
        )

        if target_index <= 0:
            new_pos = (sorted_items[0].position - 1.0) if sorted_items else 0.0
        elif target_index >= len(sorted_items):
            new_pos = (sorted_items[-1].position + 1.0) if sorted_items else 0.0
        else:
            new_pos = (sorted_items[target_index - 1].position + sorted_items[target_index].position) / 2.0

        dragged_item.position = new_pos
        dragged_item.save()

        self.queue_dirty = True
        self.prepare_next()
        self.broadcast_state()

    # -----------------------------------------------------------------------
    # Shuffle / Repeat
    # -----------------------------------------------------------------------

    def _apply_shuffle(self, current):
        """Moves the current track to the top and shuffles every other item below
        it. Records each item's original position so the shuffle is reversible."""
        items = list(QueueItem.select().order_by(QueueItem.position.asc()))
        if len(items) <= 1:
            return

        self.shuffle_original_positions = {item.id: item.position for item in items}

        others = [item for item in items if item.id != current.id]
        random.shuffle(others)

        # Current keeps its position as the anchor; everything else is laid
        # out sequentially after it, making the current track the new top.
        for offset, item in enumerate(others, start=1):
            item.position = current.position + offset
        with db.atomic():
            # One CASE-based UPDATE per batch instead of an UPDATE per row.
            QueueItem.bulk_update(others, fields=[QueueItem.position], batch_size=100)
        self.queue_dirty = True

    def set_shuffle(self, enabled: bool):
        if self.shuffle == enabled:
            return
        self.shuffle = enabled
        self.current_state["shuffle"] = enabled
        current = self._get_current()

        if enabled and current:
            self._apply_shuffle(current)
        elif not enabled and self.shuffle_original_positions:
            orig = self.shuffle_original_positions
            restored = list(QueueItem.select().where(QueueItem.id << list(orig.keys())))
            for item in restored:
                item.position = orig[item.id]
            with db.atomic():
                QueueItem.bulk_update(restored, fields=[QueueItem.position], batch_size=100)
            self.shuffle_original_positions = {}
            self.queue_dirty = True

        try:
            self.player.command('playlist-clear')
        except Exception:
            pass
        self.prepare_next()
        # Caller is responsible for sending the updated state (avoid calling
        # broadcast_state from within the asyncio event loop thread)

    def set_repeat(self, mode: int):
        self.repeat_mode = max(0, min(2, int(mode)))
        self.current_state["repeat_mode"] = self.repeat_mode
        # Repeat one loops the current file natively in mpv; the other modes
        # advance through the queue, so the file must not loop.
        self.player['loop-file'] = 'inf' if self.repeat_mode == 2 else 'no'
        try:
            self.player.command('playlist-clear')
        except Exception:
            pass
        self.prepare_next()
        # Caller is responsible for sending the updated state

    # -----------------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------------

    @property
    def current_track(self):
        current_queue_item = self._get_current()
        return current_queue_item.track if current_queue_item else None

    @property
    def is_paused(self): return self.player.pause

    def toggle_pause(self): self.player.pause = not self.player.pause

    @property
    def time_pos(self): return self.player.time_pos or 0

    @property
    def duration(self): return self.player.duration or 1

    def seek(self, seconds: float):
        if self.player.duration:
            self.player.time_pos = seconds

    def set_volume(self, volume: int):
        self.player.volume = max(0, min(100, int(volume)))
