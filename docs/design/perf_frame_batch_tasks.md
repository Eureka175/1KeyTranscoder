# 任务清单：run 级共享 coarse 缓存 + 单进程 GPU 帧批处理（v0.9.0 后可选优化）

> **状态**：**进行中**（2026-11-01，分支 `perf/frame-batch-gpu`）。
> 已完成：T0.2 黄金数据采集（C1306，18 次 estimate 调用）、T1.2 缓存
> builder、T1.5 缓存原型（opt-in `KTS_COARSE_CACHE=1`，C1306 实测
> 217.0→200.8 s、逐帧/最终 payload parity PASS）、T2.1 GCC 数学拆分
> （600 帧真实数据 0 mismatch）。T2.2+ 未开工。
> 原状态：未开工（规划）。仅当明确批准后执行。
> **目标**：把帧级 `estimate_pair` 从"12 进程 CPU 并行 + 逐帧 decimate"改为
> "run 级共享 8 kHz coarse 缓存 + 单进程 GPU 批处理"，预期 C1305
> 204.1 → **~150–160 s**、C1306 148.0 → ~110–120 s。
> **硬约束**：默认关闭（opt-in）；默认路径输出与 v0.9.0 **逐位一致**（最终
> audio MOV sha256）；CuPy/CUDA 不可用时自动回落现有 CPU 路径。
> **风险**：高（峰逻辑重写 + 跨进程共享 + cuFFT 数值）；每个检查点都有
> no-go 门。

## 0. 决策与基线（先做，1 个短会话）

