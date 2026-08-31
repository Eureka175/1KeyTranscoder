# AV1 档位标定 (SVT-AV1 / NVENC AV1 / QSV AV1)

> 状态: **标定完成** (2026-08-31)。全部数值出自 `work/av1_calib/`
> (标定脚本 `calib.py`, 素材 `work/av1_calib/src`, 汇总
> `results.csv` / `results_table.md`)。testsets 源文件全程只读
> (仅 6s 视频流片段复制到 work/)。

## 1. 标定范围与方法

- **软件 AV1**: SVT-AV1 Encoder Lib v4.2.0 (ffmpeg 9.0.1 内置
  libsvtav1), `--encoder svtav1` + `svtav1.json` + `svtav1_scaling.json`。
- **硬件 AV1**: NVEncC 9.31 (nvenc-av1) / QSVEncC 8.26 (qsv-av1),
  档位 JSON 与 HEVC 同构 (QVBR / ICQ)。
- **素材** (testsets 只读, 6s 视频流片段):
  | 片段 | 源 | 特征 |
  |---|---|---|
  | car_6s | 车内高晃动适中噪点 (4K60 HEVC 150M) | 高晃动 + 适中噪点 — 区分度主力 |
  | night_6s | 夜间手持高噪点 (4K60 HEVC 150M) | 重度噪点 — 码率极端场景 |
  | day_6s | 昼间手持几乎无噪点-稳定 (4K60) | 干净稳定 — 高压缩率场景 |
  | c9037 | Sony XAVC S 4K30 4:2:2 10-bit | 4:2:2→4:2:0 降级路径 |
- **指标**: VMAF v0.6.1 (4K 近透明段饱和, 粗判), **XPSNR 主判据**
  (AV1 社区感知指标), PSNR/SSIM 参考, 码率/耗时。
- **参照锚点**: x265.json 各档主导参数 + 原硬件 AV1 档位
  (nvenc qvbr 28/31/35/32, qsv icq 24/26/30/28)。

## 2. 调研结论 (文档/community 依据)

详见 `docs/reference/svt-av1/SVT-AV1_archival_tuning_report.md`
(全部来源带 URL)。要点:

1. **CRF 刻度不同**: SVT crf ≈ x265 crf + 9 (1080p 社区规则);
   4K 透明带约 crf 18-24, 好质量 24-30, 小体积 30-36。
2. **preset 甜点**: 4K 上 preset 4-6 实用 (Fora Soft 2026:
   preset 8→4 只多 ~6% BD-rate, 速度 30→4.5fps; preset 2 再多 1%
   却慢 3.7x)。10-bit 比 8-bit 慢 40-140%。
3. **tune 0 (VQ)** 为真实内容归档首选; `--crf` 强制 aq-mode 2。
4. **enable-qm=1 + qm-min=0** = psy-rd 对应物; **ac-bias** (v4.0+)
   高频纹理 RD 偏置; **enable-overlays=1** 提升基础层质量。
5. **无 min-keyint/scenecut 关键帧** (`--scd` 只管码率分配) —
   按 SVT 特性接受。
6. **mbr 软上限** (社区 "largely unreliable"), 非 VBV 硬钳。
7. **film-grain** 颗粒合成见 §5.3。
8. **4:2:0 only**; 4:2:2 源 WARNING 后降采样, 不用 AOM。

## 3. 转译表 (x265 设计哲学 → SVT-AV1)

| x265 | SVT-AV1 | 说明 |
|---|---|---|
| preset slow/slow/slow/fast | preset 2/4/4/7 | UHQ 基准 p2; p4-6 为 4K 甜点; FAST p7 |
| crf 20/21/23/22 | crf 20/24/30/32 | 刻度不同, 实测标定 |
| keyint 600 | keyint FR*10 | 10s GOP; 高帧率封顶 600 |
| min-keyint / scenecut | — | SVT 无场景关键帧 (文档明确), scd=1 只管码率 |
| bframes | pred-struct 2 (层级 alt-ref) | AV1 无显式 B 帧 |
| psy-rd | tune 0 + enable-qm(qm-min 0) + ac-bias | 感知纹理保留栈 |
| aq-mode 4 | aq-mode 2 (+variance-boost 仅 UHQ) | CRF 强制 aq-mode 2 |
| cutree | TPL 恒开 + enable-tf (MCTF) | 不可关 |
| sao | enable-cdef + enable-restoration | AV1 无 SAO |
| deblock | enable-dlf (无 tC/β 偏移) | 无直接对应 |
| qcomp | qp-scale-compress-strength | 近似 |
| vbv-maxrate/bufsize | mbr (软上限) | 无 VBV 缓冲参数 |
| me/merange/ref/ctu | preset 内控 | 无用户旋钮 |

## 4. 实测数据 (car_6s 主判据, 4K60 高晃动适中噪点)

| 配置 | kbps | XPSNR | 编码耗时 |
|---|---|---|---|
| **SVT UHQ** p2 crf20 (全 psy+vb+acbias) | 40.2M | **45.77** | 188s |
| SVT HQ p4 crf24 +vb (对照) | 27.6M | 45.13 | 67s |
| **SVT HQ** p4 crf24 novb+acbias | 19.6M | **44.73** | 48s |
| **SVT SMALL** p4 crf30 novb+acbias | 14.7M | **44.34** | 49s |
| **SVT FAST** p7 crf32 (无 vb/acbias) | 15.1M | **43.49** | 25s |
| SVT p4 crf38 基线 | 9.2M | 43.55 | 46s |
| x265 UHQ (c9037) | 15.2M | 42.71 | 97s |
| x265 HQ | 15.0M | 44.01 | 168s |
| x265 SMALL | 15.0M | 44.03 | 154s |
| x265 FAST | 18.6M | 43.55 | 44s |
| nvenc-av1 qvbr 26-30 (平台) | 27.2M | 42.28 | ~7s |
| nvenc-av1 qvbr 38 | 17.9M | 41.91 | 8s |
| nvenc-av1 CQP26 (天花板探针) | 776M | 44.66 | 8s |
| qsv-av1 icq 22 / 23 / 24 | 35.5/28.6/23.0M | 42.36/42.21/42.04 | ~21s |
| qsv-av1 icq 26 / 28 / 32 | 15.7/12.4/7.3M | 41.63/41.25/40.17 | ~21s |

