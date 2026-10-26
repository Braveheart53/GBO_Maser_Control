@echo off
rem =============================================================================
rem  5_headless_with_emulator.bat - visible headless run; stop with Ctrl+Break
rem  Version : 1.0.0   Created/Modified: 2026-10-26   Author: W. W. Wallace
rem  Starts the meter emulator in its own window, then the headless monitor in
rem  THIS window with its log visible and every output on (KIT\headless_output).
rem  Stop it with Ctrl+C or Ctrl+Break: the final FITS/XLSX writes must run
rem  before it exits. Answer N if cmd then asks "Terminate batch job (Y/N)?".
rem  Another way to stop: in a second window, from the app folder,
rem      python ab_stop.py --output-dir KIT\headless_output
rem =============================================================================
setlocal EnableExtensions
title AB monitor test kit - 5. headless run (Ctrl+Break to stop)
call "%~dp0_env.bat"
if errorlevel 1 (
    pause
    exit /b 1
)
set "OUT=%KIT%\headless_output"
start "AB meter emulator - close this window when finished" "%PY%" "%APP%\tests\ab_meter_emulator.py" --hosts 127.0.0.2,127.0.0.3 --port 18080
timeout /t 3 /nobreak >nul
"%PY%" "%APP%\tests\run_headless_demo.py" --headless --count 0 --interval 2 --ips 127.0.0.2,127.0.0.3 --url-template "http://{ip}:18080/{page}" --output-dir "%OUT%"
echo.
echo  Monitor exited with code %ERRORLEVEL% - look for "Final writes done" above.
echo  Output: %OUT%   (close the emulator window now)
pause
