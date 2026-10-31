# Release notes draft — 长镜头默认音频对齐（long-take default alignment）

> **状态：实现完成、真实素材验收 PASS、待用户决定发布方式（VERSION / push /
> release 平台）。** 本文件是草稿，未改变任何产品版本号。
> 相关文档：`docs/design/long_take_default_alignment.md`（任务书）、
> `docs/design/long_take_impl_checkpoint.md`（实施记录）、
> `docs/design/long_take_acceptance_report.md`（最终验收）、
> `docs/audio-sync-p4/known-limitations.md` §14（边界/未决项）。

## 功能

- **默认开启**：3–8 声道 PCM 素材在音频对齐阶段自动启用 channel-sync
  （无需新增 CLI 开关）；`>8` 声道仍需 `--audio-plan alignment=enabled`。
- 恒定延迟可用时走原固定路径；任一轨非恒定/复检失败 → **整文件时变后备**：
  - round-1 pass1 = 研究 round-3 `drift_rebuild`：2 min |CC| 加权中位锚点 →
    7 锚点（14 min）中值滤波 → 30 采样/锚点（250 ppm）限幅；
  - round-2 残差 = 研究 `fine_delay` 口径（60 s 窗 / 20 s 跳、12 kHz ±80 +
    48 kHz ±8、|CC|≥0.20、5 min 滑动中位、|r|≤100）；
  - 施加 = 参考 `interp_write` 分数插值（10,000 采样曲线网格、5M 采样块、
    clamp 后块内线性插值、越界补零）；round-2 对 pass1 再做一次插值
    （参考的 double-interp）。
- stereo 组：绑定（四重刚性门）后整组同偏移，**组内相对偏移保持源样**
  （CH1/CH2 关系不动；与 K 交付物在 CH2 上恒差 4 采样 = 预期）。
- 极性：自动检测，组级一起翻转（交付物同口径）。
- 失败安全：时变失败/测量失败/复检失败 → 整文件无对齐原始音频；轨道级
  不健康 → 该轨 untouched。
- 多声道 PCM 布局（stereo/quad/5.1/7.1 单流）：逐声道解码（绝不下混）、按源
  流重新交织输出、容器结构保持；`--audio-plan` 权限最高（透明入口让位）。

## 验收（真实 80 min 素材，两个独立 oracle）

| 文件 | 曲线层 vs 参考 final 曲线 | 音频层 vs K 交付音频 |
|---|---|---|
| C1305（80.1 min，4×mono） | med/p90/max = **0/0/0**（4808 点） | CH1/3/4 **0/0/0**，100% 块 ≤5；CH2 4/4/4（决策 C） |
| C1306（69.4 min，4×mono） | med/p90/max = **0/0/0**（4165 点） | 同上 |

- 独立复算：重跑归档参考管线（未修改研究脚本）→ 音频与 K 交付物 0/0；
  与我们输出 CH1/3/4 0/0、CH2 4/4（stereo 组决策）。
- 性能：long-take 附加 231.0 s / 193.8 s（预算 ≤20 min）；整文件音频域
  689.9 s / 389.1 s；峰值 RSS 743 MB（主）/1473 MB（进程树）。
- 测试：unit **1020 PASS / 2 FAIL**（均为缺真实 Sony 语料环境项）；
  全量 L3 **1261 PASS / 12 FAIL**（12 条均为 `testsets/` 真实媒体缺失的
  环境项，无代码/算法失败）；synthetic、integration T1–T4、A6 多声道及
  透明重封装 E2E 全 PASS。

## 行为变化 / 升级注意

1. 没有新增 CLI 开关；默认行为变化仅影响音频对齐结果。
2. 固定延迟不可用且测量到非恒定 → 现在会走整文件时变（以前 untouched/部分修）。
3. 时变失败/复检失败 → 整文件回退无对齐原始音频（不再保留部分整数修正）。
4. stereo 组（CH1/CH2 等）整组同偏移，组内相对关系不变；与部分外部参考
   成品（K）可能存在固定几采样差。
5. 透明模式 `--channel-sync-transparent` 与 `--audio-plan` 并存时由计划接管
   （INFO，不再 FATAL）。

## 已知边界 / 暂缓

- 50 GiB 级透明重封装：**已解决（2026-10-31）**。新增
  `preservation/fast_restore.py` 字节级一次顺序读写重建（不调用 GPAC
  整文件重写），透明模式 Sony 源 stage ≥2 GiB 时自动启用并做结构校验，
  失败回退标准流程。真实 50 GiB C1305 实测：stage mux 258.7 s、
  **fast restore 99.1 s（~1.08 GiB/s 合计 I/O）**；视频/rtmd 全量
  streamhash 与源一致、音频 payload 与固定 MOV 逐包一致、容器结构
  （ftyp/9 uuid/meta/track ids/tref/ipcm 条目）与源一致。
  详见 `docs/design/transparent_remux_fast_restore.md`。
- remux 失败终态仍为 `applied + file_copied`（日志 `byte copy` 可区分），是否
  改 `verify_failed` 待产品决策。
- A1 回归语料（226 片段/584 轨）本机不可用 → 以本次全量回归 + 真实两文件
  替代（按任务书 §8.2 允许的 hold-out 方式记录）。
- 发布方式（VERSION bump、push、release 平台）待用户决定；代码改动目前为
  本地提交，未 push。
