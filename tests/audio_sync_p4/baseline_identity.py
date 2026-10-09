"""Frozen-baseline identity contract — ``sha256-lf-normalized-v1``.

The P1 137-corpus baseline
(``tests/fixtures/channel_sync/a7m5_real_137_baseline.csv``) is a **frozen
validation artefact**: every P1/P4 revalidation replays it, and P4.2/P4.3/P4.4
each refused to proceed when its hash did not match the recorded value.

The original check compared the SHA-256 of the **file as checked out** against
``36242616062FFEC2…``. That value is not a property of the CSV content — it is a
property of the CSV content *plus the checkout's line endings*. The file is
stored in git as LF (549 bytes smaller); on a machine with
``core.autocrlf=true`` it is materialised with CRLF. Both are the same CSV:

    Windows worktree (CRLF)   55123 bytes   raw sha256 36242616062FFEC2…
    Linux/macOS checkout (LF) 54574 bytes   raw sha256 7B1FB2DCAEECBA4B…

So the byte hash was line-ending sensitive, and the audit harnesses aborted with
exit 4 on a clean LF checkout — fail-closed, but for the wrong reason: a
*packaging* difference was being reported as an *integrity* failure.

This module replaces that with an explicit, versioned normalization. Two
identities are now named separately and never conflated:

``raw_byte_sha256``
    Identifies the bytes on disk. Line-ending dependent. Reported for
    provenance; **never** used as the acceptance test.

``canonical_content_sha256``
    SHA-256 of the bytes after ``sha256-lf-normalized-v1``. Line-ending
    independent, and changes when any real content changes. **This** is the
    acceptance test.

The historical raw hash is retained (``RAW_BYTE_SHA256_BY_LINE_ENDING``) as
historical evidence, not deleted: it is the correct description of the bytes the
P4.2/P4.3/P4.4 runs actually read on this machine.

Normalization rule — deliberately minimal::

    b"\\r\\n"  ->  b"\\n"          (CRLF -> LF)
    b"\\r"     ->  b"\\n"          (lone CR -> LF, e.g. classic Mac endings)

Nothing else is touched. No whitespace is stripped, no field is dropped, no row
is added or removed, no CSV is re-serialised, no column is reordered, and no
trailing content is ignored. A real data change therefore still changes the
canonical hash.

Precondition, asserted by the regression rather than assumed: the frozen
baseline contains **no** ``"`` quoting, so no ``\\r`` byte can occur inside a
quoted field. For this file the normalization is therefore exact — it only ever
rewrites line terminators.

This module deliberately lives in the **tracked** test tree. The P4.2/P4.3/P4.4
``work/p4.*/p1-audit/audit_baseline.py`` harnesses are ignored, local-only files
and cannot be the permanent home of a contract that has to reproduce from a
clean GitHub checkout.
"""

from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

BASELINE_PATH = (REPO / "tests" / "fixtures" / "channel_sync"
                 / "a7m5_real_137_baseline.csv")

#: Name and version of the normalization. Changing the rule requires a new id.
NORMALIZATION_ID = "sha256-lf-normalized-v1"
NORMALIZATION_HASH = "SHA-256"
NORMALIZATION_DESCRIPTION = (
    'b"\\r\\n" -> b"\\n", then any remaining b"\\r" -> b"\\n"; '
    "no other byte is added, removed, reordered or ignored"
)

#: The acceptance test. Line-ending independent; content sensitive.
EXPECTED_CANONICAL_CONTENT_SHA256 = (
    "7B1FB2DCAEECBA4BD170973C336F470D0E7B6746A551E41A88D9032D3A6B464B"
)

#: Historical raw-byte hashes, kept as provenance. These are *observations of a
#: checkout*, not assertions about content, and are never compared for pass/fail.
RAW_BYTE_SHA256_BY_LINE_ENDING = {
    "crlf": "36242616062FFEC24F93062F6405273E9E12E90069ABA1FA50BFB4257306E2AF",
    "lf": "7B1FB2DCAEECBA4BD170973C336F470D0E7B6746A551E41A88D9032D3A6B464B",
}

#: The prefix as published in `docs/hardware-decode/final-report.md` HD-G06.
#: Retained verbatim; it is the CRLF-checkout form. Do **not** rewrite it.
RECORDED_RAW_PREFIX_HD_G06 = "36242616062FFEC2"

#: Structural invariants. Independent of the hash, so a hash collision or a
#: normalization bug cannot by itself make a wrong file look right.
EXPECTED_HEADER = (
    "file", "sample_rate", "codec", "stream_count", "anchor_stream",
    "decision", "reason", "delay_ms", "delay_samples", "confidence",
    "rms_dbfs", "traj_drift_ms", "drift_ppm", "constant", "usable_frames",
    "shift_samples", "residual",
)
EXPECTED_DATA_ROWS = 548
EXPECTED_TOTAL_LINES = EXPECTED_DATA_ROWS + 1          # header + data
EXPECTED_FIELDS = len(EXPECTED_HEADER)                 # 17

#: The fixture is pure ASCII with no quoting; this is what makes the
#: line-ending-only normalization exact.
EXPECTED_NON_ASCII_BYTES = 0
EXPECTED_QUOTE_BYTES = 0


def normalize_line_endings(raw: bytes) -> bytes:
    """Apply ``sha256-lf-normalized-v1``. Line endings only."""
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def raw_byte_sha256(raw: bytes) -> str:
    """SHA-256 of the bytes exactly as read. Checkout dependent."""
    return hashlib.sha256(raw).hexdigest().upper()


