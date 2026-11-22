"""Curve caching of MultiChart (dQ/dV and Comparison charts).

lines are cached as device-resolution images so moving the box-zoom rectangle, the hover readout
or the marker does not redraw them (dense dQ/dV results: ~200 ms -> ~17 ms per mouse move). The cache must not change
a single pixel and must follow new traces.
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PySide6.QtWidgets import QApplication, QGraphicsItem

from app.widgets.multi_chart import MultiChart, Trace


def _traces(shift: float = 0.0, symbol=None) -> list[Trace]:
    """Two dense oscillating curves, one solid and one dashed, as a dQ/dV result would draw them."""
    v = np.linspace(3.0, 4.2, 3000)
    return [Trace((1, "charge", "raw"), [(v, np.sin(v * 40 + shift) * 5, 0)], "#006D85", 1.5, 1.0, "solid", symbol,
                  "Cycle 1"),
            Trace((2, "discharge", "raw"), [(v, -np.cos(v * 25 + shift) * 3, 0)], "#B05A31", 1.5, 0.45, "dash", None,
                  "Cycle 2")]


class MultiChartCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.chart = MultiChart()
        self.chart.resize(900, 520)
        self.chart.show()

    def tearDown(self):
        self.chart.close()
        self.chart.deleteLater()
        self.app.processEvents()

    def _pixels(self):
        self.app.processEvents()
        return self.chart.pw.grab().toImage()

    def _set_cache(self, mode) -> None:
        for item in self.chart._items:
            item.curve.setCacheMode(mode)

    def test_every_trace_is_cached(self):
        self.chart.set_traces(_traces(symbol="o"))
        self.assertEqual(len(self.chart._items), 2)
        for item in self.chart._items:
            self.assertEqual(item.curve.cacheMode(), QGraphicsItem.CacheMode.DeviceCoordinateCache)
            self.assertEqual(item.scatter.cacheMode(), QGraphicsItem.CacheMode.NoCache)      # sub-pixel symbols

    def test_cached_drawing_is_pixel_identical_and_follows_new_traces(self):
        self.chart.set_traces(_traces(), keep_view=False)
        first = self._pixels()
        self.chart.set_traces(_traces(shift=1.0, symbol="o"), keep_view=False)
        cached = self._pixels()
        self.assertNotEqual(first, cached)                     # new traces replace the cached images
        self._set_cache(QGraphicsItem.CacheMode.NoCache)
        self.assertEqual(self._pixels(), cached)               # identical to drawing without cache


if __name__ == "__main__":
    unittest.main()
