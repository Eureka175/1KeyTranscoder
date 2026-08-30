# 1KeyTranscoder · HEVC 实现生产就绪度评估（重写版）

> 评估日期：2026-08-29（会话内全量代码重读 + 官方文档核查 + 社区实测调研 + 本机复测）
> 替换旧版《265 实现详细程度评估》。旧版侧重"详细程度量化"，本版回答的问题是：
> **HEVC 实现现在能不能进入生产？**——按路径给出判定、给出上线条件与证据分级。

## 0. 结论前置

**总体判定：有条件生产就绪（conditionally production-ready）。**

| 路径 | 判定 | 一句话依据 |
|---|---|---|
| NVEncC × Sony 保留管线（4:2:2 10bit → Rext） | ✅ **生产就绪** | 15/15 全链验收 + 本机能力矩阵完整（4:2:2/10bit/B5/L6.2）+ 色彩修复后 full check 全绿；官方语义逐旗标核查无偏差 |
| QSVEncC × Sony 保留管线（4:2:2 → 4:2:0 政策） | ✅ **生产就绪（速度需重定位）** | 同 15/15 验收；`tu→--quality best` 映射修复后吞吐从 ~106fps 降到 **51.6fps（本机复测）**，旧速度注记作废 |
| 硬件 × 经典路径（非 Sony） | ✅ **生产可用** | 单趟直出，元数据按策略丢弃（显式日志声明）；DJI 适配留待独立 session |
| x265 软件路径 | ⚠️ **手动档，不阻塞** | 代码层可用但 `x265_scaling.json` 数值全量 PROVISIONAL、UHQ 0.8fps 不实用；见姊妹篇 x265 评估 |
| `--experimental-multihw` | 🧪 **实验性，不建议生产** | 双后端同档质量不一致已有本机实证（见 §5.3），免责声明成立 |

**上线前必须补齐的 4 个条件（§7.2）**：
1. 各档位质量标定（VMAF/SSIMULACRA）——目前"质量"只有主观注记，无感知指标证据；
2. HLG/HDR10 端到端验证——色彩代码路径已就绪但只验证过 bt709；
3. 本会话发现的一次 QSV 编码期间系统内存耗尽/闪退——根因未定位，需复现；
4. 样本覆盖：XAVC S-I / 4K120 / 多机型（FX 系）。

**一句话结论**：对"本机 + Sony XAVC 素材批量压缩归档"这一既定场景，硬件 HEVC 双后端
的机制与验证已达生产级（防御层完整、元数据逐字节保留、15/15 验收）；**剩的是标定
证据与覆盖，不是架构缺陷**。

---

## 1. 评估方法与证据分级

- **[本机]**：项目内历史验收记录 + 本会话复测（色彩修复验证、双后端吞吐/SSIM 基准、
  能力 dump、full check 结果）。
- **[官方]**：NVIDIA/Intel/rigaya/Gyroflow/GPAC 第一方文档与源码；本地已存档于
  `docs/reference/`（NVEncC/QSVEncC Options、ReleaseNotes、多卡 `--check-features`
  dump、QSVEnc issue 存档等）。
- **[社区]**：Doom9/Reddit/厂商论坛/issue 追踪的第三方实测与口碑。

每条关键结论都标注证据等级与来源；"证据缺口"单独列出。

---

## 2. 代码现状核查（本轮重读结果）

本轮对 `1kt.py`、`core/`、`encoders/`、`preservation/` 全量重读，与旧评估相比的
**状态变化**（旧版描述已过时的地方）：

