"""Comparison page: family selector Data Explorer | Capacity | dQ/dV, the family controls (one Y only, no second
axis), one cycle filter with the original IDs, one large overlay chart (MultiChart), footer with counts, Render All
Points for the Data Explorer family. Datasets are chosen in the Comparison Settings dock. Nothing is computed here."""
from __future__ import annotations
import numpy as np
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QStackedWidget, QToolButton, QVBoxLayout, QWidget)
from ..file_dialogs import save_export
from ..capacity import CAP_COLUMNS, CAP_BY_KEY, cap_label, hidden_note, missing_in_all
from ..comparison import (CAPACITY, DQDV, EXPLORER, FAMILIES, OUTDATED, footer_capacity, footer_dqdv, missing_note)
from ..comparison_controller import ComparisonController
from ..dqdv import PHASE_CHOICES, PLOT_CHOICES, PHASES, dqdv_unit
from ..explorer import COLUMNS, Viewport, axis_label, format_coverage
from ..icons import make_icon
from ..theme import ThemeManager, set_prop
from .combos import FitComboBox
from .controls import FlowLayout, SegmentedControl, group
from .multi_chart import LegendEntry, MultiChart, Trace


def _lab(text="", muted=False, size=None, weight=None) -> QLabel:
    l = QLabel(text)
    if muted:
        l.setProperty("muted", True)
    css = (f"font-size: {size * 0.75:g}pt;" if size else "") + (f"font-weight: {weight};" if weight else "")
    if css:
        l.setStyleSheet(css)
    return l


def _btn(text, kind, h=30) -> QPushButton:
    b = QPushButton(text)
    b.setProperty(kind, True)
    b.setFixedHeight(h)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


EXPLORER_KEYS = [c.key for c in COLUMNS if c.key not in ("rest", "step", "source_row")]
CAPACITY_KEYS = [c.key for c in CAP_COLUMNS if c.key != "cycle"]


