#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""One time display for the whole workbench: MJD or ISO 8601 calendar time.

===============================================================================
 Title          : Time Display (MJD / ISO 8601 local / ISO 8601 UTC)
 Filename       : time_axis.py
 Version        : 0.0.1 (Semantic Versioning 2.0.0; first internal draft)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-05
 Modified       : 2026-11-05
 Language       : Python 3.12
 GUI            : qtpy (PySide6 default) + pyqtgraph
 Licence        : GPL-3.0-or-later; see LICENSE
 Revision       : 0.0.1 Formatting and parsing (time_format.py), TimeAxisItem (pyqtgraph axis whose data stay in MJD and
                  whose ticks follow the chosen display), TimeEdit (spin box
                  holding MJD that shows and accepts the chosen display).
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
import numpy as np
import pyqtgraph as pg
from qtpy import QtGui, QtWidgets

__version__ = "0.0.1"

# %% ===========================================================================
#    SHARED FORMATTING (time_format.py, no Qt)
# =============================================================================
from time_format import (MJD_UNIX_EPOCH, MODES, MODE_NAMES, MODE_EXAMPLES, check_mode,  # noqa: F401
                         mjd_to_epoch, to_datetime, format_time, span_text, parse_time,
                         offset_text, axis_label)


# %% ===========================================================================
#    PLOT AXIS
# =============================================================================
class TimeAxisItem(pg.DateAxisItem):
    """Axis for x data in MJD (or MJD - origin); ticks follow the time display.

    MJD mode behaves like a plain numeric axis. Calendar modes place ticks on
    round calendar times (pyqtgraph's DST-aware date logic) and label each
    tick with the full ISO 8601 time on two lines: date / Thh:mm:ss+hh:mm.
    """

    def __init__(self, orientation: str = "bottom", mode: str = "mjd", origin: float = 0.0, **kwargs):
        super().__init__(orientation, utcOffset=None, **kwargs)
        self.mode = "mjd"
        self.origin = float(origin)
        self.set_mode(mode)

    def set_mode(self, mode: str, origin: float | None = None) -> None:
        self.mode = check_mode(mode)
        if origin is not None:
            self.origin = float(origin)
        self.utcOffset = 0 if self.mode == "utc" else None     # None = this computer's zone
        self.enableAutoSIPrefix(False)
        self.picture = None
        self.update()

    def _epoch(self, x: float) -> float:
        return (x + self.origin - MJD_UNIX_EPOCH) * 86400.0

    def setZoomLevelForDensity(self, density):
        """As pyqtgraph, but spaced for the wide ISO labels (no overlap)."""
        width = self.fontMetrics.boundingRect("T00:00:00-00:00").width() + 16
        self.zoomLevel = next(iter(self.zoomLevels.values()))
        for maximal_spacing, zoom_level in self.zoomLevels.items():
            # Each level can skip ticks (e.g. days in steps of 5, 10, 15), so
            # allow up to 3x its nominal spacing before falling back to a
            # coarser level; tickValues() then removes any overlap.
            if 3.0 * maximal_spacing / width < density:
                break
            self.zoomLevel = zoom_level
        self.zoomLevel.utcOffset = self.utcOffset
        self.minSpacing = density * width

    def _label_width(self) -> float:
        metrics = getattr(self, "fontMetrics", None) or QtGui.QFontMetrics(QtGui.QFont())
        self.fontMetrics = metrics
        return metrics.boundingRect("T00:00:00-00:00").width() + 16

    def tickValues(self, minVal, maxVal, size):
        if self.mode == "mjd":
            return pg.AxisItem.tickValues(self, minVal, maxVal, size)
        if size <= 0 or maxVal <= minVal:
            return []
        self._label_width()
        levels = pg.DateAxisItem.tickValues(self, self._epoch(minVal), self._epoch(maxVal), size)
        # pyqtgraph draws the first level's labels always and drops a finer
        # level whose labels crowd the axis; wide ISO labels would drop every
        # level whose first level is empty. Merge the levels (coarse first,
        # e.g. month starts before days) into ONE level and keep only ticks
        # at least one label width apart, so labels never overlap.
        minimum = self._label_width() * (maxVal - minVal) / size        # in x units (days)

        def merge(ordered):
            kept, spacing = [], None
            for level_spacing, values in ordered:
                for value in values:
                    x = value / 86400.0 + MJD_UNIX_EPOCH - self.origin
                    if minVal <= x <= maxVal and all(abs(x - other) >= minimum for other in kept):
                        kept.append(x)
                        spacing = level_spacing / 86400.0 if spacing is None else spacing
            return kept, spacing
        # Coarse first keeps round boundaries (month starts); fine first keeps the
        # evenly spaced ticks. Use whichever labels more ticks (coarse on a tie).
        coarse, coarse_spacing = merge(levels)
        fine, fine_spacing = merge(levels[::-1])
        kept, spacing = (fine, fine_spacing) if len(fine) > len(coarse) else (coarse, coarse_spacing)
        if not kept:
            return []
        return [(spacing, sorted(kept))]

    def tickStrings(self, values, scale, spacing):
        if self.mode == "mjd":
            if values and max(abs(v) for v in values) >= 1.0e4 and spacing > 0:
                # pyqtgraph uses %g (6 digits) here, so 61349.71 and 61349.72
                # would both print as 61349.7; give the decimals the spacing needs.
                places = int(max(0, np.ceil(-np.log10(spacing))))
                return [f"{value:.{places}f}" for value in values]
            return pg.AxisItem.tickStrings(self, values, scale, spacing)
        strings = []
        for value in values:
            date, _, clock = format_time(value + self.origin, self.mode).partition("T")
            strings.append(f"{date}\nT{clock}")
        return strings


