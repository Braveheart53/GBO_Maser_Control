# MHM2010 / T4 Maser Timing Workbench

Version **0.3.0**, 2026-11-11. Python 3.12 (tested also on 3.13), qtpy with PySide6, pyqtgraph. Author: William W. Wallace (NRAO/GBO). GPL-3.0-or-later.

The workbench analyses 1 PPS timing deltas (FITS OnePpsDeltas and/or Franks `.t00new`). It reproduces your two reference figures and suggests settings for the **MHM2010** (external DDS, drift) and the **T4 iMaser 3000** (`SETFSREL`, `DRIFTCOMP`). It also reads MHM2010 HMD telemetry, T4 MONIT telemetry and the T4's own monitoring files (FTP `./Monitoring`, e.g. `MonitHCB.dat`). Release 0.3.0 adds four **further-evaluation tabs** (gap-aware stability with confidence intervals, steering removal, Kalman estimate, three-cornered hat) and **checkboxes for the Veusz pages to generate**; the base fit and the suggested settings are unchanged. **It never sends a setting to a maser.** The T4 tab can send only the documented read-only queries `MONIT;`, `RDFS;`, `GETID;` and `RDREG=…;` [4].

## Further evaluation (0.3.0)

Four tabs after "Stability" (also in the **Evaluate** menu). They read the data, the fit and the side-panel settings and write nothing back: the Results box, the reference figures and `suggested_settings` are identical with or without them (tested).

| Tab | What it shows | Method [refs] |
|---|---|---|
| Eval: gap-aware stability | OADEV, MDEV, TDEV, OHDEV with confidence intervals, from the selected samples or the steering-removed samples | Regular grid with gap markers (anchored, sequential or auto); a term that touches a gap is skipped, nothing is interpolated (NIST SP 1065 Secs. 10.4–10.6, 5.16) [2], [19]; lag-1 noise identification [13]; chi-squared intervals with the simple EDF of SP 1065 Table 5 for OADEV, σ/√N otherwise |
| Eval: steering removal | The maser without the steering recorded in the HMD (manual DDS steps and the drift compensation); the base fit beside the same fit of the removed record | DDS → fractional frequency (MHM2010 relation [1]), integrated to phase (zero-order hold) and removed |
| Eval: Kalman estimate | Frequency and drift with ±1σ along the record and at the application epoch, beside the base least-squares values; settings from it are marked EVALUATION ONLY | Three-state clock model, Kalman filter and RTS smoother [14], [15], [18]; noise fitted to the gap-aware OHDEV [16] |
| Eval: three-cornered hat | Individual Allan deviations of three clocks (three pairs, or two pairs against a common clock) | Gray–Allan separation on common time stamps [17]; which channel compares which clocks is your statement |

On the historical month (Oct.–Nov. 2025) removing the recorded steering changes the fitted drift from −6.60833065550308×10⁻¹⁶/day to −1.5935539121476658×10⁻¹⁵/day. The difference equals the HMD's drift-compensation ramp (9.331958009774105×10⁻¹⁶/day ≈ 933 pp18/day) to 0.05 %. The Kalman drift uncertainty for that 30-day window is ±1.013271635171295×10⁻¹⁵/day, which is why the drift changes sign with the window length. All numbers and the equations (with a variable table before each block) are in the manual, section "Further evaluation".

## Veusz pages to generate (0.3.0)

OUTPUT > **Veusz pages to generate**: seven base groups (on by default: the 0.2.4 file, identical by fingerprint) and four evaluation groups (off by default); **Defaults** and **All** buttons; saved with the settings. Untick "HMD telemetry plots" to leave the telemetry plots out; tick "Evaluation: steering events removed" to add the steering-removed pages. Unticking removes pages only: CSV and JSON stay complete. Batch: `--no-reference-figures`, `--no-timing-pages`, `--no-overview`, `--no-stability-pages`, `--no-hmd-plots`, `--no-t4-plots`, `--no-t4-monitoring-plots`, and `--gap-stability`, `--remove-steering` (needs `--hmd`), `--kalman`, `--tch CH1 CH2 [CH3]` with their options (`python analyze_maser.py -h`).

