"""DCIR step 3 · Cycle Correction: status strip with the explicit actions, the spans of the calculated
Accepted measurements (list 240 px), the whole-test overview with the spans marked, the Recalculation panel and
"What Apply does". Apply / Remove always go through a confirmation dialog that states the impact.

The overview is an indicative, static picture: sampled real points (as the Data Explorer samples them), no zoom, pan
or hover, redrawn only when its data or spans change. Spans last seconds on tests of hours, so they are drawn as
fixed-width marks, not to scale."""
from __future__ import annotations
from types import SimpleNamespace
from typing import Optional
import numpy as np
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainterPath, QPen
from PySide6.QtWidgets import (QDialog, QFrame, QGraphicsPathItem, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QProgressBar, QPushButton, QVBoxLayout, QWidget)
from ..correction import CORR_TITLE, REPORT_TEXT, THRESHOLD_INFO, CorrView
from ..correction_controller import CorrectionController
from ..dcir import AUTO, POLARITY_LABELS
from ..elaboration import CYCLE_ORDER_LABELS
from ..explorer import CHART
from ..icons import pixmap
from ..theme import ThemeManager, set_prop
from .chart import ChartWidget
from .controls import ElidedLabel, InfoButton

STRIP = {  # view -> (banner kind, icon)
    CorrView.UNAVAILABLE: ("n", "info"), CorrView.NO_SPANS: ("n", "info"), CorrView.NOT_APPLIED: ("n", "info"),
    CorrView.RUNNING: ("n", "loader-circle"), CorrView.CANCELLING: ("n", "loader-circle"),
    CorrView.PUBLISHING: ("n", "loader-circle"), CorrView.APPLIED: ("ok", "circle-check"),
    CorrView.OUTDATED: ("warn", "circle-alert"), CorrView.CANCELLED: ("n", "ban"), CorrView.FAILED: ("err", "circle-x"),
}
TEXT = {
    CorrView.UNAVAILABLE: "Cycle Correction uses the calculated DCIR measurements. Accept pulses in Review and "
                          "Apply & Calculate first.",
    CorrView.NO_SPANS: "None of the calculated Accepted measurements gives a span: a manual measurement needs two "
                       "boundary points (Set Span…).",
    CorrView.NOT_APPLIED: "Cycles are calculated on the whole recording. Applying excludes the spans listed below. "
                          "Nothing changes until you apply.",
    CorrView.RUNNING: "A new corrected elaboration is being written. The current results stay as they are until it is "
                      "ready.",
    CorrView.CANCELLING: "Waiting for the current step to stop. Nothing will be saved.",
    CorrView.PUBLISHING: "Work is complete; the corrected elaboration is being saved.",
    CorrView.OUTDATED: "The DCIR measurements or the Rest Current Threshold changed after the correction was applied. "
                       "The shown cycles still exclude the previous spans. Reapply to use the current ones.",
    CorrView.CANCELLED: "Nothing was saved. The results shown are unchanged.",
}
KIND_COLOR = {"n": "muted", "ok": "success", "warn": "warn", "err": "err"}


def _lab(text="", muted=False, size=None, weight=None, cls=QLabel) -> QLabel:
    """Themed label; cls=ElidedLabel for free text that should end with '…' (full text as tooltip) when short of room."""
    l = cls(text)
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


