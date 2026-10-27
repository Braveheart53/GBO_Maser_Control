#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace <wwallace@nrao.edu>
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Data of each meter stays with that meter, end to end (monitor 1.6.5).

===============================================================================
 Title          : AB Power Meter Monitor - per-meter separation tests
 Filename       : tests/test_ip_separation.py
 Version        : 0.0.1 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu
 Created        : 2026-10-27
 Modified       : 2026-10-27
 Language       : Python 3.7 through 3.12
 Requires       : the monitor's dependencies (numpy, h5py, astropy, openpyxl),
                  prometheus_client for the exporter check
 Licence        : GPL-3.0-or-later (GNU General Public License v3.0 or later)
 Status         : Internal draft
===============================================================================

Why
---
With the real meters the GUI plots of 10.16.130.50 and 10.16.130.54 looked
alike. The 0.0.1 emulator served the SAME values to every host (phase shift
only), so no earlier test could have noticed a mix-up. Emulator 0.0.2 makes
every host distinguishable: values x (1 + 0.15 x host index), its own IP on
page 1, its own serial number.

Checks
------
1  Pages of one meter are fetched strictly one at a time, in page order;
   different meters are fetched at the same time (parallel).
2  After three polls with EVERY output on, each meter's values - in the
   store, the GUI preview series, CSV, Markdown, .vszh5, FITS and XLSX - lie
   in that meter's own band, and the IP octets recorded with them are that
   meter's address. Files exist for exactly the polled meters.
3  The exporter's dict keeps the meters apart the same way.
4  A meter that reports another address than the one polled is warned
   about once (rate limited) and listed in the poll meta "reported_ips".

Run:  python tests/test_ip_separation.py     or     pytest tests/

