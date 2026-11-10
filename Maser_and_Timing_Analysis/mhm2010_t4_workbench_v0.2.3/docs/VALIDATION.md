# MHM2010 / T4 Workbench Validation

## Release 0.2.3 (2026-11-10)

| Check | Result | Confidence |
|---|---|---|
| Test suite, Python 3.13.18, PySide6 6.12.0, numpy 2.5.4, pandas 3.1.0, h5py 3.16.0, offscreen, TZ America/New_York | 133 passed (112 retained, 4 of them updated for the new names; 21 new) | High |
| `suggested_settings()` against `correction()` and `t4_settings()` | identical DDS count change and new DDS; drift change and new drift field equal to 1e-13 (both units, both pp18 conventions, both signs); SETFSREL and DRIFTCOMP identical | High |
| Known synthetic rate and drift (2e-14, 6e-16/day, 30 d hourly) | recovered within 3 sigma; signs: positive y → DDS up, positive drift → pp18 change negative ("positive raises output") | High |
| 2026 Franks MSM-GPS, last 30 days, quadratic, epoch 2026-07-12T22:29:06-04:00 | y −3.708732994065008e-14; drift −6.864816602325095e-16/day; **DDS change −526.79 ± 10.49 counts; drift change +686.48 ± 47.16 pp18/day = −9.7508 DDS counts/day**; straight-line figure −379.26 counts; quadratic at the mean sample time (MJD 61220.28) −392.00 counts, plus 13.82 d × −9.75 counts/day = −134.8 to the end | High (numbers); GPS noise not included in the sigmas |
| With DDS 5751.6770000 and drift −120 pp18/day | new DDS 5751.6769473 (−527 entered counts), new drift field +566.48 pp18/day | High |
| Empty DDS and drift fields | both changes still shown (0.2.2: nothing) | High |
| Exact exports | CSV (`phase_and_fit`, `telemetry_overlap`, T4 records, Stable32 time tags) read back bit-identical; Stable32 PHD exact; review.md holds `repr()` values; text datasets exact; full SHA-256 in notes | High |
| Names and tags | every dataset `[A-Za-z][A-Za-z0-9_]*`, every dataset tagged, exactly one `source_*` tag each; no `trace0`/`graph0` widget | High |
| HMD (historical telemetry_overlap.csv, 36 columns) | every column written, DDS change in counts, exact DDS text; ExtSynth_32 falls about 13 counts/day while Thumbwheels_31 is constant (drift compensation acting, inference) | High (data); inference flagged |
| Veusz 3.6.2 renders | `validation/veusz_render_v0.2.3/`: fit page with suggestion box above the data, MJD axes "61050" (not 6.105×10⁴), HMD DDS pages, 10-panel HMD overview, T4 log page | High |
| GUI captures | `validation/gui_reference_suggestions_light.png`, `_dark.png`, `gui_fit_results_suggestions_light.png`, `gui_results_absolute_light.png` | High |

**Display note.** Veusz's Data-editor table shows any float with 6 significant digits (Qt's default; Veusz 3.6.2 `dialogs/dataeditdialog.py`); the stored value is the full float64 (double-click a cell, or use the `*_text` datasets). The axes now use `%.12Vg`.

**Not validated:** Windows, Veusz 4.2.1 for the 0.2.3 pages, a real T4.

## Release 0.2.2 (2026-11-05)

| Check | Result | Confidence |
|---|---|---|
| Test suite, Python 3.12, PySide6 6.12.0, offscreen, TZ America/New_York | 112 passed (103 from 0.2.1 + 9 new) | High |
| ISO 8601 round trip | `2026-11-05T15:47:13-5:00` → MJD 61349.866123 → `2026-11-05T15:47:13-05:00` / `2026-11-05T20:47:13+00:00`; July → `-04:00` | High |
| Every control follows View > Time display | axes, Start/End/epoch fields, run list, results, status bar, reference, T4 and monitoring tabs | High |
| Last 30 / 90 days, 2026 Franks MSM-GPS (193 d) | 622 / 1955 samples; 90 d contains gaps → bridging enabled; disabled on the 12-day July file | High |
| Window comparison (end 2026-07-13 02:29 UTC) | 90 d: y −2.38e−14, −413 pp18/day, RMS 14.2 ns; 30 d: y −3.71e−14, −686 pp18/day, RMS 3.6 ns | High (numbers); the spread is window sensitivity |
| Batch `--start ...-4:00 --end ...-04:00` | runs; `veusz_file` printed; `--last-days 400` refused | High |
| Captures | `validation/gui_time_display_local_90d_light.png`, `gui_reference_local_30d_dark.png`, `gui_overview_utc_light.png`, `gui_view_time_display_menu.png`, `gui_fit_menu.png`, `gui_export_dialog.png` | High |

