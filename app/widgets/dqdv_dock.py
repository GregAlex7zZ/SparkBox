"""Right dock of the dQ/dV section: 'dQ/dV Settings'. Analysis expanded; the plot groups start collapsed. Every visual property of the plot is customisable : a colour per cycle (and per phase
when 'Same colour for charge and discharge' is off), line style per phase, line width, raw opacity, legend, axis
ranges and plot style. Display only: nothing here recalculates."""
from __future__ import annotations
from typing import Optional
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)
from ..dqdv import LINE_STYLES
from ..dqdv_controller import DqdvController
from ..explorer import PLOT_STYLES, Viewport, parse_range
from ..theme import ThemeManager
from .controls import ColorSquare, SegmentedControl, Switch
from .dqdv_page import DqdvParamsForm
from .elab_dock import Accordion


def _btn(text, kind) -> QPushButton:
    b = QPushButton(text)
    b.setProperty(kind, True)
    b.setFixedHeight(30)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def _muted(text="", size=11) -> QLabel:
    l = QLabel(text)
    l.setProperty("muted", True)
    l.setWordWrap(True)
    l.setStyleSheet(f"font-size: {size * 0.75:g}pt;")
    return l


class DqdvSettingsDock(QFrame):
    unitsRequested = Signal()

    def __init__(self, theme: ThemeManager, ctl: DqdvController):
        super().__init__()
        self.setObjectName("DockCard")
        self.theme, self.ctl, self.ds_id = theme, ctl, None
        self._colors_key = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(12)
        head = QHBoxLayout()
        t = QLabel("dQ/dV Settings")
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
        # Analysis
        g = Accordion(theme, "Analysis")
        self.form = DqdvParamsForm(theme, ctl, docked=True)
        g.cl.addWidget(self.form)
        g.cl.addWidget(_muted("Phase, the cycles shown and Raw / Smoothed are chosen above the chart and never recalculate."))
        foot = QHBoxLayout()
        self.pending = QLabel("Changes not applied")
        self.pending.setProperty("pending", True)
        self.go = _btn("Analyze", "goElab")
        self.go.setProperty("run", True)                     # amber: starts a calculation
        self.go.clicked.connect(lambda: self.ctl.analyze(self.ds_id))
        foot.addWidget(self.pending)
        foot.addStretch(1)
        foot.addWidget(self.go)
        w = QWidget()
        w.setLayout(foot)
        g.cl.addWidget(w)
        bl.addWidget(g)
        # Trace Colors
        g2 = self._group("Trace Colors")
        row = QHBoxLayout()
        row.addWidget(QLabel("Same colour for charge and discharge"))
        row.addStretch(1)
        self.same = Switch(theme, "Same colour for charge and discharge", True)
        self.same.toggled.connect(lambda on: self._view(lambda v: setattr(v, "same_color", on)))
        row.addWidget(self.same)
        w = QWidget()
        w.setLayout(row)
        g2.cl.addWidget(w)
        self.colors_box = QWidget()
        self.colors_grid = QGridLayout(self.colors_box)
        self.colors_grid.setContentsMargins(0, 0, 0, 0)
        self.colors_grid.setHorizontalSpacing(10)
        self.colors_grid.setVerticalSpacing(6)
        g2.cl.addWidget(self.colors_box)
        rc = _btn("Reset Colors", "link")
        rc.clicked.connect(lambda: self._view(lambda v: v.reset_colors()))
        g2.cl.addWidget(rc, 0, Qt.AlignmentFlag.AlignLeft)
        bl.addWidget(g2)
        # Line Style
        g3 = self._group("Line Style")
        grid = QGridLayout()
        self.dash: dict[str, QComboBox] = {}
        for r, ph in enumerate(("charge", "discharge")):
            grid.addWidget(QLabel(ph.capitalize()), r, 0)
            cb = QComboBox()
            for k, t2 in LINE_STYLES:
                cb.addItem(t2, k)
            cb.setAccessibleName(f"{ph} line style")
            cb.activated.connect(lambda i, p=ph, c=cb: self._view(lambda v: v.dash.__setitem__(p, c.itemData(i))))
            self.dash[ph] = cb
            grid.addWidget(cb, r, 1)
        grid.addWidget(QLabel("Line Width"), 2, 0)
        self.width = QDoubleSpinBox()
        self.width.setRange(0.5, 6.0)
        self.width.setSingleStep(0.1)
        self.width.setSuffix(" px")
        self.width.valueChanged.connect(lambda val: self._view(lambda v: setattr(v, "line_width", float(val))))
        grid.addWidget(self.width, 2, 1)
        grid.addWidget(QLabel("Raw Opacity (Both)"), 3, 0)
        self.alpha = QSpinBox()
        self.alpha.setRange(10, 100)
        self.alpha.setSuffix(" %")
        self.alpha.valueChanged.connect(lambda val: self._view(lambda v: setattr(v, "raw_alpha", val / 100.0)))
        grid.addWidget(self.alpha, 3, 1)
        w = QWidget()
        w.setLayout(grid)
        g3.cl.addWidget(w)
        g3.cl.addWidget(_muted("Charge and discharge use the same style by default. Raw opacity applies when Plot = Both."))
        bl.addWidget(g3)
        # Legend
        g4 = self._group("Legend")
        row = QHBoxLayout()
        row.addWidget(QLabel("Show Legend"))
        row.addStretch(1)
        self.legend = Switch(theme, "Show Legend", True)
        self.legend.toggled.connect(lambda on: self._view(lambda v: setattr(v, "legend", on)))
        row.addWidget(self.legend)
        w = QWidget()
        w.setLayout(row)
        g4.cl.addWidget(w)
        bl.addWidget(g4)
        # Axis Ranges
        g5 = self._group("Axis Ranges")
        ag = QGridLayout()
        self.rng: dict[str, tuple[QLineEdit, QLineEdit]] = {}
        for r, (axis, title) in enumerate((("x", "X"), ("y", "Y"))):
            ag.addWidget(QLabel(title), r, 0)
            a, b = QLineEdit(), QLineEdit()
            a.setPlaceholderText("auto")
            b.setPlaceholderText("auto")
            a.setAccessibleName(f"{title} minimum")
            b.setAccessibleName(f"{title} maximum")
            ag.addWidget(a, r, 1)
            ag.addWidget(QLabel("–"), r, 2)
            ag.addWidget(b, r, 3)
            self.rng[axis] = (a, b)
        w = QWidget()
        w.setLayout(ag)
        g5.cl.addWidget(w)
        self.r_err = QLabel()
        self.r_err.setProperty("hintErr", True)
        self.r_err.hide()
        g5.cl.addWidget(self.r_err)
        rr = QHBoxLayout()
        ap, au = _btn("Apply", "ghost"), _btn("Automatic", "link")
        ap.clicked.connect(self._apply_ranges)
        au.clicked.connect(lambda: self._view(lambda v: setattr(v, "viewport", Viewport())))
        rr.addWidget(ap)
        rr.addWidget(au)
        rr.addStretch(1)
        w = QWidget()
        w.setLayout(rr)
        g5.cl.addWidget(w)
        bl.addWidget(g5)
        # Plot Style
        g6 = self._group("Plot Style")
        self.style = SegmentedControl(list(PLOT_STYLES), small=True, aria="Plot Style")
        self.style.valueChanged.connect(lambda val: self._view(lambda v: setattr(v, "style", val)))
        g6.cl.addWidget(self.style, 0, Qt.AlignmentFlag.AlignLeft)
        bl.addWidget(g6)
        bl.addStretch(1)
        ctl.changed.connect(lambda ds: self.refresh() if ds == self.ds_id else None)

    def _group(self, title: str) -> Accordion:
        g = Accordion(self.theme, title)
        g.head.setChecked(False)
        return g

    def _view(self, fn) -> None:
        q = self.ctl.dq(self.ds_id) if self.ds_id else None
        if q is not None:
            fn(q.view)
            self.ctl.changed.emit(self.ds_id)

    def _apply_ranges(self) -> None:
        vals = {}
        for axis, (a, b) in self.rng.items():
            r, err = parse_range(a.text(), b.text())
            if err:
                self.r_err.setText(f"{axis.upper()}: {err}")
                self.r_err.show()
                return
            vals[axis] = r
        self.r_err.hide()
        self._view(lambda v: setattr(v, "viewport", Viewport(vals["x"], vals["y"])))

    def show_dataset(self, ds_id: Optional[str]) -> None:
        self.ds_id = ds_id
        self.form.ds_id = ds_id
        self._colors_key = None
        self.refresh()

    def refresh(self) -> None:
        q = self.ctl.dq(self.ds_id) if self.ds_id else None
        if q is None:
            return
        self.form.refresh()
        v = q.view
        has = q.result is not None
        mods = self.ctl.modified(self.ds_id)
        self.go.setText("Reanalyze" if has else "Analyze")
        b = self.ctl.blocker(self.ds_id)
        self.go.setEnabled(not b)
        self.go.setToolTip(b)
        self.pending.setVisible(has and bool(mods))
        for sw, val in ((self.same, v.same_color), (self.legend, v.legend)):
            sw.blockSignals(True)
            sw.setChecked(val)
            sw.blockSignals(False)
        for ph, cb in self.dash.items():
            cb.setCurrentIndex(cb.findData(v.dash.get(ph, "solid")))
        for w, val in ((self.width, v.line_width), (self.alpha, round(v.raw_alpha * 100))):
            w.blockSignals(True)
            w.setValue(val)
            w.blockSignals(False)
        self.style.set_value(v.style)
        for axis, (a, b) in self.rng.items():
            r = v.viewport.x if axis == "x" else v.viewport.y1
            if not (a.hasFocus() or b.hasFocus()):
                a.setText("" if r is None else f"{r.lo:.6g}")
                b.setText("" if r is None else f"{r.hi:.6g}")
        analyzed = sorted(q.result.params.cycles) if has else []
        key = (tuple(analyzed), v.same_color, tuple(sorted(v.colors.items())))
        if key != self._colors_key:
            self._colors_key = key
            self._fill_colors(q, analyzed)

    def _fill_colors(self, q, analyzed) -> None:
        while self.colors_grid.count():
            w = self.colors_grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        v = q.view
        if not analyzed:
            self.colors_grid.addWidget(_muted("Colours appear after the analysis."), 0, 0)
            return
        if not v.same_color:
            self.colors_grid.addWidget(_muted("Charge"), 0, 1)
            self.colors_grid.addWidget(_muted("Discharge"), 0, 2)
        for r, c in enumerate(analyzed, start=1):
            self.colors_grid.addWidget(QLabel(f"Cycle {c}"), r, 0)
            for col, ph in enumerate(("charge",) if v.same_color else ("charge", "discharge"), start=1):
                sq = ColorSquare(self.theme, f"Cycle {c} {ph if not v.same_color else ''}".strip())
                sq.set_color(v.color(c, ph, analyzed))
                sq.colorChosen.connect(lambda hx, cc=c, pp=ph: self._view(lambda vv: vv.set_color(cc, pp, hx)))
                self.colors_grid.addWidget(sq, r, col)
