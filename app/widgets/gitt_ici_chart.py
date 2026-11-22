"""GITT / ICI impulse fit chart: V(t) and V(√t) of one segment, one window in seconds. Side by side,
or stacked (``vertical=True``: V(t) above, V(√t) below) as in Pulse Fitting.

GITT VA windows live on the pulse, ICI windows on the rest after the interruption; times are relative to the origin
gregorithm documents for that role. The two plots share ONE window: dragging the region in either plot previews the
bounds (``windowPreview``) and only the release emits ``windowReleased`` for a single recalculation. On the √t
plot the bounds are converted by squaring.

Drawn curves: raw canonical voltage; the recorded √t fit line E = a + s√t (or Delacourt's fitted voltage) over its
effective window, with the real samples that window contains highlighted; when gregorithm applied a constant IR
correction c to the record, also V − c, labelled, drawn once. Fits of the other checked methods of the same
impulse can be overlaid, each with its own colour and label (a √t line and Delacourt's spherical model are different
fits, never presented as equivalent). Nothing is decimated, smoothed or recomputed here.
"""
from __future__ import annotations
import math
from typing import Optional

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..dqdv import CYCLE_PALETTE
from ..explorer import CHART
from ..gitt_ici import ImpulseTrace, fit_curve, fit_window, ir_offset, relative
from .chart import FONT_FAMILY, _font

# Every GITT / ICI chart takes its colours from the app palette (CHART and the 10-colour CYCLE_PALETTE shared with
# dQ/dV and Comparison), by role, so the same thing has the same colour in every area and in the rest of the app.
TEAL, RUST, GREEN, VIOLET, MAGENTA, BLUE, OCHRE, SEA, PURPLE, SLATE = CYCLE_PALETTE
PALETTE = {"data": CHART["y1"], "fit": CHART["y2"], "context": SLATE, "measured": MAGENTA, "delta": VIOLET,
           "target": GREEN, "extrapolated": VIOLET, "pseudo": SEA, "sampled": MAGENTA,
           "charge": CHART["y1"], "discharge": CHART["y2"]}
FIT = PALETTE["fit"]
OTHER_FITS = (VIOLET, GREEN, MAGENTA, OCHRE)
USED = PALETTE["fit"]
CORRECTED = VIOLET
REGION = (0, 109, 133, 40)                          # CHART["y1"] at low alpha
SHADE = (176, 90, 49, 28)                           # CHART["y2"] at low alpha: switching interval, fit interval


