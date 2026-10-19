#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Container-layer restoration for the transparent channel-sync remux.

**Why this module exists as a separate file.**

`core/channel_sync.py` is an audio-sync module.  The P4 audit
(`tests/audio_sync_p4/test_production_semantics.py`, case A.1) enforces that
the audio-sync layer imports no video / encoder / metadata / mux-library
module.  Restoring Sony container metadata genuinely *is* container-layer
work, so it cannot live there without breaking that boundary — but leaving it
out is worse: GPAC's ``-new`` rewrites only the track structure it
understands, so brand / ``tref`` / ``nrtm`` meta item / vendor ``uuid`` boxes
are all lost and the output silently degrades (measured on
``20260904_C1214``: Gyroflow went from 146 146 IMU samples to **0**).

So the dependency is inverted: `core.channel_sync` exposes a
``restore_metadata`` hook on its public entry point, and the caller
(`1kt.py`, which already sits above the container layer) supplies this
implementation.  The sequence inside the mux is unchanged — the hook is
invoked at exactly the point where the old inline code ran.

Nothing here decides *whether* to restore; the audio-sync layer still owns
that predicate (a source carrying an ``rtmd`` data track).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

__all__ = ["restore_after_transparent_remux"]


def restore_after_transparent_remux(
    source: Path,
    adds: list[str],
    dst: Path,
    work_dir: Path,
    gpac: Any,
    log: Callable[[str], None],
) -> Path | None:
    """Sony 元数据保全: 把保留管线的恢复步骤复用到透明重封装上。

    为什么必须做: GPAC 的 `-new` 只重写它认识的轨道结构, 容器层的
    brand / tref / nrtm meta item / 厂商 uuid 全都会丢。实测
    `20260904_C1214` 上丢掉这四类之后, Gyroflow 从成品读到
    **0/146146** 个 IMU 样本, 相机型号也认不出来 —— 而 rtmd **轨的
    payload 逐字节相同**。也就是说丢的完全是容器层。

    步骤与 `preservation/pipeline.py` 的第 4~5c 步一致, 顺序也一样:
        mux_new(stage) -> reconstruct(tref/cdsc + nrtm) -> flatten
        -> set_brand -> insert_uuid_boxes(stage -> dst) -> item_type patch

    `flatten` 不能省: 字节级 uuid 插入要求 moov 在 mdat 之后, 而且
    它将 movie timescale 归位, 后面的 set_brand 再按源 timescale 恢复。

    返回 stage 路径 (调用方不再需要); 失败则抛异常, 由调用方回退到
    原样拷贝 —— 元数据保全失败时**不能**交出一个"看着像成品"的文件。
    """
    from preservation import isobmf
    from preservation.pipeline import _item_type_bytes
    from preservation.sony import SonyPreservationBackend

    stage = work_dir / "transparent_stage.mov"
    try:
        if stage.exists():
            stage.unlink()
    except OSError:
        pass

    sony = SonyPreservationBackend(gpac, None)
    bundle_dir = work_dir / "metadata"
    bundle = sony.extract(source, bundle_dir)
    log(f"channel-sync: Sony metadata — {len(bundle.tracks)} 元数据轨, "
        f"{len(bundle.boxes)} uuid box, nrtm="
        f"{'yes' if bundle.nrtm else 'no'}, "
        f"brand={bundle.major_brand!r}")

    gpac.mux_new(stage, adds)
    sony.reconstruct(bundle, bundle_dir, stage)
    gpac.flatten(stage)

    if bundle.major_brand:
        compat = [b for b in bundle.compatible_brands
                  if b and b != bundle.major_brand]
        major = bundle.major_brand
        if bundle.brand_minor_version:
            major += f":{bundle.brand_minor_version}"
        gpac.set_brand(
            stage, major, compat,
            remove=[b for b in ("qt  ", "isom", "iso2")
                    if b not in compat and b != bundle.major_brand],
        )

    if dst.exists():
        dst.unlink()
    existing = {
        (ctx, isobmf.sha256_bytes(raw))
        for ctx, _, raw in isobmf.extract_uuid_boxes(stage)
    }
    inserts = [
        (ctx, data)
        for ctx, data in sony.uuid_inserts(bundle, bundle_dir)
        if (ctx, isobmf.sha256_bytes(data)) not in existing
    ]
    if len(inserts) < len(bundle.boxes):
        log(f"channel-sync: uuid — GPAC 已自带 "
            f"{len(bundle.boxes) - len(inserts)} 个, 补插 {len(inserts)} 个")
    isobmf.insert_uuid_boxes(stage, dst, inserts)

    if bundle.nrtm and bundle.nrtm.item_id > 0:
        target = _item_type_bytes(bundle.nrtm.item_type)
        if target is not None:
            try:
                desc = isobmf.patch_meta_item_type(
                    dst, bundle.nrtm.item_id, target)
                if desc:
                    log(f"channel-sync: {desc}")
            except RuntimeError as exc:
                log(f"channel-sync: WARNING nrtm item_type patch 跳过: {exc}")

    try:
        stage.unlink()
    except OSError:
        pass
    return stage
