"""Capacity page: header with state pill, banner, Ready / running cards, and the result view = X / Y1 / Y2
selectors over the 17 published columns, the chart (same gestures as the Data Explorer, every cycle drawn) and the Cycles
table below in a vertical splitter. Widgets never decide: they call CapacityController and re-read CapacityState."""
from __future__ import annotations
import math
from types import SimpleNamespace
from typing import Optional
import numpy as np
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QRadioButton,
                               QStackedWidget, QToolButton, QVBoxLayout, QWidget)
from ..file_dialogs import dataset_folder, save_export
from ..capacity import (CAP_COLUMNS, CapView, PILL, cap_label, fit_range, gap_breaks, hidden_note, missing_bases,
                        reference_hint, shown_columns)
from ..capacity_controller import CapacityController
from ..elaboration import CYCLE_ORDER_LABELS
from ..explorer import Viewport, filter_text
from ..formatting import fmt_int
from ..icons import make_icon, pixmap
from ..theme import ThemeManager, set_prop
from ..units import UnitPrefs
from .capacity_table import CapacityModel
from .chart import ChartWidget
from .controls import DividerSplitter, ElidedLabel, InfoButton, StatusPill
from .elab_table import FrozenTable

REFERENCE_INFO = (
    "Cycle whose second-phase capacity is the reference for the C-rate (gregorithm default: 2).",
    "The C-rate of each phase is calculated by gregorithm from the current of its constant-current (CC) portions divided "
    "by this reference capacity. If the reference cycle does not exist or its capacity is not valid, C-rates are not "
    "available. It is not a reference for capacity retention.",
    "Changing it does not alter the existing result: Reanalyze to apply it.",
)
ORDER_INFO = ("Set in Data Elaboration. The Capacity analysis must use the same Cycle Order as the current "
              "elaboration; change it there and process again.",)
BANNER_ICON = {"warn": "circle-alert", "p": "loader-circle", "n": "ban", "err": "circle-x"}


def _lab(text: str = "", muted: bool = False, size: Optional[int] = None, weight: Optional[int] = None,
         cls=QLabel) -> QLabel:
    """Themed label; cls=ElidedLabel for free text that should end with '…' (full text as tooltip) when short of room."""
    l = cls(text)
    if muted:
        l.setProperty("muted", True)
    # Use points rather than CSS pixels: Qt then retains a valid point size
    # through teardown (BUG-001) while preserving the 96-DPI design metric.
    css = (f"font-size: {size * 0.75:g}pt;" if size else "") + (f"font-weight: {weight};" if weight else "")
    if css:
        l.setStyleSheet(css)
    return l


