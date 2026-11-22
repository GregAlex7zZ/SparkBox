"""The Data Explorer chart: PyQtGraph with Plotly-like gestures, an independent right Y axis, a
fixed palette and the overlays of every state. The chart never computes data: it draws the real points the
adapter returned (ExplorerView) and reports the ranges the user asks for (viewportRequested).

Gestures:
  left drag in the plot = Box Zoom (dimmed outside, dashed rectangle; Esc cancels); Shift+drag or Pan tool = Pan;
  wheel in the plot = zoom every axis around the pointer ; wheel over the bottom / left / right axis = zoom
  X / Y1 / Y2 only, around the pointer; left drag on an axis = pan that axis only (e.g. drag the right axis down to
  move the Y2 curve and its zero down) ; a rectangle thinner than 8 px zooms one direction only; double-click
  does nothing (Reset View is the button).
"""
from __future__ import annotations
from typing import Optional
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainterPath, QPen
from PySide6.QtWidgets import (QFrame, QGraphicsItem, QGraphicsPathItem, QGridLayout, QHBoxLayout, QLabel,
                               QProgressBar, QPushButton, QVBoxLayout, QWidget)
from .. import pg_compat
from ..explorer import (CHART, Range, Viewport, ascending_x, box_zoom, pan_x, pan_y, wheel_zoom)
from ..fonts import UI_FONT
from ..formatting import fmt_time, sig4
from ..icons import pixmap
from .flat_layer import EXPORT_DPI, FlatLayer, export_png

pg.setConfigOptions(antialias=True, useOpenGL=False)      # OpenGL off by default
pg_compat.install()                                         # no QStyleHints unique-connection warning per chart
STEP_NAMES = {0: "Rest", 1: "CC", 2: "CV", 3: "Other"}     # names of the engine Step enum; display only
SNAP_PX = 30                                                # hover: nearest plotted real point within 30 px
FONT_FAMILY = UI_FONT


def _font(px: int, weight: int = 400) -> QFont:
    f = QFont(FONT_FAMILY)
    # Point sizes preserve a valid inherited QFont state during Qt teardown
    # (BUG-001); the design's pixel values map to 0.75 pt at 96 DPI.
    f.setPointSizeF(px * 0.75)
    f.setWeight(QFont.Weight(weight))
    f.setHintingPreference(QFont.HintingPreference.PreferVerticalHinting)        # same rendering as the UI text
    return f


class _WheelAxis(pg.AxisItem):
    """Axis that zooms only its own axis with the wheel and, when `on_drag` is given, pans only its own axis with a
    left drag (no PyQtGraph default behaviour). on_drag(orientation, ev) receives every pyqtgraph drag event of a drag
    that started on the axis band; without it drags are ignored.

    The resize-arrow cursor is shown only while the pointer is over the axis band and `active()` is true (e.g. not on
    a static chart). It is set on hover, never on the item: with the grid on, the axis rect covers the whole plot area,
    so a fixed item cursor turned the pointer into an up/down arrow everywhere in the chart."""

    def __init__(self, orientation: str, on_wheel, on_drag=None, active=None, **kw):
        super().__init__(orientation, **kw)
        self._on_wheel = on_wheel
        self._on_drag = on_drag
        self._active = active or (lambda: True)
        self._dragging = False
        self._drag_cursor = (Qt.CursorShape.SizeHorCursor if orientation in ("bottom", "top")
                             else Qt.CursorShape.SizeVerCursor)

    def hoverEvent(self, ev) -> None:
        over = (self._on_drag is not None and not ev.isExit() and self._active() and self._on_axis(ev))
        if over:
            self.setCursor(self._drag_cursor)
        elif self.hasCursor() and not self._dragging:
            self.unsetCursor()

    def _on_axis(self, ev) -> bool:
        """True only over the axis band itself. With the grid on, the axis bounding rect also covers the whole plot
        area; events there belong to the ViewBox (clicks, picking), not to the axis."""
        return self.mapRectFromParent(self.geometry()).contains(ev.pos())

    def wheelEvent(self, ev) -> None:
        if not self._on_axis(ev):
            ev.ignore()
            return
        d = ev.delta() if hasattr(ev, "delta") else 0
        if d:
            self._on_wheel(self.orientation, d / 120.0, ev.scenePos())
        ev.accept()

    def mouseDragEvent(self, ev) -> None:
        if self._on_drag is None or ev.button() != Qt.MouseButton.LeftButton:
            ev.ignore()
            return
        if ev.isStart():
            self._dragging = self.mapRectFromParent(self.geometry()).contains(ev.buttonDownPos())
        if not self._dragging:
            ev.ignore()
            return
        ev.accept()
        self._on_drag(self.orientation, ev)
        if ev.isFinish():
            self._dragging = False

    def mouseClickEvent(self, ev) -> None:
        if self._on_axis(ev):
            ev.accept()
        else:
            ev.ignore()


