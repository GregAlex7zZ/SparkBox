"""GITT / ICI IR drop analysis: pure rows and cells, controller measurements, manual samples with targeted
invalidation, the IR drop tab and the resistance series of the results chart.

Synthetic GITT file of test_gitt_ici_workflow (rest 3.5 V at 0 A; pulses ``ocv + 0.005 + 0.004·√k`` at 1 mA, one sample per
second; rests ``ocv' + 0.003·√60 − 0.003·√k``). Expected values are written from these formulas, never from the code
under test: at 1 s the pre-pulse ΔV is 9 mV with ΔI = +1 mA (R_on = 9 Ω) and the post-pulse ΔV of the first impulse
is ``3.51 + 0.003·√60 − 0.003 − 3.545`` with ΔI = −1 mA.
"""
from __future__ import annotations

import math
from dataclasses import replace

import gregorithm
import pytest

from app.gitt_ici import GittIciParams, GittIciState
from app.gitt_ici_ir import (dependent_series, drop_cells, format_resistance, ir_rows, reason_text,
                             resistance_chart_data, zoom_range)
from app.theme import ThemeManager
from app.widgets.gitt_ici_page import GittIciPage
from test_gitt_ici_workflow import DELTA, GEOMETRY, _ready, _run, _wait, app, pipeline  # noqa: F401  (shared fixtures)

PRE_DV = 0.005 + 0.004
POST_DV = 3.51 + 0.003 * math.sqrt(60) - 0.003 - (3.5 + 0.005 + 0.004 * 10)


def _recognised(app, pipeline, **params):
    ctl, _ = _ready(app, pipeline, **params)
    model = _run(app, ctl, "d", ctl.apply)                                    # recognition only (automatic analysis off)
    assert model.events and not model.records
    return ctl, model


# --- pure ----------------------------------------------------------------------------------------------------------


def test_dependent_series_follow_the_ir_corrections_and_a02() -> None:
    base = GittIciParams(methods=("gitt_va", "gitt_delta", "delacourt_fixed", "delacourt_free"))
    assert dependent_series(base) == (("delacourt_fixed", "rest_sample"),)                 # A-02: Delacourt only
    sampled = replace(base, ir_correction="selected_sample")
    assert dependent_series(sampled) == (("gitt_delta", "rest_sample"), ("delacourt_fixed", "rest_sample"))
    assert dependent_series(replace(base, ir_correction="gitt_intercept")) == (("delacourt_fixed", "rest_sample"),)
    assert format_resistance(0.0123) == "12.3 mΩ" and format_resistance(9.0) == "9 Ω"
    assert format_resistance(2500.0) == "2.5 kΩ" and format_resistance(None) == "N/D"


def test_cancelled_job_restores_manual_ir_samples() -> None:
    state = GittIciState()
    state.ir_samples = {("e", "pre_pulse"): 5}
    state.begin("local", False)
    state.ir_samples = {("e", "pre_pulse"): 9}
    state.cancel()
    assert state.ir_samples == {("e", "pre_pulse"): 5}
    state.reset_results()
    assert state.ir_samples == {} and state.ir_drops == {}


# --- controller ----------------------------------------------------------------------------------------------------


def test_both_ir_drops_are_measured_on_the_documented_real_samples(app, pipeline) -> None:
    ctl, model = _recognised(app, pipeline)
    drops = ctl.ir_drops("d")
    assert set(drops) == {e.event_id for e in model.events}
    first = drops[model.events[0].event_id]
    ctl.open_detail("d", model.events[0].event_id)
    trace = model.trace
    pre, post = first.pre_pulse, first.post_pulse
    assert pre.reference.source_row == trace.rest_before.rows[-1]                          # last rest sample
    assert pre.measured.source_row == trace.pulse.rows[0] and pre.measured.relative_time_s == 1.0
    assert pre.delta_voltage_v == pytest.approx(PRE_DV, rel=1e-9, abs=0)
    assert pre.delta_current_a == pytest.approx(1e-3, rel=1e-9, abs=0)
    assert pre.resistance_ohm == pytest.approx(PRE_DV / 1e-3, rel=1e-9, abs=0)
    assert post.reference.source_row == trace.pulse.rows[-1]                               # last sample under current
    assert post.measured.source_row == trace.rest_after.rows[0]
    assert post.delta_voltage_v == pytest.approx(POST_DV, rel=1e-9, abs=0)
    assert post.delta_current_a == pytest.approx(-1e-3, rel=1e-9, abs=0)                  # signed, distinct side
    rows = ir_rows(model.events, drops)
    assert rows[0].pre_mv == "+9" and rows[0].valid == "Both valid"
    assert rows[0].post_mv == f"{POST_DV * 1e3:+.4g}"
    cells = dict(drop_cells(pre))
    assert cells["R_on = |ΔV/ΔI|"] == "9 Ω" and cells["ΔI"] == "+1 mA"
    assert f"row {pre.measured.source_row}" in cells["Measured sample"]
    assert "t ≤ 1 s" in cells["Selection"]
    lo, hi = zoom_range(trace.rest_before.time_s + trace.pulse.time_s, pre)
    assert lo < 0 < pre.measured.relative_time_s < hi


