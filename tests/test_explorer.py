import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.explorer import *
from app.units import UnitPrefs

R = Range


class Columns(unittest.TestCase):
    def test_all_twelve_engine_fields(self):
        self.assertEqual([c.key for c in COLUMNS], ["time_s", "current_a", "voltage_v", "charge_capacity_ah",
                         "discharge_capacity_ah", "charge_energy_wh", "discharge_energy_wh", "cycle", "half_cycle",
                         "rest", "step", "source_row"])

    def test_labels_follow_units(self):
        u = UnitPrefs()
        self.assertEqual(axis_label("time_s", u), "Time [h]")
        self.assertEqual(axis_label("charge_capacity_ah", u), "Charge Capacity [mAh]")
        self.assertEqual(axis_label("discharge_energy_wh", u), "Discharge Energy [mWh]")
        self.assertEqual(axis_label("cycle", u), "Cycle")
        self.assertEqual(axis_label(None, u), "None")
        self.assertEqual(axis_label("time_s", u.with_unit("time", "s")), "Time [s]")

    def test_only_time_is_ascending(self):
        self.assertTrue(ascending_x("time_s"))
        self.assertFalse(ascending_x("voltage_v"))
        self.assertFalse(ascending_x("charge_capacity_ah"))


class Filter(unittest.TestCase):
    def test_grammar(self):
        self.assertEqual(parse_cycle_filter(""), ())
        self.assertEqual(parse_cycle_filter("  "), ())
        self.assertEqual(parse_cycle_filter("1"), ((1, 1),))
        self.assertEqual(parse_cycle_filter("1-100"), ((1, 100),))
        self.assertEqual(parse_cycle_filter("1,6,9"), ((1, 1), (6, 6), (9, 9)))
        self.assertEqual(parse_cycle_filter("1-5, 9 ,12 - 20"), ((1, 5), (9, 9), (12, 20)))
        self.assertEqual(parse_cycle_filter("0"), ((0, 0),))

    def test_errors(self):
        for text, part in [("9-", "has no end"), ("-3", "has no start"), ("10-5", "reversed"), ("1,,2", "empty item"),
                           ("1,", "empty item"), ("abc", "not a cycle"), ("1.5", "not a cycle"), ("-", "not a cycle"),
                           ("1-2-3", "not a cycle")]:
            with self.assertRaises(FilterError, msg=text) as cm:
                parse_cycle_filter(text)
            self.assertIn(part, str(cm.exception))
        with self.assertRaises(FilterError) as cm:
            parse_cycle_filter("10-5")
        self.assertIn("5-10", str(cm.exception))

    def test_resolve_only_existing_ids(self):
        avail = [0, 1, 2, 3, 5, 6, 128]
        self.assertIsNone(resolve_cycles((), avail))
        self.assertEqual(resolve_cycles(((1, 4),), avail), (1, 2, 3))           # 4 does not exist: not invented
        self.assertEqual(resolve_cycles(((200, 210),), avail), ())              # no match
        self.assertEqual(resolve_cycles(((6, 6), (1, 1), (1, 2)), avail), (1, 2, 6))

    def test_text(self):
        self.assertEqual(filter_text(((1, 5), (9, 9))), "1-5, 9")
        self.assertEqual(filter_text(()), "all")
        self.assertEqual(describe_available(list(range(1, 129))), "1–128")
        self.assertEqual(describe_available([0, 1, 5]), "0–5 (some numbers missing)")
        self.assertEqual(describe_available([]), "none")


class Coverage(unittest.TestCase):
    def test_format(self):
        self.assertEqual(format_coverage(2500, 125000), "2,500 / 125,000 — 2.0%")
        self.assertEqual(format_coverage(650, 650), "650 / 650 — 100%")
        self.assertEqual(format_coverage(0, 0), "0 / 0 — —")
        self.assertEqual(format_coverage(None, None), "— / —")
        self.assertEqual(format_coverage(99999, 100000), "99,999 / 100,000 — 99.9%")   # never 100.0 %
        self.assertEqual(format_coverage(1, 10_000_000), "1 / 10,000,000 — <0.1%")
        self.assertEqual(format_coverage(0, 5), "0 / 5 — 0%")
        self.assertEqual(format_coverage(12480, 197528), "12,480 / 197,528 — 6.3%")

    def test_inconsistent_counts_raise(self):
        with self.assertRaises(ValueError):
            format_coverage(11, 10)


