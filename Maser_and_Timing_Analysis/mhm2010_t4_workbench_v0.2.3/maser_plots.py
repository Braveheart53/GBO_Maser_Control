#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Reference-style 1 PPS delta plots shared by the GUI and the .vszh5 export.

===============================================================================
 Title          : 1 PPS Delta Reference Plots
 Filename       : maser_plots.py
 Version        : 0.0.2 (Semantic Versioning 2.0.0)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-03
 Modified       : 2026-11-10
 Language       : Python 3.12
 Requires       : numpy; vszh5_writer >= 0.0.24 (VszDoc, VeuszWriter, vz_text,
                  local_time_axis, mjd_to_epoch)
 Licence        : GPL-3.0-or-later; see LICENSE
 Revision       : 0.0.1 Statistics, straight-line fit with OLS standard
                  errors, rate, synthesizer adjustment and the two page
                  layouts the operator supplied as images on 2026-11-03;
                  local ISO-8601 date-time twins of both pages (writer
                  0.0.24), requested by the operator on 2026-11-03.
                  0.0.2 (operator, 2026-11-10) The fit figure also states the
                  SUGGESTED SETTINGS at the application epoch - MHM2010 DDS
                  count change AND drift change (T4: SETFSREL and
                  DRIFTCOMP) - in a second box at the upper left, with
                  headroom above the data (Veusz draws markers over a
                  label, so the box never covers points); meaningful
                  dataset, widget and page names
                  (Ref1PPS_<channel>_...), Veusz tags by source and type,
                  and every number shown in a box also stored as a
                  full-precision dataset (boxes keep the figure layout).
===============================================================================

THE TWO FIGURES
---------------
1. "<maser> 1PPS Deltas": the time differences (ns) against MJD - MJD0,
   points only, with a box "num points = N / mean = ... ns   std = ... ns".
2. Data and fitted line y = a1*x + a0 (x = MJD - MJD0 in days, y in ns),
   with a box giving N, mean, std, a0 +/- s(a0), a1 +/- s(a1) in ns/day,
   rate = a1 * 1e-9 / 86400 s and the synthesizer adjustment; below it, the
   residuals with "mean = ... ns   std = ... ns".

All numbers come from this module, so the GUI and the Veusz file agree.

EQUATIONS (ordinary least squares, N points)
--------------------------------------------
    xbar = mean(x),  Sxx = sum (x - xbar)^2,  Sxy = sum (x - xbar)(y - ybar)
    a1 = Sxy / Sxx,  a0 = ybar - a1 xbar,  r = y - (a1 x + a0)
    s^2 = sum r^2 / (N - 2)
    s(a1) = s / sqrt(Sxx),  s(a0) = s sqrt(1/N + xbar^2 / Sxx)
    rate  = a1 [ns/day] * 1e-9 / 86400   (dimensionless fractional frequency)
The displayed "std" is the population standard deviation (divisor N), as
numpy.std reports by default. s(a0) and s(a1) are conditional IID-OLS
standard errors; they ignore colored noise and GPS systematics.

MHM-2010 synthesizer adjustment (same relationship as maser_core.correction):
    counts = sign * rate * (284.08 + 2e-7 * DDS) / 2e-14
One count is one least-significant digit (1e-7) of the displayed DDS
setting; with DDS near 5752 one count is 7.04e-17 in fractional frequency.
Without a DDS setting the nominal 284.08 is used; the difference is below
1e-5 of the result.

T4 iMaser 3000 (T4S-MAN-0012 1.27, Sec. 9.6, Eq. 12; Sec. 9.4):
    y0 = -d(nu_synth) / (284 x 5 MHz)  ->  d(nu_synth) = sign * rate * 1.42e9 Hz
quantized to the 39-bit step 5 MHz / 2**39 = 9.0949e-6 Hz. The box shows the
SETFSREL entry (T4S-MAN-0015) under the selected sign convention and the
expected synthesizer change, which RDFS confirms after entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
import math

import numpy as np

from vszh5_writer import VszDoc, VeuszWriter, vz_text, local_time_axis, mjd_to_epoch, iso_local_strings
from export_names import vname, tags, full, full_strings

__version__ = "0.0.2"

#: Veusz text line break (two backslashes in the stored script).
LINE_BREAK = "\\\\"
#: Matplotlib "tab:blue" and "tab:orange", as in the operator's figures.
DATA_COLOUR = "#1f77b4"
FIT_COLOUR = "#ff7f0e"
SECONDS_PER_DAY = 86400.0

MASERS = {
    "MHM2010": {"name": "MicroSemi Maser", "short": "MSM"},
    "T4": {"name": "T4 Maser", "short": "T4"},
}


# %% ===========================================================================
#    STATISTICS AND THE STRAIGHT-LINE FIT
# =============================================================================
@dataclass
class DeltaStats:
    mjd0: float
    x_days: np.ndarray
    y_ns: np.ndarray
    n: int
    mean_ns: float
    std_ns: float


