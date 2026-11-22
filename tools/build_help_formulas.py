"""Pre-render the display equations of the Help Markdown files to docs/help/formulas/<key>.png.

The app shows the Markdown with Qt's text browser, which cannot typeset LaTeX (see app/help_docs.py). Run this
tool after adding or changing a `$$` equation; the app needs only the PNG files.

Usage (from the project folder):
  python tools/build_help_formulas.py            render missing equations with matplotlib (build-time only)
  python tools/build_help_formulas.py --force    re-render every equation
  python tools/build_help_formulas.py --from-html FILE.html ...
                                                 recover images already embedded in an older HTML edition
                                                 (each <img> carries its LaTeX in the alt attribute)
  python tools/build_help_formulas.py --prune    also delete images no document uses any more

Limits: matplotlib mathtext is a TeX subset; `\\hbox` and `\\mathop{\\mathrm{argmin}}` are rewritten to equivalents
it supports. Images are 200 dpi on a white background, the background of the Help window in every theme.
"""
from __future__ import annotations

import argparse
import base64
import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.help_docs import DOCS, FORMULA_DIR, FORMULA_DPI, equations, formula_key, normalize_tex  # noqa: E402

INK = "#172c38"


def wanted() -> dict[str, str]:
    """key -> normalised LaTeX for every equation of every Help document."""
    out = {}
    for doc in DOCS:
        for tex in equations(doc.path.read_text(encoding="utf-8")):
            out[formula_key(tex)] = tex
    return out


def render(tex: str, target: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    source = tex.replace(r"\hbox", r"\mathrm").replace(r"\mathop{\mathrm{argmin}}", r"\mathrm{argmin}")
    fig = plt.figure(figsize=(12, 1.3), dpi=FORMULA_DPI)
    artist = fig.text(0.5, 0.5, "$" + source + "$", ha="center", va="center", fontsize=16, color=INK)
    fig.canvas.draw()
    bounds = artist.get_window_extent(fig.canvas.get_renderer()).expanded(1.05, 1.25)
    fig.savefig(target, format="png", dpi=FORMULA_DPI, facecolor="white",
                bbox_inches=bounds.transformed(fig.dpi_scale_trans.inverted()))
    plt.close(fig)


def from_html(files: list[str], keys: dict[str, str]) -> int:
    n = 0
    for name in files:
        text = Path(name).read_text(encoding="utf-8")
        for alt_src in re.finditer(r'<img[^>]*src="data:image/png;base64,([^"]+)"[^>]*alt="([^"]*)"', text):
            tex = normalize_tex(html.unescape(alt_src.group(2)))
            key = formula_key(tex)
            if key in keys:
                (FORMULA_DIR / f"{key}.png").write_bytes(base64.b64decode(alt_src.group(1)))
                n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-html", nargs="+", metavar="FILE")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--prune", action="store_true")
    args = ap.parse_args()
    FORMULA_DIR.mkdir(parents=True, exist_ok=True)
    keys = wanted()
    if args.from_html:
        print(f"recovered {from_html(args.from_html, keys)} image(s) from HTML")
    todo = [k for k in keys if args.force or not (FORMULA_DIR / f"{k}.png").is_file()]
    for k in todo:
        render(keys[k], FORMULA_DIR / f"{k}.png")
    if todo:
        print(f"rendered {len(todo)} equation(s)")
    if args.prune:
        for f in FORMULA_DIR.glob("*.png"):
            if f.stem not in keys:
                f.unlink()
                print("removed", f.name)
    missing = [k for k in keys if not (FORMULA_DIR / f"{k}.png").is_file()]
    print(f"{len(keys)} equation(s), {len(missing)} missing")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
