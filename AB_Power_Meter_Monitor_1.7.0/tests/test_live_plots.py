#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace <wwallace@nrao.edu>
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Interactive GUI plots (monitor 1.7.0): right data, right meter, no leaks.

===============================================================================
 Title          : AB Power Meter Monitor - interactive plot tests
 Filename       : tests/test_live_plots.py
 Version        : 0.0.1 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu
 Created        : 2026-10-27
 Modified       : 2026-10-27
 Language       : Python 3.7 (PySide6 6.2.4 + pyqtgraph 0.12.4) through 3.12
 Requires       : numpy; for the GUI checks PySide6, qtpy, pyqtgraph,
                  matplotlib (fallback check). Without Qt only the data
                  checks run (the GUI checks print SKIP).
 Licence        : GPL-3.0-or-later (GNU General Public License v3.0 or later)
 Status         : Internal draft
===============================================================================

Data checks (no Qt)
-------------------
D1  preview_groups(ip, view) for every view holds exactly the stored samples
    of THAT meter (equal to TIME_SERIES_STORE[ip]), in its own value band.
D2  Long series are min/max decimated to about max_points; a one-sample
    spike survives.
D3  An outage (time step > factor x median) becomes a NaN gap.
D4  meter_identity(): own serial number and own reported address.

GUI checks (offscreen Qt)
-------------------------
G1  pyqtgraph backend active; one tab per meter, in IP-list order.
G2  Every curve of every plot draws the store's values of that meter
    (crosshair read-out at each sample time = the stored value).
G3  Legend column: unticking a trace hides its curve.
G4  Wheel without Ctrl scrolls the tab and leaves the plot range alone;
    Ctrl+wheel zooms and switches "Follow newest" off; the next poll then
    does not move the view.
G5  View changes and tab switches leave no widgets behind; a hidden tab
    holds no plot arrays (LIVE_PLOT_RELEASE_HIDDEN) and refills when shown.
G6  Fallbacks: PREVIEW_BACKEND = "matplotlib" and a failing pyqtgraph
    self-test both give the 1.6 picture previews.

Run:  python tests/test_live_plots.py      (sets QT_QPA_PLATFORM=offscreen)

