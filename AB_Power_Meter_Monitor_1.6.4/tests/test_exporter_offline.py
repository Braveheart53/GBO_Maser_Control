#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace <wwallace@nrao.edu>
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Offline tests of ab_prometheus_exporter 0.2.0 against the meter emulator.

===============================================================================
 Title          : AB Prometheus exporter - offline tests
 Filename       : tests/test_exporter_offline.py
 Version        : 0.0.3 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu
 Created        : 2026-10-26
 Modified       : 2026-10-27
 Language       : Python 3.7 through 3.12 (3.7.12 + prometheus_client 0.17.1 tested)
 Requires       : prometheus_client (+ monitor dependencies)
 Licence        : GPL-3.0-or-later (GNU General Public License v3.0 or later)
 Status         : Internal draft
===============================================================================

Checks
------
1  Values are exported per ip/table/parameter, once each (no duplicate series).
2  When a meter stops answering, ab_meter_up = 0 and NONE of its values are
   exported any more (0.0.x kept re-exporting the last ones).
3  ab_meter_samples_total only increases; table fetch errors are counted.
4  --timestamps attaches the fetch time to every value.
5  The real exporter process serves /metrics, polls, and stops cleanly on
   SIGTERM (POSIX) or Ctrl+Break / CTRL_BREAK_EVENT (Windows, exporter 0.1.1;
   SIGTERM on Windows is TerminateProcess and cannot be handled).
6  poll_once() returns the dict; by_unit groups it per meter and unit.
7  iter_updates()/run() continuous mode: count, on_update callback, JSON-safe.
8  Push to a (fake) Pushgateway: PUT to /metrics/job/<job>/instance/<inst>,
   no timestamps, no counters; a meter that went down is absent from the push.
9  --once CLI: one JSON line on stdout, exit 0; a dead meter gives exit 1, a
   push that fails exit 2; the run leaves NO file behind (working folder and
   program folder unchanged, no __pycache__).

REVISION HISTORY
----------------
0.0.3  2026-10-27  Tests 6-9 for exporter 0.2.0; Python 3.7 supported.
0.0.2  2026-10-26  Windows: the process test starts the exporter in its own
                   process group and stops it with CTRL_BREAK_EVENT. Under
                   Wine (no console control events) the clean-stop check is
                   reported as skipped.
