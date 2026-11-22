"""Exercise fresh-process startup rather than relying on an already-imported Qt/core test runner."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StartupTests(unittest.TestCase):
    def test_shiboken_does_not_inspect_scientific_submodules_during_import(self):
        probe = """
import inspect, json, time
original = inspect.getsource
inspected = []
def trace(obj):
    name = getattr(obj, '__name__', '')
    if name.startswith('gregorithm'):
        inspected.append(name)
    return original(obj)
inspect.getsource = trace
start = time.perf_counter()
import run
print(json.dumps(dict(elapsed_s=time.perf_counter()-start, inspected=inspected)))
"""
        result = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertFalse([name for name in data["inspected"] if name.startswith("gregorithm.")], data)

    def test_normal_entry_point_shows_and_closes_with_the_production_adapter(self):
        probe = """
import tempfile
from pathlib import Path
import run
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
import app.gregorithm_backend as adapter
original_backend = adapter.GregorithmBackend
original_exec = QApplication.exec
with tempfile.TemporaryDirectory() as temp:
    adapter.GregorithmBackend = lambda _root: original_backend(Path(temp) / 'store')
    def execute(self):
        QTimer.singleShot(200, self.closeAllWindows)
        return original_exec()
    QApplication.exec = execute
    code = run.main()
    print('PRODUCTION_STARTUP_EXIT=' + str(code))
    raise SystemExit(code)
"""
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        result = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PRODUCTION_STARTUP_EXIT=0", result.stdout)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("Point size <= 0", result.stderr)
        self.assertNotIn("unique connections require", result.stderr)


if __name__ == "__main__":
    unittest.main()
