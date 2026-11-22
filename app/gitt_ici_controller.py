"""GITT / ICI controller: sequences recognition and calculations on a worker thread and applies review actions.

All science is delegated to the backend, which calls gregorithm.gitt_ici. This class only:
  * applies the draft parameters when the operator presses Apply / Calculate, restoring them if the job is
    cancelled or fails, so current results always match the applied parameters;
  * distinguishes the global automatic recalculation of every impulse, the local re-application of the
    automatic windows to one impulse for all checked methods, a manual window applied on release
    to one impulse or, in one global job, to every complete impulse, per-rest OCV target and fit start overrides,
    and manual IR drop samples or local sample times (pre-pulse and post-pulse independent) of one impulse;
  * measures the pre-pulse / post-pulse IR drops with gregorithm.measure_ir_drops (cached per applied settings and
    manual samples) so the IR drop analysis, the resistance series and the IR corrections share one source;
  * commits a completed batch with gregorithm.commit_batch and discards late results by run id;
  * measures the sampled / extrapolated OCV of every rest with gregorithm.measure_rest_ocv, and handles a manual
    sampled point or a sampled-OCV time (global or per rest), per-rest extrapolation settings and the global ones
    with targeted recalculation;
  * stores the operator's acceptance of IR drops (IR Drop), rest periods (OCV Analysis) and pulse fits (Pulse
    Fitting) as fingerprints and keeps gregorithm's acceptance of each D equal to the last two
    (gitt_ici_review.sync_acceptance); accepted IR drops only decide which R_on / R_off are drawn and exported.
Interactive edits made while a job runs are queued; only the latest one runs after it, so an older calculation can
never overwrite a newer request, and a batch is always committed onto the snapshot it started from.
"""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional

import gregorithm
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from .backend import Progress
from .gitt_ici import (RUN_GLOBAL, RUN_LOCAL, RUN_RECOGNIZE, WINDOW_ROLE, GittIciParams, GittIciState,
                       selected_series, series_key_of, validate)
from .gitt_ici_ir import SIDE_LABELS, dependent_series, reason_text
from .gitt_ici_export import BRANCH_ORDER, ExportTable, build_table
from .gitt_ici_review import (ACCEPTED, MODIFIED, PENDING, accepted_ir, accepted_sets, ir_fingerprint, ir_state,
                              pulse_fingerprint, pulse_state, REJECT_MARK, REJECTED, rejected_pulses,
                              rest_fingerprint, rest_state, sync_acceptance, usage)


class _Signals(QObject):
    """Cross-thread signals of one controller; every payload carries the dataset id and the run id."""
    progress = Signal(str, int, object)
    finished = Signal(str, int, object)
    failed = Signal(str, int, str)
    cancelled = Signal(str, int)
    exported = Signal(str, object)
    exportFailed = Signal(str, str)


class _Call(QRunnable):
    """Runs ``fn()`` off the GUI thread; ``done(value)`` or ``failed(message)`` is emitted."""

    def __init__(self, fn: Callable, done, failed):
        super().__init__()
        self.fn, self.done, self.failed = fn, done, failed

    def run(self) -> None:
        """Execute the call and report its outcome."""
        try:
            self.done(self.fn())
        except Exception as error:
            self.failed(error)


class _Job(QRunnable):
    """Runs ``work(progress, token)`` off the GUI thread and reports through ``_Signals``."""

    def __init__(self, ds_id: str, run_id: int, work: Callable, token, signals: _Signals):
        super().__init__()
        self.ds_id, self.run_id, self.work, self.token, self.signals = ds_id, run_id, work, token, signals

    def run(self) -> None:
        """Execute the job; cancellation and errors are reported, never raised into Qt."""
        ds, rid, sig = self.ds_id, self.run_id, self.signals
        try:
            value = self.work(lambda p: sig.progress.emit(ds, rid, p), self.token)
            sig.finished.emit(ds, rid, value)
        except gregorithm.OperationCancelled:
            sig.cancelled.emit(ds, rid)
        except Exception as error:                                    # structural failure: reported, state restored
            sig.failed.emit(ds, rid, f"{type(error).__name__}: {error}")


