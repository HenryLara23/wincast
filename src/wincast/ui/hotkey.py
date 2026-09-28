"""A system-wide hotkey (default Ctrl+Shift+P) to lock/unlock the overlay.

Windows: RegisterHotKey on a small hidden helper window, read back through a
Qt native event filter. The helper exists because the overlay's own native
window is re-created every time it is locked or unlocked, which would drop a
hotkey registered on it. Elsewhere this is a no-op: register() returns False
and the tray menu does the same job.

Known limit (tested 2026-09-28): the hotkey fires on the desktop but not while
League is the focused window. Unlock from the desktop or the tray menu; the
position is remembered, so it only needs doing once.
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Signal
from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)

DEFAULT = "Ctrl+Shift+P"          # user-changeable in settings (Phase 7)
WM_HOTKEY = 0x0312
MODS = {"ctrl": 0x2, "control": 0x2, "shift": 0x4, "alt": 0x1, "win": 0x8}
MOD_NOREPEAT = 0x4000


def parse(text: str):
    """'Ctrl+Shift+P' -> (modifiers, virtual-key code). Letters, digits, F1-F24."""
    mods, key = 0, None
    for part in [p.strip() for p in text.split("+") if p.strip()]:
        low = part.lower()
        if low in MODS:
            mods |= MODS[low]
        elif len(part) == 1 and part.isalnum():
            key = ord(part.upper())
        elif low.startswith("f") and low[1:].isdigit() and 1 <= int(low[1:]) <= 24:
            key = 0x70 + int(low[1:]) - 1
        else:
            raise ValueError(f"unsupported key {part!r} in hotkey {text!r}")
    if key is None:
        raise ValueError(f"hotkey {text!r} has no key")
    return mods, key


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, hotkey_id, callback):
        super().__init__()
        self.hotkey_id = hotkey_id
        self.callback = callback

    def nativeEventFilter(self, event_type, message):
        try:
            if bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                from ctypes import wintypes
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY and msg.wParam == self.hotkey_id:
                    self.callback()
                    return True, 0
        except Exception:                         # never break the event loop
            log.exception("hotkey filter")
        return False, 0


class GlobalHotkey(QObject):
    activated = Signal()

    HOTKEY_ID = 0xB00F

    def __init__(self, text: str = DEFAULT, parent=None):
        super().__init__(parent)
        self.text = text
        self.registered = False
        self._helper = None
        self._filter = None

    def register(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            import ctypes
            from ctypes import wintypes
            mods, vk = parse(self.text)
            self._helper = QWidget()
            self._helper.setWindowTitle("Wincast hotkey")
            hwnd = wintypes.HWND(int(self._helper.winId()))    # forces a native window; never shown
            self._filter = _Filter(self.HOTKEY_ID, self.activated.emit)
            QCoreApplication.instance().installNativeEventFilter(self._filter)
            user32 = ctypes.windll.user32
            user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            self.registered = bool(user32.RegisterHotKey(hwnd, self.HOTKEY_ID,
                                                         mods | MOD_NOREPEAT, vk))
            if not self.registered:
                log.warning("could not register %s (another app may own it)", self.text)
        except Exception:
            log.exception("hotkey registration failed")
            self.registered = False
        return self.registered

    def unregister(self):
        if not self.registered:
            return
        try:
            import ctypes
            from ctypes import wintypes
            ctypes.windll.user32.UnregisterHotKey(wintypes.HWND(int(self._helper.winId())),
                                                  self.HOTKEY_ID)
        except Exception:
            pass
        self.registered = False
