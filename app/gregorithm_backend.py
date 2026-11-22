"""Production GUI adapter for the public :mod:`gregorithm` import API.

No scientific rule is implemented here.  This module translates GUI choices to
the public, name-based contracts and presents immutable canonical Parquet output
in the GUI's display units using bounded row-group reads.
"""
from __future__ import annotations

import csv
import dataclasses
import hashlib
import math
import os
import uuid
from collections import OrderedDict
from pathlib import Path
from threading import RLock

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pyarrow.compute as pc
import gregorithm
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF
from PySide6.QtGui import QColor, QPen, QPolygonF

from .backend import (AmbiguousMapping, CapacityCancelled, CapacityTable, ColumnMapping, CompareRequest, CompareSeries,
                      Curve, DcirCancelled, DqdvCancelled, DqdvCurveData, DqdvSegment, ReconCurveData,
                      CorrectionCancelled, PulseTrace, ElabCancelled, ElabColumn, ElabTable,
                      ExportCancelled, ExportDone,
                      ExplorerRequest, ExplorerView, FlatImage, FlatStyle, ImportCancelled, ImportResult, Progress,
                      RenderCancelled, ReportRow, SourceInspection)
from .elaboration import CHARGE_FIRST, ElabParams, ElabResult, SampleValues
from .capacity import CapParams, CapResult
from .dcir import (RESULT_COLUMNS, DetectionParams, DetectionResult, EventView, MeasurementView, PublishedResult,
                   result_heads, result_rows, split_event_times)
from .correction import CorrParams, CorrResult, Span
from .dqdv import DqdvParams, DqdvResult
from .gitt_ici import (SERIES as GITT_SERIES, GittIciParams, GittIciScan, ImpulseTrace,
                       SegmentTrace as GittSegmentTrace)
from .flat_render import data_layer, dot_pen
from .units import CAPACITY, CURRENT, ENERGY, RESISTANCE, TIME, VOLTAGE, UnitPrefs, scale


_COLUMN_QUANTITY = {
    "time_s": TIME, "current_a": CURRENT, "voltage_v": VOLTAGE,
    "charge_capacity_ah": CAPACITY, "discharge_capacity_ah": CAPACITY,
    "charge_energy_wh": ENERGY, "discharge_energy_wh": ENERGY,
    "resistance_ohm": RESISTANCE,
}


# Display units used when a caller passes none. UnitPrefs is a frozen dataclass, so one shared instance is safe.
DEFAULT_UNITS = UnitPrefs()


def _factor(column: str, units: UnitPrefs) -> float:
    quantity = _COLUMN_QUANTITY.get(column)
    return scale(quantity, units.unit(quantity)) if quantity else 1.0


def _numeric(array: pa.ChunkedArray | pa.Array) -> np.ndarray:
    """Arrow numeric/bool values as float, preserving nulls as NaN."""
    if isinstance(array, pa.ChunkedArray):
        array = array.combine_chunks()
    if array.null_count:
        return np.asarray(array.to_pylist(), dtype=float)
    return np.asarray(array.to_numpy(zero_copy_only=False), dtype=float)


def _text_options(path: Path) -> gregorithm.TextOptions:
    """Boundedly detect a text delimiter; gregorithm remains the actual parser."""
    with path.open("r", encoding="utf-8", newline="") as stream:
        sample = stream.read(64 * 1024)
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = ","
    return gregorithm.TextOptions(delimiter=delimiter, encoding="utf-8")


def _options(path: Path) -> gregorithm.TextOptions:
    return _text_options(path) if path.suffix.lower() in {".csv", ".txt"} else gregorithm.TextOptions()



def _file_signature(path: Path) -> tuple[int, int]:
    """Size and modification time [ns] of a file: changes when the file is rewritten."""
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns

def _dqdv_float(table: pa.Table, name: str) -> np.ndarray:
    """A required float column of the dQ/dV table; a missing value is an error, never a silent NaN."""
    column = table[name]
    if column.null_count:
        raise ValueError(f"The dQ/dV table has missing values in {name}")
    return np.asarray(column.to_numpy(), dtype=float)


def _dqdv_curves(table: pa.Table, voltage_factor: float, capacity_factor: float) -> list[DqdvCurveData]:
    """Group the rows of gregorithm's dQ/dV table into curves and segments, scaled to display units.

    A curve is (cycle, half_cycle, phase, anchor_source_row); inside it a segment is ``segment_index``. Curves and
    segments keep the order of their first row in the table and rows keep table order, including rows of one curve
    that are not contiguous. Smoothed values are None for a segment without any, otherwise NaN where missing. Values
    are only multiplied by the unit factors (float64), exactly as the former row-by-row reading did; the grouping is
    done with NumPy so the cost no longer grows with a Python object per point (regression test:
    tests/test_gregorithm_backend.py::test_dqdv_curves_match_row_by_row_reference)."""
    n = table.num_rows
    if n == 0:
        return []
    cycle = table["cycle"].to_numpy().astype(np.int64)
    half = table["half_cycle"].to_numpy().astype(np.int64)
    anchor = table["anchor_source_row"].to_numpy().astype(np.int64)
    segment = table["segment_index"].to_numpy().astype(np.int64)
    phases = pc.dictionary_encode(table["phase"].combine_chunks())
    phase_code = phases.indices.to_numpy().astype(np.int64)
    phase_names = phases.dictionary.to_pylist()
    rows = np.arange(n)
    _keys, curve_id = np.unique(np.stack([cycle, half, phase_code, anchor], axis=1), axis=0, return_inverse=True)
    curve_id = curve_id.ravel()
    curve_first = np.full(int(curve_id.max()) + 1, n)
    np.minimum.at(curve_first, curve_id, rows)
    _keys, seg_id = np.unique(np.stack([curve_id, segment], axis=1), axis=0, return_inverse=True)
    seg_id = seg_id.ravel()
    seg_first = np.full(int(seg_id.max()) + 1, n)
    np.minimum.at(seg_first, seg_id, rows)
    order = np.lexsort((rows, seg_first[seg_id], curve_first[curve_id]))
    voltage = _dqdv_float(table, "voltage_mid_v")[order] * voltage_factor
    raw = _dqdv_float(table, "dqdv_raw_ah_per_v")[order] * capacity_factor
    smoothed_column = table["dqdv_smoothed_ah_per_v"]
    missing = np.asarray(smoothed_column.is_null().to_numpy(zero_copy_only=False), dtype=bool)[order]
    smoothed = np.asarray(smoothed_column.fill_null(0.0).to_numpy(), dtype=float)[order] * capacity_factor
    smoothed[missing] = np.nan
    cuts = np.flatnonzero(np.diff(seg_id[order])) + 1
    out: list[DqdvCurveData] = []
    last_curve = None
    for start, end in zip(np.concatenate(([0], cuts)).tolist(), np.concatenate((cuts, [n])).tolist()):
        row = order[start]
        if curve_id[row] != last_curve:
            last_curve = curve_id[row]
            out.append(DqdvCurveData(int(cycle[row]), int(half[row]), str(phase_names[phase_code[row]]),
                                     int(anchor[row]), []))
        out[-1].segments.append(DqdvSegment(int(segment[row]), voltage[start:end].copy(), raw[start:end].copy(),
                                            None if missing[start:end].all() else smoothed[start:end].copy()))
    return out


class _CanonicalView:
    """Small LRU of canonical Parquet row groups; display scaling is applied on read."""
    def __init__(self, path: Path, max_groups: int = 2):
        self.path = path
        self.parquet = pq.ParquetFile(path)
        self.total = self.parquet.metadata.num_rows
        self._offsets: list[int] = [0]
        for i in range(self.parquet.num_row_groups):
            self._offsets.append(self._offsets[-1] + self.parquet.metadata.row_group(i).num_rows)
        self._cache: OrderedDict[int, np.ndarray] = OrderedDict()
        self._max_groups = max_groups
        self._lock = RLock()

    def _group(self, index: int) -> np.ndarray:
        with self._lock:
            hit = self._cache.pop(index, None)
            if hit is None:
                table = self.parquet.read_row_group(index, columns=["time_s", "current_a", "voltage_v"])
                hit = np.column_stack([table.column(i).to_numpy(zero_copy_only=False) for i in range(3)])
                self._cache[index] = hit
                while len(self._cache) > self._max_groups:
                    self._cache.popitem(last=False)
            else:
                self._cache[index] = hit
            return hit

    def rows(self, start: int, count: int, units: UnitPrefs = DEFAULT_UNITS) -> list[list[float]]:
        if start < 0 or count < 0:
            raise ValueError("Row start and count must not be negative")
        stop = min(self.total, start + count)
        out: list[list[float]] = []
        while start < stop:
            group = int(np.searchsorted(self._offsets, start, side="right") - 1)
            data = self._group(group)
            local = start - self._offsets[group]
            take = min(stop - start, len(data) - local)
            shown = data[local:local + take].copy()
            shown[:, 0] *= _factor("time_s", units)
            shown[:, 1] *= _factor("current_a", units)
            shown[:, 2] *= _factor("voltage_v", units)
            out.extend(shown.tolist())
            start += take
        return out

    def find_time(self, displayed: float, units: UnitPrefs = DEFAULT_UNITS) -> tuple[int, bool, float]:
        if not np.isfinite(displayed) or self.total == 0:
            raise ValueError("Time must be a finite number")
        factor = _factor("time_s", units)
        target = displayed / factor
        lo, hi = 0, self.parquet.num_row_groups - 1
        candidate = 0
        while lo <= hi:
            mid = (lo + hi) // 2
            data = self._group(mid)
            if target < data[0, 0]:
                hi = mid - 1
            elif target > data[-1, 0]:
                candidate = mid
                lo = mid + 1
            else:
                candidate = mid
                break
        candidates: list[tuple[float, int, float]] = []
        for group in {max(0, candidate - 1), candidate, min(self.parquet.num_row_groups - 1, candidate + 1)}:
            data = self._group(group)[:, 0]
            pos = int(np.searchsorted(data, target))
            for local in {max(0, pos - 1), min(len(data) - 1, pos)}:
                value = float(data[local])
                candidates.append((abs(value - target), self._offsets[group] + local, value))
        _, row, value = min(candidates)
        return row, value == target, value * factor

    def close(self) -> None:
        self._cache.clear()
        self.parquet.close()


_POINT_COLUMNS = (
    ElabColumn("Time", "h", "float", True), ElabColumn("Current", "mA"),
    ElabColumn("Voltage", "V"), ElabColumn("Charge Capacity", "Ah"),
    ElabColumn("Discharge Capacity", "Ah"), ElabColumn("Charge Energy", "Wh"),
    ElabColumn("Discharge Energy", "Wh"), ElabColumn("Cycle", "", "int"),
    ElabColumn("Half-Cycle", "", "int"), ElabColumn("Rest", "", "bool"),
    ElabColumn("Step", "", "int"), ElabColumn("Source Row", "", "int"),
)
_POINT_SOURCE = ("time_s", "current_a", "voltage_v", "charge_capacity_ah",
                 "discharge_capacity_ah", "charge_energy_wh", "discharge_energy_wh",
                 "cycle", "half_cycle", "rest", "step", "source_row")
_SEGMENT_COLUMNS = (
    ElabColumn("Segment ID", "", "int"), ElabColumn("First Time", "h", "float", True),
    ElabColumn("First Row", "", "int"), ElabColumn("Last Row", "", "int"),
    ElabColumn("Last Time", "h", "float", True), ElabColumn("Cycle", "", "int"),
    ElabColumn("Half-Cycle", "", "int"), ElabColumn("Step", "", "int"),
    ElabColumn("S4 Portion", "", "int"), ElabColumn("Polarity", "", "int"),
)
_SEGMENT_SOURCE = ("segment_id", "first_time_s", "first_row", "last_row", "last_time_s",
                   "cycle", "half_cycle", "step", "s4_portion", "polarity")


class _ElabView:
    """Bounded row-group access to one published elaboration generation."""
    def __init__(self, result_path: Path):
        self.result_path = result_path
        self.files = {
            "points": result_path / "points" / "elaborated.parquet",
            "segments": result_path / "indexes" / "segments.parquet",
        }
        self.parquets = {kind: pq.ParquetFile(path) for kind, path in self.files.items()}
        self._offsets: dict[str, list[int]] = {}
        for kind, parquet in self.parquets.items():
            offsets = [0]
            for i in range(parquet.num_row_groups):
                offsets.append(offsets[-1] + parquet.metadata.row_group(i).num_rows)
            self._offsets[kind] = offsets
        self._cache: OrderedDict[tuple[str, int], pa.Table] = OrderedDict()
        self._lock = RLock()

    @staticmethod
    def columns(kind: str) -> tuple[ElabColumn, ...]:
        if kind == "points":
            return _POINT_COLUMNS
        if kind == "segments":
            return _SEGMENT_COLUMNS
        raise ValueError("Table kind must be 'points' or 'segments'")

    @staticmethod
    def source_columns(kind: str) -> tuple[str, ...]:
        return _POINT_SOURCE if kind == "points" else _SEGMENT_SOURCE

    def total(self, kind: str) -> int:
        return self.parquets[kind].metadata.num_rows

    def _group(self, kind: str, index: int) -> pa.Table:
        """One row group of ``kind`` as an Arrow table (the displayed columns only), kept in a small LRU cache.

        The cache holds Arrow tables, not Python rows: converting a whole row group (up to 65,536 rows) to Python
        lists took 1-1.7 s on the GUI thread whenever scrolling entered a new group and used ~25 MB per group. Only
        the rows actually shown are converted, in rows()."""
        key = (kind, index)
        with self._lock:
            hit = self._cache.pop(key, None)
            if hit is None:
                hit = self.parquets[kind].read_row_group(index, columns=list(self.source_columns(kind)))
            self._cache[key] = hit
            while len(self._cache) > 4:
                self._cache.popitem(last=False)
            return hit

    def _times(self, index: int) -> np.ndarray:
        """Time column [s] of one points row group, for the binary search of find_time()."""
        return np.asarray(self._group("points", index).column(0).to_numpy(zero_copy_only=False), dtype=float)

    def rows(self, kind: str, start: int, count: int, units: UnitPrefs = DEFAULT_UNITS) -> list[list]:
        total = self.total(kind)
        if start < 0 or count < 0:
            raise ValueError("Row start and count must not be negative")
        stop = min(total, start + count)
        out: list[list] = []
        offsets = self._offsets[kind]
        while start < stop:
            group = int(np.searchsorted(offsets, start, side="right") - 1)
            data = self._group(kind, group)
            local = start - offsets[group]
            take = min(stop - start, data.num_rows - local)
            names = self.source_columns(kind)
            block = [[row[name] for name in names] for row in data.slice(local, take).to_pylist()]
            for row in block:
                for col, name in enumerate(names):
                    if row[col] is not None:
                        row[col] = row[col] * _factor(name, units)
            out.extend(block)
            start += take
        return out

    def find_time(self, displayed: float, units: UnitPrefs = DEFAULT_UNITS) -> tuple[int, bool, float]:
        if not np.isfinite(displayed) or self.total("points") == 0:
            raise ValueError("Time must be a finite number")
        factor = _factor("time_s", units)
        hours = displayed / factor
        parquet = self.parquets["points"]
        offsets = self._offsets["points"]
        lo, hi, candidate = 0, parquet.num_row_groups - 1, 0
        while lo <= hi:
            mid = (lo + hi) // 2
            values = self._times(mid)
            if hours < values[0]:
                hi = mid - 1
            elif hours > values[-1]:
                candidate, lo = mid, mid + 1
            else:
                candidate = mid
                break
        choices: list[tuple[float, int, float]] = []
        for group in {max(0, candidate - 1), candidate, min(parquet.num_row_groups - 1, candidate + 1)}:
            values = self._times(group)
            pos = int(np.searchsorted(values, hours, side="left"))
            for local in {max(0, pos - 1), min(len(values) - 1, pos)}:
                value = float(values[local])
                choices.append((abs(value - hours), offsets[group] + local, value))
        _, row, value = min(choices)
        return row, value == hours, value * factor

    def close(self) -> None:
        self._cache.clear()
        for parquet in self.parquets.values():
            parquet.close()


