# PyInstaller build of SparkBox.app for macOS (onedir inside an application bundle, no terminal window).
#
# Run through tools/build_macos.py, which first writes .cache/SparkBox.icns from the application icon, then adds the
# third-party license files and packs the bundle into a .dmg. Same contents as the Windows build (sparkbox.spec):
# the application code, assets, in-app help and license texts; never measurement files, tests, tools or notes.

import os
import re
from pathlib import Path

root = Path(SPECPATH).parent
version = re.search(r'^VERSION = "([^"]+)"', (root / "app" / "branding.py").read_text(encoding="utf-8"),
                    re.MULTILINE).group(1)
icon = root / ".cache" / "SparkBox.icns"

datas = [
    (str(root / "assets"), "assets"),
    (str(root / "docs" / "help"), "docs/help"),
    (str(root / "LICENSES"), "LICENSES"),
    (str(root / "LICENSE"), "."),
    (str(root / "THIRD_PARTY_NOTICES.md"), "."),
]

analysis = Analysis(
    [str(root / "run.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=["app.gregorithm_backend", "app.demo_backend", "PySide6.QtSvg", "PySide6.QtNetwork"],
    hookspath=[str(root / "packaging" / "hooks")],     # local overrides of outdated upstream hooks
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "jupyter", "pytest", "ruff", "PySide6.QtWebEngineCore",
              "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia"],
    noarchive=False,
)
# PyArrow's package data includes its own test files; they are not needed at run time.
analysis.datas = [entry for entry in analysis.datas if "tests" not in Path(entry[0]).parts]
archive = PYZ(analysis.pure)
app = EXE(
    archive, analysis.scripts, [], exclude_binaries=True,
    name="SparkBox", console=False, debug=False, strip=False, upx=False,
    argv_emulation=False,
    target_arch=os.environ.get("SPARKBOX_TARGET_ARCH") or None,
    codesign_identity=None,                           # ad-hoc signature (not notarized)
)
collected = COLLECT(app, analysis.binaries, analysis.datas, strip=False, upx=False, name="SparkBox")
bundle = BUNDLE(
    collected,
    name="SparkBox.app",
    icon=str(icon),
    bundle_identifier="io.github.gregalex7zz.sparkbox",
    version=version,
    info_plist={
        "CFBundleName": "SparkBox",
        "CFBundleDisplayName": "SparkBox",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "13.0",
        "NSHumanReadableCopyright": "Copyright (c) 2026 Alessandro Gregucci. MIT License.",
    },
)
