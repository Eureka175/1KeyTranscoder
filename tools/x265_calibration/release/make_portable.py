"""Assemble the portable 1KeyTranscoder x265 test workspace.

Produces dist/x265_portable/1KT-x265-portable/, a folder that runs the real
production x265 workflow on a machine with NO Python and NO preinstalled
tools: drop media into input/, run one launcher, everything else is automatic.

    python tools/x265_calibration/release/make_portable.py [--clean]

Layout produced:

    1KT-x265-portable/
      RUN-TEST.bat            <- double-click: watcher + collector together
      STOP.bat                <- stop both (or just close the window)
      CHECK-TEST.bat          <- read-only health check
      README-FIRST.txt        <- one page, Chinese + English
      config/                 x265.json, x265_scaling.json, collector.toml
      bin/                    1kt.exe, 1kt-watch.exe, x265-calibration-collector.exe
      tools/                  ffmpeg.exe, ffprobe.exe, GPAC/, +
                              x265-analyze/verify/deploy.exe
      input/                  DROP MEDIA HERE
      output/                 transcoded results
      calibration/            calibration dataset (the point of the test)
      logs/ , work/           run logs and scratch (work/ is cleaned per job)

Everything except the pristine copies under tools/ is writable, and the
workspace is self-contained: nothing outside the folder is referenced.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[3]           # F:\1KeyTranscoder
REPO_TOOLS = PROJECT_ROOT / "tools"
DIST_EXES = PROJECT_ROOT / "dist" / "x265_calibration"
OUT_ROOT = PROJECT_ROOT / "dist" / "x265_portable"
PKG = "1KT-x265-portable"

# (source, destination inside the package)
BIN_FILES = (
    (DIST_EXES / "1kt.exe", "bin/1kt.exe"),
    (DIST_EXES / "1kt-watch.exe", "bin/1kt-watch.exe"),
    (DIST_EXES / "x265-calibration-collector.exe",
     "bin/x265-calibration-collector.exe"),
)

TOOL_FILES = (
    (DIST_EXES / "x265-analyze.exe", "tools/x265-analyze.exe"),
    (DIST_EXES / "x265-verify.exe", "tools/x265-verify.exe"),
    (DIST_EXES / "x265-deploy.exe", "tools/x265-deploy.exe"),
)

# Third-party tool drops. ffmpeg/ffprobe are REQUIRED for x265; GPAC is used
# by the Sony/DJI preservation paths and by --channel-sync, so it ships too.
# NVEncC/QSVEncC/avhw are deliberately absent: this bundle is x265-only.
TOOL_TREES = (
    (REPO_TOOLS / "ffmpeg.exe", "tools/ffmpeg.exe"),
    (REPO_TOOLS / "ffprobe.exe", "tools/ffprobe.exe"),
    (REPO_TOOLS / "GPAC", "tools/GPAC"),
)

CONFIG_FILES = (
    (PROJECT_ROOT / "x265.json", "config/x265.json"),
    (PROJECT_ROOT / "x265_scaling.json", "config/x265_scaling.json"),
)

WORKSPACE_DIRS = (
    "input",
    "output",
    "calibration",
    "logs",
    "work",
    "bin",
    "tools",
    "config",
)


def human(num: int) -> str:
    value = float(num)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024.0 or unit == "GiB":
            return "%.2f %s" % (value, unit)
        value /= 1024.0
    return "%.2f GiB" % value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_tree(source: Path, target: Path) -> int:
    """Copy a directory (or file) and return the bytes written."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        return sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
    shutil.copy2(source, target)
    return target.stat().st_size


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\r\n")


# ---------------------------------------------------------------------------
# workspace files
# ---------------------------------------------------------------------------

