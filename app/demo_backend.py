"""SYNTHETIC demo backend for screenshots and visual review only (python run.py --demo).
Not scientific data. Never ship as the real backend."""
from __future__ import annotations
import math, time
from .backend import (AmbiguousMapping, ColumnMapping, Curve, ElabCancelled, ElabColumn, ElabTable, ExplorerRequest,
                      ExplorerView, FlatImage, FlatStyle, ImportCancelled, ImportResult, Progress, RenderCancelled,
                      ReportRow, SourceInspection)
from .elaboration import ElabParams, ElabResult
from .explorer import COLUMN_BY_KEY
from .units import FACTORY_UNITS, UnitPrefs, scale
from .demo_dqdv import DqdvDemoMixin
from .demo_compare import CompareDemoMixin

N_ROWS = 31_241
DT_H = 0.01
COLS = ["Test_Time", "Step_Time", "Current_mA", "Voltage_V", "Temperature_C", "Cycle_Index", "Step_Index"]


def _cancelled(token) -> bool:
    return token.is_set() if hasattr(token, "is_set") else token.is_cancelled()


def _preview() -> list[list[str]]:
    rows = []
    for i in range(8):
        t = f"00:00:{i:02d}.000"
        rows.append([t, t, "0.000" if i == 0 else "1250.0", f"{3.4521 + 0.0011 * i:.4f}",
                     f"{25.1 + 0.05 * (i // 3):.1f}", "1", "1" if i == 0 else "2"])
    return rows


