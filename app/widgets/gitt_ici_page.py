"""GITT / ICI page: five areas navigated like DCIR, sharing one analysis state per dataset.

    1 Setup  ›  2 IR Drop  ›  3 OCV  ›  4 Pulse Fitting  ›  5 Diffusion Results

The stepper buttons (theme ``#Step``) open an area; areas 2–5 unlock once Proceed has recognised the impulses of the
selected cycle or half-cycle. Every area reads the same GittIciState through GittIciController, so a change made in
one (a manual IR sample, a sampled-OCV point, an extrapolation setting, a fit window, an acceptance) is seen by all
the others and invalidates only what depends on it. The header shows the running calculation with Cancel.

Widgets never compute: IR drops, rest OCV, fits and D come from gregorithm through the controller; acceptance rules
come from app.gitt_ici_review. Only the open area is redrawn, and each view caches what it drew.
"""
from __future__ import annotations
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QMessageBox, QProgressBar, QPushButton, QRadioButton, QStackedWidget, QVBoxLayout,
                               QWidget)

from ..file_dialogs import dataset_folder, save_export
from ..gitt_ici import D_UNITS, selected_series, series_label, validate
from ..gitt_ici_ir import resistance_chart_data
from ..gitt_ici_plot import chart_data, ocv_curves
from ..gitt_ici_review import (ACCEPTED, MODIFIED, PENDING, accepted_sets, exclusion_text, pseudo_ocv,
                               rejected_pulses, usage)
from .gitt_ici_fit_view import FitView
from .gitt_ici_ir_view import IrDropView
from .gitt_ici_ocv_view import OcvView
from .gitt_ici_results_view import ResultsView
from .gitt_ici_setup import SetupView, button

