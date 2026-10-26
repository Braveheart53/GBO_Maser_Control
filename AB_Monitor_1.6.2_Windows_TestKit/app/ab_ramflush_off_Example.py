#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace <wwallace@nrao.edu>
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Example: caller and a caller-driven Prometheus endpoint with RAM flush OFF.

===============================================================================
 Title          : RAM-flush-off example (caller + Prometheus)
 Filename       : ab_ramflush_off_Example.py
 Version        : 1.1.0 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu   (alt: naval.antennas@gmail.com)
 Created        : 2026-07-22
 Modified       : 2026-10-26
 Language       : Python 3.12 (primary); 3.8 - 3.11; 3.7.11 language level
 Requires       : ab_power_meter_monitor.py >= 1.6.0, ab_meter_caller.py >= 1.3.0,
                  prometheus_client (prometheus mode only)
 Licence        : GPL-3.0-or-later (GNU General Public License v3.0 or later)
 Status         : Example
===============================================================================

Run modes
---------
    python ab_ramflush_off_Example.py caller --count 5 --interval 30
        drive the monitor from Python, automatic RAM flush OFF: the store keeps
        every sample of the run (watch RAM on long runs).

    python ab_ramflush_off_Example.py prometheus --port 9184 --interval 30
        a minimal caller-driven /metrics endpoint, RAM flush OFF, memory
        bounded instead by abm.trim_store(--max-history).

For production metrics use ab_prometheus_exporter.py (0.1.0+): it keeps only
the latest reading per table and never accumulates history at all.

What "RAM flush" means: when a memory limit is reached the monitor writes
every enabled output and then frees the in-memory rows. Turning it off keeps
one uninterrupted store; "Flush RAM Now" / perform_ram_flush() still work.

REVISION HISTORY
----------------
1.1.0  2026-10-26  Trimming uses abm.trim_store() (keeps the monitor's row
                   metadata aligned - hand-slicing the store lists is no
                   longer recommended); the Prometheus demo polls through
                   abc.poll_once() (monitor 1.6.0 run API), honours
                   STOP_COLLECTION and Ctrl-C, and skips non-finite values.
1.0.1  2026-08-13  Persistent logging filter for the per-poll config logging.
===============================================================================
"""

# %% IMPORTS ===================================================================
import argparse
import logging
import math
import os
import sys
import threading
import time


class _WarnAndAbove(logging.Filter):
    """Drop monitor records below WARNING (kept for 1.5.x monitors that re-init logging)."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        return record.levelno >= logging.WARNING


logging.getLogger("ABMonitor").addFilter(_WarnAndAbove())


# %% PART A - the caller from Python, RAM flush OFF ===================================
def demo_caller(count: int, interval: float, ips: str) -> None:
    """Poll through the caller with automatic RAM flushing disabled."""
    import ab_meter_caller as abc
    import ab_power_meter_monitor as abm
    if ips:
        abm.IP_LIST = abm.dedupe_ips(ips.split(","))
    data = abc.run(count=count, interval=interval, enable_ram_flush=False)
    for ip, tables in data.items():
        rows = len(tables.get("Real_Time_Power_Table", {}).get("timestamps_local", []))
        print(f"{ip}: {rows} sample(s) retained (no mid-run clear).")


# %% PART B - caller-driven Prometheus endpoint, RAM flush OFF =========================
def serve_prometheus(port: int, interval: float, ips: str, max_history: int) -> None:
    """Minimal /metrics endpoint polling through the caller (flush OFF, trimmed)."""
    import ab_meter_caller as abc
    import ab_power_meter_monitor as abm
    try:
        from prometheus_client import REGISTRY, start_http_server
        from prometheus_client.core import GaugeMetricFamily
    except ImportError:
        sys.stderr.write("Install prometheus_client first.\n")
        sys.exit(1)
    if ips:
        abm.IP_LIST = abm.dedupe_ips(ips.split(","))
    abm.ENABLE_RAM_FLUSH = 0

    class _Collector:
        """Latest value per parameter from the store (no network on a scrape)."""

        def collect(self):
            gauge = GaugeMetricFamily("ab_meter_value", "Latest AB power-meter parameter value.",
                                      labels=["ip", "table", "parameter", "unit"])
            for ip, tables in abm.store_snapshot().items():
                for tname, table in tables.items():
                    for param, values in table["columns"].items():
                        value = values[-1] if values else None
                        if value is None or not math.isfinite(value):
                            continue
                        gauge.add_metric([ip, tname, param, table["units"].get(param, "")], float(value))
            yield gauge

    REGISTRY.register(_Collector())
    start_http_server(port)
    print(f"Exporter on :{port}/metrics - RAM flush OFF, history "
          f"{'unbounded' if max_history <= 0 else max_history}")
    stop = threading.Event()
    stop_file = abm.stop_signal_file()
    cfg = abm.begin_run()
    try:
        while not stop.is_set() and not os.path.exists(stop_file):
            started = time.monotonic()
            abc.poll_once(cfg)
            abm.trim_store(max_history)          # bounds memory without a flush
            stop.wait(max(0.0, interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        pass
    finally:
        abm.end_run(cfg)


# %% ENTRY POINT =====================================================================
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Caller + Prometheus with RAM flush disabled.",
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("mode", choices=["caller", "prometheus"])
    parser.add_argument("--count", type=int, default=5, help="[caller] polls (0 = until stopped).")
    parser.add_argument("--interval", type=float, default=30.0, help="Seconds between polls.")
    parser.add_argument("--ips", type=str, default=None, help="Comma-separated meter IPs.")
    parser.add_argument("--port", type=int, default=9184, help="[prometheus] /metrics port.")
    parser.add_argument("--max-history", type=int, default=3, dest="max_history",
                        help="[prometheus] rows kept per table (0 = unbounded).")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.mode == "caller":
        demo_caller(args.count, args.interval, args.ips)
    else:
        serve_prometheus(args.port, args.interval, args.ips, args.max_history)
