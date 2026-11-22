"""Regressions for the 2026-10-26 usability fixes: wrapped table titles, elided free text, wrapping toolbars, clicks in
the gridded chart area reaching the ViewBox (manual DCIR picking), pulse zones drawn on the recorded samples, markers on
the exact measurement rows, and the moving sample window of the manual DCIR dialog."""
import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QTableView, QWidget

from app.backend import PulseTrace
from app.dcir import ManualValue, MeasurementView
from app.gregorithm_backend import GregorithmBackend
from app.theme import ThemeManager
from app.units import UnitPrefs
from app.widgets.controls import ElidedLabel, FlowLayout
from app.widgets.dcir_manual import ManualDialog
from app.widgets.dcir_page import DcirPage
from app.widgets.pulse_chart import PulseChart
from app.widgets.wrap_header import install, title_width, wrap_lines


def trace_1hz(t0=1000.0, first=-60, last=90, rows0=0):
    """1 Hz record: rest until t=-1 (pre-pulse sample), pulse samples t=0..29, rest from t=30."""
    t = np.arange(first, last, 1.0)
    on = (t >= 0) & (t < 30)
    return PulseTrace("e1", t, np.where(on, -1.0, 0.0), np.where(on, 3.6, 3.7), np.arange(rows0, rows0 + t.size),
                      t + t0, np.ones(t.size, int), t0, {}, {})


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


class WrappedTitles(Base):
    def test_lines_that_fit_are_never_elided_with_inter_ligatures(self):
        """Regression (Inter, 2026-10-27): elidedText cut 'Specific' (fi ligature) on a line measured as fitting."""
        from PySide6.QtGui import QFont, QFontMetrics
        from app.fonts import UI_FONT, load_fonts
        load_fonts()
        font = QFont(UI_FONT)
        font.setPointSizeF(9.75)
        font.setHintingPreference(QFont.HintingPreference.PreferVerticalHinting)   # as the app font
        fm = QFontMetrics(font)
        self.assertNotEqual(fm.elidedText("Discharge Specific", Qt.TextElideMode.ElideRight,
                                          fm.horizontalAdvance("Discharge Specific")), "Discharge Specific")   # Qt quirk
        text = "Discharge Specific Capacity [mAh/g]"
        lines = wrap_lines(text, fm, fm.horizontalAdvance("Discharge Specific"), max_lines=3)
        self.assertEqual(" ".join(lines), text)

    def test_wrap_lines_keeps_words_and_elides_overflow(self):
        fm = QLabel().fontMetrics()
        text = "Discharge Specific Capacity [mAh/g]"
        w = fm.horizontalAdvance("Discharge Specific")
        lines = wrap_lines(text, fm, w, max_lines=3)
        self.assertEqual(" ".join(lines), text)                          # nothing lost when it fits in 3 lines
        self.assertTrue(all(fm.horizontalAdvance(x) <= w for x in lines))
        two = wrap_lines(text, fm, fm.horizontalAdvance("Discharge"), max_lines=2)
        self.assertEqual(len(two), 2)
        self.assertTrue(two[-1].endswith("…"))                          # overflow is marked, never silently cut

    def test_title_width_fits_preferred_lines(self):
        fm = QLabel().fontMetrics()
        text = "Charge Specific Capacity [mAh/g]"
        w = title_width(text, fm, lines=2)
        self.assertLessEqual(len(wrap_lines(text, fm, w, max_lines=9)), 2)
        self.assertGreater(len(wrap_lines(text, fm, w - 1, max_lines=9)), 2)
        self.assertLess(w, fm.horizontalAdvance(text))                   # narrower than the title on one line

    def test_header_grows_for_wrapped_titles_and_columns_follow_values(self):
        v = QTableView()
        v.setModel(_Model(["Cycle", "Discharge Specific Capacity [mAh/g]"], [["1", "12.34"]]))
        h = install(v)
        h.setSectionResizeMode(h.ResizeMode.ResizeToContents)
        v.resize(600, 300)
        v.show()
        self.app.processEvents()
        h.update_height()
        self.assertGreater(h.height(), 34)                               # two lines instead of a cut title
        self.assertLess(v.columnWidth(1), h.fontMetrics().horizontalAdvance("Discharge Specific Capacity [mAh/g]") + 32)
        v.close()


