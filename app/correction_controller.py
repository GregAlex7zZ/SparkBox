"""Qt glue for Cycle Correction (DCIR step 3). Decisions live in app/correction.py (pure, tested). Runs
recalculate_cycles in a worker; when the new generation is published it is adopted by every section at once:
Data Elaboration (result, pill "DCIR corrected"), Data Explorer (reload), Capacity (result published by the correction)
and DCIR (re-published events/measurements). Nothing is applied by a selection, a hover or a parameter edit."""
from __future__ import annotations
import gregorithm
from typing import Optional
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from .backend import Backend, CorrectionCancelled, Progress
from .capacity_controller import CapacityController
from .correction import CorrParams, CorrResult, CorrectionState, span_counts
from .dcir_controller import DcirController, _err
from .elab_controller import ElabController
from .state import SessionState
from .units_controller import UnitsController
from .standby import STANDBY


class _Sig(QObject):
    progress = Signal(str, object)
    finished = Signal(str, object, object)     # ds_id, request key, CorrResult
    cancelled = Signal(str)
    failed = Signal(str, str)
    spans = Signal(str, object, object)        # ds_id, key, list[Span]
    overview = Signal(str, object, object)     # ds_id, key, Curve


class _Run(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        self.fn()


class CorrectionController(QObject):
    changed = Signal(str)
    notice = Signal(str, str)

    def __init__(self, state: SessionState, backend: Backend, elab: ElabController, capacity: CapacityController,
                 dcir: DcirController, units: UnitsController, dqdv=None):
        super().__init__()
        self.state, self.backend, self.elab, self.capacity, self.dcir, self.units = state, backend, elab, capacity, dcir, units
        self.dqdv = dqdv
        self.spans: dict[str, list] = {}
        self.overviews: dict[str, object] = {}
        self._keys: dict[tuple, tuple] = {}
        self._cancel: dict[str, gregorithm.CancellationToken] = {}
        self._sig = s = _Sig()
        s.progress.connect(self._on_progress)
        s.finished.connect(self._on_finished)
        s.cancelled.connect(self._on_cancelled)
        s.failed.connect(self._on_failed)
        s.spans.connect(self._on_spans)
        s.overview.connect(self._on_overview)
        dcir.changed.connect(self._dcir_changed)
        elab.resultReplaced.connect(self.elaboration_replaced)
        units.changed.connect(self.units_changed)

    # ---------------------------------------------------------------- access
    def co(self, ds_id: Optional[str]) -> Optional[CorrectionState]:
        d = self.state.get(ds_id) if ds_id else None
        return d.correction if d else None

    def published(self, ds_id: str):
        c = self.dcir.dc(ds_id)
        return c.published if c else None

    def counts(self, ds_id: str) -> tuple[int, int]:
        return span_counts(self.spans.get(ds_id, []))

    def view(self, ds_id: str):
        co, pub = self.co(ds_id), self.published(ds_id)
        return co.state(pub is not None, self.counts(ds_id)[0], pub.fingerprint if pub else None)

    def busy(self, ds_id: str) -> str:
        d = self.state.get(ds_id)
        if d is None:
            return "No dataset"
        if d.elab.is_running:
            return "Data Elaboration is running"
        if d.capacity.is_running:
            return "A Capacity analysis is running"
        if d.dcir.is_running:
            return "A DCIR operation is running"
        if self.dqdv is not None and d.dqdv.is_running:
            return "A dQ/dV analysis is running"
        if d.dcir.unpublished:
            return "Apply & Calculate the DCIR changes first: the spans come from the calculated measurements"
        return ""

    def apply_blocker(self, ds_id: str) -> str:
        co = self.co(ds_id)
        return co.apply_blocker(self.published(ds_id) is not None, self.counts(ds_id)[0], self.busy(ds_id)) if co else "No dataset"

    def remove_blocker(self, ds_id: str) -> str:
        co = self.co(ds_id)
        return co.remove_blocker(self.busy(ds_id)) if co else "No dataset"

    def edit(self, ds_id: str, fn) -> None:
        co = self.co(ds_id)
        if co is not None:
            fn(co)
            self.changed.emit(ds_id)

    def params(self, ds_id: str, enabled: bool) -> Optional[CorrParams]:
        d, co = self.state.get(ds_id), self.co(ds_id)
        if d is None or d.elab.result is None:
            return None
        u = self.units.units_for(ds_id)
        return CorrParams(enabled, co.threshold, u.current, d.elab.result.params.cycle_order if d.elab.result.params else "",
                          self.capacity.current_params(ds_id))

    # ---------------------------------------------------------------- spans + overview (read only)
    def load(self, ds_id: str) -> None:
        pub, d = self.published(ds_id), self.state.get(ds_id)
        if pub is None or d is None or d.elab.result is None:
            self.spans.pop(ds_id, None)
            self.overviews.pop(ds_id, None)
            return
        units, backend, sig = self.units.units_for(ds_id), self.backend, self._sig
        k1, k2 = (pub.result_id, pub.fingerprint, pub.published_at, units), (d.elab.result.result_id, units)
        if self._keys.get(("s", ds_id)) != k1:
            self._keys[("s", ds_id)] = k1

            def job_s():
                try:
                    sig.spans.emit(ds_id, k1, backend.correction_spans(pub.result_id, units))
                except Exception as ex:
                    sig.failed.emit(ds_id, f"Could not read the spans: {_err(ex)}")

            QThreadPool.globalInstance().start(_Run(job_s))
        if self._keys.get(("o", ds_id)) != k2:
            self._keys[("o", ds_id)] = k2
            rid = d.elab.result.result_id

            def job_o():
                try:
                    sig.overview.emit(ds_id, k2, backend.correction_overview(rid, units, 1600))
                except Exception:
                    sig.overview.emit(ds_id, k2, None)

            QThreadPool.globalInstance().start(_Run(job_o))

    def _on_spans(self, ds_id: str, key, spans) -> None:
        if self._keys.get(("s", ds_id)) == key:
            self.spans[ds_id] = list(spans)
            self.changed.emit(ds_id)

    def _on_overview(self, ds_id: str, key, curve) -> None:
        if self._keys.get(("o", ds_id)) == key:
            self.overviews[ds_id] = curve
            self.changed.emit(ds_id)

    def _dcir_changed(self, ds_id: str) -> None:
        self.load(ds_id)
        self.changed.emit(ds_id)

    # ---------------------------------------------------------------- apply / remove (explicit, confirmed by the page)
    def run(self, ds_id: str, enabled: bool) -> None:
        co, d, pub = self.co(ds_id), self.state.get(ds_id), self.published(ds_id)
        blocker = self.apply_blocker(ds_id) if enabled else self.remove_blocker(ds_id)
        p = self.params(ds_id, enabled)
        if co is None or blocker or p is None or pub is None:
            return
        co.begin("apply" if enabled else "remove")
        ev = gregorithm.CancellationToken()
        self._cancel[ds_id] = ev
        self.changed.emit(ds_id)
        rid, pid, backend, sig = d.elab.result.result_id, pub.result_id, self.backend, self._sig
        dqdv_rid = d.dqdv.result.result_id if self.dqdv is not None and d.dqdv.result is not None else None

        def job():
            try:
                sig.finished.emit(ds_id, rid, backend.recalculate_cycles(ds_id, rid, pid, p,
                                                                         STANDBY.paced(ds_id, lambda pr: sig.progress.emit(ds_id, pr), ev), ev,
                                                                         dqdv_rid))
            except CorrectionCancelled:
                sig.cancelled.emit(ds_id)
            except Exception as ex:
                sig.failed.emit(ds_id, _err(ex))

        QThreadPool.globalInstance().start(_Run(job))

    def cancel(self, ds_id: str) -> None:
        co = self.co(ds_id)
        if co is not None and co.request_cancel():
            self._cancel[ds_id].cancel()
            self.changed.emit(ds_id)

    def _on_progress(self, ds_id: str, p: Progress) -> None:
        if (co := self.co(ds_id)) is not None:
            co.update_progress(p.fraction, p.phase, p.publishing)
            self.changed.emit(ds_id)

    def _on_finished(self, ds_id: str, elab_rid: str, res: CorrResult) -> None:
        self._cancel.pop(ds_id, None)
        co, d = self.co(ds_id), self.state.get(ds_id)
        if co is None or d.elab.result is None or d.elab.result.result_id != elab_rid:
            for rel, rid in ((self.backend.release_result, res.elab.result_id),
                             (self.backend.release_capacity, res.capacity.result_id if res.capacity else None)):
                if rid:
                    rel(rid)
            if co is not None:
                co.cancelled()
                self.changed.emit(ds_id)
            return
        co.finished(res)
        self.elab.adopt_corrected(ds_id, res.elab)          # Explorer reloads through resultCorrected
        if res.capacity is not None:
            self.capacity.adopt(ds_id, res.capacity)
        self.dcir.adopt_published(ds_id, res.dcir)
        if self.dqdv is not None and res.dqdv is not None:
            self.dqdv.adopt(ds_id, res.dqdv)
        self._keys.pop(("o", ds_id), None)
        self.load(ds_id)
        self.notice.emit(ds_id, f"Cycle correction applied: {res.n_spans:,} span{'s' if res.n_spans != 1 else ''} excluded. "
                                "Data Explorer, Capacity and DCIR show the corrected elaboration." if res.enabled else
                         "Cycle correction removed: the uncorrected cycle labels are shown again.")
        self.changed.emit(ds_id)

    def _on_cancelled(self, ds_id: str) -> None:
        self._cancel.pop(ds_id, None)
        if (co := self.co(ds_id)) is not None:
            co.cancelled()
            self.changed.emit(ds_id)

    def _on_failed(self, ds_id: str, msg: str) -> None:
        self._cancel.pop(ds_id, None)
        co = self.co(ds_id)
        if co is not None and co.is_running:
            co.failed(msg)
        elif co is not None:
            self.notice.emit(ds_id, msg)
        self.changed.emit(ds_id)

    # ---------------------------------------------------------------- external events
    def elaboration_replaced(self, ds_id: str) -> None:
        co = self.co(ds_id)
        if co is None:
            return
        if co.is_running:
            self.cancel(ds_id)
        co.elaboration_replaced()
        self.spans.pop(ds_id, None)
        self.overviews.pop(ds_id, None)
        self._keys.pop(("s", ds_id), None)
        self._keys.pop(("o", ds_id), None)
        self.changed.emit(ds_id)

    def units_changed(self, ds_id: str) -> None:
        self.load(ds_id)

    def cancel_all(self) -> None:
        """Application closing: stop every running correction, without updating the views."""
        for token in list(self._cancel.values()):
            token.cancel()

    def dataset_closed(self, ds_id: str) -> None:
        if ev := self._cancel.pop(ds_id, None):
            ev.cancel()
        self.spans.pop(ds_id, None)
        self.overviews.pop(ds_id, None)
