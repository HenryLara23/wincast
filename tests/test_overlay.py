"""The overlay widget, off-screen: visibility rules, lock toggling, text, drawing,
saved position. Window-manager behaviour (click-through over the game, topmost)
can only be checked on Windows by hand -- see PLANNING.md Phase 6."""

import os
import tempfile
import unittest

from tests import ROOT  # noqa: F401

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtWidgets import QApplication
    from wincast.ui import hotkey
    from wincast.ui.overlay import EVEN, LOSE, WIN, OverlayWindow, chance_color, trend_level
    HAVE_QT = True
except ImportError:                                    # no PySide6 / no GUI libs
    HAVE_QT = False

from wincast.session import ENDED, IN_GAME, NO_GAME, Update


def up(state=IN_GAME, p=0.731, t=300.0, game_id=1, team="CHAOS", result=None):
    return Update(state, game_id=game_id, game_time=t, team=team,
                  p_mine=p, p_mine_raw=p, result=result)


@unittest.skipUnless(HAVE_QT, "PySide6 widgets not available")
class TestOverlay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        fd, self.ini = tempfile.mkstemp(suffix=".ini")
        os.close(fd)
        self.settings = QSettings(self.ini, QSettings.Format.IniFormat)
        self.ov = OverlayWindow(self.settings)

    def tearDown(self):
        self.ov.close()
        self.ov.deleteLater()
        os.unlink(self.ini)

    def test_hidden_until_a_game_is_scored(self):
        self.assertFalse(self.ov.isVisible())
        self.ov.show_update(Update(NO_GAME))
        self.assertFalse(self.ov.isVisible())
        self.ov.show_update(up())
        self.assertTrue(self.ov.isVisible())
        self.ov.show_update(Update(NO_GAME))
        self.assertFalse(self.ov.isVisible())

    def test_locked_is_click_through_unlocked_is_not(self):
        flag = Qt.WindowType.WindowTransparentForInput
        self.assertTrue(self.ov.locked)
        self.assertTrue(self.ov.windowFlags() & flag)
        seen = []
        self.ov.lockChanged.connect(seen.append)
        self.ov.toggle_lock()
        self.assertFalse(self.ov.locked)
        self.assertFalse(self.ov.windowFlags() & flag)
        self.assertTrue(self.ov.isVisible())               # unlocked: always visible to place it
        self.assertTrue(self.ov.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.ov.toggle_lock()
        self.assertEqual(seen, [False, True])
        self.assertFalse(self.ov.isVisible())              # locked again, no game
        self.assertTrue(self.settings.contains("overlay/x"))   # locking saves the spot

    def test_text(self):
        self.ov.show_update(up(p=0.731))
        self.assertEqual(self.ov.display(), ("73%", "", 0.731))      # no caption while playing
        self.ov.show_update(up(state=ENDED, p=0.97, result="Win"))
        self.assertEqual(self.ov.display()[1], "Victory")
        self.ov.show_update(up(state=ENDED, p=0.1, result="Lose"))
        self.assertEqual(self.ov.display()[1], "Defeat")
        self.ov.hotkey_text = "Alt+F9"
        self.ov.set_locked(False)
        self.assertIn("Alt+F9", self.ov.toolTip())                   # unlock hint lives in the tooltip
        self.ov.set_locked(True)
        self.assertEqual(self.ov.toolTip(), "")

    def test_trend_keeps_ten_minutes_by_default_and_resets_per_game(self):
        for t in range(0, 1200, 2):
            self.ov.show_update(up(t=float(t), p=0.5 + t / 3000))
        hist = list(self.ov._history)
        self.assertAlmostEqual(hist[-1][0] - hist[0][0], 600, delta=2)
        self.ov.show_update(up(game_id=2, t=5.0))
        self.assertEqual(len(self.ov._history), 1)

    def test_trend_window_is_a_setting(self):
        self.settings.setValue("overlay/trend_minutes", 15)
        ov = OverlayWindow(self.settings)
        try:
            self.assertEqual(ov.trend_seconds, 900)
        finally:
            ov.close()
        self.settings.setValue("overlay/trend_minutes", "junk")
        ov = OverlayWindow(self.settings)
        try:
            self.assertEqual(ov.trend_seconds, 600)             # bad value -> default
        finally:
            ov.close()

    def test_trend_scale_keeps_the_extremes_visible(self):
        self.assertAlmostEqual(trend_level(0.5), 0.5)
        self.assertAlmostEqual(trend_level(0.99), 1.0)
        self.assertEqual(trend_level(0.9999), 1.0)              # clamped
        self.assertEqual(trend_level(0.0001), 0.0)
        # 95% -> 99% gets ~18% of the box height; on a plain 0-100% scale it's 4%
        self.assertGreater(trend_level(0.99) - trend_level(0.95), 0.15)
        self.assertLess(trend_level(0.02), trend_level(0.05))

    def test_draws(self):
        for t in range(0, 60, 2):
            self.ov.show_update(up(t=float(t), p=0.4 + t / 200))
        img = self.ov.grab().toImage()
        self.assertFalse(img.isNull())
        c = img.pixelColor(img.width() // 2, img.height() // 2)
        self.assertGreater(c.alpha(), 0)                   # the pill is painted

    def test_colors(self):
        self.assertEqual(chance_color(0.5).name(), EVEN.name())
        self.assertEqual(chance_color(0.9).name(), WIN.name())
        self.assertEqual(chance_color(0.1).name(), LOSE.name())

    def test_off_screen_position_is_pulled_back(self):
        self.settings.setValue("overlay/x", 99999)
        self.settings.setValue("overlay/y", -5000)
        ov = OverlayWindow(self.settings)
        try:
            avail = ov.screen().availableGeometry()
            self.assertTrue(avail.contains(ov.frameGeometry().topLeft()))
        finally:
            ov.close()

    def test_hotkey_parsing(self):
        self.assertEqual(hotkey.parse(hotkey.DEFAULT), (0x2 | 0x4, ord("P")))
        self.assertEqual(hotkey.parse("alt+F9"), (0x1, 0x78))
        with self.assertRaises(ValueError):
            hotkey.parse("Ctrl+Shift")


if __name__ == "__main__":
    unittest.main()