@dataclass
class ReferenceFit(DeltaStats):
    a0: float = math.nan
    a1: float = math.nan
    sigma_a0: float = math.nan
    sigma_a1: float = math.nan
    rate: float = math.nan
    model_ns: np.ndarray | None = None
    residual_ns: np.ndarray | None = None
    residual_mean_ns: float = math.nan
    residual_std_ns: float = math.nan

    def as_dict(self) -> dict:
        return {"mjd0": self.mjd0, "num_points": self.n, "mean_ns": self.mean_ns, "std_ns": self.std_ns,
                "a0_ns": self.a0, "sigma_a0_ns": self.sigma_a0, "a1_ns_per_day": self.a1,
                "sigma_a1_ns_per_day": self.sigma_a1, "rate": self.rate,
                "residual_mean_ns": self.residual_mean_ns, "residual_std_ns": self.residual_std_ns,
                "x_definition": "MJD(UTC) - mjd0, days", "std_definition": "population (divisor N)",
                "sigma_definition": "IID OLS standard error, divisor N-2"}


def _clean(mjd, phase_ns) -> tuple[np.ndarray, np.ndarray]:
    mjd = np.asarray(mjd, float).ravel()
    phase_ns = np.asarray(phase_ns, float).ravel()
    if mjd.shape != phase_ns.shape:
        raise ValueError("MJD and delta arrays differ in length.")
    good = np.isfinite(mjd) & np.isfinite(phase_ns)
    order = np.argsort(mjd[good], kind="stable")
    return mjd[good][order], phase_ns[good][order]


def delta_stats(mjd, phase_ns, mjd0: float | None = None) -> DeltaStats:
    """Count, mean and population std of the deltas; x relative to MJD0."""
    mjd, y = _clean(mjd, phase_ns)
    if len(y) == 0:
        raise ValueError("No finite 1 PPS deltas to plot.")
    origin = float(mjd[0] if mjd0 is None else mjd0)
    return DeltaStats(origin, mjd - origin, y, len(y), float(np.mean(y)), float(np.std(y)))


def reference_linear_fit(mjd, phase_ns, mjd0: float | None = None) -> ReferenceFit:
    """Straight line y = a1*x + a0 with OLS standard errors (see module notes)."""
    base = delta_stats(mjd, phase_ns, mjd0)
    x, y, n = base.x_days, base.y_ns, base.n
    if n < 3:
        raise ValueError("A straight-line fit with uncertainties needs at least 3 points.")
    xbar, ybar = float(np.mean(x)), float(np.mean(y))
    sxx = float(np.sum((x - xbar) ** 2))
    if not sxx > 0:
        raise ValueError("All points share one time; the slope is undefined.")
    a1 = float(np.sum((x - xbar) * (y - ybar)) / sxx)
    a0 = ybar - a1 * xbar
    model = a0 + a1 * x
    residual = y - model
    s2 = float(np.sum(residual ** 2) / (n - 2))
    return ReferenceFit(base.mjd0, x, y, n, base.mean_ns, base.std_ns,
                        a0=a0, a1=a1, sigma_a0=math.sqrt(s2 * (1.0 / n + xbar ** 2 / sxx)),
                        sigma_a1=math.sqrt(s2 / sxx), rate=a1 * 1e-9 / SECONDS_PER_DAY,
                        model_ns=model, residual_ns=residual,
                        residual_mean_ns=float(np.mean(residual)), residual_std_ns=float(np.std(residual)))


def mhm2010_synth_counts(rate: float, dds_setting: float | None = None, sign: int = 1) -> float:
    """DDS least-significant-digit change that cancels ``rate`` (advisory)."""
    if sign not in (-1, 1):
        raise ValueError("sign must be +1 or -1")
    setting = 0.0 if dds_setting is None or not math.isfinite(dds_setting) else float(dds_setting)
    return sign * rate * (284.08 + 2e-7 * setting) / 2e-14


def t4_synth_change(rate: float, sign: int = 1, convention: str = "output") -> tuple[float, int, float]:
    """(SETFSREL value, synthesizer steps, synthesizer change in Hz) for a T4."""
    from maser_core import T4_SYNTH_SCALE_HZ, T4_SYNTH_STEP_HZ
    if sign not in (-1, 1) or convention not in ("output", "phase"):
        raise ValueError("invalid sign or convention")
    steps = int(np.rint(sign * rate * T4_SYNTH_SCALE_HZ / T4_SYNTH_STEP_HZ))
    change_hz = steps * T4_SYNTH_STEP_HZ
    realized_output = -change_hz / T4_SYNTH_SCALE_HZ
    return (realized_output if convention == "output" else -realized_output), steps, change_hz


