# 架构总览 — 1KeyTranscoder 端到端数据流

> **本文档的定位。** 项目此前没有任何一份**描述实际代码如何串起来**的架构文档：
> `README.md` 讲用法与特性，`docs/design/*` 讲各子系统的设计决策，
> `docs/evaluation/*` 讲后端选型评估，但"一个文件从输入目录走到输出目录，
> 中间经过哪些模块、按什么顺序、失败时发生什么"此前只存在于代码里。
>
> 本文档据**主分支 `main` 的实际代码**写成（逐文件核对了函数与行数），
> 不是设计意图的复述。凡是与代码不一致的历史说法，以本文档为准，
> 并已在 §11 列出。
>
> **适用范围**：`v0.6.2`。改动入口或管线时请同步更新本文档。

---

## 1. 两个入口，两种工作

| 入口 | 作用 | 关键差异 |
|---|---|---|
| `1kt.py` | **主入口**：递归批量转码 + 元数据保留 | 需要编码器；走完整 probe→分类→编码→保留→验证 |
| `watchfolder.py` | 轮询批处理 | 只负责轮询与转调，不含管线逻辑 |

`1kt.py:1373 main()` 在解析参数后**先做一个二选一分支**，这是全文最重要的
结构事实：

```
main()
├── --channel-sync-transparent  →  transparent_main()   ← 剪辑前预处理
└── 否则                        →  正常转码管线（§3）
```

**两个分支不共享编码逻辑。**

* `transparent_main()`（`1kt.py:1179`）**跳过视频编码**：视频与所有非音频流
  `stream copy`，只对需要修正的音频轨重新生成。全部已对齐时输出与源文件
  **字节级一致**（SHA256 相同）。它不读档位 JSON，也不需要编码器可用。
* 正常管线才解析 `--encoder` / 档位 JSON / 能力探测。

> 这条分支比它看起来重要：`--channel-sync` 是"改音频"，`--channel-sync-transparent`
> 是"只改音频、不碰视频"。后者是**无损预处理**路径，前者是**转码 + 音频修正**。

---

## 2. 后端解析：谁决定用哪个编码器

`1kt.py:1420-1458`，优先级从高到低，**四种来源互斥**：

```
1. --config <file>            → 以 JSON 里的 "encoder" 字段为准
                                 （若同时给了 --encoder 且不一致 → 直接报错，不猜）
2. --encoder <name>           → 显式指定
3. --experimental-multihw     → 自行探测 NVENC+QSV（跳过默认自动选择，省一次探测）
4. 都没给                      → resolve_default_backend() 能力优先自动选择
```

第 4 条是 **v0.6.2 的行为变更**，必须说清楚：

```python
AUTOSELECT_ORDER = ("nvenc", "qsv")          # 1kt.py:1107
HW_AUTOSELECT_FALLBACK = "x265"              # 1kt.py:1108
```

* 依次探测 NVENC → QSV，**每个后端都要求 `--check-features` 真的成功并报出
  HEVC 编码能力**（`_hw_encoders_available()`，`1kt.py:1111`）。探测与执行用的是
  同一个探针，所以"自动选中的"与"跑得起来的"不可能不一致。
* 都不可用 → `x265`。
* `--no-hw-autoselect` → 直接 `x265`，**不做任何探测**（恢复 v0.6.1 及更早行为）。

### 2.1 "硬件路径永不回退软件"到底约束什么

这句话在本项目里有**两个不同的层次**，历史上被混用过：

| 层次 | 规则 | 实现位置 |
|---|---|---|
| **后端选择** | 自动选择可以选到 x265（因为压根没有硬件后端） | `resolve_default_backend()` |
| **单次运行** | 一旦选定硬件后端，**轨道内**绝不改成软件编码 | `hw_encode_with_fallback()` |
| **硬件解码** | 生产路径**恒用 `--avsw` 软解** | `encoders/nvencc.py:107`、`encoders/qsvencc.py:102` |

第三行是当前的实际状态，也是"硬件路径永不回退软件"最容易被误读的地方：
**生产从未使用硬件解码**，两个硬件后端都是**硬编 + 软解**。硬件解码的调研
见 §8。

