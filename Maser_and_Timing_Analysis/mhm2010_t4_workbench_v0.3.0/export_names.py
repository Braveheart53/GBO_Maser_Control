#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Dataset names, Veusz tags and full-precision numbers for every export.

===============================================================================
 Title          : Export Names, Tags and Full Precision
 Filename       : export_names.py
 Version        : 0.0.1 (Semantic Versioning 2.0.0; first internal draft)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-10
 Modified       : 2026-11-10
 Language       : Python 3.12
 Requires       : numpy, pandas; bundled vszh5_writer (VszDoc)
 Licence        : GPL-3.0-or-later; see LICENSE
 Revision       : 0.0.1 Operator requests of 2026-11-10: meaningful dataset and
                  trace names, Veusz tags by source and by type, and no
                  rounding or trimming of any number in any export.
===============================================================================

NAMES
-----
Every dataset is named  <Source>_<Item>_<quantity>[_<unit>]  with letters,
digits and underscores only, so a name is also a valid Veusz expression
(no back-quotes needed), e.g.

    Franks_MSM_GPS_phase_ns            measured 1 PPS time difference
    Franks_MSM_GPS_time_MJD_UTC        its time stamps
    Fit_MSM_GPS_model_phase_ns         the selected fit on the same grid
    Ref1PPS_MSM_GPS_deltas_ns          reference figure 1 data
    HMD_ExtSynth_DDS_setting           MHM2010 external synthesizer setting
    T4log_MonitHCB_Internal_temperature_degC
    Suggested_MHM2010_drift_change_pp18_per_day

The writer (0.0.25 and later) names each trace widget after its y dataset,
so the Veusz widget tree carries the same names.

TAGS
----
Two families, shown as folders by Veusz's Data panel (Group by: Tags); a
dataset appears under each of its tags:

    source_<origin>   source_Franks, source_FITS, source_HMD,
                      source_T4_MONIT_UDP, source_T4_log_MonitHCB,
                      source_Workbench (computed by this program)
    type_<kind>       type_time_MJD, type_time_local, type_time_text,
                      type_measurement, type_fit_model, type_fit_residual,
                      type_fit_results, type_suggested_settings,
                      type_telemetry, type_status, type_stability,
                      type_reference_figure, type_metadata, ...

FULL PRECISION
--------------
* Numeric datasets are float64, written unchanged (writer).
* Numbers written as TEXT (CSV, Markdown, Veusz text datasets) use Python's
  repr(float): the shortest decimal string that converts back to the same
  float64 (17 significant digits at most). Nothing is rounded or trimmed.
* Veusz shows axis ticks with "%.12Vg" (up to 12 significant digits; Veusz's
  automatic "%Vg" switches to 6.096 x 10^4 for MJD) - ticks are positions,
  not data. Veusz's Data-editor table displays a float with Qt's default 6
  significant digits (QLocale::toString), although the file holds all 17;
  double-click a cell, or use the *_text datasets, to read every digit.
"""
from __future__ import annotations

# %% ===========================================================================
#    IMPORTS
# =============================================================================
import math
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from vszh5_writer import VszDoc

__version__ = "0.0.1"

#: Veusz tick-label format of every numeric axis written by the workbench.
NUMERIC_TICK_FORMAT = "%.12Vg"

#: Unit and symbol spellings inside names (Veusz-safe, readable).
_REPLACEMENTS = (("->", " to "), ("°C", "degC"), ("°", "deg"), ("�C", "degC"),
                 ("µ", "u"), ("μ", "u"), ("%", "pct"), ("/", " per "), ("±", "pm"),
                 ("+", " plus "), ("#", " no "))


# %% ===========================================================================
#    NAMES AND TAGS
# =============================================================================
def vname(*parts: Any) -> str:
    """Join ``parts`` into a dataset name of letters, digits and underscores.

    '->' becomes 'to', degree signs 'deg', '/' 'per', other symbols '_';
    runs of '_' collapse. Empty parts are skipped.
    """
    words = []
    for part in parts:
        text = str(part).strip()
        if not text:
            continue
        for old, new in _REPLACEMENTS:
            text = text.replace(old, new)
        text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
        if text:
            words.append(text)
    name = "_".join(words)
    name = re.sub(r"_+", "_", name)
    if not name:
        raise ValueError("empty dataset name")
    return name if not name[0].isdigit() else "d_" + name


def source_tag(origin: str) -> str:
    """Veusz tag of a data source, e.g. 'source_Franks'."""
    return "source_" + vname(origin)


def type_tag(kind: str) -> str:
    """Veusz tag of a data type, e.g. 'type_measurement'."""
    return "type_" + vname(kind)


def tags(origin: str, *kinds: str) -> tuple[str, ...]:
    """One source tag plus one or more type tags."""
    return (source_tag(origin),) + tuple(type_tag(kind) for kind in kinds)


def source_name(source_kind: str) -> str:
    """Name part of a timing source: 'Franks', 'FITS', 'Hourly median' -> 'HourlyMedian'."""
    return vname(str(source_kind).title().replace(" ", "")) if " " in str(source_kind) else vname(source_kind)


# %% ===========================================================================
#    FULL-PRECISION TEXT
# =============================================================================
def full(value: Any) -> str:
    """Shortest exact text of a number: repr(float) for floats, str(int) for ints.

    repr() of a Python float is the shortest decimal string that reads back
    to the same float64 (Python >= 3.1), so no digit is lost or invented.
    """
    if value is None:
        return ""
    if isinstance(value, (bool, np.bool_)):
        return str(bool(value))
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    number = float(value)
    if math.isnan(number):
        return "nan"
    return repr(number)


def full_strings(values: Iterable[Any]) -> list[str]:
    """full() of every element (for Veusz text datasets and reports)."""
    return [full(value) for value in np.asarray(list(values) if not isinstance(values, np.ndarray)
                                                 else values).ravel()]


def write_csv(path: str | Path, columns: Mapping[str, Any] | pd.DataFrame, **to_csv: Any) -> Path:
    """CSV with every float at full precision (pandas writes repr(float)).

    ``columns`` is a DataFrame or a mapping of column name -> 1-D values.
    A float_format argument is refused: it would round.
    """
    if "float_format" in to_csv:
        raise ValueError("write_csv never rounds: do not pass float_format")
    frame = columns if isinstance(columns, pd.DataFrame) else pd.DataFrame(dict(columns))
    target = Path(path)
    frame.to_csv(target, index=False, **to_csv)
    return target


# %% ===========================================================================
#    VEUSZ DOCUMENT WITH UNTRIMMED NUMERIC TICKS
# =============================================================================
class FullPrecisionDoc(VszDoc):
    """VszDoc whose numeric axes show up to 12 significant digits.

    Every axis gets TickLabels/format = NUMERIC_TICK_FORMAT when it is
    added; a date axis sets its own ISO 8601 format afterwards (the later
    Set wins), so only numeric axes keep it. Veusz's automatic "%Vg" shows
    MJD 60975 as 6.0975 x 10^4 and long DDS values cut at the page edge.
    """

    def add(self, wtype: str, name: str, **kw: Any) -> None:
        super().add(wtype, name, **kw)
        if wtype == "axis":
            self.set(f"{name}/TickLabels/format", NUMERIC_TICK_FORMAT)
