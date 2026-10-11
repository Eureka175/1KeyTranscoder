"""P4.11 — deterministic acquisition signal generator.

Produces the file the operator plays into both paths. Everything about its time
axis is defined here, once, and recorded in the manifest, so the recording and the
analysis cannot disagree about where anything is.

Design decisions, each with the measurement or production fact behind it:

  * **48 kHz, mono, 24-bit PCM WAV.** 48 kHz is the production path's supported
    rate; 24-bit is what the target camera records, so the reference file is not
    the weak link; mono because each physical input receives one channel.
  * **band-limited 200 Hz - 8 kHz noise as the measurement body.** P4.9B measured
    that material outside this band yields ZERO measurable frames, so a signal whose
    band did not match the estimator's would fail for an irrelevant reason.
  * **a 20 ms wideband marker at each end**, chosen on measurement
    (`marker-design.json`): every tested duration recovered the injected
    differential offset to within a sample, and 20 ms has a 10.3x margin over the
    Gaussian noise null while occupying the least audio. The protocol's 0.5 s
    figure is corrected to this.
  * **interleaved log sweeps** as interior reference events, so a delay that
    changes inside the take is detectable between the two ends rather than only at
    them. Each sweep is preceded by its own 20 ms marker.
  * **deterministic**: one seed drives every random draw, and the same
    configuration must reproduce the file sample for sample. `--verify` re-derives
    and compares.

Usage:
    python tests/audio_sync_acquisition/generate_signal.py --out DIR
    python tests/audio_sync_acquisition/generate_signal.py --out DIR --verify
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[2]
FS = 48_000
GENERATOR_VERSION = "p4.11-signal-1"

#: The one seed. Every random draw in this file derives from it, so changing it
#: changes the signal and therefore its hash — which is the point.
DEFAULT_SEED = 20261012

#: 60 s: >= 2 production plan windows (30 s each) and >= 500 measurement frames at
#: the 200 ms / 100 ms production geometry. A 30 s take leaves a single window,
#: which is the condition under which P4.9A applied zero corrections on 136 clips.
TAKE_SECONDS = 60.0

BAND_HZ = (200.0, 8000.0)
MARKER_MS = 20.0
MARKER_LEAD_S = 0.250
MARKER_GAP_S = 0.080
NOISE_LEAD_S = 0.040
SWEEP_MS = 150.0
SWEEP_INTERVAL_S = 10.0
TAIL_S = 0.400
PEAK_TARGET = 0.85          # ~ -1.4 dBFS, comfortably short of clipping

SECTION_NAMES = ("marker_start", "noise", "marker_end")


def seconds_to_samples(s: float) -> int:
    return int(round(s * FS))


def marker(seed: int, ms: float = MARKER_MS) -> np.ndarray:
    """Raised-cosine-enveloped band-limited noise: no edge click, no DC step."""
    n = seconds_to_samples(ms / 1000.0)
    rng = np.random.default_rng(seed)
    t = np.arange(n) / n
    env = 0.5 - 0.5 * np.cos(2.0 * np.pi * t)
    x = env * rng.standard_normal(n)
    return band_limit(x)


def band_limit(x: np.ndarray, band: tuple[float, float] = BAND_HZ) -> np.ndarray:
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(x.shape[0], d=1.0 / FS)
    spec[(f < band[0]) | (f > band[1])] = 0.0
    return np.fft.irfft(spec, x.shape[0])


def noise_body(seconds: float, seed: int) -> np.ndarray:
    n = seconds_to_samples(seconds)
    rng = np.random.default_rng(seed)
    return band_limit(rng.standard_normal(n))


def log_sweep(ms: float, seed: int) -> np.ndarray:
    """A log sweep across the estimator's band, windowed at both ends."""
    n = seconds_to_samples(ms / 1000.0)
    t = np.arange(n) / FS
    f0, f1 = BAND_HZ
    dur = n / FS
    k = math.log(f1 / f0)
    phase = 2.0 * np.pi * f0 * dur / k * (np.exp(t / dur * k) - 1.0)
    x = np.sin(phase)
    win = np.hanning(n)
    del seed              # a sweep is deterministic without a draw; kept in the
    return band_limit(x * win)   # signature so the call site is uniform


