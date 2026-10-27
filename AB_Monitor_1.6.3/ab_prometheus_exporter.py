#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace <wwallace@nrao.edu>
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Prometheus exporter for the Allen-Bradley site power meters.

===============================================================================
 Title          : AB Power Meter Prometheus Exporter
 Filename       : ab_prometheus_exporter.py
 Version        : 0.1.1 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu   (alt: naval.antennas@gmail.com)
 Phone          : +1 (304) 456-2216
 Created        : 2026-07-22
 Modified       : 2026-10-26
 Language       : Python 3.12 (primary, tested); 3.8 - 3.11 supported;
                  3.7.11 with prometheus_client==0.17.1 (requirements-py37.txt)
 Requires       : prometheus_client, ab_power_meter_monitor.py >= 1.6.2
                  (requests, beautifulsoup4, lxml)
 Licence        : GPL-3.0-or-later (GNU General Public License v3.0 or later)
 Status         : Release candidate - verified offline against tests/ emulator
===============================================================================

DESIGN
------
The monitor is used as a LIBRARY: a background thread calls
abm.poll_all_devices() on a fixed cadence (each meter read page by page,
meters in parallel) and stores the result in this exporter's own LATEST
SNAPSHOT, organised exactly like the monitor's dicts:

    _LATEST[ip][table] = {"fetch_epoch": float,
                          "values": {parameter: (value, unit)}}

A custom collector builds fresh metric families from that snapshot on every
scrape - no network I/O on a scrape, so scrape_timeout can never trip. It
does not use abm.main() or the headless loop (no stdout redirection, no
signal hijacking, no RAM flush logic), and it no longer copies samples into
abm.TIME_SERIES_STORE at all, so its memory is constant.

NO REPLICATED / STALE DATA (0.1.0)
----------------------------------
* A table is replaced as a whole when it is fetched and REMOVED when it
  fails or its meter is down, so a dead meter's last values are not exported
  over and over as if they were current (0.0.x kept re-exporting them from
  the store). ab_meter_up / ab_meter_table_up report the outage instead.
* --stale-after (default 2.5 x interval) also drops any table whose last
  good fetch is older than that, e.g. if the poll thread ever stalled.
* Every metric family is new per scrape and every label set is unique:
  duplicate parameter names inside a table are disambiguated by the
  monitor ("Reserved [#3]") and a guard skips any repeat, so one bad row can
  never make Prometheus reject the whole scrape for duplicate series.
* Between two polls Prometheus scrapes the same latest value again; that is
  how the pull model works (one sample per scrape). If the TSDB must hold
  exactly one sample per meter reading, run with --timestamps: each value is
  exposed with its fetch time, so repeated scrapes of one reading collapse
  to a single sample. Prometheus recommends against exporter timestamps in
  general (they change staleness handling), hence opt-in. Alternatively
  set the job's scrape_interval equal to --interval.
* ab_meter_samples_total is now a real monotonic counter (successful table
  fetches). 0.0.x exported the number of rows retained in the trimmed store
  as a "counter", which plateaued at --max-history - not a valid counter.

METRICS
-------
    ab_meter_value{ip,table,parameter,unit}         latest value (gauge)
    ab_meter_up{ip}                                  1 = meter answered last poll
    ab_meter_table_up{ip,table}                      1 = table fetched and fresh
    ab_meter_last_fetch_timestamp_seconds{ip,table}  Unix time of last good fetch
    ab_meter_samples_total{ip,table}                 successful fetches (counter)
    ab_meter_table_fetch_errors_total{ip,table}      failed fetches (counter)
    ab_meter_poll_duration_seconds                   duration of the last poll
    ab_meter_last_poll_timestamp_seconds             Unix time of the last poll
    ab_meter_polls_total                             poll cycles (counter)
    ab_meter_poll_errors_total                       wholesale failures (counter)
    ab_meter_build_info{version,monitor_version}     constant 1

USAGE
-----
    python ab_prometheus_exporter.py                              # :9184
    python ab_prometheus_exporter.py --ips 10.16.130.50,10.16.130.54 --interval 30
    python ab_prometheus_exporter.py --timestamps                 # one sample per reading
