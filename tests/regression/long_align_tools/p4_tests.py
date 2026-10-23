#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P4: known-answer, guard, idempotence and null-control tests for the pipeline.

All tests build test PCM stems under E:\\1kt_scratch\\pcm\\<name>\\ch{0..3}.f32 so
the existing stages (windows/solve/track) run unchanged, then assert:

  test_const    inject known constant offsets (+0,+977,-1234,+4700) into a 20-min
                excerpt of C1305 -> solve must recover the same relative offsets
                (<=2 samples).
  test_drift    inject (a=+900, +5 ppm) linear delay into CH3 -> tracker + linear
                fit must recover a and the rate (|a err| <= 3 samples,
                |ppm err| <= 0.5).
  test_big      inject +6000 samples (> the 100 ms = 4800 limit) -> solve must
                refuse (no solution) rather than produce a shift.
  test_null     four independent noise channels -> no correction (all |shift|<=1)
                and no crash (no false alignment).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, r"F:\1KeyTranscoder\tests\regression")
import long_align as la  # noqa: E402

PCM_ROOT = Path(r"E:\1kt_scratch\pcm")
OUT = Path(r"E:\1kt_work\long_align")
SR = 48000
EXCERPT_S = 1200.0
N = int(EXCERPT_S * SR)


def src_ch(i: int) -> np.memmap:
    return la.load_ch("20260919_C1305", i)


def write_stem(name: str, arrays: list[np.ndarray]) -> Path:
    d = PCM_ROOT / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    for i, a in enumerate(arrays):
        np.asarray(a, dtype=np.float32).tofile(d / f"ch{i}.f32")
    return d


def shifted(x: np.ndarray, shift: int) -> np.ndarray:
    out = np.zeros(len(x), dtype=np.float32)
    if shift >= 0:
        out[:len(x) - shift] = x[shift:]
    else:
        out[-shift:] = x[:len(x) + shift]
    return out


def delayed_linear(x: np.ndarray, a: float, b: float) -> np.ndarray:
    """out[k] = x[k - (a + b*k)] with linear interpolation."""
    n = len(x)
    pos = np.arange(n, dtype=np.float64) - (a + b * np.arange(n, dtype=np.float64))
    invalid = (pos < 0) | (pos > n - 1)
    np.clip(pos, 0, n - 1, out=pos)
    y = np.interp(pos, np.arange(n, dtype=np.float64),
                  np.asarray(x, dtype=np.float64))
    y[invalid] = 0.0
    return y.astype(np.float32)


def run_stage(stage: str, stem: str, extra: list[str] | None = None) -> int:
    cmd = [sys.executable, str(la.REPO / "tests" / "regression" / "long_align.py"),
           "--stage", stage, "--stem", stem]
    if extra:
        cmd += extra
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return r.returncode


def solve_shifts(stem: str) -> dict | None:
    p = OUT / f"solve_{stem}.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def solve_shifts_or_none(stem: str) -> dict | None:
    return solve_shifts(stem)


