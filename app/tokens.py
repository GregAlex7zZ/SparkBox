"""Design tokens (approved Ocean + Amber). Do not add colours here without Author approval.
`ctrl` (control border) and `dis` (disabled foreground) are Designer proposals awaiting approval.
`run` / `run_hover` / `on_run` : the amber of the charts (Y2) on the buttons that start a
calculation, so 'this runs something' reads the same everywhere.
`pri` / `on_pri` / `tint` : the orange #FB9A44 that System mode showed with the Author's
Windows accent, as accent_tokens derives it (Dark: as is; Light: darkened to 4.5:1 on the card), so Light and Dark
look like that System appearance. Charts keep their own palette (CHART, CYCLE_PALETTE)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Tokens:
    name: str
    bg: str
    card: str
    alt: str
    text: str
    muted: str
    border: str
    ctrl: str      # control border, >= 3:1 on card and bg
    pri: str
    on_pri: str
    tint: str
    dis: str       # disabled foreground (no fill)
    success: str
    ok_bg: str
    warn: str
    warn_bg: str
    err: str
    err_bg: str
    run: str       # amber: buttons that start a calculation (Process, Analyze, Detect, Apply & Calculate, ...)
    run_hover: str
    on_run: str    # text on `run`, >= 4.5:1


LIGHT = Tokens("light", bg="#F3F8F9", card="#FFFFFF", alt="#EAF2F5", text="#253B44", muted="#5B717D",
               border="#D5E6E9", ctrl="#6F8791", pri="#A3642C", on_pri="#FFFFFF", tint="#F4ECE6",
               dis="#8798A2", success="#157560", ok_bg="#E3F4EE", warn="#9A4B22",
               warn_bg="#FBEFE8", err="#B3261E", err_bg="#FBE9E7",
               run="#B05A31", run_hover="#9A4B22", on_run="#FFFFFF")
DARK = Tokens("dark", bg="#17252B", card="#20333B", alt="#29414B", text="#EBF3F5", muted="#B4C4CC",
              border="#41606A", ctrl="#6E8A95", pri="#FB9A44", on_pri="#101418", tint="#504A3D",
              dis="#6F8791", success="#6FD1B5", ok_bg="#1F3A36", warn="#E9AA7E",
              warn_bg="#3D2F28", err="#FF9C94", err_bg="#44292B",
              run="#E9AA7E", run_hover="#F0B184", on_run="#2A1A10")

# Metrics (px) - approved
SIDE_CARD_W = 196
SIDE_CARD_W_COLLAPSED = 58
NAV_BTN_W = 178
NAV_BTN_W_COLLAPSED = 40
NAV_BTN_H = 40
TAB_W, TAB_H, TAB_PITCH = 168, 36, 176
BAND_H = 56
