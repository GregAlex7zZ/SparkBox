"""dQ/dV page: header + state pill + view switcher (dQ/dV | Voltage Reconstruction, as DCIR Review | Results),
banner, Ready card (method and analysis parameters, explicit Analyze), running card, the result (Phase / Plot / Cycles
shown above the chart, never recalculating; one large chart Voltage vs dQ/dV; footer with counts, curves not available,
Export Table…, View dQ/dV Data), the data view (8 published columns) and the Voltage Reconstruction view (measured vs
reconstructed voltage curves, capacity on X, with gregorithm's APE and RMSE per curve). The method reference
(Flores and Clark, 2026) is given in full in the (i) texts and in Help, not on the page. Widgets never decide: they
call DqdvController and re-read DqdvState."""
from __future__ import annotations
from typing import Optional
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
                               QRadioButton, QStackedWidget, QTableView, QToolButton, QVBoxLayout, QWidget)
from ..file_dialogs import dataset_folder, save_export
from ..dqdv import (HISTOGRAM_METHODS, INFO, METHOD_CHOICES, PHASE_CHOICES,
                    PLOT_CHOICES, RECON_CURVES, RECON_VIEWS, TABLE_COLUMNS, DqdvView, column_title, cycles_label,
                    dqdv_unit, explain, fmt_ape, fmt_rmse, method_summary, pill, points_note, recon_summary, unavailable_lines,
                    unavailable_text)
from ..dqdv_controller import DqdvController
from ..explorer import Viewport
from ..icons import make_icon, pixmap
from ..theme import ThemeManager, set_prop
from .controls import FlowLayout, InfoButton, SegmentedControl, StatusPill, Switch, group
from .multi_chart import LegendEntry, MultiChart, Trace


def _lab(text="", muted=False, size=None, weight=None) -> QLabel:
    l = QLabel(text)
    if muted:
        l.setProperty("muted", True)
    css = (f"font-size: {size * 0.75:g}pt;" if size else "") + (f"font-weight: {weight};" if weight else "")
    if css:
        l.setStyleSheet(css)
    return l


