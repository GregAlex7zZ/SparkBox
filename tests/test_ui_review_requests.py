"""Regressions for the 2026-11-22 review round: "Sample Informations" heading without the Optional chip, the Surface
Area link to Data Elaboration in Capacity, the per-pulse V1 / I1 times with Apply / Apply to All in DCIR Review and
the Export Data link of Data Elaboration."""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from app.dcir import ACCEPTED, AUTO, PENDING, DcirState, DetectionResult, EventView, FACTORY_DETECTION
from app.demo_backend import DemoBackend
from app.main_window import MainWindow
from app.state import Dataset
from app.theme import ThemeManager


class Base(unittest.TestCase):
    """One demo main window per test class (no scientific backend needed for these widgets)."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setOrganizationName("gregorithm-test")
        cls.theme = ThemeManager(cls.app)
        cls.w = MainWindow(DemoBackend(0.0), cls.theme, demo=True)

    @classmethod
    def tearDownClass(cls):
        cls.w.deleteLater()


class Labels(Base):
    def test_sample_block_heading_is_sample_informations_without_optional_chip(self):
        texts = [label.text() for label in self.w.elab_page.sample_ready.findChildren(QLabel)]
        self.assertIn("Sample Informations", texts)
        self.assertNotIn("Sample", texts)
        self.assertNotIn("Optional", texts)

    def test_capacity_offers_the_elaboration_link_for_mass_and_area(self):
        links = [b for b in self.w.capacity_page.findChildren(QPushButton) if b.text() == "Edit in Data Elaboration"]
        self.assertEqual(len(links), 2)                 # Ready to Analyze: Active Material Mass and Surface Area

    def test_elaboration_summary_offers_export_next_to_view_and_details(self):
        page = self.w.elab_page
        self.assertEqual(page.export_link.text(), "Export Data…")
        self.assertTrue(page.export_link.property("quiet"))


class DcirTimes(Base):
    def setUp(self):
        self.w.state.add(Dataset("times", "cell", "cell.csv"))
        c = self.w.state.get("times").dcir
        events = tuple(EventView(f"e{i}", AUTO, PENDING, event_time_s=100.0 * i, cycle=1, pre_pulse_row=10)
                       for i in (1, 2))
        c.begin("detect")
        c.detected(DetectionResult("d", "el", FACTORY_DETECTION, events))
        c.decide("e1", ACCEPTED)
        c.selected = "e1"
        self.c: DcirState = c
        self.page = self.w.dcir_page
        self.page.ds_id = "times"

    def tearDown(self):
        self.w.state.remove("times")

    def test_apply_buttons_replace_the_calculated_chip(self):
        self.assertFalse(hasattr(self.page, "prev_tag"))
        self.page._fill_times(self.c)
        self.assertFalse(self.page.apply_one.isEnabled())           # nothing typed yet
        self.assertFalse(self.page.apply_all.isEnabled())
        self.assertEqual(self.page.mt.text(), "30")
        self.assertIn("all pulses", self.page.mt_scope.text())

    def test_typed_times_enable_apply_and_apply_sends_the_pulse_back_to_pending(self):
        self.c.set_draft_text("v1", "12")
        self.page._fill_times(self.c)
        self.assertTrue(self.page.apply_one.isEnabled())
        self.assertTrue(self.page.apply_all.isEnabled())
        self.assertTrue(self.c.apply_times_to_pulse())
        self.page._fill_times(self.c)
        self.assertIn("this pulse", self.page.mt_scope.text())
        self.assertEqual(self.c.event("e1").state, PENDING)
        self.assertIn("own times", self.page._sub(self.c.event("e1"), self.w.units.units_for("times")))

    def test_invalid_time_shows_the_error_and_disables_both(self):
        self.c.set_draft_text("i1", "-3")
        self.page._fill_times(self.c)
        self.assertFalse(self.page.apply_one.isEnabled())
        self.assertFalse(self.page.apply_all.isEnabled())
        self.assertTrue(self.page.mt_err.text().startswith("I1"))


if __name__ == "__main__":
    unittest.main()
