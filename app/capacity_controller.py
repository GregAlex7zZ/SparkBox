"""Qt glue for the Capacity section. Decisions live in app/capacity.py (pure, tested). This class runs
analyze_capacity, table loading and export in QThreadPool workers, keeps the previous result until a new one is
published, and drops the result when the Data Elaboration result it belongs to is replaced."""
from __future__ import annotations
import threading
import gregorithm
from typing import Optional
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from .backend import Backend, CapacityCancelled, CapacityTable, ExportCancelled, Progress
from .capacity import CapacityState, CapParams, CapResult, selectable_columns
from .elab_controller import ElabController
from .state import SessionState
from .units_controller import UnitsController
from .standby import STANDBY


class _Sig(QObject):
    progress = Signal(str, object)
    finished = Signal(str, object)
    cancelled = Signal(str)
    failed = Signal(str, str)
    table = Signal(str, str, object)          # ds_id, cap result id, CapacityTable
    tableFailed = Signal(str, str)
    cycles = Signal(str, str, object)         # ds_id, elab result id, list[int]
    exportProgress = Signal(str, object)
    exported = Signal(str, object)
    exportFailed = Signal(str, str)


class _Run(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        self.fn()


class CapacityController(QObject):
    changed = Signal(str)
    tableReady = Signal(str)
    exportState = Signal(str, str)            # ds_id, message for the table bar / status bar ("" when idle)
    exportFinished = Signal(str, str)         # ds_id, status-bar text
    exportError = Signal(str, str)

    def __init__(self, state: SessionState, backend: Backend, elab: ElabController, units: UnitsController):
        super().__init__()
        self.state, self.backend, self.elab, self.units = state, backend, elab, units
        self.tables: dict[str, CapacityTable] = {}
        self.elab_cycles: dict[str, list[int]] = {}          # elab result id -> cycle IDs (Reference Cycle hint)
        self._cancel: dict[str, gregorithm.CancellationToken] = {}
        self._export_cancel: dict[str, threading.Event] = {}
        self._sig = s = _Sig()
        s.progress.connect(self._on_progress)
        s.finished.connect(self._on_finished)
        s.cancelled.connect(self._on_cancelled)
        s.failed.connect(self._on_failed)
        s.table.connect(self._on_table)
        s.tableFailed.connect(lambda ds, msg: self.exportError.emit(ds, f"Could not read the Capacity table: {msg}"))
        s.cycles.connect(lambda ds, rid, c: (self.elab_cycles.__setitem__(rid, list(c)), self.changed.emit(ds)))
        s.exportProgress.connect(lambda ds, p: self.exportState.emit(ds, f"Exporting… {p.done or 0:,} of {p.total or 0:,} rows"))
        s.exported.connect(self._on_exported)
        s.exportFailed.connect(self._on_export_failed)
        elab.changed.connect(self.changed)                    # Sample values / Cycle Order shown read-only here
        elab.resultReplaced.connect(self.elaboration_replaced)
        units.changed.connect(self.units_changed)

    # --- access ---
    def cap(self, ds_id: Optional[str]) -> Optional[CapacityState]:
        d = self.state.get(ds_id) if ds_id else None
        return d.capacity if d else None

    def elab_result_id(self, ds_id: str) -> Optional[str]:
        d = self.state.get(ds_id)
        return d.elab.result.result_id if d and d.elab.result else None

    def current_params(self, ds_id: str) -> Optional[CapParams]:
        """Parameters an analysis would use now: Cycle Order of the PUBLISHED elaboration (must match it), the Reference
        Cycle typed here, the Sample values of Data Elaboration."""
        d = self.state.get(ds_id)
        if d is None or d.elab.result is None:
            return None
        return d.capacity.params(d.elab.result.params.cycle_order, d.elab.sample)

    def selectable(self, ds_id: str) -> list[str]:
        import math
        t = self.tables.get(ds_id)
        if t is None:
            return ["cycle"]
        with_vals = {k for k, arr in t.values.items() if any(math.isfinite(float(v)) for v in arr)}
        return selectable_columns(with_vals)

    def cycles_of_elaboration(self, ds_id: str) -> Optional[list[int]]:
        rid = self.elab_result_id(ds_id)
        if rid is None:
            return None
        if rid not in self.elab_cycles:
            self.elab_cycles[rid] = None                       # requested once
            QThreadPool.globalInstance().start(_Run(lambda: self._load_cycles(ds_id, rid)))
            return None
        return self.elab_cycles.get(rid)

    def _load_cycles(self, ds_id: str, rid: str) -> None:
        try:
            self._sig.cycles.emit(ds_id, rid, self.backend.result_cycles(rid))
        except Exception:
            pass                                               # the hint then shows only the default

    # --- edits (GUI thread) ---
    def edit(self, ds_id: str, fn) -> None:
        c = self.cap(ds_id)
        if c is not None:
            fn(c)
            self.changed.emit(ds_id)

    def set_reference_text(self, ds_id: str, text: str) -> None:
        self.edit(ds_id, lambda c: c.set_reference_text(text))

    # --- analysis ---
    def analyze(self, ds_id: str) -> None:
        d, c = self.state.get(ds_id), self.cap(ds_id)
        p = self.current_params(ds_id) if d else None
        if c is None or not c.can_analyze(p, d.elab.is_running):
            return
        c.begin(p)
        ev = gregorithm.CancellationToken()
        self._cancel[ds_id] = ev
        self.changed.emit(ds_id)
        rid, backend, sig = d.elab.result.result_id, self.backend, self._sig

        def job():
            try:
                sig.finished.emit(ds_id, backend.analyze_capacity(ds_id, rid, p, STANDBY.paced(ds_id, lambda pr: sig.progress.emit(ds_id, pr), ev), ev))
            except CapacityCancelled:
                sig.cancelled.emit(ds_id)
            except Exception as ex:                            # shown exactly as reported
                sig.failed.emit(ds_id, str(ex) or type(ex).__name__)

        QThreadPool.globalInstance().start(_Run(job))

    def cancel(self, ds_id: str) -> None:
        c = self.cap(ds_id)
        if c is not None and c.request_cancel():
            self._cancel[ds_id].cancel()
            self.changed.emit(ds_id)

    def _on_progress(self, ds_id: str, p: Progress) -> None:
        if (c := self.cap(ds_id)) is not None:
            c.update_progress(p.fraction, p.phase, p.publishing)
            self.changed.emit(ds_id)

    def _on_finished(self, ds_id: str, res: CapResult) -> None:
        self._cancel.pop(ds_id, None)
        c = self.cap(ds_id)
        if c is None:
            self.backend.release_capacity(res.result_id)       # dataset closed meanwhile
            return
        if res.elab_result_id != self.elab_result_id(ds_id):  # the elaboration changed while analysing
            self.backend.release_capacity(res.result_id)
            c.cancelled()
            c.elaboration_replaced()
            self.changed.emit(ds_id)
            return
        old = c.result
        c.finish(res)
        if old is not None:
            self.backend.release_capacity(old.result_id)
        self.tables.pop(ds_id, None)
        self.load_table(ds_id)
        self.changed.emit(ds_id)

    def _on_cancelled(self, ds_id: str) -> None:
        self._cancel.pop(ds_id, None)
        if (c := self.cap(ds_id)) is not None:
            c.cancelled()
            self.changed.emit(ds_id)

    def _on_failed(self, ds_id: str, msg: str) -> None:
        self._cancel.pop(ds_id, None)
        if (c := self.cap(ds_id)) is not None:
            c.failed(msg)
            self.changed.emit(ds_id)

    # --- table ---
    def load_table(self, ds_id: str) -> None:
        c = self.cap(ds_id)
        if c is None or c.result is None:
            return
        rid, units, backend, sig = c.result.result_id, self.units.units_for(ds_id), self.backend, self._sig

        def job():
            try:
                sig.table.emit(ds_id, rid, backend.capacity_table(rid, units))
            except Exception as ex:
                sig.tableFailed.emit(ds_id, str(ex) or type(ex).__name__)

        QThreadPool.globalInstance().start(_Run(job))

    def _on_table(self, ds_id: str, rid: str, table: CapacityTable) -> None:
        c = self.cap(ds_id)
        if c is None or c.result is None or c.result.result_id != rid:
            return                                             # stale
        self.tables[ds_id] = table
        c.view.fix_axes(self.selectable(ds_id))
        self.tableReady.emit(ds_id)
        self.changed.emit(ds_id)

    # --- export ---
    def export(self, ds_id: str, fmt: str, columns: list[str], include_diagnostics: bool, path: str) -> None:
        c, t = self.cap(ds_id), self.tables.get(ds_id)
        if c is None or c.result is None or t is None or ds_id in self._export_cancel:
            return
        cycles = c.view.export_cycles([int(x) for x in t.values["cycle"]])
        ev = threading.Event()
        self._export_cancel[ds_id] = ev
        self.exportState.emit(ds_id, "Exporting…")
        rid, units, backend, sig = c.result.result_id, self.units.units_for(ds_id), self.backend, self._sig

        def job():
            try:
                sig.exported.emit(ds_id, backend.export_capacity(rid, cycles, columns, units, fmt, path, include_diagnostics,
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
        main = receipts[0]
        self.exportFinished.emit(ds_id, f"Exported {main.rows:,} cycles to {main.path}")

    def _on_export_failed(self, ds_id: str, msg: str) -> None:
        self._export_cancel.pop(ds_id, None)
        self.exportState.emit(ds_id, "")
        if msg:
            self.exportError.emit(ds_id, msg)
        else:
            self.exportFinished.emit(ds_id, "Export cancelled. Nothing was written.")

    # --- external events ---
    def elaboration_replaced(self, ds_id: str) -> None:
        c = self.cap(ds_id)
        if c is None:
            return
        if c.is_running:
            self.cancel(ds_id)                                 # its result would belong to the old elaboration
        old = c.result
        if c.elaboration_replaced() and old is not None:
            self.backend.release_capacity(old.result_id)
        self.tables.pop(ds_id, None)
        self.tableReady.emit(ds_id)
        self.changed.emit(ds_id)

    def adopt(self, ds_id: str, result: CapResult) -> None:
        c = self.cap(ds_id)
        if c is None:
            self.backend.release_capacity(result.result_id)
            return
        old = c.result
        c.finish(result)
        if old is not None and old.result_id != result.result_id:
            self.backend.release_capacity(old.result_id)
        self.tables.pop(ds_id, None)
        self.load_table(ds_id)
        self.changed.emit(ds_id)

    def units_changed(self, ds_id: str) -> None:
        if (c := self.cap(ds_id)) is not None and c.result is not None:
            c.view.viewport = type(c.view.viewport)()
            self.load_table(ds_id)

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
        c = self.cap(ds_id)
        if c is not None and c.result is not None:
            self.backend.release_capacity(c.result.result_id)
        self.tables.pop(ds_id, None)
