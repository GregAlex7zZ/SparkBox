"""Qt glue for Data Elaboration: user default parameters (persisted), parameter editing, one background job with a
cooperative cancel event, and result bookkeeping. All state lives in app/elaboration.py (pure, tested); this class only
moves it between the GUI thread and the worker and emits `changed(ds_id)` so every widget re-reads the state."""
from __future__ import annotations
import threading
import gregorithm
from PySide6.QtCore import QObject, QRunnable, QSettings, QThreadPool, Signal
from .backend import Backend, ElabCancelled, ExportCancelled, Progress
from .elaboration import (CYCLE_ORDER_LABELS, FACTORY_DEFAULTS, ElabParams, ElabResult, ElabState,
                          fmt_param, parse_number)
from .state import SessionState


class UserDefaults(QObject):
    """Settings -> Data Elaboration -> Default Parameters. New datasets start from these values (existing datasets keep
    their own). Invalid values are never saved."""
    changed = Signal()
    _KEYS = ("rest_current_threshold_ma", "cc_relative_tolerance_pct", "cv_absolute_tolerance_mv")

    def __init__(self):
        super().__init__()
        self._s = QSettings("gregorithm", "gui")
        p = FACTORY_DEFAULTS
        order = str(self._s.value("elab/cycle_order", p.cycle_order))
        vals = {}
        for k in self._KEYS:
            v, err = parse_number(str(self._s.value(f"elab/{k}", getattr(p, k))))
            vals[k] = getattr(p, k) if err else v
        self.params = ElabParams(order if order in CYCLE_ORDER_LABELS else p.cycle_order, **vals)

    def set(self, params: ElabParams) -> None:
        self.params = params
        self._s.setValue("elab/cycle_order", params.cycle_order)
        for k in self._KEYS:
            self._s.setValue(f"elab/{k}", fmt_param(getattr(params, k)))
        self.changed.emit()


class _Sig(QObject):
    progress = Signal(str, object)
    finished = Signal(str, object)
    cancelled = Signal(str)
    failed = Signal(str, str)
    exportProgress = Signal(str, object)
    exported = Signal(str, object)
    exportFailed = Signal(str, str)


class _Call(QRunnable):
    """Run one function on the thread pool (export jobs)."""
    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        self.fn()


class _Job(QRunnable):
    def __init__(self, ds_id, ds_key, params, backend: Backend, cancel: gregorithm.CancellationToken, sig: _Sig):
        super().__init__()
        self.a = (ds_id, ds_key, params, backend, cancel, sig)

    def run(self) -> None:
        ds_id, ds_key, params, backend, cancel, sig = self.a
        try:
            res = backend.elaborate(ds_key, params, lambda p: sig.progress.emit(ds_id, p), cancel)
            sig.finished.emit(ds_id, res)
        except ElabCancelled:
            sig.cancelled.emit(ds_id)
        except Exception as e:                           # shown exactly as reported, never swallowed
            sig.failed.emit(ds_id, str(e) or type(e).__name__)


