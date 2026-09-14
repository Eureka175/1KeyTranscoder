"""Corpus resolution and generated controls for the matrix.

Fixture IDs are stable names; paths are resolved against the on-disk
corpus at run time and reported as ``SKIP`` (never ``PASS``) when a
required file is absent.

Corpus layout (measured 2026-09, see ``matrix-corpus.json``):

* ``sony_hs``       Sony XAVC HS   HEVC Main10 4:2:0 3840x2160 59.94p
* ``sony_422``      Sony XAVC S    H.264 High 4:2:2 10-bit
* ``dji``           DJI Action 4   HEVC Main10 4:2:0
* ``real_a7m5``     the A7M5 shooting corpus (42 numbered clips)
* ``real_field``    adjust / validate / stress field footage
* ``long``          the frozen long-run inputs in ``work/1KT-long/inputs``

Generated controls (built once into ``work/avhw_integration/fixtures``)
exist so the matrix has cases the camera corpus cannot provide: an MKV
wrapper with no edit list, an x265 re-encode with no leading pictures,
and a synthetic in-order stream.  They are built with stream copy where
the bitstream must stay identical and re-encoded only where the point
*is* to change the bitstream.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTSETS = ROOT / "testsets"
WORK = ROOT / "work" / "avhw_integration"
FIXDIR = WORK / "fixtures"
FFMPEG = ROOT / "tools" / "ffmpeg.exe"


@dataclass(frozen=True)
class Fixture:
    fid: str
    group: str
    rel: str | None          # path relative to ROOT, or None for generated
    note: str = ""
    generated: bool = False

    @property
    def path(self) -> Path:
        return FIXDIR / self.rel if self.generated else ROOT / (self.rel or "")


# ---------------------------------------------------------------------------
# corpus
# ---------------------------------------------------------------------------

SONY_HS = Fixture(
    "sony_hs_c0886", "sony_hs",
    "testsets/a7m5_4k60p_265_10bit420_150m_xavchs_4ch/20260823_C0886.MP4",
    "Sony XAVC HS HEVC Main10 4:2:0 4K59.94p, 360 samples, leading=3",
)
SONY_422_C9037 = Fixture(
    "sony_422_c9037", "sony_422",
    "testsets/a7m4_4k30p_264_hi422p_xavcs/C9037.MP4",
    "Sony XAVC S H.264 High 4:2:2 10-bit, 195 samples, leading=2",
)
SONY_422_C9073 = Fixture(
    "sony_422_c9073", "sony_422",
    "testsets/a7m4_4k30p_264_hi422p_xavcs/C9073.MP4",
    "Sony XAVC S H.264 4:2:2 10-bit (second sample)",
)
SONY_422_C9088 = Fixture(
    "sony_422_c9088", "sony_422",
    "testsets/a7m4_4k30p_264_hi422p_xavcs/C9088.MP4",
    "Sony XAVC S H.264 4:2:2 10-bit (third sample)",
)
SONY_422_C9110 = Fixture(
    "sony_422_c9110", "sony_422",
    "testsets/a7m4_4k30p_264_hi422p_xavcs/C9110.MP4",
    "Sony XAVC S H.264 4:2:2 10-bit (fourth sample)",
)
SONY_422_C0887 = Fixture(
    "sony_422_c0887", "sony_422",
    "testsets/a7m5_4k30p_264_hi422p_xavcs/20260823_C0887.MP4",
    "Sony A7M5 XAVC S H.264 4:2:2 10-bit",
)
DJI_0009 = Fixture(
    "dji_0009", "dji",
    "testsets/action4_4k_4x3_30+60/DJI_20260830095031_0009_D.MP4",
    "DJI Action 4 HEVC Main10 4:2:0, 105 samples, leading=0",
)
DJI_0010 = Fixture(
    "dji_0010", "dji",
    "testsets/action4_4k_4x3_30+60/DJI_20260830095040_0010_D.MP4",
    "DJI Action 4 HEVC Main10 4:2:0, 297 samples, leading=0",
)

# the real A7M5 shooting corpus — enumerated at run time so the fixture
# list cannot drift from the directory
REAL_A7M5_DIR = TESTSETS / "20260904"
REAL_FIELD_DIRS = {
    "field_adjust": TESTSETS / "adjust",
    "field_validate": TESTSETS / "validate",
    "field_stress": TESTSETS / "stress",
}
LONG_DIR = ROOT / "work" / "1KT-long" / "inputs"

VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv", ".m4v")


def _video_files(d: Path) -> list[Path]:
    if not d.is_dir():
        return []
    return sorted(
        p for p in d.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES
    )


def real_a7m5() -> list[Fixture]:
    return [
        Fixture(p.stem.lower(), "real_a7m5", str(p.relative_to(ROOT)),
                "real A7M5 shooting corpus")
        for p in _video_files(REAL_A7M5_DIR)
    ]


def real_field() -> list[Fixture]:
    out: list[Fixture] = []
    for group, d in REAL_FIELD_DIRS.items():
        for p in _video_files(d):
            out.append(Fixture(
                f"{group}_{p.stem}", group, str(p.relative_to(ROOT)),
                "real field footage",
            ))
    return out


def long_inputs() -> list[Fixture]:
    return [
        Fixture(p.stem.lower().replace("-", "_"), "long",
                str(p.relative_to(ROOT)), "frozen long-run input")
        for p in _video_files(LONG_DIR)
    ]


# ---------------------------------------------------------------------------
# generated controls
# ---------------------------------------------------------------------------

GEN_A_COPY = Fixture(
    "gen_sony_copy_mp4", "control", "A_sony_copy.mp4",
    "Sony video-only stream copy in MP4 (bitstream unchanged, edit list "
    "rebuilt by the muxer)", generated=True,
)
GEN_B_MKV = Fixture(
    "gen_sony_copy_mkv", "control", "B_sony_copy.mkv",
    "same bitstream in MKV: no edit list, millisecond timebase", generated=True,
)
GEN_C_X265 = Fixture(
    "gen_x265_reencode", "control", "C_x265_reencode.mp4",
    "x265 re-encode of Sony source: leading pictures gone, closed GOP",
    generated=True,
)
GEN_D_SYNTH = Fixture(
    "gen_synthetic_testsrc2", "control", "D_synthetic_testsrc2.mp4",
    "synthetic testsrc2, in-order, no B frames, no leading pictures",
    generated=True,
)
GEN_ALL = (GEN_A_COPY, GEN_B_MKV, GEN_C_X265, GEN_D_SYNTH)


def _run(cmd, check=True) -> str:
    p = subprocess.run(
        [str(c) for c in cmd], capture_output=True
    )
    out = p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
    if check and p.returncode != 0:
        raise RuntimeError(
            f"failed ({p.returncode}): {' '.join(str(c) for c in cmd)}\n{out[-2000:]}"
        )
    return out


def build_generated(force: bool = False, src: Fixture = SONY_HS) -> dict:
    """Build the generated control fixtures (idempotent)."""
    import sys
    sys.path.insert(0, str(ROOT))
    from tests.hwdecode.probe import probe_input

    FIXDIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, dict] = {}
    s = src.path
    if not s.is_file():
        raise FileNotFoundError(f"fixture source missing: {s}")

    steps = [
        (GEN_A_COPY, [FFMPEG, "-y", "-v", "error", "-i", s, "-c", "copy",
                      "-map", "0:v:0", GEN_A_COPY.path]),
        (GEN_B_MKV, [FFMPEG, "-y", "-v", "error", "-i", s, "-c", "copy",
                     "-map", "0:v:0", GEN_B_MKV.path]),
        (GEN_C_X265, [FFMPEG, "-y", "-v", "error", "-i", s, "-map", "0:v:0",
                      "-c:v", "libx265", "-preset", "medium", "-crf", "20",
                      "-x265-params",
                      "log-level=none:keyint=60:min-keyint=60:open-gop=0",
                      "-pix_fmt", "yuv420p10le", GEN_C_X265.path]),
        (GEN_D_SYNTH, [FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                       "-i", "testsrc2=size=1920x1080:rate=30:duration=2",
                       "-c:v", "libx265", "-preset", "ultrafast", "-crf", "28",
                       "-x265-params",
                       "log-level=none:keyint=30:min-keyint=30:open-gop=0:"
                       "b-adapt=0:bframes=0",
                       "-pix_fmt", "yuv420p10le", GEN_D_SYNTH.path]),
    ]
    for fx, cmd in steps:
        if force or not fx.path.is_file():
            _run(cmd)
        f = probe_input(fx.path, input_id=fx.fid, count_packets=True)
        report[fx.fid] = f.to_json()

    (FIXDIR / "fixtures-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report


def all_fixtures() -> list[Fixture]:
    return [
        SONY_HS, SONY_422_C9037, SONY_422_C9073, SONY_422_C9088,
        SONY_422_C9110, SONY_422_C0887, DJI_0009, DJI_0010,
        *GEN_ALL,
        *real_a7m5(), *real_field(), *long_inputs(),
    ]


def by_id(fid: str) -> Fixture | None:
    for fx in all_fixtures():
        if fx.fid == fid:
            return fx
    return None