REVISION HISTORY
----------------
0.0.1  2026-10-27  First version (monitor 1.7.0, ab_live_plots 0.1.0).
===============================================================================
"""

# %% IMPORTS ===================================================================
from __future__ import annotations

import gc
import logging
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402

from ab_meter_emulator import host_scale, start_emulators  # noqa: E402
import ab_power_meter_monitor as abm  # noqa: E402

HOSTS = ["127.0.0.51", "127.0.0.52"]          # scale 1.00 and 1.15
L1_BASE, L1_DRIFT = 120.0, 5.0                 # emulator page 2, "L1 Current"
OUTPUTS = ("enable_csv", "enable_log_append", "enable_veusz", "enable_fits", "enable_xlsx",
           "enable_log_file", "enable_ram_flush")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


PORT = _free_port()
start_emulators(HOSTS, PORT)


def _reset() -> dict:
    """Empty store; emulated meters; every file output off (nothing is written)."""
    with abm._TS_LOCK:
        abm.TIME_SERIES_STORE.clear()
        abm._TS_META.clear()
    abm._RUN_STATE["active"] = False
    abm.METER_URL_TEMPLATE = "http://{ip}:%d/{page}" % PORT
    abm.IP_LIST = list(HOSTS)
    abm.HTTP_TIMEOUT_SEC, abm.HTTP_RETRY_COUNT = 3, 1
    abm.OUTPUT_BASE_DIR = tempfile.mkdtemp(prefix="abm_live_")
    logging.getLogger("ABMonitor").setLevel(logging.WARNING)
    abm.set_console_logging(False)
    cfg = abm.default_config()
    cfg["ip_list"] = list(HOSTS)
    for key in OUTPUTS:
        cfg[key] = 0
    return cfg


def _poll(cfg: dict, n: int, pause: float = 0.25) -> list:
    statuses = []
    for _ in range(n):
        statuses.append(abm.poll_cycle(cfg))
        time.sleep(pause)
    return statuses


def _stored(ip: str) -> dict:
    """{(table, param): (epochs, values)} straight from the store (reference)."""
    out = {}
    with abm._TS_LOCK:
        for tname, tstore in abm.TIME_SERIES_STORE.get(ip, {}).items():
            epochs = np.asarray(abm._aligned_meta(ip, tname, tstore)["epoch"], dtype=float)
            for param, values in tstore["columns"].items():
                out[(tname, param)] = (epochs, np.array(values, dtype=float))
    return out


def _band_ok(ip: str, values) -> bool:
    scale = host_scale(HOSTS.index(ip))
    low, high = (L1_BASE - L1_DRIFT) * scale - 1e-6, (L1_BASE + L1_DRIFT) * scale + 1e-6
    values = [v for v in values if np.isfinite(v)]
    return bool(values) and all(low <= v <= high for v in values)


# %% DATA CHECKS (no Qt) =========================================================
def test_d1_groups_hold_only_this_meters_samples() -> None:
    cfg = _reset()
    _poll(cfg, 3)
    for ip in HOSTS:
        reference = _stored(ip)
        ref_values = {}
        for (tname, param), (_x, y) in reference.items():
            ref_values.setdefault(param, []).append(y)
        for view in abm.PREVIEW_VIEWS:
            groups = abm.preview_groups(ip, view)
            assert groups, (ip, view)
            for group in groups:
                for label, x, y in group["traces"]:
                    param = label.split(" (")[0] if label not in ref_values else label
                    finite = y[np.isfinite(y)]
                    assert any(len(r[np.isfinite(r)]) == len(finite)
                               and np.allclose(r[np.isfinite(r)], finite) for r in ref_values[param]), \
                        (ip, view, label)
        unit_a = {g["key"]: g for g in abm.preview_groups(ip, "unit")}
        l1 = [y for label, _x, y in unit_a[("unit", "A")]["traces"] if label == "L1 Current"][0]
        assert _band_ok(ip, l1), (ip, l1)
    other = [y for label, _x, y in {g["key"]: g for g in abm.preview_groups(HOSTS[1], "unit")}
             [("unit", "A")]["traces"] if label == "L1 Current"][0]
    assert not _band_ok(HOSTS[0], other), "meter 2 values fall into meter 1's band"


def test_d2_decimation_keeps_spikes() -> None:
    _reset()
    n_rows = 50000
    epochs = 1.7e9 + np.arange(n_rows) * 30.0
    values = np.sin(np.arange(n_rows) / 500.0) * 10.0 + 100.0
    values[31337] = 1000.0                                  # one-sample spike
    ip = "192.0.2.1"
    with abm._TS_LOCK:
        abm.TIME_SERIES_STORE[ip] = {"Voltage_Current_Table": {
            "timestamps_local": ["x"] * n_rows, "columns": {"L1 Current": list(values)},
            "units": {"L1 Current": "A"}}}
        abm._TS_META.setdefault(ip, {})["Voltage_Current_Table"] = {
            "seq": [0] * n_rows, "epoch": list(epochs)}
    groups = abm.preview_groups(ip, "unit", max_points=2000)
    _label, x, y = groups[0]["traces"][0]
    assert len(x) <= 2100, len(x)
    assert np.nanmax(y) == 1000.0, "spike lost"
    assert x[-1] == epochs[-1], "newest sample lost"
    with abm._TS_LOCK:
        abm.TIME_SERIES_STORE.pop(ip)
        abm._TS_META.pop(ip)


def test_d3_outage_becomes_gap() -> None:
    x = np.array([0.0, 30.0, 60.0, 90.0, 900.0, 930.0, 960.0])
    y = np.arange(len(x), dtype=float)
    xg, yg = abm._insert_gaps(x, y, 3.0)
    assert len(xg) == len(x) + 1 and np.isnan(yg[4]) and 90.0 < xg[4] < 900.0
    xg2, _ = abm._insert_gaps(x, y, 0.0)
    assert len(xg2) == len(x), "factor 0 must disable gaps"


def test_d4_identity_is_the_meters_own() -> None:
    cfg = _reset()
    _poll(cfg, 1)
    for index, ip in enumerate(HOSTS):
        ident = abm.meter_identity(ip)
        assert ident["reported_ip"] == ip and ident["matches"], ident
        assert ident["serial"] and ident["serial"].endswith(str(index)), ident
        assert ident["rows"] == 1 and ident["newest_epoch"], ident


# %% GUI CHECKS (offscreen) =========================================================
def _qt():
    """(QtCore, QtGui, QtWidgets, app) or None when Qt / pyqtgraph is missing."""
    try:
        from qtpy import QtCore, QtGui, QtWidgets
        import pyqtgraph  # noqa: F401
    except Exception as exc:                      # pragma: no cover - headless host
        print("SKIP GUI checks:", exc)
        return None
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return QtCore, QtGui, QtWidgets, app


def _settle(QtCore, app, ms: int = 30) -> None:
    """Run a real (nested) event loop for ``ms``: paints, queued signals and
    deleteLater() all happen there (PySide6 6.2 does not run deferred deletes
    from processEvents() outside an event loop)."""
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(ms, loop.quit)
    getattr(loop, "exec", None)() if hasattr(loop, "exec") else loop.exec_()
    app.processEvents()
    gc.collect()


def _window(cfg: dict, polls: int):
    QtCore, _QtGui, _QtWidgets, app = _QT
    window = abm.launch_gui(cfg, run_event_loop=False)
    window.resize(1400, 900)
    run_cfg = window._runtime_config()
    for status in _poll(run_cfg, polls):
        window._on_cycle_done(status)
        _settle(QtCore, app)
    return window, run_cfg


def _close(window) -> None:
    QtCore, _QtGui, _QtWidgets, app = _QT
    window._close_ok = True
    window.close()
    window.deleteLater()
    _settle(QtCore, app)


def _wheel(QtCore, QtGui, QtWidgets, widget, ctrl: bool) -> None:
    """Send one wheel notch (down) to the middle of ``widget``'s viewport."""
    target = widget.viewport()
    pos = QtCore.QPointF(target.width() / 2.0, target.height() / 2.0)
    mods = QtCore.Qt.KeyboardModifier.ControlModifier if ctrl else QtCore.Qt.KeyboardModifier.NoModifier
    event = QtGui.QWheelEvent(pos, QtCore.QPointF(target.mapToGlobal(pos.toPoint())),
                              QtCore.QPoint(0, 0), QtCore.QPoint(0, -120),
                              QtCore.Qt.MouseButton.NoButton, mods, QtCore.Qt.ScrollPhase.NoScrollPhase,
                              False)
    QtWidgets.QApplication.sendEvent(target, event)


