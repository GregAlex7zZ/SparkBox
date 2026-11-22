"""Right dock for Data Elaboration: 'Elaboration Settings'. Two accordion groups, per-field (i) popovers,
'Modified' tags relative to the baseline (applied result parameters, else user defaults), pending pill and reset."""
from __future__ import annotations
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget)
from ..elab_controller import ElabController
from ..elaboration import (CYCLE_ORDER_LABELS, CYCLE_ORDER_POPOVER, GROUP_CCCV, GROUP_CYCLE, NUM_FIELDS)
from ..icons import make_icon
from ..theme import ThemeManager, set_prop
from .controls import LabelRow, ParamField, SampleBlock


class Accordion(QWidget):
    """Header (chevron-right collapsed / chevron-down expanded, 16 px) + content. Expanded by default."""

    def __init__(self, theme: ThemeManager, title: str):
        super().__init__()
        self.theme = theme
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(6)
        rule = QFrame()
        rule.setObjectName("Rule")
        lay.addWidget(rule)
        self.head = QPushButton(title)
        self.head.setObjectName("GroupHead")
        self.head.setCheckable(True)
        self.head.setChecked(True)
        self.head.setCursor(Qt.CursorShape.PointingHandCursor)
        self.head.setIconSize(QSize(16, 16))
        self.head.setAccessibleName(title)
        lay.addWidget(self.head)
        self.content = QWidget()
        self.cl = QVBoxLayout(self.content)
        self.cl.setContentsMargins(22, 4, 2, 4)
        self.cl.setSpacing(14)
        lay.addWidget(self.content)
        self.head.toggled.connect(self._toggled)
        theme.changed.connect(self._icon)
        self._icon()

    def _toggled(self, on: bool) -> None:
        self.content.setVisible(on)
        self._icon()

    def _icon(self) -> None:
        self.head.setIcon(make_icon("chevron-down" if self.head.isChecked() else "chevron-right",
                                    self.theme.tokens.text, size=16))


class ElabSettingsDock(QFrame):
    def __init__(self, theme: ThemeManager, ctl: ElabController):
        super().__init__()
        self.setObjectName("DockCard")
        self.theme, self.ctl, self.ds_id = theme, ctl, None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(12)
        t = QLabel("Elaboration Settings")
        t.setStyleSheet("font-size: 11.25pt; font-weight: 500;")
        outer.addWidget(t)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 6, 0)
        bl.setSpacing(0)
        scroll = QScrollArea()
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("TabScroll")
        scroll.setWidget(body)
        self.g1, self.g2 = Accordion(theme, GROUP_CYCLE), Accordion(theme, GROUP_CCCV)
        # cycle order (select, unified dropdown style)
        order = QWidget()
        ol = QVBoxLayout(order)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(6)
        self.order_row = LabelRow(theme, "Cycle Order", CYCLE_ORDER_POPOVER)
        self.order_combo = QComboBox()
        self.order_combo.setAccessibleName("Cycle Order")
        for k, v in CYCLE_ORDER_LABELS.items():
            self.order_combo.addItem(v, k)
        self.order_hint = QLabel()
        self.order_hint.setProperty("muted", True)
        self.order_hint.setStyleSheet("font-size: 8.25pt;")
        for x in (self.order_row, self.order_combo, self.order_hint):
            ol.addWidget(x)
        self.g1.cl.addWidget(order)
        self.fields: dict[str, ParamField] = {}
        for f in NUM_FIELDS:
            pf = ParamField(theme, f)
            pf.textEdited.connect(self._typed)
            self.fields[f.key] = pf
            (self.g1 if f.group == GROUP_CYCLE else self.g2).cl.addWidget(pf)
        bl.addWidget(self.g1)
        bl.addWidget(self.g2)
        self.g3 = Accordion(theme, "Sample")
        self.sample = SampleBlock(theme, docked=True)
        self.sample.textEdited.connect(lambda k, t: self.ctl.set_sample_text(self.ds_id, k, t))
        self.g3.cl.addWidget(self.sample)
        bl.addWidget(self.g3)
        bl.addStretch(1)
        outer.addWidget(scroll, 1)
        rule = QFrame()
        rule.setObjectName("Rule")
        outer.addWidget(rule)
        foot = QHBoxLayout()
        self.pending = QLabel("Changes not applied")
        self.pending.setProperty("pending", True)
        self.reset = QPushButton("Reset to User Defaults")
        self.reset.setProperty("secondary", True)
        self.reset.setFixedHeight(30)
        self.reset.setCursor(Qt.CursorShape.PointingHandCursor)
        foot.addWidget(self.pending)
        foot.addStretch(1)
        foot.addWidget(self.reset)
        outer.addLayout(foot)
        self.order_combo.activated.connect(lambda i: self.ctl.set_order(self.ds_id, self.order_combo.itemData(i)))
        self.reset.clicked.connect(lambda: self.ctl.reset(self.ds_id))
        ctl.changed.connect(lambda ds: self.refresh() if ds == self.ds_id else None)

    def show_dataset(self, ds_id: str | None) -> None:
        self.ds_id = ds_id
        self.refresh()

    def _typed(self, key: str, text: str) -> None:
        self.ctl.set_number_text(self.ds_id, key, text)

    def refresh(self) -> None:
        e = self.ctl.elab(self.ds_id) if self.ds_id else None
        if e is None:
            return
        d = self.ctl.defaults.params
        mods = e.modified(d)
        self.order_combo.setCurrentIndex(self.order_combo.findData(e.params.cycle_order))
        changed = "cycle_order" in mods
        set_prop(self.order_combo, "changed", changed)
        self.order_row.set_modified(changed)
        self.order_hint.setText(f"Default {CYCLE_ORDER_LABELS[d.cycle_order]}")
        for f in NUM_FIELDS:
            self.fields[f.key].show_value(getattr(e.params, f.key), e.texts.get(f.key), e.errors.get(f.key, ""),
                                          f.key in mods, getattr(d, f.key))
        self.sample.show_values(e.sample, e.sample_texts, e.sample_errors)
        self.pending.setVisible(bool(mods) or not e.valid)
        self.reset.setEnabled(e.can_reset(d))

