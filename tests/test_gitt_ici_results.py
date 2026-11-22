"""GITT / ICI GUI: results chart data, the Diffusion Results area, point-to-review link, export and an
end-to-end run of the five-area workflow in the main window.

Uses the synthetic three-pulse GITT pipeline of test_gitt_ici_workflow (exact √t response, analytical GITT VA).
"""
from __future__ import annotations

import csv
import math
import os
import tempfile
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pyarrow.parquet as pq
import pytest
from PySide6.QtCore import QThreadPool

from app.gitt_ici_plot import chart_data, tooltip, x_value
from app.gregorithm_backend import GregorithmBackend
from app.state import Section
from app.theme import ThemeManager
from app.widgets.gitt_ici_page import GittIciPage
from test_gitt_ici_workflow import (DELTA, EXPECTED_VA, VA, _gitt_csv, _ready, _record, _run, _wait,  # noqa: F401
                              app, pipeline)  # fixtures shared with the workflow suite


def _accept_va(ctl, model):
    """Accept every rest period and pulse fit: the D they unlock become accepted in gregorithm."""
    ctl.accept_rests("d", None)
    ctl.accept_pulses("d", None)


# --- chart data ----------------------------------------------------------------------------------------------------


def test_d_is_placed_at_pulse_start_capacity_with_hollow_proposed_points(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline)
    model = _run(app, ctl, "d", ctl.calculate_all)
    data = chart_data(model.records, model.events, unit="cm2/s", axis="capacity_mah", log=False)
    assert [s.key for s in data.d_series] == ["GITT VA · Sampled OCV · charge", "GITT Δ · Sampled OCV · charge"]
    va = data.d_series[0]
    for point, i in zip(va.points, range(3)):
        record = _record(model, VA, i)
        assert point.x == pytest.approx(record.coordinates.capacity_start_ah * 1000)
        assert point.y == pytest.approx(EXPECTED_VA * 1e4, rel=1e-7)                          # cm²/s
        assert not point.accepted                                                            # hollow
    _accept_va(ctl, model)
    model.snapshot = model.snapshot.revoke(_record(model, DELTA).identity).reject(_record(model, DELTA).identity)
    data = chart_data(model.records, model.events, unit="m2/s", axis="capacity_ah", log=True)
    assert all(p.accepted for p in data.d_series[0].points)
    assert len(data.d_series[1].points) == 2                                                  # rejected not drawn
    assert any("1 rejected" in m for m in data.omitted)
    assert data.y_label == "D [m²/s] · logarithmic scale"


def test_log_axis_omits_non_positive_d_and_says_so(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline, methods=("gitt_va",))
    model = _run(app, ctl, "d", ctl.calculate_all)
    first = _record(model, VA)
    negative = replace(first, result=replace(first.result, diffusion_m2_s=-1e-14))
    records = [negative, *model.records[1:]]
    linear = chart_data(records, model.events, unit="cm2/s", axis="capacity_mah", log=False)
    log = chart_data(records, model.events, unit="cm2/s", axis="capacity_mah", log=True)
    assert len(linear.d_series[0].points) == 3
    assert len(log.d_series[0].points) == 2                                                   # not shifted
    assert "D ≤ 0" in log.omitted[0]


def test_soc_axis_follows_the_branch_definition(app, pipeline) -> None:
    assert x_value(0.25, 1, 1, "soc", {1: 1.0}) == 25.0
    assert x_value(0.25, -1, 2, "soc", {2: 1.0}) == 75.0                                      # discharge 100 − p
    assert x_value(0.25, 1, 1, "soc", {1: 0.0}) is None                                       # Q_end = 0: unavailable
    ctl, _ = _ready(app, pipeline)
    model = _run(app, ctl, "d", ctl.calculate_all)
    assert set(model.branch_end_ah) == {1} and model.branch_end_ah[1] > 0
    data = chart_data(model.records, model.events, unit="cm2/s", axis="soc", log=False,
                      branch_end_ah=model.branch_end_ah)
    xs = [p.x for p in data.d_series[0].points]
    assert all(0 <= x < 100 for x in xs) and xs == sorted(xs)
    missing = chart_data(model.records, model.events, unit="cm2/s", axis="soc", log=False, branch_end_ah={})
    assert not missing.d_series and "cannot be placed" in missing.omitted[0]