| 旧版描述 | 现状（2026-08-29） |
|---|---|
| QSV `tu/tu_level/mbrc/output_buf_mb` 4 键被白名单跳过 | **已修复**：`tu→--quality`、`mbrc→--mbbrc`、`output_buf_mb→--output-buf`；`tu_level` 因 QSVEncC 无对应旗标而移除。`--quality best` 现已真实生效（吞吐影响见 §5.2） |
| 色彩元数据不在评估范围 | **已落地**：`core/color.py` + 两后端 color 四件套 + atc-sei + mdcv/clli；validate/selfcheck 增 critical 色彩对比项（提交 `e295ef9`，tag `post_color_meta`） |
| "64 项自检" | selfcheck 项数按机型结构浮动（本机实测 C9037 = 51 项），文档口径统一为"逐项 PASS" |

**机制层清单（重读确认，全部有实测背书）**：
- 能力探测（`--check-features` 文本解析 + 保守回退 + 每运行查询存盘）；
- 旗标白名单（对 `--help` 自省，工具升级自适应）；
- 三级降级梯（能力预判 / 运行时格式降级 10bit420→8bit420 / reader→MP4Box strip；
  环境类失败立即 FATAL；`--no-downgrade` 可拒绝）；
- Sony 保留管线（提取→注入编码→MP4Box 原生轨复制重建→tref/nrtm→flatten→brand→
  uuid 字节补丁→stts 时长修复→lens item_type 补丁→validate→Gyroflow→report）；
- 1:1 帧数/帧率闸门（Sony 路径）、`_encoded_ok` 中间体完整性闸门；
- VFR→`--avsync forcecfr`；解码恒 `--avsw`（5.2 矩阵证实硬解丢帧）；
- 失败分类 + `failed_files.json` + 详情文件 + `--retry-list`；
- `AdaptiveJobs` 波次自适应、双窗口看板、断点续跑（manifest/encoded/final 逐级复用）。

---

## 3. 硬件能力事实（官方 + 本机）

### 3.1 NVENC（RTX 5070 Laptop，驱动 596.36，NVEncC 9.31 / SDK 13.1）

**[本机]** 本地 `--check-features` dump（`work/final_nvenc/.1ktwork/caps/`）确认：
- **物理 NVENC 引擎 = 1**（GB206，与 RTX 5060 同核；5070 Ti=2、5090=3。此前的
  "2 路零损耗、3 路饱和、~50fps 封顶"实测与单引擎模型完全吻合）；
- HEVC：**Max B-frames 5**、B Ref Mode 3（each+middle）、**Max Level 6.2**、
  **4:2:2 ✅、4:4:4 ✅、10bit ✅**、最大 8192×8192、Lookahead/Lookahead-Level ✅、
  Temporal Filter ✅；
- NVDEC：**HEVC 4:2:2（8/10/12bit）硬解 ✅**（Blackwell 新增，播放端意义见 §6.1）。

**[官方]** 4:2:2 编解码是 Blackwell 首代能力（Ada 只到 4:4:4；本地 `gpu_rtx4090.txt`
dump 佐证）；SDK 13.0 "Powered by Blackwell"，要求驱动 ≥570。

**[官方]** NVEncC 关键旗标语义（本地 `docs/reference/nvenc/NVEncC_Options.en.md`）：
`--qvbr` 0-51（默认 RC 模式，等价 `--vbr 0 --vbr-quality`）；`--lookahead` 0-32 +
`--lookahead-level` 0-3；`--tune uhq` 仅 Turing+；`--tf-level` 要求 **bframes≥4**
（本项目 5 ✓）；`--atc-sei` 为 **HEVC-only** 的 HLG 信令 SEI；`--max-cll`/
`--master-display` 支持 `copy`（隐式开 `--repeat-headers`）；HEVC profile 列表无
显式 main422，4:2:2 靠 NVENC 自动提升为 Rext（本机实测确认）。

**[官方/社区]** 会话上限：consumer GeForce = **8 路并发会话**（2023 起），与物理
引擎数无关；每路 4K+lookahead 上下文约 100-250MB VRAM（无官方表，OBS 泄漏案例 +
FFmpeg 补丁佐证）→ **8GB 卡实际并发上限约 3-5 路**，且多路触发笔记本 Dynamic Boost
功耗墙。`AdaptiveJobs` 的 fps 收缩机制对此天然兜底，但未做过 5 路以上压力实测。

