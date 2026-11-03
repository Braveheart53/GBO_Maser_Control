#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""MHM2010 phase fitting, synthesizer mapping and stability estimators.

===============================================================================
 Title          : MHM2010 Scientific Analysis Core
 Filename       : maser_core.py
 Version        : 0.1.0 (Semantic Versioning 2.0.0)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-03
 Modified       : 2026-11-03
 Language       : Python 3.12
 Requires       : numpy, scipy, astropy
 Licence        : GPL-3.0-or-later; see LICENSE
 Status         : Offline advisory calculations, not an instrument controller
 Revision       : 0.1.0 Initial scaled phase fit and four stability estimators.
===============================================================================
References:
[1] Microsemi, MHM-2010 User's Guide, 75666-201, rev. J, Oct. 2014.
    https://www.gb.nrao.edu/~fghigo/timer/MSMmaser/Maser_UG_75666-201_Rev_J.PDF
[2] W. J. Riley, Handbook of Frequency Stability Analysis, NIST SP 1065,
    Jul. 2008. https://tf.nist.gov/general/pdf/2220.pdf
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
import numpy as np
from astropy.time import Time
from maser_io import TimingSeries, Telemetry

__version__ = "0.1.0"
SECONDS_PER_DAY = 86400.0
DDS_FACTOR = 2e-7
DDS_BASE = 284.08
DDS_QUANTUM = Decimal("0.0000001")


# %% ===========================================================================
#    CONTINUITY AND FIT SELECTION
# =============================================================================
def elapsed_seconds(mjd, origin_mjd):
    """UTC MJD to elapsed SI seconds through TAI, including leap seconds."""
    return (Time(mjd, format="mjd", scale="utc").tai -
            Time(origin_mjd, format="mjd", scale="utc").tai).to_value("s")


def segments(mjd, gap_factor=5.0):
    """Indices for acquisition runs separated by long gaps, never joined."""
    times = np.asarray(mjd)
    if len(times) < 2:
        return [np.arange(len(times))]
    delta = np.diff(times)
    if not np.all(delta > 0):
        raise ValueError("Timestamps must increase strictly.")
    cuts = np.flatnonzero(delta > gap_factor * np.median(delta)) + 1
    return [indices for indices in np.split(np.arange(len(times)), cuts) if len(indices)]


@dataclass
class PhaseFit:
    epoch_mjd: float
    order: int
    coefficients_ns: np.ndarray
    covariance_ns: np.ndarray
    selected: np.ndarray
    used: np.ndarray
    residual_s: np.ndarray
    predicted_s: np.ndarray
    rms_ns: float
    condition: float
    warnings: list[str]

    def evaluate(self, mjd):
        days = elapsed_seconds(mjd, self.epoch_mjd) / SECONDS_PER_DAY
        return np.polynomial.polynomial.polyval(days, self.coefficients_ns) * 1e-9

    def frequency(self, apply_mjd):
        days = float(elapsed_seconds(apply_mjd, self.epoch_mjd)) / SECONDS_PER_DAY
        coeff = np.pad(self.coefficients_ns, (0, 3-len(self.coefficients_ns)))
        fractional = (coeff[1] + 2 * coeff[2] * days) * 1e-9 / SECONDS_PER_DAY
        drift_per_day = 2 * coeff[2] * 1e-9 / SECONDS_PER_DAY
        gradient = np.array([0., 1., 2*days])[:len(self.coefficients_ns)]
        frequency_sigma = np.sqrt(max(0., gradient @ self.covariance_ns @ gradient)) * 1e-9 / SECONDS_PER_DAY
        drift_sigma = (2*np.sqrt(max(0., self.covariance_ns[2, 2])) * 1e-9 / SECONDS_PER_DAY
                       if self.order == 2 else None)
        return float(fractional), float(drift_per_day), float(frequency_sigma), drift_sigma