def synth_text(maser: str, rate: float, eligible: bool, dds_setting: float | None = None,
               sign: int = 1, t4_convention: str = "output") -> str:
    """The 'synth adjustment' line for the fit box."""
    if not eligible:
        return "synth adjustment = n/a (not a maser/reference pair)"
    if maser == "MHM2010":
        return f"synth adjustment = {mhm2010_synth_counts(rate, dds_setting, sign):.0f} counts"
    value, steps, change_hz = t4_synth_change(rate, sign, t4_convention)
    return (f"synth adjustment = SETFSREL={value:+.3E} "
            f"({change_hz * 1e6:+.1f} µHz, {steps:+d} steps)")


def synth_value(maser: str, rate: float, eligible: bool, dds_setting: float | None = None,
                sign: int = 1, t4_convention: str = "output") -> dict | None:
    """Full-precision numbers behind the 'synth adjustment' line (0.0.2)."""
    if not eligible:
        return None
    if maser == "MHM2010":
        return {"basis": "straight-line rate (interval mean)", "dds_change_counts":
                mhm2010_synth_counts(rate, dds_setting, sign), "dds_setting_for_gain": dds_setting}
    value, steps, change_hz = t4_synth_change(rate, sign, t4_convention)
    return {"basis": "straight-line rate (interval mean)", "setfsrel": value, "synth_steps": steps,
            "synth_change_hz": change_hz, "setfsrel_convention": t4_convention}


@dataclass
class ReferenceSet:
    """Everything both reference figures need, computed once.

    0.0.2: ``suggestion`` is maser_core.suggested_settings() at the
    application epoch (both the frequency and the drift change) and
    ``suggestion_lines`` its box text; ``source`` and ``channel`` name the
    datasets (Ref1PPS_<channel>_...); ``synth`` holds the full-precision
    numbers of the straight-line 'synth adjustment' line.
    """
    raw: DeltaStats
    fit: ReferenceFit
    excluded: DeltaStats | None
    synth_line: str | None
    title: str
    ylabel: str
    fit_ylabel: str
    maser: str
    source: str = "Timing"
    channel: str = ""
    suggestion: dict | None = None
    suggestion_lines: list | None = None
    synth: dict | None = None

    @property
    def prefix(self) -> str:
        """Dataset/page prefix, e.g. 'Ref1PPS_MSM_GPS'."""
        return vname("Ref1PPS", self.channel or "channel")

    def as_dict(self) -> dict:
        return {"maser": self.maser, "title": self.title, "ylabel": self.ylabel,
                "fit_ylabel": self.fit_ylabel, "source": self.source, "channel": self.channel,
                "dataset_prefix": self.prefix,
                "deltas": {"mjd0": self.raw.mjd0, "num_points": self.raw.n, "mean_ns": self.raw.mean_ns,
                           "std_ns": self.raw.std_ns, "box": raw_box_lines(self.raw)},
                "fit": dict(self.fit.as_dict(), box=fit_box_lines(self.fit, self.synth_line),
                            residual_box=residual_box_lines(self.fit)),
                "synth_adjustment": self.synth,
                "suggested_settings": self.suggestion,
                "suggested_settings_box": list(self.suggestion_lines or []),
                "excluded_points": 0 if self.excluded is None else self.excluded.n}


def reference_set(mjd, phase_s, selected, used, maser: str, channel_label: str, eligible: bool,
                  dds_setting: float | None = None, sign: int = 1,
                  titles: dict[str, str] | None = None, t4_convention: str = "output",
                  suggestion: dict | None = None, source_kind: str = "Timing") -> ReferenceSet:
    """Build both figures from a series and the fit masks.

    The deltas figure shows every sample in the selected interval; the fit
    figure fits the samples actually used (exclusions removed) and draws
    the excluded ones hollow. Values are converted from seconds to ns.
    ``suggestion`` (0.0.2) is maser_core.suggested_settings(); its box is
    drawn only for an eligible maser/reference pair.
    """
    mjd = np.asarray(mjd, float)
    phase_ns = np.asarray(phase_s, float) * 1e9
    selected = np.asarray(selected, bool)
    used = np.asarray(used, bool)
    raw = delta_stats(mjd[selected], phase_ns[selected])
    fit = reference_linear_fit(mjd[used], phase_ns[used])
    dropped = selected & ~used
    excluded = delta_stats(mjd[dropped], phase_ns[dropped], fit.mjd0) if dropped.any() else None
    labels = dict(default_titles(maser, channel_label), **{k: v for k, v in (titles or {}).items() if v})
    shown = suggestion if (eligible and suggestion is not None) else None
    return ReferenceSet(raw, fit, excluded,
                        synth_text(maser, fit.rate, eligible, dds_setting, sign, t4_convention),
                        labels["title"], labels["ylabel"], labels["fit_ylabel"], maser,
                        source=str(source_kind), channel=str(channel_label), suggestion=shown,
                        suggestion_lines=suggestion_box_lines(shown) if shown else None,
                        synth=synth_value(maser, fit.rate, eligible, dds_setting, sign, t4_convention))


