# 长镜头默认对齐实施 checkpoint（S0–S7）

> 对应任务书：[`long_take_default_alignment.md`](long_take_default_alignment.md)（已定稿）
> 本文件是**实施过程记录**：每个 S 阶段一行状态，附可复现命令与数字；问题与决策
> 就地记录，不静默改口径。开工 2026-10-29。
>
> **执行优先级（用户 2026-10-29 明确）**：先让**长程音频对齐**真正跑通（S1→S2→S4→
> 真实两文件），再补全"所有布局拆 CH"的结构泛化（S3 的 A6 部分）。任务书范围不减，
> 只是实施顺序调整。

## 状态总览

| 阶段 | 内容 | 状态 | 备注 |
|---|---|---|---|
| S0 | 入口接入审计 + 全布局结构还原预测试 | **完成** | teammate `tv-path-analyst`；产出 `work/long_take/s0/`；5/5 布局 PASS，A6 预测试通过 |
| S1 | stereo 绑定 + 锚点判据 + 窗测量原语 | **进行中** | teammate `long-take-algo` 实现 `measure_window_delays`；Lead 已用真实片段锁定 K 最终曲线口径 |
| S2 | 1 s 曲线 → records → 内置 plan/执行 | **进行中** | Lead：`core/long_take.py`（曲线构造/内置 `run_time_varying` 施加） |
| S3a | 默认开启 + 恒定优先 + 整文件时变 + 回退 | **进行中** | Lead：`core/channel_sync.py` 集成；先支持 4×mono（真实素材布局），多声道布局随后 |
| S3b | `--audio-plan alignment=enabled` participating-only PCM 准入 | **完成** | teammate `plan-admission`；unit 979→**992 PASS / 2 FAIL**（同两条环境性）；L3 实测 rc=1 |
| S4 | 第二轮残差 + 极性 | 待开始 | Lead：`core/long_take.py` |
| S5 | 并行预算 | 待开始 | 测量按 target 并行（连续性跟踪本身串行，见问题记录） |
| S6 | 真实两文件验收（曲线层→音频层） | 待开始 | **oracle 已确认：以 `_restored.mp4` 音频反推为准，applytv2 不是最终曲线**（见 S1-B） |
| S7 | 回归 + 文档 | 待开始 | unit/A1 分类/known-limitations；`hardware_audio.py` L3 旧断言待改 |

---

## S0 — 入口审计 + 结构还原预测试（完成，2026-10-29）

**产出**：`work/long_take/s0/entry_audit.md`、`structure_pretest.py`、`structure_pretest.json`。

**审计要点**：
- 10 个 `run_channel_sync` 调用点（1kt.py ×4、batch_hw ×3、archive_repair ×3），输出接线 4 类；
  全部入口都是"视频已定稿、仅音频可换"。
- 所有输出消费方的隐含契约：`audio_files` 与源音频流 **1:1**、每文件恰 1 条流 —— 这是
  "全布局拆 CH" 必须显式重建的地方；建议在 `run_channel_sync` 第 7 步内部**按源流重新交织**，
  让所有消费方零改动（S3 采纳）。
- 当前 `_decode_stream` 用 `-ac 1`（下混），不能当"拆声道"复用；S3 的多声道解码改用
  内置 `core.audio_pcm.AudioPCMReader`（已实测其 `read(channel_index=...)` 与既有 mono 解码逐样本一致）。
- 结构还原预测试：A) 1 stereo+1 mono B) 5.1 C) 2×stereo D) 4×mono E) 7.1 **5/5 PASS**；
  边界 3CH/8CH 2/2 PASS；`a6_action=none`。陷阱：重排会让 `[2,1]` 变 `[1,1,1]` 但
  `build_output_structure.ok` 仍为 True；bogus layout 只靠计数比较会 PASS；零声道流会消失
  → A6 判定必须由 S3 自己做（源 vs 输出逐流比较，且先看 `layout_verified`）。
- 两个接线缺口：透明入口不读 `--audio-plan`（静默忽略）；主程序无 `--no-channel-sync`
  （按用户决策"不提供 CLI 关闭开关"，记录为已知语义变化）。

---

## S1-A — 内置长帧测量可行性探针（负面结果，已定论）

**问题**：能否直接用内置 `estimate_pair` 把几何换成长帧（`frame_ms=30000`、`hop_ms=10000`）
代替研究口径的窗测量？

**命令**：`work/long_take/s1/probe_longframe.py`（6 min 片段，C1305 30:00–36:00）。

**结果**（对比 K 冻结曲线 `applytv2`）：
| 通道 | 与 K 曲线 median/p90/max Δ（采样） |
|---|---|
| CH2 | 0 / 0 / 0（恒定 −4，内置测量完全正确） |
| CH3 | 74.8 / 146.4 / 149.0 |
| CH4 | 53.5 / 174.6 / 180.9 |

**根因**：内置逐窗 GCC 取**最大正峰**；两素材 CH3/CH4 与 CH1/CH2 **极性相反**，真实 lag 是
负相关峰（如 30:00 处 +1005 负峰 vs +1144 正峰，幅度 0.392 vs 0.402，独立测量会选错分支）。
→ **结论**：必须实现连续性跟踪（S1 `measure_window_delays`），不能用长帧 `estimate_pair` 替代。

## S1-B — K oracle 口径确认（关键，2026-10-29）

**方法**：从 K 最终交付 `_restored.mp4` 用 **ffmpeg 解码**（sample-accurate seek，直接 f32）
取 4 条 mono，与源同时段逐 30 s 窗测 lag，反推 K **实际施加**的曲线。

**结果**（C1305，30:00–36:00）：
- K 实际施加：CH1 = **0**、CH2 = **−4**（常量）；CH3 ≈ **1019→1013**；CH4 ≈ **1302→1269**。
- `applytv2_*.json` 同时段为 CH3 1002→1139、CH4 1274→1381 —— **与最终交付不一致（最大差 125 采样）**，
  因为 round-3 fine pass 改过曲线。
- 交叉验证：K 交付音频内部相对滞后 CH1-CH2=0、CH1-CH3=−14、CH1-CH4=+3（与文档
  "强相干块残差中位 1–3" 一致），证明交付音频确实已对齐。

**结论（写入验收口径）**：
1. **曲线层 oracle = 从 `_restored.mp4` 音频反推的曲线**，`applytv2` 只作参考、不能当最终曲线；
   S6 报告必须写明"oracle 版本 = 从成品反推"。
2. 参考音频**必须用 ffmpeg 解码取流**，**禁止用 `-c:a copy` 的 mka/容器裁剪取流**：
   实测 copy-seek 在 4 轨 PCM 上会给每轨引入不同偏移（最大 ~0.5 s），把"已对齐"的 K 成品
   误测成 246/478/743 采样相对滞后。探针/验收一律直接解 f32（与生产 `_decode_stream`/
   `AudioPCMReader` 同路径）。
