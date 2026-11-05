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
 Version        : 0.0.1 (Semantic Versioning 2.0.0; first internal draft)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-03
 Modified       : 2026-11-03
 Language       : Python 3.12
 Requires       : numpy; vszh5_writer >= 0.0.24 (VszDoc, VeuszWriter, vz_text,
                  local_time_axis, mjd_to_epoch)
 Licence        : GPL-3.0-or-later; see LICENSE
 Revision       : 0.0.1 Statistics, straight-line fit with OLS standard
                  errors, rate, synthesizer adjustment and the two page
                  layouts the operator supplied as images on 2026-11-03;
                  local ISO-8601 date-time twins of both pages (writer
                  0.0.24), requested by the operator on 2026-11-03.
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

from vszh5_writer import VszDoc, VeuszWriter, vz_text, local_time_axis, mjd_to_epoch

__version__ = "0.0.1"

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


@dataclass
class ReferenceSet:
    """Everything both reference figures need, computed once."""
    raw: DeltaStats
    fit: ReferenceFit
    excluded: DeltaStats | None
    synth_line: str | None
    title: str
    ylabel: str
    fit_ylabel: str
    maser: str

    def as_dict(self) -> dict:
        return {"maser": self.maser, "title": self.title, "ylabel": self.ylabel,
                "fit_ylabel": self.fit_ylabel,
                "deltas": {"mjd0": self.raw.mjd0, "num_points": self.raw.n, "mean_ns": self.raw.mean_ns,
                           "std_ns": self.raw.std_ns, "box": raw_box_lines(self.raw)},
                "fit": dict(self.fit.as_dict(), box=fit_box_lines(self.fit, self.synth_line),
                            residual_box=residual_box_lines(self.fit)),
                "excluded_points": 0 if self.excluded is None else self.excluded.n}


