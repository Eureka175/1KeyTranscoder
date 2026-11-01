# 异步流水线与连续 jobs 调度 — 计划书 / 可行性评估

> **状态**：规划（2026-11-01，未实现）
> **目标版本**：建议随 v1.1（v1.0 前只做低风险调度修复）
> **范围**：硬件后端批处理（`nvenc` / `qsv` / 多后端）为主；软件后端、repair、
> 透明模式单列兼容性要求。
> **不改**：每文件最终产物的语义与校验规则；默认单文件串行行为保持可用。

---

## 0. 一句话结论

- **连续补槽（slot refill）**：高可行、低风险，优先做。当前固定 `--jobs N`
  其实已经会补槽；**默认的 `--jobs auto` 是波次调度（wave）**，每波必须全部
  完成才开下一波，这是你看到 `done=1 encoding=1 / 110` 的原因。
- **音频/视频异步（同文件内 overlap）**：可行，音频对齐只依赖**源文件**，
  与视频编码互相独立；但 metadata/preservation 依赖视频产物，最终 mux 需要
  两者汇合。因此是"每文件 DAG 的 stage 流水线"，不是"所有 backend 完全独立"。
- **metadata 异步**：可跨文件与编码重叠，但本文件内必须在视频产物之后；
  收益主要是填补 GPU 空闲段，受磁盘/GPAC IO 限制。
- **主要风险**：`channel_sync` 内部已有 12 进程池，多个音频任务并发会
  CPU/内存超卖；必须先做全局资源预算（音频并发 1，或共享估计进程池）。

---

## 1. 当前实现与问题定位

### 1.1 jobs 调度的两种路径（`core/batch_hw.py`）

| 路径 | 行为 | 问题 |
|---|---|---|
| `--jobs N`（固定数字） | `run_hw_pool()` 把**全部文件**提交给 `ThreadPoolExecutor(max_workers=N)`；完成一个立即补下一个 | **已具备连续补槽**；没有波次屏障 |
| `--jobs auto`（默认） | `AdaptiveJobs` 波次调度：取 `wave_size` 个文件 → 等整波 `as_completed` 全部结束 → 计算整波吞吐 → 调下一波大小 | **波尾空槽**；你的 dashboard `done=1 encoding=1` 正是此路径 |
| `--experimental-multihw` | `run_multihw_pool()` 两个队列、全局 budget，但同样是**波次**执行 | 同波尾空槽，且两后端波次耦合 |

`AdaptiveJobs` 只用"每波聚合 fps"判断并发，缺少单文件完成粒度，无法直接用于
连续调度；需要改为滑动窗口/完成事件驱动。

### 1.2 每文件当前阶段顺序

| 路径 | 实际顺序 | 可异步点 |
|---|---|---|
| Sony 保留（`encode_one_sony_hw`） | probe → **channel_sync** → `run_sony_pipeline`（视频编码 → metadata/容器 → 校验） | audio 与 video 可并行（audio 只读源） |
| 经典非 Sony（`encode_one_hw_classic`） | probe → **视频编码** → channel_sync → remux audio → `place_final` | audio 与 video 可并行（audio 只读源） |
| DJI 保留（`encode_one_dji_hw`） | 与 Sony 类似：audio 在视频前/pipeline 内 | 同上 |
| 透明模式 | 无视频编码：audio 修正 → remux/restore | 文件级并行即可，无 stage 拆分必要 |
| 软件后端（x265/svtav1） | 单文件串行；`--jobs` 目前无效（1kt.py 明确提示） | 不在本计划第一阶段 |

### 1.3 依赖关系（关键）

```
probe ──┬─> audio_sync ─────────────────────┐
        └─> video_encode ──> metadata/mux ──┴──> final/verify
```

- `audio_sync` **只依赖源文件**，与视频编码完全独立；
- `metadata/preservation` 依赖视频中间产物（以及源容器元数据）；
- `final mux / restore` 需要视频 + 修正音频 + metadata 三者的结果；
- 因此同文件内最多把 `audio_sync` 与 `video_encode` 重叠；metadata 只能在
  视频产物之后开始；最终 mux 是汇合点。

---

## 2. 目标与非目标

### 2.1 目标

1. **连续补槽**：任何 worker 完成即取下一个文件，不等待整波；`auto` 与
   `multihw` 都适用。
2. **每文件 stage 异步**：`audio_sync` 与 `video_encode` 并行；metadata / mux
   按 DAG 排队；GPU 编码不再因音频串行而空转。
3. **资源感知**：GPU 编码槽、音频 CPU 槽、GPAC/IO 槽分别限额，避免
   `channel_sync` 多进程池叠加导致超卖。
4. **可观测**：dashboard 显示每个文件的**当前 stage**（编码/音频/preserve/
   mux/verify）与队列等待，不再只有编码/完成两态。
