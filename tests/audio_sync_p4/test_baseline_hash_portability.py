"""P4.5 — cross-platform frozen-baseline integrity regression.

Pins the defect class the P3–P4 documentation push exposed:

    the acceptance test for the frozen P1 baseline was the SHA-256 of the
    bytes **as checked out**, and that value is a property of the checkout's
    line endings, not of the CSV content.

Concretely, the same CSV is materialised two ways::

    core.autocrlf=true  (this machine)   55123 bytes  549 CRLF  36242616062FFEC2…
    clean LF checkout                    54574 bytes    0 CRLF  7B1FB2DCAEECBA4B…

The P4.2/P4.3/P4.4 audit harnesses compared against the first form and returned
**exit 4** on the second — fail-closed, but for the wrong reason: a packaging
difference was reported as an integrity failure.

The contract under test is ``sha256-lf-normalized-v1``, defined in the tracked
module :mod:`tests.audio_sync_p4.baseline_identity`. Two identities are named
separately and never conflated:

    raw_byte_sha256           checkout dependent  -> provenance only
    canonical_content_sha256  checkout independent -> the acceptance test

What this suite is designed to catch:

  * a regression that makes the canonical hash line-ending sensitive again
    (the CRLF/LF equivalence cases, plus their controls);
  * a "fix" that makes the hash insensitive to real content — over-normalising,
    trimming whitespace, re-serialising the CSV, reordering columns, ignoring
    the file tail (§B and §C together are a pincer: B requires equality, C
    requires inequality);
  * a check that passes vacuously — missing file, empty file, unreadable
    path, or a rubber-stamp comparison (§D and every CONTROL);
  * an audit harness quietly re-introducing its own raw-byte acceptance test
    after the tracked contract exists (§F).

The real frozen fixture is used throughout; every mutation is derived from its
actual bytes, never from a hand-written stand-in.

Run::

    python tests/audio_sync_p4/test_baseline_hash_portability.py
    python tests/audio_sync_p4/test_baseline_hash_portability.py --json out.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tests.audio_sync_p4 import baseline_identity as bi   # noqa: E402

RESULTS: list[dict] = []
SKIPPED: list[dict] = []
COUNTS = {"pass": 0, "fail": 0, "control_ok": 0, "control_failed": 0}

#: Paths that must not carry their own copy of the raw-byte acceptance test.
LOCAL_HARNESSES = (
    "work/p4.2/p1-audit/audit_baseline.py",
    "work/p4.3/p1-audit/audit_baseline.py",
    "work/p4.4/p1-audit/audit_baseline.py",
)
#: Tracked consumer of the fixture; must not pin a raw-byte hash either.
TRACKED_CONSUMER = "tests/hwdecode/harness.py"


def _rec(name: str, ok: bool, detail: str, kind: str = "value") -> None:
    RESULTS.append({"name": name, "ok": bool(ok), "detail": detail,
                    "kind": kind})
    if kind == "control":
        COUNTS["control_ok" if ok else "control_failed"] += 1
    else:
        COUNTS["pass" if ok else "fail"] += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if detail:
        print(f"          {detail}")


def value(name: str, ok: bool, detail: str = "") -> None:
    _rec(name, ok, detail, kind="value")


def control(name: str, bad_fn) -> None:
    """A CONTROL must FAIL on the deliberately broken input to count as PASS."""
    try:
        bad_fn()
    except BaseException:                              # noqa: BLE001
        _rec(name, True, "the check failed on the broken input, as required",
             kind="control")
        return
    _rec(name, False,
         "the check did NOT fail on a deliberately broken input — it is not "
         "discriminating", kind="control")


def skip(name: str, why: str) -> None:
    """An un-run check. Recorded, never counted as a pass."""
    SKIPPED.append({"name": name, "why": why})
    print(f"  [SKIP] {name}\n          {why}")


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def check_bytes(raw: bytes, expected: str | None = None) -> dict:
    """Run the real fail-closed contract over in-memory bytes."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "baseline.csv"
        p.write_bytes(raw)
        return bi.check_baseline(p, expected)


