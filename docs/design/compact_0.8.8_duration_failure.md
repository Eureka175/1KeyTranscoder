# Compact 批处理 0.8.8 失败分析

日期：2026-11-04
对象：`I:\compact` → `I:\compact_out`，压缩基线 **1KeyTranscoder 0.8.8**（x265 HQ）

## 1. 结果概览

| 项 | 数量 |
|---|---:|
| 输入素材总数 | 339 |
| 失败 | 80 |
| `failed_files.json` 中 `stage=encode` | 80 |
| 结构校验失败（duration MODIFIED） | 78 |
| 磁盘写满失败（No space left） | 2 |

失败的 78 个文件全部是同一类差异：

- `original - final = **30**` 或 `final - original = **30**`；
- movie timescale = 60000，**30 ticks = 0.5 ms**；
- 视频 track duration：`450450 → 450480`（+0.5 ms）
- 音频/rtmd track duration：`450450 → 450420`（-0.5 ms）
- `video.elst`、`audio.tracks`、`rtmd.elst` 同步变化；
- 没有 MISSING，最终报告为 `PRESERVED=30 MODIFIED=7`。

## 2. 根因

已用 0.8.8 实跑 C1035 并保留工作目录复现，检查中间文件：

```
video/encoded.mov: Movie Info - TimeScale 1000, Duration 7.508
  Track 1 (video): Media Duration 7.507, track duration 7.508
final/output.mov: Movie Info - TimeScale 60000, Duration 7.508
  video track duration 7.508
  audio/rtmd track duration 7.507
```

源文件：

```
Movie TimeScale 60000
video/audio/rtmd track duration = 450450 = 7.5075 s
```

机制：

1. x265/FFmpeg 生成的中间 `encoded.mov` 的 `mvhd` timescale 是 **1000**；
2. GPAC `MP4Box -new/-add` 重建时按 **1 ms** 量化每个 track 的 duration；
3. 源时长为 7.5075 s 时：
   - 视频中间体是 7.508 s → final 450480（+30）；
   - 源音频/rtmd 的 7.5075 s 被量化为 7.507 s → final 450420（-30）；
4. 0.8.8 的软件 x265 Sony 路径传入 `fix_hw_timing=False`，只在硬件后端
   路径调用 `patch_track_durations(from_stts=True)` /
   `patch_movie_duration`，所以没有修复这 0.5 ms；
5. `preservation.validate.compare` 对 timeline/track duration 做精确比较，
   于是判定 structural_success=False，文件进 failed。

只影响“源时长不是整数毫秒”的文件；整数毫秒素材（如 28.028 s、
6.006 s）四舍五入前后一致，所以正常通过。

## 3. 已验证的修复

对 0.8.8 的失败 final 直接调用当前 `isobmf`：

```
trak:vide: tkhd duration 450480 -> 450450
trak:vide: elst duration 450480 -> 450450
trak:soun: tkhd duration 450420 -> 450450
trak:soun: elst duration 450420 -> 450450
trak:meta: tkhd duration 450420 -> 450450
movie: mvhd duration 450480 -> 450450
```

再跑 `validate.compare`：`structural_success=True`，`MODIFIED=0`。

当前代码修复：

- `1kt.py` Sony 软件路径：`fix_hw_timing=True`
- `1kt.py` DJI 软件路径：`fix_hw_timing=True`
- `preservation/pipeline.py` 注释更正：FFmpeg MOV 的 1000 timescale 同样
  需要 stts 修复，不再只写 hardware。

实跑验证（当前代码）：

| 文件 | 修复前 | 修复后 |
|---|---|---|
| 20260901_C1035.MP4 | structural_success=False | **PRESERVED=37 MODIFIED=0 PASS** |
| 20260918_C1263.MP4 | structural_success=False | **PRESERVED=37 MODIFIED=0 PASS** |

## 4. 另外 2 个失败

`20260919_C1305.MP4`、`20260919_C1306.MP4`：

```
MP4Box Error writing data (No space left on device)
Failure while importing media: I/O Error
Error importing ..\video\encoded.mov#video: I/O Error
```

最终 mux 时输出盘写满，不是时长 bug；释放磁盘后重跑即可。

## 5. 结论 / 重跑建议

- 78 个结构失败是 0.8.8 的 **GPAC 毫秒量化 + 软件路径未做 stts 时长修复**；
- 当前代码修复后，抽样的两个失败文件均 PASS；
- 使用修复后的版本重跑这 78 个文件即可（原工作目录/中间体已清理，需要重新编码）；
- C1305 / C1306 先确保目标盘有足够空间再重跑；
- 不需要手工 patch 已有成品（失败的成品没有进入 compact_out）。
