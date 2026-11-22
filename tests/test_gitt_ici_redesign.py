"""Redesigned GITT / ICI area: navigation, OCV Analysis, manual sampled OCV, extrapolation settings, targeted
recalculation, queued edits and Pulse Fitting interactions on the real gregorithm pipeline.

Synthetic file of test_gitt_ici_workflow: rests after each pulse relax as ``ocv' + 0.003·√60 − 0.003·√k`` (k = 1..60 s),
so their last sample is exactly ``ocv'`` (3.51, 3.52, 3.53 V); the first rest is 3.5 V and has no relaxation origin.
"""
from __future__ import annotations

import math
import threading
from dataclasses import replace

import gregorithm
import pyqtgraph as pg
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from app.gitt_ici import GittIciState
from app.gitt_ici_review import ACCEPTED, MODIFIED, PENDING, rest_fingerprint
from app.theme import ThemeManager
from app.widgets.gitt_ici_page import GittIciPage
from test_gitt_ici_workflow import (DELTA, GEOMETRY, VA, _ready, _record, _run, _session, _wait,  # noqa: F401
                              app, pipeline)
from app.gitt_ici import GittIciParams
from app.gitt_ici_controller import GittIciController


def _page(app, ctl):
    page = GittIciPage(ThemeManager(app), ctl)
    page.show_dataset("d")
    return page


# --- navigation ----------------------------------------------------------------------------------------------------


def test_proceed_unlocks_the_areas_and_opens_ir_drop(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline)
    page = _page(app, ctl)
    assert page.stack.currentWidget() is page.setup
    assert not any(page.step_buttons[k].isEnabled() for k in ("ir", "ocv", "fit", "results"))
    page.go("results")
    assert page.stack.currentWidget() is page.setup                                        # still locked
    page.setup.proceed_btn.click()
    model = ctl.model("d")
    _wait(app, lambda: not model.is_running)
    assert model.step == "ir" and page.stack.currentWidget() is page.ir_view
    assert all(page.step_buttons[k].isEnabled() for k in ("ir", "ocv", "fit", "results"))
    assert page.step_buttons["ir"].property("current") == "true"
    page.go("setup")
    assert page.stack.currentWidget() is page.setup and model.events                       # nothing lost going back


# --- OCV analysis --------------------------------------------------------------------------------------------------


def test_ocv_area_lists_every_rest_and_keeps_sampled_and_extrapolated_apart(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline)
    model = _run(app, ctl, "d", ctl.calculate_all)
    page = _page(app, ctl)
    page.go("ocv")
    view = page.ocv_view
    rests = ctl.rest_ocv("d")
    assert view.list.topLevelItemCount() == 4 == len(rests)
    assert [r.sampled.voltage_v for r in rests[1:]] == pytest.approx([3.51, 3.52, 3.53], rel=1e-12, abs=0)
    assert all(r.extrapolated is None and r.extrapolated_reason == "not_requested" for r in rests)
    assert not view.extrapolate.isChecked() and view.chart.marks == {"sampled": rests[0].sampled.source_row}
    view.extrapolate.setChecked(True)
    view.global_target.setText("600")
    view.apply_settings.click()                                                            # all rests at once
    _wait(app, lambda: not model.is_running)
    rests = ctl.rest_ocv("d")
    assert model.applied.extrapolation and rests[0].extrapolated_reason == "relaxation_reference_missing"
    second = rests[1]
    assert second.extrapolated.voltage_v != second.sampled.voltage_v                         # both kept, separate
    assert second.sampled.voltage_v == pytest.approx(3.51, rel=1e-12, abs=0)
    page._open_rest(second.rest_segment_id)
    assert view.chart.marks["sampled"] == second.sampled.source_row
    assert view.chart.marks["extrapolated"] == 600.0                                         # extrapolated point
    names = [i.name() for i in view.chart.pi.listDataItems()]
    assert "LOG_TIME_2P fit (gregorithm)" in names
    lo, hi = view.chart.pi.vb.viewRange()[0]
    assert hi >= 600.0                                                                      # view reaches the target
    states = ctl.rest_states("d")
    assert states[rests[0].rest_segment_id] == PENDING                                      # sampled OCV is valid
    assert "relaxation_reference_missing" in view.list.topLevelItem(0).toolTip(0)           # problem still shown
    view.accept_all.click()
    states = ctl.rest_states("d")
    assert list(states.values()).count(ACCEPTED) == 4
    assert all(r.state.value == "accepted" for r in model.records if r.identity.method is DELTA)  # no fit needed


def test_manual_sampled_ocv_recalculates_only_dependent_results_and_reopens_review(app, pipeline) -> None:
    calls = []
    backend, _elab = pipeline

    class Spy:
        def __getattr__(self, name):
            return getattr(backend, name)

        def analyze_gitt_ici(self, *a, **k):
            calls.append((k.get("event_ids"), k.get("only_stale"), dict(k.get("ocv_samples") or {})))
            return backend.analyze_gitt_ici(*a, **k)

    ctl, _ = _ready(app, pipeline)
    ctl.backend = Spy()
    model = _run(app, ctl, "d", ctl.calculate_all)
    ctl.accept_rests("d", None)
    first, second, third = model.events
    rest = next(r for r in ctl.rest_ocv("d") if r.rest_segment_id == second.rest_after_segment_id)
    row = rest.samples[29].source_row                                                       # t = 30 s
    untouched = _record(model, VA, 0)
    calls.clear()
    ctl.set_ocv_sample("d", rest.rest_segment_id, row)
    _wait(app, lambda: not model.is_running)
    assert calls == [((second.event_id, third.event_id), True, {rest.rest_segment_id: row})]
    expected = 3.52 + 0.003 * math.sqrt(60) - 0.003 * math.sqrt(30)
    assert _record(model, DELTA, 1).details["ocv_after"]["voltage_v"] == pytest.approx(expected, rel=1e-12,
                                                                                         abs=0)
    assert _record(model, DELTA, 2).details["ocv_before"]["source_row"] == row
    assert _record(model, VA, 0) is untouched                                                # other impulse
    assert ctl.rest_states("d")[rest.rest_segment_id] == MODIFIED                            # accepted, then changed
    assert _record(model, DELTA, 1).state.value == "provisional"
    ctl.set_ocv_sample("d", rest.rest_segment_id, first.active_last_row)                     # not a rest sample
    assert "does not belong" in model.notice and model.ocv_samples == {rest.rest_segment_id: row}
    _run(app, ctl, "d", ctl.set_ocv_sample, rest.rest_segment_id, None)                      # back to the rule
    assert model.ocv_samples == {} and ctl.rest_states("d")[rest.rest_segment_id] == ACCEPTED


def test_global_extrapolation_change_recalculates_nothing_that_does_not_depend_on_it(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline)
    model = _run(app, ctl, "d", ctl.calculate_all)
    before = {r.identity: r for r in model.records}
    _run(app, ctl, "d", lambda ds: ctl.set_ocv_settings(ds, extrapolation=True, target_s=900.0, fit_start=0.3))
    assert all(model.record(i) is r for i, r in before.items())                            # sampled-OCV D unchanged
    rest = ctl.rest_ocv("d")[1]
    assert rest.fit.target_time_s == 900.0 and rest.fit.fit_start_s == pytest.approx(0.3 * 60, rel=1e-12, abs=0)


