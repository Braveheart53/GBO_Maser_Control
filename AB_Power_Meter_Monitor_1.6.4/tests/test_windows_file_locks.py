#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace <wwallace@nrao.edu>
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Output files held open by another program (Excel) while the monitor logs.

===============================================================================
 Title          : AB Power Meter Monitor - locked-output-file test
 Filename       : tests/test_windows_file_locks.py
 Version        : 0.0.2 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu
 Created        : 2026-10-26
 Modified       : 2026-10-26
 Language       : Python 3.8 through 3.12 (Windows and Linux)
 Requires       : the monitor's dependencies (numpy, h5py, astropy, openpyxl)
 Licence        : GPL-3.0-or-later (GNU General Public License v3.0 or later)
 Status         : Internal draft
===============================================================================

How the files are locked
------------------------
Windows  A helper process opens the files with CreateFileW(GENERIC_READ,
         FILE_SHARE_READ) - what Excel does with an open CSV or workbook:
         other programs may read the file, but appending, replacing or
         renaming it fails (sharing violation). CSV, Markdown, XLSX and FITS
         of one meter are locked.
Other    No mandatory locks exist, so the monitor's own open() is wrapped to
         raise PermissionError for the "locked" CSV and Markdown files (the
         same exception Windows raises). XLSX/FITS are covered on Windows only.

Checks
------
1  While locked: no exception, no ERROR record, exactly ONE "open in another
   program" warning per file (1.6.x logged an error per table per poll).
2  A RAM flush while locked persists every row (rows_kept_unpersisted = 0)
   by writing rescue files.
3  After unlocking, the next writes merge the rescue files back: every
   output holds exactly one row per poll, in time order, no duplicates, and
   no *_rescue.* or *.merging file is left.
4  _rescue_files() of Diagnostic_Table ignores the rescue files of
   Diagnostic_Table_Extended (name-prefix trap) and returns rescue files of
   the same second in creation order ("_rescue" before "_2_rescue").
