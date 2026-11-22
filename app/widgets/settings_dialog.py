"""Application Settings window: Profiles, Appearance, Units, New Dataset Defaults and
Advanced Settings.

Layout follows the Apple Human Interface Guidelines for settings windows: a sidebar lists every pane so nothing is
hidden behind a menu, the profile card sits at the top of the sidebar (like the account card in macOS System
Settings) so profiles are discoverable on first open, and every change takes effect immediately. There is no
Apply step; Done only closes the window. Invalid numbers are never committed: the last valid value stays in use
until the highlighted field is fixed.
"""
from typing import Optional
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QScrollArea,
                               QStackedWidget, QVBoxLayout, QWidget)
from ..elab_controller import UserDefaults
from ..elaboration import (CYCLE_ORDER_LABELS, CYCLE_ORDER_POPOVER, FACTORY_DEFAULTS, NUM_FIELDS, ElabParams,
                           parse_number, with_value)
from ..theme import MODES, ThemeManager, set_prop
from ..units import FACTORY_UNITS, OPTIONS, QUANTITIES, QUANTITY_LABELS, UnitPrefs
from .controls import LabelRow, ParamField, SegmentedControl, Switch

LABELS = {"system": "System", "light": "Light", "dark": "Dark"}

# Sidebar panes in display order. Profiles is reached through the profile card above the list.
PANES = {"appearance": "Appearance", "units": "Units", "elab": "New Dataset Defaults",
         "advanced": "Advanced Settings"}

PROFILE_STEPS = ("Set up the app the way you like to work.",
                 "Click Create New Profile to save those settings under a name.",
                 "Later, select the profile and click Use Profile to bring them all back.")


def _muted(text: str, small: bool = False) -> QLabel:
    l = QLabel(text)
    l.setProperty("muted", True)
    if small:
        l.setProperty("small", True)
    l.setWordWrap(True)
    return l


def _header(layout: QVBoxLayout, title: str, text: str) -> None:
    """Pane title plus one short sentence on what the pane controls."""
    t = QLabel(title)
    t.setProperty("paneTitle", True)
    layout.addWidget(t)
    layout.addWidget(_muted(text))


