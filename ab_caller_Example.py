#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ab_caller_Example.py
====================
Minimal usage examples for ab_meter_caller.py / ab_power_meter_monitor.py.

Compatible with:
    ab_power_meter_monitor.py  v1.5.1+
    ab_meter_caller.py         v1.2.8+

What this shows
---------------
1. A single-sample run (quick connectivity / smoke test).
2. A fixed-count run (N samples, fixed interval) — the common case.
3. A continuous run (count=0) — poll until ab_stop.py / STOP_COLLECTION.
4. How to read values out of the returned TIME_SERIES_STORE.

The caller (`ab_meter_caller.py`) already sets every REQUIRED monitor switch
on import — including, as of v1.2.8:

    abm.ENABLE_GUI            = 0   # GUI would block forever
    abm.HEADLESS_LOOP_COUNT   = 1   # one poll per abm.main() call
    abm.HEADLESS_SILENT       = 1   # no module console output
    abm.ENABLE_SIGNAL_HANDLERS = 0  # do NOT hijack the host's SIGINT/SIGTERM

So you do not need to touch any monitor switch here.  If you want to poll a
different device set, override abm.IP_LIST BEFORE the first run() call (see
the commented block below).

# %% Author Info
@Author: W. Wallace — NRAO / Green Bank Observatory
Date   : 2026-07-22
Phone  : +1 (304) 456-2216
Email  : wwallace@nrao.edu
Email2 : naval.antennas@gmail.com
Python : 3.8+
Version: 1.1.1
"""
import ab_meter_caller as abc

# Optional: narrow (or widen) the polled device set before any run() call.
# Full IP strings only.  Leave commented to use the caller's default list.
#   import ab_power_meter_monitor as abm
#   abm.IP_LIST = ["10.16.130.50", "10.16.130.54"]


# ===========================================================================
# %% PATTERN 1 — Single sample (quick smoke test)
# ===========================================================================
# run() returns abm.TIME_SERIES_STORE — the full accumulated time-series.
# With count=1 the store holds exactly ONE sample per device per table.
#
#   data = abc.run(count=1, interval=30)


# ===========================================================================
# %% PATTERN 2 — Fixed number of samples (the common case)
# ===========================================================================
# count = number of poll cycles; interval = seconds between the START of each
# poll (poll duration is subtracted, so the cadence stays on-interval).
# Here: 3 samples, 30 s apart → ~60 s of wall-clock.
data = abc.run(count=3, interval=30)

# Optional: raise the RAM-flush threshold for long runs (10–95 %).  The
# caller value dominates the module default (MEM_RAM_PCT_LIMIT).
#   data = abc.run(count=0, interval=30, ram_pct=80)

# Optional: DISABLE automatic RAM flushing entirely (v1.2.9+).  The store is
# never cleared mid-run, so you get one uninterrupted, fully-accumulated
# time-series.  Watch RAM on long runs — nothing reclaims memory.
#   data = abc.run(count=0, interval=30, enable_ram_flush=False)
# Equivalent from the command line:
#   python ab_meter_caller.py --count 0 --interval 30 --no-ram-flush


# ===========================================================================
# %% PATTERN 3 — Continuous until stopped (uncomment to use)
# ===========================================================================
# count=0 polls forever.  Because the caller sets ENABLE_SIGNAL_HANDLERS=0,
# the graceful stop is the stop-signal file, NOT Ctrl-C:
#     python ab_stop.py          # from another terminal on the same machine
#   or:
#     touch <OUTPUT_BASE_DIR>/STOP_COLLECTION
# run() returns whatever has accumulated so far once the stop is seen.
#
#   data = abc.run(count=0, interval=30)


# ===========================================================================
# %% Access the results
# ===========================================================================
# Structure:  data[ip][table_name] = {
#     "timestamps_local": [str, ...],          # one per completed sample
#     "columns":          {param: [float|None, ...]},   # same length as ^
#     "units":            {param: unit_str},
# }
#
# NOTE: parameter (column) names are the exact labels the meter reports in
# its HTML tables.  "Total Real Power" / "L1 Real Power" below are examples;
# print the keys (Pattern below) to see the real names for your devices.

# All "Total Real Power" samples for device .50:
#   data["10.16.130.50"]["Real_Time_Power_Table"]["columns"]["Total Real Power"]
#   → [123.4, 124.1, 123.9]

# Matching timestamps (same length as every column list for that table):
#   data["10.16.130.50"]["Real_Time_Power_Table"]["timestamps_local"]
#   → ["2026-07-22 12:00:00", "2026-07-22 12:00:30", "2026-07-22 12:01:00"]

# Guarded access to one series (safe against missing device/table/param):
ip = "10.16.130.50"
series = (
    data.get(ip, {})
        .get("Real_Time_Power_Table", {})
        .get("columns", {})
        .get("Total Real Power")
)
if series:
    print(f"{ip} Total Real Power — {len(series)} sample(s); latest = {series[-1]}")
else:
    print(f"{ip}: no 'Total Real Power' data (device unreachable or name differs).")

# ── Iterate everything: devices, tables, sample counts, and column names ──
for ip, tables in data.items():
    for tname, tdata in tables.items():
        n = len(tdata["timestamps_local"])
        cols = list(tdata["columns"].keys())
        print(f"{ip} / {tname}: {n} sample(s), {len(cols)} column(s)")
