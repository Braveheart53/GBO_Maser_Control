#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
# %% ab_power_meter_monitor.py Info
=========================
Allen-Bradley (Rockwell) Site Power Meter — Web Table Poller & Data Logger
NRAO / GBO Site Infrastructure Monitoring Tool

Polls Allen-Bradley power meters over HTTP, parses the 11 HTML data tables
(pages 0-10), stores data in named Python dicts, and writes results to any
combination of:
  • Live PyQt/PySide6 GUI with embedded matplotlib preview plots
  • NRAO-compliant FITS files
  • Per-table CSV files (columnar: timestamp=row, parameter=column)
  • Excel XLSX workbook (one sheet per table, line charts over time)
  • Appending columnar Markdown log files (.md) — GFM pipe tables, one file
    per device per table (timestamp=row, parameter=column)
  • Veusz HDF5 (.vszh5) project files — written at loop end with all samples

IP address base: 10.16.130.{last_octet}
Device range   : last octet 50 – 53  (configurable below)

# %%% AUthor Info
@Author: W. Wallace — NRAO / Green Bank Observatory
Date   : 2026-05-13
Phone  : +1 (304) 456-2216
Email  : wwallace@nrao.edu
Email2 : naval.antennas@gmail.com 
Python : 3.8+
Version: 1.4.19
Deps   : PySide6, matplotlib, requests, beautifulsoup4, lxml,
         astropy, openpyxl, veusz  (pip install each)

# %%% Usage
-----
Headless / scripted:
    python ab_power_meter_monitor.py

GUI mode (set ENABLE_GUI = 1 below or check the checkbox at launch):
    python ab_power_meter_monitor.py   # then toggle via GUI

All output-enable switches can be overridden at runtime via the GUI.

# %%% Notes on the HTML endpoint
---------------------------
Each meter exposes 11 table pages at:
    http://<IP>/<page_index>
where page_index is 0 … 10.  The <body> tag contains a single <table>
with rows: # | Parameter Name | Value.

Table map (page index → human name):
    0  Device Configuration Table
    1  Communications Configuration Table
    2  Voltage / Current Table
    3  Real Time Power Table
    4  Cumulative Power Table
    5  Demand Data Table
    6  Diagnostic Table
    7  Voltage / Current Snapshot Log Table
    8  Power Snapshot Log Table
    9  Min_Max Log Table
   10  Diagnostic Table (extended)
