#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace <wwallace@nrao.edu>
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""GUI soak test: widget count and RSS must stay flat while auto-polling.

===============================================================================
 Title          : AB Power Meter Monitor - GUI memory soak test
 Filename       : tests/gui_soak_test.py
 Version        : 0.0.2 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu
 Created        : 2026-10-26
 Modified       : 2026-10-26
 Language       : Python 3.8 through 3.12 (Linux and Windows)
 Requires       : PySide6, qtpy, matplotlib, psutil (+ monitor dependencies)
 Licence        : GPL-3.0-or-later (GNU General Public License v3.0 or later)
 Status         : Internal draft
===============================================================================

Runs the real main window off-screen against the meter emulator with a 1 s
poll period and every output enabled, switches preview tabs, presses "Flush
RAM Now" half-way, then closes the window (which must wait for the final
writes). Prints one line per poll: live Qt widgets and process RSS.

Pass criteria (after a warm-up of 10 polls):
  * live widget count does not grow by more than 20
  * RSS does not grow by more than 60 MB
  * Windows: process handles grow by at most 100, GDI and USER objects by
    at most 50 each (GetGuiResources) - Windows' own leak indicators
  * the window closes only after the worker finished, and outputs exist

Usage:  QT_QPA_PLATFORM=offscreen python tests/gui_soak_test.py [seconds]
        set ABM_SOAK_VISIBLE=1 to watch the real window instead of off-screen

REVISION HISTORY
----------------
0.0.2  2026-10-26  Windows: handles, GDI and USER objects sampled and judged;
                   ABM_SOAK_VISIBLE=1 shows the window.
0.0.1  2026-10-26  First version (monitor 1.6.0). The same probe on 1.5.5
                   showed +1,440 widgets and +255 MB per ~45 s.
===============================================================================
"""

# %% IMPORTS ===================================================================
from __future__ import annotations

import gc
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

if os.environ.get("ABM_SOAK_VISIBLE", "") != "1":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import psutil  # noqa: E402
from ab_meter_emulator import start_emulators  # noqa: E402
import ab_power_meter_monitor as abm  # noqa: E402


def _windows_counts() -> tuple:
    """(handles, GDI objects, USER objects) of this process on Windows, else Nones."""
    if sys.platform != "win32":
        return (None, None, None)
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32")
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    user32.GetGuiResources.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    user32.GetGuiResources.restype = wintypes.DWORD
    process = kernel32.GetCurrentProcess()
    return (psutil.Process().num_handles(), user32.GetGuiResources(process, 0),   # GR_GDIOBJECTS
            user32.GetGuiResources(process, 1))                                    # GR_USEROBJECTS


def main(seconds: float = 120.0) -> int:
    """Soak for ``seconds``; returns a process exit code (0 = pass)."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    start_emulators(["127.0.0.2", "127.0.0.3"], port)
    abm.METER_URL_TEMPLATE = "http://{ip}:%d/{page}" % port
    abm.IP_LIST = ["127.0.0.2", "127.0.0.3"]
    abm.OUTPUT_BASE_DIR = tempfile.mkdtemp(prefix="abm_gui_soak_")
    for flag in ("ENABLE_CSV", "ENABLE_LOG_APPEND", "ENABLE_VEUSZ", "ENABLE_FITS", "ENABLE_XLSX"):
        setattr(abm, flag, 1)
    abm.FULL_WRITE_EVERY_N_CYCLES = 10
    abm.HTTP_TIMEOUT_SEC = 2

    from qtpy import QtCore, QtWidgets
    import logging

    class _ErrorCounter(logging.Handler):
        """Counts ERROR records of the monitor (e.g. failed preview rendering)."""
        def __init__(self) -> None:
            super().__init__(logging.ERROR)
            self.messages = []

        def emit(self, record: logging.LogRecord) -> None:
            self.messages.append(record.getMessage())

    errors = _ErrorCounter()
    logging.getLogger("ABMonitor").addHandler(errors)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = abm.launch_gui(abm.default_config(), run_event_loop=False)
    process = psutil.Process()
    samples = []
    rendered = []

    def on_cycle(_status: dict) -> None:
        gc.collect()
        samples.append((len(app.allWidgets()), process.memory_info().rss / 2 ** 20) + _windows_counts())
        widgets, rss, handles, gdi, user = samples[-1]
        extra = f"  handles={handles:5d}  gdi={gdi:4d}  user={user:4d}" if handles is not None else ""
        print(f"poll {len(samples):4d}: widgets={widgets:5d}  rss={rss:7.1f} MB{extra}", flush=True)
        # The visible preview must hold a real image after every poll.
        key = window._current_key
        if key is not None:
            pixmap = window._tabs[key]["label"].pixmap()
            rendered.append(pixmap is not None and not pixmap.isNull())
        tabs = window._tab_widget
        if tabs.count() > 1:                    # exercise tab switching (pixmap freeing)
            tabs.setCurrentIndex(len(samples) % tabs.count())
        if len(samples) == 20 and key is not None:
            window._open_interactive(key, "soak")          # interactive dialog path
            for dialog in window.findChildren(QtWidgets.QDialog):
                QtCore.QTimer.singleShot(1500, dialog.close)
        if len(samples) == 30:
            window._flush_now()

    window._spin_period.setMinimum(0.5)
    window._spin_period.setValue(1.0)
    window._start()
    window._worker.cycle_done.connect(on_cycle)
    QtCore.QTimer.singleShot(int(seconds * 1000), window.close)   # must wait for final writes
    closed_at = []

    def check_closed() -> None:
        if not window.isVisible() and not closed_at:
            closed_at.append(time.monotonic())
            app.quit()
    poller = QtCore.QTimer()
    poller.timeout.connect(check_closed)
    poller.start(200)
    app.exec() if hasattr(app, "exec") else app.exec_()

    worker_done = window._worker is not None and not window._worker.isRunning()
    files = sum(len(f) for _r, _d, f in os.walk(abm.OUTPUT_BASE_DIR))
    warm = samples[10:] if len(samples) > 20 else samples
    widget_growth = warm[-1][0] - warm[0][0]
    rss_growth = warm[-1][1] - warm[0][1]
    print(f"polls={len(samples)} widget growth={widget_growth} rss growth={rss_growth:.1f} MB "
          f"worker finished={worker_done} files={files} previews rendered "
          f"{sum(rendered)}/{len(rendered)} monitor errors={len(errors.messages)}")
    for message in errors.messages[:5]:
        print("  ERROR:", message)
    win_ok = True
    if warm and warm[0][2] is not None:                      # Windows counters
        handle_growth, gdi_growth, user_growth = (warm[-1][k] - warm[0][k] for k in (2, 3, 4))
        win_ok = handle_growth <= 100 and gdi_growth <= 50 and user_growth <= 50
        print(f"Windows: handle growth={handle_growth} GDI growth={gdi_growth} USER growth={user_growth}")
    ok = (win_ok and widget_growth <= 20 and rss_growth <= 60 and worker_done and files > 0
          and rendered and all(rendered) and not errors.messages)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(float(sys.argv[1]) if len(sys.argv) > 1 else 120.0))
