#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# %% ===========================================================================
# Title: Tests for View > Time display, fit-window presets and Veusz export | Version: 0.0.1
# Author: William W. Wallace | Created/Modified: 2026-11-05 | Python: 3.12
# Revision: 0.0.1 MJD <-> ISO 8601 (local, DST-aware; UTC), the user's "-5:00"
#           form, TimeAxisItem ticks/labels, TimeEdit, every GUI time control
#           following the menu, last 30/90-day windows only when the data span
#           them, 90-day comparison, export dialog with the .vszh5 path,
#           Veusz discovery, batch --start/--end with offsets.
# =============================================================================
import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]
FRANKS = sorted((ROOT / "examples" / "franks_2026_01_07").glob("*.t00new"))
NEW_YORK = hasattr(time, "tzset")


@pytest.fixture()
def new_york(monkeypatch):
    """Run in America/New_York (the GBO zone); skipped where tzset is unavailable (Windows)."""
    if not NEW_YORK:
        pytest.skip("time.tzset not available")
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


# %% Formatting and parsing ------------------------------------------------------
def test_format_and_parse(new_york):
    from time_format import format_time, parse_time, axis_label, span_text
    mjd = parse_time("2026-11-05T15:47:13-5:00", "local")              # the operator's example
    assert mjd == pytest.approx(61349 + (20 * 3600 + 47 * 60 + 13) / 86400, abs=1e-9)
    assert format_time(mjd, "local") == "2026-11-05T15:47:13-05:00"
    assert format_time(mjd, "utc") == "2026-11-05T20:47:13+00:00"
    assert format_time(mjd, "mjd") == "61349.866123"
    assert format_time(61222.5, "local") == "2026-07-01T08:00:00-04:00"   # daylight saving time
    for text, mode in (("2026-11-05 15:47:13", "local"), ("2026-11-05T20:47:13Z", "utc"),
                       ("2026-11-05T20:47:13", "utc"), ("61349.86612268518", "local")):
        assert parse_time(text, mode) == pytest.approx(mjd, abs=2e-9)
    with pytest.raises(ValueError):
        parse_time("yesterday", "local")
    with pytest.raises(ValueError):
        parse_time("12", "mjd")
    assert axis_label("local", 61222.5, 61349.9) == "Local date-time, ISO 8601 (UTC-04:00 then -05:00)"
    assert axis_label("mjd", origin=61222.0) == "MJD - 61222.00000"
    assert span_text(61349.5, 61349.75, "mjd") == "MJD 61349.50000 to 61349.75000"


# %% Qt widgets -----------------------------------------------------------------
@pytest.fixture(scope="module")
def app():
    from qtpy import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_axis_ticks(app, new_york):
    from qtpy import QtGui
    from time_axis import TimeAxisItem
    axis = TimeAxisItem(mode="mjd")
    assert axis.tickStrings([61349.71, 61349.72], 1.0, 0.01) == ["61349.71", "61349.72"]   # not 61349.7 twice
    axis.set_mode("local")
    axis.fontMetrics = QtGui.QFontMetrics(QtGui.QFont())
    levels = axis.tickValues(61349.70, 61349.77, 900)
    assert len(levels) == 1 and len(levels[0][1]) >= 4
    labels = axis.tickStrings(levels[0][1], 1.0, levels[0][0])
    assert labels[0].startswith("2026-11-05\nT") and labels[0].endswith("-05:00")
    narrow = TimeAxisItem(mode="local", origin=61204.1)
    narrow.fontMetrics = axis.fontMetrics
    values = narrow.tickValues(0.0, 30.0, 380)[0][1]
    assert len(values) >= 3 and min(np.diff(values)) >= 5                  # 30 days on a narrow plot


def test_time_edit(app, new_york):
    from time_axis import TimeEdit
    edit = TimeEdit(61349.866122685, "local")
    assert edit.text() == "2026-11-05T15:47:13-05:00"
    edit.set_mode("utc")
    assert edit.text() == "2026-11-05T20:47:13+00:00"
    edit.set_mode("local")
    edit.lineEdit().setText("2026-07-04T12:00:00-4:00")
    edit.interpretText()
    assert edit.value() == pytest.approx(61225.66666667, abs=1e-8)
    edit.set_mode("mjd")
    assert edit.text() == "61225.666666667"


# %% Main window -----------------------------------------------------------------
@pytest.fixture()
def window(app, new_york):
    from MHM2010_Correction_App import MainWindow
    from maser_io import load_timing
    window = MainWindow()
    window.accept_timing(load_timing(FRANKS))
    window.channel.setCurrentText("MSM-GPS")
    yield window
    box = getattr(window, "export_box", None)
    if box is not None:
        box.close()
    window.close()


