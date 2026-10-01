"""The Qt worker: polls off the UI thread, delivers signals on it, stops cleanly."""

import json
import os
import threading
import unittest

from tests import GOLDEN_MODEL, ROOT

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
    HAVE_QT = True
except ImportError:                                   # pragma: no cover
    HAVE_QT = False

from wincast.client import OFFLINE, Poll


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class TestScoringRunner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])
        g = json.loads((ROOT / "tests" / "fixtures" / "golden_game.json").read_text("utf-8"))
        cls.snaps = g["snapshots"]

    def test_game_runs_through_the_thread(self):
        from wincast.resolver import FixedResolver
        from wincast.session import ENDED, Engine
        from wincast.worker import ScoringRunner
        from tests.test_session import with_game_end

        script = [Poll(OFFLINE)] + [Poll.of(s) for s in self.snaps[:6]] + \
                 [Poll.of(with_game_end(self.snaps[5]))] + [Poll(OFFLINE)] * 50
        poll_threads = []

        def poll():
            poll_threads.append(threading.get_ident())
            return script.pop(0) if script else Poll(OFFLINE)

        runner = ScoringRunner(Engine(FixedResolver(GOLDEN_MODEL)), poll, fast_ms=1, slow_ms=1)
        got, states, ended, started = [], [], [], []
        ui_thread = threading.get_ident()
        delivered_on = set()
        runner.updated.connect(lambda u: (got.append(u), delivered_on.add(threading.get_ident())))
        runner.stateChanged.connect(states.append)
        runner.gameStarted.connect(started.append)
        runner.gameEnded.connect(ended.append)

        loop = QEventLoop()
        runner.gameEnded.connect(lambda _g: QTimer.singleShot(20, loop.quit))
        QTimer.singleShot(10000, loop.quit)              # safety net
        runner.start()
        loop.exec()
        self.assertTrue(runner.stop())

        self.assertEqual(started, [1])
        self.assertEqual(len(ended), 1)
        self.assertEqual(ended[0].result, "Win")
        self.assertEqual(states[:3], ["no_game", "in_game", "ended"])
        self.assertTrue(any(u.state == ENDED for u in got))
        self.assertEqual(delivered_on, {ui_thread})       # signals arrive on the UI thread
        self.assertNotIn(ui_thread, set(poll_threads))    # polling never ran on it
        self.assertFalse(runner.thread.isRunning())


    def test_error_polls_fall_back_to_eventdata_for_the_result(self):
        from wincast.client import ERROR
        from wincast.resolver import FixedResolver
        from wincast.session import Engine
        from wincast.worker import ScoringRunner

        script = [Poll.of(s) for s in self.snaps[:4]] + [Poll(ERROR, detail="read timeout")] * 3 \
            + [Poll(OFFLINE)] * 50
        asked = []

        def events():
            asked.append(1)
            if len(asked) < 2:
                return None                                  # first try: nothing yet
            return [{"EventName": "GameEnd", "EventTime": 900.0, "Result": "Win"}]

        runner = ScoringRunner(Engine(FixedResolver(GOLDEN_MODEL)), lambda: script.pop(0) if script else Poll(OFFLINE),
                               fast_ms=1, slow_ms=1, events=events)
        ended, finished = [], []
        runner.gameEnded.connect(ended.append)
        runner.gameFinished.connect(finished.append)
        loop = QEventLoop()
        runner.gameFinished.connect(lambda _r: QTimer.singleShot(20, loop.quit))
        QTimer.singleShot(10000, loop.quit)
        runner.start()
        loop.exec()
        self.assertTrue(runner.stop())
        self.assertEqual([g.result for g in ended], ["Win"])
        self.assertEqual([r["result"] for r in finished], ["Win"])

    def test_game_end_between_snapshots_is_caught(self):
        """After a defeat the client closes within ~1 s of GameEnd (Viego game,
        2026-10-01: GameEnd at 1571 s, last 2 s snapshot at 1569 s, then gone).
        The quick /eventdata check between snapshots must catch it."""
        from wincast.resolver import FixedResolver
        from wincast.session import Engine
        from wincast.worker import ScoringRunner

        snaps = [Poll.of(s) for s in self.snaps[:4]]
        served = []

        def poll():
            if snaps:
                served.append(1)
                return snaps.pop(0)
            return Poll(OFFLINE)                             # the client is already gone

        def events():
            if len(served) < 4:
                return []
            return [{"EventName": "GameEnd", "EventTime": 1571.0, "Result": "Lose"}]

        runner = ScoringRunner(Engine(FixedResolver(GOLDEN_MODEL)), poll, fast_ms=400, slow_ms=400,
                               events=events, events_ms=20)
        ended, finished = [], []
        runner.gameEnded.connect(ended.append)
        runner.gameFinished.connect(finished.append)
        loop = QEventLoop()
        runner.gameFinished.connect(lambda _r: QTimer.singleShot(20, loop.quit))
        QTimer.singleShot(10000, loop.quit)
        runner.start()
        loop.exec()
        self.assertTrue(runner.stop())
        self.assertEqual([g.result for g in ended], ["Lose"])
        self.assertEqual([r["result"] for r in finished], ["Lose"])


if __name__ == "__main__":
    unittest.main()
