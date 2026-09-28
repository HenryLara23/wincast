"""Running the engine off the UI thread.

    engine = Engine(FixedResolver())
    runner = ScoringRunner(engine, LiveClient().poll)
    runner.updated.connect(overlay.show_update)      # delivered on the UI thread
    runner.start()
    ...
    runner.stop()                                     # on quit

A poll can block for up to ~3 s (connect + read timeouts) while the game is
starting, so it must never run on the UI thread. The worker lives in its own
QThread and schedules itself with a single-shot timer: fast while a game is on,
slow while waiting, so an idle Wincast costs next to nothing.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from .session import IN_GAME, LOADING_STATE, Engine

FAST_MS = 2000      # in game: matches the capture cadence the model was checked on
SLOW_MS = 5000      # no game / loading / unsupported


class ScoringWorker(QObject):
    updated = Signal(object)          # session.Update, after every poll
    stateChanged = Signal(str)        # only when the state changes
    gameStarted = Signal(int)         # new game id
    gameEnded = Signal(object)        # the finished session.Game (curve, result, model)
    failed = Signal(str)              # an unexpected error; the loop keeps going

    def __init__(self, engine: Engine, poll, fast_ms=FAST_MS, slow_ms=SLOW_MS):
        super().__init__()
        self.engine = engine
        self.poll = poll
        self.fast_ms, self.slow_ms = fast_ms, slow_ms
        self._timer = None
        self._running = False
        self._last_state = None
        self._last_game_id = 0
        self._ended_ids = set()

    @Slot()
    def start(self):
        self._running = True
        self._timer = QTimer(self)                # created here, so it lives in this thread
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)
        self._timer.start(0)

    @Slot()
    def stop(self):
        self._running = False
        if self._timer is not None:
            self._timer.stop()

    @Slot(float)
    def setSmoothing(self, tau_s):
        self.engine.set_smoothing(tau_s)

    @Slot()
    def _tick(self):
        if not self._running:
            return
        try:
            up = self.engine.feed(self.poll())
        except Exception as exc:                  # never let the loop die
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            up = self.engine.last
        if up.game_id and up.game_id != self._last_game_id:
            self._last_game_id = up.game_id
            self.gameStarted.emit(up.game_id)
        if up.state != self._last_state:
            self._last_state = up.state
            self.stateChanged.emit(up.state)
        self.updated.emit(up)
        g = self.engine.game
        if g is not None and g.result is not None and g.id not in self._ended_ids:
            self._ended_ids.add(g.id)
            self.gameEnded.emit(g)
        if self._running:
            fast = up.state in (IN_GAME, LOADING_STATE)
            self._timer.start(self.fast_ms if fast else self.slow_ms)


class ScoringRunner(QObject):
    """Owns the thread. Re-exposes the worker's signals, which Qt delivers to
    whatever thread the receiver lives in (the UI thread for widgets)."""

    updated = Signal(object)
    stateChanged = Signal(str)
    gameStarted = Signal(int)
    gameEnded = Signal(object)
    failed = Signal(str)
    _stopRequested = Signal()
    _smoothingRequested = Signal(float)

    def __init__(self, engine: Engine, poll, parent=None, **kw):
        super().__init__(parent)
        self.thread = QThread()
        self.thread.setObjectName("wincast-scoring")
        self.worker = ScoringWorker(engine, poll, **kw)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.start)
        self._stopRequested.connect(self.worker.stop)
        self._smoothingRequested.connect(self.worker.setSmoothing)
        for name in ("updated", "stateChanged", "gameStarted", "gameEnded", "failed"):
            getattr(self.worker, name).connect(getattr(self, name))

    def start(self):
        self.thread.start()

    def set_smoothing(self, tau_s: float):
        self._smoothingRequested.emit(float(tau_s))

    def stop(self, timeout_ms=5000):
        """Stop polling and join the thread. A poll in flight finishes first."""
        if not self.thread.isRunning():
            return True
        self._stopRequested.emit()
        self.thread.quit()
        return self.thread.wait(timeout_ms)