## Start (Windows, mamba or venv)

```powershell
mamba create -n maser312 -c conda-forge python=3.12 -y
mamba activate maser312
python -m pip install -r requirements.txt
python MHM2010_Correction_App.py
```

`launch_windows.cmd` starts the GUI from a `.venv` in this folder or from `py -3.12`. In Spyder, select the environment and run the entry script; the sources use `# %%` cells.

## Workflow

| Step | Where | Notes |
|---|---|---|
| Load timing | File > Open timing files | FITS and Franks may be selected together; they stay separate series |
| Choose maser | INPUTS: MHM2010 / T4 | T4: tick "Selected channel is the T4 comparison" (no FITS/Franks name identifies the T4 yet) |
| Select interval | Blue region, run list, or **Fit window** / **Fit** menu: last 7, 14, **30**, **90** days or all data | A preset is enabled only when the data span it; gaps switch on "Allow fit across acquisition gaps" (logged) |
| Reference figures | Tab "1 PPS reference plots" | Deltas + statistics; data + fit over residuals; editable title/labels |
| Suggested changes | Fit & corrections (Results box) **and** the reference fit figure (box at the upper left) | **Both, always:** MHM2010 DDS change (counts) **and** drift change (pp18/day and DDS counts/day); T4 SETFSREL **and** DRIFTCOMP. No current settings needed for the changes |
| New absolute settings | Fit & corrections, "Settings at application epoch" | Enter the current DDS and drift field (T4: RDFS, RDREG=DRIFTCOMP) to get the new absolute values |
| T4 monitoring files | File > Open T4 telemetry or monitoring files (Ctrl+T), or drag in `*.dat` | Tab "T4 monitoring files": one plot per column against MJD − MJD0, UTC or local time; every record listed |
| T4 state | T4 tab > Read synthesizer and DRIFTCOMP state | Fills the RDFS and DRIFTCOMP fields (read-only) |
| Time display | **View > Time display (axes, dates, ranges)** | MJD (Ctrl+Shift+M), local ISO 8601 `2026-11-05T15:47:13-05:00` (Ctrl+Shift+L) or UTC ISO 8601 (Ctrl+Shift+U): every axis, date field, run list and result text; data stay MJD |
| Further evaluation | Tabs "Eval: …" or the **Evaluate** menu | Gap-aware stability with confidence intervals, steering removal (needs HMD), Kalman estimate, three-cornered hat; never changes the suggestion |
| Choose Veusz pages | OUTPUT > **Veusz pages to generate** | Base pages on, evaluation pages off by default; Defaults / All |
| Export | **File > Export analysis folder (Veusz .vszh5, CSV, JSON, Stable32)** (Ctrl+E) | New folder `<maser>_analysis_<time>/analysis.vszh5` (every time page against MJD and local ISO time), CSV, JSON, review, Stable32, and `evaluation_*.csv` for ticked evaluations; a dialog gives the path with **Open in Veusz** / **Open folder** |

The figure's "synth adjustment" uses the straight-line mean rate over the plotted span, as your figures do. The "suggested changes" box and the Results box evaluate the selected model at the application epoch (the end of the window by default). With a drift present they differ: on the last 30 days of your 2026 Franks data the straight line gives −379.26 counts; the quadratic gives −392.00 counts at the mean sample time (MJD 61220.28; the 5-day gap makes the sampling uneven) and −526.79 counts at the end, 13.82 days later (13.82 d × 9.75 counts/day = 134.8 counts). **Enter the suggested-changes values, after review.**

## Suggested settings: frequency AND drift (0.2.3)

The MHM2010 has two adjustments, the external DDS (frequency) and the drift field; the T4 has `SETFSREL` and `DRIFTCOMP`. Both are now suggested together, from the selected model at the application epoch [1], [3], [4]:

