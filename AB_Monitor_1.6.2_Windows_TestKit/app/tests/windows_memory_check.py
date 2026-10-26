#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace <wwallace@nrao.edu>
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Measure what a RAM flush gives back to Windows (Task Manager's view).

===============================================================================
 Title          : AB Power Meter Monitor - Windows memory-return measurement
 Filename       : tests/windows_memory_check.py
 Version        : 0.0.2 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu
 Created        : 2026-10-26
 Modified       : 2026-10-26
 Language       : Python 3.8 through 3.12 (meant for Windows; runs elsewhere
                  for comparison, with psutil numbers)
 Requires       : psutil (+ monitor dependencies)
 Licence        : GPL-3.0-or-later (GNU General Public License v3.0 or later)
 Status         : Internal draft
===============================================================================

What it does
------------
Each configuration runs in a FRESH Python process (so one cannot inherit the
heap state of another):

    configuration     MEM_RETURN_TO_OS   MEM_TRIM_WORKING_SET
    gc_only                  0                    -           (1.6.0 on Windows)
    heap_only                1                    0
    full (default)           1                    1

each with keep = 0 (store emptied) and keep = 120 (the GUI keeps the newest
120 rows per table, which leaves the allocator's arenas partly used - the
realistic case). Per run: idle -> fill the store with --values float values
-> flush -> 2 s later. Read directly from Win32 GetProcessMemoryInfo
(PROCESS_MEMORY_COUNTERS_EX):

    W  WorkingSetSize   Task Manager "Details" tab: Working set (memory)
    C  PrivateUsage     Task Manager "Details" tab: Commit size

Pass criteria (Windows only)
----------------------------
* full, keep 0 and keep 120:  W after the flush <= W idle + 30 % of what the
  fill added  (the RAM really goes back to Windows).
* full keeps W lower than gc_only for keep = 120 (the trim does something
  the allocator alone does not).
Commit charge is reported, not judged (it depends on heap fragmentation).
Under Wine (detected via ntdll's wine_get_version) and on other systems the
numbers are printed for comparison only: Wine runs the Windows calls but has
no Windows memory manager, so a working-set trim does nothing there.

Usage
-----
    python tests/windows_memory_check.py                  # all six runs, ~1-2 min
    python tests/windows_memory_check.py --values 3000000 # smaller store
    python tests/windows_memory_check.py --hold 30        # pause 30 s after each
                                                          # flush to watch Task Manager
    python tests/windows_memory_check.py --json result.json

REVISION HISTORY
----------------
0.0.2  2026-10-26  Wine detected and reported as informational (no pass/fail).
0.0.1  2026-10-26  First version (monitor 1.6.2).
===============================================================================
"""

# %% IMPORTS ===================================================================
from __future__ import annotations

import argparse
import gc
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

CONFIGS = {"gc_only": (0, 0), "heap_only": (1, 0), "full": (1, 1)}
KEEPS = (0, 120)


# %% MEMORY COUNTERS ===========================================================
def memory_mb() -> dict:
    """Working set and commit charge of this process in MiB.

    Windows: K32GetProcessMemoryInfo with PROCESS_MEMORY_COUNTERS_EX (layout
    as in psapi.h). Elsewhere: psutil rss / vms (for comparison only).
    """
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class _Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        k32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
        counters = _Counters()
        counters.cb = ctypes.sizeof(counters)
        if not k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise OSError(ctypes.get_last_error(), "K32GetProcessMemoryInfo failed")
        # PrivateUsage is the commit charge; PagefileUsage holds the same value on
        # Windows. Wine leaves PrivateUsage at 0, hence the fallback.
        commit = counters.PrivateUsage or counters.PagefileUsage
        return {"W": counters.WorkingSetSize / 2 ** 20, "C": commit / 2 ** 20, "source": "win32"}
    import psutil
    info = psutil.Process().memory_info()
    return {"W": info.rss / 2 ** 20, "C": info.vms / 2 ** 20, "source": "psutil"}


def running_under_wine() -> bool:
    """True on Wine: its ntdll exports wine_get_version, Windows' does not."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return hasattr(ctypes.WinDLL("ntdll"), "wine_get_version")
    except OSError:
        return False


# %% ONE RUN (child process) =====================================================
def run_one(config: str, keep: int, values: int, hold: float) -> dict:
    """Fill the store, flush with one configuration, report the counters."""
    import ab_power_meter_monitor as abm
    abm.set_console_logging(False)
    for flag in ("ENABLE_CSV", "ENABLE_LOG_APPEND", "ENABLE_VEUSZ", "ENABLE_FITS", "ENABLE_XLSX"):
        setattr(abm, flag, 0)                         # no file output: the flush drops the rows
    return_os, trim = CONFIGS[config]
    gc.collect()
    time.sleep(0.5)
    idle = memory_mb()
    tables, cols = 20, 60
    rows = max(1, values // (tables * cols))
    stamp = "2026-10-26 06:00:00"
    # Filled in POLL order (row by row across every table), as the monitor
    # allocates: the newest rows then sit together in the newest allocator
    # arenas, so keep = 120 frees the older arenas as it would in a real run.
    with abm._TS_LOCK:
        tables_data = []
        for t in range(tables):
            columns = {f"P{c:02d}": [] for c in range(cols)}
            abm.TIME_SERIES_STORE.setdefault(f"10.0.0.{t % 4}", {})[f"Table_{t:02d}"] = {
                "timestamps_local": [], "columns": columns, "units": {name: "" for name in columns}}
            tables_data.append(abm.TIME_SERIES_STORE[f"10.0.0.{t % 4}"][f"Table_{t:02d}"])
        for r in range(rows):
            for t, table in enumerate(tables_data):
                table["timestamps_local"].append(stamp)
                for c, column in enumerate(table["columns"].values()):
                    column.append(float(r) + c / 100.0 + t)
    filled = memory_mb()
    cfg = abm.default_config()
    cfg.update(mem_return_to_os=return_os, mem_trim_working_set=trim)
    result = abm.perform_ram_flush(cfg, f"measure {config}", keep_samples=keep)
    flushed = memory_mb()
    if hold > 0:
        print(f"  [{config}, keep {keep}] holding {hold:.0f} s - look at Task Manager (pid {os.getpid()})",
              file=sys.stderr, flush=True)
        time.sleep(hold)
    else:
        time.sleep(2.0)
    later = memory_mb()
    return {"config": config, "keep": keep, "values": tables * rows * cols, "pid": os.getpid(),
            "idle": idle, "filled": filled, "flushed": flushed, "later": later,
            "rows_removed": result["rows_removed"], "os_release": result["os_release_text"],
            "monitor": abm.__version__}


# %% DRIVER ========================================================================
def judge(runs: list) -> list:
    """Pass/fail lines (Windows); informational elsewhere."""
    lines, by_key = [], {(r["config"], r["keep"]): r for r in runs}
    for keep in KEEPS:
        full = by_key.get(("full", keep))
        if full:
            added = full["filled"]["W"] - full["idle"]["W"]
            limit = full["idle"]["W"] + 0.30 * added
            ok = full["flushed"]["W"] <= limit
            lines.append((ok, f"full, keep {keep}: W after flush {full['flushed']['W']:.0f} MiB "
                              f"<= idle {full['idle']['W']:.0f} + 30 % of +{added:.0f} = {limit:.0f} MiB"))
    full, gc_only = by_key.get(("full", 120)), by_key.get(("gc_only", 120))
    if full and gc_only:
        ok = full["flushed"]["W"] < gc_only["flushed"]["W"]
        lines.append((ok, f"keep 120: W after flush full {full['flushed']['W']:.0f} MiB < gc_only "
                          f"{gc_only['flushed']['W']:.0f} MiB"))
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--values", type=int, default=6_000_000, help="float values in the store")
    parser.add_argument("--hold", type=float, default=0.0, help="seconds to pause after each flush")
    parser.add_argument("--json", default="", help="also write the results to this JSON file")
    parser.add_argument("--one", nargs=2, metavar=("CONFIG", "KEEP"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.one:                                        # child process
        print("RESULT " + json.dumps(run_one(args.one[0], int(args.one[1]), args.values, args.hold)),
              flush=True)
        return 0
    runs = []
    print(f"Python {sys.version.split()[0]} on {sys.platform}; {args.values:,} values per run")
    print(f"{'config':10s} {'keep':>4s} | {'W idle':>7s} {'filled':>7s} {'flushed':>8s} {'+2 s':>6s} | "
          f"{'C idle':>7s} {'filled':>7s} {'flushed':>8s} | OS release")
    for keep in KEEPS:
        for config in CONFIGS:
            proc = subprocess.run([sys.executable, __file__, "--one", config, str(keep),
                                   "--values", str(args.values), "--hold", str(args.hold)],
                                  capture_output=True, text=True, timeout=900)
            line = next((l for l in proc.stdout.splitlines() if l.startswith("RESULT ")), None)
            if proc.returncode != 0 or line is None:
                print(f"{config:10s} {keep:4d} | FAILED rc={proc.returncode}\n{proc.stderr[-1500:]}")
                return 1
            r = json.loads(line[7:])
            runs.append(r)
            print(f"{config:10s} {keep:4d} | {r['idle']['W']:7.0f} {r['filled']['W']:7.0f} "
                  f"{r['flushed']['W']:8.0f} {r['later']['W']:6.0f} | {r['idle']['C']:7.0f} "
                  f"{r['filled']['C']:7.0f} {r['flushed']['C']:8.0f} | {r['os_release']}")
    source = runs[0]["idle"]["source"]
    print(f"(MiB; W = working set, C = commit charge; counters from {source})")
    verdicts = judge(runs)
    wine = running_under_wine()
    windows = sys.platform == "win32" and not wine
    for ok, text in verdicts:
        print((("PASS  " if ok else "FAIL  ") if windows else "INFO  ") + text)
    if wine:
        print("Wine: the Windows memory manager is not emulated - numbers for comparison only.")
    elif not windows:
        print("Not Windows: numbers are for comparison only (no pass/fail).")
    if args.json:
        Path(args.json).write_text(json.dumps({"runs": runs, "verdicts": verdicts, "judged": windows,
                                               "wine": wine}, indent=2), encoding="utf-8")
    all_ok = all(ok for ok, _ in verdicts)
    print("PASS windows_memory_check" if (all_ok or not windows) else "FAIL windows_memory_check")
    return 0 if (all_ok or not windows) else 1


if __name__ == "__main__":
    sys.exit(main())
