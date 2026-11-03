# MHM2010 Correction Workbench

Version **0.1.0**, 2026-11-03. Python 3.12, qtpy with PySide6 by default, and pyqtgraph throughout the GUI. This is an offline analysis tool: it never communicates with masermon or a maser.

## Start

Create an environment inside the extracted application folder, install dependencies, and launch. On Windows:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe MHM2010_Correction_App.py
```

Alternatively, use `launch_windows.cmd` after installation. In Spyder, select that environment's interpreter and run the entry-point script; the source is divided into Spyder cells. A GUI event loop already running in an IDE may require running the script in a dedicated console.

## Workflow

- **Timing inputs:** Select multiple OnePpsDeltas `.fits` or `.fits.gz` files. FITS is preferred; `.t00new` Franks files are an independent reduced-data alternative, not merged with raw samples.
- **Housekeeping:** Load `.HMD` separately. Select an uninterrupted timing run, inspect DDS and selector events, and exclude inappropriate intervals.
- **Live fit:** Drag the blue fit interval or enter MJD limits. Use the red selector for explicit exclusions. Choose linear phase for frequency only, quadratic phase for frequency and frequency drift.
- **Settings:** Enter DDS, current enabled drift, drift input units, and the application epoch. The optional HMD lookup is historical and requires a record within 120 s. It does not determine the current drift field.
- **Both drift options:** Results show DDS units/day and parts in \(10^{18}\)/day simultaneously, with current value, increment, new value and optional integer rounding.
- **Review:** Verify measured-channel direction and the installed masermon pp18 sign convention. Compare 7/14/21/30-day fits; substantial changes mean the drift estimate is not stable enough to treat as a maser aging constant.
- **Stability:** Select raw or detrended data and an explicit sampling tolerance. No gaps are filled. The default 1% rejects the supplied full month; 2% admits its approximately 3 s maximum grid deviation on a nominal 270 s cadence.
- **Export:** Choose an output parent. The application creates a new analysis folder containing `.vszh5`, CSV, JSON, a review note, and optionally Stable32 `.PHD`/`.FRD` files.

The output-frequency/DDS relationship and the two drift-unit choices are documented by Microsemi [1]. The drift sign convention, allowed range and update cadence are not specified in the inspected Rev. J manual; they must not be assumed from the displayed units alone [1].

## Included deliverables

| Item | Purpose |
|---|---|
| `MHM2010_Correction_App.py` | GUI entry point |
| `maser_io.py`, `maser_core.py`, `maser_export.py` | Input, scientific and output modules |
| `analyze_maser.py` | Reproducible batch analysis |
| `vszh5_writer.py` | Supplied writer corrected to 0.0.22 |
| `docs/MHM2010_Workbench_Manual_v0.1.0.pdf` | Bookmarked operator/scientific manual |
| `docs/MHM2010_Workbench_Manual_v0.1.0.tex` | Editable LuaLaTeX source; compile twice with `lualatex` |
| `docs/VALIDATION.md` | Test scope, real-data findings and explicit limitations |
| `examples/historical_20251015_20251113/` | Real historical analysis, not a present-day adjustment |
| `validation/` | Test log, GUI captures, Veusz-rendered plots, environment versions |
| `tests/` | Numerical, parser, GUI and writer regression tests |

The example uses October 15 through November 13, 2025, 9,600 direct MicroSem-to-CNS_GPS1 samples. Its assumed old drift value of +975 pp18/day comes from an older supplied screenshot and is **not proven to be the November setting**. Do not enter its candidate values into a live maser. The stability estimators follow NIST SP 1065 and describe the complete comparison system, not maser-only noise [2].

## Tests and reproducibility

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m pytest tests -q
```

The numerical tests compare all four stability estimators against AllanTools. Windows interaction, actual Stable32 import and physical maser operation have not been tested in this cloud environment. Native `.vszh5` files were opened and rendered by a separate Veusz installation; the application itself does not import Veusz.

To repeat the historical analysis, unpack the original FITS archive to a selected directory and provide the original HMD file:

```text
python analyze_maser.py --fits-directory <FITS_DIRECTORY> --hmd <HMD_FILE>
  --start 2025-10-15 --end 2025-11-14 --derive-dds --drift 975
  --sampling-tolerance .02 --stable32 --output <NEW_OUTPUT_DIRECTORY>
```

Join that command onto one line. Replace placeholders locally; no personal directory paths are embedded in the scripts. The command's drift and sign options are explicit assumptions, not an automatic determination of instrument state.

## Equation appendix

All implemented equations, including both drift conversions, OLS covariance, four stability estimators, timing conversion, rejection thresholds and Stable32 differencing, are displayed in Appendix A of the supplied LuaLaTeX/PDF manual. The README contains no independent scientific derivations.

## Reference table

| Ref. | Full IEEE bibliographic entry | DOI or URL |
|---|---|---|
| [1] | Microsemi, Atomic Hydrogen Maser Frequency Standard Model MHM-2010 User’s Guide, doc. 75666-201, rev. J, Oct. 2014, pp. 14, 20, 44. Accessed Nov. 3, 2026. | https://www.gb.nrao.edu/~fghigo/timer/MSMmaser/Maser_UG_75666-201_Rev_J.PDF |
| [2] | W. J. Riley, Handbook of Frequency Stability Analysis, NIST Special Publication 1065, Jul. 2008. Accessed Nov. 3, 2026. | https://tf.nist.gov/general/pdf/2220.pdf |
| [3] | W. J. Riley, Stable32 User Manual, ver. 1.54, Sep. 7, 2008. Accessed Nov. 3, 2026. | http://www.stable32.com/Manual154.pdf |
