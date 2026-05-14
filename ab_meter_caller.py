#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
# %% ab_meter_caller.py Info
==================
Example calling script for ab_power_meter_monitor.py.

This script owns the outer loop.  On each iteration it instructs
ab_power_meter_monitor to perform exactly ONE poll of all devices then
return.  Accumulation is handled entirely by the monitor module's own
TIME_SERIES_STORE — the same store that every output file (Veusz, CSV,
XLSX, FITS) reads from.  This guarantees sample counts are always
consistent across ALL_DATA and every generated file.

# %%% Return structure  (abm.TIME_SERIES_STORE)
-----------------------------------------
::

    {
        "10.16.130.50": {
            "Real_Time_Power_Table": {
                "timestamps_local": ["2026-05-13 12:00:00", ...],
                "columns": {
                    "L1 Voltage": [120.1, 120.2, ...],   # one float per sample
                    "L2 Voltage": [119.8, 119.9, ...],
                    ...
                },
                "units": {"L1 Voltage": "V", ...},
            },
            ... # all 11 tables
        },
        "10.16.130.51": { ... },
        "10.16.130.52": { ... },
        "10.16.130.53": { ... },
    }

# %%% Switch configuration required in ab_power_meter_monitor.py
-----------------------------------------------------------
The following switches MUST be set as shown for this pattern to work
correctly.  Either edit the header of ab_power_meter_monitor.py directly,
or override them programmatically before calling main() as shown below.

    ENABLE_GUI            = 0   # REQUIRED — GUI blocks forever
    HEADLESS_LOOP_COUNT   = 1   # REQUIRED — single poll per main() call
    HEADLESS_SILENT       = 1   # REQUIRED — suppress all module console output

All other switches (ENABLE_FITS, ENABLE_CSV, ENABLE_XLSX, ENABLE_LOG_APPEND,
ENABLE_VEUSZ) can remain at whatever value you want.

# %%% Usage
-----
    python ab_meter_caller.py                    # single sample, then exit (default)
    python ab_meter_caller.py --count 0          # loop forever (Ctrl-C to stop)
    python ab_meter_caller.py --count 10         # 10 samples then exit
    python ab_meter_caller.py --count 10 --interval 60  # 10 samples, 60 s apart

