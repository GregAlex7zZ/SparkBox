"""SYNTHETIC dQ/dV for the demo backend: peak shapes that imitate a cell, NOT the gregorithm derivative.
The real adapter calls analyze_dqdv(DqdvRequest, progress, cancel) and reads tables/dqdv.parquet."""
from __future__ import annotations
import csv, hashlib, math, os, time

from .backend import DqdvCancelled, DqdvCurveData, DqdvSegment, ExportCancelled, ExportDone, Progress, ReconCurveData
from .units import CAPACITY, VOLTAGE, scale


def _g(v, m, s, a):
    return a * math.exp(-((v - m) ** 2) / (2 * s * s))


def _curve(cycle: int, phase: str, step_v: float):
    k, sh = 1 - 0.0022 * cycle, 0.0006 * cycle
    if phase == "charge":
        f = lambda v: (_g(v, 3.45 + sh, 0.035, 0.240 * k) + _g(v, 3.63 + sh, 0.05, 0.150 * k) + _g(v, 3.96 + sh, 0.06, 0.090 * k) + 0.018)
        spans = ((3.30, 3.74), (3.78, 4.15))                 # a plateau splits the charge curve into two spans
    else:
        f = lambda v: -(_g(v, 3.40 - sh, 0.04, 0.230 * k) + _g(v, 3.58 - sh, 0.05, 0.140 * k) + _g(v, 3.90 - sh, 0.06, 0.085 * k) + 0.016)
        spans = ((3.25, 4.10),)
    out = []
    for si, (a, b) in enumerate(spans):
        n = int((b - a) / step_v)
        vs = [round((math.floor(a / step_v) + j + 0.5) * step_v, 6) for j in range(n)]   # grid midpoints
        if phase == "discharge":
            vs = vs[::-1]                                     # table order follows time (voltage falling)
        out.append((si, vs, [f(v) * (1 + 0.03 * math.sin(37 * v)) for v in vs], [f(v) for v in vs]))
    return out


_DEMO_STEP_V = 0.005                                  # SYNTHETIC point spacing of the demo curves


