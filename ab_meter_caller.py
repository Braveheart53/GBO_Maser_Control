#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ab_meter_caller.py
==================
Example calling script for ab_power_meter_monitor.py.

This script owns the outer loop.  On each iteration it instructs
ab_power_meter_monitor to perform exactly ONE poll of all four devices
(10.16.130.50-53), return the full nested result dict, and produce zero
console output.  All file outputs (FITS, CSV, XLSX, Markdown log, Veusz)
are written by the module on each call if the corresponding ENABLE_ flags
are set.

Switch configuration required in ab_power_meter_monitor.py
-----------------------------------------------------------
The following switches MUST be set as shown for this pattern to work
correctly.  Either edit the header of ab_power_meter_monitor.py directly,
or override them programmatically before calling main() as shown below.

    ENABLE_GUI            = 0   # REQUIRED — must be headless; GUI blocks forever
    HEADLESS_LOOP_COUNT   = 1   # REQUIRED — single poll per main() call
    HEADLESS_SILENT       = 1   # REQUIRED — suppress all console output so
                                #            only this script controls the terminal
    HEADLESS_PRINT_EACH_SAMPLE  = 0   # not needed; caller handles output
    HEADLESS_PRINT_CUMULATIVE   = 0   # not needed; caller handles output
    HEADLESS_CONSOLE_DICTS_ONLY = 0   # redundant when SILENT=1, but harmless

All other switches (ENABLE_FITS, ENABLE_CSV, ENABLE_XLSX, ENABLE_LOG_APPEND,
ENABLE_VEUSZ) can remain at whatever value you want — file outputs are
independent of the console switches and will still be written.

Usage
-----
    python ab_meter_caller.py                   # single sample, then exit (default)
    python ab_meter_caller.py --count 0        # loop forever (Ctrl-C to stop)
    python ab_meter_caller.py --count 10        # run exactly 10 iterations
    python ab_meter_caller.py --interval 60     # 60-second pause between polls