3. 已知与决策 C 的**预期差异**：K 把 CH1/CH2 的 −4 采样也修成 0；本方案按 C 保持组内相对偏移
   （−4 低于 5 样本最小修正门，实际两者都 untouched）。验收比对需允许 CH2 上这 4 采样差，
   或以"组内相对偏移保持源样"单独说明。

**可复现命令**：
```powershell
python work\long_take\s1\probe_longframe.py --clip ... --src-offset 1800
ffmpeg -ss 1800 -t 360 -i <source.mp4>   -map 0:a:N -f f32le ...   # 解码取流
ffmpeg -ss 1800 -t 360 -i <restored.mp4> -map 0:a:N -f f32le ...   # 同上, 不用 -c copy
```

---

## S3b — audio-plan 准入（完成）

- `alignment=enabled`：participating-only PCM 准入；参与流含非 PCM → 稳定 reason
  `alignment_enabled_rejected_non_pcm`，解码前拒绝，`ok=False` → 非零退出，无半成品。
- `auto`/`disabled` 行为逐字节不变；未参与的压缩流不否决 `enabled`。
- 验证：`--level unit` 992 PASS / 2 FAIL（环境性）；L3 audio_format 29/0、audio_external 19/0；
  真实 1kt.py + AAC 源 rc=1、输出目录 0 个 MP4。
- 遗留：`tests/selftest/suites/hardware_audio.py:575–591` 仍断言旧行为（需改）；
  docs 旧决策表（S7 统一更新）。

## S1 — 窗级连续性漂移测量（完成，2026-10-29）

**执行**：teammate `long-take-algo`（task-3）。**复用**：新增
`core/sync_estimate.measure_window_delays()` + `coarse_decimate()`，未改任何既有函数。
算法 = 研究已验证口径：12 kHz 池化粗扫 + 48 kHz 局部精化 + 连续性约束 + |CC| 峰
（极性无关）+ 首窗全搜索回退。`delay>0 = 目标晚到`，`out[n]=in[n+delay]`（合成信号
双向校准：+123→123.0、−200→−200.0）。

**验证数字**：
| 检查 | 结果 |
|---|---|
| 新增单测 `tests/selftest/suites/long_take.py` | **14 PASS / 0 FAIL（2.29 s）**；常量/线性漂移/反相/独立噪声/连续性/确定性/分块一致 |
| 真实片段 C1305 30:00–36:00（31 窗，60 s/10 s）vs K `track_*.json` | CH3 **median 0 / p90 1 / max 14**；CH4 **median 0 / p90 6 / max 16**（门 median≤2、max≤30）→ PASS |
| 初始 lag | CH3=1004、CH4=1307（与 K 一致）；整脚本 6.6 s |
| 全量 unit | 该模块 14/14 绿；其余 5 条 FAIL 是 channel_sync 默认值变更后旧断言未同步（S3 已同步修复） |

## S2/S4 — 曲线/内置计划/残差/极性（合成 + 真实片段，2026-10-29）

**新增** `core/long_take.py`（编排层，不实现施加）：窗记录 → 1 s 曲线（2 min |CC|
加权中位锚点、120 采样跳变限幅、|shift|≤4800）→ 内置 `audio_sync_tv_exec.run_time_varying`
施加 + 独立复测 → 5 min 块残差第二轮（50 % 重叠、|CC|≥0.25、3 点加权中位、仅
≥3 强块且残差中位≥1 采样）→ 极性投票（60 s/30 s、±16 采样带符号峰、负票≥0.8 且
中位 |CC|≥0.25）。

**合成验证**（`work/long_take/s2/synthetic_check.py`，全部 PASS）：
- 常量 +123：corr 0.999999999，内置 verify ok；
- 线性漂移 0→240 采样/120 s：施加后残差 **−1 采样**；
- 残差测量：注入 7 采样 → 恢复 **7**；
- 极性：同相 flip=False、反相 flip=True。

**真实片段端到端**（`work/long_take/s2/clip_align_check.py`，C1305 30:00–36:00，
CH1/CH2 按 stereo 组、CH3/CH4 独立；总耗时 **14.2 s**）：
| 通道 | 本方案实际施加 vs K 实际施加（30 s 窗） | 极性 |
|---|---|---|
| CH1 | 0 vs 0 → Δ **0** | + |
| CH2 | 0 vs −4 → Δ **4**（决策 C 保持源内相对偏移，K 把它修成 0；预期差异） | + |
| CH3 | 1014 vs 1019→1013 → median **5** / p90 8 / max 8 | −（与 K 一致翻转） |
| CH4 | 1292 vs 1302→1269 → median **8** / p90 14 / max 16 | −（与 K 一致翻转） |

结论：曲线层差 ≤16 采样（任务书容差 max≤50），极性完全一致；耗时外推 80 min/2 targets
≈ 3 min，远低于 20 min 预算。

## 效率与并行（用户 2026-10-29 明确要求）

**实测瓶颈与修复**（60 s 素材基准）：
| 操作 | 耗时 | 结论 |
|---|---|---|
| `apply_shift_plan`（内置施加） | 0.012 s / 60 s | O(n) 很快，不是瓶颈 |
| `verify_applied_shift`（内置独立复测） | **16.5 s / 60 s**（nearest-centre） | 瓶颈：`_independent_join` 对 nearest-centre 是 O(n×n_windows) 的朴素 Python 扫描；外推 80 min/4808 窗 ≈ 22 min/轨 |
| 同上，latest-start | **~0.1 s / 60 s** | 量化整除分支，向量化 |

**修复（不降级、不造轮子）**：plan 的 `window_samples == hop_samples`（稠密 1 s 阶梯）时
nearest-centre 与 latest-start **数学上逐位等价**（闭式 floor((pos−win/2+hop/2)/hop) =
floor(pos/hop)），因此生产 profile 用 `latest-start` 保留内置**逐样本独立复测**能力。
同理 `linear` 是内置定义的 offline-only 策略、`apply_shift_plan` 显式拒绝，而我们的
1 s 曲线无缺口 → 用 `hold`，输出与 linear 逐位相同。
**并行计划 (S5)**：测量按 target 并行（每个 target 的连续性跟踪内部串行，但其 12 kHz
缓存可复用、残差块测量可 `ProcessPool`）；施加/复测已接近 IO 带宽，先做 target 级并行。

## S3 — 默认路径集成（进行中）

已实现（`core/channel_sync.py`）：
- 参与集合按**独立 PCM 声道**判定（3–8 自动；混合 PCM+压缩 → `mixed_audio_skip`，
  进程不退出；多声道布局暂 `not_eligible`，S3 结构步骤随后补）；
- 默认锚点 CH1→CH8；`stereo_bind_enabled` 内部键（默认 True）；
- stereo 绑定检测 `_detect_stereo_groups()`：`sample_pair_snr` 抽帧强度 → **候选门
  `bind_candidate_snr=1.75`**（真实双轨 1.80/1.85、跨组 1.02–1.41；1.3 建组门在 C1305 上
  会单链接把四轨并成一组，导致真实双轨永远绑不上）→ 候选对再做 60 s/10 s 连续性跟踪刚性
  确认（|CC|≥0.90、lag span≤5、同号≥95%）→ 不重叠贪心接受；
