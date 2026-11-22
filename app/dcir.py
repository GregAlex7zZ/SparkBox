"""DCIR section: detection parameters, event review state (Pending / Accepted / Rejected), list labels and filters,
measurement time and previews, manual measurement values, publish readiness, diagnostics texts and the Results rows.
PURE PYTHON (no Qt), unit-tested in tests/test_dcir.py.

Every scientific value (events, resistance, SOC, labels DCIRn) comes from gregorithm (detect_dcir, review_event,
measure_automatic_from_generation, create_manual_event, publish_dcir_events, event_labels). The GUI keeps only the user's
local decisions until they are published, and never interpolates, extrapolates or fills a null.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Optional

from .elaboration import Outcome, Run

# ---------------------------------------------------------------------------------------------------------------------
# Detection parameters (DcirDetectionParameters). Durations always in seconds.

BOTH, CHARGE, DISCHARGE = "both", "charge", "discharge"
POLARITY_LABELS = {BOTH: "Both", CHARGE: "Charge", DISCHARGE: "Discharge"}


@dataclass(frozen=True)
class DetectionParams:
    rest_before_s: float = 600.0
    pulse_s: float = 30.0
    rest_after_s: float = 600.0
    rest_before_tolerance_pct: float = 20.0
    pulse_tolerance_pct: float = 20.0
    rest_after_tolerance_pct: float = 20.0
    polarity: str = BOTH


FACTORY_DETECTION = DetectionParams()          # GUI defaults: 600 s rest before and after the pulse


@dataclass(frozen=True)
class DetField:
    key: str
    label: str
    unit: str
    positive: bool                 # True: > 0 ; False: >= 0
    popover: tuple[str, ...]


ERR_GE0 = "Enter a number that is 0 or greater."
ERR_GT0 = "Enter a number greater than 0."
DET_FIELDS = (
    DetField("pulse_s", "Pulse Duration", "s", True, (
        "Nominal duration of the current pulse. Pulses are constant-current (CC) segments of the current elaboration "
        "whose duration is within the tolerance of this value.",)),
    DetField("pulse_tolerance_pct", "Pulse Duration Tolerance", "%", False, (
        "Accepted deviation from the nominal pulse duration, as a percentage of that duration (not seconds).",)),
    DetField("rest_before_s", "Rest Before", "s", False, (
        "Required duration of the rest segment just before the pulse. 0 s disables this requirement.",)),
    DetField("rest_before_tolerance_pct", "Rest Before Tolerance", "%", False, (
        "Accepted deviation from Rest Before, as a percentage of that duration.",)),
    DetField("rest_after_s", "Rest After", "s", False, (
        "Required duration of the rest segment just after the pulse. 0 s disables this requirement.",)),
    DetField("rest_after_tolerance_pct", "Rest After Tolerance", "%", False, (
        "Accepted deviation from Rest After, as a percentage of that duration.",)),
)
POLARITY_POPOVER = ("Which pulses to look for: charge pulses, discharge pulses or both.",)
DET_BY_KEY = {f.key: f for f in DET_FIELDS}


def parse_param(key: str, text: str) -> tuple[Optional[float], str]:
    f = DET_BY_KEY[key]
    t = text.strip().replace(",", ".")
    try:
        v = float(t)
    except ValueError:
        return None, ERR_GT0 if f.positive else ERR_GE0
    if not math.isfinite(v) or v < 0 or (f.positive and v == 0):
        return None, ERR_GT0 if f.positive else ERR_GE0
    return v, ""


def describe_params(p: DetectionParams) -> str:
    def rest(v, tol):
        return "not required" if v == 0 else f"{v:g} s ± {tol:g} %"
    return (f"Pulse {p.pulse_s:g} s ± {p.pulse_tolerance_pct:g} % · {POLARITY_LABELS[p.polarity]} · "
            f"Rest before {rest(p.rest_before_s, p.rest_before_tolerance_pct)} · "
            f"Rest after {rest(p.rest_after_s, p.rest_after_tolerance_pct)}")


# ---------------------------------------------------------------------------------------------------------------------
# Measurement time (requested_time_s, default 30 s), one global value for all pulses.

# GUI default chosen by the Author (2026-10-27): 30 s, i.e. the end of the default 30 s pulse. The GUI always
# passes this value to the core, so the core's own 10 s default is not used.
DEFAULT_MEASUREMENT_TIME_S = 30.0
ERR_TIME = "Enter a time greater than 0 s."


def parse_measurement_time(text: str) -> tuple[Optional[float], str]:
    t = text.strip().replace(",", ".")
    try:
        v = float(t)
    except ValueError:
        return None, ERR_TIME
    if not math.isfinite(v) or v <= 0:
        return None, ERR_TIME
    return v, ""


ERR_CURRENT_TIME = "Enter a time greater than 0 s, or leave empty for the 3rd pulse sample."


def parse_current_time(text: str) -> tuple[Optional[float], str]:
    """Optional I1 time after V0 [s]. Empty -> (None, ''): the core default, the 3rd pulse sample."""
    t = text.strip().replace(",", ".")
    if not t:
        return None, ""
    try:
        v = float(t)
    except ValueError:
        return None, ERR_CURRENT_TIME
    if not math.isfinite(v) or v <= 0:
        return None, ERR_CURRENT_TIME
    return v, ""


# ---------------------------------------------------------------------------------------------------------------------
# Events and measurements as the GUI holds them (mirrors of DcirEvent / DcirMeasurement fields).

PENDING, ACCEPTED, REJECTED = "pending", "accepted", "rejected"
AUTO, MANUAL = "auto", "manual"


@dataclass(frozen=True)
class EventView:
    event_id: str
    source: str                                  # auto | manual
    state: str                                   # as published / detected (baseline)
    event_time_s: Optional[float] = None
    cycle: Optional[int] = None
    half_cycle: Optional[int] = None
    polarity: Optional[str] = None               # charge | discharge | None (manual)
    pulse_duration_s: Optional[float] = None
    pre_pulse_row: Optional[int] = None
    acceptance_order: Optional[int] = None
    time_display: Optional[float] = None         # event time in the display time unit (adapter)
    pulse_first_row: Optional[int] = None        # auto: pulse segment rows (correction span)
    pulse_last_row: Optional[int] = None
    correction_start_row: Optional[int] = None   # manual: the two boundary rows (sorted by the core), else None
    correction_end_row: Optional[int] = None


@dataclass(frozen=True)
class MeasurementView:
    """DcirMeasurement as shown: currents, voltages and resistance in the DISPLAY units requested from the adapter; times always in seconds. None = null in the core (never filled by the GUI)."""
    event_id: str
    requested_time_s: Optional[float]
    actual_time_s: Optional[float]
    time_deviation_s: Optional[float]
    i_ref: Optional[float]
    v_ref: Optional[float]
    i_meas: Optional[float]
    v_meas: Optional[float]
    delta_i: Optional[float]
    delta_v: Optional[float]
    resistance: Optional[float]
    soc_pct: Optional[float]
    diagnostics: tuple[str, ...] = ()
    reference_row: Optional[int] = None
    current_row: Optional[int] = None
    voltage_row: Optional[int] = None
    manual_values: Optional[dict] = None         # manual: the four ManualValue (+ 'boundary') as created
    current_time_s: Optional[float] = None       # actual time of the I1 sample after V0 (auto), from the core rows


@dataclass(frozen=True)
class ManualValue:
    """One of I0, V0, I1, V1 (ManualValue + ValueProvenance). sample_row set = 'sample' provenance (a real elaborated row; the
    value is the exact component of that row); None = 'typed'."""
    value: float
    sample_row: Optional[int] = None
    sample_time_s: Optional[float] = None
    cycle: Optional[int] = None

    @property
    def provenance(self) -> str:
        return "sample" if self.sample_row is not None else "typed"


MANUAL_KEYS = (("i0", "I0 · reference current", "current"), ("v0", "V0 · reference voltage", "voltage"),
               ("i1", "I1 · pulse current", "current"), ("v1", "V1 · pulse voltage", "voltage"))


@dataclass(frozen=True)
class Boundary:
    """One correction boundary: a real elaborated sample (SampleRef). The core sorts the two rows."""
    row: int
    time_s: float
    time_display: float
    cycle: Optional[int] = None


def boundary_error(values: dict) -> str:
    """Optional correction span of a manual measurement (in the manual dialog). '' = none or valid."""
    b = values.get("boundary")
    if not b:
        return ""
    a, c = b
    if a is None or c is None:
        return "Pick the second boundary point, or clear the span."
    if a.row == c.row:
        return explain("DCIR_MANUAL_CORRECTION_BOUNDARY_INVALID")
    return ""


def manual_ready(values: dict) -> str:
    """'' when the four values exist and are finite, else the message shown in the dialog."""
    for key, label, _q in MANUAL_KEYS:
        v = values.get(key)
        if v is None or not math.isfinite(v.value):
            return label.split(" \u00b7")[0] + " is missing."
    return boundary_error(values)


@dataclass(frozen=True)
class DetectionResult:
    result_id: str
    elab_result_id: str
    params: DetectionParams
    events: tuple[EventView, ...]


@dataclass(frozen=True)
class PublishedResult:
    result_id: str
    events: tuple[EventView, ...]                # states as published
    measurements: tuple[MeasurementView, ...]
    measurement_time_s: float
    labels: tuple[tuple[str, str], ...] = ()     # (event_id, "DCIRn") from event_labels()
    published_at: str = ""
    fingerprint: str = ""                          # accepted_fingerprint of the core (Cycle Correction basis)
    current_time_s: Optional[float] = None         # I1 time used for automatic pulses (None: 3rd pulse sample)
    event_times: tuple = ()                        # (event_id, V1 time, I1 time | None): pulses with their own times


# ---------------------------------------------------------------------------------------------------------------------
# Diagnostics. Sentences are designer drafts.

DIAGNOSTIC_TEXT = {
    "DCIR_NO_CANDIDATES": "No pulse matches the detection parameters.",
    "DCIR_MISSING_PRE_PULSE_REFERENCE": "There is no recorded point just before the pulse to use as reference.",
    "DCIR_ZERO_CURRENT_DIFFERENCE": "Reference and pulse current are equal: the resistance is not available.",
    "DCIR_TIME_AFTER_LAST_SAMPLE": "The measurement time is after the end of the pulse: the last recorded point of the "
                                   "pulse is used (no extrapolation).",
    "DCIR_SOC_UNAVAILABLE": "SOC is not available: the half-cycles around this pulse do not give a unique state of charge.",
    "DCIR_MANUAL_CORRECTION_BOUNDARY_INVALID": "The two boundary points are not distinct.",
    "NUMERIC_RESULT_UNREPRESENTABLE": "The result cannot be represented as a number.",
    "OPERATION_CANCELLED": "The operation was cancelled. Nothing was saved.",
}


def explain(code: str) -> str:
    t = DIAGNOSTIC_TEXT.get(code)
    return f"{t} ({code})" if t else code


# ---------------------------------------------------------------------------------------------------------------------
# State.

class Step(str, Enum):
    DETECTION = "detection"
    REVIEW = "review"
    RESULTS = "results"
    CORRECTION = "correction"


@dataclass
class DcirState:
    # detection parameters as typed
    params: DetectionParams = FACTORY_DETECTION
    param_texts: dict = field(default_factory=dict)
    param_errors: dict = field(default_factory=dict)
    # runs (detect / publish share one slot: one operation at a time per dataset)
    run: Run = Run.IDLE
    run_kind: str = ""                           # "detect" | "publish"
    outcome: Outcome = Outcome.NONE
    message: str = ""
    progress: Optional[float] = None
    phase: str = ""
    # results
    detection: Optional[DetectionResult] = None
    published: Optional[PublishedResult] = None
    elab_changed: bool = False
    # local review (unpublished)
    decisions: dict = field(default_factory=dict)        # event_id -> state
    orders: dict = field(default_factory=dict)           # event_id -> acceptance_order
    manual: dict = field(default_factory=dict)           # event_id -> (EventView, MeasurementView, values dict)
    previews: dict = field(default_factory=dict)         # event_id -> MeasurementView (at measurement_time_s)
    labels: dict = field(default_factory=dict)           # event_id -> "DCIRn" (from the core, for accepted events)
    measurement_time_s: float = DEFAULT_MEASUREMENT_TIME_S
    time_text: str = ""
    time_error: str = ""
    current_time_s: Optional[float] = None       # I1 time after V0 [s]; None = 3rd pulse sample (core default)
    current_time_text: str = ""
    current_time_error: str = ""
    # per-pulse V1 / I1 times (auto pulses only): event_id -> (V1 time [s], I1 time [s] | None). A pulse without an
    # entry uses the all-pulse times above. Typed values are a draft until Apply (this pulse) or Apply to All.
    event_times: dict = field(default_factory=dict)
    draft_event: Optional[str] = None            # pulse the draft texts below belong to
    draft_v1_text: Optional[str] = None          # None = no draft: the fields show the pulse's times
    draft_i1_text: Optional[str] = None
    last_decided: Optional[str] = None           # event most recently accepted / rejected (manual dialog start)
    # view
    step: Step = Step.DETECTION
    selected: Optional[str] = None
    f_state: str = "all"                          # all | pending | accepted | rejected
    f_polarity: str = BOTH
    f_source: str = "all"                         # all | auto | manual
    show_zones: bool = True                       # inspection chart: shade rest / pulse zones (display only)
    res_color: str = "#006D85"                    # Results: the single colour of the points (CHART y1 by default)

    # --- parameters ---
    def set_param_text(self, key: str, text: str) -> None:
        v, err = parse_param(key, text)
        if err:
            self.param_errors[key], self.param_texts[key] = err, text
        else:
            self.param_errors.pop(key, None)
            self.param_texts.pop(key, None)
            self.params = replace(self.params, **{key: v})

    def set_polarity(self, pol: str) -> None:
        if pol in POLARITY_LABELS:
            self.params = replace(self.params, polarity=pol)

    def reset_params(self, defaults: DetectionParams = FACTORY_DETECTION) -> None:
        self.params, self.param_texts, self.param_errors = defaults, {}, {}

    # --- events ---
    def events(self) -> list[EventView]:
        """All events (detected + local manual), chronological (no time last), each with its current local state."""
        base = list(self.detection.events) if self.detection else (list(self.published.events) if self.published else [])
        known = {e.event_id for e in base}
        base += [m[0] for m in self.manual.values() if m[0].event_id not in known]
        out = [replace(e, state=self.decisions.get(e.event_id, e.state)) for e in base]
        return sorted(out, key=lambda e: (e.event_time_s is None, e.event_time_s or 0.0, e.event_id))

    def event(self, event_id: Optional[str]) -> Optional[EventView]:
        return next((e for e in self.events() if e.event_id == event_id), None)

    def label(self, e: EventView) -> str:
        """DCIRn (core) for accepted events; 'Pulse n' (GUI number in detection order) otherwise."""
        if e.state == ACCEPTED and e.event_id in self.labels:
            return self.labels[e.event_id]
        autos = [x.event_id for x in self.events() if x.source == AUTO]
        if e.source == AUTO and e.event_id in autos:
            return f"Pulse {autos.index(e.event_id) + 1}"
        return "Manual"

    def counts(self) -> dict:
        ev = self.events()
        c = {PENDING: 0, ACCEPTED: 0, REJECTED: 0, MANUAL: 0, "total": len(ev)}
        for e in ev:
            c[e.state] += 1
            c[MANUAL] += e.source == MANUAL
        return c

    def visible(self) -> list[EventView]:
        return [e for e in self.events()
                if (self.f_state == "all" or e.state == self.f_state)
                and (self.f_polarity == BOTH or e.polarity == self.f_polarity)
                and (self.f_source == "all" or e.source == self.f_source)]

    def move(self, delta: int) -> Optional[str]:
        vis = [e.event_id for e in self.visible()]
        if not vis:
            return None
        i = vis.index(self.selected) if self.selected in vis else (-1 if delta > 0 else len(vis))
        self.selected = vis[max(0, min(len(vis) - 1, i + delta))]
        return self.selected

    def can_accept(self, e: Optional[EventView]) -> tuple[bool, str]:
        if e is None or self.is_running:
            return False, ""
        if e.state == ACCEPTED:
            return False, "Already accepted"
        if e.source == AUTO and e.pre_pulse_row is None:
            return False, explain("DCIR_MISSING_PRE_PULSE_REFERENCE")
        return True, ""

    def can_reject(self, e: Optional[EventView]) -> tuple[bool, str]:
        if e is None or self.is_running:
            return False, ""
        return (e.state != REJECTED), ("Already rejected" if e.state == REJECTED else "")

    def decide(self, event_id: str, state: str) -> bool:
        """Local decision (not published). Manual events can be rejected and accepted again, never set to Pending."""
        e = self.event(event_id)
        if e is None or state not in (ACCEPTED, REJECTED, PENDING):
            return False
        if state == PENDING and e.source == MANUAL:
            return False
        ok = self.can_accept(e)[0] if state == ACCEPTED else (self.can_reject(e)[0] if state == REJECTED else True)
        if not ok:
            return False
        base = self._baseline(event_id)
        if base == state and event_id not in self.manual:
            self.decisions.pop(event_id, None)
        else:
            self.decisions[event_id] = state
        if state != PENDING:
            self.last_decided = event_id
        if state == ACCEPTED:
            self.orders[event_id] = max(self.orders.values(), default=-1) + 1
        else:
            self.orders.pop(event_id, None)
        return True

    def replace_manual(self, old_id: str, event: EventView, measurement: MeasurementView, values: dict) -> None:
        """'Set Span…': a manual measurement cannot be edited (its identity includes its values); the new one is added
        Accepted and the old one is rejected (manual events are never deleted, never Pending)"""
        if old_id in self.manual:
            self.manual.pop(old_id)
            self.decisions.pop(old_id, None)
            self.orders.pop(old_id, None)
        elif self.event(old_id) is not None:
            self.decisions[old_id] = REJECTED
            self.orders.pop(old_id, None)
        self.add_manual(event, measurement, values)

    def _baseline(self, event_id: str) -> Optional[str]:
        for src in (self.published.events if self.published else (), self.detection.events if self.detection else ()):
            for e in src:
                if e.event_id == event_id:
                    return e.state
        return None

    def add_manual(self, event: EventView, measurement: MeasurementView, values: dict) -> None:
        """create_manual_event result (already Accepted, SOC None)."""
        self.manual[event.event_id] = (event, measurement, values)
        self.decisions[event.event_id] = ACCEPTED
        self.orders[event.event_id] = max(self.orders.values(), default=-1) + 1
        self.selected = event.event_id

    # --- measurement time / previews ---
    def set_time_text(self, text: str) -> bool:
        """Returns True when the time changed: previews of accepted auto pulses must be re-measured."""
        self.time_text = text
        v, err = parse_measurement_time(text)
        self.time_error = err
        if err or v == self.measurement_time_s:
            return False
        self.measurement_time_s = v
        self._drop_auto_previews()
        return True

    def set_current_time_text(self, text: str) -> bool:
        """Optional I1 time. Returns True when it changed: accepted auto pulses must be re-measured."""
        self.current_time_text = text
        v, err = parse_current_time(text)
        self.current_time_error = err
        if err or v == self.current_time_s:
            return False
        self.current_time_s = v
        self._drop_auto_previews()
        return True

    def _drop_auto_previews(self) -> None:
        """Forget the previews of auto pulses measured at the all-pulse times (pulses with their own times keep
        theirs: the all-pulse times do not apply to them)."""
        self.previews = {k: m for k, m in self.previews.items()
                         if (self.event(k) and self.event(k).source == MANUAL) or k in self.event_times}

    # --- per-pulse times ---
    def times_for(self, event_id: Optional[str]) -> tuple[float, Optional[float]]:
        """(V1 time, I1 time | None) used to measure ``event_id``: its own times when set, else the all-pulse times."""
        return self.event_times.get(event_id, (self.measurement_time_s, self.current_time_s))

    def has_own_times(self, event_id: Optional[str]) -> bool:
        """True when ``event_id`` is measured at times of its own instead of the all-pulse times."""
        return event_id in self.event_times

    def set_draft_text(self, which: str, text: str) -> None:
        """Typed V1 (``which='v1'``) or I1 (``'i1'``) time for the selected pulse. Nothing is applied until Apply or
        Apply to All; a draft belongs to the pulse selected when it was typed."""
        if self.draft_event != self.selected:
            self.clear_draft()
            self.draft_event = self.selected
        if which == "v1":
            self.draft_v1_text = text
        else:
            self.draft_i1_text = text

    def clear_draft(self) -> None:
        """Discard the typed times (selection changed, or the draft was applied)."""
        self.draft_event = self.draft_v1_text = self.draft_i1_text = None

    @property
    def has_draft(self) -> bool:
        """True when typed times of the selected pulse differ from the times it is measured at."""
        if self.draft_event != self.selected or (self.draft_v1_text is None and self.draft_i1_text is None):
            return False
        v1, i1, err = self.draft_times()
        return bool(err) or (v1, i1) != self.times_for(self.selected)

    def draft_texts(self) -> tuple[str, str]:
        """Texts the V1 / I1 fields show: the draft of the selected pulse, else its current times."""
        v1, i1 = self.times_for(self.selected)
        own = self.draft_event == self.selected
        return ((self.draft_v1_text if own and self.draft_v1_text is not None else f"{v1:g}"),
                (self.draft_i1_text if own and self.draft_i1_text is not None else ("" if i1 is None else f"{i1:g}")))

    def draft_times(self) -> tuple[Optional[float], Optional[float], str]:
        """(V1, I1, error) parsed from the fields. error is '' when both are valid (I1 None = 3rd pulse sample)."""
        v1_text, i1_text = self.draft_texts()
        v1, err1 = parse_measurement_time(v1_text)
        i1, err2 = parse_current_time(i1_text)
        return v1, i1, (err1 and f"V1: {err1}") or (err2 and f"I1: {err2}")

    def can_apply_to_pulse(self, e: Optional[EventView]) -> tuple[bool, str]:
        """Per-pulse times apply to automatic pulses with a reference sample, outside a running operation."""
        if e is None or self.is_running:
            return False, ""
        if e.source != AUTO:
            return False, "Manual measurements have no measurement times."
        if e.pre_pulse_row is None:
            return False, explain("DCIR_MISSING_PRE_PULSE_REFERENCE")
        return True, ""

    def apply_times_to_pulse(self) -> bool:
        """Apply: the typed times become the selected pulse's own times. A pulse whose times change must be reviewed
        again: it returns to Pending, which blocks Apply & Calculate until it is accepted or rejected. Times equal to
        the all-pulse times remove the pulse's own times. Returns True when the pulse's times changed."""
        e = self.event(self.selected)
        v1, i1, err = self.draft_times()
        if err or e is None or not self.can_apply_to_pulse(e)[0]:
            return False
        before = self.times_for(e.event_id)
        if (v1, i1) == (self.measurement_time_s, self.current_time_s):
            self.event_times.pop(e.event_id, None)
        else:
            self.event_times[e.event_id] = (v1, i1)
        self.clear_draft()
        if self.times_for(e.event_id) == before:
            return False
        self.previews.pop(e.event_id, None)
        if e.state != PENDING:
            self.decide(e.event_id, PENDING)
        return True

    def apply_times_to_all(self) -> bool:
        """Apply to All: the typed times become the all-pulse times and every pulse's own times are removed.
        Accepted pulses keep their decision and are measured again. Returns True when any pulse's times changed."""
        v1, i1, err = self.draft_times()
        if err or v1 is None or self.is_running:
            return False
        changed = (v1, i1) != (self.measurement_time_s, self.current_time_s) or bool(self.event_times)
        self.measurement_time_s, self.current_time_s = v1, i1
        self.time_text = self.current_time_text = ""
        self.time_error = self.current_time_error = ""
        self.event_times = {}
        self.clear_draft()
        if changed:
            self.previews = {k: m for k, m in self.previews.items() if self.event(k) and self.event(k).source == MANUAL}
        return changed

    def manual_start_event(self) -> Optional[EventView]:
        """Where the manual dialog opens: the pulse decided last, else the selected one, else the latest pulse."""
        for eid in (self.last_decided, self.selected):
            e = self.event(eid)
            if e is not None and e.event_time_s is not None:
                return e
        timed = [e for e in self.events() if e.event_time_s is not None]
        return timed[-1] if timed else None

    def to_measure(self) -> list[str]:
        """Accepted auto events (plus the selected pending one) without a preview at the current time."""
        ids = [e.event_id for e in self.events() if e.source == AUTO and (e.state == ACCEPTED or e.event_id == self.selected)
               and e.state != REJECTED]
        return [i for i in ids if i not in self.previews]

    def measurement(self, event_id: Optional[str]) -> Optional[MeasurementView]:
        if event_id in self.manual:
            return self.manual[event_id][1]
        if event_id in self.previews:
            return self.previews[event_id]
        e = self.event(event_id)
        if e is not None and e.source == MANUAL and self.published is not None:      # published manual measurement
            return next((m for m in self.published.measurements if m.event_id == event_id), None)
        return None

    # --- publish ---
    @property
    def unpublished(self) -> bool:
        """Local changes that are not in the published result ('Changes not applied'): a decision that differs from
        the detected/published state, a manual measurement not yet published, or a different measurement time."""
        if self.decisions or self.manual:
            return True
        return self.published is not None and (self.measurement_time_s != self.published.measurement_time_s
                                               or self.current_time_s != self.published.current_time_s
                                               or self.event_times != published_event_times(self.published))

    def publish_blocker(self) -> str:
        if self.is_running:
            return "An operation is running"
        pend = self.counts()[PENDING]
        if pend:
            return f"Accept or reject the {pend:,} pending pulse{'s' if pend > 1 else ''} first"
        acc = [e for e in self.events() if e.state == ACCEPTED]
        if not acc:
            return "Accept at least one pulse"
        missing = [e for e in acc if self.measurement(e.event_id) is None]
        if missing:
            return f"Measuring {len(missing)} accepted pulse{'s' if len(missing) > 1 else ''}…"
        if self.time_error:
            return self.time_error
        if self.current_time_error:
            return self.current_time_error
        return ""

    def redetect_info(self) -> tuple[int, int, bool]:
        """(pending, rejected, unpublished) decisions that a new detection will not keep (only Accepted are kept)."""
        c = self.counts()
        return c[PENDING], c[REJECTED], self.unpublished

    # --- runs ---
    @property
    def is_running(self) -> bool:
        return self.run is not Run.IDLE

    def begin(self, kind: str) -> None:
        self.run, self.run_kind, self.outcome, self.message, self.progress, self.phase = Run.RUNNING, kind, Outcome.NONE, "", None, ""

    def update_progress(self, fraction: Optional[float], phase: str, publishing: bool = False) -> None:
        if self.run is not Run.IDLE:
            self.progress, self.phase = fraction, phase
            if publishing and self.run is Run.RUNNING:
                self.run = Run.PUBLISHING

    def request_cancel(self) -> bool:
        if self.run in (Run.RUNNING, Run.PUBLISHING):
            self.run = Run.CANCELLING
            return True
        return False

    def detected(self, res: DetectionResult) -> None:
        """New detection published: only Accepted events survive (core rule); local Pending/Rejected decisions go."""
        keep = {e.event_id for e in res.events if e.state == ACCEPTED}
        base = {e.event_id: e.state for e in res.events}
        self.decisions = {k: v for k, v in self.decisions.items() if (k in keep and v != base[k]) or k in self.manual}
        self.orders = {k: v for k, v in self.orders.items() if k in keep or k in self.manual}
        self.previews = {k: v for k, v in self.previews.items() if k in keep}
        self.event_times = {k: v for k, v in self.event_times.items() if k in keep}
        self.clear_draft()
        self.detection, self.run, self.outcome, self.elab_changed = res, Run.IDLE, Outcome.NONE, False
        self.step = Step.REVIEW
        pend = [e.event_id for e in self.events() if e.state == PENDING]
        self.selected = pend[0] if pend else (self.events()[0].event_id if self.events() else None)

    def published_ok(self, res: PublishedResult) -> None:
        """Apply & Calculate succeeded: adopt the published result and show it in Results."""
        self.published, self.run, self.outcome = res, Run.IDLE, Outcome.NONE
        self.step = Step.RESULTS
        self.labels = dict(res.labels)
        self.decisions = {}
        self.orders = {e.event_id: e.acceptance_order for e in res.events if e.acceptance_order is not None}
        self.manual = {}
        self.event_times = published_event_times(res)
        if self.detection:                    # the published event set (all states, manual included)
            self.detection = replace(self.detection, events=res.events)

    def cancelled(self) -> None:
        self.run, self.outcome = Run.IDLE, Outcome.CANCELLED

    def failed(self, message: str) -> None:
        self.run, self.outcome, self.message = Run.IDLE, Outcome.FAILED, message

    def elaboration_replaced(self) -> bool:
        """A new Data Elaboration result: events and measurements belong to the old samples and are not shown."""
        had = self.detection is not None or self.published is not None or bool(self.manual)
        self.detection = self.published = None
        self.decisions, self.orders, self.manual, self.previews, self.labels = {}, {}, {}, {}, {}
        self.event_times = {}
        self.clear_draft()
        self.elab_changed = had or self.elab_changed
        self.step, self.selected, self.last_decided = Step.DETECTION, None, None
        return had