**[社区]** NVENC HEVC 质量口碑：中高码率（lookahead+AQ+multipass）接近 x265 medium；
低码率仍弱于软件（Doom9 一致结论）；Tom's Hardware 2023 实测 Ada NVENC 为当时 GPU
编码质量最优。**Blackwell 相对 Ada 的 HEVC 压缩效率增益缺乏第三方量化**（公开卖点
在 4:2:2/AV1），本项目按"≈Ada 水平"定性。

**[社区/官方]** 版本线：8.00beta1 时 rigaya 标注 yuv422 输出 experimental/untested
（作者当时无 Blackwell 卡）；**8.11 修复 RTX50xx `CUDA_ERROR_MAP_FAILED`（默认
`--cuda-mt 0`）**；9.05 起 `--ref` 默认 5；本项目 9.31（SDK 13.1）已含上述修复。
596.36 驱动未检索到 NVENC 编码回归报告；R57x 系在 **Linux/多卡/容器** 场景有 NVENC
reset/单卡工作问题（与本机 Windows 单卡场景不直接相关，但提示回归历史非零）。

### 3.2 QSV（Arc 140T，驱动 32.0.101.8974，QSVEncC 8.26 / Media SDK 2.16）

**[官方]** Intel Arc 矩阵（Article 000098345）：HEVC **解码** 8/10/12bit + 4:2:2/4:4:4；
**编码** 4:2:0 8/10bit（main/main10）。4:2:2 直编是慢路径（本机实测 1.0x vs 转换 2.0x）
——项目"4:2:2 一律转 4:2:0"政策正确。

**[本机/官方]** 三份本地特征 dump（ARL u5 245K / BMG B580 / DG2 A380）互证：
HEVC FF 编码 10bit ✅、ICQ ✅、CBR/VBR/QVBR/CQP ✅、**LA/LAHRD/LAICQ 全 x**
（Arc 全系无 HEVC lookahead）；H.264 10bit 编解全 x；`VIDEO_SIGNAL/CHROMALOC/VUI`
全 o（色彩信号写入能力无缺口）。

**[官方]** QSVEncC 关键旗标语义（本地 `docs/reference/qsv/QSVEncC_Options.en.md`）：
`-u,--quality` = TargetUsage 7 档（best…fastest，默认 balanced）；`--mbbrc` 逐宏块
码控；`--output-buf` 输出缓冲 MB（默认 8）；`--scenario-info archive` 场景提示；
`--hyper-mode` 是 Deep Link（iGPU+dGPU）选项，纯 iGPU 机器 N/A。
**`--workaround-hevc10bit-enctools`**（默认开）= 仅针对 **≤AlderLake 旧 iGPU** 的
10bit 图像损坏规避，**与 Arc 无关**（Arc 上 EncTools 的 `--tune` 在 HEVC 本就为 x）。

**两个需要正视的发现**：
1. **`qsv.json` 的 `lookahead`（→`--la-depth`）在 Arc 上被工具接受但特性无效**
   （LA 全 x；rigaya 本人口径："`--la-depth` only works for `--la` which is only
   supported in H.264"）。旗标白名单只验证"工具认识旗标"，**不验证"硬件实现该特性"**
   ——现有能力探测只覆盖 chroma/depth 矩阵，LA/ScenarioInfo 类特性无探测。属"静默
   无效参数"，需机型级跳过 + WARNING（或从 qsv.json 移除）。
2. **`--scenario-info archive` 在 Arc 上的实际效果未量化**（特征矩阵标 o，但
   issue #96 有"探测支持 ≠ 实际有效"的提示）。

