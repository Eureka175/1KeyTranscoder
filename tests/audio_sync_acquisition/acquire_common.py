"""P4.11 — shared pieces for the acquisition tooling.

Only what is genuinely shared lives here: the signal layout reader, the operator
note parser, and the reason-code vocabulary. The signal GENERATOR is not here — it
is `generate_signal.py`, which owns the time axis — and the acquisition ANALYSIS is
not here either, because it must call the production API rather than reimplement
any part of it.

Reason codes are a closed vocabulary. A checker that invents a new code per run
cannot be summarised or regression-tested, so unknown codes are rejected.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
FS_EXPECTED = 48_000
BITS_EXPECTED = 24
CHANNELS_EXPECTED = 4
CONTAINER_CHANNELS_EXPECTED = 1          # each of the four streams is mono
ALLOWED_EXT = {".mp4", ".mov"}

#: Every reason a take can be rejected on. Closed set on purpose.
REASON_CODES = (
    "OK",
    # structural / file level
    "TAKE_FILE_MISSING",
    "NOTE_FILE_MISSING",
    "SIGNAL_MANIFEST_MISSING",
    "CONTAINER_PROBE_FAILED",
    "AUDIO_STREAM_COUNT_WRONG",
    "SAMPLE_RATE_WRONG",
    "PCM_FORMAT_WRONG",
    "BIT_DEPTH_WRONG",
    "CHANNEL_LAYOUT_WRONG",
    "DECODE_FAILED",
    "TAKE_TOO_SHORT",
    "DURATION_MISMATCH",
    # operator identity
    "INPUT_MAPPING_MISSING",
    "INPUT_MAPPING_INCOMPLETE",
    "INPUT_MAPPING_AMBIGUOUS",
    "TAKE_KIND_MISSING",
    "TAKE_KIND_UNKNOWN",
    "OPERATOR_NOTE_UNPARSEABLE",
    "SIGNAL_SHA_MISMATCH",
    "DEVICE_FIELDS_MISSING",
    "AGC_OR_LIMITER_ENABLED",
    # signal quality
    "MARKER_MISSING",
    "MARKER_CORRUPT",
    "SIGNAL_CLIPPED",
    "DROP_OUT_DETECTED",
    "SIGNAL_ABSENT",
    # truth / eligibility
    "CALIBRATION_TAKE_MISSING",
    "CALIBRATION_BIAS_UNDETERMINED",
    "IDENTITY_UNCONFIRMED",
    "MARKER_MISMATCH_TIME_VARYING",
)

#: Data states, kept separate from execution states. A take that is
#: `DATA_INELIGIBLE` is not a software failure.
DATA_STATES = (
    "READY_FOR_ANALYSIS",
    "CONSTANT_DELAY_CANDIDATE",
    "TIME_VARYING_CANDIDATE",
    "DATA_INELIGIBLE",
    "IDENTITY_UNCONFIRMED",
)

TAKE_KINDS = ("MEASURE", "CABLE_CALIBRATION", "MARKER_ONLY")

#: Fields the operator note must provide. Physical input mapping is the one that
#: can never be inferred: four mono streams do not say which physical input feeds
#: each of them.
REQUIRED_NOTE_FIELDS = (
    "take_id", "recorded_at", "operator", "camera_body_serial",
    "input_mapping", "wireless_tx", "wireless_rx", "tx_rx_distance_m",
    "wireless_channel", "tx_gain", "rx_output_level", "receiver_output_type",
    "agc_limiter_lowcut_nr", "signal_file_sha256", "signal_description",
    "take_kind",
)

#: Fields whose absence is tolerated but recorded as a gap.
OPTIONAL_NOTE_FIELDS = ("notes",)

_FIELD_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(.*?)\s*$")


class SignalManifestError(Exception):
    """The signal manifest is missing or does not describe a usable signal."""


@dataclass
class SignalLayout:
    """The declared time axis of the generated signal, in samples."""

    path: Path
    sha256: str
    sample_rate: int
    sample_count: int
    duration_seconds: float
    marker_ms: float
    marker_start: tuple[int, int]
    marker_end: tuple[int, int]
    interior: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def marker_samples(self) -> int:
        return self.marker_start[1] - self.marker_start[0]

    def as_dict(self) -> dict[str, Any]:
        return {
            "manifest": str(self.path),
            "signal_sha256": self.sha256,
            "sample_rate": self.sample_rate,
            "sample_count": self.sample_count,
            "duration_seconds": self.duration_seconds,
            "marker_ms": self.marker_ms,
            "marker_samples": self.marker_samples,
            "marker_start": list(self.marker_start),
            "marker_end": list(self.marker_end),
            "interior_reference_events": len(self.interior),
        }


def load_signal_manifest(manifest_path: Path) -> SignalLayout:
    if not manifest_path.is_file():
        raise SignalManifestError(f"signal manifest not found: {manifest_path}")
    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    if "file" not in m or "sha256" not in m.get("file", {}):
        raise SignalManifestError("manifest does not record a signal hash")
    if "marker_layout" not in m or "marker_end_layout" not in m:
        raise SignalManifestError("manifest does not record both marker positions")
    lay = m["layout"]
    amp = m.get("amplitude", {})
    if int(amp.get("clipped_samples", 0)) != 0:
        raise SignalManifestError("the generated signal is clipped")
    return SignalLayout(
        path=manifest_path,
        sha256=str(m["file"]["sha256"]).upper(),
        sample_rate=int(m["spec"]["sample_rate"]),
        sample_count=int(amp["sample_count"]),
        duration_seconds=float(m.get("duration_seconds", 0.0)),
        marker_ms=float(m["spec"]["marker_ms"]),
        marker_start=(int(lay["marker_start"]["start_sample"]),
                      int(lay["marker_start"]["end_sample"])),
        marker_end=(int(lay["marker_end"]["start_sample"]),
                    int(lay["marker_end"]["end_sample"])),
        interior=list(lay.get("interior_reference_events", [])),
        raw=m,
    )


@dataclass
class OperatorNote:
    path: Path
    fields: dict[str, str]
    missing_required: list[str]
    unparseable_lines: list[str]
    input_mapping: dict[int, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "note": str(self.path),
            "fields": self.fields,
            "missing_required": self.missing_required,
            "unparseable_lines": self.unparseable_lines,
            "input_mapping": {str(k): v for k, v in sorted(self.input_mapping.items())},
        }


def parse_operator_note(path: Path) -> OperatorNote:
    """Parse a `field: value` note. Unknown lines are REPORTED, never dropped."""
    if not path.is_file():
        return OperatorNote(path=path, fields={},
                            missing_required=list(REQUIRED_NOTE_FIELDS),
                            unparseable_lines=[])
    fields: dict[str, str] = {}
    bad: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _FIELD_RE.match(line)
        if not m:
            bad.append(line[:120])
            continue
        fields[m.group(1).lower()] = m.group(2)
    missing = [f for f in REQUIRED_NOTE_FIELDS if f not in fields
               or not fields[f].strip()]
    mapping = parse_input_mapping(fields.get("input_mapping", ""))
    return OperatorNote(path=path, fields=fields, missing_required=missing,
                        unparseable_lines=bad, input_mapping=mapping)


def parse_input_mapping(text: str) -> dict[int, str]:
    """Parse `0=CH1_reference;1=CH2_spare;2=CH3_wireless;3=CH4_wireless`.

    Returns only the streams that were explicitly named. A partially filled map is
    reported as `INPUT_MAPPING_INCOMPLETE` rather than completed by assumption.
    """
    out: dict[int, str] = {}
    for part in re.split(r"[;,]", text or ""):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, _, v = part.partition("=")
        k, v = k.strip(), v.strip()
        if not k.isdigit():
            continue
        if v and v.lower() not in {"unknown", "?", "none", "tbd"}:
            out[int(k)] = v
    return out