class ElisionAndFlow(Base):
    def test_elided_label_shrinks_with_tooltip(self):
        lab = ElidedLabel("dcir_pulse.csv · 1 pulses · 1 accepted · 0 rejected · 0 pending")
        self.assertLess(lab.minimumSizeHint().width(), lab.sizeHint().width())
        lab.resize(60, 20)
        lab.show()
        lab.repaint()
        self.app.processEvents()
        self.assertEqual(lab.toolTip(), lab.text())                      # full text available when shortened
        lab.resize(lab.sizeHint().width() + 20, 20)
        lab.repaint()
        self.app.processEvents()
        self.assertEqual(lab.toolTip(), "")
        lab.close()

    def test_flow_layout_wraps_instead_of_squeezing(self):
        box = QWidget()
        flow = FlowLayout(box)
        for _ in range(4):
            w = QLabel("x" * 20)
            w.setFixedSize(120, 30)
            flow.addWidget(w)
        self.assertEqual(flow.heightForWidth(1000), 30)
        self.assertGreater(flow.heightForWidth(260), 60)                 # wrapped onto more lines
        self.assertLessEqual(flow.minimumSize().width(), 120)


class PulseChartPicking(Base):
    def test_click_in_gridded_plot_reaches_viewbox(self):
        """The axis bounding rect covers the plot when the grid is on; it used to accept the click, so manual picking
        on the chart never worked."""
        ch = PulseChart(pick=True)
        ch.resize(800, 500)
        ch.show()
        ch.set_trace(trace_1hz())
        got = []
        ch.sampleClicked.connect(lambda i, p: got.append((i, p)))
        for _ in range(5):
            self.app.processEvents()
        vp = ch.glw.viewport()
        pos = ch.glw.mapFromScene(ch.vbv.mapViewToScene(pg.Point(10.0, 3.65)))
        QTest.mouseMove(vp, pos)
        QTest.mouseClick(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
        self.app.processEvents()
        self.assertEqual(len(got), 1)
        self.assertAlmostEqual(float(ch.trace.t_rel_s[got[0][0]]), 10.0, delta=1.0)
        ch.close()

    def test_jittery_slow_click_still_picks_and_never_zooms(self):
        """pyqtgraph turns a press held over 0.5 s that moves 1 px into a drag: it used to box-zoom a tiny rectangle
        and the pick was lost ("sometimes I click on the chart and it does not take it")."""
        ch = PulseChart(pick=True)
        ch.resize(800, 500)
        ch.show()
        ch.set_trace(trace_1hz())
        got = []
        ch.sampleClicked.connect(lambda i, p: got.append(i))
        for _ in range(5):
            self.app.processEvents()
        before = ch.vbv.viewRange()
        vp = ch.glw.viewport()
        pos = ch.glw.mapFromScene(ch.vbv.mapViewToScene(pg.Point(10.0, 3.65)))
        ch.glw.scene().minDragTime = 0.0                                 # every movement counts as a drag
        QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
        QTest.mouseMove(vp, pos + type(pos)(2, 1))
        QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos + type(pos)(2, 1))
        self.app.processEvents()
        self.assertEqual(len(got), 1)
        self.assertAlmostEqual(float(ch.trace.t_rel_s[got[0]]), 10.0, delta=1.0)
        np.testing.assert_allclose(ch.vbv.viewRange(), before)
        ch.close()


class PulseChartGestures(Base):
    def test_wheel_zooms_and_axis_drag_pans_one_axis(self):
        ch = PulseChart()
        ch.resize(800, 500)
        ch.show()
        ch.set_trace(trace_1hz())
        for _ in range(5):
            self.app.processEvents()
        (x0, x1), (y0, y1) = ch.vbv.viewRange()
        ev = SimpleNamespace(delta=lambda: 120, scenePos=lambda: ch.vbv.mapViewToScene(pg.Point(10.0, 3.65)),
                             accept=lambda: None)
        ch.vbv.wheelEvent(ev)                                            # one notch in: both spans shrink
        (a0, a1), (b0, b1) = ch.vbv.viewRange()
        self.assertLess(a1 - a0, x1 - x0)
        self.assertLess(b1 - b0, y1 - y0)
        drag = SimpleNamespace(isStart=lambda: True, isFinish=lambda: True, pos=lambda: pg.Point(0, 50),
                               buttonDownPos=lambda: pg.Point(0, 0))
        ch._axis_drag(ch.vbv, "left", drag)                              # drag the Y axis down: content moves down
        (c0, c1), (d0, d1) = ch.vbv.viewRange()
        self.assertEqual((c0, c1), (a0, a1))
        self.assertGreater(d0, b0)
        self.assertAlmostEqual(d1 - d0, b1 - b0)
        ch.close()


