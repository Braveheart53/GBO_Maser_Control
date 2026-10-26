@echo off
rem =============================================================================
rem  4_gui_with_emulator.bat - the real GUI against two emulated meters
rem  Version : 1.0.0   Created/Modified: 2026-10-26   Author: W. W. Wallace
rem  Starts the meter emulator (127.0.0.2 and 127.0.0.3, port 18080) in its own
rem  window, then the monitor GUI pointed at it, writing to KIT\gui_output.
rem  Follow the GUI checklist in START_HERE. Close the emulator window after.
rem =============================================================================
setlocal EnableExtensions
title AB monitor test kit - 4. GUI with emulated meters
call "%~dp0_env.bat"
if errorlevel 1 (
    pause
    exit /b 1
)
set "OUT=%KIT%\gui_output"
start "AB meter emulator - close this window when finished" "%PY%" "%APP%\tests\ab_meter_emulator.py" --hosts 127.0.0.2,127.0.0.3 --port 18080
timeout /t 3 /nobreak >nul
echo  Monitor GUI starting - output folder %OUT%
"%PY%" "%APP%\ab_power_meter_monitor.py" --gui --ips 127.0.0.2,127.0.0.3 --url-template "http://{ip}:18080/{page}" --output-dir "%OUT%" --interval 2
echo  GUI closed (exit code %ERRORLEVEL%). Remember to close the emulator window.
pause
