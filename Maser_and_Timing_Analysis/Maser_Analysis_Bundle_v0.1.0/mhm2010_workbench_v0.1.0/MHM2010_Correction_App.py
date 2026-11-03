#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace
# %% ===========================================================================
#    PROGRAM HEADER
# =============================================================================
"""Interactive MHM2010 timing, telemetry and correction workbench.

===============================================================================
 Title          : MHM2010 Correction Workbench
 Filename       : MHM2010_Correction_App.py
 Version        : 0.1.0 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-03
 Modified       : 2026-11-03
 Language       : Python 3.12
 GUI            : qtpy abstraction; PySide6 default; pyqtgraph plots
 Requires       : See requirements.txt
 Licence        : GPL-3.0-or-later; see LICENSE
 Status         : Offline engineering analysis; no serial/network control
 Revision       : 0.1.0 Initial FITS-first MHM2010 release.
===============================================================================
PURPOSE
-------
Select FITS/FITS.GZ timing files and HMD housekeeping, inspect time intervals,
fit phase slope/curvature, review BOTH masermon drift-unit options, and export
native Veusz HDF5, Stable32, CSV and JSON. No Veusz import is needed.

SAFETY
------
All correction values are advisory, not sent to the maser. Historical data
cannot establish today's DDS state. The operator supplies the current DDS
and drift at the intended application epoch and verifies the sign convention.

REFERENCES
----------
[1] Microsemi, MHM-2010 User's Guide, 75666-201, rev. J, Oct. 2014.
    https://www.gb.nrao.edu/~fghigo/timer/MSMmaser/Maser_UG_75666-201_Rev_J.PDF
[2] W. J. Riley, Handbook of Frequency Stability Analysis, NIST SP 1065, 2008.
    https://tf.nist.gov/general/pdf/2220.pdf
"""
from __future__ import annotations
import os
os.environ.setdefault("QT_API", "pyside6")
# pyqtgraph must select the same backend as qtpy, including an explicit override.
os.environ.setdefault("PYQTGRAPH_QT_LIB",
                      {"pyside6": "PySide6", "pyqt6": "PyQt6"}.get(
                          os.environ["QT_API"].lower(), "PySide6"))
import sys
import json
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
from qtpy import QtCore, QtGui, QtWidgets
import pyqtgraph as pg
from astropy.time import Time
from maser_io import load_timing, load_hmd, overview_series, TimingSeries, TimingCollection
from maser_core import fit_phase, correction, stability, segments, telemetry_events, window_sensitivity
from maser_export import export_bundle, json_safe

__version__ = "0.1.0"
pg.setConfigOptions(antialias=True, background="#101b29", foreground="#dce5ef")


# %% ===========================================================================
#    WORKER AND REUSABLE UI HELPERS
# =============================================================================
class WorkerSignals(QtCore.QObject):
    finished = QtCore.Signal(object)
    failed = QtCore.Signal(str)


class Worker(QtCore.QRunnable):
    """Run file I/O off the GUI thread; only signals cross the Qt boundary."""
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = WorkerSignals()

    @QtCore.Slot()
    def run(self):
        try:
            self.signals.finished.emit(self.function())
        except Exception as error:
            self.signals.failed.emit(f"{type(error).__name__}: {error}")


def spin(minimum, maximum, decimals, value):
    widget = QtWidgets.QDoubleSpinBox()
    widget.setRange(minimum, maximum)
    widget.setDecimals(decimals)
    widget.setValue(value)
    widget.setKeyboardTracking(False)
    return widget


def plot(title, ylabel="Time error (ns)"):
    widget = pg.PlotWidget(title=title)
    widget.setLabel("bottom", "MJD (UTC)")
    widget.setLabel("left", ylabel)
    widget.showGrid(x=True, y=True, alpha=0.16)
    widget.getPlotItem().setClipToView(True)
    widget.getPlotItem().setDownsampling(auto=True, mode="peak")
    return widget


class DecadeAxis(pg.AxisItem):
    """Label only log decades; prevent crowded SI-scaled sub-decade ticks."""
    def logTickStrings(self, values, scale, spacing):
        return [f"1e{int(round(value))}" if abs(value-round(value)) < 1e-6 else ""
                for value in values]


