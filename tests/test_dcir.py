import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.dcir import (ACCEPTED, AUTO, CHARGE, DISCHARGE, MANUAL, PENDING, REJECTED, DcirState, DetectionParams,
                      DetectionResult, EventView, FACTORY_DETECTION, ManualValue, MeasurementView, PublishedResult, Step,
                      describe_params, explain, manual_ready, parse_current_time, parse_measurement_time, parse_param,
                      result_points, split_event_times, DEFAULT_MEASUREMENT_TIME_S)
from app.elaboration import Outcome, Run


def ev(i, state=PENDING, t=None, pol=DISCHARGE, cycle=1, pre=10, source=AUTO):
    return EventView(f"e{i}", source, state, event_time_s=float(i * 100) if t is None else t, cycle=cycle, polarity=pol,
                     pulse_duration_s=30.0, pre_pulse_row=pre)


def meas(eid, r=0.064):
    return MeasurementView(eid, 10.0, 10.0, 0.0, 0.0, 3.9, -1.25, 3.82, -1.25, -0.08, r, None)


def detected(n=3, **kw):
    s = DcirState()
    s.begin("detect")
    s.detected(DetectionResult("d1", "el1", FACTORY_DETECTION, tuple(ev(i, **kw) for i in range(1, n + 1))))
    return s


class Params(unittest.TestCase):
    def test_factory(self):
        p = FACTORY_DETECTION
        self.assertEqual((p.rest_before_s, p.pulse_s, p.rest_after_s, p.polarity), (600, 30, 600, "both"))
        self.assertEqual((p.rest_before_tolerance_pct, p.pulse_tolerance_pct, p.rest_after_tolerance_pct), (20, 20, 20))

    def test_parse(self):
        self.assertEqual(parse_param("pulse_s", "30"), (30.0, ""))
        self.assertIsNone(parse_param("pulse_s", "0")[0])            # pulse must be > 0
        self.assertEqual(parse_param("rest_before_s", "0"), (0.0, ""))  # 0 disables the rest requirement
        self.assertIsNone(parse_param("rest_after_s", "-1")[0])
        self.assertIsNone(parse_param("pulse_tolerance_pct", "abc")[0])
        self.assertEqual(parse_param("pulse_tolerance_pct", "12,5")[0], 12.5)

    def test_set_and_reset(self):
        s = DcirState()
        s.set_param_text("pulse_s", "x")
        self.assertIn("pulse_s", s.param_errors)
        self.assertEqual(s.params.pulse_s, 30.0)
        s.set_param_text("pulse_s", "10")
        self.assertEqual(s.params.pulse_s, 10.0)
        self.assertNotIn("pulse_s", s.param_errors)
        s.set_polarity(CHARGE)
        s.reset_params()
        self.assertEqual(s.params, FACTORY_DETECTION)

    def test_describe(self):
        d = describe_params(DetectionParams(rest_before_s=0))
        self.assertIn("Rest before not required", d)
        self.assertIn("Pulse 30 s", d)