- 恒定路径与绑定组整合（整组同偏移；与锚点同组的成员跟随锚点、保持源内相对偏移）；
- **整文件时变后备**：任一健康 target `non_constant`/`recheck_residual` → 丢弃整数修正 →
  `core.long_take.align_time_varying` → 成功则输出时变音频并写 `long_take` 报告块；
  失败 → `time_varying_failed` + 回退无对齐原始音频（透明模式 = 源文件拷贝）；
- 报告新增可选 `stereo_groups` / `long_take` 块（旧路径字段集合不变）。

**合成集成验证**（`work/long_take/s3/integration_check.py`，4×mono PCM mov，无 GPAC）：
全部通过：
| 用例 | 结果 |
|---|---|
| T1 常量（CH3/CH4 −500/−700 施加，绑定关闭） | `applied`，移位 −500/−700，无 `long_take` 块 |
| T2 漂移（CH3 0.3、CH4 0.5 采样/s，600 s） | 触发 `long_take.path=time_varying`（trigger CH4 non_constant）；输出实测施加曲线误差 CH3 **2.14** / CH4 **4.07** 采样 |
| T3 双内容对绑定 | groups=[[CH1,CH2],[CH3,CH4]]；仅 CH1/CH2 绑定（|CC| 1.000、span 0、同号 100%），CH3/CH4 因 span 80 拒绝 |
| T4 混合 PCM+AAC | `mixed_audio_skip`（`mixed_pcm_compressed_not_aligned: stream 0 aac`），全部 untouched |

**1kt.py 薄改动**（按任务书 §6 "1kt 只做薄分派"）：
- 默认对齐：`channel_sync_enabled = True`（不再依赖 `--channel-sync`；A 决策），
  参与集合判定全在 `run_channel_sync` 里完成；
- `--audio-plan` 存在时自动逻辑**让位**（不再 FATAL 拒绝两入口同时出现，改为显式
  INFO 并让 plan 决定）；
- 透明入口 + `--audio-plan`：S0 发现会被静默忽略 → 改为 **FATAL 明确拒绝**；
- `_transparent_sync_outcome`: `mixed_audio_skip` 计入 `skipped`（不再误计 failed）。

---

## S6 — 真实素材验收（进行中）

**真实 15 min 片段预验收**（`work/long_take/s6/real_clip_check.py`，生产入口
`run_channel_sync` 非透明模式）：

| 片段 | 结果 |
|---|---|
| C1305 30:00–45:00 | 绑定 **CH1/CH2**（|CC| 0.986、span 0、同号 100%）；CH3/CH4 拒绝（span 61）；TV 触发（CH4 non_constant）；rounds=2；报告 `long_take` 块完整 |
| C1305 00:00–15:00 | 追踪器首窗与 K 研究轨迹**逐窗一致**（CH3 1014/1024/1016…、CH4 1734/1732/1725…）；TV 触发；rounds=2 |

**两个真实数据缺陷（已定位并修复）**：
1. **残差测量选错分支（关键 bug）**：`gcc_phat_int` 的极性翻转启发式要求负峰 >1.2×
   正峰才采信；实测 C1305 CH3/CH4 反相内容上真（负）峰只强 ~1.1×，于是 round-1 已对齐
   的音频被残差测量读成 +123 采样，round-2 把 **+123 的错误**写进成品（实测输出 CH3
   施加 1141 vs 正确 ~1016）。修复：`core/long_take._abs_peak_delay()` 用**极性无关
   |CC| 峰**（盒式低通+抽取粗扫 + 全速率 ±8 精化），与 S1 跟踪器同口径。修复后真实
   round-1 残差 = **negligible (0)**，合成注入 7 采样仍精确恢复 7。
2. **中段起始片段的连续性无历史**：从 30:00 开始的片段没有 t=0 前的跟踪历史，首窗可能选
   到另一相关峰分支（CH4 1446 而非 1306）→ 片段验收不可作为分支正确性判据；**验收必须在
   整文件上执行**（与用户命令一致，K oracle 也是从 t=0 连续跟踪）。

**待执行（下一大块）**：整文件 C1305/C1306 用用户原命令跑通 → 曲线层（从输出反推每轨
施加曲线 vs K `_restored.mp4` 反推曲线：median≤1/p90≤5/max≤50；CH2 允许已知 4 采样组内
偏移差）→ 音频层（5 min 块相对滞后 median≤2/p90≤10/≥90%≤5；极性一致）→ 全量 unit →
A1 差异分类 → 文档/known-limitations。

**已知与 K 的预期差异**：CH2 差 4 采样（决策 C 保持组内相对偏移；K 把 −4 修成 0）。

### S6 继续（2026-10-30）：容器层暴露的既有性能限制

整文件用**用户原命令**（透明入口）跑到重封装阶段后暴露一个**既有**容器层限制（非本任务
音频改动引入）：

- GPAC 26.02 对 50 GiB 文件每做一次编辑（`-ref` / `-set-meta` / metadata pass …）都是
  **整文件重写**。实测 `-ref 6:cdsc:1` 一步在 F: 上跑了 **14 h 仍未完成**（进程累计读
  167 GB、写 41.75 GB，stage 文件 mtime 仍在更新）→ 生产 `restore_after_transparent_remux`
  在这类 80 min 长镜头上的容器恢复不可用。
- 这不是新问题：K 研究交付时就用 `work/fast_restore.py` **一次顺序拷贝**绕过 GPAC 的
  多轮整文件重写（`docs/audio-sync-p4/long-take-alignment-final.md` §16 记录 "`-ref` 一步
  17 min 仅 <1 GiB I/O"）。任务书 §2.2 明确"容器/元数据还原不在本任务范围"，因此本轮
  验收走**音频域**：用同一份 `run_channel_sync`（自动开启 + 恒定优先 + 时变后备 +
  stereo 绑定 + 极性）以 `transparent=False` 跑整文件，产物是每轨对齐后的 PCM 文件
  （`work/long_take/accept/full/<token>/work/audio_*.mov`），再与 K 的 `_restored.mp4`
  解码音频比对；视频/容器一致性由既有透明链路单测覆盖（本次不重复）。
- 另：**K 外部盘在 2026-10-30 断开**（`K:\1kt_work` 不存在），K oracle 暂时不可读。
  本侧曲线/极性指标已落盘（`accept/full/<token>/<token>_ours_metrics.json`），待 K
  接回后用 `accept_compare.py` 完成对比。

### S6 整文件音频域结果（2026-10-30，`full_align_only.py`，同一份生产 `run_channel_sync`）

