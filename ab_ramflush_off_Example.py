#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
# %% ab_ramflush_off_Example.py Info
===================================
Worked example: use the caller from Python AND serve Prometheus, in both
cases with the monitor's automatic RAM flush/clear ("RAM dumps") turned OFF.

Compatible with:
    ab_power_meter_monitor.py  v1.5.2+   (ENABLE_RAM_FLUSH switch)
    ab_meter_caller.py         v1.2.9+   (run(enable_ram_flush=...))
    prometheus_client          (for the Prometheus demo)

Two run modes
-------------
    python ab_ramflush_off_Example.py caller
        → drive the monitor from Python via the caller, RAM dumps OFF.

    python ab_ramflush_off_Example.py prometheus --port 9184 --interval 30
        → a caller-driven Prometheus exporter, RAM dumps OFF.

What "RAM dumps" means here
---------------------------
The monitor's headless loop can, when system RAM crosses MEM_RAM_PCT_LIMIT
(or the store/free-RAM thresholds), FLUSH enabled file outputs and then CLEAR
the in-memory TIME_SERIES_STORE to reclaim memory.  That clear is the "RAM
dump".  Turning it off gives one uninterrupted, fully-accumulated store — at
the cost of unbounded growth on long runs, so watch memory.

How it is turned off in each path
---------------------------------
• Caller  → pass enable_ram_flush=False to abc.run(); it sets
            abm.ENABLE_RAM_FLUSH = 0 for the run.
• Prometheus (this example) → same, because it drives polling through the
            caller.  We ALSO set abm.ENABLE_RAM_FLUSH = 0 explicitly as a belt.
• Dedicated exporter (ab_prometheus_exporter.py) → it NEVER runs the headless
            loop, so it never dumps regardless of the switch; bound memory with
            its --max-history flag instead (0 = unbounded / keep full history).

# %% Author Info
@Author: W. Wallace — NRAO / Green Bank Observatory
Date   : 2026-07-22
Python : 3.7.11+
Version: 1.0.1
"""
import argparse
import logging
import sys
import threading

# The caller path calls abm.main() once per poll, which re-initialises the
# monitor's logger and logs its full config at INFO on EVERY poll.  A plain
# setLevel() would be undone by that re-init (it forces the level back to
# DEBUG), so we attach a persistent FILTER instead: filters live on the logger
# and survive handler/level re-initialisation, dropping INFO/DEBUG while still
# letting WARNING/ERROR (e.g. failed fetches) through.
class _WarnAndAbove(logging.Filter):
    """Drop records below WARNING; survives the monitor's logger re-init."""
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        return record.levelno >= logging.WARNING


logging.getLogger("ABMonitor").addFilter(_WarnAndAbove())


# ===========================================================================
# %% PART A — use the caller from Python, RAM dumps OFF
# ===========================================================================
def demo_caller(count: int, interval: float, ips: str) -> None:
    """
    Poll via the caller with automatic RAM flushing disabled.

    enable_ram_flush=False is the whole point: the monitor never flushes or
    clears TIME_SERIES_STORE mid-run, so `data` accumulates every sample for
    the full run.  (count=0 would poll forever — use ab_stop.py to stop.)
    """
    import ab_meter_caller as abc
    import ab_power_meter_monitor as abm

    if ips:
        abm.IP_LIST = [s.strip() for s in ips.split(",") if s.strip()]

    # RAM dumps OFF — one uninterrupted, fully-accumulated store.
    data = abc.run(count=count, interval=interval, enable_ram_flush=False)

    # `data` is abm.TIME_SERIES_STORE — same store every output reads from.
    for ip, tables in data.items():
        pwr = tables.get("Real_Time_Power_Table", {})
        n = len(pwr.get("timestamps_local", []))
        print(f"{ip}: {n} sample(s) retained (no mid-run clear).")
    print("Done — enable_ram_flush=False kept every sample in RAM.")


