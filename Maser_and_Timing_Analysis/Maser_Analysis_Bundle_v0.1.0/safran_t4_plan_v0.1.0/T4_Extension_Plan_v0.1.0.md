# Safran T4 i3000 Extension Plan

Version 0.1.0, 2026-11-03. Planning began after MHM2010 Correction Workbench 0.1.0 was posted for download. This is a staged design and source review, not implemented T4 support; no instrument was contacted and no repository was pushed.

## Evidence and boundary

The reviewed `GBO_Maser_Control` snapshot is commit `9682a2ffbcd0b0df6612d4aef3619d7bdaf6cc66`. Its T4 examples use IPv4 UDP, port 14000 in the examples, and the command `MONIT;\r\n`; the decoder expects a dollar-prefixed payload containing 113 hexadecimal characters [1] ([T4 communication source](https://raw.githubusercontent.com/Braveheart53/GBO_Maser_Control/main/T4_Maser_Comms.py)).

The first-party Safran datasheet describes Ethernet monitoring and remote functions, but does not provide the synthesizer transfer equation, drift-control units/sign, or the complete MONIT protocol [2] ([Safran datasheet](https://safran-navigation-timing.com/wp-content/uploads/2024/03/SAFRAN_IMASER-3000_Datasheet_3-11-2024_US.pdf)). The repository comments refer to page 47 of a T4 operation manual; that manual was not obtained and verified in this effort [1] ([T4 communication source](https://raw.githubusercontent.com/Braveheart53/GBO_Maser_Control/main/T4_Maser_Comms.py)).

**Do not reuse the MHM2010 DDS conversion for T4.** Reuse the phase-fitting, stability, provenance and export infrastructure only; keep the T4 actuator mapping disabled until its units, gain, sign, epoch and existing correction state are documented.

## Source review findings

The following are static-code findings, not reports of observed instrument behavior [1] ([T4 communication source](https://raw.githubusercontent.com/Braveheart53/GBO_Maser_Control/main/T4_Maser_Comms.py)).

| Area | Finding | Planned treatment |
|---|---|---|
| UDP retries | Timeout closes the socket; the next iteration attempts to reuse it. Binding also occurs inside the loop. | One explicitly owned socket lifecycle, or a fresh socket per attempt; bounded retries, cancellation and fault-injection tests. |
| Failure return | Communication returns bytes or None, while the annotation says optional string; caller decodes before checking None. | Typed bytes result or structured error; decode only after successful response validation. |
| Peer validation | Reply address is received but not checked against the intended instrument. | Validate sender and permitted reply port; isolate devices and reject unrelated/stale datagrams. |
| Parser framing | Empty input is indexed; missing dollar delimiter and trailing characters are handled loosely. | Strict bytes framing, delimiter, exact payload size, ASCII-hex character and terminator checks; no `str(bytes)` heuristics. |
| Error handling | Parser errors open Tk dialogs and call `sys.exit`. | Pure parser raises typed exceptions; qtpy GUI handles them without terminating acquisition. |
| Status channel 39 | A field labeled LOCK 100 MHz status is multiplied by 0.03906 as if analog. | Preserve the raw byte; do not call it a boolean or engineering voltage until the manual resolves its meaning. |
| Status channel 40 | Comment describes a one-bit/MSB interpretation; implementation returns the whole last nibble. | Preserve raw nibble; gate status interpretation on the manual and known locked/unlocked captures. |
| Missing channels | NaN-gain channels are omitted. | Keep channel identity with `installed/available=false`, raw code retained, engineering value null. |
| Import side effects | Companion examples execute a request at module top level. | Do not import them into the GUI. New modules must be inert on import. |
| Configuration | Examples embed a site endpoint. | No endpoint or personal paths in generated code; explicit user configuration only. |

## Proposed architecture

- **Pure decoder:** `t4_protocol.py` accepts bytes and a versioned schema, returning raw codes, engineering values and explicit quality flags. It has no sockets, GUI calls, file writes or process exits.
- **Read-only acquisition:** `t4_monitor.py` exposes an allowlist containing only a verified monitoring command. It records request/receive UTC, monotonic round-trip time, source endpoint identifier, response bytes, attempt number and parse status.
- **Port ownership:** Begin with a single listener and serialized requests using the example's same-local-port behavior. Do not assume ephemeral local ports work. Multiple devices sharing a local port require a dispatcher and peer isolation rather than competing binds.
- **Persistence:** Store raw frames before scaled values, then write versioned normalized telemetry. Record schema hash, firmware/instrument identity and missing-channel masks. Timestamps from the acquisition computer are receive timestamps, not precision timestamps from the maser.
- **GUI:** Add a separate T4 telemetry tab using qtpy/PySide6 and pyqtgraph. Reuse in-place curve updates, bounded preview arrays, min/max display reduction, linked axes, crosshair readouts and a legend/value column, following the AB live-plot exemplar [3] ([GBO control repository](https://github.com/Braveheart53/GBO_Maser_Control/tree/main)).
- **Timing join:** Load a measured T4-to-reference FITS pair through an explicit channel map. Do not assume an existing `SigmaTau` or `MicroSem` label means T4. Preserve raw and reduced timing independently.
- **Analysis:** Reuse polynomial fitting, window sensitivity, exclusions, OADEV/MDEV/TDEV/OHDEV, native Veusz and Stable32 after validating that the timing signal is a clock-comparison measurement.
- **Actuator adapter:** `T4CorrectionModel` remains unavailable until OEM equations, field names, current-state readback and sign conventions are verified. The first T4 release should remain read-only and export advisory calculations only.

The AB monitor exemplar uses qtpy and pyqtgraph, in-place curve updates, bounded/downsampled previews and per-device identity displays; these are suitable patterns to preserve, not a reason to copy its complete meter-specific storage and network layers [3] ([GBO control repository](https://github.com/Braveheart53/GBO_Maser_Control/tree/main)).

## Staged schema and test inventory

`t4_channel_schema_draft_v0.1.0.json` contains 41 channel identities and the gains from the reviewed repository, with provenance and unverified status fields. It is a draft for review, not an approved calibration table. No host address, credentials or source-machine path is included.

| Test family | Required cases before a T4 release |
|---|---|
| Framing | Known valid frame; empty, truncated, oversized, missing `$`, bad hex, extra bytes and wrong terminator. |
| Scaling | Zero, midscale and maximum code; negative supply gain; all absent channels; verified units and offsets. |
| Status | Known locked and unlocked captures; channel 39 and 40 bit masks checked against the actual manual. |
| UDP | Timeout then success, exhaustion, wrong peer, late reply, duplicate reply, occupied port, cancellation and restart. |
| Time | Host clock step, timezone/DST display changes, monotonic RTT, missing poll intervals and UTC labeling. |
| Persistence | Disk-full/write failure, interrupted append, restart recovery, unique sequence and source hash. |
| GUI | Inert imports, no Tk, bounded memory, zoom preservation, stale-data display and worker cancellation. |
| Analysis | T4 timing-channel identity, valid reference, telemetry overlap, no housekeeping-only correction and unchanged MHM regression results. |

## Implementation sequence and acceptance gates

| Milestone | Deliverable | Gate |
|---|---|---|
| Protocol evidence | Actual manual, firmware identity, raw captures and current control-screen labels | Resolve frame layout, scaling and both lock-status ambiguities. |
| Offline parser | Pure parser and captured-frame fixtures | Exact engineering-value agreement with OEM display and malformed-input tests. |
| Read-only poller | MONIT-only worker, logs and emulator | No state-changing command path; timeout/retry/peer tests pass. |
| Telemetry GUI | T4 tab and persistent outputs | Read-only site test, stable memory, no interference with existing monitoring. |
| Timing integration | Explicit T4 FITS pair, overlap and stability | Measured sign confirmed, reference behavior reviewed, gaps preserved. |
| Correction advisory | Separate OEM-calibrated actuator model | Transfer gain, units, existing ramp, precision and application epoch verified; independent synthetic tests. |

No timeline or credit estimate is attached. The acceptance gates, rather than an assumed schedule, control progression.

## Files needed before implementation

- **T4 operation/remote-command manual:** In particular the channel table referenced as page 47, status-bit definitions, frequency adjustment and drift-control sections.
- **Identity and controls:** Installed firmware/software version and screenshots showing actual frequency/drift field names, units, current values and enabled modes.
- **Raw telemetry:** Several unedited MONIT replies and matching OEM displays, ideally including known lock states and any installed-option differences.
- **Timing input:** The FITS channel names that genuinely identify the T4 and its reference, plus an overlapping recording.
- **Site access:** A connected bracca device for staging and an explicitly approved read-only instrument endpoint before any network test. Staging remains confined to the user's previously authorized directory.

## Appendix: Proposed equations and limits

The source decoder's analog scaling is provisional until the channel table is verified [1] ([T4 communication source](https://raw.githubusercontent.com/Braveheart53/GBO_Maser_Control/main/T4_Maser_Comms.py)):

\[
v_i=g_i c_i .
\]

The source's payload length follows its channel widths; this is not yet an independent OEM protocol verification [1] ([T4 communication source](https://raw.githubusercontent.com/Braveheart53/GBO_Maser_Control/main/T4_Maser_Comms.py)):

\[
L_{\mathrm{hex}}=32(3)+8(2)+1=113 .
\]

For the future actuator model, define the locally verified fractional-frequency gain \(G=\partial\ln f_{\mathrm{out}}/\partial u\). Only after measuring or documenting that gain may a small-signal correction use:

\[
\Delta u\simeq-\frac{y}{G},
\qquad
\Delta\dot u\simeq-\frac{r}{G},
\qquad r=\frac{dy}{d(\text{day})}.
\]

These are generic first-order local relationships, not T4 firmware equations. Gain variation, actuator nonlinearity, existing steering, discrete updates and saturation require the actual T4 model; no numerical \(G\) is assigned.

## IEEE reference table

| Ref. | Full bibliographic entry | DOI or URL |
|---|---|---|
| [1] | W. W. Wallace, “T4_Maser_Comms.py,” GBO_Maser_Control, version comments through 1.0.1, last-edit header Jun. 5, 2025; reviewed commit 9682a2ffbcd0b0df6612d4aef3619d7bdaf6cc66. Accessed Nov. 3, 2026. | https://raw.githubusercontent.com/Braveheart53/GBO_Maser_Control/main/T4_Maser_Comms.py |
| [2] | Safran, “iMaser 3000,” product datasheet, Mar. 11, 2024. Accessed Nov. 3, 2026. | https://safran-navigation-timing.com/wp-content/uploads/2024/03/SAFRAN_IMASER-3000_Datasheet_3-11-2024_US.pdf |
| [3] | W. W. Wallace, “GBO_Maser_Control,” including AB Power Meter Monitor 1.7.0 and ab_live_plots.py 0.1.0, GitHub repository; reviewed commit 9682a2ffbcd0b0df6612d4aef3619d7bdaf6cc66. Accessed Nov. 3, 2026. | https://github.com/Braveheart53/GBO_Maser_Control/tree/main |
