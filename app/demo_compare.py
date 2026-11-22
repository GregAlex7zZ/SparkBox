"""SYNTHETIC Comparison for the demo backend: every dataset reuses the synthetic points with a small, deterministic
per-dataset change so the overlay is visible. NOT scientific. The real adapter reads each dataset's own published
generation (read_window with a point budget, same cycles and ranges for all)."""
from __future__ import annotations
import io, time
from .backend import CompareSeries, Curve, FlatImage, Progress, RenderCancelled
from .units import scale


class CompareDemoMixin:
    def _variant(self, ds_key: str, key: str, a):
        k = (sum(map(ord, ds_key)) % 7) / 7.0                  # 0..1, stable per dataset
        if key.endswith(("capacity_ah", "energy_wh")):
            return a * (1.0 - 0.06 * k)
        if key == "voltage_v":
            return a - 0.03 * k
        return a

    def _cmp_series(self, ds_key, req):
        import numpy as np
        P = self._arrays()
        rows = np.arange(P["time_s"].size)
        if req.cycles is not None:
            rows = rows[np.isin(P["cycle"].astype(int), np.asarray(req.cycles, int))]
        from .explorer import COLUMN_BY_KEY
        def col(key):
            q = COLUMN_BY_KEY[key].quantity
            a = self._variant(ds_key, key, P[key][rows])
            return a * scale(q, req.units.unit(q)) if q else a
        x, y = col(req.x), col(req.y)
        ok = np.isfinite(x) & np.isfinite(y)
        if req.x_range:
            ok &= (x >= req.x_range[0]) & (x <= req.x_range[1])
        if req.y_range:
            ok &= (y >= req.y_range[0]) & (y <= req.y_range[1])
        return rows, x, y, ok

    def compare_explorer(self, req, cancel):
        import numpy as np
        out = []
        budget = max(200, req.plot_width_px * 2)               # per dataset
        for ds_key, _rid in req.items:
            rows, x, y, ok = self._cmp_series(ds_key, req)
            idx = np.flatnonzero(ok)
            step = max(1, idx.size // budget)
            keep = idx[::step]
            out.append(CompareSeries(ds_key, Curve(x[keep], y[keep], rows[keep]), int(idx.size), int(keep.size)))
        return out

    def compare_render_all(self, req, series, style, progress, cancel):
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        P = style.palette
        fig = Figure(figsize=(style.width_px / 100, style.height_px / 100), dpi=100 * style.device_pixel_ratio,
                     facecolor=P.get("bg", "#FFFFFF"))
        FigureCanvasAgg(fig)
        ax = fig.add_axes((64 / style.width_px, 46 / style.height_px, 1 - 88 / style.width_px, 1 - 62 / style.height_px))
        drawn = total = 0
        for i, (ds_key, _rid) in enumerate(req.items):
            if cancel.is_set():
                raise RenderCancelled()
            rows, x, y, ok = self._cmp_series(ds_key, req)
            col, label = series.get(ds_key, ("#006D85", ds_key))
            ax.plot(x[ok], y[ok], color=col, linewidth=1.0,
                    linestyle="-" if style.style != "markers" else "none", marker="." if style.style != "lines" else None,
                    markersize=2, label=label)
            drawn += int(ok.sum()); total += int(ok.sum())
            time.sleep(self.step_delay)
            progress(Progress((i + 1) / len(req.items), "Rendering all points", None, None))
        if req.x_range:
            ax.set_xlim(*req.x_range)
        if req.y_range:
            ax.set_ylim(*req.y_range)
        ax.set_xlabel(style.x_label, fontsize=9, color=P.get("text", "#253B44"))
        ax.set_ylabel(style.y1_label, fontsize=9, color=P.get("text", "#253B44"))
        ax.grid(True, color=P.get("grid", "#E4EDEF"), linewidth=0.8)
        if style.legend:
            ax.legend(fontsize=8, frameon=True, facecolor="#FFFFFF", edgecolor=P.get("frame", "#BFD1D6"))
        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
        return FlatImage(buf.getvalue(), style.width_px, style.height_px, drawn, total)
