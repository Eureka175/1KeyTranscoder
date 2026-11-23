# XF-HEVC / XAVC 编码结构与元数据体系总览

**一句话**：XF-HEVC 与 XAVC 是**同一结构范式下的两个厂商方言** ——
容器布局、轨道组织、KLV 框架都同源；品牌标识、metadata 载荷与摆放位置各不相同。

**性质**：只读调研。未修改任何生产代码、策略、默认值、`VERSION` 或 tag。

---

## 0. 文档地图

| 文档 | 内容 | 适合谁看 |
|---|---|---|
| **本文** | **总览与结论索引** | 先看这个 |
| **`DIFF-xfvc-vs-xavc.md`** | **XFVC ↔ XAVC 系统化 diff**（顶层布局 / box 树 / 轨道 / 采样表 / metadata 载体） | 想知道两者到底差在哪 |
| **`REVERSE-ENGINEERING.md`** | **开源实现（ExifTool）对照逆向：KLV 标签族、uuid 分派、PROF 字段级解码** | 想知道未文档化结构怎么解出来的 |
| [`README.md`](README.md) | XF-HEVC 四层结构详解 + 与 XAVC 逐项对照 | 想深入 XF-HEVC 细节 |
| [`cndm.md`](cndm.md) | Canon `cndm` 轨 KLV 专项 | 关心 Canon 逐帧 metadata |
| [`sidecar-metadata.md`](sidecar-metadata.md) | 外挂文件（CPF / NRTM XML）+ XAVC 容器 metadata | 关心 sidecar 与 nrtm |
| `three-way-measured.json` | XF-HEVC vs 两条真实 XAVC 的机读对照 | 要数据不要散文 |

> 兼容性/解码路径的调研在上一级目录
> `../README.md`、`../test-matrix.md`（那份是「能不能转」，本目录是「是什么」）。

---

## 1. 素材与对照源

| 代号 | 素材 | 规格 | 用途 |
|---|---|---|---|
| **A** | `F:\CANON_R6V_…\A_0013C699…MP4` | XF-HEVC，HEVC **Rext 4:2:2 10-bit**，6912×4608，154 帧 | 主研究对象 |
| **A′** | `A_0013C700…MP4` | 同上，686 帧 | 交叉验证 |
| **B** | `testsets/a7m5_2ch_10bit422/20260921_C1316.MP4` | XAVC，HEVC **Rext 4:2:2 10-bit**，3840×2160，660 帧 | XAVC 对照 |
| **C** | `testsets/a7m5_4ch_long/20260919_C1305.MP4` | XAVC，HEVC **Main10 4:2:0**，3840×2160 | XAVC 对照（**带 XMP，被 Premiere 处理过**） |

素材身份锚点：A = `0383003f…`、A′ = `1a31c8d3…`。

---

## 2. XF-HEVC 结构（四层）

### 2.1 容器层

```
ftyp(XFVC / XFVC,mp42,iso2)
 └ moov                          ← 在 mdat **之前**（faststart）
    ├ uuid {85C0B687-820F-11E0-8111-F4CE462B6A48}   65,810 B  ← Canon 私有
    ├ udta
    ├ mvhd                        timescale 30000，154154，next_track_id 8
    ├ trak ×7
    └ free
 └ mdat                          323,380,362 B @ 524,288
```

**注意 `uuid` 排在 `mvhd` 之前**，且**没有根级 `meta` box**（XAVC 有）。

### 2.2 轨道层（7 轨）

| # | id | handler | entry | 内容 |
|---|---|---|---|---|
| 0 | 1 | `vide` | `hvc1` | 视频，`stss` 11 同步点（GOP 15），`ctts` 103 项（offset 0/3003），`elst` media_time 1001 |
| 1–4 | 2–5 | `soun` | `ipcm` | **4 × 单声道**，48 kHz / 24 bit |
| 5 | 6 | `tmcd` | `tmcd` | 时间码，**4 B/帧**，值 249959 起逐帧 +1 |
| 6 | 7 | `meta` | `cndm` | 逐帧 metadata，**1064 B/帧定长**，`tref cdsc` → 视频 |