| 文件 | 状态/耗时（最终代码） | 绑定 | 时变后备 | 极性 | 本侧曲线（30 s 窗中位 / p10–p90） |
|---|---|---|---|---|---|
| C1305 | applied / 410.8 s（long-take 169.8 s） | **CH1+CH2**（\|CC\| 0.982、span 3、同号 100%） | path=time_varying, rounds=2（CH3 残差 4、CH4 14） | CH3/CH4 翻转（负票 83%/86%） | CH1 0；CH2 0（组内保持）；CH3 1019 / 940–1094；CH4 1283 / 1102–1625 |
| C1306 | applied / 280.1 s（long-take 146.3 s） | **CH1+CH2**（\|CC\| 0.982、span 1、同号 100%） | path=time_varying, rounds=2（CH3 残差 4、CH4 4） | CH3/CH4 翻转（负票 100%/…） | CH1 0；**CH2 0**（组内保持）；CH3 1113 / 992–1126；CH4 1243 / 1209–1256 |

**C1305 部分 K 对比**（K 盘断开前抓的 `_restored.mp4` 反推快照，1815–2145 s，34 窗）：
- CH2：ours 0 vs K −4 → Δ4（决策 C 预期差异）；
- CH3：1815–1975 段 Δ ≤4（优秀），但 **2000–2145 段 ours 1013→1134 vs K 1013 → Δ 最大 120**：
  这是跟踪器在 ~34.5 min 处跟随了另一相关峰分支（K 的 round-3 fine pass 把它拉回常量曲线，
  本方案按用户决策 F 只做 2 轮残差，未复刻 K 的第 3 轮）；属**已知分支歧义**，待 K 接回后
  用完整成品判定是否需要补第 3 轮/分支选择规则。
- CH4：Δ 中位 2 / p90 26 / max 57（接近任务书 max≤50 边界）。

**C1306 绑定漏检与修复**：整文件抽帧强度实测（200 帧/对）：
C1306 CH1↔CH2 中位 **1.682**（原候选门 1.75 漏掉）；C1305 CH1↔CH2 2.094、CH3↔CH4 1.435/1.578，
跨组 ≤1.14。→ `bind_candidate_snr` 从 1.75 调到 **1.5**（跨组仍有 0.36 裕量；CH3↔CH4 会成为
候选但被四重刚性门拒绝：C1306 实测 |CC| 0.824、span 92）。C1306 用新门槛重跑后
**CH1+CH2 绑定成功、CH2 跟随锚点（源内 −4 保持）**；代价是候选确认多一次 80 min 跟踪
（绑定阶段约 3.5 min，超出 §5.3 的 1 min 预算 —— 列入 S5 并行收尾：候选确认并行 +
12k 缓存复用）。

**注意（测试脚本坑，已修）**：同一 token 重跑时 `full_align_only` 曾因 raw 缓存未刷新
读到上一轮 CH2 指标（−4）；现已改为强制重解码（`force=True`）。

### K 接回后的整文件真相（2026-10-30 16:20）

1. **我们的整文件追踪器与 K 研究轨迹逐窗一致**（`tracker_vs_k_check.py`）：C1305
   CH3 matched 471 窗，median/p90/max |Δ| = **0/0/0**；CH4 matched 474 窗，med 0、max 3。
   → 后续与 K 成品的差异**不在追踪器**，而在残差轮。
2. **整文件曲线层 vs K 成品（478 窗 × 30 s）**：CH2 Δ4（预期）；**CH3 median 12 / p90 117 /
   max 178；CH4 median 27 / p90 126 / max 208**——大量 5 min 段中位差 85~181。
   音频层（5 min 块相对滞后）：CH3 ours p90_abs 89 vs K 25.6，CH4 ours 129.6 vs K 52.8。
3. **根因（已定位）**：round-2 残差原实现 = **5 min 块 / ±256 搜索**，在音乐基底上选到
   ±200 采样的伪峰（C1305 实测块残差 −236/−220/−208/−204/−200/−192/−100，CC 仅
   0.26–0.51），把正确曲线拉坏。K 的 round-3 fine pass 用 **60 s 窗 / 20 s 跳、±80 搜索、
   |CC|≥0.20、5 min 加权中位平滑、离局部中位 >30 采样丢弃、|r|≤100**。
4. **修复**：`measure_residual_curve` 改为上述研究口径（profile 键
   `residual_window_seconds/hop_seconds/smooth_span_seconds/outlier_samples/max_abs_samples`），
   合成检查（注入 7 采样精确恢复）通过。**这是对任务书 §4.5「5 min 块」的修订**：
   与 K 验收口径对齐优于字面几何；已用真实 80 min 素材验证（重跑中）。
5. 任务书 §4.5 的 5 min 块几何保留为"残差平滑跨度"（5 min 加权中位），不是测量窗。
6. **第二个根因：建锚点没有过滤低 |CC| 窗**。C1305 62–80 min 安静段落里，追踪器
   （和 K 轨迹**一致地**）抖动：cc 0.004–0.09、lag 700–1560 乱跳；K 的
   `apply_tv.build_curve` 过滤 `accepted and cc >= 0.08` 后跨安静段线性插值，曲线保持
   平滑；我们不设门时 2 min 加权中位锚点被垃圾窗带偏 —— 实测 65–70 min 我们的
   round-1 曲线 1110–1265 vs K pass1 920–990（段中位差 260 采样）。修复：profile
   新增 `curve_min_cc: 0.08`，`build_curve_from_windows` 建锚点前过滤低 |CC| 窗
   （任务书 §4.3 第 6 点已注明）。
7. **验收方法修正（重要）**：task book 原写的"曲线层"用**从输出音频反推**的 shift；
   但 K 研究文档 §11.4 明确单窗测量在弱内容处偏差 45–205 采样（内容歧义），C1305
   的 62–80 min 就是这种段落——反推测量会把 K 成品误读成 1014（而 K 实际 pass1+fine
   是 1135）。因此真实曲线层对比改成 **applied grid 精确对比**：
   ours = round1(anchors)+round2(residual grid) vs K final = `applytv` grid +
   `refine_curve` grid（`work/long_take/s6/curve_exact_compare.py`）。音频层对比仅用
   **直接 ours-vs-K** 同窗互相关（`audio_direct_compare.py`）。

8. **round-1 已与 K pass1 逐位一致**：过滤+锚点时间回到研究口径后，我们的 round1
   grid vs K `applytv` grid：**median/p90/max = 0/0/0（4808 点）**。剩余差异全部来自
   round-2 残差（K round-3 fine 的实现细节不同：K 的离群/平滑是"局部中位±2 窗口 +
   5 min 滑动中位@60 s hop"，我们已按此重写；但同样的算法在我们 pass1 音频上复算
   K refine 仍差 median 4.8–7 / p90 12–35，说明 K 的 refine JSON 产生时用的 pass1
   音频与我们的有差异，或 K refine 脚本版本不同）。

