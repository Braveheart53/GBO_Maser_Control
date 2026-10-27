#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace <wwallace@nrao.edu>
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Offline tests of ab_prometheus_exporter 0.1.0 against the meter emulator.

===============================================================================
 Title          : AB Prometheus exporter - offline tests
 Filename       : tests/test_exporter_offline.py
 Version        : 0.0.2 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu
 Created        : 2026-10-26
 Modified       : 2026-10-26
 Language       : Python 3.8 through 3.12
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

REVISION HISTORY
----------------
0.0.2  2026-10-26  Windows: the process test starts the exporter in its own
                   process group and stops it with CTRL_BREAK_EVENT. Under
                   Wine (no console control events) the clean-stop check is
                   reported as skipped.
0.0.1  2026-10-26  First version (exporter 0.1.0).
===============================================================================
"""

# %% IMPORTS ===================================================================
from __future__ import annotations

import re
import signal
import socket
import subprocess
import sys
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


if __name__ == "__main__":
    for test in (test_collector_semantics, test_timestamps_option, test_exporter_process):
        test()
        print("PASS", test.__name__)