# %% AUthor Info
@Author: W. Wallace — NRAO / Green Bank Observatory
Date   : 2026-05-13
Phone  : +1 (304) 456-2216
Email  : wwallace@nrao.edu
Email2 : naval.antennas@gmail.com 
Python : 3.8+
Version: 1.2.4
"""
# %% Imorts
import argparse
import datetime
import json
import sys
import time
from typing import Dict, Any

# ---------------------------------------------------------------------------
# %%% Import the monitor module and configure switches BEFORE calling main().
# ---------------------------------------------------------------------------
import ab_power_meter_monitor as abm

# %% Required Switches
# ── Required overrides ──────────────────────────────────────────────────────
abm.ENABLE_GUI = 0   # REQUIRED — GUI blocks forever
abm.HEADLESS_LOOP_COUNT = 1     # REQUIRED — single poll per main() call
abm.HEADLESS_SILENT = 1   # REQUIRED — zero module console output

# Redundant with SILENT=1 but explicit is better than implicit.
abm.HEADLESS_PRINT_EACH_SAMPLE = 0
abm.HEADLESS_PRINT_CUMULATIVE = 0
abm.HEADLESS_CONSOLE_DICTS_ONLY = 0

# ── Optional: override IP list if needed ───────────────────────────────────
# Replace IP_LIST to poll a specific subset of devices.  Full IP strings only.
# Example: monitor only .50 and .53:
#   abm.IP_LIST = ["10.16.130.50", "10.16.130.53"]
abm.IP_LIST = [
    "10.16.130.50",
    "10.16.130.51"
]
# abm.SAMPLE_PERIOD_SEC is set dynamically from the interval arg in run()
# — do not set it here as it would override the caller's value.


# ---------------------------------------------------------------------------
# %% Accumulator accessor
# ---------------------------------------------------------------------------

def get_all_data() -> Dict[str, Any]:
    """
    Return abm.TIME_SERIES_STORE — the single authoritative accumulator.

    This is the identical store that Veusz, CSV, XLSX, and FITS all read
    from, so sample counts returned here will always match output files.

    Structure
    ---------
    ::

        {
            ip: {
                table_name: {
                    "timestamps_local": [str, ...],   # one per sample
                    "columns":          {param: [float|None, ...]},
                    "units":            {param: str},
                }
            }
        }

    Returns a live reference — do not mutate.  Call copy.deepcopy() if
    you need an independent snapshot.
    """
    return abm.TIME_SERIES_STORE


# ---------------------------------------------------------------------------
# %% Helpers
# ---------------------------------------------------------------------------

def poll_once() -> None:
    """
    Trigger one full poll of all devices by calling abm.main().

    abm.main() will:
      1. Poll all devices in the configured IP range.
      2. Append results to abm.TIME_SERIES_STORE via accumulate_poll().
      3. Write enabled file outputs (FITS / CSV / XLSX / log / Veusz).

    The return value of abm.main() (a snapshot dict) is discarded here
    because get_all_data() / TIME_SERIES_STORE is the authoritative source.
    Any exception is printed to stderr and swallowed so the caller loop
    continues on transient network errors.
    """
    try:
        abm.main()
    except Exception as exc:
        print(f"[ab_meter_caller] ERROR during poll: {exc}", file=sys.stderr)


def summarise(iteration: int, total: int = 0) -> None:
    """
    Print a compact human-readable summary of the current TIME_SERIES_STORE
    state to stdout after each poll.

    Shows caller-level progress (N of M), device count, accumulated sample
    count, and a preview of the most recent Real_Time_Power_Table values.
    Replace or extend with your own application logic.

    Parameters
    ----------
    iteration : int
        Current loop iteration number (1-based).
    total : int
        Total number of iterations requested by the caller.  0 = infinite.
    """
    store = get_all_data()
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    devices = list(store.keys())

    # Progress from the caller's perspective — always accurate.
    progress = f"{iteration} of {'inf' if total == 0 else total}"

    print(f"\n[{ts}] Poll {progress} — {len(devices)} device(s): {devices}")

    for ip, tables in store.items():
        pwr = tables.get("Real_Time_Power_Table", {})
        n_samples = len(pwr.get("timestamps_local", []))
        columns = pwr.get("columns", {})

        # Preview: most recent value for first 6 parameters.
        preview = {}
        for param, values in columns.items():
            if isinstance(values, list) and values:
                preview[param] = values[-1]
            if len(preview) >= 6:
                break

        print(f"  {ip}  Real_Time_Power_Table  "
              f"samples={n_samples}  latest={json.dumps(preview)}")


# ---------------------------------------------------------------------------
# %% Main loop
# ---------------------------------------------------------------------------

def run(count: int = 1, interval: float = 30.0) -> Dict[str, Any]:
    """
    Outer polling loop.

    Each iteration calls poll_once() which triggers one full device poll
    and appends results to abm.TIME_SERIES_STORE.  No separate accumulation
    is performed here — get_all_data() always reflects the true state.

    Parameters
    ----------
    count : int
        Number of iterations.  0 = infinite (Ctrl-C to stop).
        Default is 1 — single sample then exit.
    interval : float
        Seconds between successive poll_once() calls, accounting for
        poll duration.  Set to 0 for back-to-back polling.

    Returns
    -------
    dict
        abm.TIME_SERIES_STORE — full accumulated time-series.
        Structure documented in get_all_data() above.
        Returns whatever has accumulated so far on KeyboardInterrupt.

    Access examples
    ---------------
    ::

        result = run(count=6, interval=30)

        # timestamps for device .50, Real_Time_Power_Table
        result["10.16.130.50"]["Real_Time_Power_Table"]["timestamps_local"]
        # → ["2026-05-13 12:00:00", "2026-05-13 12:00:30", ...]   (6 entries)

        # all L1 Voltage samples for device .50
        result["10.16.130.50"]["Real_Time_Power_Table"]["columns"]["L1 Voltage"]
        # → [120.1, 120.2, 120.3, 120.4, 120.5, 120.6]

        # iterate all devices and tables
        for ip, tables in result.items():
            for tname, tdata in tables.items():
                n   = len(tdata["timestamps_local"])
                cols = tdata["columns"]
    """
    infinite = (count == 0)
    iteration = 0

    # Sync the monitor's SAMPLE_PERIOD_SEC with the caller's interval so
    # any internal cfg logging or reporting reflects the actual wait time.
    abm.SAMPLE_PERIOD_SEC = interval

    print(f"ab_meter_caller starting — "
          f"{'infinite loop' if infinite else f'{count} iteration(s)'}, "
          f"interval={interval}s")
    print("Press Ctrl-C to stop.\n")

    try:
        while infinite or iteration < count:
            iteration += 1
            t_start = time.monotonic()

            # ── Single poll — all devices, all 11 tables ──────────────────
            # accumulate_poll() inside abm.main() appends to TIME_SERIES_STORE.
            poll_once()

            # ── Your application logic goes here ───────────────────────────
            # get_all_data() returns the live TIME_SERIES_STORE reference.
            #
            #   data = get_all_data()
            #   data["10.16.130.50"]["Real_Time_Power_Table"]["columns"]["L1 Voltage"]
            #       → list of floats, one per completed sample so far
            #   data["10.16.130.50"]["Real_Time_Power_Table"]["timestamps_local"]
            #       → list of timestamp strings, same length as each column

            summarise(iteration, total=count)

            # ── Wait for next interval, accounting for poll duration ───────
            elapsed = time.monotonic() - t_start
            remaining = interval - elapsed
            if remaining > 0 and (infinite or iteration < count):
                print(f"  Next poll in {remaining:.1f}s …")
                time.sleep(remaining)

    except KeyboardInterrupt:
        print(f"\nStopped after {iteration} iteration(s).")

    return get_all_data()


# ---------------------------------------------------------------------------
# %%% Entry point
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


# %% Main
if __name__ == "__main__":
    args = _parse_args()
    result = run(count=args.count, interval=args.interval)

    # result is abm.TIME_SERIES_STORE — same source as every output file.
    #
    # Access examples:
    #   result["10.16.130.50"]["Real_Time_Power_Table"]["timestamps_local"]
    #   result["10.16.130.50"]["Real_Time_Power_Table"]["columns"]["L1 Voltage"]
    #
    #   for ip, tables in result.items():
    #       for tname, tdata in tables.items():
    #           n = len(tdata["timestamps_local"])
    #           print(f"{ip} / {tname}: {n} sample(s)")

    total_samples = 0
    for ip, tables in result.items():
        for tname, tdata in tables.items():
            n = len(tdata.get("timestamps_local", []))
            total_samples = max(total_samples, n)

    print(f"\nCollection complete — {total_samples} sample(s) accumulated "
          f"across {len(result)} device(s).")
