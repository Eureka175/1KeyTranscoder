# reproducer — 最小可复现素材包

给 rigaya 的 NVEncC / QSVEncC issue 用的**自包含**复现包。里面没有引用任何本仓库
路径：拿到这个文件夹就能复现、能重新生成、能直接当附件上传。

缺陷的判据（在整份语料上验证过，无需解码、无需 GPU）：

```text
avhw_frames = container_samples − pictures_presented_before_first_packet
```

## 目录

```text
reproducer/
├── README.md                      本文件
├── facts.json                     每个片子的实测数据 (大小/sha256/流参数/样本数/leading/预测帧数)
├── check-shape.ps1                形状判定器：算出 leading pictures 与预测的 --avhw 帧数
├── make-control-fixtures.ps1      用原视频生成 A/B/C/D 四个对照片子
├── make-synthetic-clip.ps1        生成"纯合成"的 XAVC 形状片子（无任何相机素材）
├── source/
│   └── 20260903_C1170.MP4         测试原视频（真实 Sony A7M5 XAVC HS）
├── generated/                     make-control-fixtures.ps1 的产物
│   ├── A_sony_copy.mp4
│   ├── B_sony_copy.mkv
│   ├── C_x265_reencode.mp4
│   └── D_synthetic_testsrc2.mp4
└── synthetic/
    └── xavc-shape-synthetic.mp4   make-synthetic-clip.ps1 的产物
```

## 六段素材各自的作用（实测，见 `facts.json`）

| 文件 | 大小 | 样本 | leading | 预测 `--avhw` | 复现？ | 作用 |
|---|---|---|---|---|---|---|
| `source/20260903_C1170.MP4` | 67.2 MB | 30 | **3** | **27** | **是** | 测试原视频，真实 XAVC HS 素材，绝对基准 |
| `generated/A_sony_copy.mp4` | 10.6 MB | 30 | **3** | **27** | **是** | 同一码流，**只留视频轨**，MP4。最适合当附件 |
| `generated/B_sony_copy.mkv` | 10.6 MB | 30 | **3** | **27** | **是** | 同一码流装进 MKV：**没有 edit list**，仍然复现 → 证明 edit list **不是**触发器 |
| `generated/C_x265_reencode.mp4` | 571 KB | 30 | 0 | 30 | 否 | x265 重编码（closed GOP）：leading 消失 → 证明"重编码"会把待测形状本身消掉 |
| `generated/D_synthetic_testsrc2.mp4` | 1.3 MB | 60 | 0 | 60 | 否 | 纯合成负对照，完全不含 Sony 素材 |
| `synthetic/xavc-shape-synthetic.mp4` | **291 KB** | 93 | **3** | **90** | **是** | **纯合成**且带 XAVC 形状 → 可以随便公开、不含任何相机画面 |

预测列是判据算出来的**预期**值。真机实测值（`--avhw` 实际交付多少帧）需要跑一次
硬件解码才能确认，本包没有做，见下面"尚未验证"。

## 建议怎么用

**首选附件：`generated/A_sony_copy.mp4`（10.6 MB，低于 GitHub 网页附件 25 MB 上限）**
真实 XAVC 码流、已剥掉全部音轨（没有音频隐私问题）、leading=3，是"最像原始报告"的
可上传证据。

> **为什么不用 `source/` 那段原视频当附件：** 它有 6 条流 —— HEVC 视频 + **4 条 PCM
> 音轨** + 1 条 `rtmd` 数据轨，并且 format tag 里带 `creation_time`。
> `rtmd` 是 Sony 的设备/镜头元数据轨，音轨是现场收音。
> `A`、`B`、`synthetic` 三段都只用 `-map 0:v:0` 生成，实测**只剩一条视频流**，
> 已核对 format tag 里没有路径、没有 `creation_time`、没有设备型号 —— 适合公开。

**如果不想公开相机画面：`synthetic/xavc-shape-synthetic.mp4`（291 KB）**
纯合成，291 KB，形状一致（leading=3）。维护者也可以自己跑 `make-synthetic-clip.ps1`
重新生成一份——生成过程完全确定，不依赖任何外部素材。

