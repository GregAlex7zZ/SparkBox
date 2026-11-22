"""Small reusable controls: SegmentedControl, InfoButton + Popover, ParamField (14.4), Switch, ColorSquare and
StatusPill. Layout helpers for narrow windows: ElidedLabel (free text ends with '…', full text as tooltip),
FlowLayout + group() (toolbars wrap onto a new line instead of squeezing controls until their text is cut)."""
from __future__ import annotations
from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPalette, QPen
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QColorDialog, QFrame, QHBoxLayout, QLabel, QLayout,
                               QLineEdit, QPushButton, QScrollArea, QSizePolicy, QSplitter, QSplitterHandle, QToolButton, QVBoxLayout, QWidget)


class ElidedLabel(QLabel):
    """Single-line label for free text (file names, counts, notes): when space is short it ends with '…' instead of
    being cut, and the complete text becomes its tooltip. Its preferred size is still the full text, so it is only
    shortened when the layout really has no room. Not for word-wrapped or rich text (those keep QLabel behaviour)."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._own_tip = ""

    def setToolTip(self, tip: str) -> None:
        self._own_tip = tip
        super().setToolTip(tip)

    def minimumSizeHint(self) -> QSize:
        s = super().minimumSizeHint()
        if self.wordWrap():
            return s
        return QSize(min(s.width(), self.fontMetrics().horizontalAdvance("M…") + 4), s.height())

    def paintEvent(self, e) -> None:
        if self.wordWrap() or self.textFormat() == Qt.TextFormat.RichText:
            super().paintEvent(e)
            return
        p = QPainter(self)
        r = self.contentsRect()
        full = self.text()
        shown = self.fontMetrics().elidedText(full, Qt.TextElideMode.ElideRight, r.width())
        self.style().drawItemText(p, r, int(self.alignment()), self.palette(), self.isEnabled(), shown,
                                  QPalette.ColorRole.WindowText)
        tip = full if shown != full else self._own_tip
        if super().toolTip() != tip:
            super().setToolTip(tip)


class FlowLayout(QLayout):
    """Lays its items left to right and continues on a new line when the row is full (a toolbar that wraps instead
    of squeezing its controls until their text is cut). Put controls that belong together in one QWidget so they
    wrap as a group. Items keep their size hints; an item wider than the whole row is shrunk to the row."""

    def __init__(self, parent=None, h_spacing: int = 8, v_spacing: int = 6):
        super().__init__(parent)
        self._items: list = []
        self._h, self._v = h_spacing, v_spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._layout(rect, apply=True)

    def sizeHint(self) -> QSize:
        w = sum(it.sizeHint().width() for it in self._visible()) + self._h * max(0, len(self._visible()) - 1)
        h = max((it.sizeHint().height() for it in self._visible()), default=0)
        m = self.contentsMargins()
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def minimumSize(self) -> QSize:
        s = QSize()
        for it in self._visible():
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _visible(self) -> list:
        return [it for it in self._items if not (it.widget() is not None and it.widget().isHidden())]

    def _layout(self, rect: QRect, apply: bool) -> int:
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = r.x(), r.y(), 0
        row: list = []

        def flush():
            for it, ix, w in row:
                hint = it.sizeHint()
                if apply:
                    it.setGeometry(QRect(ix, y + (line_h - hint.height()) // 2, w, hint.height()))

        for it in self._visible():
            hint = it.sizeHint()
            w = min(hint.width(), r.width())
            if row and x + w > r.right() + 1:
                flush()
                row, x, y = [], r.x(), y + line_h + self._v
                line_h = 0
            row.append((it, x, w))
            x += w + self._h
            line_h = max(line_h, hint.height())
        flush()
        return y + line_h - rect.y() + m.bottom()


def group(*widgets, spacing: int = 6) -> QWidget:
    """Widgets that must stay on the same line inside a FlowLayout (for example a label and its combo box)."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(spacing)
    for x in widgets:
        if isinstance(x, int):
            h.addSpacing(x)
        else:
            h.addWidget(x)
    return w
from ..elaboration import NumField, fmt_param
from ..icons import make_icon
from ..theme import ThemeManager, set_prop


