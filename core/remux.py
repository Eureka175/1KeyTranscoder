# -*- coding: utf-8 -*-
"""Lossless Sony XAVC remux mode.

The camera writes each clip into a 128 MiB media-data chunk.  When the
clip is shorter than the allocated chunk, the unused tail stays inside
``mdat`` (plus a small ``free`` box); a normal copy preserves it.  This
mode rewrites the ISOBMFF container with MP4Box's native track copy:
video, PCM audio and the Sony ``rtmd`` data track are copied bit-for-bit,
while sample tables/mdat are rebuilt without the dead padding.

Sony ``nrtm`` file-level meta, vendor uuid boxes, ``tref/cdsc`` and the
XAVC brands are restored through the existing preservation bundle, then
the result is validated against the source before it is placed.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .paths import job_id_for, safe_unlink
from preservation import isobmf
from preservation.models import PreservationBundle
from preservation.sony import SonyPreservationBackend
from preservation.validate import compare

__all__ = ["RemuxResult", "remux_one", "remux_main"]


@dataclass
class RemuxResult:
    source: Path
    output: Path
    status: str
    source_size: int = 0
    output_size: int = 0
    saved_bytes: int = 0
    detail: str = ""
    report: dict[str, Any] | None = None


def _item_type_bytes_local(text: str) -> bytes | None:
    text = (text or "").strip()
    if not text:
        return None
    if len(text) == 8:
        try:
            return bytes.fromhex(text)
        except ValueError:
            return None
    if len(text) == 4:
        return text.encode("latin-1")
    return None


def remux_one(
    *,
    source: Path,
    dst: Path,
    work_dir: Path,
    gpac: Any,
    ffprobe: Path,
    log: Callable[[str], None],
) -> RemuxResult:
    """Remux one Sony file; returns a result.  Never modifies *source*."""
    work_dir.mkdir(parents=True, exist_ok=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    bundle_dir = work_dir / "metadata"
    sony = SonyPreservationBackend(gpac, ffprobe)
    bundle = sony.extract(source, bundle_dir)

    if not bundle.tracks and not bundle.nrtm:
        raise RuntimeError(
            "not a Sony XAVC source (no rtmd track / nrtm meta); "
            "--remux only supports Sony camera files"
        )
    is_sony = (
        str(bundle.major_brand or "").upper() == "XAVC"
        or any(str(t.sample_entry_type).lower() == "rtmd"
               for t in bundle.tracks)
    )
    if not is_sony:
        raise RuntimeError(
            "not a Sony XAVC source (major brand "
            f"{bundle.major_brand!r}, no rtmd track); "
            "--remux only supports Sony camera files"
        )

    info_ts, tracks = gpac.parse_info(gpac.info(source))
    video_id = next(
        (t["id"] for t in tracks if t.get("handler") == "vide"), None
    )
    if not video_id:
        raise RuntimeError("source has no video track")

    movie_ts = bundle.movie_timescale or bundle.video_timescale or info_ts
    if movie_ts <= 0:
        raise RuntimeError("no movie timescale in source")
    gpac.movie_timescale = movie_ts

    adds = [f"{source}#{video_id}"]
    for at in bundle.audio_tracks:
        adds.append(f"{source}#{at.track_id}")
    for mt in bundle.tracks:
        adds.append(f"{source}#{mt.track_id}")

    stage = work_dir / "stage.mp4"
    final = work_dir / "final.mp4"
    for p in (stage, final):
        safe_unlink(p)

    log(f"muxing native tracks with MP4Box ({len(adds)} track source(s))")
    gpac.mux_new(stage, adds)

    log("verifying copied rtmd payload / restoring tref + nrtm")
    sony.reconstruct(bundle, bundle_dir, stage)

    log("flattening and restoring XAVC brands")
    gpac.flatten(stage)
    if bundle.major_brand:
        compat = [
            b for b in bundle.compatible_brands
            if b and b != bundle.major_brand
        ]
        major = bundle.major_brand
        if bundle.brand_minor_version:
            major += f":{bundle.brand_minor_version}"
        gpac.set_brand(
            stage, major, compat,
            remove=[
                b for b in ("qt  ", "isom", "iso2")
                if b not in compat and b != bundle.major_brand
            ],
        )

    log("re-inserting vendor uuid boxes")
    existing = {
        (ctx, isobmf.sha256_bytes(raw))
        for ctx, _, raw in isobmf.extract_uuid_boxes(stage)
    }
    inserts = [
        (ctx, data)
        for ctx, data in sony.uuid_inserts(bundle, bundle_dir)
        if (ctx, isobmf.sha256_bytes(data)) not in existing
    ]
    isobmf.insert_uuid_boxes(stage, final, inserts)

    if bundle.nrtm and bundle.nrtm.item_id > 0:
        target = _item_type_bytes_local(bundle.nrtm.item_type)
        if target:
            try:
                isobmf.patch_meta_item_type(final, bundle.nrtm.item_id, target)
            except RuntimeError as exc:
                log(f"WARNING: meta item_type patch skipped: {exc}")

    known_facts: dict[str, Any] = {}
    if bundle.tracks:
        known_facts["rtmd_sha256"] = bundle.tracks[0].samples_sha256
    if bundle.nrtm:
        known_facts.update(
            lens_sha256=bundle.nrtm.lens_profile_sha256,
            lens_size=bundle.nrtm.lens_profile_size,
            xml_sha256=bundle.nrtm.xml_sha256,
        )
    log("validating source vs remuxed file (advanced)")
    report = compare(
        source, final, gpac, ffprobe, work_dir / "validate",
        known_facts=known_facts, level="advanced", codec="hevc",
    )
    summary = report.get("summary", {})
    if not report.get("structural_success") or any(
        summary.get(k, 0) for k in ("MODIFIED", "MISSING")
    ):
        raise RuntimeError(
            f"validation failed: {json.dumps(summary, ensure_ascii=False)}"
        )

    # final placement is atomic; source is untouched
    safe_unlink(dst)
    os.replace(final, dst)
    src_size = source.stat().st_size
    out_size = dst.stat().st_size
    report["remux"] = {
        "source_bytes": src_size,
        "output_bytes": out_size,
        "saved_bytes": src_size - out_size,
        "saved_pct": round((1 - out_size / src_size) * 100, 3)
        if src_size else 0,
    }
    return RemuxResult(
        source=source, output=dst, status="done",
        source_size=src_size, output_size=out_size,
        saved_bytes=src_size - out_size, report=report,
    )


def _copy_sidecars(input_root: Path, output_root: Path, log: Callable[[str], None]) -> int:
    copied = 0
    for src in input_root.rglob("*"):
        if not src.is_file() or src.suffix.lower() != ".xml":
            continue
        rel = src.relative_to(input_root)
        dst = output_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    log(f"copied {copied} XML sidecar(s)")
    return copied


def remux_main(
    *,
    args: Any,
    input_root: Path,
    output_root: Path,
    ffprobe: Path,
    gpac: Any,
    script_dir: Path,
) -> int:
    from core.paths import discover_sources

    output_root.mkdir(parents=True, exist_ok=True)
    logs_root = output_root / "logs"
    logs_root.mkdir(parents=True, exist_ok=True)
    total_log = logs_root / "total.log"
    logger = logging.getLogger("1kt.remux")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fh = logging.FileHandler(total_log, encoding="utf-8")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(fh.formatter)
    logger.addHandler(sh)

    def log(msg: str) -> None:
        logger.info(msg)

    sources = discover_sources(input_root)
    log(f"remux mode | input={input_root} | output={output_root} | files={len(sources)}")
    sidecars = _copy_sidecars(input_root, output_root, log)
    results: list[dict[str, Any]] = []
    failed = 0
    for idx, src in enumerate(sources, 1):
        rel = src.relative_to(input_root)
        dst = output_root / rel
        if dst.is_file() and dst.stat().st_size > 0:
            log(f"[{idx}/{len(sources)}] SKIP {src.name} (exists)")
            continue
        work_dir = output_root / ".1ktwork" / job_id_for(src)
        file_log = logs_root / "files" / rel.parent / f"{rel.stem}.remux.log"
        file_log.parent.mkdir(parents=True, exist_ok=True)
        start = time.time()
        try:
            result = remux_one(
                source=src, dst=dst, work_dir=work_dir,
                gpac=gpac, ffprobe=ffprobe, log=log,
            )
        except Exception as exc:  # noqa: BLE001 - per-file failure record
            failed += 1
            detail = f"{type(exc).__name__}: {exc}"
            file_log.write_text(detail + "\n", encoding="utf-8")
            log(f"[{idx}/{len(sources)}] FAIL {src.name} | {detail}")
            results.append({
                "source": str(src), "output": str(dst),
                "status": "failed", "detail": detail,
            })
            continue
        elapsed = time.time() - start
        report_path = logs_root / "remux_reports" / f"{rel.stem}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(result.report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        file_log.write_text(
            f"status=done\nelapsed_sec={elapsed:.3f}\n"
            f"source_bytes={result.source_size}\n"
            f"output_bytes={result.output_size}\n"
            f"saved_bytes={result.saved_bytes}\n",
            encoding="utf-8",
        )
        log(
            f"[{idx}/{len(sources)}] OK {src.name} | "
            f"{result.source_size/1e6:.1f} -> "
            f"{result.output_size/1e6:.1f} MB | "
            f"saved {result.saved_bytes/1e6:.1f} MB "
            f"({(result.saved_bytes/result.source_size*100) if result.source_size else 0:.1f}%)"
        )
        results.append({
            "source": str(src), "output": str(dst), "status": "done",
            "source_bytes": result.source_size,
            "output_bytes": result.output_size,
            "saved_bytes": result.saved_bytes,
            "elapsed_sec": round(elapsed, 3),
        })
        # Scratch metadata/validation lives under .1ktwork; it has no
        # consumer after a validated success.
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except OSError as exc:
            log(f"WARNING: work cleanup incomplete {work_dir}: {exc}")

    summary = {
        "mode": "remux",
        "input": str(input_root),
        "output": str(output_root),
        "files": len(sources),
        "failed": failed,
        "xml_sidecars": sidecars,
        "results": results,
    }
    (logs_root / "remux_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    log(f"Finished | done={len(results)-failed} failed={failed}")
    logger.removeHandler(fh)
    fh.close()
    return 1 if failed else 0
