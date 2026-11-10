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
 Version        : 0.2.3 (Semantic Versioning 2.0.0)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-03
 Modified       : 2026-11-10
 Language       : Python 3.12
 Requires       : numpy, scipy, astropy
 Licence        : GPL-3.0-or-later; see LICENSE
 Status         : Offline advisory calculations, not an instrument controller
 Revision       : 0.1.0 Initial scaled phase fit and four stability estimators.
                  0.2.0 Maser identity (MHM2010 or T4) in fit warnings;
                        t4_settings(): SETFSREL/DRIFTCOMP suggestions from
                        the T4 manuals (Eq. 12, 39-bit synthesizer step,
                        documented ranges); stability factors run in
                        parallel threads.
                  0.2.1 DRIFTCOMP note corrected: REBOOT "Reboot[s] the M&C
                        software" (T4S-MAN-0015 Table 1, p. 8; Sec. 1.1.3,
                        p. 13); no calculation changed.
                  0.2.2 window_sensitivity(days=...): the GUI compares 7 to
                        90-day windows when the data span them.
                  0.2.3 suggested_settings(): BOTH suggestions at the
                        application epoch - MHM2010 DDS count change and
                        drift change (pp18/day and DDS counts/day), T4
                        SETFSREL and DRIFTCOMP - without needing the current
                        drift field (the new absolute values when the current
                        ones are given); full float64, nothing rounded.
===============================================================================
References:
[1] Microsemi, MHM-2010 User's Guide, 75666-201, rev. J, Oct. 2014.
    https://www.gb.nrao.edu/~fghigo/timer/MSMmaser/Maser_UG_75666-201_Rev_J.PDF
[2] W. J. Riley, Handbook of Frequency Stability Analysis, NIST SP 1065,
    Jul. 2008. https://tf.nist.gov/general/pdf/2220.pdf
[3] T4Science, iMaser 3000 Installation, Operation and Maintenance User
    Manual, T4S-MAN-0012, issue 1.27, May 2, 2022, Secs. 9.4, 9.6, 9.12, 9.13.
[4] T4Science, T4S-Monitoring network Software User Manual, T4S-MAN-0015,
    ver. 1.17, Jul. 19, 2022, Secs. 1.1.1-1.1.3.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
from decimal import Decimal, ROUND_HALF_EVEN
import numpy as np
from astropy.time import Time
from maser_io import TimingSeries, Telemetry

