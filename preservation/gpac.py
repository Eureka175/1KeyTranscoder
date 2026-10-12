"""GPAC/MP4Box container backend (subprocess wrappers).

Verified against GPAC 26.02-rev0-g118e60a9-master at C:\\Program Files\\GPAC.

Quirks discovered during bring-up (do not "fix" blindly):
- `-nhml <tkID>` writes <stem>_track<id>.nhml/.media NEXT TO THE INPUT;
  `-out` silently breaks the dump (empty sample list). We dump, then move.
- `-dts` writes <stem>_ts.txt next to the input as well.
- `-diso -std` streams the full box XML to stdout (clean parse source).
- Every run prints "[iso file] Unknown box type rtmd" on stderr; benign.
- MP4Box splits `a:b:c` option lists on ':', so a Windows drive colon
  cannot appear inside them. Paths embedded in option strings go through
  `_opt`, and every call using `_opt` pins MP4Box's own cwd with
  `_run(..., cwd=_rel_base(primary))` -- the media is frequently on a
  different volume than the process cwd (`1kt.py` is launched from the
  script directory), and `os.path.relpath` across volumes raises
  ValueError. See `_rel_base` for the measurements.
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

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from . import isobmf

DEFAULT_GPAC_DIR = Path(r"C:\Program Files\GPAC")


def _find_all(elem: ET.Element, name: str) -> list[ET.Element]:
    """Recursive tag search (same semantics as ``sony._find_all``).

    Duplicated on purpose: ``sony`` imports this module, so importing
    ``ParsedFile`` back from here would be a circular import.
    """
    return [e for e in elem.iter() if e.tag.split("}")[-1] == name]


def _find_child(elem: ET.Element, name: str) -> ET.Element | None:
    found = _find_all(elem, name)
    return found[0] if found else None


def _diso_meta_info(xml_text: str) -> dict[str, Any] | None:
    """File-level ``meta`` box facts parsed straight from ``-diso`` XML.

    Returns None when the file has no ``MetaBox`` at all. ``item_ID`` 0
    means "meta box present but no item table entry".
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    meta = _find_child(root, "MetaBox")
    if meta is None:
        return None
    hdlr = _find_child(meta, "HandlerBox")
    infe = _find_all(meta, "ItemInfoEntryBox")
    item = {"item_id": 0, "item_name": "", "item_type": ""}
    if infe:
        item = {
            "item_id": int(infe[0].get("item_ID", "0") or 0),
            "item_name": infe[0].get("item_name", ""),
            "item_type": infe[0].get("item_type", ""),
        }
    return {
        "handler_type": hdlr.get("hdlrType", "") if hdlr is not None else "",
        "handler_name": hdlr.get("Name", "") if hdlr is not None else "",
        "has_xml": _find_child(meta, "XMLBox") is not None,
        **item,
    }


def _diso_track_refs(xml_text: str) -> list[tuple[int, str, list[int]]]:
    """[(track_id, handler_type, [cdsc_targets])] from ``-diso`` XML.

    Hand-rolled rather than reusing ``sony.ParsedFile`` because ``sony``
    imports this module (circular import). Attribute names match what
    GPAC 26.02 actually emits: ``TrackHeaderBox/@TrackID``,
    ``HandlerBox/@hdlrType``, ``TrackReferenceTypeBox/@Type`` and
    ``TrackReferenceEntry/@TrackID``.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    def local(elem: ET.Element) -> str:
        return elem.tag.split("}")[-1]

    out = []
    for trak in [e for e in root.iter() if local(e) == "TrackBox"]:
        tkhd = next(
            (e for e in trak.iter() if local(e) == "TrackHeaderBox"), None
        )
        hdlr = next(
            (e for e in trak.iter() if local(e) == "HandlerBox"), None
        )
        tid = int((tkhd.get("TrackID", "0") if tkhd is not None else 0) or 0)
        handler = hdlr.get("hdlrType", "") if hdlr is not None else ""
        targets: list[int] = []
        for ref in trak.iter():
            if local(ref) != "TrackReferenceTypeBox":
                continue
            if ref.get("Type") != "cdsc":
                continue
            for entry in ref:
                if local(entry) != "TrackReferenceEntry":
                    continue
                try:
                    targets.append(int(entry.get("TrackID", "0") or 0))
                except ValueError:
                    continue
        out.append((tid, handler, targets))
    return out


class GpacError(RuntimeError):
    pass


def _run(
    cmd: list[str],
    what: str,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run MP4Box. `cwd` must agree with the base `_opt` used (see
    _rel_base): relative paths in `cmd` are resolved against it."""
    proc = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        cwd=str(cwd) if cwd else None,
    )
    if proc.returncode != 0:
        tail = (proc.stdout or "")[-2000:]
        raise GpacError(f"{what} failed (rc={proc.returncode}):\n{tail}")
    return proc


