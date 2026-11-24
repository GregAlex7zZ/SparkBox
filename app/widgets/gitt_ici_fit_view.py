"""GITT / ICI Pulse Fitting — dE/d√t: impulse list, V(t) beside V(√t), window editing, fit values and acceptance.

Left: every impulse with its fit state (No fit needed, Pending, Accepted, Modified after acceptance, Invalid) and a
short note of its problems. Centre: the segment fitted by the chosen series (the pulse for GITT VA and Delacourt, the
interruption after the pulse for ICI), V(t) and V(√t) side by side (as in IR Drop) with ONE shared window; the
samples inside the effective window gregorithm fitted are highlighted, the recorded √t line or Delacourt's spherical
fit is drawn, and the fits of the other checked methods of the same impulse can be overlaid, each with its own label. "Fit of" lists
one entry per distinct fit (gitt_ici_review.distinct_fits): GITT VA, ICI and Delacourt. GITT Δ has no fit of its own
and the OCV source changes only D, so series with an identical fit are merged and the OCV is named only where the fit
really differs (Delacourt fixed offset, physically guided windows).

The series chosen in "Fit of" is remembered per dataset as (method, OCV source) and stays selected when another
impulse is opened or the area is reopened; an impulse without that fit falls back to its first fit.

Below: the window bounds [s] (also editable by dragging; the recalculation starts once on release), Apply to all
impulses (the same bounds for every complete impulse, one recalculation), Re-apply automatic to this impulse,
Calculate all impulses, the fit values of every series of this impulse (D, slope, intercept, R², samples,
indicators, reason when D is missing) and Accept / Accept all valid / Return to Pending.
Recalculating a fit changes its fingerprint, so an earlier acceptance becomes "Modified". Methods without a fit
(GITT Δ) are listed as "No fit needed" and need no acceptance here.
"""
from __future__ import annotations
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..gitt_ici import (D_UNITS, ROLE_LABELS, SERIES, WINDOW_ROLE, fit_window, physical_note, review_reasons,
                        selected_series,
                        series_key_of, series_label, series_row)
from ..gitt_ici_review import (ACCEPTED, INVALID, MODIFIED, NOT_NEEDED, PENDING, REJECTED, distinct_fits,
                               fit_signature, needs_fit)
from .gitt_ici_chart import GittIciChart
from .gitt_ici_setup import (LIST_WIDTH, STATUS_KIND, FillPane, fill_scroll, button, info, list_panel, list_row, make_list, muted,
                             select_next, status_color, wrapped_tip)


def _pending_fits(params) -> list[tuple[str, str]]:
    """Checked methods with a window to set before the first calculation, one per method (the OCV source does not
    change the window); GITT Δ only when GITT VA is not checked and its IR correction uses the pulse fit."""
    out: dict[str, str] = {}
    for key, src in selected_series(params):
        if key in WINDOW_ROLE and needs_fit(key, params):
            out.setdefault(key, src)
    if "gitt_va" in out:
        out.pop("gitt_delta", None)
    return list(out.items())


ID_ROLE = Qt.ItemDataRole.UserRole
STATE_MARK = {PENDING: "● Pending", ACCEPTED: "● Accepted", MODIFIED: "● Modified", INVALID: "● Invalid",
              NOT_NEEDED: "● No fit needed", REJECTED: "● Rejected"}
STATE_TIPS = {PENDING: "Calculated, not yet accepted: its D is not used until you accept it.",
              ACCEPTED: "Accepted: its D is used in the results.",
              MODIFIED: "Recalculated after acceptance: accept it again to use the new D.",
              INVALID: "Incomplete, not calculated, out of date or every fit failed: it cannot be accepted.",
              NOT_NEEDED: "The checked methods use no fit for this impulse.",
              REJECTED: "Rejected by hand: none of its D is used. Return to Pending or Accept to undo."}


def _statistical(record, row) -> str:
    """Statistical colour followed by the q_L / q_S values it comes from, when gregorithm computed them."""
    q = record.quality
    parts = [] if q is None else [f"{name} {value:.3g} %" for name, value in
                                  (("q_L", q.q_linearity_pct), ("q_S", q.q_stability_pct)) if value is not None]
    return row.statistical + (f" ({', '.join(parts)})" if parts else "")


