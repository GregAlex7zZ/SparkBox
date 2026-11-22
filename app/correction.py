"""Cycle Correction (DCIR step 3): spans, parameters, run lifecycle and states. PURE PYTHON (no Qt), tested in
tests/test_correction.py.

gregorithm `recalculate_cycles` excludes the spans of the published Accepted DCIR measurements (auto: pulse segment;
manual: the two boundary samples) from the cycle calculation and publishes a NEW corrected elaboration, a new Capacity
result and updated DCIR events/measurements. Recorded points are never deleted; DCIR is not detected again; dQ/dV is not
recalculated (labels only). Removing the correction is another publication with enabled=False. The GUI computes nothing:
spans, times and counts come from the adapter.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .elaboration import Outcome, Run

DEFAULT_THRESHOLD_A = 0.0                      # CycleCorrectionParameters.rest_current_threshold_a
ERR_THRESHOLD = "Enter a current that is 0 or greater."
THRESHOLD_INFO = (
    "Current below which a sample counts as rest when gregorithm rebuilds the cycle labels after the exclusion "
    "(gregorithm default: 0).",
    "Shown and entered in the current unit of Settings › Units; gregorithm receives it in A.",
    "Changing it after the correction was applied makes the correction Outdated: Reapply to use it.",
)


@dataclass(frozen=True)
class Span:
    """A span to exclude, as reported by the adapter for the published DCIR result."""
    event_id: str
    label: str                                  # DCIRn
    source: str                                 # auto | manual
    start_row: Optional[int]                    # None: no span (manual without boundary points)
    end_row: Optional[int]
    start_display: Optional[float] = None       # time in the display time unit
    end_display: Optional[float] = None
    cycle: Optional[int] = None
    polarity: Optional[str] = None
    pulse_duration_s: Optional[float] = None

    @property
    def has_span(self) -> bool:
        return self.start_row is not None and self.end_row is not None


@dataclass(frozen=True)
class CorrParams:
    enabled: bool                               # False = Remove Correction (base labels again)
    rest_current_threshold: float               # display current unit (adapter converts to A)
    threshold_unit: str
    cycle_order: str                            # must equal the published elaboration's order (and Capacity's)
    capacity: object = None                     # CapParams used for the new Capacity result


@dataclass(frozen=True)
class CorrResult:
    """Published correction (or removal). The three generations are adopted by the other sections."""
    result_id: str
    enabled: bool
    elab: object                                # ElabResult (correction='dcir' when enabled)
    capacity: object                            # CapResult (corrected=True when enabled)
    dcir: object                                # PublishedResult updated by the core
    n_spans: int
    threshold: float
    threshold_unit: str
    applied_at: str = ""
    report: tuple[tuple[str, str], ...] = ()    # e.g. s4_reclassified=false, dcir_redetected=false, dqdv_recomputed=false
    dqdv: object = None                        # embedded relabelled table, never a recomputed derivative


class CorrView(str, Enum):
    UNAVAILABLE = "unavailable"                 # no published DCIR result yet
    NO_SPANS = "nospans"
    NOT_APPLIED = "notapplied"
    RUNNING = "running"
    CANCELLING = "cancelling"
    PUBLISHING = "publishing"
    APPLIED = "applied"
    OUTDATED = "outdated"
    CANCELLED = "cancelled"
    FAILED = "failed"


def parse_threshold(text: str) -> tuple[Optional[float], str]:
    t = text.strip().replace(",", ".")
    try:
        v = float(t)
    except ValueError:
        return None, ERR_THRESHOLD
    if not math.isfinite(v) or v < 0:
        return None, ERR_THRESHOLD
    return v, ""


def span_counts(spans: list[Span]) -> tuple[int, int]:
    """(with a span, without a span) - counting only, no arithmetic on values."""
    w = sum(1 for s in spans if s.has_span)
    return w, len(spans) - w


REPORT_TEXT = {
    "s4_reclassified": "Segments were not classified again",
    "dcir_redetected": "DCIR pulses were not detected again",
    "dqdv_recomputed": "dQ/dV was not recalculated (labels only)",
}


@dataclass
class CorrectionState:
    threshold: float = DEFAULT_THRESHOLD_A
    threshold_text: str = ""
    threshold_error: str = ""
    run: Run = Run.IDLE
    run_kind: str = ""                          # apply | remove
    outcome: Outcome = Outcome.NONE
    message: str = ""
    progress: Optional[float] = None
    phase: str = ""
    applied: Optional[CorrResult] = None        # the correction currently in force (None: not applied / removed)
    removed_at: str = ""
    selected: Optional[str] = None

    @property
    def is_running(self) -> bool:
        return self.run is not Run.IDLE

    def set_threshold_text(self, text: str) -> None:
        v, err = parse_threshold(text)
        self.threshold_text, self.threshold_error = text, err
        if not err:
            self.threshold = v

    def outdated(self, dcir_fingerprint: Optional[str]) -> bool:
        """The applied correction no longer matches: DCIR results re-calculated since, or another threshold typed."""
        a = self.applied
        return a is not None and a.enabled and (a.dcir.fingerprint != dcir_fingerprint or a.threshold != self.threshold)

    def state(self, has_dcir: bool, n_with_span: int, dcir_fingerprint: Optional[str]) -> CorrView:
        if self.run is Run.CANCELLING:
            return CorrView.CANCELLING
        if self.run is Run.PUBLISHING:
            return CorrView.PUBLISHING
        if self.run is Run.RUNNING:
            return CorrView.RUNNING
        if self.outcome is Outcome.FAILED:
            return CorrView.FAILED
        if self.outcome is Outcome.CANCELLED:
            return CorrView.CANCELLED
        if self.applied is not None and self.applied.enabled:
            return CorrView.OUTDATED if self.outdated(dcir_fingerprint) else CorrView.APPLIED
        if not has_dcir:
            return CorrView.UNAVAILABLE
        return CorrView.NOT_APPLIED if n_with_span else CorrView.NO_SPANS

    def apply_blocker(self, has_dcir: bool, n_with_span: int, busy: str = "") -> str:
        if self.is_running:
            return "The correction is running"
        if busy:
            return busy
        if not has_dcir:
            return "Apply & Calculate the DCIR measurements first"
        if not n_with_span:
            return "No accepted measurement gives a span to exclude"
        if self.threshold_error:
            return self.threshold_error
        return ""

    def remove_blocker(self, busy: str = "") -> str:
        if self.is_running:
            return "The correction is running"
        if busy:
            return busy
        if self.applied is None or not self.applied.enabled:
            return "No correction is applied"
        return ""

    # --- lifecycle ---
    def begin(self, kind: str) -> None:
        self.run, self.run_kind, self.outcome, self.message, self.progress, self.phase = Run.RUNNING, kind, Outcome.NONE, "", None, ""

    def update_progress(self, fraction: Optional[float], phase: str, publishing: bool = False) -> None:
        if self.run is not Run.IDLE:
            self.progress, self.phase = fraction, phase
            if publishing and self.run is Run.RUNNING:
                self.run = Run.PUBLISHING

    def request_cancel(self) -> bool:
        if self.run is Run.RUNNING:                 # once publishing, the commit is not interrupted
            self.run = Run.CANCELLING
            return True
        return False

    def finished(self, res: CorrResult) -> None:
        self.run, self.outcome, self.message = Run.IDLE, Outcome.NONE, ""
        if res.enabled:
            self.applied, self.removed_at = res, ""
            self.threshold, self.threshold_text, self.threshold_error = res.threshold, "", ""
        else:
            self.applied, self.removed_at = None, res.applied_at

    def cancelled(self) -> None:
        self.run, self.outcome = Run.IDLE, Outcome.CANCELLED

    def failed(self, message: str) -> None:
        self.run, self.outcome, self.message = Run.IDLE, Outcome.FAILED, message

    def elaboration_replaced(self) -> bool:
        """A new base elaboration (Process / re-import): the correction belonged to the old one (confirm first)."""
        had = self.applied is not None
        self.applied, self.removed_at, self.outcome, self.message, self.selected = None, "", Outcome.NONE, "", None
        return had


CORR_TITLE = {
    CorrView.UNAVAILABLE: "Cycle correction not available yet",
    CorrView.NO_SPANS: "No span to exclude",
    CorrView.NOT_APPLIED: "Cycle correction not applied",
    CorrView.RUNNING: "Applying cycle correction",
    CorrView.CANCELLING: "Cancelling…",
    CorrView.PUBLISHING: "Saving the corrected elaboration…",
    CorrView.APPLIED: "Cycle correction applied",
    CorrView.OUTDATED: "Correction based on previous DCIR results",
    CorrView.CANCELLED: "Correction cancelled",
    CorrView.FAILED: "Correction failed",
}
