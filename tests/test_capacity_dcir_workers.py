from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gregorithm
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from app.backend import CapacityTable
from app.capacity import CapResult
from app.capacity_controller import CapacityController
from app.dcir import DetectionResult
from app.dcir_controller import DcirController
from app.dqdv import DqdvResult
from app.dqdv_controller import DqdvController
from app.elab_controller import ElabController, UserDefaults
from app.elaboration import ElabResult
from app.state import Dataset, SessionState
from app.units_controller import UnitsController


class _Backend:
    """Narrow worker seam: no scientific values are calculated by this test."""

    def set_display_units(self, *_args):
        pass

    def set_sample_values(self, *_args):
        pass

    def analyze_capacity(self, _ds, result_id, params, _progress, cancel):
        if not isinstance(cancel, gregorithm.CancellationToken):
            raise TypeError("wrong Capacity cancellation token")
        return CapResult("cap", result_id, 1, params)

    def capacity_table(self, result_id, _units):
        return CapacityTable(result_id, {"cycle": [1.0]})

    def release_capacity(self, *_args):
        pass

    def result_cycles(self, *_args):
        return [1]

    def detect_dcir(self, _ds, result_id, params, _units, _progress, cancel):
        if not isinstance(cancel, gregorithm.CancellationToken):
            raise TypeError("wrong DCIR cancellation token")
        return DetectionResult("dcir", result_id, params, ())

    def release_dcir(self, *_args):
        pass

    def analyze_dqdv(self, _ds, result_id, params, _dcir_id, _progress, cancel):
        if not isinstance(cancel, gregorithm.CancellationToken):
            raise TypeError("wrong dQ/dV cancellation token")
        return DqdvResult("dqdv", result_id, params, 1, 2)

    def dqdv_curves(self, *_args):
        return []

    def release_dqdv(self, *_args):
        pass


class ScientificWorkerTokenTests(unittest.TestCase):
    """Regression for a real-data failure in the Capacity and DCIR workers."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.backend = _Backend()
        self.state = SessionState()
        dataset = Dataset("dataset", "measurement.csv", "measurement.csv")
        dataset.elab.finish(ElabResult("m2", 1, 2, 3, 4, dataset.elab.params))
        self.state.add(dataset)
        self.elab = ElabController(self.state, self.backend, UserDefaults())
        self.units = UnitsController(self.state, self.backend)

    def _flush(self):
        self.assertTrue(QThreadPool.globalInstance().waitForDone(5_000))
        self.app.processEvents()
        self.assertTrue(QThreadPool.globalInstance().waitForDone(5_000))
        self.app.processEvents()

    def test_capacity_uses_gregorithm_cancellation_token(self):
        controller = CapacityController(self.state, self.backend, self.elab, self.units)
        controller.analyze("dataset")
        self._flush()
        self.assertIsNotNone(self.state.active.capacity.result)
        self.assertEqual(self.state.active.capacity.message, "")

    def test_dcir_uses_gregorithm_cancellation_token(self):
        controller = DcirController(self.state, self.backend, self.elab, self.units)
        controller.detect("dataset")
        self._flush()
        self.assertIsNotNone(self.state.active.dcir.detection)
        self.assertEqual(self.state.active.dcir.message, "")

    def test_dqdv_uses_gregorithm_cancellation_token(self):
        controller = DqdvController(self.state, self.backend, self.elab, self.units,
                                    cycles_fn=lambda _ds: [1])
        self.state.active.dqdv.cycles_text = "1"
        controller.analyze("dataset")
        self._flush()
        self.assertIsNotNone(self.state.active.dqdv.result)
        self.assertEqual(self.state.active.dqdv.message, "")


if __name__ == "__main__":
    unittest.main()
