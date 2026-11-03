#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# %% ===========================================================================
#    PROGRAM HEADER
# =============================================================================
"""Reproducible command-line MHM2010 analysis.

===============================================================================
 Title          : MHM2010 Batch Analysis
 Filename       : analyze_maser.py
 Version        : 0.1.0 (Semantic Versioning 2.0.0)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-03
 Modified       : 2026-11-03
 Language       : Python 3.12
 Licence        : GPL-3.0-or-later; see LICENSE
 Revision       : 0.1.0 FITS directory analysis with explicit historical settings.
===============================================================================
No current drift default is assumed. Without --dds and --drift only measurement
results are exported. --derive-dds copies a historical HMD value within 120 s,
not a present-day instrument state. Consult the PDF manual before use.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
from astropy.time import Time
from maser_io import load_fits, load_hmd
from maser_core import fit_phase, correction, stability
from maser_export import export_bundle, json_safe

__version__ = "0.1.0"


# %% COMMAND LINE
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fits-directory", required=True, type=Path)
    parser.add_argument("--hmd", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--channel", default="MicroSem -> CNS_GPS1")
    parser.add_argument("--start", help="UTC ISO date or datetime")
    parser.add_argument("--end", help="UTC ISO date or datetime, exclusive")
    parser.add_argument("--dds", help="DDS setting at application epoch")
    parser.add_argument("--derive-dds", action="store_true")
    parser.add_argument("--drift", type=float, help="Known current drift; no default")
    parser.add_argument("--drift-units", choices=["pp18/day", "DDS counts/day"], default="pp18/day")
    parser.add_argument("--pp18-sign", choices=["output", "dds"], default="output")
    parser.add_argument("--phase-sign", type=int, choices=[-1, 1], default=1)
    parser.add_argument("--sampling-tolerance", type=float, default=.01)
    parser.add_argument("--stable32", action="store_true")
    arguments = parser.parse_args()
    files = sorted(path for path in arguments.fits_directory.rglob("*")
                   if path.is_file() and path.name.lower().endswith((".fits", ".fits.gz")))
    if not files:
        parser.error("No FITS files in the selected directory.")
    collection = load_fits(files)
    series = collection.series[arguments.channel]
    low = float(Time(arguments.start, scale="utc").mjd) if arguments.start else series.mjd[0]
    high = float(Time(arguments.end, scale="utc").mjd)-1e-9 if arguments.end else series.mjd[-1]
    fit = fit_phase(series, low, high)
    application_epoch = float(series.mjd[fit.used][-1])
    telemetry = load_hmd([arguments.hmd]) if arguments.hmd else None
    dds = arguments.dds
    if arguments.derive_dds:
        if telemetry is None:
            parser.error("--derive-dds requires --hmd.")
        frame = telemetry.frame
        index = int(np.argmin(abs(frame.MJD-application_epoch)))
        if abs(frame.MJD.iloc[index]-application_epoch)*86400 > 120:
            parser.error("No HMD DDS value within 120 s.")
        dds = f"{frame.ExtSynth_32.iloc[index]:.7f}"
    result = None
    if dds is not None and arguments.drift is not None:
        eligible = {"MicroSem -> CNS_GPS1": 1, "MicroSem -> CNS2018": 1,
                    "MicroSem -> CNS_GPS2": 1, "CNS_GPS1 -> MicroSem": -1,
                    "CNS2018 -> MicroSem": -1, "CNS_GPS2 -> MicroSem": -1}
        if arguments.channel not in eligible or arguments.phase_sign != eligible[arguments.channel]:
            parser.error("Correction requires an identified MicroSem/GPS pair and its matching phase sign.")
        result = correction(fit, dds, arguments.drift, application_epoch,
                            arguments.drift_units, arguments.pp18_sign,
                            sign_multiplier=arguments.phase_sign)
        result["operator_confirmed"] = False
        result["operating_state_reviewed"] = False
        result["status"] = "HISTORICAL / UNREVIEWED. Settings and signs are explicit CLI assumptions, not current recommendations."
    try:
        stability_result = stability(series.mjd[fit.used], series.phase_s[fit.used],
                                     tolerance=arguments.sampling_tolerance)
    except ValueError as error:
        if arguments.stable32:
            raise
        stability_result = None
        print("Stability not calculated:", error)
    sensitivity = []
    for days in (30, 21, 14, 7):
        start = max(low, application_epoch-days)
        trial = fit_phase(series, start, application_epoch)
        frequency, drift, sigma_y, sigma_d = trial.frequency(application_epoch)
        sensitivity.append({"window_days": days, "samples": int(trial.used.sum()),
                            "fractional_frequency_at_end": frequency,
                            "drift_pp18_per_day": drift*1e18, "rms_ns": trial.rms_ns})
    settings = {"start_mjd": low, "end_mjd": high, "sampling_tolerance": arguments.sampling_tolerance,
                "phase_sign": arguments.phase_sign, "pp18_sign": arguments.pp18_sign,
                "fit_sensitivity": sensitivity, "current_drift_is_explicit_cli_input": True}
    export_bundle(arguments.output, series, fit, result, telemetry, stability_result,
                  settings, arguments.stable32, arguments.sampling_tolerance, collection)
    print(json.dumps({"application_epoch_utc": Time(application_epoch, format="mjd").isot,
                      "fit_count": int(fit.used.sum()), "coefficients_ns": fit.coefficients_ns,
                      "correction": result, "sensitivity": sensitivity}, indent=2, default=json_safe))


if __name__ == "__main__":
    main()
