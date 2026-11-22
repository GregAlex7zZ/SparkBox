"""Texts of the (i) buttons of the GITT / ICI areas: one entry per control, title plus short paragraphs.
PURE PYTHON (no Qt). The texts describe what each control does in gregorithm terms; they never contain numbers that
are not defaults of the project. ``info(theme, key)`` in app.widgets.gitt_ici_setup turns an entry into a button.

The popover shows the first paragraph in the normal colour and the others muted, so the first paragraph is always a
general definition that holds whatever is selected; options and details follow, one idea per paragraph.
"""
from __future__ import annotations

INFO: dict[str, tuple[str, tuple[str, ...]]] = {
    # --- Setup -----------------------------------------------------------------------------------------------------
    "protocol": ("Protocol", (
        "The measurement protocol of the data. It is chosen here, never inferred from durations.",
        "GITT: current pulses separated by long rests.",
        "ICI: constant current with short interruptions.",
        "Changing it restores that protocol's default methods.")),
    "scope": ("Cycle or half-cycle", (
        "The single element analysed: one cycle or one half-cycle of the current Data Elaboration.",
        "Changing it recognises the impulses again.")),
    "methods": ("Calculation methods", (
        "Methods that compute D. Check several to compare them.",
        "Each method gives one D series per compatible OCV method. GITT VA and GITT Δ need GITT data.")),
    "gitt_va": ("GITT VA", (
        "D from the √t slope of the pulse voltage: D = 4/(πτ) · (V/A)² · (ΔEs / (τ·dE/d√t))².",
        "Needs V_act and A_eff. The fit window is edited in Pulse Fitting.")),
    "gitt_delta": ("GITT Δ", (
        "Same formula as GITT VA with the total transient ΔEτ = E(τ) − V0 instead of the √t slope: no fit.",
        "Needs V_act and A_eff. An optional IR correction is in Advanced settings.")),
    "ici": ("ICI", (
        "D from the √t slope of the voltage during each current interruption.",
        "Needs V_act and A_eff. The fit window is edited in Pulse Fitting.")),
    "delacourt_fixed": ("Delacourt", (
        "Spherical-diffusion model fitted to the whole constant-current pulse of a GITT.",
        "GITT only: the model needs a relaxed particle at the pulse start, which ICI interruptions do not give.",
        "Needs r_p, not V_act or A_eff. The offset is fixed: V0 − U0 plus the pre-pulse IR drop (A-02).")),
    "ocv_methods": ("OCV methods", (
        "Where the equilibrium voltage used in D comes from. Each selected method gives its own D series.",)),
    "rest_sample": ("Sampled OCV", (
        "The last real sample of each rest. Another sample can be chosen in OCV Analysis.",)),
    "ici_intercept": ("Pseudo OCV", (
        "The intercept a of the ICI √t fit, E = a + s·√t. Only for ICI; not an equilibrium OCV.",)),
    "relaxation_extrapolated": ("Extrapolated OCV", (
        "Each rest fitted with V = α + β·ln(t) and evaluated at a target time (10 800 s by default).",
        "The value depends on the target. Settings are in OCV Analysis.")),
    "active_volume": ("Active Volume (V_act)", (
        "Volume of the active phase, V in GITT VA, GITT Δ and ICI.",
        "Not the coating volume or a molar volume.")),
    "effective_area": ("Effective Area (A_eff)", (
        "Area through which diffusion happens, A in GITT VA, GITT Δ and ICI.",
        "Not automatically the geometric, BET or electrochemical area. For spheres, A = 3·V/r_p is an assumption.")),
    "particle_radius": ("Particle Radius (r_p)", (
        "Diffusion length of the spherical model. Needed by Delacourt and by the physically guided window.",)),
    "auto": ("Automatic analysis", (
        "Whether Proceed also calculates every impulse.",
        "Off: impulses are only recognised; calculate them in Pulse Fitting.")),
    "window_mode": ("Window method", (
        "How the automatic √t fit window of each impulse is chosen.",
        "Statistical search: the longest linear, stable window inside the bounds.",
        "Suggested window, fixed: exactly the bounds, no search.",
        "Physically guided: the search stops at the useful time k·r_p²/D. GITT only.")),
    "fixed_window": ("Window bounds", (
        "Start and end [s] of the range used for every impulse; empty = the suggested range.",
        "Fixed method: the fit window itself. Other methods: the range searched.",
        "Times start at the last rest sample (GITT pulse) or the last sample under current (ICI).")),
    "guidance": ("D source for guidance", (
        "The D series whose value for the same impulse sets the useful time of the guided window.",)),
    "coefficient": ("Physical coefficient", (
        "Coefficient k of the useful time t = k·r_p²/D.",
        "Default 0.0032, a theoretical reference cited by the specification (Chien). Change it if needed.")),
    "ir_mode": ("GITT Δ IR correction", (
        "A constant subtracted from ΔEτ of GITT Δ only. None by default.",
        "Pre-pulse sample: c = E(t_s) − V0. Pulse fit intercept: c = a_on − V0.")),
    "ir_time": ("Pre-pulse IR sample time", (
        "Time after the last rest sample at which R_on is read (last pulse sample with t ≤ this time). 1 s by "
        "default.",
        "The same sample is the Delacourt IR correction.")),
    "ir_post_time": ("Post-pulse IR sample time", (
        "Time after the last sample under current at which R_off is read. Empty = the pre-pulse time. It feeds "
        "no D.",)),
    "thresholds": ("Indicator thresholds", (
        "Limits of the statistical indicator of each fit: green / orange / red.",
        "q_L: linearity of the √t fit. q_S: stability of the slope. Advisory only.")),
    "proceed": ("Proceed", (
        "Recognises the impulses and rests, measures IR drops and OCV, and opens IR Drop.",
        "With automatic analysis on, it also calculates every impulse.")),
    # --- IR Drop -------------------------------------------------------------------------------------------------
    "ir_drop": ("IR drop", (
        "The instant voltage jump when the current switches; R = |ΔV/ΔI|.",
        "Pre-pulse (R_on): last rest sample → pulse sample at the sample time.",
        "Post-pulse (R_off): last sample under current → rest sample at the sample time.",
        "Click a blue sample to use it for this impulse; Use global times undoes it.")),
    "ir_times": ("IR sample times · all impulses", (
        "When the IR drop is read, for every impulse. Single-impulse times and clicked samples are kept.",
        "Pre-pulse also sets the IR correction of Delacourt (and GITT Δ, if chosen). Post-pulse feeds no D.")),
    "ir_local": ("IR sample times · this impulse", (
        "Times for this impulse only; pre-pulse and post-pulse can differ. Empty = the global time of that side.",
        "Use global times removes this impulse's times and clicked samples.")),
    "ir_accept": ("IR drop acceptance", (
        "Confirms the IR drops of this impulse: only accepted impulses give R_on / R_off in the results and export.",
        "The IR drops never block a D.",
        "● green accepted · ● orange pending or modified (changed after acceptance) · ● red rejected or invalid "
        "(no R on either side).",
        "Reject excludes the impulse's R_on / R_off by hand; Return to Pending or Accept undoes it. Accept all "
        "valid skips rejected impulses.")),
    # --- OCV -------------------------------------------------------------------------------------------------------
    "ocv_sampled": ("Sampled OCV of this rest", (
        "The diamond in the chart: the last real rest sample with t ≤ the time, from the last sample under "
        "current.",
        "Type a time for this rest only, or click another rest sample. Use global rule removes both.")),
    "ocv_extrapolation": ("OCV settings · all rests", (
        "How every rest without its own choice is read: sampled OCV time and LOG_TIME_2P extrapolation.",
        "Sampled at: the time of the sampled OCV; empty = the last rest sample.",
        "Extrapolation fits each rest with V = α + β·ln(t) and evaluates it at the target time.",
        "Fit start: fraction of the rest skipped (0.2 = fit the last 80 %). Single-rest choices are kept.")),
    "ocv_local": ("Extrapolation of this rest", (
        "Target and fit start for this rest only; empty = the global value. Use global settings removes both.",)),
    "ocv_accept": ("Rest acceptance", (
        "Confirms this rest's OCV. A D is used only when its rests are accepted.",
        "● green accepted · ● orange pending or modified (changed after acceptance) · ● red rejected or invalid.",
        "Reject excludes the rest by hand; Return to Pending or Accept undoes it. Accept all valid skips rejected "
        "rests.")),
    # --- Pulse Fitting ---------------------------------------------------------------------------------------------
    "fit_window": ("Fit window", (
        "Time range [s] of the √t fit; highlighted points are the samples fitted.",
        "Drag the shaded band or type the bounds. Apply to all impulses uses them everywhere.",
        "Re-apply automatic restores the automatic window of this impulse.")),
    "fit_values": ("Fit values", (
        "One row per D series of this impulse.",
        "Statistical: quality of the √t fit. q_L = linearity, q_S = slope stability against the neighbouring "
        "windows; lower is better. Green / orange / red come from the thresholds in Advanced settings.",
        "Physical: whether the window lies within the useful time k·r_p²/D. N/D without a physically guided "
        "window.",
        "To check: an automatic window to look at. Its reason is in the Note column: a statistical indicator not "
        "green, a window beyond the useful time, or a search that skipped the first data (also with green "
        "indicators, because the early transient was left out).",
        "Indicators are advisory: they never block acceptance.")),
    "fit_accept": ("Pulse acceptance", (
        "Accept confirms the fits of this impulse; only accepted fits give D in the results.",
        "● green accepted · ● orange pending or modified (recalculated after acceptance) · ● red rejected or "
        "invalid (incomplete, not calculated, out of date or every fit failed) · ● grey no fit needed.",
        "Reject excludes the impulse by hand: none of its D is used, GITT Δ included. Return to Pending or Accept "
        "undoes it; Accept all valid skips rejected impulses.")),
    # --- Diffusion Results -----------------------------------------------------------------------------------------
    "results_x": ("X axis", (
        "Capacity at the start of each pulse, specific capacity (per active mass) or SoC.",
        "Colour by branch: charge red, discharge blue, method by marker. Colour by method: the reverse.")),
    "results_y": ("Y axes", (
        "The three Y axes: D on Y1 (linear or log), OCV per rest on Y2, R_on and R_off on Y3.",
        "Only D in use is drawn: current, with accepted fit and rests. The others are counted below the chart.")),
}
