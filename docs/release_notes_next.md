# 下一开发周期 — 硬件后端接入 `--audio-plan`（**尚未发布**）

> **状态**：已实现（**尚未发布**，未分配版本号；不属于 v0.8.0）
> **基线**：`v0.8.0`（tag `v0.8.0` = `3b4cf17`）
> **范围**：
> * NVENC / QSV 等硬件 Video Encode Backend 接入 `--audio-plan`（第 1–9 节）；
> * 面向用户的正式文档 [`docs/audio_plan.md`](audio_plan.md)（第 10 节）。
>
> 本周期**不含**：drift correction / resampling / loudness、自动多文件同步、
> Sony / DJI 保留管线上的音频计划、任何新的 CLI 参数或 JSON 字段。
> `VERSION` 仍为 `0.8.0`，**未创建 Release**。
>
> v0.8.0 的发布说明在 [`release_notes_v0.8.0.md`](release_notes_v0.8.0.md)，
> 已随 tag 冻结，**本文件不改写其任何语义**。

---

## 1. 解决的问题

v0.8.0 让 `--audio-plan` 在经典软件路径（x265 / SVT-AV1）上完整可用，但硬件后端
在启动时就被拒绝：

```text
--audio-plan
    +-- x265 / SVT-AV1   OK
    +-- NVENC / QSV      拒绝（rc=2）
    +-- Sony / DJI       拒绝（rc=2）
```

本周期把中间那一格补上，并且**不**碰第三格。

## 2. 架构：一条单向的"接手"契约

硬件批量层与音频域之间没有直接依赖。唯一接缝是视频侧定义的、不含音频词汇的回调：

```text
VideoHandoff(source, video, output, work_dir, file_logger)   <- 视频侧定义
HandoffOutcome(ok, applied, detail, errors)                  <- 接手方回填
```

`encode_one_hw_classic(video_handoff=...)`：

```text
未注册（默认）  与以前逐字相同: --audio-copy, 一趟 video+audio
已注册          audio_copy=False（只产出视频）+ 编码成功后把最终文件交给接手方
                applied=True  -> 接手方已写好 dst, 中间产物删除
                applied=False -> 空计划, 中间产物原样改名成 dst（退化为既有行为）
                ok=False      -> 明确失败（绝不忽略计划继续输出）
```

接手方在 `1kt.py`（`_audio_plan_handoff`）里实现，内部调用
`production.output`。依赖方向因此仍然是：

```text
production orchestration -> Video Backend / Audio Backend -> Output Composer
```

**为什么不是让 `batch_hw` 直接调音频**：那会把硬件批量层变成音频域的调用方，
硬解/硬编路径就会被音频计划的存在牵动。回调把方向反过来：视频侧只说"我产出好了"。

回归长期钉住：`core/batch_hw.py` 与 `encoders/*` 里 `core.audio_*` import = 0，
且**代码层面**（AST 标识符，不含注释/文档字符串）不出现音频域内部类型名。

## 3. 一个实现，两种后端

软件路径与硬件接手方调用**同一个** `_apply_audio_plan()`，它只通过
`production.output` 触达音频域。回归断言：

* `1kt.py` 里 `produce_audio_output` 只有**一个**调用点；
* 同一个 `AudioPlan` 在 x265 / svtav1 / nvenc / qsv 上产出的音频结构签名
  （codec、声道数、采样率、顺序）**完全相同**。

## 4. 支持矩阵（本机实测）

```text
encoder    + --audio-plan   结果
x265       支持             真实编码，音频结构 = 计划
svtav1     支持             同上
nvenc      支持             硬件编码 + 独立音频后端 + 组装
qsv        支持             同上
qsv-av1    支持             av1 视频 + 计划音频
nvenc-av1  已知限制          编码器无法创建（见 §8），与音频计划无关
Sony/DJI   仍未支持          逐文件明确拒绝（见 §8）
```

## 5. 执行顺序

```text
hardware decode（--hw-decode 决定，与音频无关）
  -> NVENC / QSV video encode（audio_copy=False）
  -> 纯视频产物 .part.mov
       |
AudioPlan -> Audio Backend -> audio artifact(s)
       |
   OutputComposer -> final MP4 -> 就位
```

`--check full` 的 PSNR/SSIM 质量门在视频产物上**先过门再接手**；
`--channel-sync` 与 `--audio-plan` 在 CLI 层互斥，接手路径里不存在第二条改音频的路。

