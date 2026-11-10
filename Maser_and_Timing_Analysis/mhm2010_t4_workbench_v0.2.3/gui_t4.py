#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""T4 iMaser telemetry panel: files, read-only live polling, RAM control.

===============================================================================
 Title          : T4 Telemetry Panel
 Filename       : gui_t4.py
 Version        : 0.0.3 (Semantic Versioning 2.0.0)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-03
 Modified       : 2026-11-05
 Language       : Python 3.12
 GUI            : qtpy (PySide6 default) + pyqtgraph
 Licence        : GPL-3.0-or-later; see LICENSE
 Status         : Live polling tested only against the loopback emulator.
 Revision       : 0.0.1 File view (recorder CSV / text logs re-decoded with
                  schema 0.0.2), read-only live polling in a worker thread,
                  RDFS/RDREG state read-back, unit-filtered plots with
                  in-place updates, value table, stale-data indicator, RAM
                  flush and pickle offload.
                  0.0.2 Local port defaults to 14000 for a real T4 (vendor
                  example; T4_Maser_Comms.py), ephemeral for the emulator;
                  open-button tooltip names the T4 monitoring files (.dat),
                  which open in the "T4 monitoring files" tab.
                  0.0.3 Time axis and status times follow View > Time display
                  (MJD or ISO 8601 local/UTC); data stay MJD.
