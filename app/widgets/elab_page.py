"""Data Elaboration page. Pure view over ElabState: every state of the approved mockups maps to one widget
configuration, rebuilt from `refresh()`. No scientific values are computed here."""
from __future__ import annotations
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QProgressBar,
                               QPushButton, QRadioButton, QStackedWidget, QToolButton, QVBoxLayout, QWidget)
from ..backend import Backend
from ..file_dialogs import dataset_folder, save_export
from ..elab_controller import ElabController
from ..elaboration import (CYCLE_ORDER_LABELS, NUM_FIELDS, STATE_LABEL, ElabState, ElabView,
                           fmt_param, CYCLE_ORDER_POPOVER)
from ..formatting import fmt_int, fmt_time
from ..icons import make_icon, pixmap
from ..theme import ThemeManager, set_prop
from .controls import LabelRow, SampleBlock, SegmentedControl, StatusPill
from .elab_table import ElabModel, FrozenTable

PILL_ICON = {ElabView.NOT_PROCESSED: "circle-dashed", ElabView.INVALID: "circle-dashed", ElabView.CURRENT: "circle-check",
             ElabView.OUTDATED: "refresh-cw", ElabView.PROCESSING: "loader-circle", ElabView.CANCELLING: "loader-circle",
             ElabView.PUBLISHING: "loader-circle", ElabView.CANCELLED: "ban", ElabView.FAILED: "circle-x"}
PILL_COLOR = {"n": "muted", "p": "pri", "ok": "success", "warn": "warn", "err": "err"}
BANNER_ICON = {"warn": "circle-alert", "n": "ban", "err": "circle-x"}
RUNNING = (ElabView.PROCESSING, ElabView.CANCELLING, ElabView.PUBLISHING)
STOP_WAIT = "Cancelling… waiting for the current step to stop"


def _lab(text="", muted=False, size=None, weight=None, wrap=False, align=None) -> QLabel:
    l = QLabel(text)
    if muted:
        l.setProperty("muted", True)
    css = ""
    if size:
        css += f"font-size: {size * 0.75:g}pt;"
    if weight:
        css += f"font-weight: {weight};"
    if css:
        l.setStyleSheet(css)
    l.setWordWrap(wrap)
    if align:
        l.setAlignment(align)
    return l


def _btn(text: str, kind: str, h: int | None = None) -> QPushButton:
    b = QPushButton(text)
    b.setProperty(kind, True)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if h:
        b.setFixedHeight(h)
    return b


class Pill(QFrame):
    def __init__(self, theme: ThemeManager):
        super().__init__()
        self.theme = theme
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(6)
        self.icon, self.text = QLabel(), QLabel()
        lay.addWidget(self.icon)
        lay.addWidget(self.text)
        self._v = (ElabView.NOT_PROCESSED, "Not processed")

    def set_view(self, view: ElabView, text: str | None = None) -> None:
        label, kind = STATE_LABEL[view]
        self._v = (view, text or label)
        self.text.setText(self._v[1])
        set_prop(self, "pill", kind)
        col = getattr(self.theme.tokens, PILL_COLOR[kind])
        self.icon.setPixmap(pixmap(PILL_ICON[view], col, 14))
        self.setAccessibleName(f"State: {self._v[1]}")


class OrderBlock(QWidget):
    """Cycle order: label + (i) + 'Modified' tag, then a segmented control. Shown in the Ready and Summary states."""
    changed = Signal(str)

    def __init__(self, theme: ThemeManager):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 4)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.row = LabelRow(theme, "Cycle Order", CYCLE_ORDER_POPOVER, 12)
        self.row.setFixedWidth(300)
        self.seg = SegmentedControl([(k, v) for k, v in CYCLE_ORDER_LABELS.items()], aria="Cycle order")
        self.seg.valueChanged.connect(self.changed)
        lay.addWidget(self.row, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.seg, 0, Qt.AlignmentFlag.AlignHCenter)

    def show_value(self, order: str, modified: bool) -> None:
        self.seg.set_value(order)
        self.seg.set_changed(modified)
        self.row.set_modified(modified)


