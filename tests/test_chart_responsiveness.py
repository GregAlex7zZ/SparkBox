"""Regressions for chart lag (2026-11-22): hovering and zooming Data Explorer with a large dataset lagged. A text
change in the hover readout relaid out the whole main window (its request climbed from the chart to the window),
FlowLayout recomputed its geometry in Python on every query of a layout pass, and one antialiased path per curve
made each wheel-zoom frame of a dense trace take ~140 ms (segments while the wheel turns: ~45 ms)."""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEvent, QObject, QRect
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from app.widgets.chart import ChartWidget
from app.widgets.controls import FlowLayout


class _LayoutRequests(QObject):
    """Counts LayoutRequest events per receiving widget."""

    def __init__(self):
        super().__init__()
        self.seen: list = []

    def eventFilter(self, obj, event) -> bool:      # noqa: N802 (Qt API)
        if event.type() == QEvent.Type.LayoutRequest:
            self.seen.append(obj)
        return False


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])


class HoverReadout(Base):
    def test_a_new_readout_text_does_not_relayout_the_window(self):
        window = QWidget()
        lay = QVBoxLayout(window)
        chart = ChartWidget()
        lay.addWidget(chart)
        window.resize(800, 500)
        window.show()
        self.app.processEvents()
        chart.tip.show()
        self.app.processEvents()
        spy = _LayoutRequests()
        self.app.installEventFilter(spy)
        try:
            for k in range(5):
                chart.tip_head.setText(f"Row {k * 1000:,}")
                self.app.processEvents()
        finally:
            self.app.removeEventFilter(spy)
        self.assertNotIn(window, spy.seen)                  # the request stops at the readout's own host
        self.assertNotIn(chart, spy.seen)
        self.assertIs(chart.tip.parentWidget(), chart.tip_host)
        self.assertIsNone(chart.tip_host.layout())
        window.close()

    def test_curves_are_drawn_fast_while_the_wheel_turns_and_clean_at_rest(self):
        chart = ChartWidget()
        chart.resize(600, 400)
        chart.show()
        self.app.processEvents()
        modes = lambda: {c.curve.opts["segmentedLineMode"] for c in (chart.c1, chart.c2)}    # noqa: E731
        self.assertEqual(modes(), {"off"})                  # at rest: one clean antialiased path
        chart._plot_wheel(1.0, chart.vb.sceneBoundingRect().center())
        self.assertEqual(modes(), {"on"})                   # wheel burst: separate segments, ~3x faster
        self.assertTrue(chart._wheel_rest.isActive())
        chart._wheel_rest.timeout.emit()                    # the wheel rested
        self.assertEqual(modes(), {"off"})
        chart.close()


class FlowCache(Base):
    def test_heights_are_cached_until_the_layout_is_invalidated(self):
        host = QWidget()
        flow = FlowLayout(host)
        buttons = [QPushButton(t) for t in ("One", "Two", "Three")]
        for b in buttons:
            flow.addWidget(b)
        calls = []
        original = flow._layout
        flow._layout = lambda rect, apply: calls.append(apply) or original(rect, apply)
        first = flow.heightForWidth(80)
        self.assertEqual(flow.heightForWidth(80), first)
        self.assertEqual(calls.count(False), 1)             # the second query came from the cache
        flow.setGeometry(QRect(0, 0, 300, 40))
        flow.setGeometry(QRect(0, 0, 300, 40))
        self.assertEqual(calls.count(True), 1)              # same place: items not moved again
        buttons[0].setText("A much longer label")           # a child changed: Qt invalidates the layout
        flow.invalidate()
        flow.heightForWidth(80)
        self.assertEqual(calls.count(False), 2)
        label_host = QWidget()
        FlowLayout(label_host).addWidget(QLabel("x"))
        self.assertGreater(label_host.layout().sizeHint().width(), 0)



class HoverDetails(Base):
    """Hover details are read once the pointer rests, one read at a time, and only for the latest row."""

    def test_a_sweep_reads_only_the_row_where_the_pointer_rests(self):
        import threading
        import time
        from app.explorer_controller import DETAILS_REST_MS, ExplorerController

        class Backend:
            def __init__(self):
                self.rows, self.gate = [], threading.Event()

            def point_details(self, rid, row, units):
                self.rows.append(row)
                self.gate.wait(2)
                return {"cycle": 1}

        backend = Backend()
        ctl = ExplorerController(None, backend, _FakeUnits())
        ctl.result_id = lambda ds: "rid"
        got = []
        ctl.detailsReady.connect(lambda ds, row, values: got.append(row))
        for row in range(50):                                   # a sweep: 50 rows, no rest in between
            ctl.request_details("d", row)
        self.assertEqual(backend.rows, [])                      # nothing read while the pointer moves
        end = time.time() + 2
        while not backend.rows and time.time() < end:
            self.app.processEvents()
            time.sleep(DETAILS_REST_MS / 4000)
        self.assertEqual(backend.rows, [49])                    # only the row where it rested
        for row in (60, 61, 62):                                # moves while that read still runs
            ctl.request_details("d", row)
        time.sleep(DETAILS_REST_MS / 500)
        self.app.processEvents()
        self.assertEqual(backend.rows, [49])                    # one read at a time
        backend.gate.set()
        end = time.time() + 3
        while len(backend.rows) < 2 and time.time() < end:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertEqual(backend.rows, [49, 62])                # then the latest row, never 60 or 61
        from PySide6.QtCore import QThreadPool
        QThreadPool.globalInstance().waitForDone(3000)


class _FakeUnits:
    """The bits of UnitsController that ExplorerController's constructor and details use."""

    class _Signal:
        def connect(self, _slot):
            pass

    changed = _Signal()

    def units_for(self, _ds):
        return None


if __name__ == "__main__":
    unittest.main()
