# MHM2010 / T4 Maser Timing Workbench — Delivery Manifest

Application 0.2.3; writer 0.0.27 (copied unchanged from the FitsAutoPlot folder on bracca; registry master 0.0.28 writes identical files); T4 schema 0.0.2; documentation 0.2.3. Build date: 2026-11-10. Author: William W. Wallace.

- Runnable Python source (3.12; also tested on 3.13) with headers, revision histories and Spyder `# %%` cells; requirements (runtime, tested pins, test) and a Windows launcher. No new dependencies in 0.2.1 to 0.2.3.
- GUI (qtpy/PySide6, pyqtgraph): MHM2010 and T4 settings, reference 1 PPS figures, T4 telemetry tab (files and read-only live polling), T4 monitoring files tab, Light/Dark theme menu, View > Time display and fit windows (0.2.2), Veusz export dialog, **both suggested changes — DDS counts and drift (T4: SETFSREL and DRIFTCOMP) — in the Results box and on the reference fit figure (0.2.3)**.
- Exports (0.2.3): exact numbers everywhere (CSV, Stable32, review.md, JSON, Veusz text datasets), meaningful dataset/trace/page names, Veusz tags `source_*` and `type_*`, 12-digit numeric ticks, every HMD column, `Suggested_*` datasets. New `export_names.py` 0.0.1.
- T4 support: strict MONIT decoder (schema checked against T4S-MAN-0012 Table 6), read-only poller and recorder (local port 14000 by default), loopback emulator, SETFSREL/DRIFTCOMP model, reader for the T4's own monitoring files.
- Operator script `tools/T4_Reboot.py` 0.0.4 (separate from the workbench; loopback-tested).
- A bookmarked 32-page LuaLaTeX manual (PDF and source), README, CHANGELOG, VALIDATION, writer notes (0.0.24 and 0.0.27), and the 0.3.0 plan with status.
- Example inputs: the operator's Franks files `examples/franks_2026_01_07/` and `examples/t4_monitoring_2026_11_05/MonitHCB.dat`; the 0.1.0 historical FITS/HMD example is retained (its `analysis.vszh5` is the 0.1.0 output, kept unchanged as a historical record).
- Validation: pytest log (133 passed), environment record, GUI captures, Veusz renders (`validation/veusz_render_v0.2.0/`, `v0.2.1/`, `v0.2.3/`).
- SHA-256 checksums of every packaged file (`SHA256SUMS.txt`).

Not included: the T4 manuals (operator copies, not for redistribution), the full HMD and the FITS archive (operator inputs).

No Git push was made. The workbench sends no command other than the documented read-only queries; live T4 polling and the reboot script were exercised only on loopback.
