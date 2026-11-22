"""Qt glue for Comparison. Decisions live in app/comparison.py (pure, tested). Reads, for every checked
dataset, the result its own section already published — Data Explorer points (compare_explorer, same request for all),
the Capacity table, the dQ/dV curves — in workers, in the APP units; stale responses are dropped; nothing is ever
analyzed from here."""
from __future__ import annotations
import threading
from typing import Optional
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from .backend import Backend, CompareRequest, FlatStyle, Progress, RenderCancelled
from .comparison import (CAPACITY, CORRECTED, CURRENT, DQDV, EXPLORER, NONE, NOT_ANALYZED, OUTDATED, RUNNING,
                         CompareState, can_draw, status_text)
from .dqdv import cycles_label
from .state import SessionState


class _Sig(QObject):
    explorer = Signal(object, object)          # key, list[CompareSeries]
    table = Signal(str, object, object)        # family, key, payload
    failed = Signal(str)
    flat = Signal(object, object)
    flatFailed = Signal(str)
    renderProgress = Signal(object)


class _Run(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        self.fn()


class ComparisonController(QObject):
    changed = Signal()
    dataReady = Signal()
    flatReady = Signal()
    notice = Signal(str)

    def __init__(self, state: SessionState, backend: Backend, units, elab, capacity, dqdv):
        super().__init__()
        self.state, self.backend, self.units = state, backend, units
        self.elab, self.capacity, self.dqdv = elab, capacity, dqdv
        self.cs = CompareState()
        self.series: Optional[list] = None              # Data Explorer family, current request
        self._xkey = None
        self.tables: dict[tuple, object] = {}           # (family, result_id, units) -> CapacityTable | list[DqdvCurveData]
        self._pending: set = set()
        self.flat = None
        self._flat_cancel: Optional[threading.Event] = None
        self._explorer_cancel: Optional[threading.Event] = None
        self.render_progress = None
        self.plot_px = 900
        self.active = False                              # Comparison shown: only then data is read
        self._sig = s = _Sig()
        s.explorer.connect(self._on_explorer)
        s.table.connect(self._on_table)
        s.failed.connect(self.notice)
        s.flat.connect(self._on_flat)
        s.flatFailed.connect(self._on_flat_failed)
        s.renderProgress.connect(self._on_render_progress)
        for c in (elab, capacity, dqdv):
            c.changed.connect(self._source_changed)
        units.changed.connect(lambda _ds: self._source_changed())
        units.appChanged.connect(self._source_changed)

    # ---------------------------------------------------------------- availability
    @property
    def app_units(self):
        return self.units.app_units

    def kind(self, ds_id: str, family: Optional[str] = None) -> tuple[str, str]:
        """(availability kind, extra text) of a dataset for a family."""
        family = family or self.cs.family
        d = self.state.get(ds_id)
        if d is None or d.elab.result is None:
            return (RUNNING if d is not None and d.elab.is_running else NONE), ""
        if family == EXPLORER:
            if d.elab.dirty:
                return OUTDATED, ""
            return (CORRECTED if d.elab.result.correction else CURRENT), ""
        if family == CAPACITY:
            c = d.capacity
            if c.result is None:
                return (RUNNING if c.is_running else NOT_ANALYZED), ""
            if c.modified(self.capacity.current_params(ds_id)):
                return OUTDATED, ""
            corrected = bool(d.elab.result.correction and c.result.elab_result_id == d.elab.result.result_id)
            return (CORRECTED if corrected else CURRENT), ""
        q = d.dqdv
        if q.result is None:
            return (RUNNING if q.is_running else NOT_ANALYZED), ""
        extra = f"cycles {cycles_label(q.result.params.cycles)}"
        if self.dqdv.modified(ds_id):
            return OUTDATED, extra
        return (CORRECTED if q.result.corrected else CURRENT), extra

    def kinds(self) -> dict:
        return {d.id: self.kind(d.id)[0] for d in self.state.datasets}

    def entries(self) -> list[tuple]:
        """(ds_id, name, checked, drawable, status text, colour) for the Datasets list."""
        out = []
        for d in self.state.datasets:
            k, extra = self.kind(d.id)
            out.append((d.id, d.name, d.id in self.cs.checked, can_draw(k), status_text(self.cs.family, k, extra),
                        self.cs.color(d.id), k))
        return out

    def drawn(self) -> list[str]:
        return self.cs.drawn(self.kinds())

    # ---------------------------------------------------------------- edits
    def edit(self, fn, reload: bool = True) -> None:
        fn(self.cs)
        if reload:
            self.load()
        self.changed.emit()

    def toggle(self, ds_id: str, on: bool) -> None:
        k, _ = self.kind(ds_id)
        if self.cs.toggle(ds_id, on, can_draw(k)):
            self.load()
            self.changed.emit()

    def set_family(self, family: str) -> None:
        self.edit(lambda cs: setattr(cs, "family", family))

    # ---------------------------------------------------------------- data (read only)
    def load(self) -> None:
        if not self.active:
            return
        self.cancel_render()
        if self._explorer_cancel is not None:
            self._explorer_cancel.set()
            self._explorer_cancel = None
        self.flat = None
        fam, units, ids = self.cs.family, self.app_units, self.drawn()
        if len(ids) < 2:
            return
        if fam == EXPLORER:
            v = self.cs.view
            items = tuple((i, self.state.get(i).elab.result.result_id) for i in ids)
            cycles = None
            if v.cycles:
                cycles = tuple(sorted({c for a, b in v.cycles for c in range(a, b + 1)}))   # literal IDs, same for all
            vp = v.viewport
            req = CompareRequest(items, v.x, v.y, cycles, (vp.x.lo, vp.x.hi) if vp.x else None,
                                 (vp.y1.lo, vp.y1.hi) if vp.y1 else None, units, self.plot_px)
            key = (req.items, req.x, req.y, req.cycles, req.x_range, req.y_range, units, req.plot_width_px)
            if key == self._xkey and self.series is not None:
                return
            self._xkey, self.series = key, None
            backend, sig = self.backend, self._sig
            ev = threading.Event()
            self._explorer_cancel = ev

            def job():
                try:
                    sig.explorer.emit(key, backend.compare_explorer(req, ev))
                except RenderCancelled:
                    pass
                except Exception as ex:
                    sig.failed.emit(f"Comparison: could not read the result points: {ex}")

            QThreadPool.globalInstance().start(_Run(job))
            return
        for i in ids:
            d = self.state.get(i)
            rid = d.capacity.result.result_id if fam == CAPACITY else d.dqdv.result.result_id
            key = (fam, rid, units)
            if key in self.tables or key in self._pending:
                continue
            self._pending.add(key)
            backend, sig = self.backend, self._sig

            def job(rid=rid, key=key):
                try:
                    data = backend.capacity_table(rid, units) if fam == CAPACITY else backend.dqdv_curves(rid, units)
                    sig.table.emit(fam, key, data)
                except Exception as ex:
                    sig.failed.emit(f"Comparison: could not read a {fam} result: {ex}")

            QThreadPool.globalInstance().start(_Run(job))

    def table_for(self, ds_id: str):
        d = self.state.get(ds_id)
        fam = self.cs.family
        if d is None:
            return None
        r = d.capacity.result if fam == CAPACITY else (d.dqdv.result if fam == DQDV else None)
        return self.tables.get((fam, r.result_id, self.app_units)) if r is not None else None

    def _on_explorer(self, key, series) -> None:
        if key != self._xkey:
            return
        self._explorer_cancel = None
        self.series = list(series)
        self.dataReady.emit()

    def _on_table(self, fam: str, key, data) -> None:
        self._pending.discard(key)
        if key[2] != self.app_units:
            return
        self.tables[key] = data
        self.dataReady.emit()

    def _source_changed(self, *_a) -> None:
        live = set()
        for d in self.state.datasets:
            if d.capacity.result:
                live.add((CAPACITY, d.capacity.result.result_id))
            if d.dqdv.result:
                live.add((DQDV, d.dqdv.result.result_id))
        self.tables = {k: v for k, v in self.tables.items() if (k[0], k[1]) in live}
        self.cs.checked = [i for i in self.cs.checked if self.state.get(i) is not None]
        self.load()                                      # keys contain the result ids: unchanged sources are not re-read
        self.changed.emit()

    def dataset_closed(self, ds_id: str) -> None:
        self.cs.dataset_closed(ds_id)
        self._source_changed()

    # ---------------------------------------------------------------- Render All Points (Data Explorer family)
    def render_all(self, labels: tuple[str, str], legend: bool, size: tuple[int, int], dpr: float,
                   ranges: Optional[tuple[tuple[float, float], tuple[float, float]]] = None) -> None:
        """Render every point of the checked datasets as a data layer of the plot area `size` (logical px) for the
        `ranges` (x, y) the chart shows now, so the layer fits under the chart's own axes."""
        ids = self.drawn()
        if self.cs.family != EXPLORER or len(ids) < 2 or self._flat_cancel is not None:
            return
        v = self.cs.view
        items = tuple((i, self.state.get(i).elab.result.result_id) for i in ids)
        cycles = tuple(sorted({c for a, b in v.cycles for c in range(a, b + 1)})) if v.cycles else None
        vp = v.viewport
        xr = ranges[0] if ranges else ((vp.x.lo, vp.x.hi) if vp.x else None)
        yr = ranges[1] if ranges else ((vp.y1.lo, vp.y1.hi) if vp.y1 else None)
        req = CompareRequest(items, v.x, v.y, cycles, xr, yr, self.app_units, size[0])
        series = {i: (self.cs.color(i), self.state.get(i).name) for i in ids}
        from .explorer import CHART
        style = FlatStyle(size[0], size[1], dpr, labels[0], labels[1], None, CHART["y1"], CHART["y2"], legend,
                          self.cs.style, dict(CHART), line_width=self.cs.line_width)
        ev = threading.Event()
        self._flat_cancel = ev
        self.render_progress = Progress(0.0, "Rendering all datasets", 0, None)
        key = self._xkey
        backend, sig = self.backend, self._sig

        def job():
            try:
                sig.flat.emit(key, backend.compare_render_all(req, series, style, sig.renderProgress.emit, ev))
            except RenderCancelled:
                sig.flatFailed.emit("")
            except Exception as ex:
                sig.flatFailed.emit(str(ex) or type(ex).__name__)

        QThreadPool.globalInstance().start(_Run(job))
        self.changed.emit()

    def cancel_render(self) -> None:
        if self._flat_cancel is not None:
            self._flat_cancel.set()

    @property
    def rendering(self) -> bool:
        return self._flat_cancel is not None

    def _on_flat(self, key, img) -> None:
        self._flat_cancel = None
        self.render_progress = None
        if key == self._xkey:
            self.flat = img
        self.flatReady.emit()
        self.changed.emit()

    def _on_flat_failed(self, msg: str) -> None:
        self._flat_cancel = None
        self.render_progress = None
        if msg:
            self.notice.emit(f"Render All Points failed: {msg}")
        self.flatReady.emit()
        self.changed.emit()

    def _on_render_progress(self, progress) -> None:
        if self._flat_cancel is not None:
            self.render_progress = progress
            self.changed.emit()

    def close(self) -> None:
        if self._explorer_cancel is not None:
            self._explorer_cancel.set()
        self.cancel_render()
