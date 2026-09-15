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
> **适用范围**：`v0.7.1`。改动入口或管线时请同步更新本文档。

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

这句话在本项目里有**三个不同的层次**，历史上被混用过：

| 层次 | 规则 | 实现位置 |
|---|---|---|
| **后端选择** | 自动选择可以选到 x265（因为压根没有硬件后端） | `resolve_default_backend()` |
| **单次运行** | 一旦选定硬件后端，**轨道内**绝不改成软件编码 | `hw_encode_with_fallback()` |
| **硬件解码** | 默认 `--hw-decode off` = **软解 `--avsw`**；`auto`/`require` 时由**能力路由**决定读者 | `encoders/hwdecode.py`、`core/batch_hw.py:243` |

第三行是 v0.7.0 起的实际状态（v0.7.0 已并入 `main`）：**默认仍是软解**，
硬件解码是**显式启用**的能力，且只在白名单内、且必须通过完整性闸门才被接受。
细节见 §8。

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

## 6.1 音频模型与 PCM 处理（v0.7.1 Phase 1 + Phase 2 + Phase 3A + Phase 3B + RC1）

**这一层不参与任何生产决策**：默认路径上没有任何调用方，也不产生 ffmpeg
命令。它的存在是为后续音频能力（选择 / 通道映射 / 混排 / WAVE 导出 /
MP4 音轨保留）提供一个稳定、可测试、可序列化的中间模型，并把 Phase 3 起的
PCM 处理放在一条**独立的执行链**上。

```
probe_source() 的 raw stream dict（每来源一次）
      │
      ▼
core/audio_probe.py            ← Probe 适配层（不新增 ffprobe 调用）
      │  build_audio_streams() / audio_probe_of() / audio_probe_from_file()
      ▼
core/audio_models.py           ← Model 层（纯数据，不导入项目内任何模块）
      AudioSource ─► AudioStream ──channels()──► AudioChannel (+ AudioSyncResult)
           │              │
           │        AudioTrackBuilder / AudioSourceBuilder
           │              ▼
           │        AudioTrack ──► AudioPlan
           ▼                              │
core/audio_plan.py            ← 规划层（Selection → Mapping → Validation → Spec）
      AudioPlanner ─► AudioOutputTrack ─► AudioMapSpec（dry-run，不执行 ffmpeg）
      ▲
      │  ChannelSyncReport.apply_to()  ← 只**读取** core/channel_sync 的报告 dict
      │
core/channel_sync.py           ← Sync 层（算法与阈值**零改动**）

──────────── Phase 3：执行链（只有显式调用 run_audio_render 才进入） ────────────

AudioPlan
      ▼
core/audio_timeline.py   AudioTimeline / RenderPolicy  ← **唯一**时长·EOF·offset 权威
      ▼
core/audio_pcm.py        AudioPCMReader                ← ffmpeg → canonical float32
      ▼
   ┌──┴─────────────────────────────┐
   │ 无混音意图                      │ 有混音意图 (mix_buses / mix_mode)
core/audio_route.py               core/audio_mix.py
   AudioRouter (1:1 纯搬运)          AudioMixer (Σ sample × gain, float32)
   └──┬─────────────────────────────┘
      ▼
core/audio_wav.py        WavExporter                   ← RIFF / PCM16·24·32 / float32
      ▼
core/audio_process.py    AudioOutputSpec + 处理图编排（run_audio_render）
```

**同一张图, 只换节点**: `AudioRouter` 与 `AudioMixer` 暴露相同的"逐块
float32 输出"接口, 因此 `WavExporter` 完全不需要知道自己在写路由结果还是
混音结果（WAV 与 Mixing 解耦）。

### 6.1.1 四个概念 + 三维身份

| 概念 | 定义 | 例子 |
|---|---|---|
| **source** | 一个物理音频来源（文件） | `camera.mp4` / `recorder.wav` |
| **stream** | 来源内的一条音频流 | `camera` 的容器 `index=2` |
| **channel** | 流内声道的物理位置（0-based） | `camera:s2:c2` |
| **track** | 逻辑输入轨 = (stream, channel 集合) | `channel_indices=[0,1,2,3]` |
| **output track** | 输出中的一个逻辑音频单元 | `out0` ← `camera:s2:c2` |

声道身份是**三维**的：`source_id + stream_index + channel_index`，字符串形式
`{source_id}:s{stream}:c{channel}`。**跨来源同名不碰撞**
（`camera:s0:c0` ≠ `recorder:s0:c0`）。

`audio_position`（来源内 0-based 音频序号 = `-map N:a:M` = channel_sync
报告的 `stream`）与容器 `stream_index` 是**两个字段**，绝不混用。

### 6.1.2 关键性质（测试已钉住）

1. **4CH 流不塌缩**——per-stream 建 1 个 track，但 4 个 `AudioChannel` 独立
   保留，`select_channels([2])` 可取单通道；
2. **同步不改身份**——`AudioChannel.sync` 写入（或经 selection/mapping）后
   `source_ids` 仍是完整的 `source:sN:cM` 链；
