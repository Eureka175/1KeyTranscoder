"""Backend invocation for the matrix.

One job: run an encoder binary with an **explicitly chosen decode
reader**, and report what actually happened — including which reader the
tool really constructed, which the command line alone cannot be trusted
to reveal (rigaya tools silently fall back to software; see HD-A03).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import checks
from .sources import BackendSpec, backend_spec

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "work" / "avhw_integration"
RUNS = WORK / "runs"
LOGS = WORK / "logs"


@dataclass
class EncodeResult:
    label: str
    backend: str
    reader_requested: str
    reader_identity: str | None
    reader_matches_request: bool
    rc: int
    elapsed_s: float
    output: str
    output_exists: bool
    output_bytes: int
    log_path: str
    log_text: str
    encoded_frames: int | None
    reader_reported_frames: int | None
    fps: float | None
    cmd: list[str] = field(default_factory=list)
    stderr_tail: str = ""
    timed_out: bool = False

    def to_json(self) -> dict:
        d = dict(self.__dict__)
        # keep the log text out of the result file; it lives on disk
        d.pop("log_text", None)
        return d


def _reader_identity_from_log(text: str) -> str | None:
    return checks.reader_identity_from_log(text)


def build_encode_cmd(
    spec: BackendSpec,
    *,
    source: Path,
    output: Path,
    codec: str = "hevc",
    depth: int = 10,
    chroma: str | None = None,
    qvbr: int | None = 26,
    cqp: int | None = None,
    extra: list[str] | None = None,
    frames: int | None = None,
    seek: float | None = None,
    trim: str | None = None,
    audio_copy: bool = False,
    output_format: str = "mp4",
) -> list[str]:
    """Deterministic argv for one encode attempt.

    Deliberately minimal and identical across readers so that any
    difference in the output is attributable to the decode reader and
    nothing else.
    """
    cmd: list[str] = [str(spec.binary), "-i", str(source)]
    cmd += ["--video-track", "1", spec.reader_arg, "-c", codec]
    if depth:
        cmd += ["--output-depth", str(depth)]
    if chroma and chroma != "4:2:0":
        cmd += ["--output-csp", "yuv422" if chroma == "4:2:2" else chroma]
    if seek is not None:
        cmd += ["--seek", f"{seek}"]
    if trim is not None:
        cmd += ["--trim", trim]
    if frames is not None:
        cmd += ["--frames", str(frames)]
    if cqp is not None:
        cmd += ["--cqp", str(cqp)]
    elif qvbr is not None:
        cmd += ["--qvbr", str(qvbr)]
    if audio_copy:
        cmd += ["--audio-copy"]
    if extra:
        cmd += list(extra)
    cmd += ["-f", output_format, "-o", str(output)]
    return cmd


def run_encode(
    *,
    label: str,
    backend: str,
    reader: str,
    source: Path,
    output: Path,
    role: str = "hardware_decode",
    timeout: int = 7200,
    clean: bool = True,
    **kwargs,
) -> EncodeResult:
    """Run one encode and capture everything needed to judge it."""
    spec = backend_spec(backend, reader, role=role)
    return run_encode_spec(
        label=label, spec=spec, source=source, output=output,
        timeout=timeout, clean=clean, **kwargs,
    )


def run_encode_spec(
    *,
    label: str,
    spec: BackendSpec,
    source: Path,
    output: Path,
    timeout: int = 7200,
    clean: bool = True,
    **kwargs,
) -> EncodeResult:
    RUNS.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    output = Path(output)
    if clean:
        for p in (output,):
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
    output.parent.mkdir(parents=True, exist_ok=True)

    cmd = build_encode_cmd(spec, source=Path(source), output=output, **kwargs)
    log_path = LOGS / f"{label}.log"
    reader_requested = spec.reader_arg.lstrip("-")

    t0 = time.monotonic()
    timed_out = False
    try:
        p = subprocess.run(
            cmd, capture_output=True, timeout=timeout,
        )
        rc = p.returncode
        raw = (p.stdout or b"") + (p.stderr or b"")
    except subprocess.TimeoutExpired as exc:
        rc = -999
        timed_out = True
        raw = (exc.stdout or b"") + (exc.stderr or b"")
    except OSError as exc:
        rc = -998
        raw = f"launch failed: {exc}".encode("utf-8", "replace")
    elapsed = time.monotonic() - t0

    text = raw.decode("utf-8", "replace")
    log_path.write_text(text, encoding="utf-8", errors="replace")

    ident = _reader_identity_from_log(text)
    parsed = checks.parse_tool_log(text)
    out_exists = output.is_file()
    return EncodeResult(
        label=label,
        backend=spec.backend,
        reader_requested=reader_requested,
        reader_identity=ident,
        reader_matches_request=(ident == spec.reader_identity_expected),
        rc=rc,
        elapsed_s=round(elapsed, 3),
        output=str(output),
        output_exists=out_exists,
        output_bytes=output.stat().st_size if out_exists else 0,
        log_path=str(log_path),
        log_text=text,
        encoded_frames=parsed.get("encoded_frames"),
        reader_reported_frames=parsed.get("reader_frames"),
        fps=parsed.get("fps"),
        cmd=cmd,
        stderr_tail=text[-600:],
        timed_out=timed_out,
    )


# ---------------------------------------------------------------------------
# failure injection helpers (categories C-negative and F)
# ---------------------------------------------------------------------------


def broken_binary_copy(dest_dir: Path) -> Path:
    """A-07 / F-03: a copy of the tool that cannot start.

    The exe is real but its runtime DLL directory is empty, so the
    process fails at load time — a *startup* failure, which must be
    classified differently from an integrity failure.
    """
    spec = backend_spec("nvenc", "avhw", role="hardware_decode")
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / spec.binary.name
    shutil.copy2(spec.binary, target)
    return target


def truncate_video(path: Path, keep: int, dest: Path) -> Path:
    """Build a count-changing (tail-truncated) artifact for HD-C14."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(ROOT / "tools" / "ffmpeg.exe"), "-y", "-v", "error",
        "-i", str(path), "-map", "0:v:0", "-c", "copy",
        "-frames:v", str(keep), str(dest),
    ]
    subprocess.run(cmd, capture_output=True, timeout=1800)
    return dest


