"""Product identity of the GUI: its name and the icon shown in the window, the taskbar and the dock.

The interface is called SparkBox. The scientific engine underneath keeps its own name,
gregorithm: text that refers to the engine, its results ("generations") or its Python package must keep saying
gregorithm. Only the product name changes. Internal identifiers that hold user data keep their former values on
purpose: the preferences key ``QSettings("gregorithm", "gui")`` and the results folder ``~/.gregorithm-gui`` (see
app.legacy_migration), so renaming the product does not lose settings or saved results.

Usage: call ``apply_identity(qt_app)`` right after creating the QApplication (run.py does). Every window then
inherits the icon; ``APP_NAME`` is the single place for the name used in titles, menus and dialogs.

Limits: when the GUI runs from ``python run.py`` the operating system sees the Python interpreter as the program.
Windows shows the icon in the taskbar only because the process gets its own AppUserModelID (set here, from
source only; the packaged SparkBox.exe uses its own executable identity); macOS shows
it in the Dock from the window icon, but the menu-bar name stays "Python" until the app is packaged as a bundle;
Linux desktops that group windows by ``.desktop`` file need an installed ``sparkbox.desktop`` to show the icon in
their launcher. A packaged build should also embed ``assets/icon/sparkbox.ico`` (Windows) or an ``.icns`` made from
``sparkbox.png`` (macOS) in its executable.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

APP_NAME = "SparkBox"
VERSION = "0.1.0"                         # also written into the Windows executable (tools/build_windows.py)
ENGINE_NAME = "gregorithm"
AUTHOR = "Alessandro Gregucci"
ABOUT_ICON_PX = 128                       # icon size in the About dialog
AI_NOTE = "Developed with the assistance of the AI coding tools Claude (Anthropic) and Codex (OpenAI)."
ICON_DIR = Path(__file__).resolve().parent.parent / "assets" / "icon"
ICON_PNG = ICON_DIR / "sparkbox.png"      # 1024 x 1024, transparent background
ICON_ICO = ICON_DIR / "sparkbox.ico"      # 16 ... 256 px, Windows
_APP_USER_MODEL_ID = "SparkBox.GUI"


def app_icon() -> QIcon:
    """The application icon: every size of the .ico on Windows (sharp small taskbar icons), the large PNG elsewhere.
    Returns an empty QIcon when the files are missing, so a damaged install still starts."""
    icon = QIcon()
    if sys.platform == "win32" and ICON_ICO.is_file():
        icon.addFile(str(ICON_ICO))
    if ICON_PNG.is_file():
        icon.addFile(str(ICON_PNG))
    return icon


def about_html() -> str:
    """Rich text of the About dialog: product, author, build, engine, the AI-assistance note and the open-source
    components with their licences (full notices in the LICENSES folder)."""
    return (f"<h2 style='margin:0'>{APP_NAME}</h2>"
            f"<p>Author: <b>{AUTHOR}</b><br>"
            f"Version {VERSION} (alpha)<br>"
            f"Scientific engine: {ENGINE_NAME}.</p>"
            f"<p>{AI_NOTE}</p>"
            "<p>Open-source components:<br>• Lucide icons — ISC licence<br>"
            "• Inter font — SIL Open Font License 1.1<br>• Qt for Python (PySide6) — LGPL-3.0<br>"
            "• pyqtgraph, gregorithm — MIT<br>• NumPy, SciPy — BSD-3-Clause<br>• PyArrow — Apache-2.0<br>"
            "• Python — PSF License<br><br>"
            "Full notices: see THIRD_PARTY_NOTICES.md and the LICENSES folder.</p>")


def _set_windows_app_id() -> None:
    """Give the process its own taskbar identity, so Windows shows the SparkBox icon and does not group the window
    under python.exe. Harmless when the call is unavailable."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass


def apply_identity(qt_app: QApplication) -> None:
    """Set the application name and icon on ``qt_app``. Call once, before any window is shown."""
    if sys.platform == "win32" and not getattr(sys, "frozen", False):
        # Only when run from source (python.exe): the packaged SparkBox.exe already has its own identity and icon,
        # and an explicit ID there makes the taskbar show a generic icon instead of the window icon.
        _set_windows_app_id()
    qt_app.setApplicationName(APP_NAME)
    qt_app.setOrganizationName(ENGINE_NAME)
    qt_app.setDesktopFileName("sparkbox")      # Wayland / X11: matches an installed sparkbox.desktop
    qt_app.setWindowIcon(app_icon())
