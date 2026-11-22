"""Right dock of the DCIR section: 'DCIR Settings'. Detection is expanded (read-only summary, the
parameters are edited in the Detection step); the chart groups start collapsed, as in Capacity."""
from __future__ import annotations
from typing import Optional
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QFrame, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget
from ..dcir import DET_FIELDS, FACTORY_DETECTION, POLARITY_LABELS, DcirState, Step, describe_params
from ..dcir_controller import DcirController
from ..explorer import CHART
from ..theme import ThemeManager
from .controls import ColorSquare, LabelRow, ParamField, Switch
from .elab_dock import Accordion


def _btn(text: str, kind: str) -> QPushButton:
    b = QPushButton(text)
    b.setProperty(kind, True)
    b.setFixedHeight(30)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def _muted(text: str = "", size: int = 11) -> QLabel:
    l = QLabel(text)
    l.setProperty("muted", True)
    l.setWordWrap(True)
    l.setStyleSheet(f"font-size: {size * .75:g}pt;")
    return l


class DcirSettingsDock(QFrame):
    unitsRequested = Signal()

    def __init__(self, theme: ThemeManager, ctl: DcirController):
        super().__init__()
        self.setObjectName("DockCard")
        self.theme, self.ctl, self.ds_id = theme, ctl, None
        self._preferences_mode = False
        self._profiles = None
        self._defaults_state = None
        self._persistence_error = ""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(12)
        head = QHBoxLayout()
        t = QLabel("DCIR Settings")
        t.setStyleSheet("font-size: 11.25pt; font-weight: 500;")
        units = _btn("Units…", "link")
        units.clicked.connect(self.unitsRequested)
        head.addWidget(t)
        head.addStretch(1)
        head.addWidget(units)
        outer.addLayout(head)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 6, 0)
        bl.setSpacing(0)
        scroll = QScrollArea()
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("TabScroll")
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        # Detection (expanded, read-only)
        g = Accordion(theme, "Detection")
        self.params = _muted(size=12)
        g.cl.addWidget(self.params)
        edit = _btn("Edit in Detection", "link")
        self.detection_link = edit
        edit.clicked.connect(lambda: self._step(Step.DETECTION))
        g.cl.addWidget(edit, 0, Qt.AlignmentFlag.AlignLeft)
        self.mt = _muted(size=12)
        g.cl.addWidget(self.mt)
        self.detection_help = _muted("Parameters are edited in the Detection step; the measurement time in the Review strip.")
        g.cl.addWidget(self.detection_help)
        self.preferences_editor = QWidget()
        editor = QVBoxLayout(self.preferences_editor)
        editor.setContentsMargins(0, 0, 0, 0)
        self.preference_fields = {}
        for field in DET_FIELDS:
            widget = ParamField(theme, field)
            widget.textEdited.connect(lambda key, text: self._edit(lambda c: c.set_param_text(key, text)))
            self.preference_fields[field.key] = widget
            editor.addWidget(widget)
        form = QFormLayout()
        self.preference_polarity = QComboBox()
        for key, label in POLARITY_LABELS.items():
            self.preference_polarity.addItem(label, key)
        self.preference_polarity.activated.connect(
            lambda i: self._edit(lambda c: c.set_polarity(self.preference_polarity.itemData(i))))
        form.addRow("Polarity", self.preference_polarity)
        self.preference_time, self.preference_current_time = QLineEdit(), QLineEdit()
        self.preference_time.setAccessibleName("DCIR voltage measurement time [s]")
        self.preference_current_time.setAccessibleName("DCIR current measurement time [s]")
        self.preference_current_time.setPlaceholderText("3rd pulse sample")
        self.preference_time.editingFinished.connect(self._set_measurement_time)
        self.preference_current_time.editingFinished.connect(self._set_current_time)
        form.addRow("Voltage measurement time [s]", self.preference_time)
        form.addRow("Current measurement time [s]", self.preference_current_time)
        editor.addLayout(form)
        self.preference_error = _muted()
        editor.addWidget(self.preference_error)
        g.cl.addWidget(self.preferences_editor)
        self.preferences_editor.hide()
        bl.addWidget(g)
        # Inspection chart (collapsed)
        g2 = Accordion(theme, "Inspection Chart")
        g2.head.setChecked(False)
        row = QHBoxLayout()
        row.addWidget(LabelRow(theme, "Shade Zones", ("Shade rest before, pulse and rest after as reported by gregorithm.",)))
        row.addStretch(1)
        self.zones = Switch(theme, "Shade Zones", True)
        self.zones.toggled.connect(lambda on: self._edit(lambda c: setattr(c, "show_zones", on)))
        row.addWidget(self.zones)
        w = QWidget()
        w.setLayout(row)
        g2.cl.addWidget(w)
        g2.cl.addWidget(_muted("Voltage, current and marker colours are the fixed chart palette."))
        bl.addWidget(g2)
        # Results chart (collapsed)
        g3 = Accordion(theme, "Results Chart")
        g3.head.setChecked(False)
        row = QHBoxLayout()
        row.addWidget(LabelRow(theme, "Point Color", ("One colour for every calculated measurement (charge, discharge and manual).",)))
        row.addStretch(1)
        self.color = ColorSquare(theme, "Point Color")
        self.color.colorChosen.connect(lambda col: self._edit(lambda c: setattr(c, "res_color", col)))
        row.addWidget(self.color)
        w = QWidget()
        w.setLayout(row)
        g3.cl.addWidget(w)
        reset = _btn("Reset Color", "link")
        reset.clicked.connect(lambda: self._edit(lambda c: setattr(c, "res_color", CHART["y1"])))
        g3.cl.addWidget(reset, 0, Qt.AlignmentFlag.AlignLeft)
        bl.addWidget(g3)
        bl.addStretch(1)
        ctl.changed.connect(lambda ds: self.refresh() if ds == self.ds_id else None)

    def set_preferences_mode(self, enabled: bool, profiles=None) -> None:
        self._preferences_mode = enabled
        if profiles is not None:
            self._profiles = profiles
        if enabled and self.ds_id is None and self._profiles is not None:
            from ..settings_profiles import DCIR_FIELDS
            self._defaults_state = DcirState()
            for key, value in self._profiles.starting_preferences()["dcir"].items():
                if key in DCIR_FIELDS:
                    setattr(self._defaults_state, key, value)
            self._persistence_error = ""
        self.preferences_editor.setVisible(enabled)
        self.detection_link.setVisible(not enabled)
        self.detection_help.setText(
            "Edit parameters here, then use Detect or Apply & Calculate in DCIR to calculate updated results." if enabled else
            "Parameters are edited in the Detection step; the measurement time in the Review strip.")
        self.refresh()

    def default_settings_error(self) -> str:
        c = self._defaults_state if self._preferences_mode and self.ds_id is None else None
        if c is not None and (c.param_errors or c.time_error or c.current_time_error):
            return "Fix the highlighted DCIR starting settings before saving a profile."
        return self._persistence_error

    def _persist_defaults(self) -> None:
        c = self._defaults_state
        if c is None or c.param_errors or c.time_error or c.current_time_error:
            return
        from ..settings_profiles import DCIR_FIELDS
        data = self._profiles.starting_preferences()
        data["dcir"] = {key: getattr(c, key) for key in DCIR_FIELDS}
        try:
            self._profiles.update_starting_preferences(data)
            self._persistence_error = ""
        except (ValueError, OSError) as error:
            self._persistence_error = str(error)

    def _set_measurement_time(self) -> None:
        if self.ds_id:
            self.ctl.set_time_text(self.ds_id, self.preference_time.text())
        else:
            self._edit(lambda c: c.set_time_text(self.preference_time.text()))

    def _set_current_time(self) -> None:
        if self.ds_id:
            self.ctl.set_current_time_text(self.ds_id, self.preference_current_time.text())
        else:
            self._edit(lambda c: c.set_current_time_text(self.preference_current_time.text()))

    def _edit(self, fn) -> None:
        if self.ds_id:
            self.ctl.edit(self.ds_id, fn)
        elif self._preferences_mode and self._defaults_state is not None:
            fn(self._defaults_state)
            self._persist_defaults()
            self.refresh()

    def _step(self, step: Step) -> None:
        self._edit(lambda c: setattr(c, "step", step))

    def show_dataset(self, ds_id: Optional[str]) -> None:
        self.ds_id = ds_id
        self.refresh()

    def refresh(self) -> None:
        c = self.ctl.dc(self.ds_id) if self.ds_id else (self._defaults_state if self._preferences_mode else None)
        if c is None:
            return
        self.params.setText(describe_params(c.detection.params if c.detection else c.params)
                            + (" (new dataset defaults)" if self.ds_id is None else ("" if c.detection else " (not detected yet)")))
        self.mt.setText(f"Measurement time: {c.measurement_time_s:g} s (all pulses)")
        self.zones.blockSignals(True)
        self.zones.setChecked(c.show_zones)
        self.zones.blockSignals(False)
        self.color.set_color(c.res_color)
        for key, widget in self.preference_fields.items():
            widget.show_value(getattr(c.params, key), c.param_texts.get(key), c.param_errors.get(key, ""),
                              getattr(c.params, key) != getattr(FACTORY_DETECTION, key), getattr(FACTORY_DETECTION, key))
        self.preference_polarity.setCurrentIndex(self.preference_polarity.findData(c.params.polarity))
        for widget, value in ((self.preference_time, c.measurement_time_s),
                              (self.preference_current_time, c.current_time_s)):
            if not widget.hasFocus():
                widget.setText("" if value is None else f"{value:g}")
        d = self.ctl.state.get(self.ds_id)
        blocked = c.is_running or (d is not None and (d.elab.is_running or d.correction.is_running))
        corrected = d is not None and (d.correction.applied is not None or d.correction.removed_at)
        self.preferences_editor.setEnabled(not blocked)
        note = ("Wait for processing to finish before editing these parameters." if blocked else
                "Parameters can be edited, but new Detection and automatic measurements remain blocked after Cycle Correction. "
                "Calculated results are kept; process the original data again to use new detection or measurement inputs."
                if corrected else
                "Starting values are saved locally for future imports; existing datasets and the Default profile stay unchanged."
                if self.ds_id is None else "")
        self.preference_error.setText(c.time_error or c.current_time_error or self._persistence_error or note)