全部轨道用 **`co64`**（64 位块偏移），`tkhd.flags = 7`。

### 2.3 HEVC 位流层

| 项 | 值 |
|---|---|
| profile | `general_profile_idc=4`（**Rext**）、tier high、level_idc 183 |
| chroma / depth | `chroma_format_idc=2`（4:2:2）/ 10 bit |
| 分辨率 | 6912×4608，progressive，无裁剪窗 |
| **并行化** | **tiles 2 列 × 1 行**，`entropy_coding_sync_enabled_flag=0`（**不用 WPP**） |
| CTU | `log2 = 5` → **32×32** |
| 关闭项 | SAO、AMP、TMVP、long-term ref、weighted pred/bipred |
| 开启项 | **scaling list**、bitstream restriction |
| stRPS | 7 |
| `hvcC` | 210 B，`lengthSizeMinusOne=3`，VPS/SPS/PPS 各 1 |
| `colr` | `nclx`，primaries 2 / transfer 2 / matrix 1 / full range |

> ⚠️ **色彩标注问题**：`colour_primaries=2`、`transfer_characteristics=2`
> 在 H.265 里都是 **bt470bg**，只有 `matrix=1` 是 bt709。
> R6 V 是消费级相机，应当标 bt709。这是**相机端的标注问题**。
> 对照：XAVC HS 素材标的是 primaries=1 / transfer=1 / matrix=1（bt709，正确）。

### 2.4 元数据层（两层）

**文件级**：moov 级 uuid box（65,810 B）内是 Canon 私有 box：

| box | 大小 | 内容 |
|---|---|---|
| `CNCV` | 38 B | 版本串 `CanonMP4_002/10.00.03/00.00.00` |
| `CNTH` | 65,552 B | `CNDA` + **JPEG 缩略图 28,745 B** + 36,791 B **全零填充** |
| `CNOP` | 196 B | 嵌套 `CNLG` / `CNLC` / `CNFB` / `CNCM` / `CNID` |

**EXIF 内容**（从 CNTH 的 JPEG 里读出）：

```
Canon EOS R6 V
Firmware Version 1.0.0
RF24-70mm F2.8 L IS USM      ← 镜头型号
LP-E6P                        ← 电池型号
2026:05:10 00:33:37
TIFF: II，IFD0 10 个 tag（0x010F/0x0110/0x0112/0x011A/0x011B/0x0128/0x0132/0x0213/0x8769/0x8825）
```

**帧级**：`cndm` 轨，1064 B 定长样本。
结构 = **Canon 私有头（28 B）+ 6 个 KLV Local Set**。
每帧仅 **177 / 1064 字节**变化，且**只落在 Local Set[3]** 内。

---

## 3. 两侧对照（全部实测）

### 3.1 结构范式相同

| 机制 | XF-HEVC | XAVC |
|---|---|---|
| 厂商私有 brand | `XFVC` | `XAVC`(+`mp42,nras,iso6`) |
| 厂商私有 uuid box | ✅ 1 个 | ✅ 5–9 个（`PROF` / `USMT`） |
| open-GOP 时间轴 | LongGOP + `ctts` + `elst` | 同 |
| 定时 metadata 轨 | ✅ `cndm`（`meta` + `cdsc`） | ✅ `rtmd`（`meta` + `cdsc`） |
| 视频采样入口 | `hvc1` | `hvc1` |
| `stts` delta | 1001 | 1001 |
| **SMPTE UL KLV 框架** | ✅ `060e2b3402530101…` | ✅ `060e2b3402530101…`（**同前缀**） |

### 3.2 关键差异

