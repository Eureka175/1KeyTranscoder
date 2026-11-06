# GPU / channel-sync 性能报告

日期：2026-11-07
分支：`feature/gpu-pipeline-v2`
HEAD：`2abd196`
环境：

- GPU：NVIDIA GeForce RTX 5070 Laptop GPU，8151 MiB
- 驱动：616.92
- OS：Windows 10.0.26300
- Python：3.11
- 测试数据：K:\1kt_moved_20261026\testsets\a7m5_4ch_long
  - C1305：80 min 级 4ch 长素材
  - C1306：80 min 级 4ch 长素材

## 1. 完整测试结果

### L3 full（2026-11-07 01:28–01:47）

- 结果：**1294 PASS / 12 FAIL**
- 总耗时：**1168.4 s**
- 报告：`work/autotest/autotest_report.json`

### L3 full（优化后，2026-11-07 03:24–03:43）

- 结果：**1295 PASS / 11 FAIL**
- 总耗时：**1171.0 s**
- 多出的 1 PASS 是 DJI Action fallback 测试修复；
- 11 个 FAIL 全部仍是真实语料缺失，没有新增代码失败。

失败分类：

| 类别 | 数量 | 说明 |
|---|---:|---|
| Sony 真实语料缺失 | 3 | F:\testsets 下 Sony 素材不存在 |
| A7M5 4CH 真实语料缺失 | 6 | F:\testsets 下 A7M5 4CH 素材不存在 |
| classic fixture 缺失 | 1 | 由 Sony 素材缺失级联导致 |
| DJI 测试 fallback 误用非 Action 样本 | 1 | 已修复测试：Action 样本改为 K: 真实 Action 4 文件 |
| 其余真实素材缺失 | 1 | pipeline C9037 不存在 |

> 12 个 FAIL 都是**语料/环境缺失或测试 fallback 问题**，未发现代码路径新损坏。
> 修复 DJI 测试 fallback 后，`--level toolchain` 为 **1074 PASS / 2 FAIL**；
> 剩余 2 个仍是 Sony 语料缺失。

### 可执行测试

| 级别 | 结果 |
|---|---|
| unit | 1053 PASS / 2 FAIL（既有语料环境） |
| toolchain（修复 DJI fallback 后） | 1074 PASS / 2 FAIL（same） |
| full | 1294 PASS / 12 FAIL（仅语料缺失/测试问题） |

## 2. 性能基准

方法：

- 每个 case 跑 2 次；
- 同机、同 GPU、连续执行；
- 记录 wall、GPU active、window_measure、residual_measure、audio 哈希；
- pipeline=1 使用 `KTS_GPU_UNIT_PIPELINE=1`；
- fixed GPU budget / fresh FFT plan / single GPU lock / residual workers4 全开。

| case | run | wall (s) | GPU active | window (s) | residual (s) |
|---|---:|---:|---:|---:|---:|
| C1306 no-pipeline | 1 | 109.16 | 6.96% | 29.23 | 16.09 |
| C1306 no-pipeline | 2 | 107.71 | 7.00% | 31.79 | 16.05 |
| C1306 pipeline | 1 | **105.58** | 7.66% | 31.57 | 15.49 |
| C1306 pipeline | 2 | **105.07** | 7.76% | 33.46 | 15.49 |
| C1305 no-pipeline | 1 | 196.60 | 4.51% | 20.34 | 21.16 |
| C1305 no-pipeline | 2 | 195.62 | 4.97% | 20.60 | 23.12 |
| C1305 pipeline | 1 | 200.84 | 5.99% | 20.25 | 24.13 |
| C1305 pipeline | 2 | 197.76 | 5.73% | 20.77 | 23.33 |

中位数：

| case | median wall | 相对 no-pipeline |
|---|---:|---:|
| C1306 no-pipeline | 108.44 s | — |
| C1306 pipeline | **105.32 s** | **-2.9%** |
| C1305 no-pipeline | **196.11 s** | — |
| C1305 pipeline | 199.30 s | **+1.6%** |

## 3. 结果分析

### 3.1 GPU active 很低

- C1306：6.9–7.8%
- C1305：4.5–6.0%

GPU kernel 只占 wall 的很小部分；window+residual 合计：

- C1306：约 45–49 s；
- C1305：约 41–45 s。

补充实测：long-take 单个 target 的 window measurement 只有 **约 3 s**
（C1306 CH3 3.14 s / CH4 2.94 s）；`window_measure` 阶段的大头是
channel-sync 的锚点/候选对估计，而不是 long-take 内逐 target 测量。

剩余 60–150 s 在解码、音频 apply、曲线/调度、I/O 等 CPU 阶段。

### 3.2 pipeline 的收益依赖素材

- C1306：pipeline 快约 **3.1 s（-2.9%）**；
- C1305：pipeline 慢约 **3.2 s（+1.6%）**；
- 净收益不明显，但 pipeline 的 GPU active 更高，说明 overlap 生效；
- C1305 上 CPU apply/processing 与 measurement 竞争，抵消了 overlap。

### 3.3 发现默认路径的 parity 风险

C1305 no-pipeline 第 2 次运行：

```text
audio_2 = 28870a66...（基线 d7228acc...）
```

