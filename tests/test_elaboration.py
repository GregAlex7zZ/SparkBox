import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.elaboration import *


def res(params=FACTORY_DEFAULTS):
    return ElabResult("r1", 128, 256, 1024, 31241, params)


class Parsing(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(parse_number(" 1,5 "), (1.5, ""))
        self.assertEqual(parse_number("0"), (0.0, ""))

    def test_bad(self):
        for t in ("", "-5", "abc", "nan", "inf", "1e999"):
            v, e = parse_number(t)
            self.assertIsNone(v, t)
            self.assertEqual(e, ERR_NONNEG)


class Views(unittest.TestCase):
    def test_first_run(self):
        s = ElabState()
        self.assertIs(s.view(), ElabView.NOT_PROCESSED)
        s.set_number_text("rest_current_threshold_ma", "-5")
        self.assertIs(s.view(), ElabView.INVALID)
        self.assertFalse(s.can_process())
        s.set_number_text("rest_current_threshold_ma", "0.5")
        self.assertIs(s.view(), ElabView.NOT_PROCESSED)
        self.assertEqual(s.params.rest_current_threshold_ma, 0.5)

    def test_run_to_current_then_outdated(self):
        s = ElabState(); s.begin()
        self.assertIs(s.view(), ElabView.PROCESSING)
        s.update_progress(0.5, "x")
        s.update_progress(1.0, "pub", publishing=True)
        self.assertIs(s.view(), ElabView.PUBLISHING)
        s.finish(res(s.params))
        self.assertIs(s.view(), ElabView.CURRENT)
        s.set_order(DISCHARGE_FIRST)
        self.assertIs(s.view(), ElabView.OUTDATED)
        s.set_order(CHARGE_FIRST)
        self.assertIs(s.view(), ElabView.CURRENT)           # back to the applied value: not outdated

    def test_cancel_keeps_previous_result(self):
        s = ElabState(); s.finish(res())
        s.set_order(DISCHARGE_FIRST); s.begin()
        self.assertTrue(s.request_cancel())
        s.update_progress(0.9, "late")                        # late progress must not undo Cancelling
        self.assertIs(s.view(), ElabView.CANCELLING)
        s.cancelled()
        self.assertIs(s.view(), ElabView.CANCELLED)
        self.assertEqual(s.result.params, FACTORY_DEFAULTS)    # previous result untouched
        self.assertTrue(s.has_result)

    def test_failure_message_and_retry(self):
        s = ElabState(); s.begin(); s.failed("library said no")
        self.assertIs(s.view(), ElabView.FAILED)
        self.assertEqual(s.message, "library said no")
        self.assertFalse(s.has_result)
        s.begin()                                              # retry clears outcome
        self.assertIs(s.view(), ElabView.PROCESSING)
        self.assertEqual(s.message, "")

    def test_cannot_begin_twice_or_invalid(self):
        s = ElabState(); s.begin()
        with self.assertRaises(ValueError):
            s.begin()
        t = ElabState(); t.set_number_text("cc_relative_tolerance_pct", "x")
        with self.assertRaises(ValueError):
            t.begin()

    def test_cancel_only_when_active(self):
        self.assertFalse(ElabState().request_cancel())

    def test_invalidate_on_reimport(self):
        s = ElabState(); s.finish(res()); s.invalidate_result()
        self.assertFalse(s.has_result)
        self.assertIs(s.view(), ElabView.NOT_PROCESSED)


class Modified(unittest.TestCase):
    def test_baseline_is_result_params_else_defaults(self):
        s = ElabState()
        s.set_number_text("cc_relative_tolerance_pct", "2")
        self.assertEqual(s.modified(FACTORY_DEFAULTS), {"cc_relative_tolerance_pct"})
        s.finish(res(s.params))
        self.assertEqual(s.modified(FACTORY_DEFAULTS), set())
        s.set_order(DISCHARGE_FIRST)
        self.assertEqual(s.modified(FACTORY_DEFAULTS), {"cycle_order"})

    def test_reset(self):
        s = ElabState(); s.set_order(DISCHARGE_FIRST); s.set_number_text("cv_absolute_tolerance_mv", "q")
        self.assertTrue(s.can_reset(FACTORY_DEFAULTS))
        s.reset_to(FACTORY_DEFAULTS)
        self.assertFalse(s.can_reset(FACTORY_DEFAULTS))
        self.assertTrue(s.valid)


if __name__ == "__main__":
    unittest.main()

