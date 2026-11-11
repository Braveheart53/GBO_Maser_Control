# Revision History

## Workbench 0.3.0, 2026-11-11

- **Further evaluation in four new tabs** (operator request of 2026-11-10; the base behavior is unchanged). New `maser_eval.py` 0.0.1 and `gui_evaluation.py` 0.0.1; tabs after "Stability" and an **Evaluate** menu:
  - *Eval: gap-aware stability*: OADEV, MDEV, TDEV, OHDEV on a regular grid with gap markers (anchored, sequential or auto; NIST SP 1065 Secs. 5.16, 10.4–10.6); a term that touches a gap is skipped, nothing is interpolated; lag-1 noise identification (Riley and Greenhall 2004; SP 1065 Secs. 5.5.4–5.5.5); chi-squared intervals with the simple EDF of SP 1065 Table 5 for OADEV, σ/√N otherwise; data: the selected samples or the steering-removed samples.
  - *Eval: steering removal*: the MHM2010 steering recorded in the HMD DDS (all changes, or steps above a threshold) converted to fractional frequency with the MHM2010 relation, integrated to phase (zero-order hold) and removed; the base model and window fitted to the removed record and compared at the application epoch.
  - *Eval: Kalman estimate*: three-state clock model (phase, frequency, drift), Joseph-form Kalman filter and RTS smoother; gaps carried by prediction; noise fitted to the gap-aware OHDEV (NNLS, Ishizaki et al. Eq. 23b plus white PM) or entered by hand; estimate at the application epoch beside the base values; settings from it marked EVALUATION ONLY.
  - *Eval: three-cornered hat*: individual Allan deviations from three pairs, or two pairs against a common clock (Gray and Allan 1974), gap-aware OADEV on common time stamps; non-positive variances reported.
- **Veusz pages to generate.** OUTPUT checkboxes (`maser_export.ExportContent`): seven base groups on by default (the 0.2.4 file, identical by fingerprint) and four evaluation groups off by default; Defaults / All; saved with the settings; a ticked evaluation is recalculated at export and, if it cannot be calculated, left out with an audit note while the base export continues. Unticking removes pages only (CSV/JSON complete).
- **Exports** (`maser_export.py` 0.3.0): pages `GapStab_*`, `Steer_*`, `Kalman_*`, `TCH_allan_deviation` and their datasets (tags `type_evaluation`, `type_steering`, `type_kalman`, `type_metadata`); `evaluation_gap_stability.csv`, `evaluation_steering_removed.csv`, `evaluation_kalman.csv`, `evaluation_three_cornered_hat.csv`; `analysis.json` adds `export_content`, `evaluation`, `evaluation_files` (strict JSON: NaN → null); `review.md` adds "Veusz content" and "Further evaluation". Kalman ±1σ as separate datasets; every number exact.
- **Batch** (`analyze_maser.py` 0.3.0): `--no-reference-figures`, `--no-timing-pages`, `--no-overview`, `--no-stability-pages`, `--no-hmd-plots`, `--no-t4-plots`, `--no-t4-monitoring-plots`; `--gap-stability` (`--gap-source`, `--gap-detrend`, `--gap-grid`, `--gap-tau0`, `--gap-tolerance`, `--confidence`), `--remove-steering` (`--steering-mode`, `--steering-threshold`, `--steering-column`; needs `--hmd`), `--kalman` (`--kalman-noise`, `--no-kalman-smoothing`), `--tch` (`--tch-mode`, `--tch-names`, `--tch-signs`, `--tch-detrend`). Prints `evaluation_files`.
- **Fixes.** Switching to another dataset after a fit left the old fit in place and raised an `IndexError` traceback (0.2.4): the fit, candidate, suggestion and stability result are now cleared when the channel changes. Light-theme legends of the evaluation plots follow the theme. `time_format.py` 0.0.3: `__version__` matches its header (0.0.2 shipped with "0.0.1"). An HMD that does not overlap the timing data is refused with both MJD ranges.
- **Documentation.** Manual 0.3.0 (71 pages), reorganized by topic: a variable table before every equation block (35 blocks, 83 numbered equations, 21 variable tables); Appendix B (all variables, with links to the equations) and Appendix C (all stability traces: GUI, Veusz pages, datasets, columns); lists of tables (55) and figures (18) in the contents and the bookmarks (now including subsections). The 0.2.3 equations S1–S4 use the Appendix A symbols (arithmetic unchanged). README, VALIDATION, manifest.
- **Tests.** 174 pass (159 retained; `tests/test_release_024.py` 0.0.2 accepts 0.2.4 or later; + 15 new in `tests/test_evaluation_030.py`). `tools/export_fingerprint.py` 0.0.1 and `tests/data/export_fingerprint_0.2.4.json`. Writer unchanged (0.0.29).

