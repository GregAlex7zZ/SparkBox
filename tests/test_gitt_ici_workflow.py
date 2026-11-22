"""GITT / ICI GUI: pure state, controller flows on a real gregorithm pipeline, and the page.

The synthetic GITT file has three charge pulses with an exact √t response, so GITT VA has the analytical value
4/π · (V/A)² · (ΔEs / (τ · dE/d√t))² checked against gregorithm through the GUI (no science in the GUI).
Flows covered: protocol and method defaults, review and recalculation, cancellation, draft/applied settings and the redesigned
Setup validation and intermediate acceptances (rest periods, pulse fits) that decide which D are used.
"""
from __future__ import annotations

import math
import os
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gregorithm
import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from app.elaboration import CHARGE_FIRST, ElabParams, ElabResult
from app.gitt_ici import (DEFAULT_METHODS, GittIciParams, GittIciState, SegmentTrace, accept_all_label, advisories,
                          extrapolation_ratio, for_protocol, format_d, format_int_set, impulse_rows, parse_int_set,
                          parse_optional_positive, selected_series, validate, with_scope)
from app.gitt_ici_review import ACCEPTED, MODIFIED, NOT_NEEDED, PENDING
from app.gitt_ici_controller import GittIciController
from app.gregorithm_backend import GregorithmBackend
from app.state import Dataset, Section, SessionState
from app.theme import ThemeManager
from app.widgets.gitt_ici_page import GittIciPage

GEOMETRY = dict(active_volume=1e-3, active_volume_unit="cm3", effective_area=1.0, effective_area_unit="cm2")
EXPECTED_VA = 4 / math.pi * 1e-10 * (0.01 / (100 * 0.004)) ** 2        # m²/s for V = 1e-9 m³, A = 1e-4 m²
VA = gregorithm.DiffusionMethod.GITT_VA
DELTA = gregorithm.DiffusionMethod.GITT_DELTA


