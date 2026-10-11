"""P4.11 — preflight a take before any production analysis touches it.

Runs on the raw delivery (`<session>/<take_id>.MP4` + `.txt`) and decides what the
data IS, using only the production probe model plus read-only decoding. It never
modifies, trims or re-encodes an input, and a take that fails a check is KEPT with
its reason code rather than discarded.

Every verdict carries a stable reason code from the closed vocabulary in
`acquire_common.REASON_CODES`, and the data state is kept separate from the
execution state: `DATA_INELIGIBLE` is a property of the recording, not a software
failure.

Usage:
    python tests/audio_sync_acquisition/preflight.py --input <takes-dir>
    python tests/audio_sync_acquisition/preflight.py --input <takes-dir> --json out.json
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests" / "audio_sync_acquisition"))
sys.path.insert(0, str(REPO))

from acquire_common import (                                # noqa: E402
    ALLOWED_EXT, BITS_EXPECTED, CHANNELS_EXPECTED,
    CONTAINER_CHANNELS_EXPECTED, DATA_STATES, FS_EXPECTED, REASON_CODES,
    TAKE_KINDS, OperatorNote, SignalLayout, SignalManifestError,
    load_signal_manifest, parse_operator_note,
)
from core.probe import probe_streams                        # noqa: E402
from core.audio_probe import build_audio_streams            # noqa: E402

WORK = REPO / "work" / "p4.11-acquisition"
FFMPEG = REPO / "tools" / "ffmpeg.exe"
FFPROBE = REPO / "tools" / "ffprobe.exe"

#: Diagnostic qualifiers for the marker check. Belong to this tool, are NOT
#: production thresholds, and are never applied to a production estimate.
MARKER_MIN_PSR = 3.0
MARKER_MIN_ABS_RMS_DBFS = -60.0
#: How strongly the declared marker region must correlate with the marker the
#: generator actually produced, for the marker to count as PRESENT. This is a
#: shape test against KNOWN content, which is why it detects corruption; a
#: peak-to-sidelobe ratio on the same region does not, because the content and the
#: marker are both band-limited noise at the same amplitude. Measured: the first
#: `neg-marker-corrupt` fixture passed with a 30 dB peak-to-sidelobe ratio.
MARKER_MIN_CORRELATION = 0.5
#: How far the marker search looks for the marker. The production search window is
#: +/-80 ms = +/-3840 samples, so a marker that is inside the window is found; a
#: marker further away is reported absent, which for this protocol means the take
#: cannot be aligned by markers at all.
MARKER_SEARCH_SAMPLES = 3840
#: A marker pair whose differential offsets differ by more than this is
#: time-varying. One sample is the estimator's own integer resolution, so the
#: threshold cannot be tighter than that without inventing precision.
MARKER_AGREE_TOLERANCE_SAMPLES = 1.0
#: Takes shorter than one production plan window cannot reach the time-varying
#: path at all; recorded as a data property, not as a pass.
MIN_USEFUL_SECONDS = 30.0
#: Absolute value above which a sample is treated as clipped.
CLIP_THRESHOLD = 0.9995

#: The ffprobe codec name a given declared sample format must appear as, per
#: container. Measured, not guessed: `signal.wav` reports `pcm_s24le`, the MP4/MOV
#: takes report `pcm_s24be`, and a 16-bit take reports `pcm_s16be`.
PCM_CODEC_BY_CONTAINER: dict[str, dict[str, str]] = {
    ".mp4": {"s24le": "pcm_s24be", "s24be": "pcm_s24be"},
    ".mov": {"s24le": "pcm_s24be", "s24be": "pcm_s24be"},
    ".wav": {"s24le": "pcm_s24le", "s24be": "pcm_s24be"},
}


def rms_dbfs(a: np.ndarray) -> float:
    if a.size == 0:
        return float("-inf")
    ssq = float(np.sum(a * a))
    return float("-inf") if ssq <= 0 else 10.0 * math.log10(ssq / a.size)


def decode(src: Path, pos: int, dst: Path, *, start_s: float | None = None,
           seconds: float | None = None) -> tuple[bool, str]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(FFMPEG), "-v", "error", "-nostdin", "-y"]
    if start_s is not None:
        cmd += ["-ss", f"{start_s:.6f}"]
    if seconds is not None:
        cmd += ["-t", f"{seconds:.6f}"]
    cmd += ["-i", str(src), "-map", f"0:a:{pos}", "-vn", "-sn", "-dn",
            "-f", "f32le", "-ac", "1", "-ar", str(FS_EXPECTED), str(dst)]
    p = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True, encoding="utf-8",
                       errors="replace", timeout=1800)
    return p.returncode == 0 and dst.is_file() and dst.stat().st_size > 0, \
        (p.stderr or "")[-240:]


def differential_offset(ref: np.ndarray, tgt: np.ndarray, *,
                        max_lag: int = 4800) -> dict[str, Any]:
    """Offset between two channels over the SAME stretch of content.

    `c[m] = sum ref[i] * tgt[i + m]` peaks at the target's offset relative to the
    reference. Sign convention proved on a ramp in
    `work/p4.11-acquisition/sign-chain.json`: POSITIVE means the target's copy of
    this content sits LATER in the file.
    """
    n = int(min(ref.shape[0], tgt.shape[0]))
    if n < 2 * max_lag + 2:
        return {"ok": False, "reason": "window_too_short", "samples": n}
    x = np.asarray(ref[:n], dtype=np.float64)
    y = np.asarray(tgt[:n], dtype=np.float64)
    if x.std() <= 0 or y.std() <= 0:
        return {"ok": False, "reason": "zero_variance", "samples": n}
    x = x - x.mean()
    y = y - y.mean()
    nfft = 1
    while nfft < 2 * n:
        nfft <<= 1
    c = np.fft.irfft(np.fft.rfft(x, nfft).conj() * np.fft.rfft(y, nfft), nfft)

    def val(lag: int) -> float:
        return float(c[lag]) if lag >= 0 else float(c[nfft + lag])

    vals = np.array([val(l) for l in range(-max_lag, max_lag + 1)])
    i = int(np.argmax(vals))
    frac = 0.0
    if 0 < i < vals.size - 1:
        y0, y1, y2 = float(vals[i - 1]), float(vals[i]), float(vals[i + 1])
        den = y0 - 2 * y1 + y2
        if den != 0:
            frac = max(-0.5, min(0.5, 0.5 * (y0 - y2) / den))
    guard = 24
    others = np.concatenate((vals[: max(0, i - guard)], vals[i + guard + 1:]))
    side = float(others.max()) if others.size else 0.0
    peak = float(vals[i])
    denom = float(np.sqrt(np.sum(x * x) * np.sum(y * y)))
    return {
        "ok": True,
        "offset_samples": -max_lag + i + frac,
        "offset_int_samples": int(-max_lag + i),
        "normalised_peak": (peak / denom) if denom > 0 else None,
        "peak_to_sidelobe": (peak / side) if side > 0 else None,
        "samples": n,
    }


def rel(p: Path) -> str:
    """Repo-relative POSIX path, tolerating a path outside the repo."""
    try:
        return p.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return p.as_posix()


def check_take(take_mp4: Path, layout: SignalLayout,
               calib_bias_samples: float | None,
               template: np.ndarray | None = None) -> dict[str, Any]:
    take_mp4 = take_mp4.resolve()
    stem = take_mp4.stem
    session = take_mp4.parent.name
    note_path = take_mp4.with_suffix(".txt")
    rec: dict[str, Any] = {
        "take_id": stem, "session": session,
        "take_path": rel(take_mp4),
        "note_path": rel(note_path) if note_path.is_file() else None,
        "reasons": [], "data_state": None, "kind": None,
        "structural": {}, "identity": {}, "signal": {}, "marker": {},
        "calibration": {},
    }

    def fail(code: str) -> None:
        if code not in REASON_CODES:
            raise AssertionError(f"unknown reason code invented: {code}")
        if code not in rec["reasons"]:
            rec["reasons"].append(code)

    # ---- note ------------------------------------------------------------
    note: OperatorNote = parse_operator_note(note_path)
    rec["identity"] = note.as_dict()
    if not note_path.is_file():
        fail("NOTE_FILE_MISSING")
    else:
        if note.unparseable_lines:
            fail("OPERATOR_NOTE_UNPARSEABLE")
        for f in note.missing_required:
            if f == "input_mapping":
                fail("INPUT_MAPPING_MISSING")
            elif f == "take_kind":
                fail("TAKE_KIND_MISSING")
            else:
                fail("DEVICE_FIELDS_MISSING")
        if note.missing_required and "input_mapping" in note.missing_required:
            pass
        kind = (note.fields.get("take_kind") or "").strip().upper()
        rec["kind"] = kind or None
        if kind and kind not in TAKE_KINDS:
            fail("TAKE_KIND_UNKNOWN")
        mapping = note.input_mapping
        if mapping:
            if len(mapping) < CHANNELS_EXPECTED:
                fail("INPUT_MAPPING_INCOMPLETE")
            # AMBIGUITY, defined so it cannot fire on a legitimate name.
            # A mapping is ambiguous when it does not name a PHYSICAL input:
            #   * a bare stream restatement (`0=CH1`) adds no information;
            #   * a name offering alternatives (`0=CH1_or_CH2`) identifies nothing.
            # A descriptive name that merely CONTAINS a channel token
            # (`0=CH1_input1_direct_cable`) is fine and must not be flagged —
            # the first version of this check rejected it, which would have
            # failed every correctly filled note.
            for k, v in mapping.items():
                low = v.strip().lower()
                bare = low == f"ch{k + 1}" or low == f"a{k}" or low.isdigit()
                alternatives = any(tok in low for tok in
                                   ("_or_", " or ", "/", "either", "unknown",
                                    "tbd", "?"))
                if bare or alternatives:
                    fail("INPUT_MAPPING_AMBIGUOUS")
        agc = (note.fields.get("agc_limiter_lowcut_nr") or "").lower()
        if agc and "off" not in agc:
            fail("AGC_OR_LIMITER_ENABLED")
        if note.fields.get("signal_file_sha256", "").upper() != layout.sha256:
            fail("SIGNAL_SHA_MISMATCH")

    # ---- container -------------------------------------------------------
    try:
        fmt, streams = probe_streams(FFPROBE, take_mp4)
    except Exception as exc:                                # noqa: BLE001
        fail("CONTAINER_PROBE_FAILED")
        rec["structural"]["probe_error"] = f"{type(exc).__name__}: {exc}"[:200]
        rec["data_state"] = "DATA_INELIGIBLE"
        return rec

    rec["structural"]["container"] = str(fmt.get("format_name") or "") or None
    rec["structural"]["format_duration_sec"] = fmt.get("duration")
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    vids = [s for s in streams if s.get("codec_type") == "video"
            and str(s.get("codec_name") or "") != "mjpeg"]
    rec["structural"]["audio_stream_count"] = len(audio)
    rec["structural"]["video_present"] = bool(vids)
    rec["structural"]["audio_codecs"] = [str(s.get("codec_name") or "") for s in audio]
    rec["structural"]["sample_rates"] = [s.get("sample_rate") for s in audio]
    rec["structural"]["channels_per_stream"] = [s.get("channels") for s in audio]
    rec["structural"]["bits_per_sample"] = [
        s.get("bits_per_raw_sample") or s.get("bits_per_coded_sample") for s in audio]

    if len(audio) != CHANNELS_EXPECTED:
        fail("AUDIO_STREAM_COUNT_WRONG")
    if any(int(s.get("channels") or 0) != CONTAINER_CHANNELS_EXPECTED for s in audio):
        fail("CHANNEL_LAYOUT_WRONG")
    if any(int(s.get("sample_rate") or 0) != FS_EXPECTED for s in audio):
        fail("SAMPLE_RATE_WRONG")
    # Format is checked against the CONTAINER-APPROPRIATE name for the manifest's
    # declared sample format, and the codec name is compared directly because
    # 16-bit PCM reports no bits_per_raw_sample at all — a bit-depth check alone
    # lets `pcm_s16be` through. Both facts were measured, not assumed:
    #
    #   signal.wav            -> pcm_s24le   (the player's file, little-endian)
    #   take.MP4 / take.MOV   -> pcm_s24be   (QuickTime/ISO linear PCM is BE)
    #   a 16-bit take         -> pcm_s16be, bits_per_raw_sample absent
    #
    # The first version compared the take's codec against the manifest's WAV
    # format and failed every correct fixture.
    if not all(str(s.get("codec_name") or "").startswith("pcm")
               for s in audio):
        fail("PCM_FORMAT_WRONG")
    expected_fmt = str(layout.raw.get("spec", {}).get("sample_format") or "").lower()
    expected_codec = PCM_CODEC_BY_CONTAINER.get(
        take_mp4.suffix.lower(), {}).get(expected_fmt)
    observed = [str(s.get("codec_name") or "") for s in audio]
    if expected_codec and any(o != expected_codec for o in observed):
        fail("PCM_FORMAT_WRONG")
    bits = [int(b) for b in rec["structural"]["bits_per_sample"] if b]
    if bits and any(b != BITS_EXPECTED for b in bits):
        fail("BIT_DEPTH_WRONG")
    if expected_fmt == "s24le" and observed and all(
            "s16" in o or "s32" in o for o in observed):
        fail("BIT_DEPTH_WRONG")

    # ---- decode and measure ---------------------------------------------
    if len(audio) < 2:
        rec["data_state"] = "DATA_INELIGIBLE"
        return rec

    tmp = WORK / "raw"
    tmp.mkdir(parents=True, exist_ok=True)
    arrs: dict[int, np.ndarray] = {}
    for pos in range(len(audio)):
        dst = tmp / f"pf.{stem}.{pos}.f32"
        ok, err = decode(take_mp4, pos, dst)
        if not ok:
            fail("DECODE_FAILED")
            rec["structural"]["decode_error"] = err
            rec["data_state"] = "DATA_INELIGIBLE"
            return rec
        arrs[pos] = np.fromfile(dst, dtype="<f4")
        dst.unlink(missing_ok=True)

    n = int(min(a.shape[0] for a in arrs.values()))
    dur = n / FS_EXPECTED
    rec["structural"]["decoded_samples_per_stream"] = {str(k): int(v.shape[0])
                                                       for k, v in arrs.items()}
    rec["structural"]["decoded_seconds"] = round(dur, 4)
    rec["structural"]["rms_dbfs"] = {str(k): round(rms_dbfs(v), 2)
                                     for k, v in arrs.items()}
    rec["structural"]["peak_abs"] = {str(k): round(float(np.max(np.abs(v))), 6)
                                     for k, v in arrs.items()}
    if dur < MIN_USEFUL_SECONDS:
        fail("TAKE_TOO_SHORT")
    declared = float(rec["structural"]["format_duration_sec"] or dur)
    if abs(declared - dur) > 1.0:
        fail("DURATION_MISMATCH")

    if any(p >= CLIP_THRESHOLD for p in rec["structural"]["peak_abs"].values()):
        fail("SIGNAL_CLIPPED")
    if all(r < MARKER_MIN_ABS_RMS_DBFS
           for r in rec["structural"]["rms_dbfs"].values()):
        fail("SIGNAL_ABSENT")

    # ---- marker check: two INDEPENDENT things, and they are not the same check.
    #
    # (a) NORMATIVE — is the declared marker actually present in the recording?
    #     The detector's peak-to-sidelobe ratio is NOT a corruption test: content
    #     and marker are both band-limited noise at the same amplitude, so the
    #     ratio stays high (~30 dB) whether or not the marker is there. Measured on
    #     the first `neg-marker-corrupt` fixture, which passed. The only
    #     unambiguous test is to correlate the declared marker region against the
    #     marker the generator actually produced.
    #
    # (b) DIFFERENTIAL — the reference channel against the wireless channel in a
    #     window around each marker. That is the quantity that decides constant vs
    #     time-varying delay.
    ref_pos, tgt_pos = 0, 2
    ms, me = layout.marker_start
    es, ee = layout.marker_end
    ref_ch, tgt_ch = arrs[ref_pos], arrs[tgt_pos]

    marker_presence: dict[str, Any] = {}
    if template is not None:
        tm = template - float(template.mean())
        tnorm = float(np.sqrt(np.sum(tm * tm)))
        for label, (a, b) in (("start", (ms, me)), ("end", (es, ee))):
            for ch_pos in sorted(arrs):
                seg = arrs[ch_pos]
                if b > seg.shape[0]:
                    marker_presence[f"{label}_stream{ch_pos}"] = {
                        "ok": False, "reason": "out_of_range"}
                    continue
                # Search over a bounded neighbourhood rather than assuming the
                # marker sits at offset 0. A take whose wireless channel is late
                # carries the marker LATER, so a zero-offset comparison reports
                # "absent" for every correctly shifted take — which is exactly
                # what the first version of this check did.
                best, best_at = 0.0, 0
                for off in range(-MARKER_SEARCH_SAMPLES, MARKER_SEARCH_SAMPLES + 1):
                    lo, hi = a + off, b + off
                    if lo < 0 or hi > seg.shape[0]:
                        continue
                    g = seg[lo:hi].astype(np.float64)
                    g = g - g.mean()
                    den = tnorm * float(np.sqrt(np.sum(g * g)))
                    if den <= 0:
                        continue
                    c = float(np.dot(tm, g)) / den
                    if c > best:
                        best, best_at = c, off
                marker_presence[f"{label}_stream{ch_pos}"] = {
                    "best_correlation": round(best, 4),
                    "best_offset_samples": int(best_at),
                    "present": bool(best >= MARKER_MIN_CORRELATION),
                }
        corrs = [v.get("best_correlation", 0.0) for v in marker_presence.values()
                 if "best_correlation" in v]
        worst = min(corrs) if corrs else 0.0
        rec["marker"]["presence"] = marker_presence
        rec["marker"]["worst_marker_correlation"] = round(worst, 4)
        if corrs and worst < MARKER_MIN_CORRELATION:
            fail("MARKER_CORRUPT")

    half = int(1.0 * FS_EXPECTED)     # +/-1 s around each marker
    win_start = (max(0, ms - half), min(n, me + half))
    win_end = (max(0, es - half), min(n, ee + half))
    s = differential_offset(ref_ch[win_start[0]:win_start[1]],
                            tgt_ch[win_start[0]:win_start[1]])
    e = differential_offset(ref_ch[win_end[0]:win_end[1]],
                            tgt_ch[win_end[0]:win_end[1]])
    rec["marker"] = {**rec["marker"],
                     "start": s, "end": e,
                     "window_samples": {"start": list(win_start),
                                        "end": list(win_end)},
                     "tolerance_samples": MARKER_AGREE_TOLERANCE_SAMPLES}
    if not s.get("ok") or not e.get("ok"):
        fail("MARKER_MISSING")
    else:
        if abs(s["offset_samples"] - e["offset_samples"]) > \
                MARKER_AGREE_TOLERANCE_SAMPLES:
            fail("MARKER_MISMATCH_TIME_VARYING")
        rec["marker"]["offsets_agree"] = bool(
            abs(s["offset_samples"] - e["offset_samples"])
            <= MARKER_AGREE_TOLERANCE_SAMPLES)

    # ---- dropout: a long run of near-silence inside the wireless channel
    seg = tgt_ch[:n]
    blk = int(0.5 * FS_EXPECTED)
    quiet = 0
    worst = 0
    for a in range(0, n - blk, blk):
        if rms_dbfs(seg[a:a + blk]) < -70.0:
            quiet += 1
            worst = max(worst, quiet)
        else:
            quiet = 0
    rec["signal"]["longest_silent_run_seconds"] = round(worst * 0.5, 2)
    if worst >= 2:
        fail("DROP_OUT_DETECTED")

    # ---- calibration ----------------------------------------------------
    rec["calibration"] = {
        "take_kind": rec["kind"],
        "is_calibration": rec["kind"] == "CABLE_CALIBRATION",
        "bias_available_from_session": calib_bias_samples,
    }
    if rec["kind"] == "CABLE_CALIBRATION":
        # the calibration take's own offset IS the reference-path bias
        rec["calibration"]["measured_bias_samples"] = (
            s.get("offset_samples") if s.get("ok") else None)

    # ---- data state -----------------------------------------------------
    blocking_identity = {"INPUT_MAPPING_MISSING", "INPUT_MAPPING_INCOMPLETE",
                         "INPUT_MAPPING_AMBIGUOUS", "IDENTITY_UNCONFIRMED",
                         "TAKE_KIND_MISSING", "TAKE_KIND_UNKNOWN",
                         "DEVICE_FIELDS_MISSING", "NOTE_FILE_MISSING",
                         "OPERATOR_NOTE_UNPARSEABLE", "SIGNAL_SHA_MISMATCH"}
    blocking_data = {"CONTAINER_PROBE_FAILED", "AUDIO_STREAM_COUNT_WRONG",
                     "SAMPLE_RATE_WRONG", "PCM_FORMAT_WRONG", "BIT_DEPTH_WRONG",
                     "CHANNEL_LAYOUT_WRONG", "DECODE_FAILED", "SIGNAL_CLIPPED",
                     "SIGNAL_ABSENT", "AGC_OR_LIMITER_ENABLED"}
    reasons = set(rec["reasons"])
    if reasons & blocking_data:
        rec["data_state"] = "DATA_INELIGIBLE"
    elif {"MARKER_MISMATCH_TIME_VARYING", "DROP_OUT_DETECTED"} & reasons:
        rec["data_state"] = "TIME_VARYING_CANDIDATE"
    elif reasons & blocking_identity:
        rec["data_state"] = "IDENTITY_UNCONFIRMED"
    elif reasons:
        rec["data_state"] = "DATA_INELIGIBLE"
    else:
        rec["data_state"] = ("CONSTANT_DELAY_CANDIDATE"
                             if (s.get("ok") and e.get("ok") and
                                 rec["marker"].get("offsets_agree"))
                             else "READY_FOR_ANALYSIS")
    rec["status"] = "PASS" if not rec["reasons"] else "FAIL"
    return rec


def discover(roots: list[Path]) -> list[Path]:
    out: list[Path] = []
    for r in roots:
        if r.is_file() and r.suffix.lower() in ALLOWED_EXT:
            out.append(r)
        elif r.is_dir():
            for p in sorted(r.rglob("*")):
                if p.is_file() and p.suffix.lower() in ALLOWED_EXT:
                    out.append(p)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, nargs="+", required=True,
                    help="take directory (or directories / files)")
    ap.add_argument("--signal-manifest", type=Path,
                    default=WORK / "generated-signal" / "signal-manifest.json")
    ap.add_argument("--signal-wav", type=Path,
                    default=WORK / "generated-signal" / "signal.wav",
                    help="the generated signal, used to test that the declared "
                         "marker is actually PRESENT in each take")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--jsonl", type=Path, default=None)
    args = ap.parse_args(argv)

    try:
        layout = load_signal_manifest(args.signal_manifest)
    except SignalManifestError as exc:
        print(f"BLOCKED: {exc}")
        return 2

    template = None
    if args.signal_wav.is_file():
        import wave as _wave
        with _wave.open(str(args.signal_wav), "rb") as w:
            raw = w.readframes(w.getnframes())
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        q = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        q = np.where(q >= (1 << 23), q - (1 << 24), q)
        sig = q.astype(np.float64) / float(2 ** 23 - 1)
        a0, a1 = layout.marker_start
        template = sig[a0:a1].copy()
        # mean-removed so the correlation is a shape test, not a DC test
        template = template - template.mean()

    takes = discover(list(args.input))
    print(f"signal manifest : {args.signal_manifest}")
    print(f"  sha256 {layout.sha256}  {layout.sample_count} samples "
          f"({layout.duration_seconds:.1f}s @ {layout.sample_rate} Hz)")
    print(f"  marker start {layout.marker_start}  end {layout.marker_end} "
          f"({layout.marker_ms:.0f} ms)")
    print(f"takes found     : {len(takes)}\n")

    # a session-wide calibration take, if one exists, supplies the bias for its
    # session; the bias is RECORDED, never applied silently
    calib_by_session: dict[str, float] = {}
    records = []
    for t in takes:
        rec = check_take_preliminary_ok(t, layout, calib_by_session, template)
        records.append(rec)
        if (rec.get("calibration", {}).get("is_calibration")
                and rec["calibration"].get("measured_bias_samples") is not None):
            calib_by_session[rec["session"]] = \
                rec["calibration"]["measured_bias_samples"]

    print(f"{'take':26s} {'status':6s} {'data state':24s} reasons")
    for r in records:
        print(f"{r['take_id']:26s} {r['status']:6s} "
              f"{str(r['data_state']):24s} {','.join(r['reasons']) or '-'}")

    states = Counter(r["data_state"] for r in records)
    statuses = Counter(r["status"] for r in records)
    summary = {
        "signal_manifest": str(args.signal_manifest),
        "signal_sha256": layout.sha256,
        "takes": len(records),
        "statuses": dict(sorted(statuses.items())),
        "data_states": dict(sorted(states.items())),
        "calibration_bias_by_session_samples": calib_by_session,
        "reason_counts": dict(sorted(Counter(
            c for r in records for c in r["reasons"]).items())),
    }
    print(f"\nstatuses    : {summary['statuses']}")
    print(f"data states : {summary['data_states']}")
    print(f"calib bias  : {calib_by_session or '(no calibration take found)'}")

    out = {"summary": summary, "takes": records}
    dst = args.json or (WORK / "preflight" / "preflight.json")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    if args.jsonl:
        args.jsonl.parent.mkdir(parents=True, exist_ok=True)
        with args.jsonl.open("w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"-> {dst}")
    return 0


def check_take_preliminary_ok(take: Path, layout: SignalLayout,
                             calib_by_session: dict[str, float],
                             template: np.ndarray | None) -> dict[str, Any]:
    return check_take(take, layout, calib_by_session.get(take.parent.name),
                      template)


if __name__ == "__main__":
    raise SystemExit(main())

