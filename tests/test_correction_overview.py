"""Regressions for the Cycle Correction whole-test overview (DCIR step 3).

The overview used to read and draw EVERY elaborated point as an interactive line chart, so the DCIR page was slow to
show and to leave on long tests, and the accepted spans were not marked at all. It is now an indicative, static
picture: the adapter samples it like the Data Explorer (point budget per plot pixel, extremes kept) and the view draws
the span marks without zoom, pan or hover, redrawing only when its inputs change."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gregorithm
import numpy as np
import pyarrow.parquet as pq
from PySide6.QtWidgets import QApplication

from app.backend import Curve
from app.correction import Span
from app.dcir import ACCEPTED, DetectionParams
from app.elaboration import ElabParams
from app.fonts import load_fonts
from app.gregorithm_backend import GregorithmBackend, _factor
from app.theme import ThemeManager
from app.units import UnitPrefs
from app.widgets.correction_view import CorrectionView


def _long_test_csv(path: Path, n: int = 20_000) -> None:
    """Synthetic constant-current cycling: 1 s samples, 2,000 s half-cycles, voltage a triangle 3.0-4.2 V."""
    t = np.arange(n, dtype=float)
    phase = (t // 2_000) % 2
    current = np.where(phase == 0, 100.0, -100.0)
    frac = (t % 2_000) / 2_000
    voltage = np.where(phase == 0, 3.0 + 1.2 * frac, 4.2 - 1.2 * frac)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("time_s,current_mA,voltage_V\n")
        for row in zip(t, current, voltage):
            f.write("%g,%g,%.6f\n" % row)


class OverviewSamplingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.backend = GregorithmBackend(Path(self.temp.name) / "results")

    def tearDown(self):
        self.backend.close()
        self.temp.cleanup()

    def test_overview_is_sampled_real_rows_with_extremes_kept(self):
        source = Path(self.temp.name) / "long.csv"
        _long_test_csv(source)
        token = gregorithm.CancellationToken
        self.backend.inspect_source(str(source))
        self.backend.import_auto(str(source), "long", lambda _p: None, token())
        elab = self.backend.elaborate("long", ElabParams(), lambda _p: None, token())
        full = pq.read_table(self.backend._result_view(elab.result_id).files["points"],
                             columns=["time_s", "voltage_v", "source_row"])
        self.assertEqual(full.num_rows, 20_000)
        curve = self.backend.correction_overview(elab.result_id, UnitPrefs(), 250)   # budget max(1000, 4 px)
        x, y, rows = np.asarray(curve.x), np.asarray(curve.y), np.asarray(curve.source_row)
        self.assertLessEqual(x.size, 1_000)
        self.assertGreater(x.size, 500)
        self.assertTrue(np.all(np.diff(x) > 0))                                     # time order
        all_rows = full["source_row"].to_numpy()
        self.assertTrue(np.isin(rows, all_rows).all())                              # real rows, nothing invented
        voltage = full["voltage_v"].to_numpy()
        self.assertAlmostEqual(float(y.min()), float(voltage.min()))                # extremes kept
        self.assertAlmostEqual(float(y.max()), float(voltage.max()))
        self.assertEqual(int(rows[0]), int(all_rows[0]))                            # first / last kept
        self.assertEqual(int(rows[-1]), int(all_rows[-1]))
        wide = self.backend.correction_overview(elab.result_id, UnitPrefs(), 1600)
        self.assertLessEqual(len(wide.x), 6_400)

    def test_spans_carry_display_times_so_the_overview_can_mark_them(self):
        """Regression: correction_spans never set start/end_display, so the view dropped every span."""
        source = Path(__file__).parent / "fixtures" / "dcir_correction_cycles.csv"
        token = gregorithm.CancellationToken
        self.backend.inspect_source(str(source))
        self.backend.import_auto(str(source), "corr", lambda _p: None, token())
        elab = self.backend.elaborate("corr", ElabParams(), lambda _p: None, token())
        detection = DetectionParams(rest_before_s=5.0, pulse_s=5.0, rest_after_s=5.0, rest_before_tolerance_pct=0.0,
                                    pulse_tolerance_pct=0.0, rest_after_tolerance_pct=0.0)
        dcir = self.backend.detect_dcir("corr", elab.result_id, detection, UnitPrefs(), lambda _p: None, token())
        published = self.backend.publish_dcir(dcir.result_id, {dcir.events[0].event_id: ACCEPTED}, 2.0,
                                              UnitPrefs(), lambda _p: None, token())
        spans = self.backend.correction_spans(published.result_id, UnitPrefs())
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertTrue(span.has_span)
        points = pq.read_table(self.backend._result_view(elab.result_id).files["points"],
                               columns=["source_row", "time_s"]).to_pydict()
        time_of = dict(zip(points["source_row"], points["time_s"]))
        factor = _factor("time_s", UnitPrefs())
        self.assertAlmostEqual(span.start_display, time_of[span.start_row] * factor)
        self.assertAlmostEqual(span.end_display, time_of[span.end_row] * factor)
        self.assertLessEqual(span.start_display, span.end_display)


class _Signal:
    def connect(self, _slot):
        pass


class _Ctl:
    """Just what CorrectionView._draw reads."""

    def __init__(self, curve, spans, selected=None):
        self.changed = _Signal()
        self.overviews = {"d": curve}
        self.spans = {"d": spans}
        self._co = SimpleNamespace(selected=selected)
        self.units = SimpleNamespace(units_for=lambda _ds: UnitPrefs())

    def co(self, _ds):
        return self._co


class OverviewViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        load_fonts()
        cls.theme = ThemeManager(cls.app)

    def _view(self, spans, selected=None):
        x = np.linspace(0.0, 10.0, 200)
        ctl = _Ctl(Curve(x, 3.0 + 0.1 * x, np.arange(200)), spans, selected)
        view = CorrectionView(self.theme, ctl)
        view.ds_id = "d"
        return view, ctl

    def test_overview_is_static_and_marks_every_span(self):
        spans = [Span("e1", "DCIR1", "auto", 10, 20, 2.0, 2.1), Span("e2", "DCIR2", "auto", 50, 60, 6.0, 6.1),
                 Span("e3", "DCIR3", "manual", None, None)]                     # no span: not marked
        view, _ctl = self._view(spans, selected="e2")
        view._draw()
        self.assertFalse(view.chart.interactive)
        self.assertFalse(view.chart.point_tracking)
        self.assertFalse(hasattr(view, "reset_btn"))
        marked = view.marks.path().boundingRect()
        chosen = view.mark_sel.path().boundingRect()
        self.assertAlmostEqual(marked.left(), 2.0)
        self.assertAlmostEqual(marked.right(), 2.1)
        self.assertAlmostEqual(chosen.left(), 6.0)
        self.assertAlmostEqual(chosen.right(), 6.1)
        self.assertGreater(marked.height(), 0)

    def test_overview_is_not_redrawn_when_nothing_changed(self):
        view, ctl = self._view([Span("e1", "DCIR1", "auto", 10, 20, 2.0, 2.1)])
        calls = []
        original = view.chart.set_view
        view.chart.set_view = lambda v: (calls.append(v), original(v))
        view._draw()
        view._draw()                                                            # e.g. threshold typed
        self.assertEqual(len(calls), 1)
        ctl._co.selected = "e1"                                                 # selection changes the marks
        view._draw()
        self.assertEqual(len(calls), 2)
        self.assertAlmostEqual(view.mark_sel.path().boundingRect().left(), 2.0)


if __name__ == "__main__":
    unittest.main()
