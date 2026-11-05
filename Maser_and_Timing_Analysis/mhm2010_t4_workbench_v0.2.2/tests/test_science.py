#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# %% ===========================================================================
# Title: MHM2010 numerical regression tests | Version: 0.1.0
# Author: William W. Wallace | Created/Modified: 2026-11-03 | Python: 3.12
# Revision: 0.1.0 Analytic signals, independent allantools comparison, safeguards.
# =============================================================================
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pytest
import h5py
import allantools
from astropy.io import fits
from maser_io import TimingSeries, clean_series, load_fits, load_franks
from maser_core import fit_phase, correction, stability, uniform_samples
from maser_export import write_stable32, export_bundle
from vszh5_writer import VeuszWriter


# %% ANALYTIC FITS AND CORRECTIONS
def synthetic(count=2001):
    days = np.linspace(-15, 15, count)
    phase = (1000+2*days+0.03*days**2)*1e-9
    return TimingSeries(61000+days, phase, "MicroSem -> CNS_GPS1", "FITS")


def test_quadratic_recovery():
    fit = fit_phase(synthetic())
    np.testing.assert_allclose(fit.coefficients_ns, [1000, 2, .03], atol=1e-9)
    y, drift, _, _ = fit.frequency(61000)
    assert y == pytest.approx(2e-9/86400, rel=1e-9)
    assert drift == pytest.approx(.06e-9/86400, rel=1e-9)


def test_linear_does_not_claim_drift():
    fit = fit_phase(synthetic(), order=1)
    output = correction(fit, "5751.6775490", 975, 61000)
    assert output["new_drift_field"] is None
    assert output["both_drift_units"] is None


@pytest.mark.parametrize("convention", ["output", "dds"])
def test_drift_units_equivalent(convention):
    fit = fit_phase(synthetic())
    first = correction(fit, "5751.6775490", 975, 61000, positive_drift=convention)
    count_old = first["both_drift_units"]["DDS_units_per_day"]["current"]
    second = correction(fit, "5751.6775490", count_old, 61000,
                        drift_units="DDS counts/day", positive_drift=convention)
    assert first["new_external_dds"] == second["new_external_dds"]
    for name in ["DDS_units_per_day", "parts_in_1e18_per_day"]:
        assert first["both_drift_units"][name]["new"] == pytest.approx(
            second["both_drift_units"][name]["new"], rel=1e-12)


def test_dds_direction_and_quantization():
    output = correction(fit_phase(synthetic()), "5751.6775490", 0, 61000)
    assert float(output["new_external_dds"]) > 5751.6775490
    assert output["fractional_frequency_per_dds_count"] == pytest.approx(-7.04024e-17, rel=1e-5)
    assert abs(output["quantization_residual_fractional_frequency"]) <= 3.521e-17


def test_disabled_drift_ignored():
    fit = fit_phase(synthetic())
    first = correction(fit, "5751.6775490", 999, 61000, active_drift=False)
    second = correction(fit, "5751.6775490", 0, 61000, active_drift=True)
    assert first["new_drift_field"] == second["new_drift_field"]


def test_sign_reversal():
    fit = fit_phase(synthetic())
    first = correction(fit, "5751.6775490", 0, 61000, sign_multiplier=1)
    second = correction(fit, "5751.6775490", 0, 61000, sign_multiplier=-1)
    assert first["residual_fractional_frequency"] == -second["residual_fractional_frequency"]


def test_gap_rejected():
    series = synthetic()
    series.mjd[1000:] += 2
    with pytest.raises(ValueError, match="gap"):
        fit_phase(series)


def test_conflict_rejected():
    with pytest.raises(ValueError, match="conflicting"):
        clean_series([60000, 60001, 60001, 60002, 60003], [0, 1, 2, 3, 4], "test", "FITS")


def test_duplicate_removed():
    series = clean_series([60000, 60001, 60001, 60002, 60003], [0, 1, 1, 3, 4], "test", "FITS")
    assert len(series.mjd) == 4


def test_clipping_removes_outlier():
    series = synthetic()
    series.phase_s[400] += 1e-5
    fit = fit_phase(series, clip_sigma=5)
    assert not fit.used[400]
    np.testing.assert_allclose(fit.coefficients_ns, [1000, 2, .03], atol=.05)


# %% INDEPENDENT NIST-ESTIMATOR CHECKS
@pytest.mark.parametrize("name,function", [
    ("OADEV", allantools.oadev), ("MDEV", allantools.mdev),
    ("TDEV", allantools.tdev), ("OHDEV", allantools.ohdev)])
def test_against_allantools(name, function):
    random = np.random.default_rng(53)
    phase = np.cumsum(random.normal(0, 1e-10, 4096))
    mjd = 61000 + np.arange(len(phase))*30/86400
    output = stability(mjd, phase)
    values = output["estimators"][name]
    tau, deviation, error, count = function(phase, rate=1/output["tau0_s"],
                                           data_type="phase", taus=values["tau_s"])
    np.testing.assert_allclose(values["deviation"], deviation, rtol=2e-10, atol=1e-25)
    np.testing.assert_array_equal(values["terms"], count)


