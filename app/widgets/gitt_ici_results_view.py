"""GITT / ICI Diffusion Results: D (Y1), OCV curves (Y2) and IR drop resistances (Y3) against capacity or SoC.

Only D results that are USED are drawn: current, with a finite D, pulse fit accepted when the method needs one and
every rest it takes its OCV from accepted (gitt_ici_review.usage). Every other result is counted below the chart
with the reason it is excluded. Y1 is logarithmic by default, or linear (log draws D > 0 only); Y2 carries the sampled,
pseudo and extrapolated OCV curves of the rest periods (each rest once, never joined across sources or branches);
Y3 the pre-pulse (R_on) and post-pulse (R_off) resistances measured in IR Drop Analysis, the same gregorithm values.

The options panel chooses independently the X quantity (capacity, specific capacity from the sample's active mass,
or SoC; unavailable choices are disabled with the reason), the Y1 scale and which method, OCV and resistance series
are shown; changing them only redraws. Hovering a point shows its provenance; clicking a D point opens its impulse
in Pulse Fitting, an R point in IR Drop Analysis. Accepted current D are exported from here.

The chart style (lines, markers or both for D and R; marker size; charge and discharge colours) is edited in the
GITT / ICI dock (Results Chart), like the plot style and colours of the other result pages; it is display only and
shared by every dataset. With lines only, the points stay clickable and hoverable but are not drawn.
"""
from __future__ import annotations
import math

import pyqtgraph as pg
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QSizePolicy, QSplitter, QToolButton, QToolTip, QVBoxLayout, QWidget)

from ..explorer import CHART, PLOT_STYLES
from ..gitt_ici import D_UNITS
from ..gitt_ici_plot import X_AXES
from ..dqdv import CYCLE_PALETTE
from ..icons import make_icon
from .chart import FONT_FAMILY, _font
from .gitt_ici_chart import MAGENTA, OCHRE, PALETTE, PURPLE, SEA, SLATE
from .gitt_ici_setup import labelled, muted

D_COLORS = CYCLE_PALETTE                                        # the app's series palette (dQ/dV, Comparison)
OCV_COLORS = {"Sampled": SLATE, "Pseudo": SEA, "Extrapolated": MAGENTA}
OCV_STYLE = {"Sampled": Qt.PenStyle.DashLine, "Pseudo": Qt.PenStyle.DashDotLine, "Extrapolated": Qt.PenStyle.DotLine}
R_COLORS = {"R_on": OCHRE, "R_off": PURPLE}
SYMBOL = {"charge": "o", "discharge": "t"}
# Colour by branch (default): charge and discharge in the app's primary and secondary chart colours, on every axis;
# the series is told by its marker (D) or line style (OCV). Colour by method keeps one colour per D series and tells
# the branch by the marker.
R_SYMBOLS = {"R_on": "d", "R_off": "+"}
BRANCH_COLORS = {"charge": PALETTE["charge"], "discharge": PALETTE["discharge"]}
METHOD_SYMBOLS = ("o", "s", "t", "d", "star", "h")
SYMBOL_GLYPHS = {"o": "●", "s": "■", "t": "▼", "d": "◆", "star": "★", "h": "⬢"}
BRANCHES = (("both", "Charge and discharge"), ("charge", "Charge only"), ("discharge", "Discharge only"))
MARKER_SIZES = (("6", "Small"), ("9", "Medium"), ("12", "Large"))     # px of the D markers (R is one px smaller)
DEFAULT_STYLE, DEFAULT_MARKER_SIZE = "markers", 9


def short_label(label: str) -> str:
    """Compact series name for the options panel: 'Delacourt (fixed offset) · Extrapolated OCV' becomes
    'Delacourt · Extrapolated' (the full name stays in the tooltip)."""
    return (label.replace(" (fixed offset)", "").replace(" (free offset)", " free").replace(" OCV", "")
            .replace("Pseudo-OCV (ICI intercept)", "Pseudo"))


