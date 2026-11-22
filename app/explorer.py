"""Data Explorer: column catalogue, fixed chart palette, cycle-filter grammar, point-counter text, gesture geometry and
the per-dataset view state machine. PURE PYTHON (no Qt), unit-tested in tests/test_explorer.py.

Nothing here computes a scientific value. The chart shows real published points only; the numbers in the counter come
from the backend adapter. Geometry functions only map pixels to axis ranges.
"""
from __future__ import annotations
import math
import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Optional

from .units import CAPACITY, CURRENT, ENERGY, TIME, VOLTAGE, UnitPrefs

# ---------------------------------------------------------------------------------------------------------------------
# Columns of points/elaborated.parquet . Every column is offered on X, Y1 and Y2;
# Y2 also offers None. Rest is drawn as 0/1 and Step as its stored code 0-3, as plain numbers (no special cases).


@dataclass(frozen=True)
class ColumnSpec:
    key: str                         # exact engine field name
    label: str                       # UI label (English)
    quantity: Optional[str] = None   # units.QUANTITIES member, None = no unit
    nullable: bool = False           # energies may be null: drawn as gaps, never as zero


COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec("time_s", "Time", TIME),
    ColumnSpec("current_a", "Current", CURRENT),
    ColumnSpec("voltage_v", "Voltage", VOLTAGE),
    ColumnSpec("charge_capacity_ah", "Charge Capacity", CAPACITY),
    ColumnSpec("discharge_capacity_ah", "Discharge Capacity", CAPACITY),
    ColumnSpec("charge_energy_wh", "Charge Energy", ENERGY, nullable=True),
    ColumnSpec("discharge_energy_wh", "Discharge Energy", ENERGY, nullable=True),
    ColumnSpec("cycle", "Cycle"),
    ColumnSpec("half_cycle", "Half-Cycle"),
    ColumnSpec("rest", "Rest"),
    ColumnSpec("step", "Step"),
    ColumnSpec("source_row", "Source Row"),
)
COLUMN_BY_KEY = {c.key: c for c in COLUMNS}
DEFAULT_X, DEFAULT_Y1, DEFAULT_Y2 = "time_s", "voltage_v", "current_a"
NONE_LABEL = "None"


def axis_label(key: Optional[str], units: UnitPrefs) -> str:
    """'Time [h]', 'Cycle', 'None'. Used for the selectors, axis titles, legend and hover readout."""
    if key is None:
        return NONE_LABEL
    c = COLUMN_BY_KEY[key]
    return f"{c.label} [{units.unit(c.quantity)}]" if c.quantity else c.label


def ascending_x(key: str) -> bool:
    """Only time_s is guaranteed strictly increasing. PyQtGraph clip-to-view and binary search are allowed only
    then; for every other X the adapter clips."""
    return key == "time_s"


# ---------------------------------------------------------------------------------------------------------------------
# Fixed chart palette: the chart surface (background, frame, grid, ticks, titles, legend, hover readout, overlay
# cards inside the chart, flat image and saved image) is IDENTICAL in light and dark app themes. Series colours start
# from Ocean / Amber and the user may change them (Plot Colors).
CHART = {
    "bg": "#FFFFFF", "frame": "#BFD1D6", "grid": "#E4EDEF", "tick": "#5B717D", "text": "#253B44", "cross": "#6F8691",
    "y1": "#006D85", "y2": "#B05A31", "chip_bg": "#E1F3F7", "chip_fg": "#006D85", "dim": "#253B44",
}
UPDATING_DELAY_MS = 250          # show 'Updating…' only if a request lasts longer (avoids flashing) - proposal,
VIEW_DEBOUNCE_MS = 120           # wheel / Axis Ranges bursts are merged into one request - proposal
THIN_PX = 8                      # Box Zoom: thinner than this zooms one direction only
ZOOM_STEP = 0.8                  # one wheel notch keeps 80 % of the span (zoom in), 1/0.8 out - proposal


# ---------------------------------------------------------------------------------------------------------------------
# Cycle filter grammar [approved]: "1", "1-100", "1,6,9", "1-5,9,12-20"; spaces allowed; empty = all cycles.

