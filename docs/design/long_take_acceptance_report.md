# 长镜头默认对齐 — 验收报告（最终版 2026-10-31）

> 被验收对象：`docs/design/long_take_default_alignment.md`（用户批准的任务书）
> 实现提交：`6103d78`、`d972e32`、`afde8f0`（本地 main；参考算法完整实现在其后
> 的提交）
> 逐阶段实现记录：`docs/design/long_take_impl_checkpoint.md`
> 未决项/边界：`docs/audio-sync-p4/known-limitations.md` §14

## 0. 结论（先给结果）

**两条真实 80 分钟长视频（C1305 / C1306）全部通过任务书 §9 验收。**

| 层 | 容差（任务书 §9） | C1305 | C1306 |
|---|---|---|---|
| 曲线层（vs 参考 final 曲线） | med ≤1 / p90 ≤5 / max ≤50 采样 | **0 / 0 / 0**（4808 点） | **0 / 0 / 0**（4165 点） |
| 音频层（vs K 交付 restored 音频） | med ≤2 / p90 ≤10 / ≥90% 块 ≤5 | CH1/3/4 **0/0/0，100%**；CH2 **4/4/4**（见下） | 同左 |

CH2 的 4 采样差是**用户决策**：CH1/CH2 是一个 stereo 组，不能改动两条轨之间的
相对关系；我们保持 CH2 = 0（组内 −4 相对偏移保持），K 交付物把 CH2 整体移了
−4 采样（会改变组内关系），因此差异恒为 4 采样（0.083 ms），不影响组内对齐。

## 1. 参考算法（完整实现）

本轮起实现与归档研究工具（`tests/regression/long_align_tools/`）**逐算法等价**：

1. **跟踪**：60 s 窗 / 10 s 跳；12 kHz 粗扫（continuity ±(96+8·分钟) 采样）+
   48 kHz ±8 精化；`|CC| ≥ 0.06`（窗级）+ 锚点过滤 `cc ≥ 0.08`。
2. **round-3 pass1 = drift_rebuild**（`plot_drift2.py::anchors_from`）：
   2 min 分箱 |CC| 加权中位锚点 → **7 锚点（14 min）中值滤波** → **每锚点限幅
   30 采样（250 ppm）** → 锚点间线性插值（首尾 hold）。
   （round-2 的 `apply_tv` 口径为无中值滤波、120 限幅，仅保留在
   `curve_stabilize=False` 分支。）
3. **施加**：参考 `fine_delay.interp_write` 分数插值 —— 曲线在 **10,000 采样
   网格**采样、5,000,000 采样/块、`pos = k + s(k)` clamp 后块内线性插值、越界
   补零、float32 输出（`core.long_take.reference_interp_write`）。
4. **round-3 final**：在 pass1 上测 60 s 窗 / 20 s 跳残差（12 kHz ±80 + ±8
   精化、|CC|≥0.20），5 min 滑动中位（1 min 跳、离局部中位 >30 丢弃）+
   |r|≤100；再对 pass1 做一次分数插值（参考的 double-interp）。
5. 其余产品语义不变：恒定优先、stereo 绑定（四重刚性门）、组内相对偏移保持、
   极性组级翻转、|shift|≤4800 硬门、失败安全回退。

## 2. 验收方法（两个独立 oracle）

1. **重跑归档参考管线**（未修改研究脚本，真实源素材）：
   `plot_drift2.py` → `data/_scratch/drift_rebuild.json`（round-3 pass1 锚点）；
   `fine_delay.py` → `data/_scratch/aligned_fine/<stem>/aligned_4ch_fine.wav` +
   `fine_delay.json`（round-3 final 音频与残差）。
2. **K 交付成品**：`K:\1kt_work\long_align\deliver\*_4ch_aligned_restored.mp4`。
   本轮复核确认它们就是参考 round-3 final（详见 §3）。

曲线层 = 生产报告里的完整施加曲线（`curve_grid`，1 s 网格） vs
参考 final 曲线（drift_rebuild 锚点 10k 网格 + `fine_delay` 残差曲线）。
音频层 = 我们输出的音频 vs K 交付 restored 音频（5 min 块 / 50 % 重叠、
直接 |CC| 峰，不经过"源 vs 输出"二次反推）。

脚本：
- `work/long_take/s6/final_accept_v2.py`（验收汇总）
- `work/long_take/s6/compare_ref_vs_ours.py`（参考管线独立复算对比）
- `work/long_take/s6/audio_direct_compare.py`（ours vs 交付音频）
- `work/long_take/s6/deliver_check.py`（交付物结构/口径审计）

## 3. K 交付物复核结论：**是正确的**

此前"K C1305 restored 用错中间产物"的结论**已推翻**。原因：交付成品用的是
**round-3 drift_rebuild pass1 + fine_delay 残差**；而先前对比用的
`applytv+refine_curve` 是 **round-2** 的中间曲线（`refine_curve_*.json` 由
`refine_tv.py` 的 5 min 块残差生成，不是 round-3 的 fine_delay 残差）。