def _check_tip(records) -> str:
    """Tooltip of the Issues column: why each series of the impulse is "to check"."""
    lines = [f"{series_label(r.identity)}: {'; '.join(why)}" for r in records if (why := review_reasons(r))]
    return "To check:\n" + "\n".join(lines) if lines else ""


def _issues(event, records) -> str:
    """Short problem summary of one impulse: incomplete, errors, missing D, stale, quality to check."""
    if not event.complete:
        return "Incomplete"
    notes = []
    errors = sum(r.state.value == "error" for r in records)
    no_d = sum(r.state.value != "error" and r.diffusion_m2_s is None for r in records)
    stale = sum(bool(r.stale) for r in records)
    check = sum(bool(r.quality is not None and r.quality.needs_review) for r in records)
    if not records:
        notes.append("Not calculated")
    for count, text in ((errors, "failed"), (no_d, "no D"), (stale, "out of date"), (check, "to check")):
        if count:
            notes.append(f"{count} {text}")
    return ", ".join(notes) or "OK"


class ValuesTable(QTableWidget):
    """Read-only fit values, one row per series, styled like the result tables of the other pages (alternating rows,
    columns sized to their values). Always as tall as its rows: it scrolls only sideways (drag, bar, Shift + wheel, touchpad). The note (last
    column) wraps inside its cell and is also a wrapping tooltip, so a long note is always readable in full."""

    def __init__(self):
        super().__init__(0, 0)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setAlternatingRowColors(True)
        self.verticalHeader().hide()
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWordWrap(True)
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.setAccessibleName("Fit values")
        # Per pixel: by whole columns a wide table had only 2-3 stops and could not be dragged smoothly.
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.horizontalScrollBar().rangeChanged.connect(lambda *_: self._fit_height())
        self._pan_from: Optional[tuple[float, int]] = None      # pointer x and bar value at the start of a drag

    def set_rows(self, heads: list[str], rows: list[list[str]], tips: dict | None = None) -> None:
        """Show ``rows`` under ``heads``; numbers right-aligned, the note wrapped and also as a tooltip; ``tips``
        maps (row, column) to an extra tooltip (for example why Physical is N/D)."""
        self.setColumnCount(len(heads))
        self.setHorizontalHeaderLabels(heads)
        self.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, text in enumerate(row):
                cell = QTableWidgetItem(text)
                if 0 < j < 6:
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if j == len(row) - 1 and text:
                    cell.setToolTip(wrapped_tip(text))
                elif tips and (i, j) in tips:
                    cell.setToolTip(wrapped_tip(tips[(i, j)]))
                self.setItem(i, j, cell)
        self.setVisible(bool(rows))
        self.resizeRowsToContents()
        self._fit_height()

    def _fit_height(self) -> None:
        """As tall as every row, plus the scroll bar whenever the columns are wider than the table (otherwise the
        bar would cover the last row)."""
        bar = self.horizontalScrollBar()
        scrolls = bar.maximum() > 0
        height = (self.horizontalHeader().height() + sum(self.rowHeight(i) for i in range(self.rowCount()))
                  + 2 * self.frameWidth() + (bar.sizeHint().height() if scrolls else 0))
        if height != self.minimumHeight():
            self.setFixedHeight(height)
        if self._pan_from is None:
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor if scrolls else Qt.CursorShape.ArrowCursor)

    # --- scrolling with the pointer: drag the table, Shift + wheel or a horizontal (touchpad) wheel -----------------
    def mousePressEvent(self, event) -> None:  # noqa: N802  (Qt override)
        """Start dragging the table sideways (cells are not selectable, so a drag has no other meaning)."""
        if event.button() == Qt.MouseButton.LeftButton and self.horizontalScrollBar().maximum() > 0:
            self._pan_from = (event.position().x(), self.horizontalScrollBar().value())
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802  (Qt override)
        """The table follows the pointer while it is dragged."""
        if self._pan_from is not None:
            x0, value = self._pan_from
            self.horizontalScrollBar().setValue(round(value + x0 - event.position().x()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802  (Qt override)
        """End of a drag."""
        if self._pan_from is not None and event.button() == Qt.MouseButton.LeftButton:
            self._pan_from = None
            self._fit_height()                                   # restores the open-hand cursor
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802  (Qt override)
        """Horizontal wheel / touchpad and Shift + wheel scroll the columns; a plain wheel scrolls the page."""
        delta = event.angleDelta()
        dx = delta.x() if abs(delta.x()) > abs(delta.y()) else 0
        if not dx and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            dx = delta.y()
        if dx:
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - dx)
            event.accept()
        else:
            event.ignore()

    def as_text(self) -> str:
        """Headers and cells as plain text (tests, copy)."""
        heads = [self.horizontalHeaderItem(j).text() for j in range(self.columnCount())]
        cells = [self.item(i, j).text() for i in range(self.rowCount()) for j in range(self.columnCount())]
        return " | ".join(heads + cells)


class FitView(QWidget):
    """Pulse list + stacked V(t)/V(√t) chart + window controls + values + acceptance."""
    eventSelected = Signal(str)
    seriesSelected = Signal(object)
    windowApplied = Signal(str, str, float, float)
    windowAppliedAll = Signal(str, float, float)     # role, start [s], end [s]
    fitSeriesChosen = Signal(object)                 # (series key, OCV source) kept across impulses
    reapplyRequested = Signal(str)
    calculateAllRequested = Signal()
    acceptRequested = Signal(object)                 # list of event ids or None (all valid)
    revokeRequested = Signal(str)
    rejectRequested = Signal(str)

    def __init__(self, theme=None, parent=None):
        super().__init__(parent)
        self.theme = theme
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Orientation.Horizontal)
        left, ll = list_panel("Impulses")
        self.list = make_list([
            ("#", "Impulse number"),
            ("State", "● green: accepted · ● orange: pending or modified · ● red: rejected or invalid · ● grey: no fit "
                      "needed"),
            ("Alert", "OK, or how many fits of the impulse are to check (reasons in the tip)")], status_column=1)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        ll.addWidget(self.list, 1)
        self.summary = muted()
        ll.addWidget(self.summary)
        self.calc_all = button("Calculate all impulses", "run")
        self.calc_all.setToolTip("Automatic windows for every impulse; manual windows and IR samples are dropped.")
        ll.addWidget(self.calc_all)
        split.addWidget(left)
        right = FillPane()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)
        bar = QHBoxLayout()
        self.title = QLabel("Select an impulse.")
        self.title.setStyleSheet("font-weight: 500;")
        self.state = QLabel()
        self.state.setProperty("chip", True)
        self.series = QComboBox()
        self.series.setMinimumWidth(220)
        self.series.setAccessibleName("Series whose fit is edited")
        self.overlay = QCheckBox("Overlay the other fits")
        self.overlay.setChecked(True)
        self.reset = button("Reset view", "ghost")
        self.reset.clicked.connect(lambda: self.chart.reset_view())
        bar.addWidget(self.title)
        bar.addWidget(self.state)
        bar.addStretch(1)
        rl.addLayout(bar)
        tools = QHBoxLayout()                               # second row: what is shown
        tools.addWidget(QLabel("Fit of"))
        tools.addWidget(self.series, 1)
        tools.addWidget(self.overlay)
        tools.addWidget(self.reset)
        rl.addLayout(tools)
        self.chart = GittIciChart()                         # V(t) and V(√t) side by side, as in IR Drop
        self.chart.setMinimumHeight(330)
        rl.addWidget(self.chart, 1)
        strip = self._strip()
        rl.addWidget(strip)
        scroll = fill_scroll(right, "FitBody")
        split.addWidget(scroll)
        split.setStretchFactor(1, 1)
        split.setSizes([LIST_WIDTH, 1200])
        lay.addWidget(split)
        self._event = ""
        self._role: Optional[str] = None
        self._loading = False
        self._list_key = None
        self._chart_key = None
        self.list.currentItemChanged.connect(self._list_changed)
        self.series.currentIndexChanged.connect(self._series_changed)
        self.overlay.toggled.connect(lambda _on: self._force_redraw())
        self.chart.windowPreview.connect(self._preview)
        self.chart.windowReleased.connect(self._released)
        self.calc_all.clicked.connect(self.calculateAllRequested)

    def _strip(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("panel", True)
        grid = QGridLayout(frame)
        grid.setContentsMargins(14, 10, 14, 10)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        row = QHBoxLayout()
        self.window_label = QLabel("Window [s]")
        self.win_start, self.win_end = QLineEdit(), QLineEdit()
        for edit in (self.win_start, self.win_end):
            edit.setFixedWidth(90)
        self.win_apply = button("Apply window", "ghost")
        self.win_apply_all = button("Apply to all impulses", "ghost")
        self.win_apply_all.setToolTip("Use these bounds for every complete impulse and recalculate them; each one "
                                      "can still be edited afterwards.")
        self.reapply = button("Re-apply automatic to this impulse", "ghost")
        row.addWidget(self.window_label)
        row.addWidget(info(self.theme, "fit_window"))
        row.addWidget(self.win_start)
        row.addWidget(QLabel("–"))
        row.addWidget(self.win_end)
        row.addWidget(self.win_apply)
        row.addWidget(self.win_apply_all)
        row.addStretch(1)
        row.addWidget(self.reapply)
        grid.addLayout(row, 0, 0, 1, 2)
        head = QHBoxLayout()
        head.addWidget(QLabel("Fit values"))
        head.addWidget(info(self.theme, "fit_values"))
        head.addStretch(1)
        grid.addLayout(head, 1, 0, 1, 2)
        self.values = ValuesTable()
        self.values_empty = muted()
        grid.addWidget(self.values, 2, 0, 1, 2)
        grid.addWidget(self.values_empty, 2, 0, 1, 2)
        row = QHBoxLayout()
        self.note = muted()
        row.addWidget(self.note, 1)
        self.revoke = button("Return to Pending", "ghost")
        self.reject = button("Reject", "ghost")
        self.reject.setToolTip("Exclude this impulse by hand: none of its D is used.")
        self.accept_all = button("Accept all valid", "ghost")
        self.accept = button("Accept", "goElab")
        row.addWidget(info(self.theme, "fit_accept"))
        for b in (self.revoke, self.reject, self.accept_all, self.accept):
            row.addWidget(b)
        grid.addLayout(row, 3, 0, 1, 2)
        self.win_apply.clicked.connect(self._apply_fields)
        self.win_apply_all.clicked.connect(lambda: self._apply_fields(every=True))
        self.reapply.clicked.connect(lambda: self._event and self.reapplyRequested.emit(self._event))
        self.accept.clicked.connect(self._accept_current)
        self.accept_all.clicked.connect(lambda: self.acceptRequested.emit(None))
        self.revoke.clicked.connect(lambda: self._event and self.revokeRequested.emit(self._event))
        self.reject.clicked.connect(lambda: self._event and self.rejectRequested.emit(self._event))
        return frame

    # --- interaction -------------------------------------------------------------------------------------------------
    def _accept_current(self) -> None:
        """Accept the open impulse, then open the next one in the list."""
        if self._event:
            self.acceptRequested.emit([self._event])
            select_next(self.list)

    def _list_changed(self, current, _previous) -> None:
        if not self._loading and current is not None:
            self.eventSelected.emit(current.data(0, ID_ROLE))

    def _series_changed(self) -> None:
        data = self.series.currentData()
        if self._loading or data is None:
            return
        if isinstance(data, tuple):                          # a series not calculated yet: redraw its window
            self.fitSeriesChosen.emit(data)
            self._force_redraw()
        else:
            self.fitSeriesChosen.emit((series_key_of(data), data.ocv_source.value))
            self.seriesSelected.emit(data)

    def _preview(self, lo: float, hi: float) -> None:
        if not self.win_start.hasFocus() and not self.win_end.hasFocus():
            self.win_start.setText(f"{lo:.4g}")
            self.win_end.setText(f"{hi:.4g}")

    def _released(self, lo: float, hi: float) -> None:
        if self._event and self._role:
            self.windowApplied.emit(self._event, self._role, lo, hi)

    def _apply_fields(self, every: bool = False) -> None:
        """Apply the typed bounds to the open impulse, or with ``every`` to every complete impulse."""
        try:
            lo, hi = float(self.win_start.text().replace(",", ".")), float(self.win_end.text().replace(",", "."))
        except ValueError:
            self.note.setText("Enter the window bounds in seconds.")
            return
        if every and self._role:
            self.windowAppliedAll.emit(self._role, lo, hi)
        elif self._event and self._role:
            self.windowApplied.emit(self._event, self._role, lo, hi)

    def _force_redraw(self) -> None:
        self._chart_key = None
        if getattr(self, "_args", None) is not None:
            self.set_data(*self._args)

    # --- data ----------------------------------------------------------------------------------------------------------
    def set_data(self, events, records, states: dict, open_event: str, trace, selected, windows: dict, params,
                 running: bool, unit: str, fit_series: Optional[tuple[str, str]] = None) -> None:
        """Show the list, the open impulse with the chosen series (``fit_series``, kept across impulses), its window
        and values."""
        self._args = (events, records, states, open_event, trace, selected, windows, params, running, unit, fit_series)
        by_event: dict[str, list] = {}
        for r in records:
            by_event.setdefault(r.identity.event_id, []).append(r)
        rows = [(e, i) for i, e in enumerate(events, start=1)]
        key = tuple((e.event_id, states.get(e.event_id), _issues(e, by_event.get(e.event_id, []))) for e, _i in rows)
        if key != self._list_key:
            self._list_key = key
            self._loading = True
            try:
                self.list.clear()
                for e, i in rows:
                    state = states.get(e.event_id)
                    item = list_row([str(i), "", _issues(e, by_event.get(e.event_id, []))],
                                    (1, STATUS_KIND.get(state, "none"), f"{state or ''}\n{STATE_TIPS.get(state, '')}"),
                                    self.theme)
                    item.setData(0, ID_ROLE, e.event_id)
                    item.setToolTip(0, f"Impulse #{i} · cycle {e.cycle} · half-cycle {e.half_cycle}")
                    item.setToolTip(2, wrapped_tip(_check_tip(by_event.get(e.event_id, []))))
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
                if item.data(0, ID_ROLE) == open_event and self.list.currentItem() is not item:
                    self.list.setCurrentItem(item)
        finally:
            self._loading = False
        self.calc_all.setEnabled(not running and bool(events))
        event = next((e for e in events if e.event_id == open_event), None)
        self._event = "" if event is None else event.event_id
        state = states.get(self._event)
        self.accept.setEnabled(state in (PENDING, MODIFIED, REJECTED) and not running)
        self.revoke.setEnabled(state in (ACCEPTED, MODIFIED, REJECTED) and not running)
        self.reject.setEnabled(bool(open_event) and state not in (REJECTED, None) and not running)
        self.accept_all.setEnabled(any(s in (PENDING, MODIFIED) for s in states.values()) and not running)
        if event is None:
            self.title.setText("Select an impulse." if events else "Recognise impulses first (Setup → Proceed).")
            self.state.setText("")
            self.values.set_rows([], [])
            self.values_empty.setText("")
            self.chart.clear("")
            self._chart_key = None
            for w in (self.win_start, self.win_end, self.win_apply, self.win_apply_all, self.reapply):
                w.setEnabled(False)
            return
        own = sorted(by_event.get(event.event_id, []), key=lambda r: (series_key_of(r.identity),
                                                                     r.identity.ocv_source.value))
        number = next(i for e, i in rows if e.event_id == event.event_id)
        self.title.setText(f"Impulse #{number} · cycle {event.cycle} · half-cycle {event.half_cycle} · "
                           f"{'charge' if event.polarity > 0 else 'discharge'}")
        self.state.setText(STATE_MARK.get(state, ""))
        self.state.setToolTip(STATE_TIPS.get(state, ""))
        self.state.setStyleSheet(f"color: {status_color(self.theme, STATUS_KIND.get(state, 'none'))};")
        # One entry per distinct fit: GITT Δ has none, and the OCV source changes only D, not the √t fit.
        fits = distinct_fits(own, params)
        if not fits and own:
            fits = [(series_label(r.identity), r) for r in own]
        # The chosen (method, OCV source) applies to every impulse; an identity selected for review is the fallback.
        want = fit_series or (None if selected is None else (series_key_of(selected), selected.ocv_source.value))
        shown = None
        if want is not None:
            same_key = [r for _l, r in fits if series_key_of(r.identity) == want[0]]
            chosen = next((r for r in own if (series_key_of(r.identity), r.identity.ocv_source.value) == want), None)
            shown = next((r for r in same_key if chosen is not None and fit_signature(r) == fit_signature(chosen)),
                         None) or (same_key[0] if same_key else None)
        shown = shown or (fits[0][1] if fits else None)
        # Not calculated yet (automatic analysis off): offer the checked methods that have a window, so the window can
        # be set before the first calculation; Apply window then calculates this impulse.
        pending = [] if own else _pending_fits(params)
        self._loading = True
        try:
            labels = ([(label, r.identity) for label, r in fits] if fits else
                      [(f"{SERIES[k][0]} (not calculated)", (k, src)) for k, src in pending])
            wanted = tuple(data for _l, data in labels)
            current = tuple(self.series.itemData(i) for i in range(self.series.count()))
            if wanted != current:
                self.series.clear()
                for text, data in labels:
                    self.series.addItem(text, data)
            if shown is not None:
                self.series.setCurrentIndex(max(0, self.series.findData(shown.identity)))
            elif want is not None:
                self.series.setCurrentIndex(max(0, next((i for i, (_l, data) in enumerate(labels)
                                                         if data[0] == want[0]), 0)))
        finally:
            self._loading = False
        if shown is not None:
            role = WINDOW_ROLE.get(series_key_of(shown.identity))
        else:
            key = self.series.currentData()
            role = WINDOW_ROLE.get(key[0]) if isinstance(key, tuple) else None
        self._role = role
        manual = windows.get((event.event_id, role)) if role else None
        if manual is None and shown is None and role:
            manual = params.fixed_window(role)             # fixed-mode bounds preview the first calculation
        others = [] if not self.overlay.isChecked() else [
            (label, r) for label, r in fits if shown is None or r.identity != shown.identity]
        chart_key = (id(trace), None if shown is None else (shown.identity, shown.calculation_fingerprint), role,
                     manual, tuple((label, r.calculation_fingerprint) for label, r in others))
        if trace is None or trace.event_id != event.event_id:
            self.chart.clear("Reading the impulse samples…")
            self._chart_key = None
        elif chart_key != self._chart_key:
            self._chart_key = chart_key
            self.chart.show_record(trace, shown, role, manual, others)
        window = self.chart.window()
        if window is not None:
            self._preview(*window)
        self.window_label.setText(f"Window on the {ROLE_LABELS[role].lower()} [s]" if role else "No editable window")
        for w in (self.win_start, self.win_end, self.win_apply, self.win_apply_all):
            w.setEnabled(bool(role) and not running)
        self.reapply.setEnabled(event.complete and not running)
        self.values.set_rows(*(self._values(own, unit) if own else ([], [], {})))
        self.values_empty.setText("" if own else
                                  "Not calculated yet: use Calculate all impulses or Re-apply automatic to this impulse.")
        self.note.setText(STATE_TIPS.get(state, ""))

    @staticmethod
    def _values(records, unit: str) -> tuple[list[str], list[list[str]], dict]:
        """Headers and rows of the values table: one row per series with D, fit statistics, indicators and a note
        (the reason when D is missing, out of date, or why the result is to check); the Physical cells carry their
        explanation as a tooltip."""
        heads = ["Series", f"D [{D_UNITS[unit][0]}]", "Slope [V/√s]", "Intercept [V]", "R²", "Samples", "Statistical",
                 "Physical", "Note"]
        rows, tips = [], {}
        for r in records:
            row = series_row(r, unit)
            tips[(len(rows), 7)] = physical_note(r)
            window = fit_window(r) or {}
            fit = window.get("fit") if isinstance(window.get("fit"), dict) else {}

            def f(key, fmt, fit=fit):
                value = fit.get(key)
                return "—" if value is None else format(value, fmt)
            check = review_reasons(r)
            note = row.reason or ("Out of date" if row.stale else "") or ("To check: " + "; ".join(check) if check else "")
            rows.append([row.label, row.d_text, f("slope_v_per_sqrt_s", ".4g"), f("intercept_v", ".6g"),
                         f("r_squared", ".4f"), str(fit.get("sample_count", "—")), _statistical(r, row), row.physical,
                         note])
        return heads, rows, tips
