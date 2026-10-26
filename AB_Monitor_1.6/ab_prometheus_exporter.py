#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
# %% ab_prometheus_exporter.py Info
==================================
Reference Prometheus exporter for the Allen-Bradley site power meters,
built on top of ab_power_meter_monitor.py.

Design philosophy
-----------------
The monitor module is used here as a *library*, NOT as a program:

  • We call abm.poll_all_devices() + abm.update_named_dicts() directly on a
    background thread.  We deliberately DO NOT call abm.main() / abc.run(),
    because those drive the headless batch loop, redirect stdout/stderr,
    rebind the logger, and (pre-1.5.1) hijacked SIGINT/SIGTERM — all hostile
    to a long-lived server process.  Calling the poll primitives directly
    sidesteps every one of those global-state side effects.

  • A custom collector reads the shared abm.TIME_SERIES_STORE on each scrape
    and returns the LATEST value per parameter.  Scrapes therefore do NO
    network I/O and return instantly — they can never trip Prometheus's
    scrape_timeout no matter how slow or unreachable the meters are.

  • The exporter owns its own process lifecycle: it installs its own
    SIGINT/SIGTERM handlers, keeps the main thread blocked (the prometheus
    HTTP server is a daemon thread and cannot hold the process open alone),
    and also honours the ab_stop.py STOP_COLLECTION file for operational
    parity with the rest of the toolset.

Note on the monitor's ENABLE_RAM_FLUSH switch (monitor v1.5.2+)
--------------------------------------------------------------
That switch gates the monitor's AUTOMATIC RAM/size flushing, which lives in
run_headless() / the GUI poll thread.  This exporter calls neither, so the
switch has no effect here — it is intentionally not used.  Memory is bounded
instead by --max-history (default 3), which trims TIME_SERIES_STORE to the
last N samples per column after every poll (see _trim_store).

Why this is the correct pattern for a pull-model exporter
--------------------------------------------------------
Prometheus SCRAPES on its own schedule; the exporter must always have a
fresh answer ready.  We decouple acquisition (background poll loop, on a
fixed cadence) from exposition (instant read of the last known values).

Metric model
------------
All numeric parameters are exposed under ONE metric family, with identity
carried in LABELS (parameter names contain spaces and are illegal as
Prometheus metric names, but are perfectly legal as label values):

    ab_meter_value{ip="10.16.130.50",table="Real_Time_Power_Table",
                   parameter="Total Real Power",unit="W"}  123.4

Support / status metrics:

    ab_meter_up{ip="..."}                     1 = responded on last poll
    ab_meter_poll_duration_seconds            wall-time of the last poll cycle
    ab_meter_last_poll_timestamp_seconds      unix time of last successful store update
    ab_meter_samples_total{ip=...,table=...}  accumulated sample count (per table)
    ab_meter_polls_total                       counter: poll cycles attempted
    ab_meter_poll_errors_total                 counter: poll cycles that failed wholesale
    ab_meter_build_info{version=...}           1 (static build tag)

Usage
-----
    pip install prometheus_client            # plus the monitor's own deps
    python ab_prometheus_exporter.py                       # :9184, defaults
    python ab_prometheus_exporter.py --port 9200 --interval 30
    python ab_prometheus_exporter.py --ips 10.16.130.50,10.16.130.54
    python ab_prometheus_exporter.py --max-history 5        # bound RAM

Graceful stop:  Ctrl-C, SIGTERM (docker stop / systemctl stop), or
    python ab_stop.py            # writes the STOP_COLLECTION file

Prometheus scrape config (prometheus.yml)
-----------------------------------------
    scrape_configs:
      - job_name: ab_power_meter
        static_configs:
          - targets: ["exporter-host:9184"]