# %% ===========================================================================
#    SUGGESTED-SETTINGS BOX (0.0.2)
# =============================================================================
def epoch_text(mjd: float, zone=None) -> str:
    """'2026-07-13T02:29:08-04:00 (MJD 61234.93690)': local ISO 8601 and MJD."""
    local = iso_local_strings(mjd_to_epoch(np.array([float(mjd)])), zone)[0]
    return f"{local} (MJD {float(mjd):.5f})"


def suggestion_box_lines(suggestion: dict, zone=None) -> list[str]:
    """Box text for maser_core.suggested_settings(): frequency AND drift change.

    Display text only (rounded for reading); every value is in the
    suggestion dict, analysis.json, review.md and the Suggested_* datasets
    at full precision.
    """
    s = suggestion
    maser = s["maser"]
    lines = [f"suggested {maser} changes at {epoch_text(s['application_epoch_mjd_utc'], zone)}",
             f"from {s['model']}, {s['fitted_points']} points:",
             f"   y = {s['residual_fractional_frequency']:+.4e}"
             + (f",  drift = {s['residual_drift_per_day']:+.4e} /day" if s["residual_drift_per_day"] is not None
                else "")]
    f = s["frequency_setting"] or {}
    d = s["drift_setting"]
    if maser == "MHM2010":
        new = f" -> DDS {f['new_dds']}" if f.get("new_dds") else " (enter the current DDS for the new setting)"
        lines.append(f"DDS change = {f['dds_change_counts']:+.2f} ± {f['dds_change_counts_sigma']:.2f} counts{new}")
        if d is None:
            lines.append("drift change: not estimated (linear model; select Quadratic phase)")
        else:
            lines.append(f"drift change = {d['drift_change_pp18_per_day']:+.2f} ± "
                         f"{d['drift_change_pp18_per_day_sigma']:.2f} pp18/day"
                         f" = {d['drift_change_dds_counts_per_day']:+.4f} DDS counts/day")
            if d.get("new_drift_field") is not None:
                lines.append(f"   -> new drift field {d['new_drift_field']:+.2f} {d['drift_units']} "
                             f"(nearest integer {d['new_drift_field_nearest_integer']:+d})")
            else:
                lines.append("   new drift field = current field + change (current not entered)")
    else:
        command = f.get("setfsrel_command") or "none (below one synthesizer step)"
        lines.append(f"frequency: {command} ({f['synth_change_hz'] * 1e6:+.3f} µHz, {f['synth_steps']:+d} steps)")
        if d is None:
            lines.append("drift: not estimated (linear model; select Quadratic phase)")
        else:
            known = "" if d.get("current_known") else " (current DRIFTCOMP not read: 0 assumed)"
            lines.append(f"drift: DRIFTCOMP change {d['driftcomp_change_per_day']:+.3e} -> {d['command']}{known}")
            lines.append("   then REBOOT (restarts the M&C software); check with RDREG=DRIFTCOMP;")
    lines.append("ADVISORY ONLY - nothing is sent to the maser")
    return lines


#: Fraction of the fit graph height kept free above the data for the
#: suggested-settings box (0.0.2); the data use the lower part.
SUGGESTION_HEADROOM = 0.45


def headroom_limits(*arrays, top: float = SUGGESTION_HEADROOM, bottom: float = 0.05) -> tuple[float, float]:
    """(ymin, ymax) that leave ``top`` x span free above the data (for the box)
    and ``bottom`` x span below it. Veusz draws markers over a label, so the
    box gets its own space instead of covering points."""
    values = np.concatenate([np.asarray(a, float).ravel() for a in arrays if a is not None]) if arrays else []
    values = values[np.isfinite(values)] if len(values) else values
    if not len(values):
        return 0.0, 1.0
    low, high = float(values.min()), float(values.max())
    span = high - low if high > low else max(1.0, abs(high) * 1e-6)
    return low - bottom * span, high + top * span


# %% ===========================================================================
#    BOX TEXT (IDENTICAL IN GUI AND VEUSZ)
# =============================================================================
def mjd_axis_label(mjd0: float) -> str:
    return f"MJD - {mjd0:.5f}"


def raw_box_lines(stats: DeltaStats) -> list[str]:
    return [f"num points = {stats.n}", f"mean = {stats.mean_ns:.3f} ns   std = {stats.std_ns:.3f} ns"]


def fit_box_lines(fit: ReferenceFit, synth_line: str | None) -> list[str]:
    lines = [f"num points = {fit.n}", f"mean = {fit.mean_ns:.3f} ns   std = {fit.std_ns:.3f} ns", "",
             "results of fit to y = a1*x + a0:",
             f"   a0 = {fit.a0:.3f} ± {fit.sigma_a0:.3f} ns",
             f"   a1 = {fit.a1:.3f} ± {fit.sigma_a1:.3f} ns/day",
             f"rate = {fit.rate:.2e}"]
    if synth_line:
        lines += ["", synth_line]
    return lines