9. **K 两文件 oracle 不一致（阻塞验收判定，需用户决策）**：
   - **C1306 restored**（2026-10-25 03:31）与 K 研究 fine 曲线一致：measured-vs-pass1
     median 2.7 / p90 18–43（CH3）、median 2.7 / p90 18（CH4）；
   - **C1305 restored**（2026-10-29 14:58，晚于 C1306 4 天）却与**全局线性模型**
     一致：`solve/drift` JSON 的 a=1015.09、b=−0.012 ppm → measured-vs-drift
     median 3.1 / p90 18.9 / max 37.9（CH3），而 vs pass1/fine 是 median 5.7–10 /
     p90 79–81。→ C1305 的 restored 成品音频很可能是**旧的全局解 aligned 音频**
     （重新 restore 时用错了中间产物），不是 K 研究文档 §12/§16 说的 fine 音频。
   - 因此 C1305 的"曲线层 vs restored"必然失败（我们的时变曲线跟随 tracker 跳变，
     与全局解在 34.5/65 min 段差 ~130 采样）；C1306 则接近容差。

**当前数字（各取对应口径）**：

| 文件 | 曲线层（vs K fine JSON，4808/4165 点） | 音频层（ours vs K 音频，5 min 块） |
|---|---|---|
| C1305 | CH3 med 5.9 / p90 45 / max 62；CH4 med 11 / p90 45 / max 88 | CH3 med 5 / p90 28 / max 147；CH4 med 8 / p90 116 / max 244 |
| C1306 | CH3 med 1.8 / p90 15.2 / max 54；CH4 med 2.0 / p90 15.6 / max 27 | CH3 med 3 / p90 9 / max 32；CH4 med 3 / p90 6.5 / max 48 |

注：K 自己的 measured−applied 一致性（研究文档 §12.3）是 C1305 CH3 med 6 / p90 20、
CH4 med 23 / p90 63；C1306 CH3 med 4 / p90 17、CH4 med 5 / p90 13.5 —— 我们与 K 的差
多数落在 K 自身测量噪声内，但**严格照任务书容差**（曲线 med≤1/p90≤5/max≤50；音频
med≤2/p90≤10/≥90%≤5）C1306 也还差一点（音频 med 3），C1305 因 oracle 问题无法判定。

### S5 并行收尾（2026-10-30 17:30）

- **round-2 残差窗并行**：残差窗互相独立（只读 pass1 raw + 锚点），
  `ThreadPoolExecutor(4)` 按 starts 顺序归并 —— 结果与串行逐位一致。
  80 min ≈239 窗从 ~50 s/target 降到 ~15 s/target。
- **stereo 绑定候选对并行**：候选对的高精度 80 min 跟踪互相独立，先并行
  测量（线程池，最多 4），再按 SNR 排名**顺序**做决策（保持"先到先得"的
  确定性语义）。C1305/C1306 各有 2 个候选（真对 + CH3↔CH4），绑定墙钟
  从 ~2×T 降到 ~T。
- **连续性跟踪器本身仍是顺序的**（每窗搜索半径依赖前一有效 lag，这是连续性
  的定义）；`measure_window_delays(workers=)` 仅为调用面稳定保留。
- `curve_min_cc` 过滤口径修正：用 **`coarse_cc`（粗相关峰 |CC|）** 而不是
  `confidence`（= min(coarse,1)×refine 的保守乘积）。真实 C1305 安静段两者都低
  （正确丢弃），但强重采样/线性伸缩的合成素材（integration T2 CH4：coarse 0.14、
  refine 0.15 → confidence 0.02）会被 confidence 门误杀 → 整条曲线
  `no_valid_windows`。改用 coarse_cc 后 synthetic T2 恢复。
- integration T1–T4 全 PASS（108 s）；T2 的极端线性漂移（CH4 0.5 采样/s = 173 ppm，
  真实素材 ≤0.04 ppm）按研究文档 §12.2 的方法边界放行（err3≤6、err4≤10）。

### S3/A6 多声道 PCM 布局结构支持（2026-10-30 17:55 完成）

**实现**（`core/channel_sync.py`，全部复用内置 API）：
- `alignment_scope()` 本就把 multichannel 流展开成逐声道 `ChannelRef`；`run_channel_sync`
  现在把引擎的每个 "track" 视为**一个独立声道**（单声道多流时 channel index == stream
  index，行为与旧实现逐位相同）。
- 解码：单声道流走既有 `_decode_stream`；多声道流走 `_decode_multichannel_streams()` —
  `AudioSourceBuilder` + `AudioPCMReader`（identity channelmap，逐声道跨步读出写
  `decoded_{channel}.f32`），**绝不下混**。
- 输出：按源 stream 汇聚 — 单声道流 fixed 回编码 / untouched 整流 stream copy（旧行为）；
  多声道流**任一声道被修正**时，用 `audio_wav.write_wav` 把该流全部声道（fixed +
  未修原样）按源顺序交织成 WAV，再按源 codec/sample entry 编码回**一条**流；
  没有声道被修正 → 整流 stream copy（逐位保持）。`audio_files` 仍是一条源流一个文件，
  透明重封装接口无需改动。报告行新增 `source_stream` / `source_channel`
  （多声道时另有 `source_stream_channels` / `source_layout`）。
- `two_channel_only` 策略只对单声道多流布局生效（避免 5.1 的 2 条健康声道被误判）。

**验证**（`work/long_take/s3/multichannel_check.py`）：
- T5：单流 4ch pcm_s24be（4.0 layout），注入 0/+4/−500/−700 → `applied`，
  4 行决策分别 anchor/already_aligned/fixed(−500)/fixed(−700)，**1 条 4ch 输出流**；
  输出与源 codec/channels/layout/duration_ts 一致；逐声道实测施加量 {0,0,−500,−700}。
- T6：单条 stereo 流（2 声道）→ `not_eligible`（`channel_count_out_of_range: 2`）。
- 单声道路径回归：`integration_check.py` T1–T4 全 PASS（S3 改动后仍通过）。
- **透明模式端到端**（`multichannel_transparent_check.py`，真实 1kt 命令）：
  4 s MP4（1 视频 + 1 条 4ch ipcm 流，注入 0/+4/−500/−700）→
  `1kt.py --input … --output … --channel-sync-transparent` **rc=0**；
  输出仍为 1 视频 + 1 条 4ch 流，codec/duration_ts 保持，视频基本流 SHA256
  逐位一致；逐声道实测施加量 {0, 0, −500, −700}（音频真的被重写，不是
  字节拷贝）。
- 注：fixture 不能用 ffmpeg `join` 滤镜（实测按输入默认单声道 layout 名字映射，
  4 个 mono join 成 4.0 的通道顺序会变成 [1,2,0,3]）；改用内置 `write_wav` 按位置
  交织再转 MOV。

**性能/内存实测（最终代码，C1305 80 min 4×mono）**：
- long-take 附加 **169.8 s** / 整文件音频域 **410.8 s**（C1306：146.3 s / 280.1 s）。
- 峰值 RSS：主进程 **743 MB**、整棵进程树（ffmpeg + 12 个帧级估计子进程）
  **1473 MB**（`work/long_take/s6/rss_probe.py`，0.5 s 采样）——预算 ≤3 GB ✓。

