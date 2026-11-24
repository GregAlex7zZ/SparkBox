"""DCIR page: header, stepper Detection -> Review | Results -> Correction (step 3: widgets/correction_view.py),
the Detection card (7 parameters, all in seconds), the Review view (pulse list 240 px + inspection chart + full-width
measurement strip with the V1 / I1 times of the selected pulse and their Apply / Apply to All buttons, Accept /
Reject and, below them, Apply & Calculate, enabled only once no pulse is Pending) and the Results view (resistance per cycle, one colour, points only + measurements table).
A successful Apply & Calculate opens Results.
Widgets never decide: they call DcirController and re-read DcirState."""
from __future__ import annotations
import math
from types import SimpleNamespace
from typing import Optional
import numpy as np
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSize, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame,
                               QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QProgressBar, QPushButton, QRadioButton, QStackedWidget,
                               QTableView, QToolButton, QVBoxLayout, QWidget)
from ..file_dialogs import dataset_folder, save_export
from ..dcir import (ACCEPTED, AUTO, BOTH, CHARGE, DET_FIELDS, DISCHARGE, MANUAL, PENDING, POLARITY_LABELS,
                    POLARITY_POPOVER, REJECTED, RESULT_COLUMNS, Step, describe_params, explain, result_heads,
                    result_points, result_rows)
from ..dcir_controller import DcirController
from ..elaboration import Outcome, Run
from ..explorer import CHART, Viewport
from ..icons import make_icon, pixmap
from ..theme import ThemeManager, set_prop
from .chart import ChartWidget
from .controls import DividerSplitter, ElidedLabel, FlowLayout, InfoButton, SegmentedControl, StatusPill, group
from .correction_view import CorrectionView
from .dcir_manual import ManualDialog
from .pulse_chart import PulseChart
from .wrap_header import install as install_wrap_header

STATE_ICON = {PENDING: "circle", ACCEPTED: "circle-check", REJECTED: "circle-x"}
STATE_TEXT = {PENDING: "Pending", ACCEPTED: "Accepted", REJECTED: "Rejected"}
MT_INFO = ("V1: time after V0 (the last recorded sample before the pulse) at which V1 is read (default: 30 s). "
           "With 1 s logging, 30 s on a 30 s pulse is its last sample.",
           "I1: time after V0 at which I1 is read. Leave it empty for the gregorithm default, the 3rd pulse sample.",
           "Apply: the times are used for the selected pulse only. Its measurement changes, so the pulse returns to "
           "Pending and must be accepted again. Apply to All: the times are used for every pulse (pulse-specific "
           "times are removed) and the accepted pulses are measured again.",
           "The nearest recorded sample is used, never an interpolated value. Manual measurements are not affected. "
           "Values shown here are a preview: they are saved with Apply & Calculate.")


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


def num(v, unit: str = "", digits: int = 4) -> str:
    """Display only (no arithmetic): None -> em dash; minus sign; thousands separator."""
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "—"
    s = format(float(v), f",.{digits}g").replace("-", "−")
    return f"{s} {unit}".strip()


class ResultsModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.rows: list[dict] = []
        self.heads: list[str] = []
        self.tips: list[dict] = []
        self.null_color = "#8798A2"

    def set_rows(self, rows: list[dict], heads: list[str], tips: list[dict]) -> None:
        self.beginResetModel()
        self.rows, self.heads, self.tips = rows, heads, tips
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(RESULT_COLUMNS)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        key = RESULT_COLUMNS[index.column()][0]
        v = self.rows[index.row()].get(key)
        if role == Qt.ItemDataRole.DisplayRole:
            if isinstance(v, float):
                return num(v, digits=6)
            return "—" if v in (None, "") else str(v)
        if role == Qt.ItemDataRole.ToolTipRole:
            return self.tips[index.row()].get(key)
        if role == Qt.ItemDataRole.ForegroundRole and v in (None, ""):
            from PySide6.QtGui import QColor
            return QColor(self.null_color)
        if role == Qt.ItemDataRole.TextAlignmentRole and isinstance(v, (int, float)) and not isinstance(v, bool):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.heads[section] if section < len(self.heads) else None
        return None


