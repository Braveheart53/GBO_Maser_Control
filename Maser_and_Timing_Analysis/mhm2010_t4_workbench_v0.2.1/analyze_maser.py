#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# %% ===========================================================================
#    PROGRAM HEADER
# =============================================================================
"""Reproducible command-line MHM2010 / T4 maser timing analysis.

===============================================================================
 Title          : MHM2010 / T4 Batch Analysis
 Filename       : analyze_maser.py
 Version        : 0.2.1 (Semantic Versioning 2.0.0)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-03
 Modified       : 2026-11-05
 Language       : Python 3.12
 Licence        : GPL-3.0-or-later; see LICENSE
 Revision       : 0.1.0 FITS directory analysis with explicit historical settings.
                  0.2.0 FITS and/or Franks t00new input; --maser T4 with
                        SETFSREL/DRIFTCOMP suggestions; optional T4 telemetry;
                        --bridge-gaps; reference 1 PPS figures (titles and
                        labels selectable) first in analysis.vszh5, with MJD
                        and local ISO date-time pages (writer 0.0.24).
                  0.2.1 --t4-telemetry also takes the T4's own monitoring
                        files (*.dat from FTP ./Monitoring), routed by
                        content; --t4-date-order, --t4-earliest-valid.
===============================================================================
No current drift default is assumed. Without settings only measurement results
are exported. --derive-dds copies a historical HMD value within 120 s, not a
present-day instrument state. Nothing is ever sent to a maser. Consult the PDF
manual before use.

Examples (one line each):
  python analyze_maser.py --timing-directory FRANKS_DIR --channel MSM-GPS --order 1
      --bridge-gaps --last-days 30.9 --dds 5751.6770000 --output NEW_DIR
  python analyze_maser.py --timing-directory FITS_DIR --maser T4 --channel "Site1Hz -> T4CHAN"
      --t4-synth 405750.927598720 --t4-driftcomp +0.0e+00 --output NEW_DIR
  python analyze_maser.py --timing-directory FRANKS_DIR --channel MSM-GPS --bridge-gaps
      --t4-telemetry MonitHCB.dat --output NEW_DIR
  python t4_native.py MonitHCB.dat --output NEW_DIR      (monitoring files only)
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
from astropy.time import Time
from maser_io import load_timing, load_hmd, load_t4_files, FITS_SUFFIXES
from maser_core import fit_phase, correction, stability, t4_settings
from maser_plots import reference_set
from maser_export import export_bundle, json_safe

__version__ = "0.2.1"
MHM_SIGNS = {"MicroSem -> CNS_GPS1": 1, "MicroSem -> CNS2018": 1, "MicroSem -> CNS_GPS2": 1,
             "CNS_GPS1 -> MicroSem": -1, "CNS2018 -> MicroSem": -1, "CNS_GPS2 -> MicroSem": -1, "MSM-GPS": 1}


# %% COMMAND LINE
def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--timing-directory", "--fits-directory", dest="timing_directory", required=True,
                        type=Path, help="Folder with FITS/FITS.GZ and/or .t00new files (searched recursively)")
    parser.add_argument("--maser", choices=["MHM2010", "T4"], default="MHM2010")
    parser.add_argument("--hmd", type=Path, help="MHM2010 HMD telemetry")
    parser.add_argument("--t4-telemetry", type=Path, nargs="*", default=[],
                        help="T4 recorder CSV, text logs and/or T4 monitoring files (*.dat, FTP ./Monitoring)")
    parser.add_argument("--t4-date-order", choices=["DMY", "MDY", "YMD"], default="DMY",
                        help="TIME field order in T4 monitoring files (DMY verified on the GBO file)")
    parser.add_argument("--t4-earliest-valid", default="2020-01-01",
                        help="T4 monitoring records stamped before this UTC date are 'clock not set'")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--channel", default="MicroSem -> CNS_GPS1")
    parser.add_argument("--start", help="UTC ISO date or datetime")
    parser.add_argument("--end", help="UTC ISO date or datetime, exclusive")
    parser.add_argument("--last-days", type=float, help="Fit the last N days of the channel instead of --start")
    parser.add_argument("--order", type=int, choices=[1, 2], default=2, help="1 linear, 2 quadratic phase")
    parser.add_argument("--bridge-gaps", action="store_true", help="Allow one fit across acquisition gaps")
    parser.add_argument("--dds", help="MHM2010 DDS setting at application epoch")
    parser.add_argument("--derive-dds", action="store_true")
    parser.add_argument("--drift", type=float, help="Known current MHM2010 drift; no default")
    parser.add_argument("--drift-units", choices=["pp18/day", "DDS counts/day"], default="pp18/day")
    parser.add_argument("--pp18-sign", choices=["output", "dds"], default="output")
    parser.add_argument("--phase-sign", type=int, choices=[-1, 1], default=1)
    parser.add_argument("--t4-synth", type=float, help="Current T4 synthesizer frequency (RDFS), Hz")
    parser.add_argument("--t4-driftcomp", type=float, help="Current T4 DRIFTCOMP (RDREG=DRIFTCOMP)")
    parser.add_argument("--setfsrel-convention", choices=["output", "phase"], default="output")
    parser.add_argument("--title", default="", help="Reference deltas figure title")
    parser.add_argument("--ylabel", default="", help="Reference deltas y label")
    parser.add_argument("--fit-ylabel", default="", help="Reference fit y label")
    parser.add_argument("--sampling-tolerance", type=float, default=.01)
    parser.add_argument("--stable32", action="store_true")
    arguments = parser.parse_args()
    files = sorted(path for path in arguments.timing_directory.rglob("*")
                   if path.is_file() and path.name.lower().endswith(FITS_SUFFIXES + (".t00new",)))
    if not files:
        parser.error("No FITS or t00new files in the selected directory.")
    collection = load_timing(files)
    if arguments.channel not in collection.series:
        parser.error(f"Channel {arguments.channel!r} not found. Available: {', '.join(sorted(collection.series))}")
    series = collection.series[arguments.channel]
    high = float(Time(arguments.end, scale="utc").mjd)-1e-9 if arguments.end else series.mjd[-1]
    if arguments.last_days:
        low = high - arguments.last_days
    else:
        low = float(Time(arguments.start, scale="utc").mjd) if arguments.start else series.mjd[0]
    fit = fit_phase(series, low, high, order=arguments.order, maser=arguments.maser,
                    bridge_gaps=arguments.bridge_gaps)
    application_epoch = float(series.mjd[fit.used][-1])
    telemetry = load_hmd([arguments.hmd]) if arguments.hmd else None
    t4_files = (load_t4_files(arguments.t4_telemetry, date_order=arguments.t4_date_order,
                              earliest_valid=arguments.t4_earliest_valid) if arguments.t4_telemetry else None)
    t4 = t4_files.monit if t4_files else None
    t4_native = t4_files.native if t4_files else []
    result = None
    if arguments.maser == "MHM2010":
        eligible = arguments.channel in MHM_SIGNS and arguments.phase_sign == MHM_SIGNS[arguments.channel]
        dds = arguments.dds
        if arguments.derive_dds:
            if telemetry is None:
                parser.error("--derive-dds requires --hmd.")
            frame = telemetry.frame
            index = int(np.argmin(abs(frame.MJD-application_epoch)))
            if abs(frame.MJD.iloc[index]-application_epoch)*86400 > 120:
                parser.error("No HMD DDS value within 120 s.")
            dds = f"{frame.ExtSynth_32.iloc[index]:.7f}"
        if dds is not None and (arguments.drift is not None or arguments.order == 1):
            if not eligible:
                parser.error("Correction requires an identified MicroSem/GPS pair and its matching phase sign.")
            result = correction(fit, dds, arguments.drift or 0.0, application_epoch, arguments.drift_units,
                                arguments.pp18_sign, sign_multiplier=arguments.phase_sign)
    else:
        eligible = True                      # the operator designates the T4 channel with --channel
        dds = None
        result = t4_settings(fit, application_epoch, arguments.phase_sign, arguments.t4_synth,
                             arguments.t4_driftcomp, arguments.setfsrel_convention)
    if result is not None:
        result["operator_confirmed"] = False
        result["operating_state_reviewed"] = False
        result["status"] = ("HISTORICAL / UNREVIEWED. Settings and signs are explicit CLI assumptions, "
                            "not current recommendations. Nothing was sent to a maser.")
    reference = reference_set(series.mjd, series.phase_s, fit.selected, fit.used, arguments.maser, arguments.channel,
                              eligible, float(dds) if dds else None, arguments.phase_sign,
                              {"title": arguments.title, "ylabel": arguments.ylabel,
                               "fit_ylabel": arguments.fit_ylabel}, arguments.setfsrel_convention)
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
        try:
            trial = fit_phase(series, start, application_epoch, maser=arguments.maser,
                              bridge_gaps=arguments.bridge_gaps)
        except ValueError as error:
            sensitivity.append({"window_days": days, "error": str(error)})
            continue
        frequency, drift, sigma_y, sigma_d = trial.frequency(application_epoch)
        sensitivity.append({"window_days": days, "samples": int(trial.used.sum()),
                            "fractional_frequency_at_end": frequency,
                            "drift_pp18_per_day": drift*1e18, "rms_ns": trial.rms_ns})
    settings = {"maser": arguments.maser, "channel": arguments.channel, "start_mjd": low, "end_mjd": high,
                "order": arguments.order, "bridge_gaps": arguments.bridge_gaps,
                "sampling_tolerance": arguments.sampling_tolerance, "phase_sign": arguments.phase_sign,
                "pp18_sign": arguments.pp18_sign, "setfsrel_convention": arguments.setfsrel_convention,
                "fit_sensitivity": sensitivity, "current_state_is_explicit_cli_input": True}
    extra = (arguments.channel.split(" -> ")[-1],) if arguments.maser == "T4" else ()
    export_bundle(arguments.output, series, fit, result, telemetry if arguments.maser == "MHM2010" else None,
                  stability_result, settings, arguments.stable32, arguments.sampling_tolerance, collection,
                  arguments.maser, reference, t4, extra, t4_native)
    print(json.dumps({"application_epoch_utc": Time(application_epoch, format="mjd").isot,
                      "fit_count": int(fit.used.sum()), "coefficients_ns": fit.coefficients_ns,
                      "reference_fit": reference.as_dict()["fit"], "settings_candidate": result,
                      "sensitivity": sensitivity}, indent=2, default=json_safe))


if __name__ == "__main__":
    main()