class SegmentedControl(QFrame):
    """Two or more mutually exclusive options (radio semantics). Keyboard: Left/Right move, Space selects."""
    valueChanged = Signal(str)

    def __init__(self, options: list[tuple[str, str]], small: bool = False, aria: str = ""):
        super().__init__()
        self.setObjectName("Segmented")
        self.setProperty("small", small)
        self.setAccessibleName(aria)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(0)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._btn: dict[str, QPushButton] = {}
        for value, label in options:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setAccessibleName(label)
            bold = QFont(b.font())
            bold.setWeight(QFont.Weight.Medium)                   # the checked option is drawn in weight 500
            b.setMinimumWidth(QFontMetrics(bold).horizontalAdvance(label) + (24 if small else 28) + 8)   # never cut
            self._group.addButton(b)
            lay.addWidget(b)
            self._btn[value] = b
            b.clicked.connect(lambda _=False, v=value: self.valueChanged.emit(v))

    def set_value(self, value: str) -> None:
        b = self._btn.get(value)
        if b:
            b.setChecked(True)

    def value(self) -> str:
        return next((v for v, b in self._btn.items() if b.isChecked()), "")

    def set_changed(self, changed: bool) -> None:
        """Ocean border when the value differs from the baseline (mockup 'Modified')."""
        set_prop(self, "changed", changed)