**[社区]** 质量与风险：Arc HEVC 因无 LA/Tune，**质量天花板低于 NVENC-with-lookahead**
与 Intel-iGPU-with-LA（rigaya 的 max-quality 配方依赖 EncTools+LA，Arc 不可用）——
可用的杠杆只有 `--quality best` + bframes + SAO + MBBRC；rigaya vq_results 的定量
VMAF 数据在未缓存的 `vq_results_data.js` 中（证据缺口）。**驱动回归是本路径最大
风险**：2025 年 32.0.101.6557/6559 曾批量破坏 QSV HW 编码（HandBrake discussion
#6627、OBS、IGCIT #1006）；当前 8974 是否波及 QSVEncC 8.26 未核实，**上线前必须
目标机冒烟**（本会话已跑通 full check 全链，方向性证据存在）。

---

## 4. 编码参数与工具语义核查（官方文档对照结论）

| 项 | 核查结果 |
|---|---|
| nvenc.json 全部核心键 | 与 NVEncC 9.31 官方语义一致；`--tune uhq`（Turing+）、`--tf-level`（bframes≥4）、`--bref-mode each/middle`（HEVC 可用集）、B 帧 5 = 硬件上限 |
| nvenc.json `atc_sei: auto` | 曾因"atc_sei 是 AV1"的误注释被跳过；已修正并显式传递（HEVC-only SEI，HLG 信令） |
| nvenc.json `split_enc/parallel/output_buf/cuda_schedule/avoid_idle_clock` | 仍被 `_SKIPPED_ALWAYS` 跳过；**5070 Laptop 仅 1 物理引擎**，`--split-enc`（多引擎分片）本机大概率无收益，`--output-buf 64` 值得 A/B（重写结论：不再是"顺手打开"，需按单引擎现实重估） |
| qsv.json `tu→--quality best` | 修复后真实生效，吞吐重定位（§5.2）；FAST 档 `balanced` 不变 |
| qsv.json `lookahead→--la-depth` | **Arc 上无效（LA 全 x）**——待机型级处理（§3.2） |
| qsv.json `adaptive_* / sao / gpb / mbrc` | 全部被 8.26 接受且属 Arc 可用特性集（FadeDetect/Adaptive_I/B/WeightP/B/B_Pyramid/ManyBframes = o） |
| 色彩四件套 + mdcv/clli + atc-sei | 两工具官方选项齐全（`auto`/`copy` 语义确认）；本项目显式传值 + selfcheck 对比，实测 bt709 全保留（§5.1） |
| `--avsw` 软解策略 | 官方 reader 语义 + 5.2 帧精确性矩阵双重背书；Blackwell NVDEC 已支持 4:2:2 硬解，但硬解丢帧问题在 rigaya reader 层（非 NVDEC 层），保持恒软解 |

---

## 5. 本机复测数据（2026-08-29，本轮评估新增）

### 5.1 色彩元数据端到端（修复验证）

A7M4 C9037（XAVC-S 4:2:2 10bit）双后端 `--check full`：
- NVENC（Rext 直编）与 QSV（4:2:0 政策）均 **selfcheck 51/51 PASS、validate 45/0/0、
  Gyroflow PASS（13013 IMU）**；
- 成品 colr：`bt709/bt709/bt709/tv` 与源一致（修复前为 unknown）——HLG/HDR10 路径
  已具备（token 表按两工具 help 逐字核对），**但缺真实 HLG/HDR10 样本端到端验证**。

### 5.2 双后端吞吐基准（29s 4K60 XAVC 样本，1740 帧，HQ 档）

| 后端 | 吞吐 | 体积/码率 | 备注 |
|---|---|---|---|
| NVENC HQ（quality+tune hq，qvbr 25） | **22.9 fps** | 85.3 MB / 24.7 Mbps | 与历史 24.2-26.6fps 一致 |
| QSV HQ（**quality best**，icq 22） | **51.6 fps** | 60.1 MB / 17.3 Mbps | `tu=best` 修复后复测；**旧 ~106fps 注记作废**（那是默认 balanced 下测的） |

### 5.3 同档跨后端质量不对齐（SSIM/PSNR，指示性数据）