class DemoBackend(DqdvDemoMixin, CompareDemoMixin):
    def __init__(self, step_delay: float = 0.03):
        self.step_delay = step_delay
        self._units: dict[str, UnitPrefs] = {}
        self._result_ds: dict[str, str] = {}
        self._points = None

    # --- units (SYNTHETIC adapter behaviour: canonical s, A, V, Ah, Wh scaled to the display units) ---------------
    def set_display_units(self, ds_key, units):
        self._units[ds_key] = units

    def _u(self, key) -> UnitPrefs:
        return self._units.get(self._result_ds.get(key, key), FACTORY_UNITS)

    def data_columns(self, ds_key):
        u = self._u(ds_key)
        return [("Time", u.time), ("Current", u.current), ("Voltage", u.voltage)]

    def set_sample_values(self, ds_key, values):
        pass

    def inspect_source(self, path, has_header=None):
        m = ColumnMapping(0, 2, 3, "hh:mm:ss.fff", "mA", "V")
        return SourceInspection(COLS, _preview(), ambiguity_reason=(
            "Auto Mapping could not decide: two columns look like time (Test_Time, Step_Time). Choose the columns to use."),
            suggested=m)

    def _run(self, method, progress, cancel):
        for i in range(1, 11):
            if _cancelled(cancel):
                raise ImportCancelled()
            time.sleep(self.step_delay)
            progress(Progress(i / 10, "Reading rows", int(N_ROWS * i / 10), N_ROWS))
        rep = [ReportRow("Test_Time", "Time", "hh:mm:ss.fff → s", True), ReportRow("Current_mA", "Current", "mA", True),
               ReportRow("Voltage_V", "Voltage", "V", True), ReportRow("Step_Time", "Not used", "—", False),
               ReportRow("Temperature_C", "Not used", "—", False)]
        return ImportResult(N_ROWS, 412, N_ROWS * DT_H, method, rep,
                            "SYNTHETIC DEMO: reasons would be listed here as reported by the import.",
                            [("Time", "h"), ("Current", "mA"), ("Voltage", "V")])

    def import_auto(self, path, ds_key, progress, cancel):
        if "ambiguous" in path.lower():
            raise AmbiguousMapping(self.inspect_source(path))
        return self._run("Auto Mapping", progress, cancel)

    def import_mapped(self, path, mapping, progress, cancel, ds_key):
        return self._run("Manual Mapping", progress, cancel)

    def get_rows(self, ds_key, start, count):
        u = self._u(ds_key)
        kt, ki, kv = scale("time", u.time) * 3600, scale("current", u.current) / 1000, scale("voltage", u.voltage)
        out = []
        for n in range(start, min(start + count, N_ROWS)):
            out.append([n * DT_H * kt, 0.0 if n == 0 else 1250.0 * ki,
                        (3.4521 + 0.0009 * (n % 400) + 0.05 * math.sin(n / 900)) * kv])
        return out

    def find_row_by_time(self, ds_key, value):
        k = scale("time", self._u(ds_key).time) * 3600          # display units per hour
        idx = max(0, min(N_ROWS - 1, round(value / k / DT_H)))
        return idx, abs(idx * DT_H * k - value) < 1e-9, idx * DT_H * k

    def release(self, ds_key):
        pass

    # --- Data Elaboration (SYNTHETIC) -----------------------------------------------------------------
    def elaborate(self, ds_key, params: ElabParams, progress, cancel):
        """Phases and a publishing stage are simulated. A dataset whose id/path contains 'fail' fails."""
        for i in range(1, 21):
            if _cancelled(cancel):
                raise ElabCancelled()
            time.sleep(self.step_delay)
            progress(Progress(i / 20, "Classifying steps and calculating cumulative values\u2026", int(N_ROWS * i / 20), N_ROWS))
        if "fail" in str(ds_key).lower():
            raise RuntimeError("SYNTHETIC DEMO: the library reason would appear here exactly as returned.")
        progress(Progress(1.0, "Saving result\u2026", N_ROWS, N_ROWS, publishing=True))
        time.sleep(self.step_delay * 6)
        rid = f"demo-{abs(hash((ds_key, params))) % 10**8:08x}"
        self._result_ds[rid] = ds_key
        return ElabResult(rid, 128, 256, 1024, N_ROWS, params, 1.7, (("Rows processed", "31,241"),))

    def elab_table(self, result_id, kind):
        if kind == "segments":
            cols = [ElabColumn("Segment ID", "", "int"), ElabColumn("First Time", "h", "float", True), ElabColumn("Last Time", "h", "float", True),
                    ElabColumn("First Row", "", "int"), ElabColumn("Last Row", "", "int"), ElabColumn("Cycle", "", "int"),
                    ElabColumn("Half-Cycle", "", "int"), ElabColumn("Step", "", "text"),
                    ElabColumn("S4 Portion", "", "int"), ElabColumn("Polarity", "", "int")]
            return ElabTable(cols, 1024, row_number_column=False)
        u = self._u(result_id)
        cols = [ElabColumn("Time", u.time, "float", True), ElabColumn("Current", u.current), ElabColumn("Voltage", u.voltage),
                ElabColumn("Charge Capacity", u.capacity), ElabColumn("Discharge Capacity", u.capacity),
                ElabColumn("Charge Energy", u.energy), ElabColumn("Discharge Energy", u.energy), ElabColumn("Cycle", "", "int"),
                ElabColumn("Half-Cycle", "", "int"), ElabColumn("Rest", "", "bool"), ElabColumn("Step", "", "text"),
                ElabColumn("Source Row", "", "int")]
        return ElabTable(cols, N_ROWS, row_number_column=True)

    def get_elab_rows(self, result_id, kind, start, count):
        out = []
        if kind == "segments":
            for i in range(start, min(start + count, 1024)):
                fr = i * 30; lr = fr + 29; st = ["CC", "CV", "Rest"][i % 3]
                out.append([i, fr * DT_H, lr * DT_H, fr, lr, i // 8 + 1, i // 4 + 1, st, 0, 0 if st == "Rest" else (1 if i % 2 else -1)])
            return out
        u = self._u(result_id)
        kt, ki, kv = scale("time", u.time) * 3600, scale("current", u.current) / 1000, scale("voltage", u.voltage)
        kc, ke = scale("capacity", u.capacity), scale("energy", u.energy)
        for n in range(start, min(start + count, N_ROWS)):
            step = "Rest" if n % 300 > 280 else ("CC" if n % 300 < 200 else "CV")
            out.append([n * DT_H * kt, (0.0 if step == "Rest" else 1250.0) * ki, (3.4521 + 0.0009 * (n % 400)) * kv,
                        (0.4125 + 0.0001 * n) * kc, 0.0, None if n % 97 == 3 else (1.436 + 0.0001 * n) * ke, 0.0,
                        n // 300 + 1, n // 150 + 1, step == "Rest", step, n])
        return out

    def find_elab_row_by_time(self, result_id, value):
        return self.find_row_by_time(result_id, value)

    def release_result(self, result_id):
        pass


    # --- Data Explorer (SYNTHETIC: the shapes imitate a cycling test; nothing here is scientific) -------------------
    def _arrays(self):
        """Synthetic 'published points' in canonical units (s, A, V, Ah, Wh), built once with numpy."""
        if self._points is not None:
            return self._points
        import numpy as np
        n = np.arange(N_ROWS)
        t_h = n * DT_H
        per = 2.44
        c = np.floor(t_h / per).astype(np.int32) + 1
        u = t_h - (c - 1) * per
        cc_c, cv, r1, dis = u < 0.9, (u >= 0.9) & (u < 1.15), (u >= 1.15) & (u < 1.25), (u >= 1.25) & (u < 2.25)
        r2 = u >= 2.25
        v = np.where(cc_c, 3.4 + 0.8 * np.power(np.clip(u / 0.9, 0, 1), 0.7), 0.0)
        v = np.where(cv, 4.2, v)
        v = np.where(r1, 4.2 - 0.06 * (1 - np.exp(-(u - 1.15) / 0.03)), v)
        q = np.clip(u - 1.25, 0, 1)
        v = np.where(dis, 4.08 - 0.55 * q - 0.45 * q ** 6, v)
        v = np.where(r2, 3.08 + 0.25 * (1 - np.exp(-(u - 2.25) / 0.04)), v)
        i = np.where(cc_c, 1.25, 0.0)
        i = np.where(cv, 1.25 * np.exp(-(u - 0.9) / 0.06), i)
        i = np.where(dis, -1.25, i)
        half = (c - 1) * 2 + np.where(u < 1.25, 1, 2)
        step = np.where(r1 | r2, 0, np.where(cv, 2, 1)).astype(np.int8)
        dt = DT_H
        chg = np.zeros(N_ROWS); dchg = np.zeros(N_ROWS)
        start = np.r_[True, half[1:] != half[:-1]]
        idx = np.cumsum(start) - 1
        inc_c = np.where(i > 0, i * dt, 0.0); inc_d = np.where(i < 0, -i * dt, 0.0)
        cs_c = np.cumsum(inc_c); cs_d = np.cumsum(inc_d)
        first = np.flatnonzero(start)
        chg = cs_c - (cs_c[first] - inc_c[first])[idx]
        dchg = cs_d - (cs_d[first] - inc_d[first])[idx]
        e_c = chg * 3.85; e_d = dchg * 3.6
        e_c = np.where(n % 97 == 3, np.nan, e_c)            # some nulls, drawn as gaps
        self._points = {"time_s": t_h * 3600.0, "current_a": i, "voltage_v": v, "charge_capacity_ah": chg,
                        "discharge_capacity_ah": dchg, "charge_energy_wh": e_c, "discharge_energy_wh": e_d,
                        "cycle": c.astype(float), "half_cycle": half.astype(float), "rest": (step == 0).astype(float),
                        "step": step.astype(float), "source_row": n.astype(float)}
        return self._points

    def _col(self, key, units):
        a = self._arrays()[key]
        qty = COLUMN_BY_KEY[key].quantity
        return a * scale(qty, units.unit(qty)) if qty else a

    def result_cycles(self, result_id):
        return list(range(1, 129))

    def _select(self, req: ExplorerRequest):
        import numpy as np
        pts = self._arrays()
        keep = np.ones(N_ROWS, bool)
        if req.cycles is not None:
            keep = np.isin(pts["cycle"].astype(int), np.array(req.cycles, int))
        x = self._col(req.x, req.units)
        xs = x[keep]
        if xs.size == 0:
            return None
        xr = req.x_range or (float(np.nanmin(xs)), float(np.nanmax(xs)) if np.nanmax(xs) > np.nanmin(xs) else float(np.nanmin(xs)) + 1)
        inx = keep & (x >= xr[0]) & (x <= xr[1])
        return keep, x, xr, inx

    def _curve(self, key, rng, x, inx, units, width_px):
        import numpy as np
        y = self._col(key, units)
        yv = y[inx]
        ok = ~np.isnan(yv)
        if rng is None:
            if ok.any():
                lo, hi = float(np.nanmin(yv)), float(np.nanmax(yv))
                pad = (hi - lo) * 0.04 or 1.0
                rng = (lo - pad, hi + pad)
            else:
                rng = (0.0, 1.0)
        rows = np.flatnonzero(inx)
        in_y = ok & (yv >= rng[0]) & (yv <= rng[1])
        total = int(in_y.sum())
        budget = 4 * max(200, width_px)
        if rows.size <= budget:
            sel = rows
        else:                                              # real points only: first/last + min/max per pixel column
            xx = x[rows]
            span = (xx.max() - xx.min()) or 1.0
            col = np.clip(((xx - xx.min()) / span * (width_px - 1)).astype(int), 0, width_px - 1)
            order = np.lexsort((np.nan_to_num(yv, nan=np.inf), col))
            cs = col[order]
            firsts = order[np.r_[True, cs[1:] != cs[:-1]]]
            yv_nan_low = np.nan_to_num(yv, nan=-np.inf)
            order2 = np.lexsort((yv_nan_low, col))
            cs2 = col[order2]
            lasts = order2[np.r_[cs2[1:] != cs2[:-1], True]]
            pick = np.unique(np.r_[firsts, lasts, 0, rows.size - 1])
            sel = rows[pick]
        sel_y = y[sel]
        rendered = int(((~np.isnan(sel_y)) & (sel_y >= rng[0]) & (sel_y <= rng[1])).sum())
        # break the line where the filter skipped rows (never connect two separate cycles through omitted data)
        gaps = np.flatnonzero(np.diff(sel) > 1) if sel.size else np.array([], int)
        keep_all = inx  # rows inside filter and X range
        brk = [g for g in gaps if not keep_all[sel[g] + 1:sel[g + 1]].any()] if gaps.size else []
        xo, yo, ro = x[sel].astype(float), sel_y.astype(float), sel.astype(np.int64)
        if brk:
            pos = np.array(brk) + 1
            xo = np.insert(xo, pos, np.nan); yo = np.insert(yo, pos, np.nan); ro = np.insert(ro, pos, -1)
        return Curve(xo, yo, ro), total, rendered, rng

    def explorer_view(self, req, cancel):
        time.sleep(self.step_delay * 4)
        if _cancelled(cancel):
            raise RenderCancelled()
        s = self._select(req)
        if s is None:
            empty = Curve([], [], [])
            return ExplorerView(empty, None, 0, 0, (0.0, 1.0), (0.0, 1.0), None, filter_matched=False)
        keep, x, xr, inx = s
        c1, t1, r1, y1r = self._curve(req.y1, req.y1_range, x, inx, req.units, req.plot_width_px)
        c2 = y2r = None; t2 = r2 = 0
        if req.y2:
            c2, t2, r2, y2r = self._curve(req.y2, req.y2_range, x, inx, req.units, req.plot_width_px)
        import numpy as np
        nulls = 0
        for k in (req.y1, req.y2):
            if k and COLUMN_BY_KEY[k].nullable:
                nulls += int(np.isnan(self._col(k, req.units)[inx]).sum())
        eligible = np.flatnonzero(inx & (np.isfinite(self._col(req.y1, req.units)) |
                                        (np.isfinite(self._col(req.y2, req.units)) if req.y2 else False)))
        selected = set(int(v) for v in np.asarray(c1.source_row) if int(v) >= 0)
        if c2 is not None:
            selected.update(int(v) for v in np.asarray(c2.source_row) if int(v) >= 0)
        return ExplorerView(c1, c2, len(eligible), len(selected), xr, y1r, y2r, True, nulls)

    def point_details(self, result_id, source_row, units):
        return {k: float(self._col(k, units)[int(source_row)]) for k in COLUMN_BY_KEY}

    def render_all_points(self, req, style: FlatStyle, progress, cancel):
        """SYNTHETIC renderer used only for screenshots; production uses GregorithmBackend. Same contract: a
        transparent data layer of the plot area (backend.FlatStyle)."""
        import numpy as np
        from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QPointF
        from PySide6.QtGui import QColor, QPen, QPolygonF
        from .flat_render import data_layer, dot_pen
        s = self._select(req)
        if s is None:
            raise RuntimeError("SYNTHETIC DEMO: nothing to draw")
        _keep, x, xr, inx = s
        if _cancelled(cancel):
            raise RenderCancelled()
        image, painter, plot, width, height = data_layer(style)
        axes = [(req.y1, req.y1_range, style.y1_color)]
        if req.y2:
            axes.append((req.y2, req.y2_range, style.y2_color))
        rows = np.flatnonzero(inx)
        x_values = x[rows]
        x_span = (xr[1] - xr[0]) or 1.0
        for axis_index, (key, rng, color) in reversed(list(enumerate(axes))):      # Y1 on top, as on screen
            if _cancelled(cancel):
                painter.end()
                raise RenderCancelled()
            y_values = self._col(key, req.units)[rows]
            finite = np.isfinite(x_values) & np.isfinite(y_values)
            if rng is None:
                low, high = float(np.nanmin(y_values[finite])), float(np.nanmax(y_values[finite]))
                pad = (high - low) * 0.04 or 1.0
                rng = (low - pad, high + pad)
            y_span = (rng[1] - rng[0]) or 1.0
            polygon = QPolygonF([
                QPointF((float(xv) - xr[0]) / x_span * plot.width(), (1.0 - (float(yv) - rng[0]) / y_span) * plot.height())
                for xv, yv in zip(x_values[finite], y_values[finite])
            ])
            if style.style in ("lines", "lines_markers"):
                painter.setPen(QPen(QColor(color), style.line_width))
                painter.drawPolyline(polygon)
            if style.style in ("markers", "lines_markers"):
                painter.setPen(dot_pen(color))
                painter.drawPoints(polygon)
            progress(Progress((axis_index + 1) / len(axes), "Rendering all points", None, None))
        eligible = inx & np.isfinite(self._col(req.y1, req.units))
        if req.y2:
            eligible |= inx & np.isfinite(self._col(req.y2, req.units))
        drawn = total = int(eligible.sum())
        painter.end()
        dots_per_meter = int(math.ceil(style.dpi / 0.0254))
        image.setDotsPerMeterX(dots_per_meter)
        image.setDotsPerMeterY(dots_per_meter)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        buffer.close()
        return FlatImage(bytes(data), width, height, drawn, total)

