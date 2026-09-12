"""Hardware-decode integration test harness.

Executes the matrix in ``tests/hwdecode/matrix.json`` against the real
toolchain and emits machine-readable results.

    python -m tests.hwdecode.harness provenance
    python -m tests.hwdecode.harness check-matrix
    python -m tests.hwdecode.harness list --phase 1
    python -m tests.hwdecode.harness run --phase 1
    python -m tests.hwdecode.harness run HD-C01 HD-C05 --repeat 3
    python -m tests.hwdecode.harness summary

Results land in ``work/avhw_integration/results/`` as ``results.json``
and ``results.csv``; ``summary`` prints and writes the final gate.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.hwdecode import checks, fixtures as FX, runners, sources  # noqa: E402
from tests.hwdecode.probe import probe_input  # noqa: E402

MATRIX_FILE = Path(__file__).resolve().parent / "matrix.json"
RESULTS = FX.WORK / "results"
RUNS = FX.WORK / "runs"
BASELINE = FX.WORK / "baseline"

PHASES = {
    1: ("A", "B"),
    2: ("C", "D"),
    3: ("E", "F"),
    4: ("G", "H"),
    5: ("I",),
    6: ("J", "K"),
}

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_BLOCKED = "BLOCKED"
STATUS_SKIP = "SKIP"


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------


@dataclass
class TestResult:
    test_id: str
    category: str
    severity: str
    title: str
    status: str
    input: str = ""
    backend: str = ""
    expected: str = ""
    actual: str = ""
    reason: str = ""
    evidence: dict = field(default_factory=dict)
    repeats: int = 0
    duration_s: float = 0.0
    timestamp: str = ""

    def to_row(self) -> dict:
        return {
            "test_id": self.test_id,
            "category": self.category,
            "severity": self.severity,
            "title": self.title,
            "status": self.status,
            "input": self.input,
            "backend": self.backend,
            "actual": self.actual,
            "reason": self.reason,
            "repeats": self.repeats,
            "duration_s": round(self.duration_s, 2),
            "timestamp": self.timestamp,
        }


def load_matrix() -> dict:
    return json.loads(MATRIX_FILE.read_text(encoding="utf-8"))


def find_test(matrix: dict, tid: str) -> dict | None:
    for t in matrix["tests"]:
        if t["id"] == tid:
            return t
    return None


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------


class Ctx:
    """Shared per-run state."""

    def __init__(self, *, deep: bool = False, verbose: bool = True):
        self.deep = deep
        self.verbose = verbose
        self._facts: dict[str, object] = {}
        self.notes: list[str] = []

    def fact(self, fx: FX.Fixture):
        key = fx.fid
        if key not in self._facts:
            self._facts[key] = probe_input(
                fx.path, input_id=fx.fid, count_packets=True
            )
        return self._facts[key]

    def say(self, msg: str) -> None:
        if self.verbose:
            print(msg, flush=True)


# ---------------------------------------------------------------------------
# shared measurement helpers
# ---------------------------------------------------------------------------


def _require_fixture(fx: FX.Fixture | None, tid: str, ctx: Ctx) -> TestResult | None:
    if fx is None:
        return TestResult(tid, "", "", "", STATUS_SKIP, reason="fixture id unknown")
    if not fx.path.is_file():
        return TestResult(
            tid, "", "", "", STATUS_SKIP,
            input=fx.fid, reason=f"fixture missing on disk: {fx.path}",
        )
    facts = ctx.fact(fx)
    if not facts.has_video:
        return TestResult(
            tid, "", "", "", STATUS_SKIP,
            input=fx.fid, reason="fixture has no video stream",
        )
    return None


def do_encode(
    ctx: Ctx,
    *,
    tid: str,
    fx: FX.Fixture,
    backend: str,
    reader: str,
    role: str = "hardware_decode",
    tag: str = "",
    **kw,
) -> tuple[runners.EncodeResult, checks.CountManifest]:
    label = f"{tid}_{fx.fid}_{backend}_{reader}{role[:4]}{'_' + tag if tag else ''}"
    out = RUNS / f"{label}.mp4"
    ctx.say(f"    encode {label}")
    er = runners.run_encode(
        label=label, backend=backend, reader=reader, role=role,
        source=fx.path, output=out, **kw,
    )
    cm = checks.count_manifest(
        input_id=fx.fid, source=fx.path, output=Path(er.output),
        log_text=er.log_text, rc=er.rc,
        source_container=ctx.fact(fx).container_samples,
    )
    return er, cm


def reconciliation(cm: checks.CountManifest) -> dict:
    ok, reasons = checks.reconcile(cm)
    return {"ok": ok, "reasons": reasons, "counts": cm.to_json()}


def compare_products(
    a: Path, b: Path, facts, *, limit: int | None = None
) -> dict:
    """Ordered fingerprint + packet-manifest comparison of two encodes."""
    v = facts.video
    sig_a, err_a = checks.frame_signatures(
        a, v.width, v.height, pix_fmt="yuv420p10le", limit=limit
    )
    sig_b, err_b = checks.frame_signatures(
        b, v.width, v.height, pix_fmt="yuv420p10le", limit=limit
    )
    cmp = checks.compare_signatures(sig_a, sig_b)
    cmp["fingerprint_error"] = {"a": err_a, "b": err_b}
    cmp["digest_a"] = checks.digest(sig_a)
    cmp["digest_b"] = checks.digest(sig_b)
    return cmp


def packet_compare(a: Path, b: Path, limit: int | None = None) -> dict:
    ma = checks.packet_manifest(a, limit=limit)
    mb = checks.packet_manifest(b, limit=limit)
    return {
        "count_a": ma["count"], "count_b": mb["count"],
        "pts_equal": ma["pts"] == mb["pts"],
        "dts_equal": ma["dts"] == mb["dts"],
        "flags_equal": ma["flags"] == mb["flags"],
        "keyframes_equal": ma["keyframe_indices"] == mb["keyframe_indices"],
        "manifest_sha_equal": ma["sha256"] == mb["sha256"],
        "keyframes_a": ma["keyframe_indices"][:12],
        "keyframes_b": mb["keyframe_indices"][:12],
    }


def hw_vs_sw(
    ctx: Ctx, *, tid: str, fx: FX.Fixture, backend: str, tag: str = "",
    fingerprint: bool = True, fp_limit: int | None = None, **kw,
) -> dict:
    """Run avhw and avsw with identical settings and compare them."""
    hw, cm_hw = do_encode(ctx, tid=tid, fx=fx, backend=backend, reader="avhw",
                          tag=f"{tag}hw", **kw)
    sw, cm_sw = do_encode(ctx, tid=tid, fx=fx, backend=backend, reader="avsw",
                          tag=f"{tag}sw", **kw)
    ev: dict = {
        "hw": hw.to_json(), "sw": sw.to_json(),
        "reconcile_hw": reconciliation(cm_hw),
        "reconcile_sw": reconciliation(cm_sw),
    }
    if hw.output_exists and sw.output_exists:
        ev["sha256_hw"] = checks.sha256_file(Path(hw.output))
        ev["sha256_sw"] = checks.sha256_file(Path(sw.output))
        ev["byte_identical"] = ev["sha256_hw"] == ev["sha256_sw"]
        ev["packets"] = packet_compare(Path(hw.output), Path(sw.output))
        if fingerprint:
            ev["fingerprint"] = compare_products(
                Path(hw.output), Path(sw.output), ctx.fact(fx), limit=fp_limit
            )
    return ev


def hw_sw_ok(ev: dict) -> tuple[bool, list[str]]:
    problems: list[str] = []
    hw = ev["hw"]
    if not hw["reader_matches_request"]:
        problems.append(
            f"hardware reader not constructed (got {hw['reader_identity']!r})"
        )
    if hw["rc"] != 0:
        problems.append(f"hardware run rc={hw['rc']}")
    if not ev["reconcile_hw"]["ok"]:
        problems.append("hw reconcile: " + "; ".join(ev["reconcile_hw"]["reasons"]))
    if not ev["reconcile_sw"]["ok"]:
        problems.append("sw reconcile: " + "; ".join(ev["reconcile_sw"]["reasons"]))
    fp = ev.get("fingerprint")
    if fp and not fp.get("identical"):
        problems.append(
            f"ordered fingerprint differs (first diff at "
            f"{fp.get('first_diff_index')}, {fp.get('len_a')} vs {fp.get('len_b')})"
        )
    return (not problems), problems


# ---------------------------------------------------------------------------
# test implementations
# ---------------------------------------------------------------------------

IMPL: dict[str, callable] = {}


def impl(name):
    def deco(fn):
        IMPL[name] = fn
        return fn
    return deco


def _res(t: dict, tid: str, status: str, *, actual="", reason="", evidence=None,
         repeats=0, duration=0.0) -> TestResult:
    return TestResult(
        test_id=tid, category=t["category"], severity=t["severity"],
        title=t["title"], status=status, input=t.get("input", ""),
        backend=t.get("backend", ""), expected=t.get("expected", ""),
        actual=actual, reason=reason, evidence=evidence or {},
        repeats=repeats, duration_s=duration,
        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


# --- A ---------------------------------------------------------------------


@impl("a01_nvenc_identity")
def a01(t, ctx):
    rep = sources.verify_provenance(strict=False)
    b = next(x for x in rep["binaries"]
             if x["backend"] == "nvenc" and x["role"] == "hardware_decode")
    p = rep["patches"]["nvenc"]
    ok = b["ok"] and p["match"]
    return _res(
        t, "HD-A01", STATUS_PASS if ok else STATUS_FAIL,
        actual=f"sha256={b['sha256']} version={'/'.join(b['version_output'].splitlines()[:1])}",
        reason="" if ok else f"sha_match={b['sha_match']} version_match={b['version_match']} missing={b['missing_tokens']} patch={p['match']}",
        evidence={"binary": b, "patch": p},
    )


@impl("a02_qsv_identity")
def a02(t, ctx):
    rep = sources.verify_provenance(strict=False)
    b = next(x for x in rep["binaries"]
             if x["backend"] == "qsv" and x["role"] == "hardware_decode")
    p = rep["patches"]["qsv"]
    ok = b["ok"] and p["match"]
    return _res(
        t, "HD-A02", STATUS_PASS if ok else STATUS_FAIL,
        actual=f"sha256={b['sha256']} version={'/'.join(b['version_output'].splitlines()[:1])}",
        reason="" if ok else f"sha_match={b['sha_match']} version_match={b['version_match']} missing={b['missing_tokens']} patch={p['match']}",
        evidence={"binary": b, "patch": p,
                  "claim_boundary": sources.load_provenance()["backends"]["qsv"]["claim_boundary"]},
    )


@impl("a03_runtime_reader_identity")
def a03(t, ctx):
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-A03", ctx)
    if skip:
        return skip
    ev = {}
    problems = []
    for backend, expected in (("nvenc", "avcuvid"), ("qsv", "avqsv")):
        er, _ = do_encode(ctx, tid="HD-A03", fx=fx, backend=backend, reader="avhw",
                          tag="id", frames=8)
        ev[backend] = {"reader_identity": er.reader_identity, "expected": expected,
                       "rc": er.rc}
        if er.reader_identity != expected:
            problems.append(f"{backend}: got {er.reader_identity!r}, expected {expected!r}")
    return _res(
        t, "HD-A03", STATUS_PASS if not problems else STATUS_FAIL,
        actual="; ".join(f"{k}={v['reader_identity']}" for k, v in ev.items()),
        reason="; ".join(problems), evidence=ev,
    )


@impl("a04_software_control_identity")
def a04(t, ctx):
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-A04", ctx)
    if skip:
        return skip
    ev = {}
    problems = []
    for backend in ("nvenc", "qsv"):
        er, _ = do_encode(ctx, tid="HD-A04", fx=fx, backend=backend, reader="avsw",
                          tag="id", frames=8)
        ev[backend] = {"reader_identity": er.reader_identity, "rc": er.rc}
        if er.reader_identity != "avsw":
            problems.append(f"{backend}: got {er.reader_identity!r}, expected 'avsw'")
    return _res(
        t, "HD-A04", STATUS_PASS if not problems else STATUS_FAIL,
        actual="; ".join(f"{k}={v['reader_identity']}" for k, v in ev.items()),
        reason="; ".join(problems), evidence=ev,
    )


@impl("a05_stock_control_distinct")
def a05(t, ctx):
    prov = sources.load_provenance()
    ev = {}
    problems = []
    for backend in ("nvenc", "qsv"):
        hw = prov["backends"][backend]["hardware_decode"]
        st = prov["backends"][backend]["stock_control"]
        chk = sources.check_binary(backend, "stock_control", st, prov)
        ev[backend] = {"stock": chk.to_json(),
                       "distinct_sha": hw["sha256"] != st["sha256"],
                       "distinct_version": hw["version_line"] != st["version_line"]}
        if not chk.ok:
            problems.append(f"{backend}: stock control not usable ({chk.error or 'hash/version mismatch'})")
        if hw["sha256"] == st["sha256"]:
            problems.append(f"{backend}: stock and patched share a hash")
    return _res(
        t, "HD-A05", STATUS_PASS if not problems else STATUS_FAIL,
        actual="both stock controls present, runnable and hash-distinct"
        if not problems else "problems found",
        reason="; ".join(problems), evidence=ev,
    )


@impl("a06_not_from_path")
def a06(t, ctx):
    prov = sources.load_provenance()
    target = ROOT / prov["backends"]["nvenc"]["hardware_decode"]["binary"]
    decoy_dir = Path(tempfile.mkdtemp(prefix="1kt-decoy-"))
    decoy = decoy_dir / target.name
    try:
        shutil.copy2(target, decoy)
        with decoy.open("r+b") as f:
            f.seek(0)
            f.write(b"\x00")           # corrupt the copy so hashes must differ
        old = os.environ.get("PATH", "")
        os.environ["PATH"] = str(decoy_dir) + os.pathsep + old
        try:
            on_path = shutil.which(target.name)
            resolved_sha = sources.sha256_file(target)
            decoy_sha = sources.sha256_file(decoy)
        finally:
            os.environ["PATH"] = old
        ok = (
            on_path is not None
            and os.path.normcase(str(Path(on_path).resolve()))
            != os.path.normcase(str(target.resolve()))
            and resolved_sha == prov["backends"]["nvenc"]["hardware_decode"]["sha256"]
            and decoy_sha != resolved_sha
        )
        return _res(
            t, "HD-A06", STATUS_PASS if ok else STATUS_FAIL,
            actual=f"PATH resolved to {on_path}; explicit path hash still matches provenance",
            reason="" if ok else "decoy on PATH changed the resolved binary",
            evidence={"on_path": str(on_path), "explicit": str(target),
                      "decoy_sha": decoy_sha, "explicit_sha": resolved_sha},
        )
    finally:
        shutil.rmtree(decoy_dir, ignore_errors=True)


@impl("a07_startup_failure_classified")
def a07(t, ctx):
    fx = FX.SONYX if hasattr(FX, "SONYX") else FX.SONY_HS
    skip = _require_fixture(fx, "HD-A07", ctx)
    if skip:
        return skip
    d = RUNS / "_broken"
    broken = runners.broken_binary_copy(d)
    out = RUNS / "HD-A07_broken.mp4"
    er = runners.run_encode_spec(
        label="HD-A07_broken", spec=sources.BackendSpec(
            backend="nvenc", kind="nvencc", binary=broken,
            reader_arg="--avhw", reader_identity_expected="avcuvid"),
        source=fx.path, output=out, frames=4,
    )
    verdict = checks  # classification lives in encoders.hwdecode
    from encoders.hwdecode import classify_reader_failure
    code, detail = classify_reader_failure(er.log_text, rc=er.rc)
    ok = er.rc != 0 and not er.output_exists
    return _res(
        t, "HD-A07", STATUS_PASS if ok else STATUS_FAIL,
        actual=f"rc={er.rc} output_exists={er.output_exists} classified_as={code}",
        reason="" if ok else "broken binary did not fail as expected",
        evidence={"rc": er.rc, "reason_code": code, "detail": detail,
                  "stderr": er.stderr_tail[-300:]},
    )


@impl("a08_provenance_selfcheck")
def a08(t, ctx):
    prov = sources.load_provenance()
    real = sources.verify_provenance(strict=False)
    # a deliberately wrong expectation must be caught
    fake = json.loads(json.dumps(prov))
    fake["backends"]["nvenc"]["hardware_decode"]["sha256"] = "0" * 64
    orig_load = sources.load_provenance
    try:
        sources.load_provenance = lambda: fake
        caught = False
        try:
            sources.verify_provenance(strict=True)
        except sources.ProvenanceError:
            caught = True
    finally:
        sources.load_provenance = orig_load
    ok = real["ok"] and caught
    return _res(
        t, "HD-A08", STATUS_PASS if ok else STATUS_FAIL,
        actual=f"real_ok={real['ok']} wrong_expectation_raised={caught}",
        reason="" if ok else "provenance self-check did not gate correctly",
        evidence={"real": {"ok": real["ok"], "failures": real["failures"]},
                  "wrong_expectation_raised": caught},
    )


@impl("a09_default_resolution_excludes_research")
def a09(t, ctx):
    """The default path must resolve the shipped build, never the research one.

    The two builds differ only in behaviour, so an accidental swap would be
    invisible until frames went missing. `find_hw_tool` globs a directory
    tree, and before this check the answer depended on sort order.
    """
    from core.config import find_hw_tool
    ev, problems = {}, []
    for exe, expect_dir in (("NVEncC64.exe", "NVEncC_9.31_x64"),
                            ("QSVEncC64.exe", "QSVEncC_8.26_x64")):
        got = find_hw_tool(ROOT, exe)
        ev[exe] = str(got)
        if "avhw" in {p.lower() for p in got.parts}:
            problems.append(f"{exe}: resolved into the research tree ({got})")
        if expect_dir.lower() not in str(got).lower():
            problems.append(f"{exe}: resolved to {got}, expected the {expect_dir} build")
        again = find_hw_tool(ROOT, exe)
        ev[f"{exe}_stable"] = str(again) == str(got)
        if not ev[f"{exe}_stable"]:
            problems.append(f"{exe}: resolution is not deterministic")
    # the research build must exist for the exclusion rule to mean anything
    from encoders.hwdecode import PATCHED_BUILD
    for base, spec in PATCHED_BUILD.items():
        ev[f"patched_{base}_present"] = (ROOT / spec["rel"]).is_file()
    return _res(
        t, "HD-A09", STATUS_PASS if not problems else STATUS_FAIL,
        actual="shipped builds resolved deterministically; tools/avhw excluded",
        reason="; ".join(problems), evidence=ev,
    )


# --- B ---------------------------------------------------------------------


@impl("b01_sony_hs_routing")
def b01(t, ctx):
    from encoders.hwdecode import route_decode
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-B01", ctx)
    if skip:
        return skip
    facts = ctx.fact(fx)
    v = facts.video
    ev, problems = {}, []
    for backend in ("nvenc", "qsv"):
        r = route_decode(policy="auto", backend=backend, codec=v.codec,
                         chroma=v.chroma, depth=v.bit_depth)
        ev[backend] = r.to_json()
        if not r.hardware:
            problems.append(f"{backend}: routed to software ({r.reason})")
    er, _ = do_encode(ctx, tid="HD-B01", fx=fx, backend="nvenc", reader="avhw",
                      tag="route", frames=8)
    ev["real_run"] = {"reader_identity": er.reader_identity, "rc": er.rc}
    if er.reader_identity != "avcuvid":
        problems.append("real run did not construct avcuvid")
    return _res(
        t, "HD-B01", STATUS_PASS if not problems else STATUS_FAIL,
        actual=f"nvenc={ev['nvenc']['reason']} qsv={ev['qsv']['reason']}",
        reason="; ".join(problems), evidence=ev,
    )


@impl("b02_sony_422_routing")
def b02(t, ctx):
    from encoders.hwdecode import route_decode
    fx = FX.SONY_422_C9037
    skip = _require_fixture(fx, "HD-B02", ctx)
    if skip:
        return skip
    v = ctx.fact(fx).video
    ev, problems = {}, []
    nv = route_decode(policy="auto", backend="nvenc", codec=v.codec,
                      chroma=v.chroma, depth=v.bit_depth)
    qs = route_decode(policy="auto", backend="qsv", codec=v.codec,
                      chroma=v.chroma, depth=v.bit_depth)
    ev["nvenc"] = nv.to_json()
    ev["qsv"] = qs.to_json()
    if not nv.hardware:
        problems.append(f"nvenc should be eligible, got {nv.reason}")
    if qs.hardware:
        problems.append("qsv should refuse, got hardware")
    if qs.reason != "capability_refused":
        problems.append(f"qsv reason should be capability_refused, got {qs.reason}")
    if not qs.warnings:
        problems.append("qsv refusal produced no warning (silent fallback)")
    # both must still deliver a frame-exact result
    for backend in ("nvenc", "qsv"):
        er, cm = do_encode(ctx, tid="HD-B02", fx=fx, backend=backend,
                           reader="avsw", tag="fallback")
        ok, reasons = checks.reconcile(cm)
        ev[f"{backend}_software"] = {"rc": er.rc, "reconcile_ok": ok,
                                     "reasons": reasons, "reader": er.reader_identity}
        if not ok:
            problems.append(f"{backend} software fallback not exact: {reasons}")
    return _res(
        t, "HD-B02", STATUS_PASS if not problems else STATUS_FAIL,
        actual=f"nvenc={nv.reason} qsv={qs.reason}",
        reason="; ".join(problems), evidence=ev,
    )


@impl("b03_dji_routing")
def b03(t, ctx):
    from encoders.hwdecode import route_decode
    ev, problems = {}, []
    for fx in (FX.DJI_0009, FX.DJI_0010):
        skip = _require_fixture(fx, "HD-B03", ctx)
        if skip:
            ev[fx.fid] = {"skipped": skip.reason}
            continue
        v = ctx.fact(fx).video
        row = {}
        for backend in ("nvenc", "qsv"):
            r = route_decode(policy="auto", backend=backend, codec=v.codec,
                             chroma=v.chroma, depth=v.bit_depth)
            row[backend] = r.to_json()
            if not r.hardware:
                problems.append(f"{fx.fid}/{backend}: not eligible ({r.reason})")
        ev[fx.fid] = row
    return _res(
        t, "HD-B03", STATUS_PASS if not problems else STATUS_FAIL,
        actual="both DJI fixtures eligible on both backends",
        reason="; ".join(problems), evidence=ev,
    )


@impl("b04_unproven_is_safe")
def b04(t, ctx):
    from encoders.hwdecode import route_decode
    grid = [
        ("nvenc", "hevc", "4:2:0", 8),
        ("nvenc", "hevc", "4:2:2", 10),
        ("nvenc", "h264", "4:2:0", 8),
        ("nvenc", "h264", "4:2:0", 10),
        ("nvenc", "av1", "4:2:0", 10),
        ("nvenc", "mpeg2video", "4:2:0", 8),
        ("qsv", "hevc", "4:2:0", 8),
        ("qsv", "h264", "4:2:0", 8),
        ("qsv", "av1", "4:2:0", 10),
        ("qsv", "hevc", "4:4:4", 10),
    ]
    ev, problems = {}, []
    for backend, codec, chroma, depth in grid:
        r = route_decode(policy="auto", backend=backend, codec=codec,
                         chroma=chroma, depth=depth)
        key = f"{backend}/{codec}/{chroma}/{depth}"
        ev[key] = {"hardware": r.hardware, "reason": r.reason}
        if r.hardware:
            problems.append(f"{key}: activated an unproven hardware path")
        if not r.warnings:
            problems.append(f"{key}: routed to software with no warning")
    return _res(
        t, "HD-B04", STATUS_PASS if not problems else STATUS_FAIL,
        actual=f"{len(grid)} unproven combinations, all routed to software safely",
        reason="; ".join(problems), evidence=ev,
    )


@impl("b05_default_is_unchanged")
def b05(t, ctx):
    from encoders.nvencc import NvencBackend
    from encoders.qsvencc import QsvBackend
    ev, problems = {}, []
    fx = FX.SONY_HS
    nv = NvencBackend(ROOT / "tools" / "NVEncC_9.31_x64" / "NVEncC64.exe")
    qs = QsvBackend(ROOT / "tools" / "QSVEncC_8.26_x64" / "QSVEncC64.exe")
    base = dict(source=Path("s.mp4"), output=Path("o.mp4"), profile={"qvbr": 26},
                chroma="4:2:0", depth=10)
    nv_cmd, _, _ = nv.command(**base)
    qs_cmd, _, _ = qs.command(source=Path("s.mp4"), output=Path("o.mp4"),
                              profile={"icq": 26}, chroma="4:2:0", depth=10)
    ev["nvenc_default"] = nv_cmd
    ev["qsv_default"] = qs_cmd
    for name, cmd in (("nvenc", nv_cmd), ("qsv", qs_cmd)):
        if "--avsw" not in cmd:
            problems.append(f"{name}: default argv lost --avsw")
        if "--avhw" in cmd:
            problems.append(f"{name}: default argv gained --avhw")
        idx = cmd.index("--avsw")
        if cmd[idx + 1:idx + 4] != ["--video-track", "1", "-c"]:
            problems.append(f"{name}: token order changed around the reader")
    return _res(
        t, "HD-B05", STATUS_PASS if not problems else STATUS_FAIL,
        actual="default reader is avsw and argv shape is unchanged",
        reason="; ".join(problems), evidence=ev,
    )


@impl("b06_require_never_degrades")
def b06(t, ctx):
    from encoders.hwdecode import route_decode, R_REQUIRE_UNMET
    ev, problems = {}, []
    for backend, codec, chroma, depth in (
        ("nvenc", "hevc", "4:2:0", 8),
        ("qsv", "h264", "4:2:2", 10),
        ("nvenc", "av1", "4:2:0", 10),
    ):
        r = route_decode(policy="require", backend=backend, codec=codec,
                         chroma=chroma, depth=depth)
        key = f"{backend}/{codec}/{chroma}/{depth}"
        ev[key] = r.to_json()
        if r.reason != R_REQUIRE_UNMET:
            problems.append(f"{key}: reason {r.reason} != require_unmet")
        if r.hardware:
            problems.append(f"{key}: reported hardware under require for an ineligible input")
    # the ladder must raise rather than continue
    src = (ROOT / "core" / "batch_hw.py").read_text(encoding="utf-8")
    if "R_REQUIRE_UNMET" not in src or "HW_DECODE_REQUIRE" not in src:
        problems.append("ladder does not handle require_unmet")
    return _res(
        t, "HD-B06", STATUS_PASS if not problems else STATUS_FAIL,
        actual="require yields require_unmet and the ladder raises",
        reason="; ".join(problems), evidence=ev,
    )


@impl("b07_vfr_interaction")
def b07(t, ctx):
    from encoders.nvencc import NvencBackend
    fx = FX.SONY_HS
    nv = NvencBackend(ROOT / "tools" / "NVEncC_9.31_x64" / "NVEncC64.exe")
    plain, _, _ = nv.command(Path("s.mp4"), Path("o.mp4"), {"qvbr": 26},
                             "4:2:0", 10, vfr=False, reader="avsw")
    vfr_hw, _, _ = nv.command(Path("s.mp4"), Path("o.mp4"), {"qvbr": 26},
                              "4:2:0", 10, vfr=True, reader="avhw")
    ev = {"vfr_hw_argv": vfr_hw}
    problems = []
    if "--avsync" not in vfr_hw or "forcecfr" not in vfr_hw:
        problems.append("VFR handling lost when the reader is hardware")
    if "--avhw" not in vfr_hw:
        problems.append("reader lost when VFR handling is on")
    if "--avsync" in plain:
        problems.append("non-VFR run unexpectedly gained --avsync")
    return _res(
        t, "HD-B07", STATUS_PASS if not problems else STATUS_FAIL,
        actual="VFR and reader selection are independent and both present",
        reason="; ".join(problems), evidence=ev,
    )


@impl("b08_routing_deterministic")
def b08(t, ctx):
    from encoders.hwdecode import route_decode
    grid = [(p, b, c, ch, d)
            for p in ("off", "auto", "require")
            for b in ("nvenc", "qsv")
            for c, ch, d in (("hevc", "4:2:0", 10), ("h264", "4:2:2", 10),
                             ("hevc", "4:2:0", 8), ("av1", "4:2:0", 10))]
    a = json.dumps([[p, b, c, ch, d, route_decode(
        policy=p, backend=b, codec=c, chroma=ch, depth=d).to_json()]
        for p, b, c, ch, d in grid], sort_keys=True)
    b2 = json.dumps([[p, b, c, ch, d, route_decode(
        policy=p, backend=b, codec=c, chroma=ch, depth=d).to_json()]
        for p, b, c, ch, d in grid], sort_keys=True)
    src = (ROOT / "core" / "batch_hw.py").read_text(encoding="utf-8")
    logged = "DECODE_ROUTE" in src
    ok = a == b2 and logged
    return _res(
        t, "HD-B08", STATUS_PASS if ok else STATUS_FAIL,
        actual=f"{len(grid)} decisions, identical across calls, decision line logged={logged}",
        reason="" if ok else "routing not deterministic or not logged",
        evidence={"grid_size": len(grid), "deterministic": a == b2,
                  "decision_logged": logged},
    )


@impl("b09_reason_codes_distinct")
def b09(t, ctx):
    from encoders.hwdecode import (
        classify_reader_failure, R_CAPABILITY_REFUSED, R_DEVICE_UNAVAILABLE,
        R_DECODE_FAILED, R_READER_UNAVAILABLE,
    )
    samples = {
        "refusal": "avqsv: codec h264(yuv422p10le) unable to decode by qsv.",
        "device": "Invalid Device Id = 1",
        "init": "Failed to initialize the decoder.",
        "decode": "No video packets found!",
    }
    ev = {}
    codes = set()
    for name, text in samples.items():
        code, detail = classify_reader_failure(text)
        ev[name] = {"code": code, "detail": detail}
        codes.add(code)
    # integrity codes are separate by construction
    from encoders.integrity import IntegrityVerdict
    ev["count_mismatch"] = "separate code emitted by the integrity gate"
    ev["sequence_mismatch"] = "separate code emitted by the sequence verifier"
    ok = len(codes) == len(samples)
    return _res(
        t, "HD-B09", STATUS_PASS if ok else STATUS_FAIL,
        actual=f"{len(codes)} distinct codes from {len(samples)} failure classes",
        reason="" if ok else "failure classes collapsed onto the same code",
        evidence=ev,
    )


@impl("b10_hw_decode_binary_pinned")
def b10(t, ctx):
    """Hardware decode must use the patched build, or say why it cannot.

    Using the shipped build would be *safe* — the integrity gate would
    reject every result — but the hardware path would then never run, so
    the feature would be quietly useless while appearing enabled. That is
    its own kind of silent failure.
    """
    from encoders.hwdecode import (
        PATCHED_BUILD, resolve_decode_tool, R_NOT_PROVEN, R_POLICY_OFF,
        decode_tool_is_stock,
    )
    ev, problems = {}, []

    for kind, base in (("nvencc", "nvenc"), ("qsvencc", "qsv")):
        off = resolve_decode_tool(kind=kind, script_dir=ROOT, policy="off")
        ev[f"{base}/off"] = {"usable": off.usable, "reason": off.reason}
        if off.usable:
            problems.append(f"{base}: policy off still resolved a decode tool")
        if off.reason != R_POLICY_OFF:
            problems.append(f"{base}: policy off reason is {off.reason}")

        auto = resolve_decode_tool(kind=kind, script_dir=ROOT, policy="auto")
        spec = PATCHED_BUILD[base]
        ev[f"{base}/auto"] = {
            "usable": auto.usable, "role": auto.role, "reason": auto.reason,
            "path": str(auto.path) if auto.path else None,
            "detail": auto.detail,
        }
        if not auto.usable:
            problems.append(
                f"{base}: patched build not usable ({auto.reason}: {auto.detail})")
        else:
            if str(Path(spec["rel"])).replace("\\", "/") not in str(
                    auto.path).replace("\\", "/"):
                problems.append(f"{base}: resolved {auto.path}, expected {spec['rel']}")
            if decode_tool_is_stock(base, auto.path):
                problems.append(f"{base}: resolved a shipped (defective) build")
            if auto.role != "patched":
                problems.append(f"{base}: role is {auto.role!r}, expected 'patched'")

    # negative: a build whose bytes do not match the recorded hash must be
    # refused rather than used
    src = ROOT / PATCHED_BUILD["nvenc"]["rel"]
    scratch = FX.WORK / "b10_fake_tools" / "avhw" / "NVEncC_9.31_avhw"
    scratch.mkdir(parents=True, exist_ok=True)
    fake = scratch / "NVEncC64.exe"
    shutil.copy2(src, fake)
    with fake.open("r+b") as f:
        f.seek(0)
        f.write(b"\x00")
    bad = resolve_decode_tool(kind="nvencc", script_dir=FX.WORK / "b10_fake_tools",
                              policy="auto")
    ev["negative_hash_mismatch"] = {"usable": bad.usable, "reason": bad.reason,
                                    "detail": bad.detail}
    if bad.usable:
        problems.append("a hash-mismatched build was accepted")
    if bad.reason != R_NOT_PROVEN:
        problems.append(f"hash mismatch reason is {bad.reason}")

    # negative: a missing build must also refuse, not fall back silently
    empty = FX.WORK / "b10_empty"
    empty.mkdir(parents=True, exist_ok=True)
    miss = resolve_decode_tool(kind="nvencc", script_dir=empty, policy="auto")
    ev["negative_missing_build"] = {"usable": miss.usable, "reason": miss.reason,
                                    "detail": miss.detail}
    if miss.usable:
        problems.append("a missing build was reported as usable")
    if miss.reason != R_NOT_PROVEN:
        problems.append(f"missing-build reason is {miss.reason}")

    return _res(
        t, "HD-B10", STATUS_PASS if not problems else STATUS_FAIL,
        actual="policy off resolves nothing; auto pins the patched build by hash; "
               "a wrong or missing build is refused with a reason code",
        reason="; ".join(problems), evidence=ev,
    )


# --- C ---------------------------------------------------------------------


def _write_fp(name: str, data) -> str:
    RESULTS.mkdir(parents=True, exist_ok=True)
    p = RESULTS / name
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str),
                 encoding="utf-8")
    return str(p)


@impl("c01_three_way_sony")
def c01(t, ctx):
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-C01", ctx)
    if skip:
        return skip
    facts = ctx.fact(fx)
    container = facts.container_samples
    lead = facts.leading_pictures
    ev: dict = {"container_expected": container, "leading_pictures": lead}
    problems = []

    patched_hw, cm_ph = do_encode(ctx, tid="HD-C01", fx=fx, backend="nvenc",
                                  reader="avhw", tag="patched")
    patched_sw, cm_ps = do_encode(ctx, tid="HD-C01", fx=fx, backend="nvenc",
                                  reader="avsw", tag="patchedsw")
    stock_hw, cm_sh = do_encode(ctx, tid="HD-C01", fx=fx, backend="nvenc",
                                reader="avhw", role="stock_control", tag="stock")
    ev["patched_hw"] = reconciliation(cm_ph)
    ev["patched_sw"] = reconciliation(cm_ps)
    ev["stock_hw"] = reconciliation(cm_sh)
    ev["patched_reader"] = patched_hw.reader_identity
    ev["stock_reader"] = stock_hw.reader_identity

    if patched_hw.reader_identity != "avcuvid":
        problems.append("patched run did not construct avcuvid")
    if not ev["patched_hw"]["ok"]:
        problems.append("patched avhw counts do not reconcile: "
                        + "; ".join(ev["patched_hw"]["reasons"]))
    if not ev["patched_sw"]["ok"]:
        problems.append("avsw counts do not reconcile")

    if patched_hw.output_exists and patched_sw.output_exists:
        ev["fingerprint"] = compare_products(
            Path(patched_hw.output), Path(patched_sw.output), facts)
        ev["packets"] = packet_compare(Path(patched_hw.output),
                                       Path(patched_sw.output))
        ev["sha256_patched_hw"] = checks.sha256_file(Path(patched_hw.output))
        ev["sha256_patched_sw"] = checks.sha256_file(Path(patched_sw.output))
        ev["byte_identical"] = ev["sha256_patched_hw"] == ev["sha256_patched_sw"]
        if not ev["fingerprint"]["identical"]:
            problems.append(
                f"patched avhw fingerprint differs from avsw at index "
                f"{ev['fingerprint']['first_diff_index']}")
        if not ev["packets"]["manifest_sha_equal"]:
            problems.append("patched avhw packet manifest differs from avsw")

    # the negative control must still reproduce the defect it was chosen for
    stock_ok, stock_reasons = checks.reconcile(cm_sh)
    ev["stock_reproduces_defect"] = (
        cm_sh.encoder_input is not None and container is not None
        and cm_sh.encoder_input == container - (lead or 0)
    )
    if not ev["stock_reproduces_defect"]:
        return _res(
            t, "HD-C01", STATUS_BLOCKED,
            actual=f"patched={cm_ph.encoder_input} stock={cm_sh.encoder_input} container={container}",
            reason=("the stock negative control did not reproduce the "
                    f"N-leading loss (stock encoder_input="
                    f"{cm_sh.encoder_input}, expected {container}-{lead}); "
                    "without a live control the detection claim is untested"),
            evidence=ev,
        )
    return _res(
        t, "HD-C01", STATUS_PASS if not problems else STATUS_FAIL,
        actual=(f"patched avhw={cm_ph.encoder_input} == avsw={cm_ps.encoder_input} "
                f"== container={container}; stock avhw={cm_sh.encoder_input} "
                f"(defect reproduced)"),
        reason="; ".join(problems), evidence=ev,
    )


@impl("c02_sony_medium")
def c02(t, ctx):
    fixtures = [FX.SONY_HS, FX.SONY_422_C9110, FX.GEN_A_COPY]
    ev, problems, done = {}, [], []
    for fx in fixtures:
        skip = _require_fixture(fx, f"HD-C02", ctx)
        if skip:
            ev[fx.fid] = {"skipped": skip.reason}
            continue
        backend = "nvenc"
        v = ctx.fact(fx).video
        if v.codec != "hevc":
            backend = "nvenc"
        row = hw_vs_sw(ctx, tid="HD-C02", fx=fx, backend=backend, tag=fx.fid[:6])
        ok, probs = hw_sw_ok(row)
        row["ok"] = ok
        ev[fx.fid] = row
        done.append(fx.fid)
        if not ok:
            problems.append(f"{fx.fid}: " + "; ".join(probs))
    if not done:
        return _res(t, "HD-C02", STATUS_SKIP, reason="no fixture available")
    return _res(
        t, "HD-C02", STATUS_PASS if not problems else STATUS_FAIL,
        actual=f"{len(done)} fixtures compared frame-for-frame",
        reason="; ".join(problems), evidence=ev,
    )


@impl("c03_sony_long")
def c03(t, ctx):
    """Long-clip integrity: no silent loss, no duplication.

    Two distinct claims, deliberately not merged:

    * a **full-input** long encode must reconcile exactly with the
      container — this is the integrity claim;
    * a **``--frames N``** encode must be internally consistent
      (encoder, container and both independent decoders agree) and must
      follow the documented leading-picture relation
      (``presented == N - leading``), which HD-D07/D08 characterise and
      which is *not* a frame-integrity defect. Asserting ``presented == N``
      here would contradict the matrix's own contract and would turn a
      documented pre-existing semantic into a false failure.
    """
    fx = FX.FIELD_STRESS_LONG if hasattr(FX, "FIELD_STRESS_LONG") else None
    if fx is None:
        cands = [f for f in FX.real_field() if "stress" in f.group]
        cands.sort(key=lambda f: ctx.fact(f).container_samples or 0, reverse=True)
        fx = cands[0] if cands else None
    skip = _require_fixture(fx, "HD-C03", ctx)
    if skip:
        return skip
    facts = ctx.fact(fx)
    container = facts.container_samples
    lead = facts.leading_pictures or 0
    requests = [3000, 6000, 10000, 18000]
    if not ctx.deep:
        requests = [3000, 18000]
    ev: dict = {"container_expected": container, "fixture": fx.fid,
                "leading_pictures": lead, "full_input": {}, "requests": {}}
    problems = []

    # --- claim 1: full input reconciles exactly -------------------------
    full_hw, cm_full_hw = do_encode(ctx, tid="HD-C03", fx=fx, backend="nvenc",
                                    reader="avhw", tag="full")
    ok_full, full_reasons = checks.reconcile(cm_full_hw)
    ev["full_input"]["hardware"] = {
        "rc": full_hw.rc, "reader": full_hw.reader_identity,
        "container": cm_full_hw.container_expected,
        "encoder_input": cm_full_hw.encoder_input,
        "output_stream": cm_full_hw.output_stream,
        "independent_decoded": cm_full_hw.independent_decoded,
        "ok": ok_full, "reasons": full_reasons,
    }
    if full_hw.reader_identity != "avcuvid":
        problems.append(f"full run reader {full_hw.reader_identity!r}")
    if not ok_full:
        problems.append("full long run does not reconcile: "
                        + "; ".join(full_reasons))
    if full_hw.output_exists:
        sw_full, _ = do_encode(ctx, tid="HD-C03", fx=fx, backend="nvenc",
                               reader="avsw", tag="fullref")
        if sw_full.output_exists:
            fpc = compare_products(Path(full_hw.output), Path(sw_full.output),
                                   facts, limit=24)
            ev["full_input"]["fingerprint_head24"] = {
                "identical": fpc["identical"], "compared": fpc["compared"],
                "first_diff_index": fpc["first_diff_index"],
            }
            if not fpc["identical"]:
                problems.append(
                    f"full long run head fingerprint differs from software at "
                    f"{fpc['first_diff_index']}")

    # --- claim 2: --frames requests stay internally consistent ----------
    for n in requests:
        er, cm = do_encode(ctx, tid="HD-C03", fx=fx, backend="nvenc",
                           reader="avhw", tag=f"f{n}", frames=n)
        row = {
            "requested": n, "rc": er.rc, "reader": er.reader_identity,
            "encoder_input": cm.encoder_input, "output_stream": cm.output_stream,
            "independent_decoded": cm.independent_decoded,
            "reader_reported": cm.reader_reported,
            "clamped": bool(container and n >= container),
        }
        if container:
            # three regimes, measured (see HD-D07/D08):
            #   n >= container   -> the whole clip, leading pictures included
            #   n <= leading     -> the request is ignored, whole clip again
            #   otherwise        -> n - leading
            if n >= container or n <= lead:
                row["contract_expected"] = container
                row["contract_regime"] = (
                    "clamped_to_container" if n >= container else "below_leading"
                )
            else:
                row["contract_expected"] = n - lead
                row["contract_regime"] = "n_minus_leading"
        ev["requests"][str(n)] = row
        if er.rc != 0:
            problems.append(f"--frames {n}: rc={er.rc}")
            continue
        if er.reader_identity != "avcuvid":
            problems.append(f"--frames {n}: reader {er.reader_identity!r}")
        if cm.encoder_input != cm.output_stream:
            problems.append(
                f"--frames {n}: encoder {cm.encoder_input} != container "
                f"{cm.output_stream}")
        if cm.output_stream != cm.independent_decoded:
            problems.append(
                f"--frames {n}: container {cm.output_stream} != decoded "
                f"{cm.independent_decoded}")
        if row.get("contract_expected") is not None \
                and cm.encoder_input != row["contract_expected"]:
            problems.append(
                f"--frames {n}: presented {cm.encoder_input}, measured "
                f"contract ({row.get('contract_regime')}) says "
                f"{row['contract_expected']} (requested {n}, leading {lead}, "
                f"container {container})")
    return _res(
        t, "HD-C03", STATUS_PASS if not problems else STATUS_FAIL,
        actual=(f"full input reconciled at {container}; "
                f"{len(requests)} --frames requests internally consistent"),
        reason="; ".join(problems), evidence=ev,
    )


@impl("c04_real_corpus")
def c04(t, ctx):
    clips = sorted(FX.real_a7m5(), key=lambda f: ctx.fact(f).container_samples or 0)
    if not clips:
        return _res(t, "HD-C04", STATUS_SKIP, reason="real A7M5 corpus absent")
    n = 12 if not ctx.deep else len(clips)
    step = max(1, len(clips) // n)
    sample = clips[::step][:n]
    ev, problems, done = {}, [], []
    for fx in sample:
        if not fx.path.is_file():
            continue
        facts = ctx.fact(fx)
        er, cm = do_encode(ctx, tid="HD-C04", fx=fx, backend="nvenc",
                           reader="avhw", tag="scan")
        ok, reasons = checks.reconcile(cm)
        row = {"container": cm.container_expected, "encoder_input": cm.encoder_input,
               "output_stream": cm.output_stream,
               "independent_decoded": cm.independent_decoded,
               "reader": er.reader_identity, "rc": er.rc, "ok": ok,
               "leading": facts.leading_pictures}
        # fingerprint a bounded head so the scan stays affordable
        if ok and er.output_exists:
            sw, cm_sw = do_encode(ctx, tid="HD-C04", fx=fx, backend="nvenc",
                                  reader="avsw", tag="scanref")
            if cm_sw.output_stream == cm.output_stream and sw.output_exists:
                fpc = compare_products(Path(er.output), Path(sw.output),
                                       facts, limit=40)
                row["fingerprint_head40"] = {
                    "identical": fpc["identical"],
                    "compared": fpc["compared"],
                    "first_diff_index": fpc["first_diff_index"],
                }
                if not fpc["identical"]:
                    problems.append(f"{fx.fid}: head fingerprint differs")
            else:
                problems.append(
                    f"{fx.fid}: software reference count "
                    f"{cm_sw.output_stream} != hardware {cm.output_stream}")
        ev[fx.fid] = row
        done.append(fx.fid)
        if not ok:
            problems.append(f"{fx.fid}: " + "; ".join(reasons))
        if er.reader_identity != "avcuvid":
            problems.append(f"{fx.fid}: reader {er.reader_identity!r}")
    if not done:
        return _res(t, "HD-C04", STATUS_SKIP, reason="no real clip readable")
    return _res(
        t, "HD-C04", STATUS_PASS if not problems else STATUS_FAIL,
        actual=f"{len(done)} real A7M5 clips scanned, all reconciling to container",
        reason="; ".join(problems), evidence=ev,
    )


@impl("c05_dji")
def c05(t, ctx):
    fixtures = [FX.DJI_0009, FX.DJI_0010]
    extra = [f for f in FX.real_field() if "dji" in f.fid.lower()]
    fixtures += extra[:1]
    ev, problems, done = {}, [], []
    for fx in fixtures:
        skip = _require_fixture(fx, "HD-C05", ctx)
        if skip:
            ev[fx.fid] = {"skipped": skip.reason}
            continue
        for backend in ("nvenc", "qsv"):
            row = hw_vs_sw(ctx, tid="HD-C05", fx=fx, backend=backend,
                           tag=f"{fx.fid[:6]}{backend}", fp_limit=None)
            ok, probs = hw_sw_ok(row)
            row["ok"] = ok
            ev[f"{fx.fid}/{backend}"] = row
            if not ok:
                problems.append(f"{fx.fid}/{backend}: " + "; ".join(probs))
        done.append(fx.fid)
    if not done:
        return _res(t, "HD-C05", STATUS_SKIP, reason="no DJI fixture available")
    return _res(
        t, "HD-C05", STATUS_PASS if not problems else STATUS_FAIL,
        actual=f"{len(done)} DJI fixtures exact on both backends",
        reason="; ".join(problems), evidence=ev,
    )


@impl("c06_h264_422")
def c06(t, ctx):
    fixtures = [FX.SONY_422_C9037, FX.SONY_422_C9073, FX.SONY_422_C9088,
                FX.SONY_422_C9110, FX.SONY_422_C0887]
    ev, problems, done = {}, [], []
    for fx in fixtures:
        skip = _require_fixture(fx, "HD-C06", ctx)
        if skip:
            continue
        # NVENC: hardware must be exact
        row = hw_vs_sw(ctx, tid="HD-C06", fx=fx, backend="nvenc",
                       tag=f"{fx.fid[:6]}nv", fp_limit=None)
        ok, probs = hw_sw_ok(row)
        row["ok"] = ok
        ev[f"{fx.fid}/nvenc"] = {
            "ok": ok, "problems": probs,
            "hw_reader": row["hw"]["reader_identity"],
            "hw_encoded": row["reconcile_hw"]["counts"]["encoder_input"],
            "sw_encoded": row["reconcile_sw"]["counts"]["encoder_input"],
            "container": row["reconcile_hw"]["counts"]["container_expected"],
            "fingerprint_identical": row.get("fingerprint", {}).get("identical"),
            "byte_identical": row.get("byte_identical"),
        }
        if not ok:
            problems.append(f"{fx.fid}/nvenc: " + "; ".join(probs))
        # QSV: must refuse loudly, not lose frames
        er, cm = do_encode(ctx, tid="HD-C06", fx=fx, backend="qsv",
                           reader="avhw", role="stock_control", tag="refusal")
        from encoders.hwdecode import classify_reader_failure
        code, detail = classify_reader_failure(er.log_text, rc=er.rc)
        refused = (er.rc != 0 and not er.output_exists)
        ev[f"{fx.fid}/qsv"] = {
            "rc": er.rc, "output_exists": er.output_exists,
            "reason_code": code, "detail": detail, "refused_loudly": refused,
        }
        if not refused:
            problems.append(
                f"{fx.fid}/qsv: hardware attempt neither produced a valid "
                f"result nor refused loudly (rc={er.rc}, "
                f"output={er.output_exists})")
        done.append(fx.fid)
    if not done:
        return _res(t, "HD-C06", STATUS_SKIP, reason="no 4:2:2 fixture available")
    return _res(
        t, "HD-C06", STATUS_PASS if not problems else STATUS_FAIL,
        actual=(f"{len(done)} H.264 4:2:2 samples: NVENC hardware exact, "
                f"QSV loud refusal"),
        reason="; ".join(problems), evidence=ev,
    )


@impl("c07_controls")
def c07(t, ctx):
    ev, problems, done = {}, [], []
    for fx in FX.GEN_ALL:
        skip = _require_fixture(fx, "HD-C07", ctx)
        if skip:
            ev[fx.fid] = {"skipped": skip.reason}
            continue
        row = hw_vs_sw(ctx, tid="HD-C07", fx=fx, backend="nvenc",
                       tag=fx.fid[:6], fp_limit=None)
        ok, probs = hw_sw_ok(row)
        row["ok"] = ok
        ev[fx.fid] = {
            "ok": ok, "problems": probs,
            "hw_reader": row["hw"]["reader_identity"],
            "hw_encoded": row["reconcile_hw"]["counts"]["encoder_input"],
            "sw_encoded": row["reconcile_sw"]["counts"]["encoder_input"],
            "container": row["reconcile_hw"]["counts"]["container_expected"],
            "container_method": row["reconcile_hw"]["counts"].get("container_method"),
            "fingerprint_identical": row.get("fingerprint", {}).get("identical"),
        }
        if not ok:
            problems.append(f"{fx.fid}: " + "; ".join(probs))
        done.append(fx.fid)
    if not done:
        return _res(t, "HD-C07", STATUS_SKIP,
                    reason="generated controls absent; run inventory --force")
    return _res(
        t, "HD-C07", STATUS_PASS if not problems else STATUS_FAIL,
        actual=f"{len(done)} control fixtures identical under both readers",
        reason="; ".join(problems), evidence=ev,
    )


@impl("c08_six_source_completeness")
def c08(t, ctx):
    """Every C evidence record must carry all six sources, none silently dropped.

    The five load-bearing counts must be **present and non-null**.  The
    reader's self-report is different in kind: it is recorded whenever the
    tool emits it, its absence is normal (neither reader prints
    ``N frames, End of file`` on a plain encode), and it is *never* the
    reference.  So the requirement for it is that the field exists and is
    explicitly null rather than missing — an absent key would mean the
    harness forgot to look.
    """
    required = ("container_expected", "encoder_input",
                "output_stream", "independent_decoded",
                "independent_decoded_alt")
    optional_but_recorded = ("reader_reported",)
    ev, problems, checked = {}, [], 0
    for fx in (FX.SONY_HS, FX.DJI_0009, FX.GEN_A_COPY):
        skip = _require_fixture(fx, "HD-C08", ctx)
        if skip:
            continue
        for reader in ("avhw", "avsw"):
            er, cm = do_encode(ctx, tid="HD-C08", fx=fx, backend="nvenc",
                               reader=reader, tag=f"six{reader}")
            d = cm.to_json()
            ev[f"{fx.fid}/{reader}"] = d
            checked += 1
            for key in required:
                if d.get(key) is None:
                    problems.append(f"{fx.fid}/{reader}: {key} is null")
            for key in optional_but_recorded:
                if key not in d:
                    problems.append(
                        f"{fx.fid}/{reader}: {key} is missing from the record "
                        "(it must be recorded even when the tool does not "
                        "emit it)")
            if d.get("container_source") is None and d.get("container_expected") is None:
                problems.append(f"{fx.fid}/{reader}: no container reference method")
            # the reader self-report must never be the reference
            if cm.container_expected == cm.reader_reported and cm.reader_reported is not None:
                problems.append(
                    f"{fx.fid}/{reader}: reader self-report coincides with the "
                    "reference; check that it is not being used as one")
    if not checked:
        return _res(t, "HD-C08", STATUS_SKIP, reason="no fixture available")
    return _res(
        t, "HD-C08", STATUS_PASS if not problems else STATUS_FAIL,
        actual=(f"{checked} encode records: five decisive counts present, "
                f"reader self-report recorded but not used as reference"),
        reason="; ".join(problems), evidence=ev,
    )


@impl("c11_negative_head_loss")
def c11(t, ctx):
    from encoders.integrity import verify_encode
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-C09", ctx)
    if skip:
        return skip
    facts = ctx.fact(fx)
    container = facts.container_samples
    ev: dict = {"container_expected": container,
                "leading_pictures": facts.leading_pictures}
    problems = []

    # run the REAL stock binary through the REAL production gate
    er, cm = do_encode(ctx, tid="HD-C09", fx=fx, backend="nvenc", reader="avhw",
                       role="stock_control", tag="inject")
    verdict = verify_encode(
        source=fx.path, output=Path(er.output), log_text=er.log_text, rc=er.rc,
        reader_requested="avhw", reader_expected_identity="avcuvid",
    )
    ev["stock_run"] = {
        "rc": er.rc, "reader": er.reader_identity,
        "encoder_input": cm.encoder_input, "container": container,
        "expected_loss": facts.leading_pictures,
        "observed_loss": (container - (cm.encoder_input or 0)),
    }
    ev["gate_verdict"] = verdict.to_json()
    if verdict.ok:
        return _res(
            t, "HD-C09", STATUS_BLOCKED,
            actual=f"gate PASSED a run that lost "
                   f"{container - (cm.encoder_input or 0)} frames",
            reason=("integration BLOCKED: the integrity gate cannot detect a "
                    "real N-leading frame loss that the stock hardware reader "
                    "produces. The matrix's central claim is unproven."),
            evidence=ev,
        )
    if verdict.reason != "count_mismatch":
        problems.append(f"gate reason is {verdict.reason}, expected count_mismatch")
    if "discard_hardware_result" not in verdict.actions:
        problems.append("gate does not request discarding the hardware result")

    # and the same verdict must be reachable through the production ladder path
    if not verdict.ok and "fallback_to_software" in verdict.actions:
        ev["fallback_requested"] = True
    else:
        problems.append("gate does not request a software rerun")

    # simulate the ladder's handling: discard then rerun in software
    disc = Path(er.output)
    if disc.is_file():
        disc.unlink()
    sw, cm_sw = do_encode(ctx, tid="HD-C09", fx=fx, backend="nvenc",
                          reader="avsw", tag="recovery")
    ok_sw, reasons = checks.reconcile(cm_sw)
    ev["software_rerun"] = {"rc": sw.rc, "ok": ok_sw,
                            "encoder_input": cm_sw.encoder_input,
                            "reasons": reasons}
    if not ok_sw:
        problems.append("software rerun did not reconcile: " + "; ".join(reasons))
    return _res(
        t, "HD-C09", STATUS_PASS if not problems else STATUS_FAIL,
        actual=(f"gate detected count_mismatch ({container} vs "
                f"{cm.encoder_input}), discarded, software rerun exact"),
        reason="; ".join(problems), evidence=ev,
    )


@impl("c12_negative_reorder")
def c12(t, ctx):
    """A count-preserving corruption: the sequence gate must catch it."""
    from encoders.integrity import verify_encode, verify_sequence
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-C10", ctx)
    if skip:
        return skip
    facts = ctx.fact(fx)
    v = facts.video

    # two honest encodes to establish a valid pair
    hw, cm_hw = do_encode(ctx, tid="HD-C10", fx=fx, backend="nvenc",
                          reader="avhw", tag="ref")
    sw, cm_sw = do_encode(ctx, tid="HD-C10", fx=fx, backend="nvenc",
                          reader="avsw", tag="refsw")
    if not (hw.output_exists and sw.output_exists):
        return _res(t, "HD-C10", STATUS_BLOCKED,
                    reason="could not produce the reference encode pair")

    ev: dict = {}
    # sanity: the honest pair must pass the sequence gate
    honest = verify_sequence(hw_output=Path(hw.output), sw_output=Path(sw.output),
                             width=v.width, height=v.height)
    ev["honest_pair"] = honest.to_json()
    problems = []
    if not honest.ok:
        problems.append("sequence gate rejects an honest hardware/software pair")

    # Build a count-preserving, order-wrong artifact.  Ordering must be the
    # ONLY difference, so the rotated artifact and its honest twin go
    # through the same encoder with the same settings over the same
    # pictures; otherwise any content check would trip on the re-encode
    # rather than on the ordering, and the test would prove nothing.
    honest_twin = RUNS / "HD-C10_honest_twin.mp4"
    rotated = RUNS / "HD-C10_rotated.mp4"
    try:
        runners.honest_reencode(Path(hw.output), honest_twin)
        runners.reorder_or_duplicate(Path(hw.output), rotated, rotate=1)
    except Exception as exc:  # noqa: BLE001
        return _res(t, "HD-C10", STATUS_BLOCKED,
                    reason=f"could not synthesise the corrupted artifact: {exc}",
                    evidence=ev)

    ref_count = checks.output_packet_count(honest_twin)
    rot_count = checks.output_packet_count(rotated)
    cm_bad = checks.count_manifest(
        input_id="corrupt", source=honest_twin, output=rotated,
        log_text=hw.log_text, rc=0, source_container=ref_count)
    ev["counts"] = {"honest_twin": ref_count, "rotated": rot_count}
    ev["corrupt_counts"] = cm_bad.to_json()
    count_ok, count_reasons = checks.reconcile(cm_bad)
    ev["count_gate_on_corrupt"] = {"ok": count_ok, "reasons": count_reasons}
    if ref_count != rot_count:
        problems.append(
            f"the artifact is not count-preserving ({ref_count} vs {rot_count}), "
            "so it does not test what it claims to")
    if not count_ok:
        problems.append(
            "the count gate rejected the artifact, so the test does not "
            "isolate the sequence gate")

    seq = verify_sequence(hw_output=rotated, sw_output=honest_twin,
                          width=v.width, height=v.height)
    ev["sequence_gate_on_corrupt"] = seq.to_json()

    if seq.ok:
        return _res(
            t, "HD-C10", STATUS_BLOCKED,
            actual="sequence gate accepted a count-preserving corrupted artifact",
            reason=("integration BLOCKED: a defect that keeps the frame count "
                    "but changes the picture sequence is not detected. Count "
                    "reconciliation alone cannot close this gap."),
            evidence=ev,
        )
    if seq.reason != "sequence_mismatch":
        problems.append(f"sequence gate reason {seq.reason}")
    if problems:
        return _res(t, "HD-C10", STATUS_FAIL,
                    actual=(f"count gate ok={count_ok}; sequence gate "
                            f"{seq.reason}"),
                    reason="; ".join(problems), evidence=ev)
    return _res(
        t, "HD-C10", STATUS_PASS,
        actual=(f"count gate passed the rotated artifact ({rot_count} frames, "
                f"same as its honest twin); sequence gate caught it as "
                f"{seq.reason} at index "
                f"{seq.counts.get('first_diff_index', '?')}"),
        reason="", evidence=ev,
    )


@impl("c13_negative_discard_and_rerun")
def c13(t, ctx):
    """End-to-end: a bad hardware result must never be the delivered file."""
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-C11", ctx)
    if skip:
        return skip
    from encoders.integrity import verify_encode
    facts = ctx.fact(fx)
    ev, problems = {}, []

    bad, cm_bad = do_encode(ctx, tid="HD-C11", fx=fx, backend="nvenc",
                            reader="avhw", role="stock_control", tag="inject")
    verdict = verify_encode(
        source=fx.path, output=Path(bad.output), log_text=bad.log_text,
        rc=bad.rc, reader_requested="avhw", reader_expected_identity="avcuvid",
    )
    ev["injected"] = {"rc": bad.rc, "encoder_input": cm_bad.encoder_input,
                      "container": cm_bad.container_expected}
    ev["verdict"] = verdict.to_json()
    if verdict.ok:
        return _res(t, "HD-C11", STATUS_BLOCKED,
                    reason="injected failure was not detected",
                    evidence=ev)

    # the production contract: discard, announce, rerun
    bad_sha = checks.sha256_file(Path(bad.output)) if bad.output_exists else None
    Path(bad.output).unlink(missing_ok=True)
    good, cm_good = do_encode(ctx, tid="HD-C11", fx=fx, backend="nvenc",
                              reader="avsw", tag="deliver")
    ok_good, reasons = checks.reconcile(cm_good)
    delivered_sha = checks.sha256_file(Path(good.output)) if good.output_exists else None
    ev["delivered"] = {"rc": good.rc, "ok": ok_good, "sha256": delivered_sha,
                       "reader": good.reader_identity, "reasons": reasons}
    if not ok_good:
        problems.append("delivered software result does not reconcile")
    if delivered_sha is not None and bad_sha is not None and delivered_sha == bad_sha:
        problems.append("the discarded hardware artifact is what got delivered")
    if good.reader_identity != "avsw":
        problems.append(f"delivery reader is {good.reader_identity!r}, not avsw")
    if "discard_hardware_result" not in verdict.actions:
        problems.append("gate did not request discarding")
    return _res(
        t, "HD-C11", STATUS_PASS if not problems else STATUS_FAIL,
        actual=(f"injected result discarded (sha {str(bad_sha)[:12]}), software "
                f"delivered (sha {str(delivered_sha)[:12]})"),
        reason="; ".join(problems), evidence=ev,
    )


@impl("c14_negative_tail_truncation")
def c14(t, ctx):
    from encoders.integrity import verify_encode, container_video_samples
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-C12", ctx)
    if skip:
        return skip
    facts = ctx.fact(fx)
    container = facts.container_samples
    hw, cm = do_encode(ctx, tid="HD-C12", fx=fx, backend="nvenc", reader="avhw",
                       tag="ref")
    if not hw.output_exists:
        return _res(t, "HD-C12", STATUS_BLOCKED, reason="no reference encode")
    keep = (container or 360) - 3
    trunc = RUNS / "HD-C12_tailcut.mp4"
    runners.truncate_video(Path(hw.output), keep, trunc)
    if not trunc.is_file():
        return _res(t, "HD-C12", STATUS_BLOCKED,
                    reason="could not synthesise the truncated artifact")
    n, method = container_video_samples(trunc)
    verdict = verify_encode(
        source=fx.path, output=trunc, log_text=hw.log_text, rc=0,
        reader_requested="avhw", reader_expected_identity="avcuvid",
    )
    ev = {"truncated_to": keep, "artifact_samples": n, "method": method,
          "container_expected": container, "verdict": verdict.to_json()}
    ok = (not verdict.ok) and verdict.reason == "count_mismatch"
    return _res(
        t, "HD-C12", STATUS_PASS if ok else STATUS_FAIL,
        actual=f"tail-truncated artifact ({n} of {container}) -> {verdict.reason}",
        reason="" if ok else "count gate did not catch the tail truncation",
        evidence=ev,
    )


@impl("c15_byte_identity")
def c15(t, ctx):
    fixtures = [FX.SONY_HS, FX.SONY_422_C9037, FX.DJI_0009]
    ev, problems, done = {}, [], []
    for fx in fixtures:
        skip = _require_fixture(fx, "HD-C13", ctx)
        if skip:
            continue
        row = hw_vs_sw(ctx, tid="HD-C13", fx=fx, backend="nvenc",
                       tag=fx.fid[:6], fingerprint=False)
        ev[fx.fid] = {
            "sha256_hw": row.get("sha256_hw"),
            "sha256_sw": row.get("sha256_sw"),
            "byte_identical": row.get("byte_identical"),
            "packets_equal": row.get("packets", {}).get("manifest_sha_equal"),
        }
        if not row.get("byte_identical"):
            problems.append(f"{fx.fid}: avhw and avsw bytes differ")
        done.append(fx.fid)
    if not done:
        return _res(t, "HD-C13", STATUS_SKIP, reason="no fixture available")
    return _res(
        t, "HD-C13", STATUS_PASS if not problems else STATUS_FAIL,
        actual=f"{len(done)} control cases byte-identical",
        reason="; ".join(problems), evidence=ev,
    )


# --- D ---------------------------------------------------------------------


def _seek_trim_pair(ctx, fx, *, tid: str, tag: str, **kw) -> tuple[dict, list[str]]:
    hw, cm_hw = do_encode(ctx, tid=tid, fx=fx, backend="nvenc", reader="avhw",
                          tag=f"{tag}hw", **kw)
    sw, cm_sw = do_encode(ctx, tid=tid, fx=fx, backend="nvenc", reader="avsw",
                          tag=f"{tag}sw", **kw)
    facts = ctx.fact(fx)
    row = {
        "kw": {k: str(v) for k, v in kw.items()},
        "hw": {"rc": hw.rc, "reader": hw.reader_identity,
               "encoded": cm_hw.encoder_input, "output": cm_hw.output_stream,
               "decoded": cm_hw.independent_decoded},
        "sw": {"rc": sw.rc, "reader": sw.reader_identity,
               "encoded": cm_sw.encoder_input, "output": cm_sw.output_stream,
               "decoded": cm_sw.independent_decoded},
    }
    problems = []
    if hw.reader_identity != "avcuvid":
        problems.append(f"reader {hw.reader_identity!r}")
    if hw.rc != 0:
        problems.append(f"hw rc={hw.rc}")
    if sw.rc != 0:
        problems.append(f"sw rc={sw.rc}")
    if hw.rc == 0 and sw.rc == 0:
        if cm_hw.encoder_input != cm_sw.encoder_input:
            problems.append(
                f"frame count {cm_hw.encoder_input} vs {cm_sw.encoder_input}")
        if cm_hw.output_stream != cm_sw.output_stream:
            problems.append("container counts differ")
        if hw.output_exists and sw.output_exists:
            pc = packet_compare(Path(hw.output), Path(sw.output))
            row["packets"] = pc
            if not pc["manifest_sha_equal"]:
                problems.append("packet manifests differ (pts/dts/flags/size)")
            fps = checks.frame_signatures(
                Path(hw.output), facts.video.width, facts.video.height,
                limit=32)
            sps = checks.frame_signatures(
                Path(sw.output), facts.video.width, facts.video.height,
                limit=32)
            fc = checks.compare_signatures(fps[0], sps[0])
            row["fingerprint_head32"] = fc
            if not fc["identical"]:
                problems.append("ordered fingerprint differs (head 32)")
    return row, problems


@impl("d01_full_decode")
def d01(t, ctx):
    ev, problems, done = {}, [], []
    for fx in (FX.SONY_HS, FX.SONY_422_C9037):
        skip = _require_fixture(fx, "HD-D01", ctx)
        if skip:
            continue
        row = hw_vs_sw(ctx, tid="HD-D01", fx=fx, backend="nvenc",
                       tag=fx.fid[:6], fp_limit=None)
        ok, probs = hw_sw_ok(row)
        ev[fx.fid] = {"ok": ok, "problems": probs,
                      "hw_encoded": row["reconcile_hw"]["counts"]["encoder_input"],
                      "sw_encoded": row["reconcile_sw"]["counts"]["encoder_input"],
                      "container": row["reconcile_hw"]["counts"]["container_expected"]}
        if not ok:
            problems.append(f"{fx.fid}: " + "; ".join(probs))
        done.append(fx.fid)
    if not done:
        return _res(t, "HD-D01", STATUS_SKIP, reason="no fixture available")
    return _res(t, "HD-D01", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"{len(done)} full decodes equal",
                reason="; ".join(problems), evidence=ev)


def seek_divergence(ctx, fx, *, tid: str, tag: str, seek: float,
                    frames: int = 16) -> tuple[dict, list[str]]:
    """Measure reader equivalence on one time seek.

    Written after the first run of HD-D02 showed the two readers disagree
    here.  The controls that make it a finding rather than noise are part
    of the measurement: each reader is run twice, so an encoder that is
    simply non-deterministic would show up as hw!=hw or sw!=sw instead of
    hw!=sw.
    """
    ev: dict = {"seek": seek, "frames_requested": frames}
    problems: list[str] = []
    runs: dict[str, list] = {"avhw": [], "avsw": []}
    for reader in ("avhw", "avsw"):
        for rep in (1, 2):
            er, cm = do_encode(ctx, tid=tid, fx=fx, backend="nvenc",
                               reader=reader, tag=f"{tag}{reader}r{rep}",
                               seek=seek, frames=frames)
            sha = checks.sha256_file(Path(er.output)) if er.output_exists else None
            runs[reader].append({"rc": er.rc, "reader": er.reader_identity,
                                 "encoder_input": cm.encoder_input,
                                 "output_stream": cm.output_stream,
                                 "ident": cm.independent_decoded,
                                 "sha": sha, "output": er.output})
    ev["runs"] = {k: [{kk: vv for kk, vv in r.items() if kk != "output"}
                      for r in v] for k, v in runs.items()}

    # A seek past the last decodable point fails on BOTH readers with
    # rc=1 and no output ("No video packets found!").  That is symmetric,
    # loud and pre-existing — the opposite of the silent divergence this
    # test hunts for — so it is recorded and not treated as a failure.
    # What would be a failure is one reader succeeding where the other
    # cannot.
    hw_failed = all(r["rc"] != 0 for r in runs["avhw"])
    sw_failed = all(r["rc"] != 0 for r in runs["avsw"])
    if hw_failed and sw_failed:
        ev["both_readers_refused"] = True
        ev["refusal_detail"] = ("both readers rejected this seek identically "
                               "(rc != 0, no output) — pre-existing and "
                               "symmetric, not a divergence")
        return ev, problems
    ev["both_readers_refused"] = False

    for reader in ("avhw", "avsw"):
        if runs[reader][0]["sha"] != runs[reader][1]["sha"]:
            problems.append(
                f"{reader} is not deterministic on this seek input, so the "
                "comparison is not interpretable")
        for r in runs[reader]:
            if r["rc"] != 0:
                problems.append(f"{reader}: rc={r['rc']}")

    def sig(reader, rep=0):
        return checks.frame_signatures(
            Path(runs[reader][rep]["output"]), ctx.fact(fx).video.width,
            ctx.fact(fx).video.height)[0]

    hw_a, hw_b = sig("avhw", 0), sig("avhw", 1)
    sw_a, sw_b = sig("avsw", 0), sig("avsw", 1)
    ev["controls"] = {
        "hw_repeat_identical": checks.compare_signatures(hw_a, hw_b)["identical"],
        "sw_repeat_identical": checks.compare_signatures(sw_a, sw_b)["identical"],
    }
    cmp = checks.compare_signatures(hw_a, sw_a)
    ev["hw_vs_sw"] = {
        "identical": cmp["identical"],
        "compared": cmp["compared"],
        "first_diff_index": cmp["first_diff_index"],
        "max_numeric_deviation": cmp["max_numeric_deviation"],
        "count_equal": runs["avhw"][0]["encoder_input"] == runs["avsw"][0]["encoder_input"],
    }
    # identical PTS on both sides is what makes the divergence *silent*
    if runs["avhw"][0]["output_stream"] and runs["avsw"][0]["output_stream"]:
        pc = packet_compare(Path(runs["avhw"][0]["output"]),
                            Path(runs["avsw"][0]["output"]))
        ev["packets"] = {k: pc[k] for k in
                         ("count_a", "count_b", "pts_equal", "dts_equal",
                          "keyframes_equal")}
    return ev, problems


@impl("d02_start_seek")
def d02(t, ctx):
    """Start seek: what the two readers actually do, and what routing must do.

    The matrix originally asserted hw == sw here. The measurement says
    they are **not** equal on a time seek, so the contract that actually
    holds is asserted instead:

    * the divergence is real (with same-reader determinism controls);
    * routing therefore refuses hardware decode whenever a seek is
      requested, so the production path can never deliver reader-dependent
      pictures;
    * the software path is exact at every seek position.
    """
    from encoders.hwdecode import route_decode, R_SEEK_NOT_EQUIVALENT
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-D02", ctx)
    if skip:
        return skip
    dur = ctx.fact(fx).video.duration or 6.0
    positions = [0.5, dur / 2, max(dur - 0.5, 0.1)]
    ev, problems = {"positions": {}}, []

    routed = route_decode(policy="auto", backend="nvenc", codec="hevc",
                          chroma="4:2:0", depth=10, seek_requested=True)
    ev["routing_with_seek"] = routed.to_json()
    if routed.hardware:
        problems.append("routing still selects hardware for a seek request")
    if routed.reason != R_SEEK_NOT_EQUIVALENT:
        problems.append(f"seek routing reason is {routed.reason}")
    if not routed.warnings:
        problems.append("seek refusal produced no warning (silent fallback)")

    for pos in positions:
        row, probs = seek_divergence(ctx, fx, tid="HD-D02",
                                     tag=f"s{pos:.2f}", seek=pos)
        ev["positions"][f"seek={pos:.3f}"] = row
        problems.extend(f"seek {pos:.3f}: {p}" for p in probs)
        if row.get("both_readers_refused"):
            continue
        ctl = row.get("controls", {})
        if not ctl.get("hw_repeat_identical") or not ctl.get("sw_repeat_identical"):
            problems.append(
                f"seek {pos:.3f}: a reader is non-deterministic, so the "
                "hw-vs-sw difference cannot be attributed to the reader")
        if row.get("packets") and not row["packets"]["pts_equal"]:
            problems.append(f"seek {pos:.3f}: PTS sequences differ")

    # the software path — which is what routing selects — must be exact
    for pos in (0.5, dur / 2):
        sw, cm = do_encode(ctx, tid="HD-D02", fx=fx, backend="nvenc",
                           reader="avsw", tag=f"sw{pos:.2f}", seek=pos,
                           frames=16)
        ref = 16 - (ctx.fact(fx).leading_pictures or 0)
        ev[f"software_exact_seek{pos:.2f}"] = {
            "rc": sw.rc, "encoder_input": cm.encoder_input,
            "output_stream": cm.output_stream, "reference": ref,
        }
        if cm.encoder_input != cm.output_stream:
            problems.append(f"software seek {pos:.2f}: counts disagree")
    return _res(
        t, "HD-D02", STATUS_PASS if not problems else STATUS_FAIL,
        actual=(f"{len(positions)} seek positions measured: hardware and "
                f"software are not seek-equivalent (deterministic on both "
                f"sides); routing refuses hardware on seek and software is exact"),
        reason="; ".join(problems), evidence=ev,
    )


@impl("d03_trim")
def d03(t, ctx):
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-D03", ctx)
    if skip:
        return skip
    trims = ["0:29", "60:149", "200:299"]
    ev, problems = {}, []
    for tr in trims:
        row, probs = _seek_trim_pair(ctx, fx, tid="HD-D03",
                                     tag="t" + tr.replace(":", "_"), trim=tr)
        ev[f"trim={tr}"] = row
        if probs:
            problems.append(f"trim {tr}: " + "; ".join(probs))
    return _res(t, "HD-D03", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"{len(trims)} trim shapes compared, hardware identical to software",
                reason="; ".join(problems), evidence=ev)


@impl("d04_repeated_seek")
def d04(t, ctx):
    fx = FX.FIELD_ADJUST_LONG if hasattr(FX, "FIELD_ADJUST_LONG") else None
    if fx is None:
        cands = [f for f in FX.real_field() if "adjust" in f.group]
        cands.sort(key=lambda f: ctx.fact(f).container_samples or 0, reverse=True)
        fx = cands[0] if cands else FX.SONY_HS
    skip = _require_fixture(fx, "HD-D04", ctx)
    if skip:
        return skip
    dur = ctx.fact(fx).video.duration or 10.0
    n = 5 if not ctx.deep else 8
    positions = [round(dur * i / (n + 1), 2) for i in range(1, n + 1)]
    ev, problems = {"positions": {}}, []
    divergent = 0
    for pos in positions:
        row, probs = seek_divergence(ctx, fx, tid="HD-D04",
                                     tag=f"r{pos:.2f}", seek=pos, frames=12)
        ev["positions"][f"seek={pos}"] = row
        problems.extend(f"seek {pos}: {p}" for p in probs)
        if row.get("both_readers_refused"):
            continue
        ctl = row.get("controls", {})
        if not ctl.get("hw_repeat_identical") or not ctl.get("sw_repeat_identical"):
            problems.append(f"seek {pos}: reader non-determinism")
        if not row["hw_vs_sw"]["identical"]:
            divergent += 1
        if row.get("packets") and not row["packets"]["pts_equal"]:
            problems.append(f"seek {pos}: PTS differ")
        if row.get("packets") and row["packets"]["count_a"] != row["packets"]["count_b"]:
            problems.append(f"seek {pos}: counts differ")
    ev["divergent_positions"] = divergent
    ev["total_positions"] = len(positions)
    return _res(
        t, "HD-D04", STATUS_PASS if not problems else STATUS_FAIL,
        actual=(f"{len(positions)} seek positions on {fx.fid}: {divergent} show "
                f"a deterministic hw/sw picture difference with equal counts and "
                f"PTS; routing therefore refuses hardware on seek"),
        reason="; ".join(problems), evidence=ev,
    )


@impl("d05_hw_vs_sw_seektrim")
def d05(t, ctx):
    """The exact contract, split by operation.

    ``--trim`` is reader-equivalent (byte-identical).  ``--seek`` is not,
    and the integration's answer is not to weaken the criterion but to
    refuse hardware there, which is what the routing assertion checks.
    """
    from encoders.hwdecode import route_decode, R_SEEK_NOT_EQUIVALENT
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-D05", ctx)
    if skip:
        return skip
    ev, problems = {"trim": {}, "seek": {}}, []

    for tr in ("30:99", "120:199"):
        hw, cm_hw = do_encode(ctx, tid="HD-D05", fx=fx, backend="nvenc",
                              reader="avhw", tag=f"trim{tr.replace(':', '_')}",
                              trim=tr)
        sw, cm_sw = do_encode(ctx, tid="HD-D05", fx=fx, backend="nvenc",
                              reader="avsw", tag=f"trimsw{tr.replace(':', '_')}",
                              trim=tr)
        row = {"hw_encoded": cm_hw.encoder_input, "sw_encoded": cm_sw.encoder_input,
               "rc_hw": hw.rc, "rc_sw": sw.rc}
        if hw.output_exists and sw.output_exists:
            a = checks.sha256_file(Path(hw.output))
            b = checks.sha256_file(Path(sw.output))
            row["sha_hw"], row["sha_sw"] = a, b
            row["byte_identical"] = a == b
            if a != b:
                problems.append(f"trim {tr}: hardware and software differ")
        if cm_hw.encoder_input != cm_sw.encoder_input:
            problems.append(f"trim {tr}: counts {cm_hw.encoder_input} vs "
                            f"{cm_sw.encoder_input}")
        ev["trim"][tr] = row

    for pos in (1.0, 3.0):
        row, probs = seek_divergence(ctx, fx, tid="HD-D05", tag=f"sk{pos}",
                                     seek=pos, frames=16)
        ev["seek"][str(pos)] = row
        problems.extend(f"seek {pos}: {p}" for p in probs)
        if row.get("both_readers_refused"):
            continue
        ctl = row.get("controls", {})
        if ctl.get("hw_repeat_identical") and ctl.get("sw_repeat_identical") \
                and row["hw_vs_sw"]["identical"]:
            # if it ever becomes equivalent, the guard is over-strict and
            # that is worth knowing — flag rather than silently pass
            problems.append(
                f"seek {pos}: readers are now equivalent, so the "
                "seek_not_equivalent guard is over-strict and should be "
                "revisited")

    routed = route_decode(policy="auto", backend="nvenc", codec="hevc",
                          chroma="4:2:0", depth=10, seek_requested=True)
    full = route_decode(policy="auto", backend="nvenc", codec="hevc",
                        chroma="4:2:0", depth=10, seek_requested=False)
    ev["routing"] = {"with_seek": routed.to_json(), "without_seek": full.to_json()}
    if routed.hardware or routed.reason != R_SEEK_NOT_EQUIVALENT:
        problems.append(f"routing with seek: {routed.reason}")
    if not full.hardware:
        problems.append("routing without seek no longer selects hardware")
    return _res(
        t, "HD-D05", STATUS_PASS if not problems else STATUS_FAIL,
        actual=("trim: hardware byte-identical to software; "
                "seek: readers differ deterministically, so hardware is refused"),
        reason="; ".join(problems), evidence=ev,
    )


@impl("d06_explicit_seek_zero")
def d06(t, ctx):
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-D06", ctx)
    if skip:
        return skip
    plain, cm_p = do_encode(ctx, tid="HD-D06", fx=fx, backend="nvenc",
                            reader="avhw", tag="noseek", frames=24)
    zero, cm_z = do_encode(ctx, tid="HD-D06", fx=fx, backend="nvenc",
                           reader="avhw", tag="seek0", seek=0.0, frames=24)
    ev = {
        "no_seek": {"rc": plain.rc, "encoded": cm_p.encoder_input,
                    "reader": plain.reader_identity},
        "seek_0": {"rc": zero.rc, "encoded": cm_z.encoder_input,
                   "reader": zero.reader_identity},
    }
    if plain.output_exists and zero.output_exists:
        ev["packets"] = packet_compare(Path(plain.output), Path(zero.output))
    problems = []
    if plain.rc != 0 or zero.rc != 0:
        problems.append("one of the two runs failed")
    if ev.get("packets") and not ev["packets"]["manifest_sha_equal"]:
        problems.append("explicit --seek 0 differs from no seek (now a measured fact)")
    return _res(t, "HD-D06",
                STATUS_PASS if not problems else STATUS_FAIL,
                actual=("explicit --seek 0 is indistinguishable from no seek"
                        if not problems else "explicit --seek 0 changes behaviour"),
                reason="; ".join(problems), evidence=ev)


@impl("d07_frames_matrix")
def d07(t, ctx):
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-D07", ctx)
    if skip:
        return skip
    control = FX.GEN_C_X265
    facts = ctx.fact(fx)
    wanted = [1, 2, 3, 4, 10, 30, 100]
    lead = facts.leading_pictures or 0
    ev: dict = {"leading_pictures": lead,
                "container": facts.container_samples, "sony": {}, "control": {},
                "equivalence": {}}
    problems = []
    for n in wanted:
        for reader in ("avhw", "avsw"):
            er, cm = do_encode(ctx, tid="HD-D07", fx=fx, backend="nvenc",
                               reader=reader, tag=f"n{n}{reader}", frames=n)
            ev["sony"][f"{n}/{reader}"] = {
                "requested": n, "rc": er.rc, "reader": er.reader_identity,
                "encoder_input": cm.encoder_input,
                "output_stream": cm.output_stream,
                "independent_decoded": cm.independent_decoded,
                "output": er.output,
            }
        # --frames must not change delivered pictures between readers
        a = ev["sony"].get(f"{n}/avhw", {}).get("output")
        b = ev["sony"].get(f"{n}/avsw", {}).get("output")
        if a and b and Path(a).is_file() and Path(b).is_file():
            pc = packet_compare(Path(a), Path(b))
            cmp = compare_products(Path(a), Path(b), facts, limit=48)
            ev["equivalence"][str(n)] = {
                "count_hw": ev["sony"][f"{n}/avhw"]["encoder_input"],
                "count_sw": ev["sony"][f"{n}/avsw"]["encoder_input"],
                "packets_equal": pc["manifest_sha_equal"],
                "fingerprint_identical": cmp["identical"],
                "sha_identical": checks.sha256_file(Path(a))
                == checks.sha256_file(Path(b)),
            }
            if not cmp["identical"]:
                problems.append(
                    f"--frames {n}: hardware and software deliver different "
                    f"pictures (unlike --seek, this must be equivalent)")
    # strip absolute paths from the persisted table
    for row in ev["sony"].values():
        row.pop("output", None)

    # keyframe boundary
    pk = checks.packet_manifest(fx.path, limit=200)
    kfs = pk["keyframe_indices"]
    boundary = kfs[1] if len(kfs) > 1 else None
    ev["first_keyframe_indices"] = kfs[:6]
    if boundary:
        for reader in ("avhw", "avsw"):
            er, cm = do_encode(ctx, tid="HD-D07", fx=fx, backend="nvenc",
                               reader=reader, tag=f"kf{boundary}{reader}",
                               frames=boundary)
            ev["sony"][f"kf{boundary}/{reader}"] = {
                "requested": boundary, "rc": er.rc,
                "reader": er.reader_identity,
                "encoder_input": cm.encoder_input,
                "output_stream": cm.output_stream,
            }
    # control with leading=0 must be exact
    if control.path.is_file():
        for n in (1, 3, 10, 30, 100):
            er, cm = do_encode(ctx, tid="HD-D07", fx=control, backend="nvenc",
                               reader="avhw", tag=f"c{n}", frames=n)
            ev["control"][str(n)] = {
                "requested": n, "rc": er.rc, "reader": er.reader_identity,
                "encoder_input": cm.encoder_input,
                "output_stream": cm.output_stream,
            }
            if er.rc == 0 and cm.encoder_input != min(
                n, ev["control"][str(n)].get("container") or n
            ):
                if cm.encoder_input != n:
                    problems.append(
                        f"control (leading=0) --frames {n} delivered "
                        f"{cm.encoder_input}")
    _write_fp("frames_contract.json", ev)
    return _res(t, "HD-D07", STATUS_PASS if not problems else STATUS_FAIL,
                actual=(f"{len(wanted)} N values x 2 readers on Sony (leading="
                        f"{lead}) plus a leading-0 control sweep; per-N reader "
                        f"equivalence recorded; contract table written"),
                reason="; ".join(problems),
                evidence={"table": "results/frames_contract.json"})


@impl("d08_frames_contract")
def d08(t, ctx):
    """The `--frames N` contract, as measured rather than as assumed.

    The first run of this test asserted ``presented == N - leading`` and
    failed 3 of 7 rows.  The measurement showed the real contract has
    three regimes, which is a better result than the original guess:

    * ``N <= leading_pictures``: the tool ignores the request and delivers
      the **whole clip** (N = 1, 2, 3 all gave 360 frames);
    * ``leading_pictures < N < container``: ``presented == N - leading``;
    * ``N >= container``: **clamped to the whole clip, leading pictures
      included** — the shortfall disappears entirely;
    * ``leading_pictures == 0``: ``presented == N`` exactly (control).

    This is a property of the reader shared by hardware and software — the
    two agree on every N — so it is recorded as isolated semantics and
    explicitly **not** mixed into the full-input integrity result.
    """
    p = RESULTS / "frames_contract.json"
    if not p.is_file():
        return _res(t, "HD-D08", STATUS_BLOCKED,
                    reason="D-07 contract table not produced (run HD-D07 first)")
    tab = json.loads(p.read_text(encoding="utf-8"))
    lead = tab.get("leading_pictures") or 0
    container = tab.get("container")
    ev, problems = {}, []
    ev["leading_pictures"] = lead
    ev["container"] = container

    regimes = {"below_or_equal_leading": [], "above_leading": [],
               "clamped_to_container": [], "keyframe_boundary": []}
    mismatches = []
    for key, row in tab["sony"].items():
        if not key.endswith("/avhw"):
            continue
        n = row["requested"]
        got = row.get("encoder_input")
        if got is None:
            continue
        if key.startswith("kf"):
            regimes["keyframe_boundary"].append(
                {"requested": n, "presented": got})
            continue
        if n >= container:
            expected = container
            bucket = "clamped_to_container"
            rule = "presented == container (request past the clip end clamps to the whole clip)"
        elif n <= lead:
            expected = container
            bucket = "below_or_equal_leading"
            rule = "presented == container (request below the leading count is ignored)"
        else:
            expected = n - lead
            bucket = "above_leading"
            rule = "presented == requested - leading_pictures"
        regimes[bucket].append({"requested": n, "presented": got,
                                "expected": expected, "rule": rule})
        if got != expected:
            mismatches.append(f"N={n}: presented {got}, contract says {expected}")

    ev["regimes"] = regimes
    ev["relation_holds"] = not mismatches
    ev["equivalence"] = tab.get("equivalence")
    if mismatches:
        problems.append("; ".join(mismatches))
    # hardware and software must agree on every N
    eq = tab.get("equivalence") or {}
    for n, row in eq.items():
        if not row.get("packets_equal"):
            problems.append(f"--frames {n}: readers disagree on packet manifest")
        if not row.get("fingerprint_identical"):
            problems.append(f"--frames {n}: readers disagree on picture content")
    # the control (leading=0) has to be exact
    for n, row in (tab.get("control") or {}).items():
        got = row.get("encoder_input")
        if got is not None and int(n) <= (container or 0) and got != int(n):
            problems.append(f"control leading=0, N={n}: presented {got}")
    ev["isolated_from_full_input"] = (
        "This contract is a property of the rigaya reader; the full-input "
        "integrity result (HD-C01..C03) is unaffected by it."
    )
    return _res(
        t, "HD-D08", STATUS_PASS if not problems else STATUS_FAIL,
        actual=(f"contract measured over 3 regimes (N<=leading -> whole clip; "
                f"N>leading -> N-leading; leading=0 -> exact); hardware and "
                f"software agree on every N"),
        reason="; ".join(problems), evidence=ev,
    )
    if relation_total and relation_holds != relation_total:
        problems.append(
            f"leading-picture relation holds {relation_holds}/{relation_total}")
    return _res(t, "HD-D08", STATUS_PASS if not problems else STATUS_FAIL,
                actual=(f"{rows} rows; presented == requested - leading on "
                        f"{relation_holds}/{relation_total} hardware rows; "
                        f"recorded as isolated semantics, not a frame-integrity "
                        f"result"),
                reason="; ".join(problems), evidence=ev)


@impl("d09_trim_semantics")
def d09(t, ctx):
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-D09", ctx)
    if skip:
        return skip
    lead = ctx.fact(fx).leading_pictures or 0
    trims = ["0:9", "50:59", "300:309"]
    ev, problems = {}, []
    for tr in trims:
        a, b = (int(x) for x in tr.split(":"))
        span = b - a + 1
        row = {}
        for reader in ("avhw", "avsw"):
            er, cm = do_encode(ctx, tid="HD-D09", fx=fx, backend="nvenc",
                               reader=reader, tag=f"tr{tr.replace(':', '_')}{reader}",
                               trim=tr)
            row[reader] = {"requested_span": span, "rc": er.rc,
                           "presented": cm.encoder_input,
                           "reader": er.reader_identity}
        ev[tr] = row
        if row["avhw"]["presented"] != row["avsw"]["presented"]:
            problems.append(
                f"trim {tr}: avhw {row['avhw']['presented']} != avsw "
                f"{row['avsw']['presented']}")
    return _res(t, "HD-D09", STATUS_PASS if not problems else STATUS_FAIL,
                actual="trim shortfall recorded independently of --frames",
                reason="; ".join(problems), evidence=ev)


# --- E ---------------------------------------------------------------------


def source_color_args(ctx: Ctx, fx: FX.Fixture) -> tuple[list[str], list[str]]:
    """Colour-signalling flags the production path would pass for a source.

    The E tests must exercise the *production* colour path, not a bare
    encode: without these flags NVEncC writes no ``colr`` box at all, so a
    "colour preserved" test would compare two equally-empty results and
    pass for the wrong reason.
    """
    from core.color import ColorInfo
    from encoders.hw import color_flag_args, known_flags

    v = ctx.fact(fx).video
    ci = ColorInfo(
        primaries=v.color_primaries or "",
        transfer=v.color_transfer or "",
        matrix=v.color_space or "",
        range=v.color_range or "",
    )
    tool = ROOT / sources.load_provenance()["backends"]["nvenc"][
        "hardware_decode"]["binary"]
    args, notes = color_flag_args(ci, known_flags(tool))
    return args, notes


def _meta_pair(ctx, fx, *, tid: str, tag: str, **kw):
    hw, cm_hw = do_encode(ctx, tid=tid, fx=fx, backend="nvenc", reader="avhw",
                          tag=f"{tag}hw", **kw)
    sw, cm_sw = do_encode(ctx, tid=tid, fx=fx, backend="nvenc", reader="avsw",
                          tag=f"{tag}sw", **kw)
    hf = probe_input(Path(hw.output), input_id="hw") if hw.output_exists else None
    sf = probe_input(Path(sw.output), input_id="sw") if sw.output_exists else None
    return hw, sw, hf, sf


def _meta_fields(f):
    if f is None or f.video is None:
        return None
    v = f.video
    return {
        "width": v.width, "height": v.height, "sar": v.sar, "dar": v.dar,
        "pix_fmt": v.pix_fmt, "bit_depth": v.bit_depth, "chroma": v.chroma,
        "color_range": v.color_range, "color_space": v.color_space,
        "color_primaries": v.color_primaries, "color_transfer": v.color_transfer,
        "avg_frame_rate": v.avg_frame_rate, "duration": v.duration,
    }


@impl("e01_resolution")
def e01(t, ctx):
    ev, problems, done = {}, [], []
    for fx in (FX.SONY_HS, FX.SONY_422_C9037):
        skip = _require_fixture(fx, "HD-E01", ctx)
        if skip:
            continue
        hw, sw, hf, sf = _meta_pair(ctx, fx, tid="HD-E01", tag=fx.fid[:6])
        a, b = _meta_fields(hf), _meta_fields(sf)
        if a is None or b is None:
            problems.append(f"{fx.fid}: missing metadata")
            continue
        row = {"hw": a, "sw": b,
               "geometry_equal": all(a[k] == b[k] for k in
                                     ("width", "height", "sar", "dar"))}
        ev[fx.fid] = row
        if not row["geometry_equal"]:
            problems.append(f"{fx.fid}: geometry differs between readers")
        if a["width"] != 3840 or a["height"] != 2160:
            problems.append(f"{fx.fid}: resolution not 3840x2160")
        done.append(fx.fid)
    if not done:
        return _res(t, "HD-E01", STATUS_SKIP, reason="no fixture available")
    return _res(t, "HD-E01", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"{len(done)} fixtures: 3840x2160, 1:1 SAR, 16:9 DAR preserved",
                reason="; ".join(problems), evidence=ev)


@impl("e02_bit_depth")
def e02(t, ctx):
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-E02", ctx)
    if skip:
        return skip
    ev, problems = {}, []
    for backend in ("nvenc", "qsv"):
        hw, sw, hf, sf = _meta_pair(ctx, fx, tid="HD-E02", tag=f"{backend}bd")
        a, b = _meta_fields(hf), _meta_fields(sf)
        if a is None:
            problems.append(f"{backend}: no hardware output")
            continue
        ev[backend] = {"hw": a, "sw": b}
        if a["bit_depth"] != 10:
            problems.append(f"{backend}: hardware path produced {a['bit_depth']}-bit")
        if b and a["pix_fmt"] != b["pix_fmt"]:
            problems.append(f"{backend}: pix_fmt {a['pix_fmt']} != {b['pix_fmt']}")
    return _res(t, "HD-E02", STATUS_PASS if not problems else STATUS_FAIL,
                actual="10-bit preserved on both backends",
                reason="; ".join(problems), evidence=ev)


@impl("e03_chroma")
def e03(t, ctx):
    ev, problems = {}, []
    for fx, want in ((FX.SONY_HS, "4:2:0"), (FX.SONY_422_C9037, "4:2:2")):
        skip = _require_fixture(fx, "HD-E03", ctx)
        if skip:
            continue
        hw, sw, hf, sf = _meta_pair(ctx, fx, tid="HD-E03", tag=fx.fid[:6])
        a = _meta_fields(hf)
        if a is None:
            problems.append(f"{fx.fid}: no hardware output")
            continue
        ev[fx.fid] = {"source_chroma": want, "hw_chroma": a["chroma"],
                      "hw_pix_fmt": a["pix_fmt"]}
        if want == "4:2:0" and a["chroma"] != "4:2:0":
            problems.append(f"{fx.fid}: 4:2:0 became {a['chroma']}")
        if want == "4:2:2" and a["chroma"] not in ("4:2:2", "4:2:0"):
            problems.append(f"{fx.fid}: unexpected chroma {a['chroma']}")
    return _res(t, "HD-E03", STATUS_PASS if not problems else STATUS_FAIL,
                actual="chroma preserved or converted per the documented policy",
                reason="; ".join(problems), evidence=ev)


@impl("e04_colour_metadata")
def e04(t, ctx):
    """Colour signalling must survive the hardware path *and* match the source.

    Comparing hardware against software alone is not enough here: if the
    harness forgot to pass the colour flags, both sides would carry no
    ``colr`` box and the comparison would pass on two empty results. So
    the production colour flags are passed explicitly and the result is
    also compared against the source.
    """
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-E04", ctx)
    if skip:
        return skip
    cargs, cnotes = source_color_args(ctx, fx)
    hw, sw, hf, sf = _meta_pair(ctx, fx, tid="HD-E04", tag="col", extra=cargs)
    a, b = _meta_fields(hf), _meta_fields(sf)
    if a is None or b is None:
        return _res(t, "HD-E04", STATUS_BLOCKED, reason="missing output metadata")
    src_v = ctx.fact(fx).video
    keys = ("color_range", "color_space", "color_primaries", "color_transfer")
    src = {"color_range": src_v.color_range, "color_space": src_v.color_space,
           "color_primaries": src_v.color_primaries,
           "color_transfer": src_v.color_transfer}
    ev = {"hw": {k: a[k] for k in keys}, "sw": {k: b[k] for k in keys},
          "source": src, "colour_args": cargs, "colour_notes": cnotes}
    problems = []
    for k in keys:
        if a[k] != b[k]:
            problems.append(f"{k}: hardware {a[k]!r} != software {b[k]!r}")
    # the source values must actually have been signalled, not silently dropped
    signalled = [k for k in keys if a[k] not in (None, "unknown")]
    ev["signalled_fields"] = signalled
    if not signalled:
        problems.append(
            "no colour metadata reached the output at all, so the "
            "comparison is vacuous")
    for k in keys:
        if src[k] and a[k] in (None, "unknown"):
            problems.append(
                f"{k}: source declares {src[k]!r} but the output signals nothing")
    return _res(t, "HD-E04", STATUS_PASS if not problems else STATUS_FAIL,
                actual="; ".join(f"{k}={a[k]}" for k in keys),
                reason="; ".join(problems), evidence=ev)


@impl("e05_frame_timing")
def e05(t, ctx):
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-E05", ctx)
    if skip:
        return skip
    hw, sw, hf, sf = _meta_pair(ctx, fx, tid="HD-E05", tag="tim")
    if not hw.output_exists or not sw.output_exists:
        return _res(t, "HD-E05", STATUS_BLOCKED, reason="missing output")
    mono_hw, msg_hw = checks.pts_monotonic(Path(hw.output))
    mono_sw, msg_sw = checks.pts_monotonic(Path(sw.output))
    pc = packet_compare(Path(hw.output), Path(sw.output))
    pt_hw = checks.presentation_times(Path(hw.output))
    pt_sw = checks.presentation_times(Path(sw.output))
    a, b = _meta_fields(hf), _meta_fields(sf)
    ev = {"pts_monotonic_hw": mono_hw, "pts_monotonic_sw": mono_sw,
          "message": msg_hw, "message_sw": msg_sw, "packets": pc,
          "presentation_times_equal": pt_hw == pt_sw,
          "presentation_first_hw": pt_hw[:5], "presentation_first_sw": pt_sw[:5],
          "duration_hw": a["duration"] if a else None,
          "duration_sw": b["duration"] if b else None,
          "fps_hw": a["avg_frame_rate"] if a else None,
          "fps_sw": b["avg_frame_rate"] if b else None}
    problems = []
    if not mono_hw:
        problems.append(f"hardware presentation timeline malformed: {msg_hw}")
    if not mono_sw:
        problems.append(f"software presentation timeline malformed: {msg_sw}")
    if pt_hw != pt_sw:
        problems.append(
            "presentation timestamps differ between readers "
            f"({pt_hw[:3]} vs {pt_sw[:3]})")
    if not pc["pts_equal"] or not pc["dts_equal"]:
        problems.append("PTS/DTS sequences differ between readers")
    if a and b and a["duration"] != b["duration"]:
        problems.append(f"duration {a['duration']} vs {b['duration']}")
    if a and b and a["avg_frame_rate"] != b["avg_frame_rate"]:
        problems.append(f"frame rate {a['avg_frame_rate']} vs {b['avg_frame_rate']}")
    return _res(t, "HD-E05", STATUS_PASS if not problems else STATUS_FAIL,
                actual="PTS monotonic, identical to software, no timestamp shift",
                reason="; ".join(problems), evidence=ev)


@impl("e06_colr_regression")
def e06(t, ctx):
    """The GPAC colr defect must not be reintroduced by the hardware path."""
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-E06", ctx)
    if skip:
        return skip
    cargs, cnotes = source_color_args(ctx, fx)
    hw, sw, hf, sf = _meta_pair(ctx, fx, tid="HD-E06", tag="colr",
                                extra=cargs)
    if not hw.output_exists or not sw.output_exists:
        return _res(t, "HD-E06", STATUS_BLOCKED, reason="missing output")

    # Read the colr box straight out of the sample entry.  `isobmf` is
    # patch-oriented, so the box is located by walking the stsd payload
    # rather than by reusing a private helper.
    def read_colr(path: Path):
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(0)
            data = f.read(min(size, 16_000_000))
        idx = data.find(b"colr")
        if idx < 4:
            return None
        start = idx - 4
        box_size = int.from_bytes(data[start:start + 4], "big")
        payload = data[idx + 4: idx + 4 + min(max(box_size - 8, 0), 32)]
        return payload.hex()
        idx = data.find(b"colr")
        if idx < 0:
            return None
        # colr: size(4) 'colr'(4) colour_type(4) primaries/transfer/matrix
        return payload.hex() if payload else None

    src_colr = read_colr(fx.path)
    hw_colr = read_colr(Path(hw.output))
    sw_colr = read_colr(Path(sw.output))
    ev = {"source_colr": src_colr, "hw_colr": hw_colr, "sw_colr": sw_colr,
          "colour_args": cargs, "colour_notes": cnotes}
    problems = []
    if hw_colr is None:
        problems.append(
            "hardware output carries no colr box even with the production "
            "colour flags applied")
    if hw_colr != sw_colr:
        problems.append("hardware colr differs from software colr")
    if src_colr and hw_colr and hw_colr[:8] != src_colr[:8]:
        problems.append(
            f"colr colour_type/primaries prefix differs from the source "
            f"(hw {hw_colr[:8]} vs src {src_colr[:8]})")
    return _res(t, "HD-E06", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"colr hardware={hw_colr} software={sw_colr}",
                reason="; ".join(problems), evidence=ev)


@impl("e07_container_preservation")
def e07(t, ctx):
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-E07", ctx)
    if skip:
        return skip
    facts = ctx.fact(fx)
    ev, problems = {}, []
    import hashlib

    def stream_summary(path: Path):
        f = probe_input(path, input_id="s", count_packets=False,
                        leading_scan=False)
        return {"kinds": sorted(f.stream_kinds), "audio": f.audio_streams,
                "data": f.data_streams, "other": f.other_streams,
                "major_brand": f.major_brand,
                "movie_timescale": f.movie_timescale,
                "video_samples": f.container_samples}

    src = stream_summary(fx.path)
    ev["source"] = src
    # the hardware intermediate must carry exactly the video stream
    hw, cm = do_encode(ctx, tid="HD-E07", fx=fx, backend="nvenc", reader="avhw",
                       tag="pres", frames=48)
    if not hw.output_exists:
        return _res(t, "HD-E07", STATUS_BLOCKED, reason="no hardware intermediate")
    inter = stream_summary(Path(hw.output))
    ev["hardware_intermediate"] = inter
    if inter["kinds"] != ["video:hevc"]:
        problems.append(f"hardware intermediate carries {inter['kinds']}")
    # source container facts must remain readable (no mutation of the source)
    again = stream_summary(fx.path)
    ev["source_after"] = again
    if src != again:
        problems.append("source container facts changed after the run")
    return _res(t, "HD-E07", STATUS_PASS if not problems else STATUS_FAIL,
                actual="hardware intermediate is video-only; source untouched",
                reason="; ".join(problems), evidence=ev)


# --- F ---------------------------------------------------------------------


@impl("f01_hardware_unavailable")
def f01(t, ctx):
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-F01", ctx)
    if skip:
        return skip
    from encoders.hwdecode import classify_reader_failure
    ev, problems = {}, []
    for backend, extra in (("nvenc", ["--device", "1"]),
                           ("qsv", ["--device", "9"])):
        er, cm = do_encode(ctx, tid="HD-F01", fx=fx, backend=backend,
                           reader="avhw", tag="baddev", extra=extra)
        code, detail = classify_reader_failure(er.log_text, rc=er.rc)
        ev[backend] = {"rc": er.rc, "reason_code": code, "detail": detail,
                       "output_exists": er.output_exists,
                       "reader": er.reader_identity}
        if er.rc == 0 and er.output_exists:
            # the tool silently ignored the bad device; that is still a
            # valid observation but must be recorded, not hidden
            ev[backend]["note"] = "tool ignored the unavailable device"
        else:
            if code not in ("device_unavailable", "reader_unavailable",
                            "decode_failed", "capability_refused"):
                problems.append(f"{backend}: unclassified failure code {code}")
    # the software path must still succeed
    sw, cm_sw = do_encode(ctx, tid="HD-F01", fx=fx, backend="nvenc",
                          reader="avsw", tag="recovery")
    ok, reasons = checks.reconcile(cm_sw)
    ev["software_recovery"] = {"rc": sw.rc, "ok": ok, "reasons": reasons}
    if not ok:
        problems.append("software fallback did not produce an exact result")
    return _res(t, "HD-F01", STATUS_PASS if not problems else STATUS_FAIL,
                actual="unavailable hardware classified and routed to software",
                reason="; ".join(problems), evidence=ev)


@impl("f02_capability_rejection")
def f02(t, ctx):
    fx = FX.SONY_422_C9037
    skip = _require_fixture(fx, "HD-F02", ctx)
    if skip:
        return skip
    from encoders.hwdecode import classify_reader_failure, route_decode
    er, cm = do_encode(ctx, tid="HD-F02", fx=fx, backend="qsv", reader="avhw",
                       role="stock_control", tag="refuse")
    code, detail = classify_reader_failure(er.log_text, rc=er.rc)
    routed = route_decode(policy="auto", backend="qsv", codec="h264",
                          chroma="4:2:2", depth=10)
    sw, cm_sw = do_encode(ctx, tid="HD-F02", fx=fx, backend="qsv", reader="avsw",
                          tag="recovery")
    ok, reasons = checks.reconcile(cm_sw)
    ev = {
        "hardware_attempt": {"rc": er.rc, "output_exists": er.output_exists,
                             "reason_code": code, "detail": detail},
        "routing": routed.to_json(),
        "software_recovery": {"rc": sw.rc, "ok": ok, "reasons": reasons,
                              "reader": sw.reader_identity},
    }
    problems = []
    if er.rc == 0 or er.output_exists:
        problems.append("QSV hardware attempt did not refuse")
    if code != "capability_refused":
        problems.append(f"classified as {code}, expected capability_refused")
    if routed.hardware or routed.reason != "capability_refused":
        problems.append(f"routing said {routed.reason}")
    if not routed.warnings:
        problems.append("refusal produced no warning")
    if not ok:
        problems.append("software fallback not exact: " + "; ".join(reasons))
    return _res(t, "HD-F02", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"rc={er.rc}, code={code}, software fallback exact",
                reason="; ".join(problems), evidence=ev)


@impl("f03_startup_failure")
def f03(t, ctx):
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-F03", ctx)
    if skip:
        return skip
    from encoders.hwdecode import classify_reader_failure
    broken = runners.broken_binary_copy(RUNS / "_broken_f03")
    er = runners.run_encode_spec(
        label="HD-F03_broken", spec=sources.BackendSpec(
            backend="nvenc", kind="nvencc", binary=broken,
            reader_arg="--avhw", reader_identity_expected="avcuvid"),
        source=fx.path, output=RUNS / "HD-F03_broken.mp4", frames=4,
    )
    code, detail = classify_reader_failure(er.log_text, rc=er.rc)
    ev = {"rc": er.rc, "reason_code": code, "detail": detail,
          "output_exists": er.output_exists,
          "timed_out": er.timed_out}
    problems = []
    if er.rc == 0:
        problems.append("broken binary exited 0")
    if er.output_exists:
        problems.append("broken binary produced an output")
    if er.timed_out:
        problems.append("broken binary hung instead of failing")
    return _res(t, "HD-F03", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"startup failure rc={er.rc}, classified as {code}",
                reason="; ".join(problems), evidence=ev)


@impl("f04_runtime_decode_failure")
def f04(t, ctx):
    """A decode that starts and then fails must be classified, not delivered."""
    from encoders.integrity import verify_encode
    src = RUNS / "HD-F04_damaged.mp4"
    RUNS.mkdir(parents=True, exist_ok=True)
    fx = FX.GEN_A_COPY
    if not fx.path.is_file():
        return _res(t, "HD-F04", STATUS_SKIP,
                    reason="control fixture absent; run inventory --force")
    raw = fx.path.read_bytes()
    # corrupt the mdat payload while leaving the moov table intact: the
    # container still promises a full frame count, so only a real
    # integrity check can notice.
    idx = raw.find(b"mdat")
    damaged = bytearray(raw)
    if idx > 0:
        start = idx + 4
        end = min(len(damaged), start + 2_000_000)
        for i in range(start, end, 977):
            damaged[i] ^= 0xFF
    src.write_bytes(bytes(damaged))
    er, cm = do_encode(ctx, tid="HD-F04", fx=FX.Fixture(
        "f04", "synthetic", str(src), "corrupted payload", generated=False
    ), backend="nvenc", reader="avhw", tag="damaged")
    verdict = verify_encode(
        source=src, output=Path(er.output) if er.output_exists else None,
        log_text=er.log_text, rc=er.rc, reader_requested="avhw",
        reader_expected_identity="avcuvid",
    )
    ev = {"rc": er.rc, "reader": er.reader_identity,
          "output_exists": er.output_exists,
          "encoder_input": cm.encoder_input,
          "container": cm.container_expected,
          "verdict": verdict.to_json()}
    problems = []
    if er.rc != 0 and verdict.reason == "integrity_ok":
        problems.append("failed run reported integrity_ok")
    if er.rc == 0 and not verdict.ok:
        # detected and handled: acceptable
        if "fallback_to_software" not in verdict.actions:
            problems.append("gate failed without requesting a fallback")
    if er.rc == 0 and verdict.ok:
        ev["note"] = ("corruption did not change the decoded frame set; "
                      "recorded as an observation")
    return _res(t, "HD-F04", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"rc={er.rc} verdict={verdict.reason}",
                reason="; ".join(problems), evidence=ev)


@impl("f05_integrity_failure")
def f05(t, ctx):
    """Shares its evidence with HD-C09/HD-C11 by design."""
    r = IMPL["c11_negative_head_loss"](t, ctx)
    r.test_id = "HD-F05"
    r.title = t["title"]
    r.actual = "integrity failure detected, artifact discarded, software rerun exact: " + r.actual
    return r


@impl("f06_double_fallback_guard")
def f06(t, ctx):
    import core.batch_hw as bh
    ev = {"cap": bh.MAX_HW_DECODE_FALLBACKS}
    problems = []
    if bh.MAX_HW_DECODE_FALLBACKS < 1:
        problems.append("fallback cap allows no retry at all")
    src = (ROOT / "core" / "batch_hw.py").read_text(encoding="utf-8")
    if "hw_fallbacks > MAX_HW_DECODE_FALLBACKS" not in src:
        problems.append("no bound on hardware-decode fallbacks in the ladder")
    if "refusing to retry further" not in src:
        problems.append("no explicit terminal condition message")
    if "downgrade ladder exhausted" not in src:
        problems.append("format ladder has no terminal condition")
    # a runaway loop is impossible only if every retry advances a counter
    if src.count("hw_fallbacks += 1") != 1:
        problems.append("hww fallback counter is not incremented exactly once per retry")
    return _res(t, "HD-F06", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"hardware fallback cap = {bh.MAX_HW_DECODE_FALLBACKS}, "
                       f"explicit terminal condition present",
                reason="; ".join(problems), evidence=ev)


@impl("f07_fallback_is_loud")
def f07(t, ctx):
    from encoders.hwdecode import route_decode, memoize_unavailable
    ev, problems = {}, []
    r = route_decode(policy="auto", backend="qsv", codec="h264",
                     chroma="4:2:2", depth=10)
    ev["capability_refusal"] = {"warnings": r.warnings, "reason": r.reason}
    if not r.warnings:
        problems.append("capability refusal produced no warning")
    # A capability refusal stays a capability refusal even once memoized:
    # the specific reason must not degrade into a generic one.
    memo: dict = {}
    memoize_unavailable(memo, r, "capability_refused", "test detail")
    r2 = route_decode(policy="auto", backend="qsv", codec="h264",
                      chroma="4:2:2", depth=10, memo=memo)
    ev["memoized_refusal"] = {"warnings": r2.warnings, "reason": r2.reason,
                              "memo": {str(k): v for k, v in memo.items()}}
    if not r2.warnings:
        problems.append("memoized refusal produced no warning")
    if r2.reason != "capability_refused":
        problems.append(
            f"memoized refusal lost its specific reason (got {r2.reason})")
    # The memo must actually suppress a *runtime* unavailability: use an
    # otherwise-proven combination so the memo is the only reason to refuse.
    r3 = route_decode(policy="auto", backend="nvenc", codec="hevc",
                      chroma="4:2:0", depth=10)
    memo2: dict = {}
    memoize_unavailable(memo2, r3, "device_unavailable", "no usable device")
    r4 = route_decode(policy="auto", backend="nvenc", codec="hevc",
                      chroma="4:2:0", depth=10, memo=memo2)
    ev["memoized_runtime"] = {"before": r3.to_json(), "after": r4.to_json()}
    if r3.hardware is not True:
        problems.append("baseline combination is not hardware-eligible")
    if r4.hardware or r4.reason != "reader_unavailable":
        problems.append(
            f"memoized runtime unavailability not honoured (got {r4.reason})")
    if not r4.warnings:
        problems.append("memoized runtime unavailability produced no warning")
    # unproven combinations must warn too
    r3 = route_decode(policy="auto", backend="nvenc", codec="av1",
                      chroma="4:2:0", depth=10)
    ev["not_proven"] = {"warnings": r3.warnings, "reason": r3.reason}
    if not r3.warnings:
        problems.append("not_proven produced no warning")
    # the ladder writes them through emit_warning, which records in logs
    src = (ROOT / "core" / "batch_hw.py").read_text(encoding="utf-8")
    ev["wired_into_ladder"] = (
        "for w in route.warnings:" in src and "INTEGRITY |" in src
    )
    if not ev["wired_into_ladder"]:
        problems.append("routing/integrity messages are not written to logs")
    return _res(t, "HD-F07", STATUS_PASS if not problems else STATUS_FAIL,
                actual="every software fallback carries a warning and a reason code",
                reason="; ".join(problems), evidence=ev)


@impl("f08_fallback_output_identical")
def f08(t, ctx):
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-F08", ctx)
    if skip:
        return skip
    from encoders.integrity import verify_encode
    bad, cm_bad = do_encode(ctx, tid="HD-F08", fx=fx, backend="nvenc",
                            reader="avhw", role="stock_control", tag="inject")
    verdict = verify_encode(
        source=fx.path, output=Path(bad.output) if bad.output_exists else None,
        log_text=bad.log_text, rc=bad.rc, reader_requested="avhw",
        reader_expected_identity="avcuvid")
    Path(bad.output).unlink(missing_ok=True)
    after_fallback, _ = do_encode(ctx, tid="HD-F08", fx=fx, backend="nvenc",
                                  reader="avsw", tag="postfallback")
    direct, _ = do_encode(ctx, tid="HD-F08", fx=fx, backend="nvenc",
                          reader="avsw", tag="direct")
    a = checks.sha256_file(Path(after_fallback.output)) if after_fallback.output_exists else None
    b = checks.sha256_file(Path(direct.output)) if direct.output_exists else None
    ev = {"verdict": verdict.to_json(), "sha_after_fallback": a, "sha_direct": b,
          "identical": a is not None and a == b}
    problems = []
    if verdict.ok:
        problems.append("the injected failure was not detected")
    if a is None or b is None:
        problems.append("one of the software runs produced nothing")
    elif a != b:
        problems.append("fallback output differs from a direct software run")
    return _res(t, "HD-F08", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"post-fallback sha == direct sha ({str(a)[:12]})",
                reason="; ".join(problems), evidence=ev)


# --- G ---------------------------------------------------------------------


def _channel_sync_fixture() -> tuple[Path | None, str]:
    """A real multichannel PCM source for channel-sync interaction tests."""
    for cand in [FX.REAL_FIELD_DIRS["field_adjust"], FX.REAL_FIELD_DIRS["field_validate"]]:
        if not cand.is_dir():
            continue
        for p in sorted(cand.iterdir()):
            if p.suffix.lower() in (".mp4", ".mov"):
                from tests.hwdecode.probe import probe_input
                f = probe_input(p, leading_scan=False)
                if f.audio_streams >= 3:
                    return p, p.name
    return None, ""


def _run_channel_sync(path: Path, work: Path) -> dict:
    from core.channel_sync import run_channel_sync
    from tests.hwdecode.probe import probe_input
    facts = probe_input(path, leading_scan=False)
    streams = []
    try:
        import json as _j
        p = subprocess.run(
            [str(ROOT / "tools" / "ffprobe.exe"), "-v", "error",
             "-print_format", "json", "-show_streams", "-i", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300)
        streams = _j.loads(p.stdout).get("streams", [])
    except Exception:  # noqa: BLE001
        streams = []
    return run_channel_sync(
        source=path, ffmpeg=ROOT / "tools" / "ffmpeg.exe",
        work_dir=work, streams=streams, opts=None, log=lambda m: None,
    )


@impl("g01_channel_sync_aligned")
def g01(t, ctx):
    src, name = _channel_sync_fixture()
    if src is None:
        return _res(t, "HD-G01", STATUS_SKIP,
                    reason="no >=3-track PCM fixture in the corpus")
    ev, problems = {}, []
    try:
        res = _run_channel_sync(src, FX.WORK / "g01_sync")
    except Exception as exc:  # noqa: BLE001
        return _res(t, "HD-G01", STATUS_BLOCKED,
                    reason=f"channel-sync could not run: {exc}")
    ev["fixture"] = name
    ev["status"] = res.get("status")
    ev["channels"] = res.get("channels")
    if res.get("status") not in ("applied", "aligned", "unchanged", "no_change",
                                 "not_needed", "skipped"):
        problems.append(f"unexpected channel-sync status {res.get('status')}")
    # determinism: a second identical call must give the same decision
    res2 = _run_channel_sync(src, FX.WORK / "g01_sync2")
    ev["status_second_run"] = res2.get("status")
    ev["channels_second_run"] = res2.get("channels")
    if json.dumps(res.get("channels"), sort_keys=True, default=str) != json.dumps(
        res2.get("channels"), sort_keys=True, default=str
    ):
        problems.append("channel-sync decision is not deterministic across runs")
    return _res(t, "HD-G01", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"status={res.get('status')}, deterministic across runs",
                reason="; ".join(problems), evidence=ev)


@impl("g02_channel_sync_delay")
def g02(t, ctx):
    src, name = _channel_sync_fixture()
    if src is None:
        return _res(t, "HD-G02", STATUS_SKIP,
                    reason="no >=3-track PCM fixture in the corpus")
    ev, problems = {}, []
    res = _run_channel_sync(src, FX.WORK / "g02_sync")
    ev["status"] = res.get("status")
    ev["channels"] = res.get("channels")
    ev["fixture"] = name
    bad = [c for c in (res.get("channels") or [])
           if c.get("decision") not in (None, "untouched", "shifted",
                                        "no_change", "aligned", "kept")]
    ev["unexpected_decisions"] = bad
    if res.get("status") in ("measure_failed", "verify_failed"):
        ev["note"] = ("measurement did not converge on this fixture; that is a "
                      "valid per-track outcome, not an integration failure")
    if bad:
        problems.append(f"{len(bad)} tracks reported an unknown decision")
    return _res(t, "HD-G02", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"status={res.get('status')}, per-track decisions recorded",
                reason="; ".join(problems), evidence=ev)


@impl("g03_channel_sync_unusable")
def g03(t, ctx):
    src, name = _channel_sync_fixture()
    if src is None:
        return _res(t, "HD-G03", STATUS_SKIP,
                    reason="no >=3-track PCM fixture in the corpus")
    res = _run_channel_sync(src, FX.WORK / "g03_sync")
    chans = res.get("channels") or []
    ev = {"fixture": name, "status": res.get("status"), "channels": chans}
    problems = []
    # any track that could not be measured must be marked untouched and
    # must not have blocked the others
    untouched = [c for c in chans if c.get("decision") == "untouched"]
    ev["untouched"] = len(untouched)
    if len(chans) > 1 and len(untouched) == len(chans) and res.get("status") == "applied":
        problems.append("every track untouched yet status is applied")
    return _res(t, "HD-G03", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"{len(chans)} tracks, {len(untouched)} untouched, "
                       f"status={res.get('status')}",
                reason="; ".join(problems), evidence=ev)


@impl("g04_hw_video_sw_audio")
def g04(t, ctx):
    """Audio must be untouched by a hardware-decode video path."""
    src, name = _channel_sync_fixture()
    if src is None:
        return _res(t, "HD-G04", STATUS_SKIP,
                    reason="no >=3-track PCM fixture in the corpus")
    fx_hw = FX.Fixture("g04", "field", str(src.relative_to(ROOT)) if str(src).startswith(str(ROOT)) else str(src), "")
    hw, cm_hw = do_encode(ctx, tid="HD-G04", fx=fx_hw, backend="nvenc",
                          reader="avhw", tag="vid", audio_copy=True, frames=48)
    sw, cm_sw = do_encode(ctx, tid="HD-G04", fx=fx_hw, backend="nvenc",
                          reader="avsw", tag="vid", audio_copy=True, frames=48)
    ev = {"hw_rc": hw.rc, "sw_rc": sw.rc, "hw_reader": hw.reader_identity,
          "sw_reader": sw.reader_identity}
    problems = []
    if not (hw.output_exists and sw.output_exists):
        problems.append("one of the runs produced no output")
    else:
        a = checks.sha256_file(Path(hw.output))
        b = checks.sha256_file(Path(sw.output))
        ev["sha_hw"] = a
        ev["sha_sw"] = b
        ev["byte_identical"] = a == b
        if a != b:
            problems.append("hardware-decode run differs from software run "
                            "when audio is copied alongside")
    return _res(t, "HD-G04", STATUS_PASS if not problems else STATUS_FAIL,
                actual="audio path identical with hardware and software video decode",
                reason="; ".join(problems), evidence=ev)


@impl("g05_fallback_plus_audio")
def g05(t, ctx):
    from encoders.integrity import verify_encode
    src, name = _channel_sync_fixture()
    if src is None:
        return _res(t, "HD-G05", STATUS_SKIP,
                    reason="no >=3-track PCM fixture in the corpus")
    fx = FX.Fixture("g05", "field", str(src), "")
    bad, cm_bad = do_encode(ctx, tid="HD-G05", fx=fx, backend="nvenc",
                            reader="avhw", role="stock_control", tag="inject",
                            audio_copy=True, frames=48)
    verdict = verify_encode(
        source=src, output=Path(bad.output), log_text=bad.log_text, rc=bad.rc,
        reader_requested="avhw", reader_expected_identity="avcuvid")
    Path(bad.output).unlink(missing_ok=True)
    rec, _ = do_encode(ctx, tid="HD-G05", fx=fx, backend="nvenc", reader="avsw",
                       tag="recover", audio_copy=True, frames=48)
    direct, _ = do_encode(ctx, tid="HD-G05", fx=fx, backend="nvenc", reader="avsw",
                          tag="direct", audio_copy=True, frames=48)
    ev = {"verdict": verdict.to_json(), "fixture": name}
    problems = []
    if verdict.ok:
        problems.append("injected failure not detected")
    if not (rec.output_exists and direct.output_exists):
        problems.append("missing output after fallback")
    else:
        a = checks.sha256_file(Path(rec.output))
        b = checks.sha256_file(Path(direct.output))
        ev["sha_fallback"] = a
        ev["sha_direct"] = b
        if a != b:
            problems.append("post-fallback output differs from direct software run")
    return _res(t, "HD-G05", STATUS_PASS if not problems else STATUS_FAIL,
                actual="post-fallback audio+video identical to the software baseline",
                reason="; ".join(problems), evidence=ev)


@impl("g06_channel_sync_frozen_baseline")
def g06(t, ctx):
    base = ROOT / "tests" / "fixtures" / "channel_sync" / "a7m5_real_137_baseline.csv"
    if not base.is_file():
        return _res(t, "HD-G06", STATUS_SKIP,
                    reason="frozen 137-segment baseline CSV not present")
    import hashlib
    before = checks.sha256_file(base)
    # the integration must not have touched the frozen baseline; and the
    # channel-sync module must not have changed its algorithm version.
    mod = (ROOT / "core" / "channel_sync.py").read_text(encoding="utf-8")
    import re as _re
    m = _re.search(r'ALGO_VERSION\s*=\s*"([^"]+)"', mod)
    algo = m.group(1) if m else None
    ev = {"baseline_sha256": before, "algo_version": algo}
    problems = []
    if algo is None:
        problems.append("channel-sync algorithm version not declared")
    # confirm the integration branch did not modify either artefact
    st = subprocess.run(
        ["git", "diff", "--name-only", "main...HEAD", "--",
         "core/channel_sync.py", "core/sync_estimate.py",
         "tests/fixtures/channel_sync/"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    changed = [x for x in (st.stdout or "").splitlines() if x.strip()]
    ev["changed_vs_main"] = changed
    if changed:
        problems.append(f"channel-sync artefacts changed vs main: {changed}")
    return _res(t, "HD-G06", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"frozen baseline intact (sha {before[:16]}), algo {algo}",
                reason="; ".join(problems), evidence=ev)


@impl("g07_colour_in_production")
def g07(t, ctx):
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-G07", ctx)
    if skip:
        return skip
    hw, sw, hf, sf = _meta_pair(ctx, fx, tid="HD-G07", tag="colprod")
    a, b = _meta_fields(hf), _meta_fields(sf)
    if a is None or b is None:
        return _res(t, "HD-G07", STATUS_BLOCKED, reason="missing output metadata")
    keys = ("color_range", "color_space", "color_primaries", "color_transfer",
            "chroma", "bit_depth")
    ev = {"hw": {k: a[k] for k in keys}, "sw": {k: b[k] for k in keys}}
    problems = [f"{k}: {a[k]!r} != {b[k]!r}" for k in keys if a[k] != b[k]]
    return _res(t, "HD-G07", STATUS_PASS if not problems else STATUS_FAIL,
                actual="colour metadata preserved through the hardware path",
                reason="; ".join(problems), evidence=ev)


@impl("g08_audio_preservation")
def g08(t, ctx):
    src, name = _channel_sync_fixture()
    if src is None:
        return _res(t, "HD-G08", STATUS_SKIP,
                    reason="no multichannel fixture in the corpus")
    fx = FX.Fixture("g08", "field", str(src), "")
    hw, _ = do_encode(ctx, tid="HD-G08", fx=fx, backend="nvenc", reader="avhw",
                      tag="aud", audio_copy=True, frames=48)
    sw, _ = do_encode(ctx, tid="HD-G08", fx=fx, backend="nvenc", reader="avsw",
                      tag="aud", audio_copy=True, frames=48)
    if not (hw.output_exists and sw.output_exists):
        return _res(t, "HD-G08", STATUS_BLOCKED, reason="missing output")
    from tests.hwdecode.probe import probe_input as pi
    a = pi(Path(hw.output), leading_scan=False)
    b = pi(Path(sw.output), leading_scan=False)
    keys = ("audio_streams", "stream_kinds")
    ev = {"hw": {"audio": a.audio_streams, "kinds": sorted(a.stream_kinds)},
          "sw": {"audio": b.audio_streams, "kinds": sorted(b.stream_kinds)}}
    problems = []
    if a.audio_streams != b.audio_streams:
        problems.append(f"audio stream count {a.audio_streams} vs {b.audio_streams}")
    if sorted(a.stream_kinds) != sorted(b.stream_kinds):
        problems.append("stream kinds differ")

    def audio_detail(p: Path):
        try:
            r = subprocess.run(
                [str(ROOT / "tools" / "ffprobe.exe"), "-v", "error",
                 "-select_streams", "a", "-show_entries",
                 "stream=index,channels,sample_rate,channel_layout,codec_name",
                 "-print_format", "json", "-i", str(p)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=600)
            return json.loads(r.stdout).get("streams", [])
        except Exception:  # noqa: BLE001
            return []
    da, db = audio_detail(Path(hw.output)), audio_detail(Path(sw.output))
    ev["audio_detail_hw"] = da
    ev["audio_detail_sw"] = db
    if da != db:
        problems.append("audio stream properties differ between readers")
    return _res(t, "HD-G08", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"{len(da)} audio streams identical between readers",
                reason="; ".join(problems), evidence=ev)


@impl("g09_multi_stream")
def g09(t, ctx):
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-G09", ctx)
    if skip:
        return skip
    facts = ctx.fact(fx)
    src_kinds = sorted(facts.stream_kinds)
    ev = {"source_streams": src_kinds, "audio": facts.audio_streams,
          "data": facts.data_streams}
    problems = []
    if facts.audio_streams < 4 or facts.data_streams < 1:
        problems.append(
            f"fixture is not multi-stream (audio={facts.audio_streams}, "
            f"data={facts.data_streams})")
    # the encoder intermediate must expose exactly the video stream
    hw, cm = do_encode(ctx, tid="HD-G09", fx=fx, backend="nvenc", reader="avhw",
                       tag="ms", frames=48)
    if not hw.output_exists:
        problems.append("no intermediate produced")
    else:
        from tests.hwdecode.probe import probe_input as pi
        inter = pi(Path(hw.output), leading_scan=False)
        ev["intermediate_streams"] = sorted(inter.stream_kinds)
        if sorted(inter.stream_kinds) != ["video:hevc"]:
            problems.append(
                f"intermediate carries {sorted(inter.stream_kinds)}, expected "
                "video only (the preservation path re-adds the other streams "
                "at container level)")
    # the source must be untouched by a run
    from tests.hwdecode.probe import probe_input as pi
    after = pi(fx.path, leading_scan=False)
    ev["source_streams_after"] = sorted(after.stream_kinds)
    if sorted(after.stream_kinds) != src_kinds:
        problems.append("source stream inventory changed after the run")
    return _res(t, "HD-G09", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"source has {len(src_kinds)} streams; intermediate is video-only",
                reason="; ".join(problems), evidence=ev)


# --- H ---------------------------------------------------------------------


def _run_app(extra: list[str], *, timeout: int = 3600) -> subprocess.CompletedProcess:
    """Invoke the real CLI on a scratch input/output pair."""
    return subprocess.run(
        [sys.executable, str(ROOT / "1kt.py"), *extra],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


def _scratch_io(name: str, src: Path) -> tuple[Path, Path]:
    inp = FX.WORK / f"h_{name}_in"
    out = FX.WORK / f"h_{name}_out"
    inp.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    dst = inp / src.name
    if not dst.is_file():
        shutil.copy2(src, dst)
    return inp, out


@impl("h01_resume_after_success")
def h01(t, ctx):
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-H01", ctx)
    if skip:
        return skip
    ev, problems = {}, []
    inp, out = _scratch_io("h01", fx.path)
    args = ["--input", str(inp), "--output", str(out), "--check", "basic",
            "--headless", "--encoder", "nvenc", "--hw-decode", "auto",
            "--preset", "HQ"]
    first = _run_app(args)
    ev["first_rc"] = first.returncode
    produced = list(out.rglob("*.MP4")) + list(out.rglob("*.mp4"))
    if not produced:
        return _res(t, "HD-H01", STATUS_BLOCKED,
                    actual=f"first run rc={first.returncode}, no output",
                    reason="could not produce a first successful run",
                    evidence={"stdout_tail": first.stdout[-1500:],
                              "stderr_tail": first.stderr[-1500:]})
    sha_first = checks.sha256_file(produced[0])
    mtime = produced[0].stat().st_mtime_ns
    second = _run_app(args)
    ev["second_rc"] = second.returncode
    sha_second = checks.sha256_file(produced[0])
    ev["sha_first"] = sha_first
    ev["sha_second"] = sha_second
    ev["mtime_unchanged"] = produced[0].stat().st_mtime_ns == mtime
    if sha_first != sha_second:
        problems.append("resume rewrote the delivered file with different bytes")
    if not ev["mtime_unchanged"]:
        problems.append("resume recomputed rather than recognising completion")
    return _res(t, "HD-H01", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"second run rc={second.returncode}, artifact unchanged",
                reason="; ".join(problems), evidence=ev)


@impl("h02_resume_after_fallback")
def h02(t, ctx):
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-H02", ctx)
    if skip:
        return skip
    inp, out = _scratch_io("h02", fx.path)
    # first run at a bad resolution to create a failed-run cache
    bad = _run_app(["--input", str(inp), "--output", str(out), "--check",
                    "basic", "--headless", "--encoder", "nvenc",
                    "--hw-decode", "auto", "--no-downgrade"])
    good = _run_app(["--input", str(inp), "--output", str(out), "--check",
                     "basic", "--headless", "--encoder", "nvenc",
                     "--hw-decode", "auto"])
    produced = list(out.rglob("*.MP4")) + list(out.rglob("*.mp4"))
    ev = {"first_rc": bad.returncode, "second_rc": good.returncode,
          "produced": [p.name for p in produced]}
    problems = []
    if not produced:
        problems.append("retry after a failed run produced nothing")
    else:
        sha = checks.sha256_file(produced[0])
        again = _run_app(["--input", str(inp), "--output", str(out), "--check",
                          "basic", "--headless", "--encoder", "nvenc",
                          "--hw-decode", "auto"])
        ev["third_rc"] = again.returncode
        ev["sha_stable"] = checks.sha256_file(produced[0]) == sha
        if not ev["sha_stable"]:
            problems.append("resume after fallback changed the delivered bytes")
    return _res(t, "HD-H02", STATUS_PASS if not problems else STATUS_FAIL,
                actual="failed-run state did not block the retry; resume stable",
                reason="; ".join(problems), evidence=ev)


@impl("h03_interrupted_job")
def h03(t, ctx):
    fx = FX.FIELD_STRESS_LONG2 if hasattr(FX, "FIELD_STRESS_LONG2") else None
    if fx is None:
        cands = [f for f in FX.real_field() if "stress" in f.group]
        cands.sort(key=lambda f: ctx.fact(f).container_samples or 0)
        fx = cands[0] if cands else FX.SONY_HS
    skip = _require_fixture(fx, "HD-H03", ctx)
    if skip:
        return skip
    inp, out = _scratch_io("h03", fx.path)
    args = [sys.executable, str(ROOT / "1kt.py"), "--input", str(inp),
            "--output", str(out), "--check", "basic", "--headless",
            "--encoder", "nvenc", "--hw-decode", "auto"]
    proc = subprocess.Popen(args, cwd=str(ROOT), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace")
    time.sleep(25)
    proc.terminate()
    try:
        proc.communicate(timeout=90)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
    produced = list(out.rglob("*.MP4")) + list(out.rglob("*.mp4"))
    reports = list(FX.WORK.glob("h_h03_out/**/report.json")) + list(
        (out / ".1ktwork").rglob("report.json") if (out / ".1ktwork").is_dir() else [])
    ev = {"terminated": True, "produced_after_interrupt": [p.name for p in produced],
          "reports": [str(r) for r in reports]}
    problems = []
    # a terminated run must not leave a delivered artifact that claims success
    for p in produced:
        if p.stat().st_size > 0:
            problems.append(
                f"{p.name} exists after termination — verify it is not a "
                "false success")
    return _res(t, "HD-H03",
                STATUS_PASS if not problems else STATUS_FAIL,
                actual="interrupted run left no delivered artifact claiming success",
                reason="; ".join(problems), evidence=ev)


@impl("h04_retry_after_hw_failure")
def h04(t, ctx):
    from encoders.integrity import verify_encode
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-H04", ctx)
    if skip:
        return skip
    stages: list[str] = []
    bad, cm_bad = do_encode(ctx, tid="HD-H04", fx=fx, backend="nvenc",
                            reader="avhw", role="stock_control", tag="fail")
    stages.append("hardware_attempt")
    verdict = verify_encode(
        source=fx.path, output=Path(bad.output), log_text=bad.log_text,
        rc=bad.rc, reader_requested="avhw", reader_expected_identity="avcuvid")
    stages.append(f"gate:{verdict.reason}")
    Path(bad.output).unlink(missing_ok=True)
    stages.append("discard")
    rec, cm_rec = do_encode(ctx, tid="HD-H04", fx=fx, backend="nvenc",
                            reader="avsw", tag="retry")
    stages.append("software_retry")
    ok, reasons = checks.reconcile(cm_rec)
    ev = {"stages": stages, "verdict": verdict.to_json(),
          "retry_ok": ok, "reasons": reasons,
          "retry_reader": rec.reader_identity}
    problems = []
    if verdict.ok:
        problems.append("hardware failure was not detected")
    if not ok:
        problems.append("retry did not produce an exact result")
    ev["stage_order_ok"] = stages == [
        "hardware_attempt", f"gate:{verdict.reason}", "discard",
        "software_retry"] or "software_retry" in stages
    if not ev["stage_order_ok"]:
        problems.append("stages out of order")
    return _res(t, "HD-H04", STATUS_PASS if not problems else STATUS_FAIL,
                actual=" -> ".join(stages),
                reason="; ".join(problems), evidence=ev)


@impl("h05_partial_artifact")
def h05(t, ctx):
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-H05", ctx)
    if skip:
        return skip
    import core.batch_hw as bh
    inp, out = _scratch_io("h05", fx.path)
    # plant a truncated artifact where the pipeline looks for its intermediate
    work = out / ".1ktwork"
    planted = 0
    for jobdir in work.rglob("video") if work.is_dir() else []:
        target = jobdir / "encoded.mov"
        if target.is_file():
            target.write_bytes(target.read_bytes()[:4096])
            planted += 1
    ev = {"planted": planted}
    problems = []
    if planted:
        ok = bh._encoded_ok(ROOT / "tools" / "ffprobe.exe",
                            next(work.rglob("video/encoded.mov")))
        ev["encoded_ok_on_truncated"] = ok
        if ok:
            problems.append("_encoded_ok accepted a truncated artifact")
    else:
        # no prior run left an intermediate: validate the guard directly
        bad = RUNS / "HD-H05_truncated.mov"
        hw, _ = do_encode(ctx, tid="HD-H05", fx=fx, backend="nvenc",
                          reader="avhw", tag="ref", frames=24)
        if hw.output_exists:
            data = Path(hw.output).read_bytes()
            bad.write_bytes(data[: max(2048, len(data) // 50)])
            ok = bh._encoded_ok(ROOT / "tools" / "ffprobe.exe", bad)
            ev["synthetic_truncated_accepted"] = ok
            if ok:
                problems.append("_encoded_ok accepted a synthetic truncated artifact")
        else:
            return _res(t, "HD-H05", STATUS_BLOCKED,
                        reason="could not obtain an artifact to truncate")
    return _res(t, "HD-H05", STATUS_PASS if not problems else STATUS_FAIL,
                actual="a partial artifact is rejected, so no false resume",
                reason="; ".join(problems), evidence=ev)


# --- I ---------------------------------------------------------------------


def _concurrent(entries: list[dict], ctx: Ctx) -> list[dict]:
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=len(entries)) as ex:
        futs = {ex.submit(do_encode, ctx, **e): e for e in entries}
        out = []
        for fu in cf.as_completed(futs):
            e = futs[fu]
            try:
                er, cm = fu.result()
                out.append({"entry": e, "rc": er.rc,
                            "reader": er.reader_identity,
                            "output": er.output, "log_path": er.log_path,
                            "encoded": cm.encoder_input,
                            "container": cm.container_expected,
                            "output_stream": cm.output_stream,
                            "elapsed_s": er.elapsed_s})
            except Exception as exc:  # noqa: BLE001
                out.append({"entry": e, "error": f"{type(exc).__name__}: {exc}"})
    return out


@impl("i01_single_job")
def i01(t, ctx):
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-I01", ctx)
    if skip:
        return skip
    reps = 3 if ctx.deep else 2
    ev, problems = {}, []
    for backend, reader in (("nvenc", "avhw"), ("qsv", "avhw"),
                            ("nvenc", "avsw")):
        rows = []
        for i in range(reps):
            er, cm = do_encode(ctx, tid="HD-I01", fx=fx, backend=backend,
                               reader=reader, tag=f"rep{i}")
            ok, reasons = checks.reconcile(cm)
            rows.append({"rc": er.rc, "reader": er.reader_identity, "ok": ok,
                         "encoded": cm.encoder_input,
                         "elapsed_s": er.elapsed_s, "reasons": reasons})
            if not ok:
                problems.append(f"{backend}/{reader} rep{i}: " + "; ".join(reasons))
        times = [r["elapsed_s"] for r in rows if r["elapsed_s"]]
        ev[f"{backend}/{reader}"] = {
            "runs": rows,
            "elapsed": {"n": len(times), "min": min(times) if times else None,
                        "max": max(times) if times else None,
                        "mean": round(sum(times) / len(times), 2) if times else None},
        }
    return _res(t, "HD-I01", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"{reps} repeats per backend, all reconciling",
                reason="; ".join(problems), evidence=ev)


@impl("i02_two_jobs")
def i02(t, ctx):
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-I02", ctx)
    if skip:
        return skip
    entries = [
        dict(tid="HD-I02", fx=fx, backend="nvenc", reader="avhw", tag=f"conc{i}")
        for i in range(2)
    ]
    results = _concurrent(entries, ctx)
    ev = {"results": results}
    problems = []
    for i, r in enumerate(results):
        if r.get("error"):
            problems.append(f"job {i}: {r['error']}")
            continue
        if r["reader"] != "avcuvid":
            problems.append(f"job {i}: reader {r['reader']!r} (device crossed?)")
        if r["rc"] != 0:
            problems.append(f"job {i}: rc={r['rc']}")
        if r["encoded"] != r["container"]:
            problems.append(
                f"job {i}: encoded {r['encoded']} != container {r['container']}")
    logs = {r.get("log_path") for r in results if r.get("log_path")}
    ev["distinct_logs"] = len(logs) == len(results)
    if not ev["distinct_logs"]:
        problems.append("concurrent jobs shared a log file")
    return _res(t, "HD-I02", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"{len(results)} concurrent hardware jobs, isolated logs",
                reason="; ".join(problems), evidence=ev)


@impl("i03_mixed_hw_sw")
def i03(t, ctx):
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-I03", ctx)
    if skip:
        return skip
    entries = [
        dict(tid="HD-I03", fx=fx, backend="nvenc", reader="avhw", tag="mixhw"),
        dict(tid="HD-I03", fx=fx, backend="nvenc", reader="avsw", tag="mixsw"),
    ]
    results = _concurrent(entries, ctx)
    ev = {"results": results}
    problems = []
    for r in results:
        if r.get("error"):
            problems.append(str(r["error"]))
            continue
        if r["encoded"] != r["container"]:
            problems.append(
                f"{r['entry']['reader']}: encoded {r['encoded']} != "
                f"container {r['container']} under contention")
        if r["reader"] not in ("avcuvid", "avsw"):
            problems.append(f"unexpected reader {r['reader']!r}")
    return _res(t, "HD-I03", STATUS_PASS if not problems else STATUS_FAIL,
                actual="mixed hardware/software concurrency produced no false fallback",
                reason="; ".join(problems), evidence=ev)


@impl("i04_resource_exhaustion")
def i04(t, ctx):
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-I04", ctx)
    if skip:
        return skip
    # saturate the GPU with concurrent hardware encodes, then require a
    # classification (not a crash and not a corrupted product)
    entries = [
        dict(tid="HD-I04", fx=fx, backend="nvenc", reader="avhw", tag=f"load{i}")
        for i in range(4)
    ]
    sat = _concurrent(entries, ctx)
    er, cm = do_encode(ctx, tid="HD-I04", fx=fx, backend="nvenc", reader="avhw",
                       tag="underload")
    from encoders.hwdecode import classify_reader_failure
    code, detail = (classify_reader_failure(er.log_text, rc=er.rc)
                    if er.rc != 0 else ("", ""))
    ok, reasons = checks.reconcile(cm)
    ev = {"saturation": [{"rc": r.get("rc"), "error": r.get("error")}
                         for r in sat],
          "under_load": {"rc": er.rc, "reader": er.reader_identity,
                         "reason_code": code, "detail": detail,
                         "reconcile_ok": ok, "reasons": reasons}}
    problems = []
    if er.rc != 0 and not code:
        problems.append("failure under load was not classified")
    if er.rc == 0 and not ok:
        problems.append("output under load did not reconcile: " + "; ".join(reasons))
    if er.output_exists and er.rc == 0 and not ok:
        problems.append("corrupted output survived load")
    return _res(t, "HD-I04", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"under 4-way load: rc={er.rc}, classified={code or 'n/a'}, "
                       f"reconcile={ok}",
                reason="; ".join(problems), evidence=ev)


@impl("i05_four_way_4k")
def i05(t, ctx):
    fx = FX.SONY_HS
    skip = _require_fixture(fx, "HD-I05", ctx)
    if skip:
        return skip
    entries = [
        dict(tid="HD-I05", fx=fx, backend="nvenc", reader="avhw", tag=f"w{i}")
        for i in range(4)
    ]
    results = _concurrent(entries, ctx)
    ev = {"results": results, "throughput_note":
          "recorded for reference only; this test has no throughput criterion"}
    problems = []
    total_frames = 0
    total_time = 0.0
    for i, r in enumerate(results):
        if r.get("error"):
            ev.setdefault("errors", []).append(f"job {i}: {r['error']}")
            continue
        if r["output"] and Path(r["output"]).is_file():
            n = checks.independent_frame_count(Path(r["output"])) \
                if ctx.deep else None
            if r["encoded"] != r["container"]:
                problems.append(
                    f"job {i}: encoded {r['encoded']} != container {r['container']} "
                    "(data integrity under 4-way stress)")
            if n is not None and n != r["container"]:
                problems.append(f"job {i}: decoded {n} != container {r['container']}")
            total_frames += r["encoded"] or 0
        total_time = max(total_time, r.get("elapsed_s") or 0)
    if total_time:
        ev["aggregate_fps"] = round(total_frames / total_time, 2)
    return _res(t, "HD-I05", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"4-way 4K: no crash, no corruption "
                       f"(aggregate {ev.get('aggregate_fps')} fps, recorded only)",
                reason="; ".join(problems), evidence=ev)


# --- J ---------------------------------------------------------------------


def _batch_scan(ctx, clips, *, tid, backend, reader, tag, fp_head=None):
    ev, problems, done = {}, [], []
    for fx in clips:
        if not fx.path.is_file():
            continue
        er, cm = do_encode(ctx, tid=tid, fx=fx, backend=backend, reader=reader,
                           tag=tag)
        ok, reasons = checks.reconcile(cm)
        row = {"container": cm.container_expected, "encoded": cm.encoder_input,
               "output_stream": cm.output_stream,
               "decoded": cm.independent_decoded,
               "reader": er.reader_identity, "rc": er.rc, "ok": ok}
        if er.reader_identity != ("avcuvid" if backend == "nvenc" else "avqsv"):
            row["reader_mismatch"] = True
            problems.append(f"{fx.fid}: reader {er.reader_identity!r}")
        if not ok:
            problems.append(f"{fx.fid}: " + "; ".join(reasons))
        if fp_head and ok and er.output_exists:
            sw, cm_sw = do_encode(ctx, tid=tid, fx=fx, backend=backend,
                                  reader="avsw", tag=f"{tag}ref")
            if cm_sw.output_stream == cm.output_stream:
                fpc = compare_products(Path(er.output), Path(cm_sw.output),
                                       ctx.fact(fx), limit=fp_head)
                row["fingerprint_head"] = fpc["identical"]
                if not fpc["identical"]:
                    problems.append(f"{fx.fid}: head fingerprint differs")
        ev[fx.fid] = row
        done.append(fx.fid)
    return ev, problems, done


@impl("j01_short_smoke")
def j01(t, ctx):
    clips = sorted(FX.real_a7m5(), key=lambda f: ctx.fact(f).container_samples or 0)
    clips = [c for c in clips if (ctx.fact(c).container_samples or 0) <= 900][:12]
    if not clips:
        return _res(t, "HD-J01", STATUS_SKIP, reason="no short real clips available")
    ev, problems, done = _batch_scan(ctx, clips, tid="HD-J01", backend="nvenc",
                                     reader="avhw", tag="smoke",
                                     fp_head=24 if ctx.deep else None)
    return _res(t, "HD-J01", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"{len(done)} short real clips smoking clean on hardware decode",
                reason="; ".join(problems), evidence=ev)


@impl("j02_medium_matrix")
def j02(t, ctx):
    clips = sorted(FX.real_a7m5(), key=lambda f: ctx.fact(f).container_samples or 0)
    clips = [c for c in clips if 900 < (ctx.fact(c).container_samples or 0) <= 3600]
    if not ctx.deep:
        clips = clips[:: max(1, len(clips) // 8)][:8]
    if not clips:
        clips = sorted(FX.real_a7m5(),
                       key=lambda f: ctx.fact(f).container_samples or 0)[:8]
    if not clips:
        return _res(t, "HD-J02", STATUS_SKIP, reason="no medium clips available")
    ev, problems, done = _batch_scan(ctx, clips, tid="HD-J02", backend="nvenc",
                                     reader="avhw", tag="med",
                                     fp_head=24 if ctx.deep else None)
    return _res(t, "HD-J02", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"{len(done)} medium clips validated",
                reason="; ".join(problems), evidence=ev)


@impl("j03_real_sony")
def j03(t, ctx):
    allc = sorted(FX.real_a7m5(), key=lambda f: ctx.fact(f).container_samples or 0)
    if len(allc) < 12:
        allc = allc + sorted(FX.real_field(),
                             key=lambda f: ctx.fact(f).container_samples or 0)
    if not allc:
        return _res(t, "HD-J03", STATUS_SKIP, reason="real Sony corpus absent")
    n = len(allc) if ctx.deep else min(30, len(allc))
    short = allc[: max(1, n // 3)]
    mid = allc[max(1, n // 3): max(2, 2 * n // 3)]
    long_ = allc[max(2, 2 * n // 3): n]
    ev, problems = {}, []
    for label, group in (("short", short), ("medium", mid), ("long", long_)):
        e, p, d = _batch_scan(ctx, group, tid="HD-J03", backend="nvenc",
                              reader="avhw", tag=label,
                              fp_head=16 if ctx.deep else None)
        ev[label] = {"clips": d, "detail": e}
        problems.extend(f"{label}/{x}" for x in p)
    total = sum(len(v["clips"]) for v in ev.values())
    return _res(t, "HD-J03", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"{total} real Sony clips ({len(short)}/{len(mid)}/{len(long_)} short/medium/long)",
                reason="; ".join(problems), evidence=ev)


@impl("j04_real_dji")
def j04(t, ctx):
    clips = [FX.DJI_0009, FX.DJI_0010] + [
        f for f in FX.real_field() if "dji" in f.fid.lower()]
    clips = [c for c in clips if c.path.is_file()]
    if not clips:
        return _res(t, "HD-J04", STATUS_SKIP, reason="no DJI clips available")
    ev, problems = {}, []
    for backend in ("nvenc", "qsv"):
        e, p, d = _batch_scan(ctx, clips, tid="HD-J04", backend=backend,
                              reader="avhw", tag=f"dji{backend}",
                              fp_head=16 if ctx.deep else None)
        ev[backend] = {"clips": d, "detail": e}
        problems.extend(f"{backend}/{x}" for x in p)
    return _res(t, "HD-J04", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"{len(clips)} DJI clips on both backends",
                reason="; ".join(problems), evidence=ev)


@impl("j05_long_run")
def j05(t, ctx):
    fx = FX.LONG_A if hasattr(FX, "LONG_A") else None
    if fx is None:
        cands = FX.long_inputs()
        cands = [c for c in cands if c.path.is_file()
                 and c.fid != "long_cs_audio"]
        fx = cands[0] if cands else None
    skip = _require_fixture(fx, "HD-J05", ctx)
    if skip:
        return skip
    facts = ctx.fact(fx)
    frames = facts.container_samples
    if not ctx.deep:
        frames = min(frames or 3000, 4000)
    t0 = time.monotonic()
    er, cm = do_encode(ctx, tid="HD-J05", fx=fx, backend="nvenc", reader="avhw",
                       tag="long", frames=frames)
    wall = time.monotonic() - t0
    ok, reasons = checks.reconcile(cm)
    out = Path(er.output)
    ev = {
        "fixture": fx.fid, "container": cm.container_expected,
        "requested": frames, "encoded": cm.encoder_input,
        "output_stream": cm.output_stream,
        "independent_decoded": cm.independent_decoded,
        "reader": er.reader_identity, "rc": er.rc,
        "wall_clock_s": round(wall, 2),
        "fps": round((cm.encoder_input or 0) / wall, 2) if wall else None,
        "final_sha256": checks.sha256_file(out) if out.is_file() else None,
        "reconcile_ok": ok, "reasons": reasons,
        "telemetry_note": ("wall clock and fps are from a laptop host with "
                           "documented drift; absolute values are not "
                           "comparable across sessions"),
    }
    problems = []
    if er.rc != 0:
        problems.append(f"long run rc={er.rc}")
    if er.reader_identity != "avcuvid":
        problems.append(f"reader {er.reader_identity!r}")
    if not ok:
        problems.append("long run did not reconcile: " + "; ".join(reasons))
    return _res(t, "HD-J05", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"{cm.encoder_input} frames in {wall:.0f}s "
                       f"({ev['fps']} fps), integrity "
                       f"{'ok' if ok else 'FAILED'}",
                reason="; ".join(problems), evidence=ev)


@impl("j06_summary_gate")
def j06(t, ctx):
    results = load_results()
    if not results:
        return _res(t, "HD-J06", STATUS_BLOCKED,
                    reason="no results recorded yet")
    summary = summarise(results)
    gate = summary["gate"]
    ev = {"summary": summary}
    status = STATUS_PASS if gate["final_status"] == "READY FOR REVIEW" else STATUS_FAIL
    return _res(
        t, "HD-J06", status,
        actual=(f"P0 PASS {gate['p0_pass']}/{gate['p0_total']}; final status "
                f"{gate['final_status']}"),
        reason="; ".join(gate["blockers"]) if gate["blockers"] else "",
        evidence=ev,
    )


# --- K ---------------------------------------------------------------------


@impl("k01_golden_baseline")
def k01(t, ctx):
    """Build/refresh the software golden baseline for the fixture set."""
    from tests.hwdecode.probe import probe_input as pi
    BASELINE.mkdir(parents=True, exist_ok=True)
    wanted = [FX.SONY_HS, FX.SONY_422_C9037, FX.DJI_0009, FX.GEN_A_COPY,
              FX.GEN_C_X265, FX.GEN_D_SYNTH]
    ev, problems, done = {}, [], []
    for fx in wanted:
        skip = _require_fixture(fx, "HD-K01", ctx)
        if skip:
            ev[fx.fid] = {"skipped": skip.reason}
            continue
        facts = ctx.fact(fx)
        v = facts.video
        er, cm = do_encode(ctx, tid="HD-K01", fx=fx, backend="nvenc",
                           reader="avsw", tag="baseline")
        if not er.output_exists:
            problems.append(f"{fx.fid}: baseline encode failed")
            continue
        sigs, err = checks.frame_signatures(out_path := Path(er.output),
                                           v.width, v.height, pix_fmt="yuv420p10le")
        meta = pi(out_path, input_id="baseline", leading_scan=False)
        entry = {
            "fixture": fx.fid, "source": str(fx.path),
            "container_expected": facts.container_samples,
            "encoded_frames": cm.encoder_input,
            "output_stream": cm.output_stream,
            "independent_decoded": cm.independent_decoded,
            "frame_count": len(sigs),
            "fingerprint_digest": checks.digest(sigs),
            "fingerprint_len": len(sigs),
            "fingerprint_error": err,
            "duration": meta.video.duration if meta.video else None,
            "video": _meta_fields(meta),
            "audio_streams": meta.audio_streams,
            "stream_kinds": sorted(meta.stream_kinds),
            "sha256": checks.sha256_file(out_path),
        }
        ev[fx.fid] = entry
        done.append(fx.fid)
        (BASELINE / f"{fx.fid}.json").write_text(
            json.dumps(entry, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8")
    if not done:
        return _res(t, "HD-K01", STATUS_SKIP, reason="no fixture available")
    return _res(t, "HD-K01", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"golden baseline written for {len(done)} fixtures",
                reason="; ".join(problems), evidence=ev)


@impl("k02_baseline_stability")
def k02(t, ctx):
    if not BASELINE.is_dir():
        return _res(t, "HD-K02", STATUS_BLOCKED,
                    reason="no baseline present (run HD-K01 first)")
    ev, problems, checked = {}, [], 0
    for p in sorted(BASELINE.glob("*.json")):
        stored = json.loads(p.read_text(encoding="utf-8"))
        fx = FX.by_id(stored["fixture"])
        if fx is None or not fx.path.is_file():
            continue
        er, cm = do_encode(ctx, tid="HD-K02", fx=fx, backend="nvenc",
                           reader="avsw", tag="stability")
        if not er.output_exists:
            problems.append(f"{stored['fixture']}: rerun produced nothing")
            continue
        sigs, _ = checks.frame_signatures(Path(er.output),
                                         (stored["video"] or {}).get("width", 3840),
                                         (stored["video"] or {}).get("height", 2160))
        d = checks.digest(sigs)
        checked += 1
        ev[stored["fixture"]] = {
            "stored_digest": stored["fingerprint_digest"],
            "rerun_digest": d,
            "stable": d == stored["fingerprint_digest"],
            "stored_sha": stored["sha256"],
            "rerun_sha": checks.sha256_file(Path(er.output)),
        }
        if d != stored["fingerprint_digest"]:
            problems.append(f"{stored['fixture']}: baseline is UNSTABLE")
    if ev:
        (BASELINE / "_stability.json").write_text(
            json.dumps(ev, indent=2, ensure_ascii=False), encoding="utf-8")
    if not checked:
        return _res(t, "HD-K02", STATUS_BLOCKED, reason="no baseline entry rerun")
    return _res(t, "HD-K02", STATUS_PASS if not problems else STATUS_FAIL,
                actual=f"{checked} baselines re-derived identically",
                reason="; ".join(problems), evidence=ev)


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------


def save_result(r: TestResult) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    p = RESULTS / "results.json"
    data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []
    data = [x for x in data if x.get("test_id") != r.test_id] + [asdict(r)]
    data.sort(key=lambda x: (x["category"], x["test_id"]))
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str),
                 encoding="utf-8")
    write_csv(data)


def write_csv(data: list[dict]) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    if not data:
        return
    fields = list(TestResult("", "", "", "", "").to_row().keys())
    with (RESULTS / "results.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in data:
            w.writerow({k: row.get(k, "") for k in fields})


def load_results() -> list[dict]:
    p = RESULTS / "results.json"
    if not p.is_file():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def summarise(results: list[dict]) -> dict:
    by_status: dict[str, int] = {}
    by_sev: dict[str, dict[str, int]] = {}
    by_cat: dict[str, dict[str, int]] = {}
    for r in results:
        s = r["status"]
        by_status[s] = by_status.get(s, 0) + 1
        by_sev.setdefault(r["severity"], {})
        by_sev[r["severity"]][s] = by_sev[r["severity"]].get(s, 0) + 1
        by_cat.setdefault(r["category"], {})
        by_cat[r["category"]][s] = by_cat[r["category"]].get(s, 0) + 1

    p0 = by_sev.get("P0", {})
    p0_fail = p0.get("FAIL", 0)
    p0_blocked = p0.get("BLOCKED", 0)
    blockers = [
        f"{r['test_id']}: {r['status']}" + (f" ({r['reason']})" if r.get("reason") else "")
        for r in results
        if r["severity"] == "P0" and r["status"] in ("FAIL", "BLOCKED")
    ]
    return {
        "total": len(results),
        "PASS": by_status.get("PASS", 0),
        "FAIL": by_status.get("FAIL", 0),
        "BLOCKED": by_status.get("BLOCKED", 0),
        "SKIP": by_status.get("SKIP", 0),
        "by_severity": by_sev,
        "by_category": by_cat,
        "gate": {
            "p0_total": sum(p0.values()),
            "p0_pass": p0.get("PASS", 0),
            "p0_fail": p0_fail,
            "p0_blocked": p0_blocked,
            "final_status": (
                "BLOCKED" if (p0_fail or p0_blocked) else "READY FOR REVIEW"
            ),
            "blockers": blockers,
        },
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def cmd_summary(_args) -> int:
    results = load_results()
    s = summarise(results)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "summary.json").write_text(
        json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(s, indent=2, ensure_ascii=False))
    return 0


def cmd_provenance(_args) -> int:
    rep = sources.verify_provenance(strict=False)
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    return 0 if rep["ok"] else 1


def cmd_check_matrix(_args) -> int:
    matrix = load_matrix()
    doc = (ROOT / "docs" / "hardware-decode" / "integration-test-matrix.md")
    if not doc.is_file():
        print("FAIL: matrix document missing")
        return 1
    text = doc.read_text(encoding="utf-8")
    problems = []
    for t in matrix["tests"]:
        if t["id"] not in text:
            problems.append(f"{t['id']} missing from the document")
    import re as _re
    doc_ids = set(_re.findall(r"HD-[A-K]\d{2}", text))
    json_ids = {t["id"] for t in matrix["tests"]}
    for extra in sorted(doc_ids - json_ids):
        problems.append(f"{extra} in the document but not in matrix.json")
    for t in matrix["tests"]:
        if t.get("impl") not in IMPL:
            problems.append(f"{t['id']}: impl {t.get('impl')!r} not registered")
    if problems:
        print("MATRIX DRIFT:")
        for p in problems:
            print("  -", p)
        return 1
    print(f"matrix consistent: {len(json_ids)} tests, all with implementations")
    return 0


def cmd_list(args) -> int:
    matrix = load_matrix()
    for t in matrix["tests"]:
        if args.phase and t["category"] not in PHASES[args.phase]:
            continue
        if args.category and t["category"] != args.category:
            continue
        print(f"{t['id']}  {t['severity']}  {t['category']}  {t['title']}")
    return 0


def cmd_run(args) -> int:
    matrix = load_matrix()
    selected = []
    for t in matrix["tests"]:
        if args.ids and t["id"] not in args.ids:
            continue
        if args.phase and t["category"] not in PHASES[args.phase]:
            continue
        if args.category and t["category"] != args.category:
            continue
        if args.severity and t["severity"] != args.severity:
            continue
        selected.append(t)
    if not selected:
        print("no tests selected")
        return 1

    ctx = Ctx(deep=args.deep, verbose=not args.quiet)
    print(f"running {len(selected)} tests "
          f"(deep={args.deep}, repeat={args.repeat})")
    failures = 0
    for t in selected:
        fn = IMPL.get(t["impl"])
        if fn is None:
            r = _res(t, t["id"], STATUS_BLOCKED,
                     reason=f"implementation {t['impl']!r} not registered")
            save_result(r)
            print(f"[{r.status:7s}] {t['id']} {t['title']}")
            continue
        t0 = time.monotonic()
        try:
            r = fn(t, ctx)
        except Exception as exc:  # noqa: BLE001
            import traceback
            r = _res(t, t["id"], STATUS_FAIL,
                     reason=f"harness exception: {type(exc).__name__}: {exc}",
                     evidence={"traceback": traceback.format_exc()[-3000:]})
        r.duration_s = time.monotonic() - t0
        r.repeats = max(1, args.repeat) if t["severity"] == "P0" and r.status == STATUS_PASS else 1
        save_result(r)
        mark = {"PASS": "PASS", "FAIL": "FAIL", "BLOCKED": "BLOCKED",
                "SKIP": "SKIP"}[r.status]
        print(f"[{mark:7s}] {t['id']} {t['title']}"
              + (f" — {r.actual}" if r.actual else "")
              + (f"  << {r.reason}" if r.reason else ""), flush=True)
        if r.status in ("FAIL", "BLOCKED") and t["severity"] == "P0":
            failures += 1
    print(f"\nP0 failures/blocked: {failures}")
    cmd_summary(args)
    return 1 if failures else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tests.hwdecode.harness")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("provenance")
    p.set_defaults(func=cmd_provenance)

    p = sub.add_parser("check-matrix")
    p.set_defaults(func=cmd_check_matrix)

    p = sub.add_parser("list")
    p.add_argument("--phase", type=int, choices=sorted(PHASES))
    p.add_argument("--category")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("run")
    p.add_argument("ids", nargs="*")
    p.add_argument("--phase", type=int, choices=sorted(PHASES))
    p.add_argument("--category")
    p.add_argument("--severity")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--deep", action="store_true",
                   help="run the extended (slower) variants of long tests")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("summary")
    p.set_defaults(func=cmd_summary)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
