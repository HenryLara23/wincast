"""The overlay: one small pill with your win chance, drawn over the game.

Locked (the normal state)
    frameless, always on top, no taskbar entry, never takes focus, and
    click-through: Qt's WindowTransparentForInput sets WS_EX_LAYERED |
    WS_EX_TRANSPARENT on Windows, so every click lands in the game.
    Shown only while a game is being scored or has just ended.
Unlocked (the hotkey, default Ctrl+Shift+P, or the tray menu)
    always shown, dashed outline, draggable, hint in the tooltip. The position is saved per screen.

It is a plain separate window: nothing hooks or injects into the game, which
is also why the game must run in Borderless or Windowed mode.
"""

from __future__ import annotations

import sys
from collections import deque

from PySide6.QtCore import QPoint, QRectF, QSettings, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ..session import ENDED, IN_GAME
from ..smoothing import logit

BASE_W, BASE_H = 150, 46
TREND_MINUTES_DEFAULT = 10.0          # how much of the game the trend line shows (setting)
TREND_EDGE = 0.99                     # the trend's vertical scale runs 1% .. 99%

LOSE = QColor("#ff5d5d")
EVEN = QColor("#c9ced8")
WIN = QColor("#3ddc97")
BG = QColor(14, 17, 23, 215)
TEXT_DIM = QColor(200, 206, 216, 170)


def trend_level(p: float) -> float:
    """0..1 height for the trend line, on a log-odds scale clamped at 1% / 99%.

    On a plain 0-100% scale everything above ~95% squeezes into the top pixel or
    two of a 24 px box and the line looks like it vanished. Log-odds (the scale
    the smoothing already uses) gives 95 -> 99% real height; 50% stays mid."""
    edge = logit(TREND_EDGE)
    z = min(max(logit(p), -edge), edge)
    return (z + edge) / (2 * edge)


def mix(a: QColor, b: QColor, f: float) -> QColor:
    f = min(max(f, 0.0), 1.0)
    return QColor(round(a.red() + (b.red() - a.red()) * f),
                  round(a.green() + (b.green() - a.green()) * f),
                  round(a.blue() + (b.blue() - a.blue()) * f))


def chance_color(p: float) -> QColor:
    """Red when losing, grey when even, green when winning; saturates at 20/80%."""
    if p >= 0.5:
        return mix(EVEN, WIN, (p - 0.5) / 0.3)
    return mix(EVEN, LOSE, (0.5 - p) / 0.3)