3. **未知即未知**——`channel_layout` 为空时按 `C0/C1/…` positional 命名，
   `sample_format` 缺失为 `unknown`，`role` 恒为 `unknown`（只能显式赋值）；
4. **WAV 不是另一套模型**——`AudioSourceType(media|wav|external)` 只影响
   读取方式，产出的都是同一套 `AudioStream` / `AudioChannel`；
5. **Selection ≠ Mapping ≠ Mixing**——选择决定"保留什么"，映射决定"按什么
   顺序输出"，两者都不做任何样本运算；任何 "N 源声道 -> 1 输出声道"
   返回 `audio_mix_not_supported`（本模块不存在增益/求和代码路径）；
6. **`AudioPlan` 默认计划可识别**——`is_default` 为真（全选 + `derived`
   映射 + `preserve_original`）时生产路径才继续走旧行为。

### 6.1.3 Validator reason codes（稳定契约）

`audio_source_not_found` / `audio_stream_not_found` /
`audio_channel_not_found` / `audio_source_duplicate` /
`audio_mapping_output_order` / `audio_mapping_not_selected` /
`audio_mapping_incomplete` / `audio_mix_not_supported`。
`build_map_spec(plan)` **不抛异常**（返回 `executable=False` + `errors`），
`require_audio_map_spec()` 才抛 `AudioValidationError`。

### 6.1.4 执行规格策略（§13 的两档）

| 情况 | 判定 | `AudioMapSpec.strategy` |
|---|---|---|
| 完整 stream 原样保留 | 输出段恰为某条流的完整且自然顺序全通道 | `stream_copy`（可 `-map` + `-c:a copy`） |
| 只取流内部分 channel / 重排 | 其余 | `channel_filter`（需声道过滤） |

k 条 mono 流的任意重排**仍是** `stream_copy`（每条流被整体保留，只是
`-map` 顺序不同）。本阶段只生成策略分类与身份，**不生成 filtergraph
字符串**、不执行 ffmpeg。

### 6.1.5 默认路径

`-map 0` + `-c:a copy`（经典路径）与 GPAC 音频复制（Sony/DJI）**未改动**：
`AudioPlan = None` 时行为与 v0.7.0 完全一致。外挂来源的跨媒体时间对齐、
Mixing、selective MP4 retention、重采样与漂移校正**均未实现**。

### 6.1.6 AudioTimeline：时长 / EOF / offset 的唯一权威（P3A）

`AudioRouter` / `WavExporter` / （P3B 的）Mixer **都不得**自行决定 EOF 或
输出长度；三者只消费 `core/audio_timeline.AudioTimeline`。

| 项 | 规则 |
|---|---|
| render window | `RenderPolicy.UNION`（**P3A 唯一启用**）= 参与输出源 timeline 的并集；`INTERSECTION` 为预留，显式给会 `audio_timeline_invalid` |
| duration 解析 | 显式 duration → source/stream duration 事实 → 按 policy 推导 → 否则 `audio_duration_unknown` **拒绝**（不用 file_size/bit_rate 猜） |
| 短 source | EOF 之后补 **`float32 0.0`**（不循环、不复制末样本、不写 NaN），长 source **不**因此截断 |
| 输出长度 | 严格 `= frame_count`（sample-accurate）；`chunk_frames` 不影响结果（回归钉 `byte-identical`） |
| 采样率 | 同一 render 必须同采样率，否则 `audio_sample_rate_mismatch`（**不偷偷 resample**） |
| offset | `timeline = source − offset`（**实测**确定：channel_sync 对晚到轨 → `shift_samples=+960`，修正后互相关 lag = −960）；只有 `status=success` 的固定整数 offset 被应用 |
| metadata 冲突 | 实际解码样本数是最终事实；不一致记 `audio_duration_metadata_mismatch`，派生窗口按实际重算 |

### 6.1.7 PCM 处理链要点（P3A）

* `core/audio_pcm.AudioPCMReader`：只消费既有 `AudioStream`，**不新建 ffprobe
  parser**；解码落 raw 临时文件后分块读（峰值内存 ≈ 1 chunk）。canonical 内部
  格式 = **float32**，线性 PCM 归一化到 `[-1,1]`（s16 ×2⁻¹⁵ / s24 ×2⁻²³ /
  s32 ×2⁻³¹ / f32 原样）。
* ⚠️ 解码命令带 `-af channelmap=0|1|…|N-1`：ffmpeg 默认按**声明布局**重排/下混
  （实测 4 声道 + `quad` 会把 FC/BC 折进 BL/BR），identity channelmap 强制
  "按位置逐声道复制"——**不插值、不混合、不改增益**。
* `core/audio_route.AudioRouter`：**1 输出声道 = 1 源声道**，纯搬运；按块只读所需
  源区间（按流合并），无重复 decode；**不存在**样本级相加路径。
