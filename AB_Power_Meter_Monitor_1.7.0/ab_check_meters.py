#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace <wwallace@nrao.edu>
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Are the meters really different? Live and file comparison of AB meters.

===============================================================================
 Title          : AB Power Meter identity and similarity check
 Filename       : ab_check_meters.py
 Version        : 0.0.1 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu   (alt: naval.antennas@gmail.com)
 Created        : 2026-10-27
 Modified       : 2026-10-27
 Language       : Python 3.7.11+ and 3.12 (numpy optional for the file mode)
 Requires       : ab_power_meter_monitor.py >= 1.6.5 (live mode)
 Licence        : GPL-3.0-or-later (GNU General Public License v3.0 or later)
 Status         : Internal draft - verified against the meter emulator
===============================================================================

WHY
---
The GUI plots of two real meters looked alike. Either the meters measure
similar loads, or the data of one meter is filed under the other. This tool
tells the cases apart. It writes NO files (unless --markdown is given).

LIVE MODE   python ab_check_meters.py --ips 10.16.130.50,10.16.130.54 [--samples 3 --interval 10]
  Reads all 11 pages of every meter - pages one at a time per meter, meters
  in parallel, exactly like the monitor - and shows per meter:
    * the address the meter reports for itself (page 1) and its identity
      text (catalog, series, serial, firmware - page 0);
    * a short SHA-256 of every page's HTML;
  and per table, between the first two meters:
    * whether the pages are byte-identical, how many numeric readings are
      exactly equal, the median relative difference, and an example.
  With --samples > 1 it also counts how many readings of each meter changed
  between samples (a live meter's measurements change).

FILE MODE   python ab_check_meters.py --csv-dir <output folder>/csv
  Compares the monitor's CSV files of two meters (ABMeter_<ip>_<table>.csv):
  rows matched by time stamp (within --tolerance s), per parameter the share
  of exactly equal values, the median relative difference and the
  correlation. Nothing is polled.

VERDICT
-------
  SAME SOURCE     the meters report the same address or serial number, or
                  every page / value is identical -> one meter answers at both
                  addresses (duplicate IP, NAT, proxy) or files were mixed
  WRONG ADDRESS   a meter reports an address other than the one polled
  NEARLY EQUAL    readings differ by well under 1 % -> same source read at
                  slightly different times, or two meters on one circuit
  DIFFERENT       readings differ -> genuinely separate meters; similar plots
                  then mean similar loads (compare the numbers, not the shapes)

REVISION HISTORY
----------------
0.0.1  2026-10-27  First internal draft (monitor 1.6.5).
===============================================================================
"""

# %% ===========================================================================
#    IMPORTS
# =============================================================================
from __future__ import annotations

import sys

sys.dont_write_bytecode = True          # leave no __pycache__ behind

import argparse  # noqa: E402
import concurrent.futures  # noqa: E402
import csv  # noqa: E402
import datetime  # noqa: E402
import glob  # noqa: E402
import hashlib  # noqa: E402
import math  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import statistics  # noqa: E402
import time  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence, Tuple  # noqa: E402

__version__ = "0.0.1"

#: Relative difference below which two readings count as "equal" (besides exact).
EQUAL_REL = 1e-9
#: Median relative difference below which two meters count as "nearly equal".
NEARLY_EQUAL_REL = 0.005


# %% ===========================================================================
#    SMALL HELPERS
# =============================================================================
def _rel_diff(a: float, b: float) -> float:
    """|a - b| / max(|a|, |b|), 0 for two zeros."""
    scale = max(abs(a), abs(b))
    return 0.0 if scale == 0 else abs(a - b) / scale


def _fmt(value: Any) -> str:
    """Compact number formatting for the tables."""
    if isinstance(value, float):
        return "{0:.6g}".format(value)
    return str(value)


def _table(rows: List[List[Any]], header: List[str]) -> str:
    """Plain-text table (also valid Markdown) of ``rows``."""
    text = [[str(h) for h in header]] + [[_fmt(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in text) for i in range(len(header))]
    lines = ["| " + " | ".join(c.ljust(w) for c, w in zip(r, widths)) + " |" for r in text]
    lines.insert(1, "|" + "|".join("-" * (w + 2) for w in widths) + "|")
    return "\n".join(lines)


# %% ===========================================================================
#    LIVE MODE
# =============================================================================
def read_meter(abm: Any, ip: str, timeout: float, retries: int) -> Dict[str, Any]:
    """All pages of one meter, one at a time (one keep-alive session).

    Returns {"pages": {table: {"sha": str, "bytes": int, "values": {p: (v, unit)},
    "text": {p: str}}}, "reported_ip": str|None, "errors": [str]}.
    """
    import requests
    out: Dict[str, Any] = {"pages": {}, "reported_ip": None, "errors": []}
    with requests.Session() as session:
        for page, tname in sorted(abm.TABLE_NAMES.items()):
            html = abm.fetch_table_html(ip, page, timeout=timeout, retries=retries, session=session)
            if html is None:
                out["errors"].append(tname)
                continue
            parsed = abm.parse_html_table(html, tname, ip, page)
            texts = {}
            for key, name in parsed.items():
                if key.startswith("#") and not key.endswith(("_value", "_unit")):
                    value = parsed.get(key + "_value")
                    if isinstance(value, str):
                        texts[str(name)] = value
            out["pages"][tname] = {"sha": hashlib.sha256(html.encode("utf-8", "replace")).hexdigest()[:12],
                                   "bytes": len(html), "values": abm.extract_numeric_series(parsed),
                                   "text": texts}
            if tname == "Communications_Configuration_Table":
                out["reported_ip"] = abm.reported_ip(parsed)
    return out


def read_all(abm: Any, ips: Sequence[str], timeout: float, retries: int) -> Dict[str, Dict[str, Any]]:
    """read_meter() for every meter in parallel (pages serial inside each)."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(ips), thread_name_prefix="abcheck") as pool:
        futures = {ip: pool.submit(read_meter, abm, ip, timeout, retries) for ip in ips}
        return {ip: f.result() for ip, f in futures.items()}


def live_report(abm: Any, ips: List[str], samples: int, interval: float, timeout: float,
                retries: int) -> Tuple[str, str]:
    """(report text, verdict) of the live comparison."""
    runs = []
    for index in range(samples):
        started = time.monotonic()
        runs.append(read_all(abm, ips, timeout, retries))
        if index + 1 < samples:
            time.sleep(max(0.0, interval - (time.monotonic() - started)))
    first = runs[0]
    parts = ["Live check of {0} (monitor {1}, {2} sample(s))".format(", ".join(ips), abm.__version__, samples)]
    # --- identity -----------------------------------------------------------------
    rows = []
    for ip in ips:
        meter = first[ip]
        ident = meter["pages"].get("Device_Configuration_Table", {}).get("text", {})
        rows.append([ip, meter["reported_ip"] or "(not listed)", len(meter["pages"]),
                     ", ".join(meter["errors"]) or "-",
                     "; ".join("{0}={1}".format(k, v) for k, v in sorted(ident.items())) or "-"])
    parts += ["", "Identity (page 0 text, page 1 address):",
              _table(rows, ["polled IP", "reports IP", "pages read", "failed pages", "identity text"])]
    # --- per table comparison of the first two meters ----------------------------------
    verdicts: List[str] = []
    wrong = [ip for ip in ips if first[ip]["reported_ip"] and first[ip]["reported_ip"] != ip]
    if wrong:
        verdicts.append("WRONG ADDRESS: " + ", ".join("{0} reports {1}".format(ip, first[ip]["reported_ip"])
                                                     for ip in wrong))
    if len(ips) >= 2:
        a, b = ips[0], ips[1]
        reported = [first[a]["reported_ip"], first[b]["reported_ip"]]
        if reported[0] and reported[0] == reported[1]:
            verdicts.append("SAME SOURCE: both answer with address {0}".format(reported[0]))
        ident_a = first[a]["pages"].get("Device_Configuration_Table", {}).get("text", {})
        ident_b = first[b]["pages"].get("Device_Configuration_Table", {}).get("text", {})
        for key in sorted(set(ident_a) & set(ident_b)):
            if "serial" in key.lower() and ident_a[key] == ident_b[key]:
                verdicts.append("SAME SOURCE: same {0} {1}".format(key, ident_a[key]))
        rows, equal_total, common_total, rels = [], 0, 0, []
        for tname in sorted(abm.TABLE_NAMES.values(), key=abm._table_order):
            pa, pb = first[a]["pages"].get(tname), first[b]["pages"].get(tname)
            if not pa or not pb:
                rows.append([tname, "-", "-", "-", "-", "page missing"])
                continue
            common = sorted(set(pa["values"]) & set(pb["values"]))
            equal = [p for p in common if _rel_diff(pa["values"][p][0], pb["values"][p][0]) <= EQUAL_REL]
            diffs = [_rel_diff(pa["values"][p][0], pb["values"][p][0]) for p in common]
            rels += diffs
            equal_total += len(equal)
            common_total += len(common)
            example = next((p for p in common if p not in equal), common[0] if common else "")
            ex = ("{0}: {1} / {2}".format(example, _fmt(pa["values"][example][0]), _fmt(pb["values"][example][0]))
                  if example else "-")
            rows.append([tname, "yes" if pa["sha"] == pb["sha"] else "no", len(common), len(equal),
                         "{0:.3%}".format(statistics.median(diffs)) if diffs else "-", ex])
        parts += ["", "Tables of {0} vs {1}:".format(a, b),
                  _table(rows, ["table", "identical page", "readings", "equal", "median rel. diff",
                                "example (first / second)"])]
        identical_pages = sum(1 for r in rows if r[1] == "yes")
        median_rel = statistics.median(rels) if rels else 0.0
        if common_total and equal_total == common_total:
            verdicts.append("SAME SOURCE: all {0} readings equal ({1} identical pages)".format(
                common_total, identical_pages))
        elif common_total and median_rel < NEARLY_EQUAL_REL:
            verdicts.append("NEARLY EQUAL: {0} of {1} readings differ, but the median relative difference "
                            "is only {2:.3%} - one source read at slightly different times, or two meters "
                            "on the same circuit".format(common_total - equal_total, common_total, median_rel))
        elif common_total:
            verdicts.append("DIFFERENT: {0} of {1} readings differ, median relative difference {2:.2%}".format(
                common_total - equal_total, common_total, median_rel))
    # --- change between samples ------------------------------------------------------
    if samples > 1:
        rows = []
        for ip in ips:
            v0 = {(t, p): v for t, page in runs[0][ip]["pages"].items() for p, (v, _u) in page["values"].items()}
            v1 = {(t, p): v for t, page in runs[-1][ip]["pages"].items() for p, (v, _u) in page["values"].items()}
            both = set(v0) & set(v1)
            rows.append([ip, len(both), sum(1 for k in both if v0[k] != v1[k])])
        parts += ["", "Readings that changed between the first and last sample:",
                  _table(rows, ["meter", "readings", "changed"])]
    order = ("SAME SOURCE", "WRONG ADDRESS", "NEARLY EQUAL", "DIFFERENT")
    verdicts.sort(key=lambda v: next((i for i, k in enumerate(order) if v.startswith(k)), len(order)))
    if any(v.startswith("SAME SOURCE") for v in verdicts):           # DIFFERENT would only confuse
        verdicts = [v for v in verdicts if not v.startswith(("DIFFERENT", "NEARLY EQUAL"))]
    verdict = "; ".join(verdicts) if verdicts else "no comparison possible (need two meters that answer)"
    parts += ["", "VERDICT: " + verdict]
    return "\n".join(parts), verdict


# %% ===========================================================================
#    FILE MODE (monitor CSV output)
# =============================================================================
_CSV_RE = re.compile(r"^ABMeter_(.+?)_([A-Za-z_]+_Table(?:_Extended)?)\.csv$")


def _read_csv(path: str) -> Tuple[List[float], Dict[str, List[Optional[float]]]]:
    """(Unix times, {column: values}) of one monitor CSV (header, units, rows)."""
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    if len(rows) < 3:
        return [], {}
    header = rows[0]
    times, cols = [], {name: [] for name in header[1:]}
    for row in rows[2:]:
        try:
            stamp = datetime.datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").timestamp()
        except (ValueError, IndexError):
            continue
        times.append(stamp)
        for i, name in enumerate(header[1:], start=1):
            try:
                cols[name].append(float(row[i]))
            except (ValueError, IndexError):
                cols[name].append(None)
    return times, cols


def _pearson(x: List[float], y: List[float]) -> Optional[float]:
    """Correlation coefficient, None when undefined."""
    if len(x) < 3:
        return None
    mx, my = statistics.mean(x), statistics.mean(y)
    sx = math.sqrt(sum((v - mx) ** 2 for v in x))
    sy = math.sqrt(sum((v - my) ** 2 for v in y))
    if sx == 0 or sy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)