def test_view_menu_drives_everything(window, app):
    labels = [a.text() for a in window.time_actions.values()]
    assert any("2026-11-05T15:47:13-05:00" in text for text in labels)
    window.apply_time_mode("local")
    assert window.low.text().endswith(("-04:00", "-05:00")) and window.low_label.text() == "Start (local)"
    assert all(axis.mode == "local" for axis in window.time_axes())
    assert window.reference_widget.time_mode == window.t4_widget.time_mode == window.t4_logs_widget.time_mode == "local"
    assert "T" in window.run_choice.currentText() and "-04:00" in window.run_choice.currentText()
    assert "At 2026-" in window.results.toPlainText()
    assert window.phase_plot.getPlotItem().getAxis("bottom").labelText.startswith("Local date-time")
    window.apply_time_mode("utc")
    assert window.apply_epoch.text().endswith("+00:00")
    window.apply_time_mode("mjd")
    assert window.low_label.text() == "Start MJD" and "MJD" in window.run_choice.currentText()
    from MHM2010_Correction_App import view_settings
    assert view_settings().value("time_display") == "mjd"


def test_last_30_and_90_days(window, app):
    span = window.series.mjd[-1] - window.series.mjd[0]
    assert span > 90
    enabled = {window.window_choice.itemText(i): window.window_choice.model().item(i).isEnabled()
               for i in range(window.window_choice.count())}
    assert enabled["Last 30 days"] and enabled["Last 90 days"]
    window.bridge.setChecked(False)
    window.set_window(90)
    assert window.high.value() - window.low.value() == pytest.approx(90, abs=1e-6)
    assert window.bridge.isChecked() and window.fit is not None          # gaps inside: bridging switched on
    assert window.window_choice.currentText() == "Last 90 days"
    window.set_window(30)
    assert window.high.value() - window.low.value() == pytest.approx(30, abs=1e-6) and window.fit is not None
    window.region.setRegion((window.low.value() + 1, window.high.value()))  # dragged by hand
    assert window.window_choice.currentText() == "Custom (blue interval)"
    window.compare_windows()
    assert "90 d requested" in window.audit.toPlainText()


def test_windows_disabled_without_data(app, new_york):
    from MHM2010_Correction_App import MainWindow
    from maser_io import load_timing
    window = MainWindow()
    try:
        window.accept_timing(load_timing(FRANKS[-1:]))                     # one month only
        window.channel.setCurrentText("MSM-GPS")
        model = window.window_choice.model()
        rows = {window.window_choice.itemText(i): i for i in range(window.window_choice.count())}
        span = float(window.series.mjd[-1] - window.series.mjd[0])        # 12.1 d in the July file
        assert model.item(rows["Last 30 days"]).isEnabled() == (span >= 29.99)
        assert not model.item(rows["Last 90 days"]).isEnabled()
        assert "Needs 90 days" in model.item(rows["Last 90 days"]).toolTip()
    finally:
        window.close()


def test_export_dialog_names_the_veusz_file(window, app, tmp_path):
    from maser_export import export_bundle
    window.set_window(30)
    target = tmp_path / "analysis_out"
    export_bundle(target, window.series, window.fit, window.candidate, None, None, window.settings(), False, .01,
                  window.collection, window.maser_kind(), window.reference)
    window.export_done(target, "analysis.vszh5")
    assert str(target / "analysis.vszh5") in window.export_box.text()
    assert window.open_veusz_button.isEnabled() and window.last_vszh5.is_file()
    buttons = [b.text() for b in window.export_box.buttons()]
    assert "Open in Veusz" in buttons and "Open folder" in buttons


def test_find_veusz(tmp_path, monkeypatch):
    from MHM2010_Correction_App import find_veusz
    program = tmp_path / "Veusz" / "veusz.exe"
    program.parent.mkdir()
    program.write_bytes(b"")
    monkeypatch.setattr("shutil.which", lambda name: None)
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.delenv(variable, raising=False)
    assert find_veusz() == ""
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    assert find_veusz() == str(program)
    assert find_veusz(str(program)) == str(program)


# %% Batch -----------------------------------------------------------------------
def test_batch_iso_offsets_and_window_check(tmp_path, new_york):
    import subprocess
    folder = tmp_path / "franks"
    folder.mkdir()
    for path in FRANKS:
        (folder / path.name).write_bytes(path.read_bytes())
    common = [sys.executable, str(ROOT / "analyze_maser.py"), "--timing-directory", str(folder),
              "--channel", "MSM-GPS", "--order", "1", "--bridge-gaps", "--dds", "5751.6770000"]
    run = subprocess.run(common + ["--start", "2026-06-12T22:29:07-4:00", "--end", "2026-07-12T22:29:08-04:00",
                                   "--output", str(tmp_path / "out")], capture_output=True, text=True,
                         env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
    assert run.returncode == 0, run.stderr
    assert '"veusz_file"' in run.stdout and (tmp_path / "out" / "analysis.vszh5").is_file()
    too_long = subprocess.run(common + ["--last-days", "400", "--output", str(tmp_path / "out2")],
                              capture_output=True, text=True)
    assert too_long.returncode != 0 and "spans only" in too_long.stderr
