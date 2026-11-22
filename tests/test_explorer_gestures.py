from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.backend import Curve, ExplorerView
from app.widgets.chart import ChartWidget


class ExplorerGestureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.chart = ChartWidget()
        self.chart.resize(800, 500)
        self.chart.show()
        self.app.processEvents()
        rows = np.arange(101, dtype=np.uint64)
        x = np.linspace(0.0, 100.0, 101)
        y = np.linspace(0.0, 10.0, 101)
        curve = Curve(x, y, rows)
        view = ExplorerView(curve, None, 101, 101, (0.0, 100.0), (0.0, 10.0), None)
        self.chart.set_style("time_s", ("Time [h]", "Voltage [V]", None),
                             ("#006D85", "#B05A31"), True, "lines")
        self.chart.set_view(view)
        self.app.processEvents()

    def tearDown(self):
        self.chart.close()
        self.chart.deleteLater()
        self.app.processEvents()

    def _viewport_point(self, fx: float, fy: float):
        rect = self.chart.vb.boundingRect()
        local = QPointF(rect.left() + rect.width() * fx, rect.top() + rect.height() * fy)
        return self.chart.pw.mapFromScene(self.chart.vb.mapToScene(local))

    def _drag(self, start, end):
        target = self.chart.pw.viewport()
        QTest.mousePress(target, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        QTest.mouseMove(target, end, 20)
        QTest.mouseRelease(target, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
        self.app.processEvents()

    def test_box_zoom_drag_emits_smaller_viewport(self):
        emitted = []
        self.chart.viewportRequested.connect(emitted.append)
        self._drag(self._viewport_point(0.2, 0.2), self._viewport_point(0.8, 0.8))
        self.assertEqual(len(emitted), 1)
        self.assertLess(emitted[0].x.span, 100.0)
        self.assertLess(emitted[0].y1.span, 10.0)
        self.assertFalse(self.chart.box.isVisible())

    def test_pan_tool_drag_emits_shifted_viewport(self):
        emitted = []
        self.chart.viewportRequested.connect(emitted.append)
        self.chart.pan_mode = True
        self._drag(self._viewport_point(0.35, 0.5), self._viewport_point(0.65, 0.5))
        self.assertEqual(len(emitted), 1)
        self.assertAlmostEqual(emitted[0].x.span, 100.0)
        self.assertNotAlmostEqual(emitted[0].x.lo, 0.0)

    def _drag_event(self, stage: str, start: QPointF, pos: QPointF, modifiers=Qt.KeyboardModifier.NoModifier):
        """A pyqtgraph-like drag event for ChartWidget._drag: stage is 'start', 'move' or 'finish'."""
        return SimpleNamespace(button=lambda: Qt.MouseButton.LeftButton, isStart=lambda: stage == "start",
                               isFinish=lambda: stage == "finish", pos=lambda: pos, buttonDownPos=lambda: start,
                               modifiers=lambda: modifiers, accept=lambda: None, ignore=lambda: None)

    def test_pan_draws_thin_lines_only_while_dragging(self):
        """a pan redraws the curves on every move; they use a 1 px pen while the pan lasts and get
        their 1.5 px pen back on release or Esc. A box zoom never changes the pens."""
        width = lambda: self.chart.c1.opts["pen"].widthF()                     # noqa: E731
        self.assertEqual(width(), 1.5)
        a, b, c = QPointF(100, 100), QPointF(200, 120), QPointF(300, 140)
        emitted = []
        self.chart.viewportRequested.connect(emitted.append)
        self.chart.pan_mode = True
        self.chart._drag(self._drag_event("start", a, b))
        self.chart._drag(self._drag_event("move", a, c))
        self.assertEqual(width(), 1.0)
        self.assertEqual(self.chart.c1.opts["pen"].color().name(), "#006d85")       # same colour
        self.chart._drag(self._drag_event("finish", a, c))
        self.assertEqual(width(), 1.5)
        self.assertEqual(len(emitted), 1)                                           # the pan still applies
        self.chart._drag(self._drag_event("start", a, b))
        self.assertEqual(width(), 1.0)
        QTest.keyClick(self.chart, Qt.Key.Key_Escape)                               # cancelled pan
        self.assertEqual(width(), 1.5)
        self.chart._drag(self._drag_event("finish", a, c))
        self.assertEqual(width(), 1.5)
        self.chart.pan_mode = False
        self.chart._drag(self._drag_event("start", a, b))                           # box zoom: pens untouched
        self.assertEqual(width(), 1.5)
        self.chart._drag(self._drag_event("finish", a, c))
        self.assertEqual(width(), 1.5)

    def _pixels(self):
        self.app.processEvents()
        return self.chart.pw.grab().toImage()

    def _no_cache(self):
        from PySide6.QtWidgets import QGraphicsItem
        for c in (self.chart.c1, self.chart.c2):
            c.curve.setCacheMode(QGraphicsItem.CacheMode.NoCache)

    def test_cached_curves_look_identical_and_follow_new_data(self):
        """curves are cached as images so the box-zoom rectangle and the hover crosshair do not
        redraw them; the cache must not change a pixel and must refresh when the data or the style change."""
        from PySide6.QtWidgets import QGraphicsItem
        self.assertEqual(self.chart.c1.curve.cacheMode(), QGraphicsItem.CacheMode.DeviceCoordinateCache)
        x = np.linspace(0.0, 100.0, 101)
        wavy = ExplorerView(Curve(x, 5 + 4 * np.sin(x / 3), np.arange(101, dtype=np.uint64)), None, 101, 101,
                            (0.0, 100.0), (0.0, 10.0), None)
        cached_before = self._pixels()
        self.chart.set_view(wavy)                                      # new data: the cached image must follow
        cached_after = self._pixels()
        self.assertNotEqual(cached_before, cached_after)
        self.chart.set_style("time_s", ("Time [h]", "Voltage [V]", None), ("#B05A31", "#006D85"), True,
                             "lines_markers")
        cached_style = self._pixels()
        self._no_cache()
        self.assertEqual(self._pixels(), cached_style)                 # pixel-identical to drawing without cache
        self.chart.set_style("time_s", ("Time [h]", "Voltage [V]", None), ("#006D85", "#B05A31"), True, "lines")
        self.assertEqual(self._pixels(), cached_after)

    def _with_y2(self):
        rows = np.arange(101, dtype=np.uint64)
        x = np.linspace(0.0, 100.0, 101)
        view = ExplorerView(Curve(x, np.linspace(0.0, 10.0, 101), rows), Curve(x, np.linspace(-1.0, 1.0, 101), rows),
                            101, 101, (0.0, 100.0), (0.0, 10.0), (-1.0, 1.0))
        self.chart.set_style("time_s", ("Time [h]", "Voltage [V]", "Current [A]"), ("#006D85", "#B05A31"), True, "lines")
        self.chart.set_view(view)
        self.app.processEvents()

    def test_dragging_the_right_axis_down_moves_only_y2_down(self):
        """drag the Y2 axis down: the Y2 curve and its zero move down; X and Y1 do not change."""
        self._with_y2()
        emitted = []
        self.chart.viewportRequested.connect(emitted.append)
        axis = self.chart.pi.getAxis("right")
        start = self.chart.pw.mapFromScene(QPointF(axis.mapRectToScene(
            axis.mapRectFromParent(axis.geometry())).center()))
        end = start + type(start)(0, int(self.chart.vb.height() / 4))
        self._drag(start, end)
        self.assertEqual(len(emitted), 1)
        vp = emitted[0]
        self.assertEqual((vp.x.lo, vp.x.hi), (0.0, 100.0))
        self.assertEqual((vp.y1.lo, vp.y1.hi), (0.0, 10.0))
        self.assertAlmostEqual(vp.y2.span, 2.0)
        self.assertGreater(vp.y2.lo, -1.0)                       # higher values shown: the curve moved down

    def test_wheel_in_plot_zooms_every_axis_around_the_pointer(self):
        """the mouse wheel inside the plot zooms in / out (it used to do nothing)."""
        self._with_y2()
        emitted = []
        self.chart.viewportRequested.connect(emitted.append)
        centre = self.chart.vb.mapViewToScene(QPointF(50.0, 5.0))
        ev = SimpleNamespace(delta=lambda: 120, scenePos=lambda: centre, accept=lambda: None)
        self.chart.vb.wheelEvent(ev)
        vp = emitted[-1]
        self.assertLess(vp.x.span, 100.0)
        self.assertLess(vp.y1.span, 10.0)
        self.assertLess(vp.y2.span, 2.0)
        self.assertAlmostEqual((vp.x.lo + vp.x.hi) / 2, 50.0, places=6)
        ev = SimpleNamespace(delta=lambda: -120, scenePos=lambda: centre, accept=lambda: None)
        self.chart.vb.wheelEvent(ev)
        self.assertAlmostEqual(emitted[-1].x.span, 100.0, places=6)

    def test_tracking_can_be_disabled(self):
        self.chart.set_point_tracking(False)
        self.assertFalse(self.chart.point_tracking)
        self.assertFalse(self.chart.tip.isVisible())


if __name__ == "__main__":
    unittest.main()
