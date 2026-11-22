"""GITT / ICI Setup & Analysis Configuration: the entry point of the five-area workflow.

Cards, top to bottom: protocol (exclusive GITT / ICI), one cycle OR one half-cycle chosen among those of the current
elaboration, calculation methods (incompatible ones disabled with the reason), the single OCV method of the D
results (three exclusive buttons; Pseudo OCV only with ICI), Physical Inputs with units and Required / Optional
marks that follow the checked methods, automatic analysis with its window modes and parameters (the fixed mode takes
one start / end [s] for the GITT pulse and one for the ICI interruption). The advanced IR and indicator settings
are built here as ``advanced_panel`` (same draft, same signals) but shown in the right dock, like every other
section's advanced settings (app.widgets.gitt_ici_dock).
Every edit only changes the draft. The checklist lists each unmet condition; Proceed stays disabled until it is
empty, then applies the draft, recognises the impulses and opens IR Drop Analysis.

The physical coefficient of the physically guided windows starts at 0.0032, the value the specification cites as a
theoretical reference; it is editable and always passed to gregorithm explicitly. Every control has an (i) button
with its explanation (texts in app.gitt_ici_help).
"""
from __future__ import annotations
import html
import math
from dataclasses import replace
from typing import Optional

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPushButton, QRadioButton, QScrollArea,
                               QStyledItemDelegate, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..gitt_ici_help import INFO
from ..gitt_ici import particle_radius_use
from ..gitt_ici import (AREA_UNITS, DEFAULT_THRESHOLDS, IR_MODES, LENGTH_UNITS, METHOD_CHOICES, OCV_METHODS,
                        SERIES, VOLUME_UNITS, WINDOW_MODES, GittIciParams, for_protocol, method_allowed,
                        parse_optional_positive, validate, with_scope)
from ..gitt_ici_review import ACCEPTED, INVALID, MODIFIED, NOT_NEEDED, PENDING, REJECTED
from ..theme import set_prop
from ..tokens import LIGHT

INFO_GEOMETRY = ("V_act is the active-phase volume, not automatically the coating or molar volume. A_eff is the "
                 "effective diffusion area, not automatically the geometric, BET or electrochemical area. Values are "
                 "used as entered and are not inferred.")



# --- lists of the review areas (IR Drop, OCV, Pulse Fitting) -------------------------------------------------------
# One code everywhere: a dot coloured green (accepted / valid), orange (pending, modified, partly valid) or red
# (rejected, invalid, not available); grey when nothing is to review. The word is in the tooltip.
LIST_WIDTH = 260                                    # px: the same narrow list column in every area
NUMBER_WIDTH, STATUS_WIDTH = 40, 50                 # px: fixed "#" / "Rest" and dot columns of the review lists
STATUS_KIND = {ACCEPTED: "ok", PENDING: "pending", MODIFIED: "pending", REJECTED: "bad", INVALID: "bad",
               NOT_NEEDED: "none"}
# The theme's warning colour is a brown close to the error red at dot size, so pending gets a true orange
# (green / orange / red dots).
STATUS_ORANGE = {"light": "#D98200", "dark": "#F0A030"}
STATUS_ROLE = Qt.ItemDataRole.UserRole + 7          # colour of the dot drawn by StatusDelegate


def status_color(theme, kind: str) -> str:
    """Colour of a status kind: theme success (green), orange, theme error (red) or muted grey."""
    t = LIGHT if theme is None else theme.tokens
    if kind == "pending":
        return STATUS_ORANGE.get(t.name, STATUS_ORANGE["light"])
    return {"ok": t.success, "bad": t.err}.get(kind, t.muted)


class StatusDelegate(QStyledItemDelegate):
    """Draws the status dot centred in its cell, in its own colour also on the selected row (a text glyph would take
    the selection colour)."""

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: D102  (Qt override)
        super().paint(painter, option, index)
        color = index.data(STATUS_ROLE)
        if not color:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        r = option.rect
        size = 10.0
        painter.drawEllipse(QRectF(r.center().x() - size / 2 + 1, r.center().y() - size / 2 + 1, size, size))
        painter.restore()


