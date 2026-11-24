"""Combo boxes whose text is never cut: a closed box as wide as the chosen item and a drop-down list as wide as its
longest item.

Qt sizes a combo box's drop-down list from the closed box and from the combo's own padding, not from the list's: with
the theme's item padding (``QComboBox QAbstractItemView::item { padding: 0 10px }``) long items such as
"Discharge Specific Capacity [mAh/g]" were shown half cut. Two tools fix it:

* ``install_popup_fit(app)`` installs, once per application, an event filter that widens every combo-box drop-down
  list (any QComboBox, in any window or dialog) to its longest item just before it is shown, keeping it on screen.
* ``FitComboBox`` is a QComboBox whose size hint follows the text of the current item, so a selector in a toolbar
  shows the whole chosen name (use it inside a ``FlowLayout`` so a long choice wraps the toolbar instead of squeezing
  its neighbours).

Usage: ``MainWindow`` calls ``install_popup_fit`` on start; build axis or option selectors with ``FitComboBox()``
where a fixed width would cut the chosen name.
Limits: the list is never made wider than the screen; an item longer than the screen is still elided there.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QSize
from PySide6.QtWidgets import QApplication, QComboBox, QStyle, QStyleOptionComboBox, QWidget

ITEM_PADDING = 2 * 10        # horizontal padding of one list item in the theme (app/theme.py)
SLACK = 6                    # rounding of fractional font advances and the focus frame


def text_width(combo: QComboBox, index: int, metrics=None) -> int:
    """Pixel width of the text and icon of item ``index`` of ``combo`` (0 for an invalid index)."""
    if index < 0 or index >= combo.count():
        return 0
    fm = metrics or combo.fontMetrics()
    width = fm.horizontalAdvance(combo.itemText(index))
    if not combo.itemIcon(index).isNull():
        width += combo.iconSize().width() + 4
    return width


def popup_width(combo: QComboBox, popup: QWidget) -> int:
    """Width the drop-down ``popup`` of ``combo`` needs to show its longest item whole: the item text, the theme's
    item padding and the popup's own frame, padding and scroll bar (whatever the popup currently spends around its
    viewport)."""
    view = combo.view()
    fm = view.fontMetrics()
    widest = max((text_width(combo, i, fm) for i in range(combo.count())), default=0)
    chrome = popup.width() - view.viewport().width()
    if combo.count() > combo.maxVisibleItems() and not view.verticalScrollBar().isVisible():
        chrome += combo.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent, None, view)
    return widest + ITEM_PADDING + chrome + SLACK


def fit_popup(combo: QComboBox, popup: QWidget) -> None:
    """Widen ``popup`` (the drop-down container of ``combo``) to ``popup_width`` when it is narrower, moving it left
    when it would leave the screen. Never narrows it: the list stays at least as wide as the closed box."""
    need = popup_width(combo, popup)
    if need <= popup.width():
        return
    geo = popup.geometry()
    screen = (popup.screen() or combo.screen())
    if screen is not None:
        area = screen.availableGeometry()
        need = min(need, area.width())
        if geo.x() + need > area.right() + 1:
            geo.moveLeft(max(area.left(), area.right() + 1 - need))
    geo.setWidth(need)
    popup.setGeometry(geo)


class _PopupFit(QObject):
    """Application event filter: fits every combo-box drop-down list when it is about to be shown."""

    def eventFilter(self, obj, event) -> bool:      # noqa: N802 (Qt API)
        if event.type() == QEvent.Type.Show and isinstance(obj, QWidget) \
                and obj.metaObject().className() == "QComboBoxPrivateContainer":
            combo = obj.parentWidget()
            if isinstance(combo, QComboBox):
                fit_popup(combo, obj)
        return False


def install_popup_fit(app: QApplication) -> None:
    """Install the drop-down fitting filter on ``app``; calling it again does nothing."""
    if app.property("sparkboxPopupFit"):
        return
    fitter = _PopupFit(app)
    app.installEventFilter(fitter)
    app.setProperty("sparkboxPopupFit", True)


class FitComboBox(QComboBox):
    """A QComboBox whose closed box is exactly as wide as its current item (never below ``min_width``), so the chosen
    name is always readable; its drop-down list is fitted by ``install_popup_fit``. The width changes with the
    selection, so place it where the layout can absorb that (a ``FlowLayout`` or a row with a stretch)."""

    def __init__(self, min_width: int = 96, parent: QWidget | None = None):
        super().__init__(parent)
        self._min_width = min_width
        self._hinted = QSize()
        self._asked = QSize()
        self.currentIndexChanged.connect(lambda _i: self.updateGeometry())

    def paintEvent(self, event) -> None:            # noqa: N802 (Qt API)
        # Pages often refill and select with blockSignals(True), which hides the index change from the connection
        # above: a repaint with a different current text then asks the layout for the new width.
        # Ask once per change (never on every paint, which could loop with a layout that keeps its cached hint).
        fit = self._fit()
        if fit != self._hinted and fit != self._asked:
            self._asked = fit
            self.updateGeometry()
        super().paintEvent(event)

    def _fit(self) -> QSize:
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        content = QSize(text_width(self, self.currentIndex()) + 2, 0)
        width = self.style().sizeFromContents(QStyle.ContentsType.CT_ComboBox, opt, content, self).width()
        return QSize(max(width, self._min_width), super().sizeHint().height())

    def sizeHint(self) -> QSize:                    # noqa: N802 (Qt API)
        self._hinted = self._fit()
        return self._hinted

    def minimumSizeHint(self) -> QSize:             # noqa: N802 (Qt API)
        return self.sizeHint()
