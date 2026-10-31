# 性能优化 — 工作状态（长程自主任务）

> 最后更新：2026-11-01
> **分支**：`perf/audio-metadata`（从 `main` `5d08370` 切出；发布 tag `v0.8.11` = `4840dd8` 不动）
> **原始代码备份**：`work/perf/backup_v0.8.11/`（core/long_take.py、channel_sync.py、
> sync_estimate.py、audio_format.py、preservation/{fast_restore,gpac,transparent_restore}.py、
> production/output.py、1kt.py）
> **计划**：`docs/design/audio_metadata_perf_plan.md`

## 规则（用户约束）

1. 不允许开 subagent；全部自己完成。
2. **F 盘除项目目录 `F:\1KeyTranscoder` 外禁止写入**；临时/测试产物写 `K:` 或 `I:`。
3. 长程自主：每阶段输出成果/阻碍/下一步，然后继续。
4. 大改先开 branch + 备份原始代码（已做）。
5. 可用 CUDA 加速；N 卡消费级 FP64 弱 —— 优化中避免/少用 FP64。
6. 保持验收零回归（曲线 0/0/0、音频容差、确定性、fast-restore 矩阵/50 GiB E2E）。
7. **尽量减小磁盘写入次数/最小化落盘的中间文件**（生产 metadata/透明重封装尤其）；
   **显存与内存占用量**也要做必要优化（两者都是硬指标之一）。

## 环境