@dataclass
class SignalSpec:
    seed: int = DEFAULT_SEED
    take_seconds: float = TAKE_SECONDS
    fs: int = FS
    band_hz: tuple[float, float] = BAND_HZ
    marker_ms: float = MARKER_MS
    marker_lead_s: float = MARKER_LEAD_S
    marker_gap_s: float = MARKER_GAP_S
    noise_lead_s: float = NOISE_LEAD_S
    sweep_ms: float = SWEEP_MS
    sweep_interval_s: float = SWEEP_INTERVAL_S
    tail_s: float = TAIL_S
    peak_target: float = PEAK_TARGET

    def as_dict(self) -> dict[str, Any]:
        return {
            "generator": "generate_signal.py",
            "generator_version": GENERATOR_VERSION,
            "seed": self.seed,
            "sample_rate": self.fs,
            "sample_format": "s24le",
            "channel_count": 1,
            "take_seconds": self.take_seconds,
            "band_hz": list(self.band_hz),
            "marker_ms": self.marker_ms,
            "marker_lead_s": self.marker_lead_s,
            "marker_gap_s": self.marker_gap_s,
            "noise_lead_s": self.noise_lead_s,
            "sweep_ms": self.sweep_ms,
            "sweep_interval_s": self.sweep_interval_s,
            "tail_s": self.tail_s,
            "peak_target": self.peak_target,
        }


def build(spec: SignalSpec) -> tuple[np.ndarray, dict[str, Any]]:
    """Return (float samples in [-1,1], layout) for one configuration."""
    n_total = seconds_to_samples(spec.take_seconds)
    out = np.zeros(n_total, dtype=np.float64)
    layout: dict[str, Any] = {}

    mk = marker(spec.seed)
    mb = mk.shape[0]
    mk_start = seconds_to_samples(spec.marker_lead_s)
    out[mk_start:mk_start + mb] = mk
    layout["marker_start"] = {"start_sample": mk_start,
                              "end_sample": mk_start + mb}

    noise_start = mk_start + mb + seconds_to_samples(spec.marker_gap_s)
    mk_end = n_total - seconds_to_samples(spec.tail_s) - mb
    noise_end = mk_end - seconds_to_samples(spec.marker_gap_s)
    if noise_end <= noise_start:
        raise ValueError("take is too short for the requested layout")
    out[noise_start:noise_end] = noise_body(
        (noise_end - noise_start) / FS, spec.seed + 1)
    layout["noise"] = {"start_sample": noise_start, "end_sample": noise_end}

    out[mk_end:mk_end + mb] = mk
    layout["marker_end"] = {"start_sample": mk_end, "end_sample": mk_end + mb}

    # interior sweeps every `sweep_interval_s`, each preceded by its own marker,
    # so a delay that moves mid-take is visible at more than the two ends
    interior: list[dict[str, Any]] = []
    sw = log_sweep(spec.sweep_ms, spec.seed + 2)
    sb = sw.shape[0]
    step = seconds_to_samples(spec.sweep_interval_s)
    pos = noise_start + step
    idx = 0
    while pos + mb + sb < noise_end:
        out[pos:pos + mb] = mk * spec.peak_target
        out[pos + mb:pos + mb + sb] = sw * spec.peak_target
        interior.append({
            "index": idx,
            "marker_start_sample": pos,
            "marker_end_sample": pos + mb,
            "sweep_start_sample": pos + mb,
            "sweep_end_sample": pos + mb + sb,
        })
        pos += step
        idx += 1
    layout["interior_reference_events"] = interior

    # normalise to the target peak. Scaling AFTER layout is fixed keeps the
    # sample positions exactly as declared.
    peak = float(np.max(np.abs(out)))
    if peak > 0:
        out = out * (spec.peak_target / peak)

    stats = {
        "sample_count": int(out.shape[0]),
        "peak_abs": float(np.max(np.abs(out))),
        "rms_dbfs": float(20.0 * math.log10(
            max(float(np.sqrt(np.mean(out ** 2))), 1e-12))),
        "peak_dbfs": float(20.0 * math.log10(max(float(np.max(np.abs(out))), 1e-12))),
        "clipped_samples": int(np.count_nonzero(np.abs(out) >= 1.0)),
        "nonzero_samples": int(np.count_nonzero(out)),
        "silence_fraction": float(np.mean(out == 0.0)),
    }
    return out, {"layout": layout, "amplitude": stats, "spec": spec.as_dict()}


