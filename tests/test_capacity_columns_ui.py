"""Capacity table and export dialog leave out specific / areal columns whose Sample value (Active
Material Mass, Surface Area) was not entered for the shown result."""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PySide6.QtWidgets import QApplication

from app.capacity import CAP_COLUMNS, CapParams, cap_label, shown_columns
from app.elaboration import CHARGE_FIRST
from app.units import UnitPrefs
from app.widgets.capacity_page import ExportDialog
from app.widgets.capacity_table import CapacityModel


class CapacityColumnsUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_table_shows_only_columns_with_their_sample_value(self):
        values = {c.key: np.array([0.0, 1.0, 2.0]) if c.key == "cycle" else np.array([1.0, 2.0, np.nan])
                  for c in CAP_COLUMNS}
        model = CapacityModel()
        keys = shown_columns(CapParams(CHARGE_FIRST, 1, 12.4, None))
        model.set_data(values, [0, 1, 2], UnitPrefs(), [], keys)
        headers = [model.header_text(i) for i in range(model.columnCount())]
        self.assertEqual(model.columnCount(), 13)
        self.assertIn("Charge Specific Capacity [mAh/g]", headers)
        self.assertNotIn("Charge Areal Capacity [mAh/cm²]", headers)
        self.assertEqual(model.copy_value(1, 0), "1")                    # the Cycle column stays first
        model.set_data(values, [0, 1, 2], UnitPrefs(), [])               # default: every column
        self.assertEqual(model.columnCount(), 17)

    def test_export_dialog_offers_only_shown_columns(self):
        keys = shown_columns(CapParams(CHARGE_FIRST, 1, None, None))
        dialog = ExportDialog(None, UnitPrefs(), "Rows: all", keys)
        try:
            self.assertEqual(list(dialog.boxes), keys)
            self.assertNotIn("charge_capacity_mah_g", dialog.boxes)
            self.assertNotIn("discharge_energy_mwh_cm2", dialog.boxes)
            _fmt, columns, _diag = dialog.choice()
            self.assertEqual(columns, keys)
            self.assertEqual(dialog.boxes["cycle"].text(), cap_label("cycle", UnitPrefs()))
        finally:
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
