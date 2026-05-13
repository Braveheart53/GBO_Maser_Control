#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ab_meter_caller.py
==================
Example calling script for ab_power_meter_monitor.py.

This script owns the outer loop.  On each iteration it instructs
ab_power_meter_monitor to perform exactly ONE poll of all devices,
accumulates the results into a time-series dict keyed by IP → table →
parameter, and returns the full accumulated structure when done.

Return structure
----------------
``all_data`` is a nested dict with this shape::

    {
        "10.16.130.50": {
            "Real_Time_Power_Table": {
                "timestamps":  ["2026-05-13 12:00:00", "2026-05-13 12:00:30", ...],
                "L1 Voltage":  [120.1, 120.2, ...],
                "L2 Voltage":  [119.8, 119.9, ...],
                # one key per numeric parameter; lists grow with each sample
            },
            "Voltage_Current_Table": { ... },
            # ... all 11 tables
        },
        "10.16.130.51": { ... },
        "10.16.130.52": { ... },
        "10.16.130.53": { ... },
    }

Each sample appends one value to every parameter list and one timestamp
string to the "timestamps" list, so all lists within a table are always
the same length.

Switch configuration required in ab_power_meter_monitor.py
-----------------------------------------------------------
The following switches MUST be set as shown for this pattern to work
correctly.  Either edit the header of ab_power_meter_monitor.py directly,
or override them programmatically before calling main() as shown below.

    ENABLE_GUI            = 0   # REQUIRED — GUI blocks forever
    HEADLESS_LOOP_COUNT   = 1   # REQUIRED — single poll per main() call
    HEADLESS_SILENT       = 1   # REQUIRED — suppress all module console output

All other switches (ENABLE_FITS, ENABLE_CSV, ENABLE_XLSX, ENABLE_LOG_APPEND,
ENABLE_VEUSZ) can remain at whatever value you want — file outputs are
independent of the console switches and will still be written.

Usage
-----
    python ab_meter_caller.py                    # single sample, then exit (default)
    python ab_meter_caller.py --count 0          # loop forever (Ctrl-C to stop)
    python ab_meter_caller.py --count 10         # run exactly 10 iterations
    python ab_meter_caller.py --count 10 --interval 60  # 10 samples, 60 s apart

