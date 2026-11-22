"""Right dock of the GITT / ICI section: 'GITT / ICI Settings', opened by the Advanced Settings button like the dock
of every other section.

Two groups, as in the other docks:
- Analysis (expanded): the advanced panel that SetupView builds (GITT Δ IR correction, pre- and post-pulse IR sample
  times, indicator thresholds). The controls stay owned by SetupView, so loading a dataset's draft, reading it back
  and the 'edited' signal work exactly as before; only where they are shown changes.
- Results Chart (collapsed): plot style (lines, markers or both), marker size and the charge / discharge colours of
  Diffusion Results, with Reset Colors, like the plot style and colours of Capacity and DCIR.
Limits: the Analysis settings edit the draft of the dataset shown in Setup and take effect on Proceed; the chart
style is display only, applies at once and is shared by every dataset.
"""
from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ..explorer import PLOT_STYLES
from .controls import ColorSquare, LabelRow, SegmentedControl
from .elab_dock import Accordion
from .gitt_ici_results_view import MARKER_SIZES, ResultsView

BRANCH_NAMES = (("charge", "Charge"), ("discharge", "Discharge"))


class GittIciSettingsDock(QFrame):
    """Dock card with a title, the Analysis panel of the GITT / ICI Setup and the Diffusion Results chart style."""

    def __init__(self, theme, panel: QWidget, results: ResultsView):
        super().__init__()
        self.setObjectName("DockCard")
        self.theme, self.results, self.panel = theme, results, panel
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(12)
        title = QLabel("GITT / ICI Settings")
        title.setStyleSheet("font-size: 11.25pt; font-weight: 500;")
        outer.addWidget(title)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(0)
        scroll = QScrollArea()
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("TabScroll")
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        analysis = Accordion(theme, "Analysis")
        analysis.cl.addWidget(panel)
        lay.addWidget(analysis)

        chart = Accordion(theme, "Results Chart")
        chart.head.setChecked(False)
        chart.cl.addWidget(LabelRow(theme, "Plot Style", ("D and IR drop resistances as markers, lines joining "
                                                          "the impulses of each series, or both. OCV curves are "
                                                          "always lines.",)))
        self.style = SegmentedControl(list(PLOT_STYLES), small=True, aria="Plot style")
        self.style.valueChanged.connect(results.set_plot_style)
        chart.cl.addWidget(self.style, 0, Qt.AlignmentFlag.AlignLeft)
        chart.cl.addWidget(LabelRow(theme, "Marker Size", ("Size of the D markers; R markers are one pixel "
                                                           "smaller.",)))
        self.size = SegmentedControl(list(MARKER_SIZES), small=True, aria="Marker size")
        self.size.valueChanged.connect(lambda v: results.set_marker_size(int(v)))
        chart.cl.addWidget(self.size, 0, Qt.AlignmentFlag.AlignLeft)
        chart.cl.addWidget(LabelRow(theme, "Branch Colors", ("Colours of charge and discharge when the series are "
                                                             "coloured by branch (D, OCV and R).",)))
        self.colors: dict[str, ColorSquare] = {}
        for branch, name in BRANCH_NAMES:
            row = QHBoxLayout()
            square = ColorSquare(theme, name)
            square.colorChosen.connect(lambda color, b=branch: (results.set_branch_color(b, color), self.refresh()))
            row.addWidget(square)
            row.addWidget(QLabel(name))
            row.addStretch(1)
            chart.cl.addLayout(row)
            self.colors[branch] = square
        reset = QPushButton("Reset Colors")
        reset.setProperty("link", True)
        reset.setFixedHeight(30)
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.clicked.connect(lambda: (results.reset_colors(), self.refresh()))
        chart.cl.addWidget(reset, 0, Qt.AlignmentFlag.AlignLeft)
        self.chart_group = chart
        lay.addWidget(chart)
        lay.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        """Show the current chart style of Diffusion Results."""
        self.style.set_value(self.results.plot_style)
        self.size.set_value(str(self.results.marker_size))
        for branch, square in self.colors.items():
            square.set_color(self.results.branch_colors[branch])