完整表格: `work/av1_calib/results_table.md`。

### 关键发现

1. **variance-boost 在噪点内容上是码率黑洞**: +40% 码率换 +0.2
   XPSNR (27.6M vs 19.6M @ crf24) → 仅 UHQ 启用。
2. **ac-bias 1.0 免费** (实测 -14% 码率, XPSNR 持平)。
3. **film-grain 8/12 即使加 denoise 也几乎不动码率** (112→111Mbps);
   fg20+denoise 才 -54% (XPSNR -1.6, VMAF 持平) → 归档保真默认
   fg=0, fg20 作为极噪素材的体积选项留档 (§5.3)。
4. **NVENC AV1 QVBR 平台**: qvbr 26-30 在噪点内容输出一致
   (27.2Mbps/XPSNR 42.28) — QVBR lookahead 近似保守; CQP26 可达
   44.66 但 776Mbps (不实用)。档位间距重定: 26/30/38/34。
5. **QSV icq 阶梯天然单调** (23→32 每步 ~0.6 XPSNR), UHQ 用 icq
   23 对齐 NVENC UHQ 质量带 (42.21 vs 42.28)。
6. **tune 在 QVBR 下作用极小** (uhq +0.05 XPSNR), 按 NVIDIA 指南
   保留 (CQP 模式才不写 tune)。

## 5. 档位定案与依据

### 5.1 SVT-AV1 (`svtav1.json`)

| 档 | preset | crf | 独有参数 | car_6s 实测 | 依据 |
|---|---|---|---|---|---|
| UHQ | 2 | 20 | variance-boost, ac-bias 1.0, max-tx 32, qpscs 2 | 40.2M @ 45.77 | 质量标杆: 比 HQ +1.0 XPSNR @ +105% 码率 +3.9x 耗时, 非生产实用 ✓ |
| HQ | 4 | 24 | ac-bias 1.0, qpscs 1, 无 vb | 19.6M @ 44.73 | 慢速生产: x265 HQ +0.7 XPSNR @ +31% 码率; 干净内容 (day) 同尺寸 +0.1 |
| SMALL | 4 | 30 | ac-bias 1.0, 无 vb | 14.7M @ 44.34 | HQ -25% 体积, -0.4 XPSNR; 慢速生产挡 ✓ |
| FAST | 7 | 32 | 无 vb/ac-bias | 15.1M @ 43.49, ~17fps | 快速批量: 画质超硬件 AV1 带 (42.3) +1.2 XPSNR, x265 FAST 同质 -19% 体积 |

共同: tune 0, keyint FR*10, scd 1, irefresh-type 2, lookahead
FR*2(封顶 120), enable-tf/overlays/qm(qm-min 0), film-grain 0,
cdef/restoration/dlf on, pred-struct 2。

### 5.2 硬件 AV1 重标定

**nvenc-av1** (`nvenc_av1.json`): qvbr **26/30/38/34** (原 28/31/35/32)。
- UHQ 26 = QVBR 平台起点 (再低无增益); HQ 30 干净内容 -27% 码率;
  SMALL 38 噪点 -34%/干净 -73%; FAST 34 (轻 lookahead 16/bframes 4)。
- 已知限制: 噪点内容 UHQ/HQ/FAST 输出趋同 (平台 42.28), SMALL 才
  明显分离 — 硬件 QVBR 特性, JSON 注释已声明。

**qsv-av1** (`qsv_av1.json`): icq **23/26/32/28** (原 24/26/30/28)。
- UHQ 23 对齐 NVENC UHQ 带 (42.21 vs 42.28); SMALL 32 (体积优先);
  FAST 28 (quality balanced)。icq 阶梯单调, 无平台问题。

### 5.3 film-grain 决策 (night_6s, crf 24)

| film-grain | denoise | kbps | VMAF | XPSNR |
|---|---|---|---|---|
| 0 | — | 112.0M | 93.50 | 36.38 |
| 8 | 1 | 111.7M | 94.05 | 36.32 |
| 12 | 1 | 111.0M | 94.50 | 36.17 |
| 20 (crf 30) | 1 | 45.1M | 92.72 | 34.71 |

fg8/12 无码率收益; fg20+denoise -54% 码率, VMAF 持平但 XPSNR
-1.6 (真实颗粒被合成颗粒替代)。**定案: 全档 film-grain=0** (归档
保真, 与 x265 无降噪哲学一致); fg20+denoise 作为极噪素材的体积
选项记录在案, 用户按需在配置中启用。

## 6. 遗留与后续

- VMAF 4K 饱和 → 后续可用 SSIMULACRA2 (外部工具) 细化区分度;
- 场景关键帧缺失: 需要时走 ffmpeg -force_key_frames + scdet 两趟,
  归档暂不需要;
- SVT UHQ (preset 2) 4K60 实测 188s/6s ≈ 2.2fps, 与 "非生产实用"
  定位一致;
- 工具升级路径: ffmpeg 更新会带新 SVT-AV1 (4.x 迭代快, 档位需
  按新版本回归)。
