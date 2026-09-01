@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ============================================================
rem  1KeyTranscoder watchfolder launcher (barebone)
rem  Drop video files into the INPUT folder; they are processed
rem  automatically. Ctrl+C stops the loop.
rem ============================================================
set "INPUT=F:\1KeyTranscoder\watch_input"
set "OUTPUT=F:\1KeyTranscoder\watch_output"
set "ENCODER=nvenc"
set "PRESET=hq"
set "INTERVAL=60"

if not exist "%INPUT%" mkdir "%INPUT%"
if not exist "%OUTPUT%" mkdir "%OUTPUT%"

echo ============================================================
echo   1KeyTranscoder watchfolder
echo   Encoder: %ENCODER%   Preset: %PRESET%
echo   Input : %INPUT%
echo   Output: %OUTPUT%
echo   Poll  : every %INTERVAL% seconds
echo   (edit start.bat to change these)
echo ============================================================
echo.

python watchfolder.py --input "%INPUT%" --output "%OUTPUT%" --encoder %ENCODER% --preset %PRESET% --interval %INTERVAL% --auto-downgrade

echo.
echo watchfolder exited (rc=%ERRORLEVEL%)
pause