class Popover(QFrame):
    """Info card opened by an (i) button. Closes on click outside / Esc (Qt.Popup). Text is passive: no actions.

    The card is sized to its text: 290 px wide (380 px for long texts) and exactly as tall as the wrapped paragraphs
    (measured with heightForWidth: adjustSize alone under-measures word-wrapped labels in a top-level window, which cut
    the text). When the text is taller than the screen allows, the paragraphs scroll inside the card."""

    WIDTH, WIDE, LONG_TEXT = 290, 380, 450          # px, px, characters
    MARGINS = (14, 12, 14, 12)

    def __init__(self, title: str, paragraphs: tuple[str, ...], parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("Popover")
        self.setFixedWidth(self.WIDE if sum(len(p) for p in paragraphs) > self.LONG_TEXT else self.WIDTH)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*self.MARGINS)
        lay.setSpacing(6)
        self.title = QLabel(title)
        self.title.setStyleSheet("font-weight: 500;")
        self.title.setWordWrap(True)
        lay.addWidget(self.title)
        self.body = QWidget()
        self.body.setObjectName("PopoverBody")
        self.body.setStyleSheet("#PopoverBody { background: transparent; }")
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(6)
        self.labels = []
        for i, p in enumerate(paragraphs):
            l = QLabel(p)
            l.setWordWrap(True)
            l.setStyleSheet("font-size: 9pt;")
            l.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if i:
                l.setProperty("muted", True)
            bl.addWidget(l)
            self.labels.append(l)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.body)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self.scroll.viewport().setAutoFillBackground(False)
        lay.addWidget(self.scroll)

    def _fit(self, max_height: int) -> None:
        """Height = title + all paragraphs at the card's text width, capped at `max_height` (then the text scrolls)."""
        self.ensurePolished()
        left, top, right, bottom = self.MARGINS
        inner = self.width() - left - right
        for w in [self.title, *self.labels]:
            w.ensurePolished()
        spacing = self.body.layout().spacing()

        def body_height(width: int) -> int:
            return sum(l.heightForWidth(width) for l in self.labels) + spacing * max(0, len(self.labels) - 1)

        head = self.title.heightForWidth(inner) + self.layout().spacing()
        room = max(60, max_height - top - bottom - head)
        need = body_height(inner)
        if need > room:                                 # scrolling: the scroll bar takes part of the width
            need = body_height(inner - self.scroll.verticalScrollBar().sizeHint().width())
        self.scroll.setFixedHeight(min(need, room))
        self.setFixedHeight(top + head + min(need, room) + bottom)

    def show_beside(self, anchor: QWidget) -> None:
        """Inside the right dock (object name DockCard): to the LEFT of the dock, aligned to the anchor (mockup).
        Elsewhere: just below the anchor. Always within the screen; taller texts scroll inside the card."""
        scr = anchor.screen().availableGeometry()
        self._fit(scr.height() - 16)
        dock = anchor
        while dock is not None and dock.objectName() != "DockCard":
            dock = dock.parentWidget()
        if dock is not None:
            x = dock.mapToGlobal(QPoint(0, 0)).x() - self.width() - 12
            pos = QPoint(x, anchor.mapToGlobal(QPoint(0, 0)).y() - 8)
        else:
            pos = anchor.mapToGlobal(QPoint(-self.width() // 2 + anchor.width() // 2, anchor.height() + 6))
        pos.setX(max(scr.left() + 8, min(pos.x(), scr.right() - self.width() - 8)))
        pos.setY(max(scr.top() + 8, min(pos.y(), scr.bottom() - self.height() - 8)))
        self.move(pos)
        self.show()


class InfoButton(QToolButton):
    def __init__(self, theme: ThemeManager, name: str, paragraphs: tuple[str, ...]):
        super().__init__()
        self.theme, self._title, self._paras = theme, name, paragraphs
        self.setProperty("info", True)
        self.setFixedSize(20, 20)
        self.setIconSize(QSize(15, 15))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"About {name}")
        self.setAccessibleName(f"About {name}")
        self.clicked.connect(self._open)
        theme.changed.connect(self.apply_theme)
        self.apply_theme()

    def apply_theme(self) -> None:
        self.setIcon(make_icon("info", self.theme.tokens.muted, size=15))

    def _open(self) -> None:
        Popover(self._title, self._paras, self.window()).show_beside(self)


class LabelRow(QWidget):
    """label + (i) + stretch + optional 'Modified' tag."""

    def __init__(self, theme: ThemeManager, label: str, paragraphs: tuple[str, ...], size_px: int = 13):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        l = QLabel(label)
        l.setStyleSheet(f"font-size: {size_px * 0.75:g}pt;")
        lay.addWidget(l)
        lay.addWidget(InfoButton(theme, label, paragraphs))
        lay.addStretch(1)
        self.tag = QLabel("Modified")
        self.tag.setProperty("tag", True)
        self.tag.hide()
        lay.addWidget(self.tag)

    def set_modified(self, on: bool) -> None:
        self.tag.setVisible(on)


class ParamField(QWidget):
    """Numeric parameter: label row, input + unit, hint ('Default 0 mA' or the error). Emits the raw typed text on
    editingFinished AND on every edit, so Process can be enabled/disabled live (validation lives in elaboration.py)."""
    textEdited = Signal(str, str)           # key, text

    def __init__(self, theme: ThemeManager, f: NumField):
        super().__init__()
        self.f = f
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.row = LabelRow(theme, f.label, f.popover)
        lay.addWidget(self.row)
        h = QHBoxLayout()
        h.setSpacing(8)
        self.edit = QLineEdit()
        self.edit.setAccessibleName(f.label)
        self.edit.textEdited.connect(lambda t: self.textEdited.emit(f.key, t))
        unit = QLabel(f.unit)
        unit.setProperty("muted", True)
        unit.setProperty("small", True)
        unit.setFixedWidth(24)
        h.addWidget(self.edit, 1)
        h.addWidget(unit)
        lay.addLayout(h)
        self.hint = QLabel()
        self.hint.setStyleSheet("font-size: 8.25pt;")
        self.hint.setProperty("muted", True)
        lay.addWidget(self.hint)

    def show_value(self, value: float, typed: str | None, error: str, modified: bool, default: float) -> None:
        if not self.edit.hasFocus():            # never overwrite what the user is typing
            self.edit.setText(typed if typed is not None else fmt_param(value))
        set_prop(self.edit, "error", bool(error))
        set_prop(self.edit, "changed", modified and not error)
        self.row.set_modified(modified)
        if error:
            self.hint.setText(error)
            self.hint.setProperty("muted", False)
            self.hint.setProperty("hintErr", True)
        else:
            self.hint.setText(f"Default {fmt_param(default)} {self.f.unit}")
            self.hint.setProperty("muted", True)
            self.hint.setProperty("hintErr", False)
        self.hint.style().unpolish(self.hint)
        self.hint.style().polish(self.hint)


class Switch(QAbstractButton):
    """On/off switch (role switch, Space toggles). 36x20 track, 16 px knob; colours from the theme tokens."""

    def __init__(self, theme: ThemeManager, accessible_name: str, checked: bool = False):
        super().__init__()
        self.theme = theme
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(36, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(accessible_name)
        theme.changed.connect(self.update)

    def paintEvent(self, _e) -> None:
        t = self.theme.tokens
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on, en = self.isChecked(), self.isEnabled()
        track = QColor(t.pri if on else t.ctrl)
        if not en:
            track = QColor(t.border)
        p.setPen(QPen(QColor(t.pri), 2) if self.hasFocus() else Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(1, 1, 34, 18), 9, 9)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t.on_pri if on else "#FFFFFF"))
        p.drawEllipse(QRectF(18 if on else 2, 2, 16, 16))
        p.end()