class ComparisonPage(QWidget):
    def __init__(self, theme: ThemeManager, ctl: ComparisonController):
        super().__init__()
        self.theme, self.ctl = theme, ctl
        self._combo_key = None
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 12)
        root.setSpacing(10)
        head = QWidget()
        h = FlowLayout(head, h_spacing=12, v_spacing=6, last_right=True)   # wraps instead of cutting the summary
        self.meta = _lab(muted=True, size=12)
        h.addWidget(_lab("Comparison", size=16, weight=500))
        h.addWidget(self.meta)
        self.fam = SegmentedControl(list(FAMILIES), small=False, aria="What to compare")
        self.fam.valueChanged.connect(self.ctl.set_family)
        h.addWidget(self.fam)
        root.addWidget(head)
        self.body = QStackedWidget()
        self.body.addWidget(self._chart_view())       # 0
        self.body.addWidget(self._empty())            # 1
        root.addWidget(self.body, 1)
        ctl.changed.connect(self.refresh)
        ctl.dataReady.connect(lambda: self._draw(force=True))
        ctl.flatReady.connect(self._flat)
        theme.changed.connect(self._icons)
        self._icons()

    # ================================================================ construction
    def _chart_view(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        bar = QWidget()
        h = FlowLayout(bar, h_spacing=12, v_spacing=6, last_right=True)   # wraps instead of cutting the names
        self.x_lab, self.y_lab = _lab("X", muted=True, size=12, weight=500), _lab("Y", muted=True, size=12, weight=500)
        self.x_cb, self.y_cb = FitComboBox(min_width=150), FitComboBox(min_width=150)   # fit the chosen name
        for cb, axis in ((self.x_cb, "x"), (self.y_cb, "y")):
            cb.setAccessibleName(f"{axis.upper()} axis")
            cb.activated.connect(lambda i, a=axis, c=cb: self._set_axis(a, c.itemData(i)))
        self.ph_lab, self.pl_lab = _lab("Phase", muted=True, size=12, weight=500), _lab("Plot", muted=True, size=12, weight=500)
        self.phase = SegmentedControl(list(PHASE_CHOICES), small=True, aria="Phase")
        self.phase.valueChanged.connect(lambda val: self.ctl.edit(lambda cs: setattr(cs.view, "phase", val), reload=False))
        self.plot = SegmentedControl(list(PLOT_CHOICES), small=True, aria="Plot")
        self.plot.valueChanged.connect(lambda val: self.ctl.edit(lambda cs: setattr(cs.view, "plot", val), reload=False))
        self.cyc = QLineEdit()
        self.cyc.setFixedWidth(150)
        self.cyc.setPlaceholderText("All")
        self.cyc.setAccessibleName("Cycles (the same IDs for every dataset)")
        self.cyc.setToolTip("The same original cycle IDs for every dataset; a dataset without a cycle simply has no curve "
                            "for it (nothing is renumbered).")
        self.cyc.textEdited.connect(lambda t: self.ctl.edit(lambda cs: cs.view.type_cycles(t), reload=False))
        self.cyc.returnPressed.connect(lambda: self.ctl.edit(lambda cs: cs.view.apply_cycles()))
        self.cyc_err = QLabel()
        self.cyc_err.setProperty("hintErr", True)
        for pair in ((self.x_lab, self.x_cb), (self.y_lab, self.y_cb), (self.ph_lab, self.phase),
                     (self.pl_lab, self.plot), (_lab("Cycles", muted=True, size=12, weight=500), self.cyc, self.cyc_err)):
            h.addWidget(group(*pair, spacing=8))
        tools = QFrame()
        tools.setObjectName("Segmented")
        tl = QHBoxLayout(tools)
        tl.setContentsMargins(2, 2, 2, 2)
        tl.setSpacing(2)
        self.t_box, self.t_pan, self.t_reset, self.t_save = (QToolButton() for _ in range(4))
        grp = QButtonGroup(self)
        for b, name, tip in ((self.t_box, "Box Zoom", "Box Zoom · drag in the chart"), (self.t_pan, "Pan", "Pan · or hold Shift and drag"),
                             (self.t_reset, "Reset View", "Reset View"), (self.t_save, "Save Image", "Save Image…")):
            b.setObjectName("ChartTool")
            b.setFixedSize(34, 32)
            b.setIconSize(QSize(18, 18))
            b.setToolTip(tip)
            b.setAccessibleName(name)
            if b in (self.t_box, self.t_pan):
                b.setCheckable(True)
                grp.addButton(b)
            tl.addWidget(b)
        self.t_box.setChecked(True)
        self.t_box.toggled.connect(lambda on: setattr(self.chart, "pan_mode", not on))
        self.t_reset.clicked.connect(lambda: self.ctl.edit(lambda cs: setattr(cs.view, "viewport", Viewport())))
        self.t_save.clicked.connect(self._save_image)
        h.addWidget(tools)
        v.addWidget(bar)
        self.chart = MultiChart()
        self.chart.rangeChanged.connect(self._range)
        v.addWidget(self.chart, 1)
        f = QHBoxLayout()
        self.foot = _lab(muted=True, size=12)
        self.foot.setWordWrap(True)
        self.render_btn = _btn("Render All Points", "ghost")
        self.render_btn.clicked.connect(self._render)
        f.addWidget(self.foot, 1)
        f.addWidget(self.render_btn)
        v.addLayout(f)
        return w

    def _empty(self) -> QWidget:
        w = QFrame()
        w.setObjectName("DropZone")
        v = QVBoxLayout(w)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.e_title = _lab(size=20, weight=500)
        self.e_text = _lab(muted=True)
        self.e_text.setWordWrap(True)
        self.e_text.setFixedWidth(520)   # wrapped labels in a centred layout otherwise clip
        for x in (self.e_title, self.e_text):
            x.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            v.addWidget(x, 0, Qt.AlignmentFlag.AlignHCenter)
        return w

    def _icons(self) -> None:
        t = self.theme.tokens
        for b, name in ((self.t_box, "square-dashed"), (self.t_pan, "move"), (self.t_reset, "rotate-ccw"), (self.t_save, "download")):
            b.setIcon(make_icon(name, t.muted, checked=t.pri, disabled=t.dis, size=18))
        self.refresh()

    # ================================================================ behaviour
    def activate(self, on: bool) -> None:
        self.ctl.active = on
        if on:
            self.ctl.plot_px = max(400, self.chart.width())
            self.ctl.load()
            self.refresh()

    def _set_axis(self, axis: str, key) -> None:
        self.ctl.edit(lambda cs: (setattr(cs.view, axis, key), setattr(cs.view, "viewport", Viewport())))

    def _range(self, x, y) -> None:
        reload = self.ctl.cs.family == EXPLORER                  # points are re-read for the new range (sampling)
        self.ctl.edit(lambda cs: setattr(cs.view, "viewport", Viewport(x, y)), reload=reload)

    def refresh(self) -> None:
        if not hasattr(self, "chart"):
            return
        cs = self.ctl.cs
        fam, v = cs.family, cs.view
        kinds = self.ctl.kinds()
        n_open, drawn = len(self.ctl.state.datasets), self.ctl.drawn()
        self.meta.setText(f"{len(drawn)} of {n_open} open datasets · overlay of calculated results · app units")
        self.fam.set_value(fam)
        title, text = cs.message(kinds)
        if title:
            self.body.setCurrentIndex(1)
            self.e_title.setText(title)
            self.e_text.setText(text)
            self.e_text.setMinimumHeight(self.e_text.heightForWidth(self.e_text.width()))
            return
        self.body.setCurrentIndex(0)
        u = self.ctl.app_units
        exp, cap, dq = fam == EXPLORER, fam == CAPACITY, fam == DQDV
        self.x_lab.parentWidget().setVisible(exp)                # each label shares a toolbar group with its control
        self.y_lab.parentWidget().setVisible(exp or cap)
        for wd in (self.ph_lab, self.pl_lab):
            wd.parentWidget().setVisible(dq)
        missing = self._missing_bases() if cap else set()
        cap_keys = [k for k in CAPACITY_KEYS if CAP_BY_KEY[k].basis not in missing]
        if cap and v.y not in cap_keys:
            v.y = "discharge_capacity_ah"                # its Sample value was entered for none of the datasets
        key = (fam, u, v.x, v.y, tuple(sorted(missing)))
        if key != self._combo_key:
            self._combo_key = key
            for cb, keys, cur, lab in ((self.x_cb, EXPLORER_KEYS, v.x, axis_label), (self.y_cb,
                                       EXPLORER_KEYS if exp else cap_keys, v.y, axis_label if exp else cap_label)):
                cb.blockSignals(True)
                cb.clear()
                for k in keys:
                    cb.addItem(lab(k, u), k)
                i = cb.findData(cur)
                cb.setCurrentIndex(max(0, i))
                cb.setToolTip(cb.currentText())
                cb.blockSignals(False)
        self.phase.set_value(v.phase)
        self.plot.set_value(v.plot)
        if not self.cyc.hasFocus():
            self.cyc.setText(v.cycles_text)
        set_prop(self.cyc, "error", bool(v.cycles_error))
        self.cyc_err.setText(v.cycles_error)
        self.cyc_err.setVisible(bool(v.cycles_error))
        self.render_btn.setVisible(exp)
        self.render_btn.setText("Cancel" if self.ctl.rendering else ("Back to Interactive" if self.ctl.flat else "Render All Points"))
        self._draw()

    def _missing_bases(self) -> set[str]:
        """Sample values (mass, area) entered for none of the drawn datasets' Capacity results :
        their specific / areal quantities are not offered in the Y menu, as in the Capacity section."""
        params = []
        for ds_id in self.ctl.drawn():
            d = self.ctl.state.get(ds_id)
            if d is not None and d.capacity.result is not None:
                params.append(d.capacity.result.params)
        return missing_in_all(params)

    # ---------------------------------------------------------------- traces
    def _badge(self, ds_id: str) -> str:
        return " (Outdated)" if self.ctl.kind(ds_id)[0] == OUTDATED else ""

    def _traces(self):
        cs, v = self.ctl.cs, self.ctl.cs.view
        drawn = self.ctl.drawn()
        traces, legend, info = [], [], {}
        names = {d.id: d.name for d in self.ctl.state.datasets}
        dash = "none" if cs.style == "markers" else "solid"
        sym = "o" if cs.style != "lines" else None
        if cs.family == EXPLORER:
            if self.ctl.series is None:
                return None, [], {}
            tot = ren = 0
            for s in self.ctl.series:
                if s.ds_key not in drawn:
                    continue
                traces.append(Trace((s.ds_key,), [(s.curve.x, s.curve.y, None)], cs.color(s.ds_key), cs.line_width,
                                    1.0, dash, sym, names.get(s.ds_key, s.ds_key) + self._badge(s.ds_key)))
                legend.append(LegendEntry(names.get(s.ds_key, s.ds_key) + self._badge(s.ds_key), cs.color(s.ds_key)))
                tot += s.total
                ren += s.rendered
            info = {"rendered": ren, "total": tot}
            return traces, legend, info
        records, missing, ncyc, ncur, npts = 0, [], set(), 0, 0
        for ds in drawn:
            data = self.ctl.table_for(ds)
            if data is None:
                return None, [], {}
            label = names.get(ds, ds) + self._badge(ds)
            if cs.family == CAPACITY:
                cyc = np.asarray(data.values["cycle"], float)
                ids = v.cycles_of([int(c) for c in cyc])
                keep = np.ones(cyc.size, bool) if ids is None else np.isin(cyc.astype(int), np.asarray(ids, int))
                if not keep.any():
                    missing.append(names.get(ds, ds))
                    continue
                y = np.asarray(data.values[v.y], float)
                x, yy = cyc.copy(), y.copy()
                x[~keep], yy[~keep] = np.nan, np.nan                # cycles outside the filter break the line
                traces.append(Trace((ds,), [(x, yy, None)], cs.color(ds), cs.line_width, 1.0, dash, sym or "o", label))
                legend.append(LegendEntry(label, cs.color(ds)))
                records += int((keep & np.isfinite(y)).sum())
            else:
                d = self.ctl.state.get(ds)
                res = d.dqdv.result
                avail = sorted({c.cycle for c in data})
                ids = v.cycles_of(avail)
                shown = avail if ids is None else list(ids)
                if not shown:
                    missing.append(names.get(ds, ds))
                    continue
                kinds = ("raw", "smoothed") if (v.plot == "both" and res.has_smoothed) else \
                    (("smoothed",) if (v.plot == "smoothed" and res.has_smoothed) else ("raw",))
                phases = PHASES if v.phase == "both" else (v.phase,)
                for c in shown:
                    col = cs.cycle_color(ds, shown.index(c), len(shown))
                    ncyc.add(c)
                    legend.append(LegendEntry(f"{label} · cycle {c}", col))
                    for cv in data:
                        if cv.cycle != c or cv.phase not in phases:
                            continue
                        ncur += 1
                        for kind in kinds:
                            segs = [(sg.v, sg.smoothed if kind == "smoothed" else sg.raw, sg.segment_index)
                                    for sg in cv.segments if (sg.smoothed if kind == "smoothed" else sg.raw) is not None]
                            npts += sum(len(sg[0]) for sg in segs)
                            faint = kind == "raw" and len(kinds) == 2
                            traces.append(Trace((ds, c, cv.phase, kind), segs, col, cs.line_width * (0.6 if faint else 1.0),
                                                0.5 if faint else 1.0, dash, sym,
                                                f"{names.get(ds, ds)} · cycle {c} · {cv.phase} · {kind}"))
        info = {"records": records, "missing": missing, "ncyc": len(ncyc), "ncur": ncur, "npts": npts}
        return traces, legend, info

    def _draw(self, force: bool = False) -> None:
        if self.body.currentIndex() != 0:
            return
        cs, v, u = self.ctl.cs, self.ctl.cs.view, self.ctl.app_units
        if cs.family == EXPLORER:
            xl, yl = axis_label(v.x, u), axis_label(v.y, u)
        elif cs.family == CAPACITY:
            xl, yl = "Cycle", cap_label(v.y, u)
        else:
            kind = {"smoothed": "smoothed", "raw": "raw", "both": "raw and smoothed"}[v.plot]
            xl, yl = f"Voltage [{u.voltage}]", f"dQ/dV · {kind} [{dqdv_unit(u.capacity)}]"
        self.chart.set_labels(xl, yl)
        self.chart.y_unit = yl[yl.find("[") + 1:yl.find("]")] if "[" in yl else ""
        traces, legend, info = self._traces()
        if traces is None:
            self.chart.set_traces([])
            self.chart.show_message("Reading the results…")
            self.foot.setText("")
            return
        self.chart.set_traces(traces, legend if cs.legend else [], keep_view=True)
        self.chart.set_range(v.viewport.x, v.viewport.y1)
        self.chart.show_message("" if traces else "No curve for the cycles shown.")
        if cs.family == EXPLORER:
            txt = (f"Rendered {format_coverage(info['rendered'], info['total'])} · each dataset counted once"
                   f" · cycles {v.cycles_text or 'all'}")
        elif cs.family == CAPACITY:
            txt = footer_capacity(len([t for t in traces]), info["records"])
        else:
            txt = footer_dqdv(len({t.key[0] for t in traces}), info["ncyc"], info["ncur"], info["npts"])
        note = missing_note(info.get("missing", []), v.cycles_text) if cs.family != EXPLORER else ""
        tip = hidden_note(self._missing_bases()) if cs.family == CAPACITY else ""
        note = " · ".join(part for part in (note, tip) if part)
        if self.ctl.rendering and self.ctl.render_progress is not None:
            p = self.ctl.render_progress
            amount = (f"{p.done:,} / {p.total:,}" if p.done is not None and p.total is not None
                      else "preparing full-resolution image")
            txt += f" · Rendering all points {amount}"
        self.foot.setText(txt + (f" · {note}" if note else ""))
        self.t_save.setEnabled(bool(traces))

    # ---------------------------------------------------------------- render all / image
    def _render(self) -> None:
        if self.ctl.rendering:
            self.ctl.cancel_render()
        elif self.ctl.flat is not None:
            self.ctl.flat = None
            self._flat()
            self.refresh()
        else:
            v, u = self.ctl.cs.view, self.ctl.app_units
            self.ctl.render_all((axis_label(v.x, u), axis_label(v.y, u)), self.ctl.cs.legend,
                                self.chart.plot_size(), self.devicePixelRatioF(), self.chart.view_ranges())

    def _write(self, path: str) -> bool:
        """Save the chart as shown (axes, legend, every point after Render All Points) at 300 dpi."""
        try:
            with open(path, "wb") as f:                         # the file dialog already confirmed any replacement
                f.write(self.chart.export_png())
            return True
        except OSError:
            return False

    def _flat(self) -> None:
        self.chart.show_flat(self.ctl.flat.png if self.ctl.flat is not None else None)
        path = getattr(self, "_pending_save", None)
        if path and not self.ctl.rendering:
            self._pending_save = None
            if self.ctl.flat is not None:
                if self._write(path):
                    self.window().statusBar().showMessage(f"Image saved: {path}", 6000)
                else:
                    QMessageBox.warning(self, "Save Image", "Could not save the image.")
        self.refresh()

    def _save_image(self) -> None:
        if self.body.currentIndex() != 0:
            return
        fam = dict(FAMILIES)[self.ctl.cs.family]
        path = save_export(self, "Save Image", f"Comparison - {fam}.png", "PNG image (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        if self.ctl.cs.family == EXPLORER and self.ctl.flat is None:
            self._pending_save = path                        # as the Data Explorer: every point, then write
            self._render()
            return
        ok = self._write(path)
        if ok:
            self.window().statusBar().showMessage(f"Image saved: {path}", 6000)
        else:
            QMessageBox.warning(self, "Save Image", "Could not save the image.")
