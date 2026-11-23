# XFVC ↔ XAVC 结构 diff

**对象**

| 代号 | 素材 | 大小 |
|---|---|---|
| **A** | XFVC（Canon EOS R6 V）`A_0013C699H260510_003337W5_CANON.MP4` | 323,904,650 B |
| **B** | XAVC（Sony ILCE-7M5）`20260921_C1316.MP4` | 201,478,247 B |

**方法**：box 树递归解析（`preservation.isobmf`）+ 逐节点比对。
**只读静态分析，未跑任何编码测试。**

**机读产物**：`xfvc-vs-xavc-diff.json`（含两方完整 box 树）、
`xfvc-vs-xavc-diff-report.json`

---

## 1. 一句话结论

**两者的"骨架"高度一致（27 种 box 类型相同），"器官"完全不同。**

一致的骨架 = 标准 ISOBMFF 结构（`moov`/`trak`/`mdia`/`minf`/`stbl` 五层嵌套、
`stts`/`stsc`/`stsz`/`stsd` 采样表、`elst` 编辑列表、`tref cdsc`）；
不同的器官 = **品牌标识、metadata 承载方式、块偏移宽度、轨道构成**。

---

## 2. 顶层布局 diff

| | A（XFVC） | B（XAVC） |
|---|---|---|
| box 顺序 | `ftyp → moov → mdat` | `ftyp → uuid:PROF → free → mdat → moov → meta` |
| 顶层 box 数 | **3** | **6** |
| **moov 位置** | **在 mdat 之前**（faststart） | **在 mdat 之后** |
| 根级 `meta` | ❌ | ✅（10,303 B，handler=`nrtm`） |
| 根级 `uuid` | ❌ | ✅ `PROF`(183 B) |
| `free` 填充 | ❌（顶层） | ✅ 130,841 B（在 mdat 前） |

**实测偏移**：

```
A:  ftyp@0(28)  moov@28(524,260)  mdat@524,288(323,380,362)
B:  ftyp@0(32)  uuid:PROF@32(183)  free@215(130,841)
    mdat@131,056(201,326,608)  moov@201,457,664(10,280)  meta@201,467,944(10,303)
```

**这是最重要的一条结构性差异**：A 的 moov 在前、B 的 moov 在后。
`preservation/isobmf.py` 的 `insert_uuid_boxes` 明确拒绝 faststart 文件 ——
A 恰好就是 faststart。

---

## 3. box 类型集合 diff

### 3.1 只在 A（XFVC）出现

| box | 位置 | 说明 |
|---|---|---|
| **`udta`** | moov 级 | 用户数据容器（B 完全没有） |
| **`manu`** | udta 内 | **厂商名 = "Canon"** |
| **`modl`** | udta 内 | **机型 = "Canon EOS R6 V"** |
| **`urat`** | udta 内 | 全零（8 B） |
| **`sdtp`** | 视频轨 stbl 内 | **样本依赖类型表**（166 B） |
| **`co64`** | 全部 7 轨 | **64 位块偏移** |
| `uuid:{85C0B687-…}` | moov 级 | Canon 私有 uuid（65,810 B） |

**`udta` 实测内容**：

```
manu   12 B   text="Canon"            lang=5575
modl   21 B   text="Canon EOS R6 V"   lang=5575
urat    8 B   全零
free 1,975 B  零填充
```

### 3.2 只在 B（XAVC）出现

| box | 位置 | 说明 |
|---|---|---|
| **`meta`** | 根级 | 文件级 metadata 容器 |
| **`iinf`** / **`iloc`** / **`idat`** / **`xml `** | meta 内 | item 声明/位置/数据/XML（= `nrtm`） |
| **`stco`** | 视频轨 stbl 内 | **32 位块偏移** |
| `uuid:PROF` | 根级 | 文件全局配置（FPRF/VPRF/APRF/PREX） |
| `uuid:USMT` | 各轨尾 + moov 尾 | 逐轨 metadata（MTDT） |

### 3.3 两边都有（27 种）

```
ftyp moov mdat free trak tkhd edts elst mdia mdhd hdlr minf vmhd smhd nmhd
dinf dref stbl stsd stts stss ctts stsc stsz tref cdsc
```

即：**标准 ISOBMFF 骨架完全一致**。差异全部在扩展部分。

---

## 4. 轨道构成 diff