class PulseZonesAndMarks(Base):
    def event(self):
        return SimpleNamespace(pulse_first_row=60, pulse_last_row=89, pre_pulse_row=59, rest_before_duration_s=60.0,
                               rest_after_duration_s=60.0)

    def test_zones_cover_recorded_samples_only(self):
        """Core durations end at the next segment's first sample; drawing [0, duration] shifted the pulse zone one
        sample late (it covered the first rest sample and not the V0 reference)."""
        tr = trace_1hz()
        z = GregorithmBackend.pulse_zones(self.event(), tr.t_rel_s, tr.rows)
        self.assertEqual(z["pulse"], (0.0, 29.0))                        # first..last pulse sample
        self.assertEqual(z["rest_before"], (-60.0, -1.0))                # ends on the V0/I0 reference sample
        self.assertEqual(z["rest_after"], (30.0, 89.0))

    def test_markers_sit_on_measurement_rows(self):
        tr = trace_1hz()
        m = MeasurementView("e1", 30.0, 29.0, 1.0, 0.0, 3.7, -1.0, 3.6, -1.0, -0.1, 0.1, None,
                            reference_row=59, current_row=62, voltage_row=89)
        marks = DcirPage._measurement_marks(tr, m)
        self.assertEqual(float(tr.t_rel_s[marks["v0"]]), -1.0)
        self.assertEqual(marks["v0"], marks["i0"])
        self.assertEqual(float(tr.t_rel_s[marks["i1"]]), 2.0)
        self.assertEqual(float(tr.t_rel_s[marks["v1"]]), 29.0)          # the real sample, not the requested 30 s
        self.assertEqual(DcirPage._measurement_marks(tr, None), {})


class ManualNavigation(Base):
    def test_window_follows_cycle_jump_and_keeps_picks_by_row(self):
        theme = ThemeManager(self.app)
        calls = []

        def window_fn(center, span):
            calls.append((center, span))
            # absolute samples t = center-span/2 .. center+span/2, row = absolute time (1 Hz, starting at 0)
            t_abs = np.arange(max(0.0, center - span / 2), center + span / 2, 1.0)
            return PulseTrace(None, t_abs - center, np.zeros(t_abs.size), np.full(t_abs.size, 3.7), t_abs.astype(int),
                              t_abs, np.ones(t_abs.size, int), center)

        first = trace_1hz(t0=1000.0, rows0=940)                          # rows equal absolute time here too
        dlg = ManualDialog(None, theme, UnitPrefs(), first, "s", window_fn=window_fn,
                           cycles=[(1, 0.0, 3000.0), (2, 5000.0, 8000.0)])
        self.assertTrue(dlg.cycle_edit.isEnabled())
        self.assertEqual(dlg._time_title, "Time [s]")                    # absolute time, not "from V0"
        self.assertEqual(float(dlg.trace.t_rel_s[0]), 940.0)
        self.assertEqual(dlg.chart.zone_items, [])                       # no shaded pulse in manual mode
        dlg.target = "v0"
        dlg._picked(59, "v")                                             # row 999, t = 999 s on the chart
        self.assertEqual(dlg.values["v0"].sample_row, 999)
        self.assertEqual(dlg.values["v0"].sample_time_s, 999.0)
        dlg.cycle_edit.setText("2")
        dlg._jump_cycle(dlg.cycle_edit.text())                           # typed cycle 2 starts at 5000 s
        self.assertEqual(dlg.anchor_s, 0.0)
        self.assertEqual(float(dlg.trace.t_rel_s[0]), 5000.0)
        dlg._jump_cycle("7")
        self.assertIn("not in this elaboration", dlg.nav_note.text())
        self.assertEqual(float(dlg.trace.t_rel_s[0]), 5000.0)
        dlg._load(1000.0, 600.0, keep_view=True)                         # back around the pick: still marked by row
        idx = int(np.flatnonzero(np.asarray(dlg.trace.rows) == 999)[0])
        self.assertEqual(float(dlg.trace.t_rel_s[idx]), 999.0)
        x, _y = dlg.chart.marks["v0"].getData()
        self.assertEqual(list(x), [999.0])
        dlg.target = "v1"
        dlg._picked(idx + 30, "v")                                       # absolute time stays consistent after moving
        self.assertEqual(dlg.values["v1"].sample_time_s, 1029.0)
        dlg.close()

    def test_without_window_fn_there_is_no_navigation(self):
        dlg = ManualDialog(None, ThemeManager(self.app), UnitPrefs(), trace_1hz(), "s")
        self.assertFalse(hasattr(dlg, "cycle_edit"))
        dlg.close()


if __name__ == "__main__":
    unittest.main()
