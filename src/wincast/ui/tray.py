"""A minimal tray icon: move/lock the overlay, quit. Phase 7 grows this."""

from __future__ import annotations

from PySide6.QtCore import QObject, QRectF, Qt, Slot
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ..session import ENDED, IN_GAME, LOADING_STATE, UNSUPPORTED


def app_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    qp = QPainter(pm)
    qp.setRenderHint(QPainter.RenderHint.Antialiasing)
    qp.setBrush(QColor("#10141b"))
    qp.setPen(QColor("#3ddc97"))
    qp.drawEllipse(QRectF(3, 3, 58, 58))
    f = QFont("Segoe UI")
    f.setBold(True)
    f.setPixelSize(34)
    qp.setFont(f)
    qp.setPen(QColor("#3ddc97"))
    qp.drawText(QRectF(0, 0, 64, 64), Qt.AlignmentFlag.AlignCenter, "W")
    qp.end()
    return QIcon(pm)


class Tray(QObject):
    def __init__(self, overlay, hotkey_text: str, hotkey_ok: bool, parent=None):
        super().__init__(parent)
        self.overlay = overlay
        self.hotkey_text = hotkey_text
        self.icon = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.menu = QMenu()
        self.lock_action = QAction(self.menu)
        self.lock_action.triggered.connect(overlay.toggle_lock)
        self.menu.addAction(self.lock_action)
        self.menu.addSeparator()
        quit_action = QAction("Quit Wincast", self.menu)
        quit_action.triggered.connect(QApplication.quit)
        self.menu.addAction(quit_action)
        overlay.lockChanged.connect(self._lock_text)
        self._lock_text(overlay.locked)

        self.icon = QSystemTrayIcon(app_icon(), self)
        self.icon.setContextMenu(self.menu)
        self.icon.setToolTip("Wincast: waiting for a game")
        self.icon.show()
        if not hotkey_ok:
            self.icon.showMessage("Wincast", f"{hotkey_text} isn't available, so use this "
                                  "tray icon to move or lock the overlay.",
                                  QSystemTrayIcon.MessageIcon.Information, 6000)

    @Slot(bool)
    def _lock_text(self, locked):
        self.lock_action.setText(f"Move overlay ({self.hotkey_text})" if locked
                                 else f"Lock overlay ({self.hotkey_text})")

    @Slot(object)
    def show_update(self, up):
        if self.icon is None:
            return
        if up.state == IN_GAME and up.p_mine is not None:
            tip = f"Wincast: {up.p_mine:.0%} win chance"
        elif up.state == ENDED:
            tip = f"Wincast: game over ({up.result})"
        elif up.state == LOADING_STATE:
            tip = "Wincast: game loading"
        elif up.state == UNSUPPORTED:
            tip = f"Wincast: not scoring ({up.detail})"
        else:
            tip = "Wincast: waiting for a game"
        self.icon.setToolTip(tip)
