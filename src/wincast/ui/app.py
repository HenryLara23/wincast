"""Wiring: engine + background worker + overlay + hotkey + tray.

    python -m wincast                       # the app
    python -m wincast --replay cap.jsonl.gz --speed 20
    python -m wincast --unlocked            # start with the overlay movable
"""

from __future__ import annotations

import logging
import signal
import sys

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from .. import APP_NAME, __version__, applog
from ..client import LiveClient
from ..resolver import FixedResolver
from ..session import Engine
from ..worker import ScoringRunner
from .hotkey import DEFAULT as DEFAULT_HOTKEY, GlobalHotkey
from .overlay import OverlayWindow
from .tray import Tray, app_icon

log = logging.getLogger("wincast")


def run(args) -> int:
    applog.setup()
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)            # the overlay hides; the app stays
    app.setWindowIcon(app_icon())
    settings = QSettings()
    log.info("Wincast %s starting", __version__)

    resolver = FixedResolver(args.model)
    try:
        model, db = resolver()
    except Exception as exc:
        log.exception("model load failed")
        QMessageBox.critical(None, APP_NAME, f"Couldn't load the model:\n{exc}")
        return 1
    log.info("model %s (patch %s), item prices %s", model.name, model.patch, db.version)

    tau = args.smoothing if args.smoothing is not None else float(settings.value("smoothing_s", 5.0))
    engine = Engine(resolver, tau_s=tau)

    if args.replay:
        from ..replay import ReplayPoller
        poll = ReplayPoller(args.replay, args.speed)
        runner = ScoringRunner(engine, poll, fast_ms=500, slow_ms=500)
        log.info("replaying %s at %sx", args.replay, args.speed)
    else:
        client = LiveClient()
        poll = client.poll
        runner = ScoringRunner(engine, poll)
        log.info("live client: %s", client.tls)

    overlay = OverlayWindow(settings)
    runner.updated.connect(overlay.show_update)
    runner.failed.connect(lambda msg: log.warning("scoring loop: %s", msg))
    runner.stateChanged.connect(lambda s: log.info("state: %s", s))

    hotkey_text = str(settings.value("hotkey", DEFAULT_HOTKEY))
    overlay.hotkey_text = hotkey_text
    hotkey = GlobalHotkey(hotkey_text)
    hotkey.activated.connect(overlay.toggle_lock)
    hotkey_ok = hotkey.register()
    log.info("hotkey %s: %s", hotkey_text, "registered" if hotkey_ok else "not available")

    tray = Tray(overlay, hotkey_text, hotkey_ok)
    runner.updated.connect(tray.show_update)

    # First run: nowhere saved yet, so start unlocked and let the user place it.
    if args.unlocked or not overlay.has_saved_position():
        overlay.set_locked(False)
    overlay.refresh_visibility()

    def shutdown():
        runner.stop()
        overlay.save_position()
        hotkey.unregister()
        log.info("stopped")

    app.aboutToQuit.connect(shutdown)

    # Ctrl+C in a terminal: Qt's loop doesn't see Python signals unless it wakes up.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    keepalive = QTimer(interval=250)
    keepalive.timeout.connect(lambda: None)
    keepalive.start()

    runner.start()
    if args.quit_after:
        QTimer.singleShot(int(args.quit_after * 1000), app.quit)
    return app.exec()