## Workbench 0.2.4, 2026-11-10

- **Writer brought up to date.** `vszh5_writer.py` **0.0.29** = registry master **0.0.28** (FitsAutoPlot 0.4.1: faster saving, identical files; rebuilt from the bundled 0.0.27 and matched to the master byte for byte, SHA-256 `13f68513…f634a64b5`) + **0.0.29** (this release, made from the master, additions only): `iso_local_strings(timespec=)` with an `"exact"` mode and the new `mjd_iso_local_strings()`. Every 0.0.28 call writes the same file (object-by-object test). The registry and the RF-project master are updated; no other chat had a newer writer (checked 2026-11-10).
- **No rounding left in the exports** (follow-up audit of "in all exports, all floats or numbers should never be rounded or trimmed"):
  - `<stem>_time_local_ISO_text` datasets carry every digit: from the MJD in decimal arithmetic (Franks MJD 61234.10355 → `2026-07-12T22:29:06.72-04:00`; 0.2.3 cut it to `…22:29:06`); T4 monitoring logs use the whole seconds the T4 wrote (`maser_export.py` 0.2.4).
  - T4 live recorder CSVs (`t4_monitor.py` 0.0.4): MJD, RTT and the scaled channel values are written with `repr()` (0.0.3: `%.10f` = 8.64 µs steps, `%.3f`, `%.6g`); the UTC text keeps every digit of the host clock (0.0.3: whole microseconds). Re-loading a recording now gives the identical MJD floats.
  - Fit figure (GUI and Veusz, `maser_plots.py` 0.0.3): the application epoch in the suggested-changes box (`…22:29:06.72-04:00 (MJD 61234.10355)`) and the `MJD - MJD0` axis label carry every digit (`time_format.py` 0.0.2 `mjd_text()`; a FITS MJD0 was rounded to 5 decimals, up to 0.43 s).
  - Batch JSON (`analyze_maser.py` 0.2.4): `application_epoch_mjd_utc` (exact) beside the ISO text, now to 9 decimals of a second; `__version__` matches the header (0.2.3 left 0.2.2).
- **Unchanged on purpose.** The statistics/fit box keeps the figure layout of your reference figures (e.g. `a1 = -2.307 ± 0.019 ns/day`); every number in it is a full-precision dataset (`*_results_full_precision_text`, 0.2.3). A new DDS value is quantized to the 7 decimals the MHM2010 accepts; the exact change and the quantization residual are reported beside it (0.2.3).
- **Tests.** 159 pass (133 retained, 2 extended, + 26 new: `tests/test_writer_0029.py`, `tests/test_release_024.py`). **Docs.** Manual 0.2.4, README, VALIDATION, writer 0.0.29 notes, `docs/writer_0.0.29/` (master 0.0.28, the rebuild and patch scripts, the diff).

## Workbench 0.2.3, 2026-11-10

- **Both suggestions, always.** `maser_core.py` 0.2.3 `suggested_settings()`: MHM2010 DDS change (counts) **and** drift change (pp18/day and DDS counts/day); T4 `SETFSREL` **and** `DRIFTCOMP`; at the application epoch, from the selected model. The changes need no current settings (0.2.2 showed nothing when the current-drift field was empty); the new absolute values are added when the current DDS and drift field are given. Same arithmetic as `correction()` / `t4_settings()` (tested).
- **Shown everywhere.** Results box ("SUGGESTED CHANGES" with exact values); a box on the reference fit figure (GUI `gui_reference.py` 0.0.3 and both Veusz fit pages, `maser_plots.py` 0.0.2) at the upper left with headroom above the data; `analysis.json` (`suggested_settings`), `review.md` (exact-value table), `Suggested_<maser>_*` datasets; batch output (`analyze_maser.py` 0.2.3).
- **No rounding in exports.** CSV via exact float text (T4 CSVs were `%.6g`), Stable32 and `review.md` with `repr()`, full SHA-256 in notes, every figure-box number also a dataset. New `export_names.py` 0.0.1.
- **Names and tags.** Datasets `<Source>_<item>_<quantity>[_<unit>]`; Veusz tags `source_*` and `type_*` on every dataset; traces, graphs, boxes and pages named after their data (writer 0.0.27 from the FitsAutoPlot folder on bracca; was 0.0.24).
- **Veusz display.** Numeric axes `%.12Vg` (MJD no longer 6.105×10⁴; long DDS labels no longer cut); HMD: every column, DDS change in counts, exact DDS text, 10-panel overview pages; constant series padded.
- **GUI.** Placeholders no longer say "Required" for the changes; tab labels show "&" (were "Fit _corrections").
- **Tests.** 133 pass (112 retained, 4 updated for the new names, + 21 new). **Docs.** Manual 0.2.3, README, VALIDATION, writer 0.0.27 notes.