Author : W. Wallace — NRAO / Green Bank Observatory
Date   : 2026-05-13
Python : 3.8+
Version: 1.2.0
"""

import argparse
import copy
import datetime
import json
import os
import sys
import time
from typing import Dict, Any

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
abm.HEADLESS_LOOP_COUNT = 5

# HEADLESS_SILENT = 1: ab_power_meter_monitor produces zero console output.
# All file-based logging (log, CSV, XLSX, FITS, Veusz) still runs on disk.
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
# abm.SAMPLE_PERIOD_SEC = 30   # internal sleep — not used when caller owns loop


# ---------------------------------------------------------------------------
# Accumulator
# ---------------------------------------------------------------------------

# Module-level accumulated time-series store.
# Shape: { ip: { table_name: { "timestamps": [...], param: [...], ... } } }
# Grows with every call to accumulate_sample().
# Persisted to ALL_DATA_CACHE_FILE between runs so that successive
# invocations of this script (each with --count 1) keep accumulating.
ALL_DATA: Dict[str, Dict[str, Dict[str, Any]]] = {}

# Path where ALL_DATA is saved/loaded between runs.
# Set to None to disable persistence (in-memory only).
ALL_DATA_CACHE_FILE: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "ab_meter_caller_cache.json",
)


def load_cache() -> None:
    """
    Load ALL_DATA from the JSON cache file on disk (if it exists).
    Call once at startup before the first poll so that successive runs
    of this script continue accumulating rather than starting fresh.
    """
    global ALL_DATA
    if ALL_DATA_CACHE_FILE and os.path.isfile(ALL_DATA_CACHE_FILE):
        try:
            with open(ALL_DATA_CACHE_FILE, "r", encoding="utf-8") as fh:
                ALL_DATA = json.load(fh)
            print(f"[cache] Loaded {ALL_DATA_CACHE_FILE}")
        except Exception as exc:
            print(
                f"[cache] WARNING — could not load cache: {exc}", file=sys.stderr)


def save_cache() -> None:
    """
    Save ALL_DATA to the JSON cache file on disk.
    Called after every successful accumulate_sample() so that the data
    survives a crash or Ctrl-C on the next run.
    """
    if not ALL_DATA_CACHE_FILE:
        return
    try:
        with open(ALL_DATA_CACHE_FILE, "w", encoding="utf-8") as fh:
            json.dump(ALL_DATA, fh)
    except Exception as exc:
        print(
            f"[cache] WARNING — could not save cache: {exc}", file=sys.stderr)


def accumulate_sample(snapshot: dict, timestamp: str) -> None:
    """
    Merge one poll snapshot into ALL_DATA, appending each parameter value
    to its running list.

    Parameters
    ----------
    snapshot : dict
        The nested dict returned by abm.main() / poll_once().
        Shape: { ip: { table_name: { "_meta": ..., "#1": name,
                                     "#1_value": float, "#1_unit": str, ... } } }
    timestamp : str
        ISO-style local timestamp string for this sample
        (e.g. "2026-05-13 12:00:00").
    """
    # Work on a deep copy of the snapshot so that any later mutation of
    # the module's internal dicts cannot corrupt the values we read here.
    snapshot_copy = copy.deepcopy(snapshot)

    for ip, tables in snapshot_copy.items():
        # Ensure top-level IP key exists.
        if ip not in ALL_DATA:
            ALL_DATA[ip] = {}

        for table_name, table_dict in tables.items():
            # Ensure table key exists with an empty timestamp list.
            if table_name not in ALL_DATA[ip]:
                ALL_DATA[ip][table_name] = {"timestamps": []}

            target = ALL_DATA[ip][table_name]

            # Append the timestamp for this sample.
            target["timestamps"].append(timestamp)

            # Walk all numeric parameter values in the snapshot table.
            # Keys of the form "#N_value" hold the float reading;
            # the corresponding "#N" key holds the human-readable name.
            for key, value in table_dict.items():
                if not key.endswith("_value"):
                    continue  # skip _meta, name keys, unit keys

                # Derive the parameter name from the paired "#N" key.
                # e.g. "#1_value" → base="#1" → param_name="L1 Voltage"
                base = key.replace("_value", "", 1)   # safe strip of suffix
                param_name = table_dict.get(
                    base, key)       # fall back to raw key

                # Initialise the list on first encounter.
                if param_name not in target:
                    target[param_name] = []

                # Append the value (None if not a number).
                try:
                    target[param_name].append(float(value))
                except (TypeError, ValueError):
                    target[param_name].append(None)

    # Persist to disk so successive single-run invocations keep accumulating.
    save_cache()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def poll_once() -> dict:
    """
    Trigger one full poll of all devices by calling abm.main().

    abm.main() will:
      1. Poll all devices in IP range.
      2. Update TIME_SERIES_STORE inside the module with the new sample.
      3. Write enabled file outputs (FITS / CSV / XLSX / log / Veusz).
      4. Return the nested snapshot dict.

    Returns
    -------
    dict
        Snapshot dict keyed by device IP, then table name.
        Returns an empty dict if main() raises an unexpected exception.
    """
    try:
        return abm.main()
    except Exception as exc:
        # Always print errors to stderr so they are visible even in silent mode.
        print(f"[ab_meter_caller] ERROR during poll: {exc}", file=sys.stderr)
        return {}


def summarise(iteration: int) -> None:
    """
    Print a compact human-readable summary of the current ALL_DATA state
    to stdout after each poll.

    Shows device count, sample count, and a preview of Real_Time_Power_Table
    values for the most recent sample.  Replace or extend with your own logic.

    Parameters
    ----------
    iteration : int
        Current loop iteration number (1-based).
    """
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    devices = list(ALL_DATA.keys())
    print(f"\n[{ts}] Iteration {iteration} — {len(devices)} device(s): {devices}")

    for ip, tables in ALL_DATA.items():
        pwr = tables.get("Real_Time_Power_Table", {})
        n_samples = len(pwr.get("timestamps", []))

        # Build a preview dict of the most recent value for each parameter.
        preview = {}
        for param, values in pwr.items():
            if param == "timestamps":
                continue
            if isinstance(values, list) and values:
                preview[param] = values[-1]
            if len(preview) >= 6:
                break

        print(f"  {ip}  Real_Time_Power_Table  "
              f"samples={n_samples}  latest={json.dumps(preview)}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run(count: int = 1, interval: float = 30.0) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """
    Outer polling loop.  Calls poll_once() on each iteration and accumulates
    results into ALL_DATA.

    Parameters
    ----------
    count : int
        Number of iterations to run.  0 = run indefinitely (Ctrl-C to stop).
        Default is 1 — single sample then exit.
    interval : float
        Seconds to wait between successive poll_once() calls, accounting for
        the time the poll itself takes.  Set to 0 for back-to-back polling.

    Returns
    -------
    dict
        ALL_DATA — the full accumulated time-series structure:

            {
                "10.16.130.50": {
                    "Real_Time_Power_Table": {
                        "timestamps": ["2026-05-13 12:00:00", ...],
                        "L1 Voltage": [120.1, 120.2, ...],
                        ...
                    },
                    ...  # all 11 tables
                },
                "10.16.130.51": { ... },
                ...
            }

        All parameter lists grow by one entry per completed iteration.
        Returns whatever has been accumulated so far on KeyboardInterrupt.
    """
    infinite = (count == 0)
    iteration = 0

    # Load any previously accumulated data from disk before the first poll.
    load_cache()

    print(f"ab_meter_caller starting — "
          f"{'infinite loop' if infinite else f'{count} iteration(s)'}, "
          f"interval={interval}s")
    print("Press Ctrl-C to stop.\n")

    try:
        while infinite or iteration < count:
            iteration += 1
            t_start = time.monotonic()

            # Timestamp for this sample — recorded before the poll so it
            # reflects when data collection began, not when it finished.
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # ── Single poll — all devices, all 11 tables ──────────────────
            snapshot = poll_once()

            # ── Accumulate into ALL_DATA ───────────────────────────────────
            # After this call:
            #   ALL_DATA[ip][table_name]["timestamps"]  — grows by 1
            #   ALL_DATA[ip][table_name][param_name]    — grows by 1
            accumulate_sample(snapshot, ts)

            # ── Your application logic goes here ───────────────────────────
            # ALL_DATA now contains every sample collected so far.
            #
            # Access patterns:
            #   ALL_DATA["10.16.130.50"]["Real_Time_Power_Table"]["L1 Voltage"]
            #       → [120.1, 120.2, ...]   (one float per sample)
            #
            #   ALL_DATA["10.16.130.50"]["Real_Time_Power_Table"]["timestamps"]
            #       → ["2026-05-13 12:00:00", ...]
            #
            #   for ip, tables in ALL_DATA.items():
            #       for tname, tdata in tables.items():
            #           print(ip, tname, tdata["timestamps"][-1])

            summarise(iteration)

            # ── Wait for next interval, accounting for poll duration ───────
            elapsed = time.monotonic() - t_start
            remaining = interval - elapsed
            if remaining > 0 and (infinite or iteration < count):
                print(f"  Next poll in {remaining:.1f}s …")
                time.sleep(remaining)

    except KeyboardInterrupt:
        print(f"\nStopped after {iteration} iteration(s).")

    return ALL_DATA


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
    result = run(count=args.count, interval=args.interval)

    # result  ==  ALL_DATA  — full time-series, all devices, all tables.
    #
    # Quick access examples:
    #   result["10.16.130.50"]["Real_Time_Power_Table"]["timestamps"]
    #   result["10.16.130.50"]["Real_Time_Power_Table"]["L1 Voltage"]
    #
    #   for ip, tables in result.items():
    #       for tname, tdata in tables.items():
    #           n = len(tdata["timestamps"])
    #           print(f"{ip} / {tname}: {n} sample(s)")

    total_samples = 0
    for ip, tables in result.items():
        for tname, tdata in tables.items():
            n = len(tdata.get("timestamps", []))
            total_samples = max(total_samples, n)

    print(f"\nCollection complete — {total_samples} sample(s) accumulated "
          f"across {len(result)} device(s) in ALL_DATA.")
