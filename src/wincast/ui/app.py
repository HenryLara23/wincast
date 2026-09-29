"""Wiring: engine + background worker + overlay + hotkey + tray + main window.

    python -m wincast                       # the app
    python -m wincast --replay cap.jsonl.gz --speed 20
    python -m wincast --unlocked            # start with the overlay movable
"""

from __future__ import annotations

import logging
import signal
import sys

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QStyleFactory

from .. import APP_NAME, __version__, applog, prefs
from ..client import LiveClient
from ..history import HistoryStore
from ..paths import user_data_dir
from ..resolver import FixedResolver
from ..session import Engine
from ..worker import ScoringRunner
from .hotkey import GlobalHotkey
from .mainwindow import MainWindow
from .overlay import OverlayWindow
from .tray import Tray, app_icon

log = logging.getLogger("wincast")


def classic_style(app):
    """The Windows 7 Task Manager look: Qt's classic Windows style rather than the
    rounded Windows 11 one Qt 6.7+ picks by default."""
    if sys.platform == "win32" and "windowsvista" in [k.lower() for k in QStyleFactory.keys()]:
        app.setStyle("windowsvista")


def run(args) -> int:
    applog.setup()
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)            # windows hide; the app lives in the tray
    app.setWindowIcon(app_icon())
    classic_style(app)
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

    tau = args.smoothing if args.smoothing is not None else prefs.get(settings, "smoothing_s")
    engine = Engine(resolver, tau_s=tau)

    if args.replay:
        from ..replay import ReplayPoller
        poll = ReplayPoller(args.replay, args.speed)
        runner = ScoringRunner(engine, poll, fast_ms=500, slow_ms=500, source="replay")
        store = HistoryStore(user_data_dir() / "history-replay")   # keep test runs apart
        connection = f"replay ({args.speed:g}x)"
        log.info("replaying %s at %sx", args.replay, args.speed)
    else:
        client = LiveClient()
        runner = ScoringRunner(engine, client.poll, events=client.events)
        store = HistoryStore(user_data_dir() / "history")
        connection = client.tls
        log.info("live client: %s", client.tls)

    overlay = OverlayWindow(settings)
    hotkey_text = prefs.get(settings, "hotkey")
    overlay.hotkey_text = hotkey_text

    window = MainWindow(settings, store, overlay, model=model, connection=connection)
    window.runner = runner

    runner.updated.connect(overlay.show_update)
    runner.updated.connect(window.show_update)
    runner.failed.connect(lambda msg: log.warning("scoring loop: %s", msg))
    runner.stateChanged.connect(lambda s: log.info("state: %s", s))

    def save_game(record):
        try:
            path = store.save(record)
            log.info("saved game to %s (result: %s, %d points)", path,
                     record.get("result"), len(record.get("curve", [])))
            window.game_saved(path)
        except OSError:
            log.exception("could not save game history")

    runner.gameFinished.connect(save_game)

    hotkey = GlobalHotkey(hotkey_text)
    hotkey.activated.connect(overlay.toggle_lock)
    hotkey_ok = hotkey.register()
    log.info("hotkey %s: %s", hotkey_text, "registered" if hotkey_ok else "not available")
    window.hotkey = hotkey

    tray = Tray(overlay, hotkey_text, hotkey_ok, open_window=window.show_and_raise)
    runner.updated.connect(tray.show_update)
    window.tray = tray
    if tray.icon is None:                           # no tray: closing the window must quit
        app.setQuitOnLastWindowClosed(True)
        window.closeEvent = lambda e: (e.accept(), app.quit())

    # First run: nowhere saved yet, so start unlocked and let the user place it.
    first_run = not overlay.has_saved_position()
    if args.unlocked or first_run:
        overlay.set_locked(False)
    overlay.refresh_visibility()
    if first_run or prefs.get(settings, "general/open_window_on_start") or args.window:
        window.show_and_raise()

    def shutdown():
        runner.stop()                                # also saves a game still in progress
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