class LogAxis(pg.AxisItem):
    """Axis whose logarithmic ticks are labelled with the real value 10^v and never with an SI prefix."""

    def __init__(self, orientation: str):
        super().__init__(orientation)
        self.log = False

    def tickStrings(self, values, scale, spacing):  # noqa: N802  (pyqtgraph override)
        """Real value for log ticks; default formatting otherwise."""
        if self.log:
            return [f"{10 ** v:.3g}" for v in values]
        return super().tickStrings(values, scale, spacing)


class ResultsView(QWidget):
    """Three-axis results chart with its options panel and export."""
    optionsChanged = Signal()
    pointClicked = Signal(str, str)                  # area ('fit' | 'ir'), event id
    exportRequested = Signal()
    dUnitChanged = Signal(str)

    def __init__(self, theme=None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.plot_style = DEFAULT_STYLE                              # one of PLOT_STYLES
        self.marker_size = DEFAULT_MARKER_SIZE
        self.branch_colors = dict(BRANCH_COLORS)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Orientation.Horizontal)
        surface = QWidget()
        surface.setObjectName("ChartSurface")
        surface.setStyleSheet(f"#ChartSurface {{ background: {CHART['bg']}; border: 1px solid {CHART['frame']};"
                              " border-radius: 10px; }")
        sl = QVBoxLayout(surface)
        sl.setContentsMargins(6, 6, 6, 6)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)
        ll.addLayout(self._toolbar())
        ll.addWidget(surface, 1)
        self.y1 = LogAxis("left")
        self.y3 = LogAxis("right")
        self.plot = pg.PlotWidget(axisItems={"left": self.y1})
        self.plot.setBackground(CHART["bg"])
        self.plot.setMinimumHeight(380)
        self.pi = self.plot.getPlotItem()
        self.pi.setMenuEnabled(False)
        self.pi.showGrid(x=True, y=True, alpha=0.3)
        self.pi.showAxis("right")
        self.vb_ocv = pg.ViewBox(enableMenu=False)
        self.vb_r = pg.ViewBox(enableMenu=False)
        self.pi.scene().addItem(self.vb_ocv)
        self.pi.scene().addItem(self.vb_r)
        self.pi.getAxis("right").linkToView(self.vb_ocv)
        self.pi.layout.addItem(self.y3, 2, 3)                 # third axis, right of the OCV axis
        self.y3.linkToView(self.vb_r)
        for vb in (self.vb_ocv, self.vb_r):
            vb.setXLink(self.pi)
        self.pi.vb.sigResized.connect(self._geometry)
        for ax in (self.pi.getAxis("bottom"), self.y1, self.pi.getAxis("right"), self.y3):
            ax.setPen(pg.mkPen(CHART["frame"]))
            ax.setTextPen(pg.mkPen(CHART["tick"]))
            ax.setTickFont(_font(10))
        sl.addWidget(self.plot, 1)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setStyleSheet(f"color: {CHART['tick']}; font-family: {FONT_FAMILY}; font-size: 9pt;")
        sl.addWidget(self.message)
        split.addWidget(left)
        split.addWidget(self._options())
        split.setStretchFactor(0, 1)
        split.setSizes([900, 340])
        lay.addWidget(split, 1)
        self._items: dict[str, list] = {}
        self.plot.scene().sigMouseMoved.connect(self._hover)

    # --- options -----------------------------------------------------------------------------------------------------
    def _options(self) -> QWidget:
        body = QWidget()
        self._options_body = body
        body.setObjectName("ResultsOptions")
        body.setMinimumWidth(300)
        outer = QVBoxLayout(body)
        outer.setContentsMargins(0, 0, 4, 0)
        outer.setSpacing(8)

        def group(title: str, key: str) -> QVBoxLayout:
            frame = QFrame()
            frame.setProperty("panel", True)
            lay = QVBoxLayout(frame)
            lay.setContentsMargins(12, 8, 12, 10)
            lay.setSpacing(4)
            lay.addWidget(labelled(title, self.theme, key, bold=True))
            frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)  # never squeezed
            outer.addWidget(frame)
            return lay

        lay = group("Axes", "results_x")
        self.axis = QComboBox()
        for code, text in X_AXES:
            self.axis.addItem(text, code)
        self.scale = QComboBox()
        self.scale.addItem("Y1 linear", False)
        self.scale.addItem("Y1 logarithmic", True)
        self.scale.setCurrentIndex(1)               # D spans decades: logarithmic by default
        lay.addWidget(QLabel("X axis"))
        lay.addWidget(self.axis)
        lay.addWidget(QLabel("Y1 scale and D unit"))
        lay.addWidget(self.scale)
        self.d_unit = QComboBox()
        for code, (text, _f) in D_UNITS.items():
            self.d_unit.addItem(f"D in {text}", code)
        lay.addWidget(self.d_unit)
        self.branch = QComboBox()
        for code, text in BRANCHES:
            self.branch.addItem(text, code)
        self.branch.setAccessibleName("Branches shown")
        self.colour = QComboBox()
        self.colour.addItem("Colour by branch", "branch")
        self.colour.addItem("Colour by method", "method")
        self.colour.setAccessibleName("Colour of the series")
        lay.addWidget(QLabel("Branches and colours"))
        lay.addWidget(self.branch)
        lay.addWidget(self.colour)
        self.branch_legend = QLabel()
        self.branch_legend.setTextFormat(Qt.TextFormat.RichText)
        self._branch_legend()
        lay.addWidget(self.branch_legend)
        for combo in (self.axis, self.scale, self.d_unit, self.branch, self.colour):
            combo.setMinimumHeight(32)                       # themed height, known before the style is applied
        self.d_unit.currentIndexChanged.connect(lambda: self.dUnitChanged.emit(self.d_unit.currentData()))
        self.axis_note = muted()
        # A wrapped label reports a one-line height to its layout; reserve three lines so the card is never cut.
        self.axis_note.setMinimumHeight(self.axis_note.fontMetrics().lineSpacing() * 3 + 4)
        lay.addWidget(self.axis_note)
        self.boxes: dict[str, QCheckBox] = {}
        for title, keys in (("Y1 · diffusion coefficients", ()), ("Y2 · OCV curves",
                                                                   ("Sampled", "Pseudo", "Extrapolated")),
                            ("Y3 · IR drop resistance", ("R_on", "R_off"))):
            holder = group(title, "results_y")
            if title.startswith("Y1"):
                self.method_box = holder
            for key in keys:
                text = {"R_on": "◇  Pre-pulse resistance (R_on)", "R_off": "✚  Post-pulse resistance (R_off)"}.get(
                    key, f"{key} OCV")
                box = QCheckBox(text)
                box.setChecked(False)                         # overlays are opt-in; D series start ticked
                color = OCV_COLORS.get(key) or R_COLORS.get(key)
                box.setStyleSheet(f"color: {color}; font-weight: 500;")
                self.boxes[key] = box
                holder.addWidget(box)
                box.toggled.connect(self.optionsChanged)
        outer.addStretch(1)
        self._fit_options()
        self.axis.currentIndexChanged.connect(self.optionsChanged)
        self.scale.currentIndexChanged.connect(self.optionsChanged)
        self.branch.currentIndexChanged.connect(self.optionsChanged)
        self.colour.currentIndexChanged.connect(self._recolour)
        self.colour.currentIndexChanged.connect(self.optionsChanged)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea, #qt_scrollarea_viewport, #ResultsOptions { background: transparent; }")
        scroll.setWidget(body)
        return scroll

    def _toolbar(self) -> QHBoxLayout:
        """Chart header as in the other result pages: title, then Export Table… and the Reset View / Save Image tools."""
        bar = QHBoxLayout()
        title = QLabel("Diffusion results")
        title.setStyleSheet("font-size: 10.5pt; font-weight: 500;")
        bar.addWidget(title)
        bar.addStretch(1)
        self.export = QPushButton("Export Table…")
        self.export.setProperty("ghost", True)
        self.export.setFixedHeight(30)
        self.export.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export.setToolTip("One row per impulse: capacity, SoC, IR drops, OCV, slopes and D in use, charge and "
                               "discharge in separate files.")
        self.export.clicked.connect(self.exportRequested)
        bar.addWidget(self.export)
        self.reset, self.save = QToolButton(), QToolButton()
        for b, name in ((self.reset, "Reset View"), (self.save, "Save Image…")):
            b.setObjectName("ChartTool")
            b.setFixedSize(34, 32)
            b.setIconSize(QSize(18, 18))
            b.setToolTip(name)
            b.setAccessibleName(name.split("…")[0])
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            bar.addWidget(b)
        self.reset.clicked.connect(self.reset_view)
        if self.theme is not None:
            self.theme.changed.connect(self._icons)
        self._icons()
        return bar

    # --- chart style (edited in the GITT / ICI dock) ---------------------------------------------------------------
    def set_plot_style(self, style: str) -> None:
        """Lines, markers or lines + markers for D and R; unknown values are ignored."""
        if style in dict(PLOT_STYLES) and style != self.plot_style:
            self.plot_style = style
            self.optionsChanged.emit()

    def set_marker_size(self, size: int) -> None:
        """Marker size [px] of the D points (R points are one px smaller)."""
        if size > 0 and size != self.marker_size:
            self.marker_size = size
            self.optionsChanged.emit()

    def set_branch_color(self, branch: str, color: str) -> None:
        """Colour of the charge or discharge branch (used when colouring by branch)."""
        if branch in self.branch_colors and self.branch_colors[branch] != color:
            self.branch_colors[branch] = color
            self._branch_legend()
            self.optionsChanged.emit()

    def reset_colors(self) -> None:
        """Charge and discharge back to the app's chart colours."""
        if self.branch_colors != BRANCH_COLORS:
            self.branch_colors = dict(BRANCH_COLORS)
            self._branch_legend()
            self.optionsChanged.emit()

    def _branch_legend(self) -> None:
        self.branch_legend.setText(" &nbsp; ".join(f"<span style='color:{c}'>■</span> {b}"
                                                   for b, c in self.branch_colors.items()))

    def _icons(self, *_args) -> None:
        if self.theme is None:
            return
        t = self.theme.tokens
        for b, name in ((self.reset, "rotate-ccw"), (self.save, "download")):
            b.setIcon(make_icon(name, t.muted, disabled=t.dis, size=18))

    def reset_view(self) -> None:
        """Fit every axis to the drawn points again."""
        for vb in (self.pi.vb, self.vb_ocv, self.vb_r):
            vb.enableAutoRange()

    def showEvent(self, event) -> None:  # noqa: N802  (Qt override)
        """Re-measure the options once the theme has sized every control."""
        super().showEvent(event)
        self._fit_options()

    def _fit_options(self) -> None:
        """Keep the options panel at its natural height so its cards are never squeezed: the panel scrolls."""
        body = self._options_body
        body.layout().activate()
        body.setMinimumHeight(body.layout().sizeHint().height())

    def set_methods(self, available: list[tuple[str, str]]) -> None:
        """Checkboxes for the D series (method · OCV source labels) available now; new ones start ticked. Each box
        is written in the colour of its series, so the options panel is also the legend."""
        available = sorted(set(available))
        keys = [k for k, _l in available]
        for key in list(self.boxes):
            if key.startswith("D:") and key[2:] not in keys:
                self.boxes.pop(key).deleteLater()
        for key, label in available:
            if f"D:{key}" not in self.boxes:
                box = QCheckBox(short_label(label))
                box.setToolTip(label)
                box.setChecked(True)
                self.boxes[f"D:{key}"] = box
                self.method_box.addWidget(box)
                box.toggled.connect(self.optionsChanged)
        self._methods = [k for k, _l in available]
        self._recolour()
        self._fit_options()

    def _recolour(self) -> None:
        """Write each option in the colour (by method) or with the marker (by branch) it has in the chart, so the
        options panel is also the legend."""
        by_branch = self.colour.currentData() != "method"
        self.branch_legend.setVisible(by_branch)
        for i, key in enumerate(getattr(self, "_methods", [])):
            box = self.boxes[f"D:{key}"]
            label = short_label(box.toolTip() or key)
            if by_branch:
                box.setText(f"{SYMBOL_GLYPHS[METHOD_SYMBOLS[i % len(METHOD_SYMBOLS)]]}  {label}")
                box.setStyleSheet("font-weight: 500;")
            else:
                box.setText(label)
                box.setStyleSheet(f"color: {D_COLORS[i % len(D_COLORS)]}; font-weight: 500;")
        for key in ("Sampled", "Pseudo", "Extrapolated", "R_on", "R_off"):
            colour = OCV_COLORS.get(key) or R_COLORS.get(key)
            self.boxes[key].setStyleSheet("font-weight: 500;" if by_branch else f"color: {colour}; font-weight: 500;")

    def set_availability(self, has_mass: bool, has_soc: bool, has_pseudo: bool, has_extrapolated: bool) -> None:
        """Disable X choices and OCV curves that need data this analysis does not have, saying why."""
        notes = []
        for code, ok, why in (("specific_mah_g", has_mass, "Specific capacity needs the active material mass "
                                                           "(Data Elaboration → sample)."),
                              ("soc", has_soc, "SoC needs a non-zero branch capacity.")):
            item = self.axis.model().item(self.axis.findData(code))
            item.setEnabled(ok)
            if not ok:
                notes.append(why)
                if self.axis.currentData() == code:
                    self.axis.setCurrentIndex(0)
        for key, ok, why in (("Pseudo", has_pseudo, "Pseudo OCV exists only for ICI results."),
                             ("Extrapolated", has_extrapolated, "Extrapolated OCV is off (OCV Analysis).")):
            self.boxes[key].setEnabled(ok)
            self.boxes[key].setToolTip("" if ok else why)
        self.axis_note.setText(" ".join(notes))
        self._fit_options()

    def options(self) -> dict:
        """Current display options."""
        return {"axis": self.axis.currentData(), "log": bool(self.scale.currentData()),
                "branch": self.branch.currentData(), "colour": self.colour.currentData(),
                "shown": {k for k, b in self.boxes.items() if b.isChecked() and b.isEnabled()}}

    # --- drawing -----------------------------------------------------------------------------------------------------
    def _geometry(self) -> None:
        rect = self.pi.vb.sceneBoundingRect()
        self.vb_ocv.setGeometry(rect)
        self.vb_r.setGeometry(rect)

    def draw(self, d_data, ocv_series, r_data, log: bool, d_label: str, excluded: list[str]) -> None:
        """Draw the used D (``d_data``: ChartData of used records), the OCV series and the resistance ChartData,
        filtered by the ticked options; ``excluded`` lines explain the D results not drawn."""
        options = self.options()
        shown = options["shown"]
        by_branch = options["colour"] != "method"

        def wanted(branch: str) -> bool:
            return options["branch"] in ("both", branch)
        self.pi.clear()
        self.vb_ocv.clear()
        self.vb_r.clear()
        self._items = {}
        self.pi.setLabel("bottom", d_data.x_label)
        self.pi.setLabel("left", d_label)
        self.y1.log = log
        self.y1.enableAutoSIPrefix(not log)
        self.y1.picture = None
        order = sorted({series.label for series in d_data.d_series})
        lines = self.plot_style in ("lines", "lines_markers")
        markers = self.plot_style != "lines"
        for series in d_data.d_series:
            if f"D:{series.label}" not in shown or not wanted(series.branch):
                continue
            index = order.index(series.label)
            if by_branch:
                color = self.branch_colors.get(series.branch, D_COLORS[0])
                symbol = METHOD_SYMBOLS[index % len(METHOD_SYMBOLS)]
            else:
                color = D_COLORS[index % len(D_COLORS)]
                symbol = SYMBOL.get(series.branch, "o")
            points = sorted((p for p in series.points if not log or p.y > 0), key=lambda p: p.x)
            ys = [math.log10(p.y) if log else p.y for p in points]
            if lines and len(points) > 1:
                self.pi.addItem(pg.PlotDataItem([p.x for p in points], ys, pen=pg.mkPen(color, width=1.5)))
            # Lines only: the points are still there (click, hover) but drawn without pen and brush.
            pen, brush = ((pg.mkPen(color, width=1.5), pg.mkBrush(color)) if markers
                          else (pg.mkPen(None), pg.mkBrush(None)))
            scatter = pg.ScatterPlotItem(hoverable=False)
            scatter.addPoints([{"pos": (p.x, y), "data": ("fit", p), "size": self.marker_size, "symbol": symbol,
                                "pen": pen, "brush": brush} for p, y in zip(points, ys)])
            scatter.sigClicked.connect(self._clicked)
            self.pi.addItem(scatter)
            self._items.setdefault("d", []).append(scatter)
        ocv_on = False
        for series in ocv_series:
            kind = "Pseudo" if series.label.startswith("Pseudo") else series.label.split()[0]
            if kind not in shown or not wanted(series.branch):
                continue
            ocv_on = True
            color = self.branch_colors.get(series.branch, OCV_COLORS[kind]) if by_branch else OCV_COLORS[kind]
            line = pg.PlotDataItem([x for x, _ in series.points], [v for _, v in series.points],
                                   pen=pg.mkPen(color, width=1.3, style=OCV_STYLE[kind]), symbol="s",
                                   symbolSize=5, symbolBrush=color, symbolPen=color)
            self.vb_ocv.addItem(line)
            self._items.setdefault("ocv", []).append(line)
        r_on = False
        for series in r_data.d_series:
            if series.label not in shown or not wanted(series.branch):
                continue
            r_on = True
            color = (self.branch_colors.get(series.branch, PURPLE) if by_branch
                     else R_COLORS.get(series.label, PURPLE))
            points = sorted(series.points, key=lambda p: p.x)
            if lines and len(points) > 1:
                self.vb_r.addItem(pg.PlotDataItem([p.x for p in points], [p.y for p in points],
                                                  pen=pg.mkPen(color, width=1.2, style=Qt.PenStyle.DashLine)))
            pen = pg.mkPen(color, width=1.2) if markers else pg.mkPen(None)
            scatter = pg.ScatterPlotItem(hoverable=False)
            scatter.addPoints([{"pos": (p.x, p.y), "data": ("ir", p), "size": max(self.marker_size - 1, 4),
                                "symbol": R_SYMBOLS.get(series.label, "d"), "pen": pen, "brush": pg.mkBrush(None)}
                               for p in points])
            scatter.sigClicked.connect(self._clicked)
            self.vb_r.addItem(scatter)
            self._items.setdefault("r", []).append(scatter)
        right = self.pi.getAxis("right")
        right.setLabel("Y2 · OCV [V]" if ocv_on else "")
        right.setStyle(showValues=ocv_on)
        self.y3.setLabel("Y3 · R [Ω]" if r_on else "")
        self.y3.setStyle(showValues=r_on)
        self.y3.setVisible(r_on)
        for vb in (self.pi.vb, self.vb_ocv, self.vb_r):
            vb.enableAutoRange()
        notes = list(d_data.omitted) + list(excluded)
        if not d_data.d_series:
            notes.insert(0, "No D is used yet: accept the rest periods (OCV Analysis) and the pulse fits (Pulse "
                            "Fitting) it depends on.")
        self.message.setText("\n".join(notes))

    def visible_points(self, kind: str = "d") -> list:
        """Points drawn for 'd' or 'r' (DPoint objects)."""
        return [spot.data()[1] for item in self._items.get(kind, []) for spot in item.points()]

    def _clicked(self, _item, points, _ev=None) -> None:
        if len(points):
            area, point = points[0].data()
            event_id = point.identity if isinstance(point.identity, str) else point.identity.event_id
            self.pointClicked.emit(area, event_id)

    def _hover(self, scene_pos) -> None:
        for item in self._items.get("d", []) + self._items.get("r", []):
            hits = item.pointsAt(item.mapFromScene(scene_pos))
            if len(hits):
                QToolTip.showText(QCursor.pos(), hits[0].data()[1].tooltip, self.plot)
                return
        QToolTip.hideText()

    def save_png(self, path: str) -> bool:
        """Save the chart as PNG; returns success."""
        return self.plot.grab().save(path, "PNG")