class Review(unittest.TestCase):
    def test_detect_moves_to_review_and_selects_first_pending(self):
        s = detected()
        self.assertEqual(s.step, Step.REVIEW)
        self.assertEqual(s.selected, "e1")
        self.assertIs(s.run, Run.IDLE)

    def test_labels(self):
        s = detected()
        self.assertEqual([s.label(e) for e in s.events()], ["Pulse 1", "Pulse 2", "Pulse 3"])
        s.decide("e2", ACCEPTED)
        s.labels = {"e2": "DCIR1"}
        self.assertEqual(s.label(s.event("e2")), "DCIR1")
        s.decide("e2", REJECTED)
        self.assertEqual(s.label(s.event("e2")), "Pulse 2")      # DCIRn only for accepted

    def test_decide_and_order(self):
        s = detected()
        self.assertTrue(s.decide("e3", ACCEPTED))
        self.assertTrue(s.decide("e1", ACCEPTED))
        self.assertEqual(s.orders, {"e3": 0, "e1": 1})
        s.decide("e3", REJECTED)
        self.assertNotIn("e3", s.orders)
        self.assertEqual(s.counts()[ACCEPTED], 1)
        self.assertTrue(s.unpublished)

    def test_back_to_baseline_is_not_a_change(self):
        s = detected()
        s.decide("e1", ACCEPTED)
        s.decide("e1", PENDING)
        self.assertFalse(s.unpublished)

    def test_missing_reference_cannot_be_accepted(self):
        s = DcirState()
        s.detected(DetectionResult("d", "el", FACTORY_DETECTION, (ev(1, pre=None),)))
        ok, why = s.can_accept(s.event("e1"))
        self.assertFalse(ok)
        self.assertIn("DCIR_MISSING_PRE_PULSE_REFERENCE", why)
        self.assertFalse(s.decide("e1", ACCEPTED))
        self.assertTrue(s.decide("e1", REJECTED))

    def test_filters_and_navigation(self):
        s = DcirState()
        s.detected(DetectionResult("d", "el", FACTORY_DETECTION,
                                   (ev(1), ev(2, pol=CHARGE), ev(3), ev(4, pol=CHARGE))))
        s.f_polarity = CHARGE
        self.assertEqual([e.event_id for e in s.visible()], ["e2", "e4"])
        s.selected = None
        self.assertEqual(s.move(1), "e2")
        self.assertEqual(s.move(1), "e4")
        self.assertEqual(s.move(1), "e4")                       # stays on the last one
        self.assertEqual(s.move(-1), "e2")
        s.f_polarity = "both"
        s.decide("e3", REJECTED)
        s.f_state = REJECTED
        self.assertEqual([e.event_id for e in s.visible()], ["e3"])

    def test_events_sorted_chronologically(self):
        s = DcirState()
        s.detected(DetectionResult("d", "el", FACTORY_DETECTION, (ev(2, t=500.0), ev(1, t=100.0))))
        self.assertEqual([e.event_id for e in s.events()], ["e1", "e2"])


class Manual(unittest.TestCase):
    def values(self, **over):
        v = {"i0": ManualValue(0.0, 5, 1.0), "v0": ManualValue(3.9, 5, 1.0), "i1": ManualValue(-1.25),
             "v1": ManualValue(3.82, 9, 2.0, cycle=8)}
        v.update(over)
        return v

    def test_ready(self):
        self.assertEqual(manual_ready(self.values()), "")
        v = self.values()
        del v["v1"]
        self.assertEqual(manual_ready(v), "V1 is missing.")
        self.assertEqual(self.values()["i1"].provenance, "typed")
        self.assertEqual(self.values()["v0"].provenance, "sample")

    def test_add_manual_is_accepted_and_never_pending(self):
        s = detected()
        m = EventView("m1", MANUAL, ACCEPTED, event_time_s=150.0, cycle=8, pre_pulse_row=None)
        s.add_manual(m, meas("m1"), self.values())
        self.assertEqual(s.event("m1").state, ACCEPTED)
        self.assertEqual(s.selected, "m1")
        self.assertEqual(s.label(s.event("m1")), "Manual")
        self.assertFalse(s.decide("m1", PENDING))
        self.assertTrue(s.decide("m1", REJECTED))
        self.assertTrue(s.decide("m1", ACCEPTED))              # manual events need no pre-pulse row
        self.assertEqual(s.counts()[MANUAL], 1)
        self.assertTrue(s.unpublished)


class MeasurementTime(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_measurement_time("10"), (10.0, ""))
        self.assertIsNone(parse_measurement_time("0")[0])
        self.assertIsNone(parse_measurement_time("-2")[0])

    def test_change_invalidates_auto_previews_only(self):
        s = detected()
        s.decide("e1", ACCEPTED)
        s.decide("e2", ACCEPTED)
        s.add_manual(EventView("m1", MANUAL, ACCEPTED, event_time_s=1.0), meas("m1"), {})
        s.selected = "e3"
        self.assertEqual(s.to_measure(), ["e1", "e2", "e3"])
        for i in ("e1", "e2", "e3"):
            s.previews[i] = meas(i)
        self.assertEqual(s.to_measure(), [])
        self.assertEqual(s.publish_blocker(), "Accept or reject the 1 pending pulse first")
        s.decide("e3", REJECTED)
        self.assertEqual(s.publish_blocker(), "")
        self.assertTrue(s.set_time_text("5"))
        self.assertEqual(s.measurement_time_s, 5.0)
        self.assertEqual(s.to_measure(), ["e1", "e2"])          # re-measured on the pulses already accepted
        self.assertIsNotNone(s.measurement("m1"))               # manual unaffected
        self.assertIn("Measuring 2 accepted pulses", s.publish_blocker())
        self.assertFalse(s.set_time_text("5"))
        self.assertFalse(s.set_time_text("x"))
        self.assertTrue(s.time_error)
        self.assertEqual(s.measurement_time_s, 5.0)

    def test_rejected_not_measured(self):
        s = detected()
        s.selected = "e1"
        s.decide("e1", REJECTED)
        self.assertEqual(s.to_measure(), [])


