# Windows executable

`python tools/build_windows.py` (Windows x64, in an environment with `requirements-build.txt` and gregorithm
installed) produces **`dist/SparkBox-<version>-windows-x64.exe`**: one file, no Python needed on the user's
computer. `python tools/build_windows.py --launcher-only` recompiles only the launcher around the last payload.

## What happens when the user clicks it

1. **Instant banner.** A small .NET Framework launcher (`SparkBoxLauncher.cs`, compiled with the C# compiler that
   ships with Windows 10/11) paints the SparkBox banner before doing anything else: icon, name, the current step
   and a moving bar.
2. **One instance only.** A second click while SparkBox is starting or open does not start anything: the open
   window comes to the front instead. The launcher holds a per-user mutex while it runs; the application holds its
   own (`app/startup.py`) and listens on a per-user local channel (`app/single_instance.py`). The same rule
   applies to `python run.py`.
3. **Extraction once per version.** The application folder built by PyInstaller (`sparkbox.spec`) is embedded as
   a ZIP and extracted on the first start of each build to `%LOCALAPPDATA%\SparkBox\app\<build id>`; later
   starts reuse it. Older builds are removed automatically.
4. **Startup steps.** The application reports "Loading gregorithm…", "Loading interface…", "Preparing the
   workspace…", "Opening the window…" to the banner, which closes as soon as the main window is on screen.
5. **First-start note.** Until a build has reached its main window once (marker `.started` in its folder), the
   banner also explains that Windows is checking the files and that the first start can take a few minutes,
   and waits up to 15 minutes (3 otherwise) before closing. Antivirus scanning of the hundreds of newly
   extracted, unsigned DLLs/`.pyd` files loaded by "Loading gregorithm…" dominates that start; it does not
   depend on CPU speed and recurs once for every new version.
6. **Taskbar icon.** Right after extraction the launcher asks the Windows shell for the icon of the new
   `SparkBox.exe` before starting it. The taskbar draws the icon of the executable file; without this step its first
   read, made while Windows was still checking the just-started file, failed and the taskbar showed a generic icon
   for the whole first session of every new version.

Measured on the development machine: banner visible within half a second; main window after 9–17 s on the
first start of a version (extraction included) and about 4 s afterwards.

## Release notes

- Distribute only the single executable; the intermediate folder in `.cache/` is a build product.
- `packaging/version_info.txt` is generated from `app/branding.py` (`VERSION`) at every build.
- `packaging/hooks/` overrides two outdated upstream PyInstaller hooks (SciPy special functions, pyqtgraph); the
  build should print no warnings. Review them when updating PyInstaller or pyinstaller-hooks-contrib.
- The executable is not code-signed: Windows SmartScreen may show a warning the first time it is run.
- Licenses: the build copies the license and notice files of every bundled package (Python, PySide6 / shiboken6,
  pyqtgraph, NumPy, SciPy, PyArrow, gregorithm, the PyInstaller bootloader) into `LICENSES/third-party/` inside the
  bundle, with `INVENTORY.txt` of their exact versions, and stops if one is missing. `LICENSES/` also holds the
  LGPL-3.0 and GPL-3.0 texts under which Qt for Python is bundled.
- Qt is bundled under the LGPL-3.0: attach `THIRD_PARTY_NOTICES.md` and `LICENSES/` to the release page as well,
  state the exact PySide6 version (from `INVENTORY.txt`) and keep the matching SparkBox source tagged in the
  repository; `THIRD_PARTY_NOTICES.md` links the Qt for Python and Qt source downloads.
- Upload the executable as a GitHub Release asset; never commit it to the repository.