class ColorSquare(QPushButton):
    """24 px colour square; click opens the system colour picker (no colour-name dropdown, approved)."""
    colorChosen = Signal(str)

    def __init__(self, theme: ThemeManager, name: str):
        super().__init__()
        self.theme, self.name, self._hex = theme, name, "#000000"
        self.setFixedSize(24, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._pick)
        theme.changed.connect(self._style)

    def set_color(self, hex_color: str) -> None:
        self._hex = hex_color
        self.setAccessibleName(f"{self.name} colour {hex_color}, choose colour")
        self.setToolTip(f"{self.name}: {hex_color} · choose colour")
        self._style()

    def _style(self) -> None:
        t = self.theme.tokens
        self.setStyleSheet(f"QPushButton {{ background: {self._hex}; border: 1px solid {t.ctrl}; border-radius: 6px; }}"
                           f"QPushButton:focus {{ border: 2px solid {t.pri}; }}"
                           f"QPushButton:disabled {{ border-color: {t.border}; }}")

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(self._hex), self.window(), f"{self.name} colour")
        if c.isValid():
            self.colorChosen.emit(c.name().upper())


class StatusPill(QFrame):
    """Rounded status pill (QSS property 'pill': n | p | ok | warn | err) with a Lucide icon."""
    COLOR = {"n": "muted", "p": "pri", "ok": "success", "warn": "warn", "err": "err"}

    def __init__(self, theme: ThemeManager):
        super().__init__()
        self.theme = theme
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(6)
        self.icon, self.text = QLabel(), QLabel()
        lay.addWidget(self.icon)
        lay.addWidget(self.text)
        self._spec = ("n", "circle-dashed", "")
        theme.changed.connect(lambda: self.set(*self._spec))

    def set(self, kind: str, icon: str, text: str, tooltip: str = "") -> None:
        self._spec = (kind, icon, text)
        self.text.setText(text)
        set_prop(self, "pill", kind)
        from ..icons import pixmap
        self.icon.setPixmap(pixmap(icon, getattr(self.theme.tokens, self.COLOR[kind]), 14))
        self.setAccessibleName(text)
        self.setToolTip(tooltip)                         # cleared too: never a stale explanation


