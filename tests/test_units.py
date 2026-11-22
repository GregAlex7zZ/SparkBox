import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.units import *
from app.elaboration import ElabState, parse_optional_positive, SampleValues
from app.state import Dataset


class Units(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(FACTORY_UNITS, UnitPrefs("h", "mA", "V", "mAh", "mWh", "Ω"))   # resistance in ohms by default

    def test_options(self):
        self.assertEqual(OPTIONS[TIME], ("s", "min", "h"))
        self.assertEqual(OPTIONS[CURRENT], ("A", "mA", "µA"))
        self.assertEqual(OPTIONS[VOLTAGE], ("V", "mV"))
        self.assertEqual(OPTIONS[CAPACITY], ("Ah", "mAh"))
        self.assertEqual(OPTIONS[ENERGY], ("Wh", "mWh"))

    def test_scale(self):
        self.assertAlmostEqual(7200 * scale(TIME, "h"), 2.0)
        self.assertAlmostEqual(1.25 * scale(CURRENT, "mA"), 1250)
        self.assertAlmostEqual(0.4125 * scale(CAPACITY, "mAh"), 412.5)
        self.assertEqual(scale(VOLTAGE, "V"), 1.0)

    def test_with_unit_validates(self):
        self.assertEqual(FACTORY_UNITS.with_unit(TIME, "s").time, "s")
        with self.assertRaises(ValueError):
            FACTORY_UNITS.with_unit(TIME, "mA")

    def test_scope(self):
        mine = FACTORY_UNITS.with_unit(TIME, "s")
        self.assertEqual(effective(FACTORY_UNITS, None), FACTORY_UNITS)
        self.assertEqual(effective(FACTORY_UNITS, mine), mine)
        self.assertFalse(has_custom_units(FACTORY_UNITS, None))
        self.assertTrue(has_custom_units(FACTORY_UNITS, mine))
        self.assertEqual(mine.differs(FACTORY_UNITS), {TIME})

    def test_from_mapping_is_tolerant(self):
        self.assertEqual(from_mapping({"time": "s", "current": "kA", "x": "y"}), FACTORY_UNITS.with_unit(TIME, "s"))

    def test_dataset_override_is_session_only_field(self):
        d = Dataset("a", "A", "/a.csv")
        self.assertIsNone(d.units_override)


class Sample(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_optional_positive(""), (None, ""))
        self.assertEqual(parse_optional_positive(" 12,4 "), (12.4, ""))
        for bad in ("0", "-1", "abc", "nan", "inf"):
            self.assertEqual(parse_optional_positive(bad)[0], None)
            self.assertTrue(parse_optional_positive(bad)[1])

    def test_state(self):
        e = ElabState()
        e.set_sample_text("active_mass_mg", "12.4")
        e.set_sample_text("surface_area_cm2", "x")
        self.assertEqual(e.sample, SampleValues(12.4, None))
        self.assertIn("surface_area_cm2", e.sample_errors)
        self.assertEqual(e.sample_texts["surface_area_cm2"], "x")
        e.set_sample_text("surface_area_cm2", "")
        self.assertEqual(e.sample_errors, {})
        self.assertTrue(e.valid)                         # sample values never block Process (O-28)
        e.reset_to(e.params)                              # Reset to User Defaults never touches sample values
        self.assertEqual(e.sample.active_mass_mg, 12.4)
        with self.assertRaises(KeyError):
            e.set_sample_text("nope", "1")


if __name__ == "__main__":
    unittest.main()