def file_report(csv_dir: str, ips: Optional[List[str]], tolerance: float) -> Tuple[str, str]:
    """(report text, verdict) comparing the CSV files of two meters."""
    found: Dict[str, Dict[str, str]] = {}
    for path in glob.glob(os.path.join(csv_dir, "ABMeter_*.csv")):
        match = _CSV_RE.match(os.path.basename(path))
        if match:
            found.setdefault(match.group(1), {})[match.group(2)] = path
    tags = sorted(found)
    if ips:
        tags = [ip.replace(".", "_").replace(":", "_") for ip in ips]
    if len(tags) < 2 or any(t not in found for t in tags[:2]):
        return ("CSV files of two meters not found in {0} (found: {1})".format(csv_dir, ", ".join(sorted(found))),
                "no comparison possible")
    a, b = tags[0], tags[1]
    rows, equal_n, total_n, rels = [], 0, 0, []
    for tname in sorted(set(found[a]) & set(found[b])):
        ta, ca = _read_csv(found[a][tname])
        tb, cb = _read_csv(found[b][tname])
        pairs, j = [], 0
        for i, t in enumerate(ta):                       # nearest row of b within the tolerance
            while j + 1 < len(tb) and abs(tb[j + 1] - t) <= abs(tb[j] - t):
                j += 1
            if tb and abs(tb[j] - t) <= tolerance:
                pairs.append((i, j))
        for param in sorted(set(ca) & set(cb)):
            xs, ys = [], []
            for i, k in pairs:
                va, vb = ca[param][i], cb[param][k]
                if va is not None and vb is not None:
                    xs.append(va)
                    ys.append(vb)
            if not xs:
                continue
            eq = sum(1 for x, y in zip(xs, ys) if _rel_diff(x, y) <= EQUAL_REL)
            rd = statistics.median(_rel_diff(x, y) for x, y in zip(xs, ys))
            r = _pearson(xs, ys)
            equal_n += eq
            total_n += len(xs)
            rels.append(rd)
            rows.append([tname, param, len(xs), "{0:.0%}".format(eq / len(xs)), "{0:.3%}".format(rd),
                         "-" if r is None else "{0:.3f}".format(r)])
    rows.sort(key=lambda r: (-float(r[3].rstrip("%")), float(r[4].rstrip("%"))))
    head = ["table", "parameter", "matched rows", "exactly equal", "median rel. diff", "correlation"]
    text = ["File check of {0}: {1} vs {2} (rows matched within {3:g} s)".format(csv_dir, a, b, tolerance), "",
            "Most similar parameters first (top 25 of {0}):".format(len(rows)), _table(rows[:25], head)]
    if not total_n:
        verdict = "no overlapping rows (time stamps further apart than --tolerance)"
    elif equal_n == total_n:
        verdict = "SAME SOURCE: all {0} matched readings are equal - the files hold the same data".format(total_n)
    elif statistics.median(rels) < NEARLY_EQUAL_REL:
        verdict = ("NEARLY EQUAL: {0} of {1} matched readings differ, median relative difference only "
                   "{2:.3%}".format(total_n - equal_n, total_n, statistics.median(rels)))
    else:
        verdict = ("DIFFERENT: {0} of {1} matched readings differ; median relative difference over "
                   "parameters {2:.2%}".format(total_n - equal_n, total_n, statistics.median(rels)))
    text += ["", "VERDICT: " + verdict]
    return "\n".join(text), verdict