def residual_box_lines(fit: ReferenceFit) -> list[str]:
    return [f"mean = {fit.residual_mean_ns:.3f} ns   std = {fit.residual_std_ns:.3f} ns"]


def default_titles(maser: str, channel_label: str) -> dict[str, str]:
    """Editable defaults: figure title and the two y-axis labels."""
    name = MASERS.get(maser, MASERS["MHM2010"])["name"]
    return {"title": f"{name} 1PPS Deltas",
            "ylabel": f"{channel_label} (ns)",
            "fit_ylabel": f"{channel_label} (ns)"}


def padded_range(x: np.ndarray, fraction: float = 0.05) -> tuple[float, float]:
    """Data range widened by ``fraction`` of the span on each side."""
    low, high = float(np.min(x)), float(np.max(x))
    span = high - low if high > low else 1.0
    return low - fraction * span, high + fraction * span


# %% ===========================================================================
#    VEUSZ PAGES (PUBLIC VszDoc API ONLY; NO WRITER INTERNALS)
# =============================================================================
def _axes(doc: VszDoc, xlabel: str, ylabel: str, xrange: Sequence[float] | None,
          hide_x_labels: bool = False) -> None:
    doc.add("axis", "x")
    doc.add("axis", "y")
    doc.set("y/direction", "vertical")
    doc.set("x/label", vz_text(xlabel))
    doc.set("y/label", vz_text(ylabel))
    doc.set("y/autoRange", "+5%")
    if xrange is not None:
        doc.set("x/min", float(xrange[0]))
        doc.set("x/max", float(xrange[1]))
    for axis in ("x", "y"):
        doc.set(f"{axis}/MinorTicks/hide", True)
        doc.set(f"{axis}/GridLines/hide", True)
    if hide_x_labels:
        doc.set("x/TickLabels/hide", True)
        doc.set("x/Label/hide", True)


def _points(doc: VszDoc, name: str, x: str, y: str, colour: str, key: str = "",
            size: str = "2.2pt", hollow: bool = False) -> None:
    doc.add("xy", name)
    doc.set(f"{name}/xData", x)
    doc.set(f"{name}/yData", y)
    doc.set(f"{name}/marker", "circle")
    doc.set(f"{name}/markerSize", size)
    doc.set(f"{name}/PlotLine/hide", True)
    doc.set(f"{name}/MarkerFill/color", colour)
    doc.set(f"{name}/MarkerFill/hide", hollow)
    doc.set(f"{name}/MarkerLine/color", colour)
    doc.set(f"{name}/MarkerLine/hide", not hollow)
    if key:
        doc.set(f"{name}/key", vz_text(key))


def _line(doc: VszDoc, name: str, x: str, y: str, colour: str, key: str = "") -> None:
    doc.add("xy", name)
    doc.set(f"{name}/xData", x)
    doc.set(f"{name}/yData", y)
    doc.set(f"{name}/marker", "none")
    doc.set(f"{name}/PlotLine/color", colour)
    doc.set(f"{name}/PlotLine/width", "1.5pt")
    if key:
        doc.set(f"{name}/key", vz_text(key))


def _box(doc: VszDoc, name: str, lines: Sequence[str], corner: str, size: str = "9pt") -> None:
    """Bordered text box inside the current graph, at a graph corner."""
    horizontal, vertical = {"upper right": ("right", "top"), "upper left": ("left", "top"),
                            "lower left": ("left", "bottom"), "lower right": ("right", "bottom")}[corner]
    doc.add("label", name)
    doc.set(f"{name}/label", LINE_BREAK.join(vz_text(line) if line else " " for line in lines))
    doc.set(f"{name}/xPos", [0.985 if horizontal == "right" else 0.015])
    doc.set(f"{name}/yPos", [0.975 if vertical == "top" else 0.025])
    doc.set(f"{name}/alignHorz", horizontal)
    doc.set(f"{name}/alignVert", vertical)
    doc.set(f"{name}/Text/size", size)
    doc.set(f"{name}/margin", "3pt")
    doc.set(f"{name}/Border/hide", False)
    doc.set(f"{name}/Border/color", "#b0b0b0")
    doc.set(f"{name}/Background/hide", False)
    doc.set(f"{name}/Background/color", "white")


def _title(doc: VszDoc, text: str) -> None:
    doc.add("label", "title")
    doc.set("title/label", vz_text(text))
    doc.set("title/xPos", [0.5])
    doc.set("title/yPos", [0.975])
    doc.set("title/alignHorz", "centre")
    doc.set("title/alignVert", "top")
    doc.set("title/Text/size", "13pt")