def _rel_base(primary: Path | str) -> Path:
    """Directory MP4Box should run in, chosen from the path that has to
    be expressible relatively.

    MP4Box splits `a:b:c` option lists on ':'. A Windows drive colon
    inside such a list is read as an option separator -- measured on
    GPAC 26.02: `-raw <id>:output=C:\\x.bin` and
    `-dump-item <id>:path=C:\\x.bin` both fail (the latter with
    "Bad Parameter"), while the single-value options `-new C:\\x.mp4`,
    `-add C:\\x.mp4#video` and `-set-xml C:\\x.xml` happen to survive.
    So `_opt` rewrites paths relative to MP4Box's cwd.

    That rewrite used to be relative to *our* cwd, which breaks
    whenever the media is not on the same volume as the process:
    os.path.relpath raises ValueError("path is on mount 'K:', start on
    mount 'F:'"), and `1kt.py` is normally launched from the script
    directory while the sources/output sit on another drive. Pinning
    MP4Box's cwd to the directory holding `primary` keeps the rewrite
    possible; `primary` is always the path that lands inside a colon
    option list.
    """
    p = Path(primary)
    if not p.is_absolute():
        return Path.cwd()
    parent = p.parent
    if parent.is_dir():
        return parent
    return Path(p.anchor) if p.anchor else Path.cwd()


def _opt(path: Path | str, base: Path | None = None) -> str:
    """Path for embedding inside MP4Box option strings (a:b:c syntax).

    A Windows drive colon ("F:\\...") would be parsed as an option
    separator, so absolute paths are rewritten relative to `base`
    (the cwd MP4Box itself will run in -- see _rel_base). If the path
    sits on another volume than `base` it cannot be made relative and
    is passed through absolute: MP4Box accepts that for the
    single-value options, and `base` is derived from the path that
    needs the rewrite, so this only ever applies to plain positional
    arguments and `-add` specs. Positional arguments do not need the
    rewrite at all.
    """
    s = str(path)
    if not re.match(r"^[A-Za-z]:[\\/]", s):
        return s
    base = base or Path.cwd()
    try:
        return os.path.relpath(s, str(base))
    except ValueError:
        return s


