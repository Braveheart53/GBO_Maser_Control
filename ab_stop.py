#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ab_stop.py
==========
Companion stop-script for ab_power_meter_monitor.py (headless loop mode).

Usage
-----
From any terminal on the same machine::

    python ab_stop.py

Or simply::

    touch <OUTPUT_BASE_DIR>/STOP_COLLECTION

This script creates the stop-signal file that the headless polling loop
checks every 0.5 seconds.  The monitor finishes its current poll cycle,
writes all final outputs (including Veusz), and exits cleanly.

The OUTPUT_BASE_DIR value must match the one configured in
ab_power_meter_monitor.py.  Edit the constant below if you have changed
the default location.

Author : W. Wallace — NRAO / Green Bank Observatory
"""
import os
import sys

# ---------------------------------------------------------------------------
# Match this to OUTPUT_BASE_DIR in ab_power_meter_monitor.py
# ---------------------------------------------------------------------------
OUTPUT_BASE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "ab_meter_output"
)
STOP_SIGNAL_FILE = os.path.join(OUTPUT_BASE_DIR, "STOP_COLLECTION")


def main() -> None:
    os.makedirs(OUTPUT_BASE_DIR, exist_ok=True)
    try:
        with open(STOP_SIGNAL_FILE, "w", encoding="utf-8") as fh:
            fh.write("stop\n")
        print(f"Stop-signal file created: {STOP_SIGNAL_FILE}")
        print("The headless monitor will finish its current poll and exit cleanly.")
    except OSError as exc:
        print(f"ERROR: Could not create stop-signal file: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
