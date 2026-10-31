# v0.8.11 发布说明 — 默认长镜头音频对齐 + 50 GiB 透明重封装 fast restore

> **状态**：**Pre-release**（GitHub prerelease；本地 tag `v0.8.11`；`VERSION` = `0.8.11`）
> **基线**：`v0.8.8`（tag `v0.8.8` = `3d3dcac`）；自 v0.8.8 起共 17 个提交
> **日期**：2026-11-01
> **范围**：音频域（channel-sync / 长镜头时变对齐 / 透明重封装容器恢复）。
> **不含**：新的 CLI 开关（`--channel-sync-tv` 等仍只是既有内部策略入口）、
> 视频编码算法/画质策略变更、漂移模型之外的 resampling/loudness。

---

## 0. 本版一句话

v0.8.8 解决了"已经产出的成品怎么修"；v0.8.11 解决"**长镜头素材怎么在默认路径上
一次对齐到位**"，并把**透明模式 50 GiB 容器产物的恢复**从"GPAC 多次整文件重写
（实测不可用）"换成**一次顺序读写的字节级重建**。

## 1. 默认长镜头音频对齐（核心）

### 1.1 行为

3–8 声道 PCM 素材在 channel-sync 阶段**默认自动**启用长镜头对齐（不需要新开关）：

1. 恒定延迟可用 → 原固定路径（与旧版一致）；
2. 任一音轨非恒定 / 复检失败 → **整文件时变后备**：
   - round-1 pass-1 = 研究 round-3 `drift_rebuild`：2 min |CC| 加权中位锚点 →
     **7 锚点（14 min）中值滤波** → **30 采样/锚点（250 ppm）限幅** →
     锚点线性插值；
   - round-2 残差 = 研究 `fine_delay` 口径（60 s 窗 / 20 s 跳、12 kHz ±80 +
     48 kHz ±8 精化、|CC|≥0.20、5 min 滑动中位、|r|≤100）；
   - 施加 = 参考 `fine_delay.interp_write` 的**分数插值**（10,000 采样曲线网格、
     5M 采样块、clamp 后块内线性插值、越界补零）；round-1 与 round-2 都走它
     （double-interp，与 K 研究成品同口径）；
3. **stereo 组**：四重刚性门（|CC|、lag span、同号、候选 SNR）确认后整组同偏移，
   **组内相对关系保持**（CH1/CH2 关系不动；与部分外部成品的固定 4 采样差属预期）；
4. **极性**：逐组检测并整组翻转；
5. **多声道 PCM 布局（A6）**：stereo/quad/5.1/7.1 单流逐声道解码（绝不下混）、
   按源流重新交织输出、容器结构保持；`>8` 声道仍只允许
   `--audio-plan alignment=enabled` 显式开启；
6. **`--audio-plan` 权限最高**：计划存在时自动逻辑完全让位；透明模式与计划并存时
   由计划接管（INFO，不再 `FATAL`）；
7. **失败安全**：时变失败/测量失败/复检失败 → 整文件回退无对齐原始音频；轨道级
   不健康 → 该轨 untouched；透明模式 remux 失败 → 源文件拷贝。

### 1.2 真实 80 min 素材验收（PASS）

用两个独立 oracle（重跑归档研究管线 + K 交付成品）验证：

| 文件 | 曲线层 med / p90 / max | 音频层（vs K 交付） |
|---|---|---|
| C1305（80.1 min） | **0 / 0 / 0**（4808 点全 ≤5） | CH1/CH3/CH4 **0/0/0，100% ≤5**；CH2 **4/4/4**（stereo 组决策，预期） |
| C1306（69.4 min） | **0 / 0 / 0**（4165 点全 ≤5） | 同上 |

- round-1 曲线与 K 研究 pass1 **逐位一致**；输出音频与 K 交付成品在 5 min 块上
  0/0/0（CH1/3/4），CH2 恒差 4 采样（0.083 ms，故意保持组内关系）。
- 独立复算：重跑归档研究脚本（`plot_drift2.py` + `fine_delay.py`，未修改）的成品
  与我们输出 CH1/3/4 0/0、CH2 4/4；K 交付物被证实就是该研究 round-3 final
  （此前"K C1305 restored 用错中间产物"的怀疑已撤销）。
- 确定性：同一输入第二次完整运行，输出音频 raw 逐位相同、施加曲线逐点相同。

### 1.3 性能（任务书 §5.3，实测）

