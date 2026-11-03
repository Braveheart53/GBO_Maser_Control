#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Auditable exports for MHM2010 Correction Workbench.

===============================================================================
 Title          : Maser Results and Stable32 Export
 Filename       : maser_export.py
 Version        : 0.1.0 (Semantic Versioning 2.0.0)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-03
 Modified       : 2026-11-03
 Language       : Python 3.12
 Requires       : numpy, h5py, bundled vszh5_writer
 Licence        : GPL-3.0-or-later; see LICENSE
 Revision       : 0.1.0 Atomic results directory, both drift units, native HDF5.
===============================================================================
Stable32 format follows W. J. Riley, Stable32 User Manual, ver. 1.54,
2008, http://www.stable32.com/Manual154.pdf .
"""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import json
import shutil
import tempfile
import numpy as np
from maser_core import PhaseFit, uniform_samples
from maser_io import TimingSeries, Telemetry, TimingCollection, overview_series
from vszh5_writer import VeuszWriter, VszDoc, Trace, xy_page, grid_page

__version__ = "0.1.0"


# %% ===========================================================================
#    SERIALIZATION AND STABLE32
# =============================================================================
def json_safe(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(type(value).__name__)


def write_stable32(directory: Path, mjd, phase_s, prefix="raw", tolerance=0.01):
    """Export only complete uniform samples. Real zeros become 1e-99.

    Single-column PHD/FRD avoids automatic tag-unit ambiguity. A separate
    MJD+phase CSV retains original timestamps. Frequency is averaged over
    adjacent phase intervals, so there is one fewer frequency point.
    """
    phase, tau0, error = uniform_samples(mjd, phase_s, tolerance)
    frequency = np.diff(phase)/tau0
    metadata = {"tau0_s": tau0, "grid_error_s": error, "phase_count": len(phase),
                "frequency_count": len(frequency), "timetags": "separate CSV",
                "zero_policy": "Actual zero replaced by 1e-99, never interpreted as missing.",
                "units": {"PHD": "phase seconds", "FRD": "dimensionless fractional frequency"}}
    for extension, kind, values in (("PHD", "Phase", phase), ("FRD", "Frequency", frequency)):
        data = values.copy()
        metadata[f"{extension}_zero_replacements"] = int(np.sum(data == 0))
        data[data == 0] = 1e-99
        target = directory / f"{prefix}.{extension}"
        with target.open("w", encoding="ascii", newline="") as handle:
            handle.write(f"File: {target.name}\r\nType: {kind}\r\n"
                         f"Points: {len(data)}\r\nTau: {tau0:.15e}\r\n")
            for value in data:
                handle.write(f"{value:.16e}\r\n")
    (directory/f"{prefix}_stable32.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    np.savetxt(directory/f"{prefix}_timetags.csv", np.c_[mjd, phase], delimiter=",",
               header="mjd_utc,phase_s", comments="", fmt=["%.12f", "%.16e"])
    return metadata


# %% ===========================================================================
#    COMPLETE ANALYSIS BUNDLE
# =============================================================================
def export_bundle(destination: str | Path, series: TimingSeries, fit: PhaseFit,
                  correction_result=None, telemetry: Telemetry | None=None,
                  stability_result=None, settings=None, stable32=False,
                  stable32_tolerance=0.01, collection: TimingCollection | None=None):
    """Write a NEW results directory or fail without replacing existing data."""
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError("Choose a new results directory; existing results are never overwritten.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".mhm-export-", dir=destination.parent))
    try:
        payload = {
            "application": "MHM2010 Correction Workbench", "version": __version__,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "series": {"label": series.label, "kind": series.source_kind,
                       "provenance": series.provenance, "notes": series.notes},
            "fit": {key: value for key, value in asdict(fit).items()
                    if key not in {"selected", "used", "residual_s", "predicted_s"}},
            "selected_count": int(fit.selected.sum()), "fit_count": int(fit.used.sum()),
            "correction": correction_result, "settings": settings or {},
            "stability": stability_result,
            "telemetry": None if telemetry is None else {
                "provenance": telemetry.provenance, "notes": telemetry.notes,
                "first_mjd": float(telemetry.frame.MJD.min()),
                "last_mjd": float(telemetry.frame.MJD.max())},
        }
        np.savetxt(temporary/"phase_and_fit.csv",
                   np.c_[series.mjd, series.phase_s, fit.predicted_s, fit.residual_s,
                         fit.selected.astype(int), fit.used.astype(int)], delimiter=",",
                   header="mjd_utc,phase_s,model_s,residual_s,selected,used_in_fit",
                   comments="", fmt=["%.12f", "%.16e", "%.16e", "%.16e", "%d", "%d"])
        if stable32:
            # Do not compress excluded samples together: the validator will
            # reject an internal hole rather than change the sampling interval.
            selected = fit.used
            payload["stable32_raw"] = write_stable32(temporary, series.mjd[selected],
                series.phase_s[selected], "raw", stable32_tolerance)
            payload["stable32_residual"] = write_stable32(temporary, series.mjd[selected],
                fit.residual_s[selected], "fit_residual", stable32_tolerance)
        (temporary/"analysis.json").write_text(
            json.dumps(payload, indent=2, default=json_safe, allow_nan=False), encoding="utf-8")
        if stability_result:
            with (temporary/"stability.csv").open("w", encoding="utf-8") as handle:
                handle.write("estimator,tau_s,deviation,overlapping_terms\n")
                for name, result in stability_result["estimators"].items():
                    for tau, value, count in zip(result["tau_s"], result["deviation"], result["terms"]):
                        handle.write(f"{name},{tau:.12g},{value:.16e},{count}\n")
        document = VszDoc(generator=f"MHM2010 Correction Workbench {__version__}")
        with VeuszWriter(temporary/"analysis.vszh5") as writer:
            writer.dataset_1d("mjd_utc", series.mjd)
            writer.dataset_date("utc", (series.mjd-54832.0)*86400.0)
            writer.dataset_1d("phase_ns", series.phase_s*1e9)
            # Model/residual are only drawn inside the chosen interval.
            writer.dataset_1d("fit_ns", np.where(fit.selected, fit.predicted_s*1e9, np.nan))
            writer.dataset_1d("residual_ns", np.where(fit.selected, fit.residual_s*1e9, np.nan))
            writer.dataset_1d("fit_mask", fit.used.astype(float))
            writer.dataset_text("analysis_metadata", [json.dumps(payload, default=json_safe)])
            if collection is not None:
                # Retain all raw channel pairs, not only the selected fit.
                for number, (name, item) in enumerate(collection.series.items()):
                    prefix = f"raw_pair_{number:02d}"
                    writer.dataset_1d(prefix+"_mjd", item.mjd, tags=("raw",))
                    writer.dataset_1d(prefix+"_phase_ns", item.phase_s*1e9, tags=("raw",))
                    writer.dataset_text(prefix+"_label", [name])
                panels = []
                for number, (name, item) in enumerate(overview_series(collection).items()):
                    prefix = f"overview_{number:02d}"
                    writer.dataset_1d(prefix+"_mjd", item.mjd)
                    writer.dataset_1d(prefix+"_utc", (item.mjd-54832.0)*86400)
                    writer.dataset_1d(prefix+"_phase_ns", item.phase_s*1e9)
                    writer.dataset_text(prefix+"_label", [name])
                    xy_page(document, prefix+"_mjd_page",
                            [Trace(prefix+"_mjd", prefix+"_phase_ns", name, marker="circle", line=False)],
                            title=name+"; hourly medians" if item.source_kind != "Franks" else name,
                            xlabel="MJD (UTC)", ylabel="Time difference (ns)")
                    xy_page(document, prefix+"_utc_page",
                            [Trace(prefix+"_utc", prefix+"_phase_ns", name, marker="circle", line=False)],
                            title=name, xlabel="UTC", ylabel="Time difference (ns)", xmode="datetime",
                            datetime_format="%VDY-%VDm-%VDd")
            xy_page(document, "timing_fit",
                    [Trace("mjd_utc", "phase_ns", "Measured", marker="circle", line=False),
                     Trace("mjd_utc", "fit_ns", "Selected fit")],
                    title=f"{series.label}: phase and fit", xlabel="MJD (UTC)", ylabel="Clock time error (ns)")
            xy_page(document, "fit_residual", [Trace("mjd_utc", "residual_ns", "Residual")],
                    title="Residual on selected interval", xlabel="MJD (UTC)", ylabel="Residual (ns)")
            if stability_result:
                fractional_traces = []
                for name, result in stability_result["estimators"].items():
                    if not result["tau_s"]:
                        continue
                    writer.dataset_1d(name+"_tau", result["tau_s"])
                    writer.dataset_1d(name+"_value", result["deviation"])
                    trace = Trace(name+"_tau", name+"_value", name, marker="circle")
                    if name == "TDEV":
                        xy_page(document, "time_deviation", [trace], title="TDEV: comparison-system stability",
                                xlabel="Averaging time (s)", ylabel="TDEV (s)", xlog=True, ylog=True)
                    else:
                        fractional_traces.append(trace)
                if fractional_traces:
                    xy_page(document, "frequency_stability", fractional_traces,
                            title="Comparison-system stability (not isolated maser noise)",
                            xlabel="Averaging time (s)", ylabel="Fractional frequency deviation",
                            xlog=True, ylog=True)
            if telemetry is not None:
                overlap = ((telemetry.frame.MJD >= series.mjd[fit.selected].min()) &
                           (telemetry.frame.MJD <= series.mjd[fit.selected].max()))
                context = telemetry.frame.loc[overlap]
                if len(context):
                    writer.dataset_1d("telemetry_mjd", context.MJD)
                    writer.dataset_1d("telemetry_dds", context.ExtSynth_32)
                    xy_page(document, "telemetry_dds", [Trace("telemetry_mjd", "telemetry_dds", "DDS")],
                            title="Overlapping HMD external synthesizer", xlabel="MJD (UTC)",
                            ylabel="External DDS setting")
                    context.to_csv(temporary/"telemetry_overlap.csv", index=False)
            writer.finish(document.text())
        report = [
            "# MHM2010 Analysis Review", "", "Version 0.1.0. Offline advisory output; no instrument commands were sent.",
            "Use analysis.json for complete precision, assumptions and source hashes.", "",
            f"Channel: {series.label}. Input: {series.source_kind}.",
            f"Fit: order {fit.order}; {int(fit.used.sum())} samples; RMS {fit.rms_ns:.5g} ns.",
            f"Reference epoch: MJD {fit.epoch_mjd:.12f} UTC.", "",
            "## Warnings", *["- "+warning for warning in fit.warnings], "",
            "## Correction candidate", "```json",
            json.dumps(correction_result, indent=2, default=json_safe) if correction_result else
            "No correction candidate: current settings and signs must be supplied.",
            "```", "", "## Stable32", 
            "If present, import raw.PHD as phase seconds or raw.FRD as fractional frequency. "
            "Set tau to the value in the sidecar. Fit-residual files are separately labeled. "
            "No gaps were filled; actual zeros are represented by 1e-99.", "",
            "## References", "",
            "| Ref. | IEEE bibliographic entry | URL |", "|---|---|---|",
            "| [1] | Microsemi, MHM-2010 User’s Guide, 75666-201, rev. J, Oct. 2014. Accessed Nov. 3, 2026. | https://www.gb.nrao.edu/~fghigo/timer/MSMmaser/Maser_UG_75666-201_Rev_J.PDF |",
            "| [2] | W. J. Riley, Handbook of Frequency Stability Analysis, NIST SP 1065, Jul. 2008. | https://tf.nist.gov/general/pdf/2220.pdf |",
            "| [3] | W. J. Riley, Stable32 User Manual, ver. 1.54, 2008. Accessed Nov. 3, 2026. | http://www.stable32.com/Manual154.pdf |",
            "", "Equations and sign derivations: see the supplied bookmarked user manual, Appendix A."]
        (temporary/"review.md").write_text("\n".join(report)+"\n", encoding="utf-8")
        temporary.rename(destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination
