@echo off
rem =============================================================================
rem  3_task_manager_check.bat - watch a RAM flush give memory back to Windows
rem  Version : 1.0.0   Created/Modified: 2026-10-26   Author: W. W. Wallace
rem  Runs tests\windows_memory_check.py with a 20 s pause after every flush so
rem  the drop can be seen in Task Manager. Takes about 3 minutes.
rem =============================================================================
setlocal EnableExtensions
title AB monitor test kit - 3. Task Manager memory check
call "%~dp0_env.bat"
if errorlevel 1 (
    pause
    exit /b 1
)
if not exist "%KIT%\results" mkdir "%KIT%\results"
echo.
echo  1. Open Task Manager (Ctrl+Shift+Esc) and go to the Details tab.
echo  2. Right-click a column header - Select columns - tick "Working set (memory)"
echo     and "Commit size".
echo  3. Six runs follow. Each prints its PID, fills about 230 MB, flushes and then
echo     waits 20 s: find that python.exe PID and watch "Working set" fall.
echo     gc_only = 1.6.0 behaviour; heap_only = heap decommit; full = 1.6.x default.
echo.
pause
"%PY%" "%APP%\tests\windows_memory_check.py" --hold 20 --json "%KIT%\results\task_manager_check.json"
echo.
echo  Table above; also saved to %KIT%\results\task_manager_check.json
pause