5. **不回归**：输出文件与现有实现逐位一致（或按既有验收口径完全一致）；
   L3/unit/透明/repair 全部可复跑。

### 2.2 非目标（第一阶段）

- 不把单个文件**按帧/片段**拆到多机（见网络多机渲染的独立评估）。
- 不改变编码器参数、preset、metadata 语义。
- 不做软件后端的并发编码（x265 线程竞争，另行标定）。
- 不改变 repair 的进程池调度（后续单独评估）。

---

## 3. 可行性评估

### 3.1 连续补槽：可行，优先

| 方案 | 说明 | 风险 |
|---|---|---|
| **A. 固定 N + 全部提交**（现有 `--jobs N`） | 已有行为，可直接作为"auto 关闭"的退路 | 无（已验证） |
| **B. 自建 worker 循环 + 动态 target** | N 个 worker 从共享队列取任务；完成一个取一个；按滑动窗口吞吐调整 target；缩容时 worker 自然退出 | 中：线程安全、结果计数、dashboard 并发更新 |
| **C. ThreadPoolExecutor(cap) + 信号量限流** | 提交全部任务，用 semaphore 控制同时活跃数；target 变化时调整信号量 | 中：等待中的任务占线程，dashboard 可能显示 queued |

**推荐 B**：实现简单、语义清晰、可适配 auto；`AdaptiveJobs` 保留但改为
"每完成 K 个任务/每 T 秒聚合一次"计算吞吐，而不是每波一次。

### 3.2 同文件 audio/video 异步：可行，中等风险

**依据**：

- audio sync 的输入只有 `src` 和 `streams`（`run_channel_sync(source=src,...)`），
  不读视频中间产物；视频编码的输入也只有 `src`。两者无数据依赖。
- 现有顺序差异（Sony 先 audio、classic 先 video）说明二者本来就可交换；
  交换后输出一致，只是最终 mux 等待两者。

**需要改的地方**：

1. 把 `channel_sync` 从 `encode_one_*` 内部提出来，作为 stage 任务；
   视频编码与 audio task 同时启动；最终 pipeline 接收 `audio_sources`
   （或 `None` 表示原音频）。
2. Sony/DJI 的 `run_sony_pipeline` / `run_dji_pipeline` 需要在"视频产物已就绪"
   时启动 metadata/容器阶段；audio task 结果在 final mux 前 join。
3. 失败语义：audio 失败 → 沿用原音频并继续（现有行为）；video 失败 →
   取消 audio? 可以让 audio 自然完成但丢弃结果，避免浪费清理。

**主要风险**：

- `channel_sync` 内部会开 12 进程估计池；同文件 audio 与 video 并行时 CPU
  核数被编码器/估计池竞争；**多文件并发 audio** 会叠加 12×N 进程，必须
  全局限制（建议音频阶段并发 = 1；或给 `sync_estimate` 一个全局池）。
- 内存：`channel_sync` 峰值 1.1–2.3 GB；多文件并发 audio 会线性上升。
- 源文件被视频解码与音频解码同时读：Sony 源约 50 GB，页缓存压力；磁盘
  随机读可能拖慢，需要实测。

### 3.3 metadata / mux 异步：部分可行

- metadata（GPAC/`run_sony_pipeline`）依赖视频中间产物，不能与视频编码并行；
- 但可以**跨文件**：文件 A 在做 mux/校验时，文件 B 在编码；这就是 stage
  流水线的收益；
- 受磁盘 IO 和 GPAC 临时文件限制，mux 并发建议 1–2；
- 透明模式/repair 的 metadata 恢复路径不同，暂不纳入同一条流水线。

### 3.4 结论

| 能力 | 可行性 | 收益（预估） | 风险 |
|---|---|---|---|
| `auto` 连续补槽 | 高 | 波尾空槽消除；文件时长不均时吞吐 +10–30% | 低 |
| 同文件 audio/video overlap | 中高 | 每文件隐藏 audio sync 时间；GPU 利用率提升 | 中（CPU/内存超卖） |
| 跨文件 stage 流水线（metadata/mux） | 中 | 进一步填 GPU 空闲；受 IO 限制 | 中高（复杂度） |
| "audio/video/metadata 完全独立 backend" | 部分 | 只能按 DAG，不是无限并行 | 高（不是正确抽象） |

---

## 4. 推荐架构

### 4.1 调度器：`ContinuousScheduler`

```text
sources queue
   │
   ├── admission: 检查输出已存在 -> skip（沿用现有逻辑）
   │
   ├── video worker slots (N_video = --jobs / auto target)
   ├── audio worker slots (N_audio = 1 默认, 可配置)
   ├── preserve/mux worker slots (N_mux = 1–2)
   └── verify worker slots (N_verify = 1–2)
        │
        └── per-file state machine:
            PROBED -> AUDIO_RUNNING / VIDEO_RUNNING
                   -> PRESERVE_QUEUED -> MUX_QUEUED -> VERIFY -> DONE/FAILED
```

