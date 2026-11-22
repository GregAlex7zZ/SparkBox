import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.formatting import fmt_int, fmt_time, sig4


class Formatting(unittest.TestCase):
    def test_sig4_basic(self):
        self.assertEqual(sig4(1250.0), "1250")
        self.assertEqual(sig4(3.45214), "3.452")
        self.assertEqual(sig4(0.012345), "0.01235")
        self.assertEqual(sig4(0.0), "0")
        self.assertEqual(sig4(-3.45214), "-3.452")

    def test_sig4_rounding_up_keeps_four_digits(self):
        self.assertEqual(sig4(9.9996), "10.00")
        self.assertEqual(sig4(99996.0), "99996")      # integer digits are never rounded away (SPEC O-12)

    def test_time_is_not_rounded_to_four_digits(self):
        self.assertEqual(fmt_time(12.01), "12.01")
        self.assertEqual(fmt_time(12.010000000000002), "12.01")
        self.assertNotEqual(fmt_time(12.0004), sig4(12.0004))
        self.assertEqual(fmt_time(0.0), "0")

    def test_adjacent_rows_stay_distinguishable(self):
        dt_h = 1 / 3600
        a, b = 450.0, 450.0 + dt_h
        self.assertEqual(sig4(a), sig4(b))
        self.assertNotEqual(fmt_time(a), fmt_time(b))

    def test_fmt_int(self):
        self.assertEqual(fmt_int(1284330), "1,284,330")


if __name__ == "__main__":
    unittest.main()