def make_list(columns: list[tuple[str, str]], status_column: int) -> QTreeWidget:
    """Review list with centred headers and values sharing the width; ``columns`` are (short title, tooltip) and
    ``status_column`` holds the coloured dot."""
    tree = QTreeWidget()
    tree.setColumnCount(len(columns))
    tree.setHeaderLabels([title for title, _tip in columns])
    for col, (_title, tip) in enumerate(columns):
        tree.headerItem().setToolTip(col, tip)
    tree.setRootIsDecorated(False)
    tree.setUniformRowHeights(True)
    tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    header = tree.header()
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
    # The theme's 16 px header padding would make the columns wider than the narrow list and push the last one (the
    # dot) out of view, so the list header keeps a small padding and every column fits the viewport.
    header.setStyleSheet("QHeaderView::section { padding: 0 4px; }")
    header.setStretchLastSection(False)
    header.setMinimumSectionSize(28)
    header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)       # values share the width left
    for col, width in ((0, NUMBER_WIDTH), (status_column, STATUS_WIDTH)):
        header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(col, width)
    tree.setItemDelegateForColumn(status_column, StatusDelegate(tree))
    return tree


def list_row(texts: list[str], status: tuple[int, str, str] | None, theme) -> QTreeWidgetItem:
    """Row with every value centred; ``status`` = (column, kind, tooltip) draws the coloured dot in that column."""
    item = QTreeWidgetItem(texts)
    for col in range(len(texts)):
        item.setTextAlignment(col, Qt.AlignmentFlag.AlignCenter)
    if status is not None:
        col, kind, tip = status
        item.setText(col, "")
        item.setData(col, STATUS_ROLE, status_color(theme, kind))
        item.setToolTip(col, wrapped_tip(tip))
    return item


def wrapped_tip(text: str) -> str:
    """``text`` as a rich-text tooltip, which Qt wraps at a readable width; a plain-text tooltip stays on one line
    and a long note would run off the screen."""
    return "<qt>" + html.escape(text).replace("\n", "<br>") + "</qt>"


def select_next(tree: QTreeWidget) -> None:
    """Select the row below the current one, as a click would (the list's selection signal opens it), so reviewing
    moves on after an acceptance. Nothing happens on the last row or without a current row."""
    current = tree.currentItem()
    if current is None:
        return
    index = tree.indexOfTopLevelItem(current)
    if 0 <= index < tree.topLevelItemCount() - 1:
        tree.setCurrentItem(tree.topLevelItem(index + 1))


def list_panel(title: str) -> tuple[QFrame, QVBoxLayout]:
    """The left list column of a review area, always LIST_WIDTH wide."""
    frame = QFrame()
    frame.setProperty("panel", True)
    frame.setFixedWidth(LIST_WIDTH)
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(6, 6, 6, 6)
    lay.addWidget(QLabel(title))
    return frame, lay


class FillPane(QWidget):
    """Scroll-area content that fills the visible height and scrolls only below its minimum height. Without this, a
    QScrollArea sizes the content to its preferred height, which long wrapped labels inflate far beyond the screen."""

    def hasHeightForWidth(self) -> bool:  # noqa: N802  (Qt override)
        """Ignore height-for-width so the scroll area uses the minimum size and the viewport height."""
        return False

    def sizeHint(self):  # noqa: N802  (Qt override)
        """Prefer the minimum size: the scroll area then stretches the pane to the viewport, scrolling only below it."""
        return self.minimumSizeHint()