FILTER_HINT = "e.g. 1 · 1-100 · 1,6,9 · 1-5,9,12-20 · empty = all"
_USE = "Use numbers or ranges like 12-20, separated by commas."
_NUM = re.compile(r"^\d+$")
_RANGE = re.compile(r"^(\d*)\s*-\s*(\d*)$")


class FilterError(ValueError):
    pass


def parse_cycle_filter(text: str) -> tuple[tuple[int, int], ...]:
    """Return inclusive (first, last) pairs in the order typed; () means all cycles. Raises FilterError with the
    message shown under the field. Cycle IDs are taken as written (0 is valid)."""
    s = text.strip()
    if not s:
        return ()
    out: list[tuple[int, int]] = []
    for raw in s.split(","):
        term = raw.strip()
        if not term:
            raise FilterError(f"There is an empty item between commas. {_USE}")
        if _NUM.match(term):
            n = int(term)
            out.append((n, n))
            continue
        m = _RANGE.match(term)
        if m:
            a, b = m.group(1), m.group(2)
            if not a and not b:
                raise FilterError(f"“{term}” is not a cycle number or range. {_USE}")
            if not b:
                raise FilterError(f"Range “{term}” has no end. {_USE}")
            if not a:
                raise FilterError(f"Range “{term}” has no start. {_USE}")
            lo, hi = int(a), int(b)
            if lo > hi:
                raise FilterError(f"Range “{term}” is reversed. Write it as {hi}-{lo}.")
            out.append((lo, hi))
            continue
        raise FilterError(f"“{term}” is not a cycle number or range. {_USE}")
    return tuple(out)


def filter_text(ranges: tuple[tuple[int, int], ...]) -> str:
    """Canonical text of an applied filter, e.g. '1-5, 9, 12-20'; 'all' when empty."""
    if not ranges:
        return "all"
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in ranges)


def resolve_cycles(ranges: tuple[tuple[int, int], ...], available: list[int]) -> Optional[tuple[int, ...]]:
    """Map the typed ranges onto cycle IDs that EXIST in the result (sorted). None = no filter (all cycles). Missing IDs
    are simply not present: nothing is interpolated or renumbered. An empty tuple means 'no match'."""
    if not ranges:
        return None
    return tuple(c for c in sorted(set(available)) if any(a <= c <= b for a, b in ranges))


def describe_available(available: list[int]) -> str:
    """'1–128', or '0–128 (some numbers missing)' when the IDs are not contiguous; 'none' when empty."""
    ids = sorted(set(available))
    if not ids:
        return "none"
    span = f"{ids[0]}–{ids[-1]}"
    return span if len(ids) == ids[-1] - ids[0] + 1 else f"{span} (some numbers missing)"


# ---------------------------------------------------------------------------------------------------------------------
# Point counter 'Rendered / Total — %' . Counts are distinct eligible canonical rows in the cycle/X
# window. A row used by both Y curves counts once; Y viewport clipping never changes the denominator. Rows lacking every
# active Y value are excluded because no drawable sample exists. Both exact counts come from the adapter.

DASH = "—"


def format_coverage(rendered: Optional[int], total: Optional[int]) -> str:
    if rendered is None or total is None:
        return f"{DASH} / {DASH}"
    if rendered < 0 or total < 0 or rendered > total:
        raise ValueError(f"Inconsistent counts from the adapter: rendered={rendered}, total={total}")
    if total == 0:
        return f"0 / 0 {DASH} {DASH}"
    if rendered == total:
        pct = "100%"
    elif rendered == 0:
        pct = "0%"
    else:
        p = rendered * 100.0 / total
        if p < 0.1:
            pct = "<0.1%"
        else:
            s = f"{p:.1f}"
            pct = "99.9%" if s == "100.0" else f"{s}%"
    return f"{rendered:,} / {total:,} {DASH} {pct}"


# ---------------------------------------------------------------------------------------------------------------------
# Ranges and gesture geometry.


@dataclass(frozen=True)
class Range:
    lo: float
    hi: float

    def __post_init__(self):
        if not (math.isfinite(self.lo) and math.isfinite(self.hi)) or not self.lo < self.hi:
            raise ValueError(f"Invalid range {self.lo!r}..{self.hi!r}")

    @property
    def span(self) -> float:
        return self.hi - self.lo


