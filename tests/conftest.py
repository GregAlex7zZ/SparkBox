"""Test isolation shared by every test module.

The app stores its preferences with ``QSettings("gregorithm", "gui")`` (appearance, units, dataset defaults, profiles),
which on Windows is the user's registry. Tests change those preferences (theme mode, units, profiles), so without
isolation a test run overwrites the operator's real settings. ``QSettings(organization, application)`` always uses
the native format, whatever ``setDefaultFormat`` says, so this module replaces ``PySide6.QtCore.QSettings`` before
any app module is imported: every ``QSettings(organization, application)`` then reads and writes an INI file in a
temporary folder for the whole test session, and the real preferences are never touched. The app.standby gate is
reset before every test (see _standby_released).
Limits: a QSettings built with an explicit file path or format is left as is (it already names its own storage).
"""
from __future__ import annotations

import os
import tempfile

import PySide6.QtCore as QtCore

_SETTINGS_DIR = tempfile.TemporaryDirectory(prefix="gregorithm-test-settings-", ignore_cleanup_errors=True)
_RealSettings = QtCore.QSettings


class _IsolatedSettings(_RealSettings):
    """QSettings whose (organization, application) storage is an INI file under the session's temporary folder."""

    def __init__(self, *args, **kwargs):
        if args and all(isinstance(a, str) for a in args[:2]) and len(args) <= 2 and not kwargs \
                and not (len(args) == 2 and isinstance(args[1], _RealSettings.Format)):
            organization = args[0] or "default"
            application = args[1] if len(args) > 1 and args[1] else organization
            path = os.path.join(_SETTINGS_DIR.name, organization, f"{application}.ini")
            super().__init__(path, _RealSettings.Format.IniFormat)
        else:
            super().__init__(*args, **kwargs)


QtCore.QSettings = _IsolatedSettings


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _standby_released():
    """Every test starts with no dataset on standby: app.standby.STANDBY is one gate for the whole process, and a
    window of an earlier test must not pause the calculations of the next one."""
    from app.standby import STANDBY
    STANDBY.reset()
    yield
