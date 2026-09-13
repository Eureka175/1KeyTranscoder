"""Hardware-decode capability routing.

This module answers exactly one question, loudly and auditably:

    for this input, this backend and this policy, which decode reader
    must be used, and **why**?

It deliberately does **not** guess.  The proven set of (codec, chroma,
depth) triples is a closed allowlist derived from the hardware-decode
research line and extended only by measurements recorded in
``docs/hardware-decode/integration-test-matrix.md`` (categories C/J).
Anything outside it routes to software with reason code
``not_proven`` — because the failure mode this whole integration exists
to prevent is a hardware path silently producing *plausible but wrong*
output, and an untested profile is exactly where that happens.

Policy
------

``off`` (default)
    Software. Byte-for-byte the v0.6.2 behaviour; no hardware decode is
    attempted and no default changes.
``auto``
    Hardware when the input is proven eligible, software otherwise.  Any
    fallback is announced: WARNING plus a reason code in the main log,
    the per-file log and the report.
``require``
    Hardware or an error.  Never silently degrades.

Reason codes are part of the contract: a capability refusal, an
unavailable reader, a startup failure and a frame-count mismatch must be
distinguishable in a log, because on a real machine they coexist in the
same tool on adjacent files.  ``unsupported`` is not ``frame drop``.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field

# --- policy -----------------------------------------------------------------

POLICY_OFF = "off"
POLICY_AUTO = "auto"
POLICY_REQUIRE = "require"
POLICIES = (POLICY_OFF, POLICY_AUTO, POLICY_REQUIRE)

READER_HW = "avhw"
READER_SW = "avsw"

# --- reason codes -----------------------------------------------------------

R_POLICY_OFF = "policy_off"
R_PROVEN = "proven_combination"
R_NOT_PROVEN = "not_proven"
R_CAPABILITY_REFUSED = "capability_refused"
R_READER_UNAVAILABLE = "reader_unavailable"
R_DEVICE_UNAVAILABLE = "device_unavailable"
R_STARTUP_FAILED = "startup_failed"
R_DECODE_FAILED = "decode_failed"
R_COUNT_MISMATCH = "count_mismatch"
R_SEQUENCE_MISMATCH = "sequence_mismatch"
R_METADATA_MISMATCH = "metadata_mismatch"
R_INTEGRITY_OK = "integrity_ok"
R_REQUIRE_UNMET = "require_unmet"
# Measured in this session (matrix HD-D02/D-04/D-05): on a time seek the
# rigaya hardware reader delivers *different pictures* than the software
# reader, deterministically, with the same count and the same PTS
# sequence. The patch does not cause it — patched and stock `--avhw` seek
# output are byte-identical — it is pre-existing reader semantics that the
# research line never measured, because it only ever checked "seek
# unchanged vs stock", never "seek equal to --avsw".
R_SEEK_NOT_EQUIVALENT = "seek_not_equivalent"

# reason codes that mean "the hardware path was never usable here"
_HARDWARE_UNAVAILABLE = frozenset({
    R_CAPABILITY_REFUSED, R_READER_UNAVAILABLE, R_DEVICE_UNAVAILABLE,
    R_STARTUP_FAILED, R_NOT_PROVEN, R_POLICY_OFF,
})


# --- the proven allowlist ---------------------------------------------------
#
# Every entry must cite a measurement.  Adding a row without a matching
# matrix result is a policy violation, not a configuration change.
#
#   (backend, codec, chroma, depth) -> evidence
#
PROVEN: dict[tuple[str, str, str, int], str] = {
    ("nvenc", "hevc", "4:2:0", 10):
        "research: N-3 -> N at 30/330/10170 frames; byte-identical to --avsw",
    ("nvenc", "h264", "4:2:2", 10):
        "research: N-2 -> N at 195 frames; byte-identical to --avsw",
    ("qsv", "hevc", "4:2:0", 10):
        "research: N-3 -> N; per-frame SHA-256 identical to --avsw (30/30)",
}

# Combinations the hardware reader refuses outright on this machine.
# Recorded so the decision log says "refused", not "failed".
REFUSED: dict[tuple[str, str, str, int], str] = {
    ("qsv", "h264", "4:2:2", 10):
        "avqsv: codec h264(yuv422p10le) unable to decode by qsv. rc=-31, "
        "no reader constructed, no output file",
}


@dataclass
class DecodeRoute:
    """The routing decision, with everything needed to audit it."""

    reader: str                  # "avhw" | "avsw"
    hardware: bool
    reason: str
    detail: str
    policy: str
    backend: str
    codec: str
    chroma: str
    depth: int
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return dict(self.__dict__)

    def log_line(self) -> str:
        return (
            f"decode route: backend={self.backend} codec={self.codec} "
            f"{self.chroma}/{self.depth}bit policy={self.policy} -> "
            f"{'HARDWARE' if self.hardware else 'SOFTWARE'} "
            f"(reader={self.reader}, reason={self.reason}) {self.detail}"
        )


def route_decode(
    *,
    policy: str,
    backend: str,
    codec: str,
    chroma: str,
    depth: int,
    memo: dict | None = None,
    seek_requested: bool = False,
) -> DecodeRoute:
    """Decide the decode reader for one input.

    ``memo`` is an optional per-run dictionary of runtime-discovered
    facts (see :func:`memoize_unavailable`); it lets a reader that failed
    to initialise once stop being retried for every subsequent file
    without turning "failed once" into a permanent global claim.

    ``seek_requested`` must be set when the encode carries a temporal
    window (``--seek``).  Hardware decode is then refused outright: the
    two rigaya readers are not seek-equivalent, and a transcode whose
    delivered pictures depend on which reader ran is exactly the silent
    behaviour change this integration exists to prevent.
    """
    key = (backend, codec, chroma, depth)

    def _sw(reason: str, detail: str, warn: bool) -> DecodeRoute:
        r = DecodeRoute(
            reader=READER_SW, hardware=False, reason=reason, detail=detail,
            policy=policy, backend=backend, codec=codec, chroma=chroma,
            depth=depth,
        )
        if warn:
            r.warnings.append(
                f"hardware decode not used ({reason}): {detail} — "
                "falling back to software decode"
            )
        return r

    if policy not in POLICIES:
        raise ValueError(f"unknown hardware-decode policy: {policy!r}")

    if policy == POLICY_OFF:
        return _sw(R_POLICY_OFF, "policy=off is the default; software decode",
                   warn=False)

    if seek_requested:
        detail = (
            "a time seek was requested and the hardware reader is not "
            "seek-equivalent to the software reader (measured: identical "
            "count and PTS, different pictures, deterministic on both sides)"
        )
        if policy == POLICY_REQUIRE:
            return DecodeRoute(
                reader=READER_HW, hardware=False, reason=R_REQUIRE_UNMET,
                detail=f"policy=require but {detail}", policy=policy,
                backend=backend, codec=codec, chroma=chroma, depth=depth,
            )
        return _sw(R_SEEK_NOT_EQUIVALENT, detail, warn=True)

    if key in REFUSED:
        detail = REFUSED[key]
        if policy == POLICY_REQUIRE:
            return DecodeRoute(
                reader=READER_HW, hardware=False, reason=R_REQUIRE_UNMET,
                detail=f"policy=require but hardware refuses {key}: {detail}",
                policy=policy, backend=backend, codec=codec, chroma=chroma,
                depth=depth,
            )
        return _sw(R_CAPABILITY_REFUSED, detail, warn=True)

    if memo and memo.get(key):
        return _sw(R_READER_UNAVAILABLE, str(memo[key]), warn=True)

    evidence = PROVEN.get(key)
    if evidence is None:
        detail = (
            f"{codec} {chroma}/{depth}bit is not in the runtime-proven "
            f"allowlist for {backend}; not activating an unverified "
            f"hardware-decode path"
        )
        if policy == POLICY_REQUIRE:
            return DecodeRoute(
                reader=READER_HW, hardware=False, reason=R_REQUIRE_UNMET,
                detail=f"policy=require but {detail}", policy=policy,
                backend=backend, codec=codec, chroma=chroma, depth=depth,
            )
        return _sw(R_NOT_PROVEN, detail, warn=True)

    return DecodeRoute(
        reader=READER_HW, hardware=True, reason=R_PROVEN, detail=evidence,
        policy=policy, backend=backend, codec=codec, chroma=chroma,
        depth=depth,
    )


def memoize_unavailable(
    memo: dict, route: DecodeRoute, reason_class: str, detail: str
) -> None:
    """Remember a *deterministic* reason not to try hardware again.

    Only two classes qualify, because they are facts about this machine
    and this format rather than about one file:

    * ``capability_refused`` — the hardware decoder cannot handle the
      format at all (deterministic).
    * ``device_unavailable`` — there is no usable device (deterministic).

    Everything else is deliberately **not** memoized.  A transient
    initialisation or resource failure must not permanently disable the
    hardware path for the rest of a run, and an integrity failure is a
    per-file fact that says nothing about the next file.
    """
    if reason_class not in (R_CAPABILITY_REFUSED, R_DEVICE_UNAVAILABLE):
        return
    memo[(route.backend, route.codec, route.chroma, route.depth)] = detail


# --- failure classification -------------------------------------------------

def classify_reader_failure(
    log_text: str, rc: int | None = None
) -> tuple[str, str]:
    """Turn a rigaya failure log into a reason code.

    Two rules, in this order:

    1. **A process that never produced a log never started.**  A Windows
       loader failure (``STATUS_DLL_NOT_FOUND`` 0xC0000135,
       ``STATUS_DLL_INIT_FAILED`` 0xC0000142) or an empty log makes this a
       ``startup_failed``, not a decode failure — the distinction decides
       whether the fallback ladder advances or the run aborts.
    2. **An explicit capability refusal outranks generic error text**,
       because both tools print other error text around it.
    """
    text = log_text or ""
    low = text.lower()
    probe = re.sub(r"\s+", "", text)

    if rc is not None and (rc & 0xFFFFFFFF) in (
        0xC0000135,  # DLL not found
        0xC0000142,  # DLL initialisation failed
        0xC0000005,  # access violation
        0xC0000139,  # entry point not found
    ):
        return R_STARTUP_FAILED, f"loader failure rc=0x{rc & 0xFFFFFFFF:08X}"
    if not probe:
        # A real rigaya run always prints a banner (version, OS, CPU, GPU)
        # before it can fail, so a *completely* empty log means the process
        # never reached main.  The test is "empty", deliberately not "short":
        # an earlier version used a length threshold and swallowed genuine
        # one-line diagnostics such as "Invalid Device Id = 1" as startup
        # failures.
        return R_STARTUP_FAILED, (
            f"no tool output at all (rc={rc}); the process did not start"
        )

    if "unable to decode by qsv" in low or "unable to decode by" in low:
        return R_CAPABILITY_REFUSED, _first_line_with(text, "unable to decode")
    if "invalid device id" in low or "no device" in low:
        return R_DEVICE_UNAVAILABLE, _first_line_with(text, "device")
    if "failed to initialize" in low or "could not initialize" in low:
        return R_READER_UNAVAILABLE, _first_line_with(text, "initialize")
    if "no video packets found" in low:
        return R_DECODE_FAILED, _first_line_with(text, "no video packets")
    if "failed to open" in low or "could not find" in low:
        return R_DECODE_FAILED, _first_line_with(text, "open")
    if "decoder" in low and "not supported" in low:
        return R_CAPABILITY_REFUSED, _first_line_with(text, "not supported")
    return R_DECODE_FAILED, (text.strip().splitlines() or ["unknown failure"])[-1][:300]


def _first_line_with(text: str, needle: str) -> str:
    for line in text.splitlines():
        if needle.lower() in line.lower():
            return line.strip()[:300]
    return needle


def is_hardware_unavailable(reason: str) -> bool:
    return reason in _HARDWARE_UNAVAILABLE


# ---------------------------------------------------------------------------
# which binary may perform hardware decode
# ---------------------------------------------------------------------------
#
# The shipped `tools/NVEncC_9.31_x64` and `tools/QSVEncC_8.26_x64` builds
# carry the Sony head-loss defect. Asking them for `--avhw` would lose
# frames on every XAVC file — the integrity gate would then reject every
# result and the hardware path would never actually run, which is safe but
# useless. So hardware decode is pinned to the patched research builds by
# hash, and the hash is asserted before the binary is used.
#
# Those builds are **research builds and must not be distributed**
# (`release/build_release.py` does not whitelist `tools/avhw/`, and `docs/`
# is excluded from the package). The practical consequence, stated plainly:
# in a shipped installation `--hw-decode auto` degrades to software with
# reason `not_proven`, and `--hw-decode require` fails loudly. That is the
# intended behaviour, not a bug — hardware decode is a validated optional
# path in a developer checkout, never a shipped default.

PATCHED_BUILD: dict[str, dict] = {
    "nvenc": {
        "rel": "tools/avhw/NVEncC_9.31_avhw/NVEncC64.exe",
        "sha256": "dcf6d7a63143c777e54a749281bef7ee1dd50d61c44b14d128e66a1217c8be4b",
        "version_must_contain": ("9.31", "(r1)", "Sep 12 2026"),
    },
    "qsv": {
        "rel": "tools/avhw/QSVEncC_8.26_avhw/QSVEncC64.exe",
        "sha256": "f5df83f12911d99d69b07e8270d6cdef4a8e4b62adb06711599c8cee62f8804d",
        "version_must_contain": ("8.26", "(r4504)", "Sep 12 2026"),
    },
}


@dataclass
class DecodeTool:
    """The binary that may perform hardware decode, and why."""

    path: object | None          # Path when usable, else None
    role: str                    # "patched" | "explicit" | "unusable"
    reason: str
    detail: str

    @property
    def usable(self) -> bool:
        return self.path is not None


def _sha256(path) -> str | None:
    import hashlib

    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def resolve_decode_tool(
    *, kind: str, script_dir, explicit=None, policy: str = POLICY_OFF
) -> DecodeTool:
    """Pick the binary allowed to do hardware decode.

    ``kind`` is ``"nvencc"`` or ``"qsvencc"``.  An explicit operator path
    wins; otherwise the provenance-recorded patched build is used and must
    hash-match before it is trusted.
    """
    from pathlib import Path

    base = "nvenc" if kind == "nvencc" else "qsv"
    if policy == POLICY_OFF:
        return DecodeTool(None, "unusable", R_POLICY_OFF, "policy=off")

    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            p = Path(script_dir) / p
        if not p.is_file():
            return DecodeTool(None, "explicit", R_READER_UNAVAILABLE,
                              f"explicit tool path not found: {p}")
        return DecodeTool(p, "explicit", R_PROVEN,
                          "explicit tool path supplied by the operator")

    spec = PATCHED_BUILD[base]
    p = Path(script_dir) / spec["rel"]
    if not p.is_file():
        return DecodeTool(
            None, "unusable", R_NOT_PROVEN,
            f"the patched hardware-decode build is not present at "
            f"{spec['rel']}; the shipped {base} build must not be used for "
            f"hardware decode because it loses frames on Sony material",
        )
    actual = _sha256(p)
    if actual != spec["sha256"]:
        return DecodeTool(
            None, "unusable", R_NOT_PROVEN,
            f"{spec['rel']} does not match the recorded provenance "
            f"(got {actual}, expected {spec['sha256']}); refusing to use an "
            f"unverified hardware-decode build",
        )
    return DecodeTool(p, "patched", R_PROVEN,
                      f"patched build verified by sha256 {actual[:16]}")


def decode_tool_is_stock(backend: str, path) -> bool:
    """Guard: is this the shipped (defective) build?"""
    from pathlib import Path

    p = str(path).replace("\\", "/").lower()
    return "tools/avhw/" not in p and f"tools/{backend}" in p
