"""P4.11 — build deterministic take fixtures for the preflight and end-to-end tests.

Tracked alongside the tooling it tests, because `test_e2e.py` cannot run without
the fixtures and a test whose inputs cannot be rebuilt on a clean clone is not a
test. It writes ~1.4 GB of generated media into `work/`, which stays gitignored —
the generator is tracked, its output is not.

These fixtures prove the TOOLCHAIN works. They are not wireless measurements and
cannot substitute for a real recording: every "delay" in them is injected
digitally, so a PASS here says the plumbing is correct, not that any link has been
characterised.

Each fixture is a take directory shaped exactly like the operator's delivery:

    <root>/<session>/<take_id>.MP4
    <root>/<session>/<take_id>.txt

Fixture kinds, each targeting one branch of the preflight:

  positive     zero / positive / negative / large delay, with a valid note
  calibration  a CABLE_CALIBRATION take whose two electrical paths are offset by a
               known NON-ZERO bias, so the bias subtraction is exercised rather
               than assumed to be zero
  negative     one defect each: missing note, wrong signal SHA, a mapping that
               restates stream names, a mapping that names only one stream, a
               mapping left unfilled, wrong sample rate, wrong PCM format, corrupt
               marker, clipped signal, AGC enabled, take too short, unknown identity
  timevarying  start/end marker disagreement, a mid-take delay step, and a dropout
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests" / "audio_sync_acquisition"))
sys.path.insert(0, str(REPO))

from generate_signal import (                               # noqa: E402
    FS, SignalSpec, build, write_wav, sha256_file,
)

WORK = REPO / "work" / "p4.11-acquisition"
FIX = WORK / "fixtures"
SIG_DIR = WORK / "generated-signal"
FFMPEG = REPO / "tools" / "ffmpeg.exe"
SIGNAL_WAV = SIG_DIR / "signal.wav"
SIGNAL_MANIFEST = SIG_DIR / "signal-manifest.json"

#: The valid mapping used by every fixture that is supposed to PASS. It names
#: PHYSICAL inputs, never a restatement of the stream number.
VALID_MAPPING = ("0=CH1_input1_direct_cable;1=CH2_input2_spare;"
                 "2=CH3_input3_wireless_rx;3=CH4_input4_spare")


def ensure_signal() -> dict[str, Any]:
    """Generate the signal if it is absent, so the fixtures rebuild from source."""
    if not SIGNAL_WAV.is_file() or not SIGNAL_MANIFEST.is_file():
        SIG_DIR.mkdir(parents=True, exist_ok=True)
        print(f"generating signal into {SIG_DIR}")
        from generate_signal import generate
        generate(SIG_DIR, SignalSpec())
    return json.loads(SIGNAL_MANIFEST.read_text(encoding="utf-8"))


def load_signal() -> tuple[np.ndarray, dict[str, Any]]:
    import wave
    meta = ensure_signal()
    with wave.open(str(SIGNAL_WAV), "rb") as w:
        raw = w.readframes(w.getnframes())
    b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
    q = (b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16))
    q = np.where(q >= (1 << 23), q - (1 << 24), q)
    x = q.astype(np.float64) / float(2 ** 23 - 1)
    return x, meta


def delayed(x: np.ndarray, d: int) -> np.ndarray:
    """y[k] = x[k - d]; d > 0 means THIS channel's copy arrives LATE."""
    n = x.shape[0]
    y = np.zeros(n, dtype=np.float64)
    if d >= 0:
        y[d:] = x[: n - d]
    else:
        y[: n + d] = x[-d:]
    return y


def piecewise(x: np.ndarray, d_a: int, d_b: int, at: int) -> np.ndarray:
    """Late by `d_a` before `at` and by `d_b` from `at` on."""
    y = delayed(x, d_a)
    if d_b == d_a:
        return y
    tail = delayed(x, d_b)
    y[at:] = tail[at:]
    return y


def write_take_wavs(dirpath: Path, chans: dict[int, np.ndarray], *,
                    fs: int = FS, clip: bool = False) -> list[Path]:
    dirpath.mkdir(parents=True, exist_ok=True)
    out = []
    for pos, arr in sorted(chans.items()):
        a = arr
        if clip:
            a = np.clip(a * 4.0, -1.0, 1.0)
        p = dirpath / f"ch{pos}.wav"
        write_wav(p, a, fs)
        out.append(p)
    return out


