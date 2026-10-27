#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace <wwallace@nrao.edu>
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Run every AB monitor test, write a report and a results zip (Windows kit).

===============================================================================
 Title          : AB Power Meter Monitor - test runner and report bundle
 Filename       : tests/run_all_tests.py
 Version        : 0.0.1 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu
 Created        : 2026-10-26
 Modified       : 2026-10-26
 Language       : Python 3.8 through 3.12 (written for Windows; runs on Linux)
 Requires       : the monitor's dependencies; PySide6 for the GUI soak
 Licence        : GPL-3.0-or-later (GNU General Public License v3.0 or later)
 Status         : Internal draft
===============================================================================

What it does
------------
1. System report: Windows edition/build, Wine detection, CPU, RAM, disk,
   Python, every dependency's version, the monitor/test versions, and a check
   that the emulator's loopback addresses 127.0.0.2/127.0.0.3 can be bound.
2. Runs the suites below one after another, each in its own Python process
   with a time limit, showing the output live and saving it to
   <results>/<suite>.log:

       appender        tests/test_vszh5_appender.py   (+ real Veusz if found)
       offline         tests/test_monitor_offline.py
       memory_release  tests/test_memory_release.py
       file_locks      tests/test_windows_file_locks.py
       exporter        tests/test_exporter_offline.py
       memory_windows  tests/windows_memory_check.py --json ...
       gui_soak        tests/gui_soak_test.py <seconds>
       headless_soak   tests/headless_soak_test.py --minutes N

   DeprecationWarnings raised from this project's modules are errors; those
   of third-party packages are only logged.
3. Writes summary.md / summary.json / system.json and zips the whole results
   folder to <results>/../AB_TestResults_<computer>_<stamp>.zip - the one
   file to send back.

Usage
-----
    python tests/run_all_tests.py                    # everything (~20 min)
    python tests/run_all_tests.py --quick            # shorter soaks (~8 min)
    python tests/run_all_tests.py --only offline,file_locks
    python tests/run_all_tests.py --soak-minutes 60 --veusz-exe C:\\ABTest\\env_veusz\\Scripts\\veusz.exe

