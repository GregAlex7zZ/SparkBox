"""Regressions for the second 2026-10-26 usability round: bundled UI font (Inter since 2026-10-27), no QStyleHints unique-connection
warning from pyqtgraph, fixed-width Advanced-settings dock whose contents never scroll sideways, visible and clickable
Comparison dataset checkboxes, theme changes that are skipped when nothing changes and otherwise show a busy overlay,
wrapped table headers that never cover the first rows, and the explicit "Go to Correction" step under DCIR Results."""
import os
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QAbstractTableModel, QEventLoop, QModelIndex, Qt, QTimer, qInstallMessageHandler
from PySide6.QtGui import QFontDatabase, QFontInfo
from PySide6.QtWidgets import QApplication, QCheckBox, QHeaderView, QScrollArea, QTableView

from app.demo_backend import DemoBackend
from app.fonts import load_fonts
from app.main_window import MainWindow
from app.state import Section
from app.theme import ThemeManager
from app.widgets.advanced_dock import DOCK_WIDTH
from app.widgets.wrap_header import install


def pump(ms=30):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_for(cond, timeout=20.0):
    t0 = time.time()
    while not cond() and time.time() - t0 < timeout:
        pump()
    if not cond():
        raise AssertionError("timeout")


class _Model(QAbstractTableModel):
    def __init__(self, heads, rows):
        super().__init__()
        self.heads, self.rows = heads, rows

    def rowCount(self, parent=QModelIndex()):
        return len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.heads)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        return self.rows[index.row()][index.column()] if role == Qt.ItemDataRole.DisplayRole else None

    def headerData(self, s, o, role=Qt.ItemDataRole.DisplayRole):
        return self.heads[s] if role == Qt.ItemDataRole.DisplayRole and o == Qt.Orientation.Horizontal else None


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setOrganizationName("gregorithm-test")
        load_fonts()


class Fonts(Base):
    def test_inter_is_bundled_and_resolves_with_its_weights(self):
        self.assertIn("Inter", QFontDatabase.families())
        styles = set(QFontDatabase.styles("Inter"))
        self.assertTrue({"Regular", "Medium", "SemiBold", "Bold"} <= styles, styles)
        theme = ThemeManager(self.app)
        theme.apply(force=True)
        self.assertEqual(QFontInfo(self.app.font()).family(), "Inter")


class ThemeAndLayoutRound3(Base):
    """2026-10-27: Manual Mapping preview unreadable in Dark, unstyled spin boxes in dQ/dV settings, invisible
    chart/table divider in Capacity and DCIR Results, tiny Capacity markers."""

    def test_manual_mapping_preview_uses_the_themed_card_surface(self):
        from app.demo_backend import DemoBackend as Demo
        from app.widgets.map_dialog import MapDialog
        theme = ThemeManager(self.app)
        dialog = MapDialog("demo.csv", Demo().inspect_source(""), theme)
        try:
            grid = dialog.findChildren(QScrollArea)[0]
            for surface in (grid, grid.viewport(), grid.widget()):
                self.assertEqual(surface.objectName(), "MapGrid")
                self.assertTrue(surface.testAttribute(Qt.WidgetAttribute.WA_StyledBackground))
            self.assertIn("#MapGrid", self.app.styleSheet())
        finally:
            dialog.deleteLater()

    def test_combo_lists_in_the_settings_dock_are_not_transparent(self):
        """Regression: #DockHost made every descendant transparent, so the dQ/dV Method list rendered black."""
        from PySide6.QtWidgets import QComboBox
        from PySide6.QtGui import QPalette
        theme = ThemeManager(self.app)
        w = MainWindow(DemoBackend(0), theme, demo=True)
        try:
            for mode in ("light", "dark"):
                theme.set_mode(mode)
                combo = next(c for c in w.dqdv_dock.findChildren(QComboBox) if c.accessibleName() == "dQ/dV method")
                view = combo.view()
                view.ensurePolished()
                view.viewport().ensurePolished()
                self.assertEqual(view.palette().color(QPalette.ColorRole.Base).name().lower(), theme.tokens.card.lower())
        finally:
            w.close()
            w.deleteLater()

    def test_spin_boxes_are_themed(self):
        ThemeManager(self.app).apply(force=True)
        sheet = self.app.styleSheet()
        self.assertIn("QAbstractSpinBox {", sheet)
        self.assertIn("QAbstractSpinBox::up-arrow", sheet)

    def test_capacity_and_dcir_results_have_a_visible_divider_and_capacity_has_larger_markers(self):
        from app.widgets.controls import DividerSplitter
        w = MainWindow(DemoBackend(0), ThemeManager(self.app), demo=True)
        try:
            self.assertIsInstance(w.capacity_page.split, DividerSplitter)
            self.assertTrue(any(isinstance(s, DividerSplitter) for s in w.dcir_page.findChildren(DividerSplitter)))
            self.assertEqual(w.capacity_page.chart.marker_size, 7)
            self.assertEqual(w.explorer_page.chart.marker_size, 3)       # dense raw data keeps small markers
            split = w.capacity_page.split
            if split.count() > 1:
                self.assertEqual(type(split.handle(1)).__name__, "_DividerHandle")
        finally:
            w.close()
            w.deleteLater()


