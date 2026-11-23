# v0.9.14 — DJI Action 分段连接合入主程序

## 新功能：`1kt.py --dji-join`（默认关闭）

DJI Action 相机录到约 16 GB 会切出新文件。现在主程序可以直接处理：

```powershell
python 1kt.py --input F:\Pictures\2026\202611\20261115 --output F:\archive --dji-join
```

- `--dji-join` **默认关闭**：不加该开关时行为与之前完全一致，分段文件仍是
  独立任务。
- 检测连续分段（文件名时间 + 时长连续、编号连续、前段接近 16 GB、同目录同
  参数、带 `djmd`/`dbgi` 数据轨）。
- **按轨道分别无损连接**（video-only / audio-only / data 分别 `MP4Box -cat`，
  再 `mux_new` 重新混流），避免整段 `-cat` 在 AAC 比视频略长时留下边界空帧、
  导致连接后视频被判 VFR 和硬件解码回退。
- 连接后保留 `djmd`/`dbgi`/`tmcd`（Gyroflow 增稳仍可用）。
- 连接结果放在 `<output>/.dji_join_work_*` 暂存树；全部成功后退回自动删除，
  失败/Ctrl+C/`--dji-join-keep-temp` 时保留并记录路径。
- `--dry-run --dji-join`：只检测并输出分组报告，不连接不编码。
- 连接后的 Action 机型文件默认保留元数据：joined 文件会丢失 moov 的机型
  `tool` 标签，连接时已记录首段机型并显式传递给保留管线。
- 与 `--repair` / `--retry-list` / `--channel-sync-transparent` 组合时明确
  拒绝（FATAL），不做静默降级。
- 与 `--no-metadata` 组合：仍会连接分段，但按普通转码路径跳过元数据保留
  （显式关闭优先；硬件与软件后端一致）。硬件普通路径此前会漏掉源 `tmcd`
  轨（NVEncC 中间产物携带），现在由容器重封装阶段统一剥掉，输出只含
  video+audio。
- 顺带修复：硬件后端的 `--no-metadata` 此前没有传入批处理上下文，Sony/DJI
  仍走保留管线；现在与软件后端一致，显式关闭真正生效。

辅助入口：

- `dji_join.py --input <root> [<root> ...]`：多输入根包装脚本，逐根转发给
  `1kt.py --dji-join`（主程序本身仍是单输入根）。
- `dji_join.py --join-only`：只生成连接后的 staging 树，不编码。

## 实现位置

| 文件 | 职责 |
|---|---|
| `1kt.py` | `--dji-join` 开关、输入集替换、staging 生命周期、joined 源保留策略 |
| `core/dji_join.py` | 检测、分组、无损连接、staging、报告 |
| `dji_join.py` | 多输入根 / `--join-only` 薄包装，不重复连接或编码逻辑 |
| `tests/selftest/suites/dji_join.py` | L1 契约 / L2 真实素材检测 / L3 真实裁片连接 |

## 输出格式与元数据策略全路径对齐（2026-11-16）

修复"同一源素材换后端后输出形态/保留策略不一致"的问题：

- **格式决策单源化**：新增 `encoders/format_policy.py::plan_output_format()`，
  NVENC/QSV/x265/SVT-AV1 全部调用它并输出同一份 `[FORMAT-PLAN]` /
  `FORMAT_PLAN` 日志。软件路径不再由各自 backend 私有函数决定 `-pix_fmt`。
- **QSV HEVC 去除"一律转 4:2:0"特殊分支**：改为与 NVENC 相同的 caps 能力
  判定；caps 支持 4:2:2 时保留（归档保真优先，接受直编慢路径），不支持时
  才降 4:2:0/10 并告警。本机 Arc 140T caps `422=False`，实际仍输出
  4:2:0/10 —— 这是能力事实，不再写成后端固定策略。
- **x265 4:2:2 保持不变并补测试**：x265 路径本来就走
  `yuv422p10le`（4:2:2 Rext），本次把它纳入统一策略与日志契约，而不是
  改成 4:2:0。
- **SVT-AV1 8-bit 源统一输出 4:2:0/10-bit**：与 NVENC AV1 / QSV AV1
  对齐；8→10 bit 属于 AV1 能力归一化，不按"降级"告警。
- **元数据保留决策单源化**：新增 `preservation/policy.py`，硬件路径
  （`core/batch_hw.py`）与软件路径（`1kt.py`）调用同一
  `resolve_metadata_plan()`；`--no-metadata` / `--preserve-metadata` /
  DJI 分段连接强制保留 / DJI Action 默认策略的优先级与 `META_POLICY`
  日志完全一致，不再有两份 legacy 决策。