def reorder_or_duplicate(path: Path, dest: Path, *, frames: int | None = None,
                         rotate: int = 1) -> Path:
    """Build a **count-preserving, order-wrong** artifact (HD-C10).

    The point of this artifact is to be invisible to a counting gate.  To
    make that a fair test, the corruption must isolate *ordering* and
    nothing else: the honest and the rotated artifacts are therefore both
    produced by the same lossless-as-possible encoder with the same
    settings over the same source frames, so the only difference between
    them is the order in which the pictures appear.

    ``shuffleframes`` takes a per-output-frame list of source indices;
    rotating that list by ``rotate`` moves the head of the sequence to the
    tail without changing how many pictures there are.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    ff = str(ROOT / "tools" / "ffmpeg.exe")
    n = frames or _video_packet_count(path)
    if not n:
        raise RuntimeError(f"cannot determine frame count for {path}")
    perm = "|".join(str((i + rotate) % n) for i in range(n))
    cmd = [
        ff, "-y", "-v", "error", "-i", str(path), "-map", "0:v:0",
        "-vf", f"shuffleframes={perm}",
        "-c:v", "libx265", "-preset", "ultrafast", "-crf", "12",
        "-x265-params", "log-level=none", "-pix_fmt", "yuv420p10le",
        str(dest),
    ]
    p = subprocess.run(cmd, capture_output=True, timeout=7200)
    if p.returncode != 0 or not dest.is_file():
        raise RuntimeError(
            f"shuffleframes re-encode failed ({p.returncode}): "
            f"{p.stderr.decode('utf-8', 'replace')[-500:]}"
        )
    return dest


def honest_reencode(path: Path, dest: Path) -> Path:
    """The un-shuffled twin of :func:`reorder_or_duplicate`.

    Same source, same encoder, same settings — so a fingerprint
    difference between the two artifacts can only come from ordering.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    ff = str(ROOT / "tools" / "ffmpeg.exe")
    cmd = [
        ff, "-y", "-v", "error", "-i", str(path), "-map", "0:v:0",
        "-c:v", "libx265", "-preset", "ultrafast", "-crf", "12",
        "-x265-params", "log-level=none", "-pix_fmt", "yuv420p10le",
        str(dest),
    ]
    p = subprocess.run(cmd, capture_output=True, timeout=7200)
    if p.returncode != 0 or not dest.is_file():
        raise RuntimeError(
            f"honest re-encode failed ({p.returncode}): "
            f"{p.stderr.decode('utf-8', 'replace')[-500:]}"
        )
    return dest