class ElabExportDialog(QDialog):
    """Export Data…: which elaboration table (elaborated points or detected segments) and the format. Every column
    of the Elaborated Data view is written at full precision in the units shown; the file name is chosen after
    Export and an existing file is never overwritten."""

    def __init__(self, parent, n_rows: int, n_segments: int):
        super().__init__(parent)
        self.setWindowTitle("Export Elaborated Data")
        self.setMinimumWidth(440)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)
        v.addWidget(_lab("Export elaborated data", size=18, weight=500))
        t = QHBoxLayout()
        t.addWidget(_lab("Table"))
        self.points = QRadioButton(f"Elaborated points ({fmt_int(n_rows)} rows)")
        self.segments = QRadioButton(f"Segments ({fmt_int(n_segments)})")
        self.points.setChecked(True)
        t.addWidget(self.points)
        t.addWidget(self.segments)
        t.addStretch(1)
        v.addLayout(t)
        f = QHBoxLayout()
        f.addWidget(_lab("Format"))
        self.csv, self.parquet = QRadioButton("CSV"), QRadioButton("Parquet")
        self.csv.setChecked(True)
        group = QWidget()                                    # separate exclusive group from the table choice
        g = QHBoxLayout(group)
        g.setContentsMargins(0, 0, 0, 0)
        g.addWidget(self.csv)
        g.addWidget(self.parquet)
        f.addWidget(group)
        f.addStretch(1)
        v.addLayout(f)
        v.addWidget(_lab("Every column of the Elaborated Data view is written at full precision in the units shown "
                         "(Settings › Units). Empty cells stay empty.", muted=True, size=12, wrap=True))
        row = QHBoxLayout()
        row.addStretch(1)
        cancel, ok = _btn("Cancel", "ghost"), _btn("Export…", "goElab")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        v.addLayout(row)

    def choice(self) -> tuple[str, str]:
        """(table kind 'points' | 'segments', format 'csv' | 'parquet')."""
        return ("segments" if self.segments.isChecked() else "points"), ("parquet" if self.parquet.isChecked() else "csv")