| 项 | XF-HEVC | XAVC |
|---|---|---|
| **moov 位置** | mdat **之前** | mdat **之后** |
| 根级 `meta` box | ❌ | ✅（`nrtm`，~10 KB） |
| 视频 profile | **Rext 4:2:2 10-bit** | Main10 4:2:0 或 Rext 4:2:2 |
| **并行化** | **tiles 2×1** | **tiles + WPP 都关**（slice 切分） |
| 定时 metadata 轨名 | `cndm` | `rtmd` |
| 定长样本大小 | **1064 B** | **19456 B** |
| 逐帧 metadata 编码 | **KLV Local Set** | Sony 私有二进制 |
| **独立 `tmcd` 轨** | ✅ 有 | ❌ 无（时间码在 `rtmd` tag + XML） |
| 静态 metadata 位置 | uuid box 内 `CN*` | 根级 `meta` box（`nrtm`） |
| 镜头档案 | ❌（在 EXIF 里只有型号） | ✅ `nrtm` `idat` 里 **7,871 B KLV**（`LPIF`） |
| block offset box | `co64` | `stco`（B）/ `co64`（C）⚠️ **非品牌属性** |
| 音频配置 | 4 × mono `ipcm` | `twos` 2ch（B）/ 4 × mono `ipcm`（C）⚠️ **非品牌属性** |

### 3.3 可用 / 不可用的品牌判据

**可用**（可靠）：

1. `ftyp` major_brand：`XFVC` vs `XAVC`
2. 定时 metadata 轨 sample entry：`cndm` vs `rtmd`
3. **有无独立 `tmcd` 轨**
4. 根级 uuid 的 GUID / label
5. **有无根级 `meta` box**
6. moov 位置

**不可用**（会误判）：

- `co64` vs `stco` —— 两条 XAVC 内部就不一致
- 音频声道数 / sample entry —— 随机型配置变化
- `stts` delta、`ctts` 存在性、metadata `handler`/`tref`、视频 entry `hvc1`
  —— 两侧一致，无区分力

---

## 4. 外挂元数据体系对照

| 项 | Canon `.CPF` | Sony `<片段>M01.XML` |
|---|---|---|
| 格式 | 二进制 | XML (UTF-8) |
| 大小 | 63,452 B（**有效仅 283 B**） | 2.2 KB（全有效） |
| **粒度** | **相机级**（A 与 A′ 逐字节相同） | **片段级**（230 个，一片段一份） |
| 与容器关系 | 无对应 | **容器内 `nrtm` XML 的副本**（仅差 1 个尾部 NUL） |
| 机型 / 序列号 | ❌（在容器 EXIF 里） | ✅ `ILCE-7M5` / `07538140` |
| 镜头 | ❌（在容器 EXIF 里，仅型号） | ✅ 型号 + **完整镜头档案 7,871 B** |
| 编码串 | ❌ | ✅ `HEVC_3840_2160_M42210P@L51HT` |
| 色彩管线 | **`Canon Log 2`**（CPF 内） | **`s-log3-cine` / `s-gamut3.cine`**（XML 内） |
| UMID | ❌ | ✅ SMPTE UMID |
| LTC 时间码 | ❌（容器有 `tmcd` 轨） | ✅ 起止时间码 |
| 遥测声明 | ❌ | ✅ `ChangeTable[Gyroscope/Accelerometor]` |

**两个体系的元数据分布策略完全不同：**

- **Canon**：逐帧信息全部压进**容器内一条轨（`cndm`）**；
  sidecar 近乎空壳，只放相机级设置
- **Sony**：容器内 `rtmd` 轨 + `nrtm` meta box，
  **外加**每片段一份 XML 说明书；三者互为副本/说明书

### 4.1 Canon CPF 拆解结果

```
[0, 4)      文件头 b8 b8 e5 41（未识别）
[4, 196)    交错键/值表（按 2 字节对：键稀疏升序 0x01–0xE5，值域仅 6 种）
[196, 232)  两个 ASCII 流按 1 字节步长交错：
              raw[0::2] = "`abcdefghijklmno"   （键流，升序）
              raw[1::2] = "Canon Log 2"        （值流）