def test_g1_g2_g3_tabs_values_legend() -> None:
    cfg = _reset()
    window, _run = _window(cfg, 4)
    try:
        assert window._live is not None, "pyqtgraph backend not active"
        tabs = [window._tab_widget.tabText(i) for i in range(window._tab_widget.count())]
        assert tabs == ["Meter %s" % ip for ip in HOSTS], tabs
        for index, ip in enumerate(HOSTS):
            window._tab_widget.setCurrentIndex(index)
            _settle(*_QT[::3])
            panel = window._panels[ip]
            assert not panel.dirty and panel.plots, ip
            reference = _stored(ip)
            by_param = {}
            for (_t, param), (ex, ey) in reference.items():
                by_param.setdefault(param, []).append((ex, ey))
            checked = 0
            for plot in panel.plots.values():
                for label, (x, y) in plot.data.items():
                    param = label if label in by_param else label.split(" (")[0]
                    ok = np.isfinite(y)
                    for t, v in zip(x[ok], y[ok]):       # crosshair at every sample
                        _st, value = plot.value_at(label, float(t))
                        assert value == v
                        assert any(np.any((ex == t) & (ey == v)) for ex, ey in by_param[param]), \
                            (ip, label, t, v)
                        checked += 1
            assert checked > 100, checked
            amps = panel.plots[("unit", "A")]
            assert _band_ok(ip, amps.data["L1 Current"][1]), ip
            shown = amps.set_cursor(float(amps.data["L1 Current"][0][1]))
            assert shown["L1 Current"] == amps.data["L1 Current"][1][1]
            # G3 legend: untick -> hidden, tick -> shown
            item = amps.table.item(amps.rows["L1 Current"], 0)
            item.setCheckState(_QT[0].Qt.CheckState.Unchecked)
            assert not amps.curves["L1 Current"].isVisible()
            item.setCheckState(_QT[0].Qt.CheckState.Checked)
            assert amps.curves["L1 Current"].isVisible()
    finally:
        _close(window)


