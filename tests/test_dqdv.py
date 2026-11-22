import sys, threading, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.dqdv import (CYCLE_PALETTE, DqdvParams, DqdvResult, DqdvState, DqdvView, DqdvViewState, ERR_CYCLE_ZERO,
                      ERR_CYCLES_REQUIRED, cycles_label, dqdv_unit, parse_analysis_cycles, pill, sg_errors,
                      unavailable_text)
from app.elaboration import Outcome, Run


def res(cycles=(1, 10), smoothing=False, fp="", corrected=False, unavailable=()):
    return DqdvResult("q1", "el1", DqdvParams(cycles, smoothing, 5, 2), 4, 100, unavailable, fp, 0, corrected)


class Parameters(unittest.TestCase):
    def test_cycles_required_and_grammar(self):
        self.assertEqual(parse_analysis_cycles(""), (None, ERR_CYCLES_REQUIRED))
        self.assertEqual(parse_analysis_cycles("1, 10, 50-52")[0], (1, 10, 50, 51, 52))
        self.assertEqual(parse_analysis_cycles("0-3"), (None, ERR_CYCLE_ZERO))
        self.assertTrue(parse_analysis_cycles("5-2")[1])
        self.assertEqual(parse_analysis_cycles("1-200", [0, 1, 2, 3])[0], (1, 2, 3))
        self.assertTrue(parse_analysis_cycles("300", [1, 2])[1])

    def test_sg(self):
        self.assertEqual(sg_errors(5, 2), {})
        self.assertIn("sg_window", sg_errors(4, 2))               # even
        self.assertIn("sg_window", sg_errors(3, 3))               # not > order
        self.assertIn("sg_order", sg_errors(5, 5))
        self.assertEqual(sg_errors(1, 0), {})

    def test_sg_checked_only_with_smoothing(self):
        s = DqdvState(cycles_text="1")
        s.window_text = "4"
        self.assertEqual(s.errors(), {})
        s.smoothing = True
        self.assertIn("sg_window", s.errors())
        self.assertIsNone(s.params())

    def test_params(self):
        s = DqdvState(cycles_text="1, 3", smoothing=True, window_text="7", order_text="3")
        self.assertEqual(s.params(), DqdvParams((1, 3), True, 7, 3))
        self.assertEqual(s.params().method, "interpolated_gradient")          # the classic Gregorithm default


class States(unittest.TestCase):
    def test_lifecycle(self):
        s = DqdvState(cycles_text="1, 10")
        p = s.params()
        self.assertIs(s.state(p), DqdvView.NOT_ANALYZED)
        self.assertEqual(s.analyze_blocker(p), "")
        self.assertIn("cycles", DqdvState().errors())
        s.begin(p)
        self.assertIs(s.state(p), DqdvView.ANALYZING)
        s.update_progress(1.0, "Publishing", publishing=True)
        self.assertIs(s.state(p), DqdvView.PUBLISHING)
        s.finish(res())
        self.assertIs(s.state(p), DqdvView.CURRENT)
        s.cycles_text = "1, 10, 50"
        self.assertIs(s.state(s.params()), DqdvView.OUTDATED)
        self.assertEqual(s.modified(s.params()), {"cycles"})
        s.begin(s.params())
        self.assertIs(s.state(s.params()), DqdvView.REANALYZING)   # previous result still shown
        self.assertTrue(s.request_cancel())
        s.cancelled()
        self.assertIs(s.state(s.params()), DqdvView.CANCELLED)

    def test_dcir_spans_make_outdated(self):
        s = DqdvState(cycles_text="1, 10")
        s.finish(res(fp="a"))
        self.assertIs(s.state(s.params(), "a"), DqdvView.CURRENT)
        self.assertIs(s.state(s.params(), "b"), DqdvView.OUTDATED)

    def test_smoothing_window_only_matters_when_on(self):
        s = DqdvState(cycles_text="1, 10")
        s.finish(res())
        s.window_text = "9"
        self.assertIs(s.state(s.params()), DqdvView.CURRENT)

    def test_elaboration_replaced_and_correction_pill(self):
        s = DqdvState(cycles_text="1")
        s.finish(res(cycles=(1,)))
        self.assertTrue(s.elaboration_replaced())
        self.assertIs(s.state(s.params()), DqdvView.ELAB_CHANGED)
        s.adopt(res(cycles=(1,), corrected=True))
        self.assertEqual(pill(s.state(s.params()), s.result)[0], "DCIR corrected")

    def test_load_from_result(self):
        s = DqdvState()
        s.finish(res(cycles=(1, 2, 3, 9)))
        s.load_from_result()
        self.assertEqual(s.cycles_text, "1-3, 9")
        self.assertEqual(cycles_label((5,)), "5")


