"""Add Manual Measurement dialog. Four values I0, V0, I1, V1; each one is typed, or taken from a REAL data
point picked on the embedded chart (the exact recorded row, never an interpolated position). The resistance is
previewed by gregorithm (preview_fn -> backend.preview_manual_dcir, no event created) and created by
create_manual_event on Add; the dialog itself never calculates. Optional 'Correction span': two
distinct real samples (boundary_samples) used by Cycle Correction ; 'Set Span…' in Correction reopens
this dialog with the values of the measurement it replaces.
The chart's time axis is the absolute test time [s]: a manual measurement does not refer to a detected pulse, so no
pulse zone is shaded and no time is measured from a V0. The page opens the chart around the pulse decided last.
Navigation (only when the page passes window_fn): the chart shows a window of real samples that follows the view. Pan
(Pan tool, Shift+drag or drag on an axis), zoom (wheel), or use Earlier/Later, and the samples around the new view are
read; typing a cycle number jumps to the start of that cycle. Picked values keep their exact rows, so they stay valid
(and marked) wherever the window moves. Very wide views are not loaded (zoom in first) to keep reads small."""
from __future__ import annotations
import math
from dataclasses import replace
from typing import Optional
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout, QWidget)
from ..dcir import MANUAL_KEYS, Boundary, ManualValue, manual_ready
from ..theme import ThemeManager, set_prop
from .controls import SegmentedControl
from .pulse_chart import PulseChart

DEFAULT_SPAN_S = 600.0          # samples read around the view when navigating [s]
MAX_VIEW_FACTOR = 20            # views wider than this many spans are not loaded (zoom in first)


def _lab(text="", muted=False, size=None, weight=None) -> QLabel:
    l = QLabel(text)
    if muted:
        l.setProperty("muted", True)
    css = (f"font-size: {size * .75:g}pt;" if size else "") + (f"font-weight: {weight};" if weight else "")
    if css:
        l.setStyleSheet(css)
    return l


