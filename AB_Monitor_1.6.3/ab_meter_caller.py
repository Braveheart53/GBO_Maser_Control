#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace <wwallace@nrao.edu>
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Drive ab_power_meter_monitor one poll at a time from your own Python loop.

===============================================================================
 Title          : AB Power Meter Caller
 Filename       : ab_meter_caller.py
 Version        : 1.3.0 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu   (alt: naval.antennas@gmail.com)
 Phone          : +1 (304) 456-2216
 Created        : 2026-05-13
 Modified       : 2026-10-26
 Language       : Python 3.12 (primary, tested); 3.8 - 3.11 supported;
                  3.7.11 language level (pin deps with requirements-py37.txt)
 Requires       : ab_power_meter_monitor.py >= 1.6.0 and its dependencies
 Licence        : GPL-3.0-or-later (GNU General Public License v3.0 or later)
 Status         : Release candidate
===============================================================================

DESCRIPTION
-----------
This script owns the outer loop. Each iteration performs exactly ONE poll of
all meters through abm.poll_cycle(), which stores the samples in
abm.TIME_SERIES_STORE (the store every output reads), appends the enabled
outputs and applies the monitor's RAM-flush protection. run() returns that
store, so sample counts here always match the files.

Since 1.3.0 a run is begin_run() -> poll_cycle() ... -> end_run(); 1.2.x
called abm.main() for every poll, which re-initialised logging, redirected
stdout, and rewrote every FITS/XLSX/Veusz output from the whole store on
every poll. The public functions (run, poll_once, get_all_data, summarise)
and the command line are unchanged.

Return structure (abm.TIME_SERIES_STORE)::

    {"10.16.130.50": {
        "Real_Time_Power_Table": {
            "timestamps_local": ["2026-05-13 12:00:00", ...],
            "columns": {"L1 Voltage": [120.1, 120.2, ...], ...},
            "units":   {"L1 Voltage": "V", ...}},
        ...},
     "10.16.130.54": {...}}

USAGE
-----
    python ab_meter_caller.py                         # one sample, then exit
    python ab_meter_caller.py --count 0               # until ab_stop.py
    python ab_meter_caller.py --count 10 --interval 60
    python ab_meter_caller.py --count 0 --no-ram-flush

    import ab_meter_caller as abc
    data = abc.run(count=3, interval=30)

Stop a count=0 run with `python ab_stop.py` (STOP_COLLECTION file) or Ctrl-C.

REVISION HISTORY
----------------
1.3.0  2026-10-26  Uses the monitor 1.6.0 run API (begin_run / poll_cycle /
                   end_run) instead of abm.main() per poll: no per-poll
                   logging re-initialisation (1.5.x stacked a root
                   NullHandler per poll in silent mode), incremental output
                   writes, final writes once at the end. Ctrl-C now stops
                   cleanly without the monitor's signal handlers (those stay
                   off, ENABLE_SIGNAL_HANDLERS = 0); STOP_COLLECTION honoured
                   between polls; console logging of the monitor switched off
                   with abm.set_console_logging(False) (HEADLESS_SILENT).
1.2.10 2026-08-13  Docs / Python 3.7.11 note.
1.2.9  2026-08-13  run(enable_ram_flush=...) and --no-ram-flush.
1.2.8  2026-07-22  ENABLE_SIGNAL_HANDLERS = 0 on import (exporter-safe).
===============================================================================
"""

# %% ===========================================================================
#    IMPORTS
# =============================================================================
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import threading
import time
from typing import Any, Dict, Optional

import ab_power_meter_monitor as abm

__version__ = "1.3.0"

# %% ===========================================================================
#    PYTHON VERSION CHECK (advisory)
# =============================================================================
MIN_PYTHON = (3, 7, 11)
if sys.version_info < MIN_PYTHON:
    sys.stderr.write("WARNING: ab_meter_caller requires Python >= {0}; running {1}.\n".format(
        ".".join(map(str, MIN_PYTHON)), ".".join(map(str, sys.version_info[:3]))))

# %% ===========================================================================
#    MONITOR SWITCHES FOR LIBRARY USE (set on import, before any poll)
# =============================================================================
abm.ENABLE_GUI = 0                # a GUI would block this loop
abm.HEADLESS_LOOP_COUNT = 1       # kept for code that still calls abm.main()
abm.HEADLESS_SILENT = 1           # no monitor console output (see below)
abm.ENABLE_SIGNAL_HANDLERS = 0    # never take SIGINT/SIGTERM from a host process
abm.HEADLESS_PRINT_EACH_SAMPLE = 0
abm.HEADLESS_PRINT_CUMULATIVE = 0
abm.HEADLESS_CONSOLE_DICTS_ONLY = 0

# Devices for this caller (full IP strings). Override before run() if needed:
#   abm.IP_LIST = ["10.16.130.50", "10.16.130.53"]
abm.IP_LIST = [
    "10.16.130.50",
    "10.16.130.54",
]

# Set by Ctrl-C handling / callers to stop run() between polls.
STOP_EVENT = threading.Event()


# %% ===========================================================================
#    ACCESSORS AND HELPERS
# =============================================================================
def get_all_data() -> Dict[str, Any]:
    """abm.TIME_SERIES_STORE - the live store every output reads (do not mutate).

    For an independent copy use abm.store_snapshot().
    """
    return abm.TIME_SERIES_STORE


def poll_once(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One full poll of every meter (starts a monitor run if none is active).

    Returns the abm.poll_cycle() status dict (ok_ips, failed_ips, persist,
    flush, store_mb, ...). Never raises: errors are reported on stderr and in
    the returned dict, so a caller loop survives transient network problems.
    """
    try:
        return abm.poll_cycle(cfg, stop_event=STOP_EVENT)
    except Exception as exc:                      # noqa: BLE001 - keep the loop alive
        print(f"[ab_meter_caller] ERROR during poll: {exc}", file=sys.stderr)
        return {"error": str(exc), "all_failed": True}


