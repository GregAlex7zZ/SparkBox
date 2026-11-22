"""Manual Mapping dialog: all source columns with headers and sample rows."""
from __future__ import annotations
from typing import Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)
from ..backend import (CURRENT_UNITS, TIME_UNITS, VOLTAGE_UNITS, ColumnMapping, SourceInspection)
from ..icons import pixmap
from ..theme import ThemeManager

ROLES = ["Not used", "Time", "Current", "Voltage"]
UNITS = {"Time": TIME_UNITS, "Current": CURRENT_UNITS, "Voltage": VOLTAGE_UNITS}
COL_W, LABEL_W, PREVIEW_ROWS = 150, 128, 8


class MapDialog(QDialog):
    def __init__(self, filename: str, insp: SourceInspection, theme: ThemeManager, parent=None):
        super().__init__(parent)
        self.theme, self.insp = theme, insp
        self.setWindowTitle("Manual Mapping")
        self.resize(960, 600)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 14, 20, 12)
        lay.setSpacing(12)
        head = QHBoxLayout()
        title = QLabel("Manual Mapping")
        title.setStyleSheet("font-size: 12pt; font-weight: 500;")
        fn = QLabel(filename)
        fn.setProperty("muted", True)
        fn.setProperty("small", True)
        head.addWidget(title)
        head.addWidget(fn)
        head.addStretch(1)
        lay.addLayout(head)
        if insp.ambiguity_reason:
            ban = QFrame()
            ban.setProperty("banner", True)
            b = QHBoxLayout(ban)
            b.setContentsMargins(12, 10, 12, 10)
            ic = QLabel()
            ic.setPixmap(pixmap("info", theme.tokens.pri, 18))
            msg = QLabel(insp.ambiguity_reason)
            msg.setWordWrap(True)
            b.addWidget(ic)
            b.addWidget(msg, 1)
            lay.addWidget(ban)
        chips = QHBoxLayout()
        chips.setSpacing(8)
        for txt in (insp.file_format, f"Delimiter {insp.delimiter}", insp.encoding, f"{len(insp.columns)} columns"):
            c = QLabel(txt)
            c.setProperty("chip", True)
            chips.addWidget(c)
        self.header_chk = QCheckBox("First row contains column names")
        self.header_chk.setChecked(True)
        self.header_chk.setEnabled(False)
        self.header_chk.setToolTip("Only files whose first row contains column names are supported.")
        chips.addSpacing(8)
        chips.addWidget(self.header_chk)
        chips.addStretch(1)
        lay.addLayout(chips)
        # grid
        frame = QFrame()
        frame.setProperty("panel", True)
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        inner = QWidget()
        g = QGridLayout(inner)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(0)
        for r, txt in enumerate(["Role", "Unit / format", "Column name in file"]):
            lab = QLabel(txt)
            lab.setProperty("muted", True)
            lab.setProperty("small", True)
            lab.setContentsMargins(12, 0, 0, 0)
            lab.setFixedWidth(LABEL_W)
            g.addWidget(lab, r, 0)
        for i in range(PREVIEW_ROWS):
            lab = QLabel(f"Row {i + 1}")
            lab.setProperty("muted", True)
            lab.setProperty("small", True)
            lab.setContentsMargins(12, 0, 0, 0)
            g.addWidget(lab, 3 + i, 0)
        self.roles: list[QComboBox] = []
        self.units: list[QComboBox] = []
        for c, name in enumerate(insp.columns):
            role, unit = QComboBox(), QComboBox()
            role.addItems(ROLES)
            role.setFixedWidth(COL_W - 20)
            unit.setFixedWidth(COL_W - 20)
            role.setAccessibleName(f"Role of column {name}")
            unit.setAccessibleName(f"Unit or format of column {name}")
            role.currentTextChanged.connect(lambda _t, i=c: self._role_changed(i))
            self.roles.append(role)
            self.units.append(unit)
            g.addWidget(role, 0, c + 1, Qt.AlignmentFlag.AlignCenter)
            g.addWidget(unit, 1, c + 1, Qt.AlignmentFlag.AlignCenter)
            nm = QLabel(name)
            nm.setStyleSheet("font-weight: 500; font-size: 9pt;")
            nm.setContentsMargins(12, 0, 12, 0)
            nm.setToolTip(name)
            g.addWidget(nm, 2, c + 1)
            for i, row in enumerate(insp.preview_rows[:PREVIEW_ROWS]):
                v = QLabel(row[c] if c < len(row) else "")
                v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                v.setContentsMargins(0, 0, 12, 0)
                v.setStyleSheet("font-size: 9pt;")
                g.addWidget(v, 3 + i, c + 1)
        for r in range(3 + PREVIEW_ROWS):
            g.setRowMinimumHeight(r, 44 if r < 2 else (36 if r == 2 else 30))
        g.setColumnStretch(len(insp.columns) + 1, 1)
        scroll.setWidget(inner)
        # Themed surfaces: without them the viewport keeps Qt's light system palette, so in Dark the preview text
        # (theme colour) is unreadable on a light background.
        for surface in (scroll, scroll.viewport(), inner):
            surface.setObjectName("MapGrid")
            surface.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        fl.addWidget(scroll)
        lay.addWidget(frame, 1)
        # footer
        foot = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(3)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.status_icon = QLabel()
        self.status = QLabel()
        row.addWidget(self.status_icon)
        row.addWidget(self.status, 1)
        left.addLayout(row)
        left.addWidget(self._small("Preview shows the first 8 data rows of the source file. The file is never modified."))
        foot.addLayout(left, 1)
        cancel = QPushButton("Cancel")
        cancel.setProperty("secondary", True)
        self.ok = QPushButton("Import")
        self.ok.setProperty("primary", True)
        self.ok.setDefault(True)
        cancel.clicked.connect(self.reject)
        self.ok.clicked.connect(self.accept)
        foot.addWidget(cancel)
        foot.addWidget(self.ok)
        lay.addLayout(foot)
        self._apply_suggestion(insp.suggested)
        for i in range(len(insp.columns)):
            self._role_changed(i)
        self._validate()

    @staticmethod
    def _small(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setProperty("muted", True)
        lab.setProperty("small", True)
        return lab

    def _apply_suggestion(self, m: Optional[ColumnMapping]) -> None:
        if not m:
            return
        for col, role, unit in ((m.time_col, "Time", m.time_unit), (m.current_col, "Current", m.current_unit),
                                (m.voltage_col, "Voltage", m.voltage_unit)):
            self.roles[col].setCurrentText(role)
            self._fill_units(col)
            self.units[col].setCurrentText(unit)

    def _fill_units(self, i: int) -> None:
        role, unit = self.roles[i].currentText(), self.units[i]
        unit.blockSignals(True)
        unit.clear()
        if role in UNITS:
            unit.addItems(UNITS[role])
            unit.setEnabled(True)
        else:
            unit.addItem("—")
            unit.setEnabled(False)
        unit.blockSignals(False)

    def _role_changed(self, i: int) -> None:
        self._fill_units(i)
        self._validate()

    def _validate(self) -> None:
        t = self.theme.tokens
        problems = []
        for role in ("Time", "Current", "Voltage"):
            n = sum(1 for c in self.roles if c.currentText() == role)
            if n == 0:
                problems.append(f"{role} is not assigned")
            elif n > 1:
                problems.append(f"{role} is assigned to {n} columns")
        ok = not problems
        self.ok.setEnabled(ok)
        self.status_icon.setPixmap(pixmap("circle-check" if ok else "circle-alert", t.success if ok else t.muted, 16))
        self.status.setText("Time, Current and Voltage are each assigned once." if ok else "; ".join(problems) + ".")
        self.status.setProperty("ok", ok)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def mapping(self) -> ColumnMapping:
        idx = {r: next(i for i, c in enumerate(self.roles) if c.currentText() == r) for r in ("Time", "Current", "Voltage")}
        return ColumnMapping(idx["Time"], idx["Current"], idx["Voltage"], self.units[idx["Time"]].currentText(),
                             self.units[idx["Current"]].currentText(), self.units[idx["Voltage"]].currentText(),
                             self.header_chk.isChecked())
