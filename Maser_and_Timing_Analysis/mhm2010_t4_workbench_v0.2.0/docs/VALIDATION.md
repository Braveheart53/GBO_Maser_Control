# MHM2010 / T4 Workbench Validation

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
