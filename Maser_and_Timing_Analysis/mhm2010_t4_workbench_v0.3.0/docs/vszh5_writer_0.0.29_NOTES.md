---
title: vszh5_writer 0.0.29 - release notes (exact ISO-8601 text)
author: William W. Wallace (NRAO/GBO), with Claude
date: 2026-11-10
version: 0.0.29
built_on: master 0.0.28 (SHA-256 13f68513ff47e93d5e7f7da80c05f1699994b9bf0b4baa8390b77f6f634a64b5)
sha256: 50631331470ebd50162030f971529044076a6c813a264393067522a504f9d701
released_with: MHM2010 / T4 Maser Timing Workbench 0.2.4
licence: GPL-3.0-or-later
---

# vszh5_writer 0.0.29

## Summary

0.0.29 adds exact (never rounded, never cut) local ISO 8601 text to the shared writer. Every 0.0.28 call writes the same file, so it is a drop-in for every program on 0.0.25 or later.

It was made from the master copy, as the registry rules require. The workbench chat asked for every writer change from every chat. The newest found was 0.0.28 (FitsAutoPlot 0.4.1), and no chat had a 0.0.29.

The operator's standing rule *"in all exports, all floats or numbers should never be rounded or trimmed"* then showed that `iso_local_strings()` cut the fraction of a second off. That is what this release fixes.

| Item | Value |
|---|---|
| Version | 0.0.29 (2026-11-10) |
| Built on | master 0.0.28, rebuilt byte for byte from 0.0.27 plus the recorded 0.0.28 edits (`make_0028.py`; SHA-256 matched) |
| Changed | additions only: `iso_local_strings(timespec=)`, `mjd_iso_local_strings()`, `ISO_TIMESPECS`, private `_exact_wall_text`, `_EXACT`; header Version line (0.0.28 still said 0.0.27), Lineage, Status, DIFFERENCES |
| Unchanged | every other line; documents written by 0.0.28 and 0.0.29 are identical object by object |
| Python | 3.13.18 and 3.12.3 run; 3.8 grammar checked (`ast.parse(feature_version=(3, 8))`); `decimal` is standard library |

## Why the old text was not enough

`iso_local_strings()` (0.0.24–0.0.28) used `datetime.isoformat(timespec="seconds")`, which **cuts** the fraction. For example, 12:00:00.9 reads 12:00:00.

On the operator's Franks data, MJD 61234.10355 is 22:29:06.72 local time, and the old text said 22:29:06: 0.72 s gone on a timing workbench.

Microseconds are not enough either. Python's `datetime` resolves 1 µs, but the source floats are finer:

| Source | Value near | float64 spacing |
|---|---|---|
| MJD (days) | 61220 | 7.3×10⁻¹² d = **0.63 µs** |
| Unix time (s) | 1.79×10⁹ | **0.24 µs** |

## What 0.0.29 adds

| Function | Text | Inverse |
|---|---|---|
| `iso_local_strings(epoch_s, zone=None, timespec="seconds")` | `"seconds"`: unchanged default (cut). `"milliseconds"`, `"microseconds"`, `"auto"`: `isoformat`'s, after rounding to µs. **`"exact"`**: every digit of the shortest decimal that gives back the same float epoch (`repr`) | parse the seconds with `Decimal` → identical float epoch |
| `mjd_iso_local_strings(mjd, zone=None)` | exact text of the time the MJD states. The MJD's shortest decimal becomes Unix seconds in decimal arithmetic, \(t = (\mathrm{MJD} - 40587)\times 86400\), so MJD 61234.10355 → `2026-07-12T22:29:06.72-04:00`. Through the float epoch the text would read `06.7199999…` | \(\mathrm{MJD} = t/86400 + 40587\) in `Decimal` → identical MJD float |

The offset is the zone's offset at that instant, as in `local_time_axis()`. NaN gives `""`. Pre-1970 times work.

The Decimal context has 80 digits. That is far above the at most 17 + 5 digits an MJD or an epoch times 86400 can need, so no step rounds.

## Checks (workbench `tests/test_writer_0029.py`, 21 tests)

- 10 000 random epochs × 5 zones (`None`, `UTC`, `America/New_York`, `-05:00`, `+05:30`) give back the identical float.
- 10 000 random MJD × 5 zones give back the identical float, and their offsets equal `local_time_axis`. Every Franks MJD also round-trips, and its text has at most 3 decimals of a second (k × 0.864 s).
- The default text equals the 0.0.24–0.0.28 text for 2 000 epochs × 5 zones, NaN included.
- DST instant: `2026-03-08T01:59:59.25-05:00` then `03:00:00-04:00`.
- Edge cases: whole seconds have no fraction; pre-1970 times work; an invalid `timespec` raises `ValueError`.
- A document (1-D, errors, dates, text, 2-D, page script) written by 0.0.28 and by 0.0.29 is identical in every dataset, attribute and script line (save stamps excluded). A deliberate change is detected.
- Python 3.12.3 with numpy 1.26.4 and h5py 3.10.0: both round trips are exact.

## Upgrading

0.0.29 drops in for every 0.0.25+ program. Programs that want exact text pass `timespec="exact"` or call `mjd_iso_local_strings()`. Nothing changes otherwise. Programs still on 0.0.24 or older should read the registry's "Upgrading" notes about the 0.0.25 widget names first.

## Files

- `docs/writer_0.0.29/vszh5_writer_0.0.28.py`: master 0.0.28, byte for byte.
- `docs/writer_0.0.29/make_0028.py`: rebuilds 0.0.28 from 0.0.27 and checks the hash.
- `docs/writer_0.0.29/make_0029.py`: turns 0.0.28 into 0.0.29.
- `docs/writer_0.0.29/vszh5_writer_0.0.28_to_0.0.29.diff`: the diff.

## References

| # | Reference |
|---|---|
| [1] | ISO, *ISO 8601-1:2019 Date and time — Representations for information interchange — Part 1: Basic rules*, 2019. [Online]. Available: https://www.iso.org/standard/70907.html |
| [2] | IEEE, *IEEE Std 754-2019, IEEE Standard for Floating-Point Arithmetic*, 2019, doi: 10.1109/IEEESTD.2019.8766229. [Online]. Available: https://doi.org/10.1109/IEEESTD.2019.8766229 |
| [3] | Python Software Foundation, "decimal — Decimal fixed point and floating point arithmetic," *Python 3 documentation*. [Online]. Available: https://docs.python.org/3/library/decimal.html |
| [4] | Python Software Foundation, "datetime.isoformat(timespec=...)," *Python 3 documentation*. [Online]. Available: https://docs.python.org/3/library/datetime.html#datetime.datetime.isoformat |
| [5] | Python Software Foundation, "Floating-point arithmetic: issues and limitations (repr)," *Python 3 tutorial*. [Online]. Available: https://docs.python.org/3/tutorial/floatingpoint.html |
