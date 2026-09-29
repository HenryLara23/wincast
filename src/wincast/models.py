"""The models Wincast can use, and which one a new game gets.

    %LOCALAPPDATA%\\Wincast\\models\\<name>.json    downloaded or imported
    resources/models/fallback.json                 bundled with the app

Choosing (at the start of each game, never mid-game): the pinned model if the
user pinned one and it still loads, otherwise the NEWEST compatible model --
by patch, then by creation time -- with the bundled one competing on equal
terms. The Live Client API doesn't say which patch the game is on, so "newest"
is the rule.

Every file goes through the vendored loader before it's accepted, so a model for
another feature set, or anything malformed, is refused with a plain reason.
Model files are data (JSON), never code.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .paths import FALLBACK_MODEL

from lolwp.model.bundle import BundleError, Model

BUNDLED = "bundled"


@dataclass
class ModelInfo:
    path: Path
    name: str
    patch: str = ""
    created_utc: str = ""
    source: str = "installed"            # "bundled" / "installed"
    train_games: Optional[int] = None
    accuracy: Optional[float] = None
    log_loss: Optional[float] = None
    ok: bool = True
    error: str = ""
    bundle: dict = field(default=None, repr=False)

    @property
    def stamp(self) -> str:
        """When it was trained, YYYYMMDD-HHMM (how release files are named)."""
        d = re.sub(r"[^0-9]", "", self.created_utc or "")[:12].ljust(12, "0")
        return f"{d[:8]}-{d[8:12]}"

    def sort_key(self):
        return (patch_key(self.patch), self.created_utc or "")


def patch_key(patch: str):
    """'16.19' -> (16, 19); junk sorts first."""
    try:
        return tuple(int(x) for x in str(patch).split(".")[:2])
    except ValueError:
        return (0, 0)


def read_model(path: Path, source="installed") -> ModelInfo:
    path = Path(path)
    try:
        bundle = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        return ModelInfo(path, path.stem, source=source, ok=False, error=f"not readable: {exc}")
    info = info_from_bundle(bundle, path, source)
    try:
        if bundle.get("kind") != "live":
            raise BundleError(f"this is a {bundle.get('kind') or 'unknown'} model, not a live one")
        Model(bundle)                                   # full validation + feature set
    except (BundleError, KeyError, TypeError, ValueError) as exc:
        info.ok, info.error = False, str(exc)
    return info


def info_from_bundle(bundle: dict, path: Path, source: str) -> ModelInfo:
    hold = ((bundle.get("metrics") or {}).get("holdout") or {}) if isinstance(bundle, dict) else {}
    games = (bundle.get("games") or {}).get("train") if isinstance(bundle, dict) else None
    return ModelInfo(path=Path(path), name=str(bundle.get("name") or Path(path).stem),
                     patch=str(bundle.get("patch") or ""),
                     created_utc=str(bundle.get("created_utc") or ""), source=source,
                     train_games=games, accuracy=hold.get("acc"), log_loss=hold.get("log_loss"),
                     bundle=bundle)


def safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "model"


class ModelStore:
    def __init__(self, folder: Path, bundled: Path = FALLBACK_MODEL):
        self.folder = Path(folder)
        self.bundled = Path(bundled)

    def models(self) -> List[ModelInfo]:
        """Everything: bundled first, then installed, newest first; broken ones included
        (with ok=False and a reason) so the Settings list can show them."""
        out = [read_model(self.bundled, BUNDLED)]
        if self.folder.exists():
            installed = [read_model(p) for p in self.folder.glob("*.json")]
            installed.sort(key=lambda m: m.sort_key(), reverse=True)
            out += installed
        return out

    def choose(self, pinned: str = "") -> ModelInfo:
        usable = [m for m in self.models() if m.ok]
        if pinned:
            for m in usable:
                if m.name == pinned:
                    return m
        if not usable:
            raise BundleError("no usable model (the bundled one failed to load)")
        return max(usable, key=lambda m: m.sort_key())

    def newest(self) -> Optional[ModelInfo]:
        usable = [m for m in self.models() if m.ok]
        return max(usable, key=lambda m: m.sort_key()) if usable else None

    def has(self, name: str) -> bool:
        return any(m.name == name for m in self.models() if m.ok)

    def has_release(self, patch: str, stamp: str) -> bool:
        """Is the model published as model-<patch>-<stamp>... already here?"""
        return any(m.ok and m.patch == patch and m.stamp == stamp for m in self.models())

    def install_bytes(self, data: bytes, source_label="") -> ModelInfo:
        """Validate and save a model file's contents. Raises ValueError with a
        plain reason if it isn't usable. Returns the saved model."""
        try:
            bundle = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ValueError("That file isn't a Wincast model (not JSON).") from None
        if not isinstance(bundle, dict):
            raise ValueError("That file isn't a Wincast model.")
        try:
            if bundle.get("kind") != "live":
                raise BundleError(f"it's a {bundle.get('kind') or 'unknown'} model, not a live one")
            Model(bundle)
        except (BundleError, KeyError, TypeError) as exc:
            raise ValueError(f"This model can't be used: {exc}") from None
        name = str(bundle.get("name") or "model")
        self.folder.mkdir(parents=True, exist_ok=True)
        dest = self.folder / f"{safe_filename(name)}.json"
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(dest)
        return read_model(dest)

    def import_file(self, path) -> ModelInfo:
        return self.install_bytes(Path(path).read_bytes())

    def remove(self, info: ModelInfo):
        if info.source == BUNDLED:
            raise ValueError("The bundled model can't be removed.")
        Path(info.path).unlink(missing_ok=True)