| 项 | 结果 |
|---|---|
| long-take 附加耗时 | C1305 **231.0 s** / C1306 **193.8 s**（预算 ≤20 min/文件） |
| 整文件音频域 | C1305 **689.9 s** / C1306 **389.1 s** |
| 峰值 RSS | 主进程 **775 MB** / 进程树 **1470 MB**（预算 ≤3 GB） |
| S5 并行 | 残差窗（~239 窗 50 s→15 s/target）、stereo 绑定候选对并行 |

## 2. 透明模式 50 GiB 容器产物：byte-level fast restore

### 2.1 问题

标准 `preservation.transparent_restore` 用多次 GPAC 操作恢复 Sony 元数据
（`-ref` / meta / `-flat` / `-brand` / uuid），**每一步都整文件重写**：研究阶段
实测 `-ref` 一步 17 min 推进不到 1 GiB，50 GiB 上不可用。

### 2.2 方案

新增 `preservation/fast_restore.py`：**一次顺序读写**完成容器重建，不调用任何
GPAC 整文件重写：

```text
ftyp(源) + root-uuid(源) + mdat(stage 流式拷贝)
+ 重建 moov(stage moov + 插入 per-trak/moov uuid + tref（track id 源→stage 映射）
  + 换回源 ipcm 音频 sample entry（长度变化，同步修正 6 层祖先大小）
  + 按新 mdat 位置平移全部 stco/co64 并做范围校验)
+ 其余 root box + 文件级 nrtm meta(源)
```

- 透明钩子在 Sony 源且 stage ≥ **2 GiB** 时自动启用；结构校验
  （ftyp/uuid 多重集/meta/音频条目/track ids/tref）失败或异常 → 自动回退标准
  GPAC 流程；无元数据的源（既无 uuid/meta 也无 meta 轨 tref）会被前置检查拒绝，
  不会产出一个"看着像成品"却没有元数据的文件。
- 不新增 CLI 开关；注入钩子签名不变（6 位置参数），测试用
  `set_fast_restore_mode()`。

### 2.3 实测

| 场景 | 结果 |
|---|---|
| 50 GiB C1305 stage mux（一次 GPAC 写） | **258.7 s（0.19 GiB/s）** |
| 50 GiB C1305 fast restore（50 GiB 读+写） | **99.1 s（0.50 GiB/s；合计 I/O ≈1.08 GiB/s）** |
| **真实 `1kt.py --channel-sync-transparent` 端到端** | 总 **12.8 min**；`[SYNC-OK]`；Sony checker **PRESERVED=37 MODIFIED=0 MISSING=0**；独立复核 **20/20 PASS**（视频 10 s/全长 streamhash、rtmd payload、4 音频 payload、解码音频 diff=0） |
| C1306 全长 43.12 GiB | stage mux 162.9 s + fast restore **57.8 s**，16/16 检查 PASS |
| 更宽矩阵（7 个真实源） | **全部 PASS**（XAVC restored clips、原始 `isom` 布局 hard cases C0911/C0912/C0915/C0916、两条全长）；flattened-stage 变体、负向校验、门槛/回退分支均覆盖 |

> 注意：GPAC 仍然用于 **生成 stage**（一次 `mux_new`，约 4 min/50 GiB）和
> **抽取元数据 bundle**（只读）。被消除的是恢复阶段的多次整文件重写。
> 完全去掉 GPAC 需要自写 muxer + 纯 Python nrtm 解析，不在本版范围。

## 3. 多声道 PCM 布局（A6）与 bug 修复

- **A6 多声道布局**：stereo/quad/5.1/7.1 单流支持（逐声道拆、按源流重新交织、
  codec/layout/duration_ts 保持；单声道多流路径逐位不变）。
- 修复：多声道解码并发共享 `AudioPCMReader` 文件句柄导致 CH3/CH4 读成全零
  （改为按 stream 分块并行、块内声道顺序）。
- 修复：stereo 绑定组 + 组代表 fixed 时 `ref_of` 未初始化（UnboundLocalError）。
- 修复：单声道多流布局 `fixed_files` 为空。
- 修复：16-bit `ipcm` sample entry 解析误判导致透明重封装降级为源文件拷贝。
- 修复：`GPAC` 导入暂存写到 C: 导致 50 GiB remux `ENOSPC`（现固定到媒体卷
  `.gpactmp`，每次调用后清理）。