def fit_phase(series: TimingSeries, low_mjd=None, high_mjd=None, order=2,
              excluded=(), clip_sigma=0.0) -> PhaseFit:
    """OLS in normalized elapsed days, optionally iterative MAD clipping.

    Individual excluded points are retained in the full input series. A gap
    is never bridged by a fit: select one run before calling. Covariance is
    conditional IID-OLS scatter, not an Allan-aware metrological uncertainty.
    """
    if order not in (1, 2):
        raise ValueError("Phase polynomial order must be 1 or 2.")
    low = series.mjd[0] if low_mjd is None else float(low_mjd)
    high = series.mjd[-1] if high_mjd is None else float(high_mjd)
    selected = (series.mjd >= low) & (series.mjd <= high)
    selected &= np.isfinite(series.phase_s)
    window_times = series.mjd[selected]
    if len(window_times) < order + 3:
        raise ValueError("Insufficient selected samples.")
    if len(segments(window_times)) != 1:
        raise ValueError("Selection spans an acquisition gap. Select one continuous run.")
    used = selected.copy()
    for start, stop in excluded:
        used &= ~((series.mjd >= start) & (series.mjd <= stop))
    if used.sum() < order + 3:
        raise ValueError("Too few observations after exclusions.")
    epoch = float(np.mean(series.mjd[used]))
    days = elapsed_seconds(series.mjd, epoch) / SECONDS_PER_DAY
    scale = float(np.max(np.abs(days[used])))
    if not np.isfinite(scale) or scale == 0:
        raise ValueError("Zero fitting time span.")
    design = np.vander(days / scale, order+1, increasing=True)
    values_ns = series.phase_s * 1e9
    for _ in range(8):
        coefficients, _, rank, _ = np.linalg.lstsq(design[used], values_ns[used], rcond=None)
        if rank != order+1:
            raise ValueError("Rank-deficient fit.")
        residual = values_ns - design @ coefficients
        if clip_sigma <= 0:
            break
        center = np.median(residual[used])
        mad = 1.4826*np.median(np.abs(residual[used]-center))
        if mad <= np.finfo(float).eps:
            break
        revised = used & (np.abs(residual-center) <= clip_sigma*mad)
        if revised.sum() < order+3:
            raise ValueError("Clipping leaves too few samples.")
        if np.array_equal(revised, used):
            break
        used = revised
    # Refit after the final clipping update; never return stale coefficients.
    coefficients = np.linalg.lstsq(design[used], values_ns[used], rcond=None)[0]
    residual = values_ns - design @ coefficients
    variance = np.sum(residual[used]**2)/(used.sum()-order-1)
    transform = np.diag(scale**(-np.arange(order+1)))
    covariance = transform @ (variance*np.linalg.inv(design[used].T @ design[used])) @ transform
    physical_coefficients = transform @ coefficients
    warnings = ["Fit uncertainty is conditional IID-OLS only; GPS, colored noise and systematics are not included.",
                "Constant phase offset is not corrected by the DDS/drift fields."]
    duration_days = float(np.ptp(days[used]))
    if duration_days < 7:
        warnings.append("Less than 7 days selected: short-arc GPS curvature is not a reliable maser aging estimate. "
                        "Seven days is an application warning threshold, not a manufacturer requirement.")
    if series.source_kind == "Franks":
        warnings.append("Reduced hourly medians: no raw-sample information or exact reduction windows are recoverable.")
    if not any("MicroSem" in series.label and gps in series.label
               for gps in ("CNS_GPS1", "CNS_GPS2", "CNS2018")) and series.label != "MSM-GPS":
        warnings.append("Selected channel is not an identified MicroSem/GPS comparison.")
    return PhaseFit(epoch, order, physical_coefficients, covariance, selected, used,
                    residual*1e-9, design @ coefficients * 1e-9,
                    float(np.sqrt(np.mean(residual[used]**2))),
                    float(np.linalg.cond(design[used])), warnings)


