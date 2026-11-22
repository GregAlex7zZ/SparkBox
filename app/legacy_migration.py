"""One-time migration from the GUI's former name, mathbatt, to gregorithm-gui.

Two things carried the old name on the operator's computer:
- the preferences, stored with ``QSettings("mathbatt", "gui")`` (on Windows the registry key
  HKCU\\Software\\mathbatt\\gui): appearance, units, dataset defaults, profiles, sidebar state;
- the results store, the folder ``~/.mathbatt-gui`` (published elaborations and analyses).

``migrate_settings`` copies every old preference into ``QSettings("gregorithm", "gui")`` when the new store is still
empty; the old keys are left untouched, so going back to an older build loses nothing. ``results_root`` moves the old
results folder to the new name when only the old one exists (a rename on the same drive: instant, nothing copied);
if the move fails (folder in use, permissions) it keeps using the old folder rather than starting empty.
Limits: both run at start-up, before any window reads a preference; a new store that already has keys is never
overwritten, and two folders that both exist are never merged (the new one is used).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings

LEGACY_SETTINGS = ("mathbatt", "gui")
SETTINGS = ("gregorithm", "gui")
LEGACY_RESULTS_DIR = ".mathbatt-gui"
RESULTS_DIR = ".gregorithm-gui"


def migrate_settings() -> int:
    """Copy the preferences saved under the former name into the new store if it is empty; returns how many keys
    were copied (0 when there was nothing to do)."""
    new, old = QSettings(*SETTINGS), QSettings(*LEGACY_SETTINGS)
    if new.allKeys() or not old.allKeys():
        return 0
    keys = old.allKeys()
    for key in keys:
        new.setValue(key, old.value(key))
    new.sync()
    return len(keys)


def results_root(home: Path | None = None) -> Path:
    """Folder of the results store under the new name, moving the former ``~/.mathbatt-gui`` there when only the old
    one exists; the old folder is returned when the move fails."""
    home = Path.home() if home is None else home
    new, old = home / RESULTS_DIR, home / LEGACY_RESULTS_DIR
    if old.is_dir() and not new.exists():
        try:
            old.rename(new)
        except OSError:
            return old / "results"
    return new / "results"
