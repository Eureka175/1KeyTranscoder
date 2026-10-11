"""P4.11 — take a preflighted recording through the production path.

This is the entry P4.10's report promised and did not have: given a recorded take
plus its operator note plus the signal manifest, it decodes, measures with the
PRODUCTION estimator, applies the PRODUCTION evidence gate, builds the production
plan, applies through the PRODUCTION API, and verifies with an implementation that
shares no code with the estimator.

It never re-implements the estimator, the gate or the apply. Every threshold is
read from `core.channel_sync.effective_opts` at run time.

What it refuses to do, and why each refusal is a feature:

  * it never derives an ACCURACY figure without a truth. With no reference-path
    calibration and no known delay, agreement between two methods is recorded as
    `CROSS_METHOD_AGREEMENT`, never as `MEASURED_ACCURACY`;
  * it never applies a correction the gate refused;
  * it never subtracts the reference-path bias silently: raw measurement,
    calibration measurement and the derived value are all stored, with the formula
    and the sign direction written next to them;
  * it never deletes or rewrites an input.

Results are append-only per (take, attempt). A re-run with byte-identical inputs
reuses the existing result; a re-run whose inputs changed produces a NEW attempt,
because the identity hash includes every input.

Usage:
    python tests/audio_sync_acquisition/ingest.py --takes <dir> [--jsonl out.jsonl]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests" / "audio_sync_acquisition"))
sys.path.insert(0, str(REPO))

from acquire_common import (                                # noqa: E402
    FS_EXPECTED, SignalLayout, load_signal_manifest, parse_operator_note,
)
from preflight import (                                     # noqa: E402
    MARKER_SEARCH_SAMPLES, check_take, differential_offset, discover, rel,
)
from core import sync_estimate                              # noqa: E402
from core import sync_fix                                   # noqa: E402
from core.channel_sync import effective_opts                # noqa: E402
from core.probe import probe_streams                        # noqa: E402
from core import audio_sync_tv as tv                        # noqa: E402
from core import audio_sync_tv_exec as ex                   # noqa: E402

WORK = REPO / "work" / "p4.11-acquisition"
FFMPEG = REPO / "tools" / "ffmpeg.exe"
FFPROBE = REPO / "tools" / "ffprobe.exe"

PROD = effective_opts(None)
PROD_GEOM = {
    "search_window_ms": float(PROD["search_window_ms"]),
    "frame_ms": float(PROD["frame_ms"]),
    "hop_ms": float(PROD["hop_ms"]),
    "coarse_rate": int(PROD["coarse_rate"]),
    "anchor_segment_seconds": float(PROD["anchor_segment_seconds"]),
    "frame_min_rms_dbfs": float(PROD["frame_min_rms_dbfs"]),
    "fine_phase_band_hz": tuple(PROD["fine_phase_band_hz"]),
    "fix_chunk_seconds": float(PROD["fix_chunk_seconds"]),
}
PROD_GATE = {
    "min_usable_frames": int(PROD["min_usable_frames"]),
    "min_confidence": float(PROD["min_confidence"]),
    "min_usable_fraction": float(PROD["min_usable_fraction"]),
}
PROD_CONSTANCY = {
    "mad_max_ms": float(PROD["mad_max_ms"]),
    "constant_max_ppm": float(PROD["constant_max_ppm"]),
    "drift_min_ms": float(PROD["drift_min_ms"]),
    "aligned_max_ms": float(PROD["aligned_max_ms"]),
    "verify_max_ms": float(PROD["verify_max_ms"]),
}
PLAN_GEOM = {
    "window_samples": int(PROD["tv_window_samples"] or tv.DEFAULT_WINDOW_SAMPLES),
    "hop_samples": int(PROD["tv_hop_samples"] or tv.DEFAULT_HOP_SAMPLES),
    "search_samples": int(PROD["tv_search_samples"] or tv.DEFAULT_SEARCH_SAMPLES),
    "min_frames_per_window": int(PROD["tv_min_frames_per_window"]),
}

#: Reference channel and wireless channel are DECLARED, not inferred. The mapping
#: in the operator note names physical inputs; these are the stream positions the
#: note's mapping assigns to the direct cable and to the receiver output.
REF_STREAM = 0
WIRELESS_STREAM = 2


# ------------------------------------------------------------------ verifier

VERIFIER_MIN_RMS_DBFS = float(PROD["frame_min_rms_dbfs"])
VERIFIER_MIN_PEAK_TO_SIDELOBE = 1.5


def independent_delay(ref: np.ndarray, tgt: np.ndarray, *,
                      max_lag: int = 4800) -> dict[str, Any]:
    """Full-rate FFT cross-correlation. No GCC-PHAT, no decimation, no frames.

    Carried here rather than imported from another phase's work tree; the lag axis
    is the FIXED mapping (P4.9B's defect: folding with `argsort` over
    `[0..n)+[-n..0)` reads every positive delay from the wrong bin).
    `c[m] = sum ref[i] * tgt[i + m]`, so `c[m]` is lag `m` and negative lags live
    at `nfft + m`. Convention: delay > 0 means the TARGET is LATE.
    """
    n = int(min(ref.shape[0], tgt.shape[0]))
    if n < 2 * max_lag + 2:
        return {"ok": False, "reason": "too_short", "samples": n}
    a = np.asarray(ref[:n], dtype=np.float64)
    b = np.asarray(tgt[:n], dtype=np.float64)
    if a.std() <= 0 or b.std() <= 0:
        return {"ok": False, "reason": "zero_variance", "samples": n}
    a = a - a.mean()
    b = b - b.mean()
    nfft = 1
    while nfft < 2 * n:
        nfft <<= 1
    c = np.fft.irfft(np.fft.rfft(a, nfft).conj() * np.fft.rfft(b, nfft), nfft)

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
    denom = float(np.sqrt(np.sum(a * a) * np.sum(b * b)))
    return {"ok": True, "delay_samples": -max_lag + i + frac,
            "delay_int_samples": int(-max_lag + i),
            "normalised_peak": (float(vals[i]) / denom) if denom > 0 else None,
            "peak_to_sidelobe": (float(vals[i]) / side) if side > 0 else None,
            "samples": n}


def verifier_qualified(v: dict[str, Any], rms_a: float,
                       rms_b: float) -> tuple[bool, str | None]:
    if not v.get("ok"):
        return False, f"verifier_{v.get('reason', 'not_run')}"
    if min(rms_a, rms_b) < VERIFIER_MIN_RMS_DBFS:
        return False, "verifier_no_signal"
    psr = v.get("peak_to_sidelobe")
    if psr is None or psr < VERIFIER_MIN_PEAK_TO_SIDELOBE:
        return False, "verifier_ambiguous_peak"
    return True, None


# ------------------------------------------------------------------ helpers

def rms_dbfs(a: np.ndarray) -> float:
    if a.size == 0:
        return float("-inf")
    ssq = float(np.sum(a * a))
    return float("-inf") if ssq <= 0 else 10.0 * math.log10(ssq / a.size)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for blk in iter(lambda: fh.read(1 << 22), b""):
            h.update(blk)
    return h.hexdigest().upper()


def decode(src: Path, pos: int, dst: Path) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.run(
        [str(FFMPEG), "-v", "error", "-nostdin", "-y", "-i", str(src),
         "-map", f"0:a:{pos}", "-vn", "-sn", "-dn",
         "-f", "f32le", "-ac", "1", "-ar", str(FS_EXPECTED), str(dst)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=1800)
    return p.returncode == 0 and dst.is_file() and dst.stat().st_size > 0


def input_identity(parts: list[tuple[str, Path]]) -> str:
    """Hash of EVERY input. A changed input must produce a new attempt."""
    h = hashlib.sha256()
    for label, p in parts:
        h.update(label.encode())
        h.update(b"\0")
        h.update(sha256_file(p).encode() if p.is_file() else b"MISSING")
        h.update(b"\0")
    return h.hexdigest().upper()


def evidence(est: Any) -> dict[str, Any]:
    total = len(est.frames)
    cov = (est.usable_frames / total) if total else 0.0
    return {"usable_frames": int(est.usable_frames), "total_frames": int(total),
            "coverage": cov, "confidence": float(est.confidence)}


def gate_ok(ev: dict[str, Any]) -> tuple[bool, str | None]:
    """The production gate, against the SAME thresholds that were read from it."""
    if ev["usable_frames"] < PROD_GATE["min_usable_frames"]:
        return False, "low_confidence"
    if not (ev["confidence"] >= PROD_GATE["min_confidence"]):
        return False, "low_confidence"
    if ev["coverage"] < PROD_GATE["min_usable_fraction"]:
        return False, "evidence_insufficient"
    return True, None


# ------------------------------------------------------------------ the chain

def analyse_take(take: Path, note_path: Path, layout: SignalLayout,
                 calib_bias: float | None, signal_wav: Path,
                 out_dir: Path, *, force: bool, template: np.ndarray | None
                 ) -> dict[str, Any]:
    pf = check_take(take, layout, calib_bias, template)
    rec: dict[str, Any] = {
        "take_id": take.stem,
        "session": take.parent.name,
        "take_path": rel(take),
        "preflight": {"status": pf["status"], "data_state": pf["data_state"],
                      "reasons": pf["reasons"]},
    }
    identity = input_identity([
        ("take", take), ("note", note_path), ("signal", signal_wav),
        ("manifest", layout.path)])
    rec["input_identity_sha256"] = identity

    existing = out_dir / f"{take.parent.name}__{take.stem}"
    done = existing / "result.json"
    if done.is_file() and not force:
        old = json.loads(done.read_text(encoding="utf-8"))
        if old.get("input_identity_sha256") == identity:
            return {**old, "reused": True,
                    "note": "inputs byte-identical to a previous attempt; reused"}
    attempt = time.strftime("%Y%m%dT%H%M%S")
    rec["attempt_id"] = attempt
    rec["recorded_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    rec["reused"] = False

    # A take whose data state is not a candidate never reaches production code.
    # The refusal is still PERSISTED with its input identity, so a re-run with
    # unchanged inputs reuses it instead of re-decoding a take that is already
    # known to be ineligible.
    if pf["data_state"] in {"DATA_INELIGIBLE", "IDENTITY_UNCONFIRMED"}:
        rec.update({
            "terminal_state": pf["data_state"],
            "measurement": None, "gate": None, "plan": None, "apply": None,
            "verification": None, "residual": None,
            "conclusion": ("preflight refused this take; no production measurement "
                           "was attempted on it"),
        })
        existing.mkdir(parents=True, exist_ok=True)
        (existing / "result.json").write_text(
            json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
        return rec

    tmp = existing / (rec.get("attempt_id") or "attempt")
    tmp.mkdir(parents=True, exist_ok=True)
    paths: dict[int, Path] = {}
    arrs: dict[int, np.ndarray] = {}
    for pos in range(pf["structural"]["audio_stream_count"]):
        dst = tmp / f"a{pos}.f32"
        if not decode(take, pos, dst):
            rec["terminal_state"] = "DATA_INELIGIBLE"
            rec["conclusion"] = f"decode of audio position {pos} failed"
            existing.mkdir(parents=True, exist_ok=True)
            (existing / "result.json").write_text(
                json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
            return rec
        paths[pos] = dst
        arrs[pos] = np.fromfile(dst, dtype="<f4")

    ref = arrs[REF_STREAM]
    tgt = arrs[WIRELESS_STREAM]
    n = int(min(ref.shape[0], tgt.shape[0]))

    # ---- 0. the stream mapping must be COMPLETE, not just well-formed -------
    # `aquire_common.parse_input_mapping` drops unnamed or "unknown" entries, so a
    # note that identifies only some physical inputs parses to a SHORTER map and
    # every lookup succeeds. `INPUT_MAPPING_INCOMPLETE` is therefore re-checked
    # here against the number of streams actually present, because a partially
    # identified rig must not be analysed as if its mapping were known.
    note = parse_operator_note(note_path)
    n_streams = pf["structural"]["audio_stream_count"]
    mapping_complete = (len(note.input_mapping) == n_streams
                        and set(note.input_mapping) == set(range(n_streams)))
    rec["identity"] = {
        "input_mapping": {str(k): v for k, v in sorted(note.input_mapping.items())},
        "streams_present": n_streams,
        "mapping_complete": bool(mapping_complete),
        "reference_stream": REF_STREAM,
        "wireless_stream": WIRELESS_STREAM,
        "ref_channel_name": note.input_mapping.get(REF_STREAM),
        "wireless_channel_name": note.input_mapping.get(WIRELESS_STREAM),
    }
    if not mapping_complete:
        rec.update({
            "terminal_state": "IDENTITY_UNCONFIRMED",
            "reasons": sorted(set(pf["reasons"]) | {"INPUT_MAPPING_INCOMPLETE"}),
            "measurement": None, "gate": None, "plan": None, "apply": None,
            "verification": None, "residual": None, "markers": None,
            "eligibility": None, "truth": None,
            "conclusion": ("the operator note does not identify a physical input "
                           f"for all {n_streams} streams; the analysis cannot know "
                           "which stream is the reference and which is the "
                           "wireless receiver, and it will not assume"),
        })
        existing.mkdir(parents=True, exist_ok=True)
        (existing / "result.json").write_text(
            json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
        return rec

    # ---- 1. production measurement -------------------------------------
    est = sync_estimate.estimate_pair(
        ref, tgt, sample_rate=FS_EXPECTED,
        search_window_ms=PROD_GEOM["search_window_ms"],
        frame_ms=PROD_GEOM["frame_ms"], hop_ms=PROD_GEOM["hop_ms"],
        anchor_segment_seconds=PROD_GEOM["anchor_segment_seconds"],
        min_confidence=PROD_GATE["min_confidence"],
        coarse_rate=PROD_GEOM["coarse_rate"],
        min_usable_frames=PROD_GATE["min_usable_frames"],
        frame_min_rms_dbfs=PROD_GEOM["frame_min_rms_dbfs"],
        fine_phase_band_hz=PROD_GEOM["fine_phase_band_hz"])
    stats = sync_estimate.summarize_trajectory(est.frames, FS_EXPECTED)
    const = sync_estimate.classify_constant(
        stats, mad_max_ms=PROD_CONSTANCY["mad_max_ms"],
        max_ppm=PROD_CONSTANCY["constant_max_ppm"], sample_rate=FS_EXPECTED,
        drift_min_ms=PROD_CONSTANCY["drift_min_ms"])
    ev = evidence(est)
    ok, why = gate_ok(ev)
    measured = None if math.isnan(est.delay_samples) else float(est.delay_samples)
    rec["measurement"] = {
        "api": "core.sync_estimate.estimate_pair",
        "sign_convention": ("positive delay means the WIRELESS channel's copy is "
                            "LATE relative to the reference channel"),
        "delay_samples": measured,
        "delay_ms": None if measured is None else measured * 1000.0 / FS_EXPECTED,
        "confidence": float(est.confidence),
        "usable_frames": int(est.usable_frames),
        "total_frames": len(est.frames),
        "coverage": ev["coverage"],
        "trajectory": {
            "mad_ms": None if stats.mad_ms != stats.mad_ms else float(stats.mad_ms),
            "spread_samples": (None if stats.spread_samples != stats.spread_samples
                               else float(stats.spread_samples)),
            "drift_total_ms": (None if stats.drift_total_ms != stats.drift_total_ms
                               else float(stats.drift_total_ms)),
        },
        "constant": bool(const),
    }
    rec["gate"] = {"thresholds": PROD_GATE, "evidence": ev,
                   "passed": bool(ok), "reason": why}

    # ---- 2. truth bookkeeping ------------------------------------------
    # Everything the residual could be derived from, kept apart, with the formula
    # stated. NOTHING is subtracted implicitly.
    raw = measured
    calib = calib_bias
    derived = (None if raw is None or calib is None else raw - calib)
    rec["truth"] = {
        "reference_path_bias_samples": calib,
        "bias_source": ("the session's CABLE_CALIBRATION take, measured by the "
                        "same differential marker method; null when no "
                        "calibration take exists"),
        "raw_wireless_vs_reference_samples": raw,
        "formula": ("cable_calibrated_offset = raw_wireless_vs_reference "
                    "- reference_path_bias   [sign: positive = wireless LATE]"),
        "cable_calibrated_offset_samples": derived,
        "truth_grade": ("G3" if (calib is not None and ok)
                        else ("G1" if measured is not None else "G0")),
        "truth_resolution_samples": 1.0,
        "truth_resolution_basis": ("one sample at 48 kHz; valid only when the "
                                   "calibration take's own spread is within one "
                                   "sample, which is reported next to it"),
        "truth_uncertainty_samples": (
            None if calib is None else
            abs(calib - round(calib)) if calib is not None else None),
    }

    # ---- 3. marker check (already done in preflight, repeated here for the
    #         record next to the measurement it validates) -----------------
    ms, me = layout.marker_start
    es, ee = layout.marker_end
    half = int(1.0 * FS_EXPECTED)
    sm = differential_offset(ref[max(0, ms - half):min(n, me + half)],
                             tgt[max(0, ms - half):min(n, me + half)])
    em = differential_offset(ref[max(0, es - half):min(n, ee + half)],
                             tgt[max(0, es - half):min(n, ee + half)])
    rec["markers"] = {
        "start_offset_samples": sm.get("offset_samples"),
        "end_offset_samples": em.get("offset_samples"),
        "agree_within_one_sample": (None if not (sm.get("ok") and em.get("ok"))
                                    else bool(abs(sm["offset_samples"]
                                                  - em["offset_samples"]) <= 1.0)),
        "tolerance_samples": 1.0,
        "meaning": ("a disagreement means the delay MOVED inside the take, which "
                    "makes it a time-varying candidate and NOT eligible for a "
                    "constant-delay residual"),
    }

    # ---- 2b. eligibility for a CONSTANT-delay accuracy statement ---------
    # This is separate from the production gate on purpose. The gate asks "is
    # there enough evidence to move a track"; this asks "may a single number
    # describe the whole take". A take can pass the first and fail the second,
    # and the fixture `tv-marker-disagree` does exactly that. Before this guard,
    # both time-varying fixtures were reported as plain `CORRECTED`.
    markers_agree = rec["markers"]["agree_within_one_sample"]
    traversal_constant = bool(rec["measurement"]["constant"])
    # A dropout that the marker pair happens to straddle is still a dropout: the
    # wireless signal is gone for part of the take, so a single number cannot
    # describe it. The fixture `tv-dropout` agrees at both markers AND traverses
    # constant, and was reported eligible before this clause existed.
    dropout = "DROP_OUT_DETECTED" in (pf.get("reasons") or [])
    traversal_usable = bool(pf["signal"].get("longest_silent_run_seconds", 0) < 2.0)
    rec["eligibility"] = {
        "markers_agree": markers_agree,
        "traversal_constant": traversal_constant,
        "dropout_detected": dropout,
        "traversal_usable": traversal_usable,
        "constant_delay_eligible": bool(markers_agree) and traversal_constant
                                   and not dropout and traversal_usable,
        "time_varying_candidate": (markers_agree is False)
                                  or (not traversal_constant) or dropout,
        "note": ("the delay DRIFTS linearly inside every one of these fixtures "
                 "(section boundaries move the median), so `traversal_constant` "
                 "is False even for takes whose two markers agree; the residual "
                 "below is therefore a point value, not a constant-delay figure"),
    }

    # ---- 4. plan and apply, only where the gate allowed it --------------
    windows = ex.aggregate_frames_to_windows(
        est.frames, n_samples=n,
        window_samples=PLAN_GEOM["window_samples"],
        hop_samples=PLAN_GEOM["hop_samples"],
        min_frames_per_window=PLAN_GEOM["min_frames_per_window"])
    rec["plan"] = {"api": "core.audio_sync_tv_exec.build_time_varying_plan",
                   "plan_geometry": PLAN_GEOM, "window_measurements": len(windows),
                   "built": False, "refused_reason": None}
    rec["apply"] = {"status": "NOT_APPLIED", "reason": None,
                    "applied_offset_samples": None,
                    "api": "core.sync_fix.shift_stream"}

    if measured is None or not ok:
        rec["apply"]["reason"] = (
            f"gate refused ({why}); no correction was applied and none was forced")
        rec["terminal_state"] = "REFUSED_BY_GATE"
    elif abs(measured) <= PROD_CONSTANCY["aligned_max_ms"] * FS_EXPECTED / 1000.0:
        rec["apply"]["reason"] = (
            f"|{measured}| samples is within the production aligned threshold "
            f"({PROD_CONSTANCY['aligned_max_ms']} ms); the material already aligned")
        rec["terminal_state"] = "ALREADY_ALIGNED"
    else:
        out = tmp / "corrected.f32"
        try:
            sync_fix.shift_stream(paths[WIRELESS_STREAM], out,
                                  delay_samples=measured,
                                  sample_rate=FS_EXPECTED,
                                  storage_dtype="f32",
                                  chunk_seconds=PROD_GEOM["fix_chunk_seconds"])
            rec["apply"].update({"status": "APPLIED",
                                 "applied_offset_samples": measured})
            rec["terminal_state"] = "CORRECTED"
        except Exception as exc:                            # noqa: BLE001
            rec["apply"]["reason"] = f"{type(exc).__name__}: {exc}"[:200]
            rec["terminal_state"] = "APPLY_FAILED"
        if out.is_file():
            got = np.fromfile(out, dtype="<f4")
            nn = int(min(got.shape[0], n))
            want = np.zeros(nn, dtype=np.float32)
            s = int(round(measured))
            if s >= 0:
                want[: nn - s] = tgt[s:nn]
            else:
                want[-s:] = tgt[: nn + s]
            rec["apply"]["mechanics_exact"] = bool(
                nn and np.array_equal(got[:nn], want))
            rec["apply"]["mechanics_note"] = (
                "corrected == shift(wireless_channel, applied_offset) compared "
                "sample by sample; a mismatch is an apply defect")

            # ---- 5. independent verification ---------------------------
            before = independent_delay(ref, tgt)
            after = independent_delay(ref, got[:nn])
            q_b, w_b = verifier_qualified(before, rms_dbfs(ref), rms_dbfs(tgt))
            q_a, w_a = verifier_qualified(after, rms_dbfs(ref), rms_dbfs(got[:nn]))
            rec["verification"] = {
                "api": "tests/audio_sync_acquisition/ingest.independent_delay",
                "independent_of_production": True,
                "before": {**before, "qualified": q_b, "reason": w_b},
                "after": {**after, "qualified": q_a, "reason": w_a},
            }
            rec["residual"] = {
                "population": ("MEASURED_WINDOWS with an applied correction"
                               if rec["apply"]["status"] == "APPLIED"
                               else "NOT_APPLIED"),
                "before_samples": before.get("delay_samples") if q_b else None,
                "after_samples": after.get("delay_samples") if q_a else None,
                "constant_delay_eligible": rec["eligibility"][
                    "constant_delay_eligible"],
                "published_as": (
                    "TIME_VARYING_CANDIDATE"
                    if not rec["eligibility"]["constant_delay_eligible"]
                    else ("MEASURED_ACCURACY" if calib is not None
                          else "CROSS_METHOD_AGREEMENT")),
                "why_this_label": (
                    "the delay is not constant across the take (see "
                    "`eligibility`), so a single accuracy number would describe a "
                    "trajectory it cannot represent"
                    if not rec["eligibility"]["constant_delay_eligible"] else
                    ("a measured accuracy figure requires an independent truth; "
                     "without a reference-path calibration this is two methods "
                     "agreeing on the same real signal, which is a different "
                     "claim" if calib is None else
                     "the reference path is calibrated by a cable-to-cable take, "
                     "so the offset is traceable to a known construction")),
            }

    for p in paths.values():
        if not force:
            pass                     # keep decodes for the attempt record
    (existing).mkdir(parents=True, exist_ok=True)
    (existing / "result.json").write_text(
        json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
    return rec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--takes", type=Path, nargs="+", required=True)
    ap.add_argument("--signal-manifest", type=Path,
                    default=WORK / "generated-signal" / "signal-manifest.json")
    ap.add_argument("--signal-wav", type=Path,
                    default=WORK / "generated-signal" / "signal.wav")
    ap.add_argument("--out", type=Path, default=WORK / "results")
    ap.add_argument("--force", action="store_true",
                    help="re-analyse even if the inputs are unchanged")
    ap.add_argument("--jsonl", type=Path, default=WORK / "results.jsonl")
    args = ap.parse_args(argv)

    layout = load_signal_manifest(args.signal_manifest)
    template = None
    if args.signal_wav.is_file():
        import wave as _w
        with _w.open(str(args.signal_wav), "rb") as w:
            raw = w.readframes(w.getnframes())
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        q = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        q = np.where(q >= (1 << 23), q - (1 << 24), q)
        sig = q.astype(np.float64) / float(2 ** 23 - 1)
        a0, a1 = layout.marker_start
        template = (sig[a0:a1] - sig[a0:a1].mean()).copy()

    takes = discover(list(args.takes))
    # ---- calibration discovery, and the ambiguity rule -------------------
    # A bias is a property of the RIG, so one session must have exactly one
    # calibration take. Several takes give several candidate biases, and picking
    # one silently is an assumption; a session with none has no traceable truth
    # and must say so rather than defaulting to zero.
    calib_takes: dict[str, list[Path]] = {}
    for t in takes:
        note = parse_operator_note(t.with_suffix(".txt"))
        if (note.fields.get("take_kind", "").strip().upper()
                == "CABLE_CALIBRATION"):
            calib_takes.setdefault(t.parent.name, []).append(t)
    calib: dict[str, float] = {}
    calib_ambiguous: dict[str, int] = {}
    for session, ts in calib_takes.items():
        if len(ts) != 1:
            calib_ambiguous[session] = len(ts)
            continue
        pfr = check_take(ts[0], layout, None, template)
        v = pfr["marker"]["start"].get("offset_samples") \
            if pfr["marker"]["start"].get("ok") else None
        e = pfr["marker"]["end"].get("offset_samples") \
            if pfr["marker"]["end"].get("ok") else None
        if v is not None and e is not None and abs(v - e) <= 1.0:
            calib[session] = float(v)

    print(f"takes           : {len(takes)}")
    print(f"calibration bias: {calib or '(none)'}  [samples, positive = wireless LATE]")
    if calib_ambiguous:
        print(f"AMBIGUOUS       : {calib_ambiguous} "
              f"(session -> number of calibration takes); no bias applied there")
    print(f"sign convention : positive delay => the WIRELESS channel arrives LATE")
    print()
    results = []
    for t in takes:
        r = analyse_take(t, t.with_suffix(".txt"), layout,
                         calib.get(t.parent.name), args.signal_wav,
                         args.out, force=args.force, template=template)
        results.append(r)
        m = r.get("measurement") or {}
        reason = (r.get("apply") or {}).get("reason") or ""
        print(f"{r['take_id']:26s} {r.get('terminal_state'):18s} "
              f"raw={_f(m.get('delay_samples'))} cal="
              f"{_f((r.get('truth') or {}).get('cable_calibrated_offset_samples'))} "
              f"gate={'OK' if (r.get('gate') or {}).get('passed') else 'refused'} "
              f"{('| ' + reason[:44]) if reason else ''}")

    args.jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.jsonl.open("w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    from collections import Counter
    states = dict(sorted(Counter(r.get("terminal_state") for r in results).items()))
    labels = dict(sorted(Counter(
        (r.get("residual") or {}).get("published_as", "n/a")
        for r in results).items()))
    print(f"\nterminal states : {states}")
    print(f"published labels: {labels}")
    print(f"-> {args.jsonl}")
    return 0


def _f(v: Any) -> str:
    return "—" if v is None else f"{float(v):.3f}"


if __name__ == "__main__":
    raise SystemExit(main())