class CurrentTime(unittest.TestCase):
    """Optional I1 time after V0 : empty keeps the core default (3rd pulse sample)."""

    def test_parse(self):
        self.assertEqual(parse_current_time(""), (None, ""))
        self.assertEqual(parse_current_time(" 2,5 "), (2.5, ""))
        for bad in ("0", "-1", "x", "nan", "inf"):
            self.assertIsNone(parse_current_time(bad)[0])
            self.assertTrue(parse_current_time(bad)[1])

    def test_change_remeasures_auto_pulses_and_marks_unpublished(self):
        s = detected()
        s.decide("e1", ACCEPTED)
        s.add_manual(EventView("m1", MANUAL, ACCEPTED, event_time_s=1.0), meas("m1"), {})
        s.previews["e1"] = meas("e1")
        self.assertTrue(s.set_current_time_text("3"))
        self.assertEqual(s.current_time_s, 3.0)
        self.assertNotIn("e1", s.previews)                       # auto preview dropped: re-measured at the new I1 time
        self.assertIsNotNone(s.measurement("m1"))                # manual unaffected
        self.assertFalse(s.set_current_time_text("3"))
        self.assertFalse(s.set_current_time_text("-1"))
        s.decide("e2", REJECTED)
        s.decide("e3", REJECTED)
        s.previews["e1"] = meas("e1")
        self.assertIn("greater than 0", s.publish_blocker())     # an invalid I1 time blocks Apply & Calculate
        self.assertTrue(s.set_current_time_text(""))             # back to the 3rd pulse sample
        self.assertIsNone(s.current_time_s)
        self.assertEqual(s.current_time_error, "")

    def test_published_i1_time_is_compared_for_unpublished(self):
        s = detected()
        s.published_ok(PublishedResult("p1", (ev(1, ACCEPTED),), (meas("e1"),), DEFAULT_MEASUREMENT_TIME_S, current_time_s=None))
        self.assertFalse(s.unpublished)
        s.set_current_time_text("4")
        self.assertTrue(s.unpublished)


