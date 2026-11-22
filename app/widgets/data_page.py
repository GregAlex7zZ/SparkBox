"""Data section: empty state, import progress, imported dataset view."""
from __future__ import annotations
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
                               QProgressBar, QPushButton, QStackedWidget, QTableView, QVBoxLayout, QWidget)
from ..backend import Backend, ImportResult, Progress
from ..formatting import fmt_int, fmt_time, sig4
from ..icons import make_icon, pixmap
from ..theme import ThemeManager, set_prop
from .controls import StatusPill
from .wrap_header import install as install_wrap_header

BLOCK = 256
MAX_COPY_ROWS = 200_000            # largest selection copied to the clipboard
TIME_COL = 0                      # contract: display_columns[0] is always time


def _muted(text: str, small: bool = False) -> QLabel:
    lab = QLabel(text)
    lab.setProperty("muted", True)
    if small:
        lab.setProperty("small", True)
    return lab


# ------------------------------------------------------------------------------------------------
class SplitOpenButton(QWidget):
    """Single `Open File` menu button offering Auto and Manual Mapping."""

    def __init__(self, theme: ThemeManager, auto_action: QAction, manual_action: QAction):
        super().__init__()
        self.theme = theme
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.main = QPushButton("Open File")
        self.main.setObjectName("SplitMain")
        self.main.setCursor(Qt.CursorShape.PointingHandCursor)
        self.main.setToolTip("Choose Auto Mapping or Manual Mapping")
        self.main.setAccessibleName("Open file options")
        self.main.setIconSize(QSize(16, 16))
        self.menu = QMenu(self)
        self.menu.addAction(auto_action)
        self.menu.addAction(manual_action)
        lay.addWidget(self.main)
        self.main.clicked.connect(lambda: self.menu.exec(self.main.mapToGlobal(QPoint(0, self.main.height() + 6))))
        theme.changed.connect(self.apply_theme)
        self.apply_theme()

    def apply_theme(self) -> None:
        self.main.setIcon(make_icon("chevron-down", self.theme.tokens.on_pri, size=16))


class EmptyView(QWidget):
    fileDropped = Signal(str)

    def __init__(self, theme: ThemeManager, auto_action: QAction, manual_action: QAction):
        super().__init__()
        self.theme = theme
        self.setAcceptDrops(True)
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zone = QFrame()
        self.zone.setObjectName("DropZone")
        self.zone.setFixedWidth(560)
        z = QVBoxLayout(self.zone)
        z.setContentsMargins(24, 40, 24, 40)
        z.setSpacing(8)
        z.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("Open a Battery Dataset")
        title.setStyleSheet("font-size: 15pt; font-weight: 500;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub = _muted("Drag a file here, or choose how to open it.")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.split = SplitOpenButton(theme, auto_action, manual_action)
        fmts = _muted("CSV · TXT · Parquet", small=True)
        fmts.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for w in (self.icon, title, sub):
            z.addWidget(w)
        z.addSpacing(8)
        z.addWidget(self.split, 0, Qt.AlignmentFlag.AlignHCenter)
        z.addSpacing(6)
        z.addWidget(fmts)
        outer.addWidget(self.zone)
        theme.changed.connect(self.apply_theme)
        self.apply_theme()

    def apply_theme(self) -> None:
        self.icon.setPixmap(pixmap("file-up", self.theme.tokens.muted, 32))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            set_prop(self.zone, "drag", True)

    def dragLeaveEvent(self, e):
        set_prop(self.zone, "drag", False)

    def dropEvent(self, e):
        set_prop(self.zone, "drag", False)
        urls = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.fileDropped.emit(urls[0])


# ------------------------------------------------------------------------------------------------
class ImportingView(QWidget):
    """Shown for EVERY import. A fast import fills the bar at once. 100% only when truly complete."""
    cancelRequested = Signal()

    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 12)
        outer.setSpacing(12)
        card = QFrame()
        card.setProperty("panel", True)
        c = QVBoxLayout(card)
        c.setContentsMargins(18, 16, 18, 16)
        c.setSpacing(12)
        top = QHBoxLayout()
        self.title = QLabel("Importing")
        self.title.setStyleSheet("font-size: 10.5pt; font-weight: 500;")
        self.cancel = QPushButton("Cancel")
        self.cancel.setProperty("ghost", True)
        self.cancel.clicked.connect(self.cancelRequested)
        top.addWidget(self.title, 1)
        top.addWidget(self.cancel)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        self.bar.setAccessibleName("Import progress")
        row = QHBoxLayout()
        self.phase = _muted("", small=True)
        self.counts = _muted("", small=True)
        row.addWidget(self.phase, 1)
        row.addWidget(self.counts)
        note = _muted("Nothing is saved until the import completes.", small=True)
        for lay in (top,):
            c.addLayout(lay)
        c.addWidget(self.bar)
        c.addLayout(row)
        c.addWidget(note)
        outer.addWidget(card)
        ph = QLabel("The table appears when the import completes.")
        ph.setProperty("muted", True)
        ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(ph, 1)

    def begin(self, filename: str) -> None:
        self.title.setText(f"Importing {filename}")
        self.cancel.setEnabled(True)
        self.update_progress(Progress(0.0, "Starting"))

    def update_progress(self, p: Progress) -> None:
        if p.fraction is None:
            self.bar.setRange(0, 0)                      # indeterminate: total unknown, no invented percentage
            self.phase.setText(p.phase)
        else:
            self.bar.setRange(0, 100)
            self.bar.setValue(int(round(p.fraction * 100)))
            self.phase.setText(f"{p.phase} · {int(round(p.fraction * 100))}%" if p.phase else f"{int(round(p.fraction * 100))}%")
        self.counts.setText(f"{fmt_int(p.done)} of {fmt_int(p.total)} rows" if p.done is not None and p.total else "")