| Quantity | Equation (y, D: measured fractional frequency and drift/day, with the channel sign) |
|---|---|
| DDS change (counts of 10⁻⁷) | ΔN = y (284.08 + 2×10⁻⁷ DDS) / 2×10⁻¹⁴ |
| Drift change, pp18/day | ΔF = −c · D / (1 + y) · 10¹⁸ (c = +1 when a positive pp18 raises the output) |
| Drift change, DDS counts/day | ΔF_DDS = D (284.08 + 2×10⁻⁷ DDS) / 2×10⁻¹⁴ |
| T4 | `SETFSREL` from Eq. 12 (39-bit step); `DRIFTCOMP_new = DRIFTCOMP_now − D` |

- **Where:** the Results box ("SUGGESTED CHANGES", with the exact values), a box at the upper left of the reference fit figure (GUI and both Veusz fit pages; the y axis leaves room above the data), `analysis.json` → `suggested_settings`, `review.md` (table of exact values) and the `Suggested_<maser>_*` datasets.
- **Needs:** a quadratic phase model for the drift (with "Linear phase" the box says the drift is not estimated). The changes need no current settings; with the current DDS and drift field entered, the new absolute values are added (e.g. DDS 5751.6770000 → 5751.6769473; drift −120 → +566.48 pp18/day).
- **Not sent:** nothing is ever sent to a maser.

## Writer and exact times (0.2.4)

- **Writer up to date:** `vszh5_writer.py` 0.0.29 = the RF-project master 0.0.28 (FitsAutoPlot 0.4.1: faster saving, identical files) plus 0.0.29 (exact ISO 8601 text). No other chat had a newer writer. Registry: RF project `claude/vszh5-writer-registry.md`.
- **Times as text keep every digit:** `*_time_local_ISO_text` datasets, the application epoch in the suggested-changes box and the `MJD - MJD0` axis labels. Example: Franks MJD 61234.10355 → `2026-07-12T22:29:06.72-04:00` (0.2.3: `…22:29:06`). The text gives back the identical MJD float. T4 monitoring logs keep the whole seconds the T4 wrote.
- **T4 live recorder CSVs:** MJD, RTT and channel values are written with every digit (were `%.10f`, `%.3f`, `%.6g`), and the UTC text is exact.

## Exports: names, tags and full precision (0.2.3)

- **No number is rounded or trimmed** in any export: CSV columns round-trip to the same float64 (they were `%.6g` in the T4 CSVs), Stable32 files and `review.md` use exact text, file hashes are written in full, and every number shown in a figure box is also a full-precision dataset. Figure boxes keep the figure layout (rounded for reading).
- **Names:** `<Source>_<item>_<quantity>[_<unit>]`, letters, digits and `_` only, e.g. `Franks_MSM_GPS_phase_ns`, `Selected_MSM_GPS_time_MJD_UTC`, `Fit_MSM_GPS_residual_ns`, `Ref1PPS_MSM_GPS_fit_line_ns`, `HMD_ExtSynth_32`, `T4log_MonitHCB_Internal_temperature_degC`, `Suggested_MHM2010_drift_change_pp18_per_day`. Traces, graphs and boxes in the Veusz widget tree carry the same names (writer 0.0.27).
- **Tags** (Veusz Data panel, *Group by: Tags*): `source_*` (Franks, FITS, HMD, T4_MONIT_UDP, T4_log_<layout>, Workbench) and `type_*` (time_MJD, time_local, measurement, fit_model, fit_residual, fit_results, suggested_settings, telemetry, status, stability, reference_figure, text, …).
- **Axes:** numeric ticks show up to 12 significant digits (MJD 61050, not 6.105×10⁴). Veusz's Data-editor table shows 6 significant digits of any float (a Veusz display rule); double-click a cell, or open the `*_text` datasets, to see all digits.
- **HMD:** every column over the fit interval (was the DDS only), the DDS settings also as a change in counts, and their exact text.

### T4 settings in one line each [3], [4]

- Frequency: y₀ = −Δν_synth / (284 × 5 MHz) (Eq. 12). The synthesizer step is 5 MHz/2³⁹ = 9.0949 µHz (6.4×10⁻¹⁵ relative). The panel prints `SETFSREL=±x;` and the RDFS value expected afterwards. **Confirm the SETFSREL sign by comparing RDFS before and after.**
- Drift: `DRIFTCOMP_new = DRIFTCOMP_now − measured drift/day`, with |x| between 10⁻¹⁷ and 8×10⁻¹³. Send REBOOT afterwards (it restarts the M&C software [4, Table 1]), then verify with `RDREG=DRIFTCOMP;`.