证据（`work/long_take/s6/deliver_check.py` + `compare_ref_vs_ours.py`）：
- 重跑的参考管线 final 音频 vs K 交付音频：CH1/2/3/4 med **0 / 0 / 0 / 0**
  （p90 0），即交付物就是参考管线输出；
- 容器：1×HEVC hvc1 + 4×mono pcm_s24be/ipcm + rtmd data，duration_ts/时长与源
  一致，major_brand `XAVC` 保持，`remux_final.json` 记录视频逐包 bit-exact；
- 交付音频的 CH3/CH4 相对 CH1 极性为负（已翻转），与参考管线一致。

## 4. 独立复算（实现等价性）

`compare_ref_vs_ours.py`（参考管线重算 vs 我们的输出，互不共享代码）：

| 文件 | 参考 vs K 交付（med/p90） | 参考 vs 我们的输出（med/p90） |
|---|---|---|
| C1305 | CH1/3/4 0/0，CH2 0/0 | CH1/3/4 **0/0**，CH2 4/4（决策 C） |
| C1306 | CH1/3/4 0/0，CH2 0/0 | CH1/3/4 **0/0**，CH2 4/4（决策 C） |

曲线层：我们的总曲线 vs 参考总曲线 **med 0 / p90 0 / max 0，100 % ≤5 点**
（两文件两通道）。我们的 round-1 与参考 pass1 有少量差异
（C1305 CH3 med 2/p90 4、CH4 med 6/p90 30；C1306 med 1/p90 1–3.6），
但 round-2 残差在同一 pass1 上测量后总曲线**逐点一致**——这也解释了为什么
音频输出与交付物逐块 0 差。

## 5. 性能/内存（任务书 §5.3）

| 项 | 预算 | 实测（最终参考实现，C1305 / C1306） |
|---|---|---|
| long-take 附加 | ≤20 min/文件 | **231.0 s / 193.8 s** |
| 整文件音频域 | — | **689.9 s / 389.1 s** |
| 峰值 RSS | ≤3 GB | **775 MB（主）/1470 MB（进程树）**（2026-10-31 参考引擎复测，0.5 s 采样） |

## 6. 结构与安全语义（不变，全部 PASS）

- 恒定优先 / 时变后备 / 失败回退无对齐（integration T1–T4 全 PASS）；
- stereo 绑定四重门（C1305/C1306 CH1+CH2 绑定、CH3↔CH4 拒绝）；
- 极性组级翻转（交付物同口径）；
- A6 多声道单流 / 多声道透明重封装 E2E（T5/T6 + 真实 1kt 命令）全 PASS；
- 长镜头退出语义：进程不退出；透明模式 remux 失败 = 源文件拷贝。
- **生产入口端到端**（work/long_take/s3/production_tv_e2e.py，合成 4×mono、
  10.4 ppm 真实漂移）：1kt.py --channel-sync-transparent rc=0，channel_sync
  pplied、path=time_varying、targets pply_engine=reference；输出保持
  1 视频 + 4×mono 流与 duration_ts；输出残差 CH2/CH4 = 0/0、CH3 中位 3.5 采样
  （末段 hold 边界 max 44，夹具漂移是真实素材的 ~250 倍）。

## 7. 回归

- unit：**1019 PASS / 2 FAIL**（含 8 条参考算法断言；2 FAIL 均为环境缺 Sony 语料项）；
- 全量 L3：**1251 PASS / 12 FAIL**（12 条全部是 `testsets/` 真实媒体缺失的
  环境项；无代码/算法类失败）。

## 8. 复现命令

```powershell
# 1) 重跑归档参考管线（真实素材，未修改研究脚本）
python tests\regression\long_align_tools\plot_drift2.py
python tests\regression\long_align_tools\fine_delay.py

# 2) 我们的整文件音频域流程 + 最终验收
python work\long_take\s6\run_full_align_all.py
python work\long_take\s6\audio_direct_compare.py --token c1305
python work\long_take\s6\audio_direct_compare.py --token c1306
python work\long_take\s6\compare_ref_vs_ours.py
python work\long_take\s6\final_accept_v2.py   # OVERALL: PASS
```

结果：`work/long_take/accept/final_accept_v2.json`、
`work/long_take/accept/reference_verification.json`、`deliver_audit.json`。

## 9. 仍待处理（不阻塞本验收）

1. ~~透明重封装 50 GiB 容器产物~~ **已解决（2026-10-31）**：新增
   `preservation/fast_restore.py`（一次顺序读写的字节级容器重建，不调用
   GPAC 整文件重写）；真实 50 GiB C1305 实测 stage mux 258.7 s、
   fast restore **99.1 s**，视频/rtmd 全量 streamhash 与源一致、音频 payload
   逐包一致、结构/元数据与源一致。见
   `docs/design/transparent_remux_fast_restore.md`。
2. remux 失败终态 `applied + file_copied` 的语义是否改 `verify_failed`；
3. A1 回归语料（226 片段/584 轨）本机不可用 → 以本次全量回归 + 真实两文件替代；
4. 发布/VERSION/push 决策（本地提交，未 push）。