# %% ===========================================================================
#    MAIN WINDOW AND FILE INGESTION
# =============================================================================
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"MHM2010 Correction Workbench {__version__} | OFFLINE")
        self.resize(1500, 960)
        self.collection = None
        self.telemetry = None
        self.series = None
        self.fit = None
        self.candidate = None
        self.stability_result = None
        self.exclusions = []
        self.loading = False
        self.pool = QtCore.QThreadPool.globalInstance()
        self.active_workers = set()
        self.refit_timer = QtCore.QTimer(self)
        self.refit_timer.setSingleShot(True)
        self.refit_timer.setInterval(180)
        self.refit_timer.timeout.connect(self.refit)
        self._build()
        self._style()
        self.setAcceptDrops(True)

    def _build(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        banner = QtWidgets.QLabel("MHM2010   /   TIMING & TELEMETRY WORKBENCH")
        banner.setObjectName("banner")
        layout.addWidget(banner)
        subhead = QtWidgets.QLabel("FITS-first analysis  •  selectable phase fits  •  both drift units  •  no instrument writes")
        layout.addWidget(subhead)
        splitter = QtWidgets.QSplitter()
        layout.addWidget(splitter, 1)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(410)
        scroll.setMaximumWidth(470)
        side = QtWidgets.QWidget()
        side_layout = QtWidgets.QVBoxLayout(side)
        scroll.setWidget(side)
        splitter.addWidget(scroll)
        inputs = QtWidgets.QGroupBox("INPUTS")
        grid = QtWidgets.QVBoxLayout(inputs)
        self.open_button = QtWidgets.QPushButton("Open timing files: FITS / Franks")
        self.open_button.clicked.connect(self.choose_timing)
        grid.addWidget(self.open_button)
        self.hmd_button = QtWidgets.QPushButton("Open HMD telemetry")
        self.hmd_button.clicked.connect(self.choose_hmd)
        grid.addWidget(self.hmd_button)
        self.channel = QtWidgets.QComboBox()
        self.channel.currentTextChanged.connect(self.select_channel)
        grid.addWidget(self.channel)
        self.run_choice = QtWidgets.QComboBox()
        self.run_choice.currentIndexChanged.connect(self.select_run)
        grid.addWidget(self.run_choice)
        side_layout.addWidget(inputs)
        fit_box = QtWidgets.QGroupBox("FIT SELECTION")
        form = QtWidgets.QFormLayout(fit_box)
        self.low = spin(1, 100000, 9, 60000)
        self.high = spin(1, 100000, 9, 60001)
        form.addRow("Start MJD", self.low)
        form.addRow("End MJD", self.high)
        self.low.valueChanged.connect(self.numeric_selection)
        self.high.valueChanged.connect(self.numeric_selection)
        self.order = QtWidgets.QComboBox()
        self.order.addItems(["Linear phase", "Quadratic phase"])
        self.order.setToolTip("Linear phase estimates frequency only. Quadratic phase estimates frequency and drift.")
        self.order.setCurrentIndex(1)
        self.order.currentIndexChanged.connect(self.queue_fit)
        form.addRow("Model", self.order)
        self.clip = spin(0, 10, 1, 0)
        self.clip.setToolTip("0 disables residual MAD clipping. Clipping changes the noise statistics.")
        self.clip.valueChanged.connect(self.queue_fit)
        form.addRow("MAD clip sigma", self.clip)
        self.exclude_toggle = QtWidgets.QCheckBox("Show red exclusion selector")
        self.exclude_toggle.toggled.connect(lambda checked: self.exclude_region.setVisible(checked))
        form.addRow(self.exclude_toggle)
        exclude_button = QtWidgets.QPushButton("Exclude red interval")
        exclude_button.clicked.connect(self.add_exclusion)
        clear_button = QtWidgets.QPushButton("Clear exclusions")
        clear_button.clicked.connect(self.clear_exclusions)
        form.addRow(exclude_button, clear_button)
        sensitivity_button = QtWidgets.QPushButton("Compare 7 / 14 / 21 / 30 day fits")
        sensitivity_button.clicked.connect(self.compare_windows)
        form.addRow(sensitivity_button)
        side_layout.addWidget(fit_box)
        state_box = QtWidgets.QGroupBox("SETTINGS AT APPLICATION EPOCH")
        state = QtWidgets.QFormLayout(state_box)
        self.apply_epoch = spin(1, 100000, 9, 60000)
        self.apply_epoch.valueChanged.connect(self.refresh_candidate)
        state.addRow("MJD UTC", self.apply_epoch)
        self.dds = QtWidgets.QLineEdit()
        self.dds.setPlaceholderText("Required: nnnn.nnnnnnn")
        self.dds.textChanged.connect(self.refresh_candidate)
        state.addRow("External DDS", self.dds)
        nearest_button = QtWidgets.QPushButton("Read nearby HMD DDS (within 120 s)")
        nearest_button.clicked.connect(self.nearest_dds)
        state.addRow(nearest_button)
        self.drift = QtWidgets.QLineEdit()
        self.drift.setPlaceholderText("Required current drift field")
        self.drift.textChanged.connect(self.refresh_candidate)
        state.addRow("Current drift", self.drift)
        self.drift_units = QtWidgets.QComboBox()
        self.drift_units.addItems(["pp18/day", "DDS counts/day"])
        self.drift_units.currentIndexChanged.connect(self.refresh_candidate)
        state.addRow("Input units", self.drift_units)
        self.drift_active = QtWidgets.QCheckBox("Existing drift enabled")
        self.drift_active.setChecked(True)
        self.drift_active.toggled.connect(self.refresh_candidate)
        state.addRow(self.drift_active)
        self.phase_sign = QtWidgets.QComboBox()
        self.phase_sign.addItems(["+ measured", "- measured"])
        self.phase_sign.setToolTip("Multiplier converting the measured channel to maser clock time minus reference clock time.")
        self.phase_sign.currentIndexChanged.connect(self.refresh_candidate)
        state.addRow("Phase sign", self.phase_sign)
        self.drift_sign = QtWidgets.QComboBox()
        self.drift_sign.addItems(["Positive raises output", "Positive raises DDS"])
        self.drift_sign.setToolTip("Installed masermon pp18/day sign convention. Rev. J does not state it; verify before entry.")
        self.drift_sign.currentIndexChanged.connect(self.refresh_candidate)
        state.addRow("pp18 sign", self.drift_sign)
        self.confirm = QtWidgets.QCheckBox("I verified channel/sign, DDS, enabled state,\n"
                                             "drift convention and epoch against masermon")
        self.confirm.toggled.connect(self.refresh_candidate)
        state.addRow(self.confirm)
        self.state_review = QtWidgets.QCheckBox("I reviewed reference behavior, HMD and steps")
        self.state_review.toggled.connect(self.refresh_candidate)
        state.addRow(self.state_review)
        side_layout.addWidget(state_box)
        export_box = QtWidgets.QGroupBox("OUTPUT")
        export_layout = QtWidgets.QVBoxLayout(export_box)
        self.stable32_check = QtWidgets.QCheckBox("Include Stable32 phase + fractional frequency")
        export_layout.addWidget(self.stable32_check)
        export_button = QtWidgets.QPushButton("Export new analysis folder")
        export_button.clicked.connect(self.choose_export)
        export_layout.addWidget(export_button)
        config_button = QtWidgets.QPushButton("Save / load analysis settings")
        config_button.clicked.connect(self.config_menu)
        export_layout.addWidget(config_button)
        side_layout.addWidget(export_box)
        side_layout.addStretch()
        self.tabs = QtWidgets.QTabWidget()
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(1, 1)
        fit_page = QtWidgets.QWidget()
        fit_layout = QtWidgets.QVBoxLayout(fit_page)
        self.results = QtWidgets.QPlainTextEdit()
        self.results.setReadOnly(True)
        self.results.setMaximumHeight(220)
        self.results.setPlainText("Select timing FITS files to begin. HMD alone cannot measure maser frequency error.")
        fit_layout.addWidget(self.results)
        self.phase_plot = plot("Measured clock time error and selectable fit")
        self.phase_curve = self.phase_plot.plot(pen=None, symbol="o", symbolSize=3,
                                                symbolBrush="#67b7d5", symbolPen=None)
        self.fit_curve = self.phase_plot.plot(pen=pg.mkPen("#f2c078", width=2))
        self.region = pg.LinearRegionItem([60000, 60001], brush=(57, 167, 192, 35))
        self.region.sigRegionChanged.connect(self.region_selection)
        self.phase_plot.addItem(self.region)
        self.exclude_region = pg.LinearRegionItem([60000, 60001], brush=(224, 95, 91, 65))
        self.exclude_region.setVisible(False)
        self.phase_plot.addItem(self.exclude_region)
        fit_layout.addWidget(self.phase_plot, 3)
        self.residual_plot = plot("Fit residual")
        self.residual_plot.setXLink(self.phase_plot)
        self.residual_curve = self.residual_plot.plot(pen=None, symbol="o", symbolSize=3,
                                                     symbolBrush="#a9cba0", symbolPen=None)
        fit_layout.addWidget(self.residual_plot, 2)
        self.tabs.addTab(fit_page, "Fit & corrections")
        overview_page = QtWidgets.QWidget()
        overview_layout = QtWidgets.QVBoxLayout(overview_page)
        overview_layout.addWidget(QtWidgets.QLabel(
            "Raw channel pairs remain available in the selector and export. Overview uses UTC-hour medians."))
        self.overview = pg.GraphicsLayoutWidget()
        overview_layout.addWidget(self.overview)
        self.tabs.addTab(overview_page, "Timing overview")
        telemetry_page = QtWidgets.QWidget()
        telemetry_layout = QtWidgets.QVBoxLayout(telemetry_page)
        self.telemetry_channel = QtWidgets.QComboBox()
        self.telemetry_channel.currentTextChanged.connect(self.draw_telemetry)
        telemetry_layout.addWidget(self.telemetry_channel)
        self.telemetry_label = QtWidgets.QLabel("HMD housekeeping is context, not a clock-error measurement.")
        self.telemetry_label.setWordWrap(True)
        telemetry_layout.addWidget(self.telemetry_label)
        self.telemetry_plot = plot("Housekeeping", "Raw logged value")
        telemetry_layout.addWidget(self.telemetry_plot)
        self.dds_plot = plot("External DDS changes", "DDS setting")
        telemetry_layout.addWidget(self.dds_plot)
        self.tabs.addTab(telemetry_page, "HMD telemetry")
        stability_page = QtWidgets.QWidget()
        stability_layout = QtWidgets.QVBoxLayout(stability_page)
        stability_controls = QtWidgets.QHBoxLayout()
        self.detrend = QtWidgets.QComboBox()
        self.detrend.addItems(["Raw phase", "Remove mean", "Remove linear phase", "Remove quadratic phase"])
        stability_controls.addWidget(self.detrend)
        stability_controls.addWidget(QtWidgets.QLabel("Sampling tolerance (%)"))
        self.tolerance = spin(0.01, 5, 2, 1)
        stability_controls.addWidget(self.tolerance)
        calculate = QtWidgets.QPushButton("Calculate selected samples")
        calculate.clicked.connect(self.calculate_stability)
        stability_controls.addWidget(calculate)
        stability_layout.addLayout(stability_controls)
        self.stability_note = QtWidgets.QLabel("OADEV / MDEV / OHDEV and TDEV characterize the comparison system, not isolated maser noise.")
        self.stability_note.setWordWrap(True)
        stability_layout.addWidget(self.stability_note)
        self.frequency_plot = pg.PlotWidget(title="Frequency stability",
            axisItems={"bottom": DecadeAxis("bottom"), "left": DecadeAxis("left")})
        self.frequency_plot.setLabel("left", "Fractional frequency deviation")
        self.frequency_plot.setLabel("bottom", "Averaging time (s)")
        self.frequency_plot.setLogMode(True, True)
        for axis in ("left", "bottom"):
            self.frequency_plot.getAxis(axis).enableAutoSIPrefix(False)
        self.frequency_plot.showGrid(x=True, y=True, alpha=.15)
        self.frequency_plot.addLegend()
        stability_layout.addWidget(self.frequency_plot)
        self.tdev_plot = pg.PlotWidget(title="Time deviation",
            axisItems={"bottom": DecadeAxis("bottom"), "left": DecadeAxis("left")})
        self.tdev_plot.setLabel("left", "TDEV (s)")
        self.tdev_plot.setLabel("bottom", "Averaging time (s)")
        self.tdev_plot.setLogMode(True, True)
        for axis in ("left", "bottom"):
            self.tdev_plot.getAxis(axis).enableAutoSIPrefix(False)
        self.tdev_plot.showGrid(x=True, y=True, alpha=.15)
        stability_layout.addWidget(self.tdev_plot)
        self.tabs.addTab(stability_page, "Stability")
        self.audit = QtWidgets.QPlainTextEdit()
        self.audit.setReadOnly(True)
        self.tabs.addTab(self.audit, "Audit & guidance")
        for signal in (self.apply_epoch.valueChanged, self.dds.textChanged, self.drift.textChanged,
                       self.drift_units.currentIndexChanged, self.drift_active.toggled,
                       self.phase_sign.currentIndexChanged, self.drift_sign.currentIndexChanged):
            signal.connect(self.invalidate_settings_review)
        self.detrend.currentIndexChanged.connect(self.invalidate_stability)
        self.tolerance.valueChanged.connect(self.invalidate_stability)
        self.statusBar().showMessage("Offline analysis. Drag timing or HMD files into the window.")

    def _style(self):
        self.setStyleSheet("""
            QMainWindow,QWidget { background:#142132; color:#e2eaf2; font-size:12px; }
            QLabel#banner { font-size:22px; font-weight:600; padding:8px 0; color:#9cdae5; }
            QGroupBox { border:1px solid #35465b; border-radius:5px; margin-top:15px; padding-top:12px; }
            QGroupBox::title { subcontrol-origin:margin; left:10px; color:#9cdae5; }
            QPushButton { background:#284a60; border:1px solid #477089; padding:7px; border-radius:4px; }
            QPushButton:hover { background:#34637a; }
            QLineEdit,QComboBox,QDoubleSpinBox,QPlainTextEdit { background:#101b29; border:1px solid #35465b; padding:4px; }
            QTabBar::tab { padding:10px 14px; background:#203148; }
            QTabBar::tab:selected { background:#34566c; color:#ffffff; }
            QScrollArea { border:0; } QSplitter::handle { background:#35465b; }
        """)

    def log(self, message):
        self.audit.appendPlainText(datetime.now(timezone.utc).strftime("%H:%M:%S UTC")+"  "+message)

    def error(self, message):
        self.log(message)
        QtWidgets.QMessageBox.warning(self, "Analysis check", message)

    def run_worker(self, function, callback):
        worker = Worker(function)
        self.active_workers.add(worker)
        self.open_button.setEnabled(False)
        self.hmd_button.setEnabled(False)
        def finished(result):
            self.active_workers.discard(worker)
            self.open_button.setEnabled(True)
            self.hmd_button.setEnabled(True)
            callback(result)
        def failed(message):
            self.active_workers.discard(worker)
            self.open_button.setEnabled(True)
            self.hmd_button.setEnabled(True)
            self.error(message)
        worker.signals.finished.connect(finished)
        worker.signals.failed.connect(failed)
        self.pool.start(worker)

    def choose_timing(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Select timing files", "", "Timing (*.fits *.fits.gz *.fit *.fit.gz *.t00new);;All files (*)")
        if paths:
            self.run_worker(lambda: load_timing(paths), self.accept_timing)

    def choose_hmd(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Select HMD telemetry", "", "HMD (*.HMD *.hmd)")
        if paths:
            self.run_worker(lambda: load_hmd(paths), self.accept_hmd)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if self.active_workers:
            return
        if paths and all(path.lower().endswith(".hmd") for path in paths):
            self.run_worker(lambda: load_hmd(paths), self.accept_hmd)
        elif paths:
            self.run_worker(lambda: load_timing(paths), self.accept_timing)

    def accept_timing(self, collection):
        self.collection = collection
        self.channel.blockSignals(True)
        self.channel.clear()
        self.channel.addItems(collection.series)
        preferred = "MicroSem -> CNS_GPS1" if "MicroSem -> CNS_GPS1" in collection.series else "MSM-GPS"
        if preferred in collection.series:
            self.channel.setCurrentText(preferred)
        self.channel.blockSignals(False)
        self.select_channel(self.channel.currentText())
        self.draw_overview()
        for note in collection.notes:
            self.log(note)

    def accept_hmd(self, telemetry):
        self.telemetry = telemetry
        self.state_review.setChecked(False)
        self.telemetry_channel.clear()
        self.telemetry_channel.addItems([name for name in telemetry.frame if name != "MJD"])
        self.telemetry_channel.setCurrentText("IFAmp_0")
        for note in telemetry.notes:
            self.log(note)
        self.draw_telemetry()
        self.refresh_candidate()

    def select_channel(self, name):
        if not self.collection or name not in self.collection.series:
            return
        self.series = self.collection.series[name]
        self.confirm.setChecked(False)
        self.state_review.setChecked(False)
        self.exclusions.clear()
        self.phase_curve.setData(self.series.mjd, self.series.phase_s*1e9)
        self.run_choice.blockSignals(True)
        self.run_choice.clear()
        for number, indices in enumerate(segments(self.series.mjd)):
            self.run_choice.addItem(f"Run {number+1}: {len(indices)} samples / "
                                    f"{self.series.mjd[indices[-1]]-self.series.mjd[indices[0]]:.2f} d", indices)
        self.run_choice.setCurrentIndex(self.run_choice.count()-1)
        self.run_choice.blockSignals(False)
        self.select_run(self.run_choice.currentIndex())

    def select_run(self, index):
        if self.series is None or index < 0:
            return
        indices = self.run_choice.itemData(index)
        low, high = self.series.mjd[indices[[0, -1]]]
        self.region.setBounds((float(self.series.mjd[0]), float(self.series.mjd[-1])))
        self.region.setRegion((float(low), float(high)))
        self.exclude_region.setRegion((float(low+(high-low)*0.45), float(low+(high-low)*0.55)))
        self.apply_epoch.setValue(float(high))
        self.phase_plot.setXRange(low, high, padding=0.02)
        self.refit()

    def numeric_selection(self):
        if self.series is not None:
            self.region.setRegion((self.low.value(), self.high.value()))

    def region_selection(self):
        low, high = self.region.getRegion()
        for control, value in ((self.low, low), (self.high, high)):
            control.blockSignals(True)
            control.setValue(value)
            control.blockSignals(False)
        self.queue_fit()

    def queue_fit(self, *_):
        self.confirm.setChecked(False)
        self.state_review.setChecked(False)
        self.refit_timer.start()

    def add_exclusion(self):
        self.exclusions.append(tuple(self.exclude_region.getRegion()))
        self.log(f"Excluded MJD interval {self.exclusions[-1]}.")
        self.refit()

    def clear_exclusions(self):
        self.exclusions.clear()
        self.refit()


# %% ===========================================================================
#    FIT, SAFETY REVIEW AND BOTH DRIFT UNITS
# =============================================================================
    def refit(self):
        self.refit_timer.stop()
        self.fit = self.candidate = self.stability_result = None
        self.frequency_plot.clear()
        self.tdev_plot.clear()
        self.stability_note.setText("Selection/model changed. Recalculate stability for the current fitted samples.")
        if self.series is None:
            return
        try:
            self.fit = fit_phase(self.series, self.low.value(), self.high.value(),
                                 self.order.currentIndex()+1, self.exclusions, self.clip.value())
            mask = self.fit.selected
            self.fit_curve.setData(self.series.mjd[mask], self.fit.predicted_s[mask]*1e9)
            self.residual_curve.setData(self.series.mjd[mask], self.fit.residual_s[mask]*1e9)
            self.refresh_candidate()
        except Exception as error:
            self.fit_curve.clear()
            self.residual_curve.clear()
            self.results.setPlainText(str(error))

    def invalidate_settings_review(self, *_):
        self.confirm.setChecked(False)

    def invalidate_stability(self, *_):
        self.stability_result = None
        self.frequency_plot.clear()
        self.tdev_plot.clear()
        self.stability_note.setText("Stability options changed. Recalculate before exporting statistics.")

    def refresh_candidate(self, *_):
        self.candidate = None
        if self.fit is None:
            return
        fit = self.fit
        frequency, drift, frequency_sigma, drift_sigma = fit.frequency(self.apply_epoch.value())
        sign = 1 if self.phase_sign.currentIndex() == 0 else -1
        lines = [f"{self.series.label} | {int(fit.used.sum())} fitted samples | RMS {fit.rms_ns:.4g} ns",
                 f"At MJD {self.apply_epoch.value():.9f}: y = {sign*frequency:+.7e}",
                 (f"dy/day = {sign*drift:+.7e} = {sign*drift*1e18:+.4f} parts in 1e18/day"
                  if fit.order == 2 else "Linear fit: drift is not estimated; do not change the drift setting.")]
        reasons = []
        if (self.apply_epoch.value() < self.series.mjd[fit.used].min()-1e-8 or
                self.apply_epoch.value() > self.series.mjd[fit.used].max()+1e-8):
            reasons.append("Application epoch lies outside fitted data; extrapolation requires independent review.")
        eligible = self.series.label in {"MicroSem -> CNS_GPS1", "MicroSem -> CNS2018",
                                          "MicroSem -> CNS_GPS2", "CNS_GPS1 -> MicroSem",
                                          "CNS2018 -> MicroSem", "CNS_GPS2 -> MicroSem", "MSM-GPS"}
        if not eligible:
            reasons.append("Not an identified MicroSem/GPS comparison; correction fields are blocked.")
        expected_sign = -1 if self.series.label.startswith(("CNS_GPS1 ->", "CNS2018 ->", "CNS_GPS2 ->")) else 1
        if eligible and sign != expected_sign:
            reasons.append("Phase sign disagrees with the selected channel direction; resolve before entry.")
        if self.telemetry is None:
            reasons.append("No HMD loaded; operating-state review is incomplete.")
        else:
            frame = self.telemetry.frame
            lo, hi = self.series.mjd[fit.used][[0, -1]]
            if frame.MJD.min() > lo or frame.MJD.max() < hi:
                reasons.append("HMD does not cover the selected timing span.")
            events = telemetry_events(self.telemetry)
            count = int(((events.MJD >= lo) & (events.MJD <= hi)).sum())
            if count:
                reasons.append(f"{count} DDS/selector events in fitted interval: select a clean segment or review explicitly.")
        try:
            value = float(self.drift.text()) if self.drift_active.isChecked() else 0.
            self.candidate = correction(fit, self.dds.text(), value, self.apply_epoch.value(),
                                        self.drift_units.currentText(),
                                        "output" if self.drift_sign.currentIndex() == 0 else "dds",
                                        self.drift_active.isChecked(), sign)
            self.candidate["review_flags"] = reasons + fit.warnings
            self.candidate["operator_confirmed"] = self.confirm.isChecked()
            self.candidate["operating_state_reviewed"] = self.state_review.isChecked()
            if not eligible or sign != expected_sign:
                self.candidate = None
            else:
                lines.append(f"External DDS: {self.candidate['new_external_dds']} "
                             f"(increment {self.candidate['delta_dds_counts_unrounded']:+.5f} least-significant digits)")
                both = self.candidate["both_drift_units"]
                if both:
                    for key, label in (("DDS_units_per_day", "DDS units/day"),
                                       ("parts_in_1e18_per_day", "parts in 1e18/day")):
                        item = both[key]
                        lines.append(f"{label}: NEW {item['new']:+.6f}; change {item['increment']:+.6f}; "
                                     f"nearest integer {item['new_nearest_integer']:+d}")
                reviewed = self.confirm.isChecked() and self.state_review.isChecked()
                lines.append("ADVISORY ONLY: "+("operator review acknowledged." if reviewed else
                                               "NOT REVIEWED. Do not enter these values."))
        except Exception as error:
            lines.append("Correction candidate unavailable: "+str(error))
        lines.extend(reasons)
        lines.append("Conditional OLS uncertainties only; see audit/manual. GPS noise is included.")
        self.results.setPlainText("\n".join(lines))
        self.statusBar().showMessage(f"Fit epoch MJD {fit.epoch_mjd:.9f} | {int(fit.used.sum())} points | no instrument connection")

    def nearest_dds(self):
        if self.telemetry is None:
            self.error("Load HMD telemetry first.")
            return
        frame = self.telemetry.frame
        position = int(np.argmin(abs(frame.MJD-self.apply_epoch.value())))
        distance = abs(frame.MJD.iloc[position]-self.apply_epoch.value())*86400
        if distance > 120:
            self.error(f"Nearest HMD record is {distance:.1f} s away; no DDS setting copied.")
            return
        value = frame.ExtSynth_32.iloc[position]
        self.dds.setText(f"{value:.7f}")
        self.confirm.setChecked(False)
        self.log(f"Copied historical HMD DDS from MJD {frame.MJD.iloc[position]:.9f}; drift remains operator-supplied.")

    def draw_overview(self):
        self.overview.clear()
        if self.collection is None:
            return
        for index, (name, series) in enumerate(overview_series(self.collection).items()):
            pane = self.overview.addPlot(row=index//3, col=index%3, title=name)
            pane.setLabel("left", "ns")
            pane.setLabel("bottom", "MJD UTC")
            pane.showGrid(x=True, y=True, alpha=0.15)
            pane.plot(series.mjd, series.phase_s*1e9, pen=pg.mkPen("#67b7d5"))

    def draw_telemetry(self, *_):
        self.telemetry_plot.clear()
        self.dds_plot.clear()
        if self.telemetry is None:
            return
        frame = self.telemetry.frame
        name = self.telemetry_channel.currentText()
        if name in frame:
            self.telemetry_plot.plot(frame.MJD, frame[name], pen=pg.mkPen("#67b7d5"))
            self.telemetry_plot.setLabel("left", name+" (raw)")
        self.dds_plot.plot(frame.MJD, frame.ExtSynth_32, pen=pg.mkPen("#f2c078"))
        events = telemetry_events(self.telemetry)
        self.dds_plot.plot(events.MJD.to_numpy(), events.ExtSynth_32.to_numpy(), pen=None, symbol="o",
                           symbolBrush="#e78179", symbolSize=6)
        self.telemetry_label.setText(
            f"HMD MJD {frame.MJD.min():.6f} to {frame.MJD.max():.6f}; {len(frame)} rows; "
            f"{len(events)} DDS/selector events (>50 DDS least-significant digits for steps). "
            "Alarm/status voltages are not decoded booleans. A gradual ramp is not a step.")

    def calculate_stability(self):
        if self.refit_timer.isActive():
            self.refit()
        if self.fit is None:
            self.error("Select a valid fit interval first.")
            return
        try:
            mask = self.fit.used
            self.stability_result = stability(self.series.mjd[mask], self.series.phase_s[mask],
                                               self.detrend.currentIndex()-1, self.tolerance.value()/100)
            self.frequency_plot.clear()
            self.tdev_plot.clear()
            for (name, values), color in zip(self.stability_result["estimators"].items(),
                                             ["#67b7d5", "#f2c078", "#a9cba0", "#d59ada"]):
                target = self.tdev_plot if name == "TDEV" else self.frequency_plot
                target.plot(values["tau_s"], values["deviation"], pen=pg.mkPen(color, width=2),
                            symbol="o", symbolSize=4, name=name)
            self.stability_note.setText(
                f"tau0 = {self.stability_result['tau0_s']:.9f} s; maximum grid error = "
                f"{self.stability_result['grid_error_s']:.6f} s. No interpolation. "
                "At least 10 overlapping terms per point; term count is NOT independent EDF. "
                f"Detrend: {self.detrend.currentText()}. Comparison noise includes GPS and instrumentation.")
        except Exception as error:
            self.stability_result = None
            self.error(str(error))

    def compare_windows(self):
        if self.fit is None:
            self.error("Select a continuous interval first.")
            return
        try:
            rows = window_sensitivity(self.series, self.low.value(), self.high.value(), self.exclusions)
            self.log("Window sensitivity: unclipped quadratic fits; raw channel sign; not a confidence interval.")
            for row in rows:
                self.log(f"{row['requested_days']} d requested / {row['actual_span_days']:.3f} d actual: "
                         f"y(end)={row['fractional_frequency_at_end']:+.5e}, "
                         f"drift={row['drift_pp18_per_day']:+.3f} pp18/day, RMS={row['rms_ns']:.3f} ns")
            self.tabs.setCurrentIndex(4)
        except Exception as error:
            self.error(str(error))


# %% ===========================================================================
#    EXPORT AND REPRODUCIBLE SETTINGS
# =============================================================================
    def settings(self):
        return {"version": __version__, "channel": self.channel.currentText(),
                "low_mjd": self.low.value(), "high_mjd": self.high.value(),
                "order": self.order.currentIndex()+1, "clip_sigma": self.clip.value(),
                "exclusions": self.exclusions, "application_mjd": self.apply_epoch.value(),
                "dds": self.dds.text(), "drift": self.drift.text(),
                "drift_units": self.drift_units.currentIndex(), "drift_enabled": self.drift_active.isChecked(),
                "phase_sign": self.phase_sign.currentIndex(), "drift_sign": self.drift_sign.currentIndex(),
                "detrend": self.detrend.currentIndex(), "sampling_tolerance_percent": self.tolerance.value(),
                "stable32": self.stable32_check.isChecked()}

    def choose_export(self):
        if self.fit is None:
            self.error("No valid fit to export.")
            return
        parent = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose output parent directory")
        if not parent:
            return
        target = Path(parent)/("MHM2010_analysis_"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ"))
        try:
            export_bundle(target, self.series, self.fit, self.candidate, self.telemetry,
                          self.stability_result, self.settings(), self.stable32_check.isChecked(),
                          self.tolerance.value()/100, self.collection)
            self.log(f"Created {target.name}; no existing files replaced.")
            QtWidgets.QMessageBox.information(self, "Export complete", f"Created {target}")
        except Exception as error:
            self.error(str(error))

    def config_menu(self):
        choice = QtWidgets.QMessageBox.question(self, "Analysis settings",
            "Save current settings? Choose No to load settings after loading the input files.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel)
        if choice == QtWidgets.QMessageBox.Yes:
            name, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save settings", "mhm_settings.json", "JSON (*.json)")
            if name:
                Path(name).write_text(json.dumps(self.settings(), indent=2), encoding="utf-8")
        elif choice == QtWidgets.QMessageBox.No:
            name, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load settings", "", "JSON (*.json)")
            if name:
                try:
                    self.load_settings(json.loads(Path(name).read_text(encoding="utf-8")))
                except Exception as error:
                    self.error(str(error))

    def load_settings(self, settings):
        if self.collection is None or settings["channel"] not in self.collection.series:
            raise ValueError("Load the original timing inputs first; saved settings contain no local paths.")
        self.channel.setCurrentText(settings["channel"])
        self.region.setRegion((settings["low_mjd"], settings["high_mjd"]))
        self.order.setCurrentIndex(settings["order"]-1)
        self.clip.setValue(settings["clip_sigma"])
        self.exclusions = settings["exclusions"]
        self.apply_epoch.setValue(settings["application_mjd"])
        self.dds.setText(settings["dds"])
        self.drift.setText(settings["drift"])
        self.drift_units.setCurrentIndex(settings["drift_units"])
        self.drift_active.setChecked(settings["drift_enabled"])
        self.phase_sign.setCurrentIndex(settings["phase_sign"])
        self.drift_sign.setCurrentIndex(settings["drift_sign"])
        self.detrend.setCurrentIndex(settings["detrend"])
        self.tolerance.setValue(settings["sampling_tolerance_percent"])
        self.stable32_check.setChecked(settings["stable32"])
        self.confirm.setChecked(False)
        self.state_review.setChecked(False)
        self.refit()

    def closeEvent(self, event):
        if self.active_workers:
            QtWidgets.QMessageBox.information(self, "Input in progress", "Wait for the current file read to finish before closing.")
            event.ignore()
        else:
            event.accept()


def main():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    application.setApplicationName("MHM2010 Correction Workbench")
    window = MainWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