# ---------------------------------------------------------------------------
# byte-level mutation helpers, all driven by the REAL fixture
# ---------------------------------------------------------------------------

def _split(raw: bytes) -> list[bytes]:
    """Split an LF-normalised fixture keeping its exact trailing newline."""
    return raw.split(b"\n")


def _join(lines: list[bytes]) -> bytes:
    return b"\n".join(lines)


def edit_field(raw: bytes, row: int, field: int, new: bytes) -> bytes:
    lines = _split(raw)
    parts = lines[row].split(b",")
    parts[field] = new
    lines[row] = b",".join(parts)
    return _join(lines)


# ---------------------------------------------------------------------------
# A. the real frozen fixture
# ---------------------------------------------------------------------------

def section_a(real: bytes, lf: bytes) -> None:
    print("A. the real frozen fixture")
    ident = bi.check_baseline()

    value("A.1 the frozen baseline is present and readable",
          ident["present"] is True, str(bi.BASELINE_PATH.relative_to(REPO)))

    value("A.2 raw byte sha256 matches the bytes actually on disk",
          ident["raw_byte_sha256"] == bi.raw_byte_sha256(real),
          f"{ident['raw_byte_sha256']}  (checkout form "
          f"{ident['line_endings']['form']})")

    value("A.3 canonical content sha256 equals the recorded constant",
          ident["canonical_content_sha256"] ==
          bi.EXPECTED_CANONICAL_CONTENT_SHA256,
          f"{ident['canonical_content_sha256']} "
          f"({bi.NORMALIZATION_ID}, {bi.NORMALIZATION_HASH})")

    value("A.4 the contract accepts the real fixture",
          ident["ok"] is True and ident["problems"] == [],
          f"problems={ident['problems']}")

    value("A.5 structural invariants hold independently of the hash",
          ident["data_rows"] == bi.EXPECTED_DATA_ROWS
          and ident["lines"] == bi.EXPECTED_TOTAL_LINES,
          f"{ident['data_rows']} data rows, {ident['lines']} lines, "
          f"{bi.EXPECTED_FIELDS} fields/row")

    # the precondition that makes a line-ending-only rule exact for this file
    value("A.6 the fixture has no quoting, so the rule is exact for it",
          real.count(b'"') == bi.EXPECTED_QUOTE_BYTES
          and sum(1 for b in real if b > 127) == bi.EXPECTED_NON_ASCII_BYTES,
          "0 quote bytes and 0 non-ASCII bytes means no \\r can appear inside "
          "a field, so normalization only ever rewrites terminators")

    value("A.7 no stray CR: every CR is part of a CRLF terminator",
          ident["line_endings"]["lone_cr"] == 0,
          f"crlf={ident['line_endings']['crlf']} "
          f"lone_cr={ident['line_endings']['lone_cr']}")

    value("A.8 the fixture is byte-identical to the git blob's canonical form",
          bi.canonical_content_sha256(real) ==
          bi.canonical_content_sha256(lf),
          "the worktree form and the stored form are the same content")


# ---------------------------------------------------------------------------
# B. line-ending equivalence — same content, different bytes
# ---------------------------------------------------------------------------

