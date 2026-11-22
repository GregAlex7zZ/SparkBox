"""GITT / ICI intermediate acceptances: rest-period OCV, pulse fits and the D results they unlock.
PURE PYTHON (no Qt), unit-tested in tests/test_gitt_ici_redesign.py.

The operator accepts intermediate results before they feed the final coefficients:
  * a REST PERIOD (OCV Analysis): its sampled OCV and the extrapolated OCV when extrapolation is active, as measured
    by gregorithm. The ICI pseudo-OCV shown next to them is the intercept of the ICI √t fit: it is reviewed with that
    fit in Pulse Fitting and is NOT part of the rest acceptance, otherwise every rest accepted before the fits are
    calculated (OCV Analysis comes first) would turn "Modified" and silently drop every D from the results;
  * a PULSE (Pulse Fitting): every √t or model fit of that impulse used by the checked methods (GITT VA pulse window,
    ICI interruption window, Delacourt fit, and GITT Δ only when its IR correction is the GITT fit intercept);
  * the IR DROPS of an impulse (IR Drop): its pre-pulse and post-pulse samples, ΔV and R_on / R_off. Only accepted
    IR drops are drawn on the R axis of Diffusion Results and exported; they never gate a D result.

The operator can also REJECT a rest or an impulse by hand (never automatically): a rejected rest feeds no D, a
rejected impulse gives no D of any method. Rejection is stored as ``REJECT_MARK`` in place of the fingerprint and
is undone with Return to Pending or by accepting the item again.

Each acceptance stores a fingerprint of exactly what was examined. When a later change alters it (a new sample, an
extrapolation setting, a recalculated fit), the stored fingerprint no longer matches and the item is shown as
"Modified": it counts as not accepted until accepted again. Accepting never changes a value.

A D result is USED only when it is current, has a finite D, its pulse is accepted (if its method needs a fit) and
every rest its OCV comes from is accepted. ``sync_acceptance`` keeps gregorithm's own acceptance (the one export and
the Diffusion Results chart rely on) equal to that rule, and never touches results the operator rejected.

Records, events and RestOcv objects are read by attribute (duck typing), so this module does not import gregorithm.
"""
from __future__ import annotations
import hashlib
import json
import math
from dataclasses import dataclass

from .gitt_ici import OCV_LABELS, SERIES, selected_series, series_key_of

PENDING, ACCEPTED, MODIFIED, INVALID, NOT_NEEDED = "Pending", "Accepted", "Modified", "Invalid", "No fit needed"
REJECTED = "Rejected"
REJECT_MARK = "rejected"         # stored in rest_reviews / pulse_reviews instead of an accepted fingerprint
FIT_SERIES = ("gitt_va", "ici", "delacourt_fixed", "delacourt_free")


