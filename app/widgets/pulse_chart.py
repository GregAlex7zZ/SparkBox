"""DCIR inspection chart: voltage on top, current below, linked X = time from the reference sample V0 [s]
(the last recorded sample before the pulse; the same origin as the measurement time).
Draws only the real samples of PulseTrace; zones (rest before / pulse / rest after) and the measurement markers come
from the adapter. Fixed palette, same gestures as the other charts: left drag = Box Zoom, Shift+drag = Pan,
wheel in a plot = zoom time and that plot's Y around the pointer, wheel over an axis = that axis only, left drag on
an axis = pan that axis only, Reset View only via the button, Esc cancels a drag.
In 'pick' mode (manual dialog) a click selects the nearest REAL sample in time; nothing is interpolated. A press that
moves less than CLICK_PX before release still counts as a click (pyqtgraph would turn a slow, slightly moving press
into a drag, i.e. a tiny box zoom, and the pick would be lost).
Markers (V0/I0, I1, V1) are drawn on the exact recorded samples the measurement used, so their position on the time axis
is the real one; the dashed line is the requested measurement time (the nearest recorded sample is used)."""
from __future__ import annotations
from typing import Optional
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget
from ..explorer import CHART, ZOOM_STEP
from .chart import FONT_FAMILY, _WheelAxis, _font

CLICK_PX = 6                    # pick mode: drags shorter than this (screen px) are clicks
ZONES = {"rest_before": "#EAF2F5", "pulse": "#FFF0E2", "rest_after": "#EAF2F5"}
MARK = {  # key: (plot, symbol, pen colour)  - V0/I0 circle, I1 square, V1 diamond [approved]
    "v0": ("v", "o", CHART["text"]), "i0": ("i", "o", CHART["text"]),
    "i1": ("i", "s", CHART["y2"]), "v1": ("v", "d", CHART["y1"]),
}


class _VB(pg.ViewBox):
    def __init__(self, owner: "PulseChart"):
        super().__init__(enableMenu=False)
        self.owner = owner
        self.setMouseMode(pg.ViewBox.RectMode)
        self.rbScaleBox.setPen(pg.mkPen(CHART["dim"], width=1, style=Qt.PenStyle.DashLine))
        self.rbScaleBox.setBrush(pg.mkBrush(37, 59, 68, 26))

    def wheelEvent(self, ev, axis=None) -> None:
        d = ev.delta() if hasattr(ev, "delta") else 0
        if d:                                                 # zoom time and this plot's Y around the pointer
            f = ZOOM_STEP ** (d / 120.0)
            self.scaleBy(x=f, y=f, center=self.mapSceneToView(ev.scenePos()))
        ev.accept()

    def mouseDragEvent(self, ev, axis=None) -> None:
        if self.owner.pick and ev.button() == Qt.MouseButton.LeftButton:
            if ev.isStart():
                self._short = True
            if getattr(self, "_short", False):
                moved = (ev.screenPos() - ev.buttonDownScreenPos()).manhattanLength()
                if moved < CLICK_PX:
                    ev.accept()
                    if ev.isFinish():                         # a click with a little jitter: pick, never zoom
                        self._short = False
                        self.owner._click(self, ev.buttonDownScenePos())
                    return
                self._short = False                           # a real drag from here on
        shift = bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        self.setMouseMode(pg.ViewBox.PanMode if shift or self.owner.pan_mode else pg.ViewBox.RectMode)
        if self.owner._esc:                                   # Esc pressed during this drag: drop it
            self.rbScaleBox.hide()
            ev.accept()
            if ev.isFinish():
                self.owner._esc = False
            return
        super().mouseDragEvent(ev, axis)

    def mouseClickEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self.owner._click(self, ev.scenePos())
        ev.accept()

    def mouseDoubleClickEvent(self, ev) -> None:
        ev.accept()                                           # never resets (Reset View is the button)


