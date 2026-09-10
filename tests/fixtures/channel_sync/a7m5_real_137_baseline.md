# A7M5 真实素材 137 段 — 通道同步 P1 冻结基线

## 数据来源

- 源素材: `testsets/20260903` + `testsets/20260904` (Sony A7M5, XAVC-S MP4/MOV,
  音频 pcm_s24be 48 kHz 4×mono)。**testsets/ 全程只读**。
- 逐文件报告: `work/real_data_sync/<stem>/channel_sync_<stem>.json`
  (由 `work/sweep_real_sync.py` 进程内调用 `core.channel_sync.run_channel_sync`
  产生; 纯音频算法, 无视频编解码)。
- 本 CSV: `work/freeze_real_137_baseline.py` 从上述 JSON + ffprobe 汇总而来。
- 逐文件原始 CSV: `work/real_data_sync/summary.csv`;
  分析: `work/real_data_sync/analysis.md`。

## 本次运行汇总

- 文件 137 段, 逐轨行 548 行 (每段 4 条单声道音轨)。
- status: `already_aligned`=111, `measure_failed`=15, `applied`=11
- decision: `untouched`=290, `anchor`=127, `already_aligned`=120, `fixed`=11
- reason: `silent_track`=176, `non_constant`=52, `low_confidence`=43, `insufficient_frames`=9, `no_valid_anchor`=8, `recheck_residual`=2
- 修正量 shift_samples: -39 – 1222; delay_ms: -0.8125 – 25.4583
- 修正后复测残差: 11 轨测量, 精确 0.00 = 11
- ffprobe 失败: (none)

## 列映射 (CSV 列 -> JSON 字段)

| CSV 列 | 来源 / 语义 |
|---|---|
| `file` | 报告 `source` 相对 `testsets/` 的正斜杠路径, 如 `20260903/A7M5/20260903_C1154.MP4`; 稳定相对标识, 绝非绝对路径 |
| `sample_rate` | ffprobe 该文件音频流 `sample_rate` (所有音频流一致时取值) |
| `codec` | ffprobe 音频流 `codec_name` (一致时取值, 否则以 `|` 连接) |
| `stream_count` | ffprobe 探测到的**音频流数量** (非全部流数量) |
| `anchor_stream` | 报告 `anchor_stream` (0 基流索引) |
| `decision` | 轨道 `channels[i].decision` (`anchor`/`already_aligned`/`fixed`/`untouched`) |
| `reason` | 轨道 `channels[i].reason` (无则空) |
| `delay_ms` | `channels[i].delay_ms` (锚轨为 0.0) |
| `delay_samples` | `channels[i].delay_samples` |
| `confidence` | `channels[i].confidence` |
| `rms_dbfs` | `channels[i].rms_dbfs` |
| `traj_drift_ms` | `channels[i].traj_drift_ms` (轨迹总漂移) |
| `drift_ppm` | `channels[i].drift_ppm` (线性漂移斜率) |
| `constant` | `channels[i].constant` (恒定门判定结果) |
| `usable_frames` | `channels[i].usable_frames` |
| `shift_samples` | `channels[i].shift_samples` (整样本修正量, 仅 `fixed` 轨非空) |
| `residual` | 修正后复测残差 (样本)。复测方式与 `work/analyze_real_sync.py` 一致: 解码 `audio_<anchor>.mov` 为参考、`audio_<track>.mov` 为目标, 调 `core.sync_estimate.estimate_pair` (48 kHz, 窗 80 ms, 帧 200 ms, 跳 100 ms, 锚段 30 s, min_conf 0.3)。**仅 `applied` 文件的 `fixed` 轨有值, 其余空** |

## 空值约定 (重要)

- 缺失/未测量一律留**空字符串**, 绝不写 0 —— 0 会被误读为"测到了 0"。
- 具体: JSON 中为 `null` 的字段直接留空。
- 另外, 当 `delay_ms` 为 `null` (该轨根本没有有效测量, 如 `silent_track`
  / `insufficient_frames` / `no_valid_anchor`) 时, 上游 `plain_row` 写入的
  `confidence=0.0` / `drift_ppm=0.0` / `constant=false` 是**代码默认占位值
  而非测量结果**, 故本 CSV 中这三列同样留空。
- 锚轨 (`decision=anchor`) 的 `delay_ms=0.0` / `drift_ppm=0.0` /
  `constant=true` 是定义值 (锚 vs 自身), 保留; 其 `traj_*` /
  `usable_frames` 上游为 `null`, 留空。
- `constant` 列原样输出 Python 布尔 (`True`/`False`)。