**Not validated:** Windows (the zone rules come from the operating system), launching Veusz from the dialog (Veusz not installed in the test environment).

## Release 0.2.1 (2026-11-05)

| Check | Result | Confidence |
|---|---|---|
| Test suite, Python 3.12, PySide6 6.12.0, offscreen | 103 passed (79 from 0.2.0 + 9 monitoring-file + 15 reboot-script loopback) | High |
| GBO `MonitHCB.dat` (3,530 bytes, SHA-256 `6fd040ecadccc053…`) | 84 records; 74 clock-valid, 2026-11-05 17:13:09 to 18:25:39 UTC; 10 pre-sync in 2 runs (2006-01-16 ×3, 2007-06-06 ×7); interval median 60 s, mean 59.59 s, range 59–62 s | High |
| Date order and time base | DMY: `16/01/06` is not MM/DD; last record 1.5 min before receipt (18:27 UTC); operator confirmed UTC. MDY reads 81 lines and the reader warns | High |
| HCB values | Set = internal = 21.5 °C, external constant 80 °C (probably no sensor; unverified), status "No error" | High for values; inference for the sensor |
| Franks 2026 regression with the file added | N 644, a1 −2.268 ± 0.019 ns/day, −373 counts (unchanged) | High |
| Veusz 3.6.2 render of the monitoring export | 5 pages (`validation/veusz_render_v0.2.1/`) | High |
| GUI captures | `validation/gui_t4_monitoring_light.png`, `_dark.png` | High |
| Reboot script 0.0.4 (loopback fake T4) | Fixed- and source-port answers, cancel, silence, `!GETID`, `!REBOOT`, busy port, stray/late datagrams, Windows reset; original 0.0.1 ends in `TimeoutError` after a successful send | High (loopback); untested on the T4 |

**Not validated:** the main 40-channel monitoring file, the T4's reply port (inferred from the vendor example and `T4_Maser_Comms.py`), the reboot script on the T4, Windows.

## Release 0.2.0 (2026-11-03)

| Check | Result | Confidence |
|---|---|---|
| Test suite, Python 3.12.15, PySide6 6.12.0, offscreen | 79 passed (36 from 0.1.0 + 29 T4 + 10 reference/Franks/time/export + 4 GUI) | High |
| T4 manual example replies (T4S-MAN-0012 Sec. 9.13), Type 1 CR LF and Type 2 CR | Decoded; LOCK 100 MHz 4.765 V vs 4.764 V on the operator's web page; +24 V 23.93 V; lock = 1 | High for layout and gains; not yet GBO captures |
| Schema 0.0.2 vs Table 6 and repository `T4_channel_names.csv` | Widths, offsets, gains and "Not used" channels identical | High |
| UDP (loopback emulator) | Success, timeout then success, exhaustion, wrong peer, duplicate, garbage, truncation, occupied port, cancellation, CR-only; only read-only queries sent | High (emulator), untested on real T4 |
| SETFSREL model | Eq. 12 sign, 2^-39 quantum, convention flip, range flags, RDFS prediction | High for arithmetic; **sign convention of SETFSREL unverified** |
| Franks 2026 (supplied), MSM-GPS, last 30.9 d, linear | N 644; a0 -2589.694 ± 0.349 ns; a1 -2.268 ± 0.019 ns/day; rate -2.63e-14; -373 counts; DDS calculation -372.86 | High |
| Franks month boundary | 13 repeats; 1 disagreeing (MJD 61222.01917, Site1Hz 0 vs 1), resolved to the July file | High |
| Writer | 0.0.23 rebuilt byte-exact from the master; 0.0.24 renders ISO local ticks in Veusz 3.6.2 | High (3.6.2); 4.2.1 not run |
| Veusz render of a real-data export | 25 pages incl. pps_deltas/pps_fit and their _local twins (`validation/veusz_render_v0.2.0/`) | High |

