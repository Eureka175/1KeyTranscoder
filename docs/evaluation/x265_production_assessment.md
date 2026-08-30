# x265 实现生产就绪评估（重写版）

> 评估日期：2026-08。方法：①重读项目 x265 全部代码路径；②x265 官方文档
> （readthedocs master，对应本机 x265 4.3+6）**逐参数事实核查**；③本机实证
> （真实 Sony 4K60/4K30 素材：4 档管线解析 + 短段真实编码）；④社区实测调研。
> 证据产物：`work/x265_prod_eval/`（管线解析 dump、真实编码日志与输出）。
>
> **结论前置：代码与命令层正确、可以进生产；档位数值层不能。**
> 按现有 PROVISIONAL 状态与本文 §4/§9 的问题清单，x265 应定位为
> "**完成修复 + 标定后发布的手动高压缩档**"，当前不具备作为默认档位
> 发布的条件；4K60 大宗归档不推荐作为主路径（吞吐现实，见 §6）。

---

## 1. 总判定表

| 维度 | 判定 | 一句话依据 |
|---|---|---|
| 命令/管线正确性 | ✅ **可进生产** | 本机实证：4 档完整解析 + x265 4.3 真实素材编码零告警零错误；4:2:2 10bit 自动选型正确 |
| 参数合法性 | ✅（1 处实质问题） | 官方文档逐项核对 71 参数全部合法；FAST 档 `rd=2` 使 psy-rd 失效；`threaded-me` JSON 中为关闭（=0），与官方结论一致、无需改动 |
| 归档语义 | ⚠️ 有张力 | `info=1` 破坏可复现性；`no-strong-intra-smoothing=1` 增加 banding 风险；level/CPB 声明超出规格 |
| 数值标定 | ⚠️ 缩放规则自标 PROVISIONAL | x265_scaling.json 缩放比率自标 PROVISIONAL；**档位速度注记（285H@10/2/0.8fps）为作者实测标定值，为权威数据**（本机复测因前台负载偏低，见 §6） |
| 验证证据 | ⚠️ 不足 | 当前日志无 x265 端到端验证记录（溯源见 §7）；机制存在、留档缺失 |
| 吞吐现实 | ⚠️ **决定性** | 4K60：FAST 15x / HQ 57x / UHQ 180x 实时（受压下限）；无并行调度 |
| 运维完备 | ⚠️ 有差距 | 无失败详情记录、看板逐文件状态不更新、双入口并存 |

---

## 2. 代码正确性（本机实证，全部通过）

1. **管线解析**：对真实素材（C0886，4K60 10bit 4:2:0，XAVC-HS 150M）跑完整
   链 probe→classify→scale→serialize，UHQ/HQ/SMALL/FAST 四档均产出结构完整、
   语义正确的 ffmpeg 命令（含动态 VBV 计算、FR* 表达式、deblock 列表格式）。
2. **真实编码**：四档短段编码全部 rc=0、**x265 零 warning/error**；
   x265 4.3 确认 Main 10 / Level 6.1 High tier / 16 线程 / wpp 34 行。
3. **4:2:2 10bit**：a7m4 XAVC-S（yuv422p10le）编码成功，x265 自动选
   **Main 4:2:2 10** profile（ffprobe 显示 "Rext" 是 profile_idc=4 的通用
   命名，非缺陷；显式/自动均正确）。
4. **Sony 管线集成**：`fix_hw_timing=False` 正确（ffmpeg 中间件无毫秒量化
   问题）；编码后帧数+帧率 1:1 校验（`count_frames` 对比）在场。
5. **一处文案缺陷**：`preservation/validate.py` 的 `video.encode` 项硬编码
   "libx265 intermediate"——对 NVENC/QSV 输出也打 x265 标签（本评估溯源时
   已踩此坑），属误导性文案，需按后端区分。

---

## 3. 官方文档逐参数核查（x265 4.3 / readthedocs master）

**71 个参数全部与官方 CLI 一致、值域合法**（版本门槛 fades≥3.1、
hist-scenecut≥3.3、aq-mode 4≥3.2 均满足）。但核查发现实质性问题：

