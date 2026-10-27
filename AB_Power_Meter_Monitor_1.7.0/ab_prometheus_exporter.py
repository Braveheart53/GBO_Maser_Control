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
 Version        : 0.2.0 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu   (alt: naval.antennas@gmail.com)
 Phone          : +1 (304) 456-2216
 Created        : 2026-07-22
 Modified       : 2026-10-27
 Language       : Python 3.7.11+ (tested 3.7.12 with prometheus_client 0.17.1)
                  and Python 3.12 (tested with prometheus_client 0.26.0).
                  The source stays at the Python 3.7 language level.
 Requires       : prometheus_client (3.7: ==0.17.1, see requirements-py37.txt),
                  ab_power_meter_monitor.py >= 1.6.2 (requests, beautifulsoup4,
                  lxml). Grafana dashboard: ab_grafana_dashboard.py, grafana/.
 Licence        : GPL-3.0-or-later (GNU General Public License v3.0 or later)
 Copyright      : Copyright (C) 2026 William W. Wallace
 Status         : Release candidate - verified against the meter emulator with
                  Prometheus 3.5, Pushgateway 1.11 and Grafana 12.2
===============================================================================

DESIGN
------
The monitor is used as a LIBRARY only: abm.poll_all_devices() reads every
meter (each meter strictly one page at a time, meters in parallel) and the
result is kept in this exporter's own LATEST SNAPSHOT:

    _LATEST[ip][table] = {"fetch_epoch": float,
                          "values": {parameter: (value, unit)}}

A custom collector builds fresh metric families from that snapshot on every
scrape (no network I/O on a scrape). The monitor's run machinery (begin_run,
output writers, RAM flush, log file) is never started, so the exporter writes
NO files: no CSV/Markdown/FITS/XLSX/.vszh5, no log file (log records go to
stderr), no pickle, and no __pycache__ (sys.dont_write_bytecode, 0.2.0).

TWO WAYS TO RUN (0.2.0)
-----------------------
1  CONTINUOUS (default): poll every --interval seconds until stopped.
   * Prometheus PULLS /metrics (HTTP server on --port; --no-http to skip),
   * and/or every poll is PUSHED to a Pushgateway (--push-gateway URL),
   * and/or the dict is printed to stdout as one JSON line per poll
     (--print-json), e.g. for another program reading the pipe.
   --count N stops after N polls (0 = run until stopped).
2  SINGLE RUN (--once): one poll, then exit - for cron, a systemd timer or
   the Windows Task Scheduler. The panels update because the run PUSHES its
   values to a Pushgateway (--push-gateway); with no gateway given the dict
   is printed as JSON. Exit code 0 = at least one meter answered, 1 = no
   meter answered, 2 = the push failed.
   (A pull-only exporter would be gone before Prometheus scraped it, hence
   the Pushgateway [E4].)

From Python, the same two ways return the dict after every update:

    import ab_prometheus_exporter as exp
    snap = exp.poll_once(["10.16.130.50", "10.16.130.54"])          # single run
    for snap in exp.iter_updates(ips, interval=30):                  # continuous
        print(snap["by_unit"]["10.16.130.50"]["V"])                  # one panel's traces
    exp.start_server(ips, port=9184)        # optional: also serve /metrics

snapshot() layout (also the --print-json line):
    {"exporter": "0.2.0", "monitor": "1.6.4", "epoch": 1761543000.1,
     "time_utc": "2026-10-27T05:30:00Z", "poll_seconds": 1.9,
     "meters":  {ip: {"up": true, "tables": {table: {"fetch_epoch": ...,
                                     "values": {parameter: [value, unit]}}}}},
     "by_unit": {ip: {unit: {"<table> / <parameter>": value}}}}
by_unit is exactly how the Grafana dashboard groups the traces: one panel per
meter and unit ('' = no unit, shown as a table).

NO REPLICATED / STALE DATA (0.1.0)
----------------------------------
* A table is replaced as a whole when it is fetched and REMOVED when it
  fails or its meter is down; ab_meter_up / ab_meter_table_up report outages.
