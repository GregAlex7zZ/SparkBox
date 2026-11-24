"""Standby of the datasets that are not shown: their background calculations pause until they are selected again.

Every calculation of SparkBox runs in a worker thread and reports its progress through a callback. With several
large datasets open, a calculation left running on a dataset the operator is no longer looking at competed with the
shown one for the CPU and for Python's interpreter lock, and the whole interface stuttered. The operator expects the
other datasets to wait: files and results stay in memory, nothing is lost, and the work resumes when the dataset is
opened again (possibly after a short wait).

``STANDBY`` is the single gate of the application. ``MainWindow`` calls ``STANDBY.set_active(ds_id)`` whenever the
selected dataset changes. A worker wraps its progress callback with ``STANDBY.paced(ds_id, callback, token)``: at
each progress report of a dataset that is not selected, the worker thread sleeps (releasing the interpreter lock)
until the dataset is selected again, the calculation is cancelled, or the gate is opened for shutdown.

Usage:
    progress = STANDBY.paced(ds_id, lambda p: sig.progress.emit(ds_id, p), token)
    backend.analyze(..., progress, token)

Limits: a calculation pauses only at its progress reports, so a long step without reports finishes first. File
exports are never paused (the operator started them explicitly and waits for the file). With no dataset selected
(``set_active(None)``) and in the Comparison section, nothing is paused. ``open_all`` (application shutdown, tests)
releases every waiting worker for good.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

POLL_S = 0.2              # how often a paused worker re-checks cancellation


def _cancelled(token) -> bool:
    """True when ``token`` (gregorithm.CancellationToken, threading.Event or None) asks to stop."""
    if token is None:
        return False
    check = getattr(token, "is_cancelled", None)
    if callable(check):
        return bool(check())
    is_set = getattr(token, "is_set", None)
    return bool(is_set()) if callable(is_set) else False


class Standby:
    """Gate that lets only the selected dataset's calculations run. Thread-safe."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._active: Optional[str] = None
        self._everyone = True              # nothing selected yet, Comparison shown, or shut down: no pause
        self._closed = False

    def set_active(self, ds_id: Optional[str], everyone: bool = False) -> None:
        """Select the dataset whose calculations run; ``everyone=True`` (Comparison, or no dataset) runs them all."""
        with self._cond:
            self._active = ds_id
            self._everyone = everyone or ds_id is None
            self._cond.notify_all()

    def open_all(self) -> None:
        """Release every paused worker and never pause again (application shutdown)."""
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def reset(self) -> None:
        """Back to the initial state (nothing paused, gate usable again); for tests."""
        with self._cond:
            self._active, self._everyone, self._closed = None, True, False
            self._cond.notify_all()

    def runs(self, ds_id: str) -> bool:
        """Whether a calculation of ``ds_id`` may run now."""
        return self._closed or self._everyone or ds_id == self._active

    def wait(self, ds_id: str, token=None) -> None:
        """Block the calling worker while ``ds_id`` is on standby; return at once when it may run or when ``token``
        is cancelled (the calculation then stops at its next cancellation check)."""
        with self._cond:
            while not self.runs(ds_id) and not _cancelled(token):
                self._cond.wait(POLL_S)

    def paced(self, ds_id: str, callback: Callable, token=None) -> Callable:
        """Progress callback that first waits for ``ds_id`` to be selected, then calls ``callback``."""
        def report(progress) -> None:
            self.wait(ds_id, token)
            callback(progress)
        return report


STANDBY = Standby()
