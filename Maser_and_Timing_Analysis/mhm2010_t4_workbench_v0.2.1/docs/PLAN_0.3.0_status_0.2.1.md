---
title: MHM2010 / T4 Maser Timing Workbench — plan 0.3.0 (telemetry, stability, Stable32 for every input)
author: William W. Wallace (NRAO/GBO), with Claude
date: 2026-11-03
status: STAGED; P7 partly done in 0.2.1 (2026-11-05), see Section 0
builds_on: MHM2010_T4_Workbench_v0.2.0.7z (SHA-256 a460c9a5472ecfb2439c5a1a0f831b76146100cb890146a622b46555bfc67fd8), writer 0.0.24
---

# Plan 0.3.0: telemetry, timing stability and Stable32 for every input

## 0. Status after release 0.2.1 (2026-11-05)

| Item | Status after 0.2.1 |
|---|---|
| P7 T4 native log loader | **Partly done.** `t4_native.py` 0.0.1 reads the GBO `MonitHCB.dat` (HCB option: tab-separated, `DD/MM/YY HH:MM:SS` UTC, clock-not-set records flagged). The main 40-channel monitoring file has not been seen; the reader is header-driven but untested on it. |
| P6 Telemetry with timing | Not started. The monitoring files carry the maser's own UTC stamps, so they can be aligned with timing data directly (no host receive-time offset). The iM165 test report gives 2.07e-16/°C inside the HCB, a scale for the expected coupling. |
| P8 GUI and batch | Monitoring-file tab and `--t4-telemetry *.dat` done; stability and correlation options not started. |
| New: P10 local UDP port | Done in 0.2.1 for the GUI and `t4_monitor.py` command line (14000 for a real T4). Confirm on the T4 with read-only queries from `LOCAL_PORT = 0`. |
| P1–P5, P9 | Not started. |


## 1. What 0.2.0 can and cannot do today (measured 2026-11-03 on your files)

The evidence is in `capability_check_results_0.2.0.json`; `tools/capability_check.py` re-runs it.

| Capability | 0.2.0 status | Evidence | Gap |
|---|---|---|---|
| Import MHM2010 HMD | **Works** | Your `MHM2010.HMD`: 362,987 rows × 37 columns, MJD 60774.57 to 61124.97 (2025-04-09 to 2026-03-26), 60 s cadence, 1.7 to 5.4 s, about 455 MB peak RAM | Whole file held in RAM; no column subset or cache |
| Use HMD in the analysis | Context only: DDS/selector events, overlap CSV, DDS page (MJD + local) | — | No correlation of telemetry with timing residuals; no telemetry stability |
| Import T4 telemetry | **Works** for the workbench recorder (`t4_raw_frames.csv`, `t4_telemetry.csv`) and `<time> $<frame>` text logs | 29 T4 tests | **The T4's own log files (RECMONIT, FTP `./Monitoring`) cannot be read**: T4S-MAN-0012 and -0015 do not document their format |
| 1 PPS deltas → fit, reference figures, MHM2010 and T4 settings | **Works** for FITS and Franks (Franks with gap bridging) | July 2026 regression: N 644, a1 −2.268 ± 0.019 ns/day, −373 counts | T4 channel identity unknown |
| Stability (OADEV, MDEV, TDEV, OHDEV), FITS | **Works** at 2 % sampling tolerance | Oct–Nov 2025: 9,600 samples, τ0 = 270 s | Default tolerance 1 % rejects the real month (≈3 s grid error) |
| Stable32 PHD/FRD, FITS | **Works** (same condition) | raw/fit_residual PHD, FRD, sidecars written | — |
| Stability and Stable32, **Franks** | **Fails** | Inside one "continuous" run, intervals range 4.5 to 244 min (median 58.5); 9 gaps of 5 to 99 h in 2026 | No regular grid and no gap handling |
| Stability and Stable32 when the fit **bridges gaps** (any input) | **Fails** | same rule | as above |
| Batch (`analyze_maser.py`) for all of the above | Works where the GUI works | — | No grid/gap options; telemetry is context only |

## 2. Work items for 0.3.0

