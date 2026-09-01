"""运行环境版本收集 (软件 + 驱动) -> 结构化 JSON/CSV.

批量启动时调用: 记录本批次的工具链与驱动版本, 便于日后复现编码行为
(编码器/驱动版本与码控行为强相关, 例如 QSV 驱动 6557/6559 的批量回归史)。
输出 logs_root/env_versions.json + env_versions.csv, 同时返回 dict 供
日志与报告内嵌。
"""

from __future__ import annotations

import csv
import json
import platform
import re
import subprocess
import time
from pathlib import Path
from typing import Any

# ffmpeg -version 的 configuration 行里出现的库 => 能力标记
FFMPEG_LIB_MARKERS = (
    "libx265", "libsvtav1", "libaom", "libvmaf", "librav1e",
    "libdav1d", "libx264",
)


def _run(cmd: list[str], timeout: int = 60) -> str:
    try:
        proc = subprocess.run(
            [str(c) for c in cmd],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
        return proc.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _ffmpeg_versions(ffmpeg: Path) -> dict[str, str]:
    text = _run([ffmpeg, "-version"])
    out: dict[str, str] = {}
    m = re.search(r"ffmpeg version\s+(\S+)", text)
    if m:
        out["ffmpeg"] = m.group(1)
    lib_lines = {
        m.group(1): m.group(2).strip()
        for m in re.finditer(r"^\s*(lib\S+)\s+([\d. ]+/\s*[\d. ]+)", text,
                             re.MULTILINE)
    }
    for name, ver in lib_lines.items():
        out[f"lib:{name}"] = ver.split("/")[0].strip()
    config = ""
    for line in text.splitlines():
        if line.strip().startswith("configuration:"):
            config = line
            break
    for lib in FFMPEG_LIB_MARKERS:
        out[f"feature:{lib}"] = "yes" if f"--enable-{lib}" in config else "no"
    return out


def _svt_lib_version(ffmpeg: Path) -> str:
    """SVT-AV1 库版本需真实编码探测 (gyan 9.0.1 静态构建不列库版本)。"""
    text = _run(
        [ffmpeg, "-hide_banner", "-nostdin", "-y", "-f", "lavfi",
         "-i", "color=size=64x64:rate=1", "-c:v", "libsvtav1",
         "-preset", "12", "-crf", "40", "-frames:v", "1", "-f", "null", "-"],
        timeout=120,
    )
    m = re.search(
        r"SVT \[version\]:\s*SVT-AV1 Encoder Lib\s+(\S+)", text
    )
    return m.group(1) if m else ""


def _rigaya_version(exe: Path) -> str:
    text = _run([exe, "--version"], timeout=30)
    for line in text.splitlines():
        if exe.stem.replace("64", "").lower() in line.lower() or "(" in line:
            stripped = line.strip()
            if stripped:
                return stripped
    m = re.search(r"([A-Za-z]+EncC\S*)\s+\([^)]*\)\s+([\d.]+)", text)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return text.strip().splitlines()[0] if text.strip() else ""


def _file_version(path: Path) -> str:
    ps = (
        f"(Get-Item -LiteralPath '{str(path)}').VersionInfo.ProductVersion"
    )
    text = _run(
        ["powershell", "-NoProfile", "-Command", ps], timeout=60
    )
    return text.strip()


def _gpu_drivers() -> list[dict[str, str]]:
    ps = (
        "Get-CimInstance Win32_VideoController | Select-Object Name,"
        "DriverVersion,DriverDate | ConvertTo-Json -Compress"
    )
    text = _run(
        ["powershell", "-NoProfile", "-Command", ps], timeout=60
    )
    try:
        data = json.loads(text.strip())
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    return [
        {
            "gpu": str(d.get("Name", "")),
            "driver_version": str(d.get("DriverVersion", "")),
            "driver_date": str(d.get("DriverDate", "")),
        }
        for d in data if isinstance(d, dict)
    ]


def collect_versions(
    *,
    ffmpeg: Path,
    ffprobe: Path,
    gpac_version: str = "",
    nvencc: Path | None = None,
    qsvencc: Path | None = None,
    gyroflow: Path | None = None,
    probe_svt: bool = False,
    encoder: str = "",
) -> dict[str, Any]:
    """收集软件/驱动版本; 单工具失败只影响对应字段, 不抛异常。"""
    ffmpeg_info = _ffmpeg_versions(ffmpeg)
    ffprobe_text = _run([ffprobe, "-version"], timeout=30)
    m = re.search(r"ffprobe version\s+(\S+)", ffprobe_text)
    ffprobe_ver = m.group(1) if m else ""

    tools: dict[str, Any] = {
        "python": platform.python_version(),
        "os": platform.platform(),
        "ffmpeg": ffmpeg_info.get("ffmpeg", ""),
        "ffprobe": ffprobe_ver,
        "gpac": gpac_version,
    }
    libs = {k: v for k, v in ffmpeg_info.items() if k.startswith("lib:")}
    if libs:
        tools["ffmpeg_libs"] = libs
    features = {
        k.split(":", 1)[1]: v
        for k, v in ffmpeg_info.items() if k.startswith("feature:")
    }
    if features:
        tools["ffmpeg_features"] = features
    if probe_svt:
        tools["svt_av1_lib"] = _svt_lib_version(ffmpeg)
    if nvencc is not None and nvencc.is_file():
        tools["nvencc"] = _rigaya_version(nvencc)
    if qsvencc is not None and qsvencc.is_file():
        tools["qsvencc"] = _rigaya_version(qsvencc)
    if gyroflow is not None:
        tools["gyroflow"] = _file_version(gyroflow)

    drivers = _gpu_drivers()
    return {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "encoder": encoder or "",
        "tools": tools,
        "gpu_drivers": drivers,
    }


def write_version_report(
    versions: dict[str, Any],
    logs_root: Path,
) -> tuple[Path, Path]:
    """写 logs_root/env_versions.{json,csv}; 返回两者路径。"""
    logs_root.mkdir(parents=True, exist_ok=True)
    json_path = logs_root / "env_versions.json"
    json_path.write_text(
        json.dumps(versions, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    csv_path = logs_root / "env_versions.csv"
    rows: list[dict[str, str]] = []
    tools = versions.get("tools", {})
    for key, value in tools.items():
        if isinstance(value, dict):
            for sub, subval in value.items():
                rows.append({
                    "category": "tool",
                    "name": f"{key}.{sub}",
                    "version": str(subval),
                })
        else:
            rows.append({
                "category": "tool", "name": key, "version": str(value),
            })
    for d in versions.get("gpu_drivers", []):
        rows.append({
            "category": "driver",
            "name": d.get("gpu", ""),
            "version": d.get("driver_version", ""),
        })
    rows.insert(0, {
        "category": "meta",
        "name": "generated",
        "version": versions.get("generated", ""),
    })
    rows.insert(1, {
        "category": "meta",
        "name": "encoder",
        "version": versions.get("encoder", ""),
    })
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["category", "name", "version"])
        writer.writeheader()
        writer.writerows(rows)
    return json_path, csv_path
