#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Time display without Qt: MJD and ISO 8601 (local or UTC) formatting and parsing.

===============================================================================
 Title          : Time Format (MJD / ISO 8601)
 Filename       : time_format.py
 Version        : 0.0.1 (Semantic Versioning 2.0.0; first internal draft)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-05
 Modified       : 2026-11-05
 Language       : Python 3.12 (standard library + numpy)
 Licence        : GPL-3.0-or-later; see LICENSE
 Revision       : 0.0.1 format_time, parse_time, span_text, axis_label,
                  offset_text; used by the GUI (time_axis.py) and the batch
                  analysis (--start/--end).
===============================================================================
Data, fits and files always stay in MJD (UTC); only the display changes.

    t_Unix = (MJD - 40587) x 86400 s            (MJD 40587 = 1970-01-01 UTC)

Local time uses this computer's time-zone rules for each instant, so the
offset follows daylight saving time (-04:00 in July, -05:00 in November at
Green Bank). Output follows ISO 8601-1:2019 [1], e.g.
2026-11-05T15:47:13-05:00. Input also accepts a one-digit offset hour
(2026-11-05T15:47:13-5:00), 'Z', a date alone, or a plain MJD number.

[1] ISO 8601-1:2019, Date and time - Representations for information
    interchange - Part 1: Basic rules.
"""
from __future__ import annotations

# %% ===========================================================================
#    IMPORTS
# =============================================================================
from datetime import datetime, timezone
import re

import numpy as np

__version__ = "0.0.1"

# %% ===========================================================================
#    CONSTANTS
# =============================================================================
MJD_UNIX_EPOCH = 40587.0
MODES = ("mjd", "local", "utc")
MODE_NAMES = {"mjd": "MJD (UTC)",
              "local": "Calendar date-time, local (ISO 8601)",
              "utc": "Calendar date-time, UTC (ISO 8601)"}
MODE_EXAMPLES = {"mjd": "61349.86612", "local": "2026-11-05T15:47:13-05:00",
                 "utc": "2026-11-05T20:47:13+00:00"}
_ONE_DIGIT_OFFSET = re.compile(r"([+-])(\d):(\d\d)$")


def check_mode(mode: str) -> str:
    if mode not in MODES:
        raise ValueError(f"time display must be one of {MODES}, not {mode!r}")
    return mode


# %% ===========================================================================
#    CONVERSION
# =============================================================================
def mjd_to_epoch(mjd):
    """MJD (UTC) -> Unix seconds."""
    return (np.asarray(mjd, float) - MJD_UNIX_EPOCH) * 86400.0


def to_datetime(mjd: float, mode: str) -> datetime:
    """Aware datetime for one MJD, in UTC or in this computer's local zone (DST-aware)."""
    stamp = datetime.fromtimestamp(round(float(mjd_to_epoch(mjd)), 6), timezone.utc)
    return stamp if mode == "utc" else stamp.astimezone()


def format_time(mjd: float, mode: str, mjd_decimals: int = 6) -> str:
    """'61349.866123' (mjd) or '2026-11-05T15:47:13-05:00' (local) / '...+00:00' (utc)."""
    if check_mode(mode) == "mjd":
        return f"{float(mjd):.{mjd_decimals}f}"
    rounded = MJD_UNIX_EPOCH + round(float(mjd_to_epoch(mjd))) / 86400.0   # whole seconds
    return to_datetime(rounded, mode).isoformat(timespec="seconds")


def span_text(low: float, high: float, mode: str) -> str:
    """'MJD 61349.717 to 61349.768' or '2026-11-05T12:13:09-05:00 to ...'."""
    if mode == "mjd":
        return f"MJD {low:.5f} to {high:.5f}"
    return f"{format_time(low, mode)} to {format_time(high, mode)}"


def parse_time(text: str, mode: str) -> float:
    """MJD from text in the chosen display (a plain MJD number is always accepted).

    ISO text without an offset is read as local time in "local" mode and as UTC
    otherwise. Raises ValueError for anything else.
    """
    text = str(text).strip()
    if not text:
        raise ValueError("empty time")
    try:
        value = float(text)
    except ValueError:
        value = None
    if value is not None:
        if 1.0e4 < value < 1.0e6:
            return value
        raise ValueError(f"{text!r} is not an MJD between 10000 and 1000000")
    normalised = _ONE_DIGIT_OFFSET.sub(lambda m: f"{m[1]}0{m[2]}:{m[3]}", text.replace("Z", "+00:00"))
    normalised = normalised.replace(" ", "T", 1) if "T" not in normalised and " " in normalised else normalised
    stamp = datetime.fromisoformat(normalised)
    if stamp.tzinfo is None:
        stamp = stamp.astimezone() if mode == "local" else stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp() / 86400.0 + MJD_UNIX_EPOCH


def offset_text(mjd: float) -> str:
    """Local UTC offset at that instant, e.g. '-05:00'."""
    return to_datetime(mjd, "local").isoformat(timespec="seconds")[-6:]


def axis_label(mode: str, low: float | None = None, high: float | None = None,
               origin: float = 0.0, note: str = "") -> str:
    """Axis title for the chosen display; local lists the offsets in the range."""
    suffix = f" {note}" if note else ""
    if mode == "mjd":
        return (f"MJD - {origin:.5f}" if origin else "MJD (UTC)") + suffix
    if mode == "utc":
        return "UTC date-time, ISO 8601 (+00:00)" + suffix
    offsets = []
    for value in (low, high):
        if value is not None and np.isfinite(value):
            text = offset_text(value + origin)
            if text not in offsets:
                offsets.append(text)
    zone = " then ".join(offsets) if offsets else offset_text(MJD_UNIX_EPOCH + datetime.now().timestamp() / 86400)
    return f"Local date-time, ISO 8601 (UTC{zone})" + suffix
