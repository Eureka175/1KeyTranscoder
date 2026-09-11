# 1KeyTranscoder v0.6.1 — Release Notes

> 发布类型：**bugfix release**（无功能新增，无配置破坏性变更）
> 基线：`v0.6.0`（`main` 主线，HEVC/265 + AV1 合并后首个版本）
> 上游分支：`main`

## 1. Highlights

| 项 | 说明 |
|---|---|
| **Channel Sync P1** | `--channel-sync` 自动轨间延时补偿进入主线：GCC-PHAT 两阶段测量 → 相位斜率精估 → 轨道级质量门 → 纯整数样本移位 → 复检；锚点按 `CH3 > CH4 > CH1 > CH2` 自动回退；轨道级部分成功（坏轨原样保留，不拖累健康轨） |
| **AV1 mainline** | SVT-AV1（软件）/ NVENC-AV1 / QSV-AV1 三后端与 HEVC/x265 同处 `main`，共用一个入口与一套 Sony/DJI 元数据保留管线 |
| **AV1 + channel-sync** | AV1 后端与 `--channel-sync`（含 `--channel-sync-transparent`）组合路径已验证 |
| **AV1 color metadata fidelity** | 源素材未声明色彩描述时，AV1 输出不再凭空带上 bt709（修复位置在项目自己的 mux 边界，`preservation` 校验规则一行未改） |
| **Streaming memory fix** | **本版核心修复**：`--channel-sync` 长素材内存无上限增长（10 min/4CH/48 kHz 峰值 RSS 605–713 MB → 205–215 MB） |
| **Transparent / Sony / DJI preservation** | `--channel-sync-transparent` 剪辑前预处理；Sony XAVC（rtmd/nrtm/uuid）与 DJI（djmd/dbgi/tmcd）元数据原生保留 |

## 2. Fixed

### 2.1 `--channel-sync` 长素材内存无上限增长（v0.6.1 修复）

**症状**：10 分钟 / 4 轨 / 48 kHz 素材上 `--channel-sync` 峰值 RSS 达
**605–713 MB**，且**随时长线性增长**（每分钟 +50～137 MB）。同场景目标是
RSS ≤ 512 MB。

**根因**：`np.memmap` 并不等于低内存。映射本身几乎不占内存（实测增量
+0.0 MB），但每个被触碰的 file-backed 页都会计入进程 WorkingSet 并长期
保留——而实现确实顺序触碰了每一条轨的每一个字节：

- `channel_sync._track_health()` 分块扫描**整轨**算 RMS/有限性 →
  **每轨恰好 +110 MB**（单轨 raw = 28,800,760 样本 × 4 B = 109.9 MB，
  4 轨 = +440 MB）；
- `sync_fix.shift_stream()` 用 `np.memmap(mode="w+")` 写整轨 → 输出文件
  脏页常驻；
- 阶段探针实测（monkeypatch 真实 `run_channel_sync`）：峰值 719.0 MB 中
  约 **440 MB（61%）来自整轨映射页驻留**。

**修复**（`core/sync_estimate.py` / `core/sync_fix.py` / `core/channel_sync.py`）：

- 新增 `RawStream`——`seek` + `read` 的**有界窗口读取器**（1 MiB 对齐块 +
  4 槽 LRU 读缓存，每流固定约 4 MiB）；`open_source()` 对路径输入返回它，
  **不再返回 memmap**；
- `shift_stream()` 输出改为普通文件句柄 `seek`/`write`（不再写映射），输入
  经 `read_into()` 直读进目标块（省去中间副本）；
- `estimate_pair()` / `recheck_residual()` 拆分为「流所有权包装 + 纯计算」，
  只关闭自己打开的流（消除对 GC 时机的依赖）；
- 空文件 / 长度非 dtype 整数倍 → 显式 `ValueError`（与旧失败语义对齐）。

