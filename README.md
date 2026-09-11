# 1KeyTranscoder

递归、可断点续跑的 Windows 批量归档转码器，带 **Sony / DJI 双相机元数据保留**：
- **Sony XAVC**（rtmd 数据流）：逐帧陀螺仪/镜头数据（rtmd）、镜头配置文件
  （nrtm）、厂商 uuid box（PROF/USMT）全量保留；
- **DJI**（djmd 数据流，Osmo Action 系列 / 无人机）：djmd 运动四元数 +
  dbgi + tmcd 时码原生保留，Gyroflow 逐帧消费端校验。

编码后端：**NVEncC / QSVEncC 硬件编码**（HEVC 与 AV1；解码恒软解，硬件
路径永不回退软件）、**SVT-AV1 软件 AV1**、**x265 手动高压缩档**；
**未指定 `--encoder` 时按能力优先自动选择：NVENC → QSV → x265**
（v0.6.2 起；加 `--no-hw-autoselect` 可固定为 x265，即 v0.6.1 及更早的行为）。
注意 `--config` 里的 `encoder` 字段优先级最高，会覆盖自动选择。
**当前版本 `v0.6.2`**（HEVC/265 与 AV1 合并主线）。
**主入口：`1kt.py`。**

> 📚 文档索引见 [docs/README.md](docs/README.md)；评估汇总与决策见
> [docs/FINAL_REPORT.md](docs/FINAL_REPORT.md)。

## 编码器矩阵

| 后端 | 状态 | 档位 JSON | 定位 |
|---|---|---|---|
| NVEncC（NVIDIA NVENC HEVC） | ✅ 生产默认 | `nvenc.json` | 主后端。本机 5070 Laptop：HQ 4K60 ≈ 23fps，4:2:2 直编 Rext 保真 |
| QSVEncC（Intel QSV HEVC） | ✅ 生产 | `qsv.json` / **`qsv_aligned.json`** | 第二后端。`qsv_aligned.json` 为按 NVENC 同档质量标定的对齐版（见下） |
| x265（软件 HEVC） | ✅ 手动高压缩档（P0 修复完成） | `x265.json` + `x265_scaling.json` | 质量优先冷归档 / 4:2:2 保真唯一软件路径（吞吐受限，缩放规则仍 PROVISIONAL，见 `work/x265_test/`） |
| VCEEncC（AMD VCE HEVC） | 预留（JSON 已备未接） | `vce.json` | AMD 机器扩展 |
| **SVT-AV1（软件 AV1）** | ✅ 已实施（四档标定完成，见评估） | `svtav1.json` + `svtav1_scaling.json` | `--encoder svtav1`；ffmpeg 9.0.1 内置 SVT-AV1 v4.2.0；Sony/DJI 元数据保留管线；软件 AV1 体积/细节优势（档位对标 x265 判定见 `docs/evaluation/av1_calibration.md`） |
| **AV1**（NVENC/QSV 硬件） | ✅ 已实施 | `nvenc_av1.json` / `qsv_av1.json` | 免版税备选；`--encoder nvenc-av1\|qsv-av1`；Sony 源同样走保留管线（不打 XAVC tag） |

> ⚠️ 档位 JSON 内的数值为作者实测标定值，请勿改动；调参须以测试集回归
> 与 `tests/full_autotest.py` 为依据。
>
> AV1 三后端（SVT-AV1 / NVENC-AV1 / QSV-AV1）已通过合并后阶段验收：
> 真实 Sony 素材上的 Sony 元数据保留、4:2:2→4:2:0 策略、AV1 MP4 容器、
> 色彩元数据保真、以及 `--channel-sync` 组合回归（27 case / 348 断言全绿，
> 全量短回归 228 PASS / 0 FAIL）。

## 下载（自包含发布包）

