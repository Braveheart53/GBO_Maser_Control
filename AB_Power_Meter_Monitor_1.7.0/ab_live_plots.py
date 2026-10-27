#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace <wwallace@nrao.edu>
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Interactive live plots of one AB power meter (pyqtgraph) for the monitor GUI.

===============================================================================
 Title          : AB Power Meter Monitor - interactive live plots
 Filename       : ab_live_plots.py
 Version        : 0.1.0 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu   (alt: naval.antennas@gmail.com)
 Created        : 2026-10-27 (from prototypes/pyqtgraph_live_preview.py 0.0.1)
 Modified       : 2026-10-27
 Language       : Python 3.12 (pyqtgraph 0.14.0, PySide6 6.x) and
                  Python 3.7.11+ (pyqtgraph 0.12.4 with PySide6 6.2.4)
 Requires       : PySide6 + qtpy, pyqtgraph, numpy
 Companion      : ab_power_meter_monitor.py >= 1.7.0 (data: preview_groups(),
                  meter_identity()); it imports this module only for its GUI
 Licence        : GPL-3.0-or-later (GNU General Public License v3.0 or later)
 Status         : Internal release - tested offscreen (tests/test_live_plots.py)
===============================================================================

WHAT IT DRAWS
-------------
MeterPanel is the page of ONE meter in the monitor's tab widget:

    [identity line: polled address, address the meter reports, serial, rows]
    [View: by unit | by table | overlay]  [Show: last 1 h]  [x] Follow newest  [Fit all]
    +------------------------------------------+-----------------------------+
    | TracePlot: Voltage [V]                   | legend column               |
    |   every voltage of this meter, crosshair | [x] trace  at cursor  newest|
    +------------------------------------------+-----------------------------+
    | TracePlot: Current [A]  ...              |                             |

* The plots update IN PLACE (curve.setData), so zoom and pan survive every
  poll; the time axes of one meter are linked; a crosshair follows the mouse
  in every plot of the meter and the legend column lists each trace's value
  at the cursor time and its newest value [P1]-[P3].
* Mouse: drag = pan, right-drag = zoom one axis, Ctrl+wheel = zoom (a plain
  wheel scrolls the tab, LIVE_PLOT_WHEEL_NEEDS_CTRL), right-click = pyqtgraph
  menu (incl. Export: CSV / PNG / SVG), "A" corner button = auto range.
  Any manual zoom or pan switches "Follow newest" off, so a poll never moves
  the view from under the mouse.
* The legend sits in its own column, so it never covers the newest data.

DATA AND MEMORY
---------------
The panel never touches the store itself: it calls source.preview_groups()
and source.meter_identity() of the monitor module that created it (passed in,
NOT imported - the monitor may run as __main__, and a second import would see
an empty store). preview_groups() reads only TIME_SERIES_STORE[ip].
Traces are min/max decimated to LIVE_PLOT_MAX_POINTS; pyqtgraph thins them
further to the screen ("peak" downsampling, clip to view). A hidden panel can
drop its arrays (release()) and is refilled when shown again.

REFERENCES
----------
[P1] pyqtgraph developers, "pyqtgraph documentation," 2026.
     https://pyqtgraph.readthedocs.io/en/latest/
[P2] pyqtgraph developers, "DateAxisItem."
     https://pyqtgraph.readthedocs.io/en/latest/api_reference/graphicsItems/dateaxisitem.html
[P3] pyqtgraph developers, "PlotDataItem" (setData, downsampling, clipToView,
     connect="finite").
     https://pyqtgraph.readthedocs.io/en/latest/api_reference/graphicsItems/plotdataitem.html
[P4] Qt Group, "QScrollArea Class," Qt 6 documentation (wheel events that a
     child ignores propagate to the scroll area). https://doc.qt.io/qt-6/qscrollarea.html

REVISION HISTORY
----------------
0.1.0  2026-10-27  First integrated version (monitor 1.7.0), from the
                   prototype 0.0.1: legend and values in a column beside each
                   plot, crosshair shared by all plots of a meter, three views,
                   window presets, identity line, gaps for outages,
                   Ctrl+wheel zoom in a scrolling tab, start-up self test.
                   Found while testing and fixed before release: pyqtgraph's
                   parentless menus outlived deleted plots (PySide6 6.2);
                   setData([], []) kept display arrays (now clear());
                   QTableWidgetItem.setTextAlignment(int) is deprecated in
                   PySide6 >= 6.4 (now the Qt.Alignment overload, int on 6.2).
