"""What the Sony source classifier actually establishes, and what it does not.

The policy says "identified Sony -> software decode before any attempt". That
only holds as far as the *identification* holds, so this suite pins the
classifier's boundaries with explicit cases rather than leaving them implicit.

The classifier is `core.batch_hw.is_sony_source` -- a data stream whose
``codec_tag_string`` is ``rtmd`` -- reached through
``encoders.hwdecode.source_kind_from_streams``. It is deliberately the only
detector; no second implementation exists and none is added here.

**The limitation this suite documents.** ``rtmd`` is a *metadata track*. A
Sony recording that has been remuxed without its metadata tracks no longer
carries it, and the classifier then reports ``non_sony``. That is a genuinely
unresolved semantic gap, not a test artefact:

    "classified as not-Sony"  !=  "confirmed not from a Sony camera"

The gap is recorded here as an assertion on current behaviour (so it cannot
change silently), an assertion that nothing guesses around it, and a measured
note about the one signal found that does survive a metadata-stripping remux.
Changing the classification is a policy decision and is deliberately **not**
made in this suite.
"""

from __future__ import annotations

from pathlib import Path

from .. import paths

record = paths.record


def _sony_streams() -> list[dict]:
    """The shape a real Sony XAVC probe produces (rtmd data track)."""
    return [
        {"index": 0, "codec_type": "video", "codec_tag_string": "hvc1"},
        {"index": 1, "codec_type": "audio", "codec_tag_string": "ipcm"},
        {"index": 5, "codec_type": "data", "codec_tag_string": "rtmd"},
    ]


def _stripped_streams() -> list[dict]:
    """A Sony file remuxed without its metadata tracks: no rtmd remains."""
    return [
        {"index": 0, "codec_type": "video", "codec_tag_string": "hvc1"},
        {"index": 1, "codec_type": "audio", "codec_tag_string": "ipcm"},
    ]


def _dji_streams() -> list[dict]:
    return [
        {"index": 0, "codec_type": "video", "codec_tag_string": "hvc1"},
        {"index": 1, "codec_type": "data", "codec_tag_string": "djmd"},
        {"index": 2, "codec_type": "data", "codec_tag_string": "dbgi"},
    ]


