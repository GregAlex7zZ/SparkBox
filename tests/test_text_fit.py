"""Regressions for text that must never be cut (2026-11-22): combo boxes whose closed box fits the chosen name and
whose drop-down list fits the longest item, toolbars that wrap (FlowLayout with the last item on the right or centred
lines) instead of squeezing their controls, dataset tabs as wide as the file name, and the GITT / ICI diffusion
chart drawn with markers and lines by default."""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEventLoop, QRect, QTimer
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QWidget

from app.fonts import load_fonts
from app.theme import ThemeManager
from app.widgets.combos import FitComboBox, install_popup_fit, popup_width
from app.widgets.controls import FlowLayout
from app.widgets.tabs import TAB_MAX_W, TabBox
from app.tokens import TAB_W

LONG = "Discharge Specific Capacity [mAh/g]"


def pump(ms: int = 30) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setOrganizationName("gregorithm-test")
        load_fonts()
        cls.theme = ThemeManager(cls.app)
        cls.theme.apply(force=True)


class Combos(Base):
    def test_closed_box_follows_the_chosen_name_even_when_filled_with_signals_blocked(self):
        box = FitComboBox(min_width=120)
        box.addItems(["Cycle", LONG])
        box.show()
        pump()
        short = box.sizeHint().width()
        self.assertEqual(short, 120)                                       # "Cycle" fits in the minimum
        box.blockSignals(True)                                            # how the pages refill their selectors
        box.setCurrentIndex(1)
        box.blockSignals(False)
        box.repaint()
        long = box.sizeHint().width()
        self.assertGreater(long, short)
        self.assertGreaterEqual(long, box.fontMetrics().horizontalAdvance(LONG) + 20)
        box.close()

    def test_every_drop_down_list_is_widened_to_its_longest_item(self):
        install_popup_fit(self.app)
        install_popup_fit(self.app)                                       # idempotent
        box = QComboBox()                                                 # a plain combo, as in any dialog
        box.setFixedWidth(110)
        box.addItems(["None", LONG])
        box.show()
        pump()
        box.showPopup()
        pump(60)
        popup = box.view().parentWidget()
        self.assertGreaterEqual(popup.width(), popup_width(box, popup) - 1)
        self.assertGreater(popup.width(), box.width())
        box.hidePopup()
        box.close()