# %% ===========================================================================
#    DDS AND DRIFT FIELD MAPPING
# =============================================================================
def correction(fit: PhaseFit, current_dds: str, current_drift: float,
               apply_mjd: float, drift_units="pp18/day", positive_drift="output",
               active_drift=True, sign_multiplier=1) -> dict:
    """Map residual clock error onto NEW absolute settings at one epoch.

    positive_drift='output': positive GUI pp18/day increases output y.
    positive_drift='dds': positive GUI pp18/day increases DDS instead.
    The operator must verify which convention the installed masermon uses.
    DDS counts/day always means physical increasing DDS least-significant
    digits/day. An inactive existing drift contributes zero.
    """
    if positive_drift not in {"output", "dds"} or sign_multiplier not in {-1, 1}:
        raise ValueError("Invalid sign convention.")
    if drift_units not in {"pp18/day", "DDS counts/day"}:
        raise ValueError("Unknown drift units.")
    setting = Decimal(current_dds.strip())
    if not setting.is_finite() or not Decimal("0") < setting < Decimal("10000"):
        raise ValueError("DDS must be a finite nnnn.nnnnnnn value.")
    if setting != setting.quantize(DDS_QUANTUM):
        raise ValueError("DDS must have at most seven decimal places.")
    if not np.isfinite(current_drift) or not np.isfinite(apply_mjd) or apply_mjd <= 0:
        raise ValueError("Drift and application epoch must be finite.")
    error, drift, error_sigma, drift_sigma = fit.frequency(apply_mjd)
    error *= sign_multiplier
    drift *= sign_multiplier
    denominator = Decimal("284.08") + Decimal("0.0000002")*setting
    gain = float(Decimal("0.0000002")/denominator)
    # Exact static cancellation: f_new/f_old = 1/(1+y_measured).
    change = denominator / Decimal("0.0000002") * Decimal(str(error))
    candidate = (setting + change).quantize(DDS_QUANTUM, rounding=ROUND_HALF_EVEN)
    if not Decimal("0") < candidate < Decimal("10000"):
        raise ValueError("Candidate DDS is outside the displayed nnnn.nnnnnnn format.")
    effective_old = float(current_drift) if active_drift else 0.0
    convention = 1 if positive_drift == "output" else -1
    old_counts_per_day = (effective_old if drift_units == "DDS counts/day"
                         else -convention*effective_old*1e-18/(gain*1e-7))
    new_counts_per_day = (1+error)*old_counts_per_day + drift/(gain*1e-7)
    new_gain = float(Decimal("0.0000002") /
                     (Decimal("284.08")+Decimal("0.0000002")*candidate))
    old_pp18 = -convention*old_counts_per_day*gain*1e-7*1e18
    new_pp18 = -convention*new_counts_per_day*new_gain*1e-7*1e18
    if drift_units == "DDS counts/day":
        # dN_new/dd = (1+y)*dN_old/dd + (D/a)*dy/dd.
        delta_drift = effective_old*error + drift/(gain*1e-7)
        new_drift = effective_old + delta_drift
    else:
        delta_drift = -convention*drift/(1+error)*1e18
        new_drift = effective_old + delta_drift
    # Evaluate sub-epsilon residual in Decimal, not by subtracting float 1.
    residual_decimal = ((Decimal(1)+Decimal(str(error)))*denominator /
                        (Decimal("284.08")+Decimal("0.0000002")*candidate)-Decimal(1))
    return {
        "application_epoch_mjd_utc": apply_mjd,
        "residual_fractional_frequency": error,
        "residual_frequency_drift_per_day": drift if fit.order == 2 else None,
        "conditional_frequency_sigma": error_sigma,
        "conditional_drift_sigma_per_day": drift_sigma,
        "current_external_dds": str(setting),
        "delta_dds_counts_unrounded": float(change/Decimal("0.0000001")),
        "new_external_dds": f"{candidate:.7f}",
        "quantization_residual_fractional_frequency": float(residual_decimal),
        "current_drift_field": current_drift,
        "existing_drift_enabled": active_drift,
        "drift_units": drift_units,
        "positive_pp18_convention": positive_drift,
        "delta_drift_field": delta_drift if fit.order == 2 else None,
        "new_drift_field": new_drift if fit.order == 2 else None,
        "new_drift_field_rounded_integer": int(np.rint(new_drift)) if fit.order == 2 else None,
        "both_drift_units": ({
            "DDS_units_per_day": {"current": old_counts_per_day,
                                 "increment": new_counts_per_day-old_counts_per_day,
                                 "new": new_counts_per_day,
                                 "new_nearest_integer": int(np.rint(new_counts_per_day))},
            "parts_in_1e18_per_day": {"current": old_pp18,
                                     "increment": new_pp18-old_pp18,
                                     "new": new_pp18,
                                     "new_nearest_integer": int(np.rint(new_pp18))},
            "fractional_drift_residual_if_integer_DDS_per_day":
                -(np.rint(new_counts_per_day)-new_counts_per_day)*new_gain*1e-7,
            "fractional_drift_residual_if_integer_pp18_per_day":
                convention*(np.rint(new_pp18)-new_pp18)*1e-18,
            "manufacturer_drift_range": "Not specified in Rev. J; verify installed software limits."
        } if fit.order == 2 else None),
        "fractional_frequency_per_dds_count": -gain*1e-7,
        "phase_sign_multiplier": sign_multiplier,
        "status": "ADVISORY ONLY: validate current settings, epoch, sign, interval and maser state before manual entry."
    }


