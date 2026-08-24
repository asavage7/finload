"""Media Source providers."""
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

    # Fallback to jellyfin
    return JellyfinProvider(settings)
