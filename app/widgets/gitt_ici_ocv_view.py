"""GITT / ICI OCV Analysis: rest-period list, OCV plot, sampled and extrapolated OCV controls, acceptance.

Left: every rest period of the selected element with its sampled and extrapolated OCV and its state (Pending,
Accepted, Modified after acceptance, Invalid). Centre: V versus time from the relaxation origin (the last sample under
current) of the selected rest, drawing the real samples, the sampled-OCV point, and, when extrapolation is active,
the LOG_TIME_2P fit curve recorded by gregorithm, its fit interval (shaded), the extrapolated point at the target and
the ICI pseudo-OCV when it exists. The view extends to the extrapolation target; a logarithmic time axis is available.

Below, three cards: the sampled point of THIS rest (time, voltage, row; type a time for this rest only or click a
sample, "Use global rule" removes both), the OCV settings of ALL rests (sampled-OCV time, empty = last rest sample;
extrapolation on/off, target, fit start) applied to every rest without its own choice, and the extrapolation of THIS
rest only (target and fit start, empty = global value). Then the fit values and Accept / Accept all valid / Reject /
Return to Pending. Every value comes from gregorithm (RestOcv, ICI records); nothing is fitted or interpolated here.
"""
from __future__ import annotations
import math
from typing import Optional

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QSplitter, QVBoxLayout, QWidget)

from ..explorer import CHART
from ..gitt_ici import parse_optional_positive
from ..gitt_ici_review import ACCEPTED, INVALID, MODIFIED, PENDING, REJECTED
from .chart import _font
from .gitt_ici_chart import PALETTE, SHADE
from .gitt_ici_setup import (STATUS_KIND, FillPane, fill_scroll, button, labelled, list_panel, list_row, make_list,
                             muted, select_next, status_color, wrapped_tip)

ID_ROLE = Qt.ItemDataRole.UserRole
COLORS = {key: PALETTE[role] for key, role in (("raw", "data"), ("sampled", "sampled"), ("fit", "fit"),
                                               ("extrapolated", "extrapolated"), ("pseudo", "pseudo"))}
STATE_MARK = {PENDING: "● Pending", ACCEPTED: "● Accepted", MODIFIED: "● Modified", INVALID: "● Invalid",
              REJECTED: "● Rejected"}


def _v(value: Optional[float]) -> str:
    return "N/D" if value is None else f"{value:.5f}"


def _fraction(text: str) -> Optional[float]:
    """Fit start fraction typed by the operator (comma or point), or None when it is not a number in [0, 1)."""
    try:
        value = float(text.strip().replace(",", "."))
    except ValueError:
        return None
    return value if 0 <= value < 1 else None


def _short(value: Optional[float]) -> str:
    """List value: 3 significant digits, scientific notation when longer (full values in the detail and export)."""
    return "—" if value is None else f"{value:.3g}"


