"""Media-source providers.

Use ``create_provider(settings)`` to obtain the provider for the user's chosen
``library_source``. The rest of the app depends only on the ``MediaProvider``
interface, so adding a source means adding a module here and a branch below.
"""
from .base import MediaProvider
from .jellyfin import JellyfinProvider
from .local import LocalProvider

__all__ = ["MediaProvider", "JellyfinProvider", "LocalProvider", "create_provider"]


def create_provider(settings) -> MediaProvider:
    source = (settings.get("library_source") or "jellyfin").lower()

    if source == "local":
        return LocalProvider(settings)
    if source == "jellyfin":
        return JellyfinProvider(settings)

    # Unknown source — fall back to Jellyfin so the app keeps working.
    return JellyfinProvider(settings)