def sans_serif_document(doc: VszDoc, font: str = "Arial") -> None:
    """Document-wide sans-serif font (matplotlib-like). Call at the root.

    "Arial" exists on Windows; fontconfig maps it to Liberation Sans or
    DejaVu Sans on Linux.
    """
    doc.set("StyleSheet/Font/font", font)


def reference_pages_from_set(writer: VeuszWriter, doc: VszDoc, prefix: str | None, ref: ReferenceSet,
                             local: bool = True, zone=None) -> list[str]:
    """reference_pages() for a ReferenceSet (MJD pages plus local-time twins).

    ``prefix`` None (0.0.2) uses ref.prefix, e.g. 'Ref1PPS_MSM_GPS'.
    """
    return reference_pages(writer, doc, prefix or ref.prefix, ref.raw, ref.fit, ref.title, ref.ylabel,
                           ref.fit_ylabel, ref.synth_line, ref.excluded, local=local, zone=zone,
                           source=ref.source, suggestion_lines=ref.suggestion_lines, ref=ref)


def _deltas_graph(doc: VszDoc, x: str, y: str, xlabel: str, ylabel: str, xrange, lines,
                  datetime_format: str = "") -> None:
    """The deltas graph (current widget: its page). Widgets: g_deltas, <y>, box_deltas_statistics."""
    doc.add("graph", "g_deltas")
    doc.to("g_deltas")
    for side, value in (("leftMargin", "2.6cm"), ("rightMargin", "0.6cm"),
                        ("topMargin", "1.3cm"), ("bottomMargin", "1.6cm")):
        doc.set(side, value)
    _axes(doc, xlabel, ylabel, xrange)
    if datetime_format:
        doc.set("x/mode", "datetime")
        doc.set("x/TickLabels/format", datetime_format)
        doc.set("x/autoRange", "+5%")
    _points(doc, y, x, y, DATA_COLOUR)
    _box(doc, "box_deltas_statistics", lines, "upper right", "10pt")
    doc.up()


def _fit_grid(doc: VszDoc, names: dict, x: str, xlabel: str, xrange, fit: ReferenceFit,
              fit_ylabel: str, synth_line, excluded_x: str | None, datetime_format: str = "",
              suggestion_lines=None, y_limits: tuple | None = None) -> None:
    """Data + fit over residuals (current widget: its page).

    Widgets (0.0.2): g_fit (excluded, data, fit line, key, box_fit_results,
    box_suggested_settings) over g_residual (residuals, box_residual_statistics).
    """
    doc.add("grid", "grid")
    doc.to("grid")
    doc.set("rows", 2)
    doc.set("columns", 1)
    doc.set("scaleRows", [3.0, 1.0])
    for side, value in (("leftMargin", "0cm"), ("rightMargin", "0cm"), ("topMargin", "0.4cm"),
                        ("bottomMargin", "0cm"), ("internalMargin", "0cm")):
        doc.set(side, value)
    for graph, top, bottom in (("g_fit", "0.6cm", "0cm"), ("g_residual", "0cm", "1.6cm")):
        doc.add("graph", graph)
        doc.to(graph)
        for side, value in (("leftMargin", "2.8cm"), ("rightMargin", "1.2cm"),
                            ("topMargin", top), ("bottomMargin", bottom)):
            doc.set(side, value)
        if graph == "g_fit":
            _axes(doc, "", fit_ylabel, xrange, hide_x_labels=True)
            if suggestion_lines and y_limits is not None:
                # Room above the data for the suggested-settings box (0.0.2).
                doc.set("y/min", float(y_limits[0]))
                doc.set("y/max", float(y_limits[1]))
        else:
            _axes(doc, xlabel, "Residuals (ns)", xrange)
        if datetime_format:
            doc.set("x/mode", "datetime")
            doc.set("x/TickLabels/format", datetime_format)
            doc.set("x/autoRange", "+5%")
        if graph == "g_fit":
            if excluded_x is not None:
                _points(doc, names["excl_y"], excluded_x, names["excl_y"], "#8c8c8c", "excluded", hollow=True)
            _points(doc, names["fit_y"], x, names["fit_y"], DATA_COLOUR, "data")
            _line(doc, names["model"], x, names["model"], FIT_COLOUR, "fit")
            doc.add("key", "key")
            doc.set("key/horzPosn", "right")
            doc.set("key/vertPosn", "top")
            doc.set("key/Border/color", "#b0b0b0")
            doc.set("key/Text/size", "10pt")
            _box(doc, "box_fit_results", fit_box_lines(fit, synth_line), "lower left", "10pt")
            if suggestion_lines:
                _box(doc, "box_suggested_settings", suggestion_lines, "upper left", "9pt")
        else:
            _points(doc, names["residual"], x, names["residual"], DATA_COLOUR)
            _box(doc, "box_residual_statistics", residual_box_lines(fit), "upper left", "10pt")
        doc.up()
    doc.up()


