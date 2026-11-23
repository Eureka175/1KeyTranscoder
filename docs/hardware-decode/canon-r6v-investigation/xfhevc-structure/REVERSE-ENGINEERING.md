# 逆向记录：开源实现对照与字节级解读

**方法**：以现有开源实现（主要是 **ExifTool**）的解析表为参照，反向解读
XF-HEVC 与 XAVC 的未文档化结构。**只做静态分析 —— 不跑任何实际编码测试。**

**参照源码**（已下载到 `work/canon_r6v/refsrc/`，仅作参考，不入库）：

| 文件 | 用途 |
|---|---|
| `Canon.pm` | Canon `uuid`/`CNTH`/`CNOP` 表 |
| `QuickTime.pm` | `uuid` GUID 分派表、`Profile`/`UserMedia`/`MetaData` 表 |
| `Sony.pm` | `rtmd` 定时 metadata 标签表（**关键参考**） |
| `MXF.pm` | SMPTE UL 字典 |

---

## 1. 最重要的突破：Canon `cndm` 与 Sony `rtmd` 是**同一族的 KLV Local Set**

### 1.1 参照表（`Sony.pm` 的 `%Image::ExifTool::Sony::rtmd`）

```perl
# 0x060e - 16 bytes starting with 0x060e2b340253   ← "fake tag ID - comes before 0x8300 container"
0x060e => { Name => 'Sony_rtmd_0x060e' },
# 0x32xx - 16 bytes starting with 0x060e2b340401
0x3210, 0x3219, 0x321a => { ... int8u },
0x8000 => { Name => 'FNumber', Format => 'int16u',
            ValueConv => '2 ** (8-$val/8192)' },
0x8001 => { Name => 'Sony_rtmd_0x8001' }, # 可能对焦距离
0x8004 => { ... }, # FocalLength35efl?
0x8005 => { ... }, # FocalLength?
0x800a => { ... }, # FocusRingPosition?
0x800b => { ... }, # ZoomRingPosition?
# 0x8100 - 16 bytes starting with 0x060e2b340401
0x8106 => { Name => 'FrameRate', Format => 'rational64u' },
0x8109 => { Name => 'ExposureTime', Format => 'rational64u' },
0x810a => { Name => 'MasterGainAdjustment', Format => 'int16u' },
0x810b => { Name => 'ISO', Format => 'int16u' },
0x8114 => { Name => 'SerialNumber', Format => 'string' }, # 含机型
0x8500.. => GPS 系列
```

### 1.2 与 Canon `cndm` 的字节级对照

Canon `cndm` Local Set[0] 的四个三元组（手工核对过的字节序列）：

| tag | 长度 | 原始值 | ExifTool 表名 | 按表换算 |
|---|---|---|---|---|
| `0x8000` | 2 | `d0 00` | **FNumber** | `2**(8 − 53248/8192)` = **2.8284** |
| `0x8001` | 2 | `d7 d5` | 0x8001（疑对焦距离） | 55253 |
| `0x8005` | 2 | `d0 18` | 0x8005（疑焦距） | 53272 |
| `0x8006` | 2 | `00 64` | 表中未列出 | 100 |

**验证**：`0x8000` 按 ExifTool 的 Sony 公式解出 **f/2.8284 ≈ f/2.8**，
与该文件的 EXIF 记录的镜头 **RF24-70mm F2.8 L IS USM** 一致。

**同时**：Canon `cndm` 里出现的 UL `060e2b34040101 0b05 100101`
与 ExifTool 注释的 `0x060e2b340401` 前缀族相同。

**结论**：**Canon XF-HEVC 的 `cndm` 与 Sony 的 `rtmd` 使用同一套
SMPTE KLV Local Set 框架与同一批标签编号**（`0x8000` 族 = 光学参数，
`0x8100` 族 = 曝光/感光度）。差异在于：
- Sony 把 UL 拆成 `0x060e` / `0x32xx` / `0x8100` 段
- Canon 在一个 1064 B 定长样本里放 Canon 私有的 28 B 头 + 6 个 Local Set

> ⚠️ **但 Canon 的 Local Set[0] 值内部**按标准 SMPTE 三元组解析**不成立**
> （标签实测为 `80 00`/`80 01`，标准要求 2 字节大端标签序列化为 `08 00`/`08 01`）。
> 即：**框架同族、标签编号同族，但 Canon 的值内部布局与标准三元组不同**。
> 这一层仍未解出。

---

## 2. ExifTool 已确认的 Canon 结构

### 2.1 `uuid` GUID 分派表（`QuickTime.pm`）