### 2.2 三级降级梯（`core/batch_hw.py:245 hw_encode_with_fallback`）

硬件后端在**同一后端内部**降级，而不是降级到软件：

```
能力驱动的降级（静默，前置，出 WARNING）
    plan_initial_format() 按后端实测能力决定输出色度/位深
    4:2:2 / 10bit 不被支持时 → 降级并告警；--no-downgrade 则直接 FATAL
        ↓
运行期失败时逐级走 rungs（每一级出 WARNING）
        ↓
读端失败 → MP4Box strip 兜底（只用一次）
```

返回值是 `(warnings, final_format)`；致命错误抛 `RuntimeError`，由调用方
记入失败清单，**不会**静默改用 x265 重编。

---

## 3. 正常转码管线

```
1kt.py main()
  │
  ├─ 1. 工具链解析                find_executable / verify_executable（ffmpeg/ffprobe/GPAC/Gyroflow）
  ├─ 2. 参数校验                  output 不得位于 input 之内（1kt.py:1384）
  ├─ 3. 后端解析                  §2
  ├─ 4. 档位校验                  JSON 必须含全部 PRESETS（UHQ/HQ/SMALL/FAST）
  │
  ├─ 5. 发现源文件                core/paths.py discover_sources()
  │        jobs 调度              --jobs 1 | N | auto（core/batch_hw.py AdaptiveJobs:1581）
  │
  └─ 6. 逐文件 process_file_hw()（core/batch_hw.py:1415）
          │
          ├─ probe_source()            core/probe.py：编码/分辨率/位深/色度/帧数/音轨/流清单
          ├─ 源识别                    is_sony_source() / is_dji_source()（batch_hw.py:1323/1332）
          │
          ├─ 三条源专用路径（互斥）：
          │     encode_one_sony_hw()      batch_hw.py:619   ← Sony：保留管线
          │     encode_one_dji_hw()       batch_hw.py:1045  ← DJI：保留管线
          │     encode_one_hw_classic()   batch_hw.py:857   ← 其它：经典转码
          │
          ├─ hw_encode_with_fallback()     §2.2 三级降级梯
          ├─ --channel-sync                core/channel_sync.py（§5，可选前置）
          └─ 验证                          --check basic|advanced|full（§6）
```

`1kt.py` 自身仍是**薄编排器**：Sony/DJI 的硬件批量逻辑在
`core/batch_hw.py`（1752 行），`1kt.py` 里保留的 `encode_one_*` 系列
（`:338` / `:492` / `:707`）是 **x265 显式手动路径**，永不自动选中。

---

## 4. 源识别：两套机制，别混用

| 机制 | 位置 | 判据 | 用途 |
|---|---|---|---|
| **相机源识别** | `batch_hw.py:1323/1332` | 流清单特征（rtmd/djmd 等数据流） | 决定走哪条**保留管线** |
| **效率分类** | `core/source_classifier.py` | 归一化 OB（bits per pixel-frame） | 决定**缩放规则** |

效率分类阈值来自 `x265_scaling.json` / `svtav1_scaling.json` 的
`classification` 段（默认 `low_max=0.12`、`high_min=0.25`）：

```
INTRA_LIKE            仅当 codec 命中 intra_like_codecs（ProRes/DNxHD 等）
                      —— H.264/HEVC 在本阶段**永不**被判为 INTRA_LIKE
LOW_BITRATE_LONG_GOP  norm <  low_max
NORMAL_LONG_GOP       low_max <= norm < high_min
HIGH_BITRATE_LONG_GOP norm >= high_min
```

> 注意 `normalized_ob` 对 H.264/HEVC 只是**启发式效率指标，不是 GOP 检测**。
> 代码注释明确写了这一点；不要把它当码流结构分析用。

---

## 5. 元数据保留：Sony 与 DJI 是两条独立管线

