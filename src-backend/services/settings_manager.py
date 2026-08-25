import json
import logging
import os
from core.config import get_data_dir

logger = logging.getLogger(__name__)

class SettingsManager:
    def __init__(self):
        # Automatically resolves the correct OS path for user data
        self.data_dir = str(get_data_dir())
        os.makedirs(self.data_dir, exist_ok=True)
        
        self.settings_file = os.path.join(self.data_dir, "settings.json")
        
        # Every key here has a matching control in the settings UI
        # (src/lib/settings-schema.json) or the onboarding flow.
        self.defaults = {
            "onboarding_complete": False,
            "library_source": "jellyfin",  # or "local"
            "jellyfin_url": "",
            "jellyfin_username": "",
            "jellyfin_password": "",
            "jellyfin_library_ids": [],
            "jellyfin_library_ids_pending": None,
            "local_music_path": "",
            "last_synced_at_jellyfin": "",
            "last_synced_at_local": "",
            "use_album_art_for_tracks": True,
            "mpv_buffer_size": "10M",
            "enable_replay_gain": False,
            "replay_gain_mode": "auto",
            "enable_lrclib_lyrics": True,
            "enable_synced_lyrics": True,
            "enable_online_metadata": True,
            "theaudiodb_api_key": "123",
            "enable_genre_enrichment": True,
            "lastfm_api_key": "",
            "enable_radio": True,
            "analysis_worker_count": 4,
            "analysis_worker_usage": 25,
            "autoplay_default": False,
            "autoplay_queue_length": 3,
            "enable_transcoding": False,
            "transcode_format": "mp3",
            "transcode_bitrate": "192000",
        }
        self.settings = self._load()
        self._listeners = []

    def _load(self):
        """Loads settings from disk, filling missing keys with defaults."""
        if not os.path.exists(self.settings_file):
            self._save(self.defaults)
            return self.defaults.copy()
            
        with open(self.settings_file, "r") as f:
            try:
                user_settings = json.load(f)
                # Merge defaults with user settings to catch newly added options.
                merged = {**self.defaults,
                          **{k: v for k, v in user_settings.items() if k in self.defaults}}
                # Existing installs upgrading to this version won't have
                # onboarding_complete on disk yet. Don't show the wizard to
                # someone who's already configured a library source.
                if "onboarding_complete" not in user_settings and (
                    user_settings.get("jellyfin_url") or user_settings.get("local_music_path")
                ):
                    merged["onboarding_complete"] = True
                return merged
            except json.JSONDecodeError:
                return self.defaults.copy()

    def _save(self, data):
        """Writes the current settings to disk."""
        with open(self.settings_file, "w") as f:
            json.dump(data, f, indent=4)

    def add_listener(self, callback):
        """Register a callback(key, value) invoked whenever a setting changes."""
        self._listeners.append(callback)

    def get(self, key):
        return self.settings.get(key, self.defaults.get(key))

    def set(self, updates: dict):
        """Apply one or more settings with a single write to disk.

        Unknown keys are ignored so arbitrary PATCH bodies can't grow the file.
        """
        applied = {k: v for k, v in updates.items() if k in self.defaults}
        if not applied:
            return
        # Only keys whose value actually moved get announced.
        changed = {k: v for k, v in applied.items() if self.get(k) != v}
        self.settings.update(applied)
        self._save(self.settings)
        for key, value in changed.items():
            for cb in self._listeners:
                try:
                    cb(key, value)
                except Exception as e:
                    logger.exception("Settings listener error: %s", e)