"""Regressions for the mascot next to the "SparkBox" brand (app.mascot).

Covers: manifest integrity and seamless transitions of the director (no frame jumps between clips, fast exit from
the desk loop), classification of user-facing messages, the observer's debounce and reactions, and the integration in
the main window: the mascot sits in the brand row, hides with the collapsed sidebar, reacts to real outcomes (a demo
import and processing, a failed import) and never alters what the application does.
"""
import os
import random
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from app.mascot.director import EVENTS, Director
from app.mascot.observer import FAILURE, SUCCESS, MascotObserver, classify_message
from app.mascot.widget import ASSET_DIR, Mascot, load_manifest

M = load_manifest()
CLIPS, NEUTRAL = M["clips"], M["neutral"]


def pump(ms: int = 30) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_for(cond, timeout: float = 20.0) -> None:
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            raise AssertionError("condition not met in time")
        pump(20)


class FakeMascot:
    def __init__(self):
        self.played: list[str] = []

    def play(self, name: str) -> None:
        self.played.append(name)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class ManifestAndDirector(unittest.TestCase):
    def test_every_frame_file_exists_and_durations_match(self):
        for f in M["frames"].values():
            self.assertTrue((ASSET_DIR / f["file"]).exists(), f["file"])
        for name, clip in CLIPS.items():
            self.assertEqual(len(clip["frames"]), len(clip["ms"]), name)
            self.assertTrue(all(ms > 0 for ms in clip["ms"]), name)
            for fid in clip["frames"]:
                self.assertIn(fid, M["frames"], name)

    def test_every_event_starts_and_ends_on_the_neutral_pose(self):
        for event, clips in EVENTS.items():
            self.assertEqual(CLIPS[clips[0]]["frames"][0], NEUTRAL, event)
            self.assertEqual(CLIPS[clips[-1]]["frames"][-1], NEUTRAL, event)
        rest = CLIPS["desk_loop"]["frames"][-1]
        self.assertEqual(CLIPS["desk_loop"]["frames"][0], rest)
        self.assertEqual(CLIPS["desk_entry"]["frames"][-1], rest)
        self.assertEqual(CLIPS["desk_exit"]["frames"][0], rest)

    def test_random_requests_never_jump_between_clips(self):
        rng = random.Random(7)
        d = Director(M, max_queue=3)
        allowed = {(CLIPS["lift"]["frames"][-1], CLIPS["lower"]["frames"][0]),
                   (CLIPS["restroom_entry"]["frames"][-1], CLIPS["restroom_exit"]["frames"][0])}
        prev_frame, prev_clip, requests, changes = None, None, 0, 0
        while requests < 600:
            if rng.random() < 0.08:
                d.request(rng.choice(list(EVENTS) + ["desk", "idle", "desk"]))
                requests += 1
            frame, _ms = d.next_frame()
            if prev_clip is not None and d.current != prev_clip:
                changes += 1
                if frame != prev_frame:
                    self.assertIn((prev_frame, frame), allowed, f"{prev_clip}->{d.current}")
            prev_frame, prev_clip = frame, d.current
        self.assertGreater(changes, 50)

    def test_an_event_leaves_the_desk_within_three_seconds(self):
        d = Director(M)
        while d.current != "desk_loop":
            d.next_frame()
        for _ in range(7):
            d.next_frame()
        d.request("explosion")
        waited = 0
        while d.current != "explosion":
            _frame, ms = d.next_frame()
            if d.current == "desk_loop":
                waited += ms
        self.assertLess(waited, 3000)

    def test_idle_command_holds_the_neutral_pose(self):
        d = Director(M, auto_desk=False)
        self.assertEqual(d.next_frame(), (NEUTRAL, None))
        ended = []
        d.on_event_end = ended.append
        d.request("thumbs_up")
        while True:
            frame, ms = d.next_frame()
            if ms is None:
                break
        self.assertEqual(frame, NEUTRAL)
        self.assertEqual(ended, ["thumbs_up"])
        self.assertTrue(d.idle)

    def test_bursts_do_not_build_a_backlog(self):
        d = Director(M, max_queue=2)
        for _ in range(10):
            d.request("explosion")
            d.request("thumbs_up")
        self.assertEqual(list(d.events), ["explosion", "thumbs_up"])
        with self.assertRaises(ValueError):
            d.request("dance")