* `core/audio_wav.WavExporter`：头部按实际字节数回填；≥3 声道写
  `WAVE_FORMAT_EXTENSIBLE`（含 channel mask），1/2 声道写经典头；自带小型
  writer（标准库 `wave` 无法表达 24-bit / IEEE float）；**不实现 mixing**。
* 命名集中在 `AudioOutputSpec` / `default_wav_name()`，同名冲突 `-2` 后缀不覆盖。

### 6.1.8 Phase 3A 的 reason codes（新增，稳定契约）

```text
audio_sample_rate_mismatch      audio_timeline_invalid
audio_negative_render_duration  audio_sync_offset_invalid
audio_duration_unknown          audio_duration_metadata_mismatch
audio_duration_explicit_invalid audio_decode_failed
audio_pcm_format_unsupported    audio_pcm_short_read
audio_output_invalid            audio_route_offset_unknown
audio_wav_write_failed          audio_mix_invalid
```

Phase 2 的 `audio_*_not_found` / `audio_mapping_*` / `audio_mix_not_supported`
语义不变：`build_audio_map_spec()` 仍以 `audio_mix_not_supported` 拒绝
`mix_mode`（`-map` 规格表达不了样本级合成）——它描述的是 ffmpeg argv,
与 PCM 层的 `core/audio_mix.py` **并存不冲突**。

⚠️ **两个 reserved reason code（已定义, 当前生产路径不可达）**：

```text
audio_sync_offset_invalid       # 常量已导出, 但 _effective_offset() 对非法
                                # offset 一律静默回退 0, 从不抛它
audio_pcm_format_unsupported    # audio_pcm 解码路径无此拒绝; 该字符串目前
                                # 由 core/audio_wav.read_wav/parse_wav_header
                                # 以字面量形式发出
```

它们**不是** active runtime error，不应据此编写分支逻辑。

### 6.1.9 PCM 混音：N→1 + gain + peak/clipping（P3B）

`core/audio_mix.py`：`MixBus` → `MixSink` → `MixGain`(源声道 + 线性增益)，
`AudioMixer` 逐块做 `sum = Σ(sample × gain)`。

* **图的选择**: 计划无 `mix_buses` 且 `mix_mode is None` -> `AudioRouter`
  (P3A 纯搬运); 否则 -> `AudioMixer`（`AudioPlan.is_default` 也要求
  `mix_buses` 为空）;
* **不重新定义 EOF**: render window 仍来自 `AudioTimeline`; 某个输入先 EOF
  时该项贡献**静音 0.0**，输出继续（1000 帧 + 400 帧 -> 输出仍 1000 帧）;
* **offset 走同一入口**: `source_to_timeline()`；晚到 60 的轨前移后与另一轨
  在同一 timeline 位置求和;
* **不自动 normalize**: `1.0 + 1.0 = 2.0` 保留并计为 clipping;
  `ClipPolicy` = `detect`(默认, 只统计) / `hard_clip`(显式裁剪) /
  `error`(`audio_mix_clipping`, 不产出文件)。**没有** `AUTO_NORMALIZE`;
* **检波统计** `MixStats`: `peak` / `peak_frame` / `peak_inputs`(峰值逐输入
  分解, Σ = peak) / `clip_count` / `format_clip_count` / `per_output_peak`;
* **增益不写回源声道**: 只在 `MixBus` 输入项上；负增益 -> `audio_mix_invalid`;
* 混音输出声道身份是 `mix{N}`, span 取各输入区间的**交集**
  (`mix_intersection`), 逐输入区间留在 warnings 里。

### 6.1.10 Phase 3B 的 reason codes（新增）

```text
audio_mix_clipping     audio_mix_invalid
```

### 6.1.11 输出权威与执行规格的分工（RC1 冻结）

**输出音频集合与顺序的唯一权威 = `AudioTimeline`**（`output_channel_ids` +
逐 `ChannelTimeline`），**包括混音图产生的 `mixN` 输出身份**。任何"最终写了
哪些输出声道、什么顺序"的问题，答案只在 `AudioTimeline` 一处：

| 图 | 输出身份来源 | 例 |
|---|---|---|
| routing | `resolve_timeline()` → `output_channel_ids` | `camera:s2:c2`（源声道身份） |
| mixing | `mixing_timeline()` → `output_channel_ids` | `mix0` / `mix1`（合成身份） |

**`AudioMapSpec` 不是"没用的遗留物"** —— 它是 **`-map` / stream-copy /
channel-filter 路径的执行规格**，仍然服务于：

```text
完整流原样保留        -> strategy = stream_copy   （-map + -c:a copy）
只取流内部分声道/重排  -> strategy = channel_filter（需声道过滤）
N 源声道 -> 1 输出声道 -> strategy = MIXING        （该路径**不可直接执行**）
```

`strategy == MIXING` 是**明确的转交信号**：`-map` argv 表达不了
`Σ(sample × gain)`，因此该计划必须转入 PCM processing graph
（`core/audio_process.run_audio_render`），而不是被当成"计划非法"。
`AudioMapSpec` 不因此失效：它定义的输出声道顺序由
`audio_plan.effective_mapping()` 提供，`AudioTimeline` 消费的正是同一个函数
（`core/audio_timeline._mapping_refs`），两者**不会分叉**。

