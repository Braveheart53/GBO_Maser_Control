#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace <wwallace@nrao.edu>
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Headless monitor with a visible log and every output on (manual checks).

===============================================================================
 Title          : AB Power Meter Monitor - visible headless demo
 Filename       : tests/run_headless_demo.py
 Version        : 0.0.1 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu
 Created        : 2026-10-26
 Modified       : 2026-10-26
 Language       : Python 3.8 through 3.12
 Requires       : the monitor's dependencies
 Licence        : GPL-3.0-or-later (GNU General Public License v3.0 or later)
 Status         : Internal draft
===============================================================================

The monitor's command line runs headless SILENTLY by default (HEADLESS_SILENT
= 1, for services). For a hands-on check this wrapper turns the console log
on, enables CSV, Markdown, .vszh5, FITS, XLSX and ab_monitor.log, and then
runs exactly the monitor's own command-line path with the arguments given:

    python tests/run_headless_demo.py --headless --count 0 --interval 2 ^
        --ips 127.0.0.2,127.0.0.3 --url-template "http://{ip}:8080/{page}" ^
        --output-dir C:\\ABTest\\headless_output

Stop with Ctrl+C or Ctrl+Break (both finish the final writes), or run
``python ab_stop.py --output-dir <same folder>`` in another window.

REVISION HISTORY
----------------
0.0.1  2026-10-26  First version (monitor 1.6.2, Windows test kit).
===============================================================================
"""

# %% IMPORTS ===================================================================
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ab_power_meter_monitor as abm  # noqa: E402

# %% SWITCHES FOR A VISIBLE RUN ==================================================
abm.HEADLESS_SILENT = 0                      # log to the console
for _flag in ("ENABLE_CSV", "ENABLE_LOG_APPEND", "ENABLE_VEUSZ", "ENABLE_FITS",
              "ENABLE_XLSX", "ENABLE_LOG_FILE"):
    setattr(abm, _flag, 1)
abm.FULL_WRITE_EVERY_N_CYCLES = 10
abm.MEM_FLUSH_THRESHOLD_MB = 0.25            # a flush (and OS memory release) every ~30 s
abm.MEM_FLUSH_MIN_INTERVAL_SEC = 30

# %% RUN THE MONITOR'S OWN COMMAND-LINE PATH =====================================
if __name__ == "__main__":
    abm._parse_cli_and_apply(sys.argv[1:])
    abm.main()
