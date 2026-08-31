# 1KeyTranscoder · AV1 编码实现评估（详尽版）

> 评估日期：2026-08-31。方法：①上一轮 AV1 评估会话全量转储（18 个子代理，
> `.dsh-drop` 会话包）结构化回读（`work/av1_drop/findings_synthesis.md`）；
> ②本机实现与端到端实测（SVT-AV1 + NVENC AV1 + QSV AV1 三后端真实管线）；
> ③官方文档与社区实测交叉（详见 `docs/reference/`、
> `docs/evaluation/av1_hw_tuning_guide.md` 与
> `docs/reference/svt-av1/SVT-AV1_archival_tuning_report.md`）。
>
> **结论前置：三后端 AV1（软件 SVT-AV1 + 硬件 NVENC/QSV）已实现并
> 端到端实测通过；Sony/DJI 元数据保留管线对 AV1 同样生效（rtmd/nrtm/
> uuid/djmd 全保留，仅按 XAVC 规范边界不打 XAVC tag）；四档数值已按
> VMAF/XPSNR 实测标定（`docs/evaluation/av1_calibration.md`）。**

---

## 1. 实现范围与机制

| 项 | 内容 |
|---|---|
| 新后端 | `--encoder svtav1`（软件）/ `nvenc-av1` / `qsv-av1`（硬件，与 HEVC 后端同构，复用白名单/降级梯/失败分类/调度/看板全部机制） |
| 能力探测 | caps 新增 AV1 段解析（NVENC `AV1: nv12, yv12, yv12(10bit)` / QSV `Codec: AV1 FF` 10bit 行），本机实测双后端 `av1(10bit=True)` |
| 格式规划 | `plan_initial_format(codec="av1")`（硬件）与 `av1_pix_fmt()`（svtav1）：**任何非 4:2:0 源恒输出 4:2:0**（三大硬件 AV1 与 SVT-AV1 均无 4:2:2 编码；软件路径调度层 WARNING 后降采样，不用 AOM） |
| 档位 JSON | `svtav1.json` + `svtav1_scaling.json`（CRF+MBR，preset 2/4/4/7）/ `nvenc_av1.json`（QVBR）/ `qsv_av1.json`（ICQ），四档 UHQ/HQ/SMALL/FAST，**已按 VMAF/XPSNR 实测标定**（见 av1_calibration.md） |
| Sony 保留管线 | AV1 后端遇 Sony 源（rtmd）→ **保留管线照常**（rtmd/nrtm/uuid 字节保真），但**不打 XAVC tag**（brand av01）——XAVC 标准只定义 H.264/HEVC，保留 XAVC brand 的 AV1 文件是伪标准产物；XAVC 合规归档请用 HEVC 后端 |
| DJI 路径 | AV1 后端照常走 DJI 保留管线（djmd/dbgi/tmcd 原生保留 + 载荷校验 + Gyroflow 逐帧四元数），视频轨断言泛化为 av01 |
| 色彩/HDR | 复用 `core/color.py`（bt709 四件套 + mdcv/clli；`--atc-sei` 为 HEVC 专属，AV1 档自动不写） |
| 软件 AV1 专用 | AV1 中间文件必须 MP4（ffmpeg 9 的 MOV muxer 不接受 AV1）；SVT-AV1 v4.2.0 参数经 smoke-encode 逐键验证（bias-pct 等不可用键已剔除） |

## 2. 硬件能力事实（官方 + 本机实测）

### 2.1 NVENC AV1（RTX 5070 Laptop，NVEncC 9.31 / SDK 13.1）

- 编码格式：**4:2:0 8/10bit**（本机 caps dump：`AV1: nv12, yv12, yv12(10bit)`）；
  **硬件 RC 仅 CQP/CBR/VBR**（CBRHQ/VBRHQ/QVBR 无硬件对应，QVBR 是
  lookahead 软件近似）；B 帧上限 31、B Ref Mode 7（hierarchical 需
  SDK 13.1+**新驱动**，本机 596.36 实测 hierarchical 仍报 API 13.1 错误）；
  分辨率上限 8192×8192。