"""

# ===========================================================================
# %% STANDARD-LIBRARY IMPORTS
# ===========================================================================
import copy
import os
import sys
import csv
import json
import time
import logging
import datetime
import traceback
from typing import Any, Dict, List, Optional, Tuple
import requests
from bs4 import BeautifulSoup
import threading
import signal
import gc
import concurrent.futures
try:
    import psutil          # pip install psutil — for memory monitoring
except ImportError:
    # type: ignore[assignment]  graceful degradation: memory-based
    psutil = None
    # flush threshold will not trigger, sentinel 9999 MB is returned

# Set matplotlib backend at import time — before any Qt or GUI code runs.
# 'Agg' is a non-interactive raster backend used for rendering figures into
# in-memory PNG buffers that are then embedded in the Qt FigureCanvas widgets.
# This must be set ONCE here; calling matplotlib.use() inside a function
# after Qt has already initialised its own backend silently fails or raises.
try:
    import matplotlib
    matplotlib.use("Agg")
except ImportError:
    pass   # matplotlib unavailable — GUI preview skipped gracefully

# ===========================================================================
# %% Switches
#  ██████╗  ██████╗ ██╗    ██╗███████╗██████╗     ███████╗██╗    ██╗██╗████████╗ ██████╗██╗  ██╗███████╗███████╗
#  ██╔══██╗██╔═══██╗██║    ██║██╔════╝██╔══██╗    ██╔════╝██║    ██║██║╚══██╔══╝██╔════╝██║  ██║██╔════╝██╔════╝
#  ██████╔╝██║   ██║██║ █╗ ██║█████╗  ██████╔╝    ███████╗██║ █╗ ██║██║   ██║   ██║     ███████║█████╗  ███████╗
#  ██╔═══╝ ██║   ██║██║███╗██║██╔══╝  ██╔══██╗    ╚════██║██║███╗██║██║   ██║   ██║     ██╔══██║██╔══╝  ╚════██║
#  ██║     ╚██████╔╝╚███╔███╔╝███████╗██║  ██║    ███████║╚███╔███╔╝██║   ██║   ╚██████╗██║  ██║███████╗███████║
#  ╚═╝      ╚═════╝  ╚══╝╚══╝ ╚══════╝╚═╝  ╚═╝    ╚══════╝ ╚══╝╚══╝ ╚═╝   ╚═╝    ╚═════╝╚═╝  ╚═╝╚══════╝╚══════╝
#
#  ALL RUNTIME BEHAVIOUR IS CONTROLLED BY THE VARIABLES IN THIS SECTION.
#  Set 0 = False / disabled,  1 = True / enabled.
# ===========================================================================

# ---------------------------------------------------------------------------
# %% Feature / output enable switches  (0 = off, 1 = on)
# ---------------------------------------------------------------------------
ENABLE_GUI = 1   # Show PyQt/PySide6 main window
ENABLE_FITS = 1   # Write NRAO-compliant FITS files
ENABLE_CSV = 1   # Write per-table CSV files
ENABLE_XLSX = 1   # Write Excel workbook with charts
ENABLE_LOG_APPEND = 1   # Write per-device Markdown (.md) data log tables
ENABLE_LOG_FILE = 1   # Write ab_monitor.log (Python logging file handler)
# Set to 0 to keep logging console-only (no file created)
ENABLE_VEUSZ = 1   # Write Veusz HDF5 project file(s) (.vszh5)
VEUSZ_WRITE_ON_FLUSH = 0  # 1 = also save a timestamped Veusz snapshot on each RAM flush
                          # 0 = write Veusz only at loop end / Stop (default)
                          # Store is cleared BEFORE the Veusz subprocess is launched
                          # so RAM impact is minimal regardless of this setting.

# ---------------------------------------------------------------------------
# %% Headless loop control
# ---------------------------------------------------------------------------
# Number of poll cycles to run in headless mode.
# 0 = run indefinitely until stopped (Ctrl-C or stop signal file).
# N = run exactly N cycles then exit cleanly.
HEADLESS_LOOP_COUNT = 1   # 0 = infinite loop; N = run N cycles then stop

# ---------------------------------------------------------------------------
# %% Headless console / dict output switches
# ---------------------------------------------------------------------------
# These three switches control what is printed to stdout in headless mode.
# All are independent; any combination is valid.
#
# HEADLESS_CONSOLE_DICTS_ONLY
#   1 = suppress ALL other stdout text (logger, progress lines, etc.) and
#       print ONLY the named dict JSON blocks.  Other outputs (files) still
#       run normally.  0 = normal mixed output.
#
# HEADLESS_PRINT_EACH_SAMPLE
#   1 = after every poll cycle, print all 11 named dicts for the CURRENT
#       sample (latest snapshot, first device) to stdout.  0 = skip.
#
# HEADLESS_PRINT_CUMULATIVE
#   1 = print the full accumulated TIME_SERIES_STORE (all devices, all
#       samples, all tables) to stdout when the loop ends cleanly, when
#       ab_stop.py triggers a stop, or when a memory-limit flush fires.
#       0 = skip cumulative print.
# ---------------------------------------------------------------------------
HEADLESS_CONSOLE_DICTS_ONLY = 0   # 1 = dicts-only stdout; suppress all other text
HEADLESS_PRINT_EACH_SAMPLE = 0   # 1 = print 11 named dicts after every poll cycle
HEADLESS_PRINT_CUMULATIVE = 0   # 1 = print full TIME_SERIES_STORE at stop/flush
HEADLESS_SILENT = 1   # 1 = suppress ALL stdout/stderr console output;
#     file outputs (log, CSV, XLSX, FITS, Veusz)
#     are unaffected.  Overrides all other console
#     switches above when set to 1.

# ---------------------------------------------------------------------------
# %% Memory / flush thresholds (adaptive write scheduling)
# ---------------------------------------------------------------------------
# When the in-memory time-series store grows beyond MEM_FLUSH_THRESHOLD_MB
# OR free system RAM drops below MEM_FREE_MIN_MB, an intermediate flush of
# CSV / XLSX / log files is triggered mid-loop (parallel, non-blocking) so
# memory is reclaimed without dropping sample points.
MEM_FLUSH_THRESHOLD_MB = 256   # flush when store occupies more than N MB
MEM_FREE_MIN_MB = 512   # flush when system free RAM falls below N MB

# ---------------------------------------------------------------------------
# %% IP address configuration
# ---------------------------------------------------------------------------
# Explicit list of device IP addresses to poll.  Add or remove entries to
# target any combination of devices regardless of sequential ordering.
# Example: monitor only .50 and .53 → ["10.16.130.50", "10.16.130.53"]
IP_LIST: List[str] = [
    "10.16.130.50",
    "10.16.130.54"
]

# ---------------------------------------------------------------------------
# %% Polling / timing
# ---------------------------------------------------------------------------
SAMPLE_PERIOD_SEC = 30    # Seconds between successive polls of all devices
HTTP_TIMEOUT_SEC          = 5    # Per-request response timeout (sec) — NOT a port
HTTP_RETRY_COUNT          = 3    # Total fetch attempts per (ip,page); 1 = no retry
HTTP_RETRY_DELAY_SEC      = 2.0  # Seconds between retry attempts
HEADLESS_MAX_CONSEC_FAILS = 5    # Consecutive all-device failures before clean exit
                                 #   0 = never exit on failures
MEM_RAM_PCT_LIMIT         = 70   # Flush+clear store when system RAM reaches this %
APPEND_OUTPUT_FILES       = 1    # 1=append all output files, 0=overwrite each run

# ---------------------------------------------------------------------------
# %% Output paths
# ---------------------------------------------------------------------------
# PRIMARY OUTPUT ROOT — change OUTPUT_BASE_DIR to redirect ALL output
# (logs, FITS, CSV, XLSX, Veusz) to a different location without touching
# any of the sub-directory constants below.
#
# Set to an absolute path to store output anywhere on the filesystem, e.g.:
#   OUTPUT_BASE_DIR = "/mnt/data/ab_meter_output"
#   OUTPUT_BASE_DIR = r"D:\GBO\PowerMeter\output"
#
# The default resolves to a folder named "ab_meter_output" sitting next
# to this script file, which keeps everything self-contained.
# ---------------------------------------------------------------------------
OUTPUT_BASE_DIR = os.path.join(os.path.dirname(
    os.path.abspath(__file__)), "ab_meter_output")

# Sub-directories — all derived from OUTPUT_BASE_DIR so a single change
# above propagates everywhere automatically.  Override individually only
# if you need outputs split across different locations.
# kept for back-compat references
OUTPUT_DIR = OUTPUT_BASE_DIR
LOG_DIR = os.path.join(OUTPUT_BASE_DIR, "logs")   # text log files
FITS_DIR = os.path.join(OUTPUT_BASE_DIR, "fits")   # NRAO FITS files
CSV_DIR = os.path.join(OUTPUT_BASE_DIR, "csv")    # per-table CSV files
XLSX_DIR = os.path.join(OUTPUT_BASE_DIR, "xlsx")   # Excel workbooks
VEUSZ_DIR = os.path.join(OUTPUT_BASE_DIR, "veusz")  # Veusz HDF5 projects

# Path to the stop-signal file.  Touch this file (or run ab_stop.py) to
# request a clean shutdown of the headless loop.  Deleted automatically
# on startup and on clean exit.
STOP_SIGNAL_FILE = os.path.join(OUTPUT_BASE_DIR, "STOP_COLLECTION")

# ---------------------------------------------------------------------------
# %% Table page-index → canonical name mapping
# ---------------------------------------------------------------------------
TABLE_NAMES: Dict[int, str] = {
    0:  "Device_Configuration_Table",
    1:  "Communications_Configuration_Table",
    2:  "Voltage_Current_Table",
    3:  "Real_Time_Power_Table",
    4:  "Cumulative_Power_Table",
    5:  "Demand_Data_Table",
    6:  "Diagnostic_Table",
    7:  "Voltage_Current_Snapshot_Log_Table",
    8:  "Power_Snapshot_Log_Table",
    9:  "MinMax_Log_Table",
    10: "Diagnostic_Table_Extended",
}

# ---------------------------------------------------------------------------
# %% Unit inference map: substring → unit label
# Applied when building Veusz axis labels and FITS column units.
# Keys are LOWER-CASE substrings found in parameter names.
# ---------------------------------------------------------------------------
UNIT_MAP: List[Tuple[str, str]] = [
    ("current",          "A"),
    ("voltage",          "V"),
    ("frequency",        "Hz"),
    ("kw hour",          "kWh"),
    ("kvar hour",        "kVARh"),
    ("real power",       "W"),
    ("reactive power",   "VAR"),
    ("apparent power",   "VA"),
    ("true pf",          "%"),
    ("displacement pf",  "%"),
    ("distortion pf",    "%"),
    ("demand current",   "A"),
    ("demand power",     "W"),
    ("demand apparent",  "VA"),
    ("demand reactive",  "VAR"),
    ("elapsed time",     "s"),
    ("period",           "min"),
    ("interval",         "s"),
    ("pulse width",      "ms"),
]


# ===========================================================================
# %% LOGGING SETUP
# ===========================================================================
def _setup_logging(
    log_dir: str,
    append: bool = True,
    enable_log_file: bool = True,
) -> logging.Logger:
    """
    Initialise the module-level logger.

    Parameters
    ----------
    log_dir : str
        Directory where the log file will be written.  Ignored (and not
        created) when ``enable_log_file`` is False.
    append : bool
        If True, append to existing log file; otherwise overwrite.
    enable_log_file : bool
        When True (default), attach a FileHandler that writes
        ``ab_monitor.log`` to ``log_dir``.
        When False, only a console StreamHandler is attached — no directory
        is created and no file is written.

    Returns
    -------
    logging.Logger
        Configured logger instance.
    """
    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    logger = logging.getLogger("ABMonitor")
    logger.setLevel(logging.DEBUG)

    # %%% Console handler — always present
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    if not logger.handlers:
        logger.addHandler(ch)

    # %%% File handler — only when enabled
    if enable_log_file:
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "ab_monitor.log")
        file_mode = "a" if append else "w"
        fh = logging.FileHandler(log_path, mode=file_mode, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
            logger.addHandler(fh)
        logger.info("Logger initialised — output: %s", log_path)
    else:
        logger.info("Logger initialised — console only (ENABLE_LOG_FILE=0)")

    return logger


# Global logger — always initialised console-only at module/import scope.
# This avoids creating the logs/ directory on disk just because the module
# was imported.  The file handler (and the logs/ directory) are added inside
# main() after the runtime cfg is finalised and ENABLE_LOG_FILE is confirmed.
logger: logging.Logger = _setup_logging(
    LOG_DIR,
    append=bool(ENABLE_LOG_APPEND),
    enable_log_file=False,          # console-only at import time
)


# ===========================================================================
# %% FITS ASCII SANITISER
# ===========================================================================
def _fits_ascii(value: str, *, comment: bool = False) -> str:
    """
    Sanitise a string so it contains only printable 7-bit ASCII characters
    safe for use in a FITS header keyword value, COMMENT, or HISTORY field.

    FITS standard (NOST 100-2.0, sect. 4.4.2) restricts header character
    values to ASCII bytes 0x20 – 0x7E.  Any character outside that range is
    replaced with a plain hyphen '-' so the keyword is never rejected by
    astropy or CFITSIO.

    Common culprits:
      - Unicode em-dash U+2014 (—)  → ' - '
      - Unicode en-dash U+2013 (–)  → ' - '
      - Degree sign, mu, etc.        → '-'

    Parameters
    ----------
    value : str
        Raw header value string that may contain non-ASCII characters.
    comment : bool
        When True, apply the COMMENT/HISTORY card limit (72 chars, cols 9-80
        with no value indicator).  When False (default), apply the string
        value limit (68 chars — 70 value-field chars minus 2 quote chars).

    Returns
    -------
    str
        Pure printable ASCII string truncated to the appropriate FITS card
        field width.
    """
    # Replace the most common typographic substitutes first for legibility
    value = value.replace("\u2014", " - ")   # em-dash
    value = value.replace("\u2013", " - ")   # en-dash
    value = value.replace("\u00b0", "deg")   # degree sign
    value = value.replace("\u03bc", "u")     # Greek mu (micro)
    value = value.replace("\u03a9", "Ohm")   # Greek capital omega
    # Replace any remaining non-printable or non-ASCII byte with '-'
    sanitised = "".join(
        c if (0x20 <= ord(c) <= 0x7E) else "-"
        for c in value
    )
    # COMMENT/HISTORY cards: keyword (8) + space (1) = col 9; cols 9-80 → 72 chars.
    # String value cards: keyword (8) + '= ' (2) + quote (1) + value + quote (1)
    #   + optional ' / comment' → value field is 70 chars total; string content
    #   occupies at most 68 chars (two quote chars consume 2 of the 70).
    return sanitised[:72] if comment else sanitised[:68]


# ===========================================================================
# %% UNIT INFERENCE HELPER
# ===========================================================================
def infer_unit(param_name: str) -> str:
    """
    Infer a physical unit string from a parameter name using UNIT_MAP.

    Parameters
    ----------
    param_name : str
        The human-readable parameter name string from the meter HTML table.

    Returns
    -------
    str
        Unit label string, e.g. 'A', 'V', 'W', or '' if unknown.
    """
    lower = param_name.lower()
    for key, unit in UNIT_MAP:
        if key in lower:
            return unit
    return ""


# ===========================================================================
# %% HTML FETCH & PARSE
# ===========================================================================
def fetch_table_html(
    ip: str,
    page: int,
    timeout: int = HTTP_TIMEOUT_SEC,
    retries: int = HTTP_RETRY_COUNT,
    retry_delay: float = HTTP_RETRY_DELAY_SEC,
    session: Optional["requests.Session"] = None,
) -> Optional[str]:
    """Fetch raw HTML for one meter page.  URL: http://<ip>/<page> — no port.

    Parameters
    ----------
    ip : str
        Device IP address (no protocol or port).
    page : int
        Meter page index, 0-based (0 – 10, matching TABLE_NAMES keys).
    timeout : int
        Per-request response timeout in seconds.
    retries : int
        Total fetch attempts (1 = no retry).
    retry_delay : float
        Seconds between retry attempts.  Releases GIL — other threads continue.
    session : requests.Session, optional
        If provided, the caller-supplied Session is used for all attempts.
        ``poll_all_devices`` passes a dedicated per-IP Session so that the
        TCP connection established for page 0 is reused for pages 1–N via
        HTTP keep-alive, avoiding a fresh handshake on every page fetch.
        The Session is always used by exactly one thread (the per-IP poll
        thread), so there is no concurrency concern on the Session object.
        If None, a bare ``requests.get()`` call is used (no connection reuse).
    """
    url = f"http://{ip}/{page}"
    _get = session.get if session is not None else requests.get
    max_attempts = max(1, int(retries))
    for attempt in range(1, max_attempts + 1):
        try:
            resp = _get(url, timeout=timeout)
            resp.raise_for_status()
            if attempt > 1:
                logger.info("Fetched %s on attempt %d/%d.", url, attempt, max_attempts)
            else:
                logger.debug("Fetched %s — %d bytes", url, len(resp.text))
            return resp.text
        except Exception as exc:
            if attempt < max_attempts:
                logger.warning("Fetch failed %s (%d/%d): %s — retry in %.1f s...",
                               url, attempt, max_attempts, exc, retry_delay)
                time.sleep(retry_delay)
            else:
                logger.warning("Fetch failed %s — all %d attempt(s) exhausted: %s",
                               url, max_attempts, exc)
    return None

def parse_html_table(html: str, table_name: str, ip: str, page: int) -> Dict[str, Any]:
    """
    Parse an Allen-Bradley power meter HTML table body into a Python dict.

    The HTML format is:
        <tr><td>#</td><td>Parameter Name</td><td>Value</td></tr>

    Parameters
    ----------
    html : str
        Raw HTML string from the meter.
    table_name : str
        Canonical table name used as the dict key prefix.
    ip : str
        Source IP address (stored as metadata).
    page : int
        Source page index, 0-based (0 \u2013 10, matching TABLE_NAMES keys).

    Returns
    -------
    Dict[str, Any]
        Dictionary containing:
        - '_meta'  : dict  — source info, timestamp, table name
        - '#N_name': str   — parameter name (key = index string)
        - '#N_value': Any  — parsed numeric or string value
        - '#N_unit' : str  — inferred SI unit or ''
    """

    result: Dict[str, Any] = {
        "_meta": {
            "table_name":  table_name,
            "source_ip":   ip,
            "page_index":  page,
            "fetch_utc":   datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
    }

    if html is None:
        result["_meta"]["error"] = "No HTML received"
        return result

    try:
        soup = BeautifulSoup(html, "lxml")
        rows = soup.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue  # skip header row (uses <font> not <td> data)
            idx_text = cells[0].get_text(strip=True)
            param_name = cells[1].get_text(strip=True)
            raw_value = cells[2].get_text(strip=True)

            # Skip header-looking rows
            if param_name in ("Parameter Name", "#") or idx_text == "#":
                continue

            # Attempt numeric conversion
            try:
                value: Any = float(raw_value)
                if value == int(value) and "." not in raw_value:
                    value = int(value)
            except (ValueError, TypeError):
                value = raw_value  # keep as string (dates, '####', etc.)

            key = f"#{idx_text}"
            result[key] = param_name
            result[f"{key}_value"] = value
            result[f"{key}_unit"] = infer_unit(param_name)

        logger.debug("Parsed table '%s' — %d rows",
                     table_name, (len(result) - 1) // 3)

    except Exception as exc:
        logger.error("Parse error for table '%s': %s\n%s",
                     table_name, exc, traceback.format_exc())
        result["_meta"]["error"] = str(exc)

    return result


def poll_all_devices(
    ip_list: List[str],
    table_names: Dict[int, str],
    retries: int = HTTP_RETRY_COUNT,
    retry_delay: float = HTTP_RETRY_DELAY_SEC,
) -> Dict[str, Dict[str, Any]]:
    """Poll all devices in parallel, fetching each device's pages serially.

    Parallelization model
    ---------------------
    **One thread per IP address.**  Each thread fetches all of that device's
    pages sequentially (page 0, 1, 2 … in ``table_names`` order) using a
    single persistent ``requests.Session``.  Threads for different IPs run
    fully concurrently.

    Why serial pages per device
    ---------------------------
    AB power meter firmware runs a single-threaded HTTP server.  When
    multiple requests arrive simultaneously from the same client the device
    queues them internally; if the queue fills — which happens reliably when
    all 11 pages are in-flight at once — later requests are silently dropped
    or the firmware returns a TCP RST, both of which appear as timeouts to
    the caller.  Fetching pages one at a time on a single keep-alive
    connection eliminates this congestion entirely.

    Session reuse
    -------------
    One ``requests.Session`` per IP is opened before the pool starts and
    closed in a ``finally`` block.  HTTP keep-alive is negotiated
    automatically, so the TCP connection established for page 0 is reused
    for pages 1–10, giving faster per-page round-trips compared to a fresh
    handshake each time.

    Always appends ``__poll_meta__`` — caller must ``pop()`` before passing
    to ``update_named_dicts()``.

    Parameters
    ----------
    ip_list : List[str]
        IP addresses to poll.
    table_names : Dict[int, str]
        Mapping of page index → table name (e.g. ``{0: "Device_Config", …}``).
    retries : int
        Per-page fetch attempt limit.
    retry_delay : float
        Seconds between retry attempts on a single page.

    Returns
    -------
    Dict[str, Dict[str, Any]]
        Keyed by ``"<ip>_<table_name>"``.  Includes ``"__poll_meta__"`` key.
    """
    clean_ips = [ip.strip() for ip in ip_list if ip.strip()]
    all_data: Dict[str, Dict[str, Any]] = {}
    if not clean_ips:
        all_data["__poll_meta__"] = {
            "total_ips": 0, "failed_ips": [], "ok_ips": [], "all_failed": True}
        return all_data

    # Ordered page list — fetch in this order for every device.
    ordered_pages: List[Tuple[int, str]] = sorted(table_names.items())

    # Thread-safe result collector
    _lock: threading.Lock = threading.Lock()
    ip_any_success: Dict[str, bool] = {ip: False for ip in clean_ips}

    def _poll_ip(ip: str) -> None:
        """Fetch all pages for *ip* serially on a single keep-alive session.

        Results are written directly into ``all_data`` under ``_lock``.
        This function is the per-IP thread target.
        """
        session = requests.Session()
        try:
            for pidx, tname in ordered_pages:
                html: Optional[str] = None
                try:
                    html = fetch_table_html(
                        ip, pidx,
                        retries=retries,
                        retry_delay=retry_delay,
                        session=session,
                    )
                    parsed = parse_html_table(html, tname, ip, pidx)
                except Exception as exc:
                    logger.error("poll %s p%d (%s): %s", ip, pidx, tname, exc)
                    parsed = {
                        "__ip__":    ip,
                        "__table__": tname,
                        "__error__": str(exc),
                    }

                ok = (
                    html is not None
                    and parsed.get("_meta", {}).get("error") is None
                )
                key = f"{ip}_{tname}"
                with _lock:
                    all_data[key] = parsed
                    if ok:
                        ip_any_success[ip] = True

        finally:
            try:
                session.close()
            except Exception:
                pass

    # One thread per IP — pages within each IP are serial
    n_workers = len(clean_ips)
    try:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=n_workers, thread_name_prefix="ab_poll"
        ) as ex:
            futs = {ex.submit(_poll_ip, ip): ip for ip in clean_ips}
            for fut in concurrent.futures.as_completed(futs):
                ip_done = futs[fut]
                try:
                    fut.result()
                except Exception as exc:
                    logger.error("poll_ip thread %s raised: %s", ip_done, exc)
    except Exception as exc:
        logger.error("poll_all_devices executor error: %s", exc)

    failed = [ip for ip, ok in ip_any_success.items() if not ok]
    ok_ips = [ip for ip, ok in ip_any_success.items() if ok]
    all_data["__poll_meta__"] = {
        "total_ips": len(clean_ips),
        "failed_ips": failed,
        "ok_ips":     ok_ips,
        "all_failed": len(ok_ips) == 0,
    }
    if failed:
        logger.warning(
            "poll_all_devices: %d IP(s) returned no data: %s", len(failed), failed)
    if not ok_ips:
        logger.error(
            "poll_all_devices: ALL %d IP(s) failed.", len(clean_ips))
    return all_data



# ===========================================================================
# %% NAMED TABLE DICTS  (always populated; used by all output modules)
#
#  These 11 module-level dicts correspond to the 11 meter pages.
#  They are populated by update_named_dicts() after each poll.
#  Consumer code should reference these dicts directly.
# ===========================================================================

# --- Device 1 (last octet = 50, placeholder; populated at runtime) ---
Device_Configuration_Table:            Dict[str, Any] = {}
Communications_Configuration_Table:    Dict[str, Any] = {}
Voltage_Current_Table:                 Dict[str, Any] = {}
Real_Time_Power_Table:                 Dict[str, Any] = {}
Cumulative_Power_Table:                Dict[str, Any] = {}
Demand_Data_Table:                     Dict[str, Any] = {}
Diagnostic_Table:                      Dict[str, Any] = {}
Voltage_Current_Snapshot_Log_Table:    Dict[str, Any] = {}
Power_Snapshot_Log_Table:              Dict[str, Any] = {}
MinMax_Log_Table:                      Dict[str, Any] = {}
Diagnostic_Table_Extended:             Dict[str, Any] = {}

# Multi-device storage: keyed by IP then table name
ALL_DEVICE_DATA: Dict[str, Dict[str, Dict[str, Any]]] = {}

# ===========================================================================
# %% TIME-SERIES ACCUMULATOR
#  Columnar store: TIME_SERIES_STORE[ip][table_name] = {
#      "timestamps_local": [str, ...],   # local-time strings, one per poll
#      "columns":          {param: [val, ...]},  # growing list per parameter
#      "units":            {param: unit_str},    # static, set on first poll
#  }
#  This is the primary source for CSV / XLSX / log / Veusz output.
#  ALL_DEVICE_DATA is still updated each poll (latest snapshot) for FITS and
#  GUI preview.  The accumulator is the authoritative multi-sample record.
# ===========================================================================
TIME_SERIES_STORE: Dict[str, Dict[str, Dict[str, Any]]] = {}
_TS_LOCK = threading.Lock()   # protects TIME_SERIES_STORE across threads
# Counts how many RAM flushes have fired in the current run.
# Reset at the start of each new run (GUI Start / headless loop entry).
# Used by write_veusz at Stop/end to decide whether to use a timestamped
# filename (flush occurred — store is partial) or the fixed canonical name
# (no flush — store contains the entire run).
_VEUSZ_FLUSH_COUNT: int = 0


def accumulate_poll(all_device_data: Dict[str, Dict[str, Dict[str, Any]]]) -> None:
    """
    Append the current poll snapshot to the TIME_SERIES_STORE.

    Called immediately after update_named_dicts() on every poll cycle.
    Thread-safe via _TS_LOCK.

    Timestamp accuracy
    ------------------
    Each table entry uses the per-page ``_meta["fetch_utc"]`` timestamp
    recorded by :func:`parse_html_table` immediately after the HTTP response
    was received for that page.  This is more accurate than a single shared
    ``datetime.now()`` taken after all IPs and all pages have completed,
    which can lag the actual data acquisition by 10-30 s for a 2-IP /
    11-page poll under typical timeout conditions.

    ``fetch_utc`` is stored as a UTC ISO-8601 string (e.g.
    ``"2026-06-06T10:05:03Z"``); it is converted to local wall-clock time
    before storage so that all timestamps_local values remain in the same
    timezone-unaware local format used by every output file.  If the
    conversion fails for any reason the function falls back to
    ``datetime.datetime.now()`` so no sample is lost.

    Parameters
    ----------
    all_device_data : Dict
        Nested dict: {ip: {table_name: parsed_dict}} (latest snapshot).
    """
    # Fallback timestamp used when _meta fetch_utc is absent or unparseable.
    _fallback_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _local_ts_from_meta(tdict: Dict[str, Any]) -> str:
        """Convert _meta fetch_utc (UTC ISO string) to local time string.

        Falls back to _fallback_ts if the key is missing or malformed.
        """
        fetch_utc = tdict.get("_meta", {}).get("fetch_utc", "")
        if not fetch_utc:
            return _fallback_ts
        try:
            utc_dt = datetime.datetime.strptime(
                fetch_utc, "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=datetime.timezone.utc)
            return utc_dt.astimezone(tz=None).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return _fallback_ts

    with _TS_LOCK:
        for ip, tables in all_device_data.items():
            if ip not in TIME_SERIES_STORE:
                TIME_SERIES_STORE[ip] = {}

            for tname, tdict in tables.items():
                series = extract_numeric_series(tdict)
                if not series:
                    continue

                # Per-table timestamp derived from the actual HTTP fetch time.
                local_ts = _local_ts_from_meta(tdict)

                if tname not in TIME_SERIES_STORE[ip]:
                    # First poll: initialise columns and units
                    TIME_SERIES_STORE[ip][tname] = {
                        "timestamps_local": [],
                        "columns": {param: [] for param in series},
                        "units":   {param: unit for param, (_, unit) in series.items()},
                    }
                else:
                    # Subsequent polls: add any newly-appearing parameters
                    existing = TIME_SERIES_STORE[ip][tname]["columns"]
                    for param in series:
                        if param not in existing:
                            # Back-fill with None for prior rows
                            n_existing = len(
                                TIME_SERIES_STORE[ip][tname]["timestamps_local"])
                            existing[param] = [None] * n_existing
                            TIME_SERIES_STORE[ip][tname]["units"][param] = series[param][1]

                tstore = TIME_SERIES_STORE[ip][tname]
                tstore["timestamps_local"].append(local_ts)
                for param, (val, _) in series.items():
                    tstore["columns"][param].append(val)
                # Fill None for any column not present in this sample
                n_ts = len(tstore["timestamps_local"])
                for col_list in tstore["columns"].values():
                    if len(col_list) < n_ts:
                        col_list.append(None)

    logger.debug("Accumulator updated - %d devices, fallback ts: %s",
                 len(all_device_data), _fallback_ts)

# %% Memory Tracking


def ts_store_size_mb() -> float:
    """
    Estimate the current memory footprint of TIME_SERIES_STORE in megabytes.

    Uses sys.getsizeof recursively on all nested lists and dicts.  This is
    an approximation — actual RSS impact may be higher due to Python object
    overhead.

    Returns
    -------
    float
        Estimated size in megabytes.
    """
    def _size(obj: Any, seen: Optional[set] = None) -> int:
        if seen is None:
            seen = set()
        obj_id = id(obj)
        if obj_id in seen:
            return 0
        seen.add(obj_id)
        size = sys.getsizeof(obj)
        if isinstance(obj, dict):
            size += sum(_size(k, seen) + _size(v, seen)
                        for k, v in obj.items())
        elif isinstance(obj, (list, tuple)):
            size += sum(_size(i, seen) for i in obj)
        return size

    with _TS_LOCK:
        return _size(TIME_SERIES_STORE) / (1024 * 1024)


def system_free_ram_mb() -> float:
    """
    Return the amount of free system RAM in megabytes.

    Falls back to a large sentinel value (9999) if psutil is unavailable
    so that the flush threshold is never falsely triggered.

    Returns
    -------
    float
        Free RAM in megabytes, or 9999 if psutil is not installed.
    """
    try:
        import psutil as _ps
        return _ps.virtual_memory().available / (1024 * 1024)
    except ImportError:
        return 9999.0


def should_flush(cfg: Dict[str, Any]) -> bool:
    """
    Decide whether an intermediate mid-loop file flush should occur.

    Returns True when either:
      • TIME_SERIES_STORE footprint exceeds MEM_FLUSH_THRESHOLD_MB, or
      • System free RAM is below MEM_FREE_MIN_MB.

    Parameters
    ----------
    cfg : Dict
        Runtime config dict (reads flush threshold keys).

    Returns
    -------
    bool
        True when a flush is needed, False otherwise.
    """
    threshold = cfg.get("mem_flush_threshold_mb", MEM_FLUSH_THRESHOLD_MB)
    free_min = cfg.get("mem_free_min_mb",        MEM_FREE_MIN_MB)
    store_mb = ts_store_size_mb()
    free_mb = system_free_ram_mb()
    if store_mb > threshold:
        logger.info(
            "Flush triggered: store size %.1f MB > threshold %.1f MB", store_mb, threshold)
        return True
    if free_mb < free_min:
        logger.info(
            "Flush triggered: free RAM %.1f MB < minimum %.1f MB", free_mb, free_min)
        return True
    return False

# %% Dict raw data update


def update_named_dicts(
    all_data: Dict[str, Dict[str, Any]],
    ip_list: Optional[List[str]] = None,
) -> None:
    """
    Update module-level named dicts from the raw all_data poll result.

    This function also rebuilds ALL_DEVICE_DATA which groups data by IP.

    Parameters
    ----------
    all_data : Dict[str, Dict[str, Any]]
        Output of poll_all_devices() with ``__poll_meta__`` already popped.
    ip_list : list of str, optional
        The runtime IP list to use when padding missing-device entries.
        Defaults to the module-level ``IP_LIST`` when not provided.  Callers
        that obtain their IP list from a config dict or GUI spinbox should
        pass it explicitly so that stale module-level IPs are not injected
        into ALL_DEVICE_DATA during a run with a different IP set.
    """
    global Device_Configuration_Table, Communications_Configuration_Table
    global Voltage_Current_Table, Real_Time_Power_Table, Cumulative_Power_Table
    global Demand_Data_Table, Diagnostic_Table, Voltage_Current_Snapshot_Log_Table
    global Power_Snapshot_Log_Table, MinMax_Log_Table, Diagnostic_Table_Extended
    # ALL_DEVICE_DATA: no 'global' needed — dict is mutated in-place (.clear(), [ip]=…)

    ALL_DEVICE_DATA.clear()

    for dict_key, data in all_data.items():
        meta = data.get("_meta", {})
        ip = meta.get("source_ip", "unknown")
        tname = meta.get("table_name", "unknown")

        if ip not in ALL_DEVICE_DATA:
            ALL_DEVICE_DATA[ip] = {}
        ALL_DEVICE_DATA[ip][tname] = data

    # Ensure ALL expected IPs appear in ALL_DEVICE_DATA even if they returned
    # no pages this cycle (network timeout, device reboot, etc.).
    # accumulate_poll() is called below — if an IP is absent from ALL_DEVICE_DATA
    # its timestamp is not appended, creating a length mismatch between
    # timestamps_local and the column lists in TIME_SERIES_STORE.
    # Inserting an empty dict for the missing IP lets accumulate_poll() skip it
    # gracefully (no numeric series → no append) rather than causing misalignment.
    # Use the caller-supplied ip_list (runtime config) rather than the module-level
    # IP_LIST so that a GUI or headless run with a different device set does not
    # inject stale module-startup IPs into ALL_DEVICE_DATA.
    _eff_ips = ip_list if ip_list is not None else IP_LIST
    for expected_ip in _eff_ips:
        clean = expected_ip.strip()
        if clean and clean not in ALL_DEVICE_DATA:
            ALL_DEVICE_DATA[clean] = {}

    # Populate module-level dicts from the FIRST available device
    # (convenience reference; full multi-device access via ALL_DEVICE_DATA)
    first_ip = next(iter(ALL_DEVICE_DATA), None)
    if first_ip is None:
        return

    dev = ALL_DEVICE_DATA[first_ip]
    Device_Configuration_Table = dev.get(
        "Device_Configuration_Table",          {})
    Communications_Configuration_Table = dev.get(
        "Communications_Configuration_Table",  {})
    Voltage_Current_Table = dev.get("Voltage_Current_Table",               {})
    Real_Time_Power_Table = dev.get("Real_Time_Power_Table",               {})
    Cumulative_Power_Table = dev.get("Cumulative_Power_Table",              {})
    Demand_Data_Table = dev.get("Demand_Data_Table",                   {})
    Diagnostic_Table = dev.get("Diagnostic_Table",                    {})
    Voltage_Current_Snapshot_Log_Table = dev.get(
        "Voltage_Current_Snapshot_Log_Table",  {})
    Power_Snapshot_Log_Table = dev.get(
        "Power_Snapshot_Log_Table",            {})
    MinMax_Log_Table = dev.get("MinMax_Log_Table",                    {})
    Diagnostic_Table_Extended = dev.get(
        "Diagnostic_Table_Extended",           {})

    accumulate_poll(ALL_DEVICE_DATA)
    logger.info("Named dicts updated from %d device(s).", len(ALL_DEVICE_DATA))


# ===========================================================================
# %% HELPER: EXTRACT NUMERIC SERIES FROM A TABLE DICT
# ===========================================================================
def extract_numeric_series(table_dict: Dict[str, Any]) -> Dict[str, Tuple[float, str]]:
    """
    Extract all numeric parameter values from a parsed table dict.

    Parameters
    ----------
    table_dict : Dict[str, Any]
        A dict returned by parse_html_table().

    Returns
    -------
    Dict[str, Tuple[float, str]]
        {param_name: (value, unit)} for every numeric entry.
    """
    series: Dict[str, Tuple[float, str]] = {}
    for key, val in table_dict.items():
        if key.startswith("_") or key.endswith("_value") or key.endswith("_unit"):
            continue
        # key is '#N' → check for corresponding _value
        value_key = f"{key}_value"
        unit_key = f"{key}_unit"
        if value_key in table_dict:
            v = table_dict[value_key]
            u = table_dict.get(unit_key, "")
            if isinstance(v, (int, float)):
                series[val] = (float(v), u)
    return series

# %% Output Modules
# ===========================================================================
# %%% OUTPUT MODULE 1 — FITS
# ===========================================================================


def write_fits(
    all_device_data: Dict[str, Dict[str, Dict[str, Any]]],
    fits_dir: str,
    append: bool = True,
) -> None:
    """
    Write NRAO-compliant FITS files — one persistent file per device,
    overwritten on every call so it always contains the FULL accumulated
    time-series from TIME_SERIES_STORE.

    Filename is fixed per device (no timestamp in the name) so successive
    flushes in both GUI and headless modes append to the same file on disk
    rather than creating a new file per sample.

    Layout (each BinTableHDU)
    -------------------------
    Column 0 : TIMESTAMP_LOCAL  — ISO string, format 'A19' (fixed 19-char)
    Column 1+: one 64-bit float column per numeric parameter, all N rows.

    Follows FITS standard (NOST 100-2.0) and NRAO conventions:
      - Primary HDU contains global metadata in header keywords
      - Each table page becomes a FITS BinTableHDU extension
      - Column names truncated to FITS TTYPE limit (68 chars)
      - TELESCOP, INSTRUME, ORIGIN, OBSERVER keywords populated
      - DATE-OBS in ISO-8601 format (first sample timestamp)
      - DATE-END in ISO-8601 format (last sample timestamp)
      - NSAMP keyword: number of accumulated poll cycles
      - DATE-WRT: UTC timestamp of this particular write
      - BUNIT keyword on each column where units are known
      - All string header values are 7-bit ASCII (NOST 100-2.0 sect. 4.4.2)

    Data is drawn from TIME_SERIES_STORE (full accumulated history), NOT
    from the single-snapshot all_device_data dict.

    Parameters
    ----------
    all_device_data : Dict
        Nested dict: {ip: {table_name: parsed_dict}} — used only for
        iterating device IPs; data comes from TIME_SERIES_STORE.
    fits_dir : str
        Output directory path.
    """
    try:
        from astropy.io import fits as astrofits
        import numpy as np
    except ImportError as exc:
        logger.error("astropy not available — FITS output skipped: %s", exc)
        return

    os.makedirs(fits_dir, exist_ok=True)
    if not append:
        import glob as _glob
        for _f in _glob.glob(os.path.join(fits_dir, "*.fits")):
            try:
                os.remove(_f)
            except OSError:
                pass

    now_utc = datetime.datetime.utcnow()

    # Take a thread-safe snapshot of the full accumulated store.
    with _TS_LOCK:
        ts_snapshot = {
            ip: {
                tname: {
                    "timestamps_local": list(tdata["timestamps_local"]),
                    "columns": {p: list(v) for p, v in tdata["columns"].items()},
                    "units":   dict(tdata["units"]),
                }
                for tname, tdata in tables.items()
            }
            for ip, tables in TIME_SERIES_STORE.items()
        }

    for ip in all_device_data:
        safe_ip = ip.replace(".", "_")
        # Fixed filename per device — no timestamp so every write overwrites
        # the same file, keeping a single up-to-date file with all samples.
        filename = os.path.join(
            fits_dir,
            f"ABMeter_{safe_ip}.fits",
        )

        hdu_list = [astrofits.PrimaryHDU()]
        primary_hdr = hdu_list[0].header

        # --- NRAO / standard FITS primary header keywords ---
        primary_hdr["TELESCOP"] = (_fits_ascii(
            "GBT"),          _fits_ascii("Green Bank Telescope facility"))
        primary_hdr["INSTRUME"] = (_fits_ascii(
            "ABPowerMeter"), _fits_ascii("Allen-Bradley 1403 Site Power Meter"))
        primary_hdr["ORIGIN"] = (_fits_ascii(
            "NRAO-GBO"),     _fits_ascii("National Radio Astronomy Observatory"))
        primary_hdr["OBSERVER"] = (_fits_ascii("WWallace"), _fits_ascii("W. Wallace"))
        # DATE-OBS is set below from the actual first sample timestamp.
        # DATE-WRT records the UTC time of this specific write operation.
        primary_hdr["DATE-WRT"] = (
            _fits_ascii(now_utc.isoformat(timespec="seconds") + "Z"),
            _fits_ascii("UTC timestamp of this write operation"),
        )
        primary_hdr["FILENAME"] = (_fits_ascii(
            os.path.basename(filename)), _fits_ascii("FITS file name"))
        primary_hdr["DEVIP"] = (_fits_ascii(ip), _fits_ascii("Source device IP address"))
        primary_hdr["COMMENT"] = _fits_ascii(
            "Allen-Bradley power meter telemetry - NRAO GBO site infrastructure",
            comment=True,
        )
        primary_hdr["HISTORY"] = _fits_ascii(
            f"Generated by ab_power_meter_monitor.py on {now_utc.date()}",
            comment=True,
        )

        ip_tables = ts_snapshot.get(ip, {})

        # Determine the true first and last sample timestamps across all
        # tables for this device so DATE-OBS reflects the data, not the
        # write time.
        all_timestamps = [
            ts
            for tdata in ip_tables.values()
            for ts in tdata.get("timestamps_local", [])
        ]
        if all_timestamps:
            first_ts = min(all_timestamps).replace(" ", "T")
            last_ts = max(all_timestamps).replace(" ", "T")
            primary_hdr["DATE-OBS"] = (_fits_ascii(first_ts),
                                       _fits_ascii("Local time of first accumulated sample"))
            primary_hdr["DATE-END"] = (_fits_ascii(last_ts),
                                       _fits_ascii("Local time of last accumulated sample"))
            primary_hdr["NSAMP"] = (
                max(len(td.get("timestamps_local", []))
                    for td in ip_tables.values()),
                _fits_ascii("Max accumulated poll cycles across all tables"),
            )

        for tname, tdata in ip_tables.items():
            timestamps = tdata["timestamps_local"]
            columns = tdata["columns"]
            units_map = tdata["units"]
            params = list(columns.keys())
            n_samples = len(timestamps)

            if not timestamps:
                logger.debug(
                    "FITS: no accumulated samples for '%s' — skipping HDU", tname)
                continue

            # ----------------------------------------------------------------
            # Column 0: TIMESTAMP_LOCAL — 19-char ASCII strings
            # FITS format 'A19' = fixed-width 19-char ASCII
            # ----------------------------------------------------------------
            fits_cols = [
                astrofits.Column(
                    name=_fits_ascii("TIMESTAMP_LOCAL"),
                    format="A19",
                    unit=_fits_ascii("local time"),
                    array=np.array(timestamps, dtype="U19"),
                )
            ]

            # ----------------------------------------------------------------
            # Columns 1+: one 'D' (float64) column per numeric parameter.
            # None values in the accumulated list are replaced with NaN so
            # the array is densely packed and FITS-compatible.
            # ----------------------------------------------------------------
            for param in params:
                raw_vals = columns[param]
                arr = np.array(
                    [float(v) if v is not None else float("nan")
                     for v in raw_vals],
                    dtype=np.float64,
                )
                col_name = _fits_ascii(param[:68])
                unit_str = _fits_ascii(units_map.get(
                    param, "dimensionless") or "dimensionless")
                fits_cols.append(
                    astrofits.Column(
                        name=col_name,
                        format="D",
                        unit=unit_str,
                        array=arr,
                    )
                )

            hdu = astrofits.BinTableHDU.from_columns(fits_cols)
            ext_name = tname[:8]   # EXTNAME strict 8-char limit
            hdu.header["EXTNAME"] = _fits_ascii(ext_name)
            hdu.header["TBLNAME"] = _fits_ascii(tname)
            hdu.header["SRCIP"] = _fits_ascii(ip)
            hdu.header["NSAMP"] = (
                n_samples, _fits_ascii("Number of accumulated poll cycles"))
            hdu.header["DATE-OBS"] = _fits_ascii(
                timestamps[0].replace(" ", "T") if timestamps else ""
            )
            hdu.header["DATE-END"] = _fits_ascii(
                timestamps[-1].replace(" ", "T") if timestamps else ""
            )
            hdu.header["COMMENT"] = _fits_ascii(
                f"AB meter table: {tname}", comment=True)
            hdu.header["COMMENT"] = _fits_ascii(
                f"{n_samples} sample(s), columnar time-series, TIMESTAMP_LOCAL col 1",
                comment=True,
            )
            hdu_list.append(hdu)

        try:
            hdul = astrofits.HDUList(hdu_list)
            hdul.writeto(filename, overwrite=True)
            logger.info("FITS written (%d table HDUs, %s): %s",
                        len(hdu_list) - 1, ip, filename)
        except Exception as exc:
            logger.error("FITS write failed for %s: %s", ip, exc)


# ===========================================================================
# %%% OUTPUT MODULE 2 — CSV
# ===========================================================================
def write_csv(
    all_device_data: Dict[str, Dict[str, Dict[str, Any]]],
    csv_dir: str,
    append: bool = True,
) -> None:
    """
    Write one CSV file per device per table in COLUMNAR time-series format.

    Layout
    ------
    Row 1 (header) : Timestamp_Local | <param1> | <param2> | ...
                     Units row       : (units)   | <unit1>  | <unit2>  | ...
    Rows 2+        : One row per poll cycle — local timestamp + all values.

    On append (append=True), new rows are added to the existing file.
    The header and units row are written only when the file is created
    for the first time.  Column order is determined on first write and
    preserved; new parameters discovered mid-run are appended as new
    columns on the right with back-filled blanks.

    Parameters
    ----------
    all_device_data : Dict
        Nested dict: {ip: {table_name: parsed_dict}} (used only for
        directory creation; data comes from TIME_SERIES_STORE).
    csv_dir : str
        Output directory path.
    append : bool
        If False, overwrite existing files (start fresh).
    """
    os.makedirs(csv_dir, exist_ok=True)

    with _TS_LOCK:
        snapshot = {
            ip: {
                tname: {
                    "timestamps_local": list(tdata["timestamps_local"]),
                    "columns": {p: list(v) for p, v in tdata["columns"].items()},
                    "units":   dict(tdata["units"]),
                }
                for tname, tdata in tables.items()
            }
            for ip, tables in TIME_SERIES_STORE.items()
        }

    for ip, tables in snapshot.items():
        safe_ip = ip.replace(".", "_")
        for tname, tdata in tables.items():
            filename = os.path.join(csv_dir, f"ABMeter_{safe_ip}_{tname}.csv")
            is_new = not os.path.exists(filename) or not append

            timestamps = tdata["timestamps_local"]
            columns = tdata["columns"]
            units = tdata["units"]
            params = list(columns.keys())

            if not timestamps:
                continue

            try:
                if is_new:
                    # Fresh file: write header, units row, then all accumulated rows
                    with open(filename, "w", newline="", encoding="utf-8") as fh:
                        writer = csv.writer(fh)
                        writer.writerow(["Timestamp_Local"] + params)
                        writer.writerow(
                            ["(units)"] + [units.get(p, "") for p in params])
                        for i, ts in enumerate(timestamps):
                            row = [ts] + [columns[p][i] if i <
                                          len(columns[p]) else "" for p in params]
                            writer.writerow(row)
                else:
                    # Append mode: read existing header to preserve column order,
                    # detect new columns, then append only the new rows.
                    existing_params: List[str] = []
                    existing_row_count = 0
                    last_file_ts: str = ""
                    with open(filename, "r", newline="", encoding="utf-8") as fh:
                        reader = csv.reader(fh)
                        last_data_row: List[str] = []
                        for row_idx, row in enumerate(reader):
                            if row_idx == 0:
                                # skip Timestamp_Local; strip whitespace to
                                # prevent phantom duplicates when the header
                                # cell was written with trailing spaces.
                                existing_params = [c.strip() for c in row[1:]]
                            elif row_idx == 1:
                                pass  # units row
                            else:
                                existing_row_count += 1
                                last_data_row = row
                        if last_data_row:
                            last_file_ts = last_data_row[0].strip()

                    # Post-flush reset detection:
                    # After a RAM flush, TIME_SERIES_STORE is cleared and
                    # re-accumulates from index 0.  If the store has fewer rows
                    # than the CSV file, all current timestamps are newer than
                    # anything on disk and should ALL be written (start_idx=0).
                    # Guard: only apply when the first current timestamp is
                    # strictly newer than the last on-disk timestamp so we never
                    # re-append data already persisted.
                    if (
                        existing_row_count > 0
                        and len(timestamps) > 0
                        and last_file_ts
                        and timestamps[0] > last_file_ts
                    ):
                        # Store was cleared after the last write — all current
                        # timestamps are new; write from index 0.
                        start_idx = 0
                    elif existing_row_count >= len(timestamps):
                        # Nothing new to write (common after flush-before-clear).
                        start_idx = len(timestamps)
                    else:
                        start_idx = existing_row_count

                    new_params = [
                        p for p in params if p not in existing_params]
                    all_params = existing_params + new_params

                    if new_params:
                        # Rewrite file with extended header if new columns appeared
                        with open(filename, "r", newline="", encoding="utf-8") as fh:
                            old_rows = list(csv.reader(fh))
                        with open(filename, "w", newline="", encoding="utf-8") as fh:
                            writer = csv.writer(fh)
                            # Updated header
                            writer.writerow(["Timestamp_Local"] + all_params)
                            # Updated units row
                            all_units = [units.get(p, "") for p in all_params]
                            writer.writerow(["(units)"] + all_units)
                            # Re-emit data rows with blanks for new cols
                            for old_row in old_rows[2:]:
                                writer.writerow(
                                    old_row + [""] * len(new_params))

                    # Append only rows not yet written
                    rows_to_write = timestamps[start_idx:]
                    with open(filename, "a" if append else "w", newline="", encoding="utf-8") as fh:
                        writer = csv.writer(fh)
                        for i, ts in enumerate(rows_to_write):
                            abs_i = start_idx + i
                            row = [ts] + [
                                columns[p][abs_i] if (
                                    p in columns and abs_i < len(columns[p])) else ""
                                for p in all_params
                            ]
                            writer.writerow(row)

                logger.info("CSV written (columnar): %s", filename)
            except Exception as exc:
                logger.error("CSV write failed for %s / %s: %s",
                             ip, tname, exc)


# ===========================================================================
# %%% OUTPUT MODULE 3 — EXCEL (XLSX)
# ===========================================================================
def write_xlsx(
    all_device_data: Dict[str, Dict[str, Dict[str, Any]]],
    xlsx_dir: str,
    append: bool = True,
) -> None:
    """
    Write an Excel workbook per device in COLUMNAR time-series format.

    Layout (each sheet = one table)
    --------------------------------
    Row 1   : Header  — Timestamp_Local | <param1> | <param2> | ...
    Row 2   : Units   — (units)         | <unit1>  | <unit2>  | ...
    Rows 3+ : Data    — local timestamp + values (one row per poll cycle).

    An openpyxl LineChart is appended to each sheet showing all numeric
    parameters as overlaid lines vs. sample index (robust to any N samples).

    The workbook is overwritten each time (XLSX does not support true row
    append without reloading the full file anyway).  All accumulated samples
    from TIME_SERIES_STORE are written.

    Parameters
    ----------
    all_device_data : Dict
        Nested dict: {ip: {table_name: parsed_dict}} (directory only;
        data comes from TIME_SERIES_STORE).
    xlsx_dir : str
        Output directory path.
    """
    try:
        import openpyxl
        from openpyxl.chart import LineChart, Reference
        from openpyxl.chart.series import SeriesLabel
        from openpyxl.chart.data_source import StrRef
        from openpyxl.utils import get_column_letter
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError as exc:
        logger.error("openpyxl not available — XLSX output skipped: %s", exc)
        return

    os.makedirs(xlsx_dir, exist_ok=True)
    if not append:
        import glob as _glob
        for _f in _glob.glob(os.path.join(xlsx_dir, "*.xlsx")):
            try:
                os.remove(_f)
            except OSError:
                pass


    with _TS_LOCK:
        snapshot = {
            ip: {
                tname: {
                    "timestamps_local": list(tdata["timestamps_local"]),
                    "columns": {p: list(v) for p, v in tdata["columns"].items()},
                    "units":   dict(tdata["units"]),
                }
                for tname, tdata in tables.items()
            }
            for ip, tables in TIME_SERIES_STORE.items()
        }

    for ip, tables in snapshot.items():
        safe_ip = ip.replace(".", "_")
        filename = os.path.join(xlsx_dir, f"ABMeter_{safe_ip}.xlsx")

        wb = openpyxl.Workbook()
        wb.remove(wb.active)   # remove default blank sheet

        header_font = Font(name="Calibri", bold=True, color="FFFFFF")
        header_fill = PatternFill(fill_type="solid", fgColor="1F4E79")
        units_fill = PatternFill(fill_type="solid", fgColor="2E75B6")
        units_font = Font(name="Calibri", bold=False,
                          color="FFFFFF", italic=True)
        header_align = Alignment(horizontal="center")

        for tname, tdata in tables.items():
            timestamps = tdata["timestamps_local"]
            columns = tdata["columns"]
            units_map = tdata["units"]
            params = list(columns.keys())

            if not timestamps:
                continue

            safe_name = tname[:31].replace(
                "/", "_").replace("\\", "_").replace("*", "_")
            ws = wb.create_sheet(title=safe_name)

            # ── Row 1: Headers ──
            header_row = ["Timestamp_Local"] + params
            for col_idx, hdr in enumerate(header_row, start=1):
                cell = ws.cell(row=1, column=col_idx, value=hdr)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = header_align

            # ── Row 2: Units ──
            units_row = ["(units)"] + [units_map.get(p, "") for p in params]
            for col_idx, u in enumerate(units_row, start=1):
                cell = ws.cell(row=2, column=col_idx, value=u)
                cell.font = units_font
                cell.fill = units_fill
                cell.alignment = header_align

            # ── Rows 3+: Data (one row per poll cycle) ──
            for i, ts in enumerate(timestamps):
                data_row_idx = i + 3
                ws.cell(row=data_row_idx, column=1, value=ts)
                for col_idx, param in enumerate(params, start=2):
                    val = columns[param][i] if i < len(
                        columns[param]) else None
                    cell = ws.cell(row=data_row_idx, column=col_idx, value=val)
                    if isinstance(val, (int, float)):
                        cell.number_format = "0.000000"

            n_data_rows = len(timestamps)

            # ── Auto-size columns ──
            for col in ws.columns:
                max_len = 0
                col_letter = col[0].column_letter
                for cell in col:
                    try:
                        max_len = max(max_len, len(str(cell.value or "")))
                    except Exception:
                        pass
                ws.column_dimensions[col_letter].width = min(max_len + 4, 40)

            # ── Freeze header + units rows ──
            ws.freeze_panes = "A3"

            # ── Line chart — numeric params vs sample index ──
            numeric_cols = [
                col_idx
                for col_idx, p in enumerate(params, start=2)
                if any(isinstance(v, (int, float)) for v in columns[p] if v is not None)
            ]

            if n_data_rows >= 2 and numeric_cols:
                try:
                    chart = LineChart()
                    chart.title = f"{tname} — {ip}"
                    chart.style = 10
                    chart.y_axis.title = "Value"
                    chart.x_axis.title = "Sample (poll cycle)"
                    chart.width = 24
                    chart.height = 14

                    # Data rows start at row 3 (row 1 = header, row 2 = units).
                    # Reference only the data rows so neither the header text
                    # nor the "(units)" string is included in chart values.
                    # Series titles use the openpyxl SeriesLabel/StrRef API:
                    # chart.series[-1].tx = SeriesLabel(strRef=StrRef(f=addr))
                    # Assigning a bare Reference to .title raises TypeError and
                    # is silently swallowed by the except block, leaving all
                    # series untitled.  The .tx attribute is the correct target.
                    for nc in numeric_cols[:12]:
                        data_ref = Reference(
                            ws,
                            min_col=nc, max_col=nc,
                            min_row=3, max_row=n_data_rows + 2,
                        )
                        chart.add_data(data_ref, titles_from_data=False)
                        # Build a cell-reference series title pointing to the
                        # header row (row 1) so the legend shows the param name.
                        # Format: 'SheetTitle'!$COL$1 (absolute reference).
                        col_ltr = get_column_letter(nc)
                        title_addr = f"'{ws.title}'!${col_ltr}$1"
                        chart.series[-1].tx = SeriesLabel(
                            strRef=StrRef(f=title_addr)
                        )

                    # x-axis categories = Timestamp_Local column, data rows only
                    cat_ref = Reference(
                        ws, min_col=1, min_row=3, max_row=n_data_rows + 2)
                    chart.set_categories(cat_ref)
                    ws.add_chart(chart, f"A{n_data_rows + 5}")
                except Exception as exc:
                    logger.warning(
                        "Chart creation failed for sheet '%s': %s", safe_name, exc)

        try:
            wb.save(filename)
            logger.info("XLSX written (columnar): %s", filename)
        except Exception as exc:
            logger.error("XLSX save failed for %s: %s", ip, exc)


# ===========================================================================
# %%% OUTPUT MODULE 4 — TEXT LOG APPEND
# ===========================================================================
def write_log_text(
    all_device_data: Dict[str, Dict[str, Dict[str, Any]]],
    log_dir: str,
    append: bool = True,
) -> None:
    """
    Append new poll rows to per-device, per-table Markdown log files (.md).

    Each file is a valid GitHub-Flavoured Markdown document containing:

    * A level-2 heading with device IP and table name.
    * A GFM pipe table:

      | Timestamp_Local     | Param1 (unit) | Param2 (unit) | ...
      |---------------------|---------------|---------------|----
      | 2026-05-12 08:30:00 | 120.1         | 119.8         | ...
      | 2026-05-12 08:31:00 | 120.3         | 119.6         | ...

    The header and separator rows are written only once when the file is
    created.  Subsequent calls append only new data rows (rows not yet
    persisted), determined by counting existing non-empty, non-separator
    lines after the header.

    Files are named:  ABMeter_<ip>_<table_name>.md

    Parameters
    ----------
    all_device_data : Dict
        Nested dict: {ip: {table_name: parsed_dict}} (directory only;
        data is read from TIME_SERIES_STORE).
    log_dir : str
        Directory in which to create/append Markdown log files.
    """
    os.makedirs(log_dir, exist_ok=True)

    with _TS_LOCK:
        snapshot = {
            ip: {
                tname: {
                    "timestamps_local": list(tdata["timestamps_local"]),
                    "columns": {p: list(v) for p, v in tdata["columns"].items()},
                    "units":   dict(tdata["units"]),
                }
                for tname, tdata in tables.items()
            }
            for ip, tables in TIME_SERIES_STORE.items()
        }

    for ip, tables in snapshot.items():
        safe_ip = ip.replace(".", "_")
        for tname, tdata in tables.items():
            timestamps = tdata["timestamps_local"]
            columns = tdata["columns"]
            units_map = tdata["units"]
            params = list(columns.keys())

            if not timestamps:
                continue

            filename = os.path.join(log_dir, f"ABMeter_{safe_ip}_{tname}.md")

            # ----------------------------------------------------------------
            # Count data rows already persisted.
            # File structure (lines):
            #   1  ## heading
            #   2  blank
            #   3  | header row |
            #   4  | :--- separator |
            #   5+ | data rows |
            # Non-data lines to skip = 4 (heading, blank, header, separator).
            # ----------------------------------------------------------------
            # HEADER_LINES: number of non-data lines in a Markdown log file.
            # When read with 'if l.strip()' the blank separator line collapses,
            # leaving 3 non-empty non-data lines: heading, table-header, separator.
            # HEADER_LINES counts non-empty non-data lines in a Markdown log file.
            # The blank line after the ## heading is collapsed by the
            # 'if l.strip()' filter below, so non-empty lines are:
            #   1. ## heading
            #   2. | Timestamp_Local | … |  (table header row)
            #   3. |---|---|…|              (GFM separator row)
            # = 3 non-data lines, giving existing_rows = total_nonempty - 3.
            # If HEADER_LINES were wrong, appended rows would duplicate or gap.
            HEADER_LINES = 3   # heading + table header row + separator row
            existing_rows = 0
            last_md_ts: str = ""
            is_new = not os.path.exists(filename)
            if not is_new:
                try:
                    with open(filename, "r", encoding="utf-8") as fh:
                        all_lines = [l for l in fh if l.strip()]
                    existing_rows = max(0, len(all_lines) - HEADER_LINES)
                    # Read last data timestamp for post-flush reset detection.
                    # Data lines are pipe-table rows that don't contain "---".
                    for _line in reversed(all_lines):
                        stripped = _line.strip()
                        if stripped.startswith("|") and "---" not in stripped:
                            _cells = [c.strip() for c in stripped.strip("|").split("|")]
                            if _cells and _cells[0] and not _cells[0].startswith("Timestamp"):
                                last_md_ts = _cells[0]
                            break
                except Exception:
                    existing_rows = 0
                    is_new = True

            # Post-flush reset detection (mirrors write_csv logic):
            # If TIME_SERIES_STORE was cleared after the last write, the
            # current timestamps list restarts from index 0 with NEW data.
            # existing_rows may be larger than len(timestamps) in that case,
            # causing range(existing_rows, len(timestamps)) to produce no rows.
            # Detect by comparing timestamps[0] to the last line written.
            if (
                not is_new
                and existing_rows > 0
                and len(timestamps) > 0
                and last_md_ts
                and timestamps[0] > last_md_ts
            ):
                # All current timestamps are newer than the last on-disk row;
                # write all of them (post-flush re-accumulation from index 0).
                existing_rows = 0

            # Build column header labels: "Param (unit)" or just "Param"
            col_labels = ["Timestamp_Local"] + [
                f"{p} ({units_map[p]})" if units_map.get(p) else p
                for p in params
            ]

            # Column widths for GFM pipe-table alignment.
            # Minimum = header label width (or 3 for the GFM separator rule).
            # When appending to an existing file, seed widths from the
            # existing header row so appended rows never use narrower
            # padding than what is already in the file (avoids misaligned
            # columns mid-file).  Only delta rows are measured for new
            # maximums so older rows are never retroactively re-padded.
            col_widths = [max(3, len(h)) for h in col_labels]

            # Seed widths from existing file header if we are in append mode
            if not is_new and append:
                try:
                    with open(filename, "r", encoding="utf-8") as _fh:
                        for _line in _fh:
                            stripped = _line.strip()
                            if stripped.startswith("|") and "---" not in stripped:
                                # First pipe row is the header
                                _cells = [c.strip() for c in stripped.strip("|").split("|")]
                                for _ci, _cell in enumerate(_cells):
                                    if _ci < len(col_widths):
                                        col_widths[_ci] = max(col_widths[_ci], len(_cell))
                                break
                except Exception:
                    pass

            # Measure only delta rows (rows_to_write = timestamps[existing_rows:])
            # so we do not inflate widths for data already on disk.
            for i in range(existing_rows, len(timestamps)):
                ts_w = timestamps[i]
                col_widths[0] = max(col_widths[0], len(ts_w))
                for j, p in enumerate(params, start=1):
                    val = columns[p][i] if (
                        i < len(columns[p]) and columns[p][i] is not None) else ""
                    col_widths[j] = max(col_widths[j], len(str(val)))

            def _md_row(cells: List[str]) -> str:
                """Format a list of cell strings as a padded GFM table row."""
                padded = [str(c).ljust(col_widths[k])
                          for k, c in enumerate(cells)]
                return "| " + " | ".join(padded) + " |"

            def _md_sep() -> str:
                """GFM left-aligned separator row."""
                return "|" + "|".join("-" * (w + 2) for w in col_widths) + "|"

            try:
                with open(filename, "a" if append else "w", encoding="utf-8") as fh:
                    if is_new:
                        # Heading + blank line + GFM table header + separator
                        fh.write(f"## {ip} — {tname}\n\n")
                        fh.write(_md_row(col_labels) + "\n")
                        fh.write(_md_sep() + "\n")

                    # Append only rows not yet written
                    for i in range(existing_rows, len(timestamps)):
                        ts = timestamps[i]
                        row = [ts] + [
                            str(columns[p][i])
                            if (p in columns and i < len(columns[p]) and columns[p][i] is not None)
                            else ""
                            for p in params
                        ]
                        fh.write(_md_row(row) + "\n")

                logger.info("Markdown log written: %s", filename)
            except Exception as exc:
                logger.error(
                    "Markdown log write failed for %s / %s: %s", ip, tname, exc)


# ===========================================================================
# %%% OUTPUT MODULE 5 — VEUSZ  (HDF5 format, Veusz ≥ 3.6 / 4.1)
# ===========================================================================
#
#  Uses the veusz.embed.Embedded API to build the document in-process and
#  saves with mode='hdf5', producing a .vszh5 (HDF5-backed) project file.
#
#  Veusz HDF5 format stores all datasets natively in HDF5 groups, which
#  gives better performance and lossless numeric fidelity compared to the
#  legacy plain-text .vsz format.
#
#  References:
#    • Veusz 3.6 changelog — introduced stable HDF5 save API
#    • Veusz 4.1 — HDF5 is now the recommended/default format
#    • doc.Save(path, mode='hdf5')  — core API call
#    • File extension convention: .vszh5
# ===========================================================================

# Groups of parameter name substrings that share the same SI unit for overlay
# Overlay group display names use spaces (human-readable page/legend titles).
# The keys appear verbatim in Veusz page titles and key widget titles.
VEUSZ_OVERLAY_GROUPS: Dict[str, List[str]] = {
    "Current A":              ["current"],
    "Voltage L-L (V)":        ["l1-l2 voltage", "l2-l3 voltage", "l3-l1 voltage",
                               "3 phase average voltage l-l", "pos. seq. voltage",
                               "neg. seq. voltage", "aux voltage"],
    "Voltage L-N (V)":        ["l1-n voltage", "l2-n voltage", "l3-n voltage",
                               "3 phase average voltage l-n"],
    "Real Power (W)":         ["l1 real power", "l2 real power", "l3 real power",
                               "total real power"],
    "Reactive Power (VAR)":   ["l1 reactive power", "l2 reactive power",
                               "l3 reactive power", "total reactive power"],
    "Apparent Power (VA)":    ["l1 apparent power", "l2 apparent power",
                               "l3 apparent power", "total apparent power"],
    "True PF (%)": ["l1 true pf", "l2 true pf", "l3 true pf",
                    "total true pf"],
    "Displacement PF (%)": ["l1 displacement pf", "l2 displacement pf",
                            "l3 displacement pf", "total displacement pf"],
    "Distortion PF (%)": ["l1 distortion pf", "l2 distortion pf",
                          "l3 distortion pf", "total distortion pf"],
}


def _veusz_safe(name: str) -> str:
    """
    Convert a parameter name to a Veusz-safe dataset identifier.

    Veusz dataset names must not contain spaces, slashes, dots, parentheses
    or other non-word characters.  This function replaces those with
    underscores and strips anything else, returning a string no longer
    than 64 characters.

    Parameters
    ----------
    name : str
        Raw parameter name (e.g. 'L1-L2 Voltage', 'L4(Neutral) Current').

    Returns
    -------
    str
        A valid Veusz dataset name (alphanumerics + underscores only).
    """
    import re
    s = name.replace(" ", "_").replace(".", "_").replace("/", "_")
    s = re.sub(r"[^\w]", "", s)
    return s[:64]


def write_veusz(
    all_device_data: Dict[str, Dict[str, Dict[str, Any]]],
    veusz_dir: str,
    show_window: bool = False,
    append: bool = True,
    timestamp_suffix: Optional[str] = None,
    ts_snapshot: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Build and save Veusz HDF5 project files (.vszh5) — one per device.

    Uses the **new-style object API** (``embed.Root.Add(...)`` returning
    WidgetNode objects) introduced in Veusz >1.8 and documented at
    https://github.com/veusz/veusz/wiki/EmbeddingPython.  This avoids the
    path-based ``To()`` / ``Set()`` interface entirely, which is fragile and
    requires careful navigation tracking.

    Key API facts confirmed from Veusz source and official wiki:

    * ``graph`` widget has **no** ``title`` setting — use a ``label`` widget
      child to annotate graphs (or encode info in axis labels).
    * ``key`` widget (legend) has a ``title`` setting.
    * Axis settings: ``label`` (string), ``direction`` ('horizontal'/'vertical').
    * xy settings: ``xData``, ``yData``, ``marker``, ``key`` (legend text),
      ``PlotLine/width`` (via the node: ``xy.PlotLine.width.val = '1.5pt'``).
    * Settings are read/written via ``.val`` on SettingNode objects, e.g.
      ``axis.label.val = 'Voltage [V]'``.
    * ``doc.Save(path, mode='hdf5')`` — saves as HDF5 (.vszh5), Veusz >= 3.6.

    Filename strategy
    -----------------
    The Veusz embed API has no incremental-append mode — every ``doc.Save()``
    call builds and overwrites a complete document from whatever is currently
    in ``TIME_SERIES_STORE``.  For multi-day runs where the store is cleared
    after each RAM flush, each flush must produce a **separate** timestamped
    file so no data is lost.  Pass ``timestamp_suffix`` (a compact UTC string
    such as ``"20260606_103000"``) to produce::

        ABMeter_10_16_130_50_20260606_103000.vszh5

    Leave ``timestamp_suffix=None`` (default) for the final end-of-run write
    which produces the fixed canonical file::

        ABMeter_10_16_130_50.vszh5

    Parameters
    ----------
    all_device_data : Dict[str, Dict[str, Dict[str, Any]]]
        Nested dict: ``{ip: {table_name: parsed_dict}}``.
    veusz_dir : str
        Output directory path (created if absent).
    show_window : bool, optional
        If True the embedded Veusz window is shown (toolbar visible for
        interactive inspection).  Defaults to False (headless / silent).
    append : bool, optional
        When False, all existing ``.vszh5`` files in ``veusz_dir`` are deleted
        before writing.  Only honoured when ``timestamp_suffix`` is None (the
        end-of-run fixed-name write); flush writes never delete existing files.
    timestamp_suffix : str or None, optional
        If provided, appended to the filename before the extension so each
        RAM-flush write produces a distinct file.  Format: ``YYYYMMDD_HHMMSS``.
        When None the fixed canonical filename is used (end-of-run write).
    ts_snapshot : dict or None, optional
        A pre-taken deep-copy of ``TIME_SERIES_STORE`` to use as the data
        source instead of reading the live store.  Pass this when the store
        has already been cleared before calling ``write_veusz`` (e.g. after a
        RAM flush) so the subprocess is launched against already-freed memory
        rather than peak-usage memory.  When None (default), the live
        ``TIME_SERIES_STORE`` is read directly.

    Raises
    ------
    ImportError
        Logged and skipped if ``veusz`` is not installed.
        Install with: ``pip install veusz``.
    """
    try:
        import veusz.embed as vz
    except ImportError as exc:
        logger.error(
            "veusz package not available — Veusz HDF5 output skipped: %s\n"
            "Install with: pip install veusz",
            exc,
        )
        return

    os.makedirs(veusz_dir, exist_ok=True)
    # Only delete existing files for a final end-of-run (non-timestamped) write
    # when append=False.  Flush writes use unique timestamped filenames so they
    # never overwrite or delete any previous Veusz file.
    if not append and timestamp_suffix is None:
        import glob as _glob
        for _f in _glob.glob(os.path.join(veusz_dir, "*.vszh5")):
            try:
                os.remove(_f)
            except OSError:
                pass

    now_utc = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"

    # Use ts_snapshot (if supplied by caller) or live TIME_SERIES_STORE as the
    # authoritative IP + data source.  When a RAM flush has already cleared the
    # store, ts_snapshot carries the data so write_veusz still has full access
    # without needing the live store to be populated.
    _ts_src: Dict[str, Any] = ts_snapshot if ts_snapshot is not None else {}
    if ts_snapshot is None:
        with _TS_LOCK:
            _ts_src = dict(TIME_SERIES_STORE)   # shallow copy of top-level keys
    ts_ips = list(_ts_src.keys())
    if not ts_ips:
        logger.warning("write_veusz: data source is empty — nothing to write.")
        return

    for ip in ts_ips:
        # Build a minimal tables dict for this IP from the latest snapshot.
        # Only used for extract_numeric_series() on latest-value hints;
        # full time-series data always comes from _ts_src.
        tables = all_device_data.get(ip, {})
        # Fallback: derive table names from _ts_src when snapshot is empty.
        if not tables:
            store_tnames = list(_ts_src.get(ip, {}).keys())
            tables = {tn: {} for tn in store_tnames}
        safe_ip = ip.replace(".", "_")
        # Timestamped filename for mid-run flush writes (one file per flush
        # window so no data from prior flush periods is overwritten).
        # Fixed canonical filename for the final end-of-run write.
        if timestamp_suffix:
            filename = os.path.join(
                veusz_dir, f"ABMeter_{safe_ip}_{timestamp_suffix}.vszh5")
        else:
            filename = os.path.join(veusz_dir, f"ABMeter_{safe_ip}.vszh5")

        # -------------------------------------------------------------------
        # Open the embedded document window.
        # hidden=True  → no GUI window (headless/background).
        # hidden=False → opens the full Veusz application window with toolbar.
        # -------------------------------------------------------------------
        win_title = f"AB Power Meter - {ip} - {now_utc}"   # ASCII only
        try:
            doc = vz.Embedded(win_title, hidden=not show_window)
        except Exception as exc:
            logger.error("Failed to open Veusz Embedded for %s: %s", ip, exc)
            continue

        try:
            # ---------------------------------------------------------------
            # Step 1 — Collect all numeric series and load datasets.
            #
            # Each numeric parameter becomes a 1-D dataset named
            #   <table_safe>_<param_safe>
            # A companion index dataset (idx_<name>) provides the x-axis
            # so that future poll snapshots can be appended.
            # ---------------------------------------------------------------
            all_series: Dict[str, Dict[str, Tuple[float, str]]] = {}

            for tname, tdict in tables.items():
                series = extract_numeric_series(tdict)
                if series:
                    all_series[tname] = series
                    continue
                # Fallback: tdict may be an empty stub (e.g. the IP failed on
                # the last poll but has accumulated data in the data source).
                # Use _ts_src so the snapshot path works after store clear.
                tstore_fb = _ts_src.get(ip, {}).get(tname, {})
                fb_cols  = tstore_fb.get("columns", {})
                fb_units = tstore_fb.get("units",   {})
                if fb_cols:
                    # Build a minimal series dict: {param: (last_val_or_0, unit)}
                    fb_series: Dict[str, Tuple[float, str]] = {}
                    for param, col_vals in fb_cols.items():
                        last_v = next(
                            (v for v in reversed(col_vals) if v is not None),
                            0.0
                        )
                        fb_series[param] = (float(last_v), fb_units.get(param, ""))
                    if fb_series:
                        all_series[tname] = fb_series
                        logger.debug(
                            "Veusz: used TIME_SERIES_STORE fallback for '%s' "
                            "(snapshot was empty).", tname)
                        continue
                logger.debug(
                    "Veusz: no numeric data in '%s' — skipping", tname)

            if not all_series:
                logger.warning(
                    "Veusz: no numeric data for device %s — skipping", ip)
                doc.Close()
                continue

            # ---------------------------------------------------------------
            # Load ALL accumulated time-series data from _ts_src.
            # When ts_snapshot was provided (post-flush call) _ts_src is the
            # deep-copied snapshot taken before the store was cleared, so all
            # data is still available even though TIME_SERIES_STORE is empty.
            # ---------------------------------------------------------------
            ts_ip = _ts_src.get(ip, {})

            # ---------------------------------------------------------------
            # Veusz datetime epoch: days since 1900-01-01 00:00:00 (local).
            # Veusz stores datetimes internally as float days since its own
            # epoch of 1900-01-01.  Setting axis.mode.val = 'datetime' causes
            # Veusz to interpret the x-dataset as these epoch-float values,
            # enabling true datetime axis scaling, zooming, and data picking
            # with proper time-based grid lines and labels.
            # ---------------------------------------------------------------
            _VZ_EPOCH = datetime.datetime(1900, 1, 1)

            def _ts_to_veusz_epoch(ts_str: str) -> float:
                """Convert 'YYYY-MM-DD HH:MM:SS' local string to Veusz epoch days."""
                try:
                    dt = datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    return float("nan")
                delta = dt - _VZ_EPOCH
                return delta.days + delta.seconds / 86400.0

            n_datasets = 0
            # _ts_ds_map: {tname: datetime_ds_name} — built during dataset loading
            # and referenced during graph-building without mutating the series dict.
            _ts_ds_map: Dict[str, Optional[str]] = {}

            for tname, series in all_series.items():
                tstore = ts_ip.get(tname, {})
                ts_columns = tstore.get("columns", {})
                timestamps = tstore.get("timestamps_local", [])

                # ----------------------------------------------------------------
                # Datetime float dataset — one per table.
                # Named:  dt_<tname_safe>   e.g. dt_Real_Time_Power_Table
                # Contains float days-since-Veusz-epoch (1900-01-01) for each
                # sample.  This is the x-dataset for all graphs in this table's
                # page.  axis.mode = 'datetime' interprets these as real times.
                # A companion text dataset ts_<tname_safe> is also loaded so that
                # tickLabels can fall back to string labels on older Veusz builds.
                # ----------------------------------------------------------------
                dt_ds_name  = _veusz_safe(f"dt_{tname}")    # numeric datetime x-axis
                ts_ds_name  = _veusz_safe(f"ts_{tname}")    # text fallback labels

                if timestamps:
                    dt_vals = [_ts_to_veusz_epoch(t) for t in timestamps]
                    doc.SetData(dt_ds_name, dt_vals)
                    try:
                        doc.SetDataText(ts_ds_name, list(timestamps))
                    except AttributeError:
                        ts_ds_name = None   # older Veusz without SetDataText
                    _ts_ds_map[tname] = dt_ds_name   # use numeric datetime ds
                else:
                    _ts_ds_map[tname] = None

                for param in series:
                    ds_name  = _veusz_safe(f"{tname}_{param}")
                    # Keep idx dataset for backward-compat; datetime ds is primary x.
                    idx_name = _veusz_safe(f"idx_{tname}_{param}")

                    if param in ts_columns and ts_columns[param]:
                        raw  = ts_columns[param]
                        vals = [float(v) if v is not None else float("nan")
                                for v in raw]
                        idxs = [float(k) for k in range(len(vals))]
                    else:
                        vals = [float(series[param][0])]
                        idxs = [0.0]

                    doc.SetData(ds_name,  vals)
                    doc.SetData(idx_name, idxs)
                    n_datasets += 1

            logger.debug("Veusz: loaded %d datasets for %s",
                         n_datasets, ip)

            # ---------------------------------------------------------------
            # Step 2 — Per-table pages (new-style object API).
            #
            # Widget tree per page:
            #   page
            #     grid  (2-column layout)
            #       graph  (one per numeric parameter, autoadd=False)
            #         axis 'x'  (horizontal, label='Sample Index')
            #         axis 'y'  (vertical,   label='<param> [<unit>]')
            #         xy        (xData=idx_ds, yData=val_ds)
            #         label     (annotation text — graph has no title setting)
            #
            # IMPORTANT: graph widget has NO 'title' property.  The correct
            # way to annotate a graph is to add a 'label' widget child and
            # set its 'label' setting (the text string), position it at the
            # top-centre using xPos/yPos fractional coordinates.
            # ---------------------------------------------------------------
            root = doc.Root   # WidgetNode for the document root

            # ---------------------------------------------------------------
            # Colour palette — large distinguishable set for automatic cycling.
            # These are standard CSS/SVG colour names that Veusz recognises.
            # A modulo index is applied so any number of series gets a colour.
            # ---------------------------------------------------------------
            COLOUR_CYCLE = [
                "red",        "blue",       "green",      "darkorange",
                "purple",     "deeppink",   "teal",       "saddlebrown",
                "navy",       "olive",      "crimson",    "darkgreen",
                "royalblue",  "darkorchid", "darkcyan",   "chocolate",
                "darkred",    "steelblue",  "seagreen",   "goldenrod",
                "indigo",     "mediumblue", "firebrick",  "darkslategray",
            ]

            def _colour(idx: int) -> str:
                """Return a colour name from COLOUR_CYCLE by cyclic index."""
                return COLOUR_CYCLE[idx % len(COLOUR_CYCLE)]

            def _human(name: str) -> str:
                """Replace underscores with spaces for display labels."""
                return name.replace("_", " ")

            for tname, series in all_series.items():

                # Retrieve the datetime x-axis dataset name from _ts_ds_map.
                # This avoids the __ts_ds pop() bug where the first loop consumed
                # the stored name before the overlay loop could read it.
                dt_ds = _ts_ds_map.get(tname)   # numeric datetime float dataset

                # --- Page — human-readable name (spaces, no underscores) ---
                page_wname = _veusz_safe(f"{tname}_page")
                page = root.Add("page", name=page_wname, autoadd=False)

                # --- Grid (2 columns) ---
                grid = page.Add("grid", name="grid1", autoadd=False)
                grid.rows.val = max(1, (len(series) + 1) // 2)
                grid.columns.val = 2

                for p_idx, (param, (val, unit)) in enumerate(series.items()):
                    ds_name  = _veusz_safe(f"{tname}_{param}")
                    idx_name = _veusz_safe(f"idx_{tname}_{param}")
                    gname    = _veusz_safe(f"g_{param}")
                    colour   = _colour(p_idx)
                    # Y-axis label: human-readable param + unit, no underscores
                    axis_label = f"{_human(param)} [{unit}]" if unit else _human(param)

                    # --- Graph ---
                    graph = grid.Add("graph", name=gname, autoadd=False)

                    # --- x-axis: true datetime mode when dt_ds available ---
                    ax = graph.Add("axis", name="x", autoadd=False)
                    ax.label.val = "Local Time"
                    ax.direction.val = "horizontal"
                    if dt_ds:
                        # mode='datetime' tells Veusz to interpret the x-dataset
                        # as days since 1900-01-01, enabling true datetime scaling,
                        # zooming, data-point picking with time-based grid lines.
                        try:
                            ax.mode.val = "datetime"
                        except Exception:
                            pass  # graceful: very old Veusz without mode setting
                    else:
                        # Fallback: integer sample-index axis
                        ax.label.val = "Sample Index"

                    # --- y-axis (carries the parameter label) ---
                    ay = graph.Add("axis", name="y", autoadd=False)
                    ay.label.val = axis_label
                    ay.direction.val = "vertical"

                    # --- xy plotter: use datetime dataset when available ---
                    xy = graph.Add("xy", name="plot1", autoadd=False)
                    # Use datetime float dataset as x-axis; fall back to sample index.
                    xy.xData.val = dt_ds if dt_ds else idx_name
                    xy.yData.val = ds_name
                    xy.marker.val = "circle"
                    xy.PlotLine.width.val = "1.5pt"
                    try:
                        xy.PlotLine.color.val = colour
                        xy.MarkerFill.color.val = colour
                        xy.MarkerLine.color.val = colour
                    except Exception:
                        pass   # older Veusz versions may use different attr names

                    # --- label widget for graph title annotation ---
                    # graph has no 'title' property; use a label widget instead.
                    lbl = graph.Add("label", name="title lbl", autoadd=False)
                    lbl.label.val = f"{_human(param)} ({ip})"
                    lbl.xPos.val = [0.5]   # horizontally centred
                    lbl.yPos.val = [1.02]  # just above the plot area

            # ---------------------------------------------------------------
            # Step 3 — Overlay pages.
            # One page per unit group; all parameters sharing the same unit
            # are overlaid on a single graph.  Each xy series gets a unique
            # colour from COLOUR_CYCLE.  A 'key' widget provides the legend.
            # ---------------------------------------------------------------
            for group_label, substrings in VEUSZ_OVERLAY_GROUPS.items():

                # Collect (ds_name, idx_name, param, unit) tuples
                overlay: List[Tuple[str, str, str, str]] = []
                for tname, series in all_series.items():
                    for param, (val, unit) in series.items():
                        if any(sub in param.lower() for sub in substrings):
                            ds_name = _veusz_safe(f"{tname}_{param}")
                            idx_name = _veusz_safe(f"idx_{tname}_{param}")
                            overlay.append((ds_name, idx_name, param, unit))

                if not overlay:
                    continue

                first_unit = overlay[0][3] if overlay else ""
                y_label = group_label   # already human-readable (spaces)
                if first_unit:
                    y_label = f"{group_label} [{first_unit}]"

                # --- Page — human-readable, no underscores ---
                ov_page_name = _veusz_safe(f"overlay_{group_label}")
                ov_page = root.Add("page", name=ov_page_name, autoadd=False)

                # --- Single graph ---
                ov_graph = ov_page.Add(
                    "graph", name="overlay graph", autoadd=False)

                # --- Axes ---
                # Use the datetime float dataset from the first contributing
                # table (all tables share the same poll cadence / timestamps).
                # _ts_ds_map is populated during Step 1 dataset loading and is
                # always current — no pop() risk here.
                ov_dt_ds: Optional[str] = None
                first_tn = next(iter(all_series), None)
                if first_tn:
                    ov_dt_ds = _ts_ds_map.get(first_tn)

                ox = ov_graph.Add("axis", name="x", autoadd=False)
                ox.label.val = "Local Time"
                ox.direction.val = "horizontal"
                if ov_dt_ds:
                    try:
                        ox.mode.val = "datetime"
                    except Exception:
                        pass   # older Veusz without datetime mode
                else:
                    ox.label.val = "Sample Index"

                oy = ov_graph.Add("axis", name="y", autoadd=False)
                oy.label.val = y_label
                oy.direction.val = "vertical"

                # --- Key / legend ---
                key_wgt = ov_graph.Add("key", name="key1", autoadd=False)
                key_wgt.title.val = f"Overlay: {group_label}"

                # --- One xy per overlaid parameter, each with a unique colour ---
                for ov_idx, (ds_name, idx_name, param, unit) in enumerate(overlay):
                    xy_wname = _veusz_safe(f"xy_{ds_name}")
                    colour = _colour(ov_idx)
                    xy = ov_graph.Add("xy", name=xy_wname, autoadd=False)
                    # Use the datetime float dataset as x-axis so all overlay
                    # series are time-aligned with true datetime scaling.
                    xy.xData.val = ov_dt_ds if ov_dt_ds else idx_name
                    xy.yData.val = ds_name
                    # legend entry (no underscores)
                    xy.key.val = _human(param)
                    xy.marker.val = "circle"
                    xy.PlotLine.width.val = "1.5pt"
                    try:
                        xy.PlotLine.color.val = colour
                        xy.MarkerFill.color.val = colour
                        xy.MarkerLine.color.val = colour
                    except Exception:
                        pass

                # --- label widget for overlay page title ---
                ov_lbl = ov_graph.Add("label", name="title lbl", autoadd=False)
                ov_lbl.label.val = f"Overlay: {group_label} - {ip}"
                ov_lbl.xPos.val = [0.5]
                ov_lbl.yPos.val = [1.02]

            # ---------------------------------------------------------------
            # Step 4 — Save as HDF5 (.vszh5).
            # mode='hdf5' is supported from Veusz 3.6 and is the recommended
            # default in Veusz 4.1.  All datasets are stored in native HDF5
            # groups for compact, lossless, random-access storage.
            # ---------------------------------------------------------------
            doc.Save(filename, mode="hdf5")
            logger.info("Veusz HDF5 project saved: %s", filename)

        except Exception as exc:
            logger.error(
                "Veusz build/save failed for device %s: %s\n%s",
                ip, exc, traceback.format_exc(),
            )
        finally:
            # Always close to release the Veusz process resources, even on
            # error.  Harmless if the doc was already closed.
            try:
                doc.Close()
            except Exception:
                pass


def open_veusz_preview(
    all_device_data: Dict[str, Dict[str, Dict[str, Any]]],
    veusz_dir: str,
) -> None:
    """
    Convenience wrapper: call write_veusz with show_window=True so the
    Veusz application window opens with the toolbar visible for interactive
    inspection, then save to .vszh5 on close.

    This is the function wired to the "Open in Veusz" button in the GUI.

    Parameters
    ----------
    all_device_data : Dict
        Nested dict: ``{ip: {table_name: parsed_dict}}``.
    veusz_dir : str
        Output directory path.
    """
    write_veusz(all_device_data, veusz_dir, show_window=True)


def _any_output_enabled(cfg: Dict[str, Any]) -> bool:
    """
    Return True if at least one file output is enabled in *cfg*.

    Used to gate output directory creation — if every output switch is off
    no directories are created on disk.

    Covers: FITS, CSV, XLSX, Markdown log tables, log file, Veusz.
    """
    return any([
        cfg.get("enable_fits"),
        cfg.get("enable_csv"),
        cfg.get("enable_xlsx"),
        cfg.get("enable_log_append"),
        cfg.get("enable_log_file"),
        cfg.get("enable_veusz"),
    ])


def flush_outputs_parallel(cfg: Dict[str, Any]) -> "concurrent.futures.Future":
    """
    Dispatch non-Veusz output writers to a thread-pool executor so that
    CSV / XLSX / log writes happen concurrently with ongoing polling.

    Veusz is deliberately excluded — it is always built once at loop end
    from the complete accumulated dataset.

    The caller receives a Future object; the flush completes asynchronously.
    The caller should NOT await it synchronously in the poll thread — just
    fire and forget.  If the previous flush is still running when a new one
    is triggered, the new one is skipped to prevent file corruption.

    Parameters
    ----------
    cfg : Dict[str, Any]
        Runtime configuration dict.

    Returns
    -------
    concurrent.futures.Future
        Future representing the background flush task.
    """
    def _do_flush() -> None:
        # Skip entirely if no file output is enabled — avoids creating
        # any output directories on disk.
        if not _any_output_enabled(cfg):
            logger.debug("All file outputs disabled — flush skipped.")
            return

        fits_dir = cfg.get("fits_dir",  FITS_DIR)
        _append  = bool(cfg.get("append_files", APPEND_OUTPUT_FILES))
        csv_dir = cfg.get("csv_dir",   CSV_DIR)
        xlsx_dir = cfg.get("xlsx_dir",  XLSX_DIR)
        log_dir = cfg.get("log_dir",   LOG_DIR)

        # Snapshot the IP set at dispatch time so that lambdas below are
        # not affected by ALL_DEVICE_DATA being cleared/rebuilt by a
        # concurrent poll cycle while the flush thread is still running.
        # All writer functions (write_fits, write_csv, …) iterate IPs
        # from TIME_SERIES_STORE internally; the dict passed here is only
        # used as an IP hint.  Using a frozen snapshot guarantees the
        # correct set of IPs is visible to the writers regardless of
        # whether ALL_DEVICE_DATA is mutated between dispatch and execution.
        with _TS_LOCK:
            _ts_snap: Dict[str, Any] = {
                ip: {} for ip in TIME_SERIES_STORE
            }

        tasks = []
        if cfg.get("enable_fits"):
            tasks.append(
                ("FITS", lambda _a=_append, _d=_ts_snap: write_fits(_d, fits_dir, append=_a)))
        if cfg.get("enable_csv"):
            tasks.append(("CSV", lambda _a=_append, _d=_ts_snap: write_csv(
                _d, csv_dir, append=_a)))
        if cfg.get("enable_xlsx"):
            tasks.append(
                ("XLSX", lambda _a=_append, _d=_ts_snap: write_xlsx(_d, xlsx_dir, append=_a)))
        if cfg.get("enable_log_append"):
            tasks.append(
                ("LOG",  lambda _a=_append, _d=_ts_snap: write_log_text(_d, log_dir, append=_a)))

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(4, len(tasks)) if tasks else 1,
            thread_name_prefix="ab_flush",
        ) as ex:
            futs = {ex.submit(fn): name for name, fn in tasks}
            for fut in concurrent.futures.as_completed(futs):
                name = futs[fut]
                try:
                    fut.result()
                    logger.debug("Parallel flush OK: %s", name)
                except Exception as exc:
                    logger.error("Parallel flush ERROR (%s): %s", name, exc)

    # Use a module-level executor so we can check if one is already running
    ex = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="ab_flush_mgr")
    future = ex.submit(_do_flush)
    ex.shutdown(wait=False)
    return future


