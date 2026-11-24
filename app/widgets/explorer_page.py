"""Data Explorer page: header, one row of X / Y1 / Y2 selectors with the chart tools, the chart, and the
footer with the point counter and Render All Points. Widgets never decide: they call ExplorerController.edit(...) with a
pure ExplorerState method and re-read the state on `changed`."""
from __future__ import annotations
from typing import Optional
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton,
                               QToolButton, QVBoxLayout, QWidget)
from ..file_dialogs import dataset_folder, save_export
from ..explorer import (COLUMNS, Mode, Phase, axis_label, describe_available, filter_text, format_coverage)
from ..explorer_controller import ExplorerController
from ..formatting import fmt_int
from ..icons import make_icon, pixmap
from ..theme import ThemeManager
from .chart import ChartWidget
from .combos import FitComboBox
from .controls import ElidedLabel, FlowLayout, InfoButton, StatusPill, group

COUNTER_INFO = (
    "Rendered / Total counts distinct real rows inside the visible X range and the applied cycle filter.",
    "A row used by both Y curves counts once. Vertical axis limits only clip the drawing and do not change the count. "
    "Rows with no value for any active Y series cannot be drawn and are excluded.",
    "Render All Points draws every eligible row as a flat image (100%).",
)
SEL_W = 120                     # narrowest X / Y selector; it grows with the chosen name


def _swatch(color: Optional[str]) -> QIcon:
    pm = QPixmap(28, 12)
    pm.fill(Qt.GlobalColor.transparent)
    if color:
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawRoundedRect(0, 4, 28, 5, 2, 2)
        p.end()
    return QIcon(pm)