# ------------------------------------------------------------------------------------------------
class RowsModel(QAbstractTableModel):
    """Virtualised, read-only. Values come from backend.get_rows() already in display units."""

    def __init__(self, backend: Backend, ds_key: str, total: int, columns: list[tuple[str, str]]):
        super().__init__()
        self.backend, self.ds_key, self.total, self.columns = backend, ds_key, total, columns
        self._cache: dict[int, list[list[float]]] = {}
        self.highlight = -1

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.total

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 1 + len(self.columns)

    def _value(self, row: int, col: int) -> float:
        blk = row // BLOCK
        if blk not in self._cache:
            if len(self._cache) > 64:
                self._cache.pop(next(iter(self._cache)))
            self._cache[blk] = self.backend.get_rows(self.ds_key, blk * BLOCK, BLOCK)
        return self._cache[blk][row - blk * BLOCK][col]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() == 0:
                return fmt_int(index.row() + 1)
            v = self._value(index.row(), index.column() - 1)
            return fmt_time(v) if index.column() - 1 == TIME_COL else sig4(v)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if index.column() == 0:
                return int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            return int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if section == 0:
                return "Row"
            name, unit = self.columns[section - 1]
            return f"{name} [{unit}]"
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignLeft if section == 0 else Qt.AlignmentFlag.AlignRight))
        return None

    def full_precision(self, row: int, col: int) -> float:
        """Copy must use this (full precision), never the 4-digit display string."""
        return self._value(row, col - 1)


class CopyTableView(QTableView):
    """Ctrl+C copies selected rows as tab-separated text: header with units, FULL precision (repr), display units."""
    copyRefused = Signal(int)

    def keyPressEvent(self, e):
        if e.matches(QKeySequence.StandardKey.Copy):
            self.copy_selection()
            return
        super().keyPressEvent(e)

    def copy_selection(self) -> None:
        sel = self.selectionModel().selection()
        if sel.isEmpty():
            return
        if sum(r.bottom() - r.top() + 1 for r in sel) > MAX_COPY_ROWS:        # cheap guard, no row enumeration
            self.copyRefused.emit(MAX_COPY_ROWS)
            return
        rows = sorted({r for rng in sel for r in range(rng.top(), rng.bottom() + 1)})
        m = self.model()
        lines = ["\t".join(["Row"] + [f"{n} [{u}]" for n, u in m.columns])]
        for r in rows:
            lines.append("\t".join([str(r + 1)] + [repr(m.full_precision(r, c)) for c in range(1, m.columnCount())]))
        QGuiApplication.clipboard().setText("\n".join(lines))