**`source/` 与 `generated/A`、`generated/B` 三者是同一段码流的三种封装**，
一起给能让维护者一眼看出"容器不是变量，形状才是"。

## 复现步骤

### 0. 只需要形状（不跑编码器）

```powershell
.\check-shape.ps1 .\source\20260903_C1170.MP4
```

预期输出 `LeadingPictures = 3`、`PredictedAvhwFrames = 27`。这一步只用 ffprobe
读容器包序列，不解码、不需要 GPU，所以在没有独显的机器上也能跑。

### 1. 真实素材上的复现（需要 NVEncC / QSVEncC 与对应硬件）

```powershell
NVEncC64.exe -i .\source\20260903_C1170.MP4 --avhw -c raw --output-res 64x64 -o NUL
#   预期（缺陷）：encoded 27 frames
NVEncC64.exe -i .\source\20260903_C1170.MP4 --avsw -c raw --output-res 64x64 -o NUL
#   预期（正确）：encoded 30 frames

QSVEncC64.exe -i .\source\20260903_C1170.MP4 --avhw -c raw --output-res 64x64 -o NUL
#   预期（缺陷）：encoded 27 frames
QSVEncC64.exe -i .\source\20260903_C1170.MP4 --avsw -c raw --output-res 64x64 -o NUL
#   预期（正确）：encoded 30 frames
```

`synthetic/xavc-shape-synthetic.mp4` 同理，把 30 换成 93、27 换成 90。

### 2. 重新生成所有素材

```powershell
.\make-synthetic-clip.ps1                      # 纯合成，~291 KB
.\make-control-fixtures.ps1                    # A/B/C/D（A、B 各约 10.6 MB）
.\make-control-fixtures.ps1 -SkipLarge         # 只要 C 和 D
```

三个脚本都接受 `-Ffmpeg` / `-Ffprobe` 指定可执行文件路径，默认从 `PATH` 找。

## `make-synthetic-clip.ps1` 做了什么（为什么这样能造出形状）

编码器**不会**在片子开头产生 leading pictures —— 第一帧永远是 IDR，PTS 为 0。
XAVC 之所以有，是因为相机是**从 GOP 中间开始写**的：第一个解码包是一个 CRA，
它的呈现时间在它后面的几张 leading picture **之后**。

所以要合成这个形状，必须三步：

1. **用 open-GOP 编码**，让 GOP 边界是 CRA + leading picture（`open-gop=1` 加 B 帧）；
2. **在某个 CRA 处用 `-c copy` 下刀** —— 跟在 CRA 后面、呈现时间更早的那几张
   leading picture 会被原样保留下来；
3. **把时间戳整体平移到非负**，让形状和 XAVC 一样（XAVC 靠 edit list 达到同样效果）。

脚本会自动从第 3 个关键帧开始向后试，直到拿到 3 张 leading picture（与 XAVC HS 的
数量一致）；拿不到就退而取第一个可用的，并给出 WARNING。

颗粒度说明：默认 640×480。**这不是省事，是刻意的** —— 缺陷由流形状驱动，与分辨率
无关，而这个尺寸下 x265 恰好把 CRA 放成"切完剩 3 张 leading picture"，成品只有
291 KB。`-Width 1920 -Height 1080` 也能出片（约 1.9 MB，leading=2），需要更高分辨率的
话自己加参数即可。

## 尚未验证（不要当成已验证）

* **真机 `--avhw` 帧数没有在本包中确认。** 本包只做到"容器层面的形状与预测值"。
  预测式在整份语料上验证过，但每台机器上仍需自己跑一次上面 §1 的命令确认。
* `A`、`B`、`C`、`D` 的 **leading 数量（3 / 3 / 0 / 0）** 与归档记录逐条一致；但本包
  是从 30 帧的原视频 `C1170` 生成的，而归档里的那批是从更长的 360 帧源生成的，
  所以**样本数不同**（30 对 360）。形状与结论一致，绝对帧数不同。
* `synthetic/` 那段片子是本次新造的构造物。它按设计具备触发所需的流形状
  （已用容器层面判定确认 leading=3），但**没有**在 NVEncC / QSVEncC `--avhw` 上
  实测过帧数。
