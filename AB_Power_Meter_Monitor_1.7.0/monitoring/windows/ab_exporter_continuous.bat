@echo off
rem =============================================================================
rem  ab_exporter_continuous.bat - the exporter in CONTINUOUS mode on Windows
rem  Version : 0.0.1   Created/Modified: 2026-10-27   Author: W. W. Wallace
rem  Prometheus pulls http://<this PC>:9184/metrics (job ab_power_meter in
rem  monitoring\prometheus\prometheus.yml). Stop with Ctrl+C or Ctrl+Break.
rem  Writes no files. Edit PY, APP and IPS below.
rem =============================================================================
setlocal EnableExtensions
set "PY=C:\ABMeter\env\python.exe"
set "APP=C:\ABMeter"
set "IPS=10.16.130.50,10.16.130.54"
title AB meter Prometheus exporter (Ctrl+C to stop)
"%PY%" "%APP%\ab_prometheus_exporter.py" --ips %IPS% --interval 30 --port 9184
echo  Exporter exited with code %ERRORLEVEL%.
pause
