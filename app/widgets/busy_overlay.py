"""Modal "busy" overlay drawn over a window while a short blocking task runs on the GUI thread (e.g. re-styling the
whole app after a theme change). It dims the window, shows a message with an indeterminate progress bar and swallows
mouse and keyboard input, so the user sees that the app is working instead of a frozen window.

The overlay is opaque: it paints a dimmed snapshot of the window taken from the screen when it starts, so Qt never
repaints the (possibly expensive) widgets underneath while the task runs. Where no screen snapshot is available
(offscreen platform) it paints the plain dimmed window colour.

Usage: `overlay = BusyOverlay(window)`, then `overlay.start("Applying theme…")` / `overlay.stop()`. The caller must
let the event loop run (even briefly) after `start()` for the overlay to be painted. Limits: the progress bar only
animates while the event loop runs, so it pauses during a single long blocking call."""
from __future__ import annotations
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QProgressBar, QVBoxLayout, QWidget


class BusyOverlay(QWidget):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)  # hides, so never repaints, what is below
        self._snapshot = QPixmap()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.card = QFrame()
        self.card.setObjectName("Popover")
        self.card.setFixedWidth(300)
        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(20, 16, 20, 18)
        cl.setSpacing(10)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet("font-size: 10.5pt; font-weight: 500;")
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)                                  # indeterminate
        self.bar.setTextVisible(False)
        cl.addWidget(self.label)
        cl.addWidget(self.bar)
        lay.addWidget(self.card)
        parent.installEventFilter(self)
        self.hide()

    def start(self, text: str) -> None:
        """Cover the parent window and show `text`; input to the window is blocked until `stop()`."""
        self.label.setText(text)
        parent = self.parentWidget()
        self._snapshot = QPixmap()
        screen = parent.screen()
        if parent.isVisible() and screen is not None:
            shot = screen.grabWindow(parent.winId())             # read back what is on screen: no widget repaint
            if not shot.isNull() and shot.size() == parent.size() * shot.devicePixelRatio():
                self._snapshot = shot
        self.setGeometry(parent.rect())
        self.show()
        self.raise_()
        self.setFocus()

    def stop(self) -> None:
        self.hide()
        self._snapshot = QPixmap()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize and self.isVisible():
            self.setGeometry(self.parentWidget().rect())
        return False

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        if self._snapshot.isNull():
            p.fillRect(self.rect(), self.palette().window())
        else:
            p.drawPixmap(self.rect(), self._snapshot)
        p.fillRect(self.rect(), QColor(0, 0, 0, 70))

    # swallow input while visible
    def mousePressEvent(self, e):
        e.accept()

    def mouseReleaseEvent(self, e):
        e.accept()

    def mouseDoubleClickEvent(self, e):
        e.accept()

    def wheelEvent(self, e):
        e.accept()

    def keyPressEvent(self, e):
        e.accept()