def _opt_spec(spec: str, base: Path | None = None) -> str:
    """Like _opt but for '-add' specs: relpath only the part before
    the first '#' (fragment) or ':' (option)."""
    for sep in ("#", ":"):
        i = spec.find(sep)
        if i > 1 and re.match(r"^[A-Za-z]:[\\/]", spec):
            return _opt(spec[:i], base) + spec[i:]
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
        # Thread-local: parallel workers (--jobs/--experimental-multihw)
        # share one backend object, each pipeline invocation sets its
        # own source movie timescale.
        self._local = threading.local()
        self._default_ts = movie_timescale

    @property
    def movie_timescale(self) -> int:
        return getattr(self._local, "ts", self._default_ts)

    @movie_timescale.setter
    def movie_timescale(self, value: int) -> None:
        self._local.ts = value

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

    def info(self, src: Path) -> str:
        """MP4Box -info merged output (text)."""
        proc = _run(
            [str(self.mp4box), "-info", str(src)],
            f"MP4Box -info {src.name}",
        )
        return proc.stdout or ""

    @staticmethod
    def parse_info(text: str) -> tuple[int, list[dict]]:
        """Parse MP4Box -info text: (movie_timescale, [track dicts]).

        Track enumeration that stays robust where -diso XML parsing
        fails (DJI files carry a hidden mjpeg cover track whose sample
        entry breaks ElementTree). Each track dict:
        {number, id, timescale, handler, entry, sample_count,
         media_duration_ms}.
        """
        movie_ts = 0
        m = re.search(
            r"# Movie Info - (\d+) tracks? - TimeScale (\d+)", text
        )
        if m:
            movie_ts = int(m.group(2))
        tracks: list[dict] = []
        cur: dict | None = None
        for line in text.splitlines():
            m = re.match(
                r"# Track (\d+) Info - ID (\d+) - TimeScale (\d+)", line
            )
            if m:
                cur = {
                    "number": int(m.group(1)),
                    "id": int(m.group(2)),
                    "timescale": int(m.group(3)),
                    "handler": "",
                    "entry": "",
                    "sample_count": 0,
                    "media_duration_ms": 0.0,
                }
                tracks.append(cur)
                continue
            if cur is None:
                continue
            mt = re.search(r"Media Type:\s*(\w+):(\w+)", line)
            if mt:
                cur["handler"], cur["entry"] = mt.group(1), mt.group(2)
            ms = re.search(r"Media Samples:\s*(\d+)", line)
            if ms:
                cur["sample_count"] = int(ms.group(1))
            md = re.search(
                r"Media Duration\s+(\d+):(\d+):(\d+)\.(\d+)", line
            )
            if md:
                h, mi, s, frac = (
                    int(md.group(1)), int(md.group(2)),
                    int(md.group(3)), int(md.group(4).ljust(3, "0")[:3]),
                )
                cur["media_duration_ms"] = (
                    (h * 3600 + mi * 60 + s) * 1000 + frac
                )
        return movie_ts, tracks

    # -- extraction -----------------------------------------------------

    def raw_track(self, src: Path, track_id: int, out: Path) -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        base = _rel_base(out)
        _run(
            [
                str(self.mp4box),
                f"-raw", f"{track_id}:output={_opt(out, base)}",
                str(src),
            ],
            f"MP4Box -raw {track_id}",
            cwd=base,
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
        base = _rel_base(out)
        proc = _run(
            [str(self.mp4box), "-dump-xml", _opt(out, base), str(src)],
            "MP4Box -dump-xml",
            cwd=base,
        )
        return out.is_file() and "No XML" not in (proc.stdout or "")

    def dump_meta_item(self, src: Path, item_id: int, out: Path) -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        base = _rel_base(out)
        _run(
            [
                str(self.mp4box),
                "-dump-item", f"{item_id}:path={_opt(out, base)}",
                str(src),
            ],
            f"MP4Box -dump-item {item_id}",
            cwd=base,
        )
        if not out.is_file():
            raise GpacError(f"dump-item {item_id} produced nothing")

    # -- reconstruction ---------------------------------------------------

    def mux_new(self, dst: Path, adds: list[str]) -> None:
        """MP4Box -timescale T -new dst -add A -add B ..."""
        dst.parent.mkdir(parents=True, exist_ok=True)
        base = _rel_base(dst)
        cmd = [str(self.mp4box)] + self._ts_args()
        cmd += ["-new", _opt(dst, base)]
        for a in adds:
            cmd += ["-add", _opt_spec(a, base)]
        _run(cmd, "MP4Box -new/-add", cwd=base)

    def add_track(self, mov: Path, spec: str) -> None:
        base = _rel_base(mov)
        _run(
            [str(self.mp4box)] + self._ts_args()
            + ["-add", _opt_spec(spec, base), str(mov)],
            f"MP4Box -add {spec}",
            cwd=base,
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
        base = _rel_base(file)
        spec = f"{_opt(file, base)}:name={name}:mime={mime}"
        if item_id:
            spec += f":id={item_id}"
        _run(
            [str(self.mp4box)] + self._ts_args()
            + ["-add-item", spec, str(mov)],
            f"MP4Box -add-item {file.name}",
            cwd=base,
        )

    def set_meta_xml(self, mov: Path, xml_file: Path) -> None:
        base = _rel_base(xml_file)
        _run(
            [str(self.mp4box)] + self._ts_args()
            + ["-set-xml", _opt(xml_file, base), str(mov)],
            "MP4Box -set-xml",
            cwd=base,
        )

    def meta_pass(
        self,
        mov: Path,
        meta_type: str | None = None,
        meta_items: list[tuple[Path, str, str, int]] | None = None,
        meta_xml: Path | None = None,
    ) -> None:
        """One MP4Box invocation: -set-meta + -add-item* + -set-xml.

        Verified on GPAC 26.02: the combined invocation preserves the
        movie timescale (90000 for A7M4) and applies all three
        operations in one file rewrite. NOTE: -ref must stay a SEPARATE
        invocation — the combined -ref does not apply the threaded
        -timescale, and -ref is also the pass that restores the movie
        timescale after -new (see gpac.py module docstring).
        """
        items = meta_items or []
        # The meta payloads sit inside ':name=:mime=' lists, so they are
        # the paths MP4Box's cwd must be able to express relatively.
        if items:
            base = _rel_base(items[0][0])
        elif meta_xml:
            base = _rel_base(meta_xml)
        else:
            base = _rel_base(mov)
        cmd = [str(self.mp4box)] + self._ts_args()
        if meta_type:
            cmd += ["-set-meta", meta_type]
        for file, name, mime, item_id in items:
            spec = f"{_opt(file, base)}:name={name}:mime={mime}"
            if item_id:
                spec += f":id={item_id}"
            cmd += ["-add-item", spec]
        if meta_xml:
            cmd += ["-set-xml", _opt(meta_xml, base)]
        cmd.append(str(mov))
        _run(cmd, "MP4Box combined meta pass", cwd=base)

    @staticmethod
    def _read_manifest(manifest: Path) -> dict[str, Any]:
        if not manifest.is_file():
            raise GpacError(f"bundle manifest not found: {manifest}")
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GpacError(f"{manifest}: unreadable manifest: {exc}") from exc
        if not isinstance(data, dict):
            raise GpacError(f"{manifest}: manifest is not a JSON object")
        return data

    def _manifest_nrtm(self, manifest: Path) -> dict[str, Any] | None:
        """The bundle's record of the source's nrtm meta, or None."""
        nrtm = self._read_manifest(manifest).get("nrtm")
        return nrtm if isinstance(nrtm, dict) and nrtm else None

    def _manifest_source(self, manifest: Path) -> Path:
        """The source media the bundle was extracted from.

        The manifest is authored on the machine that ran the encode, so
        the recorded path may be absolute and valid here, or point at a
        volume that no longer exists.  Both cases must fail loudly with
        the path in the message rather than silently skipping the
        restore — the metadata being restored is the whole point.
        """
        raw = self._read_manifest(manifest).get("source_path")
        if not raw:
            raise GpacError(f"{manifest}: manifest records no source_path")
        candidates = [Path(str(raw))]
        parent = manifest.parent.parent  # <work>/metadata/manifest.json
        candidates.append(parent / Path(str(raw)).name)
        for cand in candidates:
            if cand.is_file():
                return cand
        raise GpacError(
            f"{manifest}: source media recorded in the manifest is not "
            f"reachable ({raw}) — cannot restore the file-level meta box"
        )

    def restore_file_level_meta(
        self, manifest: Path, dst: Path
    ) -> bool:
        """Carry the file-level meta box (Sony ``nrtm``) from src to dst.

        ``manifest`` is the preservation bundle's ``metadata/manifest.json``
        — the bundle's *own record* of what the source carried, not a
        media file.  Taking the manifest (rather than a media path) makes
        the "this artefact has no meta to restore" case explicit and
        non-silent: a media path that happens to lack a meta box would
        otherwise be indistinguishable from a caller mistake.

        A per-track ``mux_new`` (``-new`` + ``-add``) rebuilds the movie
        from its tracks and therefore **drops every file-level box**: the
        ``uuid`` boxes are restored by ``isobmf.insert_uuid_boxes``, but
        nothing restored ``meta`` — so a container conversion silently
        produced a file that no longer carried the nrtm XML / Lens
        profile (measured: Gyroflow then reads **zero** IMU samples from
        a Sony rtmd track, even though the rtmd payload itself is
        bit-identical; the metadata track alone is not enough for the
        consumer).

        The original meta box is read back from the *source* media the
        manifest points at, and re-applied with the same single combined
        invocation as the build-time ``meta_pass`` (``-set-meta`` +
        ``-add-item`` + ``-set-xml``), because that ordering is the one
        verified to work on GPAC 26.02.

        Returns True when a meta box was found and restored, False when
        the bundle records no nrtm meta at all (nothing to do).

        Raises GpacError when the manifest *does* record an nrtm meta but
        it cannot be re-applied — that is a real loss, not a no-op.
        """
        nrtm = self._manifest_nrtm(manifest)
        if not nrtm:
            return False
        source = self._manifest_source(manifest)
        info = _diso_meta_info(self.diso_xml(source))
        if not info:
            raise GpacError(
                f"{source.name}: bundle records an nrtm meta box but the "
                f"source carries none — cannot restore it"
            )
        with tempfile.TemporaryDirectory(prefix="1kt_meta_") as tmp:
            work = Path(tmp)
            xml = work / "meta.xml"
            has_xml = self.dump_meta_xml(source, xml)
            item_id = int(info.get("item_id") or 0)
            item = work / "item.bin"
            has_item = False
            if item_id:
                try:
                    self.dump_meta_item(source, item_id, item)
                    has_item = item.is_file() and item.stat().st_size > 0
                except GpacError:
                    has_item = False
            if not has_xml and not has_item:
                raise GpacError(
                    f"{source.name}: nrtm meta box exists but yielded "
                    f"neither XML nor an item payload to restore"
                )
            items = []
            if has_item:
                items.append(
                    (item, "Lens profile", "application/octet-stream",
                     item_id)
                )
            self.meta_pass(dst, info.get("handler_type") or "nrtm",
                           items, xml if has_xml else None)
            return True

    def assert_file_level_meta(self, manifest: Path, dst: Path) -> str:
        """Fail loudly if dst lost the file-level meta box the source had.

        Reads expectations from the bundle manifest, then compares
        *content* against the source (XML text, and the dumped Lens
        profile bytes) — not box bytes: an MP4Box rewrite legitimately
        changes the meta box's internal offsets/sizes, so a box-level
        hash would produce false failures.

        Returns a human-readable description; raises GpacError on a real
        loss.  Callers must not downgrade this to a warning — the whole
        defect being guarded against here was a *silent* one.
        """
        if not manifest.is_file():
            raise GpacError(f"bundle manifest not found: {manifest}")
        nrtm = self._manifest_nrtm(manifest)
        if not nrtm:
            return "bundle records no nrtm meta box (nothing to check)"
        source = self._manifest_source(manifest)
        s_info = _diso_meta_info(self.diso_xml(source))
        o_info = _diso_meta_info(self.diso_xml(dst))
        if not s_info:
            raise GpacError(
                f"{source.name}: bundle records an nrtm meta box but the "
                f"source carries none"
            )
        if not o_info:
            raise GpacError(
                f"{dst.name}: source carries file-level meta "
                f"(hdlr={s_info.get('handler_type')!r}) but the final "
                f"container has none — consumers that need it (Gyroflow's "
                f"Sony parser) will read zero samples"
            )
        if s_info.get("handler_type") != o_info.get("handler_type"):
            raise GpacError(
                f"{dst.name}: file-level meta handler "
                f"{o_info.get('handler_type')!r} != source "
                f"{s_info.get('handler_type')!r}"
            )
        with tempfile.TemporaryDirectory(prefix="1kt_metav_") as tmp:
            work = Path(tmp)
            s_xml = work / "s.xml"
            o_xml = work / "o.xml"
            has_s_xml = self.dump_meta_xml(source, s_xml)
            has_o_xml = self.dump_meta_xml(dst, o_xml)
            if has_s_xml:
                if not has_o_xml:
                    raise GpacError(
                        f"{dst.name}: source meta carries XML but the final "
                        f"container's meta carries none"
                    )
                if s_xml.read_bytes() != o_xml.read_bytes():
                    raise GpacError(
                        f"{dst.name}: file-level meta XML differs from source"
                    )
            lens = ""
            s_item = int(s_info.get("item_id") or 0)
            o_item = int(o_info.get("item_id") or 0)
            if s_item:
                if not o_item:
                    raise GpacError(
                        f"{dst.name}: source meta has item {s_item} "
                        f"(Lens profile) but the final container has none"
                    )
                s_file = work / "s.bin"
                o_file = work / "o.bin"
                self.dump_meta_item(source, s_item, s_file)
                self.dump_meta_item(dst, o_item, o_file)
                s_sha = isobmf.sha256_file(s_file)
                o_sha = isobmf.sha256_file(o_file)
                if s_sha != o_sha:
                    raise GpacError(
                        f"{dst.name}: Lens profile item payload differs from "
                        f"source ({o_sha} != {s_sha})"
                    )
                lens = (f", lens item {s_file.stat().st_size} B "
                        f"sha256={s_sha}")
            return (
                f"file-level meta preserved "
                f"(hdlr={o_info.get('handler_type')!r}"
                f"{', xml preserved' if has_s_xml else ''}{lens})"
            )

    def restore_track_refs(self, src: Path, dst: Path) -> str:
        """Re-apply ``tref/cdsc`` (metadata track -> video) after a re-mux.

        ``mux_new`` rebuilds tracks from per-track copies and drops track
        reference boxes along with the other movie-level structure. The
        Sony rtmd track is *defined* to describe the video track via
        ``tref/cdsc``; validate.compare classifies a lost ``cdsc`` as a
        critical MISSING item, so a container conversion that drops it
        is a fidelity regression even though Gyroflow happens to cope
        without it.

        Targets are read from ``src`` and applied to ``dst`` by handler
        type (``meta`` -> first ``vide``), because the re-mux renumbers
        track IDs and the source's recorded target may be stale.

        ``-ref`` must be its own invocation: the combined form does not
        apply the threaded ``-timescale`` (see the module docstring).

        Returns a human-readable description; raises GpacError when the
        source has a cdsc but no meta/video pair could be identified.
        """
        src_refs = _diso_track_refs(self.diso_xml(src))
        if not any(targets for _, _, targets in src_refs):
            return "source has no cdsc track reference (nothing to restore)"
        dst_refs = _diso_track_refs(self.diso_xml(dst))
        meta_ids = [tid for tid, h, _ in dst_refs if h == "meta" and tid]
        video_ids = [tid for tid, h, _ in dst_refs if h == "vide" and tid]
        if not meta_ids or not video_ids:
            raise GpacError(
                f"{dst.name}: source carries a cdsc reference but the "
                f"re-muxed file has no meta/video track pair to attach it "
                f"to (meta={meta_ids}, video={video_ids})"
            )
        stats = []
        for meta_id in meta_ids:
            self.add_track_ref(dst, meta_id, "cdsc", video_ids[0])
            stats.append(f"{meta_id}->{video_ids[0]}")
        return f"tref/cdsc re-applied ({', '.join(stats)})"

    def assert_track_refs(self, src: Path, dst: Path) -> str:
        """Fail loudly if dst lost a cdsc reference src carries."""
        src_refs = _diso_track_refs(self.diso_xml(src))
        src_has = any(targets for _, _, targets in src_refs)
        if not src_has:
            return "source has no cdsc reference (nothing to check)"
        dst_refs = _diso_track_refs(self.diso_xml(dst))
        if not any(targets for _, _, targets in dst_refs):
            raise GpacError(
                f"{dst.name}: source carries a cdsc track reference "
                f"(rtmd -> video) but the final container has none"
            )
        return "tref/cdsc preserved"

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
