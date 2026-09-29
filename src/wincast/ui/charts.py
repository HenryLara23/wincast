"""Task Manager (Windows 7 Performance tab) style drawing: green on a black grid.

TMGraph   the win-chance curve, with objective markers and a hover readout
TMGauge   the segmented "CPU Usage"-style meter for the current win chance
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

BLACK = QColor("#000000")
GRID = QColor("#008040")
GRID_MID = QColor("#00a050")
LINE = QColor("#00ff00")
FILL = QColor(0, 255, 0, 55)
LINE_LOW = QColor("#ff3b3b")     # below 50 %: losing
FILL_LOW = QColor(255, 40, 40, 60)
TEXT = QColor("#00c040")
OFF = QColor("#004020")
MINE = QColor("#28b8ff")       # your team's objectives
THEIRS = QColor("#ff4a4a")     # the enemy's

LETTER = {"Dragon": "D", "Baron": "B", "Herald": "H", "Grubs": "G",
          "Tower": "T", "Inhibitor": "I"}


def _font(px, bold=False):
    f = QFont()
    f.setFamilies(["Segoe UI", "Tahoma", "Arial"])
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def clock(t):
    t = max(0, int(t or 0))
    return f"{t // 60}:{t % 60:02d}"


class TMGraph(QWidget):
    """Win chance (0..1, your side) against game time.

    With `zoomable` (the History graph), like a matplotlib toolbar without the toolbar:
      drag            zoom to the dragged time range
      mouse wheel     zoom in/out around the cursor
      right-click     back to the previous zoom
      double-click    home (the whole game)
    Only time zooms; the 0-100 % scale stays put so heights stay comparable."""

    viewChanged = Signal(bool)          # True while zoomed in

    MIN_VIEW_S = 20.0

    def __init__(self, parent=None, zoomable=False):
        super().__init__(parent)
        self.points = []            # [(t, p)]
        self.events = []            # [{t, kind, team}]
        self.team = None
        self.min_span = 300.0       # a live game starts with a 5-minute-wide canvas
        self.zoomable = zoomable
        self.view = None            # (t0, t1) while zoomed, else None = whole game
        self._back = []             # previous views, for right-click
        self._drag = None           # (x_start, x_now) while dragging
        self.setMinimumSize(240, 120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

    def set_game(self, points, events=(), team=None):
        self.points = list(points)
        self.events = list(events)
        self.team = team
        self.home()

    def add_point(self, t, p):
        if not self.points or t > self.points[-1][0]:
            self.points.append((t, p))
            self.update()

    def clear(self):
        self.set_game([], [], None)

    # zoom ----------------------------------------------------------------

    def full_range(self):
        end = self.points[-1][0] if self.points else 0.0
        return 0.0, max(end, self.min_span, 60.0)

    def range(self):
        return self.view or self.full_range()

    def set_view(self, t0, t1, remember=True):
        lo, hi = self.full_range()
        t0, t1 = max(lo, min(t0, t1)), min(hi, max(t0, t1))
        if t1 - t0 < self.MIN_VIEW_S:
            mid = (t0 + t1) / 2
            t0, t1 = max(lo, mid - self.MIN_VIEW_S / 2), min(hi, mid + self.MIN_VIEW_S / 2)
        if remember:
            self._back.append(self.view)
        self.view = None if (t0 <= lo and t1 >= hi) else (t0, t1)
        self.viewChanged.emit(self.view is not None)
        self.update()

    def home(self):
        self.view = None
        self._back.clear()
        self.viewChanged.emit(False)
        self.update()

    def back(self):
        if self._back:
            self.view = self._back.pop()
            self.viewChanged.emit(self.view is not None)
            self.update()

    # geometry ------------------------------------------------------------

    def _plot(self):
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        return r.adjusted(38, 20, -8, -18)          # room for % labels, markers, minutes

    def _x(self, plot, t):
        t0, t1 = self.range()
        return plot.left() + (t - t0) / (t1 - t0) * plot.width()

    def _t(self, plot, x):
        t0, t1 = self.range()
        return t0 + (x - plot.left()) / plot.width() * (t1 - t0)

    def _xy(self, plot, t, p):
        return QPointF(self._x(plot, t), plot.bottom() - p * plot.height())

    # painting ------------------------------------------------------------

    def paintEvent(self, _e):
        qp = QPainter(self)
        qp.fillRect(self.rect(), BLACK)
        plot = self._plot()
        t0, t1 = self.range()
        px_per_s = plot.width() / (t1 - t0)

        # grid: every 10 %, and a time step that keeps lines >= ~28 px apart
        qp.setFont(_font(10))
        for i in range(11):
            y = plot.bottom() - i / 10 * plot.height()
            qp.setPen(QPen(GRID_MID if i == 5 else GRID, 1))
            qp.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        for i, label in ((10, "100%"), (5, "50%"), (0, "0%")):
            y = plot.bottom() - i / 10 * plot.height()
            qp.setPen(TEXT)
            qp.drawText(QRectF(0, y - 7, 34, 14), Qt.AlignmentFlag.AlignRight
                        | Qt.AlignmentFlag.AlignVCenter, label)
        step = next((st for st in (10, 15, 30, 60, 120, 300, 600, 900) if st * px_per_s >= 28), 900)
        label_every = 1 if step * px_per_s >= 44 else 2
        m = int(t0 // step) * step
        if m < t0:
            m += step
        while m <= t1 + 1e-6:
            x = self._x(plot, m)
            qp.setPen(QPen(GRID, 1))
            qp.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            if (m // step) % label_every == 0:
                qp.setPen(TEXT)
                label = f"{int(m) // 60}m" if step >= 60 else clock(m)
                qp.drawText(QRectF(x - 22, plot.bottom() + 2, 44, 14),
                            Qt.AlignmentFlag.AlignHCenter, label)
            m += step
        qp.setPen(QPen(GRID, 1))
        qp.drawRect(plot)

        # objective markers (letters along the top) and kills (ticks at the bottom)
        tick = max(8.0, plot.height() * 0.04)
        qp.setFont(_font(12, bold=True))
        for e in self.events:
            if e.get("team") is None or not (t0 <= e["t"] <= t1):
                continue
            mine = e["team"] == self.team or (self.team is None and e["team"] == "ORDER")
            col = MINE if mine else THEIRS
            x = self._x(plot, e["t"])
            if e["kind"] == "Kill":
                qp.setPen(QPen(col, 2))
                qp.drawLine(QPointF(x, plot.bottom() - tick), QPointF(x, plot.bottom()))
            elif e["kind"] in LETTER:
                faint = QColor(col)
                faint.setAlpha(90)
                qp.setPen(QPen(faint, 1, Qt.PenStyle.DotLine))
                qp.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
                qp.setPen(col)
                qp.drawText(QRectF(x - 8, 1, 16, 18), Qt.AlignmentFlag.AlignCenter,
                            LETTER[e["kind"]])

        # the curve: filled toward the 50 % line, green above it, red below it
        if len(self.points) >= 2:
            qp.setRenderHint(QPainter.RenderHint.Antialiasing)
            pts = self.points
            if self.view:                         # only what's on screen, plus one each side
                lo = max(0, bisect_left(pts, (t0, -1.0)) - 1)
                hi = min(len(pts), bisect_right(pts, (t1, 2.0)) + 1)
                pts = pts[lo:hi]
            line = QPainterPath()
            for i, (t, p) in enumerate(pts):
                pt = self._xy(plot, t, p)
                line.moveTo(pt) if i == 0 else line.lineTo(pt)
            if len(pts) >= 2:
                area = QPainterPath(line)
                area.lineTo(self._xy(plot, pts[-1][0], 0.5))
                area.lineTo(self._xy(plot, pts[0][0], 0.5))
                area.closeSubpath()
                mid = plot.top() + plot.height() / 2
                upper = QRectF(plot.left(), plot.top() - 2, plot.width(), mid - plot.top() + 2)
                lower = QRectF(plot.left(), mid, plot.width(), plot.bottom() - mid + 2)
                for half, fill, col in ((upper, FILL, LINE), (lower, FILL_LOW, LINE_LOW)):
                    qp.save()
                    qp.setClipRect(half)
                    qp.fillPath(area, fill)
                    qp.setPen(QPen(col, 1.5))
                    qp.drawPath(line)
                    qp.restore()
        elif not self.points:
            qp.setPen(TEXT)
            qp.setFont(_font(11))
            qp.drawText(plot, Qt.AlignmentFlag.AlignCenter, "no game")

        # the drag selection
        if self._drag is not None:
            a, b = sorted(self._drag)
            sel = QRectF(max(a, plot.left()), plot.top(), min(b, plot.right()) - max(a, plot.left()),
                         plot.height())
            qp.fillRect(sel, QColor(255, 255, 255, 40))
            qp.setPen(QPen(QColor(255, 255, 255, 160), 1, Qt.PenStyle.DashLine))
            qp.drawRect(sel)
        qp.end()

    # mouse ---------------------------------------------------------------

    def mousePressEvent(self, e):
        if not self.zoomable or not self.points:
            return
        if e.button() == Qt.MouseButton.LeftButton:
            x = e.position().x()
            self._drag = (x, x)
        elif e.button() == Qt.MouseButton.RightButton:
            self.back()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self._drag = (self._drag[0], e.position().x())
            self.update()
            return
        if not self.points:
            return
        plot = self._plot()
        t = self._t(plot, e.position().x())
        near = min(self.points, key=lambda tp: abs(tp[0] - t))
        QToolTip.showText(e.globalPosition().toPoint(), f"{clock(near[0])}   {near[1]:.0%}", self)

    def mouseReleaseEvent(self, e):
        if self._drag is None or e.button() != Qt.MouseButton.LeftButton:
            return
        a, b = sorted(self._drag)
        self._drag = None
        if b - a >= 6:                               # a real drag, not a click
            plot = self._plot()
            self.set_view(self._t(plot, a), self._t(plot, b))
        self.update()

    def mouseDoubleClickEvent(self, e):
        if self.zoomable and e.button() == Qt.MouseButton.LeftButton:
            self._drag = None
            self.home()

    def wheelEvent(self, e):
        if not self.zoomable or not self.points:
            return
        steps = e.angleDelta().y() / 120
        if not steps:
            return
        plot = self._plot()
        t0, t1 = self.range()
        at = min(max(self._t(plot, e.position().x()), t0), t1)
        f = 0.8 ** steps                             # wheel up = zoom in
        self.set_view(at - (at - t0) * f, at + (t1 - at) * f, remember=False)


class TMGauge(QWidget):
    """Segmented vertical meter plus the number under it."""

    SEGMENTS = 25

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = None
        self.setMinimumSize(84, 120)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def sizeHint(self):
        return QSize(96, 150)

    def set_value(self, p):
        self.value = p
        self.update()

    def paintEvent(self, _e):
        qp = QPainter(self)
        qp.fillRect(self.rect(), BLACK)
        r = QRectF(self.rect())
        text_h = 22
        bar = QRectF(r.center().x() - 22, 8, 44, r.height() - 16 - text_h)
        lit = 0 if self.value is None else round(self.value * self.SEGMENTS)
        seg_h = bar.height() / self.SEGMENTS
        for i in range(self.SEGMENTS):
            y = bar.bottom() - (i + 1) * seg_h
            col = LINE if i < lit else OFF
            for half in (0, 1):                     # two columns, like the Win7 meter
                qp.fillRect(QRectF(bar.left() + half * 23, y + 1, 21, max(seg_h - 2, 1)), col)
        qp.setPen(LINE if self.value is not None else TEXT)
        qp.setFont(_font(14, bold=True))
        txt = "--" if self.value is None else f"{round(self.value * 100)} %"
        qp.drawText(QRectF(0, r.height() - text_h - 4, r.width(), text_h),
                    Qt.AlignmentFlag.AlignCenter, txt)
        qp.end()
