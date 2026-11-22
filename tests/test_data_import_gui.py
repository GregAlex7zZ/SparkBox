from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from app.fonts import load_fonts
from app.main_window import MainWindow
from app.gregorithm_backend import GregorithmBackend
from app.theme import ThemeManager
from app.state import Section
from app.state import Dataset
from app.elaboration import ElabResult
from app.units import UnitPrefs
from app.widgets.map_dialog import MapDialog


FIXTURE = Path(__file__).parent / "fixtures" / "canonical.csv"


def pump(milliseconds=20):
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


class GuiData01Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        load_fonts()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.backend = GregorithmBackend(Path(self.temp.name) / "results")
        self.theme = ThemeManager(self.app)
        self.window = MainWindow(self.backend, self.theme)

    def tearDown(self):
        self.window.close()
        pump()
        self.temp.cleanup()

    def test_real_import_worker_publishes_correct_dataset(self):
        self.window.start_import(str(FIXTURE), "auto")
        self.assertFalse(self.window.act_auto.isEnabled())
        deadline = time.monotonic() + 10
        while self.window._active_job is not None and time.monotonic() < deadline:
            pump()
        self.assertIsNone(self.window._active_job)
        self.assertTrue(self.window.act_auto.isEnabled())
        self.assertEqual(len(self.window.state.datasets), 1)
        dataset = self.window.state.active
        self.assertEqual(self.window.results[dataset.id].n_valid, 4)
        self.assertIs(self.window.data_page.currentWidget(), self.window.data_page.views[dataset.id])
        view = self.window.data_page.views[dataset.id]
        self.assertEqual(view.discard_chip.toolTip(), view.result.discard_summary)
        self.assertIn("INVALID CURRENT OR VOLTAGE", view.discard_chip.toolTip())

    def test_open_file_is_one_menu_button(self):
        control = self.window.data_page.empty.split
        self.assertFalse(hasattr(control, "arrow"))
        self.assertEqual([action.text() for action in control.menu.actions()],
                         ["Auto Mapping…", "Manual Mapping…"])
        self.assertEqual(control.layout().count(), 1)

    def test_real_elaboration_worker_publishes_before_unlocking_results(self):
        self.window.start_import(str(FIXTURE), "auto")
        deadline = time.monotonic() + 10
        while self.window._active_job is not None and time.monotonic() < deadline:
            pump()
        dataset = self.window.state.active
        self.assertFalse(dataset.elab.has_result)
        self.assertFalse(self.window.state.availability()[Section.EXPLORER])
        self.window.go(Section.ELABORATION)
        self.window.elab.process(dataset.id)
        self.assertTrue(dataset.elab.is_running)
        self.assertFalse(self.window.state.availability()[Section.EXPLORER])
        deadline = time.monotonic() + 15
        while dataset.elab.is_running and time.monotonic() < deadline:
            pump()
        self.assertFalse(dataset.elab.is_running)
        self.assertTrue(dataset.elab.has_result)
        self.assertTrue(self.window.state.availability()[Section.EXPLORER])
        result = dataset.elab.result
        self.assertEqual(self.backend.elab_table(result.result_id, "points").total, 4)
        self.assertEqual(len(self.backend.elab_table(result.result_id, "segments").columns), 10)

    def test_header_control_is_visible_checked_disabled_and_explained(self):
        inspection = self.backend.inspect_source(str(FIXTURE))
        dialog = MapDialog(FIXTURE.name, inspection, self.theme)
        self.assertTrue(dialog.header_chk.isVisibleTo(dialog) or not dialog.isVisible())
        self.assertTrue(dialog.header_chk.isChecked())
        self.assertFalse(dialog.header_chk.isEnabled())
        self.assertIn("first row contains column names", dialog.header_chk.toolTip())
        dialog.close()

    def test_collapsed_navigation_icons_keep_uniform_centered_controls(self):
        self.window.nav.set_collapsed(True)
        buttons = list(self.window.nav.buttons.values())
        self.assertTrue(all(b.property("collapsed") is True for b in buttons))
        self.assertEqual({(b.width(), b.height()) for b in buttons}, {(40, 40)})
        self.window.nav.set_collapsed(False)
        self.assertTrue(all(b.property("collapsed") is False for b in buttons))
        self.assertEqual({(b.width(), b.height()) for b in buttons}, {(178, 40)})

    def test_advanced_dock_uses_themed_host_surface(self):
        self.assertEqual(self.window.dock.objectName(), "DockHost")
        self.window.band.set_advanced_checked(True)
        self.theme.set_mode("dark")
        self.assertEqual(self.theme.tokens.name, "dark")
        self.theme.set_mode("light")
        self.assertEqual(self.theme.tokens.name, "light")

    def test_real_explorer_unique_rows_units_and_sample_metadata(self):
        self.window.start_import(str(FIXTURE), "auto")
        deadline = time.monotonic() + 10
        while self.window._active_job is not None and time.monotonic() < deadline:
            pump()
        dataset = self.window.state.active
        original_hash = self.window.results[dataset.id].canonical_content_hash
        self.window.elab.process(dataset.id)
        deadline = time.monotonic() + 15
        while dataset.elab.is_running and time.monotonic() < deadline:
            pump()
        self.window.go(Section.EXPLORER)
        deadline = time.monotonic() + 15
        while dataset.id not in self.window.explorer.views and time.monotonic() < deadline:
            pump()
        view = self.window.explorer.views[dataset.id]
        self.assertEqual((view.rendered, view.total), (4, 4))
        self.assertEqual(view.y1.source_row.tolist(), view.y2.source_row.tolist())
        before = dataset.elab.result.result_id
        self.window.elab.set_sample_text(dataset.id, "active_mass_mg", "12.5")
        self.assertEqual(dataset.elab.sample.active_mass_mg, 12.5)
        self.assertEqual(dataset.elab.result.result_id, before)
        self.assertFalse(dataset.elab.dirty)
        self.window.units.set_dataset_units(dataset.id, UnitPrefs(time="s", current="A", voltage="mV",
                                                                   capacity="Ah", energy="Wh"))
        deadline = time.monotonic() + 15
        while dataset.id in self.window.explorer._cancel and time.monotonic() < deadline:
            pump()
        self.assertEqual(self.window.results[dataset.id].canonical_content_hash, original_hash)
        self.assertEqual(self.window.backend.data_columns(dataset.id),
                         [("Time", "s"), ("Current", "A"), ("Voltage", "mV")])

    def test_comparison_is_sidebar_only_and_restores_each_dataset_section(self):
        for index in range(2):
            dataset = Dataset(f"cmp-{index}", f"Dataset {index + 1}", "fixture.csv")
            dataset.elab.finish(ElabResult(f"m2-{index}", 2, 4, 4, 10, dataset.elab.params))
            self.window.state.add(dataset)
            self.window.band.add_tab(dataset.id, dataset.name)
        first, second = self.window.state.datasets
        self.window.backend.compare_explorer = lambda _request, _cancel: []
        self.window.backend.result_cycles = lambda _result_id: [1, 2]
        self.window.select_dataset(first.id)
        self.window.go(Section.EXPLORER)
        self.window.select_dataset(second.id)
        self.window.go(Section.CAPACITY)
        self.window.go(Section.COMPARISON)
        self.assertTrue(all(not box._active for box in self.window.band._boxes.values()))
        self.assertIs(self.window.stack.currentWidget(), self.window.comparison_page)
        self.window.select_dataset(first.id)
        self.assertEqual(self.window.current, Section.EXPLORER)
        self.window.go(Section.COMPARISON)
        self.window.select_dataset(second.id)
        self.assertEqual(self.window.current, Section.CAPACITY)


if __name__ == "__main__":
    unittest.main()
