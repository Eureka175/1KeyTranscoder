"""Shared machinery for the rigaya hardware encoder backends.

NVEncC and QSVEncC share one CLI family (same progress-line format,
same failure modes). This module owns:

- run_hw_tool(): process execution + progress parsing (rigaya format)
- classify_failure(): narrow stderr-pattern failure taxonomy
  (reader / format / environment) driving the fallback state machine
- ask_fallback_new_console(): interactive 60s decision prompt in a NEW
  console window (main console keeps only encode progress); the answer
  comes back through a decision file, no IPC
- plan_initial_format(): capability-driven initial format selection
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from .caps import run_tool, supports


def known_flags(tool: Path) -> set[str]:
    """Long flag names advertised in the tool's --help output.

    Handles the rigaya '(no-)' boolean convention (--(no-)aq advertises
    both --aq and --no-aq)."""
    out = run_tool(tool, "--help", timeout=30)
    names = set(re.findall(r"--\(no-\)([a-z][a-z0-9-]*)", out))
    names |= set(re.findall(r"--([a-z][a-z0-9-]*)", out))
    return names


def build_flag_args(
    profile: dict,
    param_map: dict,
    known: set[str],
) -> tuple[list[str], list[str]]:
    """Serialize profile JSON keys to CLI flags.

    param_map: json_key -> (candidates, kind), where candidates is a
    space-separated flag list ("-b --bframes") and the first candidate
    advertised in --help wins. kind: "flag" (bool presence), "value",
    "list" (join with ':'). Flags absent from the tool's help are
    skipped and reported. Returns (argv_fragment, skipped_keys).
    """
    args: list[str] = []
    skipped: list[str] = []
    for key, (candidates, kind) in param_map.items():
        if key not in profile or profile[key] is None:
            continue
        value = profile[key]

        def pick() -> str | None:
            for cand in candidates.split():
                name = cand.lstrip("-")
                if name in known:
                    return cand
            return None

        if kind == "flag":
            if isinstance(value, str):
                if value.strip().lower() in ("off", "0", "false", "no", ""):
                    continue
            elif not value:
                continue
            flag = pick()
            if flag is None:
                skipped.append(key)
                continue
            args.append(flag)
        elif kind == "list":
            flag = pick()
            if flag is None:
                skipped.append(key)
                continue
            if isinstance(value, (list, tuple)):
                args += [flag, ":".join(str(v) for v in value)]
            else:
                args += [flag, str(value)]
        else:  # "value"
            if isinstance(value, bool):
                if value:
                    flag = pick()
                    if flag is None:
                        skipped.append(key)
                        continue
                    args.append(flag)
                continue
            if isinstance(value, str) and value.strip().upper() == "AUTO":
                continue
            flag = pick()
            if flag is None:
                skipped.append(key)
                continue
            args += [flag, str(value)]
    return args, skipped

# rigaya progress: "[12.3%] 1234 frames: 56.7 fps, 12345 kbps, ..."
_PROGRESS_RE = re.compile(
    r"(\d+(?:\.\d+)?)%\]\s*(\d+) frames:\s*([\d.]+) fps"
)
_FINAL_RE = re.compile(
    r"encoded\s+(\d+)\s+frames,\s*([\d.]+)\s+fps"
)

_READER_PATTERNS = (
    "failed to open",
    "could not find",
    "invalid data",
    "moov atom",
    "no such file",
    "avcodec",
    "reader",
    "demux",
    "could not read",
    "unable to find a suitable output",
)
_ENV_PATTERNS = (
    "no space left",
    "access is denied",
    "permission",
    "disk",
    "not enough space",
    "error 5",
)


def run_hw_tool(
    cmd: list[str],
    raw_log_path: Path,
    total_frames: int,
    label: str = "hw",
    progress: bool = True,
) -> tuple[int | None, float]:
    """Run one hardware-encoder command with live progress.

    progress=False suppresses the per-session console progress line
    (used in parallel worker mode, where interleaved \\r lines would
    garble the work window; the dashboard shows status instead).
    Returns (return_code, elapsed_sec); return_code is None when the
    run was interrupted or failed to execute (callers treat None like
    a failure and clean up their own outputs).
    """
    started = time.monotonic()
    last_console = 0.0
    last_frame = 0
    last_fps = 0.0
    console_dead = False

    raw_log_path.parent.mkdir(parents=True, exist_ok=True)

    proc: subprocess.Popen[str] | None = None
    try:
        with raw_log_path.open(
            "w", encoding="utf-8", errors="replace"
        ) as raw_log:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            assert proc.stdout is not None
            for raw_line in proc.stdout:
                line = raw_line.rstrip("\r\n")
                raw_log.write(line + "\n")
                raw_log.flush()

                m = _PROGRESS_RE.search(line)
                if not m:
                    fm = _FINAL_RE.search(line)
                    if fm:
                        last_frame = int(fm.group(1))
                        last_fps = float(fm.group(2))
                    continue
                last_frame = int(m.group(2))
                last_fps = float(m.group(3))
                if not progress:
                    continue
                now = time.monotonic()
                if now - last_console >= 1.0:
                    total = str(total_frames) if total_frames > 0 else "?"
                    try:
                        print(
                            f"\r[{label.upper()}] {last_frame} frames / "
                            f"{total} frames total | "
                            f"{time.monotonic() - started:.1f} sec "
                            f"elapsed | {last_fps:.1f} fps          ",
                            end="",
                            flush=True,
                        )
                    except OSError:
                        console_dead = True
                    last_console = now
            return_code = proc.wait()
        if not console_dead:
            try:
                print()
            except OSError:
                pass
        return return_code, time.monotonic() - started
    except KeyboardInterrupt:
        print()
        if proc is not None:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
        return None, time.monotonic() - started
    except Exception:
        print()
        return None, time.monotonic() - started


def read_log_tail(raw_log_path: Path, lines: int = 40) -> str:
    try:
        text = raw_log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def classify_failure(stderr_tail: str) -> str:
    """Failure taxonomy: 'reader' (container unreadable -> strip
    fallback), 'environment' (disk/permission -> immediate fatal),
    'format' (anything else -> downgrade ladder)."""
    low = stderr_tail.lower()
    for pat in _ENV_PATTERNS:
        if pat in low:
            return "environment"
    for pat in _READER_PATTERNS:
        if pat in low:
            return "reader"
    return "format"


def ask_fallback_new_console(
    work_dir: Path,
    raw_log_path: Path,
    timeout_sec: int = 60,
) -> bool:
    """Interactive fallback decision in a NEW console window.

    The main console stays clean (only encode progress). A helper
    console prints the error log tail and asks: N aborts the fallback,
    Y / 60s silence proceeds. The answer travels back through a
    decision file. Headless sessions (console creation fails) fall
    through to 'proceed' (auto-fallback)."""
    decision = work_dir / "fallback_decision.txt"
    try:
        if decision.exists():
            decision.unlink()
    except OSError:
        pass

    batch = work_dir / "fallback_prompt.bat"
    log_text = read_log_tail(raw_log_path, 40).replace("%", "%%")
    script = (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "title 1KeyTranscoder - encode failure\r\n"
        "echo ============================================\r\n"
        "echo  ENCODE FAILURE - error log (last 40 lines)\r\n"
        "echo ============================================\r\n"
        f"echo {log_text}\r\n"
        "echo.\r\n"
        "echo Fallback: downgrade format ladder will start.\r\n"
        f"choice /C YN /T {timeout_sec} /D Y /M \"Press N within "
        f"{timeout_sec}s to ABORT, or Y/timeout to PROCEED with "
        "downgrade\"\r\n"
        f'if errorlevel 2 (echo N> "{decision}") else '
        f'(echo Y> "{decision}")\r\n'
    )
    try:
        batch.write_text(script, encoding="utf-8")
    except OSError:
        return True

    try:
        subprocess.Popen(
            ["cmd", "/c", str(batch)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    except OSError:
        # headless session: no console available, proceed automatically
        return True

    deadline = time.monotonic() + timeout_sec + 10
    while time.monotonic() < deadline:
        try:
            if decision.is_file():
                answer = decision.read_text(
                    encoding="utf-8", errors="replace"
                ).strip().upper()
                return not answer.startswith("N")
        except OSError:
            return True
        time.sleep(1.0)
    return True  # no answer within the window: proceed with fallback


def plan_initial_format(
    caps,
    backend_kind: str,
    chroma: str,
    depth: int,
) -> tuple[tuple[str, int], bool]:
    """Capability-driven initial format.

    Returns ((chroma, depth), needs_downgrade). QSV policy: 4:2:2
    sources always plan 4:2:0 conversion (direct 4:2:2 encode on Arc
    is the slow path ~1.0x vs 2.0x); NVENC keeps 4:2:2 when capable."""
    if chroma == "4:2:2":
        if backend_kind == "qsvencc":
            return ("4:2:0", max(depth, 10)), True
        if supports(caps, "4:2:2", depth):
            return (chroma, depth), False
        return ("4:2:0", max(depth, 10)), True
    if depth > 8:
        if supports(caps, chroma, depth):
            return (chroma, depth), False
        return ("4:2:0", 10), True
    return (chroma, depth), False
