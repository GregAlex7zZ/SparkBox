"""Data Elaboration: parameters, validation, result state machine and view-state derivation. PURE PYTHON (no Qt),
unit-tested in tests/test_elaboration.py. The GUI never interprets scientific meaning; it only stores the values the user
typed, validates the shape (finite number >= 0) and hands them to the backend adapter."""
from __future__ import annotations
import math
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Optional

CHARGE_FIRST = "charge_discharge"
DISCHARGE_FIRST = "discharge_charge"
CYCLE_ORDER_LABELS = {CHARGE_FIRST: "Charge → Discharge", DISCHARGE_FIRST: "Discharge → Charge"}


@dataclass(frozen=True)
class ElabParams:
    """Values exactly as the user sees them (GUI units). The backend adapter converts to the library units
    (A, V, fraction)."""
    cycle_order: str = CHARGE_FIRST
    rest_current_threshold_ma: float = 0.0
    cc_relative_tolerance_pct: float = 1.0
    cv_absolute_tolerance_mv: float = 1.0


FACTORY_DEFAULTS = ElabParams()          # equal to gregorithm's ElaborationParameters defaults


@dataclass(frozen=True)
class NumField:
    key: str                  # ElabParams attribute
    label: str
    unit: str
    group: str
    popover: tuple[str, ...]  # paragraphs shown by the (i) button


GROUP_CYCLE = "Cycle Detection"
GROUP_CCCV = "CC / CV Classification"

# Popover texts: the Rest Current Threshold text is the approved wording from the mockup. The other three are
# DESIGNER DRAFTS that stay deliberately generic; the Author/reviewer must replace them with wording checked against
# the gregorithm documentation (SPEC open issue O-14).
CYCLE_ORDER_POPOVER = (
    "Sets which half-cycle is considered the first one of a cycle.",
    "Charge → Discharge: a cycle starts with the charge half-cycle. Discharge → Charge: it starts with the discharge.",
    "Changing it can change the cycle count and every cycle-based result. Run Process or Reprocess to apply it.",
)
NUM_FIELDS = (
    NumField("rest_current_threshold_ma", "Rest Current Threshold", "mA", GROUP_CYCLE, (
        "A point is classified as Rest during segmentation when its absolute current is less than or equal to this threshold.",
        "At 0 mA only exactly zero current counts as rest. Larger values also include small currents. This can change "
        "detected rests, segmentation and the CC / CV classification.",
        "Default 0 mA. Changing the value does not alter the existing result: run Process or Reprocess to apply it.")),
    NumField("cc_relative_tolerance_pct", "CC Relative Tolerance", "%", GROUP_CCCV, (
        "Relative tolerance used when deciding whether a charge or discharge step is a constant-current (CC) step.",
        "A larger value accepts more variation of the current inside a CC step. This can change the CC / CV classification.",
        "Changing the value does not alter the existing result: run Process or Reprocess to apply it.")),
    NumField("cv_absolute_tolerance_mv", "CV Absolute Tolerance", "mV", GROUP_CCCV, (
        "Absolute voltage tolerance used when deciding whether a step is a constant-voltage (CV) step.",
        "A larger value accepts more variation of the voltage inside a CV step. This can change the CC / CV classification.",
        "Changing the value does not alter the existing result: run Process or Reprocess to apply it.")),
)
ERR_NONNEG = "Enter a number that is 0 or greater."          # approved wording (mockup)


def fmt_param(v: float) -> str:
    return f"{v:g}"


def parse_number(text: str) -> tuple[Optional[float], str]:
    """Parse a user-typed number ('.' or ',' decimal). Returns (value, error). Shape validation only: finite, >= 0.
    Any further limit is the library's decision and arrives as a failure message (never guessed here)."""
    t = text.strip().replace(",", ".")
    if not t:
        return None, ERR_NONNEG
    try:
        v = float(t)
    except ValueError:
        return None, ERR_NONNEG
    if not math.isfinite(v) or v < 0:
        return None, ERR_NONNEG
    return v, ""


