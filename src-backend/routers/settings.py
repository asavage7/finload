"""Settings routes and the Jellyfin connection test."""
from fastapi import APIRouter, Body

from core import state
from providers.jellyfin import test_connection as test_jellyfin_connection_impl

router = APIRouter()


@router.get("/api/settings")
def get_settings():
    return state.settings.settings


@router.patch("/api/settings")
def update_settings(data: dict = Body(...)):
    was_onboarded = state.settings.get("onboarding_complete")
    state.settings.set(data)

    # The source is only chosen in the setup wizard, and the app restarts into
    # that wizard, so this always applies to a freshly started app. There is
    # deliberately no hot-swap path for a source change made mid-session.
    # Otherwise, reconfigure the active provider live for its own settings.
    if "library_source" in data and not was_onboarded:
        state.switch_source()
    elif any(key in data for key in state.provider.SETTINGS_KEYS):
        state.provider.configure(state.settings)

    return state.settings.settings


@router.post("/api/jellyfin/test")
def test_jellyfin_connection(data: dict = Body(...)):
    url = (data.get("jellyfin_url") or "").strip()
    username = (data.get("jellyfin_username") or "").strip()
    password = (data.get("jellyfin_password") or "").strip()
    if not (url and username and password):
        return {"ok": False, "message": "All three fields are required"}
    return test_jellyfin_connection_impl(url, username, password)


@router.get("/api/jellyfin/libraries")
def get_jellyfin_libraries():
    return state.provider.fetch_libraries()


@router.post("/api/jellyfin/libraries/select")
def select_jellyfin_libraries(data: dict = Body(...)):
    library_ids = data.get("library_ids") or []
    # Staged as "pending", not applied immediately: browsing keeps showing
    # the current selection's results until the forced resync below actually
    # backfills library_id for the new one and SyncManager promotes it to
    # applied on success -- otherwise every already-known track would look
    # filtered-out (empty albums) for the entire backfill window. See
    # settings_manager.py's jellyfin_library_ids_pending default.
    state.settings.set({"jellyfin_library_ids_pending": library_ids})

    started = state.jobs["sync"].start(state.provider, force=True)
    return {"ok": True, "resync_started": started}
