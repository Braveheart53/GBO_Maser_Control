#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Reference-style 1 PPS delta figures for the workbench GUI (pyqtgraph).

===============================================================================
 Title          : 1 PPS Reference Plots Panel
 Filename       : gui_reference.py
 Version        : 0.0.2 (Semantic Versioning 2.0.0)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-03
 Modified       : 2026-11-05
 Language       : Python 3.12
 GUI            : qtpy (PySide6 default) + pyqtgraph
 Licence        : GPL-3.0-or-later; see LICENSE
 Revision       : 0.0.1 Deltas figure with statistics box; data + fit over
                  residuals (linked x, 3:1 heights) with the fit-results and
                  residual boxes; editable title/axis labels; light/dark.
                  0.0.2 x axes follow View > Time display: MJD - MJD0 (as the
                  operator's figures) or ISO 8601 local/UTC date-time. The
                  data, fit and boxes are unchanged (x stays MJD - MJD0).
===============================================================================
The numbers and box text come from maser_plots, the same code that writes the
Veusz pages, so the GUI and the .vszh5 always agree.
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from qtpy import QtCore, QtWidgets

from maser_plots import (DATA_COLOUR, FIT_COLOUR, ReferenceSet, fit_box_lines, mjd_axis_label,
                         raw_box_lines, residual_box_lines)
from time_axis import TimeAxisItem, axis_label

__version__ = "0.0.2"

THEMES = {
    "light": {"background": "#ffffff", "foreground": "#202020", "box_fill": (255, 255, 255, 235),
              "box_border": "#b0b0b0", "text": "#111111"},
    "dark": {"background": "#101b29", "foreground": "#dce5ef", "box_fill": (16, 27, 41, 230),
             "box_border": "#5a6d84", "text": "#e8eef5"},
}


class CornerText(pg.TextItem):
    """Bordered text box pinned to a corner of a ViewBox (pixel coordinates)."""

    def __init__(self, view_box: pg.ViewBox, corner: str, theme: str = "light"):
        anchors = {"upper right": (1, 0), "upper left": (0, 0), "lower left": (0, 1), "lower right": (1, 1)}
        super().__init__("", anchor=anchors[corner])
        self.corner = corner
        self.view_box = view_box
        self.setParentItem(view_box)
        self.setZValue(50)
        self.set_theme(theme)
        view_box.sigResized.connect(self.reposition)
        self.reposition()

    def set_theme(self, theme: str) -> None:
        colours = THEMES[theme]
        self.fill = pg.mkBrush(colours["box_fill"])
        self.border = pg.mkPen(colours["box_border"], width=1)
        self.setColor(colours["text"])
        self.update()

    def set_lines(self, lines) -> None:
        self.setText("\n".join(lines))

    def reposition(self, *_):
        rect = self.view_box.boundingRect()
        margin = 6
        x = rect.right() - margin if "right" in self.corner else rect.left() + margin
        y = rect.top() + margin if "upper" in self.corner else rect.bottom() - margin
        self.setPos(x, y)


class ReferencePlotsWidget(QtWidgets.QWidget):
    """The two operator reference figures, recomputed on every refit."""
    labelsChanged = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.theme = "dark"
        self.time_mode = "mjd"
        self.reference: ReferenceSet | None = None
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QHBoxLayout()
        self.title_edit = QtWidgets.QLineEdit()
        self.ylabel_edit = QtWidgets.QLineEdit()
        self.fit_ylabel_edit = QtWidgets.QLineEdit()
        for label, edit, tip in (("Title", self.title_edit, "Deltas figure title (also in the .vszh5)"),
                                 ("Deltas y label", self.ylabel_edit, "y-axis label of the deltas figure"),
                                 ("Fit y label", self.fit_ylabel_edit, "y-axis label of the fit figure")):
            form.addWidget(QtWidgets.QLabel(label))
            edit.setToolTip(tip)
            edit.setPlaceholderText("default")
            edit.editingFinished.connect(self.labelsChanged.emit)
            form.addWidget(edit, 1)
        reset = QtWidgets.QPushButton("Default labels")
        reset.clicked.connect(self.reset_labels)
        form.addWidget(reset)
        layout.addLayout(form)
        self.note = QtWidgets.QLabel("Select timing data and a fit interval. The deltas figure shows every "
                                     "sample in the blue interval; the fit figure uses the fitted samples.")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        splitter = QtWidgets.QSplitter()
        layout.addWidget(splitter, 1)
        self.deltas_widget = pg.GraphicsLayoutWidget()
        self.fit_widget = pg.GraphicsLayoutWidget()
        splitter.addWidget(self.deltas_widget)
        splitter.addWidget(self.fit_widget)
        splitter.setSizes([520, 760])
        # Figure 1: deltas
        self.deltas_plot = self.deltas_widget.addPlot(title="1PPS Deltas",
                                                      axisItems={"bottom": TimeAxisItem("bottom")})
        self.deltas_curve = self.deltas_plot.plot(pen=None, symbol="o", symbolSize=4,
                                                  symbolBrush=DATA_COLOUR, symbolPen=None)
        self.deltas_box = CornerText(self.deltas_plot.vb, "upper right")
        # Figure 2: data + fit (3) over residuals (1), x linked
        self.fit_plot = self.fit_widget.addPlot(row=0, col=0, axisItems={"bottom": TimeAxisItem("bottom")})
        self.fit_plot.addLegend(offset=(-10, 10))
        self.excluded_curve = self.fit_plot.plot(pen=None, symbol="o", symbolSize=5, symbolBrush=None,
                                                 symbolPen=pg.mkPen("#8c8c8c"))
        self._excluded_in_legend = False
        self.data_curve = self.fit_plot.plot(pen=None, symbol="o", symbolSize=4, symbolBrush=DATA_COLOUR,
                                             symbolPen=None, name="data")
        self.model_curve = self.fit_plot.plot(pen=pg.mkPen(FIT_COLOUR, width=2), name="fit")
        self.fit_box = CornerText(self.fit_plot.vb, "lower left")
        self.residual_plot = self.fit_widget.addPlot(row=1, col=0, axisItems={"bottom": TimeAxisItem("bottom")})
        self.residual_plot.setXLink(self.fit_plot)
        self.residual_curve = self.residual_plot.plot(pen=None, symbol="o", symbolSize=4,
                                                      symbolBrush=DATA_COLOUR, symbolPen=None)
        self.residual_box = CornerText(self.residual_plot.vb, "upper left")
        self.fit_widget.ci.layout.setRowStretchFactor(0, 3)
        self.fit_widget.ci.layout.setRowStretchFactor(1, 1)
        self.fit_widget.ci.layout.setVerticalSpacing(0)
        self.fit_plot.getAxis("bottom").setStyle(showValues=False)
        self.residual_plot.setLabel("left", "Residuals (ns)")
        for plot in (self.deltas_plot, self.fit_plot, self.residual_plot):
            plot.getAxis("left").enableAutoSIPrefix(False)
            plot.getAxis("bottom").enableAutoSIPrefix(False)
            plot.setClipToView(True)
        self.set_theme("dark")

    # ------------------------------------------------------------------ labels
    def labels(self) -> dict[str, str]:
        return {"title": self.title_edit.text().strip(), "ylabel": self.ylabel_edit.text().strip(),
                "fit_ylabel": self.fit_ylabel_edit.text().strip()}

    def set_labels(self, labels: dict | None) -> None:
        labels = labels or {}
        self.title_edit.setText(labels.get("title", ""))
        self.ylabel_edit.setText(labels.get("ylabel", ""))
        self.fit_ylabel_edit.setText(labels.get("fit_ylabel", ""))

    def reset_labels(self) -> None:
        self.set_labels({})
        self.labelsChanged.emit()

    # ------------------------------------------------------------------- draw
    def clear(self, message: str = "") -> None:
        self.reference = None
        for curve in (self.deltas_curve, self.data_curve, self.model_curve, self.residual_curve,
                      self.excluded_curve):
            curve.clear()
        for box in (self.deltas_box, self.fit_box, self.residual_box):
            box.set_lines([])
        if message:
            self.note.setText(message)

    def show_reference(self, ref: ReferenceSet) -> None:
        self.reference = ref
        self.deltas_plot.setTitle(ref.title)
        self.deltas_plot.setLabel("left", ref.ylabel)
        self.deltas_curve.setData(ref.raw.x_days, ref.raw.y_ns)
        self.deltas_box.set_lines(raw_box_lines(ref.raw))
        self.fit_plot.setLabel("left", ref.fit_ylabel)
        self.data_curve.setData(ref.fit.x_days, ref.fit.y_ns)
        order = np.argsort(ref.fit.x_days)
        self.model_curve.setData(ref.fit.x_days[order], ref.fit.model_ns[order])
        legend = self.fit_plot.legend
        if ref.excluded is not None and ref.excluded.n:
            self.excluded_curve.setData(ref.excluded.x_days, ref.excluded.y_ns)
            if not self._excluded_in_legend:
                legend.addItem(self.excluded_curve, "excluded")
                self._excluded_in_legend = True
                self.set_theme(self.theme)
        else:
            self.excluded_curve.clear()
            if self._excluded_in_legend:
                legend.removeItem(self.excluded_curve)
                self._excluded_in_legend = False
        self.residual_curve.setData(ref.fit.x_days, ref.fit.residual_ns)
        self.fit_box.set_lines(fit_box_lines(ref.fit, ref.synth_line))
        self.residual_box.set_lines(residual_box_lines(ref.fit))
        for plot in (self.deltas_plot, self.fit_plot, self.residual_plot):
            plot.enableAutoRange()
        self.set_time_mode(self.time_mode)
        self.note.setText(f"{ref.raw.n} samples in the interval; {ref.fit.n} fitted. Straight-line OLS; "
                          "std is the population value; ± are IID-OLS standard errors.")

    # ------------------------------------------------------------ time display
    def set_time_mode(self, mode: str) -> None:
        """x axes in MJD - MJD0 (figure style) or ISO 8601 local/UTC; data unchanged."""
        self.time_mode = mode
        ref = self.reference
        for plot, part in ((self.deltas_plot, ref.raw if ref else None), (self.fit_plot, ref.fit if ref else None),
                           (self.residual_plot, ref.fit if ref else None)):
            origin = part.mjd0 if part is not None else 0.0
            plot.getAxis("bottom").set_mode(mode, origin)
        if ref is None:
            return
        for plot, part in ((self.deltas_plot, ref.raw), (self.residual_plot, ref.fit)):
            if mode == "mjd":
                plot.setLabel("bottom", mjd_axis_label(part.mjd0))
            else:
                x = np.asarray(part.x_days, float)
                plot.setLabel("bottom", axis_label(mode, float(x.min()), float(x.max()), part.mjd0))

    # ------------------------------------------------------------------ theme
    def set_theme(self, theme: str) -> None:
        self.theme = theme
        colours = THEMES[theme]
        for widget in (self.deltas_widget, self.fit_widget):
            widget.setBackground(colours["background"])
        for plot in (self.deltas_plot, self.fit_plot, self.residual_plot):
            for side in ("left", "bottom", "top", "right"):
                axis = plot.getAxis(side)
                axis.setPen(pg.mkPen(colours["foreground"]))
                axis.setTextPen(pg.mkPen(colours["foreground"]))
            plot.titleLabel.setText(plot.titleLabel.text, color=colours["foreground"])
        for box in (self.deltas_box, self.fit_box, self.residual_box):
            box.set_theme(theme)
        legend = self.fit_plot.legend
        if legend is not None:
            legend.setLabelTextColor(colours["foreground"])
            legend.setBrush(pg.mkBrush(colours["box_fill"]))
            legend.setPen(pg.mkPen(colours["box_border"]))
            for _sample, label in legend.items:          # existing entries too
                label.setText(label.text, color=colours["foreground"])
