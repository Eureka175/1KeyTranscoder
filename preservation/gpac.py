"""GPAC/MP4Box container backend (subprocess wrappers).

Verified against GPAC 26.02-rev0-g118e60a9-master at C:\\Program Files\\GPAC.

Quirks discovered during bring-up (do not "fix" blindly):
- `-nhml <tkID>` writes <stem>_track<id>.nhml/.media NEXT TO THE INPUT;
  `-out` silently breaks the dump (empty sample list). We dump, then move.
- `-dts` writes <stem>_ts.txt next to the input as well.
- `-diso -std` streams the full box XML to stdout (clean parse source).
- Every run prints "[iso file] Unknown box type rtmd" on stderr; benign.
- Every file-rewriting command (`-new`, `-add`, `-ref`, `-set-meta`,
  `-flat`, ...) resets the movie timescale to the default 600 unless
  `-timescale` is passed on THAT command; on `-new` it must precede
  `-new` or it is silently ignored. Track-header durations are computed
  in movie timescale at that moment, so a post-hoc `-timescale` rewrite
  only rescales already-truncated values (17417 -> 1741700, not
  1741740). Hence `movie_timescale` is threaded through every mutating
  call below.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

DEFAULT_GPAC_DIR = Path(r"C:\Program Files\GPAC")


class GpacError(RuntimeError):
    pass


def _run(cmd: list[str], what: str) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        tail = (proc.stdout or "")[-2000:]
        raise GpacError(f"{what} failed (rc={proc.returncode}):\n{tail}")
    return proc


def _opt(path: Path | str) -> str:
    """Path for embedding inside MP4Box option strings (a:b:c syntax).

    A Windows drive colon ("F:\\...") would be parsed as an option
    separator, so absolute paths are rewritten relative to the cwd.
    Positional arguments do not need this.
    """
    s = str(path)
    if re.match(r"^[A-Za-z]:[\\/]", s):
        s = os.path.relpath(s, str(Path.cwd()))
    return s


def _opt_spec(spec: str) -> str:
    """Like _opt but for '-add' specs: relpath only the part before
    the first '#' (fragment) or ':' (option)."""
    for sep in ("#", ":"):
        i = spec.find(sep)
        if i > 1 and re.match(r"^[A-Za-z]:[\\/]", spec):
            return _opt(spec[:i]) + spec[i:]
    return spec


class GpacContainerBackend:
    def __init__(
        self,
        gpac_dir: Path | None = None,
        movie_timescale: int = 0,
    ) -> None:
        base = gpac_dir or DEFAULT_GPAC_DIR
        self.mp4box = base / "mp4box.exe"
        if not self.mp4box.is_file():
            self.mp4box = base / "MP4Box.exe"
        if not self.mp4box.is_file():
            raise FileNotFoundError(f"mp4box.exe not found under {base}")
        # Movie timescale to enforce while building/rewriting a file.
        # GPAC's default of 600 truncates track durations (e.g. 1741740
        # units @60000 -> 17417.4 -> 17417 ticks @600), which desyncs
        # the video track from a 60000-timescale rtmd timeline. Every
        # file-rewriting command resets the timescale to the default,
        # so it must be passed on EACH mutating call, and BEFORE -new
        # (after -new it is silently ignored).
        self.movie_timescale = movie_timescale

    def _ts_args(self) -> list[str]:
        if self.movie_timescale > 0:
            return ["-timescale", str(self.movie_timescale)]
        return []

    # -- inspection -----------------------------------------------------

    def version(self) -> str:
        proc = _run([str(self.mp4box), "-version"], "MP4Box -version")
        first = (proc.stdout or "").strip().splitlines()
        return first[0] if first else ""

    def diso_xml(self, src: Path) -> str:
        """Full box tree XML via stdout (-diso -std)."""
        proc = _run(
            [str(self.mp4box), "-diso", "-std", str(src)],
            f"MP4Box -diso {src.name}",
        )
        text = proc.stdout or ""
        # MP4Box prints warnings (e.g. "Unknown box type rtmd") before
        # the XML payload on the merged stream; skip to the first '<'
        start = text.find("<")
        if start < 0:
            raise GpacError(f"MP4Box -diso produced no XML for {src}")
        return text[start:]

    # -- extraction -----------------------------------------------------

    def raw_track(self, src: Path, track_id: int, out: Path) -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        _run(
            [
                str(self.mp4box),
                f"-raw", f"{track_id}:output={_opt(out)}",
                str(src),
            ],
            f"MP4Box -raw {track_id}",
        )
        if not out.is_file() or out.stat().st_size == 0:
            raise GpacError(f"raw dump of track {track_id} produced {out} empty/missing")

    def nhml_dump(
        self, src: Path, track_id: int, dest_dir: Path, name: str
    ) -> tuple[Path, Path]:
        """Dump track to NHML. Returns (nhml_path, media_path) in dest_dir.

        MP4Box writes <stem>_track<id>.nhml/.media beside the source;
        we move them into dest_dir and rewrite baseMediaFile to the new
        media file name.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        gen_nhml = src.with_name(f"{src.stem}_track{track_id}.nhml")
        gen_media = src.with_name(f"{src.stem}_track{track_id}.media")
        for p in (gen_nhml, gen_media):
            if p.exists():
                p.unlink()
        _run(
            [str(self.mp4box), "-nhml", str(track_id), str(src)],
            f"MP4Box -nhml {track_id}",
        )
        if not gen_nhml.is_file() or not gen_media.is_file():
            raise GpacError(
                f"NHML dump did not produce {gen_nhml.name}/"
                f"{gen_media.name}"
            )
        nhml = dest_dir / f"{name}.nhml"
        media = dest_dir / f"{name}.media"
        shutil.move(str(gen_nhml), nhml)
        shutil.move(str(gen_media), media)
        text = nhml.read_text(encoding="utf-8")
        text = re.sub(
            r'baseMediaFile="[^"]*"', f'baseMediaFile="{media.name}"', text
        )
        nhml.write_text(text, encoding="utf-8")
        return nhml, media

    def dump_meta_xml(self, src: Path, out: Path) -> bool:
        out.parent.mkdir(parents=True, exist_ok=True)
        proc = _run(
            [str(self.mp4box), "-dump-xml", _opt(out), str(src)],
            "MP4Box -dump-xml",
        )
        return out.is_file() and "No XML" not in (proc.stdout or "")

    def dump_meta_item(self, src: Path, item_id: int, out: Path) -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        _run(
            [
                str(self.mp4box),
                "-dump-item", f"{item_id}:path={_opt(out)}",
                str(src),
            ],
            f"MP4Box -dump-item {item_id}",
        )
        if not out.is_file():
            raise GpacError(f"dump-item {item_id} produced nothing")

    # -- reconstruction ---------------------------------------------------

    def mux_new(self, dst: Path, adds: list[str]) -> None:
        """MP4Box -timescale T -new dst -add A -add B ..."""
        dst.parent.mkdir(parents=True, exist_ok=True)
        cmd = [str(self.mp4box)] + self._ts_args()
        cmd += ["-new", _opt(str(dst))]
        for a in adds:
            cmd += ["-add", _opt_spec(a)]
        _run(cmd, "MP4Box -new/-add")

    def add_track(self, mov: Path, spec: str) -> None:
        _run(
            [str(self.mp4box)] + self._ts_args()
            + ["-add", _opt_spec(spec), str(mov)],
            f"MP4Box -add {spec}",
        )

    def add_track_ref(self, mov: Path, tk_id: int, ref: str, ref_id: int) -> None:
        _run(
            [str(self.mp4box)] + self._ts_args()
            + ["-ref", f"{tk_id}:{ref}:{ref_id}", str(mov)],
            f"MP4Box -ref {tk_id}:{ref}:{ref_id}",
        )

    def set_meta(self, mov: Path, meta_type: str) -> None:
        _run(
            [str(self.mp4box)] + self._ts_args()
            + ["-set-meta", meta_type, str(mov)],
            f"MP4Box -set-meta {meta_type}",
        )

    def add_meta_item(
        self,
        mov: Path,
        file: Path,
        name: str,
        mime: str,
        item_id: int = 0,
    ) -> None:
        spec = f"{_opt(file)}:name={name}:mime={mime}"
        if item_id:
            spec += f":id={item_id}"
        _run(
            [str(self.mp4box)] + self._ts_args()
            + ["-add-item", spec, str(mov)],
            f"MP4Box -add-item {file.name}",
        )

    def set_meta_xml(self, mov: Path, xml_file: Path) -> None:
        _run(
            [str(self.mp4box)] + self._ts_args()
            + ["-set-xml", _opt(xml_file), str(mov)],
            "MP4Box -set-xml",
        )

    def set_brand(
        self,
        mov: Path,
        major: str,
        compat: list[str],
        remove: list[str] | None = None,
    ) -> None:
        cmd = [str(self.mp4box)] + self._ts_args() + ["-brand", major]
        for b in compat:
            cmd += ["-ab", b]
        for b in remove or []:
            cmd += ["-rb", b]
        cmd.append(str(mov))
        _run(cmd, f"MP4Box -brand {major}")

    def flatten(self, mov: Path) -> None:
        """Re-store with all media data first (moov after mdat).

        Required before isobmf.insert_uuid_boxes(): insertions must never
        shift the mdat payload, or stco/co64 chunk offsets break.
        """
        _run(
            [str(self.mp4box)] + self._ts_args() + ["-flat", str(mov)],
            "MP4Box -flat",
        )