def published_event_times(pub: Optional[PublishedResult]) -> dict:
    """event_id -> (V1 time, I1 time | None) of the pulses a published result measured at times of their own."""
    return {eid: (v1, i1) for eid, v1, i1 in pub.event_times} if pub is not None else {}


def split_event_times(pairs: dict) -> tuple[float, Optional[float], dict]:
    """All-pulse times and per-pulse times recovered from the times each auto measurement was resolved with
    (``pairs``: event_id -> (V1, I1 | None), in pulse order). The most frequent pair is taken as the all-pulse times
    (the earliest pulse wins a tie); pulses measured at another pair keep it as their own. Used when a published
    result is read back without the session state that created it. With no auto measurement the core default
    (10 s, 3rd pulse sample) is returned."""
    if not pairs:
        return 10.0, None, {}
    counts: dict = {}
    for pair in pairs.values():
        counts[pair] = counts.get(pair, 0) + 1
    best = max(counts, key=lambda pair: counts[pair])     # max keeps the first pair met on ties (insertion order)
    return best[0], best[1], {eid: pair for eid, pair in pairs.items() if pair != best}


# ---------------------------------------------------------------------------------------------------------------------
# Results (published only).

RESULT_COLUMNS = (
    ("label", "Pulse", None), ("source", "Source", None), ("cycle", "Cycle", None), ("polarity", "Polarity", None),
    ("time_display", "Time", "time"), ("requested_time_s", "Requested time [s]", None),
    ("actual_time_s", "Actual time [s]", None), ("i_ref", "I0", "current"), ("v_ref", "V0", "voltage"),
    ("i_meas", "I1", "current"), ("v_meas", "V1", "voltage"), ("delta_i", "ΔI", "current"),
    ("delta_v", "ΔV", "voltage"), ("resistance", "Resistance", "resistance"), ("soc_pct", "SOC [%]", None),
    ("diagnostics", "Diagnostics", None),
)


