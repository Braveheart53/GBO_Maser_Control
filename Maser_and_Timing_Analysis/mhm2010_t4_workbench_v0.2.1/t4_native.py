#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Reader for the T4 iMaser LAN module's own monitoring files (FTP ./Monitoring).

===============================================================================
 Title          : T4 Native Monitoring File Reader
 Filename       : t4_native.py
 Version        : 0.0.1 (Semantic Versioning 2.0.0; first internal draft)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-05
 Modified       : 2026-11-05
 Language       : Python 3.12 (standard library + numpy, pandas)
 Licence        : GPL-3.0-or-later; see LICENSE
 Status         : Format inferred from ONE real file (MonitHCB.dat, GBO T4,
                  2026-11-05); the manuals do not document it.
 Revision       : 0.0.1 Header-driven reader: tab-separated, first column
                        TIME as DD/MM/YY HH:MM:SS in UTC, named columns with
                        units in brackets, CR LF lines. Clock-not-set records
                        (before the time-server sync) are kept and flagged,
                        never plotted or fitted. Degree-sign repair, numeric
                        and text columns, interval and gap statistics,
                        constant-column notes, multi-file merge per layout.
===============================================================================
What the manuals say (T4S-MAN-0015 ver. 1.17 [1]):

* RECMONIT=<s>; records the monitoring "in a file stored on the local SD
  memory card"; the file is reached by FTP under ./Monitoring; at 2 MB it is
  copied "with a tag of the date and time" and restarted (Sec. 1.1.3, p. 12).
* Each record is tagged with the module's date and time (Sec. 1.1.3, p. 12);
  the clock comes from NTP or SETDATE/SETTIME and is lost at power-up if
  no synchronisation is available (pp. 12, 22).
* Option telemetry (e.g. HCB) is recorded in the monitoring directory even
  though MONIT does not return it (Sec. 1.1.3, p. 14). For the HCB the page
  shows the set temperature, the internal regulation sensor, the external
  sensor "if used", and the alarm status (Sec. 1.1.3, p. 16).

What was established from the real file (not in the manuals):

* DD/MM/YY: "16/01/06" cannot be MM/DD, and "05/11/26 18:25:39" matched the
  upload two minutes later (13:27 EST = 18:27 UTC). The times are UTC, as the
  operator confirmed; early 2006/2007 dates predate the time-server sync.
* The degree sign arrived as U+FFFD (bytes EF BF BD); it is repaired.

Memory: files are at most about 2 MB each [1, p. 12]; many rolled files are
read in parallel threads (I/O bound). GPU processing brings no benefit here.

[1] T4Science SA, T4S-Monitoring Network Software User Manual, T4S-MAN-0015,
    ver. 1.17, Jul. 19, 2022.
