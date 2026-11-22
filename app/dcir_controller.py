"""Qt glue for the DCIR section. Decisions live in app/dcir.py (pure, tested). This class runs detection,
previews, manual creation, publish and export in QThreadPool workers; every response carries the request it answers
and is discarded when it is stale (other detection, other measurement time, other units, other event)."""
from __future__ import annotations
import threading
import gregorithm
from typing import Optional
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from .backend import Backend, DcirCancelled, ExportCancelled, Progress, PulseTrace
from .dcir import (ACCEPTED, AUTO, REJECTED, DcirState, DetectionResult, PublishedResult, explain)
from .elab_controller import ElabController
from .state import SessionState
from .units_controller import UnitsController


class _Sig(QObject):
    progress = Signal(str, object)
    detected = Signal(str, object, object)     # ds_id, request key, DetectionResult
    published = Signal(str, object, object)
    cancelled = Signal(str)
    failed = Signal(str, str)
    previews = Signal(str, object, object)     # ds_id, key, list[MeasurementView]
    trace = Signal(str, object, object)        # ds_id, key, PulseTrace
    traceFailed = Signal(str, object, str)
    manual = Signal(str, object, object)       # ds_id, dcir id, (EventView, MeasurementView, values)
    manualFailed = Signal(str, str)
    refreshed = Signal(str, object, object)    # ds_id, key, dict (events / published / manual measurements)
    exportProgress = Signal(str, object)
    exported = Signal(str, object)
    exportFailed = Signal(str, str)