class _PlotVB(pg.ViewBox):
    def __init__(self, chart: "ChartWidget"):
        super().__init__(enableMenu=False)
        self.chart = chart
        self.setMouseEnabled(False, False)

    def wheelEvent(self, ev, axis=None) -> None:
        d = ev.delta() if hasattr(ev, "delta") else 0
        if d:
            self.chart._plot_wheel(d / 120.0, ev.scenePos())   # zoom every axis around the pointer
        ev.accept()

    def mouseClickEvent(self, ev) -> None:
        # A chart consumer may use a real rendered point as a selection.  This
        # deliberately reuses the hover hit-test; no interpolated point is ever
        # manufactured by the widget.
        if ev.button() == Qt.MouseButton.LeftButton and self.chart._hover_row >= 0:
            self.chart.pointClicked.emit(self.chart._hover_row)
        ev.accept()                                         # no context menu, no double-click reset

    def mouseDragEvent(self, ev, axis=None) -> None:
        self.chart._drag(ev)


class _PassiveVB(pg.ViewBox):
    """Right-axis view box: drawn behind the main one and never takes mouse input."""

    def __init__(self):
        super().__init__(enableMenu=False)
        self.setMouseEnabled(False, False)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def wheelEvent(self, ev, axis=None) -> None:
        ev.ignore()


def _card_style() -> str:
    return (f"background: #FFFFFF; border: 1px solid {CHART['frame']}; border-radius: 12px; color: {CHART['text']};"
            f"font-family: {FONT_FAMILY};")


