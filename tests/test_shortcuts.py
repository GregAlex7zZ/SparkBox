"""Regression (2026-11-08): the only Ctrl shortcut of the application is Ctrl+O (open a file with Auto Mapping).

Ctrl+Shift+O, Ctrl+, , Ctrl+B, Ctrl+Q, Ctrl+S (Save Image) and the Help window's Ctrl+F / Ctrl+W were removed on the
author's request. Copying table cells with the system Copy key stays: it is the platform's text editing, not an
application shortcut. F1 (Help) and the DCIR Review keys (A, R, Up, Down) have no Ctrl and stay too.
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication

from app.demo_backend import DemoBackend
from app.main_window import MainWindow
from app.theme import ThemeManager


def ctrl_keys(objects) -> list[str]:
    """Shortcuts of ``objects`` (QAction / QShortcut) that use Ctrl, except the system Copy key."""
    copy = QKeySequence(QKeySequence.StandardKey.Copy)
    found = []
    for obj in objects:
        seqs = obj.shortcuts() if isinstance(obj, QAction) else [obj.key()]
        for seq in seqs:
            if seq.isEmpty() or seq == copy:
                continue
            if seq[0].keyboardModifiers() & Qt.KeyboardModifier.ControlModifier:
                found.append(seq.toString())
    return found


class OnlyOpenFileShortcut(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.theme = ThemeManager(cls.app)
        cls.w = MainWindow(DemoBackend(0.0), cls.theme, demo=True)

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def test_main_window_has_only_ctrl_o(self):
        actions = self.w.findChildren(QAction) + self.w.actions()
        self.assertEqual(sorted(set(ctrl_keys(actions))), ["Ctrl+O"])
        self.assertEqual(self.w.act_auto.shortcut().toString(), "Ctrl+O")
        self.assertEqual(self.w.act_help.shortcut().toString(), "F1")

    def test_pages_have_no_ctrl_shortcuts(self):
        self.assertEqual(ctrl_keys(self.w.findChildren(QShortcut)), [])

    def test_help_window_has_no_ctrl_shortcuts(self):
        from app.widgets.help_window import HelpWindow
        h = HelpWindow(self.theme)
        self.addCleanup(h.deleteLater)
        self.assertEqual(ctrl_keys(h.findChildren(QShortcut)), [])


if __name__ == "__main__":
    unittest.main()