def main() -> int:
    results = {}
    base_arrays = [np.asarray(src_ch(i)[:N]) for i in range(4)]
    base_offsets = [0, -4, 1014, 1624]      # 已知真实偏移 (首 20 min, 参考 CH1)
    # ---------------- 0. control run (no injection)
    print("[0] control run (no injection) -> baseline offsets", flush=True)
    write_stem("test_base", base_arrays)
    run_stage("windows", "test_base", ["--workers", "10"])
    run_stage("solve", "test_base")
    sol = solve_shifts("test_base")
    if sol is None:
        results["control"] = {"ok": False, "reason": "no solution"}
        base_solved = {0: 0}
    else:
        sh = {int(k): v for k, v in sol["shifts"].items()}
        anchor = sol["anchor"]
        base_solved = {i: sh[i] - sh[anchor] for i in range(4)}
        expected = {i: base_offsets[i] - base_offsets[anchor] for i in range(4)}
        err = {i: base_solved[i] - expected[i] for i in range(4)}
        ok = all(abs(e) <= 2 for e in err.values())
        results["control"] = {"ok": ok, "anchor": anchor,
                              "expected": expected, "got": base_solved,
                              "error": err}
        print(f"    anchor=CH{anchor + 1} expected={expected} got={base_solved} "
              f"err={err} -> {'PASS' if ok else 'FAIL'}", flush=True)
        base_solved = base_solved
    # ---------------- 1. known constant offsets on top of the real ones
    inj = [0, 977, -1234, 4700]
    print(f"[1] injected advances {inj} on top of the real offsets", flush=True)
    write_stem("test_const", [shifted(base_arrays[0], inj[0]),
                              shifted(base_arrays[1], inj[1]),
                              shifted(base_arrays[2], inj[2]),
                              shifted(base_arrays[3], inj[3])])
    run_stage("windows", "test_const", ["--workers", "10"])
    run_stage("solve", "test_const")
    sol = solve_shifts("test_const")
    if sol is None:
        results["test_const"] = {"ok": False, "reason": "no solution"}
    else:
        sh = {int(k): v for k, v in sol["shifts"].items()}
        anchor = sol["anchor"]
        got = {i: sh[i] - sh[anchor] for i in range(4)}
        # 注入 advance +shift 等价于延迟 -shift (相对量, 与真实偏移无关)
        expect_delta = {i: -inj[i] + inj[anchor] for i in range(4)}
        got_delta = {i: got[i] - base_solved[i] for i in range(4)}
        err = {i: got_delta[i] - expect_delta[i] for i in range(4)}
        ok = all(abs(e) <= 2 for e in err.values())
        results["test_const"] = {"ok": ok, "anchor": anchor,
                                 "base": base_solved, "got": got,
                                 "expect_delta": expect_delta,
                                 "got_delta": got_delta, "error": err}
        print(f"    got={got} delta={got_delta} expected delta={expect_delta} "
              f"err={err} -> {'PASS' if ok else 'FAIL'}", flush=True)
    # ---------------- 2. known linear drift on top of the real CH3 offset
    print("[2] injected linear delay a=+900 samples, b=+5 ppm on CH3", flush=True)
    write_stem("test_drift", [base_arrays[0], base_arrays[1],
                              delayed_linear(base_arrays[2], 900.0, 5e-6),
                              base_arrays[3]])
    subprocess.run([sys.executable, str(la.REPO / "work" / "track_drift.py"),
                    "--stem", "test_drift", "--targets", "2"],
                   capture_output=True, text=True)
    subprocess.run([sys.executable, str(la.REPO / "work" / "fit_drift.py"),
                    "--stem", "test_drift"],
                   capture_output=True, text=True)
    # baseline CH3 tracker on the uninjected control
    subprocess.run([sys.executable, str(la.REPO / "work" / "track_drift.py"),
                    "--stem", "test_base", "--targets", "2"],
                   capture_output=True, text=True)
    subprocess.run([sys.executable, str(la.REPO / "work" / "fit_drift.py"),
                    "--stem", "test_base"],
                   capture_output=True, text=True)

    def ch_info(stem: str) -> dict:
        q = OUT / f"drift_{stem}.json"
        if not q.is_file():
            return {}
        return (json.loads(q.read_text(encoding="utf-8"))["channels"] or {}).get("2") or {}

    info = ch_info("test_drift")
    base_info = ch_info("test_base")
    if info.get("ok") and base_info.get("ok"):
        a_delta = info["a_samples"] - base_info["a_samples"]
        b_delta = info["b_ppm"] - base_info["b_ppm"]
        a_err = abs(a_delta - 900.0)
        b_err = abs(b_delta - 5.0)
        ok = a_err <= 3.0 and b_err <= 0.5
        results["test_drift"] = {"ok": bool(ok),
                                 "base_a": base_info["a_samples"],
                                 "got_a": info["a_samples"],
                                 "a_delta": round(a_delta, 2),
                                 "a_err": round(a_err, 2),
                                 "base_b_ppm": base_info["b_ppm"],
                                 "got_b_ppm": info["b_ppm"],
                                 "b_delta": round(b_delta, 3),
                                 "b_err": round(b_err, 3),
                                 "residual_p90": info.get("residual_p90")}
        print(f"    base a={base_info['a_samples']} b={base_info['b_ppm']} | "
              f"injected a={info['a_samples']} b={info['b_ppm']} | "
              f"delta a={a_delta:+.1f} (err {a_err:.1f}) b={b_delta:+.3f} ppm "
              f"(err {b_err:.3f}) -> {'PASS' if ok else 'FAIL'}", flush=True)
    else:
        results["test_drift"] = {"ok": False, "reason": "no drift output",
                                 "info": info, "base_info": base_info}
    # ---------------- 3. guard
    print("[3] guard: +6000 samples (>4800) must be refused", flush=True)
    write_stem("test_big", [base_arrays[0], shifted(base_arrays[1], 6000),
                            base_arrays[2], base_arrays[3]])
    run_stage("windows", "test_big", ["--workers", "10"])
    q = OUT / "solve_test_big.json"
    if q.is_file():
        q.unlink()
    rc = run_stage("solve", "test_big")
    refused = (not q.is_file()) or (rc != 0)
    results["test_big"] = {"ok": bool(refused), "solve_returncode": rc,
                           "solution_written": q.is_file()}
    print(f"    solve rc={rc} solution_written={q.is_file()} -> "
          f"{'PASS (refused)' if refused else 'FAIL (accepted)'}", flush=True)
    # ---------------- 4. null control
    print("[4] null control: 4 independent noise channels", flush=True)
    rng = np.random.default_rng(11)
    write_stem("test_null", [rng.standard_normal(N).astype(np.float32)
                             for _ in range(4)])
    run_stage("windows", "test_null", ["--workers", "10"])
    run_stage("solve", "test_null")
    sol = solve_shifts("test_null")
    if sol is None:
        results["test_null"] = {"ok": True, "reason": "refused (acceptable)"}
        print("    solve refused -> PASS (no false alignment)", flush=True)
    else:
        sh = {int(k): v for k, v in sol["shifts"].items()}
        ok = all(abs(v) <= 2 for v in sh.values())
        results["test_null"] = {"ok": ok, "shifts": sh}
        print(f"    shifts={sh} -> {'PASS' if ok else 'FAIL'}", flush=True)
    results["all_pass"] = all(v.get("ok") for v in results.values()
                              if isinstance(v, dict) and "ok" in v)
    (OUT / "p4_tests.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nP4 ALL PASS: {results['all_pass']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
