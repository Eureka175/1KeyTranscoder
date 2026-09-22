# 容器格式策略 (Container Format Policy)

本文件记录 1KeyTranscoder 的容器选择规则, 以及这些规则的实测依据。
规则只有两句:

> **MOV 是硬件编码的工作容器 (working container)。**
> **最终输出默认继承输入容器 (final container = input container)。**

---

## 1. 旧行为

| 环节 | 旧行为 | 代码位置 (修改前) |
|---|---|---|
| 硬件编码中间产物 | 文件名 **`.mov`**, 实际容器由 `-f mp4` 写成 (硬编码) | `encoders/nvencc.py:161`、`encoders/qsvencc.py:156` |
| 最终输出 | 永远 `.MP4`, 与输入无关 | `core/paths.py` `output_path_for()` -> `f"{stem}.MP4"` |
| 交付 | 中间产物**直接改名**成最终文件 | `core/batch_hw.py`、`1kt.py`、`preservation/` |

于是硬件路径上同时存在两处"名实不符":

```text
encoded.mov / <stem>.part.mov      <- 名字是 MOV, 内容由 -f mp4 写成
final.MP4                          <- MOV 内容被改名成 .MP4 (输入是 MOV 时也是)
```

Sony / DJI 保留路径的 `final/output.mov` 是真实 MOV, 但交付时同样被改名
成 `.MP4`。

### 1.1 为什么原先没有被发现

NVEncC 的 `-f mov` 与 `-f mp4` 都经 libavformat 的同一个 `mov` muxer 输出,
两者对**管线真正依赖的字段**完全一致 (§3), stts 时长补丁与 GPAC 导入因此
都照常工作。名实不符在这种情况下是潜伏缺陷: 它不会立刻报错, 而是让
"后缀" 不再能作为容器的事实来源。

---

## 2. 问题

1. **文件内容和后缀必须一致。** 后缀是下游消费者 (用户、Gyroflow、剪辑软件、
   归档工具) 判断容器的第一依据。`encoded.mov` 实际是 MP4, `final.MP4`
   实际是 MOV, 都属于"声称的容器不是真实容器"。
2. **最终容器与输入无关。** MOV 源经硬件路径后一定变成 `.MP4`, 用户无法
   通过默认行为保留输入格式。
3. **两处缺陷必须一起修。** 只改 `core/paths.py` 或只改 `encoders/*.py`,
   只是把"MP4 内容冒充 MOV"换成"MOV 内容冒充 MP4", 没有解决任何问题。

---

## 3. 实测依据: `-f mov` 与 `-f mp4` 到底差在哪

在 NVEncC 9.31 (r4047) 上对同一源、同一编码参数只改 `-f` 值 (A7M5 4K60
HEVC 10bit, 60 帧 / 30 帧两种长度复测):

| 项 | `-f mp4` | `-f mov` |
|---|---|---|
| `ftyp` 长度 | 28 | 20 |
| `ftyp` major brand | `mp42` | **`mp42`** |
| `ftyp` compat 列表 | `mp42, iso2, mp41` | **`mp42`** |
| 填充 box | `free` | `wide` |
| `mvhd` | timescale **1000** / duration 501 | timescale **1000** / duration 501 |
| 视频 `mdhd` | 240000 / 120120 | 240000 / 120120 |
| 视频 `tkhd` duration | 501 | 501 |
| 视频 `elst` | `(501,0)` | `(501,0)` |
| 视频 `stts` | `n=1 [30x4004]` | `n=1 [30x4004]` |

结论 (两个都必须记住):

* `-f mov` 与 `-f mp4` **确实不是同一个文件**, 所以"把 `-f mp4` 的输出
  叫 `.mov`"是真实的错误, 不是无害的命名差异;
* 但两者对管线有意义的数值**完全相同** (rigaya 经 libavformat 输出,
  `mvhd` 固定 1000 毫秒时基 + 毫秒量化的 `elst`), 所以
  `preservation.isobmf.patch_track_durations(from_stts=True)` 的
  stts 时长修复对两者行为一致 —— 这一点是实测确认的, 不是推断;
* **major brand 两者都是 `mp42`**, 所以"靠 ftyp major brand 判断 MOV/MP4"
  是错的。唯一可判定的差异是 compat 列表是否含 ISO 品牌 (`iso2`/`mp41`)。
  ffprobe 也不区分: 两者都报同一个 demuxer
  `mov,mp4,m4a,3gp,3g2,mj2`。

这条实测限制了实现: 既然工具层无法可靠区分两种 ISOBMFF 变体, 代码就不该
假装能区分。见 §5。