| 项 | 值 |
|---|---|
| CPU | Intel Core Ultra 9 285H，16 物理核 / 16 逻辑核 |
| GPU | NVIDIA RTX 5070 Laptop，8 GiB，驱动 616.92 |
| CUDA | toolkit 13.1（nvcc 可用）；pip 可下载 `cupy-cuda12x` |
| Python | 3.11.9；numpy 2.4.6、scipy 1.17.1；无 cupy/torch/numba（可装） |
| 磁盘 | F: 项目盘（仅项目目录可写）剩余 ~255 GB；I: 358 GB；K: 543 GB |
| 临时目录 | `K:\1kt_work\perf\`（所有测试/中间产物） |

## 基线（v0.8.11 实测，来自验收记录）

| 指标 | C1305（80.1 min） | C1306（69.4 min） |
|---|---|---|
| 音频域整文件 | 689.9 s | 389.1 s |
| long-take 附加 | 231.0 s | 193.8 s |
| RSS 主/树 | 775 MB / 1470 MB | 同量级 |
| 透明 50 GiB：stage mux | 258.7 s | 43 GiB 时 162.9 s |
| 透明 50 GiB：fast restore | 99.1 s | 43 GiB 时 57.8 s |
| 透明 50 GiB E2E | 12.8 min | — |

目标：C1305 ≤450 s、C1306 ≤260 s、long-take ≤130 s、50 GiB E2E ≤9 min、
RSS 主 ≤550 MB/树 ≤1.1 GB，正确性零回归。

## 阶段日志

### WS0 — 准备（完成）

- [x] 新 branch `perf/audio-metadata`；原始代码备份到 `work/perf/backup_v0.8.11/`。
- [x] 环境探测：CPU/GPU/CUDA/包/磁盘（见上）。
- [x] 临时目录 `K:\1kt_work\perf\`。
- [x] 状态文档（本文件）。
- 备注：目标（goal）系统提示 "model cannot resume a paused goal"，本任务改用本文件
  作为事实上的长程状态记录；不影响执行。

### WS1 — profile 基线（完成，2026-11-01）

C1306 基线（timing 模式，`K:\1kt_work\perf\ws1\c1306_timing.json`）：

| 阶段/函数 | 累计耗时（嵌套，wall 归因见备注） |
|---|---|
| **wall（整文件音频域）** | **296.3 s**（baseline 记录 389.1 s 含验收解码/写盘，`final_accept` 口径） |
| long-take `align_time_varying` | 159.8 s（内含 2 次窗测量 ≈96 s + 残差 34 + 施加 26.6 + 极性 5.5 + 反转 4） |
| 窗级测量 `measure_window_delays` | 4 次共 191 s（stereo 检测 2 次 + long-take 2 次，串行排在主线程） |
| stereo 组绑定检测 | 52.5 s（内部候选对已线程并行） |
| 帧级估计 `estimate_pair` | 18 次共 64.8 s（进程池 12 workers，串行 18 对） |
| 窗测量内部（cProfile） | `_coarse_cc_peak` 69.8 s（r2c 35.1 + c2r 18.2 s）、coarse_decimate 13.2 s、refine 6.0 s |
| `reference_interp_write` | 4 次 26.6 s（interp 7.4、write 7.1） |
| 残差测量 | `measure_residual_curve` 2 次 34.2 s（resample_poly 6.2 s + FFT） |
| 解码 | 4 通道并行 13.5–17 s |

结论：**窗级相关测量（~150 s wall）与帧级估计（~65 s）是两大头**；残差/施加/极性约 72 s。

### WS2 — 音频并行优化（第一轮完成，2026-11-01）

改动（`core/channel_sync.py`、`core/long_take.py`）：

1. **stereo 组绑定检测与锚点估计并行**：检测（只读 decoded raw）放到后台
   daemon 线程，锚点估计照常走进程池，主路径前 join；失败依旧回落为无绑定组。
2. **long-take 窗级测量并行**：各 target 的 60 s/10 s 连续性测量互相独立 →
   线程池并行；锚点 coarse 缓存只 `coarse_decimate` 一次并在各线程共享
   （`measure_window_delays(ref_decimated=...)`；数组只读，结果与重算逐位相同）。
3. 外部注入的自定义 measure 保持串行（不改变其线程安全假设）。

结果（同一 C1306 素材）：

| 指标 | baseline | WS2 后 | 变化 |
|---|---|---|---|
| **wall** | 296.3 s | **253.9 s** | **-14.3%** |
| long-take `align_time_varying` | 159.8 s | 125.1 s | -21.7% |
| stereo 检测 | 52.5 s | 64.1 s（与估计并行，被 CPU 争用拉长） | — |
| 帧级估计 | 64.8 s | 70.4 s | — |
| 残差/施加/极性（串行部分） | 72 s | 72 s | 未动 |

**正确性**：`work/perf/parity_check.py` 对比 baseline manifest（4 个 audio mov +
4 个 round1/round2 raw 的 sha256 + report 语义）→ **PARITY OK（逐位一致）**；
unit **1020 PASS / 2 FAIL**（仅环境项）。

### WS3 — 音频磁盘写入/中间文件（2026-11-01，完成第一轮）

改动（`core/long_take.py`）：
- **round-1 原地施加**：f32 参考插值直接改写 `decoded_*.f32` 本身。
- **round-2 原地施加**：`reference_interp_write_inplace` 逐块读被 round-1
  改写后的同一文件、算完立即写回（研究实现的块边界 clamp 语义不变）。
- **极性原地翻转**：`_invert_raw_inplace` 逐块取反写回。
- `channel_sync` finalize 识别 `fixed_raw[i] == decoded[i]` 的原地情形，
  不会提前删除正在作为成品的 decoded 文件。

实测（C1306，一条 target 的中间文件）：
| 项 | 旧 | 新 |
|---|---|---|
| work 目录中间 raw | `round1_*` + `round2_*` + `polarity_*`（每 target ~2.3 GB） | **无**（只有 final `audio_*.mov` + report） |
| 音频侧写盘（C1306 估算） | decoded 3.7 GB + round 文件 4.6 GB + MOV 2.3 GB ≈ **10.6 GB** | decoded 3.7 GB + MOV 2.3 GB ≈ **6.0 GB（-43%）** |
| 正确性 | — | parity_check：4 条 final MOV 与 baseline sha256 完全一致；report 语义一致 |

**注意（内存）**：并行化后实测 **RSS 峰值主进程 1232–1278 MB / 进程树
2303–2331 MB**（旧串行基线 775 / 1470 MB）。逐阶段映射（run6/7/8 日志）：
估算阶段 12 个子进程把 tree 推到 ~2.3 GB；long-take 并行窗测量在 124–181 s
之间让主进程达到 ~1.2 GB（anchor + 2×target coarse 缓存 ~693 MB + 每线程
FFT 缓冲 ~120 MB + 基础 ~200 MB）。已做的缓解：
- `_COARSE_CHUNK_SECONDS` 100 s → 25 s（transient f64 缓冲 -28 MB/线程，结果逐位不变，
  parity_check 证实）；
- stereo 检测结束后 `gc.collect()` + Windows `EmptyWorkingSet` 归还空页
  （峰值 1261 → 1232 MB，作用有限——峰值在测量阶段本身）；
- PROFILE 新增 `measure_parallel_units` / `pipeline_parallel_units` 两个
  非 CLI 开关：内存受限时置 False 可退回顺序测量（峰值约 0.8 GB，wall +~45 s）。
下一步若要硬压到 ≤0.8 GB 默认：把窗测量改为**串行 + 窗级批处理**（同一
target 内预取 batch 的 CC 面，内存可控）或使用 mmap 的 coarse 缓存，
属较大改动。

### WS3b — 待做

- 残差测量：60 s 窗重叠 resample_poly 每窗重复重采样；评估整文件预抽取
  （需先证明与逐窗口径逐位一致）→ 预计残差 34 s → ~15 s。
- 估计阶段（18 次 pair，~65 s）的重复读取/滤波缓存；`换参考` 的 6 次
  几何阶梯目前顺序执行。
- 窗测量串行 + 批处理版本（兼顾 ≤0.8 GB 与 wall）。

### WS2 补充：磁盘写入/中间文件与内存（新要求，规划）

音频侧当前中间文件：decoded_*.f32（4×923 MB）→ fixed_*.f32 / round1/round2
（每 target 每轮 763 MB）→ audio_*.mov（4×572 MB）。计划：
- 合并 round1+round2 为**单次输出写**（流式两阶段插值，避免 round1 落盘）；
- 残差测量所需的 pass1 用流式虚拟输入（不落 round1 文件）；
- 评估 `decode` 后直接以 memmap/缓存复用，避免 fixed 再写一份；
- 目标：音频中间写盘从 ~11 GB 降到 ~4–6 GB（decoded + 最终 mov）。
- 元数据侧：见 WS4 —— **GPAC mux 后原地修补 moov/元数据**（不再写 stage +
  再整文件复制），目标 50 GiB 透明路径只剩 1 次 GPAC 写 + 小 moov 补丁；
  这是"最小化落盘中间文件"的核心。

### WS4 — 元数据/透明路径（第一轮完成，2026-11-01）

**目标**：透明 50 GiB 不再写 stage + 再整文件复制；最小化落盘中间文件。

实现：
1. `gpac.mux_new(..., flat=True, brand=..., add_brands=...)`：GPAC 直接以
   **flat 布局**（mdat 先、moov 后）把最终产物写到目标路径，并用
   `-brand XAVC -ab mp42 -ab nras` 得到 **32 字节 ftyp**（与 Sony XAVC 源
   ftyp 同尺寸）。
2. `preservation/fast_restore.py::fast_restore_inplace`：在**同一文件**上
   只改写 ftyp（32 B）+ moov（约 7.6 MB）+ 追加 root uuid/文件级 meta
   （15.5 KB）；**mdat 一个字节都不动**，50 GiB 不再需要第二次整文件读写。
   moov 内 chunk offset 无需平移（mdat 位置不变），仍校验 509,356 个
   chunk offset 全部落在 mdat 内。
3. `transparent_restore` 自动选择：源 ftyp 尺寸可由 GPAC 旗标匹配时走
   in-place；否则回退 copy-based fast restore（同一个 GPAC 产物作 stage，
   写临时文件后原子替换）；再失败才回退标准 GPAC 多轮流程。语义不变：
   任何失败最终仍回退源文件拷贝。

**50 GiB C1305 真实端到端（01:54:08–02:03:46）**：

| 指标 | 旧（copy fast restore） | 新（in-place） |
|---|---|---|
| 总耗时 | 12.8 min | **9.63 min（-25%）** |
| 磁盘写入 | GPAC stage 50 GiB + fast restore 写 50 GiB（另读 50 GiB） | **仅 GPAC 写 50 GiB + 7.6 MB moov 补丁** |
| 临时文件 | stage 50 GiB + 输出 50 GiB | **无 stage**（输出即最终文件） |
| 元数据校验 | 通过 | 通过（ftyp/uuid 9/9/meta 11304 B/tref/ipcm/签名）|
| payload | 通过 | rtmd 全量 streamhash = 源；视频 10 s hash 相同；4 音频解码与生产对齐 raw **diff=0** |
| Sony checker | PRESERVED=37 | **PRESERVED=37 MODIFIED=0 MISSING=0** |
| 日志 | `fast-restore: one-pass rebuild` | `fast-restore: in-place 49.78 GiB (mdat untouched 49.77 GiB, moov 7587467 -> 7587661 B, appended 15522 B, chunks 509356 checked)` |

小样本对照（107 MB XAVC clip，`work/perf/ws4/test_inplace_clip.py`）：in-place /
copy / 标准 GPAC 三条路径的视频、4 音频、rtmd payload 全部与源逐包相同；
in-place 与 copy 结构校验均通过；标准路径仍有已知 in24/meta 重编码差异。
50 GiB 复核脚本：`work/perf/ws4/verify_e2e_50g.py`。

**剩余项（WS4b）**：GPAC `-flat` mux 本身仍是 50 GiB 一次写（~2.5–4 min），
是透明路径剩余最大项；自写 muxer 直写最终文件（连 audio MOV 中间文件也省掉）
风险高，放到音频优化后再评估。

### WS5 — 真实素材回归/验收（进行中）

**C1305（80.1 min）音频域，新代码**：wall **357.2 s**（验收基线 689.9 s，
**-48.2%**）；long-take **118.6 s**（旧 231.0 s）；RSS 峰值主 1138.8 MB /
树 1922.5 MB。parity_check：4 条 final `audio_*.mov` 与旧的验收基线
**sha256 完全一致**，report 语义一致 → **无回归**。

**C1306（69.4 min）**：wall 236.3 s（旧 harness 296.3 s，**-20.2%**；验收
口径 389.1 s 含额外解码/比较）；parity OK。

**透明 hook 6 源矩阵**（`work/perf/ws5/test_hook_matrix.py`，全部 PASS）：
- XAVC clips（ftyp 32）：**in-place** 路径，结构/payload/签名全部与源一致；
- 原始 isom hard cases C0911/12/15/16（ftyp 24）：in-place 前置检查按预期拒绝
  （`stage ftyp size 20 != source ftyp size 24`），自动回退 **copy fast restore
  on the already-muxed file**（不重复 mux），同样全部 PASS。
- 说明 in-place 的适用性检查与回退链在真实布局上工作正常。

### WS6 — 长测试回归（环境阻塞）

全量 L3 尝试 4 次（03:38 起第 4 次持续 13 min 未完成）：
每次都卡在 `hardware_audio`/`p4c_production` 的硬件子进程上；py-spy 显示父
测试等待 `run_tool`/`_device_identity` 的 subprocess `communicate`，对应
native 编码器探测进程（QSVEncC64/NVEncC64 `--check-features`）不退出，且
killed parent 后 QSVEncC64 孤儿仍持有管道。`_run` 虽有 120 s timeout，但
每个卡住的硬件 probe 都会吃掉 2 min，多用例累加使 L3 远超正常 15 min。
**同样的 1kt 命令单独重跑分别 21 s / 33 s 正常完成**，说明不是本轮代码
逻辑；属于本机硬件探测/子进程管道环境问题（此前 v0.8.11 基线全量 L3 为
1261 PASS / 12 FAIL，12 条均为缺真实媒体环境项）。
已覆盖：unit 1020/2（环境项）、C1305/C1306 audio parity（final MOV sha256
完全一致）、透明 50 GiB E2E、6 源 hook 矩阵、in-place/copy/标准三路小样本。
`final_accept_v2` 级长验收留到环境恢复后集中跑一次。

### WS7 — 内存受限配置（2026-11-01，完成）

新增两个 config/内部键（非 CLI，默认=既有行为，见 `channel_sync.DEFAULTS`）：
`stereo_bind_workers`（stereo 候选对并发上限，默认 4）、`est_worker_cap`
（帧级估计进程上限，默认 12）；与 `long_take.PROFILE` 的
`measure_parallel_units` / `pipeline_parallel_units` 组合，可把并行度压到 1。

C1306 实测：

| 配置 | wall | RSS 主/树 | 输出 |
|---|---|---|---|
| 默认（全并行） | **236.3 s** | 1232.3 / 2310.7 MB | baseline 逐位一致 |
| 低内存（sb=1, est=4, 串行 LT） | 338.9 s（+43%） | **748.6 / 1036.2 MB（-39%/-55%）** | **PARITY OK（4 条 MOV sha256 一致）** |

结论：内存受限机器可用低内存配置把峰值压到 ~0.75 GB（甚至低于旧串行
775/1470 基线），代价 +~100 s；默认仍速度优先。进一步降到 ≤0.6 GB 需要
窗测量串行+批处理或 coarse 缓存 mmap（未做）。

## 下一步

1. 环境恢复后补跑全量 L3 + `final_accept_v2` 长验收。
2. 性能目标对照（audio C1305/C1306、透明 50 GiB、RSS）写入最终报告。
3. 可选后续优化：残差测量预抽取（需位级一致性证明，内存代价）、估计阶段
   换参考重测的并行化（语义需保持）、窗测量串行+批处理版本（≤0.6 GB 与 wall 平衡）。
4. 整理 `work/` 临时产物（K:/F: 项目内），保留小型 JSON/日志证据。

## 关键文件

- 计划：`docs/design/audio_metadata_perf_plan.md`
- 生产代码：`core/long_take.py`、`core/channel_sync.py`、`core/sync_estimate.py`、
  `preservation/fast_restore.py`、`preservation/transparent_restore.py`
- 验收脚本（work/，可复用）：`work/long_take/s6/final_accept_v2.py`、
  `work/long_take/s6/full_align_only.py`、`work/long_take/s7/fast_restore_matrix.py`
