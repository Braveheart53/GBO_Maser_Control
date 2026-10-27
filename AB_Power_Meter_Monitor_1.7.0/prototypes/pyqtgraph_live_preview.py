#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace <wwallace@nrao.edu>
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Stand-alone live viewer of the AB meters (no files written) - thin wrapper.

===============================================================================
 Title          : AB Power Meter live preview (stand-alone viewer)
 Filename       : prototypes/pyqtgraph_live_preview.py
 Version        : 0.0.2 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu   (alt: naval.antennas@gmail.com)
 Created        : 2026-10-27
 Modified       : 2026-10-27
 Language       : Python 3.7.11+ (pyqtgraph 0.12.4 + PySide6 6.2.4) and 3.12
 Requires       : ab_power_meter_monitor.py >= 1.7.0, ab_live_plots.py >= 0.1.0
                  (one folder up), PySide6 + qtpy, pyqtgraph, numpy
 Licence        : GPL-3.0-or-later (GNU General Public License v3.0 or later)
 Status         : Superseded by the monitor GUI 1.7.0 - kept as a quick viewer
===============================================================================

The 0.0.1 prototype's plot code now lives in ab_live_plots.py and is the
monitor GUI's own (1.7.0). This file only polls the meters through
abm.poll_cycle() with every file output off and shows one ab_live_plots
MeterPanel tab per meter - a viewer that writes nothing. For logging, use the
monitor GUI (it shows the same tabs).

USAGE
-----
    python prototypes/pyqtgraph_live_preview.py --ips 10.16.130.50,10.16.130.54 --interval 30
    python prototypes/pyqtgraph_live_preview.py --demo            # two emulated meters, 2 s
    python prototypes/pyqtgraph_live_preview.py --demo --polls 6 --screenshot shot.png   # test

