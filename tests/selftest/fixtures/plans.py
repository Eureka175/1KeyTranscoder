#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AudioPlan 构造 fixture + PCM render/读取 helper.

只依赖公开 API (`core.audio_models` / `core.audio_plan` /
`core.audio_process` / `core.audio_wav` / `core.audio_pcm`), 不复制任何
生产逻辑。
"""

from __future__ import annotations

import subprocess
from typing import Any
from pathlib import Path
from ..paths import FFMPEG
from ..fixtures.audio import P3A_SR
from ..paths import WORK
from ..fixtures.audio import _raw_audio_stream
from ..paths import ffprobe_json
from ..paths import record
from ..paths import sh

try:
    import numpy as np
except ImportError:                       # pragma: no cover
    np = None                             # type: ignore[assignment]

def _p3a_build_plan(specs: list[dict[str, Any]]) -> Any:
    """[{source_id, path, streams, [source_type]}, …] -> AudioPlan (多来源)。

    真实来源用探测得到的 `AudioStream`; 合成来源用 `_p3a_plan()`。
    """
    from core.audio_models import (
        AudioPlan, AudioSource, AudioSourceType, AudioTrackBuilder,
    )

    plan = AudioPlan()
    tracks: list[Any] = []
    for spec in specs:
        streams = list(spec["streams"])
        tracks.extend(
            AudioTrackBuilder(streams, source_id=spec["source_id"]).tracks()
        )
        plan.sources.append(AudioSource(
            source_id=spec["source_id"],
            source_type=spec.get("source_type", AudioSourceType.MEDIA),
            path=str(spec["path"]),
            streams=streams,
            input_index=spec.get("input_index"),
        ))
    plan.input_tracks = tracks
    plan.selected_tracks = [t.track_id for t in tracks]
    plan.selected_channels = [c.id for c in plan.all_channels()]
    return plan


def _p3a_plan(
    specs: list[dict[str, Any]], *, sample_rate: int = P3A_SR,
) -> Any:
    """[{source_id, path, channels, samples}, …] -> AudioPlan (多来源 WAV)。

    declared / actual 均来自 fixture 的真实长度 (WAV 无 nb_frames, 因此
    声明值走 `duration × sample_rate`), 与真实探测路径一致。
    """
    from core.audio_models import build_audio_streams

    prepared: list[dict[str, Any]] = []
    for spec in specs:
        raw = [_raw_audio_stream(
            0,
            channels=int(spec["channels"]),
            codec="pcm_f32le",
            sample_rate=str(sample_rate),
            sample_fmt="flt",
            layout=None,
            duration=f"{int(spec['samples']) / sample_rate:.9f}",
            bit_rate=None,
            codec_long_name="PCM 32-bit floating point",
            tag_string=None,
        )]
        prepared.append({
            **spec,
            "streams": build_audio_streams(
                raw, source_id=spec["source_id"]
            ),
        })
    return _p3a_build_plan(prepared)


def _p3a_swap(plan: Any, channel_id: str, replacement: Any) -> bool:
    """把某个 AudioChannel 换成另一个对象 (保持身份串)。"""
    for track in plan.input_tracks:
        for index, ch in enumerate(track.channels):
            if ch.id == channel_id:
                track.channels[index] = replacement
                return True
    return False


def _p3a_route_channels(plan: Any, order: list[str]) -> bool:
    """按给定顺序设置输出 (显式 mapping); 数量不匹配 -> 用 exclude 表达。"""
    from core.audio_plan import AudioPlanner

    planner = AudioPlanner(plan)
    ok = True
    try:
        planner.select_channels(*order)
        if len(order) > 1:
            planner.map_channels(*order)
    except Exception:                     # noqa: BLE001
        ok = False
    return ok


def _p3a_pipe_factory(plan: Any, ffmpeg: Any, work_dir: Any,
                      chunk_frames: int) -> Any:
    return _PipePCMReader(plan, ffmpeg, work_dir, chunk_frames)


def _p3a_render(
    plan: Any, out: Path, *, backend: str = "pipe", **kwargs: Any
) -> Any:
    """统一入口: 走真实渲染链路。

    `backend="pipe"` 用管道读取器 (与生产读取器同接口、不同传输层 — 两者
    结果必须逐样本一致); `backend="reader"` 用生产 `AudioPCMReader` (按块
    span 读, 适合 chunk 极小、渲染次数多的用例, 避免管道逐块读的开销)。
    """
    from core.audio_process import run_audio_render
    from core.audio_wav import WavFormat

    d = WORK / "p3a"
    d.mkdir(parents=True, exist_ok=True)
    factory = _p3a_pipe_factory if backend == "pipe" else None
    return run_audio_render(
        plan,
        ffmpeg=FFMPEG,
        work_dir=d / "work",
        output_path=out,
        sample_format=kwargs.pop("sample_format", WavFormat.FLOAT32),
        overwrite=True,
        reader_factory=factory,
        **kwargs,
    )


def _p3a_read(path: Path) -> Any:
    from core.audio_wav import read_wav

    return read_wav(path)


def _p3a_ready(tag: str) -> bool:
    """Phase 3A 前置条件 (numpy 可用); 缺失时记录一次并跳过整组。"""
    if np is not None:
        return True
    record(f"l1.p3a.{tag}.numpy 可用", False,
           "numpy 缺失 — Phase 3A 用例跳过 (与 channel-sync 同一可选依赖)")
    return False


class _PipePCMReader:
    """ffmpeg stdout 直读的 PCM reader (与 `AudioPCMReader` 同接口)。

    测试专用: 与生产读取器**同一抽象**、不同传输层 — 结果必须逐样本一致,
    从而把"结果不依赖读取实现"钉进回归 (Phase 3A §性能/§chunk invariance)。
    """

    def __init__(self, plan: Any, ffmpeg: Path, work_dir: Path,
                 chunk_frames: int = 16384) -> None:
        self.plan = plan
        self.ffmpeg = Path(ffmpeg)
        self.work_dir = Path(work_dir)
        self.chunk_frames = int(chunk_frames)
        self.streams: dict[str, dict[str, Any]] = {}
        self.procs: dict[str, Any] = {}
        self.positions: dict[str, int] = {}
        self.decoded = 0

    def _raw_stream(self, stream_id: str) -> dict[str, Any]:
        for source in self.plan.sources:
            for stream in source.streams:
                if stream.id == stream_id:
                    return {
                        "path": source.path,
                        "position": int(
                            stream.audio_position
                            if stream.audio_position is not None
                            else stream.stream_index
                        ),
                        "channels": int(stream.channel_count),
                        "sample_rate": int(stream.sample_rate),
                        "declared": (
                            int(round(float(stream.duration_sec or 0)
                                      * int(stream.sample_rate)))
                            if stream.duration_sec else None
                        ),
                    }
        raise KeyError(stream_id)

    def prepare(self, stream_ids: Any = None) -> list[Any]:
        ids = (
            [str(i) for i in stream_ids] if stream_ids is not None
            else [s.id for s in self.plan.sources for s in s.streams]
        )
        out = []
        for sid in ids:
            out.append(self._load(sid))
        return out

    def _load(self, stream_id: str) -> dict[str, Any]:
        if stream_id in self.streams:
            return self.streams[stream_id]
        spec = self._raw_stream(stream_id)
        # 实际可用帧数: WAV 读 header 的 data 大小 (与生产读取器同一判据),
        # 其它容器用 ffprobe 的 nb_frames / duration 声明值。
        actual = 0
        declared = spec["declared"]
        path = Path(spec["path"])
        try:
            from core.audio_wav import parse_wav_header

            head = parse_wav_header(path.read_bytes(), path=str(path))
            if head.channel_count == spec["channels"]:
                actual = int(head.frame_count)
        except Exception:                     # noqa: BLE001
            actual = 0
        if actual <= 0:
            approx = max(
                0, (path.stat().st_size - 4096)
                // max(1, 4 * int(spec["channels"]))
            )
            actual = int(declared) if declared else approx
        info = {
            "stream_id": stream_id, "source_id": stream_id.split(":")[0],
            "stream_index": int(stream_id.rsplit(":s", 1)[1]),
            "channels": spec["channels"],
            "sample_rate": spec["sample_rate"],
            "declared": declared,
            "actual": actual,
            "_spec": spec,
        }
        self.streams[stream_id] = info
        return info

    def _proc(self, stream_id: str) -> Any:
        proc = self.procs.get(stream_id)
        if proc is None:
            spec = self.streams[stream_id]["_spec"]
            cmd = [
                str(self.ffmpeg), "-v", "error", "-nostdin",
                "-i", str(spec["path"]),
                "-map", f"0:a:{spec['position']}",
                "-vn", "-sn", "-dn",
            ]
            if int(spec["channels"]) > 1:
                from core.audio_pcm import identity_channelmap

                cmd += ["-af", f"channelmap={identity_channelmap(int(spec['channels']))}"]
            cmd += [
                "-f", "f32le", "-ac", str(spec["channels"]), "-",
            ]
            proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            self.procs[stream_id] = proc
            self.positions[stream_id] = 0
        return proc

    def read(self, stream_id: str, *, channel_index: int = 0, start: int = 0,
             count: int) -> tuple[Any, int]:
        """顺序管道读取: 回退流(顺序读) 或 前进(co读取) 都正确。"""
        want = int(count)
        if want <= 0:
            return np.zeros(max(0, want), dtype="<f4"), 0
        info = self.streams[stream_id]
        proc = self._proc(stream_id)
        pos = self.positions[stream_id]
        chans = int(info["channels"])
        item = 4
        stride = item * chans
        lo = max(0, int(start))
        if lo < pos:
            proc.kill()
            proc.stdout.close()
            self.procs.pop(stream_id, None)
            proc = self._proc(stream_id)
            pos = 0
        skip = (lo - pos) * stride
        while skip > 0:
            got = proc.stdout.read(min(skip, 1 << 20))
            if not got:
                break
            skip -= len(got)
        raw = b""
        need = want * stride
        while len(raw) < need:
            got = proc.stdout.read(need - len(raw))
            if not got:
                break
            raw += got
        self.positions[stream_id] = lo + len(raw) // stride
        block = np.zeros(want, dtype="<f4")
        valid = len(raw) // stride
        if valid:
            framed = np.frombuffer(raw[: valid * stride], dtype="<f4").reshape(
                valid, chans
            )
            block[:valid] = framed[:, int(channel_index)]
        self.decoded += valid
        return block, valid

    def read_frames(self, stream_id: str, *, channel_index: int = 0,
                    start: int, count: int) -> Any:
        """与 `AudioPCMReader.read_frames` 同语义 (贴窗口, 越界补静音)。"""
        want = int(count)
        if want <= 0:
            return np.zeros(max(0, want), dtype="<f4")
        info = self.streams[stream_id]
        lo = int(start)
        lo_c = max(0, lo)
        hi_c = min(int(info["actual"]), lo + want)
        valid = max(0, hi_c - lo_c)
        out = np.zeros(want, dtype="<f4")
        if valid:
            got, fetched = self.read(
                stream_id, channel_index=channel_index, start=lo_c,
                count=valid,
            )
            if fetched > 0:
                out[lo_c - lo: lo_c - lo + fetched] = got[:fetched]
        return out

    def availability(self) -> dict[tuple[str, int], dict[str, Any]]:
        return {
            (i["source_id"], i["stream_index"]): {
                "declared_samples": i["declared"],
                "actual_samples": i["actual"],
                "declared_source": "duration" if i["declared"] else None,
            }
            for i in self.streams.values()
        }

    def duration_mismatches(self, tolerance_samples: int = 1) -> list[dict]:
        out = []
        for info in self.streams.values():
            dec, act = info["declared"], info["actual"]
            if dec is None or abs(act - dec) <= tolerance_samples:
                continue
            out.append({
                "reason": "audio_duration_metadata_mismatch",
                "stream_id": info["stream_id"],
                "declared_samples": int(dec), "actual_samples": int(act),
                "delta_samples": int(act) - int(dec),
            })
        return out

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "stream_id": i["stream_id"], "source_id": i["source_id"],
                "stream_index": i["stream_index"],
                "sample_rate": i["sample_rate"],
                "channel_count": i["channels"],
                "declared_samples": i["declared"],
                "actual_samples": i["actual"],
                "declared_source": "duration" if i["declared"] else None,
                "codec_name": "pcm_f32le", "linear_pcm": True,
            }
            for i in self.streams.values()
        ]

    def info(self, stream_id: str) -> Any:
        return self.streams[stream_id]

    @property
    def decode_seconds(self) -> float:
        return 0.0

    def close(self) -> None:
        for proc in self.procs.values():
            try:
                proc.kill()
                proc.stdout.close()
                proc.wait(timeout=10)
            except Exception:             # noqa: BLE001
                pass
        self.procs.clear()

    def __enter__(self) -> "_PipePCMReader":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _p3a_pcm16_roundtrip(d: Path) -> bool:
    """整数 PCM 输入经 ffmpeg -> float32 -> WAV 后仍在 [-1,1] 且可往返。"""
    from core.audio_wav import WavFormat, read_wav, write_wav

    sr = 48000
    n = 480
    ramp = (np.arange(n, dtype=np.float32) / (n / 2.0) - 1.0).astype(np.float32)
    for fmt, codec, tol in (
        (WavFormat.PCM16, "pcm_s16le", 1.5 / 32768),
        (WavFormat.PCM24, "pcm_s24le", 3.0 / 8388608),
        (WavFormat.PCM32, "pcm_s32le", 3.0 / 2147483648),
    ):
        src = d / f"int_{fmt.value}.wav"
        write_wav(src, [ramp.reshape(-1, 1)], sample_rate=sr, channel_count=1,
                  sample_format=fmt, frame_count=n, overwrite=True)
        plan = _p3a_plan([{"source_id": "i", "path": src, "channels": 1,
                           "samples": n}], sample_rate=sr)
        plan.source("i").streams[0].codec_name = codec
        out = d / f"int_{fmt.value}_out.wav"
        res = _p3a_render(plan, out)
        if not res.ok:
            return False
        back, _info = read_wav(out)
        if float(np.max(np.abs(back))) > 1.0:
            return False
        if float(np.max(np.abs(back[:, 0] - ramp))) > tol:
            return False
    return True


def _p3a_truncated_fixture(path: Path, declared_frames: int,
                           real_frames: int) -> Path:
    """WAV fixture: **header 声明 `declared_frames` / 实际 `real_frames`**。

    做法: 正常写入 `real_frames` 帧, 再把 `data` chunk 长度改写成
    `declared_frames` 对应的字节数 (文件的真实长度仍是 real, 因此
    "实际解码样本数" 与 "metadata 声明值" 人为错开)。

    用于 T12: metadata 与实际解码不一致时必须**检测到**, 并以实际解码为
    最终事实 —— 不静默相信 metadata, 也不静默补静音。
    """
    from core.audio_wav import WavFormat, write_wav

    x = np.zeros((real_frames, 1), dtype=np.float32)
    x[100, 0] = 0.5
    x[real_frames - 1, 0] = 0.25
    write_wav(path, [x], sample_rate=P3A_SR, channel_count=1,
              sample_format=WavFormat.FLOAT32, frame_count=real_frames,
              overwrite=True)
    raw = bytearray(path.read_bytes())
    # 定位 data chunk 的 size 字段并改写为声明长度
    pos = 12
    while pos + 8 <= len(raw):
        chunk_id = bytes(raw[pos:pos + 4])
        size = int.from_bytes(raw[pos + 4:pos + 8], "little")
        if chunk_id == b"data":
            raw[pos + 4:pos + 8] = (declared_frames * 4).to_bytes(4, "little")
            break
        pos += 8 + size + (size % 2)
    path.write_bytes(bytes(raw))
    return path


def _p3a_long_fixture(path: Path, *, seconds: float = 30.0) -> Path:
    """长音频 fixture (分块生成, 每块只驻留一个 chunk)。

    素材长度用 `seconds` 显式给出 (默认 30s: 4CH float32 ≈ 23MB, 足以验证
    "峰值内存受 chunk 限制"而不让回归时间失控)。
    """
    from core.audio_wav import WavFormat, write_wav

    total = int(P3A_SR * seconds)
    chunk = 4096
    t = (np.arange(total, dtype=np.float32)) / P3A_SR
    blocks = []
    for start in range(0, total, chunk):
        n = min(chunk, total - start)
        seg = t[start:start + n]
        block = np.zeros((n, 4), dtype=np.float32)
        block[:, 0] = 0.4 * np.sin(2 * np.pi * 440.0 * seg)
        block[:, 1] = 0.3 * np.sin(2 * np.pi * 1000.0 * seg)
        block[:, 2] = 0.2 * np.sin(2 * np.pi * 250.0 * seg)
        block[:, 3] = 0.1 * np.sin(2 * np.pi * 60.0 * seg)
        blocks.append(block)
    write_wav(path, blocks, sample_rate=P3A_SR, channel_count=4,
              sample_format=WavFormat.FLOAT32, frame_count=total,
              overwrite=True)
    return path


def path_size_check(path: str) -> int:
    return Path(path).stat().st_size


def _p3a_mono_header(d: Path) -> bool:
    from core.audio_wav import WavFormat, write_wav

    p = d / "mono_head.wav"
    info, _spec = write_wav(
        p, [np.zeros((16, 1), dtype=np.float32)], sample_rate=48000,
        channel_count=1, sample_format=WavFormat.PCM16, frame_count=16,
        overwrite=True,
    )
    return (not info.extensible) and info.format_code == 1 \
        and info.bits_per_sample == 16


def _p3b_bus(plan: Any, ids: list[str], gains: Any = None, **kwargs: Any) -> Any:
    from core.audio_mix import MixBusBuilder

    return MixBusBuilder(plan).sum_all(ids, gains=gains, **kwargs)


def _p3a_integer_pcm(d: Path) -> tuple[bool, str]:
    """s16/s24/s32/f32 线性 PCM -> canonical float32, 与 ffmpeg 直读逐样本一致。

    用确定性正弦 (lavfi `sine`) 生成, 避免"素材全静音"掩盖归一化错误。
    """
    from core.audio_models import build_audio_streams
    from core.audio_wav import read_wav

    details: list[str] = []
    all_ok = True
    for codec, wav_fmt in (("pcm_s16le", "s16"), ("pcm_s24le", "s24"),
                           ("pcm_s32le", "s32"), ("pcm_f32le", "f32")):
        src = d / f"int_{codec}.wav"
        rc = sh(FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                "sine=frequency=997:sample_rate=48000:duration=1",
                "-af", "volume=0.5", "-c:a", codec, "-ac", "1", src,
                timeout=600)
        if rc.returncode != 0:
            return False, f"{codec}: fixture 生成失败"
        streams = build_audio_streams(
            ffprobe_json(src).get("streams", []), source_id="pcm"
        )
        plan = _p3a_build_plan([{
            "source_id": "pcm", "path": str(src), "streams": streams,
        }])
        out = d / f"int_{codec}_out.wav"
        res = _p3a_render(plan, out, chunk_frames=4096)
        if not res.ok:
            return False, f"{codec}: render 失败 {res.errors}"
        got, info = read_wav(out)
        ref_path = d / f"int_{codec}.raw"
        rc2 = sh(FFMPEG, "-v", "error", "-y", "-i", src, "-map", "0:a:0",
                 "-vn", "-sn", "-dn", "-f", "f32le", "-ac", "1", ref_path)
        if rc2.returncode != 0:
            return False, f"{codec}: 参考解码失败"
        ref = np.fromfile(ref_path, dtype="<f4")
        exact = bool(np.array_equal(got[:, 0], ref[: info.frame_count]))
        peak = float(np.abs(got).max())
        # 归一化到 [-1,1] 且非静音 (s16 只有 16-bit 精度, sine 峰值本就低)
        all_ok = all_ok and exact and 0.01 < peak <= 1.0
        details.append(f"{wav_fmt}:exact={exact},peak={peak:.4f}")
    return all_ok, " ".join(details)


def _sync_streams(source_id: str, *, samples: int = 48000,
                  sample_rate: int = P3A_SR, channels: int = 1) -> Any:
    from core.audio_models import build_audio_streams

    rate = int(sample_rate or 0)
    duration = f"{samples / rate:.9f}" if rate > 0 else None
    return build_audio_streams([{
        "index": 0, "codec_type": "audio", "codec_name": "pcm_f32le",
        "sample_rate": str(rate), "channels": channels,
        "channel_layout": "mono" if channels == 1 else None,
        "sample_fmt": "flt",
        **({"duration": duration} if duration else {}),
        "nb_frames": str(samples),
        "codec_long_name": "PCM 32-bit floating point",
    }], source_id=source_id)


def _sync_plan(specs: list[dict[str, Any]], *, order: list[str] | None = None,
               samples: int = 48000, sample_rate: int = P3A_SR) -> Any:
    """[{source_id, path, [source_type], [channels]}, …] -> AudioPlan。

    `order` 可重排 **source 加入顺序**, 用于 source-order invariance ——
    channel identity 完全不变。
    """
    from core.audio_models import (
        AudioPlan, AudioSource, AudioSourceType, AudioTrackBuilder,
    )

    plan = AudioPlan()
    tracks: list[Any] = []
    by_id = {s["source_id"]: s for s in specs}
    sequence = list(order or [s["source_id"] for s in specs])
    for sid in sequence:
        spec = by_id[sid]
        channels = int(spec.get("channels", 1))
        streams = _sync_streams(
            sid, samples=samples, sample_rate=sample_rate, channels=channels
        )
        tracks.extend(AudioTrackBuilder(streams, source_id=sid).tracks())
        plan.sources.append(AudioSource(
            source_id=sid,
            source_type=spec.get("source_type", AudioSourceType.MEDIA),
            path=str(spec["path"]),
            streams=streams,
        ))
    plan.input_tracks = tracks
    plan.selected_tracks = [t.track_id for t in tracks]
    plan.selected_channels = [c.id for c in plan.all_channels()]
    return plan


def _sync_reader(plan: Any, work: Path) -> Any:
    """建一个已 `prepare()` 的 production `AudioPCMReader` (解码一次)。

    `estimate_sync` 接受注入的 reader, 因此多次测量可以共用同一份解码结果 ——
    免去每次调用重新走一遍 ffmpeg 解码 (profile 显示解码是绝对大头)。
    注入路径与生产路径是**同一个类**, 不是测试替身。
    """
    from core.audio_models import AudioPlan
    from core.audio_pcm import AudioPCMReader

    reader = AudioPCMReader(
        plan if isinstance(plan, AudioPlan) else plan,
        ffmpeg=FFMPEG, work_dir=work,
    )
    reader.prepare()
    return reader


def _sync_measure(plan: Any, reference: str, *, work: Path,
                  targets: list[str] | None = None,
                  reader: Any = None) -> dict[str, Any]:
    """跑一次真实估计, 返回 offsets / result。

    `reader` 可复用 (见 `_sync_reader`); 不传则内部临时解码一次。
    """
    from core.audio_sync import estimate_sync, plan_from_plan

    sync = plan_from_plan(plan, reference, target_channel_ids=targets)
    if reader is not None:
        res = estimate_sync(plan, sync, reader=reader)
    else:
        res = estimate_sync(plan, sync, ffmpeg=FFMPEG, work_dir=work)
    return {"result": res, "offsets": res.offsets(),
            "errors": [e.get("reason") for e in res.errors]}
