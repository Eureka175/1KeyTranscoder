@echo off
rem ============================================================================
rem  VERIFY-PACKAGE.bat  --  prove the packaged executables are self-contained
rem ----------------------------------------------------------------------------
rem  Runs the frozen-exe verifier against every executable in this package and
rem  reports whether each one really carries its own Python runtime, its schema
rem  and no site-packages dependency. Useful before trusting the package on a
rem  host that has no Python at all.
rem
rem  Needs no Python: the verifier is packaged as a standalone exe in tools\.
rem ============================================================================
setlocal enableextensions

set "HERE=%~dp0"
set "TOOL=%HERE%tools\x265-verify.exe"
set "OUT=%HERE%verify-report.txt"

if not exist "%TOOL%" (
  echo [FAIL] verifier not found: %TOOL%
  pause
  exit /b 2
)

echo ============================================================================ >  "%OUT%"
echo  package verification                                                      >> "%OUT%"
echo  date/time : %DATE% %TIME%                                                >> "%OUT%"
echo  package   : %HERE%                                                      >> "%OUT%"
echo ============================================================================ >> "%OUT%"
echo.                                                                          >> "%OUT%"

set "FAILED=0"
for %%F in ("%HERE%x265-calibration-collector.exe" "%HERE%tools\x265-analyze.exe" "%HERE%tools\x265-verify.exe" "%HERE%tools\x265-deploy.exe") do (
  echo --- %%F --------------------------------------------------------------- >> "%OUT%"
  "%TOOL%" --exe "%%~fF" --inspect-archive                                   >> "%OUT%" 2>&1
  if errorlevel 1 set "FAILED=1"
  echo.                                                                       >> "%OUT%"
)

echo ============================================================================ >> "%OUT%"
if "%FAILED%"=="0" (
  echo  RESULT: every executable is self-contained.                           >> "%OUT%"
) else (
  echo  RESULT: at least one executable FAILED verification.                   >> "%OUT%"
)
echo ============================================================================ >> "%OUT%"

type "%OUT%"
echo.
echo ============================================================================
echo  Result: %FAILED% (0 = all good)
echo  Full report saved to: %OUT%
echo ============================================================================
echo.
pause
exit /b %FAILED%
