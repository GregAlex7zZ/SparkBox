"""Mascot widget: the animated pixel-art lab assistant shown next to the "SparkBox" brand.

Purpose: paint the frames chosen by ``app.mascot.director.Director`` on a transparent, fixed-size widget.

Usage::

    mascot = Mascot(height=48)          # width follows from the height (scene aspect ratio, about 1.2 : 1)
    layout.addWidget(mascot)
    mascot.play("thumbs_up")            # also safe from worker threads (queued through a signal)

The widget has a fixed size, so neighbouring widgets never move whatever animation is playing. It stops its timer
while hidden (minimised window, collapsed sidebar) and rescales its frames for HiDPI screens.

Limits: frames are loaded from ``assets/mascot`` the first time the widget paints (about 90 small PNG files); a
missing frame raises FileNotFoundError at that point. Right-clicking offers a small menu to preview the animations and
"Remove Mascot", which only emits ``removeRequested``: the owner decides what removing means (the main window hides
the mascot and remembers it; Settings › Appearance brings it back).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QEvent, QPointF, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSizePolicy, QWidget

from .director import COMMANDS, EVENTS, Director

ASSET_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "mascot"

# screen or scale-factor change: frames must be rescaled
_DPR_EVENTS = tuple(t for t in (getattr(QEvent.Type, "ScreenChangeInternal", None),
                                getattr(QEvent.Type, "DevicePixelRatioChange", None)) if t is not None)

MENU_ITEMS = [("Thumbs Up", "thumbs_up"), ("Lift the Battery", "battery"), ("Explosion", "explosion"),
              ("Bathroom Break", "break"), (None, None), ("Work at the Desk", "desk"), ("Stand Still", "idle")]


def load_manifest() -> dict:
    """Read ``assets/mascot/manifest.json`` (scene size, frame files and offsets, clips with per-frame durations)."""
    return json.loads((ASSET_DIR / "manifest.json").read_text(encoding="utf-8"))


class Mascot(QWidget):
    """Animated mascot with a transparent background.

    ``height`` is in logical pixels; the width follows the scene's aspect ratio (58 px wide at 48 px high).
    ``auto_desk``: when no event is pending the mascot works at the desk. ``menu``: right-click preview menu.
    Signals: ``clipStarted(str)`` / ``clipFinished(str)`` per clip, ``eventFinished(str)`` when a whole event ends.
    """

    clipStarted = Signal(str)
    clipFinished = Signal(str)
    eventFinished = Signal(str)
    removeRequested = Signal()                             # "Remove Mascot" chosen in the right-click menu
    _requested = Signal(str)                               # lets play() be called from any thread

    NAMES = tuple(EVENTS) + COMMANDS

    def __init__(self, parent: Optional[QWidget] = None, *, height: int = 48, auto_desk: bool = True,
                 menu: bool = True, max_queue: int = 3):
        super().__init__(parent)
        self._manifest = load_manifest()
        world = self._manifest["world"]
        self._world = QSize(world["width"], world["height"])
        self.director = Director(self._manifest, auto_desk=auto_desk, max_queue=max_queue,
                                 on_clip_start=self.clipStarted.emit, on_clip_end=self.clipFinished.emit,
                                 on_event_end=self.eventFinished.emit)
        self._menu = menu
        self._cache: dict[str, tuple[QPixmap, QPointF]] = {}
        self._cache_key: Optional[tuple[int, int, float]] = None
        self._frame = self._manifest["neutral"]
        self.setObjectName("Mascot")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setToolTip("Lab assistant: reacts to what happens in SparkBox (right-click to preview or remove)")
        self.set_height(height)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._advance)
        self._requested.connect(self._on_request)
        self._kick_pending = False

    # --- public API ----------------------------------------------------------------------------------------------
    def play(self, name: str) -> None:
        """Queue an event (``thumbs_up``, ``battery``, ``explosion``, ``break``) or a command (``desk``, ``idle``)."""
        if name not in self.NAMES:
            raise ValueError(f"Unknown mascot event: {name}")
        self._requested.emit(name)

    def set_height(self, height: int) -> None:
        """Resize the widget to ``height`` logical pixels, keeping the scene's aspect ratio."""
        height = max(16, int(height))
        self.setFixedSize(self.width_for_height(height, self._world), height)
        self._cache.clear()
        self._cache_key = None
        self.update()

    @staticmethod
    def width_for_height(height: int, world: Optional[QSize] = None) -> int:
        """Horizontal space taken by the mascot at a given height."""
        if world is None:
            m = load_manifest()["world"]
            world = QSize(m["width"], m["height"])
        return round(height * world.width() / world.height())

    @staticmethod
    def height_for_width(width: int, world: Optional[QSize] = None) -> int:
        """Largest height whose width (``width_for_height``) fits in ``width`` pixels (0 when nothing fits)."""
        if world is None:
            m = load_manifest()["world"]
            world = QSize(m["width"], m["height"])
        h = max(0, int(width * world.height() / world.width()) + 1)
        while h > 0 and Mascot.width_for_height(h, world) > width:
            h -= 1
        return h

    @property
    def scene(self) -> str:
        """``"neutral"``, ``"desk"``, ``"battery"`` or ``"break"``."""
        return self.director.scene

    @property
    def frame(self) -> str:
        """Identifier of the frame currently shown."""
        return self._frame

    # --- animation -----------------------------------------------------------------------------------------------
    def _on_request(self, name: str) -> None:
        self.director.request(name)
        self._kick()                                       # restarts at once if it was holding the neutral pose

    def _kick(self) -> None:
        """Restart the timer if it is stopped (once, however many times it is called)."""
        if self._timer.isActive() or self._kick_pending or not self.isVisible():
            return
        self._kick_pending = True
        QTimer.singleShot(0, self._advance)

    def _advance(self) -> None:
        self._kick_pending = False
        if not self.isVisible():
            return
        frame, ms = self.director.next_frame()
        if frame != self._frame:
            self._frame = frame
            self.update()
        if ms is not None:
            self._timer.start(ms)

    def hideEvent(self, event) -> None:
        self._timer.stop()                                 # not visible: no work
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._kick()

    # --- painting ------------------------------------------------------------------------------------------------
    def _prepare(self) -> None:
        """Scale every frame to the current size, once per (size, device pixel ratio).

        Each frame is first placed on the full scene and then scaled, so still objects (desk, door) always fall on
        the same pixels and do not shimmer.
        """
        dpr = self.devicePixelRatioF() or 1.0
        key = (self.width(), self.height(), dpr)
        if key == self._cache_key:
            return
        self._cache.clear()
        W, H = self._world.width(), self._world.height()
        f = min(self.width() * dpr / W, self.height() * dpr / H)
        dev_w, dev_h = max(1, round(W * f)), max(1, round(H * f))
        off_x = (self.width() - dev_w / dpr) / 2           # centred horizontally, resting on the bottom edge
        off_y = self.height() - dev_h / dpr
        sources: dict[str, QImage] = {}
        for fid, info in self._manifest["frames"].items():
            src = sources.get(info["file"])
            if src is None:
                src = QImage(str(ASSET_DIR / info["file"]))
                if src.isNull():
                    raise FileNotFoundError(ASSET_DIR / info["file"])
                sources[info["file"]] = src
            scene = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
            scene.fill(Qt.GlobalColor.transparent)
            p = QPainter(scene)
            p.drawImage(info["x"], info["y"], src)         # whatever leaves the scene is clipped
            p.end()
            small = scene.scaled(dev_w, dev_h, Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
            r = _alpha_bbox(small)                         # keep only the non-empty part
            pix = QPixmap.fromImage(small.copy(r))
            pix.setDevicePixelRatio(dpr)
            self._cache[fid] = (pix, QPointF(off_x + r.x() / dpr, off_y + r.y() / dpr))
        self._cache_key = key

    def paintEvent(self, event) -> None:
        self._prepare()
        item = self._cache.get(self._frame)
        if item is None:
            return
        pix, pos = item
        p = QPainter(self)
        p.drawPixmap(pos, pix)
        p.end()

    def changeEvent(self, event) -> None:
        if event.type() in _DPR_EVENTS:
            self._cache_key = None
            self.update()
        super().changeEvent(event)

    def sizeHint(self) -> QSize:
        return self.size()

    # --- interaction ---------------------------------------------------------------------------------------------
    def contextMenuEvent(self, event) -> None:
        if not self._menu:
            return super().contextMenuEvent(event)
        menu = QMenu(self)
        for label, name in MENU_ITEMS:
            if label is None:
                menu.addSeparator()
                continue
            menu.addAction(label).triggered.connect(lambda checked=False, n=name: self.play(n))
        menu.addSeparator()
        remove = menu.addAction("Remove Mascot")
        remove.setToolTip("Hide the mascot; Settings › Appearance shows it again")
        remove.triggered.connect(self.removeRequested)
        menu.exec(event.globalPos())

    def closeEvent(self, event) -> None:
        self._timer.stop()
        super().closeEvent(event)


def _alpha_bbox(img: QImage) -> QRect:
    """Bounding rectangle of the non-transparent pixels (1x1 when the image is empty)."""
    w, h = img.width(), img.height()
    a = img.convertToFormat(QImage.Format.Format_Alpha8)
    stride = a.bytesPerLine()
    data = bytes(a.constBits())[: a.sizeInBytes()]
    x0, y0, x1, y1 = w, h, -1, -1
    for y in range(h):
        row = data[y * stride:y * stride + w]
        if not any(row):
            continue
        y0 = min(y0, y)
        y1 = y
        left = next(i for i, v in enumerate(row) if v)
        right = w - 1 - next(i for i, v in enumerate(reversed(row)) if v)
        x0, x1 = min(x0, left), max(x1, right)
    if x1 < 0:
        return QRect(0, 0, 1, 1)
    return QRect(x0, y0, x1 - x0 + 1, y1 - y0 + 1)