同源同"HQ 档"产物逐帧对比（10bit，帧数 1740 对齐；SSIM 时间基警告存在，作指示性）：

| 后端 | SSIM All | PSNR avg | 体积 |
|---|---|---|---|
| NVENC HQ | **0.9902** | **47.4 dB** | 85.3 MB |
| QSV HQ | 0.9879 | 44.3 dB | 60.1 MB |

**结论**：NVENC HQ 体积大 30% 但客观指标明显更高——两后端"同档"**质量并不对齐**
（icq 22 ≠ qvbr 25 的视觉等价点）。这正是 `--experimental-multihw` 免责声明的实证
依据，也量化了 VMAF 标定的必要性：混跑或换后端前必须先做感知指标等价标定。

### 5.4 能力 dump（本机，填补调研硬缺口）

`--check-features` 本机 dump 确认 5070 Laptop = **1 物理 NVENC 引擎**、HEVC
B5/L6.2/4:2:2/10bit/8192²、NVDEC 4:2:2 硬解 ✅（§3.1 已引用）。

### 5.5 异常记录（本会话）

一次 ad-hoc QSV 短样本编码（含 `--la-depth 20 --hyper-mode adaptive`，`work/bench`
下）期间**系统内存耗尽/闪退**（32GB 机器，事后恢复，无残留进程）。单次发生、根因
未定位——`--la-depth` 在 Arc 上的缓冲分配行为（rigaya issue #87 有
"insufficient buffer"记录）是首要怀疑对象。**列为上线前必须复现定位的阻塞项**。

---

## 6. 生态与兼容性（官方 + 社区）

### 6.1 HEVC Rext 4:2:2 播放边界

**[官方/社区]** 4:2:2 HEVC 是"剪辑/专业"格式，不是"随手播"格式：
- 硬解：**仅 2025 起的 NVIDIA Blackwell**（NVDEC 4:2:2 新增；Adobe Premiere 已
  对接）；Ampere/Ada 及更早、Intel、Apple、移动 SoC、电视芯片均只有 4:2:0；
- 软解：FFmpeg 系（VLC/mpv/PotPlayer）可播，4K 高码率吃 CPU；
- Windows 系统播放器/QuickTime 对 4:2:2 普遍不支持或半残（dpreview 实测帖）。
项目文档 `docs/design/hevc_422_rext_compatibility.md` 已给出矩阵与归档建议
（Rext=归档母本；分发用 4:2:0 副本）。**归档定位下此边界可接受，但必须在交付
说明中显式告知用户。**

### 6.2 main10 4:2:0 基线

**[官方]** 全平台近 100% 硬解（Android CDD 强制、iOS/macOS、全部电视）——QSV
政策路径（4:2:2→4:2:0）与 XAVC-HS 源的产物落在此兼容最安全的形态。

### 6.3 rtmd/nrtm/vendor-uuid 生态（本项目最大外部风险，对策已实证）

**[官方/社区]** 三类数据全部为索尼私有、无公开规范：
- Gyroflow 是唯一可靠消费方（第一方源码 `sony.rs` 逆向实现；官方支持页有机型清单；
  **A7M5 是否在官方清单未确认，但本机 Gyroflow 1.6.3 实测可读 A7M5 rtmd**
  （C0886/C0887 验收 PASS，IMU 逐样本比对））；
- FFmpeg 重封装默认丢索尼私有 box（trac #5901/#6793）；Catalyst Browse 对第三方
  重封装文件兼容性脆弱（社区多帖）；
- 社区仅有的相关工具是 xavc_rtmd2srt（rtmd+GPS 提取）。
**项目对策（已实证）**：rtmd/音频走 MP4Box 原生轨级复制（逐字节 sha256 验证）、
uuid 走 isobmf 字节级补丁、nrtm 走 set-meta/add-item/set-xml + item_type 字节补丁、
Gyroflow 消费端校验作为闸门。**残余风险**：Catalyst Browse 双端验证未做（目标工作流
若含 Catalyst 回读需补测）；FX 系列机型结构未验证（预期同构，无样本）。

