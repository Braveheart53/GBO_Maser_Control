#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace <wwallace@nrao.edu>
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""How much memory and time the interactive plots of one meter cost.

===============================================================================
 Title          : AB Power Meter Monitor - live plot memory / time check
 Filename       : tests/live_plot_memory_check.py
 Version        : 0.0.1 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu
 Created        : 2026-10-27
 Modified       : 2026-10-27
 Language       : Python 3.7 through 3.12
 Requires       : PySide6, qtpy, pyqtgraph, psutil (+ monitor dependencies)
 Licence        : GPL-3.0-or-later (GNU General Public License v3.0 or later)
 Status         : Internal draft - a measurement, not a pass/fail test
===============================================================================

One poll of an emulated meter gives the real table layout; the store is then
filled with ``rows`` synthetic rows per table (30 s apart). For each size it
prints: store estimate; the time of the first MeterPanel.refresh() (plots
created) and of the next one (update in place = what the GUI thread does
after every poll); the Python/numpy memory the filled panel holds (tracemalloc
- exact for the arrays) and what remains after release() (a hidden tab); and
the process RSS added (noisy: the allocator keeps freed memory, Qt's own
allocations are only in RSS). A throw-away panel is built first so one-time
Qt/pyqtgraph start-up costs are not counted.

Usage:  python tests/live_plot_memory_check.py [rows ...]      (default 1000 10000 50000)
        set QT_QPA_PLATFORM=offscreen to run without a display

REVISION HISTORY
----------------
0.0.1  2026-10-27  First version (monitor 1.7.0, ab_live_plots 0.1.0).
===============================================================================
"""

# %% IMPORTS ===================================================================
from __future__ import annotations

import gc
import os
import tracemalloc
import socket
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import psutil  # noqa: E402

from ab_meter_emulator import start_emulators  # noqa: E402
import ab_power_meter_monitor as abm  # noqa: E402

IP = "127.0.0.61"


def _rss_mb() -> float:
    gc.collect()
    return psutil.Process().memory_info().rss / 2.0 ** 20


def _fill(rows: int) -> None:
    """Replace the meter's store by ``rows`` synthetic rows per table (same layout)."""
    rng = np.random.default_rng(1) if hasattr(np.random, "default_rng") else None
    start = time.time() - rows * 30.0
    with abm._TS_LOCK:
        for tname, tstore in abm.TIME_SERIES_STORE[IP].items():
            base = {p: (v[-1] if v and isinstance(v[-1], (int, float)) else 0.0)
                    for p, v in tstore["columns"].items()}
            noise = (rng.standard_normal(rows) if rng is not None else np.random.randn(rows))
            tstore["timestamps_local"] = ["2026-10-27 00:00:00"] * rows
            tstore["columns"] = {p: list(float(b) * (1.0 + 0.01 * noise)) for p, b in base.items()}
            abm._TS_META[IP][tname] = {"seq": [0] * rows,
                                       "epoch": list(start + 30.0 * np.arange(rows))}


def main(sizes) -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    start_emulators([IP], port)
    abm.METER_URL_TEMPLATE = "http://{ip}:%d/{page}" % port
    abm.set_console_logging(False)
    cfg = abm.default_config()
    cfg["ip_list"] = [IP]
    for key in ("enable_csv", "enable_log_append", "enable_veusz", "enable_fits", "enable_xlsx",
                "enable_log_file", "enable_ram_flush"):
        cfg[key] = 0
    abm.poll_cycle(cfg)
    from qtpy import QtCore, QtWidgets
    import ab_live_plots as live
    import pyqtgraph
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    live.configure()
    print("Python %s, pyqtgraph %s, LIVE_PLOT_MAX_POINTS %d" % (
        sys.version.split()[0], pyqtgraph.__version__, abm.LIVE_PLOT_MAX_POINTS))
    def _spin(ms: int = 50) -> None:
        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(ms, loop.quit)
        loop.exec() if hasattr(loop, "exec") else loop.exec_()

    warm = live.MeterPanel(IP, abm)              # one-time start-up costs
    warm.show()
    warm.refresh()
    _spin()
    warm.clear_plots()
    warm.deleteLater()
    _spin()
    print("| rows/table | store est. MB | traces | 1st refresh s | next refresh s "
          "| panel arrays MB | after release MB | RSS added MB |")
    print("|---:|---:|---:|---:|---:|---:|---:|---:|")
    for rows in sizes:
        _fill(int(rows))
        store_mb = abm.ts_store_size_estimate_mb()
        base = _rss_mb()
        tracemalloc.start()
        traced0 = tracemalloc.get_traced_memory()[0]
        panel = live.MeterPanel(IP, abm, max_points=abm.LIVE_PLOT_MAX_POINTS)
        panel.resize(1400, 900)
        panel.show()
        app.processEvents()
        started = time.perf_counter()
        traces = panel.refresh()
        first_s = time.perf_counter() - started
        app.processEvents()
        started = time.perf_counter()
        panel.refresh()
        next_s = time.perf_counter() - started
        _spin()
        gc.collect()
        held = tracemalloc.get_traced_memory()[0] - traced0
        filled = _rss_mb()
        panel.release()
        _spin()
        gc.collect()
        after = tracemalloc.get_traced_memory()[0] - traced0
        tracemalloc.stop()
        print("| {0:,} | {1:.1f} | {2} | {3:.2f} | {4:.2f} | {5:.1f} | {6:.1f} | {7:+.1f} |".format(
            int(rows), store_mb, traces, first_s, next_s, held / 2.0 ** 20, after / 2.0 ** 20,
            filled - base), flush=True)
        panel.clear_plots()
        panel.deleteLater()
        _spin()
    return 0


if __name__ == "__main__":
    sys.exit(main([int(a) for a in sys.argv[1:]] or [1000, 10000, 50000]))
