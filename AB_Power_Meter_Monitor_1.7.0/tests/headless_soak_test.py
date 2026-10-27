#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace <wwallace@nrao.edu>
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""End-to-end headless soak: the real command line, every output, flushes, stop.

===============================================================================
 Title          : AB Power Meter Monitor - headless end-to-end soak test
 Filename       : tests/headless_soak_test.py
 Version        : 0.0.2 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu
 Created        : 2026-10-26
 Modified       : 2026-10-26
 Language       : Python 3.8 through 3.12 (Windows and Linux)
 Requires       : psutil, numpy, h5py (+ monitor dependencies); vszh5_writer
 Licence        : GPL-3.0-or-later (GNU General Public License v3.0 or later)
 Status         : Internal draft
===============================================================================

What it does
------------
1. Starts the meter emulator (two meters) in this process.
2. Starts the monitor as a SEPARATE process through its own command line
   (``--headless --count 0``) with CSV, Markdown, .vszh5, FITS, XLSX and the
   log file enabled, a 1 s poll period and a small store limit, so automatic
   RAM flushes (with the OS memory release) happen every ~30 s.
3. Samples the monitor process every few seconds: working set (W), commit
   charge (C, Windows), handles (Windows) or open files (POSIX), threads,
   polls and flushes.
4. Stops it the way an operator would: Ctrl+Break (CTRL_BREAK_EVENT) on
   Windows, SIGTERM elsewhere; if that is not delivered within 3 min, the
   STOP_COLLECTION file (ab_stop.py).
5. Checks the result and writes ``soak_samples.csv``, ``soak_memory.vszh5``
   (open in Veusz) and ``soak_result.json`` to the output folder.

Pass criteria
-------------
* the monitor exits with code 0 and logs "Final writes done";
* every table holds the same number of rows in CSV, Markdown, .vszh5, XLSX
  and FITS, and that number is the poll count (or one less: a stop can cut
  the last poll short) - no row lost, none written twice;
* no *_rescue* / *.merging file is left, no ERROR in the log;
* at least one automatic RAM flush happened;
* after the warm-up (first 25 % of the samples), the working set grows by at
  most 40 MiB between the second quarter and the last quarter of the run, and
  on Windows the handle count by at most 100 (no leak);
* Windows: the Ctrl+Break stop worked (falling back to STOP_COLLECTION is
  reported as WARN; under Wine, which has no console control events, it is
  expected).

Usage
-----
    python tests/headless_soak_test.py                    # 10 min
    python tests/headless_soak_test.py --minutes 60 --out C:\\ABTest\\soak
    python tests/headless_soak_test.py --minutes 2        # quick look

REVISION HISTORY
----------------
0.0.2  2026-10-26  --out that already holds files gets a new run_<time> subfolder:
                   appending to an earlier run's files made the row check
                   compare two runs' rows with one run's polls.