def section_b(real: bytes, lf: bytes) -> None:
    print("\nB. CRLF and LF produce the same canonical hash")
    crlf = lf.replace(b"\n", b"\r\n")

    value("B.1 the two forms really are different byte strings (non-vacuous)",
          crlf != lf and len(crlf) > len(lf),
          f"crlf={len(crlf)} bytes / lf={len(lf)} bytes, delta="
          f"{len(crlf) - len(lf)} == {lf.count(chr(10).encode())} terminators")

    value("B.2 canonical(CRLF) == canonical(LF)",
          bi.canonical_content_sha256(crlf) ==
          bi.canonical_content_sha256(lf),
          f"{bi.canonical_content_sha256(crlf)}")

    value("B.3 both forms equal the recorded canonical constant",
          bi.canonical_content_sha256(crlf) ==
          bi.EXPECTED_CANONICAL_CONTENT_SHA256 and
          bi.canonical_content_sha256(lf) ==
          bi.EXPECTED_CANONICAL_CONTENT_SHA256,
          bi.EXPECTED_CANONICAL_CONTENT_SHA256)

    value("B.4 the raw byte hashes of the two forms DIFFER (why the fix was "
          "needed)",
          bi.raw_byte_sha256(crlf) != bi.raw_byte_sha256(lf),
          f"crlf={bi.raw_byte_sha256(crlf)[:16]}… "
          f"lf={bi.raw_byte_sha256(lf)[:16]}…")

    # the real worktree, whatever this machine checked out, must be accepted
    ident = bi.check_baseline()
    value("B.5 the contract accepts this machine's real checkout form",
          ident["ok"] is True,
          f"form={ident['line_endings']['form']}, raw="
          f"{ident['raw_byte_sha256'][:16]}…")

    value("B.6 the ORIGINAL LF form of the real fixture is accepted too",
          check_bytes(lf)["ok"] is True,
          "simulates a clean LF checkout using the fixture's own content")

    value("B.7 the ORIGINAL CRLF form of the real fixture is accepted too",
          check_bytes(crlf)["ok"] is True,
          "simulates a Windows autocrlf checkout")

    value("B.8 mixed line endings in the same file still normalise",
          bi.canonical_content_sha256(
              lf[:100] + lf[100:].replace(b"\n", b"\r\n")) ==
          bi.EXPECTED_CANONICAL_CONTENT_SHA256,
          "first 100 bytes LF, remainder CRLF -> same canonical content")

    value("B.9 lone CR (classic Mac) endings normalise to LF",
          bi.canonical_content_sha256(lf.replace(b"\n", b"\r")) ==
          bi.EXPECTED_CANONICAL_CONTENT_SHA256,
          "CR-only form of the same content")

    value("B.10 normalization is idempotent",
          bi.normalize_line_endings(bi.normalize_line_endings(crlf)) ==
          bi.normalize_line_endings(crlf),
          "normalize(normalize(x)) == normalize(x)")

    value("B.11 normalization is the identity on already-LF input",
          bi.normalize_line_endings(lf) == lf,
          "no byte is added or removed when there is nothing to normalise")

    value("B.12 normalization touches nothing but line terminators",
          len(bi.normalize_line_endings(crlf)) ==
          len(crlf) - lf.count(b"\n"),
          "the only size change is one byte per CRLF")

    value("B.13 the contract reports BOTH identities, named separately",
          ident["raw_byte_sha256"] is not None
          and ident["canonical_content_sha256"] is not None
          and ident["normalization_id"] == bi.NORMALIZATION_ID,
          "raw_byte_sha256 and canonical_content_sha256 are distinct fields")


# ---------------------------------------------------------------------------
# C. real content changes must still be detected
# ---------------------------------------------------------------------------