REVISION HISTORY
----------------
0.0.1  2026-10-26  First version (monitor 1.6.2, Windows test kit 1.0.0).
===============================================================================
"""

# %% IMPORTS ===================================================================
from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
sys.path.insert(0, str(APP))
sys.path.insert(0, str(HERE))

__version__ = "0.0.1"
WINDOWS = sys.platform == "win32"
DISTRIBUTIONS = ("numpy", "h5py", "astropy", "openpyxl", "psutil", "requests", "beautifulsoup4",
                 "lxml", "prometheus_client", "PySide6", "QtPy", "matplotlib", "pypdf")
OWN_MODULES = ("ab_power_meter_monitor", "vszh5_writer", "ab_prometheus_exporter", "ab_meter_caller")


# %% SYSTEM REPORT ==============================================================
def _dist_version(name: str) -> str:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return "not installed"


def _loopback_ok(address: str) -> bool:
    """Can a server bind to ``address``? (the emulator uses 127.0.0.2/.3)."""
    try:
        with socket.socket() as probe:
            probe.bind((address, 0))
        return True
    except OSError:
        return False


def _windows_edition() -> str:
    if not WINDOWS:
        return ""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
        values = {}
        for name in ("ProductName", "DisplayVersion", "CurrentBuild", "UBR"):
            try:
                values[name] = winreg.QueryValueEx(key, name)[0]
            except OSError:
                values[name] = ""
        build = int(values["CurrentBuild"] or 0)
        product = str(values["ProductName"])
        if build >= 22000 and "Windows 10" in product:      # the registry still says 10 on 11
            product = product.replace("Windows 10", "Windows 11")
        return f"{product} {values['DisplayVersion']} (build {values['CurrentBuild']}.{values['UBR']})"
    except Exception as exc:
        return f"unknown ({exc})"


def system_report(results: Path) -> dict:
    import psutil
    from windows_memory_check import running_under_wine
    memory = psutil.virtual_memory()
    report = {
        "computer": socket.gethostname(),
        "time_local": datetime.datetime.now().isoformat(timespec="seconds"),
        "platform": platform.platform(),
        "windows_edition": _windows_edition(),
        "wine": running_under_wine(),
        "python": sys.version,
        "executable": sys.executable,
        "cpu_count": os.cpu_count(),
        "ram_total_gib": round(memory.total / 2 ** 30, 1),
        "ram_available_gib": round(memory.available / 2 ** 30, 1),
        "disk_free_gib": round(shutil.disk_usage(str(results)).free / 2 ** 30, 1),
        "loopback_127_0_0_2": _loopback_ok("127.0.0.2"),
        "loopback_127_0_0_3": _loopback_ok("127.0.0.3"),
        "packages": {name: _dist_version(name) for name in DISTRIBUTIONS},
        "runner": __version__,
    }
    try:
        import ab_power_meter_monitor as abm
        import ab_prometheus_exporter as exp
        import vszh5_writer as vw
        report["versions"] = {"monitor": abm.__version__, "exporter": exp.__version__,
                              "vszh5_writer": vw.__version__}
    except Exception as exc:
        report["versions"] = {"error": f"{type(exc).__name__}: {exc}"}
    return report


# %% SUITES ======================================================================
def suites(args: argparse.Namespace, results: Path) -> list:
    """(name, script arguments, timeout s, needs) in run order."""
    quick = args.quick
    soak_minutes = args.soak_minutes if args.soak_minutes else (2.0 if quick else 10.0)
    gui_seconds = args.gui_seconds if args.gui_seconds else (60.0 if quick else 150.0)
    values = "3000000" if quick else "6000000"
    return [
        ("appender", ["test_vszh5_appender.py"], 900, ()),
        ("offline", ["test_monitor_offline.py"], 1800, ()),
        ("memory_release", ["test_memory_release.py"], 900, ()),
        ("file_locks", ["test_windows_file_locks.py"], 900, ()),
        ("exporter", ["test_exporter_offline.py"], 900, ()),
        ("memory_windows", ["windows_memory_check.py", "--values", values,
                            "--json", str(results / "memory_windows.json")], 1800, ()),
        ("gui_soak", ["gui_soak_test.py", str(gui_seconds)], int(gui_seconds) + 600, ("PySide6",)),
        ("headless_soak", ["headless_soak_test.py", "--minutes", str(soak_minutes),
                           "--out", str(results / "headless_soak_output")],
         int(soak_minutes * 60) + 900, ()),
    ]


def run_suite(name: str, script_args: list, timeout: int, results: Path, env: dict) -> dict:
    """Run one suite in its own process; tee its output to the console and a log."""
    warn_flags = ["-W", "default::DeprecationWarning"]
    for module in OWN_MODULES:
        warn_flags += ["-W", f"error::DeprecationWarning:{module}"]
    command = [sys.executable, "-X", "utf8"] + warn_flags + [str(HERE / script_args[0])] + script_args[1:]
    log_path = results / f"{name}.log"
    started = time.monotonic()
    print(f"\n=== {name} " + "=" * max(4, 66 - len(name)), flush=True)
    with open(log_path, "w", encoding="utf-8") as log:
        log.write("$ " + " ".join(command) + "\n")
        proc = subprocess.Popen(command, cwd=str(APP), env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        timed_out = threading.Event()

        def kill_after() -> None:
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out.set()
                proc.kill()
        killer = threading.Thread(target=kill_after, daemon=True)
        killer.start()
        last_lines = []
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
            last_lines = (last_lines + [line.rstrip()])[-40:]
        proc.wait()
    seconds = time.monotonic() - started
    if timed_out.is_set():
        status, note = "FAIL", f"timed out after {timeout} s"
    elif proc.returncode == 0:
        status, note = "PASS", ""
        skipped = [l.strip() for l in last_lines if l.strip().startswith("SKIP")]
        if skipped:
            note = skipped[0][:160]
    else:
        status = "FAIL"
        failing = [l for l in last_lines if l.startswith(("FAIL", "AssertionError", "Traceback"))
                   or "Error" in l]
        note = (failing[-1] if failing else f"exit code {proc.returncode}")[:200]
    print(f"--- {name}: {status} in {seconds:.0f} s {('- ' + note) if note else ''}", flush=True)
    return {"suite": name, "status": status, "seconds": round(seconds, 1), "note": note,
            "exit_code": proc.returncode, "log": log_path.name}


# %% REPORT AND BUNDLE ===============================================================
def write_summary(results: Path, system: dict, outcomes: list) -> str:
    lines = [f"# AB monitor test results - {system['computer']}", "",
             f"- Time: {system['time_local']}",
             f"- System: {system['windows_edition'] or system['platform']}"
             + (" **(Wine)**" if system["wine"] else ""),
             f"- Python: {system['python'].split()[0]} ({system['executable']})",
             f"- Versions: {system.get('versions')}",
             f"- RAM: {system['ram_total_gib']} GiB total, {system['ram_available_gib']} GiB free; "
             f"CPUs: {system['cpu_count']}",
             f"- Loopback 127.0.0.2 / .3: {system['loopback_127_0_0_2']} / {system['loopback_127_0_0_3']}",
             "", "| Suite | Result | Time (s) | Note |", "|---|---|---|---|"]
    for o in outcomes:
        lines.append(f"| {o['suite']} | {o['status']} | {o['seconds']} | {o['note'].replace('|', '/')} |")
    passed = sum(o["status"] == "PASS" for o in outcomes)
    failed = sum(o["status"] == "FAIL" for o in outcomes)
    skipped = sum(o["status"] == "SKIP" for o in outcomes)
    lines += ["", f"**{passed} passed, {failed} failed, {skipped} skipped.**", "",
              "Package versions:", ""] + [f"- {k}: {v}" for k, v in system["packages"].items()]
    text = "\n".join(lines) + "\n"
    (results / "summary.md").write_text(text, encoding="utf-8")
    (results / "summary.json").write_text(json.dumps({"system": system, "suites": outcomes}, indent=2),
                                          encoding="utf-8")
    return text


def bundle(results: Path) -> Path:
    """Zip the results folder (skipping bulky data files) next to it."""
    archive = results.parent / f"AB_TestResults_{results.name}.zip"
    skip_ext = {".fits", ".xlsx", ".vszh5", ".csv", ".md", ".pkl"}
    keep = {"soak_samples.csv", "soak_memory.vszh5", "summary.md", "ab_monitor.log"}
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(results.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() in skip_ext and path.name not in keep:
                continue
            zf.write(path, path.relative_to(results.parent))
    return archive


# %% MAIN ==========================================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="Run every AB monitor test and bundle the results.")
    parser.add_argument("--quick", action="store_true", help="shorter soaks and smaller memory runs")
    parser.add_argument("--only", default="", help="comma-separated suite names")
    parser.add_argument("--skip", default="", help="comma-separated suite names to skip")
    parser.add_argument("--soak-minutes", type=float, default=0.0, help="headless soak length")
    parser.add_argument("--gui-seconds", type=float, default=0.0, help="GUI soak length")
    parser.add_argument("--veusz-exe", default="", help="veusz(.exe) for the real-Veusz render checks")
    parser.add_argument("--results", default="", help="results root folder")
    args = parser.parse_args()

    root = Path(args.results).resolve() if args.results else APP / "test_results"
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    results = root / f"{socket.gethostname()}_{stamp}"
    work = root / "work" / stamp                   # bulky test outputs (not zipped)
    results.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", TEMP=str(work), TMP=str(work))
    veusz = args.veusz_exe or os.environ.get("VEUSZ_EXE", "")
    if not veusz:
        for candidate in (APP.parent / "env_veusz" / "Scripts" / "veusz.exe",      # kit layout
                          APP / "windows" / "env_veusz" / "Scripts" / "veusz.exe"):  # repo layout
            if candidate.exists():
                veusz = str(candidate)
                break
    if not veusz:
        try:                                     # an installed Veusz (PATH, Program Files)
            import vszh5_writer
            veusz = vszh5_writer.find_veusz_exe(env_vars=("VEUSZ_EXE", "QUICKSYN_VEUSZ")) or ""
        except Exception:
            veusz = ""
    if veusz:
        env["VEUSZ_EXE"] = veusz

    print(f"AB monitor test runner {__version__} - results in {results}", flush=True)
    system = system_report(results)
    system["veusz_exe"] = veusz or "not found (real-Veusz render checks skipped)"
    (results / "system.json").write_text(json.dumps(system, indent=2), encoding="utf-8")
    print(json.dumps({k: system[k] for k in ("computer", "windows_edition", "wine", "versions",
                                             "loopback_127_0_0_2", "veusz_exe")}, indent=2), flush=True)
    if not (system["loopback_127_0_0_2"] and system["loopback_127_0_0_3"]):
        print("ERROR: 127.0.0.2 / 127.0.0.3 cannot be bound - the meter emulator needs them.", flush=True)

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    outcomes = []
    for name, script_args, timeout, needs in suites(args, results):
        if (only and name not in only) or name in skip:
            continue
        missing = [n for n in needs if system["packages"].get(n) == "not installed"]
        if missing:
            outcomes.append({"suite": name, "status": "SKIP", "seconds": 0,
                             "note": f"{', '.join(missing)} not installed", "exit_code": None, "log": ""})
            continue
        outcomes.append(run_suite(name, script_args, timeout, results, env))
    for name in ("soak_samples.csv", "soak_memory.vszh5", "soak_result.json"):
        source = results / "headless_soak_output" / name
        if source.exists():
            shutil.copy2(source, results / name)
    text = write_summary(results, system, outcomes)
    archive = bundle(results)
    print("\n" + text)
    print(f"Results zip (send this back): {archive}")
    return 0 if all(o["status"] != "FAIL" for o in outcomes) else 1


if __name__ == "__main__":
    sys.exit(main())