---

## 4. 新规则

### 4.1 硬件编码中间产物 = MOV

```text
source
  ↓
hardware decode / reader
  ↓
hardware encode          -f mov  (NVEncC / QSVEncC)
  ↓
MOV intermediate         work/<job>/video/encoded.mov
                         <out>/<stem>.part.mov
```

`encoders/nvencc.py` 与 `encoders/qsvencc.py` 的 `command()` 增加显式
`container` 参数 (默认 `"mov"`), `-f` 值由参数决定, 不再硬编码。调用方在
`core/batch_hw.py` 里用 `HW_VIDEO_CONTAINER = "mov"` 显式传入, 于是
"硬件工作容器是 MOV" 成为**代码里说出来的事实**, 而不是 backend 的隐含默认。

为什么是 MOV 而不是 MKV: GPAC 的 MKV reader 会把时间戳量化到毫秒
(`preservation/pipeline.py` 模块注释、`encoders/x265.py` 同款说明),
会破坏 rtmd 精确对齐。MOV 是唯一同时满足"GPAC 不量化时间戳"和
"能承载 HEVC"的中间容器。

#### 例外: AV1 中间产物必须是 MP4

MOV **不能**承载 AV1。这是 muxer 的硬约束, 不是偏好 —— 实测在固定版本上
(NVEncC 9.31 / QSVEncC 8.26) `-f mov` + `-c av1`:

```text
[mov @ ...] av1 only supported in MP4 and AVIF.
avout: failed to write header for output file: Invalid argument
encoded 0 frames, 0.00 fps, 0.00 kbps, 0.00 MB
```

直接产出 **0 帧**, 整轮 encode 失败。所以工作容器按 **codec** 取值:

```python
_INTERMEDIATE_CONTAINER_BY_CODEC = {"av1": "mp4"}
def intermediate_container(codec): ...   # hevc -> "mov", av1 -> "mp4"
```

这条约束代码库里已经有两处独立记录 —— `1kt.py` 的
`part_ext = ".part.mp4" if backend.name == "svtav1"`, 以及
`preservation/pipeline.py` 的 "AV1 后端传入 .mp4 (ffmpeg 9 的 MOV muxer
不接受 AV1, MP4 可以)"。本次把散落的重复推导收成**一个函数**, 并让经典
硬件路径的中间产物后缀 (`<stem>.part<ext>`) 也按它取值 —— 此前该后缀
恒为 `.part.mov`, 而 AV1 编码器写的是 MP4, 又是一处名实不符。

AV1 的**最终**容器仍跟随输入。若输入是 MOV 而中间产物是 MP4,
`place_final` 会尝试 MP4 -> MOV 重封装; MOV 装不下 AV1, 因此 AV1 + MOV
源会**明确失败** (而不是产出假 MOV)。该组合在改动前同样是失败的
(`-f mp4` 写入 `*.part.mov`, 续跑时又按 `.part.mov` 这个路径复检),
属既有缺口; AV1 + MOV 源的支持是独立议题, 本任务不顺手扩展。

### 4.2 最终容器 = 输入容器

`core/paths.py`:

```python
FINAL_SUFFIX_BY_SOURCE = {".mov": ".MOV", ".mp4": ".MP4"}
FINAL_SUFFIX_DEFAULT = ".MP4"
```

| 输入 | 中间产物 | 最终输出 |
|---|---|---|
| `foo.MOV` | MOV | `foo.MOV` (`place_final` 直接改名, 无重封装) |
| `foo.MP4` | MOV | `foo.MP4` (无损容器重封装) |
| 其它扩展名 | MOV | `foo.MP4` (沿用旧行为) |

(`AV1` 的中间产物是 MP4, 见 §4.1; 最终容器规则不变。)

扩展名统一**大写** (`foo.mov` 与 `foo.MOV` 都得到 `foo.MOV`), 只规范化扩展名,
不动主干。

不同时把 `.MKV`/`.TS`/`.MXF` 一起改成"跟随输入", 是刻意收窄范围:

* 硬件中间产物是 MOV, 重封装到 MKV/TS/MXF 并不无损 (需要不同 muxer 与
  时间基语义), 不做;
* 范围收窄到两个 ISOBMFF 容器, 是"目前能承诺无损双向重封装"的边界。
  未列出的扩展名保持旧行为 (输出 `.MP4`), 因此没有回归。

### 4.3 容器转换绝不重新编码

`core/batch_hw.py::place_final()` 是**唯一**的交付出口, 三种走向:

