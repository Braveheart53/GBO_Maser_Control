#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Further evaluation of the timing data: gap-aware stability with confidence
intervals, steering removal from the HMD, a Kalman clock estimate and the
three-cornered hat.

===============================================================================
 Title          : Maser Timing Further Evaluation
 Filename       : maser_eval.py
 Version        : 0.0.1 (Semantic Versioning 2.0.0; first internal draft)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-10
 Modified       : 2026-11-10
 Language       : Python 3.12 (3.13 tested)
 Requires       : numpy, scipy (stats, optimize); maser_core, maser_io
 Licence        : GPL-3.0-or-later; see LICENSE
 Revision       : 0.0.1 Workbench 0.3.0 (operator request of 2026-11-10):
                  evaluation methods in separate tabs; the base fit and the
                  suggested settings are NOT changed by anything here.
===============================================================================

WHAT IS HERE
------------
1. regular_grid(): samples on a nominal grid tau0 with gap markers (NaN);
   a sample is placed when |t - slot| <= epsilon * tau0, the closest of
   several in one slot is kept, nothing is interpolated [1, Secs. 10.4-10.6].
2. gap_stability(): OADEV, MDEV, TDEV and OHDEV from the grid, using only
   terms whose samples all exist [1, 3]; lag-1 autocorrelation noise
   identification [1, Sec. 5.5.5; 4]; chi-squared confidence intervals of
   OADEV with the simple EDF formulas of [1, Table 5]; simple sigma/sqrt(N)
   intervals for MDEV, TDEV and OHDEV [1, Sec. 5.3.1]. On a gap-free uniform
   record the deviations equal maser_core.stability().
3. steering_removal(): the MHM2010 frequency steering recorded in the HMD
   (external DDS setting) integrated to phase and removed from the measured
   1 PPS phase, so the maser's own behavior can be seen; the same DDS
   constants as maser_core.correction() [2].
4. kalman_clock(): three-state clock model (phase, frequency, drift) with
   white phase measurement noise [5, 6]; gaps are carried by prediction;
   Rauch-Tung-Striebel smoothing; noise parameters from the gap-aware
   Hadamard deviation [6, 7]; an estimate at the application epoch that can
   be mapped onto settings through maser_core.suggested_settings() (for
   evaluation only).
5. three_cornered_hat(): individual Allan variances of three clocks from
   three pair comparisons, or from two pairs against a common clock [8].
6. run_gap_stability(), run_steering(), run_kalman(),
   run_three_cornered_hat(): the result dicts shown by the GUI tabs and
   written by the export, shared by the GUI and analyze_maser.py.

PARALLELISM / GPU
-----------------
Averaging factors (and the three pairs of the hat) run in a thread pool;
NumPy releases the GIL in the vector operations. The arrays here are small
(<= 1e5 samples), so the GPU is not used: transfer would cost more than the
work.

References:
[1] W. J. Riley, Handbook of Frequency Stability Analysis, NIST SP 1065,
    2008. https://tf.nist.gov/general/pdf/2220.pdf
[2] Microsemi, MHM-2010 User's Guide, 75666-201, rev. J, 2014.
[3] I. Sesia and P. Tavella, "Estimating the Allan variance in presence of
    long periods of missing data and outliers," Metrologia 45, S134, 2008.
[4] W. J. Riley and C. A. Greenhall, "Power law noise identification using
    the lag 1 autocorrelation," Proc. 18th EFTF, 2004.
[5] L. Galleani and P. Tavella, "Time and the Kalman filter," IEEE Control
    Syst. Mag. 30(2), 44-65, 2010, doi:10.1109/MCS.2009.935568.
[6] C. Zucca and P. Tavella, "The clock model and its relationship with the
    Allan and related variances," IEEE TUFFC 52(2), 289-296, 2005.
[7] T. Ishizaki et al., "Higher-order Allan variance for atomic clocks of
    arbitrary order," arXiv:2302.05556 (Eq. 23b: Hadamard variance).
[8] J. E. Gray and D. W. Allan, "A method for estimating the frequency
    stability of an individual oscillator," Proc. 28th Annu. Symp. Frequency
    Control, pp. 243-246, 1974.