class Geometry(unittest.TestCase):
    X, Y1, Y2 = R(0, 100), R(0, 10), R(-1000, 1000)

    def test_box_zoom_same_pixel_band_on_both_y(self):
        vp = box_zoom(10, 25, 50, 75, 100, 100, self.X, self.Y1, self.Y2)
        self.assertEqual(vp.x, R(10, 50))
        self.assertAlmostEqual(vp.y1.lo, 2.5); self.assertAlmostEqual(vp.y1.hi, 7.5)
        self.assertAlmostEqual(vp.y2.lo, -500); self.assertAlmostEqual(vp.y2.hi, 500)

    def test_thin_rectangles(self):
        flat = box_zoom(10, 50, 60, 54, 100, 100, self.X, self.Y1, self.Y2)          # 4 px tall -> X only
        self.assertEqual((flat.x, flat.y1, flat.y2), (R(10, 60), self.Y1, self.Y2))
        tall = box_zoom(30, 10, 35, 90, 100, 100, self.X, self.Y1, self.Y2)          # 5 px wide -> Y only
        self.assertEqual(tall.x, self.X)
        self.assertAlmostEqual(tall.y1.lo, 1.0); self.assertAlmostEqual(tall.y1.hi, 9.0)
        self.assertIsNone(box_zoom(10, 10, 13, 12, 100, 100, self.X, self.Y1, self.Y2))   # a click

    def test_box_zoom_reversed_drag_and_clamp(self):
        a = box_zoom(50, 75, 10, 25, 100, 100, self.X, self.Y1, None)
        b = box_zoom(10, 25, 50, 75, 100, 100, self.X, self.Y1, None)
        self.assertEqual(a, b)
        self.assertIsNone(a.y2)
        c = box_zoom(-20, -20, 50, 50, 100, 100, self.X, self.Y1, None)
        self.assertEqual(c.x, R(0, 50))

    def test_wheel_zoom_keeps_anchor(self):
        r = wheel_zoom(R(0, 100), 50, 1)
        self.assertAlmostEqual(r.lo, 10); self.assertAlmostEqual(r.hi, 90)
        back = wheel_zoom(r, 50, -1)
        self.assertAlmostEqual(back.lo, 0); self.assertAlmostEqual(back.hi, 100)
        edge = wheel_zoom(R(0, 100), 0, 1)
        self.assertAlmostEqual(edge.lo, 0); self.assertAlmostEqual(edge.hi, 80)

    def test_wheel_zoom_refuses_degenerate(self):
        r = R(1.0, 1.0 + 1e-13)
        self.assertEqual(wheel_zoom(r, 1.0, 5), r)

    def test_pan(self):
        self.assertEqual(pan_x(R(0, 100), 10, 100), R(-10, 90))
        self.assertEqual(pan_y(R(0, 10), 10, 100), R(1, 11))

    def test_parse_range(self):
        self.assertEqual(parse_range("", ""), (None, ""))
        self.assertEqual(parse_range("−1500", "1500"), (R(-1500, 1500), ""))
        self.assertEqual(parse_range("2", "1")[1], "Min must be smaller than Max.")
        self.assertEqual(parse_range("a", "1")[1], "Enter a number in both Min and Max.")
        self.assertEqual(parse_range("1", "")[1], "Enter a number in both Min and Max.")
        with self.assertRaises(ValueError):
            R(1, 1)

    def test_axis_bounds_use_at_most_four_significant_digits(self):
        self.assertEqual(format_axis_bound(12345.678), "1.235e+04")
        self.assertEqual(format_axis_bound(-0.001234567), "−0.001235")
        self.assertEqual(format_axis_bound(3.1), "3.1")