| # | 问题 | 影响 | 处置建议 |
|---|---|---|---|
| 1 | **FAST 档 `rd=2`**：官方要求 psy-rd/tskip/ssim-rd/cu-lossless 需 `rd≥3` | FAST 的 `psy-rd=1.5` 是**死参数**（静默失效），档位真实行为与配置意图不符 | FAST 升 `rd=3`（激活 psy-rd），或删除死参数 |
| 2 | `threaded-me`（4.2+ 实验参数）：官方注明**启用时**与 VBV 互斥且降低压缩效率 | **JSON 中从未启用**（四档 `"threaded_me": false`，序列化为 0），与官方"VBV 下禁用"结论一致，属显式默认关闭、无行为差异 | **无需改动**（保留即可） |
| 3 | `info=1`：写入构建信息 SEI | 同一参数在不同 x265 构建间**输出不可复现**，与归档目标相悖 | 改 `info=0` |
| 4 | `no-strong-intra-smoothing=1`：移除防条带平滑 | 天空/渐变类素材 banding 风险上升（对 10bit 影响小但非零） | 删除该键（恢复默认平滑） |
| 5 | **level 6.1 过声明 + CPB 超限**：4K60@89-125Mbps 最低合规是 5.1+high-tier；实测动态 VBV 产出 bufsize HQ 268Mbit/UHQ 376Mbit，**超过 Level 6.1 High tier MaxCPB 240Mbit**（x265 不告警，hrd=1 下仍写入） | 严格 level 合规不成立（消费端多数容忍） | level 改 6.2，或 vbv 规则加 CPB 上限钳位，或 bufsize_factor 3.0→2.0 |
| 6 | `limit-sao`（SAO 早退）：画质有轻微代价 | 可接受权衡 | 保留，档位说明中注明 |

其他核查结论：`const-vbv`+CRF 是官方认可的确定性 VBV 组合（归档应开）；
CRF 需 maxrate+bufsize 同时非零才激活 VBV 封顶（本配置满足）；
官方无"归档"预设，最接近官方近无损路线的是 slow/veryslow + 高 CRF +
可选 `--cu-lossless`（SSIM≥0.9999）；逐位可复现只有 `--lossless`。

---

## 4. 数值标定状态（未标定明细）

`x265_scaling.json` 顶部自声明 + 逐项标注 PROVISIONAL，具体：

1. **源分类阈值**：normalized_ob 低/高档界 0.12/0.25——文件内自注"校准候选"；
   参考点仅 4 个（含一个 1080p30 10Mbps 外推），未对 testsets 全谱验证。
2. **16 组动态 VBV 比率**（4 档 × 4 类）：全部 PROVISIONAL；INTRA_LIKE 行
   明确标注"无真实 All-I 样本未标定"。本机实证显示其产出值域
   （89-125Mbps maxrate）对 150-179Mbps 源合理，但**无画质指标闭环验证**。
3. **merange 空间缩放钳位**（16-92）：PROVISIONAL（"6K/8K 素材校准后定稿"）。
4. **档位吞吐注记为作者标定值**：x265.json 各档 description 的
   "285H@10/2/0.8fps" 为作者实测标定（权威数据，勿改）；本评估复测
   （前台有 PS/LR 负载时）为受压下限 FAST 4.0 / HQ 1.05 / UHQ 0.33 fps，
   不构成对档位描述的否定。

---

## 5. 归档语义评审

- **CRF 20-23 + 10bit**：官方默认 CRF 28，20-23 属高质段；但按社区两派
  共识（Doom9 实测 + HandBrake 官方 RF 表），**像素级透明在 CRF 16-18、
  观看距离近透明在 19-22**（4K）。→ 本档位定位是"**近透明**"而非
  "像素透明"：UHQ CRF 20 处于近透明下限，对逐像素级归档需求可考虑
  UHQ 降 CRF 至 18；10bit 对 banding 显著有益（社区共识，x265 上收益
  弱于 x264 时代但为正值）。
- **preset slow 为社区共识甜点**（veryslow 边际收益小：Fora Soft 实测
  medium −38% / slow −44% / veryslow −46% BD-rate）——UHQ/HQ/SMALL 用 slow、
  FAST 用 fast 的档位结构合理。
- **aq-mode 4**（auto-variance+edge，3.2+）：UHQ/HQ 使用、strength 0.9-1.0
  偏高，与 psy-rd 1.5 叠加有涂抹风险——标定时重点观察（社区共识：aq-mode 3
  strength 0.8-1.0 更保守，见 §8）。
- **deblock [-1,-1]**（UHQ/HQ）：去块滤波向细节保留倾斜，噪点素材观感好、
  平坦区风险略增——属风格选择，保留。