class SampleBlock(QWidget):
    """Active Material Mass [mg] and Surface Area [cm²]: optional, this dataset only. Used in the Data Elaboration
    central card (compact=False: label row + one line) and in the Elaboration Settings dock (compact=True: stacked).
    Both instances show the same ElabState values (mirrored)."""
    textEdited = Signal(str, str)        # key, text

    def __init__(self, theme: ThemeManager, docked: bool = False):
        super().__init__()
        from ..elaboration import SAMPLE_FIELDS, SAMPLE_POPOVER
        self.fields: dict[str, QLineEdit] = {}
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0 if docked else 6, 0, 0 if docked else 10)
        v.setSpacing(6 if not docked else 10)
        if not docked:
            head = QHBoxLayout()
            head.setSpacing(6)
            head.addStretch(1)
            t = QLabel("Sample Informations")
            t.setProperty("muted", True)
            t.setStyleSheet("font-size: 9pt;")
            head.addWidget(t)
            head.addWidget(InfoButton(theme, "Sample values", SAMPLE_POPOVER))
            head.addStretch(1)
            v.addLayout(head)
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addStretch(1)
        for i, (key, label, unit) in enumerate(SAMPLE_FIELDS):
            f = QLineEdit()
            f.setPlaceholderText("Optional")
            f.setAlignment(Qt.AlignmentFlag.AlignRight)
            f.setAccessibleName(f"{label} in {unit}")
            f.textEdited.connect(lambda txt, k=key: self.textEdited.emit(k, txt))
            self.fields[key] = f
            lab = QLabel(label)
            lab.setBuddy(f)
            u = QLabel(unit)
            u.setProperty("muted", True)
            u.setStyleSheet("font-size: 9pt;")
            if docked:
                box = QVBoxLayout()
                box.setSpacing(6)
                lab.setStyleSheet("font-size: 9.75pt;")
                box.addWidget(lab)
                r = QHBoxLayout()
                r.addWidget(f, 1)
                u.setFixedWidth(28)
                r.addWidget(u)
                box.addLayout(r)
                v.addLayout(box)
            else:
                lab.setProperty("muted", True)
                lab.setStyleSheet("font-size: 9pt;")
                f.setFixedWidth(96)
                if i:
                    row.addSpacing(10)
                row.addWidget(lab)
                row.addWidget(f)
                row.addWidget(u)
        if not docked:
            row.addStretch(1)
            v.addLayout(row)
        self.err = QLabel()
        self.err.setProperty("hintErr", True)
        self.err.setAlignment(Qt.AlignmentFlag.AlignLeft if docked else Qt.AlignmentFlag.AlignHCenter)
        self.err.hide()
        v.addWidget(self.err)
        if docked:
            note = QLabel("Applies only to this dataset. Not part of user defaults and not changed by Reset.")
            note.setProperty("muted", True)
            note.setWordWrap(True)
            note.setStyleSheet("font-size: 8.25pt;")
            v.addWidget(note)

    def show_values(self, sample, texts: dict, errors: dict) -> None:
        for key, f in self.fields.items():
            if not f.hasFocus():
                val = getattr(sample, key)
                want = texts.get(key, "" if val is None else f"{val:g}")
                if f.text() != want:
                    f.setText(want)
            set_prop(f, "error", key in errors)
        msg = next(iter(errors.values()), "")
        self.err.setText(msg)
        self.err.setVisible(bool(msg))


class _DividerHandle(QSplitterHandle):
    """Drag handle drawn as a 1 px rule across the splitter with a short grip in the middle. Both turn to the accent
    colour while the pointer is over the handle, so the resize point is visible before it is dragged."""

    def __init__(self, orientation, splitter: "DividerSplitter", theme: ThemeManager):
        super().__init__(orientation, splitter)
        # The handle keeps its own theme: splitter() may return a fresh PySide wrapper of the parent splitter that
        # lacks its Python attributes, so reading splitter().theme in paintEvent could raise AttributeError.
        self.theme = theme
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._hot = False

    def enterEvent(self, event) -> None:
        self._hot = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hot = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        t = self.theme.tokens
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        line = QColor(t.pri if self._hot else t.border)
        grip = QColor(t.pri if self._hot else t.ctrl)
        vertical = self.orientation() == Qt.Orientation.Vertical       # handle between a top and a bottom pane
        if vertical:
            y = r.center().y()
            p.fillRect(QRect(r.left(), y, r.width(), 2 if self._hot else 1), line)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(grip)
            p.drawRoundedRect(QRectF(r.center().x() - 18, y - 2, 36, 5), 2.5, 2.5)
        else:
            x = r.center().x()
            p.fillRect(QRect(x, r.top(), 2 if self._hot else 1, r.height()), line)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(grip)
            p.drawRoundedRect(QRectF(x - 2, r.center().y() - 18, 5, 36), 2.5, 2.5)
        p.end()


class DividerSplitter(QSplitter):
    """QSplitter whose handle shows where the panes can be resized (Capacity and DCIR Results: chart above the
    table). Colours follow the theme; the handle is 12 px tall for an easy grab but only its rule and grip are drawn."""

    def __init__(self, theme: ThemeManager, orientation=Qt.Orientation.Vertical):
        super().__init__(orientation)
        self.theme = theme
        self.setHandleWidth(12)
        theme.changed.connect(self._repaint_handles)

    def createHandle(self) -> QSplitterHandle:
        return _DividerHandle(self.orientation(), self, self.theme)

    def _repaint_handles(self, *_args) -> None:
        for i in range(1, self.count()):
            self.handle(i).update()
