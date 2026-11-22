"""PyQtGraph compatibility shim: silence the QStyleHints unique-connection warning.

pyqtgraph 0.13.x calls ``mkQApp()`` every time a ``GraphicsLayoutWidget`` is built. That helper connects
``QStyleHints.colorSchemeChanged`` to a plain Python function with ``Qt.UniqueConnection``; PySide6 cannot honour a
unique connection for a non-member slot, so Qt prints "QObject::connect(QStyleHints, QStyleHints): unique connections
require a pointer to member function of a QObject subclass" once per chart.

``install()`` replaces the ``mkQApp`` reference held by every loaded pyqtgraph module with a quiet equivalent that
returns the existing application and keeps pyqtgraph's ``darkMode`` property up to date through a single ordinary
connection. Limits: it only affects modules already imported when it runs (call it right after ``import pyqtgraph``),
and it requires a QApplication to exist before the first chart is created, which is always true in this GUI.
"""
from __future__ import annotations
import sys
import pyqtgraph as pg
import pyqtgraph.Qt as pg_qt
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

_original = pg_qt.mkQApp
_connected = False


def _quiet_mkqapp(name=None):
    """Drop-in for ``pyqtgraph.mkQApp`` that never makes a unique connection to a free function."""
    global _connected
    app = QApplication.instance()
    if app is None:                                   # no app yet: let pyqtgraph create it as usual
        return _original(name)
    hints = app.styleHints()
    if hasattr(hints, "colorScheme"):
        app.setProperty("darkMode", hints.colorScheme() == Qt.ColorScheme.Dark)
        if not _connected:
            hints.colorSchemeChanged.connect(pg_qt._onColorSchemeChange)
            _connected = True
    if name is not None:
        app.setApplicationName(name)
    return app


def install() -> None:
    """Point every loaded pyqtgraph module's ``mkQApp`` at the quiet version (idempotent)."""
    for mod_name, mod in list(sys.modules.items()):
        if mod_name.startswith("pyqtgraph") and getattr(mod, "mkQApp", None) is _original:
            mod.mkQApp = _quiet_mkqapp
    pg.mkQApp = _quiet_mkqapp
