"""Help window: the User Guide and the Scientific Methods (docs/help/*.md) rendered in the app.

Qt's own text browser shows the Markdown (headings, lists, tables, code, links); display equations are the
pre-rendered images prepared by app/help_docs.py, so no web engine is needed and the packaged app stays small.
The page is always white with dark ink, whatever the app theme, like a printed manual; the surrounding window
follows the theme. A sidebar lists the sections (click to jump; it follows the reading position) and the search
field finds text (Enter = next, Shift+Enter = previous, wraps around).

Usage: `HelpWindow(theme).show_doc("guide")`. Limits: Markdown features beyond Qt's CommonMark/GitHub subset (for
example footnotes) are shown as plain text; external links open in the system browser.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QImage, QKeySequence, QPalette, QShortcut,
                           QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument, QTextFrameFormat, QTextLength,
                           QTextTable)
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QTextBrowser,
                               QToolButton, QVBoxLayout, QWidget)

from ..fonts import UI_FONT
from ..help_docs import DOCS, FORMULA_DIR, FORMULA_DPI, prepare_markdown
from ..icons import make_icon
from ..theme import ThemeManager
from .controls import SegmentedControl

# Page colours: fixed (a white "paper" page in every theme), close to the app's Light tokens.
INK, HEADING, LINE, HEAD_FILL, CODE_FILL, LINK = "#172c38", "#005f73", "#c6d6dc", "#e7f0f3", "#eff3f5", "#006d85"


class HelpBrowser(QTextBrowser):
    """QTextBrowser that resolves `formula:<key>` images to the pre-rendered PNGs at their logical (96 dpi) size."""

    def __init__(self):
        super().__init__()
        self.setObjectName("HelpBrowser")
        self.setOpenLinks(False)                                    # anchors handled below; http(s) -> system
        self.anchorClicked.connect(self._open)
        self.setStyleSheet(f"#HelpBrowser {{ background: #ffffff; color: {INK}; border: 0; "
                           f"selection-background-color: #cfe8ef; selection-color: {INK}; }}")
        pal = self.palette()
        for role, colour in ((QPalette.ColorRole.Base, "#ffffff"), (QPalette.ColorRole.Text, INK),
                             (QPalette.ColorRole.Link, LINK)):
            pal.setColor(role, QColor(colour))
        self.setPalette(pal)
        self.viewport().setAutoFillBackground(True)
        self.document().setDocumentMargin(36)

    def loadResource(self, kind: int, name: QUrl):
        """Serve `formula:<key>` from docs/help/formulas; everything else as QTextBrowser does."""
        if name.scheme() == "formula":
            image = QImage(str(FORMULA_DIR / f"{name.path()}.png"))
            if not image.isNull():
                image.setDevicePixelRatio(FORMULA_DPI / 96)         # crisp on HiDPI, normal size everywhere
                return image
        return super().loadResource(kind, name)

    def _open(self, url: QUrl) -> None:
        """Open web and mail links in the system browser; other links are ignored."""
        if url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)


def _style_document(doc: QTextDocument) -> list[tuple[int, str, int]]:
    """Typography and table styling after Markdown import; returns the (level, title, position) of every heading."""
    base = QFont(UI_FONT)
    base.setPointSizeF(10.5)
    doc.setDefaultFont(base)
    headings = []
    sizes = {1: 22.0, 2: 15.5, 3: 12.5}
    block = doc.begin()
    while block.isValid():
        cursor = QTextCursor(block)
        fmt = block.blockFormat()
        level = fmt.headingLevel()
        if level:
            fmt.setTopMargin({1: 4, 2: 26, 3: 16}.get(level, 12))
            fmt.setBottomMargin(8)
            cursor.setBlockFormat(fmt)
            ch = QTextCharFormat()
            ch.setFontPointSize(sizes.get(level, 11.5))
            ch.setFontWeight(QFont.Weight.DemiBold)
            ch.setForeground(QBrush(QColor(HEADING if level > 1 else INK)))
            cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
            cursor.mergeCharFormat(ch)
            headings.append((level, block.text().strip(), block.position()))
        elif not cursor.currentTable():
            fmt.setLineHeight(140, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            fmt.setBottomMargin(max(fmt.bottomMargin(), 6))
            it = block.begin()
            only_image = False
            while not it.atEnd():
                frag = it.fragment()
                if frag.charFormat().isImageFormat() and frag.charFormat().toImageFormat().name().startswith("formula:"):
                    only_image = True
                it += 1
            if only_image:                                          # display equation: centred, with air around it
                fmt.setAlignment(Qt.AlignmentFlag.AlignHCenter)
                fmt.setTopMargin(10)
                fmt.setBottomMargin(12)
            cursor.setBlockFormat(fmt)
        block = block.next()
    # Inline code and code blocks: a light tint, like the GitHub rendering.
    block = doc.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.charFormat().fontFixedPitch():
                c = QTextCursor(doc)
                c.setPosition(frag.position())
                c.setPosition(frag.position() + frag.length(), QTextCursor.MoveMode.KeepAnchor)
                ch = QTextCharFormat()
                ch.setBackground(QBrush(QColor(CODE_FILL)))
                ch.setFontFamilies(["Consolas", "Cascadia Mono", "DejaVu Sans Mono", "monospace"])
                ch.setFontPointSize(10)
                c.mergeCharFormat(ch)
            it += 1
        block = block.next()
    # Tables: thin rules, padding, tinted header row.
    for frame in doc.rootFrame().childFrames():
        if isinstance(frame, QTextTable):
            tf = frame.format()
            tf.setBorder(1)
            tf.setBorderBrush(QBrush(QColor(LINE)))
            tf.setBorderStyle(QTextFrameFormat.BorderStyle.BorderStyle_Solid)
            tf.setBorderCollapse(True)
            tf.setCellPadding(7)
            tf.setCellSpacing(0)
            tf.setWidth(QTextLength(QTextLength.Type.PercentageLength, 100))
            tf.setTopMargin(8)
            tf.setBottomMargin(12)
            frame.setFormat(tf)
            for col in range(frame.columns()):
                cell = frame.cellAt(0, col)
                cf = cell.format()
                cf.setBackground(QBrush(QColor(HEAD_FILL)))
                cell.setFormat(cf)
    return headings


class HelpWindow(QWidget):
    """Top-level, non-modal Help window: document switcher, search, contents sidebar and the white page."""

    def __init__(self, theme: ThemeManager, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.theme = theme
        self.setWindowTitle("SparkBox Help")
        self.setObjectName("Root")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.resize(1180, 820)
        self._doc_key: Optional[str] = None
        self._headings: list[tuple[int, str, int]] = []
        self.missing: list[str] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 16)
        lay.setSpacing(10)
        bar = QHBoxLayout()
        self.which = SegmentedControl([(d.key, d.title) for d in DOCS], aria="Document")
        self.which.valueChanged.connect(self.show_doc)
        bar.addWidget(self.which)
        bar.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search")
        self.search.setAccessibleName("Search in this document")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(260)
        self.search.returnPressed.connect(lambda: self.find(backward=False))
        self.search.textEdited.connect(lambda _t: self.found.setText(""))
        self.prev = self._tool("chevron-up", "Previous match (Shift+Enter)", lambda: self.find(backward=True))
        self.next = self._tool("chevron-down", "Next match (Enter)", lambda: self.find(backward=False))
        self.found = QLabel("")
        self.found.setProperty("muted", True)
        self.found.setProperty("small", True)
        for w in (self.found, self.search, self.prev, self.next):
            bar.addWidget(w)
        lay.addLayout(bar)

        body = QHBoxLayout()
        body.setSpacing(12)
        self.toc = QListWidget()
        self.toc.setObjectName("HelpToc")
        self.toc.setAccessibleName("Contents")
        self.toc.setFixedWidth(270)
        self.toc.setWordWrap(True)
        self.toc.itemClicked.connect(self._jump)
        self.page = HelpBrowser()
        self.page.verticalScrollBar().valueChanged.connect(self._follow)
        body.addWidget(self.toc)
        body.addWidget(self.page, 1)
        lay.addLayout(body, 1)

        QShortcut(QKeySequence("Shift+Return"), self.search, activated=lambda: self.find(backward=True))
        theme.changed.connect(self._icons)
        self._icons()

    def _tool(self, icon: str, tip: str, slot) -> QToolButton:
        """Small square icon button (search previous / next) styled like the tab-band chevrons."""
        b = QToolButton()
        b.setObjectName("ChevButton")
        b.setFixedSize(30, 30)
        b.setIconSize(QSize(16, 16))
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.setProperty("icon_name", icon)
        b.clicked.connect(slot)
        return b

    def _icons(self, *_args) -> None:
        """Recolour the search buttons for the current theme."""
        t = self.theme.tokens
        for b in (self.prev, self.next):
            b.setIcon(make_icon(b.property("icon_name"), t.muted, size=16))

    # ---------------------------------------------------------------- documents
    def show_doc(self, key: str) -> None:
        """Show the document `key` (\"guide\" or \"methods\"), loading and styling it only when it changes."""
        doc = next((d for d in DOCS if d.key == key), DOCS[0])
        self.which.set_value(doc.key)
        if doc.key == self._doc_key:
            return
        self._doc_key = doc.key
        try:
            text = doc.path.read_text(encoding="utf-8")
        except OSError as error:
            text = f"# {doc.title}\n\nThe document could not be opened: {error}."
        text, self.missing = prepare_markdown(text)
        self.page.document().setMarkdown(text, QTextDocument.MarkdownFeature.MarkdownDialectGitHub)
        self._headings = _style_document(self.page.document())
        self.toc.clear()
        for level, title, pos in self._headings:
            if level < 2 or level > 3:
                continue
            item = QListWidgetItem(title if level == 2 else "    " + title)
            item.setData(Qt.ItemDataRole.UserRole, pos)
            if level == 2:
                f = item.font()
                f.setWeight(QFont.Weight.DemiBold)
                item.setFont(f)
            self.toc.addItem(item)
        self.page.moveCursor(QTextCursor.MoveOperation.Start)       # searches start from the top of the new document
        self.page.verticalScrollBar().setValue(0)
        self.found.setText("")
        self.setWindowTitle(f"{doc.title} — SparkBox Help")

    def _block_top(self, pos: int) -> int:
        """Vertical position, in document pixels, of the block containing character `pos`."""
        block = self.page.document().findBlock(pos)
        return int(self.page.document().documentLayout().blockBoundingRect(block).top())

    def _jump(self, item: QListWidgetItem) -> None:
        """Scroll the page so the clicked section heading is at the top."""
        self.page.verticalScrollBar().setValue(self._block_top(item.data(Qt.ItemDataRole.UserRole)))

    def _follow(self, value: int) -> None:
        """Highlight the section being read (the last heading at or above the top of the page)."""
        row = -1
        for i in range(self.toc.count()):
            if self._block_top(self.toc.item(i).data(Qt.ItemDataRole.UserRole)) <= value + 24:
                row = i
        self.toc.blockSignals(True)
        self.toc.setCurrentRow(row)
        self.toc.blockSignals(False)

    # ---------------------------------------------------------------- search
    def find(self, backward: bool = False) -> bool:
        """Select the next (or previous) match of the search text, wrapping around once; False if absent."""
        text = self.search.text().strip()
        if not text:
            return False
        flags = QTextDocument.FindFlag.FindBackward if backward else QTextDocument.FindFlag(0)
        if not self.page.find(text, flags):
            cursor = self.page.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End if backward else QTextCursor.MoveOperation.Start)
            self.page.setTextCursor(cursor)
            if not self.page.find(text, flags):
                self.found.setText("Not found")
                return False
            self.found.setText("Wrapped around")
        else:
            self.found.setText("")
        return True