def telemetry_events(telemetry: Telemetry, threshold_counts=50.0):
    """Return DDS/selector events for display; alarm voltages stay undecoded."""
    frame = telemetry.frame
    event = np.zeros(len(frame), dtype=bool)
    for column in ("Thumbwheels_31", "ExtSynth_32"):
        if column in frame:
            event[1:] |= np.abs(np.diff(frame[column]))*1e7 > threshold_counts
    for column in ("IntExtSwitch_33", "IntExtSoft_34"):
        if column in frame:
            event[1:] |= np.diff(frame[column]) != 0
    return frame.loc[event, ["MJD", "ExtSynth_32"]].copy()


# %% ===========================================================================
#    UNIFORM-SAMPLING VALIDATION AND STABILITY (NIST SP 1065)
# =============================================================================
def uniform_samples(mjd, phase_s, tolerance=0.01):
    """Validate a complete uniform record. No interpolation or gap filling."""
    timestamps = np.asarray(mjd, float)
    phase = np.asarray(phase_s, float)
    if (timestamps.ndim != 1 or phase.ndim != 1 or timestamps.shape != phase.shape
            or len(timestamps) < 8 or not np.all(np.isfinite(timestamps))):
        raise ValueError("Timestamp and phase arrays must be matching finite one-dimensional records with >=8 samples.")
    times = elapsed_seconds(timestamps, float(timestamps[0]))
    if len(times) < 8 or not np.all(np.isfinite(phase)):
        raise ValueError("At least eight finite phase samples are required.")
    intervals = np.diff(times)
    tau0 = float(np.mean(intervals))
    if tolerance <= 0 or tolerance > 0.05:
        raise ValueError("Sampling tolerance must be >0 and <=5%; it is not a gap-filling control.")
    if tau0 <= 0 or np.max(np.abs(intervals/tau0-1)) > tolerance:
        raise ValueError("Nonuniform/gapped samples: select a contiguous FITS interval. "
                         "No interpolation is used. Franks hourly medians are usually irregular.")
    # Local interval checks alone can hide cumulative timing distortion.
    grid_error = np.max(np.abs(times-np.arange(len(times))*tau0))
    if grid_error > tolerance*tau0:
        raise ValueError("Timestamps drift off a uniform grid beyond the allowed tolerance.")
    return phase, tau0, float(grid_error)