| 条件（GUID 前缀） | ExifTool 命名 | 子表 |
|---|---|---|
| `\xbe\x7a\xcf\xcb\x97\xa9\x42\xe8\x9c\x71\x99\x94\x91\xe3\xaf\xac` | **`XMP`** | `XMP::Main`（Start 16） |
| `PROF!\xd2\x4f\xce\xbb\x88\x69\x5c\xfa\xc9\xc7\x40` | `UUID-PROF` | `QuickTime::Profile`（Start 24） |
| `USMT!\xd2\x4f\xce\xbb\x88\x69\x5c\xfa\xc9\xc7\x40` | `UUID-USMT` | `QuickTime::UserMedia`（Start 16） |
| `\x85\xc0\xb6\x87\x82\x0f\x11\xe0\x81\x11\xf4\xce\x46\x2b\x6a\x48` | **`UUID-Canon`** | `Canon::uuid`（Start 16） |
| `!\x0f\x16\x87\x91\x49\x11\xe4\x81\x11\x00\x24\x21\x31\xfc\xe4` | `UUID-Canon2` | `Canon::uuid2`（CR3 / DPP4） |

**逐条印证本次实测**：
- A 的 moov 级 uuid `{85C0B687-…}` = **`UUID-Canon`** ✅（GUID 完全一致）
- C 的 `{BE7ACFCB-…}` = **XMP** ✅（这正是 ExifTool 写 MP4 XMP 的官方位置）
- B/C 的 `{50524F46-…}` = `UUID-PROF`、`{55534D54-…}` = `UUID-USMT` ✅

**附带发现**：ExifTool 的 `Start` 值与我的实现一致
（`Start => 16` = 相对 box 起点，即 `whole[24:]`）。

### 2.2 `Canon::uuid` 表 —— **Canon uuid box 是静态图像 metadata，不是视频 metadata**

```perl
CNCV => { Name => 'CompressorVersion',
          RawConv => '$self->OverrideFileType($1) if $val =~ /^Canon(\w{3})/i' },
CNDM => { },   # 4 bytes - 0xff,0xd8,0xff,0xd9
CNTH => { Name => 'CanonCNTH', SubDirectory => 'Canon::CNTH' },
CNOP => { Name => 'CanonCNOP', SubDirectory => 'Canon::CNOP' },

%Image::ExifTool::Canon::CNTH = (
    VARS => { ATOM_COUNT => 1 },    # only one contained atom
    CNDA => { Name => 'ThumbnailImage',
              Notes => 'the full THM image, embedded metadata is extracted
                        as the first sub-document' },
);
%Image::ExifTool::Canon::CNOP = ( ... 注释:
    # CNFB - 52 bytes (7DmkII,M50,C200)
    # CNMI - 4 bytes: "0x20000001" (C200)
    # CNCM - 48 bytes: original file name in bytes 24-31 (C200)
);
```

**关键解读**：

1. **`CNTH` 内含 `CNDA` = 「完整的 THM 图像」** —— 也就是**相机静态图像的
   缩略图 + 内嵌 EXIF**，不是视频 metadata。这**解释了本次实测的两个现象**：
   - CNTH 里能找到完整 JPEG（28,745 B）+ EXIF（机型/镜头/电池）
   - 两条不同片段的 uuid box 内容相同（因为是同一天的静态图像 metadata）
2. **`CNCV` 的 `RawConv` 说明 Canon 用 `CanonXXX` 前缀区分 RAW 类型**
   （CR3 / CRM）—— 我们的值是 `CanonMP4_002/…`，即 **MP4 类型**
3. **`CNDM` 在 ExifTool 里只有 4 字节**（`ff d8 ff d9`，一个空 JPEG 标记）。
   **R6 V 的 `cndm` 是 1064 B/帧的 KLV 轨 —— ExifTool 的表已过时**，
   说明 Canon 在更新的机型上换成了 KLV 实现（本次首次记录）
4. **`CNOP` 的注释给出了我们实测到的 `CNFB` / `CNCM` 的用途线索**：
   `CNFB` 52 B、`CNCM` 48 B（原文件名在 byte 24-31）。
   我们实测 CNOP 188 B 里有 `CNLG`/`CNLC`/`CNFB`/`CNCM`/`CNID`，
   与 ExifTool 的清单部分吻合

---

## 3. 用 ExifTool 表解出的 XAVC `PROF` / `USMT`

### 3.1 `PROF` = `QuickTime::Profile`（文件全局配置）

**实测布局（带偏移断言走通，159 B 完整消费）**：

```
PROF body[0:8]    固定头 00000000 00000004
PROF body[8:28]   FPRF  (20 B)
PROF body[28:80]  VPRF  (52 B)
PROF body[80:124] APRF  (44 B)
PROF body[124:159] PREX (35 B，原子声明长度 0x35=53 与实际不符，疑为打包特例)
```

**XAVC 4:2:2（B）**

