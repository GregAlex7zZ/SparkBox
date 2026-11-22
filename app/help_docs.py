"""In-app Help documents. PURE PYTHON (no Qt), tested in tests/test_help.py.

The User Guide and the Scientific Methods are Markdown files in docs/help, the single source shown both on GitHub
(which renders their $$ … $$ LaTeX blocks natively) and inside the app. Qt's text browser renders Markdown but not
LaTeX, so every display equation is pre-rendered once, by tools/build_help_formulas.py, to
docs/help/formulas/<key>.png; `prepare_markdown` swaps each block for that image. Nothing is rendered at run time,
so the packaged application needs neither a web engine nor matplotlib.

Limits: only display equations on their own lines between `$$` fences are supported (the documents use no inline
math). An equation without a pre-rendered image is shown as its LaTeX source in a code block, never dropped.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

HELP_DIR = Path(__file__).resolve().parent.parent / "docs" / "help"
FORMULA_DIR = HELP_DIR / "formulas"
FORMULA_DPI = 200                  # pixel density of the pre-rendered images; shown at 96 dpi logical size
EQUATION = re.compile(r"^\$\$[ \t]*\n(.*?)\n\$\$[ \t]*$", re.S | re.M)


@dataclass(frozen=True)
class HelpDoc:
    key: str
    title: str
    filename: str

    @property
    def path(self) -> Path:
        return HELP_DIR / self.filename


DOCS = (HelpDoc("guide", "User Guide", "gregorithm_gui_user_guide.md"),
        HelpDoc("methods", "Scientific Methods", "gregorithm_gui_scientific_methods.md"))


def normalize_tex(tex: str) -> str:
    """Whitespace-insensitive form of an equation, so re-wrapping a formula does not change its image."""
    return " ".join(tex.split())


def formula_key(tex: str) -> str:
    """Stable file stem of an equation image: the first 16 hex digits of the SHA-256 of the normalised LaTeX."""
    return hashlib.sha256(normalize_tex(tex).encode("utf-8")).hexdigest()[:16]


def equations(markdown: str) -> list[str]:
    """Normalised LaTeX of every display equation, in document order."""
    return [normalize_tex(m.group(1)) for m in EQUATION.finditer(markdown)]


def prepare_markdown(markdown: str, formula_dir: Path = FORMULA_DIR) -> tuple[str, list[str]]:
    """Markdown for Qt: each `$$` block becomes its own paragraph with the pre-rendered image (`formula:<key>`).
    Returns the text and the keys whose image is missing (those blocks fall back to a fenced LaTeX code block)."""
    missing: list[str] = []

    def swap(match: re.Match) -> str:
        tex = normalize_tex(match.group(1))
        key = formula_key(tex)
        if (formula_dir / f"{key}.png").is_file():
            return f"![equation](formula:{key})"
        missing.append(key)
        return f"```\n{tex}\n```"

    return EQUATION.sub(swap, markdown), missing