def test_rest_acceptance_fingerprint_follows_what_was_examined() -> None:
    class Point:
        def __init__(self, row, v):
            self.source_row, self.voltage_v = row, v

    class Rest:
        rest_segment_id, sampled, fit, extrapolated = 2, Point(10, 3.5), None, None
    params = GittIciParams()
    base = rest_fingerprint(Rest, params)
    Rest.sampled = Point(11, 3.5)
    assert rest_fingerprint(Rest, params) != base                                           # another sample


def test_rests_accepted_before_the_ici_fits_stay_accepted_and_d_is_plotted(app, pipeline) -> None:
    # Regression: the ICI pseudo-OCV was part of the rest fingerprint, so rests accepted in OCV Analysis (before the
    # fits exist) became Modified once Pulse Fitting calculated them, and no D reached Diffusion Results.
    ctl, _ = _ready(app, pipeline, protocol="ici", methods=("ici",), ocv_methods=("ici_intercept",))
    model = _run(app, ctl, "d", ctl.apply)
    assert model.events and not model.records
    ctl.accept_rests("d", None)
    accepted = set(ctl.rest_states("d").values())
    _run(app, ctl, "d", ctl.calculate_all)
    assert set(ctl.rest_states("d").values()) == accepted == {ACCEPTED}
    ctl.accept_pulses("d", None)
    page = _page(app, ctl)
    page.go("results")
    assert len(page.results.visible_points()) == sum(r.diffusion_m2_s is not None for r in model.records) > 0


# --- queued edits and pulse fitting -------------------------------------------------------------------------------


def test_edits_made_during_a_job_are_queued_and_only_the_latest_runs(app, pipeline) -> None:
    backend, elab = pipeline
    gate = threading.Event()
    calls = []

    class Slow:
        def __getattr__(self, name):
            return getattr(backend, name)

        def analyze_gitt_ici(self, *a, **k):
            calls.append(k.get("event_id"))
            gate.wait(10)
            return backend.analyze_gitt_ici(*a, **k)

    state, _ = _session(elab)
    ctl = GittIciController(state, Slow())
    ctl.set_params("d", GittIciParams(half_cycles=(1,), **GEOMETRY))
    gate.set()
    model = _run(app, ctl, "d", ctl.calculate_all)
    event = model.events[0].event_id
    model.selected = _record(model, VA).identity
    gate.clear()
    calls.clear()
    ctl.apply_window("d", event, "pulse", 5.0, 90.0)
    assert model.is_running
    ctl.apply_window("d", event, "pulse", 20.0, 70.0)                                      # queued
    ctl.apply_window("d", event, "pulse", 10.0, 80.0)                                      # replaces it
    assert "queued" in model.notice
    gate.set()
    _wait(app, lambda: not model.is_running and len(calls) == 2)
    _wait(app, lambda: not model.is_running)
    assert model.windows == {(event, "pulse"): (10.0, 80.0)}                                # the latest wins
    assert _record(model, VA).details["window"]["requested"]["bounds"] == [10.0, 80.0]


def test_releasing_the_dragged_window_recalculates_that_fit_once(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline)
    model = _run(app, ctl, "d", ctl.calculate_all)
    ctl.accept_rests("d", None)
    ctl.accept_pulses("d", None)
    page = _page(app, ctl)
    page.go("fit")
    view = page.fit_view
    event = model.events[0].event_id
    assert model.open_event == event and view.chart.window() is not None
    view.chart.windowReleased.emit(12.0, 60.0)                                             # mouse release
    _wait(app, lambda: not model.is_running)
    assert model.windows == {(event, "pulse"): (12.0, 60.0)}
    assert ctl.pulse_states("d")[event] == MODIFIED                                          # review again
    assert "Modified" in view.list.topLevelItem(0).toolTip(1)
    assert _record(model, VA).state is gregorithm.ResultState.PROVISIONAL
    assert _record(model, VA, 1).state is gregorithm.ResultState.ACCEPTED                   # other impulse


def test_cancelled_job_restores_manual_ocv_samples() -> None:
    state = GittIciState()
    state.ocv_samples = {2: 5}
    state.begin("local", False)
    state.ocv_samples = {2: 9}
    state.cancel()
    assert state.ocv_samples == {2: 5}
    state.rest_reviews, state.pulse_reviews = {2: "x"}, {"e": "y"}
    state.reset_results()
    assert state.ocv_samples == {} and state.rest_reviews == {} and state.pulse_reviews == {}
    assert PENDING == "Pending"


def test_delacourt_is_gitt_only(app) -> None:
    """Regression: Delacourt could be selected with the ICI protocol, whose short interruptions break the relaxed
    initial state of its model; it is now disabled there, and an old ICI draft with it ticked does not keep it."""
    from app.gitt_ici import method_allowed
    assert method_allowed("gitt", "delacourt_fixed") and not method_allowed("ici", "delacourt_fixed")
    state, _ = _session()
    page = GittIciPage(ThemeManager(app), GittIciController(state, object()))
    setup = page.setup
    setup.load(GittIciParams(protocol="ici", methods=("ici", "delacourt_fixed"), ocv_methods=("ici_intercept",)), 1, 2)
    box = setup.methods["delacourt_fixed"]
    assert not box.isEnabled() and not box.isChecked()
    params, _errors = setup.read()
    assert params.methods == ("ici",)


def test_unselected_methods_are_never_used(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline, methods=("ici", "delacourt_fixed"), ocv_methods=("rest_sample",),
                    particle_radius=1.0)
    model = _run(app, ctl, "d", ctl.calculate_all)
    methods = {r.identity.method for r in model.records}
    assert methods == {gregorithm.DiffusionMethod.ICI, gregorithm.DiffusionMethod.GITT_SPHERE_FULL}
    assert all(r.error or r.result is not None for r in model.records)                       # reason, never a zero
    ctl.accept_rests("d", None)
    ctl.accept_pulses("d", None)
    used = {r.identity for r in model.records if r.state.value == "accepted"}
    ctl.set_params("d", replace(model.applied, methods=("ici",)))
    _run(app, ctl, "d", ctl.apply)
    still = {r.identity for r in model.records if r.state.value == "accepted"}
    assert still and all(i.method is gregorithm.DiffusionMethod.ICI for i in still) and still <= used


def test_d_unit_is_chosen_in_diffusion_results_and_shown_in_pulse_fitting(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline)
    model = _run(app, ctl, "d", ctl.calculate_all)
    ctl.accept_rests("d", None)
    ctl.accept_pulses("d", None)
    page = _page(app, ctl)
    page.go("results")
    view = page.results
    cm2 = sorted(p.y for p in view.visible_points())
    view.d_unit.setCurrentIndex(view.d_unit.findData("m2/s"))                              # m²/s selectable
    assert model.d_unit == "m2/s"
    assert sorted(p.y for p in view.visible_points()) == pytest.approx([v * 1e-4 for v in cm2], rel=1e-12)
    page.go("fit")
    assert "D [m²/s]" in page.fit_view.values.as_text()


