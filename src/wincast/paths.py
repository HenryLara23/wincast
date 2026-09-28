"""Where Wincast keeps things on the user's machine."""

import os
import shutil
import sys
from pathlib import Path

from . import APP_NAME

RESOURCES = Path(__file__).resolve().parent / "resources"
FALLBACK_MODEL = RESOURCES / "models" / "fallback.json"


def user_data_dir() -> Path:
    """%LOCALAPPDATA%\\Wincast on Windows, ~/.local/share/wincast elsewhere."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / APP_NAME
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_NAME.lower()


def models_dir() -> Path:
    return user_data_dir() / "models"


def setup_ddragon_cache() -> Path:
    """Point the vendored item-price cache at the user folder, seeded from the
    prices bundled with the app so a fresh install scores offline.

    Must run before `lolwp.store.items_db` is imported: it reads DDRAGON_CACHE
    once, at import time."""
    cache = Path(os.environ.get("DDRAGON_CACHE") or user_data_dir() / "ddragon")
    cache.mkdir(parents=True, exist_ok=True)
    for f in (RESOURCES / "ddragon").glob("*.json"):
        if not (cache / f.name).exists():
            shutil.copy2(f, cache / f.name)
    os.environ["DDRAGON_CACHE"] = str(cache)
    return cache
