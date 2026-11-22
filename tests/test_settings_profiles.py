"""Local profile persistence and the shared Advanced Settings panels."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QSettings, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from app.comparison import CompareState
from app.demo_backend import DemoBackend
from app.elaboration import ElabResult, Run
from app.explorer import Range, Viewport
from app.fonts import load_fonts
from app.main_window import MainWindow
from app.settings_profiles import (SettingsProfiles, apply_dataset_preferences, factory_snapshot,
                                    snapshot, validate_preferences)
from app.state import Dataset, Section
from app.theme import ThemeManager
from app.units import UnitPrefs
from app.widgets.settings_dialog import SettingsDialog


class ProfileStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.temp.name) / "profiles.ini"), QSettings.Format.IniFormat)
        self.store = SettingsProfiles(self.settings)
        self.assertEqual(self.store.records, [])          # a fresh install offers only Default
        self.fav1 = self.store.create("Favorite 1", factory_snapshot())
        self.fav2 = self.store.create("Favorite 2", factory_snapshot())

    def tearDown(self):
        self.temp.cleanup()

    def test_default_and_two_favorites_are_independent_and_default_is_immutable(self):
        self.assertEqual([r["name"] for r in self.store.records], ["Favorite 1", "Favorite 2"])
        default = self.store.get("default")
        self.assertEqual(default["dcir"]["params"].rest_before_s, 600)
        self.assertEqual(default["dcir"]["params"].rest_after_s, 600)
        self.assertEqual(default["dcir"]["measurement_time_s"], 30.)     # Author's V1 default (was 10 s)
        changed = deepcopy(default)
        changed["explorer"]["point_tracking"] = False
        self.store.save(self.fav1, changed)
        self.assertTrue(self.store.get(self.fav2)["explorer"]["point_tracking"])
        self.assertTrue(self.store.get("default")["explorer"]["point_tracking"])
        with self.assertRaises(ValueError):
            self.store.save("default", changed)
        with self.assertRaises(ValueError):
            self.store.rename("default", "Other")

    def test_restart_retains_name_selection_axes_and_tuple_colour_keys(self):
        data = factory_snapshot()
        data["dqdv"]["view"]["colors"][(7, "charge")] = "#123456"
        data["dqdv"]["view"]["viewport"] = Viewport(Range(3.1, 4.2), Range(-20., 30.))
        data["dcir"]["params"] = replace(data["dcir"]["params"], pulse_s=12.)
        self.store.save(self.fav1, data)
        self.store.rename(self.fav1, "Short pulses")
        self.store.select(self.fav1)
        reloaded = SettingsProfiles(self.settings)
        self.assertEqual(reloaded.selected, self.fav1)
        self.assertEqual(reloaded.records[0]["name"], "Short pulses")
        self.assertEqual(reloaded.get(self.fav1), data)
        with self.assertRaises(ValueError):
            reloaded.rename(self.fav2, " short pulses ")
        ident = reloaded.create("Additional favourite", data)
        self.assertEqual(len(reloaded.records), 3)
        self.assertEqual(reloaded.get(ident), data)

    def test_bad_parameters_or_corrupt_storage_do_not_apply(self):
        invalid = factory_snapshot()
        invalid["dcir"]["params"] = replace(invalid["dcir"]["params"], pulse_s=-1.)
        with self.assertRaises(ValueError):
            self.store.save(self.fav1, invalid)
        self.settings.setValue(SettingsProfiles.KEY, '{"version":100}')
        restored = SettingsProfiles(self.settings)
        self.assertTrue(restored.load_error)
        self.assertEqual(restored.get("default"), factory_snapshot())

    def test_editable_starting_values_are_separate_from_profiles_and_default_restores_them(self):
        data = factory_snapshot()
        data["dcir"]["params"] = replace(data["dcir"]["params"], rest_before_s=620.)
        self.store.update_starting_preferences(data)
        reloaded = SettingsProfiles(self.settings)
        self.assertEqual(reloaded.starting_preferences()["dcir"]["params"].rest_before_s, 620.)
        self.assertEqual(reloaded.get("default")["dcir"]["params"].rest_before_s, 600.)
        reloaded.select("default")
        self.assertEqual(SettingsProfiles(self.settings).starting_preferences()["dcir"]["params"].rest_before_s, 600.)

    def test_deleting_the_profile_in_use_keeps_new_dataset_settings_and_survives_restart(self):
        data = factory_snapshot()
        data["dcir"]["params"] = replace(data["dcir"]["params"], rest_before_s=640.)
        self.store.save(self.fav1, data)
        self.store.select(self.fav1)
        with self.assertRaises(ValueError):
            self.store.delete("default")
        self.store.delete(self.fav1)
        self.store.delete(self.fav2)
        self.assertEqual(self.store.selected, "default")
        self.assertEqual(self.store.starting_preferences()["dcir"]["params"].rest_before_s, 640.)
        reloaded = SettingsProfiles(self.settings)
        self.assertFalse(reloaded.load_error)
        self.assertEqual(reloaded.records, [])
        self.assertEqual(reloaded.starting_preferences()["dcir"]["params"].rest_before_s, 640.)

    def test_applying_preferences_preserves_publication_sample_decisions_and_source(self):
        ds = Dataset("cell-id", "Cell", "/private/input.csv")
        result = ElabResult("published", 10, 4, 2, 1, ds.elab.params)
        ds.elab.result = result
        ds.elab.sample = replace(ds.elab.sample, active_mass_mg=3.14)
        ds.dcir.decisions["event"] = "accepted"
        data = factory_snapshot()
        data["elaboration"] = replace(data["elaboration"], rest_current_threshold_ma=1.)
        data["explorer"]["point_tracking"] = False
        apply_dataset_preferences(ds, data)
        self.assertIs(ds.elab.result, result)
        self.assertTrue(ds.elab.dirty)
        self.assertEqual(ds.elab.sample.active_mass_mg, 3.14)
        self.assertEqual(ds.dcir.decisions, {"event": "accepted"})
        self.assertEqual(ds.path, "/private/input.csv")
        self.assertFalse(ds.explorer.point_tracking)
        captured = snapshot(ds, CompareState(), "light", UnitPrefs())
        self.assertNotIn("source", captured)
        self.assertNotIn("sample", captured)
        self.assertNotIn("checked", captured["comparison"])
        self.assertNotIn("result", captured["dqdv"])
        validate_preferences(captured)


class SettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        load_fonts()

    def setUp(self):
        settings = QSettings("gregorithm", "gui")
        self.original_settings = {key: settings.value(key) for key in settings.allKeys()}
        self.temp = tempfile.TemporaryDirectory()
        self.window = MainWindow(DemoBackend(0), ThemeManager(self.app), demo=True)
        self.window.profiles = SettingsProfiles(QSettings(str(Path(self.temp.name) / "settings.ini"), QSettings.Format.IniFormat))
        self.fav1 = self.window.profiles.create("Favorite 1", factory_snapshot())
        ds = Dataset("one", "Cell one", "unused.csv")
        self.window.state.add(ds)
        self.window.refresh_all()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        settings = QSettings("gregorithm", "gui")
        for key in settings.allKeys():
            if key not in self.original_settings:
                settings.remove(key)
        for key, value in self.original_settings.items():
            settings.setValue(key, value)
        settings.sync()
        self.temp.cleanup()

    def dialog(self):
        w = self.window
        return SettingsDialog(w.theme, w.user_defaults, w, w.units, "one", "Cell one", owner=w)

    def test_save_rename_and_apply_profile_without_replacing_results_or_other_dataset(self):
        w = self.window
        ds = w.state.active
        other = Dataset("two", "Other", "other.csv")
        w.state.add(other)
        w.state.set_active(ds.id)
        ds.explorer.point_tracking = False
        ds.dcir.params = replace(ds.dcir.params, pulse_s=13.)
        w.profiles.save(self.fav1, w.capture_preferences())
        w.profiles.rename(self.fav1, "My settings")
        ds.explorer.point_tracking = True
        ds.dcir.params = replace(ds.dcir.params, pulse_s=99.)
        w.apply_preferences(self.fav1)
        self.assertFalse(ds.explorer.point_tracking)
        self.assertEqual(ds.dcir.params.pulse_s, 13.)
        self.assertEqual(other.dcir.params.pulse_s, 30.)
        self.assertEqual(ds.elab.run, Run.IDLE)
        self.assertEqual(ds.dcir.run, Run.IDLE)
        self.assertIsNone(ds.dqdv.result)
        self.assertEqual(SettingsProfiles(w.profiles.settings).selected, self.fav1)

    def test_busy_and_corrected_datasets_reject_unsafe_profile_changes(self):
        w = self.window
        ds = w.state.active
        ds.dqdv.run = Run.RUNNING
        with self.assertRaisesRegex(ValueError, "Wait for"):
            w.apply_preferences("default")
        ds.dqdv.run = Run.IDLE
        ds.correction.removed_at = "published correction removed"
        ds.dcir.params = replace(ds.dcir.params, pulse_s=13.)
        with self.assertRaisesRegex(ValueError, "Cycle Correction"):
            w.apply_preferences("default")
        self.assertEqual(ds.dcir.params.pulse_s, 13.)

    def test_dcir_form_edits_the_same_controller_and_default_profile_is_read_only(self):
        dialog = self.dialog()
        try:
            dialog.select_page("advanced")
            dialog.advanced_section.setCurrentIndex(dialog.advanced_section.findData(Section.DCIR.value))
            field = self.window.dcir_dock.preference_fields["rest_before_s"]
            field.textEdited.emit("rest_before_s", "620")
            self.assertEqual(self.window.state.active.dcir.params.rest_before_s, 620.)
            dialog.select_page("profiles")
            self.assertFalse(dialog.profile_save.isEnabled())
            self.assertFalse(dialog.profile_rename.isEnabled())
            dialog.profile_list.setCurrentRow(1)
            dialog._confirm = lambda *_args: True
            dialog._save_profile()
            self.assertEqual(self.window.profiles.get(self.fav1)["dcir"]["params"].rest_before_s, 620.)
        finally:
            dialog.reject()
            dialog.deleteLater()

    def test_profiles_are_discoverable_from_the_sidebar_card_on_every_page(self):
        dialog = self.dialog()
        try:
            self.assertEqual(dialog.current_page(), "appearance")
            self.assertFalse(dialog.profile_card.isHidden())
            self.assertEqual(dialog.profile_card_name.text(), "Profile: Default")
            labels = [dialog.nav.item(i).text() for i in range(dialog.nav.count())]
            self.assertEqual(labels, ["Appearance", "Units", "New Dataset Defaults", "Advanced Settings"])
            dialog.profile_card.click()
            self.assertEqual(dialog.current_page(), "profiles")
            self.assertTrue(dialog.profile_card.isChecked())
            dialog.nav.setCurrentRow(1)
            self.assertEqual(dialog.current_page(), "units")
            self.assertFalse(dialog.profile_card.isChecked())
        finally:
            dialog.reject()
            dialog.deleteLater()

    def test_changes_apply_immediately_and_invalid_defaults_are_never_committed(self):
        w = self.window
        dialog = self.dialog()
        original_theme = w.theme.mode
        try:
            target = "dark" if original_theme != "dark" else "light"
            dialog.theme_seg._btn[target].click()
            self.assertEqual(w.theme.mode, target)
            dialog._unit_chosen("time", "s")
            self.assertEqual(w.units.app_units.time, "s")
            saved = w.user_defaults.params
            dialog._typed("rest_current_threshold_ma", "abc")
            self.assertEqual(w.user_defaults.params, saved)
            with self.assertRaisesRegex(ValueError, "New Dataset Defaults"):
                dialog._capture_profile()
            dialog._typed("rest_current_threshold_ma", "2.5")
            self.assertEqual(w.user_defaults.params.rest_current_threshold_ma, 2.5)
        finally:
            dialog.reject()
            dialog.deleteLater()
            w.theme.set_mode(original_theme)
            w.units.restore_factory()
            w.user_defaults.set(saved)

    def test_new_use_and_delete_profile_from_the_dialog(self):
        from unittest.mock import patch
        w = self.window
        ds = w.state.active
        dialog = self.dialog()
        try:
            ds.explorer.point_tracking = False
            with patch("app.widgets.settings_dialog.QInputDialog.getText", return_value=("Instrument A", True)):
                dialog._new_profile()
            ident = dialog._profile_ident()
            self.assertEqual(w.profiles.get(ident)["explorer"]["point_tracking"], False)
            ds.explorer.point_tracking = True
            dialog._load_profile()
            self.assertFalse(ds.explorer.point_tracking)
            self.assertEqual(dialog.profile_card_name.text(), "Profile: Instrument A")
            self.assertIn("in use", dialog.profile_list.currentItem().text())
            dialog._confirm = lambda *_args: False
            dialog._delete_profile()
            self.assertIn(ident, [r["id"] for r in w.profiles.records])
            dialog._confirm = lambda *_args: True
            dialog._delete_profile()
            self.assertNotIn(ident, [r["id"] for r in w.profiles.records])
            self.assertEqual(dialog.profile_card_name.text(), "Profile: Default")
            dialog.profile_list.setCurrentRow(0)
            self.assertFalse(dialog.profile_delete.isEnabled())
            self.assertFalse(dialog.profile_save.isEnabled())
        finally:
            dialog.reject()
            dialog.deleteLater()

    def test_open_file_is_reachable_from_the_file_menu_and_the_tab_band(self):
        w = self.window
        file_menu = next(a.menu() for a in w.menuBar().actions() if a.text() == "File")
        open_menu = next(a.menu() for a in file_menu.actions() if a.menu() is not None)
        self.assertEqual(open_menu.title(), "Open File")
        self.assertEqual([a.text() for a in open_menu.actions()], ["Auto Mapping…", "Manual Mapping…"])
        plus = w.band.add_btn
        self.assertEqual(plus.accessibleName(), "Open File")
        self.assertEqual([a.text() for a in plus.menu().actions()], ["Auto Mapping…", "Manual Mapping…"])
        w._set_import_actions(False)
        self.assertFalse(plus.isEnabled())
        w._set_import_actions(True)
        self.assertTrue(plus.isEnabled())

    def test_profile_retains_dataset_units_and_axis_ranges_and_seeds_new_imports(self):
        w = self.window
        ds = w.state.active
        w.units.set_dataset_units(ds.id, UnitPrefs(time="s", current="A"))
        ds.explorer.viewport = Viewport(Range(20., 30.), Range(-2., 2.))
        ds.correction.threshold = 0.25
        w.profiles.save(self.fav1, w.capture_preferences())
        w.units.set_dataset_units(ds.id, None)
        ds.explorer.viewport = Viewport()
        ds.correction.threshold = 0.
        w.apply_preferences(self.fav1)
        self.assertEqual(w.units.units_for(ds.id).time, "s")
        self.assertEqual(w.units.units_for(ds.id).current, "A")
        self.assertEqual(ds.explorer.viewport, Viewport(Range(20., 30.), Range(-2., 2.)))
        self.assertEqual(ds.correction.threshold, 0.25)
        w.start_import("new.csv", "auto")
        for _ in range(100):
            if w._active_job is None:
                break
            loop = QEventLoop()
            QTimer.singleShot(10, loop.quit)
            loop.exec()
        self.assertIsNone(w._active_job)
        new = w.state.active
        self.assertNotEqual(new.id, ds.id)
        self.assertEqual(new.units_override, ds.units_override)
        self.assertEqual(new.explorer.viewport, ds.explorer.viewport)
        self.assertEqual(new.correction.threshold, 0.25)

    def test_dcir_defaults_are_keyboard_editable_without_a_dataset_and_survive_restart(self):
        w = self.window
        w.state.remove("one")
        w.refresh_all()
        dialog = SettingsDialog(w.theme, w.user_defaults, w, w.units, owner=w)
        try:
            dialog.select_page("advanced")
            dialog.advanced_section.setCurrentIndex(dialog.advanced_section.findData(Section.DCIR.value))
            dialog.show()
            self.app.processEvents()
            field = w.dcir_dock.preference_fields["rest_before_s"].edit
            self.assertTrue(field.isEnabled())
            field.setFocus()
            field.selectAll()
            QTest.keyClicks(field, "620")
            self.assertEqual(SettingsProfiles(w.profiles.settings).starting_preferences()["dcir"]["params"].rest_before_s, 620.)
            self.assertEqual(w.profiles.get("default")["dcir"]["params"].rest_before_s, 600.)
            pulse = w.dcir_dock.preference_fields["pulse_s"].edit
            pulse.setFocus()
            pulse.selectAll()
            QTest.keyClicks(pulse, "0")
            self.assertEqual(w.profiles.starting_preferences()["dcir"]["params"].pulse_s, 30.)
            with self.assertRaisesRegex(ValueError, "highlighted DCIR"):
                w.capture_preferences()
            pulse.selectAll()
            QTest.keyClicks(pulse, "35")
            dialog.select_page("profiles")
            dialog.profile_list.setCurrentRow(1)
            dialog._confirm = lambda *_args: True
            dialog._save_profile()
            self.assertEqual(w.profiles.get(self.fav1)["dcir"]["params"].rest_before_s, 620.)
            dialog._load_profile()
        finally:
            dialog.reject()
            dialog.deleteLater()
        w.start_import("configured.csv", "auto")
        for _ in range(100):
            if w._active_job is None:
                break
            loop = QEventLoop()
            QTimer.singleShot(10, loop.quit)
            loop.exec()
        self.assertIsNotNone(w.state.active)
        self.assertEqual(w.state.active.dcir.params.rest_before_s, 620.)

    def test_corrected_dataset_allows_pending_edits_but_still_blocks_detection(self):
        from unittest.mock import patch
        w = self.window
        ds = w.state.active
        ds.correction.removed_at = "correction-history"
        from app.dcir import PublishedResult
        publication = PublishedResult("published-result", (), (), 10.)
        ds.dcir.published = publication
        dialog = self.dialog()
        try:
            dialog.select_page("advanced")
            dialog.advanced_section.setCurrentIndex(dialog.advanced_section.findData(Section.DCIR.value))
            dialog.show()
            self.app.processEvents()
            field = w.dcir_dock.preference_fields["pulse_s"].edit
            self.assertTrue(field.isEnabled())
            field.setFocus()
            field.selectAll()
            QTest.keyClicks(field, "45")
            self.assertEqual(ds.dcir.params.pulse_s, 45.)
            notices = []
            w.dcir.notice.connect(lambda _ds, message: notices.append(message))
            with patch.object(w.backend, "detect_dcir", create=True) as detect:
                w.dcir.detect(ds.id)
                detect.assert_not_called()
            self.assertTrue(any("after Cycle Correction" in message for message in notices))
            self.assertEqual(ds.correction.removed_at, "correction-history")
            self.assertIs(ds.dcir.published, publication)
        finally:
            dialog.reject()
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
