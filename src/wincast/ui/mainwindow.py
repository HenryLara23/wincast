"""The Wincast window, styled after the Windows 7 Task Manager.

    File  Options  View  Help
    [ Live ][ History ][ Settings ]
    ...
    Status: In game | Win chance: 73% | Games recorded: 12 | Overlay: locked

Closing it quits Wincast, unless "Keep running in the tray" is on: then it only
hides. Wincast never shows pop-up notifications; problems show in the window.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
                               QKeySequenceEdit, QLabel, QMainWindow, QMessageBox,
                               QPushButton, QSpinBox, QSplitter, QTabWidget, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__, autostart, prefs
from ..paths import explorer_path, user_data_dir
from ..updates import REPO_URL
from ..session import CHAOS, ENDED, IN_GAME, LOADING_STATE, NO_GAME, ORDER, UNSUPPORTED
from .charts import TMGauge, TMGraph, clock
from .hotkey import parse as parse_hotkey

STATE_TEXT = {NO_GAME: "Waiting for a game", LOADING_STATE: "Loading",
              UNSUPPORTED: "Not scored", IN_GAME: "In game", ENDED: "Game over"}
SIDE_TEXT = {ORDER: "Blue", CHAOS: "Red", None: "Spectating"}
RESULT_TEXT = {"Win": "Victory", "Lose": "Defeat", None: "No result"}


def _sunken(text=""):
    lab = QLabel(text)
    lab.setFrameStyle(QFrame.Shape.Panel | QFrame.Shadow.Sunken)
    lab.setContentsMargins(4, 0, 4, 0)
    return lab


def local_time(started_utc: str) -> str:
    try:
        dt = datetime.strptime(started_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d  %H:%M")
    except (TypeError, ValueError):
        return started_utc or "?"


class BackgroundTask(QObject):
    """Run one blocking call (a network check) off the UI thread and hand the
    result back on it: done(result, error_text)."""

    done = Signal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._callback = None
        self.done.connect(self._finish)

    def run(self, fn, callback):
        import threading
        self._callback = callback

        def target():
            try:
                self.done.emit(fn(), "")
            except Exception as exc:              # shown to the user as text
                self.done.emit(None, str(exc) or type(exc).__name__)

        threading.Thread(target=target, daemon=True, name="wincast-bg").start()

    def _finish(self, result, error):
        if self._callback is not None:
            self._callback(result, error)


class SortItem(QTreeWidgetItem):
    """Sorts by the value stored under UserRole when there is one (numbers, dates)."""

    def __lt__(self, other):
        col = self.treeWidget().sortColumn() if self.treeWidget() else 0
        a, b = self.data(col, Qt.ItemDataRole.UserRole), other.data(col, Qt.ItemDataRole.UserRole)
        if a is not None and b is not None:
            return a < b
        return self.text(col) < other.text(col)


class MainWindow(QMainWindow):
    settingsApplied = Signal()

    def __init__(self, settings, store, overlay, model=None, connection="", models=None,
                 parent=None):
        super().__init__(parent)
        self.settings = settings
        self.store = store
        self.models = models               # ModelStore (None: a fixed --model run)
        self._bg = BackgroundTask(self)
        self._release = None               # a newer release found by Check Now
        self.overlay = overlay
        self.model = model
        self.connection = connection
        self.hotkey = None                 # set by the app: GlobalHotkey
        self.tray = None                   # set by the app: Tray
        self.runner = None                 # set by the app: ScoringRunner
        self._live_game = 0

        self.setWindowTitle(f"{APP_NAME}")
        self.resize(640, 560)
        self.setMinimumSize(520, 440)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._live_tab(), "Live")
        self.tabs.addTab(self._history_tab(), "History")
        self.tabs.addTab(self._settings_tab(), "Settings")
        self.tabs.addTab(self._models_tab(), "Models")
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(6, 6, 6, 4)
        lay.addWidget(self.tabs)
        self.setCentralWidget(body)
        self._menus()
        self._status_bar()

        overlay.lockChanged.connect(self._overlay_lock_changed)
        self._overlay_lock_changed(overlay.locked)
        self.refresh_history()
        self._load_settings_into_form()
        self.update_age_note()

    # ================================================================== menus

    def _menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        a = m.addAction("Open &History Folder")
        a.triggered.connect(self.open_history_folder)
        a = m.addAction("&Import Model...")
        a.triggered.connect(self.import_model)
        a.setEnabled(self.models is not None)
        m.addSeparator()
        a = m.addAction("E&xit Wincast")
        a.triggered.connect(self._quit)

        m = mb.addMenu("&Options")
        self.act_on_top = m.addAction("&Always On Top")
        self.act_on_top.setCheckable(True)
        self.act_on_top.toggled.connect(self._always_on_top)
        self.act_move = m.addAction("&Move Overlay")
        self.act_move.setCheckable(True)
        self.act_move.toggled.connect(lambda on: self.overlay.set_locked(not on))
        m.addSeparator()
        self.act_open_on_start = m.addAction("&Open This Window At Startup")
        self.act_open_on_start.setCheckable(True)
        self.act_open_on_start.setChecked(prefs.get(self.settings, "general/open_window_on_start"))
        self.act_open_on_start.toggled.connect(
            lambda on: (prefs.put(self.settings, "general/open_window_on_start", on),
                        self.chk_open_on_start.setChecked(on)))

        m = mb.addMenu("&View")
        a = m.addAction("&Refresh History")
        a.setShortcut(QKeySequence("F5"))
        a.triggered.connect(self.refresh_history)

        m = mb.addMenu("&Help")
        a = m.addAction("Check for &Updates")
        a.triggered.connect(self._check_from_menu)
        a.setEnabled(self.models is not None)
        a = m.addAction("&Report a Problem...")
        a.triggered.connect(self.report_problem)
        a = m.addAction("Open &Log Folder")
        a.triggered.connect(lambda: self._open_folder(user_data_dir()))
        m.addSeparator()
        a = m.addAction("&About Wincast")
        a.triggered.connect(self._about)

    def _status_bar(self):
        sb = self.statusBar()
        sb.setSizeGripEnabled(True)
        self.sb_state = _sunken("Status: Waiting for a game")
        self.sb_chance = _sunken("Win chance: --")
        self.sb_games = _sunken("Games recorded: 0")
        self.sb_overlay = _sunken("Overlay: locked")
        for w, stretch in ((self.sb_state, 3), (self.sb_chance, 2), (self.sb_games, 2),
                           (self.sb_overlay, 2)):
            sb.addPermanentWidget(w, stretch)

    # ================================================================== live

    def _live_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        g1 = QGroupBox("Win chance")
        l1 = QVBoxLayout(g1)
        self.gauge = TMGauge()
        l1.addWidget(self.gauge)
        g2 = QGroupBox("Win chance history")
        l2 = QVBoxLayout(g2)
        self.live_graph = TMGraph()
        l2.addWidget(self.live_graph)
        top.addWidget(g1)
        top.addWidget(g2, 1)
        lay.addLayout(top, 1)

        g3 = QGroupBox("This game")
        grid = QGridLayout(g3)
        self.lv = {}
        left = [("status", "Status"), ("time", "Game time"), ("side", "Side"), ("champion", "Champion")]
        right = [("raw", "Model output"), ("model", "Model"), ("patch", "Model patch"),
                 ("conn", "Connection")]
        for row, (key, label) in enumerate(left):
            grid.addWidget(QLabel(label), row, 0)
            self.lv[key] = QLabel("--")
            grid.addWidget(self.lv[key], row, 1)
        for row, (key, label) in enumerate(right):
            grid.addWidget(QLabel(label), row, 2)
            self.lv[key] = QLabel("--")
            grid.addWidget(self.lv[key], row, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        lay.addWidget(g3)
        # "this Wincast is from last season", only then (see update_age_note)
        self.old_note = QLabel()
        self.old_note.setWordWrap(True)
        self.old_note.setTextFormat(Qt.TextFormat.RichText)
        self.old_note.setOpenExternalLinks(True)
        self.old_note.setStyleSheet("color: #a04000")
        self.old_note.hide()
        lay.addWidget(self.old_note)
        if self.model is not None:
            self.lv["model"].setText(self.model.name)
            self.lv["patch"].setText(str(self.model.patch))
        self.lv["conn"].setText(self.connection or "--")
        return w

    def show_update(self, up):
        """Slot for ScoringRunner.updated."""
        if up.game_id and up.game_id != self._live_game:
            self._live_game = up.game_id
            self.live_graph.clear()
            self.live_graph.team = up.team
        scoring = up.state in (IN_GAME, ENDED) and up.p_mine is not None
        if scoring and up.game_time is not None:
            self.live_graph.add_point(up.game_time, up.p_mine)
        self.gauge.set_value(up.p_mine if scoring else None)
        state = STATE_TEXT.get(up.state, up.state)
        if up.state == ENDED:
            state = RESULT_TEXT.get(up.result, "Game over")
        self.lv["status"].setText(state + (f"  ({up.detail})" if up.detail and up.state
                                           in (UNSUPPORTED, LOADING_STATE) else ""))
        self.lv["time"].setText(clock(up.game_time) if up.game_time is not None else "--")
        self.lv["side"].setText(SIDE_TEXT.get(up.team, "--") if up.game_id else "--")
        self.lv["champion"].setText(up.champion or "--")
        self.lv["raw"].setText(f"{up.p_mine_raw:.1%}" if scoring and up.p_mine_raw is not None else "--")
        if up.model_name:
            self.lv["model"].setText(up.model_name)
        self.sb_state.setText(f"Status: {state}")
        self.sb_chance.setText(f"Win chance: {up.p_mine:.0%}" if scoring else "Win chance: --")

    # ================================================================== history

    def _history_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.hist = QTreeWidget()
        self.hist.setHeaderLabels(["Date", "Champion", "Side", "Result", "Length",
                                   "Final", "Lowest", "Highest"])
        self.hist.setRootIsDecorated(False)
        self.hist.setUniformRowHeights(True)
        self.hist.setSortingEnabled(True)
        self.hist.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # Task Manager-style columns: compact, user-resizable; Champion takes the slack
        hdr = self.hist.header()
        hdr.setStretchLastSection(False)
        hdr.setMinimumSectionSize(36)
        fm = self.hist.fontMetrics()
        samples = ["2026-09-28  22:40", None, "Spectating", "Unfinished", "88:88",
                   "Final", "Lowest", "Highest"]
        for col, sample in enumerate(samples):
            if sample is None:
                hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
            else:
                hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
                self.hist.setColumnWidth(col, fm.horizontalAdvance(sample) + 22)
        self.hist.currentItemChanged.connect(self._history_selected)
        self.hist.setMinimumHeight(70)

        g = QGroupBox("Selected game")
        gl = QVBoxLayout(g)
        self.hist_graph = TMGraph(zoomable=True)
        self.hist_graph.min_span = 60
        gl.addWidget(self.hist_graph, 1)
        legend = QLabel('<span style="color:#1e90d0">&#9632;</span> your team&nbsp;&nbsp;&nbsp;'
                        '<span style="color:#d03030">&#9632;</span> enemy&nbsp;&nbsp;&nbsp;'
                        'D dragon &middot; B baron &middot; H herald &middot; G grubs &middot; '
                        'T tower &middot; I inhibitor &middot; ticks = kills')
        legend.setTextFormat(Qt.TextFormat.RichText)
        legend.setWordWrap(True)                   # own row, wraps on narrow windows
        gl.addWidget(legend)
        zoom_row = QHBoxLayout()
        hint = QLabel("Drag or scroll to zoom \u00b7 right-click to go back")
        hint.setEnabled(False)                     # greyed, like a hint
        zoom_row.addWidget(hint, 1)
        self.btn_zoom_back = QPushButton("&Back")
        self.btn_zoom_home = QPushButton("H&ome")
        for b in (self.btn_zoom_back, self.btn_zoom_home):
            b.setEnabled(False)
            zoom_row.addWidget(b)
        self.btn_zoom_back.clicked.connect(self.hist_graph.back)
        self.btn_zoom_home.clicked.connect(self.hist_graph.home)
        self.hist_graph.viewChanged.connect(self._zoom_changed)
        gl.addLayout(zoom_row)

        # list on top, graph below; the divider can be dragged and is remembered
        self.hist_split = QSplitter(Qt.Orientation.Vertical)
        self.hist_split.setChildrenCollapsible(False)
        self.hist_split.addWidget(self.hist)
        self.hist_split.addWidget(g)
        self.hist_split.setStretchFactor(0, 0)
        self.hist_split.setStretchFactor(1, 1)
        state = self.settings.value("window/history_split")
        if state is None or not self.hist_split.restoreState(state):
            self.hist_split.setSizes([150, 450])
        self.hist_split.splitterMoved.connect(
            lambda *_: self.settings.setValue("window/history_split", self.hist_split.saveState()))
        lay.addWidget(self.hist_split, 1)

        row = QHBoxLayout()
        self.hist_count = QLabel("")
        row.addWidget(self.hist_count, 1)
        self.btn_delete = QPushButton("&Delete")
        self.btn_delete.setEnabled(False)
        self.btn_delete.clicked.connect(self._delete_selected)
        btn_folder = QPushButton("Open &Folder")
        btn_folder.clicked.connect(self.open_history_folder)
        row.addWidget(self.btn_delete)
        row.addWidget(btn_folder)
        lay.addLayout(row)
        return w

    def refresh_history(self):
        entries = self.store.entries()
        current = self.hist.currentItem()
        keep = current.data(0, Qt.ItemDataRole.UserRole + 1) if current else None
        self.hist.setSortingEnabled(False)
        self.hist.clear()
        for e in entries:
            it = SortItem([
                local_time(e.get("started_utc")),
                e.get("champion") or "?",
                SIDE_TEXT.get(e.get("team"), "?"),
                RESULT_TEXT.get(e.get("result"), str(e.get("result"))),
                clock(e.get("duration_s")),
                f"{e['final']:.0%}" if e.get("final") is not None else "--",
                f"{e['low']:.0%}" if e.get("low") is not None else "--",
                f"{e['high']:.0%}" if e.get("high") is not None else "--",
            ])
            it.setData(0, Qt.ItemDataRole.UserRole, e.get("started_utc") or "")
            it.setData(0, Qt.ItemDataRole.UserRole + 1, str(e["path"]))
            it.setData(4, Qt.ItemDataRole.UserRole, float(e.get("duration_s") or 0))
            for col, key in ((5, "final"), (6, "low"), (7, "high")):
                it.setData(col, Qt.ItemDataRole.UserRole, float(e.get(key) or 0))
            for col in range(2, 8):
                it.setTextAlignment(col, Qt.AlignmentFlag.AlignCenter)
            self.hist.addTopLevelItem(it)
            if keep and str(e["path"]) == keep:
                self.hist.setCurrentItem(it)
        self.hist.setSortingEnabled(True)
        self.hist.sortByColumn(self.hist.sortColumn() if self.hist.header().isSortIndicatorShown()
                               else 0, self.hist.header().sortIndicatorOrder()
                               if self.hist.header().isSortIndicatorShown()
                               else Qt.SortOrder.DescendingOrder)
        n = len(entries)
        wins = sum(1 for e in entries if e.get("result") == "Win")
        losses = sum(1 for e in entries if e.get("result") == "Lose")
        self.hist_count.setText(f"{n} game{'s' if n != 1 else ''}  ·  {wins} won, {losses} lost"
                                if n else "No games yet. They are saved here as you play.")
        self.sb_games.setText(f"Games recorded: {n}")
        if self.hist.currentItem() is None and self.hist.topLevelItemCount():
            self.hist.setCurrentItem(self.hist.topLevelItem(0))     # newest game, graph showing
        if self.hist.currentItem() is None:
            self.hist_graph.clear()
            self.btn_delete.setEnabled(False)

    def _zoom_changed(self, zoomed):
        self.btn_zoom_home.setEnabled(zoomed)
        self.btn_zoom_back.setEnabled(bool(self.hist_graph._back))

    def _history_selected(self, item, _prev=None):
        self.btn_delete.setEnabled(item is not None)
        if item is None:
            self.hist_graph.clear()
            return
        try:
            rec = self.store.load(item.data(0, Qt.ItemDataRole.UserRole + 1))
        except (OSError, ValueError):
            self.hist_graph.clear()
            return
        self.hist_graph.set_game([(c[0], c[2]) for c in rec.get("curve", [])],
                                 rec.get("events", []), rec.get("team"))

    def _delete_selected(self):
        item = self.hist.currentItem()
        if item is None:
            return
        ok = QMessageBox.question(self, APP_NAME, "Delete this game from the history?",
                                  QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                  QMessageBox.StandardButton.No)
        if ok == QMessageBox.StandardButton.Yes:
            self.store.delete(item.data(0, Qt.ItemDataRole.UserRole + 1))
            self.refresh_history()

    def game_saved(self, _path=None):
        self.refresh_history()

    def open_history_folder(self):
        self._open_folder(self.store.folder)

    def _open_folder(self, folder):
        folder.mkdir(parents=True, exist_ok=True)
        path = str(explorer_path(folder))
        if sys.platform == "win32":
            os.startfile(path)                                     # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    # ================================================================== settings

    def _settings_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        g = QGroupBox("Overlay")
        f = QFormLayout(g)
        self.sp_scale = QSpinBox()
        self.sp_scale.setRange(60, 300)
        self.sp_scale.setSingleStep(10)
        self.sp_scale.setSuffix(" %")
        f.addRow("Size:", self.sp_scale)
        self.sp_opacity = QSpinBox()
        self.sp_opacity.setRange(30, 100)
        self.sp_opacity.setSingleStep(5)
        self.sp_opacity.setSuffix(" %")
        f.addRow("Opacity:", self.sp_opacity)
        self.chk_trend = QCheckBox("Show the trend line")
        f.addRow("", self.chk_trend)
        self.sp_trend = QSpinBox()
        self.sp_trend.setRange(1, 60)
        self.sp_trend.setSuffix(" min")
        f.addRow("Trend line length:", self.sp_trend)
        self.key_edit = QKeySequenceEdit()
        self.key_edit.setMaximumSequenceLength(1)
        f.addRow("Move/lock hotkey:", self.key_edit)
        self.hotkey_note = QLabel()                # shown only when the hotkey can't be used
        self.hotkey_note.setWordWrap(True)
        self.hotkey_note.setStyleSheet("color: #b00000")
        self.hotkey_note.hide()
        f.addRow("", self.hotkey_note)
        for box in (self.sp_scale, self.sp_opacity, self.sp_trend):
            box.setMaximumWidth(110)
        self.key_edit.setMaximumWidth(180)
        lay.addWidget(g)

        g = QGroupBox("Win chance")
        f = QFormLayout(g)
        self.sp_smooth = QDoubleSpinBox()
        self.sp_smooth.setRange(0.0, 30.0)
        self.sp_smooth.setSingleStep(1.0)
        self.sp_smooth.setDecimals(0)
        self.sp_smooth.setSuffix(" s")
        self.sp_smooth.setToolTip("How much the shown number is steadied, in game seconds. 0 = raw model output.")
        self.sp_smooth.setMaximumWidth(110)
        f.addRow("Smoothing:", self.sp_smooth)
        lay.addWidget(g)

        g = QGroupBox("General")
        v = QVBoxLayout(g)
        self.chk_autostart = QCheckBox("Start Wincast when I sign in to Windows")
        if not autostart.supported():
            self.chk_autostart.setEnabled(False)
            self.chk_autostart.setToolTip("Available in the installed Wincast.exe")
        v.addWidget(self.chk_autostart)
        self.chk_open_on_start = QCheckBox("Open this window when Wincast starts")
        v.addWidget(self.chk_open_on_start)
        self.chk_close_to_tray = QCheckBox("Keep running in the tray when I close this window")
        v.addWidget(self.chk_close_to_tray)
        lay.addWidget(g)

        lay.addStretch(1)

        row = QHBoxLayout()
        row.addStretch(1)
        btn_defaults = QPushButton("Restore &Defaults")
        btn_defaults.clicked.connect(self._restore_defaults)
        self.btn_apply = QPushButton("&Apply")
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self.apply_settings)
        row.addWidget(btn_defaults)
        row.addWidget(self.btn_apply)
        lay.addLayout(row)

        for sig in (self.sp_scale.valueChanged, self.sp_opacity.valueChanged,
                    self.chk_trend.toggled, self.sp_trend.valueChanged,
                    self.key_edit.keySequenceChanged, self.sp_smooth.valueChanged,
                    self.chk_autostart.toggled, self.chk_open_on_start.toggled,
                    self.chk_close_to_tray.toggled):
            sig.connect(lambda *_: self.btn_apply.setEnabled(True))
        self.chk_trend.toggled.connect(self.sp_trend.setEnabled)
        return w

    def _load_settings_into_form(self):
        s = self.settings
        self.sp_scale.setValue(round(prefs.get(s, "overlay/scale") * 100))
        self.sp_opacity.setValue(round(prefs.get(s, "overlay/opacity") * 100))
        self.chk_trend.setChecked(prefs.get(s, "overlay/trend"))
        self.sp_trend.setValue(prefs.get(s, "overlay/trend_minutes"))
        self.sp_trend.setEnabled(self.chk_trend.isChecked())
        self.key_edit.setKeySequence(QKeySequence(prefs.get(s, "hotkey")))
        self.sp_smooth.setValue(prefs.get(s, "smoothing_s"))
        self.chk_autostart.setChecked(autostart.enabled())
        self.chk_open_on_start.setChecked(prefs.get(s, "general/open_window_on_start"))
        self.chk_close_to_tray.setChecked(prefs.get(s, "general/close_to_tray"))
        self.btn_apply.setEnabled(False)

    def hotkey_text_from_form(self):
        seq = self.key_edit.keySequence()
        text = seq.toString(QKeySequence.SequenceFormat.PortableText) if not seq.isEmpty() else ""
        return text

    def apply_settings(self):
        s = self.settings
        text = self.hotkey_text_from_form()
        if text:
            try:
                parse_hotkey(text)
            except ValueError:
                QMessageBox.warning(self, APP_NAME, f"{text} can't be used as the hotkey.\n"
                                    "Use modifiers plus a letter, digit or F-key, e.g. Ctrl+Shift+P.")
                return
        prefs.put(s, "overlay/scale", self.sp_scale.value() / 100)
        prefs.put(s, "overlay/opacity", self.sp_opacity.value() / 100)
        prefs.put(s, "overlay/trend", self.chk_trend.isChecked())
        prefs.put(s, "overlay/trend_minutes", self.sp_trend.value())
        prefs.put(s, "smoothing_s", float(self.sp_smooth.value()))
        prefs.put(s, "general/open_window_on_start", self.chk_open_on_start.isChecked())
        prefs.put(s, "general/close_to_tray", self.chk_close_to_tray.isChecked())
        self.act_open_on_start.setChecked(self.chk_open_on_start.isChecked())
        if autostart.supported():
            autostart.set_enabled(self.chk_autostart.isChecked())
        if text and text != prefs.get(s, "hotkey"):
            prefs.put(s, "hotkey", text)
            self.overlay.hotkey_text = text
            if self.hotkey is not None:
                self.set_hotkey_status(self.hotkey.set_text(text) or sys.platform != "win32", text)
            if self.tray is not None:
                self.tray.set_hotkey_text(text)
        s.sync()
        self.overlay.apply_settings()
        if self.runner is not None:
            self.runner.set_smoothing(prefs.get(s, "smoothing_s"))
        self.btn_apply.setEnabled(False)
        self.settingsApplied.emit()

    def _restore_defaults(self):
        d = prefs.DEFAULTS
        self.sp_scale.setValue(round(d["overlay/scale"] * 100))
        self.sp_opacity.setValue(round(d["overlay/opacity"] * 100))
        self.chk_trend.setChecked(d["overlay/trend"])
        self.sp_trend.setValue(d["overlay/trend_minutes"])
        self.key_edit.setKeySequence(QKeySequence(d["hotkey"]))
        self.sp_smooth.setValue(d["smoothing_s"])
        self.chk_open_on_start.setChecked(d["general/open_window_on_start"])
        self.chk_close_to_tray.setChecked(d["general/close_to_tray"])
        self.btn_apply.setEnabled(True)

    def set_hotkey_status(self, ok: bool, text: str):
        """Say in the Settings tab (not a pop-up) when the hotkey couldn't be registered."""
        self.hotkey_note.setText("" if ok else
                                 f"{text} is taken by another app, so it does nothing right now. "
                                 "Pick another, or use the tray icon's menu to move the overlay.")
        self.hotkey_note.setVisible(not ok)

    # ================================================================== models

    def _models_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        g = QGroupBox("Installed models")
        gl = QVBoxLayout(g)
        self.model_list = QTreeWidget()
        self.model_list.setHeaderLabels(["In use", "Name", "Patch", "Trained on", "Accuracy",
                                         "Source"])
        self.model_list.setRootIsDecorated(False)
        self.model_list.setUniformRowHeights(True)
        hdr = self.model_list.header()
        hdr.setStretchLastSection(False)
        fm = self.model_list.fontMetrics()
        for col, sample in enumerate(["Next game", None, "88.88", "888,888 games", "88.8 %",
                                      "Downloaded"]):
            if sample is None:
                hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
            else:
                self.model_list.setColumnWidth(col, fm.horizontalAdvance(sample) + 22)
        self.model_list.currentItemChanged.connect(lambda *_: self._model_buttons())
        gl.addWidget(self.model_list, 1)
        note = QLabel("A new model is used from the next game; one is never swapped mid-game.")
        note.setEnabled(False)
        gl.addWidget(note)
        row = QHBoxLayout()
        self.btn_import = QPushButton("&Import...")
        self.btn_use = QPushButton("&Use Selected")
        self.btn_newest = QPushButton("Use &Newest")
        self.btn_remove = QPushButton("&Remove")
        self.btn_import.clicked.connect(self.import_model)
        self.btn_use.clicked.connect(self._pin_selected)
        self.btn_newest.clicked.connect(self._unpin)
        self.btn_remove.clicked.connect(self._remove_selected)
        row.addWidget(self.btn_import)
        row.addStretch(1)
        for b in (self.btn_use, self.btn_newest, self.btn_remove):
            row.addWidget(b)
        gl.addLayout(row)
        lay.addWidget(g, 1)

        g = QGroupBox("Updates")
        gl = QVBoxLayout(g)
        self.chk_auto_update = QCheckBox("Check for new models when Wincast starts, and install them")
        self.chk_auto_update.setChecked(prefs.get(self.settings, "models/auto_update"))
        self.chk_auto_update.toggled.connect(
            lambda on: prefs.put(self.settings, "models/auto_update", on))
        gl.addWidget(self.chk_auto_update)
        row = QHBoxLayout()
        self.update_status = QLabel("Wincast only contacts GitHub when you ask it to.")
        row.addWidget(self.update_status, 1)
        self.btn_check = QPushButton("&Check Now")
        self.btn_download = QPushButton("&Download")
        self.btn_download.setEnabled(False)
        self.btn_check.clicked.connect(self.check_for_models)
        self.btn_download.clicked.connect(self.download_model)
        row.addWidget(self.btn_check)
        row.addWidget(self.btn_download)
        gl.addLayout(row)
        lay.addWidget(g)

        if self.models is None:                   # a fixed --model run
            for b in (self.btn_import, self.btn_use, self.btn_newest, self.btn_remove,
                      self.btn_check, self.chk_auto_update):
                b.setEnabled(False)
            self.update_status.setText("Started with --model: that model is used for every game.")
        self.refresh_models()
        return w

    def refresh_models(self):
        self.model_list.clear()
        if self.models is None:
            if self.model is not None:
                it = QTreeWidgetItem(["Always", self.model.name, str(self.model.patch), "", "",
                                      "--model"])
                self.model_list.addTopLevelItem(it)
            return
        pinned = prefs.get(self.settings, "models/pinned")
        try:
            chosen = self.models.choose(pinned).path
        except Exception:
            chosen = None
        for m in self.models.models():
            in_use = ""
            if m.path == chosen:
                in_use = "Pinned" if pinned and m.name == pinned else "Next game"
            acc = f"{m.accuracy:.1%}" if m.accuracy else "--"
            games = f"{m.train_games:,} games" if m.train_games else "--"
            source = {"bundled": "Built in"}.get(m.source, "Downloaded")
            it = QTreeWidgetItem([in_use, m.name if m.ok else f"{m.name}  (can't use: {m.error})",
                                  m.patch or "--", games, acc, source])
            it.setData(0, Qt.ItemDataRole.UserRole, m)
            if not m.ok:
                it.setDisabled(True)
            if in_use:
                f = it.font(1)
                f.setBold(True)
                for c in range(6):
                    it.setFont(c, f)
            for c in (0, 2, 3, 4, 5):
                it.setTextAlignment(c, Qt.AlignmentFlag.AlignCenter)
            self.model_list.addTopLevelItem(it)
        self.btn_newest.setEnabled(bool(pinned))
        self._model_buttons()

    def _selected_model(self):
        it = self.model_list.currentItem()
        return it.data(0, Qt.ItemDataRole.UserRole) if it is not None else None

    def _model_buttons(self):
        if self.models is None:
            return
        m = self._selected_model()
        self.btn_use.setEnabled(bool(m and m.ok))
        self.btn_remove.setEnabled(bool(m and m.source != "bundled"))

    def import_model(self):
        if self.models is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import Model", "", "Wincast model (*.json)")
        if not path:
            return
        try:
            info = self.models.import_file(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return
        self.refresh_models()
        self.update_status.setText(f"Imported {info.name}.")

    def _pin_selected(self):
        m = self._selected_model()
        if m and m.ok:
            prefs.put(self.settings, "models/pinned", m.name)
            self.refresh_models()

    def _unpin(self):
        prefs.put(self.settings, "models/pinned", "")
        self.refresh_models()

    def _remove_selected(self):
        m = self._selected_model()
        if not m or m.source == "bundled":
            return
        ok = QMessageBox.question(self, APP_NAME, f"Remove {m.name}?",
                                  QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                  QMessageBox.StandardButton.No)
        if ok != QMessageBox.StandardButton.Yes:
            return
        self.models.remove(m)
        if prefs.get(self.settings, "models/pinned") == m.name:
            prefs.put(self.settings, "models/pinned", "")
        self.refresh_models()

    def check_for_models(self, install=False):
        """Check Now (and the startup check, with install=True)."""
        if self.models is None:
            return
        from .. import updates
        self.btn_check.setEnabled(False)
        self.btn_download.setEnabled(False)
        self.update_status.setText("Checking GitHub...")
        store = self.models

        def work():
            rel = updates.check()
            if install and updates.is_newer(rel, store):
                return rel, updates.download(rel, store)
            return rel, None

        self._bg.run(work, self._checked)

    def _checked(self, result, error):
        from .. import updates
        self.btn_check.setEnabled(True)
        if error:
            self.update_status.setText(f"Couldn't check: {error}")
            return
        rel, installed = result
        self.refresh_models()
        if installed is not None:
            self.update_status.setText(f"Installed {installed.name}; used from the next game.")
        elif rel is None:
            self.update_status.setText("No models published yet for this version of Wincast.")
        elif updates.is_newer(rel, self.models):
            self._release = rel
            self.btn_download.setEnabled(True)
            self.update_status.setText(f"A newer model is available: {rel.name} (patch {rel.patch}).")
        else:
            self.update_status.setText("You have the newest model.")

    def download_model(self):
        if self._release is None or self.models is None:
            return
        from .. import updates
        rel, store = self._release, self.models
        self.btn_download.setEnabled(False)
        self.btn_check.setEnabled(False)
        self.update_status.setText(f"Downloading {rel.name}...")
        self._bg.run(lambda: (rel, updates.download(rel, store)), self._checked)

    def update_age_note(self, version=None, today=None):
        """A note at the bottom of the Live tab once a new League season has started
        since this Wincast was built (16.x is the 2026 season). Offline: only the
        PC's clock and the version number. Source runs never show it."""
        from .. import updates
        version = version or __version__
        old = updates.app_is_old(version, today)
        if old:
            self.old_note.setText(
                f"This Wincast ({version}) is from the {updates.season_year(version)} season. "
                "A newer version may be out, and League changes each season can make an old one "
                f'less accurate or stop it working: <a href="{updates.RELEASES_PAGE}">check GitHub</a>.')
        self.old_note.setVisible(old)

    def _check_from_menu(self):
        self.tabs.setCurrentIndex(self.tabs.count() - 1)          # the Models tab
        self.check_for_models()

    def report_problem(self):
        import platform
        from .. import updates
        windows = f"{platform.release()} ({platform.version()})" if sys.platform == "win32" else sys.platform
        model = getattr(self.model, "name", "") or ""
        url = updates.issue_url(__version__, model, windows)
        QDesktopServices.openUrl(QUrl.fromEncoded(url.encode("ascii")))

    # ================================================================== misc

    def _overlay_lock_changed(self, locked):
        self.sb_overlay.setText("Overlay: locked" if locked else "Overlay: moving")
        self.act_move.blockSignals(True)
        self.act_move.setChecked(not locked)
        self.act_move.blockSignals(False)

    def _always_on_top(self, on):
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        self.show()

    def _about(self):
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<b>{APP_NAME}</b> {__version__}<br>"
                          "Live win chance for Summoner's Rift.<br><br>"
                          "Reads only the game's own Live Client Data API on this PC. "
                          "No Riot API key, nothing sent anywhere.<br><br>"
                          f'<a href="{REPO_URL}">{REPO_URL}</a><br><br>'
                          "<small>Wincast isn't endorsed by Riot Games and doesn't reflect the "
                          "views or opinions of Riot Games or anyone officially involved in "
                          "producing or managing Riot Games properties.</small>")

    def _quit(self):
        from PySide6.QtWidgets import QApplication
        QApplication.quit()

    def show_and_raise(self):
        self.update_age_note()                     # the app may have run since last season
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        has_tray = self.tray is not None and self.tray.icon is not None
        if has_tray and prefs.get(self.settings, "general/close_to_tray"):
            e.ignore()                             # just hide; the tray icon brings it back
            self.hide()
            return
        e.accept()
        from PySide6.QtWidgets import QApplication
        QApplication.quit()