class GittIciChart(QWidget):
    """Two synchronized plots of one impulse segment with an editable fit window."""
    windowPreview = Signal(float, float)
    windowReleased = Signal(float, float)

    def __init__(self, parent=None, vertical: bool = False):
        super().__init__(parent)
        self.setObjectName("ChartSurface")
        self.setStyleSheet(f"#ChartSurface {{ background: {CHART['bg']}; border: 1px solid {CHART['frame']};"
                           " border-radius: 10px; }")
        self.setMinimumHeight(240)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self.caption = QLabel()
        self.caption.setWordWrap(True)
        self.caption.setStyleSheet(f"color: {CHART['tick']}; font-family: {FONT_FAMILY}; font-size: 9pt;")
        lay.addWidget(self.caption)
        self.vertical = vertical                            # False: V(t) beside V(√t)
        row = QVBoxLayout() if vertical else QHBoxLayout()
        row.setSpacing(6)
        self.pt, self.ps = pg.PlotWidget(), pg.PlotWidget()
        for plot, xlabel in ((self.pt, "t − t₀ [s]"), (self.ps, "√(t − t₀) [√s]")):
            plot.setBackground(CHART["bg"])
            item = plot.getPlotItem()
            item.hideButtons()
            item.setMenuEnabled(False)
            item.showGrid(x=True, y=True, alpha=0.35)
            for name in ("bottom", "left"):
                ax = item.getAxis(name)
                ax.setPen(pg.mkPen(CHART["frame"]))
                ax.setTextPen(pg.mkPen(CHART["tick"]))
                ax.setTickFont(_font(10))
            item.setLabel("bottom", xlabel)
            item.setLabel("left", "Voltage [V]")
            row.addWidget(plot, 1)
        lay.addLayout(row, 1)
        self.legend = QLabel()                              # shared legend under the plots: never covers data
        self.legend.setTextFormat(Qt.TextFormat.RichText)
        self.legend.setWordWrap(True)
        self.legend.setStyleSheet(f"color: {CHART['tick']}; font-family: {FONT_FAMILY}; font-size: 9pt;")
        lay.addWidget(self.legend)
        self._entries: list[tuple[str, str, str]] = []
        self.region_t = pg.LinearRegionItem(brush=pg.mkBrush(*REGION), movable=True)
        self.region_s = pg.LinearRegionItem(brush=pg.mkBrush(*REGION), movable=True)
        for region, plot in ((self.region_t, self.pt), (self.region_s, self.ps)):
            region.setZValue(-5)
            plot.addItem(region, ignoreBounds=True)
            region.hide()
        self._syncing = False
        self.region_t.sigRegionChanged.connect(lambda: self._sync(self.region_t))
        self.region_s.sigRegionChanged.connect(lambda: self._sync(self.region_s))
        self.region_t.sigRegionChangeFinished.connect(self._released)
        self.region_s.sigRegionChangeFinished.connect(self._released)
        self._editable = False

    # --- drawing ---------------------------------------------------------------------------------------------------
    def clear(self, message: str = "") -> None:
        """Remove every curve and hide the window; ``message`` explains why nothing is drawn."""
        for plot in (self.pt, self.ps):
            item = plot.getPlotItem()
            for data in list(item.listDataItems()):
                item.removeItem(data)

        self.region_t.hide()
        self.region_s.hide()
        self._editable = False
        self.caption.setText(message)
        self._entries = []
        self.legend.setText("")

    def show_record(self, trace: Optional[ImpulseTrace], record, role: Optional[str],
                    window: Optional[tuple[float, float]] = None, others=()) -> None:
        """Draw the segment governed by ``role`` (or the whole pulse when the method has no window) for ``record``.
        ``window`` is the manual window to show; otherwise the record's selected window is shown. ``others`` are
        ``(label, record)`` pairs of the same impulse whose recorded fits are overlaid on the same segment."""
        self.clear()
        if trace is None:
            self.caption.setText("Open an impulse to see its samples.")
            return
        seg_role = role or "pulse"
        origin = trace.origin_s.get(seg_role)
        segment = trace.segment(seg_role)
        if origin is None or not segment.time_s:
            self.caption.setText("This impulse has no samples for the selected segment.")
            return
        points = relative(segment, origin)
        self._curve(points, "Raw voltage", CHART["y1"], 1.0, symbols=True)     # every canonical sample is drawn
        notes = []
        if record is not None:
            ir = ir_offset(record)
            if ir is not None and seg_role == "pulse":
                mode, c = ir
                self._curve([(t, v - c) for t, v in points], f"V − c (IR {mode}, c = {c:.4g} V)", CORRECTED, 1.2)
                notes.append(f"IR correction {mode} applied once by gregorithm (c = {c:.4g} V).")
            elif _delacourt_curve(record):
                notes.append("Delacourt's offset is part of the fitted model; the fit is compared with the raw "
                             "voltage.")
            else:
                notes.append("No IR correction applied to this curve.")
            line = fit_curve(record) if role else []
            if line:
                used = _in_window(points, record)
                if used:
                    self._points(used, "Samples used by the fit")
                self._curve(line, "√t fit (gregorithm)", FIT, 2.0)
            fitted = _delacourt_curve(record)
            if fitted:
                self._curve(fitted, "Delacourt spherical fit (gregorithm)", FIT, 2.0)
        for color, (label, other) in zip(OTHER_FITS, others):
            curve = _delacourt_curve(other) or (fit_curve(other) if (role or "pulse") == "pulse" else [])
            if curve:
                kind = "spherical fit" if _delacourt_curve(other) else "√t fit"
                self._curve(curve, f"{label} · {kind}", color, 1.4)
        if role:
            bounds = window or _selected(record)
            if bounds is None and points:                     # not calculated yet: the suggested whole segment
                bounds = (0.0, points[-1][0])
            if bounds is not None:
                self._set_region(*bounds)
            self._editable = True
            notes.append("Drag the shaded window in either plot; the fit is recalculated when you release it.")
        else:
            notes.append("This method has no editable √t window (GITT Δ uses samples; Delacourt fits the whole "
                         "CC pulse).")
        self.caption.setText(" ".join(notes))
        self.reset_view()

    def reset_view(self) -> None:
        """Rescale both plots to the drawn segment (called for every new impulse, series or window)."""
        for plot in (self.pt, self.ps):
            item = plot.getPlotItem()
            item.enableAutoRange()
            item.autoRange()

    def _points(self, points, name: str) -> None:
        xs = [t for t, _ in points]
        vs = [v for _, v in points]
        kw = dict(pen=None, symbol="o", symbolSize=6, symbolPen=None, symbolBrush=USED, name=name)
        self.pt.plot(xs, vs, **kw)
        self.ps.plot([math.sqrt(t) for t in xs], vs, **kw)
        self._legend(USED, "●", name)

    def _legend(self, color: str, symbol: str, name: str) -> None:
        self._entries.append((color, symbol, name))
        self.legend.setText(" &nbsp;&nbsp; ".join(f"<span style='color:{c}'>{s}</span> {n}"
                                                  for c, s, n in self._entries))

    def _curve(self, points, name: str, color: str, width: float, symbols: bool = False) -> None:
        if not points:
            return
        xs = [t for t, _ in points]
        vs = [v for _, v in points]
        kw = dict(symbol="o", symbolSize=4, symbolPen=None, symbolBrush=color) if symbols else {}
        self.pt.plot(xs, vs, pen=pg.mkPen(color, width=width), name=name, **kw)
        self.ps.plot([math.sqrt(t) for t in xs], vs, pen=pg.mkPen(color, width=width), name=name, **kw)
        self._legend(color, "●" if symbols else "—", name)

    # --- window ----------------------------------------------------------------------------------------------------
    def window(self) -> Optional[tuple[float, float]]:
        """Current window bounds [s], or None when no editable window is shown."""
        if not self._editable or not self.region_t.isVisible():
            return None
        lo, hi = self.region_t.getRegion()
        return float(lo), float(hi)

    def _set_region(self, start_s: float, end_s: float) -> None:
        self._syncing = True
        self.region_t.setRegion((start_s, end_s))
        self.region_s.setRegion((math.sqrt(max(start_s, 0.0)), math.sqrt(max(end_s, 0.0))))
        self._syncing = False
        self.region_t.show()
        self.region_s.show()

    def set_window(self, start_s: float, end_s: float) -> None:
        """Move the window from the numeric fields (no recalculation; Apply does that)."""
        if self._editable and 0 <= start_s < end_s:
            self._set_region(start_s, end_s)

    def _sync(self, source) -> None:
        if self._syncing:
            return
        lo, hi = source.getRegion()
        if source is self.region_s:
            lo, hi = max(lo, 0.0) ** 2, max(hi, 0.0) ** 2
        self._syncing = True
        if source is self.region_s:
            self.region_t.setRegion((lo, hi))
        else:
            self.region_s.setRegion((math.sqrt(max(lo, 0.0)), math.sqrt(max(hi, 0.0))))
        self._syncing = False
        self.windowPreview.emit(float(lo), float(hi))

    def _released(self) -> None:
        if self._syncing or not self._editable:
            return
        lo, hi = self.region_t.getRegion()
        self.windowReleased.emit(float(max(lo, 0.0)), float(hi))


def _selected(record) -> Optional[tuple[float, float]]:
    details = getattr(record, "details", None)
    window = details.get("window") if isinstance(details, dict) else None
    selected = window.get("selected_s") if isinstance(window, dict) else None
    return (float(selected[0]), float(selected[1])) if selected and None not in selected else None


def _delacourt_curve(record) -> list[tuple[float, float]]:
    result = getattr(record, "result", None)
    times = getattr(result, "relative_times_s", None)
    volts = getattr(result, "fitted_voltage_v", None)
    if not times or not volts:
        return []
    return [(t, v) for t, v in zip(times, volts) if t > 0]


def _in_window(points, record) -> list[tuple[float, float]]:
    """Real samples inside the effective window of the record's √t fit (the samples gregorithm fitted)."""
    window = fit_window(record)
    fit = None if window is None else window.get("fit")
    bounds = None if not isinstance(fit, dict) else fit.get("effective_s")
    if not bounds or None in bounds:
        return []
    lo, hi = bounds
    return [(t, v) for t, v in points if lo - 1e-9 <= t <= hi + 1e-9]
