#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# %% ===========================================================================
# Title: Tests for both suggestions (frequency AND drift), exact exports, names | Version: 0.0.2
# Author: William W. Wallace | Created/Modified: 2026-11-10 | Python: 3.12
# Revision: 0.0.1 Release 0.2.3: suggested_settings() against correction() and
#           t4_settings(); the box in the GUI and in the Veusz fit page (with
#           headroom); Results box with empty DDS/drift fields; nothing
#           rounded in CSV, Stable32, review.md or text datasets; dataset
#           names (letters, digits, '_') and source_/type_ tags; 12-digit
#           numeric tick format; every HMD column; batch without --dds/--drift.
#           0.0.2 Release 0.2.4: the batch JSON gives the exact application
#           epoch MJD; every *_time_local_ISO_text of the real Franks export
#           gives back its MJD dataset bit for bit (decimal inversion).
# =============================================================================
import json
import os
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]
FRANKS = sorted((ROOT / "examples" / "franks_2026_01_07").glob("*.t00new"))
HMD = ROOT / "examples" / "historical_20251015_20251113" / "telemetry_overlap.csv"
MONIT = ROOT / "examples" / "t4_monitoring_2026_11_05" / "MonitHCB.dat"

from export_names import vname, full, full_strings, write_csv, FullPrecisionDoc, NUMERIC_TICK_FORMAT  # noqa: E402
from maser_core import fit_phase, correction, t4_settings, suggested_settings  # noqa: E402
from maser_io import TimingSeries, load_hmd  # noqa: E402
from maser_plots import reference_set, suggestion_box_lines, headroom_limits  # noqa: E402
from maser_export import export_bundle, export_t4_only, write_stable32  # noqa: E402


def text(value) -> str:
    """h5py gives bytes or str depending on version and storage."""
    return value.decode("utf-8") if isinstance(value, (bytes, np.bytes_)) else str(value)


def quadratic_series(days=30.0, step_h=1.0, label="MSM-GPS", kind="Franks", seed=3):
    """Phase with a known rate and drift: y(t) = 2e-14 + 2*3e-16*t (t in days)."""
    t = np.arange(0.0, days, step_h / 24.0)
    phase_s = (2e-14 * t + 3e-16 * t ** 2) * 86400.0 + np.random.default_rng(seed).normal(0, 1e-10, t.size)
    return TimingSeries(61200.0 + t, phase_s, label, kind)


# %% suggested_settings ----------------------------------------------------------
@pytest.mark.parametrize("units", ["pp18/day", "DDS counts/day"])
@pytest.mark.parametrize("convention", ["output", "dds"])
@pytest.mark.parametrize("sign", [1, -1])
def test_mhm2010_matches_correction(units, convention, sign):
    series = quadratic_series()
    fit = fit_phase(series, order=2)
    epoch = float(series.mjd[-1])
    current = -120.0 if units == "pp18/day" else -13.25
    s = suggested_settings(fit, "MHM2010", epoch, sign, dds="5751.6774093", current_drift=current,
                           drift_units=units, positive_drift=convention)
    c = correction(fit, "5751.6774093", current, epoch, units, convention, True, sign)
    f, d = s["frequency_setting"], s["drift_setting"]
    assert f["dds_change_counts"] == c["delta_dds_counts_unrounded"]          # identical arithmetic
    assert f["new_dds"] == c["new_external_dds"]
    assert d["new_drift_field"] == pytest.approx(c["new_drift_field"], rel=1e-13, abs=1e-12)
    change = d["drift_change_dds_counts_per_day"] if units == "DDS counts/day" else d["drift_change_pp18_per_day"]
    assert change == pytest.approx(c["delta_drift_field"], rel=1e-13, abs=1e-12)
    # both units describe the same drift: pp18 = -c * counts * gain * 1e-7 * 1e18 (current 0)
    s0 = suggested_settings(fit, "MHM2010", epoch, sign, dds="5751.6774093", positive_drift=convention)
    gain = -s0["frequency_setting"]["fractional_frequency_per_dds_count"]
    conv = 1 if convention == "output" else -1
    d0 = s0["drift_setting"]
    assert d0["drift_change_pp18_per_day"] == pytest.approx(
        -conv * d0["drift_change_dds_counts_per_day"] * gain * 1e18 / (1 + s0["residual_fractional_frequency"]),
        rel=1e-12)