class SettingsDialog(QDialog):
    """Modal settings window. Without ``owner`` only Appearance, Units and New Dataset Defaults are shown."""

    def __init__(self, theme: ThemeManager, defaults: UserDefaults, parent=None, units=None,
                 ds_id: Optional[str] = None, ds_name: str = "", page: str = "appearance", owner=None):
        super().__init__(parent)
        self.theme, self.defaults = theme, defaults
        self.units_ctl, self.ds_id, self.ds_name = units, ds_id, ds_name
        self.owner = owner
        self._borrowed = []
        self.setWindowTitle("Settings")
        self.setMinimumWidth(640)
        self.resize(900 if owner else 720, 620)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 20, 16)
        lay.setSpacing(12)
        body = QHBoxLayout()
        body.setSpacing(20)
        lay.addLayout(body, 1)

        keys = [k for k in PANES if k != "units" or units]
        if not owner:
            keys.remove("advanced")
        self.order = (["profiles"] if owner else []) + keys

        side = QFrame()
        side.setObjectName("SideCard")
        side.setFixedWidth(220)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(8, 8, 8, 8)
        sv.setSpacing(8)
        self.profile_card = None
        if owner:
            sv.addWidget(self._profile_card())
        self.nav = QListWidget()
        self.nav.setObjectName("SettingsNav")
        self.nav.setAccessibleName("Settings sections")
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for key in keys:
            item = QListWidgetItem(PANES[key])
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.nav.addItem(item)
        sv.addWidget(self.nav, 1)
        body.addWidget(side)

        self.stack = QStackedWidget()
        builders = dict(profiles=lambda: self._scroll_page(self._profiles()),
                        appearance=lambda: self._scroll_page(self._appearance()),
                        units=lambda: self._scroll_page(self._units()),
                        elab=lambda: self._scroll_page(self._elab()),
                        advanced=self._advanced)
        for key in self.order:
            self.stack.addWidget(builders[key]())
        body.addWidget(self.stack, 1)
        self.nav.currentRowChanged.connect(self._nav_changed)

        row = QHBoxLayout()
        row.addWidget(_muted("Changes take effect immediately.", True), 1)
        self.done_btn = QPushButton("Done")
        self.done_btn.setProperty("primary", True)
        self.done_btn.setDefault(True)
        self.done_btn.clicked.connect(self.accept)
        row.addWidget(self.done_btn)
        lay.addLayout(row)
        self._refresh_elab()
        self.select_page(page if page in self.order else self.order[0])

    # --- Navigation ---
    def current_page(self) -> str:
        return self.order[self.stack.currentIndex()]

    def select_page(self, page: str) -> None:
        if page not in self.order:
            return
        self.stack.setCurrentIndex(self.order.index(page))
        self.nav.blockSignals(True)
        if page == "profiles":
            self.nav.setCurrentRow(-1)
            self.nav.clearSelection()
        else:
            self.nav.setCurrentRow([self.nav.item(i).data(Qt.ItemDataRole.UserRole)
                                    for i in range(self.nav.count())].index(page))
        self.nav.blockSignals(False)
        if self.profile_card is not None:
            self.profile_card.setChecked(page == "profiles")

    def _nav_changed(self, row: int) -> None:
        if row >= 0:
            self.select_page(self.nav.item(row).data(Qt.ItemDataRole.UserRole))

    def _profile_card(self) -> QPushButton:
        """Always-visible entry point that names the profile in use and says what profiles are for."""
        card = QPushButton()
        card.setObjectName("ProfileCard")
        card.setCheckable(True)
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.setMinimumHeight(64)
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(2)
        self.profile_card_name = QLabel()
        self.profile_card_name.setProperty("cardTitle", True)
        hint = _muted("Save and switch whole sets of settings", True)
        for label in (self.profile_card_name, hint):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            v.addWidget(label)
        card.clicked.connect(lambda: self.select_page("profiles"))
        self.profile_card = card
        return card

    @staticmethod
    def _scroll_page(widget):
        area = QScrollArea()
        for surface in (area, area.viewport(), widget):
            surface.setObjectName("Root")
            surface.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setWidget(widget)
        return area

    # --- Advanced Settings (the real section panels, borrowed while the window is open) ---
    def _advanced(self) -> QWidget:
        """Borrow the actual panels for this modal dialog; restore ownership on every exit path."""
        from ..state import Section, SECTION_LABELS
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        _header(lay, "Advanced Settings",
                f"Analysis and plot settings of {self.ds_name}, one section at a time. These are the same controls "
                "as the Advanced Settings panel next to each section." if self.ds_id else
                "Analysis and plot settings, one section at a time. DCIR and Comparison can be set before opening "
                "a file; the other sections need an open file.")
        self.advanced_section = QComboBox()
        self.advanced_section.setAccessibleName("Section")
        self.advanced_stack = QStackedWidget()
        self.advanced_stack.setObjectName("DockHost")
        for section, panel in self.owner.dock.docks.items():
            self.advanced_section.addItem(SECTION_LABELS[section], section.value)
            if not self.ds_id and section not in (Section.COMPARISON, Section.DCIR):
                # A dataset panel without a dataset would be a frozen, unscrollable form: explain and offer the fix.
                self.advanced_stack.addWidget(self._no_dataset(SECTION_LABELS[section]))
                continue
            self.owner.dock.removeWidget(panel)
            self.advanced_stack.addWidget(panel)
            self._borrowed.append((section, panel))
            if section is Section.DCIR:
                panel.set_preferences_mode(True, self.owner.profiles)
        self.advanced_section.currentIndexChanged.connect(self.advanced_stack.setCurrentIndex)
        lay.addWidget(self.advanced_section)
        lay.addWidget(self.advanced_stack, 1)
        return w

    def _no_dataset(self, label: str) -> QWidget:
        """Empty state for a section whose settings belong to an open dataset."""
        w = QFrame()
        w.setObjectName("HowTo")
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 24, 24, 24)
        v.setSpacing(10)
        v.addStretch(1)
        title = QLabel("No File Open")
        title.setProperty("cardTitle", True)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text = _muted(f"{label} settings belong to a dataset. Open a file to change them; the profile in use "
                      "provides their starting values.")
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        button = QPushButton("Open File")
        button.setProperty("secondary", True)
        menu = QMenu(button)
        for action in (self.owner.act_auto, self.owner.act_manual):
            item = menu.addAction(action.text())
            item.setEnabled(action.isEnabled())
            # Close Settings first: the import shows its progress and any mapping dialog in the main window.
            item.triggered.connect(lambda _=False, a=action: (self.accept(), QTimer.singleShot(0, a.trigger)))
        button.setMenu(menu)
        for widget in (title, text):
            v.addWidget(widget)
        v.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addStretch(1)
        return w

    def restore_advanced_panels(self) -> None:
        for _section, panel in self._borrowed:
            if hasattr(panel, "set_preferences_mode"):
                panel.set_preferences_mode(False)
            self.advanced_stack.removeWidget(panel)
            self.owner.dock.addWidget(panel)
            panel.setEnabled(True)
        self._borrowed.clear()
        if self.owner:
            from ..main_window import DOCK_TITLES
            self.owner.dock.set_section(self.owner.current, DOCK_TITLES.get(self.owner.current, "Advanced Settings"))

    def done(self, result: int) -> None:
        self.restore_advanced_panels()
        super().done(result)

    # --- Profiles ---
    def _profiles(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        _header(v, "Profiles", "A profile is a saved set of your settings: theme, units, analysis parameters and "
                               "plot preferences. Switch profile to change all of them at once, for example one "
                               "profile per instrument or test protocol.")
        how = QFrame()
        how.setObjectName("HowTo")
        grid = QGridLayout(how)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        title = QLabel("How It Works")
        title.setProperty("cardTitle", True)
        grid.addWidget(title, 0, 0, 1, 2)
        for i, text in enumerate(PROFILE_STEPS, 1):
            num = QLabel(str(i))
            num.setObjectName("StepNum")
            num.setFixedSize(20, 20)
            num.setAlignment(Qt.AlignmentFlag.AlignCenter)
            step = QLabel(text)
            step.setWordWrap(True)
            grid.addWidget(num, i, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(step, i, 1)
        grid.setColumnStretch(1, 1)
        v.addWidget(how)

        # Creating a profile is the first thing a new user needs, so it is a labelled button above the list.
        top = QHBoxLayout()
        heading = QLabel("Your Profiles")
        heading.setProperty("cardTitle", True)
        top.addWidget(heading)
        top.addStretch(1)
        self.profile_new = QPushButton("Create New Profile…")
        self.profile_new.setToolTip("Save the current settings as a new profile")
        top.addWidget(self.profile_new)
        v.addLayout(top)
        self.profile_list = QListWidget()
        self.profile_list.setObjectName("ProfileList")
        self.profile_list.setAccessibleName("Profiles")
        self.profile_list.setMinimumHeight(96)
        self.profile_list.setMaximumHeight(150)           # keeps the explanation below visible; longer lists scroll
        self.profile_list.currentRowChanged.connect(self._profile_selection)
        self.profile_list.itemDoubleClicked.connect(lambda _item: self._load_profile())
        v.addWidget(self.profile_list)

        # Actions on the selected profile: housekeeping on the left, Use Profile as the default action on the right.
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.profile_rename = QPushButton("Rename…")
        self.profile_delete = QPushButton("Delete…")
        self.profile_delete.setToolTip("Delete the selected profile")
        self.profile_save = QPushButton("Update with Current Settings")
        self.profile_load = QPushButton("Use Profile")
        self.profile_load.setProperty("primary", True)
        for button in (self.profile_new, self.profile_delete, self.profile_rename, self.profile_save):
            button.setProperty("ghost", True)
        for button in (self.profile_rename, self.profile_delete):
            bar.addWidget(button)
        bar.addStretch(1)
        bar.addWidget(self.profile_save)
        bar.addWidget(self.profile_load)
        v.addLayout(bar)
        self.profile_status = _muted(self.owner.profiles.load_error)
        v.addWidget(self.profile_status)
        v.addWidget(_muted("Use Profile changes the open dataset, Comparison, units and the starting settings for "
                           "new datasets. It never runs an analysis: existing results stay until you run it again. "
                           "Profiles never store sample mass or area, file paths, results or DCIR decisions. "
                           "Default holds the original app settings and cannot be changed.", True))
        v.addStretch(1)
        self.profile_load.clicked.connect(self._load_profile)
        self.profile_save.clicked.connect(self._save_profile)
        self.profile_rename.clicked.connect(self._rename_profile)
        self.profile_new.clicked.connect(self._new_profile)
        self.profile_delete.clicked.connect(self._delete_profile)
        self._reload_profiles(self.owner.profiles.selected)
        return w

    def _profile_names(self) -> dict:
        return {"default": "Default", **{r["id"]: r["name"] for r in self.owner.profiles.records}}

    def _reload_profiles(self, ident) -> None:
        active = self.owner.profiles.selected
        names = self._profile_names()
        self.profile_list.blockSignals(True)
        self.profile_list.clear()
        for key, name in names.items():
            notes = (["original settings"] if key == "default" else []) + (["in use"] if key == active else [])
            item = QListWidgetItem(name + (f"   ·   {', '.join(notes)}" if notes else ""))
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(name)
            if key == active:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.profile_list.addItem(item)
        self.profile_list.setCurrentRow(list(names).index(ident) if ident in names else 0)
        self.profile_list.blockSignals(False)
        self.profile_card_name.setText(f"Profile: {names.get(active, 'Default')}")
        self.profile_card.setAccessibleName(f"Profiles. Profile in use: {names.get(active, 'Default')}")
        self._profile_selection()

    def _profile_ident(self) -> str:
        item = self.profile_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else "default"

    def _profile_name(self) -> str:
        return self._profile_names().get(self._profile_ident(), "Default")

    def _profile_selection(self, *_args) -> None:
        custom = self._profile_ident() != "default"
        for button in (self.profile_save, self.profile_rename, self.profile_delete):
            button.setEnabled(custom)
        self.profile_save.setToolTip("Replace the settings stored in this profile with the current ones" if custom
                                     else "Default cannot be changed. Use Create New Profile to save your settings.")

    def _profile_action(self, fn) -> None:
        try:
            fn()
        except (ValueError, KeyError, OSError) as error:
            self.profile_status.setText(str(error))

    def _confirm(self, title: str, text: str, action: str) -> bool:
        """Ask before replacing or deleting stored settings; Cancel is the default button."""
        box = QMessageBox(QMessageBox.Icon.Warning, title, text, parent=self)
        ok = box.addButton(action, QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(box.addButton(QMessageBox.StandardButton.Cancel))
        box.exec()
        return box.clickedButton() is ok

    def _capture_profile(self):
        if self.d_errors:
            raise ValueError("Fix the highlighted New Dataset Defaults before saving a profile.")
        data = self.owner.capture_preferences()
        if not self.owner.state.active:
            data["elaboration"] = self.defaults.params
        return data

    def _save_profile(self) -> None:
        name = self._profile_name()
        if not self._confirm("Update Profile", f"Replace the settings stored in “{name}” with the current settings?",
                             "Update"):
            return

        def save():
            self.owner.profiles.save(self._profile_ident(), self._capture_profile())
            self.profile_status.setText(f"“{name}” now holds the current settings.")
        self._profile_action(save)

    def _load_profile(self) -> None:
        def load():
            ident = self._profile_ident()
            self.owner.apply_preferences(ident)
            self.theme_seg.set_value(self.theme.mode)
            self.d_params, self.d_errors, self.d_texts = self.defaults.params, {}, {}
            self._refresh_elab()
            dataset = self.owner.state.active
            if dataset is None:
                self.owner.dcir_dock.set_preferences_mode(True, self.owner.profiles)
            if self.units_ctl:
                self.u_app = self.units_ctl.app_units
                custom = dataset.units_override if dataset else None
                self.u_ds_on, self.u_ds = custom is not None, custom or self.u_app
                self.u_switch.setChecked(self.u_ds_on)
                self._refresh_units()
            self._reload_profiles(ident)
            self.profile_status.setText(f"Now using “{self._profile_name()}”. Run analyses again to update results.")
        self._profile_action(load)

    def _rename_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "Rename Profile", "Profile name:", text=self._profile_name())
        if ok:
            def rename():
                ident = self._profile_ident()
                self.owner.profiles.rename(ident, name)
                self._reload_profiles(ident)
                self.profile_status.setText("Profile renamed.")
            self._profile_action(rename)

    def _suggested_name(self) -> str:
        used = {n.casefold() for n in self._profile_names().values()}
        n = 1
        while f"my profile {n}" in used:
            n += 1
        return f"My Profile {n}"

    def _new_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "New Profile",
                                        "Your current settings will be saved in a new profile.\n\nProfile name:",
                                        text=self._suggested_name())
        if ok:
            def create():
                ident = self.owner.profiles.create(name, self._capture_profile())
                self._reload_profiles(ident)
                self.profile_status.setText(f"Saved “{name.strip()}”. Select it and click Use Profile at any time "
                                            "to bring these settings back.")
            self._profile_action(create)

    def _delete_profile(self) -> None:
        name = self._profile_name()
        if not self._confirm("Delete Profile", f"Delete “{name}”? This can't be undone. "
                                               "Your current settings are not changed.", "Delete"):
            return

        def delete():
            self.owner.profiles.delete(self._profile_ident())
            self._reload_profiles(self.owner.profiles.selected)
            self.profile_status.setText(f"Deleted “{name}”.")
        self._profile_action(delete)

    # --- Appearance ---
    def _appearance(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        _header(v, "Appearance", "How the app looks.")
        v.addWidget(QLabel("Theme"))
        self.theme_seg = SegmentedControl([(m, LABELS[m]) for m in MODES], aria="Theme")
        self.theme_seg.set_value(self.theme.mode)
        self.theme_seg.valueChanged.connect(self.theme.set_mode)
        v.addWidget(self.theme_seg, 0, Qt.AlignmentFlag.AlignLeft)
        v.addWidget(_muted("System follows the light or dark setting and the accent colour of your computer. Light and Dark "
                           "use the SparkBox colours.", True))
        self.mascot_switch = None
        if self.owner is not None and hasattr(self.owner, "set_mascot_enabled"):
            row = QHBoxLayout()
            row.addWidget(QLabel("Show Mascot"))
            row.addStretch(1)
            self.mascot_switch = Switch(self.theme, "Show Mascot", bool(self.owner.mascot_enabled))
            self.mascot_switch.toggled.connect(self.owner.set_mascot_enabled)
            row.addWidget(self.mascot_switch)
            v.addSpacing(8)
            v.addLayout(row)
            v.addWidget(_muted("The lab assistant next to the SparkBox name. You can also remove it by right-clicking it.",
                               True))
        v.addStretch(1)
        return w

    # --- New Dataset Defaults (Data Elaboration default parameters) ---
    def _elab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)
        _header(v, "New Dataset Defaults", "Data Elaboration values every newly opened dataset starts with. Datasets "
                                           "that are already open keep their own values.")
        self.d_params: ElabParams = self.defaults.params
        self.d_errors: dict[str, str] = {}
        self.d_texts: dict[str, str] = {}
        self.d_row = LabelRow(self.theme, "Cycle Order", CYCLE_ORDER_POPOVER)
        self.d_order = QComboBox()
        self.d_order.setAccessibleName("Default Cycle Order")
        for k, lab in CYCLE_ORDER_LABELS.items():
            self.d_order.addItem(lab, k)
        self.d_order.activated.connect(self._order_changed)
        v.addWidget(self.d_row)
        v.addWidget(self.d_order)
        self.d_fields: dict[str, ParamField] = {}
        for f in NUM_FIELDS:
            pf = ParamField(self.theme, f)
            pf.textEdited.connect(self._typed)
            self.d_fields[f.key] = pf
            v.addWidget(pf)
        restore = QPushButton("Restore Factory Defaults")
        restore.setProperty("secondary", True)
        restore.setFixedHeight(30)
        restore.clicked.connect(self._restore)
        v.addWidget(restore, 0, Qt.AlignmentFlag.AlignLeft)
        v.addStretch(1)
        return w

    def _order_changed(self, i: int) -> None:
        self.d_params = with_value(self.d_params, "cycle_order", self.d_order.itemData(i))
        self._commit_elab()

    def _typed(self, key: str, text: str) -> None:
        val, err = parse_number(text)
        if err:
            self.d_errors[key], self.d_texts[key] = err, text
        else:
            self.d_errors.pop(key, None)
            self.d_texts.pop(key, None)
            self.d_params = with_value(self.d_params, key, val)
        self._commit_elab()

    def _restore(self) -> None:
        self.d_params, self.d_errors, self.d_texts = FACTORY_DEFAULTS, {}, {}
        self._commit_elab()

    def _commit_elab(self) -> None:
        """Save valid defaults at once; while a field is invalid the previously saved values stay in use."""
        self._refresh_elab()
        if not self.d_errors and self.d_params != self.defaults.params:
            self.defaults.set(self.d_params)

    def _refresh_elab(self) -> None:
        self.d_order.setCurrentIndex(self.d_order.findData(self.d_params.cycle_order))
        self.d_row.set_modified(self.d_params.cycle_order != FACTORY_DEFAULTS.cycle_order)
        for f in NUM_FIELDS:
            self.d_fields[f.key].show_value(getattr(self.d_params, f.key), self.d_texts.get(f.key),
                                            self.d_errors.get(f.key, ""),
                                            getattr(self.d_params, f.key) != getattr(FACTORY_DEFAULTS, f.key),
                                            getattr(FACTORY_DEFAULTS, f.key))

    # --- Units ---
    def _units(self) -> QWidget:
        """Apply to: All datasets (app units, remembered by the app) or This dataset (session only)."""
        u = self.units_ctl
        self.u_app: UnitPrefs = u.app_units
        d = u.state.get(self.ds_id) if self.ds_id else None
        self.u_ds_on = bool(d and d.units_override is not None)
        self.u_ds: UnitPrefs = d.units_override if d and d.units_override else u.app_units
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        _header(v, "Units", "Units used to show values in tables, charts, readouts and saved images. "
                            "Stored results are never changed.")
        v.addWidget(QLabel("Apply Units To"))
        self.u_scope = SegmentedControl([("app", "All Datasets"),
                                         ("ds", f"This Dataset · {self.ds_name}" if self.ds_name else "This Dataset")],
                                        aria="Apply units to")
        self.u_scope.set_value("ds" if self.u_ds_on else "app")
        self.u_scope._btn["ds"].setEnabled(bool(self.ds_id))
        if not self.ds_id:
            self.u_scope._btn["ds"].setToolTip("Open a dataset to set units for it only")
        self.u_scope.valueChanged.connect(lambda _v: self._refresh_units())
        v.addWidget(self.u_scope, 0, Qt.AlignmentFlag.AlignLeft)
        self.u_scope_hint = _muted("", True)
        v.addWidget(self.u_scope_hint)
        row = QHBoxLayout()
        self.u_switch_lbl = QLabel("Use custom units for this dataset")
        self.u_switch = Switch(self.theme, "Use custom units for this dataset", self.u_ds_on)
        self.u_switch.toggled.connect(self._ds_switch)
        row.addWidget(self.u_switch_lbl)
        row.addStretch(1)
        row.addWidget(self.u_switch)
        self.u_switch_row = QWidget()
        self.u_switch_row.setLayout(row)
        v.addWidget(self.u_switch_row)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        self.u_combo: dict[str, QComboBox] = {}
        self.u_note: dict[str, QLabel] = {}
        for i, q in enumerate(QUANTITIES):
            c = QComboBox()
            c.setAccessibleName(f"{QUANTITY_LABELS[q]} unit")
            for opt in OPTIONS[q]:
                c.addItem(opt, opt)
            c.activated.connect(lambda _i, qq=q, cc=c: self._unit_chosen(qq, cc.currentData()))
            note = _muted("", True)
            grid.addWidget(QLabel(QUANTITY_LABELS[q]), i, 0)
            grid.addWidget(c, i, 1)
            grid.addWidget(note, i, 2)
            self.u_combo[q], self.u_note[q] = c, note
        v.addLayout(grid)
        self.u_reset = QPushButton()
        self.u_reset.setProperty("secondary", True)
        self.u_reset.setFixedHeight(30)
        self.u_reset.clicked.connect(self._units_reset)
        v.addWidget(self.u_reset, 0, Qt.AlignmentFlag.AlignLeft)
        v.addStretch(1)
        self._refresh_units()
        return w

    def _ds_scope(self) -> bool:
        return self.u_scope.value() == "ds"

    def _ds_switch(self, on: bool) -> None:
        self.u_ds_on = on
        if on and self.u_ds is None:
            self.u_ds = self.u_app
        self._commit_units()

    def _unit_chosen(self, q: str, unit: str) -> None:
        if self._ds_scope():
            self.u_ds = self.u_ds.with_unit(q, unit)
        else:
            self.u_app = self.u_app.with_unit(q, unit)
        self._commit_units()

    def _units_reset(self) -> None:
        if self._ds_scope():
            self.u_ds_on, self.u_ds = False, self.u_app        # "Use app units"
            self.u_switch.setChecked(False)
        else:
            self.u_app = FACTORY_UNITS                         # "Restore factory defaults"
        self._commit_units()

    def _commit_units(self) -> None:
        """Both setters ignore unchanged values, so committing on every edit is cheap."""
        self._refresh_units()
        self.units_ctl.set_app_units(self.u_app)
        if self.ds_id:
            self.units_ctl.set_dataset_units(self.ds_id, self.u_ds if self.u_ds_on else None)

    def _refresh_units(self) -> None:
        ds = self._ds_scope()
        self.u_switch_row.setVisible(ds)
        self.u_scope_hint.setText(f"Only {self.ds_name or 'this dataset'} changes. Other datasets keep the app units."
                                  if ds else "Every dataset without custom units uses these. Defaults: h, mA, V, mAh, mWh.")
        cur = self.u_ds if ds else self.u_app
        editable = (not ds) or self.u_ds_on
        for q in QUANTITIES:
            c = self.u_combo[q]
            c.setCurrentIndex(c.findData(cur.unit(q)))
            c.setEnabled(editable)
            differs = ds and self.u_ds_on and cur.unit(q) != self.u_app.unit(q)
            set_prop(c, "changed", differs)
            self.u_note[q].setText(f"App setting: {self.u_app.unit(q)}" if differs else
                                   ("Same as app" if ds else " · ".join(OPTIONS[q])))
        self.u_reset.setText("Use App Units" if ds else "Restore Factory Defaults")
        self.u_reset.setEnabled(self.u_ds_on if ds else self.u_app != FACTORY_UNITS)