### 6.4 GPAC/MP4Box 已知坑（社区 issue + 项目对策）

**[社区]** timescale 600（gpac #28/#581）、colr 保留（commit 3c25d9d、#1636/#396）、
tref/chapter 复制（#874/#2209）均为 GPAC 长期已知行为；重建式导入对私有 box 不友好。
**项目对策（已实证）**：movie timescale 线程化贯穿每个改写命令（5.2/6.3 节实测矩阵）、
tref 显式 `-ref` 重加、uuid 字节补丁（绕过 GPAC 不写 uuid 的限制）、`-flat`/`-brand`
分离调用、每趟行为都有二分定位记录。**残余风险**：全部结论绑定 GPAC 26.02——
版本升级必须重跑时序矩阵（设计文档已列回归门槛）。

### 6.5 归档定位的客观边界

**[官方/社区]** 行业"母版/长期保存"标准是 FFV1/MKV 或 ProRes/DPX；HEVC（含 10bit）
属交付/分发编码，不被档案机构认可为长期保存母版。HEVC 10bit 作为"个人/团队拍摄
素材的压缩备份副本"是普遍且被接受的做法。**建议产品措辞从"归档"精确为"压缩归档
副本"，文档明示有损、建议另存无损母版（或保留原卡/原始文件）。**

---

## 7. 生产就绪度判定

### 7.1 逐路径判定（同 §0 表）

### 7.2 阻塞项（上线前必须，按证据强度排序）

| # | 项 | 证据/影响 |
|---|---|---|
| B1 | **质量标定缺失** | 四档质量只有主观注记；§5.3 已实证同档跨后端质量不对齐。归档工具的质量主张必须靠 VMAF/SSIMULACRA 标定背书（rigaya vq_results 方法可复用）。不阻塞单后端上线，但阻塞 multihw 与"档位质量"承诺 |
| B2 | **HLG/HDR10 端到端验证** | 代码路径就绪、token 表按 help 逐字核对，但只实测过 bt709。需一个 HLG 样本跑 full check（含 Gyroflow） |
| B3 | **QSV 编码期间内存耗尽复现** | §5.5 单次事件，根因未定位（怀疑 `--la-depth` 在 Arc 的缓冲分配）。上线前需复现/定位/规避 |
| B4 | **样本覆盖** | 缺 XAVC S-I（All-I 10bit 4:2:2）、4K120、FX 系机型；A7M4/A7M5 覆盖良好。S-I 是"归档"最重要的源形态之一 |
| B5 | **驱动/工具版本对冒烟闸门** | QSV 驱动 6557/6559 曾有批量编码破坏史；8974+QSVEncC 8.26 组合仅本会话方向性验证。建议把"编码冒烟"固化为升级规程 |

### 7.3 高优先级（上线后立即/并行）

| # | 项 | 说明 |
|---|---|---|
| H1 | qsv.json `lookahead` 机型级处理 | Arc 上静默无效参数（§3.2/§4）——按能力探测跳过 + WARNING，或从 qsv.json 移除（8.26 无 LA 语义） |
| H2 | multihw 质量一致性标定 | B1 完成后解除 experimental 声明；否则维持禁用 |
| H3 | NVENC gop_len 统一 600 | 当前 `gop_len 0`（auto≈2-5s）与 QSV/x265 的 600 不一致，seek 粒度与压缩效率略优可拿 |
| H4 | 故障注入演练 | 降级梯三档、strip 回退、环境失败路径只经历史故障顺带验证过，未做系统演练（含 FATAL 审计链） |
| H5 | pytest 单测 | caps 解析、plan_initial_format、classify_failure、color token 表是纯函数，单测成本极低、回归价值高 |
| H6 | 版本漂移自检 | 启动时比对 NVEncC 9.31/QSVEncC 8.26/驱动 596.36/GPAC 26.02，漂移给显著 WARNING |
| H7 | 输出 sanity 检查 | 成品码率异常（<源 30% 或 >源 120%）、帧率/时长漂移的自动报警（validate 已有帧数时长，补码率比） |