Stop: Ctrl-C, SIGTERM (systemctl stop), Ctrl+Break (Windows), or `python ab_stop.py`.

    scrape_configs:
      - job_name: ab_power_meter
        scrape_interval: 30s            # = --interval
        static_configs: [{targets: ["exporter-host:9184"]}]

REFERENCES
----------
[E1] Prometheus, "Writing exporters" (collectors, counters, timestamps, _up).
     https://prometheus.io/docs/instrumenting/writing_exporters/
[E2] Prometheus, "Querying basics - Staleness" (5 min lookback; explicit
     timestamps). https://prometheus.io/docs/prometheus/latest/querying/basics/
[E3] prometheus_client, "Custom Collectors" (add_metric timestamp=).
     https://prometheus.github.io/client_python/collector/custom/

REVISION HISTORY
----------------
0.1.1  2026-10-26  Windows: SIGBREAK (Ctrl+Break, CTRL_BREAK_EVENT) stops the
                   exporter cleanly like SIGINT/SIGTERM (it used to kill it).
0.1.0  2026-10-26  Data model fixed (see NO REPLICATED / STALE DATA):
                   own per-table latest snapshot instead of the monitor's
                   accumulating store; failed/stale tables are no longer
                   exported; samples_total is monotonic; new table_up,
                   last_fetch_timestamp and fetch-error metrics; optional
                   --timestamps; duplicate-label guard; --stale-after and
                   --bind options; prompt shutdown (the poll is cancelled
                   between pages); de-duplicated IP list; monitor console
                   handler disabled so log lines are not printed twice.
                   --max-history kept for service compatibility (ignored).
0.0.3  2026-08-13  Docs: requirements files, Python 3.7.11 note.
0.0.1  2026-07-22  Initial reference exporter.
===============================================================================
"""

# %% ===========================================================================
#    IMPORTS
# =============================================================================
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    from prometheus_client import REGISTRY, start_http_server
    from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
except ImportError as exc:  # pragma: no cover - environment guard
    sys.stderr.write("FATAL: prometheus_client is not installed "
                     "(mamba install -c conda-forge prometheus_client).\n"
                     f"(import error: {exc})\n")
    sys.exit(1)

import ab_power_meter_monitor as abm  # noqa: E402  (library use only)

__version__ = "0.1.1"
EXPORTER_VERSION = __version__          # 0.0.x name, kept
METRIC_PREFIX = "ab_meter"

# %% ===========================================================================
#    PYTHON VERSION CHECK (advisory)
# =============================================================================
MIN_PYTHON = (3, 7, 11)
if sys.version_info < MIN_PYTHON:
    sys.stderr.write("WARNING: ab_prometheus_exporter requires Python >= {0}; running {1}.\n".format(
        ".".join(map(str, MIN_PYTHON)), ".".join(map(str, sys.version_info[:3]))))

# %% ===========================================================================
#    DEFAULTS (command-line options override them)
# =============================================================================
DEFAULT_PORT = 9184
DEFAULT_BIND = "0.0.0.0"
DEFAULT_INTERVAL_SEC = 30.0
DEFAULT_MAX_HISTORY = 3                 # accepted for compatibility; ignored since 0.1.0
STALE_FACTOR = 2.5                      # default --stale-after = 2.5 x interval

# %% ===========================================================================
#    LOGGING (the exporter's own stream; monitor records go through it once)
# =============================================================================
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                    datefmt="%Y-%m-%dT%H:%M:%S")
log = logging.getLogger("ab_exporter")
# The monitor's own stdout handler would print each of its records a second
# time (they also propagate to the root handler above). Keep WARNING+ once.
abm.set_console_logging(False)
logging.getLogger("ABMonitor").setLevel(logging.WARNING)


# %% ===========================================================================
#    SHARED STATE: written by the poll thread, read by the collector
# =============================================================================
_STATE_LOCK = threading.Lock()
# ip -> table -> {"fetch_epoch": float, "values": {param: (value, unit)}}
_LATEST: Dict[str, Dict[str, Dict[str, Any]]] = {}
_DEVICE_UP: Dict[str, bool] = {}
_SAMPLES: Dict[Tuple[str, str], int] = {}        # successful table fetches (monotonic)
_FETCH_ERRORS: Dict[Tuple[str, str], int] = {}   # failed table fetches (monotonic)
_STATS: Dict[str, float] = {"polls_total": 0, "poll_errors_total": 0,
                            "last_poll_unixtime": 0.0, "last_poll_duration": 0.0}


def _monitor_version() -> str:
    """The monitor's __version__ (or 'unknown')."""
    return str(getattr(abm, "__version__", "unknown"))


