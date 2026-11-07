# NVENC 动态 maxrate / VBV 质量验证（P0）

> 日期：2026-11-07  
> 范围：NVENC HEVC（NVEncC 9.31），static profile vs `x265_scaling.json` dynamic_vbv  
> 结论：**v0.9.4 发布档位下未测到质量回归；动态上限在代表性真实素材上未触发。**  
> 状态：P0 验证完成，未改代码、未重新打包。

## 1. 目标

验证“NVENC 的 max_bitrate / vbv_bufsize 跟随 x265 动态表”是否会导致实际画质下降。

分别测试：

1. **发布档位矩阵**：当前 `nvenc.json` 四档，static（旧静态表）vs dynamic（新动态表）；
2. **上限触发压力矩阵**：用非发布参数人为把 QVBR 需求推高，使 VBV 上限真正生效，观察上限对画质的影响。

## 2. 方法

- 源素材：`F:\Pictures\2026`，**只读**；没有向该目录写入任何内容。
- 编码：NVEncC 9.31，`--avsw` 软件解码，HEVC 4:2:0 10-bit，MOV 中间容器。
- 每段取源文件前 **8 秒**（NVEncC `--trim 0:N`），不产生中间源拷贝。
- 发布档位：`UHQ / HQ / SMALL / FAST`，各做 static/dynamic 两次编码。
- 指标：VMAF、SSIM、PSNR（相对同一源片段），输出视频码率、编码 wall time。
- 源分类：完整源文件按 `x265_scaling.json` 的 normalized OB 分类，而不是按截短片分类。
- 输出与统计：
  - `F:\_1kt_nvenc_vbv_test\results.json`（发布档位 32 次编码）
  - `F:\_1kt_nvenc_vbv_test\stress_results.json`（压力 24 次编码）
  - `summary_normal.csv`、`summary_stress.csv`、`enc/`、`logs/`、`metrics/`

### 2.1 测试源

| 场景 | 分辨率/帧率 | 像素格式 | 源 OB | 分类 |
|---|---|---|---|---:|---|
| `low_4k30_12m` | 3840×2160@29.97 | yuv420p | 12,496 kbps | LOW_BITRATE_LONG_GOP |
| `low_4k60_37m_422` | 3840×2160@59.94 | yuv422p10le | 37,180 kbps | LOW_BITRATE_LONG_GOP |
| `normal_4k60_91m` | 3840×2160@59.94 | yuv420p10le | 91,140 kbps | NORMAL_LONG_GOP |
| `high_4k60_162m` | 3840×2160@59.94 | yuv420p10le | 162,072 kbps | HIGH_BITRATE_LONG_GOP |

选源时覆盖了 4K30/4K60、8/10-bit、4:2:0/4:2:2、低/中/高码率；这批素材没有找到可用的 HDR 片段，HDR 是本次限制之一。

## 3. 发布档位矩阵：32/32 成功

完整数据见 `summary_normal.csv`。核心结论：

| 场景 | 实际码率范围 | static 上限 | dynamic 上限 | VMAF 差值 |
|---|---:|---:|---:|---:|
| high_4k60_162m | 7.7–10.6 Mbps | 60–100 Mbps | 64.8–113.5 Mbps | **0.0000** |
| low_4k30_12m | 5.7–9.8 Mbps | 40–100 Mbps | 11.2–12.5 Mbps | **-0.0005 ~ +0.0005** |
| low_4k60_37m_422 | 6.8–10.8 Mbps | 40–100 Mbps | 33.5–37.2 Mbps | **0.0000** |
| normal_4k60_91m | 24.2–49.3 Mbps | 40–100 Mbps | 50.1–77.5 Mbps | **-0.0008 ~ +0.0034** |

汇总：

- **最大 |ΔVMAF| = 0.0034**
- **最大 |ΔSSIM| = 0.000004**
- **最大 |Δ码率| = 0.212%**
- 16 组中 3 组 static/dynamic 输出字节完全相同；其余码率相同或几乎相同，哈希差异来自 NVENC 本身的重编码非确定性，不是质量差异。
- 即使 dynamic 上限在某些组合显著低于 static（例如低码率源 UHQ：100 Mbps → 12.5 Mbps），实际 QVBR 输出只有约 9.8 Mbps，**上限根本没有触发**。

这与设计预期一致：`qvbr` 是主质量控制，maxrate/VBV 只是上限；当实际上限远高于需求时，dynamic 改动不会改变结果。

## 4. 压力矩阵：让 VBV 上限真正生效

为了验证“上限触发时是否会造成不可接受的画质下降”，使用非发布参数：

```text
qvbr = 10（发布档位为 23–28）
qp_min = 0（释放 QP 质量下限）
```

其余 profile 参数、分辨率、帧率、源片段与发布矩阵相同。共 24 次编码，全部成功。

### 4.1 低码率 4K30 源（`low_4k30_12m`）