def reference_set(mjd, phase_s, selected, used, maser: str, channel_label: str, eligible: bool,
                  dds_setting: float | None = None, sign: int = 1,
                  titles: dict[str, str] | None = None, t4_convention: str = "output") -> ReferenceSet:
    """Build both figures from a series and the fit masks.

    The deltas figure shows every sample in the selected interval; the fit
    figure fits the samples actually used (exclusions removed) and draws
    the excluded ones hollow. Values are converted from seconds to ns.
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
    return ReferenceSet(raw, fit, excluded,
                        synth_text(maser, fit.rate, eligible, dds_setting, sign, t4_convention),
                        labels["title"], labels["ylabel"], labels["fit_ylabel"], maser)


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


def reference_pages_from_set(writer: VeuszWriter, doc: VszDoc, prefix: str, ref: ReferenceSet,
                             local: bool = True, zone=None) -> list[str]:
    """reference_pages() for a ReferenceSet (MJD pages plus local-time twins)."""
    return reference_pages(writer, doc, prefix, ref.raw, ref.fit, ref.title, ref.ylabel,
                           ref.fit_ylabel, ref.synth_line, ref.excluded, local=local, zone=zone)


def _deltas_graph(doc: VszDoc, x: str, y: str, xlabel: str, ylabel: str, xrange, lines,
                  datetime_format: str = "") -> None:
    """The deltas graph (current widget: its page)."""
    doc.add("graph", "graph")
    doc.to("graph")
    for side, value in (("leftMargin", "2.3cm"), ("rightMargin", "0.6cm"),
                        ("topMargin", "1.3cm"), ("bottomMargin", "1.6cm")):
        doc.set(side, value)
    _axes(doc, xlabel, ylabel, xrange)
    if datetime_format:
        doc.set("x/mode", "datetime")
        doc.set("x/TickLabels/format", datetime_format)
        doc.set("x/autoRange", "+5%")
    _points(doc, "data", x, y, DATA_COLOUR)
    _box(doc, "stats", lines, "upper right", "10pt")
    doc.up()


def _fit_grid(doc: VszDoc, names: dict, x: str, xlabel: str, xrange, fit: ReferenceFit,
              fit_ylabel: str, synth_line, excluded_x: str | None, datetime_format: str = "") -> None:
    """Data + fit over residuals (current widget: its page)."""
    doc.add("grid", "grid")
    doc.to("grid")
    doc.set("rows", 2)
    doc.set("columns", 1)
    doc.set("scaleRows", [3.0, 1.0])
    for side, value in (("leftMargin", "0cm"), ("rightMargin", "0cm"), ("topMargin", "0.4cm"),
                        ("bottomMargin", "0cm"), ("internalMargin", "0cm")):
        doc.set(side, value)
    for graph, top, bottom in (("fitgraph", "0.6cm", "0cm"), ("residgraph", "0cm", "1.6cm")):
        doc.add("graph", graph)
        doc.to(graph)
        for side, value in (("leftMargin", "2.6cm"), ("rightMargin", "1.2cm"),
                            ("topMargin", top), ("bottomMargin", bottom)):
            doc.set(side, value)
        if graph == "fitgraph":
            _axes(doc, "", fit_ylabel, xrange, hide_x_labels=True)
        else:
            _axes(doc, xlabel, "Residuals (ns)", xrange)
        if datetime_format:
            doc.set("x/mode", "datetime")
            doc.set("x/TickLabels/format", datetime_format)
            doc.set("x/autoRange", "+5%")
        if graph == "fitgraph":
            if excluded_x is not None:
                _points(doc, "excluded", excluded_x, names["excl_y"], "#8c8c8c", "excluded", hollow=True)
            _points(doc, "data", x, names["fit_y"], DATA_COLOUR, "data")
            _line(doc, "fit", x, names["model"], FIT_COLOUR, "fit")
            doc.add("key", "key")
            doc.set("key/horzPosn", "right")
            doc.set("key/vertPosn", "top")
            doc.set("key/Border/color", "#b0b0b0")
            doc.set("key/Text/size", "10pt")
            _box(doc, "results", fit_box_lines(fit, synth_line), "lower left", "10pt")
        else:
            _points(doc, "residual", x, names["residual"], DATA_COLOUR)
            _box(doc, "stats", residual_box_lines(fit), "upper left", "10pt")
        doc.up()
    doc.up()


def reference_pages(writer: VeuszWriter, doc: VszDoc, prefix: str, raw: DeltaStats,
                    fit: ReferenceFit, title: str, ylabel: str, fit_ylabel: str,
                    synth_line: str | None, excluded: DeltaStats | None = None,
                    local: bool = True, zone=None) -> list[str]:
    """Write the datasets and the reference pages; return page names.

    Pages "<prefix>_deltas" and "<prefix>_fit" use x = MJD - MJD0 in days, as
    in the operator's figures. With ``local`` (0.2.0, writer >= 0.0.24) the
    twins "<prefix>_deltas_local" and "<prefix>_fit_local" show the same
    data, fit and boxes against the processing computer's local date-time
    (ISO-8601 ticks, e.g. 2026-11-03T06:31:15-05:00; ``zone`` overrides).
    """
    names = {key: f"{prefix}_{key}" for key in ("raw_x", "raw_y", "fit_x", "fit_y", "model", "residual",
                                                  "excl_x", "excl_y", "raw_local", "fit_local",
                                                  "excl_local")}
    writer.dataset_1d(names["raw_x"], raw.x_days)
    writer.dataset_1d(names["raw_y"], raw.y_ns)
    writer.dataset_1d(names["fit_x"], fit.x_days)
    writer.dataset_1d(names["fit_y"], fit.y_ns)
    writer.dataset_1d(names["model"], fit.model_ns)
    writer.dataset_1d(names["residual"], fit.residual_ns)
    has_excluded = excluded is not None and excluded.n > 0
    if has_excluded:
        writer.dataset_1d(names["excl_x"], excluded.x_days)
        writer.dataset_1d(names["excl_y"], excluded.y_ns)
    pages = []
    # Page 1: deltas, matplotlib-like 6.4 x 4.8 inch proportions.
    variants = [("", raw.x_days, fit.x_days, None)]
    if local:
        raw_axis = local_time_axis(mjd_to_epoch(raw.mjd0 + raw.x_days), zone)
        fit_axis = local_time_axis(mjd_to_epoch(fit.mjd0 + fit.x_days), zone)
        writer.dataset_date(names["raw_local"], raw_axis.veusz_seconds)
        writer.dataset_date(names["fit_local"], fit_axis.veusz_seconds)
        if has_excluded:
            writer.dataset_date(names["excl_local"],
                                local_time_axis(mjd_to_epoch(excluded.mjd0 + excluded.x_days), zone).veusz_seconds)
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
                      names["excl_local"] if has_excluded else None, fit_x.datetime_format)
        else:
            _fit_grid(doc, names, names["fit_x"], mjd_axis_label(fit.mjd0), padded_range(fit.x_days), fit,
                      fit_ylabel, synth_line, names["excl_x"] if has_excluded else None)
        doc.up()
        pages.append(page)
    return pages