## Time display and fit windows (0.2.2)

- **View > Time display** changes every time shown, not the data: plots (fit, residual, overview, HMD, reference figures, T4 telemetry and monitoring files), the Start/End/epoch fields, the run list, the results and status bar. Calendar times keep the MJD in brackets for the audit trail. The choice is remembered in `workbench_view.ini` beside the program.
- Fields accept `2026-11-05T15:47:13-05:00`, `-5:00`, `Z`, a date alone, a time without offset (local in local mode, UTC in UTC mode) or an MJD.
- **Fit window** presets end at the last sample. "Compare 7/14/21/30/90-day fits" logs each window to the audit tab.

## Where the Veusz files are

| Action | Veusz file |
|---|---|
| File > Export analysis folder (Ctrl+E) or OUTPUT > "Export analysis: Veusz .vszh5 + CSV/JSON" | `<parent>/<MHM2010\|T4>_analysis_<time>/analysis.vszh5` (needs a valid fit) |
| File > Export T4 data (Ctrl+Shift+E) or the T4 tabs' export buttons | `<parent>/T4_telemetry_export_<time>/t4_telemetry.vszh5` |
| `analyze_maser.py ... --output DIR` | `DIR/analysis.vszh5` (printed as `veusz_file`) |
| `t4_native.py FILES --output DIR` | `DIR/t4_telemetry.vszh5` |

"Open last export in Veusz" (OUTPUT box, File menu) reopens it. Veusz is found via File > Set Veusz program, PATH, or `Program Files\Veusz`.

## Batch

```text
python analyze_maser.py --timing-directory <FRANKS_OR_FITS_DIR> --channel MSM-GPS --order 1
  --bridge-gaps --last-days 30.9 --dds 5751.6770000 --output <NEW_DIR>
python analyze_maser.py --timing-directory <DIR> --channel MSM-GPS --bridge-gaps --last-days 90
  --end 2026-07-13T02:29:08Z --output <NEW_DIR>
python analyze_maser.py --timing-directory <DIR> --channel MSM-GPS --order 1 --bridge-gaps
  --start 2026-06-12T22:29:07-4:00 --end 2026-07-12T22:29:08-04:00 --output <NEW_DIR>
python analyze_maser.py --timing-directory <DIR> --maser T4 --channel "<T4 CHANNEL>"
  --t4-synth <RDFS Hz> --t4-driftcomp <RDREG value> --output <NEW_DIR>
python analyze_maser.py --fits-directory <FITS_DIR> --hmd <HMD_FILE> --start 2025-10-15 --end 2025-11-14
  --derive-dds --output <NEW_DIR> --gap-stability --gap-source steering-removed --gap-detrend 2
  --remove-steering --kalman --no-hmd-plots
python analyze_maser.py --timing-directory <FRANKS_DIR> --channel MSM-GPS --output <NEW_DIR>
  --tch MSM-GPS CNS_GPS --tch-mode common --tch-names MSM CNS GPS
```

Put each command on one line. Every run prints `suggested_settings` (frequency and drift change at the end of the window); `--dds` and `--drift` add the new absolute values (`settings_candidate`). The evaluation flags add pages, `evaluation_*.csv` files (printed as `evaluation_files`) and `analysis.json["evaluation"]`; the printed `suggested_settings` are identical with and without them (Franks MSM-GPS, last 30 days: DDS change −526.7884344769938 counts in both).

## T4 monitoring files (FTP ./Monitoring)

`RECMONIT=<s>;` makes the T4 LAN module record its monitoring on the SD card; the files are fetched by FTP from `./Monitoring` and roll over at 2 MB [4, Sec. 1.1.3, p. 12; Sec. 6.5, p. 22]. The manual does not document their layout; the reader (`t4_native.py` 0.0.1) follows the GBO `MonitHCB.dat` of 2026-11-05:

