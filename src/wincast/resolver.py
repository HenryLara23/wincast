"""Which model and item prices a new game gets.

FixedResolver   one model file (--model on the command line, headless, tests)
StoreResolver   the app: asks the ModelStore at the start of every game, so a
                model downloaded or imported mid-game is used from the NEXT game.
Loaded models and item prices are cached, so a new game costs nothing when the
choice hasn't changed."""

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


class StoreResolver:
    def __init__(self, store, pinned=lambda: "", ddragon=None):
        self.store = store
        self.pinned = pinned                 # callable -> pinned model name or ""
        self.ddragon = ddragon
        self._cache = {}                     # path -> (mtime, model, db)
        self.current = None                  # ModelInfo of the last choice

    def __call__(self):
        info = self.store.choose(self.pinned())
        key = str(info.path)
        mtime = info.path.stat().st_mtime if info.path.exists() else 0
        hit = self._cache.get(key)
        if hit is None or hit[0] != mtime:
            model = Model(info.bundle)
            hit = (mtime, model, load_items(model, self.ddragon))
            self._cache[key] = hit
        self.current = info
        return hit[1], hit[2]
