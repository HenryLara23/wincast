"""One Wincast at a time.

A second launch asks the running copy to show its window, then exits. Two
copies would fight over the hotkey and save every game to the history twice.
Uses a local socket (a named pipe on Windows) named after the Windows user.
"""

from __future__ import annotations

import getpass

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:                                # no user name available: still one per machine
        user = "user"
    return f"Wincast-{user}"


class SingleInstance(QObject):
    activated = Signal()                             # another launch wants the window shown

    def __init__(self, name: str | None = None, parent=None):
        super().__init__(parent)
        self.name = name or server_name()
        self.server = None

    def already_running(self) -> bool:
        """True if another copy answered; it has been asked to show its window."""
        sock = QLocalSocket()
        sock.connectToServer(self.name)
        if not sock.waitForConnected(500):
            return False
        sock.write(b"show\n")
        sock.waitForBytesWritten(500)
        sock.disconnectFromServer()
        return True

    def listen(self) -> bool:
        self.server = QLocalServer(self)
        QLocalServer.removeServer(self.name)         # a stale socket file after a crash (not Windows)
        ok = self.server.listen(self.name)
        self.server.newConnection.connect(self._incoming)
        return ok

    def close(self):
        if self.server is not None:
            self.server.close()

    def _incoming(self):
        while self.server.hasPendingConnections():
            conn = self.server.nextPendingConnection()
            conn.disconnected.connect(conn.deleteLater)
            conn.disconnectFromServer()
            self.activated.emit()
