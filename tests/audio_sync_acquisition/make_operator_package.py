"""P4.11 — build the package the operator takes to the recording session.

Everything the operator needs is generated together so the signal and the notes
cannot drift apart: the signal file, its manifest, a note template pre-filled with
the fixed-format fields, and the take checklist.

The fields that describe PHYSICAL FACTS are deliberately left blank with an
explicit marker. A template that pre-filled "input_mapping" would invite the
operator to accept a mapping nobody checked, and four mono streams do not say which
physical input feeds each of them.

Usage:
    python tests/audio_sync_acquisition/make_operator_package.py --out DIR
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))

from generate_signal import (                               # noqa: E402
    DEFAULT_SEED, SignalSpec, generate,
)
from acquire_common import TAKE_KINDS                        # noqa: E402

WORK = REPO / "work" / "p4.11-acquisition"

#: Fields the operator MUST fill. Left blank on purpose, and the placeholder says
#: why rather than looking like an oversight.
BLANK = "___FILL_IN___"

CHECKLIST = """# Take checklist — read before every take

## Before the session

- [ ] Battery charged on the camera AND on the wireless TX and RX.
- [ ] The signal file is the one from this package; its SHA-256 is written on the
      note template and matches `signal-manifest.json`.
- [ ] The camera is set to **4 independent mono PCM tracks at 48 kHz, 24-bit**.
- [ ] **Auto-gain, limiter, low-cut and noise reduction are OFF on all four
      inputs.** Any of them is a time-varying nonlinearity inside the path being
      measured. Write the actual setting on the note.
- [ ] Recorder gain set so the loudest part peaks between **-12 and -6 dBFS**.
- [ ] You have decided and WRITTEN DOWN which physical input feeds each stream.
      Do not decide this afterwards from the file.

## Wiring

- [ ] Signal source output 1 -> direct cable -> the reference input.
- [ ] Signal source output 2 -> wireless TX -> wireless RX -> the wireless input.
- [ ] The reference and the wireless paths are on **different** physical inputs.
- [ ] Note the receiver output type (XLR line / 3.5 mm / other).

## Every take

- [ ] Say the take_id out loud before recording (the microphone is live).
- [ ] Start the player, then start the camera; keep the player running past the
      end marker.
- [ ] Record the whole 60 s file with no interruption.
- [ ] Stop the camera.
- [ ] Fill in the note template: take_id, timestamp, distance, channel, gains,
      and anything unusual (RF warning, dropout, battery swap).
- [ ] Do NOT trim, rename or re-encode the file. The analysis reads it as recorded.

## Session shape (P4.10 §6)

- [ ] 1 x `CABLE_CALIBRATION`: replace the wireless receiver with a SECOND direct
      cable from the same source. This take must measure 0.
- [ ] 5 x `MEASURE` at long TX-RX separation.
- [ ] 5 x `MEASURE` at medium separation.
- [ ] 5 x `MEASURE` at short separation.
- [ ] Total: 16 takes, about 15 minutes of recording.

## What NOT to do

- [ ] Do not "fix" a take that looks wrong. A take with a dropout or with markers
      that disagree is still evidence; write down what happened and keep it.
- [ ] Do not enable any processing to make the recording sound better.
- [ ] Do not record only one take per configuration "to save time" — the repeats
      are what show whether the link is reproducible.
"""


def note_template(signal_sha: str, take_kind: str, take_id: str) -> str:
    return f"""# Operator note — one per take. Save as <take_id>.txt next to <take_id>.MP4.
#
# Lines are `field: value`. The placeholder {BLANK} means YOU must decide it.
# This template refuses to guess your wiring, because four mono streams do not
# say which physical input feeds each of them.

take_id: {take_id}
recorded_at: {BLANK}            # ISO 8601 WITH timezone, e.g. 2026-10-12T14:03:00+08:00
operator: {BLANK}
camera_body_serial: {BLANK}