SAMPLE_FIELDS = (("active_mass_mg", "Active Material Mass", "mg"),
                 ("surface_area_cm2", "Surface Area", "cm²"))
SAMPLE_LABEL = {k: (label, unit) for k, label, unit in SAMPLE_FIELDS}
ERR_POSITIVE = "Enter a number greater than 0, or leave it empty."
SAMPLE_POPOVER = (
    "Mass of active material and electrode surface area of this sample.",
    "Optional: leave them empty if you do not need them. They apply only to this dataset and are not saved when the "
    "dataset is closed.",
)


@dataclass(frozen=True)
class SampleValues:
    active_mass_mg: Optional[float] = None
    surface_area_cm2: Optional[float] = None


def parse_optional_positive(text: str) -> tuple[Optional[float], str]:
    """Validate optional per-dataset sample metadata without affecting elaboration."""
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


def with_value(p: ElabParams, key: str, value) -> ElabParams:
    return replace(p, **{key: value})


def modified_keys(p: ElabParams, baseline: ElabParams) -> set[str]:
    return {k for k in ("cycle_order", *[f.key for f in NUM_FIELDS]) if getattr(p, k) != getattr(baseline, k)}


# --- result & run state -----------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ElabResult:
    """Published result summary (all numbers are reported by the library, never computed by the GUI)."""
    result_id: str
    n_cycles: int
    n_half_cycles: int
    n_segments: int
    n_rows: int
    params: ElabParams                      # the parameters this result was calculated with
    elapsed_s: Optional[float] = None
    details: tuple[tuple[str, str], ...] = ()
    correction: str = ""