class ConfirmDialog(QDialog):
    """Apply Cycle Correction? / Remove Cycle Correction? — the impact, then an explicit button."""

    def __init__(self, parent, enabled: bool, ds_name: str, rows: list[tuple[str, str]]):
        super().__init__(parent)
        self.setWindowTitle("Apply Cycle Correction" if enabled else "Remove Cycle Correction")
        self.setMinimumWidth(600)
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 16)
        v.setSpacing(12)
        v.addWidget(_lab("Apply Cycle Correction?" if enabled else "Remove Cycle Correction?", size=20, weight=500))
        t = _lab(f"gregorithm creates a new {'corrected' if enabled else 'uncorrected'} elaboration of {ds_name}. "
                 "The current one is kept until the new one is complete.", muted=True, size=13)
        t.setWordWrap(True)
        v.addWidget(t)
        box = QFrame()
        box.setProperty("panel", True)
        g = QGridLayout(box)
        g.setContentsMargins(14, 12, 14, 12)
        g.setHorizontalSpacing(14)
        for r, (k, val) in enumerate(rows):
            g.addWidget(_lab(k, muted=True, size=13), r, 0, Qt.AlignmentFlag.AlignTop)
            x = _lab(val, size=13)
            x.setWordWrap(True)
            g.addWidget(x, r, 1)
        v.addWidget(box)
        cols = QHBoxLayout()
        for title, lines, kind in (("Recalculated", ["Cycle and half-cycle labels", "Cumulative values",
                                                     "Capacity (replaces the current Capacity result)"], "ok"),
                                   ("Not changed", ["Recorded points (none is deleted)", "DCIR pulses (not detected again)",
                                                    "dQ/dV (not recalculated; labels only)"], "n")):
            f = QFrame()
            f.setProperty("banner", "n" if kind == "n" else "ok")
            fl = QVBoxLayout(f)
            fl.setContentsMargins(12, 10, 12, 10)
            fl.setSpacing(4)
            fl.addWidget(_lab(title, weight=500, size=12))
            for ln in lines:
                fl.addWidget(_lab(ln, size=12))
            cols.addWidget(f, 1)
        v.addLayout(cols)
        note = _lab(("Reversible: Remove Correction restores the uncorrected labels. " if enabled else
                     "The DCIR measurements stay as they are; you can apply the correction again later. ")
                    + "gregorithm checks the free disk space before writing.", muted=True, size=12)
        note.setWordWrap(True)
        v.addWidget(note)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel, ok = _btn("Cancel", "ghost"), _btn("Apply Correction" if enabled else "Remove Correction", "goElab")
        ok.setProperty("run", True)                     # amber: starts a calculation
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        v.addLayout(row)


