#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Validated input adapters for MHM2010 Correction Workbench.

===============================================================================
 Title          : Maser Telemetry and Timing Input
 Filename       : maser_io.py
 Version        : 0.1.0 (Semantic Versioning 2.0.0)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-03
 Modified       : 2026-11-03
 Language       : Python 3.12
 Requires       : numpy, pandas, astropy
 Licence        : GPL-3.0-or-later; see LICENSE
 Status         : Engineering analysis; no instrument control
 Revision       : 0.1.0 Initial FITS-first and HMD/Franks implementation.
===============================================================================
The counter measures arrival(B)-arrival(A). For A=MicroSem and B=GPS,
this is the clock time error clock(A)-clock(B), not its negative.
No interpolation across sequentially switched channel pairs is performed.
Reference: F. Ghigo, "Clock Monitoring at Green Bank," rev. Jun. 2020,
https://www.gb.nrao.edu/~fghigo/timer/time_logging.html .
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import io
import numpy as np
import pandas as pd
from astropy.io import fits

__version__ = "0.1.0"


# %% ===========================================================================
#    DATA CONTRACT AND PROVENANCE
# =============================================================================
@dataclass
class TimingSeries:
    """Time-tagged phase in SI seconds; timestamps are UTC MJD days."""
    mjd: np.ndarray
    phase_s: np.ndarray
    label: str
    source_kind: str
    provenance: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class TimingCollection:
    series: dict[str, TimingSeries]
    notes: list[str]


@dataclass
class Telemetry:
    frame: pd.DataFrame
    provenance: list[dict]
    notes: list[str]


def fingerprint(path: str | Path) -> dict:
    """Use basename and SHA256, never export the operator's directory path."""
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return {"name": path.name, "sha256": digest.hexdigest(), "bytes": path.stat().st_size}


def clean_series(mjd, phase_s, label, kind, provenance=None, notes=None):
    """Drop invalid rows; deduplicate identical samples, reject conflicts."""
    frame = pd.DataFrame({"mjd": mjd, "phase_s": phase_s})
    good = np.isfinite(frame.mjd) & np.isfinite(frame.phase_s) & (frame.mjd > 0)
    messages = list(notes or [])
    invalid = int((~good).sum())
    if invalid:
        messages.append(f"Rejected {invalid} nonfinite/nonpositive-time rows.")
    frame = frame.loc[good].sort_values("mjd", kind="stable")
    conflicts = frame.groupby("mjd").phase_s.nunique()
    if (conflicts > 1).any():
        raise ValueError(f"{label}: conflicting values at identical timestamps; resolve the source files.")
    count = len(frame)
    frame = frame.drop_duplicates("mjd")
    if count != len(frame):
        messages.append(f"Removed {count-len(frame)} identical duplicate samples.")
    if len(frame) < 4:
        raise ValueError(f"{label}: fewer than four valid unique phase samples.")
    return TimingSeries(frame.mjd.to_numpy(), frame.phase_s.to_numpy(),
                        label, kind, provenance or [], messages)