- 每个 stage 是独立的有界队列 + 固定 worker 数；worker 完成一个任务后从
  队列取下一个，天然连续补槽。
- 文件级 DAG 由 scheduler 在任务完成时推进；不阻塞其他文件。
- `--jobs` 只控制 **video 编码槽**（与当前语义一致）；audio/mux 用内部预算
  （或新增 config 键，非 CLI）。
- `auto`：video 槽数由滑动窗口吞吐控制器调整；缩容时 worker 完成当前任务
  后退出，不抢占队列。

### 4.2 Dashboard 状态

- `DashboardStatus.start()` 在**admission**时创建（`queued`），不再等到
  worker 真正开始；这样 110 个文件可在 dashboard 看到整体队列；
- `update()` 增加 `stage` 字段：`queued/probe/audio/video/preserve/mux/verify`；
- 行数较多时只显示活跃 + 最近完成（可选 UI 改进，非必需）；
- `set_meta` 增加 stage 计数（encoding/audio/mux/queued）。

### 4.3 资源预算（关键）

新增内部 `ResourceBudget`（非 CLI，config 可覆盖）：

| 资源 | 默认 | 说明 |
|---|---|---|
| `video_slots` | `--jobs` / auto target | 每文件一个视频编码 worker |
| `audio_slots` | **1** | `channel_sync` 自身已 12 进程；并发 1 防止 CPU/内存爆炸 |
| `mux_slots` | 1 | GPAC/最终容器 + 磁盘 IO |
| `verify_slots` | 2 | postprobe/check（轻量） |
| `est_worker_cap` | 现有 12 | 若未来允许 audio 并发 >1，需全局共享进程池 |

可选：允许 `audio_slots>1` 但把 `channel_sync` 的 `est_worker_cap` 按并发
数缩小（例如总量 12 个估计进程全局预算），这需要 `sync_estimate` 支持
全局 semaphore/池，属于二期。

---

## 5. 实施阶段与验收

### Phase 0 — 基线与可观测（0.5–1 天）

- 记录当前 `--jobs auto` 的波次行为：每波开始/结束时间、槽位占用、
  dashboard 行数；选 2–3 组文件（等长/不等长、Sony/经典/DJI）做基线。
- 增加临时 instrumentation（不发布）：`[SCHED]` 日志记录文件开始/结束、
  stage 时间、槽位空闲时间。

### Phase 1 — 连续补槽（1–2 天，低风险，优先）

- 实现 `ContinuousScheduler` 的 video-only 版本：共享队列 + N worker；
  `AdaptiveJobs` 改为完成事件驱动的滑动窗口吞吐。
- 替换 `run_hw_pool` 的 auto 波次；`run_multihw_pool` 同样改为连续调度，
  保留全局 budget 与两队列。
- Dashboard：admission 时建行、显示 queued；`update` 保持兼容。
- 验收：
  - 输出与旧实现一致（任选 10+ 文件逐字节/验收口径）；
  - unit 全绿；L3 无新增失败；
  - dashboard 不再出现"整波等一个慢文件"；波尾空槽时间显著下降；
  - 不等长文件组吞吐提升（目标 ≥10%，按实测基准）。

### Phase 2 — 同文件 audio/video overlap（3–5 天，中风险）

- 抽取 `run_audio_sync_stage(ctx, src) -> AudioStageResult`，从
  `encode_one_sony_hw` / `encode_one_hw_classic` / `encode_one_dji_hw` 中解耦；
- scheduler 启动 video + audio 两个任务（同一文件），最终 pipeline 等待两者；
- audio worker 并发固定 1（资源预算），失败时回落原音频并记录；
- Dashboard stage 显示 `audio|video|mux`。
- 验收：
  - 10+ 文件（Sony/经典/DJI）输出与旧路径一致；
  - `channel_sync` 不并发（或全局估计进程数不超预算）；RSS 不超过基线 +10%；
  - 每文件 wall 下降（音频时间被隐藏的比例按素材统计）；
  - 失败注入：audio 失败、video 失败、mux 失败均不产生错误成片；
  - 长测 L3 + C1305/C1306 验收材料。

### Phase 3 — 跨文件 metadata/mux 流水线（3–5 天，中高风险）

- 把 `run_sony_pipeline` / DJI pipeline / `place_final` 拆为 stage；
- mux/preserve 用独立 worker（1–2 并发），跨文件重叠；
- 处理磁盘 IO 压力、临时空间、GPAC 并发限制；
- 验收：吞吐提升、输出一致、无死锁/资源泄漏；透明/repair 回归。