def apply_poll(data: Dict[str, Dict[str, Any]], ip_list: List[str]) -> None:
    """Install one poll_all_devices() result in the latest snapshot.

    Per table: replaced as a whole on a good fetch (counter +1), removed on a
    failed fetch (error counter +1). Per meter: when it returned nothing at
    all, every one of its tables is removed and ab_meter_up becomes 0.
    """
    meta = data.get("__poll_meta__", {})
    ok_ips = set(meta.get("ok_ips", []))
    fresh: Dict[str, Dict[str, Dict[str, Any]]] = {}
    failed: List[Tuple[str, str]] = []
    for key, table in data.items():
        if key == "__poll_meta__" or not isinstance(table, dict):
            continue
        info = table.get("_meta", {})
        ip, tname = info.get("source_ip"), info.get("table_name")
        if not ip or not tname:
            continue
        if info.get("error"):
            failed.append((ip, tname))
            continue
        series = abm.extract_numeric_series(table)      # unique names per table
        if not series:
            failed.append((ip, tname))
            continue
        fresh.setdefault(ip, {})[tname] = {"fetch_epoch": float(info.get("fetch_epoch") or time.time()),
                                           "values": dict(series)}
    with _STATE_LOCK:
        for ip in ip_list:
            _DEVICE_UP[ip] = ip in ok_ips
            if ip not in ok_ips:
                _LATEST.pop(ip, None)                   # meter down: export nothing stale
        for ip, tables in fresh.items():
            for tname, entry in tables.items():
                _LATEST.setdefault(ip, {})[tname] = entry
                _SAMPLES[(ip, tname)] = _SAMPLES.get((ip, tname), 0) + 1
        for ip, tname in failed:
            if ip in _LATEST:
                _LATEST[ip].pop(tname, None)
            _FETCH_ERRORS[(ip, tname)] = _FETCH_ERRORS.get((ip, tname), 0) + 1


# %% ===========================================================================
#    BACKGROUND POLLER
# =============================================================================
def poll_loop(stop_event: threading.Event, ip_list: List[str], interval: float, retries: int,
              retry_delay: float, max_history: int = 0, timeout: Optional[float] = None) -> None:
    """Poll every meter each ``interval`` seconds until ``stop_event`` is set.

    ``max_history`` is accepted for 0.0.x compatibility and ignored.
    """
    log.info("Poll loop started - %d meter(s), interval %.1f s", len(ip_list), interval)
    stop_file = abm.stop_signal_file()
    while not stop_event.is_set():
        started = time.monotonic()
        try:
            data = abm.poll_all_devices(ip_list, abm.TABLE_NAMES, retries=retries,
                                        retry_delay=retry_delay, stop_event=stop_event,
                                        timeout=timeout)
            meta = data.get("__poll_meta__", {})
            if meta.get("cancelled"):
                break                                  # shutting down: keep the last good state
            apply_poll(data, ip_list)
            _STATS["polls_total"] += 1
            _STATS["last_poll_unixtime"] = time.time()
            if meta.get("all_failed", False):
                _STATS["poll_errors_total"] += 1
                log.warning("Poll: ALL %d meter(s) failed.", len(ip_list))
            data.clear()
        except Exception as exc:                       # the loop must never die
            _STATS["poll_errors_total"] += 1
            log.error("Poll cycle raised: %s", exc, exc_info=True)
        finally:
            _STATS["last_poll_duration"] = time.monotonic() - started
        deadline = started + interval
        while not stop_event.is_set():
            now = time.monotonic()
            if now >= deadline:
                break
            if os.path.exists(stop_file):
                log.info("STOP_COLLECTION file detected - stopping.")
                stop_event.set()
                break
            stop_event.wait(min(0.5, deadline - now))
    log.info("Poll loop exited.")