# %% Author Info
@Author : W. Wallace — NRAO / Green Bank Observatory
Date    : 2026-07-22
Phone   : +1 (304) 456-2216
Email   : wwallace@nrao.edu
Email2  : naval.antennas@gmail.com
Python  : 3.7.11+   (on 3.7.x use prometheus_client==0.17.1; 0.18+ needs 3.8+)
Requires: ab_power_meter_monitor.py v1.5.2+, prometheus_client
Install : pip install -r requirements.txt (Py3.8+) | requirements-py37.txt (Py3.7.x)
Version : 0.0.3          # add requirements files reference
"""

# ===========================================================================
# %% STANDARD-LIBRARY IMPORTS
# ===========================================================================
import argparse
import logging
import os
import signal
import sys
import threading
import time
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# %% Third-party imports (with actionable failure messages)
# ---------------------------------------------------------------------------
try:
    # prometheus_client provides the HTTP endpoint and the low-level metric
    # "family" objects a custom collector yields on each scrape.
    from prometheus_client import start_http_server, REGISTRY
    from prometheus_client.core import (
        GaugeMetricFamily,
        CounterMetricFamily,
    )
except ImportError as exc:  # pragma: no cover - environment guard
    sys.stderr.write(
        "FATAL: prometheus_client is not installed. "
        "Install it with:  pip install prometheus_client\n"
        f"(import error: {exc})\n"
    )
    sys.exit(1)

# The monitor module is imported as a data-access LIBRARY only.  We never call
# abm.main(); we call its poll primitives directly (see poll_loop()).
import ab_power_meter_monitor as abm  # noqa: E402  (intentional: after guard)

# ---------------------------------------------------------------------------
# %% Python version compatibility check (advisory; matches the monitor)
# ---------------------------------------------------------------------------
# 3.7 language level; 3.7.11 supported floor.  On 3.7.x pin deps to their last
# 3.7-capable releases — notably prometheus_client==0.17.1 (0.18+ needs 3.8+).
# Warns but never aborts.
MIN_PYTHON = (3, 7, 11)
if sys.version_info < MIN_PYTHON:
    sys.stderr.write(
        "WARNING: ab_prometheus_exporter requires Python >= {0}; running {1}.\n".format(
            ".".join(map(str, MIN_PYTHON)),
            ".".join(map(str, sys.version_info[:3])),
        )
    )


# ===========================================================================
# %% CONFIG DEFAULTS  (override via CLI args in _parse_args())
# ===========================================================================
DEFAULT_PORT = 9184        # Prometheus exporter listen port
DEFAULT_INTERVAL_SEC = 30.0       # seconds between background poll cycles
DEFAULT_MAX_HISTORY = 3          # keep only the last N samples per column
#                                      (0 = unbounded — grows forever; the
#                                      exporter only needs the latest value,
#                                      so a small N bounds RAM cleanly)
METRIC_PREFIX = "ab_meter"   # all metric names share this prefix
EXPORTER_VERSION = "0.0.3"

# ---------------------------------------------------------------------------
# %% Module logger — the exporter's OWN log stream (separate from ABMonitor).
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("ab_exporter")

# Quiet the monitor's own INFO chatter (it logs on every update_named_dicts()).
# Warnings/errors from the monitor (e.g. failed fetches) are still surfaced.
logging.getLogger("ABMonitor").setLevel(logging.WARNING)


# ===========================================================================
# %% SHARED RUNTIME STATE  (written by the poller, read by the collector)
# ===========================================================================
# All access to these is cheap and either atomic (int/float/bool rebind under
# CPython's GIL) or guarded — no lock needed for the scalar counters, and the
# per-IP status dict is only ever replaced key-by-key with plain bools.
_DEVICE_UP: Dict[str, bool] = {}    # ip -> responded on the most recent poll?
_STATS: Dict[str, float] = {
    "polls_total":       0,          # poll cycles attempted
    "poll_errors_total": 0,          # poll cycles that failed wholesale
    "last_poll_unixtime": 0.0,       # unix time of last successful store update
    "last_poll_duration": 0.0,       # wall-seconds of the last poll cycle
}


# ===========================================================================
# %% HELPERS
# ===========================================================================
def _monitor_version() -> str:
    """
    Best-effort extraction of the monitor module's version.

    Prefers an explicit ``abm.__version__`` if one is ever added; otherwise
    parses the ``Version: X.Y.Z`` line out of the module docstring.  Returns
    ``"unknown"`` if neither is available.  Kept dependency-free (no regex
    import needed) and never raises.
    """
    ver = getattr(abm, "__version__", None)
    if ver:
        return str(ver)
    for line in (abm.__doc__ or "").splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("version"):
            # Format in the monitor header is "Version : 1.5.1" / "Version: 1.5.1"
            _, _, tail = stripped.partition(":")
            tail = tail.strip()
            if tail:
                return tail
    return "unknown"


# ===========================================================================
# %% BACKGROUND POLLER
# ===========================================================================
def _trim_store(max_history: int) -> None:
    """
    Bound memory by keeping only the last ``max_history`` samples per column.

    The exporter reports only the latest value, so retaining full history
    would leak RAM indefinitely on an infinite run.  Because this exporter
    (unlike run_headless) owns the store's lifecycle entirely, trimming here
    is safe and has no effect on any file-output module.

    Parameters
    ----------
    max_history : int
        Number of trailing samples to retain per column.  <= 0 disables
        trimming (store grows unbounded).
    """
    if max_history <= 0:
        return
    with abm._TS_LOCK:                      # reuse the monitor's store lock
        for tables in abm.TIME_SERIES_STORE.values():
            for tdata in tables.values():
                ts = tdata["timestamps_local"]
                if len(ts) > max_history:
                    del ts[:-max_history]           # keep the tail in place
                    for col in tdata["columns"].values():
                        if len(col) > max_history:
                            del col[:-max_history]


def poll_loop(
    stop_event: threading.Event,
    ip_list: List[str],
    interval: float,
    retries: int,
    retry_delay: float,
    max_history: int,
) -> None:
    """
    Background acquisition loop — poll all devices on a fixed cadence.

    Runs on its own thread.  Each cycle calls the monitor's poll primitives
    directly (NOT abm.main()), so none of the batch-mode global side effects
    occur.  On success it updates the shared abm.TIME_SERIES_STORE that the
    collector reads; on a wholesale failure it flags every device down.

    Parameters
    ----------
    stop_event : threading.Event
        Set by the signal handler / STOP file watcher to end the loop.
    ip_list : list of str
        Device IP addresses to poll.
    interval : float
        Seconds between the START of successive poll cycles (poll duration is
        subtracted so cadence stays on-interval; never sleeps negative).
    retries, retry_delay : int, float
        Per-page fetch attempt count and inter-attempt delay.
    max_history : int
        Trailing samples to retain per column (see _trim_store).
    """
    log.info("Poll loop started — %d device(s), interval %.1fs, history=%s",
             len(ip_list), interval,
             "unbounded" if max_history <= 0 else max_history)

    while not stop_event.is_set():
        t_start = time.monotonic()
        try:
            # ── One full poll of every device (threaded inside the monitor:
            #    one worker thread per IP, pages fetched serially per device).
            data = abm.poll_all_devices(
                ip_list, abm.TABLE_NAMES,
                retries=retries, retry_delay=retry_delay,
            )
            meta = data.pop("__poll_meta__", {})   # caller MUST pop this

            if meta.get("all_failed", False):
                # Every device failed this cycle — mark all down and DO NOT
                # call update_named_dicts (mirrors run_headless: injecting an
                # all-error snapshot would corrupt the accumulator with gaps).
                for ip in ip_list:
                    _DEVICE_UP[ip] = False
                _STATS["poll_errors_total"] += 1
                log.warning("Poll cycle: ALL %d device(s) failed.", len(ip_list))
            else:
                ok = set(meta.get("ok_ips", []))
                for ip in ip_list:
                    _DEVICE_UP[ip] = ip in ok
                # Append this snapshot to TIME_SERIES_STORE (also updates the
                # monitor's ALL_DEVICE_DATA / named dicts as a side effect).
                abm.update_named_dicts(data, ip_list=ip_list)
                _STATS["last_poll_unixtime"] = time.time()
                _trim_store(max_history)

            _STATS["polls_total"] += 1

        except Exception as exc:                    # never let the loop die
            _STATS["poll_errors_total"] += 1
            log.error("Poll cycle raised: %s", exc, exc_info=True)

        finally:
            _STATS["last_poll_duration"] = time.monotonic() - t_start

        # ── Interval wait — chunked so stop requests are honoured promptly,
        #    and so the STOP_COLLECTION file is detected within ~0.5 s.
        deadline = t_start + interval
        while not stop_event.is_set():
            now = time.monotonic()
            if now >= deadline:
                break
            if os.path.exists(abm.STOP_SIGNAL_FILE):
                log.info("STOP_COLLECTION file detected — stopping.")
                stop_event.set()
                break
            time.sleep(min(0.5, max(0.0, deadline - now)))

    log.info("Poll loop exited.")


# ===========================================================================
# %% CUSTOM COLLECTOR  (read-only, instant — no network on scrape)
# ===========================================================================
class ABMeterCollector:
    """
    Prometheus collector that exposes the LATEST value of every numeric
    parameter in abm.TIME_SERIES_STORE.

    ``collect()`` is invoked by prometheus_client on every scrape.  It takes a
    consistent snapshot of the store under the monitor's lock, then yields one
    metric family per concept.  It performs NO I/O, so scrape latency is
    bounded by dict iteration alone.
    """

    def __init__(self, ip_list: List[str]) -> None:
        # Retained so ab_meter_up can report a 0 for devices that have never
        # yet returned data (absent from the store) as well as known-down ones.
        self._ip_list = list(ip_list)

    def collect(self):  # noqa: D401  (prometheus_client interface method)
        """Yield current metric families; called once per scrape."""
        # ── Snapshot latest value per (ip, table, parameter) under the lock ──
        snapshot: Dict[str, Dict[str, Dict[str, tuple]]] = {}
        with abm._TS_LOCK:
            for ip, tables in abm.TIME_SERIES_STORE.items():
                snapshot[ip] = {}
                for tname, tdata in tables.items():
                    latest: Dict[str, tuple] = {}
                    units = tdata.get("units", {})
                    n_ts = len(tdata.get("timestamps_local", []))
                    for param, values in tdata.get("columns", {}).items():
                        # Last non-None value (skip trailing gaps).
                        val = values[-1] if values else None
                        latest[param] = (val, units.get(param, ""))
                    snapshot[ip][tname] = latest
                    # Stash sample count via a sentinel key for the counter below.
                    snapshot[ip].setdefault("__counts__", {})[tname] = n_ts

        # ── ab_meter_value — the main gauge family ──────────────────────────
        gauge = GaugeMetricFamily(
            f"{METRIC_PREFIX}_value",
            "Latest Allen-Bradley power-meter parameter value.",
            labels=["ip", "table", "parameter", "unit"],
        )
        samples = CounterMetricFamily(
            f"{METRIC_PREFIX}_samples_total",
            "Accumulated sample count currently held for this table.",
            labels=["ip", "table"],
        )
        for ip, tables in snapshot.items():
            counts = tables.get("__counts__", {})
            for tname, latest in tables.items():
                if tname == "__counts__":
                    continue
                for param, (val, unit) in latest.items():
                    if val is None:
                        continue                    # never emit None as a value
                    gauge.add_metric([ip, tname, param, unit], float(val))
                samples.add_metric([ip, tname], float(counts.get(tname, 0)))
        yield gauge
        yield samples

        # ── ab_meter_up — per-device liveness from the last poll ────────────
        up = GaugeMetricFamily(
            f"{METRIC_PREFIX}_up",
            "1 if the device responded on the most recent poll, else 0.",
            labels=["ip"],
        )
        for ip in self._ip_list:
            up.add_metric([ip], 1.0 if _DEVICE_UP.get(ip, False) else 0.0)
        yield up

        # ── Poll health / timing ────────────────────────────────────────────
        yield GaugeMetricFamily(
            f"{METRIC_PREFIX}_poll_duration_seconds",
            "Wall-clock duration of the most recent poll cycle.",
            value=_STATS["last_poll_duration"],
        )
        yield GaugeMetricFamily(
            f"{METRIC_PREFIX}_last_poll_timestamp_seconds",
            "Unix time of the last successful store update.",
            value=_STATS["last_poll_unixtime"],
        )
        yield CounterMetricFamily(
            f"{METRIC_PREFIX}_polls_total",
            "Total poll cycles attempted since exporter start.",
            value=_STATS["polls_total"],
        )
        yield CounterMetricFamily(
            f"{METRIC_PREFIX}_poll_errors_total",
            "Total poll cycles that failed wholesale (all devices down / raised).",
            value=_STATS["poll_errors_total"],
        )

        # ── Static build info ───────────────────────────────────────────────
        info = GaugeMetricFamily(
            f"{METRIC_PREFIX}_build_info",
            "Exporter build info (constant 1; read the labels).",
            labels=["version", "monitor_version"],
        )
        info.add_metric([EXPORTER_VERSION, _monitor_version()], 1.0)
        yield info


# ===========================================================================
# %% ENTRY POINT
# ===========================================================================
def _parse_args() -> argparse.Namespace:
    """Parse command-line configuration."""
    p = argparse.ArgumentParser(
        description="Prometheus exporter for Allen-Bradley site power meters.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help="TCP port to serve /metrics on.")
    p.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SEC,
                   help="Seconds between background poll cycles.")
    p.add_argument("--ips", type=str, default=None,
                   help="Comma-separated device IPs (overrides the monitor's "
                        "IP_LIST). Example: 10.16.130.50,10.16.130.54")
    p.add_argument("--retries", type=int, default=abm.HTTP_RETRY_COUNT,
                   help="Per-page fetch attempts (1 = no retry).")
    p.add_argument("--retry-delay", type=float, default=abm.HTTP_RETRY_DELAY_SEC,
                   dest="retry_delay", help="Seconds between fetch retries.")
    p.add_argument("--max-history", type=int, default=DEFAULT_MAX_HISTORY,
                   dest="max_history",
                   help="Trailing samples retained per column (0 = unbounded).")
    return p.parse_args()


def main() -> None:
    """Start the HTTP server + background poller and block until stopped."""
    args = _parse_args()

    # Resolve the device list: CLI override wins, else the monitor's default.
    if args.ips:
        ip_list = [s.strip() for s in args.ips.split(",") if s.strip()]
    else:
        ip_list = [s.strip() for s in abm.IP_LIST if s.strip()]
    if not ip_list:
        log.error("No device IPs configured (use --ips or set abm.IP_LIST).")
        sys.exit(2)

    # Clear any stale stop file so we don't exit immediately on startup.
    try:
        if os.path.exists(abm.STOP_SIGNAL_FILE):
            os.remove(abm.STOP_SIGNAL_FILE)
    except OSError:
        pass

    stop_event = threading.Event()

    # The exporter OWNS its signal handling (the monitor's is disabled by
    # design here since we never call abm.main()).  A clean stop simply ends
    # the poll loop and unblocks main(), letting the process exit normally.
    def _handle_signal(signum, _frame):  # noqa: ANN001
        log.info("Signal %s received — shutting down.", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # Register the collector BEFORE starting the server so the very first
    # scrape already has the family definitions (values fill in after poll 1).
    REGISTRY.register(ABMeterCollector(ip_list))

    start_http_server(args.port)        # daemon-thread HTTP server for /metrics
    log.info("Exporter listening on :%d/metrics — devices: %s",
             args.port, ip_list)

    # Start the background acquisition thread (daemon so a hard kill can't hang;
    # normal shutdown joins it explicitly below).
    poller = threading.Thread(
        target=poll_loop,
        args=(stop_event, ip_list, args.interval,
              args.retries, args.retry_delay, args.max_history),
        name="ab_poll_loop",
        daemon=True,
    )
    poller.start()

    # ── Block the MAIN thread until stopped ─────────────────────────────────
    # This is what keeps the process (and therefore the daemon HTTP server)
    # alive.  Without a blocking main thread the interpreter would exit and
    # the exporter would die — the exact failure this design avoids.
    try:
        while not stop_event.is_set():
            stop_event.wait(timeout=1.0)
    except KeyboardInterrupt:            # belt-and-suspenders for Ctrl-C
        stop_event.set()

    log.info("Stopping — waiting for poll loop to finish current cycle…")
    poller.join(timeout=max(5.0, args.interval + 5.0))

    # Tidy up any stop file so the next start is clean.
    try:
        if os.path.exists(abm.STOP_SIGNAL_FILE):
            os.remove(abm.STOP_SIGNAL_FILE)
    except OSError:
        pass
    log.info("Exporter stopped cleanly.")


if __name__ == "__main__":
    main()
