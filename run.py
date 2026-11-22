"""SparkBox entry point.  python run.py          -> production gregorithm backend (the scientific engine)
                          python run.py --demo   -> synthetic demo backend (clearly labelled, never for production)

Only one SparkBox runs per user: a second start brings the open window to the front and exits (app.startup,
app.single_instance). Each startup step is reported to the Windows launcher's banner (app.startup.report)."""
import os
import sys
from typing import Callable

from app.startup import channel_name, claim_instance, ready, report


def _hand_over_to_running_instance() -> int:
    """Second start: let the running SparkBox take the foreground and show itself; this process opens nothing."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)    # ASFW_ANY: the click belongs to this process
    from app.single_instance import send_activate_with_retry
    send_activate_with_retry(channel_name())
    return 0


if __name__ == "__main__":
    if not claim_instance():
        sys.exit(_hand_over_to_running_instance())
    report("Loading gregorithm…")
# Load the scientific package before PySide installs its source-inspection import hook.
# This avoids Shiboken rereading all scientific submodules during a Windows launch.
import gregorithm  # noqa: E402, F401  (imported for its side effect: loaded before PySide's import hook)

if __name__ == "__main__":
    report("Loading interface…")
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.branding import apply_identity  # noqa: E402
from app.fonts import load_fonts  # noqa: E402
from app.legacy_migration import migrate_settings, results_root  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.shutdown import finish_process  # noqa: E402
from app.single_instance import ActivationServer  # noqa: E402
from app.theme import ThemeManager  # noqa: E402


def qt_args(argv: list[str]) -> list[str]:
    """Qt command line. On Windows text is rendered with FreeType (grayscale anti-aliasing, slight hinting: how the
    Inter font is meant to look) instead of DirectWrite + ClearType, whose colour fringes and strong hinting make small
    text look pixelated. An explicit -platform argument or QT_QPA_PLATFORM always wins."""
    args = [a for a in argv if a != "--demo"]
    if sys.platform == "win32" and "-platform" not in args and not os.environ.get("QT_QPA_PLATFORM"):
        args += ["-platform", "windows:fontengine=freetype"]
    return args


def main(finish: Callable[[int], int] = int) -> int:
    """Run the application and return its exit code. The real launch passes ``finish_process``, called here while
    qt_app is alive: destroying a QApplication waits for its worker threads with no limit. Tests and tools that call
    main() keep the returned code and the normal teardown."""
    demo = "--demo" in sys.argv
    qt_app = QApplication(qt_args(sys.argv))
    apply_identity(qt_app)                 # name "SparkBox" and the window / taskbar icon (app.branding)
    load_fonts()
    migrate_settings()                     # former name mathbatt: preferences carried over once
    report("Preparing the workspace…")
    if demo:
        from app.demo_backend import DemoBackend
        backend = DemoBackend()
    else:
        from app.gregorithm_backend import GregorithmBackend
        backend = GregorithmBackend(results_root())
    theme = ThemeManager(qt_app)
    report("Opening the window…")
    window = MainWindow(backend, theme, demo=demo)
    window.shutdownFinished.connect(lambda: qt_app.exit(0))
    activation = ActivationServer(channel_name(), qt_app)
    activation.activated.connect(window.bring_to_front)
    theme.apply()
    window.show()
    QTimer.singleShot(0, ready)            # after the first paint: the launcher closes its banner
    return finish(qt_app.exec())


if __name__ == "__main__":
    sys.exit(main(finish_process))         # closing the window always ends the whole process (app.shutdown)