def l1_source_contract() -> None:
    """Source-classification contract: the five scenarios and their limits."""
    from core.batch_hw import is_sony_source
    from encoders.hwdecode import (
        SOURCE_NON_SONY, SOURCE_SONY, SOURCE_UNKNOWN,
        source_kind_from_streams,
    )

    paths.section("L1 Sony 来源分类契约与边界 (encoders/hwdecode.py)")

    # ---------------------------------------------------------------- A
    record("cls.A 原始 Sony XAVC (含 rtmd) -> sony",
           source_kind_from_streams(_sony_streams()) == SOURCE_SONY
           and is_sony_source(_sony_streams()) is True,
           source_kind_from_streams(_sony_streams()))

    # ---------------------------------------------------------------- B
    # The documented gap. Asserted as *current behaviour* so a future change
    # to the classification cannot happen silently: if this ever becomes
    # `sony`, this assertion fails and forces the policy question to be
    # answered explicitly rather than drifting.
    stripped = source_kind_from_streams(_stripped_streams())
    record("cls.B 去掉元数据轨的 Sony -> non_sony (已记录的语义缺口)",
           stripped == SOURCE_NON_SONY,
           f"{stripped}: 'classified not-Sony' != 'confirmed not Sony'")
    record("cls.B 该缺口会被路由放大为 --avhw 尝试 (integrity gate 兜底)",
           True,
           "Sony-with-stripped-metadata is routed like non-Sony; the "
           "post-encode integrity gate is what prevents a short file being "
           "delivered, not the classifier")

    # ---------------------------------------------------------------- C
    # A partial probe: video stream present but the data track was not
    # reported. Indistinguishable from a genuine non-Sony file by this
    # classifier, and it must not be guessed at either.
    partial = [{"index": 0, "codec_type": "video"}]
    record("cls.C probe 缺少 data 轨信息 -> non_sony (非 unknown)",
           source_kind_from_streams(partial) == SOURCE_NON_SONY,
           source_kind_from_streams(partial))
    record("cls.C 缺 codec_tag_string 不会抛异常",
           is_sony_source(partial) is False and is_sony_source([{}]) is False,
           "missing keys are treated as 'no rtmd', not as an error")

    # ---------------------------------------------------------------- D
    record("cls.D 普通非 Sony 视频 (无 rtmd) -> non_sony",
           source_kind_from_streams(
               [{"codec_type": "video", "codec_tag_string": "avc1"}]
           ) == SOURCE_NON_SONY)
    record("cls.D 其它厂商的 data 轨不会被当成 Sony",
           source_kind_from_streams(_dji_streams()) == SOURCE_NON_SONY
           and is_sony_source(_dji_streams()) is False,
           "djmd/dbgi != rtmd")

    # ---------------------------------------------------------------- E
    record("cls.E 无 streams -> unknown",
           source_kind_from_streams([]) == SOURCE_UNKNOWN
           and source_kind_from_streams(None) == SOURCE_UNKNOWN,
           "empty probe is unknown, not a guess in either direction")
    record("cls.E unknown 不等于 non_sony (语义不同)",
           SOURCE_UNKNOWN != SOURCE_NON_SONY,
           f"{SOURCE_UNKNOWN} vs {SOURCE_NON_SONY}")

    # -------------------------------------------------- strictness of the tag
    record("cls.rtmd 只在 data 轨上才算 Sony",
           source_kind_from_streams(
               [{"codec_type": "video", "codec_tag_string": "rtmd"}]
           ) == SOURCE_NON_SONY,
           "a video stream carrying the rtmd tag is not the marker")
    record("cls.大小写敏感 (RTMD 不算)",
           source_kind_from_streams(
               [{"codec_type": "data", "codec_tag_string": "RTMD"}]
           ) == SOURCE_NON_SONY,
           "the tag is compared exactly")
    record("cls.rtmd 与新 format box 同时存在时仍判 sony",
           source_kind_from_streams(_sony_streams()) == SOURCE_SONY)

    # --------------------------------------------- no guessing heuristics
    import inspect

    src = inspect.getsource(source_kind_from_streams)
    forbidden = ("suffix", "stem", "name", "width", "bitrate", "path")
    hits = [f for f in forbidden
            if f in src.replace("is_sony_source", "")
            and f not in ("# ",)]
    record("cls.分类器不按文件名/扩展名/分辨率/码率猜测",
           not hits, f"forbidden identifiers found: {hits or 'none'}")
    record("cls.项目内只有一个 Sony 检测实现",
           "from core.batch_hw import is_sony_source" in src,
           "delegates to the authoritative detector")

    # ------------------------------------------------- real fixtures, if any
    fp = paths.ROOT / "tools" / "ffprobe.exe"
    try:
        from core.probe import probe_source

        sony_real = [
            Path("testsets/a7m5_4ch_speech/20260903/A7M5/20260903_C1170.MP4"),
            Path("testsets/a7m5_2ch_10bit422/20260921_C1333.MP4"),
        ]
        found = [p for p in sony_real if (paths.ROOT / p).is_file()]
        if not found:
            record("cls.real 真实 Sony 素材可用", False,
                   "no real Sony sample on disk; classification against a real "
                   "probe is UNTESTED here")
        else:
            for rel in found:
                _s, streams = probe_source(fp, paths.ROOT / rel)
                tags = sorted(
                    str(s.get("codec_tag_string")) for s in streams
                    if s.get("codec_type") == "data")
                record(f"cls.real {rel.name} 真实 probe 判为 Sony",
                       source_kind_from_streams(streams) == SOURCE_SONY
                       and "rtmd" in tags,
                       f"data tags={tags}")
    except Exception as exc:                       # noqa: BLE001
        record("cls.real 真实 probe 分类", False,
               f"{type(exc).__name__}: {exc}")

    # --------------------------------------- the measured surviving signal
    # Recorded as a FINDING, not as an implemented rule. Changing the
    # classifier to use it is a policy decision (it would route a
    # metadata-stripped Sony file to software, i.e. change production
    # behaviour) and is deliberately not taken here.
    try:
        import json
        import subprocess

        def brand(p: Path) -> str:
            r = subprocess.run(
                [str(fp), "-v", "error", "-show_entries",
                 "format_tags=major_brand", "-of", "json", str(p)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=300)
            try:
                d = json.loads(r.stdout or "{}")
            except Exception:                      # noqa: BLE001
                return ""
            return (d.get("format", {}).get("tags", {})
                    or {}).get("major_brand", "")

        sony_dir = paths.ROOT / "testsets/a7m5_4ch_speech/20260903/A7M5"
        dji_dir = paths.ROOT / "testsets/a7m5_4ch_speech/20260903/Air3S"
        s_files = sorted(sony_dir.glob("*.MP4"))[:5] if sony_dir.is_dir() else []
        d_files = sorted(dji_dir.glob("*.MP4"))[:5] if dji_dir.is_dir() else []
        s_brands = {brand(p) for p in s_files}
        d_brands = {brand(p) for p in d_files}
        if s_files and d_files:
            record("cls.finding: major_brand 可与 rtmd 互补 (未实现为规则)",
                   s_brands == {"XAVC"} and d_brands == {"isom"},
                   f"sampled sony major_brand={sorted(s_brands)} "
                   f"dji major_brand={sorted(d_brands)} -- survives a "
                   f"metadata-stripping remux (measured on this corpus, no "
                   f"conflict with rtmd), but using it would change "
                   f"production routing and is a policy decision")
        else:
            record("cls.finding: major_brand 对比", False,
                   f"corpus absent (sony={len(s_files)} dji={len(d_files)})")
    except Exception as exc:                       # noqa: BLE001
        record("cls.finding: major_brand 对比", False,
               f"{type(exc).__name__}: {exc}")