- **SAO：UHQ/FAST 关、HQ/SMALL 开**：档位间逻辑合理（高压缩档开 SAO）。
- **no-strong-intra-smoothing=1 全档开**：见 §3 问题 4，建议回退。

---

## 6. 性能现实（生产可行性核心）

本机实测（Core Ultra 9 285H 16 线程；**前台有 PS/LR 负载的下限值**；
**档位描述 10/2/0.8 fps 为作者标定值，以 JSON 为准**）：

| 档位 | 实测 fps（4K60 真实素材） | 相对实时(60p) | 1 小时素材耗时 |
|---|---|---|---|
| FAST | 4.0 | 15x | ~15 小时 |
| HQ | 1.05 | 57x | ~57 小时 |
| UHQ | 0.33 | 180x | ~180 小时 |

- 即便按作者标定值（10/2/0.8 fps），HQ 也 ≈30x 实时。**4K60 大宗批量归档
  在软件 x265 上不现实**（对比：本机 NVENC 4K60 实测 73fps）。
- **桌面级 CPU 参照**（社区众测，iXBT 论坛 preset slow CRF20）：7950X3D
  9.9fps / 9950X3D 11.5fps / 14900KF 10.7fps（4K60）→ 4K60 slow 约 6 倍
  实时（1 小时素材 ≈ 5-6 小时，桌面机器夜间批量勉强可行）；4K30 ≈
  14-18fps。本机 285H 受压实测 1.05fps 为笔记本下限。**结论不变：x265 是
  吞吐受限的软件路径，桌面机上"夜间批量"可行，笔记本上仅适合小素材集。**
- x265 路径**无 --jobs 并行**（顺序执行）；单实例已吃满 16 线程，
  并行边际收益有限（1080p 源例外，见下）。
- 按像素率换算：1080p60 ≈ 4K60 的 1/4 → HQ ≈ 4-8fps、UHQ ≈ 1.3fps，
  **1080p/4K30 素材勉强可接受**（夜间批量/小素材集）。
- x265 档定位因此明确：**手动高压缩档**（体积优先的冷归档、4:2:2 保真
  需求、非实时批处理），不是吞吐主路径。

---

## 7. 验证与证据链（含溯源更正）

1. **溯源更正**：`logs/preserve_reports/` 现有 14 份报告经 job_dir 核对
   **全部为 final_qsv（QSV）产出**；报告中 "libx265 intermediate" 字样是
   validate.py 硬编码文案（§2.5），**不能作为 x265 验证证据**。
2. **当前日志无 x265 端到端验证记录**：logs/total.log 仅 nvenc/qsv 批次。
3. 历史 08-23（pre_S1S5 基线时代）的 x265 全链验证机制存在（结构性
   PRESERVED + Gyroflow PASS 的管线与硬件路径共用），但报告已被后续批次
   覆盖，不可追溯。
4. `tests/run_selfcheck.py` 支持 `--encoder x265`（含 64 项自检 + Gyroflow），
   但未发现运行记录。
5. **上线前必须**：`python tests\run_selfcheck.py --encoder x265 --check full`
   留档一份 x265 端到端报告。

---

## 8. 社区实测共识（联网调研，全文见 docs/reference/x265/ 三份调研报告）

1. **CRF 共识**：透明 16-18 / 近透明 19-22（Doom9 实测 + HandBrake 官方
   RF 表：1080p 20-24、4K 22-28）；x265 CRF ≈ x264 CRF+3。【共识】
   本项目档位（20-23）落在"近透明"区间，符合"高质归档"但非"像素透明"。
2. **preset**：slow 为甜点（Fora Soft BD-rate：medium −38% / slow −44% /
   veryslow −46%）；benwaggoner（Amazon）"slower 才是 HEVC 工具真正生效处"。
   【共识】
3. **10-bit**：显著减 banding，x265 上收益弱于 x264 时代。【共识】
4. **画质争议**：x265"软/糊"真实存在（SAO 过激为主因），`--no-sao` 是保
   细节共识开关；低码率噪点素材 x265 落后 x264 为个案。【共识+个案】
5. **权威基准（MSU 2023-24 4K 10bit slow，BSQ-rate）**：SVT-AV1 仅需 x265
   的 **51-65% 码率**（省 35-49%）；但 iXBT 2026 4K HDR 近透明段实测
   x265 slow 在 40/20Mbps 的 VMAF 略高于 SVT-AV1 preset5（99.92 vs 99.74）
   ——**近透明高码率段两者都达标，分水岭是存储成本、解码生态与耗时**。
