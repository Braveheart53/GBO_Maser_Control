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
 Version        : 0.2.3 (Semantic Versioning 2.0.0)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-03
 Modified       : 2026-11-10
 Language       : Python 3.12
 Requires       : numpy, pandas, h5py, bundled vszh5_writer (>= 0.0.25);
                  maser_plots; export_names; t4_protocol
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
                  0.2.3 Operator requests of 2026-11-10:
                        * NO number in any export is rounded or trimmed:
                          CSV columns are written with pandas' exact float
                          text (round trip to the same float64; was %.6g in
                          the T4 CSVs), Stable32 and review.md use repr(),
                          file hashes are written in full; every number in
                          a figure box is also a full-precision dataset.
                        * Meaningful dataset names <Source>_<item>_<quantity>
                          (export_names.vname) and Veusz tags source_* and
                          type_* on every dataset (Data panel: Group by Tags);
                          the writer (0.0.25+) names traces after them.
                        * Numeric axes show up to 12 significant digits
                          (FullPrecisionDoc, "%.12Vg"): MJD 60975.25, not
                          6.098 x 10^4; DDS axes no longer cut at the edge.
                        * Every HMD telemetry column over the fit interval
                          (36, was the DDS only), the DDS as a change in
                          counts, and full-precision text datasets of the
                          DDS settings; compressed (gzip) large arrays.
                        * Suggested settings (frequency AND drift change at
                          the application epoch) in analysis.json, review.md
                          and Suggested_* datasets.
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
import pandas as pd
from maser_plots import ReferenceSet, reference_pages_from_set, sans_serif_document, suggestion_datasets
from export_names import (vname, tags, full, full_strings, write_csv, FullPrecisionDoc, source_name,
                          NUMERIC_TICK_FORMAT, __version__ as NAMES_VERSION)
from vszh5_writer import (VeuszWriter, VszDoc, Trace, Panel, xy_page, grid_page, local_time_axis,
                          mjd_to_epoch, time_pair_pages, iso_local_strings, text_page,
                          __version__ as WRITER_VERSION)

__version__ = "0.2.3"
APPLICATION = "MHM2010 / T4 Maser Timing Workbench"


