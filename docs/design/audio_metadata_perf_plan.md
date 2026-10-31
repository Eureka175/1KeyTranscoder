# 音频对齐 / 元数据保留 — 性能优化计划

> **状态**：计划（2026-11-01 起草）。**尚未开始实现**；本文只写方案，不跑长测试。
> **基线版本**：v0.8.11（prerelease，tag `v0.8.11` = `4840dd8`）。
> **范围**：① 默认长镜头音频对齐（audio domain / channel-sync）；② 元数据保留
> （透明模式重封装 + fast restore 容器恢复）。
> **原则**：不改变算法、阈值和输出语义；先 profile 再改；每一步可回退；
> 用 C1306（较短）迭代、C1305 做最终验收；**不新增 CLI 开关**。

## 1. 基线（v0.8.11 实测）

| 指标 | C1305（80.1 min） | C1306（69.4 min） | 来源 |
|---|---|---|---|
| 音频域整文件耗时 | **689.9 s** | **389.1 s** | `final_accept_v2.json` |
| long-take 附加耗时 | **231.0 s** | **193.8 s** | 同上 |
| 峰值 RSS（主进程 / 进程树） | **775 MB / 1470 MB** | 同量级 | `rss_probe.json` |
| 透明模式 50 GiB：bundle 抽取 | 28–36 s | — | `transparent_e2e_50g.log` |
| 透明模式 50 GiB：GPAC stage mux | **258.7 s**（0.19 GiB/s，一次写 50 GiB） | 43 GiB 时 162.9 s | 同上 |
| 透明模式 50 GiB：fast restore | **99.1 s**（50 GiB 读+写，合计 I/O ≈1.08 GiB/s） | 43 GiB 时 57.8 s | 同上 |
| 50 GiB 透明端到端（含分析/TV/检查） | **12.8 min** | — | 同上 |

**目标（建议，待你确认）**：

| 指标 | 目标 |
|---|---|
| 音频域整文件 | C1305 ≤ **450 s**（-35%），C1306 ≤ **260 s**（-33%） |
| long-take 附加 | ≤ **130 s**（-35%） |
| 透明 50 GiB 端到端 | ≤ **9 min**（-30%），其中 stage 相关 ≤ 2.5 min |
| RSS | 主进程 ≤ **550 MB**，进程树 ≤ **1.1 GB** |
| 正确性 | 曲线 0/0/0、音频 CH1/3/4 0/0/0、CH2 4 采样预期、确定性、fast-restore 矩阵与 20/20 复核**全部不回归** |

## 2. 当前瓶颈（已知证据）

### 2.1 音频侧

1. **多趟整文件解码**：measure / recheck / residual / apply 各自解码 4×mono PCM；
   80 min 素材单趟 ~2.77 GB（raw s24）或 ~3.7 GB（f32），是主要 I/O。
2. **long-take 计算**：pass-1 2 min 窗 |CC| 锚点 + 7 锚点中值滤波 + clamp；
   round-2 `fine_delay`（60 s 窗 / 20 s 跳、12 k ±80 + 48 k ±8 两级 CC、
   5 min 滑动中位）。实现为 Python 逐窗循环 + numpy FFT/加权中位，单线程
   窗口循环 + 多次 float64 转换/拷贝。
3. **施加写放大**：double-interp（round-1 + round-2）每通道各写一遍整文件
   （10k 网格、5M 块，float32），4 通道即 2×4 次全文件写。
4. **并行结构**：frame-parallel 估计 12 进程、S5 残差窗并行；但长镜头锚点、
   施加、部分固定路径测量仍偏串行；线程/进程过度订阅风险。
5. **内存**：1.44 M 采样锚点段、窗缓冲、多份 f32 copy 同时存在（RSS 775 MB）。

### 2.2 元数据 / 透明侧

1. **GPAC `mux_new` 一次整文件写 50 GiB 需 258.7 s（~0.19 GiB/s）**，是透明路径
   最大单项；GPAC 单线程交织 + 媒体盘顺序写，基本 I/O 受限。
2. **stage 二写**：GPAC 写 stage（50 GiB）→ fast restore 再读 stage + 写最终
   （50 GiB），峰值需要 2×50 GiB 临时空间，且固定多花一次 50 GiB 读写。
