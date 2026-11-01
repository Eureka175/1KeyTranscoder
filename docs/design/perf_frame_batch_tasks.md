# 任务清单：run 级共享 coarse 缓存 + 单进程 GPU 帧批处理（v0.9.0 后可选优化）

> **状态**：未开工（规划）。仅当明确批准后执行。
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
