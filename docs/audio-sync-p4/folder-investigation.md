# 素材文件夹调研工具

`tests/regression/investigate_folder.py` —— 对一个素材文件夹做**尽可能详尽**的
检查，全部结论落 JSON + 一份人读报告。CPU only、可续跑、离线跑。

## 它调研什么

| 阶段 | 内容 | 复用的既有 API |
|---|---|---|
| `checks` | 整套规范检查 **CTR-01..08 / FMT-01..05 / AUD-01..04**，含 sample entry 自洽、容器结构、时长一致、零长流、解码探针、音频对齐裁决 | `archive_repair.summarise_check`（内部调用生产 `run_channel_sync`）、`core.channel_sync.eligible_audio` |
| `video` | 编码格式深挖：codec/profile/tag/pix_fmt/位深/色彩/分辨率/帧率/码率；**码流结构**（关键帧间隔、每秒码率 p10/p50/p90、包大小）；全部流原始字段（含 data/时间码轨、handler_name、vendor_id）；容器 tag 与 side data；派生 `bits_per_pixel` | `core.probe.probe_source`、`archive_repair.probe` / `container_facts` |
| `profile` | 每 60 s 块电平与静音（逐音频流），整轨 RMS | `core.channel_sync._decode_stream` / `_track_health` |
| `stereo` | 2ch 文件 L/R 相位与分块相关（**仅信息**：产品决定不对齐 2ch） | 本工具内实现（核心库无 2ch 对齐 API） |
| `container` | **容器格式复现验证**：前 N 秒 `-c copy` 重封，逐项比对流数量/顺序/codec/profile/tag/pix_fmt/分辨率/帧率/time_base/声道/采样率/位深/色彩，以及顶层 box 与 ftyp brand。先按全部流映射，失败则退视频+音频并记录**被丢弃的流** | `archive_repair.probe` / `container_facts` |
| `pairs` | 仅 4-mono 文件：12 个有序通道对 × 2 种几何、从生产锚点出发的**连通性**（能否每条轨都被定位）、30 s 窗证据密度、建组 SNR | `core.sync_estimate.estimate_pair` / `sample_pair_snr` / `core.audio_sync_tv` |

## 明确不做的事

**不执行任何编码器**。`container` 阶段全程 `-c copy`：它只回答"源容器的流参数
能否被完整复现"，不尝试重编。因此报告里没有 SSIM/PSNR——那需要编码，按用户要求
不做。

## 盘策略

* `E:` / `I:` 可自由读写（`--out` 放在这里）。
* `F:` 只允许项目文件夹 `F:\1KeyTranscoder` 内的写入；**程序在 `--out` 指向
  F: 其它位置时直接拒绝启动**，并且不删除 F: 上任何东西。
* 中间解码与重封产物放在 `E:\1kt_scratch\investigate\`，每轮结束清理。

## 用法

```powershell
cd F:\1KeyTranscoder
python tests/regression/investigate_folder.py `
    --root "I:\2026.09.24" `
    --out  "E:\1kt_work\inv_2026_09_24" `
    --budget-minutes 180
```

可选：

| 参数 | 说明 |
|---|---|
| `--only checks video profile stereo container pairs` | 只跑指定阶段 |
| `--container-seconds 5` | 容器复现验证的取段长度（默认 5 s） |
| `--container-max-files 0` | 最多验证多少个文件（0 = 全部） |
| `--video-window 120` | 码流结构分析窗口（秒） |
| `--threads 4` | BLAS 线程上限 |

## 预期耗时

84 件 / 650 GiB 的实测口径：`checks` ≈ 2 分钟，`video` ≈ 3 分钟（每件只读前
120 s 码流），`container` ≈ 5 分钟（5 s 重封），`profile` ≈ 10–15 分钟（有音频的
40 件需全片解码音频），`pairs` ≈ 2 分钟（仅 1 件 4-mono）。**合计约 25–30 分钟**，
`--budget-minutes 180` 是安全上限。

## 产物

`--out` 下：

* `report.md` —— 人读报告（9 节）
* `files/<stem>.json` —— 每文件完整原始结果（checkpoint；重跑跳过已完成）
* `manifest.json` —— 配置、耗时、未完成任务

`report.md` 的节：1 总览 / 2 检查码统计 / 3 逐文件 / 4 电平 / 4b 立体声 /
5 编码格式逐文件 / 6 跨机位流格式对比 / 8 容器复现验证 / 9 需要处理。
