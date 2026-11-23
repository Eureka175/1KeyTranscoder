# 外挂元数据与容器内 metadata 体系对照

**对象**：Canon XF-HEVC 与 Sony XAVC 的 sidecar 文件 + 容器内 metadata 结构
**方法**：字节级直读 + ISOBMF/KLV 解析，**只读**
**机读产物**：`cpf-structure.json`、`xavc-deep.json`

---

## 1. 三句话结论

1. **两侧的 sidecar 模型根本不同**：Canon 的 `.CPF` 只有 283 字节有效数据、
   且两条不同片段共用同一份（相机级）；Sony 的 `<片段>M01.XML` 每片段一份、
   2.2 KB 全有效（片段级）。
2. **两侧容器内 metadata 的"摆放位置"完全不同**：Canon 把每帧 metadata
   放进一条**独立视频轨 `cndm`**（1064 B/帧）；Sony 用 **`meta` box
   （`iinf`/`iloc`/`idat`/`xml`）** 放静态项（镜头档案）+ **`rtmd` 轨**放遥测。
3. **但底层编码框架是同一个**：两侧的 metadata 载荷**都用 SMPTE UL KLV**，
   且都以 **`060e2b3402530101`** 开头。差异在 UL 后缀族与摆放方式。

---

## 2. Canon CPF 结构（实测）

### 2.1 整体布局

```
偏移        长度        内容
────────────────────────────────────────────────────────────
[0, 4)      4 B        文件头  b8 b8 e5 41（未识别）
[4, 196)    192 B      交错键/值表
[196, 232)  36 B       含文本字段（"Canon Log 2"）
[232, 260)  28 B       零
[260, 284)  24 B       第二张小表（键 0x80..0x8b）
[284, 63452) 63169 B   **全零填充（99.55%）**
```

**已排除**：不是压缩、不是加密

| 检验 | 结果 |
|---|---|
| 不同字节值数量 | **89 / 256** |
| `0x00` 占比 | **99.8%** |
| `0xFF` 占比 | **0%** |

### 2.2 交错键/值表（[4, 196)，按 2 字节对读出）

```
b8:b8  e5:41
01:01  02:00  04:00  06:00  07:00  08:00  09:00  0a:00  0b:00
0c:00  0d:00  0e:00  0f:01  10:00  11:5f  12:00  13:00  15:00
1a:00  21:01  22:00  23:00  24:10  25:10  26:10  2c:00 … 33:00
36:00  37:00  38:10  39:10  3a:10  3b:10  3c:10  3d:10  3e:10
3f:10  40:10  41:10  42:10  44:00  4d:01  4f:00  50:01  51:00  52:00
```

- 第一字节做键：范围 `0x01`–`0xE5`，**稀疏且基本升序**
  （缺号 = 默认值被省略）
- 第二字节取值极少：仅 `0x00 / 0x01 / 0x10 / 0x41 / 0x5F / 0xB8`
- 非零值项 17 个

第二张小表（[260, 284)）：

```
80:00  81:00  82:05  83:03  84:00  85:00  86:00
87:00  88:00  89:00  8a:01  8b:00
```

### 2.3 文本字段 `Canon Log 2`（偏移 196 起 36 字节）

```
60436161626e636f646e6520664c676f686769206a326b006c006d006e006f0000000000
```

**两个 ASCII 流，按 1 字节步长交错**（已确证，可复现）：

