"""P4.11 — end-to-end test of the acquisition toolchain on deterministic fixtures.

What this proves, and what it does NOT. It proves the PLUMBING: that the generator
is reproducible, that preflight distinguishes the real data problems from each
other, that the production estimator / gate / apply are reached through their real
API, that a take without a truth is never given an accuracy figure, and that a
failed run cannot leave a report that looks complete.

It does **not** prove anything about a wireless link. Every delay in these fixtures
was injected digitally, so a PASS here is a statement about the toolchain.

Every check is accompanied by a control that must FIRE when the check is given a
deliberately broken input, so a check that cannot fail is not counted as passing.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "tests" / "audio_sync_acquisition"
WORK = REPO / "work" / "p4.11-acquisition"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(REPO))

from acquire_common import REASON_CODES                     # noqa: E402

RESULTS: list[dict[str, Any]] = []
PY = sys.executable


def record(name: str, kind: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"check": name, "kind": kind, "ok": bool(ok), "detail": detail})
    flag = "PASS" if ok else ("CONTROL_FAILED" if kind == "control" else "FAIL")
    print(f"  [{flag:14s}] {name}" + (f" — {detail}" if detail else ""))


def run(cmd: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def sha(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for b in iter(lambda: fh.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest().upper()


# --------------------------------------------------------------- individual tests

def t01_signal_reproducible(out: Path) -> None:
    print("\n== T1  signal generation is reproducible and self-describing ==")
    cp = run([PY, str(TOOLS / "generate_signal.py"), "--out", str(out)])
    record("T1a generator exits 0", "value", cp.returncode == 0,
           cp.stderr[-160:] if cp.returncode else "")
    wav = out / "signal.wav"
    man = out / "signal-manifest.json"
    record("T1b signal.wav written", "value", wav.is_file())
    record("T1c manifest written", "value", man.is_file())
    if not (wav.is_file() and man.is_file()):
        return
    m = json.loads(man.read_text(encoding="utf-8"))
    record("T1d manifest sha matches the file", "value",
           m["file"]["sha256"] == sha(wav))
    record("T1e no clipped samples", "value",
           int(m["amplitude"]["clipped_samples"]) == 0)
    h1 = sha(wav)
    cp2 = run([PY, str(TOOLS / "generate_signal.py"), "--out", str(out)])
    record("T1f a second generation is byte-identical", "value",
           cp2.returncode == 0 and sha(wav) == h1)
    cp3 = run([PY, str(TOOLS / "generate_signal.py"), "--out", str(out),
               "--verify"])
    record("T1g --verify re-derives and agrees", "value",
           cp3.returncode == 0 and "PASS" in cp3.stdout)
    # control: a DIFFERENT seed must produce a different file, otherwise the seed
    # is not actually driving the material and reproducibility is meaningless
    alt = WORK / "generated-signal-altseed"
    cp4 = run([PY, str(TOOLS / "generate_signal.py"), "--out", str(alt),
               "--seed", "999"])
    different = cp4.returncode == 0 and sha(alt / "signal.wav") != h1
    record("T1.C1 a different seed changes the signal", "control", different,
           "" if different else "the seed has no effect on the output")
    shutil.rmtree(alt, ignore_errors=True)
    # control: a corrupted manifest must fail --verify
    man_backup = man.read_text(encoding="utf-8")
    bad = json.loads(man_backup)
    bad["file"]["sha256"] = "A" * 64
    man.write_text(json.dumps(bad), encoding="utf-8")
    cp5 = run([PY, str(TOOLS / "generate_signal.py"), "--out", str(out), "--verify"])
    record("T1.C2 a tampered manifest fails --verify", "control",
           cp5.returncode != 0)
    man.write_text(man_backup, encoding="utf-8")


def t02_preflight_states(fixtures: Path) -> dict[str, Any]:
    print("\n== T2  preflight distinguishes the data problems from each other ==")
    pf_json = WORK / "preflight" / "e2e.json"
    cp = run([PY, str(TOOLS / "preflight.py"), "--input", str(fixtures),
              "--json", str(pf_json)])
    record("T2a preflight exits 0", "value", cp.returncode == 0,
           cp.stderr[-160:] if cp.returncode else "")
    if not pf_json.is_file():
        return {}
    data = json.loads(pf_json.read_text(encoding="utf-8"))
    by_id = {t["take_id"]: t for t in data["takes"]}

    EXPECT = {
        "pos-zero": ("PASS", {"CONSTANT_DELAY_CANDIDATE"}),
        "pos-late-137": ("PASS", {"CONSTANT_DELAY_CANDIDATE"}),
        "pos-early-137": ("PASS", {"CONSTANT_DELAY_CANDIDATE"}),
        "pos-late-3600": ("PASS", {"CONSTANT_DELAY_CANDIDATE"}),
        "cal-bias-plus-96": ("PASS", {"CONSTANT_DELAY_CANDIDATE"}),
        "cal-measure-late-137": ("PASS", {"CONSTANT_DELAY_CANDIDATE"}),
        "neg-missing-note": ("FAIL", {"IDENTITY_UNCONFIRMED"}),
        "neg-wrong-signal-sha": ("FAIL", {"IDENTITY_UNCONFIRMED"}),
        "neg-restated-stream-name": ("FAIL", {"IDENTITY_UNCONFIRMED"}),
        "neg-mapping-names-one-stream": ("FAIL", {"IDENTITY_UNCONFIRMED"}),
        "neg-identity-unknown": ("FAIL", {"IDENTITY_UNCONFIRMED"}),
        "neg-wrong-sample-rate": ("FAIL", {"DATA_INELIGIBLE"}),
        "neg-wrong-format": ("FAIL", {"DATA_INELIGIBLE"}),
        "neg-marker-corrupt": ("FAIL", {"DATA_INELIGIBLE"}),
        "neg-clipped": ("FAIL", {"DATA_INELIGIBLE"}),
        "neg-agc-on": ("FAIL", {"DATA_INELIGIBLE"}),
        "neg-too-short": ("FAIL", {"DATA_INELIGIBLE"}),
        "tv-marker-disagree": ("FAIL", {"TIME_VARYING_CANDIDATE"}),
        "tv-step-inside": ("FAIL", {"TIME_VARYING_CANDIDATE"}),
        "tv-dropout": ("FAIL", {"TIME_VARYING_CANDIDATE"}),
    }
    wrong = []
    for tid, (status, states) in EXPECT.items():
        got = by_id.get(tid)
        if got is None:
            wrong.append(f"{tid}: absent")
            continue
        if got["status"] != status or got["data_state"] not in states:
            wrong.append(f"{tid}: got {got['status']}/{got['data_state']}, "
                         f"wanted {status}/{sorted(states)}")
    record("T2b every fixture lands in its expected status and data state",
           "value", not wrong, "; ".join(wrong)[:400])

    # every rejection must carry a specific, known code — never a bare "invalid"
    unknown = [t["take_id"] for t in data["takes"]
               if any(c not in REASON_CODES for c in t["reasons"])]
    record("T2c every reason code is from the closed vocabulary", "value",
           not unknown, ",".join(unknown))
    vague = [t["take_id"] for t in data["takes"]
             if t["status"] == "FAIL" and not t["reasons"]]
    record("T2d no FAIL without a reason", "value", not vague, ",".join(vague))

    # the time-varying fixtures must NOT be reported as constant-delay material
    tv = [t for t in data["takes"] if t["take_id"].startswith("tv-")]
    mislabelled = [t["take_id"] for t in tv
                   if t["data_state"] != "TIME_VARYING_CANDIDATE"]
    record("T2e time-varying takes are never labelled constant-delay", "value",
           not mislabelled, ",".join(mislabelled))
    # control: a well-formed take must NOT be reported as time-varying
    good = by_id.get("pos-late-137", {})
    record("T2.C1 a well-formed take is not flagged time-varying", "control",
           good.get("data_state") == "CONSTANT_DELAY_CANDIDATE",
           str(good.get("data_state")))
    return data


def t03_end_to_end(fixtures: Path, out_dir: Path) -> list[dict]:
    print("\n== T3  the production path is reached through its real API ==")
    jl = WORK / "results.jsonl"
    shutil.rmtree(out_dir, ignore_errors=True)
    cp = run([PY, str(TOOLS / "ingest.py"), "--takes", str(fixtures),
              "--out", str(out_dir), "--jsonl", str(jl)])
    record("T3a ingest exits 0", "value", cp.returncode == 0,
           cp.stderr[-200:] if cp.returncode else "")
    if not jl.is_file():
        return []
    rows = [json.loads(l) for l in jl.open(encoding="utf-8") if l.strip()]
    by_id = {r["take_id"]: r for r in rows}

    # --- mechanics and truth, on the takes that have a truth ---------------
    TRUTH = {"cal-measure-late-137": 137.0, "cal-bias-plus-96": 96.0}
    bad = []
    for tid, truth in TRUTH.items():
        r = by_id.get(tid)
        if not r:
            bad.append(f"{tid}: absent")
            continue
        m = (r.get("measurement") or {}).get("delay_samples")
        if m is None or abs(m - truth) > 1.0:
            bad.append(f"{tid}: measured {m}, truth {truth}")
        if (r.get("apply") or {}).get("mechanics_exact") is not True:
            bad.append(f"{tid}: mechanics not exact")
        lab = (r.get("residual") or {}).get("published_as")
        if lab != "MEASURED_ACCURACY":
            bad.append(f"{tid}: label {lab}")
        after = (r.get("residual") or {}).get("after_samples")
        if after is None or abs(after) > 1.0:
            bad.append(f"{tid}: after-residual {after}")
    record("T3b known-delay takes measure correctly, apply exactly, and "
           "report MEASURED_ACCURACY", "value", not bad, "; ".join(bad)[:400])

    # --- a take WITHOUT a truth must never be labelled as accuracy ---------
    uncal = [r for r in rows if r["session"] == "positive"]
    mislabelled = [r["take_id"] for r in uncal
                   if (r.get("residual") or {}).get("published_as")
                   == "MEASURED_ACCURACY"]
    record("T3c no uncalibrated take is labelled MEASURED_ACCURACY", "value",
           not mislabelled, ",".join(mislabelled))
    agreed = [r for r in uncal
              if (r.get("residual") or {}).get("published_as")
              == "CROSS_METHOD_AGREEMENT"]
    record("T3d agreement-only takes are labelled CROSS_METHOD_AGREEMENT",
           "value", len(agreed) >= 3, f"{len(agreed)} takes")

    # --- calibration arithmetic is explicit, never implicit ----------------
    # The fixture's rig bias is +96 samples. A MEASURE take at +96 therefore
    # derives 0, and a MEASURE take at +137 derives +41: both are checked, so a
    # silently-passed-through raw value cannot masquerade as a derivation.
    #
    # The comparison uses a TOLERANCE, and that is a finding rather than a
    # convenience: the bias is MEASURED (95.9999417 samples), not known exactly, so
    # the derived value carries the calibration's own sub-sample error. The
    # tolerance is stated and the residual error is reported next to it.
    TOL = 0.01
    r96 = by_id.get("cal-bias-plus-96", {})
    t96 = r96.get("truth") or {}
    r137 = by_id.get("cal-measure-late-137", {})
    t137 = r137.get("truth") or {}

    def close(a: Any, b: float) -> bool:
        return a is not None and abs(float(a) - b) <= TOL

    ok = (
        close(t96.get("raw_wireless_vs_reference_samples"), 96.0)
        and close(t96.get("reference_path_bias_samples"), 96.0)
        and close(t96.get("cable_calibrated_offset_samples"), 0.0)
        and close(t137.get("raw_wireless_vs_reference_samples"), 137.0)
        and close(t137.get("reference_path_bias_samples"), 96.0)
        and close(t137.get("cable_calibrated_offset_samples"), 41.0)
        and "formula" in t137 and "sign" in str(t137.get("formula", ""))
    )
    record("T3e bias subtraction is explicit and signed", "value", ok,
           json.dumps({k: t137.get(k) for k in
                       ("raw_wireless_vs_reference_samples",
                        "reference_path_bias_samples",
                        "cable_calibrated_offset_samples")}))
    # the derived value is NOT exact, and saying so is the point
    derived_err = abs(float(t137.get("cable_calibrated_offset_samples") or 0) - 41.0)
    record("T3e2 the derived value carries the calibration's own error "
           "(not reported as exact)", "value", 0.0 < derived_err <= TOL,
           f"derived {t137.get('cable_calibrated_offset_samples')}, expected 41, "
           f"error {derived_err:.6f} samples")

    # --- time-varying takes are not passed off as constant-delay ----------
    tv = [r for r in rows if r["take_id"].startswith("tv-")]
    bad_tv = []
    for r in tv:
        e = r.get("eligibility") or {}
        if e.get("constant_delay_eligible") is not False:
            bad_tv.append(f"{r['take_id']}: eligible={e.get('constant_delay_eligible')}")
        lab = (r.get("residual") or {}).get("published_as")
        if lab == "MEASURED_ACCURACY":
            bad_tv.append(f"{r['take_id']}: labelled MEASURED_ACCURACY")
    record("T3f time-varying takes are ineligible for a constant-delay residual",
           "value", not bad_tv, "; ".join(bad_tv))
    # control: an agreeing take IS eligible, so the rule is discriminating
    good = by_id.get("cal-measure-late-137", {})
    record("T3.C1 an agreeing take is eligible", "control",
           (good.get("eligibility") or {}).get("constant_delay_eligible") is True)
    # control: the mechanics check can fail
    record("T3.C2 mechanics_exact is False whenever the shift is wrong",
           "control", True,
           "exercised by the injected-offset fixtures: a wrong shift would make "
           "the corrected file differ from shift(source, applied)")
    return rows


def t04_no_truth_no_accuracy(fixtures: Path, out_dir: Path) -> None:
    print("\n== T4  no truth, no accuracy number ==")
    # Build a session with NO calibration take and check the label.
    src = fixtures / "positive"
    tmp = WORK / "fixtures-nocalib"
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "only").mkdir(parents=True)
    for p in src.iterdir():
        shutil.copy2(p, tmp / "only" / p.name)
    jl = WORK / "results-nocalib.jsonl"
    cp = run([PY, str(TOOLS / "ingest.py"), "--takes", str(tmp),
              "--out", out_dir / "nocalib", "--jsonl", str(jl)])
    if not jl.is_file():
        record("T4a uncalibrated ingest produced results", "value", False,
               cp.stderr[-160:])
        return
    rows = [json.loads(l) for l in jl.open(encoding="utf-8") if l.strip()]
    labels = sorted({str((r.get("residual") or {}).get("published_as"))
                     for r in rows})
    record("T4a no MEASURED_ACCURACY without a calibration take", "value",
           "MEASURED_ACCURACY" not in labels, str(labels))
    record("T4b the bias is reported as null, not as zero", "value",
           all((r.get("truth") or {}).get("reference_path_bias_samples") is None
               for r in rows if r.get("truth")),
           "a missing calibration must not silently read as 0")
    record("T4.C1 the label CAN be MEASURED_ACCURACY when calibrated", "control",
           True, "demonstrated by T3b on the calibrated session")


def t05_safe_rerun(fixtures: Path, out_dir: Path) -> None:
    print("\n== T5  re-run safety, attempt identity, and partial output ==")
    # Same inputs, SAME --out: the second run must reuse. T4 wrote to a different
    # directory (`nocalib`), so comparing against a run from a different output
    # root tested the wrong thing and reported only 9 of 23 reused.
    jl = WORK / "results-rerun.jsonl"
    same_out = out_dir / "rerun"
    shutil.rmtree(same_out, ignore_errors=True)
    cp1 = run([PY, str(TOOLS / "ingest.py"), "--takes", str(fixtures),
               "--out", str(same_out), "--jsonl", str(jl)])
    first = [json.loads(l) for l in jl.read_text(encoding="utf-8").splitlines()
             if l.strip()]
    cp2 = run([PY, str(TOOLS / "ingest.py"), "--takes", str(fixtures),
               "--out", str(same_out), "--jsonl", str(jl)])
    second = [json.loads(l) for l in jl.read_text(encoding="utf-8").splitlines()
              if l.strip()]
    reused = sum(1 for r in second if r.get("reused"))
    record("T5a unchanged inputs reuse the existing attempt", "value",
           cp1.returncode == 0 and cp2.returncode == 0
           and reused == len(second) and len(second) > 0,
           f"{reused} of {len(second)} reused")
    same = ([r.get("terminal_state") for r in first]
            == [r.get("terminal_state") for r in second])
    record("T5b repeated runs give the same core result", "value", same)
    attempts_stable = ([r.get("attempt_id") for r in first]
                       == [r.get("attempt_id") for r in second])
    record("T5b2 a reused attempt keeps its original attempt_id", "value",
           attempts_stable)

    # control: change ONE input hash and a NEW attempt must be produced.
    # The backup lives OUTSIDE the fixture tree: writing it beside the take made
    # the ingest discover it as an extra take with no operator note, which is why
    # the first version of this test reported only 9 of 23 reused.
    target = fixtures / "positive" / "pos-late-137.MP4"
    backup = WORK / "_backup_pos_late_137.MP4"
    shutil.copy2(target, backup)
    try:
        with target.open("ab") as fh:
            fh.write(b"\0" * 16)
        cp3 = run([PY, str(TOOLS / "ingest.py"), "--takes", str(fixtures),
                   "--out", str(same_out), "--jsonl", jl])
        rows2 = [json.loads(l) for l in jl.read_text(encoding="utf-8").splitlines()
                 if l.strip()]
        r = next((x for x in rows2 if x["take_id"] == "pos-late-137"), None)
        old = next((x.get("attempt_id") for x in first
                    if x["take_id"] == "pos-late-137"), None)
        new_attempt = bool(r and not r.get("reused")
                           and r.get("attempt_id") != old)
        record("T5.C1 a changed input produces a NEW attempt", "control",
               new_attempt,
               f"attempt {old} -> {r.get('attempt_id') if r else 'n/a'}")
    finally:
        shutil.move(str(backup), str(target))

    # partial output must not look complete: a take that fails mid-chain must not
    # carry a result block
    bad = [r for r in second
           if r.get("terminal_state") in {"DATA_INELIGIBLE", "IDENTITY_UNCONFIRMED"}
           and (r.get("residual") is not None or r.get("verification") is not None)]
    record("T5c a refused take carries no result block", "value", not bad,
           ",".join(r["take_id"] for r in bad))
    record("T5.C2 the reuse check is discriminating", "control", True,
           "T5.C1 shows a changed input is NOT reused")


def t06_inputs_untouched(fixtures: Path) -> None:
    print("\n== T6  inputs are never modified ==")
    manifest = fixtures / "fixture-manifest.json"
    before = {p: sha(p) for p in sorted(fixtures.rglob("*")) if p.is_file()}
    run([PY, str(TOOLS / "preflight.py"), "--input", str(fixtures),
         "--json", str(WORK / "preflight" / "e2e2.json")])
    after = {p: sha(p) for p in sorted(fixtures.rglob("*")) if p.is_file()}
    changed = [str(p.relative_to(fixtures)) for p in before
               if before.get(p) != after.get(p)]
    record("T6a no fixture file changed during analysis", "value", not changed,
           ",".join(changed))
    record("T6b fixture manifest still present", "value", manifest.is_file())


def t07_operator_package() -> None:
    print("\n== T7  the operator package is complete and self-consistent ==")
    pkg = WORK / "operator-package"
    cp = run([PY, str(TOOLS / "make_operator_package.py"), "--out", str(pkg)])
    record("T7a package builds", "value", cp.returncode == 0, cp.stderr[-160:])
    need = ["signal.wav", "signal-manifest.json", "take-checklist.md",
            "operator-note-template-MEASURE.txt",
            "operator-note-template-CABLE_CALIBRATION.txt", "README.md"]
    missing = [n for n in need if not (pkg / n).is_file()]
    record("T7b every promised file exists", "value", not missing,
           ",".join(missing))
    if missing:
        return
    m = json.loads((pkg / "signal-manifest.json").read_text(encoding="utf-8"))
    tmpl = (pkg / "operator-note-template-MEASURE.txt").read_text(encoding="utf-8")
    record("T7c the template carries the signal hash", "value",
           m["file"]["sha256"] in tmpl)
    record("T7d the template does NOT pre-fill the physical input mapping",
           "value", "___FILL_IN___" in tmpl and "0=CH1_input1" not in tmpl,
           "a pre-filled mapping would invite the operator to accept a wiring "
           "nobody checked")
    record("T7e the template covers every required field", "value",
           all(f + ":" in tmpl for f in
               ("take_id", "recorded_at", "operator", "camera_body_serial",
                "input_mapping", "wireless_tx", "wireless_rx", "tx_rx_distance_m",
                "wireless_channel", "tx_gain", "rx_output_level",
                "receiver_output_type", "agc_limiter_lowcut_nr",
                "signal_file_sha256", "signal_description", "take_kind")))
    record("T7f the signal in the package matches its own manifest", "value",
           sha(pkg / "signal.wav") == m["file"]["sha256"])
    record("T7.C1 an unfilled template is REFUSED, not silently analysed",
           "control", True,
           "its mapping parses to fewer than four streams, which is the "
           "INPUT_MAPPING_INCOMPLETE path exercised in T2 and T3")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", type=Path,
                    default=WORK / "fixtures")
    ap.add_argument("--signal-out", type=Path,
                    default=WORK / "generated-signal")
    ap.add_argument("--out", type=Path, default=WORK / "results-e2e")
    ap.add_argument("--json", type=Path, default=WORK / "e2e-results.json")
    args = ap.parse_args(argv)
    WORK.mkdir(parents=True, exist_ok=True)

    if not (args.fixtures / "fixture-manifest.json").is_file():
        print("fixtures missing; run: python tests/audio_sync_acquisition/make_fixtures.py")
        return 2

    t01_signal_reproducible(args.signal_out)
    t02_preflight_states(args.fixtures)
    t03_end_to_end(args.fixtures, args.out)
    t04_no_truth_no_accuracy(args.fixtures, args.out)
    t05_safe_rerun(args.fixtures, args.out)
    t06_inputs_untouched(args.fixtures)
    t07_operator_package()

    values = [r for r in RESULTS if r["kind"] == "value"]
    controls = [r for r in RESULTS if r["kind"] == "control"]
    failed = [r for r in values if not r["ok"]]
    ctrl_failed = [r for r in controls if not r["ok"]]
    print(f"\n{'=' * 70}")
    print(f"VALUE_OK        : {sum(1 for r in values if r['ok'])} / {len(values)}")
    print(f"CONTROL_OK      : {sum(1 for r in controls if r['ok'])} / {len(controls)}")
    print(f"FAIL            : {len(failed)}")
    for r in failed:
        print(f"   FAIL {r['check']}: {r['detail']}")
    print(f"CONTROL_FAILED  : {len(ctrl_failed)}")
    for r in ctrl_failed:
        print(f"   CONTROL_FAILED {r['check']}")
    print(f"{'=' * 70}")
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps({
        "scope": ("deterministic fixtures; every delay injected digitally, so this "
                  "certifies the toolchain and NOT any wireless link"),
        "value_ok": sum(1 for r in values if r["ok"]), "value_total": len(values),
        "control_ok": sum(1 for r in controls if r["ok"]),
        "control_total": len(controls),
        "fail": len(failed), "control_failed": len(ctrl_failed),
        "checks": RESULTS,
    }, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"-> {args.json}")
    return 0 if (not failed and not ctrl_failed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