class SystemAccent(Base):
    """System appearance uses the Windows accent colour (Light and Dark keep the approved Ocean palette)."""

    def test_accent_is_adjusted_to_readable_contrast_in_both_bases(self):
        from app.theme import accent_tokens, contrast
        from app.tokens import DARK, LIGHT
        for accent in ("#fb9a44", "#ffd700", "#0078d4", "#1a1a1a", "#e3008c", "#ffffff"):
            for base in (LIGHT, DARK):
                t = accent_tokens(base, accent)
                self.assertGreaterEqual(contrast(t.pri, base.card), 4.5, (accent, base.name, t.pri))
                self.assertGreaterEqual(contrast(t.on_pri, t.pri), 4.5, (accent, base.name, t.on_pri))
                self.assertEqual((t.run, t.err, t.bg), (base.run, base.err, base.bg))   # only the accent changes
        self.assertEqual(accent_tokens(LIGHT, "not a colour"), LIGHT)

    def test_light_and_dark_use_the_orange_of_the_approved_system_look(self):
        """Light and Dark carry the primary that System derived from the Author's #FB9A44 accent [2026-11-03]."""
        from app.theme import accent_tokens
        from app.tokens import DARK, LIGHT
        for base in (LIGHT, DARK):
            self.assertEqual(accent_tokens(base, "#FB9A44"), base, base.name)
        self.assertEqual(DARK.pri, "#FB9A44")
        self.assertEqual((LIGHT.run, DARK.run), ("#B05A31", "#E9AA7E"))     # amber of the compute buttons unchanged

    def test_system_mode_uses_the_accent_and_explicit_modes_do_not(self):
        from app.tokens import DARK, LIGHT
        theme = ThemeManager(self.app)
        original = theme.mode
        theme.system_accent = lambda: "#0078d4"
        try:
            theme.mode = "light"
            self.assertEqual(theme._resolve(), LIGHT)
            theme.mode = "dark"
            self.assertEqual(theme._resolve(), DARK)
            theme.mode = "system"
            resolved = theme._resolve()
            self.assertIn(resolved.name, ("light", "dark"))
            self.assertNotEqual(resolved.pri, (LIGHT if resolved.name == "light" else DARK).pri)
        finally:
            theme.mode = original


class NoErrorBeforeInteraction(Base):
    def test_empty_dqdv_cycles_are_required_but_not_shown_as_an_error_until_typed(self):
        from app.state import Dataset
        from app.widgets.dqdv_page import DqdvParamsForm
        w = MainWindow(DemoBackend(0), ThemeManager(self.app), demo=True)
        try:
            w.state.add(Dataset("one", "Cell one", "unused.csv"))
            form = DqdvParamsForm(w.theme, w.dqdv, docked=True)
            form.ds_id = "one"
            form.refresh()
            self.assertEqual(w.dqdv.dq("one").cycles_text, "")
            self.assertIn("cycles", w.dqdv.dq("one").errors())          # still required: Analyze stays disabled
            self.assertTrue(form.errs["cycles"].isHidden())
            self.assertFalse(form.cycles.property("error"))
            w.dqdv.edit("one", lambda q: setattr(q, "cycles_text", "0"))
            form.refresh()
            self.assertFalse(form.errs["cycles"].isHidden())
            self.assertTrue(form.cycles.property("error"))
        finally:
            w.close()
            w.deleteLater()


class PyqtgraphWarning(Base):
    def test_charts_do_not_print_the_qstylehints_unique_connection_warning(self):
        import pyqtgraph as pg
        import app.widgets.chart  # noqa: F401  (installs the pyqtgraph shim)
        messages: list[str] = []
        previous = qInstallMessageHandler(lambda _k, _c, message: messages.append(message))
        try:
            widgets = [pg.GraphicsLayoutWidget() for _ in range(2)]
            self.app.processEvents()
        finally:
            qInstallMessageHandler(previous)
        self.assertFalse(any("unique connections require" in m for m in messages), messages)
        self.assertEqual(len(widgets), 2)


class WrappedHeader(Base):
    def test_first_row_starts_below_a_wrapped_header_on_first_show(self):
        heads = ["Resistance [mΩ] with a long title", "Voltage before the pulse [V]", "SOC at the start [%]", "Notes"]
        table = QTableView()
        table.setModel(_Model(heads, [["1.234"] * 4 for _ in range(5)]))
        install(table)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.resize(400, 300)
        table.show()
        pump(80)
        header = table.horizontalHeader()
        self.assertGreater(header.height(), header.fontMetrics().lineSpacing() * 2)     # really wrapped
        self.assertEqual(header.sizeHint().height(), header.height())
        self.assertGreaterEqual(table.viewport().geometry().top(), header.geometry().bottom() + 1)
        table.close()