class DatasetView(QWidget):
    remapRequested = Signal(str)
    goElaborationRequested = Signal()

    def __init__(self, ds_id: str, name: str, result: ImportResult, backend: Backend, theme: ThemeManager):
        super().__init__()
        self.ds_id, self.result, self.theme = ds_id, result, theme
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 12)
        lay.setSpacing(12)
        # summary row
        s = QHBoxLayout()
        s.setSpacing(8)
        summary_items = (f"<b style='font-weight:500'>{fmt_int(result.n_valid)}</b> valid rows",
                         f"<b style='font-weight:500'>{fmt_int(result.n_discarded)}</b> discarded",
                         f"Duration <b style='font-weight:500'>{fmt_time(result.duration_s / 3600)} h</b>",
                         result.method)
        for index, text in enumerate(summary_items):
            chip = QLabel(text)
            chip.setProperty("chip", True)
            if index == 1:
                self.discard_chip = chip
                chip.setToolTip(result.discard_summary or "No discarded-row diagnostics were reported.")
            elif index == 2:
                self.duration_chip = chip
            s.addWidget(chip)
        self.units_pill = StatusPill(self.theme)
        self.units_pill.hide()
        s.addWidget(self.units_pill)
        s.addStretch(1)
        self.report_btn = QPushButton("Import Report")
        self.report_btn.setProperty("ghost", True)
        self.report_btn.setCheckable(True)
        self.report_btn.setAccessibleName("Import report")
        self.report_btn.setIconSize(QSize(14, 14))
        remap = QPushButton("Re-map Columns…")
        remap.setProperty("ghost", True)
        remap.clicked.connect(lambda: self.remapRequested.emit(self.ds_id))
        s.addWidget(self.report_btn)
        s.addWidget(remap)
        self.go_elab = QPushButton("Go to Data Elaboration")
        self.go_elab.setProperty("goElab", True)
        self.go_elab.setCursor(Qt.CursorShape.PointingHandCursor)
        self.go_elab.setToolTip("Next step: detect cycles, rests and CC / CV steps")
        self.go_elab.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.go_elab.setIconSize(QSize(14, 14))
        self.go_elab.clicked.connect(self.goElaborationRequested)
        s.addWidget(self.go_elab)
        lay.addLayout(s)
        # report panel (hidden by default)
        self.report = QFrame()
        self.report.setProperty("panel", True)
        r = QVBoxLayout(self.report)
        r.setContentsMargins(0, 0, 0, 0)
        r.setSpacing(0)
        for i, row in enumerate([("Column in file", "Role", "Source unit / format", "Used")] +
                                [(x.column, x.role, x.unit, "Yes" if x.used else "No") for x in result.report]):
            line = QWidget()
            h = QHBoxLayout(line)
            h.setContentsMargins(12, 7, 12, 7)
            for j, txt in enumerate(row):
                lab = QLabel(txt)
                lab.setProperty("small", True)
                if i == 0:
                    lab.setProperty("muted", True)
                h.addWidget(lab, (13, 10, 15, 6)[j])
            r.addWidget(line)
        r.addWidget(_muted(f"  Discarded rows: {fmt_int(result.n_discarded)}. {result.discard_summary}", small=True))
        self.report.hide()
        lay.addWidget(self.report)
        self.report_btn.toggled.connect(self._toggle_report)
        # jump row
        j = QHBoxLayout()
        j.setSpacing(6)
        self.row_edit = QLineEdit()
        self.row_edit.setPlaceholderText(f"1 – {fmt_int(result.n_valid)}")
        self.row_edit.setFixedWidth(130)
        self.row_edit.setAccessibleName("Row")
        self.time_edit = QLineEdit()
        self.time_edit.setFixedWidth(100)
        self.time_edit.setAccessibleName("Time")
        go_row, go_time = QPushButton("Go"), QPushButton("Go")
        for b in (go_row, go_time):
            b.setProperty("go", True)
        j.addWidget(_muted("Row", small=True))
        j.addWidget(self.row_edit)
        j.addWidget(go_row)
        j.addSpacing(14)
        j.addWidget(_muted("Time", small=True))
        j.addWidget(self.time_edit)
        self.time_unit = _muted(result.display_columns[0][1] if result.display_columns else "h", small=True)
        j.addWidget(self.time_unit)
        j.addWidget(go_time)
        j.addSpacing(14)
        self.jump_msg = _muted("", small=True)
        j.addWidget(self.jump_msg, 1)
        lay.addLayout(j)
        go_row.clicked.connect(self._go_row)
        self.row_edit.returnPressed.connect(self._go_row)
        go_time.clicked.connect(self._go_time)
        self.time_edit.returnPressed.connect(self._go_time)
        # table
        self.model = RowsModel(backend, ds_id, result.n_valid, result.display_columns)
        self.backend = backend
        self.table = CopyTableView()
        self.table.copyRefused.connect(self._copy_refused)
        self.table.setModel(self.model)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(28)
        install_wrap_header(self.table).setSectionResizeMode(QHeaderView.ResizeMode.Stretch)   # long titles wrap
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 110)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        lay.addWidget(self.table, 1)
        # position row
        p = QHBoxLayout()
        self.pos = _muted("", small=True)
        p.addWidget(self.pos, 1)
        p.addWidget(_muted("4 significant digits shown, time unrounded · copy keeps full precision", small=True))
        lay.addLayout(p)
        self.table.verticalScrollBar().valueChanged.connect(self._update_pos)
        theme.changed.connect(self.apply_theme)
        self.apply_theme()
        self._update_pos()

    def set_units(self, columns: list[tuple[str, str]], time_unit: str, custom: bool) -> None:
        from ..units import TIME, scale
        self.duration_chip.setText(
            f"Duration <b style='font-weight:500'>{sig4(self.result.duration_s * scale(TIME, time_unit))} "
            f"{time_unit}</b>")
        if columns:
            self.model.beginResetModel()
            self.model.columns = columns
            self.model._cache.clear()
            self.model.endResetModel()
        self.time_unit.setText(time_unit)
        self.units_pill.setVisible(custom)
        if custom:
            self.units_pill.set("p", "ruler", "Custom units", "This dataset uses its own units (Settings › Units)")

    def apply_theme(self) -> None:
        self.report_btn.setIcon(make_icon("chevron-up" if self.report_btn.isChecked() else "chevron-down",
                                          self.theme.tokens.text, size=14))
        self.go_elab.setIcon(make_icon("arrow-right", self.theme.tokens.on_pri, size=14))

    def _copy_refused(self, limit: int) -> None:
        w = self.window()
        if hasattr(w, "statusBar"):
            w.statusBar().showMessage(f"Selection too large to copy (maximum {fmt_int(limit)} rows).", 6000)

    def _toggle_report(self, on: bool) -> None:
        self.report.setVisible(on)
        self.apply_theme()

    def _update_pos(self) -> None:
        top = max(0, self.table.rowAt(0))
        bottom = self.table.rowAt(self.table.viewport().height() - 1)
        bottom = self.model.total - 1 if bottom < 0 else bottom
        self.pos.setText(f"Rows {fmt_int(top + 1)}–{fmt_int(bottom + 1)} of {fmt_int(self.model.total)}")

    def _show_row(self, row: int) -> None:
        idx = self.model.index(row, 0)
        self.table.selectRow(row)
        self.table.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)
        self._update_pos()

    def _go_row(self) -> None:
        txt = self.row_edit.text().replace(",", "").replace(" ", "")
        try:
            n = int(txt)
        except ValueError:
            self.jump_msg.setText("Enter a whole row number.")
            return
        if not 1 <= n <= self.model.total:
            self.jump_msg.setText(f"Row must be between 1 and {fmt_int(self.model.total)}.")
            return
        self._show_row(n - 1)
        self.jump_msg.setText(f"Row {fmt_int(n)}")

    def _go_time(self) -> None:
        try:
            v = float(self.time_edit.text().strip().replace(",", "."))
        except ValueError:
            self.jump_msg.setText("Enter a number.")
            return
        row, exact, nearest = self.backend.find_row_by_time(self.ds_id, v)
        self._show_row(row)
        unit = self.time_unit.text()
        if exact:
            self.jump_msg.setText(f"Row {fmt_int(row + 1)} · exact match for {fmt_time(v)} {unit}")
        else:
            self.jump_msg.setText(f"Nearest row to {fmt_time(v)} {unit}: row {fmt_int(row + 1)} ({fmt_time(nearest)} {unit})")