@dataclass(frozen=True)
class Viewport:
    """None on an axis = automatic (fit the data of the current filter)."""
    x: Optional[Range] = None
    y1: Optional[Range] = None
    y2: Optional[Range] = None

    @property
    def auto(self) -> bool:
        return self.x is None and self.y1 is None and self.y2 is None


def _tiny(r: Range, new_span: float) -> bool:
    return new_span <= 1e-12 * max(abs(r.lo), abs(r.hi), 1.0)


def box_zoom(px0: float, py0: float, px1: float, py1: float, width: float, height: float,
             x: Range, y1: Range, y2: Optional[Range]) -> Optional[Viewport]:
    """Pixel rectangle (plot-area coordinates, origin top-left, y down) -> new ranges.
    The SAME vertical pixel band is applied to Y1 and to Y2, each with its own scale. A rectangle thinner than THIN_PX in
    height zooms X only; thinner in width zooms the Y axes only; both thin = a click, returns None."""
    if width <= 0 or height <= 0:
        return None
    ax0, ax1 = sorted((max(0.0, min(width, px0)), max(0.0, min(width, px1))))
    ay0, ay1 = sorted((max(0.0, min(height, py0)), max(0.0, min(height, py1))))
    wide, tall = (ax1 - ax0) >= THIN_PX, (ay1 - ay0) >= THIN_PX
    if not wide and not tall:
        return None

    def xmap(r: Range) -> Range:
        lo, hi = r.lo + ax0 / width * r.span, r.lo + ax1 / width * r.span
        return r if _tiny(r, hi - lo) else Range(lo, hi)

    def ymap(r: Range) -> Range:
        lo, hi = r.hi - ay1 / height * r.span, r.hi - ay0 / height * r.span
        return r if _tiny(r, hi - lo) else Range(lo, hi)

    nx = xmap(x) if wide else x
    if not tall:
        return Viewport(nx, y1, y2)
    return Viewport(nx, ymap(y1), ymap(y2) if y2 is not None else None)


def wheel_zoom(r: Range, anchor: float, steps: float) -> Range:
    """Zoom one axis around the value under the pointer. steps > 0 zooms in (one notch = 1.0, trackpads give fractions)."""
    k = ZOOM_STEP ** steps
    lo, hi = anchor - (anchor - r.lo) * k, anchor + (r.hi - anchor) * k
    if _tiny(r, hi - lo) or not (math.isfinite(lo) and math.isfinite(hi)):
        return r
    return Range(lo, hi)


def pan_x(r: Range, dx_px: float, width: float) -> Range:
    """Content follows the pointer: dragging right shows earlier X."""
    d = -dx_px / width * r.span
    return Range(r.lo + d, r.hi + d)


def pan_y(r: Range, dy_px: float, height: float) -> Range:
    """Content follows the pointer: dragging down shows higher Y (pixel y grows downwards)."""
    d = dy_px / height * r.span
    return Range(r.lo + d, r.hi + d)


def parse_range(min_text: str, max_text: str) -> tuple[Optional[Range], str]:
    """Axis Ranges fields. Both empty = automatic for that axis. Values are taken exactly as typed (never rounded)."""
    a, b = min_text.strip().replace("−", "-"), max_text.strip().replace("−", "-")
    if not a and not b:
        return None, ""
    try:
        lo, hi = float(a), float(b)
    except ValueError:
        return None, "Enter a number in both Min and Max."
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return None, "Enter a finite number."
    if not lo < hi:
        return None, "Min must be smaller than Max."
    return Range(lo, hi), ""


def format_axis_bound(value: float) -> str:
    """Compact editable-axis display: never more than four significant digits."""
    return f"{value:.4g}".replace("-", "−")


# ---------------------------------------------------------------------------------------------------------------------
# Per-dataset view state.


class Mode(str, Enum):
    INTERACTIVE = "interactive"
    RENDERING = "rendering"          # Render All Points (or Save Image) running; controls locked
    FLAT = "flat"                    # flat image with every point in view; not interactive


