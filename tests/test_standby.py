"""Regressions for the standby of the datasets that are not shown (2026-11-22): with two large datasets open, work
left running on the other dataset made the interface stutter. Only the selected dataset's calculations may run (the
others pause at their next progress report and resume when selected again); cancellation and shutdown always wake a
paused worker; the pages of the sections not on screen are parked and load the active dataset only when opened."""
import os
import sys
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gregorithm
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from app.standby import STANDBY, Standby


def pump(ms: int = 30) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_for(cond, timeout: float = 20.0) -> bool:
    end = time.time() + timeout
    while not cond() and time.time() < end:
        pump()
    return cond()


class Gate(unittest.TestCase):
    def _worker(self, gate: Standby, ds_id: str, token=None):
        reports: list = []
        thread = threading.Thread(target=lambda: gate.paced(ds_id, reports.append, token)("p"), daemon=True)
        thread.start()
        return thread, reports

    def test_only_the_selected_dataset_runs(self):
        gate = Standby()
        self.assertTrue(gate.runs("a") and gate.runs("b"))            # nothing selected: nothing paused
        gate.set_active("a")
        self.assertTrue(gate.runs("a"))
        self.assertFalse(gate.runs("b"))
        gate.set_active("a", everyone=True)                           # Comparison: every dataset runs
        self.assertTrue(gate.runs("b"))

    def test_a_paused_worker_resumes_when_its_dataset_is_selected(self):
        gate = Standby()
        gate.set_active("a")
        thread, reports = self._worker(gate, "b")
        time.sleep(0.4)
        self.assertEqual(reports, [])                                 # standby: the report waits
        self.assertTrue(thread.is_alive())
        gate.set_active("b")
        thread.join(2)
        self.assertEqual(reports, ["p"])

    def test_cancellation_and_shutdown_wake_a_paused_worker(self):
        gate = Standby()
        gate.set_active("a")
        token = gregorithm.CancellationToken()
        thread, _ = self._worker(gate, "b", token)
        token.cancel()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        event = threading.Event()
        thread, _ = self._worker(gate, "b", event)
        event.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        thread, reports = self._worker(gate, "b")
        gate.open_all()
        thread.join(2)
        self.assertEqual(reports, ["p"])
        gate.set_active("a")
        self.assertTrue(gate.runs("b"))                               # shut down: never pauses again


class Window(unittest.TestCase):
    """Two demo datasets in one main window."""

    @classmethod
    def setUpClass(cls):
        from app.demo_backend import DemoBackend
        from app.main_window import MainWindow
        from app.theme import ThemeManager
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setOrganizationName("gregorithm-test")
        cls.w = MainWindow(DemoBackend(0.0), ThemeManager(cls.app), demo=True)
        cls.w.show()
        for name in ("a_cell.csv", "b_cell.csv"):
            cls.w.start_import(name, "auto")
            wait_for(lambda: cls.w._active_job is None)
        for ds in cls.w.state.datasets:
            cls.w.select_dataset(ds.id)                  # only the selected dataset calculates (app.standby)
            cls.w.elab.process(ds.id)
            wait_for(lambda d=ds: d.elab.has_result)

    @classmethod
    def tearDownClass(cls):
        cls.w.close()
        pump()
        STANDBY.reset()

    def test_the_selected_dataset_runs_and_the_other_waits(self):
        from app.state import Section
        a, b = (d.id for d in self.w.state.datasets)
        self.w.select_dataset(a)
        self.assertTrue(STANDBY.runs(a))
        self.assertFalse(STANDBY.runs(b))
        self.w.select_dataset(b)
        self.assertTrue(STANDBY.runs(b) and not STANDBY.runs(a))
        self.assertTrue(self.w.state.availability()[Section.COMPARISON])
        self.w.go(Section.COMPARISON)
        self.assertTrue(STANDBY.runs(a) and STANDBY.runs(b))         # Comparison overlays every dataset
        self.w.select_dataset(b)

    def test_hidden_sections_are_parked_and_load_when_opened(self):
        from app.state import Section
        a, b = (d.id for d in self.w.state.datasets)
        self.w.select_dataset(a)
        self.w.go(Section.CAPACITY)
        self.w.go(Section.ELABORATION)
        self.assertEqual(self.w.capacity_page.ds_id, a)               # same dataset: kept as it was
        self.w.select_dataset(b)
        self.assertEqual(self.w.elab_page.ds_id, b)                   # the section on screen follows at once
        self.assertIsNone(self.w.capacity_page.ds_id)                 # parked: nothing of the other dataset
        self.assertIsNone(self.w.cap_dock.ds_id)
        self.w.go(Section.CAPACITY)
        self.assertEqual(self.w.capacity_page.ds_id, b)               # loaded when opened
        self.assertEqual(self.w.cap_dock.ds_id, b)

    def test_a_calculation_of_the_other_dataset_waits_for_it(self):
        a, b = (d.id for d in self.w.state.datasets)
        self.w.select_dataset(a)
        reports: list = []
        thread = threading.Thread(target=lambda: STANDBY.paced(b, reports.append)("p"), daemon=True)
        thread.start()
        time.sleep(0.4)
        self.assertEqual(reports, [])
        self.w.select_dataset(b)
        thread.join(2)
        self.assertEqual(reports, ["p"])


if __name__ == "__main__":
    unittest.main()