### 7.4 建议项

- `--output-buf 64` 与 `--cuda-schedule spin` A/B（单引擎 5070L 的缓冲队列收益）；
- `--split-enc` 本机预期无收益（1 引擎），可跳过；
- Catalyst Browse 双端验证（若工作流含索尼官方工具回读）；
- 8GB VRAM 多路压力测试（3-5 路 4K 并发 + 功耗墙观测）；
- 归档措辞调整（§6.5）；
- DJI/非 Sony 全轨道保留（✅ 已落地，2026-08-30：djmd/dbgi/tmcd 原生保留 + 载荷校验 + Gyroflow 四元数消费端校验；mjpeg 封面/udta 因 GPAC 不可寻址仍丢弃）；

---

## 8. 风险清单

| 风险 | 概率 | 影响 | 缓解现状 |
|---|---|---|---|
| QSV/Arc 驱动回归破坏编码 | 中（有历史：6557/6559） | 高 | 钉版本 + 升级冒烟规程（B5）；NVENC 为主后端可兜底 |
| rigaya mp4 muxer 毫秒 elst 截断 | 已证实存在（0.5ms） | 中（Gyroflow 容差内，validate critical） | stts 基准字节补丁已内置（幂等） |
| GPAC 版本升级行为漂移 | 中（26.02 大量 quirk 已被硬编码对策） | 高 | 升级必须重跑时序矩阵 + A7M4/A7M5 全链（设计文档已列） |
| 索尼私有格式变更（新机型 rtmd/nrtm 结构变化） | 低-中 | 高 | 提取/校验失败即失败该文件（不静默交付），failed_files 可审计 |
| Rext 4:2:2 播放面窄 | 已证实 | 中 | 文档化边界（hevc_422_rext_compatibility.md）；分发走 4:2:0 副本 |
| 8GB VRAM/功耗墙限并发 | 中（高并发时） | 低 | AdaptiveJobs fps 收缩兜底；上限 8 未实测到顶 |
| 单机验证（无第二台机器回归） | 中 | 中 | 机队能力已建模（caps 探测 + 降级梯），缺真实老卡实测 |
| 归档有损定位误用 | 中（用户认知） | 中 | 文档措辞（§6.5）+ README 定位声明 |

---

## 9. 与 AV1 / x265 评估的衔接

- **AV1**（`av1_feasibility_report.md`）：AV1 定位为非 XAVC 经典路径选项、XAVC 恒
  HEVC——本评估进一步坐实 HEVC 是保留管线的唯一默认（4:2:2 保真是 AV1 全路径无法
  替代的，§3.1 的 Rext 能力即该决策的硬件基础）。
- **x265**（姊妹篇 x265 生产评估）：软件路径维持手动档；其 PROVISIONAL 标定与
  本评估 B1（硬件档质量标定）是同一类工作——建议合并做一轮"全后端质量标定
  （VMAF/SSIMULACRA）"，一次覆盖 NVENC/QSV/x265 三后端的档位质量主张。
- 机制复用结论不变：AV1 后端可复用白名单/降级梯/失败分类/调度/看板全部机制，
  需要新写的只有能力解析、PARAM_MAP 与标定。

---

## 10. 证据索引

**本机（代码/实测）**：
- `docs/design/hardware_backend_design.md`（5.2 帧精确性矩阵、5.3/5.4 速度矩阵、6 全链验收）
- `docs/design/implementation_report.md`（S1-S5、§7 15/15、§11 并行实测、§14 双后端验收）
- `docs/design/INTEGRATION_REPORT.md`（GPAC 原生时序方案）
- `docs/design/hevc_422_rext_compatibility.md`（Rext 播放边界）
- `work/final_nvenc/.1ktwork/caps/`（本机 5070L dump：1 引擎/B5/L6.2/4:2:2/NVDec 4:2:2）
- `work/bench/`（本会话基准：吞吐/体积/SSIM/PSNR 原始数据）
- git `e295ef9`（色彩元数据修复 + QSV flag 映射修复，tag `post_color_meta`）