__version__ = "0.2.3"
MASER_KINDS = ("MHM2010", "T4")
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
              excluded=(), clip_sigma=0.0, maser="MHM2010", bridge_gaps=False) -> PhaseFit:
    """OLS in normalized elapsed days, optionally iterative MAD clipping.

    Individual excluded points are retained in the full input series. A gap
    is never bridged by a fit: select one run before calling. Covariance is
    conditional IID-OLS scatter, not an Allan-aware metrological uncertainty.
    ``maser`` (0.2.0) only selects the channel-identity warning.
    ``bridge_gaps`` (0.2.0) allows one fit across acquisition gaps, as the
    operator's existing monthly Franks fits do; every bridged gap is
    reported in the warnings (no phase-step correction is applied).
    """
    if maser not in MASER_KINDS:
        raise ValueError(f"maser must be one of {MASER_KINDS}")
    if order not in (1, 2):
        raise ValueError("Phase polynomial order must be 1 or 2.")
    low = series.mjd[0] if low_mjd is None else float(low_mjd)
    high = series.mjd[-1] if high_mjd is None else float(high_mjd)
    selected = (series.mjd >= low) & (series.mjd <= high)
    selected &= np.isfinite(series.phase_s)
    window_times = series.mjd[selected]
    if len(window_times) < order + 3:
        raise ValueError("Insufficient selected samples.")
    runs = segments(window_times)
    bridged = []
    if len(runs) != 1:
        if not bridge_gaps:
            raise ValueError("Selection spans an acquisition gap. Select one continuous run, "
                             "or allow fitting across gaps.")
        bridged = [(float(window_times[run[0]-1]), float(window_times[run[0]])) for run in runs[1:]]
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
    if bridged:
        longest = max(high - low for low, high in bridged)*24
        warnings.append(f"Fit bridges {len(bridged)} acquisition gap(s), longest {longest:.1f} h; "
                        "check the plot for a phase step at each gap.")
    duration_days = float(np.ptp(days[used]))
    if duration_days < 7:
        warnings.append("Less than 7 days selected: short-arc GPS curvature is not a reliable maser aging estimate. "
                        "Seven days is an application warning threshold, not a manufacturer requirement.")
    if series.source_kind == "Franks":
        warnings.append("Reduced hourly medians: no raw-sample information or exact reduction windows are recoverable.")
    if maser == "T4":
        warnings.append("T4 channel identity is operator-designated: no FITS name is known to identify "
                        "the T4. Verify the channel names and measured sign before use.")
    elif not any("MicroSem" in series.label and gps in series.label
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


# T4 synthesizer constants [T4S-MAN-0012 1.27, Sec. 9.6 Eqs. 10-12 and the
# synthesizer description in Sec. 9.4]: y0 = -d(nu_synth) / (284 x 5 MHz).
T4_SYNTH_SCALE_HZ = 284 * 5e6                     # 1.42e9 Hz
T4_SYNTH_STEP_HZ = 5e6 / 2**39                    # 39-bit resolution, 9.0949e-6 Hz
T4_SYNTH_RANGE_HZ = (405708.0, 405795.0)          # Sec. 9.13 "Main Frequency Synthesiser"
T4_SETFSREL_LIMIT = 29e-9                         # T4S-MAN-0015 command 6 range
T4_DRIFTCOMP_RANGE = (1e-17, 8e-13)               # T4S-MAN-0015 command 11


def t4_settings(fit: PhaseFit, apply_mjd: float, sign_multiplier=1, current_synth_hz=None,
                current_driftcomp=None, setfsrel_convention="output", driftcomp_enabled=True) -> dict:
    """Suggested T4 SETFSREL and DRIFTCOMP entries. NEVER sent by this program.

    y is the measured fractional frequency of the T4 relative to the
    reference at ``apply_mjd`` (sign_multiplier converts the channel to
    T4-minus-reference). The output must change by dy = -y. From Eq. 12 the
    synthesizer must move by d(nu) = -dy * 1.42e9 Hz, quantized to the 39-bit
    step 5 MHz / 2**39 = 9.0949e-6 Hz (6.405e-15 relative).

    setfsrel_convention:
        "output": SETFSREL=+x is taken to raise the OUTPUT frequency by x.
        "phase":  SETFSREL=+x is taken as the measured phase drift [s/s] to
                  remove, i.e. it lowers the output by x.
        T4S-MAN-0015 does not settle this; verify with RDFS before/after
        (the expected synthesizer change in Hz is reported).
    DRIFTCOMP: "A positive sign will generate a positive slope compensation"
    (T4S-MAN-0015 Sec. 1.1.3), so new = current - measured residual drift.
    It needs a REBOOT to load (Sec. 1.1.3, p. 13); REBOOT restarts the M&C
    software (Table 1, p. 8).
    """
    if sign_multiplier not in (-1, 1) or setfsrel_convention not in ("output", "phase"):
        raise ValueError("Invalid sign convention.")
    error, drift, error_sigma, drift_sigma = fit.frequency(apply_mjd)
    y = sign_multiplier*error
    required = -y
    synth_change = -required*T4_SYNTH_SCALE_HZ
    steps = int(np.rint(synth_change/T4_SYNTH_STEP_HZ))
    realized_synth = steps*T4_SYNTH_STEP_HZ
    realized = -realized_synth/T4_SYNTH_SCALE_HZ
    setfsrel = realized if setfsrel_convention == "output" else -realized
    flags = []
    if abs(setfsrel) > T4_SETFSREL_LIMIT:
        flags.append("SETFSREL magnitude exceeds the documented +/-29E-9 range.")
    if steps == 0:
        flags.append("Required change is below one synthesizer step (6.4e-15); no SETFSREL suggested.")
    new_synth = None
    if current_synth_hz is not None and np.isfinite(current_synth_hz):
        new_synth = float(current_synth_hz) + realized_synth
        if not T4_SYNTH_RANGE_HZ[0] <= new_synth <= T4_SYNTH_RANGE_HZ[1]:
            flags.append("Predicted synthesizer frequency leaves the documented 405708-405795 Hz range.")
    drift_block = None
    if fit.order == 2:
        measured_drift = sign_multiplier*drift
        current = float(current_driftcomp) if (current_driftcomp is not None and driftcomp_enabled) else 0.0
        new_driftcomp = current - measured_drift
        if not T4_DRIFTCOMP_RANGE[0] < abs(new_driftcomp) < T4_DRIFTCOMP_RANGE[1]:
            flags.append("New DRIFTCOMP magnitude is outside the documented 1e-17 .. 8e-13 range.")
        drift_block = {"measured_residual_drift_per_day": measured_drift,
                       "conditional_drift_sigma_per_day": drift_sigma,
                       "current_driftcomp": current_driftcomp, "current_known": current_driftcomp is not None,
                       "new_driftcomp": new_driftcomp,
                       "command": f"DRIFTCOMP={new_driftcomp:+.2e};",
                       "note": "Send REBOOT after DRIFTCOMP to load it; REBOOT restarts the M&C software "
                               "(T4S-MAN-0015 Table 1, Sec. 1.1.3). Verify with RDREG=DRIFTCOMP;."}
    return {
        "maser": "T4",
        "application_epoch_mjd_utc": apply_mjd,
        "residual_fractional_frequency": y,
        "conditional_frequency_sigma": error_sigma,
        "required_output_change": required,
        "synth_change_hz_unrounded": synth_change,
        "synth_steps": steps,
        "synth_change_hz": realized_synth,
        "realized_output_change": realized,
        "quantization_residual": required - realized,
        "setfsrel_convention": setfsrel_convention,
        "setfsrel": setfsrel,
        "setfsrel_command": f"SETFSREL={setfsrel:+.3E};" if steps else None,
        "current_synth_hz": current_synth_hz,
        "predicted_synth_hz": new_synth,
        "drift": drift_block,
        "time_offset_note": "SETFSREL/DRIFTCOMP do not remove the 1 PPS time offset; that is ADJ1PPS/SYNCPPS.",
        "review_flags": flags,
        "phase_sign_multiplier": sign_multiplier,
        "status": "ADVISORY ONLY: this program never sends SETFSREL, DRIFTCOMP or REBOOT. Verify sign with RDFS.",
    }


# %% ===========================================================================
#    BOTH SUGGESTIONS AT ONCE: FREQUENCY AND DRIFT (0.2.3)
# =============================================================================
def _optional_dds(text) -> Decimal | None:
    """The displayed DDS setting nnnn.nnnnnnn as a Decimal, or None when blank."""
    if text is None or not str(text).strip():
        return None
    setting = Decimal(str(text).strip())
    if not setting.is_finite() or not Decimal("0") < setting < Decimal("10000"):
        raise ValueError("DDS must be a finite nnnn.nnnnnnn value.")
    if setting != setting.quantize(DDS_QUANTUM):
        raise ValueError("DDS must have at most seven decimal places.")
    return setting


def suggested_settings(fit: PhaseFit, maser: str, apply_mjd: float, sign_multiplier: int = 1, *,
                       dds=None, current_drift=None, drift_units: str = "pp18/day",
                       positive_drift: str = "output", drift_enabled: bool = True,
                       t4_synth_hz=None, t4_driftcomp=None, setfsrel_convention: str = "output") -> dict:
    """Both suggested changes at ``apply_mjd``: the frequency setting and the drift.

    ADVISORY ONLY - nothing is ever sent to a maser. All values are full
    float64 (no rounding); display code may shorten them, exports must not.

    MHM2010 (Microsemi MHM-2010 User's Guide 75666-201 rev. J [1]; the same
    relations as correction()):
        y   = sign * fractional frequency of the phase fit at apply_mjd
        D   = sign * fractional drift per day (quadratic fit only)
        g   = 2e-7 / (284.08 + 2e-7 * DDS)            (output change per DDS unit)
        DDS change [counts of 1e-7] = y / (g * 1e-7)   (cancels y; Decimal arithmetic)
        drift change [pp18/day]     = -c * D / (1 + y) * 1e18
                                      (c = +1 if positive pp18 raises the output)
        drift change [DDS counts/day] = D / (g * 1e-7) (+ old * y when the current
                                      drift is given in DDS counts/day)
    Without a DDS setting the nominal 284.08 is used (the count changes by a
    relative 4e-6 at DDS 5752); the new absolute DDS needs the current DDS.
    The DRIFT CHANGE does not need the current drift field; the new absolute
    drift field does.

    T4 iMaser 3000: the SETFSREL and DRIFTCOMP entries of t4_settings().

    A linear phase model estimates no drift: the drift block is None and the
    notes say so (select the quadratic model to get a drift suggestion).
    """
    if maser not in MASER_KINDS:
        raise ValueError(f"maser must be one of {MASER_KINDS}")
    if sign_multiplier not in (-1, 1):
        raise ValueError("Invalid sign convention.")
    error, drift, error_sigma, drift_sigma = fit.frequency(apply_mjd)
    y = sign_multiplier * error
    measured_drift = sign_multiplier * drift if fit.order == 2 else None
    result = {
        "maser": maser, "application_epoch_mjd_utc": float(apply_mjd), "model_order": int(fit.order),
        "model": "y = a2*x^2 + a1*x + a0 (quadratic phase)" if fit.order == 2 else "y = a1*x + a0 (linear phase)",
        "fitted_points": int(np.count_nonzero(fit.used)),
        "residual_fractional_frequency": float(y), "frequency_sigma": float(error_sigma),
        "residual_drift_per_day": None if measured_drift is None else float(measured_drift),
        "drift_sigma_per_day": None if measured_drift is None else float(drift_sigma),
        "phase_sign_multiplier": int(sign_multiplier),
        "frequency_setting": None, "drift_setting": None, "notes": [],
        "status": "ADVISORY ONLY: this program never sends a setting to a maser. Verify the sign, the epoch "
                  "and the maser state before any manual entry.",
    }
    if fit.order != 2:
        result["notes"].append("Linear phase model: drift is not estimated. Select 'Quadratic phase' to get "
                               "the drift suggestion.")
    if maser == "MHM2010":
        if positive_drift not in {"output", "dds"} or drift_units not in {"pp18/day", "DDS counts/day"}:
            raise ValueError("Invalid drift convention or units.")
        setting = _optional_dds(dds)
        base = setting if setting is not None else Decimal(0)
        denominator = Decimal("284.08") + Decimal("0.0000002") * base
        gain = float(Decimal("0.0000002") / denominator)
        change = denominator / Decimal("0.0000002") * Decimal(str(y))      # in DDS units (as correction())
        frequency = {
            "dds_change_counts": float(change / Decimal("0.0000001")),
            "dds_change_counts_sigma": float(error_sigma / (gain * 1e-7)),
            "fractional_frequency_per_dds_count": -gain * 1e-7,
            "current_dds": None if setting is None else f"{setting:.7f}",
            "new_dds": None, "entered_dds_change_counts": None, "quantization_residual_fractional_frequency": None,
            "dds_assumed_for_gain": setting is None,
        }
        if setting is not None:
            new = (setting + change).quantize(DDS_QUANTUM, rounding=ROUND_HALF_EVEN)
            if not Decimal("0") < new < Decimal("10000"):
                raise ValueError("Candidate DDS is outside the displayed nnnn.nnnnnnn format.")
            frequency["new_dds"] = f"{new:.7f}"
            frequency["entered_dds_change_counts"] = int((new - setting) / DDS_QUANTUM)
            frequency["quantization_residual_fractional_frequency"] = float(
                (Decimal(1) + Decimal(str(y))) * denominator / (Decimal("284.08") + Decimal("0.0000002") * new)
                - Decimal(1))
        else:
            result["notes"].append("Current DDS not entered: the count change uses the nominal 284.08 and no new "
                                   "absolute DDS is given.")
        result["frequency_setting"] = frequency
        if measured_drift is not None:
            convention = 1 if positive_drift == "output" else -1
            known = current_drift is not None and np.isfinite(float(current_drift))
            old = float(current_drift) if (known and drift_enabled) else 0.0
            change_pp18 = -convention * measured_drift / (1 + y) * 1e18
            change_counts = measured_drift / (gain * 1e-7)
            if known and drift_units == "DDS counts/day":
                change_counts += old * y
            block = {
                "drift_change_pp18_per_day": float(change_pp18),
                "drift_change_pp18_per_day_sigma": float(drift_sigma / (1 + y) * 1e18),
                "drift_change_dds_counts_per_day": float(change_counts),
                "drift_change_dds_counts_per_day_sigma": float(drift_sigma / (gain * 1e-7)),
                "positive_pp18_convention": positive_drift, "drift_units": drift_units,
                "current_drift_field": float(current_drift) if known else None,
                "existing_drift_enabled": bool(drift_enabled),
                "new_drift_field": None, "new_drift_field_nearest_integer": None,
            }
            if known:
                delta = change_counts if drift_units == "DDS counts/day" else change_pp18
                block["new_drift_field"] = float(old + delta)
                block["new_drift_field_nearest_integer"] = int(np.rint(old + delta))
            else:
                result["notes"].append("Current drift field not entered: the drift CHANGE is given; the new "
                                       "absolute field is the current value plus this change.")
            result["drift_setting"] = block
    else:
        t4 = t4_settings(fit, apply_mjd, sign_multiplier, t4_synth_hz, t4_driftcomp, setfsrel_convention)
        result["frequency_setting"] = {key: t4[key] for key in (
            "required_output_change", "synth_change_hz_unrounded", "synth_steps", "synth_change_hz",
            "realized_output_change", "quantization_residual", "setfsrel_convention", "setfsrel",
            "setfsrel_command", "current_synth_hz", "predicted_synth_hz")}
        if t4["drift"] is not None:
            block = dict(t4["drift"])
            block["driftcomp_change_per_day"] = -float(t4["drift"]["measured_residual_drift_per_day"])
            result["drift_setting"] = block
        result["notes"] += list(t4["review_flags"]) + [t4["time_offset_note"]]
    return result


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

    def one_factor(averaging_factor):
        """All estimators for one averaging factor (pure; run in threads)."""
        m = int(averaging_factor)
        tau = m*tau0
        rows = []
        second = phase[2*m:] - 2*phase[m:-m] + phase[:-2*m]
        if len(second) >= 10:
            rows.append(("OADEV", tau, float(np.sqrt(np.mean(second**2)/(2*tau**2))), len(second)))
        if len(second) >= m:
            cumulative = np.r_[0., np.cumsum(second)]
            moving = cumulative[m:] - cumulative[:-m]
            if len(moving) >= 10:
                modified = np.sqrt(np.mean(moving**2)/(2*m*m*tau*tau))
                rows.append(("MDEV", tau, float(modified), len(moving)))
                rows.append(("TDEV", tau, float(tau*modified/np.sqrt(3)), len(moving)))
        third = (phase[3*m:] - 3*phase[2*m:-m] +
                 3*phase[m:-2*m] - phase[:-3*m])
        if len(third) >= 10:
            rows.append(("OHDEV", tau, float(np.sqrt(np.mean(third**2)/(6*tau*tau))), len(third)))
        return rows

    # NumPy releases the GIL inside these vector operations, so the factors
    # run concurrently; results are assembled in factor order (deterministic).
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as pool:
        for rows in pool.map(one_factor, factors):
            for key, tau, value, terms in rows:
                stores[key]["tau_s"].append(tau)
                stores[key]["deviation"].append(value)
                stores[key]["terms"].append(terms)
    output["estimators"] = stores
    return output


# %% ===========================================================================
#    WINDOW SENSITIVITY: A DIAGNOSTIC, NOT AN UNCERTAINTY ESTIMATE
# =============================================================================
def window_sensitivity(series, low_mjd, high_mjd, excluded=(), bridge_gaps=False, maser="MHM2010",
                       days=(30, 21, 14, 7)):
    """Refit trailing windows (default 30/21/14/7 days) ending at the selected endpoint.

    Each window starts at max(low_mjd, high_mjd - days); 0.2.2 adds ``days``.
    """
    output = []
    for length in days:
        start = max(low_mjd, high_mjd-length)
        trial = fit_phase(series, start, high_mjd, order=2, excluded=excluded, maser=maser,
                          bridge_gaps=bridge_gaps)
        frequency, drift, _, _ = trial.frequency(high_mjd)
        output.append({"requested_days": length, "actual_span_days":
                       float(np.ptp(series.mjd[trial.used])), "samples": int(trial.used.sum()),
                       "fractional_frequency_at_end": frequency,
                       "drift_pp18_per_day": drift*1e18, "rms_ns": trial.rms_ns})
    return output
