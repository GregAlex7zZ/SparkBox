"""Bundled UI font. Inter (SIL OFL 1.1, rsms/inter v4.1) is a typeface designed for screen interfaces, close to Apple's
SF Pro, with clear digits for scientific values. Only the top level of assets/fonts is loaded: the previous Ubuntu
files are parked in assets/fonts/ubuntu and can be restored by moving them back and changing UI_FONT."""
from pathlib import Path
from PySide6.QtGui import QFontDatabase

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
UI_FONT = "Inter"


def load_fonts() -> int:
    """Load every .ttf in assets/fonts (Inter 300/400/500/600/700 + italic). Falls back to system fonts if absent."""
    n = 0
    for f in sorted(FONT_DIR.glob("*.ttf")):
        if QFontDatabase.addApplicationFont(str(f)) >= 0:
            n += 1
    return n
