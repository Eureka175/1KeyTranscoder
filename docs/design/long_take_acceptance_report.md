# 长镜头默认对齐 — 验收报告（2026-10-30）

> 被验收对象：`docs/design/long_take_default_alignment.md`（用户批准的任务书）
> 实现提交：`6103d78`（本地 main，未 push；本次验收另含其后的多声道线程安全修复）
> 逐阶段实现记录：`docs/design/long_take_impl_checkpoint.md`
> 未决项/失败边界：`docs/audio-sync-p4/known-limitations.md` §14

## 1. 验收口径

任务书 §9 要求曲线层优先、音频层补充。实际执行时发现两个必须记录的口径问题：

1. **不能从 K 成品反推曲线**：K 研究文档 §11.4 自报单窗测量在弱内容处偏差
   45–205 采样（内容歧义，C1305 的 62–80 min 即此类段落）。因此曲线层改
   **applied grid 精确对比**：
   - ours = `round1(anchors)` + `round2(residual grid)`（来自生产报告）；
   - K final = `applytv_<stem>.json` grid + `refine_curve_<stem>.json` grid；
   - 脚本：`work/long_take/s6/curve_exact_compare.py`。
2. **音频层直接 ours-vs-K**：5 min 块 / 50 % 重叠，两两直接互相关
   （不经过"源 vs 输出"的二次反推）；脚本：
   `work/long_take/s6/audio_direct_compare.py`。
3. **未走用户原命令的容器产物**：透明入口在 50 GiB Sony 文件上受 GPAC
   "每步整文件重写"限制（`-ref` 实测 14 h 未完成）。音频域验收使用**同一份
   生产 `run_channel_sync`**（`transparent=False`）；容器/元数据不在本任务
   范围（known-limitations §14.4）。多声道的透明重封装另用 4 s 合成素材
   做了端到端验证（见 §5）。

语料：K 成品 `K:\1kt_work\long_align\deliver\*_4ch_aligned_restored.mp4`，
参考曲线 JSON `K:\1kt_work\long_align\{applytv,refine_curve,track,...}_*.json`。

## 2. 整文件结果（最终代码，C1305 80.1 min / C1306 69.4 min）

| 项 | C1305 | C1306 |
|---|---|---|
| 状态 / 耗时（音频域） | applied / **410.8 s** | applied / **280.1 s** |
| long-take 附加耗时 | **169.8 s** | **146.3 s** |
| stereo 绑定 | **CH1+CH2**（\|CC\| 0.982、span 3、同号 100%） | **CH1+CH2**（\|CC\| 0.982、span 1、同号 100%） |
| 时变后备 | time_varying, rounds=2（CH3/CH4 触发） | 同 |
| 极性 | CH3/CH4 自动翻转 | 同 |
| **round-1 vs K pass1** | **0 / 0 / 0（4808 点）** | **0 / 0 / 0（4165 点）** |

> round-1 曲线与 K research pass1 **逐位一致**；剩余差异全部来自 round-2
> 残差（K 的 round-3 fine pass 用同一算法族，但 pass1 音频由分数插值生成，
> 与本实现的整数 hold 施加存在亚采样差；在弱内容窗会翻转相关峰分支）。

### 2.1 曲线层（median / p90 / max |Δ|，采样）

| 通道 | 任务书容差 | C1305 | C1306 |
|---|---|---|---|
| CH3 | ≤1 / ≤5 / ≤50 | 5.88 / 45.1 / 62 | **1.81 / 15.2 / 54** |
| CH4 | ≤1 / ≤5 / ≤50 | 11.0 / 45.0 / 88 | **2.00 / 15.6 / 27** |

### 2.2 音频层（5 min 块，ours vs K 直接互相关）

| 通道 | 任务书容差 | C1305 med / p90 / ≤5 比例 | C1306 med / p90 / ≤5 比例 |
|---|---|---|---|
| CH1 | ≤2 / ≤10 / ≥90% | 0 / 0 / 100% | 0 / 0 / 100% |
| CH2 | （决策 C 已知差 4） | 4 / 4 / 100% | 4 / 4 / 100% |
| CH3 | ≤2 / ≤10 / ≥90% | 5 / 28 / 55% | 3 / 9 / 77% |
| CH4 | ≤2 / ≤10 / ≥90% | 8 / 116 / 35% | 3 / 6.5 / 77% |

## 3. 为什么没有"严格全绿"

1. **K 自身噪声地板**：K 研究文档 §12.3 自报 measured−applied 一致性为
   C1305 CH3 med 6 / p90 20、CH4 med 23 / p90 63；C1306 CH3 4 / 17、CH4 5 / 13.5。
   任务书的 ≤1/≤5 与音频 ≤2/≤10 低于该素材的可验证精度。
