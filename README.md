# SparkBox

**SparkBox** is a desktop application for analysing galvanostatic battery-cycling data. It is the
graphical interface of [gregorithm](#requirements), the scientific library that performs every
calculation: SparkBox lets you import a measurement, explore it and run the analyses step by step,
review the results and export them, without writing code.

- **Data Import** – CSV, TXT and Parquet files, with automatic or manual column and unit mapping.
- **Data Elaboration** – half-cycles, cycles and steps (rest, CC, CV) with cumulative capacity and
  energy.
- **Data Explorer** – interactive plots of any column against any other, on two Y axes.
- **Capacity** – per-cycle capacity, energy, efficiency and C-rate; specific and areal values when
  the active-material mass or the electrode area is given.
- **DCIR** – pulse detection, review and DC internal resistance per cycle, including manual
  measurements.
- **Cycle Correction** – re-number cycles after the fact without re-importing data.
- **dQ/dV** – differential capacity by interpolated gradient or by the derivative-free voltage
  histograms of Flores and Clark (2026), with voltage reconstruction as a quality check.
- **GITT / ICI** – solid-state diffusion coefficients from pulse experiments (GITT VA, GITT Δ, ICI
  and the analytical spherical fit), with window selection, advisory quality indicators and
  explicit review before export.
- **Comparison** – overlay published results of several datasets.
- **Export** – every table export of a dataset (elaborated data, Capacity, DCIR, dQ/dV, GITT / ICI) in one
  step, as separate CSV or Parquet files.

Results are immutable and reproducible: every analysis is saved with its parameters and input
fingerprints, and changing a setting never overwrites an earlier result.

> **Status: alpha.** Features and saved-result formats may still change. Results are verified
> against analytic test cases; this establishes software and mathematical consistency, not the
> validity of a particular instrument or laboratory method.

## Download (Windows)

Windows users can download `SparkBox-<version>-windows-x64.exe` from the repository's
**Releases** page and run it: no installation and no Python needed. The first start of a new version
unpacks the application once and Windows checks the new files, which can take a few minutes even on a
fast computer (the banner says so); later starts are much faster. Only one
SparkBox opens at a time: clicking it again brings the open window to the front.

To run from source on any platform, see below.

## Requirements

- Python 3.12 or newer (Windows, macOS or Linux).
- The **gregorithm** library, installed in the same environment from its own repository
  (clone it, then run `python -m pip install .` in its folder).
- The packages in [requirements.txt](requirements.txt): PySide6 (Qt for Python), pyqtgraph, NumPy
  and PyArrow.

## Install and run

```console
python -m venv .venv
.venv\Scripts\activate          # Windows; on macOS / Linux: source .venv/bin/activate
python -m pip install <path-or-URL-of-gregorithm>
python -m pip install -r requirements.txt
python run.py
```

`python run.py --demo` starts the interface with clearly labelled synthetic data and no scientific
backend; it is meant for trying the interface only.

Saved results live in the `.gregorithm-gui/results` folder of your user directory; preferences are
stored with the operating system's settings mechanism.

## Help

**Help → SparkBox Help** (`F1`) and **Help → Scientific Methods** open the
[User Guide](docs/help/gregorithm_gui_user_guide.md) and the
[Scientific Methods](docs/help/gregorithm_gui_scientific_methods.md) inside the application. Both
are Markdown files and can also be read here. [Settings and profiles](docs/SETTINGS_PROFILES.md)
and [the mascot](docs/MASCOT.md) are described separately.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md). In short:

```console
python -m pip install -r requirements-dev.txt
python tools/run_tests.py        # every test file in its own process, in parallel
ruff check .
```

The Windows executable is built with `python tools/build_windows.py`; see
[packaging/README.md](packaging/README.md).

## Citation

If you publish results computed with the histogram dQ/dV methods or the voltage reconstruction,
please cite:

> E. Flores and S. Clark, "Simple and Comparable Computation of Incremental Capacity Curves",
> *J. Electrochem. Soc.* **173**, 120520 (2026).
> [doi:10.1149/1945-7111/ae7e5c](https://doi.org/10.1149/1945-7111/ae7e5c)

Please also cite gregorithm itself (see its `CITATION.cff`).

## License and authors

SparkBox is released under the [MIT License](LICENSE). Bundled fonts and icons keep their own
licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Authorship is described in
[AUTHORS.md](AUTHORS.md) and changes in [CHANGELOG.md](CHANGELOG.md).
