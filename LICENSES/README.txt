License texts of third-party components bundled with SparkBox.

INTER-OFL-1.1.txt  Inter font 4.1 (assets/fonts), SIL Open Font License 1.1.
LUCIDE-ISC.txt     Lucide icons (app/icons_builtin.py, assets/icons/lucide): official LICENSE of lucide-static 1.73
                   (ISC; the icons derived from Feather are also under the MIT License it contains).
LGPL-3.0.txt       GNU Lesser General Public License v3: the license option under which the Windows executable
                   bundles Qt for Python (PySide6, shiboken6) and the Qt libraries.
GPL-3.0.txt        GNU General Public License v3, whose terms the LGPL-3.0 incorporates.

The Windows executable also contains third-party/: the license and notice files of every other bundled package
(Python, NumPy, SciPy, PyArrow, pyqtgraph, gregorithm, the PyInstaller bootloader, ...) copied unchanged from the
build environment by tools/build_windows.py, with INVENTORY.txt listing their exact versions.

See THIRD_PARTY_NOTICES.md in the project root for the complete inventory.