"""

# %% ===========================================================================
#    IMPORTS AND CONSTANTS
# =============================================================================
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import math
import os

import numpy as np
from scipy import optimize, stats

from maser_core import (DDS_BASE, DDS_FACTOR, SECONDS_PER_DAY, PhaseFit, elapsed_seconds,
                        fit_phase, suggested_settings)
from maser_io import TimingSeries

__version__ = "0.0.1"

#: 68.27 % = one standard deviation of a normal distribution.
ONE_SIGMA = math.erf(1.0 / math.sqrt(2.0))
#: Estimators of gap_stability() in display order.
ESTIMATORS = ("OADEV", "MDEV", "TDEV", "OHDEV")
#: Noise exponents alpha of S_y(f) ~ f^alpha with simple OADEV EDF formulas.
NOISE_NAMES = {2: "White PM", 1: "Flicker PM", 0: "White FM", -1: "Flicker FM", -2: "Random-walk FM"}
#: Minimum decimated length for lag-1 noise identification [4].
NOISE_ID_MIN_POINTS = 30
#: HMD DDS column used for steering (ExtSynth_32: external synthesizer).
DEFAULT_DDS_COLUMN = "ExtSynth_32"


def _threads() -> int:
    """Worker threads for per-factor work."""
    return max(1, min(8, os.cpu_count() or 1))


# %% ===========================================================================
#    1. REGULAR GRID WITH GAP MARKERS (no interpolation)
# =============================================================================
@dataclass
class RegularGrid:
    """Samples placed on slots k = 0, 1, ...; NaN marks a gap (empty slot)."""
    tau0_s: float
    origin_mjd: float
    slot_tolerance: float
    mode: str                      # "anchored" or "sequential"
    phase_s: np.ndarray            # one value per slot, NaN = gap
    sample_index: np.ndarray       # input index placed in each slot, -1 = gap
    offset_fraction: np.ndarray    # anchored: (t - slot time)/tau0; sequential: interval misfit
    n_input: int
    n_off_grid: int                # anchored: not placed; sequential: placed, interval off by > tolerance
    n_duplicates: int              # samples not placed because their slot was taken
    gaps: list                     # (first missing slot MJD, last missing slot MJD, slots)

    @property
    def n_slots(self) -> int:
        return int(len(self.phase_s))

    @property
    def n_placed(self) -> int:
        return int(np.count_nonzero(self.sample_index >= 0))

    @property
    def slot_mjd(self) -> np.ndarray:
        """Nominal slot times (sequential mode: nominal, not the sample times)."""
        return self.origin_mjd + np.arange(self.n_slots) * self.tau0_s / SECONDS_PER_DAY

    def place(self, values) -> np.ndarray:
        """Another quantity on the same time stamps, placed on this grid."""
        values = np.asarray(values, float)
        out = np.full(self.n_slots, np.nan)
        placed = self.sample_index >= 0
        out[placed] = values[self.sample_index[placed]]
        return out

    def summary(self) -> dict:
        offsets = self.offset_fraction[self.sample_index >= 0]
        offsets = offsets[np.isfinite(offsets)]
        return {"mode": self.mode, "tau0_s": self.tau0_s, "origin_mjd": self.origin_mjd,
                "slot_tolerance_fraction": self.slot_tolerance, "slots": self.n_slots,
                "placed": self.n_placed, "gap_slots": self.n_slots - self.n_placed,
                "input_samples": self.n_input,
                ("off_grid_not_placed" if self.mode == "anchored" else "intervals_beyond_tolerance"):
                    self.n_off_grid,
                "duplicates_not_placed": self.n_duplicates,
                "max_abs_offset_fraction": float(np.max(np.abs(offsets))) if offsets.size else None,
                "gap_runs": len(self.gaps),
                "longest_gap_slots": max((g[2] for g in self.gaps), default=0)}


GRID_MODES = ("auto", "anchored", "sequential")
#: Auto mode: anchored when at least this fraction of samples is placed.
AUTO_ANCHORED_FRACTION = 0.9


def nominal_tau0(mjd) -> float:
    """Median positive sample interval (s), the default grid step."""
    seconds = elapsed_seconds(np.asarray(mjd, float), float(np.min(mjd)))
    steps = np.diff(np.sort(seconds))
    steps = steps[steps > 0]
    if not steps.size:
        raise ValueError("At least two distinct time stamps are needed.")
    return float(np.median(steps))


def _gap_runs(index: np.ndarray, origin: float, tau0: float) -> list:
    """(first, last missing slot MJD, number of slots) for every gap run."""
    gaps = []
    missing = index < 0
    if missing.any():
        edges = np.flatnonzero(np.diff(np.r_[0, missing.astype(int), 0]))
        for start, stop in zip(edges[::2], edges[1::2]):
            gaps.append((origin + start * tau0 / SECONDS_PER_DAY,
                         origin + (stop - 1) * tau0 / SECONDS_PER_DAY, int(stop - start)))
    return gaps


def regular_grid(mjd, phase_s, tau0_s: float | None = None, slot_tolerance: float = 0.10,
                 origin_mjd: float | None = None, n_slots: int | None = None,
                 mode: str = "auto") -> RegularGrid:
    """Place time-tagged phase on a regular grid; empty slots are gaps.

    "anchored": slot k = round((t - origin)/tau0); a sample is placed when
    it lies within ``slot_tolerance`` x tau0 of its slot time (a stable
    sampling clock, e.g. FITS 270 s); of two samples in one slot the closer
    one is kept.
    "sequential" (SP 1065 Secs. 5.16, 10.5 [1]): each sample advances by
    round(dt/tau0) slots from the previous one, so jitter does not
    accumulate; an interval of k > 1 slots leaves k - 1 gap slots; a sample
    less than tau0/2 after the previous one is not placed; intervals whose
    misfit |dt/tau0 - k| exceeds the tolerance are counted (SP 1065 accepts
    about +/-10 %).
    "auto": anchored when it places at least 90 % of the samples, else
    sequential. Nothing is interpolated or moved in value.
    """
    times = np.asarray(mjd, float)
    phase = np.asarray(phase_s, float)
    if times.ndim != 1 or times.shape != phase.shape or times.size < 2:
        raise ValueError("Time and phase must be matching 1-D arrays with at least two samples.")
    if not 0 < slot_tolerance <= 0.5:
        raise ValueError("Slot tolerance must be > 0 and <= 0.5 of tau0.")
    if mode not in GRID_MODES:
        raise ValueError(f"Grid mode must be one of {GRID_MODES}.")
    good = np.isfinite(times) & np.isfinite(phase)
    origin = float(np.min(times[good])) if origin_mjd is None else float(origin_mjd)
    tau0 = nominal_tau0(times[good]) if tau0_s is None else float(tau0_s)
    if not tau0 > 0:
        raise ValueError("tau0 must be positive.")
    if mode == "auto":
        anchored = regular_grid(times, phase, tau0, slot_tolerance, origin, n_slots, "anchored")
        if anchored.n_placed >= AUTO_ANCHORED_FRACTION * anchored.n_input:
            return anchored
        return regular_grid(times, phase, tau0, slot_tolerance, origin, n_slots, "sequential")
    if mode == "anchored":
        position = elapsed_seconds(times, origin) / tau0
        slot = np.rint(position).astype(np.int64)
        offset = position - slot
        usable = good & (np.abs(offset) <= slot_tolerance) & (slot >= 0)
        count = (int(slot[usable].max()) + 1 if usable.any() else 0) if n_slots is None else int(n_slots)
        usable &= slot < count
        grid = np.full(count, np.nan)
        index = np.full(count, -1, dtype=np.int64)
        offsets = np.full(count, np.nan)
        # Closest sample per slot: farthest first, so the closest is written last.
        candidates = np.flatnonzero(usable)
        order = candidates[np.argsort(-np.abs(offset[candidates]), kind="stable")]
        grid[slot[order]] = phase[order]
        index[slot[order]] = order
        offsets[slot[order]] = offset[order]
        duplicates = int(candidates.size - np.count_nonzero(index >= 0))
        off_grid = int(np.count_nonzero(good & (np.abs(offset) > slot_tolerance)))
        return RegularGrid(tau0, origin, float(slot_tolerance), "anchored", grid, index, offsets,
                           int(good.sum()), off_grid, duplicates, _gap_runs(index, origin, tau0))
    # Sequential: walk the samples in time order.
    order = np.flatnonzero(good)[np.argsort(times[good], kind="stable")]
    seconds = elapsed_seconds(times[order], origin)
    slots = np.empty(order.size, dtype=np.int64)
    misfit = np.zeros(order.size)
    keep = np.ones(order.size, dtype=bool)
    current = int(np.rint(seconds[0] / tau0)) if origin_mjd is not None else 0
    slots[0] = current
    last_time = seconds[0]
    for i in range(1, order.size):
        steps = (seconds[i] - last_time) / tau0
        k = int(np.rint(steps))
        if k < 1:
            keep[i] = False                       # crowded: slot already taken
            continue
        current += k
        slots[i] = current
        misfit[i] = steps - k
        last_time = seconds[i]
    count = int(slots[keep].max()) + 1 if n_slots is None else int(n_slots)
    keep &= slots < count
    grid = np.full(count, np.nan)
    index = np.full(count, -1, dtype=np.int64)
    offsets = np.full(count, np.nan)
    grid[slots[keep]] = phase[order[keep]]
    index[slots[keep]] = order[keep]
    offsets[slots[keep]] = misfit[keep]
    beyond = int(np.count_nonzero(keep & (np.abs(misfit) > slot_tolerance)))
    return RegularGrid(tau0, origin, float(slot_tolerance), "sequential", grid, index, offsets,
                       int(good.sum()), beyond, int(np.count_nonzero(~keep)), _gap_runs(index, origin, tau0))


def detrend_grid(grid: RegularGrid, mjd, order: int) -> np.ndarray:
    """Grid phase with a polynomial of ``order`` (-1 raw, 0..2) removed.

    The polynomial is fitted to the placed samples against their real time
    stamps (SP 1065 drift removal [1, Sec. 5.21]); gaps stay NaN.
    """
    phase = grid.phase_s.copy()
    if order not in (-1, 0, 1, 2):
        raise ValueError("Detrend order must be -1 (raw), 0, 1 or 2.")
    placed = grid.sample_index >= 0
    if order < 0:
        first = np.flatnonzero(placed)
        if first.size:
            phase -= phase[first[0]]          # a constant: stability-invariant
        return phase
    times = np.asarray(mjd, float)[grid.sample_index[placed]]
    days = elapsed_seconds(times, grid.origin_mjd) / SECONDS_PER_DAY
    scale = max(float(np.max(np.abs(days))), 1e-12)
    coefficients = np.polynomial.polynomial.polyfit(days / scale, phase[placed], order)
    phase[placed] -= np.polynomial.polynomial.polyval(days / scale, coefficients)
    return phase


# %% ===========================================================================
#    2. GAP-AWARE STABILITY WITH CONFIDENCE INTERVALS
# =============================================================================
def averaging_factors(n_slots: int) -> np.ndarray:
    """Log-spaced averaging factors, the same rule as maser_core.stability()."""
    return np.unique(np.geomspace(1, max(1, (n_slots - 10) // 3), 35).astype(int))


def gap_deviations(phase: np.ndarray, m: int, tau0: float) -> list:
    """(estimator, tau, deviation, terms) for one averaging factor m.

    Every term uses only existing samples: a second (third) difference that
    touches a gap is skipped [1, Sec. 10.6; 3]. Without gaps the values are
    those of maser_core.stability().
    """
    tau = m * tau0
    rows = []
    second = phase[2 * m:] - 2 * phase[m:-m] + phase[:-2 * m]
    finite = np.isfinite(second)
    if finite.sum() >= 10:
        rows.append(("OADEV", tau, float(np.sqrt(np.mean(second[finite] ** 2) / (2 * tau ** 2))),
                     int(finite.sum())))
    if len(second) >= m:
        filled = np.where(finite, second, 0.0)
        cumulative = np.r_[0.0, np.cumsum(filled)]
        counted = np.r_[0, np.cumsum(finite.astype(np.int64))]
        moving = cumulative[m:] - cumulative[:-m]
        complete = (counted[m:] - counted[:-m]) == m
        if complete.sum() >= 10:
            modified = math.sqrt(float(np.mean(moving[complete] ** 2)) / (2 * m * m * tau * tau))
            rows.append(("MDEV", tau, modified, int(complete.sum())))
            rows.append(("TDEV", tau, tau * modified / math.sqrt(3), int(complete.sum())))
    if len(phase) > 3 * m:
        third = phase[3 * m:] - 3 * phase[2 * m:-m] + 3 * phase[m:-2 * m] - phase[:-3 * m]
        valid = np.isfinite(third)
        if valid.sum() >= 10:
            rows.append(("OHDEV", tau, float(np.sqrt(np.mean(third[valid] ** 2) / (6 * tau * tau))),
                         int(valid.sum())))
    return rows


def lag1_acf(values: np.ndarray) -> float:
    """Lag-1 autocorrelation r1 [4, Eq. 2] (same definition as allantools)."""
    centred = values - np.mean(values)
    denominator = float(np.dot(centred, centred))
    return float(np.dot(centred[:-1], centred[1:]) / denominator) if denominator > 0 else 0.0


def noise_id_lag1(phase_run: np.ndarray, m: int, dmax: int = 2) -> tuple | None:
    """(alpha_int, alpha, d, delta) of S_y(f) ~ f^alpha from phase [1, 4].

    The gap-free phase run is decimated by m, a quadratic is removed, and the
    data are differenced until delta = r1/(1+r1) < 0.25 (or d = dmax);
    alpha = p + 2 with p = -2(delta + d), rounded as -round(2 delta) - 2d + 2.
    None when fewer than NOISE_ID_MIN_POINTS points remain.
    """
    values = np.asarray(phase_run, float)[::m]
    if values.size < NOISE_ID_MIN_POINTS:
        return None
    axis = np.arange(values.size, dtype=float)
    values = values - np.polyval(np.polyfit(axis, values, 2), axis)
    d = 0
    while True:
        r1 = lag1_acf(values)
        delta = r1 / (1.0 + r1)
        if delta < 0.25 or d >= dmax:
            alpha = -2.0 * (delta + d) + 2.0
            alpha_int = int(-np.round(2 * delta) - 2 * d) + 2
            return alpha_int, float(alpha), d, float(delta)
        values = np.diff(values)
        d += 1


def edf_simple_oadev(n_phase: float, m: float, alpha: int) -> float | None:
    """Simple approximate EDF of the overlapping Allan variance [1, Table 5].

    ``n_phase`` is the number of phase points N, ``m`` the averaging factor.
    """
    n, m = float(n_phase), float(m)
    if n - 2 * m < 1:
        return None
    if alpha == 2:
        return (n + 1) * (n - 2 * m) / (2 * (n - m))
    if alpha == 1:
        a, b = (n - 1) / (2 * m), (2 * m + 1) * (n - 1) / 4
        return math.exp(math.sqrt(math.log(a) * math.log(b))) if a > 1 and b > 1 else None
    if alpha == 0:
        return (3 * (n - 1) / (2 * m) - 2 * (n - 2) / n) * 4 * m * m / (4 * m * m + 5)
    if alpha == -1:
        return 2 * (n - 2) / (2.3 * n - 4.9) if m == 1 else 5 * n * n / (4 * m * (n + 3 * m))
    if alpha == -2:
        return (n - 2) / (m * (n - 3) ** 2) * ((n - 1) ** 2 - 3 * m * (n - 1) + 4 * m * m)
    return None


def chi2_interval(deviation: float, edf: float, level: float = ONE_SIGMA) -> tuple:
    """(low, high) deviation limits from the chi-squared law [1, Eq. 45]."""
    tail = (1.0 - level) / 2.0
    low = math.sqrt(edf * deviation ** 2 / stats.chi2.ppf(1.0 - tail, edf))
    high = math.sqrt(edf * deviation ** 2 / stats.chi2.ppf(tail, edf))
    return low, high


def longest_run(phase: np.ndarray) -> np.ndarray:
    """The longest stretch of the grid without a gap."""
    finite = np.isfinite(phase).astype(int)
    edges = np.flatnonzero(np.diff(np.r_[0, finite, 0]))
    if not edges.size:
        return phase[:0]
    starts, stops = edges[::2], edges[1::2]
    best = int(np.argmax(stops - starts))
    return phase[starts[best]:stops[best]]


def gap_stability(mjd, phase_s, detrend_order: int = -1, tau0_s: float | None = None,
                  slot_tolerance: float = 0.10, level: float = ONE_SIGMA, grid_mode: str = "auto") -> dict:
    """Gap-aware OADEV, MDEV, TDEV, OHDEV with confidence intervals.

    Returns {"grid": summary, "estimators": {name: {tau_s, deviation,
    ci_low, ci_high, ci_method, terms, edf, alpha, alpha_name, d}}, ...}.
    OADEV: chi-squared interval with the simple EDF of [1, Table 5] for the
    lag-1 noise type, N = terms + 2m (equivalent gap-free length). MDEV,
    TDEV, OHDEV and any OADEV point without a noise type: the simple interval
    deviation x (1 +/- z/sqrt(terms)) [1, Sec. 5.3.1].
    """
    grid = regular_grid(mjd, phase_s, tau0_s, slot_tolerance, mode=grid_mode)
    if grid.n_placed < 8:
        raise ValueError("At least eight samples must fall on the grid.")
    phase = detrend_grid(grid, mjd, detrend_order)
    run = longest_run(phase)
    z = float(stats.norm.ppf(0.5 + level / 2.0))
    factors = averaging_factors(grid.n_slots)

    def one_factor(m: int) -> list:
        """All estimators, noise type and intervals for one factor."""
        out = []
        noise_adev = noise_id_lag1(run, int(m), dmax=2)
        noise_hdev = noise_id_lag1(run, int(m), dmax=3)
        for name, tau, value, terms in gap_deviations(phase, int(m), grid.tau0_s):
            noise = noise_hdev if name == "OHDEV" else noise_adev
            alpha = None if noise is None else int(np.clip(noise[0], -2, 2))
            edf = None
            if name == "OADEV" and alpha is not None:
                edf = edf_simple_oadev(terms + 2 * int(m), int(m), alpha)
            if edf is not None and edf > 0:
                low, high = chi2_interval(value, edf, level)
                method = "chi2_simple_edf"
            else:
                low = max(0.0, value * (1.0 - z / math.sqrt(terms)))
                high = value * (1.0 + z / math.sqrt(terms))
                method = "simple_sigma_over_sqrtN"
            out.append((name, tau, value, terms, low, high, method, edf, alpha,
                        None if noise is None else noise[2]))
        return out

    estimators = {name: {key: [] for key in ("tau_s", "deviation", "ci_low", "ci_high", "ci_method",
                                             "terms", "edf", "alpha", "alpha_name", "d")}
                  for name in ESTIMATORS}
    with ThreadPoolExecutor(max_workers=_threads()) as pool:
        for rows in pool.map(one_factor, factors):
            for name, tau, value, terms, low, high, method, edf, alpha, d in rows:
                store = estimators[name]
                store["tau_s"].append(tau)
                store["deviation"].append(value)
                store["ci_low"].append(low)
                store["ci_high"].append(high)
                store["ci_method"].append(method)
                store["terms"].append(terms)
                store["edf"].append(edf)
                store["alpha"].append(alpha)
                store["alpha_name"].append(NOISE_NAMES.get(alpha, "not identified"))
                store["d"].append(d)
    return {"method": "gap-aware (terms touching a gap skipped); NIST SP 1065 Secs. 5.3, 5.5.5, 10.4-10.6",
            "confidence_level": level, "detrend_order": detrend_order,
            "grid": grid.summary(), "longest_gap_free_run_slots": int(run.size),
            "tau0_s": grid.tau0_s, "estimators": estimators}


# %% ===========================================================================
#    3. STEERING REMOVAL FROM THE HMD DDS RECORD
# =============================================================================
@dataclass
class SteeringResult:
    """MHM2010 steering from the HMD integrated to phase and removed."""
    mode: str                      # "all" (every DDS change) or "steps"
    column: str
    threshold_counts: float
    reference_dds: float
    sign_multiplier: int
    mjd: np.ndarray                # timing sample times
    measured_phase_s: np.ndarray
    applied_y: np.ndarray          # fractional frequency applied at each sample
    applied_phase_s: np.ndarray    # its integral (maser minus reference sense)
    removed_phase_s: np.ndarray    # measured channel with the steering removed
    covered: np.ndarray            # inside the HMD record
    hmd_mjd: np.ndarray
    hmd_dds: np.ndarray            # the DDS used (steps-only: the stepped DDS)
    hmd_applied_y: np.ndarray
    events: list
    hmd_gaps: list
    notes: list = field(default_factory=list)

    def summary(self) -> dict:
        covered = self.covered
        return {"mode": self.mode, "dds_column": self.column, "threshold_counts": self.threshold_counts,
                "reference_dds": self.reference_dds, "phase_sign_multiplier": self.sign_multiplier,
                "timing_samples": int(self.mjd.size), "covered_samples": int(covered.sum()),
                "hmd_rows": int(self.hmd_mjd.size),
                "hmd_first_mjd": float(self.hmd_mjd[0]) if self.hmd_mjd.size else None,
                "hmd_last_mjd": float(self.hmd_mjd[-1]) if self.hmd_mjd.size else None,
                "applied_y_first": float(self.applied_y[covered][0]) if covered.any() else None,
                "applied_y_last": float(self.applied_y[covered][-1]) if covered.any() else None,
                "applied_phase_total_s": float(self.applied_phase_s[covered][-1]) if covered.any() else None,
                "step_events": self.events, "hmd_gaps_held": self.hmd_gaps, "notes": self.notes}

    def removed_series(self, label: str, source_kind: str) -> TimingSeries:
        """The steering-removed record as a TimingSeries (covered samples)."""
        keep = self.covered & np.isfinite(self.removed_phase_s)
        return TimingSeries(self.mjd[keep], self.removed_phase_s[keep], label + " (steering removed)",
                            source_kind, notes=[f"MHM2010 steering from HMD {self.column} removed, mode "
                                                f"{self.mode}; maser_eval {__version__}"])


def dds_fractional_change(dds_reference: float, dds) -> np.ndarray:
    """Output fractional frequency of DDS ``dds`` relative to ``dds_reference``.

    The MHM2010 output scales as 1/(284.08 + 2e-7 N) [2]; maser_core.correction()
    uses the same relation (raising the DDS lowers the output):
        dy = D(N_ref)/D(N) - 1 = 2e-7 (N_ref - N) / (284.08 + 2e-7 N).
    """
    values = np.asarray(dds, float)
    return DDS_FACTOR * (float(dds_reference) - values) / (DDS_BASE + DDS_FACTOR * values)


def steering_removal(mjd, phase_s, hmd_frame, sign_multiplier: int = 1, mode: str = "all",
                     threshold_counts: float = 50.0, column: str = DEFAULT_DDS_COLUMN,
                     hold_limit_s: float = 3600.0) -> SteeringResult:
    """Remove the recorded MHM2010 steering from the measured phase.

    ``mode`` "all": every DDS change (manual steps and the slow drift
    compensation); "steps": only jumps between consecutive HMD rows larger
    than ``threshold_counts`` least-significant DDS digits (1e-7). The DDS is
    held constant between HMD rows (zero-order hold); holds longer than
    ``hold_limit_s`` are listed. With x the measured channel and s the phase
    sign (s x = maser minus reference):
        x_removed(t) = x(t) - s * integral_{t0}^{t} dy(t') dt'.
    Samples outside the HMD record are not corrected (NaN, covered = False).
    """
    if mode not in ("all", "steps") or sign_multiplier not in (-1, 1):
        raise ValueError("mode must be 'all' or 'steps' and the sign +1 or -1.")
    if hmd_frame is None or column not in hmd_frame or "MJD" not in hmd_frame:
        raise ValueError(f"The HMD record needs MJD and {column} columns.")
    times = np.asarray(mjd, float)
    phase = np.asarray(phase_s, float)
    frame = hmd_frame[["MJD", column]].dropna().sort_values("MJD")
    frame = frame.loc[~frame.MJD.duplicated(keep="last")]
    hmd_mjd = frame.MJD.to_numpy(float)
    dds = frame[column].to_numpy(float)
    if hmd_mjd.size < 2:
        raise ValueError("The HMD record has fewer than two DDS rows.")
    jumps = np.diff(dds)
    events = []
    big = np.flatnonzero(np.abs(jumps) * 1e7 > threshold_counts)
    for index in big:
        events.append({"mjd_utc": float(hmd_mjd[index + 1]), "dds_before": float(dds[index]),
                       "dds_after": float(dds[index + 1]), "change_counts": float(jumps[index] * 1e7)})
    if mode == "steps":
        stepped = np.r_[0.0, np.where(np.abs(jumps) * 1e7 > threshold_counts, jumps, 0.0)]
        used = dds[0] + np.cumsum(stepped)
    else:
        used = dds.copy()
    inside = (times >= hmd_mjd[0]) & (times <= hmd_mjd[-1])
    first = int(np.searchsorted(hmd_mjd, times[inside].min(), side="right") - 1) if inside.any() else 0
    reference = float(used[max(first, 0)])
    applied = dds_fractional_change(reference, used)
    seconds = elapsed_seconds(hmd_mjd, float(hmd_mjd[0]))
    held = np.diff(seconds)
    integral = np.r_[0.0, np.cumsum(applied[:-1] * held)]           # zero-order hold
    hmd_gaps = [(float(hmd_mjd[i]), float(hmd_mjd[i + 1]), float(held[i]))
                for i in np.flatnonzero(held > hold_limit_s)]
    position = np.clip(np.searchsorted(hmd_mjd, times, side="right") - 1, 0, hmd_mjd.size - 1)
    sample_seconds = elapsed_seconds(times, float(hmd_mjd[0]))
    phase_applied = integral[position] + applied[position] * (sample_seconds - seconds[position])
    # Reference the integral to the first covered sample (a constant).
    if inside.any():
        phase_applied = phase_applied - phase_applied[np.flatnonzero(inside)[0]]
    applied_at = np.where(inside, applied[position], np.nan)
    phase_applied = np.where(inside, phase_applied, np.nan)
    removed = np.where(inside, phase - sign_multiplier * phase_applied, np.nan)
    notes = [f"DDS from HMD column {column}; zero-order hold between rows; reference DDS {reference!r}.",
             "Which synthesizer drives the output (IntExtSwitch_33 / IntExtSoft_34 codes) is not decoded: "
             "confirm that the external DDS is in use."]
    if hmd_gaps:
        notes.append(f"{len(hmd_gaps)} HMD interval(s) longer than {hold_limit_s:g} s were held at the last "
                     "DDS value; a change inside one would be misplaced in time.")
    if not inside.all():
        notes.append(f"{int((~inside).sum())} timing sample(s) outside the HMD record are not corrected.")
    return SteeringResult(mode, column, float(threshold_counts), reference, int(sign_multiplier), times,
                          phase, applied_at, phase_applied, removed, inside, hmd_mjd, used, applied,
                          events, hmd_gaps, notes)


def steering_fit_comparison(series: TimingSeries, base_fit: PhaseFit, steering: SteeringResult,
                            low_mjd: float, high_mjd: float, apply_mjd: float, sign_multiplier: int = 1,
                            maser: str = "MHM2010", bridge_gaps: bool = True) -> dict:
    """Base fit versus the same fit of the steering-removed record (evaluation).

    Frequencies are maser minus reference (phase sign applied); the drift of
    the removed record is the maser's own drift with the recorded steering
    taken out.
    """
    removed = steering.removed_series(series.label, series.source_kind)
    free = fit_phase(removed, low_mjd, high_mjd, order=base_fit.order, maser=maser, bridge_gaps=bridge_gaps)
    base_y, base_d, base_sy, base_sd = base_fit.frequency(apply_mjd)
    free_y, free_d, free_sy, free_sd = free.frequency(apply_mjd)
    s = sign_multiplier
    return {"application_epoch_mjd_utc": apply_mjd,
            "base": {"fractional_frequency": s * base_y, "sigma": base_sy,
                     "drift_per_day": s * base_d if base_fit.order == 2 else None, "drift_sigma": base_sd,
                     "samples": int(base_fit.used.sum())},
            "steering_removed": {"fractional_frequency": s * free_y, "sigma": free_sy,
                                 "drift_per_day": s * free_d if free.order == 2 else None,
                                 "drift_sigma": free_sd, "samples": int(free.used.sum())},
            "removed_fit": free}


# %% ===========================================================================
#    4. KALMAN CLOCK ESTIMATE (phase, frequency, drift)
# =============================================================================
# Internal units: phase ns, frequency ns/day, drift ns/day^2, time days (the
# units of maser_core.PhaseFit), which keeps the covariance well conditioned.
_NS = 1e9


@dataclass
class KalmanNoise:
    """White PM variance R (s^2) and diffusion coefficients q1 (s), q2 (1/s),
    q3 (1/s^3) of the three-state clock model [5, 6]."""
    r_s2: float
    q1_s: float
    q2_per_s: float
    q3_per_s3: float
    source: str = "manual"
    detail: dict = field(default_factory=dict)

    def scaled(self) -> tuple:
        """(R, Q1, Q2, Q3) in ns / day units."""
        day = SECONDS_PER_DAY
        return (self.r_s2 * _NS ** 2, self.q1_s * _NS ** 2 * day, self.q2_per_s * _NS ** 2 * day ** 3,
                self.q3_per_s3 * _NS ** 2 * day ** 5)

    def hadamard_variance(self, tau_s) -> np.ndarray:
        """Model Hadamard variance (10/3)R/tau^2 + q1/tau + q2 tau/6 + 11 q3 tau^3/120 [7]."""
        tau = np.asarray(tau_s, float)
        return (10.0 / 3.0 * self.r_s2 / tau ** 2 + self.q1_s / tau + self.q2_per_s * tau / 6.0
                + 11.0 / 120.0 * self.q3_per_s3 * tau ** 3)

    def as_dict(self) -> dict:
        return {"R_s2": self.r_s2, "q1_s": self.q1_s, "q2_per_s": self.q2_per_s, "q3_per_s3": self.q3_per_s3,
                "source": self.source, "detail": self.detail}


def kalman_noise_from_hadamard(stability_result: dict) -> KalmanNoise:
    """Noise parameters from the gap-aware Hadamard deviation (OHDEV).

    Non-negative least squares of the relative misfit of
        sigma_H^2(tau) = (10/3) R / tau^2 + q1 / tau + q2 tau / 6 + (11/120) q3 tau^3
    (white PM measurement noise plus the three-state model [6, 7]); the
    Hadamard variance does not respond to a constant drift. Each point is
    weighted by sqrt(terms), so long-tau points with few terms count less
    (on simulated three-state data this gave smoothed-frequency z-scores
    with RMS 0.84-0.98 and normalized innovation squared 1.00-1.01).
    """
    hdev = stability_result["estimators"]["OHDEV"]
    tau = np.asarray(hdev["tau_s"], float)
    variance = np.asarray(hdev["deviation"], float) ** 2
    terms = np.asarray(hdev["terms"], float)
    keep = np.isfinite(tau) & np.isfinite(variance) & (variance > 0) & (terms > 0)
    tau, variance, terms = tau[keep], variance[keep], terms[keep]
    if tau.size < 4:
        raise ValueError("At least four OHDEV points are needed to estimate the Kalman noise.")
    basis = np.column_stack([10.0 / 3.0 / tau ** 2, 1.0 / tau, tau / 6.0, 11.0 / 120.0 * tau ** 3])
    weight = np.sqrt(terms)
    rows = basis / variance[:, None] * weight[:, None]
    norms = np.linalg.norm(rows, axis=0)
    solution, residual = optimize.nnls(rows / norms, weight)
    coefficients = solution / norms
    model = basis @ coefficients
    return KalmanNoise(*map(float, coefficients), source="auto (OHDEV fit, sqrt(terms) weights)",
                       detail={"tau_s": tau.tolist(), "ohdev": np.sqrt(variance).tolist(),
                               "model_ohdev": np.sqrt(model).tolist(),
                               "rms_relative_misfit": float(np.sqrt(np.mean((model / variance - 1) ** 2)))})


def _transition(dt: float) -> np.ndarray:
    return np.array([[1.0, dt, dt * dt / 2.0], [0.0, 1.0, dt], [0.0, 0.0, 1.0]])


def _process(dt: float, q1: float, q2: float, q3: float) -> np.ndarray:
    """Discrete process covariance of the three-state model for step dt [5, 7]."""
    return np.array([
        [q1 * dt + q2 * dt ** 3 / 3 + q3 * dt ** 5 / 20, q2 * dt ** 2 / 2 + q3 * dt ** 4 / 8, q3 * dt ** 3 / 6],
        [q2 * dt ** 2 / 2 + q3 * dt ** 4 / 8, q2 * dt + q3 * dt ** 3 / 3, q3 * dt ** 2 / 2],
        [q3 * dt ** 3 / 6, q3 * dt ** 2 / 2, q3 * dt]])


@dataclass
class KalmanResult:
    """Kalman (and RTS-smoothed) phase, frequency and drift of the comparison."""
    mjd: np.ndarray
    measured_ns: np.ndarray
    state: np.ndarray              # (n, 3): ns, ns/day, ns/day^2 (smoothed if smooth)
    covariance: np.ndarray         # (n, 3, 3)
    innovation_ns: np.ndarray
    innovation_sigma_ns: np.ndarray
    noise: KalmanNoise
    smoothed: bool
    last_state: np.ndarray
    last_covariance: np.ndarray

    @property
    def normalized_innovation_squared(self) -> float:
        """Mean of (innovation / its predicted sigma)^2: about 1 when the noise
        model matches the data; well above 1 means the model is too
        confident (its sigmas too small)."""
        return float(np.mean((self.innovation_ns / self.innovation_sigma_ns) ** 2))

    @property
    def fractional_frequency(self) -> np.ndarray:
        return self.state[:, 1] / _NS / SECONDS_PER_DAY

    @property
    def fractional_frequency_sigma(self) -> np.ndarray:
        return np.sqrt(self.covariance[:, 1, 1]) / _NS / SECONDS_PER_DAY

    @property
    def drift_per_day(self) -> np.ndarray:
        return self.state[:, 2] / _NS / SECONDS_PER_DAY

    @property
    def drift_sigma_per_day(self) -> np.ndarray:
        return np.sqrt(self.covariance[:, 2, 2]) / _NS / SECONDS_PER_DAY

    def at(self, apply_mjd: float) -> tuple:
        """(state, covariance) at ``apply_mjd``: the estimate at the last sample
        at or before it, predicted forward with the process noise."""
        index = int(np.searchsorted(self.mjd, apply_mjd, side="right") - 1)
        if index < 0:
            raise ValueError("The application epoch is before the first sample.")
        state, covariance = ((self.last_state, self.last_covariance) if index >= self.mjd.size - 1
                             else (self.state[index], self.covariance[index]))
        dt = float(elapsed_seconds(apply_mjd, self.mjd[index]) / SECONDS_PER_DAY)
        _, q1, q2, q3 = self.noise.scaled()
        phi = _transition(dt)
        return phi @ state, phi @ covariance @ phi.T + _process(dt, q1, q2, q3)

    def estimate(self, apply_mjd: float, sign_multiplier: int = 1) -> dict:
        """Frequency and drift (maser minus reference sense) at the epoch."""
        state, covariance = self.at(apply_mjd)
        s = sign_multiplier
        return {"application_epoch_mjd_utc": apply_mjd,
                "fractional_frequency": s * state[1] / _NS / SECONDS_PER_DAY,
                "fractional_frequency_sigma": math.sqrt(covariance[1, 1]) / _NS / SECONDS_PER_DAY,
                "drift_per_day": s * state[2] / _NS / SECONDS_PER_DAY,
                "drift_sigma_per_day": math.sqrt(covariance[2, 2]) / _NS / SECONDS_PER_DAY}

    def as_phasefit(self, apply_mjd: float) -> PhaseFit:
        """The estimate at the epoch as a PhaseFit (quadratic about the epoch),
        so maser_core.suggested_settings() maps it with the base arithmetic."""
        state, covariance = self.at(apply_mjd)
        jacobian = np.diag([1.0, 1.0, 0.5])
        used = np.ones(self.mjd.size, dtype=bool)
        return PhaseFit(float(apply_mjd), 2, jacobian @ state, jacobian @ covariance @ jacobian.T, used, used,
                        self.innovation_ns * 1e-9, (self.measured_ns - self.innovation_ns) * 1e-9,
                        float(np.sqrt(np.mean(self.innovation_ns ** 2))), float("nan"),
                        ["Kalman estimate (evaluation only; not the base suggestion)."])

    def suggestion(self, apply_mjd: float, maser: str, sign_multiplier: int = 1, **settings) -> dict:
        """Settings from the Kalman estimate through suggested_settings()."""
        result = suggested_settings(self.as_phasefit(apply_mjd), maser, apply_mjd, sign_multiplier, **settings)
        result["notes"] = ["EVALUATION ONLY: Kalman estimate, not the base suggestion."] + list(
            result.get("notes", []))
        return result


def kalman_clock(mjd, phase_s, noise: KalmanNoise, smooth: bool = True) -> KalmanResult:
    """Three-state Kalman filter (and RTS smoother) of time-tagged phase.

    State X = [x, y, d] (ns, ns/day, ns/day^2), transition
    Phi(dt) = [[1, dt, dt^2/2], [0, 1, dt], [0, 0, 1]], process covariance
    from (q1, q2, q3) [5, 7], measurement z = x + v with Var(v) = R. A gap
    is one longer prediction step (no data are invented); the covariance
    grows across it. Joseph-form update; the prior is diffuse.
    """
    times = np.asarray(mjd, float)
    measured = np.asarray(phase_s, float) * _NS
    keep = np.isfinite(times) & np.isfinite(measured)
    times, measured = times[keep], measured[keep]
    order = np.argsort(times, kind="stable")
    times, measured = times[order], measured[order]
    if times.size < 3:
        raise ValueError("At least three samples are needed.")
    r, q1, q2, q3 = noise.scaled()
    if r <= 0:
        raise ValueError("The measurement variance R must be positive.")
    days = elapsed_seconds(times, float(times[0])) / SECONDS_PER_DAY
    n = times.size
    h = np.array([1.0, 0.0, 0.0])
    eye = np.eye(3)
    predicted_x = np.zeros((n, 3))
    predicted_p = np.zeros((n, 3, 3))
    filtered_x = np.zeros((n, 3))
    filtered_p = np.zeros((n, 3, 3))
    innovation = np.zeros(n)
    innovation_sigma = np.zeros(n)
    spread = float(np.ptp(measured)) + math.sqrt(r)
    x = np.array([measured[0], 0.0, 0.0])
    p = np.diag([spread ** 2 + r, (spread + 1e3) ** 2, 1e4])      # diffuse prior
    for k in range(n):
        if k:
            dt = days[k] - days[k - 1]
            phi = _transition(dt)
            x = phi @ x
            p = phi @ p @ phi.T + _process(dt, q1, q2, q3)
        predicted_x[k], predicted_p[k] = x, p
        s = float(h @ p @ h) + r
        gain = p @ h / s
        residual = measured[k] - x[0]
        innovation[k], innovation_sigma[k] = residual, math.sqrt(s)
        x = x + gain * residual
        joseph = eye - np.outer(gain, h)
        p = joseph @ p @ joseph.T + r * np.outer(gain, gain)
        p = 0.5 * (p + p.T)
        filtered_x[k], filtered_p[k] = x, p
    state, covariance = filtered_x.copy(), filtered_p.copy()
    if smooth:
        for k in range(n - 2, -1, -1):
            phi = _transition(days[k + 1] - days[k])
            c = filtered_p[k] @ phi.T @ np.linalg.pinv(predicted_p[k + 1])
            state[k] = filtered_x[k] + c @ (state[k + 1] - predicted_x[k + 1])
            covariance[k] = filtered_p[k] + c @ (covariance[k + 1] - predicted_p[k + 1]) @ c.T
            covariance[k] = 0.5 * (covariance[k] + covariance[k].T)
    return KalmanResult(times, measured, state, covariance, innovation, innovation_sigma, noise, smooth,
                        filtered_x[-1].copy(), filtered_p[-1].copy())


# %% ===========================================================================
#    5. THREE-CORNERED HAT
# =============================================================================
TCH_MODES = ("three", "common")


def three_cornered_hat(channels: list, names=("A", "B", "C"), mode: str = "three", signs=(1, 1, 1),
                       detrend_order: int = 2, tau0_s: float | None = None,
                       slot_tolerance: float = 0.10, grid_mode: str = "auto") -> dict:
    """Individual Allan deviations of three clocks [8] (gap-aware OADEV).

    ``channels`` are (mjd, phase_s) pairs. mode "three": the channels are the
    pairs A-B, B-C and C-A (signs do not change a variance). mode "common":
    two channels against a common clock C, signs bringing them to A-C and
    B-C; the pair A-B is derived as s1 x1 - s2 x2 (C cancels). Then
        s2_A = (s2_AB + s2_CA - s2_BC)/2,  s2_B = (s2_AB + s2_BC - s2_CA)/2,
        s2_C = (s2_BC + s2_CA - s2_AB)/2.
    A negative estimate (noise, correlation, too few terms) is reported as a
    variance and its deviation is NaN.
    """
    if mode not in TCH_MODES:
        raise ValueError(f"mode must be one of {TCH_MODES}")
    need = 3 if mode == "three" else 2
    if len(channels) < need:
        raise ValueError(f"mode '{mode}' needs {need} channels.")
    used = channels[:need]
    # Common time stamps (to 1e-7 day = 8.64 ms): every pair is formed from
    # simultaneous samples, then all channels share one grid.
    keys = [np.rint(np.asarray(c[0], float) * 1e7).astype(np.int64) for c in used]
    common = keys[0]
    for key in keys[1:]:
        common = np.intersect1d(common, key)
    if common.size < 8:
        raise ValueError("The channels share fewer than eight time stamps.")
    aligned = []
    for key, (times, phase) in zip(keys, used):
        unique, first = np.unique(key, return_index=True)
        position = first[np.searchsorted(unique, common)]
        aligned.append((np.asarray(times, float)[position], np.asarray(phase, float)[position]))
    tau0 = nominal_tau0(aligned[0][0]) if tau0_s is None else float(tau0_s)
    grid = regular_grid(aligned[0][0], aligned[0][1], tau0, slot_tolerance, mode=grid_mode)
    length = grid.n_slots
    phases = [grid.place(phase) for _, phase in aligned]
    if mode == "three":
        pairs = {f"{names[0]}-{names[1]}": phases[0], f"{names[1]}-{names[2]}": phases[1],
                 f"{names[2]}-{names[0]}": phases[2]}
    else:
        derived = signs[0] * phases[0] - signs[1] * phases[1]
        pairs = {f"{names[0]}-{names[1]}": derived, f"{names[1]}-{names[2]}": signs[1] * phases[1],
                 f"{names[2]}-{names[0]}": -signs[0] * phases[0]}
    # Detrend every pair the same way, on the common sample times.
    slot_days = np.full(length, np.nan)
    placed = grid.sample_index >= 0
    slot_days[placed] = elapsed_seconds(aligned[0][0][grid.sample_index[placed]],
                                        grid.origin_mjd) / SECONDS_PER_DAY
    for key, phase in pairs.items():
        good = np.isfinite(phase)
        if detrend_order >= 0 and good.sum() > detrend_order + 1:
            scale = max(float(slot_days[good].max()), 1e-12)
            coefficients = np.polynomial.polynomial.polyfit(slot_days[good] / scale, phase[good], detrend_order)
            pairs[key] = np.where(good, phase - np.polynomial.polynomial.polyval(slot_days / scale,
                                                                                    coefficients), np.nan)
    factors = averaging_factors(length)

    def pair_oadev(phase: np.ndarray) -> dict:
        """Gap-aware OADEV of one pair for every factor."""
        out = {}
        for m in factors:
            for name, tau, value, terms in gap_deviations(phase, int(m), tau0):
                if name == "OADEV":
                    out[float(tau)] = (value, terms)
        return out

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = dict(zip(pairs, pool.map(pair_oadev, pairs.values())))
    keys = list(pairs)
    taus = sorted(set.intersection(*(set(r) for r in results.values())))
    ab, bc, ca = keys
    table = {"tau_s": taus, "pairs": {k: {"adev": [], "terms": []} for k in keys},
             "clocks": {n: {"variance": [], "adev": []} for n in names}}
    for tau in taus:
        variance = {k: results[k][tau][0] ** 2 for k in keys}
        for k in keys:
            table["pairs"][k]["adev"].append(results[k][tau][0])
            table["pairs"][k]["terms"].append(results[k][tau][1])
        individual = {names[0]: 0.5 * (variance[ab] + variance[ca] - variance[bc]),
                      names[1]: 0.5 * (variance[ab] + variance[bc] - variance[ca]),
                      names[2]: 0.5 * (variance[bc] + variance[ca] - variance[ab])}
        for n in names:
            table["clocks"][n]["variance"].append(individual[n])
            table["clocks"][n]["adev"].append(math.sqrt(individual[n]) if individual[n] > 0 else float("nan"))
    negative = {n: int(sum(v <= 0 for v in table["clocks"][n]["variance"])) for n in names}
    return {"method": "three-cornered hat, gap-aware OADEV (Gray and Allan 1974)", "mode": mode,
            "names": list(names), "signs": list(signs), "detrend_order": detrend_order, "tau0_s": tau0,
            "grid": grid.summary(), "common_samples": int(common.size),
            "non_positive_variances": negative, **table,
            "notes": ["Assumes the three clocks are uncorrelated; a negative variance means the "
                      "assumption or the averaging is insufficient at that tau.",
                      *(["Pair A-B is derived (common-clock mode): measurement noise of both channels adds."]
                        if mode == "common" else [])]}


# %% ===========================================================================
#    6. RUNNERS SHARED BY THE GUI TABS AND THE BATCH COMMAND
# =============================================================================
# Each runner returns the result dict that gui_evaluation shows and that
# maser_export writes (pages, CSV, JSON, review.md). The base fit is read,
# never changed.
def run_gap_stability(mjd, phase_s, channel: str, source: str = "selected samples (base fit)",
                      detrend_order: int = -1, tau0_s: float | None = None, slot_tolerance: float = 0.10,
                      level: float = ONE_SIGMA, grid_mode: str = "auto") -> dict:
    """gap_stability() plus the channel and data-source labels."""
    result = gap_stability(mjd, phase_s, detrend_order, tau0_s, slot_tolerance, level, grid_mode)
    result["source"] = source
    result["channel"] = channel
    return result


def run_steering(series: TimingSeries, fit: PhaseFit, hmd_frame, sign_multiplier: int, low_mjd: float,
                 high_mjd: float, apply_mjd: float, maser: str = "MHM2010", bridge_gaps: bool = True,
                 mode: str = "all", threshold_counts: float = 50.0, column: str = DEFAULT_DDS_COLUMN) -> dict:
    """steering_removal() of the whole channel and, when the HMD covers it,
    the base fit versus the same fit of the steering-removed record."""
    steering = steering_removal(series.mjd, series.phase_s, hmd_frame, sign_multiplier, mode, threshold_counts,
                                column)
    if not steering.covered.any():
        raise ValueError(f"The HMD record (MJD {float(steering.hmd_mjd[0])!r} to "
                         f"{float(steering.hmd_mjd[-1])!r}) does not overlap the timing data; open the HMD "
                         "of the same period.")
    comparison = steering_fit_comparison(series, fit, steering, low_mjd, high_mjd, apply_mjd, sign_multiplier,
                                         maser, bridge_gaps)
    return {"steering": steering, "comparison": comparison, "channel": series.label}


def steering_removed_window(steering_result: dict, low_mjd: float, high_mjd: float) -> tuple:
    """(mjd, phase) of the steering-removed record inside the fit interval."""
    steering = steering_result["steering"]
    keep = (steering.covered & np.isfinite(steering.removed_phase_s) & (steering.mjd >= low_mjd)
            & (steering.mjd <= high_mjd))
    return steering.mjd[keep], steering.removed_phase_s[keep]


def run_kalman(mjd, phase_s, fit: PhaseFit, channel: str, apply_mjd: float, sign_multiplier: int = 1,
               maser: str = "MHM2010", eligible: bool = False, suggestion_kwargs: dict | None = None,
               noise: KalmanNoise | None = None, smooth: bool = True) -> dict:
    """kalman_clock() of the fitted samples, its estimate at the epoch, the
    base least-squares values beside it and (eligible pairs only) settings
    from the Kalman estimate, marked EVALUATION ONLY."""
    mjd = np.asarray(mjd, float)
    if noise is None:
        noise = kalman_noise_from_hadamard(gap_stability(mjd, phase_s, -1))
    result = kalman_clock(mjd, phase_s, noise, smooth)
    estimate = result.estimate(apply_mjd, sign_multiplier)
    base_y, base_d, base_sy, base_sd = fit.frequency(apply_mjd)
    settings, settings_error = None, ""
    if eligible:
        try:
            settings = result.suggestion(apply_mjd, maser, sign_multiplier, **(suggestion_kwargs or {}))
        except Exception as error:                   # reported beside the estimate
            settings_error = str(error)
    else:
        settings_error = "Not an eligible maser/reference pair: no settings (as in the base)."
    return {"kalman": result, "estimate": estimate, "channel": channel,
            "base": {"fractional_frequency": sign_multiplier * base_y, "sigma": base_sy,
                     "drift_per_day": sign_multiplier * base_d if fit.order == 2 else None,
                     "drift_sigma": base_sd},
            "settings": settings, "settings_error": settings_error, "sign": sign_multiplier,
            "base_fit": fit, "base_mjd": mjd}


def run_three_cornered_hat(series_by_name: dict, chosen: list, names=("A", "B", "C"), mode: str = "three",
                           signs=(1, 1, 1), detrend_order: int = 2) -> dict:
    """three_cornered_hat() of the chosen channels of a timing collection."""
    need = 3 if mode == "three" else 2
    chosen = list(chosen)[:need]
    if len(chosen) != need or len(set(chosen)) != need or any(name not in series_by_name for name in chosen):
        raise ValueError(f"Choose {need} different, loaded channels.")
    if len(set(names)) != 3:
        raise ValueError("The three clock names must differ.")
    series = [series_by_name[name] for name in chosen]
    result = three_cornered_hat([(s.mjd, s.phase_s) for s in series], tuple(names), mode, tuple(signs),
                                detrend_order)
    result["channels"] = chosen
    return result