def test_known_rate_and_drift_recovered():
    series = quadratic_series(seed=5)
    fit = fit_phase(series, order=2)
    s = suggested_settings(fit, "MHM2010", float(series.mjd[-1]), 1)
    t_end = series.mjd[-1] - 61200.0
    # pytest.approx's default abs=1e-12 would hide everything here: use 3 sigma
    drift, y = s["residual_drift_per_day"], s["residual_fractional_frequency"]
    assert abs(drift - 6e-16) < 3 * s["drift_sigma_per_day"]
    assert abs(y - (2e-14 + 6e-16 * t_end)) < 3 * s["frequency_sigma"]
    # positive y and positive drift: DDS up (lowers the output), drift field down (output convention)
    assert s["frequency_setting"]["dds_change_counts"] > 0
    change = s["drift_setting"]["drift_change_pp18_per_day"]
    assert change == pytest.approx(-drift / (1 + y) * 1e18, rel=1e-14, abs=0)
    assert abs(change + 600) < 3 * s["drift_setting"]["drift_change_pp18_per_day_sigma"]
    assert s["drift_setting"]["drift_change_dds_counts_per_day"] > 0


def test_changes_without_current_fields():
    series = quadratic_series()
    fit = fit_phase(series, order=2)
    s = suggested_settings(fit, "MHM2010", float(series.mjd[-1]), 1)
    assert s["frequency_setting"]["new_dds"] is None and s["frequency_setting"]["dds_assumed_for_gain"]
    assert s["drift_setting"]["new_drift_field"] is None
    assert s["drift_setting"]["drift_change_pp18_per_day"] is not None
    assert any("Current drift field not entered" in note for note in s["notes"])
    lines = suggestion_box_lines(s)
    assert any(line.startswith("DDS change = ") for line in lines)
    assert any(line.startswith("drift change = ") and "pp18/day" in line and "DDS counts/day" in line
               for line in lines)


def test_linear_model_has_no_drift():
    series = quadratic_series()
    fit = fit_phase(series, order=1)
    s = suggested_settings(fit, "MHM2010", float(series.mjd[-1]), 1)
    assert s["drift_setting"] is None and "Linear phase model" in s["notes"][0]
    assert any("not estimated" in line for line in suggestion_box_lines(s))


def test_t4_matches_t4_settings():
    series = quadratic_series(label="Site1Hz -> T4", kind="FITS")
    fit = fit_phase(series, order=2, maser="T4")
    epoch = float(series.mjd[-1])
    s = suggested_settings(fit, "T4", epoch, 1, t4_synth_hz=405750.927598720, t4_driftcomp=1.5e-15)
    t = t4_settings(fit, epoch, 1, 405750.927598720, 1.5e-15)
    assert s["frequency_setting"]["setfsrel"] == t["setfsrel"]
    assert s["frequency_setting"]["setfsrel_command"] == t["setfsrel_command"]
    assert s["drift_setting"]["new_driftcomp"] == t["drift"]["new_driftcomp"]
    assert s["drift_setting"]["driftcomp_change_per_day"] == -t["drift"]["measured_residual_drift_per_day"]
    lines = suggestion_box_lines(s)
    assert any("SETFSREL=" in line for line in lines) and any("DRIFTCOMP" in line for line in lines)


