import sys, unittest, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# display_value lives in a Qt module; test the pure rule without importing Qt by exec'ing its source slice.
src = (Path(__file__).resolve().parent.parent / "app/widgets/elab_table.py").read_text(encoding="utf-8")
start = src.index("def display_value"); end = src.index("class ElabModel")
from app.formatting import fmt_time, sig4
ns = {"fmt_time": fmt_time, "sig4": sig4, "NULL": "—"}
exec(src[start:end], ns)
display_value, copy_value = ns["display_value"], ns["copy_value"]


class Display(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(display_value("float", False, 1.23456), "1.235")
        self.assertEqual(display_value("float", True, 12.010000000000002), "12.01")     # time not rounded to 4 digits
        self.assertEqual(display_value("float", False, None), "—")
        self.assertEqual(display_value("bool", False, True), "Yes")
        self.assertEqual(display_value("bool", False, False), "No")
        self.assertEqual(display_value("int", False, 3), "3")
        self.assertEqual(display_value("text", False, "CC"), "CC")

    def test_copy_full_precision(self):
        self.assertEqual(copy_value("float", 1.23456789), "1.23456789")
        self.assertEqual(copy_value("float", None), "")
        self.assertEqual(copy_value("bool", True), "Yes")


if __name__ == "__main__":
    unittest.main()