class ElabPage(QWidget):
    openSettingsRequested = Signal()
    openExplorerRequested = Signal()             # "Go to Data Explorer": the main window switches section

    def __init__(self, theme: ThemeManager, ctl: ElabController, backend: Backend):
        super().__init__()
        self.theme, self.ctl, self.backend = theme, ctl, backend
        self.ds_id: str | None = None
        self._mode: dict[str, str] = {}          # ds_id -> "summary" | "table"
        self._tab = "points"
        self._details = False
        self._table: FrozenTable | None = None
        self._table_key: tuple | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        self.banner = self._build_banner()
        root.addWidget(self.banner)
        self.body = QStackedWidget()
        self.body.addWidget(self._build_ready())      # 0
        self.body.addWidget(self._build_summary())    # 1
        self.body.addWidget(self._build_running())    # 2
        self.body.addWidget(self._build_table())      # 3
        root.addWidget(self.body, 1)
        ctl.changed.connect(self._on_changed)
        ctl.resultReplaced.connect(self._on_result_replaced)
        ctl.exportState.connect(self._export_state)
        theme.changed.connect(self.refresh)

    # ---- construction -------------------------------------------------------------------------------
    def _build_header(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(18, 16, 18, 12)
        h.setSpacing(12)
        self.back = _btn("Back to Summary", "ghost", 30)
        self.back.clicked.connect(lambda: self._set_mode("summary"))
        self.title = _lab("Data Elaboration", size=16, weight=500)
        self.subtitle = _lab("", muted=True, size=12)
        self.tabs = SegmentedControl([("points", "Points"), ("segments", "Segments")], small=True, aria="Table")
        self.tabs.valueChanged.connect(self._set_tab)
        self.pill = Pill(self.theme)
        h.addWidget(self.back)
        h.addWidget(self.title)
        h.addWidget(self.subtitle)
        h.addWidget(self.tabs)
        self.units_pill = StatusPill(self.theme)
        self.units_pill.hide()
        h.addWidget(self.units_pill)
        h.addStretch(1)
        h.addWidget(self.pill)
        return w

    def _build_banner(self) -> QWidget:
        outer = QWidget()
        ol = QVBoxLayout(outer)
        ol.setContentsMargins(18, 0, 18, 8)
        self.banner_frame = QFrame()
        bl = QHBoxLayout(self.banner_frame)
        bl.setContentsMargins(14, 10, 14, 10)
        bl.setSpacing(12)
        self.banner_icon = QLabel()
        self.banner_icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.banner_title = _lab(weight=500)
        self.banner_text = _lab(wrap=True)
        self.banner_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        col.addWidget(self.banner_title)
        col.addWidget(self.banner_text)
        bl.addWidget(self.banner_icon, 0, Qt.AlignmentFlag.AlignTop)
        bl.addLayout(col, 1)
        ol.addWidget(self.banner_frame)
        outer.hide()
        return outer

    def _build_ready(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(8)
        v.setContentsMargins(24, 0, 24, 40)
        v.addWidget(_lab("Ready to Process", size=20, weight=500, align=Qt.AlignmentFlag.AlignHCenter))
        v.addWidget(_lab("Cycles, rests and CC / CV steps are detected using the parameters in Elaboration Settings.",
                         muted=True, wrap=True, align=Qt.AlignmentFlag.AlignHCenter))
        row = QHBoxLayout()
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.setSpacing(0)
        self.defaults_line = _lab("Using your default parameters · ", muted=True, size=12)
        review = _btn("Review", "link")
        review.clicked.connect(self.openSettingsRequested)
        row.addWidget(self.defaults_line)
        row.addWidget(review)
        v.addLayout(row)
        self.order_ready = OrderBlock(self.theme)
        self.order_ready.changed.connect(self._set_order)
        v.addWidget(self.order_ready)
        self.sample_ready = SampleBlock(self.theme)
        self.sample_ready.textEdited.connect(lambda k, t: self.ctl.set_sample_text(self.ds_id, k, t))
        v.addWidget(self.sample_ready)
        self.process_btn = _btn("Process", "primary")
        self.process_btn.setProperty("run", True)                     # amber: starts a calculation
        self.process_btn.clicked.connect(lambda: self.ctl.process(self.ds_id))
        v.addWidget(self.process_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        return w

    def _build_summary(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(6)
        v.setContentsMargins(24, 0, 24, 30)
        self.cycles_label = _lab("Cycles", muted=True, size=12, align=Qt.AlignmentFlag.AlignHCenter)
        self.cycles = _lab("0", align=Qt.AlignmentFlag.AlignHCenter)
        self.cycles.setObjectName("BigNumber")
        v.addWidget(self.cycles_label)
        v.addWidget(self.cycles)
        line = QHBoxLayout()
        line.setAlignment(Qt.AlignmentFlag.AlignCenter)
        line.setSpacing(4)
        self.half_lbl = _lab(muted=True)
        self.seg_link = _btn("", "quiet")
        self.seg_link.setToolTip("View detected segments")
        self.seg_link.clicked.connect(lambda: self._open_table("segments"))
        self.rows_lbl = _lab(muted=True)
        for x in (self.half_lbl, _lab("·", muted=True), self.seg_link, _lab("·", muted=True), self.rows_lbl):
            line.addWidget(x)
        v.addLayout(line)
        self.order_sum = OrderBlock(self.theme)
        self.order_sum.changed.connect(self._set_order)
        v.addWidget(self.order_sum)
        self.sample_sum = SampleBlock(self.theme)
        self.sample_sum.textEdited.connect(lambda k, t: self.ctl.set_sample_text(self.ds_id, k, t))
        v.addWidget(self.sample_sum)
        btns = QHBoxLayout()
        btns.setAlignment(Qt.AlignmentFlag.AlignCenter)
        btns.setSpacing(10)
        self.btn_a, self.btn_b = _btn("", "primary"), _btn("", "secondary")
        self._fn_a = self._fn_b = lambda: None
        self.btn_a.clicked.connect(lambda: self._fn_a())
        self.btn_b.clicked.connect(lambda: self._fn_b())
        btns.addWidget(self.btn_a)
        btns.addWidget(self.btn_b)
        v.addLayout(btns)
        # Secondary links: the elaborated table is an extra, kept as discreet as Processing Details.
        links = QHBoxLayout()
        links.setAlignment(Qt.AlignmentFlag.AlignCenter)
        links.setSpacing(4)
        self.view_link = _btn("View Elaborated Data", "quiet")
        self.view_link.clicked.connect(lambda: self._open_table("points"))
        self.details_btn = _btn("Processing Details", "quiet")
        self.details_btn.setCheckable(True)
        self.details_btn.setIconSize(QSize(14, 14))
        self.details_btn.toggled.connect(self._toggle_details)
        self.export_link = _btn("Export Data…", "quiet")
        self.export_link.setToolTip("Save the elaborated points or segments as CSV or Parquet")
        self.export_link.clicked.connect(self._export)
        links.addWidget(self.view_link)
        links.addWidget(_lab("·", muted=True))
        links.addWidget(self.export_link)
        links.addWidget(_lab("·", muted=True))
        links.addWidget(self.details_btn)
        v.addLayout(links)
        self.export_state = _lab(muted=True, size=12, align=Qt.AlignmentFlag.AlignHCenter)
        self.export_state.hide()
        v.addWidget(self.export_state)
        self.details_box = QFrame()
        self.details_box.setProperty("panel", True)
        self.details_grid = QGridLayout(self.details_box)
        self.details_grid.setContentsMargins(16, 12, 16, 12)
        self.details_grid.setHorizontalSpacing(24)
        self.details_grid.setVerticalSpacing(6)
        self.details_box.hide()
        v.addWidget(self.details_box, 0, Qt.AlignmentFlag.AlignHCenter)
        return w

    def _build_running(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setContentsMargins(18, 0, 18, 24)
        v.setSpacing(14)
        card = QFrame()
        card.setProperty("panel", True)
        card.setFixedWidth(640)
        c = QVBoxLayout(card)
        c.setContentsMargins(18, 16, 18, 16)
        c.setSpacing(12)
        top = QHBoxLayout()
        self.run_title = _lab(weight=500, size=14)
        self.cancel_btn = _btn("Cancel", "ghost")
        self.cancel_btn.clicked.connect(lambda: self.ctl.cancel(self.ds_id))
        top.addWidget(self.run_title, 1)
        top.addWidget(self.cancel_btn)
        c.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        self.bar.setAccessibleName("Processing progress")
        c.addWidget(self.bar)
        ph = QHBoxLayout()
        self.phase = _lab(muted=True, size=12)
        self.pct = _lab(muted=True, size=12)
        ph.addWidget(self.phase, 1)
        ph.addWidget(self.pct)
        c.addLayout(ph)
        c.addWidget(_lab("Nothing is saved until processing completes. The percentage shows work done, not time remaining.",
                         muted=True, size=12, wrap=True))
        v.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
        self.prev_card = QFrame()
        self.prev_card.setProperty("panel", True)
        self.prev_card.setFixedWidth(640)
        p = QHBoxLayout(self.prev_card)
        p.setContentsMargins(18, 12, 18, 12)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(_lab("Previous Result", weight=500))
        self.prev_text = _lab(muted=True, size=12)
        col.addWidget(self.prev_text)
        self.prev_view = _btn("View Elaborated Data", "ghost")
        self.prev_view.clicked.connect(lambda: self._open_table("points"))
        p.addLayout(col, 1)
        p.addWidget(self.prev_view)
        v.addWidget(self.prev_card, 0, Qt.AlignmentFlag.AlignHCenter)
        return w

    def _build_table(self) -> QWidget:
        w = QWidget()
        self.table_lay = QVBoxLayout(w)
        self.table_lay.setContentsMargins(18, 0, 18, 12)
        self.table_lay.setSpacing(12)
        self.jump = QWidget()
        j = QHBoxLayout(self.jump)
        j.setContentsMargins(0, 0, 0, 0)
        j.setSpacing(6)
        self.row_edit, self.time_edit = QLineEdit(), QLineEdit()
        self.row_edit.setFixedWidth(130)
        self.row_edit.setAccessibleName("Row")
        self.time_edit.setFixedWidth(100)
        self.time_edit.setAccessibleName("Time")
        go_r, go_t = _btn("Go", "go"), _btn("Go", "go")
        self.time_unit = _lab(muted=True, size=12)
        self.jump_msg = _lab(muted=True, size=12)
        for x in (_lab("Row", muted=True, size=12), self.row_edit, go_r):
            j.addWidget(x)
        j.addSpacing(14)
        for x in (_lab("Time", muted=True, size=12), self.time_edit, self.time_unit, go_t):
            j.addWidget(x)
        j.addSpacing(14)
        j.addWidget(self.jump_msg, 1)
        go_r.clicked.connect(self._go_row)
        self.row_edit.returnPressed.connect(self._go_row)
        go_t.clicked.connect(self._go_time)
        self.time_edit.returnPressed.connect(self._go_time)
        self.table_lay.addWidget(self.jump)
        self.table_slot = QVBoxLayout()
        self.table_lay.addLayout(self.table_slot, 1)
        foot = QHBoxLayout()
        self.pos = _lab(muted=True, size=12)
        self.note = _lab(muted=True, size=12)
        foot.addWidget(self.pos, 1)
        foot.addWidget(self.note)
        self.table_lay.addLayout(foot)
        return w

    # ---- state -> widgets ----------------------------------------------------------------------------
    def show_dataset(self, ds_id: str | None) -> None:
        self.ds_id = ds_id
        self._table_key = None
        self.refresh()

    def _on_changed(self, ds_id: str) -> None:
        if ds_id == self.ds_id:
            self.refresh()

    def _on_result_replaced(self, ds_id: str) -> None:
        if ds_id == self.ds_id:
            self._table_key = None
            self._mode[ds_id] = "summary"
            self.refresh()

    def units_changed(self, ds_id: str, custom: bool) -> None:
        if ds_id == self.ds_id:
            self._table_key = None
            self.set_custom_units(custom)
            self.refresh()

    def set_custom_units(self, custom: bool) -> None:
        self.units_pill.setVisible(custom)
        if custom:
            self.units_pill.set("p", "ruler", "Custom units",
                                "This dataset uses its own units (Settings › Units)")

    def _state(self) -> ElabState | None:
        return self.ctl.elab(self.ds_id) if self.ds_id else None

    def _set_order(self, order: str) -> None:
        self.ctl.set_order(self.ds_id, order)

    def _set_mode(self, mode: str) -> None:
        self._mode[self.ds_id] = mode
        self.refresh()

    def _set_tab(self, tab: str) -> None:
        self._tab = tab
        self.refresh()

    def _open_table(self, tab: str) -> None:
        self._tab = tab
        self._set_mode("table")

    def _toggle_details(self, on: bool) -> None:
        self._details = on
        self.details_box.setVisible(on)
        self._icons()

    def _icons(self) -> None:
        self.details_btn.setIcon(make_icon("chevron-up" if self.details_btn.isChecked() else "chevron-down",
                                           self.theme.tokens.muted, size=14))
        self.back.setIcon(make_icon("chevron-left", self.theme.tokens.text, size=16))

    def refresh(self) -> None:
        e = self._state()
        if e is None:
            return
        ds = self.ctl.state.get(self.ds_id)
        view = e.view()
        defaults = self.ctl.defaults.params
        mods = e.modified(defaults)
        running = view in RUNNING
        has = e.has_result
        mode = self._mode.get(self.ds_id, "summary")
        in_table = mode == "table" and has
        total = e.result.n_rows if has else (e.total or 0)
        self.subtitle.setText(f"{ds.name} · {fmt_int(total)} rows" if total else ds.name)
        self.back.setVisible(in_table)
        self.tabs.setVisible(in_table)
        self.tabs.set_value(self._tab)
        self.title.setText("Elaborated Data" if in_table else "Data Elaboration")
        self.pill.set_view(view)
        self._icons()
        self._banner(e, view, has, in_table, running)
        blocker = self.ctl.start_blocker(self.ds_id)
        # ---- body selection
        if in_table:
            self.body.setCurrentIndex(3)
            self._fill_table(e)
        elif running:
            self.body.setCurrentIndex(2)
            self._fill_running(e, ds.name, view, has)
        elif has:
            self.body.setCurrentIndex(1)
            self._fill_summary(e, view, mods, blocker)
        else:
            self.body.setCurrentIndex(0)
            self._fill_ready(e, view, mods, blocker)

    def _banner(self, e: ElabState, view: ElabView, has: bool, in_table: bool, running: bool) -> None:
        prev = " The previous result is still available, based on previous settings." if has else ""
        spec = None
        if view is ElabView.OUTDATED:
            spec = ("warn", "Based on previous settings",
                    "Parameters changed since this result was calculated. Reprocess to apply them.")
        elif view is ElabView.CANCELLED:
            spec = ("n", "Processing cancelled", "Nothing was saved." + prev)
        elif view is ElabView.FAILED:
            spec = ("err", "Processing failed", f"{e.message}\nNothing was saved." + prev)
        if spec is None or in_table or running:
            self.banner.hide()
            return
        kind, title, text = spec
        set_prop(self.banner_frame, "banner", kind)
        col = getattr(self.theme.tokens, {"warn": "warn", "n": "muted", "err": "err"}[kind])
        self.banner_icon.setPixmap(pixmap(BANNER_ICON[kind], col, 18))
        self.banner_title.setText(title)
        self.banner_text.setText(text)
        self.banner.show()

    def _fill_ready(self, e: ElabState, view: ElabView, mods: set, blocker: str) -> None:
        invalid = view is ElabView.INVALID
        self.defaults_line.setText("A parameter is not valid. Fix it in Elaboration Settings to continue. " if invalid
                                   else "Using your default parameters · ")
        set_prop(self.defaults_line, "hintErr", invalid)
        self.order_ready.show_value(e.params.cycle_order, "cycle_order" in mods)
        self.sample_ready.show_values(e.sample, e.sample_texts, e.sample_errors)
        self.process_btn.setText("Process")
        self.process_btn.setEnabled(not blocker)
        self.process_btn.setToolTip(blocker or "Process")

    def _fill_summary(self, e: ElabState, view: ElabView, mods: set, blocker: str) -> None:
        r = e.result
        stale = view in (ElabView.OUTDATED, ElabView.CANCELLED, ElabView.FAILED)
        self.cycles_label.setText("Cycles · previous result" if stale else "Cycles")
        self.export_link.setEnabled(not self.ctl.is_exporting(self.ds_id))
        self.export_link.setToolTip("Save the elaborated points or segments as CSV or Parquet" if view is ElabView.CURRENT else
                                    "Save the previous result (based on previous settings) as CSV or Parquet")
        self.cycles.setText(fmt_int(r.n_cycles))
        self.half_lbl.setText(f"Half-Cycles <b>{fmt_int(r.n_half_cycles)}</b>")
        self.seg_link.setText(f"Segments {fmt_int(r.n_segments)} ›")
        self.rows_lbl.setText(f"Rows Processed <b>{fmt_int(r.n_rows)}</b>")
        self.order_sum.show_value(e.params.cycle_order, "cycle_order" in mods)
        self.sample_sum.show_values(e.sample, e.sample_texts, e.sample_errors)
        current = view is ElabView.CURRENT
        explorer_fn = self.openExplorerRequested.emit
        proc_fn = lambda: self.ctl.process(self.ds_id)
        # Main next step: Data Explorer. The elaborated table stays available as a discreet link (view_link).
        if current:
            self._fn_a, self._fn_b = explorer_fn, proc_fn
            self._set_btn(self.btn_a, "Go to Data Explorer", "primary", True, "Plot the elaborated data")
            self._set_btn(self.btn_b, "Reprocess", "secondary", not blocker,
                          blocker or "Run the elaboration again with the same parameters. Replaces the result when it completes.")
            self.view_link.setToolTip("Browse the elaborated table row by row")
        else:
            self._fn_a, self._fn_b = proc_fn, explorer_fn
            self._set_btn(self.btn_a, "Reprocess", "primary", not blocker, blocker or "Reprocess")
            self._set_btn(self.btn_b, "Go to Data Explorer", "secondary", True,
                          "Plot the previous result (based on previous settings)")
            self.view_link.setToolTip("View the previous result (based on previous settings)")
        self._fill_details(r)

    def _set_btn(self, b: QPushButton, text, kind, enabled, tip) -> None:
        b.setText(text)
        b.setProperty("primary", kind == "primary")
        b.setProperty("secondary", kind == "secondary")
        b.setProperty("run", kind == "primary" and text == "Reprocess")   # amber: starts a calculation
        b.style().unpolish(b)
        b.style().polish(b)
        b.setEnabled(enabled)
        b.setToolTip(tip)

    def _fill_details(self, r) -> None:
        while self.details_grid.count():
            it = self.details_grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        p = r.params
        rows = [("Cycle Order", CYCLE_ORDER_LABELS[p.cycle_order])] + \
               [(f.label, f"{fmt_param(getattr(p, f.key))} {f.unit}") for f in NUM_FIELDS] + list(r.details)
        for i, (k, v) in enumerate(rows):
            self.details_grid.addWidget(_lab(k, muted=True, size=12), i, 0)
            self.details_grid.addWidget(_lab(v, size=12), i, 1)
        i = len(rows)
        rid = r.result_id
        short = rid if len(rid) <= 12 else f"{rid[:6]}…{rid[-4:]}"
        self.details_grid.addWidget(_lab("Result ID", muted=True, size=12), i, 0)
        cell = QHBoxLayout()
        cell.setSpacing(4)
        cell.addWidget(_lab(short, size=12))
        cp = QToolButton()
        cp.setProperty("info", True)
        cp.setIcon(make_icon("copy", self.theme.tokens.muted, size=14))
        cp.setToolTip("Copy result ID")
        cp.setAccessibleName("Copy result ID")
        cp.clicked.connect(lambda: QGuiApplication.clipboard().setText(rid))
        cell.addWidget(cp)
        cell.addStretch(1)
        host = QWidget()
        host.setLayout(cell)
        self.details_grid.addWidget(host, i, 1)
        self.details_box.setVisible(self._details)
        self.details_btn.setChecked(self._details)

    def _fill_running(self, e: ElabState, name: str, view: ElabView, has: bool) -> None:
        self.run_title.setText(f"Processing {name}")
        cancelling = view is ElabView.CANCELLING
        self.cancel_btn.setText("Cancelling…" if cancelling else "Cancel")
        self.cancel_btn.setEnabled(not cancelling)
        pct = 100 if view is ElabView.PUBLISHING else int(round((e.progress or 0.0) * 100))
        indeterminate = e.progress is None and view is not ElabView.PUBLISHING
        self.bar.setRange(0, 0 if indeterminate else 100)
        self.bar.setValue(pct)
        self.phase.setText(STOP_WAIT if cancelling else ("Saving result…" if view is ElabView.PUBLISHING
                                                         else (e.phase or "Processing…")))
        self.pct.setText("" if indeterminate else f"{pct}%")
        self.prev_card.setVisible(has)
        if has:
            r = e.result
            self.prev_text.setText(f"Based on previous settings · {fmt_int(r.n_cycles)} cycles · {fmt_int(r.n_segments)} segments")

    # ---- export ----------------------------------------------------------------------------------------
    def _export(self) -> None:
        """Export Data…: choose table and format, then the file; the export runs on a worker thread."""
        e = self._state()
        if e is None or e.result is None or self.ctl.is_exporting(self.ds_id):
            return
        dlg = ElabExportDialog(self, e.result.n_rows, e.result.n_segments)
        if not dlg.exec():
            return
        kind, fmt = dlg.choice()
        name = self.ctl.state.get(self.ds_id).name
        ext = ".csv" if fmt == "csv" else ".parquet"
        suffix = "elaborated" if kind == "points" else "segments"
        path = save_export(self, "Export elaborated data", f"{name} - {suffix}{ext}",
                           "CSV (*.csv)" if fmt == "csv" else "Parquet (*.parquet)", folder=dataset_folder(self.ctl.state.get(self.ds_id)))
        if path:
            if not path.lower().endswith(ext):
                path += ext
            self.ctl.export(self.ds_id, kind, fmt, path)

    def _export_state(self, ds_id: str, msg: str) -> None:
        if ds_id != self.ds_id:
            return
        self.export_state.setText(msg)
        self.export_state.setVisible(bool(msg))
        self.export_link.setEnabled(not msg)

    # ---- table -----------------------------------------------------------------------------------------
    def _fill_table(self, e: ElabState) -> None:
        rid, tab = e.result.result_id, self._tab
        key = (self.ds_id, rid, tab)
        if key == self._table_key:
            return
        self._table_key = key
        if self._table is not None:
            self.table_slot.removeWidget(self._table)
            self._table.deleteLater()
            self._table = None
        self.jump.setVisible(tab == "points")
        self.jump_msg.setText("")
        try:
            info = self.backend.elab_table(rid, tab)
        except Exception as ex:                                      # shown, never swallowed
            self.pos.setText(f"Could not open the table: {ex}")
            return
        model = ElabModel(self.backend, rid, tab, info)
        self._table = FrozenTable(model)
        self._table.copyRefused.connect(self._copy_refused)
        self._table.right.verticalScrollBar().valueChanged.connect(self._update_pos)
        self.table_slot.addWidget(self._table)
        self.time_unit.setText(info.columns[0].unit or "h")
        self.row_edit.setPlaceholderText(f"1 – {fmt_int(info.total)}")
        self.note.setText("4 significant digits shown, time unrounded · copy keeps full precision" if tab == "points"
                          else "Time unrounded · copy keeps full precision")
        self._update_pos()

    def _model(self) -> ElabModel | None:
        return self._table.model_ if self._table else None

    def _update_pos(self) -> None:
        m = self._model()
        if m is None:
            return
        top = self._table.first_visible_row()
        bottom = min(m.table.total, top + self._table.visible_rows())
        unit = "Rows" if self._tab == "points" else "Segments"
        extra = (" · Row is 1-based, Source Row is 0-based" if self._tab == "points"
                 else " · values shown as stored by the library")
        self.pos.setText(f"{unit} {fmt_int(top + 1)}–{fmt_int(bottom)} of {fmt_int(m.table.total)}{extra}")

    def _copy_refused(self, limit: int) -> None:
        w = self.window()
        if hasattr(w, "statusBar"):
            w.statusBar().showMessage(f"Selection too large to copy (maximum {fmt_int(limit)} rows).", 6000)

    def _go_row(self) -> None:
        m = self._model()
        if m is None:
            return
        try:
            n = int(self.row_edit.text().replace(",", "").replace(" ", ""))
        except ValueError:
            self.jump_msg.setText("Enter a whole row number.")
            return
        if not 1 <= n <= m.table.total:
            self.jump_msg.setText(f"Row must be between 1 and {fmt_int(m.table.total)}.")
            return
        self._table.show_row(n - 1)
        self.jump_msg.setText(f"Row {fmt_int(n)}")
        self._update_pos()

    def _go_time(self) -> None:
        m = self._model()
        if m is None:
            return
        try:
            v = float(self.time_edit.text().strip().replace(",", "."))
        except ValueError:
            self.jump_msg.setText("Enter a number.")
            return
        row, exact, nearest = self.backend.find_elab_row_by_time(m.result_id, v)
        self._table.show_row(row)
        u = self.time_unit.text()
        self.jump_msg.setText(f"Row {fmt_int(row + 1)} · exact match for {fmt_time(v)} {u}" if exact else
                              f"Nearest row to {fmt_time(v)} {u}: row {fmt_int(row + 1)} ({fmt_time(nearest)} {u})")
        self._update_pos()