"""
from __future__ import annotations

# %% ===========================================================================
#    IMPORTS
# =============================================================================
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import re

import numpy as np
import pandas as pd

__version__ = "0.0.1"

# %% ===========================================================================
#    CONSTANTS
# =============================================================================
#: Date orders accepted for the TIME column. "DMY" is the verified default.
DATE_FORMATS = {"DMY": "%d/%m/%y %H:%M:%S", "MDY": "%m/%d/%y %H:%M:%S", "YMD": "%y/%m/%d %H:%M:%S"}

#: Records stamped before this UTC date are treated as "clock not set". The
#: GBO file's pre-sync records read 2006 and 2007 (module default clock).
DEFAULT_EARLIEST_VALID = "2020-01-01"

#: Unix epoch as MJD (1970-01-01 = MJD 40587).
MJD_UNIX_EPOCH = 40587.0

#: Mojibake or replacement characters seen in place of the degree sign.
_DEGREE_REPAIRS = ("�", "Â°", "º")
_UNIT = re.compile(r"^(?P<label>.*?)\s*\[(?P<unit>[^\]]*)\]\s*$")

#: Explanations for known option columns (shown as notes when relevant).
KNOWN_COLUMNS = {
    "Set temperature": "HCB set point of the SMC liquid temperature (SETTEMPHCB) [1, p. 16].",
    "Internal temperature": "HCB internal sensor used for regulation [1, p. 16].",
    "External temperature": "HCB external (deported) sensor, shown 'if used' [1, p. 16].",
    "Alarm Status": "HCB operation status, normal or alarm with error code [1, p. 16].",
}


# %% ===========================================================================
#    DATA CONTRACT
# =============================================================================
@dataclass
class T4NativeColumn:
    """One logged column: header text, label, unit and kind."""
    header: str                 # as repaired, e.g. "Set temperature [°C]"
    label: str                  # e.g. "Set temperature"
    unit: str                   # e.g. "°C" ("" if none)
    kind: str                   # "numeric" or "text"
    key: str                    # dataset-safe name, e.g. "set_temperature"


@dataclass
class T4NativeLog:
    """Records of one monitoring-file layout (one or more files merged).

    Every record is kept. ``clock_valid`` is False for records stamped before
    the time-server sync; those are listed in the CSV but never plotted.
    Times are the maser LAN module's own UTC stamps (1 s resolution).
    """
    name: str                   # layout name, e.g. "MonitHCB"
    columns: list[T4NativeColumn]
    utc: np.ndarray             # datetime64[s], UTC, every record
    mjd: np.ndarray             # float MJD (UTC), every record
    clock_valid: np.ndarray     # bool, every record
    numeric: dict[str, np.ndarray]
    text: dict[str, np.ndarray]
    time_text: np.ndarray       # TIME field as written in the file
    source: np.ndarray          # "file:line" of each record
    provenance: list[dict]
    notes: list[str] = field(default_factory=list)
    rejected: dict[str, int] = field(default_factory=dict)
    stats: dict = field(default_factory=dict)

    # ------------------------------------------------------------------ views
    @property
    def valid_mjd(self) -> np.ndarray:
        """MJD of clock-valid records only (for plots and analysis)."""
        return self.mjd[self.clock_valid]

    def valid(self, key: str) -> np.ndarray:
        """Values of one column for clock-valid records only."""
        values = self.numeric[key] if key in self.numeric else self.text[key]
        return values[self.clock_valid]

    def numeric_columns(self) -> list[T4NativeColumn]:
        return [c for c in self.columns if c.kind == "numeric"]

    def text_columns(self) -> list[T4NativeColumn]:
        return [c for c in self.columns if c.kind == "text"]

    def status_flag(self, key: str) -> np.ndarray:
        """0 for 'No error' (any case), 1 otherwise, NaN if empty."""
        values = self.text[key]
        flag = np.array([np.nan if not v else (0.0 if v.strip().lower() == "no error" else 1.0)
                         for v in values])
        return flag

    def frame(self) -> pd.DataFrame:
        """Every record with its flags, in time order of the merged files."""
        data = {"utc_iso": [f"{np.datetime_as_string(t, unit='s')}Z" for t in self.utc],
                "mjd_utc": self.mjd, "clock_valid": self.clock_valid.astype(int),
                "time_as_written": self.time_text}
        for column in self.columns:
            data[column.header] = (self.numeric[column.key] if column.kind == "numeric"
                                   else self.text[column.key])
        data["source"] = self.source
        return pd.DataFrame(data)

    def summary(self) -> dict:
        """Provenance and coverage for JSON outputs."""
        return {"layout": self.name, "records": int(len(self.mjd)),
                "clock_valid_records": int(self.clock_valid.sum()),
                "clock_not_set_records": int((~self.clock_valid).sum()),
                "columns": [c.header for c in self.columns], "stats": self.stats,
                "rejected": self.rejected, "provenance": self.provenance, "notes": self.notes,
                "clock": "maser LAN module time stamps, UTC (operator-confirmed); 1 s resolution",
                "reader": f"t4_native {__version__}"}


# %% ===========================================================================
#    HELPERS
# =============================================================================
def is_native_log(path: str | Path) -> bool:
    """True if the file starts with a tab-separated header whose first field is TIME."""
    with Path(path).open("rb") as handle:
        first = handle.readline(4096)
    first = first.lstrip(b"\xef\xbb\xbf").rstrip(b"\r\n")
    return first.split(b"\t")[0].strip().upper() == b"TIME" and b"\t" in first


def _decode(raw: bytes) -> tuple[str, list[str]]:
    """UTF-8 (BOM allowed), else Windows-1252; report which and any repair."""
    notes = []
    try:
        text = raw.decode("utf-8-sig")
        encoding = "UTF-8"
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
        encoding = "Windows-1252"
    notes.append(f"decoded as {encoding}")
    return text, notes


def _repair_degree(header: str) -> tuple[str, bool]:
    """Replace a lost degree sign inside a [unit] bracket, e.g. '[�C]' -> '[°C]'."""
    repaired = header
    for bad in _DEGREE_REPAIRS:
        repaired = re.sub(re.escape(bad) + r"(?=[CF]\])", "°", repaired)
    return repaired, repaired != header


def _key(label: str, used: set[str]) -> str:
    """Dataset-safe, unique key from a column label."""
    key = re.sub(r"[^0-9a-zA-Z]+", "_", label).strip("_").lower() or "column"
    base, number = key, 2
    while key in used:
        key, number = f"{base}_{number}", number + 1
    used.add(key)
    return key


def _parse_columns(header_fields: list[str]) -> tuple[list[tuple[str, str, str]], int]:
    """(header, label, unit) per data column and the number of degree repairs."""
    columns, repairs = [], 0
    for text in header_fields:
        text, repaired = _repair_degree(text.strip())
        repairs += repaired
        match = _UNIT.match(text)
        label, unit = (match["label"].strip(), match["unit"].strip()) if match else (text, "")
        columns.append((text, label, unit))
    return columns, repairs


def _layout_name(path: Path) -> str:
    """'MonitHCB.dat' and a rolled 'MonitHCB_<date>.dat' both give 'MonitHCB'."""
    stem = path.stem
    match = re.match(r"^([A-Za-z]+)", stem)
    return match.group(1) if match else stem


# %% ===========================================================================
#    ONE FILE
# =============================================================================
@dataclass
class _FileRecords:
    path: Path
    layout: str
    columns: list[tuple[str, str, str]]
    rows: list[list[str]]
    lines: list[int]
    times: list[datetime]
    notes: list[str]
    rejected: dict[str, int]
    fingerprint: dict


def _read_file(path: Path, date_order: str) -> _FileRecords:
    """Parse one file strictly; malformed lines are counted, never guessed."""
    raw = path.read_bytes()
    text, notes = _decode(raw)
    lines = text.splitlines()
    if not lines or not lines[0].split("\t")[0].strip().upper() == "TIME":
        raise ValueError(f"{path.name}: first line is not a TIME<tab>... header")
    header = lines[0].split("\t")
    columns, repairs = _parse_columns(header[1:])
    if repairs:
        notes.append(f"repaired {repairs} lost degree sign(s) in the header (U+FFFD in the file)")
    if raw.count(b"\r\n") != raw.count(b"\n"):
        notes.append("mixed line endings")
    fmt = DATE_FORMATS[date_order]
    rows, numbers, times, rejected = [], [], [], {}
    for number, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != len(header):
            rejected["WRONG_FIELD_COUNT"] = rejected.get("WRONG_FIELD_COUNT", 0) + 1
            continue
        try:
            stamp = datetime.strptime(fields[0].strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            rejected["BAD_TIME"] = rejected.get("BAD_TIME", 0) + 1
            continue
        rows.append([f.strip() for f in fields[1:]])
        numbers.append(number)
        times.append(stamp)
    if rejected.get("BAD_TIME"):
        # A wrong date order can still read most lines (05/11/26 is a valid
        # MM/DD/YY too). Say so if another order reads every time field.
        stamps = [line.split("\t")[0].strip() for line in lines[1:]
                  if line.strip() and len(line.split("\t")) == len(header)]
        for other, other_fmt in DATE_FORMATS.items():
            if other == date_order:
                continue
            try:
                for stamp_text in stamps:
                    datetime.strptime(stamp_text, other_fmt)
            except ValueError:
                continue
            notes.append(f"WARNING: {rejected['BAD_TIME']} time stamp(s) unreadable as {date_order} but every "
                         f"line reads as {other}; check the date order")
    digest = hashlib.sha256(raw).hexdigest()
    return _FileRecords(path, _layout_name(path), columns, rows, numbers, times, notes, rejected,
                        {"name": path.name, "sha256": digest, "bytes": len(raw)})


# %% ===========================================================================
#    MERGE AND FLAG
# =============================================================================
def _build(name: str, parts: list[_FileRecords], earliest_valid: str, now: datetime) -> T4NativeLog:
    """Merge files of one layout, flag clock-not-set records, compute statistics."""
    columns_spec = parts[0].columns
    records, notes, rejected = [], [], {}
    for part in parts:
        notes += [f"{part.path.name}: {n}" for n in part.notes]
        for reason, count in part.rejected.items():
            rejected[reason] = rejected.get(reason, 0) + count
        for stamp, row, line in zip(part.times, part.rows, part.lines, strict=True):
            records.append((stamp, tuple(row), f"{part.path.name}:{line}"))
    # Identical records (same time and values) appear when a rolled copy and
    # the live file overlap: keep one. Same time, different values: keep both
    # and report, because the cause is unknown.
    by_time: dict[datetime, set[tuple]] = {}
    unique, conflicts = [], 0
    for stamp, row, source in records:
        rows_at = by_time.setdefault(stamp, set())
        if row in rows_at:
            continue
        if rows_at:
            conflicts += 1
        rows_at.add(row)
        unique.append((stamp, row, source))
    if len(unique) != len(records):
        notes.append(f"removed {len(records) - len(unique)} identical duplicate record(s)")
    if conflicts:
        notes.append(f"{conflicts} time stamp(s) carry different values in different records (kept)")
    # File order is kept inside each file; files are ordered by their latest
    # record (a pre-sync 2006 stamp must not put the newest file first).
    threshold = datetime.fromisoformat(earliest_valid).replace(tzinfo=timezone.utc)
    future = now.timestamp() + 86400.0
    utc = np.array([np.datetime64(s.replace(tzinfo=None), "s") for s, _, _ in unique])
    epoch = np.array([s.timestamp() for s, _, _ in unique], float)
    clock_valid = (epoch >= threshold.timestamp()) & (epoch <= future)
    mjd = epoch / 86400.0 + MJD_UNIX_EPOCH
    used: set[str] = set()
    columns, numeric, text = [], {}, {}
    for index, (header, label, unit) in enumerate(columns_spec):
        values = [row[index] for _, row, _ in unique]
        key = _key(label, used)
        parsed, failed = [], 0
        for value in values:
            try:
                parsed.append(float(value) if value else np.nan)
            except ValueError:
                parsed.append(np.nan)
                failed += 1
        filled = sum(1 for value in values if value)
        # Numeric if every filled value parses; tolerate up to 10 % odd entries
        # (reported); otherwise keep the column as text.
        if filled and failed <= 0.1 * filled and failed < filled:
            kind = "numeric"
            numeric[key] = np.array(parsed, float)
            if failed:
                notes.append(f"'{header}': {failed} non-numeric value(s) set to NaN")
        else:
            kind = "text"
            text[key] = np.array(values, dtype=object)
        columns.append(T4NativeColumn(header, label, unit, kind, key))
    # Statistics on clock-valid records, in record order.
    stats = {"records": int(len(unique)), "clock_valid": int(clock_valid.sum()),
             "clock_not_set": int((~clock_valid).sum())}
    if (~clock_valid).any():
        bad = np.flatnonzero(~clock_valid)
        # A new run starts where records are not adjacent, or where the
        # unset clock itself jumps (backwards, or forwards by over an hour),
        # e.g. 2006 -> 2007 after a restart before the time-server sync.
        step = np.diff(epoch[bad])
        breaks = (np.diff(bad) != 1) | (step < 0) | (step > 3600.0)
        segments = np.split(bad, np.flatnonzero(breaks) + 1)
        stats["clock_not_set_segments"] = [
            {"first": unique[s[0]][2], "count": int(len(s)),
             "stamped_from": str(utc[s[0]]), "stamped_to": str(utc[s[-1]])} for s in segments]
        notes.append(f"{int((~clock_valid).sum())} record(s) in {len(segments)} run(s) stamped before "
                     f"{earliest_valid} (clock not yet set by the time server): kept in the CSV, "
                     f"not plotted")
    good = np.flatnonzero(clock_valid)
    if len(good) >= 2:
        steps = np.diff(epoch[good])
        median = float(np.median(steps))
        stats.update({"first_utc": str(utc[good[0]]) + "Z", "last_utc": str(utc[good[-1]]) + "Z",
                      "first_mjd": float(mjd[good[0]]), "last_mjd": float(mjd[good[-1]]),
                      "interval_s": {"median": median, "mean": float(steps.mean()),
                                     "min": float(steps.min()), "max": float(steps.max())},
                      "backward_steps": int((steps < 0).sum()),
                      "gaps_over_1p5_median": int((steps > 1.5 * median).sum())})
        if stats["backward_steps"]:
            notes.append(f"{stats['backward_steps']} backward time step(s) among clock-valid records")
    for column in columns:
        values = numeric.get(column.key, text.get(column.key))[clock_valid]
        finite = values[np.isfinite(values)] if column.kind == "numeric" else values
        if len(finite) and len(set(finite.tolist())) == 1:
            hint = (" Probably no external sensor connected (unverified)."
                    if column.label == "External temperature" else "")
            notes.append(f"'{column.header}' is constant ({finite[0]}) over all clock-valid records.{hint}")
        if column.label in KNOWN_COLUMNS:
            stats.setdefault("column_meaning", {})[column.header] = KNOWN_COLUMNS[column.label]
    return T4NativeLog(name, columns, utc, mjd, clock_valid, numeric, text,
                       np.array([s.strftime(DATE_FORMATS["DMY"]) for s, _, _ in unique], dtype=object),
                       np.array([src for _, _, src in unique], dtype=object),
                       [p.fingerprint for p in parts], notes, rejected, stats)


def load_native_logs(paths, date_order: str = "DMY", earliest_valid: str = DEFAULT_EARLIEST_VALID,
                     now: datetime | None = None, workers: int | None = None) -> list[T4NativeLog]:
    """Read T4 monitoring files; one T4NativeLog per header layout.

    Files with the same header (for example the live MonitHCB.dat and its
    rolled, date-tagged copies) are merged in order of their first record.
    Files with different headers stay separate logs.
    """
    if date_order not in DATE_FORMATS:
        raise ValueError(f"date_order must be one of {sorted(DATE_FORMATS)}")
    paths = [Path(p) for p in paths]
    if not paths:
        raise ValueError("no T4 monitoring files given")
    now = now or datetime.now(timezone.utc)
    with ThreadPoolExecutor(max_workers=workers or min(8, len(paths))) as pool:
        parts = list(pool.map(lambda p: _read_file(p, date_order), paths))
    groups: dict[tuple, list[_FileRecords]] = {}
    for part in parts:
        groups.setdefault(tuple(h for h, _, _ in part.columns), []).append(part)
    logs = []
    for group in groups.values():
        group.sort(key=lambda p: max(p.times) if p.times else datetime.max.replace(tzinfo=timezone.utc))
        names = sorted({p.layout for p in group})
        log = _build("+".join(names), group, earliest_valid, now)
        if not len(log.mjd):
            raise ValueError(f"{', '.join(p.path.name for p in group)}: no readable records "
                             f"(rejections: {log.rejected or 'none'})")
        logs.append(log)
    return logs


# %% ===========================================================================
#    COMMAND LINE
# =============================================================================
def main(argv=None) -> int:
    """Read monitoring files and write CSV, JSON and .vszh5 into a NEW folder."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", type=Path, help="files from the T4 ./Monitoring folder")
    parser.add_argument("--output", type=Path, required=True, help="new results folder")
    parser.add_argument("--date-order", choices=sorted(DATE_FORMATS), default="DMY")
    parser.add_argument("--earliest-valid", default=DEFAULT_EARLIEST_VALID,
                        help="records stamped before this UTC date are 'clock not set'")
    arguments = parser.parse_args(argv)
    logs = load_native_logs(arguments.files, arguments.date_order, arguments.earliest_valid)
    from maser_export import export_t4_only
    export_t4_only(arguments.output, None, logs)
    for log in logs:
        stats = log.stats
        print(f"{log.name}: {stats['records']} records, {stats['clock_valid']} clock-valid "
              f"({stats.get('first_utc', '?')} to {stats.get('last_utc', '?')})")
        for note in log.notes:
            print("  -", note)
    print(f"Written to {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