# %% ===========================================================================
#    SPIN BOX
# =============================================================================
class TimeEdit(QtWidgets.QDoubleSpinBox):
    """Holds an MJD (UTC); shows and accepts it in the chosen time display.

    Up/down steps one day in MJD mode and one hour in calendar modes.
    """

    def __init__(self, value: float = 60000.0, mode: str = "mjd", parent=None):
        super().__init__(parent)
        self.mode = "mjd"
        self.setRange(1.0e4, 1.0e6)
        self.setDecimals(9)
        self.setKeyboardTracking(False)
        self.setValue(value)
        self.set_mode(mode)

    def set_mode(self, mode: str) -> None:
        self.mode = check_mode(mode)
        self.setSingleStep(1.0 if mode == "mjd" else 1.0 / 24.0)
        self.setToolTip({"mjd": "MJD (UTC). A calendar time such as 2026-11-05T15:47:13-05:00 is also accepted.",
                         "local": "Local date-time, ISO 8601, e.g. 2026-11-05T15:47:13-05:00. Without an offset "
                                  "the time is read as this computer's local time; an MJD number also works.",
                         "utc": "UTC date-time, ISO 8601, e.g. 2026-11-05T20:47:13+00:00 or ...Z; an MJD "
                                "number also works."}[mode])
        metrics = QtGui.QFontMetrics(self.font())
        self.setMinimumWidth(metrics.horizontalAdvance(MODE_EXAMPLES["local"] + "000") + 30)
        self.lineEdit().setText(self.textFromValue(self.value()))

    def textFromValue(self, value):
        return format_time(value, self.mode, mjd_decimals=9)

    def valueFromText(self, text):
        try:
            return parse_time(text, self.mode)
        except ValueError:
            return self.value()

    def validate(self, text, position):
        try:
            parse_time(text, self.mode)
            return QtGui.QValidator.State.Acceptable, text, position
        except ValueError:
            return QtGui.QValidator.State.Intermediate, text, position

    def fixup(self, text):
        return self.textFromValue(self.value())
