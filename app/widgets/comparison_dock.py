"""Right dock of Comparison: 'Comparison Settings'. Datasets (expanded): one checkbox per open dataset with
its colour square and its state for the current family; datasets without a drawable result are listed but cannot be
checked (analyze them in their own section; nothing runs from here). Appearance, Legend, Axis Ranges and Plot Style
start collapsed. Display only."""
from __future__ import annotations
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)
from ..comparison import OUTDATED
from ..comparison_controller import ComparisonController
from ..explorer import PLOT_STYLES, Viewport, parse_range
from ..theme import ThemeManager
from .controls import ColorSquare, SegmentedControl, Switch
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


class ComparisonDock(QFrame):
    unitsRequested = Signal()

    def __init__(self, theme: ThemeManager, ctl: ComparisonController):
        super().__init__()
        self.setObjectName("DockCard")
        self.theme, self.ctl = theme, ctl
        self._key = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(12)
        head = QHBoxLayout()
        t = QLabel("Comparison Settings")
        t.setStyleSheet("font-size: 11.25pt; font-weight: 500;")
        units = _btn("Units…", "link")
        units.setToolTip("Comparison always uses the app units (dataset-only units are ignored)")
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
        g = Accordion(theme, "Datasets")
        self.list = QWidget()
        self.grid = QGridLayout(self.list)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(6)
        g.cl.addWidget(self.list)
        g.cl.addWidget(_muted("Checking a dataset never runs an analysis. Not analyzed results are listed but cannot be "
                              "drawn: analyze them in their own section first. Outdated results are drawn with a badge."))
        bl.addWidget(g)
        g2 = self._group("Appearance")
        row = QHBoxLayout()
        row.addWidget(QLabel("Lighter tones for later cycles"))
        row.addStretch(1)
        self.shade = Switch(theme, "Lighter tones for later cycles", True)
        self.shade.toggled.connect(lambda on: self.ctl.edit(lambda cs: setattr(cs, "cycle_shading", on), reload=False))
        row.addWidget(self.shade)
        w = QWidget()
        w.setLayout(row)
        g2.cl.addWidget(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("Line Width"))
        row.addStretch(1)
        self.width = QDoubleSpinBox()
        self.width.setRange(0.5, 6.0)
        self.width.setSingleStep(0.1)
        self.width.setSuffix(" px")
        self.width.valueChanged.connect(lambda val: self.ctl.edit(lambda cs: setattr(cs, "line_width", float(val)), reload=False))
        row.addWidget(self.width)
        w = QWidget()
        w.setLayout(row)
        g2.cl.addWidget(w)
        g2.cl.addWidget(_muted("Each dataset keeps its colour while the app is open; click its square to change it."))
        bl.addWidget(g2)
        g3 = self._group("Legend")
        row = QHBoxLayout()
        row.addWidget(QLabel("Show Legend"))
        row.addStretch(1)
        self.legend = Switch(theme, "Show Legend", True)
        self.legend.toggled.connect(lambda on: self.ctl.edit(lambda cs: setattr(cs, "legend", on), reload=False))
        row.addWidget(self.legend)
        w = QWidget()
        w.setLayout(row)
        g3.cl.addWidget(w)
        bl.addWidget(g3)
        g4 = self._group("Axis Ranges")
        ag = QGridLayout()
        self.rng = {}
        for r, (axis, title) in enumerate((("x", "X"), ("y", "Y"))):
            ag.addWidget(QLabel(title), r, 0)
            a, b = QLineEdit(), QLineEdit()
            a.setPlaceholderText("auto")
            b.setPlaceholderText("auto")
            ag.addWidget(a, r, 1)
            ag.addWidget(QLabel("–"), r, 2)
            ag.addWidget(b, r, 3)
            self.rng[axis] = (a, b)
        w = QWidget()
        w.setLayout(ag)
        g4.cl.addWidget(w)
        self.r_err = QLabel()
        self.r_err.setProperty("hintErr", True)
        self.r_err.hide()
        g4.cl.addWidget(self.r_err)
        rr = QHBoxLayout()
        ap, au = _btn("Apply", "ghost"), _btn("Automatic", "link")
        ap.clicked.connect(self._apply_ranges)
        au.clicked.connect(lambda: self.ctl.edit(lambda cs: setattr(cs.view, "viewport", Viewport())))
        rr.addWidget(ap)
        rr.addWidget(au)
        rr.addStretch(1)
        w = QWidget()
        w.setLayout(rr)
        g4.cl.addWidget(w)
        bl.addWidget(g4)
        g5 = self._group("Plot Style")
        self.style = SegmentedControl(list(PLOT_STYLES), small=True, aria="Plot Style")
        self.style.valueChanged.connect(lambda val: self.ctl.edit(lambda cs: setattr(cs, "style", val), reload=False))
        g5.cl.addWidget(self.style)
        bl.addWidget(g5)
        bl.addStretch(1)
        ctl.changed.connect(self.refresh)

    def _group(self, title: str) -> Accordion:
        g = Accordion(self.theme, title)
        g.head.setChecked(False)
        return g

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
        self.ctl.edit(lambda cs: setattr(cs.view, "viewport", Viewport(vals["x"], vals["y"])))

    def refresh(self) -> None:
        cs = self.ctl.cs
        entries = self.ctl.entries()
        key = (tuple(entries), cs.family)
        if key != self._key:
            self._key = key
            while self.grid.count():
                w = self.grid.takeAt(0).widget()
                if w:
                    w.deleteLater()
            if not entries:
                self.grid.addWidget(_muted("No dataset is open."), 0, 0)
            for r, (ds_id, name, checked, drawable, status, color, kind) in enumerate(entries):
                cb = QCheckBox()
                cb.setChecked(checked and drawable)
                cb.setEnabled(drawable)
                cb.setAccessibleName(f"Compare {name}")
                cb.toggled.connect(lambda on, d=ds_id: self.ctl.toggle(d, on))
                sq = ColorSquare(self.theme, f"{name} colour")
                sq.set_color(color)
                sq.setEnabled(drawable)
                sq.colorChosen.connect(lambda hx, d=ds_id: self.ctl.edit(lambda c: c.set_color(d, hx), reload=False))
                txt = QWidget()
                tl = QVBoxLayout(txt)
                tl.setContentsMargins(0, 0, 0, 0)
                tl.setSpacing(0)
                nl = QLabel(name)
                nl.setToolTip(name)
                if not drawable:
                    nl.setProperty("muted", True)
                st = _muted(status)
                if kind == OUTDATED:
                    st.setStyleSheet(f"font-size: 8.25pt; color: {self.theme.tokens.warn};")
                tl.addWidget(nl)
                tl.addWidget(st)
                if drawable:                                  # the name and status toggle the checkbox too
                    txt.setCursor(Qt.CursorShape.PointingHandCursor)
                    txt.mousePressEvent = lambda _e, c=cb: c.toggle()
                self.grid.addWidget(cb, r, 0, Qt.AlignmentFlag.AlignTop)
                self.grid.addWidget(sq, r, 1, Qt.AlignmentFlag.AlignTop)
                self.grid.addWidget(txt, r, 2)
            self.grid.setColumnStretch(2, 1)
        for sw, val in ((self.shade, cs.cycle_shading), (self.legend, cs.legend)):
            sw.blockSignals(True)
            sw.setChecked(val)
            sw.blockSignals(False)
        self.width.blockSignals(True)
        self.width.setValue(cs.line_width)
        self.width.blockSignals(False)
        self.style.set_value(cs.style)
        vp = cs.view.viewport
        for axis, (a, b) in self.rng.items():
            r = vp.x if axis == "x" else vp.y1
            if not (a.hasFocus() or b.hasFocus()):
                a.setText("" if r is None else f"{r.lo:.6g}")
                b.setText("" if r is None else f"{r.hi:.6g}")
