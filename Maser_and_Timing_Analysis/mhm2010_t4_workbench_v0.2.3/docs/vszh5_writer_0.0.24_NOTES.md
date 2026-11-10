---
title: vszh5_writer 0.0.24 - release notes
author: William W. Wallace (NRAO/GBO), with Claude
date: 2026-11-03
version: 0.0.24
licence: GPL-3.0-or-later
supersedes: 0.0.23 (VSZH5 writer chat, master)
---

# vszh5_writer 0.0.24

0.0.24 is released with the MHM2010 / T4 Maser Timing Workbench 0.2.0. It was built from the RF-project master 0.0.23, which was first rebuilt **byte-exact** (SHA-256 `90833d763f8a1f7d60191c9a5e1728045eb7984b06f643b6c7ddf657eea9aac6`). **Additions only:** every 0.0.23 name, parameter, default and output is unchanged.

| File | Version | SHA-256 |
|---|---|---|
| `vszh5_writer.py` | 0.0.24 | see `SHA256SUMS.txt` in the package |
| built on | 0.0.23 | `90833d763f8a1f7d60191c9a5e1728045eb7984b06f643b6c7ddf657eea9aac6` |

## Added

| Name | Purpose |
|---|---|
| `MJD_UNIX_EPOCH`, `mjd_to_epoch`, `mjd_to_veusz` | UTC MJD → Unix seconds / Veusz date seconds (MJD 40587 = 1970-01-01) |
| `iso_offset_text` | UTC offset → `+HH:MM` / `-HH:MM` |
| `ISO_DATETIME_FORMAT`, `LocalTimeAxis`, `local_time_axis` | Local wall-clock date dataset (DST-aware per sample, computer zone or IANA zone) plus an ISO 8601 tick format, e.g. `2026-11-03T06:31:15-05:00` |
| `iso_local_strings` | Per-sample ISO 8601 text with offsets (text datasets, CSV) |
| `with_x`, `time_pair_pages` | The same traces as a page against MJD and a page against local time |
| `Panel.datetime_format` | Tick format of a datetime x axis in `grid_page()` |

Veusz tick formats are static. When the data cross a DST change, the ticks omit the offset, and the axis label lists each offset with the instant it starts.

## Evidence

| Check | Result |
|---|---|
| Grammar, Python 3.8 (`ast.parse(feature_version=(3, 8))`) | parses |
| Workbench tests (`tests/test_reference_and_time.py`) | helpers, DST label, ISO strings, grid format: pass |
| Veusz 3.6.2 render | ISO ticks with offset (constant zone) and without (DST crossing) |
| Not run | the writer chat's `tests/test_vszh5_writer.py` 0.0.3 (not in this session); Veusz 4.2.1 |

## Registry

Per `claude/vszh5-writer-registry.md`, 0.0.24 is the next free number. The RF-project master (`claude/vszh5_writer.py`) and the registry are updated in the same session.
