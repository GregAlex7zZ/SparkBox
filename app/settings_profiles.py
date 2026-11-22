"""Versioned GUI preferences. Profiles contain settings, never publications or source paths."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import fields, is_dataclass
import json
import math
import uuid

from PySide6.QtCore import QSettings

from .comparison import CompareState, FamilyView
from .dcir import DetectionParams
from .elaboration import ElabParams
from .explorer import Range, Viewport
from .state import Dataset
from .units import UnitPrefs, from_mapping


EXPLORER_FIELDS = ("x", "y1", "y2", "y1_color", "y2_color", "legend", "point_tracking", "style",
                   "filter_input", "applied", "viewport")
CAPACITY_FIELDS = ("x", "y1", "y2", "y1_color", "y2_color", "legend", "style", "y2_same_scale",
                  "show_cycle0", "filter_input", "applied", "viewport")
DQDV_FIELDS = ("cycles_text", "smoothing", "window_text", "order_text", "method", "emin_text", "emax_text",
               "points_text", "trim_text", "bin_text", "sigma_text", "reconstruction")
DQDV_VIEW_FIELDS = ("phase", "plot", "shown_text", "shown", "viewport", "legend", "style", "line_width",
                    "raw_alpha", "raw_width_factor", "same_color", "colors", "dash", "recon_curves", "recon_viewport")
DCIR_FIELDS = ("params", "measurement_time_s", "current_time_s", "show_zones", "res_color",
               "f_state", "f_polarity", "f_source")
COMPARE_FIELDS = ("family", "views", "legend", "line_width", "style", "cycle_shading")
_TYPES = {c.__name__: c for c in (Range, Viewport, ElabParams, DetectionParams, UnitPrefs, FamilyView)}


def _pick(obj, names):
    return {key: deepcopy(getattr(obj, key)) for key in names}


def snapshot(dataset: Dataset, comparison: CompareState, theme: str, units: UnitPrefs) -> dict:
    """Capture only editable preferences; exclude sample metadata, decisions, IDs and run state."""
    return dict(theme=theme, units=units, dataset_units=deepcopy(dataset.units_override), elaboration=dataset.elab.params,
                explorer=_pick(dataset.explorer, EXPLORER_FIELDS),
                capacity=dict(reference_text=dataset.capacity.reference_text,
                              view=_pick(dataset.capacity.view, CAPACITY_FIELDS)),
                dcir=_pick(dataset.dcir, DCIR_FIELDS),
                correction=dict(threshold=dataset.correction.threshold),
                dqdv=dict(parameters=_pick(dataset.dqdv, DQDV_FIELDS),
                          view=_pick(dataset.dqdv.view, DQDV_VIEW_FIELDS)),
                comparison=_pick(comparison, COMPARE_FIELDS))


def factory_snapshot() -> dict:
    """The unchanged application baseline, including the Author's DCIR 600 s rests."""
    return snapshot(Dataset("", "", ""), CompareState(), "light", UnitPrefs())


def _encode(value):
    if is_dataclass(value):
        return {"$type": type(value).__name__, "values": {f.name: _encode(getattr(value, f.name)) for f in fields(value)}}
    if isinstance(value, tuple):
        return {"$tuple": [_encode(v) for v in value]}
    if isinstance(value, dict):
        return {"$map": [[_encode(k), _encode(v)] for k, v in value.items()]}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError("Unsupported preference value")


def _decode(value):
    if not isinstance(value, dict):
        if value is None or isinstance(value, (str, bool, int, float)):
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Non-finite preference value")
            return value
        raise ValueError("Invalid preference value")
    if set(value) == {"$type", "values"} and value["$type"] in _TYPES:
        return _TYPES[value["$type"]](**{k: _decode(v) for k, v in value["values"].items()})
    if set(value) == {"$tuple"}:
        return tuple(_decode(v) for v in value["$tuple"])
    if set(value) == {"$map"}:
        return {_decode(k): _decode(v) for k, v in value["$map"]}
    raise ValueError("Invalid preference record")