class Run(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    CANCELLING = "cancelling"
    PUBLISHING = "publishing"


class Outcome(str, Enum):
    NONE = "none"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ElabView(str, Enum):
    NOT_PROCESSED = "notprocessed"
    INVALID = "invalid"
    CURRENT = "current"
    OUTDATED = "modified"
    PROCESSING = "processing"
    CANCELLING = "cancelling"
    PUBLISHING = "publishing"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class ElabState:
    """Per-dataset elaboration state. `params` is what the dock shows (dirty), `result.params` what the published
    result used. A cancelled/failed run never replaces `result` (previous result preserved)."""
    params: ElabParams = FACTORY_DEFAULTS
    errors: dict = field(default_factory=dict)         # key -> message (invalid typed values)
    texts: dict = field(default_factory=dict)          # key -> raw typed text while invalid
    result: Optional[ElabResult] = None
    run: Run = Run.IDLE
    outcome: Outcome = Outcome.NONE
    message: str = ""                                  # failure reason, exactly as reported
    progress: Optional[float] = None
    phase: str = ""
    done: Optional[int] = None
    total: Optional[int] = None
    run_params: Optional[ElabParams] = None            # parameters sent with the running job
    sample: SampleValues = field(default_factory=SampleValues)
    sample_errors: dict = field(default_factory=dict)
    sample_texts: dict = field(default_factory=dict)

    # --- derived ---
    @property
    def has_result(self) -> bool:
        return self.result is not None

    @property
    def is_running(self) -> bool:
        return self.run is not Run.IDLE

    @property
    def baseline(self) -> Optional[ElabParams]:
        return self.result.params if self.result else None

    @property
    def dirty(self) -> bool:
        return self.result is not None and self.params != self.result.params

    def modified(self, user_defaults: ElabParams) -> set[str]:
        return modified_keys(self.params, self.result.params if self.result else user_defaults)

    @property
    def valid(self) -> bool:
        return not self.errors

    def view(self) -> ElabView:
        if self.run is Run.CANCELLING:
            return ElabView.CANCELLING
        if self.run is Run.PUBLISHING:
            return ElabView.PUBLISHING
        if self.run is Run.RUNNING:
            return ElabView.PROCESSING
        if self.outcome is Outcome.FAILED:
            return ElabView.FAILED
        if self.outcome is Outcome.CANCELLED:
            return ElabView.CANCELLED
        if self.result is None:
            return ElabView.INVALID if self.errors else ElabView.NOT_PROCESSED
        return ElabView.OUTDATED if self.dirty else ElabView.CURRENT

    # --- transitions (pure; the controller calls them) ---
    def can_process(self, other_running: bool = False) -> bool:
        return self.valid and not self.is_running and not other_running

    def begin(self) -> None:
        if not self.can_process():
            raise ValueError("cannot start: invalid parameters or a run is already active")
        self.run, self.outcome, self.message = Run.RUNNING, Outcome.NONE, ""
        self.progress, self.phase, self.done, self.total = 0.0, "", None, None
        self.run_params = self.params

    def update_progress(self, fraction, phase="", done=None, total=None, publishing=False) -> None:
        if self.run is Run.CANCELLING:
            return                                   # stays Cancelling until the library stops
        self.progress, self.phase, self.done, self.total = fraction, phase, done, total
        self.run = Run.PUBLISHING if publishing else Run.RUNNING

    def request_cancel(self) -> bool:
        """Cooperative: the library may take a moment (and may ignore the request while publishing)."""
        if self.run in (Run.RUNNING, Run.PUBLISHING):
            self.run = Run.CANCELLING
            return True
        return False

    def finish(self, result: ElabResult) -> None:
        self.result, self.run, self.outcome, self.message = result, Run.IDLE, Outcome.NONE, ""
        self.run_params = None

    def cancelled(self) -> None:
        self.run, self.outcome, self.run_params = Run.IDLE, Outcome.CANCELLED, None

    def failed(self, message: str) -> None:
        self.run, self.outcome, self.message, self.run_params = Run.IDLE, Outcome.FAILED, message, None

    def invalidate_result(self) -> None:
        """Re-import / re-map: the old result no longer belongs to the data."""
        self.result, self.outcome, self.message = None, Outcome.NONE, ""

    # --- editing ---
    def set_order(self, order: str) -> None:
        if order in CYCLE_ORDER_LABELS:
            self.params = with_value(self.params, "cycle_order", order)

    def set_number_text(self, key: str, text: str) -> None:
        v, err = parse_number(text)
        if err:
            self.errors[key], self.texts[key] = err, text
        else:
            self.errors.pop(key, None)
            self.texts.pop(key, None)
            self.params = with_value(self.params, key, v)

    def set_sample_text(self, key: str, text: str) -> None:
        """Invalid sample text is retained for editing but never stored as metadata."""
        if key not in SAMPLE_LABEL:
            raise KeyError(key)
        value, error = parse_optional_positive(text)
        if error:
            self.sample_errors[key], self.sample_texts[key] = error, text
        else:
            self.sample_errors.pop(key, None)
            self.sample_texts.pop(key, None)
            self.sample = replace(self.sample, **{key: value})

    def reset_to(self, defaults: ElabParams) -> None:
        self.params, self.errors, self.texts = defaults, {}, {}

    def can_reset(self, defaults: ElabParams) -> bool:
        return bool(self.errors) or self.params != defaults


STATE_LABEL = {
    ElabView.NOT_PROCESSED: ("Not processed", "n"), ElabView.INVALID: ("Not processed", "n"),
    ElabView.CURRENT: ("Current", "ok"), ElabView.OUTDATED: ("Outdated", "warn"),
    ElabView.PROCESSING: ("Processing", "p"), ElabView.CANCELLING: ("Cancelling…", "p"),
    ElabView.PUBLISHING: ("Saving…", "p"), ElabView.CANCELLED: ("Cancelled", "n"),
    ElabView.FAILED: ("Failed", "err"),
}