# %% ===========================================================================
#    SERIALIZATION AND STABLE32
# =============================================================================
def json_safe(value):
    """numpy values for json.dumps (floats keep every digit: json writes repr)."""
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
    0.2.3: every number is written with repr() (exact float64; was %.16e,
    also exact, and Tau with %.15e, which could drop the last digit).
    """
    phase, tau0, error = uniform_samples(mjd, phase_s, tolerance)
    frequency = np.diff(phase)/tau0
    metadata = {"tau0_s": tau0, "grid_error_s": error, "phase_count": len(phase),
                "frequency_count": len(frequency), "timetags": "separate CSV",
                "zero_policy": "Actual zero replaced by 1e-99, never interpreted as missing.",
                "number_format": "repr(float64): shortest exact decimal text",
                "units": {"PHD": "phase seconds", "FRD": "dimensionless fractional frequency"}}
    for extension, kind, values in (("PHD", "Phase", phase), ("FRD", "Frequency", frequency)):
        data = values.copy()
        metadata[f"{extension}_zero_replacements"] = int(np.sum(data == 0))
        data[data == 0] = 1e-99
        target = directory / f"{prefix}.{extension}"
        with target.open("w", encoding="ascii", newline="") as handle:
            handle.write(f"File: {target.name}\r\nType: {kind}\r\n"
                         f"Points: {len(data)}\r\nTau: {full(tau0)}\r\n")
            handle.write("".join(f"{full(value)}\r\n" for value in data))
    (directory/f"{prefix}_stable32.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    write_csv(directory/f"{prefix}_timetags.csv", {"mjd_utc": np.asarray(mjd, float),
                                                   "phase_s": np.asarray(phase_s, float)})
    return metadata


# %% ===========================================================================
#    T4 TELEMETRY PAGES (0.2.0; names and tags 0.2.3)
# =============================================================================
def t4_summary(t4: T4Telemetry) -> dict:
    """Provenance and coverage of loaded T4 telemetry for analysis.json."""
    return {"frames": int(len(t4.mjd)), "first_mjd": float(t4.mjd.min()), "last_mjd": float(t4.mjd.max()),
            "schema_version": t4.schema.version, "schema_sha256": t4.schema.sha256,
            "schema_status": t4.schema.status, "rejected": t4.rejected,
            "provenance": t4.provenance, "notes": t4.notes,
            "clock": "host receive time (UTC); not a maser timestamp"}


def _local_axis_dataset(writer: VeuszWriter, name: str, mjd, zone=None, dataset_tags=()):
    """Write a local-time date dataset for MJD values; return its LocalTimeAxis."""
    axis = local_time_axis(mjd_to_epoch(np.asarray(mjd, float)), zone)
    writer.dataset_date(name, axis.veusz_seconds, tags=dataset_tags)
    return axis


def _time_datasets(writer: VeuszWriter, stem: str, origin: str, mjd, zone=None, utc_date: bool = False,
                   iso_text: bool = True, clock: str = "UTC"):
    """<stem>_time_MJD_<clock>, _time_local (date), _time_local_ISO_text and
    optionally _time_UTC (date); returns (mjd name, local name, LocalTimeAxis)."""
    mjd = np.asarray(mjd, float)
    mjd_name = vname(stem, "time_MJD", clock)
    writer.dataset_1d(mjd_name, mjd, tags=tags(origin, "time_MJD"))
    local_name = vname(stem, "time_local")
    axis = _local_axis_dataset(writer, local_name, mjd, zone, tags(origin, "time_local"))
    if utc_date:
        writer.dataset_date(vname(stem, "time_UTC"), (mjd - 54832.0) * 86400.0, tags=tags(origin, "time_UTC"))
    if iso_text:
        writer.dataset_text(vname(stem, "time_local_ISO_text"), iso_local_strings(mjd_to_epoch(mjd), zone),
                            tags=tags(origin, "time_text"))
    return mjd_name, local_name, axis


def _t4_channel_name(prefix: str, channel) -> str:
    """e.g. T4_MONIT_ch05_P15V_V (channel number, short name, unit)."""
    unit = "" if channel.unit in ("", "raw") else channel.unit
    return vname(prefix, f"ch{channel.index + 1:02d}", channel.short_name, unit)


def t4_pages(writer: VeuszWriter, document: VszDoc, t4: T4Telemetry, prefix: str = "T4_MONIT",
             zone=None) -> list[str]:
    """Per functional group, one panel per unit, against MJD and local time.

    Channels without an engineering value ("Not used" in Table 6) are not
    plotted; their raw codes are written as datasets (and in the CSV). The
    main-PLL lock bit gets its own pair of pages. Times are host receive
    times (UTC), not maser stamps. 0.2.3 names: <prefix>_chNN_<short>_<unit>
    (value) and _code (raw code); tags source_T4_MONIT_UDP, type_*.
    """
    origin = "T4_MONIT_UDP"
    mjd_name, local_name, axis = _time_datasets(writer, prefix, origin, t4.mjd, zone, clock="UTC_host")
    pages = []
    for channel in t4.schema.channels:
        name = _t4_channel_name(prefix, channel)
        if channel.available or channel.is_lock:
            writer.dataset_1d(name, t4.values[:, channel.index],
                              tags=tags(origin, "status" if channel.is_lock else "telemetry"))
        writer.dataset_1d(vname(name, "code"), t4.codes[:, channel.index].astype(float),
                          tags=tags(origin, "raw_code"))
    for number, group in enumerate(t4.schema.groups):
        members = [c for c in t4.schema.channels if c.group == group and c.available]
        units = list(dict.fromkeys(c.unit for c in members))
        if not units:
            continue
        for suffix, x, xlabel, xmode, fmt in (
                ("MJD", mjd_name, "MJD (UTC, host receive time)", "numeric", ""),
                ("local", local_name, axis.label + ", host receive time", "datetime", axis.datetime_format)):
            panels = [Panel([Trace(x, _t4_channel_name(prefix, c), f"{c.index + 1}: {c.label}")
                             for c in members if c.unit == unit],
                            xlabel=xlabel, ylabel=unit, title=f"{group}: {unit}", xmode=xmode,
                            x_autorange="exact" if xmode == "datetime" else "next-tick",
                            datetime_format=fmt, name=vname("g", group, unit))
                      for unit in units]
            name = vname(prefix, group, suffix)
            grid_page(document, name, panels, title=f"T4 telemetry (T4S-MAN-0012 Table 6 scaling): {group}",
                      columns=1 if len(panels) == 1 else 2)
            pages.append(name)
    lock = [c for c in t4.schema.channels if c.is_lock]
    if lock:
        pages += time_pair_pages(document, vname(prefix, "main_PLL_lock"),
                                 [Trace(mjd_name, _t4_channel_name(prefix, c), "Main PLL lock (1 = locked)",
                                        marker="circle", line=False) for c in lock],
                                 mjd_name, local_name, axis,
                                 title="T4 main PLL lock status (channel 41)", ylabel="Lock bit",
                                 mjd_label="MJD (UTC, host receive time)")
    return pages


# %% ===========================================================================
#    T4 NATIVE MONITORING FILES (0.2.1; names and tags 0.2.3)
# =============================================================================
def _native_prefix(log, number: int) -> str:
    """Dataset prefix, e.g. 'T4log_MonitHCB' (a number is added for a second log of one layout)."""
    return vname("T4log", log.name) + (f"_{number}" if number else "")


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


def _native_column_name(prefix: str, column) -> str:
    """e.g. T4log_MonitHCB_Set_temperature_degC."""
    return vname(prefix, column.label, column.unit)


def t4_native_pages(writer: VeuszWriter, document: VszDoc, log, prefix: str, zone=None) -> list[str]:
    """Pages for one T4 monitoring log: one per unit, one for status columns.

    Only clock-valid records are plotted (records stamped before the
    time-server sync would put 2006 and 2026 on one axis). Every record,
    with its clock flag, is in the CSV. Time stamps are the maser's own UTC
    times, so these pages may be compared with timing data directly.
    0.2.3 names: <prefix>_time_MJD_UTC, _time_days_since_MJD<n>, _time_local,
    _time_UTC_ISO_text, _time_local_ISO_text, <prefix>_<label>_<unit>,
    <prefix>_<label>_flag / _text; tags source_T4_log_<layout>, type_*.
    """
    mjd = log.valid_mjd
    if len(mjd) == 0:
        return []
    origin = vname("T4_log", log.name)
    mjd_name, local_name, axis = _time_datasets(writer, prefix, origin, mjd, zone)
    # Short logs: plot against MJD - MJD0 (as in the reference 1 PPS figures),
    # so the axis is readable; the absolute MJD dataset is also written.
    mjd0 = float(np.floor(mjd[0]))
    rel_name = vname(prefix, f"time_days_since_MJD{mjd0:.0f}")
    writer.dataset_1d(rel_name, mjd - mjd0, tags=tags(origin, "time_days_since_MJD0"))
    writer.dataset_text(vname(prefix, "time_UTC_ISO_text"),
                        [f"{np.datetime_as_string(t, unit='s')}Z" for t in log.utc[log.clock_valid]],
                        tags=tags(origin, "time_text"))
    pages: list[str] = []
    mjd_label = f"MJD - {mjd0:.0f} (UTC, maser time stamps)"
    for column in log.numeric_columns():
        writer.dataset_1d(_native_column_name(prefix, column), log.valid(column.key),
                          tags=tags(origin, "telemetry"))
    units = list(dict.fromkeys(c.unit for c in log.numeric_columns()))
    for unit in units:
        # One panel per column so that each is scaled on its own (an unused
        # sensor reading a constant 80 must not flatten a 21.5 set point).
        members = [c for c in log.numeric_columns() if c.unit == unit]
        for suffix, x, xlabel, xmode, fmt in (
                ("MJD", rel_name, mjd_label, "numeric", ""),
                ("local", local_name, axis.label, "datetime", axis.datetime_format)):
            panels = [Panel([Trace(x, _native_column_name(prefix, c), c.label, marker="circle", line=True)],
                            xlabel=xlabel, ylabel=unit or "value", title=c.header, xmode=xmode,
                            x_autorange="exact" if xmode == "datetime" else "next-tick",
                            datetime_format=fmt, key=False, **_padded_range(log.valid(c.key)))
                      for c in members]
            name = vname(prefix, unit or "no_unit", suffix)
            grid_page(document, name, panels, title=f"T4 {log.name}: {unit or 'no unit'}", columns=1,
                      height=f"{max(15, 6 * len(panels))}cm")
            pages.append(name)
    status = log.text_columns()
    for column in status:
        writer.dataset_1d(vname(prefix, column.label, "flag"), log.status_flag(column.key)[log.clock_valid],
                          tags=tags(origin, "status"))
        writer.dataset_text(vname(prefix, column.label, "text"), [str(v) for v in log.valid(column.key)],
                            tags=tags(origin, "status", "text"))
    if status:
        traces = [Trace(mjd_name, vname(prefix, c.label, "flag"), f"{c.label} (0 = No error)",
                        marker="circle", line=False) for c in status]
        pages += time_pair_pages(document, vname(prefix, "status"), traces, rel_name, local_name,
                                 axis, title=f"T4 {log.name}: status (text kept in the "
                                             f"{prefix}_<column>_text datasets)",
                                 ylabel="Status flag", mjd_label=mjd_label, ymin=-0.2, ymax=1.2)
    stats = log.stats
    lines = [f"T4 monitoring log {log.name} (reader t4_native; maser UTC time stamps)", "",
             f"Records: {stats['records']}; clock-valid: {stats['clock_valid']}; "
             f"clock not set: {stats['clock_not_set']}",
             f"Clock-valid span: {stats.get('first_utc', '-')} to {stats.get('last_utc', '-')}"]
    if "interval_s" in stats:
        interval = stats["interval_s"]
        lines.append(f"Record interval: median {full(interval['median'])} s, mean {full(interval['mean'])} s, "
                     f"range {full(interval['min'])} to {full(interval['max'])} s")
    for segment in stats.get("clock_not_set_segments", []):
        lines.append(f"Clock not set: {segment['count']} record(s) from {segment['first']}, stamped "
                     f"{segment['stamped_from']} to {segment['stamped_to']}")
    lines += ["", "Notes:", *[f"- {note}" for note in log.notes], "", "Columns:"]
    for header, meaning in stats.get("column_meaning", {}).items():
        lines.append(f"- {header}: {meaning}")
    lines += ["", "Files:", *[f"- {p['name']}  {p['bytes']} bytes  sha256 {p['sha256']}"
                              for p in log.provenance],
              "", "[1] T4Science SA, T4S-MAN-0015, ver. 1.17, Jul. 19, 2022."]
    wrapped = []
    for line in lines:                      # text_page does not wrap; words are never cut
        wrapped += textwrap.wrap(line, 100, subsequent_indent="  ", break_long_words=False,
                                 break_on_hyphens=False) or [""]
    pages += text_page(document, vname(prefix, "notes"), f"T4 {log.name}: provenance and notes", wrapped)
    return pages


def write_native_csv(directory: Path, log, number: int) -> Path:
    """Every record of one log, with clock flag and source line (UTF-8 with BOM for Excel).

    0.2.3: floats at full precision (was %.6g).
    """
    target = directory / f"t4_{log.name.replace('+', '_')}_records.csv"
    if target.exists():
        target = directory / f"t4_{log.name.replace('+', '_')}_{number}_records.csv"
    return write_csv(target, log.frame(), encoding="utf-8-sig")


def _t4_decoded_frame(t4: T4Telemetry) -> pd.DataFrame:
    """Decoded MONIT values plus every raw code (full precision)."""
    return t4.frame().assign(**{f"{c.short_name}_code": t4.codes[:, c.index] for c in t4.schema.channels})


def _new_document() -> FullPrecisionDoc:
    document = FullPrecisionDoc(generator=f"{APPLICATION} {__version__} (export_names {NAMES_VERSION})")
    sans_serif_document(document)
    return document


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
            write_csv(temporary/"t4_telemetry_decoded.csv", _t4_decoded_frame(t4))
        csv_names = [write_native_csv(temporary, log, number).name for number, log in enumerate(native)]
        summary = {"application": APPLICATION, "version": __version__,
                   "created_utc": datetime.now(timezone.utc).isoformat(),
                   "number_format": "every float exact (repr / pandas round-trip text); nothing rounded",
                   "monit": None if t4 is None else t4_summary(t4),
                   "native": [dict(log.summary(), csv=name) for log, name in zip(native, csv_names, strict=True)]}
        if t4 is not None:
            summary.update(t4_summary(t4))          # 0.2.0 keys kept at the top level
        document = _new_document()
        with VeuszWriter(temporary/"t4_telemetry.vszh5", compress=True) as writer:
            for number, log in enumerate(native):
                t4_native_pages(writer, document, log, _native_prefix(log, number))
            if t4 is not None:
                t4_pages(writer, document, t4)
            writer.finish(document.text())
            summary["veusz_datasets"] = {"names": list(writer.names), "tags": writer.tags}
        (temporary/"t4_telemetry.json").write_text(json.dumps(summary, indent=2, default=json_safe),
                                                   encoding="utf-8")
        temporary.rename(destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination


# %% ===========================================================================
#    MHM2010 HMD TELEMETRY (0.2.3: every column)
# =============================================================================
#: HMD columns that hold a DDS setting nnnn.nnnnnnn (one count = 1e-7).
HMD_DDS_COLUMNS = ("ExtSynth_32", "Thumbwheels_31")


def hmd_pages(writer: VeuszWriter, document: VszDoc, context: pd.DataFrame, zone=None) -> list[str]:
    """Every HMD column over the fit interval, the DDS settings and their changes.

    Datasets: HMD_time_MJD_UTC, HMD_time_local, HMD_<column> (each column as
    read, float64), HMD_<DDS column>_change_counts ((value - first) / 1e-7,
    so 6 significant digits show every count), HMD_<DDS column>_reference
    (the first value) and HMD_<DDS column>_text (every value as exact text).
    Pages: DDS change (counts) and DDS setting against MJD and local time;
    every column on grid pages of 10 panels against MJD (DDS columns as counts).
    """
    origin = "HMD"
    mjd = context.MJD.to_numpy(float)
    mjd_name, local_name, axis = _time_datasets(writer, "HMD", origin, mjd, zone)
    columns = [column for column in context.columns if column != "MJD"]
    for column in columns:
        values = pd.to_numeric(context[column], errors="coerce").to_numpy(float)
        writer.dataset_1d(vname("HMD", column), values, tags=tags(origin, "telemetry"))
    pages: list[str] = []
    change_traces, setting_traces, change_values, setting_values = [], [], [], []
    references = {}
    for column in HMD_DDS_COLUMNS:
        if column not in context:
            continue
        values = pd.to_numeric(context[column], errors="coerce").to_numpy(float)
        finite = values[np.isfinite(values)]
        if not len(finite):
            continue
        reference = float(finite[0])
        references[column] = reference
        writer.dataset_1d(vname("HMD", column, "reference"), [reference], tags=tags(origin, "telemetry"))
        writer.dataset_1d(vname("HMD", column, "change_counts"), (values - reference) / 1e-7,
                          tags=tags("Workbench", "telemetry"))
        writer.dataset_text(vname("HMD", column, "text"), full_strings(values), tags=tags(origin, "text"))
        change_traces.append(Trace(mjd_name, vname("HMD", column, "change_counts"),
                                   f"{column} - {full(reference)}"))
        setting_traces.append(Trace(mjd_name, vname("HMD", column), column))
        change_values.append((values - reference) / 1e-7)
        setting_values.append(values)
    if change_traces:
        pages += time_pair_pages(document, "HMD_DDS_change_counts", change_traces, mjd_name, local_name, axis,
                                 title="HMD DDS settings: change from the first value in the interval",
                                 ylabel="Change (counts of 1e-7)", key=True,
                                 **_padded_range(np.concatenate(change_values)))
        pages += time_pair_pages(document, "HMD_DDS_setting", setting_traces, mjd_name, local_name, axis,
                                 title="HMD DDS settings (full value)", ylabel="DDS setting", key=True,
                                 left_margin="3.6cm", **_padded_range(np.concatenate(setting_values)))
    # Every column, 10 panels per page (5 x 2), each scaled on its own; the DDS
    # columns as their change in counts (the full values are on HMD_DDS_setting).
    panels = []
    for column in columns:
        if column in references:
            y_name = vname("HMD", column, "change_counts")
            title = f"{column} - {full(references[column])} (counts)"
            values = (pd.to_numeric(context[column], errors="coerce").to_numpy(float) - references[column]) / 1e-7
        else:
            y_name = vname("HMD", column)
            title = column
            values = pd.to_numeric(context[column], errors="coerce").to_numpy(float)
        panels.append(Panel([Trace(mjd_name, y_name, column)], xlabel="MJD (UTC)", ylabel="", title=title,
                            key=False, name=vname("g_HMD", column), **_padded_range(values)))
    per_page = 10
    for number, first in enumerate(range(0, len(panels), per_page), start=1):
        chunk = panels[first:first + per_page]
        name = f"HMD_all_channels_MJD_{number}"
        grid_page(document, name, chunk, title=f"HMD telemetry, every column over the fit interval "
                                               f"({first + 1} to {first + len(chunk)} of {len(panels)})",
                  columns=5, width="50cm", height="22cm")
        pages.append(name)
    return pages


# %% ===========================================================================
#    REVIEW TEXT (0.2.3: full precision)
# =============================================================================
def _full_table(title: str, values: dict) -> list[str]:
    """Markdown table 'quantity | exact value' (nested dicts flattened with '.')."""
    rows = []

    def walk(prefix, item):
        if isinstance(item, dict):
            for key, value in item.items():
                walk(f"{prefix}.{key}" if prefix else str(key), value)
        elif isinstance(item, (list, tuple)):
            rows.append((prefix, "; ".join(str(v) for v in item)))
        elif isinstance(item, (int, float, np.number)) and not isinstance(item, bool):
            rows.append((prefix, full(item)))
        else:
            rows.append((prefix, "" if item is None else str(item)))
    walk("", values)
    return [f"### {title}", "", "| Quantity | Value (exact) |", "|---|---|",
            *[f"| {key} | {value} |" for key, value in rows], ""]


# %% ===========================================================================
#    COMPLETE ANALYSIS BUNDLE
# =============================================================================
def export_bundle(destination: str | Path, series: TimingSeries, fit: PhaseFit,
                  correction_result=None, telemetry: Telemetry | None=None,
                  stability_result=None, settings=None, stable32=False,
                  stable32_tolerance=0.01, collection: TimingCollection | None=None,
                  maser: str = "MHM2010", reference: ReferenceSet | None = None,
                  t4: T4Telemetry | None = None, extra_overview: tuple[str, ...] = (),
                  t4_native=(), suggestion: dict | None = None):
    """Write a NEW results directory or fail without replacing existing data.

    0.2.0: ``reference`` adds the two reference-style 1 PPS pages as the
    first pages of analysis.vszh5; ``maser`` records the maser under test;
    ``t4`` adds T4 telemetry datasets/pages and t4_telemetry_decoded.csv.
    0.2.1: ``t4_native`` (T4 monitoring logs) adds their pages and CSVs.
    0.2.3: ``suggestion`` (maser_core.suggested_settings: frequency AND
    drift change) goes to analysis.json, review.md and Suggested_* datasets
    (when ``reference`` carries one, that one is used); names and tags as
    in export_names; nothing is rounded.
    """
    t4_native = list(t4_native or ())
    if suggestion is None and reference is not None:
        suggestion = reference.suggestion
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError("Choose a new results directory; existing results are never overwritten.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".mhm-export-", dir=destination.parent))
    origin = source_name(series.source_kind)
    channel = series.label
    selected_stem = vname("Selected", channel)
    fit_stem = vname("Fit", channel)
    try:
        payload = {
            "application": APPLICATION, "version": __version__, "maser": maser,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "number_format": "every float exact (JSON/repr/pandas round-trip text); nothing rounded",
            "series": {"label": series.label, "kind": series.source_kind,
                       "provenance": series.provenance, "notes": series.notes},
            "fit": {key: value for key, value in asdict(fit).items()
                    if key not in {"selected", "used", "residual_s", "predicted_s"}},
            "selected_count": int(fit.selected.sum()), "fit_count": int(fit.used.sum()),
            "correction": correction_result, "suggested_settings": suggestion, "settings": settings or {},
            "stability": stability_result,
            "telemetry": None if telemetry is None else {
                "provenance": telemetry.provenance, "notes": telemetry.notes,
                "first_mjd": float(telemetry.frame.MJD.min()),
                "last_mjd": float(telemetry.frame.MJD.max())},
            "reference_plots": None if reference is None else reference.as_dict(),
            "t4_telemetry": None if t4 is None else t4_summary(t4),
            "t4_native": [log.summary() for log in t4_native],
        }
        write_csv(temporary/"phase_and_fit.csv",
                  {"mjd_utc": series.mjd, "phase_s": series.phase_s, "model_s": fit.predicted_s,
                   "residual_s": fit.residual_s, "selected": fit.selected.astype(int),
                   "used_in_fit": fit.used.astype(int)})
        if stable32:
            # Do not compress excluded samples together: the validator will
            # reject an internal hole rather than change the sampling interval.
            selected = fit.used
            payload["stable32_raw"] = write_stable32(temporary, series.mjd[selected],
                series.phase_s[selected], "raw", stable32_tolerance)
            payload["stable32_residual"] = write_stable32(temporary, series.mjd[selected],
                fit.residual_s[selected], "fit_residual", stable32_tolerance)
        if stability_result:
            rows = [(name, tau, value, count) for name, result in stability_result["estimators"].items()
                    for tau, value, count in zip(result["tau_s"], result["deviation"], result["terms"])]
            write_csv(temporary/"stability.csv", pd.DataFrame(rows, columns=["estimator", "tau_s", "deviation",
                                                                            "overlapping_terms"]))
        document = _new_document()
        context = None
        with VeuszWriter(temporary/"analysis.vszh5", compress=True) as writer:
            if reference is not None:
                # The operator's reference figures come first in the document.
                reference_pages_from_set(writer, document, None, reference)
                suggestion_datasets(writer, suggestion, reference.suggestion_lines)
            else:
                suggestion_datasets(writer, suggestion)
            mjd_name, local_name, local_axis = _time_datasets(writer, selected_stem, origin, series.mjd,
                                                              utc_date=True)
            phase_name = vname(selected_stem, "phase_ns")
            writer.dataset_1d(phase_name, series.phase_s*1e9, tags=tags(origin, "measurement", "selected_channel"))
            # Model/residual are only drawn inside the chosen interval.
            model_name, residual_name = vname(fit_stem, "model_phase_ns"), vname(fit_stem, "residual_ns")
            writer.dataset_1d(model_name, np.where(fit.selected, fit.predicted_s*1e9, np.nan),
                              tags=tags("Workbench", "fit_model"))
            writer.dataset_1d(residual_name, np.where(fit.selected, fit.residual_s*1e9, np.nan),
                              tags=tags("Workbench", "fit_residual"))
            writer.dataset_1d(vname(fit_stem, "used_in_fit_flag"), fit.used.astype(float),
                              tags=tags("Workbench", "fit_mask"))
            writer.dataset_1d(vname(fit_stem, "coefficients_ns_per_day_power"),
                              np.asarray(fit.coefficients_ns, float), tags=tags("Workbench", "fit_results"))
            writer.dataset_1d(vname(fit_stem, "epoch_MJD_UTC"), [fit.epoch_mjd], tags=tags("Workbench",
                                                                                         "fit_results"))
            writer.dataset_1d(vname(fit_stem, "rms_ns"), [fit.rms_ns], tags=tags("Workbench", "fit_results"))
            writer.dataset_text("Workbench_analysis_metadata_JSON", [json.dumps(payload, default=json_safe)],
                                tags=tags("Workbench", "metadata"))
            if collection is not None:
                # Retain all raw channel pairs, not only the selected fit.
                for name, item in collection.series.items():
                    stem = vname(source_name(item.source_kind), name)
                    writer.dataset_1d(vname(stem, "time_MJD_UTC"), item.mjd,
                                      tags=tags(source_name(item.source_kind), "time_MJD", "all_channels"))
                    writer.dataset_1d(vname(stem, "phase_ns"), item.phase_s*1e9,
                                      tags=tags(source_name(item.source_kind), "measurement", "all_channels"))
                    writer.dataset_text(vname(stem, "label_text"), [name],
                                        tags=tags(source_name(item.source_kind), "text"))
                for name, item in overview_series(collection, extra_overview).items():
                    stem = vname("Overview", name)
                    item_origin = source_name(item.source_kind)
                    o_mjd, o_local, overview_axis = _time_datasets(writer, stem, item_origin, item.mjd,
                                                                   iso_text=False)
                    writer.dataset_1d(vname(stem, "phase_ns"), item.phase_s*1e9,
                                      tags=tags(item_origin, "measurement", "overview"))
                    writer.dataset_text(vname(stem, "label_text"), [name], tags=tags(item_origin, "text"))
                    time_pair_pages(document, stem,
                                    [Trace(o_mjd, vname(stem, "phase_ns"), name, marker="circle", line=False)],
                                    o_mjd, o_local, overview_axis,
                                    title=name+"; hourly medians" if item.source_kind != "Franks" else name,
                                    ylabel="Time difference (ns)")
            time_pair_pages(document, vname(selected_stem, "phase_and_fit"),
                            [Trace(mjd_name, phase_name, "Measured", marker="circle", line=False),
                             Trace(mjd_name, model_name, "Selected fit")],
                            mjd_name, local_name, local_axis,
                            title=f"{series.label}: phase and fit", ylabel="Clock time error (ns)")
            time_pair_pages(document, vname(fit_stem, "residual"), [Trace(mjd_name, residual_name, "Residual")],
                            mjd_name, local_name, local_axis,
                            title="Residual on selected interval", ylabel="Residual (ns)")
            if stability_result:
                fractional_traces = []
                for name, result in stability_result["estimators"].items():
                    if not result["tau_s"]:
                        continue
                    stem = vname("Stability", name)
                    writer.dataset_1d(vname(stem, "tau_s"), result["tau_s"], tags=tags("Workbench", "stability"))
                    writer.dataset_1d(vname(stem, "deviation"), result["deviation"],
                                      tags=tags("Workbench", "stability"))
                    writer.dataset_1d(vname(stem, "terms"), np.asarray(result["terms"], float),
                                      tags=tags("Workbench", "stability"))
                    trace = Trace(vname(stem, "tau_s"), vname(stem, "deviation"), name, marker="circle")
                    if name == "TDEV":
                        xy_page(document, "Stability_TDEV", [trace], title="TDEV: comparison-system stability",
                                xlabel="Averaging time (s)", ylabel="TDEV (s)", xlog=True, ylog=True)
                    else:
                        fractional_traces.append(trace)
                if fractional_traces:
                    xy_page(document, "Stability_fractional_frequency", fractional_traces,
                            title="Comparison-system stability (not isolated maser noise)",
                            xlabel="Averaging time (s)", ylabel="Fractional frequency deviation",
                            xlog=True, ylog=True)
            if telemetry is not None:
                overlap = ((telemetry.frame.MJD >= series.mjd[fit.selected].min()) &
                           (telemetry.frame.MJD <= series.mjd[fit.selected].max()))
                context = telemetry.frame.loc[overlap]
                if len(context):
                    hmd_pages(writer, document, context)
            if t4 is not None:
                t4_pages(writer, document, t4)
            for number, log in enumerate(t4_native):
                t4_native_pages(writer, document, log, _native_prefix(log, number))
                write_native_csv(temporary, log, number)
            payload_names = {"names": list(writer.names), "tags": {k: list(v) for k, v in writer.tags.items()}}
            writer.finish(document.text())
        payload["veusz_datasets"] = payload_names
        (temporary/"analysis.json").write_text(
            json.dumps(payload, indent=2, default=json_safe, allow_nan=False), encoding="utf-8")
        if context is not None and len(context):
            write_csv(temporary/"telemetry_overlap.csv", context)
        if t4 is not None:
            write_csv(temporary/"t4_telemetry_decoded.csv", _t4_decoded_frame(t4))
        report = [
            f"# {'T4' if maser == 'T4' else 'MHM2010'} Analysis Review", "",
            f"Workbench {__version__}. Offline advisory output; no instrument commands were sent.",
            f"Maser under test: {maser}." + (" T4 settings (SETFSREL, DRIFTCOMP) are advisory; confirm the SETFSREL "
                                             "sign with RDFS before and after entry." if maser == "T4" else ""),
            f"Veusz writer {WRITER_VERSION}. Every time-series page exists twice: against MJD (UTC) and against "
            "this computer's local date-time (ISO 8601 with UTC offset). Datasets are named "
            "<Source>_<item>_<quantity> and tagged source_* and type_* (Veusz Data panel: Group by > Tags); "
            f"numeric axes use the tick format {NUMERIC_TICK_FORMAT}.",
            "Numbers in this file and every CSV are exact float64 text (nothing rounded). Figure boxes "
            "keep the figure layout; their full values are in the tables below, in analysis.json and in "
            "the *_results_full_precision_text and Suggested_*_full_precision_text datasets.", "",
            f"Channel: {series.label}. Input: {series.source_kind}.",
            f"Fit: order {fit.order}; {int(fit.used.sum())} samples; RMS {full(fit.rms_ns)} ns.",
            f"Reference epoch: MJD {full(fit.epoch_mjd)} UTC.", "",
            "## Warnings", *["- "+warning for warning in fit.warnings], "",
            *(["## Suggested settings (frequency and drift)", "",
               *(["```text", *reference.suggestion_lines, "```", ""] if reference is not None and
                 reference.suggestion_lines else []),
               *_full_table("Suggested settings, exact values", suggestion), ""]
              if suggestion else ["## Suggested settings", "", "None: not an eligible maser/reference pair, or no "
                                  "fit.", ""]),
            *(["## Reference 1 PPS figures", "",
               f"Pages {reference.prefix}_deltas_MJD and {reference.prefix}_fit_MJD (first in analysis.vszh5), "
               f"with local-time twins: {reference.title}.", "",
               "```text", *reference.as_dict()["fit"]["box"], "", *reference.as_dict()["fit"]["residual_box"], "```",
               "", *_full_table("Reference figure numbers, exact values",
                                {"deltas": {k: v for k, v in reference.as_dict()["deltas"].items() if k != "box"},
                                 "fit": {k: v for k, v in reference.fit.as_dict().items()},
                                 "synth_adjustment": reference.synth or {}}), ""]
              if reference is not None else []),
            *(["## T4 telemetry", "", f"{len(t4.mjd)} frames, schema {t4.schema.version} ({t4.schema.status}). "
               "Host receive times, not maser timestamps. Channel 40 is the LOCK 100 MHz voltage; "
               "channel 41 is the main PLL lock bit.", ""]
              if t4 is not None else []),
            *(["## T4 monitoring files", "",
               *[f"- {log.name}: {log.stats['clock_valid']} clock-valid of {log.stats['records']} records, "
                 f"{log.stats.get('first_utc', '-')} to {log.stats.get('last_utc', '-')} (maser UTC stamps)."
                 for log in t4_native], ""] if t4_native else []),
            "## Correction candidate (absolute settings; needs the current DDS and drift)", "```json",
            json.dumps(correction_result, indent=2, default=json_safe) if correction_result else
            "No correction candidate: current settings and signs must be supplied. The suggested CHANGES "
            "above do not need them.",
            "```", "", "## Stable32",
            "If present, import raw.PHD as phase seconds or raw.FRD as fractional frequency. "
            "Set tau to the value in the sidecar. Fit-residual files are separately labeled. "
            "No gaps were filled; actual zeros are represented by 1e-99.", "",
            "## Veusz datasets", "",
            f"{len(payload_names['names'])} datasets; tags: " +
            ", ".join(f"{tag} ({len(names)})" for tag, names in sorted(payload_names["tags"].items())), "",
            "## References", "",
            "| Ref. | IEEE bibliographic entry | URL |", "|---|---|---|",
            "| [1] | Microsemi, MHM-2010 User’s Guide, 75666-201, rev. J, Oct. 2014. Accessed Nov. 3, 2026. | https://www.gb.nrao.edu/~fghigo/timer/MSMmaser/Maser_UG_75666-201_Rev_J.PDF |",
            "| [2] | W. J. Riley, Handbook of Frequency Stability Analysis, NIST SP 1065, Jul. 2008. | https://tf.nist.gov/general/pdf/2220.pdf |",
            "| [3] | W. J. Riley, Stable32 User Manual, ver. 1.54, 2008. Accessed Nov. 3, 2026. | http://www.stable32.com/Manual154.pdf |",
            "| [4] | Safran, iMaser 3000 datasheet, Mar. 11, 2024. Accessed Nov. 3, 2026. | https://safran-navigation-timing.com/wp-content/uploads/2024/03/SAFRAN_IMASER-3000_Datasheet_3-11-2024_US.pdf |",
            "| [5] | W. W. Wallace, T4_Maser_Comms.py, GBO_Maser_Control, commit c72244d. Accessed Nov. 3, 2026. | https://raw.githubusercontent.com/Braveheart53/GBO_Maser_Control/main/T4_Maser_Comms.py |",
            "| [6] | Veusz Developers, Veusz 3.6.2 source (utils/formatting.py: %Vg; dialogs/dataeditdialog.py). | https://pypi.org/project/veusz/3.6.2/ |",
            "", "Equations and sign derivations: see the supplied bookmarked user manual, Appendix A."]
        (temporary/"review.md").write_text("\n".join(report)+"\n", encoding="utf-8")
        temporary.rename(destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination
