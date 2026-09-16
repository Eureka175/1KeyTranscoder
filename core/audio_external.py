"""External Audio Discovery & Ingest (v0.8.0) — 外挂音频的确定性规则。

本模块只做两件事, 且**全部是确定性规则** (没有"让 Agent 发挥"的空间):

    1. 发现:  同一目录下哪些文件是这个视频的外挂音频 (§17–§21/§30–§33)
    2. 建模:  把它们变成**既有** `AudioSource` 体系里的一员 (§16/§28)

它**不**创建 `ExternalAudioPlan` / `ExternalAudioTrack` / `ExternalAudioChannel`
之类的第二套模型: 外挂来源进来之后就是普通的 `AudioSource` / `AudioStream`,
之后的 selection / mapping / execution / timeline 全部走既有代码。

文件名匹配规则 (§18) —— 唯一判据
--------------------------------

    audio filename stem 必须以 video stem **精确**开头
    且 video stem 之后的第一个字符必须是: 结束 / "-" / "_"

    video  clip001
    ✓      clip001.wav   clip001_01.wav   clip001-01.wav   clip001_audio.wav
    ✗      clip001abc.wav  clip0012.wav   A7M5_001.wav
           random_clip001.wav

比较是 **case-insensitive** 的 (Windows 文件系统同义, 且 §21 要求字母顺序
不区分大小写); 只扫描**视频所在的那一个目录**, 不递归。

前导零 (§19/§33)
----------------

    数字部分按**数值**比较: _1 / _01 / _001 / _0001 都是 1。
    归一化后相同的多个候选**仍然是不同文件**, 不得当成同一个;
    tie-break 用**完整原始文件名**的 case-insensitive natural order ——
    绝不用文件系统枚举顺序, 也绝不随机。

排序 (§20/§21/§31)
------------------

    rank 0: remainder 为空 (= 文件名主干与视频完全相同)
    rank 1: remainder 是纯数字序号 (按归一化数值, 再按文件名 natural order)
    rank 2: 其余合法后缀 (按文件名 natural order)

natural sort = 数字段按数值、其余按 case-insensitive 文本:

    audio1 < audio2 < audio10        (不是 audio1 < audio10 < audio2)
    clip-A = clip-a < clip-B

多个候选 (§31)
--------------

**全部纳入**, 顺序即上面的排序结果 (不是"只选一个")。没有任何候选时
**不报错** (§30): 视频照常输出, 只是没有外挂音频。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .audio_models import AudioSource, AudioSourceType
from .audio_probe import AudioProbeResult, audio_probe_external

__all__ = [
    "AUDIO_FILE_EXTENSIONS",
    "EXTERNAL_DISCOVERY_VERSION",
    "ExternalAudioResult",
    "ExternalCandidate",
    "ExternalDiscovery",
    "append_external_sources",
    "build_external_sources",
    "discover_external_audio",
    "external_source_id",
    "external_source_type",
    "matches_video_stem",
    "natural_key",
    "stem_remainder",
    "video_stem",
]

EXTERNAL_DISCOVERY_VERSION = 1

#: 只考虑**音频文件**: 显式扩展名白名单。刻意不含视频容器扩展名
#: (`.mp4` / `.mov` / `.mkv` …) —— 与视频同名同目录的另一个视频文件不是
#: "这个视频的外挂音频", 它必须由用户显式给出, 不能靠文件名规则混进来。
AUDIO_FILE_EXTENSIONS: tuple[str, ...] = (
    ".wav", ".wave", ".bwf",
    ".flac",
    ".aac", ".m4a", ".m4b", ".mp4a",
    ".opus", ".ogg", ".oga", ".spx", ".weba",
    ".mp3", ".mp2", ".mpga",
    ".ac3", ".eac3", ".dts", ".dtshd",
    ".mka", ".caf", ".aif", ".aiff", ".aifc",
    ".wma", ".amr", ".awb", ".wv", ".ape", ".tak", ".tta",
)

#: 后缀分隔符 (§18): 第一个字符必须结束, 或者是它们之一。
SEPARATORS = ("-", "_")

_NUMERIC_REMAINDER = re.compile(r"^[-_]+(0*)(\d+)$")


# ---------------------------------------------------------------------------
# 文件名规则 (§18/§19/§20/§21)
# ---------------------------------------------------------------------------


def video_stem(path: Path | str) -> str:
    """视频文件名主干 (`2026_0915_A7M5_001.MP4` -> `2026_0915_A7M5_001`)。"""
    return Path(path).stem.strip()


def stem_remainder(video: str, audio_stem: str) -> str | None:
    """`audio_stem` 相对视频主干的**合法**剩余部分; 不合法返回 None。

    这是 §18 的唯一实现: 精确前缀 + 分隔符/结束, 两个条件都满足才返回。
    """
    want = str(video or "").strip().lower()
    have = str(audio_stem or "").strip().lower()
    if not want or not have:
        return None
    if not have.startswith(want):
        return None
    tail = have[len(want):]
    if not tail:
        return ""
    if tail[0] not in SEPARATORS:
        return None
    return tail


def matches_video_stem(video: str, name: str) -> bool:
    """文件名 (含扩展名) 是否匹配该视频的外挂音频命名规则。"""
    return stem_remainder(video, Path(str(name)).stem) is not None


def natural_key(text: Any) -> tuple[tuple[int, int, str], ...]:
    """natural / human sort key (case-insensitive, 数字段按数值)。

    每个片段产出**同构**元组 `(kind, number, text)`, 因此整串可以直接
    用 Python 元组比较, 不会出现 int/str 混比。数字段靠 `kind=0` 排在
    文本段前, 且同位置下按数值比较 —— 这就是 `audio2 < audio10` 的来源。
    """
    parts = re.split(r"(\d+)", str(text or "").lower())
    return tuple(
        (0, int(part), "") if part.isdigit() else (1, 0, part)
        for part in parts if part != ""
    )


def _numeric_index(remainder: str) -> int | None:
    """纯数字序号 remainder -> 归一化数值 (前导零/分隔符数量无关)。"""
    match = _NUMERIC_REMAINDER.match(remainder)
    if match is None:
        return None
    return int(match.group(2))


@dataclass(frozen=True)
class ExternalCandidate:
    """一个合格的候选文件 (文件名规则已通过, 未探测)。"""

    path: str
    name: str
    stem: str
    suffix: str
    remainder: str
    rank: int
    index: int | None = None

    @property
    def key(self) -> tuple[int, int, tuple, str]:
        """排序键: (rank, 归一化序号, 文件名的 natural order, 文件名小写)。

        ⚠️ 第三个元素是**完整原始文件名**的 natural key, 第四个是它的纯
        lexical 小写形式 —— 两者一起构成 §33 要求的 deterministic tie-break:

        * `natural_key` 让 `clip001_2` 排在 `clip001_10` 前面 (数字按数值);
        * 归一化后**相同**的序号 (`clip001_1` / `clip001_01` / `clip001_001`)
          的 natural key 完全相等, 于是由纯 lexical 文件名给出先后 ——
          顺序只由文件名本身决定, 与文件系统枚举顺序无关, 也绝不随机。
        """
        return (
            int(self.rank),
            0 if self.index is None else int(self.index),
            natural_key(self.name),
            str(self.name).lower(),
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "path": self.path,
            "stem": self.stem,
            "suffix": self.suffix,
            "remainder": self.remainder,
            "rank": int(self.rank),
        }
        if self.index is not None:
            data["index"] = int(self.index)
        return data


def _candidate_rank(remainder: str) -> int:
    if remainder == "":
        return 0
    if _numeric_index(remainder) is not None:
        return 1
    return 2


@dataclass
class ExternalDiscovery:
    """一次发现操作的**全部事实** (可审计: 扫了什么、选了什么)。"""

    video: str = ""
    stem: str = ""
    directory: str = ""
    candidates: list[ExternalCandidate] = field(default_factory=list)
    scanned: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    version: int = EXTERNAL_DISCOVERY_VERSION

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def found(self) -> bool:
        return bool(self.candidates)

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.candidates]

    @property
    def paths(self) -> list[str]:
        return [c.path for c in self.candidates]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": int(self.version),
            "video": self.video,
            "stem": self.stem,
            "directory": self.directory,
            "ok": bool(self.ok),
            "candidate_count": len(self.candidates),
            "candidates": [c.to_dict() for c in self.candidates],
            "scanned_audio_files": list(self.scanned),
            "errors": [dict(e) for e in self.errors],
            "warnings": list(self.warnings),
            "notes": list(self.notes),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "stem": self.stem,
            "candidates": self.names,
            "scanned": len(self.scanned),
            "reasons": [str(e.get("reason") or "") for e in self.errors],
        }


def discover_external_audio(
    video: Path | str,
    *,
    directory: Path | str | None = None,
    enabled: bool = True,
) -> ExternalDiscovery:
    """扫描视频**同目录**下的外挂音频候选 (§17–§21/§30–§33)。

    纯文件名规则, 不探测内容、不跑 ffprobe。`enabled=False` 时返回一个
    "明确未启用" 的空结果 (不是错误)。没有任何候选同样不报错 (§30)。
    """
    target = Path(video)
    stem = video_stem(target)
    folder = Path(directory) if directory is not None else target.parent
    out = ExternalDiscovery(
        video=str(target), stem=stem, directory=str(folder)
    )
    if not enabled:
        out.notes.append("external audio discovery is not enabled")
        return out
    if not stem:
        out.errors.append({
            "reason": "external_video_stem_empty",
            "detail": f"cannot derive a video stem from {str(target)!r}",
        })
        return out
    if not folder.is_dir():
        out.errors.append({
            "reason": "external_directory_missing",
            "detail": (
                f"cannot scan for external audio: {folder} is not a directory"
            ),
        })
        return out

    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        out.errors.append({
            "reason": "external_directory_unreadable",
            "detail": f"cannot list {folder}: {exc}",
        })
        return out

    seen: set[str] = set()
    for entry in entries:
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        suffix = entry.suffix.lower()
        if suffix not in AUDIO_FILE_EXTENSIONS:
            continue
        if entry.resolve() == target.resolve():
            # 视频自己恰好也是音频扩展名时不得自我引用。
            continue
        out.scanned.append(entry.name)
        remainder = stem_remainder(stem, entry.stem)
        if remainder is None:
            continue
        key = entry.name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.candidates.append(ExternalCandidate(
            path=str(entry),
            name=entry.name,
            stem=entry.stem,
            suffix=suffix,
            remainder=remainder,
            rank=_candidate_rank(remainder),
            index=_numeric_index(remainder),
        ))

    out.candidates.sort(key=lambda c: c.key)
    if out.candidates:
        out.notes.append(
            "all matching candidates are included, in deterministic natural "
            "order (rank 0: exact stem, 1: numeric index, 2: other suffix)"
        )
    else:
        out.notes.append(
            "no file in this directory matches the video stem: the video is "
            "processed with its own audio only (never an error)"
        )
    return out


# ---------------------------------------------------------------------------
# 建模: 候选 -> 既有 AudioSource (§16/§28)
# ---------------------------------------------------------------------------


def external_source_type(path: Path | str) -> AudioSourceType:
    """扩展名 -> `AudioSourceType` (**只影响读取方式**, 不改变模型)。"""
    suffix = Path(path).suffix.lower()
    if suffix in (".wav", ".wave", ".bwf"):
        return AudioSourceType.WAV
    return AudioSourceType.EXTERNAL


@dataclass
class ExternalAudioResult:
    """外挂音频的建模结果 (来源列表 + 完整发现事实 + 错误/警告)。"""

    sources: list[AudioSource] = field(default_factory=list)
    probes: list[AudioProbeResult] = field(default_factory=list)
    discovery: ExternalDiscovery = field(default_factory=ExternalDiscovery)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def source_ids(self) -> list[str]:
        return [s.source_id for s in self.sources]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "source_ids": self.source_ids,
            "sources": [s.to_dict() for s in self.sources],
            "discovery": self.discovery.to_dict(),
            "errors": [dict(e) for e in self.errors],
            "warnings": list(self.warnings),
            "notes": list(self.notes),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "sources": self.source_ids,
            "candidates": self.discovery.names,
            "reasons": [str(e.get("reason") or "") for e in self.errors],
            "warnings": list(self.warnings),
        }


def external_source_id(path: Path | str) -> str:
    """外挂来源的 `source_id` = **带扩展名的文件名**。

    为什么不用主干: 同一目录下 `clip001.wav` 与 `clip001.aac` 的主干相同,
    主干做 id 会碰撞, 于是必须发明一套去重规则; 而"文件名"本身在同一
    目录内天然唯一、可读, 也正好是用户在计划文件里能直接写出来的东西
    (例如 `"clip001.aac:s1:c0"`)。
    """
    return Path(path).name


def build_external_sources(
    video: Path | str,
    *,
    ffprobe: Path,
    discovery: ExternalDiscovery | None = None,
    enabled: bool = True,
    directory: Path | str | None = None,
) -> ExternalAudioResult:
    """发现 + 探测 -> `AudioSource` 列表 (可直接进既有 AudioPlan)。

    失败策略 (明确, 不静默):

    * 候选文件**无法探测** -> 错误。它通过了"精确匹配视频主干"的命名规则,
      也就是用户明确把它放在了这个视频旁边; 读不了就必须说出来, 不能假装
      没看见 (那等于静默丢音频);
    * 候选文件能读但**没有音频流** -> warning + 跳过 (例如只有封面的 m4a):
      文件本身是可读的, 只是它不携带音频, 这不构成失败。
    """
    found = discovery if discovery is not None else discover_external_audio(
        video, directory=directory, enabled=enabled
    )
    out = ExternalAudioResult(discovery=found)
    if enabled and not found.ok:
        out.errors.extend(dict(e) for e in found.errors)
        return out
    out.warnings.extend(found.warnings)
    out.notes.extend(found.notes)

    for candidate in found.candidates:
        source_id = external_source_id(candidate.path)
        try:
            probe = audio_probe_external(
                ffprobe, Path(candidate.path), source_id=source_id,
                source_type=external_source_type(candidate.path),
            )
        except Exception as exc:                       # noqa: BLE001
            out.errors.append({
                "reason": "external_probe_failed",
                "detail": (
                    f"{candidate.name}: cannot probe the matched external "
                    f"audio file ({type(exc).__name__}: {exc})"
                ),
                "location": candidate.name,
            })
            continue
        if not probe.streams:
            out.warnings.append(
                f"{candidate.name}: matched the video stem but carries no "
                "audio stream — skipped"
            )
            continue
        out.probes.append(probe)
        out.sources.append(probe.to_source())

    if out.sources:
        out.notes.append(
            "external sources appended after the primary source, in "
            "discovery order: " + ", ".join(out.source_ids)
        )
    return out


def append_external_sources(
    plan: Any, sources: Sequence[AudioSource],
) -> Any:
    """把外挂来源**追加**到既有 `AudioPlan` 之后 (§29), 不重建模型。

    追加 = 原有来源与 track 原样在前, 外挂来源的 track 依次在后; 选择集
    重置为"全选 + 派生映射", 与新建一个包含全部来源的默认计划等价 ——
    也就是**没有**任何优先级/替换语义 (§29: 只有用户显式 exclude/replace/
    map 才改变关系)。
    """
    from .audio_models import AudioTrackBuilder, TrackBuildMode

    if not sources:
        return plan
    existing = {s.source_id for s in plan.sources}
    for source in sources:
        if source.source_id in existing:
            raise ValueError(
                "external_audio_source_duplicate: source_id "
                f"{source.source_id!r} already exists in the plan"
            )
        existing.add(source.source_id)

    plan.sources = list(plan.sources) + list(sources)
    for source in sources:
        plan.input_tracks.extend(
            AudioTrackBuilder(
                source.streams, source_id=source.source_id
            ).tracks(TrackBuildMode.PER_STREAM)
        )
    plan.selected_tracks = [t.track_id for t in plan.input_tracks]
    plan.selected_channels = [c.id for c in plan.all_channels()]
    plan.channel_mapping = []
    plan.mapping_kind = "derived"
    plan.output_tracks = []
    return plan
