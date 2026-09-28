"""Make `src/` importable for `python -m unittest` too (pytest reads pyproject).

Item prices come from the copy bundled with the app, so tests never touch the
user's cache or the network."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("DDRAGON_CACHE",
                      str(ROOT / "src" / "wincast" / "resources" / "ddragon"))