| | Sony | DJI |
|---|---|---|
| 载荷 | **rtmd** 数据流（逐帧陀螺仪/镜头）、**nrtm** 镜头配置文件、**PROF/USMT** uuid box | **djmd** 数据流（运动四元数）、**dbgi**、**tmcd** 时码 |
| 消费端校验 | — | **Gyroflow** 逐帧四元数比对 |
| 实现 | `preservation/sony.py` + `preservation/pipeline.py:run_sony_pipeline()` | `preservation/dji.py` |
| 容器重建 | GPAC（`preservation/gpac.py`） | GPAC |
| 低位解析 | `preservation/isobmf.py`（752 行，自建 ISO-BMF 读写） | 同 |

### 5.1 Sony 管线的幂等/续跑设计（`preservation/pipeline.py:79`）

```
1. 抽取元数据 bundle → work_dir/metadata/manifest.json
      manifest 存在且 movie_timescale>0 且音轨列表完整 → 复用（跳过抽取）
      （旧 POC manifest 缺这些字段 → 视为不可用，重新抽取）
2. 视频编码（由调用方注入 encode_video 回调）
3. 用 bundle 重建容器，恢复 XAVC brand
4. 写出 work_dir/final/output.mov + report.json
5. 续跑判定：仅当 report.json 里 structural_success == True 才认为完成
      —— 失败运行的缓存报告**不得**阻止重试
```

### 5.2 codec 对保留策略的影响（重要边界）

`run_sony_pipeline(codec=...)` 接受 `"hevc"` 或 `"av1"`：

| codec | 元数据保留 | XAVC tag |
|---|---|---|
| `hevc` | 全量 | **恢复 XAVC brand**（合规路径） |
| `av1` | 全量（rtmd/nrtm/uuid） | **按策略不打** —— AV1 不在 XAVC 规范内 |

AV1 统一输出 4:2:0；4:2:2 源会 WARNING 后降采样。视频中间文件位置也不同：
HEVC 用 `video/encoded.mov`，AV1 用 `.mp4`（ffmpeg 9 的 MOV muxer 不接受 AV1）。

---

## 6. 音频延时补偿（channel-sync）

`core/channel_sync.py`（1038 行）+ `core/sync_estimate.py`（786 行）+
`core/mp4_channel_sync.py`（369 行）+ `core/sync_fix.py`。

P1 设计见 `docs/design/channel_sync_p1.md`，实现要点：

* **适用布局**：多流单声道 PCM（无线麦 CH1/CH2 + 有线 CH3/CH4），
  48k / 96k 支持，**44.1k 显式拒绝**；2ch/1ch 布局默认不对齐。
* **测量**：逐文件 GCC-PHAT 测各通道相对锚点的固定观测时差；
  锚点候选回退 `CH3 > CH4 > CH1 > CH2`。
* **修正**：**纯整数样本移位**，尾部补零保全长；**不做**漂移 resample、
  **不做**分数 sinc。
* **轨道级降级**：空轨 / 低置信 / 非恒定 / 超窗轨保持 `untouched`，
  **不阻止**其它健康轨同步（部分成功是设计行为）。
* **质量门**：门不过或复检超差 → 该轨原样 + 警告。
* 阈值集中在 `core/channel_sync.py::DEFAULTS`。
* 真实素材标定：`tests/fixtures/channel_sync/a7m5_real_137_baseline.csv`
  （137 段 A7M5 冻结基线）。

---

## 7. 编码后验证：`--check` 三级

| 级别 | Sony | DJI |
|---|---|---|
| `basic`（默认） | 时间线 / 轨清单 / rtmd 载荷 sha256 + 时序 + tref + timecode | 轨道清单 + djmd/dbgi/tmcd 载荷 sha256/size/样本数 + 音频流 + 帧数 |
| `advanced` | 完整结构校验 + Gyroflow | `basic` + Gyroflow 逐帧四元数 |
| `full` | `advanced` + 详细自检（64 项） | `advanced` + 逐轨时基/时长/载荷首尾字节/流级事实 |

`full` 会先探测 Gyroflow 是否安装，未安装则提示并跳过消费端对比
（`preservation/quality.py`、`preservation/selfcheck.py`、`preservation/checker.py`）。

---

## 8. 硬件解码：当前状态（与调研线的区别）

**生产状态：硬件解码未被使用。** 两个硬件后端都硬编码 `--avsw`：

| 后端 | 位置 | 内容 |
|---|---|---|
| NVEncC | `encoders/nvencc.py:107` | `"--avsw", "--video-track", "1", "-c", <codec>` |
| QSVEncC | `encoders/qsvencc.py:102` | 同上 |

注意 `encoders/nvencc.py:72` 的透传白名单里**留了 `"avhw"` 这个名字**，
但没有任何调用方会传它——这是历史遗留，不是可用开关。

**调研状态：已完成并封存。** P0-A 硬件解码调研由四个 research 分支完成，
交叉结论见 `docs/archive/hardware-decode/README.md`。要点：

* rigaya `--avhw` 的 Sony 丢帧**根因已定位到厂商特有 pipeline task**，
  两处补丁**均已运行时验证**（NVEncC 9.31 / QSVEncC 8.26 pinned）。
* FFmpeg `-hwaccel` 全程帧精确，保留为独立正确性/回退路径。
* **硬件解码收益是 CPU 余量，不是单任务提速**。
* **integration 未开始**，默认后端行为未改。

---

## 9. 输出布局与续跑

* 输出路径由 `core/paths.py::output_path_for()` 决定，`job_id_for()` 生成稳定
  任务标识（续跑依赖它）。
* 每个文件一个工作目录，含中间件、日志与 `report.json`。
* `--retry-list failed_files.json` 只重跑失败项（`load_retry_list()`）。
* 失败记录 `record_failure()` → 结构化失败清单，供重试与统计。
* `--fresh-log` 清空 `total.log`（默认**追加**）。
* `core/versions.py` 记录工具链版本，供复现与问题定位。

---

## 10. 必须保持的不变量

改代码时这些是硬约束，破坏它们等于破坏项目的核心承诺：

1. **输出目录不得位于输入目录之内**（`1kt.py:1384`，FATAL 退出）。
2. **同一轨道内硬件编码不得变成软件编码。** 降级梯只在同一后端内降级。
3. **保留失败不得静默成功。** Sony 管线只在 `structural_success == True` 时
   才认为完成；否则重建。
4. **`--channel-sync-transparent` 在"全部已对齐"时必须字节级等价于源文件。**
5. **档位 JSON 的数值是实测标定值，改动必须走测试集回归**
   （`tests/full_autotest.py`）。
6. **AV1 对 Sony 源不打 XAVC tag。** 元数据保留，但容器不声称 XAVC 合规。
7. **低于 1080p 的素材默认不处理**；channel-sync 只处理 ≥3 条独立单声道 PCM 轨。

---

## 11. 与历史文档不一致之处（以本文档为准）

| 历史说法 | 出处 | 实际 |
|---|---|---|
| "`core/` 54 文件、`encoders/` 24 文件、`preservation/` 46 文件" | `docs/README.md` 目录树 | **19 / 8 / 16** 个 `.py` |
| "`core/` 含 `sync_estimate` …" 但列表遗漏 `models.py`、`postprobe.py`、`dashboard_ui.py`、`mp4_channel_sync.py`、`version.py` | `docs/README.md` | 实际 19 个模块见 §12 |
| "NVEncC … ✅ 生产默认" | `README.md` 编码器矩阵 | v0.6.2 起为**能力优先自动选择**（NVENC→QSV→x265），无固定默认 |
| "硬件解码不可达（`--avsw` 字面量）" | 硬件解码 Phase 1 文档 | 结论仍成立，但**原因**是刻意的生产决策，不是"尚未接通" |

---

## 12. 文件地图

