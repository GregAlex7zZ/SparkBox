"""Horizontal table header whose titles wrap onto several lines, so a column can be as narrow as its values instead of
as wide as its title. Long titles wrap at spaces (up to MAX_LINES lines); whatever still does not fit ends with '…' and
the full title is always available as a tooltip. fit_columns() sizes Fixed-width columns from a sample of the displayed
values; ResizeToContents columns get the same behaviour automatically through sectionSizeFromContents.
Limits: wrapping happens only at spaces (a single very long word is elided, never hyphenated). Titles are measured with
the font the theme draws sections with (SECTION_PT, `QHeaderView::section` in theme.py): measuring with any other font
makes the column width and the wrapping disagree, and titles get cut although they fit."""
from __future__ import annotations
from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QHeaderView, QStyle, QStyleOptionHeader, QTableView, QToolTip

MAX_LINES = 3
PREFERRED_LINES = 2     # columns widen (within limits) so that titles need at most this many lines
PAD_X = 32              # QHeaderView::section / QTableView::item horizontal padding in the theme (16 px per side)
PAD_Y = 10
MIN_H = 34              # height of a one-line header (the previous fixed height)
SAMPLE_ROWS = 200
SECTION_PT = 9.0        # font-size of QHeaderView::section in theme.py: what the titles are drawn with


def wrap_lines(text: str, fm: QFontMetrics, width: int, max_lines: int = MAX_LINES) -> list[str]:
    """Greedy word wrap of `text` into at most `max_lines` lines no wider than `width` px. A word longer than the line
    and the last line when text is left over are elided with '…'. Pure layout: the text itself is never changed."""
    width = max(1, int(width))
    # Elide only what does not fit: elidedText measures shaped text (e.g. Inter's 'fi' ligature) slightly differently
    # from horizontalAdvance, and would otherwise cut a line that was measured as fitting.
    elide = lambda s: s if fm.horizontalAdvance(s) <= width else fm.elidedText(s, Qt.TextElideMode.ElideRight, width)
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    cur = ""
    for i, word in enumerate(words):
        cand = f"{cur} {word}" if cur else word
        if fm.horizontalAdvance(cand) <= width:
            cur = cand
            continue
        if cur:
            if len(lines) == max_lines - 1:                    # no line left: the rest ends with '…'
                lines.append(elide(" ".join([cur, *words[i:]])))
                return lines
            lines.append(elide(cur))
        cur = word
    lines.append(elide(cur))
    return lines


def title_width(text: str, fm: QFontMetrics, lines: int = PREFERRED_LINES) -> int:
    """Smallest text width at which `text` wraps onto at most `lines` lines without eliding (never narrower than its
    longest word, never wider than the whole title on one line)."""
    words = text.split()
    if not words:
        return 0
    lo, hi = max(fm.horizontalAdvance(x) for x in words), fm.horizontalAdvance(" ".join(words))
    while lo < hi:
        mid = (lo + hi) // 2
        if len(wrap_lines(text, fm, mid, max_lines=len(words))) <= lines:
            hi = mid
        else:
            lo = mid + 1
    return lo