| 档位 | static 码率 → dynamic 码率 | ΔVMAF | ΔSSIM |
|---|---:|---:|---:|
| UHQ | 95.7 → 12.4 Mbps | **-0.0646** | -0.002568 |
| HQ | 79.5 → 12.4 Mbps | **-0.0659** | -0.002403 |
| SMALL | 40.1 → 11.2 Mbps | **-0.1115** | -0.001786 |
| FAST | 60.6 → 11.8 Mbps | **-0.1028** | -0.002084 |

动态上限把码率压低约 72–87%，但 VMAF 仅下降约 0.06–0.11，绝对 VMAF 仍 >99.86；这是“源本身很简单 + 上限远低于需求”的边界情况。

### 4.2 低码率 4K60 4:2:2 源（`low_4k60_37m_422`）

| 档位 | static 码率 → dynamic 码率 | ΔVMAF | ΔSSIM |
|---|---:|---:|---:|
| UHQ | 86.8 → 35.1 Mbps | **-0.1540** | -0.000385 |
| HQ | 72.2 → 35.0 Mbps | **-0.1309** | -0.000316 |
| SMALL | 37.3 → 31.6 Mbps | **-0.0354** | -0.000080 |
| FAST | 55.8 → 33.2 Mbps | **-0.1093** | -0.000231 |

4:2:2 输入能正常编码为 4:2:0；动态上限触发时画质下降仍在 ~0.15 VMAF 内。

### 4.3 正常码率 4K60 源（`normal_4k60_91m`）

| 档位 | static 码率 → dynamic 码率 | ΔVMAF | ΔSSIM |
|---|---:|---:|---:|
| UHQ | 100.9 → 77.7 Mbps | **-0.4176** | -0.000866 |
| HQ | 80.7 → 68.6 Mbps | **-0.3038** | -0.000572 |

这是本次压力测试中最大的质量下降；对应码率下降约 23%/15%，动态上限收紧明显。绝对 VMAF 仍为 97.40 / 97.18，绝对 SSIM >0.9945。

### 4.4 高码率 4K60 源（`high_4k60_162m`）

| 档位 | static 码率 → dynamic 码率 | ΔVMAF | ΔSSIM |
|---|---:|---:|---:|
| UHQ | 99.5 → 113.3 Mbps | **+0.0247** | +0.000140 |
| HQ | 78.2 → 79.6 Mbps | **+0.0089** | +0.000020 |

高码率类的动态上限高于或接近 static，实际输出略增，画质轻微提升，没有回归。

## 5. 结论

### 5.1 对 v0.9.4 发布档位

**未测到质量回归。**

- 代表性真实源上 dynamic 上限均未触发；
- 最大 VMAF 差 0.0034，SSIM 差 4e-6，属于不可感知/测量噪声级别；
- 因此 v0.9.4 中“NVENC follow x265 VBV”的改动在本次样本范围内是安全的。

### 5.2 压力测试的含义

当上限被迫生效时：

- 低码率类：码率大幅下降，但画质下降仅约 0.06–0.15 VMAF；
- 正常码率类：上限收得最紧，VMAF 下降约 0.30–0.42；
- 高码率类：动态上限反而更高，质量略增。

结论不是“动态上限无害”，而是：

> **发布档位的 QVBR 需求通常低于动态上限；只有高复杂度/高 QVBR 需求场景才会真正触发上限。触发时，低码率类约 -0.1 VMAF，正常码率类约 -0.3~-0.4 VMAF。**

## 6. 限制与后续

本次未覆盖：

- HDR / HLG / PQ 源（这批抽到的测试源均为 bt709 SDR）；
- 更长的整片编码（本次每段 8 秒）；
- 真实低码率但高细节/高噪声源（压力测试用 qvbr10 + qp_min0 代替）；
- x265 同条件参考编码（本次重点是 static vs dynamic NVENC，不比较跨编码器）；
- QSV（保持静态表，不在本次范围）。

建议：

1. 当前 v0.9.4 可按现有动态表继续保留，无需因本次结果修改；
2. 后续补 HDR、低码率强纹理/噪声真实素材，重复本矩阵；
3. 如果未来真实低码率复杂源在发布档位下频繁触发上限，再考虑为 LOW 类增加 maxrate 下限或按复杂度分类细化；
4. 不建议根据 `qvbr10/qp_min0` 压力结果直接改动发布表，因为它不代表发布工作点。

## 7. 复现

```powershell
python F:\_1kt_nvenc_vbv_test\run_matrix.py
python F:\_1kt_nvenc_vbv_test\stress_matrix.py
python F:\_1kt_nvenc_vbv_test\analyze.py
```

原始数据：

- `F:\_1kt_nvenc_vbv_test\results.json`
- `F:\_1kt_nvenc_vbv_test\stress_results.json`
- `F:\_1kt_nvenc_vbv_test\summary_normal.csv`
- `F:\_1kt_nvenc_vbv_test\summary_stress.csv`

源目录 `F:\Pictures\2026` 仅被读取；所有输出均写入 `F:\_1kt_nvenc_vbv_test`。