[260, 284)  第二张小表（键 0x80–0x8B）
[284, 63452) 63169 B 全零填充（99.55%）
```

未压缩、未加密（89 个不同字节值、0x00 占 99.8%、0xFF 为 0）。

### 4.2 Sony `nrtm` 里有什么

```
meta box（10,303 B，handler=nrtm）
 ├ hdlr   55 B
 ├ iinf   68 B   → item 名 "Lens profile"，mime "application/octet-stream"
 ├ iloc   32 B   → item 位置
 ├ idat  7,911 B → LPIF + KLV(UL 060e2b34025301010e060c0201020241, 7,871 B)
 └ xml   2,225 B → NonRealTimeMeta XML（= sidecar）
```

**项目把 `nrtm.lens_profile` 当作不透明 blob 处理** ——
`preservation/models.py` 只记 `item_id` / `item_name` / `item_mime` /
`item_type` / size / sha256，**从不解析其内容**。本次首次尝试拆内容：

- 载荷**不是浮点网格**（0 段连续 float）
- 头部是 TLV 风格：`ff 00 00 01 01 | ff 01 1e b6`、
  `00 00 00 01 | 00 00 00 20 | <16B>`，其后是打包数据
- 16 字节块 `a6 2b b9 0f cb 0c ac 46 34 74 28 11 8e 56 6d 2b`
  与另一处 `a6 2b cb 0c 34 74 8e 56 …` 疑似同源标识（未确认）
- **字段语义未解出**

---

### 4.3 Canon `tmcd` 时间码语义（新增）
`tmcd` 轨每帧一条 **4 字节大端样本**，值就是 **drop-frame 帧号**：

| 项 | A0013C699 | A0013C700 |
|---|---|---|
| 样本数 | 154 | 686 |
| 首样本值 | **249959** | **250113** |
| 末样本值 | 250112 | 250798 |
| 逐帧增量 | 恒 +1 | 恒 +1 |
| ffprobe 报的 timecode | `02:19:00;11` | `02:19:05;15` |
| `df_to_frames(ffprobe 的值)` | **249959** ✅ | **250113** ✅ |

**已验证**：反算完全吻合 —— 首样本值就是 ffprobe 所报时间码对应的
drop-frame 帧号，两条素材都逐字对上。

**两条素材首尾相接**：A699 末帧 250112 → A700 首帧 250113。
说明帧号是**跨片段累计的全局计数器**，不是片段内计数。

**tmcd sample entry 尾部**（26 B payload）：

```
00000000 00000003 00007530 000003e9 1e00
                  └30000   └1001    └30
```
即 timescale 30000 / frame duration 1001 / 名义帧率 30。

> ⚠️ **未解出**：按标准 drop-frame 公式**正算**得到
> `frames_to_df(249959) = 130:09:00;11`，而 ffprobe 报 `02:19:00;11` ——
> 两者相差 **128 小时**（不是简单的 mod 24，`130 mod 24 = 10`）。
> ffprobe 如何从帧号得到 `02` 未确认；文件中**没有** ASCII 形式的
> `02:19:00;11`（已搜 moov 前 600 KB），说明该字符串是 ffprobe 计算出来的。
> 本节只固定「帧号 ↔ 时间码」的反算吻合这一事实，回绕规则标为未解出。

### 4.4 Canon 等价 sidecar：本机不存在（新增）

已搜索并确认**没有**与 Sony `<片段>M01.XML` 对等的 Canon 文件：

| 位置 | 结果 |
|---|---|
| `F:\CANON_R6V_OG_7K2997P_4CH_XFVC\` | 只有 2 × MP4 + 2 × CPF，**无第三种文件** |
| `F:\` 根及深度 3 | 无 Canon 相关 sidecar |
| `F:\SONY_FTP\` | **空目录** |
| `F:\project\` | **空目录** |
| `F:\SCAN\` | 仅 3 个胶片扫描 TIFF，与视频无关 |

**结论**：Canon 这个体系的 sidecar 就只有 `.CPF`（相机级、近乎空壳）。
所有片段级信息都在容器内 ——
这进一步支持 §4 的判断：**为 Canon 做 preservation，目标是容器内轨道与 box**。

```
meta box（10,303 B，handler=nrtm）
 ├ hdlr   55 B
 ├ iinf   68 B   → item 名 "Lens profile"，mime "application/octet-stream"
