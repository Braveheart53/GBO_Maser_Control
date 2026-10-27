@echo off
rem =============================================================================
rem  1_setup_environment.bat - create the Python 3.12 test environment
rem  Version : 1.0.0   Created/Modified: 2026-10-26   Author: W. W. Wallace
rem  Creates KIT\env from environment-windows-test.txt (conda-forge, exact
rem  versions) with mamba, conda or micromamba - found on PATH or in the usual
rem  Miniforge / Miniconda / Anaconda folders. Optionally KIT\env_veusz with
rem  Veusz 4.2.1. Without any conda: a venv from "py -3.12" + pip.
rem  Nothing outside this folder is changed; delete env\ to remove it.
rem =============================================================================
setlocal EnableExtensions EnableDelayedExpansion
title AB monitor test kit - 1. set up the Python environment
set "KIT=%~dp0"
if "%KIT:~-1%"=="\" set "KIT=%KIT:~0,-1%"
if exist "%KIT%\app\ab_power_meter_monitor.py" (set "APP=%KIT%\app") else (set "APP=%KIT%\..")
set "ENV=%KIT%\env"
set "ENV_VEUSZ=%KIT%\env_veusz"
echo.
echo  AB Power Meter Monitor - Windows test kit setup
echo  Kit folder: !KIT!
echo.

rem --- conda prefixes must not contain spaces ----------------------------------
if not "!KIT!"=="!KIT: =!" (
    echo  ERROR: the kit folder path contains a space. Move the kit to a short
    echo         path without spaces, e.g. C:\ABTest, and run this again.
    goto :fail
)

rem --- find mamba / conda / micromamba ------------------------------------------
set "MGR="
for %%M in (mamba conda micromamba) do (
    if not defined MGR (
        where %%M >nul 2>nul
        if not errorlevel 1 set "MGR=%%M"
    )
)
if not defined MGR (
    for %%D in ("%USERPROFILE%\miniforge3" "%LOCALAPPDATA%\miniforge3" "%ProgramData%\miniforge3" "%USERPROFILE%\mambaforge" "%USERPROFILE%\miniconda3" "%LOCALAPPDATA%\miniconda3" "%ProgramData%\miniconda3" "%USERPROFILE%\anaconda3" "%ProgramData%\anaconda3") do (
        if not defined MGR if exist "%%~D\condabin\mamba.bat" set "MGR=%%~D\condabin\mamba.bat"
        if not defined MGR if exist "%%~D\Scripts\mamba.exe" set "MGR=%%~D\Scripts\mamba.exe"
        if not defined MGR if exist "%%~D\condabin\conda.bat" set "MGR=%%~D\condabin\conda.bat"
    )
)

rem --- an existing environment is kept (delete env\ to rebuild) --------------------
if exist "!ENV!\python.exe" (
    echo  The environment already exists: !ENV!
    echo  To rebuild it, delete that folder and run this again.
    goto :veusz
)
if exist "!ENV!\Scripts\python.exe" (
    echo  The environment already exists: !ENV!
    goto :verify
)
if not defined MGR goto :pip_fallback

echo  Package manager: !MGR!
echo  Creating !ENV! (conda-forge, Python 3.12.15) - this takes a few minutes...
echo.
call "!MGR!" create -y -p "!ENV!" -c conda-forge --override-channels --file "!KIT!\environment-windows-test.txt"
if errorlevel 1 goto :fail
if not exist "!ENV!\python.exe" goto :fail

:veusz
if exist "!ENV_VEUSZ!\Scripts\veusz.exe" (
    echo  Veusz environment present: !ENV_VEUSZ!
    goto :verify
)
if not defined MGR goto :verify
echo.
choice /c YN /t 60 /d Y /m " Also install Veusz 4.2.1 in a separate environment (about 0.5 GB; renders and opens .vszh5)"
if errorlevel 2 goto :verify
call "!MGR!" create -y -p "!ENV_VEUSZ!" -c conda-forge --override-channels --file "!KIT!\environment-veusz.txt"
if errorlevel 1 (
    echo  WARNING: the Veusz environment could not be created - the real-Veusz checks
    echo           will use an installed Veusz if there is one, otherwise they are skipped.
)
goto :verify

rem --- no conda: python.org Python 3.12 + venv + pip ---------------------------
:pip_fallback
where py >nul 2>nul
if errorlevel 1 goto :no_python
py -3.12 -c "import sys" >nul 2>nul
if errorlevel 1 goto :no_python
echo  No mamba/conda found - using Python 3.12 from python.org with a venv and pip.
py -3.12 -m venv "!ENV!"
if errorlevel 1 goto :fail
"!ENV!\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :fail
"!ENV!\Scripts\python.exe" -m pip install -r "!APP!\requirements-py312.txt"
if errorlevel 1 goto :fail
goto :verify

:no_python
echo  ERROR: neither mamba/conda nor Python 3.12 ("py -3.12") was found.
echo         Install Miniforge (https://github.com/conda-forge/miniforge) and run this again.
goto :fail

rem --- check the result -----------------------------------------------------------
:verify
call "!KIT!\_env.bat"
if errorlevel 1 goto :fail
echo.
echo  Checking the environment with !PY!
"!PY!" -c "import numpy, h5py, astropy, openpyxl, psutil, requests, bs4, lxml, prometheus_client, PySide6, qtpy, matplotlib; print('  imports OK - Python', __import__('sys').version.split()[0])"
if errorlevel 1 goto :fail
"!PY!" "!APP!\ab_power_meter_monitor.py" --help >nul
if errorlevel 1 goto :fail
echo  Monitor starts OK.
echo.
echo  SETUP COMPLETE. Next: 2_run_all_tests.bat
echo.
pause
exit /b 0

:fail
echo.
echo  SETUP FAILED - see the messages above.
echo.
pause
exit /b 1