**修复（本轮实测发现）**：`_decode_multichannel_streams` 起初按声道并行调
`AudioPCMReader.read()` —— reader 对每个 stream 缓存**单个文件句柄**并 seek，
多线程共享会互相踩读位置（实测 4ch 单流时 CH3/CH4 读出全零 → silent_track；
多声道透明 E2E 因此暴露）。改为**按 stream 分块并行、块内顺序**（不同 stream
句柄独立），T5/E2E 恢复。

**已知边界**：>8 声道仍按任务书只允许 `--audio-plan alignment=enabled` 显式开启；
非 PCM/混合布局的拒绝语义不变。

---

## 问题与决策记录

| 日期 | 问题 | 处理 |
|---|---|---|
| 2026-10-29 | 多声道 PCM 布局（5.1/7.1/单条 stereo）需要拆声道；现有 `_decode_stream` 会下混 | S0 预测试证明计划层可无损还原；实施改为：解码用内置 `AudioPCMReader`（逐声道 read），输出在 `run_channel_sync` 第 7 步按源流重新交织。**先做长程对齐，再补布局泛化** |
| 2026-10-29 | 探针用 mka `-c:a copy` 取流导致 K 成品被误测为未对齐 | **探针/验收一律直接解码 f32**（见 S1-B 结论 2）；用户已指出"用原始 PCM 流做 intermediate" |
| 2026-10-29 | 用户强调"永远优先使用内置 API" | 多声道解码用 `AudioPCMReader`；输出交织用 `audio_wav.write_wav` + 既有 ffmpeg 编码；时变施加/复测用 `audio_sync_tv_exec`；不复制研究脚本 |
| 2026-10-29 | `applytv2` 与最终交付不一致 | oracle 改为从 `_restored.mp4` 反推；S6 一致性检查已完成（不一致 → 成品为准） |
| 2026-10-29 | 真实片段 round-2 把 +123 采样错误写进成品 | 根因：`gcc_phat_int` 极性翻转启发式（需负峰>1.2×正峰）在反相 CH3/CH4 上选到伪正峰。修复：新增 `_abs_peak_delay`（|CC| 峰 + 盒式低通抽取 + 全速率精化），round-1 残差恢复 ~0，合成注入 7 仍恢复 7 |
| 2026-10-29 | 从文件中间开始的片段追踪器可能选错相关峰分支 | 连续性跟踪需要从文件头开始；片段只用于机制验证，**整文件验收**才是分支/极性正确性判据（与 K oracle 同条件） |
| 2026-10-29 | 内置 `verify_applied_shift` 在 nearest-centre 下 O(n×窗数) | 60 s 素材 verify 16.5 s、外推 80 min ≈22 min/轨。window==hop 时 latest-start 与 nearest-centre 逐位等价 → profile 改 latest-start（~0.1 s/60 s），保留内置逐样本独立复测；`linear` offline-only → 稠密无缺口用 `hold`（输出逐位相同） |
| 2026-10-30 | **整文件透明重封装失败：MP4Box 报 `No space left on device` 并回退源拷贝**（输出 53.5 GB = 源字节拷贝，status 仍 `applied`） | 根因：**GPAC 26.02 把 `-new/-add` 导入暂存写到系统 `%TEMP%`（C:）**；50 GiB 需要 C: 数十 GiB，C: 只有 ~48 GiB → ENOSPC。实测 mp4box 进程打开 `C:\Users\...\AppData\Local\Temp\txj0.0`。修复（用户明确要求"temp 不应该在 C"）：`preservation/gpac.py` 新增 `_gpac_env()`，把 TMP/TEMP/TMPDIR 重定向到 MP4Box cwd 下的 `.gpactmp`（媒体/工作卷），每次 `_run` 结束清理；Python `TemporaryDirectory`（meta 检查）也改为 `dir=dst.parent`。L1 测试新增 2 条断言（`core.l1_gpac_paths`）。小文件 mux 实测 temp=`.gpactmp\try4.0`（F:）✓ |
| 2026-10-30 | 上面失败后 status 仍报 `applied`（`file_copied=True`）容易误读为成功 | 已知语义：透明模式 remux 失败 = 源文件拷贝 + `file_copied=True`，日志 `[SYNC-OK] ... byte copy` 可区分。验收判据必须检查输出音频，不能只看 status（S6 对比脚本就是这么做的）。是否把该终态改为 `verify_failed` 属产品语义，待与用户确认 |
| 2026-10-30 | **全量回归暴露：stereo 绑定组 + 组代表 fixed 时 `UnboundLocalError: ref_of`** | 组处理（决策 C）在 4a 初始化 `ref_of` 之前就写它；HEAD 无此路径（绑定是我们新增），因此是本任务引入的 bug。修复：把 `ref_of` 初始化前移到组处理之前。已由 `channel-sync P1 E2E` 用例覆盖 |
| 2026-10-30 | `fixed_files` 在单声道多流布局下为空（报告回归） | S3 重构 step7 时只有多声道流分支把 `streams_with_fixes.append(si)`；单声道 fixed 流漏了。修复后 C15 报告 `fixed_files==2` 恢复 |
| 2026-10-30 | 透明重封装备份校验对 16-bit `ipcm` 误杀（C17 回退源拷贝） | `preservation.sample_entry.audio_entry_consistency` 对 16-bit ipcm 把 `pcmC` 四字符码当"包长"读（源自身就被判不自洽）。修复：只有当**源自身条目自洽**时才把"补丁后不自洽"升级为失败；源条目不被解析器支持时记 WARNING 继续（入口用源覆盖、等长，不改变可播性）。C17 恢复 applied 且透明输出音频确实被移动 |
| 2026-10-30 | 全量 L3 既有 channel-sync 用例按旧默认写成 | 默认锚点从 `[2,3,0,1]`（CH3 优先）改为 CH1→CH8（决策 A4）、非恒定自动走时变（决策 B）。处理：机制类用例（C21/C22/C24/C26/C27–C32）显式钉住旧锚点顺序/新语义；C15/C17 期望更新为新锚点（CH1 静音→CH2）；C16/C26 期望改为"时变失败→整文件无对齐原始音频"；C32 在 **HEAD 基线同样失败**（稀疏语音 54–65% 覆盖率过不了 95% 证据门），重新标定为"证据不足时明确失败"；C23 输出扩展名跟随源（`.mov`）；p4c T9 改用单文件输入目录（避免 quad 的计划不适用混入批次 rc）；`production_output.py` 补 `import shutil` |

### 回归状态（2026-10-30 19:25，最终代码完整重跑）

| 套件 | 结果 |
|---|---|
| unit | **1011 PASS / 2 FAIL**（两条均为环境项：缺真实 Sony 语料） |
| 全量 L3（final） | **1251 PASS / 12 FAIL**（上轮 1226/26）。12 条全部是**环境缺素材**：`testsets/` 下 Sony/DJI/A7M5/classic 真实媒体不存在（含 2 条 unit 的分类器语料项、1 条 DJI Action 机型识别工具基线项）；**没有任何代码/算法类 FAIL** |
| L3 hardware channel-sync 子集（C15–C32） | 28 项 1 "FAIL" → 已重新标定为预期失败（C32 在 HEAD 基线同样失败） |
| L3 production_output 子集（26 项） | **0 FAIL** |
| 多声道布局 A6 专项（`work/long_take/s3/multichannel_check.py`） | T5/T6 全 PASS |
| 合成集成 T1–T4（`work/long_take/s3/integration_check.py`） | 全 PASS（S3 重构后复跑） |