**为什么算法语义未变**：块寻址落在与 memmap 相同的文件偏移上，dtype 映射
与 f32→f64 转换点、RMS 平方和归约顺序均未变；GCC-PHAT 数学、delay 方向、
搜索窗、置信度/aligned/`drift_min_ms` 阈值、锚点优先级、`low_confidence` /
`insufficient_frames` 判定与报告字段**一行未改**；未引入 resample /
time-warp / fractional sinc / 新依赖。

**结果**（600 s / 4 轨 / 48 kHz / 单线程）：

| 指标 | v0.6.0 | v0.6.1 | 目标 |
|---|---:|---:|---|
| Peak RSS（进程树 500 ms 采样） | 605–713 MB | **205–215 MB** | ≤ 512 MB ✅ |
| Runtime | 46.4 s | **46.9–52.7 s** | < 60 s ✅ |
| `_track_health` 每轨增量 | +110 MB | **+3.9 MB** | — |

时长扫描（60/150/300/450/600 s）：208.1 / 228.8 / 208.5 / 264.6 / 209.3 MB
——**不再随时长增长**（600 s 不再高于 60 s）；v0.6.0 同规格为
151.6 → 605.4 MB 的线性上升。

### 2.2 AV1 色彩元数据保真（v0.6.0 引入，本版一并发布）

源素材未声明色彩描述时（`color_primaries/transfer/space = unknown`，
137 段真实 A7M5 素材中有 7 段如此），AV1 输出不得凭空带上 bt709。根因不在
本项目也不在编码器，而在 GPAC/MP4Box 的容器重建：它只在 AV1 sequence
header 的 `color_description_present_flag == 1` 时才从位流推导 `colr`，
该标志为 0 时写死 `colr nclc 1/1/1`。修复是在项目自己的 mux 边界做窄口径
原地对账（`preservation/colour.py` + `isobmf.patch_video_colr()`）：仅当源
未声明色彩时把已存在的 `colr` 三元组改写为 `2/2/2`（unspecified）；盒子
大小不变，故不触碰任何 stco/co64 偏移；源已声明色彩时完全不执行。

## 3. Validation

| 验证项 | 结果 |
|---|---|
| **137 段真实 A7M5 baseline** | `tests/fixtures/channel_sync/a7m5_real_137_baseline.csv`（逐轨 548 行）：已对齐 **111** / 实际修正 **11** / 测量失败 **15**，完全复现；11 条 fixed 轨 `shift_samples` 全同（1222/996/918/−37/−39/1051/935/956/905/1119/944） |
| **Stage 1.1**（AV1 色彩元数据保真） | 27 case / 348 断言全绿（T1 7 段 × 三后端 + T2 已知色彩 1 段 × 三后端）；修复后输出与源色彩字段完全一致 |
| **Stage 1.2**（流式内存修复） | 新旧代码并排差分 **49 用例：46 完全相同**，3 处差异全部属预期（2 处同一异常类型仅文案不同、1 处端到端报告中的绝对临时路径）；137/137 逐文件报告 JSON **字节相同**；`fixed_*.f32` / `audio_*.mov` SHA256 相同 |
| **Full short regression** | `python tests/full_autotest.py --level full` → **228 PASS / 0 FAIL**（573 s）；L1 unit 158 PASS / 0 FAIL |
| **v0.6.1 clean-room validation** | 正式包解压到全新目录、清空 `PYTHONPATH` + `PYTHONNOUSERSITE=1`、不依赖 Git 仓库/`work/`/源码目录，覆盖 `--help` / `--version` / 默认 x265 / AV1 / channel-sync / transparent / Sony preservation / AV1+channel-sync |
| **600 s channel-sync benchmark** | 见 §4 |

## 4. Performance

**只记录本次真实测得数据。**

```
规格:      600 s / 4 audio tracks / 48 kHz / --channel-sync / 单线程 (--jobs 1)
输入:      LONG-CS-AUDIO.mp4 (600.02 s, 4× pcm_s24be 单声道, 由真实 A7M5
           素材流拷贝拼接而成, 无重编码)

Peak RSS:  205.6 MB / 214.5 MB      (目标 ≤ 512 MB)   PASS
Runtime:   46.9 s  / 52.7 s         (目标 < 60 s)     PASS
```