class Classification(unittest.TestCase):
    def test_real_app_messages(self):
        cases = {
            "Imported a_cell": SUCCESS,
            "Processing finished: a_cell": SUCCESS,
            "Processing failed: a_cell": FAILURE,
            "Processing cancelled: a_cell": None,
            "Exported 120 cycles to C:/x.csv": SUCCESS,
            "Export cancelled. Nothing was written.": None,
            "Image saved: C:/x.png": SUCCESS,
            "Detection complete: 4 pulses found.": SUCCESS,
            "Calculated 12 DCIR measurements.": SUCCESS,
            "Could not read the pulse: boom": FAILURE,
            "Render All Points failed: boom": FAILURE,
            "12 results calculated · 3 technical errors": SUCCESS,
            "A late result was discarded; the current results are unchanged.": None,
            "Cancelled · previous results unchanged": None,
            "Ready": None,
            "Capacity is not available for this dataset; showing Data.": None,
            "Choose a file and import it now; asks only if columns are ambiguous": None,
            "": None,
        }
        for text, kind in cases.items():
            self.assertEqual(classify_message(text), kind, text)


class ObserverLogic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make(self, **kw):
        self.fake, self.clock = FakeMascot(), Clock()
        return MascotObserver(self.fake, rng=random.Random(1), clock=self.clock, **kw)

    def test_failure_explodes_and_success_celebrates(self):
        obs = self.make(battery_share=0.0)
        self.assertEqual(obs.on_message("Processing failed: x"), "explosion")
        self.clock.t += 10
        self.assertEqual(obs.on_message("Imported x"), "thumbs_up")
        self.assertEqual(self.fake.played, ["explosion", "thumbs_up"])

    def test_successes_mix_thumbs_up_and_battery(self):
        obs = self.make(battery_share=0.35)
        for _ in range(60):
            self.clock.t += 10
            obs.react(SUCCESS)
        self.assertEqual(set(self.fake.played), {"thumbs_up", "battery"})

    def test_one_outcome_reported_several_times_is_one_reaction(self):
        obs = self.make()
        obs.on_failure("ds", "disk full")                 # exportError
        obs.on_message("Could not save the file")       # status bar
        obs.react(FAILURE)                              # warning dialog
        self.assertEqual(self.fake.played, ["explosion"])
        obs.on_message("Exported 3 rows")                # success right after a failure: no celebration
        self.assertEqual(self.fake.played, ["explosion"])

    def test_gitt_error_reacts_only_when_it_appears(self):
        obs = self.make()
        self.assertIsNone(obs.gitt_changed("a", ""))
        self.assertEqual(obs.gitt_changed("a", "ValueError: bad"), "explosion")
        self.clock.t += 10
        self.assertIsNone(obs.gitt_changed("a", "ValueError: bad"))      # same error, later refresh

    def test_bathroom_break_every_twenty_animations(self):
        # Regression (2026-11-08): a bathroom break every 20 animations, the bathroom clips not counted.
        obs = self.make()
        for _ in range(19):
            self.assertIsNone(obs.on_clip_finished("desk_loop"))
        self.assertNotIn("break", self.fake.played)
        self.assertEqual(obs.on_clip_finished("thumbs_up"), "break")
        self.assertEqual(self.fake.played, ["break"])
        obs.on_clip_finished("restroom_entry")
        obs.on_clip_finished("restroom_exit")
        self.assertEqual(obs.clips_since_break, 0)
        for _ in range(20):
            obs.on_clip_finished("desk_loop")
        self.assertEqual(self.fake.played, ["break", "break"])

    def test_no_break_while_removed(self):
        obs = self.make()
        obs.enabled = False
        for _ in range(40):
            obs.on_clip_finished("desk_loop")
        self.assertEqual(self.fake.played, [])

    def test_real_mascot_clips_are_counted(self):
        m = Mascot(height=40)
        obs = MascotObserver(m, rng=random.Random(1), break_every=2)
        m.clipFinished.emit("desk_loop")
        self.assertEqual(obs.clips_since_break, 1)
        m.deleteLater()


