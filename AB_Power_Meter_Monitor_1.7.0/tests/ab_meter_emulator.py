#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace <wwallace@nrao.edu>
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Offline emulator of the Allen-Bradley 1403 power-meter web tables.

===============================================================================
 Title          : AB Power Meter Web-Table Emulator (test fixture)
 Filename       : tests/ab_meter_emulator.py
 Version        : 0.0.2 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu
 Created        : 2026-10-26
 Modified       : 2026-10-27
 Language       : Python 3.8 through 3.12 (standard library only)
 Requires       : nothing beyond the standard library
 Licence        : GPL-3.0-or-later (GNU General Public License v3.0 or later)
 Status         : Internal draft - test fixture, never used in production
===============================================================================

DESCRIPTION
-----------
Serves the 11 table pages (http://<host>:<port>/<page>, page 0 ... 10) that the
monitor polls, in the same shape as the real meter: one <table> whose rows are
``# | Parameter Name | Value``.  Values drift slowly with time so successive
polls differ, some values are strings ("####", dates) exactly as on the real
meter, and the Diagnostic table deliberately repeats a parameter name
("Reserved") so the duplicate-name handling of the monitor can be tested.

Like the real firmware, each host answers one request at a time (single
threaded HTTP server), so a client that floods it with parallel requests is
penalised the same way the real meter penalises it.

Fault injection (all optional, for the robustness tests):
    --fail-pages 4,5      pages that answer HTTP 500
    --down-after N        the host stops answering after N requests
    --slow-ms M           add M milliseconds of latency to every request
    --report-ip A         page 1 reports address A instead of the host's own
                          (tests the monitor's reported-IP check)

Every host is distinguishable (0.0.2): measured values are scaled by
1 + 0.15 x host index (host 0 = 1.00, host 1 = 1.15, ...), page 1 reports
the host's own address in "IP Address Octet 1" ... "4", and page 0 has a
host-specific "Serial Number". A test can therefore tell from any stored
value or output file which meter it came from.

USAGE
-----
    python tests/ab_meter_emulator.py --hosts 127.0.0.2,127.0.0.3 --port 8080
    # then point the monitor at it:
    #   METER_URL_TEMPLATE = "http://{ip}:8080/{page}"
    #   IP_LIST = ["127.0.0.2", "127.0.0.3"]

REVISION HISTORY
----------------
0.0.2  2026-10-27  Hosts distinguishable: per-host value scale, own IP in
                   page 1 (all four octets), serial number; --report-ip.
                   0.0.1 served the same values to every host (phase-shifted
                   only), so a cross-IP mix-up could not be detected.
0.0.1  2026-10-26  First version, written to reproduce the GUI memory growth
                   and to test monitor 1.6.0 / exporter 0.1.0 offline.
===============================================================================
"""

# %% ===========================================================================
#    IMPORTS
# =============================================================================
from __future__ import annotations

import argparse
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, List, Sequence, Tuple

__version__ = "0.0.2"

# %% ===========================================================================
#    TABLE CONTENT (names follow the monitor's UNIT_MAP / overlay groups)
# =============================================================================
# Each entry: (parameter name, base value or string, drift amplitude).
# A string base value is served verbatim (the monitor keeps strings out of the
# numeric time series, exactly as with the real meter).
_TABLES: Dict[int, List[Tuple[str, object, float]]] = {
    0: [("Catalog Number", "1403-LM", 0.0), ("Series", "B", 0.0), ("Serial Number", "@serial", 0.0),
        ("Firmware Revision", 3.12, 0.0), ("PT Primary", 480.0, 0.0),
        ("CT Primary", 400.0, 0.0), ("Demand Period", 15, 0.0),
        ("Date Set", "10/26/2026", 0.0)],
    1: [("IP Address Octet 1", "@ip1", 0.0), ("IP Address Octet 2", "@ip2", 0.0),
        ("IP Address Octet 3", "@ip3", 0.0), ("IP Address Octet 4", "@ip4", 0.0),
        ("BOOTP Enable", 0, 0.0), ("Baud Rate", 19200, 0.0)],
    2: [("L1 Current", 120.0, 5.0), ("L2 Current", 118.0, 5.0),
        ("L3 Current", 121.0, 5.0), ("L4(Neutral) Current", 4.0, 1.0),
        ("3 Phase Average Current", 119.7, 4.0), ("L1-N Voltage", 277.1, 1.5),
        ("L2-N Voltage", 276.8, 1.5), ("L3-N Voltage", 277.4, 1.5),
        ("3 Phase Average Voltage L-N", 277.1, 1.0), ("L1-L2 Voltage", 480.2, 2.5),
        ("L2-L3 Voltage", 479.6, 2.5), ("L3-L1 Voltage", 480.9, 2.5),
        ("3 Phase Average Voltage L-L", 480.2, 2.0), ("Frequency", 60.0, 0.02),
        ("Pos. Seq. Voltage", 480.0, 2.0), ("Neg. Seq. Voltage", 0.8, 0.2)],
    3: [("L1 Real Power", 33000.0, 900.0), ("L2 Real Power", 32500.0, 900.0),
        ("L3 Real Power", 33300.0, 900.0), ("Total Real Power", 98800.0, 2500.0),
        ("L1 Reactive Power", 4100.0, 300.0), ("Total Reactive Power", 12300.0, 600.0),
        ("L1 Apparent Power", 33300.0, 900.0), ("Total Apparent Power", 99600.0, 2500.0),
        ("L1 True PF", 99.1, 0.3), ("Total True PF", 99.0, 0.3),
        ("L1 Displacement PF", 99.2, 0.2), ("L1 Distortion PF", 99.8, 0.1)],
    4: [("kW Hour Net", 1234567.0, 0.0), ("kVAR Hour Net", 223344.0, 0.0),
        ("kVA Hour Net", 1255000.0, 0.0)],
    5: [("Demand Current", 118.0, 3.0), ("Demand Power", 97000.0, 2000.0),
        ("Demand Apparent Power", 98000.0, 2000.0), ("Projected Demand Power", 99000.0, 2000.0)],
    6: [("Status Word", 0, 0.0), ("Reserved", 0, 0.0), ("Reserved", 1, 0.0),
        ("Watchdog Count", 3, 0.0), ("Elapsed Time", 86400.0, 0.0)],
    7: [("Snapshot Record", 1, 0.0), ("Snapshot L1 Current", 119.0, 4.0),
        ("Snapshot Date", "10/26/2026 04:00:00", 0.0)],
    8: [("Snapshot Record", 1, 0.0), ("Snapshot Total Real Power", 98000.0, 2000.0)],
    9: [("Min L1 Current", 80.0, 0.0), ("Max L1 Current", 160.0, 0.0),
        ("Min Frequency", 59.95, 0.0), ("Max Frequency", 60.05, 0.0), ("Min Date", "####", 0.0)],
    10: [("Error Count", 0, 0.0), ("Last Error Code", 0, 0.0), ("Pulse Width", 50, 0.0)],
}


#: Pages whose numeric readings are scaled per host (measurements, energy,
#: logs); configuration and diagnostic pages are the same on every host.
_SCALED_PAGES = (2, 3, 4, 5, 7, 8, 9)


def host_scale(host_index: int) -> float:
    """Value scale of a host: 1.00, 1.15, 1.30, ... (0.0.2)."""
    return 1.0 + 0.15 * host_index


def _value_text(base: object, drift: float, host_index: int, page: int, row: int,
                reported_ip: str = "") -> str:
    """Current value of one row as the meter would print it (drifting with time)."""
    if isinstance(base, str):
        if base.startswith("@ip") and reported_ip:          # own address, octet n
            octets = (reported_ip.split(".") + ["0"] * 4)[:4]
            return octets[int(base[3]) - 1]
        if base == "@serial":
            return "EMU{0:05d}".format(10000 + host_index)
        return base
    scale = host_scale(host_index) if page in _SCALED_PAGES else 1.0
    if drift == 0.0:
        return str(float(base) * scale) if scale != 1.0 else str(base)
    # Slow deterministic drift: different per host, page and row, so every poll
    # returns a new, plausible value without needing random state.
    phase = time.time() / 37.0 + 1.7 * host_index + 0.31 * page + 0.13 * row
    return f"{(float(base) + drift * math.sin(phase)) * scale:.3f}"


def _page_html(host_index: int, page: int, reported_ip: str = "") -> bytes:
    """HTML body of one table page, in the meter's row layout."""
    rows = ["<tr><th><font>#</font></th><th><font>Parameter Name</font></th>"
            "<th><font>Value</font></th></tr>"]
    for row_number, (name, base, drift) in enumerate(_TABLES.get(page, []), start=1):
        rows.append(f"<tr><td>{row_number}</td><td>{name}</td>"
                    f"<td>{_value_text(base, drift, host_index, page, row_number, reported_ip)}</td></tr>")
    return ("<html><head><title>AB 1403</title></head><body><table>"
            + "".join(rows) + "</table></body></html>").encode("ascii")


# %% ===========================================================================
#    HTTP SERVER
# =============================================================================
class _Emulator(HTTPServer):
    """One emulated meter: remembers its index and the fault-injection settings."""

    def __init__(self, address: Tuple[str, int], host_index: int, fail_pages: Sequence[int],
                 down_after: int, slow_ms: int, report_ip: str = "") -> None:
        super().__init__(address, _Handler)
        self.host_index = host_index
        self.reported_ip = report_ip or address[0]     # page 1 reports this address
        self.fail_pages = set(fail_pages)
        self.down_after = down_after          # 0 = never go down
        self.slow_ms = slow_ms
        self.request_count = 0


class _Handler(BaseHTTPRequestHandler):
    """Answers GET /<page> with the page HTML (HTTP/1.1 keep-alive, like the meter)."""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - name required by BaseHTTPRequestHandler
        server: _Emulator = self.server  # type: ignore[assignment]
        server.request_count += 1
        if server.down_after and server.request_count > server.down_after:
            self.close_connection = True      # emulate a meter that stopped answering
            return
        if server.slow_ms:
            time.sleep(server.slow_ms / 1000.0)
        try:
            page = int(self.path.strip("/") or "0")
        except ValueError:
            page = -1
        if page not in _TABLES or page in server.fail_pages:
            self.send_response(500 if page in server.fail_pages else 404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = _page_html(server.host_index, page, server.reported_ip)
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:  # silence per-request logging
        return


def start_emulators(hosts: Sequence[str], port: int, fail_pages: Sequence[int] = (),
                    down_after: int = 0, slow_ms: int = 0, report_ip: str = "",
                    first_index: int = 0) -> List[_Emulator]:
    """Start one single-threaded emulator per host on daemon threads; return them.

    ``first_index`` numbers the hosts (value scale, serial number) when several
    calls start emulators that should still be distinguishable.
    """
    servers = []
    for index, host in enumerate(hosts, start=first_index):
        server = _Emulator((host, port), index, fail_pages, down_after, slow_ms, report_ip)
        threading.Thread(target=server.serve_forever, name=f"emu_{host}", daemon=True).start()
        servers.append(server)
    return servers


def main() -> None:
    """Command-line entry point: serve until Ctrl-C."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hosts", default="127.0.0.2,127.0.0.3")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--fail-pages", default="")
    parser.add_argument("--down-after", type=int, default=0)
    parser.add_argument("--slow-ms", type=int, default=0)
    parser.add_argument("--report-ip", default="", help="address reported on page 1 (default: own)")
    args = parser.parse_args()
    hosts = [h.strip() for h in args.hosts.split(",") if h.strip()]
    fails = [int(p) for p in args.fail_pages.split(",") if p.strip()]
    start_emulators(hosts, args.port, fails, args.down_after, args.slow_ms, args.report_ip)
    print(f"AB meter emulator serving {hosts} on port {args.port} (Ctrl-C to stop)")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
