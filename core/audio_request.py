"""Audio Request (Phase 4C / v0.8.0) — 用户输入 -> **既有** `AudioPlan`。

定位: **输入适配层**, 不是第二个音频模型。

生产入口需要一个可写、可读、可复现的音频意图表达。本模块把它定义成一个
很小的符号化 JSON, 并把它**解析成既有的** `core.audio_models.AudioPlan`
—— 方法全部走 `core.audio_plan.AudioPlanner` 的公开 API。

    JSON (符号化意图)
        ↓  build_audio_plan(request, plan, ...)      <- 本模块
    AudioPlan (唯一内部真相)
        ↓  resolve_audio_execution_path()
    NONE / STREAM_COPY / PCM_ROUTE / PCM_MIX

**没有** `CliAudioPlan` / `Mp4AudioPlan` / `EncoderAudioPlan`: 本模块不定义
任何平行的音频模型, 只定义"用户怎么把话说清楚"以及"怎么翻译成 AudioPlan"。
翻译完成后请求对象即被丢弃。

JSON 形状 (只有这些键; 未知键一律报错, 不静默忽略)
--------------------------------------------------

```json
{
  "version": 1,
  "encode": { "format": "aac", "bitrate": "192k" },
  "channels": {
    "select":  ["source:s1:c0", "source:s2:c0"],
    "exclude": ["source:s1:c1"],
    "map":     ["source:s2:c0", "source:s1:c0"]
  },
  "alignment": "auto",
  "sync":    { "reference": "recorder.wav:s1:c0" },
  "mapping": { "mode": "grouped", "group_size": 2 },
  "external": {},
  "note": "无线麦 CH1/CH2 -> AAC"
}
```

| 键 | 语义 | 缺省 |
|---|---|---|
| `version` | schema 版本 (只接受 1) | 1 |
| `encode` | 输出编码 (`format` / `sample_rate` / `channel_count` / `bitrate`) | 继承来源 (§8) |
| `channels.select` | 只保留这些声道 (顺序 = 给定的顺序) | 全选 (默认计划) |
| `channels.exclude` | 排除这些声道 | 无 |
| `channels.map` | 显式输出顺序 (**必须**与 select 集合一致, 否则拒绝) | 派生 |
| `alignment` | `auto` / `enabled` / `disabled` (§4/§5) | `auto` |
| `sync.reference` | alignment 的 reference channel (**显式**, 从不猜) | 无 (不对齐) |
| `mapping` | 输出流结构 `{mode, group_size}` (§27) | 跟随输入来源结构 |
| `external` | 发现并纳入同目录的外挂音频 (§16–§22) | 不启用 |
| `note` | 自由文本, 只进日志 | 无 |

版本 1 的**加法式**扩展 (v0.8.0)
--------------------------------

`alignment` / `sync` / `mapping` / `external` 是新增的**可选**键: 旧文件
(只写 `encode` + `channels`) 语义完全不变, 因此 `version` 仍是 1 —— 版本号
守的是**不兼容**变更, 而"多几个可选键"是兼容的。未知键依然一律拒绝, 所以
新文件在旧构建上会明确报错而不是被静默忽略一半。

* `channels.select` 为空 (或缺省) 且无 `exclude`/`map`/`external`/
  alignment/mapping 意图 -> **默认计划** (`is_default`), 执行图判定为
  `NONE` —— 也就是"什么都不做", 生产默认路径原样保留。
* 声道选择器用的是**既有身份** `AudioChannel.id` (`"{source}:s{stream}:c{channel}"`),
  与真实 ffprobe 事实一一对应, 不引入 `mp4_audio_index` 之类的第二套身份。
  外挂来源的身份见 `core.audio_external.external_source_id`。

边界
----

* **不解释**执行图: 请求里没有 `route` / `mix` / `stream_copy` 这类词, 也
  不接受。走哪张图完全由 `resolve_audio_execution_path()` 从解析后的
  `AudioPlan` 推导 —— 用户表达"要什么", 不表达"走哪条代码路径"。
* **不解释**格式策略: PCM/compressed 的判定在 `core.audio_format`。
* **不拼 argv**, **不执行**任何进程。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .audio_encode import (
    AudioEncodeFormat,
    AudioFormatSpec,
)
from .audio_format import (
    AudioAlignmentPolicy,
    ExplicitFormat,
)
from .audio_models import AudioPlan
from .audio_output_structure import (
    AudioMappingPolicy,
    parse_mapping_policy,
)
from .audio_plan import AudioPlanner, AudioPlanError

__all__ = [
    "AUDIO_REQUEST_VERSION",
    "REASON_AUDIO_REQUEST_INVALID",
    "REASON_AUDIO_REQUEST_MISSING",
    "REASON_AUDIO_REQUEST_PARSE",
    "REASON_AUDIO_REQUEST_SELECTION",
    "REASON_AUDIO_REQUEST_VERSION",
    "AudioRequestError",
    "AudioRequest",
    "build_audio_plan",
    "describe_audio_request",
    "load_audio_request",
    "parse_audio_request",
]

#: 本模块理解的 schema 版本 (唯一合法值; v0.8.0 的扩展是加法式的)。
AUDIO_REQUEST_VERSION = 1

REASON_AUDIO_REQUEST_MISSING = "audio_request_missing"
REASON_AUDIO_REQUEST_PARSE = "audio_request_parse"
REASON_AUDIO_REQUEST_VERSION = "audio_request_version"
REASON_AUDIO_REQUEST_INVALID = "audio_request_invalid"
REASON_AUDIO_REQUEST_SELECTION = "audio_request_selection"

_ALLOWED_KEYS = {
    "version", "encode", "channels", "note", "source_id",
    "alignment", "sync", "mapping", "external",
}
_ALLOWED_ENCODE_KEYS = {"format", "sample_rate", "channel_count", "bitrate"}
_ALLOWED_CHANNEL_KEYS = {"select", "exclude", "map"}
_ALLOWED_SYNC_KEYS = {"reference"}
_ALLOWED_EXTERNAL_KEYS = {"enabled"}


class AudioRequestError(ValueError):
    """请求本身不合法 (稳定 reason code; 不是"要不要尽力执行"的问题)。"""

    def __init__(
        self, reason: str, detail: str, *, location: str | None = None,
    ) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.location = location

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"reason": self.reason, "detail": self.detail}
        if self.location is not None:
            data["location"] = self.location
        return data


@dataclass
class AudioRequest:
    """解析后的请求 (**输入 DTO**, 不是音频模型)。

    它刻意只保存"用户说了什么", 不保存任何模型对象; `build_audio_plan()`
    才是把它翻译成 `AudioPlan` 的地方。翻译后本对象即可丢弃。

    `encode_explicit` 记录用户**真的拧过** `encode` 里的哪些旋钮 —— 优先级
    链 `manual > source-derived > encoder default` (§8) 必须能区分"用户要
    AAC"与"用户没说, 默认 AAC", 否则 §6 (PCM 默认输出 PCM) 无法实现。
    """

    version: int = AUDIO_REQUEST_VERSION
    encode: AudioFormatSpec = field(default_factory=AudioFormatSpec)
    encode_explicit: ExplicitFormat = field(default_factory=ExplicitFormat)
    select: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    map: list[str] = field(default_factory=list)
    alignment: str = AudioAlignmentPolicy.AUTO.value
    sync_reference: str = ""
    mapping: AudioMappingPolicy | None = None
    external: bool = False
    source_id: str | None = None
    note: str = ""

    @property
    def is_empty(self) -> bool:
        """是否"没有改动任何东西" = 不改动选择、也不新增任何音频意图。

        只有编码格式 (或不写) 而没有任何选择/外挂/对齐/结构化意图时, 计划
        保持默认 => 执行图 `NONE` => 生产路径与不传 `--audio-plan` 完全一致。
        """
        return not (
            self.select or self.exclude or self.map
            or self.external
            or self.alignment != AudioAlignmentPolicy.AUTO.value
            or self.mapping is not None
        )

    @property
    def alignment_policy(self) -> AudioAlignmentPolicy:
        return (
            AudioAlignmentPolicy.coerce(
                self.alignment, AudioAlignmentPolicy.AUTO
            ) or AudioAlignmentPolicy.AUTO
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "version": int(self.version),
            "encode": self.encode.to_dict(),
        }
        if self.encode_explicit.any_set:
            data["encode_explicit"] = {
                k: True for k in (
                    "format", "bitrate", "sample_rate", "channel_count",
                ) if getattr(self.encode_explicit, k)
            }
        channels: dict[str, Any] = {}
        if self.select:
            channels["select"] = list(self.select)
        if self.exclude:
            channels["exclude"] = list(self.exclude)
        if self.map:
            channels["map"] = list(self.map)
        if channels:
            data["channels"] = channels
        if self.alignment != AudioAlignmentPolicy.AUTO.value:
            data["alignment"] = self.alignment
        if self.sync_reference:
            data["sync"] = {"reference": self.sync_reference}
        if self.mapping is not None:
            data["mapping"] = self.mapping.to_dict()
        if self.external:
            data["external"] = {"enabled": True}
        if self.source_id:
            data["source_id"] = self.source_id
        if self.note:
            data["note"] = self.note
        return data


def load_audio_request(path: Path | str) -> AudioRequest:
    """从 JSON 文件读取请求 (唯一允许的入口; 解析失败即抛)。"""
    target = Path(path)
    if not target.is_file():
        raise AudioRequestError(
            REASON_AUDIO_REQUEST_MISSING,
            f"audio plan file not found: {target}",
        )
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise AudioRequestError(
            REASON_AUDIO_REQUEST_MISSING,
            f"cannot read {target}: {exc}",
        ) from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise AudioRequestError(
            REASON_AUDIO_REQUEST_PARSE,
            f"{target} is not valid JSON: {exc}",
        ) from exc
    return parse_audio_request(data)


def parse_audio_request(data: Any) -> AudioRequest:
    """校验并解析请求 dict (未知键**报错**, 不静默忽略)。"""
    if not isinstance(data, Mapping):
        raise AudioRequestError(
            REASON_AUDIO_REQUEST_INVALID,
            f"audio plan must be a JSON object, got {type(data).__name__}",
        )

    unknown = sorted(set(data) - _ALLOWED_KEYS)
    if unknown:
        raise AudioRequestError(
            REASON_AUDIO_REQUEST_INVALID,
            f"unknown key(s) {unknown}; allowed: {sorted(_ALLOWED_KEYS)}",
        )

    version = data.get("version", AUDIO_REQUEST_VERSION)
    try:
        version = int(version)
    except (TypeError, ValueError) as exc:
        raise AudioRequestError(
            REASON_AUDIO_REQUEST_VERSION,
            f"version must be an integer, got {version!r}",
        ) from exc
    if version != AUDIO_REQUEST_VERSION:
        raise AudioRequestError(
            REASON_AUDIO_REQUEST_VERSION,
            f"unsupported audio plan version {version}; this build "
            f"understands version {AUDIO_REQUEST_VERSION}",
        )

    encode_data = data.get("encode") or {}
    if not isinstance(encode_data, Mapping):
        raise AudioRequestError(
            REASON_AUDIO_REQUEST_INVALID,
            "encode must be an object",
        )
    unknown_enc = sorted(set(encode_data) - _ALLOWED_ENCODE_KEYS)
    if unknown_enc:
        raise AudioRequestError(
            REASON_AUDIO_REQUEST_INVALID,
            f"unknown encode key(s) {unknown_enc}; allowed: "
            f"{sorted(_ALLOWED_ENCODE_KEYS)}",
        )
    fmt_raw = encode_data.get("format", AudioEncodeFormat.AAC.value)
    fmt = AudioEncodeFormat.coerce(fmt_raw, None)
    if fmt is None:
        raise AudioRequestError(
            REASON_AUDIO_REQUEST_INVALID,
            f"unknown audio format {fmt_raw!r}; supported: "
            f"{[f.value for f in AudioEncodeFormat]}",
        )
    spec = AudioFormatSpec.from_dict({**dict(encode_data), "format": fmt.value})
    # 优先级链需要区分"用户要这个格式"与"用户没说, 默认这个格式": 只有
    # **显式出现**的键才算 manual (§8)。
    encode_explicit = ExplicitFormat(
        format=bool(encode_data.get("format")),
        bitrate=bool(encode_data.get("bitrate")),
        sample_rate=bool(encode_data.get("sample_rate")),
        channel_count=bool(encode_data.get("channel_count")),
    )

    channels = data.get("channels") or {}
    if not isinstance(channels, Mapping):
        raise AudioRequestError(
            REASON_AUDIO_REQUEST_INVALID, "channels must be an object",
        )
    unknown_ch = sorted(set(channels) - _ALLOWED_CHANNEL_KEYS)
    if unknown_ch:
        raise AudioRequestError(
            REASON_AUDIO_REQUEST_INVALID,
            f"unknown channels key(s) {unknown_ch}; allowed: "
            f"{sorted(_ALLOWED_CHANNEL_KEYS)}",
        )

    def _ids(key: str) -> list[str]:
        value = channels.get(key) or []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            raise AudioRequestError(
                REASON_AUDIO_REQUEST_INVALID,
                f"channels.{key} must be a list of channel ids",
            )
        out: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text:
                raise AudioRequestError(
                    REASON_AUDIO_REQUEST_INVALID,
                    f"channels.{key} contains an empty channel id",
                )
            out.append(text)
        return out

    select = _ids("select")
    exclude = _ids("exclude")
    mapping = _ids("map")

    # --- alignment (§4/§5): 意图, 不是执行图 --------------------------------
    alignment_raw = data.get("alignment", AudioAlignmentPolicy.AUTO.value)
    alignment = AudioAlignmentPolicy.coerce(alignment_raw, None)
    if alignment is None:
        raise AudioRequestError(
            REASON_AUDIO_REQUEST_INVALID,
            f"unknown alignment {alignment_raw!r}; supported: "
            f"{[p.value for p in AudioAlignmentPolicy]}",
        )
    alignment = alignment.value

    # --- sync (§39/§40): reference 必须显式, 从不猜 -------------------------
    sync_data = data.get("sync") or {}
    if not isinstance(sync_data, Mapping):
        raise AudioRequestError(
            REASON_AUDIO_REQUEST_INVALID, "sync must be an object",
        )
    unknown_sync = sorted(set(sync_data) - _ALLOWED_SYNC_KEYS)
    if unknown_sync:
        raise AudioRequestError(
            REASON_AUDIO_REQUEST_INVALID,
            f"unknown sync key(s) {unknown_sync}; allowed: "
            f"{sorted(_ALLOWED_SYNC_KEYS)}",
        )
    sync_reference = str(sync_data.get("reference") or "").strip()
    if "reference" in sync_data and not sync_reference:
        raise AudioRequestError(
            REASON_AUDIO_REQUEST_INVALID,
            "sync.reference must be a non-empty channel id "
            "(\"<source>:s<stream>:c<channel>\"); there is no implicit "
            "reference",
        )
    if sync_reference and alignment == AudioAlignmentPolicy.DISABLED.value:
        raise AudioRequestError(
            REASON_AUDIO_REQUEST_INVALID,
            "sync.reference is given but alignment is disabled: the "
            "reference would never be used",
        )

    # --- mapping (§27): 输出流结构, 未知值明确报错 --------------------------
    mapping_raw = data.get("mapping")
    try:
        mapping_policy = parse_mapping_policy(mapping_raw)
    except ValueError as exc:
        raise AudioRequestError(
            REASON_AUDIO_REQUEST_INVALID, str(exc),
        ) from exc

    # --- external (§16–§22): 出现即启用; 只接受显式对象 ---------------------
    external_raw = data.get("external")
    external_enabled = False
    if external_raw is not None:
        if external_raw is False:
            external_enabled = False
        elif external_raw is True or external_raw == {}:
            external_enabled = True
        elif isinstance(external_raw, Mapping):
            unknown_ext = sorted(set(external_raw) - _ALLOWED_EXTERNAL_KEYS)
            if unknown_ext:
                raise AudioRequestError(
                    REASON_AUDIO_REQUEST_INVALID,
                    f"unknown external key(s) {unknown_ext}; allowed: "
                    f"{sorted(_ALLOWED_EXTERNAL_KEYS)}",
                )
            external_enabled = bool(external_raw.get("enabled", True))
        else:
            raise AudioRequestError(
                REASON_AUDIO_REQUEST_INVALID,
                "external must be an object (or true/false), got "
                f"{type(external_raw).__name__}",
            )

    # map 与 select 必须是**同一个集合**: map 只重新指定顺序, 不允许借它
    # 悄悄增删声道 (否则用户以为在排序, 实际改了内容)。
    if mapping:
        if not select:
            raise AudioRequestError(
                REASON_AUDIO_REQUEST_SELECTION,
                "channels.map requires channels.select (map only reorders the "
                "selected set; it cannot add or remove channels)",
            )
        if sorted(mapping) != sorted(select):
            raise AudioRequestError(
                REASON_AUDIO_REQUEST_SELECTION,
                "channels.map must be a permutation of channels.select: "
                f"map={sorted(mapping)} select={sorted(select)}",
            )
        if len(set(mapping)) != len(mapping):
            raise AudioRequestError(
                REASON_AUDIO_REQUEST_SELECTION,
                f"channels.map contains duplicates: {mapping}",
            )

    return AudioRequest(
        version=version,
        encode=spec,
        encode_explicit=encode_explicit,
        select=select,
        exclude=exclude,
        map=mapping,
        alignment=alignment,
        sync_reference=sync_reference,
        mapping=mapping_policy,
        external=external_enabled,
        source_id=(
            str(data["source_id"]) if data.get("source_id") else None
        ),
        note=str(data.get("note") or ""),
    )


def build_audio_plan(
    request: AudioRequest,
    plan: AudioPlan,
) -> AudioPlan:
    """把请求应用到**既有** `AudioPlan` 上 (全部走 `AudioPlanner` 公开 API)。

    `plan` 是调用方用真实探测事实建好的计划 (通常是
    `core.audio_probe` 的产物)。本函数只做"选择/排除/排序"三件事。

    请求为空 (`is_empty`) 时**返回原计划不动** —— 于是
    `AudioPlan.is_default` 保持成立, 执行图判定为 `NONE`, 生产默认路径
    丝毫不受影响。
    """
    if not isinstance(request, AudioRequest):
        raise AudioRequestError(
            REASON_AUDIO_REQUEST_INVALID,
            f"expected AudioRequest, got {type(request).__name__}",
        )
    if request.is_empty:
        return plan

    planner = AudioPlanner(plan)
    try:
        if request.select:
            planner.select_channels(*request.select)
        if request.exclude:
            planner.exclude_channels(*request.exclude)
        if request.map:
            planner.map_channels(*request.map)
    except AudioPlanError as exc:
        # 选择本身不成立 (声道不存在 / 顺序冲突 / 数量不符) -> 明确的
        # selection 错误, 不吞成"编码失败"。
        raise AudioRequestError(
            REASON_AUDIO_REQUEST_SELECTION,
            f"{exc.reason if hasattr(exc, 'reason') else 'invalid'}: "
            f"{exc}",
        ) from exc
    return planner.plan


def describe_audio_request(request: AudioRequest) -> str:
    """一行可读摘要 (日志/测试断言用, 稳定格式)。"""
    parts = []
    if request.select:
        parts.append(f"select={','.join(request.select)}")
    if request.exclude:
        parts.append(f"exclude={','.join(request.exclude)}")
    if request.map:
        parts.append(f"map={','.join(request.map)}")
    if request.external:
        parts.append("external=discover")
    if request.alignment != AudioAlignmentPolicy.AUTO.value:
        parts.append(f"alignment={request.alignment}")
    if request.sync_reference:
        parts.append(f"reference={request.sync_reference}")
    if request.mapping is not None:
        parts.append(f"mapping={request.mapping.summary()}")
    encode = (
        request.encode.format.value
        if request.encode_explicit.format
        else f"inherit({request.encode.format.value})"
    )
    if request.is_empty:
        return (
            f"audio-request: no channel selection (default plan; "
            f"encode={encode})"
        )
    return f"audio-request: {' '.join(parts)} encode={encode}"
