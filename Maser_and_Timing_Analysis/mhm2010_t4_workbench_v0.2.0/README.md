# MHM2010 / T4 Maser Timing Workbench

Version **0.2.0**, 2026-11-03. Python 3.12, qtpy with PySide6, pyqtgraph. Author: William W. Wallace (NRAO/GBO). GPL-3.0-or-later.

The workbench analyses 1 PPS timing deltas (FITS OnePpsDeltas and/or Franks `.t00new`). It reproduces your two reference figures and suggests settings for the **MHM2010** (external DDS, drift) and the **T4 iMaser 3000** (`SETFSREL`, `DRIFTCOMP`). It also reads MHM2010 HMD and T4 MONIT telemetry. **It never sends a setting to a maser.** The T4 tab can send only the documented read-only queries `MONIT;`, `RDFS;`, `GETID;` and `RDREG=…;` [4].

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
| Select interval | Blue region, run list ("All data" bridges gaps) | "Allow fit across acquisition gaps" is on for Franks; each bridged gap is reported |
| Reference figures | Tab "1 PPS reference plots" | Deltas + statistics; data + fit over residuals; editable title/labels |
| Settings | Fit & corrections | MHM2010: DDS, drift (both units). T4: SETFSREL (with predicted RDFS), DRIFTCOMP |
| T4 state | T4 tab > Read synthesizer and DRIFTCOMP state | Fills the RDFS and DRIFTCOMP fields (read-only) |
| Export | File > Export analysis folder | `analysis.vszh5` (reference pages first; every time page against MJD and local ISO time), CSV, JSON, review, Stable32 |

The figure's "synth adjustment" uses the straight-line mean rate over the plotted span, as your figures do. The settings panel evaluates the selected model at the application epoch. Enter only reviewed settings-panel values.

### T4 settings in one line each [3], [4]

- Frequency: y₀ = −Δν_synth / (284 × 5 MHz) (Eq. 12). The synthesizer step is 5 MHz/2³⁹ = 9.0949 µHz (6.4×10⁻¹⁵ relative). The panel prints `SETFSREL=±x;` and the RDFS value expected afterwards. **Confirm the SETFSREL sign by comparing RDFS before and after.**
- Drift: `DRIFTCOMP_new = DRIFTCOMP_now − measured drift/day`, with |x| between 10⁻¹⁷ and 8×10⁻¹³. Send REBOOT afterwards, then verify with `RDREG=DRIFTCOMP;`.

## Batch

```text
python analyze_maser.py --timing-directory <FRANKS_OR_FITS_DIR> --channel MSM-GPS --order 1
  --bridge-gaps --last-days 30.9 --dds 5751.6770000 --output <NEW_DIR>
python analyze_maser.py --timing-directory <DIR> --maser T4 --channel "<T4 CHANNEL>"
  --t4-synth <RDFS Hz> --t4-driftcomp <RDREG value> --output <NEW_DIR>
```

Put each command on one line.

## T4 telemetry and live polling

- Files: the recorder's `t4_raw_frames.csv` (re-decoded from raw replies), `t4_telemetry.csv`, or text logs `<MJD or ISO time with offset> $<113 hex>`.
- Live: enter the address (none is built in) and tick the approval box for that endpoint, or use the loopback emulator. A recording folder is required: raw replies are saved before decoding, in `T4_telemetry_<YYYYMMDDTHHMMSS±hhmm>`. "Flush RAM preview" and "Offload preview to pickle" bound memory use.
- Command line: `python t4_monitor.py --emulator --count 5 --interval 1 --output <DIR>`.

## Files

| Item | Version | Purpose |
|---|---|---|
| `MHM2010_Correction_App.py` | 0.2.0 | GUI entry point |
| `maser_io.py`, `maser_core.py`, `maser_export.py` | 0.2.0 | Inputs, science, outputs |
| `maser_plots.py`, `gui_reference.py` | 0.0.1 | Reference figures (Veusz, GUI) |
| `t4_protocol.py`, `t4_monitor.py`, `t4_emulator.py` | 0.0.2 | T4 decoder, read-only poller/recorder, emulator |
| `gui_t4.py` | 0.0.1 | T4 telemetry tab |
| `t4/t4_monit_schema_v0.0.2.json` | 0.0.2 | Channel table per T4S-MAN-0012 Table 6 |
| `vszh5_writer.py` | 0.0.24 | Native Veusz writer (master 0.0.23 + time-axis helpers) |
| `analyze_maser.py` | 0.2.0 | Batch analysis |
| `docs/MHM2010_T4_Workbench_Manual_v0.2.0.pdf/.tex` | 0.2.0 | Bookmarked LuaLaTeX manual (compile twice with `lualatex`) |
| `docs/VALIDATION.md`, `docs/vszh5_writer_0.0.24_NOTES.md` | 0.2.0 | Validation and writer notes |
| `examples/franks_2026_01_07/` | — | Your 2026 Franks files (regression input) |
| `validation/` | — | Test logs, GUI captures, Veusz renders |

## Tests

```powershell
python -m pip install -r requirements-test.txt
python -m pytest tests -q
```

79 tests pass on Python 3.12.15 (Linux, offscreen Qt). Not yet tested: a real T4, Windows, Stable32 import and Veusz 4.2.1 for the new pages.

## References

| Ref. | IEEE bibliographic entry | DOI or URL |
|---|---|---|
| [1] | Microsemi, *Atomic Hydrogen Maser Frequency Standard Model MHM-2010 User's Guide*, doc. 75666-201, rev. J, Oct. 2014. | https://www.gb.nrao.edu/~fghigo/timer/MSMmaser/Maser_UG_75666-201_Rev_J.PDF |
| [2] | W. J. Riley, *Handbook of Frequency Stability Analysis*, NIST SP 1065, Jul. 2008. | https://tf.nist.gov/general/pdf/2220.pdf |
| [3] | T4Science SA, *iMaser 3000 Installation, Operation and Maintenance User Manual*, T4S-MAN-0012, iss. 1.27, May 2, 2022, Secs. 9.4, 9.6, 9.12, 9.13. | Operator copy (not publicly hosted) |
| [4] | T4Science SA, *T4S-Monitoring Network Software User Manual*, T4S-MAN-0015, ver. 1.17, Jul. 19, 2022. | Operator copy (not publicly hosted) |
| [5] | W. W. Wallace, "GBO_Maser_Control" (T4_Maser_Comms.py, T4_channel_names.csv), commit c72244d, accessed Nov. 3, 2026. | https://github.com/Braveheart53/GBO_Maser_Control |
| [6] | W. W. Wallace, "FitsAutoPlot" (FITS_AutoPlot.py, Franks_AutoPlot.py), commit 36add38, accessed Nov. 3, 2026. | https://github.com/Braveheart53/FitsAutoPlot |
| [7] | Safran, "iMaser 3000," datasheet, Mar. 11, 2024. | https://safran-navigation-timing.com/wp-content/uploads/2024/03/SAFRAN_IMASER-3000_Datasheet_3-11-2024_US.pdf |
| [8] | W. J. Riley, *Stable32 User Manual*, ver. 1.54, Sep. 7, 2008. | http://www.stable32.com/Manual154.pdf |
| [9] | ISO, *Date and time — Representations for information interchange — Part 1*, ISO 8601-1:2019. | https://www.iso.org/standard/70907.html |