* --stale-after (default 2.5 x interval) also drops tables whose last good
  fetch is older than that.
* Duplicate parameter names are disambiguated by the monitor ("Reserved
  [#3]") and a guard skips any repeated label set.
* --timestamps exposes each value with its fetch time (pull only; a
  Pushgateway rejects pushed timestamps, so pushes never carry them).
* Counters (ab_meter_samples_total, ...) are only meaningful for a process
  that keeps running; they are left out of pushes.

METRICS
-------
    ab_meter_value{ip,table,parameter,unit}         latest value (gauge)
    ab_meter_up{ip}                                  1 = meter answered last poll
    ab_meter_table_up{ip,table}                      1 = table fetched and fresh
    ab_meter_last_fetch_timestamp_seconds{ip,table}  Unix time of last good fetch
    ab_meter_samples_total{ip,table}                 successful fetches (counter, pull)
    ab_meter_table_fetch_errors_total{ip,table}      failed fetches (counter, pull)
    ab_meter_poll_duration_seconds                   duration of the last poll
    ab_meter_last_poll_timestamp_seconds             Unix time of the last poll
    ab_meter_polls_total                             poll cycles (counter, pull)
    ab_meter_poll_errors_total                       wholesale failures (counter, pull)
    ab_meter_build_info{version,monitor_version}     constant 1

USAGE
-----
    python ab_prometheus_exporter.py                              # pull, :9184, every 30 s
    python ab_prometheus_exporter.py --ips 10.16.130.50,10.16.130.54 --interval 30
    python ab_prometheus_exporter.py --push-gateway http://pushgw:9091 --no-http
    python ab_prometheus_exporter.py --once --push-gateway http://pushgw:9091   # cron / Task Scheduler
    python ab_prometheus_exporter.py --once --print-json                         # dict to stdout
Stop: Ctrl-C, SIGTERM (systemctl stop), Ctrl+Break (Windows), or `python ab_stop.py`.
Prometheus and Grafana set-up: grafana/README_grafana.md.

REFERENCES
----------
[E1] Prometheus, "Writing exporters" (collectors, counters, timestamps, _up).
     https://prometheus.io/docs/instrumenting/writing_exporters/
[E2] Prometheus, "Querying basics - Staleness" (5 min lookback; explicit
     timestamps). https://prometheus.io/docs/prometheus/latest/querying/basics/
[E3] prometheus_client, "Custom Collectors" (add_metric timestamp=).
     https://prometheus.github.io/client_python/collector/custom/
[E4] Prometheus, "When to use the Pushgateway" (service-level batch jobs).
     https://prometheus.io/docs/practices/pushing/
[E5] prometheus_client, "Exporting to a Pushgateway" (push_to_gateway, PUT
     replaces the whole group). https://prometheus.github.io/client_python/exporting/pushgateway/

REVISION HISTORY
----------------
0.2.0  2026-10-27  Continuous and single-run operation for Grafana.
                   * --once (one poll, exit code), --count N, --no-http.
                   * --push-gateway / --push-job / --push-instance: every poll
                     is pushed (PUT, whole group replaced; no timestamps, no
                     counters) so single runs update the dashboard [E4, E5].
                   * --print-json: the dict as one JSON line per poll on stdout
                     (logging stays on stderr).
                   * Library API returning the dict after every update:
                     poll_once(), iter_updates(), snapshot(), by_unit(),
                     start_server(), push_metrics(), run().
                   * No files written: sys.dont_write_bytecode, no log file.
                   * Python 3.7.12 + prometheus_client 0.17.1 verified as well
                     as 3.12; poll_loop() kept for 0.1.x callers.
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

import sys

# No __pycache__ next to the monitor module: the exporter must not leave files
# behind (0.2.0). Set before the monitor is imported.
sys.dont_write_bytecode = True

import argparse  # noqa: E402
import datetime  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import signal  # noqa: E402
import socket  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple  # noqa: E402

try:
    from prometheus_client import REGISTRY, CollectorRegistry, push_to_gateway, start_http_server
    from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
except ImportError as exc:  # pragma: no cover - environment guard
    sys.stderr.write("FATAL: prometheus_client is not installed "
                     "(mamba install -c conda-forge prometheus_client; "
                     "Python 3.7: pip install prometheus_client==0.17.1).\n"
                     "(import error: {0})\n".format(exc))
    sys.exit(1)

import ab_power_meter_monitor as abm  # noqa: E402  (library use only)

__version__ = "0.2.0"
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
DEFAULT_PUSH_JOB = "ab_power_meter"     # job label of pushed metrics (Pushgateway group)
PUSH_TIMEOUT_SEC = 10.0                 # HTTP timeout of one push

# %% ===========================================================================
#    LOGGING (stderr only - stdout is reserved for --print-json)
# =============================================================================
logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                    datefmt="%Y-%m-%dT%H:%M:%S")
log = logging.getLogger("ab_exporter")
# The monitor's own stdout handler would print each of its records a second
# time (they also propagate to the root handler above) and would mix log text
# into --print-json output. Keep WARNING+ once, on stderr.
abm.set_console_logging(False)
logging.getLogger("ABMonitor").setLevel(logging.WARNING)


# %% ===========================================================================
#    SHARED STATE: written by the poller, read by the collector
# =============================================================================
_STATE_LOCK = threading.Lock()
# ip -> table -> {"fetch_epoch": float, "values": {param: (value, unit)}}
_LATEST: Dict[str, Dict[str, Dict[str, Any]]] = {}
_DEVICE_UP: Dict[str, bool] = {}
_SAMPLES: Dict[Tuple[str, str], int] = {}        # successful table fetches (monotonic)
_FETCH_ERRORS: Dict[Tuple[str, str], int] = {}   # failed table fetches (monotonic)
_STATS: Dict[str, float] = {"polls_total": 0, "poll_errors_total": 0,
                            "last_poll_unixtime": 0.0, "last_poll_duration": 0.0}
_SERVER: Dict[str, Any] = {"started": False}     # start_server() bookkeeping


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
#    THE DICT: snapshot() and by_unit()  (what callers and Grafana see)
# =============================================================================
def by_unit(meters: Dict[str, Any]) -> Dict[str, Dict[str, Dict[str, float]]]:
    """Group a snapshot's values per meter and unit, as the dashboard plots them.

    Parameters
    ----------
    meters : dict
        snapshot()["meters"].

    Returns
    -------
    dict
        ``{ip: {unit: {"<table> / <parameter>": value}}}``; unit '' = no unit.
    """
    grouped: Dict[str, Dict[str, Dict[str, float]]] = {}
    for ip, meter in meters.items():
        per_unit = grouped.setdefault(ip, {})
        for tname, table in sorted(meter.get("tables", {}).items(), key=lambda kv: abm._table_order(kv[0])):
            for param, (value, unit) in table["values"].items():
                per_unit.setdefault(unit, {})["{0} / {1}".format(tname, param)] = value
    return grouped


def snapshot() -> Dict[str, Any]:
    """Copy of the latest data, safe to keep, change or serialise (see header).

    Values are ``[value, unit]`` lists (JSON-friendly); meters that did not
    answer the last poll are listed with ``"up": false`` and no tables.
    """
    with _STATE_LOCK:
        meters: Dict[str, Any] = {}
        for ip in sorted(set(_DEVICE_UP) | set(_LATEST)):
            tables = {}
            for tname, entry in _LATEST.get(ip, {}).items():
                tables[tname] = {"fetch_epoch": entry["fetch_epoch"],
                                 "values": {p: [v, u] for p, (v, u) in entry["values"].items()}}
            meters[ip] = {"up": bool(_DEVICE_UP.get(ip, False)), "tables": tables}
        epoch = float(_STATS["last_poll_unixtime"])
        duration = float(_STATS["last_poll_duration"])
    stamp = (datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
             if epoch else "")
    return {"exporter": __version__, "monitor": _monitor_version(), "epoch": epoch,
            "time_utc": stamp, "poll_seconds": round(duration, 3),
            "meters": meters, "by_unit": by_unit(meters)}


# %% ===========================================================================
#    POLLING: one update = one poll of every meter
# =============================================================================
def poll_once(ip_list: Optional[List[str]] = None, retries: Optional[int] = None,
              retry_delay: Optional[float] = None, timeout: Optional[float] = None,
              stop_event: Optional[threading.Event] = None) -> Dict[str, Any]:
    """Poll every meter once, update the exported values and return snapshot().

    This is the SINGLE-RUN building block; iter_updates() repeats it.
    Never raises for meter or network problems (they show as ``"up": false``).
    """
    ips = abm.dedupe_ips(ip_list if ip_list is not None else abm.IP_LIST)
    started = time.monotonic()
    try:
        data = abm.poll_all_devices(ips, abm.TABLE_NAMES, retries=retries, retry_delay=retry_delay,
                                    stop_event=stop_event, timeout=timeout)
        meta = data.get("__poll_meta__", {})
        if not meta.get("cancelled"):                  # shutting down: keep the last good state
            apply_poll(data, ips)
            _STATS["polls_total"] += 1
            _STATS["last_poll_unixtime"] = time.time()
            if meta.get("all_failed", False):
                _STATS["poll_errors_total"] += 1
                log.warning("Poll: no meter answered (%s).", ", ".join(ips))
        data.clear()
    except Exception as exc:                           # a poll must never kill the caller
        _STATS["poll_errors_total"] += 1
        log.error("Poll cycle raised: %s", exc, exc_info=True)
    finally:
        _STATS["last_poll_duration"] = time.monotonic() - started
    return snapshot()


def _wait_until(deadline: float, stop_event: Optional[threading.Event]) -> bool:
    """Sleep until ``deadline`` (monotonic); True if a stop was requested.

    Checks the STOP_COLLECTION file (python ab_stop.py) every 0.5 s.
    """
    stop_file = abm.stop_signal_file()
    while True:
        if stop_event is not None and stop_event.is_set():
            return True
        now = time.monotonic()
        if now >= deadline:
            return False
        if os.path.exists(stop_file):
            log.info("STOP_COLLECTION file detected - stopping.")
            if stop_event is not None:
                stop_event.set()
            return True
        if stop_event is not None:
            stop_event.wait(min(0.5, deadline - now))
        else:
            time.sleep(min(0.5, deadline - now))


def iter_updates(ip_list: Optional[List[str]] = None, interval: float = DEFAULT_INTERVAL_SEC,
                 count: int = 0, stop_event: Optional[threading.Event] = None,
                 retries: Optional[int] = None, retry_delay: Optional[float] = None,
                 timeout: Optional[float] = None,
                 push: Optional[Dict[str, Any]] = None) -> Iterator[Dict[str, Any]]:
    """CONTINUOUS operation as a generator: yields snapshot() after every poll.

    Parameters
    ----------
    ip_list : list of str, optional
        Meters (default: the monitor's IP_LIST).
    interval : float
        Seconds between the STARTS of successive polls.
    count : int
        Stop after this many polls (0 = until ``stop_event`` / STOP_COLLECTION).
    stop_event : threading.Event, optional
        Set it to stop; a poll in progress is cancelled between pages.
    push : dict, optional
        push_metrics() keyword arguments (gateway, job, instance, ...): every
        poll is also pushed. The snapshot gets ``"pushed": True/False``.
    """
    ips = abm.dedupe_ips(ip_list if ip_list is not None else abm.IP_LIST)
    done = 0
    while stop_event is None or not stop_event.is_set():
        started = time.monotonic()
        snap = poll_once(ips, retries=retries, retry_delay=retry_delay, timeout=timeout,
                         stop_event=stop_event)
        if stop_event is not None and stop_event.is_set():
            return                                     # cancelled mid-poll: no partial update
        if push:
            snap["pushed"] = push_metrics(ip_list=ips, **push)
        done += 1
        yield snap
        if count and done >= count:
            return
        if _wait_until(started + float(interval), stop_event):
            return


def poll_loop(stop_event: threading.Event, ip_list: List[str], interval: float, retries: int,
              retry_delay: float, max_history: int = 0, timeout: Optional[float] = None) -> None:
    """0.1.x API: poll every ``interval`` seconds until ``stop_event`` is set.

    ``max_history`` is accepted for 0.0.x compatibility and ignored.
    """
    log.info("Poll loop started - %d meter(s), interval %.1f s", len(ip_list), interval)
    for _snap in iter_updates(ip_list, interval, 0, stop_event, retries, retry_delay, timeout):
        pass
    log.info("Poll loop exited.")


# %% ===========================================================================
#    CUSTOM COLLECTOR (read-only, instant)
# =============================================================================
class ABMeterCollector:
    """Builds new metric families from the latest snapshot on every scrape [E1].

    Parameters
    ----------
    ip_list : list of str
        Meters reported by ab_meter_up (also those that never answered).
    stale_after : float
        Seconds after which a table is no longer exported (0 = never).
    expose_timestamps : bool
        Attach the fetch time to every value (pull only).
    include_counters : bool
        False for pushes: process-lifetime counters mean nothing there (0.2.0).
    """

    def __init__(self, ip_list: List[str], stale_after: float, expose_timestamps: bool = False,
                 include_counters: bool = True) -> None:
        self._ip_list = list(ip_list)
        self._stale_after = float(stale_after)
        self._timestamps = bool(expose_timestamps)
        self._counters = bool(include_counters)

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

        value = GaugeMetricFamily("{0}_value".format(METRIC_PREFIX),
                                  "Latest Allen-Bradley power-meter parameter value.",
                                  labels=["ip", "table", "parameter", "unit"])
        table_up = GaugeMetricFamily("{0}_table_up".format(METRIC_PREFIX),
                                     "1 if the table was fetched on the last poll and is fresh.",
                                     labels=["ip", "table"])
        fetched_at = GaugeMetricFamily("{0}_last_fetch_timestamp_seconds".format(METRIC_PREFIX),
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

        if self._counters:
            counter = CounterMetricFamily("{0}_samples".format(METRIC_PREFIX),
                                          "Successful fetches of the table since exporter start.",
                                          labels=["ip", "table"])
            for (ip, tname), count in sorted(samples.items()):
                counter.add_metric([ip, tname], float(count))
            yield counter
            counter = CounterMetricFamily("{0}_table_fetch_errors".format(METRIC_PREFIX),
                                          "Failed fetches of the table since exporter start.",
                                          labels=["ip", "table"])
            for (ip, tname), count in sorted(errors.items()):
                counter.add_metric([ip, tname], float(count))
            yield counter

        up = GaugeMetricFamily("{0}_up".format(METRIC_PREFIX), "1 if the meter answered the last poll.",
                               labels=["ip"])
        for ip in self._ip_list:
            up.add_metric([ip], 1.0 if device_up.get(ip, False) else 0.0)
        yield up
        yield GaugeMetricFamily("{0}_poll_duration_seconds".format(METRIC_PREFIX),
                                "Duration of the last poll cycle.", value=stats["last_poll_duration"])
        yield GaugeMetricFamily("{0}_last_poll_timestamp_seconds".format(METRIC_PREFIX),
                                "Unix time of the last poll cycle.", value=stats["last_poll_unixtime"])
        if self._counters:
            yield CounterMetricFamily("{0}_polls".format(METRIC_PREFIX), "Poll cycles since exporter start.",
                                      value=stats["polls_total"])
            yield CounterMetricFamily("{0}_poll_errors".format(METRIC_PREFIX),
                                      "Poll cycles in which every meter failed, or that raised.",
                                      value=stats["poll_errors_total"])
        info = GaugeMetricFamily("{0}_build_info".format(METRIC_PREFIX), "Exporter build info (constant 1).",
                                 labels=["version", "monitor_version"])
        info.add_metric([__version__, _monitor_version()], 1.0)
        yield info


# %% ===========================================================================
#    PUBLISHING: pull (/metrics) and push (Pushgateway)
# =============================================================================
def start_server(ip_list: List[str], port: int = DEFAULT_PORT, bind: str = DEFAULT_BIND,
                 stale_after: Optional[float] = None, timestamps: bool = False,
                 interval: float = DEFAULT_INTERVAL_SEC) -> None:
    """Serve /metrics for Prometheus to PULL (once per process; later calls no-op)."""
    if _SERVER["started"]:
        return
    stale = interval * STALE_FACTOR if stale_after is None else stale_after
    REGISTRY.register(ABMeterCollector(abm.dedupe_ips(ip_list), stale, timestamps))
    start_http_server(int(port), addr=bind)
    _SERVER.update(started=True, port=int(port), bind=bind)


def push_metrics(gateway: str, ip_list: List[str], job: str = DEFAULT_PUSH_JOB,
                 instance: Optional[str] = None, timeout: float = PUSH_TIMEOUT_SEC,
                 stale_after: float = 0.0) -> bool:
    """PUSH the current values to a Pushgateway; True on success [E5].

    The group ``job=<job>, instance=<instance>`` (default: this host's name) is
    REPLACED as a whole (HTTP PUT), so values of a meter that stopped
    answering disappear and ab_meter_up shows 0. Pushed samples carry no
    timestamps (the Pushgateway rejects them) and no process counters.
    """
    registry = CollectorRegistry()
    registry.register(ABMeterCollector(abm.dedupe_ips(ip_list), stale_after,
                                       expose_timestamps=False, include_counters=False))
    try:
        push_to_gateway(gateway, job=job, registry=registry,
                        grouping_key={"instance": instance or socket.gethostname()}, timeout=timeout)
        return True
    except Exception as exc:                            # URLError, HTTPError, timeout ...
        log.warning("Push to %s failed: %s", gateway, exc)
        return False


def run(ip_list: Optional[List[str]] = None, interval: float = DEFAULT_INTERVAL_SEC,
        once: bool = False, count: int = 0, port: Optional[int] = DEFAULT_PORT,
        bind: str = DEFAULT_BIND, push_gateway: Optional[str] = None,
        push_job: str = DEFAULT_PUSH_JOB, push_instance: Optional[str] = None,
        on_update: Optional[Callable[[Dict[str, Any]], None]] = None,
        print_json: bool = False, stale_after: Optional[float] = None, timestamps: bool = False,
        retries: Optional[int] = None, retry_delay: Optional[float] = None,
        timeout: Optional[float] = None, stop_event: Optional[threading.Event] = None) -> int:
    """Run the exporter (what the command line does); returns the exit code.

    once=True: one poll, then return (0 = a meter answered, 1 = none did,
    2 = the push failed); no HTTP server. Otherwise poll every ``interval``
    seconds (``count`` polls, 0 = until stopped), serving /metrics on
    ``port`` (None = no server). ``on_update(snapshot)`` is called after every
    poll (in this thread; exceptions are logged, never raised).
    """
    ips = abm.dedupe_ips(ip_list if ip_list is not None else abm.IP_LIST)
    if not ips:
        log.error("No meter IPs configured (use --ips or set IP_LIST in the monitor).")
        return 2
    stale = interval * STALE_FACTOR if stale_after is None else float(stale_after)
    if once:
        count, port = 1, None
    push = None
    if push_gateway:
        push = {"gateway": push_gateway, "job": push_job, "instance": push_instance,
                "stale_after": stale}
        if timestamps:
            log.info("--timestamps applies to /metrics only; pushes never carry timestamps.")
    if port is not None:
        start_server(ips, port, bind, stale, timestamps, interval)
    log.info("Exporter %s %s - meters %s, %s%s%s%s", __version__,
             "single run" if once else "listening on {0}:{1}/metrics".format(bind, port)
             if port is not None else "continuous (no /metrics server)",
             ips, "" if once else "interval {0:.1f} s, ".format(interval),
             "push {0} job={1}, ".format(push_gateway, push_job) if push else "",
             "JSON to stdout, " if print_json else "",
             "stale after {0:.0f} s".format(stale))
    last: Dict[str, Any] = {}
    for snap in iter_updates(ips, interval, count, stop_event, retries, retry_delay, timeout, push):
        last = snap
        if print_json:
            sys.stdout.write(json.dumps(snap, separators=(",", ":"), allow_nan=False) + "\n")
            sys.stdout.flush()
        if on_update is not None:
            try:
                on_update(snap)
            except Exception as exc:                    # the caller's code must not stop polling
                log.error("on_update callback raised: %s", exc, exc_info=True)
    if not last:
        return 0 if not once else 1                     # stopped before the first poll ended
    if push and last.get("pushed") is False:
        return 2
    if count:                                           # --once / --count: report the last poll
        return 0 if any(m["up"] for m in last["meters"].values()) else 1
    return 0


# %% ===========================================================================
#    COMMAND LINE
# =============================================================================
def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prometheus exporter for Allen-Bradley power meters.",
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="/metrics TCP port.")
    parser.add_argument("--bind", type=str, default=DEFAULT_BIND, help="Listen address.")
    parser.add_argument("--no-http", action="store_true", dest="no_http",
                        help="Do not serve /metrics (push and/or --print-json only).")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SEC,
                        help="Seconds between poll cycles.")
    parser.add_argument("--once", action="store_true",
                        help="Single run: one poll, push/print, exit (0 ok, 1 no meter, 2 push failed).")
    parser.add_argument("--count", type=int, default=0,
                        help="Stop after this many polls (0 = run until stopped).")
    parser.add_argument("--push-gateway", type=str, default=None, dest="push_gateway",
                        help="Pushgateway URL, e.g. http://localhost:9091 - push after every poll.")
    parser.add_argument("--push-job", type=str, default=DEFAULT_PUSH_JOB, dest="push_job",
                        help="job label of the pushed group.")
    parser.add_argument("--push-instance", type=str, default=None, dest="push_instance",
                        help="instance label of the pushed group (default: host name).")
    parser.add_argument("--print-json", action="store_true", dest="print_json",
                        help="Write the dict (snapshot) to stdout as one JSON line per poll. "
                             "--once without --push-gateway implies it.")
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
                        help="Expose each value with its fetch time on /metrics (one TSDB sample per reading).")
    parser.add_argument("--url-template", type=str, default=None, dest="url_template",
                        help='Meter page URL template (default "http://{ip}/{page}").')
    parser.add_argument("--max-history", type=int, default=DEFAULT_MAX_HISTORY, dest="max_history",
                        help="Ignored since 0.1.0 (kept so existing service files still start).")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """Command-line entry point; returns (and exits with) the exit code."""
    args = _parse_args(argv)
    ip_list = abm.dedupe_ips(args.ips.split(",") if args.ips else abm.IP_LIST)
    if args.url_template:
        abm.METER_URL_TEMPLATE = args.url_template
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
    print_json = args.print_json or (args.once and not args.push_gateway)
    code = run(ip_list, interval=args.interval, once=args.once, count=args.count,
               port=None if args.no_http else args.port, bind=args.bind,
               push_gateway=args.push_gateway, push_job=args.push_job,
               push_instance=args.push_instance, print_json=print_json,
               stale_after=args.stale_after, timestamps=args.timestamps, retries=args.retries,
               retry_delay=args.retry_delay, timeout=args.timeout, stop_event=stop_event)
    try:
        if os.path.exists(abm.stop_signal_file()):
            os.remove(abm.stop_signal_file())
    except OSError:
        pass
    log.info("Exporter stopped cleanly (exit code %d).", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
