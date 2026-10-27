#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace <wwallace@nrao.edu>
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Offline tests of ab_grafana_dashboard 0.0.1 against the meter emulator.

===============================================================================
 Title          : AB Grafana dashboard builder - offline tests
 Filename       : tests/test_grafana_dashboard.py
 Version        : 0.0.1 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu
 Created        : 2026-10-27
 Modified       : 2026-10-27
 Language       : Python 3.7 through 3.12
 Requires       : prometheus_client (+ monitor dependencies)
 Licence        : GPL-3.0-or-later (GNU General Public License v3.0 or later)
 Status         : Internal draft
===============================================================================

Checks
------
1  per-ip layout: one row per meter, one time-series panel per unit with a
   selector for exactly that meter and unit, a table for unit-less values,
   unique panel ids, no overlapping panels, wide panels for many traces.
2  repeat layout: one row repeated by $ip, panels match ip=~"$ip".
3  The inventory is the same from the dict (poll), from /metrics text and
   from a --print-json line.
4  Command line writes valid JSON; upload() POSTs to /api/dashboards/db with
   the token from the environment (fake Grafana).
The real rendering was checked with Grafana 12.2 + Prometheus 3.5 (see the
manual, Section "Prometheus and Grafana").

Run:  python tests/test_grafana_dashboard.py     or     pytest tests/

