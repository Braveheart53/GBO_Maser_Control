#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# %% ===========================================================================
# Title: Reference figures, Franks intake, time axes and export tests | Version: 0.0.1
# Author: William W. Wallace | Created/Modified: 2026-11-03 | Python: 3.12
# Revision: 0.0.1 OLS box values, synth lines, real 2026 Franks files,
#           month-boundary repeats, gap bridging, writer 0.0.24 MJD/local
#           ISO axes, export page layout and T4 pages.
# =============================================================================
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pytest
from maser_io import TimingSeries, TimingCollection, load_timing, load_franks, overview_series
from maser_core import fit_phase, correction
from maser_plots import (reference_set, reference_linear_fit, delta_stats, fit_box_lines, raw_box_lines,
                         residual_box_lines, mhm2010_synth_counts, synth_text)
from maser_export import export_bundle
from vszh5_writer import (__version__ as WRITER, mjd_to_epoch, mjd_to_veusz, iso_offset_text, local_time_axis,
                          iso_local_strings, VszDoc, Panel, Trace, grid_page, page_names, dataset_names)

ROOT = Path(__file__).resolve().parents[1]
FRANKS = sorted((ROOT / "examples" / "franks_2026_01_07").glob("*.t00new"))


# %% REFERENCE FIGURE NUMBERS
def test_ols_values_and_box_text():
    rng = np.random.default_rng(3)
    x = np.arange(741) / 24.0
    y = -357.49 - 2.719 * x + rng.normal(0, 3.3, x.size)
    fit = reference_linear_fit(61316.64903 + x, y)
    coefficients, covariance = np.polyfit(x, y, 1, cov="unscaled")
    residual = y - np.polyval(coefficients, x)
    s2 = np.sum(residual ** 2) / (x.size - 2)
    assert fit.a1 == pytest.approx(coefficients[0]) and fit.a0 == pytest.approx(coefficients[1])
    assert fit.sigma_a1 == pytest.approx(np.sqrt(s2 * covariance[0, 0]))
    assert fit.sigma_a0 == pytest.approx(np.sqrt(s2 * covariance[1, 1]))
    assert fit.rate == pytest.approx(fit.a1 * 1e-9 / 86400)
    assert fit.std_ns == pytest.approx(np.std(y)) and fit.n == 741
    lines = fit_box_lines(fit, synth_text("MHM2010", fit.rate, True))
    assert lines[0] == "num points = 741" and lines[3] == "results of fit to y = a1*x + a0:"
    assert lines[-1].startswith("synth adjustment = ") and lines[-1].endswith(" counts")
    assert residual_box_lines(fit)[0].startswith("mean = ")


def test_mhm_synth_counts_match_full_dds_correction():
    days = np.linspace(-15, 15, 721)
    series = TimingSeries(61330 + days, (-2.719 * days) * 1e-9, "MSM-GPS", "Franks")
    fit = fit_phase(series, order=1, bridge_gaps=True)
    full = correction(fit, "5751.6770000", 0, 61330)["delta_dds_counts_unrounded"]
    assert mhm2010_synth_counts(-2.719e-9 / 86400, 5751.677) == pytest.approx(full, rel=1e-9)
    assert round(mhm2010_synth_counts(-3.147e-14)) == -447       # operator's figure: rate -3.15e-14


def test_t4_synth_line():
    text = synth_text("T4", -3.147e-14, True)
    assert text.startswith("synth adjustment = SETFSREL=+") and "µHz" in text
    assert "n/a" in synth_text("T4", -3.147e-14, False)


def test_reference_set_excluded_and_titles():
    days = np.linspace(0, 30, 400)
    series = TimingSeries(61316 + days, (5 - 2 * days) * 1e-9, "MSM-GPS", "Franks")
    fit = fit_phase(series, order=1, excluded=[(61320.0, 61321.0)], bridge_gaps=True)
    ref = reference_set(series.mjd, series.phase_s, fit.selected, fit.used, "MHM2010", "MSM-GPS", True,
                        titles={"ylabel": "MSM-GPS vs Stime Time (ns)"})
    assert ref.title == "MicroSemi Maser 1PPS Deltas" and ref.ylabel == "MSM-GPS vs Stime Time (ns)"
    assert ref.raw.n == 400 and ref.fit.n == 400 - ref.excluded.n > 0


