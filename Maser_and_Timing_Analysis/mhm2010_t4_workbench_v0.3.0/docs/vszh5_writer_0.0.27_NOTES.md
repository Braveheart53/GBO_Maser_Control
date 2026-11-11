---
title: vszh5_writer in workbench 0.2.3 - copy notes
author: William W. Wallace (NRAO/GBO), with Claude
date: 2026-11-10
version: 0.0.27 (bundled copy; registry master is 0.0.28)
licence: GPL-3.0-or-later
supersedes: the 0.0.24 copy bundled with workbench 0.2.0 to 0.2.2
---

# vszh5_writer in workbench 0.2.3

The workbench now ships writer **0.0.27**, copied unchanged from the FitsAutoPlot working folder on bracca
(`C:\Users\wwallace\Claude\Projects\GBO_PostProcessing\FitsAutoPlot\vszh5_writer.py`, 201,312 bytes,
SHA-256 `992e46a46701331f70b44556e5b252237eb037ebe87ba573e655252d40be2369`). No line was changed.

| Item | Value |
|---|---|
| Bundled copy | 0.0.27, SHA-256 `992e46a4…40be2369` |
| Registry master | 0.0.28 (RF project document `claude/vszh5_writer.py`; SHA-256 `13f68513…f634a64b5`) |
| Difference 0.0.27 → 0.0.28 | Faster saving only; the registry records identical files (every dataset, attribute and script) |
| Why not 0.0.28 | 0.0.28 exists only as the RF project document; the newest writer **file** in the operator's folders is 0.0.27. Shipping it unchanged avoids a third, unregistered variant. 0.0.28 is a drop-in replacement. |

## What the workbench uses from 0.0.25 to 0.0.27

- Widget names from the data (0.0.25): `xy_page`/`grid_page` graphs `g_<y>` (or `Panel.name`), traces named after their y dataset. With the workbench's dataset names (`export_names.py`) the Veusz widget tree reads `g_Fit_MSM_GPS_residual_ns / Fit_MSM_GPS_residual_ns` instead of `graph / trace0`.
- Everything from 0.0.24 (MJD and local ISO 8601 axes) is unchanged.
- Not used: page notes, Smith charts, gap helpers.

## Compatibility check

The workbench sets no setting by path inside a helper-made widget (the reference figures build their own widgets), so the 0.0.25 renaming needed no workbench change. The only test that failed with the new copy pinned the version string (`0.0.24`); it now requires 0.0.27 or later. 133 of 133 tests pass. Veusz 3.6.2 renders of the new pages: `validation/veusz_render_v0.2.3/`.

## Registry

Copies table entry: *MHM2010 / T4 Maser Timing Workbench 0.2.3 (bracca, `Projects\Maser Analysis\mhm2010_t4_workbench_v0.2.3`) — 0.0.27 — `992e46a46701331f` — drop-in 0.0.28 when its file is available.*