class PulseTimes(unittest.TestCase):
    """DCIR Review: V1 / I1 times of one pulse (Apply) or of every pulse (Apply to All)."""

    def test_apply_gives_the_pulse_its_own_times_and_requires_a_new_review(self):
        s = detected()
        s.decide("e1", ACCEPTED)
        s.decide("e2", ACCEPTED)
        s.decide("e3", REJECTED)
        for i in ("e1", "e2"):
            s.previews[i] = meas(i)
        s.selected = "e1"
        self.assertEqual(s.draft_texts(), ("30", ""))
        self.assertFalse(s.has_draft)
        s.set_draft_text("v1", "20")
        s.set_draft_text("i1", "5")
        self.assertTrue(s.has_draft)
        self.assertTrue(s.apply_times_to_pulse())
        self.assertEqual(s.times_for("e1"), (20.0, 5.0))
        self.assertEqual(s.times_for("e2"), (DEFAULT_MEASUREMENT_TIME_S, None))      # others unchanged
        self.assertEqual(s.event("e1").state, PENDING)                    # changed pulse: accept it again
        self.assertNotIn("e1", s.previews)                                 # re-measured at its own times
        self.assertIn("e2", s.previews)
        self.assertIn("pending pulse", s.publish_blocker())
        self.assertEqual(s.draft_texts(), ("20", "5"))                     # fields show the pulse's own times
        self.assertFalse(s.apply_times_to_pulse())                         # nothing typed: nothing to apply

    def test_same_times_as_all_pulses_remove_the_own_times(self):
        s = detected()
        s.selected = "e2"
        s.set_draft_text("v1", "12")
        self.assertTrue(s.apply_times_to_pulse())
        self.assertTrue(s.has_own_times("e2"))
        s.set_draft_text("v1", "30")
        self.assertTrue(s.apply_times_to_pulse())
        self.assertFalse(s.has_own_times("e2"))

    def test_invalid_or_manual_cannot_be_applied(self):
        s = detected()
        s.selected = "e1"
        s.set_draft_text("v1", "0")
        self.assertIn("V1", s.draft_times()[2])
        self.assertFalse(s.apply_times_to_pulse())
        self.assertFalse(s.apply_times_to_all())
        s.add_manual(EventView("m1", MANUAL, ACCEPTED, event_time_s=1.0), meas("m1"), {})
        self.assertEqual(s.selected, "m1")
        self.assertFalse(s.can_apply_to_pulse(s.event("m1"))[0])
        nopre = detected(pre=None)
        nopre.selected = "e1"
        self.assertFalse(nopre.can_apply_to_pulse(nopre.event("e1"))[0])

    def test_draft_belongs_to_the_pulse_it_was_typed_for(self):
        s = detected()
        s.selected = "e1"
        s.set_draft_text("v1", "15")
        s.selected = "e2"
        self.assertEqual(s.draft_texts(), ("30", ""))                      # another pulse: its own times
        self.assertFalse(s.has_draft)
        s.set_draft_text("i1", "4")                                         # a new draft replaces the old one
        s.selected = "e1"
        self.assertEqual(s.draft_texts(), ("30", ""))

    def test_apply_to_all_sets_every_pulse_and_keeps_decisions(self):
        s = detected()
        s.decide("e1", ACCEPTED)
        s.selected = "e2"
        s.set_draft_text("v1", "8")
        s.apply_times_to_pulse()
        s.previews["e1"] = meas("e1")
        s.selected = "e1"
        s.set_draft_text("v1", "25")
        s.set_draft_text("i1", "2")
        self.assertTrue(s.apply_times_to_all())
        self.assertEqual((s.measurement_time_s, s.current_time_s), (25.0, 2.0))
        self.assertEqual(s.event_times, {})                                 # own times removed
        self.assertEqual(s.event("e1").state, ACCEPTED)                     # decision kept, measured again
        self.assertEqual(s.to_measure()[0], "e1")

    def test_global_change_keeps_previews_of_pulses_with_own_times(self):
        s = detected()
        s.decide("e1", ACCEPTED)
        s.decide("e2", ACCEPTED)
        s.selected = "e2"
        s.set_draft_text("v1", "8")
        s.apply_times_to_pulse()
        s.decide("e2", ACCEPTED)
        s.previews["e1"], s.previews["e2"] = meas("e1"), meas("e2")
        self.assertTrue(s.set_time_text("12"))                              # Advanced Settings: all-pulse V1
        self.assertNotIn("e1", s.previews)
        self.assertIn("e2", s.previews)                                     # measured at its own 8 s

    def test_published_own_times_and_unpublished(self):
        s = detected()
        s.decide("e1", ACCEPTED)
        pub = PublishedResult("p1", (ev(1, ACCEPTED),), (meas("e1"),), DEFAULT_MEASUREMENT_TIME_S,
                              event_times=(("e1", 20.0, None),))
        s.published_ok(pub)
        self.assertEqual(s.event_times, {"e1": (20.0, None)})
        self.assertFalse(s.unpublished)
        s.selected = "e1"
        s.set_draft_text("v1", "21")
        s.apply_times_to_pulse()
        self.assertTrue(s.unpublished)

    def test_redetection_keeps_own_times_of_accepted_pulses_only(self):
        s = detected()
        for eid in ("e1", "e2"):
            s.selected = eid
            s.set_draft_text("v1", "9")
            s.apply_times_to_pulse()
        s.begin("detect")
        s.detected(DetectionResult("d2", "el1", FACTORY_DETECTION, (ev(1, ACCEPTED), ev(2), ev(3))))
        self.assertEqual(set(s.event_times), {"e1"})
        s.elaboration_replaced()
        self.assertEqual(s.event_times, {})

    def test_split_event_times_recovers_the_all_pulse_times(self):
        self.assertEqual(split_event_times({}), (10.0, None, {}))
        pairs = {"a": (30.0, None), "b": (20.0, 5.0), "c": (30.0, None)}
        self.assertEqual(split_event_times(pairs), (30.0, None, {"b": (20.0, 5.0)}))
        self.assertEqual(split_event_times({"a": (5.0, None), "b": (7.0, None)}), (5.0, None, {"b": (7.0, None)}))