# Which PHYSICAL input feeds each of the four streams, in stream order 0..3.
# Examples of an ACCEPTABLE value: CH1_direct_cable_from_player_lineout
# Examples of an UNACCEPTABLE value: CH1  /  unknown  /  CH1_or_CH2
# All four streams must be named, or the analysis refuses to run.
input_mapping: 0={BLANK};1={BLANK};2={BLANK};3={BLANK}

wireless_tx: {BLANK}            # model and serial
wireless_rx: {BLANK}            # model and serial
tx_rx_distance_m: {BLANK}
wireless_channel: {BLANK}       # channel / band as set on the device
tx_gain: {BLANK}
rx_output_level: {BLANK}
receiver_output_type: {BLANK}   # XLR line / 3.5 mm / other

# Must state that every input had these OFF. Anything other than "off" is
# rejected, because a limiter or AGC inside the measured path invalidates it.
agc_limiter_lowcut_nr: {BLANK}  # e.g. "OFF on all four inputs"

signal_file_sha256: {signal_sha}
signal_description: p4.11 band-limited noise 200-8000 Hz + log sweeps, 60 s
take_kind: {take_kind}          # one of: {", ".join(TAKE_KINDS)}
notes: {BLANK}
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=WORK / "operator-package")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = ap.parse_args(argv)

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    spec = SignalSpec(seed=args.seed)
    manifest = generate(out, spec)

    (out / "take-checklist.md").write_text(CHECKLIST, encoding="utf-8")
    (out / "operator-note-template-CABLE_CALIBRATION.txt").write_text(
        note_template(manifest["file"]["sha256"], "CABLE_CALIBRATION",
                      "cal-01"), encoding="utf-8")
    (out / "operator-note-template-MEASURE.txt").write_text(
        note_template(manifest["file"]["sha256"], "MEASURE",
                      "meas-<distance>-<n>"), encoding="utf-8")

    sig_txt = manifest["file"]["sha256"]
    (out / "README.md").write_text(f"""# Recording package

Take these files to the session. The procedure is
`docs/audio-sync-p4/p4.10-acquisition-protocol.md` and the checklist is
`take-checklist.md`.

| file | what it is |
|---|---|
| `signal.wav` | play this. Mono, 48 kHz, 24-bit, {manifest['duration_seconds']:.1f} s. |
| `signal-manifest.json` | every section boundary in samples, and the file hash |
| `operator-note-template-CABLE_CALIBRATION.txt` | for the calibration take |
| `operator-note-template-MEASURE.txt` | for the measurement takes |
| `take-checklist.md` | read before every take |

```text
signal SHA-256 : {sig_txt}
sample count   : {manifest['amplitude']['sample_count']}
peak           : {manifest['amplitude']['peak_abs']:.4f} \
({manifest['amplitude']['peak_dbfs']:.2f} dBFS), clipped samples: \
{manifest['amplitude']['clipped_samples']}
marker start   : samples {manifest['marker_layout']['start_sample']}-\
{manifest['marker_layout']['end_sample']}
marker end     : samples {manifest['marker_end_layout']['start_sample']}-\
{manifest['marker_end_layout']['end_sample']}
interior refs  : {len(manifest['layout']['interior_reference_events'])}
```

## Deliver back

```text
takes/<session>/<take_id>.MP4      as recorded: not trimmed, not re-encoded
takes/<session>/<take_id>.txt      the filled-in note
```

Then verify locally before sending anything on:

```powershell
python tests/audio_sync_acquisition/preflight.py --input takes\\
```

`preflight.py` reports, per take, whether it is usable and what kind of data it is.
A take it rejects is still worth keeping — send it anyway and say so; a rejected
take with a stated reason is evidence, and a silently dropped one is not.
""", encoding="utf-8")

    print(f"package     : {out}")
    for p in sorted(out.iterdir()):
        print(f"  {p.name:46s} {p.stat().st_size:>10d} B")
    print(f"signal SHA  : {sig_txt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
