---
title: AB Power Meter Monitor 1.6.2 - Windows test kit
version: 1.0.0
date: 2026-10-26
author: William W. Wallace, NRAO/GBO
---

# AB Power Meter Monitor 1.6.2 — Windows test kit

Everything needed to test the monitor on a Windows 10/11 PC (written for
**TheGym**). No real meters are needed: a built-in emulator serves the 11 meter
pages for two meters on `127.0.0.2` and `127.0.0.3`. Nothing outside the kit
folder is changed; delete the folder to remove everything.

## What it checks

| Area | How |
|---|---|
| RAM flush gives memory back to **Windows** (1.6.1) | `windows_memory_check.py` reads the working set and commit charge with `GetProcessMemoryInfo`; step 3 lets you watch it in Task Manager |
| Output files held open by **Excel** (1.6.2) | `test_windows_file_locks.py` locks the CSV, Markdown, XLSX and FITS files the way Excel does (`CreateFileW`, read-share only) |
| **Ctrl+Break** stops the headless monitor and the exporter cleanly (1.6.2 / 0.1.1) | headless soak and exporter test send `CTRL_BREAK_EVENT` |
| No row lost or written twice, in every output, across flushes | offline suite + headless soak (CSV = Markdown = .vszh5 = XLSX = FITS row counts) |
| No leak over time | GUI soak (widgets, RSS, handles, GDI/USER objects) and headless soak (working set, handles, threads) |
| Python 3.12 on Windows | every suite, with this project's DeprecationWarnings as errors |

## Before you start

1. **Unblock the zip** before extracting: right-click it → *Properties* →
   tick *Unblock* → *OK*. (Otherwise Windows asks before every `.bat`.)
2. **Use a short path without spaces**, e.g. `C:\ABTest` or the ready copy in
   `C:\Users\wwallace\Claude\AB_Monitor_1.6.2_Windows_TestKit`. Conda
   environments break on paths with spaces; the setup refuses them.
3. Have **Miniforge** (mamba) installed — the setup also finds conda,
   micromamba, Miniconda or Anaconda. Without any of them it falls back to
   python.org Python 3.12 (`py -3.12`) with pip.
4. About **3 GB** free disk and an internet connection for step 1.

## Steps