def _hash(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def _num(value):
    return None if value is None or (isinstance(value, float) and not math.isfinite(value)) else round(float(value), 15)


def needs_fit(key: str, params) -> bool:
    """Whether a series depends on a fit reviewed in Pulse Fitting (GITT Δ only with the intercept IR correction)."""
    return key in FIT_SERIES or (key == "gitt_delta" and params.ir_correction == "gitt_intercept")


def fit_signature(record) -> str:
    """Fingerprint of the fit a record DRAWS: the recorded √t fit (window, intercept, slope) or Delacourt's fitted
    curve. Two records with the same signature show exactly the same fit, whatever their OCV source."""
    details = record.details if isinstance(record.details, dict) else {}
    window = details.get("window") if isinstance(details.get("window"), dict) else {}
    result = getattr(record, "result", None)
    curve = getattr(result, "fitted_voltage_v", None) or ()
    return _hash([window.get("fit"), window.get("selected_s"), [_num(v) for v in curve]])


def distinct_fits(records, params) -> list[tuple[str, object]]:
    """The fits worth plotting for one impulse, as (label, representative record).

    Only GITT VA (pulse √t), ICI (interruption √t) and Delacourt (spherical model) have a fit to inspect. GITT Δ has
    none: with the GITT-intercept IR correction it reuses the pulse √t window of GITT VA, so it is listed only when
    GITT VA is not calculated. The OCV source does not change the √t fits (it enters only ΔU, hence D), so records
    whose drawn fit is identical are merged and named without the OCV; it is kept in the label only when the fits
    really differ (Delacourt fixed offset, whose offset uses the OCV of the rest before; a physically guided window,
    which depends on D)."""
    fitted = [r for r in records if needs_fit(series_key_of(r.identity), params)]
    if any(series_key_of(r.identity) == "gitt_va" for r in fitted):
        fitted = [r for r in fitted if series_key_of(r.identity) != "gitt_delta"]
    groups: dict[tuple[str, str], list] = {}
    for r in fitted:
        groups.setdefault((series_key_of(r.identity), fit_signature(r)), []).append(r)
    per_key: dict[str, int] = {}
    for key, _sig in groups:
        per_key[key] = per_key.get(key, 0) + 1
    out = []
    order = list(SERIES)
    for (key, _sig), members in sorted(groups.items(), key=lambda kv: order.index(kv[0][0]) if kv[0][0] in order
                                       else len(order)):
        label = SERIES[key][0] if key in SERIES else key
        if per_key[key] > 1:
            label += " · " + " / ".join(OCV_LABELS.get(m.identity.ocv_source.value, m.identity.ocv_source.value)
                                        for m in members)
        out.append((label, members[0]))
    return out


def record_rests(record, event) -> tuple[int, ...]:
    """Rest periods whose OCV a record uses: the two rests of its pulse, or for the ICI pseudo-OCV the interruption
    after the pulse and the previous one recorded by gregorithm."""
    if record.identity.ocv_source.value == "ici_intercept":
        details = record.details if isinstance(record.details, dict) else {}
        previous = (details.get("previous_interruption") or {}).get("rest_segment_id")
        rests = (event.rest_after_segment_id, previous)
    else:
        rests = (event.rest_before_segment_id, event.rest_after_segment_id)
    return tuple(r for r in rests if r is not None)


# --- rest periods ------------------------------------------------------------------------------------------------


def pseudo_ocv(records, events) -> dict[int, tuple[float, float]]:
    """{rest_segment_id: (capacity_ah, pseudo-OCV V)} from the ICI records: the intercept of the √t fit of the
    interruption after each pulse, placed at the end of that pulse. Only gregorithm values are used."""
    by_id = {e.event_id: e for e in events}
    out: dict[int, tuple[float, float]] = {}
    for record in records:
        if series_key_of(record.identity) != "ici" or record.state.value == "error" or record.stale:
            continue
        event = by_id.get(record.identity.event_id)
        details = record.details if isinstance(record.details, dict) else {}
        value = (details.get("intermediates") or {}).get("pseudo_ocv_v")
        if event is None or event.rest_after_segment_id is None or value is None or record.coordinates is None:
            continue
        if event.rest_after_segment_id not in out or record.identity.ocv_source.value == "ici_intercept":
            out[event.rest_after_segment_id] = (record.coordinates.capacity_end_ah, float(value))
    return out


def rest_valid(rest, params) -> bool:
    """A rest can be accepted when its sampled OCV exists. A missing extrapolated OCV (e.g. no relaxation origin) is
    shown with its reason and only the series using it stay without D; it never blocks the sampled OCV."""
    return rest.sampled is not None


def rest_fingerprint(rest, params) -> str:
    """Fingerprint of the rest products the operator accepts: sampled point and extrapolation (when active). The ICI
    pseudo-OCV is excluded on purpose (see the module docstring)."""
    payload = {"sampled": None if rest.sampled is None else [rest.sampled.source_row, _num(rest.sampled.voltage_v)]}
    if params.extrapolation_active:
        fit = rest.fit
        payload["extrapolated"] = None if fit is None else [
            _num(fit.alpha_v), _num(fit.beta_v), _num(fit.fit_start_s), _num(fit.fit_end_s), _num(fit.target_time_s)]
    return _hash(payload)


def rest_state(rest, params, reviews: dict) -> str:
    """Rejected (by hand), Pending, Accepted, Modified (accepted, then changed) or Invalid."""
    accepted = reviews.get(rest.rest_segment_id)
    if accepted == REJECT_MARK:
        return REJECTED
    if not rest_valid(rest, params):
        return INVALID
    if accepted is None:
        return PENDING
    return ACCEPTED if accepted == rest_fingerprint(rest, params) else MODIFIED


# --- IR drops --------------------------------------------------------------------------------------------------------


def ir_valid(drops) -> bool:
    """An impulse's IR drops can be accepted when at least one side (R_on or R_off) has a resistance."""
    return drops is not None and any(getattr(drops, side).resistance_ohm is not None
                                     for side in ("pre_pulse", "post_pulse"))


def ir_fingerprint(drops) -> str:
    """Fingerprint of the IR drops the operator accepts: per side the reference and measured rows, ΔV and R. A new
    sample time or a clicked sample changes it, so an earlier acceptance becomes Modified."""
    payload = []
    for side in ("pre_pulse", "post_pulse"):
        drop = getattr(drops, side)
        payload.append([None if drop.reference is None else drop.reference.source_row,
                        None if drop.measured is None else drop.measured.source_row,
                        _num(drop.delta_voltage_v), _num(drop.resistance_ohm)])
    return _hash(payload)


def ir_state(drops, reviews: dict, event_id: str) -> str:
    """Rejected (by hand), Pending, Accepted, Modified (accepted, then changed) or Invalid (no R on either side,
    or not measured)."""
    accepted = reviews.get(event_id)
    if accepted == REJECT_MARK:
        return REJECTED
    if not ir_valid(drops):
        return INVALID
    if accepted is None:
        return PENDING
    return ACCEPTED if accepted == ir_fingerprint(drops) else MODIFIED


def accepted_ir(events, drops: dict, reviews: dict) -> set:
    """Event ids whose IR drops are accepted and unchanged since: only their R_on / R_off reach Diffusion Results
    and the export. The IR drops never gate a D result (the pre-pulse sample of an IR correction is reviewed with
    the fits)."""
    return {e.event_id for e in events if ir_state(drops.get(e.event_id), reviews, e.event_id) == ACCEPTED}


# --- pulses ----------------------------------------------------------------------------------------------------------


def fit_records(records, event_id: str, params) -> list:
    """Current records of one impulse whose series depends on a fit, in a stable order."""
    out = [r for r in records if r.identity.event_id == event_id and needs_fit(series_key_of(r.identity), params)]
    return sorted(out, key=lambda r: (series_key_of(r.identity), r.identity.ocv_source.value))


def pulse_fingerprint(records, event_id: str, params) -> str:
    """Fingerprint of the fits of one impulse: per series its dependencies (windows, settings), D and error."""
    return _hash([[series_key_of(r.identity), r.identity.ocv_source.value, list(map(list, r.dependencies)),
                   _num(r.diffusion_m2_s), r.error, bool(r.stale)] for r in fit_records(records, event_id, params)])


def pulse_state(event, records, params, reviews: dict) -> str:
    """No fit needed, Pending, Accepted, Modified or Invalid (incomplete, not calculated, a stale fit, or every fit
    failed). A failed fit of one series does not block the others: it is shown and never used."""
    fits = fit_records(records, event.event_id, params)
    if reviews.get(event.event_id) == REJECT_MARK:
        return REJECTED
    if not any(needs_fit(key, params) for key, _src in selected_series(params)):
        return NOT_NEEDED
    if (not event.complete or not fits or any(r.stale for r in fits)
            or all(r.state.value == "error" for r in fits)):
        return INVALID
    accepted = reviews.get(event.event_id)
    if accepted is None:
        return PENDING
    return ACCEPTED if accepted == pulse_fingerprint(records, event.event_id, params) else MODIFIED


# --- D results -------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Usage:
    """Whether one D result is used in the final results, and why not."""
    used: bool
    reason: str


def usage(record, event, params, rest_ok: set, pulse_ok: set, rejected: frozenset = frozenset()) -> Usage:
    """Apply the rule of the module docstring; the reason names the first unmet condition. Results of a method or
    OCV source no longer selected in Setup are kept in the snapshot but never used; ``rejected`` are the impulses
    rejected by hand, none of whose D is used."""
    if (series_key_of(record.identity), record.identity.ocv_source.value) not in selected_series(params):
        return Usage(False, "Method or OCV source not selected in Setup")
    if record.state.value == "error":
        return Usage(False, f"Calculation failed: {record.error}")
    if record.stale:
        return Usage(False, "Out of date after a change: recalculate")
    d = record.diffusion_m2_s
    if d is None or not math.isfinite(d):
        reason = getattr(record.result, "unavailable_reason", None) or "D unavailable"
        return Usage(False, f"No D: {reason}")
    if record.state.value == "rejected":
        return Usage(False, "Rejected")
    if record.identity.event_id in rejected:
        return Usage(False, "Impulse rejected")
    if needs_fit(series_key_of(record.identity), params) and record.identity.event_id not in pulse_ok:
        return Usage(False, "Pulse fit not accepted")
    missing = [r for r in record_rests(record, event) if r not in rest_ok]
    if missing:
        return Usage(False, f"Rest period(s) {', '.join(map(str, missing))} not accepted")
    return Usage(True, "")


def accepted_sets(model, params) -> tuple[set, set]:
    """(accepted rest ids, accepted event ids) whose stored fingerprint still matches the current values."""
    rest_ok = {r.rest_segment_id for r in model.rest_ocv if rest_state(r, params, model.rest_reviews) == ACCEPTED}
    pulse_ok = {e.event_id for e in model.events
                if pulse_state(e, model.records, params, model.pulse_reviews) == ACCEPTED}
    return rest_ok, pulse_ok


def rejected_pulses(model) -> frozenset:
    """Event ids of the impulses rejected by hand."""
    return frozenset(k for k, v in model.pulse_reviews.items() if v == REJECT_MARK)


def sync_acceptance(snapshot, events, params, rest_ok: set, pulse_ok: set, rejected: frozenset = frozenset()):
    """Return ``snapshot`` with gregorithm acceptance equal to the usage rule: usable proposed results are accepted,
    accepted results that are no longer usable return to proposed; rejected and error results are left alone."""
    if snapshot is None:
        return None
    by_id = {e.event_id: e for e in events}
    for record in snapshot.records:
        event = by_id.get(record.identity.event_id)
        if event is None or record.state.value in ("rejected", "error"):
            continue
        used = usage(record, event, params, rest_ok, pulse_ok, rejected).used
        if used and record.state.value == "provisional":
            snapshot = snapshot.accept(record.identity)
        elif not used and record.state.value == "accepted":
            snapshot = snapshot.revoke(record.identity)
    return snapshot


# Plain-language explanation of the gregorithm reason codes most often met in the results chart.
REASON_TEXT = {
    "ici_previous_interruption_missing": "ICI pseudo-OCV needs the previous interruption (first impulse of the branch)",
    "rest_before_missing": "no rest or interruption before the pulse in the selected element",
    "before_rest_missing": "no rest before the pulse in the selected element",
    "after_rest_missing": "no rest after the pulse in the selected element",
    "rest_after_missing": "no rest or interruption after the pulse in the selected element",
    "before_relaxation_reference_missing": "the first rest has no relaxation origin for the extrapolation",
    "physical_input_missing": "Active Volume or Effective Area missing",
    "particle_radius_required": "Particle Radius missing",
    "constant_current_required": "the active segment is not constant current",
    "incomplete_event": "the impulse is incomplete in the selected element",
}


def exclusion_text(reason: str) -> str:
    """Group key shown under the results chart: the condition plus the technical cause in plain words (the raw code
    when no plain text is known), so an excluded D always says exactly why."""
    head, sep, tail = reason.partition(": ")
    if not sep:
        return reason
    code = tail.split(":")[0].strip()
    detail = REASON_TEXT.get(code)
    if detail is None:
        detail = tail if len(tail) <= 90 else tail[:87] + "…"
    return f"{head}: {detail}"
