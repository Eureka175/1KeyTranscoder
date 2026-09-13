@echo off
rem ============================================================================
rem  INSTALL-SCHEDULED-TASK.bat  --  start the collector automatically at boot
rem ----------------------------------------------------------------------------
rem  Registers a Windows Task Scheduler entry so the collector runs whenever
rem  this machine is on. This is the recommended long-run setup.
rem
rem  Run this once. No administrator rights required for a task that runs as
rem  the current user; run it as Administrator if you want the task to run
rem  whether or not anyone is logged in.
rem
rem  What it does NOT do: it does not touch the production transcoder task, and
rem  it does not set the ONEXKT_* environment variables. Read QUICKSTART.zh-CN.txt
rem  step 2 for those.
rem ============================================================================
setlocal enableextensions

set "HERE=%~dp0"
set "EXE=%HERE%x265-calibration-collector.exe"
set "CALIB=%HERE%calibration"
set "TASK=1KeyTranscoder Calibration Collector"

if not exist "%EXE%" (
  echo [FAIL] collector executable not found: %EXE%
  echo        Extract the WHOLE zip first.
  pause
  exit /b 2
)

net session >nul 2>&1
if %ERRORLEVEL% EQU 0 (
  set "ELEVATED=yes"
) else (
  set "ELEVATED=no"
)

echo ============================================================================
echo  Register scheduled task
echo ----------------------------------------------------------------------------
echo  task name : %TASK%
echo  program   : %EXE%
echo  watching  : %CALIB%
echo  elevated  : %ELEVATED%
echo ============================================================================
echo.

if /I "%ELEVATED%"=="yes" (
  schtasks /Create /TN "%TASK%" /SC ONSTART /RU "%USERNAME%" /RL HIGHEST ^
           /TR "\"%EXE%\" --headless --watch \"%CALIB%\"" /F
) else (
  schtasks /Create /TN "%TASK%" /SC ONSTART /RU "%USERNAME%" /IT ^
           /TR "\"%EXE%\" --headless --watch \"%CALIB%\"" /F
)

if errorlevel 1 (
  echo.
  echo [FAIL] schtasks could not create the task.
  echo        Try: right-click this .bat -^> "Run as administrator".
  echo        Or just use START-COLLECTOR.bat and leave the window open.
  echo.
  pause
  exit /b 1
)

echo.
echo [OK] task registered.
echo.
echo ----------------------------------------------------------------------------
echo  NOW DO THIS BY HAND - Task Scheduler cannot set these from a script:
echo ----------------------------------------------------------------------------
echo  1. Open Task Scheduler (taskschd.msc) and find: %TASK%
echo  2. Properties -^> Settings:
echo       [x] Restart the task if it fails      1 minute, 999 times
echo       [ ] Stop the task if it runs longer than   ...  ^<-- DISABLE THIS
echo       [x] If the task is already running  -^>  Do not start a new instance
echo  3. Verify the two ONEXKT_* environment variables are set for the account
echo     that runs the PRODUCTION transcoder (see QUICKSTART.zh-CN.txt step 2).
echo ----------------------------------------------------------------------------
echo.
echo Current task definition:
schtasks /Query /TN "%TASK%" /V /FO LIST
echo.
pause
exit /b 0
