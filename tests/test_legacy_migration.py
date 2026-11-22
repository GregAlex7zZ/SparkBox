"""Migration from the former name mathbatt: preferences and the results folder survive the rename to gregorithm-gui,
are never overwritten, and the old preferences stay in place. Settings are isolated by conftest (INI files in a
temporary folder), results a temporary folder."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from app.legacy_migration import (LEGACY_RESULTS_DIR, LEGACY_SETTINGS, RESULTS_DIR, SETTINGS, migrate_settings,
                                  results_root)


@pytest.fixture
def tmp_path():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        yield Path(directory)


def _clear() -> None:
    for org_app in (SETTINGS, LEGACY_SETTINGS):
        s = QSettings(*org_app)
        s.clear()
        s.sync()


def test_old_preferences_are_copied_once_and_kept() -> None:
    _clear()
    old = QSettings(*LEGACY_SETTINGS)
    old.setValue("appearance", "system")
    old.setValue("units/capacity", "mAh")
    old.sync()
    assert migrate_settings() == 2
    new = QSettings(*SETTINGS)
    assert new.value("appearance") == "system" and new.value("units/capacity") == "mAh"
    assert QSettings(*LEGACY_SETTINGS).value("appearance") == "system"                  # old keys untouched
    new.setValue("appearance", "dark")
    new.sync()
    assert migrate_settings() == 0                                                       # never overwrites
    assert QSettings(*SETTINGS).value("appearance") == "dark"
    _clear()


def test_nothing_to_migrate_on_a_new_computer() -> None:
    _clear()
    assert migrate_settings() == 0
    assert QSettings(*SETTINGS).allKeys() == []


def test_results_folder_is_moved_to_the_new_name(tmp_path) -> None:
    (tmp_path / LEGACY_RESULTS_DIR / "results").mkdir(parents=True)
    (tmp_path / LEGACY_RESULTS_DIR / "results" / "store.bin").write_bytes(b"x")
    root = results_root(tmp_path)
    assert root == tmp_path / RESULTS_DIR / "results"
    assert (root / "store.bin").read_bytes() == b"x" and not (tmp_path / LEGACY_RESULTS_DIR).exists()


def test_existing_new_folder_is_used_and_old_one_left_alone(tmp_path) -> None:
    (tmp_path / LEGACY_RESULTS_DIR).mkdir()
    (tmp_path / RESULTS_DIR).mkdir()
    assert results_root(tmp_path) == tmp_path / RESULTS_DIR / "results"
    assert (tmp_path / LEGACY_RESULTS_DIR).exists()
    assert results_root(tmp_path / "empty") == tmp_path / "empty" / RESULTS_DIR / "results"
