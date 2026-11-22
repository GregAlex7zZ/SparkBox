"""PyInstaller hook for pyqtgraph, overriding the upstream one (packaging/sparkbox.spec: hookspath).

Why: the upstream hook imports every pyqtgraph submodule to find its UI templates, including pyqtgraph.opengl
(needs PyOpenGL) and pyqtgraph.jupyter (needs jupyter_rfb). SparkBox uses neither and does not install them, so the
build printed 'Failed to collect submodules for ...'. This hook is the upstream one with those optional parts (and
the examples) skipped.
Limits: keeps the upstream rules for templates, data files and the multiprocess bootstrap; review it when updating
pyinstaller-hooks-contrib.
"""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files("pyqtgraph", excludes=["**/examples/*"])
_skipped = ("pyqtgraph.examples", "pyqtgraph.opengl", "pyqtgraph.jupyter")
all_imports = collect_submodules("pyqtgraph", filter=lambda name: not name.startswith(_skipped))
hiddenimports = [name for name in all_imports if "Template" in name]
hiddenimports += ["pyqtgraph.multiprocess.bootstrap"]
excludedimports = ["pyqtgraph.opengl", "pyqtgraph.jupyter"]
try:
    from PyInstaller.utils.hooks.qt import exclude_extraneous_qt_bindings
except ImportError:
    pass
else:
    excludedimports += exclude_extraneous_qt_bindings(hook_name="hook-pyqtgraph", qt_bindings_order=None)
