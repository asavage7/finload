import mpv
from core.database import Track, Album, Artist, QueueItem, PlaybackState, PlayHistory, db, _chunks
import datetime
import random
import threading
import traceback

from services import radio


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
        self._queue_ended = False  # guards _handle_queue_end against double-firing
        # False until mpv actually loads a file. Queue-end handling checks this
        # because the idle-active observer also fires at startup, before any
        # playback has happened.
        self._file_loaded = False
        self.shuffle_original_positions: dict = {}
        # The open PlayHistory row for whatever's currently playing — created
        # the instant a track starts (see _start_history) so it shows up in
        # history immediately, then corrected in place (see _finalize_history)
        # once we know how much of it actually got listened to.
        self._history_entry = None
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
            "radio_enabled": False,
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
                if old == 0:
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
        self._cleanup_stale_history()
        self._restore_state()
        self._apply_replaygain()

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

    def _persist_state(self, **fields):
        """Write fields onto the PlaybackState singleton row."""
        rows = PlaybackState.update(**fields).where(PlaybackState.id == 1).execute()
        if rows == 0:
            PlaybackState.create(id=1, **fields)

    def _set_current(self, queue_item, completed: bool = False):
        """Update PlaybackState to point to queue_item (may be None). Every
        mix (queue_type=2) item at or before this position becomes "real"
        (queue_type=1): the mix ahead is volatile/replaceable, but anything
        the user has actually reached — including tracks jumped straight
        past, not just the one landed on — shouldn't be silently
        regenerated or evicted out from under them. A bulk update rather
        than promoting just queue_item itself, since jump_to_queue_item can
        skip several mix tracks in one move without each individually
        becoming current.

        This is also the single choke point for every real track transition
        (play_now, advance_queue, skip_prev/next, jump_to_queue_item,
        remove_from_queue, queue-end wraparound), so it doubles as the hook
        for play history: whatever was open gets finalized (completed=True
        for a natural end-of-track, False for a manual skip/jump/removal)
        before a fresh entry opens for queue_item. Must run before the mpv
        file itself is swapped, so self.time_pos/self.duration still refer
        to the outgoing track when completed=False."""
        self._finalize_history(completed=completed)
        if queue_item is not None:
            QueueItem.update(queue_type=1).where(
                (QueueItem.queue_type == 2) & (QueueItem.position <= queue_item.position)
            ).execute()
        self._persist_state(current_queue_item=queue_item)
        self._start_history(queue_item)

    def _cleanup_stale_history(self):
        """A row still marked in_progress=True at startup means the app was
        closed (or crashed) mid-track last session, before _finalize_history
        ever ran. There's no reliable way to recover how much actually played
        -- PlaybackState doesn't persist a live seek position, and a restart
        always resumes a track from 0 anyway (see _restore_state) -- so
        rather than guess and risk feeding a fabricated 0%-completion "skip"
        into radio.py/discovery.py's fatigue and feedback scoring, the
        unconfirmed row is discarded outright."""
        PlayHistory.delete().where(PlayHistory.in_progress == True).execute()

    def _restore_state(self):
        """Re-apply persisted playback state (current track, shuffle, repeat)
        after a restart. The track is shown paused at the start; the file itself
        is loaded lazily on the first unpause or seek, so a restart never
        touches the network or audio device on its own."""
        state = PlaybackState.get_or_none(PlaybackState.id == 1)
        if state is None:
            return

        self.repeat_mode = max(0, min(2, state.repeat_mode or 0))
        self.shuffle = bool(state.shuffle)
        self.current_state["repeat_mode"] = self.repeat_mode
        self.current_state["shuffle"] = self.shuffle
        self.current_state["radio_enabled"] = state.radio_seed_track_id is not None
        self.player['loop-file'] = 'inf' if self.repeat_mode == 2 else 'no'
        self.player.pause = True

    def _load_current(self, start: float = 0.0) -> bool:
        """Load the current queue item into mpv, optionally at an offset."""
        current = self._get_current()
        if not current:
            return False
        try:
            url = self.provider.get_stream_url(current.track_id)
        except Exception:
            return False
        self._file_loaded = True
        self._queue_ended = False
        # Restored-but-not-yet-loaded track (see _restore_state): no history
        # entry was opened for it yet since nothing had actually played this
        # session, so open one now that it's really starting.
        if self._history_entry is None:
            self._start_history(current)
        if start > 0:
            self.player.loadfile(url, start=str(start))
        else:
            self.player.loadfile(url)
        self.prepare_next()
        return True

    # -----------------------------------------------------------------------
    # Settings
    # -----------------------------------------------------------------------

    def _on_setting_changed(self, key, value):
        if key == 'mpv_buffer_size':
            self.player['demuxer-max-bytes'] = value
        elif key in ('enable_replay_gain', 'replay_gain_mode'):
            self._apply_replaygain()

    def _apply_replaygain(self):
        """Wire the Replay Gain settings into mpv's ``replaygain`` filter.

        mpv reads the gain when a file's audio chain is initialised, so a change
        takes effect on the next track (including one already preloaded via
        ``prepare_next``); the currently-playing file keeps its gain until it's
        reloaded, which we don't force mid-track to avoid an audible jump."""
        if not self.settings.get('enable_replay_gain'):
            self.player['replaygain'] = 'no'
            return
        mode = self.settings.get('replay_gain_mode') or 'auto'
        if mode == 'auto':
            # mpv has no "auto" mode; derive one from context — album gain keeps
            # a record's own relative levels intact, but when shuffling across
            # releases per-track gain is the sane choice for even loudness.
            mode = 'track' if self.shuffle else 'album'
        elif mode not in ('track', 'album'):
            mode = 'album'
        # Attenuate rather than clip when a track's gain would push it over 0 dB.
        self.player['replaygain-clip'] = True
        self.player['replaygain'] = mode

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
                "artist_id": str(item.track.artist.id) if item.track and item.track.artist else None,
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

    def _get_next_track(self, current_position):
        return QueueItem.select().where(
            QueueItem.position > current_position
        ).order_by(QueueItem.position.asc()).first()

    def _get_prev_track(self, current_position):
        return QueueItem.select().where(
            QueueItem.position < current_position
        ).order_by(QueueItem.position.desc()).first()

    def _start_history(self, queue_item):
        """Opens a PlayHistory row the instant queue_item becomes current, so
        a track shows up in history as soon as it starts playing rather than
        only once something else replaces it. completion_pct starts at 0 and
        is corrected retroactively by _finalize_history; in_progress marks it
        as not-yet-final so recommendation code (radio.py/discovery.py) skips
        it instead of reading the placeholder 0% as a real skip."""
        if queue_item is None:
            self._history_entry = None
            return
        try:
            self._history_entry = PlayHistory.create(
                track=queue_item.track,
                played_at=datetime.datetime.now(),
                completion_pct=0.0,
                in_progress=True,
            )
        except Exception:
            self._history_entry = None

    def _finalize_history(self, completed: bool):
        """Closes out the open PlayHistory entry (if any) with how much of
        the track actually played, and scrobbles it to the source if enough
        of it played. Must be called while self.time_pos/self.duration still
        refer to the outgoing track (i.e. before the mpv file is swapped)."""
        entry = self._history_entry
        self._history_entry = None
        if entry is None:
            return
        try:
            track = entry.track
            duration_s = (track.duration_ms or 0) / 1000.0
            if completed:
                elapsed = duration_s
                completion = 100.0
            else:
                elapsed = float(self.time_pos)
                completion = (elapsed / float(self.duration)) * 100.0 if self.duration else 0.0
                completion = max(0.0, min(100.0, completion))
            entry.completion_pct = completion
            entry.in_progress = False
            entry.save()

            if completed or elapsed >= duration_s / 2 or elapsed >= 90:
                self._report_play_async(track.id)
        except Exception:
            pass

    def _report_play_async(self, track_id):
        """Scrobble off the playback thread — _finalize_history runs on mpv's
        observer thread, where a blocking network POST would stall state handling."""
        provider = self.provider

        def run():
            try:
                provider.report_play(track_id)
            except Exception:
                pass

        threading.Thread(target=run, daemon=True).start()

    # -----------------------------------------------------------------------
    # Playback control
    # -----------------------------------------------------------------------

    def play_now(self, track_id, context_ids=None):
        """Clears the queue, loads the context, and starts playing track_id.

        Always ends any active radio session (see start_radio) — a plain
        "play this" replaces the queue outright, so a stale mix shouldn't
        keep topping itself up underneath the new context. start_radio calls
        this first and then sets its own seed immediately after.
        """
        self._intentional_stop = False
        self._queue_ended = False
        self._persist_state(radio_seed_track=None)
        self.current_state["radio_enabled"] = False
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

        self._file_loaded = True
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

    # -----------------------------------------------------------------------
    # Radio (auto-generated "mix" queue tracks — see radio.py/discovery.py)
    # -----------------------------------------------------------------------

    def start_radio(self, seed_track_id: str):
        """Replaces the queue with seed_track_id playing now, then generates
        the first mix batch after it. For "Start Radio" from a track context
        menu, where playing the clicked track itself is the whole point —
        see start_radio_from_reference for album/artist radio, and
        set_radio_enabled for turning radio mode on/off on top of whatever's
        already queued.

        play_now already starts audio immediately; the mix batch is
        generated *after* that, off-thread, so starting a radio never waits
        on discovery.build_queue scanning the library before sound comes
        out — it should feel as instant as any other "play this" action.
        """
        self.play_now(seed_track_id, context_ids=[seed_track_id])
        self._persist_state(radio_seed_track=seed_track_id)
        self.current_state["radio_enabled"] = True
        self._top_up_mix_async()

    def start_radio_from_reference(self, reference_track_id: str, extra_seed_ids: list[str] | None = None):
        """Album/artist/playlist radio: reference_track_id and extra_seed_ids
        (a handful of tracks off the album/artist/playlist -- see
        radio.pick_seed_tracks) only seed the recommendation engine and are
        never themselves played or queued, matching Finamp's own album/artist
        instant-mix behavior (it doesn't play a representative track either,
        it just starts the mix). Generates the first batch synchronously --
        discovery.build_queue's cost comes from scanning candidates, not
        from how many get selected,
        so asking for the whole small batch up front is no slower than
        asking for one track -- plays its first entry immediately, and
        queues the rest.
        """
        track_ids = radio.generate_batch(reference_track_id, [], set(), extra_seed_ids=extra_seed_ids,
                                          library_ids=self.settings.get("jellyfin_library_ids"))
        if not track_ids:
            # No usable recommendations (e.g. no cached audio features yet)
            # -- fall back to just playing the reference track directly.
            self.start_radio(reference_track_id)
            return

        first_id, rest_ids = track_ids[0], track_ids[1:]
        self.play_now(first_id, context_ids=[first_id])
        self._persist_state(radio_seed_track=reference_track_id)
        self.current_state["radio_enabled"] = True

        if rest_ids:
            with db.atomic():
                for i, tid in enumerate(rest_ids):
                    QueueItem.create(track=tid, queue_type=2, position=1.0 + i)
            self.queue_dirty = True
            self.prepare_next()
            self.broadcast_state()

    def set_radio_enabled(self, enabled: bool):
        """The queue panel's infinite-queue toggle: turns radio mode on/off
        for the *current* queue without clearing the user's own curated
        queue (unlike start_radio). Enabling seeds from whatever's currently
        playing and generates a fresh batch immediately; disabling drops any
        not-yet-played mix tracks (queue_type=2) and stops future top-ups —
        so toggling off then back on doubles as a "re-roll" of the mix
        without touching anything the user manually queued."""
        if not enabled:
            current = self._get_current()
            with db.atomic():
                mix_query = QueueItem.delete().where(QueueItem.queue_type == 2)
                if current:
                    mix_query = mix_query.where(QueueItem.position > current.position)
                mix_query.execute()
            self._persist_state(radio_seed_track=None)
            self.current_state["radio_enabled"] = False
            self.queue_dirty = True
            # Same "queue changed near the front, refresh the native mpv
            # lookahead" pattern used by add_to_play_next/set_shuffle/
            # set_repeat — a deleted mix track may have been the preloaded
            # next file.
            try:
                self.player.command('playlist-clear')
            except Exception:
                pass
            self.prepare_next()
            self.broadcast_state()
            return

        current = self._get_current()
        if not current:
            return
        self._persist_state(radio_seed_track=current.track_id)
        self.current_state["radio_enabled"] = True
        # An explicit re-enable is the user's "re-roll" gesture: widen the
        # first pick so the fresh mix doesn't open on the same track the
        # deleted one did (see discovery.build_queue's reroll param).
        self._top_up_mix(reroll=True)
        self.broadcast_state()

    def _top_up_mix(self, reroll: bool = False):
        """Generates and appends the next mix batch if a radio session is
        active and running low. Synchronous — callers on mpv's observer
        thread should use _top_up_mix_async instead."""
        state_row = PlaybackState.get_or_none(PlaybackState.id == 1)
        seed_id = state_row.radio_seed_track_id if state_row else None
        if not seed_id:
            return
        current = self._get_current()
        if not current:
            return

        remaining_mix = QueueItem.select().where(
            (QueueItem.queue_type == 2) & (QueueItem.position > current.position)
        ).count()
        if remaining_mix >= radio.MIX_LOW_WATER_MARK:
            return

        exclude_ids = {q.track_id for q in QueueItem.select(QueueItem.track)}
        context, feedback, elapsed_ms, manual_ids = radio.session_context(current.position)
        try:
            new_ids = radio.generate_batch(seed_id, context, exclude_ids,
                                            feedback=feedback, manual_ids=manual_ids,
                                            elapsed_ms=elapsed_ms, reroll=reroll,
                                            library_ids=self.settings.get("jellyfin_library_ids"))
        except Exception:
            traceback.print_exc()  # a dead radio should at least say why it died
            return
        if not new_ids:
            return

        last_item = QueueItem.select().order_by(QueueItem.position.desc()).first()
        start_pos = (last_item.position + 1.0) if last_item else 0.0
        with db.atomic():
            for i, tid in enumerate(new_ids):
                QueueItem.create(track=tid, queue_type=2, position=start_pos + i)

        self.queue_dirty = True
        self.prepare_next()
        self.broadcast_state()

    def _top_up_mix_async(self):
        """Runs _top_up_mix off mpv's observer thread — discovery.build_queue
        scans the whole library's cached features, too slow to run inline on
        the thread that's also driving audio playback events. broadcast_state
        is already lock-protected against concurrent callers (see its
        docstring), so a background thread here is safe the same way
        _report_play_async's is."""
        threading.Thread(target=self._top_up_mix, daemon=True).start()

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
        # prepare_next() runs more than once against the same playing file --
        # queue edits (add_to_queue, remove_from_queue, reorder_queue) and mix
        # top-ups (_top_up_mix) all call it without first clearing mpv's
        # playlist. Without trimming, a stale lookahead entry from an earlier
        # call sticks around alongside the freshly appended one, and mpv
        # gapless-advances into that stale duplicate instead of the track we
        # just computed -- desyncing the DB's "current" pointer (which moves
        # to the real next track) from what's actually playing.
        try:
            playing_pos = self.player.playlist_pos
            if playing_pos is None or playing_pos < 0:
                playing_pos = 0
            while self.player.playlist_count > playing_pos + 1:
                self.player.command('playlist-remove', self.player.playlist_count - 1)
        except Exception:
            pass
        if next_track:
            self.player.command('loadfile', self.provider.get_stream_url(next_track.track_id), 'append')

    def _replace_current_track(self, queue_item):
        try:
            self.player.command('playlist-clear')
        except Exception:
            pass
        self._file_loaded = True
        self._queue_ended = False
        self.player.play(self.provider.get_stream_url(queue_item.track_id))

    def _handle_queue_end(self):
        """Called when mpv's playlist is exhausted (last track finished naturally).
        Resets the current position to the first track at time 0 so the user can replay."""
        if not self._file_loaded:
            # mpv is idle because nothing has been loaded yet (startup), not
            # because a queue finished.
            return
        if self._intentional_stop:
            self._intentional_stop = False
            return
        # Both the playlist-pos=-1 and idle-active observers can fire for a single
        # exhaustion; guard so the just-finished track isn't recorded/scrobbled
        # twice (and the re-entry doesn't mis-record the reset-to-first track).
        if self._queue_ended:
            return
        current = self._get_current()
        if not current:
            return
        if not QueueItem.select().exists():
            return
        self._queue_ended = True
        first_track = QueueItem.select().order_by(QueueItem.position.asc()).first()
        if first_track:
            self._set_current(first_track, completed=True)
            self.queue_dirty = True
        else:
            self._finalize_history(completed=True)
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
            self._set_current(next_track, completed=True)
            self.queue_dirty = True
        else:
            self._finalize_history(completed=True)

        self.refresh_track_cache()
        self.broadcast_state()
        self._top_up_mix_async()

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

        next_track = self._get_next_track(current.position)
        if next_track:
            self._set_current(next_track)
            self.queue_dirty = True
            self._replace_current_track(next_track)
            self.prepare_next()
            self.refresh_track_cache()
            self.broadcast_state()
            self._top_up_mix_async()
        else:
            self._finalize_history(completed=False)
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
        self._top_up_mix_async()

    def remove_from_queue(self, item_id):
        """Removes a specific item from the queue. Skips to next if it was current."""
        target_item = QueueItem.get_or_none(QueueItem.id == item_id)
        if not target_item:
            return

        current = self._get_current()
        is_current = current is not None and current.id == target_item.id

        # Dismissing a not-yet-played mix suggestion is a soft "not this"
        # signal -- reuses the existing skip-fatigue mechanism (a play with a
        # low completion_pct) so future top-ups lean away from it, without
        # needing a distinct penalty tier. Never true for the current item:
        # by the time something is playing it's already been promoted to
        # queue_type=1 in _set_current, so this only ever fires for tracks
        # the user removed before ever hearing them.
        if target_item.queue_type == 2:
            PlayHistory.create(track=target_item.track, completion_pct=0.0, visible=False)

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
            if next_item:
                self._file_loaded = True
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
        self._finalize_history(completed=False)
        self._persist_state(radio_seed_track=None)
        self.current_state["radio_enabled"] = False
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
        self._finalize_history(completed=False)
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
        self._persist_state(shuffle=enabled)
        # 'auto' Replay Gain picks album vs track by shuffle state — refresh it.
        self._apply_replaygain()
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
        self._persist_state(repeat_mode=self.repeat_mode)
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

    def toggle_pause(self):
        # After a restart nothing is loaded yet; the first unpause loads the
        # restored track from the beginning.
        if not self._file_loaded and self.player.pause:
            if self._load_current():
                self.player.pause = False
            return
        self.player.pause = not self.player.pause

    @property
    def time_pos(self): return self.player.time_pos or 0

    @property
    def duration(self):
        return self.current_state.get("duration") or self.player.duration or 1

    def seek(self, seconds: float):
        if not self._file_loaded:
            # Restored track that hasn't been loaded yet: load it at the target
            # position, still paused.
            if self._load_current(start=max(0.0, float(seconds))):
                self.current_state["time_pos"] = float(seconds)
                self.last_broadcast_time = float(seconds)
                self.broadcast_state()
            return
        if self.player.duration:
            self.player.time_pos = seconds

    def set_volume(self, volume: int):
        self.player.volume = max(0, min(100, int(volume)))