**本地存档的官方文档**：
- `docs/reference/nvenc/`：NVEncC_Options.en.md、ReleaseNotes.md、nvidia_sdk13_blackwell.txt、gpu_rtx5070ti/5090/5060.txt、gpu_rtx4090.txt
- `docs/reference/qsv/`：QSVEncC_Options.en.md、QSVEnc_Readme.md、QSVEnc_ARL_u5_245K_Win.txt、QSVEnc_BMG_Arc_B580_Win.txt、QSVEnc_DG2_Arc_A380_Win.txt、issue_87/96/253 存档、a310_bframes.html、vq_results*.html

**关键外部来源（社区/官方，URL）**：
- NVIDIA 官方矩阵 https://developer.nvidia.com/video-encode-decode-support-matrix ；Blackwell 4:2:2 https://videocardz.com/newz/nvidia-geforce-rtx-50-series-adds-support-for-422-color-format-video-decoding-and-encoding ；SDK 13.0 博客 https://developer.nvidia.com/blog/nvidia-video-codec-sdk-13-0-powered-by-nvidia-blackwell/
- NVENC 会话上限 https://linustechtips.com/topic/1555694-nvidia-quietly-increased-nvenc-limit-again-from-5-to-8-concurrent-encodes-on-consumer-cards/ ；OBS VRAM https://github.com/obsproject/obs-studio/issues/13656
- Intel Arc 矩阵 https://www.intel.com/content/www/us/en/support/articles/000098345/graphics.html ；oneVPL 能力参考 https://www.intel.com/content/www/us/en/docs/onevpl/developer-reference-media-intel-hardware/1-1/overview.html ；TU 语义 https://community.intel.com/t5/Media-Intel-Video-Processing/Target-usage/m-p/1065657
- QSV 驱动回归：https://github.com/HandBrake/HandBrake/discussions/6627 、https://github.com/IGCIT/Intel-GPU-Community-Issue-Tracker-IGCIT/issues/1006 、https://community.intel.com/t5/Intel-Arc-Discrete-Graphics/QSV-HW-encoding-not-working-in-OBS-with-driver-version-32-0-101/m-p/1668170
- rigaya QSVEnc https://github.com/rigaya/QSVEnc （issue #87/#96 LA 口径）；vq_results https://rigaya.github.io/vq_results/
- Gyroflow Sony 支持 https://docs.gyroflow.xyz/app/getting-started/supported-cameras/sony ；sony.rs https://github.com/gyroflow/gyroflow/blob/35c5315a/src/core/gyro_source/sony.rs ；xavc_rtmd2srt https://github.com/SK-Hardwired/xavc_rtmd2srt
- FFmpeg trac #5901/#6793/#7756、trac.ffmpeg.org/ticket/11097
- GPAC issues https://github.com/gpac/gpac/issues/28 、/581、/874、/1636、/2209、/396
- 播放兼容实测 https://www.dpreview.com/forums/threads/win-11-hevc-10bit-4-2-2-half-works.4821247/ ；LAVFilters https://github.com/Nevcairiel/LAVFilters/issues/547 ；PugetSystems Resolve/Premiere 硬解矩阵
- 归档标准：https://blog.rockarch.org/FFV1-at-the-RAC-Part-1-The-Rationale 、https://trac.ffmpeg.org/wiki/Encode/FFV1 ；Doom9 归档讨论 https://forum.doom9.net/printthread.php?t=183030
- NVENC 质量口碑：https://forum.doom9.net/showthread.php?p=1986573 、https://forum.doom9.org/printthread.php?t=175091 、https://www.chiphell.com/archiver/tid-2453532.html