- 兼容性提示：QSV 在支持 4:2:2 的 GPU 上直编是慢路径（本机实测约
  1.0x vs 转 4:2:0 的 2.0x）；AV1 输出统一 10-bit 后 8-bit 源的输出
  体积/解码兼容性以 AV1 10-bit 为准。
- **硬件 8-bit 计划被 profile main10 覆盖的缺陷**（2026-11-18 对齐测试发现）：
  `nvenc.json`/`qsv.json` 的 profile 恒为 `main10`，即使
  `plan_output_format()` 计划 8-bit，NVEncC/QSVEncC 仍会静默输出 Main10。
  现在硬件 backend 根据 planned depth 显式传 `--profile main`（8-bit）或
  `main10`（10-bit）；`[FORMAT-PLAN]` 与实际 pix_fmt 一致。

## QSV ICQ 码率调整（2026-11-18）

用户要求提高 QSV 输出码率；下调各档 ICQ（数值越低码率越高）：

| 配置 | 原 ICQ | 新 ICQ |
|---|---|---|
| `qsv.json` HEVC UHQ/HQ/SMALL/FAST | 21 / 22 / 26 / 24 | **20 / 21 / 23 / 22** |
| `qsv_av1.json` AV1 UHQ/HQ/SMALL/FAST | 23 / 26 / 32 / 28 | **22 / 25 / 31 / 27** |

- 原配置已备份：`work/param_backup_20261118/`（含 SHA256 MANIFEST）。
- 室内静态高噪点 4K60 实测（HQ）：HEVC ICQ22→21，码率 5.4→22.0 Mbps；
  AV1 ICQ26→25，码率 8.43→11.45 Mbps（+36%）。
- 同一素材下 HEVC 的 ICQ 22→21 存在明显非线性（QSV ICQ + QP 下限交互），
  实际码率提升大于线性预期；若下游对体积敏感，可按备份回退或改用
  `qsv_aligned.json` 的折中阶梯。
- 新增测试素材：
  `work/test_clip_library/clips/indoor_static_noisy_20261117_30s.MP4`
  （4K60 HEVC 10-bit rec709，30 s，1792 帧，SHA256 见
  `work/test_clip_library/MANIFEST.json`）。

## 验证摘要

- 合并路径（`1kt.py --dji-join`）真实素材 E2E：16 GB + 4 GB 分段 →
  1 条连接素材 → NVENC FAST + `--scale 0.25` → 960x720 HEVC；
  硬件完整性 PASS，DJI 保留 `15 PRESERVED / 0 MODIFIED / 0 MISSING`。
- 21 个源文件检测：7 组（17 段）+ 4 个独立文件。
- 默认关闭路径（不加 `--dji-join`）仍是 2 个独立任务。
- 新增 selftest：L1 23 项、L2 10 项、L3 10 项全部通过。
- 2026-11-16 对齐修复：`unit` 级 1131 PASS / 2 FAIL；2 个 FAIL 均为
  `testsets/` 真实 Sony 语料缺失导致的环境性用例（`cls.real` /
  `cls.finding`），与本次改动无关。
- 真实冒烟：x265 编码 Sony 4:2:2 10-bit 测试片 → `hvc1, yuv422p10le,
  profile=Rext`；QSV（本机 Arc 140T caps `422=False`）→ 4:2:0/10；
  SVT-AV1 编码 8-bit Canon 源 → `av01, yuv420p10le`；
  `[FORMAT-PLAN]` / `FORMAT_PLAN` 日志在三类路径一致。
- 真实 DJI Action4 源：元数据策略判定 `dji/preserve=True/
  dji_action_default (tool=DJI OsmoAction4)`；`--no-metadata` 判定
  `dji/preserve=False/no_metadata`。真实 Sony FX3 源：
  `sony/preserve=True/sony_rtmd`，同样受 `--no-metadata` 覆盖。
- 2026-11-18 对齐矩阵：6 编码器（HEVC/AV1） × 3 源类型（4:2:2/10、
  4:2:0/8、4:2:0/10） × FAST = **18/18 格式断言 PASS**；同时发现并修复
  hardware profile `main10` 覆盖 8-bit plan 的缺陷（NVENC/QSV 8-bit 源
  现在实际输出 `main`/8-bit，不再被静默提升为 Main10）。
- 2026-11-18 7 片段长测：硬件 112 输出（216.4 min，全 rc=0）+ 软件
  35 输出；QSV 新 ICQ 在全部挡位同时提高码率与 VMAF（HEVC HQ
  15.7→19.5 Mbps、98.53→98.65；SMALL 7.4→12.9、97.80→98.39）。
  归档：`docs/evaluation/4k-encoder-eval-2026-11-18/`。

详见 `docs/dji-segments.md`。