3. **bundle 抽取**（28–36 s）在 mux 之前**串行**执行，期间磁盘/CPU 空闲。
4. fast restore 本身已接近盘速（合计 ≈1.08 GiB/s），连续读+写，压缩空间有限；
   主要收益在**去掉 stage 一写**和**重叠抽取**。

## 3. 工作包

### WS1 — 性能基线与 profile（0.5 天）

- 在 `work/` 下写只读 profile 脚本（不改产品代码）：phase timers
  （decode / anchor / residual / apply / write / mux / extract）、cProfile 或
  py-spy（若可用）、psutil RSS 峰值，输出 JSON。
- 用 C1306 做快速迭代基线（~6.5 min/次），C1305 只在关键节点跑。
- 产出：`work/perf/baseline_<date>.json` + top 函数/热点清单。
- 验收：能复现 §1 的耗时/RSS 量级（±10%）。

### WS2 — 音频计算优化（1.5 天）

- CC 计算：统一 `rfft` 长度与预分配缓冲；窗函数预计算；全程 float32
  （FFT 内部按需 float64）；减少 `np.ascontiguousarray`/copy。
- 长镜头 pass-1：锚点窗并行（按窗分块，限制到物理核数）；中值滤波向量化；
  clamp 向量化。
- round-2：窗口循环向量化（批量 FFT / 批量加权中位）；复用 pass-1 的
  解码缓冲，避免重复读同一段 PCM。
- 施加：每通道一个 worker 并行；大块 `np.interp`（或自写线性插值）避免
  中间 float64；输出写用大 buffer。
- 目标：long-take 附加 ≤130 s；内存不升。
- 验收：C1306 输出 raw 与优化前**逐位相同**（音频/曲线 JSON），C1305 最终同。

### WS3 — 音频 I/O 与趟数优化（1.5 天）

- 每通道一次解码 → 复用/缓存在媒体卷的 memmap（或内存预算内缓存），供
  measure / apply / recheck 共享；并行通道解码。
- 评估固定路径 recheck 窗数量是否可减（不降低材料性门槛），以及
  `--audio-plan`/channel-sync 共用解码的可能性。
- 施加 double-interp 的写：评估用 memmap 原地两趟（保持位级一致），避免
  第二份整文件临时输出。
- 目标：整文件 C1305 ≤450 s / C1306 ≤260 s；RSS 主进程 ≤550 MB。
- 验收：`final_accept_v2.py` 全 PASS + 确定性重跑逐位一致。

### WS4 — 元数据 / 透明路径优化（2 天）

1. **重叠 bundle 抽取与 stage mux**：抽取只读 moov/meta（~30 s），可在
   mux 前/并行启动（线程），预期省 28–36 s；风险低。
2. **GPAC stage mux 调参**：系统测量 `-inter` / `-timescale` / `-flat` /
   不同的 `-new` vs 分步 adds 对 50 GiB 写的影响，取最优参数（若有收益）。
3. **stage-less 直写原型**（大改，单独 gate）：
   - 直接读源 mdat 的 video/rtmd 范围 + 音频 MOV，按最终 moov 的 chunk 顺序
     一次性写成最终 mdat；
   - 复用 `fast_restore` 的 moov 重建逻辑（uuid/tref/ipcm 条目/stco 平移）；
   - 预期省一次 50 GiB stage 写 + 50 GiB 临时空间，50 GiB 端到端可降
     ~4 min（从 12.8 min → ~9 min 量级）；
   - 风险：播放器/NLE 对 chunk 交织、offset、持续时间的要求；必须通过
     7 源矩阵 + 50 GiB E2E + 结构/payload 复核后才替换。
4. 其它：bundle 抽取用 GPAC 只读命令是否可并发/精简；checker 的只读检查
   是否可并行（收益小）。

### WS5 — “全量 fast 路径、不留两条路子”评估（0.5–1 天，B11）

- 现状：fast restore 在 stage ≥2 GiB 自动启用，小文件走标准 GPAC 多轮流程
  （等价性测试需要它做对照）。
