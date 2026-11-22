"""Mascot director: decides which animation frame to show and for how long.

Purpose: the frame-sequencing logic of the mascot, kept free of Qt so it can be tested without a display. The widget
(``app.mascot.widget``) calls ``next_frame()`` each time its timer fires and shows the returned frame for the returned
number of milliseconds.

Usage::

    d = Director(load_manifest())
    d.request("thumbs_up")
    frame_id, ms = d.next_frame()

Seam guarantees (checked by tests/test_mascot.py):
- every event starts and ends on the canonical neutral pose, so any event can follow any other;
- the desk loop starts and ends on the same "resting" frame, which is also the first frame of the desk exit: the
  mascot leaves the desk only from there, so no frame ever jumps.

Limits: the director knows only the clips listed in the manifest; it has no notion of real time (the caller paces it).
"""
from __future__ import annotations

from collections import deque
from typing import Callable, Optional

# event name -> clips played in sequence (all clip names are manifest keys)
EVENTS: dict[str, list[str]] = {
    "thumbs_up": ["thumbs_up"],
    "battery": ["lift", "lower"],
    "explosion": ["explosion"],
    "break": ["restroom_entry", "restroom_exit"],
}
# commands choose what the mascot does when no event is pending
COMMANDS = ("desk", "idle")


class Director:
    """Frame sequencer for the mascot.

    ``request(name)`` queues an event (see ``EVENTS``) or applies a command (``"desk"``: work at the desk while idle;
    ``"idle"``: stand still in the neutral pose). Repeated requests for an event already waiting are dropped and at most
    ``max_queue`` events wait (the oldest is discarded), so a burst of notifications never builds a long backlog.
    Callbacks report clip start/end and the end of a whole event.
    """

    def __init__(self, manifest: dict, *, auto_desk: bool = True, max_queue: int = 4,
                 on_clip_start: Optional[Callable[[str], None]] = None,
                 on_clip_end: Optional[Callable[[str], None]] = None,
                 on_event_end: Optional[Callable[[str], None]] = None):
        self.clips = manifest["clips"]
        self.neutral = manifest["neutral"]
        loop = self.clips["desk_loop"]["frames"]
        self.desk_rest = loop[-1]                          # the only frame the desk loop may be left from
        if self.clips["desk_exit"]["frames"][0] != self.desk_rest:
            raise ValueError("Manifest: the desk exit must start on the desk loop's resting frame")
        self.auto_desk = auto_desk
        self.max_queue = max_queue
        self.on_clip_start = on_clip_start or (lambda name: None)
        self.on_clip_end = on_clip_end or (lambda name: None)
        self.on_event_end = on_event_end or (lambda name: None)
        self.events: deque[str] = deque()                  # requested events not started yet
        self.plan: deque[str] = deque()                    # remaining clips of the event in progress
        self.current_event: Optional[str] = None
        self.current: Optional[str] = None                 # clip in progress
        self.index = 0
        self.scene = "neutral"                             # "neutral" | "desk" | "battery" | "break"
        self.frame = self.neutral

    # --- requests ------------------------------------------------------------------------------------------------
    def request(self, name: str) -> None:
        """Queue an event or apply a command. Raises ValueError for unknown names."""
        if name not in EVENTS and name not in COMMANDS:
            raise ValueError(f"Unknown mascot event: {name}")
        if name in COMMANDS:
            self.auto_desk = name == "desk"
            return
        if name in self.events or name == self.current_event:
            return                                         # the same reaction is already on its way
        if len(self.events) >= self.max_queue:
            self.events.popleft()                          # keep the most recent ones
        self.events.append(name)

    @property
    def idle(self) -> bool:
        """True when standing still in the neutral pose with nothing to do (the caller's timer may sleep)."""
        return (self.current is None and not self.plan and not self.events
                and not self.auto_desk and self.scene == "neutral")

    @property
    def busy(self) -> bool:
        """True while an event is playing or waiting (the desk loop alone does not count)."""
        return bool(self.current_event or self.events or self.plan)

    # --- stepping ------------------------------------------------------------------------------------------------
    def next_frame(self) -> tuple[str, Optional[int]]:
        """Advance one step and return ``(frame_id, duration_ms)``; a None duration means "hold until a request"."""
        if self.current is not None:
            frames = self.clips[self.current]["frames"]
            wants_out = self.events or not self.auto_desk
            # early exit from the desk loop, only right after the resting frame was shown
            if (self.current == "desk_loop" and wants_out
                    and frames[self.index] == self.desk_rest and self.index > 0):
                self._end_clip()
            else:
                self.index += 1
                if self.index < len(frames):
                    return self._show()
                self._end_clip()
        return self._choose()

    def _show(self) -> tuple[str, int]:
        clip = self.clips[self.current]
        self.frame = clip["frames"][self.index]
        return self.frame, clip["ms"][self.index]

    def _start(self, name: str) -> tuple[str, int]:
        self.current, self.index = name, 0
        self.on_clip_start(name)
        return self._show()

    def _end_clip(self) -> None:
        done, self.current = self.current, None
        if done in ("desk_entry", "desk_loop"):
            self.scene = "desk"
        elif done == "lift":
            self.scene = "battery"
        elif done == "restroom_entry":
            self.scene = "break"
        else:
            self.scene = "neutral"
        self.on_clip_end(done)
        if not self.plan and self.current_event and done == EVENTS[self.current_event][-1]:
            event, self.current_event = self.current_event, None
            self.on_event_end(event)

    def _choose(self) -> tuple[str, Optional[int]]:
        if self.plan:
            return self._start(self.plan.popleft())
        if self.events:
            if self.scene == "desk":
                return self._start("desk_exit")
            self.current_event = self.events.popleft()
            self.plan.extend(EVENTS[self.current_event])
            return self._start(self.plan.popleft())
        if self.auto_desk:
            return self._start("desk_loop" if self.scene == "desk" else "desk_entry")
        if self.scene == "desk":
            return self._start("desk_exit")
        self.frame = self.neutral
        return self.neutral, None
