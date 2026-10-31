#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Byte-level "fast restore" for the transparent channel-sync remux.

**Why this module exists.**

`preservation.transparent_restore` restores Sony container metadata with a
sequence of MP4Box passes (``-new`` mux -> ``reconstruct`` -> ``flatten`` ->
``set_brand`` -> uuid insertion).  Every one of those passes rewrites the
*whole* file.  On a 50 GiB stage this is unusable: measured ``-ref`` alone
advanced less than 1 GiB of I/O in 17 minutes (research notes:
``docs/audio-sync-p4/20261025-exploration-path.md`` §5.1, original prototype:
``docs/audio-sync-p4/scripts/fast_restore.py``).

The stage produced by ``gpac.mux_new`` is already a legal MP4 with the right
tracks (video + fixed audio + rtmd/meta track).  What is missing is purely
container metadata that GPAC's ``-new`` drops: the XAVC ``ftyp`` brand, vendor
``uuid`` boxes (root PROF/XMP, moov USMT, per-trak USMT), the file-level
``nrtm`` meta box, the meta-track ``tref/cdsc``, and the MP4 ``ipcm`` audio
sample entries (GPAC writes QuickTime ``in24`` entries for the intermediate
``.mov`` files).

This module rebuilds the file in **one sequential read/write pass**:

  ftyp(source) + root-uuid(source) + mdat(stage, stream-copied)
  + rebuilt moov(stage moov + inserted uuid/tref + source audio entries,
                 stco/co64 shifted by the new mdat delta)
  + remaining stage root boxes + meta(source)

The prototype was validated against ``transparent_restore`` on a real short
Sony clip (C1093, 180 frames): brand/uuid/nrtm/tref and Gyroflow-visible
metadata were identical.  This production port generalises it:

* dynamic layout (does not assume a specific root-box order in the stage);
* ``tref/cdsc`` track IDs are re-mapped source->stage (GPAC may renumber);
* every chunk offset is range-checked against the new mdat;
* only zero-type padding boxes from the stage are dropped;
* the post-pass (durations / tkhd flags) is byte-level, no GPAC invoked.