def _btn(text, kind, h=30) -> QPushButton:
    b = QPushButton(text)
    b.setProperty(kind, True)
    b.setFixedHeight(h)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class ManualDialog(QDialog):
    def __init__(self, parent, theme: ThemeManager, units, trace, time_unit: str, preview_fn=None,
                 initial: Optional[dict] = None, replacing: str = "", window_fn=None, cycles=None):
        """window_fn(center_s, span_s) -> PulseTrace reads real samples around an absolute time (None: fixed chart);
        cycles = [(cycle, first_s, last_s), ...] for the cycle jump (empty: not offered)."""
        super().__init__(parent)
        self.window_fn, self.cycles = window_fn, list(cycles or [])
        self.anchor_s = 0.0                            # chart time = absolute test time (t_rel_s + t0_s of the trace)
        self.span_s = DEFAULT_SPAN_S
        self._time_title = "Time [s]"
        trace = self._absolute(trace)
        self._view_timer = QTimer(self)
        self._view_timer.setSingleShot(True)
        self._view_timer.setInterval(200)
        self._view_timer.timeout.connect(self._follow_view)
        self.replacing = replacing
        self.boundary: list = [None, None]
        self.setWindowTitle("Set Correction Span" if replacing else "Add Manual Measurement")
        self.setMinimumWidth(900)          # the height follows the layout, so the chart never overlaps the panels
        self.theme, self.units, self.trace, self.time_unit = theme, units, trace, time_unit
        self.values: dict[str, ManualValue] = {}
        self.source = {k: "sample" for k, _l, _q in MANUAL_KEYS}
        self.source["i1"] = "typed"                    # the pulse current is usually the programmed value
        self.target: Optional[str] = None
        self._disp_time: dict[str, float] = {}
        self.preview_fn = preview_fn
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 16)
        v.setSpacing(12)
        v.addWidget(_lab(f"Set Correction Span \u00b7 {replacing}" if replacing else "Add Manual Measurement", size=20, weight=500))
        d = _lab("Enter the four values used for the resistance. For each one choose whether you type it or take it from "
                 "a real data point; a picked point is always the exact recorded row, never an interpolated position.",
                 muted=True, size=13)
        d.setWordWrap(True)
        v.addWidget(d)
        g = QGridLayout()
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(10)
        for c, t in enumerate(("Value", "Source", "Value or data point")):
            g.addWidget(_lab(t, muted=True, size=12), 0, c)
        self.w: dict[str, dict] = {}
        for r, (key, label, q) in enumerate(MANUAL_KEYS, start=1):
            seg = SegmentedControl([("typed", "Typed"), ("sample", "Data Point")], small=True, aria=f"{label} source")
            seg.set_value(self.source[key])
            seg.valueChanged.connect(lambda val, k=key: self._set_source(k, val))
            unit = units.unit(q)
            edit = QLineEdit()
            edit.setFixedWidth(110)
            edit.setAlignment(Qt.AlignmentFlag.AlignRight)
            edit.setAccessibleName(f"{label} value")
            edit.textEdited.connect(lambda txt, k=key: self._typed(k, txt))
            typed_note = _lab("typed value · not linked to a data point", size=11)
            typed_note.setStyleSheet("font-size: 8.25pt; color: #9A4B22;")
            chip = QLabel("—")
            chip.setProperty("chip", True)
            ref = _lab("", muted=True, size=12)
            pick = _btn("Pick on Chart…", "link", 28)
            pick.setCheckable(True)
            pick.clicked.connect(lambda _c, k=key: self._start_pick(k))
            cell = QWidget()
            h = QHBoxLayout(cell)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)
            for x in (edit, _lab(unit, muted=True, size=12), typed_note, chip, ref, pick):
                h.addWidget(x)
            h.addStretch(1)
            g.addWidget(_lab(label, weight=500), r, 0)
            g.addWidget(seg, r, 1)
            g.addWidget(cell, r, 2)
            self.w[key] = {"edit": edit, "note": typed_note, "chip": chip, "ref": ref, "pick": pick, "unit": unit,
                           "seg": seg, "unit_l": h.itemAt(1).widget()}
        # optional correction span (two boundary samples)
        r = len(MANUAL_KEYS) + 1
        g.addWidget(_lab("Correction Span", weight=500), r, 0)
        g.addWidget(_lab("optional", muted=True, size=12), r, 1)
        bc = QWidget()
        bh = QHBoxLayout(bc)
        bh.setContentsMargins(0, 0, 0, 0)
        bh.setSpacing(8)
        self.b_chip, self.b_pick = [], []
        for i, name in enumerate(("A", "B")):
            ch = QLabel(f"{name} \u2014")
            ch.setProperty("chip", True)
            pk = _btn(f"Pick {name}\u2026", "link", 28)
            pk.setCheckable(True)
            pk.clicked.connect(lambda _c, k=("bA", "bB")[i]: self._start_pick(k))
            bh.addWidget(ch)
            bh.addWidget(pk)
            self.b_chip.append(ch)
            self.b_pick.append(pk)
        self.b_clear = _btn("Clear", "link", 28)
        self.b_clear.clicked.connect(self._clear_boundary)
        bh.addWidget(self.b_clear)
        bh.addWidget(_lab("two distinct recorded points: the span between them is excluded by Cycle Correction",
                          muted=True, size=11))
        bh.addStretch(1)
        g.addWidget(bc, r, 2)
        v.addLayout(g)
        self.hint = _lab("", muted=True, size=12)
        v.addWidget(self.hint)
        self.chart = PulseChart(pick=True)
        self.chart.setMinimumHeight(220)
        self.chart.set_labels(f"Voltage [{units.voltage}]", f"Current [{units.current}]", self._time_title)
        self.chart.sampleClicked.connect(self._picked)
        self.chart.set_trace(trace, zones=False)
        if trace is None:
            self.chart.show_message("No recorded samples to pick from. Type the values instead.")
        if window_fn is not None:
            v.addLayout(self._nav_bar())
            self.chart.xRangeChanged.connect(lambda *_: self._view_timer.start())
        v.addWidget(self.chart, 1)
        box = QFrame()
        box.setProperty("panel", True)
        bl = QHBoxLayout(box)
        bl.setContentsMargins(14, 10, 14, 10)
        bl.setSpacing(24)
        self.pv: dict[str, QLabel] = {}
        for key, title in (("di", "\u0394I"), ("dv", "\u0394V"), ("r", "Resistance (preview, gregorithm)"), ("soc", "SOC"),
                           ("at", "Placed at")):
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(_lab(title, muted=True, size=11))
            val = _lab("\u2014", size=16 if key == "r" else 13, weight=500 if key == "r" else None)
            col.addWidget(val)
            self.pv[key] = val
            bl.addLayout(col)
        bl.addStretch(1)
        v.addWidget(box)
        foot = _lab("The measurement is added as Accepted (SOC is not calculated for manual measurements) and is saved "
                    "with the next Apply & Calculate. The preview is calculated by gregorithm; nothing is created until you add it. The "
                    "correction span (two boundary points) is set in the Correction step.", muted=True, size=12)
        foot.setWordWrap(True)
        v.addWidget(foot)
        row = QHBoxLayout()
        self.err = QLabel()
        self.err.setProperty("hintErr", True)
        row.addWidget(self.err, 1)
        cancel, self.ok = _btn("Cancel", "ghost"), _btn("Add Measurement", "primary")
        cancel.clicked.connect(self.reject)
        self.ok.clicked.connect(self.accept)
        if replacing:
            self.ok.setText("Replace Measurement")
        row.addWidget(cancel)
        row.addWidget(self.ok)
        v.addLayout(row)
        self._prefill(initial or {})
        self._refresh()
        self._show_marks()
        screen = self.screen().availableGeometry() if self.screen() is not None else None
        self.resize(1000, min(880, screen.height() - 40) if screen is not None else 880)

    # ---------------------------------------------------------------- navigation
    def _nav_bar(self) -> QHBoxLayout:
        h = QHBoxLayout()
        h.setSpacing(8)
        h.addWidget(_lab("Cycle", muted=True, size=12))
        self.cycle_edit = QLineEdit()
        self.cycle_edit.setFixedWidth(80)
        self.cycle_edit.setAccessibleName("Jump to cycle")
        self.cycle_edit.setPlaceholderText("number" if self.cycles else "\u2014")
        self.cycle_edit.setValidator(QIntValidator(0, 10 ** 9, self.cycle_edit))
        self.cycle_edit.setEnabled(bool(self.cycles))
        self.cycle_edit.setToolTip("Type a cycle number and press Enter to jump to its start" if self.cycles
                                   else "Cycles are not available")
        self.cycle_edit.returnPressed.connect(lambda: self._jump_cycle(self.cycle_edit.text()))
        h.addWidget(self.cycle_edit)
        self.cycle_go = _btn("Go", "ghost", 28)
        self.cycle_go.setEnabled(bool(self.cycles))
        self.cycle_go.clicked.connect(lambda: self._jump_cycle(self.cycle_edit.text()))
        h.addWidget(self.cycle_go)
        h.addSpacing(10)
        self.earlier, self.later = _btn("\u2039 Earlier", "ghost", 28), _btn("Later \u203a", "ghost", 28)
        self.earlier.setToolTip("Move the view back by its width")
        self.later.setToolTip("Move the view forward by its width")
        self.earlier.clicked.connect(lambda: self._shift(-1))
        self.later.clicked.connect(lambda: self._shift(1))
        self.pan_btn = _btn("Pan", "ghost", 28)
        self.pan_btn.setCheckable(True)
        self.pan_btn.setToolTip("Drag to move along the data (or hold Shift and drag). Off: drag a box to zoom")
        self.pan_btn.toggled.connect(lambda on: setattr(self.chart, "pan_mode", on))
        self.fit_btn = _btn("Fit", "ghost", 28)
        self.fit_btn.setToolTip("Show all the samples read")
        self.fit_btn.clicked.connect(self.chart.reset_view)
        for b in (self.earlier, self.later, self.pan_btn, self.fit_btn):
            h.addWidget(b)
        self.nav_note = _lab("", muted=True, size=11)
        h.addWidget(self.nav_note, 1)
        return h

    def _absolute(self, trace):
        """The trace on the absolute time axis (t = t_rel_s + t0_s), without zones or measurement markers."""
        if trace is None:
            return None
        return replace(trace, t_rel_s=np.asarray(trace.t_rel_s, float) + float(trace.t0_s) - self.anchor_s,
                       t0_s=self.anchor_s, zones={}, markers={})

    def _jump_cycle(self, text: str) -> None:
        """Typed cycle number -> read the samples from the start of that cycle (the time axis stays absolute)."""
        try:
            wanted = int(text.strip())
        except ValueError:
            self.nav_note.setText("Type a cycle number")
            return
        hit = next(((lo, hi) for c, lo, hi in self.cycles if c == wanted), None)
        if hit is None:
            self.nav_note.setText(f"Cycle {wanted} is not in this elaboration")
            return
        lo, _hi = hit
        self._load(lo + self.span_s / 2, self.span_s, keep_view=False)

    def _shift(self, direction: int) -> None:
        lo, hi = self.chart.vbv.viewRange()[0]
        w = hi - lo
        self.chart.pv.setXRange(lo + direction * w, hi + direction * w, padding=0)

    def _follow_view(self) -> None:
        """Read the samples around the visible range when it leaves the samples already read."""
        if self.window_fn is None:
            return
        lo, hi = self.chart.vbv.viewRange()[0]
        w = hi - lo
        if self.trace is not None and len(self.trace.t_rel_s):
            t = np.asarray(self.trace.t_rel_s, float)
            slack = 0.02 * w                          # Fit / reset add a small padding around the samples
            if lo >= float(np.nanmin(t)) - slack and hi <= float(np.nanmax(t)) + slack:
                self.nav_note.setText("")
                return
        if w > MAX_VIEW_FACTOR * self.span_s:
            self.nav_note.setText("View too wide to read samples: zoom in on the time axis")
            return
        self._load(self.anchor_s + (lo + hi) / 2, max(self.span_s, 3 * w), keep_view=True)

    def _load(self, center_abs: float, span: float, keep_view: bool) -> None:
        try:
            tr = self.window_fn(center_abs, span)
        except Exception as exc:                      # the dialog stays usable with the samples already shown
            self.nav_note.setText(f"Could not read samples: {exc}")
            return
        self.trace = self._absolute(tr)
        n = len(self.trace.t_rel_s)
        self.nav_note.setText("" if n else "No recorded samples here")
        self.chart.show_message("")
        self.chart.set_trace(self.trace, zones=False, keep_view=keep_view)
        self._show_marks()
        self._refresh()

    def _show_marks(self) -> None:
        """Symbols on the values picked so far (by their exact rows, when they are among the samples shown)."""
        if self.trace is None:
            return
        rows = np.asarray(self.trace.rows)
        marks = {}
        for key, _l, _q in MANUAL_KEYS:
            mv = self.values.get(key)
            if mv is not None and mv.sample_row is not None:
                hit = np.flatnonzero(rows == mv.sample_row)
                if hit.size:
                    marks[key] = int(hit[0])
        self.chart.set_markers(marks)

    def _prefill(self, initial: dict) -> None:
        """Values of the measurement being replaced (Set Span…): same provenance, same exact values."""
        for key, _l, _q in MANUAL_KEYS:
            mv = initial.get(key)
            if mv is None:
                continue
            self.values[key] = mv
            self.source[key] = "sample" if mv.sample_row is not None else "typed"
            self.w[key]["seg"].set_value(self.source[key])
            if mv.sample_row is None:
                self.w[key]["edit"].setText(f"{mv.value:.10g}")
        b = initial.get("boundary")
        if b:
            self.boundary = list(b)
        self.target = "bA" if self.replacing and not b else None

    def _clear_boundary(self) -> None:
        self.boundary = [None, None]
        if self.target in ("bA", "bB"):
            self.target = None
        self._refresh()

    # ---------------------------------------------------------------- behaviour
    def _set_source(self, key: str, val: str) -> None:
        self.source[key] = val
        self.values.pop(key, None)
        if val == "typed":
            self._typed(key, self.w[key]["edit"].text())
        if self.target == key:
            self.target = None
        self._refresh()

    def _typed(self, key: str, text: str) -> None:
        try:
            x = float(text.strip().replace(",", ".").replace("−", "-"))
            ok = math.isfinite(x)
        except ValueError:
            ok = False
        if ok:
            self.values[key] = ManualValue(x)
        else:
            self.values.pop(key, None)
        set_prop(self.w[key]["edit"], "error", bool(text.strip()) and not ok)
        self._refresh()

    def _start_pick(self, key: str) -> None:
        self.target = None if self.target == key else key
        self._refresh()

    def _picked(self, idx: int, _plot: str) -> None:
        if self.target is None or self.trace is None:
            return
        tr, key = self.trace, self.target
        if key in ("bA", "bB"):
            cyc = tr.cycle[idx] if tr.cycle is not None else None
            self.boundary[0 if key == "bA" else 1] = Boundary(int(tr.rows[idx]), float(tr.t0_s + float(tr.t_rel_s[idx])),
                                                               float(tr.time_display[idx]), None if cyc is None else int(cyc))
            self.chart.mark_picked(idx)
            self.target = "bB" if key == "bA" and self.boundary[1] is None else None
            self._refresh()
            return
        q = dict((k, qq) for k, _l, qq in MANUAL_KEYS)[key]
        val = float((tr.current if q == "current" else tr.voltage)[idx])
        cyc = tr.cycle[idx] if tr.cycle is not None else None
        self.values[key] = ManualValue(val, int(tr.rows[idx]), float(tr.t0_s + float(tr.t_rel_s[idx])),
                                       None if cyc is None else int(cyc))
        self._disp_time[key] = float(tr.time_display[idx])
        self.chart.mark_picked(idx)
        self._show_marks()
        order = [k for k, _l, _q in MANUAL_KEYS]
        nxt = [k for k in order[order.index(key) + 1:] if self.source[k] == "sample" and k not in self.values]
        self.target = nxt[0] if nxt else None                 # continue with the next value to pick
        self._refresh()

    def _refresh(self) -> None:
        for key, w in self.w.items():
            typed = self.source[key] == "typed"
            for x in (w["edit"], w["unit_l"], w["note"]):
                x.setVisible(typed)
            for x in (w["chip"], w["ref"], w["pick"]):
                x.setVisible(not typed)
            mv = self.values.get(key)
            if not typed:
                w["chip"].setText(f"{mv.value:.6g} {w['unit']}" if mv else "—")
                td = self._disp_time.get(key)
                w["ref"].setText((f"Row {mv.sample_row:,}" + (f" · {td:.4f} {self.time_unit}" if td is not None else "")
                                  + (f" · cycle {mv.cycle}" if mv.cycle is not None else "")) if mv else "")
                w["pick"].setChecked(self.target == key)
                w["pick"].setText("Click a point…" if self.target == key else "Pick on Chart…")
                w["pick"].setEnabled(self.trace is not None)
        for i, (ch, pk) in enumerate(zip(self.b_chip, self.b_pick)):
            b = self.boundary[i]
            ch.setText(f"{'AB'[i]} \u00b7 row {b.row:,} \u00b7 {b.time_display:.4f} {self.time_unit}" if b else f"{'AB'[i]} \u2014")
            pk.setChecked(self.target == ("bA", "bB")[i])
            pk.setEnabled(self.trace is not None)
        self.b_clear.setVisible(any(self.boundary))
        if any(self.boundary):
            self.values["boundary"] = tuple(self.boundary)
        else:
            self.values.pop("boundary", None)
        names = {"bA": "boundary point A", "bB": "boundary point B"}
        self.hint.setText(f"Click the chart to take {names.get(self.target, (self.target or '').upper())} from the nearest "
                          "recorded sample." if self.target else "")
        msg = manual_ready(self.values)
        m, perr = (None, "")
        if not msg and self.preview_fn is not None:
            m, perr = self.preview_fn(dict(self.values))
        fmt = lambda x, u: "\u2014" if x is None else f"{x:,.4g} {u}".replace("-", "\u2212")
        self.pv["di"].setText(fmt(m.delta_i, self.units.current) if m else "\u2014")
        self.pv["dv"].setText(fmt(m.delta_v, self.units.voltage) if m else "\u2014")
        self.pv["r"].setText(fmt(m.resistance, self.units.resistance) if m else "\u2014")
        self.pv["soc"].setText("\u2014 not calculated for manual measurements")
        anchor = next((k for k in ("v1", "i1", "v0", "i0") if k in self.values and self.values[k].sample_row is not None), None)
        if anchor:
            a = self.values[anchor]
            self.pv["at"].setText(f"{self._disp_time.get(anchor, 0):.4f} {self.time_unit}"
                                  + (f" \u00b7 cycle {a.cycle}" if a.cycle is not None else "") + f" (from {anchor.upper()})")
        else:
            self.pv["at"].setText("\u2014 no data point picked")
        msg = msg or perr
        self.err.setText("" if not msg or not self.values else msg)
        self.ok.setEnabled(not msg)
        self.ok.setToolTip(msg)

    def chosen(self) -> dict:
        return dict(self.values)
