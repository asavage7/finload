import json
import os
from config import get_data_dir

class SettingsManager:
    def __init__(self):
        # Automatically resolves the correct OS path for user data
        self.data_dir = str(get_data_dir())
        os.makedirs(self.data_dir, exist_ok=True)
        
        self.settings_file = os.path.join(self.data_dir, "settings.json")
        
        # Define the default state
        self.defaults = {
            "theme": "dark",
            "accent_color": "adaptive",
            "library_source": "jellyfin",  # or "local"
            "jellyfin_url": "",
            "jellyfin_token": "",
            "local_music_path": "",
            "enable_music_videos": False,
            "enable_lrclib_lyrics": True,
            "enable_discovery": True,
            "enable_online_discovery": True,
            "enable_telemetry": True,
            "mpv_buffer_size": "150M"
        }
        self.settings = self._load()

    def _load(self):
        """Loads settings from disk, filling missing keys with defaults."""
        if not os.path.exists(self.settings_file):
            self._save(self.defaults)
            return self.defaults.copy()
            
        with open(self.settings_file, "r") as f:
            try:
                user_settings = json.load(f)
                # Merge defaults with user settings to catch newly added options
                merged = {**self.defaults, **user_settings}
                return merged
            except json.JSONDecodeError:
                return self.defaults.copy()

    def _save(self, data):
        """Writes the current settings to disk."""
        with open(self.settings_file, "w") as f:
            json.dump(data, f, indent=4)

    def get(self, key):
        return self.settings.get(key, self.defaults.get(key))

    def set(self, key, value):
        self.settings[key] = value
        self._save(self.settings)