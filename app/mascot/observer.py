"""Mascot observer: makes the mascot react to what the application shows, without taking part in it.

Purpose: translate outcomes that are already visible to the user into mascot reactions. The observer only *listens*:
it connects to existing public signals (status-bar messages, controller notices and run results) and watches which
window becomes active to spot warning dialogs. It never calls a controller, never changes state and never blocks;
removing it (or the mascot) leaves the application's behaviour unchanged.

Reactions:
- failure (an operation failed, a warning or error dialog appears)  -> ``explosion``;
- success (import, processing, calculation, export, saved image...) -> ``thumbs_up`` or, less often, ``battery``;
- every ``break_every`` animations (20 by default)                   -> a bathroom ``break``;
- otherwise the mascot works at its desk.

Usage::

    observer = MascotObserver(mascot, parent=window)
    observer.watch_window(window)          # wires every source the main window offers

Limits: classification of free-text messages (``classify_message``) relies on the wording of the app's messages
(English, e.g. "Processing failed", "Exported 120 cycles"); a message with new wording is simply ignored, it is never
misreported as a failure unless it says "failed", "could not", "unable to" or "cannot". Several notifications for the
same outcome (a notice, then the status bar, then a dialog) produce one reaction thanks to a short debounce window.
"""
from __future__ import annotations

import random
import re
import time
from typing import Callable, Optional

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication, QMessageBox

SUCCESS, FAILURE = "success", "failure"

_IGNORE = re.compile(r"cancel|discarded|nothing was written|not available for this dataset|^ready\b", re.I)
_FAILURE = re.compile(r"\bfailed\b|\bfailure\b|could not|couldn't|unable to|\bcannot\b", re.I)
_SUCCESS = re.compile(r"^imported\b|\bfinished\b|\bcomplete\b|\bcalculated\b|\bexported\b|\bsaved\b|"
                      r"\bapplied\b|\brecognised\b|\brecognized\b", re.I)


def classify_message(text: str) -> Optional[str]:
    """Return ``"success"``, ``"failure"`` or None (neutral / unknown) for a user-facing message.

    Cancellations and neutral notices ("Ready", "late result discarded", hints) are None. Failure wording wins over
    success wording, but a count of technical errors inside a completed calculation is still a success.
    """
    text = (text or "").strip()
    if not text or _IGNORE.search(text):
        return None
    if _FAILURE.search(text):
        return FAILURE
    if _SUCCESS.search(text):
        return SUCCESS
    return None