# ===========================================================================
# %% MATPLOTLIB PREVIEW HELPER (used by GUI)
# ===========================================================================

# ===========================================================================
# %% Preview PNG renderer — memory-safe multi-day pipeline
# ===========================================================================

def build_preview_pngs(
    _unused: Any = None,
) -> List[Dict[str, Any]]:
    """Render all preview plots directly to PNG byte-buffers.

    Design goals
    ------------
    * **Zero matplotlib Figure objects persist after this call.**  Every
      ``matplotlib.figure.Figure`` is created with ``FigureCanvasAgg``
      directly (never registered with ``pyplot``), rendered, then
      ``fig.clf()`` + ``canvas.flush_events()`` + explicit ``del`` is
      called before the next figure begins.  This prevents the well-known
      matplotlib memory leak where closed figures still hold references
      inside ``_pylab_helpers.Gcf`` or the pyplot figure manager.
    * **No ``import matplotlib.pyplot``** is used.  All rendering goes
      through ``matplotlib.figure.Figure`` + ``matplotlib.backends
      .backend_agg.FigureCanvasAgg`` — the Agg backend is purely
      in-process and has no display dependency, making it identical on
      Windows 11 and RHEL 8 headless.
    * The result is a plain ``List[dict]`` — no Figure objects cross the
      function boundary.

    Each returned dict has the keys::

        {
            "png_bytes": bytes,        # compressed PNG image data
            "title":     str,          # human-readable tab title
            "ip":        str | None,
            "tname":     str | None,   # None for overlay plots
            "group":     str | None,   # None for per-table plots
        }

    Parameters
    ----------
    _unused : Any
        Ignored.  Accepted for call-site compatibility with the old
        ``build_preview_figures(ALL_DEVICE_DATA)`` signature.

    Returns
    -------
    List[Dict[str, Any]]
        One dict per rendered plot, in the same order as
        ``build_preview_figures``.
    """
    import io

    # Only import the non-pyplot parts of matplotlib so no global figure
    # registry is ever touched.
    try:
        from matplotlib.figure import Figure as _MplFigure
        from matplotlib.backends.backend_agg import FigureCanvasAgg as _AggCanvas
        import matplotlib as _mpl
    except ImportError as exc:
        logger.error("matplotlib not available — preview skipped: %s", exc)
        return []

    # Colour cycle from rcParams (safe without pyplot)
    try:
        _colors = _mpl.rcParams["axes.prop_cycle"].by_key()["color"]
    except Exception:
        _colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
                   "#9467bd", "#8c564b", "#e377c2", "#7f7f7f"]

    results: List[Dict[str, Any]] = []

    # Snapshot the store under lock for a consistent read
    with _TS_LOCK:
        store_snap = {
            ip: {
                tn: {
                    "timestamps_local": list(td.get("timestamps_local", [])),
                    "columns": {c: list(v)
                                for c, v in td.get("columns", {}).items()},
                    "units":   dict(td.get("units", {})),
                }
                for tn, td in tables.items()
            }
            for ip, tables in TIME_SERIES_STORE.items()
        }

    def _render_to_png(fig: "_MplFigure") -> bytes:
        """Render *fig* to PNG bytes via Agg and release all renderer state."""
        canvas = _AggCanvas(fig)
        buf = io.BytesIO()
        canvas.draw()
        canvas.print_png(buf)
        buf.seek(0)
        png = buf.read()
        buf.close()
        # Free canvas renderer buffer (holds a copy of the rasterised image)
        del canvas
        return png

    def _new_fig() -> "_MplFigure":
        """Create a Figure with Agg canvas that is NOT registered with pyplot."""
        fig = _MplFigure(figsize=(10, 4))
        _AggCanvas(fig)   # attach Agg renderer — not tracked by pyplot
        return fig

    def _apply_time_ticks(ax: Any, x: list, timestamps: list,
                          n_samples: int) -> None:
        """Apply evenly-spaced timestamp tick labels to *ax*."""
        if timestamps and n_samples > 0:
            step = max(1, n_samples // 8)
            ax.set_xticks(x[::step])
            ax.set_xticklabels(timestamps[::step],
                               rotation=35, ha="right", fontsize=6)

    for ip, tables in store_snap.items():

        # ── Per-table plots ───────────────────────────────────────────────
        for tname, tdata in tables.items():
            columns    = tdata["columns"]
            timestamps = tdata["timestamps_local"]
            n_samples  = len(timestamps)

            if not columns or n_samples == 0:
                continue

            x = list(range(n_samples))

            fig = _new_fig()
            ax  = fig.add_subplot(111)

            for c_idx, (param, values) in enumerate(columns.items()):
                if len(values) != n_samples:
                    continue
                unit  = tdata["units"].get(param, "")
                label = f"{param} ({unit})" if unit else param
                ax.plot(x, values,
                        label=label,
                        color=_colors[c_idx % len(_colors)],
                        linewidth=1.4, marker="o", markersize=3)

            _apply_time_ticks(ax, x, timestamps, n_samples)
            ax.set_xlabel("Sample index")
            ax.set_ylabel("Value")
            ax.set_title(f"{tname}\n{ip}", fontsize=9)
            ax.grid(alpha=0.3)
            ax.legend(fontsize=6, loc="upper left",
                      bbox_to_anchor=(1.01, 1), borderaxespad=0)
            fig.tight_layout(rect=(0, 0, 0.82, 1))

            png = _render_to_png(fig)
            fig.clf()   # release all axes / artist objects
            del fig

            results.append({
                "png_bytes": png,
                "title":     f"{ip} \u2014 {tname}",
                "ip":        ip,
                "tname":     tname,
                "group":     None,
            })

        # ── Overlay plots by unit group ───────────────────────────────────
        for group_label, substrings in VEUSZ_OVERLAY_GROUPS.items():
            group_series: List[tuple] = []

            for tname_ov, tdata_ov in tables.items():
                cols_ov    = tdata_ov["columns"]
                ts_ov      = tdata_ov["timestamps_local"]
                n_ov       = len(ts_ov)

                for param, values in cols_ov.items():
                    if not any(s in param.lower() for s in substrings):
                        continue
                    if len(values) != n_ov or n_ov == 0:
                        continue
                    unit  = tdata_ov["units"].get(param, "")
                    lbl   = f"{tname_ov[:10]}/{param}"
                    if unit:
                        lbl += f" ({unit})"
                    group_series.append((lbl, list(range(n_ov)), values))

            if not group_series:   # skip only when truly empty
                continue

            fig = _new_fig()
            ax  = fig.add_subplot(111)

            for c_idx, (lbl, x, values) in enumerate(group_series):
                ax.plot(x, values,
                        label=lbl,
                        color=_colors[c_idx % len(_colors)],
                        linewidth=1.4, marker="o", markersize=3)

            ax.set_xlabel("Sample index")
            ax.set_ylabel(group_label)
            ax.set_title(f"Overlay: {group_label}\n{ip}", fontsize=9)
            ax.grid(alpha=0.3)
            ax.legend(fontsize=6, loc="upper left",
                      bbox_to_anchor=(1.01, 1), borderaxespad=0)
            fig.tight_layout(rect=(0, 0, 0.82, 1))

            png = _render_to_png(fig)
            fig.clf()
            del fig

            results.append({
                "png_bytes": png,
                "title":     f"{ip} \u2014 Overlay: {group_label}",
                "ip":        ip,
                "tname":     None,
                "group":     group_label,
            })

    return results



def build_preview_figures(
    all_device_data: Dict[str, Dict[str, Dict[str, Any]]],
) -> List[Any]:
    """
    Deprecated compatibility shim — delegates to build_preview_pngs().

    The GUI pipeline uses the memory-safe Agg-only renderer
    (``build_preview_pngs``) which never imports ``matplotlib.pyplot``
    and returns ``List[Dict]`` instead of ``List[Figure]``.  This wrapper
    is retained only for external callers that invoke this function by
    name; it returns the same ``List[Dict]`` format as the PNG renderer.

    Parameters
    ----------
    all_device_data : Dict
        Accepted for API compatibility; ignored internally.
        TIME_SERIES_STORE is read directly by build_preview_pngs().

    Returns
    -------
    List[Dict[str, Any]]
        Same as ``build_preview_pngs()`` — one dict per rendered plot.
    """
    return build_preview_pngs()


class _QTextEditHandler(logging.Handler):
    """
    A ``logging.Handler`` that appends formatted log records to a
    ``QTextEdit`` widget in real time.

    This is the bridge that makes every ``logger.*()`` call — including
    connection warnings from ``fetch_table_html()``, parse errors,
    FITS/CSV/XLSX failures, and Veusz messages — appear live in the
    GUI status console without any extra ``_append_log()`` calls scattered
    through the code.

    Usage
    -----
    Instantiate once, pass the target QTextEdit, then add to the module
    logger::

        handler = _QTextEditHandler(self._log_console)
        logging.getLogger("ABMonitor").addHandler(handler)

    Remove on window close to avoid writing to a destroyed widget::

        logging.getLogger("ABMonitor").removeHandler(handler)

    Thread safety
    -------------
    ``emit()`` uses ``QMetaObject.invokeMethod`` with
    ``Qt.ConnectionType.QueuedConnection`` so records originating on the
    background ``PollThread`` are safely marshalled to the GUI thread
    before touching the widget.
    """

    # Colour map: log level -> HTML colour for the console text
    _LEVEL_COLOUR: Dict[int, str] = {
        logging.DEBUG:    "#6c7086",   # muted grey
        logging.INFO:     "#cdd6f4",   # default text
        logging.WARNING:  "#f9e2af",   # yellow
        logging.ERROR:    "#f38ba8",   # red
        logging.CRITICAL: "#ff5555",   # bright red
    }

    def __init__(self, widget: Any, level: int = logging.DEBUG) -> None:
        """
        Parameters
        ----------
        widget : QTextEdit
            The console widget to append records to.
        level : int
            Minimum logging level to display (default DEBUG — show all).
        """
        super().__init__(level)
        self._widget = widget
        self.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s  %(levelname)-8s  %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    def emit(self, record: logging.LogRecord) -> None:
        """
        Append a formatted, coloured log line to the QTextEdit.

        Called by the logging framework on every matching record.
        Uses a queued cross-thread invoke so it is safe from any thread.

        Parameters
        ----------
        record : logging.LogRecord
            The log record to display.
        """
        try:
            msg = self.format(record)
            colour = self._LEVEL_COLOUR.get(record.levelno, "#cdd6f4")
            # Escape HTML special chars so angle brackets in messages render
            # correctly rather than being interpreted as HTML tags.
            escaped = (
                msg.replace("&", "&amp;")
                   .replace("<", "&lt;")
                   .replace(">", "&gt;")
            )
            html = f'<span style="color:{colour};">{escaped}</span>'

            # Marshal to the GUI thread via a queued invoke.
            # This is safe whether emit() is called from the main thread
            # or from PollThread.
            try:
                from qtpy.QtCore import QMetaObject, Qt
                from qtpy.QtCore import Q_ARG
                QMetaObject.invokeMethod(
                    self._widget,
                    "append",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, html),
                )
            except Exception:
                # Fallback: direct call (only safe on GUI thread)
                self._widget.append(html)

            # Auto-scroll to bottom
            try:
                from qtpy.QtCore import QMetaObject, Qt
                QMetaObject.invokeMethod(
                    self._widget.verticalScrollBar(),
                    "setValue",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(int, self._widget.verticalScrollBar().maximum()),
                )
            except Exception:
                pass

        except Exception:
            # Never let a logging handler crash the application
            self.handleError(record)