class ElabController(QObject):
    changed = Signal(str)             # dataset id whose elaboration state changed
    resultReplaced = Signal(str)      # a new result was published (tables must reload)
    resultCorrected = Signal(str)     # a compound DCIR correction publication replaced the elaboration
    runFinished = Signal(str, str)    # ds_id, "ok" | "cancelled" | "failed"
    exportState = Signal(str, str)    # ds_id, "Exporting…" text ('' = idle)
    exportFinished = Signal(str, str)  # ds_id, status-bar text
    exportError = Signal(str, str)

    def __init__(self, state: SessionState, backend: Backend, defaults: UserDefaults):
        super().__init__()
        self.state, self.backend, self.defaults = state, backend, defaults
        self._cancel: dict[str, gregorithm.CancellationToken] = {}
        self._sig = _Sig()
        self._sig.progress.connect(self._on_progress)
        self._sig.finished.connect(self._on_finished)
        self._sig.cancelled.connect(self._on_cancelled)
        self._sig.failed.connect(self._on_failed)
        self._export_cancel: dict[str, threading.Event] = {}
        self._sig.exportProgress.connect(
            lambda ds, p: self.exportState.emit(ds, f"Exporting… {p.done or 0:,} of {p.total or 0:,} rows"))
        self._sig.exported.connect(self._on_exported)
        self._sig.exportFailed.connect(self._on_export_failed)
        defaults.changed.connect(lambda: [self.changed.emit(d.id) for d in self.state.datasets])

    # --- access ---
    def elab(self, ds_id: str) -> ElabState | None:
        d = self.state.get(ds_id)
        return d.elab if d else None

    def start_blocker(self, ds_id: str) -> str:
        """'' when Process may start, else the reason shown as the disabled-button tooltip."""
        e = self.elab(ds_id)
        if e is None:
            return "No dataset"
        other = self.state.running_id()
        if other and other != ds_id:
            return "Another dataset is being processed. Wait for it to finish or cancel it."
        if e.is_running:
            return "Processing is already running"
        if not e.valid:
            return "Fix the highlighted parameter first"
        return ""

    # --- editing ---
    def set_order(self, ds_id: str, order: str) -> None:
        if (e := self.elab(ds_id)) is not None:
            e.set_order(order)
            self.changed.emit(ds_id)

    def set_number_text(self, ds_id: str, key: str, text: str) -> None:
        if (e := self.elab(ds_id)) is not None:
            e.set_number_text(key, text)
            self.changed.emit(ds_id)

    def set_sample_text(self, ds_id: str, key: str, text: str) -> None:
        """Store optional sample metadata without changing elaboration parameters or validity."""
        if (e := self.elab(ds_id)) is not None:
            e.set_sample_text(key, text)
            self.backend.set_sample_values(ds_id, e.sample)
            self.changed.emit(ds_id)

    def reset(self, ds_id: str) -> None:
        if (e := self.elab(ds_id)) is not None:
            e.reset_to(self.defaults.params)
            self.changed.emit(ds_id)

    # --- running ---
    def process(self, ds_id: str) -> None:
        d, why = self.state.get(ds_id), self.start_blocker(ds_id)
        if d is None or why:
            return
        d.elab.begin()
        ev = gregorithm.CancellationToken()
        self._cancel[ds_id] = ev
        self.changed.emit(ds_id)
        QThreadPool.globalInstance().start(_Job(ds_id, d.id, d.elab.params, self.backend, ev, self._sig))

    def cancel(self, ds_id: str) -> None:
        e = self.elab(ds_id)
        if e is not None and e.request_cancel():
            self._cancel[ds_id].cancel()
            self.changed.emit(ds_id)

    def cancel_all(self) -> None:
        for ds_id in list(self._cancel):
            self.cancel(ds_id)
        for ev in list(self._export_cancel.values()):
            ev.set()

    # --- worker results (GUI thread) ---
    def _on_progress(self, ds_id: str, p: Progress) -> None:
        if (e := self.elab(ds_id)) is not None:
            e.update_progress(p.fraction, p.phase, p.done, p.total, p.publishing)
            self.changed.emit(ds_id)

    def _on_finished(self, ds_id: str, res: ElabResult) -> None:
        self._cancel.pop(ds_id, None)
        e = self.elab(ds_id)
        if e is None:                                    # dataset closed meanwhile
            self.backend.release_result(res.result_id)
            return
        old = e.result
        e.finish(res)
        if old is not None and old.result_id != res.result_id:
            self.backend.release_result(old.result_id)
        self.changed.emit(ds_id)
        self.resultReplaced.emit(ds_id)
        self.runFinished.emit(ds_id, "ok")

    def adopt_corrected(self, ds_id: str, result: ElabResult) -> None:
        """Adopt one atomically published correction generation.

        This deliberately emits a distinct signal: a Process replacement
        invalidates DCIR, while a correction transports its DCIR provenance as
        part of the same compound result.
        """
        e = self.elab(ds_id)
        if e is None:
            self.backend.release_result(result.result_id)
            return
        e.finish(result)
        self.changed.emit(ds_id)
        self.resultCorrected.emit(ds_id)

    def _on_cancelled(self, ds_id: str) -> None:
        self._cancel.pop(ds_id, None)
        if (e := self.elab(ds_id)) is not None:
            e.cancelled()
            self.changed.emit(ds_id)
        self.runFinished.emit(ds_id, "cancelled")

    # --- export ---
    def export(self, ds_id: str, kind: str, fmt: str, path: str) -> None:
        """Export the current elaboration table ``kind`` ('points' or 'segments') to ``path`` as CSV or Parquet, in
        the units of the Elaborated Data view, on a worker thread. One export per dataset at a time."""
        e = self.elab(ds_id)
        if e is None or e.result is None or ds_id in self._export_cancel:
            return
        ev = threading.Event()
        self._export_cancel[ds_id] = ev
        self.exportState.emit(ds_id, "Exporting…")
        rid, backend, sig = e.result.result_id, self.backend, self._sig

        def job():
            try:
                sig.exported.emit(ds_id, backend.export_elab(rid, kind, None, fmt, path,
                                                             lambda p: sig.exportProgress.emit(ds_id, p), ev))
            except ExportCancelled:
                sig.exportFailed.emit(ds_id, "")
            except Exception as ex:
                sig.exportFailed.emit(ds_id, str(ex) or type(ex).__name__)

        QThreadPool.globalInstance().start(_Call(job))

    def cancel_export(self, ds_id: str) -> None:
        if ev := self._export_cancel.get(ds_id):
            ev.set()

    def is_exporting(self, ds_id: str) -> bool:
        return ds_id in self._export_cancel

    def _on_exported(self, ds_id: str, receipts) -> None:
        self._export_cancel.pop(ds_id, None)
        self.exportState.emit(ds_id, "")
        self.exportFinished.emit(ds_id, f"Exported {receipts[0].rows:,} rows to {receipts[0].path}")

    def _on_export_failed(self, ds_id: str, msg: str) -> None:
        self._export_cancel.pop(ds_id, None)
        self.exportState.emit(ds_id, "")
        if msg:
            self.exportError.emit(ds_id, msg)
        else:
            self.exportFinished.emit(ds_id, "Export cancelled. Nothing was written.")

    def _on_failed(self, ds_id: str, msg: str) -> None:
        self._cancel.pop(ds_id, None)
        if (e := self.elab(ds_id)) is not None:
            e.failed(msg)
            self.changed.emit(ds_id)
        self.runFinished.emit(ds_id, "failed")

