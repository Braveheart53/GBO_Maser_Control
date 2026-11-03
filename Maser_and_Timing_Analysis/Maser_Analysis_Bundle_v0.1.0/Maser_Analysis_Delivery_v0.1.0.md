# Maser Analysis Delivery

Bundle version 0.1.0, 2026-11-03. This bundle contains the completed MHM2010 software release and the separately prepared Safran T4 planning package.

## Contents

- **`mhm2010_workbench_v0.1.0/`:** Python 3.12 application, writer 0.0.22, 36 tests, bookmarked PDF and LuaLaTeX manual, native Veusz examples, Stable32 files, plots and validation.
- **`safran_t4_plan_v0.1.0/`:** Planning-only design, source-review findings, draft 41-channel schema, acceptance gates, bookmarked PDF and LuaLaTeX. No T4 implementation or live acquisition is claimed.

The MHM2010 archive was posted before T4 research and planning began, as requested. The combined bundle is a convenience delivery and does not change the previously posted MHM2010 source release.

## Important limits

The historical MHM2010 candidate is not approved for live entry. The measured drift is sensitive to fitting window, and the example's old drift field is an explicit assumption. No maser writes or Git pushes occurred.

Bracca was not available in the device inventory, so all staging and testing occurred in the cloud workspace. Local transfer remains pending; any later transfer must stay inside the previously authorized directory.

Begin with `mhm2010_workbench_v0.1.0/README.md`. Consult the T4 plan for the manual, captures and channel-identity information required before implementation.