RUN_TEST = r"""@echo off
rem ============================================================================
rem  RUN-TEST.bat  --  start the whole portable x265 test
rem ----------------------------------------------------------------------------
rem  Starts TWO independent processes and leaves them running:
rem
rem    1) 1kt-watch.exe                    watches input\, transcodes everything
rem       x265-calibration-collector.exe   records calibration data
rem
rem  Drop media files into the input\ folder at any time - they are picked up
rem  on the next scan (every 60 s). Already-transcoded files are skipped, so
rem  re-running this launcher is always safe.
rem
rem  Close this window (or press Ctrl+C) to stop both.
rem  Needs NO Python and no preinstalled tools: everything is in this folder.
rem ============================================================================
setlocal enableextensions
chcp 65001 >nul
cd /d "%~dp0"

set "HERE=%~dp0"
set "BIN=%HERE%bin"
set "PY=%BIN%\1kt.exe"
set "WATCH=%BIN%\1kt-watch.exe"
set "COLLECTOR=%BIN%\x265-calibration-collector.exe"
set "FFMPEG=%HERE%tools\ffmpeg.exe"
set "FFPROBE=%HERE%tools\ffprobe.exe"
set "GPAC=%HERE%tools\GPAC"

rem ---- the knobs you are most likely to change -------------------------------
set "PRESET=hq"
set "CHECK=basic"
set "JOBS=1"
set "INTERVAL=60"
rem ---------------------------------------------------------------------------

echo ============================================================================
echo  1KeyTranscoder - portable x265 test
echo ----------------------------------------------------------------------------
echo  preset=%PRESET%  check=%CHECK%  jobs=%JOBS%  scan=%INTERVAL%s
echo  input : %HERE%input
echo  output: %HERE%output
echo  data  : %HERE%calibration
echo ============================================================================
echo.

for %%F in ("%PY%" "%WATCH%" "%COLLECTOR%" "%FFMPEG%" "%FFPROBE%") do (
  if not exist %%F (
    echo [FAIL] missing: %%F
    echo        Extract the WHOLE folder before running anything.
    pause
    exit /b 2
  )
)

if not exist "%HERE%input"      mkdir "%HERE%input"
if not exist "%HERE%output"     mkdir "%HERE%output"
if not exist "%HERE%logs"       mkdir "%HERE%logs"
if not exist "%HERE%calibration\events\pending" mkdir "%HERE%calibration\events\pending"

rem ---- 1) calibration collector (independent sibling process) ---------------
echo [1/2] starting calibration collector...
start "1kt calibration collector" /min "%COLLECTOR%" --headless --watch "%HERE%calibration" --job-final-timeout 900

rem give it a moment to take its lock before the watcher starts writing events
ping -n 3 127.0.0.1 >nul

rem ---- 2) watch folder + transcoder ------------------------------------------
echo [2/2] starting transcoder watcher...
echo.
echo ----------------------------------------------------------------------------
echo  Now drop media files into:
echo      %HERE%input
echo  They are transcoded automatically. Leave this window open.
echo ----------------------------------------------------------------------------
echo.

rem The watcher locates the transcoder and the bundled toolchain itself
rem (bin\1kt.exe, tools\ffmpeg.exe, tools\GPAC, config\x265.json), so this
rem stays a SHORT single command on purpose: a long caret-continued line with
rem nested quotes did not survive cmd.exe in testing and silently started the
rem transcoder without its tool paths.
"%WATCH%" --input "%HERE%input" --output "%HERE%output" --preset %PRESET% --check %CHECK% --jobs %JOBS% --interval %INTERVAL% --calibration-spool "%HERE%calibration"

set "RC=%ERRORLEVEL%"
echo.
echo [watcher stopped, rc=%RC%]  stopping the collector...
taskkill /IM x265-calibration-collector.exe /F >nul 2>&1
echo Done. Results in the output folder, calibration data in the calibration folder.
pause
exit /b %RC%
"""

