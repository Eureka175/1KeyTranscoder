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

__all__ = ["restore_after_transparent_remux", "set_fast_restore_mode"]

#: Test hook: force the fast path on/off without changing the public hook
#: signature (channel_sync injects this function positionally).  None = auto
#: (fast for stages >= FAST_RESTORE_MIN_BYTES).
_FORCE_FAST: bool | None = None


def set_fast_restore_mode(mode: bool | None) -> None:
    """测试/诊断用: None=自动, True=强制快速路径, False=强制标准 GPAC 路径."""
    global _FORCE_FAST
    _FORCE_FAST = mode


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

    标准流程与 `preservation/pipeline.py` 的第 4~5c 步一致:
        mux_new(stage) -> reconstruct(tref/cdsc + nrtm) -> flatten
        -> set_brand -> insert_uuid_boxes(stage -> dst) -> item_type patch

    **大文件快速路径（2026-10-31）**: 以上每一步 GPAC 都会整文件重写,
    50 GiB stage 上实测 `-ref` 17 分钟推进不到 1 GiB。stage >=
    `fast_restore.FAST_RESTORE_MIN_BYTES`（默认 2 GiB）时改走
    `preservation.fast_restore.fast_restore_from_stage`: 一次顺序读写的
    字节级重建（ftyp/root-uuid/meta/tref/audio sample entry/co64-stco 修正,
    不调用任何 GPAC 重写）。`fast` 参数可显式强制 True/False（测试用）;
    快速路径失败会自动回退到标准流程，语义不变。

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
    stage_size = stage.stat().st_size
    from preservation.fast_restore import (
        FAST_RESTORE_MIN_BYTES,
        fast_restore_from_stage,
        verify_fast_restore_structure,
    )

    use_fast = bool(_FORCE_FAST) if _FORCE_FAST is not None else (
        stage_size >= FAST_RESTORE_MIN_BYTES)
    if use_fast:
        try:
            stats = fast_restore_from_stage(
                source=source, stage=stage, bundle=bundle,
                bundle_dir=bundle_dir, dst=dst, log=log)
            ver = verify_fast_restore_structure(source, dst)
            if not ver.get("ok"):
                raise RuntimeError(
                    "fast restore structural verification failed: "
                    + "; ".join(ver.get("problems") or []))
            log("channel-sync: fast restore metadata verified — track ids "
                f"{ver.get('track_ids')}, uuid "
                f"{ver.get('uuid_count')}, tref={ver.get('tref_present')}, "
                f"meta={ver.get('meta_bytes')} B, bytes="
                f"{stats.get('bytes_written')}")
            try:
                stage.unlink()
            except OSError:
                pass
            return stage
        except Exception as exc:                   # noqa: BLE001
            log(f"channel-sync: fast restore failed ({type(exc).__name__}: "
                f"{exc}) — falling back to standard GPAC restore")
            try:
                if dst.exists():
                    dst.unlink()
            except OSError:
                pass
            # 标准流程需要干净的 stage; mux_new 的产物仍是合法的 stage,
            # reconstruct/flatten 会就地修它。

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

    # tkhd 轨道启用标志 / alternate group 归一: GPAC -new 不保证每轨
    # flags=7 / alternate_group=0, 而 alternate_group 会被 NLE 解释为
    # "同一轨的备选版本" (只显示一条音轨)。放在文件结构全部落定之后,
    # 只改 tkhd 头, 不动 payload (见 isobmf.normalize_track_flags)。
    try:
        for desc in isobmf.normalize_track_flags(dst):
            log(f"channel-sync: {desc}")
    except (OSError, RuntimeError) as exc:
        log(f"channel-sync: WARNING tkhd normalization skipped: {exc}")

    try:
        stage.unlink()
    except OSError:
        pass
    return stage