| 路径 | 行数 | 职责 |
|---|---|---|
| `1kt.py` | 1896 | 主入口：参数解析、后端解析、编排、x265 手动路径 |
| `watchfolder.py` | 92 | 轮询批处理转调 |
| `core/batch_hw.py` | 1752 | 硬件批量：降级梯、三条源路径、并发池、失败记录 |
| `core/channel_sync.py` | 1038 | 延时补偿主算法与阈值 |
| `core/sync_estimate.py` | 786 | GCC-PHAT 时差估计 |
| `core/logging_utils.py` | 493 | 分层日志 + 缩放 CSV |
| `core/probe.py` | 385 | 源探测 |
| `core/mp4_channel_sync.py` | 369 | MP4 音频轨重建 |
| `core/scaling.py` | 347 | 缩放规则引擎 |
| `core/config.py` | 312 | 工具链与 JSON 解析 |
| `core/versions.py` | 221 | 工具链版本记录 |
| `core/postprobe.py` | 155 | 编码后复探 |
| `core/sync_fix.py` | 154 | 样本移位实施 |
| `core/models.py` | 136 | PRESETS / EffectiveParams / SourceInfo |
| `core/dashboard.py` | 116 | 进度面板 |
| `core/color.py` | 107 | 色彩元数据处理 |
| `core/dashboard_ui.py` | 107 | 面板 UI |
| `core/paths.py` | 89 | 发现/命名/工作目录 |
| `core/source_classifier.py` | 76 | 效率分类 |
| `core/version.py` | 30 | 版本字符串 |
| `encoders/hw.py` | 395 | `plan_initial_format()`：能力 → 初始输出形态 |
| `encoders/x265.py` | 300 | x265 后端 |
| `encoders/svtav1.py` | 249 | SVT-AV1 后端 |
| `encoders/caps.py` | 215 | `--check-features` 能力探测 |
| `encoders/nvencc.py` | 145 | NVEncC 后端（`--avsw`） |
| `encoders/qsvencc.py` | 142 | QSVEncC 后端（`--avsw`） |
| `encoders/base.py` | 80 | 后端接口 |
| `preservation/isobmf.py` | 752 | ISO-BMF 底层读写 |
| `preservation/dji.py` | 693 | DJI 保留 |
| `preservation/selfcheck.py` | 556 | `--check full` 自检 |
| `preservation/sony.py` | 499 | Sony 保留 |
| `preservation/validate.py` | 486 | 验证 |
| `preservation/pipeline.py` | 401 | Sony 管线编排（幂等/续跑） |
| `preservation/quality.py` | 383 | 质量校验 + Gyroflow |
| `preservation/gpac.py` | 379 | GPAC 容器后端 |
| `preservation/checker.py` | 179 | `--check` 分级驱动 |
| `preservation/poc_video.py` | 148 | POC 视频处理 |
| `preservation/models.py` | 145 | PreservationBundle 等数据结构 |
| `preservation/gyroflow.py` | 138 | Gyroflow 定位与调用 |
| `preservation/colour.py` | 100 | 保留侧色彩处理 |
| `preservation/backends.py` | 57 | 后端选择 |
| `preservation/audio_sync.py` | 53 | 保留管线内的音轨同步 |
| `tests/full_autotest.py` | 2924 | 全量自动回归（档位改动的唯一依据） |
| `tests/run_selfcheck.py` | 189 | 自检驱动 |
| `tests/sony_selfcheck.py` | 21 | Sony 自检入口 |
| `release/build_release.py` | — | 发布包构建（allowlist） |
| `release/verify_package.py` | — | 发布包校验 |

> 行数为 `main` @ `15cf218` 实测值，随代码演进会漂移；结构以函数名与职责为准。

---

## 13. 相关文档

| 主题 | 文档 |
|---|---|
| 用法、编码器矩阵、发布包 | 根 `README.md` |
| 文档分类索引 | `docs/README.md` |
| 评估汇总与决策 | `docs/FINAL_REPORT.md` |
| 硬件后端设计定稿与踩坑 | `docs/design/hardware_backend_design.md` |
| 降级链与回退演练 | `docs/design/implementation_report.md` |
| channel-sync P1 设计 | `docs/design/channel_sync_p1.md` |
| HEVC 4:2:2 Rext 兼容矩阵 | `docs/design/hevc_422_rext_compatibility.md` |
| 后端选型评估 | `docs/evaluation/*` |
| 硬件解码调研（已封存） | `docs/archive/hardware-decode/` |
| 历史代码快照 | `olddocs/` |
