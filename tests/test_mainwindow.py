"""The main window, off-screen: live tab follows updates, history lists/plots/
deletes saved games, settings apply to the overlay and persist."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from tests import GOLDEN_MODEL, ROOT

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtGui import QKeySequence
    from PySide6.QtWidgets import QApplication, QMessageBox
    from wincast.ui.mainwindow import MainWindow
    from wincast.ui.overlay import OverlayWindow
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

from wincast import prefs
from wincast.client import Poll
from wincast.history import HistoryStore, record_from_game
from wincast.resolver import FixedResolver
from wincast.session import ENDED, IN_GAME, Engine, Update

GOLDEN = json.loads((ROOT / "tests" / "fixtures" / "golden_game.json").read_text("utf-8"))


def played_record(result="Win"):
    from tests.test_session import with_game_end
    eng = Engine(FixedResolver(GOLDEN_MODEL), tau_s=5)
    for s in GOLDEN["snapshots"]:
        eng.feed(Poll.of(s))
    eng.feed(Poll.of(with_game_end(GOLDEN["snapshots"][-1], result)))
    return record_from_game(eng.pop_finished()[0])


@unittest.skipUnless(HAVE_QT, "PySide6 widgets not available")
class TestMainWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.model = FixedResolver(GOLDEN_MODEL)()[0]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.tmp.name) / "s.ini"), QSettings.Format.IniFormat)
        self.store = HistoryStore(Path(self.tmp.name) / "history")
        self.overlay = OverlayWindow(self.settings)
        from wincast.models import ModelStore
        self.models = ModelStore(Path(self.tmp.name) / "models")
        self.win = MainWindow(self.settings, self.store, self.overlay, model=self.model,
                              connection="verified (riotgames.pem)", models=self.models)

    def tearDown(self):
        self.win.deleteLater()
        self.overlay.deleteLater()
        self.tmp.cleanup()

    def test_live_tab_follows_updates(self):
        for t in range(0, 300, 2):
            self.win.show_update(Update(IN_GAME, game_id=1, game_time=float(t), team="CHAOS", champion="Xayah",
                                        p_mine=0.6, p_mine_raw=0.62))
        self.assertEqual(len(self.win.live_graph.points), 150)
        self.assertEqual(self.win.gauge.value, 0.6)
        self.assertEqual(self.win.lv["side"].text(), "Red")
        self.assertEqual(self.win.lv["time"].text(), "4:58")
        self.assertEqual(self.win.lv["champion"].text(), "Xayah")
        self.assertIn("60%", self.win.sb_chance.text())
        self.win.show_update(Update(ENDED, game_id=1, game_time=300.0, team="CHAOS",
                                    p_mine=0.6, p_mine_raw=0.6, result="Win"))
        self.assertEqual(self.win.lv["status"].text(), "Victory")
        self.win.show_update(Update(IN_GAME, game_id=2, game_time=2.0, team="ORDER",
                                    p_mine=0.5, p_mine_raw=0.5))
        self.assertEqual(len(self.win.live_graph.points), 1)       # new game, new graph

    def test_history_lists_plots_and_deletes(self):
        self.assertIn("No games yet", self.win.hist_count.text())
        self.store.save(played_record("Win"))
        rec = played_record("Lose")
        rec["started_utc"] = "2030-01-01T00:00:00Z"
        self.store.save(rec)
        self.win.game_saved()
        self.assertEqual(self.win.hist.topLevelItemCount(), 2)
        self.assertIn("1 won, 1 lost", self.win.hist_count.text())
        self.assertEqual(self.win.sb_games.text(), "Games recorded: 2")
        first = self.win.hist.topLevelItem(0)
        self.assertEqual(first.text(3), "Defeat")                     # newest first
        self.win.hist.setCurrentItem(first)
        self.assertGreater(len(self.win.hist_graph.points), 10)
        self.assertTrue(self.win.hist_graph.events)
        self.assertTrue(self.win.btn_delete.isEnabled())
        orig = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
        try:
            self.win._delete_selected()
        finally:
            QMessageBox.question = orig
        self.assertEqual(self.win.hist.topLevelItemCount(), 1)

    def test_history_graph_zoom(self):
        self.store.save(played_record("Win"))
        self.win.game_saved()
        g = self.win.hist_graph
        self.assertTrue(g.zoomable)
        lo, hi = g.full_range()
        self.assertGreater(hi, 1000)
        self.assertFalse(self.win.btn_zoom_home.isEnabled())
        g.set_view(300, 600)
        self.assertEqual(g.range(), (300, 600))
        self.assertTrue(self.win.btn_zoom_home.isEnabled())
        g.set_view(400, 405)                                  # too narrow -> widened
        t0, t1 = g.range()
        self.assertAlmostEqual(t1 - t0, g.MIN_VIEW_S)
        g.back()
        self.assertEqual(g.range(), (300, 600))
        g.set_view(-500, 99999)                               # whole game -> not zoomed
        self.assertIsNone(g.view)
        g.set_view(100, 200)
        g.home()
        self.assertIsNone(g.view)
        self.assertFalse(self.win.btn_zoom_home.isEnabled())
        img = g.grab().toImage()                              # draws while zoomed, too
        g.set_view(600, 700)
        self.assertFalse(g.grab().toImage().isNull())
        self.assertFalse(img.isNull())

    def test_history_list_and_graph_split_is_remembered(self):
        self.win.hist_split.setSizes([100, 400])
        self.win.hist_split.splitterMoved.emit(100, 1)
        self.assertIsNotNone(self.settings.value("window/history_split"))

    def test_settings_apply_to_overlay_and_persist(self):
        self.assertFalse(self.win.btn_apply.isEnabled())
        self.win.sp_scale.setValue(150)
        self.win.sp_trend.setValue(15)
        self.win.chk_trend.setChecked(False)
        self.win.key_edit.setKeySequence(QKeySequence("Ctrl+Alt+W"))
        self.assertTrue(self.win.btn_apply.isEnabled())
        self.win.apply_settings()
        self.assertEqual(prefs.get(self.settings, "overlay/scale"), 1.5)
        self.assertEqual(prefs.get(self.settings, "overlay/trend_minutes"), 15)
        self.assertEqual(prefs.get(self.settings, "hotkey"), "Ctrl+Alt+W")
        self.assertEqual(self.overlay.scale, 1.5)
        self.assertEqual(self.overlay.width(), 225)
        self.assertFalse(self.overlay.show_trend)
        self.assertEqual(self.overlay.trend_seconds, 900)
        self.assertEqual(self.overlay.hotkey_text, "Ctrl+Alt+W")
        self.assertFalse(self.win.btn_apply.isEnabled())

    def test_restore_defaults(self):
        self.win.sp_scale.setValue(200)
        self.win.apply_settings()
        self.win._restore_defaults()
        self.win.apply_settings()
        self.assertEqual(prefs.get(self.settings, "overlay/scale"), 1.0)
        self.assertEqual(self.overlay.width(), 150)

    def test_move_overlay_menu_mirrors_lock(self):
        self.win.act_move.setChecked(True)
        self.assertFalse(self.overlay.locked)
        self.assertEqual(self.win.sb_overlay.text(), "Overlay: moving")
        self.overlay.set_locked(True)
        self.assertFalse(self.win.act_move.isChecked())

    def _wait(self, cond, ms=5000):
        import time
        end = time.time() + ms / 1000
        while not cond() and time.time() < end:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()

    def test_models_tab_lists_pins_and_removes(self):
        from tests.test_models import variant
        ml = self.win.model_list
        self.assertEqual(ml.topLevelItemCount(), 1)
        self.assertEqual(ml.topLevelItem(0).text(0), "Next game")
        self.assertEqual(ml.topLevelItem(0).text(5), "Built in")
        self.models.install_bytes(variant("m_16.19", "16.19", "2026-10-01T00:00:00Z"))
        self.win.refresh_models()
        self.assertEqual(ml.topLevelItemCount(), 2)
        self.assertEqual(ml.topLevelItem(1).text(0), "Next game")        # newest wins
        self.assertEqual(ml.topLevelItem(0).text(0), "")
        ml.setCurrentItem(ml.topLevelItem(0))
        self.win._pin_selected()
        built_in = self.models.models()[0].name              # whatever model ships
        self.assertEqual(prefs.get(self.settings, "models/pinned"), built_in)
        self.assertEqual(ml.topLevelItem(0).text(0), "Pinned")
        self.assertTrue(self.win.btn_newest.isEnabled())
        self.win._unpin()
        self.assertEqual(ml.topLevelItem(1).text(0), "Next game")
        ml.setCurrentItem(ml.topLevelItem(1))
        orig = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
        try:
            self.win._remove_selected()
        finally:
            QMessageBox.question = orig
        self.assertEqual(ml.topLevelItemCount(), 1)
        ml.setCurrentItem(ml.topLevelItem(0))
        self.assertFalse(self.win.btn_remove.isEnabled())                # built-in stays

    def test_check_now_and_download(self):
        from tests.test_models import variant
        from wincast import updates
        rel = updates.ModelRelease("model-16.19-x", "m_16.19", "16.19", "", "u", "s")
        orig = updates.check, updates.download
        updates.check = lambda *a, **k: rel
        updates.download = lambda r, store, **k: store.install_bytes(
            variant("m_16.19", "16.19", "2026-10-01T00:00:00Z"))
        try:
            self.assertFalse(self.win.btn_download.isEnabled())
            self.win.check_for_models()
            self._wait(lambda: self.win.btn_download.isEnabled())
            self.assertIn("newer model is available", self.win.update_status.text())
            self.win.download_model()
            self._wait(lambda: "Installed" in self.win.update_status.text())
            self.assertIn("m_16.19", self.win.update_status.text())
            self.assertEqual(self.models.choose().name, "m_16.19")
            self.win.check_for_models()
            self._wait(lambda: "newest" in self.win.update_status.text())
            self.assertFalse(self.win.btn_download.isEnabled())
        finally:
            updates.check, updates.download = orig

    def test_check_now_reports_errors(self):
        from wincast import updates
        orig = updates.check

        def boom(*a, **k):
            raise updates.UpdateError("couldn't reach GitHub (ConnectionError)")
        updates.check = boom
        try:
            self.win.check_for_models()
            self._wait(lambda: "Couldn't check" in self.win.update_status.text())
            self.assertIn("reach GitHub", self.win.update_status.text())
            self.assertTrue(self.win.btn_check.isEnabled())
        finally:
            updates.check = orig

    def test_close_hides_instead_of_quitting(self):
        self.win.show()
        self.win.close()
        self.assertFalse(self.win.isVisible())


class TestPrefs(unittest.TestCase):
    def test_bad_values_fall_back(self):
        class S(dict):
            def value(self, k, d=None):
                return self.get(k, d)
        s = S({"overlay/scale": "junk", "overlay/trend_minutes": 999, "overlay/trend": "false"})
        self.assertEqual(prefs.get(s, "overlay/scale"), 1.0)
        self.assertEqual(prefs.get(s, "overlay/trend_minutes"), 60)
        self.assertFalse(prefs.get(s, "overlay/trend"))
        self.assertEqual(prefs.get(s, "hotkey"), "Ctrl+Shift+P")


if __name__ == "__main__":
    unittest.main()
