from math import floor, log10


def fmt_int(n: int) -> str:
    return f"{n:,}"


def fmt_time(v: float) -> str:
    """Time column display: excluded from the 4-significant-digit rule, shown unrounded by the GUI.
    15 significant digits remove binary floating-point noise (12.010000000000002 -> 12.01) without hiding real
    resolution. Single place to change if the Author later prefers fixed decimals (SPEC O-10)."""
    if v != v:
        return "NaN"
    return f"{v:.15g}"


def sig4(v: float) -> str:
    """Display value with 4 significant digits. Copy must use full precision, not this."""
    if v != v:
        return "NaN"
    if v == 0:
        return "0"
    mag = floor(log10(abs(v)))
    # Use scientific notation where fixed notation would be misleading or wide.
    if mag <= -4:
        return f"{v:.3e}"
    if mag >= 4:
        return f"{v:.0f}"
    decimals = max(0, 3 - mag)
    rounded = round(v, decimals)
    if rounded and floor(log10(abs(rounded))) > mag:
        decimals = max(0, decimals - 1)
    return f"{v:.{decimals}f}"
