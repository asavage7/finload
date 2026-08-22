"""Shared application state.

The singletons every router needs (settings, the active provider, playback,
sync, metadata) live here. Modules must access them as ``state.provider`` /
``state.playback`` attribute lookups rather than ``from state import provider``,
because switching the library source replaces some of them at runtime.
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
# `lambda: provider` closes over this module's global name, not its value at
# construction time, so it still sees the current provider after
# switch_source() reassigns it.
audio_features = AudioFeatureManager(settings, db, lambda: provider)

# Background jobs, keyed by name — routers/jobs.py looks jobs up generically
# by this name instead of one hardcoded attribute per job.
jobs = {"sync": sync, "metadata": metadata, "genre_enrichment": genre_enrichment,
        "audio_features": audio_features}

# A completed sync kicks off follow-up enrichment for anything left un-enriched.
sync.follow_up_jobs = [metadata, genre_enrichment, audio_features]

# PlaybackManager creates the mpv core (and opens an audio output). Defer it to
# app startup so it's built only in the worker that actually serves requests,
# not in uvicorn's --reload supervisor, which imports this module but never runs
# startup events. That avoids a second, idle mpv instance during development.
playback: PlaybackManager = None  # type: ignore[assignment]


def init_playback():
    global playback
    if playback is None:
        playback = PlaybackManager(provider, settings)


def switch_source():
    """Swap the provider and database to match the saved library source.

    Each source has its own database so the two libraries stay independent.
    Playback is stopped first since the current queue/track lives in the
    database being swapped out.
    """
    global db, provider
    playback.stop_for_source_switch()
    db = switch_database(settings.get("library_source"))
    provider = create_provider(settings)
    playback.provider = provider
