"""GITT / ICI IR drop analysis view: pulse list, pre-pulse and post-pulse inspection charts, sample times, acceptance.

Laid out like Pulse Fitting. Left: every recognised impulse with its pre-pulse and post-pulse IR drop (ΔV in mV,
distinct columns) and its review state (Pending, Accepted, Modified after acceptance, Rejected, Invalid); selecting
one updates the whole view. Centre: the two charts ALWAYS side by side (pre-pulse left, post-pulse right, each half
of the width, whatever the window size), each zoomed on its transition and drawing the real canonical samples
(V vs time from the side's reference):
  * the reference sample (square) and the measured sample (diamond) that gregorithm used, matched by source row;
  * the reference time (dashed line), the switching interval between the reference and the first sample of the
    next segment (shaded: the exact switching instant lies somewhere inside it), and the sample-time target
    T (dotted line) of the automatic rule "last sample with t ≤ T";
  * the computed ΔV as a vertical segment from V_ref to V_meas.
Below each chart: rows, times, voltages, currents, ΔV, ΔI and R of that side, and how the sample was chosen.

Below the charts, one sample-times card as in DCIR: the pre-pulse and post-pulse fields show the times the open
impulse uses (its own ones, or the global ones); Apply keeps them for this impulse only, Apply to All makes them the
global times of every impulse, Use global times (shown when the impulse has its own times or clicked samples) removes
them. Then the acceptance row (Return to Pending, Reject, Accept all valid, Accept).

Manual selection reuses the gregorithm rule that a click keeps the exact row identity: clicking a real sample
of the measured segment emits ``sampleChosen(event_id, side, row)``. Nothing is computed here: values come from
gregorithm.measure_ir_drops through the controller, review states from gitt_ici_review.ir_state.
"""
from __future__ import annotations
from typing import Optional

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSizePolicy, QSplitter, QVBoxLayout, QWidget)

from ..explorer import CHART
from ..gitt_ici import parse_optional_positive
from ..gitt_ici_ir import SIDE_LABELS, SIDE_SYMBOLS, SIDES, drop_cells, ir_rows, zoom_range
from ..gitt_ici_review import ACCEPTED, INVALID, MODIFIED, PENDING, REJECTED
from .chart import _font
from .gitt_ici_chart import PALETTE, SHADE
from .controls import ElidedLabel, FlowLayout, WrapLabel, group
from .gitt_ici_setup import (LIST_WIDTH, STATUS_KIND, FillPane, button, fill_scroll, info, labelled, list_panel,
                             list_row, make_list, muted, select_next, status_color, wrapped_tip)

ID_ROLE = Qt.ItemDataRole.UserRole
STATE_MARK = {PENDING: "● Pending", ACCEPTED: "● Accepted", MODIFIED: "● Modified", INVALID: "● Invalid",
              REJECTED: "● Rejected"}
STATE_TIPS = {PENDING: "Measured, not yet accepted: its R_on / R_off are not used until you accept them.",
              ACCEPTED: "Accepted: its R_on / R_off are drawn in Diffusion Results and exported.",
              MODIFIED: "Changed after acceptance (new sample or time): accept it again to use the new values.",
              INVALID: "No resistance on either side, or not measured: it cannot be accepted.",
              REJECTED: "Rejected by hand: its R_on / R_off are not used. Return to Pending or Accept to undo."}
# Shared by the pre-pulse and post-pulse charts; rows and values of each side are in the table under its chart.
LEGEND = ("■ reference sample · ◆ measured sample · grey: before the switch · blue: after the switch, click one to "
          "use it · shaded: where the switch happened")
COLORS = {"context": PALETTE["context"], "candidate": PALETTE["data"], "reference": PALETTE["fit"],
          "measured": PALETTE["measured"], "delta": PALETTE["delta"], "target": PALETTE["target"]}


PLOT_MIN_H = 150               # px; the charts grow with the window, the page scrolls only below this
PAIR_CELLS = 430               # px of one chart: from here the short values sit two per line
WIDE_CELLS = 620               # px of one chart: from here the long sample values also sit side by side