def test_ocv_overlay_uses_each_rest_once_at_its_represented_capacity(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline, methods=("gitt_va", "ici"))
    model = _run(app, ctl, "d", ctl.calculate_all)
    data = chart_data(model.records, model.events, unit="cm2/s", axis="capacity_ah", log=False)
    sampled = next(s for s in data.ocv_series if s.label.startswith("Sampled"))
    assert len(sampled.points) == 4                                                           # rests 0, 2, 4, 6
    first = _record(model, VA, 0)
    assert sampled.points[0] == (first.details["ocv_before"]["capacity_ah"],
                                 first.details["ocv_before"]["voltage_v"])
    assert [x for x, _ in sampled.points] == sorted(x for x, _ in sampled.points)            # ordered
    pseudo = next(s for s in data.ocv_series if s.label.startswith("Pseudo"))
    assert "not equilibrium" in pseudo.label and len(pseudo.points) == 3


def test_tooltips_show_only_the_statistics_of_each_method(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline)
    model = _run(app, ctl, "d", ctl.calculate_all)
    va = tooltip(_record(model, VA), 1, "cm2/s", "Capacity = 0.1 mAh")
    delta = tooltip(_record(model, DELTA), 1, "cm2/s", "Capacity = 0.1 mAh")
    assert "Slope" in va and "R²" in va and "√t window" in va and "impulse #1" in va
    assert "Slope" not in delta and "ΔE_τ" in delta                                           # nothing invented
    assert "Statistical (fit) uncertainty" in va and "error bar" not in va


# --- widget, link to review, export --------------------------------------------------------------------------------


def test_diffusion_results_draw_only_used_d_with_ocv_and_resistance_axes(app, pipeline) -> None:
    ctl, _ = _ready(app, pipeline)
    page = GittIciPage(ThemeManager(app), ctl)
    page.show_dataset("d")
    model = _run(app, ctl, "d", ctl.calculate_all)
    page.go("results")
    view = page.results
    assert view.visible_points() == [] and "No D is used yet" in view.message.text()       # nothing accepted
    ctl.accept_rests("d", None)
    assert len(view.visible_points()) == 3                                                  # GITT Δ: no fit needed
    assert "Pulse fit not accepted" in view.message.text()                                  # reason of the others
    ctl.accept_pulses("d", None)
    assert len(view.visible_points()) == 6
    view.boxes["D:GITT Δ · Sampled OCV"].setChecked(False)                                  # choose the methods
    assert len(view.visible_points()) == 3
    view.scale.setCurrentIndex(view.scale.findData(True))                                   # logarithmic Y1
    first = view._items["d"][0].points()[0]
    assert first.pos().y() == pytest.approx(math.log10(EXPECTED_VA * 1e4), rel=1e-9)
    view.boxes["Sampled"].setChecked(True)                                                  # Y2 OCV curve
    assert view._items["ocv"] and view.pi.getAxis("right").labelText.startswith("Y2")
    view.boxes["R_on"].setChecked(True)                                                     # Y3 resistance
    assert view.visible_points("r") == []                                                   # IR not accepted yet
    ctl.accept_ir("d", None)
    drops = ctl.ir_drops("d")
    r = view.visible_points("r")
    assert sorted(p.y for p in r) == pytest.approx(sorted(d.pre_pulse.resistance_ohm for d in drops.values()),
                                                   rel=1e-12)                                # same IR values
    assert not view.boxes["Pseudo"].isEnabled()                                             # no ICI results
    item = view.axis.model().item(view.axis.findData("specific_mah_g"))
    assert not item.isEnabled() and "active material mass" in view.axis_note.text()
    target = _record(model, VA, 2).identity
    spot = next(s for s in view._items["d"][0].points() if s.data()[1].identity == target)
    view._clicked(None, [spot])
    assert model.open_event == target.event_id and page.stack.currentWidget() is page.fit_view