### 用户决策（2026-10-30 晚，new）

1. **重新导出正确 C1305 restored**（K restored 音频用错中间产物）。处理见下节
   "C1305 oracle 重建"。
2. **保证两条长视频完整通过**（目标）。
3. **`--audio-plan` 权限最高**：透明入口与计划并存时不再 FATAL，计划接管
   （`1kt.py` 已改：INFO + 继续走计划路径；L3 p4c T9 新增断言）。
4. **remux 失败 → 源文件拷贝**：保持现有回退语义（不改 status 语义）。
5. **>8CH 保持现状**（仅 `--audio-plan alignment=enabled` 显式开启）。
6. **CH1/CH2 是一个 stereo 组，不能动这两条轨之间的关系**：即决策 C 不变
   （组内 −4 保持；对应 K 的 0 的 4 采样差是预期）。
7. **透明重封装暂时置空**：不移植 fast_restore，不阻塞音频域验收；容器产物
   另行处理。

### C1305 oracle 重建（重新导出正确 fine 音频）

K 盘上 `aligned_fine` wav / `drift_rebuild.json` 均不可用（K/I 的
`1kt_moved_20261026\data\_from_*` 是空壳）。用**可用的 K JSON** 重建研究 fine
音频（`work/long_take/s6/build_fine_oracle.py`）：
- `final_shift(t) = applytv_<stem>.json(grid) + refine_curve_<stem>.json(grid)`
  （CH1=0 常量、CH2=−4 常量，与 K `apply_tv.py` 的 CH2 处理一致）；
- 用**内置** `run_time_varying`（`core.long_take.apply_curve`，hold/latest-start
  1 s 网格）施加到源码流 → `accept/oracle/<token>/fine_{0..3}.f32`；
- 验证方法在 C1306 上有效（其 restored 与 fine 一致）：重建 fine vs K restored
  直接互相关 **CH1/CH2 = 0/0、CH3 med 3.5/p90 28.5、CH4 med 4.5/p90 18.5**
  （与 K 自身测量噪声同量级）；
- C1305 重建后 vs K restored：CH1/CH2=0/0、CH3 med 9/p90 46、CH4 med 10/p90 104
  → 进一步确认 K C1305 restored 与 research fine 不是同一版音频。

### round-2 残差改为**研究 fine pass 同源实现**（2026-10-30 晚）

之前的实现是"盒式抽取 + ±80 搜索 + 自定义加权中位平滑"；本轮改为
`fine_delay.py` 同算法：`resample_poly` 12 kHz 粗扫 + ±8 全速率精化 +
|CC|≥0.20 + 局部中位离群 + **5 min 滑动中位（1 min 跳）** + |r|≤100。
合成注入 7 采样仍精确恢复，integration T1–T4 全 PASS。
目的：让 round-2 与 K `refine_curve` 同源，缩小与 K 成品的差异
（重跑实测见下一节）。


### 最终结果（2026-10-30 23:30，含本轮三处改进）

**本轮改进**
1. round-2 残差改为 **K fine pass 同源实现**（12 kHz 
esample_poly 粗扫 +
   ±8 全速率精化 + |CC|≥0.20 + 局部中位离群 + 5 min 滑动中位 + |r|≤100）。
2. 残差改在**分数插值重建的 pass-1** 上测量（
ender_fractional_pass1，
   与 K interp_write 同条件）；生产施加仍用内置整数执行器。
3. 
esidual_min_median_samples 1.0 → **0.25**：K fine 即使残差中位 <1 也施加，
   C1306 CH4（中位 0.87）此前被丢弃，实测音频 p90 12.5 →（丢弃时）22.5。

**最终数字（两条长视频，音频域运行完成）**

| 文件 | 耗时（整文件 / long-take 附加） | 曲线 CH3 med/p90/max | 曲线 CH4 med/p90/max | 音频 CH3 med/p90/≤5 | 音频 CH4 med/p90/≤5 |
|---|---|---|---|---|---|
| C1305 | 478.6 s / 197.8 s | 4.80 / 12.14 / 46 | 7.00 / 35.22 / 47 | 5 / 9 / 52% | 6 / 34 / 42% |
| C1306 | 303.6 s / 164.9 s | **1.77 / 8.23 / 13** | 2.81 / 12.48 / 22 | 2 / 10 / 81% | 3 / 12.5 / 77% |

（曲线 vs applytv+refine_curve；音频 vs 重建的 research fine 音频；CH1/CH2
为 0/0 与已知 4。仍未达任务书字面容差，原因见 known-limitations §14.2。）

**C1305 正确 restored 重建：BLOCKED**
- applytv+refine 重建 vs K restored：CH3 9/46、CH4 10/104；
- drift+refine 重建 vs K restored：CH3 6/22、CH4 27/386；
- 用 K **块式插值**逐位重建 pass1 再跑 fine 算法，残差与分数 pass1 完全相同
  → 
efine_curve 的 pass1 既不是 applytv 也不是 drift（应是丢失的
  drift_rebuild.json）；
- 方法在 C1306 上验证有效（重建 fine vs restored：CH1/2 0/0、CH3 3.5/28.5、
  CH4 4.5/18.5）。
→ 需要用户提供 aligned_fine wav / drift_rebuild.json / K 侧重导成品之一。

**用户决策落实**
- --audio-plan 权限最高：1kt.py 透明入口与计划并存时 INFO + 计划接管
  （不再 FATAL）；L3 p4c T9 新增断言，production_output 子集 27 项 0 FAIL。
- remux 失败 → 源文件拷贝：保持现状（status 语义未改）。
- >8CH 保持现状；CH1/CH2 stereo 组关系保持（决策 C）；透明重封装置空。

### K deliverable 复核（2026-10-31 凌晨）— 修正此前结论

**结论：`K:\1kt_work\long_align\deliver` 下两个 restored 成品是正确的**
（结构 + 音频口径都是研究 round-3 final）。此前"C1305 restored 用错中间
产物"的结论**不成立**：那是拿 round-2 的 `applytv` 曲线去比，而成品用的是
round-3 的 **drift_rebuild** pass1。

证据链（归档研究脚本仍在 `tests/regression/long_align_tools/`）：
- `remux_final.py`：成品音频来自 `data/_scratch/aligned_fine/<stem>/ch{i}_fine.f32`；
- `fine_delay.py`：`fine = interp(pass1, k + refine_curve)`，其中
  `pass1 = interp(src, k + s0)`、`s0` 来自 `drift_rebuild.json` 锚点；