- 修复：透明模式下 `--channel-sync-tv` 未转发、同步失败与文件失败混淆。
- 测试：新增 long-take L1 套件（窗级测量 + 参考算法 + drift_rebuild 稳定化 +
  分数插值语义）、A6/透明多声道 E2E、`--audio-plan` 权限最高契约、fast-restore
  入口契约；更新受默认行为变化影响的既有 C15–C32 用例。

## 4. 硬件后端接入 `--audio-plan`（随 v0.8.8 交付，本版说明补录）

`core/batch_hw.py` 的 `VideoHandoff` 单向接手契约（NVENC/QSV 编码 → 音频接手方）
已经在 v0.8.8 代码中可用；详细设计/实测记录见
[`release_notes_next.md`](release_notes_next.md)（该文件此后作为历史设计文档保留）。
本版没有改动硬件后端接口，只保证长镜头/透明改动不影响它（全量回归覆盖）。

## 5. 升级注意 / 行为变化

1. **默认音频行为变化**：固定延迟不可用且测到非恒定 → 现在走整文件时变对齐
   （旧版可能 untouched/部分修）；时变/复检失败 → 整文件回退无对齐原始音频。
2. **无新 CLI 开关**；`--channel-sync` 语义与既有参数不变。
3. `--audio-plan` 权限最高：与 channel-sync/透明入口并存时由计划接管（不再 FATAL）。
4. stereo 组（CH1/CH2 等）整组同偏移、组内关系保持；与部分外部成品可能有固定
   几采样差。
5. `>8` 声道仍只允许 `--audio-plan alignment=enabled`。
6. 透明模式 50 GiB 产物现在可以真正生成；运行期需要 stage + 输出两份磁盘空间
   （与旧标准流程同量级），stage 用完即删。

## 6. 测试与验收状态

| 套件 | 结果 |
|---|---|
| unit | **1020 PASS / 2 FAIL**（2 条仅因本机缺真实 Sony 语料；无代码失败） |
| 全量 L3 | **1261 PASS / 12 FAIL**（12 条均为 `testsets/` 真实媒体缺失的环境项；无代码/算法失败） |
| 长镜头真实验收 | C1305/C1306 曲线 **0/0/0**、音频 CH1/3/4 **0/0/0**、CH2 预期 4（见 §1.2） |
| 透明 50 GiB | 端到端 12.8 min，Sony checker 37/37，独立复核 20/20 PASS（见 §2.3） |
| fast-restore 矩阵 | 7 个真实源全部 PASS（16 项检查/源） |
| 复用/确定性 | 同一输入二次运行输出逐位相同 |

验收脚本/报告（不随发布包分发，仓库 `work/` 下）：`final_accept_v2.json`、
`reference_verification.json`、`transparent_e2e_50g.json`、`fast_restore_50g.json`、
`fast_restore_matrix.json`。

## 7. 安装与校验（self-contained 包）

- 包：`1KeyTranscoder-v0.8.11-win64-selfcontained.zip`（附带 `.sha256` sidecar）。
- 环境：Windows 10/11 x64、Python 3.11+、对应 GPU 驱动（NVENC/QSV 时）。
- 包内自带 ffmpeg/ffprobe、NVEncC 9.31、QSVEncC 8.26、GPAC 26.02。
- 构建/校验：

```powershell
python release\build_release.py --out-dir dist
python release\verify_package.py `
    dist\1KeyTranscoder-v0.8.11-win64-selfcontained.zip `
    --extract-dir "$env:TEMP\1KT-v0.8.11-clean"
```

## 8. 已知限制 / 未决项

- **A1 回归语料**（226 片段/584 轨）本机不可用 → 以全量回归 + 两条真实 80 min
  文件 + 7 源矩阵替代（按任务书 §8.2 的 hold-out 记录方式）。
- 透明模式 remux 失败终态仍是 `applied + file_copied`（日志 `byte copy` 可区分），
  是否改 `verify_failed` 留待后续产品决策。
- fast restore 仅支持"源带 Sony 容器元数据（rtmd + meta 轨 tref + uuid/nrtm）"的
  场景；非 Sony 源不走该路径。
- 长镜头验收使用 K 交付成品作为 oracle；K 自身在弱内容段有内容歧义噪声，但本次
  两个独立 oracle 一致到 0/0/0（见 §1.2）。
- 本版为 **prerelease**；如需要可先在小批量真实素材上抽查后再转正。

---

**完整变更记录**：`git log v0.8.8..v0.8.11`（17 commits）。
