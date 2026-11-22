# PyInstaller build of the SparkBox application folder (onedir, no console window).
#
# Run through tools/build_windows.py, which also writes packaging/version_info.txt and wraps the folder in the
# single-file launcher. Only explicit resources are collected: the application code, assets, in-app help and
# license texts; never measurement files, tests, tools or private notes.

from pathlib import Path

root = Path(SPECPATH).parent

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
    icon=str(root / "assets" / "icon" / "sparkbox.ico"),
    version=str(root / "packaging" / "version_info.txt"),
)
bundle = COLLECT(app, analysis.binaries, analysis.datas, strip=False, upx=False, name="SparkBox")