# %% FRANKS: REAL 2026 FILES, MONTH BOUNDARIES, MIXED INTAKE, GAPS
@pytest.mark.skipif(len(FRANKS) < 7, reason="example Franks files not present")
def test_real_franks_july_2026_regression():
    collection = load_franks(FRANKS)
    assert any("61222.01917" in note and "kept 2026_07.t00new" in note for note in collection.notes)
    series = collection.series["MSM-GPS"]
    with pytest.raises(ValueError, match="gap"):
        fit_phase(series, series.mjd[-1] - 30.9, series.mjd[-1], order=1)
    fit = fit_phase(series, series.mjd[-1] - 30.9, series.mjd[-1], order=1, bridge_gaps=True)
    assert any("bridges 1 acquisition gap" in warning for warning in fit.warnings)
    ref = reference_set(series.mjd, series.phase_s, fit.selected, fit.used, "MHM2010", "MSM-GPS", True,
                        5751.677)
    assert ref.fit.n == 644
    assert ref.fit.a1 == pytest.approx(-2.268, abs=0.0005) and ref.fit.sigma_a1 == pytest.approx(0.019, abs=0.0005)
    assert ref.synth_line == "synth adjustment = -373 counts"


def test_month_boundary_repeats_and_mixed_intake(tmp_path):
    header = ("TITLE: GB Timing Data: Site1Hz vs: (nanosecs) (medians over 60.0 mins)\n"
              "    MJD         date        time     Site1Hz  MSM-GPS\n")
    rows_june = [" 61221.95%03d  06-30-2026  23:00:00  1  -2600" % i for i in range(5)]
    june = tmp_path / "2026_06.t00new"
    july = tmp_path / "2026_07.t00new"
    june.write_text(header + "\n".join(rows_june + [" 61222.01917  07-01-2026  00:27:37  0  -2621"]) + "\n")
    july.write_text(header + "\n".join([" 61222.01917  07-01-2026  00:27:37  1  -2621"] +
                                       [" 61222.0%d000  07-01-2026  01:00:00  1  -2622" % i for i in range(2, 7)]) + "\n")
    collection = load_timing([june, july])
    site = collection.series["Site1Hz"]
    assert site.phase_s[np.argmin(abs(site.mjd - 61222.01917))] == pytest.approx(1e-9)
    assert any("disagree" in note for note in collection.notes)


def test_fits_and_franks_kept_separate():
    franks = TimingSeries(np.arange(10.) + 61000, np.zeros(10), "MSM-GPS", "Franks")
    fits = TimingSeries(np.arange(10.) + 61000, np.zeros(10), "MicroSem -> CNS_GPS1", "FITS")
    view = overview_series(TimingCollection({"MSM-GPS": franks, "MicroSem -> CNS_GPS1": fits}, []))
    assert "Franks: MSM-GPS" in view and "MicroSem vs CNS_GPS1 (direct)" in view


# %% WRITER 0.0.24 TIME AXES
def test_writer_time_helpers(monkeypatch):
    assert WRITER == "0.0.24"
    assert mjd_to_epoch(40587.0) == 0.0 and mjd_to_veusz(54832.0) == pytest.approx(0.0)
    assert iso_offset_text(-18000) == "-05:00" and iso_offset_text(19800) == "+05:30"
    axis = local_time_axis(mjd_to_epoch(np.linspace(61347.0, 61348.0, 10)), zone="America/New_York")
    assert axis.datetime_format == "%VDY-%VDm-%VDdT%VDH:%VDM:%VDS-05:00"
    assert axis.label == "Local time (ISO 8601, UTC-05:00)"
    across = local_time_axis(mjd_to_epoch(np.linspace(61345.0, 61348.0, 300)), zone="America/New_York")
    assert across.datetime_format.endswith("%VDS") and "UTC-04:00" in across.label and "UTC-05:00" in across.label
    assert iso_local_strings(mjd_to_epoch([61347.5]), zone="America/New_York") == ["2026-11-03T07:00:00-05:00"]


def test_grid_panel_datetime_format():
    doc = VszDoc()
    grid_page(doc, "p", [Panel([Trace("t", "y")], xmode="datetime", datetime_format="%VDY")])
    assert "Set('x/TickLabels/format', '%VDY')" in doc.text()


# %% EXPORT LAYOUT
def test_export_reference_first_and_time_pairs(tmp_path):
    days = np.linspace(0, 20, 480)
    series = TimingSeries(61320 + days, (-357 - 2.7 * days) * 1e-9, "MSM-GPS", "Franks")
    fit = fit_phase(series, order=1, bridge_gaps=True)
    ref = reference_set(series.mjd, series.phase_s, fit.selected, fit.used, "MHM2010", "MSM-GPS", True)
    out = export_bundle(tmp_path / "run", series, fit, reference=ref,
                        collection=TimingCollection({"MSM-GPS": series}, []))
    pages = page_names(out / "analysis.vszh5")
    assert pages[:4] == ["pps_deltas", "pps_fit", "pps_deltas_local", "pps_fit_local"]
    for name in ("timing_fit_mjd", "timing_fit_local", "fit_residual_mjd", "fit_residual_local"):
        assert name in pages
    assert "local_time_iso" in dataset_names(out / "analysis.vszh5")
    payload = json.loads((out / "analysis.json").read_text())
    assert payload["reference_plots"]["fit"]["num_points"] == 480