| Property | As found |
|---|---|
| Layout | Tab-separated, CR LF, header `TIME` then `Label [unit]` |
| Time | `DD/MM/YY HH:MM:SS`, **UTC**, maser time stamps |
| Clock not set | Records stamped before the time-server sync (2006, 2007 in the GBO file) are listed, never plotted ("Clock valid from", default 2020-01-01 UTC) |
| Degree sign | Arrived as U+FFFD; repaired to ° |

Outputs: `t4_<layout>_records.csv` (every record, clock flag, file:line), `t4_telemetry.json`, and `t4_telemetry.vszh5` (one panel per column, status, notes; against MJD − MJD0 and local ISO time). Command line: `python t4_native.py MonitHCB.dat --output NEW_DIR`, or `analyze_maser.py ... --t4-telemetry MonitHCB.dat`.

In the GBO file the HCB external temperature reads a constant 80 °C, which suggests no external sensor is connected (inference). The main 40-channel monitoring file has not been seen yet.

## T4 telemetry and live polling

- Files: the recorder's `t4_raw_frames.csv` (re-decoded from raw replies), `t4_telemetry.csv`, or text logs `<MJD or ISO time with offset> $<113 hex>`.
- Live: enter the address (none is built in) and tick the approval box for that endpoint, or use the loopback emulator. The local UDP port defaults to 14000, as in the vendor example and `T4_Maser_Comms.py` (the T4 probably answers to that port; 0 = ephemeral). A recording folder is required: raw replies are saved before decoding, in `T4_telemetry_<YYYYMMDDTHHMMSS±hhmm>`. "Flush RAM preview" and "Offload preview to pickle" bound memory use.
- Command line: `python t4_monitor.py --emulator --count 5 --interval 1 --output <DIR>`.

## Operator script (not part of the workbench)

`tools/T4_Reboot.py` 0.0.4 reboots the T4 M&C software: read-only checks (`GETID`, `RDFS`, `RDREG=DRIFTCOMP`) from local port 14000, then one `REBOOT;` after you type `REBOOT`, a 30 s wait and the same checks again. `REBOOT` has no answer [4, Table 1]; `!REBOOT` means it was rejected. Tested on loopback only; confirm the address and 1 PPS afterwards.

## Files

