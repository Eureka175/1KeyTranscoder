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
) -> DecodeRoute:
    """Decide the decode reader for one input.

    ``memo`` is an optional per-run dictionary of runtime-discovered
    facts (see :func:`memoize_unavailable`); it lets a reader that failed
    to initialise once stop being retried for every subsequent file
    without turning "failed once" into a permanent global claim.
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
    if len(probe) < 40:
        return R_STARTUP_FAILED, (
            f"no usable tool output (rc={rc}); the process did not start"
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