def launch_gui(
    initial_switches: Dict[str, Any],
    initial_figures:  List[Any],
) -> None:
    """
    Launch the main PyQt/PySide6 GUI window.

    The GUI provides:
      • Light / dark theme toggle (menu)
      • Check-boxes for each output switch
      • IP range last-octet spin-boxes
      • Sample period spin-box
      • Log directory file chooser
      • Live plot preview (matplotlib FigureCanvas)
      • Poll Now / Start / Stop buttons

    Parameters
    ----------
    initial_switches : Dict[str, Any]
        Dict of switch states loaded from module-level config variables.
    initial_figures : List
        Pre-computed matplotlib Figure objects for initial display.
    """
    import os
    os.environ.setdefault("QT_API", "pyside6")

    try:
        from qtpy import QtWidgets, QtGui
        from qtpy.QtWidgets import (
            QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
            QGridLayout, QGroupBox, QCheckBox, QSpinBox, QDoubleSpinBox,
            QLabel, QPushButton, QFileDialog, QLineEdit, QTabWidget,
            QScrollArea, QAction, QStatusBar,
            QTextEdit, QSplitter, QDialog,
        )
        from qtpy.QtCore import Qt, Signal, QThread
    except ImportError as exc:
        logger.critical(
            "GUI dependencies missing: %s\nInstall: pip install qtpy pyside6 matplotlib", exc)
        return

    # -----------------------------------------------------------------------
    # %%% Background polling thread
    # -----------------------------------------------------------------------
    class PollThread(QThread):
        """Worker thread that polls devices on a configurable interval."""

        data_ready    = Signal(dict)   # emits all_device_data dict each cycle
        error_occur   = Signal(str)    # emits error description string
        log_message   = Signal(str)    # emits log text for status console
        consec_limit  = Signal(int)    # emits fail count when limit reached → auto-stop

        def __init__(self, config: Dict[str, Any], parent=None):
            super().__init__(parent)
            self.config = config
            self._running = False
            # Event is set by stop() so the inter-poll sleep wakes immediately
            # even if poll_all_devices() is currently blocked on an HTTP request.
            self._stop_event = threading.Event()

        def run(self) -> None:
            self._running = True
            self._stop_event.clear()
            _consec_fails = 0
            _max_fails = int(self.config.get(
                "headless_max_consec_fails", HEADLESS_MAX_CONSEC_FAILS))
            while self._running:
                try:
                    self.log_message.emit(
                        f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Polling …"
                    )
                    data = poll_all_devices(
                        ip_list=self.config["ip_list"],
                        table_names=TABLE_NAMES,
                        retries=int(self.config.get("http_retry_count", HTTP_RETRY_COUNT)),
                        retry_delay=float(self.config.get("http_retry_delay_sec", HTTP_RETRY_DELAY_SEC)),
                    )
                    _meta = data.pop("__poll_meta__", {})
                    if _meta.get("all_failed", False):
                        _consec_fails += 1
                        self.error_occur.emit(
                            f"All IPs failed (consecutive: {_consec_fails}"
                            + (f"/{_max_fails}" if _max_fails > 0 else "") + ") — check network/device.")
                        # Auto-stop when consecutive failure limit is reached (0 = never stop)
                        if _max_fails > 0 and _consec_fails >= _max_fails:
                            self.consec_limit.emit(_consec_fails)
                            self._running = False
                            break
                    else:
                        if _consec_fails > 0:
                            self.log_message.emit(
                                f"Connectivity restored after {_consec_fails} failure(s).")
                        _consec_fails = 0
                        update_named_dicts(data, ip_list=self.config.get("ip_list", IP_LIST))
                        self.data_ready.emit(data)
                        self.log_message.emit(
                            f"[{datetime.datetime.now().strftime('%H:%M:%S')}] "
                            f"Poll complete — {len(data)} table dicts."
                        )
                except Exception as exc:
                    self.error_occur.emit(f"Poll error: {exc}")

                # Sleep in 0.5 s chunks so stop() is responsive.
                # _stop_event.wait() wakes immediately when stop() is called,
                # even if the current poll blocked for the full HTTP timeout.
                remaining = self.config.get("sample_period", SAMPLE_PERIOD_SEC)
                while remaining > 0 and self._running:
                    if self._stop_event.wait(timeout=min(0.5, remaining)):
                        break
                    remaining -= 0.5

        def stop(self) -> None:
            """Signal the run loop to exit and wake the inter-poll sleep."""
            self._running = False
            self._stop_event.set()

    # -----------------------------------------------------------------------
    # %%% Main Window
    # -----------------------------------------------------------------------
    class MainWindow(QMainWindow):
        """
        Primary application window for the AB Power Meter Monitor.
        """

        # Emitted to open an interactive plot dialog on the main thread.
        # Payload: dict with keys title, ip, tname, group, frozen_store, plot_key.
        _sig_open_iplot = Signal(object)

        LIGHT_STYLE = ""   # use Qt default

        DARK_STYLE = """
            QMainWindow, QWidget, QDialog {
                background-color: #1e1e2e;
                color: #cdd6f4;
            }
            QGroupBox {
                background-color: #181825;
                border: 1px solid #45475a;
                border-radius: 6px;
                margin-top: 10px;
                padding: 8px;
                color: #89b4fa;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
            }
            QCheckBox, QLabel, QSpinBox, QDoubleSpinBox, QLineEdit {
                color: #cdd6f4;
                background-color: transparent;
            }
            QPushButton {
                background-color: #313244;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 4px;
                padding: 4px 12px;
            }
            QPushButton:hover  { background-color: #45475a; }
            QPushButton:pressed{ background-color: #585b70; }
            QSpinBox, QDoubleSpinBox, QLineEdit {
                background-color: #313244;
                border: 1px solid #45475a;
                border-radius: 3px;
                padding: 2px;
            }
            QTabWidget::pane { border: 1px solid #45475a; }
            QTabBar::tab {
                background: #313244;
                color: #cdd6f4;
                padding: 6px 16px;
            }
            QTabBar::tab:selected { background: #45475a; color: #89b4fa; }
            QTextEdit {
                background-color: #181825;
                color: #a6e3a1;
                border: 1px solid #45475a;
                font-family: "Courier New", monospace;
                font-size: 10pt;
            }
            QScrollBar:vertical {
                background: #1e1e2e;
                width: 10px;
            }
            QScrollBar::handle:vertical {
                background: #45475a;
                border-radius: 5px;
            }
            QStatusBar { background: #181825; color: #a6adc8; }
            QMenuBar { background: #181825; color: #cdd6f4; }
            QMenuBar::item:selected { background: #313244; }
            QMenu { background: #1e1e2e; color: #cdd6f4; border: 1px solid #45475a; }
            QMenu::item:selected { background: #313244; }
        """

        def __init__(self, switches: Dict[str, Any], figures: List[Any]) -> None:
            super().__init__()
            self.setWindowTitle("AB Power Meter Monitor — NRAO / GBO")
            self.resize(1280, 820)

            self._switches = dict(switches)
            self._figures = list(figures)
            self._thread: Optional[PollThread] = None
            self._dark_mode = False
            # attached after widget is built
            self._log_handler: Optional[logging.Handler] = None
            # Tracks in-flight background file flush so we never start two at once
            self._flush_future: Optional["concurrent.futures.Future"] = None
            # Set of plot keys (ip, tname) for currently-open interactive windows.
            # Used to: (a) prevent duplicate windows, (b) skip per-tab preview
            # refresh for that plot while the user is viewing it interactively.
            # The preview for ALL other tabs continues to update normally.
            self._iplot_open_set: set = set()
            # Guard: True after _do_stop has written the final Veusz file so that
            # closeEvent (which also calls _do_stop) does not write it a second time.
            self._veusz_final_written: bool = False

            self._build_menu()
            self._build_central()      # builds self._log_console
            self._build_status_bar()

            # Wire the interactive-plot signal so _show_iplot_dialog always
            # runs on the main thread (Qt requirement for all widget creation).
            self._sig_open_iplot.connect(self._show_iplot_dialog)

            # --- Attach the QTextEditHandler to the module logger so that
            # every logger.info/warning/error call in the entire codebase
            # (poll errors, FITS issues, CSV writes, Veusz, etc.) appears
            # live in the status console with colour coding by severity.
            self._log_handler = _QTextEditHandler(
                self._log_console, level=logging.DEBUG)
            logging.getLogger("ABMonitor").addHandler(self._log_handler)

            # Apply initial dark mode if OS prefers it
            if QtWidgets.QApplication.instance().palette().window().color().lightness() < 128:
                self._apply_dark()

        # ----------------------------------------------------------------
        # %%% Menu bar
        # ----------------------------------------------------------------
        def _build_menu(self) -> None:
            menubar = self.menuBar()

            # View menu — theme toggle
            view_menu = menubar.addMenu("&View")
            self._act_toggle_theme = QAction("Switch to &Dark Theme", self)
            self._act_toggle_theme.triggered.connect(self._toggle_theme)
            view_menu.addAction(self._act_toggle_theme)

            # File menu
            file_menu = menubar.addMenu("&File")
            act_quit = QAction("&Quit", self)
            act_quit.triggered.connect(self.close)
            file_menu.addAction(act_quit)

            # Help menu
            help_menu = menubar.addMenu("&Help")
            act_about = QAction("&About", self)
            act_about.triggered.connect(self._show_about)
            help_menu.addAction(act_about)

        # ----------------------------------------------------------------
        # Central widget
        # ----------------------------------------------------------------
        def _build_central(self) -> None:
            central = QWidget()
            main_layout = QVBoxLayout(central)
            main_layout.setSpacing(6)

            splitter = QSplitter(Qt.Orientation.Horizontal)

            # ---- Left panel: controls ----
            ctrl_widget = QWidget()
            ctrl_layout = QVBoxLayout(ctrl_widget)
            ctrl_layout.setSpacing(6)

            ctrl_layout.addWidget(self._build_ip_group())
            ctrl_layout.addWidget(self._build_timing_group())
            ctrl_layout.addWidget(self._build_output_group())
            ctrl_layout.addWidget(self._build_action_buttons())
            ctrl_layout.addStretch()

            self._log_console = QTextEdit()
            self._log_console.setReadOnly(True)
            self._log_console.setMaximumHeight(180)
            self._log_console.setPlaceholderText("Status / log output …")
            ctrl_layout.addWidget(QLabel("Status Console"))
            ctrl_layout.addWidget(self._log_console)

            ctrl_widget.setMaximumWidth(340)
            splitter.addWidget(ctrl_widget)

            # ---- Right panel: plot tabs ----
            self._tab_widget = QTabWidget()
            self._populate_plot_tabs(self._figures)
            splitter.addWidget(self._tab_widget)
            splitter.setStretchFactor(1, 1)

            main_layout.addWidget(splitter)
            self.setCentralWidget(central)

        def _build_ip_group(self) -> QGroupBox:
            """Build the IP address list control group."""
            grp = QGroupBox("Device IP Addresses")
            layout = QGridLayout()

            layout.addWidget(QLabel("IP List (comma-separated):"), 0, 0)
            # Pre-populate from the header switch list or the switches dict.
            default_ips = ", ".join(
                self._switches.get("ip_list", IP_LIST)
            )
            self._le_ip_list = QLineEdit(default_ips)
            self._le_ip_list.setToolTip(
                "Comma-separated full IP addresses to poll.\n"
                "Example: 10.16.130.50, 10.16.130.53"
            )
            self._le_ip_list.setMinimumWidth(260)
            layout.addWidget(self._le_ip_list, 0, 1)

            grp.setLayout(layout)
            return grp

        def _build_timing_group(self) -> QGroupBox:
            """Build the sample period control group."""
            grp = QGroupBox("Timing")
            layout = QGridLayout()

            layout.addWidget(QLabel("Sample Period (s):"), 0, 0)
            self._spin_period = QDoubleSpinBox()
            self._spin_period.setRange(5.0, 3600.0)
            self._spin_period.setSingleStep(5.0)
            self._spin_period.setDecimals(1)
            self._spin_period.setValue(self._switches.get(
                "sample_period", SAMPLE_PERIOD_SEC))
            layout.addWidget(self._spin_period, 0, 1)

            grp.setLayout(layout)
            return grp

        def _build_output_group(self) -> QGroupBox:
            """Build the output enable check-boxes and log-dir chooser."""
            grp = QGroupBox("Output Options")
            layout = QVBoxLayout()

            # Check-boxes for the 6 file-output modes.
            # NOTE: "Enable GUI" is intentionally omitted here — the GUI is
            # already running, so that switch is only meaningful in the
            # ENABLE_GUI header variable and has no in-app toggle.
            self._cb_fits = QCheckBox("Enable FITS output")
            self._cb_csv = QCheckBox("Enable CSV output")
            self._cb_xlsx = QCheckBox("Enable Excel (XLSX) output")
            self._cb_log = QCheckBox("Append to Markdown log tables (.md)")
            self._cb_log_file = QCheckBox("Write ab_monitor.log file")
            self._cb_veusz = QCheckBox("Enable Veusz HDF5 output (.vszh5)")
            self._cb_veusz_on_flush = QCheckBox(
                "  └ Save timestamped Veusz snapshot on each RAM flush (requires Enable Veusz)")

            self._cb_fits.setChecked(
                bool(self._switches.get("enable_fits",       ENABLE_FITS)))
            self._cb_csv.setChecked(
                bool(self._switches.get("enable_csv",        ENABLE_CSV)))
            self._cb_xlsx.setChecked(
                bool(self._switches.get("enable_xlsx",       ENABLE_XLSX)))
            self._cb_log.setChecked(
                bool(self._switches.get("enable_log_append", ENABLE_LOG_APPEND)))
            self._cb_log_file.setChecked(
                bool(self._switches.get("enable_log_file",   ENABLE_LOG_FILE)))
            self._cb_veusz.setChecked(
                bool(self._switches.get("enable_veusz",      ENABLE_VEUSZ)))
            self._cb_veusz_on_flush.setChecked(
                bool(self._switches.get("veusz_write_on_flush", VEUSZ_WRITE_ON_FLUSH)))
            # Sub-option depends on parent: disable when Enable Veusz is unchecked.
            self._cb_veusz_on_flush.setEnabled(self._cb_veusz.isChecked())
            self._cb_veusz.stateChanged.connect(
                lambda state: self._cb_veusz_on_flush.setEnabled(
                    bool(state)))
            self._cb_veusz_on_flush.setToolTip(
                "Controls whether mid-run RAM flush events also produce Veusz files.\n"
                "\n"
                "Unchecked (default):\n"
                "  Veusz is written only at Stop / loop end.\n"
                "  If a RAM flush fired during the run, the final file is\n"
                "  automatically timestamped so it covers only its flush\n"
                "  window — no data is silently lost or overwritten.\n"
                "  Short runs (no flush) produce the fixed canonical file\n"
                "  ABMeter_<ip>.vszh5 containing the full run.\n"
                "\n"
                "Checked:\n"
                "  Each RAM flush ALSO saves a timestamped snapshot\n"
                "  ABMeter_<ip>_YYYYMMDD_HHMMSS.vszh5 for that window,\n"
                "  in addition to the final Stop write.\n"
                "  Use for multi-day runs where you want a Veusz file\n"
                "  available without waiting until Stop.\n"
                "\n"
                "The store is always cleared before the subprocess\n"
                "launches so RAM impact is low in either case.")

            for cb in [self._cb_fits, self._cb_csv, self._cb_xlsx,
                       self._cb_log, self._cb_log_file, self._cb_veusz,
                       self._cb_veusz_on_flush]:
                layout.addWidget(cb)

            # --- Output root directory (drives FITS, CSV, XLSX, Veusz sub-dirs) ---
            layout.addWidget(QLabel("Output Root Directory:"))
            out_dir_layout = QHBoxLayout()
            self._le_out_dir = QLineEdit(self._switches.get(
                "output_base_dir", OUTPUT_BASE_DIR))
            self._le_out_dir.setPlaceholderText(
                "Base directory for all output files …")
            self._le_out_dir.setToolTip(
                "All sub-directories (fits/, csv/, xlsx/, veusz/, logs/) are "
                "created inside this folder.  Mirrors the OUTPUT_BASE_DIR "
                "header variable."
            )
            btn_browse_out = QPushButton("Browse …")
            btn_browse_out.clicked.connect(self._choose_out_dir)
            out_dir_layout.addWidget(self._le_out_dir)
            out_dir_layout.addWidget(btn_browse_out)
            layout.addLayout(out_dir_layout)

            # --- Log directory override (defaults to <output_root>/logs) ---
            layout.addWidget(QLabel("Log File Directory (override):"))
            log_dir_layout = QHBoxLayout()
            self._le_log_dir = QLineEdit(
                self._switches.get("log_dir", LOG_DIR))
            self._le_log_dir.setPlaceholderText(
                "Log file directory (leave blank to use output root) …")
            self._le_log_dir.setToolTip(
                "Override only the log directory.  Leave blank to use "
                "<Output Root>/logs/ automatically."
            )
            btn_browse_log = QPushButton("Browse …")
            btn_browse_log.clicked.connect(self._choose_log_dir)
            log_dir_layout.addWidget(self._le_log_dir)
            log_dir_layout.addWidget(btn_browse_log)
            layout.addLayout(log_dir_layout)

            grp.setLayout(layout)
            adv = QGroupBox("Reliability & Memory")
            al  = QGridLayout()
            al.addWidget(QLabel("HTTP Retries:"), 0, 0)
            self._spin_http_retries = QSpinBox()
            self._spin_http_retries.setRange(1, 10)
            self._spin_http_retries.setValue(int(HTTP_RETRY_COUNT))
            self._spin_http_retries.setToolTip("Total fetch attempts per page. 1=no retry.")
            al.addWidget(self._spin_http_retries, 0, 1)
            al.addWidget(QLabel("Retry Delay (s):"), 0, 2)
            self._spin_retry_delay = QDoubleSpinBox()
            self._spin_retry_delay.setRange(0.5, 30.0)
            self._spin_retry_delay.setSingleStep(0.5)
            self._spin_retry_delay.setDecimals(1)
            self._spin_retry_delay.setValue(float(HTTP_RETRY_DELAY_SEC))
            al.addWidget(self._spin_retry_delay, 0, 3)
            al.addWidget(QLabel("Max Consec Fails:"), 1, 0)
            self._spin_max_fails = QSpinBox()
            self._spin_max_fails.setRange(0, 100)
            self._spin_max_fails.setValue(int(HEADLESS_MAX_CONSEC_FAILS))
            self._spin_max_fails.setToolTip("Headless: exit after N consecutive all-device failures. 0=never.")
            al.addWidget(self._spin_max_fails, 1, 1)
            al.addWidget(QLabel("RAM Flush Limit (%):"), 1, 2)
            self._spin_ram_pct = QSpinBox()
            self._spin_ram_pct.setRange(10, 95)
            self._spin_ram_pct.setSuffix(" %")
            self._spin_ram_pct.setValue(int(MEM_RAM_PCT_LIMIT))
            self._spin_ram_pct.setToolTip("Flush all outputs and clear store when system RAM reaches this %.")
            al.addWidget(self._spin_ram_pct, 1, 3)
            self._cb_append = QCheckBox("Append output files (otherwise overwrite)")
            self._cb_append.setChecked(bool(APPEND_OUTPUT_FILES))
            self._cb_append.setToolTip("Checked=append to existing files. Unchecked=overwrite at run start.")
            al.addWidget(self._cb_append, 2, 0, 1, 4)
            adv.setLayout(al)
            layout.addWidget(adv)

            return grp

        def _build_action_buttons(self) -> QWidget:
            """Build Poll Now / Start / Stop action buttons."""
            widget = QWidget()
            layout = QHBoxLayout(widget)

            self._btn_poll   = QPushButton("Poll Now")
            self._btn_start  = QPushButton("Start Auto")
            self._btn_stop   = QPushButton("Stop")
            self._btn_veusz  = QPushButton("Open in Veusz")
            self._btn_stop.setEnabled(False)
            self._btn_veusz.setToolTip(
                "Build plots in the live Veusz window and save as .vszh5 (HDF5)"
            )

            self._btn_poll.clicked.connect(self._do_poll_once)
            self._btn_start.clicked.connect(self._do_start)
            self._btn_stop.clicked.connect(self._do_stop)
            self._btn_veusz.clicked.connect(self._do_open_veusz)

            for b in [self._btn_poll, self._btn_start, self._btn_stop,
                      self._btn_veusz]:
                layout.addWidget(b)

            return widget

        def _build_status_bar(self) -> None:
            """Build the bottom status bar."""
            self._status_bar = QStatusBar()
            self.setStatusBar(self._status_bar)
            self._status_bar.showMessage(
                "Ready — configure options and click Poll Now.")

        # ----------------------------------------------------------------
        # Plot tab management
        # ----------------------------------------------------------------
        def _populate_plot_tabs(self, png_specs: "List[Dict[str, Any]]") -> None:
            """Populate preview tabs from pre-rendered PNG byte-buffers.

            Memory model
            ------------
            Receives ``List[Dict]`` from ``build_preview_pngs()``.  Each dict
            holds ``png_bytes`` (raw PNG data already rendered by the Agg
            canvas) plus plot metadata.  No matplotlib Figure objects are
            created or held inside this method — the bytes go straight into a
            ``QPixmap`` and the buffer is released immediately.  Peak memory
            per refresh cycle is bounded by the total size of one set of PNG
            thumbnails (~50–200 KB for all tabs) and is GC-eligible as soon
            as the method returns.

            Per-plot interactive button
            ---------------------------
            Each tab has a full-width button at the bottom.  Clicking opens
            ``_open_interactive_for_plot``, which snapshots the live store at
            that instant and displays a frozen interactive window in a daemon
            thread.  Background polling, all other tabs, and file writers
            continue without interruption.

            Parameters
            ----------
            png_specs : List[Dict[str, Any]]
                Output of ``build_preview_pngs()``.  Required keys per dict:
                ``png_bytes``, ``title``, ``ip``, ``tname``, ``group``.
            """
            self._tab_widget.clear()

            if not png_specs:
                placeholder = QLabel("No data yet — click 'Poll Now' to fetch.")
                placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self._tab_widget.addTab(placeholder, "Waiting …")
                return

            for spec in png_specs:
                png_bytes = spec["png_bytes"]
                title     = spec.get("title", "Plot")
                fig_ip    = spec.get("ip")
                fig_tname = spec.get("tname")
                fig_group = spec.get("group")
                plot_key  = (fig_ip, fig_tname, fig_group)

                # Load PNG bytes directly into QPixmap — no Figure object ever made
                pixmap = QtGui.QPixmap()
                pixmap.loadFromData(png_bytes, "PNG")

                lbl = QLabel()
                lbl.setPixmap(pixmap)
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

                scroll = QScrollArea()
                scroll.setWidget(lbl)
                scroll.setWidgetResizable(True)

                # Full-width interactive-plot button anchored to bottom of tab
                btn_text = (
                    f"▶  Open Interactive — {title}"
                    if len(title) <= 55
                    else "▶  Open Interactive Plot"
                )
                iplot_btn = QPushButton(btn_text)
                iplot_btn.setMinimumHeight(34)
                iplot_btn.setStyleSheet(
                    "QPushButton {"
                    "  background-color: #2a5298;"
                    "  color: white;"
                    "  font-weight: bold;"
                    "  border: none;"
                    "  border-radius: 0px;"
                    "}"
                    "QPushButton:hover  { background-color: #3a6bc4; }"
                    "QPushButton:pressed{ background-color: #1e3d7a; }"
                )
                iplot_btn.setToolTip(
                    "Open a full interactive matplotlib window for this plot.\n"
                    "Zoom, pan, and cursor-pick data points freely.\n"
                    "Background polling and all other tabs keep updating.\n"
                    "This window shows a frozen snapshot from when you clicked\n"
                    "and will not auto-refresh while open."
                )
                # Default-argument capture avoids late-binding closure bug
                iplot_btn.clicked.connect(
                    lambda _c=False,
                           _k=plot_key,
                           _i=fig_ip,
                           _t=fig_tname,
                           _g=fig_group,
                           _ti=title:
                    self._open_interactive_for_plot(_k, _i, _t, _g, _ti)
                )

                tab_w   = QWidget()
                tab_lay = QVBoxLayout(tab_w)
                tab_lay.setContentsMargins(0, 0, 0, 0)
                tab_lay.setSpacing(0)
                tab_lay.addWidget(scroll, stretch=1)
                tab_lay.addWidget(iplot_btn, stretch=0)

                self._tab_widget.addTab(tab_w, title[:30])

        def _choose_out_dir(self) -> None:
            """Open a folder dialog to select the output root directory."""
            path = QFileDialog.getExistingDirectory(
                self, "Select Output Root Directory", self._le_out_dir.text()
            )
            if path:
                self._le_out_dir.setText(path)
                # Auto-update the log dir field to match the new root
                # unless the user has already customised it independently.
                default_log = os.path.join(OUTPUT_BASE_DIR, "logs")
                if self._le_log_dir.text() == default_log or not self._le_log_dir.text():
                    self._le_log_dir.setText(os.path.join(path, "logs"))

        def _choose_log_dir(self) -> None:
            """Open a folder dialog to select the log-file directory."""
            path = QFileDialog.getExistingDirectory(
                self, "Select Log Directory", self._le_log_dir.text()
            )
            if path:
                self._le_log_dir.setText(path)

        def _get_runtime_config(self) -> Dict[str, Any]:
            """Collect current GUI state into a config dict."""
            base = self._le_out_dir.text().strip() or OUTPUT_BASE_DIR
            log = self._le_log_dir.text().strip() or os.path.join(base, "logs")
            # Parse the comma-separated IP list from the GUI field.
            # Strip whitespace, drop any empty tokens.
            raw_ips = self._le_ip_list.text()
            ip_list = [ip.strip() for ip in raw_ips.split(",") if ip.strip()]
            if not ip_list:
                ip_list = list(IP_LIST)   # fall back to header default
            return {
                "ip_list":           ip_list,
                "sample_period":     self._spin_period.value(),
                # enable_gui is not surfaced in the GUI (already running);
                # it is read directly from the ENABLE_GUI module variable.
                "enable_fits":       int(self._cb_fits.isChecked()),
                "enable_csv":        int(self._cb_csv.isChecked()),
                "enable_xlsx":       int(self._cb_xlsx.isChecked()),
                "enable_log_append": int(self._cb_log.isChecked()),
                "enable_log_file":   int(self._cb_log_file.isChecked()),
                "enable_veusz":      int(self._cb_veusz.isChecked()),
                "veusz_write_on_flush": int(self._cb_veusz_on_flush.isChecked()),
                # Runtime output directories — derived from the GUI fields.
                # All sub-dirs are built under output_base_dir unless the
                # user has overridden log_dir independently.
                "output_base_dir":   base,
                "fits_dir":          os.path.join(base, "fits"),
                "csv_dir":           os.path.join(base, "csv"),
                "xlsx_dir":          os.path.join(base, "xlsx"),
                "veusz_dir":         os.path.join(base, "veusz"),
                "log_dir":                  log,
                "http_retry_count":         int(self._spin_http_retries.value()),
                "http_retry_delay_sec":     float(self._spin_retry_delay.value()),
                "headless_max_consec_fails":int(self._spin_max_fails.value()),
                "mem_ram_pct_limit":        float(self._spin_ram_pct.value()),
                "append_files":             int(self._cb_append.isChecked()),
            }

        def _do_poll_once(self) -> None:
            """Perform a single synchronous poll and refresh display."""
            self._append_log("Starting single poll …")
            cfg = self._get_runtime_config()
            # poll_all_devices returns a flat dict keyed by "ip_tablename".
            # update_named_dicts() converts it into the nested ALL_DEVICE_DATA
            # structure {ip: {table_name: parsed_dict}} used by all downstream
            # functions.  Always pass ALL_DEVICE_DATA to those functions, never
            # the raw flat 'data' return value.
            data = poll_all_devices(
                ip_list=cfg["ip_list"],
                table_names=TABLE_NAMES,
                retries=int(cfg.get("http_retry_count", HTTP_RETRY_COUNT)),
                retry_delay=float(cfg.get("http_retry_delay_sec", HTTP_RETRY_DELAY_SEC)),
            )
            _meta = data.pop("__poll_meta__", {})
            if _meta.get("all_failed", False):
                self._append_log("WARNING: All IPs failed — check network/device.")
                # Do not call update_named_dicts or _process_outputs when every
                # device failed — ALL_DEVICE_DATA would be rebuilt with only
                # error entries, corrupting the time-series accumulator.
            else:
                # populates ALL_DEVICE_DATA and appends to TIME_SERIES_STORE
                update_named_dicts(data, ip_list=cfg.get("ip_list", IP_LIST))
                self._process_outputs(ALL_DEVICE_DATA, cfg)
            png_specs = build_preview_pngs()          # memory-safe PNG pipeline
            self._populate_plot_tabs(png_specs)
            gc.collect()
            self._append_log(
                f"Poll complete — {len(ALL_DEVICE_DATA)} device(s), "
                f"{sum(len(t) for t in ALL_DEVICE_DATA.values())} table dicts."
            )
            self._status_bar.showMessage(
                f"Last poll: {datetime.datetime.now().strftime('%H:%M:%S')}")

        def _do_start(self) -> None:
            """Start the background polling thread."""
            if self._thread and self._thread.isRunning():
                return
            # Reset flush counter so the end-of-run Veusz write correctly
            # reflects whether any RAM flush fired during this run.
            global _VEUSZ_FLUSH_COUNT
            _VEUSZ_FLUSH_COUNT = 0
            self._veusz_final_written = False   # reset guard for new run
            cfg = self._get_runtime_config()
            self._thread = PollThread(cfg, parent=self)
            self._thread.data_ready.connect(self._on_data_ready)
            self._thread.error_occur.connect(self._on_thread_error)
            self._thread.log_message.connect(self._append_log)
            self._thread.consec_limit.connect(self._on_consec_limit)
            self._thread.start()
            self._btn_start.setEnabled(False)
            self._btn_stop.setEnabled(True)
            self._status_bar.showMessage("Auto-polling started …")

        def _do_stop(self) -> None:
            """Stop the auto-poll thread, then write final Veusz file if enabled."""
            if self._thread:
                self._thread.stop()
                # Wait up to 20 s — poll_all_devices may be mid-HTTP request.
                # HTTP_TIMEOUT_SEC(5) × retries(3) + margin = ~18 s worst case.
                if not self._thread.wait(20_000):
                    logger.warning(
                        "_do_stop: PollThread did not finish in 20 s — "
                        "terminating forcibly.")
                    self._thread.terminate()
                    self._thread.wait(2000)
            self._btn_start.setEnabled(True)
            self._btn_stop.setEnabled(False)
            self._status_bar.showMessage("Auto-polling stopped.")
            # Write Veusz with all accumulated samples now that polling has stopped.
            # Use TIME_SERIES_STORE (canonical accumulator) not ALL_DEVICE_DATA.
            # _veusz_final_written guards against a second write when closeEvent
            # also calls _do_stop after the user has already clicked Stop.
            cfg = self._get_runtime_config()
            if cfg.get("enable_veusz") and TIME_SERIES_STORE and not self._veusz_final_written:
                veusz_dir = cfg.get("veusz_dir", VEUSZ_DIR)
                # If any RAM flush fired during this run, TIME_SERIES_STORE only
                # holds post-last-flush data.  Use a timestamped filename so this
                # partial window is preserved alongside any earlier flush snapshots
                # rather than overwriting the canonical fixed-name file with
                # incomplete data.  If no flush ever fired, the store holds the
                # entire run — write the fixed canonical file.
                _end_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S") \
                    if _VEUSZ_FLUSH_COUNT > 0 else None
                _label = f"(flush window {_VEUSZ_FLUSH_COUNT + 1})" \
                    if _end_ts else "(full run)"
                self._append_log(
                    f"Writing final Veusz file {_label}…")
                try:
                    _app_f = bool(cfg.get("append_files", APPEND_OUTPUT_FILES))
                    write_veusz(ALL_DEVICE_DATA, veusz_dir,
                                show_window=False, append=_app_f,
                                timestamp_suffix=_end_ts)
                    self._append_log(f"Veusz saved: {veusz_dir}")
                    self._veusz_final_written = True   # prevent duplicate write on close
                except Exception as exc:
                    self._append_log(f"Veusz final write error: {exc}")
                    logger.error("Veusz final write failed: %s", exc)

        def _on_data_ready(self, data: Dict) -> None:
            """Slot: called each poll cycle. Handles RAM flush then updates plots."""
            cfg = self._get_runtime_config()
            _ram_pct   = system_ram_used_pct()
            _ram_limit = float(cfg.get("mem_ram_pct_limit", MEM_RAM_PCT_LIMIT))
            if _ram_pct >= _ram_limit:
                self._append_log(
                    f"[RAM Flush] RAM {_ram_pct:.1f}% >= {_ram_limit:.0f}%. Flushing...")
                if self._flush_future is not None and not self._flush_future.done():
                    try:
                        self._flush_future.result(timeout=60)
                    except Exception as _fe:
                        logger.error("RAM flush wait: %s", _fe)
                # Assign the returned future so the flush guard at _process_outputs
                # knows this flush is in-flight and won't launch a concurrent write.
                self._flush_future = flush_outputs_parallel(cfg)
                # Snapshot the store under the lock BEFORE clearing it.
                # write_veusz (if enabled) will use this snapshot so it runs
                # against freed memory instead of peak-pressure memory,
                # preventing a second RAM-flush trigger from the Veusz subprocess.
                _vz_snapshot: Optional[Dict[str, Any]] = None
                if cfg.get("enable_veusz") and cfg.get("veusz_write_on_flush"):
                    with _TS_LOCK:
                        _vz_snapshot = copy.deepcopy(dict(TIME_SERIES_STORE))
                # Wait for the background flush to finish BEFORE clearing the
                # store — avoids a race where flush threads read TIME_SERIES_STORE
                # concurrently with _clear_time_series_store().
                if self._flush_future is not None and not self._flush_future.done():
                    try:
                        self._flush_future.result(timeout=60)
                    except Exception as _fw:
                        logger.error("RAM flush wait before clear: %s", _fw)
                # Clear the store NOW — before launching Veusz subprocess so
                # the subprocess starts with system RAM already freed.
                _clear_time_series_store()
                global _VEUSZ_FLUSH_COUNT
                _VEUSZ_FLUSH_COUNT += 1
                self._flush_future = None
                self._append_log(f"[RAM Flush] Store cleared. RAM now {system_ram_used_pct():.1f}%.")
                if _vz_snapshot and ALL_DEVICE_DATA:
                    try:
                        # Timestamped filename so each flush window is preserved
                        # as a separate file.  The Veusz embed API cannot append
                        # to an existing .vszh5 — every Save() builds from scratch.
                        _vts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                        _vdir = cfg.get("veusz_dir", VEUSZ_DIR)
                        write_veusz(ALL_DEVICE_DATA, _vdir,
                                    show_window=False, append=True,
                                    timestamp_suffix=_vts,
                                    ts_snapshot=_vz_snapshot)
                        self._append_log(
                            f"[Veusz flush] Saved flush window to {_vdir} "
                            f"(suffix {_vts}).")
                    except Exception as _ve:
                        logger.error("Veusz RAM-flush: %s", _ve)
                    finally:
                        del _vz_snapshot   # release snapshot memory
                        gc.collect()
                else:
                    logger.debug(
                        "Veusz skipped during RAM flush (veusz_write_on_flush=0). "
                        "Will write at Stop/loop-end.")
            self._process_outputs(ALL_DEVICE_DATA, cfg)
            # build_preview_pngs() renders directly via FigureCanvasAgg —
            # no pyplot, no Figure registry, no persistent Figure objects.
            # Memory per cycle ≈ size of PNG thumbnails only (~50–200 KB).
            png_specs = build_preview_pngs()
            self._populate_plot_tabs(png_specs)
            # Explicitly collect Agg renderer scratch buffers and any
            # reference cycles that Python's ref-counter missed.
            gc.collect()
            self._status_bar.showMessage(
                f"Updated: {datetime.datetime.now().strftime('%H:%M:%S')} "
                f"| RAM: {_ram_pct:.1f}% / {_ram_limit:.0f}%")

        def _on_thread_error(self, msg: str) -> None:
            self._append_log(f"ERROR: {msg}")
            self._status_bar.showMessage(f"Error — {msg[:60]}")

        def _on_consec_limit(self, count: int) -> None:
            """Slot: PollThread hit the consecutive-failure limit — auto-stop.

            Delegates to _do_stop() so that the final Veusz file (and any other
            cleanup) is written consistently regardless of whether the user
            clicked Stop or the limit was reached automatically.
            """
            limit = int(self._spin_max_fails.value())
            self._append_log(
                f"[Auto-Stop] {count} consecutive all-device failure(s) reached "
                f"the limit ({limit}). Polling stopped.")
            # _do_stop() updates button states, waits for the thread, and writes
            # the final Veusz file — call it instead of duplicating that logic here.
            self._do_stop()

        def _open_interactive_for_plot(
            self,
            plot_key:  tuple,
            ip:        Optional[str],
            tname:     Optional[str],
            group:     Optional[str],
            title:     str,
        ) -> None:
            """Snapshot the store and emit _sig_open_iplot to open an interactive dialog.

            Design
            ------
            * Data is **snapshotted under lock** at the moment the button is
              clicked.  The QDialog that opens shows that frozen snapshot and
              is never auto-refreshed, giving the user a stable view for
              zooming, panning, and cursor-picking.
            * All Qt widget creation happens on the main thread via the
              ``_sig_open_iplot`` signal — Qt prohibits widget creation from
              worker threads.  This method just builds the frozen data dict
              and emits the signal; ``_show_iplot_dialog`` does the rest.
            * Background polling, all file writers, and all other preview
              tabs continue to update completely uninterrupted.
            * The Agg backend (set at module import) is never touched.
            * A duplicate-window guard (``_iplot_open_set``) prevents a
              second window for the same plot key from being opened while
              one is already open.

            Parameters
            ----------
            plot_key : tuple
                ``(ip, tname, group)`` — unique identifier for this plot.
            ip : str or None
                Device IP address.
            tname : str or None
                Table name (None for overlay plots).
            group : str or None
                Overlay group label (None for per-table plots).
            title : str
                Human-readable window title.
            """
            if not TIME_SERIES_STORE:
                self._append_log("No data yet — poll first.")
                return

            if plot_key in self._iplot_open_set:
                self._append_log(
                    f"Interactive window already open for '{title}'. "
                    "Close it before opening a new one for this plot.")
                return

            # ── Snapshot the relevant data under lock NOW (frozen for this window) ──
            with _TS_LOCK:
                if ip and ip in TIME_SERIES_STORE:
                    if tname and tname in TIME_SERIES_STORE[ip]:
                        # Per-table snapshot
                        raw = TIME_SERIES_STORE[ip][tname]
                        frozen_store = {
                            ip: {
                                tname: {
                                    "timestamps_local": list(raw.get("timestamps_local", [])),
                                    "columns": {
                                        c: list(v) for c, v in raw.get("columns", {}).items()
                                    },
                                    "units": dict(raw.get("units", {})),
                                }
                            }
                        }
                    elif group:
                        # Overlay snapshot — collect all tables for this IP
                        frozen_store = {
                            ip: {
                                tn: {
                                    "timestamps_local": list(td.get("timestamps_local", [])),
                                    "columns": {
                                        c: list(v) for c, v in td.get("columns", {}).items()
                                    },
                                    "units": dict(td.get("units", {})),
                                }
                                for tn, td in TIME_SERIES_STORE[ip].items()
                            }
                        }
                    else:
                        self._append_log("Cannot resolve plot key — no data snapshot.")
                        return
                else:
                    self._append_log(f"IP {ip!r} not found in store.")
                    return

            n_samples = 0
            if ip and tname and ip in frozen_store and tname in frozen_store[ip]:
                n_samples = len(frozen_store[ip][tname]["timestamps_local"])
            elif ip and ip in frozen_store:
                n_samples = max(
                    len(td["timestamps_local"])
                    for td in frozen_store[ip].values()
                ) if frozen_store[ip] else 0

            self._iplot_open_set.add(plot_key)
            self._append_log(
                f"Opening interactive window: '{title}' "
                f"({n_samples} sample(s) — frozen snapshot).")
            # Emit signal — _show_iplot_dialog runs on the main thread.
            # Qt prohibits widget creation from worker threads; this is the
            # only correct pattern for opening a child window from a button
            # click that may have been triggered by any context.
            self._sig_open_iplot.emit({
                "plot_key":     plot_key,
                "ip":           ip,
                "tname":        tname,
                "group":        group,
                "title":        title,
                "frozen_store": frozen_store,
                "n_samples":    n_samples,
            })


        def _show_iplot_dialog(self, payload: dict) -> None:
            """Build and show a non-modal QDialog with a full matplotlib
            interactive canvas for the frozen snapshot in *payload*.

            Runs on the main thread (connected via ``_sig_open_iplot`` signal).
            All Qt widget creation is here; the Agg backend used for preview
            rendering is never disturbed.

            Parameters
            ----------
            payload : dict
                Dict emitted by ``_open_interactive_for_plot``.  Keys:
                ``plot_key``, ``ip``, ``tname``, ``group``, ``title``,
                ``frozen_store``, ``n_samples``.
            """
            try:
                from matplotlib.figure import Figure as MplFigure
                from matplotlib.backends.backend_qtagg import (
                    FigureCanvasQTAgg as FigureCanvas,
                    NavigationToolbar2QT as NavToolbar,
                )
            except ImportError:
                try:
                    from matplotlib.figure import Figure as MplFigure
                    from matplotlib.backends.backend_qt5agg import (
                        FigureCanvasQTAgg as FigureCanvas,
                        NavigationToolbar2QT as NavToolbar,
                    )
                except ImportError as exc:
                    self._append_log(
                        f"Interactive plot unavailable — "
                        f"no Qt matplotlib backend found: {exc}")
                    plot_key = payload.get("plot_key")
                    if plot_key:
                        self._iplot_open_set.discard(plot_key)
                    return

            plot_key     = payload["plot_key"]
            ip           = payload["ip"]
            tname        = payload["tname"]
            group        = payload["group"]
            title        = payload["title"]
            frozen_store = payload["frozen_store"]
            # n_samples from payload is used for logging in _open_interactive_for_plot;
            # inside this dialog we compute lengths locally from the frozen data.

            prop_colors = [
                "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
                "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
                "#bcbd22", "#17becf",
            ]

            fig = MplFigure(figsize=(13, 5))
            ax  = fig.add_subplot(111)
            any_data = False

            if tname:
                tdata = frozen_store.get(ip, {}).get(tname, {})
                cols  = tdata.get("columns", {})
                tss   = tdata.get("timestamps_local", [])
                n     = len(tss)
                x     = list(range(n))
                for c_idx, (param, values) in enumerate(cols.items()):
                    if len(values) != n or n == 0:
                        continue
                    unit = tdata.get("units", {}).get(param, "")
                    lbl  = f"{param} ({unit})" if unit else param
                    ax.plot(x, values, label=lbl,
                            color=prop_colors[c_idx % len(prop_colors)],
                            linewidth=1.4, marker="o", markersize=3, picker=5)
                    any_data = True
                if tss and n > 0:
                    step = max(1, n // 10)
                    ax.set_xticks(x[::step])
                    ax.set_xticklabels(
                        tss[::step], rotation=35, ha="right", fontsize=7)
                ax.set_xlabel("Sample index")
                ax.set_ylabel("Value")
                ax.set_title(
                    f"{tname}\n{ip}  [Frozen snapshot \u2014 {n} sample(s)]",
                    fontsize=9)
            else:
                c_idx  = 0
                all_ts: list = []
                for tn, tdata in frozen_store.get(ip, {}).items():
                    cols = tdata.get("columns", {})
                    tss  = tdata.get("timestamps_local", [])
                    n    = len(tss)
                    subs = VEUSZ_OVERLAY_GROUPS.get(group, [])
                    for param, values in cols.items():
                        if not any(s in param.lower() for s in subs):
                            continue
                        if len(values) != n or n == 0:
                            continue
                        unit = tdata.get("units", {}).get(param, "")
                        lbl  = f"{tn[:10]}/{param}"
                        if unit:
                            lbl += f" ({unit})"
                        ax.plot(list(range(n)), values, label=lbl,
                                color=prop_colors[c_idx % len(prop_colors)],
                                linewidth=1.4, marker="o", markersize=3, picker=5)
                        c_idx   += 1
                        any_data = True
                        if len(tss) > len(all_ts):
                            all_ts = tss
                if all_ts:
                    nn   = len(all_ts)
                    step = max(1, nn // 10)
                    ax.set_xticks(list(range(0, nn, step)))
                    ax.set_xticklabels(
                        all_ts[::step], rotation=35, ha="right", fontsize=7)
                ax.set_xlabel("Sample index")
                ax.set_ylabel(group or "Value")
                ax.set_title(f"Overlay: {group}\n{ip}  [Frozen snapshot]",
                             fontsize=9)

            if not any_data:
                ax.text(0.5, 0.5, "No numeric data in snapshot",
                        ha="center", va="center",
                        transform=ax.transAxes, fontsize=12, color="grey")

            ax.grid(alpha=0.3)
            ax.legend(fontsize=7, loc="upper left",
                      bbox_to_anchor=(1.01, 1), borderaxespad=0)
            fig.tight_layout(rect=(0, 0, 0.82, 1))

            # Embed in a non-modal QDialog with the full NavigationToolbar.
            dlg = QDialog(self)
            dlg.setWindowTitle(f"Interactive \u2014 {title}")
            dlg.resize(1100, 520)
            try:
                dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            except AttributeError:
                # PySide2 / older PyQt5 use the short form
                dlg.setAttribute(Qt.WA_DeleteOnClose, True)

            canvas  = FigureCanvas(fig)
            toolbar = NavToolbar(canvas, dlg)

            lay = QVBoxLayout(dlg)
            lay.setContentsMargins(4, 4, 4, 4)
            lay.setSpacing(2)
            lay.addWidget(toolbar)
            lay.addWidget(canvas, stretch=1)

            # Release the open-set guard when the dialog is closed.
            dlg.finished.connect(lambda _: self._on_iplot_closed(plot_key))
            # Release the Agg renderer memory when the dialog is closed.
            # fig.clf() drops all axes/artists; the canvas widget is then
            # freed by Qt via WA_DeleteOnClose.  Without this, the Agg
            # backing buffer (~13 MB for 1100×520) is retained until the
            # next GC cycle.
            dlg.finished.connect(lambda _: (fig.clf(), gc.collect()))

            dlg.show()   # non-modal: collection never blocks

        def _on_iplot_closed(self, plot_key: tuple) -> None:
            """Slot: called when an interactive plot dialog is closed.

            Removes *plot_key* from the open-window tracking set so the same
            plot can be reopened.  The next normal poll cycle refreshes the
            preview tab automatically.
            """
            self._iplot_open_set.discard(plot_key)
            self._append_log(
                "Interactive window closed. "
                "Preview will refresh on the next poll cycle.")

        def _do_open_veusz(self) -> None:
            """
            Open the live Veusz window (separate process window with toolbar)
            for the most recently polled data, then save as .vszh5 (HDF5).

            The output directory is read from the GUI 'Output Root Directory'
            field so it honours any runtime override made by the user.
            Runs synchronously in the GUI thread — Veusz's own event loop
            handles interaction in its separate window.  The file is saved
            when write_veusz() reaches doc.Save() after building all pages.
            """
            if not ALL_DEVICE_DATA:
                self._append_log("No data to plot — run Poll Now first.")
                return
            cfg = self._get_runtime_config()
            veusz_dir = cfg.get("veusz_dir", VEUSZ_DIR)
            self._append_log(f"Opening Veusz window … output: {veusz_dir}")
            try:
                open_veusz_preview(ALL_DEVICE_DATA, veusz_dir)
                self._append_log(f"Veusz HDF5 file(s) saved to: {veusz_dir}")
            except Exception as exc:
                self._append_log(f"Veusz error: {exc}")
                logger.error("Veusz preview failed: %s", exc)

        def _process_outputs(self, data: Dict, cfg: Dict) -> None:
            """
            Dispatch file-output writers to a background thread-pool so the
            GUI poll cycle returns immediately and sampling is never blocked
            by I/O.

            Uses flush_outputs_parallel() — the same function used by the
            headless loop.  A guard on self._flush_future prevents launching
            a new flush while the previous one is still running, which would
            risk concurrent writes to the same files.

            Veusz is intentionally excluded — it is written once on Stop
            (_do_stop) so it always contains the full accumulated dataset.

            Parameters
            ----------
            data : Dict
                Unused directly; kept for signature compatibility.  All
                writers pull from TIME_SERIES_STORE / ALL_DEVICE_DATA.
            cfg : Dict
                Runtime config dict from _get_runtime_config().
            """
            # If a previous flush is still running, skip this cycle's flush
            # rather than risk overlapping writes.  The TIME_SERIES_STORE will
            # include this cycle's data in the *next* flush that does fire.
            if self._flush_future is not None and not self._flush_future.done():
                logger.warning(
                    "Previous output flush still running — skipping flush for this cycle. "
                    "Consider increasing sample period if this recurs."
                )
                return

            self._flush_future = flush_outputs_parallel(cfg)
            # Veusz is NOT written mid-loop — it is written once when polling
            # stops (_do_stop) so it contains all accumulated samples.
            # Use 'Open in Veusz' button to preview with latest data at any time.

        def _append_log(self, text: str) -> None:
            """Append a line to the status console widget."""
            self._log_console.append(text)
            self._log_console.verticalScrollBar().setValue(
                self._log_console.verticalScrollBar().maximum()
            )

        # ----------------------------------------------------------------
        # %%% Theme switching
        # ----------------------------------------------------------------
        def _toggle_theme(self) -> None:
            if self._dark_mode:
                self._apply_light()
            else:
                self._apply_dark()

        def _apply_dark(self) -> None:
            QtWidgets.QApplication.instance().setStyleSheet(self.DARK_STYLE)
            self._act_toggle_theme.setText("Switch to &Light Theme")
            self._dark_mode = True

        def _apply_light(self) -> None:
            QtWidgets.QApplication.instance().setStyleSheet(self.LIGHT_STYLE)
            self._act_toggle_theme.setText("Switch to &Dark Theme")
            self._dark_mode = False

        # ----------------------------------------------------------------
        # %%% About dialog
        # ----------------------------------------------------------------
        def _show_about(self) -> None:
            from qtpy.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "About AB Power Meter Monitor",
                "Allen-Bradley Site Power Meter Monitor\n"
                "NRAO / Green Bank Observatory\n\n"
                "Polls AB 1403 power meters (pages 0–10),\n"
                "stores data in named Python dicts, and\n"
                "exports to FITS, CSV, XLSX, Veusz, and logs.\n\n"
                "Author: W. Wallace\n"
                "Version: 1.4.19\n"
                "Python: 3.8+\n"
                "Qt backend: PySide6 (via QtPy)",
            )

        def closeEvent(self, event) -> None:
            """Ensure all resources are released cleanly on window close.

            Order of operations
            -------------------
            1. Close any open interactive-plot QDialogs before the parent
               window is destroyed (prevents dangling child widget crashes).
            2. Stop PollThread and wait up to 20 s (worst-case HTTP timeout).
               Forcibly terminate if still alive after the wait.
            3. Disconnect PollThread signals so no slots fire after destroy.
            4. Wait up to 30 s for any in-flight background file flush.
            5. Detach the QTextEditHandler before the QTextEdit is destroyed.
            6. Force a GC cycle to release matplotlib Agg buffers and any
               reference cycles held in TIME_SERIES_STORE / ALL_DEVICE_DATA.
            """
            # 1. Close child iplot dialogs — findChildren works on QDialog
            #    children of this QMainWindow (parented via QDialog(self)).
            try:
                from qtpy.QtWidgets import QDialog as _QDlg
                for dlg in list(self.findChildren(_QDlg)):
                    try:
                        dlg.close()
                    except Exception:
                        pass
            except Exception:
                pass

            # 2+3. Stop PollThread; disconnect signals to prevent post-destroy calls.
            self._do_stop()   # stops PollThread (waits up to 20 s / terminates)
            if self._thread is not None:
                try:
                    self._thread.data_ready.disconnect()
                    self._thread.error_occur.disconnect()
                    self._thread.log_message.disconnect()
                    self._thread.consec_limit.disconnect()
                except Exception:
                    pass   # already disconnected or never connected
                self._thread = None

            # 4. Wait for any in-flight background file flush.
            if self._flush_future is not None and not self._flush_future.done():
                logger.info(
                    "Waiting for background flush to finish before closing…")
                try:
                    self._flush_future.result(timeout=30)
                except Exception as exc:
                    logger.error("Flush error on close: %s", exc)
            self._flush_future = None

            # 5. Detach QTextEditHandler before widget is destroyed.
            if self._log_handler is not None:
                logging.getLogger("ABMonitor").removeHandler(self._log_handler)
                self._log_handler = None

            # 6. Release large in-memory stores and matplotlib Agg buffers.
            gc.collect()

            event.accept()

    # -----------------------------------------------------------------------
    # %%% Application entry point
    # -----------------------------------------------------------------------
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    window = MainWindow(switches=initial_switches, figures=initial_figures)
    window.show()

    sys.exit(app.exec())


# ===========================================================================
# %% HEADLESS MODE — run without GUI
# ===========================================================================
# ===========================================================================
#  HEADLESS CONSOLE OUTPUT HELPERS
# ===========================================================================

# Ordered list of all 11 named dict references used for console printing.
# Built as a callable so it always picks up the current module-global values.
def _get_named_dicts() -> Dict[str, Dict[str, Dict]]:
    """
    Return a two-level dict of all polled devices and their 11 table dicts.

    Structure
    ---------
    {
        "10.16.130.50": {
            "Device_Configuration_Table":         { ... },
            "Communications_Configuration_Table": { ... },
            "Voltage_Current_Table":              { ... },
            ...  (all 11 tables present for this device)
        },
        "10.16.130.51": { ... },
        ...
    }

    Device identity
    ---------------
    The outer key is the device IP address string as used throughout
    ALL_DEVICE_DATA and the _meta["source_ip"] field inside each table
    dict.  Each inner dict is the latest poll snapshot for that device;
    tables not yet fetched for a device are absent from the inner dict.

    Falls back gracefully to the first-device module-level globals if
    ALL_DEVICE_DATA is empty (e.g. called before any poll has run).

    Returns
    -------
    Dict[str, Dict[str, Dict]]
        Keyed by IP string, then by table name.
    """
    if ALL_DEVICE_DATA:
        # Return a shallow copy of ALL_DEVICE_DATA so the caller cannot
        # accidentally mutate the live module-level store.
        return {
            ip: dict(tables)
            for ip, tables in ALL_DEVICE_DATA.items()
        }

    # No poll data yet — return the module globals under a placeholder key
    # so the return shape is always Dict[ip -> Dict[tname -> dict]].
    return {
        "(no data)": {
            "Device_Configuration_Table":           Device_Configuration_Table,
            "Communications_Configuration_Table":   Communications_Configuration_Table,
            "Voltage_Current_Table":                Voltage_Current_Table,
            "Real_Time_Power_Table":                Real_Time_Power_Table,
            "Cumulative_Power_Table":               Cumulative_Power_Table,
            "Demand_Data_Table":                    Demand_Data_Table,
            "Diagnostic_Table":                     Diagnostic_Table,
            "Voltage_Current_Snapshot_Log_Table":   Voltage_Current_Snapshot_Log_Table,
            "Power_Snapshot_Log_Table":             Power_Snapshot_Log_Table,
            "MinMax_Log_Table":                     MinMax_Log_Table,
            "Diagnostic_Table_Extended":            Diagnostic_Table_Extended,
        }
    }


def _print_named_dicts(cycle: int, label: str = "current snapshot") -> None:
    """
    Print all 11 named table dicts for ALL devices to stdout as
    pretty-printed JSON, keyed by device IP.

    Parameters
    ----------
    cycle : int
        Current poll cycle number, included in the header.
    label : str
        Short description shown in the banner (e.g. "current snapshot").
    """
    all_devs = _get_named_dicts()
    print("\n" + "=" * 72)
    print(f"  NAMED TABLE DICTS — cycle {cycle} ({label})")
    print(f"  Devices: {list(all_devs.keys())}")
    print("=" * 72)
    for ip, tables in all_devs.items():
        print(f"\n{'─' * 72}")
        print(f"  Device: {ip}")
        print(f"{'─' * 72}")
        for dname, dval in tables.items():
            print(f"\n  {dname}:")
            if dval:
                # Exclude _meta for brevity; show all other k/v
                display = {k: str(v) for k, v in dval.items() if k != "_meta"}
                print(json.dumps(display, indent=4))
            else:
                print("    (empty — no data fetched yet)")


def _print_cumulative_store(cycle: int, reason: str = "end of loop") -> None:
    """
    Print the full accumulated TIME_SERIES_STORE to stdout.

    Each device → table is printed as a compact columnar summary showing
    all timestamps and all parameter values collected so far.

    Parameters
    ----------
    cycle : int
        Current poll cycle number, included in the header.
    reason : str
        Why the print was triggered (e.g. "end of loop", "stop signal",
        "memory flush").
    """
    with _TS_LOCK:
        # Deep-copy only the structures we need for printing
        snapshot = {
            ip: {
                tname: {
                    "timestamps_local": list(tdata["timestamps_local"]),
                    "columns": {p: list(v) for p, v in tdata["columns"].items()},
                    "units":   dict(tdata["units"]),
                }
                for tname, tdata in tables.items()
            }
            for ip, tables in TIME_SERIES_STORE.items()
        }

    banner = f"  CUMULATIVE TIME-SERIES STORE — cycle {cycle}, trigger: {reason}"
    print("\n" + "=" * 72)
    print(banner)
    print("=" * 72)

    if not snapshot:
        print("  (store is empty — no data accumulated yet)")
        return

    for ip, tables in snapshot.items():
        print(f"\n{'─' * 72}")
        print(f"  Device: {ip}")
        print(f"{'─' * 72}")
        for tname, tdata in tables.items():
            timestamps = tdata["timestamps_local"]
            columns = tdata["columns"]
            units_map = tdata["units"]
            params = list(columns.keys())
            n = len(timestamps)

            print(f"\n  Table: {tname}  ({n} sample(s))")
            if not timestamps:
                print("    (no samples yet)")
                continue

            # Header row
            col_labels = ["Timestamp_Local"] + [
                f"{p} ({units_map[p]})" if units_map.get(p) else p
                for p in params
            ]
            # Compute column widths for aligned output
            col_w = [max(3, len(h)) for h in col_labels]
            for i, ts in enumerate(timestamps):
                col_w[0] = max(col_w[0], len(ts))
                for j, p in enumerate(params, start=1):
                    v = columns[p][i] if i < len(columns[p]) else ""
                    col_w[j] = max(col_w[j], len(
                        str(v) if v is not None else ""))

            def _row(cells):
                return "  | " + " | ".join(
                    str(c).ljust(col_w[k]) for k, c in enumerate(cells)
                ) + " |"

            sep = "  |" + "|".join("-" * (w + 2) for w in col_w) + "|"
            print(_row(col_labels))
            print(sep)
            for i, ts in enumerate(timestamps):
                row = [ts] + [
                    str(columns[p][i]) if (p in columns and i < len(columns[p])
                                           and columns[p][i] is not None) else ""
                    for p in params
                ]
                print(_row(row))


# Module-level stop flag for headless loop (set by signal handler or stop script)
_HEADLESS_STOP = threading.Event()


def _install_signal_handlers() -> None:
    """
    Install SIGINT / SIGTERM handlers that set _HEADLESS_STOP cleanly.

    Also removes any pre-existing stop-signal file from the last run so a
    fresh loop does not exit immediately.
    """
    def _handler(signum, frame):  # noqa: ANN001
        logger.info(
            "Signal %s received — requesting clean stop after current poll.", signum)
        _HEADLESS_STOP.set()

    try:
        signal.signal(signal.SIGINT,  _handler)
        signal.signal(signal.SIGTERM, _handler)
    except (OSError, ValueError):
        pass   # non-main thread — signals cannot be registered; ignore

    # Remove stale stop-signal file from a previous run
    try:
        if os.path.exists(STOP_SIGNAL_FILE):
            os.remove(STOP_SIGNAL_FILE)
            logger.info("Removed stale stop-signal file: %s", STOP_SIGNAL_FILE)
    except OSError:
        pass


def system_ram_used_pct() -> float:
    """Return system RAM usage 0-100.  Safe fallback = 0.0."""
    try:
        if psutil is not None:
            return float(psutil.virtual_memory().percent)
    except Exception:
        pass
    return 0.0


def _clear_time_series_store() -> None:
    """Lock, clear TIME_SERIES_STORE, force GC — called after RAM flush."""
    # No 'global' declaration needed — .clear() mutates in-place, no rebind.
    with _TS_LOCK:
        TIME_SERIES_STORE.clear()
    gc.collect()
    logger.info("[RAM Flush] TIME_SERIES_STORE cleared — GC collected.")


def run_headless(cfg: Dict[str, Any]) -> None:
    """
    Execute headless polling loop — N cycles or infinite until stopped.

    Loop behaviour
    --------------
    • HEADLESS_LOOP_COUNT = 0  → run until Ctrl-C, SIGTERM, or stop-signal
      file (``STOP_SIGNAL_FILE``) is touched.
    • HEADLESS_LOOP_COUNT = N  → run exactly N poll cycles then exit.

    Stopping cleanly
    ----------------
    From the same machine (another terminal or script)::

        touch <OUTPUT_BASE_DIR>/STOP_COLLECTION

    Or call the companion script::

        python ab_stop.py

    Or press Ctrl-C / send SIGTERM.

    The loop finishes the in-progress poll before exiting, then flushes
    all accumulated data to all enabled output formats (including Veusz).

    Adaptive flushing
    -----------------
    If TIME_SERIES_STORE grows beyond MEM_FLUSH_THRESHOLD_MB or free RAM
    falls below MEM_FREE_MIN_MB, intermediate CSV / XLSX / log writes are
    dispatched in a background thread-pool so sampling is not interrupted.
    Veusz is always written once at loop end only.

    Parameters
    ----------
    cfg : Dict[str, Any]
        Configuration dict built from module-level switch variables.
    """
    _install_signal_handlers()
    _HEADLESS_STOP.clear()   # ensure flag is clear for this run
    global _VEUSZ_FLUSH_COUNT
    _VEUSZ_FLUSH_COUNT = 0   # reset so end-of-run write knows if any flush fired

    # Read loop_count directly from the module global so that a caller
    # that sets  abm.HEADLESS_LOOP_COUNT = 1  is always honoured, even
    # if cfg was built before the assignment was made.
    loop_count = HEADLESS_LOOP_COUNT
    sample_sec = cfg.get("sample_period",          SAMPLE_PERIOD_SEC)
    veusz_dir = cfg.get("veusz_dir",              VEUSZ_DIR)
    infinite = (loop_count == 0)
    cycle = 0
    flush_future: Optional["concurrent.futures.Future"] = None
    _consec_fails: int = 0
    _max_fails:    int = int(cfg.get("headless_max_consec_fails",
                                      HEADLESS_MAX_CONSEC_FAILS))


    # Read console-output switches directly from module globals so that
    # caller assignments (e.g. abm.HEADLESS_SILENT = 1) are always honoured.
    silent = bool(HEADLESS_SILENT)
    dicts_only = bool(HEADLESS_CONSOLE_DICTS_ONLY) and not silent
    print_each = bool(HEADLESS_PRINT_EACH_SAMPLE) and not silent
    print_cumulative = bool(HEADLESS_PRINT_CUMULATIVE) and not silent

    # Redirect console streams when suppression is requested.
    # File handlers (log file on disk) are deliberately left intact in
    # both cases — only the terminal streams are affected.
    _devnull_stdout = None
    _devnull_stderr = None

    if silent:
        # Full silence: strip all console StreamHandlers FIRST (while
        # sys.__stdout__ still matches the handler's captured stream),
        # then redirect stdout/stderr to /dev/null.
        # Order matters: handlers are compared against sys.__stdout__ and
        # sys.__stderr__ before redirection so the match is reliable.
        root_log = logging.getLogger()
        for h in list(root_log.handlers):
            if isinstance(h, logging.StreamHandler) and getattr(
                h, "stream", None
            ) in (sys.__stdout__, sys.__stderr__):
                root_log.removeHandler(h)
        root_log.addHandler(logging.NullHandler())
        # Now redirect the actual streams.
        _devnull_stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: WPS515
        _devnull_stderr = open(os.devnull, "w", encoding="utf-8")
        sys.stdout = _devnull_stdout
        sys.stderr = _devnull_stderr

    elif dicts_only:
        # Dicts-only: strip console StreamHandlers but leave stdout open
        # for print() calls in the caller.
        root_log = logging.getLogger()
        for h in list(root_log.handlers):
            if isinstance(h, logging.StreamHandler) and getattr(
                h, "stream", None
            ) in (sys.__stdout__, sys.__stderr__):
                root_log.removeHandler(h)
        root_log.addHandler(logging.NullHandler())

    if not dicts_only:
        logger.info(
            "Headless loop starting — %s, sample period %.1f s, output: %s",
            "infinite (stop with Ctrl-C / stop-signal file)" if infinite else f"{loop_count} cycles",
            sample_sec,
            cfg.get("output_base_dir", OUTPUT_BASE_DIR),
        )

    try:
        while True:
            # ── Check stop conditions ─────────────────────────────────────────────
            if _HEADLESS_STOP.is_set():
                if not dicts_only:
                    logger.info(
                        "Stop flag set — exiting loop after cycle %d.", cycle)
                if print_cumulative:
                    _print_cumulative_store(
                        cycle, reason="stop signal (SIGINT/SIGTERM)")
                break
            if os.path.exists(STOP_SIGNAL_FILE):
                if not dicts_only:
                    logger.info(
                        "Stop-signal file detected (%s) — exiting.", STOP_SIGNAL_FILE)
                try:
                    os.remove(STOP_SIGNAL_FILE)
                except OSError:
                    pass
                if print_cumulative:
                    _print_cumulative_store(
                        cycle, reason="ab_stop.py stop-signal file")
                break
            if not infinite and cycle >= loop_count:
                if not dicts_only:
                    logger.info(
                        "Reached %d cycle(s) — loop complete.", loop_count)
                break

            # ── Poll ─────────────────────────────────────────────────────────────────
            cycle += 1
            if not dicts_only:
                logger.info("--- Headless cycle %d%s ---",
                            cycle, f" / {loop_count}" if not infinite else "")
            t_poll_start = time.monotonic()

            data = poll_all_devices(
                ip_list=cfg["ip_list"],
                table_names=TABLE_NAMES,
                retries=int(cfg.get("http_retry_count", HTTP_RETRY_COUNT)),
                retry_delay=float(cfg.get("http_retry_delay_sec",
                                          HTTP_RETRY_DELAY_SEC)),
            )
            _poll_meta = data.pop("__poll_meta__", {})
            if _poll_meta.get("all_failed", False):
                _consec_fails += 1
                if not dicts_only:
                    logger.warning("Cycle %d: ALL IPs failed (consec: %d/%s).",
                        cycle, _consec_fails,
                        str(_max_fails) if _max_fails > 0 else "unlimited")
                if _max_fails > 0 and _consec_fails >= _max_fails:
                    logger.error("[headless] Consec failure limit %d — exiting.",
                                 _max_fails)
                    break
                # All IPs failed this cycle — do NOT call update_named_dicts.
                # Doing so would clear ALL_DEVICE_DATA and rebuild it with
                # only error entries, corrupting the TIME_SERIES_STORE
                # accumulator with gap timestamps.
            else:
                if _consec_fails > 0 and not dicts_only:
                    logger.info("Cycle %d: connectivity restored after %d fail(s).",
                                cycle, _consec_fails)
                _consec_fails = 0
                update_named_dicts(data, ip_list=cfg.get("ip_list", IP_LIST))  # also calls accumulate_poll()

            poll_elapsed = time.monotonic() - t_poll_start
            _ram_pct   = system_ram_used_pct()
            _ram_limit = float(cfg.get("mem_ram_pct_limit", MEM_RAM_PCT_LIMIT))
            if _ram_pct >= _ram_limit:
                logger.warning("[RAM Flush] RAM %.1f%% >= %.0f%%. Flushing...", _ram_pct, _ram_limit)
                if flush_future is not None and not flush_future.done():
                    try:
                        flush_future.result(timeout=60)
                    except Exception as _fe:
                        logger.error("Flush wait: %s", _fe)
                flush_future = flush_outputs_parallel(cfg)  # track in-flight flush
                # Snapshot the store under the lock BEFORE clearing it.
                # write_veusz will use this snapshot so the subprocess starts
                # with system RAM already freed, preventing a second RAM-flush
                # trigger from the Veusz subprocess memory spike.
                _vz_snapshot: Optional[Dict[str, Any]] = None
                if cfg.get("enable_veusz") and cfg.get("veusz_write_on_flush"):
                    with _TS_LOCK:
                        _vz_snapshot = copy.deepcopy(dict(TIME_SERIES_STORE))
                # Wait for the background flush to finish BEFORE clearing the
                # store — avoids a race where flush threads read TIME_SERIES_STORE
                # concurrently with _clear_time_series_store().
                if flush_future is not None and not flush_future.done():
                    try:
                        flush_future.result(timeout=60)
                    except Exception as _fw:
                        logger.error("RAM flush wait before clear: %s", _fw)
                    flush_future = None
                # Clear the store NOW — before launching Veusz subprocess so
                # the subprocess starts with system RAM already freed.
                _clear_time_series_store()
                _VEUSZ_FLUSH_COUNT += 1   # global declared at run_headless() entry
                logger.info("[RAM Flush] Store cleared. RAM now %.1f%%. Sampling continues.",
                            system_ram_used_pct())
                if _vz_snapshot and ALL_DEVICE_DATA:
                    try:
                        # Timestamped filename so each flush window is preserved
                        # as a separate file.  The Veusz embed API cannot append
                        # to an existing .vszh5 — every Save() builds from scratch.
                        _vts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                        _vdir = cfg.get("veusz_dir", VEUSZ_DIR)
                        write_veusz(ALL_DEVICE_DATA, _vdir,
                                    show_window=False, append=True,
                                    timestamp_suffix=_vts,
                                    ts_snapshot=_vz_snapshot)
                        logger.info("[Veusz flush] Saved flush window: %s (suffix %s)",
                                    _vdir, _vts)
                    except Exception as _ve:
                        logger.error("Veusz RAM-flush: %s", _ve)
                    finally:
                        del _vz_snapshot   # release snapshot memory
                        gc.collect()
                else:
                    logger.debug(
                        "Veusz skipped during RAM flush (VEUSZ_WRITE_ON_FLUSH=0). "
                        "Will write at loop end.")

            if not dicts_only:
                logger.info("Cycle %d poll complete in %.2f s — store %.1f MB, free RAM %.0f MB",
                            cycle, poll_elapsed, ts_store_size_mb(), system_free_ram_mb())

            # ── Per-cycle dict print (HEADLESS_PRINT_EACH_SAMPLE) ────────────────────
            # Prints the 11 named dicts (latest snapshot, first device) after
            # every successful poll cycle when the switch is enabled.
            if print_each:
                _print_named_dicts(cycle, label="current snapshot")

            # ── Adaptive mid-loop flush ───────────────────────────────────────────────
            # If store is getting large or RAM is tight, trigger a background
            # flush of CSV / XLSX / log (NOT Veusz — that waits for loop end).
            # Only launch a new flush if the previous one has completed.
            # Also print cumulative store (HEADLESS_PRINT_CUMULATIVE) on flush.
            if should_flush(cfg):
                if flush_future is None or flush_future.done():
                    if not dicts_only:
                        logger.info(
                            "Dispatching background flush (cycle %d)…", cycle)
                    if print_cumulative:
                        _print_cumulative_store(
                            cycle, reason="memory limit flush")
                    flush_future = flush_outputs_parallel(cfg)
                else:
                    if not dicts_only:
                        logger.warning(
                            "Previous flush still running — skipping mid-cycle flush.")

            # ── Wait for next cycle ──────────────────────────────────────────────────────────
            # Skip the sleep entirely if this was the last required cycle so
            # that callers with HEADLESS_LOOP_COUNT=1 return immediately
            # without blocking for a full sample period first.
            _last_cycle = (not infinite) and (cycle >= loop_count)
            if not _last_cycle:
                # Sleep in 0.5 s chunks to remain responsive to stop signals.
                remaining = sample_sec - (time.monotonic() - t_poll_start)
                while remaining > 0 and not _HEADLESS_STOP.is_set():
                    if os.path.exists(STOP_SIGNAL_FILE):
                        break
                    time.sleep(min(0.5, remaining))
                    remaining -= 0.5

    except KeyboardInterrupt:
        if not dicts_only:
            logger.info("KeyboardInterrupt — stopping headless loop.")
        if print_cumulative:
            _print_cumulative_store(cycle, reason="KeyboardInterrupt")

    # ── Post-loop cleanup (try/finally ensures stdout/stderr always restored) ──
    # Any exception in the final-write block must NOT leave sys.stdout/stderr
    # permanently redirected to /dev/null.  The finally clause guarantees
    # restoration even if write_xlsx, write_veusz, or any other writer raises.
    try:
        # ── Final flush: wait for any in-flight background flush ────────────────
        if flush_future is not None and not flush_future.done():
            if not dicts_only:
                logger.info("Waiting for background flush to complete…")
            try:
                flush_future.result(timeout=60)
            except Exception as exc:
                if not dicts_only:
                    logger.error("Background flush error on exit: %s", exc)

        if not dicts_only:
            logger.info(
                "Headless loop ended after %d cycle(s). Writing final outputs…", cycle)

        # Final full write of all enabled formats (including Veusz with all samples).
        # Honour cfg['append_files'] consistently across every writer here.
        fits_dir  = cfg.get("fits_dir",  FITS_DIR)
        csv_dir   = cfg.get("csv_dir",   CSV_DIR)
        xlsx_dir  = cfg.get("xlsx_dir",  XLSX_DIR)
        log_dir   = cfg.get("log_dir",   LOG_DIR)
        _app_f    = bool(cfg.get("append_files", APPEND_OUTPUT_FILES))

        if cfg.get("enable_fits"):
            write_fits(ALL_DEVICE_DATA, fits_dir, append=_app_f)
        if cfg.get("enable_csv"):
            write_csv(ALL_DEVICE_DATA, csv_dir, append=_app_f)
        if cfg.get("enable_xlsx"):
            write_xlsx(ALL_DEVICE_DATA, xlsx_dir, append=_app_f)
        if cfg.get("enable_log_append"):
            write_log_text(ALL_DEVICE_DATA, log_dir, append=_app_f)
        if cfg.get("enable_veusz"):
            # Use a timestamped filename if any RAM flush fired during this run
            # (store is partial — covers only post-last-flush window).  Use the
            # fixed canonical name only when no flush occurred (full-run data).
            _end_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S") \
                if _VEUSZ_FLUSH_COUNT > 0 else None
            _label = f"flush window {_VEUSZ_FLUSH_COUNT + 1}" \
                if _end_ts else "full run"
            logger.info(
                "Building Veusz project (%s, %d cycle(s))…", _label, cycle)
            write_veusz(ALL_DEVICE_DATA, veusz_dir, append=_app_f,
                        timestamp_suffix=_end_ts)

        if not dicts_only:
            if _any_output_enabled(cfg):
                logger.info("All outputs written. Output directory: %s",
                            cfg.get("output_base_dir", OUTPUT_BASE_DIR))
            else:
                logger.info("No File Output Selected, Nothing Generated")

        # Clean up any leftover stop-signal file so the next run does not exit
        # immediately (handles the case where the file was created externally but
        # the loop had already exited via a different path before detecting it).
        try:
            if os.path.exists(STOP_SIGNAL_FILE):
                os.remove(STOP_SIGNAL_FILE)
                logger.debug("Removed residual stop-signal file on clean exit.")
        except OSError:
            pass

        # ── End-of-loop console output ──────────────────────────────────────────
        # Print the final snapshot dicts only when NOT in silent mode.
        # Previously this fired unconditionally, wasting CPU emitting output
        # into /dev/null and confusing callers expecting total silence.
        if not silent and not dicts_only:
            _print_named_dicts(cycle, label="final snapshot")

        # Print cumulative store at end-of-loop when switch 3 is enabled
        # (unless it was already printed by a stop/flush trigger earlier).
        if print_cumulative:
            _print_cumulative_store(cycle, reason="end of loop")

    finally:
        # Restore stdout / stderr if they were redirected for silent mode.
        # Placed in finally so that any exception during final writes still
        # leaves the interpreter in a clean state when control returns to
        # a caller that imported the module.
        if silent:
            sys.stdout = sys.__stdout__
            sys.stderr = sys.__stderr__
            if _devnull_stdout is not None:
                try:
                    _devnull_stdout.close()
                except OSError:
                    pass
            if _devnull_stderr is not None:
                try:
                    _devnull_stderr.close()
                except OSError:
                    pass

    # Release large in-memory structures accumulated over the run so that
    # callers who import and invoke main() do not retain MBs of stale data
    # in module globals after the function returns.  TIME_SERIES_STORE is
    # still referenced by the return value in main() — only collect cycles.
    gc.collect()


# ===========================================================================
# %% MAIN ENTRY POINT
# ===========================================================================
def main() -> Dict[str, Dict[str, Dict]]:
    """
    Application entry point.

    Reads module-level switch variables, optionally launches the GUI
    or runs a headless polling loop, then returns all device dicts so
    callers can do::

        from ab_power_meter_monitor import main
        results = main()   # blocks until loop ends / GUI closes

        # iterate all devices
        for ip, tables in results.items():
            print(ip, tables["Real_Time_Power_Table"])

        # address a specific device + table directly
        results["10.16.130.51"]["Voltage_Current_Table"]

    Return shape (TIME_SERIES_STORE — full accumulated time-series)::

        {
            "10.16.130.50": {
                "Real_Time_Power_Table": {
                    "timestamps_local": ["2026-05-13 12:00:00", ...],  # one per sample
                    "columns": {"L1 Voltage": [120.1, ...], ...},
                    "units":   {"L1 Voltage": "V", ...},
                },
                ... # all tables that returned numeric data
            },
            "10.16.130.51": { ... },
        }

    When HEADLESS_LOOP_COUNT=1, the store contains exactly one poll cycle.
    When run directly (``python ab_power_meter_monitor.py``) the return
    value is discarded by the ``if __name__ == "__main__"`` block.
    """
    # Build runtime config dict from module-level switches.
    # All derived output directories are populated here from OUTPUT_BASE_DIR.
    # NOTE: headless loop/console switches (HEADLESS_LOOP_COUNT, HEADLESS_SILENT,
    # etc.) are intentionally read directly from module globals inside
    # run_headless() so that caller overrides (abm.HEADLESS_LOOP_COUNT = 1)
    # are always honoured.  The copies in cfg below are kept only for logging.
    cfg: Dict[str, Any] = {
        "ip_list":           list(IP_LIST),   # explicit device IP list
        "sample_period":          SAMPLE_PERIOD_SEC,
        "headless_loop_count":          HEADLESS_LOOP_COUNT,
        "mem_flush_threshold_mb":        MEM_FLUSH_THRESHOLD_MB,
        "mem_free_min_mb":               MEM_FREE_MIN_MB,
        "headless_console_dicts_only":   HEADLESS_CONSOLE_DICTS_ONLY,
        "headless_print_each_sample":    HEADLESS_PRINT_EACH_SAMPLE,
        "headless_print_cumulative":     HEADLESS_PRINT_CUMULATIVE,
        "headless_silent":               HEADLESS_SILENT,
        # used by main() branch logic only; not shown in GUI
        "enable_gui":        ENABLE_GUI,
        "enable_fits":       ENABLE_FITS,
        "enable_csv":        ENABLE_CSV,
        "enable_xlsx":       ENABLE_XLSX,
        "enable_log_append": ENABLE_LOG_APPEND,
        "enable_log_file":   ENABLE_LOG_FILE,
        "enable_veusz":      ENABLE_VEUSZ,   # write Veusz HDF5 project file(s)
        "veusz_write_on_flush": VEUSZ_WRITE_ON_FLUSH,  # 1=also write on RAM flush
        # Output directories — all derived from OUTPUT_BASE_DIR.
        # Change OUTPUT_BASE_DIR at the top of the file to relocate everything.
        "output_base_dir":   OUTPUT_BASE_DIR,
        "fits_dir":          FITS_DIR,
        "csv_dir":           CSV_DIR,
        "xlsx_dir":          XLSX_DIR,
        "veusz_dir":         VEUSZ_DIR,
        "log_dir":                  LOG_DIR,
        "http_retry_count":         HTTP_RETRY_COUNT,
        "http_retry_delay_sec":     HTTP_RETRY_DELAY_SEC,
        "headless_max_consec_fails":HEADLESS_MAX_CONSEC_FAILS,
        "mem_ram_pct_limit":        MEM_RAM_PCT_LIMIT,
        "append_files":             APPEND_OUTPUT_FILES,
    }

    # Create only the output directories that are actually needed.
    # If every file-output switch is disabled (including the log file),
    # no directories are created on disk at all — the output tree stays
    # completely absent until at least one output is enabled.
    if _any_output_enabled(cfg):
        # Always create the base dir when any output is active.
        os.makedirs(cfg["output_base_dir"], exist_ok=True)
        # Sub-dirs — only when their corresponding output is enabled.
        if cfg.get("enable_fits"):
            os.makedirs(cfg["fits_dir"],  exist_ok=True)
        if cfg.get("enable_csv"):
            os.makedirs(cfg["csv_dir"],   exist_ok=True)
        if cfg.get("enable_xlsx"):
            os.makedirs(cfg["xlsx_dir"],  exist_ok=True)
        if cfg.get("enable_veusz"):
            os.makedirs(cfg["veusz_dir"], exist_ok=True)
        # logs/ dir is handled by _setup_logging() which already gates
        # makedirs behind enable_log_file.  write_log_text() (Markdown
        # tables) also calls makedirs only when invoked, which only
        # happens when enable_log_append is set in _do_flush / run_headless.
        if cfg.get("enable_log_append") or cfg.get("enable_log_file"):
            os.makedirs(cfg["log_dir"],   exist_ok=True)

    # Re-initialise logger now that cfg is finalised.
    # The module-scope logger was created console-only (no FileHandler) to
    # avoid touching disk on import.  Here we attach the FileHandler — and
    # create logs/ — only when ENABLE_LOG_FILE is actually on.
    global logger  # noqa: PLW0603  (intentional module-global re-bind)
    logger = _setup_logging(
        cfg["log_dir"],
        append=bool(cfg.get("enable_log_append", ENABLE_LOG_APPEND)),
        enable_log_file=bool(cfg.get("enable_log_file", ENABLE_LOG_FILE)),
    )

    logger.info("AB Power Meter Monitor starting up.")
    logger.info("Configuration: %s", json.dumps(cfg, indent=2))

    if ENABLE_GUI:
        # Pre-compute an initial (empty) figure set; GUI will refresh on first poll
        initial_figs: List[Any] = []
        try:
            launch_gui(initial_switches=cfg, initial_figures=initial_figs)
        except SystemExit:
            # launch_gui ends with sys.exit(app.exec()) — catch SystemExit so
            # control returns here and TIME_SERIES_STORE can be returned to
            # any caller that imported and called main() directly.
            pass
        except Exception as exc:
            logger.critical("GUI launch failed: %s\n%s",
                            exc, traceback.format_exc())
            logger.info("Falling back to headless mode.")
            run_headless(cfg)
    else:
        run_headless(cfg)

    # Return the full accumulated TIME_SERIES_STORE so callers that import
    # and invoke main() directly receive the complete multi-sample time-series.
    # Structure: {ip: {table_name: {"timestamps_local": [...], "columns": {...}, "units": {...}}}}
    # This is the same store that every output file (CSV, XLSX, FITS, Veusz) reads from,
    # so sample counts here always match what was written to disk.
    # When HEADLESS_LOOP_COUNT=1 this contains exactly one poll cycle per device.
    return TIME_SERIES_STORE


if __name__ == "__main__":
    main()