| Item | Version | Purpose |
|---|---|---|
| `MHM2010_Correction_App.py` | 0.3.0 | GUI entry point (Eval tabs, Evaluate menu, Veusz page checkboxes) |
| `maser_io.py` | 0.2.1 | Inputs |
| `maser_export.py` | 0.3.0 | Outputs (names, tags, exact numbers and times, suggestions, every HMD column; `ExportContent` page choices; evaluation pages and `evaluation_*.csv`) |
| `maser_eval.py` | 0.0.1 | Further evaluation: gap-aware stability with confidence intervals, steering removal, Kalman estimate, three-cornered hat |
| `gui_evaluation.py` | 0.0.1 | The four "Eval:" tabs |
| `export_names.py` | 0.0.1 | Dataset names, Veusz tags, exact number text, 12-digit tick format |
| `maser_core.py` | 0.2.3 | Science (`suggested_settings()`: frequency and drift) |
| `time_format.py`, `time_axis.py` | 0.0.3, 0.0.1 | MJD / ISO 8601 formatting and parsing (exact `mjd_text`); time axis and time field for the GUI |
| `maser_plots.py` | 0.0.3 | Reference figures (Veusz; suggestion box with exact epoch, names, tags) |
| `gui_reference.py` | 0.0.3 | Reference figures (GUI; time display; suggestion box) |
| `t4_protocol.py`, `t4_emulator.py` | 0.0.2 | T4 decoder, emulator |
| `t4_monitor.py` | 0.0.4 | Read-only poller/recorder (local port 14000 default; CSVs with every digit) |
| `t4_native.py` | 0.0.1 | T4 monitoring files (FTP ./Monitoring) |
| `gui_t4.py` | 0.0.3 | T4 telemetry tab |
| `gui_t4_logs.py` | 0.0.2 | T4 monitoring files tab (x axis follows View > Time display) |
| `tools/T4_Reboot.py` | 0.0.4 | Operator reboot script (not used by the workbench) |
| `t4/t4_monit_schema_v0.0.2.json` | 0.0.2 | Channel table per T4S-MAN-0012 Table 6 |
| `vszh5_writer.py` | 0.0.29 | Native Veusz writer (master 0.0.28 + exact ISO text; names widgets after their data) |
| `analyze_maser.py` | 0.3.0 | Batch analysis (prints `suggested_settings`, the exact epoch MJD and `evaluation_files`; page and evaluation flags) |
| `tools/export_fingerprint.py` | 0.0.1 | Fingerprint of a `.vszh5` (dataset hashes, tags, pages, script) to compare exports |
| `tools/manual_audit.py` | 0.0.1 | Checks the manual's rules: a variable table before every equation block, every equation in Appendix B, every table captioned and listed |
| `docs/MHM2010_T4_Workbench_Manual_v0.3.0.pdf/.tex` | 0.3.0 | Bookmarked LuaLaTeX manual, 71 pages: variable table before every equation block, Appendix B (all variables), Appendix C (all stability traces), lists of tables and figures (compile three times with `lualatex`) |
| `docs/VALIDATION.md`, `docs/vszh5_writer_0.0.29_NOTES.md` | 0.3.0 / 0.0.29 | Validation and writer notes (0.0.24 and 0.0.27 notes kept) |
| `docs/writer_0.0.29/` | — | Master 0.0.28, rebuild and patch scripts, 0.0.28→0.0.29 diff |
| `examples/franks_2026_01_07/` | — | Your 2026 Franks files (regression input) |
| `examples/t4_monitoring_2026_11_05/` | — | Your `MonitHCB.dat` (regression input) |
| `docs/PLAN_0.3.0_status_0.2.1.md` | — | Plan 0.3.0 with item status after 0.2.1 |
| `validation/` | — | Test logs, GUI captures, Veusz renders |

## Tests

```powershell
python -m pip install -r requirements-test.txt
python -m pytest tests -q
```

174 tests pass on Python 3.13.18 (Linux, offscreen Qt, America/New_York); 0.2.4 passed 159, 0.2.3 133, 0.2.2 112 on Python 3.12. Veusz 3.6.2 renders of the 0.3.0 export with every page group (33 pages, 13 evaluation pages) are in `validation/veusz_render_v0.3.0/`, GUI captures of the new tabs in `validation/gui_0.3.0/`. Not yet tested: a real T4 (MONIT captures, reply port, the main monitoring file), Windows, Stable32 import, Veusz 4.2.1 for the new pages and the HMD selector codes (steering removal assumes the external DDS drives the output).

## References