def _number_datasets(writer: VeuszWriter, prefix: str, source: str, raw: DeltaStats, fit: ReferenceFit,
                     ref: "ReferenceSet | None") -> None:
    """Every number of the boxes as a full-precision dataset (0.0.2), tagged type_fit_results."""
    numbers = {
        "deltas_MJD0": raw.mjd0, "deltas_num_points": raw.n, "deltas_mean_ns": raw.mean_ns,
        "deltas_std_ns": raw.std_ns, "fit_MJD0": fit.mjd0, "fit_num_points": fit.n, "fit_mean_ns": fit.mean_ns,
        "fit_std_ns": fit.std_ns, "fit_a0_ns": fit.a0, "fit_a0_sigma_ns": fit.sigma_a0,
        "fit_a1_ns_per_day": fit.a1, "fit_a1_sigma_ns_per_day": fit.sigma_a1, "fit_rate": fit.rate,
        "fit_residual_mean_ns": fit.residual_mean_ns, "fit_residual_std_ns": fit.residual_std_ns,
    }
    synth = ref.synth if ref is not None else None
    if synth:
        for key in ("dds_change_counts", "setfsrel", "synth_steps", "synth_change_hz"):
            if key in synth and synth[key] is not None:
                numbers[f"synth_adjustment_{key}"] = synth[key]
    for key, value in numbers.items():
        writer.dataset_1d(vname(prefix, key), [float(value)],
                          tags=tags("Workbench", "fit_results", "reference_figure"))
    lines = [f"{key} = {full(value)}" for key, value in numbers.items()]
    writer.dataset_text(vname(prefix, "results_full_precision_text"), lines,
                        tags=tags("Workbench", "fit_results", "text"))


def suggestion_datasets(writer: VeuszWriter, suggestion: dict | None, lines=None) -> list[str]:
    """Suggested settings as 1-element full-precision datasets (0.0.2).

    Names: Suggested_<maser>_<quantity>; plus Suggested_<maser>_box_text
    (the box) and Suggested_<maser>_full_precision_text (key = value lines).
    Tags: source_Workbench, type_suggested_settings.
    """
    if not suggestion:
        return []
    stem = vname("Suggested", suggestion["maser"])
    flat = {"application_epoch_MJD_UTC": suggestion["application_epoch_mjd_utc"],
            "model_order": suggestion["model_order"], "fitted_points": suggestion["fitted_points"],
            "residual_fractional_frequency": suggestion["residual_fractional_frequency"],
            "frequency_sigma": suggestion["frequency_sigma"],
            "residual_drift_per_day": suggestion["residual_drift_per_day"],
            "drift_sigma_per_day": suggestion["drift_sigma_per_day"]}
    for block in ("frequency_setting", "drift_setting"):
        for key, value in (suggestion.get(block) or {}).items():
            flat[key] = value
    names = []
    text = []
    for key, value in flat.items():
        text.append(f"{key} = {full(value) if isinstance(value, (int, float, np.number)) else value}")
        if key in ("current_dds", "new_dds") and isinstance(value, str):
            # The DDS text nnnn.nnnnnnn is exact (in the text dataset); the
            # numeric copy is the nearest float64, for plotting.
            name = vname(stem, key, "setting")
            writer.dataset_1d(name, [float(value)], tags=tags("Workbench", "suggested_settings"))
            names.append(name)
            continue
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.number)):
            continue
        name = vname(stem, key)
        writer.dataset_1d(name, [float(value)], tags=tags("Workbench", "suggested_settings"))
        names.append(name)
    writer.dataset_text(vname(stem, "full_precision_text"), text, tags=tags("Workbench", "suggested_settings",
                                                                           "text"))
    if lines:
        writer.dataset_text(vname(stem, "box_text"), list(lines), tags=tags("Workbench", "suggested_settings",
                                                                            "text"))
    return names


