"""Right dock of the Capacity section: 'Capacity Settings'. Analysis is expanded by default; the plot
groups (Cycle Filter, Plot Colors, Legend, Axis Ranges, Plot Style) start collapsed. Cycle Order and the Sample
values are read-only here, with links to Data Elaboration."""
from __future__ import annotations
from typing import Optional
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)
from ..capacity import cap_label, reference_hint
from ..capacity_controller import CapacityController
from ..elaboration import CYCLE_ORDER_LABELS
from ..explorer import FILTER_HINT, PLOT_STYLES, Viewport, parse_range
from ..icons import make_icon
from ..theme import ThemeManager, set_prop
from ..units import CAPACITY, scale
from .capacity_page import ORDER_INFO, REFERENCE_INFO
from .controls import ColorSquare, LabelRow, SegmentedControl, Switch
from .elab_dock import Accordion
from .explorer_dock import FILTER_INFO


def _btn(text: str, kind: str) -> QPushButton:
    b = QPushButton(text)
    b.setProperty(kind, True)
    b.setFixedHeight(30)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def _muted(text: str = "", size: int = 11) -> QLabel:
    l = QLabel(text)
    l.setProperty("muted", True)
    l.setWordWrap(True)
    l.setStyleSheet(f"font-size: {size * 0.75:g}pt;")
    return l


def _ro(text: str = "") -> QLabel:
    l = QLabel(text)
    l.setProperty("chip", True)
    return l


