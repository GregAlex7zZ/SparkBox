"""Qt glue for display units. App units are an app preference (QSettings, the app remembers itself);
dataset units live only in the session (Dataset.units_override). Every change is pushed to the backend
adapter (set_display_units) and announced with `changed(ds_id)` so tables and charts reload."""
from __future__ import annotations
from typing import Optional
from PySide6.QtCore import QObject, QSettings, Signal
from .backend import Backend
from .state import SessionState
from .units import FACTORY_UNITS, QUANTITIES, UnitPrefs, effective, from_mapping, has_custom_units


class UnitsController(QObject):
    changed = Signal(str)              # dataset id whose effective units changed
    appChanged = Signal()              # global units changed, including datasets with overrides

    def __init__(self, state: SessionState, backend: Backend):
        super().__init__()
        self.state, self.backend = state, backend
        self._s = QSettings("gregorithm", "gui")
        self.app_units = from_mapping({q: self._s.value(f"units/{q}") for q in QUANTITIES})

    def units_for(self, ds_id: Optional[str]) -> UnitPrefs:
        d = self.state.get(ds_id) if ds_id else None
        return effective(self.app_units, d.units_override if d else None)

    def is_custom(self, ds_id: Optional[str]) -> bool:
        d = self.state.get(ds_id) if ds_id else None
        return bool(d) and has_custom_units(self.app_units, d.units_override)

    def dataset_added(self, ds_id: str) -> None:
        self.backend.set_display_units(ds_id, self.units_for(ds_id))

    def set_app_units(self, units: UnitPrefs) -> None:
        if units == self.app_units:
            return
        self.app_units = units
        for q in QUANTITIES:
            self._s.setValue(f"units/{q}", units.unit(q))
        for d in self.state.datasets:
            if d.units_override is None:
                self._push(d.id)
        self.appChanged.emit()

    def set_dataset_units(self, ds_id: str, units: Optional[UnitPrefs]) -> None:
        """units=None: 'Use app units' (removes the dataset-only choice)."""
        d = self.state.get(ds_id)
        if d is None or d.units_override == units:
            return
        d.units_override = units
        self._push(ds_id)

    def restore_factory(self) -> None:
        self.set_app_units(FACTORY_UNITS)

    def _push(self, ds_id: str) -> None:
        self.backend.set_display_units(ds_id, self.units_for(ds_id))
        self.changed.emit(ds_id)
