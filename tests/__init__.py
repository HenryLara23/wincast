"""Make `src/` importable for `python -m unittest` too (pytest reads pyproject).

Tests score with a FROZEN model and item prices (tests/fixtures/golden_model.json,
tests/fixtures/ddragon/) rather than whatever model the app ships, so the release
build can swap the built-in model without breaking the recorded numbers. They
never touch the user's cache or the network."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
FIXTURES = ROOT / "tests" / "fixtures"
GOLDEN_MODEL = FIXTURES / "golden_model.json"
os.environ.setdefault("DDRAGON_CACHE", str(FIXTURES / "ddragon"))