## Workbench 0.2.2, 2026-11-05

- **Time display.** View > Time display (axes, dates, ranges): MJD (UTC), calendar date-time local, or UTC, ISO 8601 (e.g. `2026-11-05T15:47:13-05:00`; shortcuts Ctrl+Shift+M/L/U). One choice drives every time axis (fit, residual, overview, HMD, reference figures, T4 telemetry, T4 monitoring files), the Start/End/epoch fields (they show and accept the chosen form, including `-5:00`, `Z`, dates or MJD), the run list, results, status bar and audit messages. Data, fits and files stay MJD. Remembered in `workbench_view.ini` and in saved settings. New `time_format.py` and `time_axis.py` 0.0.1 (DST-aware ticks, two-line ISO labels without overlap).
- **Fit windows.** "Fit window" presets and a Fit menu: last 7, 14, 30, 90 days or all data, enabled only when the data span them; gaps switch on fit-across-gaps (logged). Comparison adds 90 days and skips windows longer than the data (`maser_core.py` 0.2.2: `window_sensitivity(days=...)`).
- **Veusz files.** Menus and the OUTPUT button say "Veusz .vszh5"; after an export a dialog gives the path with Open in Veusz / Open folder; "Open last export in Veusz"; File > Set Veusz program.
- **Batch** (`analyze_maser.py` 0.2.2): `--start/--end` accept ISO 8601 with offsets or MJD; `--last-days` must fit in the data; 90-day comparison when spanned; prints `veusz_file`.
- **Fixes.** MJD tick labels keep the decimals the spacing needs (61349.71, not 61349.7 twice); plot titles readable in Light; run list no longer widens the side panel. `gui_reference.py` 0.0.2, `gui_t4.py` 0.0.3, `gui_t4_logs.py` 0.0.2 (its own x-axis selector replaced by the global setting).
- **Tests.** 112 pass (103 retained + 9 new). **Docs.** Manual 0.2.2 (28 pages), README, VALIDATION.

## Workbench 0.2.1, 2026-11-05

- **T4 monitoring files.** New `t4_native.py` 0.0.1 reads the LAN module's own logs from FTP `./Monitoring` (format inferred from the GBO `MonitHCB.dat`: tab-separated, `TIME` in `DD/MM/YY HH:MM:SS` UTC, `Label [unit]` columns). Records stamped before the time-server sync are kept and flagged, never plotted; runs are reported separately. Degree-sign repair, Windows-1252 fallback, date-order warning, interval statistics, rolled-file merge, parallel file reads.
- **GUI.** New tab "T4 monitoring files" (`gui_t4_logs.py` 0.0.1): one x-linked plot per column against MJD − MJD0, UTC or local time; every record (clock-not-set rows greyed); reader options; Clear from RAM. Open (Ctrl+T), T4 menu and drag-and-drop route files by content. `gui_t4.py` 0.0.2: local port 14000 by default for a real T4.
- **Exports.** `maser_export.py` 0.2.1: monitoring-file pages (per column, status, notes; MJD and local), records CSV and JSON summary; `export_t4_only` accepts MONIT, monitoring files or both; analysis bundles include them. Review reference to `GBO_Maser_Control` corrected to commit c72244d.
- **Inputs.** `maser_io.py` 0.2.1: `load_t4_files()` routes MONIT and monitoring files; they are never merged.
- **Poller.** `t4_monitor.py` 0.0.3: command line uses local port 14000 for a real T4 (vendor example; `T4_Maser_Comms.py`), ephemeral for `--emulator`.
- **Core.** `maser_core.py` 0.2.1: DRIFTCOMP note now says REBOOT restarts the M&C software (T4S-MAN-0015 Table 1); no calculation changed.
- **Batch.** `analyze_maser.py` 0.2.1: `--t4-telemetry` takes monitoring files; `--t4-date-order`, `--t4-earliest-valid`.
- **Operator script.** `tools/T4_Reboot.py` 0.0.4 (not used by the workbench) with 15 loopback tests.
- **Tests.** 103 pass (79 retained + 9 monitoring-file + 15 reboot-script). Franks regression unchanged.
- **Docs.** Manual 0.2.1 (24 pages): monitoring files, reboot script, local port, corrected REBOOT wording and page citations; README, VALIDATION; plan 0.3.0 status.

