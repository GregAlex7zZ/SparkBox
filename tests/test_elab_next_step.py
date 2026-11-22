"""Regression for the Data Elaboration summary (2026-11-08): the highlighted next step is "Go to Data Explorer", while
"View Elaborated Data" is a discreet link next to Processing Details that still opens the elaborated table."""
import os
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from app.demo_backend import DemoBackend
from app.main_window import MainWindow
from app.state import Section
from app.theme import ThemeManager


def pump(ms: int = 30) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_for(cond, timeout: float = 20.0) -> None:
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            raise AssertionError("condition not met in time")
        pump(20)


class ElabNextStep(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.theme = ThemeManager(cls.app)
        cls.theme.apply(force=True)
        cls.w = MainWindow(DemoBackend(0.0), cls.theme, demo=True)
        cls.w.show()
        cls.w.start_import("a_cell.csv", "auto")
        wait_for(lambda: cls.w._active_job is None)
        cls.ds = cls.w.state.datasets[0]
        cls.w.elab.process(cls.ds.id)
        wait_for(lambda: cls.ds.elab.has_result and not cls.ds.elab.is_running)
        pump(100)

    @classmethod
    def tearDownClass(cls):
        cls.w.close()
        pump()

    def setUp(self):
        self.w.go(Section.ELABORATION)
        self.page = self.w.elab_page
        self.page.show_dataset(self.ds.id)
        pump()

    def test_go_to_explorer_is_the_highlighted_button(self):
        a, b = self.page.btn_a, self.page.btn_b
        self.assertEqual(a.text(), "Go to Data Explorer")
        self.assertTrue(a.property("primary"))
        self.assertFalse(a.property("run"))
        self.assertEqual(b.text(), "Reprocess")
        self.assertTrue(b.property("secondary"))
        self.assertNotIn("View Elaborated Data", (a.text(), b.text()))

    def test_view_elaborated_data_is_a_quiet_link(self):
        link = self.page.view_link
        self.assertTrue(link.isVisible())
        self.assertTrue(link.property("quiet"))
        self.assertFalse(link.property("primary"))
        self.assertEqual(link.parent(), self.page.details_btn.parent())

    def test_buttons_lead_to_explorer_and_table(self):
        self.page.btn_a.click()
        pump()
        self.assertIs(self.w.current, Section.EXPLORER)
        self.w.go(Section.ELABORATION)
        pump()
        self.page.view_link.click()
        pump()
        self.assertEqual(self.page._mode.get(self.ds.id), "table")
        self.page._mode[self.ds.id] = "summary"


if __name__ == "__main__":
    unittest.main()