| Ref. | IEEE bibliographic entry | DOI or URL |
|---|---|---|
| [1] | Microsemi, *Atomic Hydrogen Maser Frequency Standard Model MHM-2010 User's Guide*, doc. 75666-201, rev. J, Oct. 2014. | https://www.gb.nrao.edu/~fghigo/timer/MSMmaser/Maser_UG_75666-201_Rev_J.PDF |
| [2] | W. J. Riley, *Handbook of Frequency Stability Analysis*, NIST SP 1065, Jul. 2008. | https://tf.nist.gov/general/pdf/2220.pdf |
| [3] | T4Science SA, *iMaser 3000 Installation, Operation and Maintenance User Manual*, T4S-MAN-0012, iss. 1.27, May 2, 2022, Secs. 9.4, 9.6, 9.12, 9.13. | Operator copy (not publicly hosted) |
| [4] | T4Science SA, *T4S-Monitoring Network Software User Manual*, T4S-MAN-0015, ver. 1.17, Jul. 19, 2022: Table 1 (p. 8), Secs. 1.1.2 (p. 11), 1.1.3 (pp. 12–16), 4 (p. 20), 6.1 (p. 21), 6.5 (p. 22), 6.7 (p. 24). | Operator copy (not publicly hosted) |
| [5] | W. W. Wallace, "GBO_Maser_Control" (T4_Maser_Comms.py, T4_channel_names.csv), commit c72244d, accessed Nov. 3, 2026. | https://github.com/Braveheart53/GBO_Maser_Control |
| [6] | W. W. Wallace, "FitsAutoPlot" (FITS_AutoPlot.py, Franks_AutoPlot.py), commit 36add38, accessed Nov. 3, 2026. | https://github.com/Braveheart53/FitsAutoPlot |
| [7] | Safran, "iMaser 3000," datasheet, Mar. 11, 2024. | https://safran-navigation-timing.com/wp-content/uploads/2024/03/SAFRAN_IMASER-3000_Datasheet_3-11-2024_US.pdf |
| [8] | W. J. Riley, *Stable32 User Manual*, ver. 1.54, Sep. 7, 2008. | http://www.stable32.com/Manual154.pdf |
| [9] | ISO, *Date and time — Representations for information interchange — Part 1*, ISO 8601-1:2019. | https://www.iso.org/standard/70907.html |
| [10] | Veusz Developers, *Veusz* 3.6.2 source: `utils/formatting.py` (`%Vg`), `dialogs/dataeditdialog.py` (table display), `qtwidgets/datasetbrowser.py` (tags). | https://pypi.org/project/veusz/3.6.2/ |
| [11] | IEEE, *IEEE Standard for Floating-Point Arithmetic*, IEEE Std 754-2019, Jul. 2019 (binary64: 17 significant digits round-trip). | https://doi.org/10.1109/IEEESTD.2019.8766229 |
| [12] | Python Software Foundation, "decimal — Decimal fixed point and floating point arithmetic," *Python 3 documentation* (exact time text). | https://docs.python.org/3/library/decimal.html |
| [13] | W. J. Riley and C. A. Greenhall, "Power law noise identification using the lag 1 autocorrelation," in *Proc. 18th European Frequency and Time Forum (EFTF 2004)*, Guildford, U.K., 2004. | https://doi.org/10.1049/cp:20040932 (preprint http://www.wriley.com/Paper125Preprint.pdf) |
| [14] | L. Galleani and P. Tavella, "Time and the Kalman filter," *IEEE Control Systems Magazine*, vol. 30, no. 2, pp. 44–65, 2010. | https://doi.org/10.1109/MCS.2009.935568 |
| [15] | C. Zucca and P. Tavella, "The clock model and its relationship with the Allan and related variances," *IEEE Trans. Ultrason., Ferroelectr., Freq. Control*, vol. 52, no. 2, pp. 289–296, Feb. 2005. | https://doi.org/10.1109/TUFFC.2005.1406554 |
| [16] | T. Ishizaki, T. Ichimura, T. Kawaguchi, Y. Yano and Y. Hanado, "Higher-order Allan variance for atomic clocks of arbitrary order: mathematical foundation," *Metrologia*, 2023, Eq. (23b); arXiv:2302.05556v3. | https://doi.org/10.1088/1681-7575/ad02fe |
| [17] | J. E. Gray and D. W. Allan, "A method for estimating the frequency stability of an individual oscillator," in *Proc. 28th Annu. Symp. Frequency Control*, 1974, pp. 243–246. | https://tf.nist.gov/general/pdf/57.pdf |
| [18] | H. E. Rauch, F. Tung and C. T. Striebel, "Maximum likelihood estimates of linear dynamic systems," *AIAA Journal*, vol. 3, no. 8, pp. 1445–1450, Aug. 1965. | https://doi.org/10.2514/3.3166 |
| [19] | I. Sesia and P. Tavella, "Estimating the Allan variance in the presence of long periods of missing data and outliers," *Metrologia*, vol. 45, pp. S134–S142, 2008. | https://hdl.handle.net/11696/29548 |
| [20] | Microsemi, "MHM 2010 Active Hydrogen Maser," datasheet DS/MHM2010/082714, 2014. | https://dtsheet.com/doc/1889985/mhm-2010 |
| [21] | A. E. E. Wallin *et al.*, "AllanTools," Python package, ver. 2024.06 (test cross-checks). | https://github.com/aewallin/allantools |