def fill_scroll(pane: QWidget, name: str) -> QScrollArea:
    """Scroll area showing ``pane`` (a FillPane) at the visible height, scrolling only below its minimum height.
    QScrollArea asks the content's LAYOUT for height-for-width, so the pane sits inside a plain holder whose layout
    sees only the pane, which declares no height-for-width."""
    holder = QWidget()
    holder.setObjectName(name)
    lay = QVBoxLayout(holder)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(pane)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setStyleSheet(f"QScrollArea, #qt_scrollarea_viewport, #{name} {{ background: transparent; }}")
    scroll.setWidget(holder)
    return scroll


def info(theme, key: str) -> QWidget:
    """(i) button opening the explanation ``INFO[key]``; an empty placeholder when no theme is available."""
    if theme is None:
        return QWidget()
    from .controls import InfoButton
    title, paragraphs = INFO[key]
    return InfoButton(theme, title, paragraphs)


def labelled(text: str, theme, key: str, bold: bool = False) -> QWidget:
    """Label followed by its (i) button, for form rows, card titles and strips."""
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    label = QLabel(text)
    if bold:
        label.setStyleSheet("font-weight: 500;")
    row.addWidget(label)
    row.addWidget(info(theme, key))
    row.addStretch(1)
    return box


def card(title: str, theme=None, key: str = "") -> tuple[QFrame, QVBoxLayout]:
    """Themed card (QFrame[panel]) with a bold title and its (i), the grouping idiom shared by the GITT/ICI areas."""
    frame = QFrame()
    frame.setProperty("panel", True)
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(14, 10, 14, 12)
    lay.setSpacing(6)
    if key:
        lay.addWidget(labelled(title, theme, key, bold=True))
    else:
        head = QLabel(title)
        head.setStyleSheet("font-weight: 500;")
        lay.addWidget(head)
    return frame, lay