## Workbench 0.2.0, 2026-11-03

- **T4 iMaser 3000.** Maser selector. `t4_settings()` suggests `SETFSREL` (Eq. 12 of T4S-MAN-0012, 39-bit synthesizer step 5 MHz/2^39) and `DRIFTCOMP` (T4S-MAN-0015) with documented ranges, predicted `RDFS` read-back and an explicit sign-convention selector. Nothing is ever sent.
- **T4 telemetry.** `t4_protocol.py` 0.0.2 (strict MONIT framing, CR LF or CR, schema 0.0.2 checked against Table 6, lock bit, vectorized decoder, read-only query parser). `t4_monitor.py` 0.0.2 (read-only poller, raw-first recorder, RAM flush and pickle offload). `t4_emulator.py` 0.0.2 (manual example reply, fault injection). `gui_t4.py` 0.0.1 tab with live polling and state read-back.
- **Reference 1 PPS figures.** `maser_plots.py` 0.0.1 and `gui_reference.py` 0.0.1 reproduce the operator's deltas and fit/residual figures, with identical numbers in the GUI and Veusz. They are the first pages of `analysis.vszh5`.
- **Inputs.** FITS and Franks in one selection, kept separate. Franks month-boundary repeats are resolved and reported (a real 2026_06/2026_07 conflict was found). Optional fit across gaps (default for Franks). "All data" run choice.
- **Time axes.** Every time-series page is written against MJD and against local ISO 8601 date-time (writer 0.0.24).
- **GUI.** Menu bar with View > Theme (Light/Dark), T4 menu and About; window title MHM2010 / T4 Maser Timing Workbench.
- **Core.** Stability estimators run per averaging factor in a thread pool (results unchanged; AllanTools comparison passes). `window_sensitivity()` honours gap bridging.
- **Batch.** `analyze_maser.py` 0.2.0: `--timing-directory` (FITS and/or Franks), `--maser T4`, `--t4-synth`, `--t4-driftcomp`, `--setfsrel-convention`, `--last-days`, `--order`, `--bridge-gaps`, `--title/--ylabel/--fit-ylabel`.
- **Tests.** 79 pass (36 retained + 43 new), including a regression on the supplied July 2026 Franks files.
- **Docs.** Manual 0.2.0 (LuaLaTeX, bookmarked, 20 pages) with the new derivations.

## Bundled writer 0.0.24, 2026-11-03

Built on the master 0.0.23 from the VSZH5 writer chat (rebuilt byte-exact, SHA-256 `90833d76…eaac6`). Additions only: `mjd_to_epoch`, `mjd_to_veusz`, `iso_offset_text`, `LocalTimeAxis`/`local_time_axis`, `iso_local_strings`, `with_x`, `time_pair_pages`, and `Panel.datetime_format`. See `docs/vszh5_writer_0.0.24_NOTES.md`.

## Workbench 0.1.0, 2026-11-03

- FITS/FITS.GZ primary ingestion, separate Franks reduced-data ingestion, HMD housekeeping.
- qtpy/PySide6 GUI with pyqtgraph interval selection, exclusions, phase/residual fitting, telemetry and timing overview.
- Linear and quadratic phase models with DDS correction and both drift-unit representations.
- OADEV, MDEV, TDEV and OHDEV, explicit sampling tolerance and no interpolation.
- Native h5py Veusz export, Stable32 PHD/FRD, CSV, JSON and reproducible settings.
- Bookmarked LuaLaTeX PDF manual and regression tests.

## Bundled writer 0.0.22, 2026-11-03

The supplied 0.0.21 writer accepted a multidimensional date array. This could produce an invalid date dataset, or flatten it silently in resizable mode. `dataset_date()` now rejects non-1-D input before creating the dataset group. Existing valid calls and all other writer behavior are unchanged. The upstream revision history and license remain intact.
