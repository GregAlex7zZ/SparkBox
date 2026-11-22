"""GITT / ICI results chart data: D versus capacity or SoC, optional OCV overlay and point tooltips.
PURE PYTHON (no Qt), unit-tested in tests/test_gitt_ici_m7.py.

Every value comes from gregorithm records: D (m²/s), the capacity coordinates of the result (D placed at the canonical
capacity at the start of its CC segment), and the OCV products recorded in the technical details, each at the
capacity of the state it represents. This module only selects, groups, converts display units and writes text:
  * one D series per method/variant/OCV source and branch, charge and discharge never joined;
  * proposed results drawn hollow, accepted filled; rejected, stale and error records are not drawn;
  * on a logarithmic axis, D that is missing, zero, negative or not finite is omitted and counted, never shifted
    to a fictitious positive value;
  * OCV points are connected only inside one source and one branch, in capacity order; the line is a visual aid,
    not a model; a pseudo-OCV is labelled as such, never as equilibrium OCV;
  * tooltips show only statistics that exist for the method; uncertainty only when gregorithm estimated it, labelled
    as statistical (fit) uncertainty.

Specific capacity is the canonical capacity divided by the active material mass of the sample (mAh/g); it is
unavailable without that mass. SoC is p = 100 · Q / Q_end over the whole branch (half-cycle), p for charge and
100 − p for discharge; it is unavailable when Q_end is missing or zero. Q_end is the canonical capacity at the last
sample of the half-cycle, supplied by the backend.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Optional

from .gitt_ici import D_UNITS, OCV_LABELS, STATE_LABELS, format_d, series_key_of, series_label

X_AXES = (("capacity_mah", "Capacity [mAh]"), ("capacity_ah", "Capacity [Ah]"),
          ("specific_mah_g", "Specific capacity [mAh/g]"), ("soc", "SoC [%]"))
X_LABELS = dict(X_AXES)
X_PARTS = {"capacity_mah": ("Capacity", "mAh"), "capacity_ah": ("Capacity", "Ah"),
           "specific_mah_g": ("Specific capacity", "mAh/g"), "soc": ("SoC", "%")}
OCV_SERIES_LABELS = {
    "rest_sample": "Sampled OCV (last rest sample)",
    "relaxation_extrapolated": "Extrapolated OCV (relaxation fit at target)",
    "ici_intercept": "Pseudo-OCV (ICI intercept, not equilibrium)",
}
DRAWN_STATES = ("provisional", "accepted")


@dataclass(frozen=True)
class DPoint:
    """One drawable D result: x in the chosen axis, y in the display unit, and its review identity."""
    identity: object
    x: float
    y: float
    accepted: bool
    tooltip: str


@dataclass(frozen=True)
class DSeries:
    """Points of one method/variant/OCV source in one branch."""
    key: str
    label: str
    branch: str
    points: tuple[DPoint, ...]


@dataclass(frozen=True)
class OcvSeries:
    """OCV points of one source in one half-cycle, sorted by x; drawn as connected points."""
    key: str
    label: str
    branch: str
    points: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class ChartData:
    """Everything the results chart draws, plus the human-readable reasons for omitted results."""
    d_series: tuple[DSeries, ...] = ()
    ocv_series: tuple[OcvSeries, ...] = ()
    omitted: tuple[str, ...] = ()
    x_label: str = ""
    y_label: str = ""


def branch_name(polarity: int) -> str:
    """'charge' for positive polarity, 'discharge' otherwise."""
    return "charge" if polarity > 0 else "discharge"


def x_value(capacity_ah: Optional[float], polarity: int, half_cycle: int, axis: str,
            branch_end_ah: dict, mass_g: Optional[float] = None) -> Optional[float]:
    """Position of a capacity on the chosen axis, or None when it cannot be placed (SoC without Q_end, specific
    capacity without the active mass)."""
    if capacity_ah is None or not math.isfinite(capacity_ah):
        return None
    if axis == "capacity_mah":
        return capacity_ah * 1000.0
    if axis == "specific_mah_g":
        return None if not mass_g or mass_g <= 0 else capacity_ah * 1000.0 / mass_g
    if axis == "capacity_ah":
        return capacity_ah
    q_end = branch_end_ah.get(half_cycle)
    if not q_end or not math.isfinite(q_end):
        return None
    p = 100.0 * capacity_ah / q_end
    return p if polarity > 0 else 100.0 - p


def _num(value, digits: int = 4) -> str:
    return "N/D" if value is None else f"{value:.{digits}g}"


def tooltip(record, number: Optional[int], unit: str, x_text: str) -> str:
    """Contextual summary of one D point: value, position, impulse, series, state, indicators and the method's
    own statistics. Fields that do not apply to a method are absent, never filled with invented values."""
    unit_text = D_UNITS[unit][0]
    c = record.coordinates
    lines = [f"D = {format_d(record.diffusion_m2_s, unit)} {unit_text}",
             f"{x_text}" + ("" if number is None else f" · impulse #{number}"),
             f"Capacity at pulse start: {_num(None if c is None else c.capacity_start_ah * 1000)} mAh "
             f"({branch_name(c.polarity) if c is not None else 'N/D'}, half-cycle {c.half_cycle if c else 'N/D'})",
             series_label(record.identity),
             f"State: {STATE_LABELS.get(record.state.value, record.state.value)}"]
    q = record.quality
    if q is not None:
        lines.append(f"Indicators: statistical {q.statistical.value}, physical {q.physical.value}, "
                     f"overall {q.overall.value}" + (" · to check" if q.needs_review else ""))
    details = record.details if isinstance(record.details, dict) else {}
    window = details.get("window") if isinstance(details.get("window"), dict) else None
    fit = window.get("fit") if window and isinstance(window.get("fit"), dict) else None
    if fit is not None:
        lo, hi = fit.get("effective_s") or (None, None)
        lines.append(f"√t window {_num(lo)}–{_num(hi)} s · {fit.get('sample_count')} samples")
        lines.append(f"Slope {_num(fit.get('slope_v_per_sqrt_s'))} V/√s (SE {_num(fit.get('slope_standard_error'))}) "
                     f"· R² {_num(fit.get('r_squared'))} · RMSE {_num(fit.get('rmse_v'))} V")
        if window.get("q_linearity_pct") is not None or window.get("q_stability_pct") is not None:
            lines.append(f"q_L {_num(window.get('q_linearity_pct'))} % · q_S {_num(window.get('q_stability_pct'))} %")
    result = record.result
    if series_key_of(record.identity) == "gitt_delta" and result is not None:
        lines.append(f"ΔE_τ {_num(getattr(result, 'delta_e_tau_v', None))} V · τ {_num(getattr(result, 'tau_s', None))} s")
    if hasattr(result, "identifiable"):                                              # Delacourt diagnostics
        lines.append(f"Fit RMSE {_num(result.rmse_v)} V · R² {_num(result.r_squared)} · "
                     f"identifiable {'yes' if result.identifiable else 'no'}"
                     + (" · at search boundary" if result.at_search_boundary else ""))
        lines.append(f"Sensitivity {_num(result.sensitivity_norm_v_per_log10_theta)} V/decade · "
                     f"{result.optimizer_evaluations} evaluations")
    u = getattr(result, "standard_uncertainty_m2_s", None)
    lines.append(f"Statistical (fit) uncertainty u(D) = {format_d(u, unit)} {unit_text}" if u is not None
                 else "Statistical (fit) uncertainty: not estimable")
    return "\n".join(lines)


def _ocv_points(record) -> list[tuple[str, int, float, float, int, int, str]]:
    """(source, key, capacity_ah, voltage_v, half_cycle, polarity, dedup id) of the OCV products of one record."""
    out = []
    c = record.coordinates
    details = record.details if isinstance(record.details, dict) else {}
    if c is None:
        return out
    for label in ("before", "after"):
        ocv = details.get(f"ocv_{label}")
        if isinstance(ocv, dict) and ocv.get("voltage_v") is not None and ocv.get("capacity_ah") is not None:
            out.append((ocv["source"], ocv["capacity_ah"], ocv["voltage_v"], c.half_cycle, c.polarity,
                        f"rest:{ocv.get('rest_segment_id')}"))
    intermediates = details.get("intermediates")
    if isinstance(intermediates, dict) and intermediates.get("pseudo_ocv_v") is not None:
        # The ICI pseudo-OCV belongs to the interruption after the pulse, i.e. the state at the pulse end.
        out.append(("ici_intercept", c.capacity_end_ah, intermediates["pseudo_ocv_v"], c.half_cycle, c.polarity,
                    f"event:{record.identity.event_id}"))
    return out


def chart_data(records, events, *, unit: str, axis: str, log: bool, branch_end_ah: Optional[dict] = None,
               mass_g: Optional[float] = None) -> ChartData:
    """Build the results chart from the current records (see the module docstring for every rule)."""
    branch_end_ah = branch_end_ah or {}
    numbers = {e.event_id: i for i, e in enumerate(events, start=1)}
    series: dict[str, list[DPoint]] = {}
    meta: dict[str, tuple[str, str]] = {}
    omitted = {"state": 0, "stale": 0, "no_d": 0, "log": 0, "axis": 0}
    for record in records:
        state = record.state.value
        if state not in DRAWN_STATES:
            omitted["state"] += 1
            continue
        if record.stale:
            omitted["stale"] += 1
            continue
        value = record.diffusion_m2_s
        if value is None or not math.isfinite(value):
            omitted["no_d"] += 1
            continue
        if log and value <= 0:
            omitted["log"] += 1                                                       # never a fake positive
            continue
        c = record.coordinates
        x = None if c is None else x_value(c.capacity_start_ah, c.polarity, c.half_cycle, axis, branch_end_ah,
                                           mass_g)
        if x is None:
            omitted["axis"] += 1
            continue
        branch = branch_name(c.polarity)
        key = f"{series_label(record.identity)} · {branch}"
        meta[key] = (series_label(record.identity), branch)
        name, x_unit = X_PARTS[axis]
        x_text = f"{name} = {x:.4g} {x_unit}"
        series.setdefault(key, []).append(DPoint(
            record.identity, x, value * D_UNITS[unit][1], state == "accepted",
            tooltip(record, numbers.get(record.identity.event_id), unit, x_text)))
    d_series = tuple(DSeries(k, meta[k][0], meta[k][1], tuple(sorted(v, key=lambda p: p.x)))
                     for k, v in sorted(series.items()))
    ocv: dict[tuple, dict[str, tuple[float, float]]] = {}
    for record in records:
        if record.state.value == "error":
            continue
        for source, cap, volt, half_cycle, polarity, dedup in _ocv_points(record):
            x = x_value(cap, polarity, half_cycle, axis, branch_end_ah, mass_g)
            if x is not None and volt is not None and math.isfinite(volt):
                ocv.setdefault((source, half_cycle, polarity), {})[dedup] = (x, volt)
    ocv_series = tuple(
        OcvSeries(f"{OCV_SERIES_LABELS.get(src, OCV_LABELS.get(src, src))} · {branch_name(pol)} · half-cycle {hc}",
                  OCV_SERIES_LABELS.get(src, src), branch_name(pol), tuple(sorted(points.values())))
        for (src, hc, pol), points in sorted(ocv.items()))
    messages = []
    if omitted["log"]:
        messages.append(f"{omitted['log']} result(s) with D ≤ 0 are not drawn on the logarithmic axis; "
                        "they stay in the review list.")
    if omitted["no_d"]:
        messages.append(f"{omitted['no_d']} result(s) without D are not drawn; see their reasons in the list.")
    if omitted["axis"]:
        messages.append(f"{omitted['axis']} result(s) cannot be placed on this axis (SoC needs a non-zero branch "
                        "capacity, specific capacity the active material mass).")
    if omitted["state"] or omitted["stale"]:
        messages.append(f"Not drawn: {omitted['state']} rejected or error, {omitted['stale']} stale result(s).")
    unit_text = D_UNITS[unit][0]
    return ChartData(d_series, ocv_series, tuple(messages), X_LABELS[axis],
                     f"D [{unit_text}] · {'logarithmic' if log else 'linear'} scale")


def ocv_curves(rests, pseudo: dict, events, *, axis: str, branch_end_ah: Optional[dict] = None,
               mass_g: Optional[float] = None) -> tuple[OcvSeries, ...]:
    """OCV curves from the rest-level products (gregorithm RestOcv) and the ICI pseudo-OCV, one series per source and
    half-cycle, each rest once at the capacity of the state it represents. Sampled and extrapolated
    points are separate series; nothing is joined across sources or branches."""
    branch_end_ah = branch_end_ah or {}
    half_of = {}
    for e in events:
        for rest in (e.rest_before_segment_id, e.rest_after_segment_id):
            if rest is not None:
                half_of.setdefault(rest, (e.half_cycle, e.polarity))
    groups: dict[tuple, list[tuple[float, float]]] = {}
    for rest in rests:
        half, polarity = half_of.get(rest.rest_segment_id, (None, rest.polarity))
        for source, point in (("rest_sample", rest.sampled), ("relaxation_extrapolated", rest.extrapolated)):
            if point is None:
                continue
            x = x_value(point.capacity_ah, polarity, half, axis, branch_end_ah, mass_g)
            if x is not None:
                groups.setdefault((source, half, polarity), []).append((x, point.voltage_v))
    for rest_id, (capacity, volts) in pseudo.items():
        half, polarity = half_of.get(rest_id, (None, 1))
        x = x_value(capacity, polarity, half, axis, branch_end_ah, mass_g)
        if x is not None:
            groups.setdefault(("ici_intercept", half, polarity), []).append((x, volts))
    return tuple(
        OcvSeries(f"{OCV_SERIES_LABELS[src]} · {branch_name(pol)} · half-cycle {hc}", OCV_SERIES_LABELS[src],
                  branch_name(pol), tuple(sorted(points)))
        for (src, hc, pol), points in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0, kv[0][2])))