0.0.1  2026-10-26  First version (monitor 1.6.2).
===============================================================================
"""

# %% IMPORTS ===================================================================
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
sys.path.insert(0, str(APP))
sys.path.insert(0, str(HERE))

import psutil  # noqa: E402
from ab_meter_emulator import start_emulators  # noqa: E402

WINDOWS = sys.platform == "win32"
HOSTS = ["127.0.0.2", "127.0.0.3"]
TABLE = "Real_Time_Power_Table"            # the table whose rows are compared
POLL_RE = re.compile(r"\| INFO\s+\| ABMonitor \| Poll (\d+) done")

# Run inside the monitor process: apply the switches, then the real CLI path.
_CHILD = r'''
import json, sys
settings = json.loads(sys.argv[1])
sys.path.insert(0, settings["app"])
import ab_power_meter_monitor as abm
for name, value in settings["switches"].items():
    setattr(abm, name, value)
abm._parse_cli_and_apply(settings["argv"])
abm.main()
'''


# %% HELPERS ===================================================================
def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _running_under_wine() -> bool:
    try:
        from windows_memory_check import running_under_wine
        return running_under_wine()
    except Exception:
        return False


def _sample(proc: psutil.Process) -> dict:
    """One measurement of the monitor process (MiB, counts)."""
    info = proc.memory_info()
    row = {"W": info.rss / 2 ** 20,
           "C": (info.vms / 2 ** 20) if WINDOWS else float("nan"),   # vms = pagefile on Windows
           "threads": proc.num_threads()}
    try:
        row["handles"] = proc.num_handles() if WINDOWS else proc.num_fds()
    except (psutil.Error, AttributeError):
        row["handles"] = float("nan")
    return row


class _LogTail:
    """Reads ab_monitor.log incrementally; counts polls, flushes and errors."""

    def __init__(self, path: str) -> None:
        self.path, self.offset = path, 0
        self.polls, self.flushes, self.errors, self.final = 0, 0, [], False

    def update(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as handle:
                handle.seek(self.offset)
                text = handle.read()
                self.offset = handle.tell()
        except OSError:
            return
        for line in text.splitlines():
            match = POLL_RE.search(line)
            if match:
                self.polls = max(self.polls, int(match.group(1)))
            if "[RAM Flush]" in line and "| WARNING" in line:
                self.flushes += 1
            if "| ERROR" in line or "| CRITICAL" in line:
                self.errors.append(line[:300])
            if "Final writes done" in line:
                self.final = True


def _rows_per_output(out: str, ip: str) -> dict:
    """Rows of TABLE in every output for one meter."""
    import h5py
    import openpyxl
    from astropy.io import fits
    rows = {}
    with open(os.path.join(out, "csv", f"ABMeter_{ip}_{TABLE}.csv"), newline="", encoding="utf-8") as h:
        rows["csv"] = sum(1 for r in list(csv.reader(h))[2:] if r)
    with open(os.path.join(out, "logs", f"ABMeter_{ip}_{TABLE}.md"), encoding="utf-8") as h:
        rows["md"] = sum(1 for line in h if line.startswith("| ") and not line.startswith("| Timestamp_Local"))
    vsz = sorted(glob.glob(os.path.join(out, "veusz", f"ABMeter_{ip}*.vszh5")))
    total = 0
    for path in vsz:                                   # rollover would split the rows
        with h5py.File(path, "r") as hdf:
            names = [n for n in hdf["Veusz/Data"] if TABLE in n]
            total += max((hdf[f"Veusz/Data/{n}/data"].shape[0] for n in names), default=0)
    rows["vszh5"] = total
    book = openpyxl.load_workbook(os.path.join(out, "xlsx", f"ABMeter_{ip}.xlsx"), read_only=True)
    sheet = next(ws for ws in book.worksheets if ws.title.startswith(TABLE[:31]))
    rows["xlsx"] = sum(1 for _ in sheet.iter_rows()) - 2
    book.close()
    with fits.open(os.path.join(out, "fits", f"ABMeter_{ip}.fits")) as hdul:
        rows["fits"] = len(hdul[TABLE].data)
    return rows


def _write_reports(out: str, samples: list, summary: dict) -> None:
    """soak_samples.csv, soak_memory.vszh5 (Veusz) and soak_result.json."""
    keys = ["t_min", "W", "C", "handles", "threads", "polls", "flushes"]
    with open(os.path.join(out, "soak_samples.csv"), "w", newline="", encoding="utf-8") as h:
        writer = csv.writer(h)
        writer.writerow(keys)
        writer.writerows([[s[k] for k in keys] for s in samples])
    try:
        import numpy as np
        from vszh5_writer import Trace, VeuszWriter, VszDoc, xy_page
        doc = VszDoc(generator="headless_soak_test 0.0.1")
        xy_page(doc, "memory", [Trace("t_min", "W", "working set"), Trace("t_min", "C", "commit")],
                title="Monitor process memory (headless soak)", xlabel="Elapsed time (min)",
                ylabel="MiB")
        xy_page(doc, "handles_threads", [Trace("t_min", "handles", "handles / open files"),
                                         Trace("t_min", "threads", "threads")],
                title="Handles and threads", xlabel="Elapsed time (min)", ylabel="count")
        xy_page(doc, "polls_flushes", [Trace("t_min", "polls", "polls"),
                                       Trace("t_min", "flushes", "RAM flushes")],
                title="Polls and RAM flushes", xlabel="Elapsed time (min)", ylabel="count")
        with VeuszWriter(os.path.join(out, "soak_memory.vszh5"), writer_tag="headless_soak_test 0.0.1") as w:
            for k in keys:
                w.dataset_1d(k, np.array([s[k] for s in samples], dtype=float))
            w.finish(doc.text())
    except Exception as exc:                              # report only - never fail the test
        summary["vszh5_report_error"] = str(exc)
    with open(os.path.join(out, "soak_result.json"), "w", encoding="utf-8") as h:
        json.dump({"summary": summary, "samples": samples}, h, indent=2)


# %% MAIN ========================================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="Headless end-to-end soak of the AB monitor.")
    parser.add_argument("--minutes", type=float, default=10.0, help="run time before the stop")
    parser.add_argument("--interval", type=float, default=1.0, help="poll period (s)")
    parser.add_argument("--store-mb", type=float, default=0.25, help="automatic flush store limit (MB)")
    parser.add_argument("--sample-sec", type=float, default=5.0, help="measurement period (s)")
    parser.add_argument("--out", default="", help="output folder (default: a new temp folder)")
    args = parser.parse_args()

    out = os.path.abspath(args.out) if args.out else tempfile.mkdtemp(prefix="abm_soak_")
    if os.path.isdir(out) and os.listdir(out):          # never append to an earlier run
        out = os.path.join(out, time.strftime("run_%Y%m%d_%H%M%S"))
    os.makedirs(out, exist_ok=True)
    port = _free_port()
    start_emulators(HOSTS, port)
    url = "http://{ip}:%d/{page}" % port
    settings = {
        "app": str(APP),
        "switches": {"ENABLE_CSV": 1, "ENABLE_LOG_APPEND": 1, "ENABLE_VEUSZ": 1, "ENABLE_FITS": 1,
                     "ENABLE_XLSX": 1, "ENABLE_LOG_FILE": 1, "FULL_WRITE_EVERY_N_CYCLES": 10,
                     "MEM_FLUSH_THRESHOLD_MB": args.store_mb, "MEM_FLUSH_MIN_INTERVAL_SEC": 30,
                     "MEM_RAM_PCT_LIMIT": 0, "MEM_FREE_MIN_MB": 0, "HEADLESS_SILENT": 1,
                     "HEADLESS_MAX_CONSEC_FAILS": 0, "HTTP_TIMEOUT_SEC": 3},
        "argv": ["--headless", "--count", "0", "--interval", str(args.interval),
                 "--ips", ",".join(HOSTS), "--url-template", url, "--output-dir", out],
    }
    console = open(os.path.join(out, "monitor_console.log"), "w", encoding="utf-8")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0      # Ctrl+Break target
    child = subprocess.Popen([sys.executable, "-X", "utf8", "-c", _CHILD, json.dumps(settings)],
                             stdout=console, stderr=subprocess.STDOUT, creationflags=flags)
    proc = psutil.Process(child.pid)
    tail = _LogTail(os.path.join(out, "logs", "ab_monitor.log"))
    print(f"Monitor pid {child.pid}; output {out}; running {args.minutes:g} min "
          f"(poll {args.interval:g} s, flush above {args.store_mb:g} MB store)", flush=True)
    samples, started = [], time.monotonic()
    try:
        while time.monotonic() - started < args.minutes * 60:
            time.sleep(args.sample_sec)
            if child.poll() is not None:
                print(f"Monitor exited early with code {child.returncode}", flush=True)
                break
            tail.update()
            row = _sample(proc)
            row.update(t_min=(time.monotonic() - started) / 60.0, polls=tail.polls, flushes=tail.flushes)
            samples.append(row)
            if len(samples) % 12 == 0:
                print(f"  {row['t_min']:6.1f} min: polls {row['polls']:5d}  flushes {row['flushes']:3d}  "
                      f"W {row['W']:7.1f} MiB  C {row['C']:7.1f} MiB  handles {row['handles']}  "
                      f"threads {row['threads']}", flush=True)
        # ---- stop like an operator --------------------------------------------------
        stop_method = "none"
        if child.poll() is None:
            stop_method = "Ctrl+Break" if WINDOWS else "SIGTERM"
            try:
                child.send_signal(signal.CTRL_BREAK_EVENT if WINDOWS else signal.SIGTERM)
                child.wait(timeout=180)
            except (OSError, subprocess.TimeoutExpired) as exc:
                # OSError: no console to send CTRL_BREAK_EVENT through (Wine, or a
                # run without a console window) - stop the documented other way.
                Path(out, "STOP_COLLECTION").write_text("stop\n", encoding="utf-8")
                stop_method = f"STOP_COLLECTION ({stop_method} not delivered: {type(exc).__name__})"
                try:
                    child.wait(timeout=180)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=60)
                    stop_method = "killed (no graceful stop)"
    finally:
        if child.poll() is None:
            child.kill()
        console.close()
    tail.update()

    # ---- verdicts --------------------------------------------------------------------------
    checks = []
    checks.append(("exit code 0", child.returncode == 0, f"code {child.returncode}"))
    checks.append(("final writes logged", tail.final, ""))
    polls = tail.polls
    for ip in ("127_0_0_2", "127_0_0_3"):
        try:
            rows = _rows_per_output(out, ip)
            same = len(set(rows.values())) == 1
            count_ok = polls - 1 <= next(iter(rows.values())) <= polls
            checks.append((f"{ip}: rows equal in all outputs and = polls", same and count_ok,
                           f"{rows} polls {polls}"))
        except Exception as exc:
            checks.append((f"{ip}: rows readable in all outputs", False, f"{type(exc).__name__}: {exc}"))
    leftovers = [p for p in glob.glob(os.path.join(out, "**", "*"), recursive=True)
                 if "_rescue" in os.path.basename(p) or p.endswith(".merging")]
    checks.append(("no rescue / merging files left", not leftovers, ", ".join(leftovers[:3])))
    checks.append(("no ERROR in the log", not tail.errors, " / ".join(tail.errors[:2])))
    checks.append(("automatic RAM flushes happened", tail.flushes >= 1, f"{tail.flushes} flush(es)"))
    warm = samples[len(samples) // 4:]
    if len(warm) >= 8:
        quarter = max(2, len(warm) // 3)
        early, late = warm[:quarter], warm[-quarter:]
        w_growth = statistics.median(s["W"] for s in late) - statistics.median(s["W"] for s in early)
        checks.append(("working set flat after warm-up (<= 40 MiB)", w_growth <= 40, f"{w_growth:+.1f} MiB"))
        if WINDOWS:
            h_growth = statistics.median(s["handles"] for s in late) - statistics.median(s["handles"] for s in early)
            checks.append(("handle count flat (<= 100)", h_growth <= 100, f"{h_growth:+.0f}"))
    else:
        checks.append(("enough samples for the memory trend", False, f"{len(samples)} samples"))
    wine = _running_under_wine()
    warn = []
    if WINDOWS and stop_method != "Ctrl+Break":
        warn.append(f"stopped via {stop_method}" + (" (expected under Wine)" if wine else ""))

    summary = {"python": sys.version.split()[0], "platform": sys.platform, "wine": wine,
               "minutes": args.minutes, "interval_s": args.interval, "polls": polls,
               "flushes": tail.flushes, "stop_method": stop_method, "output": out,
               "checks": [{"check": c, "ok": ok, "detail": d} for c, ok, d in checks], "warnings": warn}
    _write_reports(out, samples, summary)
    print(f"Stopped via {stop_method}; {polls} polls, {tail.flushes} automatic flushes")
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    for text in warn:
        print(f"WARN  {text}")
    print(f"Reports: {out}{os.sep}soak_samples.csv, soak_memory.vszh5, soak_result.json")
    ok = all(ok for _, ok, _ in checks)
    print("PASS headless_soak_test" if ok else "FAIL headless_soak_test")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