## 6. 视频完整性：最强判据可用

本机实测 NVENC / QSV 在固定输入上是确定性的（连跑两次视频基本流 sha256 相同），
且 `--audio-copy` 的有无**不改变**视频基本流。因此这里断言的是最强形态：

```text
同一素材，默认路径 vs --audio-plan -> 视频基本流 sha256 完全相同
```

实测值（FAST 档，320x240，同一个 4CH 素材）：

```text
nvenc   e09fe9d62eb8c9d1…   默认 / +PCM / +AAC / +外挂 / +对齐   全部一致
qsv     dc4ff4b78669986d…   默认 / +PCM / +AAC                    全部一致
--hw-decode auto 下：
nvenc   108d493d9d5a5580…   默认 / +计划                          全部一致
qsv     02b37a852f440471…   默认 / +计划                          全部一致
```

另外逐案断言 `codec / width / height / fps / pix_fmt` 不变。

## 7. 硬件解码：不受影响（实测，不是推断）

`--hw-decode auto` 的路由不受音频计划影响。HEVC 4:2:0 10-bit 素材（白名单组合）：

```text
[HWDEC] decode route: backend=nvenc codec=hevc 4:2:0/10bit policy=auto
        -> HARDWARE (reader=avhw, reason=proven_combination)
```

带 `--audio-plan` 的运行日志里仍然是 **HARDWARE**，视频基本流与"硬件解码 + 无计划"
逐字节一致。即 **AudioPlan 不会把硬件解码踢回软解**。

## 8. 已知限制（含实测证据）

```text
1. nvenc-av1 在本机无法创建编码器：
     Max bitrate is lowered 80000 -> 66666 due to level 6.1 restriction.
     nvenc : Error on nvEncInitializeEncoder: 8 (Invalid Level.)
     Failed to create encoder
   触发条件是 nvenc_av1.json 的 level=6.1 与本机 NVENC AV1 编码器不兼容；
   加不加 --audio-plan 结果完全相同 —— 既有的 AV1 profile/驱动限制。
   本周期不修改 AV1 架构；回归同时断言"有计划"与"无计划"都失败。
2. Sony / DJI 保留管线仍不支持 --audio-plan：这类**文件**会明确失败并给出原因
   （不静默忽略、不回退到原音频、不降级为软解）。
3. 与 v0.8.0 相同、未改变的边界：drift correction / resampling / loudness 未实现；
   自动多文件同步未实现；混音图仍只产出一条输出流。
```

## 9. 测试

```text
新增套件: tests/selftest/suites/hardware_audio.py
  L1  17 断言   契约形状 / Sony-DJI 拒绝规则 / 硬件侧音频依赖 = 0 的 AST 审计 /
                CLI 面不变
  L3  36 断言   真实 NVENC + QSV: PCM / AAC / Opus / 外挂 WAV / compressed 显式对齐 /
                硬件解码 + 硬件编码 + 计划 / 空计划零回归 / 跨后端音频等价 /
                QSV AV1 / NVENC AV1 已知限制 / Sony 真实素材拒绝 / CLI 冲突

另外在生产入口套件里新增 2 条"文档契约"断言: docs/audio_plan.md 的每个 json
代码块都必须能被真实请求解析器解析。
```

## 10. 用户文档

新增 [`docs/audio_plan.md`](audio_plan.md) —— 面向使用者的正式文档：

```text
一分钟上手 / 是什么 / 默认行为 / 各后端支持表 / 计划文件结构 /
选择-排除-排序 / 编码继承与"什么时候不会重编码" / alignment（含压缩音频规则）/
mapping（source / independent / grouped）/ 外挂音频命名与排序规则 /
硬件后端用法 / 常见组合（可直接复制）/ 限制与未实现 / 排错
```

README 只增加入口链接，不塞入完整手册。

**文档防腐烂**：回归断言 `docs/audio_plan.md` 里每个 ```json 代码块都能被真实
请求解析器解析通过。这条检查在写作过程中真的抓到两个文档缺陷（一个带 `...`
占位的伪 JSON、一个 `map` 与 `select` 集合不一致的示例）。

## 11. 版本

```text
VERSION        0.8.0（本周期未改动）
tag            未新增
GitHub Release 未创建（本周期）
```

发布（patch 还是 minor）留待下一阶段决定。
