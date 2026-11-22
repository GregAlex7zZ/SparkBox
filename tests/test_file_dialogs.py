"""Remembered folders: every export dialog opens in the folder of the last export, every Open dialog in the folder
of the last dataset loaded (Open dialog, drag and drop or re-map), across sessions."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QFileDialog

from app import file_dialogs as fd


class FileDialogFolderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        settings = QSettings("gregorithm", "gui")
        settings.remove(fd.IMPORT_KEY)
        settings.remove(fd.EXPORT_KEY)

    def tearDown(self):
        self.temp.cleanup()

    def _save(self, returned: str, suggested: str = "cell - dqdv.csv") -> str:
        with mock.patch.object(QFileDialog, "getSaveFileName", return_value=(returned, "")) as dialog:
            fd.save_export(None, "Export", suggested, "CSV (*.csv)")
        return dialog.call_args.args[2]

    def test_export_starts_in_home_then_in_last_export_folder(self):
        self.assertEqual(Path(self._save("")), Path.home() / "cell - dqdv.csv")
        first = self.root / "exports"
        first.mkdir()
        self._save(str(first / "a.csv"))
        # A different export (chart image) of another section opens where the table was saved.
        self.assertEqual(Path(self._save("", "cell - dQdV.png")), first / "cell - dQdV.png")

    def test_cancelled_export_keeps_the_remembered_folder(self):
        folder = self.root / "kept"
        folder.mkdir()
        self._save(str(folder / "a.csv"))
        self._save("")
        self.assertEqual(Path(self._save("")).parent, folder)

    def test_suggested_folder_part_is_replaced_and_missing_folder_falls_back_home(self):
        gone = self.root / "gone"
        gone.mkdir()
        self._save(str(gone / "a.csv"))
        gone.rmdir()
        self.assertEqual(Path(self._save("", "elsewhere/cell.png")), Path.home() / "cell.png")

    def test_dataset_exports_start_in_the_original_file_folder(self):
        """Exports of a dataset start where its original file is, even after another export elsewhere; without that
        folder (gone, or no dataset) the last export folder is used."""
        from types import SimpleNamespace
        source = self.root / "measurements"
        source.mkdir()
        dataset = SimpleNamespace(path=str(source / "cell.csv"))
        other = self.root / "other"
        other.mkdir()
        self._save(str(other / "a.csv"))
        with mock.patch.object(QFileDialog, "getSaveFileName", return_value=("", "")) as dialog:
            fd.save_export(None, "Export", "cell - dcir.csv", "CSV (*.csv)", folder=fd.dataset_folder(dataset))
        self.assertEqual(Path(dialog.call_args.args[2]), source / "cell - dcir.csv")
        self.assertEqual(Path(fd.export_start_folder(fd.dataset_folder(dataset))), source)
        self.assertIsNone(fd.dataset_folder(SimpleNamespace(path=str(self.root / "gone" / "cell.csv"))))
        self.assertIsNone(fd.dataset_folder(None))
        self.assertEqual(Path(fd.export_start_folder(None)), other)

    def test_open_uses_last_import_folder_and_drops_count_as_imports(self):
        with mock.patch.object(QFileDialog, "getOpenFileName", return_value=("", "")) as dialog:
            fd.open_dataset(None, "Open", "*.csv")
        self.assertEqual(Path(dialog.call_args.args[2]), Path.home())
        dropped = self.root / "data"
        dropped.mkdir()
        fd.remember_import(str(dropped / "cell.csv"))
        with mock.patch.object(QFileDialog, "getOpenFileName", return_value=("", "")) as dialog:
            fd.open_dataset(None, "Open", "*.csv")
        self.assertEqual(Path(dialog.call_args.args[2]), dropped)
        # Import and export folders are independent.
        self.assertEqual(Path(self._save("")).parent, Path.home())


if __name__ == "__main__":
    unittest.main()