def test_several_ocv_methods_give_separate_d_series_and_each_is_used_on_its_own_rests(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline, methods=("gitt_va",), ocv_methods=("rest_sample", "relaxation_extrapolated"))
    model = _run(app, ctl, "d", ctl.calculate_all)
    sources = {r.identity.ocv_source.value for r in model.records}
    assert sources == {"rest_sample", "relaxation_extrapolated"} and len(model.records) == 6
    assert model.applied.extrapolation_active                                               # extrapolation needed
    sampled = [r for r in model.records if r.identity.ocv_source.value == "rest_sample"]
    order = {e.event_id: i for i, e in enumerate(model.events)}
    extrapolated = sorted((r for r in model.records if r.identity.ocv_source.value == "relaxation_extrapolated"),
                          key=lambda r: order[r.identity.event_id])
    assert all(r.diffusion_m2_s for r in sampled)
    assert extrapolated[0].error and all(r.diffusion_m2_s for r in extrapolated[1:])      # first rest: no origin
    ctl.accept_rests("d", None)
    ctl.accept_pulses("d", None)
    page = _page(app, ctl)
    page.go("results")
    labels = {k for k in page.results.boxes if k.startswith("D:")}
    assert labels == {"D:GITT VA · Sampled OCV", "D:GITT VA · Extrapolated OCV"}
    assert len(page.results.visible_points()) == 5                                          # 3 sampled + 2 extrapolated


def test_every_sample_of_the_segments_is_plotted_and_fitted(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline, auto_analysis=True, window_mode="auto_fixed",
                    methods=("gitt_va", "delacourt_fixed"), particle_radius=1.0)
    model = _run(app, ctl, "d", ctl.apply)
    page = _page(app, ctl)
    page.go("fit")
    trace = model.trace
    raw = next(i for i in page.fit_view.chart.pt.getPlotItem().listDataItems() if i.name() == "Raw voltage")
    assert len(raw.xData) == len(trace.pulse.time_s) == 100                                 # every pulse sample
    va = _record(model, VA)
    fit = va.details["window"]["fit"]
    assert fit["sample_count"] == 100 and fit["effective_s"] == [1.0, 100.0]               # whole segment fitted
    dela = _record(model, gregorithm.DiffusionMethod.GITT_SPHERE_FULL)
    assert len(dela.result.relative_times_s) == 100                                        # Delacourt: all samples
    page.go("ir")
    pre = page.ir_view.charts["pre_pulse"]
    drawn = sum(len(i.data) for i in pre.pi.items if isinstance(i, pg.ScatterPlotItem) and len(i.data) > 1)
    assert drawn == len(trace.rest_before.time_s) + len(trace.pulse.time_s)                # whole rest + pulse
    page.go("ocv")
    rest = ctl.rest_ocv("d")[1]
    page._open_rest(rest.rest_segment_id)
    samples = next(i for i in page.ocv_view.chart.pi.listDataItems() if i.name() == "Rest samples")
    assert len(samples.xData) == len(rest.samples) == 60                                    # every rest sample


def test_every_setup_control_has_an_explanation(app) -> None:
    from app.gitt_ici_help import INFO
    from app.widgets.controls import InfoButton
    state, _ = _session()
    page = GittIciPage(ThemeManager(app), GittIciController(state, object()))
    # The advanced controls (IR correction, IR sample times, thresholds) live in setup.advanced_panel, which the
    # "GITT / ICI Settings" dock hosts instead of the page: their explanations are looked up there too.
    panel = page.setup.advanced_panel
    titles = {b.accessibleName() for w in (page, panel) for b in w.findChildren(InfoButton)}
    for key in ("protocol", "scope", "methods", "gitt_va", "gitt_delta", "ici", "delacourt_fixed", "ocv_methods",
                "rest_sample", "ici_intercept", "relaxation_extrapolated", "active_volume", "effective_area",
                "particle_radius", "auto", "window_mode", "guidance", "coefficient", "ir_mode", "ir_time",
                "ir_post_time", "thresholds", "proceed", "ir_drop", "ir_times", "ir_local", "ir_accept",
                "ocv_sampled", "ocv_extrapolation", "ocv_local",
                "ocv_accept", "fit_window", "fit_accept", "results_x", "results_y"):
        assert f"About {INFO[key][0]}" in titles, key
    assert "0.0032" in " ".join(INFO["coefficient"][1])


def test_explanations_lead_with_a_general_definition() -> None:
    """Regression: the popover highlights the first paragraph, so it must hold whatever is selected; an option such
    as "GITT: ..." first looked like the active choice when ICI was selected."""
    import re
    from app.gitt_ici_help import INFO
    for key, (_title, paragraphs) in INFO.items():
        assert not re.match(r"^\S+( \S+){0,2}: ", paragraphs[0]), key               # no "Option: ..." lead
        assert len(paragraphs[0]) <= 130, key                                         # a short definition


def test_ir_legend_is_shown_once_for_both_charts(app, pipeline) -> None:
    from app.widgets.gitt_ici_ir_view import LEGEND
    ctl, _ = _ready(app, pipeline)
    _run(app, ctl, "d", ctl.calculate_all)
    page = _page(app, ctl)
    page.go("ir")
    assert sum(label.text() == LEGEND for label in page.ir_view.findChildren(QLabel)) == 1
    assert all(chart.message.text() == "" for chart in page.ir_view.charts.values())     # no repeated legend


def test_fit_values_explain_the_statistical_indicator_and_pending(app, pipeline) -> None:
    from app.gitt_ici_help import INFO
    from app.widgets.controls import InfoButton
    ctl, _ = _ready(app, pipeline)
    model = _run(app, ctl, "d", ctl.calculate_all)
    page = _page(app, ctl)
    page.go("fit")
    view = page.fit_view
    va = _record(model, VA)
    assert va.quality.q_linearity_pct is not None
    assert f"q_L {va.quality.q_linearity_pct:.3g} %" in view.values.as_text()             # the numbers behind the colour
    assert view.state.text() == "● Pending" and "not yet accepted" in view.note.text()
    assert "not yet accepted" in view.state.toolTip()
    titles = {b.accessibleName() for b in view.findChildren(InfoButton)}
    assert f"About {INFO['fit_values'][0]}" in titles


def test_to_check_with_green_indicators_says_why() -> None:
    """Regression: an impulse was "to check" with q_L and q_S green and no reason shown; gregorithm also flags an
    automatic search that skipped the first data of its range, and the GUI now says so."""
    from types import SimpleNamespace
    from app.gitt_ici import review_reasons
    green = SimpleNamespace(value="green")
    quality = SimpleNamespace(needs_review=True, statistical=green, physical=SimpleNamespace(value="not_available"))
    record = SimpleNamespace(quality=quality, details={"window": {"early_data_excluded": True,
                                                                  "selected_s": [12.0, 90.0]}})
    assert review_reasons(record) == ["the automatic window skips the first 12 s of the range searched"]
    quality.needs_review = False
    assert review_reasons(record) == []