class PulseChart(QWidget):
    sampleClicked = Signal(int, str)            # index into the trace arrays, "v" | "i" (plot clicked)
    xRangeChanged = Signal(float, float)        # visible time range (pan / zoom), for windowed loading

    def __init__(self, parent=None, pick: bool = False):
        super().__init__(parent)
        self.setObjectName("ChartSurface")
        self.setStyleSheet(f"#ChartSurface {{ background: {CHART['bg']}; border: 1px solid {CHART['frame']}; border-radius: 10px; }}")
        self.setMinimumSize(360, 260)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.pick, self.pan_mode, self._esc = pick, False, False
        self.trace = None
        self._vu = self._iu = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        self.glw = pg.GraphicsLayoutWidget()
        self.glw.setBackground(CHART["bg"])
        lay.addWidget(self.glw)
        self.vbv, self.vbi = _VB(self), _VB(self)
        axes = lambda vb: {k: _WheelAxis(k, lambda o, s, p: self._wheel(vb, o, s, p),
                                         lambda o, ev: self._axis_drag(vb, o, ev)) for k in ("left", "bottom")}
        self.pv = self.glw.addPlot(row=0, col=0, viewBox=self.vbv, axisItems=axes(self.vbv))
        self.pi = self.glw.addPlot(row=1, col=0, viewBox=self.vbi, axisItems=axes(self.vbi))
        self.pi.setXLink(self.pv)
        for p in (self.pv, self.pi):
            p.hideButtons()
            p.setMenuEnabled(False)
            p.showGrid(x=True, y=True, alpha=0.35)
            p.getAxis("left").setWidth(64)                    # aligned Y axes
            for name in ("bottom", "left"):
                ax = p.getAxis(name)
                ax.setPen(pg.mkPen(CHART["frame"]))
                ax.setTextPen(pg.mkPen(CHART["tick"]))
                ax.setTickFont(_font(11))
        self.pv.getAxis("bottom").setStyle(showValues=False)
        self.cv = pg.PlotDataItem(pen=pg.mkPen(CHART["y1"], width=1.5), connect="finite")
        self.ci = pg.PlotDataItem(pen=pg.mkPen(CHART["y2"], width=1.5), connect="finite")
        self.pv.addItem(self.cv)
        self.pi.addItem(self.ci)
        self.zone_items: list = []
        self.req = [pg.InfiniteLine(angle=90, pen=pg.mkPen(CHART["cross"], width=1, style=Qt.PenStyle.DashLine)) for _ in range(2)]
        self.cross = [pg.InfiniteLine(angle=90, pen=pg.mkPen(CHART["cross"], width=1, style=Qt.PenStyle.DotLine)) for _ in range(2)]
        for p, a, b in ((self.pv, self.req[0], self.cross[0]), (self.pi, self.req[1], self.cross[1])):
            p.addItem(a, ignoreBounds=True)
            p.addItem(b, ignoreBounds=True)
            a.hide()
            b.hide()
        self.marks = {k: pg.ScatterPlotItem(size=11, symbol=sym, pen=pg.mkPen(col, width=2), brush=pg.mkBrush("#FFFFFF"))
                      for k, (_p, sym, col) in MARK.items()}
        for k, it in self.marks.items():
            it.setZValue(10)
            it.setAcceptedMouseButtons(Qt.MouseButton.NoButton)   # a click on a symbol still reaches the plot
            (self.pv if MARK[k][0] == "v" else self.pi).addItem(it, ignoreBounds=True)
        self.picked = pg.ScatterPlotItem(size=12, symbol="o", pen=pg.mkPen(CHART["text"], width=2), brush=pg.mkBrush(CHART["y1"]))
        self.picked.setZValue(12)
        self.pv.addItem(self.picked, ignoreBounds=True)
        self.picked_i = pg.ScatterPlotItem(size=12, symbol="o", pen=pg.mkPen(CHART["text"], width=2), brush=pg.mkBrush(CHART["y2"]))
        self.picked_i.setZValue(12)
        self.pi.addItem(self.picked_i, ignoreBounds=True)
        for it in (self.picked, self.picked_i):
            it.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.readout = QLabel(self)
        self.readout.setStyleSheet(f"background: rgba(255,255,255,235); border: 1px solid {CHART['frame']}; border-radius: 6px;"
                                   f" padding: 2px 8px; color: {CHART['text']}; font-family: {FONT_FAMILY}; font-size: 8.25pt;")
        self.readout.hide()
        self.msg = QLabel(self)
        self.msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.msg.setWordWrap(True)
        self.msg.setStyleSheet(f"color: {CHART['tick']}; font-family: {FONT_FAMILY}; font-size: 9.75pt; background: transparent;")
        self.msg.hide()
        self.glw.scene().sigMouseMoved.connect(self._moved)
        self.vbv.sigXRangeChanged.connect(lambda _vb, r: self.xRangeChanged.emit(float(r[0]), float(r[1])))

    # ---------------------------------------------------------------- data
    def set_labels(self, voltage: str, current: str, time: str = "Time from V0 [s]") -> None:
        unit = lambda t: t[t.find("[") + 1:t.find("]")] if "[" in t else ""
        self._vu, self._iu = unit(voltage), unit(current)
        lab = {"font-family": FONT_FAMILY, "font-size": "9pt", "font-weight": "500"}
        self.pv.setLabel("left", voltage, color=CHART["y1"], **lab)
        self.pi.setLabel("left", current, color=CHART["y2"], **lab)
        self.pi.setLabel("bottom", time, color=CHART["text"], **lab)

    def set_trace(self, trace, requested_time_s: Optional[float] = None, zones: bool = True,
                  keep_view: bool = False, current_time_s: Optional[float] = None) -> None:
        """Draw `trace` (None clears). keep_view keeps the current zoom/pan (used when a moving window is reloaded).
        The dashed lines mark the requested times: V1 on the voltage plot, I1 on the current plot (current_time_s;
        None draws the V1 time there too, the core then uses the 3rd pulse sample for I1)."""
        self.trace = trace
        for plot, it in self.zone_items:
            plot.removeItem(it)
        self.zone_items = []
        self.picked.setData([], [])
        self.picked_i.setData([], [])
        if trace is None:
            self.cv.setData([], [])
            self.ci.setData([], [])
            for m in self.marks.values():
                m.setData([], [])
            for r in self.req:
                r.hide()
            return
        t = np.asarray(trace.t_rel_s, float)
        self.cv.setData(t, np.asarray(trace.voltage, float))
        self.ci.setData(t, np.asarray(trace.current, float))
        for name, (a, b) in (trace.zones.items() if zones else ()):
            for p in (self.pv, self.pi):
                z = pg.LinearRegionItem((a, b), movable=False, brush=pg.mkBrush(ZONES.get(name, "#EAF2F5")),
                                        pen=pg.mkPen(None))
                z.setZValue(-20)
                p.addItem(z, ignoreBounds=True)
                self.zone_items.append((p, z))
        self.set_markers(trace.markers)
        for r, t_req in ((self.req[0], requested_time_s),
                         (self.req[1], current_time_s if current_time_s is not None else requested_time_s)):
            r.setVisible(t_req is not None)
            if t_req is not None:
                r.setPos(t_req)
        if not keep_view:
            self.reset_view()

    def set_markers(self, markers: dict) -> None:
        """V0/I0/I1/V1 symbols on trace indices ({'v0': idx, ...}; a missing key hides that symbol). View unchanged."""
        for k, m in self.marks.items():
            idx = markers.get(k) if self.trace is not None else None
            if idx is None:
                m.setData([], [])
                continue
            arr = self.trace.voltage if MARK[k][0] == "v" else self.trace.current
            m.setData([float(self.trace.t_rel_s[idx])], [float(arr[idx])])

    def reset_view(self) -> None:
        if self.trace is None:
            return
        t = np.asarray(self.trace.t_rel_s, float)
        self.pv.setXRange(float(np.nanmin(t)), float(np.nanmax(t)), padding=0.01)
        for vb, arr in ((self.vbv, self.trace.voltage), (self.vbi, self.trace.current)):
            a = np.asarray(arr, float)
            a = a[np.isfinite(a)]
            if a.size:
                lo, hi = float(a.min()), float(a.max())
                pad = (hi - lo) * 0.08 or max(abs(hi) * 0.02, 1e-6)
                vb.setYRange(lo - pad, hi + pad, padding=0)

    def show_message(self, text: str) -> None:
        self.msg.setText(text)
        self.msg.setVisible(bool(text))
        self.glw.setVisible(not text)
        self._place()

    def mark_picked(self, idx: Optional[int]) -> None:
        if self.trace is None or idx is None:
            self.picked.setData([], [])
            self.picked_i.setData([], [])
            return
        t = float(self.trace.t_rel_s[idx])
        self.picked.setData([t], [float(self.trace.voltage[idx])])
        self.picked_i.setData([t], [float(self.trace.current[idx])])

    # ---------------------------------------------------------------- interaction
    def _nearest(self, x: float) -> Optional[int]:
        if self.trace is None:
            return None
        t = np.asarray(self.trace.t_rel_s, float)
        ok = np.isfinite(np.asarray(self.trace.voltage, float)) & np.isfinite(np.asarray(self.trace.current, float))
        if not ok.any():
            return None
        d = np.where(ok, np.abs(t - x), np.inf)
        return int(np.argmin(d))

    def _click(self, vb, scene_pos) -> None:
        if not self.pick:
            return
        idx = self._nearest(vb.mapSceneToView(scene_pos).x())
        if idx is not None:
            self.sampleClicked.emit(idx, "v" if vb is self.vbv else "i")

    def _moved(self, pos) -> None:
        if self.trace is None:
            return
        for vb in (self.vbv, self.vbi):
            if vb.sceneBoundingRect().contains(pos):
                idx = self._nearest(vb.mapSceneToView(pos).x())
                if idx is None:
                    break
                tr = self.trace
                for c in self.cross:
                    c.setPos(float(tr.t_rel_s[idx]))
                    c.show()
                cyc = tr.cycle[idx] if tr.cycle is not None else None
                self.readout.setText(f"{float(tr.t_rel_s[idx]):+.6g} s · {float(tr.voltage[idx]):.4g} {self._vu} · "
                                     f"{float(tr.current[idx]):.4g} {self._iu} · row {int(tr.rows[idx]):,}"
                                     + (f" · cycle {cyc}" if cyc is not None else ""))
                self.readout.adjustSize()
                self.readout.move(self.width() - self.readout.width() - 12, 8)
                self.readout.show()
                return
        for c in self.cross:
            c.hide()
        self.readout.hide()

    def _wheel(self, vb, orientation: str, steps: float, scene_pos) -> None:
        f = ZOOM_STEP ** steps
        p = vb.mapSceneToView(scene_pos)
        if orientation == "bottom":
            vb.scaleBy(x=f, y=None, center=p)
        else:
            vb.scaleBy(x=None, y=f, center=p)

    def _axis_drag(self, vb, orientation: str, ev) -> None:
        """Left drag on an axis pans that axis only; the content follows the pointer."""
        if ev.isStart() or getattr(self, "_axis_start", None) is None:
            self._axis_start = vb.viewRange()
        (x0, x1), (y0, y1) = self._axis_start
        d = ev.pos() - ev.buttonDownPos()
        if orientation == "bottom":
            dx = -d.x() / max(1.0, vb.width()) * (x1 - x0)
            vb.setXRange(x0 + dx, x1 + dx, padding=0)
        else:
            dy = d.y() / max(1.0, vb.height()) * (y1 - y0)
            vb.setYRange(y0 + dy, y1 + dy, padding=0)
        if ev.isFinish():
            self._axis_start = None

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self._esc = True
            for vb in (self.vbv, self.vbi):
                vb.rbScaleBox.hide()
            e.accept()
            return
        super().keyPressEvent(e)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._place()

    def _place(self) -> None:
        self.msg.setGeometry(20, 20, self.width() - 40, self.height() - 40)