class _Run(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        self.fn()


def _err(ex: Exception) -> str:
    msg = str(ex) or type(ex).__name__
    return explain(msg) if msg.isupper() and "_" in msg else msg      # a bare diagnostic code gets its sentence


class DcirController(QObject):
    changed = Signal(str)
    traceReady = Signal(str)
    manualError = Signal(str, str)
    notice = Signal(str, str)                  # ds_id, status-bar text
    exportState = Signal(str, str)
    exportFinished = Signal(str, str)
    exportError = Signal(str, str)

    def __init__(self, state: SessionState, backend: Backend, elab: ElabController, units: UnitsController):
        super().__init__()
        self.state, self.backend, self.elab, self.units = state, backend, elab, units
        self.traces: dict[str, PulseTrace] = {}
        self._trace_key: dict[str, tuple] = {}
        self._cycle_spans: dict[str, list] = {}
        self._cancel: dict[str, gregorithm.CancellationToken] = {}
        self._export_cancel: dict[str, threading.Event] = {}
        self._measuring: dict[str, tuple] = {}
        self._sig = s = _Sig()
        s.progress.connect(self._on_progress)
        s.detected.connect(self._on_detected)
        s.published.connect(self._on_published)
        s.cancelled.connect(self._on_cancelled)
        s.failed.connect(self._on_failed)
        s.previews.connect(self._on_previews)
        s.trace.connect(self._on_trace)
        s.traceFailed.connect(self._on_trace_failed)
        s.manual.connect(self._on_manual)
        s.manualFailed.connect(self.manualError)
        s.refreshed.connect(self._on_refreshed)
        s.exportProgress.connect(lambda ds, p: self.exportState.emit(ds, f"Exporting… {p.done or 0:,} of {p.total or 0:,} rows"))
        s.exported.connect(self._on_exported)
        s.exportFailed.connect(self._on_export_failed)
        elab.resultReplaced.connect(self.elaboration_replaced)
        units.changed.connect(self.units_changed)

    # ---------------------------------------------------------------- access
    def dc(self, ds_id: Optional[str]) -> Optional[DcirState]:
        d = self.state.get(ds_id) if ds_id else None
        return d.dcir if d else None

    def elab_result_id(self, ds_id: str) -> Optional[str]:
        d = self.state.get(ds_id)
        return d.elab.result.result_id if d and d.elab.result else None

    def dcir_id(self, ds_id: str) -> Optional[str]:
        c = self.dc(ds_id)
        if c is None:
            return None
        return c.detection.result_id if c.detection else (c.published.result_id if c.published else None)

    def edit(self, ds_id: str, fn) -> None:
        c = self.dc(ds_id)
        if c is not None:
            fn(c)
            self.changed.emit(ds_id)

    def _start(self, fn) -> None:
        QThreadPool.globalInstance().start(_Run(fn))

    # ---------------------------------------------------------------- detection
    def detect(self, ds_id: str) -> None:
        d, c = self.state.get(ds_id), self.dc(ds_id)
        if d is not None and (d.correction.applied is not None or d.correction.removed_at):
            self.notice.emit(ds_id, "Detection is unavailable after Cycle Correction. The corrected elaboration keeps the DCIR results it was built from; process the original data again to start a new detection.")
            return
        if c is None or c.is_running or c.param_errors or d.elab.result is None or d.elab.is_running:
            return
        c.begin("detect")
        ev = gregorithm.CancellationToken()
        self._cancel[ds_id] = ev
        self.changed.emit(ds_id)
        rid, params, units, backend, sig = d.elab.result.result_id, c.params, self.units.units_for(ds_id), self.backend, self._sig

        def job():
            try:
                sig.detected.emit(ds_id, rid, backend.detect_dcir(ds_id, rid, params, units,
                                                                  lambda p: sig.progress.emit(ds_id, p), ev))
            except DcirCancelled:
                sig.cancelled.emit(ds_id)
            except Exception as ex:
                sig.failed.emit(ds_id, _err(ex))

        self._start(job)

    def cancel(self, ds_id: str) -> None:
        c = self.dc(ds_id)
        if c is not None and c.request_cancel():
            self._cancel[ds_id].cancel()
            self.changed.emit(ds_id)

    def _on_progress(self, ds_id: str, p: Progress) -> None:
        if (c := self.dc(ds_id)) is not None:
            c.update_progress(p.fraction, p.phase, p.publishing)
            self.changed.emit(ds_id)

    def _on_detected(self, ds_id: str, elab_rid: str, res: DetectionResult) -> None:
        self._cancel.pop(ds_id, None)
        c = self.dc(ds_id)
        if c is None or elab_rid != self.elab_result_id(ds_id):      # dataset closed / elaboration replaced meanwhile
            self.backend.release_dcir(res.result_id)
            if c is not None:
                c.cancelled()
                self.changed.emit(ds_id)
            return
        old = self.dcir_id(ds_id)
        c.detected(res)
        if old and old != res.result_id:
            self.backend.release_dcir(old)
        self.traces.pop(ds_id, None)
        n = len(res.events)
        self.notice.emit(ds_id, f"Detection complete: {n:,} pulse{'s' if n != 1 else ''} found." if n
                         else "Detection complete: no pulse matches the detection parameters.")
        self.after_selection(ds_id)

    def _on_cancelled(self, ds_id: str) -> None:
        self._cancel.pop(ds_id, None)
        if (c := self.dc(ds_id)) is not None:
            c.cancelled()
            self.changed.emit(ds_id)

    def _on_failed(self, ds_id: str, msg: str) -> None:
        self._cancel.pop(ds_id, None)
        if (c := self.dc(ds_id)) is not None:
            c.failed(msg)
            self.changed.emit(ds_id)

    # ---------------------------------------------------------------- review
    def select(self, ds_id: str, event_id: Optional[str]) -> None:
        c = self.dc(ds_id)
        if c is None or c.selected == event_id:
            return
        c.selected = event_id
        self.after_selection(ds_id)

    def move(self, ds_id: str, delta: int) -> None:
        c = self.dc(ds_id)
        if c is not None and c.move(delta):
            self.after_selection(ds_id)

    def decide(self, ds_id: str, state: str, advance: bool = True) -> None:
        """Accept / Reject the selected pulse (local until Apply & Calculate). Moves to the next pulse in the list."""
        c = self.dc(ds_id)
        if c is None or c.selected is None or not c.decide(c.selected, state):
            return
        if advance:
            vis = [e.event_id for e in c.visible()]
            if c.selected in vis and vis.index(c.selected) + 1 < len(vis):
                c.selected = vis[vis.index(c.selected) + 1]
            elif vis and c.selected not in vis:
                c.selected = vis[0]
        self.after_selection(ds_id)

    def set_filter(self, ds_id: str, attr: str, value: str) -> None:
        self.edit(ds_id, lambda c: setattr(c, attr, value))

    def after_selection(self, ds_id: str) -> None:
        self.load_trace(ds_id)
        self.measure_needed(ds_id)
        self.changed.emit(ds_id)

    # ---------------------------------------------------------------- inspection trace
    def load_trace(self, ds_id: str) -> None:
        c, did = self.dc(ds_id), self.dcir_id(ds_id)
        e = c.event(c.selected) if c else None
        if c is None or did is None or e is None or e.source != AUTO:
            self.traces.pop(ds_id, None)
            self._trace_key.pop(ds_id, None)
            self.traceReady.emit(ds_id)
            return
        units = self.units.units_for(ds_id)
        t = c.times_for(e.event_id)[0] if e.state != REJECTED else None
        key = (did, e.event_id, t, units)
        if self._trace_key.get(ds_id) == key and ds_id in self.traces:
            return
        self._trace_key[ds_id] = key
        backend, sig = self.backend, self._sig

        def job():
            try:
                sig.trace.emit(ds_id, key, backend.dcir_trace(did, e.event_id, units, t))
            except Exception as ex:
                sig.traceFailed.emit(ds_id, key, _err(ex))

        self._start(job)

    def _on_trace(self, ds_id: str, key, trace: PulseTrace) -> None:
        if self._trace_key.get(ds_id) != key:
            return                                                    # stale
        self.traces[ds_id] = trace
        self.traceReady.emit(ds_id)

    def _on_trace_failed(self, ds_id: str, key, msg: str) -> None:
        if self._trace_key.get(ds_id) == key:
            self.traces.pop(ds_id, None)
            self.notice.emit(ds_id, f"Could not read the pulse: {msg}")
            self.traceReady.emit(ds_id)

    def window(self, ds_id: str, center_s: float, span_s: float = 600.0) -> PulseTrace:
        """Synchronous read for the manual dialog (small window of real samples)."""
        return self.backend.dcir_window(self.elab_result_id(ds_id), center_s, span_s, self.units.units_for(ds_id))

    def cycle_spans(self, ds_id: str) -> list[tuple[int, float, float]]:
        """(cycle, first time [s], last time [s]) of the current elaboration, for the manual dialog's cycle jump.
        Read once per elaboration result; an empty list when it cannot be read (the jump is then simply not offered)."""
        rid = self.elab_result_id(ds_id)
        if rid not in self._cycle_spans:
            try:
                self._cycle_spans[rid] = list(self.backend.cycle_spans(rid))
            except Exception:
                return []
        return self._cycle_spans[rid]

    # ---------------------------------------------------------------- measurement time + previews
    def set_time_text(self, ds_id: str, text: str) -> None:
        c = self.dc(ds_id)
        if c is None:
            return
        if c.set_time_text(text):
            self.after_selection(ds_id)                               # re-measures the pulses already accepted
        else:
            self.changed.emit(ds_id)

    def set_current_time_text(self, ds_id: str, text: str) -> None:
        """Optional I1 time (empty = 3rd pulse sample); a change re-measures the accepted auto pulses."""
        c = self.dc(ds_id)
        if c is None:
            return
        if c.set_current_time_text(text):
            self.after_selection(ds_id)
        else:
            self.changed.emit(ds_id)

    def set_draft_text(self, ds_id: str, which: str, text: str) -> None:
        """Typed V1 / I1 time of the selected pulse (Review strip); applied only by Apply or Apply to All."""
        self.edit(ds_id, lambda c: c.set_draft_text(which, text))

    def apply_times_to_pulse(self, ds_id: str) -> None:
        """Apply: the typed times become the selected pulse's own times; the pulse returns to Pending and is
        measured again at its times (it must be accepted again before Apply & Calculate)."""
        c = self.dc(ds_id)
        if c is None:
            return
        if c.apply_times_to_pulse():
            self.notice.emit(ds_id, f"New times for {c.label(c.event(c.selected))}: review the pulse again and accept it.")
            self.after_selection(ds_id)
        else:
            self.changed.emit(ds_id)

    def apply_times_to_all(self, ds_id: str) -> None:
        """Apply to All: the typed times become the all-pulse times; pulse-specific times are removed and the
        accepted pulses are measured again."""
        c = self.dc(ds_id)
        if c is None:
            return
        if c.apply_times_to_all():
            self.after_selection(ds_id)
        else:
            self.changed.emit(ds_id)

    def measure_needed(self, ds_id: str) -> None:
        c, did = self.dc(ds_id), self.dcir_id(ds_id)
        d = self.state.get(ds_id)
        if d is not None and (d.correction.applied is not None or d.correction.removed_at):
            return
        if c is None or did is None or c.time_error or c.current_time_error:
            return
        ids = [i for i in c.to_measure() if c.event(i).pre_pulse_row is not None]
        if not ids:
            return
        units = self.units.units_for(ds_id)
        key = self._measure_key(ds_id, c)
        if self._measuring.get(ds_id) == (key, tuple(ids)):
            return
        self._measuring[ds_id] = (key, tuple(ids))
        groups: dict = {}                                             # (V1, I1) -> pulses measured at those times
        for event_id in ids:
            groups.setdefault(c.times_for(event_id), []).append(event_id)
        backend, sig = self.backend, self._sig

        def job():
            try:
                out = []
                for (t, ti), group in groups.items():
                    out += backend.measure_dcir(did, group, t, units, threading.Event(), current_time_s=ti)
                sig.previews.emit(ds_id, key, out)
            except Exception as ex:
                sig.manualFailed.emit(ds_id, f"Could not measure the pulses: {_err(ex)}")

        self._start(job)

    def _measure_key(self, ds_id: str, c: DcirState) -> tuple:
        """What a preview depends on: detection, V1 time, I1 time, per-pulse times and units."""
        return (self.dcir_id(ds_id), c.measurement_time_s, c.current_time_s, tuple(sorted(c.event_times.items())),
                self.units.units_for(ds_id))

    def _on_previews(self, ds_id: str, key, ms) -> None:
        self._measuring.pop(ds_id, None)
        c = self.dc(ds_id)
        if c is None or key != self._measure_key(ds_id, c):
            return                                                    # stale: other detection, times or units
        for m in ms:
            c.previews[m.event_id] = m
        self.changed.emit(ds_id)
        self.measure_needed(ds_id)                                    # something accepted meanwhile

    # ---------------------------------------------------------------- manual measurement
    def preview_manual(self, ds_id: str, values: dict):
        """Resistance preview for the dialog, calculated by gregorithm without creating an event.
        Synchronous: four values, no data read. Returns (MeasurementView | None, error sentence)."""
        did = self.dcir_id(ds_id)
        if did is None:
            return None, ""
        try:
            return self.backend.preview_manual_dcir(did, values, self.units.units_for(ds_id)), ""
        except Exception as ex:
            return None, _err(ex)

    def add_manual(self, ds_id: str, values: dict) -> None:
        c, did = self.dc(ds_id), self.dcir_id(ds_id)
        if c is None or did is None or c.is_running:
            return
        units, backend, sig = self.units.units_for(ds_id), self.backend, self._sig

        def job():
            try:
                ev, m = backend.create_manual_dcir(did, values, units)
                sig.manual.emit(ds_id, did, (ev, m, values))
            except Exception as ex:
                sig.manualFailed.emit(ds_id, _err(ex))

        self._start(job)

    def _on_manual(self, ds_id: str, did: str, payload) -> None:
        c = self.dc(ds_id)
        if c is None or did != self.dcir_id(ds_id):
            return
        if len(payload) == 4:
            ev, m, values, old_id = payload
            c.replace_manual(old_id, ev, m, values)
            self.notice.emit(ds_id, "Correction span set: the manual measurement was replaced. Apply & Calculate, then "
                                    "Apply Cycle Correction.")
        else:
            c.add_manual(*payload)
            self.notice.emit(ds_id, "Manual measurement added as Accepted. Apply & Calculate to include it.")
        self.after_selection(ds_id)

    def replace_manual(self, ds_id: str, old_id: str, values: dict) -> None:
        """'Set Span…' from Correction: a new manual measurement with the boundary points replaces the old one."""
        c, did = self.dc(ds_id), self.dcir_id(ds_id)
        if c is None or did is None or c.is_running:
            return
        units, backend, sig = self.units.units_for(ds_id), self.backend, self._sig

        def job():
            try:
                ev, m = backend.create_manual_dcir(did, values, units)
                sig.manual.emit(ds_id, did, (ev, m, values, old_id))
            except Exception as ex:
                sig.manualFailed.emit(ds_id, _err(ex))

        self._start(job)

    def adopt_published(self, ds_id: str, res: PublishedResult) -> None:
        """DCIR events/measurements re-published by Cycle Correction (new cycle labels; same decisions)."""
        c = self.dc(ds_id)
        if c is None:
            return
        c.published = res
        if c.detection is not None:
            from dataclasses import replace
            c.detection = replace(c.detection, events=res.events)
        c.labels = dict(res.labels)
        c.previews.clear()
        self.traces.pop(ds_id, None)
        self._trace_key.pop(ds_id, None)
        self.after_selection(ds_id)

    # ---------------------------------------------------------------- publish
    def publish(self, ds_id: str) -> None:
        c, did = self.dc(ds_id), self.dcir_id(ds_id)
        if c is None or did is None or c.publish_blocker():
            return
        decisions = {e.event_id: e.state for e in c.events()}
        c.begin("publish")
        ev = gregorithm.CancellationToken()
        self._cancel[ds_id] = ev
        self.changed.emit(ds_id)
        t, ti, units, backend, sig = c.measurement_time_s, c.current_time_s, self.units.units_for(ds_id), self.backend, self._sig
        own = {k: v for k, v in c.event_times.items() if decisions.get(k) == ACCEPTED}

        def job():
            try:
                sig.published.emit(ds_id, did, backend.publish_dcir(did, decisions, t, units,
                                                                    lambda p: sig.progress.emit(ds_id, p), ev,
                                                                    current_time_s=ti, event_times=own))
            except DcirCancelled:
                sig.cancelled.emit(ds_id)
            except Exception as ex:
                sig.failed.emit(ds_id, _err(ex))

        self._start(job)

    def _on_published(self, ds_id: str, did: str, res: PublishedResult) -> None:
        self._cancel.pop(ds_id, None)
        c = self.dc(ds_id)
        if c is None or did != self.dcir_id(ds_id):
            return
        c.published_ok(res)
        n = len(res.measurements)
        self.notice.emit(ds_id, f"Calculated {n:,} DCIR measurement{'s' if n != 1 else ''}.")
        self.after_selection(ds_id)

    # ---------------------------------------------------------------- export (Results)
    def export(self, ds_id: str, fmt: str, columns: list[str], path: str) -> None:
        c = self.dc(ds_id)
        if c is None or c.published is None or ds_id in self._export_cancel:
            return
        ev = threading.Event()
        self._export_cancel[ds_id] = ev
        self.exportState.emit(ds_id, "Exporting…")
        pid, units, backend, sig = c.published.result_id, self.units.units_for(ds_id), self.backend, self._sig

        def job():
            try:
                sig.exported.emit(ds_id, backend.export_dcir(pid, columns, units, fmt, path,
                                                             lambda p: sig.exportProgress.emit(ds_id, p), ev))
            except ExportCancelled:
                sig.exportFailed.emit(ds_id, "")
            except Exception as ex:
                sig.exportFailed.emit(ds_id, _err(ex))

        self._start(job)

    def cancel_export(self, ds_id: str) -> None:
        if ev := self._export_cancel.get(ds_id):
            ev.set()

    def is_exporting(self, ds_id: str) -> bool:
        return ds_id in self._export_cancel

    def _on_exported(self, ds_id: str, receipts) -> None:
        self._export_cancel.pop(ds_id, None)
        self.exportState.emit(ds_id, "")
        self.exportFinished.emit(ds_id, f"Exported {receipts[0].rows:,} measurements to {receipts[0].path}")

    def _on_export_failed(self, ds_id: str, msg: str) -> None:
        self._export_cancel.pop(ds_id, None)
        self.exportState.emit(ds_id, "")
        if msg:
            self.exportError.emit(ds_id, msg)
        else:
            self.exportFinished.emit(ds_id, "Export cancelled. Nothing was written.")

    # ---------------------------------------------------------------- external events
    def units_changed(self, ds_id: str) -> None:
        """Values are re-read in the new units: events (time), previews, manual measurements, published result, trace."""
        c, did = self.dc(ds_id), self.dcir_id(ds_id)
        if c is None or did is None:
            return
        c.previews.clear()
        self.traces.pop(ds_id, None)
        self._trace_key.pop(ds_id, None)
        units = self.units.units_for(ds_id)
        manual_ids, pub = list(c.manual), c.published.result_id if c.published else None
        backend, sig, key = self.backend, self._sig, (did, units)

        def job():
            try:
                out = {"events": backend.dcir_events(did, units),
                       "manual": {m: backend.manual_measurement(did, m, units) for m in manual_ids},
                       "published": backend.dcir_published(pub, units) if pub else None}
                sig.refreshed.emit(ds_id, key, out)
            except Exception as ex:
                sig.manualFailed.emit(ds_id, f"Could not read DCIR values in the new units: {_err(ex)}")

        self._start(job)

    def _on_refreshed(self, ds_id: str, key, out: dict) -> None:
        c = self.dc(ds_id)
        if c is None or key != (self.dcir_id(ds_id), self.units.units_for(ds_id)):
            return
        if c.detection is not None:
            ids = {e.event_id for e in c.detection.events}
            from dataclasses import replace
            c.detection = replace(c.detection, events=tuple(e for e in out["events"] if e.event_id in ids))
        for mid, m in out["manual"].items():
            if mid in c.manual:
                ev, _old, vals = c.manual[mid]
                c.manual[mid] = (ev, m, vals)
        if out["published"] is not None and c.published is not None:
            from dataclasses import replace
            old = c.published                                         # same times: only the units changed
            c.published = replace(out["published"], measurement_time_s=old.measurement_time_s,
                                  current_time_s=old.current_time_s, event_times=old.event_times)
        self.after_selection(ds_id)

    def elaboration_replaced(self, ds_id: str) -> None:
        c = self.dc(ds_id)
        if c is None:
            return
        if c.is_running:
            self.cancel(ds_id)
        old = self.dcir_id(ds_id)
        if c.elaboration_replaced() and old:
            self.backend.release_dcir(old)
        self.traces.pop(ds_id, None)
        self._trace_key.pop(ds_id, None)
        self.traceReady.emit(ds_id)
        self.changed.emit(ds_id)

    def cancel_all(self) -> None:
        """Application closing: stop every running calculation and export, without updating the views."""
        for token in list(self._cancel.values()):
            token.cancel()
        for event in list(self._export_cancel.values()):
            event.set()

    def dataset_closed(self, ds_id: str) -> None:
        for d in (self._cancel, self._export_cancel):
            if ev := d.pop(ds_id, None):
                ev.cancel() if hasattr(ev, "cancel") else ev.set()
        if did := self.dcir_id(ds_id):
            self.backend.release_dcir(did)
        self.traces.pop(ds_id, None)
