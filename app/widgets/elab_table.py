"""Elaborated data table: virtualised model + two synchronised views so the first two columns (Row/Segment ID and Time)
stay frozen while the rest scrolls horizontally. Display rules as in the Data section:
4 significant digits except time columns; copy uses full precision and the displayed units."""
from __future__ import annotations
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QTableView)
from ..backend import Backend, ElabTable
from ..formatting import fmt_int, fmt_time, sig4
from .wrap_header import fit_columns, install as install_wrap_header, link as link_headers

BLOCK = 256
MAX_COPY_ROWS = 200_000
ROW_H = 28
FROZEN_W = (90, 120)
COL_W = 148
NULL = "—"


def display_value(kind: str, is_time: bool, v) -> str:
    """Pure formatting rule (unit-tested). None -> em dash; bool -> Yes/No; floats 4 significant digits except time."""
    if v is None:
        return NULL
    if kind == "bool":
        return "Yes" if v else "No"
    if kind == "text":
        return str(v)
    if kind == "int":
        return str(int(v))
    return fmt_time(v) if is_time else sig4(v)


def copy_value(kind: str, v) -> str:
    if v is None:
        return ""
    if kind == "bool":
        return "Yes" if v else "No"
    if kind in ("text", "int"):
        return str(v)
    return repr(float(v))


class ElabModel(QAbstractTableModel):
    def __init__(self, backend: Backend, result_id: str, kind: str, table: ElabTable):
        super().__init__()
        self.backend, self.result_id, self.kind, self.table = backend, result_id, kind, table
        self._cache: dict[int, list[list]] = {}
        self._off = 1 if table.row_number_column else 0

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.table.total

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self._off + len(self.table.columns)

    def _row(self, r: int) -> list:
        blk = r // BLOCK
        if blk not in self._cache:
            if len(self._cache) > 64:
                self._cache.pop(next(iter(self._cache)))
            self._cache[blk] = self.backend.get_elab_rows(self.result_id, self.kind, blk * BLOCK, BLOCK)
        return self._cache[blk][r - blk * BLOCK]

    def header_text(self, c: int) -> str:
        if self._off and c == 0:
            return "Row"
        col = self.table.columns[c - self._off]
        return f"{col.name} [{col.unit}]" if col.unit else col.name

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        c = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if self._off and c == 0:
                return fmt_int(index.row() + 1)
            col = self.table.columns[c - self._off]
            return display_value(col.kind, col.is_time, self._row(index.row())[c - self._off])
        if role == Qt.ItemDataRole.TextAlignmentRole:
            left = (self._off and c == 0) or (not self._off and c == 0)
            return int(Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignLeft if left else Qt.AlignmentFlag.AlignRight))
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return self.header_text(section)
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return int(Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignLeft if section == 0 else Qt.AlignmentFlag.AlignRight))
        return None

    def copy_value(self, r: int, c: int) -> str:
        if self._off and c == 0:
            return str(r + 1)
        col = self.table.columns[c - self._off]
        return copy_value(col.kind, self._row(r)[c - self._off])


class ElabCopyView(QTableView):
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
        if sum(r.bottom() - r.top() + 1 for r in sel) > MAX_COPY_ROWS:
            self.copyRefused.emit(MAX_COPY_ROWS)
            return
        rows = sorted({r for rng in sel for r in range(rng.top(), rng.bottom() + 1)})
        m: ElabModel = self.model()
        cols = range(m.columnCount())
        lines = ["\t".join(m.header_text(c) for c in cols)]
        lines += ["\t".join(m.copy_value(r, c) for c in cols) for r in rows]
        QGuiApplication.clipboard().setText("\n".join(lines))


def _prepare(v: ElabCopyView) -> None:
    v.setFrameShape(QFrame.Shape.NoFrame)
    v.setShowGrid(False)
    v.setAlternatingRowColors(False)
    v.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    v.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    v.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    v.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    v.verticalHeader().hide()
    v.verticalHeader().setDefaultSectionSize(ROW_H)
    install_wrap_header(v).setSectionResizeMode(QHeaderView.ResizeMode.Fixed)    # titles wrap, columns fit the values


class FrozenTable(QFrame):
    """Left view = frozen columns (no scrollbars), right view = the rest; one shared selection model and a synchronised
    vertical scrollbar. Only the right view shows scrollbars."""
    copyRefused = Signal(int)

    def __init__(self, model: ElabModel, n_frozen: int = 2):
        super().__init__()
        self.setProperty("panel", True)
        self.model_ = model
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.left, self.right = ElabCopyView(), ElabCopyView()
        for v in (self.left, self.right):
            v.setModel(model)
            _prepare(v)
            v.copyRefused.connect(self.copyRefused)
        self.right.setSelectionModel(self.left.selectionModel())
        n = model.columnCount()
        for c in range(n):
            self.left.setColumnHidden(c, c >= n_frozen)
            self.right.setColumnHidden(c, c < n_frozen)
            if c < n_frozen:
                self.left.setColumnWidth(c, FROZEN_W[min(c, 1)])
            self.right.setColumnWidth(c, COL_W)
        self.n_frozen = n_frozen
        link_headers(self.left.horizontalHeader(), self.right.horizontalHeader())
        self.fit_columns()
        model.modelReset.connect(self.fit_columns)
        self.left.setObjectName("FrozenLeft")
        self.left.setFixedWidth(sum(FROZEN_W[min(c, 1)] for c in range(min(n_frozen, n))) + 1)
        self.left.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.left.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.left.verticalScrollBar().valueChanged.connect(self.right.verticalScrollBar().setValue)
        self.right.verticalScrollBar().valueChanged.connect(self.left.verticalScrollBar().setValue)
        lay.addWidget(self.left)
        lay.addWidget(self.right, 1)

    def fit_columns(self) -> None:
        """Scrolling columns as wide as their (sampled) values; the titles wrap above them."""
        fit_columns(self.right, range(self.n_frozen, self.model_.columnCount()), max_w=COL_W + 40)

    def show_row(self, row: int) -> None:
        self.left.selectRow(row)
        self.left.scrollTo(self.left.model().index(row, 0), QAbstractItemView.ScrollHint.PositionAtCenter)

    def first_visible_row(self) -> int:
        return max(0, self.right.rowAt(0))

    def visible_rows(self) -> int:
        return max(1, self.right.viewport().height() // ROW_H)