- 评估步骤：
  1. 强制 fast 模式跑 L3 透明相关用例 + 7 源矩阵 + 50 GiB E2E；
  2. 若全部通过（结构/payload/元数据/时长/播放器检查），删除标准多轮
     restore、`FAST_RESTORE_MIN_BYTES` 阈值和等价对照路径，仅保留
     “fast 失败 → 源文件拷贝”这一失败安全分支；
  3. 更新单元/集成测试与文档（`transparent_remux_fast_restore.md`、README）。
- Gate：任何一项不通过则保留现路径，并把缺失证据记录为已知限制。

### WS6 — 回归与验收（1 天，**按你的要求暂不跑长测试**，留待下一轮）

- unit（1020/2 环境项）→ 全量 L3（1261/12 环境项）→ fast 矩阵/50 GiB E2E
  → C1306/C1305 `final_accept_v2` → 确定性/包 smoke。
- 产出：前后对比表（耗时/RSS/正确性）、checkpoint 更新、发布说明。
- 长测试只在实现稳定后集中跑一次。

## 4. 里程碑与顺序

| 里程碑 | 内容 | 出口条件 |
|---|---|---|
| M0 | WS1 profile 基线 | 复现 §1 数据 ±10% |
| M1 | WS2 计算优化 | C1306 raw/曲线逐位不变，long-take -20% 以上 |
| M2 | WS3 I/O 优化 | C1305/C1306 达到耗时目标，RSS 目标 |
| M3 | WS4 抽取重叠 + GPAC 调参 | 50 GiB E2E -30 s 以上 |
| M4 | WS4 stage-less 原型 | 结构/payload 全 PASS，决策是否替换 |
| M5 | WS5 fast-only 评估 | 若通过则删双路径并更新测试/文档 |
| M6 | WS6 回归/验收（长测试） | 全部 PASS + 前后对比报告 |

**建议顺序**：WS1 → WS2 → WS3（先音频，收益确定）→ WS4（抽取重叠低风险先做，
stage-less 原型单独立项）→ WS5（可与 WS4 并行评估）→ WS6。

## 5. 风险与约束

- **位级一致性**：优化只能用等价数学（向量化/并行/减少拷贝），不得改变
  插值/滤波/阈值语义；每个 WS 结束对比优化前后 raw 音频与曲线 JSON 的
  sha256（C1306/C1305）。
- **并行度**：限制到物理核数，避免 FFT 进程与编码进程抢占；测量 I/O 与 CPU
  时间分离。
- **磁盘**：stage-less 原型需要额外实验盘空间；失败必须可回退到现路径。
- **GPAC 行为**：stage mux 耗时受磁盘缓存/后台 IO 影响，需多次测量取中位。
- **不改变发布语义**：优化结果进入下一版本（A6 未定），不修改 v0.8.11
  prerelease 资产。

## 6. 本次已确认的决策（供实现时遵守）

| 编号 | 决定 | 状态 |
|---|---|---|
| A1 | 保持 v0.8.11 为 prerelease | 已确认，不转正 |
| A2 | README 说明“输入应为相机原始素材；对已修复成品再转码会触发结构时长检查” | 已确认（见 README 限制节） |
| A3 | 不必要的测试产物删除 | 已执行（F: 回收至 ~255 GB 空闲） |
| A4 | untracked 文档目录不动 | 已确认 |
| A5 | 真实媒体已替换为修复后成片，A1 语料暂不可得 | 保持 hold-out 记录 |
| A6 | 下一版本号/主题暂不决定 | 待定 |
| B1 | remux 失败终态改为 `verify_failed` | 已确认，作为独立小改动（非性能范围）排入下一轮 |
| B11 | 评估“全量 fast 路径、不留两条路子” | 纳入 WS5，先测试后决定是否删旧路径 |
| 其他 B 项 | 不做决定，保持现状 | — |

## 7. 下一步（本轮结束后的第一件事）

1. 按 B1 先做 `verify_failed` 小改动（独立于性能，1 个文件 + 测试）。
2. 按 WS1 写 profile 脚本，产出 C1306 基线 JSON（不跑长测试）。
3. 拿到基线后再改 WS2 代码；每次改动都做 C1306 raw/曲线逐位对比。
4. 长测试（L3/两条 80 min 验收）留到实现稳定后**集中一次**跑。