class CorrectionView(QWidget):
    setSpanRequested = Signal(str)            # event id of a manual measurement without a span
    openCapacityRequested = Signal()

    def __init__(self, theme: ThemeManager, ctl: CorrectionController):
        super().__init__()
        self.theme, self.ctl = theme, ctl
        self.ds_id: Optional[str] = None
        self._list_key = None
        self._draw_key = None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        # status strip
        self.strip = QFrame()
        sl = QHBoxLayout(self.strip)
        sl.setContentsMargins(14, 10, 14, 10)
        sl.setSpacing(12)
        self.s_icon = QLabel()
        col = QVBoxLayout()
        col.setSpacing(2)
        self.s_title, self.s_text = _lab(weight=500, size=14), _lab(muted=True, size=12)
        self.s_text.setWordWrap(True)
        self.s_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.s_bar = QProgressBar()
        self.s_bar.setTextVisible(False)
        self.s_bar.setMaximumWidth(320)
        for w in (self.s_title, self.s_text, self.s_bar):
            col.addWidget(w)
        self.remove_btn = _btn("Remove Correction…", "ghost")
        self.remove_btn.clicked.connect(lambda: self._confirm(False))
        self.main_btn = _btn("Apply Cycle Correction…", "goElab")
        self.main_btn.clicked.connect(self._main)
        sl.addWidget(self.s_icon, 0, Qt.AlignmentFlag.AlignTop)
        sl.addLayout(col, 1)
        sl.addWidget(self.remove_btn)
        sl.addWidget(self.main_btn)
        v.addWidget(self.strip)
        # list + overview
        top = QHBoxLayout()
        top.setSpacing(12)
        left = QFrame()
        left.setProperty("panel", True)
        left.setFixedWidth(240)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(6, 6, 6, 8)
        ll.setSpacing(6)
        head = QHBoxLayout()
        head.setContentsMargins(8, 2, 8, 0)
        head.addWidget(_lab("Accepted measurement", muted=True, size=11))
        head.addStretch(1)
        head.addWidget(_lab("Span", muted=True, size=11))
        ll.addLayout(head)
        self.list = QListWidget()
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setAccessibleName("Spans to exclude")
        self.list.currentItemChanged.connect(self._sel)
        ll.addWidget(self.list, 1)
        self.list_foot = _lab(muted=True, size=11, cls=ElidedLabel)
        self.list_foot.setWordWrap(True)
        ll.addWidget(self.list_foot)
        top.addWidget(left)
        self.chart = ChartWidget()
        self.chart.row_title, self.chart.meta_line = "Source Row", False
        self.chart.interactive = False                  # indicative picture: no zoom / pan
        self.chart.set_point_tracking(False)            # no hover snapping over the whole test
        self.chart.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.chart.setCursor(Qt.CursorShape.ArrowCursor)
        self.chart.vb.setCursor(Qt.CursorShape.ArrowCursor)      # no crosshair: nothing to pick
        self.marks, self.mark_sel = self._mark_item(0.55, 1.5), self._mark_item(1.0, 2.5)
        right = QVBoxLayout()
        right.setSpacing(4)
        bar = QHBoxLayout()
        bar.addWidget(_lab("Whole Test", weight=500, size=13))
        self.ov_note = _lab("Marked: spans excluded from the cycle calculation (each lasts seconds: marks are not to "
                            "scale)", muted=True, size=11)
        bar.addWidget(self.ov_note)
        bar.addStretch(1)
        right.addLayout(bar)
        right.addWidget(self.chart, 1)
        top.addLayout(right, 1)
        v.addLayout(top, 1)
        # bottom panels
        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        p1 = QFrame()
        p1.setProperty("panel", True)
        g = QGridLayout(p1)
        g.setContentsMargins(16, 12, 16, 12)
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(8)
        g.addWidget(_lab("Recalculation", weight=500, size=13), 0, 0, 1, 2)
        lr = QWidget()
        lh = QHBoxLayout(lr)
        lh.setContentsMargins(0, 0, 0, 0)
        lh.setSpacing(4)
        lh.addWidget(_lab("Rest Current Threshold", muted=True, size=12))
        lh.addWidget(InfoButton(theme, "Rest Current Threshold", THRESHOLD_INFO))
        g.addWidget(lr, 1, 0, Qt.AlignmentFlag.AlignRight)
        tr = QWidget()
        th = QHBoxLayout(tr)
        th.setContentsMargins(0, 0, 0, 0)
        th.setSpacing(6)
        self.thr = QLineEdit()
        self.thr.setFixedWidth(80)
        self.thr.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.thr.setAccessibleName("Rest Current Threshold")
        self.thr.textEdited.connect(lambda t: self.ctl.edit(self.ds_id, lambda co: co.set_threshold_text(t)))
        self.thr_unit = _lab(muted=True, size=12)
        self.thr_err = QLabel()
        self.thr_err.setProperty("hintErr", True)
        for w in (self.thr, self.thr_unit, self.thr_err):
            th.addWidget(w)
        th.addStretch(1)
        g.addWidget(tr, 1, 1)
        self.order_chip, self.cap_chip = QLabel(), QLabel()
        for c in (self.order_chip, self.cap_chip):
            c.setProperty("chip", True)
        g.addWidget(_lab("Cycle Order", muted=True, size=12), 2, 0, Qt.AlignmentFlag.AlignRight)
        g.addWidget(self._hrow(self.order_chip, _lab("from Data Elaboration", muted=True, size=12)), 2, 1)
        to_cap = _btn("Change in Capacity", "link", 26)
        to_cap.clicked.connect(self.openCapacityRequested)
        g.addWidget(_lab("Capacity", muted=True, size=12), 3, 0, Qt.AlignmentFlag.AlignRight)
        g.addWidget(self._hrow(self.cap_chip, to_cap), 3, 1)
        g.addWidget(_lab("dQ/dV", muted=True, size=12), 4, 0, Qt.AlignmentFlag.AlignRight)
        g.addWidget(_lab("No dQ/dV result yet: nothing to re-label", muted=True, size=12), 4, 1)
        g.setColumnStretch(1, 1)
        bottom.addWidget(p1, 1)
        p2 = QFrame()
        p2.setProperty("panel", True)
        pl = QVBoxLayout(p2)
        pl.setContentsMargins(16, 12, 16, 12)
        pl.setSpacing(8)
        pl.addWidget(_lab("What Apply does", weight=500, size=13))
        self.impact = []
        for icon, text in (("circle-check", "Creates a new, corrected elaboration: cycle and half-cycle labels, "
                                            "cumulative values and Capacity are recalculated without the spans."),
                           ("ban", "Recorded points are not deleted · pulses are not detected again · dQ/dV is not "
                                   "recalculated (labels only)."),
                           ("rotate-ccw", "Data Explorer, Capacity and DCIR then show the corrected result (“DCIR "
                                          "corrected”). Remove Correction restores the uncorrected labels.")):
            r = QHBoxLayout()
            r.setSpacing(8)
            ic = QLabel()
            self.impact.append((ic, icon))
            t = _lab(text, size=12)
            t.setWordWrap(True)
            r.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
            r.addWidget(t, 1)
            pl.addLayout(r)
        self.report = _lab(muted=True, size=11)
        self.report.setWordWrap(True)
        pl.addWidget(self.report)
        pl.addStretch(1)
        bottom.addWidget(p2, 1)
        v.addLayout(bottom)
        ctl.changed.connect(lambda ds: self.refresh() if ds == self.ds_id else None)
        theme.changed.connect(self.refresh)

    def _mark_item(self, alpha: float, width: float) -> QGraphicsPathItem:
        """Path of vertical span marks in the chart's data coordinates; cosmetic pen, so marks keep `width` px."""
        c = QColor(CHART["y2"])
        c.setAlphaF(alpha)
        pen = QPen(c, width)
        pen.setCosmetic(True)
        item = QGraphicsPathItem()
        item.setPen(pen)
        item.setZValue(6)                               # above the curve
        self.chart.vb.addItem(item, ignoreBounds=True)
        return item

    @staticmethod
    def _hrow(*ws) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        for x in ws:
            h.addWidget(x)
        h.addStretch(1)
        return w

    # ---------------------------------------------------------------- behaviour
    def show_dataset(self, ds_id: Optional[str]) -> None:
        self.ds_id = ds_id
        self._list_key = None
        self._draw_key = None
        if ds_id:
            self.ctl.load(ds_id)
        self.refresh()

    def _sel(self, cur, _prev) -> None:
        if cur is None or not self.ds_id:
            return
        eid = cur.data(Qt.ItemDataRole.UserRole)
        self.ctl.edit(self.ds_id, lambda co: setattr(co, "selected", eid))

    def _main(self) -> None:
        view = self.ctl.view(self.ds_id)
        if view in (CorrView.RUNNING,):
            self.ctl.cancel(self.ds_id)
        else:
            self._confirm(True)

    def _confirm(self, enabled: bool) -> None:
        d, co = self.ctl.state.get(self.ds_id), self.ctl.co(self.ds_id)
        if d is None:
            return
        spans = self.ctl.spans.get(self.ds_id, [])
        u = self.ctl.units.units_for(self.ds_id)
        with_s = [s.label for s in spans if s.has_span]
        without = [s.label for s in spans if not s.has_span]
        cp = self.ctl.capacity.current_params(self.ds_id)
        rows = []
        if enabled:
            rows += [("Spans excluded", f"{len(with_s):,} · {', '.join(with_s[:12])}{'…' if len(with_s) > 12 else ''}")]
            if without:
                rows.append(("Not excluded", f"{', '.join(without)} · manual measurement without boundary points"))
        rows += [("Rest Current Threshold", f"{co.threshold:g} {u.current}"),
                 ("Cycle Order", CYCLE_ORDER_LABELS.get(d.elab.result.params.cycle_order, "") if d.elab.result and d.elab.result.params else "—"),
                 ("Capacity", self._cap_text(cp))]
        if ConfirmDialog(self, enabled, d.name, rows).exec():
            self.ctl.run(self.ds_id, enabled)

    @staticmethod
    def _cap_text(cp) -> str:
        if cp is None:
            return "—"
        m = f"Mass {cp.active_material_mass_mg:g} mg" if cp.active_material_mass_mg else "Mass not set"
        a = f"Area {cp.electrode_area_cm2:g} cm²" if cp.electrode_area_cm2 else "Area not set"
        return f"Reference cycle {cp.reference_cycle} · {m} · {a}"

    # ---------------------------------------------------------------- refresh
    def refresh(self) -> None:
        co = self.ctl.co(self.ds_id) if self.ds_id else None
        if co is None:
            return
        t = self.theme.tokens
        d = self.ctl.state.get(self.ds_id)
        u = self.ctl.units.units_for(self.ds_id)
        view = self.ctl.view(self.ds_id)
        kind, icon = STRIP[view]
        set_prop(self.strip, "banner", kind)
        self.s_icon.setPixmap(pixmap(icon, getattr(t, KIND_COLOR[kind]), 20))
        title = CORR_TITLE[view]
        a = co.applied
        if view in (CorrView.APPLIED, CorrView.OUTDATED) and a is not None and view is CorrView.APPLIED:
            title = f"Cycle correction applied · {a.applied_at} · {a.n_spans:,} span{'s' if a.n_spans != 1 else ''}"
        self.s_title.setText(title)
        if view is CorrView.FAILED:
            text = f"{co.message}\nNothing was saved. The results shown are unchanged."
        elif view is CorrView.APPLIED:
            text = (f"Data Explorer, Capacity and DCIR show the corrected elaboration. Rest Current Threshold "
                    f"{a.threshold:g} {a.threshold_unit}.")
        elif view is CorrView.NOT_APPLIED and co.removed_at:
            text = f"Correction removed {co.removed_at}. " + TEXT[view]
        else:
            text = TEXT.get(view, "")
        self.s_text.setText(text)
        running = view in (CorrView.RUNNING, CorrView.CANCELLING, CorrView.PUBLISHING)
        self.s_bar.setVisible(running)
        if running:
            if co.progress is None:
                self.s_bar.setRange(0, 0)
            else:
                self.s_bar.setRange(0, 1000)
                self.s_bar.setValue(int(co.progress * 1000))
            self.s_text.setText(f"{TEXT.get(view, '')}  {co.phase}".strip())
        # buttons
        applied = view in (CorrView.APPLIED, CorrView.OUTDATED)
        self.remove_btn.setVisible(applied)
        rb = self.ctl.remove_blocker(self.ds_id)
        self.remove_btn.setEnabled(not rb)
        self.remove_btn.setToolTip(rb)
        if view is CorrView.RUNNING:
            self.main_btn.setText("Cancel")
            set_prop(self.main_btn, "goElab", False)
            set_prop(self.main_btn, "ghost", True)
            set_prop(self.main_btn, "run", False)
            self.main_btn.setEnabled(True)
            self.main_btn.setToolTip("")
        else:
            self.main_btn.setText("Reapply…" if applied else "Apply Cycle Correction…")
            primary = view is not CorrView.APPLIED
            set_prop(self.main_btn, "goElab", primary)
            set_prop(self.main_btn, "ghost", not primary)
            set_prop(self.main_btn, "run", primary)                # amber: starts a calculation
            ab = self.ctl.apply_blocker(self.ds_id) if not running else "The correction is running"
            self.main_btn.setEnabled(not ab)
            self.main_btn.setToolTip(ab)
        self.main_btn.setVisible(view not in (CorrView.CANCELLING, CorrView.PUBLISHING))
        # list
        spans = self.ctl.spans.get(self.ds_id, [])
        key = (tuple(spans), co.selected, t.name)
        if key != self._list_key:
            self._list_key = key
            self._fill_list(spans, co.selected, u)
        w_s, wo = self.ctl.counts(self.ds_id)
        self.list_foot.setText(f"{w_s:,} of {len(spans):,} accepted measurements give a span. A manual measurement gives "
                               "one only with two boundary points." if spans else
                               "No calculated Accepted measurement yet.")
        # panels
        if not self.thr.hasFocus():
            self.thr.setText(co.threshold_text if co.threshold_error else f"{co.threshold:g}")
        set_prop(self.thr, "error", bool(co.threshold_error))
        self.thr_err.setText(co.threshold_error)
        self.thr.setEnabled(not running)
        self.thr_unit.setText(u.current)
        order = d.elab.result.params.cycle_order if d.elab.result and d.elab.result.params else ""
        self.order_chip.setText(CYCLE_ORDER_LABELS.get(order, "—"))
        self.cap_chip.setText(self._cap_text(self.ctl.capacity.current_params(self.ds_id)))
        for ic, name in self.impact:
            ic.setPixmap(pixmap(name, t.success if name == "circle-check" else t.muted, 16))
        if a is not None and a.report:
            self.report.setText("Last correction report: " + " · ".join(REPORT_TEXT.get(k, f"{k} = {v}") for k, v in a.report))
            self.report.show()
        else:
            self.report.hide()
        self._draw()

    def _fill_list(self, spans, selected, u) -> None:
        t = self.theme.tokens
        self.list.blockSignals(True)
        self.list.clear()
        for s in spans:
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, s.event_id)
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 4, 8, 4)
            h.setSpacing(8)
            ic = QLabel()
            ic.setPixmap(pixmap("circle-check" if s.has_span else "ban", t.success if s.has_span else t.muted, 14))
            ic.setToolTip("Excluded when applied" if s.has_span else "No span: not excluded")
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(_lab(s.label or s.event_id, size=13, weight=500 if s.event_id == selected else 400))
            parts = ["Auto" if s.source == AUTO else "Manual"]
            if s.polarity:
                parts.append(POLARITY_LABELS.get(s.polarity, "").lower())
            if s.cycle is not None:
                parts.append(f"cycle {s.cycle}")
            if s.start_display is not None:
                parts.append(f"{s.start_display:.2f} {u.time}")
            col.addWidget(_lab(" · ".join(parts), muted=True, size=11))
            h.addWidget(ic)
            h.addLayout(col, 1)
            if s.has_span:
                txt = f"Pulse {s.pulse_duration_s:g} s" if s.source == AUTO and s.pulse_duration_s else \
                      f"Rows {s.start_row:,}–{s.end_row:,}"
                h.addWidget(_lab(txt, size=11))
            else:
                b = _btn("Set Span…", "link", 24)
                b.clicked.connect(lambda _c=False, e=s.event_id: self.setSpanRequested.emit(e))
                h.addWidget(b)
            it.setSizeHint(QSize(220, 46))
            self.list.addItem(it)
            self.list.setItemWidget(it, row)
            if s.event_id == selected:
                self.list.setCurrentItem(it)
        self.list.blockSignals(False)

    def _draw(self) -> None:
        """Draw the overview and the span marks; skipped when nothing they depend on changed (refresh runs on every
        controller change, e.g. while the threshold is typed)."""
        curve = self.ctl.overviews.get(self.ds_id) if self.ds_id else None
        co = self.ctl.co(self.ds_id) if self.ds_id else None
        if curve is None or co is None:
            self._draw_key = None
            self.chart.set_view(None)
            self._set_marks([], None, (0.0, 1.0))
            self.chart.show_message("chart-line", "Reading the whole test…" if self.ds_id else "", "")
            return
        u = self.ctl.units.units_for(self.ds_id)
        spans = [s for s in self.ctl.spans.get(self.ds_id, []) if s.has_span and s.start_display is not None]
        sel = next((i for i, s in enumerate(spans) if s.event_id == co.selected), None)
        key = (self.ds_id, id(curve), u, tuple((s.start_display, s.end_display) for s in spans), sel)
        if key == self._draw_key:
            return
        self._draw_key = key
        x, y = np.asarray(curve.x, float), np.asarray(curve.y, float)
        fx = (float(np.nanmin(x)), float(np.nanmax(x))) if x.size else (0.0, 1.0)
        fy = (float(np.nanmin(y)), float(np.nanmax(y))) if y.size else (0.0, 1.0)
        pad = (fy[1] - fy[0]) * 0.06 or 0.1
        yr = (fy[0] - pad, fy[1] + pad)
        view = SimpleNamespace(y1=SimpleNamespace(x=x, y=y, source_row=np.asarray(curve.source_row)), y2=None,
                               x_range=fx, y1_range=yr, y2_range=None)
        self.chart.set_style("time_s", (f"Time [{u.time}]", f"Voltage [{u.voltage}]", None), (CHART["y1"], CHART["y2"]),
                             False, "lines")
        self.chart.set_view(view)
        self.chart.show_message(None)
        self._set_marks(spans, sel, yr)

    def _set_marks(self, spans, sel, yr) -> None:
        """Full-height lines at the start and end of each span (merged at this scale); selected one stronger."""
        path, chosen = QPainterPath(), QPainterPath()
        for i, s in enumerate(spans):
            p = chosen if i == sel else path
            for xv in {float(s.start_display), float(s.end_display if s.end_display is not None else s.start_display)}:
                p.moveTo(xv, yr[0])
                p.lineTo(xv, yr[1])
        self.marks.setPath(path)
        self.mark_sel.setPath(chosen)
