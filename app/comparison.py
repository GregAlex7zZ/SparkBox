"""Comparison: overlay of results that each dataset ALREADY published (no cross-sample science). PURE PYTHON (no Qt),
unit-tested in tests/test_comparison.py.

Families : Data Explorer (published points, one X and one Y), Capacity (X = Cycle, one Y column), dQ/dV
(Phase, Plot). No DCIR, no second Y axis, no table. One cycle filter with the ORIGINAL cycle IDs for every
dataset; a dataset without a cycle simply has no curve for it. Units: always the app units (dataset-only units are
ignored) so the axis is common. Outdated results are drawn with an "Outdated" badge. Selecting a dataset
never runs an analysis.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from .explorer import FilterError, Viewport, parse_cycle_filter, resolve_cycles

EXPLORER, CAPACITY, DQDV = "explorer", "capacity", "dqdv"
FAMILIES = ((EXPLORER, "Data Explorer"), (CAPACITY, "Capacity"), (DQDV, "dQ/dV"))

DATASET_PALETTE = ("#006D85", "#B05A31", "#5B8C2A", "#8569CD", "#C2417A",
                   "#2F6FB0", "#A07A12", "#1E8A7A", "#7A4E9E", "#6F8691")

# availability kinds reported by the controller for (dataset, family)
NONE, NOT_ANALYZED, RUNNING, CURRENT, CORRECTED, OUTDATED = "none", "notanalyzed", "running", "current", "corrected", "outdated"
DRAWABLE = (CURRENT, CORRECTED, OUTDATED)
WHERE = {EXPLORER: "Data Elaboration", CAPACITY: "Capacity", DQDV: "dQ/dV"}


def status_text(family: str, kind: str, extra: str = "") -> str:
    """Line under the dataset name in the Datasets list."""
    if kind == NONE:
        return "Imported only · run Data Elaboration"
    if kind == NOT_ANALYZED:
        return f"Not analyzed · analyze in {WHERE[family]}"
    if kind == RUNNING:
        return "Running · the previous result is shown when there is one"
    base = {CURRENT: "Current", CORRECTED: "DCIR corrected", OUTDATED: "Outdated"}[kind]
    return f"{base} · {extra}" if extra else base


def can_draw(kind: str) -> bool:
    return kind in DRAWABLE


def shade(hex_color: str, f: float) -> str:
    """Lighter tone of a dataset colour for its later cycles (display only)."""
    n = int(hex_color.lstrip("#"), 16)
    r, g, b = n >> 16, (n >> 8) & 255, n & 255
    m = lambda c: round(c + (255 - c) * max(0.0, min(0.8, f)))
    return f"#{m(r):02X}{m(g):02X}{m(b):02X}"


@dataclass
class FamilyView:
    x: str = "discharge_capacity_ah"            # Data Explorer only
    y: str = "voltage_v"                         # Data Explorer column, or Capacity column
    phase: str = "both"                          # dQ/dV only
    plot: str = "smoothed"                       # dQ/dV only
    cycles_text: str = ""
    cycles: tuple = ()                           # applied ranges, () = all
    cycles_error: str = ""
    viewport: Viewport = field(default_factory=Viewport)

    def type_cycles(self, text: str) -> None:
        self.cycles_text = text
        try:
            parse_cycle_filter(text)
            self.cycles_error = ""
        except FilterError as e:
            self.cycles_error = str(e)

    def apply_cycles(self) -> bool:
        if self.cycles_error:
            return False
        new = parse_cycle_filter(self.cycles_text)
        changed = new != self.cycles
        self.cycles = new
        return changed

    def cycles_of(self, available: list[int]) -> Optional[tuple[int, ...]]:
        """The SAME literal IDs for every dataset; None = all of its cycles; () = none of them exists here."""
        return resolve_cycles(self.cycles, available)


DEFAULT_VIEWS = {
    EXPLORER: dict(x="discharge_capacity_ah", y="voltage_v", cycles_text="1", cycles=((1, 1),)),
    CAPACITY: dict(y="discharge_capacity_ah"),
    DQDV: dict(),
}


@dataclass
class CompareState:
    family: str = EXPLORER                       # opens on Data Explorer
    checked: list = field(default_factory=list)  # dataset ids, in the order they were checked
    colors: dict = field(default_factory=dict)   # ds_id -> hex (stable, assigned once)
    views: dict = field(default_factory=lambda: {k: FamilyView(**v) for k, v in DEFAULT_VIEWS.items()})
    legend: bool = True
    line_width: float = 1.6
    style: str = "lines"                         # lines | markers | lines_markers
    cycle_shading: bool = True                   # later cycles of a dataset in lighter tones of its colour
    _next: int = 0

    @property
    def view(self) -> FamilyView:
        return self.views[self.family]

    def color(self, ds_id: str) -> str:
        if ds_id not in self.colors:                 # assigned once: independent of the checking order later
            self.colors[ds_id] = DATASET_PALETTE[self._next % len(DATASET_PALETTE)]
            self._next += 1
        return self.colors[ds_id]

    def set_color(self, ds_id: str, hex_color: str) -> None:
        self.colors[ds_id] = hex_color

    def toggle(self, ds_id: str, on: bool, drawable: bool) -> bool:
        """Only datasets with a drawable result of the current family can be checked."""
        if on and not drawable:
            return False
        if on and ds_id not in self.checked:
            self.checked.append(ds_id)
            self.color(ds_id)
        elif not on and ds_id in self.checked:
            self.checked.remove(ds_id)
        return True

    def drawn(self, kinds: dict) -> list[str]:
        """Checked datasets that can be drawn for the current family (kinds: ds_id -> availability kind)."""
        return [d for d in self.checked if can_draw(kinds.get(d, NONE))]

    def message(self, kinds: dict) -> tuple[str, str]:
        """('', '') when the chart is shown; else (title, text) (notice with one dataset)."""
        n = len(self.drawn(kinds))
        if not kinds:
            return ("Open at least two datasets to compare",
                    "Comparison overlays results already calculated for each dataset. Nothing is calculated here.")
        if n == 0:
            return ("Select datasets on the right",
                    "Check two or more datasets in Comparison Settings. Only results that were already analyzed can "
                    "be compared; nothing is calculated here.")
        if n == 1:
            return ("Select at least one more dataset",
                    "One dataset is selected. Comparison overlays two or more datasets; its own sections show it alone.")
        return "", ""

    def dataset_closed(self, ds_id: str) -> None:
        if ds_id in self.checked:
            self.checked.remove(ds_id)
        self.colors.pop(ds_id, None)

    def cycle_color(self, ds_id: str, index: int, n: int) -> str:
        """Colour of the index-th shown cycle (of n) of a dataset: base colour, then lighter tones."""
        base = self.color(ds_id)
        if not self.cycle_shading or n <= 1:
            return base
        return shade(base, 0.6 * index / (n - 1))


def footer_capacity(n_datasets: int, n_records: int) -> str:
    return f"{n_datasets:,} datasets · {n_records:,} cycle records drawn"


def footer_dqdv(n_datasets: int, n_cycles: int, n_curves: int, n_points: int) -> str:
    return (f"{n_datasets:,} datasets · {n_cycles:,} cycle{'s' if n_cycles != 1 else ''} · {n_curves:,} curves · "
            f"{n_points:,} derivative points (calculated on each dataset's voltage grid, not measured)")


def missing_note(names_missing: list[str], cycles_text: str) -> str:
    """'Reference cell A has none of the cycles 120-128' - the IDs are never renumbered or filled."""
    if not names_missing:
        return ""
    who = ", ".join(names_missing[:3]) + ("…" if len(names_missing) > 3 else "")
    return f"{who}: none of the cycles {cycles_text or 'shown'} exists (no cycle is renumbered or filled)"
