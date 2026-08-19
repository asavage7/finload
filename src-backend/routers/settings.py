"""Settings routes and the Jellyfin connection test."""
from fastapi import APIRouter, Body

import state
from providers.jellyfin import test_connection as test_jellyfin_connection_impl

router = APIRouter()


@router.get("/api/settings")
def get_settings():
    return state.settings.settings


@router.patch("/api/settings")
def update_settings(data: dict = Body(...)):
    state.settings.set(data)

    # Switching library source swaps in a different provider and its own
    # database. Otherwise, if any of the active provider's own settings
    # changed, reconfigure it live so the user doesn't have to restart.
    if "library_source" in data:
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