def summarise(iteration: int, total: int = 0) -> None:
    """Print progress and the newest Real_Time_Power_Table values per meter."""
    store = abm.store_snapshot(tname="Real_Time_Power_Table")
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{stamp}] Poll {iteration} of {'inf' if total == 0 else total} - "
          f"{len(store)} meter(s): {list(store)}")
    for ip, tables in store.items():
        table = tables.get("Real_Time_Power_Table", {})
        preview = {p: v[-1] for p, v in list(table.get("columns", {}).items())[:6] if v}
        print(f"  {ip}  Real_Time_Power_Table  samples={len(table.get('timestamps_local', []))}  "
              f"latest={json.dumps(preview)}")


# %% ===========================================================================
#    MAIN LOOP
# =============================================================================
def run(count: int = 1, interval: float = 30.0, ram_pct: Optional[float] = None,
        enable_ram_flush: Optional[bool] = None, quiet: bool = False) -> Dict[str, Any]:
    """Poll ``count`` times (0 = until stopped), ``interval`` s apart; return the store.

    Parameters
    ----------
    count : int
        Polls to perform; 0 = until ab_stop.py / STOP_EVENT / Ctrl-C.
    interval : float
        Seconds between the STARTS of successive polls (poll time subtracted).
    ram_pct : float, optional
        System-RAM % flush threshold (10-99) for this run.
    enable_ram_flush : bool, optional
        None = leave abm.ENABLE_RAM_FLUSH; False = never flush/clear mid-run
        (one complete store - watch RAM); True = force automatic flushing on.
    quiet : bool
        Skip the per-poll summary printout.

    Returns
    -------
    dict
        abm.TIME_SERIES_STORE (whatever accumulated, also after a stop).
    """
    infinite = count == 0
    STOP_EVENT.clear()
    abm.SAMPLE_PERIOD_SEC = interval
    if ram_pct is not None:
        abm.MEM_RAM_PCT_LIMIT = max(10.0, min(99.0, float(ram_pct)))
    if enable_ram_flush is not None:
        abm.ENABLE_RAM_FLUSH = 1 if enable_ram_flush else 0
    if abm.HEADLESS_SILENT:
        abm.set_console_logging(False)            # files keep logging when enabled
    cfg = abm.begin_run()
    stop_file = abm.stop_signal_file()
    print(f"ab_meter_caller {__version__} - {'until stopped' if infinite else f'{count} poll(s)'}, "
          f"interval {interval}s (stop: python ab_stop.py or Ctrl-C)")
    iteration = 0
    try:
        while infinite or iteration < count:
            if STOP_EVENT.is_set() or os.path.exists(stop_file):
                print(f"\n[ab_meter_caller] stop requested - exiting after {iteration} poll(s).")
                break
            iteration += 1
            started = time.monotonic()
            poll_once(cfg)
            # ---- your application logic goes here (store = get_all_data()) ----
            if not quiet:
                summarise(iteration, total=count)
            if not infinite and iteration >= count:
                break
            while not STOP_EVENT.is_set() and not os.path.exists(stop_file):
                remaining = interval - (time.monotonic() - started)
                if remaining <= 0:
                    break
                STOP_EVENT.wait(min(0.5, remaining))
    except KeyboardInterrupt:
        print(f"\nStopped after {iteration} poll(s).")
    finally:
        abm.end_run(cfg)                            # final writes, always
        try:
            if os.path.exists(stop_file):
                os.remove(stop_file)
        except OSError:
            pass
    return get_all_data()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calling wrapper for ab_power_meter_monitor.py",
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--count", "-n", type=int, default=1,
                        help="Number of polls (0 = until stopped).")
    parser.add_argument("--interval", "-i", type=float, default=30.0,
                        help="Seconds between polls.")
    parser.add_argument("--ram-pct", "-r", type=float, default=None, dest="ram_pct",
                        help="System RAM %% flush threshold (10-99).")
    parser.add_argument("--no-ram-flush", action="store_true", dest="no_ram_flush",
                        help="Disable automatic RAM flushing for this run.")
    parser.add_argument("--quiet", "-q", action="store_true", help="No per-poll summary.")
    return parser.parse_args()


# %% ===========================================================================
#    SCRIPT ENTRY
# =============================================================================
if __name__ == "__main__":
    args = _parse_args()
    result = run(count=args.count, interval=args.interval, ram_pct=args.ram_pct,
                 enable_ram_flush=False if args.no_ram_flush else None, quiet=args.quiet)
    samples = max((len(t.get("timestamps_local", [])) for d in result.values() for t in d.values()),
                  default=0)
    print(f"\nCollection complete - {samples} sample(s) in memory across {len(result)} meter(s).")