# %% Names, text and precision helpers ------------------------------------------
def test_names_and_full_text():
    assert vname("Franks", "MSM-GPS", "phase_ns") == "Franks_MSM_GPS_phase_ns"
    assert vname("FITS", "Site1Hz -> CNS_GPS1") == "FITS_Site1Hz_to_CNS_GPS1"
    assert vname("T4log", "Set temperature", "°C") == "T4log_Set_temperature_degC"
    assert vname("x", "pp18/day") == "x_pp18_per_day"
    values = [0.1, 1 / 3, 5751.6774093, 61349.866122685185, -2.3820000000000002e-06, 1e-99]
    assert [float(text) for text in full_strings(values)] == values
    assert full(np.float64(5751.6774093)) == "5751.6774093" and full(np.int64(-527)) == "-527"
    doc = FullPrecisionDoc()
    doc.add("axis", "x")
    assert f"Set('x/TickLabels/format', '{NUMERIC_TICK_FORMAT}')" in doc.text()


def test_write_csv_round_trip(tmp_path):
    rng = np.random.default_rng(7)
    data = {"a": rng.normal(size=1000) * 1e-9, "b": 61000 + rng.random(1000), "c": rng.normal(size=1000) * 1e6}
    target = write_csv(tmp_path / "x.csv", data)
    back = pd.read_csv(target)
    for key, values in data.items():
        assert np.array_equal(back[key].to_numpy(), values)            # bit-exact
    with pytest.raises(ValueError):
        write_csv(tmp_path / "y.csv", data, float_format="%.6g")


def test_stable32_exact(tmp_path):
    mjd = 61000 + np.arange(200) / 86400.0
    phase = np.random.default_rng(2).normal(size=200) * 1e-9
    write_stable32(tmp_path, mjd, phase)
    lines = (tmp_path / "raw.PHD").read_text().splitlines()
    assert np.array_equal(np.array([float(x) for x in lines[4:]]), np.where(phase == 0, 1e-99, phase))
    back = pd.read_csv(tmp_path / "raw_timetags.csv")
    assert np.array_equal(back.mjd_utc.to_numpy(), mjd) and np.array_equal(back.phase_s.to_numpy(), phase)


def test_headroom_limits():
    low, high = headroom_limits(np.array([0.0, 10.0]))
    assert low == pytest.approx(-0.5) and high == pytest.approx(14.5)


# %% Exports: names, tags, suggestion datasets, HMD columns -------------------------
@pytest.fixture(scope="module")
def hmd_export(tmp_path_factory):
    telemetry = load_hmd([HMD])
    mjd = np.arange(60963.0, 60993.0, 1 / 24)
    phase = -2.0e-9 * (mjd - 60963) - 3.0e-11 * (mjd - 60963) ** 2
    series = TimingSeries(mjd, phase, "MSM-GPS", "Franks")
    fit = fit_phase(series)
    suggestion = suggested_settings(fit, "MHM2010", float(mjd[-1]), 1, dds="5751.6774093", current_drift=-13.25,
                                    drift_units="DDS counts/day")
    ref = reference_set(mjd, phase, fit.selected, fit.used, "MHM2010", "MSM-GPS", True, 5751.6774093, 1,
                        suggestion=suggestion, source_kind="Franks")
    target = export_bundle(tmp_path_factory.mktemp("hmd") / "run", series, fit, None, telemetry, None, {}, False,
                           .01, None, "MHM2010", ref)
    return target, suggestion, telemetry