def validate_preferences(data: dict) -> None:
    """Validate staged preferences before persistence or application, using existing GUI parsers."""
    from .dcir import DET_FIELDS, POLARITY_LABELS, parse_current_time, parse_measurement_time, parse_param
    from .dqdv import METHOD_CHOICES
    from .elaboration import CYCLE_ORDER_LABELS, NUM_FIELDS, parse_number
    from .explorer import COLUMNS, PLOT_STYLES, parse_cycle_filter
    from .capacity import CAP_BY_KEY, parse_reference_cycle
    from .correction import parse_threshold

    base = factory_snapshot()

    def shape(value, expected):
        if isinstance(expected, dict):
            if not isinstance(value, dict) or set(value) != set(expected):
                raise ValueError("Incomplete profile")
            for key in expected:
                # Arbitrary keys are allowed only for the known colour and line-style maps.
                if key in {"colors", "dash"}:
                    if not isinstance(value[key], dict):
                        raise ValueError("Invalid plot map")
                else:
                    shape(value[key], expected[key])
        elif expected is not None and type(value) is not type(expected):
            raise ValueError("Invalid preference type")

    shape(data, base)
    if data["theme"] not in ("light", "dark", "system") or from_mapping(vars(data["units"])) != data["units"]:
        raise ValueError("Invalid appearance or units")
    custom = data["dataset_units"]
    if custom is not None and (not isinstance(custom, UnitPrefs) or from_mapping(vars(custom)) != custom):
        raise ValueError("Invalid dataset units")
    p = data["elaboration"]
    if p.cycle_order not in CYCLE_ORDER_LABELS or any(parse_number(str(getattr(p, f.key)))[1] for f in NUM_FIELDS):
        raise ValueError("Invalid Data Elaboration parameters")
    c = data["dcir"]
    if c["params"].polarity not in POLARITY_LABELS or any(
            parse_param(f.key, str(getattr(c["params"], f.key)))[1] for f in DET_FIELDS):
        raise ValueError("Invalid DCIR detection parameters")
    if parse_measurement_time(str(c["measurement_time_s"]))[1] or parse_current_time(
            "" if c["current_time_s"] is None else str(c["current_time_s"]))[1]:
        raise ValueError("Invalid DCIR measurement times")
    d = Dataset("", "", "")
    for key, value in data["dqdv"]["parameters"].items():
        setattr(d.dqdv, key, value)
    if d.dqdv.method not in dict(METHOD_CHOICES) or set(d.dqdv.errors()) - {"cycles"}:
        raise ValueError("Invalid dQ/dV parameters")
    if d.dqdv.cycles_text.strip() and "cycles" in d.dqdv.errors():
        raise ValueError("Invalid dQ/dV cycle selection")
    if parse_reference_cycle(data["capacity"]["reference_text"])[1]:
        raise ValueError("Invalid Capacity reference cycle")
    if parse_threshold(str(data["correction"]["threshold"]))[1]:
        raise ValueError("Invalid Cycle Correction threshold")
    styles = dict(PLOT_STYLES)
    for view, columns in ((data["explorer"], {c.key for c in COLUMNS}),
                           (data["capacity"]["view"], set(CAP_BY_KEY))):
        if view["style"] not in styles or any(view[k] not in columns for k in ("x", "y1")):
            raise ValueError("Invalid plot settings")
        if view["y2"] is not None and view["y2"] not in columns:
            raise ValueError("Invalid second axis")
        parse_cycle_filter(view["filter_input"])
    if data["comparison"]["family"] not in CompareState().views:
        raise ValueError("Invalid Comparison mode")
    for view in data["comparison"]["views"].values():
        parse_cycle_filter(view.cycles_text)


def apply_dataset_preferences(dataset: Dataset, data: dict) -> None:
    """Change editable state only; keep all immutable results, provenance and sample metadata."""
    validate_preferences(data)
    dataset.units_override = data["dataset_units"]
    dataset.elab.reset_to(data["elaboration"])
    for obj, values in ((dataset.explorer, data["explorer"]),
                        (dataset.capacity.view, data["capacity"]["view"]),
                        (dataset.dqdv, data["dqdv"]["parameters"]),
                        (dataset.dqdv.view, data["dqdv"]["view"])):
        for key, value in values.items():
            setattr(obj, key, deepcopy(value))
    dataset.capacity.set_reference_text(data["capacity"]["reference_text"])
    dataset.correction.set_threshold_text(str(data["correction"]["threshold"]))
    c = dataset.dcir
    c.reset_params(data["dcir"]["params"])
    c.set_time_text(str(data["dcir"]["measurement_time_s"]))
    c.set_current_time_text("" if data["dcir"]["current_time_s"] is None else str(data["dcir"]["current_time_s"]))
    for key in ("show_zones", "res_color", "f_state", "f_polarity", "f_source"):
        setattr(c, key, data["dcir"][key])


