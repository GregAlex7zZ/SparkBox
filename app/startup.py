"""Startup helpers without Qt: one running SparkBox per user, and progress messages for the startup banner.

They run before any heavy import (run.py loads gregorithm before PySide on purpose), so a second start is detected
in milliseconds and the banner shows each step as it happens.

Single instance. ``claim_instance()`` takes a per-user lock: a named mutex on Windows, an ``fcntl`` lock on a file in
the temporary folder elsewhere. Both are released by the operating system when the process ends, also after a
crash. A process that cannot take it is a second start: it asks the running one to show its window
(``app.single_instance.send_activate``) and exits.

Startup messages. The Windows launcher (``packaging/SparkBoxLauncher.cs``) shows a banner the moment the user
clicks, then starts the application with two environment variables: ``SPARKBOX_STARTUP_STATUS`` (a file the
launcher reads to show the current step, e.g. "Loading gregorithm…") and ``SPARKBOX_STARTUP_READY`` (a file whose
creation tells the launcher that the main window is on screen, so it closes the banner). ``report()`` and
``ready()`` write them; without the variables (``python run.py``) ``report`` prints to the console, if any.

Limits: the lock is per user and per machine, so two users of one computer can each run SparkBox. Status files are
best effort: a failure to write one never stops the application.
"""
from __future__ import annotations

import getpass
import hashlib
import os
import sys
import tempfile

STATUS_ENV = "SPARKBOX_STARTUP_STATUS"
READY_ENV = "SPARKBOX_STARTUP_READY"
_held: list[object] = []                       # keeps the lock handle alive for the life of the process


def report(message: str) -> None:
    """Show ``message`` as the current startup step: in the launcher banner, or on the console when run from source."""
    path = os.environ.get(STATUS_ENV)
    if path:
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(message)
            os.replace(tmp, path)               # atomic: the launcher never reads a half-written message
        except OSError:
            pass
    elif sys.stdout is not None:
        print(message, flush=True)


def ready() -> None:
    """Tell the launcher that the main window is visible, so it closes the startup banner (only the first call acts)."""
    path = os.environ.pop(READY_ENV, None)
    if path:
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("ready")
        except OSError:
            pass


def channel_name() -> str:
    """Per-user name shared by the lock, the activation channel and the launcher (which computes the same name)."""
    try:
        user = getpass.getuser()
    except Exception:                          # no user name available (rare): one instance per machine
        user = "user"
    return "SparkBox-" + hashlib.sha256(user.lower().encode("utf-8")).hexdigest()[:16]


def claim_instance(name: str | None = None) -> bool:
    """Take the per-user single-instance lock; False when another SparkBox already holds it."""
    name = name or channel_name()
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, "Local\\" + name)
        if not handle:
            return True                        # cannot tell: never block the start
        if ctypes.get_last_error() == 183:     # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(ctypes.c_void_p(handle))
            return False
        _held.append(handle)
        return True
    import fcntl
    handle = open(os.path.join(tempfile.gettempdir(), name + ".lock"), "a+")  # noqa: SIM115 (kept open on purpose)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _held.append(handle)
    return True
