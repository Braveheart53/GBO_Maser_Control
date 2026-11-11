# MHM2010 / T4 Maser Timing Workbench — Delivery Manifest

Application 0.3.0; writer 0.0.29 (unchanged from 0.2.4: registry master 0.0.28 + exact ISO 8601 text); T4 schema 0.0.2; documentation 0.3.0. Build date: 2026-11-11. Author: William W. Wallace.

- Runnable Python source (3.12; also tested on 3.13) with headers, revision histories and Spyder `# %%` cells; requirements (runtime, tested pins, test) and a Windows launcher. No new runtime dependency in 0.3.0 (SciPy, already required, provides the chi-squared and normal quantiles and the non-negative least squares).
- GUI (qtpy/PySide6, pyqtgraph): MHM2010 and T4 settings, reference 1 PPS figures, T4 telemetry tab (files and read-only live polling), T4 monitoring files tab, Light/Dark theme menu, View > Time display and fit windows, Veusz export dialog, both suggested changes in the Results box and on the reference fit figure.
- **0.3.0: further evaluation** in four tabs and an Evaluate menu (`maser_eval.py` 0.0.1, `gui_evaluation.py` 0.0.1): gap-aware stability with confidence intervals (NIST SP 1065), steering removal from the HMD DDS record, Kalman estimate (three-state clock model, RTS smoother), three-cornered hat. They never change the base fit or the suggested settings (tested).
- **0.3.0: Veusz pages to generate**: OUTPUT checkboxes and batch flags (`maser_export.ExportContent`); defaults give the 0.2.4 file (identical by fingerprint, `tools/export_fingerprint.py` 0.0.1); evaluation pages `GapStab_*`, `Steer_*`, `Kalman_*`, `TCH_allan_deviation` and the files `evaluation_*.csv`.
- Exports: exact numbers everywhere (CSV, Stable32, review.md, strict JSON, Veusz text datasets), meaningful dataset/trace/page names, Veusz tags `source_*` and `type_*`, 12-digit numeric ticks, every HMD column, `Suggested_*` datasets, exact time text.
- T4 support: strict MONIT decoder (schema checked against T4S-MAN-0012 Table 6), read-only poller and recorder (local port 14000 by default), loopback emulator, SETFSREL/DRIFTCOMP model, reader for the T4's own monitoring files.
- Operator script `tools/T4_Reboot.py` 0.0.4 (separate from the workbench; loopback-tested).
- A bookmarked 71-page LuaLaTeX manual 0.3.0 (PDF and source): a variable table before every equation block, Appendix A (equations), Appendix B (all variables), Appendix C (all stability traces), lists of tables and figures in the contents and the bookmarks. README, CHANGELOG, VALIDATION, writer notes (0.0.24, 0.0.27 and 0.0.29), and the 0.3.0 plan status of 0.2.1.
- Example inputs: the operator's Franks files `examples/franks_2026_01_07/` and `examples/t4_monitoring_2026_11_05/MonitHCB.dat`; the 0.1.0 historical FITS/HMD example is retained (its `analysis.vszh5` is the 0.1.0 output, kept unchanged as a historical record).
- Validation: pytest log (174 passed), environment record, GUI captures (`validation/gui_0.3.0/` and earlier), Veusz renders (`validation/veusz_render_v0.2.0/`, `v0.2.1/`, `v0.2.3/`, `v0.2.4/`, `v0.3.0/` with every 0.3.0 page and the full rendered PDF).
- SHA-256 checksums of every packaged file (`SHA256SUMS.txt`).

Not included: the T4 manuals (operator copies, not for redistribution), the full HMD and the FITS archive (operator inputs).

No Git push was made. The workbench sends no command other than the documented read-only queries; live T4 polling and the reboot script were exercised only on loopback.
