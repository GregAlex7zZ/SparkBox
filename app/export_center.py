"""Export Center: every table export of one dataset, offered together.

Purpose: the Export button at the bottom of the section list opens one window listing the exports of every section
(elaborated data and segments, Capacity, DCIR, dQ/dV, GITT / ICI). Exports whose section has a result can be ticked;
the others stay locked and say which section must be run first. The ticked exports are written as separate files in
one folder, exactly as each section's own Export would write them with all rows and all columns.

Usage: ``export_items(dataset, gitt_ready)`` (pure, tested) lists what is available; ``ExportCenterController``
runs the chosen exports one after the other on a worker thread and reports each outcome.

Limits: only table exports are offered (chart images are saved from each section, where the chart is shown).
Exports use the section defaults: every row and every column (Capacity: the columns shown for its Sample values;
dQ/dV: every analysed cycle and both phases; GITT / ICI: one file per branch). An existing file is never
overwritten: a free name "<name> (2)" is chosen instead. No scientific value is computed here; every value comes from
the backend export functions, which publish gregorithm's results in the display units.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import gregorithm
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from .backend import Backend, ExportCancelled
from .capacity import shown_columns
from .dcir import RESULT_COLUMNS
from .dqdv import PHASES, TABLE_COLUMNS as DQDV_COLUMNS
from .state import SessionState


@dataclass(frozen=True)
class ExportItem:
    """One export of the Export Center.

    key: stable identifier; section: section name shown as the group; title: what the file contains;
    suffix: file name suffix ("<dataset> - <suffix>.<ext>"); available: a result exists; reason: what to run first
    when it does not (shown on the locked row and when it is clicked)."""
    key: str
    section: str
    title: str
    suffix: str
    available: bool
    reason: str = ""


def export_items(dataset, gitt_ready: bool) -> list[ExportItem]:
    """Exports of ``dataset`` (state.Dataset or None) in section order, each with its availability.

    ``gitt_ready`` is GittIciController.can_export(): GITT / ICI exports only accepted diffusion coefficients, so a
    result without any accepted D cannot be exported either."""
    elab = dataset is not None and dataset.elab.result is not None
    run_elab = "Run Data Elaboration first (Data Elaboration › Process)."
    items = [
        ExportItem("elab_points", "Data Elaboration", "Elaborated data (every point)", "elaborated", elab, run_elab),
        ExportItem("elab_segments", "Data Elaboration", "Detected segments", "segments", elab, run_elab),
    ]
    cap = dataset is not None and dataset.capacity.result is not None
    items.append(ExportItem("capacity", "Capacity", "Capacity table (one row per cycle)", "capacity", cap,
                            "" if cap else (run_elab if not elab else "Run Capacity first (Capacity › Analyze).")))
    dcir = dataset is not None and dataset.dcir.published is not None
    items.append(ExportItem("dcir", "DCIR", "DCIR measurements (one row per accepted pulse)", "dcir", dcir,
                            "" if dcir else (run_elab if not elab else
                                             "Calculate DCIR first (DCIR › Review › Apply & Calculate).")))
    dqdv = dataset is not None and dataset.dqdv.result is not None
    items.append(ExportItem("dqdv", "dQ/dV", "dQ/dV table (every analysed cycle, both phases)", "dqdv", dqdv,
                            "" if dqdv else (run_elab if not elab else "Run dQ/dV first (dQ/dV › Calculate).")))
    items.append(ExportItem("gitt_ici", "GITT / ICI", "Diffusion results (one file per branch)", "gitt-ici",
                            gitt_ready, "" if gitt_ready else (
                                run_elab if not elab else
                                "Calculate GITT / ICI results first: at least one accepted diffusion coefficient.")))
    return items


def free_path(folder: Path, stem: str, ext: str, taken: set[str] = frozenset()) -> Path:
    """``folder/stem.ext``, or ``stem (2).ext``, ``stem (3).ext``… when that file (or a GITT branch file derived
    from it, or a path already in ``taken``) exists: an export never overwrites a file."""
    n = 1
    while True:
        name = stem if n == 1 else f"{stem} ({n})"
        path = folder / f"{name}{ext}"
        related = [path, folder / f"{name} - charge{ext}", folder / f"{name} - discharge{ext}"]
        if not any(p.exists() or str(p) in taken for p in related):
            return path
        n += 1


@dataclass(frozen=True)
class ExportOutcome:
    """Result of one export: written file paths, or an error ('' when it succeeded); cancelled when stopped."""
    key: str
    title: str
    paths: tuple[str, ...] = ()
    error: str = ""
    cancelled: bool = False


class _Sig(QObject):
    progress = Signal(str)                 # text: which export is being written
    finished = Signal(object)              # list[ExportOutcome]


class _Call(QRunnable):
    """Run one function on the thread pool."""
    def __init__(self, fn: Callable[[], None]):
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        self.fn()


class ExportCenterController(QObject):
    """Runs the chosen exports of one dataset sequentially on a worker thread.

    Every export is prepared on the GUI thread (state is read only there) and written by the backend on the worker.
    One failure does not stop the others; each outcome is reported. Cancel stops the export being written (nothing
    is left of it) and skips the rest."""
    progress = Signal(str)
    finished = Signal(object)              # list[ExportOutcome]

    def __init__(self, state: SessionState, backend: Backend, units, gitt_ici):
        super().__init__()
        self.state, self.backend, self.units, self.gitt_ici = state, backend, units, gitt_ici
        self._event: Optional[threading.Event] = None
        self._token: Optional[gregorithm.CancellationToken] = None
        self._sig = _Sig()
        self._sig.progress.connect(self.progress)
        self._sig.finished.connect(self._done)

    def items(self, ds_id: Optional[str]) -> list[ExportItem]:
        """The Export Center rows for ``ds_id``."""
        ds = self.state.get(ds_id) if ds_id else None
        return export_items(ds, ds is not None and self.gitt_ici.can_export(ds_id))

    @property
    def running(self) -> bool:
        return self._event is not None

    def cancel(self) -> None:
        """Stop the running exports: the current file is removed, the remaining exports are skipped."""
        if self._event is not None:
            self._event.set()
        if self._token is not None:
            self._token.cancel()

    def run(self, ds_id: str, keys: list[str], fmt: str, folder: str) -> bool:
        """Start the exports ``keys`` of ``ds_id`` into ``folder`` as ``fmt`` ('csv' | 'parquet'). Returns False when
        nothing could start (already running, no dataset or nothing available among ``keys``)."""
        ds = self.state.get(ds_id)
        if self.running or ds is None:
            return False
        available = {i.key: i for i in self.items(ds_id) if i.available}
        chosen = [available[k] for k in keys if k in available]
        if not chosen:
            return False
        ext = ".csv" if fmt == "csv" else ".parquet"
        units = self.units.units_for(ds_id)
        taken: set[str] = set()
        jobs: list[tuple[ExportItem, Callable]] = []
        event, token = threading.Event(), gregorithm.CancellationToken()
        backend, quiet = self.backend, (lambda _p: None)
        for item in chosen:
            path = free_path(Path(folder), f"{ds.name} - {item.suffix}", ext, taken)
            taken.add(str(path))
            target = str(path)
            if item.key in ("elab_points", "elab_segments"):
                rid, kind = ds.elab.result.result_id, item.key.split("_")[1]
                jobs.append((item, lambda rid=rid, kind=kind, target=target:
                             backend.export_elab(rid, kind, None, fmt, target, quiet, event)))
            elif item.key == "capacity":
                rid, cols = ds.capacity.result.result_id, shown_columns(ds.capacity.result.params)
                jobs.append((item, lambda rid=rid, cols=cols, target=target:
                             backend.export_capacity(rid, None, cols, units, fmt, target, False, quiet, event)))
            elif item.key == "dcir":
                pid, cols = ds.dcir.published.result_id, [k for k, _t, _q in RESULT_COLUMNS]
                jobs.append((item, lambda pid=pid, cols=cols, target=target:
                             backend.export_dcir(pid, cols, units, fmt, target, quiet, event)))
            elif item.key == "dqdv":
                res = ds.dqdv.result
                cycles, cols = tuple(sorted(res.params.cycles)), [k for k, _t, _q in DQDV_COLUMNS]
                jobs.append((item, lambda rid=res.result_id, cycles=cycles, cols=cols, target=target:
                             backend.export_dqdv(rid, cycles, PHASES, cols, units, fmt, target, quiet, event)))
            elif item.key == "gitt_ici":
                files, inputs = self.gitt_ici.export_files(ds_id, target), self.gitt_ici.export_inputs(ds_id)
                if files is None or inputs is None:
                    continue
                for path_, _t, _r in files:
                    taken.add(path_)
                elab_id, snapshot, params = inputs
                jobs.append((item, lambda files=files, elab_id=elab_id, snapshot=snapshot, params=params:
                             backend.export_gitt_ici(ds_id, elab_id, snapshot, params, files, fmt, token)))
        if not jobs:
            return False
        self._event, self._token = event, token
        sig = self._sig

        def work():
            out: list[ExportOutcome] = []
            for n, (item, call) in enumerate(jobs, 1):
                if event.is_set() or token.is_cancelled():
                    out.append(ExportOutcome(item.key, item.title, cancelled=True))
                    continue
                sig.progress.emit(f"Exporting {n} of {len(jobs)}: {item.section} · {item.title}…")
                try:
                    receipts = call()
                    out.append(ExportOutcome(item.key, item.title, tuple(r.path for r in receipts)))
                except ExportCancelled:
                    out.append(ExportOutcome(item.key, item.title, cancelled=True))
                except gregorithm.OperationCancelled:
                    out.append(ExportOutcome(item.key, item.title, cancelled=True))
                except Exception as ex:                    # reported per export, never swallowed
                    out.append(ExportOutcome(item.key, item.title, error=str(ex) or type(ex).__name__))
            sig.finished.emit(out)

        QThreadPool.globalInstance().start(_Call(work))
        return True

    def _done(self, outcomes: list) -> None:
        self._event = self._token = None
        self.finished.emit(outcomes)
