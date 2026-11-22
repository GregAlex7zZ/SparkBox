import sys, threading, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.comparison import (CAPACITY, CURRENT, DATASET_PALETTE, DQDV, EXPLORER, NONE, NOT_ANALYZED, OUTDATED,
                            CompareState, FamilyView, can_draw, missing_note, shade, status_text)


class Families(unittest.TestCase):
    def test_defaults(self):
        s = CompareState()
        self.assertEqual(s.family, EXPLORER)                      # opens on Data Explorer
        self.assertEqual(s.views[CAPACITY].y, "discharge_capacity_ah")
        s.family = DQDV
        self.assertEqual(s.view.phase, "both")

    def test_status_texts(self):
        self.assertEqual(status_text(CAPACITY, NOT_ANALYZED), "Not analyzed · analyze in Capacity")
        self.assertIn("run Data Elaboration", status_text(EXPLORER, NONE))
        self.assertEqual(status_text(DQDV, OUTDATED, "cycles 10, 50"), "Outdated · cycles 10, 50")
        self.assertTrue(can_draw(OUTDATED))                       # drawn with a badge
        self.assertFalse(can_draw(NOT_ANALYZED))


class Selection(unittest.TestCase):
    def test_checking_and_colours(self):
        s = CompareState()
        self.assertFalse(s.toggle("c", True, drawable=False))     # not analyzed: cannot be checked
        s.toggle("b", True, True)
        s.toggle("a", True, True)
        ca, cb = s.color("a"), s.color("b")
        self.assertEqual(cb, DATASET_PALETTE[0])
        s.toggle("b", False, True)
        s.toggle("b", True, True)
        self.assertEqual((s.color("a"), s.color("b")), (ca, cb))  # stable, independent of the order
        s.set_color("a", "#000000")
        self.assertEqual(s.color("a"), "#000000")
        s.dataset_closed("a")
        self.assertEqual(s.checked, ["b"])

    def test_messages(self):
        s = CompareState()
        self.assertIn("Open at least two", s.message({})[0])
        kinds = {"a": CURRENT, "b": OUTDATED, "c": NOT_ANALYZED}
        self.assertEqual(s.message(kinds)[0], "Select datasets on the right")
        s.toggle("a", True, True)
        self.assertEqual(s.message(kinds)[0], "Select at least one more dataset")   # notice with one
        s.toggle("b", True, True)
        self.assertEqual(s.message(kinds), ("", ""))
        self.assertEqual(s.drawn(kinds), ["a", "b"])
        kinds["b"] = NOT_ANALYZED                                 # e.g. its result was dropped meanwhile
        self.assertEqual(s.drawn(kinds), ["a"])


class Cycles(unittest.TestCase):
    def test_same_ids_for_every_dataset(self):
        v = FamilyView()
        v.type_cycles("80-100")
        self.assertTrue(v.apply_cycles())
        self.assertEqual(v.cycles_of(list(range(1, 129))), tuple(range(80, 101)))
        self.assertEqual(v.cycles_of(list(range(1, 91))), tuple(range(80, 91)))   # never renumbered or padded
        self.assertEqual(v.cycles_of([1, 2]), ())
        v.type_cycles("x")
        self.assertFalse(v.apply_cycles())
        self.assertEqual(v.cycles, ((80, 100),))
        self.assertIsNone(FamilyView().cycles_of([1, 2]))
        self.assertIn("none of the cycles 120", missing_note(["Ref A"], "120"))

    def test_shades(self):
        s = CompareState()
        s.toggle("a", True, True)
        self.assertEqual(s.cycle_color("a", 0, 3), DATASET_PALETTE[0])
        self.assertNotEqual(s.cycle_color("a", 2, 3), DATASET_PALETTE[0])
        s.cycle_shading = False
        self.assertEqual(s.cycle_color("a", 2, 3), DATASET_PALETTE[0])
        self.assertEqual(shade("#000000", 1.0), "#CCCCCC")       # capped: never white


class DemoFlow(unittest.TestCase):
    def test_explorer_family(self):
        from app.backend import CompareRequest
        from app.demo_backend import DemoBackend
        from app.units import FACTORY_UNITS
        b = DemoBackend(0)
        req = CompareRequest((("a", "el-a"), ("b", "el-b")), "discharge_capacity_ah", "voltage_v", (10,), None, None,
                             FACTORY_UNITS, 800)
        out = b.compare_explorer(req, threading.Event())
        self.assertEqual([o.ds_key for o in out], ["a", "b"])
        for o in out:
            self.assertLessEqual(o.rendered, o.total)
            self.assertGreater(len(o.curve.x), 0)


if __name__ == "__main__":
    unittest.main()
