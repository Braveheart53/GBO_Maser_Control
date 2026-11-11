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
 Version        : 0.2.1 (Semantic Versioning 2.0.0)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-03
 Modified       : 2026-11-05
 Language       : Python 3.12
 Requires       : numpy, pandas, astropy; t4_protocol (T4 telemetry only)
 Licence        : GPL-3.0-or-later; see LICENSE
 Status         : Engineering analysis; no instrument control
 Revision       : 0.1.0 Initial FITS-first and HMD/Franks implementation.
                  0.2.0 T4 telemetry input: re-decodes raw MONIT frames from
                        the recorder CSV or from timestamped text logs with
                        the selected schema (raw codes always retained);
                        FITS and Franks in one selection (kept separate);
                        Franks month-boundary repeats resolved and reported
                        (real 2026_06/2026_07 files disagreed at one hour).
                  0.2.1 load_t4_files(): one selection may mix MONIT
                        telemetry (recorder CSV, text logs) and the T4's own
                        monitoring files (FTP ./Monitoring, e.g. MonitHCB.dat,
                        read by t4_native); each kind is returned separately.
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
import re
import numpy as np
import pandas as pd
from astropy.io import fits

__version__ = "0.2.1"


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

    0.2.0: consecutive monthly files repeat the hours at the month boundary.
    Identical repeats are dropped. If a repeat DISAGREES (seen in 2026_06 vs
    2026_07 at MJD 61222.01917), the row from the file named for the row's
    own month (YYYY_MM.t00new vs the row's MM-DD-YYYY date) is kept, and the
    conflict is reported in the notes instead of rejecting the selection.
    """
    frames, notes, provenance = [], [], []
    for path in paths:
        path = Path(path)
        provenance.append(fingerprint(path))
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        header_index = next((i for i, line in enumerate(lines) if line.strip().startswith("MJD")), None)
        if header_index is None or "nanosecs" not in "\n".join(lines[:header_index]).lower():
            raise ValueError(f"{path.name}: expected t00new header declaring nanosecs.")
        names = lines[header_index].split()
        frame = pd.read_csv(io.StringIO("\n".join(lines[header_index+1:])), sep=r"\s+", names=names,
                            dtype={"date": str, "time": str})
        stem = re.match(r"(\d{4})_(\d{2})", path.name)
        row_month = frame["date"].str.slice(6, 10) + "_" + frame["date"].str.slice(0, 2)
        frame["_rank"] = 0 if stem is None else (row_month != f"{stem.group(1)}_{stem.group(2)}").astype(int)
        frame["_file"] = path.name
        frames.append(frame)
        notes.append(f"{path.name}: reduced hourly medians; sampling is not assumed uniform.")
    frame = pd.concat(frames, ignore_index=True)
    columns = [name for name in frame.columns if name not in ("MJD", "date", "time", "_rank", "_file")]
    repeated = frame[frame.duplicated("MJD", keep=False)]
    conflicts = [mjd for mjd, group in repeated.groupby("MJD")
                 if len(group[columns].drop_duplicates()) > 1]
    for mjd in conflicts:
        group = frame[frame.MJD == mjd].sort_values("_rank", kind="stable")
        notes.append(f"MJD {mjd:.5f}: month-boundary rows disagree between "
                     f"{', '.join(group._file)}; kept {group._file.iloc[0]} (its own month).")
    before = len(frame)
    frame = frame.sort_values(["MJD", "_rank"], kind="stable").drop_duplicates("MJD", keep="first")
    if before != len(frame):
        notes.append(f"Removed {before-len(frame)} repeated month-boundary rows "
                     f"({len(conflicts)} with disagreeing values).")
    output = {}
    for name in columns:
        values = pd.to_numeric(frame[name], errors="coerce").to_numpy(dtype=float)
        values[np.isin(values, [-9999, -999])] = np.nan
        if np.isfinite(values).sum() >= 4:
            output[name] = clean_series(frame.MJD.to_numpy(float), values*1e-9, name, "Franks", provenance, notes)
    return TimingCollection(output, notes)


FITS_SUFFIXES = (".fits", ".fits.gz", ".fit", ".fit.gz")


def load_timing(paths: list[str | Path]) -> TimingCollection:
    """FITS, Franks t00new, or BOTH (0.2.0) in one selection.

    The two kinds are loaded separately and never merged or interpolated:
    FITS series are keyed "A -> B" (raw channel pairs), Franks series by
    their column name (hourly medians, e.g. "MSM-GPS"), so keys cannot
    collide and each series keeps its own source_kind and provenance.
    """
    if not paths:
        raise ValueError("Select at least one file.")
    fits_paths = [path for path in paths if str(path).lower().endswith(FITS_SUFFIXES)]
    franks_paths = [path for path in paths if str(path).lower().endswith(".t00new")]
    unknown = [Path(path).name for path in paths if path not in fits_paths and path not in franks_paths]
    if unknown:
        raise ValueError(f"Unsupported timing file(s): {', '.join(unknown[:3])}. Use FITS or .t00new.")
    collections = ([load_fits(fits_paths)] if fits_paths else []) + \
                  ([load_franks(franks_paths)] if franks_paths else [])
    if len(collections) == 1:
        return collections[0]
    series = {**collections[0].series, **collections[1].series}
    return TimingCollection(series, collections[0].notes + collections[1].notes +
                            ["FITS and Franks inputs are kept as separate series; they are never merged."])


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
#    T4 TELEMETRY: RE-DECODE RAW FRAMES WITH THE SELECTED SCHEMA
# =============================================================================
@dataclass
class T4Telemetry:
    """Decoded T4 MONIT telemetry. Times are HOST receive times (UTC MJD)."""
    mjd: np.ndarray
    codes: np.ndarray
    values: np.ndarray
    schema: object
    provenance: list[dict]
    notes: list[str]
    rejected: dict[str, int]

    def frame(self) -> pd.DataFrame:
        from t4_protocol import column_names
        frame = pd.DataFrame(self.values, columns=column_names(self.schema))
        frame.insert(0, "MJD", self.mjd)
        return frame


_ISO_ZONE = re.compile(r"(Z|[+-]\d\d:?\d\d)$")


def _line_time_to_mjd(token: str, assume_utc: bool) -> float:
    """MJD from an MJD number or an ISO-8601 time with an explicit zone."""
    token = token.strip().strip(",;")
    try:
        value = float(token)
        if 40000 < value < 100000:
            return value
    except ValueError:
        pass
    if not _ISO_ZONE.search(token) and not assume_utc:
        raise ValueError(f"time {token!r} has no UTC offset")
    stamp = pd.Timestamp(token)
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    return stamp.value / 86400e9 + 40587.0


def load_t4_telemetry(paths: list[str | Path], schema=None, allow_prefix: bool = False,
                      assume_utc: bool = False, use_gpu: bool = False) -> T4Telemetry:
    """Read T4 telemetry and decode it with ``schema`` (default: shipped draft).

    Accepted inputs (one kind per file, detected from the content):

    * recorder ``t4_raw_frames.csv``: raw datagrams are re-parsed strictly
      (this is the preferred source: a newer schema re-decodes history);
    * recorder ``t4_telemetry.csv``: the stored raw *codes* are re-scaled;
    * text logs: one reply per line, ``<time> ... $<113 hex>``, where time is
      an MJD or an ISO-8601 time WITH a zone (``assume_utc`` accepts naive).

    Rejected lines are counted by reason, never silently dropped.
    """
    from t4_protocol import T4FrameError, decode_many, load_schema, split_frame
    schema = schema or load_schema()
    times, payloads, notes, provenance = [], [], [], []
    rejected: dict[str, int] = {}
    direct_codes: list[np.ndarray] = []
    direct_times: list[np.ndarray] = []

    def reject(reason: str) -> None:
        rejected[reason] = rejected.get(reason, 0) + 1

    for path in map(Path, paths):
        provenance.append(fingerprint(path))
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        first = text.splitlines()[0] if text else ""
        if first.startswith("sequence,") and "raw_ascii" in first:
            from t4_monitor import unescape_raw
            frame = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
            for mjd_text, raw_text, status in zip(frame.receive_mjd, frame.raw_ascii, frame.status):
                if not raw_text:
                    reject(f"no reply ({status or 'unknown'})")
                    continue
                try:
                    _, payload, _ = split_frame(unescape_raw(raw_text), schema, allow_prefix=allow_prefix)
                    times.append(float(mjd_text))
                    payloads.append(payload)
                except (T4FrameError, ValueError) as error:
                    reject(getattr(error, "reason", "UNREADABLE"))
            notes.append(f"{path.name}: recorder raw frames re-decoded with schema {schema.version}.")
        elif first.startswith("receive_mjd,") and "T4_00_code" in first:
            frame = pd.read_csv(io.StringIO(text))
            code_columns = [f"{channel.short_name}_code" for channel in schema.channels]
            missing = [name for name in code_columns if name not in frame]
            if missing:
                raise ValueError(f"{path.name}: missing code columns {missing[:3]}...")
            direct_times.append(frame.receive_mjd.to_numpy(float))
            direct_codes.append(frame[code_columns].to_numpy(np.int64))
            notes.append(f"{path.name}: stored raw codes re-scaled with schema {schema.version}.")
        else:
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                position = line.find("$")
                if position <= 0:
                    reject("NO_TIME_OR_NO_FRAME")
                    continue
                try:
                    mjd = _line_time_to_mjd(line[:position].split()[0] if line[:position].split() else "",
                                            assume_utc)
                    _, payload, _ = split_frame(line[position:].encode("ascii", "replace"), schema,
                                             require_terminator=False, allow_prefix=False)
                    times.append(mjd)
                    payloads.append(payload)
                except T4FrameError as error:
                    reject(error.reason)
                except (ValueError, IndexError):
                    reject("BAD_TIME")
            notes.append(f"{path.name}: text log, one '$' frame per timestamped line.")
    codes, values = decode_many(payloads, schema, use_gpu=use_gpu)
    mjd = np.asarray(times, float)
    if direct_codes:
        from t4_protocol import values_from_codes
        extra = np.concatenate(direct_codes)
        codes = np.vstack([codes, extra])
        values = np.vstack([values, values_from_codes(extra, schema)])
        mjd = np.concatenate([mjd, *direct_times])
    if len(mjd) == 0:
        raise ValueError("No decodable T4 frames. Rejections: " + (str(rejected) or "none"))
    order = np.argsort(mjd, kind="stable")
    mjd, codes, values = mjd[order], codes[order], values[order]
    keep = np.r_[True, (np.diff(mjd) != 0) | np.any(np.diff(codes, axis=0) != 0, axis=1)]
    if (~keep).any():
        notes.append(f"Removed {int((~keep).sum())} identical duplicate frames.")
    notes.append(f"Schema {schema.version} sha256 {schema.sha256[:16]}…: {schema.status}")
    if rejected:
        notes.append("Rejected lines/frames: " + ", ".join(f"{key} x{value}" for key, value in rejected.items()))
    return T4Telemetry(mjd[keep], codes[keep], values[keep], schema, provenance, notes, rejected)


@dataclass
class T4Files:
    """Result of load_t4_files: MONIT telemetry and/or native monitoring logs."""
    monit: T4Telemetry | None
    native: list
    notes: list[str]

    def describe(self) -> str:
        parts = []
        if self.monit is not None:
            parts.append(f"{len(self.monit.mjd)} MONIT frames")
        for log in self.native:
            parts.append(f"{log.name}: {log.stats['clock_valid']} clock-valid of {log.stats['records']} records")
        return "; ".join(parts) or "nothing loaded"


def load_t4_files(paths: list[str | Path], schema=None, allow_prefix: bool = False,
                  assume_utc: bool = False, use_gpu: bool = False, date_order: str = "DMY",
                  earliest_valid: str | None = None) -> T4Files:
    """Route each file to the right T4 reader (0.2.1).

    Files whose first line is a tab-separated header starting with TIME are
    the T4 LAN module's own monitoring files (FTP ./Monitoring) and go to
    t4_native.load_native_logs; everything else goes to load_t4_telemetry.
    The two kinds are never merged: they have different channels and clocks
    (MONIT rows use host receive times; native rows use the maser's UTC stamps).
    """
    import t4_native
    paths = [Path(p) for p in paths]
    native_paths = [p for p in paths if t4_native.is_native_log(p)]
    monit_paths = [p for p in paths if p not in native_paths]
    notes = []
    monit = (load_t4_telemetry(monit_paths, schema, allow_prefix, assume_utc, use_gpu)
             if monit_paths else None)
    native = (t4_native.load_native_logs(native_paths, date_order,
                                         earliest_valid or t4_native.DEFAULT_EARLIEST_VALID)
              if native_paths else [])
    if monit_paths and native_paths:
        notes.append("MONIT telemetry and T4 monitoring files are kept separate (different clocks).")
    return T4Files(monit, native, notes)


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


def overview_series(collection: TimingCollection, extra_names: tuple[str, ...] = ()) -> dict[str, TimingSeries]:
    """Select meaningful overview channels present, never invent missing ones.

    ``extra_names`` (0.2.0) adds operator-designated channel names, e.g. the
    FITS name of the T4, which is not known to this program in advance.
    """
    if not collection.series:
        return {}
    franks = {name: item for name, item in collection.series.items() if item.source_kind == "Franks"}
    if len(franks) == len(collection.series):
        return collection.series
    output = {}
    for name in ("Site1Hz", "SigmaTau", "MicroSem", "CNS_GPS1", "CNS_GPS2",
                 "CNS2018", "GBT_VLBA", "RA", "GBTRtn", *extra_names):
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
    for name, item in franks.items():            # mixed FITS + Franks selection
        output["Franks: " + name] = item
    return output