```text
intermediate 后缀 == 目标后缀          -> 直接改名 (不做任何重封装)
目标后缀需要转换, 且中间产物是 ISOBMFF -> 无损容器重封装后改名
目标后缀需要转换, 但无法完成转换        -> 抛错 (绝不改名冒充)
```

"无法完成转换"指中间产物不是 ISOBMFF (例如 Matroska, MP4Box 读不了) 或
没有 GPAC 后端。此时**报错而不是改名**, 因为把非 MP4 改名成 `.MP4` 正是
本函数要防的那个缺陷。

重封装实现 (`remux_container_lossless`) 用 GPAC/MP4Box 按**轨道号**逐条
原生复制 (不是 `#video`/`#audio` 语义选择器 —— 那种写法只取第一条音轨,
多轨 PCM 会丢轨), 并把源 movie timescale 线程化传入, 避免 GPAC 把 movie
时基重置为首轨媒体时基。

---

## 5. 两个实现陷阱 (都实测踩过)

### 5.1 不能用 ftyp major brand 判断容器

见 §3: `-f mov` 与 `-f mp4` 的 major brand 都是 `mp42`。第一版实现用
"`qt  ` 就是 MOV" 判断, 于是把 `-f mov` 的产物判成 MP4 (因为它 "不是
`qt  `"), 逻辑完全反过来。现在代码只判断"是不是 ISOBMFF"
(`_iso_media_kind`), 不再假装能区分两种变体。

### 5.2 staging 必须用**目标**后缀

MP4Box 从 `-new` 路径的**扩展名**决定输出文件类型 (以及 ftyp brand)。
第一版把重封装产物 staging 成 `container.mov` 再改名, 结果:

```text
-new container.mov  ->  ftyp major = 'qt  '   <- 名字叫 .MP4, brand 是 QuickTime
-new container.MP4  ->  ftyp major = 'isom'   <- 正确
```

即"MP4 只存在于文件名里", 与本任务要消灭的缺陷同类。现在 staging 路径用
`dst.suffix`, L3 测试用 "major brand 不是 `qt  `" 钉住这条。

---

## 6. 验证

### 6.1 单元级 (L1, `tests/selftest/suites/core.py::l1_container_format`)

* `final_suffix_for` 对 `.mov`/`.MOV`/`.Mov` -> `.MOV`, `.mp4`/`.MP4` ->
  `.MP4`, `.mkv`/`.ts`/`.mxf` -> `.MP4` (旧行为);
* `output_path_for` 保留目录结构与多 preset 目录维度;
* `intermediate_container("hevc") == "mov"` 且 `intermediate_container("av1")
  == "mp4"` (§4.1 的 codec 例外);
* 重封装表只覆盖 ISOBMFF 容器;
* `_iso_media_kind` 对 ISOBMFF / Matroska / 空文件的判定。

### 6.2 集成级 (L3, `tests/selftest/suites/hardware.py::l3_container_format`)

真实 NVENC 编码 (经典路径), 每一种输入各跑一遍完整程序, 断言**看实际
容器而不是看后缀**:

| 断言 | MOV 输入 | MP4 输入 |
|---|---|---|
| 交付成功 | ✓ | ✓ |
| 最终后缀 = 输入后缀 | `.MOV` | `.MP4` |
| 硬件产物为 ISOBMFF | ✓ | ✓ |
| 硬件编码用 `-f mov` (compat 无 `iso2`/`mp41`) | ✓ | ✓ |
| 同容器直接改名, 无 `.part.mov` 残留 | ✓ | — |
| 中间产物保留 (供重封装读取) | — | ✓ |
| 视频基本流 SHA256: 中间 == 最终 | — | ✓ (相同) |
| 产物是 ISO MP4 (major brand 非 `qt  `) | — | ✓ |
| 产物不是中间产物原样改名 | — | ✓ (字节不同) |
| 非 ISOBMFF 中间产物 + `.MP4` 目标 -> 拒绝 | ✓ (抛错) | |

视频基本流比对用 `ffmpeg -c copy -f streamhash`, 因此 "ES hash 一致"
等价于"没有重新编码"。

### 6.3 手工实测 (研究记录, 不入仓库)

`work/container_probe/` 下的临时脚本做过的独立确认:

* NVEncC 9.31 `-f mov` / `-f mp4` box 级解剖 (§3);
* 单文件隔离运行的 MOV 输入: 输出目录**只有** `classic_test.MOV` 与编码
  日志, `.part.mov` 已被改名消耗 —— 与"改名而非重封装"一致;
* MP4 输入: `classic_test.part.mov` 与 `classic_test.MP4` 的视频基本流
  SHA256 相同, 大小相差约 1.8 KB (纯 box 重写)。

---

## 7. 回归状态

完整 L3 套件 (`tests/full_autotest.py --level full`) 的 `pipeline` 组在本改动
上跑过两轮, 第一轮**抓到一个真实回归**, 见 §7.2。

| 项目 | 结果 |
|---|---|
| L1 单元 (`--level unit`) | **617 PASS / 0 FAIL** (含新增容器策略组) |
| L3 容器格式 (新增 `l3_container_format`) | **16 PASS / 0 FAIL** |
| L3 `pipeline` 组 | **全绿** (966s): Sony HEVC / DJI HEVC / QSV / x265 / NVENC / AV1 (nvenc-av1, qsv-av1, svtav1) / channel-sync / quality / resume / retry / strip 回退。首轮 AV1 硬件组 3 项 FAIL -> 修复后 0 FAIL |

### 7.1 保留路径为什么没被改动破坏

保留路径的**内部工作容器保持 MOV 不变** (`preservation/pipeline.py` 的
`final/output.mov`、`preservation/dji.py` 的 `final/output.mov`), GPAC 重建、
rtmd/nrtm/uuid 补丁、`validate.compare` 全部照旧比对同一个中间产物; 容器
转换只在 `validate` **通过之后**发生, 且只搬 box 不动基本流。Sony 的
`movie_timescale` 由 bundle 线程化到每个改写命令, 重封装时同样线程化传入。

HEVC 硬件路径的中间产物文件名与改动前**逐字相同** (`encoded.mov` /
`<stem>.part.mov`), 所以保留管线的续跑 (`_encoded_ok` 复检) 与既有日志格式
不受影响。

### 7.2 首轮回归抓到的问题 (AV1 不能被 MOV 承载)

把工作容器无条件写成 `mov` 是**错的**。首轮 L3 直接报出:

```text
[mov @ ...] av1 only supported in MP4 and AVIF.
avout: failed to write header for output file: Invalid argument
encoded 0 frames, 0.00 fps, 0.00 kbps, 0.00 MB
```

C33 (`nvenc-av1` DJI) / C34 (`nvenc-av1` Sony) / C35 (`qsv-av1` DJI) 三项
FAIL, rc=1。修复即 §4.1 的 `intermediate_container(codec)`: AV1 -> `-f mp4`。
复测三项全部 PASS (`codec_name=av1`, `pix_fmt=yuv420p10le`, 元数据轨
`djmd/dbgi/tmcd`、`rtmd` 均在)。

这条教训值得留在文档里: **"硬件工作容器统一 MOV" 必须限定为"非 AV1"**。
本任务的目标句是"中间工作容器统一 MOV", 而 AV1 是 muxer 层面的硬例外;
把它当成单纯命名问题会直接产 0 帧。

### 7.3 已知缺口

Sony MP4 源 (即"内部 MOV 工作容器 + 最终 MP4") 的完整端到端目前没有真实
素材 fixture —— `testsets` 里的 Sony 素材都是 `.MP4` XAVC, 现有 `pipeline`
套件的 Sony 用例断言的交付文件名仍是 `.MP4`, 与"跟随输入"一致。Sony
preservation 对 MP4 源的端到端覆盖属于下一阶段的测试缺口, 已在报告里
明确标出, 不用单元级断言冒充端到端验证。

AV1 + MOV 源 (§4.1 末) 同样是已知缺口, 改动前后都失败, 状态未变。

---

## 8. 相关代码

| 文件 | 职责 |
|---|---|
| `encoders/nvencc.py` / `encoders/qsvencc.py` | `command(..., container="mov")` -> `-f <container>` |
| `core/paths.py` | `FINAL_SUFFIX_BY_SOURCE` / `final_suffix_for()` / `output_path_for()` |
| `core/batch_hw.py` | `HW_VIDEO_CONTAINER`、`intermediate_container()`、`place_final()`、`remux_container_lossless()`、`_iso_media_kind()` |
| `1kt.py` | 软件路径与 Sony/DJI 软件路径同样经 `place_final()` 交付 |
| `preservation/pipeline.py`、`preservation/dji.py` | 保留管线内部工作容器 (MOV) 与 validate |
| `tests/selftest/suites/core.py::l1_container_format` | L1 策略断言 |
| `tests/selftest/suites/hardware.py::l3_container_format` | L3 真实容器端到端断言 |