class GittIciController(QObject):
    """Per-dataset GITT/ICI workflow. ``changed(ds_id)`` asks views to refresh; ``notice(ds_id, text)`` is a status
    message for the main window."""
    changed = Signal(str)
    notice = Signal(str, str)
    exportFinished = Signal(str, str)
    exportError = Signal(str, str)

    def __init__(self, state, backend):
        super().__init__()
        self.state, self.backend = state, backend
        self._jobs: dict[str, tuple] = {}          # ds_id -> (run_id, cancellation token, snapshot at start)
        self._export_tokens: dict[str, gregorithm.CancellationToken] = {}   # ds_id -> token of its latest export
        self._queued: dict[str, Callable] = {}     # ds_id -> latest interactive edit requested during a job
        self._sig = _Signals()
        self._sig.progress.connect(self._progress)
        self._sig.finished.connect(self._finished)
        self._sig.failed.connect(self._failed)
        self._sig.cancelled.connect(self._cancelled)
        self._sig.exported.connect(self._exported)
        self._sig.exportFailed.connect(self._export_failed)

    # --- state access ----------------------------------------------------------------------------------------------
    def model(self, ds_id: Optional[str]) -> Optional[GittIciState]:
        """GITT/ICI state of a dataset, or None."""
        ds = self.state.get(ds_id) if ds_id else None
        return None if ds is None else ds.gitt_ici

    def _elab_id(self, ds_id: str) -> Optional[str]:
        ds = self.state.get(ds_id)
        return None if ds is None or ds.elab.result is None else ds.elab.result.result_id

    def _say(self, ds_id: str, model: GittIciState, text: str) -> None:
        model.notice = text
        self.notice.emit(ds_id, text)
        self.changed.emit(ds_id)

    # --- inputs (no calculation) ------------------------------------------------------------------------------
    def set_params(self, ds_id: str, params: GittIciParams) -> None:
        """Store edited inputs as the draft; results and applied parameters are unchanged until Apply."""
        if (model := self.model(ds_id)) is not None:
            model.set_draft(params)
            model.error = "" if not model.is_running else model.error      # a fixed input clears the old message
            self.changed.emit(ds_id)

    def set_d_unit(self, ds_id: str, unit: str) -> None:
        """Change the display unit of D (cm²/s default, m²/s); stored values stay in SI."""
        if (model := self.model(ds_id)) is not None and unit != model.d_unit:
            model.d_unit = unit
            self.changed.emit(ds_id)

    def select(self, ds_id: str, identity) -> None:
        """Make one result series of one impulse the active one for review."""
        if (model := self.model(ds_id)) is not None:
            model.selected = identity
            self.changed.emit(ds_id)

    def set_fit_series(self, ds_id: str, series: tuple[str, str]) -> None:
        """Remember the (series key, OCV source) chosen in "Fit of"; Pulse Fitting keeps showing it when another
        impulse is opened or the area is left and reopened."""
        if (model := self.model(ds_id)) is not None and model.fit_series != tuple(series):
            model.fit_series = tuple(series)
            self.changed.emit(ds_id)

    def open_detail(self, ds_id: str, event_id: str) -> None:
        """Open an impulse in manual mode: load its raw trace, never start a fit just for opening it."""
        model = self.model(ds_id)
        if model is None or not model.scope_id or model.event(event_id) is None:
            return
        if model.open_event != event_id or model.trace is None:
            try:
                model.trace = self.backend.gitt_ici_trace(model.scope_id, event_id)
            except Exception as error:
                model.trace = None
                self._say(ds_id, model, f"Impulse trace unavailable: {error}")
        model.open_event = event_id
        self.changed.emit(ds_id)

    # --- IR drop analysis -------------------------------------------------------------------------------------------
    def ir_drops(self, ds_id: str) -> dict:
        """{event_id: ImpulseIrDrops} of every recognised impulse, measured by gregorithm with the APPLIED sample
        times and the manual samples; recomputed only when one of them or the recognised scope changes."""
        model = self.model(ds_id)
        if model is None or not model.scope_id or not hasattr(self.backend, "gitt_ici_ir_drops"):
            return {}
        p = model.applied
        key = (model.scope_id, p.ir_sample_time_s, p.ir_post_sample_time_s, tuple(sorted(model.ir_samples.items())))
        if model.ir_key != key:
            try:
                model.ir_drops = self.backend.gitt_ici_ir_drops(model.scope_id, p, dict(model.ir_samples))
            except Exception as error:                            # e.g. scope released: report, never invent values
                model.ir_drops = {}
                model.notice = f"IR drops unavailable: {error}"
            model.ir_key = key
        return model.ir_drops

    def set_ir_time(self, ds_id: str, event_id: str, side: str, seconds: Optional[float]) -> None:
        """Local sample time [s] of one side of one impulse (None: back to the global time). gregorithm applies the
        same rule, last sample with t ≤ T, with this impulse's own T; the same targeted recalculation as a manual
        sample follows."""
        if seconds is not None and not seconds > 0:
            model = self.model(ds_id)
            if model is not None:
                self._say(ds_id, model, "The sample time must be greater than 0 s.")
            return
        self.set_ir_sample(ds_id, event_id, side, None if seconds is None else ("time", float(seconds)))

    def set_ir_sample(self, ds_id: str, event_id: str, side: str, row) -> None:
        """Use the real sample ``row`` (or the automatic rule with None) as the measured sample of one side of one
        impulse; see ``set_ir_choices``."""
        self.set_ir_choices(ds_id, event_id, {side: row})

    def set_ir_local_times(self, ds_id: str, event_id: str, times: dict) -> None:
        """Local sample times [s] of one impulse, ``{side: seconds or None}`` (None: back to the global time of that
        side); pre-pulse and post-pulse may differ. One re-measurement and at most one recalculation."""
        model = self.model(ds_id)
        if any(t is not None and not t > 0 for t in times.values()):
            if model is not None:
                self._say(ds_id, model, "The sample time must be greater than 0 s.")
            return
        self.set_ir_choices(ds_id, event_id, {side: None if t is None else ("time", float(t))
                                              for side, t in times.items()})

    def set_ir_choices(self, ds_id: str, event_id: str, choices: dict) -> None:
        """Apply ``{side: row | ('time', T) | None}`` to one impulse: an exact real sample, a local sample time or the
        global rule. gregorithm re-measures the IR drops at once; a row outside that side, or a time with no sample,
        is refused and nothing changes. A pre-pulse change alters the selected-sample IR corrections, so exactly the
        dependent series of this impulse (GITT Δ with that correction, Delacourt fixed offset) are invalidated and
        recalculated; R_off feeds no D."""
        model = self.model(ds_id)
        if model is None or not model.scope_id or model.event(event_id) is None or not choices:
            return
        if self._defer(ds_id, model, lambda: self.set_ir_choices(ds_id, event_id, choices)):
            return
        samples = dict(model.ir_samples)
        for side, row in choices.items():
            if row is None:
                samples.pop((event_id, side), None)
            else:
                samples[(event_id, side)] = row if isinstance(row, tuple) else int(row)
        if samples == model.ir_samples:
            return
        try:
            measured = self.backend.gitt_ici_ir_drops(model.scope_id, model.applied, samples, (event_id,))
        except Exception as error:
            self._say(ds_id, model, f"IR drop unavailable: {error}")
            return
        for side, row in choices.items():
            drop = getattr(measured.get(event_id), side, None)
            if drop is None or row is None:
                continue
            if drop.reason == "manual_sample_invalid" or (isinstance(row, tuple)
                                                          and drop.reason == "no_sample_within_target"):
                self._say(ds_id, model, f"{SIDE_LABELS[side]} IR drop: {reason_text(drop.reason)}")
                return
        changed = {side for side in choices if samples.get((event_id, side)) != model.ir_samples.get((event_id, side))}
        what = " and ".join(SIDE_LABELS[s] for s in ("pre_pulse", "post_pulse") if s in changed) or "IR"
        affected = ()
        if "pre_pulse" in changed and model.snapshot is not None:
            present = {(series_key_of(r.identity), r.identity.ocv_source.value) for r in model.records
                       if r.identity.event_id == event_id}
            affected = tuple(s for s in dependent_series(model.applied) if s in present)
        if not affected:
            model.ir_samples = samples
            self._say(ds_id, model, f"{what} IR drop re-measured; no calculated result depends on it.")
            return
        self._start(ds_id, RUN_LOCAL, apply_draft=False, scan=False, compute=True, event_id=event_id,
                    series=affected, automatic=True, set_ir=samples)

    def _defer(self, ds_id: str, model, request: Callable) -> bool:
        """While a job runs, keep only the latest interactive edit and run it afterwards; True when deferred."""
        if model is not None and model.is_running:
            self._queued[ds_id] = request
            self._say(ds_id, model, "Change queued: it is applied when the running calculation ends.")
            return True
        return False

    def _run_queued(self, ds_id: str) -> None:
        request = self._queued.pop(ds_id, None)
        if request is not None:
            request()

    # --- OCV analysis ----------------------------------------------------------------------------------------------
    def rest_ocv(self, ds_id: str) -> tuple:
        """gregorithm RestOcv of every rest in scope with the applied settings and manual sampled points; recomputed
        only when one of them or the scope changes."""
        model = self.model(ds_id)
        if model is None or not model.scope_id or not hasattr(self.backend, "gitt_ici_rest_ocv"):
            return ()
        p = model.applied
        key = (model.scope_id, p.extrapolation_active, p.ocv_target_s, p.ocv_fit_start_fraction, p.ocv_sample_time_s,
               tuple(sorted(model.ocv_overrides.items())), tuple(sorted(model.ocv_samples.items())),
               tuple(sorted(model.ocv_fit_overrides.items())))
        if model.rest_key != key:
            try:
                model.rest_ocv = tuple(self.backend.gitt_ici_rest_ocv(model.scope_id, p, dict(model.ocv_overrides),
                                                                      dict(model.ocv_samples),
                                                                      dict(model.ocv_fit_overrides)))
            except Exception as error:                            # reported, never replaced by invented values
                model.rest_ocv = ()
                model.notice = f"OCV unavailable: {error}"
            model.rest_key = key
        return model.rest_ocv

    def _rest_events(self, model, rest_id: int) -> tuple:
        return tuple(e.event_id for e in model.events if rest_id in (e.rest_before_segment_id, e.rest_after_segment_id))

    def set_ocv_time(self, ds_id: str, rest_id: int, seconds: Optional[float]) -> None:
        """Sample time [s] of the sampled OCV of one rest only (None: back to the global rule): gregorithm takes the
        last real rest sample with t ≤ T from the relaxation origin; see ``set_ocv_sample``."""
        model = self.model(ds_id)
        if seconds is not None and not seconds > 0:
            if model is not None:
                self._say(ds_id, model, "The sampled OCV time must be greater than 0 s.")
            return
        self.set_ocv_sample(ds_id, rest_id, None if seconds is None else ("time", float(seconds)))

    def set_ocv_sample(self, ds_id: str, rest_id: int, row) -> None:
        """Use the real sample ``row``, a local time ``('time', T)`` or (None) the global rule as the sampled OCV of
        one rest. A row outside the rest or a time without samples is refused. Results using that rest's sampled OCV
        become stale and only they are recalculated."""
        model = self.model(ds_id)
        if model is None or not model.scope_id or self._defer(ds_id, model, lambda: self.set_ocv_sample(
                ds_id, rest_id, row)):
            return
        samples = dict(model.ocv_samples)
        if row is None:
            samples.pop(rest_id, None)
        else:
            samples[rest_id] = row if isinstance(row, tuple) else int(row)
        if samples == model.ocv_samples:
            return
        try:
            measured = self.backend.gitt_ici_rest_ocv(model.scope_id, model.applied, dict(model.ocv_overrides),
                                                      samples, dict(model.ocv_fit_overrides))
        except Exception as error:
            self._say(ds_id, model, f"OCV unavailable: {error}")
            return
        rest = next((r for r in measured if r.rest_segment_id == rest_id), None)
        if row is not None and (rest is None or rest.sampled is None):
            self._say(ds_id, model, "No rest sample within this time." if isinstance(row, tuple) else
                      "The chosen sample does not belong to this rest period.")
            return
        affected = "rest_sample" in model.applied.ocv_methods and any(
            r.identity.event_id in self._rest_events(model, rest_id) for r in model.records)
        if not affected:
            model.ocv_samples = samples
            self._sync(ds_id)
            self._say(ds_id, model, "Sampled OCV changed; no calculated result depends on it.")
            return
        self._start(ds_id, RUN_LOCAL, apply_draft=False, scan=False, compute=True, event_id=None, series=None,
                    automatic=True, set_ocv_sample=samples, event_ids=self._rest_events(model, rest_id),
                    only_stale=True)

    def set_rest_target(self, ds_id: str, rest_id: int, target_s: Optional[float]) -> None:
        """Override (or reset with None) the extrapolation target of one rest; the fit start of that rest is kept."""
        model = self.model(ds_id)
        if model is not None:
            self.set_rest_extrapolation(ds_id, rest_id, target_s, model.ocv_fit_overrides.get(rest_id))

    def set_rest_extrapolation(self, ds_id: str, rest_id: int, target_s: Optional[float],
                               fit_start: Optional[float]) -> None:
        """Extrapolation settings of one rest only: target [s] and fit start fraction, each None for the global
        value. Only results using that rest's extrapolated OCV are recalculated; the other rests are unchanged."""
        model = self.model(ds_id)
        if model is None or not model.scope_id or self._defer(ds_id, model, lambda: self.set_rest_extrapolation(
                ds_id, rest_id, target_s, fit_start)):
            return
        if target_s is not None and not target_s > 0:
            self._say(ds_id, model, "The OCV target must be greater than 0 s.")
            return
        if fit_start is not None and not 0 <= fit_start < 1:
            self._say(ds_id, model, "The fit start must be at least 0 and below 1.")
            return
        if (model.ocv_overrides.get(rest_id), model.ocv_fit_overrides.get(rest_id)) == (target_s, fit_start):
            return
        compute = "relaxation_extrapolated" in model.applied.ocv_methods and bool(model.records)
        self._start(ds_id, RUN_LOCAL, apply_draft=False, scan=False, compute=compute, event_id=None, series=None,
                    automatic=True, set_target=(rest_id, target_s), set_fit_start=(rest_id, fit_start),
                    event_ids=self._rest_events(model, rest_id), only_stale=True)

    def set_ocv_settings(self, ds_id: str, *, extrapolation: bool, target_s: float, fit_start: float,
                         sample_time: Optional[float] = ...) -> None:
        """Global OCV settings of OCV Analysis: sampled-OCV time (None: last rest sample; omitted: unchanged),
        extrapolation on/off for every rest, global target and fit start fraction. Per-rest choices are kept. Only
        results whose dependencies changed are recalculated."""
        model = self.model(ds_id)
        if model is None or self._defer(ds_id, model, lambda: self.set_ocv_settings(
                ds_id, extrapolation=extrapolation, target_s=target_s, fit_start=fit_start, sample_time=sample_time)):
            return
        if sample_time is ...:
            sample_time = model.applied.ocv_sample_time_s
        new = replace(model.applied, extrapolation=extrapolation, ocv_target_s=target_s,
                      ocv_fit_start_fraction=fit_start, ocv_sample_time_s=sample_time)
        errors = validate(new)
        if errors:
            self._say(ds_id, model, " ".join(errors))
            return
        model.draft = replace(model.draft, extrapolation=extrapolation, ocv_target_s=target_s,
                              ocv_fit_start_fraction=fit_start, ocv_sample_time_s=sample_time)
        if new == model.applied:
            return
        self._start(ds_id, RUN_GLOBAL, apply_draft=False, scan=False, compute=bool(model.records), event_id=None,
                    series=None, automatic=True, new_applied=new, only_stale=True)

    def set_ir_times(self, ds_id: str, pre_s: float, post_s: Optional[float]) -> None:
        """Sample times of the IR drops of every impulse: pre-pulse (also the selected-sample IR correction of
        Delacourt and, when chosen, GITT Δ) and post-pulse (None: same as pre-pulse). The IR drops are re-measured;
        only results depending on the pre-pulse sample are recalculated, the post-pulse time feeds no D."""
        model = self.model(ds_id)
        if model is None or self._defer(ds_id, model, lambda: self.set_ir_times(ds_id, pre_s, post_s)):
            return
        new = replace(model.applied, ir_sample_time_s=pre_s, ir_post_sample_time_s=post_s)
        errors = validate(new)
        if errors:
            self._say(ds_id, model, " ".join(errors))
            return
        model.draft = replace(model.draft, ir_sample_time_s=pre_s, ir_post_sample_time_s=post_s)
        if new == model.applied:
            return
        if new.ir_sample_time_s == model.applied.ir_sample_time_s or not model.records:
            model.applied = new                                       # nothing calculated depends on it
            self._sync(ds_id)
            self._say(ds_id, model, "IR drops re-measured with the new sample times.")
            return
        self._start(ds_id, RUN_GLOBAL, apply_draft=False, scan=False, compute=True, event_id=None, series=None,
                    automatic=True, new_applied=new, only_stale=True)

    # --- intermediate acceptances ------------------------------------------------------------------------------------
    def rest_states(self, ds_id: str) -> dict:
        """{rest_segment_id: Pending | Accepted | Modified | Invalid}."""
        model = self.model(ds_id)
        if model is None:
            return {}
        return {r.rest_segment_id: rest_state(r, model.applied, model.rest_reviews) for r in self.rest_ocv(ds_id)}

    def pulse_states(self, ds_id: str) -> dict:
        """{event_id: No fit needed | Pending | Accepted | Modified | Invalid}."""
        model = self.model(ds_id)
        if model is None:
            return {}
        return {e.event_id: pulse_state(e, model.records, model.applied, model.pulse_reviews) for e in model.events}

    def ir_states(self, ds_id: str) -> dict:
        """{event_id: Pending | Accepted | Modified | Invalid | Rejected} of the IR drops of every impulse."""
        model = self.model(ds_id)
        if model is None:
            return {}
        drops = self.ir_drops(ds_id)
        return {e.event_id: ir_state(drops.get(e.event_id), model.ir_reviews, e.event_id) for e in model.events}

    def accepted_ir(self, ds_id: str) -> set:
        """Event ids whose IR drops are accepted (and unchanged since)."""
        model = self.model(ds_id)
        return set() if model is None else accepted_ir(model.events, self.ir_drops(ds_id), model.ir_reviews)

    def accept_ir(self, ds_id: str, event_ids=None) -> None:
        """Accept the IR drops of one, several or (None) every valid impulse with exactly the values shown now;
        accepting all skips rejected impulses."""
        model = self.model(ds_id)
        if model is None or model.is_running:
            return
        states = self.ir_states(ds_id)
        allowed = (PENDING, MODIFIED, ACCEPTED) + (() if event_ids is None else (REJECTED,))
        wanted = [e for e in (states if event_ids is None else event_ids) if states.get(e) in allowed]
        drops = self.ir_drops(ds_id)
        model.ir_reviews = {**model.ir_reviews, **{e: ir_fingerprint(drops[e]) for e in wanted}}
        skipped = len(states) - len(wanted) if event_ids is None else 0
        self._say(ds_id, model, f"{len(wanted)} impulse IR drop(s) accepted"
                  + (f" · {skipped} invalid or rejected not accepted" if skipped else ""))

    def revoke_ir(self, ds_id: str, event_id: str) -> None:
        """Return the IR drops of one impulse to Pending; its R_on / R_off leave the results and the export."""
        model = self.model(ds_id)
        if model is not None and not model.is_running and event_id in model.ir_reviews:
            model.ir_reviews = {k: v for k, v in model.ir_reviews.items() if k != event_id}
            self._say(ds_id, model, "IR drops returned to Pending")

    def reject_ir(self, ds_id: str, event_id: str) -> None:
        """Reject the IR drops of one impulse by hand: its R_on / R_off are not used until accepted again."""
        model = self.model(ds_id)
        if model is not None and not model.is_running:
            model.ir_reviews = {**model.ir_reviews, event_id: REJECT_MARK}
            self._say(ds_id, model, "IR drops rejected: R_on / R_off of this impulse are not used")

    def accept_rests(self, ds_id: str, rest_ids=None) -> None:
        """Accept one, several or (None) every valid rest period with exactly the OCV values shown now."""
        model = self.model(ds_id)
        if model is None or model.is_running:
            return
        states = self.rest_states(ds_id)
        allowed = (PENDING, MODIFIED, ACCEPTED) + (() if rest_ids is None else (REJECTED,))   # all: skip rejected
        wanted = [r for r in (states if rest_ids is None else rest_ids) if states.get(r) in allowed]
        reviews = dict(model.rest_reviews)
        for rest in model.rest_ocv:
            if rest.rest_segment_id in wanted:
                reviews[rest.rest_segment_id] = rest_fingerprint(rest, model.applied)
        model.rest_reviews = reviews
        self._sync(ds_id)
        skipped = len(states) - len(wanted) if rest_ids is None else 0
        self._say(ds_id, model, f"{len(wanted)} rest period(s) accepted" + (f" · {skipped} invalid not accepted"
                                                                             if skipped else ""))

    def revoke_rest(self, ds_id: str, rest_id: int) -> None:
        """Return one rest period to Pending; D results using its OCV leave the final results."""
        model = self.model(ds_id)
        if model is not None and not model.is_running and rest_id in model.rest_reviews:
            model.rest_reviews = {k: v for k, v in model.rest_reviews.items() if k != rest_id}
            self._sync(ds_id)
            self._say(ds_id, model, "Rest period returned to Pending")

    def reject_rest(self, ds_id: str, rest_id: int) -> None:
        """Reject one rest period by hand: its OCV feeds no D until it is returned to Pending or accepted again."""
        model = self.model(ds_id)
        if model is not None and not model.is_running:
            model.rest_reviews = {**model.rest_reviews, rest_id: REJECT_MARK}
            self._sync(ds_id)
            self._say(ds_id, model, "Rest period rejected: the D using its OCV are excluded")

    def accept_pulses(self, ds_id: str, event_ids=None) -> None:
        """Accept the fits of one, several or (None) every valid impulse exactly as calculated now."""
        model = self.model(ds_id)
        if model is None or model.is_running:
            return
        states = self.pulse_states(ds_id)
        allowed = (PENDING, MODIFIED, ACCEPTED) + (() if event_ids is None else (REJECTED,))  # all: skip rejected
        wanted = [e for e in (states if event_ids is None else event_ids) if states.get(e) in allowed]
        reviews = dict(model.pulse_reviews)
        for event_id in wanted:
            reviews[event_id] = pulse_fingerprint(model.records, event_id, model.applied)
        model.pulse_reviews = reviews
        self._sync(ds_id)
        self._say(ds_id, model, f"{len(wanted)} impulse fit(s) accepted")

    def revoke_pulse(self, ds_id: str, event_id: str) -> None:
        """Return one impulse to Pending so it can be re-examined; its fitted D leave the final results."""
        model = self.model(ds_id)
        if model is not None and not model.is_running and event_id in model.pulse_reviews:
            model.pulse_reviews = {k: v for k, v in model.pulse_reviews.items() if k != event_id}
            self._sync(ds_id)
            self._say(ds_id, model, "Impulse fit returned to Pending")

    def reject_pulse(self, ds_id: str, event_id: str) -> None:
        """Reject one impulse by hand: none of its D is used until it is returned to Pending or accepted again."""
        model = self.model(ds_id)
        if model is not None and not model.is_running:
            model.pulse_reviews = {**model.pulse_reviews, event_id: REJECT_MARK}
            self._sync(ds_id)
            self._say(ds_id, model, "Impulse rejected: none of its D is used")

    def _sync(self, ds_id: str) -> None:
        """Make gregorithm's acceptance of every D equal to the intermediate acceptances (see gitt_ici_review)."""
        model = self.model(ds_id)
        if model is None or model.snapshot is None:
            return
        self.rest_ocv(ds_id)
        rest_ok, pulse_ok = accepted_sets(model, model.applied)
        model.snapshot = sync_acceptance(model.snapshot, model.events, model.applied, rest_ok, pulse_ok,
                                         rejected_pulses(model))

    # --- jobs ------------------------------------------------------------------------------------------------------
    def apply(self, ds_id: str) -> None:
        """Apply the draft: recognise impulses if protocol or scope changed, invalidate dependent results and
        recalculate when results already exist or the automatic analysis is enabled. Manual windows are kept."""
        model = self.model(ds_id)
        if model is None or model.is_running:
            return
        if not self._check(ds_id, model, model.draft):
            return
        scan = model.needs_scan(model.draft)
        compute = model.draft.auto_analysis or (bool(model.records) and not scan)
        windows_changed = model.draft.window_key() != model.applied.window_key()
        model.proceeding = True
        self._start(ds_id, RUN_GLOBAL if compute else RUN_RECOGNIZE, apply_draft=True, scan=scan, compute=compute,
                    event_id=None, series=None, automatic=True, only_stale=not scan and not windows_changed)

    def calculate_all(self, ds_id: str) -> None:
        """Global automatic recalculation of every impulse in scope; applies the draft and drops manual windows
        and manual IR samples."""
        model = self.model(ds_id)
        if model is None or model.is_running or not self._check(ds_id, model, model.draft):
            return
        self._start(ds_id, RUN_GLOBAL, apply_draft=True, scan=model.needs_scan(model.draft), compute=True,
                    event_id=None, series=None, automatic=True, clear_windows=True)

    def reapply_automatic(self, ds_id: str, event_id: str) -> None:
        """Re-apply the automatic windows to one impulse for ALL checked methods with the applied
        parameters; the impulse's manual windows and manual IR samples are dropped."""
        model = self.model(ds_id)
        if model is None or not model.scope_id or model.event(event_id) is None:
            return
        if self._defer(ds_id, model, lambda: self.reapply_automatic(ds_id, event_id)):
            return
        self._start(ds_id, RUN_LOCAL, apply_draft=False, scan=False, compute=True, event_id=event_id,
                    series=None, automatic=True, clear_event=event_id)

    def apply_window(self, ds_id: str, event_id: str, role: str, start_s: float, end_s: float) -> None:
        """Apply a manual √t window [s] of one impulse/role on release or Apply and recalculate, for that
        impulse only, the checked series that use this role plus the series under review."""
        model = self.model(ds_id)
        if model is None or not model.scope_id:
            return
        if self._defer(ds_id, model, lambda: self.apply_window(ds_id, event_id, role, start_s, end_s)):
            return
        if not (0 <= start_s < end_s):
            self._say(ds_id, model, "The window end must be after its start.")
            return
        series = [s for s in selected_series(model.applied) if WINDOW_ROLE.get(s[0]) == role]
        if not any(r.identity.event_id == event_id for r in model.records):
            series = list(selected_series(model.applied))     # first calculation of this impulse: every series
        if model.selected is not None and model.selected.event_id == event_id:
            own = (series_key_of(model.selected), model.selected.ocv_source.value)
            if WINDOW_ROLE.get(own[0]) == role and own not in series:
                series.append(own)
        if not series:
            self._say(ds_id, model, "No checked method uses this window.")
            return
        self._start(ds_id, RUN_LOCAL, apply_draft=False, scan=False, compute=True, event_id=event_id,
                    series=tuple(series), automatic=True, set_window=((event_id, role), (start_s, end_s)))

    def apply_window_all(self, ds_id: str, role: str, start_s: float, end_s: float) -> None:
        """Apply the same manual √t window [s] of one role to every complete impulse and recalculate, in one global
        job, the checked series using that role (every checked series when some impulse is not calculated yet).
        Each impulse keeps its own window afterwards, so it can still be edited impulse by impulse."""
        model = self.model(ds_id)
        if model is None or not model.scope_id:
            return
        if self._defer(ds_id, model, lambda: self.apply_window_all(ds_id, role, start_s, end_s)):
            return
        if not (0 <= start_s < end_s):
            self._say(ds_id, model, "The window end must be after its start.")
            return
        events = [e.event_id for e in model.events if e.complete]
        series = [s for s in selected_series(model.applied) if WINDOW_ROLE.get(s[0]) == role]
        calculated = {r.identity.event_id for r in model.records}
        if any(e not in calculated for e in events):
            series = list(selected_series(model.applied))
        if not events or not series:
            self._say(ds_id, model, "No complete impulse or no checked method uses this window.")
            return
        self._start(ds_id, RUN_GLOBAL, apply_draft=False, scan=False, compute=True, event_id=None,
                    series=tuple(series), automatic=True, event_ids=tuple(events),
                    set_windows={(e, role): (start_s, end_s) for e in events})

    def cancel(self, ds_id: str) -> None:
        """Request cancellation; the job stops at its next checkpoint and nothing is published."""
        model = self.model(ds_id)
        job = self._jobs.get(ds_id)
        if model is not None and model.is_running and job is not None and job[0] == model.run_id:
            model.cancel_requested = True
            job[1].cancel()
            self.changed.emit(ds_id)

    def _check(self, ds_id: str, model: GittIciState, params: GittIciParams) -> bool:
        if not hasattr(self.backend, "scan_gitt_ici"):
            self._say(ds_id, model, "GITT / ICI needs the gregorithm backend; it is not available in demo mode.")
            return False
        if self._elab_id(ds_id) is None:
            self._say(ds_id, model, "Run Data Elaboration first.")
            return False
        errors = validate(params)
        if errors:
            model.error = " ".join(errors)
            self.changed.emit(ds_id)
            return False
        return True

    def _start(self, ds_id: str, kind: str, *, apply_draft: bool, scan: bool, compute: bool, event_id, series,
               automatic: bool, clear_windows: bool = False, clear_event: str = "", set_window=None,
               set_windows: Optional[dict] = None, set_target=None, set_ir=None, set_ocv_sample=None,
               new_applied=None, event_ids=None, only_stale: bool = False, set_fit_start=None) -> None:
        model = self.model(ds_id)
        elab_id = self._elab_id(ds_id)
        run_id = model.begin(kind, apply_draft)
        if new_applied is not None:
            model.applied = new_applied
        invalidate = apply_draft
        if clear_windows:
            invalidate = invalidate or bool(model.ir_samples)
            model.windows, model.ir_samples = {}, {}
        if clear_event:
            model.windows = {k: v for k, v in model.windows.items() if k[0] != clear_event}
            if any(k[0] == clear_event for k in model.ir_samples):
                model.ir_samples = {k: v for k, v in model.ir_samples.items() if k[0] != clear_event}
                invalidate = True
        if set_ir is not None:
            model.ir_samples, invalidate = dict(set_ir), True
        if set_ocv_sample is not None:
            model.ocv_samples, invalidate = dict(set_ocv_sample), True
        if new_applied is not None:
            invalidate = True
        if set_window is not None:
            model.windows = {**model.windows, set_window[0]: set_window[1]}
        if set_windows is not None:
            model.windows = {**model.windows, **set_windows}
        if set_target is not None:
            rest_id, target = set_target
            overrides = dict(model.ocv_overrides)
            if target is None:
                overrides.pop(rest_id, None)
            else:
                overrides[rest_id] = target
            model.ocv_overrides, invalidate = overrides, True
        if set_fit_start is not None:
            rest_id, fraction = set_fit_start
            fits = dict(model.ocv_fit_overrides)
            if fraction is None:
                fits.pop(rest_id, None)
            else:
                fits[rest_id] = fraction
            model.ocv_fit_overrides, invalidate = fits, True
        params, windows, overrides = model.applied, dict(model.windows), dict(model.ocv_overrides)
        ir_samples, ocv_samples = dict(model.ir_samples), dict(model.ocv_samples)
        fit_overrides = dict(model.ocv_fit_overrides)
        series = selected_series(params) if series is None else series
        previous = None if scan else model.snapshot
        scope_id, backend = model.scope_id, self.backend
        token = gregorithm.CancellationToken()
        self._jobs[ds_id] = (run_id, token, model.snapshot)

        def work(progress, cancel):
            scanned = None
            current_scope = scope_id
            if scan:
                progress(_phase("Recognising impulses"))
                scanned = backend.scan_gitt_ici(ds_id, elab_id, params)
                current_scope = scanned.scope_id
            try:
                gregorithm.check_cancelled(cancel, "gitt_ici")            # recognition has no inner checkpoint
                if not compute:
                    return scanned, None
                calc = backend.analyze_gitt_ici(
                    current_scope, params, previous, event_id=event_id, series=series, windows=windows,
                    ocv_overrides=overrides, automatic=automatic, invalidate=invalidate and previous is not None,
                    progress=progress, cancel=cancel, ir_samples=ir_samples, ocv_samples=ocv_samples,
                    event_ids=event_ids, only_stale=only_stale and previous is not None,
                    ocv_fit_overrides=fit_overrides)
            except BaseException:
                if scanned is not None:
                    backend.release_gitt_ici(scanned.scope_id)
                raise
            return scanned, calc

        self.changed.emit(ds_id)
        QThreadPool.globalInstance().start(_Job(ds_id, run_id, work, token, self._sig))

    # --- job results -----------------------------------------------------------------------------------------------
    def _current(self, ds_id: str, run_id: int) -> Optional[GittIciState]:
        model = self.model(ds_id)
        return model if model is not None and model.is_running and model.run_id == run_id else None

    def _end_job(self, ds_id: str, run_id: int) -> object:
        """Forget the bookkeeping of ``run_id`` only; a newer job of the same dataset keeps its token."""
        job = self._jobs.get(ds_id)
        if job is None or job[0] != run_id:
            return None
        del self._jobs[ds_id]
        return job[2]

    def _progress(self, ds_id: str, run_id: int, progress) -> None:
        if (model := self._current(ds_id, run_id)) is not None:
            model.progress, model.phase = progress.fraction, progress.phase
            model.done, model.total = progress.done or 0, progress.total or 0
            self.changed.emit(ds_id)

    def _finished(self, ds_id: str, run_id: int, value) -> None:
        scanned, calc = value
        model = self._current(ds_id, run_id)
        base_at_start = self._end_job(ds_id, run_id)
        if model is None or (calc is not None and scanned is None and model.snapshot is not base_at_start):
            if scanned is not None:                                       # late result: discard and free memory
                self.backend.release_gitt_ici(scanned.scope_id)
            if model is not None:
                model.cancel()
                self._say(ds_id, model, "A late result was discarded; the current results are unchanged.")
            return
        if scanned is not None:
            if model.scope_id:
                self.backend.release_gitt_ici(model.scope_id)
            model.reset_results()
            model.scope_id, model.events, model.scanned = scanned.scope_id, scanned.events, model.applied.scan_key()
            model.branch_end_ah = dict(scanned.branch_end_ah)
        message = f"{len(model.events)} impulses recognised" if scanned is not None else ""
        if calc is not None:
            base, outcome, skipped = calc
            model.snapshot = gregorithm.commit_batch(base, outcome)
            model.skipped = skipped
            message = (f"{outcome.completed} results calculated · {outcome.recoverable_errors} technical errors"
                       + (f" · {len(skipped)} incomplete impulses not analysed" if skipped else ""))
        model.finish()
        self._sync(ds_id)
        if getattr(model, "proceeding", False):
            model.proceeding = False
            if model.events and model.step == "setup":
                model.step = "ir"
            elif not model.events:
                message = "No impulse of the chosen protocol in the selected scope: nothing to analyse."
        self._say(ds_id, model, message or "Complete")
        self._run_queued(ds_id)

    def _failed(self, ds_id: str, run_id: int, message: str) -> None:
        self._end_job(ds_id, run_id)
        if (model := self._current(ds_id, run_id)) is not None:
            model.fail(message)
            model.proceeding = False
            self.changed.emit(ds_id)
            self._run_queued(ds_id)

    def _cancelled(self, ds_id: str, run_id: int) -> None:
        self._end_job(ds_id, run_id)
        if (model := self._current(ds_id, run_id)) is not None:
            model.cancel()
            model.proceeding = False
            self._say(ds_id, model, "Cancelled · previous results unchanged")
            self._run_queued(ds_id)

    # --- export -------------------------------------------------------------------------------
    def export_table(self, ds_id: str) -> Optional[ExportTable]:
        """The export rows of the current results (app.gitt_ici_export): the values drawn in Diffusion Results, with
        D only for the results in use. None when nothing is recognised."""
        model = self.model(ds_id)
        if model is None or not model.events:
            return None
        params = model.applied
        rest_ok, pulse_ok = accepted_sets(model, params)
        rejected = rejected_pulses(model)
        by_id = {e.event_id: e for e in model.events}
        used = [r for r in model.records if r.identity.event_id in by_id
                and usage(r, by_id[r.identity.event_id], params, rest_ok, pulse_ok, rejected).used]
        ds = self.state.get(ds_id)
        mass_mg = None if ds is None else ds.elab.sample.active_mass_mg
        return build_table(model.events, used, model.records, self.ir_drops(ds_id), self.rest_ocv(ds_id),
                           unit=model.d_unit, branch_end_ah=model.branch_end_ah,
                           mass_g=mass_mg / 1000.0 if mass_mg else None, ir_ok=self.accepted_ir(ds_id))

    def can_export(self, ds_id: str) -> bool:
        """True when the current results have at least one accepted D to export and nothing is running."""
        model = self.model(ds_id)
        if model is None or model.is_running or model.exporting or model.snapshot is None:
            return False
        table = self.export_table(ds_id)
        return table is not None and any(k.startswith("d:") for k, _t in table.columns)

    def export_files(self, ds_id: str, path: str, columns: Optional[list[str]] = None,
                     split: bool = True) -> Optional[list]:
        """The files an export writes, as (path, column titles, rows): one per branch when ``split`` (named
        "<name> - charge", "<name> - discharge"), else one with a Branch column. None when no D is in use."""
        table = self.export_table(ds_id)
        if table is None or not any(k.startswith("d:") for k, _t in table.columns):
            return None
        chosen = [(k, t) for k, t in table.columns if columns is None or k in columns or k == "impulse"]
        target = Path(path)
        files = []
        if split:
            for branch in BRANCH_ORDER:
                rows = table.rows.get(branch)
                if rows:
                    files.append((str(target.with_name(f"{target.stem} - {branch}{target.suffix}")),
                                  [t for _k, t in chosen], [[row.get(k) for k, _t in chosen] for row in rows]))
        else:
            rows = [(branch, row) for branch in BRANCH_ORDER for row in table.rows.get(branch, [])]
            files.append((str(target), ["Branch"] + [t for _k, t in chosen],
                          [[branch.capitalize()] + [row.get(k) for k, _t in chosen] for branch, row in rows]))
        return files

    def export_inputs(self, ds_id: str) -> Optional[tuple]:
        """(elaboration result id, snapshot, applied parameters) the backend export needs, or None."""
        model = self.model(ds_id)
        if model is None or model.snapshot is None:
            return None
        return self._elab_id(ds_id), model.snapshot, model.applied

    def export(self, ds_id: str, path: str, fmt: str, columns: Optional[list[str]] = None,
               split: bool = True) -> None:
        """Export one row per impulse with the chosen ``columns`` (keys of export_table; None = all). ``split`` writes
        one file per branch ("<name> - charge", "<name> - discharge"); otherwise one file with a Branch column.
        With no D in use nothing is written; an existing file is never overwritten."""
        model = self.model(ds_id)
        if model is None or model.is_running or model.exporting or model.snapshot is None:
            return
        files = self.export_files(ds_id, path, columns, split)
        if files is None:
            self._say(ds_id, model, "No accepted current D: nothing was exported and no file was created.")
            return
        elab_id = self._elab_id(ds_id)
        snapshot, params, backend, sig = model.snapshot, model.applied, self.backend, self._sig
        model.exporting = True
        self.changed.emit(ds_id)
        token = self._export_tokens[ds_id] = gregorithm.CancellationToken()
        QThreadPool.globalInstance().start(_Call(
            lambda: backend.export_gitt_ici(ds_id, elab_id, snapshot, params, files, fmt, token),
            lambda done: sig.exported.emit(ds_id, done),
            lambda error: sig.exportFailed.emit(ds_id, _export_message(error))))

    def _exported(self, ds_id: str, receipts) -> None:
        model = self.model(ds_id)
        if model is not None:
            model.exporting = False
            rows = sum(r.rows for r in receipts)
            text = f"Exported {rows} impulses to " + " and ".join(r.path for r in receipts)
            self._say(ds_id, model, text)
            self.exportFinished.emit(ds_id, text)

    def _export_failed(self, ds_id: str, message: str) -> None:
        model = self.model(ds_id)
        if model is not None:
            model.exporting = False
            self._say(ds_id, model, f"Export failed: {message}")
            self.exportError.emit(ds_id, message)

    # --- lifecycle -------------------------------------------------------------------------------------------------
    def elaboration_replaced(self, ds_id: str) -> None:
        """A new elaboration makes events and results of the old generation obsolete: cancel and reset."""
        model = self.model(ds_id)
        if model is None:
            return
        job = self._jobs.pop(ds_id, None)
        if job is not None:
            job[1].cancel()
        if model.scope_id:
            self.backend.release_gitt_ici(model.scope_id)
        had_results = bool(model.records)
        model.run_id += 1                                     # any job still in flight becomes a late result
        model.run, model.cancel_requested = "", False
        model.reset_results()
        if had_results:
            self._say(ds_id, model, "Elaboration replaced: GITT/ICI results were cleared; apply the settings again.")
        else:
            self.changed.emit(ds_id)

    def cancel_all(self) -> None:
        """Application closing: stop every running calculation and export, without updating the views."""
        for job in list(self._jobs.values()):
            job[1].cancel()
        for token in list(self._export_tokens.values()):
            token.cancel()

    def dataset_closed(self, ds_id: str) -> None:
        """Cancel a running job and free the samples held for the closed dataset."""
        job = self._jobs.pop(ds_id, None)
        if job is not None:
            job[1].cancel()
        model = self.model(ds_id)
        if model is not None and model.scope_id:
            self.backend.release_gitt_ici(model.scope_id)


def _export_message(error: Exception) -> str:
    """Operator-facing export failure text; a failed export is never reported as successful."""
    if isinstance(error, gregorithm.NoAcceptedResultsError):
        return "no accepted current D; no file was created."
    return str(error) or type(error).__name__


def _phase(text: str) -> Progress:
    """Indeterminate progress for a step without a known total (recognition)."""
    return Progress(None, text, None, None, False)