class View(unittest.TestCase):
    def test_plot_choice_and_fallback(self):
        v = DqdvViewState()
        self.assertEqual(v.effective_plot(False), "raw")          # smoothing not calculated
        self.assertEqual(v.kinds(True), ("smoothed",))
        v.plot = "both"
        self.assertEqual(v.kinds(True), ("raw", "smoothed"))
        self.assertEqual(v.kinds(False), ("raw",))

    def test_shown_filter(self):
        v = DqdvViewState()
        self.assertEqual(v.visible_cycles([1, 10, 50]), [1, 10, 50])
        v.type_shown("1-20")
        self.assertTrue(v.apply_shown())
        self.assertEqual(v.visible_cycles([1, 10, 50]), [1, 10])
        v.type_shown("x")
        self.assertFalse(v.apply_shown())
        self.assertEqual(v.visible_cycles([1, 10, 50]), [1, 10])  # previous filter kept
        v.phase = "charge"
        self.assertEqual(v.visible_phases(), ("charge",))

    def test_colors_same_or_different(self):
        v, an = DqdvViewState(), [1, 10, 50]
        self.assertEqual(v.color(10, "discharge", an), CYCLE_PALETTE[1])
        v.set_color(10, "charge", "#111111")
        self.assertEqual(v.color(10, "discharge", an), "#111111")  # same colour by default
        v.same_color = False
        v.set_color(10, "discharge", "#222222")
        self.assertEqual((v.color(10, "charge", an), v.color(10, "discharge", an)), ("#111111", "#222222"))
        v.reset_colors()
        self.assertEqual(v.color(10, "charge", an), CYCLE_PALETTE[1])

    def test_style(self):
        v, an = DqdvViewState(), list(range(1, 13))
        st = v.trace_style(1, "discharge", "smoothed", an)
        self.assertEqual((st.dash, st.alpha, st.symbol), ("solid", 1.0, None))   # same style for both phases
        self.assertEqual(v.trace_style(11, "charge", "smoothed", an).color, CYCLE_PALETTE[0])
        self.assertEqual(v.trace_style(11, "charge", "smoothed", an).symbol, "o")  # 11th cycle: colour repeats, marker
        v.plot = "both"
        raw = v.trace_style(1, "charge", "raw", an)
        self.assertEqual(raw.alpha, 0.5)
        self.assertLess(raw.width, v.line_width)
        v.dash["discharge"] = "dash"
        self.assertEqual(v.trace_style(1, "discharge", "smoothed", an).dash, "dash")

    def test_texts(self):
        self.assertEqual(dqdv_unit("mAh"), "mAh/V")
        self.assertEqual(unavailable_text(()), "")
        self.assertEqual(unavailable_text(((100, "discharge", "DQDV_NO_VALID_SPAN"),)), "1 curve not available")


class DemoFlow(unittest.TestCase):
    def test_analyze_and_read(self):
        from app.demo_backend import DemoBackend
        from app.units import FACTORY_UNITS
        b, ev = DemoBackend(0), threading.Event()
        p = DqdvParams((1, 10, 100), True, 7, 2)
        r = b.analyze_dqdv("ds", "el1", p, None, lambda pr: None, ev)
        self.assertEqual(r.params, p)
        self.assertEqual(r.unavailable, ((100, "discharge", "DQDV_NO_VALID_SPAN"),))
        curves = b.dqdv_curves(r.result_id, FACTORY_UNITS)
        self.assertEqual(sorted({(c.cycle, c.phase) for c in curves}), [(1, "charge"), (1, "discharge"), (10, "charge"),
                                                                        (10, "discharge"), (100, "charge")])
        ch = next(c for c in curves if c.cycle == 1 and c.phase == "charge")
        self.assertEqual(len(ch.segments), 2)                     # two monotonic spans, never joined
        self.assertIsNotNone(ch.segments[0].smoothed)
        self.assertEqual(r.n_points, sum(len(sg.v) for c in curves for sg in c.segments))



