#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace <wwallace@nrao.edu>
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Minimal usage examples for ab_meter_caller.py / ab_power_meter_monitor.py.

===============================================================================
 Title          : AB Power Meter Caller - usage examples
 Filename       : ab_caller_Example.py
 Version        : 1.2.0 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu   (alt: naval.antennas@gmail.com)
 Phone          : +1 (304) 456-2216
 Created        : 2026-07-22
 Modified       : 2026-10-26
 Language       : Python 3.12 (primary); 3.8 - 3.11; 3.7.11 language level
 Requires       : ab_meter_caller.py >= 1.3.0, ab_power_meter_monitor.py >= 1.6.0
 Licence        : GPL-3.0-or-later (GNU General Public License v3.0 or later)
 Status         : Example (run cell by cell in Spyder, or as a script)
===============================================================================

What this shows
---------------
1. A single-sample run (quick connectivity / smoke test).
2. A fixed-count run (N samples, fixed interval) - the common case.
3. A continuous run (count=0) - poll until ab_stop.py / STOP_COLLECTION.
4. Reading values out of the returned TIME_SERIES_STORE.
5. (1.6.0) Calling the monitor's run API directly, without the caller.

The caller sets the required monitor switches on import (ENABLE_GUI = 0,
ENABLE_SIGNAL_HANDLERS = 0, silent console), so nothing needs to be set here.

REVISION HISTORY
----------------
1.2.0  2026-10-26  House-style header; pattern 5 (abm.begin_run / poll_cycle /
                   end_run); notes on output switches and Flush RAM.
1.1.2  2026-08-13  Documents enable_ram_flush.
===============================================================================
"""

# %% IMPORTS ===================================================================
import ab_meter_caller as abc
import ab_power_meter_monitor as abm  # noqa: F401  (used in the commented patterns)

# Optional: choose devices / outputs BEFORE the first run() call.
#   abm.IP_LIST = ["10.16.130.50", "10.16.130.54"]
#   abm.ENABLE_CSV = 1          # appended every poll
#   abm.ENABLE_VEUSZ = 1        # ABMeter_<ip>.vszh5 appended every poll (no Veusz needed)

# %% PATTERN 1 - single sample (smoke test) ====================================
#   data = abc.run(count=1, interval=30)

# %% PATTERN 2 - fixed number of samples (the common case) ======================
# count = polls; interval = seconds between poll STARTS. 3 samples ~ 60 s.
data = abc.run(count=3, interval=30)
#   data = abc.run(count=0, interval=30, ram_pct=80)               # higher RAM-% threshold
#   data = abc.run(count=0, interval=30, enable_ram_flush=False)   # never clear mid-run

# %% PATTERN 3 - continuous until stopped =========================================
# Stop from another terminal:  python ab_stop.py   (or Ctrl-C in this one)
#   data = abc.run(count=0, interval=30)

# %% PATTERN 4 - access the results =================================================
# data[ip][table] = {"timestamps_local": [...], "columns": {param: [...]}, "units": {...}}
# Parameter names are the meter's own labels; print the keys to see them.
ip = "10.16.130.50"
series = data.get(ip, {}).get("Real_Time_Power_Table", {}).get("columns", {}).get("Total Real Power")
if series:
    print(f"{ip} Total Real Power - {len(series)} sample(s); latest = {series[-1]}")
else:
    print(f"{ip}: no 'Total Real Power' data (meter unreachable or the name differs).")
for ip, tables in data.items():
    for tname, tdata in tables.items():
        print(f"{ip} / {tname}: {len(tdata['timestamps_local'])} sample(s), "
              f"{len(tdata['columns'])} column(s)")

# %% PATTERN 5 - the monitor's run API directly (1.6.0) ============================
#   cfg = abm.begin_run()                 # folders, log file, fresh-file bookkeeping
#   status = abm.poll_cycle(cfg)          # one poll + persist + memory protection
#   print(status["ok_ips"], status["store_mb"], status["persist"])
#   abm.perform_ram_flush(cfg, "manual")  # persist, then free the in-memory rows
#   abm.end_run(cfg)                      # final writes (FITS/XLSX included)