⚠️ 两者的差异必须记住：`AudioMapSpec` 描述的是**ffmpeg argv 能做的那部分**；
`AudioTimeline` 描述的是**实际 PCM 输出**。Phase 4 起若需要"最终写进容器的
音频流集合"，读 `AudioTimeline`；若要生成 `-map` 参数，读 `AudioMapSpec`。

### 6.1.12 RC1 冻结边界（明确**不**保证兼容的部分）

以下条目在 v0.7.1-rc1 被显式标注为内部/预留。**不是缺陷清单**，而是
"Phase 4 不得依赖"的边界声明：

| 对象 | 状态 | 说明 |
|---|---|---|
| `AudioPlan.channel_map` / `AudioPlan.wav_outputs` | **reserved / 不保证兼容** | 字段已存在且参与 `is_default`，但**全项目零写入者**；语义未定，Phase 4 不得依赖，也不得据此推断行为 |
| `AudioOutputSpec.kind` | 当前唯一合法值 `"wav"` | 自由字符串、无枚举校验；Phase 4B 引入编码输出时可替换为 typed output-kind 抽象（届时为兼容变更） |
| `AudioMixer.output_format` | **internal** | 仅为"面向 WAV 的混音"提供 `WavFormat.clip_limit()`；Phase 4B 引入编码音频格式时该抽象会被替换 |
| `AudioMixer.frames()` | **INTERNAL** | 产出 `(block, faithful)`，与 `AudioRouter.frames()`（默认只产出 `block`）**契约不一致**；当前靠 `audio_process._iter_blocks()` 适配。不承诺对外稳定 |
| `AudioMixer.render_to()` | **INTERNAL / 当前无调用者** | 保留为便捷入口，无生产调用方 |
| `AudioPCMReader.read()` | **not thread-safe** | 共享 `self._handles` 并使用**绝对 `seek`**；当前 Router/Mixer 串行调用，因此现阶段无问题。Phase 5/6 并行化必须重新设计这一边界（不要直接并发调用） |
| `AudioPlan.notes` | **stable string only** | 只承诺"稳定字符串"（便于日志比对），**不是**结构化的机器可读元数据契约 |

### 6.1.13 序列化字段策略（自 v0.7.1 起）

```text
新增 serialized 字段:
    默认可选 —— 旧 JSON 缺失该键必须照常工作
    为默认值时 **不写出**（保持 schema 紧凑, 减少无意义 diff）

删除 serialized 字段:
    必须递增 AUDIO_MODEL_VERSION（core.audio_models）
    不做 migration framework, 也不静默改写旧 JSON
```

未知键与未知 enum 一律**容错**（`from_dict` 忽略未知键；`_StrEnum.coerce`
未知值回退安全默认），因此旧代码读新 JSON 不会崩，但也不会解释新语义 ——
这是刻意的：**宁可"未知"，不可"猜"**。

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

## 8. 硬件解码：v0.7.0 起已接入（默认仍为软解）

> **v0.7.0 已于 2026-09-14 并入 `main`**（merge commit
> `merge: integrate v0.7.0 hardware decode into main`），因此本节描述的是
> **当前主线行为**，不再是"调研线未接通"。research/Phase 1 的归档结论仍在
> [`olddocs/docs/hardware-decode/`](../olddocs/docs/hardware-decode/README.md)，
> integration 交付物在 [`docs/hardware-decode/`](../hardware-decode/README.md)。

### 8.1 策略：`--hw-decode off|auto|require`（默认 `off`）

| 策略 | 行为 |
|---|---|
| `off`（**默认**） | 软解。行为与 v0.6.2 **逐字节一致**：不尝试硬件解码，不改变任何默认 |
| `auto` | 输入命中 **runtime-proven 白名单**时用硬件读者，否则软解。**每次降级都出声**：WARNING + reason code 进主日志/单文件日志/report |
| `require` | 必须硬件解码；不可用则报错（`require_unmet`），**绝不静默降级** |

路由实现：`encoders/hwdecode.py::decide_route()`。它回答且只回答一个问题
——"这个输入、这个后端、这个策略下，**必须**用哪个解码读者，**为什么**"。
判定依据是**闭集白名单** `PROVEN`（`(backend, codec, chroma, depth)` 四元组，
每行必须引用一次实测），白名单外一律 `not_proven` → 软解。理由写在模块里：
这个 integration 存在的意义就是防止硬件路径**静默产出"看着对但实际错"的结果**，
而未测过的 profile 正是它出现的场景。

`REFUSED` 记录硬件读者**明确拒绝**的组合（如 QSV 的 H.264 4:2:2，`rc=-31`，
读者根本没被构造出来）——日志要说"refused"，不是"failed"。

### 8.2 完整性闸门：`encoders/integrity.py`

