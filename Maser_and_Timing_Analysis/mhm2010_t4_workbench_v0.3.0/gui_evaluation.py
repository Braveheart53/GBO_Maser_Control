#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Further-evaluation tabs of the workbench GUI (0.3.0).

===============================================================================
 Title          : Evaluation Tabs (gap-aware stability, steering removal,
                  Kalman estimate, three-cornered hat)
 Filename       : gui_evaluation.py
 Version        : 0.0.1 (Semantic Versioning 2.0.0; first internal draft)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-10
 Modified       : 2026-11-10
 Language       : Python 3.12 (3.13 tested)
 Requires       : qtpy (PySide6), pyqtgraph, numpy; maser_eval, maser_core
 Licence        : GPL-3.0-or-later; see LICENSE
 Revision       : 0.0.1 Four tabs for further evaluation. They read the
                  loaded data and the base fit through a context callable
                  and NEVER change the base fit, the Results box or the
                  suggested settings; every result is labelled "evaluation".
===============================================================================

Each tab keeps its last result (``result``) for the Veusz export; the main
window's OUTPUT checkboxes decide whether it is written. A tab that is
ticked for export but was never calculated is calculated with its current
controls when the export starts (``ensure_result``).
"""

# %% ===========================================================================
#    IMPORTS
# =============================================================================
from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np
import pyqtgraph as pg
from qtpy import QtCore, QtWidgets

import maser_eval as ev
from maser_core import PhaseFit
from maser_io import Telemetry, TimingCollection, TimingSeries
from time_axis import TimeAxisItem

__version__ = "0.0.1"

COLOURS = {"OADEV": "#67b7d5", "MDEV": "#f2c078", "TDEV": "#a9cba0", "OHDEV": "#d59ada"}
TRACE_COLOURS = ("#67b7d5", "#f2c078", "#a9cba0", "#d59ada", "#e05f5b", "#9fa8da")
CONFIDENCE_LEVELS = (("68.27 % (1 sigma)", ev.ONE_SIGMA), ("90 %", 0.90), ("95 %", 0.95))
DETREND_CHOICES = ("Raw phase", "Remove mean", "Remove linear phase", "Remove quadratic phase")


@dataclass
class EvaluationContext:
    """What the tabs read from the main window (nothing is written back)."""
    series: TimingSeries | None
    fit: PhaseFit | None
    collection: TimingCollection | None
    telemetry: Telemetry | None
    sign: int = 1
    maser: str = "MHM2010"
    apply_mjd: float = float("nan")
    low_mjd: float = float("nan")
    high_mjd: float = float("nan")
    bridge: bool = True
    eligible: bool = False
    suggestion_kwargs: dict = field(default_factory=dict)


# %% ===========================================================================
#    SMALL HELPERS
# =============================================================================
class _DecadeAxis(pg.AxisItem):
    """Label only log decades (as the base Stability tab)."""
    def logTickStrings(self, values, scale, spacing):
        return [f"1e{int(round(v))}" if abs(v - round(v)) < 1e-6 else "" for v in values]


def log_plot(title: str, ylabel: str) -> pg.PlotWidget:
    widget = pg.PlotWidget(title=title, axisItems={"bottom": _DecadeAxis("bottom"), "left": _DecadeAxis("left")})
    widget.setLabel("left", ylabel)
    widget.setLabel("bottom", "Averaging time (s)")
    widget.setLogMode(True, True)
    for side in ("left", "bottom"):
        widget.getAxis(side).enableAutoSIPrefix(False)
    widget.showGrid(x=True, y=True, alpha=.15)
    widget.addLegend()
    return widget


def time_plot(title: str, ylabel: str) -> pg.PlotWidget:
    widget = pg.PlotWidget(title=title, axisItems={"bottom": TimeAxisItem("bottom")})
    widget.setLabel("bottom", "MJD (UTC)")
    widget.setLabel("left", ylabel)
    widget.showGrid(x=True, y=True, alpha=.16)
    widget.addLegend()
    return widget


def add_log_errors(widget: pg.PlotWidget, tau, value, low, high, colour: str) -> None:
    """Error bars on a log-log plot (pyqtgraph draws items in log10 coordinates)."""
    tau, value, low, high = (np.asarray(a, float) for a in (tau, value, low, high))
    ok = (tau > 0) & (value > 0) & (low > 0) & (high > 0)
    if not ok.any():
        return
    y = np.log10(value[ok])
    widget.addItem(pg.ErrorBarItem(x=np.log10(tau[ok]), y=y, top=np.log10(high[ok]) - y,
                                   bottom=y - np.log10(low[ok]), beam=0.03, pen=pg.mkPen(colour)))


def number_field(text: str, tip: str) -> QtWidgets.QLineEdit:
    edit = QtWidgets.QLineEdit(text)
    edit.setToolTip(tip)
    return edit


def full(value) -> str:
    """Exact text of a number (repr), '-' for None."""
    return "-" if value is None else repr(float(value)) if isinstance(value, (float, np.floating)) else repr(value)


# %% ===========================================================================
#    BASE TAB
# =============================================================================
class EvaluationTab(QtWidgets.QWidget):
    """Common frame: controls row, Calculate, plots, read-only text."""
    message = QtCore.Signal(str)
    computed = QtCore.Signal(str)
    title = "Evaluation"
    banner = "Evaluation only: the base fit and the suggested settings are not changed."

    def __init__(self, context_provider, parent=None):
        super().__init__(parent)
        self.context_provider = context_provider
        self.result = None
        self.result_error = ""
        layout = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel(self.banner)
        note.setWordWrap(True)
        note.setObjectName("evaluationBanner")
        layout.addWidget(note)
        self.controls = QtWidgets.QGridLayout()
        layout.addLayout(self.controls)
        self.calculate_button = QtWidgets.QPushButton(f"Calculate {self.title.lower()}")
        self.calculate_button.clicked.connect(self.calculate)
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        layout.addWidget(self.splitter, 1)
        self.plots = QtWidgets.QWidget()
        self.plot_layout = QtWidgets.QVBoxLayout(self.plots)
        self.plot_layout.setContentsMargins(0, 0, 0, 0)
        self.splitter.addWidget(self.plots)
        self.text = QtWidgets.QPlainTextEdit()
        self.text.setReadOnly(True)
        self.splitter.addWidget(self.text)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)
        self.plot_widgets: list = []
        self.time_widgets: list = []
        self.build()
        self.controls.addWidget(self.calculate_button, self.controls.rowCount(), 0, 1, 4)

    # -- to override ---------------------------------------------------------
    def build(self):
        """Add controls and plots."""

    def compute(self, context: EvaluationContext):
        """Return the result object; raise ValueError with a reason."""
        raise NotImplementedError

    def show_result(self, result):
        """Draw the result."""

    def options(self) -> dict:
        """Current control values (saved with the analysis settings)."""
        return {}

    def set_options(self, options: dict) -> None:
        """Restore control values."""

    # -- common --------------------------------------------------------------
    def add_plot(self, widget: pg.PlotWidget, time_axis: bool = False) -> pg.PlotWidget:
        self.plot_layout.addWidget(widget)
        self.plot_widgets.append(widget)
        if time_axis:
            self.time_widgets.append(widget)
        return widget

    def invalidate(self, reason: str = "Inputs changed; recalculate.") -> None:
        self.result = None
        self.result_error = reason
        for widget in self.plot_widgets:
            widget.clear()
        self.text.setPlainText(reason)

    def calculate(self) -> bool:
        context = self.context_provider()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        try:
            self.result = self.compute(context)
            self.result_error = ""
        except Exception as error:                       # shown, never raised into Qt
            self.result = None
            self.result_error = f"{type(error).__name__}: {error}"
            for widget in self.plot_widgets:
                widget.clear()
            self.text.setPlainText(f"{self.title}: not calculated.\n{self.result_error}")
            self.message.emit(f"{self.title}: {self.result_error}")
            return False
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        for widget in self.plot_widgets:
            widget.clear()
        self.show_result(self.result)
        self.computed.emit(self.title)
        self.message.emit(f"{self.title} calculated (evaluation only).")
        return True

    def ensure_result(self):
        """The last result, calculating it now if there is none."""
        if self.result is None:
            self.calculate()
        return self.result

    def set_theme(self, background, foreground) -> None:
        for widget in self.plot_widgets:
            widget.setBackground(background)
            for side in ("left", "bottom"):
                widget.getAxis(side).setPen(pg.mkPen(foreground))
                widget.getAxis(side).setTextPen(pg.mkPen(foreground))
            item = widget.getPlotItem()
            item.titleLabel.setText(item.titleLabel.text, color=foreground)
            if item.legend is not None:                      # legend text follows the theme
                item.legend.setLabelTextColor(foreground)

    def set_time_mode(self, mode: str) -> None:
        for widget in self.time_widgets:
            widget.getAxis("bottom").set_mode(mode)

    @staticmethod
    def _selected(context: EvaluationContext) -> tuple:
        """(mjd, phase) of the samples the base fit uses."""
        if context.series is None or context.fit is None:
            raise ValueError("Load timing data and make a valid fit first.")
        mask = context.fit.used
        return context.series.mjd[mask], context.series.phase_s[mask]


# %% ===========================================================================
#    1. GAP-AWARE STABILITY
# =============================================================================
class GapStabilityTab(EvaluationTab):
    title = "Gap-aware stability"
    banner = ("Evaluation only. Gaps stay gaps (nothing interpolated): a difference term that touches a gap is "
              "skipped (NIST SP 1065 Secs. 10.4-10.6). Confidence intervals: chi-squared with the simple EDF "
              "for OADEV and the lag-1 noise type, sigma/sqrt(N) otherwise. The base Stability tab is unchanged.")

    def __init__(self, context_provider, steering_provider=None, parent=None):
        self.steering_provider = steering_provider
        super().__init__(context_provider, parent)

    def build(self):
        self.detrend = QtWidgets.QComboBox()
        self.detrend.addItems(DETREND_CHOICES)
        self.grid_mode = QtWidgets.QComboBox()
        self.grid_mode.addItems(["Auto", "Anchored (fixed clock)", "Sequential (own intervals)"])
        self.grid_mode.setToolTip("Anchored: slots at origin + k tau0 (FITS 270 s). Sequential: each sample "
                                  "advances round(dt/tau0) slots (jittered data such as Franks). Auto: anchored "
                                  "when it places >= 90 % of the samples.")
        self.tau0 = QtWidgets.QDoubleSpinBox()
        self.tau0.setRange(0, 1e7)
        self.tau0.setDecimals(3)
        self.tau0.setSpecialValueText("auto (median interval)")
        self.tau0.setSuffix(" s")
        self.tolerance = QtWidgets.QDoubleSpinBox()
        self.tolerance.setRange(1, 50)
        self.tolerance.setValue(10)
        self.tolerance.setSuffix(" % of tau0")
        self.level = QtWidgets.QComboBox()
        for label, _ in CONFIDENCE_LEVELS:
            self.level.addItem(label)
        self.source = QtWidgets.QComboBox()
        self.source.addItems(["Selected samples (as the base fit)", "Steering-removed samples"])
        rows = (("Detrend", self.detrend), ("Grid", self.grid_mode), ("tau0", self.tau0),
                ("Slot tolerance", self.tolerance), ("Confidence", self.level), ("Data", self.source))
        for i, (label, widget) in enumerate(rows):
            self.controls.addWidget(QtWidgets.QLabel(label), i // 3, 2 * (i % 3))
            self.controls.addWidget(widget, i // 3, 2 * (i % 3) + 1)
        self.frequency = self.add_plot(log_plot("Gap-aware frequency stability with confidence intervals",
                                                "Fractional frequency deviation"))
        self.tdev = self.add_plot(log_plot("Gap-aware time deviation", "TDEV (s)"))

    def options(self) -> dict:
        return {"detrend": self.detrend.currentIndex(), "grid_mode": self.grid_mode.currentIndex(),
                "tau0_s": self.tau0.value(), "slot_tolerance_percent": self.tolerance.value(),
                "confidence": self.level.currentIndex(), "source": self.source.currentIndex()}

    def set_options(self, options: dict) -> None:
        self.detrend.setCurrentIndex(int(options.get("detrend", 0)))
        self.grid_mode.setCurrentIndex(int(options.get("grid_mode", 0)))
        self.tau0.setValue(float(options.get("tau0_s", 0.0)))
        self.tolerance.setValue(float(options.get("slot_tolerance_percent", 10.0)))
        self.level.setCurrentIndex(int(options.get("confidence", 0)))
        self.source.setCurrentIndex(int(options.get("source", 0)))

    def compute(self, context: EvaluationContext) -> dict:
        if self.source.currentIndex() == 1:
            steering = self.steering_provider() if self.steering_provider else None
            if steering is None:
                raise ValueError("Calculate the steering removal first (Steering removal tab).")
            mjd, phase = ev.steering_removed_window(steering, context.low_mjd, context.high_mjd)
            source = "steering removed"
        else:
            mjd, phase = self._selected(context)
            source = "selected samples (base fit)"
        return ev.run_gap_stability(mjd, phase, context.series.label if context.series is not None else "", source,
                                    self.detrend.currentIndex() - 1, self.tau0.value() or None,
                                    self.tolerance.value() / 100.0, CONFIDENCE_LEVELS[self.level.currentIndex()][1],
                                    ev.GRID_MODES[self.grid_mode.currentIndex()])

    def show_result(self, result: dict) -> None:
        for name, values in result["estimators"].items():
            if not values["tau_s"]:
                continue
            target = self.tdev if name == "TDEV" else self.frequency
            target.plot(values["tau_s"], values["deviation"], pen=pg.mkPen(COLOURS[name], width=2),
                        symbol="o", symbolSize=5, symbolBrush=COLOURS[name], name=name)
            add_log_errors(target, values["tau_s"], values["deviation"], values["ci_low"], values["ci_high"],
                           COLOURS[name])
        grid = result["grid"]
        lines = [f"Gap-aware stability (evaluation) | {result['channel']} | {result['source']}",
                 f"Grid {grid['mode']}: tau0 {full(grid['tau0_s'])} s; {grid['placed']} of {grid['input_samples']} "
                 f"samples placed in {grid['slots']} slots; {grid['gap_slots']} gap slots in {grid['gap_runs']} runs "
                 f"(longest {grid['longest_gap_slots']}); duplicates {grid['duplicates_not_placed']}; "
                 f"max offset {full(grid['max_abs_offset_fraction'])} tau0.",
                 f"Confidence level {full(result['confidence_level'])}; detrend order {result['detrend_order']}.",
                 "", "estimator | tau (s) | deviation | low | high | method | EDF | noise | terms"]
        for name, values in result["estimators"].items():
            for i, tau in enumerate(values["tau_s"]):
                lines.append(f"{name} | {full(tau)} | {full(values['deviation'][i])} | {full(values['ci_low'][i])} | "
                             f"{full(values['ci_high'][i])} | {values['ci_method'][i]} | {full(values['edf'][i])} | "
                             f"{values['alpha_name'][i]} | {values['terms'][i]}")
        self.text.setPlainText("\n".join(lines))


# %% ===========================================================================
#    2. STEERING REMOVAL
# =============================================================================
class SteeringTab(EvaluationTab):
    title = "Steering removal"
    banner = ("Evaluation only. The MHM2010 frequency steering recorded in the HMD (DDS setting) is integrated "
              "to phase and removed from the measured channel, showing the maser without the recorded "
              "adjustments. Uses the phase sign of the side panel. The base suggestion is unchanged.")

    def build(self):
        self.mode = QtWidgets.QComboBox()
        self.mode.addItems(["All DDS changes (steps and drift compensation)", "Steps only (> threshold)"])
        self.threshold = QtWidgets.QDoubleSpinBox()
        self.threshold.setRange(1, 1e7)
        self.threshold.setValue(50)
        self.threshold.setSuffix(" counts")
        self.threshold.setToolTip("A step is a jump between consecutive HMD rows larger than this many DDS "
                                  "least-significant digits (1e-7).")
        self.column = QtWidgets.QComboBox()
        self.column.addItems(["ExtSynth_32", "Thumbwheels_31"])
        self.column.setToolTip("HMD column with the DDS that drives the output (external DDS by default).")
        rows = (("Remove", self.mode), ("Step threshold", self.threshold), ("DDS column", self.column))
        for i, (label, widget) in enumerate(rows):
            self.controls.addWidget(QtWidgets.QLabel(label), 0, 2 * i)
            self.controls.addWidget(widget, 0, 2 * i + 1)
        self.phase = self.add_plot(time_plot("Measured and steering-removed phase", "Phase (ns)"), True)
        self.applied = self.add_plot(time_plot("Recorded steering: applied fractional frequency",
                                               "Fractional frequency"), True)

    def options(self) -> dict:
        return {"mode": self.mode.currentIndex(), "threshold_counts": self.threshold.value(),
                "column": self.column.currentText()}

    def set_options(self, options: dict) -> None:
        self.mode.setCurrentIndex(int(options.get("mode", 0)))
        self.threshold.setValue(float(options.get("threshold_counts", 50.0)))
        self.column.setCurrentText(str(options.get("column", "ExtSynth_32")))

    def compute(self, context: EvaluationContext) -> dict:
        if context.series is None or context.fit is None:
            raise ValueError("Load timing data and make a valid fit first.")
        if context.telemetry is None:
            raise ValueError("Open the MHM2010 HMD telemetry: steering is read from its DDS record.")
        return ev.run_steering(context.series, context.fit, context.telemetry.frame, context.sign, context.low_mjd,
                               context.high_mjd, context.apply_mjd, context.maser, context.bridge,
                               ("all", "steps")[self.mode.currentIndex()], self.threshold.value(),
                               self.column.currentText())

    def show_result(self, result: dict) -> None:
        steering, comparison = result["steering"], result["comparison"]
        self.phase.plot(steering.mjd, steering.measured_phase_s * 1e9, pen=None, symbol="o", symbolSize=3,
                        symbolBrush=TRACE_COLOURS[0], symbolPen=None, name="Measured")
        self.phase.plot(steering.mjd, steering.removed_phase_s * 1e9, pen=None, symbol="o", symbolSize=3,
                        symbolBrush=TRACE_COLOURS[1], symbolPen=None, name="Steering removed")
        if comparison is not None:
            fit = comparison["removed_fit"]
            inside = fit.selected
            removed = steering.removed_series("", "")
            self.phase.plot(removed.mjd[inside], fit.predicted_s[inside] * 1e9, pen=pg.mkPen(TRACE_COLOURS[4], width=2),
                            name="Fit of removed record")
        self.applied.plot(steering.hmd_mjd, steering.hmd_applied_y, pen=pg.mkPen(TRACE_COLOURS[2], width=2),
                          name=f"From HMD {steering.column}")
        summary = steering.summary()
        lines = [f"Steering removal (evaluation) | {result['channel']} | mode {summary['mode']} | "
                 f"DDS column {summary['dds_column']}",
                 f"Covered {summary['covered_samples']} of {summary['timing_samples']} timing samples; HMD rows "
                 f"{summary['hmd_rows']}; reference DDS {full(summary['reference_dds'])}.",
                 f"Applied fractional frequency: first {full(summary['applied_y_first'])}, last "
                 f"{full(summary['applied_y_last'])}; integrated phase {full(summary['applied_phase_total_s'])} s.",
                 f"Step events (> {full(summary['threshold_counts'])} counts): {len(summary['step_events'])}"]
        lines += [f"  MJD {full(e['mjd_utc'])}: {full(e['dds_before'])} -> {full(e['dds_after'])} "
                  f"({full(e['change_counts'])} counts)" for e in summary["step_events"][:50]]
        if comparison is not None:
            base, free = comparison["base"], comparison["steering_removed"]
            lines += ["", f"At MJD {full(comparison['application_epoch_mjd_utc'])} (maser minus reference):",
                      "quantity | base fit (measured, steered) | steering removed (maser alone)",
                      f"fractional frequency | {full(base['fractional_frequency'])} +/- {full(base['sigma'])} | "
                      f"{full(free['fractional_frequency'])} +/- {full(free['sigma'])}",
                      f"drift per day | {full(base['drift_per_day'])} +/- {full(base['drift_sigma'])} | "
                      f"{full(free['drift_per_day'])} +/- {full(free['drift_sigma'])}",
                      f"samples | {base['samples']} | {free['samples']}"]
        lines += ["", "Notes:", *[f"- {n}" for n in summary["notes"]]]
        self.text.setPlainText("\n".join(lines))


# %% ===========================================================================
#    3. KALMAN ESTIMATE
# =============================================================================
class KalmanTab(EvaluationTab):
    title = "Kalman estimate"
    banner = ("Evaluation only. Three-state clock model (phase, frequency, drift) with white phase measurement "
              "noise; gaps are carried by prediction, nothing is filled. Shown beside the base least-squares "
              "fit; any settings here are for comparison and never replace the base suggestion.")

    def build(self):
        self.noise_source = QtWidgets.QComboBox()
        self.noise_source.addItems(["Auto: fit to gap-aware OHDEV", "Manual values below"])
        self.r_field = number_field("4e-18", "White PM measurement variance R (s^2)")
        self.q1_field = number_field("1e-24", "White FM diffusion q1 (s): sigma_y^2 = q1/tau")
        self.q2_field = number_field("1e-32", "Random-walk FM diffusion q2 (1/s): sigma_y^2 = q2 tau/3")
        self.q3_field = number_field("0", "Random-walk drift diffusion q3 (1/s^3)")
        self.smooth = QtWidgets.QCheckBox("RTS smoothing (plots)")
        self.smooth.setChecked(True)
        rows = (("Noise", self.noise_source), ("R (s^2)", self.r_field), ("q1 (s)", self.q1_field),
                ("q2 (1/s)", self.q2_field), ("q3 (1/s^3)", self.q3_field), ("", self.smooth))
        for i, (label, widget) in enumerate(rows):
            self.controls.addWidget(QtWidgets.QLabel(label), i // 3, 2 * (i % 3))
            self.controls.addWidget(widget, i // 3, 2 * (i % 3) + 1)
        self.frequency = self.add_plot(time_plot("Kalman fractional frequency (+/- 1 sigma) and base fit",
                                                 "Fractional frequency"), True)
        self.drift = self.add_plot(time_plot("Kalman drift (+/- 1 sigma) and base fit", "Drift per day"), True)
        self.innovation = self.add_plot(time_plot("Innovations (measured minus predicted phase)", "ns"), True)

    def options(self) -> dict:
        return {"noise_source": self.noise_source.currentIndex(), "R_s2": self.r_field.text(),
                "q1_s": self.q1_field.text(), "q2_per_s": self.q2_field.text(),
                "q3_per_s3": self.q3_field.text(), "smooth": self.smooth.isChecked()}

    def set_options(self, options: dict) -> None:
        self.noise_source.setCurrentIndex(int(options.get("noise_source", 0)))
        for edit, key in ((self.r_field, "R_s2"), (self.q1_field, "q1_s"), (self.q2_field, "q2_per_s"),
                          (self.q3_field, "q3_per_s3")):
            if key in options:
                edit.setText(str(options[key]))
        self.smooth.setChecked(bool(options.get("smooth", True)))

    def compute(self, context: EvaluationContext) -> dict:
        mjd, phase = self._selected(context)
        noise = None
        if self.noise_source.currentIndex() == 1:
            noise = ev.KalmanNoise(float(self.r_field.text()), float(self.q1_field.text()),
                                   float(self.q2_field.text()), float(self.q3_field.text()), "manual")
        return ev.run_kalman(mjd, phase, context.fit, context.series.label, context.apply_mjd, context.sign,
                             context.maser, context.eligible, context.suggestion_kwargs, noise,
                             self.smooth.isChecked())

    def show_result(self, result: dict) -> None:
        kalman, sign = result["kalman"], result["sign"]
        y, sy = sign * kalman.fractional_frequency, kalman.fractional_frequency_sigma
        d, sd = sign * kalman.drift_per_day, kalman.drift_sigma_per_day
        for widget, value, sigma, colour, name in ((self.frequency, y, sy, TRACE_COLOURS[0], "Kalman"),
                                                    (self.drift, d, sd, TRACE_COLOURS[2], "Kalman")):
            upper = widget.plot(kalman.mjd, value + sigma, pen=pg.mkPen(colour, width=0.5))
            lower = widget.plot(kalman.mjd, value - sigma, pen=pg.mkPen(colour, width=0.5))
            widget.addItem(pg.FillBetweenItem(upper, lower, brush=pg.mkBrush(colour + "40")))
            widget.plot(kalman.mjd, value, pen=pg.mkPen(colour, width=2), name=name)
        fit = result["base_fit"]
        base_mjd = result["base_mjd"]
        days = (base_mjd - fit.epoch_mjd)
        coefficients = np.pad(fit.coefficients_ns, (0, 3 - len(fit.coefficients_ns)))
        base_y = sign * (coefficients[1] + 2 * coefficients[2] * days) * 1e-9 / 86400.0
        self.frequency.plot(base_mjd, base_y, pen=pg.mkPen(TRACE_COLOURS[1], width=2, style=QtCore.Qt.PenStyle.DashLine),
                            name="Base least-squares fit")
        if fit.order == 2:
            self.drift.plot(base_mjd, np.full(base_mjd.size, sign * 2 * coefficients[2] * 1e-9 / 86400.0),
                            pen=pg.mkPen(TRACE_COLOURS[1], width=2, style=QtCore.Qt.PenStyle.DashLine),
                            name="Base least-squares fit")
        self.innovation.plot(kalman.mjd, kalman.innovation_ns, pen=None, symbol="o", symbolSize=3,
                             symbolBrush=TRACE_COLOURS[3], symbolPen=None, name="Innovation")
        noise, estimate, base = kalman.noise, result["estimate"], result["base"]
        lines = [f"Kalman estimate (evaluation) | {result['channel']} | {kalman.mjd.size} samples | noise "
                 f"{noise.source}",
                 f"R {full(noise.r_s2)} s^2; q1 {full(noise.q1_s)} s; q2 {full(noise.q2_per_s)} 1/s; "
                 f"q3 {full(noise.q3_per_s3)} 1/s^3",
                 f"Model check: normalized innovation squared {full(kalman.normalized_innovation_squared)} "
                 "(about 1 when the noise model fits; >> 1: the sigmas below are too small)."]
        if "rms_relative_misfit" in noise.detail:
            lines.append(f"OHDEV fit relative RMS misfit {full(noise.detail['rms_relative_misfit'])}.")
        lines += ["", f"At MJD {full(estimate['application_epoch_mjd_utc'])} (maser minus reference):",
                  "quantity | base least squares | Kalman",
                  f"fractional frequency | {full(base['fractional_frequency'])} +/- {full(base['sigma'])} | "
                  f"{full(estimate['fractional_frequency'])} +/- {full(estimate['fractional_frequency_sigma'])}",
                  f"drift per day | {full(base['drift_per_day'])} +/- {full(base['drift_sigma'])} | "
                  f"{full(estimate['drift_per_day'])} +/- {full(estimate['drift_sigma_per_day'])}",
                  "The base sigma is conditional on white noise; the Kalman sigma includes the model's "
                  "random-walk terms."]
        if result["settings"] is not None:
            frequency = result["settings"].get("frequency_setting") or {}
            drift = result["settings"].get("drift_setting") or {}
            lines += ["", "Settings from the Kalman estimate (EVALUATION ONLY, not the base suggestion):"]
            lines += [f"  {key}: {full(value) if isinstance(value, (int, float)) else value}"
                      for block in (frequency, drift) for key, value in block.items()]
        else:
            lines += ["", f"No settings from the Kalman estimate: {result['settings_error']}"]
        self.text.setPlainText("\n".join(lines))


# %% ===========================================================================
#    4. THREE-CORNERED HAT
# =============================================================================
class ThreeCorneredHatTab(EvaluationTab):
    title = "Three-cornered hat"
    banner = ("Evaluation only. Individual Allan deviations of three clocks from their pair comparisons "
              "(Gray and Allan 1974), gap-aware OADEV on the common time stamps. Assumes uncorrelated clocks; "
              "which channel compares which clocks is the operator's statement (not known to the program).")

    def build(self):
        self.mode = QtWidgets.QComboBox()
        self.mode.addItems(["Three measured pairs: A-B, B-C, C-A", "Two pairs against a common clock C"])
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.channels = [QtWidgets.QComboBox() for _ in range(3)]
        self.signs = [QtWidgets.QComboBox() for _ in range(2)]
        for sign in self.signs:
            sign.addItems(["+ measured", "- measured"])
        self.names = [QtWidgets.QLineEdit(text) for text in ("A", "B", "C")]
        self.detrend = QtWidgets.QComboBox()
        self.detrend.addItems(DETREND_CHOICES)
        self.detrend.setCurrentIndex(3)
        self.channel_labels = [QtWidgets.QLabel(t) for t in ("Channel A-B", "Channel B-C", "Channel C-A")]
        self.controls.addWidget(QtWidgets.QLabel("Mode"), 0, 0)
        self.controls.addWidget(self.mode, 0, 1, 1, 3)
        self.controls.addWidget(QtWidgets.QLabel("Detrend each pair"), 0, 4)
        self.controls.addWidget(self.detrend, 0, 5)
        for i in range(3):
            self.controls.addWidget(self.channel_labels[i], 1 + i, 0)
            self.controls.addWidget(self.channels[i], 1 + i, 1)
            self.controls.addWidget(QtWidgets.QLabel(f"Clock {'ABC'[i]} name"), 1 + i, 2)
            self.controls.addWidget(self.names[i], 1 + i, 3)
            if i < 2:
                self.controls.addWidget(QtWidgets.QLabel("Sign"), 1 + i, 4)
                self.controls.addWidget(self.signs[i], 1 + i, 5)
        self.plot = self.add_plot(log_plot("Three-cornered hat: individual and pair Allan deviations",
                                           "Allan deviation"))
        self._mode_changed()

    def _mode_changed(self, *_):
        common = self.mode.currentIndex() == 1
        texts = (("Channel A-C", "Channel B-C", "(derived A-B)") if common
                 else ("Channel A-B", "Channel B-C", "Channel C-A"))
        for label, text in zip(self.channel_labels, texts):
            label.setText(text)
        self.channels[2].setEnabled(not common)
        for sign in self.signs:
            sign.setEnabled(common)

    def set_channels(self, names: list) -> None:
        for index, combo in enumerate(self.channels):
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(names)
            if current in names:
                combo.setCurrentText(current)
            elif len(names) > index:
                combo.setCurrentIndex(index)
            combo.blockSignals(False)

    def options(self) -> dict:
        return {"mode": self.mode.currentIndex(), "channels": [c.currentText() for c in self.channels],
                "signs": [s.currentIndex() for s in self.signs], "names": [n.text() for n in self.names],
                "detrend": self.detrend.currentIndex()}

    def set_options(self, options: dict) -> None:
        self.mode.setCurrentIndex(int(options.get("mode", 0)))
        for combo, name in zip(self.channels, options.get("channels", [])):
            combo.setCurrentText(name)
        for combo, index in zip(self.signs, options.get("signs", [])):
            combo.setCurrentIndex(int(index))
        for edit, text in zip(self.names, options.get("names", [])):
            edit.setText(text)
        self.detrend.setCurrentIndex(int(options.get("detrend", 3)))

    def compute(self, context: EvaluationContext) -> dict:
        if context.collection is None:
            raise ValueError("Load timing data with at least two channels first.")
        common = self.mode.currentIndex() == 1
        names = tuple(n.text().strip() or "ABC"[i] for i, n in enumerate(self.names))
        signs = tuple(1 if s.currentIndex() == 0 else -1 for s in self.signs) + (1,)
        return ev.run_three_cornered_hat(context.collection.series, [c.currentText() for c in self.channels],
                                         names, "common" if common else "three", signs,
                                         self.detrend.currentIndex() - 1)

    def show_result(self, result: dict) -> None:
        for index, (name, values) in enumerate(result["clocks"].items()):
            self.plot.plot(result["tau_s"], values["adev"], pen=pg.mkPen(TRACE_COLOURS[index], width=2),
                           symbol="o", symbolSize=6, symbolBrush=TRACE_COLOURS[index], name=f"Clock {name}",
                           connect="finite")
        for index, (pair, values) in enumerate(result["pairs"].items()):
            self.plot.plot(result["tau_s"], values["adev"], pen=pg.mkPen(TRACE_COLOURS[3 + index], width=1,
                                                                         style=QtCore.Qt.PenStyle.DashLine),
                           name=f"Pair {pair}")
        lines = [f"Three-cornered hat (evaluation) | mode {result['mode']} | channels {', '.join(result['channels'])}",
                 f"Clocks {', '.join(result['names'])}; common samples {result['common_samples']}; grid "
                 f"{result['grid']['mode']}, tau0 {full(result['tau0_s'])} s; detrend order {result['detrend_order']}.",
                 f"Non-positive variance estimates (deviation not shown): {result['non_positive_variances']}",
                 "", "tau (s) | " + " | ".join(f"{n} ADEV" for n in result["names"]) + " | "
                 + " | ".join(f"{p} ADEV" for p in result["pairs"])]
        for i, tau in enumerate(result["tau_s"]):
            lines.append(f"{full(tau)} | " + " | ".join(full(result["clocks"][n]["adev"][i]) for n in result["names"])
                         + " | " + " | ".join(full(v["adev"][i]) for v in result["pairs"].values()))
        lines += ["", "Notes:", *[f"- {n}" for n in result["notes"]]]
        self.text.setPlainText("\n".join(lines))


# %% ===========================================================================
#    CONTAINER
# =============================================================================
class EvaluationTabs:
    """Creates the four tabs and adds them to the main tab widget."""

    def __init__(self, tab_widget: QtWidgets.QTabWidget, context_provider):
        self.steering = SteeringTab(context_provider)
        self.gap = GapStabilityTab(context_provider, lambda: self.steering.result)
        self.kalman = KalmanTab(context_provider)
        self.tch = ThreeCorneredHatTab(context_provider)
        self.tabs = [self.gap, self.steering, self.kalman, self.tch]
        for tab, label in zip(self.tabs, ("Eval: gap-aware stability", "Eval: steering removal",
                                          "Eval: Kalman estimate", "Eval: three-cornered hat")):
            tab_widget.addTab(tab, label)

    def invalidate(self, reason: str = "Inputs changed; recalculate.") -> None:
        for tab in self.tabs:
            tab.invalidate(reason)

    def set_theme(self, background, foreground) -> None:
        for tab in self.tabs:
            tab.set_theme(background, foreground)

    def set_time_mode(self, mode: str) -> None:
        for tab in self.tabs:
            tab.set_time_mode(mode)

    def options(self) -> dict:
        return {"gap_stability": self.gap.options(), "steering": self.steering.options(),
                "kalman": self.kalman.options(), "three_cornered_hat": self.tch.options()}

    def set_options(self, options: dict) -> None:
        for tab, key in ((self.gap, "gap_stability"), (self.steering, "steering"), (self.kalman, "kalman"),
                         (self.tch, "three_cornered_hat")):
            if key in options:
                tab.set_options(options[key])