### Phase 4 — 自适应与默认化（2–3 天）

- 根据实测设定默认 `audio_slots`/`mux_slots`；auto 控制器调优；
- 决定是否默认开启异步流水线（建议先 opt-in 一个版本再默认）；
- 文档/README/发布说明；性能前后对照。

---

## 6. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| `channel_sync` 多进程超卖 | CPU 100%、内存爆、编码变慢 | 默认 audio 并发 1；全局估计进程预算；压测 `--jobs 2/4` |
| 源盘并发读 | 视频解码/音频解码抢 IO | 实测；必要时限制 audio 与 video 同时启动的文件数 |
| 磁盘临时空间 | mux 并发导致空间不足 | mux 并发 1–2；失败回退/清理 |
| Dashboard/日志并发 | 状态丢失、日志交错 | `DashboardStatus` 已有锁；失败记录/日志用锁/队列 |
| 输出非确定 | 验收失败 | 每文件独立 work dir；最终产物仍单写者；逐文件校验 |
| 失败/中断 | 半成品/残留 | 现有 `.part`/work 清理逻辑；scheduler 取消下游；resume 仍按输出存在判断 |
| 复杂度 | 回归风险 | 分阶段、opt-in、每阶段跑 L3+验收；Phase 1 独立可回退 |

---

## 7. 性能测量与目标

| 指标 | 采集方式 | 目标 |
|---|---|---|
| 批量 wall | 总开始/结束 | 不等长文件组 ≥-10%；Sony 混合组按实测 |
| GPU 编码利用率 | nvidia-smi / 编码 fps | 波尾空闲时间显著下降 |
| 槽位占用率 | scheduler 日志 | 完成即补槽，无整波等待 |
| 每文件 stage 时间 | dashboard/日志 | audio 时间大部分被 video 覆盖 |
| RSS/VRAM | psutil/nvidia-smi | RSS ≤ 基线 +10%；VRAM 不超 |
| 输出一致性 | 逐文件 sha/验收 | 100% 一致或按既有口径 PASS |

---

## 8. 开放决策（需你确认）

1. **默认策略**：Phase 1 连续补槽是否直接替换 `auto`（建议是）；Phase 2
   异步流水线先 opt-in（建议 `--async-pipeline` 或 config 键）还是直接默认？
2. **`--jobs` 语义**：是否仍只控制 video 编码槽；audio/mux 用内部预算？
3. **软件后端**：是否纳入异步（x265 并发需线程标定，建议后续）。
4. **transparent/repair**：是否要求同一套调度（建议 separate，先不动）。
5. **资源键**：是否允许 config 覆盖 `audio_slots`/`mux_slots`（非 CLI），
   还是完全固定。

---

## 9. 预估工作量

| 阶段 | 工作量 | 风险 | 价值 |
|---|---|---|---|
| Phase 0 基线 | 0.5–1 天 | 低 | 必要 |
| Phase 1 连续补槽 | 1–2 天 | 低 | 立即改善波尾空槽 |
| Phase 2 audio/video overlap | 3–5 天 | 中 | 隐藏音频时间，GPU 利用率提升 |
| Phase 3 metadata/mux 流水线 | 3–5 天 | 中高 | 进一步吞吐 |
| Phase 4 自适应/默认化 | 2–3 天 | 中 | 收尾 |

**建议**：先做 Phase 1（独立小改动，可先发 v1.0.x），Phase 2 作为 v1.1
的主要性能项；Phase 3 视 Phase 2 收益决定。

---

## 10. 附录 A：状态机

```text
ADMITTED
  ├─ output exists -> SKIPPED
  └─ PROBED
       ├─ AUDIO_QUEUED -> AUDIO_RUNNING -> AUDIO_DONE/FAILED_ORIGINAL
       └─ VIDEO_QUEUED -> VIDEO_RUNNING -> VIDEO_DONE/FAILED
                           (Sony/DJI) -> PRESERVE_QUEUED -> PRESERVE_DONE
       └─ when video (and preserve) done:
            MUX_QUEUED -> MUX_RUNNING -> MUX_DONE
       └─ VERIFY_QUEUED -> VERIFY_DONE
       └─ DONE / FAILED (first failure cancels downstream; cleanup)
```

## 11. 附录 B：调度伪代码

```python
while pending or running:
    while free_video_slot and pending:
        src = pending.popleft()
        submit(video_executor, process_video_stage, src)
    while free_audio_slot and audio_pending:
        src = audio_pending.popleft()
        submit(audio_executor, process_audio_stage, src)
    completed = wait_first_completion()
    advance_file_state(completed)
    maybe_adjust_video_slots(rolling_throughput())
```

关键点：**任何 stage 完成都立刻从对应队列补下一个任务**；`auto` 只调整
video 槽数，不阻塞补槽。
