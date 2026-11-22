"""Mascot placement next to the "SparkBox" brand label.

Purpose: give the mascot as much room as the header allows. The mascot is a free child of the sidebar column (not
part of its layout), so it may use the column's right margin. Its height is the largest that fits between the end of
the brand text and the column's right edge, capped at the brand row's height.

Usage::

    BrandSlot(mascot, brand_label, column)       # keeps the mascot placed from then on

The slot re-measures whenever the brand label is resized, re-styled (theme or font change) or shown, so the mascot
never covers the brand text, whatever font is in use.

Centred mode (``centered = True`` then ``place()``, used with the collapsed sidebar): the brand shows no text and the mascot is
centred horizontally in the column, as large as the column width and the brand row allow.

Limits: the mascot is never smaller than ``MIN_H`` px; if the brand text were so wide that even that does not fit,
the mascot is hidden rather than drawn over the text.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QLabel, QWidget

from .widget import Mascot

MIN_H = 36            # below this the drawing loses its details
EDGE_PAD = 2          # px kept free at the column's right edge (the tab band starts right after it)
TEXT_GAP = 2          # px kept free after the brand text


class BrandSlot(QObject):
    """Places ``mascot`` at the bottom right of ``brand``'s row inside ``column`` and sizes it to fit."""

    _WATCHED = (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show, QEvent.Type.FontChange,
                QEvent.Type.StyleChange, QEvent.Type.Polish)

    def __init__(self, mascot: Mascot, brand: QLabel, column: QWidget, max_h: int | None = None):
        super().__init__(mascot)
        self.mascot, self.brand, self.column = mascot, brand, column
        self.max_h = max_h or brand.height()
        self.enabled = True
        self.centered = False
        mascot.setParent(column)
        brand.installEventFilter(self)
        column.installEventFilter(self)
        self.place()

    def set_enabled(self, enabled: bool) -> None:
        """Show (and place) or hide the mascot, e.g. with the collapsed sidebar."""
        self.enabled = enabled
        self.place()

    def text_right(self) -> int:
        """Right end of the brand text, in column coordinates."""
        self.brand.ensurePolished()
        m = self.brand.contentsMargins()
        width = self.brand.fontMetrics().horizontalAdvance(self.brand.text())
        return self.brand.x() + m.left() + width

    def place(self) -> None:
        """Size and move the mascot to the space left of the column's right edge."""
        if not self.enabled:
            self.mascot.hide()
            return
        right = self.column.width() - EDGE_PAD
        room = right - EDGE_PAD if self.centered else right - (self.text_right() + TEXT_GAP)
        h = min(self.max_h, Mascot.height_for_width(room))
        if h < MIN_H:
            self.mascot.hide()
            return
        if h != self.mascot.height():
            self.mascot.set_height(h)
        bottom = self.brand.y() + self.brand.height()
        x = (self.column.width() - self.mascot.width()) // 2 if self.centered else right - self.mascot.width()
        self.mascot.move(x, bottom - self.mascot.height())
        self.mascot.show()
        self.mascot.raise_()

    def eventFilter(self, obj, event) -> bool:
        if event.type() in self._WATCHED:
            self.place()
        return False