def _video_packet_count(path: Path) -> int | None:
    from .checks import output_packet_count

    return output_packet_count(path)


# ---------------------------------------------------------------------------
# long-run telemetry (HD-J05)
# ---------------------------------------------------------------------------


class Telemetry:
    """Sample process RSS / CPU time and GPU utilisation while a job runs.

    The matrix asks for wall clock, CPU time, peak RSS and GPU utilisation
    on the long run, and the project has no psutil dependency, so the
    sampling is done with the platform tools that are already present:
    ``tasklist`` for the encoder's working set and ``nvidia-smi`` for the
    GPU.  Sampling failures are recorded as unavailable rather than
    silently reported as zero — a fabricated telemetry number is worse
    than a missing one.
    """

    def __init__(self, process_name: str, interval: float = 2.0,
                 measure_gpu: bool = True):
        self.process_name = process_name
        self.interval = interval
        self.measure_gpu = measure_gpu
        self.samples: list[dict] = []
        self.errors: list[str] = []
        self._stop = None
        self._thread = None
        self._t0 = 0.0

    # -- internals ---------------------------------------------------------
    def _sample_once(self) -> dict:
        row: dict = {}
        try:
            p = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {self.process_name}",
                 "/FO", "CSV", "/NH"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=20)
            for line in (p.stdout or "").splitlines():
                if self.process_name.lower() not in line.lower():
                    continue
                parts = [c.strip('"') for c in line.split('","')]
                if len(parts) >= 5:
                    rss = parts[4].replace(",", "").replace(" K", "").strip()
                    row["rss_kb"] = int(rss) if rss.isdigit() else None
                    cput = parts[3].replace(",", "").strip()
                    try:
                        h, m, s = (cput.split(":"))
                        row["cpu_s"] = (int(h) * 3600 + int(m) * 60
                                        + float(s))
                    except (ValueError, TypeError):
                        row["cpu_s"] = None
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.errors.append(f"tasklist: {exc}")
        if self.measure_gpu:
            try:
                g = subprocess.run(
                    ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=20)
                first = (g.stdout or "").strip().splitlines()
                if first:
                    util, mem = [c.strip() for c in first[0].split(",")]
                    row["gpu_util_pct"] = float(util)
                    row["vram_mib"] = float(mem)
            except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
                self.errors.append(f"nvidia-smi: {exc}")
        row["t"] = round(time.monotonic() - self._t0, 1)
        return row

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.samples.append(self._sample_once())
            except Exception as exc:  # noqa: BLE001
                self.errors.append(f"sample: {exc}")

    # -- context manager ---------------------------------------------------
    def __enter__(self):
        import threading

        self._stop = threading.Event()
        self._t0 = time.monotonic()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        if self._stop is not None:
            self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        return False

    def summary(self) -> dict:
        rss = [s["rss_kb"] for s in self.samples if s.get("rss_kb")]
        cpu = [s["cpu_s"] for s in self.samples if s.get("cpu_s") is not None]
        gpu = [s["gpu_util_pct"] for s in self.samples
               if s.get("gpu_util_pct") is not None]
        vram = [s["vram_mib"] for s in self.samples if s.get("vram_mib")]
        out = {
            "samples": len(self.samples),
            "peak_rss_mib": round(max(rss) / 1024, 1) if rss else None,
            "cpu_time_s": round(max(cpu) - min(cpu), 2) if len(cpu) > 1 else None,
            "gpu_util_mean_pct": round(sum(gpu) / len(gpu), 1) if gpu else None,
            "gpu_util_peak_pct": max(gpu) if gpu else None,
            "vram_peak_mib": max(vram) if vram else None,
            "errors": sorted(set(self.errors)),
            "note": ("sampled with tasklist + nvidia-smi; null means the "
                     "platform tool did not report it, not zero"),
        }
        return out