# %% ===========================================================================
#    CUSTOM COLLECTOR (read-only, instant)
# =============================================================================
class ABMeterCollector:
    """Builds new metric families from the latest snapshot on every scrape [E1]."""

    def __init__(self, ip_list: List[str], stale_after: float, expose_timestamps: bool = False) -> None:
        self._ip_list = list(ip_list)
        self._stale_after = float(stale_after)
        self._timestamps = bool(expose_timestamps)

    def collect(self):  # noqa: D401 - prometheus_client interface
        """Yield the metric families for one scrape."""
        now = time.time()
        with _STATE_LOCK:
            latest = {ip: {t: (e["fetch_epoch"], dict(e["values"])) for t, e in tables.items()}
                      for ip, tables in _LATEST.items()}
            device_up = dict(_DEVICE_UP)
            samples = dict(_SAMPLES)
            errors = dict(_FETCH_ERRORS)
            stats = dict(_STATS)

        value = GaugeMetricFamily(f"{METRIC_PREFIX}_value",
                                  "Latest Allen-Bradley power-meter parameter value.",
                                  labels=["ip", "table", "parameter", "unit"])
        table_up = GaugeMetricFamily(f"{METRIC_PREFIX}_table_up",
                                     "1 if the table was fetched on the last poll and is fresh.",
                                     labels=["ip", "table"])
        fetched_at = GaugeMetricFamily(f"{METRIC_PREFIX}_last_fetch_timestamp_seconds",
                                       "Unix time of the last successful fetch of the table.",
                                       labels=["ip", "table"])
        seen = set()
        for ip in sorted(latest):
            for tname in sorted(latest[ip]):
                epoch, values = latest[ip][tname]
                fresh = self._stale_after <= 0 or now - epoch <= self._stale_after
                table_up.add_metric([ip, tname], 1.0 if fresh else 0.0)
                fetched_at.add_metric([ip, tname], epoch)
                if not fresh:
                    continue                            # never export stale readings
                for param, (reading, unit) in values.items():
                    labels = (ip, tname, param, unit)
                    if labels in seen:                  # guard: one series per label set
                        continue
                    seen.add(labels)
                    value.add_metric(list(labels), float(reading),
                                     timestamp=epoch if self._timestamps else None)
        yield value
        yield table_up
        yield fetched_at

        counter = CounterMetricFamily(f"{METRIC_PREFIX}_samples",
                                      "Successful fetches of the table since exporter start.",
                                      labels=["ip", "table"])
        for (ip, tname), count in sorted(samples.items()):
            counter.add_metric([ip, tname], float(count))
        yield counter
        counter = CounterMetricFamily(f"{METRIC_PREFIX}_table_fetch_errors",
                                      "Failed fetches of the table since exporter start.",
                                      labels=["ip", "table"])
        for (ip, tname), count in sorted(errors.items()):
            counter.add_metric([ip, tname], float(count))
        yield counter

        up = GaugeMetricFamily(f"{METRIC_PREFIX}_up", "1 if the meter answered the last poll.",
                               labels=["ip"])
        for ip in self._ip_list:
            up.add_metric([ip], 1.0 if device_up.get(ip, False) else 0.0)
        yield up
        yield GaugeMetricFamily(f"{METRIC_PREFIX}_poll_duration_seconds",
                                "Duration of the last poll cycle.", value=stats["last_poll_duration"])
        yield GaugeMetricFamily(f"{METRIC_PREFIX}_last_poll_timestamp_seconds",
                                "Unix time of the last poll cycle.", value=stats["last_poll_unixtime"])
        yield CounterMetricFamily(f"{METRIC_PREFIX}_polls", "Poll cycles since exporter start.",
                                  value=stats["polls_total"])
        yield CounterMetricFamily(f"{METRIC_PREFIX}_poll_errors",
                                  "Poll cycles in which every meter failed, or that raised.",
                                  value=stats["poll_errors_total"])
        info = GaugeMetricFamily(f"{METRIC_PREFIX}_build_info", "Exporter build info (constant 1).",
                                 labels=["version", "monitor_version"])
        info.add_metric([__version__, _monitor_version()], 1.0)
        yield info