闸门存在的理由是一个真实缺陷：stock 硬件读者在 Sony 素材上只吐 `N−3` 帧，
`rc = 0`、无任何错误文本、文件完全可播放。只看"进程成功了吗"的闸门会放它过去
——事实上过去就是放过去的。

| 级别 | 触发 | 检查内容 | 代价 |
|---|---|---|---|
| `count` | 硬件解码开启时**恒开** | 五方帧数对账 + **读者身份断言** | 一次容器解析 + 两次**输出**解码 |
| `sequence` | `--hw-decode-verify` 显式开启 | 硬件与软件结果**逐帧有序指纹比对** | 多一次完整编码 |

规则是 research 线自己的结论：**以交付容器的实际内容为准，绝不以解码器/读者的
自述为准**。读者的 `N frames` 只作证据记录 —— XAVC 上它按构造就少报
（`FramePosList::setPocAndFix`），信它反而会掩盖要找的丢帧。

闸门不过 → **丢弃硬件产物 + 出声 + 改用软解重跑**（`HD-C11..C13`/`HD-F05`）。

### 8.3 `--seek` 拒绝（`seek_not_equivalent`）

实测（矩阵 HD-D02/D-04/D-05）：带时间 seek 时两个 rigaya 读者**不等价**，
且**两侧都确定性**——帧数与 PTS 序列相同，画面内容不同。补丁不背这个锅
（patched 与 stock `--avhw` 的 seek 输出逐字节相同），这是**既有的读者语义**，
research 线此前没测到，因为它只验过"seek 与 stock 一致"，从未验过
"seek 与 `--avsw` 一致"。

因此：**只要请求了 `--seek`，路由直接拒绝硬件解码**（`seek_not_equivalent`），
走软解（软解在每个 seek 位置上都是精确的）。`--trim` 是读者等价的（逐字节相同），
不受此限制。显式 `--seek 0` 与不 seek 无差别（HD-D06）。

### 8.4 binary provenance（`docs/hardware-decode/toolchain-provenance.json`）

`tests/hwdecode/sources.py::verify_provenance()` 在**任何测试执行前**运行，
逐项校验 sha256 + 版本行 token；**不符即 FAIL，不是 warning**。三类二进制：

* `hardware_decode`：`tools/avhw/` 下的**补丁版**（research build，**不得分发**）
* `software_control`：同一个补丁版跑 `--avsw`
* `stock_control`：`tools/` 下的 shipped 版 —— **负对照**，用来复现 N−3 / N−2 头部丢帧

默认路径必须解析到 shipped build，硬件解码必须解析到 patched build：两个 build
只在行为上不同，误换后要**到丢帧才发现**（`HD-A09`/`HD-B10`）。

### 8.5 与发布包的关系（边界声明）

`tools/avhw/` 与 `docs/` 都不进发布包（`release/build_release.py` 白名单制），
因此在**发布安装**中：

* `--hw-decode auto` 会以 `not_proven` 降级到软解；
* `--hw-decode require` 会明确失败。

**这是设计行为，不是缺陷。** 引用补丁结论时必须带边界：NVEncC 补丁仅在
9.31（`2cb9d810`）验证；QSVEncC 补丁 **runtime-proven on 8.26 pinned
revision, not a general claim for later releases**（8.27–8.30 未检验）。

### 8.6 测试矩阵

`tests/hwdecode/`（83 个用例，A–K 十一类），入口见
[`docs/hardware-decode/README.md`](../hardware-decode/README.md)。三条硬规则：

1. **`exit code = 0` 不是正确性证据**（上面那个 N−3 就是 `rc=0`）。
2. **读者身份必须从工具日志读，不能从命令行推断** —— QSVEncC 在硬件不可用时会
   **静默构造 avsw**，把它记成 hardware pass 等于把整个特性做成假的。
3. **`--frames` 是编码器输入帧数契约，不是解码器输出帧数**（HD-D07/D08）；
   两者混用会让闸门误判。

---

## 9. 输出布局与续跑

* 输出路径由 `core/paths.py::output_path_for()` 决定，`job_id_for()` 生成稳定
  任务标识（续跑依赖它）。
* 每个文件一个工作目录，含中间件、日志与 `report.json`。
* `--retry-list failed_files.json` 只重跑失败项（`load_retry_list()`）。
* 失败记录 `record_failure()` → 结构化失败清单，供重试与统计。
* `--fresh-log` 清空 `total.log`（默认**追加**）。
* `core/versions.py` 记录工具链版本，供复现与问题定位。

### 9.1 ⚠️ `tools/` 的位置不可变（踩过的坑）

`tools/`（ffmpeg/ffprobe、NVEncC、QSVEncC、GPAC，约 1.3 GB）**被 `.gitignore` 排除**，
即**不受 git 保护**：删除不进回收站、不留记录，缺失时的症状只是"报错找不到 ffmpeg"。