def _btn(text, kind, h=30) -> QPushButton:
    b = QPushButton(text)
    b.setProperty(kind, True)
    b.setFixedHeight(h)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class DqdvParamsForm(QWidget):
    """The analysis parameters, shared by the Ready card and the dQ/dV Settings dock (same state, same validation).
    Rows that do not apply to the chosen method are hidden (rule read from gregorithm's descriptor via DqdvState.uses);
    their values are kept."""

    def __init__(self, theme: ThemeManager, ctl: DqdvController, docked: bool):
        super().__init__()
        self.theme, self.ctl, self.ds_id, self.docked = theme, ctl, None, docked
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8 if not docked else 4)

        class _Cells:
            """Places cells on the logical (row, column) grid of the Ready card. Docked, the two columns are stacked
            (label above its field) so the form fits the fixed-width dock without horizontal scrolling."""

            @staticmethod
            def addWidget(widget, row, col, *align):
                if docked:
                    row, col = 2 * row + col, 0
                grid.addWidget(widget, row, col, *align)

        g = _Cells()
        self.tags: dict[str, QLabel] = {}
        self.errs: dict[str, QLabel] = {}
        self.rows: dict[str, list[QWidget]] = {}          # setting -> widgets hidden together

        def label(key, title, row):
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 8 if docked and row else 0, 0, 0)   # docked: gap above each label
            h.setSpacing(4)
            h.addWidget(_lab(title, muted=not docked, size=12 if not docked else 13))
            h.addWidget(InfoButton(theme, title, INFO[key]))
            tag = QLabel("Modified")
            tag.setProperty("tag", True)
            tag.hide()
            h.addWidget(tag)
            self.tags[key] = tag
            g.addWidget(w, row, 0, Qt.AlignmentFlag.AlignRight if not docked else Qt.AlignmentFlag.AlignLeft)
            self.rows.setdefault(key, []).append(w)

        def err(key, row):
            e = QLabel()
            e.setProperty("hintErr", True)
            e.setWordWrap(True)
            self.errs[key] = e
            g.addWidget(e, row, 1)

        def field(key, row, *widgets):
            w = self._row(*widgets)
            g.addWidget(w, row, 1)
            self.rows.setdefault(key, []).append(w)

        def line(accessible, attr, width=80, placeholder=""):
            e = QLineEdit()
            e.setFixedWidth(width)
            e.setAlignment(Qt.AlignmentFlag.AlignRight)
            e.setAccessibleName(accessible)
            e.setPlaceholderText(placeholder)
            e.textEdited.connect(lambda t: self._edit(lambda q: setattr(q, attr, t)))
            return e

        label("cycles", "Cycles to Analyze", 0)
        self.cycles = QLineEdit()
        self.cycles.setPlaceholderText("e.g. 1, 10, 50-55")
        self.cycles.setMinimumWidth(200)
        self.cycles.setAccessibleName("Cycles to Analyze")
        self.cycles.textEdited.connect(lambda t: self._edit(lambda q: setattr(q, "cycles_text", t)))
        self.cycles.returnPressed.connect(lambda: self.ctl.analyze(self.ds_id))
        g.addWidget(self.cycles, 0, 1)
        self.c_hint = _lab(muted=True, size=11)
        g.addWidget(self.c_hint, 1, 1)
        err("cycles", 2)
        label("method", "Method", 3)
        self.method = QComboBox()
        self.method.setAccessibleName("dQ/dV method")
        for key, title in METHOD_CHOICES:
            self.method.addItem(title, key)
        self.method.activated.connect(lambda i: self._edit(lambda q: setattr(q, "method", self.method.itemData(i))))
        field("method", 3, self.method)
        # The method reference (Flores and Clark) is given in full in the (i) of Method and Voltage Reconstruction
        # and in Help › Scientific Methods, not as a link in the form .
        label("e_range", "E min / E max", 5)
        self.emin = line("E min in V", "emin_text", 70, "min")
        self.emax = line("E max in V", "emax_text", 70, "max")
        field("e_range", 5, self.emin, _lab("–", muted=True), self.emax, _lab("V", muted=True, size=12))
        err("e_range", 6)
        label("points", "Interpolation Points", 9)
        self.points = line("Interpolation points, count or percentage", "points_text", 80, "10%")
        field("points", 9, self.points, _lab("e.g. 200 or 10%", muted=True, size=11))
        err("points", 10)
        label("edge_trim_mv", "Edge Trim", 11)
        self.trim = line("Edge trim in mV", "trim_text")
        field("edge_trim_mv", 11, self.trim, _lab("mV", muted=True, size=12))
        err("edge_trim_mv", 12)
        label("bin_mv", "Bin Size", 13)
        self.bin = line("Histogram bin size in mV", "bin_text")
        field("bin_mv", 13, self.bin, _lab("mV", muted=True, size=12))
        err("bin_mv", 14)
        label("smoothing", "Smoothing", 15)
        self.smooth = Switch(theme, "Smoothing")
        self.smooth.toggled.connect(lambda on: self._edit(lambda q: setattr(q, "smoothing", on)))
        self.sm_note = _lab("Savitzky–Golay, applied to the derivative", muted=True, size=11)
        self.sm_note.setWordWrap(True)
        field("smoothing", 15, self.smooth, self.sm_note)
        label("sg_window", "SG Window", 16)
        self.win = line("SG Window in points", "window_text")
        field("sg_window", 16, self.win, _lab("points", muted=True, size=12))
        err("sg_window", 17)
        label("sg_order", "SG Polynomial Order", 18)
        self.order = line("SG Polynomial Order", "order_text")
        field("sg_order", 18, self.order)
        err("sg_order", 19)
        label("gaussian_sigma", "Gaussian Width", 20)
        self.sigma = line("Gaussian filter width in bins", "sigma_text")
        field("gaussian_sigma", 20, self.sigma, _lab("bins", muted=True, size=12))
        err("gaussian_sigma", 21)
        label("reconstruction", "Voltage Reconstruction", 22)
        self.recon = Switch(theme, "Voltage Reconstruction", True)
        self.recon.toggled.connect(lambda on: self._edit(lambda q: setattr(q, "reconstruction", on)))
        rec_note = _lab("Rebuild the voltage curve from dQ/dV; APE and RMSE", muted=True, size=11)
        rec_note.setWordWrap(True)
        field("reconstruction", 22, self.recon, rec_note)
        dcir_lab = _lab("DCIR pulses", muted=not docked, size=12 if not docked else 13)
        if docked:
            dcir_lab.setContentsMargins(0, 8, 0, 0)
        g.addWidget(dcir_lab, 23, 0, Qt.AlignmentFlag.AlignRight if not docked else Qt.AlignmentFlag.AlignLeft)
        self.dcir = QLabel()
        self.dcir.setProperty("chip", True)
        self.dcir.setToolTip("gregorithm excludes the spans of the calculated Accepted DCIR pulses (set in DCIR). "
                             "Not a setting of dQ/dV.")
        g.addWidget(self.dcir, 23, 1)
        if docked:                                            # label above field in the fixed-width dock
            grid.setColumnStretch(0, 1)
            self.cycles.setMinimumWidth(0)
        else:
            grid.setColumnStretch(1, 1)

    @staticmethod
    def _row(*ws) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        for x in ws:
            h.addWidget(x)
        h.addStretch(1)
        return w

    def _edit(self, fn) -> None:
        if self.ds_id:
            self.ctl.edit(self.ds_id, fn)

    def refresh(self) -> None:
        q = self.ctl.dq(self.ds_id) if self.ds_id else None
        if q is None:
            return
        av = self.ctl.available(self.ds_id)
        texts = ((self.cycles, q.cycles_text), (self.win, q.window_text),
                 (self.order, q.order_text), (self.emin, q.emin_text), (self.emax, q.emax_text),
                 (self.points, q.points_text), (self.trim, q.trim_text), (self.bin, q.bin_text),
                 (self.sigma, q.sigma_text))
        for w, txt in texts:
            if not w.hasFocus() and w.text() != txt:
                w.setText(txt)
        for sw, on in ((self.smooth, q.smoothing), (self.recon, q.reconstruction)):
            sw.blockSignals(True)
            sw.setChecked(on)
            sw.blockSignals(False)
        i = self.method.findData(q.method)
        if i >= 0 and i != self.method.currentIndex():
            self.method.setCurrentIndex(i)
        histogram = q.method in HISTOGRAM_METHODS
        self.sm_note.setText("Gaussian filter of the histogram" if histogram else "Savitzky–Golay, applied to the derivative")
        for key in ("points", "edge_trim_mv", "bin_mv"):
            self._show(key, q.uses(key))
        self._show("sg_window", q.method not in HISTOGRAM_METHODS)
        self._show("sg_order", q.method not in HISTOGRAM_METHODS)
        self._show("gaussian_sigma", histogram)
        for w in (self.win, self.order, self.sigma):
            w.setEnabled(q.smoothing and not q.is_running)       # values kept while smoothing is off
        for w in (self.cycles, self.smooth, self.method, self.emin, self.emax, self.points, self.trim,
                  self.bin, self.recon):
            w.setEnabled(not q.is_running)
        if av:
            from ..explorer import describe_available
            self.c_hint.setText(f"Available cycles {describe_available([c for c in av if c >= 1])} · required")
        else:
            self.c_hint.setText("e.g. 1 · 1-100 · 1,6,9 · required")
        errs = q.errors(av)
        if not q.cycles_text.strip():
            # An empty, required field is not an error the user made: the hint says "required" and Analyze stays
            # disabled, so nothing is shown in red before the user types (Apple HIG: validate after interaction).
            errs.pop("cycles", None)
        errs_shown = dict(errs)
        if "e_min" in errs or "e_max" in errs:
            errs_shown["e_range"] = errs.get("e_min") or errs.get("e_max")
        for k, e in self.errs.items():
            e.setText(errs_shown.get(k, ""))
            e.setVisible(k in errs_shown)
        fields = (("cycles", self.cycles), ("sg_window", self.win), ("sg_order", self.order),
                  ("e_min", self.emin), ("e_max", self.emax), ("points", self.points), ("edge_trim_mv", self.trim),
                  ("bin_mv", self.bin), ("gaussian_sigma", self.sigma))
        for k, w in fields:
            set_prop(w, "error", k in errs)
        mods = self.ctl.modified(self.ds_id)
        for k, tag in self.tags.items():
            tag.setVisible(k in mods or (k in ("sg_window", "sg_order") and "sg" in mods))
        pub = self.ctl.dcir_published(self.ds_id)
        if q.result is not None and q.result.n_excluded_spans:
            self.dcir.setText(f"Excludes {q.result.n_excluded_spans:,} accepted DCIR span{'s' if q.result.n_excluded_spans != 1 else ''}")
        elif pub is not None:
            self.dcir.setText("Excludes the accepted DCIR spans")
        else:
            self.dcir.setText("No calculated DCIR pulses")

    def _show(self, key: str, visible: bool) -> None:
        for w in self.rows.get(key, ()):
            w.setVisible(visible)
        if key in self.errs and not visible:
            self.errs[key].setVisible(False)


class DqdvTableModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.rows: list[tuple] = []
        self.heads: list[str] = []
        self.null_color = "#8798A2"

    def set_rows(self, rows, heads) -> None:
        self.beginResetModel()
        self.rows, self.heads = rows, heads
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(TABLE_COLUMNS)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        v = self.rows[index.row()][index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            if v is None:
                return "—"
            if isinstance(v, float):
                return format(v, ".4g").replace("-", "−")        # 4 significant digits on screen
            return f"{v:,}" if isinstance(v, int) and index.column() == 3 else str(v)
        if role == Qt.ItemDataRole.ToolTipRole and isinstance(v, float):
            return repr(v)                                            # full precision
        if role == Qt.ItemDataRole.ForegroundRole and v is None:
            return QColor(self.null_color)
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() >= 3:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal and section < len(self.heads):
            return self.heads[section]
        return None


class ReconMetricsModel(QAbstractTableModel):
    """APE and RMSE per curve, as published by gregorithm (reports/dqdv.json); '—' when unavailable."""
    HEADS = ("Cycle · phase", "APE", "RMSE")

    def __init__(self):
        super().__init__()
        self.items: list = []
        self.voltage_unit = "V"
        self.null_color = "#8798A2"

    def set_rows(self, items, voltage_unit: str) -> None:
        self.beginResetModel()
        self.items, self.voltage_unit = list(items), voltage_unit
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADS)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        x, col = self.items[index.row()], index.column()
        missing = col >= 1 and (x.ape_percent if col == 1 else x.rmse) is None
        if role == Qt.ItemDataRole.DisplayRole:
            return (f"{x.cycle} · {x.phase}", fmt_ape(x.ape_percent), fmt_rmse(x.rmse, self.voltage_unit))[col]
        if role == Qt.ItemDataRole.ToolTipRole:
            if missing and x.code:
                return explain(x.code)
            if col == 1 and x.ape_percent is not None:
                return repr(x.ape_percent)
            if col == 2 and x.rmse is not None:
                return f"{x.rmse!r} {self.voltage_unit}"
        if role == Qt.ItemDataRole.ForegroundRole and missing:
            return QColor(self.null_color)
        if role == Qt.ItemDataRole.TextAlignmentRole and col >= 1:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADS[section]
        return None


