"""Logging to %LOCALAPPDATA%\\Wincast\\wincast.log (a windowed .exe has no console)."""

import logging
import sys
from logging.handlers import RotatingFileHandler

from .paths import user_data_dir


def setup(level=logging.INFO):
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        d = user_data_dir()
        d.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(d / "wincast.log", maxBytes=1_000_000, backupCount=2,
                                 encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass
    if sys.stderr is not None:                      # None in a windowed .exe
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
