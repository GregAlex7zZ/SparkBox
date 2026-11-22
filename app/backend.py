"""GUI-side backend contract.

IMPORTANT: these names are the GUI's own contract, NOT gregorithm's public API. `GregorithmBackend` maps each
method to the verified public API documented in `docs/DATA_01_CONTROL_API_MAP.md`. The GUI never
computes scientific results, never guesses units, never edits source files.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import threading
from typing import TYPE_CHECKING, Callable, Optional, Protocol

if TYPE_CHECKING:
    from .elaboration import ElabParams, ElabResult, SampleValues
    from .units import UnitPrefs


@dataclass
class SourceInspection:
    columns: list[str]                       # header names as found in the file (or generated if no header)
    preview_rows: list[list[str]]            # first rows, raw text, one list per row (all columns)
    delimiter: str = ";"
    encoding: str = "UTF-8"
    has_header: bool = True
    file_format: str = "CSV"
    ambiguity_reason: Optional[str] = None   # why Auto could not decide (shown in the dialog)
    suggested: Optional["ColumnMapping"] = None


@dataclass
class ColumnMapping:
    time_col: int
    current_col: int
    voltage_col: int
    time_unit: str = "s"                     # one of TIME_UNITS (including core duration format)
    current_unit: str = "A"
    voltage_unit: str = "V"
    has_header: bool = True


@dataclass
class ReportRow:
    column: str
    role: str                                # "Time" | "Current" | "Voltage" | "Not used"
    unit: str                                # source unit/format, e.g. "hh:mm:ss.fff → s"
    used: bool


@dataclass
class ImportResult:
    n_valid: int
    n_discarded: int
    duration_s: float
    method: str                              # "Auto Mapping" | "Manual Mapping"
    report: list[ReportRow] = field(default_factory=list)
    discard_summary: str = ""                # reasons exactly as reported by gregorithm
    # display columns for the table: [(header, unit_label)], values come from get_rows()
    display_columns: list[tuple[str, str]] = field(default_factory=list)
    canonical_path: Optional[Path] = None
    canonical_content_hash: str = ""
    canonical_generation_id: str = ""


@dataclass
class Progress:
    fraction: Optional[float]                # 0..1, None = indeterminate (total unknown)
    phase: str = ""
    done: Optional[int] = None
    total: Optional[int] = None
    publishing: bool = False


ProgressCb = Callable[[Progress], None]


@dataclass
class ElabColumn:
    name: str
    unit: str = ""
    kind: str = "float"
    is_time: bool = False


@dataclass
class ElabTable:
    columns: list[ElabColumn]
    total: int
    row_number_column: bool = True


@dataclass
class ExplorerRequest:
    result_id: str
    x: str
    y1: str
    y2: Optional[str]
    cycles: Optional[tuple[int, ...]]
    x_range: Optional[tuple[float, float]]
    y1_range: Optional[tuple[float, float]]
    y2_range: Optional[tuple[float, float]]
    units: "UnitPrefs"
    plot_width_px: int
    plot_height_px: int


@dataclass
class Curve:
    x: object
    y: object
    source_row: object


@dataclass
class ExplorerView:
    """Selected real rows. Counts are distinct eligible row identities, never Y-series totals."""
    y1: Curve
    y2: Optional[Curve]
    total: int
    rendered: int
    x_range: tuple[float, float]
    y1_range: tuple[float, float]
    y2_range: Optional[tuple[float, float]]
    filter_matched: bool = True
    null_excluded: int = 0


@dataclass
class FlatStyle:
    """Render All Points request. The backend draws a transparent DATA LAYER only (curves / markers, no axes, grid,
    labels or legend) covering exactly the plot area of `width_px` x `height_px` logical px, at
    max(device_pixel_ratio, dpi / 96) device px per logical px. The chart overlays it on its own axes, so the flat view
    and the saved image keep the interactive chart's ticks, formats and legend. Labels, legend and palette describe the
    chart for adapters that need them; the production adapter ignores them."""
    width_px: int
    height_px: int
    device_pixel_ratio: float
    x_label: str
    y1_label: str
    y2_label: Optional[str]
    y1_color: str
    y2_color: str
    legend: bool
    style: str
    palette: dict = field(default_factory=dict)
    font_family: str = "Inter"
    dpi: int = 300
    line_width: float = 1.5


@dataclass
class FlatImage:
    png: bytes
    width_px: int
    height_px: int
    drawn: int
    total: int


@dataclass
class CapacityTable:
    """Published Capacity data in display units; null numerics are NaN."""
    result_id: str
    values: dict
    diagnostics: list = field(default_factory=list)
    report: dict = field(default_factory=dict)


@dataclass
class ExportDone:
    path: str
    rows: int
    bytes: int
    sha256: str = ""


@dataclass
class CompareRequest:
    """One shared Explorer request applied independently to immutable dataset results."""
    items: tuple
    x: str
    y: str
    cycles: Optional[tuple]
    x_range: Optional[tuple[float, float]]
    y_range: Optional[tuple[float, float]]
    units: "UnitPrefs"
    plot_width_px: int


@dataclass
class CompareSeries:
    ds_key: str
    curve: Curve
    total: int
    rendered: int


@dataclass
class DqdvSegment:
    segment_index: int
    v: object
    raw: object
    smoothed: Optional[object] = None


@dataclass
class DqdvCurveData:
    cycle: int
    half_cycle: int
    phase: str
    anchor_source_row: int
    segments: list = field(default_factory=list)


@dataclass
class ReconCurveData:
    """Voltage reconstruction of one dQ/dV curve, in display units (gregorithm tables/dqdv_reconstruction.parquet).

    `measured_*` are the points the curve was calculated from; `rebuilt_*` the curve integrated back from dQ/dV.
    APE is in %; RMSE is in the display voltage unit; both None when gregorithm reports them unavailable (`code`)."""
    cycle: int
    half_cycle: int
    phase: str
    anchor_source_row: int
    measured_q: object
    measured_v: object
    rebuilt_q: object
    rebuilt_v: object
    ape_percent: Optional[float] = None
    rmse: Optional[float] = None
    code: str = ""


class ImportCancelled(Exception):
    pass


class ElabCancelled(Exception):
    """Raised after the core confirms cooperative elaboration cancellation."""


class CapacityCancelled(Exception):
    """Raised after `gregorithm.analyze_capacity` confirms cancellation."""


class ExportCancelled(Exception):
    """Raised when GUI presentation export leaves no committed output."""


@dataclass
class PulseTrace:
    event_id: Optional[str]
    t_rel_s: object
    current: object
    voltage: object
    rows: object
    time_display: object
    cycle: object
    t0_s: float = 0.0
    zones: dict = field(default_factory=dict)
    markers: dict = field(default_factory=dict)


class DcirCancelled(Exception):
    """Raised after a DCIR core operation confirms cancellation."""


class DqdvCancelled(Exception):
    """Raised after `gregorithm.analyze_dqdv` confirms cancellation."""


class CorrectionCancelled(Exception):
    """Raised after the correction core operation confirms cancellation."""


class RenderCancelled(Exception):
    """Raised when a complete static render is cancelled before publication."""


class AmbiguousMapping(Exception):
    def __init__(self, inspection: SourceInspection):
        super().__init__(inspection.ambiguity_reason or "Ambiguous column mapping")
        self.inspection = inspection


TIME_UNITS = ["s", "min", "h", "ms", "hh:mm:ss.fff"]
CURRENT_UNITS = ["A", "mA"]
VOLTAGE_UNITS = ["V", "mV"]
SUPPORTED_FILTER = "Battery data (*.csv *.txt *.parquet);;All files (*)"


class Backend(Protocol):
    def inspect_source(self, path: str, has_header: Optional[bool] = None) -> SourceInspection: ...
    def import_auto(self, path: str, ds_key: str, progress: ProgressCb, cancel: object) -> ImportResult: ...
    def import_mapped(self, path: str, mapping: ColumnMapping, progress: ProgressCb,
                      cancel: object, ds_key: str) -> ImportResult: ...
    def get_rows(self, ds_key: str, start: int, count: int) -> list[list[float]]: ...
    def find_row_by_time(self, ds_key: str, value: float) -> tuple[int, bool, float]: ...
    def release(self, ds_key: str) -> None: ...
    def elaborate(self, ds_key: str, params: "ElabParams", progress: ProgressCb,
                  cancel: object) -> "ElabResult": ...
    def elab_table(self, result_id: str, kind: str) -> ElabTable: ...
    def get_elab_rows(self, result_id: str, kind: str, start: int, count: int) -> list[list]: ...
    def find_elab_row_by_time(self, result_id: str, value: float) -> tuple[int, bool, float]: ...
    def release_result(self, result_id: str) -> None: ...
    def export_elab(self, result_id: str, kind: str, units: Optional["UnitPrefs"], fmt: str, path: str, progress: ProgressCb,
                    cancel: object) -> list[ExportDone]: ...
    def set_sample_values(self, ds_key: str, values: "SampleValues") -> None: ...
    def set_display_units(self, ds_key: str, units: "UnitPrefs") -> None: ...
    def data_columns(self, ds_key: str) -> list[tuple[str, str]]: ...
    def result_cycles(self, result_id: str) -> list[int]: ...
    def cycle_spans(self, result_id: str) -> list[tuple[int, float, float]]: ...
    def explorer_view(self, req: ExplorerRequest, cancel: threading.Event) -> ExplorerView: ...
    def point_details(self, result_id: str, source_row: int, units: "UnitPrefs") -> dict[str, object]: ...
    def render_all_points(self, req: ExplorerRequest, style: FlatStyle, progress: ProgressCb,
                          cancel: threading.Event) -> FlatImage: ...
    def analyze_capacity(self, ds_key: str, elab_result_id: str, params: object, progress: ProgressCb,
                         cancel: object) -> object: ...
    def capacity_table(self, cap_result_id: str, units: "UnitPrefs") -> CapacityTable: ...
    def export_capacity(self, cap_result_id: str, cycles: Optional[tuple[int, ...]], columns: list[str],
                        units: "UnitPrefs", fmt: str, path: str, include_diagnostics: bool,
                        progress: ProgressCb, cancel: object) -> list[ExportDone]: ...
    def release_capacity(self, cap_result_id: str) -> None: ...
    def detect_dcir(self, ds_key: str, elab_result_id: str, params: object, units: "UnitPrefs", progress: ProgressCb, cancel: object) -> object: ...
    def scan_gitt_ici(self, ds_key: str, elab_result_id: str, params: object) -> object: ...
    def release_gitt_ici(self, scope_id: str) -> None: ...
    def gitt_ici_dependencies(self, scope_id: str, params: object, ocv_overrides: dict,
                              ir_samples: Optional[dict] = None, ocv_samples: Optional[dict] = None,
                              ocv_fit_overrides: Optional[dict] = None) -> dict: ...
    def gitt_ici_rest_ocv(self, scope_id: str, params: object, ocv_overrides: dict, ocv_samples: dict,
                          ocv_fit_overrides: Optional[dict] = None) -> tuple: ...
    def gitt_ici_ir_drops(self, scope_id: str, params: object, ir_samples: dict,
                          event_ids: Optional[tuple] = None) -> dict: ...
    def analyze_gitt_ici(self, scope_id: str, params: object, previous: object, *, event_id: Optional[str],
                         series: tuple, windows: dict, ocv_overrides: dict, automatic: bool, invalidate: bool,
                         progress: ProgressCb, cancel: object, ir_samples: Optional[dict] = None,
                         ocv_samples: Optional[dict] = None, event_ids: Optional[tuple] = None,
                         only_stale: bool = False, ocv_fit_overrides: Optional[dict] = None) -> tuple: ...
    def gitt_ici_trace(self, scope_id: str, event_id: str) -> object: ...
    def export_gitt_ici(self, ds_key: str, elab_result_id: str, snapshot: object, params: object, files: list,
                        fmt: str, cancel: object) -> list[ExportDone]: ...
    def dcir_events(self, dcir_id: str, units: "UnitPrefs") -> tuple: ...
    def dcir_trace(self, dcir_id: str, event_id: str, units: "UnitPrefs", requested_time_s: Optional[float]) -> PulseTrace: ...
    def dcir_window(self, elab_result_id: str, center_s: float, span_s: float, units: "UnitPrefs") -> PulseTrace: ...
    def measure_dcir(self, dcir_id: str, event_ids: list[str], requested_time_s: float, units: "UnitPrefs", cancel: object,
                     current_time_s: Optional[float] = None) -> list: ...
    def preview_manual_dcir(self, dcir_id: str, values: dict, units: "UnitPrefs") -> object: ...
    def create_manual_dcir(self, dcir_id: str, values: dict, units: "UnitPrefs") -> tuple: ...
    def manual_measurement(self, dcir_id: str, event_id: str, units: "UnitPrefs") -> object: ...
    def publish_dcir(self, dcir_id: str, decisions: dict, measurement_time_s: float, units: "UnitPrefs", progress: ProgressCb, cancel: object,
                     current_time_s: Optional[float] = None, event_times: Optional[dict] = None) -> object: ...
    def dcir_published(self, published_id: str, units: "UnitPrefs") -> object: ...
    def export_dcir(self, published_id: str, columns: list[str], units: "UnitPrefs", fmt: str, path: str, progress: ProgressCb, cancel: object) -> list[ExportDone]: ...
    def release_dcir(self, dcir_id: str) -> None: ...
    def analyze_dqdv(self, ds_key: str, elab_result_id: str, params: object,
                     dcir_published_id: Optional[str], progress: ProgressCb, cancel: object) -> object: ...
    def dqdv_curves(self, dqdv_result_id: str, units: "UnitPrefs") -> list[DqdvCurveData]: ...
    def dqdv_reconstruction(self, dqdv_result_id: str, units: "UnitPrefs") -> list[ReconCurveData]: ...
    def export_dqdv(self, dqdv_result_id: str, cycles: tuple, phases: tuple, columns: list[str],
                    units: "UnitPrefs", fmt: str, path: str, progress: ProgressCb,
                    cancel: object) -> list[ExportDone]: ...
    def release_dqdv(self, dqdv_result_id: str) -> None: ...
    def compare_explorer(self, req: CompareRequest, cancel: threading.Event) -> list[CompareSeries]: ...
    def compare_render_all(self, req: CompareRequest, series: dict, style: FlatStyle,
                           progress: ProgressCb, cancel: threading.Event) -> FlatImage: ...
    def correction_spans(self, dcir_published_id: str, units: "UnitPrefs") -> list: ...
    def correction_overview(self, elab_result_id: str, units: "UnitPrefs", width_px: int) -> Curve: ...
    def recalculate_cycles(self, ds_key: str, elab_result_id: str, dcir_published_id: str, params: object,
                           progress: ProgressCb, cancel: object,
                           existing_dqdv_result_id: Optional[str] = None) -> object: ...