class MascotObserver(QObject):
    """Turns application outcomes into mascot events.

    ``mascot`` is anything with ``play(name)`` (normally ``app.mascot.widget.Mascot``). ``rng`` and ``clock`` are
    injectable for tests. ``debounce_s``: outcomes of the same kind closer than this are one reaction.
    ``battery_share``: fraction of successes shown by lifting the battery instead of a thumbs up.
    ``break_every``: number of animations played (desk-work loops and reactions alike) before the mascot goes to the
    bathroom; counted from the mascot's ``clipFinished`` signal, restarted after each break.
    """

    def __init__(self, mascot, parent: Optional[QObject] = None, *, rng: Optional[random.Random] = None,
                 clock: Callable[[], float] = time.monotonic, debounce_s: float = 3.0, battery_share: float = 0.35,
                 break_every: int = 20):
        super().__init__(parent)
        self.mascot = mascot
        self.rng = rng or random.Random()
        self.clock = clock
        self.debounce_s = debounce_s
        self.battery_share = battery_share
        self.break_every = max(1, int(break_every))
        self.clips_since_break = 0
        self._last: dict[str, float] = {}                  # kind -> time of its last reaction
        self.enabled = True                                # False while the mascot is removed: nothing is queued
        self._gitt_errors: dict[str, str] = {}             # ds_id -> last GITT/ICI error seen
        finished = getattr(mascot, "clipFinished", None)
        if finished is not None:
            finished.connect(self.on_clip_finished)

    # --- reactions -----------------------------------------------------------------------------------------------
    def react(self, kind: Optional[str]) -> Optional[str]:
        """React to an outcome kind; return the mascot event played, or None when debounced / neutral."""
        if kind not in (SUCCESS, FAILURE) or not self.enabled:
            return None
        now = self.clock()
        last = self._last.get(kind)
        if last is not None and now - last < self.debounce_s:
            return None
        if kind == SUCCESS and now - self._last.get(FAILURE, -1e9) < self.debounce_s:
            return None                                    # a failure just happened: do not celebrate over it
        self._last[kind] = now
        if kind == FAILURE:
            event = "explosion"
        else:
            event = "battery" if self.rng.random() < self.battery_share else "thumbs_up"
        self.mascot.play(event)
        return event

    def on_message(self, text: str) -> Optional[str]:
        """Slot for any user-facing message (status bar, notices)."""
        return self.react(classify_message(text))

    def on_run_finished(self, _ds_id: str, outcome: str) -> Optional[str]:
        """Slot for ``ElabController.runFinished(ds_id, "ok" | "cancelled" | "failed")``."""
        return self.react({"ok": SUCCESS, "failed": FAILURE}.get(outcome))

    def on_failure(self, *_args) -> Optional[str]:
        """Slot for signals that only ever report a failure (export errors, save failures)."""
        return self.react(FAILURE)

    def on_window_activated(self, *_args) -> Optional[str]:
        """Slot for ``QGuiApplication.focusWindowChanged``: a warning or error dialog is a failure."""
        box = QApplication.activeModalWidget() or QApplication.activeWindow()
        if isinstance(box, QMessageBox) and box.icon() in (QMessageBox.Icon.Warning, QMessageBox.Icon.Critical):
            return self.react(FAILURE)
        return None

    def gitt_changed(self, ds_id: str, error: str) -> Optional[str]:
        """Report a GITT/ICI state change; a newly appeared error message is a failure."""
        previous = self._gitt_errors.get(ds_id, "")
        self._gitt_errors[ds_id] = error or ""
        if error and error != previous:
            return self.react(FAILURE)
        return None

    # --- bathroom breaks -----------------------------------------------------------------------------------------
    def on_clip_finished(self, clip: str) -> Optional[str]:
        """Count one finished animation; every ``break_every`` of them the mascot goes to the bathroom.

        The bathroom clips themselves (``restroom_*``) are not counted. Returns ``"break"`` when a break was requested.
        Limits: nothing is counted while the mascot is removed or hidden, since it plays no clip then.
        """
        if clip.startswith("restroom_") or not self.enabled:
            return None
        self.clips_since_break += 1
        if self.clips_since_break < self.break_every:
            return None
        self.clips_since_break = 0
        self.mascot.play("break")
        return "break"

    # --- wiring --------------------------------------------------------------------------------------------------
    def watch_window(self, window) -> None:
        """Connect to every outcome source the main window exposes. Missing sources are skipped silently, so the
        observer keeps working if a section is renamed or removed."""
        window.statusBar().messageChanged.connect(self.on_message)
        elab = getattr(window, "elab", None)
        if elab is not None and hasattr(elab, "runFinished"):
            elab.runFinished.connect(self.on_run_finished)
        for name in ("capacity", "dcir", "dqdv", "gitt_ici"):
            ctl = getattr(window, name, None)
            if ctl is None:
                continue
            if hasattr(ctl, "exportError"):
                ctl.exportError.connect(self.on_failure)
            if hasattr(ctl, "exportFinished"):
                ctl.exportFinished.connect(lambda _ds, text: self.on_message(text))
        for name in ("dcir", "correction", "gitt_ici"):
            ctl = getattr(window, name, None)
            if ctl is not None and hasattr(ctl, "notice"):
                ctl.notice.connect(lambda _ds, text: self.on_message(text))
        comparison = getattr(window, "comparison", None)
        if comparison is not None and hasattr(comparison, "notice"):
            comparison.notice.connect(self.on_message)
        explorer = getattr(window, "explorer", None)
        if explorer is not None:
            if hasattr(explorer, "savedImage"):
                explorer.savedImage.connect(lambda *_a: self.react(SUCCESS))
            if hasattr(explorer, "saveFailed"):
                explorer.saveFailed.connect(self.on_failure)
        gitt = getattr(window, "gitt_ici", None)
        if gitt is not None and hasattr(gitt, "model"):
            def _gitt(ds_id: str) -> None:
                model = gitt.model(ds_id)
                self.gitt_changed(ds_id, getattr(model, "error", "") if model is not None else "")
            gitt.changed.connect(_gitt)
        app = QApplication.instance()
        if app is not None:
            app.focusWindowChanged.connect(self.on_window_activated)
