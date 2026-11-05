#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Auditable exports for MHM2010 Correction Workbench.

===============================================================================
 Title          : Maser Results and Stable32 Export
 Filename       : maser_export.py
 Version        : 0.2.1 (Semantic Versioning 2.0.0)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-03
 Modified       : 2026-11-05
 Language       : Python 3.12
 Requires       : numpy, h5py, bundled vszh5_writer; maser_plots; t4_protocol
 Licence        : GPL-3.0-or-later; see LICENSE
 Revision       : 0.1.0 Atomic results directory, both drift units, native HDF5.
                  0.2.0 Reference-style 1 PPS pages first in every .vszh5
                        (deltas with statistics box; data + fit over
                        residuals); maser identity (MHM2010/T4); T4 advisory;
                        T4 telemetry pages, decoded CSV with raw codes, and a
                        separate T4-only export.
                  0.2.1 T4 native monitoring files (t4_native): pages per
                        unit and a status page against MJD and local time
                        (clock-valid records only), every record in CSV with
                        its clock flag, a notes page; export_t4_only accepts
                        MONIT telemetry, native logs or both. Review
                        reference [5] commit corrected to c72244d.
===============================================================================
Stable32 format follows W. J. Riley, Stable32 User Manual, ver. 1.54,
2008, http://www.stable32.com/Manual154.pdf .
"""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import json
import shutil
import tempfile
import textwrap
import numpy as np
from maser_core import PhaseFit, uniform_samples
from maser_io import TimingSeries, Telemetry, TimingCollection, overview_series, T4Telemetry
from maser_plots import ReferenceSet, reference_pages_from_set, sans_serif_document
from vszh5_writer import (VeuszWriter, VszDoc, Trace, Panel, xy_page, grid_page, local_time_axis,
                          mjd_to_epoch, time_pair_pages, iso_local_strings, text_page,
                          __version__ as WRITER_VERSION)

__version__ = "0.2.1"
APPLICATION = "MHM2010 / T4 Maser Timing Workbench"


# %% ===========================================================================
#    SERIALIZATION AND STABLE32
# =============================================================================
def json_safe(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(type(value).__name__)


def write_stable32(directory: Path, mjd, phase_s, prefix="raw", tolerance=0.01):
    """Export only complete uniform samples. Real zeros become 1e-99.

    Single-column PHD/FRD avoids automatic tag-unit ambiguity. A separate
    MJD+phase CSV retains original timestamps. Frequency is averaged over
    adjacent phase intervals, so there is one fewer frequency point.
    """
    phase, tau0, error = uniform_samples(mjd, phase_s, tolerance)
    frequency = np.diff(phase)/tau0
    metadata = {"tau0_s": tau0, "grid_error_s": error, "phase_count": len(phase),
                "frequency_count": len(frequency), "timetags": "separate CSV",
                "zero_policy": "Actual zero replaced by 1e-99, never interpreted as missing.",
                "units": {"PHD": "phase seconds", "FRD": "dimensionless fractional frequency"}}
    for extension, kind, values in (("PHD", "Phase", phase), ("FRD", "Frequency", frequency)):
        data = values.copy()
        metadata[f"{extension}_zero_replacements"] = int(np.sum(data == 0))
        data[data == 0] = 1e-99
        target = directory / f"{prefix}.{extension}"
        with target.open("w", encoding="ascii", newline="") as handle:
            handle.write(f"File: {target.name}\r\nType: {kind}\r\n"
                         f"Points: {len(data)}\r\nTau: {tau0:.15e}\r\n")
            for value in data:
                handle.write(f"{value:.16e}\r\n")
    (directory/f"{prefix}_stable32.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    np.savetxt(directory/f"{prefix}_timetags.csv", np.c_[mjd, phase], delimiter=",",
               header="mjd_utc,phase_s", comments="", fmt=["%.12f", "%.16e"])
    return metadata


# %% ===========================================================================
#    T4 TELEMETRY PAGES (0.2.0)
# =============================================================================
def t4_summary(t4: T4Telemetry) -> dict:
    """Provenance and coverage of loaded T4 telemetry for analysis.json."""
    return {"frames": int(len(t4.mjd)), "first_mjd": float(t4.mjd.min()), "last_mjd": float(t4.mjd.max()),
            "schema_version": t4.schema.version, "schema_sha256": t4.schema.sha256,
            "schema_status": t4.schema.status, "rejected": t4.rejected,
            "provenance": t4.provenance, "notes": t4.notes,
            "clock": "host receive time (UTC); not a maser timestamp"}


def _local_axis_dataset(writer: VeuszWriter, name: str, mjd, zone=None):
    """Write a local-time date dataset for MJD values; return its LocalTimeAxis."""
    axis = local_time_axis(mjd_to_epoch(np.asarray(mjd, float)), zone)
    writer.dataset_date(name, axis.veusz_seconds)
    return axis


def t4_pages(writer: VeuszWriter, document: VszDoc, t4: T4Telemetry, prefix: str = "t4",
             zone=None) -> list[str]:
    """Per functional group, one panel per unit, against MJD and local time.

    Channels without an engineering value ("Not used" in Table 6) are not
    plotted; their raw codes are in the CSV. The main-PLL lock bit gets its
    own pair of pages. Times are host receive times (UTC), not maser stamps.
    """
    writer.dataset_1d(f"{prefix}_mjd", t4.mjd)
    axis = _local_axis_dataset(writer, f"{prefix}_local", t4.mjd, zone)
    pages = []
    for channel in t4.schema.channels:
        if channel.available or channel.is_lock:
            writer.dataset_1d(f"{prefix}_{channel.short_name}", t4.values[:, channel.index])
    for number, group in enumerate(t4.schema.groups):
        members = [c for c in t4.schema.channels if c.group == group and c.available]
        units = list(dict.fromkeys(c.unit for c in members))
        if not units:
            continue
        for suffix, x, xlabel, xmode, fmt in (
                ("_mjd", f"{prefix}_mjd", "MJD (UTC, host receive time)", "numeric", ""),
                ("_local", f"{prefix}_local", axis.label + ", host receive time", "datetime",
                 axis.datetime_format)):
            panels = [Panel([Trace(x, f"{prefix}_{c.short_name}", f"{c.index + 1}: {c.label}")
                             for c in members if c.unit == unit],
                            xlabel=xlabel, ylabel=unit, title=f"{group}: {unit}", xmode=xmode,
                            x_autorange="exact" if xmode == "datetime" else "next-tick",
                            datetime_format=fmt)
                      for unit in units]
            name = f"{prefix}_group{number:02d}{suffix}"
            grid_page(document, name, panels, title=f"T4 telemetry (T4S-MAN-0012 Table 6 scaling): {group}",
                      columns=1 if len(panels) == 1 else 2)
            pages.append(name)
    lock = [c for c in t4.schema.channels if c.is_lock]
    if lock:
        pages += time_pair_pages(document, f"{prefix}_lock",
                                 [Trace(f"{prefix}_mjd", f"{prefix}_{c.short_name}", "Main PLL lock (1 = locked)",
                                        marker="circle", line=False) for c in lock],
                                 f"{prefix}_mjd", f"{prefix}_local", axis,
                                 title="T4 main PLL lock status (channel 41)", ylabel="Lock bit",
                                 mjd_label="MJD (UTC, host receive time)")
    return pages


# %% ===========================================================================
#    T4 NATIVE MONITORING FILES (0.2.1)
# =============================================================================
def _native_prefix(log, number: int) -> str:
    """Dataset prefix, e.g. 't4log0_monithcb'."""
    return f"t4log{number}_" + "".join(ch for ch in log.name.lower() if ch.isalnum())


def _padded_range(values) -> dict:
    """ymin/ymax with 10 % padding; a constant series gets +/-0.5 (or 1 %).

    Veusz's automatic range puts a constant series on the frame edge.
    """
    values = np.asarray(values, float)
    values = values[np.isfinite(values)]
    if not len(values):
        return {}
    low, high = float(values.min()), float(values.max())
    pad = 0.1 * (high - low) if high > low else max(0.5, 0.01 * abs(high))
    return {"ymin": low - pad, "ymax": high + pad}


def t4_native_pages(writer: VeuszWriter, document: VszDoc, log, prefix: str, zone=None) -> list[str]:
    """Pages for one T4 monitoring log: one per unit, one for status columns.

    Only clock-valid records are plotted (records stamped before the
    time-server sync would put 2006 and 2026 on one axis). Every record,
    with its clock flag, is in the CSV. Time stamps are the maser's own UTC
    times, so these pages may be compared with timing data directly.
    """
    mjd = log.valid_mjd
    if len(mjd) == 0:
        return []
    writer.dataset_1d(f"{prefix}_mjd", mjd)
    # Short logs: plot against MJD - MJD0 (as in the reference 1 PPS figures),
    # so the axis is readable; the absolute MJD dataset is also written.
    mjd0 = float(np.floor(mjd[0]))
    writer.dataset_1d(f"{prefix}_mjd_rel", mjd - mjd0)
    axis = _local_axis_dataset(writer, f"{prefix}_local", mjd, zone)
    epoch = mjd_to_epoch(mjd)
    writer.dataset_text(f"{prefix}_utc_iso",
                        [f"{np.datetime_as_string(t, unit='s')}Z" for t in log.utc[log.clock_valid]])
    writer.dataset_text(f"{prefix}_local_iso", iso_local_strings(epoch, zone))
    pages: list[str] = []
    mjd_label = f"MJD - {mjd0:.0f} (UTC, maser time stamps)"
    for column in log.numeric_columns():
        writer.dataset_1d(f"{prefix}_{column.key}", log.valid(column.key))
    units = list(dict.fromkeys(c.unit for c in log.numeric_columns()))
    for number, unit in enumerate(units):
        # One panel per column so that each is scaled on its own (an unused
        # sensor reading a constant 80 must not flatten a 21.5 set point).
        members = [c for c in log.numeric_columns() if c.unit == unit]
        for suffix, x, xlabel, xmode, fmt in (
                ("_mjd", f"{prefix}_mjd_rel", mjd_label, "numeric", ""),
                ("_local", f"{prefix}_local", axis.label, "datetime", axis.datetime_format)):
            panels = [Panel([Trace(x, f"{prefix}_{c.key}", c.label, marker="circle", line=True)],
                            xlabel=xlabel, ylabel=unit or "value", title=c.header, xmode=xmode,
                            x_autorange="exact" if xmode == "datetime" else "next-tick",
                            datetime_format=fmt, key=False, **_padded_range(log.valid(c.key)))
                      for c in members]
            name = f"{prefix}_unit{number}{suffix}"
            grid_page(document, name, panels, title=f"T4 {log.name}: {unit or 'no unit'}", columns=1,
                      height=f"{max(15, 6 * len(panels))}cm")
            pages.append(name)
    status = log.text_columns()
    for column in status:
        writer.dataset_1d(f"{prefix}_{column.key}_flag", log.status_flag(column.key)[log.clock_valid])
        writer.dataset_text(f"{prefix}_{column.key}_text", [str(v) for v in log.valid(column.key)])
    if status:
        traces = [Trace(f"{prefix}_mjd", f"{prefix}_{c.key}_flag", f"{c.label} (0 = No error)",
                        marker="circle", line=False) for c in status]
        pages += time_pair_pages(document, f"{prefix}_status", traces, f"{prefix}_mjd_rel", f"{prefix}_local",
                                 axis, title=f"T4 {log.name}: status (text kept in dataset "
                                             f"{prefix}_<column>_text)",
                                 ylabel="Status flag", mjd_label=mjd_label, ymin=-0.2, ymax=1.2)
    stats = log.stats
    lines = [f"T4 monitoring log {log.name} (reader t4_native; maser UTC time stamps)", "",
             f"Records: {stats['records']}; clock-valid: {stats['clock_valid']}; "
             f"clock not set: {stats['clock_not_set']}",
             f"Clock-valid span: {stats.get('first_utc', '-')} to {stats.get('last_utc', '-')}"]
    if "interval_s" in stats:
        interval = stats["interval_s"]
        lines.append(f"Record interval: median {interval['median']:.0f} s, mean {interval['mean']:.2f} s, "
                     f"range {interval['min']:.0f} to {interval['max']:.0f} s")
    for segment in stats.get("clock_not_set_segments", []):
        lines.append(f"Clock not set: {segment['count']} record(s) from {segment['first']}, stamped "
                     f"{segment['stamped_from']} to {segment['stamped_to']}")
    lines += ["", "Notes:", *[f"- {note}" for note in log.notes], "", "Columns:"]
    for header, meaning in stats.get("column_meaning", {}).items():
        lines.append(f"- {header}: {meaning}")
    lines += ["", "Files:", *[f"- {p['name']}  {p['bytes']} bytes  sha256 {p['sha256'][:16]}..."
                              for p in log.provenance],
              "", "[1] T4Science SA, T4S-MAN-0015, ver. 1.17, Jul. 19, 2022."]
    wrapped = []
    for line in lines:                      # text_page does not wrap
        wrapped += textwrap.wrap(line, 100, subsequent_indent="  ") or [""]
    pages += text_page(document, f"{prefix}_notes", f"T4 {log.name}: provenance and notes", wrapped)
    return pages


def write_native_csv(directory: Path, log, number: int) -> Path:
    """Every record of one log, with clock flag and source line (UTF-8 with BOM for Excel)."""
    target = directory / f"t4_{log.name.replace('+', '_')}_records.csv"
    if target.exists():
        target = directory / f"t4_{log.name.replace('+', '_')}_{number}_records.csv"
    log.frame().to_csv(target, index=False, encoding="utf-8-sig", float_format="%.6g")
    return target


def export_t4_only(destination: str | Path, t4: T4Telemetry | None = None, native=()) -> Path:
    """New folder with T4 MONIT telemetry and/or native monitoring logs.

    MONIT: decoded CSV (values + raw codes). Native logs: every record with
    its clock flag. Both: t4_telemetry.json and t4_telemetry.vszh5.
    """
    native = list(native or ())
    if t4 is None and not native:
        raise ValueError("Nothing to export: load T4 telemetry or T4 monitoring files first.")
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError("Choose a new results directory; existing results are never overwritten.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".t4-export-", dir=destination.parent))
    try:
        if t4 is not None:
            t4.frame().assign(**{f"{c.short_name}_code": t4.codes[:, c.index] for c in t4.schema.channels}
                              ).to_csv(temporary/"t4_telemetry_decoded.csv", index=False, float_format="%.6g")
        csv_names = [write_native_csv(temporary, log, number).name for number, log in enumerate(native)]
        summary = {"application": APPLICATION, "version": __version__,
                   "created_utc": datetime.now(timezone.utc).isoformat(),
                   "monit": None if t4 is None else t4_summary(t4),
                   "native": [dict(log.summary(), csv=name) for log, name in zip(native, csv_names, strict=True)]}
        if t4 is not None:
            summary.update(t4_summary(t4))          # 0.2.0 keys kept at the top level
        (temporary/"t4_telemetry.json").write_text(json.dumps(summary, indent=2, default=json_safe),
                                                   encoding="utf-8")
        document = VszDoc(generator=f"{APPLICATION} {__version__}")
        sans_serif_document(document)
        with VeuszWriter(temporary/"t4_telemetry.vszh5") as writer:
            for number, log in enumerate(native):
                t4_native_pages(writer, document, log, _native_prefix(log, number))
            if t4 is not None:
                t4_pages(writer, document, t4)
            writer.finish(document.text())
        temporary.rename(destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination


# %% ===========================================================================
#    COMPLETE ANALYSIS BUNDLE
# =============================================================================
def export_bundle(destination: str | Path, series: TimingSeries, fit: PhaseFit,
                  correction_result=None, telemetry: Telemetry | None=None,
                  stability_result=None, settings=None, stable32=False,
                  stable32_tolerance=0.01, collection: TimingCollection | None=None,
                  maser: str = "MHM2010", reference: ReferenceSet | None = None,
                  t4: T4Telemetry | None = None, extra_overview: tuple[str, ...] = (),
                  t4_native=()):
    """Write a NEW results directory or fail without replacing existing data.

    0.2.0: ``reference`` adds the two reference-style 1 PPS pages as the
    first pages of analysis.vszh5; ``maser`` records the maser under test;
    ``t4`` adds T4 telemetry datasets/pages and t4_telemetry_decoded.csv.
    0.2.1: ``t4_native`` (T4 monitoring logs) adds their pages and CSVs.
    """
    t4_native = list(t4_native or ())
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError("Choose a new results directory; existing results are never overwritten.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".mhm-export-", dir=destination.parent))
    try:
        payload = {
            "application": APPLICATION, "version": __version__, "maser": maser,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "series": {"label": series.label, "kind": series.source_kind,
                       "provenance": series.provenance, "notes": series.notes},
            "fit": {key: value for key, value in asdict(fit).items()
                    if key not in {"selected", "used", "residual_s", "predicted_s"}},
            "selected_count": int(fit.selected.sum()), "fit_count": int(fit.used.sum()),
            "correction": correction_result, "settings": settings or {},
            "stability": stability_result,
            "telemetry": None if telemetry is None else {
                "provenance": telemetry.provenance, "notes": telemetry.notes,
                "first_mjd": float(telemetry.frame.MJD.min()),
                "last_mjd": float(telemetry.frame.MJD.max())},
            "reference_plots": None if reference is None else reference.as_dict(),
            "t4_telemetry": None if t4 is None else t4_summary(t4),
            "t4_native": [log.summary() for log in t4_native],
        }
        np.savetxt(temporary/"phase_and_fit.csv",
                   np.c_[series.mjd, series.phase_s, fit.predicted_s, fit.residual_s,
                         fit.selected.astype(int), fit.used.astype(int)], delimiter=",",
                   header="mjd_utc,phase_s,model_s,residual_s,selected,used_in_fit",
                   comments="", fmt=["%.12f", "%.16e", "%.16e", "%.16e", "%d", "%d"])
        if stable32:
            # Do not compress excluded samples together: the validator will
            # reject an internal hole rather than change the sampling interval.
            selected = fit.used
            payload["stable32_raw"] = write_stable32(temporary, series.mjd[selected],
                series.phase_s[selected], "raw", stable32_tolerance)
            payload["stable32_residual"] = write_stable32(temporary, series.mjd[selected],
                fit.residual_s[selected], "fit_residual", stable32_tolerance)
        (temporary/"analysis.json").write_text(
            json.dumps(payload, indent=2, default=json_safe, allow_nan=False), encoding="utf-8")
        if stability_result:
            with (temporary/"stability.csv").open("w", encoding="utf-8") as handle:
                handle.write("estimator,tau_s,deviation,overlapping_terms\n")
                for name, result in stability_result["estimators"].items():
                    for tau, value, count in zip(result["tau_s"], result["deviation"], result["terms"]):
                        handle.write(f"{name},{tau:.12g},{value:.16e},{count}\n")
        document = VszDoc(generator=f"{APPLICATION} {__version__}")
        sans_serif_document(document)
        with VeuszWriter(temporary/"analysis.vszh5") as writer:
            if reference is not None:
                # The operator's reference figures come first in the document.
                reference_pages_from_set(writer, document, "pps", reference)
            writer.dataset_1d("mjd_utc", series.mjd)
            writer.dataset_date("utc", (series.mjd-54832.0)*86400.0)
            local_axis = _local_axis_dataset(writer, "local_time", series.mjd)
            writer.dataset_text("local_time_iso", iso_local_strings(mjd_to_epoch(series.mjd)))
            writer.dataset_1d("phase_ns", series.phase_s*1e9)
            # Model/residual are only drawn inside the chosen interval.
            writer.dataset_1d("fit_ns", np.where(fit.selected, fit.predicted_s*1e9, np.nan))
            writer.dataset_1d("residual_ns", np.where(fit.selected, fit.residual_s*1e9, np.nan))
            writer.dataset_1d("fit_mask", fit.used.astype(float))
            writer.dataset_text("analysis_metadata", [json.dumps(payload, default=json_safe)])
            if collection is not None:
                # Retain all raw channel pairs, not only the selected fit.
                for number, (name, item) in enumerate(collection.series.items()):
                    prefix = f"raw_pair_{number:02d}"
                    writer.dataset_1d(prefix+"_mjd", item.mjd, tags=("raw",))
                    writer.dataset_1d(prefix+"_phase_ns", item.phase_s*1e9, tags=("raw",))
                    writer.dataset_text(prefix+"_label", [name])
                panels = []
                for number, (name, item) in enumerate(overview_series(collection, extra_overview).items()):
                    prefix = f"overview_{number:02d}"
                    writer.dataset_1d(prefix+"_mjd", item.mjd)
                    writer.dataset_1d(prefix+"_phase_ns", item.phase_s*1e9)
                    writer.dataset_text(prefix+"_label", [name])
                    overview_axis = _local_axis_dataset(writer, prefix+"_local", item.mjd)
                    time_pair_pages(document, prefix,
                                    [Trace(prefix+"_mjd", prefix+"_phase_ns", name, marker="circle", line=False)],
                                    prefix+"_mjd", prefix+"_local", overview_axis,
                                    title=name+"; hourly medians" if item.source_kind != "Franks" else name,
                                    ylabel="Time difference (ns)")
            time_pair_pages(document, "timing_fit",
                            [Trace("mjd_utc", "phase_ns", "Measured", marker="circle", line=False),
                             Trace("mjd_utc", "fit_ns", "Selected fit")],
                            "mjd_utc", "local_time", local_axis,
                            title=f"{series.label}: phase and fit", ylabel="Clock time error (ns)")
            time_pair_pages(document, "fit_residual", [Trace("mjd_utc", "residual_ns", "Residual")],
                            "mjd_utc", "local_time", local_axis,
                            title="Residual on selected interval", ylabel="Residual (ns)")
            if stability_result:
                fractional_traces = []
                for name, result in stability_result["estimators"].items():
                    if not result["tau_s"]:
                        continue
                    writer.dataset_1d(name+"_tau", result["tau_s"])
                    writer.dataset_1d(name+"_value", result["deviation"])
                    trace = Trace(name+"_tau", name+"_value", name, marker="circle")
                    if name == "TDEV":
                        xy_page(document, "time_deviation", [trace], title="TDEV: comparison-system stability",
                                xlabel="Averaging time (s)", ylabel="TDEV (s)", xlog=True, ylog=True)
                    else:
                        fractional_traces.append(trace)
                if fractional_traces:
                    xy_page(document, "frequency_stability", fractional_traces,
                            title="Comparison-system stability (not isolated maser noise)",
                            xlabel="Averaging time (s)", ylabel="Fractional frequency deviation",
                            xlog=True, ylog=True)
            if telemetry is not None:
                overlap = ((telemetry.frame.MJD >= series.mjd[fit.selected].min()) &
                           (telemetry.frame.MJD <= series.mjd[fit.selected].max()))
                context = telemetry.frame.loc[overlap]
                if len(context):
                    writer.dataset_1d("telemetry_mjd", context.MJD)
                    writer.dataset_1d("telemetry_dds", context.ExtSynth_32)
                    hmd_axis = _local_axis_dataset(writer, "telemetry_local", context.MJD)
                    time_pair_pages(document, "telemetry_dds", [Trace("telemetry_mjd", "telemetry_dds", "DDS")],
                                    "telemetry_mjd", "telemetry_local", hmd_axis,
                                    title="Overlapping HMD external synthesizer", ylabel="External DDS setting")
                    context.to_csv(temporary/"telemetry_overlap.csv", index=False)
            if t4 is not None:
                t4_pages(writer, document, t4)
                t4.frame().assign(**{f"{c.short_name}_code": t4.codes[:, c.index] for c in t4.schema.channels}
                                  ).to_csv(temporary/"t4_telemetry_decoded.csv", index=False, float_format="%.6g")
            for number, log in enumerate(t4_native):
                t4_native_pages(writer, document, log, _native_prefix(log, number))
                write_native_csv(temporary, log, number)
            writer.finish(document.text())
        report = [
            f"# {'T4' if maser == 'T4' else 'MHM2010'} Analysis Review", "",
            f"Workbench {__version__}. Offline advisory output; no instrument commands were sent.",
            f"Maser under test: {maser}." + (" T4 settings (SETFSREL, DRIFTCOMP) are advisory; confirm the SETFSREL "
                                             "sign with RDFS before and after entry." if maser == "T4" else ""),
            f"Veusz writer {WRITER_VERSION}. Every time-series page exists twice: against MJD (UTC) and against "
            "this computer's local date-time (ISO 8601 with UTC offset).",
            "Use analysis.json for complete precision, assumptions and source hashes.", "",
            f"Channel: {series.label}. Input: {series.source_kind}.",
            f"Fit: order {fit.order}; {int(fit.used.sum())} samples; RMS {fit.rms_ns:.5g} ns.",
            f"Reference epoch: MJD {fit.epoch_mjd:.12f} UTC.", "",
            "## Warnings", *["- "+warning for warning in fit.warnings], "",
            *(["## Reference 1 PPS figures", "",
               f"Pages pps_deltas and pps_fit (first in analysis.vszh5): {reference.title}.", "",
               "```text", *reference.as_dict()["fit"]["box"], "", *reference.as_dict()["fit"]["residual_box"], "```", ""]
              if reference is not None else []),
            *(["## T4 telemetry", "", f"{len(t4.mjd)} frames, schema {t4.schema.version} ({t4.schema.status}). "
               "Host receive times, not maser timestamps. Channel 40 is the LOCK 100 MHz voltage; "
               "channel 41 is the main PLL lock bit.", ""]
              if t4 is not None else []),
            *(["## T4 monitoring files", "",
               *[f"- {log.name}: {log.stats['clock_valid']} clock-valid of {log.stats['records']} records, "
                 f"{log.stats.get('first_utc', '-')} to {log.stats.get('last_utc', '-')} (maser UTC stamps)."
                 for log in t4_native], ""] if t4_native else []),
            "## Correction candidate", "```json",
            json.dumps(correction_result, indent=2, default=json_safe) if correction_result else
            "No correction candidate: current settings and signs must be supplied.",
            "```", "", "## Stable32", 
            "If present, import raw.PHD as phase seconds or raw.FRD as fractional frequency. "
            "Set tau to the value in the sidecar. Fit-residual files are separately labeled. "
            "No gaps were filled; actual zeros are represented by 1e-99.", "",
            "## References", "",
            "| Ref. | IEEE bibliographic entry | URL |", "|---|---|---|",
            "| [1] | Microsemi, MHM-2010 User’s Guide, 75666-201, rev. J, Oct. 2014. Accessed Nov. 3, 2026. | https://www.gb.nrao.edu/~fghigo/timer/MSMmaser/Maser_UG_75666-201_Rev_J.PDF |",
            "| [2] | W. J. Riley, Handbook of Frequency Stability Analysis, NIST SP 1065, Jul. 2008. | https://tf.nist.gov/general/pdf/2220.pdf |",
            "| [3] | W. J. Riley, Stable32 User Manual, ver. 1.54, 2008. Accessed Nov. 3, 2026. | http://www.stable32.com/Manual154.pdf |",
            "| [4] | Safran, iMaser 3000 datasheet, Mar. 11, 2024. Accessed Nov. 3, 2026. | https://safran-navigation-timing.com/wp-content/uploads/2024/03/SAFRAN_IMASER-3000_Datasheet_3-11-2024_US.pdf |",
            "| [5] | W. W. Wallace, T4_Maser_Comms.py, GBO_Maser_Control, commit c72244d. Accessed Nov. 3, 2026. | https://raw.githubusercontent.com/Braveheart53/GBO_Maser_Control/main/T4_Maser_Comms.py |",
            "", "Equations and sign derivations: see the supplied bookmarked user manual, Appendix A."]
        (temporary/"review.md").write_text("\n".join(report)+"\n", encoding="utf-8")
        temporary.rename(destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination
