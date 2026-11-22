"""Render All Points inside the live charts.

The adapter returns a transparent, high-resolution DATA LAYER of the plot area (backend.FlatStyle). `FlatLayer` places
it in a pyqtgraph ViewBox exactly over the data rectangle it was drawn for, so the chart keeps its own axes, tick
spacing, number formats, grid and legend: the flat view looks like the interactive one, only with every point.
`export_png` renders a whole chart (axes and text repainted at scale, the data layer from its full resolution) at
300 dpi.

Limits: the layer is drawn for one data rectangle; it is only correct while the view shows that rectangle, which is
why the charts are not interactive in the flat mode."""
from __future__ import annotations
import math
from typing import Optional
import pyqtgraph as pg
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QRegion, QTransform
from PySide6.QtWidgets import QGraphicsPixmapItem, QWidget

EXPORT_DPI = 300


class FlatLayer:
    """A pixmap item in `vb` that maps the data layer onto the data rectangle it was rendered for."""

    def __init__(self, vb: pg.ViewBox):
        self.vb = vb
        self.item = QGraphicsPixmapItem()
        self.item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.item.setZValue(5)                               # above the grid, below crosshair / markers
        self.item.hide()
        vb.addItem(self.item, ignoreBounds=True)

    @property
    def visible(self) -> bool:
        return self.item.isVisible()

    def show(self, png: bytes, rect: Optional[QRectF] = None) -> bool:
        """Show `png` over `rect` (data coordinates; default: the range the view box shows now)."""
        pm = QPixmap()
        if not png or not pm.loadFromData(png, "PNG") or pm.isNull():
            self.hide()
            return False
        r = rect if rect is not None else self.vb.viewRect()
        if r.width() == 0 or r.height() == 0:
            self.hide()
            return False
        self.item.setPixmap(pm)
        # image row 0 is the TOP of the plot (largest y); the view box y axis grows upwards
        self.item.setTransform(QTransform.fromScale(r.width() / pm.width(), -r.height() / pm.height()))
        self.item.setPos(r.left(), r.top() + r.height())  # viewRect: top = smallest y
        self.item.show()
        return True

    def hide(self) -> None:
        self.item.hide()
        self.item.setPixmap(QPixmap())


def export_png(widget: QWidget, dpi: int = EXPORT_DPI, background: str = "#FFFFFF") -> bytes:
    """PNG of `widget` (a chart and its visible overlays, e.g. a legend) at `dpi`, logical px being 96 dpi. Text,
    axes and lines are painted again at that scale (not enlarged pixels); the data layer comes from its own full
    resolution."""
    scale = max(1.0, dpi / 96.0)
    image = QImage(int(round(widget.width() * scale)), int(round(widget.height() * scale)), QImage.Format.Format_ARGB32)
    image.fill(QColor(background))
    painter = QPainter(image)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                           | QPainter.RenderHint.SmoothPixmapTransform)
    painter.scale(scale, scale)
    QWidget.render(widget, painter, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)  # also for QGraphicsView
    painter.end()
    dpm = int(math.ceil(dpi / 0.0254))
    image.setDotsPerMeterX(dpm)
    image.setDotsPerMeterY(dpm)
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    buf.close()
    return bytes(data)
