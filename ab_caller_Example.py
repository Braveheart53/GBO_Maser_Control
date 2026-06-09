#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ab_caller_Example.py
====================
Minimal usage example for ab_meter_caller.py / ab_power_meter_monitor.py.

Compatible with ab_power_meter_monitor.py v1.4.0+
"""
import ab_meter_caller as abc

# ── Single run: 2 samples, 20 s apart ────────────────────────────────────
# run() returns abm.TIME_SERIES_STORE — the full accumulated time-series.
# With count=2 the store contains exactly 2 samples per device per table.
data = abc.run(count=2, interval=20)

# ── Access examples ───────────────────────────────────────────────────────

# All Total Real Power samples for device .50
data["10.16.130.50"]["Real_Time_Power_Table"]["columns"]["Total Real Power"]
# → [123.4, 124.1]   (one float per completed sample)

# Matching timestamps (same length as every column list)
data["10.16.130.50"]["Real_Time_Power_Table"]["timestamps_local"]
# → ["2026-05-13 12:00:00", "2026-05-13 12:00:20"]

# L1 Real Power for the same device
data["10.16.130.50"]["Real_Time_Power_Table"]["columns"]["L1 Real Power"]

# Iterate all devices and tables
for ip, tables in data.items():
    for tname, tdata in tables.items():
        n = len(tdata["timestamps_local"])
        cols = list(tdata["columns"].keys())
        print(f"{ip} / {tname}: {n} sample(s), {len(cols)} column(s)")
