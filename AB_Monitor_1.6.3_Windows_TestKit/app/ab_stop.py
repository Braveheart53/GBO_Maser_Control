#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace <wwallace@nrao.edu>
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Request a clean stop of the headless monitor, caller or exporter.

===============================================================================
 Title          : AB Power Meter - stop helper
 Filename       : ab_stop.py
 Version        : 1.1.0 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu   (alt: naval.antennas@gmail.com)
 Phone          : +1 (304) 456-2216
 Created        : 2026-05-13
 Modified       : 2026-10-26
 Language       : Python 3.7.11 - 3.12 (standard library only)
 Requires       : nothing
 Licence        : GPL-3.0-or-later (GNU General Public License v3.0 or later)
 Status         : Release candidate
===============================================================================

DESCRIPTION
-----------
Creates the STOP_COLLECTION file that the headless loop, ab_meter_caller.run()
and the Prometheus exporter check every 0.5 s. They finish the current poll,
write every enabled output and exit cleanly (exit code 0, so systemd does not
restart them).

The file lives in the monitor's output root, resolved in this order:
  1. --output-dir DIR
  2. environment variable ABMETER_OUTPUT_DIR (same variable the monitor reads)
  3. <folder of this script>/ab_meter_output   (the monitor's default)

USAGE
-----
    python ab_stop.py
    python ab_stop.py --output-dir /srv/abmeter
    ABMETER_OUTPUT_DIR=/srv/abmeter python ab_stop.py

REVISION HISTORY
----------------
1.1.0  2026-10-26  House-style header; honours ABMETER_OUTPUT_DIR and
                   --output-dir like the monitor 1.6.0; Spyder cells.
1.0.0  2026-05-13  First version.
===============================================================================
"""

# %% IMPORTS ===================================================================
import argparse
import os
import sys

__version__ = "1.1.0"


# %% PATHS =====================================================================
def output_base_dir(explicit: str = "") -> str:
    """Output root: --output-dir, else ABMETER_OUTPUT_DIR, else the default."""
    if explicit:
        return os.path.abspath(explicit)
    from_env = os.environ.get("ABMETER_OUTPUT_DIR", "").strip()
    if from_env:
        return from_env
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "ab_meter_output")


# %% MAIN ======================================================================
def main(argv=None) -> int:
    """Write the STOP_COLLECTION file; return the process exit code."""
    parser = argparse.ArgumentParser(description="Stop the AB power-meter monitor cleanly.")
    parser.add_argument("--output-dir", default="", dest="output_dir",
                        help="Monitor output root (default: ABMETER_OUTPUT_DIR or ./ab_meter_output).")
    args = parser.parse_args(argv)
    base = output_base_dir(args.output_dir)
    stop_file = os.path.join(base, "STOP_COLLECTION")
    try:
        os.makedirs(base, exist_ok=True)
        with open(stop_file, "w", encoding="utf-8") as handle:
            handle.write("stop\n")
    except OSError as exc:
        print(f"ERROR: could not create the stop-signal file {stop_file}: {exc}", file=sys.stderr)
        return 1
    print(f"Stop-signal file created: {stop_file}")
    print("The monitor finishes its current poll, writes all outputs and exits cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
