import json
import os
import sys
from pathlib import Path

from platformdirs import user_data_dir
from dotenv import load_dotenv

# Single source of truth for how the app identifies itself, both to external
# services (User-Agent) and to media servers (client name/version).
APP_NAME = "Finload"
APP_VERSION = "0.1.0"
USER_AGENT = f"{APP_NAME.lower()}/{APP_VERSION}"


def _find_env_file() -> Path | None:
    candidates = []

    if getattr(sys, "frozen", False):
        # PyInstaller bundle: look next to the executable, then in user data dir.
        candidates.append(Path(sys.executable).parent / ".env")
        candidates.append(Path(user_data_dir("finload")) / ".env")
    else:
        # Dev: look two levels up from this file (i.e. finload-new/.env).
        candidates.append(Path(__file__).resolve().parents[1] / ".env")

    return next((p for p in candidates if p.exists()), None)


try:
    _env_path = _find_env_file()
    if _env_path:
        load_dotenv(dotenv_path=str(_env_path))
except Exception:
    # Don't fail import; environment variables may be provided by the process.
    pass


def _split_csv(value: str | None, default: list[str]) -> list[str]:
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


def get_backend_host() -> str:
    return os.getenv("BACKEND_HOST", "127.0.0.1")


def get_backend_port() -> int:
    return int(os.getenv("BACKEND_PORT", "8000"))


def get_cors_origins() -> list[str]:
    return _split_csv(
        os.getenv("CORS_ORIGINS"),
        ["http://localhost:1420", "http://localhost:5173", "tauri://localhost", "https://tauri.localhost"],
    )


def get_data_dir() -> Path:
    override = (
        os.getenv("DATA_DIR", "").strip()
        or os.getenv("FINLOAD_DATA_DIR", "").strip()
        or os.getenv("DATABASE_PATH", "").strip()
    )

    if override:
        path = Path(override).expanduser()
        if path.suffix.lower() == ".db":
            return path.parent
        return path

    return Path(user_data_dir("finload"))


def get_library_source() -> str:
    """The user's chosen library source, read straight from settings.json.

    Read from disk (rather than via SettingsManager) so ``get_database_path``
    can resolve the per-source DB file at import time, before any managers exist.
    """
    try:
        with open(get_data_dir() / "settings.json", "r") as fh:
            data = json.load(fh)
        source = (data.get("library_source") or "jellyfin").strip().lower()
        return source or "jellyfin"
    except Exception:
        return "jellyfin"


def get_database_path(source: str | None = None) -> Path:
    """Path to the SQLite file for a library source.

    Each source gets its own database so switching between, say, Jellyfin and a
    local library doesn't wipe and re-sync the other one. An explicit
    ``DATABASE_PATH`` override still pins a single file (handy for dev/tests).
    """
    data_dir = get_data_dir()
    override = os.getenv("DATABASE_PATH", "").strip()

    if override:
        path = Path(override).expanduser()
        if path.suffix.lower() != ".db":
            path = path / "jelly_local.db"
    else:
        if source is None:
            source = get_library_source()
        # Keep the historical filename for Jellyfin so existing installs keep
        # their library; give every other source its own file.
        filename = "jelly_local.db" if source == "jellyfin" else f"library_{source}.db"
        path = data_dir / filename

    path.parent.mkdir(parents=True, exist_ok=True)
    return path