===============================================================================
"""

# %% ===========================================================================
#    IMPORTS
# =============================================================================
from __future__ import annotations

import datetime
import logging
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

os.environ.setdefault("QT_API", "pyside6")        # qtpy and pyqtgraph pick the same binding

import numpy as np  # noqa: E402
from qtpy import QtGui, QtWidgets  # noqa: E402
from qtpy.QtCore import Qt, Signal  # noqa: E402
import pyqtgraph as pg  # noqa: E402

__version__ = "0.1.0"
logger = logging.getLogger("ABMonitor")

# %% ===========================================================================
#    SETTINGS (the monitor passes its LIVE_PLOT_* switches; these are defaults)
# =============================================================================
#: Trace colours: Tableau 10 (distinguishable with common colour-vision deficiencies).
COLOURS: Tuple[str, ...] = ("#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
                            "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac")
#: Units whose axis may use SI prefixes (33 000 W -> 33 kW); the others stay as they are.
SI_UNITS = frozenset({"V", "A", "W", "VAR", "VA", "Hz", "s"})
#: (background, foreground, crosshair) per theme; dark matches the monitor's style sheet.
THEMES: Dict[str, Tuple[str, str, str]] = {"dark": ("#1e1e2e", "#cdd6f4", "#9399b2"),
                                          "light": ("#ffffff", "#202020", "#707070")}
#: "Show" presets of the follow window: (text, seconds); 0 = all data in memory.
WINDOW_PRESETS: Tuple[Tuple[str, int], ...] = (
    ("last 10 min", 600), ("last 30 min", 1800), ("last 1 h", 3600), ("last 3 h", 10800),
    ("last 12 h", 43200), ("last 24 h", 86400), ("last 7 d", 604800), ("all in memory", 0))
VIEW_TEXT: Dict[str, str] = {"unit": "by unit", "table": "by table", "overlay": "overlay groups"}
PLOT_HEIGHT = 270              # px per plot row
LEGEND_WIDTH = 330             # px of the legend column (user can drag the splitter)
SYMBOL_MAX_POINTS = 200        # draw point symbols only up to this many points per trace
WHEEL_NEEDS_CTRL = True        # overridden by the monitor's LIVE_PLOT_WHEEL_NEEDS_CTRL


# %% ===========================================================================
#    SMALL HELPERS
# =============================================================================
def _ctrl_pressed(event: Any) -> bool:
    """True when Ctrl is held during a (scene) wheel event (PySide6 6.2 and 6.12)."""
    try:
        return bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
    except Exception:                              # pragma: no cover - unusual binding
        return False


def _local_text(epoch: Optional[float], seconds: bool = True) -> str:
    """Local wall-clock text of a Unix time ('-' when unknown)."""
    if epoch is None or not np.isfinite(epoch) or epoch <= 0:
        return "-"
    stamp = datetime.datetime.fromtimestamp(float(epoch))
    return stamp.strftime("%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M")


def _value_text(value: float, unit: str) -> str:
    """Compact number with unit; '-' for NaN."""
    if value is None or not np.isfinite(value):
        return "-"
    return "{0:.6g} {1}".format(float(value), unit).rstrip()


def _align_right(item: Any) -> None:
    """Right-align a table cell: Qt.Alignment on PySide6 >= 6.4 (the int overload
    is deprecated there), int on PySide6 6.2 (Python 3.7), whose overload takes int."""
    alignment = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    try:
        item.setTextAlignment(alignment)
    except TypeError:                              # PySide6 6.2: int only
        item.setTextAlignment(int(alignment))


def _swatch(colour: str) -> Any:
    """Small square icon in a trace colour (legend column)."""
    pixmap = QtGui.QPixmap(12, 12)
    pixmap.fill(QtGui.QColor(colour))
    return QtGui.QIcon(pixmap)


# %% ===========================================================================
#    pyqtgraph items: a plain wheel scrolls the tab, Ctrl+wheel zooms
# =============================================================================
# A plot inside a scroll area would otherwise eat every wheel event, and
# scrolling down the tab would zoom the plot under the mouse instead. Without
# Ctrl the plot scrolls the surrounding QScrollArea itself [P4]: an ignored
# event would only travel on when Qt marks it "spontaneous", which a
# QGraphicsView's own scroll-bar hand-off does not guarantee.
def _scroll_enclosing_area(event: Any) -> bool:
    """Scroll the nearest QScrollArea around the plot by the wheel's steps.

    Returns False when the plot is not inside a scroll area (the caller then
    ignores the event). One notch (120 eighths of a degree) scrolls the
    system's "wheel scroll lines" times the bar's single step, like Qt does.
    """
    widget = event.widget() if hasattr(event, "widget") else None
    while widget is not None and not isinstance(widget, QtWidgets.QScrollArea):
        widget = widget.parentWidget()
    if widget is None:
        return False
    bar = widget.verticalScrollBar()
    notches = float(event.delta()) / 120.0
    lines = QtWidgets.QApplication.wheelScrollLines()
    bar.setValue(bar.value() - int(round(notches * lines * bar.singleStep())))
    return True


def _wheel_passes_through(event: Any) -> bool:
    """True when the wheel must NOT zoom: scroll the tab instead (event accepted)."""
    if not WHEEL_NEEDS_CTRL or _ctrl_pressed(event):
        return False
    if _scroll_enclosing_area(event):
        event.accept()
    else:
        event.ignore()
    return True


class _ViewBox(pg.ViewBox):
    """ViewBox whose wheel zoom needs Ctrl (when WHEEL_NEEDS_CTRL)."""

    def wheelEvent(self, ev: Any, axis: Any = None) -> None:  # noqa: N802 - Qt override
        if not _wheel_passes_through(ev):
            super().wheelEvent(ev, axis)


class _AxisItem(pg.AxisItem):
    """Value axis: the wheel over it zooms only with Ctrl (it would always accept)."""

    def wheelEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        if not _wheel_passes_through(event):
            super().wheelEvent(event)


class _DateAxisItem(pg.DateAxisItem):
    """Local-time axis with the same wheel rule."""

    def wheelEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        if not _wheel_passes_through(event):
            super().wheelEvent(event)


class _PlotWidget(pg.PlotWidget):
    """PlotWidget that reports when the mouse leaves it (to clear the crosshair)."""
    left = Signal()

    def leaveEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().leaveEvent(event)
        self.left.emit()


# %% ===========================================================================
#    START-UP CHECK
# =============================================================================
def self_test() -> Optional[str]:
    """Build and delete one plot; None when pyqtgraph works, else the reason.

    pyqtgraph 0.12.4 imports fine with PySide6 >= 6.3 but fails as soon as a
    plot gets a title (LabelItem), so importing alone proves nothing. Needs a
    QApplication.
    """
    plot = None
    try:
        plot = TracePlot("self-test", "V", ["a"], dark=False)
        plot.set_traces([("a", np.array([1.0, 2.0]), np.array([3.0, 4.0]))])
        plot.set_cursor(1.5, 3.5)
        return None
    except Exception as exc:                       # anything: the caller falls back
        return "{0}: {1}".format(type(exc).__name__, exc)
    finally:
        if plot is not None:
            plot.dispose()
            plot.setParent(None)
            plot.deleteLater()


def configure(antialias: bool = True, wheel_needs_ctrl: bool = True) -> None:
    """Global pyqtgraph options and the wheel rule (call once, before plots exist)."""
    global WHEEL_NEEDS_CTRL
    WHEEL_NEEDS_CTRL = bool(wheel_needs_ctrl)
    pg.setConfigOptions(antialias=bool(antialias))


# %% ===========================================================================
#    ONE PLOT: all traces of one group (unit / table+unit / overlay group)
# =============================================================================
class TracePlot(QtWidgets.QWidget):
    """A pyqtgraph plot with its legend-and-values column beside it.

    Signals
    -------
    hovered(float, float) : the mouse is over the data area at (time, value)
    unhovered()           : the mouse left the data area
    manualRange()         : the user zoomed or panned (mouse or Ctrl+wheel)
    """
    hovered = Signal(float, float)
    unhovered = Signal()
    manualRange = Signal()

    def __init__(self, title: str, unit: str, labels: Sequence[str] = (), dark: bool = False,
                 parent: Any = None) -> None:
        super().__init__(parent)
        self.title, self.unit = title, unit
        self.curves: Dict[str, Any] = {}          # label -> PlotDataItem (created once)
        self.rows: Dict[str, int] = {}            # label -> legend row
        self.data: Dict[str, Tuple[Any, Any]] = {}   # label -> (x, y) as drawn (NaN = gap)
        self._newest: Optional[float] = None       # cache of newest_time(), reset by new data
        self._dark = dark
        splitter = QtWidgets.QSplitter(Qt.Orientation.Horizontal, self)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)
        # --- the plot ----------------------------------------------------------------------
        self.widget = _PlotWidget(viewBox=_ViewBox(),
                                  axisItems={"bottom": _DateAxisItem(orientation="bottom"),
                                             "left": _AxisItem(orientation="left")})
        self.widget.setMinimumHeight(PLOT_HEIGHT)
        self.plot = self.widget.getPlotItem()
        self.view = self.plot.getViewBox()
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("left", "", units=unit)
        self.plot.getAxis("left").enableAutoSIPrefix(unit in SI_UNITS)
        self.view.setAutoVisible(y=True)          # y auto range uses the visible time span only
        self.vline = pg.InfiniteLine(angle=90, movable=False)
        self.hline = pg.InfiniteLine(angle=0, movable=False)
        for line in (self.vline, self.hline):
            self.plot.addItem(line, ignoreBounds=True)
            line.hide()
        self._proxy = pg.SignalProxy(self.plot.scene().sigMouseMoved, rateLimit=30,
                                     slot=self._mouse_moved)
        self.view.sigRangeChangedManually.connect(lambda *_a: self.manualRange.emit())
        self.widget.left.connect(self.unhovered.emit)
        splitter.addWidget(self.widget)
        # --- the legend column: [x] trace | at cursor | newest -----------------------------
        side = QtWidgets.QWidget()
        column = QtWidgets.QVBoxLayout(side)
        column.setContentsMargins(4, 2, 2, 2)
        column.setSpacing(2)
        self.header = QtWidgets.QLabel()
        self.header.setTextFormat(Qt.TextFormat.RichText)
        column.addWidget(self.header)
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Trace", "At cursor", "Newest"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.verticalHeader().setDefaultSectionSize(20)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        for col in (1, 2):
            head.setSectionResizeMode(col, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(self._legend_toggled)
        column.addWidget(self.table, 1)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([900, LEGEND_WIDTH])
        self.apply_theme(dark)
        self._set_header(None)

    # ---------------------------------------------------------------------------------
    # data
    # ---------------------------------------------------------------------------------
    def set_traces(self, traces: Sequence[Tuple[str, Any, Any]]) -> None:
        """Show ``traces`` [(label, unix_times, values)]: curves are made once, then setData().

        A trace that is no longer delivered is emptied (it keeps its colour and
        legend row, so colours never shuffle between polls).
        """
        seen = set()
        self._newest = None
        for label, x, y in traces:
            seen.add(label)
            curve = self.curves.get(label)
            if curve is None:
                curve = self._add_curve(label)
            curve.setData(x, y, connect="finite")
            symbol = "o" if int(np.isfinite(y).sum()) <= SYMBOL_MAX_POINTS else None
            if curve.opts.get("symbol") != symbol:   # setSymbol() redraws: only on change
                curve.setSymbol(symbol)
            self.data[label] = (x, y)
        for label in list(self.data):
            if label not in seen:
                self.curves[label].clear()
                self.data[label] = (np.empty(0), np.empty(0))
        self._update_newest()

    def _add_curve(self, label: str) -> Any:
        colour = COLOURS[len(self.curves) % len(COLOURS)]
        curve = self.plot.plot([], [], pen=pg.mkPen(colour, width=1.5), symbol=None, symbolSize=4,
                               symbolBrush=colour, symbolPen=None)
        curve.setDownsampling(auto=True, method="peak")   # thin to the screen, keep spikes
        curve.setClipToView(True)
        self.curves[label] = curve
        row = self.table.rowCount()
        self.table.blockSignals(True)
        self.table.insertRow(row)
        item = QtWidgets.QTableWidgetItem(_swatch(colour), label)
        item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        item.setCheckState(Qt.CheckState.Checked)
        item.setToolTip("Untick to hide this trace")
        self.table.setItem(row, 0, item)
        for col in (1, 2):
            cell = QtWidgets.QTableWidgetItem("-")
            _align_right(cell)
            self.table.setItem(row, col, cell)
        self.table.blockSignals(False)
        self.rows[label] = row
        return curve

    def release(self) -> None:
        """Drop every array (hidden tab); curves and legend rows stay for the refill.

        PlotDataItem.clear(), not setData([], []): with empty data pyqtgraph
        skips the redraw, so its inner PlotCurveItem kept the last display
        arrays - views that held the whole decimated traces alive (6.9 MB of
        44 traces at 50 000 rows, tests/live_plot_memory_check.py).
        """
        for label, curve in self.curves.items():
            curve.clear()
            self.data[label] = (np.empty(0), np.empty(0))
        self._newest = None

    def dispose(self) -> None:
        """Free what deleting the widget does not: pyqtgraph's parentless menus.

        PlotItem and ViewBox build their right-click menus without a Qt parent
        (pyqtgraph 0.12.4: 11 QMenus per plot). With PySide6 6.2 they outlived
        the deleted plot - 351 menus per view change in tests/test_live_plots.py
        - so they are deleted here explicitly before the plot itself.
        """
        try:
            self._proxy.disconnect()
        except Exception:                          # already disconnected / scene gone
            pass
        menus: List[Any] = [getattr(self.plot, "ctrlMenu", None)]
        menus.extend(getattr(self.plot, "subMenus", None) or [])
        view_menu = getattr(self.view, "menu", None)
        if view_menu is not None:
            menus.extend(getattr(view_menu, "axes", None) or [])
            menus.append(getattr(view_menu, "leftMenu", None))
            menus.append(view_menu)
        for menu in menus:
            if isinstance(menu, QtWidgets.QMenu):
                try:
                    menu.deleteLater()
                except RuntimeError:               # C++ object already gone
                    pass

    def newest_time(self) -> Optional[float]:
        """Newest finite sample time of all traces (None if no data); cached per update."""
        if self._newest is None and self.data:
            times = [float(x[np.isfinite(y)][-1]) for x, y in self.data.values()
                     if len(x) and np.isfinite(y).any()]
            self._newest = max(times) if times else None
        return self._newest

    def oldest_time(self) -> Optional[float]:
        times = [float(x[np.isfinite(y)][0]) for x, y in self.data.values()
                 if len(x) and np.isfinite(y).any()]
        return min(times) if times else None

    # ---------------------------------------------------------------------------------
    # crosshair and values
    # ---------------------------------------------------------------------------------
    def value_at(self, label: str, x: float) -> Tuple[Optional[float], Optional[float]]:
        """(sample time, value) of trace ``label`` nearest to time ``x`` (finite samples only).

        Returns (None, None) when the nearest sample is farther than 1.5 x the
        trace's median step (the cursor is in a gap, before or after the data).
        """
        xs, ys = self.data.get(label, (np.empty(0), np.empty(0)))
        ok = np.isfinite(ys)
        if not ok.any():
            return None, None
        xs, ys = xs[ok], ys[ok]
        index = int(np.clip(np.searchsorted(xs, x), 0, len(xs) - 1))
        if index > 0 and abs(xs[index - 1] - x) <= abs(xs[index] - x):
            index -= 1
        if len(xs) > 1:
            limit = 1.5 * float(np.median(np.diff(xs)))
            if abs(xs[index] - x) > max(limit, 1.0):
                return None, None
        return float(xs[index]), float(ys[index])

    def set_cursor(self, x: Optional[float], y: Optional[float] = None) -> Dict[str, Optional[float]]:
        """Crosshair at time ``x`` (and value ``y`` on the hovered plot); fill the column.

        x None clears the cursor. Returns {label: value at the cursor} (tests).
        """
        values: Dict[str, Optional[float]] = {}
        self.table.blockSignals(True)
        if x is None:
            self.vline.hide()
            self.hline.hide()
            for row in self.rows.values():
                self.table.item(row, 1).setText("")
            self.table.blockSignals(False)
            self._set_header(None)
            return values
        self.vline.setPos(x)
        self.vline.show()
        if y is None:
            self.hline.hide()
        else:
            self.hline.setPos(y)
            self.hline.show()
        for label, row in self.rows.items():
            _t, value = self.value_at(label, x)
            values[label] = value
            self.table.item(row, 1).setText(_value_text(value, self.unit) if value is not None else "-")
        self.table.blockSignals(False)
        self._set_header(x)
        return values

    def _update_newest(self) -> None:
        self.table.blockSignals(True)
        for label, row in self.rows.items():
            xs, ys = self.data.get(label, (np.empty(0), np.empty(0)))
            ok = np.isfinite(ys)
            self.table.item(row, 2).setText(_value_text(ys[ok][-1], self.unit) if ok.any() else "-")
        self.table.blockSignals(False)
        if not self.vline.isVisible():
            self._set_header(None)

    def _set_header(self, x: Optional[float]) -> None:
        newest = self.newest_time() if self.curves else None
        cursor = "cursor <b>{0}</b>".format(_local_text(x)) if x is not None else "cursor -"
        self.header.setText("<b>{0}</b><br>{1} &nbsp;|&nbsp; newest {2}".format(
            self.title, cursor, _local_text(newest)))

    def _mouse_moved(self, event: Any) -> None:
        pos = event[0]
        if not self.view.sceneBoundingRect().contains(pos):
            self.unhovered.emit()
            return
        point = self.view.mapSceneToView(pos)
        self.hovered.emit(float(point.x()), float(point.y()))

    def _legend_toggled(self, item: Any) -> None:
        if item.column() != 0:
            return
        curve = self.curves.get(item.text())
        if curve is not None:
            curve.setVisible(item.checkState() == Qt.CheckState.Checked)

    # ---------------------------------------------------------------------------------
    # look
    # ---------------------------------------------------------------------------------
    def apply_theme(self, dark: bool) -> None:
        self._dark = bool(dark)
        background, foreground, cross = THEMES["dark" if dark else "light"]
        self.widget.setBackground(background)
        for name in ("left", "bottom"):
            axis = self.plot.getAxis(name)
            axis.setPen(foreground)
            axis.setTextPen(foreground)
        pen = pg.mkPen(cross, style=Qt.PenStyle.DashLine)
        self.vline.setPen(pen)
        self.hline.setPen(pen)


# %% ===========================================================================
#    ONE METER: identity line, view controls and a scrolling column of plots
# =============================================================================
class MeterPanel(QtWidgets.QWidget):
    """The tab of one meter. Call refresh() after a poll (only for the visible tab).

    Parameters
    ----------
    ip : str
        The meter; every number shown comes from source.preview_groups(ip, ...).
    source : module or object
        Provides preview_groups(ip, view, max_points) and meter_identity(ip)
        (the monitor module itself).
    """

    def __init__(self, ip: str, source: Any, dark: bool = False, view: str = "unit",
                 window_s: int = 3600, max_points: int = 10000, parent: Any = None) -> None:
        super().__init__(parent)
        self.ip, self.source = ip, source
        self.view_name = view if view in VIEW_TEXT else "unit"
        self.max_points = int(max_points)
        self.window_s = int(window_s)
        self.dark = bool(dark)
        self.dirty = True                         # needs refresh() before it is shown
        self.plots: Dict[Tuple[Any, ...], TracePlot] = {}
        self.last_poll: Dict[str, Any] = {}
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)
        # --- identity line --------------------------------------------------------------
        self.identity = QtWidgets.QLabel("Meter {0}: no data yet".format(ip))
        self.identity.setTextFormat(Qt.TextFormat.RichText)
        self.identity.setWordWrap(True)
        outer.addWidget(self.identity)
        # --- controls ------------------------------------------------------------------------
        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel("View:"))
        self.view_box = QtWidgets.QComboBox()
        for key, text in VIEW_TEXT.items():
            self.view_box.addItem(text, key)
        self.view_box.setCurrentIndex(list(VIEW_TEXT).index(self.view_name))
        self.view_box.currentIndexChanged.connect(self._view_changed)
        bar.addWidget(self.view_box)
        bar.addWidget(QtWidgets.QLabel("Show:"))
        self.window_box = QtWidgets.QComboBox()
        for text, seconds in WINDOW_PRESETS:
            self.window_box.addItem(text, seconds)
        presets = [s for _t, s in WINDOW_PRESETS]
        self.window_box.setCurrentIndex(presets.index(self.window_s) if self.window_s in presets
                                        else presets.index(3600))
        self.window_s = int(self.window_box.currentData())
        self.window_box.currentIndexChanged.connect(self._window_changed)
        bar.addWidget(self.window_box)
        self.follow = QtWidgets.QCheckBox("Follow newest")
        self.follow.setChecked(True)
        self.follow.setToolTip("Keep the newest reading at the right edge.\n"
                               "Any zoom or pan with the mouse switches this off.")
        self.follow.toggled.connect(lambda on: on and self.apply_follow())
        bar.addWidget(self.follow)
        fit = QtWidgets.QPushButton("Fit all")
        fit.setToolTip("Show all data in memory (and follow it)")
        fit.clicked.connect(self.fit_all)
        bar.addWidget(fit)
        bar.addStretch(1)
        hint = QtWidgets.QLabel("drag: pan · right-drag: zoom axis · "
                                + ("Ctrl+wheel" if WHEEL_NEEDS_CTRL else "wheel")
                                + ": zoom · right-click: menu / export")
        hint.setStyleSheet("color: #8c8fa1;")
        bar.addWidget(hint)
        outer.addLayout(bar)
        # --- scrolling column of plots ---------------------------------------------------
        self.page = QtWidgets.QWidget()
        self.column = QtWidgets.QVBoxLayout(self.page)
        self.column.setContentsMargins(0, 0, 0, 0)
        self.column.setSpacing(6)
        self.empty = QtWidgets.QLabel("No readings of this meter in memory yet.")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.column.addWidget(self.empty)
        self.column.addStretch(1)
        self.scroller = QtWidgets.QScrollArea()
        self.scroller.setWidgetResizable(True)
        self.scroller.setWidget(self.page)
        outer.addWidget(self.scroller, 1)

    # ---------------------------------------------------------------------------------
    # refresh
    # ---------------------------------------------------------------------------------
    def refresh(self) -> int:
        """Re-read this meter's data and update the plots in place; returns the trace count."""
        groups = self.source.preview_groups(self.ip, self.view_name, self.max_points)
        wanted = [g["key"] for g in groups]
        for key in [k for k in self.plots if k not in wanted]:     # group vanished (flush, view)
            self._remove_plot(key)
        traces = 0
        master = None
        for position, group in enumerate(groups):
            plot = self.plots.get(group["key"])
            if plot is None:
                plot = TracePlot(group["title"], group["unit"], dark=self.dark)
                plot.hovered.connect(self._hovered)
                plot.unhovered.connect(self._unhovered)
                plot.manualRange.connect(self._manual_range)
                self.column.insertWidget(position, plot)
                self.plots[group["key"]] = plot
            plot.set_traces(group["traces"])
            traces += len(group["traces"])
        ordered = [self.plots[k] for k in wanted]
        for plot in ordered[1:]:                   # one meter: zoom in time together
            master = ordered[0]
            if plot.view.linkedView(pg.ViewBox.XAxis) is not master.view:
                plot.plot.setXLink(master.plot)
        self.empty.setVisible(not groups)
        self._refresh_identity()
        self.dirty = False
        if self.follow.isChecked():
            self.apply_follow()
        return traces

    def release(self) -> None:
        """Free the plot arrays of this (hidden) tab; the next refresh() refills them."""
        for plot in self.plots.values():
            plot.release()
        self.dirty = True

    def set_poll_status(self, status: Dict[str, Any]) -> None:
        """Remember what the last poll said about this meter (identity line)."""
        self.last_poll = {"answered": self.ip in status.get("ok_ips", []),
                          "failed": self.ip in status.get("failed_ips", []),
                          "reported": (status.get("reported_ips") or {}).get(self.ip),
                          "cycle": status.get("cycle")}

    def _refresh_identity(self) -> None:
        ident = self.source.meter_identity(self.ip)
        parts = ["<b>Meter {0}</b>".format(self.ip)]
        if ident.get("reported_ip") is None:
            parts.append("does not list its address")
        elif ident.get("matches"):
            parts.append("reports {0} <span style='color:#40a02b'>(matches)</span>".format(
                ident["reported_ip"]))
        else:
            parts.append("<span style='color:#e64553'><b>reports {0} - NOT the polled address"
                         "</b></span>".format(ident["reported_ip"]))
        if ident.get("serial"):
            parts.append("serial {0}".format(ident["serial"]))
        if ident.get("catalog"):
            parts.append("catalog {0}".format(ident["catalog"]))
        parts.append("{0} rows in memory, newest {1}".format(ident.get("rows", 0),
                                                              _local_text(ident.get("newest_epoch"))))
        if self.last_poll.get("failed"):
            parts.append("<span style='color:#e64553'>no answer in poll {0}</span>".format(
                self.last_poll.get("cycle")))
        elif self.last_poll.get("answered"):
            parts.append("answered poll {0}".format(self.last_poll.get("cycle")))
        self.identity.setText(" &nbsp;·&nbsp; ".join(parts))

    def _remove_plot(self, key: Tuple[Any, ...]) -> None:
        plot = self.plots.pop(key)
        plot.dispose()
        self.column.removeWidget(plot)
        plot.setParent(None)
        plot.deleteLater()

    def clear_plots(self) -> None:
        """Delete every plot (view change); widgets are freed by deleteLater()."""
        for key in list(self.plots):
            self._remove_plot(key)
        self.dirty = True

    # ---------------------------------------------------------------------------------
    # view, follow, crosshair
    # ---------------------------------------------------------------------------------
    def _view_changed(self, _index: int) -> None:
        self.view_name = str(self.view_box.currentData())
        self.clear_plots()
        self.refresh()

    def _window_changed(self, _index: int) -> None:
        self.window_s = int(self.window_box.currentData())
        if self.follow.isChecked():
            self.apply_follow()
        else:
            self.follow.setChecked(True)            # toggled -> apply_follow()

    def fit_all(self) -> None:
        """Show everything in memory and keep following it."""
        self.window_box.setCurrentIndex([s for _t, s in WINDOW_PRESETS].index(0))
        self.follow.setChecked(True)
        self.apply_follow()

    def apply_follow(self) -> None:
        """x range = the chosen window ending at the newest reading; y auto (visible data)."""
        if not self.plots:
            return
        newest = [t for t in (p.newest_time() for p in self.plots.values()) if t is not None]
        oldest = [t for t in (p.oldest_time() for p in self.plots.values()) if t is not None]
        if not newest:
            return
        right, first = max(newest), min(oldest)
        left = first if self.window_s <= 0 else max(first, right - self.window_s)
        if right - left < 1.0:                     # one sample: show +- 30 s around it
            left, right = left - 30.0, right + 30.0
        pad = 0.02 * (right - left)
        master = next(iter(self.plots.values()))
        master.plot.setXRange(left, right + pad, padding=0.0)
        for plot in self.plots.values():
            plot.view.enableAutoRange(axis=pg.ViewBox.YAxis)

    def _manual_range(self) -> None:
        if self.follow.isChecked():
            self.follow.blockSignals(True)
            self.follow.setChecked(False)
            self.follow.blockSignals(False)

    def _hovered(self, x: float, y: float) -> None:
        source = self.sender()
        for plot in self.plots.values():
            plot.set_cursor(x, y if plot is source else None)

    def _unhovered(self) -> None:
        for plot in self.plots.values():
            plot.set_cursor(None)

    def apply_theme(self, dark: bool) -> None:
        self.dark = bool(dark)
        for plot in self.plots.values():
            plot.apply_theme(dark)