## 5. 一条对项目既有结论的独立佐证

Sony XML 里的三个字段是**容器内 metadata 的"说明书"**：

| XML 字段 | 指向 |
|---|---|
| `TargetMaterial/@umidRef` | 容器内 UMID |
| `KlvPacketTable/KlvPacket` | 声明容器内 KLV 事件及起始帧 |
| `ChangeTable[Gyroscope]` / `[Accelerometor]` | 声明 `rtmd` 轨承载遥测 |

这为项目 `preservation/validate.py` 里那条实测记录提供了机制解释：

> source carries the Sony nrtm meta box (handler=nrtm) but the final container
> has none — **Gyroflow reads zero IMU samples from an otherwise perfect rtmd
> track**（14014 IMU samples in, 0 out, rtmd payload bit-identical）

**说明书丢了，数据再完整也读不出来。**

---

## 6. 对项目的直接影响

1. **Canon 侧没有可保留的 sidecar 信息。** CPF 只有 283 B 有效数据且与片段无关；
   有价值的信息在容器的 `cndm` 轨与 `CNTH`/EXIF 里。
   若为 Canon 做 preservation，**目标是容器内轨道，不是 CPF**。
2. **两侧共用 SMPTE UL KLV 框架** → Canon 的 `cndm` 解析
   **可复用同一套 KLV 基础设施**，不必单独写解析器。
3. **`cndm` 的结构与 `rtmd` 同构**（定长采样 + 独立轨 + `cdsc` 关联），
   项目现成的 `preservation/` 机制在结构上适用。丢弃它不是"没法保留"，
   而是没有 Canon 保留管线。
4. **但有一个硬障碍**：XF-HEVC 是 faststart（moov 在 mdat 之前），
   而 `preservation/isobmf.py` 的 `insert_uuid_boxes` **明确拒绝 faststart 文件**。
5. **XAVC 对照素材的纯净度需标注**：C（4:2:0）被 Premiere 两次保存，
   带 XMP `{BE7ACFCB-…}` + 额外 USMT/UUID；B（4:2:2）没有 XMP。
6. **Canon 色彩标注错误**（primaries/transfer = bt470bg）值得在转码时
   归一到 bt709，并在报告中标注原值。

---

## 7. 结论与未决项

### 已实测确认 ✅

- XF-HEVC 四层结构（容器 / 轨道 / 位流 / metadata）全部字段级记录
- 7 轨组成、`co64`、GOP 15、`cndm` 1064 B 定长、`tmcd` 4 B/帧
- SPS/PPS 全字段，含 **tiles 2×1 且不用 WPP**
- Canon `CN*` box 清单与 EXIF 内容（机型 / 固件 1.0.0 /
  **镜头 RF24-70mm F2.8 L IS USM** / 电池 LP-E6P）
- CPF 布局（283 B 有效 + 99.55% 零填充）与 `Canon Log 2` 字段（两个 ASCII 流）
- Sony `nrtm` 五个子 box 与 `idat` 的 `LPIF` + KLV 结构
- **容器 XML 与 sidecar XML 仅差 1 个尾部 NUL**
- 两侧 UL 前缀同为 `060e2b3402530101`
- **`tmcd` 帧号 ↔ ffprobe 时间码反算逐字吻合**（249959 ↔ `02:19:00;11`；
  250113 ↔ `02:19:05;15`），且两条素材首尾相接（250112 → 250113）