| # | Item | Design | Acceptance test |
|---|---|---|---|
| P1 | **Regular grid with gap markers** (no interpolation) | Nominal τ0 (FITS 270 s, auto from the median interval; Franks 3600 s). Assign each sample to the nearest slot if \|t − slot\| ≤ ε·τ0 (operator ε, default 0.05 FITS, 0.15 Franks). Empty slots are gaps; duplicate slots are rejected. Report slot-error statistics. | Gap-free FITS gives exactly the 0.2.0 numbers; synthetic data with known gaps match a brute-force loop |
| P2 | **Gap-aware estimators** | OADEV/MDEV/TDEV/OHDEV use only terms whose samples all exist; record term counts per τ. Phase-to-frequency only across adjacent present slots. NIST SP 1065 [2] | Matches AllanTools on gap-free data; matches a loop reference on gapped data |
| P3 | **Stable32 with gaps** | Regular-grid PHD/FRD with **0 = gap**, the Stable32 convention already used in 0.1.0 (actual zeros written as 1e-99) [3]; sidecar lists τ0, slots, gaps and slot errors | Gap count in file = gap count in sidecar; manual Stable32 import on Windows (your check) |
| P4 | **Default tolerances** | FITS stability default 2 % (documented reason); Franks uses P1 | Real FITS month passes by default |
| P5 | **HMD memory** | `usecols`/dtype map, chunked read, optional cache (pickle/parquet) beside the source; "Flush telemetry RAM" | Peak RAM under 200 MB on your HMD; same values as 0.2.0 |
| P6 | **Telemetry with timing** (MHM2010 HMD, T4 MONIT) | Resample telemetry onto the timing grid (mean per slot, gaps kept); overlay pages (MJD + local); correlation and linear sensitivity of fit residuals to chosen channels (e.g. oven/box/ambient temperatures) with lag scan; optional OADEV of telemetry channels as environment stability. Output CSV/JSON/Veusz | Synthetic injected coupling recovered; real HMD Jan–Mar 2026 vs Franks MSM-GPS |
| P7 | **T4 native log loader** | Parser for the RECMONIT files from FTP `./Monitoring` once a sample is available; raw codes kept; schema 0.0.2 | Your sample file decodes; values match the web page snapshot |
| P8 | **GUI and batch** | Stability tab: "Regular grid with gaps" mode, τ0 and ε fields, gap statistics; Telemetry-vs-timing tab; `analyze_maser.py --grid auto --slot-tolerance --stable32 --hmd --t4-telemetry --correlate` | GUI tests offscreen; batch run on your files |
| P9 | **Docs and release** | Manual 0.3.0 (LuaLaTeX, bookmarks) with the gap equations; README; VALIDATION; 7z; stage on Bracca | Fresh extract passes all tests and checksums |

## 3. Inputs staged here (`inputs/`)

| File | Use |
|---|---|
| `FITS_20251015-20251113.7z` | Raw FITS month (stability/Stable32 regression, MHM2010) |
| `MHM2010_HMD.7z` | Your HMD, 2025-04-09 to 2026-03-26 (P5, P6) |
| `Maser_UG_75666-201_Rev_J.PDF` | MHM2010 manual |
| `t4_manuals/` | T4S-MAN-0012 1.27, T4S-MAN-0015 1.17, iM165 test report, iMaser 3000 spec sheets |
| `reference_figures/` | Your two 1 PPS figures and the two T4 web pages |
| Franks 2026-01..07 | Already in the 0.2.0 package, `examples/franks_2026_01_07/` |

## 4. Inputs still needed from you

| # | Needed | Why |
|---|---|---|
| 1 | ~~One or two T4 log files~~ `MonitHCB.dat` received 2026-11-05. Still needed: the main monitoring file (40 channels) and a rolled, date-tagged copy | P7: confirm the reader on the main file and the rolled-file name |
| 2 | Which FITS/Franks channel is the T4 | T4 settings and T4 stability |
| 3 | FITS files for Jan–Mar 2026 | Raw-data stability overlapping the HMD (HMD ends 2026-03-26) |
| 4 | A newer HMD (after 2026-03-26) if you want telemetry for Oct–Nov 2026 | P6 on your reference-figure period |
| 5 | 3–5 MONIT replies from the GBO T4, and RDFS before/after one SETFSREL | T4 decoder confirmation and SETFSREL sign |

## 5. Resume prompt for the next session

> Continue the MHM2010 / T4 Maser Timing Workbench from 0.2.0 to 0.3.0 using
> `C:\Users\wwallace\Claude\Projects\Maser Analysis\staging_0.3.0\PLAN_0.3.0_telemetry_stability_stable32.md`.
> Source: `MHM2010_T4_Workbench_v0.2.0.7z` in the same folder; inputs in `staging_0.3.0\inputs`.
> Writer: master `claude/vszh5_writer.py` 0.0.24 (registry `claude/vszh5-writer-registry.md`).
> Implement P1–P9, keep every 0.2.0 test passing, never send a setting to a maser, package as 7z only,
> and stage the result back in the same folder.

## References

| Ref. | IEEE entry | URL |
|---|---|---|
| [1] | Microsemi, *MHM-2010 User's Guide*, 75666-201, rev. J, Oct. 2014. | https://www.gb.nrao.edu/~fghigo/timer/MSMmaser/Maser_UG_75666-201_Rev_J.PDF |
| [2] | W. J. Riley, *Handbook of Frequency Stability Analysis*, NIST SP 1065, Jul. 2008. | https://tf.nist.gov/general/pdf/2220.pdf |
| [3] | W. J. Riley, *Stable32 User Manual*, ver. 1.54, Sep. 2008. | http://www.stable32.com/Manual154.pdf |
| [4] | T4Science SA, *iMaser 3000 Installation, Operation and Maintenance User Manual*, T4S-MAN-0012, iss. 1.27, 2022. | Operator copy |
| [5] | T4Science SA, *T4S-Monitoring Network Software User Manual*, T4S-MAN-0015, ver. 1.17, 2022 (Secs. 1.1.3 RECMONIT, 6.5). | Operator copy |
