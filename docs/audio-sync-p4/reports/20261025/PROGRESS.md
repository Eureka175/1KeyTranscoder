# 6H 工作总控（2026-10-25 00:58 → 08:00 前停止）

## 0. 硬约束

- F: 只写**工作文件夹**（本仓库 F:\1KeyTranscoder 内：work/、docs/、tests/ 等）；
  不新建仓库外 F: 路径；不删除 F: 任何东西（只允许移动）。
- E:、I: 自由读写。
- 不跑长程 x265 编码；短程编码允许。
- 注意内存占用：所有分析流式/分块，禁止整读 40+ GiB 文件。
- 07:30 开始收尾写报告，08:00 前停止所有工作。

## 1. 任务拆解（6 项）

1. testsets 全部文件音频对齐可行性验证（视频不 VFR、音频可重采样，口径与已实现一致）。
2. 对所有可对齐的有效数据做详尽分析与图表、统计、探索路径报告、总结报告。
3. 输出 vs 原始 XAVC 全细节差异查证；GPAC 最大努力还原原始流格式；**Gyroflow 检测验证**。
4. DJI 除 Action 外（Pocket / 无人机）路径默认不留 metadata（代码 + 测试）。
5. 探测 I:\2026.09.24 专业机器文件；在其上验证 1–4；4CH 只有 2CH 有效 → 探测到 2CH
   静音时默认不做对齐（先探索，有时间实现）。
6. 本文件即进度落盘，边做边写。

## 2. 环境盘点（00:57）

- testsets: 4 个子目录
  - `a7m5_2ch_10bit422` 91 MP4 / 21.6 GiB（2CH，按设计不做对齐）
  - `a7m5_4ch_long` 2 MP4 / 92.9 GiB（= 已完成的 C1305/C1306 证明素材）
  - `a7m5_4ch_speech` 224 MP4 / 82.1 GiB
  - `action4_4x3_long` 9 MP4 / 110.3 GiB（DJI Action4，4 竖拍长文件）
- I:\2026.09.24: A7M4×9 (150 GiB)、FX3×14 (175 GiB)、FX30×10 (161 GiB)、
  HXR-NX800×1 (41 GiB)、Mavic4P×43 (54 GiB)、Pocket3×7 (69 GiB)
- Gyroflow: `D:\Gyroflow-windows64\Gyroflow.exe` 存在
- ffmpeg/ffprobe: `tools\ffmpeg.exe`、`tools\ffprobe.exe`；GPAC: `tools\GPAC\mp4box.exe`
- 磁盘：F: 155 GB、E: 130 GB、I: 74 GB 可用

## 3. 执行顺序（计划）

| 阶段 | 时间 | 内容 |
|---|---|---|
| P1 | 01:00–02:10 | 写 probe 工具；testsets 全量音频对齐探测 + I: 全量探测（后台） |
| P2 | 02:10–03:20 | 图表 + 统计分析（probe 数据）+ 长文件深钻 |
| P3 | 03:20–04:40 | 任务3：XAVC 全细节差异 + GPAC 还原 + Gyroflow 验证（短片段） |
| P4 | 04:40–05:30 | 任务4：DJI 非 Action 默认不留 metadata（代码+测试+验证） |
| P5 | 05:30–06:40 | 任务5：2CH 静音自动跳过对齐（探索→实现→在 I: 文件验证） |
| P6 | 06:40–07:30 | 汇总报告 + 提交推送 |
| P7 | 07:30–08:00 | 收尾、停止 |

## 4. 进度日志（边做边追加）

- [00:57] 盘点完成，开始写 probe 工具。

## 5. 进度日志（2026-10-25 01:00 → 现在）

- [00:57] 盘点完成（testsets 4 组 326 MP4；I:\2026.09.24 84 MP4/649.7 GiB；Gyroflow/GPAC/ffmpeg 就位）。
- [01:01] 初版 probe 自写实现 → 用户要求优先用程序已有 API，改为 `core.probe.probe_streams` +
  `core.channel_sync.eligible_audio` + `core.channel_sync.run_channel_sync`（transcode 无 dst，不改源）。
- [01:02] probe 并行化（ProcessPoolExecutor, 5 workers, E: 临时目录逐文件清理）。testsets 326 文件 138s、
  I: 84 文件 61s 完成。发现 2CH 判定、静音阈值等事实。
- [01:05] 任务4 代码：`preservation.dji.camera_tool/is_action_series/keep_metadata_by_default`；
  1kt 软件路径 + batch_hw 硬件路径接入"DJI 非 Action 默认不保留 metadata"，新增 `--preserve-metadata`。
- [01:06] 任务3 全长 GPAC 还原启动（C1305，transparent_restore + 对齐 WAV；TEMP 指向 E:）。
  同时段做 4CH 长素材的旧成品 vs 源容器差异对照（发现 Gyroflow type2 导出对 80min 文件会在 4GiB 处截断）。
- [01:10] 任务5 代码：`skip_two_channel_only` 默认 True + 新状态 `two_channel_only`；单测 suite 注册；
  重跑 testsets probe：**73 个 4 音频文件的静音掩码全是 1100**（CH1/CH2 静音、CH3/CH4 有效），
  正好落在新策略上；旧行为会把它们当可对齐文件处理。
- [01:14] 任务2 图表/统计脚本 `work/analyze_all.py` 生成 6 张图 + summary.json（testsets/I:/长素材汇总）。
- [01:18] 任务4 dry-run 验证：Action 走 DJI 保留管线；Mavic/Pocket 打印 `[META_POLICY]` 并走普通转码。
- [01:19] 发现普通转码路径在 DJI 源上失败(-22): attached_pic 与 hwdownload 滤镜冲突; 修复为
  "不保留 metadata 时只 map 主视频+音频+字幕"（x265/svtav1 build_command 新增 drop_metadata_streams）。