- **关键坑（已按坑避让）**：`--profile main` = 8bit、`high` = 10bit
  （main+output-depth 10 会**静默产出 8bit**）→ 档位统一 `profile: high`；
  非层级 GOP 下 **B 帧 ≤7**（本机实测 16 报错）→ 档位用 7/4；
CQP 模式下 `--tune` 实测降画质（NVIDIA 论坛）——QVBR/VBR 路线按官方指南沿用 tune hq/uhq。
- 质量基线：本机 4K60 实测 73fps（cqp 30 短样，前轮会话数据）；
  Tom's Hardware 实测 **Ada NVENC AV1 与 NVENC HEVC 同码率质量无大差异**
  （NVIDIA "40% 节省" 是 AV1 vs H.264 口径）。

### 2.2 QSV AV1（Arc 140T，QSVEncC 8.26）

- **仅 FF 固定功能路径**（PG 全 x）→ 档位显式 `--function-mode FF`；
  **RC 仅 CBR/VBR/CQP/ICQ**（AVBR/QVBR/LA 系列全 x，三份 GPUFeatures
  dump 互证）；
- B 帧由 **`--gop-ref-dist`**（=bframes+1，默认 8 = 甜点，rigaya A310
  实测）表达；`--bframes` 对 AV1 **静默无效**；
- **ICQ/CQP 是 0-255 刻度**（HEVC 1-51，数值不可照搬）→ 档位 icq
  24/26/30/28 为标定起点，待 VMAF 重标；
- lookahead 新用法：`--la-depth` 叠 icq/vbr/cbr + `--extbrc` +
  `--i-adapt --b-adapt`（旧 `--la/--la-icq/--la-hrd` 不可用）；
- 驱动历史：时间戳 bug（vpl-gpu-rt #253 / ffmpeg #10062，驱动 3959
  修复，QSVEnc 7.20+ 内置 workaround；本机 8.26 含）；早期驱动
  "mbbrc 探测 o 但无效"（issue #96）。
- 质量基线：rigaya 官方 VQ 站 QSV AV1 曲线整体在 QSV HEVC 上方
  （同 VMAF 省 ~20% 码率，与 Intel 官方口径一致）；速度同级（都走 FF）。

## 3. 质量证据分层（官方 + 社区）

| 层 | 结论 | 来源 |
|---|---|---|
| 硬件 AV1 vs 硬件 HEVC | NVIDIA 打平（Tom's）；Intel 省 ~20%（rigaya VQ 站）；AMD +30% VMAF（自家对比） | 前轮会话 6a3cf6e2/31c0e4e9/e6ce795b |
| 硬件 vs 软件 | 软件 AV1 完胜：SVT-AV1 比 x265 省 35-49% 码率（MSU 2022-2025 BSQ-rate）；硬件编码器（含 AV1）普遍需 +13~30% 码率追平 x265 slow（Fora Soft/iXBT） | 前轮会话 512d88d2/c1bb1d10 |
| 本项目定位 | 硬件 AV1 的价值 = 免版税 + QSV 侧体积收益 + 4:2:0 全平台硬解（AV1 硬解覆盖逐年改善，Apple 2023 起）；**不是**对 HEVC 的质量升级 | 综合 |

## 4. 本机端到端实测（2026-08-31，输入均为 work/ 副本，testsets 只读）

| 用例 | 后端 | check | 结果 |
|---|---|---|---|
| DJI Action4 30fps ×2 | nvenc-av1 | basic | **15/0/0**，av01 Main 10bit + djmd/dbgi/tmcd 保真 |
| DJI Action4 ×2 | qsv-av1 | basic | **15/0/0** 同上 |
| DJI Action4 ×2 | nvenc-av1 | **full** | **26/0/0 + Gyroflow PASS**（105/330 运动样本逐帧一致） |
| Sony C9037 | nvenc-av1 | basic | **36/0/0**，av01 + rtmd 保留 + brand av01（无 XAVC） |
| Sony C9037 | svtav1 | basic | **36/0/0**，av01 + rtmd 保留 + 4:2:2→4:2:0 WARNING + brand av01 |
| DJI Action4 | svtav1 | basic | **15/0/0**，av01 + djmd/dbgi/tmcd 保真 |
| Sony AV1 / DJI AV1 Gyroflow | 消费端 | — | Gyroflow 正常读 AV1 + rtmd（13013 IMU 样本）/ djmd（四元数） |
| 自动化测试 | 全量 | full | L1 96 项 + L2 + L3（C16 改为保留管线断言 + 新增 C18-C21 svtav1 四例） |