# %% ===========================================================================
#    COMMAND LINE
# =============================================================================
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--ips", help="meters (default: the monitor's IP_LIST)")
    parser.add_argument("--url-template", dest="url_template", help='e.g. "http://{ip}/{page}"')
    parser.add_argument("--samples", type=int, default=1, help="live: number of readings of every meter")
    parser.add_argument("--interval", type=float, default=10.0, help="live: seconds between samples")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--csv-dir", dest="csv_dir", help="file mode: the monitor's csv output folder")
    parser.add_argument("--tolerance", type=float, default=5.0, help="file mode: max time offset (s)")
    parser.add_argument("--markdown", help="also write the report to this Markdown file")
    args = parser.parse_args(argv)
    ips = [ip.strip() for ip in args.ips.split(",") if ip.strip()] if args.ips else None
    if args.csv_dir:
        report, verdict = file_report(args.csv_dir, ips, args.tolerance)
    else:
        import logging
        import ab_power_meter_monitor as abm
        abm.set_console_logging(False)
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
        logging.getLogger("ABMonitor").setLevel(logging.WARNING)   # no per-page DEBUG lines
        if args.url_template:
            abm.METER_URL_TEMPLATE = args.url_template
        report, verdict = live_report(abm, abm.dedupe_ips(ips or abm.IP_LIST), max(1, args.samples),
                                      args.interval, args.timeout, args.retries)
    print(report)
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as handle:
            handle.write("# AB meter check ({0})\n\n```\n{1}\n```\n".format(
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), report))
    return 0 if verdict.startswith("DIFFERENT") else 1


if __name__ == "__main__":
    sys.exit(main())
