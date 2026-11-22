"""Pure-Python session state and unlock rules (no Qt). Unit-tested in tests/test_state.py."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Optional

from .elaboration import ElabResult, ElabState
from .explorer import ExplorerState
from .capacity import CapacityState
from .dcir import DcirState
from .correction import CorrectionState
from .dqdv import DqdvState
from .gitt_ici import GittIciState
from .units import UnitPrefs


class Section(str, Enum):
    DATA = "data"
    ELABORATION = "elaboration"
    EXPLORER = "explorer"
    CAPACITY = "capacity"
    DCIR = "dcir"
    GITT_ICI = "gitt_ici"
    DQDV = "dqdv"
    COMPARISON = "comparison"


SECTION_ORDER = [Section.DATA, Section.ELABORATION, Section.EXPLORER, Section.CAPACITY,
                 Section.DCIR, Section.GITT_ICI, Section.DQDV, Section.COMPARISON]
SECTION_LABELS = {Section.DATA: "Data", Section.ELABORATION: "Data Elaboration", Section.EXPLORER: "Data Explorer",
                  Section.CAPACITY: "Capacity", Section.DCIR: "DCIR", Section.GITT_ICI: "GITT / ICI", Section.DQDV: "dQ/dV",
                  Section.COMPARISON: "Comparison"}
SECTION_ICONS = {Section.DATA: "database", Section.ELABORATION: "sliders-horizontal", Section.EXPLORER: "chart-line",
                 Section.CAPACITY: "capacity", Section.DCIR: "dcir", Section.GITT_ICI: "chart-line", Section.DQDV: "dqdv",
                 Section.COMPARISON: "arrow-left-right"}
UNLOCK_REASON = {Section.ELABORATION: "Load a dataset to unlock",
                 Section.EXPLORER: "Run Data Elaboration to unlock", Section.CAPACITY: "Run Data Elaboration to unlock",
                 Section.DCIR: "Run Data Elaboration to unlock", Section.DQDV: "Run Data Elaboration to unlock",
                 Section.GITT_ICI: "Run Data Elaboration to unlock",
                 Section.COMPARISON: "Needs at least two elaborated datasets"}


class Status(IntEnum):
    LOADED = 1       # imported, not (or no longer) elaborated
    ELABORATED = 2


@dataclass
class Dataset:
    id: str
    name: str
    path: str
    elab: ElabState = field(default_factory=ElabState)
    explorer: ExplorerState = field(default_factory=ExplorerState)
    capacity: CapacityState = field(default_factory=CapacityState)
    dcir: DcirState = field(default_factory=DcirState)
    gitt_ici: GittIciState = field(default_factory=GittIciState)
    correction: CorrectionState = field(default_factory=CorrectionState)
    dqdv: DqdvState = field(default_factory=DqdvState)
    units_override: Optional[UnitPrefs] = None

    @property
    def status(self) -> Status:
        return Status.ELABORATED if self.elab.has_result else Status.LOADED


def available_sections(active: Optional[Dataset], datasets: list[Dataset]) -> dict[Section, bool]:
    elaborated_active = active is not None and active.status >= Status.ELABORATED
    n_elab = sum(1 for d in datasets if d.status >= Status.ELABORATED)
    return {
        Section.DATA: True,
        Section.ELABORATION: active is not None,
        Section.EXPLORER: elaborated_active,
        Section.CAPACITY: elaborated_active,
        Section.DCIR: elaborated_active,
        Section.GITT_ICI: elaborated_active,
        Section.DQDV: elaborated_active,
        Section.COMPARISON: n_elab >= 2,
    }


def fallback_section(current: Section, avail: dict[Section, bool]) -> Section:
    """Keep `current` if still available, else the highest available section in sidebar order,
    excluding Comparison (approved rule)."""
    if avail.get(current, False):
        return current
    candidates = [s for s in SECTION_ORDER if avail.get(s, False) and s is not Section.COMPARISON]
    return candidates[-1] if candidates else Section.DATA


class SessionState:
    def __init__(self) -> None:
        self.datasets: list[Dataset] = []
        self.active_id: Optional[str] = None

    @property
    def active(self) -> Optional[Dataset]:
        return next((d for d in self.datasets if d.id == self.active_id), None)

    def get(self, ds_id: str) -> Optional[Dataset]:
        return next((d for d in self.datasets if d.id == ds_id), None)

    def add(self, ds: Dataset) -> None:
        self.datasets.append(ds)
        self.active_id = ds.id

    def remove(self, ds_id: str) -> None:
        idx = next((i for i, d in enumerate(self.datasets) if d.id == ds_id), None)
        if idx is None:
            return
        self.datasets.pop(idx)
        if self.active_id == ds_id:
            self.active_id = self.datasets[min(idx, len(self.datasets) - 1)].id if self.datasets else None

    def set_active(self, ds_id: str) -> None:
        if self.get(ds_id):
            self.active_id = ds_id

    def mark_elaborated(self, ds_id: str, result: Optional[ElabResult] = None) -> None:
        d = self.get(ds_id)
        if d:
            d.elab.finish(result or ElabResult("demo", 0, 0, 0, 0, d.elab.params))

    def invalidate_elaboration(self, ds_id: str) -> None:
        d = self.get(ds_id)
        if d:
            d.elab.invalidate_result()

    def running_id(self) -> Optional[str]:
        return next((d.id for d in self.datasets if d.elab.is_running), None)

    def availability(self) -> dict[Section, bool]:
        return available_sections(self.active, self.datasets)
