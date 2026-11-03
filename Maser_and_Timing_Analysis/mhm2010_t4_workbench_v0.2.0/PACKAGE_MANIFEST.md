# MHM2010 / T4 Maser Timing Workbench — Delivery Manifest

Application 0.2.0; writer 0.0.24 (built on master 0.0.23); T4 schema 0.0.2; documentation 0.2.0. Build date: 2026-11-03. Author: William W. Wallace.

- Runnable Python 3.12 source with headers, revision histories and Spyder `# %%` cells; requirements (runtime, tested pins, test) and a Windows launcher.
- GUI (qtpy/PySide6, pyqtgraph): MHM2010 and T4 settings, reference 1 PPS figures, T4 telemetry tab (files and read-only live polling), Light/Dark theme menu.
- T4 support: strict MONIT decoder, schema checked against T4S-MAN-0012 Table 6, read-only poller and recorder, loopback emulator, SETFSREL/DRIFTCOMP model.
- Writer 0.0.24 with MJD and local ISO 8601 time-axis helpers, plus release notes.
- A bookmarked 20-page LuaLaTeX manual (PDF and source), README, CHANGELOG and VALIDATION.
- Example input: the operator's Franks files `examples/franks_2026_01_07/` (2026-01 to 2026-07). The 0.1.0 historical FITS/HMD example is retained.
- Validation: pytest log (79 passed), environment record, GUI captures, and a real-data Veusz render (`validation/veusz_render_v0.2.0/`, 25 pages, bookmarked PDF).
- T4 plan 0.1.0 with a 0.2.0 status note (`docs/t4_plan_v0.1.0/`).
- SHA-256 checksums of every packaged file (`SHA256SUMS.txt`).

Not included: the T4 manuals (operator copies, not for redistribution), the full HMD and the FITS archive (operator inputs).

No Git push was made, and no command other than documented read-only queries can be sent to a maser. Live T4 polling was exercised only against the loopback emulator.
