import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.capacity import *
from app.elaboration import CHARGE_FIRST, SampleValues
from app.explorer import Range, Viewport
from app.units import UnitPrefs


def res(ref=2, mass=12.4, area=None, rid="cap1", elab="e1"):
    return CapResult(rid, elab, 128, CapParams(CHARGE_FIRST, ref, mass, area))


class Columns(unittest.TestCase):
    def test_seventeen_published_columns_in_schema_order(self):
        self.assertEqual(len(CAP_COLUMNS), 17)
        self.assertEqual(CAP_COLUMNS[0].key, "cycle")
        self.assertEqual(CAP_COLUMNS[-1].key, "discharge_energy_mwh_cm2")

    def test_labels_and_units(self):
        u = UnitPrefs()
        self.assertEqual(cap_label("discharge_capacity_ah", u), "Discharge Capacity [mAh]")
        self.assertEqual(cap_label("charge_energy_wh", u.with_unit("energy", "Wh")), "Charge Energy [Wh]")
        self.assertEqual(cap_label("coulombic_efficiency_pct", u), "Coulombic Efficiency [%]")
        self.assertEqual(cap_label("charge_capacity_mah_g", u), "Charge Specific Capacity [mAh/g]")   # never converted
        self.assertEqual(cap_label("discharge_energy_mwh_cm2", u), "Discharge Areal Energy [mWh/cm²]")
        self.assertEqual(cap_label("cycle", u), "Cycle")
        self.assertEqual(cap_label(None, u), "None")

    def test_only_columns_with_values_are_selectable(self):
        with_vals = {"charge_capacity_ah", "discharge_capacity_ah", "coulombic_efficiency_pct", "charge_capacity_mah_g"}
        sel = selectable_columns(with_vals)
        self.assertEqual(sel[0], "cycle")
        self.assertIn("charge_capacity_mah_g", sel)
        self.assertNotIn("charge_capacity_mah_cm2", sel)

    def test_specific_and_areal_columns_are_left_out_without_their_sample_value(self):
        """without Active Material Mass / Surface Area the specific / areal columns are not shown
        in the table or offered for export, and a short tip says how to get them."""
        specific = {c.key for c in CAP_COLUMNS if c.basis == "mass"}
        areal = {c.key for c in CAP_COLUMNS if c.basis == "area"}
        self.assertEqual(len(specific), 4)
        self.assertEqual(len(areal), 4)
        both = shown_columns(res(mass=12.4, area=1.13).params)
        self.assertEqual(both, [c.key for c in CAP_COLUMNS])                       # all 17, table order
        mass_only = shown_columns(res(mass=12.4, area=None).params)
        self.assertTrue(specific <= set(mass_only))
        self.assertFalse(areal & set(mass_only))
        neither = shown_columns(res(mass=None, area=0.0).params)                   # zero counts as not entered
        self.assertEqual(len(neither), 9)
        self.assertEqual(neither[0], "cycle")
        self.assertFalse((specific | areal) & set(neither))
        self.assertEqual(shown_columns(None), [c.key for c in CAP_COLUMNS])
        self.assertEqual(missing_bases(res(mass=None, area=None).params), {"mass", "area"})
        # Comparison: offered when at least one drawn dataset has the Sample value
        self.assertEqual(missing_in_all([]), set())
        self.assertEqual(missing_in_all([res(mass=None).params, res(mass=None).params]), {"mass", "area"})
        self.assertEqual(missing_in_all([res(mass=None).params, res(mass=5.0).params]), {"area"})
        self.assertEqual(missing_in_all([res(mass=None, area=2.0).params, res(mass=5.0).params]), set())
        self.assertEqual(hidden_note(set()), "")
        self.assertEqual(hidden_note({"mass"}), "Tip: enter Active Material Mass in Data Elaboration (Sample) to also "
                                                "get specific capacity and energy.")
        self.assertEqual(hidden_note({"mass", "area"}),
                         "Tip: enter Active Material Mass and Surface Area in Data Elaboration (Sample) to also get "
                         "specific and areal capacity and energy.")


class Diagnostics(unittest.TestCase):
    def test_texts(self):
        self.assertIn("discharge phase", explain("CAPACITY_MISSING_PHASE", "discharge_capacity_ah"))
        self.assertIn("Surface Area", explain("NORMALIZATION_BASIS_MISSING", "charge_energy_mwh_cm2"))
        self.assertIn("Active Material Mass", explain("NORMALIZATION_BASIS_MISSING", "charge_capacity_mah_g"))
        self.assertTrue(explain("C_RATE_CC_UNAVAILABLE", "charge_c_rate").endswith("(C_RATE_CC_UNAVAILABLE)"))
        self.assertEqual(explain("SOMETHING_NEW", "cycle"), "SOMETHING_NEW")          # never invented

    def test_null_is_not_zero(self):
        self.assertIsNone(finite_or_none(None))
        self.assertIsNone(finite_or_none(float("nan")))
        self.assertEqual(finite_or_none(0.0), 0.0)


class Reference(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_reference_cycle(" 2 "), (2, ""))
        for bad in ("0", "-1", "2.5", "", "x"):
            self.assertEqual(parse_reference_cycle(bad), (None, ERR_REFERENCE))

    def test_hint(self):
        self.assertEqual(reference_hint(2, [0, 1, 2]), ("Cycle 2 exists in this result · Default 2", False))
        text, warn = reference_hint(200, [1, 2])
        self.assertTrue(warn)
        self.assertIn("C-rates will not be available", text)
        # The untouched default (cycle 2) on a one-cycle result is explained, not flagged as an error.
        text, warn = reference_hint(2, [1])
        self.assertFalse(warn)
        self.assertIn("C-rates will not be available", text)


