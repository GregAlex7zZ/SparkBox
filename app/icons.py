"""Icon loading with per-state recolouring. Qt does not inherit CSS currentColor, so the colour string is
substituted before rendering. Lookup order: assets/icons/custom, assets/icons/lucide (official), builtin fallback."""
from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from .icons_builtin import BUILTIN

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "icons"
_HEAD = ('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" '
         'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">')
_cache: dict[tuple, QPixmap] = {}


def _svg_text(name: str) -> str:
    for sub in ("custom", "lucide"):
        p = ASSETS / sub / f"{name}.svg"
        if p.is_file():
            return p.read_text(encoding="utf-8")
    inner = BUILTIN.get(name)
    if inner is None:
        raise KeyError(f"Icon not found: {name}")
    return f"{_HEAD}{inner}</svg>"


def pixmap(name: str, color: str, size: int = 20) -> QPixmap:
    key = (name, color, size)
    if key in _cache:
        return _cache[key]
    screen = QGuiApplication.primaryScreen()
    dpr = max(2.0, screen.devicePixelRatio() if screen else 1.0)
    renderer = QSvgRenderer(QByteArray(_svg_text(name).replace("currentColor", color).encode("utf-8")))
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    pm.setDevicePixelRatio(dpr)
    _cache[key] = pm
    return pm


def make_icon(name: str, normal: str, checked: str | None = None, disabled: str | None = None,
              size: int = 20) -> QIcon:
    ic = QIcon()
    n = pixmap(name, normal, size)
    ic.addPixmap(n, QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(n, QIcon.Mode.Active, QIcon.State.Off)
    if checked:
        c = pixmap(name, checked, size)
        ic.addPixmap(c, QIcon.Mode.Normal, QIcon.State.On)
        ic.addPixmap(c, QIcon.Mode.Active, QIcon.State.On)
    if disabled:
        d = pixmap(name, disabled, size)
        ic.addPixmap(d, QIcon.Mode.Disabled, QIcon.State.Off)
        ic.addPixmap(d, QIcon.Mode.Disabled, QIcon.State.On)
    return ic