Author : W. Wallace — NRAO / Green Bank Observatory
Date   : 2026-05-11
Python : 3.8+
Version: 1.0.3
"""

import argparse
import datetime
import json
import sys
import time

# ---------------------------------------------------------------------------
# Import the monitor module and configure switches BEFORE calling main().
# Overriding module-level variables here is equivalent to editing the header
# switches in ab_power_meter_monitor.py — the module reads these values each
# time main() is called.
# ---------------------------------------------------------------------------
import ab_power_meter_monitor as abm

# ── Required overrides ──────────────────────────────────────────────────────
# ENABLE_GUI must be 0: GUI mode blocks indefinitely; we need headless.
abm.ENABLE_GUI = 0

# HEADLESS_LOOP_COUNT = 1: each main() call polls all devices exactly once
# then returns.  The outer loop in THIS script drives the repetition.
abm.HEADLESS_LOOP_COUNT = 3

# HEADLESS_SILENT = 1: ab_power_meter_monitor produces zero console output.
# All logging still goes to the log file on disk.
abm.HEADLESS_SILENT = 1

# Suppress the other console-print switches (redundant with SILENT=1,
# but explicit is better than implicit).
abm.HEADLESS_PRINT_EACH_SAMPLE = 0
abm.HEADLESS_PRINT_CUMULATIVE = 0
abm.HEADLESS_CONSOLE_DICTS_ONLY = 0

# ── Optional: override IP range or sample period if needed ──────────────────
abm.IP_BASE = "10.16.130"
abm.IP_LAST_OCTET_START = 50
abm.IP_LAST_OCTET_END = 51
# abm.SAMPLE_PERIOD_SEC = 30   # only affects internal sleep; not used here


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def poll_once() -> dict:
    """
    Trigger one full poll of all devices by calling main().

    main() will:
      1. Poll all devices in IP range (10.16.130.50-53 by default).
      2. Update TIME_SERIES_STORE with the new sample.
      3. Write enabled file outputs (FITS / CSV / XLSX / log / Veusz).
      4. Return the nested dict:
            { "10.16.130.50": { "Real_Time_Power_Table": {...}, ... },
              "10.16.130.51": { ... }, ... }

    Returns
    -------
    dict
        Two-level dict keyed by device IP, then table name.
        Returns an empty dict if main() raises an unexpected exception.
    """
    try:
        return abm.main()
    except Exception as exc:
        # Print errors to stderr so they are visible even in silent mode.
        print(f"[ab_meter_caller] ERROR during poll: {exc}", file=sys.stderr)
        return {}


def summarise(results: dict, iteration: int) -> None:
    """
    Print a compact human-readable summary of one poll result to stdout.

    Shows: timestamp, devices found, and a selection of key values from
    Real_Time_Power_Table for a quick sanity-check.  Replace or extend
    this function with whatever processing your application needs.

    Parameters
    ----------
    results : dict
        Return value from poll_once().
    iteration : int
        Current loop iteration number (1-based).
    """
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    devices = list(results.keys())
    print(f"\n[{ts}] Iteration {iteration} — {len(devices)} device(s): {devices}")

    for ip, tables in results.items():
        pwr = tables.get("Real_Time_Power_Table", {})
        # Pull a few representative values; skip _meta and index keys
        kv = {
            k: v for k, v in pwr.items()
            if not k.startswith("#") and k != "_meta" and "_unit" not in k
        }
        # Limit to first 6 for readability
        preview = dict(list(kv.items())[:6])
        print(f"  {ip}  Real_Time_Power_Table (preview): {json.dumps(preview)}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run(count: int = 1, interval: float = 30.0) -> list:
    """
    Outer polling loop.

    Parameters
    ----------
    count : int
        Number of iterations to run.  0 = run indefinitely (Ctrl-C to stop).
        Default is 1 — single sample then exit.
    interval : float
        Seconds to wait between successive poll_once() calls.
        Set to 0 for back-to-back polling (useful for testing).

    Returns
    -------
    list of dict
        One entry per completed iteration.  Each entry is the nested dict
        returned by abm.main()::

            all_results[0]  # first sample
            all_results[-1] # most recent sample
            all_results[2]["10.16.130.50"]["Real_Time_Power_Table"]

        Returns an empty list if no iterations completed (e.g. immediate
        KeyboardInterrupt).
    """
    # Accumulate every poll result here so the caller has the full history.
    all_results: list = []

    infinite = (count == 0)
    iteration = 0

    print(f"ab_meter_caller starting — "
          f"{'infinite loop' if infinite else f'{count} iteration(s)'}, "
          f"interval={interval}s")
    print("Press Ctrl-C to stop.\n")

    try:
        while infinite or iteration < count:
            iteration += 1
            t_start = time.monotonic()

            # ── Single poll — all devices, all 11 tables ──────────────────
            # results shape:
            #   { "10.16.130.50": { "Real_Time_Power_Table": {...}, ... },
            #     "10.16.130.51": { ... },
            #     "10.16.130.52": { ... },
            #     "10.16.130.53": { ... } }
            results = poll_once()

            # Append this iteration's result to the running list.
            all_results.append(results)

            # ── Your application logic goes here ──────────────────────────
            # Access patterns:
            #   all_results[-1]["10.16.130.50"]["Real_Time_Power_Table"]
            #   for ip, tables in results.items():
            #       tables["Voltage_Current_Table"]
            #
            # Example: check total real power on device .50
            #   pwr_table = results.get("10.16.130.50", {}).get("Real_Time_Power_Table", {})
            #   total_pwr = pwr_table.get("#4_value")  # value depends on table layout

            summarise(results, iteration)

            # ── Wait for next interval, accounting for poll duration ───────
            elapsed = time.monotonic() - t_start
            remaining = interval - elapsed
            if remaining > 0 and (infinite or iteration < count):
                print(f"  Next poll in {remaining:.1f}s …")
                time.sleep(remaining)

    except KeyboardInterrupt:
        print(f"\nStopped after {iteration} iteration(s).")

    return all_results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calling wrapper for ab_power_meter_monitor.py",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--count", "-n",
        type=int,
        default=1,
        help="Number of poll iterations (0 = infinite, default 1 = single sample)",
    )
    parser.add_argument(
        "--interval", "-i",
        type=float,
        default=30.0,
        help="Seconds between poll iterations",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    all_data = run(count=args.count, interval=args.interval)

    # all_data is a list of nested dicts, one per completed iteration.
    # Index examples:
    #   all_data[0]                                        — first sample
    #   all_data[-1]                                       — most recent sample
    #   all_data[-1]["10.16.130.50"]["Real_Time_Power_Table"]  — specific table
    #   for sample in all_data:                            — iterate all samples
    #       for ip, tables in sample.items():
    #           print(ip, tables["Voltage_Current_Table"])

    print(f"\nCollection complete — {len(all_data)} sample(s) in all_data.")