| 取法 | 十六进制 | ASCII |
|---|---|---|
| `raw[0::2]`（第 0,2,4,… 字节） | `606162636465666768696a6b6c6d6e6f` | **`` `abcdefghijklmno ``** |
| `raw[1::2]`（第 1,3,5,… 字节） | `43616e6f6e204c6f672032` | **`Canon Log 2`** |

即：**单字节升序键流 + 单字节 ASCII 值流交错**。

> ⚠️ **本报告先前版本的更正**：初版把值流按 **UTF-16BE** 解读，
> 并写成「1 字节键 + 2 字节 UTF-16BE 字符」。**那是错的** ——
> `43 61 6e 6f 6e 20 4c 6f 67 20 32` 是 13 个 ASCII 字节，
> 不是 6.5 个 UTF-16BE 字符。按 UTF-16BE 解会得到乱码 `䍡湯渠䱯朠㈀`。
> 现有产物 `cpf-structure.json` 已按 ASCII 重新生成（`status: RESOLVED`）。

**含义**：`Canon Log 2` 是 Canon 的 log 曲线名称，功能上对应
Sony XML 里的 `CaptureGammaEquation="s-log3-cine"`。
即：**Canon 把这个信息放在 CPF，Sony 放在 NRTM XML。**

### 2.4 关键观察：两条素材的 CPF 逐字节相同

| | A0013C699 | A0013C700 |
|---|---|---|
| 帧数 / 时长 | 154 / 5.138 s | 686 / 22.890 s |
| 拍摄时刻 | 00:33:38 | 00:33:50 |
| **CPF sha256** | `beb049833861c38e426a9214af3bc28b…` | **完全相同** |

**推论**：CPF **不是片段级 metadata**，而是**相机/卡级设置快照或索引**。

---

## 3. Sony NRTM XML（sidecar，实测）

命名空间 `urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.20`。

| 元素 | 取值 |
|---|---|
| `TargetMaterial/@umidRef` | **SMPTE UMID** `060A2B340101010501010D4313000000FF1EA85D041306DB0C802FFFFE47A0F1` |
| `Duration/@value` | 660 帧 |
| `LtcChangeTable` | `tcFps=30`，起始 `58575202` / 结束 `59885302`（**LTC 时间码**） |
| `CreationDate` | `2026-09-21T16:46:49+08:00` |
| `KlvPacketTable/KlvPacket` | `key=060E2B34010101050301020A02000000`，`frameCount=0`，`lengthValue=095F5265635374617274`，`status=spot` |
| `VideoFrame` | `videoCodec="HEVC_3840_2160_M42210P@L51HT"` `captureFps=59.94p` |
| `VideoLayout` | `3840×2160`，`16:9` |
| `AudioFormat` | `numOfChannel=2`，`LPCM16`，`CH1`/`CH2` |
| `SubStream` | `HEVC_Proxy_1920_1080_M10P@L41MT`（**代理码流**） |
| `Device` | `manufacturer=Sony` `modelName=ILCE-7M5` `serialNo=07538140` |
| `Lens` | `20-200mm F3.5-6.3 DG \| Contempo` |
| `CameraUnitMetadataSet` | `CaptureGammaEquation=s-log3-cine`、`CaptureColorPrimaries=s-gamut3-cine`、`CodingEquations=rec709` |
| `ChangeTable` ×5 | `ImagerControlInformation` / `LensControlInformation` / `DistortionCorrection` / **`Gyroscope`** / **`Accelerometor`** |

### 3.1 XML **同时存在于两处**（实测，差异已定位）

| 位置 | 大小 |
|---|---|
| 容器内根级 `meta` box 的 `xml ` box | payload 2217 B（含 4 B FullBox 头 → 2213 B） |
| 同目录 sidecar `<片段>M01.XML` | 2212 B |

**逐字节比对结果**（`nrtm-xml-diff.json`）：

| 素材 | 容器 XML | sidecar XML | 差异 |
|---|---|---|---|
| `20260921_C1316.MP4` | 2,213 B | 2,212 B | **仅 1 B：容器侧多一个尾部 `\x00`** |
| `20260919_C1305.MP4` | 3,214 B | 3,213 B | **仅 1 B：容器侧多一个尾部 `\x00`** |

**结论**：两者**内容完全相同**，唯一差异是容器内的 XML 多一个
C 字符串终止符 NUL。即 **sidecar XML 就是容器内 `nrtm` XML 的导出副本**。

> 先前版本报告写的是「大小差 1 B、sha256 不同，差异位置未逐一比对」——
> 现已定位并解释。

**含义**：丢了容器内的 `meta` box，sidecar 仍保有同一份 XML ——
这解释了为什么项目 `preservation/validate.py` 会对
`nrtm.lens_profile` 和 `nrtm.xml` 分别做 sha256 校验。

### 3.2 XML 是容器内 metadata 的"说明书"

三个字段直接指向容器内容：

| XML 字段 | 指向 |
|---|---|
| `TargetMaterial/@umidRef` | 容器内的 UMID（素材标识） |
| `KlvPacketTable/KlvPacket` | **声明容器内 KLV 事件**及其起始帧 |
| `ChangeTable[Gyroscope]` / `[Accelerometor]` | **声明 `rtmd` 轨承载陀螺仪/加速度计** |

这为项目 `preservation/validate.py` 里那条实测记录提供了机制解释：

> "source carries the Sony nrtm meta box (handler=nrtm) but the final container
> has none — **Gyroflow reads zero IMU samples from an otherwise perfect rtmd
> track**（14014 IMU samples in, 0 out, rtmd payload bit-identical）"

**`nrtm` 丢了，即使 `rtmd` 逐字节保留，消费端也读不出数据** —— 说明书没了。

---

## 4. XAVC 容器深挖：根级 `meta` box

两条 XAVC 素材的顶层顺序都是
`ftyp → uuid[×N] → free → mdat → moov → meta`，**尾部有一个根级 `meta` box**。

### 4.1 `meta` box 内部结构（实测，两条一致）

| 子 box | 偏移(相对) | 大小 | 内容 |
|---|---|---|---|
| `hdlr` | 12 | 55 B | **handler_type = `nrtm`** |
| `iinf` | 67 | 68 B | item 声明 |
| `iloc` | 135 | 32 B | item 位置 |
| `idat` | 167 | **7,911 B** | item 数据 |
| `xml ` | 2,078 | 2,225 / 3,226 B | **NonRealTimeMeta XML** |

### 4.2 `iinf` 声明的 item = **"Lens profile"**

```
00000000 0001 00000036 696e6665 00000000 0001 0000
4c656e732070726f66696c65 00            ← "Lens profile"
6170706c69636174696f6e2f6f637465742d73747265616d 00  ← "application/octet-stream"
```

### 4.3 `idat` 是 **KLV**，且与 Canon 同前缀族

```
idat 头部: ....LPIF......+4.S......
```

- 头 8 字节含 **`LPIF`**（Lens Profile Interchange Format）
- 偏移 12 起是 **KLV**：

| 项 | 值 |
|---|---|
| UL | `060e2b34025301010e060c0201020241` |
| 长度 | **7,871 B**（单个项，包裹整个镜头档案） |

**关键**：UL 前缀 **`060e2b3402530101` 与 Canon `cndm` 完全相同**。
两侧都遵循同一条 SMPTE UL 注册前缀，只在后续字节上分族：

| 侧 | UL 后缀族 | 出现位置 |
|---|---|---|
| Canon | `0c020101…` | `cndm` 轨（6 个 Local Set） |
| Sony | `0e060c0201…`（镜头档案）、`0401010b…`（KLVFill） | `nrtm` 的 `idat` |

### 4.4 意外发现：一条 A7M5 素材**被 Premiere 处理过**

XAVC HS 4:2:0 那条素材多出一个根级 uuid box
`{BE7ACFCB-97A9-42E8-9C71-999491E3AFAC}`（4,035 B），载荷是 **XMP**：

```xml
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 9.1-c002 …">
  … xmp:CreateDate="2026-09-19T11:44:59Z"
     xmp:ModifyDate="2026-09-20T11:19:46+08:00"
     xmpDM:audioChannelType="Mono" xmpDM:audioSampleType="24Int"
     xmpDM:videoFrameRate="59.940060"
  <xmpDM:duration xmpDM:value="288438150" xmpDM:scale="1/60000"/>
  <xmpDM:videoFrameSize stDim:w="3840" stDim:h="2160"/>
  <xmpMM:History>
    <rdf:li stEvt:action="saved"
            stEvt:softwareAgent="Adobe Premiere Pro 2025.0 (Windows)"
            stEvt:when="2026-09-20T11:19:46+08:00" stEvt:changed="/"/>
    <rdf:li stEvt:action="saved"
            stEvt:softwareAgent="Adobe Premiere Pro 2025.0 (Windows)"
            stEvt:when="2026-09-20T11:19:52+08:00" stEvt:changed="/metadata"/>
  </xmpMM:History>
