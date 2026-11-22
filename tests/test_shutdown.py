"""Closing the main window ends the whole application.

Regressions: closeEvent cancelled only some sections and then waited with no limit, so a long GITT / ICI, Capacity,
DCIR, Correction or Explorer job kept the process alive, and a job that ignores cancellation kept it alive for as long
as it ran. The process tests run a real entry point in a child process (settings isolated by conftest, demo backend).
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import gregorithm
from PySide6.QtCore import QRunnable, QThreadPool
from PySide6.QtWidgets import QApplication, QWidget

import app.main_window as main_window
from app.demo_backend import DemoBackend
from app.main_window import MainWindow
from app.theme import ThemeManager

ROOT = Path(__file__).resolve().parent.parent

CHILD = """
import os, runpy, sys, time
sys.path[:0] = [{root!r}, {tests!r}]
import conftest                                     # isolated preferences, before any app import
from PySide6.QtCore import QRunnable, QThreadPool, QTimer
from PySide6.QtWidgets import QWidget
import app.main_window
import app.shutdown
sys.argv = ["run.py", "--demo"]
app.main_window.SHUTDOWN_WAIT_MS = 200

class Stubborn(QRunnable):
    def run(self):
        time.sleep(300)                             # ignores cancellation, far longer than the test timeout

class Window(app.main_window.MainWindow):
    def showEvent(self, event):
        super().showEvent(event)
        {on_show}
        QTimer.singleShot(200, self.close)

app.main_window.MainWindow = Window
finish = app.shutdown.finish_process
app.shutdown.finish_process = lambda code: (print("loop-left", flush=True), finish(code))
runpy.run_path({entry!r}, run_name="__main__")      # the real launch, run.py as a script
print("not-reached", flush=True)
"""


def _run_child(on_show: str) -> subprocess.CompletedProcess:
    script = CHILD.format(root=str(ROOT), tests=str(ROOT / "tests"), entry=str(ROOT / "run.py"), on_show=on_show)
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    return subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=90, env=env,
                          cwd=str(ROOT), check=False)


def test_process_ends_even_if_a_job_ignores_cancellation() -> None:
    self_job = "self._job = Stubborn(); QThreadPool.globalInstance().start(self._job)"
    done = _run_child(self_job)
    assert done.returncode == 0, done.stderr
    assert "loop-left" in done.stdout and "not-reached" not in done.stdout


def test_process_ends_with_another_window_still_open() -> None:
    """A window with no parent keeps Qt's event loop alive unless closing the main window closes it too."""
    done = _run_child("self._other = QWidget(); self._other.show()")
    assert done.returncode == 0, done.stderr
    assert "loop-left" in done.stdout and "not-reached" not in done.stdout


def _window() -> MainWindow:
    return MainWindow(DemoBackend(0.01), ThemeManager(QApplication.instance() or QApplication([])), demo=True)


def test_closing_cancels_the_jobs_of_every_section() -> None:
    win = _window()
    tokens = {name: gregorithm.CancellationToken() for name in ("capacity", "dcir", "correction", "gitt")}
    win.capacity._cancel["d"] = tokens["capacity"]
    win.dcir._cancel["d"] = tokens["dcir"]
    win.correction._cancel["d"] = tokens["correction"]
    win.gitt_ici._jobs["d"] = (1, tokens["gitt"], None)
    export = win.gitt_ici._export_tokens["d"] = gregorithm.CancellationToken()
    events = {name: threading.Event() for name in ("capacity", "dcir", "explorer", "render")}
    win.capacity._export_cancel["d"] = events["capacity"]
    win.dcir._export_cancel["d"] = events["dcir"]
    win.explorer._cancel["d"] = events["explorer"]
    win.explorer._render_cancel["d"] = events["render"]
    win.show()
    win.close()
    assert not win.isVisible()
    assert all(t.is_cancelled() for t in tokens.values()) and export.is_cancelled()
    assert all(e.is_set() for e in events.values())


def test_other_windows_close_and_the_end_of_closing_is_signalled() -> None:
    win = _window()
    other = QWidget()
    other.show()
    finished = []
    win.shutdownFinished.connect(lambda: finished.append(True))
    win.show()
    win.close()
    assert not other.isVisible() and finished == [True]
    win.close()                                            # a second close does nothing more
    assert finished == [True]


def test_backend_files_stay_open_while_a_worker_may_still_use_them(monkeypatch) -> None:
    release = threading.Event()

    class Stubborn(QRunnable):
        def run(self) -> None:
            release.wait(30)

    closed = []
    win = _window()
    monkeypatch.setattr(win.backend, "close", lambda: closed.append(True), raising=False)
    monkeypatch.setattr(main_window, "SHUTDOWN_WAIT_MS", 50)
    job = Stubborn()
    job.setAutoDelete(False)
    QThreadPool.globalInstance().start(job)
    started = time.monotonic()
    win.close()
    assert time.monotonic() - started < 10 and closed == []     # bounded wait, handles left to the process exit
    release.set()
    assert QThreadPool.globalInstance().waitForDone(10000)