def button(text: str, kind: str) -> QPushButton:
    """Push button styled by the theme property ``kind`` (run, ghost, secondary, goElab)."""
    b = QPushButton(text)
    b.setProperty(kind, True)
    if kind == "run":
        b.setProperty("goElab", True)                 # filled amber button of a calculation, as in DCIR
    b.setMinimumHeight(32)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def muted(text: str = "") -> QLabel:
    """Wrapped secondary text."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setProperty("muted", True)
    return label


def _read_window(start: str, end: str) -> tuple[Optional[tuple[float, float]], str]:
    """Parse the start / end [s] of a fixed window: both empty -> None (suggested window); otherwise two numbers,
    start ≥ 0 and end > start (comma accepted as decimal separator)."""
    start, end = start.strip().replace(",", "."), end.strip().replace(",", ".")
    if not start and not end:
        return None, ""
    try:
        lo, hi = float(start), float(end)
    except ValueError:
        return None, "enter both start and end in seconds."
    if not (math.isfinite(lo) and math.isfinite(hi) and 0 <= lo < hi):
        return None, "start must be at least 0 and below end."
    return (lo, hi), ""


class SetupView(QWidget):
    """Setup area. ``paramsEdited(params, errors)`` carries the draft read from the fields; ``proceed`` asks the
    page to apply it. The view never validates scientifically: it shows gitt_ici.validate's conditions."""
    paramsEdited = Signal(object, list)
    proceed = Signal()

    def __init__(self, theme=None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._loading = False
        self._base = GittIciParams()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        body = QWidget()
        body.setObjectName("GittSetup")
        grid = QGridLayout(body)
        grid.setContentsMargins(0, 0, 8, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        grid.addWidget(self._protocol_card(), 0, 0)
        grid.addWidget(self._scope_card(), 1, 0)
        grid.addWidget(self._methods_card(), 2, 0)
        grid.addWidget(self._ocv_card(), 3, 0)
        grid.addWidget(self._inputs_card(), 0, 1, 2, 1)
        grid.addWidget(self._auto_card(), 2, 1, 2, 1)
        self.advanced_panel = self._advanced_panel()           # hosted by GittIciSettingsDock, not in this grid
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(5, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea, #qt_scrollarea_viewport, #GittSetup { background: transparent; }")
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        outer.addWidget(self._proceed_bar())

    # --- cards -----------------------------------------------------------------------------------------------------
    def _protocol_card(self) -> QFrame:
        frame, lay = card("Protocol", self.theme, "protocol")
        row = QHBoxLayout()
        self.protocol = {key: QRadioButton(text) for key, text in (("gitt", "GITT"), ("ici", "ICI"))}
        group = QButtonGroup(self)
        for key, radio in self.protocol.items():
            group.addButton(radio)
            row.addWidget(radio)
            radio.toggled.connect(lambda on, k=key: on and self._protocol_changed(k))
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(muted("Chosen explicitly; it is never inferred from segment durations. Changing it restores "
                            "the methods and OCV method preselected for that protocol."))
        return frame

    def _scope_card(self) -> QFrame:
        frame, lay = card("Cycle or half-cycle", self.theme, "scope")
        row = QHBoxLayout()
        self.scope_kind = {key: QRadioButton(text) for key, text in (("cycle", "Cycle"),
                                                                      ("half_cycle", "Half-cycle"))}
        group = QButtonGroup(self)
        for key, radio in self.scope_kind.items():
            group.addButton(radio)
            row.addWidget(radio)
            radio.toggled.connect(lambda on: on and self._scope_kind_changed())
        self.scope_value = QComboBox()
        self.scope_value.setMinimumWidth(110)
        self.scope_value.setAccessibleName("Cycle or half-cycle number")
        row.addWidget(self.scope_value)
        row.addStretch(1)
        lay.addLayout(row)
        self.scope_note = muted()
        lay.addWidget(self.scope_note)
        self.scope_value.currentIndexChanged.connect(self._edited)
        self._counts = (0, 0)
        return frame

    def _methods_card(self) -> QFrame:
        frame, lay = card("Calculation methods", self.theme, "methods")
        self.methods = {}
        row = QHBoxLayout()
        for key, text in METHOD_CHOICES:
            box = QCheckBox(text)
            self.methods[key] = box
            row.addWidget(box)
            row.addWidget(info(self.theme, key))
            row.addSpacing(8)
            box.toggled.connect(self._edited)
        row.addStretch(1)
        lay.addLayout(row)
        self.methods_note = muted()
        lay.addWidget(self.methods_note)
        return frame

    def _ocv_card(self) -> QFrame:
        frame, lay = card("OCV methods", self.theme, "ocv_methods")
        row = QHBoxLayout()
        row.setSpacing(6)
        self.ocv: dict[str, QPushButton] = {}
        for key, text in OCV_METHODS:
            b = button(text, "secondary")
            b.setCheckable(True)                             # any combination: one D series per OCV method
            b.setProperty("ocvChoice", True)
            b.setAccessibleName(f"{text} (OCV method of the D results)")
            b.toggled.connect(lambda _on, bb=b: self._ocv_toggled(bb))
            self.ocv[key] = b
            row.addWidget(b)
            row.addWidget(info(self.theme, key))
            row.addSpacing(6)
        row.addStretch(1)
        lay.addLayout(row)
        self.ocv_note = muted("Select one or more: each selected OCV method gives every compatible calculation "
                              "method its own D series, for comparison. Sampled and extrapolated OCV are kept apart; "
                              "one never overwrites the other.")
        lay.addWidget(self.ocv_note)
        return frame

    def _inputs_card(self) -> QFrame:
        frame, lay = card("Physical Inputs")
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.fields, self.units, self.need = {}, {}, {}
        for key, label, tip, units in (
                ("active_volume", "Active Volume (V_act)", "Active-phase volume", VOLUME_UNITS),
                ("effective_area", "Effective Area (A_eff)", "Effective diffusion area", AREA_UNITS),
                ("particle_radius", "Particle Radius (r_p)", "Particle radius", LENGTH_UNITS)):
            edit, unit, need = QLineEdit(), QComboBox(), QLabel()
            edit.setPlaceholderText("not entered")
            edit.setAccessibleName(f"{label}: {tip}")
            edit.setMinimumWidth(90)
            for code, text in units:
                unit.addItem(text, code)
            need.setProperty("chip", True)
            row = QHBoxLayout()
            row.addWidget(edit, 1)
            row.addWidget(unit)
            row.addWidget(need)
            form.addRow(labelled(label, self.theme, key), row)
            self.fields[key], self.units[key], self.need[key] = edit, unit, need
            edit.textEdited.connect(self._edited)
            unit.currentIndexChanged.connect(self._edited)
        lay.addLayout(form)
        self.rp_note = muted()                         # where r_p is used now (particle_radius_use)
        lay.addWidget(self.rp_note)
        lay.addWidget(muted(INFO_GEOMETRY))
        return frame

    def _auto_card(self) -> QFrame:
        frame, lay = card("Automatic analysis", self.theme, "auto")
        self.auto = QCheckBox("Calculate every impulse automatically after recognition")
        lay.addWidget(self.auto)
        self.auto_body = QWidget()
        form = QFormLayout(self.auto_body)
        form.setContentsMargins(0, 0, 0, 0)
        self.window_mode = QComboBox()
        for code, text in WINDOW_MODES:
            self.window_mode.addItem(text, code)
        self.guidance = QComboBox()
        self.coefficient = QLineEdit()
        self.coefficient.setPlaceholderText("default 0.0032")
        form.addRow(labelled("Window method", self.theme, "window_mode"), self.window_mode)
        self.fixed: dict[str, tuple[QLineEdit, QLineEdit]] = {}
        for role, label in (("pulse", "GITT pulse window [s]"), ("rest_after", "ICI interruption window [s]")):
            start, end = QLineEdit(), QLineEdit()
            start.setPlaceholderText("suggested")         # empty pair = gregorithm's suggested range
            end.setPlaceholderText("suggested")
            start.setAccessibleName(f"{label}: start")
            end.setAccessibleName(f"{label}: end")
            row = QHBoxLayout()
            row.addWidget(start, 1)
            row.addWidget(QLabel("–"))
            row.addWidget(end, 1)
            form.addRow(labelled(label, self.theme, "fixed_window"), row)
            self.fixed[role] = (start, end)
            start.textEdited.connect(self._edited)
            end.textEdited.connect(self._edited)
        form.addRow(labelled("D source for guidance", self.theme, "guidance"), self.guidance)
        form.addRow(labelled("Physical coefficient", self.theme, "coefficient"), self.coefficient)
        lay.addWidget(self.auto_body)
        self.auto_note = muted()
        lay.addWidget(self.auto_note)
        self.auto.toggled.connect(self._edited)
        self.window_mode.currentIndexChanged.connect(self._edited)
        self.guidance.currentIndexChanged.connect(self._edited)
        self.coefficient.textEdited.connect(self._edited)
        return frame

    def _advanced_panel(self) -> QWidget:
        """IR and indicator settings, laid out for the narrow right dock (each label above its field)."""
        panel = QWidget()
        form = QFormLayout(panel)
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.ir_mode = QComboBox()
        for code, text in IR_MODES:
            self.ir_mode.addItem(text, code)
        self.ir_time, self.ir_post_time = QLineEdit(), QLineEdit()
        self.ir_post_time.setPlaceholderText("same as pre-pulse")
        self.thresholds = [QLineEdit() for _ in DEFAULT_THRESHOLDS]
        form.addRow(labelled("GITT Δ IR correction", self.theme, "ir_mode"), self.ir_mode)
        form.addRow(labelled("Pre-pulse IR sample time [s]", self.theme, "ir_time"), self.ir_time)
        form.addRow(labelled("Post-pulse IR sample time [s]", self.theme, "ir_post_time"), self.ir_post_time)
        for edit, label in zip(self.thresholds, ("q_L green ≤ [%]", "q_L orange ≤ [%]", "q_S green ≤ [%]",
                                                 "q_S orange ≤ [%]")):
            form.addRow(labelled(label, self.theme, "thresholds"), edit)
        form.addRow(muted("The Delacourt fixed offset always uses the pre-pulse sample as its IR correction "
                          "(decision A-02); the GITT Δ correction is independent and off by default. Indicator "
                          "thresholds are advisory and never block acceptance."))
        self.ir_mode.currentIndexChanged.connect(self._edited)
        for edit in (self.ir_time, self.ir_post_time, *self.thresholds):
            edit.textEdited.connect(self._edited)
        return panel

    def _proceed_bar(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("panel", True)
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(14, 10, 14, 10)
        col = QVBoxLayout()
        self.check_title = QLabel()
        self.check_title.setStyleSheet("font-weight: 500;")
        self.checklist = muted()
        col.addWidget(self.check_title)
        col.addWidget(self.checklist)
        lay.addLayout(col, 1)
        self.proceed_btn = button("Proceed", "run")
        self.proceed_btn.setMinimumWidth(140)
        self.proceed_btn.clicked.connect(self.proceed)
        lay.addWidget(info(self.theme, "proceed"), 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.proceed_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        return frame

    # --- loading and reading ----------------------------------------------------------------------------------------
    def load(self, params: GittIciParams, n_cycles: int, n_half_cycles: int) -> None:
        """Show ``params`` (a dataset's own draft) and the cycles / half-cycles of its current elaboration."""
        self._loading = True
        try:
            self._base = params
            self._counts = (n_cycles, n_half_cycles)
            self.protocol[params.protocol].setChecked(True)
            kind, value = params.scope
            self.scope_kind[kind].setChecked(True)
            self._fill_scope_values(kind, value)
            for key, box in self.methods.items():          # a method of the other protocol is never kept ticked
                box.setChecked(key in params.methods and method_allowed(params.protocol, key))
            for key, b in self.ocv.items():
                b.setChecked(key in params.ocv_methods)
                self._ocv_toggled(b, quiet=True)
            for key in self.fields:
                value_ = getattr(params, key)
                self.fields[key].setText("" if value_ is None else f"{value_:g}")
                self.units[key].setCurrentIndex(max(0, self.units[key].findData(getattr(params, f"{key}_unit"))))
            self.auto.setChecked(params.auto_analysis)
            self.window_mode.setCurrentIndex(max(0, self.window_mode.findData(params.window_mode)))
            for role, bounds in (("pulse", params.fixed_pulse_window_s), ("rest_after", params.fixed_ici_window_s)):
                for edit, value_ in zip(self.fixed[role], bounds or (None, None)):
                    edit.setText("" if value_ is None else f"{value_:g}")
            self._fill_guidance(params)
            self.coefficient.setText("" if params.guidance_coefficient is None else f"{params.guidance_coefficient:g}")
            self.ir_mode.setCurrentIndex(max(0, self.ir_mode.findData(params.ir_correction)))
            self.ir_time.setText(f"{params.ir_sample_time_s:g}")
            self.ir_post_time.setText("" if params.ir_post_sample_time_s is None
                                      else f"{params.ir_post_sample_time_s:g}")
            for edit, v in zip(self.thresholds, params.thresholds):
                edit.setText(f"{v:g}")
        finally:
            self._loading = False
        self.show_state(params, [], validate(params), False, True)

    def _fill_scope_values(self, kind: str, value: Optional[int]) -> None:
        count = self._counts[0 if kind == "cycle" else 1]
        self.scope_value.blockSignals(True)
        self.scope_value.clear()
        self.scope_value.addItem("Choose…", None)
        for n in range(1, count + 1):
            self.scope_value.addItem(f"{'Cycle' if kind == 'cycle' else 'Half-cycle'} {n}", n)
        self.scope_value.setCurrentIndex(max(0, self.scope_value.findData(value)))
        self.scope_value.blockSignals(False)
        what = "cycles" if kind == "cycle" else "half-cycles"
        self.scope_note.setText(f"The current elaboration has {count} {what}." if count
                                else "Run Data Elaboration first: no cycle is available yet.")

    def _fill_guidance(self, params: GittIciParams) -> None:
        self.guidance.blockSignals(True)
        self.guidance.clear()
        self.guidance.addItem("Choose a D source…", "")
        for key in params.methods:
            if key in SERIES and key != "ici":
                for source in params.ocv_methods:
                    if source != "ici_intercept":
                        self.guidance.addItem(f"{SERIES[key][0]} · {dict(OCV_METHODS)[source]}", f"{key}|{source}")
        self.guidance.setCurrentIndex(max(0, self.guidance.findData(params.guidance_source)))
        self.guidance.blockSignals(False)

    def _protocol_changed(self, key: str) -> None:
        if self._loading:
            return
        params, _errors = self.read()
        params = for_protocol(params or self._base, key)
        self.load(params, *self._counts)
        self._edited()

    def _scope_kind_changed(self) -> None:
        if self._loading:
            return
        kind = "half_cycle" if self.scope_kind["half_cycle"].isChecked() else "cycle"
        self._fill_scope_values(kind, None)
        self._edited()

    def read(self) -> tuple[Optional[GittIciParams], list[str]]:
        """Parameters from the fields and the entry errors (non-numbers); conditions come from validate()."""
        errors, values = [], {}
        for key, edit in self.fields.items():
            value, err = parse_optional_positive(edit.text())
            if err:
                errors.append(f"{edit.accessibleName().split(':')[0]}: {err}")
            values[key] = value
        coefficient, err = parse_optional_positive(self.coefficient.text())
        errors += [f"Physical coefficient: {err}"] if err else []
        ir_time, err = parse_optional_positive(self.ir_time.text())
        errors += [f"Pre-pulse IR sample time: {err}"] if err else []
        ir_post, err = parse_optional_positive(self.ir_post_time.text())
        errors += [f"Post-pulse IR sample time: {err}"] if err else []
        fixed = {}
        for role, (start, end) in self.fixed.items():
            fixed[role], err = _read_window(start.text(), end.text())
            errors += [f"{start.accessibleName().split(':')[0]}: {err}"] if err else []
        thresholds = []
        for edit in self.thresholds:
            try:
                thresholds.append(float(edit.text().strip().replace(",", ".")))
            except ValueError:
                errors.append("Thresholds: enter numbers.")
                break
        if errors:
            return None, errors
        base = self._base
        protocol = "ici" if self.protocol["ici"].isChecked() else "gitt"
        kind = "half_cycle" if self.scope_kind["half_cycle"].isChecked() else "cycle"
        ocv = tuple(k for k, b in self.ocv.items() if b.isChecked() and b.isEnabled())
        params = replace(
            with_scope(base, kind, self.scope_value.currentData()), protocol=protocol,
            methods=tuple(k for k, b in self.methods.items() if b.isChecked() and method_allowed(protocol, k)),
            ocv_methods=ocv,
            active_volume=values["active_volume"], active_volume_unit=self.units["active_volume"].currentData(),
            effective_area=values["effective_area"], effective_area_unit=self.units["effective_area"].currentData(),
            particle_radius=values["particle_radius"],
            particle_radius_unit=self.units["particle_radius"].currentData(),
            auto_analysis=self.auto.isChecked(), window_mode=self.window_mode.currentData(),
            fixed_pulse_window_s=fixed["pulse"], fixed_ici_window_s=fixed["rest_after"],
            guidance_source=self.guidance.currentData() or "", guidance_coefficient=coefficient,
            ir_correction=self.ir_mode.currentData(),
            ir_sample_time_s=base.ir_sample_time_s if ir_time is None else ir_time, ir_post_sample_time_s=ir_post,
            thresholds=tuple(thresholds))
        return params, []

    def _ocv_toggled(self, b: QPushButton, quiet: bool = False) -> None:
        """Show a selected OCV method as filled (checkable buttons have no other visible checked state)."""
        b.setProperty("goElab", b.isChecked())
        b.setProperty("secondary", not b.isChecked())
        b.style().unpolish(b)
        b.style().polish(b)
        b.setText(("✓ " if b.isChecked() else "") + dict(OCV_METHODS)[next(k for k, v in self.ocv.items()
                                                                          if v is b)])
        if not quiet:
            self._edited()

    def _edited(self, *_args) -> None:
        if self._loading:
            return
        params, errors = self.read()
        if params is not None:
            self._base = params
        self.paramsEdited.emit(params, errors)

    # --- state -----------------------------------------------------------------------------------------------------
    def show_state(self, params: GittIciParams, entry_errors: list, conditions: list, running: bool,
                   has_elaboration: bool) -> None:
        """Enable controls and the Proceed button from the draft and its unmet conditions."""
        protocol = params.protocol
        blocked = []
        for key, box in self.methods.items():
            allowed = method_allowed(protocol, key)
            box.setEnabled(allowed)
            box.setToolTip("" if allowed else f"Not compatible with {protocol.upper()}: it needs a GITT pulse "
                           "between two rests.")
            if not allowed:
                blocked.append(box.text())
        self.methods_note.setText(
            (f"Not available with {protocol.upper()}: {', '.join(blocked)}. " if blocked else "")
            + "Several methods can be compared; Delacourt uses the fixed offset (A-02).")
        pseudo_ok = "ici" in params.methods
        self.ocv["ici_intercept"].setEnabled(pseudo_ok)
        self.ocv["ici_intercept"].setToolTip("Used only by ICI." if pseudo_ok else
                                             "Available only when ICI is selected.")
        classic = any(k in params.methods for k in ("gitt_va", "gitt_delta", "ici"))
        guided = params.auto_analysis and params.window_mode == "physically_guided"
        for key, needed in (("active_volume", classic), ("effective_area", classic),
                            ("particle_radius", "delacourt_fixed" in params.methods or guided)):
            self.need[key].setText("Required" if needed else "Optional")
            set_prop(self.need[key], "required", needed)    # Required: highlighted in the accent colour
        self.rp_note.setText(particle_radius_use(params))
        self.auto_body.setVisible(params.auto_analysis)
        self.guidance.setEnabled(guided)
        fixed_mode = params.window_mode == "auto_fixed"
        for role, (wanted, method) in (
                ("pulse", (any(k in params.methods for k in ("gitt_va", "gitt_delta")), "GITT VA or GITT Δ")),
                ("rest_after", ("ici" in params.methods, "ICI"))):
            for edit in self.fixed[role]:
                edit.setEnabled(wanted)
                edit.setToolTip(f"Select {method} to use this window." if not wanted else
                                "Fitted window of every impulse." if fixed_mode else
                                "Range searched for every impulse; empty = suggested range.")
        self.coefficient.setEnabled(guided)
        self.auto_note.setText(
            "Windows are selected automatically for every impulse; you can still edit each one in Pulse Fitting."
            if params.auto_analysis else
            "Off: Proceed only recognises the impulses. Calculate them in Pulse Fitting, where windows can be edited "
            "and the automatic choice re-applied impulse by impulse.")
        issues = list(entry_errors) + list(conditions)
        if not has_elaboration:
            issues.insert(0, "Run Data Elaboration first.")
        self.check_title.setText("Ready to proceed" if not issues else f"{len(issues)} condition(s) to fix")
        self.checklist.setText("\n".join(f"• {text}" for text in issues) if issues else
                               "Proceed recognises the impulses of the selected element and opens IR Drop Analysis.")
        self.proceed_btn.setEnabled(not issues and not running)