def stability(mjd, phase_s, detrend_order=-1, tolerance=0.01) -> dict:
    """OADEV, MDEV, TDEV, OHDEV on phase seconds. No confidence intervals.

    Use log-spaced integer averaging factors, requiring >=10 terms per
    estimate. Overlapping term counts are not independent degrees of freedom.
    """
    phase, tau0, jitter = uniform_samples(mjd, phase_s, tolerance)
    if detrend_order not in (-1, 0, 1, 2):
        raise ValueError("Detrend order must be -1 (raw), 0, 1, or 2.")
    phase = phase.copy()
    if detrend_order >= 0:
        axis = np.linspace(-1, 1, len(phase))
        fit = np.polynomial.polynomial.polyfit(axis, phase, detrend_order)
        phase -= np.polynomial.polynomial.polyval(axis, fit)
    else:
        phase -= phase[0]  # constant subtraction is stability-invariant
    factors = np.unique(np.geomspace(1, max(1, (len(phase)-10)//3), 35).astype(int))
    output = {"tau0_s": tau0, "grid_error_s": jitter, "sampling_tolerance": tolerance,
              "detrend_order": detrend_order,
              "sample_count": len(phase), "estimators": {}}
    stores = {key: {"tau_s": [], "deviation": [], "terms": []}
              for key in ("OADEV", "MDEV", "TDEV", "OHDEV")}
    for averaging_factor in factors:
        m = int(averaging_factor)
        tau = m*tau0
        second = phase[2*m:] - 2*phase[m:-m] + phase[:-2*m]
        if len(second) >= 10:
            value = np.sqrt(np.mean(second**2)/(2*tau**2))
            for key, item in (("OADEV", value),):
                stores[key]["tau_s"].append(tau)
                stores[key]["deviation"].append(float(item))
                stores[key]["terms"].append(len(second))
        if len(second) >= m:
            cumulative = np.r_[0., np.cumsum(second)]
            moving = cumulative[m:] - cumulative[:-m]
            if len(moving) >= 10:
                modified = np.sqrt(np.mean(moving**2)/(2*m*m*tau*tau))
                for key, value in (("MDEV", modified), ("TDEV", tau*modified/np.sqrt(3))):
                    stores[key]["tau_s"].append(tau)
                    stores[key]["deviation"].append(float(value))
                    stores[key]["terms"].append(len(moving))
        third = (phase[3*m:] - 3*phase[2*m:-m] +
                 3*phase[m:-2*m] - phase[:-3*m])
        if len(third) >= 10:
            stores["OHDEV"]["tau_s"].append(tau)
            stores["OHDEV"]["deviation"].append(float(np.sqrt(np.mean(third**2)/(6*tau*tau))))
            stores["OHDEV"]["terms"].append(len(third))
    output["estimators"] = stores
    return output


# %% ===========================================================================
#    WINDOW SENSITIVITY: A DIAGNOSTIC, NOT AN UNCERTAINTY ESTIMATE
# =============================================================================
def window_sensitivity(series, low_mjd, high_mjd, excluded=()):
    """Refit trailing 7/14/21/30-day windows ending at the selected endpoint."""
    output = []
    for days in (30, 21, 14, 7):
        start = max(low_mjd, high_mjd-days)
        trial = fit_phase(series, start, high_mjd, order=2, excluded=excluded)
        frequency, drift, _, _ = trial.frequency(high_mjd)
        output.append({"requested_days": days, "actual_span_days":
                       float(np.ptp(series.mjd[trial.used])), "samples": int(trial.used.sum()),
                       "fractional_frequency_at_end": frequency,
                       "drift_pp18_per_day": drift*1e18, "rms_ns": trial.rms_ns})
    return output