# ===========================================================================
# %% PART B — caller-driven Prometheus exporter, RAM dumps OFF
# ===========================================================================
def serve_prometheus(port: int, interval: float, ips: str, max_history: int) -> None:
    """
    Minimal Prometheus exporter that polls through the caller with RAM dumps
    OFF, on a background thread, and serves the latest values on /metrics.

    NOTE — for production prefer the dedicated ab_prometheus_exporter.py: it
    calls the monitor's poll primitives directly (no per-poll global state
    churn) and never runs the headless loop, so it never dumps anyway.  This
    example exists to show the *caller* path with dumps disabled.
    """
    import ab_meter_caller as abc
    import ab_power_meter_monitor as abm
    try:
        from prometheus_client import start_http_server, REGISTRY
        from prometheus_client.core import GaugeMetricFamily
    except ImportError:
        sys.stderr.write("Install prometheus_client first: pip install prometheus_client\n")
        sys.exit(1)

    if ips:
        abm.IP_LIST = [s.strip() for s in ips.split(",") if s.strip()]

    # RAM dumps OFF — explicit belt (the caller also sets this via run()).
    abm.ENABLE_RAM_FLUSH = 0

    def _trim() -> None:
        """Bound memory WITHOUT dumping: keep only the last N samples.
        max_history <= 0 disables trimming entirely (unbounded — watch RAM)."""
        if max_history <= 0:
            return
        with abm._TS_LOCK:
            for tables in abm.TIME_SERIES_STORE.values():
                for td in tables.values():
                    ts = td["timestamps_local"]
                    if len(ts) > max_history:
                        del ts[:-max_history]
                        for col in td["columns"].values():
                            if len(col) > max_history:
                                del col[:-max_history]

    class _Collector:
        """Reads TIME_SERIES_STORE on each scrape — no network I/O on scrape."""
        def collect(self):
            g = GaugeMetricFamily(
                "ab_meter_value", "Latest AB power-meter parameter value.",
                labels=["ip", "table", "parameter", "unit"])
            with abm._TS_LOCK:
                for ip, tables in abm.TIME_SERIES_STORE.items():
                    for tname, td in tables.items():
                        units = td.get("units", {})
                        for param, vals in td.get("columns", {}).items():
                            val = vals[-1] if vals else None
                            if val is None:
                                continue          # never emit None
                            g.add_metric([ip, tname, param, units.get(param, "")],
                                         float(val))
            yield g

    REGISTRY.register(_Collector())
    start_http_server(port)
    print(f"Exporter on :{port}/metrics — RAM dumps OFF, "
          f"history={'unbounded' if max_history <= 0 else max_history}")

    # Background poll loop via the caller, RAM dumps OFF.  Trim after the store
    # is updated so memory stays bounded even though nothing is ever dumped.
    def _poll_forever():
        # count=0 → poll forever; enable_ram_flush=False → never flush/clear.
        # We wrap the caller so we can trim between polls; the caller's own
        # loop would also work, but this makes the trim point explicit.
        import time
        abm.HEADLESS_LOOP_COUNT = 1          # one poll per abm.main() call
        abc.abm.ENABLE_RAM_FLUSH = 0
        while True:
            abc.poll_once()                  # one full poll -> updates store
            _trim()
            time.sleep(interval)

    t = threading.Thread(target=_poll_forever, name="ab_caller_poll", daemon=True)
    t.start()

    # Block the main thread so the daemon HTTP server stays alive.
    threading.Event().wait()


# ===========================================================================
# %% Entry point
# ===========================================================================
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Example: caller + Prometheus with RAM dumps disabled.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("mode", choices=["caller", "prometheus"],
                   help="Which demo to run.")
    p.add_argument("--count", type=int, default=5,
                   help="[caller] poll cycles (0 = forever).")
    p.add_argument("--interval", type=float, default=30.0,
                   help="Seconds between polls.")
    p.add_argument("--ips", type=str, default=None,
                   help="Comma-separated device IPs (overrides IP_LIST).")
    p.add_argument("--port", type=int, default=9184,
                   help="[prometheus] /metrics port.")
    p.add_argument("--max-history", type=int, default=3, dest="max_history",
                   help="[prometheus] trailing samples kept (0 = unbounded).")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.mode == "caller":
        demo_caller(args.count, args.interval, args.ips)
    else:
        serve_prometheus(args.port, args.interval, args.ips, args.max_history)
