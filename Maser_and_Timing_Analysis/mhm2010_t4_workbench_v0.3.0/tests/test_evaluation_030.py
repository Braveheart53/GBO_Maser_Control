#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Release 0.3.0: further-evaluation science, Veusz page choices, GUI tabs, batch.

# %% Header Info
 Title    : Workbench 0.3.0 evaluation tests
 Filename : tests/test_evaluation_030.py
 Version  : 0.0.1
 Author   : William W. Wallace (NRAO/GBO)
 Created  : 2026-11-11

# %% Revisions
# %%%% 0.0.1: First version.
#              * maser_eval: gap-free deviations = maser_core.stability() and
#                allantools; gapped OADEV = brute-force loop; lag-1 noise ID,
#                simple EDF and chi-squared limits = allantools; grids never
#                change a value; steering removal exact; Kalman recovers a
#                simulated clock; three-cornered hat recovers three clocks;
#              * export: the DEFAULT content writes the 0.2.4 Veusz document
#                (fingerprint tests/data/export_fingerprint_0.2.4.json, made
#                with the 0.2.4 code); unticked groups leave their pages out;
#                evaluation pages, CSVs, JSON and review.md; NaN -> null;
#              * GUI: four tabs, checkboxes, base suggestion unchanged by any
#                evaluation, settings round trip, stale-fit fix;
#              * batch: evaluation flags and their errors.