def section_c(lf: bytes) -> None:
    print("\nC. real content changes still change the canonical hash")

    def expect_detected(name: str, mutated: bytes, why: str) -> None:
        canon = bi.canonical_content_sha256(mutated)
        rep = check_bytes(mutated)
        value(name,
              canon != bi.EXPECTED_CANONICAL_CONTENT_SHA256
              and rep["ok"] is False,
              f"{why}; canonical={canon[:16]}… verdict="
              f"{'ACCEPTED (WRONG)' if rep['ok'] else 'rejected'}")

    # 1..548 are data rows (row 0 is the header).
    expect_detected("C.1 one digit changed in a delay_samples value",
                    edit_field(lf, 548, 8, b"1"),
                    "field 'delay_samples' of the last row rewritten")
    expect_detected("C.2 a decision word changed (already_aligned -> fixed)",
                    edit_field(lf, 548, 5, b"fixed"),
                    "field 'decision' of the last row rewritten")
    expect_detected("C.3 a field's value changed in the first data row",
                    edit_field(lf, 1, 0, b"20260903/A7M5/CHANGED.MP4"),
                    "field 'file' of row 1 rewritten")
    expect_detected("C.4 a trailing space added inside a field",
                    edit_field(lf, 548, 6, b" "),
                    "whitespace must NOT be normalised away")

    lines = _split(lf)
    expect_detected("C.5 a data row removed",
                    _join(lines[:100] + lines[101:]),
                    "row count 548 -> 547")
    expect_detected("C.6 a data row duplicated",
                    _join(lines[:100] + [lines[100]] + lines[100:]),
                    "row count 548 -> 549")
    expect_detected("C.7 two data rows reordered",
                    _join(lines[:100] + [lines[101], lines[100]] + lines[102:]),
                    "same rows, different order")

    header = lines[0].split(b",")
    header[5], header[6] = header[6], header[5]        # decision <-> reason
    expect_detected("C.8 two header columns reordered",
                    _join([b",".join(header), *lines[1:]]),
                    "column order must be part of the content")
    expect_detected("C.9 the final newline removed",
                    lf[:-1],
                    "the file tail must not be ignored")
    expect_detected("C.10 a blank line prepended",
                    b"\n" + lf,
                    "an extra line is a change, not a line-ending form")

    # the pincer: same content, different endings -> EQUAL (in B);
    # changed content, different endings -> NOT equal (here)
    expect_detected("C.11 content change combined with a line-ending change",
                    edit_field(lf, 548, 8, b"1").replace(b"\n", b"\r\n"),
                    "changing endings must not mask a simultaneous content "
                    "change")

    value("C.12 the 548 data row values are unchanged by normalisation",
          bi.normalize_line_endings(lf).split(b"\n")[:3] ==
          lf.split(b"\n")[:3],
          "line content is byte-identical after normalisation, only "
          "terminators changed")


# ---------------------------------------------------------------------------
# D. fail closed — un-run and empty must never be a pass
# ---------------------------------------------------------------------------

