#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace <wwallace@nrao.edu>
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Tests of the 1.6.1 "return freed memory to the OS" step of the RAM flush.

===============================================================================
 Title          : AB Power Meter Monitor - memory-release tests
 Filename       : tests/test_memory_release.py
 Version        : 0.0.1 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu
 Created        : 2026-10-26
 Modified       : 2026-10-26
 Language       : Python 3.8 through 3.12 (Linux and Windows builds)
 Requires       : psutil (+ monitor dependencies)
 Licence        : GPL-3.0-or-later (GNU General Public License v3.0 or later)
 Status         : Internal draft
===============================================================================

Runs unchanged on Linux and on a Windows build of Python (tested on Windows
Python 3.12 under Wine; Wine implements the calls but not the Windows memory
manager, so on Wine only "the calls bind, run and never raise" is checked).

Checks
------
1  The OS bindings are built once, with explicit argument types (no 64-bit
   HANDLE truncation), and return_memory_to_os() never raises.
2  A RAM flush of a ~100 MB store removes every row and reports the
   OS-release steps (Linux: malloc_trim; Windows: heap_optimize,
   working_set_trim); on Linux the RSS falls by at least half of what the
   store added. (Windows: heap_optimize = HeapSetInformation
   HeapOptimizeResources, working_set_trim = EmptyWorkingSet.)
3  MEM_RETURN_TO_OS = 0 skips the OS calls (gc only).
4  Two concurrent releases never overlap (the second one is skipped).

Usage:  python tests/test_memory_release.py

REVISION HISTORY
----------------
0.0.1  2026-10-26  First version (monitor 1.6.1).
===============================================================================
"""

# %% IMPORTS ===================================================================
from __future__ import annotations

import gc
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import ab_power_meter_monitor as abm  # noqa: E402

abm.set_console_logging(False)
for _flag in ("ENABLE_CSV", "ENABLE_LOG_APPEND", "ENABLE_VEUSZ", "ENABLE_FITS", "ENABLE_XLSX"):
    setattr(abm, _flag, 0)                 # no file output: a flush drops the rows


# %% HELPERS ===================================================================
def _fill_store(n_tables: int = 20, n_rows: int = 2500, n_cols: int = 60) -> int:
    """Put n_tables x n_rows x n_cols distinct float objects into the store."""
    stamp = "2026-10-26 06:00:00"
    with abm._TS_LOCK:
        for t in range(n_tables):
            ip = f"10.0.0.{t % 4}"
            columns = {f"P{c:02d}": [float(r) + c / 100.0 + t for r in range(n_rows)]
                       for c in range(n_cols)}
            abm.TIME_SERIES_STORE.setdefault(ip, {})[f"Table_{t:02d}"] = {
                "timestamps_local": [stamp] * n_rows, "columns": columns,
                "units": {name: "" for name in columns}}
    return n_tables * n_rows * n_cols


# %% TESTS =====================================================================
def test_bindings_and_no_raise() -> None:
    report = abm.return_memory_to_os(trim_working_set=True)
    assert not report["skipped"] and isinstance(report["steps"], dict), report
    api = abm._os_memory_api()
    assert api is abm._os_memory_api(), "bindings must be built once"
    if sys.platform == "win32":
        import ctypes
        for name in ("heap_set_information", "empty_working_set"):
            assert name in api, (name, api["errors"])
        # 64-bit handles: HANDLE must be declared, not left to the int default.
        assert api["heap_set_information"].argtypes[0] is ctypes.wintypes.HANDLE
        assert api["empty_working_set"].argtypes == [ctypes.wintypes.HANDLE]
        assert ctypes.sizeof(api["heap_optimize_info_type"]) == 8      # {DWORD, DWORD}
        assert set(report["steps"]) == {"heap_optimize", "working_set_trim"}, report
        assert "commit_mb_before" in report and abm.process_commit_mb() > 0
    elif sys.platform.startswith("linux"):
        assert "malloc_trim" in report["steps"], report
    print("  report:", abm.describe_memory_release(report), "| errors:", report["errors"])


def test_flush_returns_memory() -> None:
    gc.collect()
    abm.return_memory_to_os()
    rss0 = abm.process_rss_mb()
    values = _fill_store()
    rss1 = abm.process_rss_mb()
    cfg = abm.default_config()
    cfg.update(mem_return_to_os=1, mem_trim_working_set=1, enable_ram_flush=1)
    result = abm.perform_ram_flush(cfg, "test", keep_samples=0)
    rss2 = abm.process_rss_mb()
    added, freed = rss1 - rss0, rss1 - rss2
    print(f"  {values:,} values: RSS {rss0:.0f} -> {rss1:.0f} (+{added:.0f}) -> {rss2:.0f} MB "
          f"(freed {freed:.0f} MB); {result['os_release_text']}")
    assert abm.TIME_SERIES_STORE == {} and result["rows_removed"] == 20 * 2500, result
    assert result["os_release"]["steps"], result["os_release"]
    if sys.platform.startswith("linux"):
        assert added > 50 and freed >= 0.5 * added, (rss0, rss1, rss2)


def test_switch_off() -> None:
    _fill_store(n_tables=2, n_rows=100, n_cols=5)
    cfg = abm.default_config()
    cfg.update(mem_return_to_os=0)
    result = abm.perform_ram_flush(cfg, "test-off", keep_samples=0)
    assert result["os_release"] == {} and result["os_release_text"] == "OS release off", result
    assert abm.release_memory(cfg) is False


def test_no_overlap() -> None:
    held = abm._RELEASE_LOCK.acquire()
    try:
        assert held
        report = abm.return_memory_to_os()
        assert report["skipped"] and not report["steps"], report
    finally:
        abm._RELEASE_LOCK.release()
    results = []
    threads = [threading.Thread(target=lambda: results.append(abm.return_memory_to_os()))
               for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 4 and all(isinstance(r, dict) for r in results)


if __name__ == "__main__":
    print(f"Python {sys.version.split()[0]} on {sys.platform}, monitor {abm.__version__}")
    for test in (test_bindings_and_no_raise, test_flush_returns_memory, test_switch_off, test_no_overlap):
        test()
        print("PASS", test.__name__)