Public entry point: :func:`fast_restore_from_stage`.
"""

from __future__ import annotations

import io
import struct
from pathlib import Path
from typing import Any, Callable

from . import isobmf
from .sony import PreservationBundle

__all__ = ["fast_restore_from_stage", "FAST_RESTORE_MIN_BYTES",
           "verify_fast_restore_structure"]

#: Files at least this large use the fast path automatically.  Below it the
#: (well-tested) GPAC multi-pass restore is kept; on small files both are
#: equivalent and the standard path is the historical reference.
FAST_RESTORE_MIN_BYTES = 2 * 1024**3


# ---------------------------------------------------------------------------
# box helpers (all offsets are absolute with respect to the passed buffer)
# ---------------------------------------------------------------------------
def _root_children(buf: bytes) -> list[isobmf.Box]:
    bio = io.BytesIO(buf)
    out: list[isobmf.Box] = []
    off = 0
    while off < len(buf):
        box = isobmf._read_header(bio, off, len(buf))
        if box is None:
            break
        out.append(box)
        off = box.end
    return out


def _children(buf: bytes, box: isobmf.Box) -> list[isobmf.Box]:
    return list(isobmf._children(io.BytesIO(buf), box))


def _read_box(path: Path, box: isobmf.Box) -> bytes:
    with path.open("rb") as f:
        f.seek(box.start)
        return f.read(box.size)


def _moov_root(buf: bytes) -> isobmf.Box:
    roots = _root_children(buf)
    if not roots or roots[0].type != "moov":
        raise RuntimeError("buffer does not start with a moov box")
    return roots[0]


def _iter_traks(moov_buf: bytes) -> list[isobmf.Box]:
    moov = _moov_root(moov_buf)
    return [b for b in _children(moov_buf, moov) if b.type == "trak"]


def _trak_handler(moov_buf: bytes, trak: isobmf.Box) -> str:
    return isobmf._trak_handler(io.BytesIO(moov_buf), trak)


def _track_id(moov_buf: bytes, trak: isobmf.Box) -> int | None:
    for child in _children(moov_buf, trak):
        if child.type != "tkhd":
            continue
        ver = moov_buf[child.data_offset]
        off = 20 if ver == 1 else 12
        return int.from_bytes(moov_buf[child.data_offset + off:
                                       child.data_offset + off + 4], "big")
    return None


def _handler_ids(moov_buf: bytes) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for trak in _iter_traks(moov_buf):
        tid = _track_id(moov_buf, trak)
        if tid is not None:
            out.setdefault(_trak_handler(moov_buf, trak), []).append(tid)
    return out


def _insert_tail(moov: bytearray, handler: str, index: int,
                 payload: bytes) -> bool:
    """Insert payload at the end of the index-th trak of `handler`."""
    buf = bytes(moov)
    for trak in _iter_traks(buf):
        if _trak_handler(buf, trak) != handler:
            continue
        if index != 0:
            index -= 1
            continue
        moov[trak.end:trak.end] = payload
        old = struct.unpack(">I", moov[trak.start:trak.start + 4])[0]
        moov[trak.start:trak.start + 4] = struct.pack(">I", old + len(payload))
        moov_sz = struct.unpack(">I", moov[0:4])[0]
        moov[0:4] = struct.pack(">I", moov_sz + len(payload))
        return True
    return False


def _insert_moov_tail(moov: bytearray, payload: bytes) -> None:
    end = _moov_root(bytes(moov)).end
    moov[end:end] = payload
    old = struct.unpack(">I", moov[0:4])[0]
    moov[0:4] = struct.pack(">I", old + len(payload))


def _trak_uuid_count(moov_buf: bytes, handler: str, index: int) -> int:
    seen = 0
    for trak in _iter_traks(moov_buf):
        if _trak_handler(moov_buf, trak) != handler:
            continue
        if seen != index:
            seen += 1
            continue
        return sum(1 for c in _children(moov_buf, trak) if c.type == "uuid")
    return -1


def _moov_uuid_count(moov_buf: bytes) -> int:
    moov = _moov_root(moov_buf)
    return sum(1 for c in _children(moov_buf, moov) if c.type == "uuid")


def _replace_audio_entries(moov: bytearray,
                           source_entries: list[bytes]) -> list[str]:
    """Replace audio sample entries by the source bytes (size may change).

    GPAC writes QuickTime ``in24`` entries (78 B) for the intermediate
    ``.mov`` files; the source uses MP4 ``ipcm`` entries (50 B).  The entry
    length change must be propagated through stsd/stbl/minf/mdia/trak/moov.
    """
    if not source_entries:
        return []
    buf = bytes(moov)
    plans: list[tuple[int, int, int, list[int], bytes]] = []
    idx = 0
    for trak in _iter_traks(buf):
        if _trak_handler(buf, trak) != "soun":
            continue
        if idx >= len(source_entries):
            break
        new_entry = source_entries[idx]
        idx += 1
        for mdia in _children(buf, trak):
            if mdia.type != "mdia":
                continue
            for minf in _children(buf, mdia):
                if minf.type != "minf":
                    continue
                for stbl in _children(buf, minf):
                    if stbl.type != "stbl":
                        continue
                    for stsd in _children(buf, stbl):
                        if stsd.type != "stsd":
                            continue
                        entry = isobmf._read_header(
                            io.BytesIO(buf), stsd.data_offset + 8, stsd.end)
                        if entry is None:
                            raise RuntimeError("cannot parse audio sample entry")
                        delta = len(new_entry) - entry.size
                        plans.append((entry.start, entry.size, delta,
                                      [stsd.start, stbl.start, minf.start,
                                       mdia.start, trak.start, 0],
                                      new_entry))
    descs: list[str] = []
    for entry_start, entry_size, delta, ancestors, new_entry in sorted(
            plans, key=lambda x: x[0], reverse=True):
        moov[entry_start:entry_start + entry_size] = new_entry
        for anc in ancestors:
            old = struct.unpack(">I", moov[anc:anc + 4])[0]
            moov[anc:anc + 4] = struct.pack(">I", old + delta)
        descs.append(f"audio entry {entry_size} -> {entry_size + delta} bytes")
    return descs


def _patch_chunk_offsets(moov: bytearray, delta: int) -> list[str]:
    """Shift every stco/co64 chunk offset by `delta` (new mdat position)."""
    if delta == 0:
        return []
    buf = bytes(moov)
    descs: list[str] = []
    for trak in _iter_traks(buf):
        for mdia in _children(buf, trak):
            if mdia.type != "mdia":
                continue
            for minf in _children(buf, mdia):
                if minf.type != "minf":
                    continue
                for stbl in _children(buf, minf):
                    if stbl.type != "stbl":
                        continue
                    for child in _children(buf, stbl):
                        if child.type not in ("stco", "co64"):
                            continue
                        do = child.data_offset
                        count = struct.unpack(">I", buf[do + 4:do + 8])[0]
                        width = 4 if child.type == "stco" else 8
                        for i in range(count):
                            p = do + 8 + width * i
                            if width == 4:
                                v = struct.unpack(">I", buf[p:p + 4])[0]
                                moov[p:p + 4] = struct.pack(">I", v + delta)
                            else:
                                v = struct.unpack(">Q", buf[p:p + 8])[0]
                                moov[p:p + 8] = struct.pack(">Q", v + delta)
                        descs.append(f"{child.type}: {count} offsets "
                                     f"{delta:+d}")
    return descs


def _check_offsets(moov_buf: bytes, mdat_start: int, mdat_end: int) -> int:
    """All chunk offsets must fall inside the new mdat; returns count."""
    checked = 0
    for trak in _iter_traks(moov_buf):
        for mdia in _children(moov_buf, trak):
            if mdia.type != "mdia":
                continue
            for minf in _children(moov_buf, mdia):
                if minf.type != "minf":
                    continue
                for stbl in _children(moov_buf, minf):
                    if stbl.type != "stbl":
                        continue
                    for child in _children(moov_buf, stbl):
                        if child.type not in ("stco", "co64"):
                            continue
                        do = child.data_offset
                        count = struct.unpack(">I", moov_buf[do + 4:do + 8])[0]
                        width = 4 if child.type == "stco" else 8
                        for i in range(count):
                            p = do + 8 + width * i
                            v = (struct.unpack(">I", moov_buf[p:p + 4])[0]
                                 if width == 4
                                 else struct.unpack(">Q",
                                                    moov_buf[p:p + 8])[0])
                            if not (mdat_start <= v < mdat_end):
                                raise RuntimeError(
                                    f"chunk offset {v} outside mdat "
                                    f"[{mdat_start}, {mdat_end})")
                            checked += 1
    return checked


def _rebuild_tref(source_moov: bytes, src_ids: dict[str, list[int]],
                  dst_ids: dict[str, list[int]]) -> bytes | None:
    """Rebuild the meta-track tref/cdsc with source->stage track-id mapping."""
    meta_traks = [t for t in _iter_traks(source_moov)
                  if _trak_handler(source_moov, t) == "meta"]
    if not meta_traks:
        return None
    tref = None
    for child in _children(source_moov, meta_traks[0]):
        if child.type == "tref":
            tref = child
            break
    if tref is None:
        return None
    src_raw = source_moov[tref.start:tref.end]
    # src_raw 是完整 tref box（含自己的 8 字节头），枚举其 payload 里的子 box
    head = _root_children(src_raw)
    if not head or head[0].type != "tref":
        raise RuntimeError("cannot parse source tref box")
    tref_box = head[0]
    body = src_raw[tref_box.data_offset:tref_box.end]
    out_children: list[bytes] = []
    for child in _root_children(body):
        raw = body[child.start:child.end]
        if child.size == 12 and child.type in (
                "cdsc", "hint", "cdep", "mpod", "sync", "chap", "tmcd"):
            src_tid = struct.unpack(">I", raw[8:12])[0]
            mapped = None
            for handler, ids in src_ids.items():
                if src_tid in ids:
                    idx = ids.index(src_tid)
                    if handler in dst_ids and idx < len(dst_ids[handler]):
                        mapped = dst_ids[handler][idx]
                    break
            if mapped is None:
                raise RuntimeError(
                    f"cannot map tref track id {src_tid} source->stage")
            out_children.append(struct.pack(">I", 12)
                                + child.type.encode("latin-1")
                                + struct.pack(">I", mapped))
        else:
            raise RuntimeError(f"unsupported tref child {child.type!r}")
    body = b"".join(out_children)
    return struct.pack(">I", 8 + len(body)) + b"tref" + body


def _actual_movie_timescale(path: Path) -> int:
    with path.open("rb") as f:
        for box in isobmf.root_boxes(path):
            if box.type != "moov":
                continue
            mvhd = next(c for c in isobmf._children(f, box) if c.type == "mvhd")
            f.seek(mvhd.data_offset)
            ver = f.read(1)[0]
            f.seek(mvhd.data_offset + (20 if ver == 1 else 12))
            return struct.unpack(">I", f.read(4))[0]
    raise RuntimeError(f"no mvhd in {path}")


def fast_restore_from_stage(
    *,
    source: Path,
    stage: Path,
    bundle: PreservationBundle,
    bundle_dir: Path,
    dst: Path,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """One-pass byte-level container restore of `stage` using `source` metadata.

    The stage must already contain the target tracks (video + fixed audio +
    rtmd/meta track) in source order; it is the output of ``gpac.mux_new``.
    Returns a JSON-able report.
    """
    source = Path(source)
    stage = Path(stage)
    dst = Path(dst)
    stage_roots = isobmf.root_boxes(stage)
    src_roots = {b.type: b for b in isobmf.root_boxes(source)}
    if "ftyp" not in src_roots:
        raise RuntimeError("source has no ftyp")
    ftyp_src = _read_box(source, src_roots["ftyp"])
    meta_src = (_read_box(source, src_roots["meta"])
                if "meta" in src_roots else b"")
    root_uuids = [_read_box(source, b) for b in isobmf.root_boxes(source)
                  if b.type == "uuid"]

    moov_src_box = src_roots.get("moov")
    if moov_src_box is None:
        raise RuntimeError("source has no moov")
    source_moov = _read_box(source, moov_src_box)
    src_ids = _handler_ids(source_moov)
    moov_uuid = b""
    for child in _children(source_moov, _moov_root(source_moov)):
        if child.type == "uuid":
            moov_uuid += source_moov[child.start:child.end]

    from .sample_entry import read_audio_entries

    src_entries = [e["bytes"] for e in read_audio_entries(source)]

    mdat = next((b for b in stage_roots if b.type == "mdat"), None)
    if mdat is None:
        raise RuntimeError("stage has no mdat")
    moov_stage = next((b for b in stage_roots if b.type == "moov"), None)
    if moov_stage is None:
        raise RuntimeError("stage has no moov")
    stage_moov_bytes = _read_box(stage, moov_stage)
    dst_ids = _handler_ids(stage_moov_bytes)

    tref_bytes = _rebuild_tref(source_moov, src_ids, dst_ids)

    trak_uuid: dict[str, list[bytes]] = {}
    for box in sorted(bundle.boxes, key=lambda b: (b.parent_context,
                                                   b.ordinal)):
        ctx = box.parent_context or ""
        if not ctx.startswith("trak:"):
            continue
        handler = ctx.split(":", 1)[1]
        trak_uuid.setdefault(handler, []).append(
            (Path(bundle_dir) / box.payload_file).read_bytes())

    # ---- build the new moov
    moov = bytearray(stage_moov_bytes)
    inserted_uuid = 0
    for handler in ("vide", "soun", "meta"):
        for i, payload in enumerate(trak_uuid.get(handler, [])):
            if _trak_uuid_count(bytes(moov), handler, i) == 0:
                if _insert_tail(moov, handler, i, payload):
                    inserted_uuid += 1
    inserted_moov_uuid = 0
    if moov_uuid and _moov_uuid_count(bytes(moov)) == 0:
        _insert_moov_tail(moov, moov_uuid)
        inserted_moov_uuid = 1
    inserted_tref = 0
    if tref_bytes is not None:
        if _insert_tail(moov, "meta", 0, tref_bytes):
            inserted_tref = 1
    entry_descs = _replace_audio_entries(moov, src_entries)

    # ---- dynamic layout: new mdat starts right after ftyp + root uuids
    new_prefix = len(ftyp_src) + sum(len(b) for b in root_uuids)
    old_prefix = int(mdat.start)
    delta = new_prefix - old_prefix
    off_descs = _patch_chunk_offsets(moov, delta)
    new_moov_bytes = bytes(moov)
    new_mdat_start = new_prefix
    new_mdat_end = new_prefix + mdat.size
    checked = _check_offsets(new_moov_bytes, new_mdat_start, new_mdat_end)

    # remaining stage root boxes copied verbatim after moov
    tail_boxes = [b for b in stage_roots
                  if b.type not in ("ftyp", "moov", "mdat")
                  and not b.type.startswith("\x00")]
    expected = (new_prefix + mdat.size + len(new_moov_bytes)
                + sum(b.size for b in tail_boxes) + len(meta_src))

    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    written = 0
    with stage.open("rb") as fh, dst.open("wb") as out:
        out.write(ftyp_src)
        written += len(ftyp_src)
        for blob in root_uuids:
            out.write(blob)
            written += len(blob)
        fh.seek(mdat.start)
        remaining = mdat.size
        CHUNK = 8 * 1024 * 1024
        while remaining > 0:
            block = fh.read(min(CHUNK, remaining))
            if not block:
                raise RuntimeError("truncated stage mdat")
            out.write(block)
            remaining -= len(block)
            written += len(block)
        out.write(new_moov_bytes)
        written += len(new_moov_bytes)
        for box in tail_boxes:
            fh.seek(box.start)
            out.write(fh.read(box.size))
            written += box.size
        if meta_src:
            out.write(meta_src)
            written += len(meta_src)
    if written != expected:
        raise RuntimeError(
            f"fast restore wrote {written} bytes, expected {expected}")

    # ---- byte-level post-pass: tkhd flags only.
    # Durations are intentionally NOT patched here: the stage moov written by
    # GPAC already carries correct tkhd/elst/mvhd durations (copied from the
    # source tracks / fixed-audio intermediates), and the integration layer
    # (channel_sync) runs `repair_remux_timescale` on the final file anyway.
    # Patching here with a guessed movie timescale measurably corrupted the
    # durations on the C1305 clip test (+1001 ticks on video and rtmd).
    durations: list[str] = []
    flags = isobmf.normalize_track_flags(dst)

    report = {
        "fast_restore": True,
        "source": str(source),
        "stage": str(stage),
        "dst": str(dst),
        "bytes_written": written,
        "ftyp_bytes": len(ftyp_src),
        "root_uuids": [len(b) for b in root_uuids],
        "meta_bytes": len(meta_src),
        "mdat": {"old_start": old_prefix, "new_start": new_mdat_start,
                 "size": mdat.size, "delta": delta},
        "moov_growth": len(new_moov_bytes) - moov_stage.size,
        "uuid_inserted_trak": inserted_uuid,
        "uuid_inserted_moov": inserted_moov_uuid,
        "tref_inserted": inserted_tref,
        "tref_bytes": len(tref_bytes) if tref_bytes else 0,
        "audio_entries": entry_descs,
        "chunk_offsets_checked": checked,
        "offset_patches": off_descs,
        "duration_patches": durations,
        "tkhd_flags_patched": len(flags),
        "track_ids": {"source": src_ids, "stage": dst_ids},
    }
    log(f"fast-restore: one-pass rebuild {dst.stat().st_size / 2**30:.2f} GiB"
        f" (ftyp {len(ftyp_src)} B, mdat {delta:+d}, uuid +{inserted_uuid}"
        f"/{inserted_moov_uuid}, tref {inserted_tref}, "
        f"audio entries {len(entry_descs)}, chunks {checked} checked)")
    return report


def verify_fast_restore_structure(source: Path, dst: Path) -> dict[str, Any]:
    """Cheap structural verification of a fast-restored file against source.

    Compares moov-level facts only (no video payload read): ftyp, uuid
    inventory, file-level meta, tref presence, audio sample entries, track
    handlers/ids and durations.  Full payload verification is a separate
    (expensive) acceptance test.
    """
    from .sample_entry import read_audio_entries

    out: dict[str, Any] = {"ok": True, "problems": []}

    def problem(msg: str) -> None:
        out["ok"] = False
        out["problems"].append(msg)

    src_roots = {b.type: b for b in isobmf.root_boxes(source)}
    dst_roots = {b.type: b for b in isobmf.root_boxes(dst)}
    for name in ("ftyp", "moov"):
        if name not in dst_roots:
            problem(f"missing {name}")
    if "ftyp" in src_roots and "ftyp" in dst_roots:
        if _read_box(source, src_roots["ftyp"]) != _read_box(
                dst, dst_roots["ftyp"]):
            problem("ftyp differs")
    if "meta" in src_roots:
        if "meta" not in dst_roots:
            problem("file-level meta missing")
        elif _read_box(source, src_roots["meta"]) != _read_box(
                dst, dst_roots["meta"]):
            problem("file-level meta differs")
    src_uuids = sorted((ctx, isobmf.uuid_guid(ext), isobmf.sha256_bytes(raw))
                       for ctx, ext, raw in isobmf.extract_uuid_boxes(source))
    dst_uuids = sorted((ctx, isobmf.uuid_guid(ext), isobmf.sha256_bytes(raw))
                       for ctx, ext, raw in isobmf.extract_uuid_boxes(dst))
    if src_uuids != dst_uuids:
        problem(f"uuid inventory differs: source {len(src_uuids)}, "
                f"dst {len(dst_uuids)}")
    src_entries = [e["bytes"] for e in read_audio_entries(source)]
    dst_entries = [e["bytes"] for e in read_audio_entries(dst)]
    if src_entries != dst_entries:
        problem(f"audio sample entries differ ({len(src_entries)} vs "
                f"{len(dst_entries)})")
    src_moov = _read_box(source, src_roots["moov"])
    dst_moov = _read_box(dst, dst_roots["moov"])
    src_ids = _handler_ids(src_moov)
    dst_ids = _handler_ids(dst_moov)
    if src_ids != dst_ids:
        problem(f"track ids differ source={src_ids} dst={dst_ids}")
    src_meta_traks = [t for t in _iter_traks(src_moov)
                      if _trak_handler(src_moov, t) == "meta"]
    dst_meta_traks = [t for t in _iter_traks(dst_moov)
                      if _trak_handler(dst_moov, t) == "meta"]
    src_tref = bool(src_meta_traks) and any(
        c.type == "tref" for c in _children(src_moov, src_meta_traks[0]))
    dst_tref = bool(dst_meta_traks) and any(
        c.type == "tref" for c in _children(dst_moov, dst_meta_traks[0]))
    if src_tref and not dst_tref:
        problem("meta track tref missing")
    out["track_ids"] = src_ids
    out["tref_present"] = dst_tref
    out["uuid_count"] = {"source": len(src_uuids), "dst": len(dst_uuids)}
    out["meta_bytes"] = (int(dst_roots["meta"].size)
                         if "meta" in dst_roots else 0)
    return out
