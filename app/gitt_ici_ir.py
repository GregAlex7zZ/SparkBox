"""GITT / ICI IR drop analysis: pulse list rows, value cells, zoom geometry and resistance series.
PURE PYTHON (no Qt), unit-tested in tests/test_gitt_ici_ir.py.

Every value comes from gregorithm.measure_ir_drops through the backend (one ImpulseIrDrops per impulse): the real
reference and measured samples (source row, time, voltage, current, capacity), the signed ΔV and ΔI and R = |ΔV/ΔI|.
This module only formats them and decides what to draw; it never selects a sample, interpolates or fills a missing
value. The two sides are kept apart everywhere:
  * pre-pulse (R_on): reference = last sample of the rest before the pulse, measured = pulse sample at t ≤ T_on;
  * post-pulse (R_off): reference = last sample under current, measured = following-rest sample at t ≤ T_off.

The pre-pulse sample is the one used by the "selected sample" IR corrections (GITT Δ when chosen; the Delacourt
fixed offset by default, decision A-02), so ``dependent_series`` lists exactly the series a manual pre-pulse sample
changes. A post-pulse sample feeds no D result.

Objects from gregorithm are read by attribute (duck typing), so this module does not import gregorithm.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Optional

from .gitt_ici import selected_series
from .gitt_ici_plot import X_LABELS, X_PARTS, ChartData, DPoint, DSeries, branch_name, x_value

SIDES = (("pre_pulse", "Pre-pulse"), ("post_pulse", "Post-pulse"))
SIDE_LABELS = dict(SIDES)
SIDE_SYMBOLS = {"pre_pulse": "R_on", "post_pulse": "R_off"}
# What the reference and the measured sample are on each side (shown under the charts).
SIDE_RULES = {
    "pre_pulse": ("last sample of the rest before the pulse", "pulse"),
    "post_pulse": ("last sample under current", "following rest"),
}
REASONS = {
    "rest_before_missing": "No rest before the pulse in the selected scope.",
    "pulse_samples_missing": "The pulse has no samples.",
    "rest_after_missing": "No rest after the pulse in the selected scope.",
    "no_sample_within_target": "No real sample between the reference and the sample time.",
    "manual_sample_invalid": "The chosen sample does not belong to this side of the impulse.",
    "zero_current_step": "The current does not change between the two samples (ΔI = 0): R is not defined.",
}


def side_of(drops, side: str):
    """The IrDrop of ``side`` ('pre_pulse' or 'post_pulse') of an ImpulseIrDrops."""
    return getattr(drops, side)


def reason_text(code: Optional[str]) -> str:
    """Human-readable reason of an unavailable IR drop ('' when available)."""
    return "" if not code else REASONS.get(code, code.replace("_", " "))


def dependent_series(params) -> tuple[tuple[str, str], ...]:
    """Checked series whose result uses the pre-pulse sample: GITT Δ with the selected-sample IR correction and the
    Delacourt fixed offset (selected sample by default). Delacourt with a free offset never uses it."""
    keys = {"delacourt_fixed"} | ({"gitt_delta"} if params.ir_correction == "selected_sample" else set())
    return tuple(s for s in selected_series(params) if s[0] in keys)


def format_mv(volts: Optional[float], unit: bool = True) -> str:
    """Signed voltage difference in mV (with or without the unit), or 'N/D'."""
    if volts is None or not math.isfinite(volts):
        return "N/D"
    return f"{volts * 1e3:+.4g}" + (" mV" if unit else "")


def format_current(amps: Optional[float]) -> str:
    """Current in mA (signed), or 'N/D'."""
    return "N/D" if amps is None or not math.isfinite(amps) else f"{amps * 1e3:+.4g} mA"


def format_resistance(ohm: Optional[float]) -> str:
    """Resistance in the most readable of mΩ, Ω and kΩ, or 'N/D'."""
    if ohm is None or not math.isfinite(ohm):
        return "N/D"
    if ohm != 0 and abs(ohm) < 1:
        return f"{ohm * 1e3:.4g} mΩ"
    if abs(ohm) >= 1e3:
        return f"{ohm / 1e3:.4g} kΩ"
    return f"{ohm:.4g} Ω"


@dataclass(frozen=True)
class IrRow:
    """One impulse of the pulse list: both IR drops (ΔV in mV, unit in the column header) in distinct columns and a
    validity indicator."""
    event_id: str
    number: int
    cycle: int
    half_cycle: int
    branch: str
    pre_mv: str
    post_mv: str
    valid: str
    tooltip: str
    manual: bool


def validity(drops) -> str:
    """'Both valid', 'Pre-pulse only', 'Post-pulse only' or 'Not available'."""
    pre, post = drops.pre_pulse.resistance_ohm is not None, drops.post_pulse.resistance_ohm is not None
    return {(True, True): "Both valid", (True, False): "Pre-pulse only",
            (False, True): "Post-pulse only"}.get((pre, post), "Not available")


def ir_rows(events, drops: dict) -> list[IrRow]:
    """Pulse list in recognition order; impulses without measurement say so (nothing is filtered)."""
    rows = []
    for number, event in enumerate(events, start=1):
        item = drops.get(event.event_id)
        branch = "Charge" if event.polarity > 0 else "Discharge"
        if item is None:
            rows.append(IrRow(event.event_id, number, event.cycle, event.half_cycle, branch, "N/D", "N/D",
                              "Not measured", "", False))
            continue
        tips = [f"{SIDE_LABELS[side]}: {reason_text(side_of(item, side).reason)}" for side, _l in SIDES
                if side_of(item, side).reason]
        rows.append(IrRow(event.event_id, number, event.cycle, event.half_cycle, branch,
                          format_mv(item.pre_pulse.delta_voltage_v, False),
                          format_mv(item.post_pulse.delta_voltage_v, False),
                          validity(item), "\n".join(tips),
                          any(d.manual or d.local_time for d in (item.pre_pulse, item.post_pulse))))
    return rows


def drop_cells(drop) -> list[tuple[str, str]]:
    """(title, value) cells shown under one chart: samples (row, time, voltage), ΔV, current, R and the rule."""
    ref, meas = drop.reference, drop.measured
    rule_ref, rule_meas = SIDE_RULES[drop.side.value]
    cells = [
        ("Reference sample", "N/D" if ref is None else f"row {ref.source_row} · t = 0 s · {ref.voltage_v:.6g} V"),
        ("Measured sample", "N/D" if meas is None else
         f"row {meas.source_row} · t = {meas.relative_time_s:.4g} s · {meas.voltage_v:.6g} V"),
        ("IR drop ΔV", format_mv(drop.delta_voltage_v)),
        ("Currents I_ref → I_meas", "N/D" if ref is None or meas is None else
         f"{format_current(ref.current_a)} → {format_current(meas.current_a)}"),
        ("ΔI", format_current(drop.delta_current_a)),
        (f"{SIDE_SYMBOLS[drop.side.value]} = |ΔV/ΔI|", format_resistance(drop.resistance_ohm)),
        ("Selection", ("manual: exact row chosen in the chart" if drop.manual else
                       f"{'local time of this impulse' if drop.local_time else 'global time'}: last {rule_meas} "
                       f"sample with t ≤ {drop.target_time_s:g} s")
         + f" · reference = {rule_ref}"),
    ]
    if drop.reason:
        cells.append(("Not available", reason_text(drop.reason)))
    return cells


def zoom_range(times: tuple[float, ...], drop) -> Optional[tuple[float, float]]:
    """x range [s from the reference] that frames the transition: the reference, the measured sample and the
    target, plus a few sample intervals on both sides. None without a reference."""
    if drop.reference is None:
        return None
    origin = drop.reference.time_s
    rel = sorted(t - origin for t in times)
    steps = [b - a for a, b in zip(rel, rel[1:]) if b > a]
    step = sorted(steps)[len(steps) // 2] if steps else 1.0
    reach = max(drop.target_time_s, drop.measured.relative_time_s if drop.measured is not None else 0.0, 3 * step)
    return -max(reach, 3 * step), 1.5 * reach + step


def resistance_chart_data(events, drops: dict, *, axis: str, log: bool,
                          branch_end_ah: Optional[dict] = None, mass_g: Optional[float] = None) -> ChartData:
    """R_on and R_off series per branch for the results chart. R_on is placed at the capacity of its reference (start
    of the pulse) and R_off at the capacity of its reference (end of the pulse). Missing R and, on a log axis,
    R ≤ 0 are omitted and counted. Points carry the event id, so a click opens that impulse's IR drop analysis."""
    branch_end_ah = branch_end_ah or {}
    numbers = {e.event_id: i for i, e in enumerate(events, start=1)}
    series: dict[str, list[DPoint]] = {}
    meta: dict[str, tuple[str, str]] = {}
    missing = log_omitted = axis_omitted = 0
    name, x_unit = X_PARTS[axis]
    for event in events:
        item = drops.get(event.event_id)
        if item is None:
            continue
        for side, label in SIDES:
            drop = side_of(item, side)
            if drop.resistance_ohm is None:
                missing += 1
                continue
            if log and drop.resistance_ohm <= 0:
                log_omitted += 1
                continue
            x = x_value(drop.reference.capacity_ah, event.polarity, event.half_cycle, axis, branch_end_ah, mass_g)
            if x is None:
                axis_omitted += 1
                continue
            branch = branch_name(event.polarity)
            key = f"{SIDE_SYMBOLS[side]} ({label.lower()}) · {branch}"
            meta[key] = (SIDE_SYMBOLS[side], branch)
            tip = (f"{SIDE_SYMBOLS[side]} = {format_resistance(drop.resistance_ohm)}\n{name} = {x:.4g} {x_unit}\n"
                   f"Impulse #{numbers.get(event.event_id)} · {branch}\nΔV = {format_mv(drop.delta_voltage_v)} · "
                   f"ΔI = {format_current(drop.delta_current_a)}\nRows {drop.reference.source_row} → "
                   f"{drop.measured.source_row}" + (" · manual sample" if drop.manual else ""))
            series.setdefault(key, []).append(DPoint(event.event_id, x, drop.resistance_ohm, True, tip))
    out = tuple(DSeries(k, meta[k][0], meta[k][1], tuple(sorted(v, key=lambda p: p.x)))
                for k, v in sorted(series.items()))
    messages = []
    if missing:
        messages.append(f"{missing} IR drop(s) without R are not drawn; see their reasons in the IR drop list.")
    if log_omitted:
        messages.append(f"{log_omitted} R = 0 value(s) are not drawn on the logarithmic axis.")
    if axis_omitted:
        messages.append(f"{axis_omitted} value(s) cannot be placed on this axis (SoC unavailable).")
    return ChartData(out, (), tuple(messages), X_LABELS[axis], f"R [Ω] · {'logarithmic' if log else 'linear'} scale")
