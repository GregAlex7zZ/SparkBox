"""GITT / ICI export table: one row per complete impulse, split by branch (charge / discharge).
PURE PYTHON (no Qt), tested in tests/test_gitt_ici_m7.py and tests/test_gitt_ici_redesign.py.

The export carries only what the operator analyses, with the same values the Diffusion Results chart draws:
  * position: capacity [mAh], specific capacity [mAh/g] and SoC [%] at the start of the pulse (where D is placed);
  * IR drop: pre-pulse and post-pulse ΔV [mV] and R_on / R_off [Ω] from IR Drop Analysis (accepted impulses only);
  * OCV: every source available, sampled and extrapolated OCV of the rest before and after the pulse, and the ICI
    pseudo-OCV (end of the pulse, not an equilibrium value);
  * the √t slope of every method that fits one (GITT VA on the pulse, ICI on the interruption);
  * D of every series in use (accepted fit and rests, current), in the display unit chosen in Diffusion Results.

Columns that are empty for every impulse are left out; an empty cell is a value that does not exist, never zero.
Only D in use is written: a proposed or out-of-date result leaves its cell empty, as it is not drawn either.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Optional

from .gitt_ici import D_UNITS, fit_window, series_key_of, series_label
from .gitt_ici_ir import side_of
from .gitt_ici_plot import branch_name, x_value

BRANCH_ORDER = ("charge", "discharge")
SLOPE_METHODS = (("gitt_va", "GITT VA"), ("ici", "ICI"))     # methods with a √t fit of their own


@dataclass
class ExportTable:
    """Column keys and titles (in file order) and the rows of each branch, as {key: value or None}."""
    columns: list[tuple[str, str]] = field(default_factory=list)
    rows: dict[str, list[dict]] = field(default_factory=dict)

    def count(self) -> int:
        """Number of impulse rows over every branch."""
        return sum(len(r) for r in self.rows.values())


def _finite(value) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


def _start_capacity(event, records, drops) -> Optional[float]:
    """Capacity [Ah] at the start of the pulse: the one recorded with D, else the pre-pulse IR reference sample."""
    for r in records:
        if r.coordinates is not None and _finite(r.coordinates.capacity_start_ah) is not None:
            return r.coordinates.capacity_start_ah
    item = drops.get(event.event_id)
    if item is not None:
        ref = side_of(item, "pre_pulse").reference
        if ref is not None:
            return _finite(ref.capacity_ah)
    return None


def build_table(events, used, all_records, drops: dict, rests, *, unit: str, branch_end_ah: Optional[dict] = None,
                mass_g: Optional[float] = None, ir_ok: Optional[set] = None) -> ExportTable:
    """Assemble the export rows. ``used`` are the D results in use (gitt_ici_review.usage), ``all_records`` every
    current record (for the ICI pseudo-OCV and the slopes of fits), ``drops`` the IR drops per impulse and ``rests``
    the gregorithm RestOcv of the scope. ``ir_ok`` are the impulses whose IR drops are accepted in IR Drop: only they
    get IR drop and R_on / R_off values (None: every impulse, for callers without IR review)."""
    branch_end_ah = branch_end_ah or {}
    factor = D_UNITS[unit][1]
    d_unit = D_UNITS[unit][0]
    rest_by_id = {r.rest_segment_id: r for r in rests}
    used_by_event: dict[str, list] = {}
    for r in used:
        used_by_event.setdefault(r.identity.event_id, []).append(r)
    fits_by_event: dict[str, list] = {}
    for r in all_records:
        if r.state.value != "error" and not r.stale:
            fits_by_event.setdefault(r.identity.event_id, []).append(r)
    d_titles = {series_label(r.identity): f"D {series_label(r.identity)} [{d_unit}]" for r in used}
    columns = [("impulse", "Impulse"), ("cycle", "Cycle"), ("half_cycle", "Half-cycle"),
               ("capacity", "Capacity [mAh]"), ("specific_capacity", "Specific capacity [mAh/g]"), ("soc", "SoC [%]"),
               ("ir_pre", "IR drop pre-pulse [mV]"), ("ir_post", "IR drop post-pulse [mV]"),
               ("r_on", "R_on [Ω]"), ("r_off", "R_off [Ω]"),
               ("ocv_sampled_before", "Sampled OCV before [V]"), ("ocv_sampled_after", "Sampled OCV after [V]"),
               ("ocv_extrapolated_before", "Extrapolated OCV before [V]"),
               ("ocv_extrapolated_after", "Extrapolated OCV after [V]"),
               ("ocv_pseudo", "Pseudo OCV (ICI) [V]")]
    columns += [(f"slope:{key}", f"Slope {name} [V/√s]") for key, name in SLOPE_METHODS]
    columns += [(f"d:{label}", title) for label, title in sorted(d_titles.items())]
    rows: dict[str, list[dict]] = {b: [] for b in BRANCH_ORDER}
    for number, event in enumerate(events, start=1):
        if not event.complete:
            continue
        own_used = used_by_event.get(event.event_id, [])
        own_fits = fits_by_event.get(event.event_id, [])
        capacity = _start_capacity(event, own_fits, drops)
        row: dict = {"impulse": number, "cycle": event.cycle, "half_cycle": event.half_cycle}
        if capacity is not None:
            row["capacity"] = capacity * 1000.0
            row["specific_capacity"] = x_value(capacity, event.polarity, event.half_cycle, "specific_mah_g",
                                               branch_end_ah, mass_g)
            row["soc"] = x_value(capacity, event.polarity, event.half_cycle, "soc", branch_end_ah, mass_g)
        item = drops.get(event.event_id) if ir_ok is None or event.event_id in ir_ok else None
        if item is not None:
            for side, dv, res in (("pre_pulse", "ir_pre", "r_on"), ("post_pulse", "ir_post", "r_off")):
                drop = side_of(item, side)
                volts = _finite(drop.delta_voltage_v)
                row[dv] = None if volts is None else volts * 1000.0
                row[res] = _finite(drop.resistance_ohm)
        for when, rest_id in (("before", event.rest_before_segment_id), ("after", event.rest_after_segment_id)):
            rest = rest_by_id.get(rest_id)
            if rest is not None:
                row[f"ocv_sampled_{when}"] = None if rest.sampled is None else _finite(rest.sampled.voltage_v)
                row[f"ocv_extrapolated_{when}"] = (None if rest.extrapolated is None
                                                   else _finite(rest.extrapolated.voltage_v))
        for r in sorted(own_fits, key=lambda r: r.identity.ocv_source.value):
            key = series_key_of(r.identity)
            details = r.details if isinstance(r.details, dict) else {}
            pseudo = (details.get("intermediates") or {}).get("pseudo_ocv_v") if key == "ici" else None
            if pseudo is not None and row.get("ocv_pseudo") is None:
                row["ocv_pseudo"] = _finite(pseudo)
            fit = (fit_window(r) or {}).get("fit")
            if key in dict(SLOPE_METHODS) and isinstance(fit, dict) and row.get(f"slope:{key}") is None:
                row[f"slope:{key}"] = _finite(fit.get("slope_v_per_sqrt_s"))
        for r in own_used:
            value = _finite(r.diffusion_m2_s)
            row[f"d:{series_label(r.identity)}"] = None if value is None else value * factor
        rows[branch_name(event.polarity)].append(row)
    keep = {"impulse", "cycle", "half_cycle"}
    keep |= {key for branch in rows.values() for row in branch for key, value in row.items() if value is not None}
    return ExportTable([(k, t) for k, t in columns if k in keep], {b: r for b, r in rows.items() if r})