def test_irregular_not_resampled():
    mjd = 61000 + np.arange(200)*30/86400
    mjd[100] += 10/86400
    with pytest.raises(ValueError, match="Nonuniform"):
        uniform_samples(mjd, np.arange(200))


def test_grid_drift_rejected():
    intervals = np.r_[np.full(100, 30.2), np.full(100, 29.8)]
    mjd = 61000 + np.r_[0, np.cumsum(intervals)]/86400
    with pytest.raises(ValueError, match="grid"):
        uniform_samples(mjd, np.arange(201))


def test_zero_preserved_stable32(tmp_path):
    mjd = 61000 + np.arange(100)*30/86400
    values = np.zeros(100)
    meta = write_stable32(tmp_path, mjd, values)
    assert meta["PHD_zero_replacements"] == 100
    phase = np.loadtxt(tmp_path/"raw.PHD", skiprows=4)
    assert np.all(phase == 1e-99)
    assert len(np.loadtxt(tmp_path/"raw.FRD", skiprows=4)) == 99


# %% WRITER REGRESSION AND EXPORT ATOMICITY
@pytest.mark.parametrize("resizable", [False, True])
def test_writer_rejects_2d_date(tmp_path, resizable):
    destination = tmp_path/"invalid.vszh5"
    with pytest.raises(ValueError, match="must be 1-D"):
        with VeuszWriter(destination, resizable=resizable) as writer:
            writer.dataset_date("date", np.ones((2, 3)))
            writer.finish("")
    assert not destination.exists()


def test_writer_valid_date(tmp_path):
    destination = tmp_path/"valid.vszh5"
    with VeuszWriter(destination) as writer:
        writer.dataset_date("date", [0., 1.])
        writer.finish("")
    with h5py.File(destination) as handle:
        assert handle["Veusz/Data/date/data"].shape == (2,)


def test_existing_export_untouched(tmp_path):
    series = synthetic()
    with pytest.raises(FileExistsError):
        export_bundle(tmp_path, series, fit_phase(series))


def test_export_schema(tmp_path):
    series = synthetic()
    destination = export_bundle(tmp_path/"run", series, fit_phase(series))
    assert (destination/"analysis.json").exists()
    with h5py.File(destination/"analysis.vszh5") as handle:
        assert "phase_ns" in handle["Veusz/Data"]


def test_invalid_dds():
    for value in ["nan", "-2", "5751.12345678", "10001"]:
        with pytest.raises(ValueError):
            correction(fit_phase(synthetic()), value, 0, 61000)


# %% FITS AND FRANKS ADAPTER CONTRACTS
def make_fits(path, simulate=0, units="nanoseconds", timesys="UTC"):
    primary = fits.PrimaryHDU()
    primary.header["TIMESYS"] = timesys
    primary.header["SIMULATE"] = simulate
    table = fits.BinTableHDU.from_columns([
        fits.Column(name="DMJD", format="D", unit="d",
                    array=np.r_[61000+np.arange(8)/320, 0.]),
        fits.Column(name="CHANNELA", format="16A", array=["MicroSem"]*8+[""]),
        fits.Column(name="CHANNELB", format="16A", array=["CNS_GPS1"]*8+[""]),
        fits.Column(name="DELTAT", format="J", unit=units, array=[-9999, 0, 1, 2, 3, 4, 5, 6, 0])
    ], name="OnePpsDeltas")
    fits.HDUList([primary, table]).writeto(path)


def test_fits_padding_and_valid_negative(tmp_path):
    path = tmp_path/"input.fits.gz"
    make_fits(path)
    result = load_fits([path]).series["MicroSem -> CNS_GPS1"]
    assert len(result.mjd) == 8
    assert result.phase_s[0] == pytest.approx(-9999e-9)
    assert result.phase_s[1] == 0


@pytest.mark.parametrize("kwargs", [{"simulate": 1}, {"units": "unknown"}, {"timesys": "TAI"}])
def test_fits_rejects_ambiguous_metadata(tmp_path, kwargs):
    path = tmp_path/"input.fits"
    make_fits(path, **kwargs)
    with pytest.raises(ValueError):
        load_fits([path])


def test_franks_sentinels_and_column_names(tmp_path):
    path = tmp_path/"input.t00new"
    path.write_text("TITLE: GB Timing Data (nanosecs)\n"
                    "MJD date time MicroSem MSM-GPS\n" +
                    "\n".join(f"{61000+i/24} 01-01-2026 00:00:00 6 {value}"
                              for i, value in enumerate([-9999, -999, -2600, -2601, -2602, -2603])),
                    encoding="utf-8")
    collection = load_franks([path])
    assert len(collection.series["MSM-GPS"].mjd) == 4
    assert len(collection.series["MicroSem"].mjd) == 6
