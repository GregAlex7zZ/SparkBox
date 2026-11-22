"""How the application ends: closing the main window always ends the whole process.

MainWindow.closeEvent hides the window at once, cancels every job and waits for the workers at most SHUTDOWN_WAIT_MS.
``finish_process`` then ends the process from run.py, after the Qt event loop: normal interpreter and Qt teardown would
wait for a worker still inside a long non-cooperative core call (QThreadPool's destructor waits with no limit), so the
process could stay alive, invisible, for as long as that call lasts.

Windows: os._exit goes through ExitProcess, which notifies every loaded DLL while workers still run, and crashes
there (access violation); TerminateProcess ends the process without that step.

Usage: ``finish_process(qt_app.exec())`` inside the entry point, while its QApplication is alive (destroying it waits
for the workers with no limit); never from a library or a test process.
Limits: nothing runs after it (no atexit handlers, no object destructors), so preferences are flushed here first.
Results stay consistent because every store and export is written to a temporary file and moved into place when
complete; a worker stopped by the exit can leave only a ``.tmp`` file behind, never a partial result.
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from typing import NoReturn

from PySide6.QtCore import QSettings

from .legacy_migration import SETTINGS

SHUTDOWN_WAIT_MS = 10_000          # most workers stop at their next checkpoint, well within this time


def finish_process(code: int) -> NoReturn:
    """Flush the preferences and the console streams, then end the process with ``code``, whatever is still running."""
    QSettings(*SETTINGS).sync()
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:                       # None under pythonw (no console)
            try:
                stream.flush()
            except (OSError, ValueError):            # a closed console must not prevent the exit
                pass
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel32.TerminateProcess(kernel32.GetCurrentProcess(), code)
    os._exit(code)                                   # other systems, or TerminateProcess refused