| ID | 任务 | 验收/产出 |
|---|---|---|
| T0.1 | 冻结 parity 合同 | 明确"成功"定义：C1305/C1306 最终 audio MOV sha256 相同 + `final_accept_v2` PASS + `parity_check` report 等价；`FrameDelay` 尽量逐位相同但不作为唯一门 |
| T0.2 | 采集黄金数据 | 对 C1305/C1306 的每一次 `estimate_pair`（含换参考重试）记录：ref/tgt 通道、几何、`frames` 列表、`PairEstimate` 字段、report 行；存 `K:\1kt_work\perf\ws13\golden\`，供后续逐帧对比 |
| T0.3 | 细粒度计时基线 | 每个 pair 的 wall 与阶段拆分（逐帧 decimate、coarse GCC、full GCC、峰逻辑、IO）；记录 CPU 204.1/148.0 s、GPU 窗口路径的 VRAM/RSS/util |
| T0.4 | 设定预算与 no-go 门 | C1305 ≤160 s、C1306 ≤120 s；RSS ≤2.5 GB、VRAM ≤2 GB；任一 parity 门失败即停在当前版本并归档 |

**检查点 G0**：黄金数据可复现、计时拆分能解释 ≥90% 的估计耗时。

## 1. Run 级共享 coarse 缓存（CPU/共享内存侧）

| ID | 任务 | 验收/产出 |
|---|---|---|
| T1.1 | 缓存契约 | 每通道 8 kHz `float64` 全量数组；采样位置、文件外 zero-pad、与 `_coarse_slice` 的**逐位等价边界规则**（首尾帧回退）写成文档/测试 |
| T1.2 | builder 原型 | 分块 decimate（带 margin）+ 与逐帧 `_coarse_slice` 对比；C1305 每通道构建 ≤3 s、4 通道 ≤12 s；全帧/边界对比 0 mismatch |
| T1.3 | 存储与共享方案 | 选型：`multiprocessing.shared_memory`（推荐）vs 只读 memmap（临时文件，注意写盘约束）；Windows spawn 兼容、崩溃清理、`--jobs` 并发上限 |
| T1.4 | channel_sync 集成 | decode/detect 后按需构建 cache（仅 opt-in）；生命周期与异常清理；与 `--jobs`、断点续跑、透明模式的兼容 |
| T1.5 | sync_estimate 接口改造 | `_est_frames` 接受 cache 切片；`_chunk_job` 通过共享句柄切片；首尾帧回退 `_coarse_slice`；帧切分/顺序/结果不变 |
| T1.6 | 缓存测试 | synthetic + 真实素材逐位对比；cache on/off 结果一致；内存泄漏/共享内存残留测试；unit 全绿 |

**检查点 G1**：cache 构建成本 ≤12 s，逐帧输出与 v0.9.0 **逐位一致**；若构建成本 >20 s 或边界 mismatch 不可消除 → 降级为"仅单进程缓存（不跨进程）"或 no-go。

## 2. 单进程 GPU 批处理帧估计

| ID | 任务 | 验收/产出 |
|---|---|---|
| T2.1 | GCC 数学拆分 | 把 `gcc_phat_int` 拆成"生成 surface" + `_peak_from_surface` 纯函数；CPU 重构先证明所有帧输出逐位不变 |
| T2.2 | GPU surface 批处理 | 批量 gather（coarse 阶段；full-rate 阶段）→ cuFFT float64 batched rfft/irfft → normalize；batch 大小适配 8 GB VRAM；释放/复用策略 |
| T2.3 | 峰逻辑重做 | 复刻 `find_peaks(distance=2)` / `peak_prominences` / confidence 公式；**先 CPU 逐帧循环吃 GPU 输出**（保 parity），再评估向量化（NumPy/GPU）；逐帧与 v0.9.0 `FrameDelay` 对比，记录差异分类 |
| T2.4 | 估计执行器 | opt-in `gpu_est` 下用单进程批处理替换 12 进程池；维护 pair 调用顺序/换参考语义；CUDA context/流安全；OOM/无 GPU 自动回落 CPU |
| T2.5 | 数据搬运优化 | memmap 读取、批量/双缓冲、pinned memory、避免重复 host 拷贝；测量 IO 占比 |
| T2.6 | 单元/黄金测试 | 所有帧逐位相等（或差异仅限不影响决策/最终 MOV 的字段）；`PairEstimate`/report/决策一致；determinism 两跑一致；CPU 路径不受影响 |

**检查点 G2**（关键）：单进程 GPU 批处理本身（含缓存）在 C1305 上 ≤175 s 且逐帧 parity；若单进程串行峰逻辑导致 >200 s → 放弃"单进程"或改为"GPU surface + 多进程峰逻辑"再评估。

## 3. 集成、性能与验收

| ID | 任务 | 验收/产出 |
|---|---|---|
| T3.1 | 拓扑定稿 | 帧估计单进程 GPU + 现有窗口/残差 GPU 路径共存；VRAM/上下文/线程安全；fallback 矩阵 |
| T3.2 | A/B 基准 | cache-only CPU、GPU-only、cache+GPU 三组，在 C1305/C1306 上的 wall/RSS/VRAM/util；写明选择与默认值 |
| T3.3 | parity/回归 | 最终 audio MOV sha256、`parity_check`、`final_accept_v2`、unit、6 源 hook 矩阵、透明 50 GiB 定向复验（代码路径若未动可抽样） |
| T3.4 | 内存/回退测试 | 低内存配置、无 CUDA、CuFFT OOM、共享内存失败；确认默认路径字节级不变 |
| T3.5 | 确定性与并发 | 重复运行、缓存复用/淘汰、`--jobs>1` 并发、长时间运行无泄漏 |
| T3.6 | 配置/文档/发布 | `gpu_est`/缓存键（内部/config，非 CLI）、默认 off、README/release notes、版本递增、构建+verify+prerelease 流程 |

**检查点 G3**：C1305 ≤160 s、C1306 ≤120 s、所有 parity 门 PASS、默认路径未变 → 可发新 prerelease；否则保持 v0.9.0，把结果作为负向结论归档。

## 3.5 当前进度（2026-11-01，1h 批次）

| ID | 状态 | 说明 |
|---|---|---|
| T0.2 | ✅ | `KTS_EST_DUMP_DIR` 钩子；C1306 18 次调用 frames+PairEstimate 落盘 `K:\1kt_work\perf\ws13\golden_c1306` |
| T0.3 | ✅（已有） | v0.9.0 阶段耗时表已在 `audio_metadata_perf_report.md` |
| T1.2 | ✅ | `core/coarse_cache.py`：分块 build，60 s 真实音频 599 帧 ref 0 mismatch / tgt 仅 4 边界帧 |
| T1.5 | ✅ 原型 | opt-in env `KTS_COARSE_CACHE=1`：缓存文件写 decoded 旁（f64）；worker memmap 切片；首尾 8 帧回退；池关闭清理 |
| T2.1 | ✅ | `gcc_phat_int` 拆为 `_gcc_surface` + `_gcc_peak_from_surface`；600 帧真实数据与备份实现 0 mismatch |
| T2.2+ | ⬜ | 未开工 |

**T2.4 单进程 GPU 原型实测（真实 60 s，599 帧，CUDA 预热后）**：

| 路径 | wall | 分解 |
|---|---:|---|
| CPU `_est_frames`（单进程） | 0.575 s（0.96 ms/帧） | — |
| GPU 单进程原型 | **0.377 s（1.52×）** | cache+coarse gather 0.136 s；**CPU coarse 峰循环 0.192 s**；full gather 0.008 s；CPU full 峰循环 0.001 s |
| 对比 | delay **599/599 相同**；confidence 逐位 203/599（其余差末位） | — |

**G2 预判：单进程架构 no-go（按当前实现）**。原因：
1. GPU 只把 FFT 阶段变快（T2.2 7.6×），但单进程下 **CPU 峰逻辑 0.192 s/599 帧**
   成为新瓶颈（约占 GPU 路径 51%）；
2. 生产估计目前是 12 进程并行（每 pair wall 3–8 s）；单进程串行 41k 帧约
   26 s/pair × 18–36 次 = 470–940 s，远慢于现状，"单进程"本身不可行；
3. 需要先做 T2.3 的**向量化峰逻辑**（NumPy/GPU），把 0.192 s 压到 ~0.02 s，
   并保留 pair 级并行（多进程各自 GPU 或单进程多流），才可能进 G2。

**建议的修订方案**（待决策）：
- 方案 A：现有 12 进程池内每 worker 用 GPU 批量 surface（12 个 CUDA 上下文，
  VRAM ~12×~200 MB），配 shared_memory 缓存；峰逻辑仍在 worker 内 CPU；
- 方案 B：T2.3 向量化峰逻辑 + 单进程 GPU，但需要把 pair 估计重新并行化
  （例如 2–4 个 GPU 进程各批处理不同 pair），并重新算 G2；
- 方案 C：放弃"单进程"，只保留 T2.2 GPU surface + 现有进程池，收益上限较低
  （gcc FFT 约占帧估计 CPU 42%，12 进程下 wall 收益有限）。

**T2.2 原型实测（GPU 批量 surface，真实 60 s 音频 600 帧）**：

- 产物：`core/gpu_frames.py`（只算 float64 相关面；峰逻辑仍走 CPU
  `_gcc_peak_from_surface`）。
- 全速率阶段：CPU 0.414 ms/帧 → **GPU 0.054 ms/帧（7.6×）**；首批含 CUDA
  JIT/上下文时首调 2.3 s，必须预热。
- 与 CPU 参考对比：**600/600 帧整数 delay 完全相同**；confidence 只有
  385/600 逐位相同（其余差 float64 FFT 末位）。这意味着：最终整数移位/音频
  很可能不变，但 report/confidence 门限需要重新验证，符合"高风险"预判。
- 该原型尚未接入 `_est_frames`；接入后还需 CPU 峰逻辑开销与单进程串行代价
  （G2 门）。

**T1.5 原型实测（memmap 文件版，CPU 默认路径）**：

| 素材 | v0.9.0 基线 | + coarse cache | 结论 |
|---|---|---|---|
| C1306（69 min） | 217.0 s（黄金 dump 版 231.3 s） | **200.8 s**（-7.5%，但与运行方差同量级） | 疑似小幅收益，需复测 |
| C1305（80 min） | **288.4 s** | **303.5 s（+5.2%）** | **明确 no-go** |
| C1305 `estimate_pair` 累计 | 106.5 s | **128.9 s（+21%）** | 缓存反而更慢 |

C1305 明确变慢的两个原因：
1. 构建 4 通道 f64 缓存（4×293 MB = 1.17 GB）在第一个 pair 内一次性付出，
   `max_s=16.8 s`；
2. 每个 worker 用 `np.memmap` 随机切 1600/4000 样本的窗（41k 帧/对），Windows
   文件页缺页 + 分散读比"对 14 k 样本做一次 filtfilt"还贵；页缓存命中也不够。
C1306 的 -7.5% 不足以覆盖该结构性代价，且没有复现稳定性。

**结论**：T1.5 的 memmap 文件缓存原型 **no-go**，不进入默认路径；集成代码
保留在 `KTS_COARSE_CACHE=1` 环境开关后（默认关闭）作为后续 shared_memory /
连续预读版的基础。T1.3 需要改为 **shared_memory + 一次性连续拷贝**（或
worker 内连续缓冲），并在 G1 重新量测；当前不继续 T1。

T1.2/T2.1 仍有效：`core/coarse_cache.py` 的分块构建语义已被验证；
`_gcc_surface`/`_gcc_peak_from_surface` 拆分与备份实现 600 帧 0 mismatch。

## 4. 风险与预案

- **峰逻辑重写**（T2.3）是最大风险：先 CPU 循环、逐帧 equality，再谈向量化；任何无法解释的差异都停在 CPU 循环或放弃。
- **单进程 vs 12 进程**：GPU 批处理若被 Python 峰逻辑/IO 拖慢，收益可能 <10%；G2 提前量测，不达标直接 no-go。
- **共享内存**：Windows `spawn` + `--jobs` 并发时需限额；崩溃残留需启动清理；备选只读 memmap（但增加写盘，需用户批准）。
- **内存**：4 通道 f64 缓存 C1305 ≈1.2 GB；加 GPU 缓冲/RSS 需 ≤2.5 GB 门限。
- **cuFFT 数值**：默认仅要求最终 MOV sha 一致；若逐帧 delay 有翻转导致决策变化，则必须保留 CPU 峰逻辑/放弃 GPU 帧路径。
- **排期**：T0 ~0.5 天；T1 ~2–3 天；T2 ~3–5 天；T3 ~1–2 天（按人类工程日粗估；实际以检查点为准）。

## 5. 交付物

- 分支 `perf/frame-batch-gpu`（从 `main` v0.9.0 切出）+ 原始文件备份；
- `docs/design/perf_frame_batch_tasks.md`（本清单）+ 实测报告；
- 黄金数据/基准脚本：`work/perf/ws13/`；
- 成功后：默认 off 的实现 + 新 prerelease；失败后：负向报告与 v0.9.0 保持不变。
