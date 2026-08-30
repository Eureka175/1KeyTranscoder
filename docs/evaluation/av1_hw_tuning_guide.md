# AV1 硬件后端调参指南：NVENC / QSV / VCE 支持度与 HEVC→AV1 参数翻译

> 配套文档：`av1_feasibility_report.md`（同目录，可行性总报告）。
> 本指南产出三件事：①三后端 AV1 支持度矩阵（本地工具实测 + 联网调研）；
> ②以项目现有成熟 HEVC 档（nvenc.json / qsv.json / vce.json）为基准的
> **逐键参数翻译表**；③可直接落盘的 AV1 预设 JSON 草案。
> 本地工具版本：NVEncC 9.31（r4047）、QSVEncC 8.26、VCEEncC 9.12（AMF 1.5.0）。

---

## 1. 三后端 AV1 支持度矩阵（本机实测 + 官方文档交叉验证）

### 1.1 编码能力

| 维度 | NVENC AV1（Ada/Blackwell） | QSV AV1（Arc/Battlemage/MTL+/ARL+） | VCE AV1（RDNA3/VCN4+） |
|---|---|---|---|
| 硬件代际门槛 | RTX 40+（RTX 30 仅解码） | Arc Alchemist 起；Tiger Lake 仅解码 | RX 7000 起；RDNA4 增强 |
| 色彩格式 | 4:2:0 8/10bit（nv12/yv12/yv12(10bit)） | 4:2:0 8/10bit | 4:2:0 8/10bit（NV12/P010） |
| 4:2:2 / 4:4:4 编码 | ❌ | ❌ | ❌ |
| profile | ⚠️ **rigaya 映射：main=8bit、high=10bit**（本机实测：`--profile main --output-depth 10` 静默产出 yuv420p 8bit；high/auto 才得 yuv420p10le。产物 spec 上均为 AV1 Main） | auto/main/high/pro 列表存在但 **high/pro 实测报 invalid video parameters，只用 main**（10bit 也走 main） | main（10bit 靠位深表达） |
| tier | ❌ **无 AV1 行**（help 仅 HEVC: main/high），不写 | ❌ **`[HEVC only]`，AV1 不写** | ❌ 无 tier 旗标 |
| level | auto/2/2.1/3/3.1/4/4.1/5~6.1 | auto/2~7.3（官方文档全列；本地 8.26 帮助仅列到 3，写 auto 最稳） | auto/2/2.1/2.2/2.3/3 |
| 本机实测编码 | ✅ 4K60 73fps（10bit） | ✅（10bit，LP/FF 路径） | 本机无 AMD GPU，未实测 |
| B 帧 | ⚠️ **非层级 ≤7（本机实测）；≤31 需层级化+SDK13.1+** | 支持（BFrame/GopRef/B_Pyramid=o） | RDNA3 无；**RDNA4 才加入 B 帧** |
| 分辨率上限 | 8192×8192 | 未标注 | 未标注 |

> ⚠️ **本机实测修正（2026-08）**：NVENC AV1 在**非层级 GOP（bref-mode each/middle）
> 下 B 帧上限为 7**（"number of B-frames must be <= 7"，实测 bframes 16 报错）；
> `--bref-mode hierarchical` 报错 "requires NVENC API 13.1 or later"（本机当前
> 驱动/API 组合不可用）。→ 档位用 **bframes ≤7 + bref-mode middle/each**。
> 实测通过的组合：`--cqp 22:24:26 --aq --aq-strength 6 --aq-temporal
> --lookahead 32 --bframes 7 --bref-mode middle --refs-forward 4
> --refs-backward 3 --tile-columns 2`（720p60 实测 194fps）。
> **注意该组合当时用了 `--profile main`，产物为 8bit**；10bit 必须
> `--profile high`（见 1.1 profile 行）。QVBR 0-63 标尺实测可用；QSV AV1
> `--function-mode FF --icq 22 --b-pyramid` 实测可用。

### 1.2 速率控制（RC）可用矩阵

