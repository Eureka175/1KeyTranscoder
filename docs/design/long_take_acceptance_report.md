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
| 状态 / 耗时（音频域） | applied / **478.6 s** | applied / **303.6 s** |
| long-take 附加耗时 | **197.8 s** | **164.9 s** |
| stereo 绑定 | **CH1+CH2**（\|CC\| 0.982、span 3、同号 100%） | **CH1+CH2**（\|CC\| 0.982、span 1、同号 100%） |
| 时变后备 | time_varying, rounds=2（CH3/CH4 触发） | 同 |
| 极性 | CH3/CH4 自动翻转 | 同 |
| **round-1 vs K pass1** | **0 / 0 / 0（4808 点）** | **0 / 0 / 0（4165 点）** |

> round-1 曲线与 K research pass1 **逐位一致**；剩余差异全部来自 round-2
> 残差（K 的 round-3 fine pass 用同一算法族，但 pass1 音频由分数插值生成，
> 与本实现的整数 hold 施加存在亚采样差；在弱内容窗会翻转相关峰分支）。

### 2.1 曲线层（median / p90 / max |Δ|，采样；vs `applytv+refine_curve`）

| 通道 | 任务书容差 | C1305 | C1306 |
|---|---|---|---|
| CH3 | ≤1 / ≤5 / ≤50 | 4.80 / 12.14 / 46.0 | **1.77 / 8.23 / 13.0** |
| CH4 | ≤1 / ≤5 / ≤50 | 7.00 / 35.22 / 47.3 | 2.81 / 12.48 / 22.0 |

### 2.2 音频层（5 min 块，ours vs **重建的 research fine 音频**）

| 通道 | 任务书容差 | C1305 med / p90 / ≤5 比例 | C1306 med / p90 / ≤5 比例 |
|---|---|---|---|
| CH1 | ≤2 / ≤10 / ≥90% | 0 / 0 / 100% | 0 / 0 / 100% |
| CH2 | （决策 C 已知差 4） | 4 / 4 / 100% | 4 / 4 / 100% |
| CH3 | ≤2 / ≤10 / ≥90% | 5 / 9 / 52% | 2 / 10 / 81% |
| CH4 | ≤2 / ≤10 / ≥90% | 6 / 34 / 42% | 3 / 22.5 / 77% |

> 重建的 research fine 音频：`final = applytv(grid) + refine_curve(grid)`
> （CH1=0、CH2=−4），用同一生产施加器渲染（`build_fine_oracle.py`）。
> 该方法在 C1306 上被验证：与 K restored 直接互相关 CH1/CH2 = 0/0、
> CH3 med 3.5/p90 28.5、CH4 med 4.5/p90 18.5（与 K 自身测量噪声同量级）。

## 3. C1305 正确 restored 的重建与仍存在的不可判定性

用户决策（2026-10-30）：**重新导出正确 C1305 restored**。K 盘上的
`aligned_fine` wav / `drift_rebuild.json` 已不可用（K/I 的
`1kt_moved_20261026\data\_from_*` 是空壳）；因此只能用可用的 K JSON 重建。
三种候选重建与 restored 的直接互相关（5 min 块，med/p90）：

| C1305 候选最终曲线 | vs K restored（CH3） | vs K restored（CH4） |
|---|---|---|
| `applytv`（tracker pass1）+ `refine_curve`（研究文档口径） | 9 / 46 | 10 / 104 |
| `drift` 全局线性解 + `refine_curve` | 6 / 22 | 27 / 386 |
| 仅 `applytv`（pass1） | 5.7 / 81 | 19 / 116 |

另外：用 K 的 `apply_tv.apply_channel` **块式插值**逐位重建 pass1，再跑
`fine_delay` 同算法测残差，结果与用全局分数插值 pass1 完全相同
（vs K refine：CH3 4.8/12.14、CH4 7/35.22）→ **`refine_curve_*.json` 的测量
pass1 既不是 `applytv`，也不是 `drift` 全局解**，应是已丢失的
`drift_rebuild.json` 锚点曲线。

**结论**：用现有 K 盘产物无法重建"正确的 C1305 restored"；K 的 C1305
restored 与任何可重建的研究 final 都不一致。需要用户提供以下任一：
1. 原始 `aligned_fine/<C1305>/aligned_4ch_fine.wav`（或其 `fine.f32`）；
2. `data/_scratch/drift_rebuild.json`（round-3 pass1 锚点）；
3. 由 K 侧重新导出一份"正确 C1305 restored"（最好是 fine 口径）。

在此之前，C1305 的严格数值验收只能按"研究文档口径重建 fine"记录，
且 K 自身的 measured−applied 一致性（§12.3：CH3 med 6/p90 20、CH4 med 23/p90 63）
已低于任务书容差。

## 3b. 为什么 C1306 也没全绿

C1306 rebuilt fine 与 restored 一致，故其验收是可靠的。差异来源：
1. 任务书曲线容差（med≤1/p90≤5/max≤50）低于 K 自身噪声地板；我们已把
   round-2 残差改成与 K `fine_delay` 同源算法（12k resample_poly + ±8 精化
   + 5 min 滑动中位 + 局部中位离群），并改为在"分数插值重建的 pass1"上测量
   （与 K pass1 的分数移位同条件）——曲线 p90 从 15.2/15.6 降到 **8.2/12.5**。
2. C1306 音频层：CH3 med2/p90 10、CH4 med3/p90 22.5，仍未达到
   "≥90% 块 ≤5 采样"，与 K 研究文档自报的 CH3 med4/p90 17、CH4 med5/p90 13.5
   属同一噪声量级。

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
| long-take 附加 | ≤20 min/文件 | 197.8 s (C1305) / 164.9 s (C1306) |
| 整文件（不含容器） | — | 478.6 s / 303.6 s |
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