class DqdvExportDialog(QDialog):
    def __init__(self, parent, heads: list[str], rows_text: str):
        super().__init__(parent)
        self.setWindowTitle("Export dQ/dV Table")
        self.setMinimumWidth(460)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)
        v.addWidget(_lab("Export dQ/dV table", size=18, weight=500))
        t = _lab(rows_text, muted=True)
        t.setWordWrap(True)
        v.addWidget(t)
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
        self.boxes = {}
        for i, ((key, _t, _q), head) in enumerate(zip(TABLE_COLUMNS, heads)):
            b = QCheckBox(head)
            b.setChecked(True)
            self.boxes[key] = b
            grid.addWidget(b, i // 2, i % 2)
        v.addLayout(grid)
        note = _lab("Rows: the derived points of the curves shown (cycles and phase chosen above the chart). Values at "
                    "full precision in the units shown. Empty cells stay empty.", muted=True, size=12)
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

    def choice(self):
        return ("parquet" if self.parquet.isChecked() else "csv"), [k for k, b in self.boxes.items() if b.isChecked()]


class DqdvPage(QWidget):
    goElaborationRequested = Signal()

    def __init__(self, theme: ThemeManager, ctl: DqdvController):
        super().__init__()
        self.theme, self.ctl = theme, ctl
        self.ds_id: Optional[str] = None
        self._drawn = None
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 12)
        root.setSpacing(10)
        h = QHBoxLayout()
        h.setSpacing(12)
        h.addWidget(_lab("dQ/dV", size=16, weight=500))
        self.tab_seg = SegmentedControl(list(RECON_VIEWS), small=True, aria="dQ/dV or voltage reconstruction")
        self.tab_seg.setProperty("stepper", True)            # styled as the DCIR Review | Results pills
        for b in self.tab_seg._btn.values():
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tab_seg.valueChanged.connect(lambda v: self.ctl.edit(self.ds_id, lambda q: setattr(q, "tab", v)))
        h.addWidget(self.tab_seg)
        self.meta = _lab(muted=True, size=12)
        self.meta.setWordWrap(True)
        self.units_pill, self.pill = StatusPill(theme), StatusPill(theme)
        h.addWidget(self.meta, 1)
        h.addWidget(self.units_pill)
        h.addStretch(1)
        h.addWidget(self.pill)
        root.addLayout(h)
        self.banner = self._banner()
        root.addWidget(self.banner)
        self.body = QStackedWidget()
        self.body.addWidget(self._ready())      # 0
        self.body.addWidget(self._running())    # 1
        self.body.addWidget(self._result())     # 2
        self.body.addWidget(self._data())       # 3
        self.body.addWidget(self._recon())      # 4
        root.addWidget(self.body, 1)
        ctl.changed.connect(lambda ds: self.refresh() if ds == self.ds_id else None)
        ctl.progressed.connect(lambda ds: self._show_progress() if ds == self.ds_id else None)
        ctl.curvesReady.connect(lambda ds: (self._draw(force=True), self._draw_recon(force=True)) if ds == self.ds_id else None)
        ctl.exportState.connect(self._export_state)
        theme.changed.connect(self._icons)
        self._icons()

    # ================================================================ construction
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
        v.setSpacing(12)
        self.r_title = _lab("Ready to Analyze dQ/dV", size=20, weight=500)
        self.r_text = _lab("gregorithm calculates the differential capacity of the cycles you choose, for charge and "
                           "discharge, from the constant-current (CC) parts of the current elaboration. Nothing is "
                           "drawn until the analysis is complete.", muted=True)
        self.r_text.setWordWrap(True)
        self.r_text.setFixedWidth(560)        # a centred word-wrapped label otherwise gets a too-short height and clips
        for x in (self.r_title, self.r_text):
            x.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            v.addWidget(x, 0, Qt.AlignmentFlag.AlignHCenter)
        box = QFrame()
        box.setProperty("panel", True)
        bl = QVBoxLayout(box)
        bl.setContentsMargins(20, 16, 20, 16)
        self.form = DqdvParamsForm(self.theme, self.ctl, docked=False)
        bl.addWidget(self.form)
        v.addWidget(box, 0, Qt.AlignmentFlag.AlignHCenter)
        self.analyze_btn = _btn("Analyze", "primary", 36)
        self.analyze_btn.setProperty("run", True)                     # amber: starts a calculation
        self.analyze_btn.clicked.connect(lambda: self.ctl.analyze(self.ds_id))
        v.addWidget(self.analyze_btn, 0, Qt.AlignmentFlag.AlignHCenter)
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
        note = _lab("Nothing is saved until the analysis completes. 100% can be reached before saving ends.",
                    muted=True, size=12)
        note.setWordWrap(True)
        cl.addLayout(top)
        cl.addWidget(self.run_bar)
        cl.addWidget(self.run_phase)
        cl.addWidget(note)
        v.addWidget(card)
        return w

    def _result(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        bar = QWidget()                     # wraps onto a second line when narrow (e.g. dock open): nothing is cut
        flow = FlowLayout(bar, h_spacing=14, v_spacing=6)
        self.phase_seg = SegmentedControl(list(PHASE_CHOICES), small=True, aria="Phase")
        self.phase_seg.valueChanged.connect(lambda val: self._view(lambda vs: setattr(vs, "phase", val)))
        self.plot_seg = SegmentedControl(list(PLOT_CHOICES), small=True, aria="Plot")
        self.plot_seg.valueChanged.connect(lambda val: self._view(lambda vs: setattr(vs, "plot", val)))
        self.shown = QLineEdit()
        self.shown.setFixedWidth(210)
        self.shown.setAccessibleName("Cycles shown")
        self.shown.textEdited.connect(lambda t: self._view(lambda vs: vs.type_shown(t), redraw=False))
        self.shown.returnPressed.connect(lambda: self._view(lambda vs: vs.apply_shown()))
        self.shown.editingFinished.connect(lambda: self._view(lambda vs: vs.apply_shown()))
        self.shown_err = QLabel()
        self.shown_err.setProperty("hintErr", True)
        for title, wd in (("Phase", self.phase_seg), ("Plot", self.plot_seg)):
            flow.addWidget(group(_lab(title, muted=True, size=12, weight=500), wd, spacing=8))
        flow.addWidget(group(_lab("Cycles", muted=True, size=12, weight=500), self.shown, self.shown_err, spacing=8))
        tools = QFrame()
        tools.setObjectName("Segmented")
        tl = QHBoxLayout(tools)
        tl.setContentsMargins(2, 2, 2, 2)
        tl.setSpacing(2)
        self.t_box, self.t_pan, self.t_reset, self.t_save = (QToolButton() for _ in range(4))
        grp = QButtonGroup(self)
        for b, name, tip in ((self.t_box, "Box Zoom", "Box Zoom · drag in the chart"), (self.t_pan, "Pan", "Pan · or hold Shift and drag"),
                             (self.t_reset, "Reset View", "Reset View"), (self.t_save, "Save Image", "Save Image…")):
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
        self.t_reset.clicked.connect(lambda: self._view(lambda vs: setattr(vs, "viewport", Viewport())))
        self.t_save.clicked.connect(self._save_image)
        flow.addWidget(tools)
        v.addWidget(bar)
        self.chart = MultiChart()
        self.chart.rangeChanged.connect(lambda x, y: self._view(lambda vs: setattr(vs, "viewport", Viewport(x, y)), redraw=False))
        self.chart.traceClicked.connect(lambda key: self._view(lambda vs: setattr(vs, "selected", key[:2]), redraw=False))
        v.addWidget(self.chart, 1)
        foot = QHBoxLayout()
        self.foot = _lab(muted=True, size=12)
        self.unav = _btn("", "link", 24)
        self.unav.clicked.connect(self._show_unavailable)
        self.exp_state = _lab(muted=True, size=12)
        self.exp_cancel = _btn("Cancel Export", "ghost")
        self.exp_cancel.clicked.connect(lambda: self.ctl.cancel_export(self.ds_id))
        self.exp_cancel.hide()
        self.exp_btn = _btn("Export Table…", "ghost")
        self.exp_btn.clicked.connect(self._export)
        self.data_btn = _btn("View dQ/dV Data", "ghost")
        self.data_btn.clicked.connect(lambda: self.ctl.edit(self.ds_id, lambda q: setattr(q, "data_view", True)))
        for x in (self.foot, self.unav):
            foot.addWidget(x)
        foot.addStretch(1)
        for x in (self.exp_state, self.exp_cancel, self.exp_btn, self.data_btn):
            foot.addWidget(x)
        v.addLayout(foot)
        return w

    def _data(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        h = QHBoxLayout()
        back = _btn("‹  Back to chart", "ghost")
        back.clicked.connect(lambda: self.ctl.edit(self.ds_id, lambda q: setattr(q, "data_view", False)))
        h.addWidget(back)
        h.addWidget(_lab("dQ/dV data", size=14, weight=500))
        self.d_note = _lab(muted=True, size=12)
        h.addWidget(self.d_note)
        h.addStretch(1)
        exp = _btn("Export Table…", "ghost")
        exp.clicked.connect(self._export)
        h.addWidget(exp)
        v.addLayout(h)
        self.model = DqdvTableModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        QShortcut(QKeySequence.StandardKey.Copy, self.table, activated=self._copy)
        v.addWidget(self.table, 1)
        return w

    def _recon(self) -> QWidget:
        """Voltage Reconstruction: measured and reconstructed voltage curves (capacity on X) and APE / RMSE per curve."""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        bar = QWidget()
        flow = FlowLayout(bar, h_spacing=14, v_spacing=6)
        self.rc_phase = SegmentedControl(list(PHASE_CHOICES), small=True, aria="Phase")
        self.rc_phase.valueChanged.connect(lambda val: self._view(lambda vs: setattr(vs, "phase", val)))
        self.rc_curves = SegmentedControl(list(RECON_CURVES), small=True, aria="Curves")
        self.rc_curves.valueChanged.connect(lambda val: self._view(lambda vs: setattr(vs, "recon_curves", val)))
        self.rc_shown = QLineEdit()
        self.rc_shown.setFixedWidth(210)
        self.rc_shown.setAccessibleName("Cycles shown")
        self.rc_shown.textEdited.connect(lambda t: self._view(lambda vs: vs.type_shown(t), redraw=False))
        self.rc_shown.returnPressed.connect(lambda: self._view(lambda vs: vs.apply_shown()))
        self.rc_shown.editingFinished.connect(lambda: self._view(lambda vs: vs.apply_shown()))
        for title, wd in (("Phase", self.rc_phase), ("Curves", self.rc_curves), ("Cycles", self.rc_shown)):
            flow.addWidget(group(_lab(title, muted=True, size=12, weight=500), wd, spacing=8))
        tools = QFrame()
        tools.setObjectName("Segmented")
        tl = QHBoxLayout(tools)
        tl.setContentsMargins(2, 2, 2, 2)
        tl.setSpacing(2)
        self.rc_box, self.rc_pan, self.rc_reset, self.rc_save = (QToolButton() for _ in range(4))
        grp = QButtonGroup(self)
        for b, name, tip in ((self.rc_box, "Box Zoom", "Box Zoom · drag in the chart"), (self.rc_pan, "Pan", "Pan · or hold Shift and drag"),
                             (self.rc_reset, "Reset View", "Reset View"), (self.rc_save, "Save Image", "Save Image…")):
            b.setObjectName("ChartTool")
            b.setFixedSize(34, 32)
            b.setIconSize(QSize(18, 18))
            b.setToolTip(tip)
            b.setAccessibleName(name)
            if b in (self.rc_box, self.rc_pan):
                b.setCheckable(True)
                grp.addButton(b)
            tl.addWidget(b)
        self.rc_box.setChecked(True)
        self.rc_box.toggled.connect(lambda on: setattr(self.rc_chart, "pan_mode", not on))
        self.rc_reset.clicked.connect(lambda: self._view(lambda vs: setattr(vs, "recon_viewport", Viewport())))
        self.rc_save.clicked.connect(self._save_image)
        flow.addWidget(tools)
        v.addWidget(bar)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.rc_chart = MultiChart()
        self.rc_chart.rangeChanged.connect(
            lambda x, y: self._view(lambda vs: setattr(vs, "recon_viewport", Viewport(x, y)), redraw=False))
        row.addWidget(self.rc_chart, 1)
        side = QFrame()
        side.setProperty("panel", True)
        side.setFixedWidth(330)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 10, 12, 10)
        sl.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(_lab("Reconstruction Quality", weight=500))
        head.addWidget(InfoButton(self.theme, "Reconstruction quality", INFO["reconstruction"]))
        head.addStretch(1)
        sl.addLayout(head)
        note = _lab("APE: error of the total capacity. RMSE: voltage error over the common capacity range. "
                    "Lower is better; 0 is perfect.", muted=True, size=11)
        note.setWordWrap(True)
        sl.addWidget(note)
        self.rc_model = ReconMetricsModel()
        self.rc_table = QTableView()
        self.rc_table.setModel(self.rc_model)
        self.rc_table.verticalHeader().hide()
        self.rc_table.setAlternatingRowColors(True)
        self.rc_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        hh = self.rc_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)           # Curve takes the slack: nothing is cut
        self.rc_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sl.addWidget(self.rc_table, 1)
        row.addWidget(side)
        v.addLayout(row, 1)
        self.rc_foot = _lab(muted=True, size=12)
        self.rc_foot.setWordWrap(True)
        v.addWidget(self.rc_foot)
        return w

    def _icons(self) -> None:
        t = self.theme.tokens
        for b, name in ((self.t_box, "square-dashed"), (self.t_pan, "move"), (self.t_reset, "rotate-ccw"), (self.t_save, "download"),
                        (self.rc_box, "square-dashed"), (self.rc_pan, "move"), (self.rc_reset, "rotate-ccw"),
                        (self.rc_save, "download")):
            b.setIcon(make_icon(name, t.muted, checked=t.pri, disabled=t.dis, size=18))
        self.model.null_color = t.dis
        self.rc_model.null_color = t.dis
        self.refresh()

    # ================================================================ behaviour
    def show_dataset(self, ds_id: Optional[str]) -> None:
        self.ds_id = ds_id
        self.form.ds_id = ds_id
        self._drawn = self._rc_drawn = None
        if ds_id:
            self.ctl.load_curves(ds_id)
        self.refresh()

    def _view(self, fn, redraw: bool = True) -> None:
        q = self.ctl.dq(self.ds_id) if self.ds_id else None
        if q is None:
            return
        fn(q.view)
        if redraw:
            self.ctl.changed.emit(self.ds_id)
        else:
            self._sync_controls(q)

    def _banner_action(self) -> None:
        if self.b_btn.property("action") == "cancel":
            self.ctl.cancel(self.ds_id)
        else:
            self.ctl.analyze(self.ds_id)

    def refresh(self) -> None:
        q = self.ctl.dq(self.ds_id) if self.ds_id else None
        if q is None or not hasattr(self, "table"):
            return
        d = self.ctl.state.get(self.ds_id)
        view = self.ctl.view(self.ds_id)
        label, kind, icon = pill(view, q.result)
        self.pill.set(kind, icon, label)
        custom = self.ctl.units.is_custom(self.ds_id)
        self.units_pill.setVisible(custom)
        if custom:
            self.units_pill.set("p", "ruler", "Custom units", "This dataset uses its own units (Settings › Units)")
        r = q.result
        if r is not None:
            p = r.params
            self.meta.setText(f"{d.name} · cycles {cycles_label(p.cycles)} · {method_summary(p)}")
        else:
            self.meta.setText(f"{d.name} · elaboration ready · dQ/dV not analyzed yet")
        self.tab_seg.setVisible(r is not None)
        if r is not None:
            self.tab_seg.set_value(q.tab)
            b = self.tab_seg._btn["reconstruction"]
            b.setEnabled(r.has_reconstruction)
            b.setToolTip("" if r.has_reconstruction else
                         "This result was calculated without voltage reconstruction (dQ/dV Settings › Voltage Reconstruction)")
        self.form.refresh()
        self._fill_banner(view, q)
        if view in (DqdvView.NOT_ANALYZED, DqdvView.ELAB_CHANGED):
            self.body.setCurrentIndex(0)
            changed = view is DqdvView.ELAB_CHANGED
            self.r_title.setText("Elaboration Changed" if changed else "Ready to Analyze dQ/dV")
            if changed:
                self.r_text.setText("The previous dQ/dV result was based on an earlier elaboration of this dataset, so it "
                                    "is no longer shown. Analyze again to use the current elaboration.")
            b = self.ctl.blocker(self.ds_id)
            self.analyze_btn.setEnabled(not b)
            self.analyze_btn.setToolTip(b)
        elif r is None:
            self.body.setCurrentIndex(1)
            self.run_title.setText(f"Analyzing dQ/dV · {d.name}")
            self._progress(self.run_bar, q.progress)
            self.run_phase.setText(q.phase or "Starting…")
            self.run_cancel.setEnabled(view is not DqdvView.CANCELLING)
            self.run_cancel.setVisible(view is not DqdvView.PUBLISHING)
        elif q.tab == "reconstruction" and r.has_reconstruction:
            self.body.setCurrentIndex(4)
            self._sync_recon(q)
            self._draw_recon()
        elif q.data_view:
            self.body.setCurrentIndex(3)
            self._fill_table(q)
        else:
            self.body.setCurrentIndex(2)
            self._sync_controls(q)
            self._draw()

    def _show_progress(self) -> None:
        """Move the progress bar and phase text of the running analysis, without rebuilding the page.

        Only the widgets that show progress change: the full-page card of a first analysis, or the banner bar while a
        previous result stays on screen. Anything else (a state change) arrives through ``changed`` and refresh()."""
        q = self.ctl.dq(self.ds_id) if self.ds_id else None
        if q is None or not hasattr(self, "table"):
            return
        if q.result is None and self.body.currentIndex() == 1:
            self._progress(self.run_bar, q.progress)
            self.run_phase.setText(q.phase or "Starting…")
        elif self.banner.isVisibleTo(self) and self.b_bar.isVisibleTo(self):
            self._progress(self.b_bar, q.progress)

    @staticmethod
    def _progress(bar, fraction) -> None:
        if fraction is None:
            bar.setRange(0, 0)
        else:
            bar.setRange(0, 1000)
            bar.setValue(int(fraction * 1000))

    def _fill_banner(self, view: DqdvView, q) -> None:
        mods = self.ctl.modified(self.ds_id)
        why = []
        if mods & {"cycles", "smoothing", "sg", "method", "e_range", "points", "edge_trim_mv",
                   "bin_mv", "gaussian_sigma", "reconstruction"}:
            why.append("the analysis parameters changed")
        if "dcir" in mods:
            why.append("the accepted DCIR pulses changed")
        if "elab" in mods:
            why.append("a new elaboration (cycle correction) was created")
        spec = {
            DqdvView.OUTDATED: ("warn", "refresh-cw", "Based on previous settings",
                                f"Since this result was calculated {' and '.join(why) or 'something changed'}. The chart "
                                "shows the previous result. Reanalyze to apply.", "Reanalyze"),
            DqdvView.REANALYZING: ("n", "loader-circle", "Analyzing dQ/dV · previous result still shown",
                                   "The previous result is replaced only when the new one is ready.", "Cancel"),
            DqdvView.CANCELLING: ("n", "loader-circle", "Cancelling…", "Nothing will be saved.", ""),
            DqdvView.PUBLISHING: ("n", "loader-circle", "Saving result…", "Work is complete; the new result is being saved.", ""),
            DqdvView.CANCELLED: ("n", "ban", "Analysis cancelled", "Nothing was saved. The previous result is still shown.", "Reanalyze"),
            DqdvView.FAILED: ("err", "circle-x", "Analysis failed", f"{q.message}\nNothing was saved.", "Reanalyze"),
        }.get(view)
        if spec is None or q.result is None:
            self.banner.hide()
            return
        kind, icon, title, text, btn = spec
        set_prop(self.banner, "banner", kind)
        self.b_icon.setPixmap(pixmap(icon, getattr(self.theme.tokens, {"warn": "warn", "n": "muted", "err": "err"}[kind]), 18))
        self.b_title.setText(title)
        self.b_text.setText(text)
        running = view in (DqdvView.REANALYZING, DqdvView.CANCELLING, DqdvView.PUBLISHING)
        self.b_bar.setVisible(running)
        if running:
            self._progress(self.b_bar, q.progress)
        self.b_btn.setVisible(bool(btn))
        self.b_btn.setText(btn)
        self.b_btn.setProperty("action", "cancel" if btn == "Cancel" else "analyze")
        set_prop(self.b_btn, "goElab", btn != "Cancel")
        set_prop(self.b_btn, "ghost", btn == "Cancel")
        set_prop(self.b_btn, "run", btn in ("Analyze", "Reanalyze"))
        if btn == "Reanalyze":
            b = self.ctl.blocker(self.ds_id)
            self.b_btn.setEnabled(not b)
            self.b_btn.setToolTip(b)
        self.banner.show()

    def _sync_controls(self, q) -> None:
        vs, r = q.view, q.result
        self.phase_seg.set_value(vs.phase)
        self.plot_seg.set_value(vs.effective_plot(r.has_smoothed))
        for key in ("smoothed", "both"):
            b = self.plot_seg._btn[key]
            b.setEnabled(r.has_smoothed)
            b.setToolTip("" if r.has_smoothed else "Smoothing was not calculated for this result (dQ/dV Settings › Smoothing)")
        if not self.shown.hasFocus():
            self.shown.setText(vs.shown_text)
        self.shown.setPlaceholderText(f"All analyzed ({cycles_label(r.params.cycles)})")
        set_prop(self.shown, "error", bool(vs.shown_error))
        self.shown_err.setText(vs.shown_error)
        self.shown_err.setVisible(bool(vs.shown_error))
        self.chart.show_legend(vs.legend)

    # ---------------------------------------------------------------- chart
    def _traces(self, q):
        curves = self.ctl.curves.get(self.ds_id)
        if curves is None:
            return None, [], 0
        r, vs = q.result, q.view
        analyzed = sorted(r.params.cycles)
        cyc = set(vs.visible_cycles(analyzed))
        phases = vs.visible_phases()
        kinds = vs.kinds(r.has_smoothed)
        traces, legend, npts, seen = [], [], 0, []
        for cv in curves:
            if cv.cycle not in cyc or cv.phase not in phases:
                continue
            for kind in kinds:
                st = vs.trace_style(cv.cycle, cv.phase, kind, analyzed)
                segs = []
                for sg in cv.segments:
                    y = sg.smoothed if kind == "smoothed" else sg.raw
                    if y is None:
                        continue
                    segs.append((sg.v, y, sg.segment_index))
                    npts += len(sg.v)
                if segs:
                    traces.append(Trace((cv.cycle, cv.phase, kind), segs, st.color, st.width, st.alpha,
                                        st.dash if vs.style != "markers" else "none",
                                        st.symbol if vs.style != "lines" or st.symbol else None,
                                        f"Cycle {cv.cycle} · {cv.phase} · {kind}"))
            if cv.cycle not in seen:
                seen.append(cv.cycle)
        for c in sorted(seen):
            if vs.same_color:
                legend.append(LegendEntry(f"Cycle {c}", vs.color(c, "charge", analyzed)))
            else:
                for ph in phases:
                    legend.append(LegendEntry(f"Cycle {c} · {ph}", vs.color(c, ph, analyzed), vs.dash.get(ph, "solid")))
        return traces, legend, npts

    def _draw(self, force: bool = False) -> None:
        q = self.ctl.dq(self.ds_id) if self.ds_id else None
        if q is None or q.result is None:
            return
        u = self.ctl.units.units_for(self.ds_id)
        traces, legend, npts = self._traces(q)
        r = q.result
        kind_txt = {"smoothed": "smoothed", "raw": "raw", "both": "raw and smoothed"}[q.view.effective_plot(r.has_smoothed)]
        self.chart.set_labels(f"Voltage [{u.voltage}]", f"dQ/dV · {kind_txt} [{dqdv_unit(u.capacity)}]")
        self.chart.y_unit = dqdv_unit(u.capacity)
        if traces is None:
            self.chart.set_traces([])
            self.chart.show_message("Reading the dQ/dV curves…")
            return
        look = {k: v for k, v in q.view.__dict__.items() if k not in ("viewport", "selected")}
        key = (id(self.ctl.curves.get(self.ds_id)), repr(look), u)
        if key != self._drawn or force:
            self._drawn = key
            self.chart.set_traces(traces, legend if q.view.legend else [], keep_view=True)
        vp = q.view.viewport
        self.chart.set_range(vp.x, vp.y1)
        self.chart.show_legend(q.view.legend)
        self.chart.show_message("" if traces else "No curve for the cycles and phase shown.")
        n_cyc = len({t.key[0] for t in traces})
        n_cur = len({t.key[:2] for t in traces})
        self.foot.setText(f"{n_cyc:,} cycle{'s' if n_cyc != 1 else ''} · {n_cur:,} curve{'s' if n_cur != 1 else ''} · "
                          f"{npts:,} {points_note(r.params.method)}"
                          + (" · " if r.unavailable else ""))
        self.unav.setText(unavailable_text(r.unavailable))
        self.unav.setVisible(bool(r.unavailable))
        self.exp_btn.setEnabled(bool(traces) and not self.ctl.is_exporting(self.ds_id))
        self.t_save.setEnabled(bool(traces))

    # ---------------------------------------------------------------- voltage reconstruction
    def _sync_recon(self, q) -> None:
        vs, r = q.view, q.result
        self.rc_phase.set_value(vs.phase)
        self.rc_curves.set_value(vs.recon_curves)
        if not self.rc_shown.hasFocus():
            self.rc_shown.setText(vs.shown_text)
        self.rc_shown.setPlaceholderText(f"All analyzed ({cycles_label(r.params.cycles)})")
        set_prop(self.rc_shown, "error", bool(vs.shown_error))
        self.rc_chart.show_legend(vs.legend)

    def _recon_shown(self, q) -> list:
        items = self.ctl.recon.get(self.ds_id)
        if items is None:
            return None
        vs = q.view
        cyc = set(vs.visible_cycles(sorted(q.result.params.cycles)))
        return [x for x in items if x.cycle in cyc and x.phase in vs.visible_phases()]

    def _draw_recon(self, force: bool = False) -> None:
        q = self.ctl.dq(self.ds_id) if self.ds_id else None
        if q is None or q.result is None or not hasattr(self, "rc_chart"):
            return
        u = self.ctl.units.units_for(self.ds_id)
        self.rc_chart.set_labels(f"Capacity [{u.capacity}]", f"Voltage [{u.voltage}]")
        self.rc_chart.y_unit = u.voltage
        items = self._recon_shown(q)
        if items is None:
            self.rc_chart.set_traces([])
            self.rc_chart.show_message("Reading the voltage curves…")
            self.rc_model.set_rows([], u.voltage)
            return
        vs = q.view
        analyzed = sorted(q.result.params.cycles)
        traces, legend, seen = [], [], []
        kinds = ("measured", "reconstructed") if vs.recon_curves == "both" else (vs.recon_curves,)
        for x in items:
            color = vs.color(x.cycle, x.phase, analyzed)
            for kind in kinds:
                xs, ys = (x.measured_q, x.measured_v) if kind == "measured" else (x.rebuilt_q, x.rebuilt_v)
                if len(xs) < 1:
                    continue
                rebuilt = kind == "reconstructed"
                traces.append(Trace((x.cycle, x.phase, kind), [(xs, ys, 0)], color,
                                    vs.line_width * (1.0 if rebuilt else 1.4), 1.0 if rebuilt else 0.45,
                                    "dash" if rebuilt else "solid", None, f"Cycle {x.cycle} · {x.phase} · {kind}"))
            if x.cycle not in seen:
                seen.append(x.cycle)
        for c in sorted(seen):
            legend.append(LegendEntry(f"Cycle {c}", vs.color(c, "charge", analyzed)))
        if vs.recon_curves == "both":
            legend.append(LegendEntry("Measured (solid, faint)", self.theme.tokens.muted))
            legend.append(LegendEntry("Reconstructed (dashed)", self.theme.tokens.muted, "dash"))
        key = (id(self.ctl.recon.get(self.ds_id)), repr({k: v for k, v in vs.__dict__.items()
                                                          if k not in ("viewport", "recon_viewport", "selected")}), u)
        if key != self._rc_drawn or force:
            self._rc_drawn = key
            self.rc_chart.set_traces(traces, legend if vs.legend else [], keep_view=True)
        vp = vs.recon_viewport
        self.rc_chart.set_range(vp.x, vp.y1)
        self.rc_chart.show_legend(vs.legend)
        self.rc_chart.show_message("" if traces else "No reconstruction for the cycles and phase shown.")
        self.rc_model.set_rows(items, u.voltage)
        summary = recon_summary(items)
        self.rc_foot.setText(
            f"{len(items):,} curve{'s' if len(items) != 1 else ''} · solid: measured points the dQ/dV was calculated "
            "from · dashed: voltage rebuilt by integrating dQ/dV (gregorithm)" + (f" · {summary}" if summary else ""))
        self.rc_save.setEnabled(bool(traces))

    def _show_unavailable(self) -> None:
        q = self.ctl.dq(self.ds_id)
        if q is not None and q.result is not None:
            QMessageBox.information(self, "Curves not available", "\n".join(unavailable_lines(q.result.unavailable)))

    # ---------------------------------------------------------------- table
    def _fill_table(self, q) -> None:
        curves = self.ctl.curves.get(self.ds_id) or []
        u = self.ctl.units.units_for(self.ds_id)
        vs, r = q.view, q.result
        cyc = set(vs.visible_cycles(sorted(r.params.cycles)))
        rows = []
        for cv in curves:
            if cv.cycle not in cyc or cv.phase not in vs.visible_phases():
                continue
            for sg in cv.segments:
                for i in range(len(sg.v)):
                    rows.append((cv.cycle, cv.half_cycle, cv.phase, cv.anchor_source_row, sg.segment_index,
                                 float(sg.v[i]), float(sg.raw[i]), None if sg.smoothed is None else float(sg.smoothed[i])))
        self.model.set_rows(rows, [column_title(k, u) for k, _t, _q in TABLE_COLUMNS])
        self.d_note.setText(f"{len(rows):,} rows · {points_note(q.result.params.method)} · cycles "
                            f"{cycles_label(tuple(sorted(cyc)))} · {'both phases' if vs.phase == 'both' else vs.phase}")

    def _copy(self) -> None:
        from PySide6.QtWidgets import QApplication
        idx = sorted(self.table.selectionModel().selectedIndexes(), key=lambda i: (i.row(), i.column()))
        if not idx:
            return
        lines, cur, row = [], [], idx[0].row()
        for i in idx:
            if i.row() != row:
                lines.append("\t".join(cur))
                cur, row = [], i.row()
            v = self.model.rows[i.row()][i.column()]
            cur.append("" if v is None else (repr(v) if isinstance(v, float) else str(v)))   # full precision
        lines.append("\t".join(cur))
        QApplication.clipboard().setText("\n".join(lines))

    # ---------------------------------------------------------------- export / image
    def _export(self) -> None:
        q = self.ctl.dq(self.ds_id)
        if q is None or q.result is None:
            return
        u = self.ctl.units.units_for(self.ds_id)
        vs = q.view
        cyc = vs.visible_cycles(sorted(q.result.params.cycles))
        rows_text = (f"Rows: cycles {cycles_label(tuple(cyc))} · "
                     f"{'charge and discharge' if vs.phase == 'both' else vs.phase} (as shown above the chart).")
        dlg = DqdvExportDialog(self, [column_title(k, u) for k, _t, _q in TABLE_COLUMNS], rows_text)
        if not dlg.exec():
            return
        fmt, cols = dlg.choice()
        d = self.ctl.state.get(self.ds_id)
        ext = ".csv" if fmt == "csv" else ".parquet"
        path = save_export(self, "Export dQ/dV table", f"{d.name} - dqdv{ext}",
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
        """Save Image: the chart as shown, every derivative point of the shown curves is drawn (no sampling), PNG."""
        q = self.ctl.dq(self.ds_id) if self.ds_id else None
        if q is None or q.result is None or self.body.currentIndex() not in (2, 4):
            return
        recon = self.body.currentIndex() == 4
        d = self.ctl.state.get(self.ds_id)
        name = f"{d.name} - voltage reconstruction.png" if recon else f"{d.name} - dQdV.png"
        path = save_export(self, "Save Image", name, "PNG image (*.png)", folder=dataset_folder(self.ctl.state.get(self.ds_id)))
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        if (self.rc_chart if recon else self.chart).grab().save(path, "PNG"):
            self.window().statusBar().showMessage(f"Image saved: {path}", 6000)
        else:
            QMessageBox.warning(self, "Save Image", "Could not save the image.")