# %% ===========================================================================
#    ENTRY POINT
# =============================================================================
def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prometheus exporter for Allen-Bradley power meters.",
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="/metrics TCP port.")
    parser.add_argument("--bind", type=str, default=DEFAULT_BIND, help="Listen address.")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SEC,
                        help="Seconds between poll cycles.")
    parser.add_argument("--ips", type=str, default=None,
                        help="Comma-separated meter IPs (default: the monitor's IP_LIST).")
    parser.add_argument("--retries", type=int, default=abm.HTTP_RETRY_COUNT,
                        help="Attempts per page (1 = no retry).")
    parser.add_argument("--retry-delay", type=float, default=abm.HTTP_RETRY_DELAY_SEC,
                        dest="retry_delay", help="Seconds between attempts.")
    parser.add_argument("--timeout", type=float, default=abm.HTTP_TIMEOUT_SEC,
                        help="HTTP timeout per request (s).")
    parser.add_argument("--stale-after", type=float, default=None, dest="stale_after",
                        help="Drop a table whose last good fetch is older than this "
                             "(s; default 2.5 x interval; 0 = never).")
    parser.add_argument("--timestamps", action="store_true",
                        help="Expose each value with its fetch time (one TSDB sample per reading).")
    parser.add_argument("--url-template", type=str, default=None, dest="url_template",
                        help='Meter page URL template (default "http://{ip}/{page}").')
    parser.add_argument("--max-history", type=int, default=DEFAULT_MAX_HISTORY, dest="max_history",
                        help="Ignored since 0.1.0 (kept so existing service files still start).")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    """Start the HTTP server and the poll thread; block until stopped."""
    args = _parse_args(argv)
    ip_list = abm.dedupe_ips(args.ips.split(",") if args.ips else abm.IP_LIST)
    if not ip_list:
        log.error("No meter IPs configured (use --ips or set IP_LIST in the monitor).")
        sys.exit(2)
    if args.url_template:
        abm.METER_URL_TEMPLATE = args.url_template
    stale_after = args.interval * STALE_FACTOR if args.stale_after is None else args.stale_after
    try:
        stop_file = abm.stop_signal_file()
        if os.path.exists(stop_file):
            os.remove(stop_file)                    # stale stop file: do not exit at once
    except OSError:
        pass
    stop_event = threading.Event()

    def _handle_signal(signum: int, _frame: Any) -> None:
        log.info("Signal %s received - shutting down.", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    if hasattr(signal, "SIGBREAK"):                 # Windows: Ctrl+Break / CTRL_BREAK_EVENT (0.1.1)
        signal.signal(signal.SIGBREAK, _handle_signal)
    REGISTRY.register(ABMeterCollector(ip_list, stale_after, args.timestamps))
    start_http_server(args.port, addr=args.bind)
    log.info("Exporter %s listening on %s:%d/metrics - meters %s, interval %.1f s, stale after "
             "%.0f s, timestamps %s", __version__, args.bind, args.port, ip_list, args.interval,
             stale_after, "on" if args.timestamps else "off")
    poller = threading.Thread(target=poll_loop, name="ab_poll_loop", daemon=True,
                              args=(stop_event, ip_list, args.interval, args.retries,
                                    args.retry_delay, 0, args.timeout))
    poller.start()
    try:
        while not stop_event.is_set():              # keeps the process (and HTTP server) alive
            stop_event.wait(timeout=1.0)
    except KeyboardInterrupt:
        stop_event.set()
    log.info("Stopping - waiting for the poll thread ...")
    poller.join(timeout=max(5.0, args.timeout * 2 + 5.0))
    try:
        if os.path.exists(abm.stop_signal_file()):
            os.remove(abm.stop_signal_file())
    except OSError:
        pass
    log.info("Exporter stopped cleanly.")


if __name__ == "__main__":
    main()