# %% ===========================================================================
#    FITS FIRST: KEEP EACH MEASURED CHANNEL PAIR SEPARATE
# =============================================================================
def load_fits(paths: list[str | Path]) -> TimingCollection:
    """Read raw OnePpsDeltas FITS or FITS.GZ without extracting to disk.

    Reject simulated/non-UTC inputs and unknown units. Invalid zero-filled
    trailing records are counted. No fallback to arbitrary HDUs or units.
    """
    groups: dict[str, list] = {}
    notes, provenance = [], []
    for path in paths:
        path = Path(path)
        provenance.append(fingerprint(path))
        with fits.open(path, memmap=False) as hdus:
            if int(hdus[0].header.get("SIMULATE", 0)):
                raise ValueError(f"{path.name}: simulated data are not correction inputs.")
            if str(hdus[0].header.get("TIMESYS", "")).strip().upper() != "UTC":
                raise ValueError(f"{path.name}: TIMESYS must explicitly be UTC.")
            if "OnePpsDeltas" not in hdus:
                raise ValueError(f"{path.name}: missing OnePpsDeltas table.")
            table = hdus["OnePpsDeltas"]
            required = {"DMJD", "CHANNELA", "CHANNELB", "DELTAT"}
            if not required.issubset(table.columns.names):
                raise ValueError(f"{path.name}: expected DMJD, CHANNELA, CHANNELB, DELTAT.")
            time_unit = str(table.columns["DMJD"].unit).strip().lower()
            phase_unit = str(table.columns["DELTAT"].unit).strip().lower()
            scales = {"nanoseconds": 1e-9, "nanosecond": 1e-9, "ns": 1e-9,
                      "s": 1.0, "seconds": 1.0}
            if time_unit not in {"d", "day", "days"} or phase_unit not in scales:
                raise ValueError(f"{path.name}: unrecognized units {time_unit!r}, {phase_unit!r}.")
            data = table.data
            times = np.asarray(data["DMJD"], dtype=float)
            phase = np.asarray(data["DELTAT"], dtype=float) * scales[phase_unit]
            first = np.char.strip(np.asarray(data["CHANNELA"], dtype=str))
            second = np.char.strip(np.asarray(data["CHANNELB"], dtype=str))
            valid = np.isfinite(times) & (times > 0) & (first != "") & (second != "")
            valid &= np.isfinite(phase)
            null_value = table.header.get(f"TNULL{table.columns.names.index('DELTAT')+1}")
            if null_value is not None:
                valid &= np.asarray(data["DELTAT"]) != null_value
            notes.append(f"{path.name}: {int(valid.sum())} rows; {int((~valid).sum())} invalid/padding rows.")
            keys = np.char.add(np.char.add(first, " -> "), second)
            for key in np.unique(keys[valid]):
                selected = valid & (keys == key)
                groups.setdefault(str(key), []).append((times[selected], phase[selected]))
    output = {}
    for key, blocks in groups.items():
        output[key] = clean_series(np.concatenate([item[0] for item in blocks]),
                                   np.concatenate([item[1] for item in blocks]),
                                   key, "FITS", provenance, notes)
    if not output:
        raise ValueError("No valid channel pairs.")
    return TimingCollection(output, notes)


def load_franks(paths: list[str | Path]) -> TimingCollection:
    """Read named t00new columns; never mix reduced and raw observations.

    -9999 and historical -999 are missing sentinels in these reduced files
    only. The same integer values remain valid in raw FITS.
    """
    groups, notes, provenance = {}, [], []
    for path in paths:
        path = Path(path)
        provenance.append(fingerprint(path))
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        header_index = next((i for i, line in enumerate(lines) if line.strip().startswith("MJD")), None)
        if header_index is None or "nanosecs" not in "\n".join(lines[:header_index]).lower():
            raise ValueError(f"{path.name}: expected t00new header declaring nanosecs.")
        names = lines[header_index].split()
        frame = pd.read_csv(io.StringIO("\n".join(lines[header_index+1:])), sep=r"\s+", names=names)
        for name in names[3:]:
            values = pd.to_numeric(frame[name], errors="coerce").to_numpy(dtype=float)
            values[np.isin(values, [-9999, -999])] = np.nan
            groups.setdefault(name, []).append((frame.MJD.to_numpy(float), values * 1e-9))
        notes.append(f"{path.name}: reduced hourly medians; sampling is not assumed uniform.")
    output = {}
    for key, blocks in groups.items():
        times = np.concatenate([item[0] for item in blocks])
        values = np.concatenate([item[1] for item in blocks])
        if np.isfinite(values).sum() >= 4:
            output[key] = clean_series(times, values, key, "Franks", provenance, notes)
    return TimingCollection(output, notes)


def load_timing(paths: list[str | Path]) -> TimingCollection:
    suffixes = [str(path).lower() for path in paths]
    if not suffixes:
        raise ValueError("Select at least one file.")
    if all(name.endswith((".fits", ".fits.gz", ".fit", ".fit.gz")) for name in suffixes):
        return load_fits(paths)
    if all(name.endswith(".t00new") for name in suffixes):
        return load_franks(paths)
    raise ValueError("Choose FITS files or t00new files, not a mixed batch.")