def to_s24le(x: np.ndarray) -> bytes:
    """Quantise to signed 24-bit little-endian, saturating rather than wrapping."""
    y = np.clip(x, -1.0, 1.0)
    q = np.round(y * (2 ** 23 - 1)).astype(np.int64)
    q = np.clip(q, -(2 ** 23), 2 ** 23 - 1)
    u = (q & 0xFFFFFF).astype(np.uint32)
    b = np.empty((u.shape[0], 3), dtype=np.uint8)
    b[:, 0] = (u & 0xFF).astype(np.uint8)
    b[:, 1] = ((u >> 8) & 0xFF).astype(np.uint8)
    b[:, 2] = ((u >> 16) & 0xFF).astype(np.uint8)
    return b.tobytes()


def write_wav(path: Path, x: np.ndarray, fs: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(3)
        w.setframerate(fs)
        w.writeframes(to_s24le(x))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest().upper()


def sha256_pcm(x: np.ndarray) -> str:
    return hashlib.sha256(to_s24le(x)).hexdigest().upper()


def generate(out_dir: Path, spec: SignalSpec) -> dict[str, Any]:
    x, meta = build(spec)
    wav = out_dir / "signal.wav"
    write_wav(wav, x, spec.fs)
    manifest = {
        "signal_id": f"p4.11-{spec.seed}-{int(spec.take_seconds)}s",
        **meta,
        "file": {
            "name": "signal.wav",
            "bytes": wav.stat().st_size,
            "sha256": sha256_file(wav),
            "pcm_s24le_sha256": sha256_pcm(x),
        },
        "duration_seconds": x.shape[0] / spec.fs,
        "sections": ["marker_start", "noise", "marker_end"],
        "marker_layout": meta["layout"]["marker_start"],
        "marker_end_layout": meta["layout"]["marker_end"],
        "expected_playback": (
            "play this file into BOTH the reference input and the wireless "
            "transmitter from the same output; do not fade, compress or normalise"),
    }
    (out_dir / "signal-manifest.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    return manifest


def verify(out_dir: Path, spec: SignalSpec) -> tuple[bool, list[str]]:
    """Re-derive and compare: same configuration must give the same samples."""
    problems: list[str] = []
    manifest_path = out_dir / "signal-manifest.json"
    if not manifest_path.is_file():
        return False, ["signal-manifest.json missing"]
    recorded = json.loads(manifest_path.read_text(encoding="utf-8"))
    x, _meta = build(spec)
    if recorded["file"]["sha256"] != sha256_file(out_dir / "signal.wav"):
        problems.append("signal.wav hash differs from the manifest")
    if recorded["file"]["pcm_s24le_sha256"] != sha256_pcm(x):
        problems.append("re-derived PCM differs from the recorded PCM hash")
    if recorded["amplitude"]["clipped_samples"] != 0:
        problems.append("signal is clipped")
    # second derivation must be identical to the first
    x2, _ = build(spec)
    if not np.array_equal(to_s24le(x), to_s24le(x2)):
        problems.append("two derivations of the same configuration differ")
    return (not problems), problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path,
                    default=REPO / "work" / "p4.11-acquisition" / "generated-signal")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--take-seconds", type=float, default=TAKE_SECONDS)
    ap.add_argument("--verify", action="store_true",
                    help="re-derive and compare instead of writing")
    args = ap.parse_args(argv)

    spec = SignalSpec(seed=args.seed, take_seconds=args.take_seconds)
    if args.verify:
        ok, problems = verify(args.out, spec)
        for p in problems:
            print(f"  PROBLEM {p}")
        print(f"verify: {'PASS' if ok else 'FAIL'}  ({args.out})")
        return 0 if ok else 1

    m = generate(args.out, spec)
    print(f"signal        : {args.out / 'signal.wav'}")
    print(f"bytes         : {m['file']['bytes']}")
    print(f"samples       : {m['amplitude']['sample_count']} "
          f"({m['duration_seconds']:.3f} s @ {m['spec']['sample_rate']} Hz)")
    print(f"peak          : {m['amplitude']['peak_abs']:.4f} "
          f"({m['amplitude']['peak_dbfs']:.2f} dBFS), "
          f"clipped {m['amplitude']['clipped_samples']}")
    print(f"rms           : {m['amplitude']['rms_dbfs']:.2f} dBFS")
    print(f"marker start  : {m['marker_layout']}")
    print(f"marker end    : {m['marker_end_layout']}")
    print(f"interior refs : {len(m['layout']['interior_reference_events'])}")
    print(f"sha256        : {m['file']['sha256']}")
    print(f"manifest      : {args.out / 'signal-manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
