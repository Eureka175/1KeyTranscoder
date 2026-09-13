@echo off
rem ============================================================================
rem  START-COLLECTOR.bat  --  start or restart the calibration collector
rem ----------------------------------------------------------------------------
rem  Double-click this file, or let Task Scheduler run it.
rem  It runs the collector in the FOREGROUND and keeps this window open until
rem  you press Ctrl+C or close the window.
rem
rem  Calibration data directory:  <this folder>\calibration
rem  If you want it somewhere else, edit the CALIB= line below to an
rem  absolute path, or run the exe directly with --watch <path>.
rem ============================================================================
setlocal enableextensions

set "HERE=%~dp0"
set "EXE=%HERE%x265-calibration-collector.exe"
set "CALIB=%HERE%calibration"

if not exist "%EXE%" (
  echo [FAIL] collector executable not found:
  echo        %EXE%
  echo.
  echo        Extract the WHOLE zip before running anything. Running the exe
  echo        from inside the zip preview window does not work.
  echo.
  pause
  exit /b 2
)

echo ============================================================================
echo  x265 calibration collector
echo ----------------------------------------------------------------------------
echo  program   : %EXE%
echo  watching  : %CALIB%
echo.
echo  Leave this window open. Production does NOT depend on this program:
echo  even if you close it, production keeps running and events keep piling up
echo  in events\pending\ until the collector is started again.
echo.
echo  Stop with Ctrl+C or by closing this window.
echo ============================================================================
echo.

if not exist "%CALIB%\events\pending" mkdir "%CALIB%\events\pending" 2>nul

"%EXE%" --headless --watch "%CALIB%" --verbose
set "RC=%ERRORLEVEL%"

echo.
echo [collector exited with code %RC%]
echo   0 = clean stop      3 = another collector instance is already running
echo   2 = bad arguments   4 = internal error
echo.
pause
exit /b %RC%
