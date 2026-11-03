# Revision History

## Workbench 0.1.0, 2026-11-03

- FITS/FITS.GZ primary ingestion, separate Franks reduced-data ingestion, HMD housekeeping.
- qtpy/PySide6 GUI with pyqtgraph interval selection, exclusions, phase/residual fitting, telemetry and timing overview.
- Linear and quadratic phase models with DDS correction and both drift-unit representations.
- OADEV, MDEV, TDEV and OHDEV, explicit sampling tolerance and no interpolation.
- Native h5py Veusz export, Stable32 PHD/FRD, CSV, JSON and reproducible settings.
- Bookmarked LuaLaTeX PDF manual and regression tests.

## Bundled writer 0.0.22, 2026-11-03

The supplied 0.0.21 writer accepted a multidimensional date array. This could produce an invalid date dataset, or flatten it silently in resizable mode. `dataset_date()` now rejects non-1-D input before creating the dataset group. Existing valid calls and all other writer behavior are unchanged. The upstream revision history and license remain intact.
