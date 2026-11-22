import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.elaboration import ElabResult
from app.state import (Dataset, Section, SessionState, Status, fallback_section)


def ds(i, st=Status.LOADED):
    d = Dataset(str(i), f"d{i}", f"/x/{i}.csv")
    if st is Status.ELABORATED:
        d.elab.finish(ElabResult("r", 1, 2, 3, 4, d.elab.params))
    return d


class UnlockRules(unittest.TestCase):
    def test_nothing_loaded(self):
        a = SessionState().availability()
        self.assertEqual([s for s, v in a.items() if v], [Section.DATA])

    def test_loaded_unlocks_elaboration_only(self):
        s = SessionState(); s.add(ds(1))
        on = {k for k, v in s.availability().items() if v}
        self.assertEqual(on, {Section.DATA, Section.ELABORATION})

    def test_elaborated_unlocks_results_not_comparison(self):
        s = SessionState(); s.add(ds(1, Status.ELABORATED))
        a = s.availability()
        for sec in (Section.EXPLORER, Section.CAPACITY, Section.DCIR, Section.DQDV):
            self.assertTrue(a[sec])
        self.assertFalse(a[Section.COMPARISON])

    def test_comparison_needs_two_elaborated_any_active(self):
        s = SessionState(); s.add(ds(1, Status.ELABORATED)); s.add(ds(2, Status.ELABORATED))
        self.assertTrue(s.availability()[Section.COMPARISON])
        s.add(ds(3))  # active is a merely loaded dataset
        a = s.availability()
        self.assertTrue(a[Section.COMPARISON])
        self.assertFalse(a[Section.EXPLORER])

    def test_switch_to_unelaborated_locks_results_and_falls_back(self):
        s = SessionState(); s.add(ds(1, Status.ELABORATED)); s.add(ds(2))
        s.set_active("2")
        self.assertEqual(fallback_section(Section.EXPLORER, s.availability()), Section.ELABORATION)

    def test_invalidate_locks_downstream(self):
        s = SessionState(); s.add(ds(1, Status.ELABORATED))
        s.invalidate_elaboration("1")
        a = s.availability()
        self.assertFalse(a[Section.EXPLORER]); self.assertTrue(a[Section.ELABORATION])

    def test_comparison_lost_falls_back_to_highest_non_comparison(self):
        s = SessionState(); s.add(ds(1, Status.ELABORATED)); s.add(ds(2, Status.ELABORATED))
        s.invalidate_elaboration("2")
        self.assertEqual(fallback_section(Section.COMPARISON, s.availability()), Section.ELABORATION)

    def test_close_active_picks_neighbour_or_none(self):
        s = SessionState(); s.add(ds(1)); s.add(ds(2))
        s.remove("2"); self.assertEqual(s.active_id, "1")
        s.remove("1"); self.assertIsNone(s.active_id)

    def test_fallback_keeps_current_when_available(self):
        s = SessionState(); s.add(ds(1, Status.ELABORATED))
        self.assertEqual(fallback_section(Section.CAPACITY, s.availability()), Section.CAPACITY)


class ResultKeepsSectionsUnlocked(unittest.TestCase):
    def test_outdated_running_failed_keep_unlock(self):
        s = SessionState(); d = ds(1, Status.ELABORATED); s.add(d)
        d.elab.set_order("discharge_charge")                 # outdated
        self.assertTrue(s.availability()[Section.CAPACITY])
        d.elab.begin()                                        # reprocessing
        self.assertEqual(s.running_id(), "1")
        self.assertTrue(s.availability()[Section.CAPACITY])
        d.elab.failed("x")
        self.assertIsNone(s.running_id())
        self.assertTrue(s.availability()[Section.CAPACITY])

    def test_failed_first_run_does_not_unlock(self):
        s = SessionState(); d = ds(1); s.add(d); d.elab.begin(); d.elab.failed("x")
        self.assertFalse(s.availability()[Section.CAPACITY])

    def test_reimport_locks_again(self):
        s = SessionState(); s.add(ds(1, Status.ELABORATED)); s.invalidate_elaboration("1")
        self.assertFalse(s.availability()[Section.CAPACITY])


if __name__ == "__main__":
    unittest.main()

