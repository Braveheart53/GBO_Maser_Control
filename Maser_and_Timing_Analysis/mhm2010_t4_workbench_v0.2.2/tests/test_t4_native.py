#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# %% ===========================================================================
# Title: Tests for T4 native monitoring files (t4_native, exports, GUI tab) | Version: 0.0.1
# Author: William W. Wallace | Created/Modified: 2026-11-05 | Python: 3.12
# Revision: 0.0.1 Real MonitHCB.dat regression (GBO T4, 2026-11-05: 84 records,
#           74 clock-valid, two pre-sync runs, UTC stamps, DD/MM/YY), degree
#           repair, Windows-1252, bad lines, rolled-file merge, date order,
#           routing with MONIT logs, exports (native only, bundle), GUI tab.
# =============================================================================
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from datetime import datetime, timezone
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import h5py
import numpy as np
import pytest

import t4_native
from maser_io import load_t4_files
from maser_export import export_t4_only

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "examples" / "t4_monitoring_2026_11_05" / "MonitHCB.dat"
NOW = datetime(2026, 11, 5, 18, 30, tzinfo=timezone.utc)
HEADER = "TIME\tSet temperature [°C]\tInternal temperature [°C]\tExternal temperature [°C]\tAlarm Status"


def write(path: Path, lines, encoding="utf-8") -> Path:
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode(encoding))
    return path


# %% Real file -----------------------------------------------------------------
def test_real_file_regression():
    assert t4_native.is_native_log(REAL)
    log, = t4_native.load_native_logs([REAL], now=NOW)
    assert log.name == "MonitHCB"
    assert [c.header for c in log.columns] == ["Set temperature [°C]", "Internal temperature [°C]",
                                               "External temperature [°C]", "Alarm Status"]
    assert [c.kind for c in log.columns] == ["numeric", "numeric", "numeric", "text"]
    stats = log.stats
    assert (stats["records"], stats["clock_valid"], stats["clock_not_set"]) == (84, 74, 10)
    runs = stats["clock_not_set_segments"]
    assert [r["count"] for r in runs] == [3, 7]
    assert runs[0]["stamped_from"] == "2006-01-16T21:15:37" and runs[1]["stamped_from"] == "2007-06-06T13:33:30"
    assert stats["first_utc"] == "2026-11-05T17:13:09Z" and stats["last_utc"] == "2026-11-05T18:25:39Z"
    # 2026-11-05 is MJD 61349; 17:13:09 UTC = 0.717465 day.
    assert stats["first_mjd"] == pytest.approx(61349 + (17 * 3600 + 13 * 60 + 9) / 86400, abs=1e-9)
    assert stats["interval_s"]["median"] == 60.0
    assert stats["interval_s"]["min"] == 59.0 and stats["interval_s"]["max"] == 62.0
    assert stats["backward_steps"] == 0
    assert np.all(log.valid("set_temperature") == 21.5)
    assert np.all(log.valid("external_temperature") == 80.0)
    assert np.all(log.status_flag("alarm_status") == 0)
    notes = " ".join(log.notes)
    assert "repaired 3 lost degree sign" in notes and "no external sensor" in notes
    frame = log.frame()
    assert len(frame) == 84 and frame.clock_valid.sum() == 74
    assert frame.source.iloc[0] == "MonitHCB.dat:2" and frame.time_as_written.iloc[-1] == "05/11/26 18:25:39"


def test_real_file_times_are_utc_and_day_first():
    """DMY reads every line and ends at the upload time; MDY loses '16/01/06' and warns."""
    wrong, = t4_native.load_native_logs([REAL], date_order="MDY", now=NOW)
    assert wrong.rejected == {"BAD_TIME": 3}
    assert any("every line reads as DMY" in note for note in wrong.notes)
    log, = t4_native.load_native_logs([REAL], now=NOW)
    assert log.rejected == {} and not any("WARNING" in note for note in log.notes)
    last = log.utc[log.clock_valid][-1].astype("datetime64[s]").astype(datetime).replace(tzinfo=timezone.utc)
    upload = datetime(2026, 11, 5, 13, 27, tzinfo=timezone.utc).replace(hour=18)   # 13:27 EST
    assert 0 < (upload - last).total_seconds() < 180


# %% Synthetic edge cases --------------------------------------------------------
def test_windows_1252_degree_and_bad_lines(tmp_path):
    lines = [HEADER, "05/11/26 10:00:00\t21.5\t21.6\t80\tNo error", "05/11/26 10:01:00\t21.5\t21.6",
             "31/02/26 10:02:00\t21.5\t21.6\t80\tNo error", "05/11/26 10:03:00\t21.5\t21.7\t80\tError 12"]
    path = write(tmp_path / "MonitHCB.dat", lines, encoding="cp1252")
    log, = t4_native.load_native_logs([path], now=NOW)
    assert log.columns[0].unit == "°C"
    assert log.rejected == {"WRONG_FIELD_COUNT": 1, "BAD_TIME": 1}
    assert list(log.status_flag("alarm_status")) == [0.0, 1.0]
    assert any("Windows-1252" in note for note in log.notes)