**Not validated:** a real T4 (no GBO MONIT captures or RDFS before/after a SETFSREL entry), which FITS/Franks channel is the T4, Windows, Stable32 import, and Veusz 4.2.1 for the new pages.

---


Version 0.1.0, 2026-11-03. This report separates verified computation from installation-specific and physical validation. No commands were sent to a maser and no Git repository was pushed.

## Automated checks

- **Python 3.12:** 36 tests pass, covering analytic coefficient recovery, DDS sign and quantization, equivalent drift-unit results, disabled drift, sign reversal, duplicate rejection, explicit exclusions/clipping, sample-grid checks, both writer allocation modes, atomic output protection and GUI controls, including stale-result and review-acknowledgment invalidation.
- **Independent estimators:** OADEV, MDEV, TDEV and OHDEV agree with AllanTools for a seeded synthetic phase record, including the number of contributing overlapping terms. These are comparisons of numerical implementations, not a calibration of a physical timing system.
- **Input handling:** FITS/FITS.GZ parsing verifies UTC and units, rejects simulated inputs, removes blank/zero-time padding and preserves valid raw zero and negative values. Franks-only missing sentinels are not applied to raw FITS.
- **GUI:** qtpy/PySide6 offscreen tests exercise timing selection, HMD event markers, both drift options, settings round-trip and stale-stability invalidation. GUI screenshots are supplied.
- **Veusz:** The 29-page example opens and renders through an external Veusz installation. Its PDF has bookmarks. Both MJD and UTC overview pages and all raw measured channel-pair datasets are retained.
- **Stable32:** File structure, CRLF headers, units, point counts, tau and actual-zero substitution are checked; import in the proprietary Stable32 executable has not been tested. The format follows the Stable32 manual [1](http://www.stable32.com/Manual154.pdf).

## Real uploaded data

The archive contains 109 FITS.GZ files and 40 measured channel pairs, with 9,810 samples per pair. The historical example restricts the fit to October 15 through November 13, 2025, yielding 9,600 direct MicroSem-to-CNS_GPS1 samples. The full HMD record runs from April 9, 2025, through March 25, 2026; the July 2026 FITS and Franks files therefore have no HMD overlap.

The 30-day quadratic fit has residual RMS 5.405 ns. At the final sample, November 13, 2025, 23:57:24.571 UTC, it estimates fractional-frequency error +5.83844e-15 and residual frequency drift -660.833 parts in 1e18/day. These are differential maser-versus-GPS measurements, not GPS-independent maser aging.

### Historical candidate, not a live recommendation

The nearby HMD external DDS is 5751.6772565. Assuming the drift was enabled at +975 pp18/day and positive pp18 increases output frequency, the calculated DDS candidate is 5751.6772648 and the new drift is +1635.833 pp18/day, equivalent to -23.235467 DDS units/day. The +975 value is an explicit example assumption from the older screenshot, not a verified November setting.

Integer rounding would give +1636 pp18/day or -23 DDS units/day. These are alternative representations, not two settings to apply together. The DDS direction follows the manufacturer equation; the pp18 GUI sign remains a site check because the manual does not state it [2](https://www.gb.nrao.edu/~fghigo/timer/MSMmaser/Maser_UG_75666-201_Rev_J.PDF).

### Window sensitivity

| Trailing window | Samples | Frequency error at common end | Residual drift, parts in 1e18/day | RMS, ns |
|---|---:|---:|---:|---:|
| 30 d | 9,600 | +5.83844e-15 | -660.833 | 5.405 |
| 21 d | 6,720 | +3.80025e-15 | -883.575 | 5.810 |
| 14 d | 4,481 | +1.39421e-14 | +407.199 | 5.922 |
| 7 d | 2,241 | -4.32655e-15 | -5454.180 | 6.970 |

The drift changes sign with the fit window. This is a substantive warning against applying the month-fit candidate without longer-term/reference/operating-state review. Small conditional OLS standard errors do not capture this window dependence, colored noise or systematic errors.

For the same 30-day selection, the MicroSem-to-CNS2018 and MicroSem-to-CNS_GPS2 drift estimates are -688.966 and -652.571 pp18/day, respectively. These are useful cross-checks, but the receivers may share environmental or GPS-related errors and do not automatically provide independent confirmation.

### Franks/Veusz comparison

Against the supplied `FranksProcessed_20260521.vszh5`, 735 overlapping UTC-hour bins were compared using the full archive span. The new UTC-hour median reductions are close but not asserted bit-for-bit equivalent to historical reduction windows.

| Comparison | Matched bins | Median new minus reference, ns | RMS difference, ns | Maximum absolute difference, ns |
|---|---:|---:|---:|---:|
| Derived MSM-GPS | 735 | approximately 0 | 1.284 | 4 |
| MicroSem | 735 | approximately 0 | 0.852 | 2 |
| CNS_GPS1 | 735 | 0 | 1.004 | 4 |

The reference file also contains legacy invalid negative MJD values and all-missing channels. Those were not copied into the new overview; only channels actually present in the selected source are plotted. Raw FITS pairs, direct MicroSem/GPS medians and the difference of Site1Hz-based medians remain distinct.

## Writer correction

The supplied writer, version 0.0.21, accepted a 2-D array in `dataset_date()`. In fixed-size mode this stored a two-dimensional date dataset; in resizable mode the array could be flattened silently. Version 0.0.22 rejects non-1-D dates before creating the dataset group. The test reproduces the old defect and checks both corrected modes. This was a targeted review, not a claim that every unrelated writer feature was exhaustively audited.

## Remaining validation

- **Windows:** Actual rendering, scaling, file dialogs and packaging on bracca remain untested. The environment exposed no connected bracca device at the last check.
- **masermon:** Confirm installed version, pp18 sign, allowed entry precision/range, drift update behavior, persistence and enable-state semantics. The Rev. J manual specifies the units but not all these behaviors [2](https://www.gb.nrao.edu/~fghigo/timer/MSMmaser/Maser_UG_75666-201_Rev_J.PDF).
- **Instrument:** Confirm the external synthesizer is actually selected. A displayed numeric HMD selector code is not decoded into that assertion.
- **Stable32:** Open the supplied historical PHD/FRD files, verify tau and zero handling, then compare chosen statistics with the supplied CSV [1](http://www.stable32.com/Manual154.pdf).
- **Uncertainty:** Fit errors are conditional IID-OLS estimates, not confidence intervals for a colored-noise frequency standard. Stability term counts are not equivalent degrees of freedom; NIST discusses the distinction [3](https://tf.nist.gov/general/pdf/2220.pdf).

## Equation appendix

The complete implemented equations and assumptions are displayed in the manual's Appendix A. This validation report uses those definitions without introducing additional estimators.

## Reference table

| Ref. | Full IEEE bibliographic entry | DOI or URL |
|---|---|---|
| [1] | W. J. Riley, Stable32 User Manual, ver. 1.54, Sep. 7, 2008. Accessed Nov. 3, 2026. | http://www.stable32.com/Manual154.pdf |
| [2] | Microsemi, Atomic Hydrogen Maser Frequency Standard Model MHM-2010 User’s Guide, doc. 75666-201, rev. J, Oct. 2014. Accessed Nov. 3, 2026. | https://www.gb.nrao.edu/~fghigo/timer/MSMmaser/Maser_UG_75666-201_Rev_J.PDF |
| [3] | W. J. Riley, Handbook of Frequency Stability Analysis, NIST SP 1065, Jul. 2008. Accessed Nov. 3, 2026. | https://tf.nist.gov/general/pdf/2220.pdf |