class DqdvDemoMixin:
    def _dq(self):
        if not hasattr(self, "_dqdv"):
            self._dqdv = {}
        return self._dqdv

    def analyze_dqdv(self, ds_key, elab_result_id, params, dcir_published_id, progress, cancel):
        from .dqdv import DqdvResult
        n = len(params.cycles)
        for i, c in enumerate(params.cycles):
            if cancel.is_cancelled() if hasattr(cancel, "is_cancelled") else cancel.is_set():
                raise DqdvCancelled()
            time.sleep(self.step_delay * 3)
            progress(Progress((i + 1) / n, f"Cycle {i + 1} of {n}", i + 1, n))
        progress(Progress(1.0, "Saving result…", n, n, publishing=True))
        time.sleep(self.step_delay * 4)
        rid = f"dqdv-{abs(hash((elab_result_id, params, time.time()))) % 10**8:08x}"
        self._result_ds[rid] = ds_key
        unavailable = tuple((c, "discharge", "DQDV_NO_VALID_SPAN") for c in params.cycles if c == 100)
        store = {"params": params, "unavailable": unavailable}
        self._dq()[rid] = store
        pts = sum(len(vs) for c in params.cycles for ph in ("charge", "discharge") if (c, ph, "DQDV_NO_VALID_SPAN") not in unavailable
                  for _si, vs, _r, _s in _curve(c, ph, _DEMO_STEP_V))
        fp, nsp = "", 0
        if dcir_published_id:
            try:
                pub = self.dcir_published(dcir_published_id, self._u(ds_key))
                fp, nsp = pub.fingerprint, sum(1 for s in self.correction_spans(dcir_published_id, self._u(ds_key)) if s.has_span)
            except Exception:
                pass
        curves = 2 * len(params.cycles) - len(unavailable)
        store["n_points"], store["fp"], store["nsp"] = pts, fp, nsp
        return DqdvResult(rid, elab_result_id, params, curves, pts, unavailable, fp, nsp, False,
                          (("Curve records", str(curves)), ("Point records", f"{pts:,}")))

    def dqdv_curves(self, dqdv_result_id, units):
        import numpy as np
        st = self._dq()[dqdv_result_id]
        p = st["params"]
        sv, sq = scale(VOLTAGE, units.voltage), scale(CAPACITY, units.capacity)
        ds = self._result_ds.get(dqdv_result_id, "")
        sq *= 1.0 - 0.08 * ((sum(map(ord, ds)) % 7) / 7.0)       # SYNTHETIC: datasets differ a little
        out = []
        for c in p.cycles:
            for hi, ph in enumerate(("charge", "discharge")):
                if (c, ph, "DQDV_NO_VALID_SPAN") in st["unavailable"]:
                    continue
                segs = [DqdvSegment(si, np.asarray(vs) * sv, np.asarray(raw) * sq, (np.asarray(sm) * sq) if p.smoothing else None)
                        for si, vs, raw, sm in _curve(c, ph, _DEMO_STEP_V)]
                out.append(DqdvCurveData(c, 2 * c - 1 + hi, ph, 1000 * c + 200 * hi, segs))
        return out

    def export_dqdv(self, dqdv_result_id, cycles, phases, columns, units, fmt, path, progress, cancel):
        """SYNTHETIC: CSV of the shown curves, display units, full precision, never overwrites."""
        from .dqdv import column_title
        if fmt != "csv":
            raise RuntimeError("SYNTHETIC DEMO: Parquet export is written by the adapter (PyArrow).")
        if os.path.exists(path):
            raise FileExistsError(f"{path} already exists (the export never overwrites).")
        rows = 0
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([column_title(k, units) for k in columns])
            for cv in self.dqdv_curves(dqdv_result_id, units):
                if cv.cycle not in cycles or cv.phase not in phases:
                    continue
                for sg in cv.segments:
                    for i in range(len(sg.v)):
                        if cancel.is_set():
                            f.close(); os.remove(path)
                            raise ExportCancelled()
                        rec = {"cycle": cv.cycle, "half_cycle": cv.half_cycle, "phase": cv.phase,
                               "anchor_source_row": cv.anchor_source_row, "segment_index": sg.segment_index,
                               "voltage_mid": repr(float(sg.v[i])), "dqdv_raw": repr(float(sg.raw[i])),
                               "dqdv_smoothed": "" if sg.smoothed is None else repr(float(sg.smoothed[i]))}
                        w.writerow([rec[k] for k in columns])
                        rows += 1
                progress(Progress(None, "Writing rows", rows, None))
        return [ExportDone(path, rows, os.path.getsize(path), hashlib.sha256(open(path, "rb").read()).hexdigest())]

    def dqdv_reconstruction(self, dqdv_result_id, units):
        """SYNTHETIC: a smooth voltage curve and a slightly offset 'reconstruction' (NOT the gregorithm integral)."""
        import numpy as np
        st = self._dq()[dqdv_result_id]
        p = st["params"]
        if not getattr(p, "reconstruction", False):
            return []
        sv, sq = scale(VOLTAGE, units.voltage), scale(CAPACITY, units.capacity)
        out = []
        for c in p.cycles:
            for hi, ph in enumerate(("charge", "discharge")):
                if (c, ph, "DQDV_NO_VALID_SPAN") in st["unavailable"]:
                    continue
                q = np.linspace(0.0, 2.5 * (1 - 0.002 * c), 400)
                shape = 3.3 + 0.8 * q / q[-1] + 0.05 * np.tanh((q - 1.2) * 6)
                v = shape if ph == "charge" else shape[::-1] - 0.06
                rebuilt_q = q[2:-2:4]
                rebuilt_v = np.interp(rebuilt_q, q, v) + (0.002 if ph == "charge" else -0.002)
                out.append(ReconCurveData(c, 2 * c - 1 + hi, ph, 1000 * c + 200 * hi, q * sq, v * sv,
                                          rebuilt_q * sq, rebuilt_v * sv, 0.4 + 0.01 * c, 0.002 * sv))
        return out

    def release_dqdv(self, dqdv_result_id):
        pass
