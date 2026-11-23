# XF-HEVC 编码结构调研报告

**对象**：Canon EOS R6 V 的 XF-HEVC 素材（`A_0013C699…` / `A_0013C700…`）
**方法**：ISOBMFF box 逐层解析 + HEVC 位流直读（`trace_headers`），**只读**，
不依赖任何解码器推断
**对照基准**：Sony XAVC（取自本项目自身的代码与文档记录）
**结论一句话**：**XF-HEVC 与 XAVC 的结构范式相同、具体标识与载荷不同。**

> 素材身份锚点：
> A0013C699 `0383003f…`（154 帧）、A0013C700 `1a31c8d3…`（686 帧）

---

## 目录

1. [总体判断](#1-总体判断)
2. [容器层](#2-容器层)
3. [轨道层](#3-轨道层)
4. [HEVC 位流层](#4-hevc-位流层)
5. [Canon 私有 metadata 层](#5-canon-私有-metadata-层)
6. [与 XAVC 逐项对照](#6-与-xavc-逐项对照)
7. [为什么 rigaya 缺陷对两者同源](#7-为什么-rigaya-缺陷对两者同源)
8. [产物索引与复现](#8-产物索引与复现)
9. [已知限制](#9-已知限制)

---

## 1. 总体判断

XF-HEVC 与 XAVC 都是「**厂商私有 ISOBMFF 方言**」，共享同一套结构范式。

**本节的对照已全部改为实测**（见 §6）：同一套探针跑在 XF-HEVC 与两条真实
Sony XAVC 素材上，不再引用文档推断。

```
ftyp(厂商 brand)                       XFVC  /  XAVC(+mp42,nras,iso6)
 ├ uuid[×N]  厂商私有 metadata          Canon: 1 个 {85C0B687-…}
 │                                      Sony : PROF×1 + USMT×4~8 + 其他
 ├ free                                 Canon: 无    Sony: 有
 ├ moov
 │  ├ mvhd                              timescale 30000 / 60000
 │  ├ trak  video  (hvc1)               LongGOP + ctts + elst（两侧一致）
 │  ├ trak  audio  × N                  ipcm 或 twos，1~4 声道
 │  ├ trak  tmcd                        Canon: 有    Sony: 无
 │  └ trak  meta  (cndm / rtmd)         tref cdsc → 视频轨（两侧一致）
 ├ mdat
 └ meta                                 Canon: 无    Sony: 有（根级，~10 KB）
```

**同构项**（机制相同）：

| # | 机制 | XF-HEVC | XAVC |
|---|---|---|---|
| 1 | 厂商私有 brand | `XFVC` | `XAVC` |
| 2 | open-GOP 时间轴 | LongGOP + `ctts` + `elst` | 同 |
| 3 | 定时 metadata 轨（`meta` handler + `cdsc`） | `cndm` | `rtmd` |
| 4 | 厂商私有 uuid box | `{85C0B687-…}` ×1 | `PROF` / `USMT` ×5–9 |

**关键差异**：视频 profile（XF-HEVC 是 **Rext 4:2:2 10-bit**）、
metadata 载体与载荷（`cndm` KLV vs `rtmd` 私有二进制）、
**独立 `tmcd` 轨的有无**、**moov 位置**、**根级 `meta` box 的有无**、
音频配置。

---

## 2. 容器层

### 2.1 顶层布局

| 项 | 值 |
|---|---|
| 文件大小 | 323,904,650 B（A0013C699） |
| 顶层 box 顺序 | `ftyp` → `moov` → `mdat` |
| moov 大小 / 偏移 | 524,260 B @ 0 |
| mdat 大小 / 偏移 | 323,380,362 B @ 524,288 |
| **moov 位置** | **在 mdat 之前（faststart 布局）** |
| padding box | 无 `free`/`skip`/`wide` 顶层填充 |

> **与 XAVC 的第一处实质差异（已实测确认）**：两条真实 Sony XAVC 素材
> （`testsets/a7m5_2ch_10bit422/20260921_C1316.MP4` 与
> `testsets/a7m5_4ch_long/20260919_C1305.MP4`）的顶层顺序都是
> **`ftyp → uuid[×N] → free → mdat → moov → meta`**，即 **moov 在 mdat 之后**，
> 且尾部还有一个根级 `meta` box。XF-HEVC 则是 **`ftyp → moov → mdat`**
> （**moov 在 mdat 之前**），**没有根级 `meta` box**。
>
> 这会影响 GPAC / MP4Box 的处理路径、边下边播、以及任何假设
> "moov 在尾部"的修复逻辑。本项目 `preservation/sony.py` 的重建流程依赖
> moov-at-EOF 布局，`preservation/isobmf.py` 的 `insert_uuid_boxes` 更是
> **明确拒绝 faststart 文件**（见该文件顶部 Safety rule）——
> XF-HEVC 恰好就是 faststart。详见 §9.6。

### 2.2 `ftyp`

```
major_brand   = XFVC
minor_version = 1
compatible    = XFVC, mp42, iso2
```

### 2.3 `moov` 子 box 顺序

```
uuid, udta, mvhd, trak ×7, free
```

注意 **`uuid` 排在 `udta` 与 `mvhd` 之前**，即厂商 metadata 在最前。

### 2.4 `mvhd`

| 字段 | 值 |
|---|---|
| version | 0 |
| timescale | **30000** |
| duration | 154154（= 5.138467 s） |
| rate | 1.0 |
| volume | 1.0 |
| next_track_id | **8**（即 7 条轨道，id 1..7） |

---

## 3. 轨道层

### 3.1 七轨总表

| # | track_id | handler | sample entry | timescale | 样本数 | 说明 |
|---|---|---|---|---|---|---|
| 0 | 1 | `vide` | `hvc1` | 30000 | 154 | 视频 |
| 1 | 2 | `soun` | `ipcm` | 48000 | 246646 | 音频 CH1 |
| 2 | 3 | `soun` | `ipcm` | 48000 | 246646 | 音频 CH2 |
| 3 | 4 | `soun` | `ipcm` | 48000 | 246646 | 音频 CH3 |
| 4 | 5 | `soun` | `ipcm` | 48000 | 246646 | 音频 CH4 |
| 5 | 6 | `tmcd` | `tmcd` | 30000 | 154 | 时间码 |
| 6 | 7 | `meta` | `cndm` | 30000 | 154 | Canon 元数据 |

全部 `tkhd.flags = 7`（enabled | in_movie | in_preview）。
全部轨道使用 **`co64`**（64 位块偏移），**不是 `stco`**。

### 3.2 视频轨（track 0）

**采样入口 `hvc1`（VisualSampleEntry，实测字节布局）**

| 字段 | 值 |
|---|---|
| 宽度 × 高度 | 6912 × 4608 |
| depth | 24 |
| frame_count | 1 |
| horizresolution / vertresolution | 72 / 72（16.16 定点 = 0x00480000） |
| compressorname | 全零（未使用） |
| 子 box | `hvcC`(210 B), `colr`(19 B) |

> 注意 `horizresolution=72` 是**默认占位值**，不是真实 DPI；真实几何在
> `hvcC` / SPS 里。任何据此推断显示尺寸的逻辑都会出错。

**时间轴**

| box | 内容 |
|---|---|
| `stts` | 1 条 entry，154 样本，delta 恒 **1001**（全片 CFR） |
| `stss` | **11 个同步样本**，索引 1,16,31,46,61,76,91,106,121,136,151 → **GOP = 15** |
| `ctts` | **103 条 entry**，offset 只有 **0** 与 **3003**（= 3 × 1001） |
| `elst` | media_time = **1001**（1 帧），segment_duration = 154154 |
| `stsc` | 2 条 entry：chunk 1 起 15 样本/chunk，chunk 11 起 4 样本/chunk |
| `co64` | 11 个 chunk |
| `stsz` | 非定长（各帧大小不同） |

**这组数据说明**：`ctts` 存在且 offset 为 3 帧，即存在 B 帧重排序；
`stss` 有值说明是 **LongGOP 而非全 I 帧**；`elst` media_time = 1 帧，
表示首帧有 1 帧的呈现偏移。目录名里的 `OG` 会让人误以为 intra-only —
**实测不是**。

### 3.3 音频轨（track 1–4）

| 字段 | 值 |
|---|---|
| sample entry | `ipcm` |
| data_reference_index | 1 |
| version / revision | 0 / 0 |
| **channel_count** | **1**（每轨单声道） |
| **sample_size** | **24** bit |
| **sample_rate** | **48000** |
| vendor | 0 |
| 子 box | `pcmC`(14 B) |
| `stts` | 1 条 entry，246646 样本，delta 恒 1 |
| `elst` | media_time = 0 |

**`4CH` 的真实含义 = 4 条独立单声道轨**，不是一条四声道流，也不是
2 条双声道流。ffprobe 报告 `pcm_s24be`（big-endian 24-bit），
容器 tag 是 `ipcm`。

### 3.4 `tmcd` 轨（track 5）

| 字段 | 值 |
|---|---|
| sample entry | `tmcd`（payload 26 B） |
| 采样入口尾部 | `00000000 00000003 00007530 000003e9 1e00` |
| → time scale | `0x00007530` = **30000** |
| → frame duration | `0x000003E9` = **1001** |
| **样本大小** | **4 B 定长**（154 样本 × 4 B = 616 B） |
| 样本[0] | `00 03 d0 67` = 249959（大端） |
| 样本序列 | 249959, 249960, 249961, … 逐帧 +1 |
| `elst` | 无 |

**这是 Canon 相对 Sony 的一处明显差异**：Canon 用**标准 `tmcd` 轨**
（每帧 4 字节帧计数）承载时间码，而 Sony 把时间码放在 `rtmd` 轨的
stream tag 上。ffprobe 报的 `timecode=02:19:00;11` 就来自这条轨。

### 3.5 视频轨的三处「非 4K 默认值」

XF-HEVC 是 4:2:2 10-bit，这些值明显偏离常见 4:2:0 编码器默认：

| 位置 | 值 |
|---|---|
| SPS `scaling_list_enabled_flag` | **1**（启用缩放矩阵） |
| SPS `amp_enabled_flag` | **0**（关闭 AMP） |
| SPS `sample_adaptive_offset_enabled_flag` | **0**（关闭 SAO） |
| SPS `sps_temporal_mvp_enabled_flag` | **0**（关闭 TMVP） |
| PPS `weighted_pred_flag` / `weighted_bipred_flag` | 0 / 0 |
| PPS `tiles_enabled_flag` | **1**（2 列 × 1 行，uniform spacing） |
| PPS `entropy_coding_sync_enabled_flag` | 0（即**不用 WPP**） |
| PPS `pps_loop_filter_across_slices_enabled_flag` | 1 |

> **并行化策略差异（已实测两侧）**：XF-HEVC 用 **tiles（2 列 × 1 行）**
> 且 `entropy_coding_sync_enabled_flag = 0`（**不用 WPP**）；
> 实测的真实 XAVC HS 4:2:0 素材则是 **`tiles_enabled_flag = 0` 且
> `entropy_coding_sync_enabled_flag = 0`**（即 tiles 与 WPP 都关，
> 靠 slice 切分）。
>
> 这解释了一个风险点：6912×4608 一把梭超单核吞吐，Canon 选择用 tiles；
> 而 3840×2160 的 A7M5 不需要。**这条差异是分辨率驱动的，不是品牌驱动的**，
> 但如果换用只支持 WPP 或只支持 slice 的硬解实现，两侧会遇到不同问题。
> 本机 NVDEC / QSV 对 XF-HEVC 的 tiles 都能正常解码（见兼容性报告）。

---

## 4. HEVC 位流层

来源：`ffmpeg trace_headers` 位流直读（不是解码推断）。

### 4.1 `hvcC`（HEVCDecoderConfigurationRecord，210 B）

| 字段 | 值 |
|---|---|
| configurationVersion | 1 |
| general_profile_space | 0 |
| **general_tier_flag** | **1（high tier）** |
| **general_profile_idc** | **4（Format Range Extensions / Rext）** |
| general_profile_compatibility_flags | `0x08000000` |
| general_constraint_indicator_flags | `0x9D0800000000` |
| **general_level_idc** | **183**（= 6.1） |
| min_spatial_segmentation_idc | 0 |
| parallelismType | 0 |
| **chromaFormat** | **2（4:2:2）** |
| **bitDepthLumaMinus8 / bitDepthChromaMinus8** | **2 / 2（10 bit）** |
| avgFrameRate | 0（未声明） |
| **constantFrameRate** | **0** |
| numTemporalLayers | 1 |
| temporalIdNested | 1 |
| **lengthSizeMinusOne** | **3（4 字节 NAL 长度前缀）** |
| numOfArrays | 3 |

NAL 数组：`VPS`(type 32, 1 个, 23 B)、`SPS`(type 33, 1 个, 133 B)、
`PPS`(type 34, 1 个, 8 B)。位流中出现的 NAL 类型：
`0, 1, 6, 19, 32, 33, 34`
（1 = TRAIL_R 非 IDR 片，19 = IDR_W_RADL，6 = SEI）。

### 4.2 `colr` box

```
colour_type = nclx
colour_primaries        = 2
transfer_characteristics= 2
matrix_coefficients     = 1
full_range_flag         = true
```

### 4.3 SPS / PPS 关键字段

| 字段 | 值 |
|---|---|
| `vps_max_sub_layers_minus1` | 0（单时间层） |
| `vps_temporal_id_nesting_flag` | 1 |
| `sps_max_sub_layers_minus1` | 0 |
| `chroma_format_idc` | 2（4:2:2） |
| `pic_width_in_luma_samples` | 6912 |
| `pic_height_in_luma_samples` | 4608 |
| `conformance_window_flag` | 0（无裁剪窗） |
| `bit_depth_luma_minus8` | 2 |
| `bit_depth_chroma_minus8` | 2 |
| `log2_max_pic_order_cnt_lsb_minus4` | 4 |
| `log2_min_luma_coding_block_size_minus3` | 0 → min CB = 8 |
| `log2_diff_max_min_luma_coding_block_size` | 2 → **CTU log2 = 5，即 CTU = 32×32** |
| `log2_min_luma_transform_block_size_minus2` | 0 |
| `log2_diff_max_min_luma_transform_block_size` | 2 |
| `max_transform_hierarchy_depth_inter/_intra` | 2 / 2 |
| `num_short_term_ref_pic_sets` | **7** |
| `long_term_ref_pics_present_flag` | 0 |
| `general_progressive_source_flag` | 1 |
| `general_frame_only_constraint_flag` | 1（逐行，无场） |
| `vui_parameters_present_flag` | 1 |
| `video_signal_type_present_flag` | 1 |
| `video_full_range_flag` | **1（full range）** |
| `colour_description_present_flag` | 1 |
| `colour_primaries` / `transfer_characteristics` | 2 / 2 |
| `matrix_coefficients` | 1（bt709） |
| `bitstream_restriction_flag` | 1 |

### 4.4 首帧 slice header

| 字段 | 值 |
|---|---|
| `first_slice_segment_in_pic_flag` | 1 |
| `slice_type` | **2（I slice）** |
| `five_minus_max_num_merge_cand` | 4 |

### 4.5 一处需要留意的**色彩标注不一致**

| 位置 | 取值 | 解读 |
|---|---|---|
| 容器 `colr` | primaries=2, transfer=2, matrix=1, full_range=1 | primaries/transfer = **bt470bg** |
| SPS VUI | 同上 | 同上 |
| ffprobe 报告 | `color_range=pc, color_space=bt709, transfer=(空), primaries=(空)` | 同上 |

**问题**：`colour_primaries=2` 与 `transfer_characteristics=2` 在 H.265 里
都是 **bt470bg（PAL/SECAM）**，只有 `matrix_coefficients=1` 是 bt709。
而 R6 V 是消费级相机，实际应当是 **bt709**（primaries=1, transfer=1）。
这是**相机端的标注问题**，不是本项目造成的。但它意味着：

- 下游任何按 primaries/transfer 做色彩管理的工具会把素材当 bt470bg 处理；
- 本项目 `SOURCE_COLOR` 日志里 primaries/transfer 显示为 `-`
  （因为 `color_primaries`/`color_transfer` 在 ffprobe 输出里为空，
  只有 `color_space=bt709`）。

**建议**（不在本次调研范围内，仅记录）：在 preservation / 转码时
把 primaries/transfer 显式归一到 bt709，并在报告中标注原值。

---

## 5. Canon 私有 metadata 层

XF-HEVC 有**两层**厂商 metadata，XAVC 也恰好有两层，但载体不同。

### 5.1 文件级：moov 级 uuid box

```
uuid box 总数        : 1
GUID                 : {85C0B687-820F-11E0-8111-F4CE462B6A48}
整个 box 大小        : 65,810 B（payload 65,786 B）
上下文               : moov
```

内部是一组 Canon 私有 box（`size(4) + fourcc(4) + payload`）：

| type | 大小 | 内容 |
|---|---|---|
| **`CNCV`** | 38 B | 版本串 **`CanonMP4_002/10.00.03/00.00.00`** |
| **`CNTH`** | 65,552 B | 容器级 metadata，**内含 JPEG EXIF 缩略图**（28,745 B） |
| **`CNOP`** | 196 B | 操作/选项表，内含嵌套 fourcc（`CNLG` 等） |
| `CNDA` / `CNCM` / `CNID` | — | 相机数据 / 型号 / 素材标识（出现文件名 `A_0013C699`） |

EXIF 内可读串：

```
Exif
Canon
Canon EOS R6 V
2026:05:10 00:33:37
MVI:Canon EOS R6 V
```

两条素材的 GUID 相同、`CNCV` 版本串相同（同机同固件）。

### 5.2 帧级：`cndm` 定时 metadata 轨

| 项 | 值 |
|---|---|
| handler_type | `meta` |
| sample entry | `cndm`（payload 仅 8 B：`00000000 00000001`） |
| **样本大小** | **1064 B 定长**，154 帧全是 1064 |
| 总载荷 | 154 × 1064 = **163,856 B** |
| `tref` | **`cdsc` → track_id 1（视频轨）** |
| `elst` | media_time = 0，segment_duration = 30030000 |
| `stts` | delta 恒 1001（与视频同拍） |
| `co64` | 11 chunk，首块偏移 524,404 |

**载荷是 KLV 编码**（SMPTE 键长值），不是裸二进制：

```
样本[0] 头部：
01000000 0c000000 0001b50b 67d00300 00000000 02000000
0c040000 060e2b34 02530101 0c020101 01010000 83000018
80000002 d0008001 0002d7d5 80050002 d0188006 00020064
06 0e 2b 34 02 53 01 01   ← SMPTE Universal Label 前缀
83 xx xx                  ← BER 长度编码
```

逐帧变化：**只有 177 个字节不同**（偏移 12 与 625–1063）。
- **偏移 12**：`67 → 68 → 69 → …` 逐帧 +1 的帧计数器
- **偏移 625 起**：逐帧变化的数据区（推测是 IMU / 镜头 / 对焦状态）

**与 Sony `rtmd` 的对照**：

| 项 | XF-HEVC `cndm` | XAVC `rtmd` |
|---|---|---|
| handler | `meta` | `meta` |
| tref | `cdsc` → 视频 | `cdsc` → 视频 |
| 定长样本 | **1064 B** | **19456 B** |
| 载荷格式 | **KLV（SMPTE 键长值）** | Sony 私有二进制 |
| 逐帧增量 | 帧计数 + 数据区 | 帧计数 + IMU/镜头 |

**结构完全同构，名称/大小/载荷格式完全不同。**

---

## 6. 与 XAVC 逐项对照（**实测**，非引用文档）

> **本节已升级为实测对照。** 本仓库 `testsets/` 里就有真实 Sony XAVC 素材，
> 因此我把同一套探针跑在三条素材上（同项目、同脚本、同口径）：
>
> | 列 | 素材 | 规格 |
> |---|---|---|
> | A | `F:\CANON_R6V_…\A_0013C699…MP4` | XF-HEVC，HEVC **Rext 4:2:2 10-bit** |
> | B | `testsets/a7m5_2ch_10bit422/20260921_C1316.MP4` | XAVC，HEVC **Rext 4:2:2 10-bit** |
> | C | `testsets/a7m5_4ch_long/20260919_C1305.MP4` | XAVC，HEVC **Main10 4:2:0 10-bit** |
>
> 机读产物：`three-way-measured.json`

### 6.1 实测对照表

| 项 | A: XF-HEVC | B: XAVC 4:2:2 | C: XAVC HS 4:2:0 | 判定 |
|---|---|---|---|---|
| `ftyp` major | **`XFVC`** | `XAVC` | `XAVC` | **品牌** |
| `ftyp` compat | `XFVC,mp42,iso2` | `XAVC,mp42,nras,iso6` | `XAVC,mp42,nras,iso6` | **品牌** |
| 顶层 box 顺序 | `ftyp,moov,mdat` | `ftyp,uuid,free,mdat,moov,meta` | `ftyp,uuid,uuid,free,mdat,moov,meta` | **结构** |
| **moov 位置** | **mdat 之前** | mdat 之后 | mdat 之后 | **结构** |
| moov 大小 | 524,260 | 10,280 | 3,711,534 | — |
| 根级 uuid 数 | **1** | 5 | **9** | **结构** |
| uuid label | `{85C0B687-…}` | `PROF`×1, `USMT`×4 | `PROF`, `USMT`, `{BE7ACFCB-…}` | **品牌** |
| 轨道数 | **7** | 3 | 6 | **结构** |
| `mvhd` timescale | **30000** | 60000 | 60000 | **结构** |
| 视频采样入口 | `hvc1` | `hvc1` | `hvc1` | ✅ **同** |
| **块偏移 box** | `co64` | **`stco`** | **`co64`** | ⚠️ **非品牌属性** |
| `stts` delta | 1001 | 1001 | 1001 | ✅ **同** |
| `stss` 同步样本数 | 11 | 11 | 4,803 | — |
| GOP 长度 | **15** | 60 | 60 | 编码器参数 |
| `ctts` 存在 | 是（103 entry） | 是（660） | 是（288150） | ✅ **同** |
| `elst` media_time | **1001** | 2002 | 2002 | 编码器参数 |
| 定时 metadata 轨 | **`cndm`** | `rtmd` | `rtmd` | **品牌** |
| metadata handler | `meta` | `meta` | `meta` | ✅ **同** |
| metadata `tref` | `cdsc`→视频 | `cdsc`→视频 | `cdsc`→视频 | ✅ **同** |
| **`tmcd` 轨** | **有**（4 B/帧） | 无 | 无 | **品牌** |
| 音频轨数 | **4** | 1 | **4** | 视机型 |
| 音频 sample entry | **`ipcm`** | `twos` | **`ipcm`** | 视机型 |
| 音频声道 | **1（mono）** | 2 | **1（mono）** | 视机型 |
| `hvcC` 大小 | **210** | 467 | 669 | **品牌** |
| `colr` box | **有**（nclx） | 无 | 无 | **品牌** |

### 6.2 对照推翻了三条此前的推断

**① `co64` vs `stco` 不是品牌属性。**
两条 XAVC 素材一条用 `stco`（B）、一条用 `co64`（C）；XF-HEVC 用 `co64`。
所以这是**按素材/编码器配置**变化的，不能用作品牌或格式判据。
（原报告把此项列为"待补测"，现已解决。）

**② `4 × mono` / `ipcm` 不是 Canon 特征。**
C（Sony XAVC HS 4ch）也是 **4 条单声道 + `ipcm`**，与 XF-HEVC 完全相同。
所以"4CH = 4×mono"是**这批素材的音频配置**，不是 XF-HEVC 的格式特征。
`twos`（B）是 2 声道 16-bit，属另一种机型配置。

**③ `colr` box 的存在是 XF-HEVC 侧的差异。**
只有 A（XF-HEVC）带 `colr`/`nclx`；两条 XAVC 素材都没有 `colr` box。
这也解释了为什么 Canon 素材能给出 primaries/transfer（尽管取值有问题，
见 §4.5），而 XAVC 素材在这方面信息更少。

### 6.3 真正的品牌/格式判据（按可靠性排序）

| 判据 | XF-HEVC | XAVC |
|---|---|---|
| `ftyp` major_brand | `XFVC` | `XAVC` |
| 定时 metadata 轨 sample entry | `cndm` | `rtmd` |
| 是否存在 `tmcd` 轨 | **有** | 无 |
| 根级 uuid label/GUID | `{85C0B687-…}` | `PROF` / `USMT` |
| 是否有根级 `meta` box | **无** | **有**（两条都有，10 KB 量级） |
| moov 位置 | mdat 之前 | mdat 之后 |
| 音频 sample entry | `ipcm`（视机型） | `ipcm` 或 `twos`（视机型） |

> ⚠️ **不可用**作判据的：`co64`/`stco`、音频声道数、`stts` delta、
> `ctts` 存在性、metadata `handler`/`tref`、视频采样入口（`hvc1`）——
> 这些在两侧或两侧内部都一致或随配置变化。

### 6.4 结构范式对比

| 结构要素 | XF-HEVC | XAVC |
|---|---|---|
| 厂商私有 brand | ✅ | ✅ |
| 根级厂商私有 uuid box | ✅ 1 个 | ✅ 5–9 个 |
| 根级 `meta` box（文件级 metadata） | ❌（改用 uuid 内含 `CN*`） | ✅ |
| 定时 metadata 轨（`meta` handler + `cdsc`） | ✅ `cndm` | ✅ `rtmd` |
| LongGOP + `ctts` + `elst` | ✅ | ✅ |
| 独立 `tmcd` 时间码轨 | ✅ | ❌（时间码在 rtmd tag） |
| 独立音频轨 × N | ✅ 4×mono | ✅ 1–4 |

**结论重申**：两者的**结构范式相同**（都是厂商 ISOBMFF 方言，都有
厂商 brand / uuid / 定时 metadata 轨 / open-GOP 时间轴），
**具体标识、metadata 载体与载荷不同**（brand、uuid GUID、`cndm` vs `rtmd`、
`tmcd` 轨的有无、moov 位置、根级 `meta` box 的有无）。

---

## 7. 为什么 rigaya 缺陷对两者同源（**已实测钉死**）

第 6 节里有两行值得单独强调。我把三条素材的**包序**都实测了一遍：

| 素材 | 首包（解码序 idx 0） | 首 DTS | 首个同步包之前呈现的图像 |
|---|---|---|---|
| XF-HEVC（Canon R6 V） | pts=0.066733 **KEY** | **−0.033367** | **2** |
| XAVC 4:2:2（A7M5） | pts=0.05005 **KEY** | **−0.033367** | **3** |
| XAVC HS 4:2:0（A7M5） | pts=0.05005 **KEY** | **−0.033367** | **3** |

**三条素材的首 DTS 完全相同，都是 −0.033367**（= −1001/30000）。
且三条的首个同步样本**都不是**呈现序的第一帧 —— 也就是说
**首个同步包之前都存在前导图像**。

这说明「负 DTS + 前导图像」**不是某个品牌或某个编码器的特性，
而是这类相机 open-GOP 时间轴的通例**。

而 rigaya 的缺陷（项目文档 `docs/hardware-decode/xavc-hs-frame-count.md`
RC-1）正是：

> 把「第一个喂给解码器的 packet 的 PTS」当成「应当呈现的第一帧的 PTS」，
> 于是丢掉 presentation order 上排在第一个 keyframe 之前的那些 picture。

**缺陷命中判定的是流形态，不是品牌。** 项目自己的文档也把结论写成
"预测式是流形态而非 codec/容器属性"。本次调研用两条实测把它确认到了
Canon XF-HEVC 上：

| 前端 | reader | A0013C699 | A0013C700 |
|---|---|---|---|
| NVEncC `--avhw` | `avcuvid` | 152（容器 154） | 684（容器 686） |
| QSVEncC `--avhw` | `avqsv` | 152（容器 154） | 684（容器 686） |
| 各自 `--avsw` | `avsw` | 154 ✅ | 686 ✅ |

丢弃量 **2 = 前导图像数 2**，与项目公式
`丢弃量 = 首个同步样本之前呈现的图像数` 吻合。

---

## 8. 产物索引与复现

### 产物（本目录）

| 文件 | 内容 |
|---|---|
| `README.md` | 本报告 |
| `container-layout.json` | 顶层布局、`ftyp`、`mvhd`、完整 box 树 |
| `track-structure.json` | 7 轨的 `tkhd`/`mdhd`/`elst`/`tref`/采样表/采样入口 |
| `hevc-bitstream.json` | `hvcC`、`colr`、SPS/PPS/VPS 全字段、首 slice |
| `canon-private-boxes.json` | uuid box 与 `CN*` box 清单、`cndm` 结构 |
| **`cndm.md`** + **`cndm-structure.json`** | **`cndm` 轨结构专项**（KLV 框架、6 个 Local Set、逐帧差异规律、语义未解出的原因） |
| **`sidecar-metadata.md`** + `cpf-structure.json` + `xavc-deep.json` | **外挂元数据与容器内 metadata 体系对照**（Canon CPF 拆解、Sony NRTM XML、XAVC 根级 `meta` box / XMP / 镜头档案 KLV） |
| **`three-way-measured.json`** | **XF-HEVC vs 两条真实 XAVC 的实测对照**（同探针同口径） |
| `xavc-comparison.json` | 早期版本：XAVC 侧取自项目文档记录（保留作交叉参考） |

### 复现

```powershell
cd F:\1KeyTranscoder

# 1. 深度结构探测（生成 work/canon_r6v/xfhevc-structure.json）
python work\canon_r6v\xfhevc_structure.py

# 2. XAVC 侧同口径探测（4:2:2 全量；4:2:0 用轻量版避开 trace_headers）
python work\canon_r6v\xfhevc_structure.py `
  --src "testsets\a7m5_2ch_10bit422\20260921_C1316.MP4" --out struct_xavc_422.json
python work\canon_r6v\light_struct.py `
  "testsets\a7m5_4ch_long\20260919_C1305.MP4" struct_xavc_hs420.json

# 3. 生成报告产物到本目录
python work\canon_r6v\make_xfhevc_report.py
python work\canon_r6v\make_three_way.py
```

诊断脚本（定位字节偏移用，保留作证据）：
`work/canon_r6v/_diag_entry.py`（VisualSampleEntry 偏移）、
`work/canon_r6v/_diag_stsd.py`（`stsd` FullBox 偏移）、
`work/canon_r6v/_diag_uuid_cndm.py`（uuid / cndm 结构）。

### 解析过程中踩到的三个坑（已修正，记录以免重犯）

1. **`stsd` 不能用 `_children(fullbox=True)`** —— 那只跳 4 字节，
   会落到 `entry_count` 上并返回空列表。entry 从 `data_offset + 8` 起。
2. **`VisualSampleEntry` 字段偏移**（实测，非按文档）：
   `width@24 height@26 hres@28 vres@32 frame_count@40
   compressorname@42 depth@74 pre_defined@76`，**子 box 从 @78 起**。
3. **全部 7 轨用 `co64` 而非 `stco`** —— 只读 `stco` 会拿不到块偏移，
   数据轨样本读不出来。

---

## 9. 已知限制

1. **XAVC 侧已改为实测对照**（见 §6），不再是引用文档。用的素材是
   本仓库 `testsets/` 里的真实 Sony 素材，同一套探针、同一口径。
   原始引用（`preservation/isobmf.py`、`models.py`、`core/batch_hw.py`、
   `docs/hardware-decode/xavc-hs-frame-count.md`）保留在
   `xavc-comparison.json` 里作为交叉参考。
2. **`cndm` 载荷框架已解、语义未解。** 见专项报告 **`cndm.md`**：
   KLV 框架（16B SMPTE UL `060e2b3402530101…` + BER 长度）、
   首样本 6 个 Local Set、逐帧仅 177/1064 字节变化且**只落在 Local Set[3] 内**、
   偏移 12 是相机侧计数器（A699 起始 26576 / A700 起始 465，非剪辑内帧号）——
   这些都是实测。但**值内部的字段语义没解出**：按 SMPTE KLV 三元组解析不成立
   （标签实测为 `80 00`/`80 01` 形式，而标准要求 `08 00`/`08 01`），
   `cndm.md` §4 写明了检验过程与失败原因，没有臆测字段含义。
3. **`CNTH` / `CNOP` / `CNDA` / `CNCM` / `CNID` 只做了清单级解析**
   （名字、大小、内嵌 fourcc），内部语义未解析。
4. **色彩标注不一致（primaries/transfer = bt470bg）已发现但未处理** ——
   见 §4.5。
5. **只解析了 A0013C699 的完整结构。** A0013C700 只取了 `CNCV` 做比对
   （版本串与 GUID 相同）；未逐项核对两者的 `cndm`/`CNTH` 是否完全同构。
6. **未验证 moov-before-mdat 对项目现有代码的影响。**
   `preservation/isobmf.py` 的 `insert_uuid_boxes` 明确"拒绝 faststart 文件"
   （该文件顶部 Safety rule：insertions 必须在 mdat 之后，
   否则 stco/co64 偏移失效）。**XF-HEVC 恰好就是 faststart** ——
   如果将来要给它做 preservation，这条会直接撞上。本次**未做**该项验证。
7. **XAVC HS 4:2:0 那条（C）只做了轻量探测**（容器/轨道层），
   未跑 `trace_headers`（51011 MB 文件上极慢，超时）。因此
   C 列的 SPS/PPS 字段与 `hvcC` 内容未取，`hvcC_size` 来自 box 长度。
8. **XAVC 的并行化策略已补测**（原"待补测"项已解决）：
   A7M5 的 XAVC HS 4:2:0 实测 `tiles_enabled_flag=0` 且
   `entropy_coding_sync_enabled_flag=0`，与 XF-HEVC 的 tiles 2×1 不同。
   注意该结论只对 4:2:0 那条实测；4:2:2 那条（192 MB）未取 SPS/PPS，
   但同为 A7M5 机型，预期一致 —— 未验证。
