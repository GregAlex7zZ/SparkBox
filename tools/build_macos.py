"""Build the macOS application of SparkBox: dist/SparkBox-<version>-macos-<arch>.dmg containing SparkBox.app.

Steps: make .cache/SparkBox.icns from assets/icon/sparkbox.png (sips + iconutil); build SparkBox.app with
PyInstaller (packaging/sparkbox_macos.spec); copy the license files of every bundled third-party package into it
(Contents/Resources/LICENSES/third-party, with INVENTORY.txt of exact versions, as the Windows build does); sign the
bundle ad hoc; pack it with a link to /Applications into a compressed disk image. The result needs no Python on the
user's Mac.

Usage (macOS, from the project folder, in an environment with requirements-build.txt and gregorithm installed):
  python tools/build_macos.py

Limits: builds for the architecture of the Python running it (arm64 on Apple silicon, x86_64 on Intel); NumPy,
SciPy and PyArrow ship no universal2 wheels, so each architecture needs its own build. The app is signed ad hoc,
not with a Developer ID, and not notarized: on first start macOS refuses to open it until the user allows it
(Finder › right-click › Open, or System Settings › Privacy & Security › Open Anyway). See packaging/README.md.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_windows import CACHE, ROOT, third_party_licenses, version  # noqa: E402

ICON_SIZES = (16, 32, 128, 256, 512)


def make_icns(source: Path, target: Path) -> None:
    """Write ``target`` (.icns) from the 1024 px PNG ``source`` with the macOS tools sips and iconutil."""
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "SparkBox.iconset"
        iconset.mkdir()
        for size in ICON_SIZES:
            for scale in (1, 2):
                name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
                px = str(size * scale)
                subprocess.run(["sips", "-z", px, px, str(source), "--out", str(iconset / name)], check=True,
                               stdout=subprocess.DEVNULL)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(target)], check=True)


def make_dmg(app: Path, target: Path, volume: str) -> None:
    """Compressed read-only disk image with ``app`` and a link to /Applications for drag-and-drop install."""
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / volume
        stage.mkdir()
        subprocess.run(["ditto", str(app), str(stage / app.name)], check=True)   # keeps symlinks and signatures
        (stage / "Applications").symlink_to("/Applications")
        target.unlink(missing_ok=True)
        subprocess.run(["hdiutil", "create", "-volname", volume, "-srcfolder", str(stage), "-fs", "HFS+",
                        "-format", "UDZO", "-ov", str(target)], check=True)


def main() -> int:
    """Run every build step; returns 0 and prints the path of the disk image."""
    if sys.platform != "darwin":
        raise SystemExit("Build the macOS application on macOS.")
    ver = version()
    arch = platform.machine()
    CACHE.mkdir(exist_ok=True)
    print("1/4 Icon", flush=True)
    make_icns(ROOT / "assets" / "icon" / "sparkbox.png", CACHE / "SparkBox.icns")

    print("2/4 PyInstaller: SparkBox.app", flush=True)
    folder_dist = CACHE / "pyinstaller-dist-macos"
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
                    "--distpath", str(folder_dist), "--workpath", str(CACHE / "pyinstaller-work-macos"),
                    str(ROOT / "packaging" / "sparkbox_macos.spec")], cwd=ROOT, check=True)
    app = folder_dist / "SparkBox.app"
    if not (app / "Contents" / "MacOS" / "SparkBox").is_file():
        raise SystemExit(f"PyInstaller did not produce {app}")

    print("3/4 Licenses and signature", flush=True)
    for line in third_party_licenses(app / "Contents" / "Resources" / "LICENSES" / "third-party"):
        print("   license:", line, flush=True)
    # Files were added after PyInstaller signed the bundle: sign it again (ad hoc) so its seal matches.
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)

    print("4/4 Disk image", flush=True)
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    dmg = dist / f"SparkBox-{ver}-macos-{arch}.dmg"
    make_dmg(app, dmg, f"SparkBox {ver}")
    shutil.rmtree(CACHE / "pyinstaller-work-macos", ignore_errors=True)
    print(f"Built {dmg} ({dmg.stat().st_size / 2**20:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