def canonical_content_sha256(raw: bytes) -> str:
    """SHA-256 after ``sha256-lf-normalized-v1``. Checkout independent."""
    return hashlib.sha256(normalize_line_endings(raw)).hexdigest().upper()


def line_ending_profile(raw: bytes) -> dict:
    """Count line terminators and name the checkout form."""
    crlf = raw.count(b"\r\n")
    lone_cr = raw.count(b"\r") - crlf
    lf = raw.count(b"\n")
    if lf == 0:
        form = "no-newline"
    elif crlf == 0 and lone_cr == 0:
        form = "lf"
    elif lone_cr == 0 and crlf == lf:
        form = "crlf"
    else:
        form = "mixed"
    return {"crlf": crlf, "lone_cr": lone_cr, "lf": lf, "form": form}


def structure_report(raw: bytes) -> dict:
    """Header order, field count and row count — hash-independent invariants."""
    problems: list[str] = []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return {"ok": False, "problems": [f"not valid UTF-8: {exc}"],
                "data_rows": None, "header": None, "lines": None}

    lines = text.splitlines()
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return {"ok": False, "problems": ["file is empty — no header"],
                "data_rows": 0, "header": None, "lines": len(lines)}

    rows = list(reader)
    if tuple(header) != EXPECTED_HEADER:
        problems.append(
            f"header/column order differs: {tuple(header)!r} != "
            f"{EXPECTED_HEADER!r}")
    bad = [i for i, r in enumerate(rows, start=2) if len(r) != EXPECTED_FIELDS]
    if bad:
        problems.append(
            f"{len(bad)} row(s) do not have {EXPECTED_FIELDS} fields "
            f"(first at line {bad[0]})")
    if len(rows) != EXPECTED_DATA_ROWS:
        problems.append(
            f"data row count {len(rows)} != {EXPECTED_DATA_ROWS}")
    if len(lines) != EXPECTED_TOTAL_LINES:
        problems.append(
            f"line count {len(lines)} != {EXPECTED_TOTAL_LINES}")

    return {"ok": not problems, "problems": problems, "data_rows": len(rows),
            "header": tuple(header), "lines": len(lines)}


def check_baseline(path: Path | str | None = None,
                   expected_canonical: str | None = None) -> dict:
    """Fail-closed identity check for the frozen baseline.

    Returns a dict; ``ok`` is True **only** when the file is present, readable,
    non-empty, canonically identical to ``expected_canonical`` and structurally
    intact. Every other outcome is ``ok=False`` with ``problems`` populated.

    A missing file is never a pass, and an empty file is never a pass.
    """
    p = Path(path) if path is not None else BASELINE_PATH
    want = (expected_canonical or EXPECTED_CANONICAL_CONTENT_SHA256).upper()
    problems: list[str] = []

    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        return _failed(p, want, ["baseline file is missing"],
                       NORMALIZATION_ID)
    except IsADirectoryError:
        return _failed(p, want, ["path is a directory, not a file"],
                       NORMALIZATION_ID)
    except OSError as exc:
        return _failed(p, want, [f"baseline is unreadable: {exc}"],
                       NORMALIZATION_ID)

    profile = line_ending_profile(raw)
    got = canonical_content_sha256(raw)
    structure = structure_report(raw)

    if not raw:
        problems.append("file is empty — an empty baseline is not a pass")
    if got != want:
        problems.append(
            f"canonical content sha256 {got} != expected {want} "
            f"(normalization {NORMALIZATION_ID})")
    problems.extend(structure["problems"])

    non_ascii = sum(1 for b in raw if b > 127)
    if non_ascii != EXPECTED_NON_ASCII_BYTES:
        problems.append(f"{non_ascii} non-ASCII bytes; expected "
                        f"{EXPECTED_NON_ASCII_BYTES}")
    quote_bytes = raw.count(b'"')
    if quote_bytes != EXPECTED_QUOTE_BYTES:
        problems.append(
            f"{quote_bytes} quote bytes; the line-ending-only "
            "normalization is exact only because the fixture is unquoted")

    return {
        "ok": not problems,
        "path": str(p),
        "present": True,
        "normalization_id": NORMALIZATION_ID,
        "expected_canonical_sha256": want,
        "raw_byte_sha256": raw_byte_sha256(raw),
        "canonical_content_sha256": got,
        "raw_matches_a_recorded_checkout_form": (
            raw_byte_sha256(raw) in RAW_BYTE_SHA256_BY_LINE_ENDING.values()),
        "line_endings": profile,
        "size": len(raw),
        "data_rows": structure["data_rows"],
        "lines": structure["lines"],
        "problems": problems,
    }


def _failed(p: Path, want: str, problems: list[str], norm: str) -> dict:
    return {
        "ok": False,
        "path": str(p),
        "present": False,
        "normalization_id": norm,
        "expected_canonical_sha256": want,
        "raw_byte_sha256": None,
        "canonical_content_sha256": None,
        "raw_matches_a_recorded_checkout_form": False,
        "line_endings": None,
        "size": None,
        "data_rows": None,
        "lines": None,
        "problems": problems,
    }


__all__ = [
    "BASELINE_PATH", "EXPECTED_CANONICAL_CONTENT_SHA256", "EXPECTED_DATA_ROWS",
    "EXPECTED_FIELDS", "EXPECTED_HEADER", "EXPECTED_TOTAL_LINES",
    "NORMALIZATION_DESCRIPTION", "NORMALIZATION_HASH", "NORMALIZATION_ID",
    "RAW_BYTE_SHA256_BY_LINE_ENDING", "RECORDED_RAW_PREFIX_HD_G06",
    "canonical_content_sha256", "check_baseline", "line_ending_profile",
    "normalize_line_endings", "raw_byte_sha256", "structure_report",
]
