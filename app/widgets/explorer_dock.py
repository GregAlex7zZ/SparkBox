"""Right dock of the Data Explorer: 'Plot Settings'. Accordions expanded by default: Cycle Filter, Plot
Colors, Legend, Axis Ranges, Plot Style. Every control is disabled while Render All Points is active."""
from __future__ import annotations
from typing import Optional
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)
from ..explorer import FILTER_HINT, PLOT_STYLES, Viewport, axis_label, format_axis_bound, parse_range
from ..explorer_controller import ExplorerController
from ..theme import ThemeManager, set_prop
from .controls import ColorSquare, LabelRow, SegmentedControl, Switch
from .elab_dock import Accordion

FILTER_INFO = (
    "Shows only the cycles you list. Type cycle numbers or ranges separated by commas, then press Apply Filter.",
    "Examples: 1 · 1-100 · 1,6,9 · 1-5,9,12-20. Leave it empty to show all cycles.",
    "Cycle numbers are the ones of the current result. Numbers that do not exist are simply not shown.",
)
LOCK_TIP = "Turn off Render All Points to change"


def _btn(text: str, kind: str) -> QPushButton:
    b = QPushButton(text)
    b.setProperty(kind, True)
    b.setFixedHeight(30)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class PlotSettingsDock(QFrame):
    unitsRequested = Signal()

    def __init__(self, theme: ThemeManager, ctl: ExplorerController):
        super().__init__()
        self.setObjectName("DockCard")
        self.theme, self.ctl, self.ds_id = theme, ctl, None
        self._range_dirty = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(12)
        head = QHBoxLayout()
        t = QLabel("Plot Settings")
        t.setStyleSheet("font-size: 11.25pt; font-weight: 500;")
        units = _btn("Units…", "link")
        units.setToolTip("Open Settings › Units (app or this dataset)")
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
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setObjectName("TabScroll")
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # Cycle Filter
        g = Accordion(theme, "Cycle Filter")
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        v.addWidget(LabelRow(theme, "Cycles", FILTER_INFO))
        self.f_edit = QLineEdit()
        self.f_edit.setPlaceholderText("All cycles")
        self.f_edit.setAccessibleName("Cycle filter")
        self.f_edit.textEdited.connect(lambda txt: self._edit(lambda e: e.type_filter(txt) or False, reload=False))
        self.f_edit.returnPressed.connect(lambda: self._edit(lambda e: e.apply_filter()))
        self.f_hint = QLabel(FILTER_HINT)
        self.f_hint.setWordWrap(True)
        self.f_hint.setProperty("muted", True)
        self.f_hint.setStyleSheet("font-size: 8.25pt;")
        row = QHBoxLayout()
        self.f_apply = _btn("Apply Filter", "go")
        self.f_clear = _btn("Clear", "ghost")
        self.f_state = QLabel()
        self.f_state.setProperty("muted", True)
        self.f_state.setStyleSheet("font-size: 8.25pt;")
        self.f_apply.clicked.connect(lambda: self._edit(lambda e: e.apply_filter()))
        self.f_clear.clicked.connect(lambda: self._edit(lambda e: e.clear_filter()))
        row.addWidget(self.f_apply)
        row.addWidget(self.f_clear)
        row.addStretch(1)
        row.addWidget(self.f_state)
        for w in (self.f_edit, self.f_hint):
            v.addWidget(w)
        v.addLayout(row)
        g.cl.addWidget(box)
        bl.addWidget(g)

        # Point Tracking
        g = Accordion(theme, "Point Tracking")
        r = QHBoxLayout()
        lab = QLabel("Track points on hover")
        self.tracking = Switch(theme, "Track points on hover", True)
        self.tracking.setToolTip("Show the nearest real data point while the pointer moves over the chart.")
        self.tracking.toggled.connect(
            lambda on: self._edit(lambda e: e.set_point_tracking(on), reload=False))
        r.addWidget(lab)
        r.addStretch(1)
        r.addWidget(self.tracking)
        w = QWidget()
        w.setLayout(r)
        g.cl.addWidget(w)
        bl.addWidget(g)

        # Plot Colors
        g = Accordion(theme, "Plot Colors")
        self.sq: dict[str, ColorSquare] = {}
        self.sq_lbl: dict[str, QLabel] = {}
        self.sq_hex: dict[str, QLabel] = {}
        for axis in ("y1", "y2"):
            r = QHBoxLayout()
            r.setSpacing(10)
            sq = ColorSquare(theme, axis.upper())
            sq.colorChosen.connect(lambda c, a=axis: self._edit(lambda e: e.set_color(a, c), reload=False))
            lbl, hx = QLabel(), QLabel()
            hx.setProperty("muted", True)
            hx.setStyleSheet("font-size: 8.25pt;")
            r.addWidget(sq)
            r.addWidget(lbl)
            r.addStretch(1)
            r.addWidget(hx)
            w = QWidget()
            w.setLayout(r)
            g.cl.addWidget(w)
            self.sq[axis], self.sq_lbl[axis], self.sq_hex[axis] = sq, lbl, hx
        self.c_reset = _btn("Reset Colors", "link")
        self.c_reset.clicked.connect(lambda: self._edit(lambda e: e.reset_colors() or False, reload=False))
        g.cl.addWidget(self.c_reset, 0, Qt.AlignmentFlag.AlignLeft)
        bl.addWidget(g)

        # Legend
        g = Accordion(theme, "Legend")
        r = QHBoxLayout()
        lab = QLabel("Show Legend")
        self.legend = Switch(theme, "Show Legend", True)
        self.legend.toggled.connect(lambda on: self._edit(lambda e: e.set_legend(on), reload=False))
        r.addWidget(lab)
        r.addStretch(1)
        r.addWidget(self.legend)
        w = QWidget()
        w.setLayout(r)
        g.cl.addWidget(w)
        bl.addWidget(g)

        # Axis Ranges
        g = Accordion(theme, "Axis Ranges")
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        for col, txt in ((1, "Min"), (2, "Max")):
            l = QLabel(txt)
            l.setProperty("muted", True)
            l.setStyleSheet("font-size: 8.25pt;")
            grid.addWidget(l, 0, col)
        self.r_lbl: dict[str, QLabel] = {}
        self.r_min: dict[str, QLineEdit] = {}
        self.r_max: dict[str, QLineEdit] = {}
        for i, axis in enumerate(("x", "y1", "y2"), start=1):
            l = QLabel()
            l.setStyleSheet("font-size: 9pt;")
            mn, mx = QLineEdit(), QLineEdit()
            for f in (mn, mx):
                f.setMinimumWidth(54)
                f.setMaximumWidth(88)
                f.setAlignment(Qt.AlignmentFlag.AlignRight)
                f.textEdited.connect(self._range_typed)
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
        r = QHBoxLayout()
        self.r_apply = _btn("Apply Ranges", "go")
        self.r_reset = _btn("Reset View", "ghost")
        self.r_apply.clicked.connect(self._apply_ranges)
        self.r_reset.clicked.connect(lambda: (setattr(self, "_range_dirty", False), self._edit(lambda e: e.reset_view())))
        r.addWidget(self.r_apply)
        r.addWidget(self.r_reset)
        r.addStretch(1)
        g.cl.addLayout(r)
        bl.addWidget(g)

        # Plot Style (open decision O-27)
        g = Accordion(theme, "Plot Style")
        self.style = SegmentedControl(list(PLOT_STYLES), small=True, aria="Plot style")
        self.style.valueChanged.connect(lambda v: self._edit(lambda e: e.set_style(v), reload=False))
        g.cl.addWidget(self.style, 0, Qt.AlignmentFlag.AlignLeft)
        bl.addWidget(g)
        bl.addStretch(1)

        ctl.changed.connect(lambda ds: self.refresh() if ds == self.ds_id else None)
        ctl.viewReady.connect(lambda ds: self._sync_ranges() if ds == self.ds_id else None)
        ctl.units.changed.connect(lambda ds: self.refresh() if ds == self.ds_id else None)

    # ---------------------------------------------------------------- behaviour
    def _edit(self, fn, reload: bool = True) -> None:
        if self.ds_id:
            self.ctl.edit(self.ds_id, fn, reload=reload)

    def show_dataset(self, ds_id: Optional[str]) -> None:
        self.ds_id = ds_id
        self._range_dirty = False
        self.refresh()
        self._sync_ranges()

    def _range_typed(self, _t: str) -> None:
        self._range_dirty = True
        self.r_err.hide()

    def _apply_ranges(self) -> None:
        e = self.ctl.ex(self.ds_id)
        if e is None:
            return
        vals = {}
        for axis in ("x", "y1", "y2"):
            if axis == "y2" and e.y2 is None:
                vals[axis] = None
                continue
            r, err = parse_range(self.r_min[axis].text(), self.r_max[axis].text())
            if err:
                self.r_err.setText(f"{self.r_lbl[axis].text()}: {err}")
                self.r_err.show()
                set_prop(self.r_min[axis], "error", True)
                set_prop(self.r_max[axis], "error", True)
                return
            set_prop(self.r_min[axis], "error", False)
            set_prop(self.r_max[axis], "error", False)
            vals[axis] = r
        self._range_dirty = False
        self._edit(lambda e: e.set_viewport(Viewport(vals["x"], vals["y1"], vals["y2"])))

    def _sync_ranges(self) -> None:
        """Follow the visible ranges unless the user is typing; show at most four significant digits."""
        if self._range_dirty or not self.ds_id:
            return
        view = self.ctl.views.get(self.ds_id)
        e = self.ctl.ex(self.ds_id)
        if e is None:
            return
        rng = {"x": e.viewport.x, "y1": e.viewport.y1, "y2": e.viewport.y2}
        for axis in ("x", "y1", "y2"):
            r = rng[axis]
            pair = (r.lo, r.hi) if r else (getattr(view, f"{axis}_range", None) if view else None)
            if axis == "y2" and e.y2 is None:
                pair = None
            self.r_min[axis].setText(format_axis_bound(pair[0]) if pair else "")
            self.r_max[axis].setText(format_axis_bound(pair[1]) if pair else "")

    def refresh(self) -> None:
        e = self.ctl.ex(self.ds_id) if self.ds_id else None
        if e is None:
            return
        u = self.ctl.units.units_for(self.ds_id)
        locked = e.locked
        # filter
        if self.f_edit.text() != e.filter_input:
            self.f_edit.setText(e.filter_input)
        set_prop(self.f_edit, "error", bool(e.filter_error))
        self.f_edit.setAccessibleDescription(e.filter_error)
        self.f_hint.setText(e.filter_error or FILTER_HINT)
        set_prop(self.f_hint, "hintErr", bool(e.filter_error))
        self.f_apply.setEnabled(e.can_apply_filter)
        self.f_clear.setEnabled(e.can_clear_filter)
        self.f_state.setText("Not applied" if e.filter_error else ("Applied" if e.applied else "All cycles"))
        # colours / legend / style
        for axis in ("y1", "y2"):
            key = getattr(e, axis)
            self.sq[axis].set_color(getattr(e, f"{axis}_color"))
            self.sq_lbl[axis].setText(f"{axis.upper()} · {axis_label(key, u).split(' [')[0]}")
            self.sq_hex[axis].setText(getattr(e, f"{axis}_color"))
        self.legend.blockSignals(True)
        self.legend.setChecked(e.legend)
        self.legend.blockSignals(False)
        self.tracking.blockSignals(True)
        self.tracking.setChecked(e.point_tracking)
        self.tracking.blockSignals(False)
        self.style.set_value(e.style)
        # ranges
        for axis in ("x", "y1", "y2"):
            key = getattr(e, axis)
            self.r_lbl[axis].setText(f"{axis.upper()} [{axis_label(key, u).split('[')[1]}" if key and "[" in axis_label(key, u)
                                     else axis.upper())
            on = not locked and (axis != "y2" or e.y2 is not None)
            self.r_min[axis].setEnabled(on)
            self.r_max[axis].setEnabled(on)
        # lock everything while Render All Points is active
        for w in (self.f_edit, self.f_apply, self.f_clear, self.c_reset, self.legend, self.tracking, self.style, self.r_apply,
                  self.r_reset, *self.sq.values()):
            if locked:
                w.setEnabled(False)
                w.setToolTip(LOCK_TIP)
            elif w not in (self.f_apply, self.f_clear):
                w.setEnabled(True)
                if w.toolTip() == LOCK_TIP:
                    w.setToolTip("")
        self._sync_ranges()

