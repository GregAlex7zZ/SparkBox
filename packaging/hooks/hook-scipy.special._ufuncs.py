"""PyInstaller hook for scipy.special._ufuncs, overriding the upstream one (packaging/sparkbox.spec: hookspath).

Why: the upstream hook adds scipy.special._cdflib for every SciPy >= 1.13, but newer SciPy releases (1.18 here) no
longer ship that extension, so the build printed 'Hidden import "scipy.special._cdflib" not found!'. Each helper
extension is listed only when the installed SciPy really has it.
Limits: covers the extensions the upstream hook knew about; a future SciPy needing another one must be added here.
"""
from importlib.util import find_spec


def _present(name: str) -> bool:
    """True when the installed SciPy provides the extension module ``name``."""
    try:
        return find_spec(name) is not None
    except (ImportError, ValueError):
        return False


hiddenimports = [name for name in ("scipy.special._ufuncs_cxx", "scipy.special._cdflib",
                                   "scipy.special._special_ufuncs") if _present(name)]
