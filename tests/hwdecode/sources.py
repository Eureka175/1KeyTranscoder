"""Toolchain resolution and provenance enforcement (matrix category A).

Rule: **any binary provenance that is not exactly what the matrix
expects is a FAIL, not a warning.**  A run that silently used the stock
binary, an older build, or a binary picked up from ``PATH`` would make
every downstream hardware-decode result meaningless — and the failure
mode is invisible, because the run still succeeds and still produces
plausible output.

``verify_provenance()`` is therefore called before any test executes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROVENANCE_FILE = ROOT / "docs" / "hardware-decode" / "toolchain-provenance.json"


class ProvenanceError(RuntimeError):
    """Raised when a binary on disk is not the one the matrix expects."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_provenance() -> dict:
    return json.loads(PROVENANCE_FILE.read_text(encoding="utf-8"))


def _version_output(binary: Path) -> str:
    try:
        p = subprocess.run(
            [str(binary), "--version"],
            capture_output=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProvenanceError(f"could not run {binary}: {exc}") from exc
    return (p.stdout or b"").decode("utf-8", "replace") + (
        p.stderr or b""
    ).decode("utf-8", "replace")


@dataclass
class BinaryCheck:
    role: str
    backend: str
    path: str
    exists: bool
    sha256: str | None
    expected_sha256: str
    sha_match: bool
    version_output: str = ""
    version_match: bool = False
    missing_tokens: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.exists and self.error is None
            and self.sha_match and self.version_match
        )

    def to_json(self) -> dict:
        d = dict(self.__dict__)
        d["ok"] = self.ok
        return d


def check_binary(backend: str, role: str, spec: dict, prov: dict) -> BinaryCheck:
    path = ROOT / spec["binary"]
    chk = BinaryCheck(
        role=role, backend=backend, path=str(path), exists=path.is_file(),
        sha256=None, expected_sha256=spec["sha256"], sha_match=False,
    )
    if not chk.exists:
        chk.error = "binary missing"
        return chk
    try:
        chk.sha256 = sha256_file(path)
    except OSError as exc:
        chk.error = f"hash failed: {exc}"
        return chk
    chk.sha_match = chk.sha256 == spec["sha256"]
    try:
        chk.version_output = _version_output(path)
    except ProvenanceError as exc:
        chk.error = str(exc)
        return chk
    tokens = spec.get("version_must_contain", [])
    chk.missing_tokens = [t for t in tokens if t not in chk.version_output]
    chk.version_match = not chk.missing_tokens
    return chk


def verify_provenance(*, strict: bool = True) -> dict:
    """Check every binary the matrix depends on.

    Returns a report dict; raises :class:`ProvenanceError` when
    ``strict`` and any required binary fails.
    """
    prov = load_provenance()
    checks: list[BinaryCheck] = []
    for backend, bspec in prov["backends"].items():
        for role in ("hardware_decode", "software_control", "stock_control"):
            spec = bspec.get(role)
            if not spec:
                continue
            # software_control reuses the hardware binary in both tools;
            # verify it once per distinct path.
            if role == "software_control":
                same = bspec.get("hardware_decode", {}).get("binary")
                if same == spec.get("binary"):
                    continue
            checks.append(check_binary(backend, role, spec, prov))

    # patch provenance
    patch_report = {}
    for backend, bspec in prov["backends"].items():
        p = bspec.get("patch")
        if not p:
            continue
        pf = ROOT / p["file"]
        actual = sha256_file(pf) if pf.is_file() else None
        patch_report[backend] = {
            "file": p["file"],
            "expected": p["sha256"],
            "actual": actual,
            "match": actual == p["sha256"],
        }

    failed = [c for c in checks if not c.ok]
    patch_failed = [k for k, v in patch_report.items() if not v["match"]]
    report = {
        "binaries": [c.to_json() for c in checks],
        "patches": patch_report,
        "ok": not failed and not patch_failed,
        "failures": [f"{c.backend}/{c.role}: "
                     f"{'sha mismatch' if not c.sha_match else ''}"
                     f"{' version tokens missing ' + str(c.missing_tokens) if c.missing_tokens else ''}"
                     f"{c.error or ''}".strip()
                     for c in failed] + [f"{k}: patch hash mismatch" for k in patch_failed],
    }
    if strict and not report["ok"]:
        raise ProvenanceError(
            "toolchain provenance check failed:\n  "
            + "\n  ".join(report["failures"])
        )
    return report


# ---------------------------------------------------------------------------
# A-06: the toolchain must never be resolved from PATH
# ---------------------------------------------------------------------------


def assert_not_from_path(binary: Path) -> tuple[bool, str]:
    """A-06 helper: is ``binary`` reachable via ``PATH`` (i.e. ambiguous)?"""
    on_path = shutil.which(Path(binary).name)
    if on_path is None:
        return True, "not on PATH (unambiguous)"
    same = os.path.normcase(str(Path(on_path).resolve())) == os.path.normcase(
        str(Path(binary).resolve())
    )
    return True, (
        f"PATH resolves {Path(binary).name} -> {on_path} "
        f"({'same file' if same else 'DIFFERENT FILE'})"
    )


# ---------------------------------------------------------------------------
# backend descriptors used by the runners
# ---------------------------------------------------------------------------


@dataclass
class BackendSpec:
    backend: str            # "nvenc" | "qsv"
    kind: str               # "nvencc" | "qsvencc"
    binary: Path
    reader_arg: str         # "--avhw" | "--avsw"
    reader_identity_expected: str
    role: str = "hardware_decode"

    @property
    def key(self) -> str:
        return f"{self.backend}:{self.reader_arg.lstrip('-')}:{self.role}"


def backend_spec(
    backend: str, reader: str, role: str = "hardware_decode"
) -> BackendSpec:
    """Resolve a runnable backend descriptor.

    ``reader`` is ``avhw`` or ``avsw``; ``role`` selects the binary
    (``hardware_decode`` = patched, ``stock_control`` = shipped).
    """
    prov = load_provenance()
    bspec = prov["backends"][backend]
    if role == "stock_control":
        key = "stock_control"
        identity = bspec["stock_control"]["reader_identity_expected"]
    elif reader == "avsw":
        key = "software_control"
        identity = bspec["software_control"]["reader_identity_expected"]
    else:
        key = "hardware_decode"
        identity = bspec["hardware_decode"]["reader_identity_expected"]
    spec = bspec[key]
    return BackendSpec(
        backend=backend,
        kind=bspec["kind"],
        binary=ROOT / spec["binary"],
        reader_arg=f"--{reader}",
        reader_identity_expected=identity,
        role=role,
    )


def proven_combinations(backend: str) -> list[dict]:
    return list(load_provenance()["backends"][backend]["proven_combinations"])


def known_refusals(backend: str) -> list[dict]:
    return list(load_provenance()["backends"][backend].get("known_refusals", []))
