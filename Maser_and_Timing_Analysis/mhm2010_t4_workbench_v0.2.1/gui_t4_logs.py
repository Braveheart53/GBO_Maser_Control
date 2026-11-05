#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""T4 monitoring-file panel: the LAN module's own logs from FTP ./Monitoring.

===============================================================================
 Title          : T4 Monitoring Files Panel
 Filename       : gui_t4_logs.py
 Version        : 0.0.1 (Semantic Versioning 2.0.0; first internal draft)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-05
 Modified       : 2026-11-05
 Language       : Python 3.12
 GUI            : qtpy (PySide6 default) + pyqtgraph
 Licence        : GPL-3.0-or-later; see LICENSE
 Status         : Offline file view; sends nothing to any maser.
 Revision       : 0.0.1 Log selector, column check list, one x-linked plot
                  per column, x axis MJD - MJD0 / UTC / local date-time,
                  records table (every record, clock-not-set rows greyed),
                  summary with notes, reader options (date order, clock
                  valid from), RAM clear.
===============================================================================
Records stamped before the time-server sync (clock not set) are listed in the
table but never plotted. Time stamps are the maser's own UTC times [1, p. 12].

[1] T4Science SA, T4S-Monitoring Network Software User Manual, T4S-MAN-0015,
    ver. 1.17, Jul. 19, 2022.
