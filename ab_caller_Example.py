# =============================================================================
# ab_caller_Example.py
# Example / quick-start usage of ab_meter_caller.run()
#
# Version: 1.0.1
# Requires: ab_meter_caller.py  (v1.2.6+)
#           ab_power_meter_monitor.py  (v1.1.1+)
# =============================================================================

import ab_meter_caller as abc

# ── Minimal usage ─────────────────────────────────────────────────────────────
# Single poll, default 30 s interval, default 70 % RAM flush limit.
data = abc.run(count=1, interval=20)

# Access a specific value from the returned store snapshot.
# Structure: data[device_ip][table_name]["columns"][column_name]
# Returns a list of values (one per sample collected so far).
total_real_power = data["10.16.130.50"]["Real_Time_Power_Table"]["columns"]["Total Real Power"]
print("Total Real Power samples:", total_real_power)

# ── Continuous sampling (infinite) ────────────────────────────────────────────
# count=0 runs forever until Ctrl-C.  interval=30 waits 30 s between polls.
# data = abc.run(count=0, interval=30)

# ── Override RAM flush-and-clear limit ────────────────────────────────────────
# When system RAM usage reaches ram_pct %, all enabled outputs are written /
# appended, TIME_SERIES_STORE is fully cleared, and sampling continues.
# User-supplied value overrides the MEM_RAM_PCT_LIMIT default (70 %) set in
# ab_power_meter_monitor.py.
# data = abc.run(count=0, interval=30, ram_pct=60)

# ── Fixed sample count with custom RAM limit ──────────────────────────────────
# data = abc.run(count=10, interval=15, ram_pct=80)

# ── Access data after the run ─────────────────────────────────────────────────
# The return value mirrors abm.TIME_SERIES_STORE at the moment run() returns.
# Keys at each level:
#   data[<device_ip>][<table_name>]["columns"][<param_name>]  -> List of values
#   data[<device_ip>][<table_name>]["timestamps_local"]       -> List of timestamps
#   data[<device_ip>][<table_name>]["units"][<param_name>]    -> unit string
#
# Example: iterate all devices and tables
for ip, tables in data.items():
    for table_name, table_data in tables.items():
        timestamps = table_data.get("timestamps_local", [])
        for param, values in table_data.get("columns", {}).items():
            unit = table_data.get("units", {}).get(param, "")
            print(f"  {ip} / {table_name} / {param} ({unit}): {len(values)} sample(s)")
