#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L2 工具链: 真实工具版本 / 能力探测 / 旗标白名单 (不编码)。"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..paths import ROOT
from ..paths import WORK
from ..paths import record
from ..paths import section
from ..paths import sh

def l2_toolchain() -> None:
    from core.config import find_executable, find_hw_tool
    from encoders.caps import probe_backend
    from encoders.hw import known_flags
    from preservation.gpac import GpacContainerBackend
    from preservation.gyroflow import find_gyroflow
    section("L2 工具链")
    ffprobe = find_executable("ffprobe", ROOT)
    ffmpeg = find_executable("ffmpeg", ROOT)
    r = sh(ffprobe, "-version")
    record("tool.ffprobe 可用", r.returncode == 0
           and "ffprobe version" in (r.stdout or "").lower())
    r = sh(ffmpeg, "-version")
    record("tool.ffmpeg 可用", r.returncode == 0
           and "ffmpeg version" in (r.stdout or "").lower())
    # 必须使用项目自带 tools/ffmpeg.exe (PATH 老版本不支持 AV1 新特性)
    record("tool.ffmpeg 项目自带 (tools/)",
           ROOT.resolve() in Path(ffmpeg).resolve().parents,
           f"ffmpeg={ffmpeg}")
    r = sh(ffmpeg, "-hide_banner", "-encoders")
    enc_text = (r.stdout or "") + (r.stderr or "")
    record("tool.ffmpeg libsvtav1 编码器", "libsvtav1" in enc_text)
    record("tool.ffmpeg libvmaf 滤波器", "libvmaf" in
           ((sh(ffmpeg, "-hide_banner", "-filters").stdout or "")
            + (sh(ffmpeg, "-hide_banner", "-filters").stderr or "")))
    nvenc = find_hw_tool(ROOT, "NVEncC64.exe")
    qsv = find_hw_tool(ROOT, "QSVEncC64.exe")
    r = sh(nvenc, "--version")
    record("tool.NVEncC 版本", r.returncode == 0
           and "NVEncC" in (r.stdout or ""), (r.stdout or "")[:80].strip())
    r = sh(qsv, "--version")
    record("tool.QSVEncC 版本", r.returncode == 0
           and "QSVEncC" in (r.stdout or ""), (r.stdout or "")[:80].strip())

    caps_n = probe_backend(nvenc, "nvencc")
    record("caps.nvenc 实机探测",
           caps_n is not None and caps_n.codecs.get("hevc") is not None,
           f"device={getattr(caps_n, 'device', '?')}")
    if caps_n and caps_n.codecs.get("hevc"):
        hevc = caps_n.codecs["hevc"]
        record("caps.nvenc 实机 10bit/422",
               hevc.bit10 and hevc.csp_422 and hevc.bit10_422, f"{hevc}")
    caps_q = probe_backend(qsv, "qsvencc")
    record("caps.qsv 实机探测",
           caps_q is not None and caps_q.codecs.get("hevc") is not None,
           f"device={getattr(caps_q, 'device', '?')}")
    if caps_q and caps_q.codecs.get("hevc"):
        record("caps.qsv 实机 10bit", caps_q.codecs["hevc"].bit10)

    kn = known_flags(nvenc)
    need = {"colorprim", "transfer", "colormatrix", "colorrange",
            "master-display", "max-cll", "atc-sei", "avsw"}
    record("flags.nvenc 色彩/软解旗标", need <= kn,
           f"missing={sorted(need - kn)}")
    kq = known_flags(qsv)
    record("flags.qsv 色彩/质量旗标",
           need <= kq and "quality" in kq,
           f"missing={sorted(need - kq)}")

    gpac = GpacContainerBackend()
    record("tool.GPAC 版本", "GPAC" in gpac.version())
    gyro = find_gyroflow(None)
    record("tool.Gyroflow 探测", gyro is not None, f"gyro={gyro}")

    dji_src = (ROOT / "testsets" / "action4_4k_4x3_30+60"
               / "DJI_20260830095031_0009_D.MP4")
    if dji_src.is_file():
        movie_ts, tracks = gpac.parse_info(gpac.info(dji_src))
        record("tool.GPAC -info DJI 解析",
               movie_ts > 0 and any(t["entry"] == "djmd" for t in tracks),
               f"movie_ts={movie_ts} tracks={len(tracks)}")




