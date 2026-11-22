"""The GUI is called SparkBox and shows its icon in the window, taskbar and dock; the engine keeps
the gregorithm name, and the preferences key keeps its former value so user settings survive the rename."""
import os
import struct
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from app.branding import APP_NAME, ENGINE_NAME, ICON_ICO, ICON_PNG, app_icon, apply_identity


class BrandingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_icon_files_are_square_and_readable(self):
        png = QImage(str(ICON_PNG))
        self.assertFalse(png.isNull())
        self.assertEqual((png.width(), png.height()), (1024, 1024))
        self.assertTrue(png.hasAlphaChannel())
        self.assertTrue(ICON_ICO.is_file())
        data = ICON_ICO.read_bytes()
        self.assertEqual(data[:4], b"\x00\x00\x01\x00")                            # ICO header
        count = struct.unpack_from("<H", data, 4)[0]
        entries = [struct.unpack_from("<BBBBHH", data, 6 + 16 * i) for i in range(count)]
        # 0 planes makes the Windows shell and taskbar show a generic icon for the executable (tools/make_icon.py)
        self.assertTrue(all(planes == 1 and bpp == 32 for *_size, planes, bpp in entries), entries)
        self.assertIn(16, [e[0] for e in entries])
        self.assertIn(0, [e[0] for e in entries])                                  # 0 means 256 px

    def test_identity_sets_name_and_icon(self):
        apply_identity(self.app)
        self.assertEqual(APP_NAME, "SparkBox")
        self.assertEqual(ENGINE_NAME, "gregorithm")
        self.assertEqual(self.app.applicationName(), "SparkBox")
        self.assertFalse(self.app.windowIcon().isNull())
        self.assertFalse(app_icon().pixmap(16, 16).isNull())                       # small taskbar size

    def test_main_window_uses_product_name_and_keeps_settings_key(self):
        from app.demo_backend import DemoBackend
        from app.main_window import MainWindow
        from app.theme import ThemeManager
        w = MainWindow(DemoBackend(), ThemeManager(self.app), demo=True)
        try:
            self.assertEqual(w.windowTitle(), "SparkBox")
            self.assertEqual(w.brand.text(), "SparkBox")
            self.assertEqual(w.act_about.text(), "About SparkBox")
            self.assertEqual(w.act_help.text(), "SparkBox Help")
            # tests store QSettings(org, app) as <tmp>/<org>/<app>.ini (conftest): the former key is still used
            self.assertEqual(Path(w.settings.fileName()).parts[-2:], ("gregorithm", "gui.ini"))
        finally:
            w.close()
            w.deleteLater()

    def test_about_names_author_ai_tools_and_shows_large_icon(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMainWindow
        from app.main_window import MainWindow
        host = QMainWindow()
        seen = {}

        def inspect_and_close():
            box = host._about_box
            seen.update(text=box.text(), title=box.windowTitle(), pixmap=box.iconPixmap())
            box.accept()
        QTimer.singleShot(0, inspect_and_close)
        MainWindow._about(host)                                  # only uses self as dialog parent
        if sys.platform != "darwin":                             # macOS: Qt ignores message box titles (Apple HIG)
            self.assertEqual(seen["title"], "About SparkBox")
        for part in ("Alessandro Gregucci", "Claude", "Codex", "gregorithm", "LICENSES"):
            self.assertIn(part, seen["text"])
        self.assertGreaterEqual(min(seen["pixmap"].width(), seen["pixmap"].height()), 128)
        host.deleteLater()


if __name__ == "__main__":
    unittest.main()