class OverlayWindow(QWidget):
    lockChanged = Signal(bool)

    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._locked = True
        self._update = None
        self._game_id = 0
        self._history = deque()           # (game_time, p_mine)
        self._drag_offset = None
        self.hotkey_text = "Ctrl+Shift+P"     # shown in the unlocked caption; app.py sets the real one

        self.setWindowTitle("Wincast")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._apply_flags()

        self.scale = min(max(float(settings.value("overlay/scale", 1.0)), 0.6), 3.0)
        self.opacity = min(max(float(settings.value("overlay/opacity", 0.95)), 0.3), 1.0)
        self.show_trend = str(settings.value("overlay/trend", "true")).lower() == "true"
        try:
            minutes = float(settings.value("overlay/trend_minutes", TREND_MINUTES_DEFAULT))
        except (TypeError, ValueError):
            minutes = TREND_MINUTES_DEFAULT
        self.trend_seconds = min(max(minutes, 1.0), 60.0) * 60.0
        self.setFixedSize(round(BASE_W * self.scale), round(BASE_H * self.scale))
        self.setWindowOpacity(self.opacity)

        self._save_timer = QTimer(self, singleShot=True, interval=400)
        self._save_timer.timeout.connect(self.save_position)
        self._restoring = True
        self.restore_position()
        self._restoring = False

        if sys.platform == "win32":        # borderless games sometimes push themselves above us
            self._top_timer = QTimer(self, interval=3000)
            self._top_timer.timeout.connect(self._reassert_topmost)
            self._top_timer.start()

    # ------------------------------------------------------------------ lock

    @property
    def locked(self) -> bool:
        return self._locked

    def _flags(self):
        f = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
             | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus
             | Qt.WindowType.NoDropShadowWindowHint)
        if self._locked:
            f |= Qt.WindowType.WindowTransparentForInput
        return f

    def _apply_flags(self):
        visible = self.isVisible()
        self.setWindowFlags(self._flags())  # re-creates the native window; it comes back hidden
        if visible:
            self.show()

    @Slot(bool)
    def set_locked(self, locked: bool):
        if locked == self._locked:
            return
        self._locked = locked
        pos = self.pos()
        self._apply_flags()
        self.move(pos)
        if locked:
            self.save_position()
        self.setToolTip("" if locked else f"Drag to move \u00b7 {self.hotkey_text} to lock")
        self.refresh_visibility()
        self.update()
        self.lockChanged.emit(locked)

    @Slot()
    def toggle_lock(self):
        self.set_locked(not self._locked)

    # ------------------------------------------------------------------ data

    def should_show(self) -> bool:
        if not self._locked:
            return True
        up = self._update
        return bool(up and up.state in (IN_GAME, ENDED) and up.p_mine is not None)

    def refresh_visibility(self):
        want = self.should_show()
        if want and not self.isVisible():
            self.show()
        elif not want and self.isVisible():
            self.hide()

    @Slot(object)
    def show_update(self, up):
        if up.game_id != self._game_id:
            self._game_id = up.game_id
            self._history.clear()
        if up.state in (IN_GAME, ENDED) and up.p_mine is not None and up.game_time is not None:
            if not self._history or up.game_time > self._history[-1][0]:
                self._history.append((up.game_time, up.p_mine))
            while self._history and self._history[0][0] < up.game_time - self.trend_seconds:
                self._history.popleft()
        self._update = up
        self.refresh_visibility()
        self.update()

    def display(self):
        """-> (big text, end-of-game caption or "", p or None). Kept apart from
        painting so it can be tested."""
        up = self._update
        scoring = up is not None and up.state in (IN_GAME, ENDED) and up.p_mine is not None
        p = up.p_mine if scoring else None
        big = f"{round(p * 100)}%" if p is not None else "--%"
        if up is not None and up.state == ENDED:
            caption = {"Win": "Victory", "Lose": "Defeat"}.get(up.result, "Game over")
        else:
            caption = ""                  # just the number and the trend while playing
        return big, caption, p

    # ------------------------------------------------------------------ painting

    def paintEvent(self, _event):
        s = self.scale
        big, caption, p = self.display()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)

        path = QPainterPath()
        path.addRoundedRect(r, 12 * s, 12 * s)
        qp.fillPath(path, BG)
        if not self._locked:
            pen = QPen(QColor(255, 255, 255, 170), 1.5 * s, Qt.PenStyle.DashLine)
            qp.setPen(pen)
            qp.drawPath(path)

        accent = chance_color(p) if p is not None else EVEN

        # the number
        f = QFont()
        f.setFamilies(["Segoe UI Semibold", "Segoe UI", "Inter", "Arial"])
        f.setPixelSize(round(30 * s))
        f.setBold(True)
        qp.setFont(f)
        qp.setPen(accent if p is not None else TEXT_DIM)
        qp.drawText(QRectF(10 * s, 0, 82 * s, r.height()),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, big)

        right = QRectF(94 * s, (r.height() - 24 * s) / 2, 46 * s, 24 * s)
        if caption:                               # game over: the result replaces the trend
            f2 = QFont(f)
            f2.setPixelSize(max(8, round(12 * s)))
            qp.setFont(f2)
            qp.setPen(accent)
            qp.drawText(right, Qt.AlignmentFlag.AlignCenter, caption)
        elif self.show_trend and p is not None and len(self._history) >= 2:
            plot = right.adjusted(0, 2 * s, 0, -2 * s)    # keep the pen off the edges
            mid = plot.top() + plot.height() / 2
            qp.setPen(QPen(QColor(255, 255, 255, 45), 1 * s, Qt.PenStyle.DotLine))
            qp.drawLine(plot.left(), mid, plot.right(), mid)
            t0, t1 = self._history[0][0], self._history[-1][0]
            span = max(t1 - t0, 1.0)
            line = QPainterPath()
            for i, (t, v) in enumerate(self._history):
                pt = (plot.left() + (t - t0) / span * plot.width(),
                      plot.bottom() - trend_level(v) * plot.height())
                line.moveTo(*pt) if i == 0 else line.lineTo(*pt)
            qp.setPen(QPen(accent, 1.6 * s))
            qp.drawPath(line)
        qp.end()

    # ------------------------------------------------------------------ moving

    def mousePressEvent(self, e):
        if self._locked or e.button() != Qt.MouseButton.LeftButton:
            return
        wh = self.windowHandle()
        if wh is not None and wh.startSystemMove():   # native drag: smooth, snaps to screens
            return
        self._drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_offset is not None and not self._locked:
            self.move(e.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, e):
        self._drag_offset = None

    def moveEvent(self, e):
        super().moveEvent(e)
        if not self._restoring:
            self._save_timer.start()

    # ------------------------------------------------------------------ position

    def save_position(self):
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        g = screen.geometry()
        self.settings.setValue("overlay/screen", screen.name())
        self.settings.setValue("overlay/x", self.x() - g.x())
        self.settings.setValue("overlay/y", self.y() - g.y())
        self.settings.sync()

    def has_saved_position(self) -> bool:
        return self.settings.contains("overlay/x")

    def restore_position(self):
        screens = QGuiApplication.screens()
        primary = QGuiApplication.primaryScreen()
        if not screens or primary is None:
            return
        name = self.settings.value("overlay/screen")
        screen = next((sc for sc in screens if sc.name() == name), primary)
        avail = screen.availableGeometry()
        if self.has_saved_position():
            pos = QPoint(screen.geometry().x() + int(self.settings.value("overlay/x")),
                         screen.geometry().y() + int(self.settings.value("overlay/y")))
        else:   # right edge, a little below the top: clear of the minimap and the HUD
            pos = QPoint(avail.right() - self.width() - 24,
                         avail.top() + round(avail.height() * 0.18))
        # never restore off-screen (a monitor unplugged, a resolution change)
        x = min(max(pos.x(), avail.left()), avail.right() - self.width())
        y = min(max(pos.y(), avail.top()), avail.bottom() - self.height())
        self.move(x, y)

    # ------------------------------------------------------------------ windows

    def _reassert_topmost(self):
        if not self.isVisible():
            return
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                            wintypes.UINT]
            SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_NOOWNERZORDER = 0x1, 0x2, 0x10, 0x200
            user32.SetWindowPos(wintypes.HWND(int(self.winId())), wintypes.HWND(-1),  # HWND_TOPMOST
                                0, 0, 0, 0,
                                SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_NOOWNERZORDER)
        except Exception:
            pass