CHECK_TEST = r"""@echo off
rem ============================================================================
rem  CHECK-TEST.bat  --  read-only health check of this portable test
rem ----------------------------------------------------------------------------
rem  Writes check-report.txt. Does NOT modify anything.
rem ============================================================================
setlocal enableextensions
chcp 65001 >nul
cd /d "%~dp0"
set "HERE=%~dp0"
set "REPORT=%HERE%check-report.txt"

echo ============================================================================ >  "%REPORT%"
echo  1KeyTranscoder portable x265 test - health check                          >> "%REPORT%"
echo  date/time : %DATE% %TIME%                                                >> "%REPORT%"
echo  folder    : %HERE%                                                       >> "%REPORT%"
echo ============================================================================ >> "%REPORT%"
echo.                                                                          >> "%REPORT%"

echo --- transcoder version -------------------------------------------------- >> "%REPORT%"
"%HERE%bin\1kt.exe" --version                                                 >> "%REPORT%" 2>&1
echo.                                                                          >> "%REPORT%"

echo --- bundled toolchain --------------------------------------------------- >> "%REPORT%"
"%HERE%tools\ffmpeg.exe" -version 2>&1 | findstr /i "ffmpeg version"            >> "%REPORT%"
"%HERE%tools\ffprobe.exe" -version 2>&1 | findstr /i "ffprobe version"          >> "%REPORT%"
"%HERE%tools\GPAC\mp4box.exe" -version 2>&1 | findstr /i /c:"MP4Box"            >> "%REPORT%"
echo.                                                                          >> "%REPORT%"

echo --- media waiting in input\ --------------------------------------------- >> "%REPORT%"
dir /b "%HERE%input"                                                           >> "%REPORT%" 2>&1
echo.                                                                          >> "%REPORT%"

echo --- results in output\ -------------------------------------------------- >> "%REPORT%"
dir /b "%HERE%output"                                                          >> "%REPORT%" 2>&1
echo.                                                                          >> "%REPORT%"

echo --- calibration dataset ------------------------------------------------ >> "%REPORT%"
"%HERE%bin\x265-calibration-collector.exe" --headless --status --spool "%HERE%calibration" >> "%REPORT%" 2>&1
echo.                                                                          >> "%REPORT%"

echo --- dataset integrity (read-only) ------------------------------------- >> "%REPORT%"
"%HERE%bin\x265-calibration-collector.exe" --headless --validate "%HERE%calibration"       >> "%REPORT%" 2>&1

type "%REPORT%"
echo.
echo Report saved to: %REPORT%
pause
exit /b 0
"""

ANALYZE_TEST = r"""@echo off
rem ============================================================================
rem  ANALYZE-DATA.bat  --  calibration report for everything collected so far
rem ============================================================================
setlocal enableextensions
chcp 65001 >nul
cd /d "%~dp0"
set "HERE=%~dp0"
set "OUT=%HERE%analysis-report.txt"

if not exist "%HERE%calibration\samples" (
  echo [FAIL] nothing collected yet: %HERE%calibration\samples does not exist
  echo        Run RUN-TEST.bat and transcode at least one file first.
  pause
  exit /b 1
)

"%HERE%tools\x265-analyze.exe" "%HERE%calibration" > "%OUT%" 2>&1
set "RC=%ERRORLEVEL%"
type "%OUT%"
echo.
echo Report saved to: %OUT%
pause
exit /b %RC%
"""

