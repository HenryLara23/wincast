"""Start Wincast with Windows: one value under
HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run.

Only for the built .exe -- a `python -m wincast` run has no stable program to
point at, so it reports unsupported and the option is greyed out."""

import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "Wincast"


def supported() -> bool:
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def _command():
    return f'"{sys.executable}"'


def enabled() -> bool:
    if not supported():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            value, _ = winreg.QueryValueEx(k, NAME)
            return value == _command()
    except OSError:
        return False


def set_enabled(on: bool) -> bool:
    if not supported():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, _command())
            else:
                try:
                    winreg.DeleteValue(k, NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False