def test_pulse_fitting_is_compact_and_matches_the_other_result_pages(app, pipeline) -> None:
    from PySide6.QtWidgets import QToolButton
    ctl, _ = _ready(app, pipeline)
    _run(app, ctl, "d", ctl.calculate_all)
    page = _page(app, ctl)
    page.go("fit")
    view = page.fit_view
    assert view.chart.vertical is False                                                    # V(t) beside V(√t)
    assert view.values.rowCount() == 2 and view.values.alternatingRowColors()              # a real table
    page.go("results")
    tools = {b.accessibleName() for b in page.results.findChildren(QToolButton) if b.objectName() == "ChartTool"}
    assert tools == {"Reset View", "Save Image"}
    assert page.results.export.text() == "Export Table…"


def test_export_dialog_offers_only_columns_with_values(app, pipeline) -> None:
    from app.widgets.gitt_ici_page import GittIciExportDialog
    ctl, _ = _ready(app, pipeline, methods=("gitt_va",))
    model = _run(app, ctl, "d", ctl.calculate_all)
    ctl.accept_rests("d", None)
    ctl.accept_pulses("d", None)
    table = ctl.export_table("d")
    assert not {"ir_pre", "ir_post", "r_on", "r_off"} & {k for k, _t in table.columns}     # IR not accepted yet
    ctl.accept_ir("d", None)
    table = ctl.export_table("d")
    keys = [k for k, _t in table.columns]
    assert "ocv_pseudo" not in keys and "slope:ici" not in keys                              # no ICI: not offered
    assert {"capacity", "ir_pre", "ir_post", "ocv_sampled_before", "slope:gitt_va"} <= set(keys)
    dlg = GittIciExportDialog(None, table.columns, table.rows)
    assert dlg.split.isChecked() and not dlg.boxes["impulse"].isEnabled()
    dlg.boxes["r_on"].setChecked(False)
    fmt, columns, split = dlg.choice()
    assert fmt == "csv" and split and "r_on" not in columns and len(model.events) == 3


def test_review_lists_share_one_status_code_and_one_width(app, pipeline) -> None:
    """Regression: the lists showed cycle / half-cycle, ticks and crosses, rest segment ids and long values; they now
    share one narrow width, centred values and one coloured dot: green accepted, orange pending, red rejected."""
    from app.gitt_ici_review import ACCEPTED, PENDING
    from app.widgets.gitt_ici_setup import LIST_WIDTH, STATUS_ROLE, status_color
    ctl, _ = _ready(app, pipeline)
    _run(app, ctl, "d", ctl.calculate_all)
    page = _page(app, ctl)
    theme = page.fit_view.theme
    heads = {}
    for step, view in (("ir", page.ir_view), ("ocv", page.ocv_view), ("fit", page.fit_view)):
        page.go(step)
        tree = view.list
        heads[step] = [tree.headerItem().text(c) for c in range(tree.columnCount())]
        assert tree.parentWidget().width() == LIST_WIDTH or tree.parentWidget().maximumWidth() == LIST_WIDTH
        item = tree.topLevelItem(0)
        assert all(item.textAlignment(c) & Qt.AlignmentFlag.AlignHCenter for c in range(tree.columnCount()))
    assert heads == {"ir": ["#", "Pre", "Post", "State"], "ocv": ["#", "OCV", "Extr.", "State"],
                     "fit": ["#", "State", "Alert"]}
    ocv = page.ocv_view.list
    assert [ocv.topLevelItem(i).text(0) for i in range(ocv.topLevelItemCount())] == ["1", "2", "3", "4"]
    assert "before impulse #1" in ocv.topLevelItem(0).toolTip(0)                          # rest 1 precedes impulse 1
    assert len(ocv.topLevelItem(0).text(1)) <= 5                                          # 3 significant digits
    fit = page.fit_view.list.topLevelItem(0)
    assert fit.data(1, STATUS_ROLE) == status_color(theme, "pending")                     # orange dot
    assert fit.text(2) in ("OK", "1 to check", "2 to check")
    ctl.accept_pulses("d", [ctl.model("d").events[0].event_id])
    page.go("fit")
    assert page.fit_view.list.topLevelItem(0).data(1, STATUS_ROLE) == status_color(theme, "ok")    # green dot
    assert len({status_color(theme, k) for k in ("ok", "pending", "bad", "none")}) == 4
    assert ctl.pulse_states("d")[ctl.model("d").events[0].event_id] == ACCEPTED and PENDING


def test_review_list_columns_fit_the_list_and_accept_moves_to_the_next_item(app, pipeline) -> None:
    """Regressions: the theme's header padding made the IR columns wider than the list, so the status dot was pushed
    off its right edge; Accept left the reviewer on the item just accepted instead of opening the next one."""
    ctl, _ = _ready(app, pipeline)
    model = _run(app, ctl, "d", ctl.calculate_all)
    page = _page(app, ctl)
    page.resize(1300, 800)
    page.show()
    for step, view in (("ir", page.ir_view), ("ocv", page.ocv_view), ("fit", page.fit_view)):
        page.go(step)
        app.processEvents()
        header = view.list.header()
        assert sum(header.sectionSize(c) for c in range(header.count())) <= view.list.viewport().width(), step
    page.go("ocv")
    first_rest = page.ocv_view.list.topLevelItem(0).data(0, Qt.ItemDataRole.UserRole)
    page.ocv_view.list.setCurrentItem(page.ocv_view.list.topLevelItem(0))
    app.processEvents()
    page.ocv_view.accept.click()
    _wait(app, lambda: not model.is_running)
    assert page.ocv_view.list.currentItem() is page.ocv_view.list.topLevelItem(1)
    assert ctl.rest_states("d")[first_rest] == ACCEPTED
    page.go("fit")
    fit = page.fit_view.list
    fit.setCurrentItem(fit.topLevelItem(0))
    app.processEvents()
    page.fit_view.accept.click()
    _wait(app, lambda: not model.is_running)
    assert fit.currentItem() is fit.topLevelItem(1) and page.fit_view._event == model.events[1].event_id
    last = fit.topLevelItemCount() - 1
    fit.setCurrentItem(fit.topLevelItem(last))
    app.processEvents()
    page.fit_view.accept.click()
    _wait(app, lambda: not model.is_running)
    assert fit.currentItem() is fit.topLevelItem(last)                                     # stays on the last one


def test_long_fit_notes_wrap_in_the_table_and_in_the_tooltip(app) -> None:
    """Regression: a long note was cut in its cell and its one-line tooltip ran off the screen."""
    from app.widgets.gitt_ici_fit_view import ValuesTable
    note = "The automatic window skips the first 12.5 s of the range searched; " * 4
    table = ValuesTable()
    table.resize(500, 200)
    table.set_rows(["Series", "D", "Note"], [["GITT VA", "1e-10", note]])
    assert table.wordWrap()
    assert table.rowHeight(0) > table.fontMetrics().height() * 2                            # several lines
    tip = table.item(0, 2).toolTip()
    assert tip.startswith("<qt>") and "12.5 s" in tip                                      # rich text: Qt wraps it


