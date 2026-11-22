"""Display units (Settings > Units). PURE PYTHON (no Qt), unit-tested in tests/test_units.py.

Decisions: defaults h, mA, V, mAh, mWh, Ω ; the same units apply to tables, chart, hover readout and saved
images; units are chosen for the whole app OR for one dataset only; nothing is remembered between runs except
the app-level choice.

The canonical units of the published result are s, A, V, Ah, Wh. Converting a canonical value to a display
unit is a plain scale factor and is allowed at the interface boundary. WHERE the conversion runs (the
adapter, or the library if it offers display units) is the adapter's job: widgets never call `scale()` themselves,
they receive values already in display units from the backend adapter.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from typing import Optional

TIME, CURRENT, VOLTAGE, CAPACITY, ENERGY, RESISTANCE = "time", "current", "voltage", "capacity", "energy", "resistance"
QUANTITIES = (TIME, CURRENT, VOLTAGE, CAPACITY, ENERGY, RESISTANCE)
QUANTITY_LABELS = {TIME: "Time", CURRENT: "Current", VOLTAGE: "Voltage", CAPACITY: "Capacity", ENERGY: "Energy", RESISTANCE: "Resistance"}
CANONICAL = {TIME: "s", CURRENT: "A", VOLTAGE: "V", CAPACITY: "Ah", ENERGY: "Wh", RESISTANCE: "Ω"}

# unit -> factor that multiplies a canonical value (value_display = value_canonical * factor)
FACTORS: dict[str, dict[str, float]] = {
    TIME: {"s": 1.0, "min": 1.0 / 60.0, "h": 1.0 / 3600.0},
    CURRENT: {"A": 1.0, "mA": 1e3, "µA": 1e6},
    VOLTAGE: {"V": 1.0, "mV": 1e3},
    CAPACITY: {"Ah": 1.0, "mAh": 1e3},
    ENERGY: {"Wh": 1.0, "mWh": 1e3},
    RESISTANCE: {"Ω": 1.0, "mΩ": 1e3},
}
OPTIONS = {q: tuple(FACTORS[q]) for q in QUANTITIES}


@dataclass(frozen=True)
class UnitPrefs:
    time: str = "h"
    current: str = "mA"
    voltage: str = "V"
    capacity: str = "mAh"
    energy: str = "mWh"
    resistance: str = "Ω"

    def unit(self, quantity: str) -> str:
        return getattr(self, quantity)

    def with_unit(self, quantity: str, unit: str) -> "UnitPrefs":
        if quantity not in QUANTITIES or unit not in FACTORS[quantity]:
            raise ValueError(f"Unknown unit {unit!r} for {quantity!r}")
        return replace(self, **{quantity: unit})

    def differs(self, other: "UnitPrefs") -> set[str]:
        return {q for q in QUANTITIES if self.unit(q) != other.unit(q)}


FACTORY_UNITS = UnitPrefs()


def scale(quantity: str, unit: str) -> float:
    """Factor from the canonical unit to `unit`. Used by the backend adapter only (see module docstring)."""
    return FACTORS[quantity][unit]


def effective(app: UnitPrefs, dataset_override: Optional[UnitPrefs]) -> UnitPrefs:
    """Units a dataset is shown in: its own units when the user set them for this dataset only, else the app units."""
    return dataset_override if dataset_override is not None else app


def has_custom_units(app: UnitPrefs, dataset_override: Optional[UnitPrefs]) -> bool:
    """Drives the 'Custom units' pill. A dataset override identical to the app units still counts as custom: the user
    asked for dataset-specific units, and later app changes will not affect it."""
    return dataset_override is not None


def from_mapping(values: dict) -> UnitPrefs:
    """Build prefs from stored strings (QSettings); unknown values fall back to the factory unit, never raise."""
    out = FACTORY_UNITS
    for q in QUANTITIES:
        u = values.get(q)
        if isinstance(u, str) and u in FACTORS[q]:
            out = out.with_unit(q, u)
    return out