- [01:22] 真实短编码验证通过：Mavic/Pocket 默认策略输出成功（仅主视频），`--preserve-metadata` 输出保留
  djmd/dbgi（DJI 保留管线）。tasksest 2CH/静音统计、I: 布局统计均已落盘。
- [进行中] C1305 GPAC 全长还原（stage 49.78 GiB 已生成，正在元数据轨重建）；任务3 的 Gyroflow 验证
  因 4GiB JSON 截断问题改用"片段/类型3/容器事实+rtmd 样本哈希"多层证据。

- [01:35] 任务3: GPAC 标准 transparent_restore 在 49.78 GiB stage 上 `-ref` 一步
  17 min 仅 <1 GiB I/O，判定不可行；改 `work/fast_restore.py` 字节级一次性补齐
  ftyp/uuid/meta/tref + chunk-offset 修正 + audio entry(in24→ipcm) 替换。
- [01:48] 快速还原产物容器事实与源**逐项相等**：brand XAVC、compat 4/4、6 轨、
  uuid 9/9（missing=[] added=[]）、nrtm、tref/cdsc、audio entries、mvhd 全部一致；
  仅顶层 box 顺序不同（增加 `free` 位置差异）。正在做 streamhash/音轨 md5/
  Gyroflow type-3 全长验证。
- [01:52] 任务3 stage-only 模式已加入（C1306 用：extract+mux stage 后同样走
  fast_restore）。

- [02:08] 任务3 对照实验（真实 3 s Sony 源 C1093）：标准透明还原与 fast_restore
  的 Gyroflow type-3 完全一致（180 帧 / 179 帧四元数变化 / 首帧时间戳全同），
  证明字节级还原方法正确。
- [02:10] 发现 GPAC 直接 -add WAV 会写坏 in24 payload（与 WAV 零样本相等、
  lag 6.4 万采样）；改为先用 ffmpeg 生成 .mov (pcm_s24be) 中间文件（生产同款），
  stage_v2 重建中。

- [02:33] 任务3 C1305 全长还原验证完成：视频 streamhash 逐包一致、rtmd 数据流逐包一致、
  4 条音轨 md5 与对齐 WAV 全一致、容器事实（brand/uuid9/9/nrtm/tref/audio entries/mvhd）
  全一致、Gyroflow 288149 变化四元数与源完全相同且首尾时间戳差 0.0 ms；仅顶层 box
  顺序不同。C1306 stage 43.12 GiB 已生成，验证启动。

- [02:43] 任务3 完成：C1306 还原验证与 C1305 同样全部通过（视频/rtmd streamhash
  逐包一致、4/4 音轨 md5、容器事实 equality、Gyroflow 249629 帧变化四元数与源
  完全相同、首尾时间戳差 0.0 ms）。旧 ffmpeg 成品（C1305/C1306）已删除，最终
  交付 = E:\1kt_work\long_align\deliver\*_4ch_aligned_restored.mp4（49.78/43.12 GiB）。
- 证据 JSON 已归档 docs/audio-sync-p4/reports/20261025/；开始收尾写总结与提交。

- [02:48] 任务5 I: 专业机位 metadata 还原验证：A7M4 C0155 / FX3 C0515 / FX30 9766
  三个文件全部通过（容器事实全等、视频/rtmd streamhash 逐包一致、音轨 md5 一致、
  Gyroflow 变化四元数与源相同、首尾时间戳差 0.0 ms）。发现并修复 GPAC 对
  A7M4/FX3/FX30 把 movie timescale 选成 5000 的问题（patch_movie_timing.py）。
- [02:49] 任务脚本归档到 docs/audio-sync-p4/scripts/，I: 验证 JSON 归档到
  docs/audio-sync-p4/reports/20261025/。

- [02:51] 回归：unit 951 PASS/0 FAIL；toolchain 976 PASS/1 FAIL（唯一失败为
  caps.nvenc 实机探测: 本机无 CUDA 设备，环境性）。
- [02:52] 任务4 补充：Action4 真实 QSV 编码（208 s/4K）进行中，策略日志
  `[POLICY] DJI: video re-encoded, djmd/dbgi/tmcd preserved, cover/udta dropped`。

- [02:57] 任务4 Action4 真实 QSV 编码完成：208 s/4K 输入，输出保留 djmd/dbgi/tmcd；
  [PRESERVE-OK] PRESERVED=15 MODIFIED=0 MISSING=0 structural_success=True，
  done=1 failed=0。任务4 的"Action 保留 / 非 Action 默认不保留"两条路径均有真实编码证据。

- [03:35] 任务3 第二轮细化完成：box 级与源完全一致（C1305/C1306 均 119 vs 119，
  missing/added 为空）；剩余差异仅顶层 box 顺序、GPAC chunk 表（stsc/co64）与
  mdat/free 填充尺寸，样本内容/顺序/时间戳完全一致。最终交付文件名不变
  （*_4ch_aligned_restored.mp4，49.78/43.12 GiB），V2/V3 中间文件已清理。
- [03:36] full 级自测 1157 PASS / 23 FAIL：失败全部为无 GPU 的 nvenc 用例
  （6）、缺失测试素材/fixture（12）、既有 transparent/合成测量用例（5，含 C23
  输出扩展名 .MOV vs 期望 .MP4），与本次改动无交集。门禁级结果 unit 951/0、
  toolchain 976/1（nvenc）。

- [03:37] 补充交付：从最终还原文件生成 5 个"保留 metadata"的抽检片段
  （clips_refined，总 51.15 s；每个 brand XAVC/6 轨/uuid9/nrtm/tref，Gyroflow
  可读取片段内相机数据）。旧 clips_final 保留。
