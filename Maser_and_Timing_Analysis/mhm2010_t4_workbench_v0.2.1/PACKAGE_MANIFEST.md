# MHM2010 / T4 Maser Timing Workbench — Delivery Manifest

Application 0.2.1; writer 0.0.24 (built on master 0.0.23); T4 schema 0.0.2; documentation 0.2.1. Build date: 2026-11-05. Author: William W. Wallace.

- Runnable Python 3.12 source with headers, revision histories and Spyder `# %%` cells; requirements (runtime, tested pins, test) and a Windows launcher. No new dependencies in 0.2.1.
- GUI (qtpy/PySide6, pyqtgraph): MHM2010 and T4 settings, reference 1 PPS figures, T4 telemetry tab (files and read-only live polling), **T4 monitoring files tab (new)**, Light/Dark theme menu.
- T4 support: strict MONIT decoder (schema checked against T4S-MAN-0012 Table 6), read-only poller and recorder (local port 14000 by default), loopback emulator, SETFSREL/DRIFTCOMP model, **reader for the T4's own monitoring files (new)**.
- Operator script `tools/T4_Reboot.py` 0.0.4 (separate from the workbench; loopback-tested).
- Writer 0.0.24 with MJD and local ISO 8601 time-axis helpers (unchanged since 0.2.0).
- A bookmarked 24-page LuaLaTeX manual (PDF and source), README, CHANGELOG, VALIDATION, and the 0.3.0 plan with status.
- Example inputs: the operator's Franks files `examples/franks_2026_01_07/` and `examples/t4_monitoring_2026_11_05/MonitHCB.dat`; the 0.1.0 historical FITS/HMD example is retained.
- Validation: pytest log (103 passed), environment record, GUI captures, Veusz renders (`validation/veusz_render_v0.2.0/`, `validation/veusz_render_v0.2.1/`).
- SHA-256 checksums of every packaged file (`SHA256SUMS.txt`).

Not included: the T4 manuals (operator copies, not for redistribution), the full HMD and the FITS archive (operator inputs).

No Git push was made. The workbench sends no command other than the documented read-only queries; live T4 polling and the reboot script were exercised only on loopback.