修复记录：full 级 `dji.video.ffprobe` 深检查项原比对视频 profile
（转码后 HEVC Main 10 → AV1 Main 必然变化）→ 改为只比
宽/高/pix_fmt，profile 由 video_entry 项（av01/hvc1）把关；
ffmpeg 9 的 MOV muxer 拒绝 AV1 → AV1 中间文件改 MP4（GPAC 无差别）。

## 5. XAVC 合规边界（新策略：保留管线 + 不打 XAVC tag）

- XAVC 标准只定义 H.264/HEVC；保留 XAVC brand 的 AV1 文件是伪标准
  产物 → **AV1 后端对 Sony 源保留 rtmd/nrtm/uuid 元数据管线，但
  brand 改 av01**（GPAC -brand 通行同时恢复 movie timescale，
  validate/selfcheck 的 ftyp 断言按 AV1 策略改写: 断言"无 XAVC"
  而非"与源一致"）；
- XAVC 合规归档（需保留 XAVC brand）请用 HEVC 后端；
- 三大硬件 AV1 与 SVT-AV1 全 4:2:0 → 4:2:2 源走色度降采样
  （WARNING 记录）。

## 6. 消费端兼容（AV1 4:2:0 Main 10）

- 4:2:0 Main 10 = 全平台最安全基线；硬解覆盖：Android 新 SoC / Apple
  A17 Pro 起 / RTX 30+ / Arc / RDNA2+；软解 dav1d 全覆盖（ffmpeg 内置）；
- **Sony 机身/Catalyst 生态不识 AV1**（本就只服务非 Sony 素材，无冲突）；
- Gyroflow（ffmpeg 解码）可正常读 AV1 视频 + djmd（本机 full 级已实证）。

## 7. 待办与边界（诚实清单）

1. ~~**档位标定**~~ **已完成**（2026-08-31）：
   `docs/evaluation/av1_calibration.md`（SVT-AV1 四档 + 硬件双后端
   QVBR/ICQ 重标定, VMAF/XPSNR/PSNR/SSIM 实测矩阵）；
2. NVENC AV1 的 hierarchical B 帧（SDK 13.1+新驱动）与 QSV 新驱动
   mbbrc/extbrc 实效——工具/驱动升级后按能力探测重测；
3. 工具升级顺手项：NVEncC 9.31→9.33、QSVEncC 8.26→8.28
   （白名单机制兜底）；ffmpeg 升级会带新 SVT-AV1（4.x 迭代快，
   档位需按新版本回归）；
4. 质量定位诚实声明：硬件 AV1 相对硬件 HEVC **不是质量升级**（NVIDIA
   打平），换码收益是免版税/体积/生态；软件 SVT-AV1 实测在噪点素材
   上比 x265 slow 档 +0.7 XPSNR（+31% 码率）或同码率 +0.4 XPSNR，
   干净素材持平——价值 = 免版税 + film-grain 极噪体积选项
   （-54% 码率） + 全平台解码覆盖。

## 8. 证据索引

- `work/av1_calib/` — 档位标定全量数据（calib.py / results.csv /
  results_table.md / 各矩阵 spec 与日志）
- `docs/evaluation/av1_calibration.md` — 标定报告（★档位定案）
- `docs/reference/svt-av1/SVT-AV1_archival_tuning_report.md` — SVT-AV1
  v4.2.0 调参调研（官方文档 + community, 全带 URL）
- `work/av1_drop/findings_synthesis.md` — 前轮 18 子代理结论汇总（带来源 id）
- `docs/evaluation/av1_hw_tuning_guide.md` — 支持度矩阵 + 逐键翻译 + 预设草案
- `docs/evaluation/av1_feasibility_report.md` — 可行性总报告（★XAVC 决策）
- `docs/reference/nvenc/`、`docs/reference/qsv/`、`docs/reference/svt-av1/` — 一手资料
- 本机实测日志：`work/ct_av1_out/`（AV1 管线产物）、`work/autotest/`、`work/av1_calib/smoke_*`
- git tag `post_av1` — 实现基线
