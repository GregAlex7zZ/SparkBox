"""Single running SparkBox and startup-banner messages (app.startup, app.single_instance).

A second start must not open a second window: it cannot take the per-user lock and asks the running instance to
come to the front. The launcher banner shows each step written by report() and closes when ready() is written.
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

from app import startup
from app.single_instance import ActivationServer, send_activate


class StartupMessages(unittest.TestCase):
    def test_report_and_ready_write_the_launcher_files(self):
        with tempfile.TemporaryDirectory() as folder:
            status, done = os.path.join(folder, "status"), os.path.join(folder, "ready")
            os.environ[startup.STATUS_ENV], os.environ[startup.READY_ENV] = status, done
            try:
                startup.report("Loading gregorithm…")
                self.assertEqual(Path(status).read_text(encoding="utf-8"), "Loading gregorithm…")
                startup.report("Opening the window…")                     # replaced, never appended
                self.assertEqual(Path(status).read_text(encoding="utf-8"), "Opening the window…")
                self.assertFalse(os.path.exists(done))
                startup.ready()
                self.assertTrue(os.path.exists(done))
                self.assertNotIn(startup.READY_ENV, os.environ)              # only the first call acts
            finally:
                os.environ.pop(startup.STATUS_ENV, None)
                os.environ.pop(startup.READY_ENV, None)

    def test_channel_name_is_stable_and_per_user(self):
        self.assertEqual(startup.channel_name(), startup.channel_name())
        self.assertTrue(startup.channel_name().startswith("SparkBox-"))


class SingleInstanceLock(unittest.TestCase):
    def test_second_claim_fails_while_the_first_process_holds_the_lock(self):
        name = "SparkBoxTest-" + uuid.uuid4().hex[:12]
        holder = subprocess.Popen(
            [sys.executable, "-c", "import sys, time; sys.path.insert(0, sys.argv[1]); from app.startup import "
             "claim_instance; print(claim_instance(sys.argv[2]), flush=True); time.sleep(30)", str(ROOT), name],
            stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "True")
            self.assertFalse(startup.claim_instance(name))                  # a second start
        finally:
            holder.kill()
            holder.wait()
        deadline = time.monotonic() + 5
        while not startup.claim_instance(name):                             # released when the holder ends
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.1)


class ActivationChannel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_second_start_brings_the_running_window_forward(self):
        name = "SparkBoxTest-" + uuid.uuid4().hex[:12]
        server = ActivationServer(name)
        self.assertTrue(server.listening)
        hits = []
        server.activated.connect(lambda: hits.append(1))
        # the second start is another process, as in real use
        sender = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); from PySide6.QtCore import "
             "QCoreApplication; app = QCoreApplication([]); from app.single_instance import send_activate; "
             "print(send_activate(sys.argv[2], 3000), flush=True)", str(ROOT), name],
            stdout=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 10
        while (not hits or sender.poll() is None) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertEqual(sender.stdout.read().strip(), "True")
        self.assertEqual(hits, [1])
        server.close()
        self.assertFalse(send_activate(name, 200))                          # nobody listening any more


if __name__ == "__main__":
    unittest.main()