同步结果正确性：`status = applied`，CH1 `shift_samples = 1051`
（= 该 fixture 冻结标定值 +21.8958 ms @48 kHz），复检残差 **+0.0180 ms**
（≤ 0.05 ms 门限），低置信轨 CH2 仍 `untouched`（未误修），4 条音轨完整保留，
输出可解析、完整解码 stderr 为空。

> 说明：runtime 余量受机器后台负载影响（同一构建在后台服务繁忙时段实测
> 可达 59 s 量级）；内存指标不受此影响且余量充足。

## 5. Backward Compatibility

- `VERSION` / `--version` → `1KeyTranscoder 0.6.1`；
- 档位 JSON 与命令行**无破坏性变更**；`--channel-sync` 的阈值、默认值、
  报告字段与 JSON 结构完全不变（现有脚本/报表无需改动）；
- `--channel-sync` 关闭时输出与 v0.6.0 行为一致；
- 唯一**行为差异**：`--channel-sync` 不再把整条文件映射进内存，因此长素材
  下进程工作集显著下降——这正是本次修复目的。

## 6. Known Limitations

- **SVT-AV1 无场景关键帧**（`scd` 只管码率分配）、**mbr 为软上限**（非 VBV 硬钳）；
- **AV1 仅 MP4 容器**，且统一 **4:2:0** 输出（4:2:2 源 WARNING 后降采样）；
- `--channel-sync` 只做**整数样本移位**：无 resample、无 fractional sinc、
  无 time-warp；真实慢漂移（39–76 ppm）判 `non_constant` 并拒绝修正；
- `--channel-sync` 仅支持 **48 / 96 kHz 线性 PCM**、**≥3 条独立单声道轨**；
- `--channel-sync` 长程性能仅在 **4 轨 / 48 kHz / 单线程** 规格下实测；
- `--channel-sync-transparent` 成功路径保留 `.1ktwork/`（4× `audio_*.mov`
  + 通道报告 JSON，10 分钟约 330 MB），属既有行为。

**以下项目仍未完成，不得视为已解决**：

- Sony 10 分钟素材长程验证 —— **PENDING**
- DJI 10 分钟素材长程验证 —— **PENDING**
- `--jobs auto` 长程验证 —— **PENDING**
- `--experimental-multihw` 长程验证 —— **PENDING**
- P2 漂移分类与 resample（漂移补偿） —— **NOT STARTED**

## 7. Package

| 项 | 值 |
|---|---|
| Archive | `1KeyTranscoder-v0.6.1-win64-selfcontained.zip` |
| Sidecar | `1KeyTranscoder-v0.6.1-win64-selfcontained.zip.sha256` |
| Manifest | `release-manifest.json`（逐文件 size + sha256、runtime 版本、git commit/tag） |
| 内含运行时 | ffmpeg/ffprobe 9.0.1（libx265 / libsvtav1 v4.2.0 / libvmaf）、NVEncC 9.31、QSVEncC 8.26、GPAC 26.02 |
| 不含 | `.git/`、`work/`、`testsets/`、`docs/`、`olddocs/`、`logs/`、`metadata_forensics/`、`dist/`、`release/`、`__pycache__/` |

包完整性校验：`python release/verify_package.py <archive> --extract-dir <clean>`
（archive SHA256 / sidecar / manifest 对账 / ZIP CRC / 逐文件 hash /
文件清单 / 运行时版本复现）。

## 8. 相关文档

- `docs/design/channel_sync_p1.md` —— `--channel-sync` P1 设计文档
- `docs/fixtures/a7m5_channel_sync_fixtures.md` —— A7M5 真实素材 fixture 说明
- `docs/evaluation/av1_calibration.md` —— AV1 档位标定
- `work/stage12_memory_validation.md` —— 流式内存修复验证报告（含 §34 长程表）
- `work/channel_sync_memory_audit.md` —— 内存审计（逐阶段归因）