def result_rows(pub: Optional[PublishedResult]) -> list[tuple[dict, dict]]:
    """Rows of the Results table and of the DCIR export: one per published measurement, in pulse-time order, as
    (values by RESULT_COLUMNS key, tooltip by key). Values are those the adapter published in display units; a
    missing value stays None (never 0)."""
    if pub is None:
        return []
    evs, lab = {e.event_id: e for e in pub.events}, dict(pub.labels)
    out = []
    ordered = sorted((m for m in pub.measurements if m.event_id in evs),
                     key=lambda m: (evs[m.event_id].event_time_s is None, evs[m.event_id].event_time_s or 0))
    for m in ordered:
        e = evs[m.event_id]
        row = {"label": lab.get(m.event_id, ""), "source": "Manual" if e.source == MANUAL else "Auto", "cycle": e.cycle,
               "polarity": POLARITY_LABELS.get(e.polarity, None) if e.polarity else None, "time_display": e.time_display,
               "diagnostics": " ".join(m.diagnostics) or None}
        for key, _t, _q in RESULT_COLUMNS:
            if key not in row:
                row[key] = getattr(m, key)
        tip = {"soc_pct": "SOC is not calculated for manual measurements." if e.source == MANUAL else
               (explain("DCIR_SOC_UNAVAILABLE") if m.soc_pct is None else None),
               "resistance": explain("DCIR_ZERO_CURRENT_DIFFERENCE") if m.resistance is None else None,
               "diagnostics": "\n".join(explain(x) for x in m.diagnostics) or None}
        out.append((row, tip))
    return out


def result_heads(units) -> list[str]:
    """Column titles of RESULT_COLUMNS with the display unit of each quantity (``units``: UnitPrefs)."""
    return [f"{title} [{units.unit(q)}]" if q else title for _key, title, q in RESULT_COLUMNS]


def result_points(pub: Optional[PublishedResult]) -> tuple[list[tuple[int, float, str]], int]:
    """(cycle, resistance, event_id) of published Accepted measurements that have both values; and how many accepted
    measurements were left out (no cycle or no resistance)."""
    if pub is None:
        return [], 0
    ev = {e.event_id: e for e in pub.events}
    pts, left = [], 0
    for m in pub.measurements:
        e = ev.get(m.event_id)
        if e is None or e.state != ACCEPTED:
            continue
        if e.cycle is None or m.resistance is None or not math.isfinite(m.resistance):
            left += 1
            continue
        pts.append((e.cycle, m.resistance, m.event_id))
    return sorted(pts), left