def test_export_writes_one_row_per_impulse_split_by_branch_and_never_an_empty_file(app, pipeline) -> None:
    """The export holds only the analysed quantities (position, IR drops, OCV, slopes, D in use), one row per
    impulse, one file per branch; nothing is written without a D in use and nothing is overwritten."""
    ctl, _ = _ready(app, pipeline, methods=("gitt_va",))
    model = _run(app, ctl, "d", ctl.calculate_all)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        root = Path(directory)
        empty = root / "nothing" / "none.csv"
        ctl.export("d", str(empty), "csv")
        assert not empty.parent.exists() and "no file was created" in model.notice
        _accept_va(ctl, model)
        ctl.accept_ir("d", None)                                                              # R_on / R_off exported
        ctl.export("d", str(root / "accepted.csv"), "csv")
        _wait(app, lambda: not model.exporting)
        charge = root / "accepted - charge.csv"
        assert charge.is_file() and not (root / "accepted - discharge.csv").exists()          # the fixture charges
        with charge.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        assert len(rows) == 3
        heads = set(rows[0])
        assert {"Impulse", "Capacity [mAh]", "IR drop pre-pulse [mV]", "IR drop post-pulse [mV]", "R_on [Ω]",
                "Sampled OCV before [V]", "Slope GITT VA [V/√s]", "D GITT VA · Sampled OCV [cm²/s]"} <= heads
        assert not heads & {"method", "state", "diffusion_m2_s", "Branch"}                    # no technical columns
        assert float(rows[0]["D GITT VA · Sampled OCV [cm²/s]"]) == pytest.approx(EXPECTED_VA * 1e4, rel=1e-7)
        ctl.revoke_pulse("d", model.events[1].event_id)                                       # re-examine one impulse
        parquet = root / "after-revoke.parquet"
        ctl.export("d", str(parquet), "parquet", split=False)                                 # one file, Branch column
        _wait(app, lambda: not model.exporting)
        table = pq.read_table(parquet).to_pydict()
        assert table["Branch"] == ["Charge"] * 3
        assert table["D GITT VA · Sampled OCV [cm²/s]"][1] is None                            # not in use: empty
        chosen = root / "chosen.csv"
        ctl.export("d", str(chosen), "csv", ["capacity", "d:GITT VA · Sampled OCV"], split=False)
        _wait(app, lambda: not model.exporting)
        with chosen.open(newline="", encoding="utf-8") as stream:
            assert next(csv.reader(stream)) == ["Branch", "Impulse", "Capacity [mAh]",
                                                "D GITT VA · Sampled OCV [cm²/s]"]
        ctl.export("d", str(root / "accepted.csv"), "csv")                                    # never overwritten
        _wait(app, lambda: not model.exporting)
        assert "already exists" in model.notice


# --- end to end ----------------------------------------------------------------------------------------------------


def test_end_to_end_in_the_main_window(app) -> None:
    from app.main_window import MainWindow
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        root = Path(directory)
        _gitt_csv(root / "gitt.csv")
        theme = ThemeManager(app)
        win = MainWindow(GregorithmBackend(root / "store"), theme)
        win.show()
        win.start_import(str(root / "gitt.csv"), "auto")
        _wait(app, lambda: len(win.state.datasets) == 1
              and win.state.availability().get(Section.ELABORATION, False), timeout=60)
        ds = win.state.datasets[0].id
        win.elab.process(ds)
        _wait(app, lambda: win.state.datasets[0].elab.has_result, timeout=60)
        assert win.state.availability()[Section.GITT_ICI]
        win.go(Section.GITT_ICI)
        page = win.gitt_ici_page
        setup = page.setup
        # Advanced Settings opens the GITT / ICI dock with the IR and indicator settings, never an empty placeholder.
        assert win.dock.currentWidget() is win.gitt_ici_dock
        assert win.gitt_ici_dock.isAncestorOf(setup.ir_time) and not page.isAncestorOf(setup.ir_time)
        assert all(win.gitt_ici_dock.isAncestorOf(edit) for edit in (setup.ir_mode, setup.ir_post_time,
                                                                      *setup.thresholds))
        assert not page.step_buttons["ir"].isEnabled()                                      # nothing recognised yet
        setup.scope_kind["half_cycle"].setChecked(True)
        setup.scope_value.setCurrentIndex(setup.scope_value.findData(1))
        for key, value in (("active_volume", "0.001"), ("effective_area", "1")):
            setup.fields[key].setText(value)
            setup.fields[key].textEdited.emit(value)
        setup.auto.setChecked(True)
        assert setup.proceed_btn.isEnabled()
        setup.proceed_btn.click()                                                           # 1 → 2
        model = win.gitt_ici.model(ds)
        _wait(app, lambda: not model.is_running and bool(model.records), timeout=60)
        assert not model.error and page.stack.currentWidget() is page.ir_view               # IR Drop opens
        assert page.ir_view.list.topLevelItemCount() == 3
        page.step_buttons["ocv"].click()                                                    # 3
        page.ocv_view.accept_all.click()
        page.step_buttons["fit"].click()                                                    # 4
        page.fit_view.accept_all.click()
        page.step_buttons["results"].click()                                                # 5
        assert len(page.results.visible_points()) == 6
        out = root / "e2e.csv"
        win.gitt_ici.export(ds, str(out), "csv")
        _wait(app, lambda: not model.exporting)
        assert (root / "e2e - charge.csv").is_file() and "Exported 3 impulses" in model.notice
        QThreadPool.globalInstance().waitForDone(10000)
        win.close()


