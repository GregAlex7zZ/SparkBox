"""Chart with many traces on one X / one Y axis (dQ/dV; reusable by Comparison). Fixed chart palette for frame, grid and
text; trace colours come from the caller. Each trace is a list of segments that are NEVER joined (NaN breaks).
Gestures as everywhere: left drag = Box Zoom, Shift+drag or Pan tool = Pan, wheel only over an axis (that axis only),
Esc cancels a drag, Reset View only via the button. Hover = nearest drawn point (a derived or published point, as the
caller says), never an interpolated position."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QGraphicsItem, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget
from ..explorer import CHART, ZOOM_STEP, Range
from .chart import FONT_FAMILY, _WheelAxis, _font
from .flat_layer import EXPORT_DPI, FlatLayer, export_png

DASH = {"solid": Qt.PenStyle.SolidLine, "dash": Qt.PenStyle.DashLine, "dot": Qt.PenStyle.DotLine}
SNAP_PX = 24


@dataclass
class Trace:
    key: tuple                                   # e.g. (cycle, phase, kind)
    segments: list                               # [(x array, y array, meta)], meta = e.g. segment index
    color: str
    width: float = 1.6
    alpha: float = 1.0
    dash: str = "solid"
    symbol: Optional[str] = None
    readout: str = ""                            # first part of the hover text (e.g. "Cycle 10 · charge · smoothed")


@dataclass
class LegendEntry:
    label: str
    color: str
    dash: str = "solid"


class _VB(pg.ViewBox):
    def __init__(self, owner: "MultiChart"):
        super().__init__(enableMenu=False)
        self.owner = owner
        self.setMouseMode(pg.ViewBox.RectMode)
        self.rbScaleBox.setPen(pg.mkPen(CHART["dim"], width=1, style=Qt.PenStyle.DashLine))
        self.rbScaleBox.setBrush(pg.mkBrush(37, 59, 68, 26))

    def wheelEvent(self, ev, axis=None) -> None:
        ev.ignore()

    def mouseDragEvent(self, ev, axis=None) -> None:
        shift = bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        self.setMouseMode(pg.ViewBox.PanMode if shift or self.owner.pan_mode else pg.ViewBox.RectMode)
        if self.owner._esc:
            self.rbScaleBox.hide()
            ev.accept()
            if ev.isFinish():
                self.owner._esc = False
            return
        super().mouseDragEvent(ev, axis)
        if ev.isFinish():
            self.owner._user_range()

    def mouseClickEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton and self.owner._hover is not None:
            self.owner.traceClicked.emit(self.owner._hover[0])
        ev.accept()

    def mouseDoubleClickEvent(self, ev) -> None:
        ev.accept()


class MultiChart(QWidget):
    rangeChanged = Signal(object, object)        # Range x, Range y chosen by a gesture
    traceClicked = Signal(object)                # trace key

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ChartSurface")
        self.setStyleSheet(f"#ChartSurface {{ background: {CHART['bg']}; border: 1px solid {CHART['frame']}; border-radius: 10px; }}")
        self.setMinimumSize(360, 240)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.pan_mode, self._esc = False, False
        self._traces: list[Trace] = []
        self._items: list = []
        self._hover = None
        self._fit = None
        self.y_unit = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        self.vb = _VB(self)
        axes = {k: _WheelAxis(k, self._wheel) for k in ("bottom", "left")}
        self.pw = pg.PlotWidget(viewBox=self.vb, axisItems=axes, background=CHART["bg"])
        self.pw.setFrameShape(QFrame.Shape.NoFrame)
        lay.addWidget(self.pw)
        self.pi = self.pw.getPlotItem()
        self.pi.hideButtons()
        self.pi.setMenuEnabled(False)
        self.pi.showGrid(x=True, y=True, alpha=0.35)
        self.pi.layout.setContentsMargins(8, 12, 12, 6)
        for name in ("bottom", "left"):
            ax = self.pi.getAxis(name)
            ax.setPen(pg.mkPen(CHART["frame"]))
            ax.setTextPen(pg.mkPen(CHART["tick"]))
            ax.setTickFont(_font(11))
        self.zero = pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen(CHART["frame"], width=1))
        self.vb.addItem(self.zero, ignoreBounds=True)
        self.mark = pg.ScatterPlotItem(size=10, pen=pg.mkPen(CHART["text"], width=2), brush=pg.mkBrush("#FFFFFF"))
        self.mark.setZValue(50)
        self.vb.addItem(self.mark, ignoreBounds=True)
        self.pi.scene().sigMouseMoved.connect(self._moved)
        # overlays: legend (scrolls), readout, message
        self.legend = QScrollArea(self)
        self.legend.setWidgetResizable(True)
        self.legend.setFrameShape(QFrame.Shape.NoFrame)
        self.legend.setStyleSheet(f"QScrollArea {{ background: rgba(255,255,255,240); border: 1px solid {CHART['frame']};"
                                  f" border-radius: 8px; }} QLabel {{ color: {CHART['text']}; font-family: {FONT_FAMILY};"
                                  f" font-size: 9pt; background: transparent; }}")
        self.leg_body = QWidget()
        self.leg_body.setStyleSheet("background: transparent;")
        self.leg_lay = QVBoxLayout(self.leg_body)
        self.leg_lay.setContentsMargins(8, 6, 8, 6)
        self.leg_lay.setSpacing(4)
        self.legend.setWidget(self.leg_body)
        self.legend.hide()
        self.readout = QLabel(self)
        self.readout.setStyleSheet(f"background: rgba(255,255,255,240); border: 1px solid {CHART['frame']}; border-radius: 6px;"
                                   f" padding: 3px 8px; color: {CHART['text']}; font-family: {FONT_FAMILY}; font-size: 9pt;")
        self.readout.hide()
        self.msg = QLabel(self)
        self.msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.msg.setWordWrap(True)
        self.msg.setStyleSheet(f"color: {CHART['tick']}; font-family: {FONT_FAMILY}; font-size: 9.75pt; background: transparent;")
        self.msg.hide()
        self.flat = FlatLayer(self.vb)                    # every point, drawn under the chart's own axes and legend

    def show_flat(self, png: Optional[bytes]) -> None:
        """Show the Render All Points data layer over the current view (None: back to the sampled traces)."""
        shown = bool(png) and self.flat.show(png)
        if not shown:
            self.flat.hide()
        for it in self._items:
            it.setOpacity(0.0 if shown else 1.0)

    def plot_size(self) -> tuple[int, int]:
        """Logical size of the plot area (the data layer requested from Render All Points)."""
        s = self.vb.size()
        return max(1, int(round(s.width()))), max(1, int(round(s.height())))

    def view_ranges(self) -> tuple[tuple[float, float], tuple[float, float]]:
        (x0, x1), (y0, y1) = self.vb.viewRange()
        return (float(x0), float(x1)), (float(y0), float(y1))

    def export_png(self, dpi: int = EXPORT_DPI) -> bytes:
        """The chart with its legend at `dpi` (hover readout and messages are not part of the image)."""
        self.readout.hide()
        return export_png(self, dpi, CHART["bg"])

    # ---------------------------------------------------------------- data
    def set_labels(self, x: str, y: str) -> None:
        lab = {"font-family": FONT_FAMILY, "font-size": "12px", "font-weight": "500"}
        self.pi.setLabel("bottom", x, color=CHART["text"], **lab)
        self.pi.setLabel("left", y, color=CHART["text"], **lab)

    def set_traces(self, traces: list[Trace], legend: Optional[list[LegendEntry]] = None, keep_view: bool = True) -> None:
        for it in self._items:
            self.vb.removeItem(it)
        self._items, self._traces, self._hover = [], traces, None
        self.mark.setData([], [])
        self.readout.hide()
        xs_all, ys_all = [], []
        for tr in traces:
            parts_x, parts_y = [], []
            for x, y, _m in tr.segments:
                parts_x += [np.asarray(x, float), np.array([np.nan])]
                parts_y += [np.asarray(y, float), np.array([np.nan])]
            if not parts_x:
                continue
            x, y = np.concatenate(parts_x), np.concatenate(parts_y)
            col = QColor(tr.color)
            col.setAlphaF(tr.alpha)
            pen = pg.mkPen(col, width=tr.width, style=DASH.get(tr.dash, Qt.PenStyle.SolidLine)) if tr.dash != "none" else None
            item = pg.PlotDataItem(x, y, pen=pen, connect="finite")
            item.setDownsampling(ds=1, auto=False)                  # every point drawn
            # Cache each drawn line as a device-resolution image, redrawn only when it changes (data, pen, zoom,
            # pan): the box-zoom rectangle, the hover readout and the marker reuse it (same as ChartWidget). Symbols
            # are not cached: pyqtgraph places them at sub-pixel positions that a cached image would shift.
            item.curve.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
            if self.flat.visible:
                item.setOpacity(0.0)                                # under the Render All Points layer
            if tr.symbol:
                item.setSymbol(tr.symbol)
                item.setSymbolSize(5)
                item.setSymbolBrush(pg.mkBrush(col))
                item.setSymbolPen(None)
            self.vb.addItem(item)
            self._items.append(item)
            xs_all.append(x)
            ys_all.append(y)
        if xs_all:
            ax, ay = np.concatenate(xs_all), np.concatenate(ys_all)
            ok = np.isfinite(ax) & np.isfinite(ay)
            if ok.any():
                x0, x1, y0, y1 = float(ax[ok].min()), float(ax[ok].max()), float(ay[ok].min()), float(ay[ok].max())
                px = (x1 - x0) * 0.03 or 0.01
                py = (y1 - y0) * 0.06 or max(abs(y1) * 0.05, 1e-9)
                self._fit = ((x0 - px, x1 + px), (y0 - py, y1 + py))
            else:
                self._fit = None
        else:
            self._fit = None
        self._fill_legend(legend or [])
        if not keep_view:
            self.reset_view()

    def set_range(self, x: Optional[Range], y: Optional[Range]) -> None:
        """None on an axis = automatic (fit the drawn traces)."""
        fx, fy = self._fit or ((0.0, 1.0), (0.0, 1.0))
        self.vb.setRange(xRange=(x.lo, x.hi) if x else fx, yRange=(y.lo, y.hi) if y else fy, padding=0)

    def reset_view(self) -> None:
        self.set_range(None, None)

    def ranges(self) -> tuple[Range, Range]:
        (x0, x1), (y0, y1) = self.vb.viewRange()
        return Range(x0, x1), Range(y0, y1)

    def _user_range(self) -> None:
        x, y = self.ranges()
        self.rangeChanged.emit(x, y)

    def _fill_legend(self, entries: list[LegendEntry]) -> None:
        while self.leg_lay.count():
            w = self.leg_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        for e in entries:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(7)
            sw = QFrame()
            sw.setFixedSize(18, 3 if e.dash == "solid" else 2)
            border = "solid" if e.dash == "solid" else ("dashed" if e.dash == "dash" else "dotted")
            sw.setStyleSheet(f"background: {e.color};" if e.dash == "solid" else f"border-top: 2px {border} {e.color};")
            h.addWidget(sw)
            h.addWidget(QLabel(e.label))
            h.addStretch(1)
            self.leg_lay.addWidget(row)
        self.legend.setVisible(bool(entries))
        self._place()

    def show_legend(self, on: bool) -> None:
        self.legend.setVisible(on and self.leg_lay.count() > 0)

    def show_message(self, text: str) -> None:
        self.msg.setText(text)
        self.msg.setVisible(bool(text))
        self._place()

    # ---------------------------------------------------------------- interaction
    def _wheel(self, orientation: str, steps: float, scene_pos) -> None:
        f = ZOOM_STEP ** steps
        p = self.vb.mapSceneToView(scene_pos)
        if orientation == "bottom":
            self.vb.scaleBy(x=f, y=None, center=p)
        else:
            self.vb.scaleBy(x=None, y=f, center=p)
        self._user_range()

    def _moved(self, pos) -> None:
        if not self._traces or not self.vb.sceneBoundingRect().contains(pos):
            self.mark.setData([], [])
            self.readout.hide()
            self._hover = None
            return
        best = None
        (x0, x1), (y0, y1) = self.vb.viewRange()
        r = self.vb.sceneBoundingRect()
        sx, sy = r.width() / (x1 - x0 or 1), r.height() / (y1 - y0 or 1)
        p = self.vb.mapSceneToView(pos)
        for tr in self._traces:
            for x, y, meta in tr.segments:
                x, y = np.asarray(x, float), np.asarray(y, float)
                if not x.size:
                    continue
                d = np.hypot((x - p.x()) * sx, (y - p.y()) * sy)
                i = int(np.nanargmin(d)) if np.isfinite(d).any() else -1
                if i >= 0 and (best is None or d[i] < best[0]):
                    best = (float(d[i]), tr, float(x[i]), float(y[i]), meta)
        if best is None or best[0] > SNAP_PX:
            self.mark.setData([], [])
            self.readout.hide()
            self._hover = None
            return
        _d, tr, x, y, meta = best
        self._hover = (tr.key,)
        self.mark.setData([x], [y])
        self.readout.setText(f"{tr.readout}{' · span ' + str(meta) if meta is not None else ''} · {x:.4g} · {y:.4g} {self.y_unit}")
        self.readout.adjustSize()
        self.readout.move(12, 8)
        self.readout.show()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self._esc = True
            self.vb.rbScaleBox.hide()
            e.accept()
            return
        super().keyPressEvent(e)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._place()

    def _place(self) -> None:
        n = self.leg_lay.count()
        hgt = min(self.height() - 40, 12 + n * 20)
        self.legend.setGeometry(self.width() - 190, 10, 176, max(30, hgt))
        self.msg.setGeometry(20, 20, self.width() - 40, self.height() - 40)