def test_g4_wheel_scrolls_ctrl_wheel_zooms() -> None:
    QtCore, QtGui, QtWidgets, app = _QT
    cfg = _reset()
    window, run_cfg = _window(cfg, 3)
    try:
        panel = window._panels[HOSTS[0]]
        plot = panel.plots[("unit", "W")]          # third plot: the tab can scroll to it
        bar = panel.scroller.verticalScrollBar()
        assert bar.maximum() > 0, "tab does not scroll - window too tall for the test"
        before_range = plot.view.viewRange()
        before_scroll = bar.value()
        _wheel(QtCore, QtGui, QtWidgets, plot.widget, ctrl=False)
        _settle(QtCore, app)
        assert plot.view.viewRange() == before_range, "plain wheel zoomed the plot"
        assert bar.value() > before_scroll, "plain wheel did not scroll the tab"
        assert panel.follow.isChecked()
        _wheel(QtCore, QtGui, QtWidgets, plot.widget, ctrl=True)
        _settle(QtCore, app)
        zoomed = plot.view.viewRange()
        assert zoomed != before_range, "Ctrl+wheel did not zoom"
        assert not panel.follow.isChecked(), "manual zoom must stop following"
        status = abm.poll_cycle(run_cfg)           # a new poll must not move the view
        window._on_cycle_done(status)
        _settle(QtCore, app)
        assert np.allclose(plot.view.viewRange()[0], zoomed[0]), "poll moved a zoomed view"
        panel.follow.setChecked(True)               # follow again: right edge = newest
        _settle(QtCore, app)
        assert plot.view.viewRange()[0][1] >= plot.newest_time()
    finally:
        _close(window)


def test_g5_no_widget_leak_and_release() -> None:
    QtCore, _QtGui, QtWidgets, app = _QT
    cfg = _reset()
    window, run_cfg = _window(cfg, 2)
    try:
        panel = window._panels[HOSTS[0]]
        _settle(QtCore, app)
        start = len(app.allWidgets())
        for _round in range(3):
            for view in ("table", "overlay", "unit"):
                panel.view_box.setCurrentIndex(list(abm.PREVIEW_VIEWS).index(view))
                _settle(QtCore, app)
            window._on_cycle_done(abm.poll_cycle(run_cfg))
            _settle(QtCore, app)
        end = len(app.allWidgets())
        assert abs(end - start) <= 5, (start, end)
        window._tab_widget.setCurrentIndex(1)       # meter 1 hidden: arrays released
        _settle(QtCore, app)
        assert panel.dirty and all(len(x) == 0 for p in panel.plots.values() for x, _y in p.data.values())
        window._tab_widget.setCurrentIndex(0)       # shown again: refilled
        _settle(QtCore, app)
        assert not panel.dirty and any(len(x) for p in panel.plots.values() for x, _y in p.data.values())
        window._apply_theme(True)
        window._apply_theme(False)
    finally:
        _close(window)


def test_g6_fallbacks_to_pictures() -> None:
    QtCore, _QtGui, _QtWidgets, app = _QT
    import ab_live_plots
    saved_backend, saved_test = abm.PREVIEW_BACKEND, ab_live_plots.self_test
    try:
        abm.PREVIEW_BACKEND = "matplotlib"
        window, _run = _window(_reset(), 1)
        assert window._live is None and window._tabs, "picture previews expected"
        key = window._current_key
        assert key is not None and window._tabs[key]["has_pixmap"]
        _close(window)
        abm.PREVIEW_BACKEND = "pyqtgraph"
        ab_live_plots.self_test = lambda: "simulated pyqtgraph failure"
        window, _run = _window(_reset(), 1)
        assert window._live is None and window._tabs, "self-test failure must fall back"
        _close(window)
    finally:
        abm.PREVIEW_BACKEND, ab_live_plots.self_test = saved_backend, saved_test


# %% MAIN ======================================================================
_QT = _qt()

if __name__ == "__main__":
    started = time.monotonic()
    tests = [test_d1_groups_hold_only_this_meters_samples, test_d2_decimation_keeps_spikes,
             test_d3_outage_becomes_gap, test_d4_identity_is_the_meters_own]
    if _QT is not None:
        tests += [test_g1_g2_g3_tabs_values_legend, test_g4_wheel_scrolls_ctrl_wheel_zooms,
                  test_g5_no_widget_leak_and_release, test_g6_fallbacks_to_pictures]
    for test in tests:
        test()
        print("PASS", test.__name__, flush=True)
    print("all %d tests passed in %.1f s%s" % (len(tests), time.monotonic() - started,
                                               "" if _QT is not None else " (GUI checks skipped)"))