REVISION HISTORY
----------------
0.0.1  2026-10-27  First version (monitor 1.6.5, emulator 0.0.2).
===============================================================================
"""

# %% IMPORTS ===================================================================
from __future__ import annotations

import csv
import logging
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import h5py  # noqa: E402
import openpyxl  # noqa: E402
from astropy.io import fits  # noqa: E402

from ab_meter_emulator import host_scale, start_emulators  # noqa: E402
import ab_power_meter_monitor as abm  # noqa: E402

HOSTS = ["127.0.0.21", "127.0.0.22"]          # host index 0 and 1 (scale 1.00 and 1.15)
L1_BASE, L1_DRIFT = 120.0, 5.0                 # emulator page 2, "L1 Current"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


PORT = _free_port()
start_emulators(HOSTS, PORT, slow_ms=25)       # latency makes the two meters overlap in time


def _band(ip: str):
    """(low, high) of "L1 Current" for this meter."""
    scale = host_scale(HOSTS.index(ip))
    return (L1_BASE - L1_DRIFT) * scale - 1e-6, (L1_BASE + L1_DRIFT) * scale + 1e-6


def _in_band(ip: str, values) -> bool:
    low, high = _band(ip)
    values = [float(v) for v in values if v is not None and str(v) not in ("", "nan")]
    return bool(values) and all(low <= v <= high for v in values)


def _reset(folder: str) -> dict:
    """Fresh monitor state, every output on, writing to ``folder``."""
    with abm._TS_LOCK:
        abm.TIME_SERIES_STORE.clear()
        abm._TS_META.clear()
    with abm._CURSOR_LOCK:
        abm._CURSORS.clear()
    abm._INFLIGHT.clear()
    abm._MD_HEADER_CACHE.clear()
    abm._RUN_STATE["active"] = False
    abm.METER_URL_TEMPLATE = "http://{ip}:%d/{page}" % PORT
    abm.IP_LIST = list(HOSTS)
    abm.HTTP_TIMEOUT_SEC, abm.HTTP_RETRY_COUNT = 3, 1
    abm.ENABLE_RAM_FLUSH = 0
    abm.FULL_WRITE_EVERY_N_CYCLES = 1
    for flag in ("ENABLE_CSV", "ENABLE_LOG_APPEND", "ENABLE_VEUSZ", "ENABLE_FITS", "ENABLE_XLSX"):
        setattr(abm, flag, 1)
    abm.OUTPUT_BASE_DIR = folder
    logging.getLogger("ABMonitor").setLevel(logging.WARNING)
    return abm.default_config()


# %% TESTS =====================================================================
def test_pages_serial_per_meter_parallel_across_meters() -> None:
    _reset(tempfile.mkdtemp(prefix="absep_t1_"))
    real_fetch = abm.fetch_table_html
    lock = threading.Lock()
    active = {ip: 0 for ip in HOSTS}
    worst = {ip: 0 for ip in HOSTS}
    overlap = [0]
    order = {ip: [] for ip in HOSTS}

    def tracked(ip, page, **kwargs):
        with lock:
            active[ip] += 1
            worst[ip] = max(worst[ip], active[ip])
            overlap[0] = max(overlap[0], sum(active.values()))
            order[ip].append(page)
        try:
            return real_fetch(ip, page, **kwargs)
        finally:
            with lock:
                active[ip] -= 1

    abm.fetch_table_html = tracked
    try:
        data = abm.poll_all_devices(HOSTS, abm.TABLE_NAMES, retries=1, timeout=3)
    finally:
        abm.fetch_table_html = real_fetch
    assert data["__poll_meta__"]["ok_ips"] == HOSTS
    assert worst == {ip: 1 for ip in HOSTS}, "two pages of one meter in flight: %s" % worst
    assert overlap[0] == 2, "meters were not polled in parallel"
    assert all(order[ip] == sorted(abm.TABLE_NAMES) for ip in HOSTS), order


def test_every_output_keeps_its_own_meter() -> str:
    folder = tempfile.mkdtemp(prefix="absep_t2_")
    cfg = _reset(folder)
    abm.begin_run(cfg)
    for _ in range(3):
        assert abm.poll_cycle(cfg)["ok_ips"] == HOSTS
    abm.end_run(cfg)
    for ip in HOSTS:
        tag = abm._safe_ip(ip)
        last = int(ip.split(".")[-1])
        # store and GUI series
        store = abm.TIME_SERIES_STORE[ip]
        assert _in_band(ip, store["Voltage_Current_Table"]["columns"]["L1 Current"]), ip
        assert set(store["Communications_Configuration_Table"]["columns"]["IP Address Octet 4"]) == {last}
        series = {label: y for label, _x, y in abm.preview_series(ip, "Voltage_Current_Table")}
        assert _in_band(ip, list(series["L1 Current (A)"])), ip
        # CSV
        with open(os.path.join(folder, "csv", "ABMeter_%s_Voltage_Current_Table.csv" % tag),
                  newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        col = rows[0].index("L1 Current")
        assert len(rows) - 2 == 3 and _in_band(ip, [r[col] for r in rows[2:]]), (ip, rows[2:])
        with open(os.path.join(folder, "csv", "ABMeter_%s_Communications_Configuration_Table.csv" % tag),
                  newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        col = rows[0].index("IP Address Octet 4")
        assert {float(r[col]) for r in rows[2:]} == {last}, (ip, rows[2:])
        # Markdown
        with open(os.path.join(folder, "logs", "ABMeter_%s_Voltage_Current_Table.md" % tag),
                  encoding="utf-8") as handle:
            lines = [l for l in handle if l.startswith("|")]
        header = [c.strip() for c in lines[0].strip().strip("|").split("|")]
        body = [[c.strip() for c in l.strip().strip("|").split("|")] for l in lines if l.startswith("| 20")]
        col = header.index("L1 Current (A)")                 # Markdown headers carry the unit
        assert len(body) == 3 and _in_band(ip, [r[col] for r in body]), ip
        assert lines[0] and "## %s" % ip in open(os.path.join(folder, "logs", "ABMeter_%s_Voltage_Current_Table.md"
                                                               % tag), encoding="utf-8").read()
        # .vszh5
        with h5py.File(os.path.join(folder, "veusz", "ABMeter_%s.vszh5" % tag), "r") as hdf:
            data = hdf["Veusz/Data"]
            assert _in_band(ip, data["%d_Voltage_Current_Table_L1_Current/data" % last][:]), ip
            octet = data["%d_Communications_Configuration_Table_IP_Address_Octet_4/data" % last][:]
            assert set(octet.tolist()) == {float(last)}, (ip, octet)
            others = [n for n in data if not n.startswith("%d_" % last) and "_Table_" in n]
            assert not others, "datasets of another meter in %s: %s" % (ip, others[:3])
        # FITS
        with fits.open(os.path.join(folder, "fits", "ABMeter_%s.fits" % tag)) as hdul:
            assert _in_band(ip, hdul["Voltage_Current_Table"].data["L1 Current"]), ip
            assert set(hdul["Communications_Configuration_Table"].data["IP Address Octet 4"]) == {last}
        # XLSX
        book = openpyxl.load_workbook(os.path.join(folder, "xlsx", "ABMeter_%s.xlsx" % tag), read_only=True)
        try:
            rows = list(book["Voltage_Current_Table"].iter_rows(values_only=True))
            col = list(rows[0]).index("L1 Current")
            assert _in_band(ip, [r[col] for r in rows[2:]]), ip
        finally:
            book.close()
    for sub, suffix in (("veusz", ".vszh5"), ("fits", ".fits"), ("xlsx", ".xlsx")):
        files = sorted(f for f in os.listdir(os.path.join(folder, sub)) if f.endswith(suffix))
        assert len(files) == len(HOSTS), files
    low0, high0 = _band(HOSTS[0])
    low1, high1 = _band(HOSTS[1])
    assert high0 < low1, "emulator bands must not overlap for this test to mean anything"
    return folder


def test_exporter_keeps_meters_apart() -> None:
    import ab_prometheus_exporter as exp
    abm.METER_URL_TEMPLATE = "http://{ip}:%d/{page}" % PORT
    snap = exp.poll_once(HOSTS, retries=1, timeout=3)
    for ip in HOSTS:
        values = [v for k, v in snap["by_unit"][ip]["A"].items() if k.endswith("/ L1 Current")
                  and k.startswith("Voltage_Current_Table")]
        assert _in_band(ip, values), (ip, values)
        comm = snap["meters"][ip]["tables"]["Communications_Configuration_Table"]["values"]
        assert comm["IP Address Octet 4"][0] == float(ip.split(".")[-1])


def test_reported_ip_mismatch_is_warned() -> None:
    port = _free_port()
    start_emulators(["127.0.0.23"], port, report_ip="127.0.0.21", first_index=2)
    abm.METER_URL_TEMPLATE = "http://{ip}:%d/{page}" % port
    abm._IDENTITY_WARNED.clear()
    records = []

    class _Grab(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    grab = _Grab(level=logging.WARNING)
    logging.getLogger("ABMonitor").addHandler(grab)
    try:
        for _ in range(2):
            meta = abm.poll_all_devices(["127.0.0.23"], abm.TABLE_NAMES, retries=1, timeout=3)["__poll_meta__"]
    finally:
        logging.getLogger("ABMonitor").removeHandler(grab)
    assert meta["reported_ips"] == {"127.0.0.23": "127.0.0.21"}, meta
    warned = [r for r in records if "reports its address as 127.0.0.21" in r]
    assert len(warned) == 1, records
    assert abm.reported_ip({"#1": "IP Address Octet 1", "#1_value": 10,
                            "#2": "Gateway IP Address Octet 1", "#2_value": 1}) is None


# %% RUN AS A SCRIPT ===========================================================
if __name__ == "__main__":
    started = time.monotonic()
    for test in (test_pages_serial_per_meter_parallel_across_meters, test_every_output_keeps_its_own_meter,
                 test_exporter_keeps_meters_apart, test_reported_ip_mismatch_is_warned):
        test()
        print("PASS", test.__name__)
    print("all tests passed in %.1f s" % (time.monotonic() - started))