> **2026-09 两次真实事故**：为让 research 工作树共享工具链，在工作树里建了
> `tools` → 主 `tools/` 的 **junction**；随后 `git worktree remove --force`
> 递归删除工作树，把工作树里的**工具链实体副本**一并删除，主 `tools/` 变空。

三条硬规则：

1. **工具链只存在 `F:\1KeyTranscoder\tools\` 一处**，不在任何工作树/临时目录复制或链接它。
   跨位置引用用参数：`--tool-nvencc` / `--tool-qsvencc` / `--ffmpeg` / `--ffprobe` / `--gpac-dir`。
2. **不要用 `Move-Item` 移动 junction**（跟随语义，移动的是目标内容）；动带链接的目录前
   先 `Get-Item <路径> -Force | Select LinkType,Target`。
3. **`git worktree remove --force` 会删除该工作树下的全部内容**（含未跟踪文件）。
   执行前用 `git status --ignored` 列一遍，确认没有要紧东西。

根 `README.md` §依赖 有更完整的说明。

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
8. **（v0.7.1）音频模型不得改变默认音频路径。** `AudioPlan` 未显式接入输出
   决策时，经典路径仍是 `-map 0` + `-c:a copy`，Sony/DJI 仍是 GPAC 音频复制。
9. **（v0.7.1）同步不得破坏原始 stream/channel 身份。** 任何 track 经
   channel-sync 结果回填后，`source_ids` 仍必须能追溯回
   `(stream_index, channel_index)`。
10. **（v0.7.1）未知不得伪装成已知。** `channel_layout` 为空/未知不丢流、
   不冒充标准声道语义；`sample_format` 未知即 `unknown`；`role` 只能显式
   赋值（且必须给 reason），不做任何自动推断。

11. **（v0.7.0）硬件解码默认关闭。** `--hw-decode` 默认 `off`，即 v0.6.2 行为；
   硬件解码只在 `auto`/`require` **且命中 `PROVEN` 白名单**时使用，并且
   **必须**通过完整性闸门才被接受。白名单外一律 `not_proven` → 软解，
   **不得**因为「看起来能用」而放行未测过的 profile。
12. **（v0.7.0）读者身份以工具日志为准，不得从命令行推断。** QSVEncC 在硬件
   不可用时会静默构造 `avsw`；把它记成 hardware pass 等于把整个特性做成假的。
13. **（v0.7.0）请求了 `--seek` 就不得用硬件解码**（`seek_not_equivalent`）：
   两个 rigaya 读者在时间 seek 上不等价（帧数与 PTS 相同、画面不同），
   且补丁不背这个锅（patched 与 stock 输出逐字节相同）。`--trim` 不受限制。
14. **（v0.7.0）binary provenance 不符即 FAIL，不是 warning。**
   `tests/hwdecode/sources.py::verify_provenance()` 在任何测试执行前运行，
   逐项校验 sha256 + 版本 token。
15. **（v0.7.0）`--frames` 是编码器输入帧数契约，不是解码器输出帧数。**
16. **（v0.7.1 P2）Selection / Mapping / Mixing 三者不得混淆。** 选择决定
    「保留什么」，映射决定「按什么顺序输出」，两者都不做样本运算；任何
    「N 源声道 -> 1 输出声道」必须返回 `audio_mix_not_supported`。
17. **（v0.7.1 P2）跨来源身份不得碰撞，且不得丢来源维度。** 声道身份是
    `source_id + stream_index + channel_index`；选择与映射**只改**
    `AudioPlan` 的选择/映射字段，绝不改 `AudioSource` / `AudioStream` /
    `AudioChannel` 的原身份与 `sync`。
18. **（v0.7.1 P2）默认计划必须可识别。** `AudioPlan.is_default` 为真时
    （全选 + `derived` 映射 + `preserve_original`），生产路径继续走旧行为，
    不得因为引入音频规划而自动进入 filtergraph。
19. **（v0.7.1 P3A）时长/EOF 只有一个权威。** `AudioRouter` / `WavExporter` /
    Mixer 都不得自行决定 render duration 或 EOF：一律来自
    `core/audio_timeline.AudioTimeline`。输出样本数严格 `= frame_count`，
    `chunk_frames` 不得影响结果。
20. **（v0.7.1 P3A）缺数据一律是确定性静音。** 短 source 的缺失区间补
    `float32 0.0`（**不是** NaN、**不**循环、**不**复制末样本）；长 source
    不因为别的 source EOF 而截断。
21. **（v0.7.1 P3A）offset 只在唯一入口换算，方向由实测钉死。**
    `timeline = source − offset`（`source_to_timeline()`）；只有
    `status=success` 的固定整数 offset 被应用，**不重新估计 delay**
    （不重写 GCC-PHAT）。`core/channel_sync.py` 的算法与阈值一字不改。
22. **（v0.7.1 P3A）解码不得隐式重排/混音声道。** 解码命令行必须带 identity
    `channelmap`；canonical 中间格式是 float32，满量程归一不得改变原始数据
    语义（sample_rate / 声道数仍由 source model 给出）。
23. **（v0.7.1 P3A）不偷偷 resample。** 同一 render 内采样率不一致即
    `audio_sample_rate_mismatch` 拒绝。
24. **（v0.7.1 P3A）WAV 头部必须准确。** `data_size` / `riff_size` 按实际写出的
    字节数回填；写入样本数与声明不符 → `audio_wav_write_failed` 且不留半成品。
25. **（v0.7.1 P3B）Mixing 不得重新定义 EOF / 时长。** 混音与路由共用
    `AudioTimeline`；短输入在混音里同样按 §EOF 策略补静音，不终止整个 render。
26. **（v0.7.1 P3B）不自动归一化。** 超范围样本按明确的 `ClipPolicy` 处理
    （`detect` / `hard_clip` / `error`），并如实记录 `clip_count`；
    增益只能是 `MixBus` 上的有限非负线性系数，绝不写回源声道模型。
27. **（v0.7.1 P3B）WAV exporter 不实现 mixing。** 混音与导出解耦：路由图与
    混音图暴露同一"逐块 float32"接口，只换节点。
28. **（v0.7.1 RC1）输出权威只有 `AudioTimeline`。** 混音图的输出声道身份是
    `mixN`（不是源声道），任何"逐输入事实"统计都必须查**未混音的 base
    timeline**（`AudioRenderResult.silence_samples` 曾因传错 timeline 而恒报
    满，见 §6.1.11）。`AudioMapSpec` 只描述 `-map` / stream-copy /
    channel-filter 路径，`strategy == MIXING` 表示必须转入 PCM 图。
29. **（v0.7.1 RC1）冻结边界必须写明。** §6.1.12 列的内部/预留对象
    （`AudioPlan.channel_map` / `wav_outputs`、`AudioOutputSpec.kind`、
    `AudioMixer.output_format` / `frames()` / `render_to()`、
    `AudioPCMReader.read()` 的线程安全边界）不得被当作对外契约；
    序列化字段的新增/删除遵循 §6.1.13。

---

## 11. 与历史文档不一致之处（以本文档为准）

| 历史说法 | 出处 | 实际 |
|---|---|---|
| "`core/` 54 文件、`encoders/` 24 文件、`preservation/` 46 文件" | `docs/README.md` 目录树 | **28 / 10 / 16** 个 `.py`（v0.7.0 + v0.7.1 并入后实测） |
| "`core/` 含 `sync_estimate` …" 但列表遗漏 `models.py`、`postprobe.py`、`dashboard_ui.py`、`mp4_channel_sync.py`、`version.py` | `docs/README.md` | 实际 28 个模块见 §12（v0.7.1 新增 `audio_models.py` / `audio_probe.py` / `audio_plan.py` / `audio_timeline.py` / `audio_pcm.py` / `audio_route.py` / `audio_wav.py` / `audio_process.py` / `audio_mix.py`） |
| "NVEncC … ✅ 生产默认" | `README.md` 编码器矩阵 | v0.6.2 起为**能力优先自动选择**（NVENC→QSV→x265），无固定默认 |
| "生产路径恒用 `--avsw` 软解" / "硬件解码不可达（`--avsw` 字面量）" / "integration 未开始" | 本文档 §2.1、§8（v0.6.2 版）与硬件解码 research 归档 | **已被 v0.7.0 推翻（v0.7.0 已于 2026-09-14 并入 main）**：硬件解码已接入 `--hw-decode off\|auto\|require`，带 runtime-proven 白名单、完整性闸门与 `--seek` 拒绝。**默认仍是 `off` = 软解**，所以"默认行为未改"这一半仍然成立。以本版 §8 为准 |
| "音频模型只有 Phase 1（4CH 已是全部场景）" | 本档 §6.1（v0.7.1 P1 版） | **已被 Phase 2/3A 扩展**：Phase 2 新增 `AudioSource`（media/wav/external）、三维声道身份、Selection、Channel Mapping、`AudioOutputTrack`、`AudioMapSpec`；Phase 3A 新增 `AudioTimeline`（时长/EOF/offset 唯一权威）、`AudioPCMReader`（canonical float32）、`AudioRouter`（无混音）、`WavExporter`、`AudioOutputSpec`。Phase 1 的「4CH 流不塌缩」「同步不改身份」等结论仍然成立 |
| 各文档中 "当前版本 `v0.6.2`" | 根 `README.md` 等 | `main` 现为 **v0.7.1**，且已含 v0.7.0 hardware-decode integration（`v0.7.0 ∈ ancestors(main)`）；`v0.6.x` 的说法仅描述 v0.6 线的能力基线 |

---

## 12. 文件地图

| 路径 | 行数 | 职责 |
|---|---|---|
| `1kt.py` | 1923 | 主入口：参数解析、后端解析、编排、x265 手动路径 |
| `watchfolder.py` | 92 | 轮询批处理转调 |
| `core/batch_hw.py` | 1966 | 硬件批量：降级梯、三条源路径、并发池、失败记录、**硬件解码接线与完整性闸门调用** |
| `core/channel_sync.py` | 1038 | 延时补偿主算法与阈值 |
| `core/audio_models.py` | 2158 | **v0.7.1** 音频模型：Source/Stream/Channel/Track/Plan/Sync（纯数据） |
| `core/audio_plan.py` | 1771 | **v0.7.1 P2** 规划层：Selection / Channel Mapping / Validation / AudioMapSpec / **公开 `effective_mapping()`**（输出顺序定义，Timeline 与 spec 共用） |
| `core/audio_probe.py` | 328 | **v0.7.1** 音频 Probe 适配层（raw stream → 模型，含来源维度） |
| `core/audio_timeline.py` | 1212 | **v0.7.1 P3A** AudioTimeline / RenderPolicy / EOF·offset 唯一权威 |
| `core/audio_pcm.py` | 665 | **v0.7.1 P3A** PCM Reader：ffmpeg → canonical float32（chunked） |
| `core/audio_route.py` | 530 | **v0.7.1 P3A** 通道路由：纯样本搬运（无混音） |
| `core/audio_wav.py` | 935 | **v0.7.1 P3A** WAV writer/reader（PCM16·24·32 / float32 / EXTENSIBLE） |
| `core/audio_process.py` | 685 | **v0.7.1 P3A/P3B** 处理图：AudioOutputSpec + run_audio_render 编排（路由/混音换节点；静音统计走 base timeline） |
| `core/audio_mix.py` | 832 | **v0.7.1 P3B** PCM 混音：MixBus/MixSink/MixGain + AudioMixer（float32 累加 / 检波 / ClipPolicy） |
| `core/sync_estimate.py` | 786 | GCC-PHAT 时差估计 |
| `core/logging_utils.py` | 493 | 分层日志 + 缩放 CSV |
| `core/probe.py` | 391 | 源探测（v0.7.1 起 `-show_entries` 增加 `stream_tags`） |
| `core/mp4_channel_sync.py` | 369 | MP4 音频轨重建 |
| `core/scaling.py` | 347 | 缩放规则引擎 |
| `core/config.py` | 324 | 工具链与 JSON 解析（v0.7.0 起含硬件解码 binary 解析） |
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
| `encoders/integrity.py` | 539 | **v0.7.0** 硬件解码完整性闸门（count / sequence 两级） |
| `encoders/hwdecode.py` | 428 | **v0.7.0** 硬件解码能力路由 + 白名单 + reason codes |
| `encoders/hw.py` | 395 | `plan_initial_format()`：能力 → 初始输出形态 |
| `encoders/x265.py` | 300 | x265 后端 |
| `encoders/svtav1.py` | 249 | SVT-AV1 后端 |
| `encoders/caps.py` | 215 | `--check-features` 能力探测 |
| `encoders/nvencc.py` | 162 | NVEncC 后端（读者由 `--hw-decode` 决定，默认 `--avsw`） |
| `encoders/qsvencc.py` | 157 | QSVEncC 后端（同上） |
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
| `tests/hwdecode/harness.py` | 4256 | **v0.7.0** 硬件解码集成测试矩阵驱动与全部用例实现 |
| `tests/hwdecode/probe.py` | 629 | 容器/读者探针（帧数、PTS、指纹、读者身份） |
| `tests/hwdecode/checks.py` | 568 | 矩阵断言原语 |
| `tests/hwdecode/runners.py` | 405 | 编码调用封装（reader/seek/trim/frames 契约） |
| `tests/hwdecode/sources.py` | 239 | **provenance 校验**（sha256 + 版本 token，不符即 FAIL） |
| `tests/hwdecode/fixtures.py` | 238 | control fixtures 生成 |
| `tests/hwdecode/inventory.py` | 80 | 语料盘点 + fixture 刷新 |
| `tests/hwdecode/matrix.json` | 351 | **机器可读矩阵**（83 用例，与 harness 双向漂移检查） |
| `tests/full_autotest.py` | 6505 | 全量自动回归（档位改动的唯一依据） |
| `tests/run_selfcheck.py` | 189 | 自检驱动 |
| `tests/sony_selfcheck.py` | 21 | Sony 自检入口 |
| `release/build_release.py` | — | 发布包构建（allowlist） |
| `release/verify_package.py` | — | 发布包校验 |

> 行数为 `main` @ 2026-09-14（v0.7.0 hardware decode + v0.7.1 音频模型并入后）
> 实测值，随代码演进会漂移；结构以函数名与职责为准。

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
| **硬件解码 integration（已并入 main）** | [`docs/hardware-decode/`](../hardware-decode/README.md) ★ 主交付物 `integration-test-matrix.md` |
| 硬件解码 research（已封存） | `olddocs/docs/hardware-decode/` |
| v0.7.1 音频模型（Phase 1 + Phase 2 + Phase 3A） | [`docs/release_notes_v0.7.1.md`](../release_notes_v0.7.1.md) |
| 历史代码快照 | `olddocs/backup/` |
