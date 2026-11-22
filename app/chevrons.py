"""Themed chevron images for QSS (QComboBox::down-arrow). QSS can only reference image FILES, so the recoloured SVGs
are written once per colour into a cache folder and referenced with url(). Single source of the dropdown indicator
style. The same mechanism draws the tick of the themed QCheckBox indicator ("check")."""
from __future__ import annotations
import tempfile
from pathlib import Path

_PATHS = {"down": "m6 9 6 6 6-6", "up": "m18 15-6-6-6 6", "right": "m9 18 6-6-6-6", "check": "M20 6 9 17l-5-5"}
_DIR = Path(tempfile.gettempdir()) / "gregorithm_gui_chevrons"


def chevron_file(direction: str, color: str, size: int = 16) -> str:
    """Return a forward-slash path to an SVG chevron of the given colour (created on demand)."""
    _DIR.mkdir(parents=True, exist_ok=True)
    f = _DIR / f"chev-{direction}-{color.lstrip('#')}-{size}.svg"
    if not f.exists():
        f.write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            f'<path d="{_PATHS[direction]}"/></svg>', encoding="utf-8")
    return f.as_posix()