class DcirExportDialog(QDialog):
    """Export Table… : CSV or Parquet, chosen columns, one row per published Accepted measurement, display units.
    No JSON (Author). The file name is chosen after Export; never overwrites."""

    def __init__(self, parent, heads: list[str], n_rows: int):
        super().__init__(parent)
        self.setWindowTitle("Export DCIR Measurements")
        self.setMinimumWidth(460)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)
        v.addWidget(_lab("Export DCIR measurements", size=18, weight=500))
        v.addWidget(_lab(f"Rows: {n_rows:,} calculated Accepted measurements.", muted=True))
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
        for i, ((key, _t, _q), head) in enumerate(zip(RESULT_COLUMNS, heads)):
            b = QCheckBox(head)
            b.setChecked(True)
            if key == "label":
                b.setEnabled(False)
            self.boxes[key] = b
            grid.addWidget(b, i // 2, i % 2)
        v.addLayout(grid)
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

    def choice(self) -> tuple[str, list[str]]:
        return ("parquet" if self.parquet.isChecked() else "csv"), [k for k, b in self.boxes.items() if b.isChecked()]


class DcirPage(QWidget):
    openSettingsRequested = Signal()
    openCapacityRequested = Signal()

    def __init__(self, theme: ThemeManager, ctl: DcirController, corr=None):
        super().__init__()
        self.theme, self.ctl, self.corr = theme, ctl, corr
        self.ds_id: Optional[str] = None
        self._list_key = None
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 12)
        root.setSpacing(10)
        root.addLayout(self._header())
        self.banner = self._banner()
        root.addWidget(self.banner)
        root.addWidget(self._controls())
        self.body = QStackedWidget()
        self.body.addWidget(self._detection())     # 0
        self.body.addWidget(self._review())        # 1
        self.body.addWidget(self._results())       # 2
        self.body.addWidget(self._running())       # 3
        self.corr_view = CorrectionView(theme, corr) if corr is not None else QWidget()
        self.body.addWidget(self.corr_view)        # 4
        if corr is not None:
            self.corr_view.setSpanRequested.connect(self._set_span)
            self.corr_view.openCapacityRequested.connect(self.openCapacityRequested)
            corr.changed.connect(lambda ds: self.refresh() if ds == self.ds_id else None)
        root.addWidget(self.body, 1)
        ctl.changed.connect(lambda ds: (self.refresh(), self._draw_trace()) if ds == self.ds_id else None)
        ctl.traceReady.connect(lambda ds: self._draw_trace() if ds == self.ds_id else None)
        ctl.exportState.connect(self._export_state)
        ctl.manualError.connect(lambda ds, m: QMessageBox.warning(self, "DCIR", m) if ds == self.ds_id else None)
        theme.changed.connect(self._icons)
        for key, fn in (("A", lambda: self._key_decide(ACCEPTED)), ("R", lambda: self._key_decide(REJECTED)),
                        ("Up", lambda: self._key_move(-1)), ("Down", lambda: self._key_move(1))):
            sc = QShortcut(QKeySequence(key), self.review, activated=fn)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._icons()

    # ================================================================ construction
    def _header(self) -> QHBoxLayout:
        h = QHBoxLayout()
        h.setSpacing(12)
        h.addWidget(_lab("DCIR", size=16, weight=500))
        self.meta = _lab(muted=True, size=12, cls=ElidedLabel)
        self.units_pill, self.unpub_pill, self.corr_pill = StatusPill(self.theme), StatusPill(self.theme), StatusPill(self.theme)
        h.addWidget(self.meta)
        h.addWidget(self.units_pill)
        h.addStretch(1)
        h.addWidget(self.corr_pill)
        h.addWidget(self.unpub_pill)
        return h

    def _banner(self) -> QFrame:
        f = QFrame()
        bl = QHBoxLayout(f)
        bl.setContentsMargins(14, 10, 14, 10)
        bl.setSpacing(12)
        self.b_icon = QLabel()
        col = QVBoxLayout()
        col.setSpacing(4)
        self.b_title, self.b_text = _lab(weight=500), _lab(muted=True, size=12)
        self.b_text.setWordWrap(True)
        self.b_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.b_bar = QProgressBar()
        self.b_bar.setTextVisible(False)
        for w in (self.b_title, self.b_text, self.b_bar):
            col.addWidget(w)
        self.b_btn = _btn("Cancel", "ghost")
        self.b_btn.clicked.connect(lambda: self.ctl.cancel(self.ds_id))
        bl.addWidget(self.b_icon, 0, Qt.AlignmentFlag.AlignTop)
        bl.addLayout(col, 1)
        bl.addWidget(self.b_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        f.hide()
        return f

    def _controls(self) -> QWidget:
        """Stepper, filters and pulse navigation. The row wraps onto a second line when the window is narrow, so no
        control is ever squeezed until its text is cut."""
        box = QWidget()
        flow = FlowLayout(box, h_spacing=16, v_spacing=6)
        self.st_det = QPushButton()
        self.st_det.setObjectName("Step")
        self.st_det.setCursor(Qt.CursorShape.PointingHandCursor)
        self.st_det.clicked.connect(lambda: self._go(Step.DETECTION))
        self.st_seg = SegmentedControl([("review", "Review"), ("results", "Results")], small=True, aria="Review or results")
        self.st_seg.setProperty("stepper", True)             # step buttons styled as clickable pills (theme #Step)
        for b in self.st_seg._btn.values():
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.st_seg.valueChanged.connect(lambda v: self._go(Step.REVIEW if v == "review" else Step.RESULTS))
        self.st_corr = QPushButton("3  Correction")
        self.st_corr.setObjectName("Step")
        self.st_corr.setCursor(Qt.CursorShape.PointingHandCursor)
        self.st_corr.clicked.connect(lambda: self._go(Step.CORRECTION))
        self.n2 = QLabel("2")
        self.n2.setObjectName("StepNum")
        arrow = lambda: _lab("›", muted=True, size=18)
        flow.addWidget(group(self.st_det, arrow(), self.n2, self.st_seg, arrow(), self.st_corr, spacing=8))
        self.filters: dict[str, QComboBox] = {}
        self.nav_groups: list[QWidget] = []
        for attr, label, opts in (("f_state", "State", (("all", "All"), (PENDING, "Pending"), (ACCEPTED, "Accepted"), (REJECTED, "Rejected"))),
                                  ("f_polarity", "Polarity", ((BOTH, "Both"), (CHARGE, "Charge"), (DISCHARGE, "Discharge"))),
                                  ("f_source", "Source", (("all", "All"), (AUTO, "Detected"), (MANUAL, "Manual")))):
            l = _lab(label, muted=True, size=12)
            cb = QComboBox()
            cb.setAccessibleName(f"{label} filter")
            for v, t in opts:
                cb.addItem(t, v)
            cb.activated.connect(lambda i, a=attr, c=cb: self.ctl.set_filter(self.ds_id, a, c.itemData(i)))
            l.setBuddy(cb)
            g = group(l, cb, spacing=8)
            flow.addWidget(g)
            self.nav_groups.append(g)
            self.filters[attr] = cb
        self.pos_lab = _lab(muted=True, size=12)
        self.prev_b, self.next_b = QToolButton(), QToolButton()
        for b, name, d in ((self.prev_b, "Previous pulse (↑)", -1), (self.next_b, "Next pulse (↓)", 1)):
            b.setObjectName("ChartTool")
            b.setFixedSize(30, 30)
            b.setToolTip(name)
            b.setAccessibleName(name)
            b.clicked.connect(lambda _c=False, dd=d: self.ctl.move(self.ds_id, dd))
        g = group(self.pos_lab, self.prev_b, self.next_b, spacing=4)
        flow.addWidget(g)
        self.nav_groups.append(g)
        return box

    # ---------------------------------------------------------------- Detection card
    def _detection(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        v.setSpacing(12)
        self.warn = QFrame()
        self.warn.setProperty("banner", "warn")
        wl = QHBoxLayout(self.warn)
        wl.setContentsMargins(14, 10, 14, 10)
        self.warn_icon = QLabel()
        wc = QVBoxLayout()
        wc.setSpacing(2)
        self.warn_title = _lab("Detecting again keeps only Accepted pulses", weight=500)
        self.warn_text = _lab(muted=True, size=12)
        self.warn_text.setWordWrap(True)
        wc.addWidget(self.warn_title)
        wc.addWidget(self.warn_text)
        wl.addWidget(self.warn_icon, 0, Qt.AlignmentFlag.AlignTop)
        wl.addLayout(wc, 1)
        self.warn.setMaximumWidth(720)
        v.addWidget(self.warn, 0, Qt.AlignmentFlag.AlignHCenter)
        card = QFrame()
        card.setProperty("panel", True)
        card.setMaximumWidth(720)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(24, 20, 24, 18)
        cl.setSpacing(14)
        cl.addWidget(_lab("Detect Pulses", size=20, weight=500), 0, Qt.AlignmentFlag.AlignHCenter)
        d = _lab("Pulses are found among the constant-current segments of the current elaboration, with a rest before "
                 "and after. Nothing is measured or changed until you review and apply them.", muted=True, size=13)
        d.setWordWrap(True)
        d.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        cl.addWidget(d)
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(10)
        self.fields: dict[str, QLineEdit] = {}
        self.errs: dict[str, QLabel] = {}
        pairs = (("Pulse Duration", "pulse_s", "pulse_tolerance_pct"), (None, None, None),
                 ("Rest Before", "rest_before_s", "rest_before_tolerance_pct"),
                 ("Rest After", "rest_after_s", "rest_after_tolerance_pct"))
        by = {f.key: f for f in DET_FIELDS}
        for r, (title, k1, k2) in enumerate(pairs):
            if title is None:                                 # polarity row
                g.addWidget(self._lrow("Pulse Polarity", POLARITY_POPOVER), r, 0, Qt.AlignmentFlag.AlignRight)
                self.pol = SegmentedControl([(BOTH, "Both"), (CHARGE, "Charge"), (DISCHARGE, "Discharge")], small=True,
                                            aria="Pulse Polarity")
                self.pol.valueChanged.connect(lambda val: self.ctl.edit(self.ds_id, lambda c: c.set_polarity(val)))
                g.addWidget(self.pol, r, 1, 1, 5, Qt.AlignmentFlag.AlignLeft)
                continue
            g.addWidget(self._lrow(title, by[k1].popover + by[k2].popover), r, 0, Qt.AlignmentFlag.AlignRight)
            for c, key, unit in ((1, k1, "s"), (4, k2, "%")):
                e = QLineEdit()
                e.setFixedWidth(80)
                e.setAlignment(Qt.AlignmentFlag.AlignRight)
                e.setAccessibleName(by[key].label)
                e.textEdited.connect(lambda t, kk=key: self.ctl.edit(self.ds_id, lambda cc: cc.set_param_text(kk, t)))
                e.returnPressed.connect(self._detect_clicked)
                self.fields[key] = e
                g.addWidget(e, r, c)
                g.addWidget(_lab(unit, muted=True, size=12), r, c + 1)
            g.addWidget(_lab("±", muted=True), r, 3, Qt.AlignmentFlag.AlignRight)
            err = QLabel()
            err.setProperty("hintErr", True)
            self.errs[k1] = err
            g.addWidget(err, r, 6)
        g.setColumnStretch(6, 1)
        cl.addLayout(g)
        row = QHBoxLayout()
        row.addStretch(1)
        self.reset_btn = _btn("Reset to Defaults", "ghost")
        self.reset_btn.clicked.connect(lambda: self.ctl.edit(self.ds_id, lambda c: c.reset_params()))
        self.detect_btn = _btn("Detect", "primary", 36)
        self.detect_btn.setProperty("run", True)                     # amber: starts a calculation
        self.detect_btn.clicked.connect(self._detect_clicked)
        row.addWidget(self.reset_btn)
        row.addWidget(self.detect_btn)
        row.addStretch(1)
        cl.addLayout(row)
        self.last_run = _lab(muted=True, size=12)
        self.last_run.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.last_run.setWordWrap(True)
        cl.addWidget(self.last_run)
        v.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
        return w

    def _lrow(self, title: str, paragraphs) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        h.addWidget(_lab(title, size=13))
        h.addWidget(InfoButton(self.theme, title, tuple(paragraphs)))
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
        self.run_phase = _lab(muted=True, size=12)
        note = _lab("Nothing is saved until detection completes.", muted=True, size=12)
        for x in (self.run_bar, self.run_phase, note):
            pass
        cl.addLayout(top)
        cl.addWidget(self.run_bar)
        cl.addWidget(self.run_phase)
        cl.addWidget(note)
        v.addWidget(card)
        return w

    # ---------------------------------------------------------------- Review
    def _review(self) -> QWidget:
        self.review = w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
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
        head.addWidget(_lab("Pulse", muted=True, size=11))
        head.addStretch(1)
        self.r_head = _lab("R", muted=True, size=11)
        head.addWidget(self.r_head)
        ll.addLayout(head)
        self.list = QListWidget()
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setAccessibleName("Pulses")
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.currentItemChanged.connect(self._list_changed)
        ll.addWidget(self.list, 1)
        self.add_manual = _btn("+  Add Manual Measurement…", "ghost")
        self.add_manual.clicked.connect(self._manual)
        ll.addWidget(self.add_manual)
        top.addWidget(left)
        right = QVBoxLayout()
        right.setSpacing(6)
        bar = QHBoxLayout()
        self.ev_title = _lab(weight=500, size=14, cls=ElidedLabel)   # "…" + tooltip, never a hard cut
        self.ev_sub = _lab(muted=True, size=12, cls=ElidedLabel)
        bar.addWidget(self.ev_title)
        bar.addWidget(self.ev_sub)
        bar.addStretch(1)
        tools = QFrame()
        tools.setObjectName("Segmented")
        tl = QHBoxLayout(tools)
        tl.setContentsMargins(2, 2, 2, 2)
        tl.setSpacing(2)
        self.t_box, self.t_pan, self.t_reset, self.t_save = (QToolButton() for _ in range(4))
        grp = QButtonGroup(self)
        for b, name, tip in ((self.t_box, "Box Zoom", "Box Zoom · drag in the chart"), (self.t_pan, "Pan", "Pan · or hold Shift and drag"),
                             (self.t_reset, "Reset View", "Reset View · whole pulse"), (self.t_save, "Save Image", "Save Image…")):
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
        self.t_box.toggled.connect(lambda on: setattr(self.pchart, "pan_mode", not on))
        self.t_reset.clicked.connect(lambda: self.pchart.reset_view())
        self.t_save.clicked.connect(self._save_image)
        bar.addWidget(tools)
        right.addLayout(bar)
        self.pchart = PulseChart()
        right.addWidget(self.pchart, 1)
        self.legend = legend = _lab("", muted=True, size=11)
        legend.setWordWrap(True)
        right.addWidget(legend)
        top.addLayout(right, 1)
        v.addLayout(top, 1)
        # full-width measurement strip
        strip = QFrame()
        strip.setProperty("panel", True)
        sl = QHBoxLayout(strip)
        sl.setContentsMargins(14, 10, 14, 10)
        sl.setSpacing(16)
        mt = QVBoxLayout()
        mt.setSpacing(4)
        ml = QHBoxLayout()
        ml.setSpacing(4)
        self.mt_scope = _lab("Times after V0 · all pulses", muted=True, size=11)
        ml.addWidget(self.mt_scope)
        ml.addWidget(InfoButton(self.theme, "Measurement times", MT_INFO))
        mt.addLayout(ml)
        tg = QGridLayout()
        tg.setHorizontalSpacing(6)
        tg.setVerticalSpacing(4)
        self.mt = QLineEdit()
        self.mt.setAccessibleName("V1 measurement time in s")
        self.mt.textEdited.connect(lambda t: self.ctl.set_draft_text(self.ds_id, "v1", t))
        self.mt.returnPressed.connect(lambda: self.ctl.apply_times_to_pulse(self.ds_id))
        self.ct = QLineEdit()
        self.ct.setAccessibleName("I1 measurement time in s, empty for the 3rd pulse sample")
        self.ct.setPlaceholderText("3rd smp.")
        self.ct.setToolTip("Time after V0 at which I1 is read. Empty: the 3rd pulse sample (gregorithm default).")
        self.ct.textEdited.connect(lambda t: self.ctl.set_draft_text(self.ds_id, "i1", t))
        self.ct.returnPressed.connect(lambda: self.ctl.apply_times_to_pulse(self.ds_id))
        for r, (name, edit) in enumerate((("V1 at", self.mt), ("I1 at", self.ct))):
            edit.setFixedWidth(84)
            edit.setAlignment(Qt.AlignmentFlag.AlignRight)
            tg.addWidget(_lab(name, muted=True, size=12), r, 0)
            tg.addWidget(edit, r, 1)
            tg.addWidget(_lab("s", muted=True, size=12), r, 2)
        tg.setColumnStretch(3, 1)
        mt.addLayout(tg)
        self.mt_err = QLabel()
        self.mt_err.setProperty("hintErr", True)
        mt.addWidget(self.mt_err)
        apply_row = QHBoxLayout()                    # Apply (selected pulse) · Apply to All (every pulse)
        apply_row.setSpacing(6)
        self.apply_one = _btn("Apply", "ghost", 28)
        self.apply_one.clicked.connect(lambda: self.ctl.apply_times_to_pulse(self.ds_id))
        self.apply_all = _btn("Apply to All", "ghost", 28)
        self.apply_all.clicked.connect(lambda: self.ctl.apply_times_to_all(self.ds_id))
        apply_row.addWidget(self.apply_one, 1)
        apply_row.addWidget(self.apply_all, 1)
        mt.addLayout(apply_row)
        mtw = QWidget()
        mtw.setLayout(mt)
        mtw.setFixedWidth(200)
        sl.addWidget(mtw)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sl.addWidget(sep)
        self.cells = QGridLayout()
        self.cells.setHorizontalSpacing(22)
        self.cells.setVerticalSpacing(2)
        self.cell_w: list[tuple[QLabel, QLabel]] = []
        for i in range(10):
            k, val = _lab(muted=True, size=11, cls=ElidedLabel), _lab(size=13)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.cells.addWidget(k, (i // 5) * 2, i % 5)
            self.cells.addWidget(val, (i // 5) * 2 + 1, i % 5)
            self.cell_w.append((k, val))
        cw = QWidget()
        cw.setLayout(self.cells)
        sl.addWidget(cw, 1)
        act = QVBoxLayout()
        act.setSpacing(6)
        ar = QHBoxLayout()
        self.reject_btn, self.accept_btn = _btn("Reject", "ghost", 34), _btn("Accept", "goElab", 34)
        self.reject_btn.setToolTip("Reject (R)")
        self.accept_btn.setToolTip("Accept (A)")
        self.reject_btn.clicked.connect(lambda: self.ctl.decide(self.ds_id, REJECTED))
        self.accept_btn.clicked.connect(lambda: self.ctl.decide(self.ds_id, ACCEPTED))
        ar.addWidget(self.reject_btn, 1)
        ar.addWidget(self.accept_btn, 1)
        act.addLayout(ar)
        self.act_hint = _lab("A accept · R reject · ↑↓ move", muted=True, size=11)
        self.act_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.act_hint.setWordWrap(True)
        act.addWidget(self.act_hint)
        self.publish_btn = _btn("Apply && Calculate", "goElab", 36)
        self.publish_btn.setProperty("run", True)                     # amber: starts a calculation
        self.publish_btn.clicked.connect(lambda: self.ctl.publish(self.ds_id))
        act.addWidget(self.publish_btn)
        self.publish_hint = _lab("", muted=True, size=11)
        self.publish_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.publish_hint.setWordWrap(True)
        act.addWidget(self.publish_hint)
        aw = QWidget()
        aw.setLayout(act)
        aw.setFixedWidth(230)
        sl.addWidget(aw)
        v.addWidget(strip)
        self.empty_review = _lab("", muted=True)
        return w

    # ---------------------------------------------------------------- Results
    def _results(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        split = DividerSplitter(self.theme, Qt.Orientation.Vertical)        # visible chart/table divider
        split.setChildrenCollapsible(False)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(6)
        bar = QHBoxLayout()
        bar.addWidget(_lab("Resistance per Cycle", size=14, weight=500))
        self.res_note = _lab(muted=True, size=12, cls=ElidedLabel)
        bar.addWidget(self.res_note)
        bar.addStretch(1)
        self.res_reset, self.res_save = QToolButton(), QToolButton()
        for b, name in ((self.res_reset, "Reset View"), (self.res_save, "Save Image…")):
            b.setObjectName("ChartTool")
            b.setFixedSize(34, 32)
            b.setIconSize(QSize(18, 18))
            b.setToolTip(name)
            b.setAccessibleName(name.split("…")[0])
            bar.addWidget(b)
        self.res_reset.clicked.connect(lambda: (setattr(self, "_res_vp", Viewport()), self._draw_results()))
        self.res_save.clicked.connect(self._save_image)
        tl.addLayout(bar)
        self.rchart = ChartWidget()
        self.rchart.row_title, self.rchart.meta_line = "Measurement", False
        self.rchart.viewportRequested.connect(lambda vp: (setattr(self, "_res_vp", vp), self._draw_results()))
        self.rchart.pointClicked.connect(self._result_clicked)
        self.rchart.hoverRow.connect(self._result_hover)
        tl.addWidget(self.rchart, 1)
        split.addWidget(top)
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(6)
        b2 = QHBoxLayout()
        b2.addWidget(_lab("Measurements", size=14, weight=500))
        self.pub_note = _lab(muted=True, size=12, cls=ElidedLabel)
        b2.addWidget(self.pub_note)
        b2.addStretch(1)
        self.exp_state = _lab(muted=True, size=12, cls=ElidedLabel)
        self.exp_cancel = _btn("Cancel Export", "ghost")
        self.exp_cancel.clicked.connect(lambda: self.ctl.cancel_export(self.ds_id))
        self.exp_cancel.hide()
        self.exp_btn = _btn("Export Table…", "ghost")
        self.exp_btn.clicked.connect(self._export)
        for x in (self.exp_state, self.exp_cancel, self.exp_btn):
            b2.addWidget(x)
        bl.addLayout(b2)
        self.rmodel = ResultsModel()
        self.rtable = QTableView()
        self.rtable.setModel(self.rmodel)
        self.rtable.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.rtable.verticalHeader().hide()
        install_wrap_header(self.rtable)                     # titles wrap: columns as wide as their values
        self.rtable.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.rtable.horizontalHeader().setStretchLastSection(True)
        self.rtable.setAlternatingRowColors(True)
        bl.addWidget(self.rtable, 1)
        split.addWidget(bottom)
        split.setStretchFactor(0, 50)
        split.setStretchFactor(1, 50)
        split.setSizes([500, 500])
        v.addWidget(split, 1)
        v.addWidget(self._next_step())
        self._res_vp = Viewport()
        self._res_pts: list = []
        return w

    def _next_step(self) -> QFrame:
        """Strip under Results that makes the optional next step explicit: Cycle Correction (step 3) removes the spans
        of the published Accepted pulses from the cycles. Hidden when the page has no Correction controller."""
        self.next_strip = QFrame()
        self.next_strip.setProperty("banner", "next")              # amber accent: a calculation step follows
        h = QHBoxLayout(self.next_strip)
        h.setContentsMargins(14, 8, 10, 8)
        h.setSpacing(12)
        txt = QVBoxLayout()
        txt.setSpacing(0)
        self.next_title = _lab(size=13, weight=500)                 # texts: _fill_next_step()
        self.next_text = _lab(muted=True, size=12)
        self.next_text.setWordWrap(True)
        txt.addWidget(self.next_title)
        txt.addWidget(self.next_text)
        h.addLayout(txt, 1)
        self.next_btn = _btn("", "goElab", h=32)
        self.next_btn.clicked.connect(lambda: self._go(Step.CORRECTION))
        h.addWidget(self.next_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.next_strip.setVisible(self.corr is not None)
        self._fill_next_step(False)
        return self.next_strip

    def _fill_next_step(self, corrected: bool) -> None:
        if self.corr is None:
            return
        if corrected:
            self.next_title.setText("Cycles already corrected · Cycle Correction")
            self.next_text.setText("The elaboration excludes the DCIR spans. Open Correction to review or remove it.")
            self.next_btn.setText("Open Correction  ›")
        else:
            self.next_title.setText("Next step (optional) · Cycle Correction")
            self.next_text.setText("Exclude the spans of these Accepted pulses from the cycles: creates a corrected "
                                   "elaboration with cycle labels and Capacity recalculated. Recorded points are kept.")
            self.next_btn.setText("Go to Correction  ›")
        if hasattr(self, "st_corr"):
            self.next_btn.setEnabled(self.st_corr.isEnabled())

    def _icons(self) -> None:
        t = self.theme.tokens
        for b, name in ((self.t_box, "square-dashed"), (self.t_pan, "move"), (self.t_reset, "rotate-ccw"),
                        (self.t_save, "download"), (self.res_reset, "rotate-ccw"), (self.res_save, "download")):
            b.setIcon(make_icon(name, t.muted, checked=t.pri, disabled=t.dis, size=18))
        self.prev_b.setIcon(make_icon("chevron-up", t.muted, disabled=t.dis, size=16))
        self.next_b.setIcon(make_icon("chevron-down", t.muted, disabled=t.dis, size=16))
        self.warn_icon.setPixmap(pixmap("triangle-alert", t.warn, 18))
        self.rmodel.null_color = t.dis
        self._list_key = None
        self.refresh()

    # ================================================================ behaviour
    def show_dataset(self, ds_id: Optional[str]) -> None:
        self.ds_id = ds_id
        self._list_key = None
        self._res_vp = Viewport()
        if self.corr is not None:
            self.corr_view.show_dataset(ds_id)
        self.refresh()
        self._draw_trace()

    def _go(self, step: Step) -> None:
        c = self.ctl.dc(self.ds_id)
        if c is None:
            return
        if step is not Step.DETECTION and c.detection is None and c.published is None:
            return
        if step is Step.CORRECTION and self.corr is None:
            return
        if step is Step.RESULTS and c.published is None:
            return
        c.step = step
        self.refresh()

    def _detect_clicked(self) -> None:
        c = self.ctl.dc(self.ds_id)
        if c is None or c.is_running:
            return
        if c.detection is not None:
            pend, rej, unpub = c.redetect_info()
            if pend or rej or unpub:
                box = QMessageBox(self)
                box.setWindowTitle("Detect Again")
                box.setIcon(QMessageBox.Icon.Warning)
                box.setText("Detecting again keeps only Accepted pulses.")
                parts = []
                if pend or rej:
                    parts.append(f"{pend:,} Pending and {rej:,} Rejected decisions will not be kept.")
                if unpub:
                    parts.append("Changes not applied are lost unless you apply them first.")
                box.setInformativeText(" ".join(parts))
                pub = box.addButton("Apply First", QMessageBox.ButtonRole.AcceptRole) if unpub and not c.publish_blocker() else None
                anyway = box.addButton("Detect Anyway", QMessageBox.ButtonRole.DestructiveRole)
                box.addButton(QMessageBox.StandardButton.Cancel)
                box.exec()
                if pub is not None and box.clickedButton() is pub:
                    self.ctl.publish(self.ds_id)
                    return
                if box.clickedButton() is not anyway:
                    return
        self.ctl.detect(self.ds_id)

    def _key_decide(self, state: str) -> None:
        c = self.ctl.dc(self.ds_id)
        if c is not None and c.step is Step.REVIEW and not self.mt.hasFocus() and not self.ct.hasFocus():
            self.ctl.decide(self.ds_id, state)

    def _key_move(self, d: int) -> None:
        c = self.ctl.dc(self.ds_id)
        if c is not None and c.step is Step.REVIEW:
            self.ctl.move(self.ds_id, d)

    def _list_changed(self, cur, _prev) -> None:
        if cur is not None and self.ds_id:
            self.ctl.select(self.ds_id, cur.data(Qt.ItemDataRole.UserRole))

    # ---------------------------------------------------------------- refresh
    def refresh(self) -> None:
        c = self.ctl.dc(self.ds_id) if self.ds_id else None
        if c is None or not hasattr(self, "rtable"):
            return
        d = self.ctl.state.get(self.ds_id)
        u = self.ctl.units.units_for(self.ds_id)
        cnt = c.counts()
        if c.detection is not None or c.published is not None:
            self.meta.setText(f"{d.name} · {cnt['total']:,} pulses · {cnt[ACCEPTED]:,} accepted · {cnt[REJECTED]:,} rejected"
                              f" · {cnt[PENDING]:,} pending" + (f" · {cnt[MANUAL]:,} manual" if cnt[MANUAL] else ""))
        else:
            self.meta.setText(f"{d.name} · elaboration ready · pulses not detected yet")
        custom = self.ctl.units.is_custom(self.ds_id)
        self.units_pill.setVisible(custom)
        if custom:
            self.units_pill.set("p", "ruler", "Custom units", "This dataset uses its own units (Settings › Units)")
        self.unpub_pill.setVisible(c.unpublished)
        if c.unpublished:
            self.unpub_pill.set("warn", "info", "Changes not applied", "Decisions or values not yet applied: Apply & Calculate")
        blocker = c.publish_blocker()
        has_det = c.detection is not None or c.published is not None
        d_res = d.elab.result
        corrected = d_res is not None and d_res.correction == "dcir"
        self.corr_pill.setVisible(corrected)
        if corrected:
            self.corr_pill.set("ok", "circle-check", "DCIR corrected", "The elaboration shown excludes the DCIR spans "
                               "(Cycle Correction)")
        why = blocker or ("" if c.unpublished else "Nothing new to apply")
        self.publish_btn.setEnabled(not why)
        self.publish_btn.setToolTip(why or "Measure the accepted pulses, save them and open Results")
        self.publish_hint.setText(why)
        self.publish_hint.setVisible(bool(why))
        self._fill_banner(c)
        # stepper
        self.st_det.setText(("✓  " if has_det else "1  ") + "Detection")
        set_prop(self.st_det, "current", c.step is Step.DETECTION)
        self.st_seg.setEnabled(has_det)
        self.st_seg.set_value("results" if c.step is Step.RESULTS else "review")
        self.st_seg.set_changed(c.step in (Step.REVIEW, Step.RESULTS))
        self.st_corr.setEnabled(has_det and self.corr is not None)
        self.st_corr.setToolTip("" if has_det else "Detect pulses first")
        set_prop(self.st_corr, "current", c.step is Step.CORRECTION)
        self.st_seg._btn["results"].setEnabled(c.published is not None)
        self.st_seg._btn["results"].setToolTip("" if c.published else "Calculate measurements to see the results")
        rv = c.step is Step.REVIEW
        for w in self.nav_groups:
            w.setVisible(rv)
        # body
        if c.is_running and c.run_kind == "detect" and not has_det:
            self.body.setCurrentIndex(3)
            self.run_title.setText(f"Detecting pulses · {d.name}")
            self._progress(self.run_bar, c.progress)
            self.run_phase.setText(c.phase or "Starting…")
            self.run_cancel.setEnabled(c.run is not Run.CANCELLING)
            self.run_cancel.setText("Cancelling…" if c.run is Run.CANCELLING else "Cancel")
        elif c.step is Step.DETECTION:
            self.body.setCurrentIndex(0)
            self._fill_detection(c)
        elif c.step is Step.RESULTS:
            self.body.setCurrentIndex(2)
            self._draw_results()
            self._fill_next_step(corrected)
        elif c.step is Step.CORRECTION and self.corr is not None:
            self.body.setCurrentIndex(4)
            self.corr_view.refresh()
        else:
            self.body.setCurrentIndex(1)
            self._fill_review(c, u)

    @staticmethod
    def _progress(bar: QProgressBar, fraction) -> None:
        if fraction is None:
            bar.setRange(0, 0)
        else:
            bar.setRange(0, 1000)
            bar.setValue(int(fraction * 1000))

    def _fill_banner(self, c) -> None:
        has_det = c.detection is not None or c.published is not None
        spec = None
        if c.is_running and (has_det or c.run_kind == "publish"):
            title = {"detect": "Detecting pulses again", "publish": "Measuring and saving"}[c.run_kind]
            if c.run is Run.CANCELLING:
                title = "Cancelling…"
            elif c.run is Run.PUBLISHING:
                title = "Saving result…"
            spec = ("n", "loader-circle", title, c.phase or "Starting…", True)
        elif c.outcome is Outcome.FAILED:
            spec = ("err", "circle-x", "Operation failed", f"{c.message}\nNothing was saved.", False)
        elif c.outcome is Outcome.CANCELLED:
            spec = ("n", "ban", "Operation cancelled", explain("OPERATION_CANCELLED").split(" (")[0], False)
        elif c.elab_changed and not has_det:
            spec = ("warn", "circle-alert", "Elaboration Changed", "The previous DCIR events belonged to an earlier "
                    "elaboration of this dataset, so they are no longer shown. Detect again.", False)
        if spec is None:
            self.banner.hide()
            return
        kind, icon, title, text, running = spec
        set_prop(self.banner, "banner", kind)
        col = getattr(self.theme.tokens, {"warn": "warn", "n": "muted", "err": "err"}[kind])
        self.b_icon.setPixmap(pixmap(icon, col, 18))
        self.b_title.setText(title)
        self.b_text.setText(text)
        self.b_bar.setVisible(running)
        if running:
            self._progress(self.b_bar, c.progress)
        self.b_btn.setVisible(running and c.run is not Run.PUBLISHING)
        self.b_btn.setEnabled(c.run is not Run.CANCELLING)
        self.banner.show()

    def _fill_detection(self, c) -> None:
        p = c.params
        for key, e in self.fields.items():
            if not e.hasFocus():
                txt = c.param_texts.get(key, f"{getattr(p, key):g}")
                if e.text() != txt:
                    e.setText(txt)
            set_prop(e, "error", key in c.param_errors)
        for k1, err in self.errs.items():
            k2 = k1.replace("_s", "_tolerance_pct")
            msg = c.param_errors.get(k1) or c.param_errors.get(k2) or ""
            err.setText(msg)
        self.pol.set_value(p.polarity)
        d = self.ctl.state.get(self.ds_id)
        has_det = c.detection is not None
        self.detect_btn.setText("Detect Again" if has_det else "Detect")
        ok = not c.param_errors and not c.is_running and not d.elab.is_running
        self.detect_btn.setEnabled(ok)
        self.detect_btn.setToolTip("" if ok else ("Correct the highlighted values" if c.param_errors else "Wait for the current operation"))
        pend, rej, unpub = c.redetect_info()
        self.warn.setVisible(has_det)
        if has_det:
            kept = c.counts()[ACCEPTED]
            self.warn_text.setText(f"{kept:,} Accepted pulse{'s' if kept != 1 else ''} (manual measurements included) are kept. "
                                   f"{pend:,} Pending and {rej:,} Rejected decisions will not be kept."
                                   + (" Changes not applied are lost unless you apply them first." if unpub else ""))
        if has_det:
            self.last_run.setText(f"Last detection: {len(c.detection.events):,} pulses · {describe_params(c.detection.params)}")
        else:
            self.last_run.setText("")

    def _fill_review(self, c, u) -> None:
        vis = c.visible()
        key = (self.ds_id, tuple((e.event_id, e.state, e.time_display) for e in vis),
               tuple(sorted((k, m.resistance) for k, m in c.previews.items())), tuple(c.manual), tuple(c.labels.items()), u,
               tuple(sorted(c.event_times.items())))
        if key != self._list_key:
            self._list_key = key
            self._fill_list(c, vis, u)
        self._sync_list_selection(c)
        for attr, cb in self.filters.items():
            cb.setCurrentIndex(cb.findData(getattr(c, attr)))
        ids = [e.event_id for e in vis]
        i = ids.index(c.selected) + 1 if c.selected in ids else 0
        self.pos_lab.setText(f"Pulse {i:,} of {len(ids):,}" if ids else "No pulses match the filters")
        self.prev_b.setEnabled(i > 1)
        self.next_b.setEnabled(0 < i < len(ids))
        self.r_head.setText(f"R [{u.resistance}]")
        self._fill_times(c)
        self.add_manual.setEnabled(not c.is_running and (c.detection is not None))
        e = c.event(c.selected)
        m = c.measurement(c.selected) if e else None
        if e is None:
            self.ev_title.setText("No pulse selected")
            self.ev_sub.setText("")
        else:
            self.ev_title.setText(c.label(e))
            self.ev_sub.setText(self._sub(e, u))
        self._fill_cells(c, e, m, u)
        ok_a, why_a = c.can_accept(e)
        ok_r, why_r = c.can_reject(e)
        self.accept_btn.setEnabled(ok_a)
        self.reject_btn.setEnabled(ok_r)
        self.accept_btn.setToolTip(why_a or "Accept (A)")
        self.reject_btn.setToolTip(why_r or "Reject (R)")
        self.act_hint.setText(why_a if (e is not None and not ok_a and e.state != ACCEPTED) else "A accept · R reject · ↑↓ move")
        self.pchart.set_labels(f"Voltage [{u.voltage}]", f"Current [{u.current}]")
        i1 = "3rd pulse sample" if c.current_time_s is None else "at I1 time"
        self.legend.setText(f"○ V0 · I0 reference   □ I1 {i1}   ◇ V1 at measurement time   dashed: requested times   "
                            "shaded: recorded rest before · pulse · rest after samples")

    def _fill_times(self, c) -> None:
        """V1 / I1 fields of the selected pulse (its own times, the all-pulse times, or what is being typed) and the
        Apply / Apply to All buttons. Apply needs an automatic pulse and typed times that differ from its times."""
        v1_text, i1_text = c.draft_texts()
        if not self.mt.hasFocus() and self.mt.text() != v1_text:
            self.mt.setText(v1_text)
        if not self.ct.hasFocus() and self.ct.text() != i1_text:
            self.ct.setText(i1_text)
        v1, i1, draft_err = c.draft_times()
        set_prop(self.mt, "error", draft_err.startswith("V1") or bool(c.time_error))
        set_prop(self.ct, "error", draft_err.startswith("I1") or bool(c.current_time_error))
        err = draft_err or c.time_error or c.current_time_error
        self.mt_err.setText(err)
        self.mt_err.setWordWrap(True)
        self.mt_err.setVisible(bool(err))
        self.mt.setEnabled(not c.is_running)
        self.ct.setEnabled(not c.is_running)
        e = c.event(c.selected)
        own = c.has_own_times(c.selected)
        self.mt_scope.setText("Times after V0 · this pulse" if own else "Times after V0 · all pulses")
        self.mt_scope.setToolTip("This pulse is measured at times of its own." if own else
                                 "This pulse is measured at the times used for all pulses.")
        ok, why = c.can_apply_to_pulse(e)
        one = ok and not draft_err and c.has_draft
        self.apply_one.setEnabled(one)
        self.apply_one.setToolTip(why or (draft_err if draft_err else
                                          "Use these times for this pulse only. It returns to Pending and must be "
                                          "accepted again." if one else "Type other times for this pulse first."))
        everywhere = not draft_err and not c.is_running and (
            (v1, i1) != (c.measurement_time_s, c.current_time_s) or bool(c.event_times))
        self.apply_all.setEnabled(everywhere)
        self.apply_all.setToolTip(draft_err or ("Use these times for every pulse; accepted pulses are measured "
                                                "again." if everywhere else "Every pulse already uses these times."))

    def _sub(self, e, u) -> str:
        parts = []
        if e.source == MANUAL:
            parts.append("Manual")
        elif e.polarity:
            parts.append(POLARITY_LABELS[e.polarity])
        if e.cycle is not None:
            parts.append(f"cycle {e.cycle}")
        if e.time_display is not None:
            parts.append(f"{e.time_display:.2f} {u.time}")
        c = self.ctl.dc(self.ds_id) if self.ds_id else None
        if c is not None and c.has_own_times(e.event_id):
            parts.append("own times")
        return " · ".join(parts)

    def _fill_list(self, c, vis, u) -> None:
        t = self.theme.tokens
        self.list.blockSignals(True)
        self.list.clear()
        col = {PENDING: t.muted, ACCEPTED: t.success, REJECTED: t.err}
        for e in vis:
            m = c.measurement(e.event_id)
            r = "" if (m is None or e.state != ACCEPTED) else num(m.resistance, digits=3)
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, e.event_id)
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 4, 8, 4)
            h.setSpacing(8)
            ic = QLabel()
            ic.setPixmap(pixmap("circle-plus" if (e.source == MANUAL and e.state == ACCEPTED) else STATE_ICON[e.state],
                                col[e.state], 14))
            ic.setToolTip(STATE_TEXT[e.state] + (" · manual" if e.source == MANUAL else ""))
            txt = QVBoxLayout()
            txt.setSpacing(0)
            a = _lab(c.label(e), size=13, weight=500 if e.state == ACCEPTED else 400, cls=ElidedLabel)
            b = _lab(self._sub(e, u), muted=True, size=11, cls=ElidedLabel)
            txt.addWidget(a)
            txt.addWidget(b)
            rl = _lab(r, size=12)
            rl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            h.addWidget(ic)
            h.addLayout(txt, 1)
            h.addWidget(rl)
            row.setAccessibleName(f"{c.label(e)}, {STATE_TEXT[e.state]}, {self._sub(e, u)}")
            it.setSizeHint(QSize(220, 44))
            self.list.addItem(it)
            self.list.setItemWidget(it, row)
        self.list.blockSignals(False)

    def _sync_list_selection(self, c) -> None:
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == c.selected:
                self.list.setCurrentItem(it)
                self.list.scrollToItem(it)
                break
        else:
            self.list.clearSelection()
        self.list.blockSignals(False)

    def _fill_cells(self, c, e, m, u) -> None:
        cells: list[tuple[str, str, str]] = []
        if e is not None:
            cells.append(("State", STATE_TEXT[e.state] + (" · manual" if e.source == MANUAL else ""), ""))
            if e.source == AUTO:
                cells.append(("Polarity", f"{POLARITY_LABELS.get(e.polarity, '—')} · {num(e.pulse_duration_s, 's', 3)}", ""))
            else:
                cells.append(("Source", "Manual measurement", ""))
            if m is not None:
                cells += [("I0 (reference)", num(m.i_ref, u.current), ""), ("V0 (reference)", num(m.v_ref, u.voltage, 5), ""),
                          (self._i1_title(e, m, c.current_time_s), num(m.i_meas, u.current), ""),
                          (f"V1 at {num(m.actual_time_s, 's', 3)}" if m.actual_time_s is not None else "V1",
                           num(m.v_meas, u.voltage, 5), ""),
                          ("ΔV", num(m.delta_v, u.voltage, 4), ""),
                          ("Resistance", num(m.resistance, u.resistance, 4),
                           "" if m.resistance is not None else explain("DCIR_ZERO_CURRENT_DIFFERENCE")),
                          ("SOC [%]", num(m.soc_pct, "", 3) if e.source == AUTO else "—",
                           "SOC is not calculated for manual measurements." if e.source == MANUAL else
                           (explain("DCIR_SOC_UNAVAILABLE") if m.soc_pct is None else "")),
                          ("Notes", ", ".join(x for x in m.diagnostics if x != "DCIR_SOC_UNAVAILABLE") or "—",
                           "\n".join(explain(x) for x in m.diagnostics))]
            elif e.state == REJECTED:
                cells.append(("Measurement", "Not measured (rejected)", ""))
            elif e.pre_pulse_row is None and e.source == AUTO:
                cells.append(("Measurement", "Not available", explain("DCIR_MISSING_PRE_PULSE_REFERENCE")))
            else:
                cells.append(("Measurement", "Measuring…", ""))
        for i, (k, v) in enumerate(self.cell_w):
            if i < len(cells):
                k.setText(cells[i][0])
                v.setText(cells[i][1])
                v.setToolTip(cells[i][2])
                k.show()
                v.show()
            else:
                k.hide()
                v.hide()

    @staticmethod
    def _i1_title(e, m, requested) -> str:
        """I1 cell title: the actual time after V0 of the sample the core used; '3rd sample' when no I1 time is set."""
        if e.source != AUTO:
            return "I1"
        at = f" at {num(m.current_time_s, 's', 3)}" if m.current_time_s is not None else ""
        return f"I1{at}" if requested is not None else f"I1 (3rd sample){at}"

    def _draw_trace(self) -> None:
        c = self.ctl.dc(self.ds_id) if self.ds_id else None
        e = c.event(c.selected) if c else None
        tr = self.ctl.traces.get(self.ds_id) if self.ds_id else None
        if not (tr is not None and e is not None and tr.event_id == e.event_id):
            self._trace_drawn = None
        if c is None or e is None:
            self.pchart.set_trace(None)
            self.pchart.show_message("Select a pulse in the list.")
        elif e.source == MANUAL:
            self.pchart.set_trace(None)
            self.pchart.show_message("Manual measurement: its values were typed or picked when it was added. "
                                     "The strip below shows them.")
        elif tr is None or tr.event_id != e.event_id:
            self.pchart.show_message("Reading the pulse…")
        else:
            measurable = e.state != REJECTED and e.pre_pulse_row is not None
            req, req_i = c.times_for(e.event_id) if measurable else (None, None)
            marks = self._measurement_marks(tr, c.measurement(e.event_id) if e.state != REJECTED else None)
            key = (id(tr), req, req_i, c.show_zones, tuple(sorted(marks.items())))
            self.pchart.show_message("")
            if key == getattr(self, "_trace_drawn", None):
                return                                            # same picture: keep the user's zoom
            same_trace = getattr(self, "_trace_drawn", None) is not None and self._trace_drawn[0] == id(tr)
            self._trace_drawn = key
            from dataclasses import replace
            self.pchart.set_trace(replace(tr, markers=marks), req, zones=c.show_zones, keep_view=same_trace,
                                  current_time_s=req_i)
        self.t_save.setEnabled(tr is not None)

    @staticmethod
    def _measurement_marks(tr, m) -> dict:
        """Trace indices of the real samples the core measurement used: V0/I0 = reference row, I1 = current row,
        V1 = voltage row. Only rows present in the drawn trace get a symbol; nothing is placed by time."""
        if tr is None or m is None:
            return {}
        rows = np.asarray(tr.rows)
        out = {}
        for keys, row in ((("v0", "i0"), m.reference_row), (("i1",), m.current_row), (("v1",), m.voltage_row)):
            if row is None:
                continue
            hit = np.flatnonzero(rows == row)
            if hit.size:
                for k in keys:
                    out[k] = int(hit[0])
        return out

    # ---------------------------------------------------------------- results
    def _draw_results(self) -> None:
        c = self.ctl.dc(self.ds_id) if self.ds_id else None
        if c is None or c.published is None:
            self.rchart.set_view(None)
            self.rmodel.set_rows([], [], [])
            return
        u = self.ctl.units.units_for(self.ds_id)
        pub = c.published
        pts, left = result_points(pub)
        self._res_pts = pts
        x = np.asarray([p[0] for p in pts], float)
        y = np.asarray([p[1] for p in pts], float)
        rows = np.arange(1, len(pts) + 1)                            # 1-based ids shown in the hover readout
        fit = lambda a: (float(a.min()) - (float(np.ptp(a)) * 0.06 or 1.0), float(a.max()) + (float(np.ptp(a)) * 0.06 or 1.0)) if a.size else (0.0, 1.0)
        vp = self._res_vp
        view = SimpleNamespace(y1=SimpleNamespace(x=x, y=y, source_row=rows), y2=None,
                               x_range=(vp.x.lo, vp.x.hi) if vp.x else fit(x),
                               y1_range=(vp.y1.lo, vp.y1.hi) if vp.y1 else fit(y), y2_range=None)
        self.rchart.set_style("cycle", ("Cycle", f"Resistance [{u.resistance}]", None), (c.res_color, CHART["y2"]), False, "markers")
        for item in (self.rchart.c1,):
            item.setSymbolSize(8)                                     # single colour, points only, no line
        self.rchart.set_view(view)
        self.rchart.show_message(None if pts else "chart-line", "" if pts else "No resistance to draw",
                                 "" if pts else "No calculated Accepted measurement has both a cycle and a resistance.")
        self.res_note.setText(f"{len(pts):,} calculated measurement{'s' if len(pts) != 1 else ''}"
                              + (f" · {left:,} without a value {'is' if left == 1 else 'are'} not drawn" if left else ""))
        own = len(pub.event_times)
        self.pub_note.setText((f"Calculated {pub.published_at} · Measurement time {pub.measurement_time_s:g} s"
                               + (f" · {own:,} pulse{'s' if own != 1 else ''} with own times" if own else ""))
                              .replace("Calculated  ·", "Calculated ·"))
        rows = result_rows(pub)
        out, tips = [r for r, _t in rows], [t for _r, t in rows]
        self.rmodel.set_rows(out, result_heads(u), tips)
        self.exp_btn.setEnabled(bool(out) and not self.ctl.is_exporting(self.ds_id))

    def _result_hover(self, i: int) -> None:
        if 1 <= i <= len(self._res_pts):
            cyc, r, _eid = self._res_pts[i - 1]
            self.rchart.show_details(i, {"cycle": cyc, "resistance": r}, ("cycle", "resistance", None))

    def _result_clicked(self, i: int) -> None:
        if 1 <= i <= len(self._res_pts):
            eid = self._res_pts[i - 1][2]
            for r in range(self.rmodel.rowCount()):
                if self.rmodel.rows[r]["label"] == dict(self.ctl.dc(self.ds_id).published.labels).get(eid):
                    self.rtable.selectRow(r)
                    self.rtable.scrollTo(self.rmodel.index(r, 0))
                    break

    # ---------------------------------------------------------------- dialogs / files
    def _manual(self) -> None:
        c = self.ctl.dc(self.ds_id)
        if c is None:
            return
        u = self.ctl.units.units_for(self.ds_id)
        e = c.manual_start_event()                                # opens where the user decided last
        try:
            tr = self.ctl.window(self.ds_id, e.event_time_s if e is not None else 0.0)
        except Exception:
            tr = None
        dlg = ManualDialog(self, self.theme, u, tr, u.time, preview_fn=lambda vals: self.ctl.preview_manual(self.ds_id, vals),
                           **self._navigation())
        if dlg.exec():
            self.ctl.add_manual(self.ds_id, dlg.chosen())

    def _navigation(self) -> dict:
        """Manual dialog navigation: read real samples around any time, and the cycle list for the cycle jump."""
        ds = self.ds_id
        return {"window_fn": lambda center, span: self.ctl.window(ds, center, span), "cycles": self.ctl.cycle_spans(ds)}

    def _set_span(self, event_id: str) -> None:
        """Set Span… (Correction): reopen the manual dialog with the values of that measurement and add the two
        boundary points; the measurement is replaced (new identity) and must be calculated again."""
        c = self.ctl.dc(self.ds_id)
        if c is None:
            return
        e, m = c.event(event_id), c.measurement(event_id)
        if e is None or m is None or not m.manual_values:
            QMessageBox.information(self, "Set Span", "The values of this manual measurement are not available.")
            return
        u = self.ctl.units.units_for(self.ds_id)
        try:
            tr = self.ctl.window(self.ds_id, e.event_time_s if e.event_time_s is not None else 0.0)
        except Exception:
            tr = None
        dlg = ManualDialog(self, self.theme, u, tr, u.time, preview_fn=lambda vals: self.ctl.preview_manual(self.ds_id, vals),
                           initial=m.manual_values, replacing=c.label(e), **self._navigation())
        if dlg.exec():
            self.ctl.replace_manual(self.ds_id, event_id, dlg.chosen())

    def _export(self) -> None:
        c = self.ctl.dc(self.ds_id)
        if c is None or c.published is None:
            return
        dlg = DcirExportDialog(self, self.rmodel.heads, self.rmodel.rowCount())
        if not dlg.exec():
            return
        fmt, cols = dlg.choice()
        d = self.ctl.state.get(self.ds_id)
        ext = ".csv" if fmt == "csv" else ".parquet"
        path = save_export(self, "Export DCIR measurements", f"{d.name} - dcir{ext}",
                           "CSV (*.csv)" if fmt == "csv" else "Parquet (*.parquet)", folder=dataset_folder(self.ctl.state.get(self.ds_id)))
        if path:
            if not path.lower().endswith(ext):
                path += ext
            self.ctl.export(self.ds_id, fmt, cols, path)

    def _export_state(self, ds_id: str, msg: str) -> None:
        if ds_id != self.ds_id:
            return
        self.exp_state.setText(msg)
        self.exp_cancel.setVisible(bool(msg))
        self.exp_btn.setEnabled(not msg)

    def _save_image(self) -> None:
        """Save Image: the chart exactly as shown, fixed palette, PNG."""
        c = self.ctl.dc(self.ds_id) if self.ds_id else None
        if c is None or c.step is Step.DETECTION:
            return
        d = self.ctl.state.get(self.ds_id)
        if c.step is Step.RESULTS:
            widget, name = self.rchart, f"{d.name} - DCIR resistance vs cycle.png"
        else:
            e = c.event(c.selected)
            if e is None or self.ctl.traces.get(self.ds_id) is None:
                return
            widget, name = self.pchart, f"{d.name} - {c.label(e)}.png"
        path = save_export(self, "Save Image", name, "PNG image (*.png)", folder=dataset_folder(self.ctl.state.get(self.ds_id)))
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        if widget.grab().save(path, "PNG"):
            self.window().statusBar().showMessage(f"Image saved: {path}", 6000)
        else:
            QMessageBox.warning(self, "Save Image", "Could not save the image.")
