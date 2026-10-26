@echo off
rem =============================================================================
rem  2_run_all_tests.bat - run every test and make the results zip
rem  Version : 1.0.0   Created/Modified: 2026-10-26   Author: W. W. Wallace
rem  About 20 minutes; "2_run_all_tests.bat --quick" takes about 8. Any option
rem  of tests\run_all_tests.py can be added (--soak-minutes 60, --only ...).
rem  Results: KIT\results\<computer>_<time>\ and AB_TestResults_*.zip
rem =============================================================================
setlocal EnableExtensions
title AB monitor test kit - 2. run all tests
call "%~dp0_env.bat"
if errorlevel 1 (
    pause
    exit /b 1
)
if not exist "%KIT%\results" mkdir "%KIT%\results"
echo.
echo  Running every AB monitor test - results in %KIT%\results
echo  Leave this window open; other programs may be used meanwhile.
echo.
"%PY%" "%APP%\tests\run_all_tests.py" --results "%KIT%\results" %*
set "RC=%ERRORLEVEL%"
start "" explorer "%KIT%\results"
echo.
if "%RC%"=="0" (echo  ALL TESTS PASSED.) else (echo  SOME TESTS FAILED.)
echo  Send the AB_TestResults_*.zip from the results folder back to Claude.
echo.
pause
exit /b %RC%