class SettingsProfiles:
    """Default is immutable; user profiles are local QSettings records with stable IDs (none until created)."""
    KEY = "preferences/profiles_v1"
    STARTING_KEY = "preferences/starting_v1"

    def __init__(self, settings=None):
        self.settings = settings if settings is not None else QSettings("gregorithm", "gui")
        self.selected = "default"
        self.records = []
        self.load_error = ""
        raw = self.settings.value(self.KEY)
        if raw:
            try:
                doc = json.loads(raw)
                if doc["version"] != 1:
                    raise ValueError("Unsupported profile file")
                records = []
                for item in doc["profiles"]:
                    data = _decode(item["settings"])
                    validate_preferences(data)
                    records.append(dict(id=str(item["id"]), name=self._name(item["name"]), settings=data))
                ids = [r["id"] for r in records]
                names = [r["name"].casefold() for r in records]
                if "default" in ids or len(set(ids)) != len(ids) or len(set(names)) != len(names) or "default" in names:
                    raise ValueError("Duplicate profile")
                self.records = records
                self.selected = doc["selected"] if doc["selected"] in ["default", *ids] else "default"
            except (KeyError, ValueError, TypeError, AttributeError):
                self.load_error = "Stored profiles could not be read. Only Default is available."
        self._starting = None
        raw_starting = self.settings.value(self.STARTING_KEY)
        if raw_starting:
            try:
                starting = _decode(json.loads(raw_starting))
                validate_preferences(starting)
                self._starting = starting
            except (KeyError, ValueError, TypeError, AttributeError):
                self.load_error = "Stored starting settings could not be read; the selected profile is used."

    @staticmethod
    def _name(name):
        name = str(name).strip()
        if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
            raise ValueError("Enter a profile name of 1–80 characters.")
        return name

    def _unique_name(self, name, exclude=None):
        name = self._name(name)
        if name.casefold() == "default" or any(r["id"] != exclude and r["name"].casefold() == name.casefold() for r in self.records):
            raise ValueError("This profile name is already in use.")
        return name

    def get(self, ident):
        if ident == "default":
            return factory_snapshot()
        return deepcopy(next(r["settings"] for r in self.records if r["id"] == ident))

    def starting_preferences(self):
        """Editable defaults for future datasets, separate from the immutable Default profile."""
        return deepcopy(self._starting) if self._starting is not None else self.get(self.selected)

    def update_starting_preferences(self, data):
        validate_preferences(data)
        self._starting = deepcopy(data)
        self.settings.setValue(self.STARTING_KEY, json.dumps(_encode(data), ensure_ascii=False, allow_nan=False))
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            raise OSError("Could not save starting settings locally.")

    def save(self, ident, data):
        if ident == "default":
            raise ValueError("Default is read-only. Create a new profile instead.")
        validate_preferences(data)
        next(r for r in self.records if r["id"] == ident)["settings"] = deepcopy(data)
        self.persist()

    def create(self, name, data):
        name = self._unique_name(name)
        validate_preferences(data)
        ident = uuid.uuid4().hex
        self.records.append(dict(id=ident, name=name, settings=deepcopy(data)))
        self.persist()
        return ident

    def rename(self, ident, name):
        if ident == "default":
            raise ValueError("Default cannot be renamed.")
        name = self._unique_name(name, ident)
        next(r for r in self.records if r["id"] == ident)["name"] = name
        self.persist()

    def delete(self, ident):
        """Remove a saved profile. Deleting the profile in use keeps its values as the starting settings, so
        datasets opened afterwards are not silently changed; the in-use marker returns to Default."""
        if ident == "default":
            raise ValueError("Default cannot be deleted.")
        record = next(r for r in self.records if r["id"] == ident)
        if ident == self.selected:
            if self._starting is None:
                self.update_starting_preferences(record["settings"])
            self.selected = "default"
        self.records.remove(record)
        self.persist()

    def select(self, ident):
        self.get(ident)
        self.selected = ident
        self._starting = None
        self.settings.remove(self.STARTING_KEY)
        self.persist()

    def persist(self):
        doc = dict(version=1, selected=self.selected,
                   profiles=[dict(id=r["id"], name=r["name"], settings=_encode(r["settings"])) for r in self.records])
        self.settings.setValue(self.KEY, json.dumps(doc, ensure_ascii=False, allow_nan=False))
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            raise OSError("Could not save profiles to local application settings.")