- `plot_drift2.py::anchors_from`：drift_rebuild 锚点 = 2 min 加权中位锚点 →
  **7 个锚点（14 min）中值滤波** → **每锚点限幅 30 采样（250 ppm）**；
  round-2 的 applytv 是 120 采样限幅、无中值滤波 —— 这就是两者在 tracker
  跳变段的差异来源；
- 用 K `track_*.json` 轨迹 + `refine_curve_*.json` 重建 drift_rebuild+refine，
  与 deliver 音频直接互相关：C1305 CH3 med 5.5 / CH4 6.5，C1306 CH3 1.5 /
  CH4 3.0 采样，|CC| ≥ 0.99（残差来自 drift_rebuild.json 缺失导致的锚点
  重建差，不是口径错误）；
- 容器：两文件均为 1×HEVC hvc1 + 4×mono pcm_s24be/ipcm + rtmd data，
  duration_ts 与源一致，major_brand 保持 XAVC，`remux_final.json` 记录父成品
  视频逐包 bit-exact、音频 md5 与 WAV 一致；restored 为 final 再恢复元数据。

**对实现的含义**：要对齐这两个交付物，round-1 曲线应采用 round-3 的
drift_rebuild 稳定化（中值滤波 7 + 限幅 30），而不是 round-2 的 applytv
（120 限幅）；round-2 残差继续用 K fine-pass 同源算法。验收 oracle 改用
deliver 里的 restored 音频（或重建的 drift_rebuild+refine）。
下一步：在 `build_curve_from_windows` 加稳定化级、重跑两条长视频并用
deliver 音频作 oracle 验收。

### 参考算法完整实现 + 最终验收 PASS（2026-10-31）

**实现（`core/long_take.py`）**
- `build_curve_from_windows`：2 min 加权中位锚点 → 7 锚点中值滤波 →
  30 采样/锚点限幅（参考 `plot_drift2.anchors_from`）；真实漂移超过限幅时
  按 1.5×稳健斜率自适应放大（合成线性漂移测试需要；真实素材 ≤2 ppm 不触发）。
- 新增 `reference_interp_write`：与参考 `fine_delay.interp_write` 逐行等价
  （10,000 采样曲线网格、5M 采样块、clamp 后块内 `np.interp`、越界补零、
  float32）。TV 路径 round-1 与 round-2 final 均改用它（`tv_apply_engine=
  "reference"`，内置 `apply_curve` 作 fallback）。
- round-2 残差直接在参考分数 pass1 上测量（不再需要单独重建 pass1），final 再
  对 pass1 做一次残差插值（参考 double-interp）。
- 报告新增 `curve_grid`（完整 1 s 施加曲线）、`curve_round1`、
  `apply_engine`（验收/审计用）。

**参考管线复算（独立 oracle，未修改归档研究脚本）**
- `plot_drift2.py` → `data/_scratch/drift_rebuild.json`（pass1 锚点；
  C1305 CH3 swing 26 / CH4 swing 506；C1306 CH3 131 / CH4 48）。
- `fine_delay.py` → `data/_scratch/aligned_fine/<stem>/aligned_4ch_fine.wav` +
  `fine_delay.json`（round-3 final 音频与残差）。
- 复算 final 音频 vs K 交付 restored：CH1/2/3/4 **0/0/0/0**（p90 0）→
  再次证明交付物是参考 round-3 final（此前"用错中间产物"结论已撤销）。
- 复算 final 音频 vs 我们的输出：CH1/3/4 **0/0**，CH2 **4/4**（用户
  stereo 组决策）。
- 曲线层：我们的总曲线 vs 参考总曲线：C1305/C1306 两通道
  **med 0 / p90 0 / max 0（4808/4165 点全 ≤5）**；round-1 vs 参考 pass1 有
  少量差异（C1305 CH3 2/4、CH4 6/30；C1306 1/1–3.6），但残差补偿后总曲线
  逐点一致。

**最终验收（`work/long_take/s6/final_accept_v2.py` → OVERALL: PASS）**

| 层 | 容差 | C1305 | C1306 |
|---|---|---|---|
| 曲线（vs 参考 final 曲线） | med≤1/p90≤5/max≤50 | **0/0/0** | **0/0/0** |
| 音频（vs K 交付音频） | med≤2/p90≤10/≥90%≤5 | CH1/3/4 0/0/0 100%；CH2 4/4/4（决策 C） | 同左 |

耗时：整文件音频域 C1305 689.9 s / C1306 389.1 s；long-take 附加
231.0 s / 193.8 s。unit 1019/2（环境项）；integration/多声道 E2E 全 PASS。
参考引擎复测峰值 RSS：主进程 **775 MB** / 进程树 **1470 MB**（预算 ≤3 GB）。
**确定性复跑**（同一代码、同一输入第二次完整运行两条长视频）：输出音频 raw
逐位相同（SHA256 一致）、施加曲线逐点相同、残差决策相同
（work/long_take/s6/determinism_check.py）。
产物：`work/long_take/accept/final_accept_v2.json`、`reference_verification.json`、
`deliver_audit.json`、`ref_pipeline_*.log`。

### 收尾（2026-10-31 凌晨）：全量回归 + 参考算法测试 + 发布草稿

- **新增 L1 测试** `l1_long_take_reference_algorithm`（8 条，纯 numpy）：
  7 锚点中值滤波剔除孤立跳变（与 round-2 口径对照 ≥100 采样差）、
  自适应限幅跟住 0.5 采样/s 真实漂移（固定 30 限幅会压出 >50 采样阶梯）、
  `reference_interp_write` 的整数/分数/负移位/越界补零语义。
- **最终全量回归**（含新套件）：**1260 PASS / 12 FAIL**（1306.1 s）；
  12 条 FAIL 全部为 `testsets/` 真实媒体缺失的环境项（Sony/DJI/A7M5/
  classic），**无代码/算法失败**。unit 单独跑：**1019 PASS / 2 FAIL**。
- **提交链**（本地 main，未 push）：
  `6103d78` 默认长镜头对齐 → `d972e32` 多声道线程安全修复 →
  `afde8f0` K fine-pass 残差 + 计划权限最高 →
  `a4799cb` 参考 round-3 算法完整实现 + 验收 PASS →
  `d3191cf` 参考算法 L1 测试。
- **发布草稿**：`docs/release_notes_long_take.md`（VERSION/push/release 待用户
  决定）。
- **生产端到端**：2026-10-31 晚补测 production_tv_e2e.py —— 真实
  1kt.py --channel-sync-transparent 在合成 4×mono（10.4 ppm 真实漂移）上
  applied/time_varying/reference 引擎，输出 4×mono 保持，CH2/CH4 残差 0/0、
  CH3 中位 3.5 采样（末段 hold 边界 max 44）。
- **机器休眠**：已注册 Windows 计划任务 `1kt_hibernate_0745`（07:45）、
  `1kt_hibernate_0752`、`1kt_hibernate_0758`（备份，`shutdown /h`，
  WakeToRun），并关闭 AC/DC 自动睡眠；按用户要求 08:00 前进入休眠。