class Publish(unittest.TestCase):
    def test_default_v1_measurement_time_is_30_s(self):
        """V1 is read at 30 s by default (the end of the default 30 s pulse), not the core's 10 s."""
        self.assertEqual(DEFAULT_MEASUREMENT_TIME_S, 30.)
        self.assertEqual(DcirState().measurement_time_s, 30.)

    def test_success_opens_results(self):
        s = detected()
        s.step = Step.REVIEW
        s.begin("publish")
        s.published_ok(PublishedResult("p1", (ev(1, ACCEPTED),), (meas("e1"),), 10.0))
        self.assertIs(s.step, Step.RESULTS)

    def test_manual_dialog_starts_at_the_pulse_decided_last(self):
        s = detected()
        s.selected = None
        self.assertEqual(s.manual_start_event().event_id, "e3")          # nothing decided or selected: the latest
        s.selected = "e2"
        self.assertEqual(s.manual_start_event().event_id, "e2")
        s.selected = "e1"
        s.decide("e1", ACCEPTED)
        s.selected = "e2"                                                # the review advanced after the decision
        self.assertEqual(s.manual_start_event().event_id, "e1")

    def test_blockers(self):
        s = detected()
        self.assertEqual(s.publish_blocker(), "Accept or reject the 3 pending pulses first")
        s.decide("e1", REJECTED)
        s.decide("e2", REJECTED)
        s.decide("e3", REJECTED)
        self.assertEqual(s.publish_blocker(), "Accept at least one pulse")
        s.decide("e1", ACCEPTED)
        self.assertIn("Measuring 1 accepted pulse", s.publish_blocker())
        s.previews["e1"] = meas("e1")
        self.assertEqual(s.publish_blocker(), "")
        s.begin("publish")
        self.assertEqual(s.publish_blocker(), "An operation is running")
        self.assertFalse(s.can_accept(s.event("e2"))[0])

    def test_published_clears_local_changes(self):
        s = detected()
        s.decide("e1", ACCEPTED)
        s.decide("e2", REJECTED)
        s.previews["e1"] = meas("e1")
        s.begin("publish")
        evs = (ev(1, ACCEPTED), ev(2, REJECTED), ev(3))
        s.published_ok(PublishedResult("p1", evs, (meas("e1"),), DEFAULT_MEASUREMENT_TIME_S, (("e1", "DCIR1"),)))
        self.assertFalse(s.unpublished)
        self.assertEqual(s.label(s.event("e1")), "DCIR1")
        self.assertEqual(s.event("e2").state, REJECTED)
        s.set_time_text("20")
        self.assertTrue(s.unpublished)

    def test_published_manual_keeps_its_measurement(self):
        s = detected()
        s.add_manual(EventView("m1", MANUAL, ACCEPTED, event_time_s=1.0), meas("m1", 0.05), {})
        s.begin("publish")
        evs = (ev(1), ev(2), ev(3), EventView("m1", MANUAL, ACCEPTED, event_time_s=1.0, acceptance_order=0))
        s.published_ok(PublishedResult("p1", evs, (meas("m1", 0.05),), 10.0, (("m1", "DCIR1"),)))
        self.assertEqual(s.manual, {})
        self.assertEqual(s.measurement("m1").resistance, 0.05)
        self.assertEqual(s.publish_blocker(), "Accept or reject the 3 pending pulses first")
        self.assertEqual(s.label(s.event("m1")), "DCIR1")

    def test_redetect_keeps_only_accepted(self):
        s = detected()
        s.decide("e1", ACCEPTED)
        s.decide("e2", REJECTED)
        self.assertEqual(s.redetect_info(), (1, 1, True))
        s.begin("detect")
        s.detected(DetectionResult("d2", "el1", FACTORY_DETECTION, (ev(1, ACCEPTED), ev(5))))
        self.assertEqual(s.decisions, {})                       # e2's local rejection is gone
        self.assertEqual([e.event_id for e in s.events()], ["e1", "e5"])

    def test_cancel_and_fail(self):
        s = detected()
        s.begin("publish")
        self.assertTrue(s.request_cancel())
        self.assertIs(s.run, Run.CANCELLING)
        s.cancelled()
        self.assertIs(s.outcome, Outcome.CANCELLED)
        s.begin("detect")
        s.failed("boom")
        self.assertEqual((s.outcome, s.message), (Outcome.FAILED, "boom"))

    def test_elaboration_replaced(self):
        s = detected()
        s.decide("e1", ACCEPTED)
        self.assertTrue(s.elaboration_replaced())
        self.assertTrue(s.elab_changed)
        self.assertEqual((s.events(), s.step), ([], Step.DETECTION))
        self.assertFalse(s.unpublished)