| # | A（XFVC）7 轨 | B（XAVC）3 轨 |
|---|---|---|
| 0 | `vide`/`hvc1` n=154<br>`stsd,stts,stss,ctts,stsc,stsz,`**`co64`**`,`**`sdtp`** | `vide`/`hvc1` n=660<br>`stsd,stts,ctts,stsc,stsz,`**`stco`**,stss` |
| 1 | `soun`/`ipcm` n=246646 | `soun`/**`twos`** n=528528 |
| 2 | `soun`/`ipcm` n=246646 | **`meta`/`rtmd`** n=660 |
| 3 | `soun`/`ipcm` n=246646 | — |
| 4 | `soun`/`ipcm` n=246646 | — |
| 5 | **`tmcd`/`tmcd`** n=154 | — |
| 6 | **`meta`/`cndm`** n=154 | — |

### 4.1 按 handler 归类

| handler | A | B | A 的 sample entry | B 的 sample entry |
|---|---|---|---|---|
| `vide` | 1 | 1 | `hvc1` | `hvc1` ✅ 同 |
| `soun` | **4** | 1 | `ipcm` ×4 | `twos` |
| `meta` | 1 | 1 | **`cndm`** | **`rtmd`** |
| `tmcd` | **1** | **0** | `tmcd` | — |

### 4.2 三条关键差异

1. **`tmcd` 轨只在 A 有。** Canon 用独立 `tmcd` 轨（4 B/帧）承载时间码；
   B 把时间码放在 `rtmd` 轨的 tag 与 `nrtm` XML 的 `LtcChangeTable` 里。
2. **`meta` 轨的 sample entry 不同**：`cndm`（Canon，1064 B 定长）vs
   `rtmd`（Sony，19456 B 定长）。**两者都用 `tref cdsc` 关联视频轨**（相同）。
3. **音频：A 是 4 × 单声道 `ipcm`；B 是 1 × 立体声 `twos`。**
   这是**机型配置差异**，不是格式差异 —— 同项目另一条 XAVC 素材
   （4:2:0）就是 4 × 单声道 `ipcm`，与 A 完全相同。

### 4.3 `stsd` 构成（实测，逐子 box）

| | A 视频轨 | B 视频轨 | B′（XAVC 4:2:0） |
|---|---|---|---|
| `stsd` | **331 B** | **569 B** | **771 B** |
| sample entry | `hvc1`(315 B) | `hvc1`(553 B) | `hvc1`(755 B) |
| 子 box | **`hvcC`(210) + `colr`(19)** | **`hvcC`(467)，无 `colr`** | **`hvcC`(669)，无 `colr`** |

**两点**：
1. **A 独有 `colr`** —— 这是本次三份素材里唯一带色彩标注 box 的。
   也正是它给出了有问题的 primaries/transfer = bt470bg（见 `README.md` §4.5）。
2. **`hvcC` 大小 210 → 467 → 669** 随 profile/分辨率递增
   （A 是 Rext 4:2:2 小 SPS，B 是 Rext 4:2:2 大 SPS，B′ 是 Main10 4:2:0）。
   **不能用作品牌判据**。

### 4.4 `sdtp`（样本依赖类型表）—— A 独有，内容已解

`ISO/IEC 14496-12` 的 `sdtp` 每样本一字节，四个 2 位字段：
`is_leading` | `sample_depends_on` | `sample_is_depended_on` | `sample_has_redundancy`。

**A 实测**（payload 158 B = 4 B version/flags + **154 个样本**）：

| 取值 | 数量 | 解码 |
|---|---|---|
| `0x00` | 52 | leading=0, **depends_on=0**（I 片）, is_depended=0, redundancy=0 |
| `0x08` | 102 | leading=0, **depends_on=0**, **is_depended=2**, redundancy=0 |

**逐样本模式**（前 12）：`0, 8, 8, 0, 8, 8, 0, 8, 8, …` —— 周期性 3。

**两点观察**：
1. **`is_leading` 全部为 0**，包括**前两个前导帧**。
   这值得注意：本项目实测该素材**确实有 2 个前导图像**
   （首 DTS −0.033367、首个同步包前有 2 帧），
   但 Canon 在 `sdtp` 里**没有把它们标成 leading**。
   即：**不能用 `sdtp` 判断本素材是否有前导图像** ——
   判断依据仍是包序/PTS（见 `../README.md` §7）。
2. `depends_on = 0` 对全部帧成立 —— 但这是 I **和** P 片共用的值
   （规范里 0 = 未知、1 = 依赖、2 = 不依赖），
   与 `ctts` 显示的 B 帧重排序并存，说明该字段**不能用来区分 I/P/B**。

**B 完全没有 `sdtp`** —— 若做 preservation 重建，`sdtp` 是 A 侧需要
一并保留的 box，而现有 `preservation` 流程从未见过它。

---

## 5. 采样表 diff

| box | A（XFVC） | B（XAVC） | 判定 |
|---|---|---|---|
| `stts` | 1 entry，delta 恒 **1001** | 1 entry，delta 恒 1001 | ✅ **同** |
| `stss` | 11 同步样本（GOP 15） | 11 同步样本（GOP 60） | 都存在，参数不同 |
| `ctts` | 103 entry，offset 0 / **3003** | 660 entry，offset 0 / **5005** | ✅ 都存在（B 帧重排序） |
| `elst` | media_time **1001** | media_time **2002** | 都存在，参数不同 |
| **块偏移** | **`co64`**（11 chunk） | **`stco`**（11 chunk） | ⚠️ **宽度不同** |
| `sdtp` | ✅ 166 B | ❌ | A 独有 |
| `stsc` | 2 entry | 1 entry | 参数不同 |

**`co64` vs `stco` 判定**：A 用 64 位，B 用 32 位。
但**同项目另一条 XAVC 素材用的是 `co64`** ——
所以这是**按文件大小/编码器配置**变化的，**不是品牌属性**。

---

## 6. metadata 载体 diff

| 载体 | A（XFVC） | B（XAVC） |
|---|---|---|
| 根级 uuid | ❌ | `PROF`(183 B) |
| moov 级 uuid | **`{85C0B687-…}`(65,810 B)**，内含 `CNCV`/`CNTH`/`CNOP` | ❌ |
| 根级 meta box | ❌ | **`meta`(10,303 B)** = `nrtm` |
| ├ `iinf`/`iloc`/`idat` | ❌ | ✅（item = "Lens profile"，7,911 B） |
| └ `xml ` | ❌ | ✅ 2,225 B（NonRealTimeMeta） |
| 逐轨 uuid | ❌ | **`USMT`(52 B) × 3** + moov 尾 ×1 |
| 定时 metadata 轨 | **`cndm`(1064 B/帧)** | **`rtmd`(19456 B/帧)** |
| 时间码 | **`tmcd` 独立轨** | `rtmd` tag + XML |
| udta | **`manu`+`modl`+`urat`** | ❌ |
| 文件级厂商 metadata | **moov 级 uuid 内 `CN*`** | **根级 `meta`(nrtm)** |

### 6.1 一句话概括两者的 metadata 策略

- **A（Canon）**：全部塞进 **moov 内的一个 uuid box** + **一条定时轨 `cndm`**；
  顶层极简（只有 3 个 box），机型信息放 `udta`。
- **B（Sony）**：分散在 **根级 `meta`(nrtm)** + **根级 `uuid:PROF`** +
  **逐轨 `uuid:USMT`** + **`rtmd` 轨**；顶层 6 个 box。

---

## 7. 完整 diff 表（速查）

| 维度 | A（XFVC） | B（XAVC） | 关系 |
|---|---|---|---|
| 顶层 box 数 | 3 | 6 | 不同 |
| moov 位置 | **mdat 前** | mdat 后 | **不同** |
| 根级 `meta` | ❌ | ✅ `nrtm` | **不同** |
| 根级 `free` | ❌ | ✅ 130 KB | 不同 |
| `ftyp` 大小 | 28 B | 32 B | 不同（品牌名长度） |
| `udta` | ✅ `manu`="Canon" / `modl`="Canon EOS R6 V" | ❌ | **不同** |
| 标准 ISOBMFF 骨架 | ✅ | ✅ | **相同（27 种）** |
| 视频 sample entry | `hvc1` | `hvc1` | **相同** |
| `colr` | ✅ (19 B) | ❌ | **不同** |
| `hvcC` 大小 | 210 B | 467 B | 不同（随 profile） |
| `stts` delta | 1001 | 1001 | **相同** |
| `ctts` | ✅ | ✅ | **相同** |
| `elst` | ✅ | ✅ | **相同** |
| `tref cdsc` | ✅ | ✅ | **相同** |
| 块偏移 box | `co64` | `stco` | 不同（非品牌） |
| `sdtp` | ✅ 154 B（每样本 1 B） | ❌ | **不同** |
| 轨道数 | 7 | 3 | 不同 |
| 音频配置 | 4×mono `ipcm` | 1×stereo `twos` | 不同（机型） |
| `tmcd` 轨 | ✅ | ❌ | **不同** |
| `meta` 轨 entry | `cndm` | `rtmd` | **不同** |
| 厂商 uuid 位置 | moov 级 ×1 | 根级 ×1 + 逐轨 ×N | **不同** |
| 文件级 metadata | uuid 内 `CN*` | 根级 `nrtm` | **不同** |

---

## 8. 对项目的影响

1. **品牌判据（可靠）**：`ftyp` major_brand、`meta` 轨 sample entry
   （`cndm` vs `rtmd`）、**有无 `tmcd` 轨**、有无根级 `meta` box、
   moov 位置、`udta` 的 `manu`/`modl`。
2. **不可用作判据**：`co64`/`stco`、音频配置、`stts` delta、
   `ctts`/`elst`/`tref` 存在性、视频 sample entry（都是 `hvc1`）、
   `hvcC` 大小（随 profile 变化）。
3. **`udta` 是 A 侧一个未被项目利用的信息源**：
   `manu`="Canon"、`modl`="Canon EOS R6 V" 是**明文可读**的，
   比解析 `CNCV`（`CanonMP4_002/…`）或 EXIF 缩略图更直接。
   项目 `core/batch_hw.py` 的分类器只看 data 流的 `rtmd` tag，
   不读 `udta` —— 若要识别 Canon，`udta` 是现成的判据。
4. **`sdtp`（样本依赖类型表）A 有 B 无** ——
   若做 preservation 重建，`sdtp` 是需要一并保留的 box。

---

## 9. 复现

```powershell
cd F:\1KeyTranscoder

# box 树 diff（生成 xfvc-vs-xavc-diff.json）
python work\canon_r6v\xfvc_xavc_diff.py

# 汇总报告 + udta 内容
python work\canon_r6v\make_diff_report.py

# 打印两方完整 box 树
python work\canon_r6v\_show_trees.py
```