def test_export_names_tags_and_ticks(hmd_export):
    target, suggestion, telemetry = hmd_export
    with h5py.File(target / "analysis.vszh5", "r") as handle:
        names = [text(handle["Veusz/Data"][key]["data"].attrs["vsz_name"]) for key in handle["Veusz/Data"]]
        tags = {key: [text(v) for v in handle["Veusz/Document/Tags"][key][()]]
                for key in handle["Veusz/Document/Tags"]}
        script = text(handle["Veusz/Document/document"][0])
        data = {name: handle["Veusz/Data"][name]["data"][()] for name in (
            "Suggested_MHM2010_dds_change_counts", "Suggested_MHM2010_drift_change_dds_counts_per_day",
            "Suggested_MHM2010_drift_change_pp18_per_day", "HMD_ExtSynth_32", "HMD_ExtSynth_32_change_counts")}
        dds_text = [text(v) for v in handle["Veusz/Data/HMD_ExtSynth_32_text/data"][()]]
    assert all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) for name in names)
    tagged = set().union(*tags.values())
    assert set(names) <= tagged                                           # every dataset carries a tag
    assert all(tag.startswith(("source_", "type_")) for tag in tags)
    assert {"source_Franks", "source_HMD", "source_Workbench", "type_suggested_settings",
            "type_telemetry"} <= set(tags)
    for name in names:                                                   # one source tag per dataset
        assert sum(name in members for tag, members in tags.items() if tag.startswith("source_")) == 1
    assert data["Suggested_MHM2010_dds_change_counts"][0] == suggestion["frequency_setting"]["dds_change_counts"]
    assert (data["Suggested_MHM2010_drift_change_pp18_per_day"][0] ==
            suggestion["drift_setting"]["drift_change_pp18_per_day"])
    # every HMD column, exactly as read; DDS as exact text and as counts
    columns = [c for c in telemetry.frame.columns if c != "MJD"]
    assert all(f"HMD_{c}" in names for c in columns)
    frame = telemetry.frame
    window = frame[(frame.MJD >= 60963.0) & (frame.MJD <= 60993.0 - 1 / 24)]
    assert np.array_equal(data["HMD_ExtSynth_32"], window.ExtSynth_32.to_numpy(float))
    assert [float(v) for v in dds_text] == window.ExtSynth_32.tolist()
    assert np.allclose(data["HMD_ExtSynth_32_change_counts"],
                       (window.ExtSynth_32.to_numpy() - window.ExtSynth_32.iloc[0]) / 1e-7, rtol=0, atol=1e-6)
    # the box and its headroom, the trace names, 12-digit numeric ticks
    assert "Add('label', name='box_suggested_settings'" in script and "DDS change = " in script
    assert "drift change = " in script
    assert "Add('xy', name='Ref1PPS_MSM_GPS_fit_data_ns'" in script
    assert script.count(f"TickLabels/format', '{NUMERIC_TICK_FORMAT}'") >= 10
    assert "trace0" not in script and "'graph0'" not in script


def test_export_text_is_exact(hmd_export):
    target, suggestion, telemetry = hmd_export
    review = (target / "review.md").read_text(encoding="utf-8")
    assert repr(suggestion["frequency_setting"]["dds_change_counts"]) in review
    assert repr(suggestion["drift_setting"]["drift_change_dds_counts_per_day"]) in review
    payload = json.loads((target / "analysis.json").read_text(encoding="utf-8"))
    assert payload["suggested_settings"]["drift_setting"] == suggestion["drift_setting"]
    back = pd.read_csv(target / "telemetry_overlap.csv")
    window = telemetry.frame[(telemetry.frame.MJD >= 60963.0) & (telemetry.frame.MJD <= 60993.0 - 1 / 24)]
    for column in window.columns:
        assert np.array_equal(back[column].to_numpy(float), window[column].to_numpy(float))
    fit_csv = pd.read_csv(target / "phase_and_fit.csv")
    assert np.array_equal(fit_csv.mjd_utc.to_numpy(), np.arange(60963.0, 60993.0, 1 / 24))


def test_t4_log_export_exact(tmp_path):
    from t4_native import load_native_logs
    logs = load_native_logs([MONIT])
    target = export_t4_only(tmp_path / "t4", None, logs)
    back = pd.read_csv(target / "t4_MonitHCB_records.csv", encoding="utf-8-sig")
    assert np.array_equal(back.mjd_utc.to_numpy(), logs[0].mjd)
    summary = json.loads((target / "t4_telemetry.json").read_text(encoding="utf-8"))
    names = summary["veusz_datasets"]["names"]
    assert "T4log_MonitHCB_Internal_temperature_degC" in names
    assert "source_T4_log_MonitHCB" in summary["veusz_datasets"]["tags"]
    with h5py.File(target / "t4_telemetry.vszh5", "r") as handle:
        notes = text(handle["Veusz/Document/document"][0])
    assert logs[0].provenance[0]["sha256"] in notes                       # full hash, not 16 characters