COLLECT_RESULTS = r"""@echo off
rem ============================================================================
rem  COLLECT-RESULTS.bat  --  package everything for the trip back
rem ----------------------------------------------------------------------------
rem  Creates results-to-send-back.zip next to this script, containing:
rem
rem     calibration\samples\     the calibration dataset (the important part)
rem     analysis-report.txt      the analysis report
rem     calibration\summaries\   per-day rollups
rem     logs\                    transcode logs
rem     check-report.txt         health check output, if you ran it
rem
rem  Uses PowerShell's Compress-Archive, which ships with Windows 10/11.
rem  Nothing is deleted: this only copies into a zip.
rem ============================================================================
setlocal enableextensions
chcp 65001 >nul
cd /d "%~dp0"
set "HERE=%~dp0"
set "STAGE=%HERE%_results_stage"
set "OUT=%HERE%results-to-send-back.zip"

if not exist "%HERE%calibration\samples" (
  echo [FAIL] no dataset yet: %HERE%calibration\samples is missing.
  echo        Run RUN-TEST.bat and transcode something first.
  pause
  exit /b 1
)

echo [1/3] refreshing the analysis report...
if exist "%HERE%tools\x265-analyze.exe" (
  "%HERE%tools\x265-analyze.exe" "%HERE%calibration" > "%HERE%analysis-report.txt" 2>&1
)

echo [2/3] staging files to include...
if exist "%STAGE%" rmdir /s /q "%STAGE%"
mkdir "%STAGE%" 2>nul
xcopy /e /i /q /y "%HERE%calibration\samples"     "%STAGE%\calibration\samples\"     >nul
if exist "%HERE%calibration\summaries" xcopy /e /i /q /y "%HERE%calibration\summaries" "%STAGE%\calibration\summaries\" >nul
if exist "%HERE%calibration\events\archive" xcopy /e /i /q /y "%HERE%calibration\events\archive" "%STAGE%\calibration\events\archive\" >nul
if exist "%HERE%calibration\errors" xcopy /e /i /q /y "%HERE%calibration\errors" "%STAGE%\calibration\errors\" >nul
if exist "%HERE%logs"            xcopy /e /i /q /y "%HERE%logs"            "%STAGE%\logs\"            >nul
if exist "%HERE%analysis-report.txt" copy /y "%HERE%analysis-report.txt" "%STAGE%\" >nul
if exist "%HERE%check-report.txt"    copy /y "%HERE%check-report.txt"    "%STAGE%\" >nul
if exist "%HERE%README-FIRST.txt"    copy /y "%HERE%README-FIRST.txt"    "%STAGE%\" >nul
if exist "%HERE%PACKAGE-CONTENTS.txt" copy /y "%HERE%PACKAGE-CONTENTS.txt" "%STAGE%\" >nul

echo [3/3] compressing...
if exist "%OUT%" del /q "%OUT%"
powershell -NoProfile -Command "Compress-Archive -Path '%STAGE%\*' -DestinationPath '%OUT%' -CompressionLevel Optimal -Force"
if errorlevel 1 (
  echo.
  echo [FAIL] Compress-Archive failed. The staged files are still in:
  echo        %STAGE%
  echo        Zip that folder by hand (right-click -^> Send to -^> Compressed folder).
  pause
  exit /b 1
)
rmdir /s /q "%STAGE%"

echo.
echo ============================================================================
echo  Done: %OUT%
echo ============================================================================
echo  Send that file back to the development machine.
echo.
echo  It contains the calibration dataset in calibration\samples\ -
echo  one JSONL file per day, one record per encode attempt.
echo ============================================================================
echo.
pause
exit /b 0
"""