# %% ===========================================================================
#    HOUSEKEEPING TELEMETRY IS NOT PHASE DATA
# =============================================================================
def load_hmd(paths: list[str | Path]) -> Telemetry:
    frames, notes, provenance = [], [], []
    for path in paths:
        path = Path(path)
        provenance.append(fingerprint(path))
        frame = pd.read_csv(path, skipinitialspace=True)
        frame.columns = frame.columns.str.strip()
        if "MJD" not in frame or "ExtSynth_32" not in frame:
            raise ValueError(f"{path.name}: not the documented MHM2010 HMD layout.")
        frame = frame.apply(pd.to_numeric, errors="coerce")
        good = np.isfinite(frame.MJD) & (frame.MJD > 0)
        notes.append(f"{path.name}: {int(good.sum())} timestamp-valid telemetry rows; "
                     f"{int((~good).sum())} rejected.")
        frames.append(frame.loc[good])
    frame = pd.concat(frames, ignore_index=True).sort_values("MJD", kind="stable")
    # Repeated acquisitions are common. Keep different readings at the same
    # timestamp visible; they are not timing observations and are never fitted.
    frame = frame.drop_duplicates().reset_index(drop=True)
    if len(frame) < 2:
        raise ValueError("Too few telemetry rows.")
    notes.append("Alarm/status channels are raw logged values, not decoded booleans.")
    return Telemetry(frame, provenance, notes)


# %% ===========================================================================
#    FRANKS-STYLE OVERVIEW, WITHOUT DISCARDING RAW CHANNEL PAIRS
# =============================================================================
def hourly_medians(series: TimingSeries) -> TimingSeries:
    """UTC-hour buckets, median MJD and phase; empty buckets stay absent."""
    frame = pd.DataFrame({"mjd": series.mjd, "phase": series.phase_s})
    frame["hour"] = np.floor(frame.mjd*24).astype(np.int64)
    reduced = frame.groupby("hour").median()
    return TimingSeries(reduced.mjd.to_numpy(), reduced.phase.to_numpy(),
                        series.label+" [UTC-hour median]", "Hourly median",
                        series.provenance,
                        series.notes+["UTC-hour binning, not asserted identical to historical llogsum windows."])


def overview_series(collection: TimingCollection) -> dict[str, TimingSeries]:
    """Select meaningful overview channels present, never invent missing ones."""
    if not collection.series:
        return {}
    if next(iter(collection.series.values())).source_kind == "Franks":
        return collection.series
    output = {}
    for name in ("Site1Hz", "SigmaTau", "MicroSem", "CNS_GPS1", "CNS_GPS2",
                 "CNS2018", "GBT_VLBA", "RA", "GBTRtn"):
        key = "Site1Hz -> "+name
        if key in collection.series:
            output[name] = hourly_medians(collection.series[key])
    for reference in ("CNS_GPS1", "CNS2018", "CNS_GPS2"):
        key = "MicroSem -> "+reference
        if key in collection.series:
            output["MicroSem vs "+reference+" (direct)"] = hourly_medians(collection.series[key])
    # Preserve the historically useful difference of Site1Hz comparisons as a
    # separate reduction, not a substitute for directly measured maser/GPS.
    if "MicroSem" in output and "CNS_GPS1" in output:
        left, right = output["CNS_GPS1"], output["MicroSem"]
        left_hours = np.floor(left.mjd*24).astype(np.int64)
        right_hours = np.floor(right.mjd*24).astype(np.int64)
        _, li, ri = np.intersect1d(left_hours, right_hours, return_indices=True)
        output["MSM-GPS (derived medians)"] = TimingSeries(
            (left.mjd[li]+right.mjd[ri])/2, left.phase_s[li]-right.phase_s[ri],
            "MSM-GPS (derived medians)", "Hourly median", left.provenance,
            ["median(Site1Hz -> CNS_GPS1) - median(Site1Hz -> MicroSem), per UTC hour."])
    return output