def _write_columns(destination: Path, fmt: str, columns: dict, cancel) -> ExportDone:
    """Write ``columns`` (title -> list of values, None = empty cell) as CSV or Parquet to ``destination``, through
    a temporary file in the same folder. Never overwrites an existing file; nothing is left behind on failure or
    cancellation (``cancel``: an object with is_set())."""
    if destination.exists():
        raise FileExistsError(f"The destination already exists: {destination}")
    if fmt.lower() not in ("csv", "parquet"):
        raise ValueError("Export format must be CSV or Parquet")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + f".{uuid.uuid4().hex}.tmp")
    table = pa.table({title: pa.array(values) for title, values in columns.items()})
    try:
        if cancel.is_set():
            raise ExportCancelled()
        if fmt.lower() == "parquet":
            pq.write_table(table, temporary)
        else:
            import pyarrow.csv as pacsv
            pacsv.write_csv(table, temporary)
        if cancel.is_set():
            raise ExportCancelled()
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return ExportDone(str(destination), table.num_rows, destination.stat().st_size,
                      hashlib.sha256(destination.read_bytes()).hexdigest())


class GregorithmBackend:
    """Adapter used by normal application mode; never supplies synthetic data."""
    def __init__(self, caller_root: Path):
        self.caller_root = Path(caller_root)
        self.caller_root.mkdir(parents=True, exist_ok=True)
        self._views: dict[str, _CanonicalView] = {}
        self._imports: dict[str, ImportResult] = {}
        self._elab_views: dict[str, _ElabView] = {}
        self._elab_params: dict[str, ElabParams] = {}
        self._cycles_cache: dict[str, tuple[int, ...]] = {}  # elaboration result -> distinct cycles (immutable)
        self._capacity_paths: dict[str, Path] = {}
        self._capacity_ds: dict[str, str] = {}
        self._dqdv_paths: dict[str, Path] = {}
        self._dqdv_ds: dict[str, str] = {}
        self._dqdv_elaboration: dict[str, str] = {}
        self._dqdv_params: dict[str, DqdvParams] = {}
        self._dqdv_dcir_fingerprint: dict[str, str] = {}
        self._dqdv_span_count: dict[str, int] = {}
        self._dqdv_metrics: dict[str, dict[int, tuple]] = {}    # result -> anchor_source_row -> (APE %, RMSE V, code)
        self._dcir_paths: dict[str, Path] = {}
        self._dcir_events: dict[str, tuple] = {}
        self._dcir_measurements: dict[str, tuple] = {}
        self._dcir_elaboration: dict[str, str] = {}
        self._result_ds: dict[str, str] = {}
        self._gitt_scopes: dict[str, tuple] = {}     # scope_id -> (ds, events, {event_id: EventData})
        self._units: dict[str, UnitPrefs] = {}
        self._samples: dict[str, SampleValues] = {}
        # path -> (file signature, inspection, core inspection, text options); see _file_signature
        self._inspections: dict[Path, tuple[tuple[int, int], SourceInspection, object, gregorithm.TextOptions]] = {}
        self._lock = RLock()

    def inspect_source(self, path: str, has_header: bool | None = None) -> SourceInspection:
        if has_header is False:
            raise ValueError("Files without a header row are not supported")
        source = Path(path).resolve(strict=True)
        options = _options(source)
        raw = gregorithm.inspect_source(source, preview_rows=8, text_options=options)
        suggested = None
        if raw.suggested_mapping:
            m = raw.suggested_mapping
            suggested = ColumnMapping(raw.columns.index(m.time_column), raw.columns.index(m.current_column),
                                      raw.columns.index(m.voltage_column),
                                      "hh:mm:ss.fff" if m.time_format is gregorithm.TimeFormat.DURATION else m.time_unit,
                                      m.current_unit, m.voltage_unit, True)
        inspection = SourceInspection(list(raw.columns), [["" if v is None else v for v in row] for row in raw.preview],
                                      delimiter=options.delimiter, encoding=options.encoding.upper(), has_header=True,
                                      file_format=raw.kind.value.upper(),
                                      ambiguity_reason=None if raw.mapping_confirmed else
                                      "Auto Mapping could not identify one unambiguous Time, Current and Voltage mapping.",
                                      suggested=suggested)
        with self._lock:
            self._inspections[source] = (_file_signature(source), inspection, raw, options)
        return inspection

    @staticmethod
    def _core_mapping(inspection: SourceInspection, mapping: ColumnMapping) -> gregorithm.ColumnMapping:
        indices = (mapping.time_col, mapping.current_col, mapping.voltage_col)
        if len(set(indices)) != 3 or any(i < 0 or i >= len(inspection.columns) for i in indices):
            raise ValueError("Three distinct existing columns are required")
        duration = mapping.time_unit == "hh:mm:ss.fff"
        return gregorithm.ColumnMapping(inspection.columns[mapping.time_col], inspection.columns[mapping.current_col],
                                      inspection.columns[mapping.voltage_col], "s" if duration else mapping.time_unit,
                                      mapping.current_unit, mapping.voltage_unit,
                                      gregorithm.TimeFormat.DURATION if duration else gregorithm.TimeFormat.NUMERIC)

    def _inspection(self, path: str) -> tuple[SourceInspection, object, gregorithm.TextOptions]:
        """The inspection of ``path``, reused only while the file is unchanged on disk.

        A file edited and imported again in the same session is inspected again, so its columns, mapping and text
        options are never those of the earlier content."""
        source = Path(path).resolve(strict=True)
        with self._lock:
            cached = self._inspections.get(source)
        if cached is None or cached[0] != _file_signature(source):
            self.inspect_source(str(source))
            with self._lock:
                cached = self._inspections[source]
        return cached[1:]

    def import_auto(self, path: str, ds_key: str, progress, cancel) -> ImportResult:
        inspection, raw, _ = self._inspection(path)
        mapping = raw.suggested_mapping
        if mapping is None:
            raise AmbiguousMapping(inspection)
        return self._import(path, ds_key, mapping, inspection, "Auto Mapping", progress, cancel)

    def import_mapped(self, path: str, mapping: ColumnMapping, progress, cancel, ds_key: str) -> ImportResult:
        inspection, _, _ = self._inspection(path)
        return self._import(path, ds_key, self._core_mapping(inspection, mapping), inspection,
                            "Manual Mapping", progress, cancel)

    def _import(self, path: str, ds_key: str, mapping, inspection, method: str, progress, cancel) -> ImportResult:
        source = Path(path).resolve(strict=True)
        _, _, options = self._inspection(path)
        request = gregorithm.ImportRequest(source=source, caller_root=self.caller_root, sample_id=ds_key,
                                         job_id=uuid.uuid4().hex, generation_id=uuid.uuid4().hex,
                                         mapping=mapping, text_options=options)
        def on_progress(event):
            progress(Progress(None if event.total is None else event.completed / max(1, event.total),
                              event.phase.replace("_", " ").title(), event.completed, event.total))
        try:
            core = gregorithm.import_source(request, progress=on_progress, cancel=cancel)
        except gregorithm.OperationCancelled as exc:
            raise ImportCancelled() from exc
        canonical = core.ref.path / "points" / "canonical.parquet"
        view = _CanonicalView(canonical)
        first = view.rows(0, 1)[0][0]
        last = view.rows(view.total - 1, 1)[0][0]
        used = {mapping.time_column: ("Time", f"{'hh:mm:ss.fff' if mapping.time_format is gregorithm.TimeFormat.DURATION else mapping.time_unit} -> h"),
                mapping.current_column: ("Current", f"{mapping.current_unit} -> mA"),
                mapping.voltage_column: ("Voltage", f"{mapping.voltage_unit} -> V")}
        report = [ReportRow(name, used[name][0], used[name][1], True) if name in used
                  else ReportRow(name, "Not used", "—", False) for name in inspection.columns]
        diagnostics = " ".join(d.message for d in core.report.diagnostics) or "No rows were discarded."
        result = ImportResult(core.report.retained_rows,
                              core.report.removed_invalid_measurement + core.report.removed_duplicate_time,
                              (last - first) * 3600.0, method, report, diagnostics,
                              [("Time", "h"), ("Current", "mA"), ("Voltage", "V")], canonical,
                              core.report.content_hash, core.ref.generation_id)
        with self._lock:
            old = self._views.get(ds_key)
            self._views[ds_key] = view
            self._imports[ds_key] = result
        if old is not None:
            old.close()
        return result

    def get_rows(self, ds_key: str, start: int, count: int) -> list[list[float]]:
        with self._lock:
            view = self._views.get(ds_key)
        if view is None:
            raise KeyError("Dataset is no longer available")
        return view.rows(start, count, self._units.get(ds_key, UnitPrefs()))

    def find_row_by_time(self, ds_key: str, value: float) -> tuple[int, bool, float]:
        with self._lock:
            view = self._views.get(ds_key)
        if view is None:
            raise KeyError("Dataset is no longer available")
        return view.find_time(value, self._units.get(ds_key, UnitPrefs()))

    def release(self, ds_key: str) -> None:
        with self._lock:
            view = self._views.pop(ds_key, None)
            self._imports.pop(ds_key, None)
            self._units.pop(ds_key, None)
            self._samples.pop(ds_key, None)
        if view is not None:
            view.close()

    def close(self) -> None:
        with self._lock:
            views = list(self._views.values())
            self._views.clear()
            elab_views = list(self._elab_views.values())
            self._elab_views.clear()
            self._elab_params.clear()
            self._cycles_cache.clear()
            self._capacity_paths.clear()
            self._capacity_ds.clear()
            self._dcir_paths.clear()
            self._dcir_events.clear()
            self._dcir_measurements.clear()
            self._dcir_elaboration.clear()
        for view in views:
            view.close()
        for view in elab_views:
            view.close()

    def elaborate(self, ds_key: str, params: ElabParams, progress, cancel) -> ElabResult:
        with self._lock:
            imported = self._imports.get(ds_key)
        if imported is None or imported.canonical_path is None or not imported.canonical_content_hash:
            raise KeyError("The canonical import generation is not available")
        core_params = gregorithm.ElaborationParameters(
            rest_current_threshold_a=params.rest_current_threshold_ma / 1000.0,
            cycle_order=(gregorithm.CycleOrder.CHARGE_DISCHARGE if params.cycle_order == CHARGE_FIRST
                         else gregorithm.CycleOrder.DISCHARGE_CHARGE),
            cc_relative_tolerance=params.cc_relative_tolerance_pct / 100.0,
            cv_absolute_tolerance_v=params.cv_absolute_tolerance_mv / 1000.0,
        )
        request = gregorithm.ElaborationRequest(
            canonical_parquet=imported.canonical_path,
            canonical_content_hash=imported.canonical_content_hash,
            caller_root=self.caller_root,
            sample_id=f"{ds_key}.elaboration",
            job_id=uuid.uuid4().hex,
            generation_id=uuid.uuid4().hex,
            parameters=core_params,
        )
        def on_progress(event):
            publishing = event.total is not None and event.completed >= event.total
            progress(Progress(None if event.total is None else event.completed / max(1, event.total),
                              "Saving result…" if publishing else event.phase.replace("_", " ").title(),
                              event.completed, event.total, publishing))
        try:
            core = gregorithm.elaborate(request, progress=on_progress, cancel=cancel)
        except gregorithm.OperationCancelled as exc:
            raise ElabCancelled() from exc
        view = _ElabView(core.ref.path)
        result_id = core.ref.generation_id
        with self._lock:
            self._elab_views[result_id] = view
            self._result_ds[result_id] = ds_key
            self._elab_params[result_id] = params
        self.result_cycles(result_id)            # warm the cache here, on the worker thread, not on the GUI thread
        return ElabResult(result_id, core.cycles, core.half_cycles, view.total("segments"), core.rows, params,
                          details=(("Result fingerprint", core.fingerprint),))

    def elab_table(self, result_id: str, kind: str) -> ElabTable:
        with self._lock:
            view = self._elab_views.get(result_id)
        if view is None:
            raise KeyError("Elaboration result is no longer available")
        ds_key = self._result_ds.get(result_id, "")
        units = self._units.get(ds_key, UnitPrefs())
        columns = []
        for source, column in zip(view.source_columns(kind), view.columns(kind)):
            quantity = _COLUMN_QUANTITY.get(source)
            columns.append(ElabColumn(column.name, units.unit(quantity) if quantity else column.unit,
                                      column.kind, column.is_time))
        return ElabTable(columns, view.total(kind), row_number_column=(kind == "points"))

    def get_elab_rows(self, result_id: str, kind: str, start: int, count: int) -> list[list]:
        with self._lock:
            view = self._elab_views.get(result_id)
        if view is None:
            raise KeyError("Elaboration result is no longer available")
        ds_key = self._result_ds.get(result_id, "")
        return view.rows(kind, start, count, self._units.get(ds_key, UnitPrefs()))

    def find_elab_row_by_time(self, result_id: str, value: float) -> tuple[int, bool, float]:
        with self._lock:
            view = self._elab_views.get(result_id)
        if view is None:
            raise KeyError("Elaboration result is no longer available")
        ds_key = self._result_ds.get(result_id, "")
        return view.find_time(value, self._units.get(ds_key, UnitPrefs()))

    def release_result(self, result_id: str) -> None:
        with self._lock:
            view = self._elab_views.pop(result_id, None)
            self._cycles_cache.pop(result_id, None)
            self._result_ds.pop(result_id, None)
            self._elab_params.pop(result_id, None)
        if view is not None:
            view.close()

    # --- GITT/ICI: orchestration only; every scientific value comes from gregorithm.gitt_ici. ---
    def scan_gitt_ici(self, ds_key: str, elab_result_id: str, params: GittIciParams) -> GittIciScan:
        """Recognise the impulses of the explicit protocol in the chosen cycle/half-cycle scope, reading only
        the current canonical elaboration generation. Incomplete impulses are kept and flagged.
        The canonical samples of the scope stay in memory under the returned ``scope_id`` until released."""
        view = self._result_view(elab_result_id)
        scope = gregorithm.load_canonical_scope(view.result_path, cycles=params.cycles,
                                                half_cycles=params.half_cycles)
        events = scope.events(gregorithm.Protocol(params.protocol), complete_only=False)
        data = {item.event.event_id: item
                for item in gregorithm.build_event_data(events, scope.segments, scope.samples)}
        # SoC denominator: canonical branch capacity at the last sample of each analysed half-cycle. Context
        # segments of other half-cycles are excluded by the half_cycle filter.
        polarity = {e.half_cycle: e.polarity for e in events}
        last: dict[int, object] = {}
        for sample in scope.samples:
            if sample.half_cycle in polarity and (sample.half_cycle not in last
                                                  or sample.source_row > last[sample.half_cycle].source_row):
                last[sample.half_cycle] = sample
        branch_end = {h: (s.charge_capacity_ah if polarity[h] > 0 else s.discharge_capacity_ah)
                      for h, s in last.items()}
        scope_id = uuid.uuid4().hex
        with self._lock:
            self._gitt_scopes[scope_id] = (ds_key, tuple(events), data)
        return GittIciScan(scope_id, tuple(events), branch_end)

    def release_gitt_ici(self, scope_id: str) -> None:
        """Drop the canonical samples held for a recognised scope."""
        with self._lock:
            self._gitt_scopes.pop(scope_id, None)

    def _gitt_scope(self, scope_id: str) -> tuple:
        with self._lock:
            scope = self._gitt_scopes.get(scope_id)
        if scope is None:
            raise KeyError("The recognised GITT/ICI impulses are no longer available; apply the settings again")
        return scope

    @staticmethod
    def _gitt_variant(key: str, ocv_source: str) -> gregorithm.MethodVariant:
        _label, method, variant = GITT_SERIES[key]
        return gregorithm.MethodVariant(gregorithm.DiffusionMethod(method), gregorithm.OcvSource(ocv_source), variant)

    @staticmethod
    def _gitt_settings(params: GittIciParams, ocv_overrides: dict,
                       ocv_fit_overrides: dict | None = None) -> gregorithm.AnalysisSettings:
        """gregorithm AnalysisSettings of the applied parameters, with the per-rest extrapolation targets
        (``ocv_overrides``: rest id -> target [s]) and fit start fractions (``ocv_fit_overrides``: rest id ->
        fraction) of OCV Analysis."""
        geometry = gregorithm.physical_inputs_si(
            active_volume=params.active_volume, active_volume_unit=gregorithm.VolumeUnit(params.active_volume_unit),
            effective_area=params.effective_area, effective_area_unit=gregorithm.AreaUnit(params.effective_area_unit),
            particle_radius=params.particle_radius,
            particle_radius_unit=gregorithm.LengthUnit(params.particle_radius_unit))
        lg, lo, sg, so = params.thresholds
        targets = gregorithm.OcvTargetPlan(params.ocv_target_s, tuple(sorted(ocv_overrides.items())),
                                           tuple(sorted((ocv_fit_overrides or {}).items())))
        return gregorithm.AnalysisSettings(
            geometry=geometry, ocv_targets=targets, ir_correction=gregorithm.IrCorrection(params.ir_correction),
            ir_sample_time_s=params.ir_sample_time_s, post_pulse_sample_time_s=params.ir_post_sample_time_s,
            relaxation_fit_start_fraction=params.ocv_fit_start_fraction,
            ocv_sample_time_s=params.ocv_sample_time_s,
            thresholds=gregorithm.QualityThresholds(linearity_green_pct=lg, linearity_orange_pct=lo,
                                                    stability_green_pct=sg, stability_orange_pct=so))

    @staticmethod
    def _ir_selections(ir_samples: dict | None) -> dict:
        """GUI manual IR selections {(event_id, side value): row | ('time', seconds)} as gregorithm selections: an
        exact source row, or a local sample time of that impulse side (gregorithm.IrSampleTime)."""
        return {(eid, gregorithm.IrDropSide(side)): (gregorithm.IrSampleTime(value[1]) if isinstance(value, tuple)
                                                    else value)
                for (eid, side), value in (ir_samples or {}).items()}

    @staticmethod
    def _ocv_selections(ocv_samples: dict | None) -> dict:
        """GUI sampled-OCV choices {rest id: row | ('time', seconds)} as gregorithm selections: an exact source row,
        or a local sample time of that rest (gregorithm.OcvSampleTime)."""
        return {rest: (gregorithm.OcvSampleTime(value[1]) if isinstance(value, tuple) else value)
                for rest, value in (ocv_samples or {}).items()}

    def gitt_ici_dependencies(self, scope_id: str, params: GittIciParams, ocv_overrides: dict,
                              ir_samples: dict | None = None, ocv_samples: dict | None = None,
                              ocv_fit_overrides: dict | None = None) -> dict:
        """Named dependency fingerprints of the applied settings, OCV targets and manual pre-pulse IR samples, for
        targeted invalidation: results whose recorded dependency differs become stale; the others are
        untouched."""
        _ds, events, _data = self._gitt_scope(scope_id)
        settings = self._gitt_settings(params, ocv_overrides, ocv_fit_overrides)
        rests = sorted({rest for e in events for rest in (e.rest_before_segment_id, e.rest_after_segment_id)
                        if rest is not None})
        return {**settings.dependency_fingerprints(),
                **gregorithm.ocv_target_dependencies(settings.ocv_targets, rests),
                **gregorithm.ir_sample_dependencies(self._ir_selections(ir_samples), [e.event_id for e in events]),
                **gregorithm.ocv_sample_dependencies(self._ocv_selections(ocv_samples), rests,
                                                     params.ocv_sample_time_s)}

    def gitt_ici_rest_ocv(self, scope_id: str, params: GittIciParams, ocv_overrides: dict,
                          ocv_samples: dict, ocv_fit_overrides: dict | None = None) -> tuple:
        """Sampled and (when active) extrapolated OCV of every rest of the scope, measured by
        gregorithm.measure_rest_ocv with the applied fit settings, per-rest targets and fit starts, the sampled-OCV
        time and the per-rest sampled choices (row or local time): the same code path as the D results, so the
        values always agree. The two products stay separate objects."""
        _ds, events, data = self._gitt_scope(scope_id)
        settings = self._gitt_settings(params, ocv_overrides, ocv_fit_overrides)
        ordered = tuple(data[e.event_id] for e in events if e.event_id in data)
        return gregorithm.measure_rest_ocv(ordered, settings, samples=self._ocv_selections(ocv_samples),
                                           extrapolate=params.extrapolation_active)

    def gitt_ici_ir_drops(self, scope_id: str, params: GittIciParams, ir_samples: dict,
                          event_ids: tuple | None = None) -> dict:
        """Pre-pulse and post-pulse IR drops of the impulses in scope (all, or ``event_ids``), measured by
        gregorithm.measure_ir_drops with the applied sample times and manual samples: {event_id: ImpulseIrDrops}.
        The same pre-pulse sample is the one the selected-sample IR corrections use, so values never diverge."""
        _ds, events, data = self._gitt_scope(scope_id)
        settings = self._gitt_settings(params, {})
        selections = self._ir_selections(ir_samples)
        wanted = None if event_ids is None else set(event_ids)
        return {e.event_id: gregorithm.measure_ir_drops(data[e.event_id], settings, samples=selections)
                for e in events if (wanted is None or e.event_id in wanted) and e.event_id in data}

    def analyze_gitt_ici(self, scope_id: str, params: GittIciParams, previous, *, event_id: str | None,
                         series: tuple, windows: dict, ocv_overrides: dict, automatic: bool, invalidate: bool,
                         progress, cancel, ir_samples: dict | None = None, ocv_samples: dict | None = None,
                         event_ids: tuple | None = None, only_stale: bool = False,
                         ocv_fit_overrides: dict | None = None) -> tuple:
        """Run one global or local (``event_id``) recalculation and return ``(base, outcome,
        skipped)`` WITHOUT committing: the caller commits ``outcome`` onto ``base`` with gregorithm.commit_batch,
        which rejects late results. ``base`` is ``previous`` after targeted invalidation when ``invalidate`` is set.

        ``windows`` maps (event_id, role) to a manual (start_s, end_s) window; every other window of the
        calculated impulses uses ``params.window_mode`` when ``automatic`` is set. ``ir_samples`` are the manual
        IR drop samples (only a pre-pulse one changes a result: selected-sample IR corrections), ``ocv_samples`` the
        sampled-OCV choices per rest (row or local time), ``ocv_fit_overrides`` the per-rest fit start fractions.
        ``event_ids`` limits a global plan to those impulses (a rest shared by two
        impulses); ``only_stale`` computes only identities that are missing or stale after the invalidation, so a
        settings change recalculates just the dependent results. Cancellation raises
        OperationCancelled before anything is returned."""
        _ds, events, data = self._gitt_scope(scope_id)
        settings = self._gitt_settings(params, ocv_overrides, ocv_fit_overrides)
        base = previous if previous is not None else gregorithm.ResultSnapshot()
        if invalidate:
            base = base.invalidate(self.gitt_ici_dependencies(scope_id, params, ocv_overrides, ir_samples,
                                                              ocv_samples, ocv_fit_overrides))
        targets = [e.event_id for e in events if event_id is None or e.event_id == event_id]
        mode = gregorithm.WindowMode(params.window_mode)
        requests = {}
        for eid in targets:
            for role in gregorithm.WindowRole:
                manual = windows.get((eid, role.value))
                if manual is not None:
                    requests[(eid, role)] = gregorithm.FitWindowRequest(
                        gregorithm.WindowMode.MANUAL, gregorithm.WindowBounds(*manual))
                elif automatic:
                    # Operator bounds replace the suggested range in every automatic mode; a statistical
                    # request without bounds is gregorithm's default, so it is left out of the plan.
                    bounds = params.window_bounds(role.value)
                    if bounds is not None or mode is not gregorithm.WindowMode.AUTO_STATISTICAL:
                        requests[(eid, role)] = gregorithm.FitWindowRequest(
                            mode, None if bounds is None else gregorithm.WindowBounds(*bounds))
        guidance = None
        if mode is gregorithm.WindowMode.PHYSICALLY_GUIDED and params.guidance_source:
            key, source = params.guidance_source.split("|", 1)
            guidance = gregorithm.PhysicalGuidance(self._gitt_variant(key, source), params.guidance_coefficient)
        plan = gregorithm.plan_analysis(
            tuple(data[e.event_id] for e in events),
            variants=[self._gitt_variant(key, source) for key, source in series], settings=settings,
            scope=gregorithm.RecalculationScope.LOCAL if event_id else gregorithm.RecalculationScope.GLOBAL,
            event_id=event_id, windows=requests, guidance=guidance,
            guidance_snapshot=base if guidance is not None else None, ir_samples=self._ir_selections(ir_samples),
            ocv_samples=self._ocv_selections(ocv_samples))
        tasks = plan.tasks
        if event_ids is not None:
            wanted = set(event_ids)
            tasks = tuple(t for t in tasks if t.identity.event_id in wanted)
        if only_stale:
            tasks = tuple(t for t in tasks if (r := base.current(t.identity)) is None or r.stale)

        def on_progress(event):
            kind, _sep, method = event.phase.partition(":")
            label = next((v[0] for v in GITT_SERIES.values() if v[1] == method), method.replace("_", " "))
            phase = f"Calculating · {label}" if method else kind.replace("_", " ").capitalize()
            progress(Progress(None if not event.total else event.completed / event.total,
                              phase, event.completed, event.total, False))

        outcome = gregorithm.run_batch(tasks, previous=base, progress=on_progress, cancel=cancel)
        return base, outcome, tuple(d.message for d in plan.skipped)

    def export_gitt_ici(self, ds_key: str, elab_result_id: str, snapshot, params: GittIciParams, files: list,
                        fmt: str, cancel) -> list[ExportDone]:
        """Write the GITT / ICI export tables built by app.gitt_ici_export: ``files`` lists (path, column titles,
        rows) with one row per impulse, one file per branch when split. The snapshot is first published as an
        immutable gregorithm generation, so every export stays traceable to its inputs. No destination is
        ever overwritten: every path is checked before anything is written, and a failure removes the files
        already written by this export."""
        destinations = [Path(path) for path, _titles, _rows in files]
        for destination in destinations:
            if destination.exists():
                raise FileExistsError(f"The destination already exists: {destination}")
        if fmt.lower() not in ("csv", "parquet"):
            raise ValueError("Export format must be CSV or Parquet")
        fingerprint = self._elab_result_fingerprint(elab_result_id)["fingerprint"]
        request = gregorithm.SnapshotPublicationRequest(
            self.caller_root, f"{ds_key}.gitt_ici", uuid.uuid4().hex, uuid.uuid4().hex,
            dataclasses.asdict(params), (gregorithm.DependencyRef("elaboration", fingerprint),))
        gregorithm.publish_snapshot(snapshot, request, cancel=cancel)
        written: list[Path] = []
        receipts = []
        try:
            for destination, (_path, titles, rows) in zip(destinations, files):
                if cancel.is_cancelled():
                    raise ExportCancelled()
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(destination.name + f".{uuid.uuid4().hex}.tmp")
                table = pa.table({title: pa.array([row[i] for row in rows]) for i, title in enumerate(titles)})
                try:
                    if fmt.lower() == "parquet":
                        pq.write_table(table, temporary)
                    else:
                        import pyarrow.csv as pacsv
                        pacsv.write_csv(table, temporary)
                    os.replace(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)
                written.append(destination)
                receipts.append(ExportDone(str(destination), len(rows), destination.stat().st_size,
                                           hashlib.sha256(destination.read_bytes()).hexdigest()))
        except BaseException:
            for done in written:
                done.unlink(missing_ok=True)
            raise
        return receipts

    def gitt_ici_trace(self, scope_id: str, event_id: str) -> ImpulseTrace:
        """Raw canonical samples of the rest before, the pulse and the rest after one impulse, with the time origins
        gregorithm documents for its windows (GITT pulse: last sample of the rest before; ICI rest: last sample under
        current). No decimation, interpolation or correction is applied."""
        _ds, _events, data = self._gitt_scope(scope_id)
        item = data.get(event_id)
        if item is None:
            raise KeyError("Unknown impulse")

        def segment(seg, samples):
            return GittSegmentTrace(None if seg is None else seg.segment_id,
                                    tuple(s.source_row for s in samples), tuple(s.time_s for s in samples),
                                    tuple(s.voltage_v for s in samples), tuple(s.current_a for s in samples))
        return ImpulseTrace(event_id, segment(item.rest_before_segment, item.rest_before),
                            segment(item.active_segment, item.active),
                            segment(item.rest_after_segment, item.rest_after),
                            {"pulse": item.rest_before[-1].time_s if item.rest_before else None,
                             "rest_after": item.active[-1].time_s if item.active else None})

    # --- Capacity: all scientific values are produced by public gregorithm APIs. ---
    @staticmethod
    def _core_capacity_params(params: CapParams) -> gregorithm.CapacityParameters:
        order = (gregorithm.CycleOrder.CHARGE_DISCHARGE if params.cycle_order == CHARGE_FIRST
                 else gregorithm.CycleOrder.DISCHARGE_CHARGE)
        return gregorithm.CapacityParameters(cycle_order=order, reference_cycle=params.reference_cycle,
                                           active_material_mass_mg=params.active_material_mass_mg,
                                           electrode_area_cm2=params.electrode_area_cm2)

    def analyze_capacity(self, ds_key: str, elab_result_id: str, params: CapParams, progress, cancel) -> CapResult:
        view = self._result_view(elab_result_id)
        fingerprint = dict(self._elab_result_fingerprint(elab_result_id))
        request = gregorithm.CapacityRequest(elaboration_path=view.result_path,
                                           elaboration_fingerprint=fingerprint["fingerprint"],
                                           caller_root=self.caller_root, sample_id=f"{ds_key}.capacity",
                                           job_id=uuid.uuid4().hex, generation_id=uuid.uuid4().hex,
                                           parameters=self._core_capacity_params(params))
        def on_progress(event):
            publishing = event.total is not None and event.completed >= event.total
            progress(Progress(None if event.total is None else event.completed / max(1, event.total),
                              "Saving result…" if publishing else event.phase.replace("_", " ").title(),
                              event.completed, event.total, publishing))
        try:
            core = gregorithm.analyze_capacity(request, progress=on_progress, cancel=cancel)
        except gregorithm.OperationCancelled as exc:
            raise CapacityCancelled() from exc
        result_id = core.ref.generation_id
        with self._lock:
            self._capacity_paths[result_id] = core.ref.path
            self._capacity_ds[result_id] = ds_key
        report = self._read_json(core.ref.path / "reports" / "capacity.json")
        details = tuple((str(k).replace("_", " ").title(), str(v)) for k, v in report.items()
                        if k not in {"diagnostics"})
        return CapResult(result_id, elab_result_id, core.cycles, params,
                         report.get("reference_capacity_ah"), details)

    def _elab_result_fingerprint(self, result_id: str) -> dict:
        manifest = self._read_json(self._result_view(result_id).result_path / "manifest.json")
        value = manifest.get("fingerprint") or manifest.get("content_hash")
        if not value:
            # Public result reports always have a fingerprint; fail closed rather than guess lineage.
            raise ValueError("The saved elaboration has no fingerprint")
        return {"fingerprint": str(value)}

    @staticmethod
    def _read_json(path: Path) -> dict:
        import json
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)

    def capacity_table(self, cap_result_id: str, units: UnitPrefs) -> CapacityTable:
        with self._lock:
            path = self._capacity_paths.get(cap_result_id)
        if path is None:
            raise KeyError("Capacity result is no longer available")
        table = pq.read_table(path / "tables" / "capacity.parquet")
        values = {name: _numeric(table[name]) * _factor(name, units) for name in table.column_names}
        diag_path = path / "tables" / "capacity_diagnostics.parquet"
        diagnostics = []
        if diag_path.exists():
            diag = pq.read_table(diag_path)
            diagnostics = [(int(row["cycle"]), str(row["column"]), str(row["code"]))
                           for row in diag.to_pylist()]
        report_path = path / "reports" / "capacity.json"
        if not report_path.exists():
            # A cycle-correction generation embeds its Capacity table but publishes
            # reports/cycle_correction.json instead of reports/capacity.json.
            report_path = path / "reports" / "cycle_correction.json"
        report = self._read_json(report_path) if report_path.exists() else {}
        return CapacityTable(cap_result_id, values, diagnostics, report)

    def export_capacity(self, cap_result_id: str, cycles, columns: list[str], units: UnitPrefs, fmt: str, path: str,
                        include_diagnostics: bool, progress, cancel) -> list[ExportDone]:
        table = self.capacity_table(cap_result_id, units)
        requested = [c for c in columns if c in table.values]
        if "cycle" not in requested:
            requested.insert(0, "cycle")
        source_cycles = np.asarray(table.values["cycle"], dtype=int)
        mask = np.ones(len(source_cycles), dtype=bool) if cycles is None else np.isin(source_cycles, list(cycles))
        destination = Path(path)
        if destination.exists():
            raise FileExistsError(f"The destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + f".{uuid.uuid4().hex}.tmp")
        selected = {name: pa.array(np.asarray(table.values[name])[mask]) for name in requested}
        selected_table = pa.table(selected)
        try:
            progress(Progress(0.0, "Writing export", 0, int(mask.sum())))
            if cancel.is_set():
                raise ExportCancelled()
            if fmt.lower() == "parquet":
                pq.write_table(selected_table, temporary)
            elif fmt.lower() == "csv":
                import pyarrow.csv as pacsv
                pacsv.write_csv(selected_table, temporary)
            else:
                raise ValueError("Export format must be CSV or Parquet")
            if cancel.is_set():
                raise ExportCancelled()
            os.replace(temporary, destination)
            progress(Progress(1.0, "Export complete", int(mask.sum()), int(mask.sum())))
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        receipts = [ExportDone(str(destination), int(mask.sum()), destination.stat().st_size, digest)]
        if include_diagnostics:
            diag_name = destination.with_name(destination.stem + " - diagnostics" + destination.suffix)
            if diag_name.exists():
                raise FileExistsError(f"The destination already exists: {diag_name}")
            allowed = {int(v) for v in source_cycles[mask]}
            rows = [d for d in table.diagnostics if d[0] in allowed and d[1] in requested]
            diag_temp = diag_name.with_name(diag_name.name + f".{uuid.uuid4().hex}.tmp")
            try:
                diag_table = pa.table({"cycle": [r[0] for r in rows], "column": [r[1] for r in rows],
                                       "code": [r[2] for r in rows]})
                if fmt.lower() == "parquet":
                    pq.write_table(diag_table, diag_temp)
                else:
                    import pyarrow.csv as pacsv
                    pacsv.write_csv(diag_table, diag_temp)
                if cancel.is_set():
                    raise ExportCancelled()
                os.replace(diag_temp, diag_name)
                receipts.append(ExportDone(str(diag_name), len(rows), diag_name.stat().st_size,
                                            hashlib.sha256(diag_name.read_bytes()).hexdigest()))
            except Exception:
                diag_temp.unlink(missing_ok=True)
                destination.unlink(missing_ok=True)
                raise
        return receipts

    def release_capacity(self, cap_result_id: str) -> None:
        with self._lock:
            self._capacity_paths.pop(cap_result_id, None)
            self._capacity_ds.pop(cap_result_id, None)

    # --- dQ/dV: gregorithm owns derivation, interpolation and smoothing. ---
    def analyze_dqdv(self, ds_key: str, elab_result_id: str, params: DqdvParams,
                     dcir_published_id: str | None, progress, cancel) -> DqdvResult:
        view = self._result_view(elab_result_id)
        fingerprint = self._elab_result_fingerprint(elab_result_id)["fingerprint"]
        spans: tuple[tuple[int, int], ...] = ()
        accepted_fingerprint = ""
        if dcir_published_id:
            linked = self._dcir_elaboration.get(dcir_published_id)
            if linked != elab_result_id:
                raise ValueError("The calculated DCIR result does not belong to the selected elaboration")
            events = tuple(self._dcir_events.get(dcir_published_id, ()))
            measurements = tuple(self._dcir_measurements.get(dcir_published_id, ()))
            spans = gregorithm.accepted_correction_spans(events, measurements)
            accepted_fingerprint = gregorithm.accepted_event_fingerprint(events, measurements)
        core_params = params.core_parameters()
        # gregorithm validates every field whatever the method. The GUI retains
        # valid defaults for settings that do not apply, so validate the exact
        # submitted object before starting a worker publication.
        core_params.validate()
        request = gregorithm.DqdvRequest(
            elaboration_path=view.result_path,
            elaboration_fingerprint=fingerprint,
            caller_root=self.caller_root,
            sample_id=f"{ds_key}.dqdv",
            job_id=uuid.uuid4().hex,
            generation_id=uuid.uuid4().hex,
            cycles=tuple(params.cycles),
            accepted_spans=spans,
            parameters=core_params,
        )

        def on_progress(event):
            publishing = event.total is not None and event.completed >= event.total
            progress(Progress(
                None if event.total is None else event.completed / max(1, event.total),
                "Saving result…" if publishing else event.phase.replace("_", " ").title(),
                event.completed, event.total, publishing,
            ))

        try:
            core = gregorithm.analyze_dqdv(request, progress=on_progress, cancel=cancel)
        except gregorithm.OperationCancelled as exc:
            raise DqdvCancelled() from exc
        result_id = core.ref.generation_id
        report = self._read_json(core.ref.path / "reports" / "dqdv.json")
        unavailable = tuple(
            (int(item["cycle"]), str(item["phase"]), str(item["code"]))
            for item in report.get("unavailable", ())
        )
        metrics = {
            int(item["anchor_source_row"]): (item.get("ape_percent"), item.get("rmse_v"), item.get("code") or "",
                                             (int(item["cycle"]), int(item["half_cycle"]), str(item["phase"])))
            for item in report.get("reconstruction", ())
        }
        with self._lock:
            self._dqdv_metrics[result_id] = metrics
            self._dqdv_paths[result_id] = core.ref.path
            self._dqdv_ds[result_id] = ds_key
            self._dqdv_elaboration[result_id] = elab_result_id
            self._dqdv_params[result_id] = params
            self._dqdv_dcir_fingerprint[result_id] = accepted_fingerprint
            self._dqdv_span_count[result_id] = len(spans)
        details = (("Fingerprint", core.fingerprint), ("Scientific engine", "gregorithm"))
        return DqdvResult(result_id, elab_result_id, params, core.curves, core.points,
                          unavailable, accepted_fingerprint, len(spans), False, details)

    def _dqdv_path(self, result_id: str) -> Path:
        with self._lock:
            path = self._dqdv_paths.get(result_id)
        if path is None:
            raise KeyError("dQ/dV result is no longer available")
        table = path / "tables" / "dqdv.parquet"
        if not table.exists():
            raise FileNotFoundError("The saved result has no dQ/dV table")
        return path

    def dqdv_curves(self, dqdv_result_id: str, units: UnitPrefs) -> list[DqdvCurveData]:
        """Curves of a dQ/dV result in display units, grouped by curve and monotonic segment (see _dqdv_curves)."""
        table = pq.read_table(self._dqdv_path(dqdv_result_id) / "tables" / "dqdv.parquet")
        return _dqdv_curves(table, scale(VOLTAGE, units.voltage), scale(CAPACITY, units.capacity))

    def dqdv_reconstruction(self, dqdv_result_id: str, units: UnitPrefs) -> list[ReconCurveData]:
        """Measured and reconstructed voltage curves of each dQ/dV curve, in display units, with APE and RMSE.

        Empty when the result was calculated without reconstruction. Values are gregorithm's; only unit scaling here."""
        source = self._dqdv_path(dqdv_result_id) / "tables" / "dqdv_reconstruction.parquet"
        if not source.exists():
            return []
        table = pq.read_table(source)
        with self._lock:
            metrics = dict(self._dqdv_metrics.get(dqdv_result_id, {}))
        sv, sq = scale(VOLTAGE, units.voltage), scale(CAPACITY, units.capacity)
        cycle = _numeric(table["cycle"]).astype(int)
        half = _numeric(table["half_cycle"]).astype(int)
        anchor = _numeric(table["anchor_source_row"]).astype(np.int64)
        phase = np.asarray(table["phase"].to_pylist(), dtype=object)
        kind = np.asarray(table["curve"].to_pylist(), dtype=object)
        q = _numeric(table["capacity_ah"]) * sq
        v = _numeric(table["voltage_v"]) * sv
        out: list[ReconCurveData] = []
        order: dict[int, list[int]] = {}
        for index, a in enumerate(anchor.tolist()):
            order.setdefault(a, []).append(index)
        for a, rows in order.items():
            idx = np.asarray(rows)
            measured, rebuilt = idx[kind[idx] == "measured"], idx[kind[idx] == "reconstructed"]
            ape, rmse, code, _labels = metrics.get(a, (None, None, "", None))
            first = int(idx[0])
            out.append(ReconCurveData(int(cycle[first]), int(half[first]), str(phase[first]), a,
                                      q[measured], v[measured], q[rebuilt], v[rebuilt],
                                      None if ape is None else float(ape),
                                      None if rmse is None else float(rmse) * sv, str(code)))
        empty = np.empty(0, dtype=float)
        for a, (_ape, _rmse, code, labels) in metrics.items():     # curves with no dQ/dV: listed, nothing drawn
            if a not in order and labels is not None:
                out.append(ReconCurveData(labels[0], labels[1], labels[2], a, empty, empty, empty, empty, None, None,
                                          str(code)))
        out.sort(key=lambda x: (x.cycle, x.half_cycle))
        return out

    def export_dqdv(self, dqdv_result_id: str, cycles: tuple, phases: tuple, columns: list[str],
                    units: UnitPrefs, fmt: str, path: str, progress, cancel) -> list[ExportDone]:
        source = self._dqdv_path(dqdv_result_id) / "tables" / "dqdv.parquet"
        destination = Path(path)
        if destination.exists():
            raise FileExistsError(f"The destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + f".{uuid.uuid4().hex}.tmp")
        requested = [name for name in columns if name in {
            "cycle", "half_cycle", "phase", "anchor_source_row", "segment_index",
            "voltage_mid", "dqdv_raw", "dqdv_smoothed",
        }]
        if not requested:
            raise ValueError("Select at least one dQ/dV column")
        canonical = {
            "voltage_mid": "voltage_mid_v", "dqdv_raw": "dqdv_raw_ah_per_v",
            "dqdv_smoothed": "dqdv_smoothed_ah_per_v",
        }
        read_columns = list(dict.fromkeys([canonical.get(name, name) for name in requested] + ["cycle", "phase"]))
        parquet = pq.ParquetFile(source)
        source_schema = parquet.schema_arrow
        output_schema = pa.schema([
            pa.field(name, source_schema.field(canonical.get(name, name)).type) for name in requested
        ])
        total = parquet.metadata.num_rows
        seen = selected_rows = 0
        writer = None
        try:
            if cancel.is_set():
                raise ExportCancelled()
            if fmt.lower() == "parquet":
                writer = pq.ParquetWriter(temporary, output_schema, compression="zstd")
            elif fmt.lower() == "csv":
                import pyarrow.csv as pacsv
                writer = pacsv.CSVWriter(temporary, output_schema)
            else:
                raise ValueError("Export format must be CSV or Parquet")
            for batch in parquet.iter_batches(batch_size=65_536, columns=read_columns):
                if cancel.is_set():
                    raise ExportCancelled()
                data = pa.Table.from_batches((batch,))
                cycle_values = _numeric(data["cycle"]).astype(int)
                phase_values = np.asarray(data["phase"].to_pylist(), dtype=object)
                mask = np.isin(cycle_values, list(cycles)) & np.isin(phase_values, list(phases))
                filtered = data.filter(pa.array(mask))
                output = []
                for name in requested:
                    core_name = canonical.get(name, name)
                    values = filtered[core_name]
                    if name == "voltage_mid":
                        output.append(pc.multiply(values, scale(VOLTAGE, units.voltage)))
                    elif name in {"dqdv_raw", "dqdv_smoothed"}:
                        output.append(pc.multiply(values, scale(CAPACITY, units.capacity)))
                    else:
                        output.append(values)
                converted = pa.Table.from_arrays(output, schema=output_schema)
                if converted.num_rows:
                    writer.write_table(converted)
                seen += batch.num_rows
                selected_rows += filtered.num_rows
                progress(Progress(seen / max(1, total), "Preparing export", seen, total))
            if cancel.is_set():
                raise ExportCancelled()
            writer.close()
            writer = None
            if cancel.is_set():
                raise ExportCancelled()
            os.replace(temporary, destination)
            progress(Progress(1.0, "Export complete", selected_rows, selected_rows))
        except Exception:
            if writer is not None:
                writer.close()
                writer = None
            temporary.unlink(missing_ok=True)
            raise
        return [ExportDone(str(destination), selected_rows, destination.stat().st_size,
                           hashlib.sha256(destination.read_bytes()).hexdigest())]

    def release_dqdv(self, dqdv_result_id: str) -> None:
        with self._lock:
            self._dqdv_paths.pop(dqdv_result_id, None)
            self._dqdv_ds.pop(dqdv_result_id, None)
            self._dqdv_elaboration.pop(dqdv_result_id, None)
            self._dqdv_params.pop(dqdv_result_id, None)
            self._dqdv_dcir_fingerprint.pop(dqdv_result_id, None)
            self._dqdv_span_count.pop(dqdv_result_id, None)
            self._dqdv_metrics.pop(dqdv_result_id, None)

    # --- DCIR: the adapter only translates persisted/public core values. ---
    @staticmethod
    def _core_detection_params(params: DetectionParams):
        return gregorithm.DcirDetectionParameters(
            rest_before_s=params.rest_before_s, pulse_s=params.pulse_s, rest_after_s=params.rest_after_s,
            rest_before_tolerance_pct=params.rest_before_tolerance_pct,
            pulse_tolerance_pct=params.pulse_tolerance_pct,
            rest_after_tolerance_pct=params.rest_after_tolerance_pct,
            polarity=gregorithm.PulsePolarity(params.polarity))

    @staticmethod
    def _core_event(row: dict):
        return gregorithm.DcirEvent(
            event_id=row["event_id"], source=gregorithm.EventSource(row["source"]),
            state=gregorithm.ReviewState(row["state"]), acceptance_order=row.get("acceptance_order"),
            event_time_s=row.get("event_time_s"), cycle=row.get("cycle"), half_cycle=row.get("half_cycle"),
            polarity=gregorithm.PulsePolarity(row["polarity"]) if row.get("polarity") else None,
            pulse_segment_id=row.get("pulse_segment_id"), pulse_first_row=row.get("pulse_first_row"),
            pulse_last_row=row.get("pulse_last_row"), pulse_duration_s=row.get("pulse_duration_s"),
            rest_before_segment_id=row.get("rest_before_segment_id"), rest_before_duration_s=row.get("rest_before_duration_s"),
            rest_after_segment_id=row.get("rest_after_segment_id"), rest_after_duration_s=row.get("rest_after_duration_s"),
            pre_pulse_row=row.get("pre_pulse_row"), correction_start_row=row.get("correction_start_row"),
            correction_end_row=row.get("correction_end_row"))

    @classmethod
    def _read_dcir_events(cls, path: Path) -> tuple:
        import json
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return ()
        if path.suffix == ".jsonl":
            return tuple(cls._core_event(json.loads(line)) for line in raw.splitlines())
        return tuple(cls._core_event(row) for row in json.loads(raw))

    @staticmethod
    def _event_view(event, units: UnitPrefs) -> EventView:
        return EventView(event.event_id, event.source.value, event.state.value, event.event_time_s,
                         event.cycle, event.half_cycle, event.polarity.value if event.polarity else None,
                         event.pulse_duration_s, event.pre_pulse_row, event.acceptance_order,
                         None if event.event_time_s is None else event.event_time_s * scale(TIME, units.time),
                         event.pulse_first_row, event.pulse_last_row, event.correction_start_row, event.correction_end_row)

    @staticmethod
    def _measurement_view(value, units: UnitPrefs) -> MeasurementView:
        codes = tuple(d.code if hasattr(d, "code") else str(d) for d in value.diagnostics)
        return MeasurementView(value.event.event_id, value.requested_time_s, value.actual_time_s, value.time_deviation_s,
            value.i_ref_a * scale(CURRENT, units.current), value.v_ref_v * scale(VOLTAGE, units.voltage),
            value.i_meas_a * scale(CURRENT, units.current), value.v_meas_v * scale(VOLTAGE, units.voltage),
            None if value.delta_i_a is None else value.delta_i_a * scale(CURRENT, units.current),
            None if value.delta_v_v is None else value.delta_v_v * scale(VOLTAGE, units.voltage),
            None if value.resistance_ohm is None else value.resistance_ohm * scale(RESISTANCE, units.resistance),
            value.soc_pct, codes,
            value.reference_sample.source_row if value.reference_sample else None,
            value.current_sample.source_row if value.current_sample else None,
            value.voltage_sample.source_row if value.voltage_sample else None,
            current_time_s=(value.current_sample.time_s - value.reference_sample.time_s
                            if value.current_sample and value.reference_sample else None))

    def detect_dcir(self, ds_key: str, elab_result_id: str, params: DetectionParams, units: UnitPrefs, progress, cancel) -> DetectionResult:
        view = self._result_view(elab_result_id)
        fingerprint = self._elab_result_fingerprint(elab_result_id)["fingerprint"]
        request = gregorithm.DcirDetectionRequest(view.result_path, fingerprint, self.caller_root, f"{ds_key}.dcir",
            uuid.uuid4().hex, uuid.uuid4().hex, self._core_detection_params(params))
        def on_progress(event):
            publishing = event.total is not None and event.completed >= event.total
            progress(Progress(None if event.total is None else event.completed / max(1, event.total),
                              "Saving result…" if publishing else event.phase.replace("_", " ").title(),
                              event.completed, event.total, publishing))
        try:
            core = gregorithm.detect_dcir(request, progress=on_progress, cancel=cancel)
        except gregorithm.OperationCancelled as exc:
            raise DcirCancelled() from exc
        events = self._read_dcir_events(core.ref.path / "tables" / "dcir_events.jsonl")
        with self._lock:
            self._dcir_paths[core.ref.generation_id] = core.ref.path
            self._dcir_events[core.ref.generation_id] = events
            self._dcir_measurements[core.ref.generation_id] = ()
            self._dcir_elaboration[core.ref.generation_id] = elab_result_id
        return DetectionResult(core.ref.generation_id, elab_result_id, params, tuple(self._event_view(x, units) for x in events))

    def dcir_events(self, dcir_id: str, units: UnitPrefs) -> tuple:
        return tuple(self._event_view(x, units) for x in self._dcir_events.get(dcir_id, ()))

    def _dcir_context(self, dcir_id: str):
        events = self._dcir_events.get(dcir_id)
        elab_id = self._dcir_elaboration.get(dcir_id)
        if events is None or elab_id is None:
            raise KeyError("DCIR generation is no longer available")
        return events, elab_id, self._result_view(elab_id)

    def measure_dcir(self, dcir_id: str, event_ids: list[str], requested_time_s: float, units: UnitPrefs, cancel,
                     current_time_s: float | None = None) -> list:
        """Preview measurements of auto events at the voltage time and the optional I1 time (None: 3rd sample)."""
        events, _elab_id, view = self._dcir_context(dcir_id)
        fingerprint = self._elab_result_fingerprint(_elab_id)["fingerprint"]
        out = []
        for order, event in enumerate(events):
            if event.event_id not in event_ids:
                continue
            if cancel.is_set():
                raise DcirCancelled()
            # Review decisions are local until Apply & Calculate.  Measure the
            # real elaborated event through the core using a temporary Accepted view;
            # never mutate the persisted detection generation.
            reviewed = event if event.state is gregorithm.ReviewState.ACCEPTED else gregorithm.review_event(
                event, gregorithm.ReviewState.ACCEPTED, order)
            out.append(self._measurement_view(gregorithm.measure_automatic_from_generation(
                view.result_path, fingerprint, reviewed, requested_time_s=requested_time_s,
                current_time_s=current_time_s, label=f"DCIR{order + 1}"), units))
        return out

    def _sample_ref(self, elab_id: str, row: int):
        table = pq.read_table(self._result_view(elab_id).files["points"], filters=[("source_row", "=", int(row))]).to_pylist()
        if len(table) != 1:
            raise ValueError("The selected manual DCIR point is not a real elaborated sample")
        x = table[0]
        return gregorithm.SampleRef(int(x["source_row"]), float(x["time_s"]), float(x["current_a"]), float(x["voltage_v"]),
                                  int(x["cycle"]), int(x["half_cycle"]), self._elab_result_fingerprint(elab_id)["fingerprint"])

    def _manual_values(self, dcir_id: str, values: dict):
        """Core ManualValue tuple and boundary samples from the dialog values (display units).
        A picked value takes the exact base-unit component of its real sample: dividing the displayed value by the
        unit factor is not exact in floating point, and the core requires equality with the sample."""
        _events, elab_id, _view = self._dcir_context(dcir_id)
        converted = []
        for key, _label, quantity in (("i0", "", CURRENT), ("v0", "", VOLTAGE), ("i1", "", CURRENT), ("v1", "", VOLTAGE)):
            item = values[key]
            if item.sample_row is not None:
                sample = self._sample_ref(elab_id, item.sample_row)
                exact = sample.current_a if quantity == CURRENT else sample.voltage_v
                converted.append(gregorithm.ManualValue(exact, gregorithm.ValueProvenance.SAMPLE, sample))
                continue
            factor = scale(quantity, values.get("units", UnitPrefs()).unit(quantity)) if values.get("units") else 1.0
            converted.append(gregorithm.ManualValue(item.value / factor))
        boundaries = values.get("boundary") or ()
        refs = tuple(self._sample_ref(elab_id, b.row) for b in boundaries if b is not None)
        return tuple(converted), (refs if len(refs) == 2 else None)

    def _core_manual(self, dcir_id: str, values: dict, units: UnitPrefs):
        """The core manual measurement (create_manual_event returns one DcirMeasurement; its .event is the event)."""
        manual, boundaries = self._manual_values(dcir_id, dict(values, units=units))
        return gregorithm.create_manual_event(*manual, acceptance_order=len(self._dcir_events[dcir_id]),
                                              boundary_samples=boundaries)

    def create_manual_dcir(self, dcir_id: str, values: dict, units: UnitPrefs) -> tuple:
        measure = self._core_manual(dcir_id, values, units)
        event = measure.event
        # Manual values are validated and computed by gregorithm.  Retaining this
        # unpublished core record lets the later publication include exactly the
        # same event, rather than reconstructing or recalculating it in the GUI.
        with self._lock:
            self._dcir_events[dcir_id] = (*self._dcir_events[dcir_id], event)
            self._dcir_measurements[dcir_id] = (*self._dcir_measurements[dcir_id], measure)
        return self._event_view(event, units), self._measurement_view(measure, units)

    def preview_manual_dcir(self, dcir_id: str, values: dict, units: UnitPrefs):
        """Resistance preview only: computed by the core, nothing is retained (no event is created)."""
        return self._measurement_view(self._core_manual(dcir_id, values, units), units)

    def manual_measurement(self, dcir_id: str, event_id: str, units: UnitPrefs):
        for value in self._dcir_measurements.get(dcir_id, ()):
            if value.event.event_id == event_id:
                return self._measurement_view(value, units)
        raise KeyError("Manual DCIR measurement is not available")

    def publish_dcir(self, dcir_id: str, decisions: dict, measurement_time_s: float, units: UnitPrefs, progress, cancel,
                     current_time_s: float | None = None, event_times: dict | None = None) -> PublishedResult:
        """Publish the reviewed events. Accepted auto pulses are measured by gregorithm at the all-pulse times, or at
        their own ``event_times`` (event_id -> (V1 time, I1 time | None)) when given; each measurement records the
        times it was resolved with and gregorithm re-verifies it at those times before publishing."""
        event_times = dict(event_times or {})
        events, elab_id, view = self._dcir_context(dcir_id)
        reviewed = []
        order = 0
        for event in events:
            state = gregorithm.ReviewState(decisions.get(event.event_id, event.state.value))
            accepted_order = order if state is gregorithm.ReviewState.ACCEPTED else None
            reviewed.append(gregorithm.review_event(event, state, accepted_order))
            if state is gregorithm.ReviewState.ACCEPTED:
                order += 1
        labels = gregorithm.event_labels(tuple(reviewed))
        fingerprint = self._elab_result_fingerprint(elab_id)["fingerprint"]
        # One measurement per accepted automatic event, with its final accepted order and label (labels are
        # presentation metadata). A first pass inside the loop above used to measure every event a second time
        # and was then discarded; gregorithm verifies the measurements again when publishing.
        times = lambda e: event_times.get(e.event_id, (measurement_time_s, current_time_s))
        measurements = [gregorithm.measure_automatic_from_generation(view.result_path, fingerprint, e,
            requested_time_s=times(e)[0], current_time_s=times(e)[1], label=labels.get(e.event_id)) for e in reviewed
            if e.state is gregorithm.ReviewState.ACCEPTED and e.source is gregorithm.EventSource.AUTO]
        accepted_auto = {e.event_id for e in reviewed
                         if e.state is gregorithm.ReviewState.ACCEPTED and e.source is gregorithm.EventSource.AUTO}
        own = {k: v for k, v in event_times.items() if k in accepted_auto}
        measurements.extend(m for m in self._dcir_measurements.get(dcir_id, ())
                            if m.event.event_id in {e.event_id for e in reviewed if e.state is gregorithm.ReviewState.ACCEPTED and e.source is gregorithm.EventSource.MANUAL})
        request = gregorithm.DcirPublicationRequest(self.caller_root, f"{dcir_id}.measure", uuid.uuid4().hex, uuid.uuid4().hex,
            self._elab_result_fingerprint(elab_id)["fingerprint"], tuple(reviewed), gregorithm.OperationId.MEASURE_DCIR,
            {"measurement_time_s": measurement_time_s, **({"current_time_s": current_time_s} if current_time_s is not None else {}),
             **({"event_times": {k: {"measurement_time_s": v1, **({"current_time_s": i1} if i1 is not None else {})}
                                 for k, (v1, i1) in sorted(own.items())}} if own else {})},
            tuple(measurements), view.result_path)
        def on_progress(event):
            publishing = event.total is not None and event.completed >= event.total
            progress(Progress(None if event.total is None else event.completed / max(1, event.total),
                "Saving result…" if publishing else event.phase.replace("_", " ").title(), event.completed, event.total, publishing))
        try:
            core = gregorithm.publish_dcir_events(request, progress=on_progress, cancel=cancel)
        except gregorithm.OperationCancelled as exc:
            raise DcirCancelled() from exc
        with self._lock:
            self._dcir_paths[core.ref.generation_id] = core.ref.path
            self._dcir_events[core.ref.generation_id] = tuple(reviewed)
            self._dcir_measurements[core.ref.generation_id] = tuple(measurements)
            self._dcir_elaboration[core.ref.generation_id] = elab_id
        return PublishedResult(core.ref.generation_id, tuple(self._event_view(e, units) for e in reviewed),
            tuple(self._measurement_view(m, units) for m in measurements), measurement_time_s,
            tuple(gregorithm.event_labels(tuple(reviewed)).items()), "", core.accepted_fingerprint, current_time_s,
            tuple((k, v1, i1) for k, (v1, i1) in sorted(own.items())))

    def dcir_published(self, published_id: str, units: UnitPrefs) -> PublishedResult:
        events = self._dcir_events.get(published_id, ())
        measurements = self._dcir_measurements.get(published_id, ())
        report_path = self._dcir_paths[published_id] / "reports" / "dcir.json"
        if report_path.exists():
            fingerprint = str(self._read_json(report_path).get("accepted_event_fingerprint", ""))
        else:
            # Cycle-correction generations carry DCIR tables without reports/dcir.json.
            fingerprint = gregorithm.accepted_event_fingerprint(tuple(events), tuple(measurements))
        # The times are those the automatic measurements were resolved with (10 s / 3rd sample when none exists):
        # the most frequent pair is the all-pulse times, other pulses keep their own (dcir.split_event_times).
        v1, i1, own = split_event_times({m.event.event_id: (m.requested_time_s, m.current_requested_time_s)
                                         for m in measurements if m.requested_time_s is not None})
        return PublishedResult(published_id, tuple(self._event_view(e, units) for e in events),
            tuple(self._measurement_view(m, units) for m in measurements), v1,
            tuple(gregorithm.event_labels(events).items()), "", fingerprint, i1,
            tuple((k, a, b) for k, (a, b) in sorted(own.items())))

    def _trace_for_rows(self, elab_id: str, start_s: float, end_s: float, units: UnitPrefs, event_id=None, t0_s=0.0) -> PulseTrace:
        table = pq.read_table(self._result_view(elab_id).files["points"], filters=[("time_s", ">=", start_s), ("time_s", "<=", end_s)])
        rows = table.to_pylist()
        return PulseTrace(event_id, np.asarray([r["time_s"] - t0_s for r in rows]),
            np.asarray([r["current_a"] * scale(CURRENT, units.current) for r in rows]),
            np.asarray([r["voltage_v"] * scale(VOLTAGE, units.voltage) for r in rows]),
            np.asarray([r["source_row"] for r in rows]), np.asarray([r["time_s"] * scale(TIME, units.time) for r in rows]),
            np.asarray([r["cycle"] for r in rows]), t0_s)

    def dcir_trace(self, dcir_id: str, event_id: str, units: UnitPrefs, requested_time_s: float | None) -> PulseTrace:
        events, elab_id, _view = self._dcir_context(dcir_id)
        event = next((e for e in events if e.event_id == event_id), None)
        if event is None or event.event_time_s is None:
            raise KeyError("The selected DCIR event is not available")
        span = max(60.0, (event.pulse_duration_s or 0.0) + 60.0)
        trace = self._trace_for_rows(elab_id, event.event_time_s - span, event.event_time_s + span, units, event_id, 0.0)
        # Time zero of the chart is the reference sample V0, the same origin the core uses for the measurement time,
        # so the dashed requested-time line and the V1 marker share one axis (pulse start when V0 is missing).
        t_abs, rows = np.asarray(trace.t_rel_s, float), np.asarray(trace.rows)
        ref = np.flatnonzero(rows == event.pre_pulse_row) if event.pre_pulse_row is not None else np.empty(0, int)
        t0 = float(t_abs[ref[0]]) if ref.size else float(event.event_time_s)
        t_rel = t_abs - t0
        return PulseTrace(trace.event_id, t_rel, trace.current, trace.voltage, trace.rows, trace.time_display,
                          trace.cycle, t0, self.pulse_zones(event, t_rel, trace.rows), {})

    @staticmethod
    def pulse_zones(event, t_rel_s, rows) -> dict:
        """Shaded zones of the inspection chart, as the extent of the RECORDED samples of each part: the pulse from its
        first to its last pulse sample (core rows pulse_first_row..pulse_last_row), the rest before up to the V0/I0
        reference sample (pre_pulse_row), the rest after from its first sample. A segment's duration in the core runs
        to the first sample of the next segment, so drawing [start, start + duration] would cover one sample of the
        next part and look shifted by one sampling interval. Gaps between zones are the unrecorded step transitions."""
        t, r = np.asarray(t_rel_s, float), np.asarray(rows)
        zones: dict = {}
        if not t.size:
            return zones
        extent = lambda m: (float(t[m].min()), float(t[m].max())) if m.any() else None
        if event.pulse_first_row is not None and event.pulse_last_row is not None:
            pulse = extent((r >= event.pulse_first_row) & (r <= event.pulse_last_row))
            if pulse:
                zones["pulse"] = pulse
            if event.rest_after_duration_s is not None:
                after = r > event.pulse_last_row
                if after.any():
                    first = float(t[after].min())
                    rest = extent(after & (t < first + event.rest_after_duration_s))
                    if rest:
                        zones["rest_after"] = rest
        if event.pre_pulse_row is not None and event.rest_before_duration_s is not None and "pulse" in zones:
            ref = r == event.pre_pulse_row
            if ref.any():
                start = zones["pulse"][0] - event.rest_before_duration_s     # rest segment start (core duration)
                rest = extent((t >= start) & (t <= float(t[ref][0])))
                if rest:
                    zones["rest_before"] = rest
        return zones

    def dcir_window(self, elab_result_id: str, center_s: float, span_s: float, units: UnitPrefs) -> PulseTrace:
        return self._trace_for_rows(elab_result_id, center_s - span_s / 2, center_s + span_s / 2, units, None, center_s)

    def correction_spans(self, dcir_published_id: str, units: UnitPrefs) -> list:
        """Spans of the Accepted measurements of a published DCIR result, with their start/end times in the display
        time unit (read from the elaborated points by source row) so the Correction overview can mark them.
        Auto: pulse rows; manual: the correction rows, else the two boundary samples; neither -> no span."""
        events = self._dcir_events.get(dcir_published_id, ())
        measurements = {m.event.event_id: m for m in self._dcir_measurements.get(dcir_published_id, ())}
        labels = gregorithm.event_labels(events)
        rows: dict[str, tuple[int | None, int | None]] = {}
        for event in events:
            if event.state is not gregorithm.ReviewState.ACCEPTED:
                continue
            start, end = event.correction_start_row, event.correction_end_row
            boundary = getattr(measurements.get(event.event_id), "boundary_samples", None)
            if start is None and boundary is not None:
                start, end = boundary[0].source_row, boundary[1].source_row
            if start is None and event.pulse_first_row is not None:
                start, end = event.pulse_first_row, event.pulse_last_row
            rows[event.event_id] = (start, end)
        times = self._row_times(self._dcir_elaboration.get(dcir_published_id),
                                {r for pair in rows.values() for r in pair if r is not None})
        factor = scale(TIME, units.time)
        out = []
        for event in events:
            if event.event_id not in rows:
                continue
            start, end = rows[event.event_id]
            t0, t1 = times.get(start), times.get(end)
            out.append(Span(event.event_id, labels.get(event.event_id, "DCIR"), event.source.value, start, end,
                            t0 * factor if t0 is not None else None, t1 * factor if t1 is not None else None,
                            cycle=event.cycle, polarity=event.polarity.value if event.polarity else None,
                            pulse_duration_s=event.pulse_duration_s))
        return out

    def _row_times(self, elab_result_id: str | None, source_rows: set[int]) -> dict[int, float]:
        """time_s of the given source rows of an elaboration ({} when it is unknown or no row is asked)."""
        if elab_result_id is None or not source_rows:
            return {}
        try:
            path = self._result_view(elab_result_id).files["points"]
        except (KeyError, FileNotFoundError):
            return {}
        table = pq.read_table(path, columns=["source_row", "time_s"],
                              filters=[("source_row", "in", sorted(int(r) for r in source_rows))])
        return {int(r): float(t) for r, t in zip(table["source_row"].to_pylist(), table["time_s"].to_pylist())
                if t is not None}

    def correction_overview(self, elab_result_id: str, units: UnitPrefs, width_px: int) -> Curve:
        """Voltage over time of the whole test for the Cycle Correction overview, sampled like the Data Explorer
        (gregorithm.read_window with a point budget of 4 real rows per plot pixel: first/last rows and the voltage
        extremes are always kept). The overview is an indicative, non-interactive picture, so it never needs every
        point; reading all of them made the DCIR page slow to show and to leave on long tests."""
        view = self._result_view(elab_result_id)
        columns = ("time_s", "voltage_v", "source_row")
        total = view.parquets["points"].metadata.num_rows
        budget = max(1_000, min(100_000, int(width_px) * 4))
        table = gregorithm.read_window(gregorithm.WindowRequest(view.result_path, columns=columns,
                                                                point_budget=min(total, budget) or None))
        x, y = _numeric(table["time_s"]) * scale(TIME, units.time), _numeric(table["voltage_v"]) * scale(VOLTAGE, units.voltage)
        mask = np.isfinite(x) & np.isfinite(y)
        return Curve(x[mask], y[mask], _numeric(table["source_row"]).astype(int)[mask])

    def recalculate_cycles(self, ds_key: str, elab_result_id: str, dcir_published_id: str, params: CorrParams,
                           progress, cancel, existing_dqdv_result_id: str | None = None) -> CorrResult:
        events = self._dcir_events.get(dcir_published_id)
        measurements = self._dcir_measurements.get(dcir_published_id, ())
        if events is None:
            raise KeyError("The saved DCIR result is no longer available")
        base = self._result_view(elab_result_id)
        order = gregorithm.CycleOrder.CHARGE_DISCHARGE if params.cycle_order == CHARGE_FIRST else gregorithm.CycleOrder.DISCHARGE_CHARGE
        cap = self._core_capacity_params(params.capacity) if params.capacity is not None else gregorithm.CapacityParameters(cycle_order=order)
        existing_dqdv_path = None
        if existing_dqdv_result_id is not None:
            if self._dqdv_elaboration.get(existing_dqdv_result_id) != elab_result_id:
                raise ValueError("The dQ/dV result does not belong to the elaboration being corrected")
            existing_dqdv_path = self._dqdv_path(existing_dqdv_result_id)
        request = gregorithm.CycleCorrectionRequest(
            elaboration_path=base.result_path,
            elaboration_fingerprint=self._elab_result_fingerprint(elab_result_id)["fingerprint"],
            caller_root=self.caller_root,
            sample_id=f"{ds_key}.correction",
            job_id=uuid.uuid4().hex,
            generation_id=uuid.uuid4().hex,
            events=tuple(events),
            measurements=tuple(measurements),
            parameters=gregorithm.CycleCorrectionParameters(
                params.enabled, params.rest_current_threshold / scale(CURRENT, params.threshold_unit), order, cap),
            existing_dqdv_path=existing_dqdv_path,
        )
        def on_progress(event):
            publishing = event.total is not None and event.completed >= event.total
            progress(Progress(None if event.total is None else event.completed / max(1, event.total),
                "Saving result…" if publishing else event.phase.replace("_", " ").title(), event.completed, event.total, publishing))
        try:
            core = gregorithm.recalculate_cycles(request, progress=on_progress, cancel=cancel)
        except gregorithm.OperationCancelled as exc:
            raise CorrectionCancelled() from exc
        # The core publishes the corrected elaboration, Capacity table, and DCIR records
        # as one generation.  Register each view under the shared generation id.
        corrected = _ElabView(core.ref.path)
        rid = core.ref.generation_id
        with self._lock:
            self._elab_views[rid] = corrected
            self._result_ds[rid] = ds_key
            self._elab_params[rid] = self._elab_params[elab_result_id]
            self._capacity_paths[rid] = core.ref.path
            self._capacity_ds[rid] = ds_key
            self._dcir_paths[rid] = core.ref.path
            self._dcir_elaboration[rid] = rid
        self.result_cycles(rid)                  # warm the cycle cache on the worker thread
        event_file = core.ref.path / "tables" / "dcir_events.json"
        corrected_events = self._read_dcir_events(event_file) if event_file.exists() else tuple(events)
        measurements = self._corrected_measurements(core.ref.path / "tables" / "dcir_measurements.json",
                                                    tuple(measurements), corrected_events)
        with self._lock:
            self._dcir_events[rid] = corrected_events
            self._dcir_measurements[rid] = measurements
        elab = ElabResult(rid, core.cycles, core.half_cycles, corrected.total("segments"), core.rows,
                          self._elab_params[elab_result_id],
                          details=(("Correction", "DCIR" if params.enabled else "Removed"),),
                          correction="dcir" if params.enabled else "removed")
        capacity = CapResult(rid, rid, core.cycles, params.capacity, None) if (core.ref.path / "tables" / "capacity.parquet").exists() else None
        published = PublishedResult(rid, tuple(self._event_view(e, self._units.get(ds_key, UnitPrefs())) for e in corrected_events),
            tuple(self._measurement_view(m, self._units.get(ds_key, UnitPrefs())) for m in measurements), 10.0,
            tuple(gregorithm.event_labels(corrected_events).items()), "", gregorithm.accepted_event_fingerprint(corrected_events, tuple(measurements)))
        dqdv = None
        dqdv_table = core.ref.path / "tables" / "dqdv.parquet"
        if existing_dqdv_result_id is not None and dqdv_table.exists():
            previous_params = self._dqdv_params[existing_dqdv_result_id]
            corrected_dcir_fingerprint = gregorithm.accepted_event_fingerprint(corrected_events, tuple(measurements))
            corrected_span_count = len(gregorithm.accepted_correction_spans(corrected_events, tuple(measurements)))
            parquet = pq.ParquetFile(dqdv_table)
            point_count = parquet.metadata.num_rows
            table = pq.read_table(dqdv_table, columns=["cycle", "half_cycle", "phase", "anchor_source_row"])
            curve_count = len(set(zip(
                table["cycle"].to_pylist(), table["half_cycle"].to_pylist(),
                table["phase"].to_pylist(), table["anchor_source_row"].to_pylist(),
            )))
            with self._lock:
                self._dqdv_paths[rid] = core.ref.path
                self._dqdv_ds[rid] = ds_key
                self._dqdv_elaboration[rid] = rid
                self._dqdv_params[rid] = previous_params
                self._dqdv_dcir_fingerprint[rid] = corrected_dcir_fingerprint
                self._dqdv_span_count[rid] = corrected_span_count
                # Metrics follow each curve by its anchor row; Cycle Correction only re-labels the curves.
                # Curves without any reconstructed point keep no stale labels (they are re-listed only on Reanalyze).
                self._dqdv_metrics[rid] = {a: m[:3] + (None,) for a, m in
                                           self._dqdv_metrics.get(existing_dqdv_result_id, {}).items()}
            dqdv = DqdvResult(rid, rid, previous_params, curve_count, point_count, (), corrected_dcir_fingerprint,
                              corrected_span_count, params.enabled,
                              (("Correction", "DCIR relabelled; derivative values were not recomputed"),))
        return CorrResult(rid, params.enabled, elab, capacity, published, core.spans, params.rest_current_threshold,
                          params.threshold_unit, report=(("dcir_redetected", "false"),
                                                         ("dqdv_recomputed", "false")), dqdv=dqdv)

    @classmethod
    def _corrected_measurements(cls, path: Path, measurements: tuple, events: tuple) -> tuple:
        """Return measurements whose event records match the corrected events.

        Cycle correction re-associates every DCIR event with its corrected cycle and refreshes the
        optional SOC, while resistance values stay unchanged. The core requires each measurement
        to embed exactly its event record, so a later Reapply/Remove must receive the corrected
        records. SOC fields are taken from the published ``dcir_measurements.json`` when present.
        """
        import dataclasses
        by_id = {event.event_id: event for event in events}
        published = {}
        if path.exists():
            published = {row["event_id"]: row for row in cls._read_json_list(path)}
        out = []
        for measurement in measurements:
            event = by_id.get(measurement.event.event_id, measurement.event)
            changes = {"event": event}
            row = published.get(event.event_id)
            if row is not None:
                context = row.get("soc_context")
                changes["soc_pct"] = row.get("soc_pct")
                changes["soc_context"] = None if context is None else gregorithm.SocContext(
                    **{**context, "direction": gregorithm.PulsePolarity(context["direction"])})
                changes["diagnostics"] = tuple(gregorithm.Diagnostic.from_dict(item) for item in row.get("diagnostics", ()))
            out.append(dataclasses.replace(measurement, **changes))
        return tuple(out)

    @staticmethod
    def _read_json_list(path: Path) -> list:
        import json
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)

    def export_dcir(self, published_id: str, columns: list[str], units: UnitPrefs, fmt: str, path: str, progress,
                    cancel) -> list[ExportDone]:
        """Write the published DCIR measurements (one row per measurement, the Results table) with the chosen
        ``columns`` (RESULT_COLUMNS keys; the Pulse label is always included) in the display ``units``.
        The values are those gregorithm published; empty cells stay empty. Never overwrites a file."""
        published = self.dcir_published(published_id, units)
        keys = [key for key, _t, _q in RESULT_COLUMNS if key in columns or key == "label"]
        titles = dict(zip((key for key, _t, _q in RESULT_COLUMNS), result_heads(units)))
        rows = [row for row, _tip in result_rows(published)]
        progress(Progress(0.0, "Writing export", 0, len(rows)))
        receipt = _write_columns(Path(path), fmt, {titles[k]: [row[k] for row in rows] for k in keys}, cancel)
        progress(Progress(1.0, "Export complete", len(rows), len(rows)))
        return [receipt]

    def export_elab(self, result_id: str, kind: str, units: UnitPrefs | None, fmt: str, path: str, progress,
                    cancel) -> list[ExportDone]:
        """Write a published elaboration table (``kind``: 'points' = elaborated data, 'segments' = detected
        segments) with every column of the Elaborated Data view, in the display ``units`` and at full precision.
        Streams one row group at a time, so memory stays bounded on large files. Never overwrites a file; a
        cancelled or failed export leaves nothing behind. ``units`` None = the units of the Elaborated Data view."""
        view = self._result_view(result_id)
        if units is None:
            units = self._units.get(self._result_ds.get(result_id, ""), UnitPrefs())
        names = view.source_columns(kind)
        titles = [f"{c.name} [{units.unit(_COLUMN_QUANTITY[s]) if s in _COLUMN_QUANTITY else c.unit}]"
                  if (s in _COLUMN_QUANTITY or c.unit) else c.name
                  for s, c in zip(names, view.columns(kind))]
        parquet = view.parquets[kind]
        total = view.total(kind)
        destination = Path(path)
        if destination.exists():
            raise FileExistsError(f"The destination already exists: {destination}")
        if fmt.lower() not in ("csv", "parquet"):
            raise ValueError("Export format must be CSV or Parquet")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + f".{uuid.uuid4().hex}.tmp")
        writer = None
        done = 0
        try:
            for batch in parquet.iter_batches(batch_size=65_536, columns=list(names)):
                if cancel.is_set():
                    raise ExportCancelled()
                arrays = []
                for name in names:
                    column = batch.column(batch.schema.get_field_index(name))
                    factor = _factor(name, units)
                    arrays.append(column if factor == 1.0 else pc.multiply(pc.cast(column, pa.float64()), factor))
                table = pa.Table.from_arrays(arrays, names=titles)
                if writer is None:
                    if fmt.lower() == "parquet":
                        writer = pq.ParquetWriter(temporary, table.schema, compression="zstd")
                    else:
                        import pyarrow.csv as pacsv
                        writer = pacsv.CSVWriter(temporary, table.schema)
                writer.write_table(table)
                done += batch.num_rows
                progress(Progress(done / max(1, total), "Writing export", done, total))
            if writer is None:                                   # empty table: header only
                schema = pa.schema([pa.field(t, pa.float64()) for t in titles])
                if fmt.lower() == "parquet":
                    writer = pq.ParquetWriter(temporary, schema)
                else:
                    import pyarrow.csv as pacsv
                    writer = pacsv.CSVWriter(temporary, schema)
            writer.close()
            writer = None
            if cancel.is_set():
                raise ExportCancelled()
            os.replace(temporary, destination)
        except BaseException:
            if writer is not None:
                writer.close()
            temporary.unlink(missing_ok=True)
            raise
        return [ExportDone(str(destination), done, destination.stat().st_size,
                           hashlib.sha256(destination.read_bytes()).hexdigest())]

    def release_dcir(self, dcir_id: str) -> None:
        with self._lock:
            self._dcir_paths.pop(dcir_id, None); self._dcir_events.pop(dcir_id, None)
            self._dcir_measurements.pop(dcir_id, None); self._dcir_elaboration.pop(dcir_id, None)

    def _result_view(self, result_id: str) -> _ElabView:
        with self._lock:
            view = self._elab_views.get(result_id)
        if view is None:
            raise KeyError("Elaboration result is no longer available")
        return view

    def result_cycles(self, result_id: str) -> list[int]:
        """Sorted distinct cycle numbers of a published elaboration.

        Published elaborations are immutable, so the list is computed once per result (one vectorised pass over the
        ``cycle`` column) and cached until :meth:`release_result`. The GUI asks for it on every dQ/dV form refresh;
        rescanning the points table each time froze the window for seconds on files with ~10^5 rows. A copy is
        returned so callers cannot alter the cache."""
        with self._lock:
            cached = self._cycles_cache.get(result_id)
        if cached is None:
            parquet = self._result_view(result_id).parquets["points"]
            found = [np.unique(_numeric(batch.column(0)))
                     for batch in parquet.iter_batches(batch_size=131_072, columns=["cycle"])]
            values = np.unique(np.concatenate(found)) if found else np.empty(0)
            cached = tuple(int(v) for v in values[np.isfinite(values)])
            with self._lock:
                if result_id in self._elab_views:        # do not resurrect an entry for a released result
                    self._cycles_cache[result_id] = cached
        return list(cached)

    def cycle_spans(self, result_id: str) -> list[tuple[int, float, float]]:
        """(cycle, first time [s], last time [s]) of every cycle of the published elaboration, read from its real
        samples; used to jump the manual DCIR chart to a cycle. One streaming pass over two columns."""
        parquet = self._result_view(result_id).parquets["points"]
        spans: dict[int, list[float]] = {}
        for batch in parquet.iter_batches(batch_size=131_072, columns=["cycle", "time_s"]):
            cyc = _numeric(batch.column(0))
            t = _numeric(batch.column(1))
            ok = np.isfinite(cyc) & np.isfinite(t)
            cyc, t = cyc[ok].astype(int), t[ok]
            for c in np.unique(cyc):
                tc = t[cyc == c]
                lo, hi = float(tc.min()), float(tc.max())
                cur = spans.get(int(c))
                spans[int(c)] = [lo, hi] if cur is None else [min(cur[0], lo), max(cur[1], hi)]
        return [(c, lo, hi) for c, (lo, hi) in sorted(spans.items())]

    @staticmethod
    def _batch_values(batch: pa.RecordBatch, name: str, units: UnitPrefs) -> np.ndarray:
        return _numeric(batch.column(batch.schema.get_field_index(name))) * _factor(name, units)

    @staticmethod
    def _batch_mask(batch: pa.RecordBatch, req: ExplorerRequest, x: np.ndarray,
                    y1: np.ndarray, y2: np.ndarray | None) -> tuple[np.ndarray, int]:
        mask = np.isfinite(x)
        if req.cycles is not None:
            cycles = np.asarray(batch.column(batch.schema.get_field_index("cycle")).to_numpy(zero_copy_only=False))
            mask &= np.isin(cycles, np.asarray(req.cycles))
        if req.x_range is not None:
            mask &= (x >= req.x_range[0]) & (x <= req.x_range[1])
        drawable = np.isfinite(y1)
        if y2 is not None:
            drawable |= np.isfinite(y2)
        missing = int(np.count_nonzero(mask & ~drawable))
        return mask & drawable, missing

    def _scan_view(self, req: ExplorerRequest, cancel, collect_extrema: bool = False):
        """Bounded first pass: exact eligible unique-row count and full-data ranges."""
        view = self._result_view(req.result_id)
        names = list(dict.fromkeys([req.x, req.y1] + ([req.y2] if req.y2 else []) +
                                   ["cycle", "source_row"]))
        total = missing = 0
        ranges = {key: [math.inf, -math.inf] for key in (req.x, req.y1) + ((req.y2,) if req.y2 else ())}
        extrema_rows: set[int] = set()
        for batch in view.parquets["points"].iter_batches(batch_size=131_072, columns=names):
            if cancel.is_set():
                raise RenderCancelled()
            x = self._batch_values(batch, req.x, req.units)
            y1 = self._batch_values(batch, req.y1, req.units)
            y2 = self._batch_values(batch, req.y2, req.units) if req.y2 else None
            mask, miss = self._batch_mask(batch, req, x, y1, y2)
            missing += miss
            total += int(np.count_nonzero(mask))
            rows = np.asarray(batch.column(batch.schema.get_field_index("source_row")).to_numpy(zero_copy_only=False))
            for key, values in ((req.x, x), (req.y1, y1), *(((req.y2, y2),) if req.y2 else ())):
                valid = mask & np.isfinite(values)
                if not np.any(valid):
                    continue
                selected = values[valid]
                lo_i, hi_i = int(np.argmin(selected)), int(np.argmax(selected))
                source = rows[valid]
                ranges[key][0] = min(ranges[key][0], float(selected[lo_i]))
                ranges[key][1] = max(ranges[key][1], float(selected[hi_i]))
                if collect_extrema:
                    extrema_rows.update((int(source[lo_i]), int(source[hi_i])))
        return total, missing, ranges, extrema_rows

    @staticmethod
    def _safe_range(values: list[float], requested: tuple[float, float] | None) -> tuple[float, float]:
        if requested is not None:
            return requested
        lo, hi = values
        if not (math.isfinite(lo) and math.isfinite(hi)):
            return (0.0, 1.0)
        if lo == hi:
            pad = max(abs(lo) * 0.01, 1e-12)
            return (lo - pad, hi + pad)
        return (lo, hi)

    def _selected_non_time(self, req: ExplorerRequest, total: int, extrema_rows: set[int], cancel):
        """Second bounded pass for arbitrary non-monotonic X; selection follows canonical row order."""
        budget = max(1_000, min(100_000, req.plot_width_px * 4))
        targets = set(np.linspace(0, total - 1, min(total, budget), dtype=np.int64).tolist()) if total else set()
        view = self._result_view(req.result_id)
        names = list(dict.fromkeys([req.x, req.y1] + ([req.y2] if req.y2 else []) +
                                   ["cycle", "source_row"]))
        picked: list[tuple[int, float, float, float]] = []
        rank = 0
        for batch in view.parquets["points"].iter_batches(batch_size=131_072, columns=names):
            if cancel.is_set():
                raise RenderCancelled()
            x = self._batch_values(batch, req.x, req.units)
            y1 = self._batch_values(batch, req.y1, req.units)
            y2 = self._batch_values(batch, req.y2, req.units) if req.y2 else None
            mask, _ = self._batch_mask(batch, req, x, y1, y2)
            rows = np.asarray(batch.column(batch.schema.get_field_index("source_row")).to_numpy(zero_copy_only=False))
            for i in np.flatnonzero(mask):
                row = int(rows[i])
                if rank in targets or row in extrema_rows:
                    picked.append((row, float(x[i]), float(y1[i]), float(y2[i]) if y2 is not None else math.nan))
                rank += 1
        picked.sort(key=lambda item: item[0])
        return picked

    def explorer_view(self, req: ExplorerRequest, cancel) -> ExplorerView:
        total, missing, ranges, extrema = self._scan_view(req, cancel, collect_extrema=req.x != "time_s")
        if total == 0:
            empty = Curve(np.array([], dtype=float), np.array([], dtype=float), np.array([], dtype=np.uint64))
            return ExplorerView(empty, empty if req.y2 else None, 0, 0,
                                self._safe_range(ranges[req.x], req.x_range),
                                self._safe_range(ranges[req.y1], req.y1_range),
                                self._safe_range(ranges[req.y2], req.y2_range) if req.y2 else None,
                                null_excluded=missing)
        if req.x == "time_s":
            view = self._result_view(req.result_id)
            columns = tuple(dict.fromkeys((req.x, req.y1) + ((req.y2,) if req.y2 else ()) +
                                          ("cycle", "source_row")))
            kwargs = {}
            if req.x_range is not None:
                factor = _factor("time_s", req.units)
                kwargs = {"start_time_s": req.x_range[0] / factor, "end_time_s": req.x_range[1] / factor}
            budget = max(1_000, min(100_000, req.plot_width_px * 4))
            table = gregorithm.read_window(gregorithm.WindowRequest(view.result_path, cycles=req.cycles or (),
                                                                 columns=columns, point_budget=min(total, budget), **kwargs))
            if cancel.is_set():
                raise RenderCancelled()
            x = _numeric(table[req.x]) * _factor(req.x, req.units)
            y1 = _numeric(table[req.y1]) * _factor(req.y1, req.units)
            y2 = _numeric(table[req.y2]) * _factor(req.y2, req.units) if req.y2 else None
            mask = np.isfinite(x) & (np.isfinite(y1) | (np.isfinite(y2) if y2 is not None else False))
            rows = np.asarray(table["source_row"].to_numpy(zero_copy_only=False), dtype=np.uint64)[mask]
            x, y1 = x[mask], y1[mask]
            if y2 is not None:
                y2 = y2[mask]
        else:
            picked = self._selected_non_time(req, total, extrema, cancel)
            rows = np.asarray([p[0] for p in picked], dtype=np.uint64)
            x = np.asarray([p[1] for p in picked], dtype=float)
            y1 = np.asarray([p[2] for p in picked], dtype=float)
            y2 = np.asarray([p[3] for p in picked], dtype=float) if req.y2 else None
        y1_curve = Curve(x, y1, rows)
        y2_curve = Curve(x, y2, rows) if y2 is not None else None
        return ExplorerView(y1_curve, y2_curve, total, len(rows),
                            self._safe_range(ranges[req.x], req.x_range),
                            self._safe_range(ranges[req.y1], req.y1_range),
                            self._safe_range(ranges[req.y2], req.y2_range) if req.y2 else None,
                            null_excluded=missing)

    def point_details(self, result_id: str, source_row: int, units: UnitPrefs) -> dict[str, object]:
        view = self._result_view(result_id)
        if source_row < 0 or source_row >= view.total("points"):
            raise IndexError(source_row)
        values = view.rows("points", source_row, 1, units)[0]
        return dict(zip(_POINT_SOURCE, values))

    def render_all_points(self, req: ExplorerRequest, style: FlatStyle, progress, cancel) -> FlatImage:
        """Render every eligible row into a worker-safe, transparent data layer using bounded Parquet batches.

        The image covers exactly the plot area (`style.width_px` x `style.height_px` logical px, at
        max(device pixel ratio, dpi / 96) device px per logical px) and holds only the curves: the chart overlays it on
        its own axes, grid and legend, so the flat view looks like the interactive one."""
        total, _, ranges, _ = self._scan_view(req, cancel)
        xr = self._safe_range(ranges[req.x], req.x_range)
        y1r = self._safe_range(ranges[req.y1], req.y1_range)
        y2r = self._safe_range(ranges[req.y2], req.y2_range) if req.y2 else None
        image, painter, plot, width, height = data_layer(style)

        def point(xv: float, yv: float, yr: tuple[float, float]) -> QPointF:
            px = plot.left() + (xv - xr[0]) / (xr[1] - xr[0]) * plot.width()
            py = plot.bottom() - (yv - yr[0]) / (yr[1] - yr[0]) * plot.height()
            return QPointF(px, py)

        view = self._result_view(req.result_id)
        names = list(dict.fromkeys([req.x, req.y1] + ([req.y2] if req.y2 else []) + ["cycle"]))
        pens = tuple(QPen(QColor(c), style.line_width) for c in (style.y1_color, style.y2_color))
        dots = tuple(dot_pen(c) for c in (style.y1_color, style.y2_color))      # = the chart's 3 px symbols
        previous = [None, None]
        done = 0
        progress(Progress(0.0 if total else 1.0, "Rendering all points", 0, total))
        for batch in view.parquets["points"].iter_batches(batch_size=65_536, columns=names):
            if cancel.is_set():
                painter.end()
                raise RenderCancelled()
            x = self._batch_values(batch, req.x, req.units)
            y1 = self._batch_values(batch, req.y1, req.units)
            y2 = self._batch_values(batch, req.y2, req.units) if req.y2 else None
            mask, _ = self._batch_mask(batch, req, x, y1, y2)
            eligible = np.flatnonzero(mask)
            # Y2 first so Y1 is on top, as in the interactive chart
            for series, (values, yr) in reversed(list(enumerate(((y1, y1r), (y2, y2r) if y2 is not None else (None, None))))):
                if values is None or yr is None:
                    continue
                valid = eligible[np.isfinite(values[eligible])]
                if not len(valid):
                    previous[series] = None
                    continue
                painter.setPen(pens[series])
                pts = [point(float(x[i]), float(values[i]), yr) for i in valid]
                if previous[series] is not None:
                    pts.insert(0, previous[series])
                polygon = QPolygonF(pts)
                if style.style in ("lines", "lines_markers") and len(polygon) > 1:
                    painter.drawPolyline(polygon)
                if style.style in ("markers", "lines_markers"):
                    painter.setPen(dots[series])
                    painter.drawPoints(polygon)
                previous[series] = pts[-1]
            done += len(eligible)
            progress(Progress(done / total if total else 1.0, "Rendering all points", done, total))

        painter.end()
        dots_per_meter = math.ceil(style.dpi / 0.0254)
        image.setDotsPerMeterX(dots_per_meter)
        image.setDotsPerMeterY(dots_per_meter)
        if cancel.is_set():
            raise RenderCancelled()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise RuntimeError("The complete image could not be encoded as PNG.")
        buffer.close()
        return FlatImage(bytes(data), width, height, done, total)

    # --- Comparison: read each immutable result independently; never calculate cross-dataset science. ---
    def compare_explorer(self, req: CompareRequest, cancel) -> list[CompareSeries]:
        budget_width = max(240, int(req.plot_width_px / max(1, len(req.items))))
        out: list[CompareSeries] = []
        for ds_key, result_id in req.items:
            if cancel.is_set():
                raise RenderCancelled()
            view = self.explorer_view(ExplorerRequest(
                result_id, req.x, req.y, None, req.cycles, req.x_range, req.y_range, None,
                req.units, budget_width, 600,
            ), cancel)
            out.append(CompareSeries(ds_key, view.y1, view.total, view.rendered))
        return out

    def compare_render_all(self, req: CompareRequest, series: dict, style: FlatStyle, progress, cancel) -> FlatImage:
        """Stream all eligible real rows of every selected dataset into one publication image."""
        requests: list[tuple[str, ExplorerRequest]] = []
        scans = []
        for ds_key, result_id in req.items:
            item = ExplorerRequest(result_id, req.x, req.y, None, req.cycles, req.x_range,
                                   req.y_range, None, req.units, req.plot_width_px, style.height_px)
            requests.append((ds_key, item))
            scans.append(self._scan_view(item, cancel))
        total = sum(scan[0] for scan in scans)
        x_values = [bound for scan in scans for bound in scan[2][req.x] if math.isfinite(bound)]
        y_values = [bound for scan in scans for bound in scan[2][req.y] if math.isfinite(bound)]
        xr = req.x_range or ((min(x_values), max(x_values)) if x_values else (0.0, 1.0))
        yr = req.y_range or ((min(y_values), max(y_values)) if y_values else (0.0, 1.0))
        xr = self._safe_range(list(xr), req.x_range)
        yr = self._safe_range(list(yr), req.y_range)

        image, painter, plot, width, height = data_layer(style)

        def image_point(x_value: float, y_value: float) -> QPointF:
            return QPointF(
                plot.left() + (x_value - xr[0]) / (xr[1] - xr[0]) * plot.width(),
                plot.bottom() - (y_value - yr[0]) / (yr[1] - yr[0]) * plot.height(),
            )

        done = 0
        progress(Progress(0.0 if total else 1.0, "Rendering all datasets", 0, total))
        for ds_key, item in requests:
            color, _label = series[ds_key]
            line_pen, marker_pen = QPen(QColor(color), max(0.5, float(style.line_width))), dot_pen(color)
            painter.setPen(line_pen)
            view = self._result_view(item.result_id)
            names = list(dict.fromkeys([item.x, item.y1, "cycle", "source_row"]))
            previous = None
            previous_cycle = None
            for batch in view.parquets["points"].iter_batches(batch_size=65_536, columns=names):
                if cancel.is_set():
                    painter.end()
                    raise RenderCancelled()
                x = self._batch_values(batch, item.x, item.units)
                y = self._batch_values(batch, item.y1, item.units)
                mask, _ = self._batch_mask(batch, item, x, y, None)
                cycle_values = np.asarray(batch.column(batch.schema.get_field_index("cycle")).to_numpy(zero_copy_only=False))
                eligible = np.flatnonzero(mask & np.isfinite(x) & np.isfinite(y))
                segment: list[QPointF] = []
                for row_index in eligible:
                    cycle = int(cycle_values[row_index])
                    point = image_point(float(x[row_index]), float(y[row_index]))
                    if previous is not None and previous_cycle == cycle and not segment:
                        segment.append(previous)
                    if previous_cycle is not None and cycle != previous_cycle and segment:
                        if style.style in ("lines", "lines_markers") and len(segment) > 1:
                            painter.setPen(line_pen)
                            painter.drawPolyline(QPolygonF(segment))
                        if style.style in ("markers", "lines_markers"):
                            painter.setPen(marker_pen)
                            painter.drawPoints(QPolygonF(segment))
                        segment = []
                    segment.append(point)
                    previous, previous_cycle = point, cycle
                if segment:
                    polygon = QPolygonF(segment)
                    if style.style in ("lines", "lines_markers") and len(polygon) > 1:
                        painter.setPen(line_pen)
                        painter.drawPolyline(polygon)
                    if style.style in ("markers", "lines_markers"):
                        painter.setPen(marker_pen)
                        painter.drawPoints(polygon)
                done += len(eligible)
                progress(Progress(done / total if total else 1.0, "Rendering all datasets", done, total))

        painter.end()
        dots_per_meter = math.ceil(style.dpi / 0.0254)
        image.setDotsPerMeterX(dots_per_meter)
        image.setDotsPerMeterY(dots_per_meter)
        if cancel.is_set():
            raise RenderCancelled()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise RuntimeError("The complete comparison image could not be encoded as PNG")
        buffer.close()
        if done != total:
            raise RuntimeError(f"Comparison render coverage mismatch: {done} of {total} eligible rows")
        return FlatImage(bytes(data), width, height, done, total)

    def set_display_units(self, ds_key: str, units: UnitPrefs) -> None:
        with self._lock:
            self._units[ds_key] = units

    def set_sample_values(self, ds_key: str, values: SampleValues) -> None:
        """Session-only metadata for a future Capacity request; it never enters elaboration."""
        with self._lock:
            self._samples[ds_key] = values

    def data_columns(self, ds_key: str) -> list[tuple[str, str]]:
        units = self._units.get(ds_key, UnitPrefs())
        return [("Time", units.time), ("Current", units.current), ("Voltage", units.voltage)]
