"""Source color metadata + rigaya token tables (single source of truth).

ffprobe reports color fields with ffmpeg spellings; the rigaya encoder
family (NVEncC / QSVEncC) advertises nearly the same spellings but with
a handful of aliases (st428, st431-2, st432-1, ebu3213-e, YCgCo, GBR,
derived-ncl, derived-cl, ictco, 2100-lms). Verified against
NVEncC 9.31 --help and QSVEncC 8.26 --help (same CLI family).

ColorInfo is produced by core.probe.build_source_info (from the one
ffprobe metadata pass, incl. HDR side data) and consumed by the
encoder backends via encoders.hw.color_flag_args(). The raw ffprobe
spellings remain on the summary dict for CSV reporting.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ColorInfo:
    """Color/HDR signalling of the source video stream.

    primaries/transfer/matrix/range use ffprobe spellings
    (e.g. "bt709", "arib-std-b67", "smpte2084", "bt2020nc", "tv").
    master_display is the rigaya --master-display string
    ("G(x,y)B(x,y)R(x,y)WP(x,y)L(max,min)", luminance in 0.0001 cd/m2);
    max_cll is "maxCLL,maxFALL" in nits for --max-cll.
    """

    primaries: str = ""
    transfer: str = ""
    matrix: str = ""
    range: str = ""
    master_display: str = ""
    max_cll: str = ""

    @property
    def is_set(self) -> bool:
        return bool(
            self.primaries or self.transfer or self.matrix
            or self.range or self.master_display or self.max_cll
        )


# ffprobe spellings that carry no signal (nothing to write).
UNSET = ("", "unknown", "N/A", None)

# ffprobe spelling -> rigaya CLI token (exact spellings from --help).
PRIMARIES_TOKENS = {
    "bt709": "bt709",
    "bt470m": "bt470m",
    "smpte170m": "smpte170m",
    "bt470bg": "bt470bg",
    "smpte240m": "smpte240m",
    "film": "film",
    "bt2020": "bt2020",
    "smpte428": "st428",
    "smpte431": "st431-2",
    "smpte432": "st432-1",
    "jedec-p22": "ebu3213-e",
}

TRANSFER_TOKENS = {
    "bt709": "bt709",
    "smpte170m": "smpte170m",
    "bt470m": "bt470m",
    "bt470bg": "bt470bg",
    "smpte240m": "smpte240m",
    "linear": "linear",
    "log100": "log100",
    "log316": "log316",
    "iec61966-2-4": "iec61966-2-4",
    "bt1361e": "bt1361e",
    "iec61966-2-1": "iec61966-2-1",
    "bt2020-10": "bt2020-10",
    "bt2020-12": "bt2020-12",
    "smpte2084": "smpte2084",
    "smpte428": "smpte428",
    "arib-std-b67": "arib-std-b67",
}

MATRIX_TOKENS = {
    "bt709": "bt709",
    "fcc": "fcc",
    "bt470bg": "bt470bg",
    "smpte170m": "smpte170m",
    "smpte240m": "smpte240m",
    "ycgco": "YCgCo",
    "rgb": "GBR",
    "gbr": "GBR",
    "bt2020nc": "bt2020nc",
    "bt2020ncl": "bt2020nc",
    "bt2020c": "bt2020c",
    "bt2020cl": "bt2020c",
    "smpte2085": "2100-lms",
    "chroma-derived-nc": "derived-ncl",
    "chroma-derived-c": "derived-cl",
    "ictcp": "ictco",
}

RANGE_TOKENS = {
    "tv": "tv",
    "pc": "pc",
    "limited": "limited",
    "full": "full",
}