而同一组 C1305 pipeline 两次运行都是基线。

这说明：

- **当前 no-pipeline 默认路径仍存在间歇性非确定性**；
- pipeline 的 canonical prewarm 使 residual 计划状态固定，反而更稳定；
- 但 pipeline 在 C1305 上更慢，所以不能直接作为“纯性能优化”默认。

## 4. 性能优化尝试与决策

### 4.0 channel-sync estimation worker cap

C1306（同一晚，单次）：

| `est_worker_cap` | wall |
|---:|---:|
| 8 | 108.64 s |
| 12（当前默认） | 103.93 s |
| 16 | **101.44 s** |

C1305：

| `est_worker_cap` | wall |
|---:|---:|
| 12（当前默认） | 181.30 s |
| 16 | 184.83 s |

结论：

- cap16 对 C1306 更快，但对 C1305 更慢；
- 当前默认 cap12 是综合更稳的点；
- 不把 cap16 默认化。

已尝试：

1. **adaptive pipeline**（按首个 unit 测量耗时选择）
   - 首个 unit 仅约 3.1 s，阈值无法区分 C1305/C1306；
   - 放弃。
2. **pipeline 默认化**
   - C1306 更快，但 C1305 更慢；
   - 且 pipeline 有额外 prewarm 成本；
   - 暂不默认。
3. **residual workers8**
   - 单独 residual 更快，但完整入口曾出现哈希变化；
   - 不采用。
4. **coarse workers8**
   - C1306 wall 110.8 s，与默认接近，无收益；
   - 不采用。

当前优化结论：

> 瓶颈不在 GPU kernel，而在 CPU 阶段的 GPU idle；  
> pipeline 能 overlap，但对 C1305 是负收益；  
> 默认路径仍存在 C1305 间歇性 parity 问题，优先于纯性能。

## 4.1 已知性能瓶颈排序

1. channel-sync 帧级估计（cap12 已接近最优，cap16 不通用）
2. 音频 apply / decode / I/O（GPU active 仅 4–8%）
3. long-take target processing 的 CPU 部分
4. GPU window/residual kernel 本身（占比小）

## 5. 下一步（优化前）

1. **修复 C1305 no-pipeline 间歇性非确定性**
   - 与 pipeline 共用 canonical plan prewarm；
   - 或确定 residual engine 创建顺序。
2. 在该修复后重新评估 pipeline 默认化；
3. 若 pipeline 稳定，则按素材/耗时自适应选择；
4. 继续优化 CPU/GPU overlap，而不是继续调 GPU kernel。

## 6. 优化落地（本轮完成）

### 6.1 并行音频 apply

`reference_interp_write_inplace()` 的 chunk 处理改为线程池：

- `KTS_APPLY_WORKERS` 控制，默认在 pipeline 模式下为 **4**；
- pipeline 关闭时默认回到 **1**，避免 no-pipeline + 并行 apply 的 parity 风险；
- 每个 chunk 独立读/写自己的文件区间；
- 合成 23M 样本 bitwise 测试：workers1 与 workers4 输出 **SHA256 完全相同**。

### 6.2 正式启用 pipeline

- `KTS_GPU_UNIT_PIPELINE` 默认从 0 改为 **1**；
- pipeline 的 canonical window-plan prewarm 保证 residual 计划顺序；
- apply workers4 只在 pipeline 模式下默认开启。

### 6.3 结果

| 场景 | 旧默认 | 新默认 | 变化 |
|---|---:|---:|---:|
| C1306 | 108.44 s | **98.20 / 98.75 / 99.69 s** | 中位约 **-8.9%** |
| C1305 | 196.11 s | **178.76 / 183.83 s** | 中位约 **-7.5%** |

- C1306 pipeline+apply4：2 次，哈希全部基线；
- C1305 pipeline+apply4：2 次，哈希全部基线；
- C1306 默认（不设 env）：99.69 s，哈希基线；
- 默认 commit 前的 no-pipeline + parallel apply C1305 曾出现 audio_3 变化，因此默认 apply4 只在 pipeline 模式下启用。

### 6.4 当前默认

```text
KTS_GPU_UNIT_PIPELINE = 1
KTS_APPLY_WORKERS      = 4 (pipeline 模式默认；pipeline off 时默认1)
est_worker_cap         = 12
fixed GPU budget       = on
fresh FFT plan         = on
residual workers       = 4
```

## 7. 最终结论

本轮得到了可交付的净性能提升：

- **C1306 约 -8.9%**
- **C1305 约 -7.5%**
- 所有验证运行 audio 哈希与基线一致。

主要收益来自：

1. 并行音频 apply（chunk 级线程池）；
2. pipeline 与 canonical plan prewarm 使 apply 并行保持 parity；
3. 没有改变 GPU kernel 或输出语义。

## 6. 测试修正

- `tests/selftest/suites/dji_metadata.py`
  - Action 样本 fallback 不再错误使用 Mavic 非 Action 样本；
  - 增加 K: 真实 Action 4 样本作为 fallback；
  - 修复后 `--level toolchain` 为 **1074 PASS / 2 FAIL**（剩余 2 个是 Sony 语料缺失）。