# %% GUI: blank fields still give both suggestions --------------------------------
def test_gui_both_suggestions_with_blank_fields(qapp_offscreen):
    from MHM2010_Correction_App import MainWindow
    from maser_io import load_timing
    window = MainWindow()
    try:
        window.accept_timing(load_timing(FRANKS))
        window.channel.setCurrentText("MSM-GPS")
        window.set_window(30)
        assert window.dds.text() == "" and window.drift.text() == ""
        text = window.results.toPlainText()
        assert "SUGGESTED CHANGES" in text and "DDS change = " in text and "drift change = " in text
        assert window.suggestion["drift_setting"]["drift_change_pp18_per_day"] == pytest.approx(686.48, abs=0.01)
        box = window.reference_widget.suggestion_box
        assert box.isVisible() or box.toPlainText()                          # drawn on the fit figure
        assert "drift change" in box.toPlainText()
        y_low, y_high = window.reference_widget.fit_plot.vb.viewRange()[1]
        assert y_high > float(np.max(window.reference.fit.y_ns)) + 0.3 * np.ptp(window.reference.fit.y_ns)
        window.dds.setText("5751.6770000")
        window.drift.setText("-120")
        text = window.results.toPlainText()
        assert "-> DDS 5751.6769473" in text and "new drift field +566.48" in text
        window.order.setCurrentIndex(0)
        window.refit() if hasattr(window, "refit") else window.queue_fit()
        for _ in range(50):
            qapp_offscreen.processEvents()
            if window.fit is not None and window.fit.order == 1:
                break
        if window.fit is not None and window.fit.order == 1:
            assert "not estimated" in window.results.toPlainText()
    finally:
        window.close()


@pytest.fixture(scope="module")
def qapp_offscreen():
    from qtpy import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# %% Batch ------------------------------------------------------------------------
def test_batch_suggestions_without_settings(tmp_path):
    folder = tmp_path / "franks"
    folder.mkdir()
    for path in FRANKS:
        (folder / path.name).write_bytes(path.read_bytes())
    run = subprocess.run([sys.executable, str(ROOT / "analyze_maser.py"), "--timing-directory", str(folder),
                          "--channel", "MSM-GPS", "--bridge-gaps", "--last-days", "30",
                          "--output", str(tmp_path / "out")], capture_output=True, text=True,
                         env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
    assert run.returncode == 0, run.stderr
    printed = json.loads(run.stdout[run.stdout.index("{"):])
    suggestion = printed["suggested_settings"]
    assert suggestion["drift_setting"]["drift_change_pp18_per_day"] == pytest.approx(686.48, abs=0.01)
    assert suggestion["frequency_setting"]["dds_change_counts"] == pytest.approx(-526.79, abs=0.01)
    assert printed["settings_candidate"] is None                         # absolute values need --dds/--drift
    # 0.2.4: exact epoch in the JSON, exact local ISO text in the Veusz file
    assert printed["application_epoch_mjd_utc"] == suggestion["application_epoch_mjd_utc"]
    assert len(printed["application_epoch_utc"].split(".")[1]) == 9
    from tests.test_release_024 import mjd_from_iso
    from vszh5_writer import read_text_dataset
    vszh5 = tmp_path / "out" / "analysis.vszh5"
    with h5py.File(vszh5, "r") as handle:
        stems = [text(handle["Veusz/Data"][k]["data"].attrs["vsz_name"])[:-len("_time_local_ISO_text")]
                 for k in handle["Veusz/Data"] if k.endswith("_time_local_ISO_text")]
        assert stems
        for stem in stems:
            mjd = handle["Veusz/Data"][stem + "_time_MJD_UTC"]["data"][()]
            iso = read_text_dataset(vszh5, stem + "_time_local_ISO_text")
            back = np.array([np.nan if not t else mjd_from_iso(t) for t in iso])
            assert np.array_equal(back, mjd, equal_nan=True), stem
            # Franks MJD has 5 decimals (0.864 s): at most 3 decimals of a second
            assert all(len(t[:-6].partition(".")[2]) <= 3 for t in iso if t), stem