class CapacitySettingsDock(QFrame):
    unitsRequested = Signal()
    goElaborationRequested = Signal()
    changedView = Signal()                  # the page must redraw (display-only edits)

    def __init__(self, theme: ThemeManager, ctl: CapacityController):
        super().__init__()
        self.setObjectName("DockCard")
        self.theme, self.ctl, self.ds_id = theme, ctl, None
        self._range_dirty = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(12)
        head = QHBoxLayout()
        t = QLabel("Capacity Settings")
        t.setStyleSheet("font-size: 11.25pt; font-weight: 500;")
        units = _btn("Units…", "link")
        units.clicked.connect(self.unitsRequested)
        head.addWidget(t)
        head.addStretch(1)
        head.addWidget(units)
        outer.addLayout(head)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 6, 0)
        bl.setSpacing(0)
        scroll = QScrollArea()
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("TabScroll")
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # ---- Analysis (expanded)
        g = Accordion(theme, "Analysis")
        g.cl.addWidget(LabelRow(theme, "Cycle Order", ORDER_INFO))
        self.order = _ro()
        g.cl.addWidget(self.order)
        l1 = _btn("Change in Data Elaboration", "link")
        l1.clicked.connect(self.goElaborationRequested)
        g.cl.addWidget(l1, 0, Qt.AlignmentFlag.AlignLeft)
        self.ref_row = LabelRow(theme, "Reference Cycle", REFERENCE_INFO)
        self.ref = QLineEdit()
        self.ref.setAccessibleName("Reference Cycle")
        self.ref.textEdited.connect(lambda txt: self.ctl.set_reference_text(self.ds_id, txt))
        self.ref.returnPressed.connect(lambda: self.ctl.analyze(self.ds_id))
        self.ref_hint = _muted()
        for w in (self.ref_row, self.ref, self.ref_hint):
            g.cl.addWidget(w)
        g.cl.addWidget(QLabel("Sample"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        self.mass, self.area = _ro(), _ro()
        self.mass_tag, self.area_tag = QLabel("Modified"), QLabel("Modified")
        for i, (name, val, tag) in enumerate((("Active Material Mass", self.mass, self.mass_tag), ("Surface Area", self.area, self.area_tag))):
            tag.setProperty("tag", True)
            grid.addWidget(_muted(name, 12), i, 0)
            grid.addWidget(val, i, 1)
            grid.addWidget(tag, i, 2)
        w = QWidget()
        w.setLayout(grid)
        g.cl.addWidget(w)
        l2 = _btn("Edit in Data Elaboration", "link")
        l2.clicked.connect(self.goElaborationRequested)
        g.cl.addWidget(l2, 0, Qt.AlignmentFlag.AlignLeft)
        g.cl.addWidget(_muted("Read-only here: the Sample values of this dataset are edited in Data Elaboration. A change "
                              "makes this result Outdated."))
        foot = QHBoxLayout()
        self.pending = QLabel("Changes not applied")
        self.pending.setProperty("pending", True)
        self.reanalyze = _btn("Reanalyze", "goElab")
        self.reanalyze.setProperty("run", True)                     # amber: starts a calculation
        self.reanalyze.clicked.connect(lambda: self.ctl.analyze(self.ds_id))
        foot.addWidget(self.pending)
        foot.addStretch(1)
        foot.addWidget(self.reanalyze)
        g.cl.addLayout(foot)
        self.details_btn = _btn("Analysis Details", "quiet")
        self.details_btn.setCheckable(True)
        self.details_btn.setIconSize(QSize(14, 14))
        self.details_btn.toggled.connect(self._details)
        g.cl.addWidget(self.details_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.details = QFrame()
        self.details.setProperty("panel", True)
        self.dgrid = QGridLayout(self.details)
        self.dgrid.setContentsMargins(12, 10, 12, 10)
        self.dgrid.setHorizontalSpacing(14)
        self.details.hide()
        g.cl.addWidget(self.details)
        bl.addWidget(g)

        # ---- Cycle Filter (collapsed)
        self.groups = []
        g = self._group("Cycle Filter")
        g.cl.addWidget(LabelRow(theme, "Cycles", FILTER_INFO))
        self.f_edit = QLineEdit()
        self.f_edit.setPlaceholderText("All cycles")
        self.f_edit.setAccessibleName("Cycle filter")
        self.f_edit.textEdited.connect(lambda txt: self._view(lambda v: v.type_filter(txt)))
        self.f_edit.returnPressed.connect(lambda: self._view(lambda v: v.apply_filter()))
        self.f_hint = _muted(FILTER_HINT)
        r = QHBoxLayout()
        self.f_apply, self.f_clear = _btn("Apply Filter", "go"), _btn("Clear", "ghost")
        self.f_apply.clicked.connect(lambda: self._view(lambda v: v.apply_filter()))
        self.f_clear.clicked.connect(lambda: self._view(lambda v: v.clear_filter()))
        self.f_state = _muted()
        r.addWidget(self.f_apply)
        r.addWidget(self.f_clear)
        r.addStretch(1)
        r.addWidget(self.f_state)
        for x in (self.f_edit, self.f_hint):
            g.cl.addWidget(x)
        g.cl.addLayout(r)
        r0 = QHBoxLayout()
        r0.addWidget(QLabel("Show Preliminary (cycle 0)"))
        r0.addStretch(1)
        self.c0 = Switch(theme, "Show Preliminary (cycle 0)", False)
        self.c0.toggled.connect(lambda on: self._view(lambda v: setattr(v, "show_cycle0", on)))
        r0.addWidget(self.c0)
        g.cl.addLayout(r0)
        g.cl.addWidget(_muted("Cycle 0 is the phase before the first charge or discharge. It is always included in an "
                              "unfiltered export."))

        # ---- Plot Colors
        g = self._group("Plot Colors")
        self.sq: dict[str, ColorSquare] = {}
        self.sq_lbl: dict[str, QLabel] = {}
        for axis in ("y1", "y2"):
            row = QHBoxLayout()
            sq = ColorSquare(theme, axis.upper())
            sq.colorChosen.connect(lambda col, a=axis: self._view(lambda v: setattr(v, f"{a}_color", col)))
            lbl = QLabel()
            row.addWidget(sq)
            row.addWidget(lbl)
            row.addStretch(1)
            g.cl.addLayout(row)
            self.sq[axis], self.sq_lbl[axis] = sq, lbl
        rc = _btn("Reset Colors", "link")
        from ..explorer import CHART
        rc.clicked.connect(lambda: self._view(lambda v: (setattr(v, "y1_color", CHART["y1"]), setattr(v, "y2_color", CHART["y2"]))))
        g.cl.addWidget(rc, 0, Qt.AlignmentFlag.AlignLeft)

        # ---- Legend
        g = self._group("Legend")
        row = QHBoxLayout()
        row.addWidget(QLabel("Show Legend"))
        row.addStretch(1)
        self.legend = Switch(theme, "Show Legend", True)
        self.legend.toggled.connect(lambda on: self._view(lambda v: setattr(v, "legend", on)))
        row.addWidget(self.legend)
        g.cl.addLayout(row)

        # ---- Axis Ranges
        g = self._group("Axis Ranges")
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.addWidget(_muted("Min"), 0, 1)
        grid.addWidget(_muted("Max"), 0, 2)
        self.r_lbl, self.r_min, self.r_max = {}, {}, {}
        for i, axis in enumerate(("x", "y1", "y2"), start=1):
            l, mn, mx = QLabel(), QLineEdit(), QLineEdit()
            for f in (mn, mx):
                f.setAlignment(Qt.AlignmentFlag.AlignRight)
                f.textEdited.connect(lambda _t: setattr(self, "_range_dirty", True))
                f.returnPressed.connect(self._apply_ranges)
            grid.addWidget(l, i, 0)
            grid.addWidget(mn, i, 1)
            grid.addWidget(mx, i, 2)
            self.r_lbl[axis], self.r_min[axis], self.r_max[axis] = l, mn, mx
        w = QWidget()
        w.setLayout(grid)
        g.cl.addWidget(w)
        self.r_err = QLabel()
        self.r_err.setProperty("hintErr", True)
        self.r_err.hide()
        g.cl.addWidget(self.r_err)
        row = QHBoxLayout()
        ap, rv = _btn("Apply Ranges", "go"), _btn("Reset View", "ghost")
        ap.clicked.connect(self._apply_ranges)
        rv.clicked.connect(lambda: (setattr(self, "_range_dirty", False), self._view(lambda v: setattr(v, "viewport", Viewport()))))
        row.addWidget(ap)
        row.addWidget(rv)
        row.addStretch(1)
        g.cl.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Y2 uses the Y1 scale"))
        row.addStretch(1)
        self.same = Switch(theme, "Y2 uses the Y1 scale", False)
        self.same.toggled.connect(lambda on: self._view(lambda v: setattr(v, "y2_same_scale", on)))
        row.addWidget(self.same)
        g.cl.addLayout(row)
        g.cl.addWidget(_muted("Display only: Y1 and Y2 share the left scale, useful to compare charge and discharge."))

        # ---- Plot Style
        g = self._group("Plot Style")
        self.style = SegmentedControl(list(PLOT_STYLES), small=True, aria="Plot style")
        self.style.valueChanged.connect(lambda s: self._view(lambda v: setattr(v, "style", s)))
        g.cl.addWidget(self.style, 0, Qt.AlignmentFlag.AlignLeft)
        for g in self.groups:
            bl.addWidget(g)
        bl.addStretch(1)
        ctl.changed.connect(lambda ds: self.refresh() if ds == self.ds_id else None)
        ctl.tableReady.connect(lambda ds: self.refresh() if ds == self.ds_id else None)
        theme.changed.connect(self._icons)
        self._icons()

    def _group(self, title: str) -> Accordion:
        g = Accordion(self.theme, title)
        g.head.setChecked(False)                              # collapsed by default in Capacity
        self.groups.append(g)
        return g

    def _icons(self) -> None:
        self.details_btn.setIcon(make_icon("chevron-up" if self.details_btn.isChecked() else "chevron-down",
                                           self.theme.tokens.muted, size=14))

    def _details(self, on: bool) -> None:
        self.details.setVisible(on)
        self._icons()

    # ---------------------------------------------------------------- behaviour
    def _view(self, fn) -> None:
        c = self.ctl.cap(self.ds_id) if self.ds_id else None
        if c is None:
            return
        fn(c.view)
        self.ctl.changed.emit(self.ds_id)
        self.changedView.emit()

    def show_dataset(self, ds_id: Optional[str]) -> None:
        self.ds_id = ds_id
        self._range_dirty = False
        self.refresh()

    def _apply_ranges(self) -> None:
        c = self.ctl.cap(self.ds_id)
        if c is None:
            return
        vals = {}
        for axis in ("x", "y1", "y2"):
            if axis == "y2" and (c.view.y2 is None or c.view.y2_same_scale):
                vals[axis] = None
                continue
            r, err = parse_range(self.r_min[axis].text(), self.r_max[axis].text())
            if err:
                self.r_err.setText(f"{self.r_lbl[axis].text()}: {err}")
                self.r_err.show()
                return
            vals[axis] = r
        self.r_err.hide()
        self._range_dirty = False
        self._view(lambda v: setattr(v, "viewport", Viewport(vals["x"], vals["y1"], vals["y2"])))

    def refresh(self) -> None:
        c = self.ctl.cap(self.ds_id) if self.ds_id else None
        d = self.ctl.state.get(self.ds_id) if self.ds_id else None
        if c is None or d is None:
            return
        u = self.ctl.units.units_for(self.ds_id)
        p = self.ctl.current_params(self.ds_id)
        mods = c.modified(p)
        self.order.setText(CYCLE_ORDER_LABELS.get(d.elab.result.params.cycle_order, "—") if d.elab.result else "—")
        if not self.ref.hasFocus() and self.ref.text() != c.reference_text:
            self.ref.setText(c.reference_text)
        set_prop(self.ref, "error", bool(c.reference_error))
        set_prop(self.ref, "changed", "reference_cycle" in mods)
        self.ref_row.set_modified("reference_cycle" in mods)
        hint, warn = reference_hint(c.reference, self.ctl.cycles_of_elaboration(self.ds_id))
        self.ref_hint.setText(c.reference_error or hint)
        set_prop(self.ref_hint, "hintErr", bool(c.reference_error) or warn)
        s = d.elab.sample
        self.mass.setText(f"{s.active_mass_mg:g} mg" if s.active_mass_mg else "Not set")
        self.area.setText(f"{s.surface_area_cm2:g} cm²" if s.surface_area_cm2 else "Not set")
        self.mass_tag.setVisible("active_material_mass_mg" in mods)
        self.area_tag.setVisible("electrode_area_cm2" in mods)
        self.pending.setVisible(bool(mods) or bool(c.reference_error))
        ok = c.can_analyze(p, d.elab.is_running)
        self.reanalyze.setText("Reanalyze" if c.has_result else "Analyze")
        self.reanalyze.setEnabled(ok)
        self.reanalyze.setToolTip("" if ok else (c.reference_error or "Not available while an analysis or elaboration runs"))
        # details
        while self.dgrid.count():
            it = self.dgrid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.details_btn.setVisible(c.result is not None)
        if c.result is not None:
            t = self.ctl.tables.get(self.ds_id)
            rep = t.report if t else {}
            refq = rep.get("reference_capacity_ah")
            rp = c.result.params
            rows = [("Result ID", c.result.result_id),
                    ("Cycle Order", CYCLE_ORDER_LABELS.get(rp.cycle_order, rp.cycle_order)),
                    ("Reference Cycle", str(rp.reference_cycle)),
                    ("Reference capacity", "not available" if refq is None else
                     f"{refq * scale(CAPACITY, u.capacity):.6g} {u.capacity}"),
                    ("Active Material Mass", f"{rp.active_material_mass_mg:g} mg" if rp.active_material_mass_mg else "Not set"),
                    ("Surface Area", f"{rp.electrode_area_cm2:g} cm²" if rp.electrode_area_cm2 else "Not set")]
            rows += list(c.result.details)
            for i, (k, val) in enumerate(rows):
                self.dgrid.addWidget(_muted(k, 12), i, 0)
                v = QLabel(val)
                v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                self.dgrid.addWidget(v, i, 1)
        # view groups
        v = c.view
        if self.f_edit.text() != v.filter_input:
            self.f_edit.setText(v.filter_input)
        set_prop(self.f_edit, "error", bool(v.filter_error))
        self.f_hint.setText(v.filter_error or FILTER_HINT)
        set_prop(self.f_hint, "hintErr", bool(v.filter_error))
        self.f_apply.setEnabled(v.can_apply_filter)
        self.f_clear.setEnabled(bool(v.applied) or bool(v.filter_input.strip()))
        self.f_state.setText("Not applied" if v.filter_error else ("Applied" if v.applied else "All cycles"))
        for sw, val in ((self.c0, v.show_cycle0), (self.legend, v.legend), (self.same, v.y2_same_scale)):
            sw.blockSignals(True)
            sw.setChecked(val)
            sw.blockSignals(False)
        for axis in ("y1", "y2"):
            self.sq[axis].set_color(getattr(v, f"{axis}_color"))
            key = getattr(v, axis)
            self.sq_lbl[axis].setText(f"{axis.upper()} · {cap_label(key, u).split(' [')[0]}")
        self.style.set_value(v.style)
        for axis in ("x", "y1", "y2"):
            key = getattr(v, axis)
            lab = cap_label(key, u)
            self.r_lbl[axis].setText(f"{axis.upper()} [{lab.split('[')[1]}" if "[" in lab else axis.upper())
            on = axis != "y2" or (v.y2 is not None and not v.y2_same_scale)
            self.r_min[axis].setEnabled(on)
            self.r_max[axis].setEnabled(on)
        if not self._range_dirty:
            vp = v.viewport
            for axis in ("x", "y1", "y2"):
                r = getattr(vp, axis)
                self.r_min[axis].setText("" if r is None else f"{r.lo:.15g}".replace("-", "−"))
                self.r_max[axis].setText("" if r is None else f"{r.hi:.15g}".replace("-", "−"))
