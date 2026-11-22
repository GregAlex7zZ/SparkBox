"""Qt glue for the Data Explorer. All decisions live in app/explorer.py (pure, tested); this class builds
the backend requests, runs them in QThreadPool workers (never on the GUI thread), discards stale responses by version,
debounces bursts, and moves results back to the GUI thread through queued signals."""
from __future__ import annotations
import threading
from typing import Callable, Optional
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal
from .backend import Backend, ExplorerRequest, ExplorerView, FlatStyle, Progress, RenderCancelled
from .explorer import (CHART, UPDATING_DELAY_MS, VIEW_DEBOUNCE_MS, ExplorerState, Mode, Phase, axis_label,
                       resolve_cycles)
from .state import SessionState
from .units_controller import UnitsController


class _Sig(QObject):
    view = Signal(str, int, object)          # ds_id, version, ExplorerView
    viewFailed = Signal(str, int, str)
    cycles = Signal(str, str, object)        # ds_id, result_id, list[int]
    progress = Signal(str, object)           # ds_id, Progress
    flat = Signal(str, object)               # ds_id, FlatImage
    flatCancelled = Signal(str)
    flatFailed = Signal(str, str)
    details = Signal(str, int, object)       # ds_id, source_row, dict


class _Run(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        self.fn()


class ExplorerController(QObject):
    changed = Signal(str)                    # state of this dataset changed: widgets re-read it
    viewReady = Signal(str)                  # new arrays to draw for this dataset
    flatReady = Signal(str)                  # flat image ready (or removed)
    detailsReady = Signal(str, int, object)  # hover metadata of one real point
    savedImage = Signal(str, str)            # ds_id, path (status-bar message)
    saveFailed = Signal(str, str)

    def __init__(self, state: SessionState, backend: Backend, units: UnitsController):
        super().__init__()
        self.state, self.backend, self.units = state, backend, units
        self.views: dict[str, ExplorerView] = {}
        self.flats: dict[str, bytes] = {}                 # ds_id -> Render All Points data layer (plot area only)
        # ds_id -> PNG of the whole chart with the data layer shown (set by the page); Save Image writes it
        self.composer: Optional[Callable[[str], Optional[bytes]]] = None
        self._cycles: dict[str, list[int]] = {}           # result_id -> available cycle IDs
        self._cancel: dict[str, threading.Event] = {}     # ds_id -> cancel event of the pending view request
        self._render_cancel: dict[str, threading.Event] = {}
        self._plot_px = (800, 480)
        self._slow: set[str] = set()                      # datasets whose request exceeded UPDATING_DELAY_MS
        self._sig = _Sig()
        self._sig.view.connect(self._on_view)
        self._sig.viewFailed.connect(self._on_view_failed)
        self._sig.cycles.connect(self._on_cycles)
        self._sig.progress.connect(self._on_progress)
        self._sig.flat.connect(self._on_flat)
        self._sig.flatCancelled.connect(self._on_flat_cancelled)
        self._sig.flatFailed.connect(self._on_flat_failed)
        self._sig.details.connect(self.detailsReady)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(VIEW_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._fire_pending)
        self._pending: set[str] = set()
        units.changed.connect(self._units_changed)

    # --- access ---
    def ex(self, ds_id: Optional[str]) -> Optional[ExplorerState]:
        d = self.state.get(ds_id) if ds_id else None
        return d.explorer if d else None

    def result_id(self, ds_id: str) -> Optional[str]:
        d = self.state.get(ds_id)
        return d.elab.result.result_id if d and d.elab.result else None

    def is_slow(self, ds_id: str) -> bool:
        return ds_id in self._slow

    def set_plot_size(self, w: int, h: int) -> None:
        if (w, h) != self._plot_px and w > 50 and h > 50:
            self._plot_px = (w, h)

    # --- user edits (GUI thread) ---
    def edit(self, ds_id: str, fn, reload: bool = True, debounce: bool = False) -> None:
        """Apply `fn(state) -> bool`; when it returns True and `reload`, request a new view."""
        e = self.ex(ds_id)
        if e is None:
            return
        need = fn(e)
        self.changed.emit(ds_id)
        if need and reload:
            self.request(ds_id, debounce=debounce)

    def request(self, ds_id: str, debounce: bool = False) -> None:
        if debounce:
            self._pending.add(ds_id)
            self._debounce.start()
            return
        self._start_view(ds_id)

    def ensure_view(self, ds_id: Optional[str]) -> None:
        """Called when the Explorer is shown for a dataset: load once if nothing was drawn yet."""
        e = self.ex(ds_id)
        if e is not None and not e.has_view and e.mode is Mode.INTERACTIVE and ds_id not in self._cancel:
            self._start_view(ds_id)

    def _fire_pending(self) -> None:
        for ds_id in list(self._pending):
            self._start_view(ds_id)
        self._pending.clear()

    def _make_request(self, ds_id: str, cycles) -> Optional[ExplorerRequest]:
        e, rid = self.ex(ds_id), self.result_id(ds_id)
        if e is None or rid is None:
            return None
        vp = e.viewport
        w, h = self._plot_px
        rng = lambda r: (r.lo, r.hi) if r else None
        return ExplorerRequest(rid, e.x, e.y1, e.y2, cycles, rng(vp.x), rng(vp.y1), rng(vp.y2) if e.y2 else None,
                               self.units.units_for(ds_id), w, h)

    def _start_view(self, ds_id: str) -> None:
        e, rid = self.ex(ds_id), self.result_id(ds_id)
        if e is None or rid is None or e.locked:
            return
        if old := self._cancel.pop(ds_id, None):
            old.set()                                         # the previous request is obsolete
        version = e.request()
        self._slow.discard(ds_id)
        QTimer.singleShot(UPDATING_DELAY_MS, lambda v=version: self._mark_slow(ds_id, v))
        self.changed.emit(ds_id)
        ev = threading.Event()
        self._cancel[ds_id] = ev
        cycles_known = self._cycles.get(rid)
        applied = e.applied
        req = self._make_request(ds_id, None)             # built on the GUI thread: the worker never reads the state
        backend, sig = self.backend, self._sig

        def job():
            try:
                avail = cycles_known if cycles_known is not None else backend.result_cycles(rid)
                if cycles_known is None:
                    sig.cycles.emit(ds_id, rid, avail)
                cyc = resolve_cycles(applied, avail)
                if cyc == ():                                 # no match: nothing to read
                    sig.view.emit(ds_id, version, None)
                    return
                if ev.is_set():
                    return
                req.cycles = cyc
                sig.view.emit(ds_id, version, backend.explorer_view(req, ev))
            except RenderCancelled:
                pass
            except Exception as ex:                           # shown verbatim
                sig.viewFailed.emit(ds_id, version, str(ex) or type(ex).__name__)

        QThreadPool.globalInstance().start(_Run(job))

    def _mark_slow(self, ds_id: str, version: int) -> None:
        e = self.ex(ds_id)
        if e is not None and e.version == version and e.phase in (Phase.UPDATING, Phase.LOADING):
            self._slow.add(ds_id)
            self.changed.emit(ds_id)

    # --- worker results (GUI thread) ---
    def _on_cycles(self, ds_id: str, rid: str, avail) -> None:
        self._cycles[rid] = list(avail)

    def available_cycles(self, ds_id: str) -> Optional[list[int]]:
        rid = self.result_id(ds_id)
        return self._cycles.get(rid) if rid else None

    def _on_view(self, ds_id: str, version: int, view: Optional[ExplorerView]) -> None:
        e = self.ex(ds_id)
        if e is None:
            return
        ok = e.accept(version, 0, 0, filter_matched=False) if view is None else \
            e.accept(version, view.rendered, view.total, view.filter_matched)
        if not ok:
            return                                            # stale: a newer request or a locked state
        self._cancel.pop(ds_id, None)
        self._slow.discard(ds_id)
        if view is not None:
            self.views[ds_id] = view
        else:
            self.views.pop(ds_id, None)
        self.viewReady.emit(ds_id)
        self.changed.emit(ds_id)

    def _on_view_failed(self, ds_id: str, version: int, msg: str) -> None:
        e = self.ex(ds_id)
        if e is not None and e.reject(version, msg):
            self._cancel.pop(ds_id, None)
            self._slow.discard(ds_id)
            self.changed.emit(ds_id)

    # --- Render All Points / Save Image ---
    def flat_style(self, ds_id: str, w: int, h: int, dpr: float) -> FlatStyle:
        e, u = self.ex(ds_id), self.units.units_for(ds_id)
        return FlatStyle(w, h, dpr, axis_label(e.x, u), axis_label(e.y1, u), axis_label(e.y2, u) if e.y2 else None,
                         e.y1_color, e.y2_color, e.legend, e.style, dict(CHART))

    def render(self, ds_id: str, size: tuple[int, int, float], save_path: Optional[str] = None) -> None:
        e = self.ex(ds_id)
        if e is None or not e.begin_render(save_path):
            return
        view = self.views.get(ds_id)
        if old := self._cancel.pop(ds_id, None):
            old.set()
        ev = threading.Event()
        self._render_cancel[ds_id] = ev
        self.changed.emit(ds_id)
        # the flat image uses EXACTLY the ranges currently shown (fitted ranges included)
        req = self._make_request(ds_id, resolve_cycles(e.applied, self._cycles.get(self.result_id(ds_id), [])))
        if view is not None:
            req.x_range, req.y1_range = view.x_range, view.y1_range
            req.y2_range = view.y2_range if e.y2 else None
        style = self.flat_style(ds_id, *size)
        backend, sig = self.backend, self._sig

        def job():
            try:
                img = backend.render_all_points(req, style, lambda p: sig.progress.emit(ds_id, p), ev)
                sig.flat.emit(ds_id, img)
            except RenderCancelled:
                sig.flatCancelled.emit(ds_id)
            except Exception as ex:
                sig.flatFailed.emit(ds_id, str(ex) or type(ex).__name__)

        QThreadPool.globalInstance().start(_Run(job))

    def cancel_render(self, ds_id: str) -> None:
        if ev := self._render_cancel.get(ds_id):
            ev.set()

    def leave_flat(self, ds_id: str) -> None:
        e = self.ex(ds_id)
        if e is not None and e.leave_flat():
            self.flats.pop(ds_id, None)
            self.flatReady.emit(ds_id)
            self.changed.emit(ds_id)

    def _on_progress(self, ds_id: str, p: Progress) -> None:
        e = self.ex(ds_id)
        if e is not None:
            e.render_progress(p.fraction, p.done, p.total)
            self.changed.emit(ds_id)

    def _on_flat(self, ds_id: str, img) -> None:
        self._render_cancel.pop(ds_id, None)
        e = self.ex(ds_id)
        if e is None:
            return
        path = e.save_path
        if not e.render_finished(img.drawn, img.total):
            self.changed.emit(ds_id)
            return
        self.flats[ds_id] = img.png
        self.flatReady.emit(ds_id)                         # the chart shows the layer first ...
        if path:
            self.write_png(ds_id, path)                    # ... so the saved image is the composed chart
        self.changed.emit(ds_id)

    def write_png(self, ds_id: str, path: str) -> None:
        """Write the chart with every point (axes, legend and data layer at 300 dpi) to `path`."""
        if ds_id not in self.flats:
            return
        png = self.composer(ds_id) if self.composer is not None else None
        if png is None:
            self.saveFailed.emit(ds_id, "The chart is not shown, so the image could not be composed.")
            return
        try:
            with open(path, "wb") as f:
                f.write(png)
            self.savedImage.emit(ds_id, path)
        except OSError as ex:
            self.saveFailed.emit(ds_id, str(ex))

    def _on_flat_cancelled(self, ds_id: str) -> None:
        self._render_cancel.pop(ds_id, None)
        if (e := self.ex(ds_id)) is not None:
            e.render_cancelled()
            self.changed.emit(ds_id)

    def _on_flat_failed(self, ds_id: str, msg: str) -> None:
        self._render_cancel.pop(ds_id, None)
        if (e := self.ex(ds_id)) is not None:
            e.render_failed(msg)
            self.changed.emit(ds_id)

    # --- hover details ---
    def request_details(self, ds_id: str, source_row: int) -> None:
        rid = self.result_id(ds_id)
        if rid is None or source_row < 0:
            return
        units, backend, sig = self.units.units_for(ds_id), self.backend, self._sig

        def job():
            try:
                sig.details.emit(ds_id, source_row, backend.point_details(rid, source_row, units))
            except Exception:
                pass                                          # the readout keeps the values it already shows

        QThreadPool.globalInstance().start(_Run(job))

    # --- external events ---
    def result_replaced(self, ds_id: str) -> None:
        if (e := self.ex(ds_id)) is None:
            return
        self.cancel_render(ds_id)
        e.result_replaced()
        self.views.pop(ds_id, None)
        self.flats.pop(ds_id, None)
        self.flatReady.emit(ds_id)
        self.viewReady.emit(ds_id)
        self._start_view(ds_id)

    def _units_changed(self, ds_id: str) -> None:
        if (e := self.ex(ds_id)) is None:
            return
        self.cancel_render(ds_id)
        e.units_changed()
        self.views.pop(ds_id, None)
        self.flats.pop(ds_id, None)
        self.flatReady.emit(ds_id)
        self.viewReady.emit(ds_id)
        if self.result_id(ds_id):
            self._start_view(ds_id)

    def cancel_all(self) -> None:
        """Application closing: stop every running data load and render, without updating the views."""
        for event in (*self._cancel.values(), *self._render_cancel.values()):
            event.set()

    def dataset_closed(self, ds_id: str) -> None:
        for d in (self._cancel, self._render_cancel):
            if ev := d.pop(ds_id, None):
                ev.set()
        self.views.pop(ds_id, None)
        self.flats.pop(ds_id, None)
        self._slow.discard(ds_id)
        self._pending.discard(ds_id)