REVISION HISTORY
----------------
0.0.1  2026-10-27  First version (dashboard builder 0.0.1, exporter 0.2.0).
===============================================================================
"""

# %% IMPORTS ===================================================================
from __future__ import annotations

import http.server
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from prometheus_client import CollectorRegistry, generate_latest  # noqa: E402

from ab_meter_emulator import start_emulators  # noqa: E402
import ab_power_meter_monitor as abm  # noqa: E402
import ab_prometheus_exporter as exp  # noqa: E402
import ab_grafana_dashboard as gd  # noqa: E402

HOSTS = ["127.0.0.13", "127.0.0.14"]


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


PORT = _free_port()
start_emulators(HOSTS, PORT)
abm.METER_URL_TEMPLATE = "http://{ip}:%d/{page}" % PORT
SNAP = exp.poll_once(HOSTS, retries=1, timeout=3)


# %% HELPERS ===================================================================
def _overlaps(panels) -> list:
    """Pairs of non-row panels whose grid rectangles overlap."""
    boxes = [(p["id"], p["gridPos"]) for p in panels if p["type"] != "row"]
    bad = []
    for i, (ida, a) in enumerate(boxes):
        for idb, b in boxes[i + 1:]:
            if (a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"]
                    and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]):
                bad.append((ida, idb))
    return bad


# %% TESTS =====================================================================
def test_per_ip_layout() -> None:
    inv = gd.inventory_from_snapshot(SNAP)
    dash = gd.build_dashboard(inv, names={HOSTS[0]: "Maser"})
    panels = dash["panels"]
    ids = [p["id"] for p in panels]
    assert len(ids) == len(set(ids)), "panel ids must be unique"
    assert not _overlaps(panels), _overlaps(panels)
    rows = [p["title"] for p in panels if p["type"] == "row"]
    assert rows == ["Overview", "Meter Maser ({0})".format(HOSTS[0]), "Meter " + HOSTS[1]], rows
    for ip in HOSTS:
        units = [u for u in inv[ip] if u]
        series = [p for p in panels if p["type"] == "timeseries" and 'ip="{0}"'.format(ip) in p["targets"][0]["expr"]]
        assert len(series) == len(units), (ip, len(series), units)
        for unit in units:
            match = [p for p in series if p["targets"][0]["expr"].endswith('unit="{0}"}}'.format(unit))]
            assert len(match) == 1, (ip, unit)
            wide = len(inv[ip][unit]) > gd.WIDE_ABOVE_TRACES
            assert (match[0]["gridPos"]["w"] == 24) == wide
            assert match[0]["options"]["legend"]["placement"] == ("right" if wide else "bottom")
        tables = [p for p in panels if p["type"] == "table" and 'ip="{0}"'.format(ip) in p["targets"][0]["expr"]]
        assert len(tables) == 1 and tables[0]["targets"][0]["expr"].endswith('unit=""}')
    volts = next(p for p in panels if p["title"].startswith("Voltage [V] - Maser"))
    assert volts["fieldConfig"]["defaults"]["unit"] == "volt"
    assert dash["templating"]["list"][0]["current"]["value"] == gd.DEFAULT_DS_UID
    json.dumps(dash)


def test_repeat_layout() -> None:
    dash = gd.build_dashboard(gd.inventory_from_snapshot(SNAP), layout="repeat")
    rows = [p for p in dash["panels"] if p["type"] == "row"]
    assert [r.get("repeat") for r in rows] == [None, "ip"]
    exprs = [p["targets"][0]["expr"] for p in dash["panels"] if p["type"] in ("timeseries", "table")
             and "ab_meter_value" in p["targets"][0]["expr"]]
    assert exprs and all('ip=~"$ip"' in e for e in exprs), exprs[:2]
    names = [v["name"] for v in dash["templating"]["list"]]
    assert names == ["datasource", "job", "ip"], names
    assert not _overlaps(dash["panels"])


def test_inventory_sources_agree() -> None:
    from_dict = gd.inventory_from_snapshot(SNAP)
    registry = CollectorRegistry()
    registry.register(exp.ABMeterCollector(HOSTS, stale_after=0))
    text = generate_latest(registry).decode()
    from prometheus_client.parser import text_string_to_metric_families
    labels = [s.labels for f in text_string_to_metric_families(text) if f.name == gd.METRIC for s in f.samples]
    from_text = gd.inventory_from_labels(labels)
    norm = lambda inv: {ip: {u: sorted(p) for u, p in units.items()} for ip, units in inv.items()}  # noqa: E731
    assert norm(from_dict) == norm(from_text)
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
        handle.write(json.dumps(SNAP) + "\n")
    try:
        assert norm(gd.inventory_from_json(handle.name)) == norm(from_dict)
    finally:
        os.remove(handle.name)


class _FakeGrafana(http.server.BaseHTTPRequestHandler):
    """Accepts POST /api/dashboards/db like Grafana and records it."""
    received: list = []

    def do_POST(self):  # noqa: N802 - http.server interface
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode())
        _FakeGrafana.received.append((self.path, self.headers.get("Authorization"), body))
        reply = json.dumps({"status": "success", "url": "/d/" + body["dashboard"]["uid"]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *args):
        pass


def test_cli_and_upload() -> None:
    out = os.path.join(tempfile.mkdtemp(prefix="abgd_"), "dash.json")
    server = http.server.HTTPServer(("127.0.0.1", 0), _FakeGrafana)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = dict(os.environ, GRAFANA_TOKEN="glsa_test_token")
    try:
        proc = subprocess.run(
            [sys.executable, str(HERE.parent / "ab_grafana_dashboard.py"), "--ips", ",".join(HOSTS),
             "--url-template", "http://{ip}:%d/{page}" % PORT, "--out", out, "--uid", "t-uid",
             "--grafana-url", "http://127.0.0.1:%d" % server.server_address[1]],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, timeout=180)
        assert proc.returncode == 0, proc.stderr[-800:]
        with open(out, encoding="utf-8") as handle:
            dash = json.load(handle)
        assert dash["uid"] == "t-uid" and len(dash["panels"]) > 10
        path, auth, body = _FakeGrafana.received[-1]
        assert path == "/api/dashboards/db" and auth == "Bearer glsa_test_token"
        assert body["overwrite"] is True and body["dashboard"]["uid"] == "t-uid"
        assert "glsa_test_token" not in proc.stderr + proc.stdout
    finally:
        server.shutdown()


# %% RUN AS A SCRIPT ===========================================================
if __name__ == "__main__":
    for test in (test_per_ip_layout, test_repeat_layout, test_inventory_sources_agree, test_cli_and_upload):
        test()
        print("PASS", test.__name__)
