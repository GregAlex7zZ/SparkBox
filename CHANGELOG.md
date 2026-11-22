# Changelog

All notable changes to SparkBox are documented here. The project follows
[Semantic Versioning](https://semver.org/); during the 0.x series, features and saved-result
formats may change between minor versions when documented here.

## [0.1.0] — unreleased

First public version.

### Added

- Data Import of CSV, TXT and Parquet files with automatic or manual column and unit mapping, a
  discard report and a paged table of the imported points.
- Data Elaboration: half-cycles, cycles and steps with cumulative capacity and energy, sample
  values (active-material mass, electrode area) and a paged table of the elaborated points.
- Data Explorer with two Y axes, box zoom, pan, wheel zoom, hover readout, "Render All Points" and
  image export.
- Capacity: per-cycle table and chart; specific and areal columns appear only when the mass or the
  area is entered.
- DCIR: pulse detection, review (accept / reject / pending), manual measurements and results per
  cycle, in Ω.
- Cycle Correction based on accepted DCIR events or manual spans.
- dQ/dV: interpolated gradient and the histogram methods of Flores and Clark (2026), optional
  smoothing, voltage window and voltage reconstruction with APE and RMSE.
- GITT / ICI: setup, rest OCV, pulse fitting, IR drops, diffusion results with review, and export
  with one row per impulse. IR Drop shows the pre-pulse and post-pulse charts always side by side,
  takes independent pre/post sample times per impulse or for all impulses, and has its own
  acceptance (only accepted R_on / R_off are drawn and exported). OCV Analysis sets the sampled-OCV
  time for all rests or one rest, and the extrapolation target and fit start of a single rest.
- Comparison of published Data Explorer, Capacity and dQ/dV results across datasets.
- Display units for the whole application or for one dataset, light / dark / system themes,
  favourite settings profiles, an optional animated mascot and in-app help.
- File dialogs remember the last import and export folders.
- Export Center: an Export button at the bottom of the section list writes the exports of every section of the
  active dataset in one step, as separate files; sections without a result are locked and say what to run first.
- Data Elaboration: Export Data… saves the elaborated points or segments as CSV or Parquet.
- DCIR Review: V1 / I1 times can be set for one pulse (Apply) or for every pulse (Apply to All); a pulse whose
  times change returns to Pending and must be accepted again.

### Changed

- Data Elaboration: the sample values are headed "Sample Informations" (the Optional chip is gone).
- Capacity: Surface Area also links to Data Elaboration ("Edit in Data Elaboration").
- Exports of a dataset (each section and the Export window) start in the folder of its original file; the last
  export folder is used only when that folder is unknown or gone.
- GITT / ICI Setup: Required physical inputs are highlighted in the accent colour.

### Fixed

- DCIR Results › Export failed with every scientific result: the gregorithm adapter had no DCIR export.
- Windows executable: the taskbar showed a generic icon on the first start of every new version.
- Windows executable: the license texts of every bundled third-party package (Qt for Python under the LGPL-3.0,
  Python, NumPy, SciPy, PyArrow, pyqtgraph, gregorithm, PyInstaller) are now included, with an inventory of their
  versions; the About dialog lists them.
- Windows build: no more PyInstaller warnings for scipy.special._cdflib (absent in recent SciPy) and the
  optional pyqtgraph.opengl / pyqtgraph.jupyter modules (local hooks in packaging/hooks).
- Single-file Windows executable with an instant startup banner; only one SparkBox runs at a time (a
  second start brings the open window to the front).