0.0.1  2026-10-26  First version (exporter 0.1.0).
===============================================================================
"""

# %% IMPORTS ===================================================================
from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from prometheus_client import CollectorRegistry, generate_latest  # noqa: E402
from prometheus_client.parser import text_string_to_metric_families  # noqa: E402

from ab_meter_emulator import start_emulators  # noqa: E402
import ab_power_meter_monitor as abm  # noqa: E402
import ab_prometheus_exporter as exp  # noqa: E402


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _scrape(collector) -> dict:
    """{metric family name: [samples]} from one collection."""
    registry = CollectorRegistry()
    registry.register(collector)
    text = generate_latest(registry).decode()
    return {f.name: f.samples for f in text_string_to_metric_families(text)}, text


# %% TESTS =====================================================================
def test_collector_semantics() -> None:
    port = _free_port()
    start_emulators(["127.0.0.4"], port)                       # stays up
    start_emulators(["127.0.0.5"], port, down_after=22)        # 2 polls x 11 pages, then dead
    abm.METER_URL_TEMPLATE = "http://{ip}:%d/{page}" % port
    ips = ["127.0.0.4", "127.0.0.5"]
    collector = exp.ABMeterCollector(ips, stale_after=60.0)
    previous_samples = 0.0
    for poll in range(1, 5):
        data = abm.poll_all_devices(ips, abm.TABLE_NAMES, retries=1, timeout=1.0)
        exp.apply_poll(data, ips)
        families, text = _scrape(collector)
        values = families["ab_meter_value"]
        label_sets = [tuple(sorted(s.labels.items())) for s in values]
        assert len(label_sets) == len(set(label_sets)), "duplicate series"
        exported_ips = {s.labels["ip"] for s in values}
        up = {s.labels["ip"]: s.value for s in families["ab_meter_up"]}
        if poll <= 2:
            assert exported_ips == set(ips) and up == {ip: 1.0 for ip in ips}, (poll, exported_ips, up)
        else:                                                   # .5 is down now
            assert exported_ips == {"127.0.0.4"} and up["127.0.0.5"] == 0.0, (poll, exported_ips, up)
        total = sum(s.value for s in families["ab_meter_samples"] if s.name.endswith("_total"))
        assert total > previous_samples, "samples_total must increase"
        previous_samples = total
        assert 'parameter="Reserved [#3]"' in text
    errors = families["ab_meter_table_fetch_errors"]
    assert any(s.labels["ip"] == "127.0.0.5" and s.value > 0 for s in errors if s.name.endswith("_total"))


def test_timestamps_option() -> None:
    collector = exp.ABMeterCollector(["127.0.0.4"], stale_after=0, expose_timestamps=True)
    _families, text = _scrape(collector)
    line = next(l for l in text.splitlines() if l.startswith("ab_meter_value{"))
    # name{labels} value timestamp_ms  (labels may contain spaces, so match the tail)
    assert re.search(r"\} \S+ \d{13}$", line), line


def test_exporter_process() -> None:
    meter_port, metrics_port = _free_port(), _free_port()
    start_emulators(["127.0.0.6"], meter_port)
    windows = sys.platform == "win32"
    # Windows: a console control event reaches only a process-group leader.
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if windows else 0
    proc = subprocess.Popen(
        [sys.executable, str(HERE.parent / "ab_prometheus_exporter.py"), "--ips", "127.0.0.6",
         "--port", str(metrics_port), "--bind", "127.0.0.1", "--interval", "1",
         "--url-template", "http://{ip}:%d/{page}" % meter_port],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=flags)
    try:
        deadline, body = time.monotonic() + 30, ""
        while time.monotonic() < deadline:
            try:
                body = urllib.request.urlopen(f"http://127.0.0.1:{metrics_port}/metrics",
                                              timeout=2).read().decode()
                if 'ab_meter_value{ip="127.0.0.6"' in body:
                    break
            except OSError:
                pass
            time.sleep(0.5)
        assert 'ab_meter_value{ip="127.0.0.6"' in body, body[:500]
        assert 'ab_meter_up{ip="127.0.0.6"} 1.0' in body
        from windows_memory_check import running_under_wine
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT if windows else signal.SIGTERM)
            output, _ = proc.communicate(timeout=20)
        except (OSError, subprocess.TimeoutExpired) as exc:
            # GenerateConsoleCtrlEvent needs a console shared with the exporter:
            # Wine has none (OSError / no delivery); on Windows run from a console.
            if not (windows and running_under_wine()):
                raise AssertionError(f"Ctrl+Break / SIGTERM stop failed ({exc}) - run the "
                                     "tests from a console window") from exc
            proc.kill()
            output, _ = proc.communicate(timeout=20)
            print("  SKIP clean-stop check: Wine delivers no console control events")
            return
        assert proc.returncode == 0 and "Exporter stopped cleanly" in output, output[-800:]
        assert output.count(f"Exporter {exp.__version__} listening") == 1
    finally:
        if proc.poll() is None:
            proc.kill()


# %% 0.2.0: the dict, continuous / single run, push =============================
def _emulator(host: str) -> int:
    """Start an emulator for ``host`` on a free port and point the monitor at it."""
    port = _free_port()
    start_emulators([host], port)
    abm.METER_URL_TEMPLATE = "http://{ip}:%d/{page}" % port
    return port


def test_poll_once_and_by_unit() -> None:
    _emulator("127.0.0.7")
    snap = exp.poll_once(["127.0.0.7"], retries=1, timeout=3)
    meter = snap["meters"]["127.0.0.7"]
    assert meter["up"] and len(meter["tables"]) == len(abm.TABLE_NAMES), meter.keys()
    volts = snap["by_unit"]["127.0.0.7"]["V"]
    assert volts and all(isinstance(v, float) for v in volts.values())
    assert "Voltage_Current_Table / L1-N Voltage" in volts, sorted(volts)[:5]
    total = sum(len(t["values"]) for t in meter["tables"].values())
    assert total == sum(len(d) for d in snap["by_unit"]["127.0.0.7"].values())
    json.dumps(snap, allow_nan=False)                   # JSON-safe as --print-json writes it


def test_iter_updates_and_run_callback() -> None:
    _emulator("127.0.0.8")
    stamps = [s["epoch"] for s in exp.iter_updates(["127.0.0.8"], interval=0.2, count=3,
                                                    retries=1, timeout=3)]
    assert len(stamps) == 3 and stamps == sorted(stamps), stamps
    seen = []
    code = exp.run(["127.0.0.8"], interval=0.2, count=2, port=None, on_update=seen.append,
                   retries=1, timeout=3)
    assert code == 0 and len(seen) == 2 and seen[-1]["meters"]["127.0.0.8"]["up"]


class _FakeGateway(http.server.BaseHTTPRequestHandler):
    """Records PUT requests the way a Pushgateway receives them."""
    received: list = []

    def do_PUT(self):  # noqa: N802 - http.server interface
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        _FakeGateway.received.append((self.path, body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):                       # keep the test output quiet
        pass


def test_push_to_gateway() -> None:
    _emulator("127.0.0.9")                              # 127.0.0.10 below never answers
    server = http.server.HTTPServer(("127.0.0.1", 0), _FakeGateway)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    gateway = "http://127.0.0.1:%d" % server.server_address[1]
    try:
        _FakeGateway.received.clear()
        exp.poll_once(["127.0.0.9", "127.0.0.10"], retries=1, timeout=2)
        assert exp.push_metrics(gateway, ["127.0.0.9", "127.0.0.10"], job="abtest", instance="hostA")
        path, body = _FakeGateway.received[-1]
        assert path == "/metrics/job/abtest/instance/hostA", path
        assert 'ab_meter_value{ip="127.0.0.9"' in body and 'ab_meter_value{ip="127.0.0.10"' not in body
        assert 'ab_meter_up{ip="127.0.0.10"} 0.0' in body
        assert "_total" not in body, "counters must not be pushed"
        value_lines = [l for l in body.splitlines() if l.startswith("ab_meter_value{")]
        assert value_lines and all(re.search(r"\} \S+$", l) for l in value_lines), "no timestamps"
        assert not exp.push_metrics("http://127.0.0.1:%d" % _free_port(), ["127.0.0.9"], timeout=2)
    finally:
        server.shutdown()


def _snapshot_dir(folder: str) -> set:
    return {os.path.relpath(os.path.join(dp, f), folder) for dp, dn, fn in os.walk(folder) for f in fn + dn}


def test_once_cli_json_exit_codes_no_files() -> None:
    port = _free_port()
    start_emulators(["127.0.0.11"], port)
    work = tempfile.mkdtemp(prefix="abexp_once_")
    prog = os.path.join(work, "prog")                   # copies: their folder is checked too
    os.makedirs(prog)
    for name in ("ab_power_meter_monitor.py", "ab_prometheus_exporter.py"):
        shutil.copy(str(HERE.parent / name), prog)
    run_dir = os.path.join(work, "cwd")
    os.makedirs(run_dir)
    before = _snapshot_dir(work)
    env = dict(os.environ)
    env.pop("PYTHONDONTWRITEBYTECODE", None)            # the exporter itself must prevent .pyc
    base = [sys.executable, os.path.join(prog, "ab_prometheus_exporter.py"), "--once",
            "--url-template", "http://{ip}:%d/{page}" % port, "--retries", "1", "--timeout", "2"]
    try:
        out = subprocess.run(base + ["--ips", "127.0.0.11"], cwd=run_dir, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
        assert out.returncode == 0, out.stderr[-800:]
        lines = [l for l in out.stdout.splitlines() if l.strip()]
        assert len(lines) == 1, out.stdout[:300]
        snap = json.loads(lines[0])
        assert snap["meters"]["127.0.0.11"]["up"] and snap["by_unit"]["127.0.0.11"]["A"]
        dead = subprocess.run(base + ["--ips", "127.0.0.12"], cwd=run_dir, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
        assert dead.returncode == 1 and not json.loads(dead.stdout)["meters"]["127.0.0.12"]["up"]
        nopush = subprocess.run(base + ["--ips", "127.0.0.11", "--push-gateway",
                                        "http://127.0.0.1:%d" % _free_port()], cwd=run_dir, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
        assert nopush.returncode == 2 and nopush.stdout == "", (nopush.returncode, nopush.stdout[:200])
        after = _snapshot_dir(work)
        assert after == before, sorted(after - before)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    for test in (test_collector_semantics, test_timestamps_option, test_exporter_process,
                 test_poll_once_and_by_unit, test_iter_updates_and_run_callback,
                 test_push_to_gateway, test_once_cli_json_exit_codes_no_files):
        test()
        print("PASS", test.__name__)
