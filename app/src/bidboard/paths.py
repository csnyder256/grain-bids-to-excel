"""Central path registry. Everything the app reads or writes lives under
the shipped folder; nothing touches the wider system."""
from __future__ import annotations

from pathlib import Path

# .../app/src/bidboard/paths.py -> app/src/bidboard -> app/src -> app -> root
PACKAGE_DIR = Path(__file__).resolve().parent
SRC_DIR = PACKAGE_DIR.parent
APP_DIR = SRC_DIR.parent
ROOT_DIR = APP_DIR.parent

DATA_DIR = APP_DIR / "data"
LOGS_DIR = DATA_DIR / "logs"
DB_PATH = DATA_DIR / "bidboard.db"
SETTINGS_PATH = DATA_DIR / "settings.json"
LOCK_PATH = DATA_DIR / "server.lock"
SETUP_MARKER = DATA_DIR / ".setup-complete"

SPREADSHEETS_DIR = ROOT_DIR / "Spreadsheets"
ARCHIVE_DIR = SPREADSHEETS_DIR / "Archive"

BROWSERS_DIR = APP_DIR / "browsers"


def ensure_runtime_dirs() -> None:
    for d in (DATA_DIR, LOGS_DIR, SPREADSHEETS_DIR):
        d.mkdir(parents=True, exist_ok=True)