class Window(Base):
    """One MainWindow on the demo backend with two elaborated datasets, shared by the tests below."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.theme = ThemeManager(cls.app)
        cls.theme.set_mode("light")
        cls.theme.apply(force=True)
        cls.w = MainWindow(DemoBackend(0.0), cls.theme, demo=True)
        cls.w.resize(1360, 840)
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

    def test_dock_has_one_fixed_width_and_no_section_needs_horizontal_scrolling(self):
        self.w.band.set_advanced_checked(True)
        try:
            for section in (Section.ELABORATION, Section.EXPLORER, Section.CAPACITY, Section.DCIR, Section.DQDV,
                            Section.COMPARISON):
                self.w.go(section)
                pump(60)
                self.assertEqual(self.w.dock.width(), DOCK_WIDTH, section)
                for area in self.w.dock.currentWidget().findChildren(QScrollArea):
                    self.assertEqual(area.horizontalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                    body = area.widget()
                    if body is not None:
                        self.assertLessEqual(body.minimumSizeHint().width(), area.viewport().width(), section)
        finally:
            self.w.band.set_advanced_checked(False)

    def test_comparison_checkbox_is_drawn_and_the_dataset_name_toggles_it(self):
        self.assertIn("QCheckBox::indicator", self.app.styleSheet())       # visible indicator in the dock
        self.w.go(Section.COMPARISON)
        pump(60)
        dock = self.w.comparison_dock
        boxes = [b for b in dock.findChildren(QCheckBox) if b.isVisible()]
        self.assertEqual(len(boxes), 2)
        first_id = self.w.state.datasets[0].id
        row = dock.grid.itemAtPosition(0, 2).widget()                        # name + status of the first dataset
        row.mousePressEvent(None)
        pump(60)
        self.assertIn(first_id, self.w.comparison.cs.checked)
        self.w.comparison.toggle(first_id, False)

    def test_theme_change_is_skipped_when_unchanged_and_otherwise_shows_the_busy_overlay(self):
        events: list[str] = []
        overlay_seen: list[bool] = []
        started = lambda: (events.append("start"), QTimer.singleShot(0, lambda: overlay_seen.append(self.w.busy.isVisible())))
        self.theme.applyStarted.connect(started)
        self.theme.applyFinished.connect(lambda: events.append("finish"))
        try:
            self.theme.set_mode("light")                                     # unchanged: nothing re-styled
            self.assertEqual(events, [])
            self.theme.set_mode("dark")
            self.assertEqual(events, ["start", "finish"])
            self.assertEqual(overlay_seen, [True])                           # painted while the work ran
            self.assertFalse(self.w.busy.isVisible())
            self.assertEqual(self.theme.tokens.name, "dark")
        finally:
            self.theme.applyStarted.disconnect(started)
            self.theme.set_mode("light")


class DcirNextStep(Base):
    def test_results_show_a_go_to_correction_button_that_opens_step_3(self):
        from app.dcir import Step
        from app.widgets.dcir_page import DcirPage
        page = DcirPage.__new__(DcirPage)                                    # only the strip, no controller needed
        page.corr = object()
        calls = []
        page._go = calls.append
        strip = page._next_step()
        self.assertFalse(strip.isHidden())
        self.assertIn("Correction", page.next_btn.text())
        page.next_btn.click()
        self.assertEqual(calls, [Step.CORRECTION])
        page._fill_next_step(corrected=True)
        self.assertIn("Open Correction", page.next_btn.text())



class DividerHandlePaint(Base):
    def test_handle_paints_without_reading_the_theme_from_its_splitter(self):
        """Regression: paintEvent read splitter().theme, which raised AttributeError when PySide returned a wrapper
        of the parent splitter without its Python attributes."""
        from PySide6.QtWidgets import QLabel
        from app.widgets.controls import DividerSplitter
        split = DividerSplitter(ThemeManager(self.app), Qt.Orientation.Vertical)
        split.addWidget(QLabel("top"))
        split.addWidget(QLabel("bottom"))
        handle = split.handle(1)
        del split.theme                                                      # simulate the attribute-less wrapper
        errors, hook = [], sys.excepthook
        sys.excepthook = lambda *exc: errors.append(exc[1])                  # PySide reports override errors here
        try:
            self.assertFalse(handle.grab().isNull())                         # paints with the handle's own theme
        finally:
            sys.excepthook = hook
        self.assertEqual(errors, [])

if __name__ == "__main__":
    unittest.main()
