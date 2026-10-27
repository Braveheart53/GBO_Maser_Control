@echo off
rem =============================================================================
rem  _env.bat - shared settings for the AB monitor Windows test kit
rem  Version : 1.0.1   Created/Modified: 2026-10-26   Author: W. W. Wallace
rem  Called by 2_ ... 5_*.bat (not meant to be run on its own). Sets:
rem    KIT  this folder          APP  the program folder (KIT\app or KIT\..)
rem    ENV  KIT\env              PY   the environment's python.exe
rem  PATH is NOT changed (1.0.1): conda-forge Python 3.11+ adds its own bin,
rem  Scripts and Library\bin folders to the DLL search path by itself, and a
rem  monitor env first on PATH made Veusz (env_veusz) load the wrong HDF5/Qt.
rem =============================================================================
set "KIT=%~dp0"
if "%KIT:~-1%"=="\" set "KIT=%KIT:~0,-1%"
if exist "%KIT%\app\ab_power_meter_monitor.py" (set "APP=%KIT%\app") else (set "APP=%KIT%\..")
set "ENV=%KIT%\env"
set "PY="
if exist "%ENV%\python.exe" set "PY=%ENV%\python.exe"
if not defined PY if exist "%ENV%\Scripts\python.exe" set "PY=%ENV%\Scripts\python.exe"
if not defined PY (
    echo.
    echo  The test environment is missing - run 1_setup_environment.bat first.
    echo.
    exit /b 1
)
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "QT_API=pyside6"
exit /b 0