| RC 模式 | NVENC AV1 | QSV AV1（Arc FF 特性表实测） | VCE AV1 |
|---|---|---|---|
| CQP | ✅ | ✅ | ✅ |
| CBR / CBRHQ | ✅ / ⚠️ | ✅ / — | ✅ / ✅ |
| VBR / VBRHQ | ✅ / ⚠️ | ✅ / — | ✅ / ✅ |
| QVBR | ⚠️（**硬件 RC 仅 CQP/CBR/VBR**；QVBR 为软件近似，本机实测可用） | **❌ x**（工具列旗标但 Arc FF 不可用） | ✅（0-51 标尺，依赖 PreAnalysis，VCEEncC 自动开启） |
| VBR-quality | ✅（0-63 标尺） | — | — |
| ICQ / LA-ICQ | — | ✅ / ❌（旧 `--la` 系列不可用；**新用法 `--la-depth` 叠 vbr/cbr/icq + `--extbrc`**） | — |
| AVBR | — | ❌ x | — |
| LA（lookahead） | ✅ `--lookahead 1-32` | ⚠️ 旧 `--la` 系列全 x；新用法 `--la-depth`+`--extbrc`（实测 gop-ref-dist 生效） | PA 预分析（见下） |
| multipass | ✅（VBR/CBR） | — | — |

### 1.3 后端专属 AV1 工具

- **NVENC**：`--part-size-min/max`（亮度划分块）、`--tile-columns/--tile-rows`
  （默认 0=auto）、`--refs-forward/--refs-backward`（前/后向参考帧数，AV1 版
  `--ref`）、`--bitstream-padding`、`--bref-mode hierarchical`（API 13.1+）、
  HDR：`--max-cll`/`--master-display`。（`--atc-sei` 官方文档无 AV1 对应，
  AV1 档不写。）
- **QSV**：`--tile-row/--tile-col`；**AV1 只走 LP/FF 固定功能路径**
  （`--function-mode FF` / `--fixed-func`；PG 路径全 x），早期驱动有时间戳/
  稳定性 bug（QSVEnc #87/#96、vpl-gpu-rt #253），需新驱动 + 显式 FF。
- **VCE**：`--aq-mode`（AV1 AQ 模式）、`--cdef-mode`（CDEF，默认 auto）、
  `--cdf-update`/`--cdf-frame-end-update`、`--screen-content-tools`（屏幕内容
  工具，录屏类素材有用）、`--tiles`（每帧 tile 数）、`--adapt-minigop`
  [H.264/AV1]、`--temporal-layers`、`--multi-instance`。
  **`--vbaq` 仅 [H.264/HEVC]，AV1 用 `--aq-mode` 替代**。PA 预分析为复合
  选项 `--pa key=value,...`（activity-type/initqpsc/scene 检测等）。

---

## 2. HEVC→AV1 参数翻译表（逐键）

图例：✅ 直接等价 / 🔄 换旗标或换值 / ➖ 无等价物，AV1 不写 / 🆕 AV1 新增。

### 2.1 nvenc.json → nvenc_av1.json（NVENC）