- **Canon 无等价 sidecar**（全盘搜索确认）
- **Canon `cndm` 与 Sony `rtmd` 是同一族 KLV Local Set**：
  UL 均以 `060e2b340401` 为族前缀，标签编号同族（`0x8000`=FNumber、
  `0x8100` 族=曝光/感光度）。Canon `cndm` 的 `0x8000` 按 ExifTool 公式解出
  **f/2.8284**，与该文件 EXIF 记录的 **RF24-70mm F2.8** 一致
- **ExifTool 的 uuid GUID 表逐条印证本次实测**：
  `{85C0B687-…}` = `UUID-Canon`、`{BE7ACFCB-…}` = **XMP**、
  `{50524F46-…}` = `UUID-PROF`、`{55534D54-…}` = `UUID-USMT`
- **`CNTH` 内含 `CNDA` = THM 静态图像** → Canon uuid box 是**静态图像 metadata**，
  不是视频 metadata（这解释了为什么两条片段内容相同）
- **XAVC `PROF` 字段级解出**：`FPRF`(flags) / `VPRF`(codec/frame_count/scale) /
  `APRF`(codec/samples/sample_rate) / `PREX`
- **Canon `cndm` 是 1064 B/帧，而 ExifTool 记录的只有 4 B** —— R6 V 用了更新实现

### 未解出 ❌

| 项 | 状态 |
|---|---|
| `cndm` 值内部字段语义 | KLV 框架已确认；值内不是标准 SMPTE 三元组，需 Canon 规范 |
| CPF 官方语义 | 文件头 `b8 b8 e5 41` 未识别；键 `0x01–0x8B` 含义未解；表 1 与文本区编码不一致，**未解通** |
| Sony 镜头档案载荷 | 只到 TLV 风格头部；7000+ B 数据区结构未解 |
| `CNOP` 内 `CNLG`/`CNLC`/`CNFB`/`CNCM`/`CNID` | 只有清单，无字段语义 |
| **`tmcd` 帧号的小时回绕规则** | 反算吻合，但正算 `130:09:00;11` 与 ffprobe 的 `02:19:00;11` 差 128 小时，规则未解出 |
| Canon 卡的其它卷/目录 | 本机已全盘搜索无果；**原始卡未接触**，不排除卡上有 `CONTENTS/` 之类结构 |

### 一条重要的方法论提醒

本次调研中 `Canon Log 2` 字段我**解错了两次**（先按 UTF-16BE、后靠目视对齐），
最终靠**逐字节计算**才确定为两个 ASCII 流。
**产出物里已注明更正**。经验：识别文本编码只靠"看起来对"是不够的，
必须做可复现的字节计算。

---

## 8. 复现

```powershell
cd F:\1KeyTranscoder

# --- XF-HEVC 结构 ---
python work\canon_r6v\xfhevc_structure.py          # 四层深度探测
python work\canon_r6v\make_xfhevc_report.py        # 生成报告 JSON

# --- XAVC 对照 ---
python work\canon_r6v\xfhevc_structure.py --src "testsets\a7m5_2ch_10bit422\20260921_C1316.MP4" --out struct_xavc_422.json
python work\canon_r6v\light_struct.py "testsets\a7m5_4ch_long\20260919_C1305.MP4" struct_xavc_hs420.json
python work\canon_r6v\make_three_way.py            # 三方实测对照

# --- metadata ---
python work\canon_r6v\cndm_klv2.py                 # cndm KLV 结构
python work\canon_r6v\make_cndm_report.py          # cndm 报告
python work\canon_r6v\cpf_decode.py                # CPF 拆解
python work\canon_r6v\xavc_deep.py                 # XAVC uuid / 根级 meta / XMP
python work\canon_r6v\xml_diff.py                  # 容器 XML vs sidecar XML 差异
python work\canon_r6v\sony_lens_profile.py         # Sony 镜头档案
python work\canon_r6v\lens_profile_tlv.py          # 镜头档案载荷内部
python work\canon_r6v\canon_cnth.py                # Canon CN* box + EXIF
```