README_FIRST = """\
================================================================================
 1KeyTranscoder —— 便携 x265 自动测试包（中文在前，English below）
================================================================================

这个文件夹是**完整可跑的**：
  * 自带生产转码器本体（1kt）
  * 自带 ffmpeg / ffprobe / GPAC
  * 自带标定采集器
  * 不需要 Python，不需要安装任何东西，不需要联网

你只需要做一件事：**把素材丢进 input\\ 文件夹**，然后它会自动转码 +
自动记录标定数据。


================================================================================
 三步开始
================================================================================

  第 1 步  把整个文件夹解压到目标机，比如 D:\\1KT-x265-portable\\

  第 2 步  双击  RUN-TEST.bat
           会起两个独立进程：
             * 1kt-watch.exe                  盯着 input\\ 自动转码
             * x265-calibration-collector.exe  记录标定数据
           窗口保持开着就行。

  第 3 步  把素材（mp4/mov/mkv/mxf/mts...）拷进 input\\ 文件夹
           默认每 60 秒扫一次，发现新文件就转码。
           已经转过的会被跳过，所以重复放、重复启动都安全。


  结果在哪：
    output\\        转码结果
    calibration\\   标定数据（这是这次测试真正要收集的东西）
    logs\\          运行日志


================================================================================
 其它按钮
================================================================================

  CHECK-TEST.bat    只读体检：版本、工具链、input/output 内容、数据状态
                    输出到 check-report.txt

  ANALYZE-DATA.bat  出标定分析报告 -> analysis-report.txt
                    一个月后回传数据之前跑一次

  COLLECT-RESULTS.bat  打包所有要回传的东西 -> results-to-send-back.zip
                       里面有 calibration\\samples\\（数据本体）、分析报告、日志。
                       直接把这个 zip 发回开发机就行。

  停止：关掉 RUN-TEST.bat 那个窗口，或者按 Ctrl+C


================================================================================
 想改行为
================================================================================

  用记事本打开 RUN-TEST.bat，最上面这几行就是要改的：

      set "PRESET=hq"        UHQ / HQ / SMALL / FAST
      set "CHECK=basic"      basic / advanced / full
      set "JOBS=1"           1 / N / auto
      set "INTERVAL=60"      扫描间隔（秒）

  换成别的 preset 或者画质检查等级都只改这里，不用碰别的东西。

  也可以手动调用，参数和原来一致：

      bin\\1kt.exe --input input --output output --encoder x265 ^
                    --preset uhq --check full --jobs auto --headless ^
                    --ffmpeg tools\\ffmpeg.exe --ffprobe tools\\ffprobe.exe ^
                    --gpac-dir tools\\GPAC ^
                    --config config\\x265.json ^
                    --scaling-config config\\x265_scaling.json


================================================================================
 重要：一个月期间不要改的东西
================================================================================

  ✗ 不要改 config\\x265.json
  ✗ 不要改 config\\x265_scaling.json
  ✗ 不要升级 ffmpeg / 换显卡驱动

  标定数据只有在"配置从头到尾固定不变"的前提下才能用来做参数拟合。
  中途改了任何一个，前后数据就不能放在一起分析了。

  采集器不会碰这些东西，它只读不写。转码器也不会自动调参。


================================================================================
 目录说明
================================================================================

  RUN-TEST.bat            启动全部（转码 + 采集）
  CHECK-TEST.bat          只读体检
  ANALYZE-DATA.bat        标定分析报告
  bin\\1kt.exe            生产转码器本体（完整功能，非精简）
  bin\\1kt-watch.exe      自动看文件夹并调用 1kt
  bin\\x265-calibration-collector.exe   标定采集器
  tools\\ffmpeg.exe       FFmpeg
  tools\\ffprobe.exe      FFprobe
  tools\\GPAC\\           MP4Box / GPAC
  tools\\x265-analyze.exe 标定数据分析器
  tools\\x265-verify.exe  自包含验证
  tools\\x265-deploy.exe  部署辅助
  config\\x265.json       生产 profile（固定，别改）
  config\\x265_scaling.json  scaling 规则（固定，别改）
  input\\                 素材丢这里
  output\\                转码结果
  calibration\\           标定数据集（samples\\ 才是要回传的）
  logs\\  work\\          日志和临时文件

  架构：转码器和采集器是**两个平级的独立进程**，
  没有谁启动谁、没有 RPC、没有 stdout 耦合，
  只通过 calibration\\events\\pending\\ 这个文件目录通信。
  采集器失败不影响转码；转码器也不需要采集器在线。


================================================================================
 一个月之后
================================================================================

  1. 关掉 RUN-TEST.bat 窗口
  2. 双击 ANALYZE-DATA.bat 出报告
  3. 把这些东西拷回开发机：
       calibration\\samples\\        <- 数据本体，最要紧
       analysis-report.txt           <- 报告
       output\\ 的日志（可选）

  参数优化是**另一个 session 在开发机上做的**，这个包只负责采集。


================================================================================
 English (short)
================================================================================

This folder runs the real production x265 transcoder on a machine with no
Python and no installed tools. Drop media into input\\, double-click
RUN-TEST.bat, and it transcodes everything automatically while recording
calibration data.

  RUN-TEST.bat      start the watcher + the calibration collector
  CHECK-TEST.bat    read-only health check -> check-report.txt
  ANALYZE-DATA.bat  calibration report -> analysis-report.txt
  input\\           drop media here
  output\\          results
  calibration\\     the calibration dataset (this is what you bring back)

Do NOT edit config\\x265.json or config\\x265_scaling.json during the test:
the dataset is only usable for calibration if the configuration is constant
for the whole period.
"""


