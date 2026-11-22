"""Dataset tab strip (scroll only, no overflow menu), a "+" button that opens another file (Auto or Manual
Mapping menu, like a browser's new-tab button) and the fixed Advanced Settings button."""
from __future__ import annotations
from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QPushButton, QScrollArea, QToolButton, QWidget)
from ..icons import make_icon
from ..theme import ThemeManager, repolish
from ..tokens import BAND_H, TAB_H, TAB_PITCH, TAB_W


class TabBox(QFrame):
    selected = Signal(str)
    closeRequested = Signal(str)

    def __init__(self, ds_id: str, name: str, theme: ThemeManager):
        super().__init__()
        self.ds_id, self.name, self.theme, self._active = ds_id, name, theme, False
        self.setObjectName("TabBox")
        self.setFixedSize(TAB_W, TAB_H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 15, 0)
        lay.setSpacing(6)
        self.label = QPushButton()
        self.label.setObjectName("TabLabel")
        self.label.setFlat(True)
        self.label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.label.setToolTip(name)
        self.label.setAccessibleName(f"Dataset {name}")
        self.label.setText(self.label.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, 100))
        self.label.clicked.connect(lambda: self.selected.emit(self.ds_id))
        self.close_btn = QToolButton()
        self.close_btn.setObjectName("TabClose")
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setIconSize(QSize(14, 14))
        self.close_btn.setToolTip("Close")
        self.close_btn.setAccessibleName(f"Close {name}")
        self.close_btn.clicked.connect(lambda: self.closeRequested.emit(self.ds_id))
        lay.addWidget(self.label, 1)
        lay.addWidget(self.close_btn)
        theme.changed.connect(self.apply_theme)
        self.apply_theme()

    def set_active(self, active: bool) -> None:
        self._active = active
        self.setProperty("active", active)
        repolish(self)
        self.apply_theme()

    def apply_theme(self) -> None:
        t = self.theme.tokens
        self.close_btn.setIcon(make_icon("x", t.pri if self._active else t.muted, size=14))


class TabBand(QWidget):
    tabSelected = Signal(str)
    tabCloseRequested = Signal(str)
    advancedToggled = Signal(bool)

    def __init__(self, theme: ThemeManager):
        super().__init__()
        self.theme = theme
        self.setFixedHeight(BAND_H)
        self._boxes: dict[str, TabBox] = {}
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(6)
        self.left = self._chev("Scroll tabs left")
        self.right = self._chev("Scroll tabs right")
        self.scroll = QScrollArea()
        self.scroll.setObjectName("TabScroll")
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFixedHeight(TAB_H + 4)
        self.strip = QWidget()
        self.strip.setObjectName("TabStrip")
        self.strip_lay = QHBoxLayout(self.strip)
        self.strip_lay.setContentsMargins(0, 2, 0, 2)
        self.strip_lay.setSpacing(TAB_PITCH - TAB_W)
        self.scroll.setWidget(self.strip)
        self.sep = QFrame()
        self.sep.setObjectName("Sep")
        self.sep.setFixedSize(1, 22)
        self.adv = QPushButton("Advanced Settings")
        self.adv.setObjectName("AdvButton")
        self.adv.setCheckable(True)
        self.adv.setFixedHeight(32)
        self.adv.setIconSize(QSize(18, 18))
        self.adv.setCursor(Qt.CursorShape.PointingHandCursor)
        self.adv.setToolTip("Advanced Settings")
        self.adv.setAccessibleName("Advanced Settings")
        self.adv.toggled.connect(self._adv_toggled)
        self.add_btn = QToolButton()
        self.add_btn.setObjectName("AddTab")
        self.add_btn.setFixedSize(32, 32)
        self.add_btn.setIconSize(QSize(18, 18))
        self.add_btn.setToolTip("Open another file")
        self.add_btn.setAccessibleName("Open File")
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.strip_lay.addWidget(self.add_btn, 0, Qt.AlignmentFlag.AlignVCenter)   # follows the last tab
        self.strip_lay.addStretch(1)
        for w in (self.left, self.scroll, self.right):
            lay.addWidget(w, 1 if w is self.scroll else 0)
        lay.addSpacing(4)
        lay.addWidget(self.sep)
        lay.addSpacing(4)
        lay.addWidget(self.adv)
        bar = self.scroll.horizontalScrollBar()
        bar.rangeChanged.connect(lambda *_: self._update_chevrons())
        bar.valueChanged.connect(lambda *_: self._update_chevrons())
        self.left.clicked.connect(lambda: self._scroll_by(-TAB_PITCH))
        self.right.clicked.connect(lambda: self._scroll_by(TAB_PITCH))
        theme.changed.connect(self.apply_theme)
        self.apply_theme()

    def _chev(self, tip: str) -> QToolButton:
        b = QToolButton()
        b.setObjectName("ChevButton")
        b.setFixedSize(28, 32)
        b.setIconSize(QSize(16, 16))
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    # --- API ---------------------------------------------------------------------------------
    def add_tab(self, ds_id: str, name: str) -> None:
        box = TabBox(ds_id, name, self.theme)
        box.selected.connect(self.tabSelected)
        box.closeRequested.connect(self.tabCloseRequested)
        self._boxes[ds_id] = box
        self.strip_lay.insertWidget(self.strip_lay.indexOf(self.add_btn), box)

    def set_open_menu(self, menu) -> None:
        """The "+" button shows `menu` (Auto Mapping / Manual Mapping) on click."""
        self.add_btn.setMenu(menu)

    def remove_tab(self, ds_id: str) -> None:
        box = self._boxes.pop(ds_id, None)
        if box:
            box.setParent(None)
            box.deleteLater()

    def set_active(self, ds_id: str | None) -> None:
        for k, box in self._boxes.items():
            box.set_active(k == ds_id)
        if ds_id in self._boxes:
            self.scroll.ensureWidgetVisible(self._boxes[ds_id], 20, 0)

    def set_compact(self, compact: bool) -> None:
        """Icon-only Advanced Settings button when the window is narrow."""
        self.adv.setText("" if compact else "Advanced Settings")
        self.adv.setFixedWidth(38 if compact else 176)

    def set_advanced_checked(self, checked: bool) -> None:
        self.adv.setChecked(checked)

    # --- internals ---------------------------------------------------------------------------
    def _scroll_by(self, dx: int) -> None:
        bar = self.scroll.horizontalScrollBar()
        anim = QPropertyAnimation(bar, b"value", self)
        anim.setDuration(150)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(bar.value())
        anim.setEndValue(max(bar.minimum(), min(bar.maximum(), bar.value() + dx)))
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _update_chevrons(self) -> None:
        bar = self.scroll.horizontalScrollBar()
        self.left.setEnabled(bar.value() > bar.minimum())
        self.right.setEnabled(bar.value() < bar.maximum())

    def _adv_toggled(self, checked: bool) -> None:
        self.apply_theme()
        self.advancedToggled.emit(checked)

    def apply_theme(self) -> None:
        t = self.theme.tokens
        self.left.setIcon(make_icon("chevron-left", t.muted, disabled=t.dis, size=16))
        self.right.setIcon(make_icon("chevron-right", t.muted, disabled=t.dis, size=16))
        self.add_btn.setIcon(make_icon("plus", t.text, disabled=t.dis, size=18))
        name = "panel-right-close" if self.adv.isChecked() else "panel-right-open"
        self.adv.setIcon(make_icon(name, t.text, size=18))
        self._update_chevrons()