def test_rests_and_impulses_can_be_rejected_by_hand(app, pipeline) -> None:
    """A rejected impulse gives no D of any method (GITT Δ included); a rejected rest feeds no D; accepting all
    skips rejected items, and Return to Pending undoes a rejection."""
    from app.gitt_ici_review import PENDING, REJECTED
    ctl, _ = _ready(app, pipeline)
    model = _run(app, ctl, "d", ctl.calculate_all)
    ctl.accept_rests("d", None)
    ctl.accept_pulses("d", None)
    page = _page(app, ctl)
    page.go("results")
    before = len(page.results.visible_points())
    first = model.events[0].event_id
    page.go("fit")
    assert page.fit_view.reject.isEnabled()
    page.fit_view.reject.click()
    assert ctl.pulse_states("d")[first] == REJECTED
    page.go("results")
    drawn = page.results.visible_points()
    assert len(drawn) == before - 2 and all(p.identity.event_id != first for p in drawn)   # GITT VA and GITT Δ
    ctl.accept_pulses("d", None)
    assert ctl.pulse_states("d")[first] == REJECTED                                       # all valid: skipped
    ctl.revoke_pulse("d", first)
    assert ctl.pulse_states("d")[first] == PENDING
    rest = ctl.rest_ocv("d")[1].rest_segment_id
    ctl.reject_rest("d", rest)
    assert ctl.rest_states("d")[rest] == REJECTED
    page.go("ocv")
    page._open_rest(rest)
    assert page.ocv_view.state.text() == "● Rejected" and page.ocv_view.revoke.isEnabled()
    ctl.accept_rests("d", [rest])                                                         # an explicit accept undoes it
    assert ctl.rest_states("d")[rest] != REJECTED


def test_continue_button_leads_forward_and_is_filled_once_the_area_is_accepted(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline)
    _run(app, ctl, "d", ctl.calculate_all)
    page = _page(app, ctl)
    assert page.footer.isHidden()                                                          # Setup has Proceed
    page.go("ocv")
    assert page.next_btn.text() == "Continue to Pulse Fitting ›" and page.next_btn.property("ghost")
    assert "4 rest period(s) still to review" in page.next_hint.text()
    page.ocv_view.accept_all.click()
    assert page.next_btn.property("goElab") and page.next_hint.text() == "Area complete"
    page.next_btn.click()
    assert page.stack.currentWidget() is page.fit_view
    assert "3 impulse fit(s) still to review" in page.next_hint.text()
    page.fit_view.accept_all.click()
    assert page.next_btn.property("goElab")
    page.next_btn.click()
    assert page.stack.currentWidget() is page.results and page.next_btn.isHidden()
    page.back.click()
    assert page.stack.currentWidget() is page.fit_view


def test_window_can_be_typed_before_the_first_calculation_without_automatic_analysis(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline)                                                         # automatic analysis off
    model = _run(app, ctl, "d", ctl.apply)
    assert model.events and not model.records
    page = _page(app, ctl)
    page.go("fit")
    view = page.fit_view
    assert [view.series.itemText(i) for i in range(view.series.count())] == ["GITT VA (not calculated)"]
    assert view.win_start.isEnabled() and view.win_apply.isEnabled()
    assert view.chart.window() == pytest.approx((0.0, 100.0))                                # suggested: whole pulse
    view.win_start.setText("10")
    view.win_end.setText("80")
    view.win_apply.click()
    _wait(app, lambda: not model.is_running)
    event = model.events[0].event_id
    assert model.windows == {(event, "pulse"): (10.0, 80.0)}
    own = [r for r in model.records if r.identity.event_id == event]
    assert {r.identity.method for r in own} == {VA, DELTA}                                  # whole impulse computed
    assert _record(model, VA).details["window"]["requested"]["bounds"] == [10.0, 80.0]


def test_fit_charts_are_rescaled_to_each_new_impulse_and_ocv_legend_is_outside_the_plot(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline)
    model = _run(app, ctl, "d", ctl.calculate_all)
    page = _page(app, ctl)
    page.go("fit")
    chart = page.fit_view.chart
    chart.pt.getPlotItem().setXRange(40.0, 41.0, padding=0)                                 # the user zooms in
    chart.pt.getPlotItem().setYRange(0.0, 0.1, padding=0)
    page.fit_view.list.setCurrentItem(page.fit_view.list.topLevelItem(2))
    app.processEvents()
    assert model.open_event == model.events[2].event_id
    (x0, x1), (y0, y1) = chart.pt.getPlotItem().vb.viewRange()
    trace = model.trace
    volts = trace.pulse.voltage_v
    assert x0 <= 1.0 and x1 >= 100.0                                                        # whole segment again
    assert y0 <= min(volts) and y1 >= max(volts) and y1 - y0 < 0.2                          # centred on the data
    page.go("ocv")
    ocv = page.ocv_view.chart
    assert ocv.pi.legend is None and "Rest samples" in ocv.legend.text()                    # legend below the plot


def test_ir_sample_times_are_edited_in_ir_drop_and_recalculate_only_dependent_results(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline, methods=("gitt_va", "delacourt_fixed"), particle_radius=1.0)
    model = _run(app, ctl, "d", ctl.calculate_all)
    page = _page(app, ctl)
    page.go("ir")
    view = page.ir_view
    assert view.pre_time.text() == "1" and view.post_time.text() == ""
    va = _record(model, VA)
    view.pre_time.setText("3")
    view.post_time.setText("5")
    view.apply_times.click()
    _wait(app, lambda: not model.is_running)
    assert model.applied.ir_sample_time_s == 3.0 and model.applied.ir_post_sample_time_s == 5.0
    assert model.draft.ir_sample_time_s == 3.0                                              # Setup shows the same
    drops = ctl.ir_drops("d")[model.events[0].event_id]
    assert drops.pre_pulse.measured.relative_time_s == 3.0 and drops.post_pulse.measured.relative_time_s == 5.0
    assert _record(model, VA) is va                                                          # GITT VA: independent
    dela = _record(model, gregorithm.DiffusionMethod.GITT_SPHERE_FULL)
    assert dela.details["delacourt_ir"]["sample_time_s"] == 3.0                             # A-02 correction follows
    view.post_time.setText("2")
    view.apply_times.click()
    _wait(app, lambda: not model.is_running)
    assert _record(model, gregorithm.DiffusionMethod.GITT_SPHERE_FULL) is dela               # post time: no D


def test_sample_time_can_be_changed_for_one_impulse_only(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline, methods=("gitt_va", "delacourt_fixed"), particle_radius=1.0)
    model = _run(app, ctl, "d", ctl.calculate_all)
    page = _page(app, ctl)
    page.go("ir")
    first, second = model.events[0].event_id, model.events[1].event_id
    others = {r.identity: r for r in model.records if r.identity.event_id != first}
    pre = page.ir_view.charts["pre_pulse"]
    page.ir_view.local_pre.setText("4")
    page.ir_view.local_apply.click()
    _wait(app, lambda: not model.is_running)
    drops = ctl.ir_drops("d")
    assert drops[first].pre_pulse.local_time and drops[first].pre_pulse.measured.relative_time_s == 4.0
    assert drops[second].pre_pulse.measured.relative_time_s == 1.0                          # other impulses: global
    assert drops[first].post_pulse.target_time_s == 1.0                                     # other side unchanged
    dela = next(r for r in model.records if r.identity.event_id == first
                and r.identity.method is gregorithm.DiffusionMethod.GITT_SPHERE_FULL)
    assert dela.details["delacourt_ir"]["target_time_s"] == 4.0                             # same sample
    assert all(model.record(i) is r for i, r in others.items())                             # only this impulse
    assert "this impulse" in dict((k.text(), v.text()) for k, v in pre.cells)["Selection"]
    assert "✎" in page.ir_view.list.topLevelItem(0).text(0)
    page.ir_view.local_reset.click()                                                        # back to the global time
    _wait(app, lambda: not model.is_running)
    assert not ctl.ir_drops("d")[first].pre_pulse.local_time and model.ir_samples == {}


