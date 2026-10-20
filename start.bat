@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ============================================================
rem  1KeyTranscoder watchfolder launcher
rem  Drop video files into the INPUT folder; they are processed
rem  automatically. Ctrl+C stops the loop.
rem  (edit the variables below to change behaviour)
rem ============================================================
set "INPUT=F:\1KeyTranscoder\watch_input"
set "OUTPUT=F:\1KeyTranscoder\watch_output"
set "ENCODER=nvenc"
set "PRESET=hq"
set "CHECK=basic"
set "JOBS=1"
set "INTERVAL=60"

rem ---- 声道延时对齐 (v0.8.7) ----
rem  这条启动链以前从不转发 --channel-sync, 所以经由它产出的成品**从来没有
rem  做过对齐**: 实测归档盘 751 个成品的抽样件仍带 19.5~20.8 ms 通道偏移,
rem  且与源素材逐采样相同。下面两个变量现在默认开启, 把这一项接进批处理。
rem    SYNC=1    -> --channel-sync (4 声道恒定延时对齐, 纯整数样本移位)
rem    GROUPS=1  -> --channel-sync-groups (分组锚点; 只在"一支片子里有两支
rem                 互不相干的麦克风"时才多修几条轨, 其它情况行为不变)
rem    ACCURATE=1 -> --channel-sync-accurate (精确模式: 小偏移也尝试修, 但
rem                 每一级都由施加后的独立复检残差验收, 1 样本做不到退 2
rem                 样本, 再做不到退回常规 5 样本并告警)
rem  想关掉(例如先跑一遍对照组): 把 SYNC 改成 0。SYNC=0 是**总开关**: 即使
rem  GROUPS/ACCURATE 还是 1 也不会传任何对齐参数 (否则"关掉"只关掉一半,
rem  而日志里看不出区别)。分组与精确模式只在 SYNC=1 时才跟随各自变量。
set "SYNC=1"
set "GROUPS=1"
set "ACCURATE=0"

set "SYNCARGS="
if "%SYNC%"=="1" set "SYNCARGS=%SYNCARGS% --channel-sync"
if "%SYNC%"=="1" if "%GROUPS%"=="1" set "SYNCARGS=%SYNCARGS% --channel-sync-groups"
if "%SYNC%"=="1" if "%ACCURATE%"=="1" set "SYNCARGS=%SYNCARGS% --channel-sync-accurate"

if not exist "%INPUT%" mkdir "%INPUT%"
if not exist "%OUTPUT%" mkdir "%OUTPUT%"

echo ============================================================
echo   1KeyTranscoder watchfolder
echo   Encoder: %ENCODER%   Preset: %PRESET%
echo   Check  : %CHECK%    Jobs: %JOBS%
echo   Input : %INPUT%
echo   Output: %OUTPUT%
echo   Poll  : every %INTERVAL% seconds
echo   Sync  : SYNC=%SYNC% GROUPS=%GROUPS% ACCURATE=%ACCURATE%  (args:%SYNCARGS%)
echo ============================================================
echo.

python watchfolder.py --input "%INPUT%" --output "%OUTPUT%" --encoder %ENCODER% --preset %PRESET% --check %CHECK% --jobs %JOBS% --interval %INTERVAL%%SYNCARGS%

echo.
echo watchfolder exited (rc=%ERRORLEVEL%)
pause
