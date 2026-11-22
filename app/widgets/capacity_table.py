"""Cycles table of the Capacity section: rows = visible cycles (filter + cycle-0 switch), Cycle column
frozen, the 17 columns (specific/areal ones only when their Sample value is set), 4 significant digits, null = '—' with the core diagnostic as tooltip, Ctrl+C at full precision in
the displayed units. Reuses FrozenTable from the Data Elaboration table."""
from __future__ import annotations
import math
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from ..capacity import CAP_COLUMNS, NULL_TEXT, cap_label, explain
from ..formatting import sig4
from ..units import UnitPrefs


class CapacityModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.values: dict = {}
        self.rows: list[int] = []              # indices into the arrays, in cycle order
        self.units = UnitPrefs()
        self.diag: dict[tuple[int, str], str] = {}
        self.keys = [c.key for c in CAP_COLUMNS]
        self.null_color = "#8798A2"            # the page sets the theme's `dis` token

    def set_data(self, values: dict, rows: list[int], units: UnitPrefs, diagnostics: list,
                 keys: list[str] | None = None) -> None:
        """Show ``rows`` of ``values``; ``keys`` are the columns in order (default: all 17). The page leaves out the
        specific/areal columns whose Sample value was not entered (capacity.shown_columns)."""
        self.beginResetModel()
        self.values, self.rows, self.units = values, rows, units
        self.keys = list(keys) if keys is not None else [c.key for c in CAP_COLUMNS]
        self.diag = {(int(c), col): code for c, col, code in diagnostics}
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.keys)

    def cycle_at(self, r: int) -> int:
        return int(self.values["cycle"][self.rows[r]])

    def row_of_cycle(self, cycle: int) -> int:
        for r, i in enumerate(self.rows):
            if int(self.values["cycle"][i]) == cycle:
                return r
        return -1

    def _v(self, r: int, c: int):
        v = self.values[self.keys[c]][self.rows[r]]
        f = float(v)
        return f if math.isfinite(f) else None

    def header_text(self, c: int) -> str:
        return cap_label(self.keys[c], self.units)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        r, c = index.row(), index.column()
        v = self._v(r, c)
        if role == Qt.ItemDataRole.DisplayRole:
            if c == 0:
                cyc = self.cycle_at(r)
                return "0 · Preliminary" if cyc == 0 else str(cyc)
            return NULL_TEXT if v is None else sig4(v)
        if role == Qt.ItemDataRole.ToolTipRole and v is None and c > 0:
            code = self.diag.get((self.cycle_at(r), self.keys[c]))
            return explain(code, self.keys[c]) if code else "No value."
        if role == Qt.ItemDataRole.ForegroundRole and v is None:
            from PySide6.QtGui import QColor
            return QColor(self.null_color)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignLeft if c == 0 else Qt.AlignmentFlag.AlignRight))
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return self.header_text(section)
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return int(Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignLeft if section == 0 else Qt.AlignmentFlag.AlignRight))
        return None

    def copy_value(self, r: int, c: int) -> str:
        if c == 0:
            return str(self.cycle_at(r))
        v = self._v(r, c)
        return "" if v is None else repr(v)