class Compact(Base):
    """IR Drop values on one screen, and the Pulse Fitting values table scrolled with the pointer (2026-11-24)."""

    def test_wrapped_label_shrinks_again(self):
        from app.widgets.controls import WrapLabel
        label = WrapLabel("global time: last pulse sample with t = 1 s, reference = last sample of the rest")
        label.resize(120, 20)
        label.show()
        pump()
        tall = label.minimumHeight()
        label.resize(900, 20)
        pump()
        self.assertLess(label.minimumHeight(), tall)                      # it used to keep the tallest height
        label.close()

    def test_ir_values_are_arranged_by_width_and_an_empty_message_takes_no_room(self):
        from app.widgets.gitt_ici_ir_view import IrDropChart
        chart = IrDropChart("pre_pulse")
        chart.show()
        rows = {}
        for width in (300, 500, 700):
            chart.resize(width, 600)
            pump()
            rows[width] = [chart.grid.getItemPosition(chart.grid.indexOf(k))[0] for k, _v in chart.cells]
        self.assertEqual(len(set(rows[300])), 8)                          # one value per line
        self.assertEqual(rows[500][2], rows[500][4])                      # ΔV and ΔI share a line
        self.assertNotEqual(rows[500][0], rows[500][1])
        self.assertEqual(rows[700][0], rows[700][1])                      # both samples on one line
        self.assertTrue(chart.message.isHidden())
        chart.clear("Nothing")
        self.assertFalse(chart.message.isHidden())
        chart.close()

    def test_fit_values_table_is_dragged_sideways_and_its_bar_hides_no_row(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        from app.widgets.gitt_ici_fit_view import ValuesTable
        table = ValuesTable()
        table.setFixedWidth(300)
        table.show()
        table.set_rows([f"A long column {i}" for i in range(9)], [["some value"] * 9] * 3)
        pump()
        bar = table.horizontalScrollBar()
        self.assertGreater(bar.maximum(), 100)                            # pixels, not whole columns
        self.assertGreaterEqual(table.viewport().height(), sum(table.rowHeight(i) for i in range(3)))
        view = table.viewport()
        QTest.mousePress(view, Qt.MouseButton.LeftButton, pos=QPoint(200, 10))
        QTest.mouseMove(view, QPoint(120, 10))
        pump()
        QTest.mouseRelease(view, Qt.MouseButton.LeftButton, pos=QPoint(120, 10))
        self.assertEqual(bar.value(), 80)
        table.close()


class Flow(Base):
    def _row(self, last_right=False, centered=False, width=400):
        host = QWidget()
        flow = FlowLayout(host, h_spacing=8, last_right=last_right, centered=centered)
        items = [QPushButton(t) for t in ("One", "Two", "Three")]
        for b in items:
            flow.addWidget(b)
        flow.setGeometry(QRect(0, 0, width, 100))
        return host, items

    def test_last_item_goes_to_the_right_edge(self):
        _host, items = self._row(last_right=True)
        self.assertEqual(items[-1].geometry().right(), 399)
        self.assertEqual(items[0].geometry().left(), 0)

    def test_centered_lines(self):
        _host, items = self._row(centered=True)
        left = items[0].geometry().left()
        right = 399 - items[-1].geometry().right()
        self.assertLessEqual(abs(left - right), 1)

    def test_a_narrow_row_wraps_instead_of_squeezing(self):
        _host, items = self._row(width=60)
        for b in items:
            self.assertGreaterEqual(b.geometry().width(), min(b.sizeHint().width(), 60))
        self.assertGreater(items[-1].geometry().top(), items[0].geometry().top())


class Tabs(Base):
    def test_tab_fits_the_file_name_up_to_its_maximum(self):
        short = TabBox("a", "a.csv", self.theme)
        self.assertEqual(short.width(), TAB_W)
        name = "gitt_long_file_name_for_tab_check"
        mid = TabBox("b", name, self.theme)
        self.assertGreater(mid.width(), TAB_W)
        self.assertEqual(mid.label.text(), name)                          # whole name, no "…"
        huge = TabBox("c", "x" * 200, self.theme)
        self.assertEqual(huge.width(), TAB_MAX_W)
        self.assertTrue(huge.label.text().endswith("…"))


class Pages(Base):
    def test_axis_selectors_are_fit_combos_in_wrapping_rows(self):
        from app.demo_backend import DemoBackend
        from app.main_window import MainWindow
        w = MainWindow(DemoBackend(0.0), self.theme, demo=True)
        try:
            for combos in (w.capacity_page.combos.values(), w.explorer_page.combos.values(),
                           (w.comparison_page.x_cb, w.comparison_page.y_cb)):
                for c in combos:
                    self.assertIsInstance(c, FitComboBox)
                    self.assertIsInstance(c.parentWidget().parentWidget().layout(), FlowLayout)
            labels = {lab.text() for lab in w.elab_page.sample_ready.findChildren(QLabel)}
            self.assertIn("Active Material Mass", labels)
            self.assertTrue(self.app.property("sparkboxPopupFit"))
        finally:
            w.deleteLater()
            pump()

    def test_diffusion_chart_draws_markers_and_lines_by_default(self):
        from app.widgets.gitt_ici_results_view import DEFAULT_STYLE, ResultsView
        self.assertEqual(DEFAULT_STYLE, "lines_markers")
        self.assertEqual(ResultsView(self.theme).plot_style, "lines_markers")


if __name__ == "__main__":
    unittest.main()