class GittIciExportDialog(QDialog):
    """Export Table… as in the other result pages: CSV or Parquet, chosen columns, and charge / discharge in two
    files (default) or one file with a Branch column. The file name is chosen after Export; never overwrites."""

    def __init__(self, parent, columns: list[tuple[str, str]], rows: dict):
        super().__init__(parent)
        self.setWindowTitle("Export GITT / ICI Results")
        self.setMinimumWidth(500)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)
        title = QLabel("Export GITT / ICI results")
        title.setStyleSheet("font-size: 13.5pt; font-weight: 500;")
        v.addWidget(title)
        counts = " · ".join(f"{len(r)} {branch}" for branch, r in rows.items())
        v.addWidget(_muted(f"Rows: one per impulse ({counts or 'none'}). D only where it is in use."))
        f = QHBoxLayout()
        f.addWidget(QLabel("Format"))
        self.csv, self.parquet = QRadioButton("CSV"), QRadioButton("Parquet")
        self.csv.setChecked(True)
        f.addWidget(self.csv)
        f.addWidget(self.parquet)
        f.addStretch(1)
        v.addLayout(f)
        self.split = QCheckBox("Charge and discharge in separate files")
        self.split.setChecked(True)
        v.addWidget(self.split)
        v.addWidget(QLabel("Columns"))
        grid = QGridLayout()
        self.boxes: dict[str, QCheckBox] = {}
        for i, (key, head) in enumerate(columns):
            b = QCheckBox(head)
            b.setChecked(True)
            if key == "impulse":
                b.setEnabled(False)
            self.boxes[key] = b
            grid.addWidget(b, i // 2, i % 2)
        v.addLayout(grid)
        v.addWidget(_muted("Values at full precision in the units shown. Columns without any value are not offered; "
                           "empty cells stay empty."))
        row = QHBoxLayout()
        row.addStretch(1)
        cancel, ok = button("Cancel", "ghost"), button("Export…", "goElab")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        v.addLayout(row)

    def choice(self) -> tuple[str, list[str], bool]:
        """(format, column keys, split by branch)."""
        return (("parquet" if self.parquet.isChecked() else "csv"),
                [k for k, b in self.boxes.items() if b.isChecked()], self.split.isChecked())


def _muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setProperty("muted", True)
    return label


STEPS = (("setup", "1  Setup"), ("ir", "2  IR Drop"), ("ocv", "3  OCV"), ("fit", "4  Pulse Fitting"),
         ("results", "5  Diffusion Results"))
NEXT = {"ir": ("ocv", "Continue to OCV ›"), "ocv": ("fit", "Continue to Pulse Fitting ›"),
        "fit": ("results", "Continue to Diffusion Results ›")}
PREVIOUS = {"ir": "setup", "ocv": "ir", "fit": "ocv", "results": "fit"}


class GittIciPage(QWidget):
    """The GITT / ICI section page; all actions go through GittIciController."""

    def __init__(self, theme, controller):
        super().__init__()
        self.theme, self.ctl, self.ds_id = theme, controller, None
        self._entry_errors: list[str] = []
        self._keys: dict[str, object] = {}                 # step -> what its view last drew (skip identical redraws)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 12)
        root.setSpacing(10)
        head = QHBoxLayout()
        title = QLabel("GITT / ICI")
        title.setStyleSheet("font-size: 16pt; font-weight: 500;")
        head.addWidget(title)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setProperty("muted", True)
        head.addWidget(self.status, 1)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(180)
        self.progress.setTextVisible(False)
        self.cancel = button("Cancel", "ghost")
        head.addWidget(self.progress)
        head.addWidget(self.cancel)
        root.addLayout(head)
        steps = QHBoxLayout()
        steps.setSpacing(8)
        self.step_buttons: dict[str, QPushButton] = {}
        for i, (key, text) in enumerate(STEPS):
            if i:
                arrow = QLabel("›")
                arrow.setProperty("muted", True)
                steps.addWidget(arrow)
            b = QPushButton(text)
            b.setObjectName("Step")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, k=key: self.go(k))
            self.step_buttons[key] = b
            steps.addWidget(b)
        steps.addStretch(1)
        root.addLayout(steps)
        self.setup = SetupView(theme)
        self.ir_view = IrDropView(theme)
        self.ocv_view = OcvView(theme)
        self.fit_view = FitView(theme)
        self.results = ResultsView(theme)
        self.stack = QStackedWidget()
        self.views = {"setup": self.setup, "ir": self.ir_view, "ocv": self.ocv_view, "fit": self.fit_view,
                      "results": self.results}
        for view in self.views.values():
            self.stack.addWidget(view)
        root.addWidget(self.stack, 1)
        root.addWidget(self._footer())
        self._connect()
        controller.changed.connect(lambda ds: self.refresh() if ds == self.ds_id else None)

    def _footer(self) -> QFrame:
        """Back / Continue bar of the areas after Setup: Continue is filled once the area is complete."""
        self.footer = QFrame()
        self.footer.setProperty("panel", True)
        row = QHBoxLayout(self.footer)
        row.setContentsMargins(12, 8, 12, 8)
        self.back = button("‹ Back", "ghost")
        self.next_hint = QLabel()
        self.next_hint.setProperty("muted", True)
        self.next_btn = button("Continue ›", "goElab")
        self.next_btn.setMinimumWidth(240)
        row.addWidget(self.back)
        row.addStretch(1)
        row.addWidget(self.next_hint)
        row.addWidget(self.next_btn)
        self.back.clicked.connect(lambda: self._model() and self.go(PREVIOUS.get(self._model().step, "setup")))
        self.next_btn.clicked.connect(lambda: self._model() and self.go(NEXT[self._model().step][0]))
        return self.footer

    def _fill_footer(self, model) -> None:
        """Show Back / Continue and whether the open area still has items to review."""
        step = model.step
        self.footer.setVisible(step != "setup")
        self.next_btn.setVisible(step in NEXT)
        if step not in NEXT:
            self.next_hint.setText("")
            return
        if step == "ir":
            states = list(self.ctl.ir_states(self.ds_id).values())
            todo, what = states.count(PENDING) + states.count(MODIFIED), "impulse IR drop(s)"
        elif step == "ocv":
            states = list(self.ctl.rest_states(self.ds_id).values())
            todo, what = states.count(PENDING) + states.count(MODIFIED), "rest period(s)"
        elif step == "fit":
            states = list(self.ctl.pulse_states(self.ds_id).values())
            todo, what = states.count(PENDING) + states.count(MODIFIED), "impulse fit(s)"
        else:
            states, todo, what = [ACCEPTED], 0, ""
        done = todo == 0 and not model.is_running
        self.next_btn.setText(NEXT[step][1])
        self.next_btn.setProperty("goElab", done)
        self.next_btn.setProperty("ghost", not done)
        self.next_btn.style().unpolish(self.next_btn)
        self.next_btn.style().polish(self.next_btn)
        self.next_hint.setText("Area complete" if done else f"{todo} {what} still to review")

    def _connect(self) -> None:
        ctl = self.ctl
        ds = lambda: self.ds_id                                      # noqa: E731  (current dataset at call time)
        self.cancel.clicked.connect(lambda: ds() and ctl.cancel(ds()))
        self.setup.paramsEdited.connect(self._params_edited)
        self.setup.proceed.connect(lambda: ds() and ctl.apply(ds()))
        self.ir_view.eventSelected.connect(lambda eid: ds() and ctl.open_detail(ds(), eid))
        self.ir_view.sampleChosen.connect(lambda eid, side, row: ds() and ctl.set_ir_sample(ds(), eid, side, row))
        self.ir_view.timesApplied.connect(lambda pre, post: ds() and ctl.set_ir_times(ds(), pre, post))
        self.ir_view.localTimesChosen.connect(lambda eid, times: ds() and ctl.set_ir_local_times(ds(), eid, times))
        self.ir_view.acceptRequested.connect(lambda eids: ds() and ctl.accept_ir(ds(), eids))
        self.ir_view.revokeRequested.connect(lambda eid: ds() and ctl.revoke_ir(ds(), eid))
        self.ir_view.rejectRequested.connect(lambda eid: ds() and ctl.reject_ir(ds(), eid))
        self.ocv_view.restSelected.connect(self._open_rest)
        self.ocv_view.sampleChosen.connect(lambda rest, row: ds() and ctl.set_ocv_sample(ds(), rest, row))
        self.ocv_view.sampleTimeChosen.connect(lambda rest, t: ds() and ctl.set_ocv_time(ds(), rest, t))
        self.ocv_view.settingsApplied.connect(
            lambda on, target, frac, t: ds() and ctl.set_ocv_settings(ds(), extrapolation=on, target_s=target,
                                                                      fit_start=frac, sample_time=t))
        self.ocv_view.extrapolationChosen.connect(
            lambda rest, target, frac: ds() and ctl.set_rest_extrapolation(ds(), rest, target, frac))
        self.ocv_view.acceptRequested.connect(lambda rests: ds() and ctl.accept_rests(ds(), rests))
        self.ocv_view.revokeRequested.connect(lambda rest: ds() and ctl.revoke_rest(ds(), rest))
        self.ocv_view.rejectRequested.connect(lambda rest: ds() and ctl.reject_rest(ds(), rest))
        self.fit_view.eventSelected.connect(lambda eid: ds() and ctl.open_detail(ds(), eid))
        self.fit_view.seriesSelected.connect(lambda identity: ds() and ctl.select(ds(), identity))
        self.fit_view.windowApplied.connect(lambda eid, role, lo, hi: ds() and ctl.apply_window(ds(), eid, role, lo,
                                                                                                hi))
        self.fit_view.windowAppliedAll.connect(lambda role, lo, hi: ds() and ctl.apply_window_all(ds(), role, lo, hi))
        self.fit_view.fitSeriesChosen.connect(lambda series: ds() and ctl.set_fit_series(ds(), series))
        self.fit_view.reapplyRequested.connect(lambda eid: ds() and ctl.reapply_automatic(ds(), eid))
        self.fit_view.calculateAllRequested.connect(lambda: ds() and ctl.calculate_all(ds()))
        self.fit_view.acceptRequested.connect(lambda eids: ds() and ctl.accept_pulses(ds(), eids))
        self.fit_view.revokeRequested.connect(lambda eid: ds() and ctl.revoke_pulse(ds(), eid))
        self.fit_view.rejectRequested.connect(lambda eid: ds() and ctl.reject_pulse(ds(), eid))
        self.results.optionsChanged.connect(lambda: self._fill("results", force=True))
        self.results.pointClicked.connect(self._focus)
        self.results.save.clicked.connect(self._save_image)
        self.results.exportRequested.connect(self._export)
        self.results.dUnitChanged.connect(lambda unit: ds() and ctl.set_d_unit(ds(), unit))

    # --- navigation --------------------------------------------------------------------------------------------------
    def _model(self):
        return self.ctl.model(self.ds_id) if self.ds_id else None

    def go(self, step: str) -> None:
        """Open an area; areas after Setup need recognised impulses."""
        model = self._model()
        if model is None or (step != "setup" and not model.events):
            return
        model.step = step
        if step == "ocv" and model.open_rest is None:
            rests = self.ctl.rest_ocv(self.ds_id)
            model.open_rest = rests[0].rest_segment_id if rests else None
        if step in ("ir", "fit") and not model.open_event and model.events:
            self.ctl.open_detail(self.ds_id, model.events[0].event_id)
        self.refresh()

    def _open_rest(self, rest_id: int) -> None:
        model = self._model()
        if model is not None:
            model.open_rest = rest_id
            self.refresh()

    def _focus(self, area: str, event_id: str) -> None:
        """A clicked result point opens its impulse in Pulse Fitting (D) or IR Drop Analysis (R)."""
        if self.ds_id:
            self.ctl.open_detail(self.ds_id, event_id)
            self.go("fit" if area == "fit" else "ir")

    # --- setup -------------------------------------------------------------------------------------------------------
    def show_dataset(self, ds_id: Optional[str]) -> None:
        """Show one dataset; Setup is loaded from that dataset's own draft (never carried over)."""
        self.ds_id = ds_id
        model = self._model()
        if model is not None:
            dataset = self.ctl.state.get(ds_id)
            result = None if dataset is None else dataset.elab.result
            self.setup.load(model.draft, getattr(result, "n_cycles", 0), getattr(result, "n_half_cycles", 0))
        self._entry_errors = []
        self.refresh()

    def _params_edited(self, params, errors: list) -> None:
        self._entry_errors = errors
        if params is not None and self.ds_id:
            self.ctl.set_params(self.ds_id, params)
        else:
            self.refresh()

    # --- refresh -----------------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        """Synchronise the header, the stepper and the open area with the dataset's state."""
        model = self._model()
        if model is None:
            self.setEnabled(False)
            return
        self.setEnabled(True)
        running = model.is_running
        self.cancel.setEnabled(running and not model.cancel_requested)
        self.cancel.setVisible(running)
        self.progress.setVisible(running)
        self.progress.setRange(0, 0 if running and model.progress is None else 100)
        self.progress.setValue(round((model.progress or 0) * 100))
        text = model.error or (f"{model.phase} · impulse {model.done}/{model.total}" if running and model.total
                               else model.phase if running else model.notice)
        self.status.setText(text or "")
        for key, b in self.step_buttons.items():
            b.setEnabled(key == "setup" or bool(model.events))
            b.setProperty("current", "true" if key == model.step else "false")
            b.style().unpolish(b)
            b.style().polish(b)
        self.stack.setCurrentWidget(self.views[model.step])
        self._fill(model.step)
        self._fill_footer(model)

    def _fill(self, step: str, force: bool = False) -> None:
        model = self._model()
        if model is None:
            return
        running = model.is_running
        snap = None if model.snapshot is None else model.snapshot.fingerprint
        key = (self.ds_id, running, snap, model.ir_key, model.rest_key, model.open_event, id(model.trace),
               model.open_rest, model.selected, model.fit_series, model.applied,
               tuple(sorted(model.rest_reviews.items())),
               tuple(sorted(model.pulse_reviews.items())), tuple(sorted(model.windows.items())), model.d_unit,
               len(model.events), model.exporting, tuple(sorted(model.ir_samples.items())),
               tuple(sorted(model.ocv_samples.items())), tuple(sorted(model.ocv_overrides.items())),
               tuple(sorted(model.ocv_fit_overrides.items())), tuple(sorted(model.ir_reviews.items())))
        if step != "setup" and not force and self._keys.get(step) == key:
            return
        self._keys[step] = key
        ds = self.ctl.state.get(self.ds_id)
        if step == "setup":
            self.setup.show_state(model.draft, self._entry_errors, validate(model.draft), running,
                                  ds is not None and ds.elab.result is not None)
        elif step == "ir":
            self.ir_view.set_data(model.events, self.ctl.ir_drops(self.ds_id), model.open_event, model.trace,
                                  self._ir_usage(model.applied), editable=not running,
                                  states=self.ctl.ir_states(self.ds_id))
            self.ir_view.set_times(model.applied.ir_sample_time_s, model.applied.ir_post_sample_time_s, not running)
        elif step == "ocv":
            rests = self.ctl.rest_ocv(self.ds_id)
            positions = {}
            for i, e in enumerate(model.events, start=1):
                if e.rest_after_segment_id is not None:
                    positions.setdefault(e.rest_after_segment_id, f"after #{i}")
                if e.rest_before_segment_id is not None:
                    positions.setdefault(e.rest_before_segment_id, f"before #{i}")
            self.ocv_view.set_data(rests, self.ctl.rest_states(self.ds_id), pseudo_ocv(model.records, model.events),
                                   positions, model.open_rest, model.applied, model.ocv_overrides, running,
                                   model.ocv_fit_overrides, model.ocv_samples)
        elif step == "fit":
            self.fit_view.set_data(model.events, model.records, self.ctl.pulse_states(self.ds_id), model.open_event,
                                   model.trace, model.selected, model.windows, model.applied, running, model.d_unit,
                                   model.fit_series)
        elif step == "results":
            self._fill_results(model, running, ds)

    @staticmethod
    def _ir_usage(params) -> str:
        """Which applied IR corrections use the pre-pulse sample (the default applies to Delacourt only)."""
        gitt = {"none": "GITT Δ: no IR correction", "selected_sample": "GITT Δ: corrected with this pre-pulse sample",
                "gitt_intercept": "GITT Δ: corrected with the GITT fit intercept (not this sample)"}
        parts = [gitt.get(params.ir_correction, params.ir_correction)]
        if "delacourt_fixed" in params.methods:
            parts.append("Delacourt fixed offset: this pre-pulse sample (default, Delacourt only)")
        parts.append("R_off feeds no D result")
        return " · ".join(parts) + ". Sample times: above, or in Advanced Settings (top right)."

    def _fill_results(self, model, running: bool, ds) -> None:
        params = model.applied
        rests = self.ctl.rest_ocv(self.ds_id)
        rest_ok, pulse_ok = accepted_sets(model, params)
        rejected = rejected_pulses(model)
        by_id = {e.event_id: e for e in model.events}
        used, reasons = [], {}
        for record in model.records:
            event = by_id.get(record.identity.event_id)
            if event is None:
                continue
            u = usage(record, event, params, rest_ok, pulse_ok, rejected)
            if u.used:
                used.append(record)
            else:
                key = exclusion_text(u.reason)
                reasons[key] = reasons.get(key, 0) + 1
        mass = None if ds is None or not ds.elab.sample.active_mass_mg else ds.elab.sample.active_mass_mg / 1000.0
        self.results.d_unit.blockSignals(True)
        self.results.d_unit.setCurrentIndex(max(0, self.results.d_unit.findData(model.d_unit)))
        self.results.d_unit.blockSignals(False)
        pseudo = pseudo_ocv(model.records, model.events)
        self.results.set_methods([(series_label(r.identity), series_label(r.identity)) for r in used]
                                 if used else [])
        self.results.set_availability(mass is not None, any(model.branch_end_ah.values()), bool(pseudo),
                                      params.extrapolation_active)
        opts = self.results.options()
        d_data = chart_data(used, model.events, unit=model.d_unit, axis=opts["axis"], log=opts["log"],
                            branch_end_ah=model.branch_end_ah, mass_g=mass)
        ocv = ocv_curves(rests, pseudo, model.events, axis=opts["axis"], branch_end_ah=model.branch_end_ah,
                         mass_g=mass)
        drops, ir_ok = self.ctl.ir_drops(self.ds_id), self.ctl.accepted_ir(self.ds_id)
        r_data = resistance_chart_data(model.events, {k: v for k, v in drops.items() if k in ir_ok},
                                       axis=opts["axis"], log=False, branch_end_ah=model.branch_end_ah, mass_g=mass)
        excluded = [f"Not drawn: {n} result(s) · {reason}" for reason, n in sorted(reasons.items())]
        if len(drops) > len(ir_ok & drops.keys()):
            excluded.append(f"Not drawn: R_on / R_off of {len(drops) - len(ir_ok & drops.keys())} impulse(s) · "
                            "IR drop not accepted")
        self.results.draw(d_data, ocv, r_data, opts["log"],
                          f"Y1 · D [{D_UNITS[model.d_unit][0]}] · {'logarithmic' if opts['log'] else 'linear'}",
                          excluded)
        self.results.export.setEnabled(not running and not model.exporting and bool(used))
        if not selected_series(params):
            self.results.message.setText("No calculation method is selected in Setup.")

    # --- files -------------------------------------------------------------------------------------------------------
    def _export(self) -> None:
        dataset = self.ctl.state.get(self.ds_id) if self.ds_id else None
        table = self.ctl.export_table(self.ds_id) if dataset is not None else None
        if table is None:
            return
        dlg = GittIciExportDialog(self, table.columns, table.rows)
        if not dlg.exec():
            return
        fmt, columns, split = dlg.choice()
        ext = ".csv" if fmt == "csv" else ".parquet"
        path = save_export(self, "Export GITT / ICI results", f"{dataset.name} - gitt-ici{ext}",
                           "CSV (*.csv)" if fmt == "csv" else "Parquet (*.parquet)", folder=dataset_folder(self.ctl.state.get(self.ds_id)))
        if path:
            if not path.lower().endswith(ext):
                path += ext
            self.ctl.export(self.ds_id, path, fmt, columns, split)

    def _save_image(self) -> None:
        """Save Image: the results chart exactly as shown, fixed palette, PNG (as in the other result pages)."""
        dataset = self.ctl.state.get(self.ds_id) if self.ds_id else None
        if dataset is None or self.stack.currentWidget() is not self.results:
            return
        path = save_export(self, "Save Image", f"{dataset.name} - GITT-ICI diffusion results.png",
                           "PNG image (*.png)", folder=dataset_folder(self.ctl.state.get(self.ds_id)))
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        if self.results.save_png(path):
            bar = getattr(self.window(), "statusBar", None)
            if callable(bar):
                bar().showMessage(f"Image saved: {path}", 6000)
            else:
                self.status.setText(f"Image saved: {path}")
        else:
            QMessageBox.warning(self, "Save Image", "Could not save the image.")