6. **硬件 vs 软件**：Fora Soft 实测 Ada NVENC HEVC 需 **+25% 码率**才追平
   x265 slow（+30% 追平 veryslow）；iXBT：x265 领先 QSV 1.4-1.6dB PSNR。
   【共识】→ 质量优先归档场景 x265 仍有不可替代性（本项目硬件后端画质
   差距的量化依据）。
7. **x265 4.0/4.1（2024）**：未改默认 AQ/SAO（仍是 aq-mode 2 + SAO 开），
   新特性（BBAQ/SBRC/mcstf 等）默认关闭。本项目 aq-mode 3/4 是**有意偏离
   官方默认**的激进选择，标定时必须验证其相对 aq-mode 2 的净收益。
8. **模板**：共识基线 `--preset slow --crf 16-18 --profile main10`；
   `--tune grain` 官方展开为一整套联动参数（aq-mode 0:cutree 0:ipratio 1.1:
   pbratio 1.0:qpstep 1:sao 0:psy-rd 4.0:psy-rdoq 10.0:rskip 0），社区评价
   偏负面，本项目未用（正确）。

调研限制（如实）：Reddit r/DataHoarder 帖子级原文因 IP 封禁未取得（HN +
第三方实测替代，未虚构）；FFmpeg trac 经 ASWF 镜像核对；MSU 完整分值为
付费版（取免费页内嵌 BSQ-rate JSON）。

---

## 9. 进入生产的条件清单

**P0（必须，否则不发布）：**
1. FAST 档 `rd` 2→3（激活 psy-rd；FAST 定位"快但有效"）
2. `info` true→false（归档可复现性）
3. 删除 `no_strong_intra_smoothing`（恢复默认防条带平滑）
4. level/CPB 修正：level-idc 改 6.2，或 vbv 规则加 CPB 上限钳位（≤240Mbit），
   或 bufsize_factor 3.0→2.0（三选一，建议钳位+6.2）
5. **一轮档位标定回归**：testsets 全谱（4K60/4K30/4:2:2/高噪点）× 4 档跑批，
   VMAF/SSIMULACRA 对比，校准分类阈值、VBV 比率、aq/psy 组合（档位速度
   注记已有作者标定，不动）
6. x265 端到端验证留档：`run_selfcheck --encoder x265 --check full`

**P1（应该）：**
7. failed_files.json 接入 x265 路径（当前只有计数，无详情记录）
8. 看板逐文件状态（当前 x265 循环不调用 status.start/finish）
9. validate.py `video.encode` 文案按后端区分
10. 双入口处理：`x265_archive.py`（1128 行旧单体）与 1kt.py x265 路径并存，
    二选一（建议退役 x265_archive.py，README 已只指向 1kt.py）

**P2（可选）：**
11. 1080p 素材的 2 路并行（--jobs 对 x265 的意义仅在小分辨率源）
12. scaling/classifier/serializer 的 pytest 单元测试

---

## 10. 结论

- **代码与命令层：生产可用**（本机实证充分：解析正确、编码零告警、
  4:2:2 正确、Sony 管线集成正确）。
- **档位层：条件性不通过**——PROVISIONAL 数值 + §3 参数问题清单 +
  验证留档缺失。修完 P0 后可作为**手动高压缩档**发布（`--encoder x265`
  显式选择，README 明示定位与吞吐预期）。
- **定位不变**：x265 是软件高压缩路径，不替代硬件后端；其价值在
  ①4:2:2 保真（唯一支持 4:2:2 的后端）、②近透明高码率段画质可靠且
  大幅优于硬件 HEVC（+25-30% 码率优势）、③NLE/XAVC 生态兼容与解码
  普及度（SVT-AV1 虽更省码率，但受 XAVC 合规与消费端限制，见 AV1
  评估）。4K60 大宗归档请用硬件后端。

## 附录 A. 证据与产物

- `work/x265_prod_eval/pipeline_dump.txt` — 四档在真实 4K60 源上的完整解析
- `work/x265_prod_eval/encode_real_out.txt` + `{FAST,HQ,UHQ}_real.log` —
  真实素材编码日志（零告警佐证）
- `work/x265_prod_eval/C9037_422.mov` — 4:2:2 10bit 编码产物
- `docs/reference/x265/` — 官方 CLI/presets/releasenotes/lossless 文档、
  HEVC level 表、社区调研三份报告（主报告 + Doom9 共识全文 + MSU/AV1
  基准全文，均带来源 URL 与测试条件）
