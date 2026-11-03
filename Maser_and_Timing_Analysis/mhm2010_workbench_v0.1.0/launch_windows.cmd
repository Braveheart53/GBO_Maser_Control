@echo off
rem MHM2010 Correction Workbench 0.1.0 - Python 3.12 - no fixed local paths
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" MHM2010_Correction_App.py
) else (
  py -3.12 MHM2010_Correction_App.py
)
if errorlevel 1 pause