def test_excluded_d_say_the_exact_cause(app, pipeline) -> None:
    from app.gitt_ici_review import exclusion_text
    assert exclusion_text("No D: ici_previous_interruption_missing") == (
        "No D: ICI pseudo-OCV needs the previous interruption (first impulse of the branch)")
    assert exclusion_text("Calculation failed: delacourt_ir_sample_missing: x") == (
        "Calculation failed: delacourt_ir_sample_missing: x")
    ctl, _ = _ready(app, pipeline, protocol="ici", methods=("ici",), ocv_methods=("ici_intercept",))
    model = _run(app, ctl, "d", ctl.calculate_all)
    ctl.accept_rests("d", None)
    ctl.accept_pulses("d", None)
    page = _page(app, ctl)
    page.go("results")
    assert len(page.results.visible_points()) == sum(r.diffusion_m2_s is not None for r in model.records) > 0
    assert "previous interruption" in page.results.message.text()


def test_fit_of_lists_one_entry_per_distinct_fit(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline, methods=("gitt_va", "gitt_delta", "delacourt_fixed"), particle_radius=1.0,
                    ocv_methods=("rest_sample", "relaxation_extrapolated"))
    model = _run(app, ctl, "d", ctl.calculate_all)
    page = _page(app, ctl)
    page.go("fit")
    view = page.fit_view
    view.list.setCurrentItem(view.list.topLevelItem(1))                     # both OCV sources exist from impulse 2
    app.processEvents()
    entries = [view.series.itemText(i) for i in range(view.series.count())]
    # GITT Δ has no fit; the OCV source does not change the GITT VA √t fit, so it is one entry. Delacourt's fixed
    # offset uses the OCV of the rest before, so its fits differ and keep the OCV in the label.
    assert entries[0] == "GITT VA" and not any(e.startswith("GITT Δ") for e in entries)     # catalogue order
    assert sum(e.startswith("GITT VA") for e in entries) == 1
    assert {e for e in entries if e.startswith("Delacourt")} == {"Delacourt (fixed offset) · Sampled OCV",
                                                                  "Delacourt (fixed offset) · Extrapolated OCV"}
    event = model.events[1].event_id
    assert sum(r.identity.event_id == event for r in model.records) == 6     # every series is still computed
    assert "GITT Δ · Sampled OCV" in view.values.as_text()                      # and listed with its D


def test_results_split_charge_and_discharge_with_two_colours(app, pipeline) -> None:
    from app.widgets.gitt_ici_results_view import BRANCH_COLORS
    ctl, _ = _ready(app, pipeline, methods=("gitt_va",))
    model = _run(app, ctl, "d", ctl.calculate_all)
    ctl.accept_rests("d", None)
    ctl.accept_pulses("d", None)
    page = _page(app, ctl)
    page.go("results")
    view = page.results
    assert view.colour.currentData() == "branch" and view.branch.currentData() == "both"
    branches = {r.coordinates.polarity for r in model.records}
    colours = {s.pen().color().name().upper() for item in view._items["d"] for s in item.points()}
    assert colours == {BRANCH_COLORS["charge" if p > 0 else "discharge"].upper() for p in branches}
    total = len(view.visible_points())
    view.branch.setCurrentIndex(view.branch.findData("discharge" if min(branches) < 0 else "charge"))
    app.processEvents()
    assert len(view.visible_points()) == total                                # the fixture has one branch only
    view.branch.setCurrentIndex(view.branch.findData("charge" if min(branches) < 0 else "discharge"))
    app.processEvents()
    assert view.visible_points() == []                                        # the other branch is hidden
    view.colour.setCurrentIndex(view.colour.findData("method"))
    view.branch.setCurrentIndex(view.branch.findData("both"))
    app.processEvents()
    from app.widgets.gitt_ici_results_view import D_COLORS
    colours = {s.pen().color().name().upper() for item in view._items["d"] for s in item.points()}
    assert colours == {D_COLORS[0].upper()}                                   # one method: the app's first colour
    symbols = {s.symbol() for item in view._items["d"] for s in item.points()}
    assert symbols == {"o" if min(branches) > 0 else "t"}                     # the branch is told by the marker


def test_gitt_ici_charts_use_the_app_palette() -> None:
    """Regression: GITT / ICI had its own colours (red / blue branches and others); every chart colour now comes
    from the app's CHART colours and the 10-colour series palette shared with dQ/dV and Comparison."""
    from app.dqdv import CYCLE_PALETTE
    from app.explorer import CHART
    from app.widgets import gitt_ici_chart, gitt_ici_ir_view, gitt_ici_ocv_view, gitt_ici_results_view as rv
    allowed = {c.upper() for c in CYCLE_PALETTE} | {c.upper() for c in CHART.values()}
    used = (set(gitt_ici_chart.PALETTE.values()) | set(gitt_ici_chart.OTHER_FITS) | set(gitt_ici_ir_view.COLORS.values())
            | set(gitt_ici_ocv_view.COLORS.values()) | set(rv.D_COLORS) | set(rv.OCV_COLORS.values())
            | set(rv.R_COLORS.values()) | set(rv.BRANCH_COLORS.values()))
    assert {c.upper() for c in used} <= allowed
    assert rv.BRANCH_COLORS == {"charge": CHART["y1"], "discharge": CHART["y2"]}


def test_one_window_is_applied_to_every_impulse(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline)
    model = _run(app, ctl, "d", ctl.calculate_all)
    page = _page(app, ctl)
    page.go("fit")
    view = page.fit_view
    view.win_start.setText("10")
    view.win_end.setText("80")
    view.win_apply_all.click()
    _wait(app, lambda: not model.is_running)
    complete = [e.event_id for e in model.events if e.complete]
    assert model.windows == {(e, "pulse"): (10.0, 80.0) for e in complete}
    for event_index in range(len(complete)):
        assert _record(model, VA, event_index).details["window"]["requested"]["bounds"] == [10.0, 80.0]


def test_fit_of_choice_stays_when_another_impulse_is_opened(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline, methods=("gitt_va", "ici"))
    model = _run(app, ctl, "d", ctl.calculate_all)
    page = _page(app, ctl)
    page.go("fit")
    view = page.fit_view
    ici = next(i for i in range(view.series.count()) if view.series.itemText(i).startswith("ICI"))
    view.series.setCurrentIndex(ici)
    app.processEvents()
    assert model.fit_series[0] == "ici"
    for item in range(1, view.list.topLevelItemCount()):
        view.list.setCurrentItem(view.list.topLevelItem(item))
        app.processEvents()
        assert view.series.currentText().startswith("ICI")
    page.go("results")
    page.go("fit")
    assert view.series.currentText().startswith("ICI")


