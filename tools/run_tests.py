"""Run the GUI test suite with every test file in its own Python process, several files in parallel.

Why: the GUI tests build many windows and pages. Each page connects lambdas that capture ``self`` to the signals of
its own child widgets, a cycle through Qt that Python's garbage collector cannot break, so in one long pytest process
tens of thousands of widgets stay alive. Every theme change (QApplication.setStyleSheet) then re-polishes all of
them: one theme test took more than five minutes and the whole suite about 50 minutes. In the application each page
is built once, so this is a test-process effect only. A fresh process per file keeps every file's cost independent.

Usage (from the project folder):
  python tools/run_tests.py                     every tests/test_*.py, as many workers as CPUs allow
  python tools/run_tests.py -j 4                four files at a time
  python tools/run_tests.py tests/test_dqdv.py  only the given files
  python tools/run_tests.py -- -k export        extra pytest arguments after "--", passed to every file

Output: one line per file (result, counts, seconds) as files finish, then the full output of every file that failed
and a total. Exit code 0 only when every file passed (skips allowed). Each file runs with QT_QPA_PLATFORM=offscreen
unless the variable is already set, and with the same conftest.py isolation as a normal pytest run.

Limits: tests must not depend on the order of files or on state left by another file (each process starts clean);
``-j`` above the number of CPU cores mostly adds contention, since Qt rendering is CPU bound. A file that exceeds
``--timeout`` seconds is stopped and reported as failed.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = re.compile(r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed|deselected|warnings?)")
# pytest's final line, e.g. "1 failed, 9 passed in 2.84s"; read from stdout only (Qt may still write to stderr later)
FINAL_LINE = re.compile(r"\b(passed|failed|skipped|errors?|deselected|no tests ran)\b.* in [\d.]+s")


@dataclass
class FileResult:
    """Outcome of one test file: pytest exit code, counts by outcome, duration [s] and the captured output."""
    path: Path
    code: int
    counts: dict[str, int]
    seconds: float
    output: str

    @property
    def ok(self) -> bool:
        """True when pytest succeeded, or collected nothing runnable (exit code 5, e.g. all deselected by -k)."""
        return self.code in (0, 5)


def run_file(path: Path, extra: list[str], timeout: float) -> FileResult:
    """Run pytest on one file in a new interpreter and parse the counts of its final summary line."""
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(path), *extra]
    start = time.perf_counter()
    try:
        done = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, check=False)
        code, stdout, output = done.returncode, done.stdout, done.stdout + done.stderr
    except subprocess.TimeoutExpired as exc:
        captured = (exc.stdout or "") if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(errors="replace")
        code, stdout, output = -1, captured, captured + f"\nStopped after {timeout:.0f} s (--timeout)."
    seconds = time.perf_counter() - start
    counts: dict[str, int] = {}
    final = next((line for line in reversed(stdout.splitlines()) if FINAL_LINE.search(line)), "")
    for number, kind in SUMMARY.findall(final):
        if not kind.startswith("warning"):
            counts["errors" if kind.startswith("error") else kind] = int(number)
    return FileResult(path, code, counts, seconds, output)


def main(argv: list[str] | None = None) -> int:
    """Parse the command line, run the files in parallel, print a report; return the process exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    extra: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        argv, extra = argv[:split], argv[split + 1:]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path, help="test files (default: every tests/test_*.py)")
    parser.add_argument("-j", "--jobs", type=int, default=max(1, min(8, (os.cpu_count() or 2) // 2)),
                        help="files run at the same time (default: half the CPUs, at most 8)")
    parser.add_argument("--timeout", type=float, default=1800.0, help="seconds allowed per file (default 1800)")
    args = parser.parse_args(argv)
    files = [f if f.is_absolute() else ROOT / f for f in args.files] or sorted((ROOT / "tests").glob("test_*.py"))
    missing = [f for f in files if not f.is_file()]
    if missing:
        parser.error("not found: " + ", ".join(str(f) for f in missing))

    start = time.perf_counter()
    results: list[FileResult] = []
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = [pool.submit(run_file, f, extra, args.timeout) for f in files]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            counts = ", ".join(f"{n} {k}" for k, n in result.counts.items()) or "no summary"
            print(f"{'ok  ' if result.ok else 'FAIL'} {result.path.name:45s} {counts:35s} {result.seconds:7.1f} s",
                  flush=True)

    failed = [r for r in results if not r.ok]
    for result in sorted(failed, key=lambda r: r.path.name):
        print(f"\n{'=' * 30} {result.path.name} (exit {result.code}) {'=' * 30}\n{result.output.rstrip()}")
    total: dict[str, int] = {}
    for result in results:
        for kind, number in result.counts.items():
            total[kind] = total.get(kind, 0) + number
    summary = ", ".join(f"{n} {k}" for k, n in sorted(total.items()))
    print(f"\n{len(results)} files, {summary}; {len(failed)} file(s) failed; "
          f"{time.perf_counter() - start:.0f} s with {args.jobs} worker(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
