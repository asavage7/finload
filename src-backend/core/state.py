"""Shared application state.

The singletons live here. Modules must access them as state.provider / state.playback attribute lookups.
"""
from core.database import DatabaseManager, switch_database
from providers import create_provider
from services.audio_analysis import AudioFeatureManager
from services.genre_enrichment import GenreEnrichmentManager
from services.metadata_manager import MetadataManager
from services.playback_manager import PlaybackManager
from services.settings_manager import SettingsManager
from services.sync_manager import SyncManager

settings = SettingsManager()
db = DatabaseManager()
provider = create_provider(settings)
sync = SyncManager(db, settings)
metadata = MetadataManager(settings)
genre_enrichment = GenreEnrichmentManager(settings, db)
audio_features = AudioFeatureManager(settings, db, lambda: provider)

# Background jobs, see routers/jobs.py
jobs = {"sync": sync, "metadata": metadata, "genre_enrichment": genre_enrichment,
        "audio_features": audio_features}

# Syncing automatically triggers the other jobs
sync.follow_up_jobs = [metadata, genre_enrichment, audio_features]

playback: PlaybackManager = None  # type: ignore[assignment]


def init_playback():
    global playback
    if playback is None:
        playback = PlaybackManager(provider, settings)


def switch_source():
    """Swap the provider and database to match the saved library source.

    Only reached from the setup wizard, which is the only place the source can be
    chosen. The app restarts into that wizard (see the settings page's re-run
    action), so this always runs on a freshly started app with nothing playing.
    """
    global db, provider
    playback.stop_for_source_switch()
    db = switch_database(settings.get("library_source"))
    provider = create_provider(settings)
    playback.provider = provider