def test_post_pulse_time_is_independent_and_needs_no_recalculation(app, pipeline) -> None:
    ctl, model = _recognised(app, pipeline)
    ctl.set_params("d", replace(model.applied, ir_post_sample_time_s=4.0))
    _run(app, ctl, "d", ctl.apply)
    first = ctl.ir_drops("d")[model.events[0].event_id]
    assert first.pre_pulse.measured.relative_time_s == 1.0
    assert first.post_pulse.measured.relative_time_s == 4.0
    expected = 3.51 + 0.003 * math.sqrt(60) - 0.003 * 2 - 3.545
    assert first.post_pulse.delta_voltage_v == pytest.approx(expected, rel=1e-9, abs=0)


def test_missing_or_invalid_ir_drops_are_reported_without_values(app, pipeline) -> None:
    ctl, model = _recognised(app, pipeline)
    drops = dict(ctl.ir_drops("d"))
    eid = model.events[0].event_id
    broken = replace(drops[eid].post_pulse, measured=None, delta_voltage_v=None, delta_current_a=None,
                     resistance_ohm=None, reason="rest_after_missing")
    drops[eid] = replace(drops[eid], post_pulse=broken)
    row = ir_rows(model.events, drops)[0]
    assert row.post_mv == "N/D" and row.valid == "Pre-pulse only"
    assert reason_text("rest_after_missing") in row.tooltip
    assert dict(drop_cells(broken))["Not available"] == reason_text("rest_after_missing")
    chart = resistance_chart_data(model.events, drops, axis="capacity_mah", log=False)
    r_off = next(s for s in chart.d_series if s.label == "R_off")
    assert len(r_off.points) == len(model.events) - 1                                     # no artificial point
    assert "1 IR drop(s) without R" in chart.omitted[0]


def test_manual_sample_is_an_exact_row_and_invalid_rows_are_refused(app, pipeline) -> None:
    ctl, model = _recognised(app, pipeline)
    eid = model.events[0].event_id
    ctl.open_detail("d", eid)
    third = model.trace.pulse.rows[2]
    ctl.set_ir_sample("d", eid, "pre_pulse", third)
    assert not model.is_running                                                           # nothing depends on it
    pre = ctl.ir_drops("d")[eid].pre_pulse
    assert pre.manual and pre.measured.source_row == third
    assert pre.delta_voltage_v == pytest.approx(0.005 + 0.004 * math.sqrt(3), rel=1e-9, abs=0)
    ctl.set_ir_sample("d", eid, "pre_pulse", model.trace.rest_after.rows[0])              # not a pulse sample
    assert "does not belong" in model.notice and model.ir_samples == {(eid, "pre_pulse"): third}
    ctl.set_ir_sample("d", eid, "pre_pulse", None)                                        # automatic again
    assert not ctl.ir_drops("d")[eid].pre_pulse.manual and model.ir_samples == {}