def write_workspace_files(pkg_root: Path) -> None:
    """The .bat launchers, guides and the pre-created folder tree.

    Separated from build() so the scripts can be refreshed without re-copying
    the ~500 MB of binaries next to them (--refresh-scripts).
    """
    for sub in WORKSPACE_DIRS:
        (pkg_root / sub).mkdir(parents=True, exist_ok=True)

    write_text(pkg_root / "RUN-TEST.bat", RUN_TEST)
    write_text(pkg_root / "CHECK-TEST.bat", CHECK_TEST)
    write_text(pkg_root / "ANALYZE-DATA.bat", ANALYZE_TEST)
    write_text(pkg_root / "COLLECT-RESULTS.bat", COLLECT_RESULTS)
    write_text(pkg_root / "README-FIRST.txt", README_FIRST)
    write_text(pkg_root / "input" / "PUT-MEDIA-HERE.txt", (
        "Drop media files into this folder.\r\n"
        "\r\n"
        "RUN-TEST.bat scans it every 60 seconds and transcodes whatever it\r\n"
        "finds. Supported: mp4 mov mxf mts m2ts ts mkv avi m4v wmv webm\r\n"
        "mpg mpeg\r\n"
        "\r\n"
        "Files that already have an output are skipped, so you can drop more\r\n"
        "files in later, or restart the launcher, without re-doing work.\r\n"
        "\r\n"
        "Keep this file (or delete it, it is ignored); it exists so the folder\r\n"
        "is not empty and the purpose is obvious.\r\n"
    ))
    write_text(pkg_root / "calibration" / "README_FIRST.txt", (
        "Calibration dataset. The collector owns this folder.\r\n"
        "\r\n"
        "samples\\           THE DATA. One JSONL file per day, one immutable\r\n"
        "                   record per encode attempt (including failures).\r\n"
        "                   This is what you copy back after the test.\r\n"
        "events\\pending\\    where the transcoder drops event files\r\n"
        "events\\archive\\    consumed events, kept for auditing\r\n"
        "events\\failed\\     events that failed validation (+ .error.txt)\r\n"
        "summaries\\         per-day rollups (rebuildable from samples\\)\r\n"
        "errors\\            collector.log and rejected_events.jsonl\r\n"
        "state\\             collector bookkeeping; do not edit\r\n"
    ))


def build(clean: bool, verbose: bool = True) -> Path:
    if not DIST_EXES.is_dir():
        raise SystemExit(
            "[FAIL] %s not found. Build the executables first:\n"
            "       python tools/x265_calibration/release/build_standalone.py"
            % DIST_EXES
        )

    pkg_root = OUT_ROOT / PKG
    if clean and pkg_root.exists():
        shutil.rmtree(pkg_root)
    pkg_root.mkdir(parents=True, exist_ok=True)
    for sub in WORKSPACE_DIRS:
        (pkg_root / sub).mkdir(parents=True, exist_ok=True)

    total = 0
    copied: list[tuple[str, int, str]] = []

    def place(source: Path, dest: str) -> None:
        nonlocal total
        if not source.exists():
            raise SystemExit("[FAIL] missing input: %s" % source)
        target = pkg_root / dest
        size = copy_tree(source, target)
        total += size
        digest = sha256_file(target) if target.is_file() else "(tree)"
        copied.append((dest, size, digest))
        if verbose:
            print("  %-46s %12s" % (dest, human(size)))

    print("=" * 78)
    print("assembling portable x265 test workspace")
    print("=" * 78)
    print("  bin/")
    for source, dest in BIN_FILES:
        place(source, dest)
    print("  tools/")
    for source, dest in TOOL_FILES + TOOL_TREES:
        place(source, dest)
    print("  config/")
    for source, dest in CONFIG_FILES:
        place(source, dest)

    print("  workspace files")
    write_workspace_files(pkg_root)

    # manifest of what was shipped
    lines = [
        "=" * 78,
        "1KeyTranscoder portable x265 test - contents",
        "=" * 78,
        "package        : %s" % PKG,
        "built (UTC)    : %s"
        % datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "project version: 0.7.0",
        "branch         : feature/x265-calibration-collector",
        "",
        "python required  : NO",
        "network required : NO",
        "encoder          : x265 only (software); NVEncC/QSVEncC not bundled",
        "",
        "-" * 78,
        "%-12s  %-64s  %s" % ("size", "sha256", "path"),
        "-" * 78,
    ]
    for dest, size, digest in copied:
        lines.append("%12d  %-64s  %s" % (size, digest, dest))
    lines.append("-" * 78)
    lines.append("binaries + tools + configs: %s" % human(total))
    lines.append("=" * 78)
    write_text(pkg_root / "PACKAGE-CONTENTS.txt", "\n".join(lines) + "\n")

    print()
    print("=" * 78)
    print("[WORKSPACE OK]")
    print("  folder : %s" % pkg_root)
    print("  payload: %s (bin+tools+config)" % human(total))
    return pkg_root


