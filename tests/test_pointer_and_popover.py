"""Regressions for the pointer over charts and the (i) info cards.

- The axes of every chart set a resize-arrow cursor on the whole axis item. With the grid on, an axis item covers the
  whole plot area, so the pointer became an up/down arrow anywhere in the chart (also on the static Correction
  overview). The arrow must appear only over the axis band of an interactive chart.
- Info cards had a fixed width and adjustSize(), which under-measures word-wrapped labels in a popup: long texts were
  cut. The card must be tall enough for all of its text, or scroll inside when the screen is too short."""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication, QWidget

from app.fonts import load_fonts
from app.widgets.chart import ChartWidget
from app.widgets.controls import Popover


class _Hover:
    """Minimal pyqtgraph HoverEvent: position in item coordinates."""

    def __init__(self, pos, exit_=False):
        self._pos, self._exit = pos, exit_

    def pos(self):
        return self._pos

    def isExit(self):
        return self._exit


class PointerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        load_fonts()

    def setUp(self):
        self.chart = ChartWidget()
        self.chart.resize(700, 400)
        self.chart.show()
        self.app.processEvents()
        self.left = self.chart.pi.getAxis("left")

    def tearDown(self):
        self.chart.close()

    def _band_point(self):
        r = self.left.mapRectFromParent(self.left.geometry())
        return r.center()

    def _plot_point(self):
        vb = self.chart.vb
        return self.left.mapFromItem(vb, vb.boundingRect().center())

    def test_no_resize_cursor_by_default_or_inside_the_plot(self):
        self.assertFalse(self.left.hasCursor())
        self.left.hoverEvent(_Hover(self._plot_point()))
        self.assertFalse(self.left.hasCursor())

    def test_resize_cursor_only_over_the_axis_band(self):
        self.left.hoverEvent(_Hover(self._band_point()))
        self.assertTrue(self.left.hasCursor())
        self.left.hoverEvent(_Hover(self._plot_point()))
        self.assertFalse(self.left.hasCursor())
        self.left.hoverEvent(_Hover(self._band_point()))
        self.left.hoverEvent(_Hover(QPointF(), exit_=True))
        self.assertFalse(self.left.hasCursor())

    def test_static_chart_never_shows_the_resize_cursor(self):
        self.chart.interactive = False
        self.left.hoverEvent(_Hover(self._band_point()))
        self.assertFalse(self.left.hasCursor())


LONG = ("A long explanation " * 40).strip()


class PopoverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        load_fonts()

    def setUp(self):
        self.host = QWidget()
        self.host.resize(400, 300)
        self.host.show()

    def tearDown(self):
        self.host.close()

    def test_card_is_tall_enough_for_all_its_text(self):
        pop = Popover("Title", ("Short first paragraph.", LONG, LONG), self.host)
        pop.show_beside(self.host)
        self.app.processEvents()
        self.assertEqual(pop.width(), Popover.WIDE)
        bar = pop.scroll.verticalScrollBar()
        self.assertEqual(bar.maximum(), 0)                  # nothing hidden: no scrolling needed
        for label in pop.labels:
            self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
        pop.close()

    def test_text_taller_than_the_screen_scrolls_inside_the_card(self):
        pop = Popover("Title", tuple([LONG] * 12), self.host)
        pop.show_beside(self.host)
        self.app.processEvents()
        screen = self.host.screen().availableGeometry()
        self.assertLessEqual(pop.height(), screen.height())
        self.assertGreater(pop.scroll.verticalScrollBar().maximum(), 0)
        pop.close()

    def test_short_text_keeps_the_narrow_card(self):
        pop = Popover("Title", ("One line.",), self.host)
        pop.show_beside(self.host)
        self.assertEqual(pop.width(), Popover.WIDTH)
        pop.close()


if __name__ == "__main__":
    unittest.main()