class ViewState(unittest.TestCase):
    def test_filter_applies_to_visible_cycles_and_cycle0(self):
        v = CapViewState()
        cyc = list(range(0, 11))
        self.assertEqual(v.visible_cycles(cyc), list(range(1, 11)))     # cycle 0 hidden by default
        v.show_cycle0 = True
        self.assertEqual(v.visible_cycles(cyc)[0], 0)
        v.type_filter("2-4, 9")
        self.assertTrue(v.apply_filter())
        self.assertEqual(v.visible_cycles(cyc), [2, 3, 4, 9])
        self.assertEqual(v.export_cycles(cyc), (2, 3, 4, 9))

    def test_export_rows(self):
        v = CapViewState()
        self.assertIsNone(v.export_cycles([0, 1, 2]))                   # all cycles, cycle 0 included
        v.type_filter("0-1"); v.apply_filter()
        self.assertEqual(v.export_cycles([0, 1, 2]), (0, 1))

    def test_axes(self):
        v = CapViewState()
        allowed = selectable_columns({"charge_capacity_ah", "discharge_capacity_ah", "coulombic_efficiency_pct"})
        self.assertFalse(v.set_axis("y1", "charge_capacity_mah_g", allowed))   # not listed
        self.assertTrue(v.set_axis("y1", "charge_capacity_ah", allowed))
        with self.assertRaises(ValueError):
            v.set_axis("x", None, allowed)
        v.y2 = "charge_capacity_mah_g"
        self.assertTrue(v.fix_axes(allowed))
        self.assertEqual(v.y2, "coulombic_efficiency_pct")

    def test_invalid_filter(self):
        v = CapViewState()
        v.type_filter("9-")
        self.assertFalse(v.can_apply_filter)
        self.assertFalse(v.apply_filter())


class States(unittest.TestCase):
    P = CapParams(CHARGE_FIRST, 2, 12.4, None)

    def test_flow(self):
        s = CapacityState()
        self.assertEqual(s.state(self.P), CapView.NOT_ANALYZED)
        self.assertTrue(s.can_analyze(self.P))
        s.begin(self.P)
        self.assertEqual(s.state(self.P), CapView.ANALYZING)
        self.assertFalse(s.can_analyze(self.P))
        s.update_progress(1.0, "capacity-complete", publishing=True)
        self.assertEqual((s.state(self.P), s.phase), (CapView.PUBLISHING, "Saving result…"))
        s.finish(res())
        self.assertEqual(s.state(self.P), CapView.CURRENT)

    def test_outdated_by_reference_or_sample(self):
        s = CapacityState(result=res())
        s.set_reference_text("3")
        cur = s.params(CHARGE_FIRST, SampleValues(12.4, None))
        self.assertEqual(s.state(cur), CapView.OUTDATED)
        self.assertEqual(s.modified(cur), {"reference_cycle"})
        s.set_reference_text("2")
        cur = s.params(CHARGE_FIRST, SampleValues(10.0, 1.13))
        self.assertEqual(s.modified(cur), {"active_material_mass_mg", "electrode_area_cm2"})

    def test_invalid_reference_blocks(self):
        s = CapacityState()
        s.set_reference_text("0")
        self.assertEqual(s.reference_error, ERR_REFERENCE)
        self.assertIsNone(s.params(CHARGE_FIRST, SampleValues()))
        self.assertFalse(s.can_analyze(None))

    def test_previous_result_kept_on_cancel_and_failure(self):
        s = CapacityState(result=res())
        s.begin(self.P)
        self.assertEqual(s.state(self.P), CapView.REANALYZING)
        self.assertTrue(s.request_cancel())
        self.assertEqual(s.state(self.P), CapView.CANCELLING)
        s.cancelled()
        self.assertEqual((s.state(self.P), s.result.result_id), (CapView.CANCELLED, "cap1"))
        s.begin(self.P); s.failed("disk full")
        self.assertEqual((s.state(self.P), s.message), (CapView.FAILED, "disk full"))

    def test_new_elaboration_drops_result(self):
        s = CapacityState(result=res())
        s.view.selected_cycle = 5
        s.view.viewport = Viewport(Range(0, 1))
        self.assertTrue(s.elaboration_replaced())
        self.assertEqual((s.result, s.state(self.P), s.view.selected_cycle), (None, CapView.ELAB_CHANGED, None))
        s.begin(self.P); s.finish(res(rid="cap2", elab="e2"))
        self.assertEqual(s.state(self.P), CapView.CURRENT)
        self.assertFalse(s.elab_changed)

    def test_never_analyzed_then_new_elaboration(self):
        s = CapacityState()
        self.assertFalse(s.elaboration_replaced())
        self.assertEqual(s.state(self.P), CapView.NOT_ANALYZED)

    def test_pills_cover_every_state(self):
        self.assertEqual(set(PILL), set(CapView))


class ChartHelpers(unittest.TestCase):
    def test_fit_range(self):
        lo, hi = fit_range([1.0, None, float("nan"), 3.0])
        self.assertAlmostEqual(lo, 0.92); self.assertAlmostEqual(hi, 3.08)
        self.assertIsNone(fit_range([None], []))
        self.assertEqual(fit_range([2.0]), (2.0 - 0.08, 2.0 + 0.08))

    def test_gap_breaks(self):
        existing = [0, 1, 2, 3, 4, 5, 7, 8, 9]          # cycle 6 does not exist
        self.assertEqual(gap_breaks([1, 2, 3], existing), [])
        self.assertEqual(gap_breaks([2, 3, 4, 9], existing), [2])  # filter hid 5, 7, 8
        self.assertEqual(gap_breaks([5, 7], existing), [])         # 6 never existed: keep the line


if __name__ == "__main__":
    unittest.main()
