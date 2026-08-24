"""Application logging."""
import logging
import sys
from logging.handlers import RotatingFileHandler

from core.config import get_data_dir

LOG_FILENAME = "finload.log"

_MAX_BYTES = 1_000_000 # 1 MB
_BACKUP_COUNT = 3

_configured = False


def log_path():
    return get_data_dir() / LOG_FILENAME


def setup_logging(level=logging.INFO):
    """Attach the file and stream handlers to the root logger."""
    global _configured
    if _configured:
        return
    _configured = True

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    # Don't kill the app if logging to file fails
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
        root.info("Logging to %s", path)
    except OSError as exc:
        root.warning("File logging disabled (%s); logging to stdout only", exc)
