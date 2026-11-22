from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtWidgets import QApplication, QWidget

from app.demo_backend import DemoBackend
from app.fonts import load_fonts
from app.main_window import MainWindow
from app.theme import ThemeManager


class FontLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        load_fonts()

    def test_every_live_widget_has_a_positive_point_size_and_closes_cleanly(self):
        theme = ThemeManager(self.app)
        window = MainWindow(DemoBackend(0), theme, demo=True)
        theme.apply()
        window.show()
        self.app.processEvents()
        widgets = [window, *window.findChildren(QWidget)]
        invalid = [(type(widget).__name__, widget.objectName(), widget.font().pixelSize())
                   for widget in widgets if widget.font().pointSizeF() <= 0]
        self.assertEqual(invalid, [])
        window.close()
        self.app.processEvents()

    def test_style_hint_connection_does_not_emit_unique_connection_warning(self):
        messages: list[str] = []
        previous = qInstallMessageHandler(
            lambda _kind, _context, message: messages.append(message)
        )
        try:
            ThemeManager(self.app)
            self.app.processEvents()
        finally:
            qInstallMessageHandler(previous)

        self.assertFalse(any(
            "unique connections require a pointer to member function" in message
            for message in messages
        ), messages)


if __name__ == "__main__":
    unittest.main()