def test_manual_pre_pulse_sample_recalculates_only_the_dependent_series(app, pipeline) -> None:
    backend, _elab = pipeline
    calls = []

    class Spy:
        def __getattr__(self, name):
            return getattr(backend, name)

        def analyze_gitt_ici(self, *a, **k):
            calls.append((k["event_id"], k["series"], dict(k.get("ir_samples") or {})))
            return backend.analyze_gitt_ici(*a, **k)

    ctl, _ = _ready(app, pipeline, methods=("gitt_va", "gitt_delta"), ir_correction="selected_sample")
    ctl.backend = Spy()
    model = _run(app, ctl, "d", ctl.calculate_all)
    first, second = (e.event_id for e in model.events[:2])
    before = {r.identity: r for r in model.records}
    ctl.open_detail("d", first)
    row = model.trace.pulse.rows[4]
    calls.clear()
    ctl.set_ir_sample("d", first, "pre_pulse", row)
    _wait(app, lambda: not model.is_running)
    assert calls == [(first, (("gitt_delta", "rest_sample"),), {(first, "pre_pulse"): row})]
    delta = next(r for r in model.records if r.identity.event_id == first and r.identity.method is DELTA)
    assert delta.details["ir"]["sample_row"] == row == ctl.ir_drops("d")[first].pre_pulse.measured.source_row
    assert delta.details["ir"]["offset_v"] == pytest.approx(0.005 + 0.004 * math.sqrt(5), rel=1e-9, abs=0)
    changed = {i for i, r in before.items() if model.record(i) is not r}
    assert changed == {delta.identity}                                                    # nothing else touched
    calls.clear()
    snapshot = model.snapshot
    ctl.open_detail("d", second)
    ctl.set_ir_sample("d", second, "post_pulse", model.trace.rest_after.rows[2])
    assert not calls and model.snapshot is snapshot                                       # R_off feeds no D
    assert ctl.ir_drops("d")[second].post_pulse.manual


# --- page ----------------------------------------------------------------------------------------------------------


def test_ir_tab_draws_the_samples_used_and_a_click_selects_a_sample(app, pipeline) -> None:
    ctl, model = _recognised(app, pipeline)
    page = GittIciPage(ThemeManager(app), ctl)
    page.show_dataset("d")
    view = page.ir_view
    assert view.list.topLevelItemCount() == len(model.events)
    view.list.setCurrentItem(view.list.topLevelItem(1))                                  # select from the list
    app.processEvents()
    eid = model.events[1].event_id
    assert model.open_event == eid and "Impulse #2" in view.title.text()
    drops = ctl.ir_drops("d")[eid]
    for side, chart in view.charts.items():                                               # both charts updated
        drop = getattr(drops, side)
        assert chart.marks == {"reference": drop.reference.source_row, "measured": drop.measured.source_row}
    pre_values = {k.text(): v.text() for k, v in view.charts["pre_pulse"].cells if not k.isHidden()}
    assert pre_values["IR drop ΔV"] == "+9 mV" and pre_values["R_on = |ΔV/ΔI|"] == "9 Ω"
    row = model.trace.pulse.rows[3]
    view.charts["pre_pulse"].sampleClicked.emit(row)                                     # click on a real sample
    app.processEvents()
    assert view.charts["pre_pulse"].marks["measured"] == row
    assert view.local_reset.isEnabled() and view.pre_time.placeholderText() == f"row {row}"
    assert "✎" in view.list.topLevelItem(1).text(0)
    view.local_reset.click()                                                              # back to the global time
    app.processEvents()
    assert view.charts["pre_pulse"].marks["measured"] == drops.pre_pulse.measured.source_row


def test_resistance_series_feed_the_results_chart_and_open_the_ir_tab(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline)
    page = GittIciPage(ThemeManager(app), ctl)
    page.show_dataset("d")
    model = _run(app, ctl, "d", ctl.calculate_all)
    page.go("results")
    view = page.results
    view.boxes["R_on"].setChecked(True)
    view.boxes["R_off"].setChecked(True)                                                  # both at once
    assert view.visible_points("r") == []                                                 # IR drops not accepted
    assert "IR drop not accepted" in view.message.text()
    ctl.accept_ir("d", None)
    drops = ctl.ir_drops("d")
    r_on = sorted(view.visible_points("r"), key=lambda p: (p.tooltip.startswith("R_off"), p.x))
    assert len(r_on) == 2 * len(model.events)
    first = drops[model.events[0].event_id].pre_pulse
    point = next(p for p in r_on if p.identity == model.events[0].event_id and p.tooltip.startswith("R_on"))
    assert point.x == pytest.approx(first.reference.capacity_ah * 1000, rel=1e-12, abs=0)
    assert point.y == pytest.approx(first.resistance_ohm, rel=1e-12, abs=0)              # same source
    assert view.y3.labelText.startswith("Y3") and view.y3.isVisible()                      # third axis
    spot = next(s for item in view._items["r"] for s in item.points()
                if s.data()[1].identity == model.events[1].event_id)
    view._clicked(None, [spot])
    assert model.open_event == model.events[1].event_id
    assert page.stack.currentWidget() is page.ir_view
    assert gregorithm.IrDropSide.PRE_PULSE.value == "pre_pulse"