def _btn(text: str, kind: str, h: int = 30) -> QPushButton:
    b = QPushButton(text)
    b.setProperty(kind, True)
    b.setFixedHeight(h)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class ExportDialog(QDialog):
    """Export Table…: format, columns, diagnostics; rows = cycles of the applied filter; values in the units
    shown. The file name is chosen after Export (never overwrites). ``columns`` are the keys offered, in table order:
    specific/areal columns are left out when their Sample value was not entered (capacity.shown_columns)."""

    def __init__(self, parent, units, rows_text: str, columns: list[str]):
        super().__init__(parent)
        self.setWindowTitle("Export Capacity Table")
        self.setMinimumWidth(460)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)
        v.addWidget(_lab("Export Capacity table", size=18, weight=500))
        v.addWidget(_lab(rows_text, muted=True))
        f = QHBoxLayout()
        f.addWidget(_lab("Format"))
        self.csv, self.parquet = QRadioButton("CSV"), QRadioButton("Parquet")
        self.csv.setChecked(True)
        f.addWidget(self.csv)
        f.addWidget(self.parquet)
        f.addStretch(1)
        v.addLayout(f)
        v.addWidget(_lab("Columns"))
        grid = QGridLayout()
        self.boxes: dict[str, QCheckBox] = {}
        for i, c in enumerate(c for c in CAP_COLUMNS if c.key in columns):
            b = QCheckBox(cap_label(c.key, units))
            b.setChecked(True)
            if c.key == "cycle":
                b.setEnabled(False)                              # the cycle ID is always exported
            self.boxes[c.key] = b
            grid.addWidget(b, i // 2, i % 2)
        v.addLayout(grid)
        self.diag = QCheckBox("Also export diagnostics (reason codes of empty cells)")
        v.addWidget(self.diag)
        note = _lab("Values are written at full precision in the units shown (Settings › Units). Empty cells stay empty.",
                    muted=True, size=12)
        note.setWordWrap(True)
        v.addWidget(note)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel, ok = _btn("Cancel", "ghost"), _btn("Export…", "goElab")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        v.addLayout(row)

    def choice(self) -> tuple[str, list[str], bool]:
        cols = [k for k, b in self.boxes.items() if b.isChecked()]
        return ("parquet" if self.parquet.isChecked() else "csv"), cols, self.diag.isChecked()


class CapacityPage(QWidget):
    goElaborationRequested = Signal()

    def __init__(self, theme: ThemeManager, ctl: CapacityController):
        super().__init__()
        self.theme, self.ctl = theme, ctl
        self.ds_id: Optional[str] = None
        self._combo_key = None
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 12)
        root.setSpacing(10)
        root.addLayout(self._header())
        self.banner = self._banner()
        root.addWidget(self.banner)
        self.body = QStackedWidget()
        self.body.addWidget(self._ready())         # 0
        self.body.addWidget(self._running())       # 1
        self.body.addWidget(self._result())        # 2
        root.addWidget(self.body, 1)
        ctl.changed.connect(lambda ds: self.refresh() if ds == self.ds_id else None)
        ctl.tableReady.connect(lambda ds: self._draw() if ds == self.ds_id else None)
        ctl.exportState.connect(self._export_state)
        ctl.units.changed.connect(lambda ds: self.refresh() if ds == self.ds_id else None)
        theme.changed.connect(self._icons)
        self._icons()

    # ---------------------------------------------------------------- construction
    def _header(self) -> QHBoxLayout:
        h = QHBoxLayout()
        h.setSpacing(12)
        h.addWidget(_lab("Capacity", size=16, weight=500))
        self.meta = _lab(muted=True, size=12, cls=ElidedLabel)
        self.units_pill = StatusPill(self.theme)
        self.pill = StatusPill(self.theme)
        h.addWidget(self.meta)
        h.addWidget(self.units_pill)
        h.addStretch(1)
        h.addWidget(self.pill)
        return h

    def _banner(self) -> QFrame:
        f = QFrame()
        bl = QHBoxLayout(f)
        bl.setContentsMargins(14, 10, 14, 10)
        bl.setSpacing(12)
        self.b_icon = QLabel()
        col = QVBoxLayout()
        col.setSpacing(4)
        self.b_title = _lab(weight=500)
        self.b_text = _lab(muted=True, size=12)
        self.b_text.setWordWrap(True)
        self.b_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        pr = QHBoxLayout()
        self.b_bar = QProgressBar()
        self.b_bar.setTextVisible(False)
        self.b_phase = _lab(muted=True, size=12)
        pr.addWidget(self.b_bar, 1)
        pr.addWidget(self.b_phase)
        self.b_prog = QWidget()
        self.b_prog.setLayout(pr)
        for w in (self.b_title, self.b_text, self.b_prog):
            col.addWidget(w)
        self.b_btn = _btn("", "goElab")
        self.b_btn.clicked.connect(self._banner_action)
        bl.addWidget(self.b_icon, 0, Qt.AlignmentFlag.AlignTop)
        bl.addLayout(col, 1)
        bl.addWidget(self.b_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        f.hide()
        return f

    def _ready(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(10)
        self.r_title = _lab("Ready to Analyze", size=20, weight=500)
        self.r_text = _lab(muted=True)
        self.r_text.setWordWrap(True)
        self.r_text.setFixedWidth(520)   # wrapped labels in a centred layout otherwise clip
        for x in (self.r_title, self.r_text):
            x.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            v.addWidget(x, 0, Qt.AlignmentFlag.AlignHCenter)
        box = QFrame()
        box.setProperty("panel", True)
        g = QGridLayout(box)
        g.setContentsMargins(18, 14, 18, 14)
        g.setHorizontalSpacing(16)
        g.setVerticalSpacing(10)
        self.r_order = QLabel()
        self.r_order.setProperty("chip", True)
        to_elab = _btn("Change in Data Elaboration", "link")
        to_elab.clicked.connect(self.goElaborationRequested)
        self.r_ref = QLineEdit()
        self.r_ref.setFixedWidth(72)
        self.r_ref.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.r_ref.setAccessibleName("Reference Cycle")
        self.r_ref.textEdited.connect(lambda t: self.ctl.set_reference_text(self.ds_id, t))
        self.r_ref.returnPressed.connect(lambda: self.ctl.analyze(self.ds_id))
        self.r_ref_hint = _lab(muted=True, size=12)
        self.r_mass, self.r_area = QLabel(), QLabel()
        for x in (self.r_mass, self.r_area):
            x.setProperty("chip", True)
        edit = _btn("Edit in Data Elaboration", "link")
        edit.clicked.connect(self.goElaborationRequested)
        edit_area = _btn("Edit in Data Elaboration", "link")
        edit_area.clicked.connect(self.goElaborationRequested)
        self.r_area_note = _lab(muted=True, size=12)
        rows = ((_lab("Cycle Order", muted=True, size=12), self._hrow(self.r_order, InfoButton(self.theme, "Cycle Order", ORDER_INFO), to_elab)),
                (self._hrow(_lab("Reference Cycle", muted=True, size=12), InfoButton(self.theme, "Reference Cycle", REFERENCE_INFO)),
                 self._hrow(self.r_ref, self.r_ref_hint)),
                (_lab("Active Material Mass", muted=True, size=12), self._hrow(self.r_mass, edit)),
                (_lab("Surface Area", muted=True, size=12), self._hrow(self.r_area, edit_area, self.r_area_note)))
        for i, (a, b) in enumerate(rows):
            g.addWidget(a, i, 0, Qt.AlignmentFlag.AlignRight)
            g.addWidget(b, i, 1)
        v.addWidget(box, 0, Qt.AlignmentFlag.AlignHCenter)
        self.r_err = QLabel()
        self.r_err.setProperty("hintErr", True)
        v.addWidget(self.r_err, 0, Qt.AlignmentFlag.AlignHCenter)
        self.analyze_btn = _btn("Analyze", "primary", 36)
        self.analyze_btn.setProperty("run", True)                     # amber: starts a calculation
        self.analyze_btn.clicked.connect(lambda: self.ctl.analyze(self.ds_id))
        v.addWidget(self.analyze_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        return w

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

    def _running(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card = QFrame()
        card.setProperty("panel", True)
        card.setMaximumWidth(640)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(18, 16, 18, 16)
        cl.setSpacing(12)
        top = QHBoxLayout()
        self.run_title = _lab(weight=500, size=14)
        self.run_cancel = _btn("Cancel", "ghost")
        self.run_cancel.clicked.connect(lambda: self.ctl.cancel(self.ds_id))
        top.addWidget(self.run_title)
        top.addStretch(1)
        top.addWidget(self.run_cancel)
        self.run_bar = QProgressBar()
        self.run_bar.setTextVisible(False)
        row = QHBoxLayout()
        self.run_phase, self.run_pct = _lab(muted=True, size=12), _lab(muted=True, size=12)
        row.addWidget(self.run_phase)
        row.addStretch(1)
        row.addWidget(self.run_pct)
        note = _lab("Nothing is saved until the analysis completes. The percentage shows work done, not time "
                    "remaining.", muted=True, size=12)
        note.setWordWrap(True)
        cl.addLayout(top)
        cl.addWidget(self.run_bar)
        cl.addLayout(row)
        cl.addWidget(note)
        v.addWidget(card)
        return w

    def _result(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        h = QHBoxLayout()
        h.setSpacing(14)
        self.combos: dict[str, QComboBox] = {}
        for axis, label in (("x", "X"), ("y1", "Y1"), ("y2", "Y2")):
            l = _lab(label, muted=True, size=12, weight=500)
            l.setFixedWidth(18)
            l.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            c = QComboBox()
            c.setMinimumWidth(150)
            c.setMaximumWidth(220)
            c.setAccessibleName(f"{label} axis")
            c.activated.connect(lambda i, a=axis, cb=c: self._set_axis(a, cb.itemData(i)))
            l.setBuddy(c)
            h.addWidget(l)
            h.addWidget(c)
            self.combos[axis] = c
        h.addStretch(1)
        tools = QFrame()
        tools.setObjectName("Segmented")
        tl = QHBoxLayout(tools)
        tl.setContentsMargins(2, 2, 2, 2)
        tl.setSpacing(2)
        self.t_box, self.t_pan, self.t_reset, self.t_save = (QToolButton() for _ in range(4))
        grp = QButtonGroup(self)
        for b, name, tip in ((self.t_box, "Box Zoom", "Box Zoom · drag in the chart"), (self.t_pan, "Pan", "Pan · or hold Shift and drag"),
                             (self.t_reset, "Reset View", "Reset View · show every visible cycle"), (self.t_save, "Save Image", "Save Image…")):
            b.setObjectName("ChartTool")
            b.setFixedSize(34, 32)
            b.setIconSize(QSize(18, 18))
            b.setToolTip(tip)
            b.setAccessibleName(name)
            if b in (self.t_box, self.t_pan):
                b.setCheckable(True)
                grp.addButton(b)
            tl.addWidget(b)
        self.t_box.setChecked(True)
        self.t_box.toggled.connect(lambda on: setattr(self.chart, "pan_mode", not on))
        self.t_reset.clicked.connect(lambda: self._edit(lambda c: setattr(c.view, "viewport", Viewport())))
        self.t_save.clicked.connect(self._save_image)
        h.addWidget(tools)
        v.addLayout(h)
        self.hidden = _lab(muted=True, size=12, cls=ElidedLabel)
        v.addWidget(self.hidden)
        self.split = DividerSplitter(self.theme, Qt.Orientation.Vertical)   # visible chart/table divider
        self.split.setChildrenCollapsible(False)
        self.chart = ChartWidget()
        self.chart.row_title, self.chart.meta_line = "Cycle", False
        self.chart.marker_size = 7                       # one point per cycle: markers must be easy to see
        self.chart.viewportRequested.connect(lambda vp: self._edit(lambda c: setattr(c.view, "viewport", vp)))
        self.chart.hoverRow.connect(self._hover)
        self.chart.pointClicked.connect(self._select_cycle)
        self.split.addWidget(self.chart)
        panel = QWidget()
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(8)
        bar = QHBoxLayout()
        bar.setSpacing(10)
        bar.addWidget(_lab("Cycles", size=14, weight=500))
        self.t_note = _lab(muted=True, size=12, cls=ElidedLabel)
        self.exp_state = _lab(muted=True, size=12, cls=ElidedLabel)
        self.exp_cancel = _btn("Cancel Export", "ghost")
        self.exp_cancel.clicked.connect(lambda: self.ctl.cancel_export(self.ds_id))
        self.exp_btn = _btn("Export Table…", "ghost")
        self.exp_btn.clicked.connect(self._export)
        self.collapse = QToolButton()
        self.collapse.setObjectName("ChartTool")
        self.collapse.setCheckable(True)
        self.collapse.setFixedSize(30, 30)
        self.collapse.setAccessibleName("Collapse table")
        self.collapse.toggled.connect(self._collapse)
        for x in (self.t_note,):
            bar.addWidget(x)
        bar.addStretch(1)
        for x in (self.exp_state, self.exp_cancel, self.exp_btn, self.collapse):
            bar.addWidget(x)
        pl.addLayout(bar)
        self.model = CapacityModel()
        self.table = FrozenTable(self.model, n_frozen=1)
        self.table.left.setColumnWidth(0, 110)
        self.table.left.setFixedWidth(111)
        self.table.left.selectionModel().currentRowChanged.connect(
            lambda cur, _p: self._select_cycle(self.model.cycle_at(cur.row())) if cur.isValid() else None)
        pl.addWidget(self.table, 1)
        self.panel = panel
        self.split.addWidget(panel)
        self.split.setStretchFactor(0, 45)
        self.split.setStretchFactor(1, 55)
        self.split.setSizes([450, 550])           # default: the cycles table gets slightly more room than the chart
        v.addWidget(self.split, 1)
        self.foot = _lab(muted=True, size=12, cls=ElidedLabel)
        v.addWidget(self.foot)
        self.exp_cancel.hide()
        return w

    def _icons(self) -> None:
        t = self.theme.tokens
        for b, name in ((self.t_box, "square-dashed"), (self.t_pan, "move"), (self.t_reset, "rotate-ccw"), (self.t_save, "download")):
            b.setIcon(make_icon(name, t.muted, checked=t.pri, disabled=t.dis, size=18))
        self.collapse.setIcon(make_icon("chevron-up" if self.collapse.isChecked() else "chevron-down", t.muted, size=16))
        self.model.null_color = t.dis
        self.refresh()

    # ---------------------------------------------------------------- behaviour
    def _edit(self, fn) -> None:
        if self.ds_id:
            self.ctl.edit(self.ds_id, fn)                       # emits changed -> refresh -> redraw

    def show_dataset(self, ds_id: Optional[str]) -> None:
        self.ds_id = ds_id
        self._combo_key = None
        self.refresh()
        self._draw()

    def activate(self) -> None:
        """Refresh the read-only view when this page becomes visible."""
        self.refresh()
        self._draw()

    def _collapse(self, on: bool) -> None:
        self.table.setVisible(not on)
        self.collapse.setToolTip("Expand table" if on else "Collapse table")
        self.collapse.setAccessibleName("Expand table" if on else "Collapse table")
        self._icons()

    def refresh(self) -> None:
        c = self.ctl.cap(self.ds_id) if self.ds_id else None
        if c is None or not hasattr(self, "foot"):
            return
        d = self.ctl.state.get(self.ds_id)
        p = self.ctl.current_params(self.ds_id)
        view = c.state(p)
        label, kind, icon = PILL[view]
        er = d.elab.result if d is not None else None
        corrected = (view is CapView.CURRENT and er is not None and er.correction == "dcir"
                     and c.result is not None and c.result.elab_result_id == er.result_id)
        if corrected:                                   # published by Cycle Correction on the corrected elaboration
            label = "DCIR corrected"
        self.pill.set(kind, icon, label, "Calculated on the elaboration that excludes the DCIR spans (Cycle Correction)"
                      if corrected else "")
        custom = self.ctl.units.is_custom(self.ds_id)
        self.units_pill.setVisible(custom)
        if custom:
            self.units_pill.set("p", "ruler", "Custom units", "This dataset uses its own units (Settings › Units)")
        order = CYCLE_ORDER_LABELS.get(d.elab.result.params.cycle_order, "") if d.elab.result else ""
        if c.result:
            self.meta.setText(f"{d.name} · {fmt_int(c.result.n_cycles)} cycles · Reference cycle {c.result.params.reference_cycle}"
                              f" · Cycle order {order}")
        else:
            self.meta.setText(f"{d.name} · elaboration ready · Capacity not analyzed yet")
        self._fill_banner(view, c)
        # body
        if view in (CapView.NOT_ANALYZED, CapView.ELAB_CHANGED):
            self.body.setCurrentIndex(0)
            self._fill_ready(view, c, d, order)
        elif view in (CapView.ANALYZING,) or (view in (CapView.CANCELLING, CapView.PUBLISHING) and not c.has_result):
            self.body.setCurrentIndex(1)
            self.run_title.setText(f"Analyzing Capacity · {d.name}")
            self._progress(self.run_bar, c.progress)
            self.run_phase.setText(c.phase or "Starting…")
            self.run_pct.setText("" if c.progress is None else f"{c.progress * 100:.0f}%")
            self.run_cancel.setEnabled(view is not CapView.CANCELLING)
            self.run_cancel.setText("Cancelling…" if view is CapView.CANCELLING else "Cancel")
        else:
            self.body.setCurrentIndex(2)
            self._fill_result(c)
            self._draw()

    @staticmethod
    def _progress(bar: QProgressBar, fraction) -> None:
        if fraction is None:
            bar.setRange(0, 0)
        else:
            bar.setRange(0, 1000)
            bar.setValue(int(fraction * 1000))

    def _fill_banner(self, view: CapView, c) -> None:
        spec = {
            CapView.OUTDATED: ("warn", "Based on previous settings", "Reference Cycle or Sample values changed since this "
                               "result was calculated. Reanalyze to apply them. Chart and table show the previous result.", "Reanalyze"),
            CapView.REANALYZING: ("p", "Analyzing Capacity · previous result still shown", "You can keep exploring the previous "
                                  "result. It is replaced only when the new one is ready.", "Cancel"),
            CapView.CANCELLING: ("p", "Cancelling…", "Waiting for the current step to stop. Nothing will be saved.", ""),
            CapView.PUBLISHING: ("p", "Saving result…", "Work is complete; the new result is being saved.", ""),
            CapView.CANCELLED: ("n", "Analysis cancelled", "Nothing was saved. The previous result is still shown, based on "
                                "previous settings.", "Reanalyze"),
            CapView.FAILED: ("err", "Analysis failed", f"{c.message}\nNothing was saved. The previous result is still shown.",
                             "Reanalyze"),
        }.get(view)
        if spec is None or not c.has_result:
            self.banner.hide()
            return
        kind, title, text, btn = spec
        set_prop(self.banner, "banner", kind if kind != "p" else "n")
        col = getattr(self.theme.tokens, {"warn": "warn", "p": "pri", "n": "muted", "err": "err"}[kind])
        self.b_icon.setPixmap(pixmap(BANNER_ICON[kind], col, 18))
        self.b_title.setText(title)
        self.b_text.setText(text)
        running = view in (CapView.REANALYZING, CapView.CANCELLING, CapView.PUBLISHING)
        self.b_prog.setVisible(running)
        if running:
            self._progress(self.b_bar, 1.0 if view is CapView.PUBLISHING else c.progress)
            self.b_phase.setText(c.phase or "Starting…")
        self.b_btn.setVisible(bool(btn))
        self.b_btn.setText(btn)
        self.b_btn.setProperty("action", "cancel" if btn == "Cancel" else "analyze")
        set_prop(self.b_btn, "goElab", btn != "Cancel")
        set_prop(self.b_btn, "ghost", btn == "Cancel")
        set_prop(self.b_btn, "run", btn in ("Analyze", "Reanalyze"))
        if btn == "Reanalyze":
            d = self.ctl.state.get(self.ds_id)
            ok = c.can_analyze(self.ctl.current_params(self.ds_id), d.elab.is_running)
            self.b_btn.setEnabled(ok)
            self.b_btn.setToolTip("" if ok else (c.reference_error or "Not available now"))
        else:
            self.b_btn.setEnabled(True)
        self.banner.show()

    def _banner_action(self) -> None:
        if self.b_btn.property("action") == "cancel":
            self.ctl.cancel(self.ds_id)
        else:
            self.ctl.analyze(self.ds_id)

    def _fill_ready(self, view: CapView, c, d, order: str) -> None:
        changed = view is CapView.ELAB_CHANGED
        self.r_title.setText("Elaboration Changed" if changed else "Ready to Analyze")
        self.r_text.setText("The previous Capacity result was based on an earlier elaboration of this dataset, so it is no "
                            "longer shown. Analyze again to use the current elaboration." if changed else
                            "Capacity, energy, efficiencies, C-rate and normalized values are calculated per cycle by gregorithm "
                            "from the current elaboration.")
        self.r_text.setMinimumHeight(self.r_text.heightForWidth(self.r_text.width()))   # all wrapped lines stay visible
        self.r_order.setText(order or "—")
        if not self.r_ref.hasFocus() and self.r_ref.text() != c.reference_text:
            self.r_ref.setText(c.reference_text)
        set_prop(self.r_ref, "error", bool(c.reference_error))
        hint, warn = reference_hint(c.reference, self.ctl.cycles_of_elaboration(self.ds_id))
        self.r_ref_hint.setText(hint)
        set_prop(self.r_ref_hint, "hintErr", warn)
        s = d.elab.sample
        self.r_mass.setText(f"{s.active_mass_mg:g} mg" if s.active_mass_mg else "Not set")
        self.r_area.setText(f"{s.surface_area_cm2:g} cm²" if s.surface_area_cm2 else "Not set")
        self.r_area_note.setText("" if s.surface_area_cm2 else "areal values will not be available")
        self.r_err.setText(c.reference_error)
        self.r_err.setVisible(bool(c.reference_error))
        ok = c.can_analyze(self.ctl.current_params(self.ds_id), d.elab.is_running)
        self.analyze_btn.setEnabled(ok)
        self.analyze_btn.setToolTip("" if ok else (c.reference_error or "Wait for Data Elaboration to finish"))

    def _fill_result(self, c) -> None:
        u = self.ctl.units.units_for(self.ds_id)
        allowed = self.ctl.selectable(self.ds_id)
        v = c.view
        key = (self.ds_id, u, tuple(allowed), v.x, v.y1, v.y2)
        if key != self._combo_key:
            self._combo_key = key
            for axis, cb in self.combos.items():
                cb.blockSignals(True)
                cb.clear()
                if axis == "y2":
                    cb.addItem("None", None)
                    cb.insertSeparator(1)
                for k in allowed:
                    cb.addItem(cap_label(k, u), k)
                cb.setCurrentIndex(cb.findData(getattr(v, axis)))
                cb.setToolTip(cb.currentText())
                cb.blockSignals(False)
        t = self.ctl.tables.get(self.ds_id)
        note = hidden_note(missing_bases(c.result.params)) if t is not None and c.result is not None else ""
        self.hidden.setText(note)
        self.hidden.setVisible(bool(note))
        self.t_save.setEnabled(t is not None)
        self.exp_btn.setEnabled(t is not None and not self.ctl.is_exporting(self.ds_id))

    def _set_axis(self, axis: str, key) -> None:
        self._edit(lambda c: c.view.set_axis(axis, key, self.ctl.selectable(self.ds_id)))
        self.refresh()

    # ---------------------------------------------------------------- chart + table
    def _draw(self) -> None:
        c = self.ctl.cap(self.ds_id) if self.ds_id else None
        t = self.ctl.tables.get(self.ds_id) if self.ds_id else None
        if c is None or t is None or c.result is None:
            self.chart.set_view(None)
            self.model.set_data({}, [], self.ctl.units.units_for(self.ds_id) if self.ds_id else UnitPrefs(), [])
            self.chart.mark_selected(None)
            self.foot.setText("")
            self.t_note.setText("")
            return
        u = self.ctl.units.units_for(self.ds_id)
        v = c.view
        cyc_all = [int(x) for x in t.values["cycle"]]
        visible = v.visible_cycles(cyc_all)
        idx = [cyc_all.index(cy) for cy in visible]
        breaks = set(gap_breaks(visible, cyc_all))

        def series(key):
            a = np.asarray(t.values[key], float)[idx] if idx else np.array([], float)
            return a

        def with_breaks(a):
            out = []
            for i, val in enumerate(a):
                out.append(val)
                if i in breaks:
                    out.append(np.nan)                               # never join cycles the filter hid
            return np.asarray(out, float)

        rows = with_breaks(np.asarray(visible, float))
        x = with_breaks(series(v.x))
        y1 = with_breaks(series(v.y1))
        y2 = with_breaks(series(v.y2)) if v.y2 else None
        xr = (v.viewport.x.lo, v.viewport.x.hi) if v.viewport.x else fit_range(x)
        y1r = (v.viewport.y1.lo, v.viewport.y1.hi) if v.viewport.y1 else (fit_range(y1, y2) if v.y2_same_scale and y2 is not None else fit_range(y1))
        y2r = None
        if y2 is not None:
            y2r = y1r if v.y2_same_scale else ((v.viewport.y2.lo, v.viewport.y2.hi) if v.viewport.y2 else fit_range(y2))
        mk = lambda a, b: SimpleNamespace(x=a, y=b, source_row=rows)
        view = SimpleNamespace(y1=mk(x, y1), y2=mk(x, y2) if y2 is not None else None, x_range=xr or (0, 1),
                               y1_range=y1r or (0, 1), y2_range=y2r)
        self.chart.set_style(v.x, (cap_label(v.x, u), cap_label(v.y1, u), cap_label(v.y2, u) if v.y2 else None),
                             (v.y1_color, v.y2_color), v.legend, v.style)
        self.chart.link_y2(v.y2_same_scale and v.y2 is not None)
        self.chart.set_view(view)
        empty_y = y1r is None and (y2 is None or y2r is None)
        if empty_y or not visible:
            self.chart.show_message("chart-line", "No values for the selected columns",
                                    "Choose other columns, or change the cycle filter.")
        else:
            self.chart.show_message(None)
        self.model.set_data(t.values, idx, u, t.diagnostics, shown_columns(c.result.params))
        sel = v.selected_cycle
        self.chart.mark_selected(float(np.asarray(t.values[v.x], float)[cyc_all.index(sel)]) if sel in cyc_all and sel in visible else None)
        inc = [cy for cy in visible if any(code == "CAPACITY_MISSING_PHASE" and cc == cy for cc, _col, code in t.diagnostics)]
        parts = [f"{len(visible):,} of {len(cyc_all):,} cycles", "every cycle is drawn"]
        if not v.show_cycle0 and 0 in cyc_all:
            parts.append("Preliminary (cycle 0) hidden")
        if inc:
            parts.append(f"incomplete: cycle{'s' if len(inc) > 1 else ''} {', '.join(map(str, inc[:3]))}{'…' if len(inc) > 3 else ''}")
        self.foot.setText(" · ".join(parts))
        self.t_note.setText(f"{len(visible):,} rows · cycle filter: {filter_text(v.applied)}")

    def _hover(self, cycle: int) -> None:
        c, t = self.ctl.cap(self.ds_id), self.ctl.tables.get(self.ds_id)
        if c is None or t is None:
            return
        cyc_all = [int(x) for x in t.values["cycle"]]
        if cycle not in cyc_all:
            return
        i = cyc_all.index(cycle)
        vals = {k: (None if not math.isfinite(float(a[i])) else float(a[i])) for k, a in t.values.items()}
        self.chart.show_details(cycle, vals, (c.view.x, c.view.y1, c.view.y2))

    def _select_cycle(self, cycle: int) -> None:
        c = self.ctl.cap(self.ds_id)
        if c is None or c.view.selected_cycle == cycle:
            return
        c.view.selected_cycle = cycle
        r = self.model.row_of_cycle(cycle)
        if r >= 0:
            self.table.show_row(r)
        self._draw()

    # ---------------------------------------------------------------- export / image
    def _export(self) -> None:
        c, t = self.ctl.cap(self.ds_id), self.ctl.tables.get(self.ds_id)
        if c is None or t is None:
            return
        u = self.ctl.units.units_for(self.ds_id)
        cyc_all = [int(x) for x in t.values["cycle"]]
        rows = c.view.export_cycles(cyc_all)
        rows_text = (f"Rows: all {len(cyc_all):,} cycles (cycle 0 included)." if rows is None else
                     f"Rows: {len(rows):,} cycles of the applied filter ({filter_text(c.view.applied)}).")
        dlg = ExportDialog(self, u, rows_text, shown_columns(c.result.params if c.result else None))
        if not dlg.exec():
            return
        fmt, cols, diag = dlg.choice()
        d = self.ctl.state.get(self.ds_id)
        ext = ".csv" if fmt == "csv" else ".parquet"
        path = save_export(self, "Export Capacity table", f"{d.name} - capacity{ext}",
                           "CSV (*.csv)" if fmt == "csv" else "Parquet (*.parquet)", folder=dataset_folder(self.ctl.state.get(self.ds_id)))
        if path:
            if not path.lower().endswith(ext):
                path += ext
            self.ctl.export(self.ds_id, fmt, cols, diag, path)

    def _export_state(self, ds_id: str, msg: str) -> None:
        if ds_id != self.ds_id:
            return
        self.exp_state.setText(msg)
        self.exp_cancel.setVisible(bool(msg))
        self.exp_btn.setEnabled(not msg)

    def _save_image(self) -> None:
        """Save Image: the chart exactly as shown (every cycle is drawn), fixed palette, PNG."""
        c = self.ctl.cap(self.ds_id) if self.ds_id else None
        if c is None or self.ctl.tables.get(self.ds_id) is None:
            return
        d = self.ctl.state.get(self.ds_id)
        u = self.ctl.units.units_for(self.ds_id)
        name = f"{d.name} - {cap_label(c.view.y1, u).split(' [')[0]} vs {cap_label(c.view.x, u).split(' [')[0]}.png"
        path = save_export(self, "Save Image", name, "PNG image (*.png)", folder=dataset_folder(self.ctl.state.get(self.ds_id)))
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        if self.chart.grab().save(path, "PNG"):
            self.window().statusBar().showMessage(f"Image saved: {path}", 6000)
        else:
            QMessageBox.warning(self, "Save Image", "Could not save the image.")