无需自行搭建工具链：发布包内置 **ffmpeg/ffprobe 9.0.1（libsvtav1
v4.2.0/libx265/libvmaf）+ NVEncC 9.31 + QSVEncC 8.26 + GPAC 26.02**，
解压即用（另需 Python 3.11+ 与对应 GPU 驱动；Gyroflow 为可选消费端
工具，请从 gyroflow.xyz 单独安装）。

- **v0.6.1**（`main` 主线 · **当前正式发布** · bugfix release）：
  [1KeyTranscoder-v0.6.1-win64-selfcontained.zip](https://github.com/Eureka175/1KeyTranscoder/releases/download/v0.6.1/1KeyTranscoder-v0.6.1-win64-selfcontained.zip)
  —— 修复 `--channel-sync` 长素材内存无上限增长（10 min/4CH/48 kHz 峰值
  RSS ≤ 512 MB 已实测达标），并含 AV1 色彩元数据保真修复；发布说明见
  [docs/release_notes_v0.6.1.md](docs/release_notes_v0.6.1.md)
- **v0.6.0**（`main` 主线 · HEVC/265 + AV1 合并后首个版本 + AV1 色彩
  元数据保真修复）：该版本**长程 channel-sync 内存问题未修复**（10 min/4CH
  峰值 RSS 达 605–713 MB），**不建议用于长素材的 `--channel-sync`**，请用
  v0.6.1。**注意：历史上未发布 v0.6.0 的独立发布包；仓库里 `v0.6.0` 这个
  tag 指向的是发布基建（`VERSION` / `release/` / `--version`）落地之前的
  mainline commit**，与本节描述的能力不完全对应——需要复现合并后能力请用
  `v0.6.1`（或 `main` 上的 `f721b1f` 及其后提交）
- **v0.5.1**（AV1 线 · 软件 + 硬件 AV1；tag 现已在 `main` 历史中）：
  [1KeyTranscoder-v0.5.1-win64-selfcontained.zip](https://github.com/Eureka175/1KeyTranscoder/releases/download/v0.5.1/1KeyTranscoder-v0.5.1-win64-selfcontained.zip)
- **v0.4.2**（HEVC/265 线）：
  [1KeyTranscoder-v0.4.2-win64-selfcontained.zip](https://github.com/Eureka175/1KeyTranscoder/releases/download/v0.4.2/1KeyTranscoder-v0.4.2-win64-selfcontained.zip)

> 版本线：`v0.4.x` = HEVC/265 线，`v0.5.x` = AV1 独立线，**`v0.6.x` =
> 两条线合并进 `main` 后的主线**（AV1 与 HEVC 同处一分支，共用一个入口与
> 一套保留管线）。`v0.6.1` 是 `v0.6.0` 的缺陷修复版本，无功能新增。
> `v0.6.1` 是 v0.6 线上**唯一带独立发布包**的版本。

## 快速开始

```powershell
# NVENC, HQ 档, 默认验证强度 (basic)
python 1kt.py --input D:\素材 --output D:\归档 --encoder nvenc --preset hq

# QSV 后端（质量与 NVENC 对齐版）
python 1kt.py --input D:\素材 --output D:\归档 --encoder qsv --config qsv_aligned.json --preset hq

# 全档位 (UHQ/HQ/SMALL/FAST)
python 1kt.py --input D:\素材 --output D:\归档 --encoder nvenc --preset all

# x265 手动高压缩档（软件，慢）
python 1kt.py --input D:\素材 --output D:\归档 --encoder x265 --preset hq

# 软件 AV1（SVT-AV1，元数据保留管线，不打 XAVC tag）
python 1kt.py --input D:\素材 --output D:\归档 --encoder svtav1 --preset hq

# AV1 硬件档（Sony/DJI 均走保留管线, 不打 XAVC tag）
python 1kt.py --input D:\素材 --output D:\归档 --encoder nvenc-av1 --preset hq

# 自动延时补偿（无线麦 CH1/CH2 相对有线参考的逐文件观测时差, 自动测量+整数样本修正）
python 1kt.py --input D:\素材 --output D:\归档 --encoder nvenc --preset hq --channel-sync

# 透明模式（剪辑前预处理: 跳过视频编码, 视频/非音频流 stream copy, 仅修音频轨）
python 1kt.py --input D:\素材 --output D:\归档 --channel-sync-transparent

# 无人值守 (不弹看板窗口, 全部落日志)
python 1kt.py ... --headless
```

**三条自动路径**：Sony XAVC（rtmd）→ 元数据保留管线（AV1 后端同样
保留 rtmd/nrtm/uuid，但按策略不打 XAVC tag — AV1 不在 XAVC 规范内）；
DJI（djmd）→ DJI
保留管线（视频重编码 + djmd/dbgi/tmcd 原生复制 + 载荷 sha256 + Gyroflow
四元数校验；mjpeg 封面与 udta 因 GPAC 26.02 不可寻址而丢弃并显式记录）；
其余素材 → 经典单趟（按策略仅视频+音频，日志显式声明）。输出为同名
`.MP4`，保留目录结构。

## 依赖

| 组件 | 说明 |
|---|---|
| GPAC / MP4Box | `C:\Program Files\GPAC`（或 `--gpac-dir`）——容器重建与元数据保留核心（**行为绑定 26.02**，升级须回归） |
| NVEncC / QSVEncC | `tools/NVEncC_9.31_x64/`、`tools/QSVEncC_8.26_x64/`（或 `--tool-*`） |
| ffmpeg / ffprobe | 9.0.1 gyan full（tools/ 自带，内置 libx265/libsvtav1 v4.2.0/libvmaf；**必须用项目自带版本**，PATH 老版本不支持 AV1 新特性） |
| Gyroflow（可选） | 消费端校验（`--check advanced/full`；未安装则提示并跳过） |
| numpy / scipy（可选） | 仅 `--channel-sync` 延时补偿需要（缺失时该功能跳过并 WARNING，转码不受影响） |

## 自动延时补偿：`--channel-sync`（P1）

无线麦克风（CH1/CH2）经数字无线链路相比有线通道（CH3/CH4）存在逐文件
变化的固定微延迟（实测 19.7–29.5 ms）。P1（`algo 2.3.0-p1`）开启后对
每条音轨自动执行 **GCC-PHAT 两阶段测量（8kHz 粗扫 + 全速率精测）→
相位斜率精估 → 轨道级质量门 → 纯整数样本移位 → 复检**，全程无人工
常数；锚点按候选顺序 **CH3 > CH4 > CH1 > CH2 自动回退**（CH3 只是
默认优先级最高，不是"真值基准"）：

- **支持 48kHz / 96kHz；44.1kHz 显式拒绝**（有意的范围收窄，不会
  silently 跑旧算法）；codec 支持线性 PCM 八类（s16/s24/s32/f32 ×
  大小端，用户决定：大小端无所谓都支持 — 实测 A7M5 XAVC-S 为大端
  s24be），压缩/非线性格式（aac/alaw 等）拒绝
- **适用布局**：≥3 条独立单声道 PCM 流；**2ch（立体声）/1ch（单声道）
  布局默认不做对齐**（用户决定）
- **默认修正 = 纯整数样本移位**（48k 下最大量化残差 0.5 sample ≈
  10.4 µs），无滤波、无插值、样本值不变；尾部补零保全长（轨道时长
  与源一致，Sony/DJI 结构校验不受影响）
- **轨道级部分成功**：空轨/静音轨（`silent_track`）、NaN/Inf
  （`non_finite`）、低置信（`low_confidence`）、非恒定
  （`non_constant`）、超搜索窗（`out_of_range`）、复检超差
  （`recheck_residual`）的轨一律 `untouched` 原样保留，**不阻止其它
  健康轨同步**；文件级失败（无有效锚点/全部健康轨均不可靠）才整文件
  原音频不动
- **恒定性**只做 constant/non_constant 二分（MAD + 极差 + 漂移 ppm），
  不做漂移 `resample`；P1 不提供 fractional sinc 生产模式
- 质量门不过或复检超差 → 该轨原样 + 显著 WARNING，绝不静音或乱移
- 三条路径（Sony 保留 / DJI 保留 / 经典）均接入；测量报告
  `channel_sync_<名>.json` 落盘（含 `decision/reason/shift_samples/
  fine_delay_ms` 等逐轨字段，`result_scope = file|partial`）
- **语义约定**：报告中的 delay 是**当前文件内的观测轨间时差**（可含
  电子/无线链路延迟、录音链路差与麦克风物理位置的声学传播差），
  不自动等价于设备 latency；不同物理位置的麦克风不禁止同步，但相关
  性不足时安全放弃该轨
- 阈值集中在 `core/channel_sync.py::DEFAULTS`（注释"初值, 待真实素材
  标定"），可经档位 JSON 的 `channel_sync` 节覆盖
- **真实素材标定（137 段 Sony A7M5 4CH）**：已对齐 111 文件 / 实际修正
  11 文件 / 测量失败 15 文件；**11/11 修正轨算法复测残差 0.00 样本**
  （9 段大修正 905–1222 样本 = 18.9–25.5 ms，2 段小修正 −37/−39 样本 =
  −0.77/−0.81 ms）；真实慢漂移（39–76 ppm，总漂移 0.4–2 ms）与弱相关轨
  被安全拒修。冻结基线：`tests/fixtures/channel_sync/a7m5_real_137_baseline.csv`
  （逐轨 548 行，仅文本，不含媒体）。注意 `detail`/`reason` 是诊断口径，
  不构成互斥的顶层状态，不可直接相加
- **长程性能（v0.6.1 实测达标）**：10 分钟 / 4 轨 / 48 kHz / 单线程
  benchmark 实测 **Peak RSS 153–288 MB**、**Runtime 45.3–52.7 s**
  （5 次运行，进程树 500 ms 采样；达标线 **RSS ≤ 512 MB / Runtime < 60 s**。
  离散度主要来自机器后台负载——同一构建在后台服务繁忙时段曾测得 59.2 s，
  仍在门限内）。峰值 RSS **不随时长增长**（60 / 150 / 300 / 450 / 600 s
  扫描：208.1 / 228.8 / 208.5 / 264.6 / 209.3 MB）；v0.6.0 同场景为
  605–713 MB（随时长线性增长，v0.6.1 已修复）。
  详见 [docs/release_notes_v0.6.1.md](docs/release_notes_v0.6.1.md) 与
  `work/stage12_memory_validation.md`

### 透明模式：`--channel-sync-transparent`（剪辑前预处理）

隐含启用 `--channel-sync`；跳过视频编码：视频与所有非音频流
**stream copy**，仅对需要修正的音频轨重新生成，`untouched` 轨保持
原始内容；全部已对齐时输出与源文件**字节级一致**（SHA256 相同）；
文件级失败时输出为源文件原样拷贝（音频原样）。不依赖编码器配置，
输出命名与常规转码一致。

算法由 `core/sync_estimate.py`（两阶段估计 + 相位斜率精估）与
`core/sync_fix.py`（整数流式移位）实现；vendored
`core/mp4_channel_sync.py`（ChronoSync 1.x，MIT）保留作回滚与对照，
不再被 P1 主路径引用。设计细节与 P1 vs vendored 差异对照见
`docs/design/channel_sync_p1.md`。

## 编码后验证：`--check basic|advanced|full`

| 强度 | Sony（rtmd） | DJI（djmd） |
|---|---|---|
| `basic`（默认） | 时间线/轨清单/rtmd 载荷 sha256+时序+tref+timecode | 轨道清单 + djmd/dbgi/tmcd 载荷 sha256/size/样本数 + 音频流 + 帧数 |
| `advanced` | + lens/XML/uuid 完整结构 + Gyroflow 消费端 | + Gyroflow 逐帧四元数（type-2 机型/镜头配置 + type-3 org_quat/stab_quat） |
| `full` | + 详细自检（逐项 PASS/FAIL 落盘）+ **PSNR/SSIM 质量抽样** | + 逐轨时基/媒体时长、载荷首尾 32 字节、ffprobe 流级事实 + **PSNR/SSIM 质量抽样** |

任何 critical MISSING/MODIFIED 或 Gyroflow FAIL 都会使该文件判定失败。

**PSNR/SSIM 质量抽样**（`full` 级，防花屏/出错，不是质量门槛）：
源文件名 sha256 确定性 **10 取 1**，仅 **≤60s 短视频**；`setpts=N`
帧索引对齐（规避容器 timebase 失配）；阈值 psnr ≥25dB、ssim ≥0.80、
垃圾帧（psnr<12dB）占比 ≤2%，达标外判定该文件失败（经典路径在
交付前拦截）。阈值可经各档位 JSON 的 `quality_check` 节调整；
结果落盘 `quality_<名>.json` + 批次汇总 `logs/quality_samples.csv`。

**环境版本记录**：每批次启动时收集软件/驱动版本
（ffmpeg/SVT-AV1 库/NVEncC/QSVEncC/GPAC/Gyroflow + GPU 驱动）→
`logs/env_versions.json` + `env_versions.csv`，供编码行为复现。

## 质量对齐（NVENC ↔ QSV）

`qsv_aligned.json` 为 QSV 档位按 **NVENC 同档三指标参照**标定的对齐版
（VMAF v0.6.1 主指标 + SSIM/PSNR 辅，双片段验证轮）：

| 档位 | 原 icq | 对齐 icq |
|---|---|---|
| UHQ | 21 | 20 |
| HQ | 22 | 21 |
| SMALL | 26 | 23 |
| FAST | 24 | 22 |

标定报告与全量数据见 `work/quality_align/align_report.md`（可复跑脚本
`align.py`）。注意：Arc 无 Lookahead/EncTools（官方确认），对齐后的 QSV
在高运动素材上仍低 NVENC ~1dB——这是硬件天花板，非配置问题；
`--experimental-multihw` 混跑建议使用对齐版配置以缩小跨后端质量差。

## 降级与报错处理（不弹窗）

- **能力预判降级**：4:2:2 → 10bit 4:2:0 → 8bit 4:2:0，显著 WARNING +
  三处记录（log/CSV/report）；`--no-downgrade` 时改为跳过；
- **运行时失败**：自动降级梯重试；读不了容器先走 MP4Box strip 回退；
  全失败则该文件 failed，批处理继续；
- **失败记录**：`logs/failed_files.json` + 独立详情文件；`--retry-list`
  支持换后端重跑（可接受 failed_files.json 或纯文本路径清单）。

## 并行调度

- `--jobs 1`（默认）/ `--jobs N` 固定并发 / `--jobs auto` 自适应
  （波次实测聚合吞吐动态调整，无写死预算表）；
- `--experimental-multihw` 实验性双后端并行（NVENC+QSV，**质量一致性
  不保证**——见质量对齐节）。**v0.6.2 起无需再指定 `--encoder`**：开关
  自身探测 NVENC/QSV；两个都可用则双后端调度，只有一个可用则**退化为
  单后端池并告警**（此时跨后端质量差问题不存在），都不可用则直接报错。
  与 `--encoder x265|svtav1|*-av1` 同时给出会报错（multihw 只调度 HEVC）；
- x265 路径顺序执行。

## 日志与可观测性

三个层级各落一个文件（`logs/` 下），互不干扰：

| 文件 | 内容 | 何时写入 |
|---|---|---|
| `error.log` | 仅 ERROR | **始终写入**（跨批次追加，排查先看这个） |
| `warn.log` | WARNING 及以上 | **始终写入**（跨批次追加） |
| `total.log` | 按 `--log-level` | 默认 INFO 及以上；**追加**，可用 `--fresh-log` 清空 |
| `debug.log` | DEBUG（完整命令行/阶段耗时） | 仅 `--log-level debug` 时创建 |

```powershell
--log-level error|warn|info|debug   # 文件详细度（大小写不敏感），默认 info
--verbose                            # 控制台输出 DEBUG（文件级别不受影响）
--fresh-log                          # 启动时清空 total.log / debug.log
```

`error.log` / `warn.log` 与级别无关地始终追加：失败报告不会因为下一次
正常运行而被截断。

## 双窗口 UI / watchfolder

非 headless 运行自动打开进度看板（nvidia-smi 风格，1.5s 刷新）+ 工作信息
窗口；状态数据恒写 `logs/dashboard.json`。`start.bat` /
`python watchfolder.py --input <dir> --output <dir> --encoder nvenc ...`
可做轮询批处理（续跑逻辑使重复轮询近零开销）。

## 自动化测试（三级深度）

```powershell
python tests\full_autotest.py --level unit        # L1 纯逻辑 (178 项, 秒级, 零外部依赖)
python tests\full_autotest.py --level toolchain   # L2 + 工具版本/实机能力/旗标白名单 (~16s)
python tests\full_autotest.py --level full        # L3 + 真实管线集成 + 故障注入 (~9.5 分钟)
python tests\full_autotest.py --level all         # 等同 full
```

> 当前基线：**L1 = 178 PASS / 0 FAIL**（v0.6.2 新增 20 条：CLI 大小写 /
> 默认后端自动选择 / 分层日志）。`--level full` 在 v0.6.1 为 228 PASS /
> 0 FAIL，v0.6.2 因上述新增断言应为 248 PASS。任何改动后必须复核不出现
> 新增 FAIL。

- **L1 unit**（158 项）：color token 表、caps 解析、格式规划、失败分类、
  flag 构造、probe/paths、源分类、缩放引擎、gpac parse_info、dji facts、
  channel-sync 纯逻辑、**channel-sync 内存回归（有界窗口流 / 窗口切片一致 /
  64 MB 整轨扫描后工作集增量 ≤32 MB）**、AV1 档位与参数映射；
- **L2 toolchain**（+16 项）：真实工具版本、`--check-features` 实机能力、
  known_flags 白名单、Gyroflow/GPAC 探测；
- **L3 full**（+54 项）：Sony/DJI/经典 × NVENC/QSV 真实管线（basic+full check）、
  截断文件/尾部垃圾/断点续跑/retry-list 故障注入、strip 机制本体、
  AV1 管线、channel-sync P1 端到端与算法级。
  输入在 `work/autotest/` 自建副本（testsets 只读），报告
  `work/autotest/autotest_report.{json,md}`，退出码 0=全过。

另有定向自检：`python tests\run_selfcheck.py --encoder nvenc|qsv|x265`、
`python -m preservation.selfcheck <original> <final> <log_dir>`。

## 文档导航

```
docs/
├── README.md            分类索引
├── FINAL_REPORT.md      ★ 四份评估汇总结论与路线图
├── design/              设计文档：硬件后端设计 / 实施报告(含 DJI §15) / 集成报告 / HEVC 4:2:2 Rext 播放兼容性
├── evaluation/          评估：HEVC 生产就绪度(重写版) / x265 生产就绪 / AV1 可行性 / AV1 调参 / SVT-AV1 归档 / AV1 档位标定
└── reference/           第三方一手资料存档（x265 / SVT-AV1 含 v4.2.0 调参调研报告 / NVENC / QSV / VCE）

olddocs/                 历史档案存档（各阶段代码快照 / 被取代的旧脚本），详见 olddocs/README.md
```

## 关键决策记录

1. **AV1 与 XAVC 边界**：XAVC 标准只定义 H.264/HEVC。AV1 后端（svtav1 /
   nvenc-av1 / qsv-av1）对 Sony 源保留 rtmd/nrtm/uuid 元数据管线，但
   **不打 XAVC tag**（brand 改 av01）——保留 XAVC brand 的 AV1 文件是
   伪标准产物；XAVC 合规归档请用 HEVC 后端。AV1 统一 4:2:0 输出
   （4:2:2 源 WARNING 后降采样，不用 AOM）。
2. **DJI 专线**：djmd 即运动数据载体（Gyroflow 官方支持 Action 4/5/6、
   Avata、Neo）；`MP4Box -diso` XML 对 DJI 文件解析失败 → 轨道枚举全部
   走 `-info` 文本解析；mjpeg 封面/udta GPAC 不可寻址，按策略丢弃。
3. **HEVC 生产就绪度**：硬件双后端"有条件生产就绪"（15/15 验收 +
   色彩元数据端到端 + 质量对齐）；4:2:2 输出为 Rext，硬解仅 Blackwell，
   归档定位为"压缩归档副本"（母版标准是 FFV1/ProRes）。
4. **x265 定位**：手动高压缩档。P0 修复已落地并回归（info=false
   可复现、level 6.2 + CPB 钳位 240Mbit；FAST rd 保持 2 不动）；
   `no-strong-intra-smoothing` **全档开启**（用户决定 2026-09-01：
   触发帧内强力平滑的条件苛刻、对画面影响低，带上后编码器改用
   其他平滑手段，细纹理/颗粒保留更好）；DJI 素材走同构保留管线
   （djmd 原生保留）；缩放规则仍 PROVISIONAL。详见
   `work/x265_test/x265_test_report.md`。
5. **档位数值权威性**：JSON 数值为作者实测标定，调参须回归测试集。
6. **AV1 色彩元数据保真**（v0.6.0 修复）：源素材**未声明**色彩描述时
   （`color_primaries/transfer/space = unknown`；137 段真实 A7M5 素材中
   有 7 段如此），AV1 输出**不得凭空带上 bt709**。根因不在本项目也不在
   编码器——编码器位流本就是 `unspecified`——而在 GPAC/MP4Box 的容器重建：
   它只在 AV1 sequence header 的 `color_description_present_flag == 1` 时
   才从位流推导 `colr`，该标志为 0 时写死 `colr nclc 1/1/1`（bt709）。
   QSVEncC 恰好置了该标志，故 `qsv-av1` 从未暴露此问题；FFmpeg/libsvtav1
   与 NVEncC 不置，于是 `svtav1`/`nvenc-av1` 被判 critical MODIFIED 而失败
   且无产出。修复是在项目自己的 mux 边界做**窄口径原地对账**
   （`preservation/colour.py` + `isobmf.patch_video_colr()`）：仅当源未声明
   色彩时，把已存在的 `colr` 三元组改写为 `2/2/2`（unspecified）。盒子大小
   不变，故不触碰任何 stco/co64 偏移；源已声明色彩时**完全不执行**。
   `preservation` 校验规则**一行未改**（不放宽任何 critical 项）。
   验收：27 case / 348 断言全绿（T1 7 段 × 三后端 + T2 已知色彩 1 段 ×
   三后端），修复后输出与源色彩字段完全一致。

## 已知限制与说明

- DJI：mjpeg 封面与 udta 丢弃（GPAC 26.02 不可寻址，日志显式）；机内
  Rocksteady/EIS 开启的素材无运动数据（djmd 存在但四元数为空，校验按
  两侧相等通过）；
- QSV：`lookahead(--la-depth)` 在 Arc 全系无效（LA 全 x，旗标被接受但
  特性不生效）；驱动/QSVEncC 版本对需钉住（6557/6559 曾有批量编码回归史）；
- Sony 4:2:2 成品 = HEVC Rext，播放硬解仅 NVIDIA 50 系，其余需软解播放器
  （VLC/mpv）；分发请出 4:2:0 副本；
- **AV1**：
  - 统一 4:2:0 输出（所有 AV1 后端）；Sony 源保留 rtmd/nrtm/uuid 元数据但
    不打 XAVC tag（brand av01）；
  - **源未声明色彩 → 输出也不声明**，不发明 bt709（见关键决策记录 6）；
  - **受支持输入为 ≥1080p**：低于 1080p 的素材默认不处理
    （`nvenc_av1.json` 的 `level 6.1` 在受支持范围内实测均可用）；
  - SVT-AV1 无场景关键帧（scd 只管码率分配）、mbr 为软上限（非 VBV 硬钳）；
  - 4K60 UHQ 档（preset 1，≈1fps 对齐 x265 UHQ）编码耗时极高，属基准档
    非生产实用。
- 非 Sony 非 DJI 素材按策略丢弃元数据（仅视频+音频）；
- VFR 素材自动 `--avsync forcecfr` 规范化（WARNING 记录）；
- 经典路径无 1:1 帧闸门（不误杀 VFR）；Sony/DJI 路径有；
- **`--channel-sync`（P1）能力边界**：
  - 只做**整数样本移位**（无重采样、无 fractional sinc、无 time-warp）。
    真实慢漂移（39–76 ppm）会被判 `non_constant` 并**拒绝修正**，本轮
    **不做漂移补偿**（P2 drift/resample 为后续工作，未实施）；
  - 仅 **48 / 96 kHz 线性 PCM**（44.1 kHz 与压缩/非线性格式显式拒绝）；
  - 只处理 **≥3 条独立单声道 PCM 轨**（2ch/1ch 布局默认不对齐）；
  - 长程验证只覆盖 **4 轨 / 48 kHz / 单线程**这一规格（含 `--jobs 1`）。
    `--jobs auto` 长程、Sony/DJI 10 分钟素材长程、`--experimental-multihw`
    长程**均未验证**，不得据此声明性能；
  - `--channel-sync-transparent` 成功路径会在 `.1ktwork/` 保留 4 个
    `audio_*.mov` 与通道报告 JSON（10 分钟输入约 330 MB），属既有行为，
    需自行判废。

## 许可证

**GNU Lesser General Public License v3.0 或更高版本（LGPL-3.0-or-later）**，
全部开源。详见 [`LICENSE`](LICENSE)。

> 第三方工具（NVEncC/QSVEncC、GPAC、ffmpeg、Gyroflow）以独立可执行文件
> 形式调用，各按其自身许可证分发，不并入本项目。

## 回滚

```powershell
git checkout pre_S1S5              # S1-S5 前基线
git tag -l                         # pre_S1S5 / post_S1S5 / pre_ui / post_1kt_ui
                                   # post_adaptive / post_hw_fulltest / post_color_meta
                                   # post_dji / post_dji_checklevels / post_quality_align
                                   # post_autotest / post_x265 / v0.4.0 / v0.4.1 / v0.4.2
                                   # post_av1 / post_av1_calib / v0.5.0 / v0.5.1
                                   # v0.6.0 (HEVC+AV1 合并主线, 含 AV1 色彩保真修复)
                                   # v0.6.1 (channel-sync 流式内存修复, 当前发布)
git checkout backup/pre-av1-main-merge   # AV1 合并进 main 之前的状态 (回滚点)
```

> 分支约定: `main` = **HEVC/265 + AV1 合并主线**（两条线能力同处一分支，
> 各线最后一次发布包见上节；合并后主线的当前发布为 **`v0.6.1`**）；
> `av1` 分支保留为 AV1 独立线历史
> （含 post_av1 / post_av1_calib / v0.5.0 / v0.5.1 tag）；
> `backup/pre-av1-main-merge` = AV1 合并前的 `main` 快照。
