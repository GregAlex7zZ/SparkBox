from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

import gregorithm
import numpy as np
import pyarrow.parquet as pq
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from app.backend import AmbiguousMapping, ColumnMapping, CompareRequest, ElabCancelled, ExplorerRequest, FlatStyle
from app.capacity import CapParams
from app.correction import CorrParams
from app.dcir import ACCEPTED, DetectionParams, ManualValue
from app.dqdv import DqdvParams
from app.elaboration import CHARGE_FIRST, DISCHARGE_FIRST, ElabParams
from app.gitt_ici import GittIciParams, selected_series
from app.gregorithm_backend import GregorithmBackend, _factor
from app.units import UnitPrefs


FIXTURES = Path(__file__).parent / "fixtures"


class GregorithmBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.backend = GregorithmBackend(Path(self.temp.name) / "results")

    def tearDown(self):
        self.backend.close()
        self.temp.cleanup()

    def test_auto_import_uses_real_gregorithm_and_bounded_view(self):
        source = FIXTURES / "canonical.csv"
        inspection = self.backend.inspect_source(str(source))
        self.assertEqual(inspection.columns[:3], ["time_s", "current_mA", "voltage_V"])
        token = gregorithm.CancellationToken()
        seen = []
        result = self.backend.import_auto(str(source), "dataset-a", seen.append, token)
        self.assertEqual((result.n_valid, result.n_discarded), (4, 2))
        self.assertTrue(result.canonical_path.is_file())
        rows = self.backend.get_rows("dataset-a", 0, 10)
        self.assertEqual(len(rows), 4)
        self.assertAlmostEqual(rows[1][0], 0.5 / 3600)
        self.assertAlmostEqual(rows[0][1], 1000.123456789)
        self.assertAlmostEqual(rows[-1][2], 3.600045678912)
        row, exact, nearest = self.backend.find_row_by_time("dataset-a", 2.75 / 3600)
        self.assertEqual(row, 2)
        self.assertTrue(exact)
        self.assertEqual(nearest, rows[2][0])
        self.assertTrue(seen)
        self.assertIsNone(seen[0].fraction)

    def test_manual_indices_map_to_names_and_duration_parser(self):
        source = FIXTURES / "duration_semicolon.csv"
        inspection = self.backend.inspect_source(str(source))
        self.assertEqual(inspection.delimiter, ";")
        with self.assertRaises(AmbiguousMapping):
            self.backend.import_auto(str(source), "ambiguous", lambda _p: None, gregorithm.CancellationToken())
        mapping = ColumnMapping(0, 1, 2, "hh:mm:ss.fff", "A", "mV")
        result = self.backend.import_mapped(str(source), mapping, lambda _p: None,
                                            gregorithm.CancellationToken(), "dataset-b")
        rows = self.backend.get_rows("dataset-b", 0, 3)
        self.assertAlmostEqual(rows[1][0], 0.125 / 3600)
        self.assertAlmostEqual(rows[1][1], 2.34567891)
        self.assertAlmostEqual(rows[1][2], 3.20025)
        self.assertAlmostEqual(result.duration_s, 1.25)

    def test_cancelled_import_publishes_no_result(self):
        source = FIXTURES / "canonical.csv"
        self.backend.inspect_source(str(source))
        token = gregorithm.CancellationToken()
        token.cancel()
        from app.backend import ImportCancelled
        with self.assertRaises(ImportCancelled):
            self.backend.import_auto(str(source), "cancelled", lambda _p: None, token)
        self.assertNotIn("cancelled", self.backend._views)

    def test_headerless_mode_is_rejected_explicitly(self):
        with self.assertRaisesRegex(ValueError, "without a header"):
            self.backend.inspect_source(str(FIXTURES / "canonical.csv"), has_header=False)

    def test_elaboration_matches_direct_core_and_exposes_true_tables(self):
        source = FIXTURES / "canonical.csv"
        self.backend.inspect_source(str(source))
        imported = self.backend.import_auto(str(source), "elab-a", lambda _p: None,
                                            gregorithm.CancellationToken())
        params = ElabParams(CHARGE_FIRST, 0.25, 2.5, 3.5)
        progress = []
        adapted = self.backend.elaborate("elab-a", params, progress.append,
                                         gregorithm.CancellationToken())
        direct_params = gregorithm.ElaborationParameters(
            rest_current_threshold_a=0.00025,
            cycle_order=gregorithm.CycleOrder.CHARGE_DISCHARGE,
            cc_relative_tolerance=0.025,
            cv_absolute_tolerance_v=0.0035,
        )
        direct = gregorithm.elaborate(gregorithm.ElaborationRequest(
            imported.canonical_path, imported.canonical_content_hash,
            Path(self.temp.name) / "direct", "sample", "job", "generation", direct_params))
        self.assertEqual((adapted.n_rows, adapted.n_half_cycles, adapted.n_cycles),
                         (direct.rows, direct.half_cycles, direct.cycles))
        self.assertEqual(self.backend.elab_table(adapted.result_id, "points").total, direct.rows)
        self.assertEqual(len(self.backend.elab_table(adapted.result_id, "points").columns), 12)
        segments = pq.ParquetFile(direct.ref.path / "indexes" / "segments.parquet").metadata.num_rows
        self.assertEqual(adapted.n_segments, segments)
        self.assertEqual(len(self.backend.elab_table(adapted.result_id, "segments").columns), 10)
        direct_row = pq.read_table(direct.ref.path / "points" / "elaborated.parquet").slice(0, 1).to_pylist()[0]
        gui_row = self.backend.get_elab_rows(adapted.result_id, "points", 0, 1)[0]
        self.assertAlmostEqual(gui_row[0], direct_row["time_s"] / 3600)
        self.assertAlmostEqual(gui_row[1], direct_row["current_a"] * 1000)
        self.assertAlmostEqual(gui_row[2], direct_row["voltage_v"])
        self.assertTrue(progress[-1].publishing)
        self.assertEqual(progress[-1].fraction, 1.0)

    def test_gitt_ici_adapter_consumes_only_the_elaboration_generation(self):
        source = FIXTURES / "canonical.csv"
        self.backend.inspect_source(str(source))
        self.backend.import_auto(str(source), "gitt-a", lambda _p: None,
                                 gregorithm.CancellationToken())
        elaboration = self.backend.elaborate("gitt-a", ElabParams(CHARGE_FIRST),
                                             lambda _p: None, gregorithm.CancellationToken())
        params = GittIciParams(methods=("gitt_va", "gitt_delta"))
        scan = self.backend.scan_gitt_ici("gitt-a", elaboration.result_id, params)
        self.assertIsInstance(scan.events, tuple)
        base, outcome, skipped = self.backend.analyze_gitt_ici(
            scan.scope_id, params, None, event_id=None, series=selected_series(params), windows={},
            ocv_overrides={}, automatic=True, invalidate=False, progress=lambda _p: None,
            cancel=gregorithm.CancellationToken())
        snapshot = gregorithm.commit_batch(base, outcome)
        self.assertIsInstance(snapshot, gregorithm.ResultSnapshot)
        self.assertIsInstance(skipped, tuple)
        self.backend.release_gitt_ici(scan.scope_id)
        with self.assertRaises(KeyError):
            self.backend.gitt_ici_trace(scan.scope_id, "missing")

    def test_both_cycle_orders_and_cancel_preserve_previous_result(self):
        source = FIXTURES / "canonical.csv"
        self.backend.inspect_source(str(source))
        self.backend.import_auto(str(source), "elab-b", lambda _p: None,
                                 gregorithm.CancellationToken())
        first = self.backend.elaborate("elab-b", ElabParams(CHARGE_FIRST), lambda _p: None,
                                       gregorithm.CancellationToken())
        second = self.backend.elaborate("elab-b", ElabParams(DISCHARGE_FIRST), lambda _p: None,
                                        gregorithm.CancellationToken())
        self.assertNotEqual(first.result_id, second.result_id)
        self.assertEqual(second.params.cycle_order, DISCHARGE_FIRST)
        token = gregorithm.CancellationToken()
        token.cancel()
        with self.assertRaises(ElabCancelled):
            self.backend.elaborate("elab-b", ElabParams(), lambda _p: None, token)
        self.assertEqual(self.backend.elab_table(second.result_id, "points").total, 4)

        during_work = gregorithm.CancellationToken()
        def cancel_work(event):
            if not event.publishing:
                during_work.cancel()
        with self.assertRaises(ElabCancelled):
            self.backend.elaborate("elab-b", ElabParams(), cancel_work, during_work)
        self.assertEqual(self.backend.elab_table(second.result_id, "points").total, 4)

        during_publish = gregorithm.CancellationToken()
        def cancel_publish(event):
            if event.publishing:
                during_publish.cancel()
        with self.assertRaises(ElabCancelled):
            self.backend.elaborate("elab-b", ElabParams(), cancel_publish, during_publish)
        self.assertEqual(self.backend.elab_table(second.result_id, "points").total, 4)

    def test_capacity_matches_the_public_core_and_exposes_its_17_columns(self):
        """The GUI adapter supplies lineage and units only; gregorithm owns values."""
        source = FIXTURES / "canonical.csv"
        self.backend.inspect_source(str(source))
        self.backend.import_auto(str(source), "capacity-a", lambda _p: None, gregorithm.CancellationToken())
        elaboration = self.backend.elaborate("capacity-a", ElabParams(CHARGE_FIRST), lambda _p: None,
                                             gregorithm.CancellationToken())
        params = CapParams(CHARGE_FIRST, 2, None, None)
        seen = []
        adapted = self.backend.analyze_capacity("capacity-a", elaboration.result_id, params, seen.append,
                                                 gregorithm.CancellationToken())
        manifest = self.backend._read_json(self.backend._result_view(elaboration.result_id).result_path / "manifest.json")
        direct = gregorithm.analyze_capacity(gregorithm.CapacityRequest(
            self.backend._result_view(elaboration.result_id).result_path,
            manifest.get("fingerprint") or manifest["content_hash"], Path(self.temp.name) / "capacity-direct",
            "sample", "job", "generation", gregorithm.CapacityParameters(
                cycle_order=gregorithm.CycleOrder.CHARGE_DISCHARGE, reference_cycle=2)))
        direct_table = pq.read_table(direct.ref.path / "tables" / "capacity.parquet")
        adapted_table = self.backend.capacity_table(adapted.result_id, UnitPrefs())
        self.assertEqual(len(adapted_table.values), 17)
        self.assertEqual(set(adapted_table.values), set(direct_table.column_names))
        for name in direct_table.column_names:
            expected = direct_table[name].to_numpy() * _factor(name, UnitPrefs())
            np.testing.assert_allclose(adapted_table.values[name], expected, equal_nan=True)
        self.assertEqual(adapted.n_cycles, direct.cycles)
        self.assertTrue(seen[-1].publishing)

    def test_dcir_detection_review_and_measurement_match_the_public_core(self):
        source = FIXTURES / "dcir_pulse.csv"
        self.backend.inspect_source(str(source))
        self.backend.import_auto(str(source), "dcir-a", lambda _p: None, gregorithm.CancellationToken())
        elaboration = self.backend.elaborate("dcir-a", ElabParams(CHARGE_FIRST), lambda _p: None,
                                             gregorithm.CancellationToken())
        params = DetectionParams(rest_before_s=0.0, rest_after_s=0.0, pulse_s=40.0)
        detected = self.backend.detect_dcir("dcir-a", elaboration.result_id, params, UnitPrefs(), lambda _p: None,
                                            gregorithm.CancellationToken())
        self.assertEqual(len(detected.events), 1)
        event = detected.events[0]
        preview = self.backend.measure_dcir(detected.result_id, [event.event_id], 10.0, UnitPrefs(),
                                            threading.Event())
        self.assertEqual(len(preview), 1)
        published = self.backend.publish_dcir(detected.result_id, {event.event_id: ACCEPTED}, 10.0, UnitPrefs(),
                                              lambda _p: None, gregorithm.CancellationToken())
        self.assertEqual(len(published.measurements), 1)
        shown = published.measurements[0]
        core_event = self.backend._dcir_events[published.result_id][0]
        core_measurement = gregorithm.measure_automatic_from_generation(
            self.backend._result_view(elaboration.result_id).result_path,
            self.backend._elab_result_fingerprint(elaboration.result_id)["fingerprint"], core_event,
            requested_time_s=10.0, label="DCIR1")
        self.assertAlmostEqual(shown.i_meas, core_measurement.i_meas_a * 1000.0)
        self.assertAlmostEqual(shown.resistance, core_measurement.resistance_ohm)      # default display unit: Ω
        self.assertEqual(published.labels, ((event.event_id, "DCIR1"),))

    def test_dcir_trace_time_zero_is_the_v0_reference_sample(self):
        """The chart and the core share one time origin, V0: V0 is drawn at 0 and V1 at exactly actual_time_s."""
        source = FIXTURES / "dcir_pulse.csv"
        self.backend.inspect_source(str(source))
        self.backend.import_auto(str(source), "dcir-v0", lambda _p: None, gregorithm.CancellationToken())
        elaboration = self.backend.elaborate("dcir-v0", ElabParams(CHARGE_FIRST), lambda _p: None,
                                             gregorithm.CancellationToken())
        detected = self.backend.detect_dcir("dcir-v0", elaboration.result_id,
                                            DetectionParams(rest_before_s=0.0, rest_after_s=0.0, pulse_s=40.0),
                                            UnitPrefs(), lambda _p: None, gregorithm.CancellationToken())
        event = detected.events[0]
        m = self.backend.measure_dcir(detected.result_id, [event.event_id], 10.0, UnitPrefs(), threading.Event())[0]
        trace = self.backend.dcir_trace(detected.result_id, event.event_id, UnitPrefs(), 10.0)
        rows, t = np.asarray(trace.rows), np.asarray(trace.t_rel_s, float)
        self.assertEqual(float(t[rows == m.reference_row][0]), 0.0)
        self.assertAlmostEqual(float(t[rows == m.voltage_row][0]), m.actual_time_s)
        self.assertGreater(trace.zones["pulse"][0], 0.0)                 # first pulse sample is after V0

    def _dcir_pulse(self, key: str):
        """dcir_pulse.csv: V0 at t=20 s, pulse samples at t=30, 40, 50, 60 s (-1000 mA)."""
        source = FIXTURES / "dcir_pulse.csv"
        self.backend.inspect_source(str(source))
        self.backend.import_auto(str(source), key, lambda _p: None, gregorithm.CancellationToken())
        elaboration = self.backend.elaborate(key, ElabParams(CHARGE_FIRST), lambda _p: None,
                                             gregorithm.CancellationToken())
        detected = self.backend.detect_dcir(key, elaboration.result_id,
                                            DetectionParams(rest_before_s=0.0, rest_after_s=0.0, pulse_s=40.0),
                                            UnitPrefs(), lambda _p: None, gregorithm.CancellationToken())
        return elaboration, detected

    def test_manual_preview_and_create_use_the_core_result_and_picked_samples(self):
        """Regression: create_manual_event returns one DcirMeasurement; unpacking it as (event, measurement) failed, so
        the dialog preview always failed and 'Add Measurement' stayed disabled. A preview must not create an event."""
        _elab, detected = self._dcir_pulse("dcir-manual")
        did = detected.result_id
        tr = self.backend.dcir_trace(did, detected.events[0].event_id, UnitPrefs(), 10.0)
        t = np.asarray(tr.t_rel_s, float)

        def pick(at, quantity):
            i = int(np.flatnonzero(t == at)[0])
            value = float((tr.current if quantity == "i" else tr.voltage)[i])     # display units (mA, V)
            return ManualValue(value, int(tr.rows[i]), float(tr.t0_s + t[i]), int(tr.cycle[i]))

        values = {"i0": pick(0.0, "i"), "v0": pick(0.0, "v"), "i1": ManualValue(-1000.0), "v1": pick(20.0, "v")}
        before = len(self.backend._dcir_events[did])
        preview = self.backend.preview_manual_dcir(did, values, UnitPrefs())
        self.assertAlmostEqual(preview.resistance, 0.020)                     # |3.68 - 3.70| V / 1 A = 0.020 Ω
        self.assertEqual(len(self.backend._dcir_events[did]), before)         # nothing retained by a preview
        event, measurement = self.backend.create_manual_dcir(did, values, UnitPrefs())
        self.assertEqual(event.source, "manual")
        self.assertAlmostEqual(measurement.resistance, 0.020)
        self.assertEqual(len(self.backend._dcir_events[did]), before + 1)
        core = self.backend._dcir_measurements[did][-1]
        self.assertEqual(core.manual_values[0].sample.source_row, values["i0"].sample_row)
        self.assertEqual(core.manual_values[3].value, core.manual_values[3].sample.voltage_v)   # exact sample value

    def test_i1_time_selects_the_current_sample_and_is_published(self):
        """optional I1 time: 12 s after V0 (t=20) -> the pulse sample at t=30; default: 3rd sample, t=50."""
        _elab, detected = self._dcir_pulse("dcir-i1")
        did, eid = detected.result_id, detected.events[0].event_id
        default = self.backend.measure_dcir(did, [eid], 10.0, UnitPrefs(), threading.Event())[0]
        chosen = self.backend.measure_dcir(did, [eid], 10.0, UnitPrefs(), threading.Event(), current_time_s=12.0)[0]
        self.assertEqual(default.current_time_s, 30.0)
        self.assertEqual(chosen.current_time_s, 10.0)
        self.assertNotEqual(chosen.current_row, default.current_row)
        self.assertEqual(chosen.voltage_row, default.voltage_row)
        published = self.backend.publish_dcir(did, {eid: ACCEPTED}, 10.0, UnitPrefs(), lambda _p: None,
                                              gregorithm.CancellationToken(), current_time_s=12.0)
        self.assertEqual(published.current_time_s, 12.0)
        self.assertEqual(published.measurements[0].current_row, chosen.current_row)
        reread = self.backend.dcir_published(published.result_id, UnitPrefs())
        self.assertEqual((reread.measurement_time_s, reread.current_time_s), (10.0, 12.0))

    def test_explorer_uses_real_rows_unique_counts_units_and_complete_qt_image(self):
        source = FIXTURES / "canonical.csv"
        self.backend.inspect_source(str(source))
        self.backend.import_auto(str(source), "explorer-a", lambda _p: None, gregorithm.CancellationToken())
        result = self.backend.elaborate("explorer-a", ElabParams(), lambda _p: None,
                                        gregorithm.CancellationToken())
        units = UnitPrefs()
        request = ExplorerRequest(result.result_id, "time_s", "voltage_v", "current_a", None,
                                  None, None, None, units, 800, 480)
        view = self.backend.explorer_view(request, threading.Event())
        self.assertEqual(view.total, result.n_rows)
        self.assertEqual(view.rendered, result.n_rows)
        self.assertEqual(len(set(view.y1.source_row.tolist())), view.rendered)
        self.assertEqual(view.y1.source_row.tolist(), view.y2.source_row.tolist())
        clipped = ExplorerRequest(result.result_id, "time_s", "voltage_v", "current_a", None,
                                  None, (99.0, 100.0), (-1.0, 1.0), units, 800, 480)
        clipped_view = self.backend.explorer_view(clipped, threading.Event())
        self.assertEqual(clipped_view.total, view.total)
        details = self.backend.point_details(result.result_id, int(view.y1.source_row[0]), units)
        self.assertAlmostEqual(details["current_a"], view.y2.y[0])
        progress = []
        style = FlatStyle(640, 360, 1.0, "Time [h]", "Voltage [V]", "Current [mA]",
                          "#006D85", "#B05A31", True, "lines",
                          {"bg": "#FFFFFF", "frame": "#BFD1D6", "grid": "#E4EDEF", "text": "#253B44"})
        image = self.backend.render_all_points(request, style, progress.append, threading.Event())
        self.assertEqual((image.drawn, image.total), (result.n_rows, result.n_rows))
        self.assertTrue(image.png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(progress[-1].done, result.n_rows)
        decoded = QImage.fromData(image.png, "PNG")
        self.assertGreaterEqual(decoded.width(), 2000)
        self.assertGreaterEqual(decoded.height(), 1125)
        self.assertGreaterEqual(decoded.dotsPerMeterX() * 0.0254, 300.0)
        marker_style = FlatStyle(640, 360, 1.0, "Time [h]", "Voltage [V]", "Current [mA]",
                                 "#006D85", "#B05A31", False, "markers",
                                 {"bg": "#FFFFFF", "frame": "#BFD1D6", "grid": "#E4EDEF",
                                  "text": "#253B44"})
        marker_image = self.backend.render_all_points(request, marker_style, lambda _p: None,
                                                       threading.Event())
        self.assertEqual((marker_image.drawn, marker_image.total), (result.n_rows, result.n_rows))
        self.assertTrue(marker_image.png.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_non_time_x_filters_before_selection_and_does_not_sort(self):
        source = FIXTURES / "canonical.csv"
        self.backend.inspect_source(str(source))
        self.backend.import_auto(str(source), "explorer-v", lambda _p: None, gregorithm.CancellationToken())
        result = self.backend.elaborate("explorer-v", ElabParams(), lambda _p: None,
                                        gregorithm.CancellationToken())
        raw = pq.read_table(self.backend._elab_views[result.result_id].files["points"]).to_pylist()
        lo, hi = min(r["voltage_v"] for r in raw[1:]), max(r["voltage_v"] for r in raw[1:])
        request = ExplorerRequest(result.result_id, "voltage_v", "current_a", None, None,
                                  (lo, hi), None, None, UnitPrefs(), 800, 480)
        view = self.backend.explorer_view(request, threading.Event())
        expected = [r["source_row"] for r in raw if lo <= r["voltage_v"] <= hi]
        self.assertEqual(view.total, len(expected))
        self.assertEqual(view.y1.source_row.tolist(), expected)


    def test_corrected_generation_supports_capacity_dcir_views_and_removal(self):
        """Regression: views of a cycle-correction generation and Remove Correction must work.

        A corrected generation publishes reports/cycle_correction.json instead of
        reports/capacity.json and reports/dcir.json, and its DCIR events carry corrected cycle
        context. Reading those views or removing the correction previously raised
        FileNotFoundError or "Correction measurements must match their event records".
        """
        source = FIXTURES / "dcir_correction_cycles.csv"
        token = gregorithm.CancellationToken
        self.backend.inspect_source(str(source))
        self.backend.import_auto(str(source), "corr", lambda _p: None, token())
        elab = self.backend.elaborate("corr", ElabParams(), lambda _p: None, token())
        self.assertEqual(elab.n_half_cycles, 6)
        cap_params = CapParams("charge_discharge", 1, None, None)
        detection = DetectionParams(rest_before_s=5.0, pulse_s=5.0, rest_after_s=5.0,
                                    rest_before_tolerance_pct=0.0, pulse_tolerance_pct=0.0,
                                    rest_after_tolerance_pct=0.0)
        dcir = self.backend.detect_dcir("corr", elab.result_id, detection, UnitPrefs(), lambda _p: None, token())
        self.assertEqual(len(dcir.events), 1)
        published = self.backend.publish_dcir(dcir.result_id, {dcir.events[0].event_id: ACCEPTED}, 2.0,
                                              UnitPrefs(), lambda _p: None, token())
        dqdv = self.backend.analyze_dqdv("corr", elab.result_id, DqdvParams((1, 2), edge_trim_mv=0.0),
                                         published.result_id, lambda _p: None, token())
        original_dqdv = pq.read_table(self.backend._dqdv_path(dqdv.result_id) / "tables" / "dqdv.parquet")
        self.assertEqual(dqdv.n_excluded_spans, 1)
        self.assertEqual(dqdv.dcir_fingerprint, published.fingerprint)
        apply = CorrParams(True, 0.0, "mA", CHARGE_FIRST, cap_params)
        corrected = self.backend.recalculate_cycles("corr", elab.result_id, published.result_id, apply,
                                                    lambda _p: None, token(), dqdv.result_id)
        rid = corrected.result_id
        self.assertEqual(corrected.n_spans, 1)
        self.assertEqual(corrected.elab.n_half_cycles, 4)
        table = self.backend.capacity_table(rid, UnitPrefs())
        self.assertEqual(list(table.values["cycle"]), [0, 1, 2])
        self.assertTrue(table.report.get("enabled"))
        view = self.backend.dcir_published(rid, UnitPrefs())
        self.assertEqual(len(view.events), 1)
        self.assertEqual(len(view.fingerprint), 64)
        self.assertIsNotNone(corrected.dqdv)
        self.assertTrue(corrected.dqdv.corrected)
        self.assertEqual(corrected.dqdv.dcir_fingerprint, corrected.dcir.fingerprint)
        self.assertEqual(dict(corrected.report)["dqdv_recomputed"], "false")
        relabelled = pq.read_table(self.backend._dqdv_path(corrected.dqdv.result_id) / "tables" / "dqdv.parquet")
        for column in ("voltage_mid_v", "dqdv_raw_ah_per_v", "dqdv_smoothed_ah_per_v"):
            np.testing.assert_allclose(_numeric_arrow(original_dqdv[column]), _numeric_arrow(relabelled[column]),
                                       equal_nan=True)
        # Reconstruction metrics follow each curve by its anchor row across the relabelling.
        # Curves without any dQ/dV point (listed with "—" before) are not re-listed after a correction.
        before = {x.anchor_source_row: (x.ape_percent, x.rmse) for x in self.backend.dqdv_reconstruction(dqdv.result_id, UnitPrefs())
                  if len(x.rebuilt_q)}
        after = {x.anchor_source_row: (x.ape_percent, x.rmse) for x in
                 self.backend.dqdv_reconstruction(corrected.dqdv.result_id, UnitPrefs())}
        self.assertTrue(before)
        self.assertEqual(before, after)
        removed = self.backend.recalculate_cycles("corr", rid, rid, CorrParams(False, 0.0, "mA", CHARGE_FIRST, cap_params),
                                                  lambda _p: None, token())
        self.assertEqual(removed.elab.n_half_cycles, 6)
        self.assertEqual(removed.n_spans, 0)

    def test_result_cycles_is_exact_cached_and_released(self):
        """Regression: the dQ/dV form asks for the available cycles on every refresh, on the GUI thread. Rescanning
        the points table each time froze the window for tens of seconds with ~2*10^5-row files. The list must equal
        the distinct cycles of the published table, be read once, and be dropped with the result."""
        source = FIXTURES / "dcir_correction_cycles.csv"
        token = gregorithm.CancellationToken
        self.backend.inspect_source(str(source))
        self.backend.import_auto(str(source), "cyc", lambda _p: None, token())
        elab = self.backend.elaborate("cyc", ElabParams(), lambda _p: None, token())
        view = self.backend._result_view(elab.result_id)
        stored = pq.read_table(view.files["points"], columns=["cycle"])["cycle"]
        expected = sorted({int(v) for v in stored.to_pylist() if v is not None})
        parquet = view.parquets["points"]
        calls = []
        original = parquet.iter_batches
        parquet.iter_batches = lambda *a, **k: (calls.append(1), original(*a, **k))[1]
        try:
            first = self.backend.result_cycles(elab.result_id)
            first.append(999)                                  # callers get a copy, never the cached list
            for _ in range(50):
                self.assertEqual(self.backend.result_cycles(elab.result_id), expected)
        finally:
            parquet.iter_batches = original
        self.assertEqual(calls, [])                            # already computed by elaborate(), on the worker
        self.backend.release_result(elab.result_id)
        self.assertNotIn(elab.result_id, self.backend._cycles_cache)
        with self.assertRaises(KeyError):
            self.backend.result_cycles(elab.result_id)

    def test_reimport_of_an_edited_file_uses_its_new_content(self):
        """Regression: the inspection of a path was cached for the whole session, so a file edited on disk and
        imported again reused the old columns and mapping."""
        import os
        source = Path(self.temp.name) / "edited.csv"
        source.write_text("time_s,current_mA,voltage_V\n0,0,3.0\n1,1,3.1\n2,1,3.2\n", encoding="utf-8")
        token = gregorithm.CancellationToken
        first = self.backend.import_auto(str(source), "e1", lambda _p: None, token())
        self.assertEqual(first.n_valid, 3)
        source.write_text("voltage_V,time_s,current_mA,temperature_C\n3.0,0,0,25\n3.1,1,1,25\n3.2,2,1,25\n3.3,3,1,25\n",
                          encoding="utf-8")
        stat = source.stat()
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))   # coarse file-system clocks
        second = self.backend.import_auto(str(source), "e2", lambda _p: None, token())
        self.assertEqual(second.n_valid, 4)
        self.assertEqual([(r.column, r.role) for r in second.report],
                         [("voltage_V", "Voltage"), ("time_s", "Time"), ("current_mA", "Current"),
                          ("temperature_C", "Not used")])

    def test_elaboration_table_pages_cross_row_groups_and_cache_arrow_tables(self):
        """Regression: the Data Elaboration table converted a whole row group to Python rows per page (1-1.7 s on
        the GUI thread with 65,536-row groups). Pages are now sliced from cached Arrow groups; values, Python types,
        unit scaling, pages across group borders and the time search must be unchanged."""
        import pyarrow as pa
        from app.gregorithm_backend import _ElabView, _POINT_SOURCE, _SEGMENT_SOURCE
        root = Path(self.temp.name) / "elab"
        (root / "points").mkdir(parents=True)
        (root / "indexes").mkdir()
        n = 23
        points = pa.table({
            "time_s": [float(i * 10) for i in range(n)], "current_a": [0.001 * (i % 3 - 1) for i in range(n)],
            "voltage_v": [3.0 + i / 100 for i in range(n)], "charge_capacity_ah": [None if i % 5 == 0 else i * 1e-4 for i in range(n)],
            "discharge_capacity_ah": [0.0] * n, "charge_energy_wh": [i * 1e-3 for i in range(n)],
            "discharge_energy_wh": [0.0] * n, "cycle": [1 + i // 10 for i in range(n)],
            "half_cycle": [1 + i // 5 for i in range(n)], "rest": [i % 4 == 0 for i in range(n)],
            "step": [i % 3 for i in range(n)], "source_row": list(range(n))})
        segments = pa.table({name: list(range(4)) for name in _SEGMENT_SOURCE})
        pq.write_table(points, root / "points" / "elaborated.parquet", row_group_size=7)
        pq.write_table(segments, root / "indexes" / "segments.parquet", row_group_size=3)
        view = _ElabView(root)
        try:
            units = UnitPrefs()
            full = points.to_pylist()
            expected = [[None if r[c] is None else r[c] * _factor(c, units) for c in _POINT_SOURCE] for r in full]
            for start in range(n):
                for count in (1, 5, 9, 30):
                    got = view.rows("points", start, count, units)
                    self.assertEqual(got, expected[start:start + count])
                    self.assertEqual([[type(x) for x in r] for r in got],
                                     [[type(x) for x in r] for r in expected[start:start + count]])
            self.assertTrue(all(isinstance(t, pa.Table) for t in view._cache.values()))
            self.assertLessEqual(len(view._cache), 4)
            self.assertEqual(view.rows("segments", 2, 5, units)[0][0], 2)
            factor = _factor("time_s", units)
            self.assertEqual(view.find_time(70.0 * factor), (7, True, 70.0 * factor))       # first row of group 2
            row, exact, value = view.find_time(74.0 * factor)
            self.assertEqual((row, exact), (7, False))
            self.assertEqual(view.find_time(1e9 * factor)[0], n - 1)
        finally:
            view.close()

    @staticmethod
    def _row_by_row_curves(table, voltage_factor, capacity_factor):
        """Reference: the former row-by-row grouping of the dQ/dV table (dict per row), kept as the test oracle."""
        import math
        from collections import OrderedDict
        grouped = OrderedDict()
        for row in table.to_pylist():
            key = (int(row["cycle"]), int(row["half_cycle"]), str(row["phase"]), int(row["anchor_source_row"]))
            grouped.setdefault(key, OrderedDict()).setdefault(int(row["segment_index"]), []).append(row)
        out = []
        for key, segments in grouped.items():
            converted = []
            for index, values in segments.items():
                smoothed = [x["dqdv_smoothed_ah_per_v"] for x in values]
                converted.append((index,
                                  np.asarray([float(x["voltage_mid_v"]) * voltage_factor for x in values], dtype=float),
                                  np.asarray([float(x["dqdv_raw_ah_per_v"]) * capacity_factor for x in values], dtype=float),
                                  None if all(x is None for x in smoothed) else np.asarray(
                                      [math.nan if x is None else float(x) * capacity_factor for x in smoothed],
                                      dtype=float)))
            out.append((key, converted))
        return out

    def _assert_curves_identical(self, curves, reference):
        self.assertEqual([(c.cycle, c.half_cycle, c.phase, c.anchor_source_row) for c in curves],
                         [key for key, _segments in reference])
        for curve, (_key, segments) in zip(curves, reference):
            self.assertEqual([s.segment_index for s in curve.segments], [s[0] for s in segments])
            for seg, (_index, v, raw, smoothed) in zip(curve.segments, segments):
                self.assertEqual(seg.v.tobytes(), v.tobytes())                      # bit-identical values
                self.assertEqual(seg.raw.tobytes(), raw.tobytes())
                self.assertEqual(seg.smoothed is None, smoothed is None)
                if smoothed is not None:
                    self.assertEqual(seg.smoothed.tobytes(), smoothed.tobytes())

    def test_dqdv_curves_match_row_by_row_reference(self):
        """Regression: the vectorised reading of the dQ/dV table must give bit-identical curves to the former
        row-by-row reading, for real results and for non-contiguous rows and partly/entirely missing smoothing."""
        import pyarrow as pa
        from app.gregorithm_backend import _dqdv_curves
        source = FIXTURES / "dcir_correction_cycles.csv"
        token = gregorithm.CancellationToken
        self.backend.inspect_source(str(source))
        self.backend.import_auto(str(source), "dq-v", lambda _p: None, token())
        elab = self.backend.elaborate("dq-v", ElabParams(), lambda _p: None, token())
        for extra in ({}, {"smoothing": True}, {"method": "histogram", "bin_mv": 5.0}):
            result = self.backend.analyze_dqdv("dq-v", elab.result_id, DqdvParams((1, 2), **extra), None,
                                               lambda _p: None, token())
            table = pq.read_table(self.backend._dqdv_path(result.result_id) / "tables" / "dqdv.parquet")
            self.assertGreater(table.num_rows, 0)
            for factors in ((1.0, 1000.0), (1000.0, 1.0)):
                self._assert_curves_identical(_dqdv_curves(table, *factors), self._row_by_row_curves(table, *factors))
        table = pa.table({
            "cycle": [1, 1, 2, 1, 1, 2], "half_cycle": [1, 1, 3, 1, 1, 3],
            "phase": ["charge", "charge", "discharge", "charge", "charge", "discharge"],
            "anchor_source_row": [10, 10, 30, 10, 10, 30], "segment_index": [0, 1, 0, 0, 1, 0],
            "voltage_mid_v": [3.1, 3.2, 3.5, 3.15, 3.25, 3.4], "dqdv_raw_ah_per_v": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            "dqdv_smoothed_ah_per_v": pa.array([None, 0.5, None, 0.7, None, None], pa.float64())})
        self._assert_curves_identical(_dqdv_curves(table, 1.0, 1000.0), self._row_by_row_curves(table, 1.0, 1000.0))
        self.assertEqual(_dqdv_curves(table.slice(0, 0), 1.0, 1.0), [])

    def test_dqdv_methods_and_voltage_reconstruction_match_core(self):
        source = FIXTURES / "dcir_correction_cycles.csv"
        token = gregorithm.CancellationToken
        self.backend.inspect_source(str(source))
        self.backend.import_auto(str(source), "dq-h", lambda _p: None, token())
        elab = self.backend.elaborate("dq-h", ElabParams(), lambda _p: None, token())
        for method in ("histogram", "histogram_weighted", "interpolated_gradient"):
            params = DqdvParams((1, 2), method=method, bin_mv=5.0, points_mode="count", points_value=50.0)
            adapted = self.backend.analyze_dqdv("dq-h", elab.result_id, params, None, lambda _p: None, token())
            direct = gregorithm.analyze_dqdv(gregorithm.DqdvRequest(
                self.backend._result_view(elab.result_id).result_path,
                self.backend._elab_result_fingerprint(elab.result_id)["fingerprint"],
                Path(self.temp.name) / f"direct-{method}", "sample", "job", "generation", cycles=(1, 2),
                parameters=params.core_parameters()))
            self.assertEqual(pq.read_table(self.backend._dqdv_path(adapted.result_id) / "tables" / "dqdv.parquet").to_pylist(),
                             pq.read_table(direct.ref.path / "tables" / "dqdv.parquet").to_pylist())
            report = json.loads((direct.ref.path / "reports" / "dqdv.json").read_text(encoding="utf-8"))
            expected = {r["anchor_source_row"]: r["ape_percent"] for r in report["reconstruction"]}
            units = UnitPrefs(voltage="mV", capacity="mAh")
            rec = self.backend.dqdv_reconstruction(adapted.result_id, units)
            self.assertEqual({x.anchor_source_row: x.ape_percent for x in rec}, expected)
            table = pq.read_table(direct.ref.path / "tables" / "dqdv_reconstruction.parquet").to_pylist()
            for x in rec:
                rows = [r for r in table if r["anchor_source_row"] == x.anchor_source_row]
                measured = [r["voltage_v"] * 1000 for r in rows if r["curve"] == "measured"]
                rebuilt = [r["capacity_ah"] * 1000 for r in rows if r["curve"] == "reconstructed"]
                np.testing.assert_allclose(x.measured_v, measured)            # display units, values unchanged
                np.testing.assert_allclose(x.rebuilt_q, rebuilt)
                rmse = next(r["rmse_v"] for r in report["reconstruction"] if r["anchor_source_row"] == x.anchor_source_row)
                self.assertEqual(x.rmse, None if rmse is None else rmse * 1000)
        off = self.backend.analyze_dqdv("dq-h", elab.result_id, DqdvParams((1,), reconstruction=False), None,
                                        lambda _p: None, token())
        self.assertEqual(self.backend.dqdv_reconstruction(off.result_id, UnitPrefs()), [])

    def test_dqdv_matches_core_exports_and_comparison_counts_unique_rows(self):
        source = FIXTURES / "dcir_correction_cycles.csv"
        token = gregorithm.CancellationToken
        self.backend.inspect_source(str(source))
        self.backend.import_auto(str(source), "dq-a", lambda _p: None, token())
        self.backend.import_auto(str(source), "dq-b", lambda _p: None, token())
        first = self.backend.elaborate("dq-a", ElabParams(), lambda _p: None, token())
        second = self.backend.elaborate("dq-b", ElabParams(), lambda _p: None, token())
        params = DqdvParams((1, 2), True, 3, 1, edge_trim_mv=0.0)
        adapted = self.backend.analyze_dqdv("dq-a", first.result_id, params, None, lambda _p: None, token())
        direct = gregorithm.analyze_dqdv(gregorithm.DqdvRequest(
            self.backend._result_view(first.result_id).result_path,
            self.backend._elab_result_fingerprint(first.result_id)["fingerprint"],
            Path(self.temp.name) / "direct-dqdv", "sample", "job", "generation", cycles=(1, 2),
            parameters=gregorithm.DqdvParameters(smoothing_enabled=True, sg_window_points=3, sg_poly_order=1,
                                                 edge_trim_mv=0.0)))
        actual = pq.read_table(self.backend._dqdv_path(adapted.result_id) / "tables" / "dqdv.parquet")
        expected = pq.read_table(direct.ref.path / "tables" / "dqdv.parquet")
        self.assertEqual(actual.schema.names, ["cycle", "half_cycle", "phase", "anchor_source_row", "segment_index",
                                                "voltage_mid_v", "dqdv_raw_ah_per_v", "dqdv_smoothed_ah_per_v"])
        self.assertEqual(actual.to_pylist(), expected.to_pylist())
        curves = self.backend.dqdv_curves(adapted.result_id, UnitPrefs())
        self.assertEqual(sum(len(segment.v) for curve in curves for segment in curve.segments), adapted.n_points)
        self.assertGreater(adapted.n_points, 0)

        with tempfile.TemporaryDirectory() as export_dir:
            csv_path = Path(export_dir) / "dqdv.csv"
            receipts = self.backend.export_dqdv(adapted.result_id, (2,), ("charge",),
                ["cycle", "phase", "voltage_mid", "dqdv_raw", "dqdv_smoothed"], UnitPrefs(), "csv",
                str(csv_path), lambda _p: None, threading.Event())
            exported = __import__("pyarrow.csv").csv.read_csv(csv_path)
            expected_rows = sum(1 for row in actual.to_pylist() if row["cycle"] == 2 and row["phase"] == "charge")
            self.assertEqual((receipts[0].rows, exported.num_rows), (expected_rows, expected_rows))
            with self.assertRaises(FileExistsError):
                self.backend.export_dqdv(adapted.result_id, (2,), ("charge",), ["cycle"], UnitPrefs(), "csv",
                                         str(csv_path), lambda _p: None, threading.Event())
            parquet_path = Path(export_dir) / "dqdv.parquet"
            self.backend.export_dqdv(adapted.result_id, (1, 2), ("charge", "discharge"),
                ["cycle", "phase", "voltage_mid", "dqdv_raw", "dqdv_smoothed"], UnitPrefs(), "parquet",
                str(parquet_path), lambda _p: None, threading.Event())
            exported_parquet = pq.read_table(parquet_path)
            self.assertEqual(exported_parquet.schema.names,
                             ["cycle", "phase", "voltage_mid", "dqdv_raw", "dqdv_smoothed"])
            cancelled_path = Path(export_dir) / "cancelled.csv"
            cancelled = threading.Event()
            cancelled.set()
            from app.backend import ExportCancelled
            with self.assertRaises(ExportCancelled):
                self.backend.export_dqdv(adapted.result_id, (1,), ("charge",), ["cycle"], UnitPrefs(), "csv",
                                         str(cancelled_path), lambda _p: None, cancelled)
            self.assertFalse(cancelled_path.exists())

        compare = self.backend.compare_explorer(CompareRequest(
            (("dq-a", first.result_id), ("dq-b", second.result_id)), "voltage_v", "current_a", (1, 2),
            None, None, UnitPrefs(), 800), threading.Event())
        self.assertEqual(len(compare), 2)
        for item in compare:
            self.assertEqual(item.rendered, len(set(item.curve.source_row.tolist())))
            self.assertLessEqual(item.rendered, item.total)
        progress = []
        image = self.backend.compare_render_all(CompareRequest(
            (("dq-a", first.result_id), ("dq-b", second.result_id)), "voltage_v", "current_a", (1, 2),
            None, None, UnitPrefs(), 800),
            {"dq-a": ("#006D85", "Dataset A"), "dq-b": ("#B05A31", "Dataset B")},
            FlatStyle(640, 360, 1.0, "Voltage [V]", "Current [mA]", None, "#006D85", "#B05A31",
                      True, "lines", {"bg": "#FFFFFF", "frame": "#BFD1D6", "grid": "#E4EDEF",
                                      "text": "#253B44"}),
            progress.append, threading.Event())
        self.assertEqual(image.drawn, sum(item.total for item in compare))
        self.assertEqual(image.drawn, image.total)
        self.assertEqual(progress[-1].done, image.total)
        self.assertTrue(image.png.startswith(b"\x89PNG\r\n\x1a\n"))


    def test_per_pulse_times_are_measured_published_and_verified_by_the_core(self):
        """DCIR Review: a pulse can have its own V1 / I1 times. Publication measures it at those times (gregorithm
        re-verifies every measurement at the times it records) and reports them as the pulse's own times."""
        _elab, detected = self._dcir_pulse("dcir-own")
        did, eid = detected.result_id, detected.events[0].event_id
        own = self.backend.measure_dcir(did, [eid], 30.0, UnitPrefs(), threading.Event(), current_time_s=12.0)[0]
        published = self.backend.publish_dcir(did, {eid: ACCEPTED}, 10.0, UnitPrefs(), lambda _p: None,
                                              gregorithm.CancellationToken(), event_times={eid: (30.0, 12.0)})
        m = published.measurements[0]
        self.assertEqual((m.requested_time_s, m.voltage_row, m.current_row),
                         (30.0, own.voltage_row, own.current_row))
        self.assertEqual(published.event_times, ((eid, 30.0, 12.0),))
        self.assertEqual((published.measurement_time_s, published.current_time_s), (10.0, None))
        plain = self.backend.publish_dcir(did, {eid: ACCEPTED}, 10.0, UnitPrefs(), lambda _p: None,
                                          gregorithm.CancellationToken())
        self.assertEqual(plain.event_times, ())
        self.assertEqual(plain.measurements[0].requested_time_s, 10.0)

    def test_dcir_export_writes_the_results_table(self):
        """Regression: export_dcir was declared by the backend protocol but missing in the gregorithm adapter, so
        DCIR › Results › Export always failed. It now writes the Results table (display units, never overwrites)."""
        import pyarrow.csv as pacsv
        _elab, detected = self._dcir_pulse("dcir-export")
        eid = detected.events[0].event_id
        published = self.backend.publish_dcir(detected.result_id, {eid: ACCEPTED}, 10.0, UnitPrefs(),
                                              lambda _p: None, gregorithm.CancellationToken())
        target = Path(self.temp.name) / "out" / "dcir.csv"
        receipts = self.backend.export_dcir(published.result_id, ["resistance", "v_meas"], UnitPrefs(resistance="mΩ"),
                                            "csv", str(target), lambda _p: None, threading.Event())
        self.assertEqual((receipts[0].path, receipts[0].rows), (str(target), 1))
        table = pacsv.read_csv(target)
        self.assertEqual(table.column_names, ["Pulse", "V1 [V]", "Resistance [mΩ]"])
        self.assertAlmostEqual(table["Resistance [mΩ]"][0].as_py(), published.measurements[0].resistance * 1000)
        with self.assertRaises(FileExistsError):
            self.backend.export_dcir(published.result_id, ["resistance"], UnitPrefs(), "csv", str(target),
                                     lambda _p: None, threading.Event())

    def test_elaboration_export_streams_every_column_in_display_units(self):
        """Data Elaboration › Export Data: the points (or segments) table with every column of the Elaborated
        Data view, values equal to the view's rows; a cancelled export leaves no file; never overwrites."""
        import pyarrow.csv as pacsv
        source = FIXTURES / "dcir_pulse.csv"
        self.backend.inspect_source(str(source))
        self.backend.import_auto(str(source), "elab-export", lambda _p: None, gregorithm.CancellationToken())
        elab = self.backend.elaborate("elab-export", ElabParams(CHARGE_FIRST), lambda _p: None,
                                      gregorithm.CancellationToken())
        out = Path(self.temp.name) / "out"
        target = out / "points.csv"
        progress = []
        done = self.backend.export_elab(elab.result_id, "points", None, "csv", str(target), progress.append,
                                        threading.Event())[0]
        info = self.backend.elab_table(elab.result_id, "points")
        rows = self.backend.get_elab_rows(elab.result_id, "points", 0, info.total)
        table = pacsv.read_csv(target)
        self.assertEqual(done.rows, info.total)
        self.assertEqual(table.num_rows, info.total)
        self.assertEqual(table.column_names[:3], ["Time [h]", "Current [mA]", "Voltage [V]"])
        np.testing.assert_allclose(np.asarray(table.column(1).to_pylist(), float), [r[1] for r in rows])
        self.assertEqual(progress[-1].done, info.total)
        segments = self.backend.export_elab(elab.result_id, "segments", UnitPrefs(), "parquet",
                                            str(out / "segments.parquet"), lambda _p: None, threading.Event())[0]
        self.assertEqual(pq.read_table(segments.path).num_rows, self.backend.elab_table(elab.result_id, "segments").total)
        with self.assertRaises(FileExistsError):
            self.backend.export_elab(elab.result_id, "points", None, "csv", str(target), lambda _p: None,
                                     threading.Event())
        cancelled = threading.Event()
        cancelled.set()
        from app.backend import ExportCancelled
        with self.assertRaises(ExportCancelled):
            self.backend.export_elab(elab.result_id, "points", None, "csv", str(out / "stopped.csv"),
                                     lambda _p: None, cancelled)
        self.assertEqual(sorted(x.name for x in out.iterdir()), ["points.csv", "segments.parquet"])

def _numeric_arrow(column):
    return np.asarray(column.to_pylist(), dtype=float)


if __name__ == "__main__":
    unittest.main()
