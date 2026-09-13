@echo off
rem ============================================================================
rem  ANALYZE-DATA.bat  --  analyze the collected calibration dataset
rem ----------------------------------------------------------------------------
rem  Produces a human-readable summary of everything collected so far and saves
rem  it next to this script. Read-only: it never writes to the dataset, and
rem  never writes x265.json / x265_scaling.json.
rem
rem  Needs no Python: the analyzer is packaged as a standalone exe in tools\.
rem ============================================================================
setlocal enableextensions

set "HERE=%~dp0"
set "TOOL=%HERE%tools\x265-analyze.exe"
set "CALIB=%HERE%calibration"
set "OUT=%HERE%analysis-report.txt"

if not exist "%TOOL%" (
  echo [FAIL] analyzer not found: %TOOL%
  echo        Extract the WHOLE zip first.
  pause
  exit /b 2
)

if not exist "%CALIB%\samples" (
  echo [FAIL] no dataset found at: %CALIB%\samples
  echo        Nothing has been collected yet.
  echo.
  echo        Check, in this order:
  echo          1. the two ONEXKT_* environment variables are set for the
  echo             account that runs PRODUCTION  ^(see QUICKSTART.zh-CN.txt^)
  echo          2. events\pending\ actually has *.json files in it
  echo          3. the collector has been started at least once
  echo.
  pause
  exit /b 1
)

echo ============================================================================ >  "%OUT%"
echo  x265 calibration dataset analysis                                         >> "%OUT%"
echo  date/time : %DATE% %TIME%                                                >> "%OUT%"
echo  dataset   : %CALIB%                                                      >> "%OUT%"
echo ============================================================================ >> "%OUT%"
echo.                                                                          >> "%OUT%"
"%TOOL%" "%CALIB%"                                                            >> "%OUT%" 2>&1
set "RC=%ERRORLEVEL%"
echo.                                                                          >> "%OUT%"
echo [analyzer exit code %RC%]                                                 >> "%OUT%"

type "%OUT%"
echo.
echo ============================================================================
echo  Full report saved to: %OUT%
echo ============================================================================
echo.
pause
exit /b %RC%