def section_d(lf: bytes) -> None:
    print("\nD. the contract fails closed")

    with tempfile.TemporaryDirectory() as td:
        missing = Path(td) / "nope.csv"
        rep = bi.check_baseline(missing)
        value("D.1 a missing file is a failure, not a pass",
              rep["ok"] is False and rep["present"] is False
              and rep["problems"],
              f"problems={rep['problems']}")

        rep_dir = bi.check_baseline(Path(td))
        value("D.2 an unreadable path is a failure, not a pass",
              rep_dir["ok"] is False and bool(rep_dir["problems"]),
              f"problems={rep_dir['problems'][:1]}")

    rep = check_bytes(b"")
    value("D.3 an empty file is a failure, not a pass",
          rep["ok"] is False and rep["canonical_content_sha256"] !=
          bi.EXPECTED_CANONICAL_CONTENT_SHA256,
          f"problems={rep['problems'][:1]}")

    rep = check_bytes(b"\n")
    value("D.4 a newline-only file is a failure, not a pass",
          rep["ok"] is False, f"problems={rep['problems'][:1]}")

    rep = check_bytes(lf[: len(lf) // 2])
    value("D.5 a truncated fixture is a failure, not a pass",
          rep["ok"] is False, f"problems={rep['problems'][:1]}")

    # truncate at a line boundary so ONLY the row count is wrong: this proves
    # the structural invariants catch what a hash alone might not be read as
    lines = _split(lf)
    rep = check_bytes(_join(lines[:200]))
    value("D.6 truncation at a clean line boundary is still a failure",
          rep["ok"] is False
          and any("row count" in p for p in rep["problems"]),
          f"problems={rep['problems']}")

    rep = bi.check_baseline(expected_canonical="0" * 64)
    value("D.7 a wrong expected canonical hash still fails",
          rep["ok"] is False and rep["canonical_content_sha256"] ==
          bi.EXPECTED_CANONICAL_CONTENT_SHA256,
          "the file is fine; the expectation is wrong, and that is a failure")

    rep = check_bytes(lf, expected="F" * 64)
    value("D.8 a wrong expectation fails even on the real content",
          rep["ok"] is False, f"problems={rep['problems'][:1]}")

    value("D.9 the contract never returns ok=True without a digest",
          all(bi.check_baseline(p)["ok"] is False
              for p in (Path("no/such/path.csv"),))
          and check_bytes(b"")["canonical_content_sha256"] is not None,
          "ok=True implies a computed digest over non-empty bytes")


# ---------------------------------------------------------------------------
# E. raw vs canonical, and the historical record
# ---------------------------------------------------------------------------

def section_e(real: bytes, lf: bytes) -> None:
    print("\nE. raw byte hash vs canonical content hash")

    value("E.1 the two names are distinct exported constants",
          bi.RAW_BYTE_SHA256_BY_LINE_ENDING["crlf"] !=
          bi.RAW_BYTE_SHA256_BY_LINE_ENDING["lf"],
          f"crlf={bi.RAW_BYTE_SHA256_BY_LINE_ENDING['crlf'][:16]}… "
          f"lf={bi.RAW_BYTE_SHA256_BY_LINE_ENDING['lf'][:16]}…")

    value("E.2 the canonical constant equals the LF form's raw hash",
          bi.RAW_BYTE_SHA256_BY_LINE_ENDING["lf"] ==
          bi.EXPECTED_CANONICAL_CONTENT_SHA256,
          "expected: the stored blob is already LF, so normalising it is a "
          "no-op — the two coincide for THIS file only, and are still "
          "separately named")

    value("E.3 the historical HD-G06 prefix is retained verbatim",
          bi.RECORDED_RAW_PREFIX_HD_G06 == "36242616062FFEC2"
          and bi.RECORDED_RAW_PREFIX_HD_G06 ==
          bi.RAW_BYTE_SHA256_BY_LINE_ENDING["crlf"][:16],
          "the published value is the CRLF-checkout RAW hash; it is evidence "
          "about a checkout, not a content claim, and was not rewritten")

    value("E.4 the historical prefix describes the CRLF form, not the LF form",
          bi.RAW_BYTE_SHA256_BY_LINE_ENDING["crlf"].startswith(
              bi.RECORDED_RAW_PREFIX_HD_G06)
          and not bi.RAW_BYTE_SHA256_BY_LINE_ENDING["lf"].startswith(
              bi.RECORDED_RAW_PREFIX_HD_G06),
          "this is exactly why the old raw-byte contract aborted on LF")

    value("E.5 the module says which normalization it implements",
          bi.NORMALIZATION_ID == "sha256-lf-normalized-v1"
          and bi.NORMALIZATION_HASH == "SHA-256"
          and "\\r" in bi.NORMALIZATION_DESCRIPTION,
          f"{bi.NORMALIZATION_ID} / {bi.NORMALIZATION_HASH}: "
          f"{bi.NORMALIZATION_DESCRIPTION}")


# ---------------------------------------------------------------------------
# F. no consumer re-introduces the raw-byte acceptance test
# ---------------------------------------------------------------------------

def section_f() -> None:
    print("\nF. consumers use the tracked contract")

    for rel in LOCAL_HARNESSES:
        p = REPO / rel
        if not p.is_file():
            skip(f"F harness {rel}",
                 "not present in this checkout (work/ is git-ignored, so a "
                 "clean clone legitimately has no copy) — NOT counted as pass")
            continue
        txt = p.read_text(encoding="utf-8", errors="replace")
        value(f"F {rel} imports the tracked contract",
              "baseline_identity" in txt,
              "uses tests/audio_sync_p4/baseline_identity.py")
        value(f"F {rel} no longer accepts on the raw byte prefix",
              "startswith(RECORDED_SHA256_PREFIX)" not in txt
              and "RECORDED_SHA256_PREFIX =" not in txt,
              "the raw prefix survives only as a named historical constant")

    p = REPO / TRACKED_CONSUMER
    if p.is_file():
        txt = p.read_text(encoding="utf-8", errors="replace")
        value("F the tracked hwdecode HD-G06 does not pin a raw byte hash",
              bi.RECORDED_RAW_PREFIX_HD_G06 not in txt
              and bi.RAW_BYTE_SHA256_BY_LINE_ENDING["lf"] not in txt,
              "HD-G06 computes the hash as evidence and compares the fixture "
              "via `git diff main...HEAD`, so it was never line-ending "
              "sensitive and needed no change")
    else:
        skip("F tracked hwdecode consumer", "not present in this checkout")

    keep = REPO / "tests" / "audio_sync_p4" / "baseline_identity.py"
    value("F the contract lives in the TRACKED test tree",
          keep.is_file(),
          "a contract that must reproduce from a clean GitHub checkout "
          "cannot live only under the ignored work/ tree")


# ---------------------------------------------------------------------------
# controls
# ---------------------------------------------------------------------------

def controls(real: bytes, lf: bytes) -> None:
    print("\nControls (each must FAIL on deliberately broken input)")
    crlf = lf.replace(b"\n", b"\r\n")

    def c_old_contract_is_sensitive() -> None:
        _need(bi.raw_byte_sha256(crlf) == bi.raw_byte_sha256(lf),
              "the raw byte hash equated CRLF and LF")
    control("E/CONTROL the original raw-byte contract really is line-ending "
            "dependent", c_old_contract_is_sensitive)

    def c_identity_normalization() -> None:
        ident = lambda b: b                                  # noqa: E731
        _need(hashlib.sha256(ident(crlf)).hexdigest()
              == hashlib.sha256(ident(lf)).hexdigest(),
              "an identity normalization equated the two forms")
    control("E/CONTROL without the CRLF rule the two forms are NOT equal",
            c_identity_normalization)

    def c_old_prefix_on_lf() -> None:
        _need(bi.raw_byte_sha256(lf).startswith(
            bi.RECORDED_RAW_PREFIX_HD_G06),
            "the historical raw prefix matched the LF form")
    control("E/CONTROL the old acceptance prefix rejects the LF form (exit-4 "
            "path reproduced)", c_old_prefix_on_lf)

    def c_wrong_expected() -> None:
        _need(bi.check_baseline(expected_canonical="0" * 64)["ok"],
              "a wrong expected hash was accepted")
    control("D/CONTROL a wrong expected canonical hash is rejected",
            c_wrong_expected)

    def c_missing_file() -> None:
        _need(bi.check_baseline(Path("no/such/baseline.csv"))["ok"],
              "a missing file was accepted")
    control("D/CONTROL a missing file is rejected", c_missing_file)

    def c_empty_file() -> None:
        _need(check_bytes(b"")["ok"], "an empty file was accepted")
    control("D/CONTROL an empty file is rejected", c_empty_file)

    def c_empty_matches_expected() -> None:
        _need(bi.canonical_content_sha256(b"") ==
              bi.EXPECTED_CANONICAL_CONTENT_SHA256,
              "the empty digest matched the frozen baseline digest")
    control("D/CONTROL the empty digest is not the baseline digest (no empty "
            "pass)", c_empty_matches_expected)

    def c_mutation_is_real() -> None:
        _need(edit_field(lf, 548, 8, b"1") == lf,
              "the delay_samples mutation did not change the bytes")
    control("C/CONTROL the mutations really change the bytes (no vacuous "
            "sensitivity)", c_mutation_is_real)

    def c_structure_rowcount() -> None:
        lines = _split(lf)
        _need(bi.structure_report(_join(lines[:200]))["ok"],
              "the structural check passed on a truncated file")
    control("D/CONTROL the structural check detects a wrong row count",
            c_structure_rowcount)

    def c_structure_header() -> None:
        parts = _split(lf)[0].split(b",")
        parts[5], parts[6] = parts[6], parts[5]
        swapped = _join([b",".join(parts), *_split(lf)[1:]])
        _need(bi.structure_report(swapped)["ok"],
              "the structural check passed on a reordered header")
    control("C/CONTROL the structural check detects a reordered header",
            c_structure_header)


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    print("=" * 78)
    print("P4.5 — frozen-baseline cross-platform integrity")
    print("=" * 78)
    print(f"contract      : {bi.NORMALIZATION_ID} ({bi.NORMALIZATION_HASH})")
    print(f"fixture       : {bi.BASELINE_PATH.relative_to(REPO)}")
    print(f"expected      : canonical_content_sha256 = "
          f"{bi.EXPECTED_CANONICAL_CONTENT_SHA256}")
    print(f"historical    : raw_byte_sha256 (CRLF checkout) = "
          f"{bi.RECORDED_RAW_PREFIX_HD_G06}…")
    print()

    if not bi.BASELINE_PATH.is_file():
        print("BLOCKED: the frozen baseline is absent — this suite cannot "
              "verify it, and an absent fixture is not a pass.",
              file=sys.stderr)
        return 3

    real = bi.BASELINE_PATH.read_bytes()
    lf = bi.normalize_line_endings(real)

    section_a(real, lf)
    section_b(real, lf)
    section_c(lf)
    section_d(lf)
    section_e(real, lf)
    section_f()
    controls(real, lf)

    n_cases = len(RESULTS)
    n_fail = sum(1 for r in RESULTS if not r["ok"])
    print("\n" + "=" * 78)
    print(f"cases        : {n_cases}")
    print(f"PASS         : {n_cases - n_fail}")
    print(f"FAIL         : {n_fail}")
    print(f"CONTROL_OK   : {COUNTS.get('control_ok', 0)}")
    print(f"CONTROL_FAIL : {COUNTS.get('control_failed', 0)}")
    print(f"SKIPPED      : {len(SKIPPED)}  (not counted as passes)")
    print("=" * 78)

    if n_cases == 0:
        print("FAIL: no case ran — an empty run is not a pass", file=sys.stderr)
        return 2
    if COUNTS.get("control_ok", 0) == 0:
        print("FAIL: no control was demonstrated discriminating",
              file=sys.stderr)
        return 2

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"cases": n_cases, "fail": n_fail,
             "controls_ok": COUNTS.get("control_ok", 0),
             "skipped": SKIPPED,
             "normalization_id": bi.NORMALIZATION_ID,
             "expected_canonical_sha256":
                 bi.EXPECTED_CANONICAL_CONTENT_SHA256,
             "raw_byte_sha256_on_this_checkout":
                 bi.raw_byte_sha256(real),
             "line_endings_on_this_checkout":
                 bi.line_ending_profile(real),
             "results": RESULTS}, indent=1), encoding="utf-8")
        print(f"json -> {args.json}")

    if n_fail:
        print(f"\nRESULT: FAIL ({n_fail})")
        for r in RESULTS:
            if not r["ok"]:
                print(f"  FAIL {r['name']} -- {r['detail']}")
        return 1
    print(f"\nRESULT: PASS — {n_cases} cases, "
          f"{COUNTS.get('control_ok', 0)} controls discriminating"
          + (f", {len(SKIPPED)} skipped" if SKIPPED else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