class ExplorerPage(QWidget):
    openSettingsRequested = Signal()

    def __init__(self, theme: ThemeManager, ctl: ExplorerController):
        super().__init__()
        self.theme, self.ctl = theme, ctl
        self.ds_id: Optional[str] = None
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 12)
        root.setSpacing(10)
        root.addLayout(self._header())
        root.addWidget(self._selectors())
        self.banner = self._banner()
        root.addWidget(self.banner)
        self.chart = ChartWidget()
        root.addWidget(self.chart, 1)
        root.addWidget(self._footer())
        self.chart.viewportRequested.connect(lambda vp: self._edit(lambda e: e.set_viewport(vp), debounce=True))
        self.chart.plotResized.connect(ctl.set_plot_size)
        self.chart.hoverRow.connect(lambda row: ctl.request_details(self.ds_id, row) if self.ds_id else None)
        self.chart.overlayButton.connect(self._overlay_action)
        self.chart.cancelRender.connect(lambda: ctl.cancel_render(self.ds_id))
        ctl.changed.connect(lambda ds: self.refresh() if ds == self.ds_id else None)
        ctl.viewReady.connect(lambda ds: self._draw() if ds == self.ds_id else None)
        ctl.flatReady.connect(lambda ds: self._flat() if ds == self.ds_id else None)
        ctl.composer = self._compose
        ctl.detailsReady.connect(self._details)
        ctl.units.changed.connect(lambda ds: self.refresh() if ds == self.ds_id else None)
        theme.changed.connect(self._icons)
        self._icons()

    # ---------------------------------------------------------------- construction
    def _header(self) -> QHBoxLayout:
        h = QHBoxLayout()
        h.setSpacing(12)
        t = QLabel("Data Explorer")
        t.setStyleSheet("font-size: 12pt; font-weight: 500;")
        self.meta = ElidedLabel()                     # ends with "…" (full text as tooltip) when short of room
        self.meta.setProperty("muted", True)
        self.meta.setStyleSheet("font-size: 9pt;")
        self.units_pill = StatusPill(self.theme)
        self.result_pill = StatusPill(self.theme)
        h.addWidget(t)
        h.addWidget(self.meta)
        h.addWidget(self.units_pill)
        h.addStretch(1)
        h.addWidget(self.result_pill)
        return h

    def _selectors(self) -> QWidget:
        """X / Y1 / Y2 selectors and the chart tools; the row wraps instead of cutting a column name."""
        bar = QWidget()
        h = FlowLayout(bar, h_spacing=14, v_spacing=6, last_right=True)
        self.combos: dict[str, QComboBox] = {}
        for axis, label in (("x", "X"), ("y1", "Y1"), ("y2", "Y2")):
            l = QLabel(label)
            l.setFixedWidth(18)
            l.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            l.setStyleSheet("font-size: 9pt; font-weight: 500;")
            l.setProperty("muted", True)
            c = FitComboBox(min_width=SEL_W)      # always as wide as the chosen column name
            c.setAccessibleName(f"{label} axis")
            c.setIconSize(QSize(14, 6))
            c.activated.connect(lambda i, a=axis, cb=c: self._edit(lambda e: e.set_axis(a, cb.itemData(i))))
            l.setBuddy(c)
            h.addWidget(group(l, c, spacing=6))
            self.combos[axis] = c
        tools = QFrame()
        tools.setObjectName("Segmented")
        tl = QHBoxLayout(tools)
        tl.setContentsMargins(2, 2, 2, 2)
        tl.setSpacing(2)
        self.t_box, self.t_pan = QToolButton(), QToolButton()
        self.t_reset, self.t_save = QToolButton(), QToolButton()
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        specs = ((self.t_box, "Box Zoom", "Box Zoom · drag in the chart", True),
                 (self.t_pan, "Pan", "Pan · or hold Shift and drag", True),
                 (self.t_reset, "Reset View", "Reset View · show all data in the current filter", False),
                 (self.t_save, "Save Image", "Save Image… · saves the chart with every point", False))
        for i, (b, name, tip, check) in enumerate(specs):
            b.setObjectName("ChartTool")
            b.setFixedSize(34, 32)
            b.setIconSize(QSize(18, 18))
            b.setToolTip(tip)
            b.setAccessibleName(name)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if check:
                b.setCheckable(True)
                grp.addButton(b)
            if i == 2:
                sep = QFrame()
                sep.setObjectName("Sep")
                sep.setFixedSize(1, 18)
                tl.addWidget(sep)
            if i == 3:
                sep = QFrame()
                sep.setObjectName("Sep")
                sep.setFixedSize(1, 18)
                tl.addWidget(sep)
            tl.addWidget(b)
        self.t_box.setChecked(True)
        self.t_box.toggled.connect(lambda on: setattr(self.chart, "pan_mode", not on))
        self.t_reset.clicked.connect(lambda: self._edit(lambda e: e.reset_view()))
        self.t_save.clicked.connect(self._save)
        h.addWidget(tools)
        return bar

    def _banner(self) -> QFrame:
        f = QFrame()
        f.setProperty("banner", "err")
        bl = QHBoxLayout(f)
        bl.setContentsMargins(14, 10, 10, 10)
        bl.setSpacing(12)
        self.banner_icon = QLabel()
        col = QVBoxLayout()
        col.setSpacing(2)
        self.banner_title = QLabel()
        self.banner_title.setStyleSheet("font-weight: 500;")
        self.banner_text = QLabel()
        self.banner_text.setProperty("muted", True)
        self.banner_text.setStyleSheet("font-size: 9pt;")
        self.banner_text.setWordWrap(True)
        self.banner_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        col.addWidget(self.banner_title)
        col.addWidget(self.banner_text)
        self.banner_x = QToolButton()
        self.banner_x.setObjectName("TabClose")
        self.banner_x.setAccessibleName("Dismiss")
        self.banner_x.setToolTip("Dismiss")
        self.banner_x.clicked.connect(self._dismiss)
        bl.addWidget(self.banner_icon, 0, Qt.AlignmentFlag.AlignTop)
        bl.addLayout(col, 1)
        bl.addWidget(self.banner_x, 0, Qt.AlignmentFlag.AlignTop)
        f.hide()
        return f

    def _footer(self) -> QWidget:
        """Point counter, gesture hint and Render All Points; wraps instead of cutting them in a narrow window."""
        bar = QWidget()
        h = FlowLayout(bar, h_spacing=12, v_spacing=4, last_right=True)
        cap = QLabel("Rendered / Total")
        cap.setProperty("muted", True)
        cap.setStyleSheet("font-size: 9pt;")
        self.counter = QLabel()
        self.counter.setStyleSheet("font-size: 9.75pt; font-weight: 500;")
        self.counter.setAccessibleName("Rendered / Total")
        self.counter.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.counter_info = InfoButton(self.theme, "Rendered / Total", COUNTER_INFO)
        hint = QLabel("Wheel over an axis zooms that axis")
        hint.setProperty("muted", True)
        hint.setStyleSheet("font-size: 9pt;")
        self.render_btn = QPushButton("Render All Points")
        self.render_btn.setProperty("ghost", True)
        self.render_btn.setCheckable(True)
        self.render_btn.setIconSize(QSize(16, 16))
        self.render_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.render_btn.clicked.connect(self._toggle_render)
        h.addWidget(group(cap, self.counter, self.counter_info, spacing=8))
        h.addWidget(group(hint, self.render_btn, spacing=8))
        return bar

    def _icons(self) -> None:
        t = self.theme.tokens
        for b, name in ((self.t_box, "square-dashed"), (self.t_pan, "move"), (self.t_reset, "rotate-ccw"),
                        (self.t_save, "download")):
            b.setIcon(make_icon(name, t.muted, checked=t.pri, disabled=t.dis, size=18))
        self.render_btn.setIcon(make_icon("render-all-points", t.text, checked=t.pri, disabled=t.dis, size=16))
        self.banner_x.setIcon(make_icon("x", t.muted, size=14))
        self.refresh()

    # ---------------------------------------------------------------- behaviour
    def _edit(self, fn, reload: bool = True, debounce: bool = False) -> None:
        if self.ds_id:
            self.ctl.edit(self.ds_id, fn, reload=reload, debounce=debounce)

    def show_dataset(self, ds_id: Optional[str]) -> None:
        self.ds_id = ds_id
        self._fill_combos()
        self._draw()
        self._flat()
        self.refresh()

    def activate(self) -> None:
        """The Explorer became visible for the active dataset."""
        self.ctl.ensure_view(self.ds_id)

    def _fill_combos(self) -> None:
        e = self.ctl.ex(self.ds_id)
        if e is None:
            return
        u = self.ctl.units.units_for(self.ds_id)
        key = (self.ds_id, u, e.x, e.y1, e.y2, e.y1_color, e.y2_color)
        if key == getattr(self, "_combo_key", None):
            return
        self._combo_key = key
        colors = {"x": None, "y1": e.y1_color, "y2": e.y2_color}
        for axis, c in self.combos.items():
            c.blockSignals(True)
            c.clear()
            if axis == "y2":
                c.addItem(_swatch(None), "None", None)
                c.insertSeparator(1)
            for col in COLUMNS:
                c.addItem(_swatch(colors[axis]) if axis != "x" else QIcon(), axis_label(col.key, u), col.key)
            c.setCurrentIndex(c.findData(getattr(e, axis)))
            cur = c.currentText()
            c.setToolTip(cur)
            c.blockSignals(False)

    def refresh(self) -> None:
        e = self.ctl.ex(self.ds_id) if self.ds_id else None
        if e is None or not hasattr(self, "counter"):
            return
        d = self.ctl.state.get(self.ds_id)
        u = self.ctl.units.units_for(self.ds_id)
        rows = d.elab.result.n_rows if d and d.elab.result else 0
        self.meta.setText(f"{d.name} · {fmt_int(rows)} points · Cycles: {filter_text(e.applied)}")
        # pills
        custom = self.ctl.units.is_custom(self.ds_id)
        self.units_pill.setVisible(custom)
        if custom:
            self.units_pill.set("p", "ruler", "Custom units", "This dataset uses its own units (Settings › Units)")
        outdated = bool(d and d.elab.dirty)
        corrected = bool(d and d.elab.result and d.elab.result.correction == "dcir")
        self.result_pill.set("warn" if outdated else "ok", "circle-alert" if outdated else "circle-check",
                             "Based on previous settings" if outdated else
                             ("DCIR corrected" if corrected else "Current result"),
                             "Parameters changed in Data Elaboration since this result was calculated." if outdated else
                             ("The chart shows the corrected result: the DCIR spans are excluded from the cycles (Cycle "
                              "Correction)." if corrected else "The chart shows the current result."))
        # selectors and tools
        self._fill_combos()
        locked = e.locked
        lock_tip = "Turn off Render All Points to change"
        for c in self.combos.values():
            c.setEnabled(not locked)
            if locked:
                c.setToolTip(lock_tip)
        for b in (self.t_box, self.t_pan, self.t_reset):
            b.setEnabled(not locked)
            b.setToolTip(lock_tip if locked else b.accessibleName())
        self.t_save.setEnabled(e.can_save)
        # banner
        msg = e.render_error or e.view_error
        self.banner.setVisible(bool(msg))
        if msg:
            self.banner_icon.setPixmap(pixmap("circle-x", self.theme.tokens.err, 18))
            self.banner_title.setText("Could not render all points" if e.render_error else "Could not load the points")
            self.banner_text.setText(f"{msg}\nThe interactive chart is shown, unchanged." if e.render_error else msg)
        # chart state
        labels = (axis_label(e.x, u), axis_label(e.y1, u), axis_label(e.y2, u) if e.y2 else None)
        self.chart.set_style(e.x, labels, (e.y1_color, e.y2_color), e.legend, e.style)
        self.chart.set_point_tracking(e.point_tracking)
        self.chart.set_faded(e.phase is Phase.UPDATING and self.ctl.is_slow(self.ds_id))
        chip = ""
        if e.mode is Mode.FLAT:
            chip = "All points · flat image, not interactive"
        elif e.phase is Phase.UPDATING and self.ctl.is_slow(self.ds_id):
            chip = "Updating…"
        self.chart.show_chip(chip)
        if e.mode is Mode.RENDERING or e.mode is Mode.FLAT:
            self.chart.show_message(None)
        elif e.phase is Phase.LOADING:
            self.chart.show_message("loader-circle", "Loading points…",
                                    "Reading the result. The chart appears when the first points are ready.")
        elif e.phase is Phase.NO_MATCH:
            avail = self.ctl.available_cycles(self.ds_id) or []
            self.chart.show_message("funnel-x", f"No points for cycles {filter_text(e.applied)}",
                                    f"This result has cycles {describe_available(avail)}. Change the filter in Plot Settings.",
                                    "Clear Filter", "clear_filter")
        elif e.phase is Phase.EMPTY:
            self.chart.show_message("chart-line", "No points in view",
                                    "Zoom out, or press Reset View to show all data in the current filter.",
                                    "Reset View", "reset_view")
        else:
            self.chart.show_message(None)
        self.chart.show_progress(e.mode is Mode.RENDERING, e.render_fraction, e.render_done, e.render_total)
        self.chart.interactive = e.mode is Mode.INTERACTIVE
        # footer
        cov = e.displayed_coverage()
        self.counter.setText(format_coverage(*cov) if cov else format_coverage(None, None))
        view = self.ctl.views.get(self.ds_id)
        if view is not None and view.null_excluded:
            self.counter.setToolTip(f"{view.null_excluded:,} records have no value for the selected axes.")
        else:
            self.counter.setToolTip("")
        self.render_btn.setChecked(e.mode is not Mode.INTERACTIVE)
        self.render_btn.setEnabled(e.can_render)
        self.render_btn.setToolTip(
            "Press again to return to the interactive chart" if e.mode is Mode.FLAT else
            "Rendering… use Cancel on the chart" if e.mode is Mode.RENDERING else
            "Draw every eligible real row in the current X range as a flat, non-interactive image. Can take a while.")

    def _draw(self) -> None:
        self.chart.set_view(self.ctl.views.get(self.ds_id) if self.ds_id else None)

    def _flat(self) -> None:
        self.chart.show_flat(self.ctl.flats.get(self.ds_id) if self.ds_id else None)

    def _details(self, ds_id: str, row: int, values) -> None:
        e = self.ctl.ex(ds_id)
        if ds_id == self.ds_id and e is not None:
            self.chart.show_details(row, values, (e.x, e.y1, e.y2))

    def _dismiss(self) -> None:
        def fn(e):
            e.dismiss_render_error()
            e.view_error = ""
            return False
        self._edit(fn, reload=False)

    def _overlay_action(self, action: str) -> None:
        if action == "clear_filter":
            self._edit(lambda e: e.clear_filter())
        elif action == "reset_view":
            self._edit(lambda e: e.reset_view())

    def _size(self) -> tuple[int, int, float]:
        """Plot-area size of the data layer requested from Render All Points (see backend.FlatStyle)."""
        return (*self.chart.plot_size(), self.chart.devicePixelRatioF())

    def _compose(self, ds_id: str):
        return self.chart.export_png() if ds_id == self.ds_id and self.chart.flat.visible else None

    def _toggle_render(self) -> None:
        e = self.ctl.ex(self.ds_id)
        if e is None:
            return
        if e.mode is Mode.FLAT:
            self.ctl.leave_flat(self.ds_id)
        elif e.mode is Mode.INTERACTIVE:
            self.ctl.render(self.ds_id, self._size())
        self.refresh()

    def _save(self) -> None:
        """Save Image always saves the image with EVERY point in view. From the interactive chart: choose the
        file, render with progress and Cancel, write, then show the flat image that was saved."""
        e = self.ctl.ex(self.ds_id)
        if e is None or not e.can_save:
            return
        d = self.ctl.state.get(self.ds_id)
        u = self.ctl.units.units_for(self.ds_id)
        name = f"{d.name} - {axis_label(e.y1, u).split(' [')[0]} vs {axis_label(e.x, u).split(' [')[0]}.png"
        path = save_export(self, "Save Image", name, "PNG image (*.png)", folder=dataset_folder(self.ctl.state.get(self.ds_id)))
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        if e.mode is Mode.FLAT:
            self.ctl.write_png(self.ds_id, path)
        else:
            self.ctl.render(self.ds_id, self._size(), save_path=path)

