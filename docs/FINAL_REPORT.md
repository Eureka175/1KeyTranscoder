# 1KeyTranscoder · 最终评估报告

> 汇总日期：2026-08。本文档是近期四份评估的**汇总结论与决策记录**，
> 详细证据见各评估原文（docs/evaluation/）与参考资料存档（docs/reference/）。
> **重要声明：各编码器档位 JSON（nvenc/qsv/vce/x265/x265_scaling）内的
> 数值与速度注记均为作者实测标定值，为权威数据；本文档及其子报告不做
> 改动，任何调参须以测试集回归为依据。**

---

## 1. 项目现状快照

- 主体：`1kt.py`（Windows 批量归档转码，递归 + 断点续跑 + Sony 元数据保留）
- 生产后端：NVEncC / QSVEncC（硬件 HEVC，4 档位 UHQ/HQ/SMALL/FAST）
- 手动后端：x265（软件 HEVC 高压缩档，档位 JSON 已标定）
- 预留：VCEEncC（JSON 已备未接）
- 评估完成待实施：AV1（NVENC/QSV 硬件 + SVT 软件）

## 2. 编码器矩阵与定位

| 后端 | 状态 | 定位 |
|---|---|---|
| NVENC HEVC | ✅ 生产默认 | 主后端（4K60 实测 73fps 超实时） |
| QSV HEVC | ✅ 生产默认 | 第二后端 / 双后端并行 |
| x265 | ⚠️ 手动高压缩档（见 §4 条件清单） | 4:2:2 保真、质量优先冷归档；吞吐受限 |
| VCE HEVC | 预留 | AMD 机器扩展 |
| AV1（NVENC/QSV/SVT） | 📋 待实施 | **仅非 XAVC 经典路径**（§5 决策） |

## 3. 评估一：265 实现详细程度（原文：hevc_implementation_assessment.md）

**结论：生产级骨架 + 部分标定中的高详细度实现**（~7800 行核心 Python、
4 套档位 JSON、真实相机测试集）。机制层（能力探测/降级梯/调度/日志/Sony
保留全链）生产级并经过真实素材验证；软件档的缩放规则自标 PROVISIONAL。

## 4. 评估二：x265 生产就绪（原文：x265_production_assessment.md）

**判定：代码与命令层可进生产；档位层条件性通过。**
本机实证（真实 Sony 素材）：4 档管线解析正确、x265 4.3 编码零告警、
4:2:2 10bit 自动选型 Main 4:2:2 10；官方文档逐参数核查 71 参数全部合法。
已知改进项（P0）：

1. FAST 档 `rd=2` 使 psy-rd 静默失效（官方要求 rd≥3）
2. `info` 1→0（构建信息 SEI 破坏归档可复现性）
3. 删除 `no-strong-intra-smoothing`（防条带平滑回退）
4. level/CPB 修正（动态 VBV 的 bufsize 超 Level 6.1 High tier CPB 上限）
5. 端到端验证留档：`run_selfcheck --encoder x265 --check full`

（`threaded-me` 经复核**无需改动**：JSON 中为关闭状态，与官方"VBV 下禁用"
结论一致。）

吞吐：档位 JSON 速度注记（285H@10/2/0.8fps）为作者标定值；本机复测
（前台 PS/LR 负载下）为受压下限（FAST 4.0 / HQ 1.05 / UHQ 0.33 fps）。
结论：4K60 大宗归档以硬件后端为主；x265 为质量优先冷归档/4:2:2 保真路径。

## 5. 评估三：AV1 可行性（原文：av1_feasibility_report.md）

**技术链路可行**（NVENC/QSV/SVT 三路径均本机实测打通编码→封装→解码），
**但集成定位受 XAVC 合规约束**：

- XAVC 标准只定义 H.264/HEVC；保留 XAVC brand 的 AV1 文件是伪标准产物，
  NLE/Catalyst/机身消费链不认 → **AV1 不默认集成元数据保留管线**
- AV1 默认仅服务经典路径（非 Sony 素材）；XAVC 素材恒用 HEVC
- "AV1+rtmd"仅作剥离 brand 的实验模式预留
- 全部 AV1 编码路径只有 4:2:0（XAVC-S 4:2:2 源需色度降级）

## 6. 评估四：AV1 硬件调参（原文：av1_hw_tuning_guide.md）

三后端支持度实测 + HEVC→AV1 逐键翻译表 + 预设草案已完成。关键陷阱：
NVENC `--profile main`=8bit/`high`=10bit（静默位深陷阱）、非层级 B 帧 ≤7、
CQP 模式调 tune 反降画质；QSV 仅 CBR/VBR/CQP/ICQ、`--bframes` 静默无效
（用 `--gop-ref-dist`）、ICQ 0-255 刻度；VCE 六种 RC 全可用、RDNA3 无
B 帧/RDNA4 有。质量诚实结论：NVENC AV1 ≈ NVENC HEVC；QSV/VCE AV1 明显
强于自家 HEVC。

## 6b. 评估五：SVT-AV1 归档可行性（原文：svt_av1_archival_assessment.md）

软件 AV1（主线 + PSY fork）的归档可行性专项：MSU 基准同画质比 x265 省
35-49% 码率；归档级 preset 4-6 的 4K60 吞吐 0.1-0.5x 实时；输出仅 4:2:0；
PSY 进入维护收缩期 → 定位为经典路径（非 XAVC）软件高压缩档候选，
长期归档倾向主线。详见原文。

## 7. 路线图

| 阶段 | 内容 |
|---|---|
| **x265-P0** | §4 六项修复 + 验证留档（不改档位 JSON 的标定值，仅参数结构修复，逐项经测试集回归） |
| **AV1-P1** | NVENC AV1 后端仅经典路径（能力解析 + PARAM_MAP + 门控 + 预设草案标定） |
| **AV1-P2** | QSV AV1 同构 + 双后端扩展 |
| **AV1-P3** | SVT-AV1 软件档（非 Sony 高压缩） |
| 预留 | VCE 后端接线；AV1+rtmd 实验模式；VCE AV1 |

## 8. 证据链与文档地图

```
docs/
├── README.md              分类索引
├── FINAL_REPORT.md        本文档
├── design/                设计文档（硬件后端设计/实施报告/集成报告）
├── evaluation/
│   ├── hevc_implementation_assessment.md   265 详细程度评估
│   ├── x265_production_assessment.md       x265 生产就绪评估（★本报告 §4）
│   ├── av1_feasibility_report.md           AV1 可行性（★本报告 §5）
│   └── av1_hw_tuning_guide.md              AV1 调参指南（★本报告 §6）
└── reference/             官方文档与社区调研存档（x265/SVT-AV1/NVENC/QSV/VCE）
work/x265_prod_eval/        x265 实证产物（管线 dump、真实编码日志、4:2:2 产物）
work/av1_feasibility/       AV1 实证产物（三后端编码+封装+解码+4K60 基准）
```