def reference_pages(writer: VeuszWriter, doc: VszDoc, prefix: str, raw: DeltaStats,
                    fit: ReferenceFit, title: str, ylabel: str, fit_ylabel: str,
                    synth_line: str | None, excluded: DeltaStats | None = None,
                    local: bool = True, zone=None, source: str = "Timing",
                    suggestion_lines=None, ref: "ReferenceSet | None" = None) -> list[str]:
    """Write the datasets and the reference pages; return page names.

    Pages "<prefix>_deltas_MJD" and "<prefix>_fit_MJD" use x = MJD - MJD0 in
    days, as in the operator's figures. With ``local`` (writer >= 0.0.24)
    the twins "<prefix>_deltas_local" and "<prefix>_fit_local" show the same
    data, fit and boxes against the processing computer's local date-time
    (ISO-8601 ticks, e.g. 2026-11-03T06:31:15-05:00; ``zone`` overrides).

    0.0.2 dataset names (prefix e.g. Ref1PPS_MSM_GPS):
        <prefix>_deltas_t_days, _deltas_ns, _deltas_t_local      figure 1
        <prefix>_fit_t_days, _fit_data_ns, _fit_line_ns,
        _fit_residual_ns, _fit_t_local                           figure 2
        <prefix>_excluded_t_days, _excluded_ns, _excluded_t_local
        <prefix>_<box number> (1 element each), _results_full_precision_text
    tagged source_<timing source> or source_Workbench and type_*.
    """
    src = source or "Timing"
    names = {"raw_x": vname(prefix, "deltas_t_days"), "raw_y": vname(prefix, "deltas_ns"),
             "fit_x": vname(prefix, "fit_t_days"), "fit_y": vname(prefix, "fit_data_ns"),
             "model": vname(prefix, "fit_line_ns"), "residual": vname(prefix, "fit_residual_ns"),
             "excl_x": vname(prefix, "excluded_t_days"), "excl_y": vname(prefix, "excluded_ns"),
             "raw_local": vname(prefix, "deltas_t_local"), "fit_local": vname(prefix, "fit_t_local"),
             "excl_local": vname(prefix, "excluded_t_local")}
    writer.dataset_1d(names["raw_x"], raw.x_days, tags=tags(src, "time_days_since_MJD0", "reference_figure"))
    writer.dataset_1d(names["raw_y"], raw.y_ns, tags=tags(src, "measurement", "reference_figure"))
    writer.dataset_1d(names["fit_x"], fit.x_days, tags=tags(src, "time_days_since_MJD0", "reference_figure"))
    writer.dataset_1d(names["fit_y"], fit.y_ns, tags=tags(src, "measurement", "reference_figure"))
    writer.dataset_1d(names["model"], fit.model_ns, tags=tags("Workbench", "fit_model", "reference_figure"))
    writer.dataset_1d(names["residual"], fit.residual_ns, tags=tags("Workbench", "fit_residual", "reference_figure"))
    has_excluded = excluded is not None and excluded.n > 0
    if has_excluded:
        writer.dataset_1d(names["excl_x"], excluded.x_days, tags=tags(src, "time_days_since_MJD0", "excluded"))
        writer.dataset_1d(names["excl_y"], excluded.y_ns, tags=tags(src, "measurement", "excluded"))
    _number_datasets(writer, prefix, src, raw, fit, ref)
    y_limits = headroom_limits(fit.y_ns, fit.model_ns, excluded.y_ns if has_excluded else None)
    pages = []
    # Page 1: deltas, matplotlib-like 6.4 x 4.8 inch proportions.
    variants = [("_MJD", raw.x_days, fit.x_days, None)]
    if local:
        raw_axis = local_time_axis(mjd_to_epoch(raw.mjd0 + raw.x_days), zone)
        fit_axis = local_time_axis(mjd_to_epoch(fit.mjd0 + fit.x_days), zone)
        writer.dataset_date(names["raw_local"], raw_axis.veusz_seconds, tags=tags(src, "time_local",
                                                                                  "reference_figure"))
        writer.dataset_date(names["fit_local"], fit_axis.veusz_seconds, tags=tags(src, "time_local",
                                                                                  "reference_figure"))
        if has_excluded:
            writer.dataset_date(names["excl_local"],
                                local_time_axis(mjd_to_epoch(excluded.mjd0 + excluded.x_days), zone).veusz_seconds,
                                tags=tags(src, "time_local", "excluded"))
        variants.append(("_local", raw_axis, fit_axis, True))
    for suffix, raw_x, fit_x, is_local in variants:
        page = f"{prefix}_deltas{suffix}"
        doc.add("page", page)
        doc.to(page)
        doc.set("width", "16.3cm" if not is_local else "20cm")
        doc.set("height", "12.2cm")
        _title(doc, title)
        if is_local:
            _deltas_graph(doc, names["raw_local"], names["raw_y"], raw_x.label, ylabel, None,
                          raw_box_lines(raw), raw_x.datetime_format)
        else:
            _deltas_graph(doc, names["raw_x"], names["raw_y"], mjd_axis_label(raw.mjd0), ylabel,
                          padded_range(raw.x_days), raw_box_lines(raw))
        doc.up()
        pages.append(page)
        page = f"{prefix}_fit{suffix}"
        doc.add("page", page)
        doc.to(page)
        doc.set("width", "24cm")
        doc.set("height", "18.4cm")
        if is_local:
            _fit_grid(doc, names, names["fit_local"], fit_x.label, None, fit, fit_ylabel, synth_line,
                      names["excl_local"] if has_excluded else None, fit_x.datetime_format,
                      suggestion_lines, y_limits)
        else:
            _fit_grid(doc, names, names["fit_x"], mjd_axis_label(fit.mjd0), padded_range(fit.x_days), fit,
                      fit_ylabel, synth_line, names["excl_x"] if has_excluded else None, "",
                      suggestion_lines, y_limits)
        doc.up()
        pages.append(page)
    return pages