def test_window_bounds_are_editable_and_bound_the_statistical_search(app, pipeline) -> None:
    """Regression: the window times of Automatic analysis were disabled outside the fixed mode, although gregorithm
    accepts operator bounds as the search range of every automatic mode."""
    ctl, _ = _ready(app, pipeline, auto_analysis=True)
    page = _page(app, ctl)
    model = ctl.model("d")
    assert model.draft.window_mode == "auto_statistical"
    assert all(edit.isEnabled() for edit in page.setup.fixed["pulse"])
    assert not page.setup.fixed["rest_after"][0].isEnabled()                               # ICI not checked
    start, end = page.setup.fixed["pulse"]
    start.setText("5")
    end.setText("60")
    start.textEdited.emit("5")
    assert model.draft.fixed_pulse_window_s == (5.0, 60.0)
    assert model.draft.fixed_window("pulse") is None                   # no fixed-window preview in Pulse Fitting
    _run(app, ctl, "d", ctl.apply)
    window = _record(model, VA).details["window"]
    assert window["requested"] == {"mode": "auto_statistical", "bounds": [5.0, 60.0]}


def test_fixed_window_mode_uses_the_bounds_entered_in_setup(app, pipeline) -> None:
    from app.gitt_ici import validate
    assert validate(GittIciParams(half_cycles=(1,), **GEOMETRY, fixed_pulse_window_s=(5.0, 5.0)))
    ctl, _ = _ready(app, pipeline, auto_analysis=True, window_mode="auto_fixed", fixed_pulse_window_s=(10.0, 80.0))
    page = _page(app, ctl)
    assert page.setup.fixed["pulse"][0].text() == "10" and page.setup.fixed["pulse"][0].isEnabled()
    assert not page.setup.fixed["rest_after"][0].isEnabled()                               # ICI not checked
    model = _run(app, ctl, "d", ctl.apply)
    requested = _record(model, VA).details["window"]["requested"]
    assert requested == {"mode": "auto_fixed", "bounds": [10.0, 80.0]}
    # Changing only the bounds and applying again recalculates the windows, not only stale results.
    ctl.set_params("d", replace(model.draft, fixed_pulse_window_s=(20.0, 90.0)))
    _run(app, ctl, "d", ctl.apply)
    assert _record(model, VA).details["window"]["requested"]["bounds"] == [20.0, 90.0]


def test_diffusion_coefficients_are_plotted_on_a_log_axis_by_default(app, pipeline) -> None:
    """D spans decades, so Diffusion Results opens with Y1 logarithmic; linear stays available ."""
    ctl, _ = _ready(app, pipeline)
    page = _page(app, ctl)
    assert page.results.options()["log"] is True
    page.results.scale.setCurrentIndex(page.results.scale.findData(False))
    assert page.results.options()["log"] is False


def test_results_chart_style_and_colours_are_edited_in_the_dock(app, pipeline) -> None:
    """Like the other result pages, Diffusion Results has a plot style (lines / markers / both), a marker size and
    charge / discharge colours, edited in the GITT / ICI dock; lines only keep the points clickable."""
    from app.widgets.gitt_ici_dock import GittIciSettingsDock
    from app.widgets.gitt_ici_results_view import BRANCH_COLORS
    ctl, _ = _ready(app, pipeline)
    model = _run(app, ctl, "d", ctl.calculate_all)
    ctl.accept_rests("d", None)
    ctl.accept_pulses("d", None)
    _wait(app, lambda: not model.is_running)
    page = _page(app, ctl)
    dock = GittIciSettingsDock(page.theme, page.setup.advanced_panel, page.results)
    page.go("results")
    results = page.results
    assert results.plot_style == "markers" and dock.style.value() == "markers"
    drawn = len(results.visible_points("d"))
    assert drawn

    def items(kind):
        return [i for i in results.pi.items if isinstance(i, kind)]
    assert not items(pg.PlotDataItem)
    dock.style._btn["lines_markers"].click()
    assert results.plot_style == "lines_markers" and items(pg.PlotDataItem)
    dock.style._btn["lines"].click()
    assert len(results.visible_points("d")) == drawn                                        # still clickable
    dock.size._btn["12"].click()
    assert results.marker_size == 12
    dock.colors["charge"].colorChosen.emit("#123456")
    assert results.branch_colors["charge"] == "#123456" and "#123456" in results.branch_legend.text()
    results.reset_colors()
    dock.refresh()
    assert results.branch_colors == BRANCH_COLORS


def test_setup_says_where_the_particle_radius_is_used(app) -> None:
    """Regression: entering r_p with the statistical window and no Delacourt changed nothing and said nothing."""
    from app.gitt_ici import GittIciParams, particle_radius_use
    base = GittIciParams(methods=("gitt_va",), particle_radius=5.0)
    assert "not used" in particle_radius_use(base)
    assert "Delacourt" in particle_radius_use(replace(base, methods=("gitt_va", "delacourt_fixed")))
    guided = replace(base, auto_analysis=True, window_mode="physically_guided")
    assert "physically guided window" in particle_radius_use(guided) and "used by" in particle_radius_use(guided)


def test_physical_n_d_says_why(app, pipeline) -> None:
    """The Physical cell of the fit values explains N/D: only the physically guided window checks the useful time."""
    from app.gitt_ici import physical_note
    ctl, _ = _ready(app, pipeline)
    model = _run(app, ctl, "d", ctl.calculate_all)
    record = _record(model, VA)
    assert "physically guided window" in physical_note(record)
    page = _page(app, ctl)
    page.go("fit")
    table = page.fit_view.values
    col = [table.horizontalHeaderItem(j).text() for j in range(table.columnCount())].index("Physical")
    va = next(i for i in range(table.rowCount()) if "VA" in table.item(i, 0).text())
    assert "physically guided window" in table.item(va, col).toolTip()
    assert all("useful time" in table.item(i, col).toolTip() for i in range(table.rowCount()))


# --- IR Drop layout, local times and acceptance ---------------------------------------------------------------------


def test_ir_charts_stay_side_by_side_in_a_narrow_window(app, pipeline) -> None:
    """Regression: the pre-pulse and post-pulse charts used to move one under the other when the window shrank;
    they now always share one row, as V(t) and V(√t) in Pulse Fitting."""
    ctl, _ = _ready(app, pipeline)
    _run(app, ctl, "d", ctl.calculate_all)
    page = _page(app, ctl)
    page.go("ir")
    page.show()
    for width in (1600, 900, 700):
        page.resize(width, 800)
        app.processEvents()
        pre, post = page.ir_view.charts["pre_pulse"], page.ir_view.charts["post_pulse"]
        assert pre.geometry().top() == post.geometry().top(), width                     # same row
        assert pre.geometry().right() < post.geometry().left(), width                   # pre left, post right
        assert abs(pre.width() - post.width()) <= 2, width                              # each half of the width
    page.hide()


