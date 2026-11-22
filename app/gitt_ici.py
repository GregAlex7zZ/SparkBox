"""GITT / ICI section: entry parameters, run state, impulse review rows and detail traces.
PURE PYTHON (no Qt), unit-tested in tests/test_gitt_ici_m6.py.

Every scientific value (event association, windows, OCV products, D, quality indicators, review transitions,
invalidation) comes from gregorithm through the backend. This module only keeps what the operator typed (the draft),
the parameters of the last explicit Apply, local window, OCV-target and IR-sample overrides, and turns gregorithm records
into display rows. It never computes, interpolates, averages or fills a missing D.

Records and snapshots are read by attribute (duck typing), so this module does not import gregorithm.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field, replace
from typing import Optional

# ---------------------------------------------------------------------------------------------------------------------
# Catalogues. Keys are the gregorithm enum values, so the backend maps them without a second vocabulary.


# Series key -> (label, gregorithm method value, gregorithm variant). Delacourt has two approved offset variants.
SERIES = {
    "gitt_va": ("GITT VA", "gitt_va", "default"),
    "gitt_delta": ("GITT Δ", "gitt_delta", "default"),
    "ici": ("ICI", "ici", "default"),
    "delacourt_fixed": ("Delacourt (fixed offset)", "gitt_sphere_full", "fixed"),
    "delacourt_free": ("Delacourt (free offset)", "gitt_sphere_full", "free"),
}
SERIES_BY_IDENTITY = {(method, variant): key for key, (_label, method, variant) in SERIES.items()}

# Preselection per protocol: GITT -> GITT VA + GITT Δ; ICI -> ICI. ICI and Delacourt can be added to GITT.
DEFAULT_METHODS = {"gitt": ("gitt_va", "gitt_delta"), "ici": ("ici",)}
# Methods offered in Setup. "Delacourt" is the fixed-offset variant (decision A-02); the free offset stays available in
# gregorithm but is not offered. Compatibility per protocol: GITT VA and GITT Δ need a GITT pulse between two rests;
# ICI can also be applied to a GITT pulse. Delacourt is GITT only: its analytical model needs a uniform
# (relaxed) state at the pulse start, which the short ICI interruptions do not give, and it is offered only with GITT
# .
METHOD_CHOICES = (("gitt_va", "GITT VA"), ("gitt_delta", "GITT Δ"), ("ici", "ICI"), ("delacourt_fixed", "Delacourt"))
PROTOCOL_METHODS = {"gitt": ("gitt_va", "gitt_delta", "ici", "delacourt_fixed"), "ici": ("ici",)}
# OCV methods: any combination can be selected; every selected method gives each compatible calculation method its
# own D series, so the results can be compared. Pseudo OCV exists only for ICI.
OCV_METHODS = (("rest_sample", "Sampled OCV"), ("ici_intercept", "Pseudo OCV"),
               ("relaxation_extrapolated", "Extrapolated OCV"))
DEFAULT_OCV_METHODS = {"gitt": ("rest_sample",), "ici": ("ici_intercept",)}
# Physical coefficient of the physically guided window, t_useful = k · r_p² / D. Default: the value the specification
# cites for GITT (Chien supplement discussion), a theoretical reference under specific assumptions; it is
# editable and gregorithm always receives it explicitly.
DEFAULT_PHYSICAL_COEFFICIENT = 0.0032
DEFAULT_FIT_START_FRACTION = 0.2        # gregorithm AnalysisSettings.relaxation_fit_start_fraction ("last 80 %")

OCV_LABELS = {"rest_sample": "Sampled OCV", "relaxation_extrapolated": "Extrapolated OCV",
              "ici_intercept": "Pseudo-OCV (ICI intercept)"}
ICI_OCV_CHOICES = ("ici_intercept", "rest_sample", "relaxation_extrapolated")

# Square-root-time window role of each method: GITT VA fits the pulse, ICI the rest after the interruption.
# GITT Δ uses samples only and Delacourt fits the whole CC pulse, so neither has an editable window.
WINDOW_ROLE = {"gitt_va": "pulse", "ici": "rest_after"}
ROLE_LABELS = {"pulse": "Pulse", "rest_after": "Rest after interruption"}

WINDOW_MODES = (
    ("auto_statistical", "Statistical search in the suggested window"),       # first automatic pass
    ("auto_fixed", "Suggested window, fixed"),
    ("physically_guided", "Physically guided (needs r_p and a D source)"),
)

VOLUME_UNITS = (("cm3", "cm³"), ("mm3", "mm³"), ("m3", "m³"))
AREA_UNITS = (("cm2", "cm²"), ("m2", "m²"))
LENGTH_UNITS = (("um", "µm"), ("mm", "mm"), ("m", "m"))

# Display only: values are stored in SI (m²/s); cm²/s is the initial display unit.
D_UNITS = {"cm2/s": ("cm²/s", 1e4), "m2/s": ("m²/s", 1.0)}
DEFAULT_D_UNIT = "cm2/s"

# Constant IR correction of GITT Δ: none by default; applied once by gregorithm, never twice.
IR_MODES = (("none", "None"), ("selected_sample", "Selected sample under current"),
            ("gitt_intercept", "GITT pulse fit intercept"))

DEFAULT_OCV_TARGET_S = 10800.0          # bibliographic suggestion, editable; global target with overrides
DEFAULT_THRESHOLDS = (2.0, 5.0, 5.0, 15.0)   # provisional q_L green/orange and q_S green/orange [%]

STATE_LABELS = {"provisional": "Proposed", "accepted": "Accepted", "rejected": "Rejected", "error": "Error"}
COLOR_LABELS = {"green": "Green", "orange": "Orange", "red": "Red", "not_available": "N/D",
                "not_applicable": "N/A"}

ERR_POSITIVE = "Enter a number greater than 0."


# ---------------------------------------------------------------------------------------------------------------------
# Parameters


@dataclass(frozen=True)
class GittIciParams:
    """Everything the operator chooses for one analysis; immutable so draft and applied values never alias.

    Physical inputs are direct values with their units; None means not entered. They are re-entered for
    every new analysis and never copied between datasets. ``cycles``/``half_cycles`` empty = whole elaboration.
    """
    protocol: str = "gitt"
    cycles: tuple[int, ...] = ()
    half_cycles: tuple[int, ...] = ()
    auto_analysis: bool = False                                  # off by default
    active_volume: Optional[float] = None
    active_volume_unit: str = "cm3"
    effective_area: Optional[float] = None
    effective_area_unit: str = "cm2"
    particle_radius: Optional[float] = None
    particle_radius_unit: str = "um"
    methods: tuple[str, ...] = DEFAULT_METHODS["gitt"]
    ocv_methods: tuple[str, ...] = DEFAULT_OCV_METHODS["gitt"]    # OCV sources of the D results (one series each)
    extrapolation: bool = False                                  # extrapolated OCV shown for every rest (comparison)
    ocv_fit_start_fraction: float = DEFAULT_FIT_START_FRACTION
    ocv_sample_time_s: Optional[float] = None                    # sampled OCV time [s]; None = last rest sample
    window_mode: str = "auto_statistical"
    # Operator bounds [s] replacing gregorithm's suggested range, one per window role (GITT pulse, ICI interruption);
    # None keeps the suggested range for that role. The fixed mode fits exactly these bounds; the statistical and
    # physically guided modes search inside them.
    fixed_pulse_window_s: Optional[tuple[float, float]] = None
    fixed_ici_window_s: Optional[tuple[float, float]] = None
    guidance_source: str = ""                                    # "series_key|ocv_source"; "" = none chosen
    guidance_coefficient: Optional[float] = DEFAULT_PHYSICAL_COEFFICIENT  # editable default (see constant)
    ocv_target_s: float = DEFAULT_OCV_TARGET_S
    thresholds: tuple[float, float, float, float] = DEFAULT_THRESHOLDS
    ir_correction: str = "none"                                  # GITT Δ only
    ir_sample_time_s: float = 1.0                                # +1 s from the last rest sample (default)
    ir_post_sample_time_s: Optional[float] = None                # post-pulse R_off target; None = same as above

    def scan_key(self) -> tuple:
        """What event recognition depends on: protocol and scope. Other fields only change calculations."""
        return self.protocol, self.cycles, self.half_cycles

    def window_key(self) -> tuple:
        """What the automatic windows depend on. gregorithm records window requests per result, not as a named
        settings dependency, so a change here must recalculate every automatic window, not only stale results."""
        return (self.window_mode, self.window_bounds("pulse"), self.window_bounds("rest_after"),
                self.guidance_source, self.guidance_coefficient)

    def window_bounds(self, role: str) -> Optional[tuple[float, float]]:
        """Operator bounds [s] of one window role for every automatic mode, or None (gregorithm's suggested range).
        They are the fitted window in the fixed mode and the search range in the statistical and guided modes."""
        return self.fixed_pulse_window_s if role == "pulse" else self.fixed_ici_window_s

    def fixed_window(self, role: str) -> Optional[tuple[float, float]]:
        """Operator bounds [s] of one window role in the fixed mode, or None (suggested window, or another mode)."""
        if self.window_mode != "auto_fixed":
            return None
        return self.window_bounds(role)

    @property
    def extrapolation_active(self) -> bool:
        """Extrapolated OCV is computed when requested in OCV Analysis or when it is the OCV method of D."""
        return self.extrapolation or "relaxation_extrapolated" in self.ocv_methods

    @property
    def scope(self) -> tuple[str, Optional[int]]:
        """('cycle' | 'half_cycle', number) of the single analysed element, or (kind, None) when none is chosen."""
        if self.half_cycles:
            return "half_cycle", self.half_cycles[0] if len(self.half_cycles) == 1 else None
        return "cycle", self.cycles[0] if len(self.cycles) == 1 else None


def with_scope(params: GittIciParams, kind: str, value: Optional[int]) -> GittIciParams:
    """Select exactly one cycle or one half-cycle; the two are mutually exclusive (selecting one clears the other)."""
    chosen = () if value is None else (int(value),)
    if kind == "half_cycle":
        return replace(params, cycles=(), half_cycles=chosen)
    return replace(params, cycles=chosen, half_cycles=())


def method_allowed(protocol: str, key: str) -> bool:
    """Whether a Setup method is compatible with the protocol."""
    return key in PROTOCOL_METHODS.get(protocol, ())


def ocv_compatible(key: str, ocv_method: str) -> bool:
    """Pseudo OCV is the intercept of consecutive ICI interruptions and exists only for ICI."""
    return ocv_method != "ici_intercept" or key == "ici"


def for_protocol(params: GittIciParams, protocol: str) -> GittIciParams:
    """Switch the explicit protocol and reset the method preselection to that protocol's defaults."""
    if protocol not in DEFAULT_METHODS:
        raise ValueError(f"Unknown protocol: {protocol}")
    return replace(params, protocol=protocol, methods=DEFAULT_METHODS[protocol],
                   ocv_methods=DEFAULT_OCV_METHODS[protocol])


def selected_series(params: GittIciParams) -> tuple[tuple[str, str], ...]:
    """Return the checked (series key, OCV source) pairs in catalogue order: every compatible checked method with
    every selected OCV method it can use; each pair is one result series."""
    return tuple((key, source) for key in SERIES
                 if key in params.methods and method_allowed(params.protocol, key)
                 for source, _label in OCV_METHODS
                 if source in params.ocv_methods and ocv_compatible(key, source))


def series_key_of(identity) -> str:
    """Return the SERIES key of a gregorithm ResultIdentity (method + variant)."""
    return SERIES_BY_IDENTITY.get((identity.method.value, identity.variant), identity.method.value)


def series_label(identity) -> str:
    """Human label of a result series: method/variant and OCV source, e.g. 'GITT VA · Sampled OCV'."""
    key = series_key_of(identity)
    label = SERIES[key][0] if key in SERIES else key
    return f"{label} · {OCV_LABELS.get(identity.ocv_source.value, identity.ocv_source.value)}"


def parse_optional_positive(text: str) -> tuple[Optional[float], str]:
    """Parse an optional positive finite number ('' -> None); comma is accepted as decimal separator."""
    t = text.strip().replace(",", ".")
    if not t:
        return None, ""
    try:
        v = float(t)
    except ValueError:
        return None, ERR_POSITIVE
    if not math.isfinite(v) or v <= 0:
        return None, ERR_POSITIVE
    return v, ""


def parse_int_set(text: str) -> tuple[tuple[int, ...], str]:
    """Parse a cycle/half-cycle selection such as '1, 3-5' ('' -> no restriction). Values must be >= 1."""
    t = text.strip()
    if not t:
        return (), ""
    values: set[int] = set()
    try:
        for part in t.replace(";", ",").split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                lo, hi = (int(x) for x in part.split("-", 1))
                if hi < lo:
                    raise ValueError
                values.update(range(lo, hi + 1))
            else:
                values.add(int(part))
    except ValueError:
        return (), "Use numbers and ranges, for example 1, 3-5."
    if any(v < 1 for v in values):
        return (), "Use numbers and ranges, for example 1, 3-5."
    return tuple(sorted(values)), ""


def format_int_set(values: tuple[int, ...]) -> str:
    """Inverse of parse_int_set for display (contiguous values become ranges)."""
    out, run = [], []
    for v in values:
        if run and v == run[-1] + 1:
            run.append(v)
            continue
        if run:
            out.append(str(run[0]) if len(run) == 1 else f"{run[0]}-{run[-1]}")
        run = [v]
    if run:
        out.append(str(run[0]) if len(run) == 1 else f"{run[0]}-{run[-1]}")
    return ", ".join(out)


def validate(params: GittIciParams) -> list[str]:
    """Conditions that keep Proceed disabled : protocol, exactly one cycle or half-cycle, compatible
    methods and OCV method, the physical inputs the checked methods require, and valid automatic parameters."""
    errors = []
    if params.protocol not in DEFAULT_METHODS:
        errors.append("Choose GITT or ICI explicitly.")
    if len(params.cycles) + len(params.half_cycles) != 1 or (params.cycles and params.half_cycles):
        errors.append("Select exactly one cycle or one half-cycle.")
    wrong = [label for key, label in METHOD_CHOICES
             if key in params.methods and not method_allowed(params.protocol, key)]
    if wrong:
        errors.append(f"Not available with {params.protocol.upper()}: {', '.join(wrong)}.")
    if not any(key in params.methods for key, _l in METHOD_CHOICES):
        errors.append("Select at least one calculation method.")
    if not params.ocv_methods or any(s not in dict(OCV_METHODS) for s in params.ocv_methods):
        errors.append("Select at least one OCV method.")
    else:
        if "ici_intercept" in params.ocv_methods and "ici" not in params.methods:
            errors.append("Pseudo OCV needs the ICI method.")
        orphans = [label for key, label in METHOD_CHOICES
                   if key in params.methods and method_allowed(params.protocol, key)
                   and not any(ocv_compatible(key, s) for s in params.ocv_methods)]
        if orphans:
            errors.append(f"Pseudo OCV exists only for ICI; select Sampled or Extrapolated OCV for "
                          f"{', '.join(orphans)}.")
    classic = [label for key, label in METHOD_CHOICES if key in params.methods and key != "delacourt_fixed"]
    if classic and params.active_volume is None:
        errors.append(f"Active volume is required by {', '.join(classic)}.")
    if classic and params.effective_area is None:
        errors.append(f"Effective area is required by {', '.join(classic)}.")
    if "delacourt_fixed" in params.methods and params.particle_radius is None:
        errors.append("Particle radius is required by Delacourt.")
    if not (0 <= params.ocv_fit_start_fraction < 1):
        errors.append("The relaxation fit start must be at least 0 and below 1.")
    ocv_time = params.ocv_sample_time_s
    if ocv_time is not None and not (math.isfinite(ocv_time) and ocv_time > 0):
        errors.append("The sampled OCV time must be greater than 0 s.")
    if not (math.isfinite(params.ir_sample_time_s) and params.ir_sample_time_s > 0):
        errors.append("The IR sample time must be greater than 0 s.")
    post = params.ir_post_sample_time_s
    if post is not None and not (math.isfinite(post) and post > 0):
        errors.append("The post-pulse IR sample time must be greater than 0 s.")
    if not (math.isfinite(params.ocv_target_s) and params.ocv_target_s > 0):
        errors.append("The OCV target must be greater than 0 s.")
    for bounds, what in ((params.fixed_pulse_window_s, "GITT pulse"), (params.fixed_ici_window_s, "ICI")):
        if bounds is not None and not (all(math.isfinite(v) for v in bounds) and 0 <= bounds[0] < bounds[1]):
            errors.append(f"The {what} window bounds need 0 ≤ start < end.")
    lg, lo, sg, so = params.thresholds
    if any(not math.isfinite(v) or v < 0 for v in params.thresholds) or lo < lg or so < sg:
        errors.append("Quality thresholds must be 0 or greater, with orange ≥ green.")
    if params.auto_analysis and params.window_mode == "physically_guided":
        if not params.guidance_source:
            errors.append("Physically guided windows need a D source series.")
        if params.guidance_coefficient is None:
            errors.append("Physically guided windows need the physical coefficient.")
        if params.particle_radius is None:
            errors.append("Physically guided windows need the particle radius.")
    return errors


def advisories(params: GittIciParams) -> list[str]:
    """Non-blocking notes shown next to the inputs: which D will be unavailable and why."""
    notes = []
    classic = {"gitt_va", "gitt_delta", "ici"} & set(params.methods)
    if classic and (params.active_volume is None or params.effective_area is None):
        notes.append("Without V_act and A_eff, GITT VA, GITT Δ and ICI keep their intermediates but D is unavailable.")
    if {"delacourt_fixed", "delacourt_free"} & set(params.methods) and params.particle_radius is None:
        notes.append("Delacourt needs the particle radius r_p; without it D is unavailable.")
    if params.window_mode == "physically_guided" and params.particle_radius is None:
        notes.append("Physical guidance needs r_p; windows fall back to the statistical search, with the reason.")
    if params.window_mode == "physically_guided" and "ici" in params.methods:
        notes.append("Physical guidance is not defined for ICI; ICI windows use the statistical search.")
    return notes


# ---------------------------------------------------------------------------------------------------------------------
# Impulse list: one row per impulse, expandable into its result series; complete list, no filters.


@dataclass(frozen=True)
class SeriesRow:
    """One result series of one impulse, as shown in the expanded list and the review controls."""
    identity: object
    label: str
    d_text: str
    statistical: str
    physical: str
    overall: str
    to_check: bool
    state: str
    stale: bool
    reason: str
    exportable: bool


@dataclass(frozen=True)
class ImpulseRow:
    """One canonical impulse (event). ``series`` keeps every method's own state and colour."""
    event_id: str
    number: int
    cycle: int
    half_cycle: int
    branch: str
    complete: bool
    note: str
    series: tuple[SeriesRow, ...]


def format_d(value: Optional[float], unit: str = DEFAULT_D_UNIT) -> str:
    """Format a D stored in m²/s in the chosen display unit; None or non-finite -> 'N/D' (never a fake zero)."""
    if value is None or not math.isfinite(value):
        return "N/D"
    return f"{value * D_UNITS[unit][1]:.4g}"


def unavailable_reason(record) -> str:
    """Technical reason of a record without D: the error text or the result's unavailable_reason."""
    if record.error:
        return record.error
    result = record.result
    if result is not None and getattr(result, "diffusion_m2_s", None) is None:
        return getattr(result, "unavailable_reason", None) or "D unavailable"
    return ""


def series_row(record, unit: str = DEFAULT_D_UNIT) -> SeriesRow:
    """Display row of one gregorithm AnalysisRecord."""
    q = record.quality
    color = (lambda c: COLOR_LABELS.get(c.value, c.value))
    return SeriesRow(
        identity=record.identity, label=series_label(record.identity),
        d_text=format_d(record.diffusion_m2_s, unit),
        statistical="N/D" if q is None else color(q.statistical),
        physical="N/D" if q is None else color(q.physical),
        overall="N/D" if q is None else color(q.overall),
        to_check=bool(q is not None and q.needs_review),
        state=STATE_LABELS.get(record.state.value, record.state.value), stale=bool(record.stale),
        reason=unavailable_reason(record), exportable=bool(record.exportable))


def _series_order(identity) -> tuple:
    keys = list(SERIES)
    key = series_key_of(identity)
    sources = list(ICI_OCV_CHOICES)
    return (keys.index(key) if key in keys else len(keys),
            sources.index(identity.ocv_source.value) if identity.ocv_source.value in sources else len(sources))


def impulse_rows(events, records, unit: str = DEFAULT_D_UNIT) -> list[ImpulseRow]:
    """Build the complete impulse list in natural (recognition) order. Nothing is filtered; incomplete
    impulses are listed with their note and no result."""
    by_event: dict[str, list] = {}
    for record in records:
        by_event.setdefault(record.identity.event_id, []).append(record)
    rows = []
    for number, event in enumerate(events, start=1):
        recs = sorted(by_event.pop(event.event_id, []), key=lambda r: _series_order(r.identity))
        note = ""
        if not event.complete:
            note = "Incomplete in the selected scope: not analysed"
        elif not event.constant_current:
            note = "Active segment is not constant current: no D for CC methods"
        rows.append(ImpulseRow(event.event_id, number, event.cycle, event.half_cycle,
                               "Charge" if event.polarity > 0 else "Discharge", event.complete, note,
                               tuple(series_row(r, unit) for r in recs)))
    return rows


def counts(records) -> dict[str, int]:
    """Review counts over all records (proposed/accepted/rejected/error/stale/exportable)."""
    out = {"proposed": 0, "accepted": 0, "rejected": 0, "error": 0, "stale": 0, "exportable": 0}
    for r in records:
        out[{"provisional": "proposed"}.get(r.state.value, r.state.value)] += 1
        out["stale"] += bool(r.stale)
        out["exportable"] += bool(r.exportable)
    return out


def accept_all_label(identity) -> str:
    """Button text that makes the exact Accept-all scope evident: one method/variant/OCV series."""
    return f"Accept all: {series_label(identity)}"


@dataclass(frozen=True)
class GittIciScan:
    """Result of impulse recognition: the backend handle of the loaded scope, its gregorithm events and, per
    half-cycle, the canonical branch capacity at its last sample (Q_end, the SoC denominator)."""
    scope_id: str
    events: tuple
    branch_end_ah: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------------------------------------------------
# Detail trace (manual detail, windows, curves). Raw samples only; times are absolute seconds.


@dataclass(frozen=True)
class SegmentTrace:
    """Canonical samples of one segment of an impulse, exactly as stored in the elaboration (no decimation)."""
    segment_id: Optional[int]
    rows: tuple[int, ...] = ()
    time_s: tuple[float, ...] = ()
    voltage_v: tuple[float, ...] = ()
    current_a: tuple[float, ...] = ()

    @property
    def duration_s(self) -> Optional[float]:
        """Observed duration (last minus first sample time) or None for an empty segment."""
        return None if len(self.time_s) < 2 else self.time_s[-1] - self.time_s[0]


@dataclass(frozen=True)
class ImpulseTrace:
    """Rest before, pulse and rest after of one impulse plus the documented gregorithm time origins of each window
    role: GITT pulse windows start at the last sample of the rest before; ICI windows at the last sample under current."""
    event_id: str
    rest_before: SegmentTrace
    pulse: SegmentTrace
    rest_after: SegmentTrace
    origin_s: dict = field(default_factory=dict)      # role -> absolute origin time [s] or None

    def segment(self, role: str) -> SegmentTrace:
        """Samples governed by a window role: the pulse or the rest after the interruption."""
        return self.pulse if role == "pulse" else self.rest_after


def relative(trace: SegmentTrace, origin_s: float) -> list[tuple[float, float]]:
    """(t − origin, V) of the samples after the origin; the origin itself (t = 0) is excluded (no ln/√ of 0 issues)."""
    return [(t - origin_s, v) for t, v in zip(trace.time_s, trace.voltage_v) if t > origin_s]


GUIDANCE_REASONS = {
    "guidance_source_not_selected": "no D source is chosen for the guidance",
    "particle_radius_missing": "the particle radius r_p is not entered",
    "guidance_diffusion_unavailable": "the guidance source has no D for this impulse",
    "guidance_not_applicable": "this method gives no D to guide the window",
    "ici_useful_time_not_validated": "the useful time is not defined for ICI",
}


def particle_radius_use(params: "GittIciParams") -> str:
    """What the particle radius is used for with these settings, in words: r_p feeds only Delacourt and the
    physically guided window, so with the statistical or fixed window and no Delacourt an entered r_p changes
    nothing, and the operator is told so instead of seeing no effect."""
    users = []
    if "delacourt_fixed" in params.methods:
        users.append("Delacourt")
    if params.auto_analysis and params.window_mode == "physically_guided":
        users.append("the physically guided window")
    if users:
        return "r_p is used by " + " and ".join(users) + "."
    if params.particle_radius is None:
        return "r_p is needed only by Delacourt and by the physically guided window."
    return ("r_p is not used with these choices: only Delacourt and the physically guided window (Automatic "
            "analysis → Window method) use it.")


def physical_note(record) -> str:
    """Why the Physical indicator of a record has its value, in words (tooltip of the Physical cell). gregorithm
    compares the window end with the useful time k·r_p²/D only for physically guided windows; otherwise the
    indicator is N/D and the guidance details say why."""
    q = record.quality
    value = None if q is None else q.physical.value
    if value in ("green", "orange", "red"):
        return {"green": "The window ends within the useful time k·r_p²/D.",
                "orange": "The window ends slightly after the useful time k·r_p²/D.",
                "red": "The window ends well after the useful time k·r_p²/D."}[value]
    details = record.details if isinstance(record.details, dict) else {}
    guidance = details.get("guidance")
    if isinstance(guidance, dict) and not guidance.get("applied", True):
        reason = GUIDANCE_REASONS.get(str(guidance.get("reason")), str(guidance.get("reason")))
        return f"Not determined: the guided window fell back to the statistical search because {reason}."
    if value == "not_applicable":
        return "Not applicable: this method has no √t fit window to compare with the useful time."
    return ("Not determined: only the physically guided window (Setup → Automatic analysis → Window method) "
            "compares the window with the useful time k·r_p²/D.")


def fit_window(record) -> Optional[dict]:
    """The √t-fit payload recorded by gregorithm for a record (GITT VA / ICI), or None."""
    details = record.details if isinstance(record.details, dict) else {}
    window = details.get("window")
    return window if isinstance(window, dict) else None


def review_reasons(record) -> list[str]:
    """Why gregorithm marks a result "to check" (needs_review), in words; [] when it does not. The flag is raised
    by an automatic window whose statistical indicator is not green, whose window ends after the useful time
    (physical orange / red), or whose statistical search skipped the first data of the range it searched: the window
    can be green and still be flagged in that last case, because the early transient was left out."""
    q = record.quality
    if q is None or not q.needs_review:
        return []
    reasons = []
    if q.statistical.value != "green":
        reasons.append(f"statistical indicator {COLOR_LABELS.get(q.statistical.value, q.statistical.value).lower()}")
    if q.physical.value in ("orange", "red"):
        reasons.append("window ends after the useful time")
    window = fit_window(record) or {}
    if window.get("early_data_excluded"):
        start = (window.get("selected_s") or [None])[0]
        reasons.append("the automatic window skips the first " + ("data" if start is None else f"{start:.3g} s")
                       + " of the range searched")
    return reasons or ["automatic window to confirm"]


def fit_curve(record, points: int = 60) -> list[tuple[float, float]]:
    """Line E = a + s√t of the recorded fit over its effective window [s]; display only, using gregorithm's a, s."""
    window = fit_window(record)
    fit = None if window is None else window.get("fit")
    if not isinstance(fit, dict):
        return []
    a, s = fit.get("intercept_v"), fit.get("slope_v_per_sqrt_s")
    lo, hi = (fit.get("effective_s") or [None, None])
    if None in (a, s, lo, hi) or hi <= lo:
        return []
    step = (hi - lo) / (points - 1)
    return [(lo + i * step, a + s * math.sqrt(lo + i * step)) for i in range(points)]


def ir_offset(record) -> Optional[tuple[str, float]]:
    """(mode, c) of the constant IR correction gregorithm applied to the GITT Δ voltage of this record, if any.
    The GUI may draw V − c once for comparison; it never applies the correction a second time. Delacourt's fixed
    offset is part of its fitted model (compared with the raw voltage), so it is deliberately not returned here."""
    details = record.details if isinstance(record.details, dict) else {}
    ir = details.get("ir")
    if isinstance(ir, dict) and ir.get("mode") not in (None, "none") and ir.get("offset_v") is not None:
        return str(ir["mode"]), float(ir["offset_v"])
    return None


def extrapolation_ratio(target_s: float, rest: SegmentTrace) -> Optional[float]:
    """Target / observed rest duration, shown as an informative warning above 1; never a block."""
    duration = rest.duration_s
    return None if not duration else target_s / duration


# ---------------------------------------------------------------------------------------------------------------------
# Run and review state of one dataset


RUN_RECOGNIZE, RUN_GLOBAL, RUN_LOCAL = "recognize", "global", "local"


@dataclass
class GittIciState:
    """Per-dataset GITT/ICI state. ``draft`` follows the inputs; ``applied`` changes only when a run starts from an
    explicit Apply/Calculate and is restored if that run is cancelled or fails, so current results always
    match the applied parameters. ``snapshot`` is the gregorithm ResultSnapshot (one current record per identity)."""
    draft: GittIciParams = field(default_factory=GittIciParams)
    applied: GittIciParams = field(default_factory=GittIciParams)
    scanned: Optional[tuple] = None                 # scan_key() of the recognised events
    scope_id: str = ""                              # backend handle of the loaded canonical scope
    events: tuple = ()
    skipped: tuple[str, ...] = ()                   # diagnostics of impulses not analysed
    branch_end_ah: dict = field(default_factory=dict)   # half_cycle -> Q_end [Ah] for the SoC axis
    snapshot: object | None = None
    windows: dict = field(default_factory=dict)     # (event_id, role) -> (start_s, end_s) manual window
    ocv_overrides: dict = field(default_factory=dict)   # rest_segment_id -> target_s
    ocv_fit_overrides: dict = field(default_factory=dict)   # rest_segment_id -> fit start fraction of that rest
    ir_samples: dict = field(default_factory=dict)      # (event_id, side) -> source row | ('time', local T [s])
    ocv_samples: dict = field(default_factory=dict)     # rest_segment_id -> source row | ('time', local T [s])
    rest_ocv: tuple = ()                                # gregorithm RestOcv per rest (applied settings)
    rest_key: Optional[tuple] = None                    # what rest_ocv was measured with
    rest_reviews: dict = field(default_factory=dict)    # rest_segment_id -> fingerprint accepted by the user
    pulse_reviews: dict = field(default_factory=dict)   # event_id -> fingerprint of the fits accepted by the user
    ir_reviews: dict = field(default_factory=dict)      # event_id -> fingerprint of the IR drops accepted by the user
    step: str = "setup"                                 # open area: setup, ir, ocv, fit, results
    open_rest: Optional[int] = None                     # rest period open in OCV Analysis
    proceeding: bool = False                            # the running job was started by Proceed (Setup)
    ir_drops: dict = field(default_factory=dict)        # event_id -> gregorithm ImpulseIrDrops (applied settings)
    ir_key: Optional[tuple] = None                      # what ir_drops were measured with
    d_unit: str = DEFAULT_D_UNIT
    selected: object | None = None                  # ResultIdentity under review
    fit_series: Optional[tuple[str, str]] = None    # (series key, OCV source) shown in Pulse Fitting for every impulse
    open_event: str = ""                            # impulse whose detail is open (manual mode)
    trace: object | None = None                     # ImpulseTrace of open_event
    run: str = ""
    run_id: int = 0
    cancel_requested: bool = False
    progress: Optional[float] = None
    phase: str = ""
    done: int = 0
    total: int = 0
    error: str = ""
    notice: str = ""
    exporting: bool = False
    _restore: Optional[tuple] = None

    @property
    def is_running(self) -> bool:
        """True while a recognition or calculation job of this dataset is in flight."""
        return bool(self.run)

    @property
    def records(self) -> tuple:
        """Current records of the snapshot (empty before the first calculation)."""
        return () if self.snapshot is None else tuple(self.snapshot.records)

    def set_draft(self, params: GittIciParams) -> None:
        """Store edited inputs without touching results."""
        self.draft = params

    def needs_scan(self, params: GittIciParams) -> bool:
        """Whether recognition must run before calculating with ``params``."""
        return self.scanned != params.scan_key()

    def begin(self, kind: str, apply_draft: bool) -> int:
        """Start a job; returns its run id. Applying the draft is undone by cancel()/fail()."""
        self._restore = (self.applied, dict(self.windows), dict(self.ocv_overrides), dict(self.ir_samples),
                         dict(self.ocv_samples), dict(self.ocv_fit_overrides))
        if apply_draft:
            self.applied = self.draft
        self.run_id += 1
        self.run, self.cancel_requested, self.error, self.notice = kind, False, "", ""
        self.progress, self.phase, self.done, self.total = 0.0, "Starting", 0, 0
        return self.run_id

    def finish(self) -> None:
        """Mark the current job as completed; the caller has already stored its results."""
        self.run, self.cancel_requested, self.progress, self.phase, self._restore = "", False, 1.0, "Complete", None

    def _undo(self) -> None:
        if self._restore is not None:
            (self.applied, self.windows, self.ocv_overrides, self.ir_samples, self.ocv_samples,
             self.ocv_fit_overrides) = self._restore
        self._restore = None
        self.run, self.cancel_requested = "", False

    def cancel(self) -> None:
        """Cancelled job: nothing was published; previous results and applied parameters stay."""
        self._undo()
        self.phase = "Cancelled · previous results unchanged"

    def fail(self, message: str) -> None:
        """Structural failure of a job: same preservation as cancel, with the message."""
        self._undo()
        self.error, self.phase = message, "Failed"

    def reset_results(self) -> None:
        """Drop events, results and local overrides (new elaboration or a different protocol/scope)."""
        self.scanned, self.scope_id, self.events, self.skipped, self.branch_end_ah = None, "", (), (), {}
        self.snapshot, self.selected, self.open_event, self.trace = None, None, "", None
        self.windows, self.ocv_overrides, self.ir_samples, self.ir_drops, self.ir_key = {}, {}, {}, {}, None
        self.ocv_samples, self.rest_ocv, self.rest_key, self.open_rest = {}, (), None, None
        self.ocv_fit_overrides = {}
        self.rest_reviews, self.pulse_reviews, self.ir_reviews = {}, {}, {}

    def record(self, identity) -> object | None:
        """Current record of an identity, or None."""
        return None if self.snapshot is None or identity is None else self.snapshot.current(identity)

    def event(self, event_id: str) -> object | None:
        """Recognised event by id, or None."""
        return next((e for e in self.events if e.event_id == event_id), None)