class OcvChart(QWidget):
    """V vs time of one rest; clicking a real sample emits its source row."""
    sampleClicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ChartSurface")
        self.setStyleSheet(f"#ChartSurface {{ background: {CHART['bg']}; border: 1px solid {CHART['frame']};"
                           " border-radius: 10px; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.plot = pg.PlotWidget()
        self.plot.setBackground(CHART["bg"])
        self.plot.setMinimumHeight(260)
        self.pi = self.plot.getPlotItem()
        self.pi.setMenuEnabled(False)
        self.pi.showGrid(x=True, y=True, alpha=0.3)
        self.pi.setLabel("bottom", "Time from the last sample under current [s]")
        self.pi.setLabel("left", "Voltage [V]")
        for name in ("bottom", "left"):
            ax = self.pi.getAxis(name)
            ax.setPen(pg.mkPen(CHART["frame"]))
            ax.setTextPen(pg.mkPen(CHART["tick"]))
            ax.setTickFont(_font(10))
        lay.addWidget(self.plot, 1)
        self.legend = QLabel()                              # legend under the plot: it never covers the data
        self.legend.setTextFormat(Qt.TextFormat.RichText)
        self.legend.setWordWrap(True)
        self.legend.setStyleSheet(f"color: {CHART['tick']}; font-size: 9pt;")
        lay.addWidget(self.legend)
        self.editable = True
        self.marks: dict[str, object] = {}

    def show_rest(self, rest, extrapolation: bool, pseudo: Optional[float], log_x: bool) -> None:
        """Draw one gregorithm RestOcv (see the module docstring)."""
        self.pi.setLogMode(x=False, y=False)
        self.pi.clear()
        self.marks = {}
        self.legend.setText("")
        if rest is None or not rest.samples:
            return
        origin = rest.reference.time_s if rest.reference is not None else rest.samples[0].time_s
        pts = [(s.source_row, s.time_s - origin, s.voltage_v) for s in rest.samples]
        if log_x:
            pts = [p for p in pts if p[1] > 0]
        # PlotDataItem everywhere: unlike a bare scatter it follows the logarithmic time axis.
        samples = pg.PlotDataItem([t for _r, t, _v in pts], [v for _r, _t, v in pts], pen=None, symbol="o",
                                  symbolSize=6, symbolPen=None, symbolBrush=COLORS["raw"], name="Rest samples",
                                  data=[r for r, _t, _v in pts])
        samples.sigPointsClicked.connect(self._clicked)
        self.pi.addItem(samples)
        xs = [t for _r, t, _v in pts]
        ys = [v for _r, _t, v in pts]
        if rest.sampled is not None:
            sample = next((s for s in rest.samples if s.source_row == rest.sampled.source_row), None)
            if sample is not None:
                t = sample.time_s - origin
                self.marks["sampled"] = sample.source_row
                if t > 0 or not log_x:
                    self.pi.addItem(pg.PlotDataItem([t], [sample.voltage_v], pen=None, symbol="d", symbolSize=15,
                                                    symbolPen=pg.mkPen(COLORS["sampled"], width=2),
                                                    symbolBrush=COLORS["sampled"],
                                                    name="Sampled OCV" + (" (manual)" if rest.manual_sample else "")))
        fit = rest.fit if extrapolation else None
        if fit is not None:
            self.pi.addItem(pg.LinearRegionItem((math.log10(fit.fit_start_s) if log_x else fit.fit_start_s,
                                                 math.log10(fit.fit_end_s) if log_x else fit.fit_end_s),
                                                movable=False, brush=pg.mkBrush(*SHADE), pen=pg.mkPen(None)))
            lo, hi = fit.fit_start_s, max(fit.target_time_s, fit.fit_end_s)
            n = 160
            ts = [lo * (hi / lo) ** (i / (n - 1)) for i in range(n)]
            self.pi.plot(ts, [fit.alpha_v + fit.beta_v * math.log(t) for t in ts],
                         pen=pg.mkPen(COLORS["fit"], width=2, style=Qt.PenStyle.DashLine),
                         name="LOG_TIME_2P fit (gregorithm)")
            self.marks["extrapolated"] = fit.target_time_s
            self.pi.addItem(pg.PlotDataItem([fit.target_time_s], [fit.target_voltage_v], pen=None, symbol="star",
                                            symbolSize=16, symbolPen=pg.mkPen(COLORS["extrapolated"], width=1.5),
                                            symbolBrush=COLORS["extrapolated"],
                                            name=f"Extrapolated OCV at {fit.target_time_s:g} s"))
            xs.append(fit.target_time_s)
            ys.append(fit.target_voltage_v)
        if pseudo is not None:
            self.marks["pseudo"] = pseudo
            line = pg.InfiniteLine(pseudo, angle=0, pen=pg.mkPen(COLORS["pseudo"], style=Qt.PenStyle.DotLine),
                                   label="Pseudo-OCV (ICI intercept, not equilibrium)",
                                   labelOpts={"position": 0.25, "color": COLORS["pseudo"]})
            self.pi.addItem(line)
            ys.append(pseudo)
        self.pi.setLogMode(x=log_x, y=False)
        for item in self.pi.listDataItems():
            item.setLogMode(log_x, False)
        entries = [(COLORS["raw"], "●", "Rest samples (click one to use it)")]
        if "sampled" in self.marks:
            entries.append((COLORS["sampled"], "◆", "Sampled OCV" + (" (manual)" if rest.manual_sample else "")))
        if "extrapolated" in self.marks:
            entries += [(COLORS["fit"], "- -", "LOG_TIME_2P fit (gregorithm)"), (PALETTE["fit"], "■", "Fit interval"),
                        (COLORS["extrapolated"], "★", f"Extrapolated OCV at {self.marks['extrapolated']:g} s")]
        if "pseudo" in self.marks:
            entries.append((COLORS["pseudo"], "···", "Pseudo-OCV (ICI intercept, not equilibrium)"))
        self.legend.setText(" &nbsp;&nbsp; ".join(f"<span style='color:{c}'>{sym}</span> {text}"
                                                  for c, sym, text in entries))
        if xs and ys:
            x0, x1 = (min(xs), max(xs))
            if log_x:
                x0, x1 = math.log10(max(min(x for x in xs if x > 0), 1e-9)), math.log10(max(x1, 1e-9))
            y0, y1 = min(ys), max(ys)
            pad = max((y1 - y0) * 0.08, 1e-4)
            self.pi.setXRange(x0, x1, padding=0.04)
            self.pi.setYRange(y0 - pad, y1 + pad, padding=0)

    def _clicked(self, _item, points, _ev=None) -> None:
        if self.editable and len(points):
            self.sampleClicked.emit(int(points[0].data()))


class OcvView(QWidget):
    """Rest list + OCV chart + controls. Signals carry the operator's requests to the page/controller."""
    restSelected = Signal(int)
    sampleChosen = Signal(int, object)               # rest id, source row or None (global rule)
    sampleTimeChosen = Signal(int, float)            # rest id, sampled-OCV time [s] of that rest only
    settingsApplied = Signal(bool, float, float, object)   # extrapolation, target [s], fit start, sample time or None
    extrapolationChosen = Signal(int, object, object)      # rest id, target [s] or None, fit start or None (global)
    acceptRequested = Signal(object)                 # list of rest ids or None (all valid)
    revokeRequested = Signal(int)
    rejectRequested = Signal(int)

    def __init__(self, theme=None, parent=None):
        super().__init__(parent)
        self.theme = theme
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Orientation.Horizontal)
        left, ll = list_panel("Rest periods · OCV [V]")
        self.list = make_list([
            ("#", "Rest number in time order: rest 1 precedes the first impulse"),
            ("OCV", "Sampled OCV [V]: the last real sample of the rest"),
            ("Extr.", "Extrapolated OCV [V] at the target time"),
            ("State", "● green: accepted · ● orange: pending or modified · ● red: rejected or invalid")],
            status_column=3)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        ll.addWidget(self.list, 1)
        self.summary = muted()
        ll.addWidget(self.summary)
        split.addWidget(left)
        right = FillPane()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)
        bar = QHBoxLayout()
        self.title = QLabel("Select a rest period.")
        self.title.setStyleSheet("font-weight: 500;")
        self.state = QLabel()
        self.state.setProperty("chip", True)
        self.log_x = QCheckBox("Logarithmic time")
        bar.addWidget(self.title)
        bar.addWidget(self.state)
        bar.addStretch(1)
        bar.addWidget(self.log_x)
        rl.addLayout(bar)
        self.chart = OcvChart()
        self.chart.setMinimumHeight(300)
        rl.addWidget(self.chart, 1)
        self.hint = muted()
        rl.addWidget(self.hint)
        strip = self._strip()
        rl.addWidget(strip)
        scroll = fill_scroll(right, "OcvBody")
        split.addWidget(scroll)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 1200])                         # list: LIST_WIDTH
        lay.addWidget(split)
        self._rest: Optional[int] = None
        self._loading = False
        self._list_key = None
        self.list.currentItemChanged.connect(self._list_changed)
        self.chart.sampleClicked.connect(lambda row: self._rest is not None and self.sampleChosen.emit(self._rest, row))
        self.log_x.toggled.connect(lambda _on: self._redraw())
        self._last = None

    def _strip(self) -> QWidget:
        """Controls under the chart: three cards side by side (sampled OCV of this rest, OCV settings of all rests,
        extrapolation of this rest), the fit values in full width, then the acceptance row. Nothing shares a line with
        anything else."""
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        cards = QHBoxLayout()
        cards.setSpacing(10)

        def panel(title: str, key: str) -> tuple[QFrame, QVBoxLayout]:
            frame = QFrame()
            frame.setProperty("panel", True)
            lay = QVBoxLayout(frame)
            lay.setContentsMargins(12, 8, 12, 10)
            lay.setSpacing(6)
            lay.addWidget(labelled(title, self.theme, key, bold=True))
            return frame, lay

        def form() -> QFormLayout:
            out = QFormLayout()
            out.setContentsMargins(0, 0, 0, 0)
            return out

        def buttons(lay: QVBoxLayout, *items) -> None:
            row = QHBoxLayout()
            for b in items:
                row.addWidget(b)
            row.addStretch(1)
            lay.addStretch(1)
            lay.addLayout(row)

        frame, lay = panel("Sampled OCV · this rest", "ocv_sampled")
        self.sampled_text = QLabel()
        self.sampled_text.setWordWrap(True)
        self.local_time = QLineEdit()
        self.local_time.setToolTip("Sampled OCV time [s] of this rest only: the last real rest sample with t ≤ this "
                                   "time, from the last sample under current.")
        self.local_time.setAccessibleName("Sampled OCV time of this rest [s]")
        self.time_apply = button("Apply to this rest", "ghost")
        self.last_sample = button("Use global rule", "ghost")
        self.last_sample.setToolTip("Drop the time or the sample chosen for this rest and use the rule of all rests.")
        lay.addWidget(self.sampled_text)
        f = form()
        f.addRow("Sampled at [s]", self.local_time)
        lay.addLayout(f)
        buttons(lay, self.time_apply, self.last_sample)
        cards.addWidget(frame, 1)

        frame, lay = panel("OCV settings · all rests", "ocv_extrapolation")
        self.sample_time = QLineEdit()
        self.sample_time.setPlaceholderText("last sample")
        self.sample_time.setToolTip("Sampled OCV time [s] of every rest without its own choice; empty = the last "
                                    "rest sample.")
        self.sample_time.setAccessibleName("Sampled OCV time of all rests [s]")
        self.extrapolate = QCheckBox("Extrapolate OCV")
        self.global_target, self.fit_start = QLineEdit(), QLineEdit()
        self.global_target.setToolTip("Global relaxation target [s] for every rest without a local target.")
        self.fit_start.setToolTip("Start of the LOG_TIME_2P fit as a fraction of the observed rest (0.2 = last 80 %).")
        f = form()
        f.addRow("Sampled at [s]", self.sample_time)
        f.addRow(self.extrapolate)
        f.addRow("Target [s]", self.global_target)
        f.addRow("Fit start (fraction)", self.fit_start)
        lay.addLayout(f)
        self.apply_settings = button("Apply to all rests", "run")
        buttons(lay, self.apply_settings)
        cards.addWidget(frame, 1)

        frame, lay = panel("Extrapolation · this rest", "ocv_local")
        self.local_target, self.local_fit = QLineEdit(), QLineEdit()
        self.local_target.setToolTip("Target [s] of this rest only; empty = the global target.")
        self.local_fit.setToolTip("Fit start fraction of this rest only; empty = the global fit start.")
        self.local_target.setAccessibleName("Extrapolation target of this rest [s]")
        self.local_fit.setAccessibleName("Fit start fraction of this rest")
        self.local_apply = button("Apply to this rest", "ghost")
        self.local_reset = button("Use global settings", "ghost")
        f = form()
        f.addRow("Target [s]", self.local_target)
        f.addRow("Fit start (fraction)", self.local_fit)
        lay.addLayout(f)
        buttons(lay, self.local_apply, self.local_reset)
        cards.addWidget(frame, 1)
        col.addLayout(cards)

        values = QFrame()
        values.setProperty("panel", True)
        vl = QVBoxLayout(values)
        vl.setContentsMargins(12, 8, 12, 8)
        self.fit_text = muted()
        self.fit_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        vl.addWidget(self.fit_text)
        col.addWidget(values)

        row = QHBoxLayout()
        self.revoke = button("Return to Pending", "ghost")
        self.reject = button("Reject", "ghost")
        self.reject.setToolTip("Exclude this rest by hand: the D using its OCV are not used.")
        self.accept_all = button("Accept all valid", "ghost")
        self.accept = button("Accept", "goElab")
        row.addWidget(labelled("Acceptance", self.theme, "ocv_accept"))
        row.addStretch(1)
        for b in (self.revoke, self.reject, self.accept_all, self.accept):
            row.addWidget(b)
        col.addLayout(row)
        self.last_sample.clicked.connect(lambda: self._rest is not None and self.sampleChosen.emit(self._rest, None))
        self.time_apply.clicked.connect(self._apply_time)
        self.local_time.returnPressed.connect(self._apply_time)
        self.apply_settings.clicked.connect(self._apply_settings)
        self.local_apply.clicked.connect(self._apply_local)
        self.local_reset.clicked.connect(
            lambda: self._rest is not None and self.extrapolationChosen.emit(self._rest, None, None))
        self.accept.clicked.connect(self._accept_current)
        self.accept_all.clicked.connect(lambda: self.acceptRequested.emit(None))
        self.revoke.clicked.connect(lambda: self._rest is not None and self.revokeRequested.emit(self._rest))
        self.reject.clicked.connect(lambda: self._rest is not None and self.rejectRequested.emit(self._rest))
        return holder

    # --- requests --------------------------------------------------------------------------------------------------
    def _accept_current(self) -> None:
        """Accept the open rest, then open the next one in the list."""
        if self._rest is not None:
            self.acceptRequested.emit([self._rest])
            select_next(self.list)

    def _apply_settings(self) -> None:
        target, err_t = parse_optional_positive(self.global_target.text())
        sample_time, err_s = parse_optional_positive(self.sample_time.text())
        fraction = _fraction(self.fit_start.text())
        if err_t or err_s or target is None or fraction is None:
            self.fit_text.setText("Enter a target greater than 0 s, a fit start between 0 and 1 (excluded) and a "
                                  "sampled time greater than 0 s (or empty for the last sample).")
            return
        self.settingsApplied.emit(self.extrapolate.isChecked(), target, fraction, sample_time)

    def _apply_time(self) -> None:
        """Sampled OCV time of this rest only; an empty field returns to the rule of all rests."""
        if self._rest is None:
            return
        seconds, err = parse_optional_positive(self.local_time.text())
        if err:
            self.fit_text.setText("Enter a sampled OCV time greater than 0 s, or leave it empty for the global rule.")
            return
        if seconds is None:
            self.sampleChosen.emit(self._rest, None)
        else:
            self.sampleTimeChosen.emit(self._rest, seconds)

    def _apply_local(self) -> None:
        """Target and fit start of this rest only; an empty field keeps the global value."""
        target, err = parse_optional_positive(self.local_target.text())
        text = self.local_fit.text().strip()
        fraction = _fraction(text) if text else None
        if err or (text and fraction is None):
            self.fit_text.setText("Enter a local target greater than 0 s and a fit start between 0 and 1 (excluded), "
                                  "or leave a field empty for the global value.")
            return
        if self._rest is not None:
            self.extrapolationChosen.emit(self._rest, target, fraction)

    def _list_changed(self, current, _previous) -> None:
        if not self._loading and current is not None:
            self.restSelected.emit(current.data(0, ID_ROLE))

    # --- data ------------------------------------------------------------------------------------------------------
    def set_data(self, rests, states: dict, pseudo: dict, positions: dict, open_rest: Optional[int], params,
                 overrides: dict, running: bool, fit_overrides: Optional[dict] = None,
                 samples: Optional[dict] = None) -> None:
        """Show every rest (``positions``: rest id -> text such as 'after #3'), the open rest and the controls.
        ``overrides`` / ``fit_overrides`` are the per-rest targets and fit starts, ``samples`` the per-rest
        sampled-OCV choices (source row or ('time', T))."""
        fit_overrides, samples = fit_overrides or {}, samples or {}
        key = (getattr(getattr(self.theme, "tokens", None), "name", ""),
               tuple((r.rest_segment_id, _v(r.sampled.voltage_v if r.sampled else None),
                      _v(r.extrapolated.voltage_v if r.extrapolated else None), states.get(r.rest_segment_id),
                      positions.get(r.rest_segment_id)) for r in rests))
        self._numbers = {r.rest_segment_id: n for n, r in enumerate(rests, start=1)}
        if key != self._list_key:
            self._list_key = key
            self._loading = True
            try:
                self.list.clear()
                for number, r in enumerate(rests, start=1):
                    state = states.get(r.rest_segment_id)
                    problems = [x for x in (r.sampled_reason, r.extrapolated_reason) if x and x != "not_requested"]
                    where = positions.get(r.rest_segment_id, "")
                    tip = "\n".join(t for t in (f"Rest {number}" + (f" · {where.replace('#', 'impulse #')}" if where
                                                                   else ""), state or "", *problems) if t)
                    item = list_row([str(number), _short(r.sampled.voltage_v if r.sampled else None),
                                     _short(r.extrapolated.voltage_v if r.extrapolated else None), ""],
                                    (3, STATUS_KIND.get(state, "none"), tip), self.theme)
                    item.setData(0, ID_ROLE, r.rest_segment_id)
                    for col in range(3):
                        item.setToolTip(col, wrapped_tip(tip))
                    self.list.addTopLevelItem(item)
            finally:
                self._loading = False
        counts = {s: list(states.values()).count(s) for s in (PENDING, ACCEPTED, MODIFIED, INVALID, REJECTED)}
        self.summary.setText(f"{counts[ACCEPTED]} accepted · {counts[PENDING] + counts[MODIFIED]} pending · "
                             f"{counts[REJECTED] + counts[INVALID]} rejected or invalid")
        self._loading = True
        try:
            for i in range(self.list.topLevelItemCount()):
                item = self.list.topLevelItem(i)
                if item.data(0, ID_ROLE) == open_rest and self.list.currentItem() is not item:
                    self.list.setCurrentItem(item)
        finally:
            self._loading = False
        locked = "relaxation_extrapolated" in params.ocv_methods
        if not self.global_target.hasFocus():
            self.global_target.setText(f"{params.ocv_target_s:g}")
        if not self.fit_start.hasFocus():
            self.fit_start.setText(f"{params.ocv_fit_start_fraction:g}")
        if not self.sample_time.hasFocus():
            self.sample_time.setText("" if params.ocv_sample_time_s is None else f"{params.ocv_sample_time_s:g}")
        self.extrapolate.blockSignals(True)
        self.extrapolate.setChecked(params.extrapolation_active)
        self.extrapolate.blockSignals(False)
        self.extrapolate.setEnabled(not locked and not running)
        self.extrapolate.setToolTip("Always on: Extrapolated OCV is one of the OCV methods of the D results." if locked else
                                    "Show the extrapolated OCV of every rest for comparison.")
        for w in (self.global_target, self.fit_start, self.sample_time, self.apply_settings):
            w.setEnabled(not running)
        rest = next((r for r in rests if r.rest_segment_id == open_rest), None)
        self._rest = None if rest is None else rest.rest_segment_id
        state = states.get(self._rest)
        self.chart.editable = not running
        editable = rest is not None and not running
        for w in (self.local_time, self.time_apply):
            w.setEnabled(editable)
        self.last_sample.setEnabled(editable and self._rest in samples)
        for w in (self.local_target, self.local_fit, self.local_apply):
            w.setEnabled(editable and params.extrapolation_active)
        self.local_reset.setEnabled(editable and (self._rest in overrides or self._rest in fit_overrides))
        self.accept.setEnabled(state in (PENDING, MODIFIED, REJECTED) and not running)
        self.revoke.setEnabled(state in (ACCEPTED, MODIFIED, REJECTED) and not running)
        self.reject.setEnabled(rest is not None and state != REJECTED and not running)
        self.accept_all.setEnabled(any(s in (PENDING, MODIFIED) for s in states.values()) and not running)
        if rest is None:
            self.title.setText("Select a rest period." if rests else "Recognise impulses first (Setup → Proceed).")
            self.state.setText("")
            self.sampled_text.setText("")
            self.fit_text.setText("")
            self.chart.show_rest(None, False, None, False)
            self._last = None
            return
        where = positions.get(rest.rest_segment_id, "").replace("#", "impulse #")
        self.title.setText(f"Rest {self._numbers.get(rest.rest_segment_id, '')}" + (f" · {where}" if where else ""))
        self.state.setText(STATE_MARK.get(state, ""))
        self.state.setStyleSheet(f"color: {status_color(self.theme, STATUS_KIND.get(state, 'none'))};")
        if rest.sampled is not None:
            sample = next((s for s in rest.samples if s.source_row == rest.sampled.source_row), None)
            origin = rest.reference.time_s if rest.reference is not None else rest.samples[0].time_s
            t = None if sample is None else sample.time_s - origin
            choice = samples.get(rest.rest_segment_id)
            sample_time = getattr(rest, "sample_time_s", None)
            how = ("manual (exact row chosen in the chart)" if choice is not None and not isinstance(choice, tuple)
                   else f"last rest sample with t ≤ {sample_time:g} s"
                   + (" (this rest)" if isinstance(choice, tuple) else " (all rests)") if sample_time is not None
                   else "last real sample of the rest")
            self.sampled_text.setText(f"{rest.sampled.voltage_v:.6g} V at t = {t:.4g} s · row "
                                      f"{rest.sampled.source_row} · {how}")
        else:
            self.sampled_text.setText(f"Not available: {rest.sampled_reason}")
        choice = samples.get(rest.rest_segment_id)
        self.local_time.setPlaceholderText(
            f"row {choice}" if choice is not None and not isinstance(choice, tuple) else
            f"global {params.ocv_sample_time_s:g}" if params.ocv_sample_time_s is not None else "last sample")
        if not self.local_time.hasFocus():
            self.local_time.setText(f"{choice[1]:g}" if isinstance(choice, tuple) else "")
        self.local_target.setPlaceholderText(f"global {params.ocv_target_s:g}")
        self.local_fit.setPlaceholderText(f"global {params.ocv_fit_start_fraction:g}")
        if not self.local_target.hasFocus():
            target = overrides.get(rest.rest_segment_id)
            self.local_target.setText("" if target is None else f"{target:g}")
        if not self.local_fit.hasFocus():
            fraction = fit_overrides.get(rest.rest_segment_id)
            self.local_fit.setText("" if fraction is None else f"{fraction:g}")
        fit = rest.fit
        lines = []
        if params.extrapolation_active:
            if fit is None:
                lines.append(f"Extrapolation not available: {rest.extrapolated_reason}")
            else:
                source = "local" if rest.rest_segment_id in overrides else "global"
                start = "local" if rest.rest_segment_id in fit_overrides else "global"
                lines.append(f"Extrapolated OCV {fit.target_voltage_v:.6g} V at {fit.target_time_s:g} s ({source} "
                             f"target, {start} fit start) · V = α + β·ln(t/1 s), α = {fit.alpha_v:.6g} V, β = {fit.beta_v:.4g} V · fit "
                             f"{fit.fit_start_s:.4g}–{fit.fit_end_s:.4g} s, {fit.sample_count} samples, RMSE "
                             f"{fit.rmse_v * 1e3:.3g} mV"
                             + ("" if fit.r_squared is None else f", R² {fit.r_squared:.4f}"))
                if fit.extrapolation_ratio > 1:
                    lines.append(f"The target is {fit.extrapolation_ratio:.1f}× the observed rest: allowed, but far "
                                 "from the data.")
        else:
            lines.append("Extrapolation is off: tick Extrapolate OCV and apply to compute it for every rest.")
        if self._rest in pseudo:
            lines.append(f"Pseudo-OCV (ICI intercept, not equilibrium): {pseudo[self._rest][1]:.6g} V.")
        self.fit_text.setText("\n".join(lines))
        self.hint.setText("Click a rest sample, or type a time below, to choose the sampled OCV of this rest. "
                          "Changing it re-evaluates the acceptance of this rest and recalculates only the results that "
                          "use its sampled OCV.")
        self._last = (rest, params.extrapolation_active, pseudo.get(self._rest, (None, None))[1])
        self._redraw()

    def _redraw(self) -> None:
        if self._last is not None:
            rest, extrapolation, pseudo = self._last
            self.chart.show_rest(rest, extrapolation, pseudo, self.log_x.isChecked())