# ------------------------------------------------------------------------------------------------
class DataPage(QStackedWidget):
    fileDropped = Signal(str)
    cancelRequested = Signal()
    remapRequested = Signal(str)
    goElaborationRequested = Signal()

    def __init__(self, theme: ThemeManager, auto_action: QAction, manual_action: QAction):
        super().__init__()
        self.theme = theme
        self.empty = EmptyView(theme, auto_action, manual_action)
        self.importing = ImportingView()
        self.views: dict[str, DatasetView] = {}
        self.addWidget(self.empty)
        self.addWidget(self.importing)
        self.empty.fileDropped.connect(self.fileDropped)
        self.importing.cancelRequested.connect(self.cancelRequested)

    def show_empty(self) -> None:
        self.setCurrentWidget(self.empty)

    def show_importing(self, filename: str) -> None:
        self.importing.begin(filename)
        self.setCurrentWidget(self.importing)

    def set_dataset_view(self, ds_id: str, name: str, result: ImportResult, backend: Backend) -> None:
        old = self.views.pop(ds_id, None)
        if old:
            self.removeWidget(old)
            old.deleteLater()
        v = DatasetView(ds_id, name, result, backend, self.theme)
        v.remapRequested.connect(self.remapRequested)
        v.goElaborationRequested.connect(self.goElaborationRequested)
        self.views[ds_id] = v
        self.addWidget(v)

    def show_dataset(self, ds_id: str | None) -> None:
        if ds_id and ds_id in self.views:
            self.setCurrentWidget(self.views[ds_id])
        else:
            self.show_empty()

    def set_units(self, ds_id: str, columns: list[tuple[str, str]], time_unit: str, custom: bool) -> None:
        if ds_id in self.views:
            self.views[ds_id].set_units(columns, time_unit, custom)

    def drop_dataset(self, ds_id: str) -> None:
        v = self.views.pop(ds_id, None)
        if v:
            self.removeWidget(v)
            v.deleteLater()
