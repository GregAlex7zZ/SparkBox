"""dQ/dV section: analysis parameters, run lifecycle and states, plot-only view choices (phase, raw/smoothed, cycles
shown), the fully customisable trace appearance and the Voltage Reconstruction view. PURE PYTHON (no Qt), unit-tested in
tests/test_dqdv.py.

Every dQ/dV value comes from gregorithm `analyze_dqdv` (tables/dqdv.parquet, 8 columns) with the method the user chose:
the classic Gregorithm interpolated gradient (default) or the derivative-free voltage histograms (standard or
capacity-weighted) of Flores and Clark (2026). The measured and reconstructed voltage curves and their APE / RMSE come
from tables/dqdv_reconstruction.parquet and reports/dqdv.json. The GUI never differentiates, smooths, interpolates,
integrates or normalises; a curve without data is not drawn (never a zero line).
Which settings apply to which method is read from `gregorithm.dqdv_descriptor()` (parameter conditions), so the GUI
does not duplicate that rule.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import Optional

import gregorithm

from .elaboration import Outcome, Run
from .explorer import FilterError, Viewport, filter_text, parse_cycle_filter, resolve_cycles

# ---------------------------------------------------------------------------------------------------------------------
# Parameters (DqdvParameters + request cycles). Defaults equal those of gregorithm's DqdvParameters.

INTERPOLATED, HISTOGRAM, HISTOGRAM_WEIGHTED = "interpolated_gradient", "histogram", "histogram_weighted"
METHOD_CHOICES = ((INTERPOLATED, "Interpolated Gradient (Classic)"), (HISTOGRAM, "Histogram"),
                  (HISTOGRAM_WEIGHTED, "Weighted Histogram"))
METHOD_LABEL = dict(METHOD_CHOICES)
HISTOGRAM_METHODS = (HISTOGRAM, HISTOGRAM_WEIGHTED)
CITATION = gregorithm.DQDV_HISTOGRAM_REFERENCE.citation()


@dataclass(frozen=True)
class DqdvParams:
    cycles: tuple[int, ...]                     # explicit IDs, required ; sorted, unique, >= 1
    smoothing: bool = False
    sg_window: int = 5
    sg_order: int = 2
    method: str = INTERPOLATED
    e_min: Optional[float] = None               # V; None = measured minimum
    e_max: Optional[float] = None
    points_mode: str = "percent"                # interpolated gradient: "percent" | "count"
    points_value: float = 10.0
    edge_trim_mv: float = 5.0
    bin_mv: float = 1.0
    gaussian_sigma: float = 1.0
    reconstruction: bool = True

    def core_parameters(self) -> "gregorithm.DqdvParameters":
        """The exact gregorithm settings of this request (validated by the caller)."""
        return gregorithm.DqdvParameters(
            smoothing_enabled=self.smoothing, sg_window_points=self.sg_window, sg_poly_order=self.sg_order,
            method=gregorithm.DqdvMethod(self.method), voltage_min_v=self.e_min, voltage_max_v=self.e_max,
            interpolation_mode=gregorithm.InterpolationPoints(self.points_mode),
            interpolation_value=float(self.points_value), edge_trim_mv=self.edge_trim_mv,
            histogram_bin_mv=self.bin_mv, gaussian_sigma_bins=self.gaussian_sigma,
            reconstruction_enabled=self.reconstruction)


DEFAULT_WINDOW, DEFAULT_ORDER = 5, 2
DEFAULT_POINTS, DEFAULT_TRIM_MV, DEFAULT_BIN_MV, DEFAULT_SIGMA = ("percent", 10.0), 5.0, 1.0, 1.0
ERR_CYCLES_REQUIRED = "Choose the cycles to analyze."
ERR_CYCLE_ZERO = "Cycle 0 has no dQ/dV curve: choose cycles from 1."
ERR_WINDOW = "Enter an odd whole number of points, greater than the polynomial order."
ERR_ORDER = "Enter a whole number of 0 or more, smaller than the SG window."
ERR_POINTS = "Enter a number of points (e.g. 200, at least 3) or a percentage (e.g. 10%)."
ERR_VOLTAGE = "Enter a voltage in V, or leave it empty for the measured limit."
ERR_RANGE = "E min must be lower than E max."
ERR_TRIM = "Enter 0 mV or more."
ERR_BIN = "Enter a bin size greater than 0 mV."
ERR_SIGMA = "Enter a width greater than 0 bins."

# GUI setting -> gregorithm parameter id (dqdv_descriptor), used to read the method conditions.
_CORE_ID = {"points": "interpolation_value", "edge_trim_mv": "edge_trim_mv", "bin_mv": "histogram_bin_mv", "sg_window": "sg_window_points", "sg_order": "sg_poly_order",
            "gaussian_sigma": "gaussian_sigma_bins"}


@lru_cache(maxsize=1)
def _conditions() -> dict:
    return {p.id: p.conditions for p in gregorithm.dqdv_descriptor().parameters}


def relevant(key: str, method: str, smoothing: bool) -> bool:
    """Whether a setting is used by gregorithm for this method (and smoothing switch), from the descriptor."""
    core = _CORE_ID.get(key)
    if core is None:
        return True
    values = {"method": method, "smoothing_enabled": smoothing}
    return all(values.get(c.parameter_id) in c.values for c in _conditions()[core])

INFO = {
    "cycles": ("Cycles for which gregorithm calculates dQ/dV, for charge and discharge. Required.",
               "Write numbers or ranges: 1 · 1-100 · 1,6,9 · 1-5,9,12-20. Cycles that do not exist in the elaboration "
               "give no curve.", "Changing them makes the result Outdated: Reanalyze to apply."),
    "smoothing": ("Savitzky–Golay smoothing applied by gregorithm to the derivative, after it is calculated. The raw "
                  "derivative is always kept.", "Curves shorter than the window keep their raw values."),
    "sg_window": ("Number of derivative points in the smoothing window: odd, and greater than the polynomial order "
                  "(default 5).",),
    "sg_order": ("Degree of the smoothing polynomial: 0 or more, smaller than the window (default 2).",),
    "method": ("How gregorithm calculates dQ/dV.",
               "Interpolated Gradient (Classic): the original Gregorithm workflow. Points near the voltage limits are "
               "trimmed, Q(V) is interpolated on N evenly spaced voltages and differentiated with numpy.gradient.",
               "Histogram: derivative-free. The number of points per voltage bin, scaled by the capacity, gives dQ/dV; "
               "noise is not amplified. Assumes uniform capacity per point.",
               "Weighted Histogram: as Histogram, each interval weighted by the capacity it carried (for changing "
               "sampling or C-rate).",
               f"Histogram methods and the voltage reconstruction: {CITATION}."),
    "e_range": ("Voltage window of the analysis, for every method. Leave empty to use the measured limits.",
                "Values in V."),
    "points": ("Number of evenly spaced voltages of the interpolation grid (interpolated gradient).",
               "A count (e.g. 200) or a percentage of the measured points of each half-cycle (e.g. 10%). "
               "Limited to 3–5000 points."),
    "edge_trim_mv": ("Points closer than this to the highest or lowest voltage of the half-cycle are ignored "
                     "(interpolated gradient, default 5 mV). 0 keeps every point.",),
    "bin_mv": ("Width of the voltage bins (histogram methods, default 1 mV). It sets the resolution of the curve.",
               "Small bins resolve subtle plateaus but are noisier; keep it close to the voltage spacing of the "
               "measurements. Compare bin sizes with the Voltage Reconstruction view (lower APE and RMSE is better)."),
    "gaussian_sigma": ("Width of the Gaussian filter applied to the histogram, in bins (default 1, as in "
                       "Flores and Clark).",),
    "reconstruction": ("gregorithm integrates each dQ/dV curve back into a voltage curve and compares it with the "
                       "measured one: APE (capacity error, %) and RMSE (voltage error).",
                       "Shown in the Voltage Reconstruction view. Lower is better; 0 is a perfect reconstruction.",
                       f"Method: {CITATION}."),
}


def parse_analysis_cycles(text: str, available: Optional[list[int]] = None) -> tuple[Optional[tuple[int, ...]], str]:
    """Cycles to Analyze: required, the cycle-filter grammar, IDs >= 1. With `available` the typed ranges are resolved
    onto existing cycle IDs; without it single numbers / closed ranges are expanded as written."""
    if not text.strip():
        return None, ERR_CYCLES_REQUIRED
    try:
        ranges = parse_cycle_filter(text)
    except FilterError as e:
        return None, str(e)
    if any(a == 0 for a, _b in ranges):
        return None, ERR_CYCLE_ZERO
    if available is not None:
        ids = resolve_cycles(ranges, [c for c in available if c >= 1]) or ()
        if not ids:
            return None, "None of these cycles exists in the elaboration."
        return ids, ""
    ids = sorted({c for a, b in ranges for c in range(a, b + 1)})
    return tuple(ids), ""


def parse_int(text: str) -> Optional[int]:
    t = text.strip()
    return int(t) if t.isdigit() else None


def parse_points(text: str) -> tuple[Optional[tuple[str, float]], str]:
    """Interpolation points as in the classic Gregorithm: '200' (count), '10%' or '0.1' (10 %)."""
    t = text.strip().replace(",", ".")
    try:
        if t.endswith("%"):
            v = float(t[:-1])
            return (("percent", v), "") if math.isfinite(v) and 0 < v <= 100 else (None, ERR_POINTS)
        v = float(t)
    except ValueError:
        return None, ERR_POINTS
    if not math.isfinite(v) or v <= 0:
        return None, ERR_POINTS
    if v <= 1:
        return ("percent", v * 100.0), ""
    return (("count", float(int(v))), "") if v == int(v) and v >= 3 else (None, ERR_POINTS)


def points_text(mode: str, value: float) -> str:
    return f"{value:g}%" if mode == "percent" else f"{int(value)}"


def parse_optional_voltage(text: str) -> tuple[Optional[float], str]:
    t = text.strip().replace(",", ".")
    if not t:
        return None, ""
    try:
        v = float(t)
    except ValueError:
        return None, ERR_VOLTAGE
    return (v, "") if math.isfinite(v) else (None, ERR_VOLTAGE)


def parse_positive(text: str, error: str, allow_zero: bool = False) -> tuple[Optional[float], str]:
    try:
        v = float(text.strip().replace(",", "."))
    except ValueError:
        return None, error
    ok = math.isfinite(v) and (v >= 0 if allow_zero else v > 0)
    return (v, "") if ok else (None, error)


def method_summary(p: DqdvParams) -> str:
    """One line for the page header: method and its main settings."""
    window = ""
    if p.e_min is not None or p.e_max is not None:
        lo = "min" if p.e_min is None else f"{p.e_min:g}"
        hi = "max" if p.e_max is None else f"{p.e_max:g}"
        window = f" · E {lo}–{hi} V"
    if p.method in HISTOGRAM_METHODS:
        core = f"{METHOD_LABEL[p.method]} · bin {p.bin_mv:g} mV"
    else:
        core = f"Interpolated Gradient · {points_text(p.points_mode, p.points_value)} points · trim {p.edge_trim_mv:g} mV"
    if not p.smoothing:
        sm = "No smoothing"
    elif p.method in HISTOGRAM_METHODS:
        sm = f"Gaussian σ {p.gaussian_sigma:g} bin"
    else:
        sm = f"Smoothing SG {p.sg_window} / {p.sg_order}"
    return f"{core}{window} · {sm}"


def points_note(method: str) -> str:
    """What the drawn dQ/dV points are, for footers and the data view (never measured samples)."""
    if method in HISTOGRAM_METHODS:
        return "histogram bins (bin centres, not measured)"
    return "derived points (interpolation grid nodes, not measured)"


def sg_errors(window: Optional[int], order: Optional[int]) -> dict:
    err = {}
    if window is None or window < 1 or window % 2 == 0 or (order is not None and window <= order):
        err["sg_window"] = ERR_WINDOW
    if order is None or (window is not None and order >= window):
        err["sg_order"] = ERR_ORDER
    return err


# ---------------------------------------------------------------------------------------------------------------------
# Result

PHASES = ("charge", "discharge")


@dataclass(frozen=True)
class DqdvResult:
    result_id: str
    elab_result_id: str
    params: DqdvParams
    n_curves: int
    n_points: int
    unavailable: tuple[tuple[int, str, str], ...] = ()     # (cycle, phase, code) from reports/dqdv.json
    dcir_fingerprint: str = ""                            # accepted DCIR spans used (excluded)
    n_excluded_spans: int = 0
    corrected: bool = False                               # labels re-aligned by Cycle Correction
    details: tuple[tuple[str, str], ...] = ()

    @property
    def has_smoothed(self) -> bool:
        return self.params.smoothing

    @property
    def has_reconstruction(self) -> bool:
        return self.params.reconstruction

    @property
    def cites_paper(self) -> bool:
        return self.params.method in HISTOGRAM_METHODS or self.params.reconstruction


DIAGNOSTIC_TEXT = {
    "DQDV_NO_VALID_SPAN": "Not enough constant-current data for this phase with the chosen method: no dQ/dV curve.",
    "DQDV_RECONSTRUCTION_UNAVAILABLE": "The voltage curve cannot be reconstructed, or its error cannot be measured.",
    "NUMERIC_RESULT_UNREPRESENTABLE": "The result cannot be represented as a number.",
    "OPERATION_CANCELLED": "The operation was cancelled. Nothing was saved.",
}


def explain(code: str) -> str:
    t = DIAGNOSTIC_TEXT.get(code)
    return f"{t} ({code})" if t else code


def unavailable_text(items: tuple[tuple[int, str, str], ...]) -> str:
    """'1 curve not available' / 'n curves not available' ('' when none). Counting only."""
    n = len(items)
    return "" if n == 0 else f"{n:,} curve{'s' if n != 1 else ''} not available"


def unavailable_lines(items) -> list[str]:
    return [f"Cycle {c} · {p}: {explain(code)}" for c, p, code in items]


def dqdv_unit(capacity_unit: str) -> str:
    """dQ/dV shown in the capacity display unit per volt (Ah/V, mAh/V) ."""
    return f"{capacity_unit}/V"


RECON_VIEWS = (("dqdv", "dQ/dV"), ("reconstruction", "Voltage Reconstruction"))
RECON_CURVES = (("both", "Both"), ("measured", "Measured"), ("reconstructed", "Reconstructed"))


def fmt_ape(value: Optional[float]) -> str:
    """APE with three decimals: floating-point residues of a perfect reconstruction read 0.000 %."""
    return "—" if value is None else f"{value:.3f} %"


def fmt_rmse(value: Optional[float], voltage_unit: str) -> str:
    """RMSE already in the display voltage unit; shown in milli-units of V for readability when the unit is V."""
    if value is None:
        return "—"
    if voltage_unit == "V":
        return f"{value * 1000:.3g} mV"
    return f"{value:.3g} {voltage_unit}"


def recon_summary(items) -> str:
    """Footer text: median APE and RMSE over the curves shown ('' when none)."""
    apes = sorted(x.ape_percent for x in items if x.ape_percent is not None)
    if not apes:
        return ""
    return f"median APE {fmt_ape(apes[len(apes) // 2])}"


# ---------------------------------------------------------------------------------------------------------------------
# Trace appearance: everything is customisable . Charge and discharge share the style by default and can
# differ; one colour per cycle by default (10 colours, then repeated with markers).

CYCLE_PALETTE = ("#006D85", "#B05A31", "#5B8C2A", "#8569CD", "#C2417A",
                 "#2F6FB0", "#A07A12", "#1E8A7A", "#7A4E9E", "#6F8691")
MARKER_CYCLE = (None, "o", "s", "t", "d")       # pass 0 = no marker, then markers to tell repeated colours apart
LINE_STYLES = (("solid", "Solid"), ("dash", "Dashed"), ("dot", "Dotted"))
PLOT_CHOICES = (("smoothed", "Smoothed"), ("raw", "Raw"), ("both", "Both"))
PHASE_CHOICES = (("both", "Both"), ("charge", "Charge"), ("discharge", "Discharge"))


@dataclass(frozen=True)
class TraceStyle:
    color: str
    width: float
    alpha: float
    dash: str
    symbol: Optional[str]


@dataclass
class DqdvViewState:
    phase: str = "both"
    plot: str = "smoothed"                      # smoothed | raw | both (smoothed/both need a smoothed result)
    shown_text: str = ""                        # cycles shown (plot filter, within the analyzed cycles)
    shown: tuple = ()                           # applied ranges; () = all analyzed
    shown_error: str = ""
    viewport: Viewport = field(default_factory=Viewport)
    legend: bool = True
    style: str = "lines"                        # lines | markers | lines_markers
    line_width: float = 1.6
    raw_alpha: float = 0.5                      # raw when Plot = Both: thinner and 50 %
    raw_width_factor: float = 0.6
    same_color: bool = True                     # charge and discharge of a cycle share the colour
    colors: dict = field(default_factory=dict)  # (cycle, phase) -> hex chosen by the user
    dash: dict = field(default_factory=lambda: {"charge": "solid", "discharge": "solid"})   # (same style)
    selected: Optional[tuple] = None            # (cycle, phase) clicked
    recon_curves: str = "both"                  # Voltage Reconstruction: both | measured | reconstructed
    recon_viewport: Viewport = field(default_factory=Viewport)

    def effective_plot(self, has_smoothed: bool) -> str:
        return self.plot if has_smoothed else "raw"

    def type_shown(self, text: str) -> None:
        self.shown_text = text
        try:
            parse_cycle_filter(text)
            self.shown_error = ""
        except FilterError as e:
            self.shown_error = str(e)

    def apply_shown(self) -> bool:
        if self.shown_error:
            return False
        self.shown = parse_cycle_filter(self.shown_text)
        return True

    def visible_cycles(self, analyzed: list[int]) -> list[int]:
        r = resolve_cycles(self.shown, analyzed)
        return sorted(analyzed) if r is None else list(r)

    def visible_phases(self) -> tuple[str, ...]:
        return PHASES if self.phase == "both" else (self.phase,)

    def default_color(self, cycle: int, analyzed: list[int]) -> str:
        i = sorted(analyzed).index(cycle) if cycle in analyzed else 0
        return CYCLE_PALETTE[i % len(CYCLE_PALETTE)]

    def color(self, cycle: int, phase: str, analyzed: list[int]) -> str:
        if self.same_color:
            return self.colors.get((cycle, "charge"), self.default_color(cycle, analyzed))
        return self.colors.get((cycle, phase), self.colors.get((cycle, "charge"), self.default_color(cycle, analyzed)))

    def set_color(self, cycle: int, phase: str, hex_color: str) -> None:
        if self.same_color:
            self.colors[(cycle, "charge")] = hex_color
            self.colors[(cycle, "discharge")] = hex_color
        else:
            self.colors[(cycle, phase)] = hex_color

    def reset_colors(self) -> None:
        self.colors.clear()

    def trace_style(self, cycle: int, phase: str, kind: str, analyzed: list[int]) -> TraceStyle:
        """kind: 'smoothed' | 'raw'. Raw is thinner and 50 % only when both are plotted."""
        i = sorted(analyzed).index(cycle) if cycle in analyzed else 0
        sym = MARKER_CYCLE[(i // len(CYCLE_PALETTE)) % len(MARKER_CYCLE)]
        if self.style in ("markers", "lines_markers") and sym is None:
            sym = "o"
        faint = kind == "raw" and self.plot == "both"
        return TraceStyle(self.color(cycle, phase, analyzed),
                          self.line_width * (self.raw_width_factor if faint else 1.0),
                          self.raw_alpha if faint else 1.0,
                          self.dash.get(phase, "solid"), sym)

    def kinds(self, has_smoothed: bool) -> tuple[str, ...]:
        p = self.effective_plot(has_smoothed)
        return ("raw", "smoothed") if p == "both" else (p,)


# ---------------------------------------------------------------------------------------------------------------------
# State

class DqdvView(str, Enum):
    NOT_ANALYZED = "ready"
    ELAB_CHANGED = "elabchanged"
    ANALYZING = "analyzing"
    REANALYZING = "reanalyzing"
    CANCELLING = "cancelling"
    PUBLISHING = "publishing"
    CURRENT = "current"
    OUTDATED = "outdated"
    CANCELLED = "cancelled"
    FAILED = "failed"


PILL = {DqdvView.NOT_ANALYZED: ("Not analyzed", "n", "circle-dashed"), DqdvView.ELAB_CHANGED: ("Not analyzed", "n", "circle-dashed"),
        DqdvView.ANALYZING: ("Analyzing", "p", "loader-circle"), DqdvView.REANALYZING: ("Analyzing", "p", "loader-circle"),
        DqdvView.CANCELLING: ("Cancelling…", "p", "loader-circle"), DqdvView.PUBLISHING: ("Saving…", "p", "loader-circle"),
        DqdvView.CURRENT: ("Current", "ok", "circle-check"), DqdvView.OUTDATED: ("Outdated", "warn", "refresh-cw"),
        DqdvView.CANCELLED: ("Cancelled", "n", "ban"), DqdvView.FAILED: ("Failed", "err", "circle-x")}


def pill(view: DqdvView, result: Optional[DqdvResult]) -> tuple[str, str, str]:
    if view is DqdvView.CURRENT and result is not None and result.corrected:
        return "DCIR corrected", "ok", "circle-check"
    return PILL[view]


@dataclass
class DqdvState:
    cycles_text: str = ""
    smoothing: bool = False
    window_text: str = "5"
    order_text: str = "2"
    method: str = INTERPOLATED
    emin_text: str = ""
    emax_text: str = ""
    points_text: str = "10%"
    trim_text: str = "5"
    bin_text: str = "1"
    sigma_text: str = "1"
    reconstruction: bool = True
    tab: str = "dqdv"                            # dqdv | reconstruction (the second view, as DCIR Review | Results)
    run: Run = Run.IDLE
    outcome: Outcome = Outcome.NONE
    message: str = ""
    progress: Optional[float] = None
    phase: str = ""
    run_params: Optional[DqdvParams] = None
    result: Optional[DqdvResult] = None
    elab_changed: bool = False
    view: DqdvViewState = field(default_factory=DqdvViewState)
    data_view: bool = False                      # View dQ/dV Data

    # --- parameters ---
    def uses(self, key: str) -> bool:
        """Whether a setting applies to the chosen method (and smoothing switch)."""
        return relevant(key, self.method, self.smoothing)

    def errors(self, available: Optional[list[int]] = None) -> dict:
        """Errors of the settings that apply to the chosen method; hidden settings are never validated."""
        err = {}
        _c, e = parse_analysis_cycles(self.cycles_text, available)
        if e:
            err["cycles"] = e
        lo, e1 = parse_optional_voltage(self.emin_text)
        hi, e2 = parse_optional_voltage(self.emax_text)
        if e1:
            err["e_min"] = e1
        if e2:
            err["e_max"] = e2
        if lo is not None and hi is not None and lo >= hi:
            err["e_max"] = ERR_RANGE
        checks = (("points", lambda: parse_points(self.points_text)[1]),
                  ("edge_trim_mv", lambda: parse_positive(self.trim_text, ERR_TRIM, allow_zero=True)[1]),
                  ("bin_mv", lambda: parse_positive(self.bin_text, ERR_BIN)[1]),
                  ("gaussian_sigma", lambda: parse_positive(self.sigma_text, ERR_SIGMA)[1]))
        for key, check in checks:
            if self.uses(key) and (e := check()):
                err[key] = e
        if self.uses("sg_window"):                           # SG values are validated only when used
            err.update(sg_errors(parse_int(self.window_text), parse_int(self.order_text)))
        return err

    def params(self, available: Optional[list[int]] = None) -> Optional[DqdvParams]:
        if self.errors(available):
            return None
        c, _ = parse_analysis_cycles(self.cycles_text, available)
        # gregorithm validates every retained field whatever the method. Hidden invalid text must never reach the core:
        # settings that do not apply fall back to their defaults.
        w, o = parse_int(self.window_text), parse_int(self.order_text)
        if not self.uses("sg_window") and sg_errors(w, o):
            w, o = DEFAULT_WINDOW, DEFAULT_ORDER
        pts = parse_points(self.points_text)[0] if self.uses("points") else None
        trim = parse_positive(self.trim_text, ERR_TRIM, True)[0] if self.uses("edge_trim_mv") else None
        bin_mv = parse_positive(self.bin_text, ERR_BIN)[0] if self.uses("bin_mv") else None
        sigma = parse_positive(self.sigma_text, ERR_SIGMA)[0] if self.uses("gaussian_sigma") else None
        if pts is None:
            pts = parse_points(self.points_text)[0] or DEFAULT_POINTS
        if trim is None:
            trim = parse_positive(self.trim_text, ERR_TRIM, True)[0]
            trim = DEFAULT_TRIM_MV if trim is None else trim
        if bin_mv is None:
            bin_mv = parse_positive(self.bin_text, ERR_BIN)[0] or DEFAULT_BIN_MV
        if sigma is None:
            sigma = parse_positive(self.sigma_text, ERR_SIGMA)[0] or DEFAULT_SIGMA
        return DqdvParams(c, self.smoothing, w if w is not None else DEFAULT_WINDOW, o if o is not None else DEFAULT_ORDER,
                          self.method, parse_optional_voltage(self.emin_text)[0], parse_optional_voltage(self.emax_text)[0],
                          pts[0], pts[1], trim, bin_mv, sigma, self.reconstruction)

    def modified(self, current: Optional[DqdvParams], dcir_fingerprint: str = "", elab_result_id: str = "") -> set[str]:
        """Keys that differ from the shown result ('dcir' when the accepted DCIR spans changed)."""
        if self.result is None:
            return set()
        r = self.result.params
        out = set()
        if current is None:
            return {"invalid"}
        for k in ("cycles", "smoothing", "method", "reconstruction"):
            if getattr(current, k) != getattr(r, k):
                out.add(k)
        if (current.e_min, current.e_max) != (r.e_min, r.e_max):
            out.add("e_range")
        fields = {"points": ("points_mode", "points_value"),
                  "edge_trim_mv": ("edge_trim_mv",), "bin_mv": ("bin_mv",), "gaussian_sigma": ("gaussian_sigma",)}
        for key, names in fields.items():
            if relevant(key, current.method, current.smoothing) and any(
                    getattr(current, n) != getattr(r, n) for n in names):
                out.add(key)
        if relevant("sg_window", current.method, current.smoothing) and (
                (current.sg_window, current.sg_order) != (r.sg_window, r.sg_order)):
            out.add("sg")
        if dcir_fingerprint != self.result.dcir_fingerprint:
            out.add("dcir")
        if elab_result_id and elab_result_id != self.result.elab_result_id:
            out.add("elab")                                   # e.g. a correction published a new generation
        return out

    def state(self, current: Optional[DqdvParams], dcir_fingerprint: str = "", elab_result_id: str = "") -> DqdvView:
        if self.run is Run.CANCELLING:
            return DqdvView.CANCELLING
        if self.run is Run.PUBLISHING:
            return DqdvView.PUBLISHING
        if self.run is Run.RUNNING:
            return DqdvView.REANALYZING if self.result else DqdvView.ANALYZING
        if self.result is None:
            return DqdvView.ELAB_CHANGED if self.elab_changed else DqdvView.NOT_ANALYZED
        if self.outcome is Outcome.FAILED:
            return DqdvView.FAILED
        if self.outcome is Outcome.CANCELLED:
            return DqdvView.CANCELLED
        return DqdvView.OUTDATED if self.modified(current, dcir_fingerprint, elab_result_id) else DqdvView.CURRENT

    def analyze_blocker(self, current: Optional[DqdvParams], busy: str = "", available: Optional[list[int]] = None) -> str:
        if self.is_running:
            return "The analysis is running"
        if busy:
            return busy
        err = self.errors(available)
        if err:
            return next(iter(err.values()))
        return ""

    def load_from_result(self) -> None:
        """Fields show the parameters of the shown result (after publish)."""
        p = self.result.params
        self.cycles_text = filter_text(_ranges(p.cycles))
        self.smoothing = p.smoothing
        self.window_text, self.order_text = str(p.sg_window), str(p.sg_order)
        self.method, self.reconstruction = p.method, p.reconstruction
        self.emin_text = "" if p.e_min is None else f"{p.e_min:g}"
        self.emax_text = "" if p.e_max is None else f"{p.e_max:g}"
        self.points_text = points_text(p.points_mode, p.points_value)
        self.trim_text, self.bin_text, self.sigma_text = f"{p.edge_trim_mv:g}", f"{p.bin_mv:g}", f"{p.gaussian_sigma:g}"

    # --- run lifecycle ---
    @property
    def is_running(self) -> bool:
        return self.run is not Run.IDLE

    def begin(self, params: DqdvParams) -> None:
        self.run, self.outcome, self.message, self.progress, self.phase, self.run_params = Run.RUNNING, Outcome.NONE, "", None, "", params

    def update_progress(self, fraction: Optional[float], phase: str, publishing: bool = False) -> None:
        if self.run is not Run.IDLE:
            self.progress, self.phase = fraction, phase
            if publishing and self.run is Run.RUNNING:
                self.run = Run.PUBLISHING

    def request_cancel(self) -> bool:
        if self.run is Run.RUNNING:
            self.run = Run.CANCELLING
            return True
        return False

    def finish(self, res: DqdvResult) -> None:
        self.result, self.run, self.outcome, self.message, self.elab_changed = res, Run.IDLE, Outcome.NONE, "", False
        self.view.viewport = Viewport()
        self.view.recon_viewport = Viewport()
        if not res.has_reconstruction:
            self.tab = "dqdv"
        if not res.has_smoothed and self.view.plot != "raw":
            pass                                                # effective_plot() falls back to raw, choice kept

    def cancelled(self) -> None:
        self.run, self.outcome = Run.IDLE, Outcome.CANCELLED

    def failed(self, message: str) -> None:
        self.run, self.outcome, self.message = Run.IDLE, Outcome.FAILED, message

    def adopt(self, res: DqdvResult) -> None:
        """Labels re-aligned by Cycle Correction (existing_dqdv_path): same curves, published again."""
        self.result, self.outcome, self.message = res, Outcome.NONE, ""

    def elaboration_replaced(self) -> bool:
        had = self.result is not None
        self.result, self.outcome, self.message, self.data_view = None, Outcome.NONE, "", False
        self.elab_changed = had or self.elab_changed
        self.view.viewport, self.view.selected = Viewport(), None
        self.view.recon_viewport = Viewport()
        return had


def _ranges(ids: tuple[int, ...]) -> tuple[tuple[int, int], ...]:
    """Compact consecutive IDs into ranges for display ('1-5, 9')."""
    out: list[list[int]] = []
    for c in sorted(ids):
        if out and c == out[-1][1] + 1:
            out[-1][1] = c
        else:
            out.append([c, c])
    return tuple((a, b) for a, b in out)


def cycles_label(ids: tuple[int, ...]) -> str:
    return filter_text(_ranges(ids))


TABLE_COLUMNS = (
    ("cycle", "Cycle", None), ("half_cycle", "Half-Cycle", None), ("phase", "Phase", None),
    ("anchor_source_row", "Anchor Source Row", None), ("segment_index", "Segment", None),
    ("voltage_mid", "Voltage (midpoint)", "voltage"), ("dqdv_raw", "dQ/dV raw", "dqdv"),
    ("dqdv_smoothed", "dQ/dV smoothed", "dqdv"),
)


def column_title(key: str, units) -> str:
    title, q = next((t, q) for k, t, q in TABLE_COLUMNS if k == key)
    if q == "voltage":
        return f"{title} [{units.voltage}]"
    if q == "dqdv":
        return f"{title} [{dqdv_unit(units.capacity)}]"
    return title