"""
from __future__ import annotations

# %% ===========================================================================
#    IMPORTS
# =============================================================================
import numpy as np
import pyqtgraph as pg
from qtpy import QtCore, QtGui, QtWidgets

import t4_native

__version__ = "0.0.1"
PALETTE = ("#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf", "#8c564b", "#e377c2")
X_AXES = ("MJD - MJD0 (UTC)", "Date-time (UTC)", "Date-time (local)")


# %% ===========================================================================
#    TABLE MODEL (a QTableView over a DataFrame: no per-cell widgets)
# =============================================================================
class _FrameModel(QtCore.QAbstractTableModel):
    """Read-only view of a pandas DataFrame; clock-not-set rows are greyed."""

    def __init__(self, frame=None, parent=None):
        super().__init__(parent)
        self.frame = frame

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if self.frame is None or parent.isValid() else len(self.frame)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return 0 if self.frame is None or parent.isValid() else len(self.frame.columns)

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if self.frame is None or not index.isValid():
            return None
        if role == QtCore.Qt.DisplayRole:
            value = self.frame.iat[index.row(), index.column()]
            return f"{value:.6g}" if isinstance(value, (float, np.floating)) else str(value)
        if role == QtCore.Qt.ForegroundRole and "clock_valid" in self.frame.columns:
            if not self.frame["clock_valid"].iat[index.row()]:
                return QtGui.QBrush(QtGui.QColor("#8a8a8a"))
        return None

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        if role != QtCore.Qt.DisplayRole or self.frame is None:
            return None
        if orientation == QtCore.Qt.Horizontal:
            return str(self.frame.columns[section])
        return str(section + 1)

    def set_frame(self, frame) -> None:
        self.beginResetModel()
        self.frame = frame
        self.endResetModel()


# %% ===========================================================================
#    PANEL
# =============================================================================
class T4LogsWidget(QtWidgets.QWidget):
    """Shows T4NativeLog objects; the main window loads files and calls set_logs."""
    openRequested = QtCore.Signal()
    exportRequested = QtCore.Signal()
    cleared = QtCore.Signal()
    message = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logs: list = []
        self.foreground = "#dce5ef"
        self._build()

    # ------------------------------------------------------------------ layout
    def _build(self):
        layout = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        open_button = QtWidgets.QPushButton("Open T4 monitoring files…")
        open_button.setToolTip("Files from the T4 LAN module's FTP folder ./Monitoring, e.g. MonitHCB.dat "
                               "(recorded with RECMONIT=<s>; T4S-MAN-0015 Sec. 1.1.3)")
        open_button.clicked.connect(self.openRequested.emit)
        export_button = QtWidgets.QPushButton("Export T4 data…")
        export_button.clicked.connect(self.exportRequested.emit)
        clear_button = QtWidgets.QPushButton("Clear from RAM")
        clear_button.clicked.connect(self.clear)
        self.date_order = QtWidgets.QComboBox()
        self.date_order.addItems(["DMY", "MDY", "YMD"])
        self.date_order.setToolTip("Order of the TIME field. DMY was verified on the GBO file "
                                   "(16/01/06 cannot be MM/DD).")
        self.earliest = QtWidgets.QDateEdit(QtCore.QDate.fromString(t4_native.DEFAULT_EARLIEST_VALID,
                                                                    "yyyy-MM-dd"))
        self.earliest.setDisplayFormat("yyyy-MM-dd")
        self.earliest.setCalendarPopup(True)
        self.earliest.setToolTip("Records stamped before this UTC date are 'clock not set' (before the "
                                 "time-server sync): listed, never plotted. Applies on the next open.")
        for widget in (open_button, export_button, clear_button):
            top.addWidget(widget)
        top.addWidget(QtWidgets.QLabel("Date order"))
        top.addWidget(self.date_order)
        top.addWidget(QtWidgets.QLabel("Clock valid from (UTC)"))
        top.addWidget(self.earliest)
        top.addStretch(1)
        layout.addLayout(top)
        self.summary = QtWidgets.QLabel("No T4 monitoring files loaded.")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.summary)
        splitter = QtWidgets.QSplitter()
        layout.addWidget(splitter, 1)
        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.addWidget(QtWidgets.QLabel("Log (one per file layout)"))
        self.log_choice = QtWidgets.QComboBox()
        self.log_choice.currentIndexChanged.connect(self._show_log)
        left_layout.addWidget(self.log_choice)
        left_layout.addWidget(QtWidgets.QLabel("X axis"))
        self.x_axis = QtWidgets.QComboBox()
        self.x_axis.addItems(X_AXES)
        self.x_axis.currentIndexChanged.connect(self.draw)
        left_layout.addWidget(self.x_axis)
        left_layout.addWidget(QtWidgets.QLabel("Columns (one plot each)"))
        self.columns = QtWidgets.QListWidget()
        self.columns.itemChanged.connect(lambda *_: self.draw())
        left_layout.addWidget(self.columns, 1)
        splitter.addWidget(left)
        right = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.plots = pg.GraphicsLayoutWidget()
        right.addWidget(self.plots)
        self.model = _FrameModel()
        self.table = QtWidgets.QTableView()
        self.table.setModel(self.model)
        self.table.horizontalHeader().setStretchLastSection(True)
        right.addWidget(self.table)
        right.setSizes([600, 200])
        splitter.addWidget(right)
        splitter.setSizes([240, 920])

    # ------------------------------------------------------------- data in/out
    def reader_options(self) -> dict:
        """Options for t4_native.load_native_logs from the panel controls."""
        return {"date_order": self.date_order.currentText(),
                "earliest_valid": self.earliest.date().toString("yyyy-MM-dd")}

    def set_logs(self, logs) -> None:
        self.logs = list(logs)
        self.log_choice.blockSignals(True)
        self.log_choice.clear()
        self.log_choice.addItems([log.name for log in self.logs])
        self.log_choice.blockSignals(False)
        self._show_log()

    def current(self):
        index = self.log_choice.currentIndex()
        return self.logs[index] if 0 <= index < len(self.logs) else None

    def clear(self) -> None:
        """Release the loaded logs (RAM) and empty the view."""
        self.logs = []
        self.log_choice.clear()
        self.columns.clear()
        self.plots.clear()
        self.model.set_frame(None)
        self.summary.setText("No T4 monitoring files loaded.")
        self.cleared.emit()
        self.message.emit("T4 monitoring files cleared from RAM.")

    def _show_log(self, *_):
        log = self.current()
        self.columns.blockSignals(True)
        self.columns.clear()
        if log is None:
            self.columns.blockSignals(False)
            return
        for column in log.numeric_columns() + log.text_columns():
            label = column.header + ("" if column.kind == "numeric" else "  (status: 0 = No error)")
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, column.key)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked)
            self.columns.addItem(item)
        self.columns.blockSignals(False)
        self.model.set_frame(log.frame())
        self.table.resizeColumnsToContents()
        stats = log.stats
        lines = [f"<b>{log.name}</b>: {stats['records']} records; {stats['clock_valid']} clock-valid "
                 f"({stats.get('first_utc', '-')} to {stats.get('last_utc', '-')}); "
                 f"{stats['clock_not_set']} stamped before the time-server sync (grey, not plotted)."]
        if "interval_s" in stats:
            interval = stats["interval_s"]
            lines.append(f"Record interval: median {interval['median']:.0f} s, mean {interval['mean']:.2f} s, "
                         f"range {interval['min']:.0f} to {interval['max']:.0f} s.")
        lines += [f"• {note}" for note in log.notes]
        self.summary.setText("<br>".join(lines))
        self.draw()

    # ------------------------------------------------------------------- plots
    def _x(self, log):
        """x values and label for clock-valid records, per the x-axis choice."""
        mjd = log.valid_mjd
        choice = self.x_axis.currentIndex()
        if choice == 0:
            mjd0 = float(np.floor(mjd[0]))
            return mjd - mjd0, f"MJD - {mjd0:.0f} (UTC, maser time stamps)", None
        epoch = (mjd - t4_native.MJD_UNIX_EPOCH) * 86400.0
        if choice == 1:
            return epoch, "UTC date-time (maser time stamps)", 0
        return epoch, "Local date-time (this computer's zone)", None

    def draw(self, *_):
        self.plots.clear()
        log = self.current()
        if log is None or not log.clock_valid.any():
            return
        x, xlabel, utc_offset = self._x(log)
        wanted = [self.columns.item(row).data(QtCore.Qt.UserRole) for row in range(self.columns.count())
                  if self.columns.item(row).checkState() == QtCore.Qt.Checked]
        columns = {c.key: c for c in log.columns}
        first = None
        for number, key in enumerate(wanted):
            column = columns[key]
            axis_items = None
            if self.x_axis.currentIndex() > 0:
                axis_items = {"bottom": pg.DateAxisItem(orientation="bottom", utcOffset=utc_offset)}
            plot = self.plots.addPlot(row=number, col=0, axisItems=axis_items)
            y = (log.valid(key) if column.kind == "numeric"
                 else log.status_flag(key)[log.clock_valid])
            plot.plot(x, y, pen=pg.mkPen(PALETTE[number % len(PALETTE)], width=1.5),
                      symbol="o", symbolSize=4, symbolBrush=PALETTE[number % len(PALETTE)])
            plot.setLabel("left", column.unit or ("flag" if column.kind == "text" else "value"))
            plot.setTitle(column.header if column.kind == "numeric" else f"{column.header} (0 = No error)",
                          color=self.foreground, size="9pt")
            plot.showGrid(x=True, y=True, alpha=0.15)
            left = plot.getAxis("left")
            left.enableAutoSIPrefix(False)          # no "x0.001" on a 0/1 status flag
            plot.getAxis("bottom").enableAutoSIPrefix(False)
            left.setWidth(60)
            finite = y[np.isfinite(y)]
            if column.kind == "text":
                plot.setYRange(-0.6, 1.6, padding=0)
                left.setTickSpacing(major=1.0, minor=0.5)
            elif len(finite):
                low, high = float(finite.min()), float(finite.max())
                pad = 0.1 * (high - low) if high > low else max(0.5, 0.01 * abs(high))
                plot.setYRange(low - pad, high + pad, padding=0)
                # Three labelled ticks at most: the plots are short and stacked.
                span = (high + pad) - (low - pad)
                step = 10 ** np.floor(np.log10(span / 2))
                for factor in (1, 2, 5, 10):
                    if span / (factor * step) <= 3:
                        step *= factor
                        break
                left.setTickSpacing(major=step, minor=step / 2)
            for side in ("left", "bottom"):
                plot.getAxis(side).setPen(pg.mkPen(self.foreground))
                plot.getAxis(side).setTextPen(pg.mkPen(self.foreground))
            if first is None:
                first = plot
            else:
                plot.setXLink(first)
            if number == len(wanted) - 1:
                plot.setLabel("bottom", xlabel)
            else:
                plot.hideAxis("bottom")             # x-linked: one time axis, at the bottom

    def set_theme(self, background: str, foreground: str):
        self.foreground = foreground
        self.plots.setBackground(background)
        self.draw()
