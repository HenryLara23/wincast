"""Which model and item prices a new game gets.

For now: the model bundled with the app (or one given on the command line),
with item prices for its patch. Phase 8 replaces this with a pick from the
user's model folder by live patch. Loaded once and reused across games."""

from __future__ import annotations

from .paths import FALLBACK_MODEL, setup_ddragon_cache

setup_ddragon_cache()                     # before lolwp.store.items_db is imported

from lolwp.model.bundle import Model      # noqa: E402
from lolwp.store.items_db import ItemDB   # noqa: E402


def load_items(model, ddragon=None):
    """Item prices for the model's patch; the newest cached/available otherwise."""
    tries = []
    if ddragon:
        tries.append({"version": ddragon})
    if model.patch:
        tries.append({"game_version": model.patch + ".1"})
    tries.append({})
    last = None
    for kw in tries:
        try:
            return ItemDB.load(**kw)
        except Exception as exc:          # offline, or no release for that patch
            last = exc
    raise RuntimeError(f"could not load item prices: {last}")


class FixedResolver:
    def __init__(self, model_path=None, ddragon=None):
        self.model_path = str(model_path or FALLBACK_MODEL)
        self.ddragon = ddragon
        self._cached = None

    def __call__(self):
        if self._cached is None:
            model = Model.load(self.model_path)
            self._cached = (model, load_items(model, self.ddragon))
        return self._cached
