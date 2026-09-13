@echo off
rem ============================================================================
rem  UNINSTALL-SCHEDULED-TASK.bat  --  remove the collector's scheduled task
rem ----------------------------------------------------------------------------
rem  Removes ONLY the collector's Task Scheduler entry. It does not delete the
rem  calibration data, does not touch the production transcoder task, and does
rem  not remove the ONEXKT_* environment variables.
rem
rem  The data in calibration\ (next to this script) is untouched. Keep it -
rem  that is the whole point of the last month.
rem ============================================================================
setlocal enableextensions
set "TASK=1KeyTranscoder Calibration Collector"

echo Removing scheduled task: %TASK%
schtasks /Delete /TN "%TASK%" /F
if errorlevel 1 (
  echo.
  echo [FAIL] task not found, or insufficient rights.
  echo        Try running this as administrator, or delete it by hand in
  echo        Task Scheduler (taskschd.msc).
  echo.
  pause
  exit /b 1
)
echo.
echo [OK] task removed. Calibration data was NOT deleted.
echo      Your data is still in: %~dp0..\calibration
echo.
pause
exit /b 0