def l2_file_level_meta() -> None:
    """L2 文件级元数据: 容器重封装不得丢掉 nrtm meta / tref-cdsc。

    回归自真实故障 (v0.8.0, 709 个文件静默通过): Sony MP4 源交付 MP4 时,
    `place_final` -> `remux_container_lossless` 用 `MP4Box -new -add` 逐轨
    重建 MP4, 而 `-add` **只搬轨道**, 文件级盒全部丢失。

    症状极具欺骗性: rtmd 载荷逐字节与源一致、stts/stsz/stsd 全对、
    `-diso` 结构检查 36/36 PRESERVED —— 但 Gyroflow 从同一份 rtmd 轨
    读出 **0** 个 IMU 样本, 因为它的 Sony 解析器要先看到文件级
    nrtm meta 才肯解析 rtmd 轨。

    本用例在 L2 复现**那条重封装**, 并钉住三件事:
      1. 重封装确实会丢 meta/tref (故障前提, 防止用例在无故障的世界里
         变成永真断言);
      2. `restore_file_level_meta` 之后 XML 与 Lens profile 逐字节一致;
      3. `assert_file_level_meta` / `assert_track_refs` 在丢失时**报错**,
         而不是静默放过。
    """
    from preservation.gpac import GpacContainerBackend, GpacError

    section("L2 文件级元数据 (nrtm meta / tref-cdsc 跨重封装)")
    gpac = GpacContainerBackend()

    src = _sony_rtmd_sample()
    if src is None:
        # `testsets/` 是仓库素材, 不进发布包 (见 build_release 的 allowlist)。
        # 发布包内没有素材时**跳过**: 这里缺的是素材不是代码, 报 FAIL 会让
        # 包内自测看起来是坏的。若连素材目录都不存在, 那才是真的异常。
        if not (ROOT / "testsets").is_dir():
            record("nrtm_跳过 (本布局无 testsets 素材)", True,
                   "release-package layout — covered by L3 in-repo")
            return
        record("nrtm_样本就绪", False, "no Sony rtmd sample under testsets/")
        return

    work = WORK / "file_level_meta"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    # 源必须真的带文件级 nrtm meta, 否则整条用例没有意义。
    from preservation.gpac import _diso_meta_info, _diso_track_refs
    s_info = _diso_meta_info(gpac.diso_xml(src))
    s_refs = _diso_track_refs(gpac.diso_xml(src))
    if not (s_info and s_info.get("handler_type") == "nrtm"):
        record("nrtm_源带文件级 nrtm meta", False,
               f"meta={s_info}")
        return
    s_cdsc = any(targets for _, _, targets in s_refs)
    record("nrtm_源带文件级 nrtm meta", True,
           f"hdlr={s_info.get('handler_type')} item={s_info.get('item_id')}")
    record("nrtm_源带 tref/cdsc", s_cdsc, f"refs={s_refs}")

    # 复现故障路径: 逐轨重建 (与 remux_container_lossless 同一条命令)。
    movie_ts, tracks = gpac.parse_info(gpac.info(src))
    staged = work / "remuxed.mp4"
    prev = gpac.movie_timescale
    gpac.movie_timescale = movie_ts or prev
    try:
        gpac.mux_new(staged, [f"{src}#{t['id']}" for t in tracks])
    finally:
        gpac.movie_timescale = prev
    if not staged.is_file():
        record("nrtm_逐轨重封装可用", False, "no output")
        return
    record("nrtm_逐轨重封装可用", True, f"{staged.stat().st_size} B")

    # (1) 故障前提: 未修复时 meta 确实会丢 —— 这条断言让整个用例有意义。
    lost_info = _diso_meta_info(gpac.diso_xml(staged))
    record("nrtm_重封装本身会丢 meta (故障前提)", not lost_info,
           f"meta={lost_info}")
    try:
        gpac.assert_file_level_meta(_write_manifest(work, src, s_info), staged)
        record("nrtm_丢失时 assert 报错", False, "assert 静默放过了")
    except GpacError as exc:
        record("nrtm_丢失时 assert 报错", True, str(exc)[:110])

    # (2) 恢复后 XML + Lens profile 逐字节一致。
    manifest = _write_manifest(work, src, s_info)
    try:
        restored = gpac.restore_file_level_meta(manifest, staged)
        detail = gpac.assert_file_level_meta(manifest, staged)
        record("nrtm_恢复后内容与源一致", restored, detail[:150])
    except Exception as exc:  # noqa: BLE001 - 失败要进报告, 不是崩栈
        record("nrtm_恢复后内容与源一致", False, f"{type(exc).__name__}: {exc}")
        return

    # (3) tref/cdsc 同样会被重封装丢掉, 恢复后必须在。
    try:
        gpac.restore_track_refs(src, staged)
        record("nrtm_tref_cdsc 恢复", True,
               gpac.assert_track_refs(src, staged))
    except Exception as exc:  # noqa: BLE001
        record("nrtm_tref_cdsc 恢复", False, f"{type(exc).__name__}: {exc}")