class WrapHeader(QHeaderView):
    """Drop-in horizontal header: wrapped titles, height following the tallest title, full title as tooltip."""

    def __init__(self, parent=None, max_lines: int = MAX_LINES):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.max_lines = max_lines
        self.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setHighlightSections(False)
        self.setSectionsClickable(False)
        self._fitted = MIN_H                                   # height chosen by update_height()
        self.setFixedHeight(MIN_H)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.update_height)
        self.sectionResized.connect(lambda *_: self._timer.start(0))
        self.sectionCountChanged.connect(lambda *_: self._timer.start(0))
        self.geometriesChanged.connect(lambda: self._timer.start(0))

    def setModel(self, model) -> None:
        old = self.model()
        if old is not None:
            for sig in (old.modelReset, old.headerDataChanged, old.layoutChanged):
                try:
                    sig.disconnect(self._schedule)
                except (RuntimeError, TypeError):
                    pass
        super().setModel(model)
        if model is not None:
            for sig in (model.modelReset, model.headerDataChanged, model.layoutChanged):
                sig.connect(self._schedule)
        self._schedule()

    def _schedule(self, *_a) -> None:
        self._timer.start(0)

    def title(self, section: int) -> str:
        m = self.model()
        t = m.headerData(section, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) if m is not None else None
        return "" if t is None else str(t)

    def lines(self, section: int, width: int | None = None) -> list[str]:
        w = (self.sectionSize(section) if width is None else width) - PAD_X
        return wrap_lines(self.title(section), self.section_metrics(), w, self.max_lines)

    def needed_height(self) -> int:
        n = 1
        for s in range(self.count()):
            if not self.isSectionHidden(s) and self.sectionSize(s) > 0:
                n = max(n, len(self.lines(s)))
        return max(MIN_H, n * self.section_metrics().lineSpacing() + 2 * PAD_Y)

    def update_height(self) -> None:
        """Fit the height to the tallest title; linked headers (side-by-side views) share the tallest height.

        The table reserves room for its header from the header's sizeHint(), not from its fixed height, so the hint
        follows the fitted height and the table re-lays out; otherwise the first rows would slide under a taller
        header until something else triggered a re-layout."""
        group = getattr(self, "group", (self,))
        h = max(x.needed_height() for x in group)
        for x in group:
            if h != x.height() or h != x._fitted:
                x._fitted = h
                x.setFixedHeight(h)
                x.updateGeometry()
                view = x.parentWidget()
                if isinstance(view, QTableView):
                    view.updateGeometries()

    def sizeHint(self) -> QSize:
        s = super().sizeHint()
        return QSize(s.width(), max(s.height(), self._fitted))

    def sectionSizeFromContents(self, section: int) -> QSize:
        """Narrowest width at which the title fits on PREFERRED_LINES lines (it wraps; values may widen the column)."""
        base = super().sectionSizeFromContents(section)
        w = title_width(self.title(section), self.section_metrics()) + PAD_X + 2     # +2: rounding of the paint rect
        return QSize(w, base.height())

    def section_metrics(self) -> QFontMetrics:
        """Metrics of the font the sections are painted with (the widget family at SECTION_PT)."""
        f = QFont(self.font())
        f.setPointSizeF(SECTION_PT)
        return QFontMetrics(f)

    def paintSection(self, painter, rect, section: int) -> None:
        if not rect.isValid():
            return
        opt = QStyleOptionHeader()
        self.initStyleOption(opt)
        opt.rect = rect
        opt.section = section
        opt.text = "\n".join(self.lines(section, rect.width()))
        m = self.model()
        align = m.headerData(section, Qt.Orientation.Horizontal, Qt.ItemDataRole.TextAlignmentRole) if m is not None else None
        opt.textAlignment = Qt.AlignmentFlag(int(align)) if align is not None else self.defaultAlignment()
        painter.save()
        self.style().drawControl(QStyle.ControlElement.CE_Header, opt, painter, self)
        painter.restore()

    def viewportEvent(self, e) -> bool:
        if e.type() == QEvent.Type.ToolTip:
            s = self.logicalIndexAt(e.pos())
            if s >= 0:
                m = self.model()
                tip = m.headerData(s, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole) if m is not None else None
                QToolTip.showText(e.globalPos(), str(tip) if tip else self.title(s), self)
                return True
        return super().viewportEvent(e)


def install(view: QTableView, max_lines: int = MAX_LINES) -> WrapHeader:
    """Replace the view's horizontal header with a WrapHeader, keeping its resize mode and stretch setting."""
    old = view.horizontalHeader()
    h = WrapHeader(view, max_lines)
    h.setStretchLastSection(old.stretchLastSection())
    h.setMinimumSectionSize(48)
    view.setHorizontalHeader(h)
    return h


def link(*headers: WrapHeader) -> None:
    """Headers of views shown side by side (frozen + scrolling) always get the same height."""
    for h in headers:
        h.group = headers
        h.update_height()


def fit_columns(view: QTableView, columns, max_w: int = 200, min_w: int = 64, rows: int = SAMPLE_ROWS) -> None:
    """Fixed-width columns sized to the displayed values of the first `rows` rows and to the title wrapped on
    PREFERRED_LINES lines, clamped to [min_w, max_w]. Wider values are elided by the view as usual."""
    m = view.model()
    if m is None:
        return
    fm = view.fontMetrics()
    header = view.horizontalHeader()
    hfm = header.section_metrics() if isinstance(header, WrapHeader) else header.fontMetrics()
    n = min(m.rowCount(), rows)
    for c in columns:
        title = m.headerData(c, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
        w = title_width(str(title or ""), hfm)
        for r in range(n):
            v = m.data(m.index(r, c), Qt.ItemDataRole.DisplayRole)
            if v is not None:
                w = max(w, fm.horizontalAdvance(str(v)))
        view.setColumnWidth(c, max(min_w, min(max_w, w + PAD_X + 6)))
