"""Rebuild assets/icon/sparkbox.ico from assets/icon/sparkbox.png (the 1024 px master).

Why a dedicated writer: every size must declare 1 colour plane and 32 bits per pixel in the ICO directory. An ICO
whose entries declare 0 planes (as some image libraries write it) still opens in most viewers, but the Windows
shell and taskbar cannot match a size and show the generic application icon for the executable instead.

Usage (from the project folder, PySide6 installed): python tools/make_icon.py
Each size is resampled by Qt from the master and stored as a lossless PNG payload (supported by Windows Vista+).
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage  # noqa: E402

ICON_DIR = Path(__file__).resolve().parents[1] / "assets" / "icon"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def ico_bytes(master: QImage, sizes: tuple[int, ...] = SIZES) -> bytes:
    """ICO file content with one PNG payload per size, each entry declaring 1 plane and 32 bpp."""
    payloads = []
    for size in sizes:
        image = master.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise RuntimeError(f"Could not encode the {size} px icon")
        buffer.close()
        payloads.append(bytes(data.data()))
    contents = bytearray(struct.pack("<HHH", 0, 1, len(sizes)))
    offset = 6 + 16 * len(sizes)
    for size, payload in zip(sizes, payloads, strict=True):
        contents += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(payload), offset)
        offset += len(payload)
    for payload in payloads:
        contents += payload
    return bytes(contents)


def main() -> None:
    """Write sparkbox.ico next to the master PNG."""
    app = QGuiApplication.instance() or QGuiApplication([])
    master = QImage(str(ICON_DIR / "sparkbox.png"))
    if master.isNull():
        raise SystemExit("assets/icon/sparkbox.png could not be read")
    (ICON_DIR / "sparkbox.ico").write_bytes(ico_bytes(master))
    app.processEvents()
    print(f"Wrote {ICON_DIR / 'sparkbox.ico'} ({', '.join(map(str, SIZES))} px)")


if __name__ == "__main__":
    main()