def make_zip(pkg_root: Path, out: Path) -> tuple[Path, bool]:
    """Zip the workspace so it can be carried to the other machine.

    Compression is worth it here: ffmpeg/ffprobe are highly compressible, so
    ~715 MB of payload travels as roughly 250 MB. Returns (path, ok).
    """
    import zipfile

    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    stamp = (2026, 9, 13, 0, 0, 0)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        for path in sorted(pkg_root.rglob("*")):
            rel = path.relative_to(pkg_root).as_posix()
            arcname = f"{PKG}/{rel}"
            if path.is_dir():
                info = zipfile.ZipInfo(arcname + "/", date_time=stamp)
                info.external_attr = (0o40755 << 16) | 0x10
                info.compress_type = zipfile.ZIP_STORED
                zf.writestr(info, b"")
                continue
            info = zipfile.ZipInfo(arcname, date_time=stamp)
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, path.read_bytes(), compresslevel=6)

    # read it back: integrity + the files that make it usable
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        bad = zf.testzip()
    required = (
        f"{PKG}/RUN-TEST.bat",
        f"{PKG}/CHECK-TEST.bat",
        f"{PKG}/ANALYZE-DATA.bat",
        f"{PKG}/README-FIRST.txt",
        f"{PKG}/bin/1kt.exe",
        f"{PKG}/bin/1kt-watch.exe",
        f"{PKG}/bin/x265-calibration-collector.exe",
        f"{PKG}/tools/ffmpeg.exe",
        f"{PKG}/tools/ffprobe.exe",
        f"{PKG}/tools/GPAC/mp4box.exe",
        f"{PKG}/tools/x265-analyze.exe",
        f"{PKG}/config/x265.json",
        f"{PKG}/config/x265_scaling.json",
        f"{PKG}/input/PUT-MEDIA-HERE.txt",
    )
    missing = [r for r in required if r not in names]
    ok = bad is None and not missing
    print()
    print("=" * 78)
    print("zip")
    print("  path     : %s" % out)
    print("  size     : %s (%s uncompressed)"
          % (human(out.stat().st_size),
             human(sum(f.file_size for f in zipfile.ZipFile(out).infolist()))))
    print("  entries  : %d" % len(names))
    print("  integrity: %s" % ("OK" if bad is None else "CORRUPT " + str(bad)))
    if missing:
        print("  MISSING  : %s" % missing)
    print("  vs source: %s"
          % ("matches the workspace" if ok else "PROBLEM - see above"))
    return out, ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="make_portable.py")
    parser.add_argument("--clean", action="store_true",
                        help="wipe the previous workspace first")
    parser.add_argument("--zip", action="store_true",
                        help="also produce the transferable zip")
    parser.add_argument("--only-zip", action="store_true",
                        help="skip assembly; just zip the existing workspace")
    parser.add_argument("--refresh-scripts", action="store_true",
                        help="re-write only the .bat/.txt workspace files "
                             "(no binaries copied), then exit")
    args = parser.parse_args(argv)

    if args.refresh_scripts:
        pkg = OUT_ROOT / PKG
        if not pkg.is_dir():
            raise SystemExit("[FAIL] no workspace at %s" % pkg)
        write_workspace_files(pkg)
        print("[OK] refreshed the launcher scripts and guides in %s" % pkg)
        return 0

    if args.only_zip:
        pkg = OUT_ROOT / PKG
        if not pkg.is_dir():
            raise SystemExit("[FAIL] no workspace at %s" % pkg)
    else:
        pkg = build(clean=args.clean)

    du = sum(p.stat().st_size for p in pkg.rglob("*") if p.is_file())
    print("  total uncompressed: %s" % human(du))

    status = 0
    if args.zip or args.only_zip:
        zip_path = OUT_ROOT / f"{PKG}.zip"
        _path, ok = make_zip(pkg, zip_path)
        if not ok:
            status = 1
        else:
            print()
            print("Copy that zip to the target machine, extract it whole,")
            print("drop media into input\\ and double-click RUN-TEST.bat.")

    print("=" * 78)
    return status


if __name__ == "__main__":
    sys.exit(main())