class Methods(unittest.TestCase):
    """dQ/dV methods (finite differences, classic interpolated gradient, Flores & Clark histograms) and the
    classic Gregorithm settings (E min / E max, interpolation points as count or %, edge trim)."""

    def test_points_follow_the_classic_spec(self):
        from app.dqdv import parse_points, points_text
        self.assertEqual(parse_points("10%"), (("percent", 10.0), ""))
        self.assertEqual(parse_points("0,15"), (("percent", 15.0), ""))           # <= 1 means a fraction
        self.assertEqual(parse_points("200"), (("count", 200.0), ""))
        for bad in ("2", "2.5", "0%", "101%", "abc", ""):
            self.assertIsNone(parse_points(bad)[0], bad)
        self.assertEqual(points_text("percent", 10.0), "10%")
        self.assertEqual(points_text("count", 200.0), "200")

    def test_relevance_comes_from_the_gregorithm_descriptor(self):
        from app.dqdv import relevant
        from app.dqdv import METHOD_CHOICES
        self.assertEqual([m for m, _t in METHOD_CHOICES], ["interpolated_gradient", "histogram", "histogram_weighted"])
        self.assertTrue(relevant("points", "interpolated_gradient", False))
        self.assertTrue(relevant("edge_trim_mv", "interpolated_gradient", False))
        self.assertTrue(relevant("bin_mv", "histogram_weighted", False))
        self.assertFalse(relevant("bin_mv", "interpolated_gradient", False))
        self.assertTrue(relevant("gaussian_sigma", "histogram", True))
        self.assertFalse(relevant("gaussian_sigma", "histogram", False))
        self.assertFalse(relevant("sg_window", "histogram", True))
        self.assertTrue(relevant("sg_window", "interpolated_gradient", True))

    def test_only_settings_of_the_method_are_validated(self):
        s = DqdvState(cycles_text="1", method="histogram", points_text="x", trim_text="-1", bin_text="0")
        self.assertEqual(set(s.errors()), {"bin_mv"})
        s.bin_text = "2"
        p = s.params()
        self.assertEqual((p.method, p.bin_mv, p.edge_trim_mv, p.points_mode, p.points_value),
                         ("histogram", 2.0, 5.0, "percent", 10.0))           # hidden invalid text -> defaults
        p.core_parameters().validate()
        s.method = "interpolated_gradient"
        self.assertEqual(set(s.errors()), {"points", "edge_trim_mv"})

    def test_voltage_window(self):
        s = DqdvState(cycles_text="1", emin_text="3,4", emax_text="")
        self.assertEqual((s.params().e_min, s.params().e_max), (3.4, None))
        s.emax_text = "3.2"
        self.assertEqual(s.errors()["e_max"], "E min must be lower than E max.")
        s.emax_text = "volts"
        self.assertIn("e_max", s.errors())

    def test_classic_parameters_reach_gregorithm(self):
        s = DqdvState(cycles_text="1-2", method="interpolated_gradient", points_text="250", trim_text="0",
                      emin_text="3.5", emax_text="3.9", reconstruction=False)
        core = s.params().core_parameters()
        core.validate()
        self.assertEqual((core.method, core.interpolation_mode, core.interpolation_value, core.edge_trim_mv),
                         ("interpolated_gradient", "count", 250.0, 0.0))
        self.assertEqual((core.voltage_min_v, core.voltage_max_v, core.reconstruction_enabled), (3.5, 3.9, False))

    def test_method_change_makes_the_result_outdated(self):
        s = DqdvState(cycles_text="1, 10")
        s.finish(res())
        s.load_from_result()
        self.assertIs(s.state(s.params()), DqdvView.CURRENT)
        s.method = "histogram_weighted"
        self.assertIn("method", s.modified(s.params()))
        s.method = "interpolated_gradient"
        s.bin_text = "3"                                      # not used by the interpolated gradient: not a change
        self.assertIs(s.state(s.params()), DqdvView.CURRENT)
        s.emin_text = "3.3"
        self.assertEqual(s.modified(s.params()), {"e_range"})

    def test_reconstruction_view_and_citation(self):
        from app.dqdv import CITATION, fmt_ape, fmt_rmse, method_summary
        self.assertIn("10.1149/1945-7111/ae7e5c", CITATION)
        s = DqdvState(cycles_text="1")
        s.tab = "reconstruction"
        s.finish(DqdvResult("q", "e", DqdvParams((1,), reconstruction=False), 2, 10))
        self.assertEqual(s.tab, "dqdv")                       # no reconstruction published: back to dQ/dV
        self.assertEqual(fmt_ape(None), "—")
        self.assertEqual(fmt_ape(0.2834), "0.283 %")
        self.assertEqual(fmt_ape(7.8e-14), "0.000 %")                    # float residue of a perfect reconstruction
        self.assertEqual(fmt_rmse(0.00146, "V"), "1.46 mV")
        self.assertEqual(fmt_rmse(1.46, "mV"), "1.46 mV")
        self.assertEqual(method_summary(DqdvParams((1,), method="histogram", bin_mv=2.0, smoothing=True)),
                         "Histogram · bin 2 mV · Gaussian σ 1 bin")

    def test_demo_reconstruction(self):
        from app.demo_backend import DemoBackend
        from app.units import FACTORY_UNITS
        b, ev = DemoBackend(0), threading.Event()
        r = b.analyze_dqdv("ds", "el1", DqdvParams((1, 2), method="histogram"), None, lambda pr: None, ev)
        rec = b.dqdv_reconstruction(r.result_id, FACTORY_UNITS)
        self.assertEqual(len(rec), 4)
        self.assertTrue(all(len(x.measured_q) and len(x.rebuilt_q) and x.ape_percent is not None for x in rec))


if __name__ == "__main__":
    unittest.main()