5  New Markdown rows after a merged rescue section start a "(continued)"
   section (they are not filed under the rescue section's heading).

Usage:  python tests/test_windows_file_locks.py

REVISION HISTORY
----------------
0.0.2  2026-10-26  Rescue files of one second are merged in creation order;
                   checks the Markdown "(continued)" section.
0.0.1  2026-10-26  First version (monitor 1.6.2).
===============================================================================
"""

# %% IMPORTS ===================================================================
from __future__ import annotations

import builtins
import csv
import logging
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import ab_power_meter_monitor as abm  # noqa: E402
from ab_meter_emulator import start_emulators  # noqa: E402

WINDOWS = sys.platform == "win32"
HOSTS = ["127.0.0.2", "127.0.0.3"]
LOCKED_TABLE = "Real_Time_Power_Table"

# Helper run in a separate process on Windows: holds the files like Excel does
# until a line arrives on stdin.
_LOCKER = r'''
import ctypes, sys
from ctypes import wintypes
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
k32.CreateFileW.restype = wintypes.HANDLE
k32.CloseHandle.argtypes = [wintypes.HANDLE]
GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING, NORMAL = 0x80000000, 0x1, 3, 0x80
INVALID = ctypes.c_void_p(-1).value
handles = []
for path in sys.argv[1:]:
    handle = k32.CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, None, OPEN_EXISTING, NORMAL, None)
    if handle in (None, INVALID):
        print("FAIL", path, ctypes.get_last_error(), flush=True)
        sys.exit(1)
    handles.append(handle)
print("locked", len(handles), flush=True)
sys.stdin.readline()
for handle in handles:
    k32.CloseHandle(handle)
print("unlocked", flush=True)
'''


# %% HELPERS ===================================================================
class _Records(logging.Handler):
    """Keeps every WARNING+ record of the monitor logger."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.records = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class _Lock:
    """Hold ``paths`` open like Excel (Windows) or simulate it (elsewhere)."""

    def __init__(self, paths) -> None:
        self.paths = [os.path.abspath(p) for p in paths]
        self.proc = None

    def __enter__(self) -> "_Lock":
        if WINDOWS:
            self.proc = subprocess.Popen([sys.executable, "-c", _LOCKER] + self.paths,
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            line = self.proc.stdout.readline().strip()
            assert line.startswith("locked"), line
        else:
            locked = set(self.paths)

            def guarded_open(file, mode="r", *args, **kwargs):
                if any(c in mode for c in "wax+") and os.path.abspath(str(file)) in locked:
                    raise PermissionError(13, "Permission denied (simulated Excel lock)", str(file))
                return builtins.open(file, mode, *args, **kwargs)
            abm.open = guarded_open                      # shadows the builtin in the monitor only
        return self

    def check_blocks(self, path: str) -> None:
        """The lock must really stop an append (validates the test itself)."""
        opener = getattr(abm, "open", builtins.open)
        try:
            with opener(path, "a"):
                pass
        except PermissionError:
            return
        raise AssertionError(f"lock does not block appends to {path}")

    def __exit__(self, *exc) -> None:
        if self.proc is not None:
            self.proc.stdin.write("\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=30)
        elif hasattr(abm, "open"):
            del abm.open


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _csv_stamps(path: str) -> list:
    with open(path, newline="", encoding="utf-8") as handle:
        return [row[0] for row in list(csv.reader(handle))[2:] if row]


def _md_rows(path: str) -> int:
    with open(path, encoding="utf-8") as handle:
        return sum(1 for line in handle if line.startswith("| ") and not line.startswith("| Timestamp_Local"))


def _leftovers(folder: str) -> list:
    return sorted(str(p) for p in Path(folder).rglob("*") if "_rescue" in p.name or p.name.endswith(".merging"))


# %% TESTS =====================================================================
def test_rescue_name_matching() -> None:
    folder = tempfile.mkdtemp(prefix="abm_lk0_")
    base = os.path.join(folder, "ABMeter_10_0_0_1_Diagnostic_Table.csv")
    for name in ("ABMeter_10_0_0_1_Diagnostic_Table_20261026_101500_rescue.csv",
                 "ABMeter_10_0_0_1_Diagnostic_Table_20261026_101500_2_rescue.csv",
                 "ABMeter_10_0_0_1_Diagnostic_Table_Extended_20261026_101500_rescue.csv",
                 "ABMeter_10_0_0_1_Diagnostic_Table_20261026_101500_rescue.csv.merging"):
        Path(folder, name).write_text("x", encoding="utf-8")
    found = [os.path.basename(p) for p in abm._rescue_files(base)]
    # Creation order: the first rescue file of a second has no counter, the
    # second one "_2" (a plain name sort would put "_2" first).
    assert found == ["ABMeter_10_0_0_1_Diagnostic_Table_20261026_101500_rescue.csv",
                     "ABMeter_10_0_0_1_Diagnostic_Table_20261026_101500_2_rescue.csv"], found


def test_locked_outputs_lose_nothing() -> None:
    port = _free_port()
    start_emulators(HOSTS, port)
    folder = tempfile.mkdtemp(prefix="abm_lk1_")
    abm.OUTPUT_BASE_DIR = folder
    abm.METER_URL_TEMPLATE = "http://{ip}:%d/{page}" % port
    abm.IP_LIST = list(HOSTS)
    abm.HTTP_TIMEOUT_SEC, abm.HTTP_RETRY_COUNT, abm.ENABLE_RAM_FLUSH = 2, 1, 0
    abm.ENABLE_CSV = abm.ENABLE_LOG_APPEND = 1
    abm.ENABLE_XLSX = abm.ENABLE_FITS = 1 if WINDOWS else 0
    abm.ENABLE_VEUSZ = 0
    abm.FULL_WRITE_EVERY_N_CYCLES = 1
    abm.set_console_logging(False)
    records = _Records()
    logging.getLogger("ABMonitor").addHandler(records)
    cfg = abm.default_config()
    abm.begin_run(cfg)
    polls = 0
    for _ in range(2):
        abm.poll_cycle(cfg)
        polls += 1
    abm.persist_outputs(cfg, full=True, must_persist=True, reason="test")   # XLSX/FITS exist now
    ip = "127_0_0_2"
    csv_path = os.path.join(cfg["csv_dir"], f"ABMeter_{ip}_{LOCKED_TABLE}.csv")
    md_path = os.path.join(cfg["log_dir"], f"ABMeter_{ip}_{LOCKED_TABLE}.md")
    targets = [csv_path, md_path]
    if WINDOWS:
        targets += [os.path.join(cfg["xlsx_dir"], f"ABMeter_{ip}.xlsx"),
                    os.path.join(cfg["fits_dir"], f"ABMeter_{ip}.fits")]
    with _Lock(targets) as lock:
        lock.check_blocks(csv_path)
        for _ in range(3):
            abm.poll_cycle(cfg)
            polls += 1
        in_use = [r for r in records.records if "is open in another program" in r.getMessage()]
        for path in (csv_path, md_path):
            hits = [r for r in in_use if path in r.getMessage()]
            assert len(hits) == 1, (path, [r.getMessage() for r in in_use])
        errors = [r.getMessage() for r in records.records if r.levelno >= logging.ERROR]
        assert not errors, errors
        flush = abm.perform_ram_flush(cfg, "test while locked", keep_samples=0)
        assert flush["rows_kept_unpersisted"] == 0, flush
        assert abm._rescue_files(csv_path) and abm._rescue_files(md_path)
        abm.poll_cycle(cfg)                              # still locked: rows kept in memory
        polls += 1
    for _ in range(2):                                   # unlocked: rescue files merged back
        abm.poll_cycle(cfg)
        polls += 1
    abm.end_run(cfg)
    logging.getLogger("ABMonitor").removeHandler(records)
    stamps = _csv_stamps(csv_path)
    # Exactly one row per poll (a lost row lowers, a duplicate raises the count),
    # in time order (rescue rows merged before the newer ones). Timestamps have
    # 1 s resolution and polls run back to back, so equal stamps are genuine.
    assert len(stamps) == polls, (len(stamps), polls)
    assert stamps == sorted(stamps), stamps
    assert _md_rows(md_path) == polls, (_md_rows(md_path), polls)
    with open(md_path, encoding="utf-8") as handle:
        text = handle.read()
    assert "rows written while the log was open elsewhere" in text
    assert text.index("rows written while the log was open elsewhere") < text.index("(continued)"), \
        "new rows must follow the merged rescue section in their own section"
    if WINDOWS:
        import openpyxl
        from astropy.io import fits
        book = openpyxl.load_workbook(targets[2], read_only=True)
        counts = [sum(1 for _ in ws.iter_rows()) - 2 for ws in book.worksheets]
        book.close()
        assert set(counts) == {polls}, counts
        with fits.open(targets[3]) as hdul:
            assert all(len(h.data) == polls for h in hdul[1:]), [len(h.data) for h in hdul[1:]]
    assert not _leftovers(folder), _leftovers(folder)
    errors = [r.getMessage() for r in records.records if r.levelno >= logging.ERROR]
    assert not errors, errors
    print(f"  {polls} polls, lock mode {'CreateFileW (Windows)' if WINDOWS else 'simulated'}; "
          f"outputs checked: {'CSV, MD, XLSX, FITS' if WINDOWS else 'CSV, MD'}")


if __name__ == "__main__":
    print(f"Python {sys.version.split()[0]} on {sys.platform}, monitor {abm.__version__}")
    for test in (test_rescue_name_matching, test_locked_outputs_lose_nothing):
        test()
        print("PASS", test.__name__)