2. **C1305 的 K restored 成品音频疑似用错中间产物**：它跟随**旧的全局线性解**
   （`solve/drift` a=1015.09、b=−0.012 ppm；measured-vs-drift med 3.1 /
   p90 18.9），而不是研究 fine 曲线（measured-vs-pass1 p90 79–81）。
   C1306 restored 则与 fine 一致（med 2.7–4.8 / p90 18–43）。
   → C1305 的"vs restored 音频"没有意义，本报告对 C1305 采用 research fine
   曲线 JSON 作为 oracle（与 C1306 同源）。**待用户确认**（重导成品或认可该口径）。
3. 上述两点均写入 known-limitations §14，不把无法验证的差异记成"通过"。

## 4. 结构与安全语义验证（合成 + 真实）

| 项 | 结果 |
|---|---|
| 恒定优先 / 时变后备 / 失败回退无对齐 | integration T1–T4 全 PASS；真实文件行为一致 |
| stereo 绑定四重门（\|CC\|、span、同号、候选 SNR） | C1305/C1306 真对绑定、CH3↔CH4 正确拒绝；合成 T3 通过 |
| A6 多声道单流（4ch 4.0） | `multichannel_check.py` T5 全 PASS（1 条 4ch 输出、逐声道 {0,0,−500,−700}）；T6 stereo 拒绝 |
| **多声道透明重封装端到端** | `multichannel_transparent_check.py`：真实 `1kt.py --channel-sync-transparent` rc=0；1 视频+1 条 4ch 流保持；视频 SHA256 逐位一致；逐声道 {0,0,−500,−700} |
| 极性 | C1305/C1306 CH3/CH4 自动翻转（负票 83–100%），输出相对 CH1 正号 ≥98% |
| 失败安全 | 时变失败/测量失败/复检失败 → 整文件无对齐原始音频；轨道级不健康 → 该轨 untouched |
| 长镜头退出语义 | 进程不退出；透明模式文件级失败输出=源文件原样拷贝（日志 `byte copy` 可区分） |

## 5. 性能/内存（任务书 §5.3）

| 项 | 预算 | 实测 |
|---|---|---|
| long-take 附加 | ≤20 min/文件 | 169.8 s (C1305) / 146.3 s (C1306) |
| 整文件（不含容器） | — | 410.8 s / 280.1 s |
| 残差窗并行 | — | 80 min ≈239 窗：~50 s → ~15 s/target |
| 绑定候选并行 | — | 2 候选：2×T → ~T |
| 峰值 RSS（主进程 / 进程树） | ≤3 GB | **743 MB / 1473 MB**（C1305，0.5 s 采样） |

## 6. 回归

- **unit：1011 PASS / 2 FAIL**（均为缺真实 Sony 语料的环境项）。
- **全量 L3：1251 PASS / 12 FAIL**（上轮 1226/26）——12 条**全部为 testsets
  真实媒体缺失**的环境项；无代码/算法类失败。
- 旧默认语义用例已按任务书更新（锚点 CH1→CH8、非恒定→整文件时变、时变失败
  →无对齐；机制类 C21–C32 显式钉旧锚点口径）；C32 在 HEAD 基线同样失败，已
  重新标定；本轮另修 `ref_of` 未初始化、`fixed_files` 缺失、16-bit ipcm
  误判、AudioPCMReader 多线程共享句柄 4 个真实缺陷。

## 7. 复现命令

```powershell
# 整文件音频域对齐（两个文件）
python work\long_take\s6\run_full_align_all.py

# 曲线层 / 音频层 / 汇总
python work\long_take\s6\curve_exact_compare.py --token c1305
python work\long_take\s6\curve_exact_compare.py --token c1306
python work\long_take\s6\audio_direct_compare.py --token c1305
python work\long_take\s6\audio_direct_compare.py --token c1306
python work\long_take\s6\final_accept_report.py --tokens c1305,c1306

# 结构与多声道 E2E
python work\long_take\s3\integration_check.py
python work\long_take\s3\multichannel_check.py
python work\long_take\s3\multichannel_transparent_check.py

# 性能/内存
python work\long_take\s6\rss_probe.py
```

结果 JSON：`work/long_take/accept/final_accept_report.json`、
`work/long_take/accept/full/<token>/<token>_{report,curve_exact_compare,audio_direct_compare}.json`。

## 8. 结论

- **音频域实现与安全语义：通过**（结构、绑定、极性、失败回退、长镜头退出语义、
  多声道 A6 及透明重封装端到端、性能/内存均满足任务书）。
- **与 K 的严格数值容差：未通过**，原因已定位为 K 成品自身的内容歧义噪声地板
  与 C1305 oracle 数据缺陷（非本实现可消除）；**不作为静默通过**。
- 待用户决定：C1305 oracle 口径、容差是否按噪声地板修订、是否移植 K 的
  fast_restore 以打通 50 GiB 透明容器产物、以及 release/VERSION 决策。
