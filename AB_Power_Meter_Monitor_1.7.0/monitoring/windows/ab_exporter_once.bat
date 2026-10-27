@echo off
rem =============================================================================
rem  ab_exporter_once.bat - ONE poll of the AB meters, pushed to a Pushgateway
rem  Version : 0.0.1   Created/Modified: 2026-10-27   Author: W. W. Wallace
rem  For the Windows Task Scheduler (single-run mode). Writes no files.
rem  Edit PY, APP, IPS and GATEWAY below, then register it, e.g. every minute:
rem    schtasks /Create /TN "AB meters single poll" /SC MINUTE /MO 1 /TR "C:\ABMeter\monitoring\windows\ab_exporter_once.bat"
rem  Exit code: 0 ok, 1 no meter answered, 2 push failed (Task Scheduler "Last Run Result").
rem =============================================================================
setlocal EnableExtensions
set "PY=C:\ABMeter\env\python.exe"
set "APP=C:\ABMeter"
set "IPS=10.16.130.50,10.16.130.54"
set "GATEWAY=http://localhost:9091"
"%PY%" "%APP%\ab_prometheus_exporter.py" --once --ips %IPS% --push-gateway %GATEWAY%
exit /b %ERRORLEVEL%