class MascotWidget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_fixed_size_and_animates_without_resizing(self):
        m = Mascot(height=46)
        m.show()
        self.assertEqual((m.width(), m.height()), (Mascot.width_for_height(46), 46))
        m.play("thumbs_up")
        seen = set()
        end = time.monotonic() + 3
        while time.monotonic() < end and "thumbs_up" not in (m.director.current or ""):
            pump(30)
            seen.add(m.frame)
        self.assertEqual(m.director.current_event, "thumbs_up")
        self.assertEqual((m.width(), m.height()), (Mascot.width_for_height(46), 46))
        img = m.grab().toImage()
        self.assertFalse(img.isNull())
        m.close()

    def test_hidden_mascot_does_not_tick(self):
        m = Mascot(height=46)
        m.show()
        pump(50)
        m.hide()
        self.assertFalse(m._timer.isActive())
        m.close()

    def test_height_for_width_is_the_inverse_of_width_for_height(self):
        for width in range(30, 120):
            h = Mascot.height_for_width(width)
            self.assertLessEqual(Mascot.width_for_height(h), width)
            self.assertGreater(Mascot.width_for_height(h + 1), width)

    def test_unknown_event_is_rejected(self):
        m = Mascot(height=46)
        with self.assertRaises(ValueError):
            m.play("dance")
        m.close()


class MainWindowIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.demo_backend import DemoBackend
        from app.main_window import MainWindow
        from app.theme import ThemeManager
        from app.fonts import load_fonts
        cls.app = QApplication.instance() or QApplication([])
        load_fonts()                                      # the real brand font: placement depends on its width
        cls.theme = ThemeManager(cls.app)
        cls.theme.apply(force=True)
        cls.w = MainWindow(DemoBackend(0.0), cls.theme, demo=True)
        cls.w.show()
        cls.fake = FakeMascot()
        cls.w.mascot_observer.mascot = cls.fake           # record reactions instead of animating them
        cls.w.mascot_observer.debounce_s = 0.0

    @classmethod
    def tearDownClass(cls):
        cls.w.close()
        pump()

    def setUp(self):
        self.fake.played.clear()

    def test_mascot_sits_next_to_the_brand_and_replaces_it_when_collapsed(self):
        w = self.w
        w.nav.set_collapsed(False, user=True)
        pump()
        self.assertTrue(w.mascot.isVisible())
        self.assertEqual(w.mascot.parent(), w.brand.parent())
        self.assertGreaterEqual(w.mascot.geometry().left(), w.brand.geometry().left())
        self.assertLessEqual(w.mascot.geometry().right(), w.left.width())
        # larger than the first 46 px version, never over the brand text, resting on the brand row's bottom
        self.assertGreater(w.mascot.height(), 46)
        self.assertLessEqual(w.mascot.height(), w.brand.height())
        self.assertGreater(w.mascot.x(), w.mascot_slot.text_right())
        self.assertEqual(w.mascot.geometry().bottom(), w.brand.geometry().bottom())
        w.nav.set_collapsed(True, user=True)                       # mascot only, centred
        pump()
        self.assertTrue(w.mascot.isVisible())
        self.assertEqual(w.brand.text(), "")
        self.assertEqual(w.brand.accessibleName(), "SparkBox")
        centre = w.mascot.geometry().center().x()
        self.assertLessEqual(abs(centre - w.left.width() / 2), 1)
        self.assertGreaterEqual(w.mascot.x(), 0)
        self.assertLessEqual(w.mascot.geometry().right(), w.left.width())
        self.assertEqual(w.mascot.geometry().bottom(), w.brand.geometry().bottom())
        w.set_mascot_enabled(False)                                # mascot off: the letter g comes back
        pump()
        self.assertFalse(w.mascot.isVisible())
        self.assertEqual(w.brand.text(), "S")
        w.set_mascot_enabled(True)
        pump()
        self.assertTrue(w.mascot.isVisible())
        self.assertEqual(w.brand.text(), "")
        w.nav.set_collapsed(False, user=True)
        pump()
        self.assertTrue(w.mascot.isVisible())
        self.assertEqual(w.brand.text(), "SparkBox")
        self.assertGreater(w.mascot.x(), w.mascot_slot.text_right())

    def test_import_and_processing_are_celebrated(self):
        w = self.w
        w.start_import("a_cell.csv", "auto")
        wait_for(lambda: w._active_job is None)
        self.assertTrue(self.fake.played)
        self.assertTrue(set(self.fake.played) <= {"thumbs_up", "battery"})
        ds = w.state.datasets[-1]
        self.fake.played.clear()
        w.elab.process(ds.id)
        wait_for(lambda: ds.elab.has_result)
        pump(100)
        self.assertTrue(self.fake.played)
        self.assertNotIn("explosion", self.fake.played)

    def test_warning_dialog_explodes(self):
        box = QMessageBox(QMessageBox.Icon.Warning, "Import Failed", "Could not import x.", parent=self.w)
        QTimer.singleShot(0, lambda: self.w.mascot_observer.on_window_activated())
        QTimer.singleShot(50, box.reject)
        box.exec()
        self.assertIn("explosion", self.fake.played)

    def test_question_dialog_is_not_a_failure(self):
        box = QMessageBox(QMessageBox.Icon.Question, "Close dataset", "Close?", parent=self.w)
        QTimer.singleShot(0, lambda: self.w.mascot_observer.on_window_activated())
        QTimer.singleShot(50, box.reject)
        box.exec()
        self.assertNotIn("explosion", self.fake.played)


