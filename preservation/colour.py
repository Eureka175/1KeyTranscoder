"""Colour-metadata fidelity for the AV1 preservation container rebuild.

Background (Stage 1.1 root cause, verified against GPAC 26.02 /
FFmpeg 9.0.1 / NVEncC 9.31 / QSVEncC 8.26):

- For a source that declares **no** colour description, the AV1
  bitstream written by every backend is already correct: its
  color_config() carries color_primaries = transfer_characteristics =
  matrix_coefficients = 2 ("unspecified").
- `color_description_present_flag` differs between backends though:
  QSVEncC sets it (values 2/2/2), FFmpeg/libsvtav1 and NVEncC leave it
  clear.
- MP4Box derives an imported AV1 track's `colr` atom from that
  description **only when the flag is set**. With the flag clear it
  does not fall back to "unspecified" — it stamps its SDR default
  `nclc 1/1/1` (bt709) on the track.
- Result: the final container claims bt709 for material that never
  declared a colour description, `preservation.compare` reports
  video.color_primaries/transfer/space MODIFIED (they are critical
  items), `structural_success` becomes False and the job fails with
  rc=1 and no output. 7 of the 137 frozen A7M5 clips are affected.

There is no encoder-side knob for this on the rigaya backends
(NVEncC `--colorprim/--transfer/--colormatrix undef` still emits
color_description_present_flag = 0; QSVEncC's `undef` happens to set it
and therefore never showed the bug) and MP4Box exposes no colour import
option (`-add …:colr=…` is rejected as a bad parameter). So the
reconciliation happens at the project's own mux boundary, in the same
narrow in-place container-patch idiom already used for uuid boxes and
the meta item_type.

Scope is deliberately minimal: the patch runs **only** for AV1 outputs
whose source video stream declares no colour description, and it only
ever rewrites the existing `colr` triple to "unspecified" (2/2/2).
Sources that do declare a description are never touched, and HEVC/H.264
outputs and the preservation checker rules are untouched.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from core.color import UNSET

from . import isobmf

_COLOUR_KEYS = ("color_primaries", "color_transfer", "color_space")


def source_video_colour(ffprobe: Path, source: Path) -> dict[str, str | None]:
    """Colour fields of the source's first video stream.

    ffprobe spellings; a field absent from the container comes back as
    None, which `core.color.UNSET` treats as "no signal". Returns {} when
    the probe fails or there is no video stream.
    """
    proc = subprocess.run(
        [
            str(ffprobe), "-v", "error",
            "-select_streams", "v:0",
            "-show_entries",
            "stream=color_primaries,color_transfer,color_space",
            "-of", "json", str(source),
        ],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace", check=False,
    )
    if proc.returncode != 0:
        return {}
    try:
        streams = json.loads(proc.stdout).get("streams") or []
    except ValueError:
        return {}
    if not streams:
        return {}
    return {key: streams[0].get(key) for key in _COLOUR_KEYS}


def reconcile_av1_colour(
    final: Path,
    source: Path,
    ffprobe: Path,
) -> str | None:
    """Make an AV1 output stop claiming a colour description the source
    never had.

    Returns a description of the container patch, or None when no
    correction is required (source declares a description, the `colr`
    already reads "unspecified", or there is no `colr` to correct).
    """
    fields = source_video_colour(ffprobe, source)
    if not fields:
        return None
    if any(value not in UNSET for value in fields.values()):
        # the source declares colour: never touch the output's signalling
        return None
    return isobmf.patch_video_colr(final, isobmf.COLOUR_UNSPECIFIED)