| HEVC 键（现档值） | 状态 | AV1 翻译与理由 |
|---|---|---|
| preset（quality/default） | ✅ | `--preset` 同为 default/performance/quality，原值沿用 |
| tune（uhq/hq） | 🔄 | **CQP 路线不写 tune**（[NVIDIA 论坛实测：AV1 CQP 模式下调 tune 反而降画质](https://forums.developer.nvidia.com/t/nvencs-tune-parameter-results-in-lower-quality-in-constqp-mode-for-av1-encoding/331787/2)，tune 面向 VBR RC 设计）；QVBR/VBR 路线沿用 hq/uhq |
| profile（main10） | 🔄 | **10bit 必须写 `high`**（rigaya 映射 main=8bit/high=10bit，本机实测 main+10bit=静默 8bit）；8bit 档才写 main |
| tier（high） | ➖ | NVEncC AV1 无 tier 行（help 仅 HEVC），不写 |
| level（6.1） | ✅ | AV1 level 支持到 6.1，沿用（或 auto） |
| output_depth（10） | ✅ | AV1 支持 8/10，沿用 10 |
| qvbr（23/25/28/26） | 🔄 | 两选一：**CQP 路线**（推荐，行为最可预测）或 **QVBR 0-63 标尺换算**（HEVC 0-51 → AV1 ≈ ×63/51≈×1.24：23→28、25→31、28→35、26→32，需标定验证）。⚠️ **AV1 硬件 RC 仅 CQP/CBR/VBR**，QVBR/VBRHQ/CBRHQ 是 lookahead 软件近似 |
| max_bitrate / vbv_bufsize | ✅ | 旗标同名，VBR/QVBR 下生效，沿用 |
| aq / aq_strength / aq_temporal | ✅ | AV1 有 AQ（空域+时域），沿用（strength 档位一致） |
| lookahead（32） | ✅ | `--lookahead 1-32` AV1 可用（Ada/Blackwell）；**lookahead_level 仅 Blackwell（SDK13.0+）**，本机 5070 可用性靠白名单+实测 |
| bframes（5） | 🔄 | **本机实测：非层级 GOP 下 ≤7**（bframes 16 报错 "<= 7"）；层级化（hierarchical+31 B 帧）需 Blackwell+SDK13.1+新驱动，本机当前组合不可用。UHQ/HQ 用 7，FAST 用 4 |
| bref_mode（middle/each） | 🔄 | 用 middle/each（实测可用）；`hierarchical` 需 Blackwell/SDK13.1，本机报错 |
| ref（4-5） | 🔄 | AV1 换 `--refs-forward` / `--refs-backward`（AV1 专属）；`--multiref-l0/l1` 无 AV1 对应 |
| tf_level（4/off） | ➖ | AV1 支持需 SDK13.0/driver 570+，本机组合未验证 → 档位不写（层级 B 帧取代） |
| nonrefp | ➖ | HEVC 概念，不写 |
| mv_precision（q-pel） | ➖ | 旗标存在但 AV1 语义存疑，档位不写 |
| chroma_qp_offset（-1/-2） | ➖ | AV1 无对应，不写 |
| qp_init / qp_min / qp_max（三元组） | ✅ | 仅 CQP 档写（I:P:B 三元组，`--cqp` 默认 23:25:auto） |
| gop_len（0=auto） | ✅ | 沿用 0（auto） |
| aud / repeat_headers / pic_struct | ➖ | HEVC NAL 概念，AV1 不写 |
| atc_sei（auto） | ➖ | 官方文档无 AV1 对应（nvencc.py 注释"atc_sei 是 AV1"存疑，以 NVEncC_Options.en.md 为准：AV1 档不写） |
| split_enc/parallel/output_buf/cuda_schedule/avoid_idle_clock/avhw | ✅ | 会话级，同名沿用 |
| 🆕 tile_columns / tile_rows | 🆕 | 默认 0=auto；4K 可显式 2/0 |
| 🆕 refs_forward / refs_backward | 🆕 | 见上 ref 行 |
| 🆕 part_size_min/max | 🆕 | 默认 auto，一般不写 |
| 🆕 bitstream_padding | 🆕 | 可开（字节对齐），归档非必需 |
| 🆕 max_cll / master_display | 🆕 | HDR 素材用（与源一致） |

### 2.2 qsv.json → qsv_av1.json（QSV）

| HEVC 键（现档值） | 状态 | AV1 翻译与理由 |
|---|---|---|
| tu（best/balanced）+ tu_level | 🔄 | **QSVEnc 无 `--tu` 旗标**（现有 HEVC 档的 tu 键已被白名单跳过，见 README 已知限制）；TU 的载体是 **`--quality`**（best/higher/high/balanced/fast/faster/fastest，默认 balanced），AV1 可用 → 写 `"quality": "best"` 等 |
| icq（21/22/26/24） | 🔄 | AV1 ICQ 可用但**刻度 0-255（HEVC 1-51），数值不可照搬，必须重标定**（起点建议 icq 24）；⚠️ [ICQ 因子在 4032 驱动后对 AV1 有变更](https://github.com/rigaya/QSVEnc/issues/108)，跨驱动需重标；QSVEnc ≥7.21 才放开 255 上限 |
| profile（main10） | 🔄 | AV1 写 `main`；high/pro 实测报 invalid video parameters（10bit 也在 main 内） |
| tier（high） | ➖ | **`[HEVC only]`**，AV1 不写 |
| level（6.1） | 🔄 | AV1 level auto/2~7.3，写 auto |
| output_depth（10） | ✅ | 沿用 10 |
| max_bitrate / vbv_bufsize | 🔄 | 沿用；⚠️ 早期驱动下 max-bitrate 仅对 VBR 有意义（对 ICQ/CQP 配合 mbbrc 生效，随驱动演进，标定时验证） |
| bframes（8） | 🔄 | **`--bframes` 标注 [H.264/HEVC/MPEG2]，AV1 静默忽略** → 换 **`--gop-ref-dist`（=bframes+1，默认 8）**；rigaya B 帧专项：AV1 最优 ≈ `-b 7` 即 gop-ref-dist 8。本机实测 4/8/16 → 2047/1732/1702KB，8 为甜点 |
| b_pyramid | ✅ | AV1 **默认已开启**（B_Pyramid=o），可显式写 true |
| ref（8） | ✅ | 通用旗标，AV1 可用但官方注明收益有限 |
| adaptive_i / adaptive_b | ✅ | Adaptive_I/B=o，沿用（配合 la-depth 使用） |
| adaptive_ltr / adaptive_cqm | ✅ | AV1 矩阵 = o；`--adapt-ltr` 需配 `--extbrc` |
| mbrc | 🔄 | AV1 用 **`--mbbrc`**（CBR/VBR/ICQ 有效，CQP=x）；⚠️ 早期驱动"探测 o 但实际无效果"（issue #96），新驱动+EncTools 生效 |
| scenario（archive） | ✅ | `--scenario-info` 对 AV1 = o；`game_streaming` 可触发 EncTools BRC（对归档素材保持 archive） |
| sao（all） | ➖ | `--sao` 标注 [hevc]，AV1 矩阵 SAO=x，不写（AV1 的 CDEF/还原滤镜不可调） |
| ctu_size（64） | ➖ | `--ctu` [HEVC]；AV1 超级块固定，不写 |
| transform_skip / weight_p / weight_b / gpb | ➖ | HEVC 概念，AV1 矩阵均 x（静默无效），不写 |
| aud | ➖ | 不写 |
| qp_min / qp_max（三元组） | 🔄 | AV1 矩阵 QP Min/Max=o 但**仅 CBR/VBR/ICQ**（CQP 为 x）；ICQ 档可写，CQP 档不写 |
| open_gop | ➖ | AV1 无此概念，不写 |
| hyper_mode（adaptive） | ➖ | ⚠️ 需 iGPU+dGPU Deep Link 配对（本机理论可行但历史上有 HEVC 10bit 崩溃 bug 且要求小 GOP 才有提速、对压缩率负面）→ AV1 档不写 |
| gop_len（600） | 🔄 | AV1 建议 240-300（5-10s）或 auto |
| async_depth（3）/ output_buf_mb（8） | ✅ | 沿用（输出缓冲/流水线） |
| lookahead/la-depth（40/20/16） | 🔄 | **新用法**：`--la-depth 40` 叠在 icq/vbr/cbr 上 + **`--extbrc`** + `--i-adapt --b-adapt`；**不要用** `--la/--la-icq/--la-hrd`（AV1 不可用） |
| 🆕 extbrc | 🆕 | `--extbrc`（EncTools 外部码控）：ICQ/VBR 档建议开（CQP=x）；la-depth/adapt-ltr/mbbrc 都依赖它 |
| 🆕 function_mode（FF/fixed_func） | 🆕 | **必写**：AV1 只有 FF 路径（全代无 PG），固定 `--function-mode FF` 规避路径切换与历史驱动 bug |
| 🆕 tile_row / tile_col | 🆕 | AV1TILE=o；默认 auto，4K 可显式 |
| 🆕 max_cll / master_display | 🆕 | HDR 全系列 `[HEVC, AV1]` 同名可用 |

### 2.3 vce.json → vce_av1.json（VCE/AMF）

| HEVC 键（现档值） | 状态 | AV1 翻译与理由 |
|---|---|---|
| preset（slow/balanced） | 🔄 | VCEEncC preset 对 AV1 的适用值待实测（白名单兜底） |
| profile（main10） | 🔄 | AV1 写 `main`（**10bit 靠 `--output-depth 10` 表达**，与 NVENC 的 main/high 映射不同） |
| tier（high）/ level（6.1） | ➖/🔄 | AV1 无 tier（删除）；level 写 auto |
| output_depth（10） | ✅ | 沿用 |
| qvbr_quality（23/25/28/26，0-51） | ✅ | **VCE AV1 六种 RC 全可用**（CQP/CBR/CBRHQ/VBR/VBRHQ/QVBR）；QVBR/VBRHQ/CBRHQ 依赖 PreAnalysis（VCEEncC 自动开启）。0-51 同标尺，原值沿用（推荐 VCE AV1 主力 RC） |
| max_bitrate / vbv_bufsize | ✅ | 沿用（AV1 上限 160000 kbps） |
| pa_* 家族 | 🔄 | 复合选项 `--pa key=value,...`；**AV1 支持 PreAnalysis**（paq=caq 与 aq-mode caq 搭配、fskip-maxqp 替代 skip-frame、ltr=true 替代 --ltr）；`--adapt-minigop` 标注 [H.264/AV1] |
| pre_encode | 🔄 | 预分析总开关，待实测 |
| vbaq（true） | ➖ | **`--vbaq` 标注 [H.264/HEVC]**，AV1 改用 **`--aq-mode caq`** |
| ltr（3/off） | 🔄 | `--ltr` 为 HEVC 专属；AV1 用 `--pa ltr=true` |
| motion_estimation（q-pel） | ➖ | HEVC 专属，删除 |
| ref_frames（4-5） | 🔄 | `--ref` 通用（白名单兜底） |
| qp_init / qp_min / qp_max | 🔄 | CQP 档写；⚠️ **AV1 CQP 刻度上限 255（与 HEVC 不同），需重标定** |
| gop_length（0） | ✅ | 沿用 auto |
| aud / repeat_headers | ➖ | 不写 |
| 🆕 bframes / adapt_gop | 🆕 | **仅 RDNA4（VCN5）**，VCEEncC 8.24+；RDNA3 上静默失效 → 按 GPU 代际分支（能力探测） |
| 🆕 aq_mode | 🆕 | `none|caq`（VBAQ 替代，搭配 `--pa paq=caq`） |
| 🆕 cdef_mode | 🆕 | 默认 auto 即可（替代 HEVC 的 deblock 概念） |
| 🆕 cdf_update / cdf_frame_end_update | 🆕 | 默认 auto |
| 🆕 screen_content_tools | 🆕 | 录屏/桌面素材可开 |
| 🆕 tiles / multi_instance / temporal_layers | 🆕 | tiles 4K 可显式；multi-instance 多卡/并行；temporal-layers 上限 4 |

---

## 3. 三份 AV1 预设 JSON 草案（待档位标定）

### 3.1 nvenc_av1.json（NVENC AV1，CQP 路线）

```jsonc
{
  "encoder": "nvenc-av1",
  "codec": "av1",
  "profile": {
    "UHQ":  { "preset": "quality", "profile": "high",
              "level": "6.1", "output_depth": 10,
              "cqp": [20, 22, 24], "qp_min": [18, 20, 22],
              "qp_max": [32, 36, 40], "aq": true, "aq_strength": 6,
              "aq_temporal": true, "lookahead": 32, "bframes": 7,
              "bref_mode": "middle", "refs_forward": 4,
              "refs_backward": 3, "gop_len": 0, "tile_columns": 2,
              "split_enc": "auto", "parallel": "auto",
              "output_buf": 64, "cuda_schedule": "spin",
              "avoid_idle_clock": true, "avhw": true },
    "HQ":   { /* 同构：cqp [22,24,26], qp_min [20,22,24],
                 qp_max [35,40,45], aq_strength 6, lookahead 32,
                 bframes 7, bref_mode middle, refs_forward 3,
                 refs_backward 2 */ },
    "SMALL":{ /* cqp [25,27,29], qp_min [22,24,26], qp_max [38,42,45],
                 aq_strength 6, bframes 7, bref_mode each */ },
    "FAST": { /* preset default, cqp [23,25,27],
                 qp_min [20,22,24], qp_max [38,42,45],
                 aq_strength 4, lookahead 16, bframes 4,
                 bref_mode each, refs_forward 2, refs_backward 1 */ }
  }
}
```
标定要点：CQP 值与 HEVC QVBR 的视觉等价需用 testsets + VMAF/SSIMULACRA 重标
（初始按"HEVC qvbr 数值 −2~−3"起试：AV1 同 QP 下效率更高，同画质可用更低 QP
保底时同样视觉质量）。QVBR 路线备选：qvbr 28/31/35/32（0-63 换算，待验证）。

### 3.2 qsv_av1.json（QSV AV1，ICQ 路线）

```jsonc
{
  "encoder": "qsv-av1",
  "codec": "av1",
  "profile": {
    "UHQ":  { "icq": 24, "profile": "main", "level": "auto",
              "output_depth": 10, "max_bitrate": 100000,
              "vbv_bufsize": 300000, "gop_ref_dist": 8, "ref": 8,
              "b_pyramid": true, "adaptive_i": true, "adaptive_b": true,
              "quality": "best", "extbrc": true, "mbbrc": true,
              "la_depth": 40, "gop_len": 240, "async_depth": 3,
              "output_buf_mb": 8,
              "function_mode": "FF", "tile_col": 2, "tile_row": 0 },
    "HQ":   { /* icq 26(待标定), quality best, max_bitrate 80000,
                 vbv_bufsize 240000, gop_len 240 */ },
    "SMALL":{ /* icq 30(待标定), max_bitrate 40000, vbv_bufsize 120000,
                 gop_len 300 */ },
    "FAST": { /* icq 28(待标定), quality balanced, max_bitrate 60000,
                 vbv_bufsize 240000, gop_ref_dist 8 */ }
  }
}
```
注意：**ICQ 是 0-255 刻度（HEVC 1-51），值必须重标定**，上表为起点候选；
`bframes` 对 AV1 静默无效（用 gop_ref_dist）；tier/sao/ctu/tskip 等一律不写。

### 3.3 vce_av1.json（VCE AV1，QVBR 路线）

```jsonc
{
  "encoder": "vce-av1",
  "codec": "av1",
  "profile": {
    "UHQ":  { "preset": "slow", "profile": "main", "output_depth": 10,
              "qvbr_quality": 23, "max_bitrate": 100000,
              "vbv_bufsize": 300000, "aq_mode": "caq",
              "cdef_mode": "auto", "tiles": 0, "gop_length": 0,
              "qp_init": [20, 22, 24], "qp_min": [18, 20, 22],
              "qp_max": [32, 36, 40] },
    /* HQ/SMALL/FAST 同构：qvbr_quality 25/28/26；PA 键（paq=caq、
       ltr=true 等）按实测回填；RDNA3 档不写 bframes/adapt-gop
       （代际分支），RDNA4 档加 bframes */
  }
}
```
注意：`--pa`/PreAnalysis 对 AV1 可用（QVBR 系列依赖它，VCEEncC 自动开启）；
B 帧按 GPU 代际分支；CQP 数值需按 AV1 刻度（上限 255）重标定。

---

## 4. 社区调参共识（联网调研结论，来源可回源核对）

### 4.1 NVENC AV1

- **质量要诚实**：NVIDIA 官方"40% 码率节省"是 **AV1 vs H.264** 的口径
  （[NVIDIA Ada AV1 博客](https://developer.nvidia.com/blog/improving-video-quality-and-performance-with-av1-and-nvidia-ada-lovelace-architecture/)）；
  Tom's Hardware 实测 **Ada 上 NVENC AV1 与 NVENC HEVC 同码率质量"无大差异"**，
  AV1 的优势主要在低码率段 + 免版税 + 生态。Blackwell 的 AV1 UHQ
  （lookahead-level + 时域滤波，SDK13）在收窄差距。
  → **对"归档画质优先"场景，NVENC AV1 相对 NVENC HEVC 没有决定性优势**，
  换编的真实收益是免版税/通用性；XAVC 素材不值得为它牺牲标准合规。
- CQP 模式下调 `tune` 反而降画质（[NVIDIA 论坛实测](https://forums.developer.nvidia.com/t/nvencs-tune-parameter-results-in-lower-quality-in-constqp-mode-for-av1-encoding/331787/2)）；
  tune 面向 VBR RC 设计 → CQP 档不写 tune，QVBR/VBR 档写 hq/uhq。
- lookahead 对 AV1 有效（Ada+），AQ（空域+时域）建议保留；10bit 恒用；
  multipass 仅 VBR/CBR 的软件两趟。
- 硬件 RC 仅 CQP/CBR/VBR；QVBR/CBRHQ/VBRHQ 是 lookahead 软件近似。

### 4.2 QSV AV1

- rigaya 官方 VQ 基准站（https://rigaya.github.io/vq_results/）：QSV AV1 曲线
  整体在 QSV HEVC 上方（同 VMAF 更省码率，编解码层 ~20%，与 Intel 官方
  "体积比 H.265 小 20%" 口径一致）；速度同级（都走 FF）。
- **B 帧专项**（A310）：AV1 最优 ≈ `-b 7`，即 **`--gop-ref-dist 8`（默认值
  已是甜点）**；对应 HEVC `-b 8`、H.264 `-b 3`。
- ICQ/CQP 是 **0-255 刻度**（media-driver 上限），与 HEVC 1-51 不可比：
  不能照搬 HEVC 档数字，必须重标定（起点 icq 24）。
- lookahead 新用法：`--la-depth` 叠 icq/vbr/cbr + `--extbrc` + `--i-adapt
  --b-adapt`；旧 `--la/--la-icq/--la-hrd` 对 AV1 不可用。
- 驱动门槛：Arc AV1 ≥3259 可用、≥3430 稳定、**≥3959（API 2.08）修复时间戳
  bug**（vpl-gpu-rt #253 / ffmpeg #10062；QSVEnc 7.20+ 内置 workaround）；
  QSVEnc ≥7.21（ICQ 255 上限、gop-ref-dist 默认 8）。
- `--mbbrc/--extbrc` 早期驱动"探测 o 但实际无效果"（issue #96），新驱动+
  EncTools 才真生效；`--scenario-info game_streaming` 可触发 EncTools BRC。

### 4.3 VCE AV1

- AMD VCN4（RDNA3）AV1 比自家 HEVC 约 **+30% 压缩效率（VMAF +1~2 分）**，
  速度 RX 7900 XTX 单流峰值最快；RDNA4（VCN5）官方称 VMAF 同比再 **+20%**。
  → 三家里 VCE AV1 相对自家 HEVC 的增益最大。
- **B 帧是代际分界**：RDNA3 无 B 帧；RDNA4 才有（`--bframes`/`--adapt-gop`，
  VCEEncC 8.24+）→ 预设必须按 GPU 代际分支（能力探测）。
- QVBR/VBRHQ/CBRHQ 依赖 PreAnalysis（VCEEncC 自动开启）；`--pa paq=caq`
  与 `--aq-mode caq` 是 VBAQ 的 AV1 替代路径。
- 坑：VCN5 AV1 CBR 偶发达不到目标码率（OBS #12048）；CQP 刻度与 HEVC 不同
  （AV1 上限 255）需重标。

## 5. 落地清单

1. 三份 JSON 按 §3 落盘（`nvenc_av1.json` / `qsv_av1.json` / `vce_av1.json`）。
2. caps.py 增加 AV1 段解析（nvenc `Codec: AV1` 块 / qsv `Codec: AV1 FF` 块 /
   vce 的 AV1 特性行），供 plan_initial_format 与能力 WARNING 使用；
   **VCE 需按 GPU 代际（RDNA3/RDNA4）分支 B 帧能力**。
3. PARAM_MAP 按 §2 表新增（各后端 ~10-15 行）。
4. **位深陷阱清单（实现时必须回归）**：NVENC `--profile main`+10bit=静默 8bit
   （10bit 必须 profile high）；QSV `--bframes` 对 AV1 静默无效（用
   gop-ref-dist）；QSV ICQ 0-255 刻度；三家的 CQP 数值均需重标定。
5. 档位标定：testsets 三组素材 × UHQ/HQ/SMALL/FAST 跑批，VMAF + SSIMULACRA
   对比 HEVC 档，校准 §3 的 CQP/ICQ/QVBR 值（这是唯一无法靠文档替代的步骤）。
6. 门控：AV1 后端默认仅经典路径（见 av1_feasibility_report.md ★ 决策节）。
7. 工具升级（顺手项）：NVEncC 9.31→9.33、QSVEncC 8.26→8.28、VCEEncC
   9.12→9.14（白名单机制对新旧旗标兜底）。