class RemoveAndRestore(unittest.TestCase):
    """Regression (2026-11-08): the mascot can be removed (right-click › Remove Mascot or Settings › Appearance) and
    restored from Settings; the choice survives a restart, and a removed mascot reacts to nothing."""

    @classmethod
    def setUpClass(cls):
        from app.demo_backend import DemoBackend
        from app.main_window import MainWindow
        from app.theme import ThemeManager
        from app.fonts import load_fonts
        cls.app = QApplication.instance() or QApplication([])
        load_fonts()
        cls.theme = ThemeManager(cls.app)
        cls.theme.apply(force=True)
        cls.MainWindow, cls.DemoBackend = MainWindow, DemoBackend

    def window(self):
        w = self.MainWindow(self.DemoBackend(0.0), self.theme, demo=True)
        w.show()
        w.nav.set_collapsed(False, user=True)
        pump()
        self.addCleanup(lambda: (w.close(), pump()))
        return w

    def tearDown(self):
        from PySide6.QtCore import QSettings
        QSettings("gregorithm", "gui").remove("mascot/enabled")

    def test_context_menu_offers_remove(self):
        w = self.window()
        self.assertTrue(w.mascot.isVisible())
        w.mascot.removeRequested.emit()                  # what "Remove Mascot" in the right-click menu emits
        pump()
        self.assertFalse(w.mascot.isVisible())
        self.assertFalse(w.mascot_observer.enabled)
        self.assertIsNone(w.mascot_observer.react(FAILURE))

    def test_removal_is_remembered_and_settings_restore_it(self):
        from app.widgets.settings_dialog import SettingsDialog
        w = self.window()
        w.set_mascot_enabled(False)
        w.close()
        pump()
        w2 = self.window()                               # next launch
        self.assertFalse(w2.mascot_enabled)
        self.assertFalse(w2.mascot.isVisible())
        w2.nav.set_collapsed(True, user=True)            # expanding the sidebar must not bring it back
        w2.nav.set_collapsed(False, user=True)
        pump()
        self.assertFalse(w2.mascot.isVisible())
        dlg = SettingsDialog(self.theme, w2.user_defaults, w2, units=w2.units, page="appearance", owner=w2)
        self.addCleanup(dlg.deleteLater)
        self.assertIsNotNone(dlg.mascot_switch)
        self.assertFalse(dlg.mascot_switch.isChecked())
        dlg.mascot_switch.click()
        pump()
        self.assertTrue(w2.mascot.isVisible())
        self.assertTrue(w2.mascot_observer.enabled)
        w2.close()
        pump()
        self.assertTrue(self.window().mascot_enabled)

    def test_menu_has_remove_entry(self):
        from PySide6.QtWidgets import QMenu
        w = self.window()
        seen = []

        def grab():
            menu = next((m for m in QApplication.topLevelWidgets() if isinstance(m, QMenu) and m.isVisible()), None)
            if menu is not None:
                seen.extend(a.text() for a in menu.actions())
                menu.close()
        QTimer.singleShot(100, grab)
        from PySide6.QtGui import QContextMenuEvent
        from PySide6.QtCore import QPoint
        w.mascot.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5),
                                                    w.mascot.mapToGlobal(QPoint(5, 5))))
        self.assertIn("Remove Mascot", seen)


if __name__ == "__main__":
    unittest.main()
