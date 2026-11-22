"""Capacity section: column catalogue of tables/capacity.parquet (17 columns), diagnostic texts, Reference Cycle
validation, analysis run state and chart/table view state. PURE PYTHON (no Qt), unit-tested in tests/test_capacity.py.

Every number comes from gregorithm.analyze_capacity (the Capacity generation). The GUI never aggregates half-cycles, never computes
efficiencies, C-rates, normalisations, retention or fade, never fills a null with zero.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Optional

from .elaboration import CYCLE_ORDER_LABELS, Outcome, Run, SampleValues
from .explorer import (CHART, FilterError, Viewport, parse_cycle_filter, resolve_cycles)
from .units import CAPACITY, ENERGY, UnitPrefs

# ---------------------------------------------------------------------------------------------------------------------
# Columns (CAPACITY_SCHEMA). Labels for the normalised columns follow gregorithm.capacity_descriptor(order).


@dataclass(frozen=True)
class CapColumn:
    key: str
    label: str
    quantity: Optional[str] = None      # CAPACITY / ENERGY -> display unit from Settings > Units
    unit: str = ""                      # fixed unit when quantity is None ("%", "C", "mAh/g", ...)
    basis: str = ""                     # "mass" | "area" | "" : which Sample value the column needs


CAP_COLUMNS: tuple[CapColumn, ...] = (
    CapColumn("cycle", "Cycle"),
    CapColumn("charge_capacity_ah", "Charge Capacity", CAPACITY),
    CapColumn("discharge_capacity_ah", "Discharge Capacity", CAPACITY),
    CapColumn("coulombic_efficiency_pct", "Coulombic Efficiency", unit="%"),
    CapColumn("charge_energy_wh", "Charge Energy", ENERGY),
    CapColumn("discharge_energy_wh", "Discharge Energy", ENERGY),
    CapColumn("energy_efficiency_pct", "Energy Efficiency", unit="%"),
    CapColumn("charge_c_rate", "Charge C-rate", unit="C"),
    CapColumn("discharge_c_rate", "Discharge C-rate", unit="C"),
    CapColumn("charge_capacity_mah_g", "Charge Specific Capacity", unit="mAh/g", basis="mass"),
    CapColumn("discharge_capacity_mah_g", "Discharge Specific Capacity", unit="mAh/g", basis="mass"),
    CapColumn("charge_capacity_mah_cm2", "Charge Areal Capacity", unit="mAh/cm²", basis="area"),
    CapColumn("discharge_capacity_mah_cm2", "Discharge Areal Capacity", unit="mAh/cm²", basis="area"),
    CapColumn("charge_energy_wh_kg", "Charge Specific Energy", unit="Wh/kg", basis="mass"),
    CapColumn("discharge_energy_wh_kg", "Discharge Specific Energy", unit="Wh/kg", basis="mass"),
    CapColumn("charge_energy_mwh_cm2", "Charge Areal Energy", unit="mWh/cm²", basis="area"),
    CapColumn("discharge_energy_mwh_cm2", "Discharge Areal Energy", unit="mWh/cm²", basis="area"),
)
CAP_BY_KEY = {c.key: c for c in CAP_COLUMNS}
CAP_DEFAULT_X, CAP_DEFAULT_Y1, CAP_DEFAULT_Y2 = "cycle", "discharge_capacity_ah", "coulombic_efficiency_pct"


def cap_unit(key: str, units: UnitPrefs) -> str:
    c = CAP_BY_KEY[key]
    return units.unit(c.quantity) if c.quantity else c.unit


def cap_label(key: Optional[str], units: UnitPrefs) -> str:
    if key is None:
        return "None"
    u = cap_unit(key, units)
    return f"{CAP_BY_KEY[key].label} [{u}]" if u else CAP_BY_KEY[key].label


def selectable_columns(with_values: set[str]) -> list[str]:
    """Columns offered in X / Y1 / Y2: Cycle always, every other column only when the current result has at
    least one value in it (a column that is entirely null, e.g. specific values without a mass, is not listed)."""
    return [c.key for c in CAP_COLUMNS if c.key == "cycle" or c.key in with_values]


def missing_bases(params: Optional["CapParams"]) -> set[str]:
    """Normalisation bases ("mass", "area") the Capacity result was calculated without.

    A specific (per mass) or areal (per area) column cannot have values when its Sample value was not entered, so the
    Capacity table, the export dialog and the axis menus leave those columns out . A zero or
    missing value counts as not entered. Without a result (``params`` None) nothing is reported missing."""
    if params is None:
        return set()
    missing = set()
    if not params.active_material_mass_mg:
        missing.add("mass")
    if not params.electrode_area_cm2:
        missing.add("area")
    return missing


def missing_in_all(params_list: list) -> set[str]:
    """Bases missing from every Capacity result of ``params_list`` (Comparison overlays several datasets): a specific
    or areal quantity is offered when at least one dataset has its Sample value. An empty list reports nothing
    missing (no dataset is drawn yet)."""
    if not params_list:
        return set()
    missing = {"mass", "area"}
    for params in params_list:
        missing &= missing_bases(params)
    return missing


def shown_columns(params: Optional["CapParams"]) -> list[str]:
    """Column keys of the Capacity table and export, in table order, without the specific/areal columns whose Sample
    value (Active Material Mass, Surface Area) was not entered for the shown result."""
    missing = missing_bases(params)
    return [c.key for c in CAP_COLUMNS if c.basis not in missing]


def hidden_note(missing: set[str]) -> str:
    """Short reminder shown when specific or areal values are left out because the Sample value was not entered
    (empty when nothing is missing). ``missing`` is the result of missing_bases()."""
    wanted = []
    if "mass" in missing:
        wanted.append(("Active Material Mass", "specific"))
    if "area" in missing:
        wanted.append(("Surface Area", "areal"))
    if not wanted:
        return ""
    fields = " and ".join(name for name, _kind in wanted)
    kinds = " and ".join(kind for _name, kind in wanted)
    return f"Tip: enter {fields} in Data Elaboration (Sample) to also get {kinds} capacity and energy."


# ---------------------------------------------------------------------------------------------------------------------
# Diagnostics (tables/capacity_diagnostics.parquet: cycle, column, code). Texts are DESIGNER DRAFTS, to be checked by the
# reviewer against gregorithm.diagnostics. Unknown codes are shown as the raw code: the reason is never invented.

DIAGNOSTIC_TEXT = {
    "CAPACITY_MISSING_PHASE": "The {phase} phase of this cycle is missing (incomplete cycle).",
    "CAPACITY_ZERO_DENOMINATOR": "Not available: the value it is divided by is zero.",
    "CAPACITY_NO_ACTIVE_PAIR_CYCLE_ZERO": "Not available for the preliminary cycle 0 (no charge/discharge pair).",
    "INTEGRATION_ENERGY_UNAVAILABLE": "Energy is not available for this phase (energy integration was not valid).",
    "NORMALIZATION_BASIS_MISSING": "{basis} is not set for this dataset.",
    "NUMERIC_RESULT_UNREPRESENTABLE": "The result cannot be represented as a number.",
    "C_RATE_REFERENCE_UNAVAILABLE": "C-rate not available: the reference cycle capacity is missing or not valid.",
    "C_RATE_CC_UNAVAILABLE": "C-rate not available: this phase has no usable constant-current (CC) portion.",
}
NULL_TEXT = "—"


def explain(code: str, column: str) -> str:
    """Tooltip text of a null cell; `column` is the engine column key."""
    tpl = DIAGNOSTIC_TEXT.get(code)
    if tpl is None:
        return code
    c = CAP_BY_KEY.get(column)
    phase = "discharge" if column.startswith("discharge") else "charge"
    basis = {"mass": "Active Material Mass", "area": "Surface Area"}.get(c.basis if c else "", "The sample value")
    return f"{tpl.format(phase=phase, basis=basis)} ({code})"


# ---------------------------------------------------------------------------------------------------------------------
# Parameters. cycle_order comes from the published elaboration (read-only here); mass and area are the Sample values of
# Data Elaboration (read-only here); reference_cycle is edited in Capacity (default 2).

DEFAULT_REFERENCE_CYCLE = 2          # gregorithm default
ERR_REFERENCE = "Enter a whole number of 1 or more."
PHASE_TEXT = {"starting": "Starting…", "aggregate-s6": "Aggregating half-cycles",
              "scan-cc": "Scanning CC portions for C-rate", "finalize-capacity": "Finalizing",
              "capacity-complete": "Saving result…"}


def parse_reference_cycle(text: str) -> tuple[Optional[int], str]:
    t = text.strip()
    if not t.isdigit() or int(t) < 1:
        return None, ERR_REFERENCE
    return int(t), ""


def reference_hint(ref: Optional[int], cycles: Optional[list[int]]) -> tuple[str, bool]:
    """(text, warning). Existence is checked against the cycle IDs of the published elaboration; a missing reference
    does not block the analysis (the core then publishes null C-rates with diagnostics)."""
    if ref is None:
        return "", False
    if cycles is None:
        return f"Default {DEFAULT_REFERENCE_CYCLE}", False
    if ref in set(cycles):
        return f"Cycle {ref} exists in this result · Default {DEFAULT_REFERENCE_CYCLE}", False
    if ref == DEFAULT_REFERENCE_CYCLE:
        # The untouched default is not a user mistake: say what it means without a warning colour.
        return (f"This result has no cycle {ref}, so C-rates will not be available. Choose an existing cycle to "
                "get them."), False
    return f"Cycle {ref} does not exist in this result: C-rates will not be available.", True


@dataclass(frozen=True)
class CapParams:
    cycle_order: str
    reference_cycle: int
    active_material_mass_mg: Optional[float]
    electrode_area_cm2: Optional[float]


@dataclass(frozen=True)
class CapResult:
    """Published Capacity generation. Numbers are reported by the library."""
    result_id: str
    elab_result_id: str                 # the elaboration generation it was computed from
    n_cycles: int
    params: CapParams
    reference_capacity_ah: Optional[float] = None
    details: tuple[tuple[str, str], ...] = ()


class CapView(str, Enum):
    NOT_ANALYZED = "ready"
    ELAB_CHANGED = "elabchanged"
    ANALYZING = "analyzing"             # running, no previous result
    REANALYZING = "analyzingprev"       # running, previous result shown
    CANCELLING = "cancelling"
    PUBLISHING = "publishing"
    CURRENT = "current"
    OUTDATED = "outdated"
    CANCELLED = "cancelled"
    FAILED = "failed"


PILL = {CapView.NOT_ANALYZED: ("Not analyzed", "n", "circle-dashed"), CapView.ELAB_CHANGED: ("Not analyzed", "n", "circle-dashed"),
        CapView.ANALYZING: ("Analyzing", "p", "loader-circle"), CapView.REANALYZING: ("Analyzing", "p", "loader-circle"),
        CapView.CANCELLING: ("Cancelling…", "p", "loader-circle"), CapView.PUBLISHING: ("Saving…", "p", "loader-circle"),
        CapView.CURRENT: ("Current", "ok", "circle-check"), CapView.OUTDATED: ("Outdated", "warn", "refresh-cw"),
        CapView.CANCELLED: ("Cancelled", "n", "ban"), CapView.FAILED: ("Failed", "err", "circle-x")}


# ---------------------------------------------------------------------------------------------------------------------
# Chart + table view state (same exploration features as the Data Explorer, no sampling).

@dataclass
class CapViewState:
    x: str = CAP_DEFAULT_X
    y1: str = CAP_DEFAULT_Y1
    y2: Optional[str] = CAP_DEFAULT_Y2
    y1_color: str = CHART["y1"]
    y2_color: str = CHART["y2"]
    legend: bool = True
    style: str = "lines_markers"         # one marker per cycle by default (proposal)
    y2_same_scale: bool = False          # "Y2 uses the Y1 scale"
    show_cycle0: bool = False            # Preliminary (cycle 0) hidden by default
    filter_input: str = ""
    filter_error: str = ""
    applied: tuple[tuple[int, int], ...] = ()
    viewport: Viewport = field(default_factory=Viewport)
    selected_cycle: Optional[int] = None  # synchronised chart <-> table selection

    def set_axis(self, axis: str, key: Optional[str], allowed: list[str]) -> bool:
        if axis in ("x", "y1") and key is None:
            raise ValueError("X and Y1 are required")
        if key is not None and key not in allowed:
            return False
        if getattr(self, axis) == key:
            return False
        setattr(self, axis, key)
        self.viewport = replace(self.viewport, **{axis: None})
        return True

    def fix_axes(self, allowed: list[str]) -> bool:
        """After a new result some columns may have no values any more: fall back to the defaults (never keep an axis
        on a column that is not listed)."""
        changed = False
        for axis, default in (("x", CAP_DEFAULT_X), ("y1", CAP_DEFAULT_Y1), ("y2", CAP_DEFAULT_Y2)):
            k = getattr(self, axis)
            if k is not None and k not in allowed:
                setattr(self, axis, default if default in allowed else ("cycle" if axis != "y2" else None))
                changed = True
        return changed

    def type_filter(self, text: str) -> None:
        self.filter_input = text
        try:
            parse_cycle_filter(text)
            self.filter_error = ""
        except FilterError as e:
            self.filter_error = str(e)

    @property
    def can_apply_filter(self) -> bool:
        if self.filter_error:
            return False
        try:
            return parse_cycle_filter(self.filter_input) != self.applied
        except FilterError:
            return False

    def apply_filter(self) -> bool:
        if not self.can_apply_filter:
            return False
        self.applied = parse_cycle_filter(self.filter_input)
        self.viewport = Viewport()
        return True

    def clear_filter(self) -> bool:
        changed = bool(self.applied)
        self.filter_input, self.filter_error, self.applied = "", "", ()
        if changed:
            self.viewport = Viewport()
        return changed

    def visible_cycles(self, cycles: list[int]) -> list[int]:
        """Cycles shown in the chart AND the table: the applied filter, then cycle 0 only if shown."""
        res = resolve_cycles(self.applied, cycles)
        ids = list(res) if res is not None else sorted(set(cycles))
        return [c for c in ids if c != 0 or self.show_cycle0]

    def export_cycles(self, cycles: list[int]) -> Optional[tuple[int, ...]]:
        """Rows of Export Table: None = all cycles (cycle 0 included) when no filter; otherwise exactly the typed
        cycles that exist. The cycle-0 switch only changes the view."""
        return resolve_cycles(self.applied, cycles)


@dataclass
class CapacityState:
    """Per-dataset Capacity state. `reference_text` is the field content; the analysis uses the parsed value."""
    reference_text: str = str(DEFAULT_REFERENCE_CYCLE)
    reference_error: str = ""
    result: Optional[CapResult] = None
    elab_changed: bool = False          # a result existed for an earlier elaboration (it is not shown)
    run: Run = Run.IDLE
    outcome: Outcome = Outcome.NONE
    message: str = ""
    progress: Optional[float] = None
    phase: str = ""
    run_params: Optional[CapParams] = None
    view: CapViewState = field(default_factory=CapViewState)

    @property
    def reference(self) -> Optional[int]:
        return parse_reference_cycle(self.reference_text)[0]

    def set_reference_text(self, text: str) -> None:
        self.reference_text = text
        self.reference_error = parse_reference_cycle(text)[1]

    def params(self, cycle_order: str, sample: SampleValues) -> Optional[CapParams]:
        if self.reference is None or cycle_order not in CYCLE_ORDER_LABELS:
            return None
        return CapParams(cycle_order, self.reference, sample.active_mass_mg, sample.surface_area_cm2)

    @property
    def has_result(self) -> bool:
        return self.result is not None

    @property
    def is_running(self) -> bool:
        return self.run is not Run.IDLE

    def modified(self, current: Optional[CapParams]) -> set[str]:
        """Which parameters differ from the ones of the shown result ('Modified' tags)."""
        if self.result is None or current is None:
            return set()
        a, b = self.result.params, current
        return {k for k in ("reference_cycle", "active_material_mass_mg", "electrode_area_cm2") if getattr(a, k) != getattr(b, k)}

    def state(self, current: Optional[CapParams]) -> CapView:
        if self.run is Run.CANCELLING:
            return CapView.CANCELLING
        if self.run is Run.PUBLISHING:
            return CapView.PUBLISHING
        if self.run is Run.RUNNING:
            return CapView.REANALYZING if self.has_result else CapView.ANALYZING
        if not self.has_result:
            return CapView.ELAB_CHANGED if self.elab_changed else CapView.NOT_ANALYZED
        if self.outcome is Outcome.FAILED:
            return CapView.FAILED
        if self.outcome is Outcome.CANCELLED:
            return CapView.CANCELLED
        return CapView.OUTDATED if self.modified(current) else CapView.CURRENT

    def can_analyze(self, current: Optional[CapParams], elab_running: bool = False) -> bool:
        return current is not None and not self.is_running and not elab_running and not self.reference_error

    # --- run lifecycle (mirrors ElabState) ---
    def begin(self, params: CapParams) -> None:
        self.run, self.outcome, self.message, self.progress, self.phase = Run.RUNNING, Outcome.NONE, "", None, ""
        self.run_params = params

    def update_progress(self, fraction: Optional[float], phase: str, publishing: bool = False) -> None:
        if self.run is Run.IDLE:
            return
        self.progress, self.phase = fraction, PHASE_TEXT.get(phase, phase)
        if publishing and self.run is Run.RUNNING:
            self.run = Run.PUBLISHING

    def request_cancel(self) -> bool:
        if self.run in (Run.RUNNING, Run.PUBLISHING):
            self.run = Run.CANCELLING
            return True
        return False

    def finish(self, result: CapResult) -> None:
        self.result, self.run, self.outcome, self.message, self.elab_changed = result, Run.IDLE, Outcome.NONE, "", False
        self.view.viewport = Viewport()

    def cancelled(self) -> None:
        self.run, self.outcome = Run.IDLE, Outcome.CANCELLED

    def failed(self, message: str) -> None:
        self.run, self.outcome, self.message = Run.IDLE, Outcome.FAILED, message

    def elaboration_replaced(self) -> bool:
        """A new Data Elaboration result (or a re-import) makes the Capacity result belong to another generation: it is
        no longer shown and a new analysis is required. Returns True if a result was dropped."""
        had = self.result is not None
        self.result, self.outcome, self.message = None, Outcome.NONE, ""
        self.elab_changed = had or self.elab_changed
        self.view.viewport, self.view.selected_cycle = Viewport(), None
        return had


def finite_or_none(v) -> Optional[float]:
    """Table/readout helper: None and NaN are 'no value' (null), never zero."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ---------------------------------------------------------------------------------------------------------------------
# Chart helpers (display only).

def fit_range(*series) -> Optional[tuple[float, float]]:
    """Automatic axis range for the shown values: min..max of the finite values with 4 % padding (1.0 when flat).
    Purely visual: it never changes a value."""
    vals = [float(v) for s in series for v in s if v is not None and math.isfinite(float(v))]
    if not vals:
        return None
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.04 or (abs(lo) * 0.04 or 1.0)
    return lo - pad, hi + pad


def gap_breaks(visible: list[int], existing: list[int]) -> list[int]:
    """Indices i (into `visible`, sorted) after which the line must break because the cycle filter hid existing cycles
    between visible[i] and visible[i+1]. Missing cycle numbers that do not exist do not break the line."""
    ex = sorted(set(existing))
    pos = {c: k for k, c in enumerate(ex)}
    return [i for i in range(len(visible) - 1) if pos[visible[i + 1]] - pos[visible[i]] > 1]
