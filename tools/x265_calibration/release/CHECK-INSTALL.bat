@echo off
rem ============================================================================
rem  CHECK-INSTALL.bat  --  read-only installation check
rem ----------------------------------------------------------------------------
rem  Runs three checks and writes a report to check-report.txt.
rem  This does NOT modify anything: --status and --validate are read-only.
rem
rem  Run this once after extracting the zip, and again any time you want to
rem  know whether the collector is working.
rem ============================================================================
setlocal enableextensions

set "HERE=%~dp0"
set "EXE=%HERE%x265-calibration-collector.exe"
set "CALIB=%HERE%calibration"
set "REPORT=%HERE%check-report.txt"

if not exist "%EXE%" (
  echo [FAIL] collector executable not found: %EXE%
  echo        Extract the WHOLE zip first.
  pause
  exit /b 2
)

echo ============================================================================ >  "%REPORT%"
echo  x265 calibration collector - installation check                          >> "%REPORT%"
echo  date/time : %DATE% %TIME%                                                >> "%REPORT%"
echo  folder    : %HERE%                                                       >> "%REPORT%"
echo  watching  : %CALIB%                                                      >> "%REPORT%"
echo ============================================================================ >> "%REPORT%"
echo.                                                                          >> "%REPORT%"

echo --- 1/3  version ------------------------------------------------------- >> "%REPORT%"
"%EXE%" --version                                                             >> "%REPORT%" 2>&1
echo.                                                                          >> "%REPORT%"

echo --- 2/3  status (dataset inventory) ----------------------------------- >> "%REPORT%"
"%EXE%" --headless --status --spool "%CALIB%"                                 >> "%REPORT%" 2>&1
echo.                                                                          >> "%REPORT%"

echo --- 3/3  validate (dataset integrity, read-only) ---------------------- >> "%REPORT%"
"%EXE%" --headless --validate "%CALIB%"                                       >> "%REPORT%" 2>&1
echo.                                                                          >> "%REPORT%"

echo ============================================================================ >> "%REPORT%"
echo  Environment variables that the PRODUCTION transcoder needs             >> "%REPORT%"
echo ---------------------------------------------------------------------------- >> "%REPORT%"
echo  ONEXKT_CALIBRATION       = %ONEXKT_CALIBRATION%                          >> "%REPORT%"
echo  ONEXKT_CALIBRATION_SPOOL = %ONEXKT_CALIBRATION_SPOOL%                    >> "%REPORT%"
echo.                                                                          >> "%REPORT%"
echo  Both must be set for the account that runs production, otherwise NO      >> "%REPORT%"
echo  events will ever appear in events\pending\.                              >> "%REPORT%"
echo ============================================================================ >> "%REPORT%"

type "%REPORT%"
echo.
echo Report written to: %REPORT%
echo.
pause
exit /b 0
