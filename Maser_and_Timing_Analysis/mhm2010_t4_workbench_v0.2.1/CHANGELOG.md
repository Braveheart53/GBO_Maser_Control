# Revision History

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
