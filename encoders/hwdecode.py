"""Source-aware hardware-decode routing for the rigaya encoders.

This module answers exactly two questions, loudly and auditably:

    which decode reader must this input use, **why**, and what was it
    about the *source* that decided it?

The policy is source-first, because the one measured defect is
source-specific:

* **Sony XAVC** (a data stream carrying the ``rtmd`` tag) is decoded in
  software, decided before any attempt (``sony_hardware_decode_safety``).
  Measured on this installation, the deployed NVEncC's own ``--avhw``
  reader returns rc=0, a playable file, and **27 frames of 30** on both
  XAVC HS 4:2:0 10-bit and XAVC 4:2:2 10-bit, while ``--avsw`` returns 30.
  The same class of loss exists in QSVEncC.  A Sony title therefore does
  not get hardware decode even if some short clip once happened to pass.
* **Everything else** uses the native ``--avhw`` reader of the deployed
  encoder binary.  Non-Sony material is **not** required to appear in the
  research allowlist to be attempted: the allowlist is evidence, and the
  binding decision is the integrity gate in ``core.batch_hw``, which runs
  after the encoder and can reject the result.

What this module still refuses, and must keep refusing:

* a requested time seek — the two rigaya readers are not seek-equivalent
  (same count, same PTS, different pictures);
* a format the hardware is *known* to refuse on this machine;
* a device/format failure remembered for this run.

Those are capability and semantics facts, not policy preferences.  Losing
them would not make hardware "more available", it would move a certain
refusal to runtime where it reads as a failure.

Policy
------

``off``
    Software.  No hardware decode is attempted.
``auto``
    The source-aware decision above.  Every non-hardware outcome is
    announced: WARNING plus a reason code in the main log, the per-file
    log and the report.
``require``
    Hardware or an error.  Never silently degrades.

Reason codes are part of the contract: a Sony preventive software
decision, a capability refusal, an unavailable reader, a startup failure
and a frame-count mismatch must all be distinguishable in a log, because
on a real machine they coexist in the same tool on adjacent files.
``sony_hardware_decode_safety`` is **not** ``hardware_integrity_failed``.
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

# --- source classes ---------------------------------------------------------
#
# The decode decision is made **primarily on where the material came from**,
# because the one measured defect is source-specific: the rigaya hardware
# reader drops leading pictures on Sony XAVC open-GOP timelines, silently,
# with rc=0 and a playable file. Source therefore precedes every other
# consideration except an explicitly requested time seek.
#
# ``unknown`` is a real state, not a synonym for "not Sony". It means the
# stream probe returned nothing usable, so no claim about the source can be
# made. It is routed like a non-Sony source -- no provenance signal is
# evidence about the *reader*, so withholding a measured-safe path on the
# absence of evidence would disable hardware decode for every remuxed file
# -- but the classification is recorded as unknown so the decision is
# auditable. Nothing is inferred from filename, resolution or encoder model.

SOURCE_SONY = "sony"
SOURCE_NON_SONY = "non_sony"
SOURCE_UNKNOWN = "unknown"
SOURCE_KINDS = (SOURCE_SONY, SOURCE_NON_SONY, SOURCE_UNKNOWN)

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
#: Sony material is decoded in software **by policy**, before any attempt.
#: This is a preventive decision, not a failure, and must never be logged or
#: reported as "hardware decode failed" or as a fallback.
R_SONY_SOFTWARE = "sony_hardware_decode_safety"
#: The source could not be classified. Hardware is still attempted (see
#: SOURCE_UNKNOWN above); the code exists so the log says why the source
#: class is what it is rather than implying a positive non-Sony finding.
R_SOURCE_UNKNOWN = "source_unclassified"
#: A non-Sony source passed native hardware decode and the integrity gate.
R_NATIVE_HW_ACCEPTED = "native_hw_integrity_ok"
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
    R_STARTUP_FAILED, R_NOT_PROVEN, R_POLICY_OFF, R_SONY_SOFTWARE,
})


def source_kind_from_streams(streams) -> str:
    """Classify a probed source, or :data:`SOURCE_UNKNOWN` if it cannot be.

    Sony detection is **not** re-implemented here: the authoritative model is
    ``core.batch_hw.is_sony_source`` (a data stream carrying the ``rtmd``
    codec tag), and the import is local because that module imports this one.

    Guessing is deliberately not attempted. A Sony file whose metadata has
    been stripped, or a probe that returned nothing, must not be *assumed*
    Sony (which would silently disable hardware decode for real non-Sony
    material) nor assumed non-Sony (which would claim a safety property that
    was never established). Callers that already know the answer pass it
    explicitly.
    """
    if not streams:
        return SOURCE_UNKNOWN
    try:
        from core.batch_hw import is_sony_source

        if is_sony_source(streams):
            return SOURCE_SONY
    except Exception:                              # noqa: BLE001
        return SOURCE_UNKNOWN
    return SOURCE_NON_SONY


# --- the research allowlist ------------------------------------------------
#
# This is **no longer a precondition** for hardware decode. It is the set of
# combinations the hardware-decode research line measured as byte-identical
# to software decoding, and it is kept as *evidence*: a hit is reported with
# reason `proven_combination`, a miss with `native_hw_integrity_ok`. Either
# way the integrity gate decides whether the result may be delivered.
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
# Recorded so the decision log says "refused", not "failed". This is a real
# capability fact, not a policy preference -- removing it would only move the
# refusal to runtime, where it would be misread as a failure and would cost a
# full encode attempt per file.
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
    source_kind: str = SOURCE_UNKNOWN
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return dict(self.__dict__)

    def log_line(self) -> str:
        return (
            f"decode route: backend={self.backend} codec={self.codec} "
            f"{self.chroma}/{self.depth}bit source={self.source_kind} "
            f"policy={self.policy} -> "
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
    source_kind: str = SOURCE_UNKNOWN,
) -> DecodeRoute:
    """Decide the decode reader for one input.

    The decision is **source-aware**.  ``source_kind`` (see
    :func:`source_kind_from_streams`) is the primary input:

    ``SOURCE_SONY``
        Software reader, decided **before** any attempt, with reason
        ``sony_hardware_decode_safety``.  Measured: the rigaya ``--avhw``
        reader loses leading pictures on Sony XAVC with rc=0 and a playable
        file (30-frame XAVC HS 4:2:0 10-bit -> 27 frames; 4:2:2 10-bit ->
        27; ``--avsw`` -> 30 in both).  A Sony format that once passed on a
        short clip therefore does **not** enable hardware decode by default.
        This is prevention, not a fallback: nothing is attempted and nothing
        fails.

    ``SOURCE_NON_SONY`` / ``SOURCE_UNKNOWN``
        The native hardware reader is the default.  Non-Sony material is no
        longer required to appear in the closed research allowlist: the
        deployed binary is used, and whether the result may be delivered is
        decided by the integrity gate in ``core.batch_hw`` *after* the run,
        not by a pre-emptive allowlist.  ``SOURCE_UNKNOWN`` takes the same
        path but is recorded as unknown (see :data:`SOURCE_UNKNOWN`).

    What still refuses hardware, and must keep doing so:

    * an explicitly requested time seek (``seek_requested``) -- the two
      rigaya readers are not seek-equivalent;
    * a format the hardware is **known** to refuse on this machine
      (:data:`REFUSED`) -- so the log says "refused", not "failed";
    * a *device/format* fact remembered for this run (``memo``).

    ``memo`` is an optional per-run dictionary of runtime-discovered
    facts (see :func:`memoize_unavailable`); it lets a reader that failed
    to initialise once stop being retried for every subsequent file
    without turning "failed once" into a permanent global claim.
    """
    key = (backend, codec, chroma, depth)
    if source_kind not in SOURCE_KINDS:
        raise ValueError(f"unknown source kind: {source_kind!r}")

    def _sw(reason: str, detail: str, warn: bool) -> DecodeRoute:
        r = DecodeRoute(
            reader=READER_SW, hardware=False, reason=reason, detail=detail,
            policy=policy, backend=backend, codec=codec, chroma=chroma,
            depth=depth, source_kind=source_kind,
        )
        if warn:
            r.warnings.append(
                f"hardware decode not used ({reason}): {detail} — "
                "using software decode"
            )
        return r

    def _require_unmet(detail: str) -> DecodeRoute:
        return DecodeRoute(
            reader=READER_HW, hardware=False, reason=R_REQUIRE_UNMET,
            detail=f"policy=require but {detail}", policy=policy,
            backend=backend, codec=codec, chroma=chroma, depth=depth,
            source_kind=source_kind,
        )

    def _hw(reason: str, detail: str) -> DecodeRoute:
        return DecodeRoute(
            reader=READER_HW, hardware=True, reason=reason, detail=detail,
            policy=policy, backend=backend, codec=codec, chroma=chroma,
            depth=depth, source_kind=source_kind,
        )

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
            return _require_unmet(detail)
        return _sw(R_SEEK_NOT_EQUIVALENT, detail, warn=True)

    # --- Sony: preventive software decode --------------------------------
    if source_kind == SOURCE_SONY:
        detail = (
            "Sony XAVC source (rtmd track present): the hardware reader "
            "loses leading pictures on these open-GOP timelines with rc=0 "
            "and a playable file (measured 30 -> 27 frames on 4:2:0 and "
            "4:2:2 10-bit); software decoding is selected up front"
        )
        if policy == POLICY_REQUIRE:
            return _require_unmet(detail)
        return _sw(R_SONY_SOFTWARE, detail, warn=False)

    # --- known refusals: a capability fact, not a failure -----------------
    if key in REFUSED:
        detail = REFUSED[key]
        if policy == POLICY_REQUIRE:
            return _require_unmet(f"hardware refuses {key}: {detail}")
        return _sw(R_CAPABILITY_REFUSED, detail, warn=True)

    if memo and memo.get(key):
        detail = str(memo[key])
        # `require` must not be satisfied by a remembered failure either: the
        # caller asked for hardware, and a *previous* file proved this format
        # or device cannot provide it. Returning a software reader here would
        # let require silently degrade, which is the one thing it must not do.
        if policy == POLICY_REQUIRE:
            return _require_unmet(
                f"hardware is unavailable for {key} in this run: {detail}"
            )
        return _sw(R_READER_UNAVAILABLE, detail, warn=True)

    # --- everything else: try the deployed binary's native reader ---------
    if source_kind == SOURCE_UNKNOWN:
        return _hw(
            R_SOURCE_UNKNOWN,
            f"source could not be classified (no usable stream probe), so it "
            f"is not known to carry the Sony rtmd marker; attempting the "
            f"native {backend} hardware reader and requiring the integrity "
            f"gate to accept the result",
        )

    proven = PROVEN.get(key)
    return _hw(
        R_PROVEN if proven else R_NATIVE_HW_ACCEPTED,
        proven or (
            f"{codec} {chroma}/{depth}bit is not in the research allowlist "
            f"for {backend}, but the source is not Sony, so the native "
            f"hardware reader is attempted and the integrity gate decides "
            f"whether the result may be delivered"
        ),
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
# which binary performs hardware decode
# ---------------------------------------------------------------------------
#
# **Production uses the deployed binary.**  The reason the patched research
# build used to be mandatory is Sony-specific: the shipped NVEncC/QSVEncC
# drop leading pictures on XAVC.  That defect is handled by decoding Sony in
# software *before* any attempt (`sony_hardware_decode_safety`), so the
# patched build is no longer required for -- and no longer gates -- the
# production path.  Non-Sony material runs `--avhw` on the deployed binary
# and is judged by the integrity gate.
#
# What that costs, stated plainly: the research allowlist covered Sony
# formats (XAVC HS 4:2:0 10-bit, XAVC S H.264 4:2:2 10-bit).  Those entries
# are now unreachable in production by design, because Sony is software-
# decoded.  The allowlist is retained as evidence, not as a gate.
#
# `resolve_decode_tool` below is the **experimental** escape hatch and is
# deliberately isolated: it is not called by any production path.  It keeps
# the provenance-verified research build reachable for the hardware-decode
# research line and for the historical matrix, which still needs it.
#
# Those builds are **research builds and must not be distributed**
# (`release/build_release.py` does not whitelist `tools/avhw/`, and `docs/`
# is excluded from the package).

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
    """Resolve the **experimental** patched hardware-decode build.

    NOT a production path: ``core.batch_hw`` runs hardware decode on the
    deployed binary and does not call this.  It exists so the research line
    and the historical matrix can still reach the provenance-verified build,
    and so the provenance check itself stays exercisable.

    ``kind`` is ``"nvencc"`` or ``"qsvencc"``.  An explicit operator path
    wins; otherwise the recorded research build is used and must hash-match
    before it is trusted.
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
            f"the experimental patched build is not present at "
            f"{spec['rel']}; this is the research path only -- production "
            f"hardware decode runs on the deployed binary and is gated by "
            f"source classification plus the integrity check",
        )
    actual = _sha256(p)
    if actual != spec["sha256"]:
        return DecodeTool(
            None, "unusable", R_NOT_PROVEN,
            f"{spec['rel']} does not match the recorded provenance "
            f"(got {actual}, expected {spec['sha256']}); refusing to use an "
            f"unverified build for the research path",
        )
    return DecodeTool(p, "patched", R_PROVEN,
                      f"patched build verified by sha256 {actual[:16]}")


def decode_tool_is_stock(backend: str, path) -> bool:
    """Guard: is this the shipped (defective) build?"""
    from pathlib import Path

    p = str(path).replace("\\", "/").lower()
    return "tools/avhw/" not in p and f"tools/{backend}" in p