def test_one_impulse_can_use_different_pre_and_post_pulse_times(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline, methods=("gitt_va", "delacourt_fixed"), particle_radius=1.0)
    model = _run(app, ctl, "d", ctl.calculate_all)
    page = _page(app, ctl)
    page.go("ir")
    view = page.ir_view
    first, second = model.events[0].event_id, model.events[1].event_id
    assert view.local_pre.text() == "" and view.local_pre.placeholderText() == "global 1"
    view.local_pre.setText("4")
    view.local_post.setText("7")
    view.local_apply.click()
    _wait(app, lambda: not model.is_running)
    drops = ctl.ir_drops("d")
    assert drops[first].pre_pulse.measured.relative_time_s == 4.0 and drops[first].pre_pulse.local_time
    assert drops[first].post_pulse.measured.relative_time_s == 7.0 and drops[first].post_pulse.local_time
    assert drops[second].pre_pulse.target_time_s == drops[second].post_pulse.target_time_s == 1.0
    assert view.local_pre.text() == "4" and view.local_post.text() == "7"
    view.local_post.setText("")                                                             # post back to global
    view.local_apply.click()
    _wait(app, lambda: not model.is_running)
    drops = ctl.ir_drops("d")
    assert drops[first].pre_pulse.local_time and not drops[first].post_pulse.local_time
    view.pre_time.setText("2")                                                              # all impulses
    view.apply_times.click()
    _wait(app, lambda: not model.is_running)
    drops = ctl.ir_drops("d")
    assert drops[first].pre_pulse.target_time_s == 4.0                                     # local time kept
    assert drops[second].pre_pulse.target_time_s == 2.0


def test_ir_drops_are_accepted_like_rests_and_fits_and_gate_only_the_resistance(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline)
    model = _run(app, ctl, "d", ctl.calculate_all)
    page = _page(app, ctl)
    page.go("ir")
    view = page.ir_view
    first, second = model.events[0].event_id, model.events[1].event_id
    assert set(ctl.ir_states("d").values()) == {PENDING} and "still to review" in page.next_hint.text()
    view.list.setCurrentItem(view.list.topLevelItem(0))
    app.processEvents()
    assert view.state.text() == "● Pending" and view.accept.isEnabled() and not view.revoke.isEnabled()
    view.accept.click()                                                                    # accept, open the next
    app.processEvents()
    assert ctl.ir_states("d")[first] == ACCEPTED and model.open_event == second
    assert ctl.accepted_ir("d") == {first}
    snapshot = model.snapshot.fingerprint
    ctl.set_ir_local_times("d", first, {"post_pulse": 3.0})                                # a new value: Modified
    assert ctl.ir_states("d")[first] == MODIFIED and ctl.accepted_ir("d") == set()
    ctl.reject_ir("d", second)
    ctl.accept_ir("d", None)                                                               # all valid, not rejected
    states = ctl.ir_states("d")
    assert states[second] == "Rejected" and states[first] == ACCEPTED
    assert ctl.accepted_ir("d") == {e.event_id for e in model.events} - {second}
    assert model.snapshot.fingerprint == snapshot                                          # D never gated by IR
    page.go("ir")
    assert page.next_hint.text() == "Area complete"
    ctl.revoke_ir("d", first)
    assert ctl.ir_states("d")[first] == PENDING


# --- OCV: sampled time and per-rest extrapolation ------------------------------------------------------------------


def test_sampled_ocv_time_can_be_set_for_all_rests_or_one_rest(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline)
    model = _run(app, ctl, "d", ctl.calculate_all)
    page = _page(app, ctl)
    page.go("ocv")
    view = page.ocv_view
    second = ctl.rest_ocv("d")[1].rest_segment_id
    va = _record(model, VA)
    view.sample_time.setText("30")                                                         # every rest
    view.apply_settings.click()
    _wait(app, lambda: not model.is_running)
    rests = ctl.rest_ocv("d")
    at = lambda ocv, t: ocv + 0.003 * math.sqrt(60) - 0.003 * math.sqrt(t)                 # noqa: E731
    assert model.applied.ocv_sample_time_s == 30.0 and model.draft.ocv_sample_time_s == 30.0
    assert rests[1].sampled.voltage_v == pytest.approx(at(3.51, 30), rel=1e-12, abs=0)
    assert _record(model, VA) is not va                                                    # D follows the new OCV
    page._open_rest(second)
    assert "t ≤ 30 s (all rests)" in view.sampled_text.text()
    view.local_time.setText("10")                                                          # this rest only
    view.time_apply.click()
    _wait(app, lambda: not model.is_running)
    rests = ctl.rest_ocv("d")
    assert rests[1].sampled.voltage_v == pytest.approx(at(3.51, 10), rel=1e-12, abs=0)
    assert rests[2].sampled.voltage_v == pytest.approx(at(3.52, 30), rel=1e-12, abs=0)    # other rests: global
    assert model.ocv_samples == {second: ("time", 10.0)} and "(this rest)" in view.sampled_text.text()
    view.last_sample.click()                                                               # back to the global rule
    _wait(app, lambda: not model.is_running)
    assert model.ocv_samples == {}
    view.sample_time.setText("")                                                           # last sample again
    view.apply_settings.click()
    _wait(app, lambda: not model.is_running)
    assert model.applied.ocv_sample_time_s is None
    assert ctl.rest_ocv("d")[1].sampled.voltage_v == pytest.approx(3.51, rel=1e-12, abs=0)


def test_extrapolation_of_one_rest_does_not_change_the_others(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline, ocv_methods=("rest_sample", "relaxation_extrapolated"))
    model = _run(app, ctl, "d", ctl.calculate_all)
    page = _page(app, ctl)
    page.go("ocv")
    view = page.ocv_view
    before = ctl.rest_ocv("d")
    second = before[1].rest_segment_id
    page._open_rest(second)
    assert view.local_fit.text() == "" and view.local_fit.placeholderText().startswith("global ")
    others = {r.identity: r for r in model.records}
    view.local_target.setText("900")
    view.local_fit.setText("0.5")
    view.local_apply.click()
    _wait(app, lambda: not model.is_running)
    after = ctl.rest_ocv("d")
    assert model.ocv_overrides == {second: 900.0} and model.ocv_fit_overrides == {second: 0.5}
    assert after[1].fit.target_time_s == 900.0
    assert after[1].fit.fit_start_s == pytest.approx(0.5 * after[1].fit.fit_end_s, rel=1e-12, abs=0)
    assert after[2].fit == before[2].fit and after[3].fit == before[3].fit                 # the others unchanged
    changed = {i for i, r in others.items() if model.record(i) is not r}
    events = {e.event_id for e in model.events if second in (e.rest_before_segment_id, e.rest_after_segment_id)}
    assert changed and all(i.event_id in events for i in changed)                          # only dependent results
    assert all(i.ocv_source.value == "relaxation_extrapolated" for i in changed)
    assert "local fit start" in view.fit_text.text()
    view.local_reset.click()
    _wait(app, lambda: not model.is_running)
    assert model.ocv_overrides == {} and model.ocv_fit_overrides == {}
    assert ctl.rest_ocv("d")[1].fit == before[1].fit