class Phase(str, Enum):
    LOADING = "loading"              # no valid view drawn yet
    READY = "ready"
    UPDATING = "updating"            # a request is pending; the last valid view stays visible
    EMPTY = "empty"                  # 0 points in view
    NO_MATCH = "nomatch"             # the applied cycle filter matches no cycle of this result


PLOT_STYLES = (("lines", "Lines"), ("markers", "Markers"), ("lines_markers", "Lines + Markers"))   # OPEN (O-27)


@dataclass
class ExplorerState:
    x: str = DEFAULT_X
    y1: str = DEFAULT_Y1
    y2: Optional[str] = DEFAULT_Y2
    y1_color: str = CHART["y1"]
    y2_color: str = CHART["y2"]
    legend: bool = True
    point_tracking: bool = True
    style: str = "lines"
    filter_input: str = ""                         # text in the field (may be invalid)
    filter_error: str = ""
    applied: tuple[tuple[int, int], ...] = ()      # last applied filter; () = all cycles
    viewport: Viewport = field(default_factory=Viewport)
    mode: Mode = Mode.INTERACTIVE
    phase: Phase = Phase.LOADING
    version: int = 0
    has_view: bool = False
    coverage: Optional[tuple[int, int]] = None
    view_error: str = ""                           # last failed view request (shown verbatim)
    render_fraction: Optional[float] = None
    render_done: Optional[int] = None
    render_total: Optional[int] = None
    render_error: str = ""
    coverage_flat: Optional[tuple[int, int]] = None   # counts of the flat image (always equal: every point drawn)
    save_path: Optional[str] = None                # Save Image from interactive mode renders first, then writes here

    # --- derived ---
    @property
    def locked(self) -> bool:
        return self.mode is not Mode.INTERACTIVE

    @property
    def can_apply_filter(self) -> bool:
        if self.locked or self.filter_error:
            return False
        try:
            return parse_cycle_filter(self.filter_input) != self.applied
        except FilterError:
            return False

    @property
    def can_clear_filter(self) -> bool:
        return not self.locked and (bool(self.applied) or bool(self.filter_input.strip()))

    @property
    def can_render(self) -> bool:
        """Render All Points button: enabled to start (interactive, something drawn) or to leave the flat image."""
        if self.mode is Mode.FLAT:
            return True
        return self.mode is Mode.INTERACTIVE and self.has_view and self.phase in (Phase.READY, Phase.UPDATING) \
            and bool(self.coverage and self.coverage[1] > 0)

    @property
    def can_save(self) -> bool:
        return self.mode is Mode.FLAT or self.can_render

    # --- requests ---
    def request(self) -> int:
        """Called whenever the view must be (re)loaded. Returns the version the response must carry."""
        self.version += 1
        self.phase = Phase.UPDATING if self.has_view else Phase.LOADING
        self.view_error = ""
        return self.version

    def accept(self, version: int, rendered: int, total: int, filter_matched: bool = True) -> bool:
        """A worker response. Stale responses (older version, or arriving while locked) are discarded."""
        if version != self.version or self.locked:
            return False
        format_coverage(rendered, total)                   # validates the counts
        self.coverage, self.has_view = (rendered, total), True
        self.phase = Phase.NO_MATCH if not filter_matched else (Phase.EMPTY if total == 0 else Phase.READY)
        return True

    def reject(self, version: int, message: str) -> bool:
        if version != self.version:
            return False
        self.view_error = message
        self.phase = Phase.READY if self.has_view else Phase.LOADING
        return True

    # --- editing (each returns True when a new view request is needed) ---
    def set_axis(self, axis: str, key: Optional[str]) -> bool:
        if self.locked:
            return False
        if key is not None and key not in COLUMN_BY_KEY:
            raise ValueError(f"Unknown column {key!r}")
        if axis in ("x", "y1") and key is None:
            raise ValueError("X and Y1 are required")
        if getattr(self, axis) == key:
            return False
        setattr(self, axis, key)
        self.viewport = replace(self.viewport, **{axis: None})          # the changed axis fits its new data
        return True

    def type_filter(self, text: str) -> None:
        self.filter_input = text
        try:
            parse_cycle_filter(text)
            self.filter_error = ""
        except FilterError as e:
            self.filter_error = str(e)

    def apply_filter(self) -> bool:
        if not self.can_apply_filter:
            return False
        self.applied = parse_cycle_filter(self.filter_input)
        self.viewport = Viewport()
        return True

    def clear_filter(self) -> bool:
        if not self.can_clear_filter:
            return False
        changed = bool(self.applied)
        self.filter_input, self.filter_error, self.applied = "", "", ()
        if changed:
            self.viewport = Viewport()
        return changed

    def set_viewport(self, vp: Viewport) -> bool:
        if self.locked or vp == self.viewport:
            return False
        if vp.y2 is not None and self.y2 is None:
            vp = replace(vp, y2=None)
        self.viewport = vp
        return True

    def reset_view(self) -> bool:
        """Reset View button only (no double-click reset)."""
        return self.set_viewport(Viewport())

    def set_color(self, axis: str, hex_color: str) -> bool:
        """Display-only, immediate, no request. Not allowed while locked."""
        if self.locked or not re.fullmatch(r"#[0-9A-Fa-f]{6}", hex_color):
            return False
        setattr(self, f"{axis}_color", hex_color.upper())
        return True

    def reset_colors(self) -> None:
        if not self.locked:
            self.y1_color, self.y2_color = CHART["y1"], CHART["y2"]

    def set_legend(self, on: bool) -> bool:
        if self.locked:
            return False
        self.legend = bool(on)
        return True

    def set_point_tracking(self, on: bool) -> bool:
        """Display-only hover tracking preference, kept independently for each dataset."""
        if self.locked:
            return False
        self.point_tracking = bool(on)
        return True

    def set_style(self, style: str) -> bool:
        if self.locked or style not in dict(PLOT_STYLES):
            return False
        self.style = style
        return True

    # --- Render All Points / Save Image ---
    def begin_render(self, save_path: Optional[str] = None) -> bool:
        if self.mode is not Mode.INTERACTIVE or not self.can_render:
            return False
        self.mode, self.save_path, self.render_error = Mode.RENDERING, save_path, ""
        self.render_fraction = self.render_done = self.render_total = None
        return True

    def render_progress(self, fraction: Optional[float], done: Optional[int], total: Optional[int]) -> None:
        if self.mode is Mode.RENDERING:
            self.render_fraction, self.render_done, self.render_total = fraction, done, total

    def render_finished(self, drawn: int, total: int) -> bool:
        """The flat image replaces the interactive chart only if EVERY point in view was drawn; otherwise it is a
        failure (never show 100 % unless true)."""
        if self.mode is not Mode.RENDERING:
            return False
        if drawn != total:
            self.render_failed(f"Not every point was drawn ({drawn:,} of {total:,}). The image was not used.")
            return False
        self.mode, self.coverage_flat = Mode.FLAT, (drawn, total)
        return True

    def render_cancelled(self) -> None:
        if self.mode is Mode.RENDERING:
            self.mode, self.save_path = Mode.INTERACTIVE, None

    def render_failed(self, message: str) -> None:
        if self.mode is Mode.RENDERING:
            self.mode, self.save_path, self.render_error = Mode.INTERACTIVE, None, message

    def leave_flat(self) -> bool:
        if self.mode is not Mode.FLAT:
            return False
        self.mode, self.save_path, self.coverage_flat = Mode.INTERACTIVE, None, None
        return True

    def dismiss_render_error(self) -> None:
        self.render_error = ""

    def displayed_coverage(self) -> Optional[tuple[int, int]]:
        if self.mode is Mode.FLAT:
            return self.coverage_flat
        if self.phase is Phase.LOADING:
            return None
        return self.coverage

    # --- external events ---
    def result_replaced(self) -> None:
        """A new result was published (Reprocess): keep axes, filter, colours, legend and style; drop the flat
        image, reset the view and reload."""
        self.mode, self.save_path, self.render_error, self.coverage_flat = Mode.INTERACTIVE, None, "", None
        self.viewport, self.has_view, self.coverage = Viewport(), False, None
        self.phase = Phase.LOADING

    def units_changed(self) -> None:
        """New display units: the numbers on the axes change, so the view is reset and reloaded; no flat image kept."""
        self.result_replaced()