| 原子 | 字段 | 值 |
|---|---|---|
| `FPRF` | FileProfileVersion | 0 |
| | **FileFunctionFlags** | **`0x20000000`** → 按 ExifTool 位表 = **Additional tracks**（bit29） |
| `VPRF` | VideoProfileVersion | 0 |
| | **codec** | **`hvc1`** |
| | frame_count_or_id | **9369** |
| | v3 | 2 |
| | **scale_a / scale_b** | **100000 / 100000** |
| | fps_num / fps_den | 3928230 / 3928230 |
| `APRF` | AudioProfileVersion | 0 |
| | v1 | `0x00000002` |
| | **codec** | **`twos`**（16-bit big-endian PCM） |
| | u32be_3 / u32be_4 | 0 / 0 |
| | **samples** | **1536** |
| | **sample_rate** | **48000** |
| `PREX` | （ExifTool 注：Sony ILCE-7M5 写的，疑似另一个视频 profile） | `VPRF` + `hvc1` |

**XAVC HS 4:2:0（C）**

| 原子 | 字段 | 值 | 与 B 的差异 |
|---|---|---|---|
| `FPRF` | FileFunctionFlags | `0x20000000`（Additional tracks） | 同 |
| `VPRF` | codec | `hvc1` | 同 |
| | frame_count_or_id | **8857** | 不同 |
| | **scale_a / scale_b** | **75000 / 75000** | **不同（B 是 100000）** |
| | fps_num / fps_den | 3928230 / 3928230 | 同 |
| `APRF` | **codec** | **`ipcm`**（24-bit PCM） | **不同（B 是 twos）** |
| | **u32be_3** | **24** | 不同（B 是 0）—— 疑为位深 |
| | samples | **1152** | 不同（B 是 1536） |
| | sample_rate | 48000 | 同 |

**解读**：
- `FPRF.FileFunctionFlags = 0x20000000` 两条都是 **Additional tracks**。
  ExifTool 位表另有 bit30 = `Edited`，本例**未置位** ——
  与 §4.1 的 XMP 发现（C 被 Premiere 保存过）**不矛盾但也不互证**：
  `Edited` 指的是"主音视频轨被编辑"，而 Premiere 的保存可能只改写了 metadata。
  **修正**：先前版本报告写"FPRF flags 含 Edited"是**错的**，实测只有 bit29。
- `VPRF.scale_a/b` 从 100000 变到 75000，两条素材唯一的 profile 差异之一，
  疑似质量/码率档位参数（**未证实**）
- `APRF.codec` 的 `twos` vs `ipcm`，与 ffprobe 报的音频 sample entry 完全一致，
  **互相印证**

### 3.2 `USMT` = `QuickTime::UserMedia` → `MTDT`

**实测布局（带断言走通，28 B 完整消费）**：

```
USMT body[0:4]    0000001c (28)
USMT body[4:8]    'MTDT'
USMT body[8:28]   20 B 内容
```

按 4 字节对齐的原始读数：

| 上下文 | `u32be` 序列 | 末个 u32 |
|---|---|---|
| `trak:vide` | `00010012 0000000A 55C40000 00000001 00000000` | **1** = track ID |
| `trak:soun` | 同上（与视频轨**逐字节相同**） | 1 |
| `trak:meta` | `00010012 0000000A 55C40000 00000010 00000000` | **16** = track ID |
| `moov` | `0001000c 0000000b d5c40000 000001e0` | **480** |

可确认的字节事实：
- 首 8 字节 = 标准 atom 头：`0000001c`（28 B）+ **`MTDT`**
- `0001 0012`：高 16 位 `0001`、低 16 位 `0012`（两条素材、四个上下文都相同）
- `0000000A` / `0000000B`：逐上下文递增的小整数
- `55c40000` / `d5c40000`：常量
- **末 4 字节 = track ID**（video=1、meta=16、moov=480）

> **未解出**：`MTDT` 内部各字段的语义。ExifTool 的 `UserMedia` 表只声明
> `MTDT => MetaData`，而 `MetaData` 表的标签是 `0x01 Title / 0x03
> ProductionDate / 0x04 Software / 0x05 Product`（**tag-byte 索引**），
> 与实测的定长 20 B 布局不符 —— 说明这里的 `MTDT` **不是** ExifTool 记录的那一种。
> 本报告只列字节事实，**不臆测语义**。

---

## 4. 参照源码给出的两条交叉印证

### 4.1 XMP 是 ExifTool 写 MP4 XMP 的官方位置

`QuickTime.pm` 里那句注释：

```perl
# *** this is where ExifTool writes XMP in MP4 videos (as per XMP spec) ***
```

证实：`{BE7ACFCB-97A9-42E8-9C71-999491E3AFAC}` 是 **XMP 规范定义的
MP4 承载位置**，不是 Sony 或 Adobe 私有的。