| Step | Double-click | Time | What happens |
|---|---|---|---|
| 1 | `1_setup_environment.bat` | 5–10 min | creates `env\` (Python 3.12.15 and the exact package versions tested); asks whether to add `env_veusz\` (Veusz 4.2.1, for the real-Veusz checks and "Open in Veusz") — answer **Y** unless Veusz is installed |
| 2 | `2_run_all_tests.bat` | ~20 min | all eight suites; writes `results\<computer>_<time>\` and **`results\AB_TestResults_<computer>_<time>.zip`**. `2_run_all_tests.bat --quick` takes ~8 min |
| 3 | `3_task_manager_check.bat` | ~3 min | six flush runs, each pausing 20 s so you can watch Task Manager |
| 4 | `4_gui_with_emulator.bat` | ~10 min | the real GUI against the emulator — follow the checklist below |
| 5 | `5_headless_with_emulator.bat` | ~3 min | headless run with a visible log; stop it with **Ctrl+C** or **Ctrl+Break** |

Longer soak (optional, e.g. over lunch):
`2_run_all_tests.bat --only headless_soak --soak-minutes 60`

### Step 3 — what to look for

Task Manager → *Details* → right-click a column header → *Select columns* →
tick **Working set (memory)** and **Commit size**. Each run prints its PID.
After the flush of a `full` run the working set $W$ should fall back close to
where it started:

$$
W_\text{after flush} \le W_\text{idle} + 0.3\,\bigl(W_\text{filled} - W_\text{idle}\bigr)
$$

`gc_only` is how 1.6.0 behaved on Windows (rows dropped, memory kept);
`heap_only` releases heap memory without trimming the working set; `full` is
the default since 1.6.1. Commit size falls by whatever the heaps could
decommit; it is reported, not judged.

### Step 4 — GUI checklist

The GUI writes to `gui_output\`. Tick the outputs you want (CSV at least) and
press **Start Auto**.

- [ ] Window opens; *View* menu switches light/dark theme; previews update every 2 s.
- [ ] Status line shows `Store … | RSS … MB (commit … MB) | system RAM … %`.
- [ ] **Flush RAM Now** → log line `OS release [heap_optimize ok, working_set_trim ok]; RSS a -> b MB, commit c -> d MB`, and the python.exe working set in Task Manager drops.
- [ ] Open `gui_output\csv\ABMeter_127_0_0_2_Real_Time_Power_Table.csv` **in Excel** while polling → exactly **one** warning "is open in another program", no errors, polling continues.
- [ ] With the CSV still open in Excel, press **Save Now** → a `…_rescue.csv` file appears next to it.
- [ ] Close Excel → within one poll the rescue file disappears and the CSV continues without gaps or duplicates (rows in time order).
- [ ] **Open in Veusz** (needs `env_veusz` or an installed Veusz) opens `ABMeter_127_0_0_2.vszh5`; it keeps growing while polling (reload in Veusz).
- [ ] Close the window while polling → it waits for the final writes, then closes.

### Step 5 — headless stop

Press **Ctrl+Break** (or Ctrl+C) in the monitor window: the log must end with
`Final writes done` and the exit code must be 0. Answer **N** if cmd asks
"Terminate batch job (Y/N)?". Many laptops have no Break key — Ctrl+C does the
same here; Ctrl+Break matters for supervisors that send `CTRL_BREAK_EVENT`.

## What to send back

1. `results\AB_TestResults_<computer>_<time>.zip` (from step 2 — the main one).
2. `results\task_manager_check.json` (step 3) and, if you like, a Task Manager screenshot.
3. Which GUI checklist items passed or failed, and anything odd.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Windows protected your PC" / security warning per `.bat` | unblock the zip (see *Before you start*) and extract again |
| Setup: "path contains a space" | move the kit to e.g. `C:\ABTest` |
| Setup: neither mamba/conda nor Python 3.12 found | install Miniforge (https://github.com/conda-forge/miniforge), then run step 1 again |
| `system.json` shows `loopback_127_0_0_2: false` | the emulator cannot use 127.0.0.2; report it — the tests need it |
| A firewall prompt | should not appear (everything binds to 127.0.0.x); allowing "private networks" is harmless |
| Tests slow | antivirus scanning the many small writes; normal, the time limits allow for it |
| Port 18080 in use (steps 4–5) | edit the two `.bat` files: change both `18080` to another free port |
| Rebuild the environment | delete `env\` (and `env_veusz\`), run step 1 again |

## Already verified before shipping (2026-10-26)

* Linux, Python 3.12.3: all eight suites pass (`run_all_tests.py --quick`).
* Windows build of Python 3.12.15 (same package versions as
  `environment-windows-test.txt`) under Wine 9.0: setup script, memory release,
  file locks (real `CreateFileW` locks), memory check and the GUI soak pass.
  Not checkable under Wine, so checked for the first time on your PC: the real
  Veusz render, the Ctrl+Break stop, and the Task Manager drop itself.

## Files

| File | Purpose |
|---|---|
| `1_` … `5_*.bat` | the steps above |
| `_env.bat` | shared settings for the steps (finds `env\python.exe`, sets PATH) |
| `environment-windows-test.txt` | exact conda-forge versions for `env\` |
| `environment-veusz.txt` | Veusz 4.2.1 for `env_veusz\` |
| `app\` | the monitor 1.6.2, exporter 0.1.1, writer, tests, docs |
| `app\tests\run_all_tests.py` | the runner behind step 2 (options: `--quick`, `--only`, `--skip`, `--soak-minutes`, `--gui-seconds`, `--veusz-exe`) |