class Results(unittest.TestCase):
    def test_points_exclude_nulls(self):
        evs = (ev(1, ACCEPTED, cycle=2), ev(2, ACCEPTED, cycle=None), ev(3, ACCEPTED, cycle=1), ev(4, REJECTED))
        ms = (meas("e1", 0.07), meas("e2"), meas("e3", None), meas("e4"))
        pts, left = result_points(PublishedResult("p", evs, ms, 10.0))
        self.assertEqual(pts, [(2, 0.07, "e1")])
        self.assertEqual(left, 2)
        self.assertEqual(result_points(None), ([], 0))

    def test_explain(self):
        self.assertTrue(explain("DCIR_ZERO_CURRENT_DIFFERENCE").endswith("(DCIR_ZERO_CURRENT_DIFFERENCE)"))
        self.assertEqual(explain("X_UNKNOWN"), "X_UNKNOWN")


if __name__ == "__main__":
    unittest.main()


@unittest.skip("The production integration intentionally has no synthetic DCIR backend.")
class DemoFlow(unittest.TestCase):
    """State + SYNTHETIC demo backend end to end, without Qt (what the controller does, step by step)."""

    def test_flow(self):
        import threading
        from app.demo_backend import DemoBackend
        from app.units import FACTORY_UNITS, UnitPrefs
        b, s, ev = DemoBackend(0), DcirState(), threading.Event()
        s.begin("detect")
        s.detected(b.detect_dcir("ds", "el1", s.params, FACTORY_UNITS, lambda p: None, ev))
        self.assertEqual(s.counts()["total"], 24)
        for m in b.measure_dcir(s.detection.result_id, s.to_measure(), s.measurement_time_s, FACTORY_UNITS, ev):
            s.previews[m.event_id] = m
        self.assertTrue(s.decide(s.selected, ACCEPTED))
        self.assertFalse(s.decide("e010", ACCEPTED))                  # synthetic pulse without pre-pulse sample
        for m in b.measure_dcir(s.detection.result_id, s.to_measure(), s.measurement_time_s, FACTORY_UNITS, ev):
            s.previews[m.event_id] = m
        self.assertEqual(s.publish_blocker(), "")
        vals = {"i0": ManualValue(0.0), "v0": ManualValue(3.9, 5, 10.0, 1), "i1": ManualValue(-1250.0),
                "v1": ManualValue(3.82, 9, 20.0, 1)}
        self.assertAlmostEqual(b.preview_manual_dcir(s.detection.result_id, vals, FACTORY_UNITS).resistance, 0.064, places=9)
        s.add_manual(*b.create_manual_dcir(s.detection.result_id, vals, FACTORY_UNITS), vals)
        self.assertAlmostEqual(s.measurement(s.selected).resistance, 0.064, places=9)  # demo arithmetic, Ω (default)
        s.begin("publish")
        s.published_ok(b.publish_dcir(s.detection.result_id, {e.event_id: e.state for e in s.events()},
                                      s.measurement_time_s, FACTORY_UNITS, lambda p: None, ev))
        pts, left = result_points(s.published)
        self.assertEqual((len(pts), left), (2, 0))
        self.assertFalse(s.unpublished)
        milli = b.dcir_published(s.published.result_id, UnitPrefs(resistance="mΩ"))
        self.assertAlmostEqual(result_points(milli)[0][0][1], pts[0][1] * 1000, places=9)
