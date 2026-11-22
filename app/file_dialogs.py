"""File dialogs that remember the last folder used.

Two folders are remembered across sessions in the application preferences (``QSettings("gregorithm", "gui")``):

* the **import folder** - the folder of the last dataset loaded, whether chosen in the Open dialog, dropped on the
  Data page or re-mapped;
* the **export folder** - the folder of the last file saved by any export. Exports of one dataset start instead
  in the folder of that dataset's original file (``folder=dataset_folder(dataset)``); the remembered export folder
  is used when there is no such folder (Comparison, or the original file's folder no longer exists).

Usage::

    path = open_dataset(parent, "Open battery dataset", SUPPORTED_FILTER)
    path = save_export(parent, "Export dQ/dV table", "cell - dqdv.csv", "CSV (*.csv)")

Both return ``""`` when the user cancels; a cancelled dialog never changes the remembered folder. A remembered folder
that no longer exists (unplugged drive, deleted folder) is ignored, and the dialog opens in the user's home folder.
The suggested export name keeps only its file name: any folder part is replaced by the starting folder.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QFileDialog, QWidget

IMPORT_KEY = "dialogs/lastImportDir"
EXPORT_KEY = "dialogs/lastExportDir"


def _settings() -> QSettings:
    return QSettings("gregorithm", "gui")


def last_dir(key: str) -> str:
    """The remembered folder for ``key`` if it still exists, otherwise the user's home folder."""
    value = _settings().value(key, "")
    folder = Path(str(value)) if value else None
    if folder is not None and folder.is_dir():
        return str(folder)
    return str(Path.home())


def remember(key: str, file_path: str) -> None:
    """Store the folder that contains ``file_path`` under ``key``. Empty paths are ignored."""
    if not file_path:
        return
    folder = Path(file_path).expanduser().resolve().parent
    settings = _settings()
    settings.setValue(key, str(folder))
    settings.sync()


def remember_import(file_path: str) -> None:
    """Record the folder of a dataset that is being loaded (Open dialog, drag and drop or re-map)."""
    remember(IMPORT_KEY, file_path)


def open_dataset(parent: QWidget | None, caption: str, file_filter: str) -> str:
    """Open dialog that starts in the last import folder; the choice is remembered on acceptance."""
    path, _ = QFileDialog.getOpenFileName(parent, caption, last_dir(IMPORT_KEY), file_filter)
    if path:
        remember_import(path)
    return path


def dataset_folder(dataset) -> str | None:
    """Folder of the original file of ``dataset`` (state.Dataset or None), or None when unknown or gone: the
    default folder of that dataset's exports."""
    path = getattr(dataset, "path", "") if dataset is not None else ""
    if not path:
        return None
    folder = Path(path).expanduser().parent
    return str(folder) if folder.is_dir() else None


def export_start_folder(folder: str | None = None) -> str:
    """Where an export dialog starts: ``folder`` (the original file's folder) when it exists, else the last export
    folder."""
    if folder and Path(folder).is_dir():
        return str(folder)
    return last_dir(EXPORT_KEY)


def save_export(parent: QWidget | None, caption: str, suggested_name: str, file_filter: str,
                folder: str | None = None) -> str:
    """Save dialog for every export: starts in ``folder`` (pass dataset_folder(dataset): the folder of the original
    file) or, without one, in the last export folder, with ``suggested_name`` pre-filled; remembers the chosen folder
    on acceptance."""
    start = str(Path(export_start_folder(folder)) / Path(suggested_name).name)
    path, _ = QFileDialog.getSaveFileName(parent, caption, start, file_filter)
    if path:
        remember(EXPORT_KEY, path)
    return path