配合实测的 `xmpMM:History`（Adobe Premiere Pro 2025 两次保存），
**C 素材被 NLE 处理过**这一判定有双重独立证据。

> ⚠️ **一处更正**：先前版本报告据 `PROF` 的 `FileFunctionFlags` 说
> "两条 A7M5 素材都含 `Edited`"，**那是错的** ——
> 实测值是 `0x20000000`，只置位 bit29（Additional tracks），
> **bit30（Edited）未置位**。第三方的 `Edited` 证据只有 XMP 那一条。

### 4.2 SMPTE UL 字典（`MXF.pm`）印证 KLV 归属

```
'060e2b34.0401.0101.01030201.01000000' => 'SMPTE 12M Timecode Track'
'060e2b34.0401.0101.01030202.01000000' => 'Picture Essence Track'
```

即 `060e2b34.0401.0101…` 是 MXF 的轨道类型 UL。Canon/Sony 用的
`060e2b34 04 01 01 0b…` 同属 SMPTE 13.1 组，但后缀是厂商自定义。

---

## 5. 逆向结论汇总

| 项 | 状态 | 依据 |
|---|---|---|
| Canon uuid `{85C0B687-…}` = `UUID-Canon` | ✅ **确认** | ExifTool GUID 表逐字节一致 |
| `{BE7ACFCB-…}` = XMP | ✅ **确认** | ExifTool GUID 表 + XMP 规范注释 |
| `CNTH` 内含 `CNDA` = THM 静态图像 | ✅ **确认** | ExifTool `Canon::CNTH` 表 |
| Canon uuid box 是静态图像 metadata（非视频） | ✅ **推断成立** | 上一条 + 两片段内容相同 |
| `CNCV` 的 `CanonMP4_` 前缀 = MP4 类型 | ✅ **确认** | ExifTool `RawConv` 说明 |
| Canon `cndm` 与 Sony `rtmd` 同 KLV 族 | ✅ **确认** | UL `060e2b340401` 族 + 标签编号同族 |
| `0x8000` = FNumber，值 = f/2.8284 | ✅ **确认** | ExifTool 公式 + 与 EXIF 镜头型号吻合 |
| **XAVC `PROF` 原子布局（FPRF/VPRF/APRF/PREX）** | ✅ **解出** | 带偏移断言完整消费 159 B |
| **`PROF` 字段级取值** | ✅ **解出** | codec / frame_count / scale / samples / sample_rate 等 |
| 两条 A7M5 素材 `FileFunctionFlags = 0x20000000`（仅 Additional tracks） | ✅ **确认** | 实测；**先前写"含 Edited"是错的，已更正** |
| ExifTool 的 `cndm` 表（4 B）已过时 | ✅ **确认** | 实测 1064 B/帧 |
| `cndm` 值内部字段语义 | ❌ **未解出** | 值内不符合标准 SMPTE 三元组 |
| `USMT` / `MTDT` 内部字段语义 | ❌ **未解出** | ExifTool 的 `MTDT` 表与本例布局不符 |
| `PREX` 原子内容 | ❌ **未解出** | 内含一个 VPRF，语义未知 |
| `VPRF.scale_a/b` 的含义 | ❌ **未解出** | 100000 vs 75000，疑质量档位 |
| CPF 官方语义 | ❌ **未解出** | ExifTool 无 CPF 支持 |

---

## 6. 可继续的逆向方向

1. **Gyroflow 的 Sony 镜头档案解析器** —— GNU/Linux 开源项目，
   若它解析 `nrtm` 的 `idat` KLV（7,871 B 那份），可直接对照；
   本次未及下载（`LPIF` 在 ExifTool 中无任何实现）。
2. **ExifTool 的 `rtmd` 解析器源码**（`Sony.pm` 的 `sub Process_rtmd`，L11609）
   —— 可以看清它如何切 `0x060e`/`0x32xx`/`0x8100` 段，用于反推 Canon 的
   28 B 私有头。
3. **`libavformat/mov.c`**（FFmpeg）—— 看它如何认 `uuid` 与 `CN*`，
   可作为第二套独立参照。
4. **SMPTE ST 336 / RP 210** 的 UL 注册表 —— 用于给
   `060e2b3402530101…` 后缀定身份（本次只用到 `MXF.pm` 里的节选）。

---

## 7. 复现

```powershell
cd F:\1KeyTranscoder

# 参照源码（已下载，仅作参考，不入库）
ls work\canon_r6v\refsrc

# 用 ExifTool 的表解 XAVC uuid
python work\canon_r6v\xavc_uuid_decode.py

# 原始载荷字节（不解释，只列事实）
#   见本文 §3.2 的 hex 表，命令在会话中
```
