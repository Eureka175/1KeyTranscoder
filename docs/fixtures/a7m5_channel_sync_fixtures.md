# A7M5 通道同步固定 fixture 清单（P1）

> 依据 P1 任务书 §19.2：固定 fixture 清单 + 测试说明。**原始大文件不提交
> 进仓库**，仅记录路径引用与期望标签；物理位置关系只作为测试标签，不进入
> 算法（P1 不做声学传播差建模，也不把 delay 解释为设备 latency）。

## 素材来源

- 机型: Sony A7M5（当前第一阶段实测素材以短素材为主）
- 布局: 4CH 音频（无线麦 CH1/CH2 + 有线参考 CH3/CH4）
- 素材根目录（本机路径引用, 不入库）: `D:\素材\A7M5\channel_sync_fixtures\`

## 清单

| ID | 原始文件（路径引用） | 布局 | 空轨 | 物理位置标签 | 预期决策/结果 |
|---|---|---|---|---|---|
| A7M5-01 | `A7M5-01*.MP4` | 4×mono PCM 48k | 无 | CH1/CH2 与参考同位置 | CH1/CH2 fixed; CH3 anchor; CH4 already_aligned/fixed |
| A7M5-02 | `A7M5-02*.MP4` | 4×mono PCM 48k | CH1 空 | — | CH1 untouched(silent_track); CH2 独立同步 |
| A7M5-03 | `A7M5-03*.MP4` | 4×mono PCM 48k | CH2 空 | — | CH2 untouched(silent_track); CH1 独立同步 |
| A7M5-04 | `A7M5-04*.MP4` | 4×mono PCM 48k | CH1/CH2 均空 | — | CH1/CH2 untouched; CH3 anchor; CH4 正常; 任务不得失败 |
| A7M5-05 | `A7M5-05*.MP4` | 4×mono PCM 48k | 无 | CH1 与参考不同位置 | 相关性足够 → 作为"音轨时间对齐"仍可 fixed; 相关性不足 → untouched(low_confidence) |
| A7M5-06 | `A7M5-06*.MP4` | 4×mono PCM 48k | 无 | CH2 与参考不同位置 | 同 A7M5-05 语义 |
| A7M5-07 | `A7M5-07*.MP4` | 4×mono PCM 48k | 无 | CH3/CH4 同位置 | CH3 anchor; CH4 高相关、固定时差 → fixed |
| A7M5-08 | `A7M5-08*.MP4` | 4×mono PCM 48k | 无 | — | 全部已对齐 → already_aligned, 原音频不动 |
| A7M5-09 | `A7M5-09*.MP4` | 4×mono PCM 48k | 无 | — | 不同实际固定 delay（含近窗边缘值）→ 各自 fixed, shift 与复检残差落报告 |
| A7M5-10 | `A7M5-10*.MP4` | 4×mono PCM 48k | 无 | 低相关/不可测 | 受影响轨 untouched(low_confidence/insufficient_frames), 不得错修 |

## 每 fixture 必须记录的字段

```text
原始文件名 / 音频流布局 / 预计健康轨 / 是否空轨 /
麦克风物理位置关系（仅测试标签, 不进入算法）/ 实测 delay /
是否预期 fixed / 预期 JSON decision + reason
```

## 验证动作（对每个 fixture）

1. 直接跑 `run_channel_sync`（或 `1kt.py --channel-sync --keep-work`），
   读取 `channel_sync_<名>.json`:
   - `decision/reason` 与预期一致（尤其 untouched 轨的理由）;
   - fixed 轨 `shift_samples` 为整数、`fine_delay_ms` 为诊断读数;
   - 报告 `anchor_stream` 与候选回退一致（CH3 空时自动落到 CH4）。
2. 修正后复测（对输出再跑一次同步）: fixed 轨 → `already_aligned`
   （残差 < `aligned_max_ms`），untouched 轨保持原样。
3. transparent 模式（`--channel-sync-transparent`）:
   - 视频/非音频流与源一致（ffmpeg `streamhash` MD5）;
   - untouched 轨 f32 逐字节与源一致;
   - 全部已对齐时输出 SHA256 与源相同;
   - 文件级失败时输出 = 源文件原样拷贝。
4. 空轨场景（A7M5-02/03/04）: 空轨不得阻止其它健康轨同步; 只有"无任何
   健康 anchor"才允许 `measure_failed / no_valid_anchor`。
5. 不同物理位置（A7M5-05/06）: 验证系统**不**把结果标成设备 latency;
   信号可相关时允许作为"音轨时间对齐"修正; 相关性不足时安全放弃该轨。

## 短期/低 SNR 边界说明

- 过短轨（< `min_audio_seconds`）→ 轨道级 `insufficient_frames`，不阻止
  其它轨; 若因此无法形成有效同步任务 → `measure_failed`。
- 短素材 + 静音段多的素材: 帧轨迹证据覆盖率 < `min_usable_fraction`
  (0.6) 时触发宽窗复测; 宽窗仍不可靠 → `low_confidence`（宁可少修）。

## 记录位置

- 每 fixture 实测结果追加到 `logs/channel_sync_fixture_results.md`
  （不入库亦可, 或按需提交结论摘要）。
- 阈值标定结论回流到 `core/channel_sync.py::DEFAULTS`（所有阈值标注
  "初值, 待真实素材标定"，不得把初值描述成充分标定的最终值）。

## 实测结论（20260903 + 20260904，137 段 A7M5 真实素材）

扫描工具: `work/sweep_real_sync.py`（进程内 `run_channel_sync`，纯音频
算法，源文件只读）→ `work/real_data_sync/summary.csv`；分析:
`work/analyze_real_sync.py` → `analysis.md`。

| 状态 | 段数 | 说明 |
|---|---|---|
| already_aligned | 111 | 无轨需要修正（含空轨/弱相关轨 untouched） |
| applied | 11 | 修正量 905–1222 样本 = 18.9–25.5 ms（与实测无线麦延迟量级一致） |
| measure_failed | 15 | 四轨全静音 5 + 无有效锚点 3 + 目标轨全部不可靠 7 |

- **修正正确性**: 11/11 修正轨算法复测 vs 锚轨，残差 **0.00 样本**。
- **不误修**: 已对齐轨未被误改；真实慢漂移轨（spread 0.6–2.2 ms、
  34–76 ppm，轨迹单调）与弱相关轨（全窗最大归一化相关 0.09–0.46）
  均安全拒修（`non_constant` / `low_confidence`）。
- 因真实素材产生的标定: codec 白名单大小端全支持（A7M5 实为大端
  s24be）、漂移材料性门 `drift_min_ms`、reason 区分
  （`low_confidence` vs `insufficient_frames`）。
- 待办（P2 范围）: 素材内部 0.4–2 ms 量级的慢漂移需 resample 处理；
  P1 明确不做，漂移轨保持原音频。