class StateMachine(unittest.TestCase):
    def ready(self):
        s = ExplorerState()
        v = s.request()
        self.assertEqual(s.phase, Phase.LOADING)
        self.assertTrue(s.accept(v, 12480, 197528))
        return s

    def test_loading_then_ready_then_updating(self):
        s = self.ready()
        self.assertEqual(s.phase, Phase.READY)
        v = s.request()
        self.assertEqual(s.phase, Phase.UPDATING)
        self.assertTrue(s.accept(v, 10, 10))
        self.assertEqual(s.displayed_coverage(), (10, 10))

    def test_stale_responses_discarded(self):
        s = self.ready()
        old = s.request()
        new = s.request()
        self.assertFalse(s.accept(old, 1, 1))
        self.assertTrue(s.accept(new, 2, 2))

    def test_empty_and_no_match(self):
        s = self.ready()
        v = s.request()
        s.accept(v, 0, 0)
        self.assertEqual(s.phase, Phase.EMPTY)
        v = s.request()
        s.accept(v, 0, 0, filter_matched=False)
        self.assertEqual(s.phase, Phase.NO_MATCH)
        self.assertFalse(s.can_render)

    def test_failed_request_keeps_last_view(self):
        s = self.ready()
        v = s.request()
        self.assertTrue(s.reject(v, "boom"))
        self.assertEqual((s.phase, s.view_error, s.coverage), (Phase.READY, "boom", (12480, 197528)))

    def test_axis_change_resets_that_axis_only(self):
        s = self.ready()
        s.set_viewport(Viewport(R(0, 1), R(2, 3), R(4, 5)))
        self.assertTrue(s.set_axis("y1", "charge_capacity_ah"))
        self.assertEqual(s.viewport, Viewport(R(0, 1), None, R(4, 5)))
        self.assertFalse(s.set_axis("y1", "charge_capacity_ah"))
        self.assertTrue(s.set_axis("y2", None))
        with self.assertRaises(ValueError):
            s.set_axis("x", None)
        with self.assertRaises(ValueError):
            s.set_axis("y1", "nope")
        s.set_viewport(Viewport(R(0, 1), R(2, 3), R(4, 5)))
        self.assertIsNone(s.viewport.y2)                       # no Y2 range without a Y2 column

    def test_filter_flow(self):
        s = self.ready()
        s.type_filter("1-5, 9-")
        self.assertIn("has no end", s.filter_error)
        self.assertFalse(s.can_apply_filter)
        self.assertFalse(s.apply_filter())
        s.type_filter("1-5, 9")
        s.set_viewport(Viewport(R(0, 1)))
        self.assertTrue(s.apply_filter())
        self.assertEqual((s.applied, s.viewport), (((1, 5), (9, 9)), Viewport()))
        self.assertFalse(s.can_apply_filter)                  # same as applied
        self.assertTrue(s.clear_filter())
        self.assertEqual((s.applied, s.filter_input), ((), ""))
        self.assertFalse(s.can_clear_filter)

    def test_reset_view_only_by_button(self):
        s = self.ready()
        s.set_viewport(Viewport(R(0, 1)))
        self.assertTrue(s.reset_view())
        self.assertTrue(s.viewport.auto)
        self.assertFalse(s.reset_view())

    def test_colors_and_legend(self):
        s = self.ready()
        self.assertTrue(s.set_color("y1", "#123abc"))
        self.assertEqual(s.y1_color, "#123ABC")
        self.assertFalse(s.set_color("y2", "red"))
        s.reset_colors()
        self.assertEqual((s.y1_color, s.y2_color), (CHART["y1"], CHART["y2"]))
        self.assertTrue(s.set_legend(False))
        self.assertTrue(s.set_point_tracking(False))
        self.assertFalse(s.point_tracking)

    def test_render_all_points_locks_and_returns(self):
        s = self.ready()
        s.set_viewport(Viewport(R(0, 1)))
        self.assertTrue(s.begin_render())
        self.assertTrue(s.locked)
        for call in (lambda: s.set_axis("y1", "cycle"), lambda: s.set_viewport(Viewport()), lambda: s.set_color("y1", "#000000"),
                     lambda: s.set_legend(False), lambda: s.set_style("markers")):
            self.assertFalse(call())
        s.type_filter("3")
        self.assertFalse(s.apply_filter())
        self.assertFalse(s.accept(s.version, 1, 1))           # no view responses while locked
        s.render_progress(0.42, 1_204_331, 2_866_290)
        self.assertTrue(s.render_finished(197528, 197528))
        self.assertEqual((s.mode, s.displayed_coverage()), (Mode.FLAT, (197528, 197528)))
        self.assertFalse(s.can_apply_filter)
        self.assertTrue(s.leave_flat())
        self.assertEqual((s.mode, s.viewport, s.coverage), (Mode.INTERACTIVE, Viewport(R(0, 1)), (12480, 197528)))

    def test_partial_render_is_a_failure(self):
        s = self.ready()
        s.begin_render()
        self.assertFalse(s.render_finished(10, 11))
        self.assertEqual(s.mode, Mode.INTERACTIVE)
        self.assertIn("Not every point was drawn", s.render_error)

    def test_cancel_and_fail_restore_interactive(self):
        s = self.ready()
        s.begin_render("out.png")
        s.render_cancelled()
        self.assertEqual((s.mode, s.save_path, s.render_error), (Mode.INTERACTIVE, None, ""))
        s.begin_render()
        s.render_failed("disk full")
        self.assertEqual((s.mode, s.render_error), (Mode.INTERACTIVE, "disk full"))
        s.dismiss_render_error()
        self.assertEqual(s.render_error, "")

    def test_cannot_render_before_a_view(self):
        s = ExplorerState()
        self.assertFalse(s.can_render)
        self.assertFalse(s.begin_render())
        self.assertFalse(s.can_save)

    def test_new_result_resets_view_keeps_choices(self):
        s = self.ready()
        s.set_axis("y1", "charge_capacity_ah")
        s.type_filter("1-5"); s.apply_filter()
        s.set_color("y2", "#000000"); s.set_legend(False)
        s.set_viewport(Viewport(R(0, 1)))
        s.begin_render(); s.render_finished(5, 5)
        s.result_replaced()
        self.assertEqual((s.mode, s.viewport, s.phase, s.has_view), (Mode.INTERACTIVE, Viewport(), Phase.LOADING, False))
        self.assertEqual((s.y1, s.applied, s.y2_color, s.legend), ("charge_capacity_ah", ((1, 5),), "#000000", False))


if __name__ == "__main__":
    unittest.main()