class ChartWidget(QWidget):
    viewportRequested = Signal(object)          # Viewport chosen by a gesture
    plotResized = Signal(int, int)              # plot-area size in px (sampling hint)
    hoverRow = Signal(int)                      # source row under the pointer (details requested by the page)
    pointClicked = Signal(int)                   # an already hit-tested, real source row
    overlayButton = Signal(str)                 # "clear_filter" | "reset_view"
    cancelRender = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ChartSurface")
        self.setStyleSheet(f"#ChartSurface {{ background: {CHART['bg']}; border: 1px solid {CHART['frame']};"
                           f" border-radius: 10px; }}")
        self.setMinimumSize(360, 220)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.interactive = True
        self.marker_size = 3                     # px; dense raw data keeps small markers, per-cycle charts enlarge it
        self.pan_mode = False
        self.point_tracking = True
        # Capacity uses cycles as its source identifiers.  Explorer keeps the
        # historical canonical-row wording and metadata line.
        self.row_title = "Canonical row"
        self.meta_line = True
        self._x_key = "time_s"
        self._rows: list[np.ndarray] = []
        self._xy: list[tuple[np.ndarray, np.ndarray]] = []
        self._drag_state: Optional[dict] = None
        self._pan_pens: Optional[list] = None              # pens saved while a pan draws thinner lines
        self._axis_drag_start = None                # ranges when an axis drag started
        self._hover_row = -1
        self._labels = ("", "", None)
        self._colors = (CHART["y1"], CHART["y2"])

        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        self.vb = _PlotVB(self)
        axes = {k: _WheelAxis(k, self._axis_wheel, self._axis_drag, lambda: self.interactive)
                for k in ("bottom", "left", "right")}
        self.pw = pg.PlotWidget(viewBox=self.vb, axisItems=axes, background=CHART["bg"])
        self.pw.viewport().installEventFilter(self)
        self.pw.setFrameShape(QFrame.Shape.NoFrame)
        lay.addWidget(self.pw)
        self.pi = self.pw.getPlotItem()
        self.pi.hideButtons()
        self.pi.setMenuEnabled(False)
        self.pi.showAxis("right")
        self.pi.layout.setContentsMargins(8, 12, 8, 6)
        for name in ("bottom", "left", "right"):
            ax = self.pi.getAxis(name)
            ax.setPen(pg.mkPen(CHART["frame"]))
            ax.setTextPen(pg.mkPen(CHART["tick"]))
            ax.setTickFont(_font(11))
        self.pi.showGrid(x=True, y=True, alpha=0.35)        # grid follows the axis pen: light, fixed colour
        self.vb2 = _PassiveVB()
        self.pi.scene().addItem(self.vb2)
        self.vb2.setZValue(-10)                             # behind the Y1 view box: Y1 is drawn on top, mouse goes to Y1
        self.pi.getAxis("right").linkToView(self.vb2)
        self.vb2.setXLink(self.vb)
        self.vb.sigResized.connect(self._sync_vb2)
        self.vb.setCursor(Qt.CursorShape.CrossCursor)
        self.c1 = pg.PlotDataItem(connect="finite")
        self.c2 = pg.PlotDataItem(connect="finite")
        for c in (self.c1, self.c2):
            c.setDownsampling(ds=1, auto=False)             # no unaccounted decimation: the counter stays exact
            # Keep each drawn line as a device-resolution image and redraw it only when the line itself changes (data,
            # pen, zoom or pan). Moving the box-zoom rectangle, the hover crosshair, markers or tooltips then reuses
            # the image: on dense GITT traces a repaint dropped from ~175 ms to ~25 ms. The image holds the exact
            # pixels (same antialiasing) and costs about one plot-area bitmap per curve in memory. Symbols are not
            # cached: pyqtgraph places them at sub-pixel positions that a cached image would shift by a few levels.
            c.curve.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.vb.addItem(self.c1)
        self.vb2.addItem(self.c2)
        self.legend = pg.LegendItem(offset=(10, 8), brush=pg.mkBrush(255, 255, 255, 235), pen=pg.mkPen(CHART["frame"]),
                                    labelTextColor=CHART["text"], labelTextSize="9pt")
        self.legend.setParentItem(self.vb)
        # hover readout (inside the chart: fixed palette)
        self.cross = pg.InfiniteLine(angle=90, pen=pg.mkPen(CHART["cross"], width=1, style=Qt.PenStyle.DashLine))
        self.cross.setZValue(20)
        self.vb.addItem(self.cross, ignoreBounds=True)
        self.selected = pg.InfiniteLine(angle=90, pen=pg.mkPen(CHART["y1"], width=2))
        self.selected.setZValue(19)
        self.vb.addItem(self.selected, ignoreBounds=True)
        self.selected.hide()
        self.m1 = pg.ScatterPlotItem(size=9, pen=pg.mkPen(CHART["y1"], width=2), brush=pg.mkBrush("#FFFFFF"))
        self.m2 = pg.ScatterPlotItem(size=9, pen=pg.mkPen(CHART["y2"], width=2), brush=pg.mkBrush("#FFFFFF"))
        self.vb.addItem(self.m1, ignoreBounds=True)
        self.vb2.addItem(self.m2, ignoreBounds=True)
        self._hide_hover()
        self.pi.scene().sigMouseMoved.connect(self._mouse_moved)
        # box-zoom overlay (pixel coordinates of the view box)
        self.dim = QGraphicsPathItem(self.vb)
        self.dim.setBrush(QBrush(QColor(37, 59, 68, 26)))
        self.dim.setPen(QPen(Qt.PenStyle.NoPen))
        self.box = QGraphicsPathItem(self.vb)
        self.box.setPen(pg.mkPen(CHART["dim"], width=1, style=Qt.PenStyle.DashLine))
        for it in (self.dim, self.box):
            it.setZValue(1e6)
            it.hide()
        self._build_overlays()

    # ---------------------------------------------------------------- overlays (children of this widget)
    def _build_overlays(self) -> None:
        self.tip = QFrame(self)
        self.tip.setStyleSheet(f"QFrame {{ background: #FFFFFF; border: 1px solid {CHART['frame']}; border-radius: 8px; }}"
                               f"QLabel {{ border: 0; background: transparent; color: {CHART['text']}; font-family: {FONT_FAMILY};"
                               f" font-size: 9pt; }}")
        tl = QVBoxLayout(self.tip)
        tl.setContentsMargins(10, 8, 10, 8)
        tl.setSpacing(5)
        self.tip_head = QLabel()
        self.tip_head.setStyleSheet("font-weight: 500;")
        self.tip_grid = QGridLayout()
        self.tip_grid.setHorizontalSpacing(14)
        self.tip_grid.setVerticalSpacing(2)
        self.tip_rows = [(QLabel(), QLabel()) for _ in range(3)]
        for i, (a, b) in enumerate(self.tip_rows):
            self.tip_grid.addWidget(a, i, 0)
            self.tip_grid.addWidget(b, i, 1)
        self.tip_meta = QLabel()
        self.tip_meta.setStyleSheet(f"color: {CHART['tick']};")
        tl.addWidget(self.tip_head)
        tl.addLayout(self.tip_grid)
        tl.addWidget(self.tip_meta)
        self.tip.hide()

        self.chip = QLabel(self)
        self.chip.setStyleSheet(f"background: {CHART['chip_bg']}; color: {CHART['chip_fg']}; border-radius: 12px;"
                                f" padding: 0 9px; font-family: {FONT_FAMILY}; font-size: 9pt; font-weight: 500;")
        self.chip.setFixedHeight(24)
        self.chip.hide()

        self.msg = QFrame(self)
        self.msg.setStyleSheet(f"QLabel {{ color: {CHART['text']}; font-family: {FONT_FAMILY}; background: transparent; }}")
        ml = QVBoxLayout(self.msg)
        ml.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ml.setSpacing(8)
        self.msg_icon, self.msg_title, self.msg_text = QLabel(), QLabel(), QLabel()
        self.msg_title.setStyleSheet("font-size: 11.25pt; font-weight: 500;")
        self.msg_text.setStyleSheet(f"font-size: 9pt; color: {CHART['tick']};")
        self.msg_text.setWordWrap(True)
        self.msg_text.setFixedWidth(340)   # wrapped labels in a centred layout otherwise clip
        self.msg_btn = QPushButton()
        self.msg_btn.setStyleSheet(f"QPushButton {{ height: 30px; padding: 0 12px; border: 1px solid #6F8691; border-radius: 8px;"
                                   f" background: #FFFFFF; color: {CHART['text']}; font-family: {FONT_FAMILY}; }}"
                                   f"QPushButton:focus {{ border: 2px solid {CHART['y1']}; }}")
        self.msg_btn.clicked.connect(lambda: self.overlayButton.emit(self.msg_btn.property("action") or ""))
        for w in (self.msg_icon, self.msg_title, self.msg_text, self.msg_btn):
            w.setAlignment(Qt.AlignmentFlag.AlignHCenter) if isinstance(w, QLabel) else None
            ml.addWidget(w, 0, Qt.AlignmentFlag.AlignHCenter)
        self.msg.hide()

        self.prog = QFrame(self)
        self.prog.setObjectName("ProgCard")
        self.prog.setStyleSheet(f"#ProgCard {{ {_card_style()} }} QLabel {{ color: {CHART['text']}; background: transparent;"
                                f" font-family: {FONT_FAMILY}; }}"
                                f"QProgressBar {{ background: {CHART['grid']}; border: 0; border-radius: 4px; max-height: 8px;"
                                f" min-height: 8px; }} QProgressBar::chunk {{ background: {CHART['y1']}; border-radius: 4px; }}")
        pl = QVBoxLayout(self.prog)
        pl.setContentsMargins(18, 16, 18, 16)
        pl.setSpacing(10)
        top = QHBoxLayout()
        t = QLabel("Rendering All Points")
        t.setStyleSheet("font-size: 10.5pt; font-weight: 500;")
        self.prog_cancel = QPushButton("Cancel")
        self.prog_cancel.setStyleSheet(self.msg_btn.styleSheet())
        self.prog_cancel.clicked.connect(self.cancelRender)
        top.addWidget(t)
        top.addStretch(1)
        top.addWidget(self.prog_cancel)
        self.prog_bar = QProgressBar()
        self.prog_bar.setTextVisible(False)
        row = QHBoxLayout()
        self.prog_left, self.prog_right = QLabel(), QLabel()
        for l in (self.prog_left, self.prog_right):
            l.setStyleSheet(f"font-size: 9pt; color: {CHART['tick']};")
        row.addWidget(self.prog_left)
        row.addStretch(1)
        row.addWidget(self.prog_right)
        note = QLabel("Cancel keeps the interactive chart exactly as it is.")
        note.setStyleSheet(f"font-size: 9pt; color: {CHART['tick']};")
        pl.addLayout(top)
        pl.addWidget(self.prog_bar)
        pl.addLayout(row)
        pl.addWidget(note)
        self.prog.setFixedWidth(420)
        self.prog.hide()
        self.veil = QWidget(self)
        self.veil.setStyleSheet("background: rgba(255,255,255,184);")
        self.veil.hide()
        self.flat = FlatLayer(self.vb)                       # every point, drawn under the chart's own axes

    # ---------------------------------------------------------------- data
    def set_style(self, x_key: str, labels: tuple[str, str, Optional[str]], colors: tuple[str, str],
                  legend: bool, style: str) -> None:
        self._x_key, self._labels, self._colors = x_key, labels, colors
        self._pan_pens = None                    # a new style sets new pens: nothing to restore after a pan
        lab = {"font-family": FONT_FAMILY, "font-size": "9pt", "font-weight": "500"}
        self.pi.setLabel("bottom", labels[0], color=CHART["text"], **lab)
        self.pi.setLabel("left", labels[1], color=colors[0], **lab)
        y2 = labels[2] is not None
        self.pi.getAxis("right").setVisible(y2)
        if y2:
            self.pi.setLabel("right", labels[2], color=colors[1], **lab)
        lines, marks = style in ("lines", "lines_markers"), style in ("markers", "lines_markers")
        for c, col in ((self.c1, colors[0]), (self.c2, colors[1])):
            c.setPen(pg.mkPen(col, width=1.5) if lines else None)
            c.setSymbol("o" if marks else None)
            c.setSymbolSize(self.marker_size)
            c.setSymbolBrush(pg.mkBrush(col))
            c.setSymbolPen(None)
            c.setClipToView(ascending_x(x_key))             # allowed only for ascending X
        self.m1.setPen(pg.mkPen(colors[0], width=2))
        self.m2.setPen(pg.mkPen(colors[1], width=2))
        self.c2.setVisible(y2)
        self.legend.clear()
        if legend:
            self.legend.addItem(self.c1, f"{labels[1]} · Y1")
            if y2:
                self.legend.addItem(self.c2, f"{labels[2]} · Y2")
        self.legend.setVisible(legend)

    def set_view(self, view) -> None:
        """view: ExplorerView or None (nothing to draw). Arrays are kept referenced while drawn."""
        self._hide_hover()
        if view is None:
            self.c1.setData([], [])
            self.c2.setData([], [])
            self._rows, self._xy = [], []
            self.selected.hide()
            return
        x1, y1 = np.asarray(view.y1.x, float), np.asarray(view.y1.y, float)
        self.c1.setData(x1, y1)
        self._rows = [np.asarray(view.y1.source_row)]
        self._xy = [(x1, y1)]
        if view.y2 is not None:
            x2, y2 = np.asarray(view.y2.x, float), np.asarray(view.y2.y, float)
            self.c2.setData(x2, y2)
            self._rows.append(np.asarray(view.y2.source_row))
            self._xy.append((x2, y2))
        else:
            self.c2.setData([], [])
        self.vb.disableAutoRange()
        self.vb2.disableAutoRange()
        self.vb.setRange(xRange=view.x_range, yRange=view.y1_range, padding=0)
        if view.y2_range:
            self.vb2.setYRange(*view.y2_range, padding=0)

    def link_y2(self, linked: bool) -> None:
        """Use a shared Y scale when a view explicitly requests it.

        PyQtGraph still owns two axes, which keeps labels and units intact.  A
        linked display merely mirrors the visible Y1 interval to the right axis.
        """
        self._y2_linked = bool(linked)
        if linked and self._labels[2] is not None:
            self.vb2.setYRange(*self.vb.viewRange()[1], padding=0)

    def mark_selected(self, x: Optional[float]) -> None:
        if x is None or not np.isfinite(x):
            self.selected.hide()
            return
        self.selected.setPos(float(x))
        self.selected.show()

    def ranges(self) -> tuple[Range, Range, Optional[Range]]:
        (x0, x1), (a0, a1) = self.vb.viewRange()
        y2 = self.vb2.viewRange()[1] if self._labels[2] is not None else None
        return Range(x0, x1), Range(a0, a1), (Range(*y2) if y2 else None)

    def _sync_vb2(self) -> None:
        self.vb2.setGeometry(self.vb.sceneBoundingRect())
        self.vb2.linkedViewChanged(self.vb, self.vb2.XAxis)
        if getattr(self, "_y2_linked", False) and self._labels[2] is not None:
            self.vb2.setYRange(*self.vb.viewRange()[1], padding=0)
        r = self.vb.sceneBoundingRect()
        self.plotResized.emit(int(r.width()), int(r.height()))
        self._place_overlays()

    # ---------------------------------------------------------------- state presentation
    def show_message(self, icon: Optional[str], title: str = "", text: str = "", button: str = "", action: str = "") -> None:
        if icon is None:
            self.msg.hide()
            return
        self.msg_icon.setPixmap(pixmap(icon, CHART["tick"], 26))
        self.msg_title.setText(title)
        self.msg_text.setText(text)
        self.msg_btn.setVisible(bool(button))
        self.msg_btn.setText(button)
        self.msg_btn.setProperty("action", action)
        self.msg.show()
        self._place_overlays()

    def show_chip(self, text: str, icon: str = "") -> None:
        self.chip.setVisible(bool(text))
        self.chip.setText(text)
        self.chip.adjustSize()
        self._place_overlays()

    def set_faded(self, faded: bool) -> None:
        for c in (self.c1, self.c2):
            c.setOpacity(0.45 if faded else 1.0)

    def set_point_tracking(self, enabled: bool) -> None:
        self.point_tracking = bool(enabled)
        if not self.point_tracking:
            self._hide_hover()

    def show_progress(self, on: bool, fraction: Optional[float] = None, done: Optional[int] = None,
                      total: Optional[int] = None) -> None:
        self.prog.setVisible(on)
        self.veil.setVisible(on)
        if on:
            if fraction is None:
                self.prog_bar.setRange(0, 0)                # indeterminate: total not known yet
                self.prog_right.setText("")
            else:
                self.prog_bar.setRange(0, 1000)
                self.prog_bar.setValue(int(fraction * 1000))
                self.prog_right.setText(f"{fraction * 100:.0f}%")
            self.prog_left.setText(f"{done:,} of {total:,} points" if done is not None and total else "")
            self.veil.raise_()
            self.prog.raise_()
        self._place_overlays()

    def show_flat(self, png: Optional[bytes]) -> None:
        """Show the Render All Points data layer (or go back to the sampled curves with None). The axes, ticks and
        legend stay the chart's own; the chart is not interactive while the layer is shown."""
        shown = png is not None and self.flat.show(png)
        if not shown:
            self.flat.hide()
        for c in (self.c1, self.c2):
            c.setOpacity(0.0 if shown else 1.0)             # kept in the legend, hidden under the full layer
        self.interactive = not shown
        if shown:
            self._hide_hover()
            self.chip.raise_()

    def plot_size(self) -> tuple[int, int]:
        """Logical size of the plot area (the data layer requested from Render All Points)."""
        s = self.vb.size()
        return max(1, int(round(s.width()))), max(1, int(round(s.height())))

    def export_png(self, dpi: int = EXPORT_DPI) -> bytes:
        """The plot (axes, legend, curves or data layer) at `dpi`, without the chips and cards drawn over it."""
        return export_png(self.pw, dpi, CHART["bg"])

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._place_overlays()

    def _plot_rect(self) -> QRectF:
        r = self.vb.sceneBoundingRect()
        tl = self.pw.mapFromScene(r.topLeft())
        return QRectF(self.pw.mapTo(self, tl), r.size())

    def _place_overlays(self) -> None:
        r = self._plot_rect()
        self.msg.setGeometry(r.toRect())
        self.veil.setGeometry(r.toRect())
        self.prog.adjustSize()
        self.prog.move(int(r.center().x() - self.prog.width() / 2), int(r.center().y() - self.prog.height() / 2))
        self.chip.move(int(r.right() - self.chip.width() - 10), int(r.top() + 8))
        self.chip.raise_()

    # ---------------------------------------------------------------- gestures
    def eventFilter(self, watched, event) -> bool:
        """Own plot-area drags so Box Zoom and Pan do not depend on pyqtgraph scene routing."""
        if watched is self.pw.viewport() and event.type() in (
                QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
            if not self.interactive:
                return False
            scene_pos = self.pw.mapToScene(event.position().toPoint())
            pos = self.vb.mapFromScene(scene_pos)
            inside = self.vb.boundingRect().contains(pos)
            if event.type() == QEvent.Type.MouseButtonPress:
                if event.button() != Qt.MouseButton.LeftButton or not inside:
                    return False
                self.setFocus()
                pan = self.pan_mode or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                self._drag_state = {"pan": pan, "start": self.ranges(), "p0": pos, "cancelled": False,
                                    "viewport_event": True}
                self._hide_hover()
                if pan:
                    self.vb.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return True
            if self._drag_state is None or not self._drag_state.get("viewport_event"):
                return False
            state = self._drag_state
            if state["cancelled"]:
                self._drag_state = None
                return True
            p0 = state["p0"]
            width, height = self.vb.width(), self.vb.height()
            if state["pan"]:
                x, y1, y2 = state["start"]
                dx, dy = pos.x() - p0.x(), pos.y() - p0.y()
                viewport = Viewport(pan_x(x, dx, width), pan_y(y1, dy, height),
                                    pan_y(y2, dy, height) if y2 else None)
                self._apply_local(viewport)
            else:
                viewport = None
                self._show_box(p0, pos, width, height)
            if event.type() == QEvent.Type.MouseButtonRelease:
                if state["pan"]:
                    self.vb.setCursor(Qt.CursorShape.CrossCursor)
                else:
                    self._end_box()
                    x, y1, y2 = state["start"]
                    viewport = box_zoom(p0.x(), p0.y(), pos.x(), pos.y(), width, height, x, y1, y2)
                self._drag_state = None
                if viewport is not None:
                    self.viewportRequested.emit(viewport)
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape and self._drag_state is not None:
            self._drag_state["cancelled"] = True
            self._end_box()
            if self._drag_state.get("pan"):
                x, y1, y2 = self._drag_state["start"]
                self._restore_pens()
                self._apply_local(Viewport(x, y1, y2))
            e.accept()
            return
        super().keyPressEvent(e)

    def _drag(self, ev) -> None:
        if ev.button() != Qt.MouseButton.LeftButton or not self.interactive:
            ev.ignore()
            return
        ev.accept()
        if ev.isStart():
            self.setFocus()
            pan = self.pan_mode or bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self._drag_state = {"pan": pan, "start": self.ranges(), "p0": ev.buttonDownPos(), "cancelled": False}
            self._hide_hover()
            if pan:
                self.vb.setCursor(Qt.CursorShape.ClosedHandCursor)
                self._light_pens()
        st = self._drag_state
        if st is None or st["cancelled"]:
            if ev.isFinish():
                self._drag_state = None
            return
        p0, p1 = st["p0"], ev.pos()
        w, h = self.vb.width(), self.vb.height()
        if st["pan"]:
            x, y1, y2 = st["start"]
            dx, dy = p1.x() - p0.x(), p1.y() - p0.y()
            vp = Viewport(pan_x(x, dx, w), pan_y(y1, dy, h), pan_y(y2, dy, h) if y2 else None)
            self._apply_local(vp)
            if ev.isFinish():
                self.vb.setCursor(Qt.CursorShape.CrossCursor)
                self._drag_state = None
                self._restore_pens()
                self.viewportRequested.emit(vp)
            return
        if not ev.isFinish():
            self._show_box(p0, p1, w, h)
            return
        self._end_box()
        self._drag_state = None
        x, y1, y2 = st["start"]
        vp = box_zoom(p0.x(), p0.y(), p1.x(), p1.y(), w, h, x, y1, y2)
        if vp is not None:
            self.viewportRequested.emit(vp)

    def _light_pens(self) -> None:
        """While panning, draw the curves with a 1 px pen instead of the 1.5 px one.

        A pan moves the curves, so they are redrawn on every mouse move. An antialiased 1.5 px stroke is Qt's slowest
        case: on dense GITT traces one frame took ~177 ms (about 6 frames per second); a 1 px stroke takes ~27 ms. The
        colour, the points and the data are unchanged, and the original pens come back when the pan ends or is
        cancelled (_restore_pens). Markers-only styles have no line pen and are left as they are."""
        if self._pan_pens is not None:
            return
        self._pan_pens = []
        for curve in (self.c1, self.c2):
            pen = curve.opts.get("pen")
            self._pan_pens.append(pen)
            if pen is not None and QPen(pen).style() != Qt.PenStyle.NoPen:
                light = QPen(pen)
                light.setWidthF(1.0)
                curve.setPen(light)

    def _restore_pens(self) -> None:
        """Put back the pens saved by _light_pens (nothing to do when no pan lightened them)."""
        if self._pan_pens is None:
            return
        for curve, pen in zip((self.c1, self.c2), self._pan_pens):
            curve.setPen(pen)
        self._pan_pens = None

    def _apply_local(self, vp: Viewport) -> None:
        self.vb.setRange(xRange=(vp.x.lo, vp.x.hi), yRange=(vp.y1.lo, vp.y1.hi), padding=0)
        if vp.y2:
            self.vb2.setYRange(vp.y2.lo, vp.y2.hi, padding=0)

    def _show_box(self, p0: QPointF, p1: QPointF, w: float, h: float) -> None:
        x0, x1 = sorted((max(0, min(w, p0.x())), max(0, min(w, p1.x()))))
        y0, y1 = sorted((max(0, min(h, p0.y())), max(0, min(h, p1.y()))))
        # thin rectangles preview the one-direction zoom (full height / full width band)
        from ..explorer import THIN_PX
        if y1 - y0 < THIN_PX:
            y0, y1 = 0, h
        elif x1 - x0 < THIN_PX:
            x0, x1 = 0, w
        rect = QRectF(x0, y0, x1 - x0, y1 - y0)
        outer = QPainterPath()
        outer.setFillRule(Qt.FillRule.OddEvenFill)
        outer.addRect(QRectF(0, 0, w, h))
        outer.addRect(rect)
        inner = QPainterPath()
        inner.addRect(rect)
        self.dim.setPath(outer)
        self.box.setPath(inner)
        self.dim.show()
        self.box.show()

    def _end_box(self) -> None:
        self.dim.hide()
        self.box.hide()

    def _axis_wheel(self, orientation: str, steps: float, scene_pos) -> None:
        if not self.interactive:
            return
        x, y1, y2 = self.ranges()
        if orientation == "bottom":
            vp = Viewport(wheel_zoom(x, self.vb.mapSceneToView(scene_pos).x(), steps), y1, y2)
        elif orientation == "left":
            vp = Viewport(x, wheel_zoom(y1, self.vb.mapSceneToView(scene_pos).y(), steps), y2)
        elif y2 is not None:
            vp = Viewport(x, y1, wheel_zoom(y2, self.vb2.mapSceneToView(scene_pos).y(), steps))
        else:
            return
        self._apply_local(vp)
        self.viewportRequested.emit(vp)                     # the controller debounces wheel bursts

    def _plot_wheel(self, steps: float, scene_pos) -> None:
        """Wheel inside the plot: X, Y1 and Y2 zoom together around the point under the pointer."""
        if not self.interactive:
            return
        x, y1, y2 = self.ranges()
        p1, p2 = self.vb.mapSceneToView(scene_pos), self.vb2.mapSceneToView(scene_pos)
        vp = Viewport(wheel_zoom(x, p1.x(), steps), wheel_zoom(y1, p1.y(), steps),
                      wheel_zoom(y2, p2.y(), steps) if y2 is not None else None)
        self._apply_local(vp)
        self.viewportRequested.emit(vp)                     # the controller debounces wheel bursts

    def _axis_drag(self, orientation: str, ev) -> None:
        """Left drag on an axis pans that axis only; the content follows the pointer (drag the right axis down and
        the Y2 curve, with its zero, moves down). The other axes keep their ranges."""
        if not self.interactive:
            return
        if ev.isStart() or self._axis_drag_start is None:
            self._axis_drag_start = self.ranges()
            self._hide_hover()
        x, y1, y2 = self._axis_drag_start
        d = ev.pos() - ev.buttonDownPos()
        w, h = self.vb.width(), self.vb.height()
        linked = getattr(self, "_y2_linked", False)
        if orientation == "bottom":
            vp = Viewport(pan_x(x, d.x(), w), y1, y2)
        elif orientation == "left" or (linked and y2 is not None):
            vp = Viewport(x, pan_y(y1, d.y(), h), pan_y(y2, d.y(), h) if (linked and y2) else y2)
        elif y2 is not None:
            vp = Viewport(x, y1, pan_y(y2, d.y(), h))
        else:
            return
        self._apply_local(vp)
        if ev.isFinish():
            self._axis_drag_start = None
            self.viewportRequested.emit(vp)

    # ---------------------------------------------------------------- hover (real points only)
    def _hide_hover(self) -> None:
        self.cross.hide()
        self.m1.setData([], [])
        self.m2.setData([], [])
        if hasattr(self, "tip"):
            self.tip.hide()
        self._hover_row = -1

    def _mouse_moved(self, pos) -> None:
        if not self.point_tracking or not self.interactive or self._drag_state is not None or not self._xy:
            return self._hide_hover()
        if not self.vb.sceneBoundingRect().contains(pos):
            return self._hide_hover()
        best = None                                          # (pixel distance, curve index, point index)
        for ci, (xs, ys) in enumerate(self._xy):
            vb = self.vb if ci == 0 else self.vb2
            ok = np.isfinite(xs) & np.isfinite(ys)
            if not ok.any():
                continue
            if ascending_x(self._x_key):
                xv = vb.mapSceneToView(pos).x()
                idx = np.flatnonzero(ok)
                k = np.searchsorted(xs[idx], xv)
                cand = idx[max(0, k - 1):k + 1]
            else:
                cand = np.flatnonzero(ok)
            pts = [vb.mapViewToScene(QPointF(float(xs[i]), float(ys[i]))) for i in cand[:4000]]
            for i, p in zip(cand[:4000], pts):
                d = ((p.x() - pos.x()) ** 2 + (p.y() - pos.y()) ** 2) ** 0.5
                if ascending_x(self._x_key):
                    d = abs(p.x() - pos.x())
                if best is None or d < best[0]:
                    best = (d, ci, int(i))
        if best is None or best[0] > SNAP_PX:
            return self._hide_hover()
        _, ci, i = best
        row = int(self._rows[ci][i])
        xs, ys = self._xy[ci]
        self.cross.setPos(float(xs[i]))
        self.cross.show()
        (self.m1 if ci == 0 else self.m2).setData([float(xs[i])], [float(ys[i])])
        (self.m2 if ci == 0 else self.m1).setData([], [])
        if row != self._hover_row:
            self._hover_row = row
            self._fill_tip(row, {})                          # immediate values of the curve, details follow
            self.hoverRow.emit(row)
        self._move_tip(self.vb.mapViewToScene(QPointF(float(xs[i]), float(ys[i]))) if ci == 0 else
                       self.vb2.mapViewToScene(QPointF(float(xs[i]), float(ys[i]))))

    def show_details(self, row: int, values: dict, keys: tuple[str, str, Optional[str]]) -> None:
        """Values of one real point (all columns, display units) from the backend; markers move to that row."""
        if row != self._hover_row or not self.tip.isVisible():
            return
        self._fill_tip(row, values, keys)
        x = values.get(keys[0])
        if x is not None and np.isfinite(x):
            y1 = values.get(keys[1])
            self.m1.setData([x], [y1]) if y1 is not None and np.isfinite(y1) else self.m1.setData([], [])
            if keys[2]:
                y2 = values.get(keys[2])
                self.m2.setData([x], [y2]) if y2 is not None and np.isfinite(y2) else self.m2.setData([], [])

    def _fmt(self, key: Optional[str], v) -> str:
        if v is None or (isinstance(v, float) and not np.isfinite(v)):
            return "— (no value)"
        if key == "time_s":
            return fmt_time(float(v))
        if key in ("cycle", "half_cycle", "source_row", "step", "rest"):
            return f"{int(v):,}" if key == "source_row" else str(int(v))
        return sig4(float(v))

    def _fill_tip(self, row: int, values: dict, keys: Optional[tuple] = None) -> None:
        self.tip_head.setText(f"{self.row_title} {row:,}")
        labs = self._labels
        keys = keys or (None, None, None)
        colors = (CHART["tick"], *self._colors)
        for i, (a, b) in enumerate(self.tip_rows):
            show = i < 2 or labs[2] is not None
            a.setVisible(show)
            b.setVisible(show)
            if show:
                name = (labs[i] or "").split(" [")[0]
                unit = (labs[i] or "").split(" [")[1].rstrip("]") if " [" in (labs[i] or "") else ""
                a.setText(name)
                a.setStyleSheet(f"color: {colors[i]};")
                b.setText(f"{self._fmt(keys[i], values.get(keys[i])) if keys[i] in values else '…'} {unit}".strip())
        if values and self.meta_line:
            step = values.get("step")
            self.tip_meta.setText(f"Cycle {self._fmt('cycle', values.get('cycle'))} · Half-Cycle "
                                  f"{self._fmt('half_cycle', values.get('half_cycle'))} · Step "
                                  f"{STEP_NAMES.get(int(step), str(step)) if step is not None else chr(8212)}")
        else:
            self.tip_meta.setText("")
        self.tip.adjustSize()
        self.tip.show()
        self.tip.raise_()

    def _move_tip(self, scene_pt) -> None:
        p = self.pw.mapTo(self, self.pw.mapFromScene(scene_pt))
        x = p.x() + 14 if p.x() + 14 + self.tip.width() < self.width() else p.x() - 14 - self.tip.width()
        y = max(8, min(p.y() - 10, self.height() - self.tip.height() - 8))
        self.tip.move(int(x), int(y))

