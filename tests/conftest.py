"""Test isolation shared by every test module.

The app stores its preferences with ``QSettings("gregorithm", "gui")`` (appearance, units, dataset defaults, profiles),
which on Windows is the user's registry. Tests change those preferences (theme mode, units, profiles), so without
isolation a test run overwrites the operator's real settings. ``QSettings(organization, application)`` always uses
the native format, whatever ``setDefaultFormat`` says, so this module replaces ``PySide6.QtCore.QSettings`` before
any app module is imported: every ``QSettings(organization, application)`` then reads and writes an INI file in a
temporary folder for the whole test session, and the real preferences are never touched.
Limits: a QSettings built with an explicit file path or format is left as is (it already names its own storage).
"""
from __future__ import annotations

import os
import tempfile

import PySide6.QtCore as QtCore
import pytest

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


_exit_status = 0


def pytest_sessionfinish(session, exitstatus):
    """Remember the final exit status for ``pytest_unconfigure``."""
    global _exit_status
    _exit_status = int(exitstatus)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    """End a Qt test process without interpreter finalization.

    Tests close windows and charts but rarely delete them; at interpreter exit Python and Qt then destroy the remaining
    widgets, pyqtgraph scenes and the QApplication in no fixed order, which sometimes crashes the process with a
    segmentation fault (exit -11) after every test passed (seen on Linux with Python 3.12). Once pytest has written its
    report nothing is left to do, so when Qt widgets were loaded the process exits right here with pytest's status.
    Limits: atexit handlers do not run; the temporary settings folder is removed explicitly.
    """
    import sys

    if "PySide6.QtWidgets" not in sys.modules:
        return
    _SETTINGS_DIR.cleanup()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_exit_status)