class IrDropChart(QWidget):
    """V-vs-time zoom on one side of one impulse; clicking a sample of the measured segment emits its row. Its
    minimum width is small on purpose, so the two charts of the view always fit side by side."""
    sampleClicked = Signal(int)

    def __init__(self, side: str, parent=None):
        super().__init__(parent)
        self.side = side
        # The width hint of the value cells is ignored, so the two charts always split the row in equal halves; the
        # cells wrap instead of widening one chart.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(220)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        bar = QHBoxLayout()
        self.title = ElidedLabel(f"{SIDE_LABELS[side]} IR drop ({SIDE_SYMBOLS[side]})")   # "…" + tooltip if narrow
        self.title.setStyleSheet("font-weight: 500;")
        bar.addWidget(self.title)
        bar.addStretch(1)
        self.reset = QPushButton("Reset view")
        self.reset.setProperty("ghost", True)
        bar.addWidget(self.reset)
        lay.addLayout(bar)
        self.plot = pg.PlotWidget()
        self.plot.setBackground(CHART["bg"])
        self.plot.setMinimumHeight(PLOT_MIN_H)
        self.plot.setMinimumWidth(160)
        self.pi = self.plot.getPlotItem()
        self.pi.setMenuEnabled(False)
        self.pi.showGrid(x=True, y=True, alpha=0.3)
        self.pi.setLabel("bottom", "Time from the reference sample [s]")
        self.pi.setLabel("left", "Voltage [V]")
        for name in ("bottom", "left"):
            ax = self.pi.getAxis(name)
            ax.setPen(pg.mkPen(CHART["frame"]))
            ax.setTextPen(pg.mkPen(CHART["tick"]))
            ax.setTickFont(_font(10))
        surface = QWidget()
        surface.setObjectName("ChartSurface")                 # fixed chart palette in both app themes
        surface.setStyleSheet(f"#ChartSurface {{ background: {CHART['bg']}; border: 1px solid {CHART['frame']};"
                              " border-radius: 10px; }")
        sl = QVBoxLayout(surface)
        sl.setContentsMargins(6, 6, 6, 6)
        sl.addWidget(self.plot)
        lay.addWidget(surface, 1)
        self.message = WrapLabel()                    # wrapped texts keep all their lines when the view is narrow
        self.message.setProperty("muted", True)
        lay.addWidget(self.message)
        self.message.hide()                           # takes no room until there is something to say
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(1)
        self.cells: list[tuple[QLabel, QLabel]] = []
        for i in range(8):
            key, value = WrapLabel(), WrapLabel()
            key.setProperty("muted", True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.cells.append((key, value))
        self.grid.setColumnStretch(1, 1)
        self.grid.setColumnStretch(3, 1)
        self._cell_mode = -1
        self._place_cells(1)
        lay.addLayout(self.grid)
        self._range: Optional[tuple] = None
        self.marks: dict[str, int] = {}             # 'reference'/'measured' -> source row drawn (for tests)
        self.editable = True
        self.reset.clicked.connect(self.reset_view)

    def _place_cells(self, mode: int) -> None:
        """Place the value cells compactly so the whole area fits on one screen. ``mode`` 0 (very narrow chart): one
        value per line; 1: the short values (ΔV, currents, ΔI, R) two per line; 2 (wide): the two samples side by
        side as well. The selection rule and the reason always take the full width. Does nothing when the
        arrangement is already the requested one."""
        if mode == self._cell_mode:
            return
        self._cell_mode = mode
        if mode == 0:
            places = [(i, 0, 3) for i in range(len(self.cells))]
        else:
            places = [(0, 0, 1), (0, 2, 1)] if mode == 2 else [(0, 0, 3), (1, 0, 3)]
            places += [(2, 0, 1), (3, 0, 1), (2, 2, 1), (3, 2, 1), (6, 0, 3), (7, 0, 3)]
        for (key, value), (row, column, span) in zip(self.cells, places):
            self.grid.addWidget(key, row, column, Qt.AlignmentFlag.AlignTop)
            self.grid.addWidget(value, row, column + 1, 1, span, Qt.AlignmentFlag.AlignTop)

    def resizeEvent(self, event) -> None:  # noqa: N802  (Qt override)
        """Rearrange the value cells for the new width."""
        super().resizeEvent(event)
        self._place_cells(2 if self.width() >= WIDE_CELLS else 1 if self.width() >= PAIR_CELLS else 0)

    def _say(self, text: str) -> None:
        """Explanation under the chart; hidden when empty so it takes no room."""
        self.message.setText(text)
        self.message.setVisible(bool(text))

    def clear(self, text: str) -> None:
        """Empty chart with an explanation."""
        self.pi.clear()
        self._range, self.marks = None, {}
        self._say(text)
        self._fill_cells([])

    def show_drop(self, drop, reference_segment, measured_segment, editable: bool = True) -> None:
        """Draw one side: context samples of the reference segment, clickable samples of the measured segment, the
        samples gregorithm used (matched by source row), the switching interval, the target and ΔV. Clicks are
        ignored while ``editable`` is False (a calculation is running)."""
        self.pi.clear()
        self.marks = {}
        self.editable = editable
        self._fill_cells(drop_cells(drop))
        if drop.reference is None:
            self._range = None
            self._say("No reference sample: nothing to draw.")
            return
        origin = drop.reference.time_s
        times = tuple(reference_segment.time_s) + tuple(measured_segment.time_s)
        rng = zoom_range(times, drop)
        self._range = rng
        lo, hi = rng
        pen_none = pg.mkPen(None)

        def every(seg):
            """All samples of a segment: the zoom only sets the initial view, panning shows the rest."""
            return [(r, t - origin, v) for r, t, v in zip(seg.rows, seg.time_s, seg.voltage_v)]

        context, candidates = every(reference_segment), every(measured_segment)
        if context:
            self.pi.addItem(pg.ScatterPlotItem([t for _r, t, _v in context], [v for _r, _t, v in context], size=5,
                                               pen=pen_none, brush=pg.mkBrush(COLORS["context"])))
        scatter = pg.ScatterPlotItem(size=7, pen=pen_none, brush=pg.mkBrush(COLORS["candidate"]), hoverable=True,
                                     hoverSize=11, tip=None)
        scatter.addPoints([{"pos": (t, v), "data": r} for r, t, v in candidates])
        scatter.sigClicked.connect(self._clicked)
        self.pi.addItem(scatter)
        if measured_segment.time_s:
            first = measured_segment.time_s[0] - origin
            self.pi.addItem(pg.LinearRegionItem((0.0, first), movable=False,
                                                brush=pg.mkBrush(*SHADE), pen=pen_none))
        self.pi.addItem(pg.InfiniteLine(0.0, angle=90, pen=pg.mkPen(COLORS["reference"], style=Qt.PenStyle.DashLine),
                                        label="reference", labelOpts={"position": 0.06, "color": COLORS["reference"]}))
        if not drop.manual:
            self.pi.addItem(pg.InfiniteLine(drop.target_time_s, angle=90,
                                            pen=pg.mkPen(COLORS["target"], style=Qt.PenStyle.DotLine),
                                            label=f"T = {drop.target_time_s:g} s"
                                            + (" (this impulse)" if drop.local_time else ""),
                                            labelOpts={"position": 0.94, "color": COLORS["target"]}))
        ref = drop.reference
        self.pi.addItem(pg.ScatterPlotItem([0.0], [ref.voltage_v], size=13, symbol="s",
                                           pen=pg.mkPen(COLORS["reference"], width=2), brush=pg.mkBrush(None)))
        self.marks["reference"] = ref.source_row
        meas = drop.measured
        if meas is not None:
            self.marks["measured"] = meas.source_row
            self.pi.addItem(pg.ScatterPlotItem([meas.relative_time_s], [meas.voltage_v], size=14, symbol="d",
                                               pen=pg.mkPen(COLORS["measured"], width=2),
                                               brush=pg.mkBrush(COLORS["measured"])))
            self.pi.addItem(pg.PlotDataItem([0.0, meas.relative_time_s], [ref.voltage_v, ref.voltage_v],
                                            pen=pg.mkPen(COLORS["delta"], style=Qt.PenStyle.DashLine)))
            self.pi.addItem(pg.PlotDataItem([meas.relative_time_s, meas.relative_time_s],
                                            [ref.voltage_v, meas.voltage_v], pen=pg.mkPen(COLORS["delta"], width=2)))
            text = pg.TextItem(f"ΔV = {(meas.voltage_v - ref.voltage_v) * 1e3:+.4g} mV", color=COLORS["delta"],
                               anchor=(0, 0.5))
            text.setPos(meas.relative_time_s, (ref.voltage_v + meas.voltage_v) / 2)
            self.pi.addItem(text)
        self._say("" if meas is not None else "No measured sample: no IR drop on this side.")
        self._view_y = [v for _r, t, v in context + candidates if lo - 1e-9 <= t <= hi + 1e-9]
        self.reset_view()

    def reset_view(self) -> None:
        """Zoom back to the transition region chosen for this side."""
        if self._range is None:
            return
        lo, hi = self._range
        self.pi.setXRange(lo, hi, padding=0.02)
        ys = getattr(self, "_view_y", [])
        if ys:
            y0, y1 = min(ys), max(ys)
            pad = max((y1 - y0) * 0.12, 1e-4)
            self.pi.setYRange(y0 - pad, y1 + pad, padding=0)

    def _clicked(self, _item, points, _ev=None) -> None:
        if self.editable and len(points):
            self.sampleClicked.emit(int(points[0].data()))

    def _fill_cells(self, cells) -> None:
        for i, (key, value) in enumerate(self.cells):
            if i < len(cells):
                key.setText(cells[i][0])
                value.setText(cells[i][1])
                key.show()
                value.show()
            else:
                key.hide()
                value.hide()


class IrDropView(QWidget):
    """Pulse list + the two side-by-side inspection charts + sample times + acceptance. Signals go to the page,
    which forwards them to the controller."""
    eventSelected = Signal(str)
    sampleChosen = Signal(str, str, object)          # event id, side, source row (a clicked sample)
    timesApplied = Signal(float, object)            # pre-pulse time [s], post-pulse time [s] or None (same)
    localTimesChosen = Signal(str, object)          # event id, {side: local time [s] or None (global)}
    acceptRequested = Signal(object)                # list of event ids or None (all valid)
    revokeRequested = Signal(str)
    rejectRequested = Signal(str)

    def __init__(self, theme=None, parent=None):
        super().__init__(parent)
        self.theme = theme
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Orientation.Horizontal)
        left, ll = list_panel("Impulses · IR drop [mV]")
        self.list = make_list([
            ("#", "Impulse number (✎: a time or sample chosen for this impulse)"),
            ("Pre", "Pre-pulse IR drop [mV] (R_on side): V(pulse sample) − V(last rest sample)"),
            ("Post", "Post-pulse IR drop [mV] (R_off side): V(rest sample) − V(last sample under current)"),
            ("State", "● green: accepted · ● orange: pending or modified · ● red: rejected or invalid")],
            status_column=3)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setAccessibleName("IR drop pulse list")
        ll.addWidget(self.list, 1)
        self.summary = muted()
        ll.addWidget(self.summary)
        split.addWidget(left)
        body = FillPane()
        body.setObjectName("IrBody")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(8, 0, 4, 0)
        bl.setSpacing(5)
        head = QHBoxLayout()
        self.title = QLabel("Select an impulse in the pulse list.")
        self.title.setStyleSheet("font-weight: 500;")
        self.state = QLabel()
        self.state.setProperty("chip", True)
        head.addWidget(self.title)
        head.addWidget(self.state)
        head.addWidget(info(theme, "ir_drop"))
        head.addStretch(1)
        bl.addLayout(head)
        self.charts = {side: IrDropChart(side) for side, _l in SIDES}
        row = QHBoxLayout()                                 # always side by side, as V(t) / V(√t) in Pulse Fitting
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        for side, _l in SIDES:
            row.addWidget(self.charts[side], 1)
        bl.addLayout(row, 1)
        self.legend = QLabel(LEGEND)                       # one legend for both charts: they share every mark
        self.legend.setWordWrap(True)
        self.legend.setProperty("muted", True)
        bl.addWidget(self.legend)
        bl.addWidget(self._strip())
        for side, chart in self.charts.items():
            chart.sampleClicked.connect(lambda row, s=side: self._event and self.sampleChosen.emit(self._event, s,
                                                                                                    row))
        scroll = fill_scroll(body, "IrHolder")
        split.addWidget(scroll)
        split.setStretchFactor(1, 1)
        split.setSizes([LIST_WIDTH, 1200])
        lay.addWidget(split)
        self._event = ""
        self._drops = None
        self._globals: tuple = (None, None)       # applied global (pre-pulse, post-pulse) sample times
        self._editable = True
        self._list_key = None
        self._loading = False
        self.list.currentItemChanged.connect(self._list_changed)

    def _strip(self) -> QWidget:
        """Controls under the charts, compact so they never widen the area: ONE sample-times card (as in DCIR: the
        fields show the times the open impulse uses; Apply keeps them for this impulse, Apply to All makes them the
        times of every impulse), the usage note, then the acceptance row."""
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(5)
        self.pre_time, self.post_time = QLineEdit(), QLineEdit()
        for edit, what in ((self.pre_time, "Pre-pulse IR sample time"), (self.post_time, "Post-pulse IR sample time")):
            edit.setFixedWidth(112)                  # "= pre-pulse" placeholder fits whole
            edit.setAccessibleName(f"{what} [s]")
        self.local_apply = button("Apply", "ghost")
        self.local_apply.setToolTip("Use these times for this impulse only; an empty field keeps the global time of "
                                    "that side.")
        self.apply_times = button("Apply to All", "ghost")
        self.apply_times.setToolTip("Make these the sample times of every impulse; times and samples chosen for "
                                    "single impulses are kept (Use global times removes them).")
        self.local_reset = button("Use global times", "ghost")
        self.local_reset.setToolTip("Drop the times and the samples chosen for this impulse and use the global "
                                    "sample times again.")
        frame = QFrame()
        frame.setProperty("panel", True)
        card = FlowLayout(frame, h_spacing=14, v_spacing=6)       # one line; wraps in a narrow window
        card.setContentsMargins(12, 6, 12, 6)
        self.times_scope = muted("· all impulses")     # whose times the fields show: this impulse or all impulses
        card.addWidget(group(labelled("Sample times", self.theme, "ir_times", bold=True), self.times_scope))
        for text, edit in (("Pre-pulse [s]", self.pre_time), ("Post-pulse [s]", self.post_time)):
            card.addWidget(group(muted(text), edit))
        card.addWidget(group(self.local_apply, self.apply_times, self.local_reset, spacing=8))
        col.addWidget(frame)
        self.usage = muted()
        self.usage.setWordWrap(True)
        col.addWidget(self.usage)
        row = QHBoxLayout()
        self.note = muted()
        self.note.setWordWrap(True)
        row.addWidget(self.note, 1)
        self.revoke = button("Return to Pending", "ghost")
        self.reject = button("Reject", "ghost")
        self.reject.setToolTip("Exclude the IR drops of this impulse by hand: its R_on / R_off are not used.")
        self.accept_all = button("Accept all valid", "ghost")
        self.accept = button("Accept", "goElab")
        row.addWidget(info(self.theme, "ir_accept"))
        for b in (self.revoke, self.reject, self.accept_all, self.accept):
            row.addWidget(b)
        col.addLayout(row)
        self.local_apply.clicked.connect(self._apply_local)
        self.pre_time.returnPressed.connect(self._apply_local)
        self.post_time.returnPressed.connect(self._apply_local)
        self.local_reset.clicked.connect(
            lambda: self._event and self.localTimesChosen.emit(self._event, {"pre_pulse": None, "post_pulse": None}))
        self.apply_times.clicked.connect(self._apply_times)
        self.accept.clicked.connect(self._accept_current)
        self.accept_all.clicked.connect(lambda: self.acceptRequested.emit(None))
        self.revoke.clicked.connect(lambda: self._event and self.revokeRequested.emit(self._event))
        self.reject.clicked.connect(lambda: self._event and self.rejectRequested.emit(self._event))
        return holder

    # --- requests ----------------------------------------------------------------------------------------------------
    def _accept_current(self) -> None:
        """Accept the open impulse, then open the next one in the list."""
        if self._event:
            self.acceptRequested.emit([self._event])
            select_next(self.list)

    def _global_time(self, side: str) -> Optional[float]:
        """Global sample time of ``side`` as it is applied (an empty post-pulse time means the pre-pulse one)."""
        pre, post = self._globals
        return pre if side == "pre_pulse" or post is None else post

    def _apply_local(self) -> None:
        """Emit the local times that differ from what this impulse uses now. An empty field, or the global time
        itself, means the global time of that side, except on a side whose sample was clicked: an empty field there
        keeps the clicked sample."""
        if not self._event or self._drops is None:
            return
        changes = {}
        for side, edit in (("pre_pulse", self.pre_time), ("post_pulse", self.post_time)):
            value, err = parse_optional_positive(edit.text())
            if err:
                self.note.setText("Enter sample times greater than 0 s, or leave a field empty for the global time.")
                return
            drop = getattr(self._drops, side)
            current = drop.target_time_s if drop.local_time else None
            if value is None and drop.manual:
                continue
            if value is not None and value == self._global_time(side) and not drop.local_time and not drop.manual:
                value = None                         # the global time typed back: nothing of its own
            if value != current or (drop.manual and value is not None):
                changes[side] = value
        if changes:
            self.localTimesChosen.emit(self._event, changes)

    def _apply_times(self) -> None:
        pre, err = parse_optional_positive(self.pre_time.text())
        post, err_post = parse_optional_positive(self.post_time.text())
        if err or err_post or pre is None:
            self.note.setText("Enter times greater than 0 s (post-pulse may stay empty).")
            return
        self.timesApplied.emit(pre, post)

    def _list_changed(self, current, _previous) -> None:
        if self._loading or current is None:
            return
        self.eventSelected.emit(current.data(0, ID_ROLE))

    # --- data ------------------------------------------------------------------------------------------------------
    def set_times(self, pre_s: float, post_s, editable: bool) -> None:
        """Remember the applied global sample times and show the times of the open impulse (not while the operator
        is typing). Called after ``set_data``."""
        self._globals = (pre_s, post_s)
        self._editable = editable
        self._fill_times()

    def _fill_times(self) -> None:
        """Fill the two fields with what the open impulse uses: its own time, the clicked sample (empty field,
        'row N' placeholder) or the global time; the scope label says whose times they are."""
        item, editable = self._drops, self._editable
        own = item is not None and any(d.manual or d.local_time for d in (item.pre_pulse, item.post_pulse))
        self.times_scope.setText("· this impulse" if own else "· all impulses")
        pre_g, post_g = self._globals
        for side, edit in (("pre_pulse", self.pre_time), ("post_pulse", self.post_time)):
            drop = getattr(item, side) if item is not None else None
            if drop is not None and drop.manual:
                text = ""
                edit.setPlaceholderText(f"row {drop.measured.source_row}" if drop.measured is not None else "clicked")
            else:
                edit.setPlaceholderText("= pre-pulse" if side == "post_pulse" else "")
                if drop is not None and drop.local_time:
                    text = f"{drop.target_time_s:g}"
                else:
                    value = pre_g if side == "pre_pulse" else post_g
                    text = "" if value is None else f"{value:g}"
            if not edit.hasFocus():
                edit.setText(text)
            edit.setEnabled(editable)
        self.apply_times.setEnabled(editable)
        self.local_apply.setEnabled(editable and item is not None)
        self.local_reset.setEnabled(editable and own)
        self.local_reset.setVisible(own)

    def set_data(self, events, drops: dict, event_id: str, trace, usage: str, editable: bool,
                 states: Optional[dict] = None) -> None:
        """Show the list with review ``states`` ({event_id: state}) and, for ``event_id``, both sides drawn from
        ``trace`` (ImpulseTrace of that impulse), its local times and the acceptance buttons."""
        states = states or {}
        self._fill_list(events, drops, event_id, states)
        self._event = event_id
        self.usage.setText(usage)
        counts = {s: list(states.values()).count(s) for s in (PENDING, ACCEPTED, MODIFIED, INVALID, REJECTED)}
        self.summary.setText(f"{counts[ACCEPTED]} accepted · {counts[PENDING] + counts[MODIFIED]} pending · "
                             f"{counts[REJECTED] + counts[INVALID]} rejected or invalid")
        item = drops.get(event_id)
        self._drops = item
        event = next((e for e in events if e.event_id == event_id), None)
        number = next((i for i, e in enumerate(events, start=1) if e.event_id == event_id), None)
        state = states.get(event_id)
        self.accept.setEnabled(item is not None and state in (PENDING, MODIFIED, REJECTED) and editable)
        self.revoke.setEnabled(item is not None and state in (ACCEPTED, MODIFIED, REJECTED) and editable)
        self.reject.setEnabled(item is not None and state not in (REJECTED, None) and editable)
        self.accept_all.setEnabled(any(s in (PENDING, MODIFIED) for s in states.values()) and editable)
        self.note.setText(STATE_TIPS.get(state, ""))
        self.state.setText(STATE_MARK.get(state, ""))
        self.state.setToolTip(STATE_TIPS.get(state, ""))
        self.state.setStyleSheet(f"color: {status_color(self.theme, STATUS_KIND.get(state, 'none'))};")
        if item is None or number is None:
            self.title.setText("Select an impulse in the pulse list." if events else
                               "Recognise impulses first (Apply or Calculate all impulses).")
            for chart in self.charts.values():
                chart.clear("")
            self._editable = editable
            self._fill_times()
            return
        self.title.setText(f"Impulse #{number} · cycle {event.cycle} · half-cycle {event.half_cycle} · "
                           f"{'charge' if event.polarity > 0 else 'discharge'}")
        self._editable = editable
        self._fill_times()
        for side, chart in self.charts.items():
            drop = getattr(item, side)
            if trace is None or trace.event_id != event_id:
                chart.clear("Reading the impulse samples…")
                continue
            ref_seg, meas_seg = ((trace.rest_before, trace.pulse) if side == "pre_pulse"
                                 else (trace.pulse, trace.rest_after))
            chart.show_drop(drop, ref_seg, meas_seg, editable)

    def _fill_list(self, events, drops: dict, event_id: str, states: dict) -> None:
        rows = ir_rows(events, drops)
        key = (getattr(getattr(self.theme, 'tokens', None), 'name', ''),
               tuple((r.event_id, r.pre_mv, r.post_mv, r.valid, r.manual, states.get(r.event_id)) for r in rows))
        if key != self._list_key:
            self._list_key = key
            self._loading = True
            try:
                self.list.clear()
                for r in rows:
                    state = states.get(r.event_id)
                    tip = "\n".join(t for t in (f"Impulse #{r.number} · cycle {r.cycle} · half-cycle {r.half_cycle} · "
                                                 f"{r.branch} · {r.valid}", state or "",
                                                 "Time or sample chosen for this impulse (✎)" if r.manual else "",
                                                 r.tooltip) if t)
                    item = list_row([f"{r.number}" + (" ✎" if r.manual else ""), r.pre_mv, r.post_mv, ""],
                                    (3, STATUS_KIND.get(state, "none"), tip), self.theme)
                    item.setData(0, ID_ROLE, r.event_id)
                    for col in range(3):
                        item.setToolTip(col, wrapped_tip(tip))
                    self.list.addTopLevelItem(item)
            finally:
                self._loading = False
        self.select_event(event_id)

    def select_event(self, event_id: str) -> None:
        """Highlight ``event_id`` in the list without emitting a selection."""
        self._loading = True
        try:
            for i in range(self.list.topLevelItemCount()):
                item = self.list.topLevelItem(i)
                if item.data(0, ID_ROLE) == event_id:
                    if self.list.currentItem() is not item:
                        self.list.setCurrentItem(item)
                    break
        finally:
            self._loading = False
