"""Application logging.

An installed copy is launched from a desktop entry, so the sidecar's stdout goes
somewhere the user can't reach; a bug report can then only ever say "it broke".
Records go to both a file and stdout, so `npm run dev:backend` still prints to
the terminal while an installed copy leaves something on disk to attach to an
issue.

The file lives beside the database in the user data dir, which is already the
one directory the app is guaranteed to be able to write to.
"""
import logging
import sys
from logging.handlers import RotatingFileHandler

from config import get_data_dir

LOG_FILENAME = "finload.log"

# Small enough that a user can attach one to an issue, with a couple of previous
# runs kept so a crash isn't immediately rotated away by the restart after it.
_MAX_BYTES = 1_000_000
_BACKUP_COUNT = 3

_configured = False


def log_path():
    return get_data_dir() / LOG_FILENAME


def setup_logging(level=logging.INFO):
    """Attach the file and stream handlers to the root logger. Idempotent."""
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

    # A read-only or missing data dir must not stop the app from starting, so a
    # failure here leaves stdout logging in place rather than raising.
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
