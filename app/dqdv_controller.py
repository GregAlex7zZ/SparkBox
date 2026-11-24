"""Qt glue for the dQ/dV section. Decisions live in app/dqdv.py (pure, tested). Runs analyze_dqdv,
the curve read (dQ/dV curves and the measured / reconstructed voltage curves) and export in QThreadPool workers; stale responses are dropped; the previous result stays until a new one
is published; a new Data Elaboration result drops it ("Elaboration changed"); Cycle Correction may re-label it."""
from __future__ import annotations
import threading
import gregorithm
from typing import Optional
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from .backend import Backend, DqdvCancelled, ExportCancelled, Progress
from .dqdv import DqdvParams, DqdvResult, DqdvState
from .elab_controller import ElabController
from .state import SessionState
from .units_controller import UnitsController
from .standby import STANDBY


class _Sig(QObject):
    progress = Signal(str, object)
    finished = Signal(str, object, object)
    cancelled = Signal(str)
    failed = Signal(str, str)
    curves = Signal(str, object, object)
    curvesFailed = Signal(str, str)
    exportProgress = Signal(str, object)
    exported = Signal(str, object)
    exportFailed = Signal(str, str)


class _Run(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        self.fn()


class DqdvController(QObject):
    changed = Signal(str)
    progressed = Signal(str)                    # only fraction/phase moved: views update their bars
    curvesReady = Signal(str)
    exportState = Signal(str, str)
    exportFinished = Signal(str, str)
    exportError = Signal(str, str)

    def __init__(self, state: SessionState, backend: Backend, elab: ElabController, units: UnitsController,
                 cycles_fn=None, dcir=None):
        super().__init__()
        self.state, self.backend, self.elab, self.units = state, backend, elab, units
        self.cycles_fn = cycles_fn                     # ds_id -> list[int] | None (cycles of the published elaboration)
        self.dcir = dcir                               # DcirController (accepted spans, fingerprint)
        self.curves: dict[str, list] = {}
        self.recon: dict[str, list] = {}               # ReconCurveData per dataset (Voltage Reconstruction view)
        self._ckey: dict[str, tuple] = {}
        self._cancel: dict[str, gregorithm.CancellationToken] = {}
        self._export_cancel: dict[str, threading.Event] = {}
        self._sig = s = _Sig()
        s.progress.connect(self._on_progress)
        s.finished.connect(self._on_finished)
        s.cancelled.connect(self._on_cancelled)
        s.failed.connect(self._on_failed)
        s.curves.connect(self._on_curves)
        s.curvesFailed.connect(lambda ds, m: self.exportError.emit(ds, f"Could not read the dQ/dV curves: {m}"))
        s.exportProgress.connect(lambda ds, p: self.exportState.emit(ds, f"Exporting… {p.done or 0:,} rows"))
        s.exported.connect(self._on_exported)
        s.exportFailed.connect(self._on_export_failed)
        elab.changed.connect(self.changed)
        elab.resultReplaced.connect(self.elaboration_replaced)
        units.changed.connect(self.units_changed)

    # ---------------------------------------------------------------- access
    def dq(self, ds_id: Optional[str]) -> Optional[DqdvState]:
        d = self.state.get(ds_id) if ds_id else None
        return d.dqdv if d else None

    def available(self, ds_id: str) -> Optional[list[int]]:
        return self.cycles_fn(ds_id) if self.cycles_fn else None

    def current_params(self, ds_id: str) -> Optional[DqdvParams]:
        q = self.dq(ds_id)
        return q.params(self.available(ds_id)) if q else None

    def dcir_published(self, ds_id: str):
        c = self.dcir.dc(ds_id) if self.dcir else None
        return c.published if c else None

    def dcir_fingerprint(self, ds_id: str) -> str:
        p = self.dcir_published(ds_id)
        return p.fingerprint if p else ""

    def view(self, ds_id: str):
        q, d = self.dq(ds_id), self.state.get(ds_id)
        rid = d.elab.result.result_id if d and d.elab.result else ""
        return q.state(self.current_params(ds_id), self.dcir_fingerprint(ds_id), rid)

    def modified(self, ds_id: str) -> set[str]:
        q, d = self.dq(ds_id), self.state.get(ds_id)
        rid = d.elab.result.result_id if d and d.elab.result else ""
        return q.modified(self.current_params(ds_id), self.dcir_fingerprint(ds_id), rid) if q else set()

    def busy(self, ds_id: str) -> str:
        d = self.state.get(ds_id)
        if d is None or d.elab.result is None:
            return "Run Data Elaboration first"
        if d.elab.is_running:
            return "Wait for Data Elaboration to finish"
        if d.correction.is_running:
            return "Wait for the cycle correction to finish"
        return ""

    def blocker(self, ds_id: str) -> str:
        q = self.dq(ds_id)
        return q.analyze_blocker(self.current_params(ds_id), self.busy(ds_id), self.available(ds_id)) if q else "No dataset"

    def edit(self, ds_id: str, fn) -> None:
        q = self.dq(ds_id)
        if q is not None:
            fn(q)
            self.changed.emit(ds_id)

    # ---------------------------------------------------------------- analysis (explicit only)
    def analyze(self, ds_id: str) -> None:
        q, d, p = self.dq(ds_id), self.state.get(ds_id), self.current_params(ds_id)
        if q is None or p is None or self.blocker(ds_id):
            return
        q.begin(p)
        ev = gregorithm.CancellationToken()
        self._cancel[ds_id] = ev
        self.changed.emit(ds_id)
        pub = self.dcir_published(ds_id)
        rid, pid, backend, sig = d.elab.result.result_id, pub.result_id if pub else None, self.backend, self._sig

        def job():
            try:
                sig.finished.emit(ds_id, rid, backend.analyze_dqdv(ds_id, rid, p, pid, STANDBY.paced(ds_id, lambda pr: sig.progress.emit(ds_id, pr), ev), ev))
            except DqdvCancelled:
                sig.cancelled.emit(ds_id)
            except Exception as ex:
                sig.failed.emit(ds_id, str(ex) or type(ex).__name__)

        QThreadPool.globalInstance().start(_Run(job))

    def cancel(self, ds_id: str) -> None:
        q = self.dq(ds_id)
        if q is not None and q.request_cancel():
            self._cancel[ds_id].cancel()
            self.changed.emit(ds_id)

    def _on_progress(self, ds_id: str, p: Progress) -> None:
        """Record a progress tick. A tick that only moves the fraction or the phase text emits ``progressed``, so
        the page updates its bars without a full refresh (and the dock and Comparison, which show nothing of it, are
        left alone); a tick that changes the run state (running -> saving) emits ``changed`` as any state change."""
        if (q := self.dq(ds_id)) is not None:
            run = q.run
            q.update_progress(p.fraction, p.phase, p.publishing)
            (self.changed if q.run is not run else self.progressed).emit(ds_id)

    def _on_finished(self, ds_id: str, elab_rid: str, res: DqdvResult) -> None:
        self._cancel.pop(ds_id, None)
        q, d = self.dq(ds_id), self.state.get(ds_id)
        if q is None or d.elab.result is None or d.elab.result.result_id != elab_rid:
            self.backend.release_dqdv(res.result_id)
            if q is not None:
                q.cancelled()
                q.elaboration_replaced()
                self.changed.emit(ds_id)
            return
        old = q.result
        q.finish(res)
        if old is not None and old.result_id != res.result_id:
            self.backend.release_dqdv(old.result_id)
        self.curves.pop(ds_id, None)
        self.recon.pop(ds_id, None)
        self.load_curves(ds_id)
        self.changed.emit(ds_id)

    def _on_cancelled(self, ds_id: str) -> None:
        self._cancel.pop(ds_id, None)
        if (q := self.dq(ds_id)) is not None:
            q.cancelled()
            self.changed.emit(ds_id)

    def _on_failed(self, ds_id: str, msg: str) -> None:
        self._cancel.pop(ds_id, None)
        if (q := self.dq(ds_id)) is not None:
            q.failed(msg)
            self.changed.emit(ds_id)

    # ---------------------------------------------------------------- curves (published rows, display units)
    def load_curves(self, ds_id: str) -> None:
        q = self.dq(ds_id)
        if q is None or q.result is None:
            return
        units = self.units.units_for(ds_id)
        key = (q.result.result_id, units)
        if self._ckey.get(ds_id) == key and ds_id in self.curves:
            return
        self._ckey[ds_id] = key
        rid, backend, sig = q.result.result_id, self.backend, self._sig

        def job():
            try:
                curves = backend.dqdv_curves(rid, units)
                recon = backend.dqdv_reconstruction(rid, units)
                sig.curves.emit(ds_id, key, (curves, recon))
            except Exception as ex:
                sig.curvesFailed.emit(ds_id, str(ex) or type(ex).__name__)

        QThreadPool.globalInstance().start(_Run(job))

    def _on_curves(self, ds_id: str, key, data) -> None:
        if self._ckey.get(ds_id) != key:
            return
        curves, recon = data
        self.curves[ds_id] = list(curves)
        self.recon[ds_id] = list(recon)
        self.curvesReady.emit(ds_id)
        self.changed.emit(ds_id)

    # ---------------------------------------------------------------- export (shown cycles and phases, display units)
    def export(self, ds_id: str, fmt: str, columns: list[str], path: str) -> None:
        q = self.dq(ds_id)
        if q is None or q.result is None or ds_id in self._export_cancel:
            return
        analyzed = list(q.result.params.cycles)
        cycles, phases = tuple(q.view.visible_cycles(analyzed)), q.view.visible_phases()
        ev = threading.Event()
        self._export_cancel[ds_id] = ev
        self.exportState.emit(ds_id, "Exporting…")
        rid, units, backend, sig = q.result.result_id, self.units.units_for(ds_id), self.backend, self._sig

        def job():
            try:
                sig.exported.emit(ds_id, backend.export_dqdv(rid, cycles, phases, columns, units, fmt, path,
                                                             lambda p: sig.exportProgress.emit(ds_id, p), ev))
            except ExportCancelled:
                sig.exportFailed.emit(ds_id, "")
            except Exception as ex:
                sig.exportFailed.emit(ds_id, str(ex) or type(ex).__name__)

        QThreadPool.globalInstance().start(_Run(job))

    def cancel_export(self, ds_id: str) -> None:
        if ev := self._export_cancel.get(ds_id):
            ev.set()

    def is_exporting(self, ds_id: str) -> bool:
        return ds_id in self._export_cancel

    def _on_exported(self, ds_id: str, receipts) -> None:
        self._export_cancel.pop(ds_id, None)
        self.exportState.emit(ds_id, "")
        self.exportFinished.emit(ds_id, f"Exported {receipts[0].rows:,} dQ/dV rows to {receipts[0].path}")

    def _on_export_failed(self, ds_id: str, msg: str) -> None:
        self._export_cancel.pop(ds_id, None)
        self.exportState.emit(ds_id, "")
        if msg:
            self.exportError.emit(ds_id, msg)
        else:
            self.exportFinished.emit(ds_id, "Export cancelled. Nothing was written.")

    # ---------------------------------------------------------------- external events
    def adopt(self, ds_id: str, res: DqdvResult) -> None:
        """Cycle Correction re-labelled the existing dQ/dV result (labels only, no new derivative)."""
        q = self.dq(ds_id)
        if q is None:
            self.backend.release_dqdv(res.result_id)
            return
        old = q.result
        q.adopt(res)
        if old is not None and old.result_id != res.result_id:
            self.backend.release_dqdv(old.result_id)
        self.curves.pop(ds_id, None)
        self.recon.pop(ds_id, None)
        self.load_curves(ds_id)
        self.changed.emit(ds_id)

    def elaboration_replaced(self, ds_id: str) -> None:
        q = self.dq(ds_id)
        if q is None:
            return
        if q.is_running:
            self.cancel(ds_id)
        old = q.result
        if q.elaboration_replaced() and old is not None:
            self.backend.release_dqdv(old.result_id)
        self.curves.pop(ds_id, None)
        self.recon.pop(ds_id, None)
        self.curvesReady.emit(ds_id)
        self.changed.emit(ds_id)

    def units_changed(self, ds_id: str) -> None:
        q = self.dq(ds_id)
        if q is not None and q.result is not None:
            from .explorer import Viewport
            q.view.viewport = Viewport()
            q.view.recon_viewport = Viewport()
            self.load_curves(ds_id)

    def dataset_closed(self, ds_id: str) -> None:
        for d in (self._cancel, self._export_cancel):
            if ev := d.pop(ds_id, None):
                ev.cancel() if hasattr(ev, "cancel") else ev.set()
        q = self.dq(ds_id)
        if q is not None and q.result is not None:
            self.backend.release_dqdv(q.result.result_id)
        self.curves.pop(ds_id, None)
        self.recon.pop(ds_id, None)

    def cancel_all(self) -> None:
        for token in list(self._cancel.values()):
            token.cancel()
        for event in list(self._export_cancel.values()):
            event.set()