def _sony_rtmd_sample() -> Path | None:
    """First Sony source under ``testsets/`` carrying rtmd **and** nrtm.

    Probed rather than hard-coded: `testsets/` is a working scratch area
    whose contents change, and a pinned filename rots into a permanent
    "sample missing" failure -- which is what happened. The previous version
    probed two pinned filenames and two pinned folder names; the corpus was
    then reorganised (Sony material now lives under ``a7m5_4ch_*/...``) and
    every hard-coded path missed, so the suite reported "no Sony rtmd sample"
    while 94 valid Sony files were on disk.

    The caller's first assertion is `handler_type == "nrtm"`, and not every
    Sony file has a file-level meta box -- the long 4CH clips carry rtmd but
    no nrtm -- so the picker has to check both properties, not just rtmd.
    Camera A7M5 material carries both, hence the probe order.

    Ordering is sorted and the probe budget is bounded: this runs inside the
    toolchain suite, and an unbounded walk over a 940-file corpus (one
    ffprobe per file) times the suite out -- the first version of this fix
    did exactly that.
    """
    base = ROOT / "testsets"
    if not base.is_dir():
        return None

    # Cheap pinned hints first: a known-good path needs no probing at all.
    for preferred in (
        base / "20260903" / "A7M5" / "20260903_C1170.MP4",
        base / "v0.8.0_failed" / "_evidence" / "SOURCE_20260901_C0955.MP4",
    ):
        if preferred.is_file():
            return preferred

    ordered: list[Path] = []
    for pattern in ("a7m5_4ch_speech/*/A7M5", "a7m5_4ch_speech/*/*",
                    "a7m5_4ch_*/A7M5", "a7m5_4ch_long", "a7m5_4ch_*",
                    "a7m5_4k60p_*", "a7m5_4k30p_*"):
        for p in sorted(base.glob(pattern)):
            if p.is_dir() and p not in ordered:
                ordered.append(p)
    if not ordered:
        ordered = [base]

    ffprobe = _find_ffprobe()
    if ffprobe is None:
        return None
    budget = 4
    fallback: Path | None = None
    for d in ordered:
        for cand in sorted(d.glob("*.MP4"))[:budget]:
            if not _has_rtmd_track(ffprobe, cand):
                continue
            if _has_nrtm_meta(cand):
                return cand
            if fallback is None:
                fallback = cand
    return fallback


def _find_ffprobe() -> Path | None:
    from core.config import find_executable

    try:
        return find_executable("ffprobe", ROOT)
    except Exception:                              # noqa: BLE001
        return None


def _has_rtmd_track(ffprobe: Path, path: Path) -> bool:
    """True when the file exposes an rtmd data stream (the Sony marker)."""
    try:
        r = sh(str(ffprobe), "-v", "error", "-show_entries",
               "stream=codec_tag_string", "-of", "csv=p=0", str(path),
               timeout=60)
    except Exception:                              # noqa: BLE001
        return False
    return "rtmd" in (r.stdout or "")


def _has_nrtm_meta(path: Path) -> bool:
    """True when the file carries a file-level ``nrtm`` meta box."""
    from preservation.gpac import (
        GpacContainerBackend, GpacError, _diso_meta_info,
    )

    try:
        gpac = GpacContainerBackend()
        info = _diso_meta_info(gpac.diso_xml(path))
    except (GpacError, OSError, ValueError):
        return False
    return bool(info and info.get("handler_type") == "nrtm")


def _write_manifest(work: Path, src: Path, info: dict) -> Path:
    """满足 restore/assert 前置条件的最小 bundle manifest。

    必须带 `nrtm` 键: 没有它 `restore_file_level_meta` 会走"本家族没有
    文件级 meta"的合法 no-op 分支, 于是整条用例会变成永真断言。
    """
    import json

    meta = work / "metadata"
    meta.mkdir(parents=True, exist_ok=True)
    manifest = meta / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "source_path": str(src),
                "nrtm": {
                    "handler_type": info.get("handler_type") or "nrtm",
                    "item_id": int(info.get("item_id") or 1),
                    "item_name": info.get("item_name") or "Lens profile",
                    "item_mime": "application/octet-stream",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest


# ===========================================================================
# L2 — 工具链
# ===========================================================================
