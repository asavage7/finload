import mpv
from database import Track, QueueItem, PlayHistory, db
import datetime

class PlaybackManager:
    def __init__(self, bridge):
        self.bridge = bridge
        self.player = mpv.MPV(
        ytdl=False, 
        osc=False,
        vid='no',
        config='no',
        profile='low-latency',
        )
        
        self.player['gapless-audio'] = 'yes'
        self.player['audio-buffer'] = 0.2
        self.player['demuxer-max-bytes'] = '15M'
        
        self.listeners = []
        self.queue_state = []
        self.queue_dirty = True
        self.current_state = {
            "time_pos": 0,
            "duration": 0,
            "is_paused": True,
            "current_track": None,
            "queue": [],
            "lyrics": None
        }
        
        # Set previous state to be empty so the first broadcast sends everything
        self.prev_state = {}

        self.last_broadcast_time = 0

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

        self.refresh_track_cache()
                
    def add_listener(self, callback):
        self.listeners.append(callback)
        # Send the actual playback state so new clients start from the real mpv state.
        if self.queue_dirty or not self.queue_state:
            self.queue_state = self.build_queue_state()
            self.queue_dirty = False

        callback({**self.current_state, "queue": self.queue_state})

    def refresh_track_cache(self):
        """Fetches the track from the DB and caches it as a dictionary."""
        current_item = QueueItem.get_or_none(QueueItem.is_current == True)
        if current_item and current_item.track:
            track = current_item.track
            try:
                album_id = str(track.album.secondary_id) if hasattr(track.album, 'secondary_id') else str(track.album.id)
                album_name = str(track.album.title) if hasattr(track.album, 'title') else "Unknown Album"
            except Exception:
                album_id = None
                album_name = "Unknown Album"
                
            self.current_state["current_track"] = {
                "id": getattr(track, 'id', None),
                "album_id": album_id,
                "artist_id": str(track.artist.secondary_id) if track.artist and getattr(track.artist, 'secondary_id', None) else (str(track.artist.id) if track.artist else None),
                "album_name": album_name,
                "title": getattr(track, 'title', "Unknown Title"),
                "artist_name": track.artist.name if track.artist else "Unknown Artist",
                "rating": getattr(track, 'rating', 0)
            }
            self.current_state["is_paused"] = self.player.pause
        else:
            self.current_state["current_track"] = None
            self.current_state["is_paused"] = True

    def remove_listener(self, callback):
        if callback in self.listeners:
            self.listeners.remove(callback)
            
    def build_queue_state(self):
        """Builds a list of queue items with their associated track info."""
        queue_state = []
        for item in QueueItem.select().order_by(QueueItem.position.asc()):
            track_info = {
                "id": getattr(item.track, 'id', None),
                "album_id": str(item.track.album.secondary_id) if hasattr(item.track.album, 'secondary_id') else str(item.track.album.id),
                "album_name": str(item.track.album.title) if item.track and hasattr(item.track.album, 'title') else "Unknown Album",
                "title": getattr(item.track, 'title', "Unknown Title") if item.track else "Unknown Title",
                "artist_name": item.track.artist.name if item.track and item.track.artist else "Unknown Artist",
                "runtime": getattr(item.track, 'duration_ms', 0) if item.track else 0
            }
            queue_state.append({
                "id": item.id,
                "track": track_info,
                "queue_type": item.queue_type,
                "position": item.position,
                "is_current": item.is_current
            })
        return queue_state

    def broadcast_state(self):
        """Sends the current playback state to all connected listeners. Only sends diffs to minimize payload size."""
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

    def _restore_state(self):
        """Loads the current queue item if app was restarted."""
        current = QueueItem.get_or_none(QueueItem.is_current == True)
        if current:
            self.player.command('loadfile', self.bridge.get_stream_url(current.track_id))
            self.player.pause = True
            self.prepare_next()

    def _get_next_track(self, current_position):
        """Safely finds the next track, completely ignoring gaps in positions."""
        return QueueItem.select().where(
            QueueItem.position > current_position
        ).order_by(QueueItem.position.asc()).first()

    def play_now(self, track_id, context_ids=None):
        """Clears the queue, loads the album, and sets absolute positions."""
        with db.atomic():
            QueueItem.delete().execute() # Clear everything
            self.queue_dirty = True
            
            if context_ids:
                for i, tid in enumerate(context_ids):
                    QueueItem.create(
                        track=tid, 
                        queue_type=1,
                        position=i,
                        is_current=(tid == track_id)
                    )
                    
        self.player.play(self.bridge.get_stream_url(track_id))
        self.player.pause = False
        self.prepare_next()
        
        self.refresh_track_cache()
        self.broadcast_state()

    def add_to_play_next(self, track_ids, top=False):
        """Inserts songs directly after the currently playing song."""
        current = QueueItem.get_or_none(QueueItem.is_current == True)
        if not current: return
        
        amt = len(track_ids)
        
        with db.atomic():
            if top:
                insert_pos = current.position + 1
            else:
                last_priority = QueueItem.select().where(
                    (QueueItem.queue_type == 0) & 
                    (QueueItem.position > current.position)
                ).order_by(QueueItem.position.desc()).first()
                
                insert_pos = (last_priority.position + 1) if last_priority else (current.position + 1)
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

    def add_to_queue(self, track_ids):
        """Inserts songs at the end of the user-curated queue (before the Mix)."""
        # Find the last item that ISN'T the infinite mix (type 2)
        last_curated = QueueItem.select().where(QueueItem.queue_type <= 1).order_by(QueueItem.position.desc()).first()
        insert_pos = (last_curated.position + 1) if last_curated else 0
        amt = len(track_ids)

        with db.atomic():
            # Shift the Infinite Mix (if it exists) down to make room
            QueueItem.update(position=QueueItem.position + amt).where(QueueItem.position >= insert_pos).execute()
            
            for i, tid in enumerate(track_ids):
                QueueItem.create(track=tid, queue_type=1, position=insert_pos + i)

        self.queue_dirty = True
                
        self.prepare_next()
        self.broadcast_state()

    def prepare_next(self):
        current = QueueItem.get_or_none(QueueItem.is_current == True)
        if not current: return

        # 1. Ask the "Brain" what is next
        next_track = self._get_next_track(current.position)
        
        # 2. Use the "Muscle" to tell MPV to buffer it
        if next_track:
            self.player.command('loadfile', self.bridge.get_stream_url(next_track.track_id), 'append')

    def _replace_current_track(self, queue_item):
        try:
            self.player.command('playlist-clear')
        except Exception:
            pass

        self.player.play(self.bridge.get_stream_url(queue_item.track_id))

    def advance_queue(self):
        """Called automatically by MPV when a track finishes."""
        current = QueueItem.get_or_none(QueueItem.is_current == True)
        self.last_broadcast_time = 0
        if not current: return
        
        # USE THE NEW HELPER
        next_track = self._get_next_track(current.position)
        
        if next_track:
            with db.atomic():
                current.is_current = False
                current.save()
                next_track.is_current = True
                next_track.save()
            self.queue_dirty = True
        else:
            pass
        
        self.refresh_track_cache()
        self.broadcast_state()

    def skip_prev(self):
        if float(self.time_pos or 0) > 3.0:
            self.seek(0)
            return

        current = QueueItem.get_or_none(QueueItem.is_current == True)
        if not current: return

        # Just get position - 1!
        prev_track = QueueItem.get_or_none(QueueItem.position == current.position - 1)
        if prev_track:
            with db.atomic():
                current.is_current = False
                current.save()
                prev_track.is_current = True
                prev_track.save()
            self.queue_dirty = True
            self._replace_current_track(prev_track)
            self.prepare_next()
            self.broadcast_state()
        else:
            self.seek(0)
            
        self.refresh_track_cache()
        self.broadcast_state()
                
    def skip_next(self):
        current = QueueItem.get_or_none(QueueItem.is_current == True)
        if not current: return
        PlayHistory.create(track=current.track, played_at=datetime.datetime.now() - datetime.timedelta(seconds=float(self.time_pos)), completion_pct=(float(self.time_pos) / float(self.duration)) * 100.0)

        next_track = self._get_next_track(current.position)
        
        if next_track:
            with db.atomic():
                current.is_current = False
                current.save()
                next_track.is_current = True
                next_track.save()
            self.queue_dirty = True
                
            self._replace_current_track(next_track)
            self.prepare_next()
            self.broadcast_state()
        else:
            self.seek(current.track.duration_ms / 1000.0)
            self.toggle_pause()
            
        self.refresh_track_cache()
        self.broadcast_state()
            
    def jump_to_queue_item(self, item_id):
        """Forces playback to jump immediately to a specific queue item."""
        queue_item_id_field = getattr(QueueItem, "id")
        target_item = QueueItem.get_or_none(queue_item_id_field == item_id)
        current = QueueItem.get_or_none(QueueItem.is_current == True)
        
        if not target_item or (current and current.id == target_item.id):
            return

        with db.atomic():
            if current:
                current.is_current = False
                current.save()
            target_item.is_current = True
            target_item.save()

        self.queue_dirty = True

        self._replace_current_track(target_item)
        self.prepare_next()
        
        # Trigger all UI updates (Queue highlight needs to move, Footer needs new data)
        self.refresh_track_cache()
        self.broadcast_state()
        
    def remove_from_queue(self, item_id):
        """Removes a specific item from the queue. If it's the currently playing item, it will skip to the next track."""
        queue_item_id_field = getattr(QueueItem, "id")
        target_item = QueueItem.get_or_none(queue_item_id_field == item_id)
        next_item = None
        if not target_item: return

        is_current = target_item.is_current
        with db.atomic():
            if is_current:
                next_item = self._get_next_track(target_item.position)  # Get the next track before deleting the current one
                if next_item:
                    next_item.is_current = True
                    next_item.save()
                    
            target_item.delete_instance()
            QueueItem.update(position=QueueItem.position - 1).where(QueueItem.position > target_item.position).execute()

        self.queue_dirty = True
            

        if is_current:
            self.refresh_track_cache()
            self.player.play(self.bridge.get_stream_url(next_item.track_id) if next_item else None)
        else:
            self.prepare_next()
            self.refresh_track_cache()
            self.broadcast_state()

    def clear_queue(self, queue_type: int = -1):
        """Removes all items of a specific type (except the playing track)."""
        with db.atomic():
            QueueItem.delete().where(True).execute()
        self.queue_dirty = True
        self.player.stop()
        self.player.pause = True
        self.player.command('playlist-clear')
        self.prepare_next() # Re-evaluate what is coming up next just in case
        self.broadcast_state()

    def reorder_queue(self, item_id: int, target_position: int):
        """Moves an item and shifts the other items to make room."""
        queue_item_id_field = getattr(QueueItem, "id")
        dragged_item = QueueItem.get_or_none(queue_item_id_field == item_id)
        if not dragged_item: return

        old_pos = dragged_item.position
        if old_pos == target_position: return

        with db.atomic():
            if old_pos < target_position:
                # Dragging DOWN: Shift items between old and new position UP by 1
                QueueItem.update(position=QueueItem.position - 1).where(
                    (QueueItem.position > old_pos) & (QueueItem.position <= target_position)
                ).execute()
            else:
                # Dragging UP: Shift items between new and old position DOWN by 1
                QueueItem.update(position=QueueItem.position + 1).where(
                    (QueueItem.position >= target_position) & (QueueItem.position < old_pos)
                ).execute()

            # Set the new position
            dragged_item.position = target_position
            dragged_item.save()

        self.queue_dirty = True

        self.prepare_next() # Re-evaluate next track in case the user dragged an item to "up next"
        self.broadcast_state()

    @property
    def current_track(self):
        current_queue_item = QueueItem.get_or_none(QueueItem.is_current == True)
        return current_queue_item.track if current_queue_item else None

    @property
    def is_paused(self): return self.player.pause
    
    def toggle_pause(self): self.player.pause = not self.player.pause
    
    @property
    def time_pos(self): return self.player.time_pos or 0
    
    @property
    def duration(self): return self.player.duration or 1
    
    def seek(self, seconds: float):
        if self.player.duration: self.player.time_pos = seconds