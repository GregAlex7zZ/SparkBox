"""Activation channel of the single running SparkBox (the lock itself is in app.startup).

The running instance listens on a per-user local channel (``QLocalServer``: a named pipe on Windows, a local
socket elsewhere) and emits ``ActivationServer.activated`` when a second start sends ``activate``; the main
window then restores itself and comes to the front. The Windows launcher sends the same message when the user
clicks the executable again.

Usage::

    server = ActivationServer(channel_name())        # in the first instance, after QApplication exists
    server.activated.connect(window.bring_to_front)
    ...
    send_activate(channel_name())                     # in a second start (another process); True when reached

Limits: a first instance that is still loading has not opened the channel yet; ``send_activate_with_retry``
keeps trying for a few seconds and then gives up, so the second start still exits without opening a window.
Sender and receiver must be different processes (in one thread the blocking send cannot be served).
"""
from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

ACTIVATE = b"activate"


class ActivationServer(QObject):
    """Listens for ``activate`` messages from later starts; emits ``activated`` for each one."""

    activated = Signal()

    def __init__(self, name: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._open: set[_Connection] = set()   # PySide keeps no strong reference to connected Python methods
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        QLocalServer.removeServer(name)        # a socket file left by a crashed process (Unix)
        self.listening = self._server.listen(name)
        self._server.newConnection.connect(self._on_connection)

    def _on_connection(self) -> None:
        while self._server.hasPendingConnections():
            connection = _Connection(self._server.nextPendingConnection(), self.activated.emit, self._open.discard)
            if not connection.closed:
                self._open.add(connection)

    def close(self) -> None:
        """Stop listening (also done automatically when the process ends)."""
        self._server.close()


class _Connection:
    """One incoming connection: accumulates bytes and calls ``on_activate`` once when ``activate`` has arrived.

    The sender writes and disconnects at once, so data may already be buffered when the connection is accepted:
    it is read immediately and again on disconnection. The server holds the object until ``on_close``."""

    def __init__(self, socket: QLocalSocket, on_activate: Callable[[], None],
                 on_close: Callable[[_Connection], None]) -> None:
        self.socket, self.on_activate, self.on_close = socket, on_activate, on_close
        self.data, self.done, self.closed = b"", False, False
        socket.readyRead.connect(self.read)
        socket.disconnected.connect(self.finish)
        self.read()
        if socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self.finish()

    def read(self) -> None:
        if self.closed:
            return
        self.data += bytes(self.socket.readAll())
        if ACTIVATE in self.data and not self.done:
            self.done = True
            self.on_activate()

    def finish(self) -> None:
        if self.closed:
            return
        self.read()
        self.closed = True
        self.socket.deleteLater()
        self.on_close(self)


def send_activate(name: str, timeout_ms: int = 300) -> bool:
    """Ask the running instance to bring its window to the front; True when it was reached."""
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(timeout_ms):
        return False
    socket.write(ACTIVATE)
    socket.flush()
    socket.waitForBytesWritten(timeout_ms)
    socket.disconnectFromServer()
    return True


def send_activate_with_retry(name: str, wait_s: float = 5.0) -> bool:
    """``send_activate`` repeated until the first instance answers or ``wait_s`` seconds have passed."""
    deadline = time.monotonic() + wait_s
    while True:
        if send_activate(name):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)
