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
- Standby of the datasets not selected: their calculations pause until their tab is selected again (Comparison
  lets every dataset run), and the sections not on screen load a newly selected dataset only when opened, so a
  second large dataset no longer slows the interface down.
- GITT / ICI IR Drop: one Sample times card, as in DCIR, whose fields show the times of the open impulse, with
  Apply (this impulse) and Apply to All. OCV Analysis: two cards, Sampled OCV and Extrapolated OCV, each with Apply
  (this rest) and Apply to All; Extrapolate OCV applies at once.
- GITT / ICI Diffusion Results: markers joined by lines by default.
- GITT / ICI IR Drop is compact enough to fit on one screen without scrolling: the values under each chart sit two
  per line, the sample times on one line, and the charts take all the remaining height.
- GITT / ICI Pulse Fitting: the fit values table scrolls sideways smoothly by dragging it with the pointer, with its
  bar, with Shift + wheel or with a touchpad, and the scroll bar no longer covers the last row.
- Help: a new User Guide section 9.7, Fit statistics, explains R², q_L, q_S, the statistical and physical
  indicators, the Delacourt and GITT Δ checks and the OCV-extrapolation values.
- Dataset tabs are as wide as their file name (up to 300 px).

### Fixed

- Chart lag with large datasets: hovering a chart relaid out the whole window at every mouse move (now ~7 ms
  instead of ~35 ms per move), and a wheel zoom redrew dense traces as one antialiased path (~140 ms per step;
  now drawn as segments while the wheel turns, ~45 ms, and once more as a clean path when it rests).
- Hover details (cycle, half-cycle, step) are read once the pointer rests, one read at a time: a sweep over the
  full view used to queue one row-group read per mouse move and kept about one CPU core busy.

- Text cut in half: the X / Y1 / Y2 selectors of Capacity, Data Explorer and Comparison now fit the chosen
  column name, every drop-down list is as wide as its longest item, and toolbars (Data summary, chart selectors,
  Explorer footer, GITT / ICI steps, Sample Informations) wrap onto a new line instead of squeezing their labels
  and buttons in a narrow window; the IR Drop values keep all their wrapped lines.

- DCIR Results › Export failed with every scientific result: the gregorithm adapter had no DCIR export.
- Windows executable: the taskbar showed a generic icon on the first start of every new version.
- Windows executable: the license texts of every bundled third-party package (Qt for Python under the LGPL-3.0,
  Python, NumPy, SciPy, PyArrow, pyqtgraph, gregorithm, PyInstaller) are now included, with an inventory of their
  versions; the About dialog lists them.
- Windows build: no more PyInstaller warnings for scipy.special._cdflib (absent in recent SciPy) and the
  optional pyqtgraph.opengl / pyqtgraph.jupyter modules (local hooks in packaging/hooks).
- Single-file Windows executable with an instant startup banner; only one SparkBox runs at a time (a
  second start brings the open window to the front).
