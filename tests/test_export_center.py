"""Export Center: availability of every section's export, file naming without overwriting, the Export button in
the section list, and a locked export that explains which section must be run first."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.export_center import export_items, free_path


def dataset(elab=False, capacity=False, dcir=False, dqdv=False):
    """Minimal stand-in for state.Dataset: only the result fields the Export Center reads."""
    return SimpleNamespace(elab=SimpleNamespace(result=object() if elab else None),
                           capacity=SimpleNamespace(result=object() if capacity else None),
                           dcir=SimpleNamespace(published=object() if dcir else None),
                           dqdv=SimpleNamespace(result=object() if dqdv else None))


class Availability(unittest.TestCase):
    def test_only_sections_with_a_result_can_be_exported(self):
        items = {i.key: i for i in export_items(dataset(elab=True, dcir=True), gitt_ready=False)}
        self.assertEqual(list(items), ["elab_points", "elab_segments", "capacity", "dcir", "dqdv", "gitt_ici"])
        self.assertTrue(items["elab_points"].available and items["elab_segments"].available)
        self.assertTrue(items["dcir"].available)
        for key in ("capacity", "dqdv", "gitt_ici"):
            self.assertFalse(items[key].available)
        self.assertIn("Run Capacity first", items["capacity"].reason)
        self.assertIn("Apply & Calculate", export_items(dataset(elab=True), False)[3].reason)
        self.assertIn("accepted diffusion coefficient", items["gitt_ici"].reason)

    def test_without_elaboration_every_reason_points_to_data_elaboration(self):
        for item in export_items(dataset(), gitt_ready=False):
            self.assertFalse(item.available)
            self.assertTrue(item.reason.startswith("Run Data Elaboration first"))
        self.assertTrue(all(not i.available for i in export_items(None, False)))

    def test_gitt_follows_accepted_results(self):
        item = export_items(dataset(elab=True), gitt_ready=True)[-1]
        self.assertTrue(item.available)


class Naming(unittest.TestCase):
    def test_free_path_never_reuses_an_existing_or_reserved_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.assertEqual(free_path(folder, "cell - dcir", ".csv").name, "cell - dcir.csv")
            (folder / "cell - dcir.csv").write_text("x")
            self.assertEqual(free_path(folder, "cell - dcir", ".csv").name, "cell - dcir (2).csv")
            taken = {str(folder / "cell - dcir (2).csv")}
            self.assertEqual(free_path(folder, "cell - dcir", ".csv", taken).name, "cell - dcir (3).csv")
            (folder / "cell - gitt-ici - charge.csv").write_text("x")    # GITT writes per-branch files
            self.assertEqual(free_path(folder, "cell - gitt-ici", ".csv").name, "cell - gitt-ici (2).csv")


def _delete(obj) -> None:
    """Destroy a parentless Qt object now: left to the garbage collector at interpreter exit, it could outlive the
    QApplication and crash the test process after every test had passed."""
    import shiboken6
    if shiboken6.isValid(obj):
        shiboken6.delete(obj)


class ExportButton(unittest.TestCase):
    """The Export button sits at the bottom of the section list and opens the Export Center."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_nav_has_an_export_button_enabled_with_a_dataset(self):
        from app.theme import ThemeManager
        from app.widgets.nav import NavSidebar
        nav = NavSidebar(ThemeManager(self.app))
        self.addCleanup(_delete, nav)
        fired = []
        nav.exportRequested.connect(lambda: fired.append(True))
        self.assertFalse(nav.export_btn.isEnabled())
        nav.set_export_enabled(True)
        self.assertTrue(nav.export_btn.isEnabled())
        self.assertFalse(nav.export_btn.isCheckable())                 # an action, not a section
        nav.export_btn.click()
        self.assertEqual(fired, [True])
        nav.set_collapsed(True)
        self.assertEqual(nav.export_btn.text(), "")

    def test_locked_row_explains_what_to_run(self):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from app.export_center import ExportItem
        from app.theme import ThemeManager
        from app.widgets.export_center import ExportCenterDialog

        from PySide6.QtCore import QObject, Signal

        class FakeController(QObject):
            """Export Center controller stand-in: fixed rows, never running."""
            progress = Signal(str)
            finished = Signal(object)
            running = False

            def items(self, _ds):
                return [ExportItem("elab_points", "Data Elaboration", "Elaborated data", "elaborated", True),
                        ExportItem("capacity", "Capacity", "Capacity table", "capacity", False,
                                   "Run Capacity first (Capacity › Analyze).")]

        ctl = FakeController()
        dlg = ExportCenterDialog(None, ThemeManager(self.app), ctl, "ds", "cell")
        self.addCleanup(_delete, ctl)
        self.addCleanup(_delete, dlg)                                  # deleted before the controller it listens to
        available, locked = dlg.rows
        self.assertTrue(available.box.isChecked())
        self.assertFalse(locked.box.isEnabled())
        self.assertEqual(dlg.export_btn.text(), "Export 1 file")
        self.assertTrue(dlg.notice.isHidden())
        press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(5, 5), Qt.MouseButton.LeftButton,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        locked.mousePressEvent(press)
        self.assertFalse(dlg.notice.isHidden())
        self.assertIn("Run Capacity first", dlg.notice_text.text())
        self.assertEqual(dlg.notice_go.text(), "Go to Capacity")
        go = []
        dlg.goSectionRequested.connect(go.append)
        dlg.notice_go.click()
        self.assertEqual(go, ["capacity"])
        available.mousePressEvent(press)                                 # a click on an available row toggles it
        self.assertFalse(available.box.isChecked())
        self.assertFalse(dlg.export_btn.isEnabled())


if __name__ == "__main__":
    unittest.main()