def test_rolled_files_merge_and_deduplicate(tmp_path):
    first = write(tmp_path / "MonitHCB_20261101_000000.dat",
                  [HEADER] + [f"01/11/26 00:0{m}:00\t21.5\t21.5\t80\tNo error" for m in range(4)])
    second = write(tmp_path / "MonitHCB.dat",
                   [HEADER, "16/01/06 21:15:37\t22\t22\t80\tNo error",
                    "01/11/26 00:03:00\t21.5\t21.5\t80\tNo error",          # overlap: identical record
                    "01/11/26 00:04:00\t21.5\t21.5\t80\tNo error"])
    log, = t4_native.load_native_logs([second, first], now=NOW)      # order of selection must not matter
    assert log.name == "MonitHCB"
    assert log.stats["records"] == 6 and log.stats["clock_valid"] == 5
    assert any("1 identical duplicate" in note for note in log.notes)
    assert log.source[0].startswith("MonitHCB_20261101")              # older rolled file first
    assert len(log.provenance) == 2


def test_different_layouts_stay_separate_and_future_stamps_flagged(tmp_path):
    hcb = write(tmp_path / "MonitHCB.dat", [HEADER, "05/11/26 10:00:00\t21.5\t21.5\t80\tNo error",
                                            "05/11/27 10:00:00\t21.5\t21.5\t80\tNo error"])
    other = write(tmp_path / "MonitXY.dat", ["TIME\tU batt.A [V]\tLOCK [V]",
                                             "05/11/26 10:00:00\t26.1\t4.76", "05/11/26 10:01:00\t26.0\t4.77"])
    logs = t4_native.load_native_logs([hcb, other], now=NOW)
    assert sorted(log.name for log in logs) == ["MonitHCB", "MonitXY"]
    hcb_log = next(log for log in logs if log.name == "MonitHCB")
    assert hcb_log.stats["clock_not_set"] == 1                         # a year in the future


def test_routing_with_monit_text_log(tmp_path):
    from t4_emulator import MANUAL_EXAMPLE
    monit = tmp_path / "monit.log"
    monit.write_text(f"2026-11-05T18:00:00Z {MANUAL_EXAMPLE.decode('ascii')}\n", encoding="ascii")
    result = load_t4_files([REAL, monit])
    assert result.monit is not None and len(result.monit.mjd) == 1
    assert len(result.native) == 1 and result.native[0].stats["clock_valid"] == 74
    assert "kept separate" in result.notes[0]


# %% Exports ---------------------------------------------------------------------
def test_export_native_only(tmp_path):
    logs = t4_native.load_native_logs([REAL], now=NOW)
    target = export_t4_only(tmp_path / "out", None, logs)
    summary = json.loads((target / "t4_telemetry.json").read_text(encoding="utf-8"))
    assert summary["monit"] is None and summary["native"][0]["records"] == 84
    csv = (target / "t4_MonitHCB_records.csv").read_text(encoding="utf-8-sig").splitlines()
    assert len(csv) == 85 and "clock_valid" in csv[0] and "°C" in csv[0]
    with h5py.File(target / "t4_telemetry.vszh5", "r") as handle:
        names = set(handle["Veusz"]["Data"]) if "Veusz" in handle else set(handle)
        text = json.dumps(sorted(names))
    assert "t4log0_monithcb_set_temperature" in text and "t4log0_monithcb_local" in text
    with pytest.raises(ValueError):
        export_t4_only(tmp_path / "empty", None, [])
    with pytest.raises(FileExistsError):
        export_t4_only(target, None, logs)


def test_bundle_includes_native_pages(tmp_path):
    from maser_core import fit_phase
    from maser_io import TimingSeries
    mjd = 61349.5 + np.arange(48) / 24
    series = TimingSeries(mjd, 1e-9 * (mjd - mjd[0]) * 2.0, "Site1Hz -> T4", "FITS")
    from maser_export import export_bundle
    fit = fit_phase(series, mjd[0], mjd[-1], order=1, maser="T4")
    logs = t4_native.load_native_logs([REAL], now=NOW)
    target = export_bundle(tmp_path / "bundle", series, fit, maser="T4", t4_native=logs)
    analysis = json.loads((target / "analysis.json").read_text(encoding="utf-8"))
    assert analysis["t4_native"][0]["clock_valid_records"] == 74
    assert (target / "t4_MonitHCB_records.csv").exists()
    assert "T4 monitoring files" in (target / "review.md").read_text(encoding="utf-8")


# %% GUI -------------------------------------------------------------------------
@pytest.fixture(scope="module")
def app():
    from qtpy import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_gui_monitoring_tab(app):
    from MHM2010_Correction_App import MainWindow
    from maser_io import load_t4_files as loader
    window = MainWindow()
    try:
        result = loader([REAL], **window.t4_logs_widget.reader_options())
        window.accept_t4_files(result)
        widget = window.t4_logs_widget
        assert window.tabs.currentWidget() is widget
        assert widget.model.rowCount() == 84 and widget.columns.count() == 4
        assert "74 clock-valid" in widget.summary.text()
        for mode in ("utc", "local", "mjd"):            # View > Time display drives the tab
            window.apply_time_mode(mode)
            app.processEvents()
            assert widget.time_mode == mode
        window.apply_theme("light")
        assert window.t4_native and window.settings()["t4_monitoring_reader"]["date_order"] == "DMY"
        widget.clear()
        assert window.t4_native == [] and widget.model.rowCount() == 0
    finally:
        window.close()