def mux(dirpath: Path, wavs: dict[int, Path], dst: Path, *,
        fs: int = FS, sample_fmt: str = "pcm_s24be", extra: list[str] | None = None,
        video: bool = True) -> tuple[bool, str]:
    """Mux four mono WAVs (plus a tiny video track) into one take container."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(FFMPEG), "-v", "error", "-nostdin", "-y"]
    if video:
        cmd += ["-f", "lavfi", "-i", "color=c=black:s=320x240:r=30:d=60"]
    for pos in sorted(wavs):
        cmd += ["-i", str(wavs[pos])]
    idx = 1 if video else 0
    if video:
        cmd += ["-map", "0:v:0"]
    for _pos in sorted(wavs):
        cmd += ["-map", f"{idx}:a:0"]
        idx += 1
    if video:
        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "40",
                "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", sample_fmt, "-ar", str(fs), "-ac", "1"]
    if extra:
        cmd += extra
    cmd += [str(dst)]
    p = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True, encoding="utf-8",
                       errors="replace", timeout=1800)
    return p.returncode == 0 and dst.is_file(), (p.stderr or "")[-300:]


def note_text(take_id: str, *, signal_sha: str, kind: str,
              mapping: str = VALID_MAPPING,
              overrides: dict[str, str] | None = None,
              drop: tuple[str, ...] = ()) -> str:
    fields = {
        "take_id": take_id,
        "recorded_at": "2026-10-12T14:03:00+08:00",
        "operator": "P4.11 fixture generator",
        "camera_body_serial": "FIXTURE-NOT-A-REAL-CAMERA",
        "input_mapping": mapping,
        "wireless_tx": "fixture-tx",
        "wireless_rx": "fixture-rx",
        "tx_rx_distance_m": "1.0",
        "wireless_channel": "fixture-ch",
        "tx_gain": "0 dB",
        "rx_output_level": "fixture level",
        "receiver_output_type": "XLR line",
        "agc_limiter_lowcut_nr": "OFF on all four inputs",
        "signal_file_sha256": signal_sha,
        "signal_description": "p4.11 band-limited noise 200-8000 Hz, 60 s",
        "take_kind": kind,
        "notes": "GENERATED FIXTURE - not a real wireless recording",
    }
    for k in drop:
        fields.pop(k, None)
    fields.update(overrides or {})
    return "\n".join(f"{k}: {v}" for k, v in fields.items()) + "\n"


def emit(root: Path, session: str, take_id: str, chans: dict[int, np.ndarray], *,
         kind: str = "MEASURE", signal_sha: str, note_kw: dict | None = None,
         fs: int = FS, sample_fmt: str = "pcm_s24be", clip: bool = False,
         video: bool = True, drop_note: bool = False) -> dict[str, Any]:
    d = root / session
    tmp = FIX / "_tmp" / take_id
    if tmp.exists():
        shutil.rmtree(tmp)
    write_take_wavs(tmp, chans, fs=fs, clip=clip)
    wmap = {p: tmp / f"ch{p}.wav" for p in chans}
    dst = d / f"{take_id}.MP4"
    ok, err = mux(tmp, wmap, dst, fs=fs, sample_fmt=sample_fmt, video=video)
    if not drop_note:
        kw = dict(note_kw or {})
        mapping = kw.pop("mapping", None)
        kind_override = kw.pop("kind", None)
        overrides = dict(kw.pop("overrides", {}) or {})
        dropf = tuple(kw.pop("drop", ()) or ())
        overrides.update(kw)
        # `mapping=None` means "use the default", but an EXPLICITLY SUPPLIED
        # mapping means exactly what it says — including an empty or ambiguous one.
        # The first version used `mapping or default`, so a deliberately ambiguous
        # mapping was silently replaced by a valid one and the negative control
        # passed.
        effective_mapping = VALID_MAPPING if mapping is None else mapping
        (d / f"{take_id}.txt").write_text(
            note_text(take_id, signal_sha=signal_sha,
                      kind=kind_override or kind, mapping=effective_mapping,
                      overrides=overrides, drop=dropf), encoding="utf-8")
    shutil.rmtree(tmp, ignore_errors=True)
    return {"take_id": take_id, "path": str(dst.relative_to(REPO).as_posix()),
            "mux_ok": ok, "mux_error": err if not ok else ""}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=FIX)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    x, smeta = load_signal()
    sig_sha = smeta["file"]["sha256"]
    root = args.out
    if root.exists() and not args.keep:
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    built: list[dict[str, Any]] = []

    lay = smeta["layout"]
    at = lambda s: int(round(s * FS))                       # noqa: E731
    interior = lay["interior_reference_events"]

    # ---------------- positive controls ----------------
    for name, d in (("pos-zero", 0), ("pos-late-137", 137),
                    ("pos-early-137", -137), ("pos-late-3600", 3600)):
        chans = {0: x, 1: x, 2: delayed(x, d), 3: x}
        built.append(emit(root, "positive", name, chans, signal_sha=sig_sha))

    # ---------------- calibration takes ----------------
    # ONE calibration take per session, with a NON-ZERO bias. Two reasons:
    #   * the bias is a property of the rig, so a session with several calibration
    #     takes has several candidate biases and picking one silently would be an
    #     assumption; `ingest.py` refuses that case outright;
    #   * a zero-bias calibration take cannot distinguish "the bias is zero" from
    #     "the bias was never measured", so the calibrated fixture carries a known
    #     non-zero bias and the derived value has to show the subtraction working.
    built.append(emit(root, "calibration", "cal-bias-plus-96",
                      {0: x, 1: x, 2: delayed(x, 96), 3: x},
                      kind="CABLE_CALIBRATION", signal_sha=sig_sha))
    # A MEASURE take inside the calibrated session, so `MEASURED_ACCURACY` is
    # exercised on a real number rather than only on the calibration take.
    built.append(emit(root, "calibration", "cal-measure-late-137",
                      {0: x, 1: x, 2: delayed(x, 137), 3: x},
                      signal_sha=sig_sha))

    # ---------------- time-varying controls ----------------
    start_at = interior[1]["marker_start_sample"] if len(interior) > 1 else at(25.0)
    built.append(emit(root, "timevarying", "tv-step-inside",
                      {0: x, 1: x, 2: piecewise(x, 120, 900, start_at), 3: x},
                      signal_sha=sig_sha))
    built.append(emit(root, "timevarying", "tv-marker-disagree",
                      {0: x, 1: x, 2: piecewise(x, 120, 1400, at(50.0)), 3: x},
                      signal_sha=sig_sha))
    drop = x.copy()
    a, b = at(20.0), at(24.0)
    drop[a:b] = 0.0
    built.append(emit(root, "timevarying", "tv-dropout",
                      {0: x, 1: x, 2: drop, 3: x}, signal_sha=sig_sha))

    # ---------------- negative controls, one defect each ----------------
    good = {0: x, 1: x, 2: delayed(x, 137), 3: x}
    built.append(emit(root, "negative", "neg-missing-note", good,
                      signal_sha=sig_sha, drop_note=True))
    built.append(emit(root, "negative", "neg-wrong-signal-sha", good,
                      signal_sha="0" * 64))
    built.append(emit(root, "negative", "neg-missing-mapping-value", good,
                      signal_sha=sig_sha, note_kw={"mapping": ""}))
    built.append(emit(root, "negative", "neg-mapping-names-one-stream", good,
                      signal_sha=sig_sha,
                      note_kw={"mapping": "0=CH1_direct_cable"}))
    built.append(emit(root, "negative", "neg-restated-stream-name", good,
                      signal_sha=sig_sha,
                      note_kw={"mapping": "0=CH1;1=CH2;2=CH3;3=CH4"}))
    built.append(emit(root, "negative", "neg-identity-unknown", good,
                      signal_sha=sig_sha,
                      note_kw={"mapping": "0=CH1_direct;2=unknown"}))
    built.append(emit(root, "negative", "neg-wrong-sample-rate", good,
                      signal_sha=sig_sha, fs=44_100))
    built.append(emit(root, "negative", "neg-wrong-format", good,
                      signal_sha=sig_sha, sample_fmt="pcm_s16be"))
    built.append(emit(root, "negative", "neg-clipped", good,
                      signal_sha=sig_sha, clip=True))
    built.append(emit(root, "negative", "neg-agc-on", good, signal_sha=sig_sha,
                      note_kw={"agc_limiter_lowcut_nr": "AGC ON (mistake)"}))
    built.append(emit(root, "negative", "neg-missing-note-field", good,
                      signal_sha=sig_sha, note_kw={"drop": ("input_mapping",)}))
    built.append(emit(root, "negative", "neg-kind-unknown", good,
                      signal_sha=sig_sha, note_kw={"kind": "SOMETHING_ELSE"}))
    # A marker that genuinely CANNOT be found: the declared marker region on the
    # wireless channel is replaced by content from OUTSIDE the estimator's band.
    # The first version zeroed the region, which is not corruption at all —
    # zeroing band-limited noise leaves band-limited noise, and the 1 s
    # differential window still locked at the right offset with a 30 dB peak.
    broken = delayed(x, 137).copy()
    ms, me = lay["marker_start"]["start_sample"], lay["marker_start"]["end_sample"]
    tone = 0.5 * np.sin(2.0 * np.pi * 17_000.0 * np.arange(me - ms) / FS)
    broken[ms:me] = tone
    built.append(emit(root, "negative", "neg-marker-corrupt",
                      {0: x, 1: x, 2: broken, 3: x}, signal_sha=sig_sha))
    # a take that is too short to hold a plan window
    short = x[: int(8.0 * FS)]
    built.append(emit(root, "negative", "neg-too-short",
                      {0: short, 1: short, 2: delayed(short, 137), 3: short},
                      signal_sha=sig_sha))

    (root / "fixture-manifest.json").write_text(
        json.dumps({
            "warning": ("GENERATED FIXTURES. They prove the toolchain, not any "
                        "wireless link: every delay here was injected digitally."),
            "generator": "tests/audio_sync_acquisition/make_fixtures.py",
            "signal_sha256": sig_sha,
            "takes": built,
        }, indent=1, ensure_ascii=False), encoding="utf-8")

    ok = sum(1 for b in built if b["mux_ok"])
    print(f"fixtures built : {ok}/{len(built)}")
    for b in built:
        if not b["mux_ok"]:
            print(f"  ERR {b['path']}  {b['mux_error'][:120]}")
    print(f"-> {root / 'fixture-manifest.json'}")
    return 0 if ok == len(built) else 1


if __name__ == "__main__":
    raise SystemExit(main())