===============================================================================
SAFETY: the only commands this panel can cause to be sent are the read-only
queries in t4_protocol.READ_ONLY_QUERIES. A non-loopback endpoint needs the
approval box ticked for that endpoint; no address is built in.
"""
from __future__ import annotations

from pathlib import Path
import threading
import time

import numpy as np
import pyqtgraph as pg
from qtpy import QtCore, QtWidgets

from t4_protocol import load_schema, synth_hz_from_reply
from time_axis import TimeAxisItem, axis_label, span_text

__version__ = "0.0.3"
PALETTE = ("#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf", "#8c564b", "#e377c2",
           "#7f7f7f", "#bcbd22")


class _Bridge(QtCore.QObject):
    """Carries worker-thread results onto the GUI thread (queued signals)."""
    record = QtCore.Signal(object)
    state = QtCore.Signal(object)
    finished = QtCore.Signal(str)


class T4TelemetryWidget(QtWidgets.QWidget):
    openRequested = QtCore.Signal()
    exportRequested = QtCore.Signal()
    stateRead = QtCore.Signal(object)       # {"synth_hz":..., "driftcomp":..., "lsbweight":..., "id":...}
    message = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.schema = load_schema()
        self.telemetry = None                # loaded T4Telemetry
        self.recorder = None                 # live T4Recorder
        self.monitor = None
        self.emulator = None
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None
        self.last_frame_time = None
        self.counts = {"ok": 0, "bad": 0}
        self.bridge = _Bridge()
        self.bridge.record.connect(self._on_record)
        self.bridge.state.connect(self._on_state)
        self.bridge.finished.connect(self._on_finished)
        self._build()
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    # ------------------------------------------------------------------ layout
    def _build(self):
        layout = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        open_button = QtWidgets.QPushButton("Open T4 telemetry files…")
        open_button.setToolTip("Recorder t4_raw_frames.csv / t4_telemetry.csv, text logs '<time> $<frame>', "
                               "or the T4's own monitoring files (*.dat from FTP ./Monitoring; shown in the "
                               "'T4 monitoring files' tab)")
        open_button.clicked.connect(self.openRequested.emit)
        export_button = QtWidgets.QPushButton("Export T4 telemetry…")
        export_button.clicked.connect(self.exportRequested.emit)
        top.addWidget(open_button)
        top.addWidget(export_button)
        self.schema_label = QtWidgets.QLabel(f"Schema {self.schema.version}: {self.schema.status}")
        self.schema_label.setWordWrap(True)
        top.addWidget(self.schema_label, 1)
        layout.addLayout(top)
        live = QtWidgets.QGroupBox("LIVE, READ-ONLY (MONIT; RDFS; RDREG=…; only)")
        grid = QtWidgets.QGridLayout(live)
        self.host = QtWidgets.QLineEdit()
        self.host.setPlaceholderText("T4 address (operator supplied)")
        self.port = QtWidgets.QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(14000)
        self.local_port = QtWidgets.QSpinBox()
        self.local_port.setRange(0, 65535)
        self.local_port.setSpecialValueText("ephemeral")
        self.local_port.setValue(14000)
        self.local_port.setToolTip("Local UDP port. The T4 probably answers to port 14000 (vendor example, "
                                   "T4_Maser_Comms.py); 0 = ephemeral. Ignored for the loopback emulator.")
        self.interval = QtWidgets.QDoubleSpinBox()
        self.interval.setRange(0.5, 3600)
        self.interval.setValue(10)
        self.interval.setSuffix(" s")
        self.timeout = QtWidgets.QDoubleSpinBox()
        self.timeout.setRange(0.1, 30)
        self.timeout.setValue(1.0)
        self.timeout.setSuffix(" s")
        self.use_emulator = QtWidgets.QCheckBox("Use loopback emulator (synthetic data)")
        self.use_emulator.setChecked(True)
        self.approve = QtWidgets.QCheckBox("I approve read-only polling of this endpoint")
        self.allow_prefix = QtWidgets.QCheckBox("Accept text before '$'")
        self.output_dir = QtWidgets.QLineEdit()
        self.output_dir.setPlaceholderText("Recording parent folder (required)")
        browse = QtWidgets.QPushButton("…")
        browse.clicked.connect(self._choose_output)
        self.start_button = QtWidgets.QPushButton("Start polling")
        self.start_button.clicked.connect(self.start)
        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop)
        self.stop_button.setEnabled(False)
        self.state_button = QtWidgets.QPushButton("Read synthesizer and DRIFTCOMP state")
        self.state_button.clicked.connect(self.read_state)
        widgets = [("Host", self.host), ("Port", self.port), ("Local port", self.local_port),
                   ("Interval", self.interval), ("Timeout", self.timeout)]
        for column, (label, widget) in enumerate(widgets):
            grid.addWidget(QtWidgets.QLabel(label), 0, 2*column)
            grid.addWidget(widget, 0, 2*column+1)
        grid.addWidget(self.use_emulator, 1, 0, 1, 3)
        grid.addWidget(self.approve, 1, 3, 1, 4)
        grid.addWidget(self.allow_prefix, 1, 7, 1, 3)
        grid.addWidget(self.output_dir, 2, 0, 1, 8)
        grid.addWidget(browse, 2, 8)
        buttons = QtWidgets.QHBoxLayout()
        for widget in (self.start_button, self.stop_button, self.state_button):
            buttons.addWidget(widget)
        self.flush_button = QtWidgets.QPushButton("Flush RAM preview")
        self.flush_button.clicked.connect(self.flush_ram)
        self.offload_button = QtWidgets.QPushButton("Offload preview to pickle")
        self.offload_button.clicked.connect(self.offload)
        buttons.addWidget(self.flush_button)
        buttons.addWidget(self.offload_button)
        grid.addLayout(buttons, 3, 0, 1, 10)
        self.status = QtWidgets.QLabel("Idle.")
        self.status.setWordWrap(True)
        grid.addWidget(self.status, 4, 0, 1, 10)
        layout.addWidget(live)
        splitter = QtWidgets.QSplitter()
        layout.addWidget(splitter, 1)
        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        self.unit = QtWidgets.QComboBox()
        self.unit.currentTextChanged.connect(self._populate_channels)
        left_layout.addWidget(QtWidgets.QLabel("Unit (one unit per plot)"))
        left_layout.addWidget(self.unit)
        self.channels = QtWidgets.QListWidget()
        self.channels.itemChanged.connect(lambda *_: self.refresh(force=True))
        left_layout.addWidget(self.channels, 1)
        splitter.addWidget(left)
        right = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.time_mode = "mjd"
        self.plot = pg.PlotWidget(title="T4 telemetry", axisItems={"bottom": TimeAxisItem("bottom")})
        self.plot.setLabel("bottom", "MJD (UTC, host receive time)")
        self.plot.addLegend()
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.getPlotItem().setClipToView(True)
        self.plot.getPlotItem().setDownsampling(auto=True, mode="peak")
        right.addWidget(self.plot)
        self.table = QtWidgets.QTableWidget(len(self.schema.channels), 5)
        self.table.setHorizontalHeaderLabels(["Ch", "Name", "Raw code", "Value", "Unit"])
        self.table.verticalHeader().setVisible(False)
        for row, channel in enumerate(self.schema.channels):
            for column, text in enumerate((str(channel.index + 1), channel.label, "", "", channel.unit)):
                self.table.setItem(row, column, QtWidgets.QTableWidgetItem(text))
        self.table.horizontalHeader().setStretchLastSection(True)
        right.addWidget(self.table)
        splitter.addWidget(right)
        splitter.setSizes([260, 900])
        self.curves: dict[int, pg.PlotDataItem] = {}
        units = list(dict.fromkeys(c.unit for c in self.schema.channels if c.available or c.is_lock))
        self.unit.addItems(units)

    def _choose_output(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Recording parent folder")
        if folder:
            self.output_dir.setText(folder)

    def _populate_channels(self, unit: str):
        self.channels.blockSignals(True)
        self.channels.clear()
        for channel in self.schema.channels:
            if channel.unit == unit and (channel.available or channel.is_lock):
                item = QtWidgets.QListWidgetItem(f"{channel.index + 1:2d}  {channel.label}")
                item.setData(QtCore.Qt.UserRole, channel.index)
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                item.setCheckState(QtCore.Qt.Checked)
                self.channels.addItem(item)
        self.channels.blockSignals(False)
        self.plot.clear()
        self.plot.addLegend()
        self.curves.clear()
        self.plot.setLabel("left", unit)
        self.refresh(force=True)

    # -------------------------------------------------------------- loaded data
    def set_telemetry(self, telemetry) -> None:
        self.telemetry = telemetry
        self.schema = telemetry.schema
        self.status.setText(f"Loaded {len(telemetry.mjd)} frames, "
                            f"{span_text(telemetry.mjd.min(), telemetry.mjd.max(), self.time_mode)}. "
                            + " ".join(telemetry.notes[-2:]))
        self.set_time_mode(self.time_mode)
        self.refresh(force=True)

    def set_time_mode(self, mode: str) -> None:
        """View > Time display; x data stay MJD (host receive time)."""
        self.time_mode = mode
        self.plot.getAxis("bottom").set_mode(mode)
        span = (float(self.telemetry.mjd.min()), float(self.telemetry.mjd.max())) \
            if self.telemetry is not None and len(self.telemetry.mjd) else (None, None)
        self.plot.setLabel("bottom", axis_label(mode, *span, note="(host receive time)"))

    def _data(self):
        if self.recorder is not None and self.recorder.preview_mjd:
            return self.recorder.preview_arrays(), None
        if self.telemetry is not None:
            return (self.telemetry.mjd, self.telemetry.values), self.telemetry.codes
        return None, None

    def refresh(self, force: bool = False):
        data, codes = self._data()
        if self.thread is not None and self.last_frame_time is not None:
            age = time.monotonic() - self.last_frame_time
            stale = age > 3 * self.interval.value()
            if stale:
                self.status.setText(self.status.text().split(" | STALE")[0] + f" | STALE: no frame for {age:.0f} s")
        if data is None or (not force and self.thread is None):
            return
        mjd, values = data
        if not len(mjd):
            return
        wanted = [self.channels.item(row).data(QtCore.Qt.UserRole) for row in range(self.channels.count())
                  if self.channels.item(row).checkState() == QtCore.Qt.Checked]
        for index in list(self.curves):
            if index not in wanted:
                self.plot.removeItem(self.curves.pop(index))
        for number, index in enumerate(wanted):
            if index not in self.curves:      # in-place updates afterwards
                self.curves[index] = self.plot.plot(name=self.schema.channels[index].label,
                                                    pen=pg.mkPen(PALETTE[number % len(PALETTE)], width=1.5))
            self.curves[index].setData(mjd, values[:, index])
        last_values = values[-1]
        last_codes = codes[-1] if codes is not None else None
        if self.recorder is not None and self.last_record is not None and self.last_record.frame is not None:
            last_codes = self.last_record.frame.codes
        for row in range(len(self.schema.channels)):
            value = last_values[row]
            self.table.item(row, 3).setText("" if not np.isfinite(value) else f"{value:.5g}")
            self.table.item(row, 2).setText("" if last_codes is None else str(int(last_codes[row])))

    # -------------------------------------------------------------- live polling
    last_record = None

    def _config(self):
        """(MonitorConfig, emulator or None). The caller owns the emulator."""
        from t4_monitor import MonitorConfig
        host = self.host.text().strip()
        emulator = None
        if self.use_emulator.isChecked():
            from t4_emulator import T4Emulator
            emulator = T4Emulator()
            emulator.start()
            host, port = emulator.address
        else:
            port = self.port.value()
            if not host:
                raise ValueError("Enter the T4 address, or use the loopback emulator.")
        local_port = None if emulator is not None else (self.local_port.value() or None)
        config = MonitorConfig(host, port, local_port, self.timeout.value(), 3, 1.5,
                               self.interval.value(), self.allow_prefix.isChecked(), self.approve.isChecked())
        return config, emulator

    def start(self):
        from t4_monitor import T4Monitor, T4Recorder, run_loop
        if self.thread is not None:
            return
        try:
            if not self.output_dir.text().strip():
                raise ValueError("Choose a recording parent folder first; raw frames are saved before decoding.")
            config, self.emulator = self._config()
            self.monitor = T4Monitor(config, self.schema)
            self.recorder = T4Recorder(self.output_dir.text().strip(), self.schema, config)
        except Exception as error:                         # noqa: BLE001 - shown to the operator
            self._stop_emulator()
            self.message.emit(f"T4 polling not started: {error}")
            QtWidgets.QMessageBox.warning(self, "T4 polling", str(error))
            return
        self.cancel.clear()
        self.counts = {"ok": 0, "bad": 0}
        self.thread = threading.Thread(
            target=lambda: self.bridge.finished.emit(
                f"{run_loop(self.monitor, self.recorder, self.cancel, None, self.bridge.record.emit)} polls"),
            name="T4Poller", daemon=True)
        self.thread.start()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.message.emit(f"T4 polling started; recording to {self.recorder.folder}")

    def stop(self):
        self.cancel.set()
        self.stop_button.setEnabled(False)

    def _on_finished(self, text: str):
        if self.thread is not None:
            self.thread.join(timeout=2)
        self.thread = None
        self._stop_emulator()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.message.emit(f"T4 polling stopped after {text}.")

    def _stop_emulator(self):
        if self.emulator is not None:
            self.emulator.stop()
            self.emulator = None

    def _on_record(self, record):
        self.last_record = record
        self.counts["ok" if record.ok else "bad"] += 1
        if record.ok:
            self.last_frame_time = time.monotonic()
        lock = None if record.frame is None else record.frame.locked
        from t4_monitor import process_rss_bytes
        rss = process_rss_bytes()
        self.status.setText(
            f"OK {self.counts['ok']}  failed {self.counts['bad']} | last {record.status}"
            f"{'' if record.ok else ': ' + record.error[:120]} | RTT {record.rtt_ms:.1f} ms | "
            f"main PLL {'LOCKED' if lock else ('UNLOCKED' if lock is False else 'unknown')} | preview "
            f"{self.recorder.preview_bytes()/1e6:.1f} MB" + ("" if rss is None else f" | process {rss/1e6:.0f} MB"))

    def read_state(self):
        """RDFS, RDREG=DRIFTCOMP, RDREG=LSBWEIGHT and GETID in a worker thread."""
        from t4_monitor import T4Monitor
        temporary_emulator = None
        try:
            config, temporary_emulator = self._config()
            monitor = T4Monitor(config, self.schema)
        except Exception as error:                         # noqa: BLE001
            if temporary_emulator is not None:
                temporary_emulator.stop()
            QtWidgets.QMessageBox.warning(self, "T4 state", str(error))
            return

        def work():
            answers = {}
            for key, command in (("synth_raw", b"RDFS;\r\n"), ("driftcomp", b"RDREG=DRIFTCOMP;\r\n"),
                                 ("lsbweight", b"RDREG=LSBWEIGHT;\r\n"), ("id", b"GETID;\r\n")):
                record, answer = monitor.query(command)
                answers[key] = answer if answer is not None else f"ERROR {record.status}: {record.error}"
            try:
                answers["synth_hz"] = synth_hz_from_reply(answers["synth_raw"])
            except ValueError:
                answers["synth_hz"] = None
            if temporary_emulator is not None:
                temporary_emulator.stop()
            self.bridge.state.emit(answers)
        threading.Thread(target=work, name="T4State", daemon=True).start()

    def _on_state(self, answers: dict):
        self.message.emit("T4 state (read-only): " + ", ".join(f"{k}={v}" for k, v in answers.items()))
        self.stateRead.emit(answers)

    def flush_ram(self):
        if self.recorder is None:
            return
        freed = self.recorder.flush_ram()
        self.message.emit(f"T4 preview flushed ({freed/1e6:.2f} MB); data remain in {self.recorder.folder}.")

    def offload(self):
        if self.recorder is None:
            return
        path = self.recorder.offload_pickle()
        self.message.emit(f"T4 preview offloaded to {path}." if path else "Nothing to offload.")

    def shutdown(self):
        self.cancel.set()
        if self.thread is not None:
            self.thread.join(timeout=3)
        self._stop_emulator()

    def set_theme(self, background: str, foreground: str):
        self.plot.setBackground(background)
        for side in ("left", "bottom"):
            self.plot.getAxis(side).setPen(pg.mkPen(foreground))
            self.plot.getAxis(side).setTextPen(pg.mkPen(foreground))
