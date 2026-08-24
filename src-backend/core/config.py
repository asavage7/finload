import json
import os
import sys
from pathlib import Path

from platformdirs import user_data_dir
from dotenv import load_dotenv

# User agent identifiers. Version is automatically set from scripts/set-version.mjs
APP_NAME = "Finload"
APP_VERSION = "0.1.2"
USER_AGENT = f"{APP_NAME.lower()}/{APP_VERSION}"


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
    """Gets the user's chosen library source from onboarding/settings."""
    try:
        with open(get_data_dir() / "settings.json", "r") as fh:
            data = json.load(fh)
        source = (data.get("library_source") or "jellyfin").strip().lower()
        return source or "jellyfin"
    except Exception:
        return "jellyfin"


def get_database_path(source: str | None = None) -> Path:
    """Gets the database path based on the chosen library source."""
    data_dir = get_data_dir()
    override = os.getenv("DATABASE_PATH", "").strip()
    
    if source is None:
        source = get_library_source()
    
    def get_filename(source: str | None) -> str:
        return f"library_{source}.db"

    if override:
        path = Path(override).expanduser()
        if path.suffix.lower() != ".db":
            path = path / get_filename(source)
    else:
        filename = get_filename(source)
        path = data_dir / filename

    path.parent.mkdir(parents=True, exist_ok=True)
    return path
