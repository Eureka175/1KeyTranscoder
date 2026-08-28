"""Hardware encoder capability probing (NVEncC / QSVEncC) and caching.

Queried fresh at every run start (per final design) and saved under
<output>/.1ktwork/caps/<backend>_caps.json with the raw tool output
alongside. Parsing is deliberately conservative: on any parse failure
the caller falls back to the minimal capability (8-bit 4:2:0) and logs
a prominent warning.

The encode-side format matrix is parsed from the text `--check-features`
output (neither tool exposes a JSON export — `--check-features-json`
does not exist). Decode-side capabilities are NOT modeled: hardware
encode paths always decode with `--avsw` (software), so decode
capability is irrelevant by design.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CodecCaps:
    """Encoder-side format support for one codec."""

    bit10: bool = False       # 10-bit depth encode
    csp_422: bool = False     # 4:2:2 encode (8-bit)
    csp_444: bool = False     # 4:4:4 encode
    bit10_422: bool = False   # 4:2:2 10-bit encode


@dataclass
class BackendCaps:
    tool: str = ""
    tool_version: str = ""
    device: str = ""
    driver: str = ""
    codecs: dict[str, CodecCaps] = field(default_factory=dict)
    raw: str = ""

    def to_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def save(self, caps_dir: Path) -> tuple[Path, Path]:
        """Write caps JSON + raw tool output; return (json_path, raw_path)."""
        key = re.sub(r"[^A-Za-z0-9_.-]+", "_", self.tool or "backend")
        json_path = caps_dir / f"{key}_caps.json"
        raw_path = caps_dir / f"{key}_caps_raw.txt"
        self.to_json(json_path)
        raw_path.write_text(self.raw or "", encoding="utf-8", errors="replace")
        return json_path, raw_path


def run_tool(tool: Path, *args: str, timeout: int = 120) -> str:
    proc = subprocess.run(
        [str(tool), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return proc.stdout or ""


def tool_version(tool: Path) -> str:
    out = run_tool(tool, "--version", timeout=30)
    first = out.strip().splitlines()
    return first[0] if first else ""


def _parse_nvenc(text: str) -> dict[str, CodecCaps]:
    """NVEncC --check-features: per-codec input-format list lines like
    'H.265/HEVC: nv12, yv12, yv12(10bit), yuv444(10bit), yuv422(10bit), ...'"""
    caps: dict[str, CodecCaps] = {}
    for m in re.finditer(
        r"H\.(264/AVC|265/HEVC)\s*:\s*([^\r\n]+)", text
    ):
        codec = "hevc" if "265" in m.group(1) else "h264"
        fmt = m.group(2)
        c = CodecCaps()
        c.bit10 = "10bit" in fmt
        c.csp_422 = bool(re.search(r"\byuv422\b", fmt))
        c.csp_444 = "yuv444" in fmt
        c.bit10_422 = "yuv422(10bit)" in fmt
        caps[codec] = c
    return caps


def _parse_qsv(text: str) -> dict[str, CodecCaps]:
    """QSVEncC --check-features: per-codec '10bit depth' row (o/x per
    RC mode). 4:2:2/4:4:4 columns are not parsed — the QSV backend
    policy always plans 4:2:0 for 4:2:2 sources (direct 4:2:2 encode on
    Arc is a slow path, ~1.0x vs 2.0x for the 4:2:0 conversion)."""
    caps: dict[str, CodecCaps] = {}
    for m in re.finditer(
        r"Codec:\s*H\.(264/AVC|265/HEVC)\s*\w*\s*\n(.*?)(?=Codec:|$)",
        text,
        re.S,
    ):
        codec = "hevc" if "265" in m.group(1) else "h264"
        section = m.group(2)
        row = re.search(r"10bit depth\s+([ox ]+)", section)
        caps[codec] = CodecCaps(
            bit10=bool(row and "o" in row.group(1)),
        )
    return caps


def _env_info(text: str) -> tuple[str, str]:
    device = ""
    driver = ""
    # NVEncC: "#0: NVIDIA GeForce RTX 5070 Laptop GPU (4608 cores,
    # 1545 MHz)[PCIe5x16][596.36]"
    m = re.search(
        r"#\d+:\s*([^\r\n]+?)\s*\[([^\]]*)\]\s*$", text, re.M
    )
    if m:
        device = m.group(1).split(" (")[0].strip()
        driver = m.group(2)
        return device, driver
    # QSVEncC: "GPU: Intel Arc 140T GPU (16GB) (128EU) ... (32.0.101.8974)"
    m = re.search(r"GPU:\s*([^\r\n(]+(?:\([^)]*\))?)", text)
    if m:
        device = m.group(1).strip()
    m = re.search(r"\((\d+\.\d+\.\d+\.\d+)\)", text)
    if m:
        driver = m.group(1)
    return device, driver


def probe_backend(tool: Path, kind: str) -> BackendCaps | None:
    """Query a rigaya tool's encode capabilities.

    kind: "nvencc" | "qsvencc". Returns None when the probe command
    fails entirely (caller falls back to conservative capabilities).
    """
    raw = run_tool(tool, "--check-features")
    if not raw or "Codec:" not in raw:
        return None
    if kind == "nvencc":
        codecs = _parse_nvenc(raw)
    elif kind == "qsvencc":
        codecs = _parse_qsv(raw)
    else:
        raise ValueError(f"unknown backend kind: {kind}")
    device, driver = _env_info(raw)
    caps = BackendCaps(
        tool=tool.name,
        tool_version=tool_version(tool),
        device=device,
        driver=driver,
        codecs=codecs,
        raw=raw,
    )
    if not codecs.get("hevc"):
        # HEVC is the only production codec; missing section means the
        # parse failed for the codec we care about.
        caps.codecs["hevc"] = CodecCaps()
    return caps


def supports(caps: BackendCaps | None, chroma: str, depth: int) -> bool:
    """Can the backend encode (chroma, depth) HEVC?"""
    if caps is None:
        return False
    hevc = caps.codecs.get("hevc")
    if hevc is None:
        return False
    if chroma == "4:2:2":
        return hevc.bit10_422 if depth > 8 else hevc.csp_422
    if chroma == "4:4:4":
        return hevc.csp_444
    # 4:2:0 / unknown
    if depth > 8:
        return hevc.bit10
    return True


def downgrade_ladder(chroma: str, depth: int) -> list[tuple[str, int]]:
    """Format ladder below the source: source format -> 10-bit 4:2:0 ->
    8-bit 4:2:0, deduplicated, skipping already-tried rungs."""
    rungs: list[tuple[str, int]] = [(chroma, depth)]
    for c, d in (("4:2:0", 10), ("4:2:0", 8)):
        if (c, d) != (chroma, depth):
            rungs.append((c, d))
    return rungs


CONSERVATIVE_CAPS = BackendCaps(tool="unknown", codecs={"hevc": CodecCaps()})