def _gitt_csv(path: Path) -> None:
    lines = ["time_s,current_a,voltage_v"]
    now, ocv = 0, 3.5
    for _ in range(60):
        now += 1
        lines.append(f"{now},0,{ocv}")
    for _ in range(3):
        for k in range(1, 101):
            now += 1
            lines.append(f"{now},0.001,{ocv + 0.005 + 0.004 * math.sqrt(k):.15f}")
        ocv += 0.01
        intercept = ocv + 0.003 * math.sqrt(60)
        for k in range(1, 61):
            now += 1
            lines.append(f"{now},0,{intercept - 0.003 * math.sqrt(k):.15f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def pipeline():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        root = Path(directory)
        _gitt_csv(root / "gitt.csv")
        backend = GregorithmBackend(root / "store")
        backend.inspect_source(str(root / "gitt.csv"))
        backend.import_auto(str(root / "gitt.csv"), "d", lambda _p: None, gregorithm.CancellationToken())
        elab = backend.elaborate("d", ElabParams(CHARGE_FIRST), lambda _p: None, gregorithm.CancellationToken())
        yield backend, elab
        QThreadPool.globalInstance().waitForDone(10000)
        backend.release_result(elab.result_id)


def _session(elab=None, ds_id="d"):
    state = SessionState()
    dataset = Dataset(ds_id, "sample", "sample.csv")
    state.add(dataset)
    if elab is not None:
        state.mark_elaborated(ds_id, elab)
    return state, dataset


def _wait(app, cond, timeout=30.0):
    end = time.monotonic() + timeout
    while not cond():
        app.processEvents()
        QThreadPool.globalInstance().waitForDone(10)
        if time.monotonic() > end:
            raise AssertionError("timed out")
    app.processEvents()


def _run(app, ctl, ds_id, action, *args):
    action(ds_id, *args)
    _wait(app, lambda: not ctl.model(ds_id).is_running)
    model = ctl.model(ds_id)
    assert not model.error, model.error
    return model


def _ready(app, pipeline, **params):
    backend, elab = pipeline
    state, dataset = _session(elab)
    ctl = GittIciController(state, backend)
    ctl.set_params("d", GittIciParams(half_cycles=(1,), **{**GEOMETRY, **params}))
    return ctl, dataset


def _record(model, method, event_index=0):
    event_id = model.events[event_index].event_id
    return next(r for r in model.records if r.identity.event_id == event_id and r.identity.method is method)


# --- pure state --------------------------------------------------------------------------------------------------


def test_section_is_present_and_unlocks_only_after_elaboration() -> None:
    state, dataset = _session()
    assert Section.GITT_ICI in state.availability()
    assert not state.availability()[Section.GITT_ICI]
    state.mark_elaborated("d", ElabResult("result", 1, 1, 1, 1, dataset.elab.params))
    assert state.availability()[Section.GITT_ICI]


def test_defaults_follow_d31_d32_and_protocol_switch_resets_preselection() -> None:
    params = GittIciParams()
    assert params.protocol == "gitt" and params.auto_analysis is False                      # explicit, automatic analysis off
    assert params.methods == ("gitt_va", "gitt_delta")                                       # GITT
    assert params.active_volume is None and params.effective_area is None                   # never prefilled
    ici = for_protocol(params, "ici")
    assert ici.methods == DEFAULT_METHODS["ici"] == ("ici",) and ici.ocv_methods == ("ici_intercept",)
    assert selected_series(ici) == (("ici", "ici_intercept"),)                              # pseudo-OCV default
    assert selected_series(params) == (("gitt_va", "rest_sample"), ("gitt_delta", "rest_sample"))
    one = GittIciParams(methods=("gitt_va", "ici", "delacourt_fixed"), ocv_methods=("relaxation_extrapolated",))
    assert selected_series(one) == (("gitt_va", "relaxation_extrapolated"), ("ici", "relaxation_extrapolated"),
                                    ("delacourt_fixed", "relaxation_extrapolated"))
    many = GittIciParams(methods=("gitt_va", "ici"),
                         ocv_methods=("rest_sample", "ici_intercept", "relaxation_extrapolated"))
    assert selected_series(many) == (("gitt_va", "rest_sample"), ("gitt_va", "relaxation_extrapolated"),
                                     ("ici", "rest_sample"), ("ici", "ici_intercept"),
                                     ("ici", "relaxation_extrapolated"))                    # multiple OCV methods
    pseudo = GittIciParams(protocol="ici", methods=("ici", "delacourt_fixed"), ocv_methods=("ici_intercept",))
    assert selected_series(pseudo) == (("ici", "ici_intercept"),)                           # never a silent switch
    assert with_scope(GittIciParams(cycles=(2,)), "half_cycle", 5).scope == ("half_cycle", 5)
    assert with_scope(GittIciParams(half_cycles=(5,)), "cycle", 2).half_cycles == ()        # mutually exclusive
    with pytest.raises(ValueError):
        for_protocol(params, "auto")


def test_parsing_validation_and_non_blocking_advisories() -> None:
    assert parse_optional_positive("") == (None, "")
    assert parse_optional_positive("2,5") == (2.5, "")
    assert parse_optional_positive("0")[1] and parse_optional_positive("nan")[1]
    assert parse_int_set("1, 3-5") == ((1, 3, 4, 5), "")
    assert parse_int_set("5-3")[1] and parse_int_set("0")[1]
    assert format_int_set((1, 3, 4, 5)) == "1, 3-5"
    ready = GittIciParams(half_cycles=(1,), **GEOMETRY)
    assert validate(ready) == []

    def says(params, text):
        return any(text in e for e in validate(params))
    assert says(GittIciParams(**GEOMETRY), "exactly one cycle or one half-cycle")
    assert says(replace(ready, cycles=(1,)), "exactly one cycle or one half-cycle")
    assert says(replace(ready, half_cycles=(1, 2)), "exactly one cycle or one half-cycle")
    assert says(replace(ready, methods=()), "at least one calculation method")
    assert says(replace(ready, active_volume=None), "Active volume is required")
    assert says(replace(ready, methods=("delacourt_fixed",)), "Particle radius is required by Delacourt")
    assert validate(replace(ready, methods=("delacourt_fixed",), active_volume=None, effective_area=None,
                            particle_radius=1.0)) == []                                     # r_p only for Delacourt
    assert says(replace(ready, ocv_methods=("ici_intercept", "rest_sample")), "Pseudo OCV needs the ICI method")
    assert says(replace(ready, methods=("ici", "gitt_va"), ocv_methods=("ici_intercept",)),
                "select Sampled or Extrapolated OCV for GITT VA")
    assert validate(replace(ready, methods=("ici", "gitt_va"), ocv_methods=("ici_intercept", "rest_sample"))) == []
    assert says(replace(ready, ocv_methods=()), "at least one OCV method")
    assert says(replace(ready, protocol="ici", methods=("gitt_va",)), "Not available with ICI: GITT VA")
    assert says(replace(ready, protocol="ici", methods=("ici", "delacourt_fixed"), ocv_methods=("rest_sample",),
                        particle_radius=1.0), "Not available with ICI: Delacourt")          # GITT only
    assert says(replace(ready, thresholds=(5.0, 2.0, 5.0, 15.0)), "Quality thresholds")
    guided = replace(ready, auto_analysis=True, window_mode="physically_guided")
    assert len(validate(guided)) == 2                                                       # source and r_p
    assert ready.guidance_coefficient == 0.0032                                             # editable default
    assert says(replace(guided, guidance_coefficient=None), "physical coefficient")
    notes = advisories(GittIciParams(methods=("gitt_va", "delacourt_fixed")))
    assert any("V_act" in n for n in notes) and any("r_p" in n for n in notes)


def test_draft_is_applied_only_at_run_start_and_restored_on_cancel_or_failure() -> None:
    model = GittIciState()
    draft = GittIciParams(protocol="ici", methods=("ici",), active_volume=2.0)
    model.set_draft(draft)
    assert model.applied != draft
    model.windows = {("e", "pulse"): (1.0, 9.0)}
    model.begin("global", apply_draft=True)
    model.windows = {}
    assert model.applied == draft and model.is_running
    model.cancel()
    assert model.applied == GittIciParams() and model.windows == {("e", "pulse"): (1.0, 9.0)}
    assert not model.is_running
    model.begin("global", apply_draft=True)
    model.fail("Boom")
    assert model.applied == GittIciParams() and model.error == "Boom"
    assert format_d(2e-14) == "2e-10" and format_d(2e-14, "m2/s") == "2e-14"               # display only
    assert format_d(None) == format_d(float("nan")) == "N/D"
    assert extrapolation_ratio(600.0, SegmentTrace(1, (1, 2), (0.0, 60.0))) == 10.0


def test_impulse_list_is_complete_one_row_per_impulse(app, pipeline) -> None:
    ctl, dataset = _ready(app, pipeline)
    model = _run(app, ctl, "d", ctl.calculate_all)
    first = _record(model, VA).identity
    model.snapshot = model.snapshot.reject(first)
    rows = impulse_rows(model.events, model.records)
    assert [r.number for r in rows] == [1, 2, 3]                                            # natural order
    assert all(len(r.series) == 2 for r in rows)
    assert rows[0].series[0].state == "Rejected"                                            # nothing filtered
    assert rows[0].series[0].label == "GITT VA · Sampled OCV"
    assert accept_all_label(first) == "Accept all: GITT VA · Sampled OCV"                   # scope is evident


# --- controller on the real pipeline -----------------------------------------------------------------------------


def test_apply_recognises_without_fitting_when_automatic_analysis_is_off(app, pipeline) -> None:
    ctl, _dataset = _ready(app, pipeline)
    model = _run(app, ctl, "d", ctl.apply)
    assert len(model.events) == 3 and all(e.complete for e in model.events)
    assert model.records == ()                                                              # no fit
    ctl.open_detail("d", model.events[0].event_id)                                          # opening fits nothing
    assert model.records == () and model.trace is not None and len(model.trace.pulse.time_s) == 100
    assert model.trace.origin_s["pulse"] == model.trace.rest_before.time_s[-1]


def test_automatic_analysis_and_analytical_gitt_va(app, pipeline) -> None:
    ctl, _dataset = _ready(app, pipeline, auto_analysis=True)
    model = _run(app, ctl, "d", ctl.apply)
    assert len(model.records) == 6
    for i in range(3):
        assert _record(model, VA, i).diffusion_m2_s == pytest.approx(EXPECTED_VA, rel=1e-7)
        assert _record(model, VA, i).state is gregorithm.ResultState.PROVISIONAL
    assert model.done == model.total == 3 and "results calculated" in model.notice


def test_d_is_used_only_after_its_rest_periods_and_pulse_fit_are_accepted(app, pipeline) -> None:
    ctl, _dataset = _ready(app, pipeline)
    model = _run(app, ctl, "d", ctl.calculate_all)
    states = lambda method: [r.state for r in model.records if r.identity.method is method]  # noqa: E731
    assert set(states(VA)) == set(states(DELTA)) == {gregorithm.ResultState.PROVISIONAL}
    assert set(ctl.pulse_states("d").values()) == {PENDING}
    assert set(ctl.rest_states("d").values()) == {PENDING} and len(model.rest_ocv) == 4
    ctl.accept_rests("d", None)
    assert set(states(DELTA)) == {gregorithm.ResultState.ACCEPTED}                          # GITT Δ: no fit needed
    assert set(states(VA)) == {gregorithm.ResultState.PROVISIONAL}                          # fits still pending
    first, second, third = model.events
    ctl.accept_pulses("d", [first.event_id])
    assert _record(model, VA, 0).state is gregorithm.ResultState.ACCEPTED
    assert _record(model, VA, 1).state is gregorithm.ResultState.PROVISIONAL
    ctl.revoke_rest("d", first.rest_after_segment_id)                                       # shared by #1 and #2
    assert ctl.rest_states("d")[first.rest_after_segment_id] == PENDING
    assert _record(model, VA, 0).state is gregorithm.ResultState.PROVISIONAL
    assert _record(model, DELTA, 0).state is _record(model, DELTA, 1).state is gregorithm.ResultState.PROVISIONAL
    assert _record(model, DELTA, 2).state is gregorithm.ResultState.ACCEPTED
    assert sum(r.exportable for r in model.records) == 1
    only_delta = GittIciParams(half_cycles=(1,), methods=("gitt_delta",), **GEOMETRY)
    assert ctl.pulse_states("d")[third.event_id] == PENDING
    ctl.set_params("d", only_delta)
    _run(app, ctl, "d", ctl.calculate_all)
    assert set(ctl.pulse_states("d").values()) == {NOT_NEEDED}                              # nothing to accept


def test_acceptance_survives_an_identical_recalculation_but_not_a_changed_fit(app, pipeline) -> None:
    ctl, _dataset = _ready(app, pipeline)
    model = _run(app, ctl, "d", ctl.calculate_all)
    ctl.accept_rests("d", None)
    ctl.accept_pulses("d", None)
    assert all(r.state is gregorithm.ResultState.ACCEPTED for r in model.records)
    event = model.events[1].event_id
    _run(app, ctl, "d", ctl.reapply_automatic, event)                                       # same result
    assert all(r.state is gregorithm.ResultState.ACCEPTED for r in model.records)          # same fingerprint
    assert len(model.records) == 6                                                          # one per identity
    model.selected = _record(model, VA, 1).identity
    _run(app, ctl, "d", ctl.apply_window, event, "pulse", 10.0, 80.0)                      # the examined fit changed
    assert ctl.pulse_states("d")[event] == MODIFIED
    assert _record(model, VA, 1).state is gregorithm.ResultState.PROVISIONAL               # review again
    assert _record(model, DELTA, 1).state is gregorithm.ResultState.ACCEPTED               # needs no fit
    ctl.accept_pulses("d", [event])
    assert ctl.pulse_states("d")[event] == ACCEPTED
    assert _record(model, VA, 1).state is gregorithm.ResultState.ACCEPTED


def test_manual_window_recalculates_only_window_series_of_that_impulse(app, pipeline) -> None:
    ctl, _dataset = _ready(app, pipeline)
    model = _run(app, ctl, "d", ctl.calculate_all)
    before = {r.identity: r.calculation_fingerprint for r in model.records}
    event = model.events[0].event_id
    model.selected = _record(model, VA).identity
    _run(app, ctl, "d", ctl.apply_window, event, "pulse", 10.0, 80.0)
    va = _record(model, VA)
    assert va.details["window"]["requested"] == {"mode": "manual", "bounds": [10.0, 80.0]}
    assert va.diffusion_m2_s == pytest.approx(EXPECTED_VA, rel=1e-7)                       # exact √t data
    changed = {i for i, fp in before.items() if model.record(i).calculation_fingerprint != fp}
    assert changed == {va.identity}                                                         # GITT Δ untouched
    assert model.windows == {(event, "pulse"): (10.0, 80.0)}
    _run(app, ctl, "d", ctl.calculate_all)                                                  # drops manual windows
    assert model.windows == {} and _record(model, VA).details["window"]["requested"]["mode"] == "auto_statistical"


def test_edits_change_nothing_until_apply_then_dependent_results_update(app, pipeline) -> None:
    ctl, _dataset = _ready(app, pipeline)
    model = _run(app, ctl, "d", ctl.calculate_all)
    va = _record(model, VA)
    ctl.set_params("d", GittIciParams(half_cycles=(1,), **{**GEOMETRY, "active_volume": 2e-3}))
    assert _record(model, VA) is va and model.applied.active_volume == 1e-3
    _run(app, ctl, "d", ctl.apply)                                                          # results exist -> recalc
    assert _record(model, VA).diffusion_m2_s == pytest.approx(4 * EXPECTED_VA, rel=1e-7)   # D ∝ V²
    assert all(not r.stale for r in model.records)


def test_ocv_target_override_is_per_rest_and_recalculates_only_the_impulses_sharing_it(app, pipeline) -> None:
    ctl, _dataset = _ready(app, pipeline, ocv_methods=("relaxation_extrapolated",), methods=("gitt_va",))
    model = _run(app, ctl, "d", ctl.calculate_all)
    first, second, third = model.events
    assert _record(model, VA, 0).error.startswith("before_relaxation_reference_missing")   # first rest: no origin
    shared = second.rest_after_segment_id
    assert shared == third.rest_before_segment_id
    untouched = _record(model, VA, 0)
    _run(app, ctl, "d", ctl.set_rest_target, shared, 30.0)
    assert model.ocv_overrides == {shared: 30.0}
    assert _record(model, VA, 1).details["ocv_after"]["target_time_s"] == 30.0
    assert _record(model, VA, 2).details["ocv_before"]["target_time_s"] == 30.0
    assert _record(model, VA, 0) is untouched and not any(r.stale for r in model.records)  # targeted
    rest = next(r for r in model.rest_ocv if r.rest_segment_id == shared)
    assert rest.extrapolated.target_time_s == 30.0 and rest.sampled is not None             # both kept
    _run(app, ctl, "d", ctl.set_rest_target, shared, None)
    assert model.ocv_overrides == {}


def test_cancellation_preserves_snapshot_and_applied_parameters(app, pipeline) -> None:
    backend, elab = pipeline

    class Cancelling:
        def __getattr__(self, name):
            return getattr(backend, name)

        def analyze_gitt_ici(self, *_a, **_k):
            raise gregorithm.OperationCancelled("gitt_ici")

    state, _ = _session(elab)
    ctl = GittIciController(state, backend)
    ctl.set_params("d", GittIciParams(half_cycles=(1,), **GEOMETRY))
    model = _run(app, ctl, "d", ctl.calculate_all)
    snapshot, applied = model.snapshot, model.applied
    ctl.backend = Cancelling()
    ctl.set_params("d", replace(applied, active_volume=5e-3))
    ctl.calculate_all("d")
    _wait(app, lambda: not model.is_running)
    assert model.snapshot is snapshot and model.applied == applied
    assert "unchanged" in model.notice


def test_elaboration_replacement_discards_a_late_result(app, pipeline) -> None:
    backend, elab = pipeline
    gate = threading.Event()

    class Slow:
        def __getattr__(self, name):
            return getattr(backend, name)

        def analyze_gitt_ici(self, *a, **k):
            gate.wait(10)
            return backend.analyze_gitt_ici(*a, **k)

    state, _ = _session(elab)
    ctl = GittIciController(state, Slow())
    ctl.set_params("d", GittIciParams(half_cycles=(1,), **GEOMETRY))
    gate.set()
    model = _run(app, ctl, "d", ctl.calculate_all)
    first = model.records[0].identity
    gate.clear()
    ctl.calculate_all("d")
    assert model.is_running
    ctl.accept_rests("d", None)                                                             # refused while running
    ctl.accept_pulses("d", None)
    assert model.rest_reviews == {} and model.pulse_reviews == {}
    assert model.record(first).state is gregorithm.ResultState.PROVISIONAL
    ctl.elaboration_replaced("d")
    gate.set()
    QThreadPool.globalInstance().waitForDone(10000)
    app.processEvents()
    assert model.snapshot is None and model.events == () and not model.is_running


# --- page ----------------------------------------------------------------------------------------------------------


def test_setup_inputs_are_per_dataset_and_proceed_follows_the_conditions(app) -> None:
    state, _ = _session(ElabResult("result", 3, 6, 9, 100, ElabParams(CHARGE_FIRST)))
    state.add(Dataset("e", "other", "other.csv"))
    ctl = GittIciController(state, object())
    page = GittIciPage(ThemeManager(app), ctl)
    page.show_dataset("d")
    setup = page.setup
    assert not setup.auto.isChecked()
    assert setup.methods["gitt_va"].isChecked() and not setup.methods["ici"].isChecked()
    assert not setup.ocv["ici_intercept"].isEnabled()                                       # Pseudo needs ICI
    assert not setup.proceed_btn.isEnabled() and "exactly one cycle" in setup.checklist.text()
    assert setup.need["particle_radius"].text() == "Optional"
    assert setup.need["particle_radius"].property("required") is False             # plain chip
    assert setup.need["active_volume"].property("required") is True                # highlighted in the accent colour
    setup.methods["delacourt_fixed"].setChecked(True)
    assert setup.need["particle_radius"].text() == "Required"
    assert setup.need["particle_radius"].property("required") is True
    setup.methods["delacourt_fixed"].setChecked(False)
    setup.scope_kind["half_cycle"].setChecked(True)
    assert setup.scope_value.count() == 7                                                   # Choose… + 6
    setup.scope_value.setCurrentIndex(setup.scope_value.findData(2))
    for key, value in (("active_volume", "3"), ("effective_area", "1")):
        setup.fields[key].setText(value)
        setup.fields[key].textEdited.emit(value)
    model = ctl.model("d")
    assert model.draft.half_cycles == (2,) and model.draft.cycles == ()
    assert model.draft.active_volume == 3.0 and model.applied.active_volume is None
    assert setup.proceed_btn.isEnabled() and "Ready" in setup.check_title.text()
    setup.protocol["ici"].setChecked(True)
    assert setup.methods["ici"].isChecked() and not setup.methods["gitt_va"].isChecked()
    assert not setup.methods["gitt_va"].isEnabled() and setup.ocv["ici_intercept"].isEnabled()
    assert not setup.methods["delacourt_fixed"].isEnabled()                                 # GITT only
    assert model.draft.ocv_methods == ("ici_intercept",)
    setup.ocv["rest_sample"].click()                                                        # multiple selection
    assert model.draft.ocv_methods == ("rest_sample", "ici_intercept")
    assert setup.ocv["rest_sample"].isChecked() and setup.ocv["ici_intercept"].isChecked()
    page.show_dataset("e")                                                                  # not carried over
    assert setup.fields["active_volume"].text() == "" and setup.protocol["gitt"].isChecked()
    setup.fields["particle_radius"].setText("-1")
    setup.fields["particle_radius"].textEdited.emit("-1")
    assert "greater than 0" in setup.checklist.text() and not setup.proceed_btn.isEnabled()
    assert setup.coefficient.text() == "0.0032"                                             # default value


def test_ici_protocol_uses_pseudo_ocv_and_shows_reasons_instead_of_zero(app, pipeline) -> None:
    ctl, _dataset = _ready(app, pipeline, protocol="ici", methods=("ici",), ocv_methods=("ici_intercept",))
    model = _run(app, ctl, "d", ctl.calculate_all)
    assert model.records and all(r.identity.method is gregorithm.DiffusionMethod.ICI for r in model.records)
    assert {r.identity.ocv_source.value for r in model.records} == {"ici_intercept"}
    rows = impulse_rows(model.events, model.records)
    for row in rows:
        for s in row.series:
            assert s.d_text != "0"
            assert (s.d_text == "N/D") == bool(s.reason)


def test_late_job_does_not_steal_the_cancellation_of_a_newer_job(app, pipeline) -> None:
    backend, elab = pipeline
    gates = [threading.Event(), threading.Event()]
    calls = []

    class Slow:
        def __getattr__(self, name):
            return getattr(backend, name)

        def scan_gitt_ici(self, *a, **k):
            gate = gates[len(calls)]
            calls.append(gate)
            gate.wait(10)
            return backend.scan_gitt_ici(*a, **k)

    state, _ = _session(elab)
    ctl = GittIciController(state, Slow())
    ctl.set_params("d", GittIciParams(half_cycles=(1,), **GEOMETRY))
    model = ctl.model("d")
    ctl.apply("d")                                                  # job A blocks in recognition
    _wait(app, lambda: len(calls) == 1)
    ctl.elaboration_replaced("d")                                   # A becomes late
    ctl.apply("d")                                                  # job B blocks too
    _wait(app, lambda: len(calls) == 2)
    gates[0].set()                                                  # A finishes late
    _wait(app, lambda: QThreadPool.globalInstance().activeThreadCount() <= 1)
    assert model.is_running and model.events == ()                  # A discarded
    ctl.cancel("d")                                                 # B is still cancellable
    assert model.cancel_requested
    gates[1].set()
    _wait(app, lambda: not model.is_running)
    assert model.events == () and "unchanged" in model.notice      # cancelled after recognition: nothing kept


def test_fixing_an_invalid_draft_clears_the_validation_message(app, pipeline) -> None:
    ctl, _dataset = _ready(app, pipeline, methods=())
    ctl.apply("d")
    model = ctl.model("d")
    assert "at least one calculation method" in model.error and not model.is_running
    ctl.set_params("d", GittIciParams(half_cycles=(1,), **GEOMETRY))
    assert model.error == ""


def test_physical_guidance_records_its_provisional_source_and_delacourt_runs(app, pipeline) -> None:
    ctl, _dataset = _ready(app, pipeline, particle_radius=50.0, methods=("gitt_va", "delacourt_fixed"))
    model = _run(app, ctl, "d", ctl.calculate_all)
    dela = _record(model, gregorithm.DiffusionMethod.GITT_SPHERE_FULL)
    assert dela.identity.variant == "fixed" and dela.state is not gregorithm.ResultState.ERROR
    guided = replace(model.applied, window_mode="physically_guided", guidance_source="gitt_va|rest_sample",
                     guidance_coefficient=0.0032)
    ctl.set_params("d", guided)
    _run(app, ctl, "d", ctl.calculate_all)
    guidance = _record(model, VA).details["guidance"]
    assert guidance["source"]["method"] == "gitt_va" and guidance["physical_coefficient"] == 0.0032
    assert guidance["source"]["provisional"] is True                                        # not accepted
    # r_p = 5 µm gives a guided useful time of about 1 s: fewer than five distinct times, so the result becomes
    # an error record with its reason instead of a fabricated D, and the list shows that reason.
    # Changing r_p makes the guiding D stale, so the first run falls back with an explicit reason (no
    # implicit fallback source); the next run uses the fresh D.
    ctl.set_params("d", replace(guided, particle_radius=5.0))
    _run(app, ctl, "d", ctl.apply)
    assert _record(model, VA).details["guidance"] == {"applied": False, "reason": "guidance_diffusion_unavailable"}
    _run(app, ctl, "d", ctl.calculate_all)
    va = _record(model, VA)
    assert va.state is gregorithm.ResultState.ERROR and "five distinct times" in va.error
    row = impulse_rows(model.events, model.records)[0]
    assert row.series[0].d_text == "N/D" and "five distinct times" in row.series[0].reason


def test_threshold_and_target_inputs_reach_gregorithm_settings() -> None:
    settings = GregorithmBackend._gitt_settings(
        GittIciParams(thresholds=(1.0, 3.0, 4.0, 9.0), ocv_target_s=600.0), {7: 30.0})
    t = settings.thresholds
    assert (t.linearity_green_pct, t.linearity_orange_pct, t.stability_green_pct, t.stability_orange_pct) == (
        1.0, 3.0, 4.0, 9.0)
    assert settings.ocv_targets.effective_target_s(7) == 30.0                               # override
    assert settings.ocv_targets.effective_target_s(8) == 600.0


def test_demo_backend_without_gitt_ici_reports_instead_of_failing() -> None:
    state, dataset = _session()
    state.mark_elaborated("d", ElabResult("result", 1, 1, 1, 1, dataset.elab.params))
    ctl = GittIciController(state, object())
    ctl.apply("d")
    assert not ctl.model("d").is_running and "demo mode" in ctl.model("d").notice


def test_gitt_delta_ir_correction_is_drawn_once_next_to_the_raw_voltage(app, pipeline) -> None:
    ctl, _dataset = _ready(app, pipeline, methods=("gitt_delta",), ir_correction="selected_sample")
    model = _run(app, ctl, "d", ctl.calculate_all)
    delta = _record(model, DELTA)
    assert delta.details["ir"]["mode"] == "selected_sample"                                 # mode reached core
    page = GittIciPage(ThemeManager(app), ctl)
    page.show_dataset("d")
    page.go("fit")
    app.processEvents()
    names = [item.name() for item in page.fit_view.chart.pt.getPlotItem().listDataItems()]
    assert names == ["Raw voltage", f"V − c (IR selected_sample, c = {delta.details['ir']['offset_v']:.4g} V)"]
    assert "applied once" in page.fit_view.chart.caption.text()
    assert not page.fit_view.win_apply.isEnabled()                                          # GITT Δ: no window


