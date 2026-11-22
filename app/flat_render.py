"""Shared drawing helpers for Render All Points (production and demo adapters): the transparent, high-resolution
data layer that the charts overlay on their own axes (see backend.FlatStyle). Worker-thread safe (QImage only)."""
from __future__ import annotations
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from .backend import FlatStyle


def data_layer(style: FlatStyle):
    """Transparent, antialiased image of the plot area for Render All Points: (image, painter, plot rect in logical
    px, device width, device height). Resolution: max(device pixel ratio, dpi / 96) device px per logical px, so the
    layer is at least `style.dpi` (300) when the chart is exported at that resolution."""
    logical_width, logical_height = max(16, int(style.width_px)), max(16, int(style.height_px))
    scale = max(float(style.device_pixel_ratio), float(style.dpi) / 96.0)
    width, height = int(round(logical_width * scale)), int(round(logical_height * scale))
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(scale, scale)
    return image, painter, QRectF(0.0, 0.0, float(logical_width), float(logical_height)), width, height


def dot_pen(color: str) -> QPen:
    """Round 3 px dots: the same marker size as the interactive charts."""
    pen = QPen(QColor(color), 3.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    return pen
