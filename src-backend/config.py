import os
from pathlib import Path

from platformdirs import user_data_dir
from dotenv import load_dotenv


# Attempt to load a .env file from the project root (finload-new/.env)
# so running `uvicorn main:app --app-dir src-backend` still picks up values.
try:
    _project_root = Path(__file__).resolve().parents[1]
    _env_path = _project_root / ".env"
    if _env_path.exists():
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
        ["http://localhost:1420", "http://localhost:5173"],
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


def get_jellyfin_config() -> tuple[str, str, str]:
    server_url = os.getenv("JELLYFIN_URL", "").strip()
    api_key = os.getenv("JELLYFIN_API_KEY", "").strip()
    user_id = os.getenv("JELLYFIN_USER_ID", "").strip()

    missing = [
        name
        for name, value in [
            ("JELLYFIN_URL", server_url),
            ("JELLYFIN_API_KEY", api_key),
            ("JELLYFIN_USER_ID", user_id),
        ]
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing required environment variables: " + ", ".join(missing)
        )

    return server_url, api_key, user_id


def get_database_path() -> Path:
    data_dir = get_data_dir()
    override = os.getenv("DATABASE_PATH", "").strip()

    if override:
        path = Path(override).expanduser()
        if path.suffix.lower() != ".db":
            path = path / "jelly_local.db"
    else:
        path = data_dir / "jelly_local.db"

    path.parent.mkdir(parents=True, exist_ok=True)
    return path