Run:  python -m pytest tests/test_evaluation_030.py
"""
# %% Imports
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import maser_eval as ev                                          # noqa: E402
from maser_core import fit_phase, stability, suggested_settings  # noqa: E402
from maser_io import TimingSeries, TimingCollection, Telemetry, load_franks  # noqa: E402
from maser_export import ExportContent, export_bundle            # noqa: E402
from vszh5_writer import page_names, dataset_names               # noqa: E402

allantools = pytest.importorskip("allantools")
EXAMPLE = ROOT / "examples" / "historical_20251015_20251113"
FRANKS = sorted((ROOT / "examples" / "franks_2026_01_07").glob("*.t00new"))


# %% Fixtures
def white_fm_record(n=3000, tau0=270.0, seed=1, gaps=False):
    """Phase (s) of white FM plus white PM on a 270 s grid; optional gaps."""
    rng = np.random.default_rng(seed)
    mjd = 61000.0 + np.arange(n) * tau0 / 86400.0
    phase = np.cumsum(rng.normal(0, 1e-11, n)) * tau0 + rng.normal(0, 2e-9, n)
    if gaps:
        keep = np.ones(n, bool)
        for start in rng.integers(0, n - 40, 25):
            keep[start:start + rng.integers(1, 30)] = False
        return mjd[keep], phase[keep], mjd, phase
    return mjd, phase


@pytest.fixture(scope="module")
def historical():
    """The shipped Oct-Nov 2025 month: FITS MicroSem -> CNS_GPS1 with the HMD."""
    table = pd.read_csv(EXAMPLE / "phase_and_fit.csv")
    hmd = pd.read_csv(EXAMPLE / "telemetry_overlap.csv")
    series = TimingSeries(table.mjd_utc.to_numpy(), table.phase_s.to_numpy(), "MicroSem -> CNS_GPS1", "FITS")
    used = table.used_in_fit.to_numpy(bool)
    low, high = float(table.mjd_utc[used].min()), float(table.mjd_utc[used].max())
    fit = fit_phase(series, low, high, order=2, maser="MHM2010", bridge_gaps=False)
    return {"series": series, "hmd": hmd, "fit": fit, "low": low, "high": high,
            "epoch": float(series.mjd[fit.used][-1])}


# %% 1. Gap-aware stability
def test_gap_free_equals_base_and_allantools():
    mjd, phase = white_fm_record()
    base = stability(mjd, phase, 2, 0.01)
    result = ev.gap_stability(mjd, phase, 2, tau0_s=base["tau0_s"])
    for name in ev.ESTIMATORS:
        mine = np.asarray(result["estimators"][name]["deviation"])
        theirs = np.asarray(base["estimators"][name]["deviation"])
        assert np.allclose(mine, theirs, rtol=1e-12, atol=0)
    detrended = ev.detrend_grid(ev.regular_grid(mjd, phase, base["tau0_s"]), mjd, 2)
    rate = 1.0 / base["tau0_s"]
    for name, function in (("OADEV", allantools.oadev), ("MDEV", allantools.mdev),
                           ("TDEV", allantools.tdev), ("OHDEV", allantools.ohdev)):
        values = result["estimators"][name]
        taus = np.asarray(values["tau_s"])
        _, reference, _, _ = function(detrended, rate=rate, data_type="phase", taus=taus)
        assert np.allclose(values["deviation"], reference, rtol=1e-9), name


def test_gapped_oadev_equals_brute_force_and_values_untouched():
    mjd, phase, full_mjd, full_phase = white_fm_record(gaps=True)
    grid = ev.regular_grid(mjd, phase, 270.0)
    assert grid.n_placed == mjd.size and grid.n_slots == full_mjd.size
    placed = grid.sample_index >= 0
    assert np.array_equal(grid.phase_s[placed], phase[grid.sample_index[placed]])     # never interpolated
    assert np.isnan(grid.phase_s[~placed]).all()
    result = ev.gap_stability(mjd, phase, -1, tau0_s=270.0)
    x = ev.detrend_grid(grid, mjd, -1)
    for tau, value, terms in zip(*(result["estimators"]["OADEV"][k] for k in ("tau_s", "deviation", "terms"))):
        m = int(round(tau / 270.0))
        sums, count = 0.0, 0
        for i in range(x.size - 2 * m):
            term = x[i + 2 * m] - 2 * x[i + m] + x[i]
            if math.isfinite(term):
                sums += term * term
                count += 1
        assert count == terms
        assert math.isclose(value, math.sqrt(sums / count / (2 * tau * tau)), rel_tol=1e-12)


def test_noise_id_edf_and_interval_match_allantools():
    rng = np.random.default_rng(7)
    for kind in ("wpm", "wfm", "rwfm"):
        n = 4000
        if kind == "wpm":
            x = rng.normal(0, 1, n)
        elif kind == "wfm":
            x = np.cumsum(rng.normal(0, 1, n))
        else:
            x = np.cumsum(np.cumsum(rng.normal(0, 1, n)))
        for m in (1, 4, 16):
            mine = ev.noise_id_lag1(x, m)
            theirs = allantools.autocorr_noise_id(x, m, data_type="phase", dmin=0, dmax=2)
            assert mine[0] == theirs[0] and mine[2] == theirs[2]
    for alpha in (2, 1, 0, -1, -2):
        for n, m in ((1000, 1), (1000, 10), (5000, 100)):
            mine, theirs = ev.edf_simple_oadev(n, m, alpha), allantools.edf_simple(n, m, alpha)
            assert mine is not None and math.isclose(mine, theirs, rel_tol=1e-12)
            low, high = ev.chi2_interval(1e-13, mine)
            ref_low, ref_high = allantools.confidence_interval(1e-13, mine)
            assert math.isclose(low, ref_low, rel_tol=1e-12) and math.isclose(high, ref_high, rel_tol=1e-12)


def test_sequential_grid_for_jittered_franks():
    collection = load_franks(FRANKS)
    series = collection.series["MSM-GPS"]
    grid = ev.regular_grid(series.mjd, series.phase_s)
    assert grid.mode == "sequential"                       # the 58.5-min sampling is not a fixed clock
    placed = grid.sample_index >= 0
    assert np.array_equal(grid.phase_s[placed], series.phase_s[grid.sample_index[placed]])
    result = ev.gap_stability(series.mjd, series.phase_s, 2)
    oadev = result["estimators"]["OADEV"]
    assert oadev["tau_s"] and all(lo <= v <= hi for v, lo, hi in zip(oadev["deviation"], oadev["ci_low"],
                                                                       oadev["ci_high"]))


# %% 2. Steering removal
def test_steering_removal_recovers_unsteered_phase():
    n, tau0 = 6000, 60.0
    mjd = 61000.0 + np.arange(n) * tau0 / 86400.0
    dds = np.where(np.arange(n) < 3000, 5751.6772768, 5751.6772268)          # one -500-count step
    y = ev.dds_fractional_change(dds[0], dds)
    seconds = np.arange(n) * tau0
    steered = np.r_[0.0, np.cumsum(y[:-1] * tau0)]
    own = 1e-9 * np.sin(seconds / 2e4)
    for sign in (1, -1):
        measured = sign * (own + steered)                   # measured channel = sign x (maser - reference)
        hmd = pd.DataFrame({"MJD": mjd, "ExtSynth_32": dds})
        result = ev.steering_removal(mjd, measured, hmd, sign, "all")
        assert result.covered.all()
        # 1e-18 s: MJD time stamps resolve 0.63 us, times y ~ 1.7e-14 is ~1e-20 s
        assert np.max(np.abs(result.removed_phase_s - sign * own)) < 1e-18
        steps = ev.steering_removal(mjd, measured, hmd, sign, "steps", threshold_counts=50)
        assert len(steps.events) == 1 and math.isclose(steps.events[0]["change_counts"], -500.0, abs_tol=1e-3)
    outside = ev.steering_removal(np.r_[mjd, mjd[-1] + 1], np.r_[measured, 0.0], hmd, -1)
    assert not outside.covered[-1] and np.isnan(outside.removed_phase_s[-1])


def test_steering_on_the_historical_month(historical):
    h = historical
    result = ev.run_steering(h["series"], h["fit"], h["hmd"], 1, h["low"], h["high"], h["epoch"])
    summary = result["steering"].summary()
    assert summary["covered_samples"] > 9000 and summary["dds_column"] == "ExtSynth_32"
    base, free = result["comparison"]["base"], result["comparison"]["steering_removed"]
    assert base["samples"] == int(h["fit"].used.sum())
    assert free["fractional_frequency"] != base["fractional_frequency"]
    with pytest.raises(ValueError, match="does not overlap"):
        collection = load_franks(FRANKS)
        franks = collection.series["MSM-GPS"]
        ffit = fit_phase(franks, franks.mjd[-500], franks.mjd[-1], bridge_gaps=True)
        ev.run_steering(franks, ffit, h["hmd"], 1, franks.mjd[-500], franks.mjd[-1], franks.mjd[-1])


# %% 3. Kalman estimate
def test_kalman_recovers_a_simulated_clock_and_never_touches_the_base():
    rng = np.random.default_rng(3)
    n, tau0 = 4000, 270.0
    t = np.arange(n) * tau0
    y0, drift = 1.2e-13, 1e-15 / 86400.0                    # fractional frequency, drift per second
    frequency = y0 + drift * t + np.cumsum(rng.normal(0, 3e-16, n))
    phase = np.r_[0.0, np.cumsum(frequency[:-1] * tau0)] + rng.normal(0, 2e-9, n)
    mjd = 61000.0 + t / 86400.0
    keep = np.ones(n, bool)
    keep[1500:1700] = False                                 # a gap: carried by prediction
    series = TimingSeries(mjd[keep], phase[keep], "MicroSem -> CNS_GPS1", "FITS")
    fit = fit_phase(series, mjd[0], mjd[-1], order=2, maser="MHM2010", bridge_gaps=True)
    before = json.dumps(suggested_settings(fit, "MHM2010", mjd[-1], 1), sort_keys=True, default=str)
    result = ev.run_kalman(series.mjd, series.phase_s, fit, series.label, float(mjd[-1]), 1, "MHM2010", True, {})
    estimate = result["estimate"]
    assert abs(estimate["fractional_frequency"] - frequency[-1]) < 4 * estimate["fractional_frequency_sigma"]
    assert 0.5 < result["kalman"].normalized_innovation_squared < 2.0
    assert result["settings"]["notes"][0].startswith("EVALUATION ONLY")
    after = json.dumps(suggested_settings(fit, "MHM2010", mjd[-1], 1), sort_keys=True, default=str)
    assert before == after


# %% 4. Three-cornered hat
def test_three_cornered_hat_recovers_three_clocks():
    rng = np.random.default_rng(1)
    n, tau0 = 20000, 100.0
    sigma = {"A": 1e-12, "B": 2e-12, "C": 4e-12}
    clocks = {k: np.cumsum(rng.normal(0, v, n)) * tau0 for k, v in sigma.items()}
    t = 61000.0 + np.arange(n) * tau0 / 86400.0
    three = ev.three_cornered_hat([(t, clocks["A"] - clocks["B"]), (t, clocks["B"] - clocks["C"]),
                                   (t, clocks["C"] - clocks["A"])], mode="three", detrend_order=-1)
    common = ev.three_cornered_hat([(t, clocks["A"] - clocks["C"]), (t, clocks["B"] - clocks["C"])],
                                   mode="common", detrend_order=-1)
    for name, level in sigma.items():
        assert abs(three["clocks"][name]["adev"][0] / level - 1) < 0.1
        assert abs(common["clocks"][name]["adev"][0] / level - 1) < 0.1
    collection = load_franks(FRANKS)
    with pytest.raises(ValueError):
        ev.run_three_cornered_hat(collection.series, ["MSM-GPS", "MSM-GPS"], mode="common")


# %% 5. Export: default = 0.2.4; page choices; evaluation output
def test_default_export_is_the_0_2_4_document(tmp_path):
    import export_fingerprint
    os.environ["VSZH5_DISPLAY_TZ"] = "America/New_York"
    expected = json.loads((ROOT / "tests" / "data" / "export_fingerprint_0.2.4.json").read_text())
    vszh5 = export_fingerprint.default_export(ROOT, tmp_path / "default")
    assert export_fingerprint.fingerprint(vszh5) == expected


def _export(tmp_path, historical, name, content, evaluation=None):
    h = historical
    suggestion = suggested_settings(h["fit"], "MHM2010", h["epoch"], 1)
    export_bundle(tmp_path / name, h["series"], h["fit"], None, Telemetry(h["hmd"], [], []), None, {}, False,
                  0.02, TimingCollection({h["series"].label: h["series"]}, []), "MHM2010", None, None, (), (),
                  suggestion, content, evaluation)
    return tmp_path / name, suggestion


def test_unticked_groups_leave_their_pages_out(tmp_path, historical):
    full, _ = _export(tmp_path, historical, "full", ExportContent())
    lean, _ = _export(tmp_path, historical, "lean", ExportContent(hmd_telemetry=False, timing_pages=False))
    full_pages, lean_pages = page_names(full / "analysis.vszh5"), page_names(lean / "analysis.vszh5")
    assert any(p.startswith("HMD_") for p in full_pages) and not any(p.startswith("HMD_") for p in lean_pages)
    assert not any("phase_and_fit" in p for p in lean_pages)
    assert (lean / "telemetry_overlap.csv").is_file()                  # data files stay complete
    review = (lean / "review.md").read_text(encoding="utf-8")
    assert "- [ ] HMD telemetry plots (MHM2010)" in review


def test_evaluation_output_never_changes_the_suggestion(tmp_path, historical):
    h = historical
    collection = load_franks(FRANKS)
    evaluation = {
        "gap_stability": ev.run_gap_stability(h["series"].mjd[h["fit"].used], h["series"].phase_s[h["fit"].used],
                                              h["series"].label),
        "steering_removed": ev.run_steering(h["series"], h["fit"], h["hmd"], 1, h["low"], h["high"], h["epoch"]),
        "kalman": ev.run_kalman(h["series"].mjd[h["fit"].used], h["series"].phase_s[h["fit"].used], h["fit"],
                                h["series"].label, h["epoch"], 1, "MHM2010", True, {}),
        "three_cornered_hat": ev.run_three_cornered_hat(collection.series, ["MSM-GPS", "CNS_GPS"],
                                                        ("MSM", "CNS", "GPS"), "common"),
    }
    content = ExportContent(**{n: True for n in ExportContent.names()})
    folder, suggestion = _export(tmp_path, historical, "eval", content, evaluation)
    base, _ = _export(tmp_path, historical, "base", ExportContent())
    payload = json.loads((folder / "analysis.json").read_text())            # strict JSON (NaN -> null)
    assert set(payload["evaluation"]) == set(ExportContent.EVALUATION)
    assert payload["suggested_settings"] == json.loads((base / "analysis.json").read_text())["suggested_settings"]
    pages = page_names(folder / "analysis.vszh5")
    for prefix in ("GapStab_", "Steer_", "Kalman_", "TCH_"):
        assert any(p.startswith(prefix) for p in pages), prefix
    names = dataset_names(folder / "analysis.vszh5")
    assert "GapStab_OADEV_deviation" in names and "TCH_tau_s" in names
    for csv in ("evaluation_gap_stability.csv", "evaluation_steering_removed.csv", "evaluation_kalman.csv",
                "evaluation_three_cornered_hat.csv"):
        assert (folder / csv).is_file()
    assert "## Further evaluation (not the base suggestion)" in (folder / "review.md").read_text(encoding="utf-8")
    table = pd.read_csv(folder / "evaluation_gap_stability.csv", dtype=str)
    for text in table.deviation:                                        # exact numbers
        assert repr(float(text)) == text


# %% 6. GUI
@pytest.fixture
def window():
    from MHM2010_Correction_App import MainWindow, QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    table = pd.read_csv(EXAMPLE / "phase_and_fit.csv")
    series = TimingSeries(table.mjd_utc.to_numpy(), table.phase_s.to_numpy(), "MicroSem -> CNS_GPS1", "FITS")
    window.accept_timing(TimingCollection({series.label: series}, []))
    window.accept_hmd(Telemetry(pd.read_csv(EXAMPLE / "telemetry_overlap.csv"), [], []))
    used = table.used_in_fit.to_numpy(bool)
    window.low.setValue(float(table.mjd_utc[used].min()))
    window.high.setValue(float(table.mjd_utc[used].max()))
    window.refit()
    yield window
    window.close()
    del app


def test_gui_tabs_checkboxes_and_unchanged_suggestion(window):
    titles = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    for label in ("Eval: gap-aware stability", "Eval: steering removal", "Eval: Kalman estimate",
                  "Eval: three-cornered hat"):
        assert label in titles
    assert titles.index("Stability") < titles.index("Eval: gap-aware stability") < titles.index("Audit && guidance")
    assert window.export_content() == ExportContent()                    # defaults = the 0.2.4 file
    before = json.dumps(window.suggestion, sort_keys=True, default=str)
    results_text = window.results.toPlainText()
    for tab in (window.evaluation.steering, window.evaluation.gap, window.evaluation.kalman):
        assert tab.calculate(), tab.result_error
    assert not window.evaluation.tch.calculate()                          # one channel: reported, not raised
    assert json.dumps(window.suggestion, sort_keys=True, default=str) == before
    assert window.results.toPlainText() == results_text
    window.evaluation.gap.source.setCurrentIndex(1)                     # steering-removed samples
    assert window.evaluation.gap.calculate(), window.evaluation.gap.result_error
    assert window.evaluation.gap.result["source"] == "steering removed"
    window.refit()
    assert window.evaluation.kalman.result is None                       # a new fit invalidates


def test_gui_export_selection_and_settings(window):
    window.set_export_content(ExportContent(hmd_telemetry=False, kalman=True, three_cornered_hat=True))
    content = window.export_content()
    results, skipped = window.evaluation_for_export(content)
    assert "kalman" in results and "three_cornered_hat" in skipped
    settings = window.settings()
    assert settings["export_content"]["hmd_telemetry"] is False and "evaluation_options" in settings
    window.set_export_content(ExportContent())
    window.load_settings(settings)
    assert window.export_content() == content
    assert window.settings()["evaluation_options"] == settings["evaluation_options"]


def test_gui_data_change_drops_the_old_fit(window):
    collection = load_franks(FRANKS)
    window.accept_timing(collection)                                     # 0.2.4: IndexError traceback
    assert window.fit is None or window.fit.used.size == window.series.mjd.size
    window.refresh_candidate()
    window.refit()
    assert window.fit is not None and window.fit.used.size == window.series.mjd.size


# %% 7. Batch command
def test_batch_evaluation_flags(tmp_path):
    folder = tmp_path / "franks"
    folder.mkdir()
    for path in FRANKS:
        (folder / path.name).write_bytes(path.read_bytes())
    command = [sys.executable, str(ROOT / "analyze_maser.py"), "--timing-directory", str(folder), "--channel",
               "MSM-GPS", "--bridge-gaps", "--last-days", "30", "--gap-stability", "--kalman", "--tch", "MSM-GPS",
               "CNS_GPS", "--tch-mode", "common", "--no-hmd-plots", "--output", str(tmp_path / "out")]
    run = subprocess.run(command, capture_output=True, text=True, cwd=ROOT, timeout=600,
                         env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    assert run.returncode == 0, run.stderr[-2000:]
    printed = json.loads(run.stdout[run.stdout.index("{"):])
    assert printed["evaluation_files"] == ["evaluation_gap_stability.csv", "evaluation_kalman.csv",
                                           "evaluation_three_cornered_hat.csv"]
    payload = json.loads((tmp_path / "out" / "analysis.json").read_text())
    assert payload["export_content"]["hmd_telemetry"] is False
    assert set(payload["evaluation"]) == {"gap_stability", "kalman", "three_cornered_hat"}
    failure = subprocess.run(command[:-2] + ["--remove-steering", "--output", str(tmp_path / "out2")],
                             capture_output=True, text=True, cwd=ROOT, timeout=600,
                             env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    assert failure.returncode != 0 and "--hmd" in failure.stderr
