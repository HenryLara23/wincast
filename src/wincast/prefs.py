"""Every user setting in one place: key, default, and how to read it safely.

Stored with QSettings (on Windows: HKEY_CURRENT_USER\\Software\\Wincast\\Wincast).
A bad or missing value falls back to the default rather than breaking startup.
"""

DEFAULTS = {
    "overlay/scale": 1.0,            # 0.6 .. 3.0
    "overlay/opacity": 0.95,         # 0.3 .. 1.0
    "overlay/trend": True,
    "overlay/trend_minutes": 10,     # 1 .. 60
    "hotkey": "Ctrl+Shift+P",
    "smoothing_s": 5.0,              # 0 .. 30 (0 = raw model output)
    "general/open_window_on_start": True,
    "models/auto_update": False,     # check GitHub at startup and install new models
    "models/pinned": "",             # a model name to always use; "" = the newest
}

LIMITS = {
    "overlay/scale": (0.6, 3.0),
    "overlay/opacity": (0.3, 1.0),
    "overlay/trend_minutes": (1, 60),
    "smoothing_s": (0.0, 30.0),
}


def get(settings, key):
    default = DEFAULTS[key]
    raw = settings.value(key, default)
    try:
        if isinstance(default, bool):
            val = raw if isinstance(raw, bool) else str(raw).strip().lower() in ("true", "1", "yes")
        elif isinstance(default, (int, float)):
            val = type(default)(float(raw))
        else:
            val = str(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        return default
    if key in LIMITS:
        lo, hi = LIMITS[key]
        val = min(max(val, lo), hi)
    return val


def put(settings, key, value):
    settings.setValue(key, value)


def reset(settings):
    for key in DEFAULTS:
        settings.remove(key)