</x:xmpmeta>
```

**这条素材经过 Premiere 两次保存**（一次整体、一次仅 metadata）。

**重要影响**：该素材**不是相机直出**。用它做"XAVC 相机行为"的对照时
必须注明这一点 —— 容器结构可能已被 NLE 重写过。
另一条 A7M5 素材（4:2:2，192 MB）**没有** XMP box。

### 4.5 两条 XAVC 素材的 uuid box 差异

| | XAVC 4:2:2 (192 MB) | XAVC HS 4:2:0 (51 GB) |
|---|---|---|
| 根级顺序 | `ftyp, uuid, free, mdat, moov, meta` | `ftyp, uuid, **uuid**, free, mdat, moov, meta` |
| uuid 总数 | 5 | **9** |
| PROF | 1 | 1 |
| USMT | 4 | 7 |
| **XMP (`{BE7ACFCB-…}`)** | ❌ | ✅ 1 |
| `nrtm` xml 大小 | 2,225 B | 3,226 B |

---

## 5. 三侧 metadata 体系对照

| 项 | Canon XF-HEVC | Sony XAVC |
|---|---|---|
| **sidecar 文件名** | `<片段>.CPF` | `<片段>M01.XML` |
| sidecar 格式 | 二进制 | XML (UTF-8) |
| sidecar 大小 | 63,452 B（**有效 283 B**） | 2.2 KB（全有效） |
| **sidecar 粒度** | **相机级**（两片段共用同一份） | **片段级** |
| sidecar 与容器关系 | 无对应 | **容器内 `nrtm` XML 的副本** |
| 容器内静态 metadata | moov 级 uuid `{85C0B687-…}`（`CN*` box） | 根级 `meta` box（`nrtm`）+ `nrtm` 的 `idat`（镜头档案 KLV） |
| 容器内逐帧 metadata | **`cndm` 轨**（1064 B/帧，KLV Local Set） | **`rtmd` 轨**（19456 B/帧，Sony 私有） |
| 时间码 | 独立 `tmcd` 轨（4 B/帧） | `rtmd` 轨 tag + XML `LtcChangeTable` |
| 遥测声明 | 无（在 `cndm` 内含） | XML `ChangeTable[Gyroscope/Accelerometor]` |
| 厂商 uuid | 1 个 | 5–9 个（PROF / USMT / 可选 XMP） |
| **KLV 框架** | ✅ UL `060e2b3402530101…` | ✅ UL `060e2b3402530101…`（同前缀） |
| moov 位置 | mdat **之前** | mdat **之后** + 尾部 `meta` |

**一句话**：Canon 把 metadata **集中在容器内的一条轨**里，sidecar 近乎空壳；
Sony 把 metadata **分散在轨道 + meta box + sidecar 三处**，且三者互为副本/说明书。

---

## 6. 对项目的直接影响

1. **Canon 侧没有可保留的"sidecar 信息"。** CPF 只有 283 字节有效数据且
   与片段无关；真正有价值的信息在容器的 `cndm` 轨与 `CNTH` 里。
   项目若要为 Canon 做 preservation，**目标应是容器内轨道，不是 CPF**。
2. **Sony 侧的 sidecar 是容器内 `nrtm` 的副本**，不是独立数据源。
   这解释了项目为什么对 `nrtm.lens_profile` 与 `nrtm.xml` 分别做 sha256
   —— 两者是同一份 XML 的两种载体。
3. **A7M5 素材的"对照纯净度"需要标注**：4:2:0 那条被 Premiere 重写过，
   带 XMP + 额外 USMT/UUID；4:2:2 那条没有 XMP。
   做 XAVC 行为断言时应指明用的是哪条。
4. **两侧共用 SMPTE UL KLV 框架**这一发现，意味着 Canon 的 `cndm`
   解析**可以复用同一套 KLV 解析基础设施** —— 不必为 Canon 单独写解析器。
   （但见 §7：字段语义仍未解出。）

---

## 7. 未解出 / 未验证

1. **CPF 的官方语义未确认。** 文件头 `b8 b8 e5 41` 未识别；
   键 `0x01`–`0x8B` 的字段含义未解；
   表 1（`[0,196)` 区间按 2 字节对读出）与文本区（偏移 196 起、
   两个 ASCII 流按 1 字节步长交错）的**编码方式看起来不一致**，
   我没有解通这个不一致。
   "相机级设置快照"是基于「两片段共用同一份 + 99.55% 零填充」的**推论**，
   无官方文档佐证，也没有第三份素材交叉验证。
2. **`cndm` 字段语义未解出**（见 `cndm.md` §4）：
   KLV 框架已确认，但值内部按 SMPTE 三元组解析不成立。
3. **容器内 `nrtm` XML 与 sidecar XML 未逐字节比对。** 已知大小差 1 B、
   sha256 不同；差异位置未定位。
4. **Sony 镜头档案（`idat` 里 7,871 B 的 KLV）未解。** 只确认了
   `LPIF` 头 + 单条 UL + 长度；内部结构未拆。
5. **230 个 XML 未逐一对比。** 只细读 1 个；预期镜头/时长/时间码不同。
6. **Canon 侧未找到与 NRTM XML 对应的文件。** 目录内只有 `.CPF` + `.MP4`。
   若 Canon 等价物存在，可能在卡的其它目录（如 `CONTENTS/`）——
   **本次未调查**。
7. **`CNTH`（65,552 B，含 JPEG EXIF）/`CNOP`/`CNDA`/`CNCM` 只做清单级解析**，
   内部结构未拆。

---

## 8. 复现

```powershell
cd F:\1KeyTranscoder

# Canon CPF
python work\canon_r6v\cpf_structure.py    # 非零区段 + 交错文本发现
python work\canon_r6v\cpf_decode.py       # 表 + 文本字段

# XAVC 容器深挖（uuid / 根级 meta / XMP / idat KLV）
python work\canon_r6v\xavc_deep.py

# Sony sidecar XML（UTF-8 文本，直接读）
Get-Content testsets\a7m5_2ch_10bit422\20260921_C1316M01.XML -Raw
```
