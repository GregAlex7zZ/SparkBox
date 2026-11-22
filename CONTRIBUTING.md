# Contributing to SparkBox

Thank you for your interest in improving SparkBox. Bug reports, documentation fixes and focused
pull requests are welcome.

## Development setup

Use CPython 3.12 or newer in a virtual environment. Install gregorithm first (from its repository),
then:

```console
python -m pip install -r requirements-dev.txt
```

## Quality checks

Run these before opening a pull request (the CI runs the same checks):

```console
ruff check .
python tools/run_tests.py
```

`tools/run_tests.py` runs every test file in its own process, several at a time (about 1.5 minutes).
A single `python -m pytest` process also works but is much slower: widgets built by earlier tests
stay alive and every theme change re-styles all of them. Tests run with `QT_QPA_PLATFORM=offscreen`
and never touch your real preferences (`tests/conftest.py`).

After editing an equation in the Scientific Methods help, regenerate its images with
`python tools/build_help_formulas.py` (needs Matplotlib, build time only).

## Conventions

- Code, docstrings, comments and documentation are written in English.
- Every module, class and function has a docstring explaining its purpose, usage and limits.
- **No science in the GUI.** Every scientific quantity comes from gregorithm's public API;
  SparkBox only converts display units and draws what the library publishes. A missing value is
  shown as missing, never as zero.
- Long work runs on worker threads; the GUI thread only updates widgets.
- Add a regression test for every bug fix in parsing, processing, memory or display behaviour.

## Data and privacy

Do not commit measurement files, files with identifying metadata, local absolute paths,
credentials or result stores. Tests and examples use small synthetic data only.

## Pull requests

Keep pull requests focused. Describe the change, the tests you ran (platform and Python version)
and include a screenshot for visible changes.

## Continuous integration

The CI workflow installs gregorithm from the requirement stored in the repository variable
`GREGORITHM_REQUIREMENT` (for example `gregorithm @ git+https://github.com/<owner>/<repo>`). Set it
in *Settings → Secrets and variables → Actions → Variables*.