REVISION HISTORY
----------------
0.0.2  2026-10-27  Thin wrapper around ab_live_plots.MeterPanel (monitor 1.7.0).
0.0.1  2026-10-27  First prototype (monitor 1.6.5).
===============================================================================
"""

# %% IMPORTS ===================================================================
from __future__ import annotations

import argparse
import concurrent.futures
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

os.environ.setdefault("QT_API", "pyside6")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))                 # the monitor lives one folder up

from qtpy import QtCore, QtWidgets  # noqa: E402

import ab_power_meter_monitor as abm  # noqa: E402
import ab_live_plots as live  # noqa: E402

__version__ = "0.0.2"


# %% MAIN WINDOW ================================================================
class PreviewWindow(QtWidgets.QMainWindow):
    """One MeterPanel tab per meter; polls in a worker thread; writes no files."""

    def __init__(self, ips: List[str], interval: float, dark: bool = True) -> None:
        super().__init__()
        self.setWindowTitle("AB meters - live viewer {0} (no files written)".format(__version__))
        self.resize(1400, 900)
        self.ips, self.interval, self.dark = ips, float(interval), dark
        self.cfg = abm.default_config()
        self.cfg["ip_list"] = list(ips)
        for key in ("enable_csv", "enable_log_append", "enable_veusz", "enable_fits", "enable_xlsx",
                    "enable_log_file", "enable_ram_flush"):
            self.cfg[key] = 0
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="viewer_poll")
        self.future: Optional[concurrent.futures.Future] = None
        self.polls = 0
        self.tabs = QtWidgets.QTabWidget()
        self.setCentralWidget(self.tabs)
        self.panels: Dict[str, Any] = {}
        for ip in ips:
            panel = live.MeterPanel(ip, abm, dark=dark, window_s=abm.LIVE_PLOT_WINDOW_SEC)
            self.panels[ip] = panel
            self.tabs.addTab(panel, "Meter {0}".format(ip))
        self.tabs.currentChanged.connect(lambda _i: self._refresh_visible())
        menu = self.menuBar().addMenu("&View")
        for name in ("dark", "light"):
            action = menu.addAction("{0} theme".format(name.title()))
            action.triggered.connect(lambda _c=False, _d=(name == "dark"): self._set_theme(_d))
        abm.begin_run(self.cfg)
        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.timeout.connect(self._start_poll)
        self.poll_timer.start(int(self.interval * 1000))
        self.check_timer = QtCore.QTimer(self)
        self.check_timer.timeout.connect(self._check_poll)
        self.check_timer.start(200)
        self._start_poll()

    def _start_poll(self) -> None:
        if self.future is None or self.future.done():
            self.future = self.pool.submit(abm.poll_cycle, self.cfg)

    def _check_poll(self) -> None:
        if self.future is None or not self.future.done():
            return
        future, self.future = self.future, None
        try:
            status = future.result()
        except Exception as exc:                         # shown, never fatal
            self.statusBar().showMessage("Poll failed: {0}".format(exc))
            return
        self.polls += 1
        for panel in self.panels.values():
            panel.set_poll_status(status)
            panel.dirty = True
        self._refresh_visible()
        self.statusBar().showMessage("Poll {0} at {1}: {2:.1f} s, answering {3}, no answer {4}".format(
            self.polls, time.strftime("%H:%M:%S"), status["poll_seconds"],
            ", ".join(status["ok_ips"]) or "-", ", ".join(status["failed_ips"]) or "-"))

    def _refresh_visible(self) -> None:
        current = self.tabs.currentWidget()
        for panel in self.panels.values():
            if panel is current:
                if panel.dirty:
                    panel.refresh()
            elif not panel.dirty:
                panel.release()

    def _set_theme(self, dark: bool) -> None:
        self.dark = dark
        self.setStyleSheet("QWidget { background-color: #1e1e2e; color: #cdd6f4; }" if dark else "")
        for panel in self.panels.values():
            panel.apply_theme(dark)

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt interface
        self.poll_timer.stop()
        self.check_timer.stop()
        self.pool.shutdown(wait=True)
        abm.end_run(self.cfg)
        super().closeEvent(event)


# %% COMMAND LINE ===============================================================
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ips", help="meters (default: the monitor's IP_LIST)")
    parser.add_argument("--url-template", dest="url_template")
    parser.add_argument("--interval", type=float, default=30.0, help="seconds between polls")
    parser.add_argument("--demo", action="store_true", help="two emulated meters (127.0.0.2/.3), 2 s polls")
    parser.add_argument("--theme", choices=["dark", "light"], default="dark")
    parser.add_argument("--polls", type=int, default=0, help="test: quit after this many polls")
    parser.add_argument("--screenshot", help="test: save the window after --polls polls")
    args = parser.parse_args(argv)
    logging.getLogger("ABMonitor").setLevel(logging.WARNING)
    abm.set_console_logging(False)
    ips = [ip.strip() for ip in args.ips.split(",")] if args.ips else list(abm.IP_LIST)
    interval = args.interval
    if args.demo:
        sys.path.insert(0, str(HERE.parent / "tests"))
        from ab_meter_emulator import start_emulators
        port = 18099
        ips = ["127.0.0.2", "127.0.0.3"]
        start_emulators(ips, port)
        abm.METER_URL_TEMPLATE = "http://{ip}:%d/{page}" % port
        interval = min(interval, 2.0)
    if args.url_template:
        abm.METER_URL_TEMPLATE = args.url_template
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    live.configure(antialias=True, wheel_needs_ctrl=bool(abm.LIVE_PLOT_WHEEL_NEEDS_CTRL))
    problem = live.self_test()
    if problem:
        print("pyqtgraph does not work here: {0}".format(problem))
        return 2
    window = PreviewWindow(ips, interval, args.theme == "dark")
    window._set_theme(args.theme == "dark")
    window.show()
    if args.polls:
        def _watch() -> None:
            if window.polls < args.polls:
                return
            app.processEvents()
            if args.screenshot:
                window.grab().save(args.screenshot)
            window.close()
            app.quit()
        watcher = QtCore.QTimer()
        watcher.timeout.connect(_watch)
        watcher.start(250)
    return app.exec_() if hasattr(app, "exec_") else app.exec()


if __name__ == "__main__":
    sys.exit(main())
