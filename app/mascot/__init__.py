"""Animated mascot shown next to the "SparkBox" brand.

Modules:
- ``director``: Qt-free frame sequencing (which frame, for how long, seamless transitions);
- ``widget``: the ``Mascot`` QWidget that paints the frames from ``assets/mascot``;
- ``observer``: ``MascotObserver``, which listens to existing application signals and plays reactions.

The mascot is purely decorative: it observes the application and never influences it.
"""
from .director import COMMANDS, EVENTS, Director

__all__ = ["Director", "EVENTS", "COMMANDS", "Mascot", "MascotObserver"]


def __getattr__(name):
    # lazy imports: the director (and its tests) do not need Qt widgets
    if name == "Mascot":
        from .widget import Mascot
        return Mascot
    if name == "MascotObserver":
        from .observer import MascotObserver
        return MascotObserver
    raise AttributeError(name)
