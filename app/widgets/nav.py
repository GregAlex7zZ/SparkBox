"""Left sidebar: the section buttons with unlock state, the Export button (opens the Export Center, which offers the
exports of every section of the active dataset) and the collapse toggle."""
from __future__ import annotations
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QPushButton, QToolButton, QVBoxLayout
from ..icons import make_icon
from ..state import SECTION_ICONS, SECTION_LABELS, SECTION_ORDER, UNLOCK_REASON, Section
from ..theme import ThemeManager
from ..tokens import (NAV_BTN_H, NAV_BTN_W, NAV_BTN_W_COLLAPSED, SIDE_CARD_W, SIDE_CARD_W_COLLAPSED)


class NavSidebar(QFrame):
    sectionRequested = Signal(object)      # Section
    collapsedChanged = Signal(bool)
    exportRequested = Signal()             # the Export button: open the Export Center

    def __init__(self, theme: ThemeManager):
        super().__init__()
        self.theme = theme
        self.setObjectName("SideCard")
        self._collapsed = False
        self._avail = {s: s is Section.DATA for s in SECTION_ORDER}
        self._export_enabled = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[Section, QPushButton] = {}
        for sec in SECTION_ORDER:
            b = QPushButton(SECTION_LABELS[sec])
            b.setProperty("nav", True)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setIconSize(QSize(20, 20))
            b.setAccessibleName(SECTION_LABELS[sec])
            b.clicked.connect(lambda _=False, s=sec: self.sectionRequested.emit(s))
            self.group.addButton(b)
            self.buttons[sec] = b
            lay.addWidget(b)
        lay.addStretch(1)
        # Export: not a section (never checked); it opens a window with every export of the active dataset.
        self.export_btn = QPushButton("Export")
        self.export_btn.setProperty("nav", True)
        self.export_btn.setObjectName("NavExport")
        self.export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_btn.setIconSize(QSize(20, 20))
        self.export_btn.setAccessibleName("Export")
        self.export_btn.clicked.connect(self.exportRequested)
        lay.addWidget(self.export_btn)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 2, 2)
        row.addStretch(1)
        self.toggle = QToolButton()
        self.toggle.setObjectName("QuietButton")
        self.toggle.setFixedSize(32, 32)
        self.toggle.setIconSize(QSize(18, 18))
        self.toggle.clicked.connect(lambda: self.set_collapsed(not self._collapsed, user=True))
        row.addWidget(self.toggle)
        lay.addLayout(row)
        self.buttons[Section.DATA].setChecked(True)
        theme.changed.connect(self.refresh)
        self.set_collapsed(False)

    # --- API -----------------------------------------------------------------------------------
    def set_available(self, avail: dict[Section, bool]) -> None:
        self._avail = dict(avail)
        self.refresh()

    def set_export_enabled(self, enabled: bool) -> None:
        """Enable Export when a dataset is open (the window itself explains which exports need a run first)."""
        self._export_enabled = enabled
        self.refresh()

    def set_current(self, section: Section) -> None:
        self.buttons[section].setChecked(True)

    def set_collapsed(self, collapsed: bool, user: bool = False) -> None:
        self._collapsed = collapsed
        self.setFixedWidth(SIDE_CARD_W_COLLAPSED if collapsed else SIDE_CARD_W)
        for sec, b in self.buttons.items():
            b.setProperty("collapsed", self._collapsed)
            b.setFixedSize(NAV_BTN_W_COLLAPSED if collapsed else NAV_BTN_W, NAV_BTN_H)
        self.export_btn.setProperty("collapsed", self._collapsed)
        self.export_btn.setFixedSize(NAV_BTN_W_COLLAPSED if collapsed else NAV_BTN_W, NAV_BTN_H)
        self.refresh()
        if user:
            self.collapsedChanged.emit(collapsed)

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    # --- rendering -----------------------------------------------------------------------------
    def refresh(self) -> None:
        t = self.theme.tokens
        for sec, b in self.buttons.items():
            enabled = self._avail.get(sec, False)
            b.setEnabled(enabled)
            label = SECTION_LABELS[sec]
            b.setText("" if self._collapsed else label)
            b.setToolTip(label if enabled else UNLOCK_REASON.get(sec, label))
            b.setIcon(make_icon(SECTION_ICONS[sec], t.text, checked=t.on_pri, disabled=t.dis))
            # Keep the collapsed state declarative so the global stylesheet can
            # center icons without changing the open-sidebar metrics.
            b.setProperty("collapsed", self._collapsed)
            b.style().unpolish(b)
            b.style().polish(b)
            b.update()
        e = self.export_btn
        e.setEnabled(self._export_enabled)
        e.setText("" if self._collapsed else "Export")
        e.setToolTip("Export the results of every section of this dataset" if self._export_enabled
                     else "Load a dataset to export its results")
        e.setIcon(make_icon("download", t.text, disabled=t.dis))
        e.setProperty("collapsed", self._collapsed)
        e.style().unpolish(e)
        e.style().polish(e)
        name = "panel-left-open" if self._collapsed else "panel-left-close"
        self.toggle.setIcon(make_icon(name, t.muted))
        tip = "Expand sidebar" if self._collapsed else "Collapse sidebar"
        self.toggle.setToolTip(tip)
        self.toggle.setAccessibleName(tip)
