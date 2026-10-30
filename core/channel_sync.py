"""自动延时补偿集成层 (P1 正式版, algo 2.3.0-p1).

流程 (transcode 模式; transparent 模式跳过视频编码, 见下):
  1. effective_opts 归一 (DEFAULTS + 用户覆盖, 旧键 max_lag_seconds 兼容);
  2. eligible_audio(): 文件级边界 — 音频流 >= 3 / 每流单声道 / 线性 PCM
     (s16/s24/s32/f32 × le/be) / 采样率一致且 ∈ {48000, 96000};
     **2ch/1ch 布局默认不做对齐** (用户决定); 44.1kHz 显式拒绝;
  3. ffmpeg 逐流解码 raw (存储精度按 §6.2 表: s32 源走 f64, 其余 f32),
     逐轨健康检查 (NaN/Inf / 静音 RMS / 最短时长) — 轨道级状态;
  4. 锚点候选回退 [CH3, CH4, CH1, CH2] (0-based [2,3,0,1]): 首个与至少
     一条其它健康轨得到有效估计者当选; 全部失败 -> no_valid_anchor;
  4b. (opt-in, **默认关闭**) 分组锚点: 先按"相关峰/噪声底"把健康轨分成
     互不相关的组, 未被锚点覆盖的组各用自己的组内锚点再测一次。只做
     **加法** —— 只有判成 untouched 的轨才可能被改判, 已经定下来的修正
     绝不丢失、也绝不改值 (结构性保证, 不靠回归去赌);
  5. anchor <-> 其余健康轨逐一 estimate_pair (两阶段 GCC-PHAT + 相位斜率
     精估), 每轨独立质量门: usable_frames / confidence / constant
     (MAD + 漂移 ppm) / out_of_range (搜索窗边缘堆积);
  6. 每轨独立决策: silent_track / non_finite / insufficient_frames /
     low_confidence / non_constant / out_of_range -> untouched (不阻止
     其它健康轨同步); |delay| < aligned_max_ms 或 |shift| <=
     min_shift_samples (默认 5 样本) -> already_aligned;
     其余 -> fixed (纯整数样本移位, rint(delay_samples));
  7. 对 fixed 轨执行 recheck_residual() (局部 GCC + 相位斜率, 门
     verify_max_ms): 失败仅回退**该轨**为 untouched, 不回滚其它轨;
  8. 输出: 每轨一个音频中间文件 (fixed 轨 = 移位后按源 codec 回编码,
     其余轨 = 源轨 stream copy, bit-exact) — audio_files 全轨清单,
     fixed_files 为其中实际被移位的子集; 尾部补零保全长, 不做截断;
  9. 文件级失败 (无有效锚点 / 全部健康 target 均不可靠 / 解码或回编码
     失败) 时整文件音频原样; 轨道级失败保留其它成功轨的结果。

transparent 模式 (剪辑前预处理, --channel-sync-transparent):
  视频与所有非音频流 stream copy, 仅 fixed 音轨重新生成, untouched
  音轨保持原始内容; 全部已对齐时输出为源文件字节级拷贝
  (SHA256(output) == SHA256(input)); 文件级失败时输出 = 源文件拷贝
  (音频原样)。重封装走既有 GPAC 后端 (同 preservation/audio_sync.py
  的机制, 但按轨引用源文件以保留 data/meta 轨)。

vendored core/mp4_channel_sync.py (ChronoSync 1.x, MIT) 自 algo
2.1.0-p1 起不再被引用, 保留作回滚与对照; P1 与 vendored 1.x 的差异
对照见 docs/design/channel_sync_p1.md。

numpy/scipy 为可选依赖: 仅 --channel-sync 且源具备多流单声道 PCM
布局时才导入; 缺失时给出明确 WARNING 并跳过 (不影响正常转码)。

方向约定: delay > 0 = 目标轨比锚点**晚到**, 修正 = 整体前移 delay。
语义约定: delay 是**当前文件内的观测轨间时差** (可包含电子/无线链路
延迟、录音链路差、麦克风物理位置的声学传播差等), 不得解释为设备固有
latency; 不同物理位置不禁止同步, 但相关性不足时安全放弃该轨。
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import math
import os
import shutil
import struct
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

__all__ = [
    "DEFAULTS",
    "effective_opts",
    "eligible_audio",
    "group_anchor_of",
    "group_by_snr",
    "run_channel_sync",
    "repair_remux_timescale",
]

DEFAULTS: dict[str, Any] = {
    # —— 模式 ——
    "channel_sync_transparent": False,   # 视频/非音频 stream copy + 轨道级音频同步

    # —— 输入边界 (2026-10-29, 用户决策 A) ——
    # 所有布局一视同仁拆成**独立声道**再计数: mono/stereo/5.1/7.1 都按声道数
    # 参与判定。3–8 个 PCM 声道默认自动对齐 (不要求 --channel-sync);
    # <3 (如单个 stereo 的 2 声道) 不自动; >8 只允许 --audio-plan
    # alignment=enabled 显式开启。`min_audio_streams` 仅保留兼容 (旧键)。
    "min_audio_streams": 3,              # 旧键: 兼容读取, 判定改用 min_auto_channels
    "min_auto_channels": 3,              # 自动开启对齐的最小 PCM 声道数
    "max_auto_channels": 8,              # 自动开启对齐的最大 PCM 声道数
    "supported_sample_rates": [48000, 96000],  # 仅 48k/96k (44.1k 显式拒绝)
    "supported_codecs": [
        # 用户决定: 大小端无所谓, 都支持; 需要排除的是 44.1kHz 采样率
        "pcm_s16le", "pcm_s16be",
        "pcm_s24le", "pcm_s24be",
        "pcm_s32le", "pcm_s32be",
        "pcm_f32le", "pcm_f32be",
    ],
    "min_audio_seconds": 2.0,            # 初值, 待真实素材标定 (过短轨=轨道级不可测)
    "silent_rms_dbfs": -60.0,            # 初值, 待真实素材标定 (整轨静音判定)

    # —— 估计 ——
    "search_window_ms": 80.0,            # 初值, 待真实素材标定 (电子带上限30 + 10m声学~29 + 余量)
    "wide_search_ms": 250.0,             # 初值, 待真实素材标定 (仅诊断超窗用的宽窗复测)
    "frame_ms": 200.0,                   # 初值, 待真实素材标定
    "hop_ms": 100.0,                     # 初值, 待真实素材标定
    "coarse_rate": 8000,                 # 初值, 待真实素材标定 (48k->q6, 96k->q12, 整数抽取)
    "anchor_segment_seconds": 30.0,      # 初值, 待真实素材标定 (相位斜率锚段, 不足取全长)
    "min_confidence": 0.3,               # 初值, 待真实素材标定
    "min_usable_frames": 5,              # 初值, 待真实素材标定
    "min_usable_fraction": 0.0,          # 已标定 (真实语料实测, 非初值)
    # ^ 原为 0.6，注释写作"初值, 待真实素材标定"。实测 (2026-10-16,
    #   I:\compact 全部 263 个 4×mono 片段, 仅音频路径) 结果:
    #     0.6  -> measure_failed 66/263 (25.1%), applied 0
    #     0.0  -> measure_failed 15/263 ( 5.7%), applied 14
    #   其中 46 个是**假失败** (无轨需要修正, 却因覆盖率不足被报成
    #   measure_failed); 14 个真实修正经端到端验证 13/14 通过, 零回归。
    #   比例门删除后**绝对下限仍然生效**: `min_usable_frames` (5) 与
    #   `min_confidence` (0.3) 继续拒绝由单帧噪声相关得出的移位;
    #   证据不足的轨道依旧 untouched, 只是不再让**整文件**报成失败。
    #   设置 0.0 而不是删除该键: `evidence_ok` / `evidence_of_est` /
    #   `audio_sync_tv_exec` 都通过 `effective_opts()` 读这一项,
    #   删键会让它们回退到 DEFAULTS 从而复活 0.6。
    "fine_phase_band_hz": [200.0, 8000.0],  # 初值, 待真实素材标定 (96k 上沿仍 8k)
    # 帧级 RMS 门 (dBFS): 任一窗低于此值该帧不可测 — 抽取滤波振铃会把
    # 真静音抬到 ~1e-3 (-60dB), 缺省 -50dB 高于振铃、低于真实内容
    "frame_min_rms_dbfs": -50.0,         # 初值, 待真实素材标定

    # —— 恒定性 (P1 二分 constant/non_constant; step/ramp 细分属二期) ——
    "mad_max_ms": 1.0,                   # 初值, 待真实素材标定
    "step_min_ms": 3.0,                  # 初值, 待真实素材标定 (仅诊断警告, 不参与判定)
    "constant_max_ppm": 5.0,             # 初值, 待真实素材标定
    # 漂移材料性门 (真实素材标定): ppm 超门**且**全片预测漂移 > 此值才判非恒定 —
    # A7M5 实测: 已对齐轨的量化噪声斜率可达 5~30ppm 但漂移量仅 ~0.03ms;
    # 真实慢漂移轨 39~49ppm 对应 0.42~1.9ms, 远超此门仍被正确拦下
    "drift_min_ms": 0.1,                 # 初值, 待真实素材标定

    # —— 修正 / 复检 ——
    "verify_max_ms": 0.05,               # 复检残差阈值 (整数修正量化残差理论上 <= 0.5 sample)
    # 复检的**相对**容差 (2026-10-20): 绝对门 0.05 ms 有个方向性错误 ——
    # 它把"留 20.79 ms 偏差"判成比"留 0.08 ms"更安全。实测 `C1213 CH1`
    # 测出 +20.79 ms、修正后残差 0.08 ms, 却因为 0.08 > 0.05 被整条退回。
    # 打开后有效门为 max(verify_max_ms, |delay_ms| * verify_max_rel):
    # 小于 5 采样的偏移由 `min_shift_samples` 管, 这里不会放宽它们。
    # 默认 0.0 = 关闭 (与旧行为逐位一致)。
    "verify_max_rel": 0.0,
    # 复检窗口数 (2026-10-20): 1 = 只看中部锚段 (旧行为)。>1 = 沿片长均匀取
    # 多个锚段, 取**最差**残差。为"偏移不是全片常数"的轨准备:
    # 实测 `C1179 CH1` 中部局部对齐、全片仍有 31.5 采样残差, 单窗会放过它。
    "recheck_windows": 1,
    # 第三道门 (2026-10-20): **独立预检估计必须与施加的移位一致**。
    # 0 = 关闭 (旧行为)。>0 时, 在施加移位**之前**用与生产估计器不同的一条
    # 路径 (宽搜索窗 GCC + 相位斜率, 多窗取中位) 重新测一次这条轨的延迟,
    # 若与 `shift` 相差超过该样本数, 判该轨不可信 -> untouched(
    # reason=`estimate_disagreement`)。
    # 为什么不能只靠"残差够小": 实测 `rel=0.2` 放行 4 条候选, 其中
    # `C1167 CH2` 与 `C1267 CH1` 的移位值与独立测量差 5–6 采样 ——
    # 数据写对了、数值是错的。残差门看不出来, 两条独立估计的一致性才看得出来。
    "verify_agree_samples": 0,
    # 一致性容差的**相对**与**置信**量纲 (2026-10-22):
    # 固定采样数在不同偏移量级上要求不同 —— 20 采样对 24 ms 是 1.7%,
    # 对 5 ms 是 8%。所以容差随尺度走, 取三者最宽:
    #   verify_agree_samples  绝对下限 (采样)
    #   verify_agree_rel      相对偏差: rel × |delay|
    #   verify_agree_sigma    sigma × 独立估计的多窗标准差
    # 默认 0 = 不启用, 与旧行为一致。
    "verify_agree_rel": 0.0,
    "verify_agree_sigma": 0.0,
    # 一致性判据是否**否决**该轨 (默认 False = 只诊断)。
    #
    # 依据 (2026-10-22, 归档+访谈集去重后 73 条被修轨; 独立标签 =
    # 数据精确 且 改善倍数 >= 10x, 良好 70 / 不良 3, 3 条不良全是 after 不可测):
    #   全放行              保留 73, 良好 70, 误收 3, 误拒 0
    #   绝对 5 采样否决      保留 47, 良好 47, 误收 0, **误拒 23**
    #   绝对 20 采样否决     保留 58, 良好 56, 误收 2, **误拒 14**
    #   相对 5%*|delay| 否决 保留 68, 良好 65, 误收 3, **误拒 5**
    #   5 sigma 否决         保留 62, 良好 59, 误收 3, **误拒 11**
    # 任何量纲的否决都是"误拒 5-23 条良好修正, 换多挡 <=1 条不良" —— 因为两条
    # 估计的分歧主要来自**独立估计自身的方法偏差**, 而不是移位值错了。
    # 所以默认只记录 + 超阈警告, 否决权留给"修后残差"。
    "verify_agree_veto": False,
    # 逐轨参考选择 (默认 False = 关闭, 行为与旧实现逐位一致)。
    #
    # 主锚点下测不出的轨, 换用其它已定位的轨当参考重测, 再把"相对参考的延迟"
    # 合成为"相对主锚点的移位" (见 compose_reference_shift)。只在**主锚点下
    # 已判 untouched 的轨**上动手, 且成功写回仍要过 decide_and_record 同一道
    # 门 + 修后残差核验 (复检以主锚点为参照), 因此开关打开只可能多修, 不会
    # 改掉任何已定的值。
    #
    # 依据: `F:\align_4ch_proof` 的有效素材里, CH1↔CH2 的 NCC 为 0.975–0.977
    # (lag 离散 1 采样), 而生产固定用 CH3 当锚点时 C1211 的 CH1、C1197 的 CH2
    # 覆盖率只有个位数。C1211 的链路 19.02+6.76 = 25.78 ms 与直接测量
    # 25.4 ms 吻合, 说明合成方向与量值都对。
    "track_reference": False,
    # 材料性判据 (2026-10-21): 0 = 关闭 (旧行为)。
    #
    # 恒定性不通过的轨, 只要 `|delay| >= materiality_k × 实测稳健位移`,
    # 就按可修处理。依据是"修正前后的误差比较": 整数移位后的残余上界是实测
    # 位移, 而拒绝留下的误差是 |delay|; 两者相差 k 倍以上时修正显然更优。
    #
    # 实测依据 (2026-10-21, `F:\align_4ch_proof` 的 4ch 有效原始文件):
    # 47 条被拒且偏差 >= 5 采样的轨里, **39 条 (83%)** 有 SNR>=1.3 的可对齐
    # 伙伴、可用帧中位 58 —— 它们不缺数据、不缺相关性, 只是被恒定性门拦下。
    # 归档全量口径下, 这类轨的稳健位移中位 1.553 ms 而偏差中位 23.95 ms
    # (比值 15×), 取 k=3 可放行而残余上界仅 0.78 ms。
    # 全范围(离群)门不需要单独调系数 —— 它拦下的那 20 条同样满足本判据。
    "materiality_k": 0.0,
    "aligned_max_ms": 0.05,              # 全部通道 |delay| 低于此值 -> already_aligned
    # 修正的**最小幅度** (整数样本)。|shift| <= 此值的轨一律判 already_aligned,
    # 不做写入。"5 sample 以内都不修"是明确的产品决定, 不是阈值微调:
    #   * 48 kHz 下 1 样本 = 0.0208 ms, 5 样本 = 0.104 ms;
    #   * 这个量级已经在测量不确定度里 —— 同一套估计器在重复测量上的散布
    #     就有 1~2 个样本, 再往下修等于把噪声写成文件内容;
    #   * 并且它对真实缺陷没有收益: 归档里 14 条待修轨的位移是 +905~+1222
    #     或 −37~−39 样本, 与 5 样本差着两个数量级。
    # 用**样本数**而不是毫秒, 因为要表达的是量化粒度, 与采样率无关。
    "min_shift_samples": 5,
    # 精确模式 (opt-in, **默认关闭**): 把最小幅度门降到 1 样本, 但**必须由
    # 施加后的独立复检残差来验收** —— 1 样本做不到就退 2 样本, 再做不到就
    # 退回常规 5 样本并打 WARNING。见 `resolve_accurate_tier()`。
    # 关掉时这一整段不执行, 决策路径与 v0.8.7 逐字节相同。
    "channel_sync_accurate": False,
    "fix_chunk_seconds": 60.0,           # 初值, 待真实素材标定 (移位分块大小)

    # —— 锚点 (P1: 候选顺序 + 健康门; 完整打分属二期) ——
    # 2026-10-29 用户决策 A4: 默认锚点改为 CH1→CH2→…→CH8 (0-based 0..7),
    # 当前锚点置信度/帧数低于门即换下一个。旧默认 [2,3,0,1] 由 config 显式
    # 覆盖时仍然生效 (兼容)。所有依赖锚点的数值都会变 —— 回归单列
    # `anchor_effect` (docs/design/long_take_default_alignment.md §8.2)。
    "anchor_candidates": [0, 1, 2, 3, 4, 5, 6, 7],

    # —— 分组锚点 (opt-in, **默认关闭**) ——
    #
    # 单锚点模型隐含一个前提: 四条轨录的是同一个声场。真实 4 声道素材经常
    # 不成立 —— CH1/CH2 与 CH3/CH4 是两支互不相干的麦克风。此时锚点选中的
    # 那一组能被修, 另一组**没有任何修正机会**, 尽管它内部完全可对齐
    # (实测 20260919_C1305/C1306: 组内相关峰/噪声中位 1.53~1.78,
    # 跨组 0.91~1.02 —— 后者低于噪声底, 即"与随机不可分")。
    #
    # 默认关闭的理由: 这是**策略变更** (多了一个坐标系), 不是缺陷修复。
    # 关闭时下方代码路径与此特性引入前逐字节相同。
    "group_anchor_enabled": False,
    # 建组阈值 = 相关峰/噪声底的中位数门。1.3 是实测稳定窗口的下界:
    # 1.2 会出现 ~1.27 的跨组边导致过度合并 (C1305 四轨并成一组),
    # 1.5 起开始过分裂 (C1214 拆掉真实同组的 CH2/CH3)。
    "group_snr_threshold": 1.3,
    # 每对抽多少帧来估计这个比值。长片上 200 帧与全量 41645 帧的差别只是
    # 噪声, 而代价从 ~11 s 降到 ~20 ms。
    "group_snr_samples": 200,
    # 有效样本低于此值 -> 该对**不可判定**, 按不连通处理 (不猜)。3 s 素材
    # 就有 30 帧; 更短的片段无法建组, 因此行为回落到单锚点。
    "group_snr_min_samples": 30,

    # —— P4.6 时变同步 (逐窗修正): **显式 opt-in**, 默认关闭 ——
    #
    # 三项必须同时由调用方给出。policy 与 coordinate 是 D2/D5 两个**未冻结的
    # 产品决策**, 因此这里**没有默认值**: 缺任何一个就拒绝执行时变路径, 而不是
    # 替调用方选一个。`None` 表示"调用方没有说", 不是"用实现默认值"。
    "time_varying": False,               # 显式开启逐窗修正
    "tv_policy": None,                   # D2: hold / no_correction_gap / explicit_state / linear / next
    "tv_coordinate": None,               # D5: latest-start / nearest-centre
    # 计划几何: 默认取 P3 已冻结的生产几何 (30 s 窗 / 10 s 跳), 与
    # core.audio_sync_tv 的具名常量同值。**测量几何**是另一回事: 帧轨迹仍按
    # frame_ms/hop_ms 测量, 由 aggregate_frames_to_windows 显式聚合。
    "tv_window_samples": None,           # None -> core.audio_sync_tv.DEFAULT_WINDOW_SAMPLES
    "tv_hop_samples": None,              # None -> core.audio_sync_tv.DEFAULT_HOP_SAMPLES
    "tv_search_samples": None,           # None -> core.audio_sync_tv.DEFAULT_SEARCH_SAMPLES
    "tv_chunk_samples": 480_000,         # apply 分块 (= 一个 hop); 不改变结果
    # 一个窗要多少帧才被接受为**可信**。与 min_usable_frames 是**不同的
    # 群体**: 后者是整条轨被采纳估计的帧数门, 这里是单个计划窗内的帧数门。
    # 取值 3 的理由: 计划窗 30 s 覆盖 300 个 100 ms 跳的帧, 3 帧是"这个窗里有
    # 真实测量"的下限, 不是精度要求 —— 精度由中位数与 D3 的残差报告承担。
    "tv_min_frames_per_window": 3,

    # —— 布局策略 (2026-10-25) ——
    #: 音频流 >= 3 但只有 2 条健康 (其余静音/非有限) 时, 默认**整文件跳过
    #: 对齐**: 4CH 素材里只有 2CH 有效是专业机常见的采样形态 (2CH 副本或
    #: 关闭轨), 对剩余 2 条做纠正在用户口径里没有意义, 还会让透明模式白白
    #: 重封装整个文件。保持为可覆盖项 (False 可恢复旧行为)。
    "skip_two_channel_only": True,
    # —— stereo 组绑定 (2026-10-29 用户决策 C; 默认开启) ——
    # 内部键 (不是 CLI 开关): 供 config/回归的 off 臂与单元测试使用;
    # 默认行为 = 高相关刚性双轨绑定为整组同偏移。
    "stereo_bind_enabled": True,

    # —— 兼容 ——
    "max_lag_seconds": None,             # 旧键: 读取时映射为 search_window_ms
    "algo_version": "2.3.0-p1",
}

_INT_KEYS = ("min_audio_streams", "min_auto_channels", "max_auto_channels",
             "coarse_rate", "min_usable_frames",
             "group_snr_samples", "group_snr_min_samples",
             "min_shift_samples", "recheck_windows", "verify_agree_samples")
#: **可为 None** 的整数键 (P4.6): None 是"未指定, 用上游具名默认常量", 与 0
#: 不是一回事。它们不能进 `_INT_KEYS` —— `int(None)` 会抛错, 而
#: `effective_opts` 的 except 会把**整份配置**回退成 DEFAULTS, 于是调用方写的
#: policy/coordinate 会被一个几何键悄悄丢掉。
_INT_OR_NONE_KEYS = ("tv_window_samples", "tv_hop_samples", "tv_search_samples")
#: **可为 None** 的字符串键 (P4.6): None = 调用方没有说 (拒绝时变路径)。
_STR_OR_NONE_KEYS = ("tv_policy", "tv_coordinate")
_FLOAT_KEYS = ("min_audio_seconds", "silent_rms_dbfs", "search_window_ms",
               "wide_search_ms", "frame_ms", "hop_ms",
               "anchor_segment_seconds",
               "min_confidence", "min_usable_fraction", "mad_max_ms",
               "step_min_ms", "constant_max_ppm", "drift_min_ms",
               "verify_max_ms", "verify_max_rel", "materiality_k",
               "verify_agree_rel", "verify_agree_sigma",
               "aligned_max_ms", "fix_chunk_seconds", "frame_min_rms_dbfs",
               "group_snr_threshold")
_LIST_INT_KEYS = ("supported_sample_rates", "anchor_candidates")
_LIST_FLOAT_KEYS = ("fine_phase_band_hz",)
_LIST_STR_KEYS = ("supported_codecs",)
#: **生产工作点**: 经实测标定的一组 opt-in 参数。
#:
#: 这些值不是"更激进的猜测", 而是每一项都有对照实测:
#:
#:   materiality_k=3            恒定性不通过的轨, 只要 |delay| >= 3×实测位移就按
#:                              可修处理。归档 667 件: 修正 13 -> 69
#:   verify_max_rel=0.2         复检相对门: 修正值本身远大于残余时放行
#:   recheck_windows=3          三窗取最差, 抓片内非恒定 (小位移 <1ms 不启用)
#:   verify_agree_samples=20    一致性判据只记录不否决 (见 verify_agree_veto
#:                              的实测: 任何量纲的否决都误拒 5-23 条良好修正)
#:   track_reference=True       逐轨参考选择: 归档 667 件再 +12 条; 验证集
#:                              C1197/C1211 因此全通
#:
#: 合起来在归档 667 件上的实测: 修正 13 -> 81, 新增 68, **失去 0 / 已定轨
#: 移位被改 0**; 验证集 35 件: 全通 25 -> 27 (有效短素材 27/27 全通)。
#: 默认的 `DEFAULTS` 仍然全部保守 —— 这份 profile 只由生产入口显式套用, 单测与
#: 回放因此仍能验证"默认路径逐位不变"。
PRODUCTION_OPTS: dict[str, Any] = {
    "materiality_k": 3.0,
    "verify_max_rel": 0.2,
    "recheck_windows": 3,
    "verify_agree_samples": 20,
    "track_reference": True,
}


def production_opts(
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """生产工作点 + 调用方覆盖 (config 文件 / CLI 优先)。

    `overrides` 里值为 None 的键**不覆盖** —— 调用方"没给"与"给了 None"在这条
    路径上必须可区分, 否则一个未设置的 config 键会把 profile 值擦成 None。
    """
    out: dict[str, Any] = dict(PRODUCTION_OPTS)
    for k, v in (overrides or {}).items():
        if k.startswith("_") or v is None:
            continue
        out[k] = v
    return out


_BOOL_KEYS = ("channel_sync_transparent", "time_varying", "group_anchor_enabled",
              "channel_sync_accurate", "verify_agree_veto",
              "track_reference", "skip_two_channel_only",
              "stereo_bind_enabled")

# 参与"全部健康 target 均失败 -> 文件级 measure_failed" 判定的轨道级原因
_ESTIMATION_FAIL_REASONS = frozenset({
    "insufficient_frames", "low_confidence", "non_constant",
    "out_of_range", "recheck_residual",
    # 2026-10-20: 两条独立估计不一致 —— 值本身就是可疑的, 与"没测出来"同类。
    "estimate_disagreement",
    # P4.2: 证据覆盖率不足 = 估计层未通过证据门 (与 low_confidence 同类,
    # 文件级判定同样算作"该 target 未测出").
    "evidence_insufficient",
})


def effective_opts(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """DEFAULTS + 用户覆盖归一; 非法值整体回退默认。

    兼容: 旧键 max_lag_seconds (秒) 非 None 时映射为
    search_window_ms = max_lag_seconds * 1000; 废弃键 reference_stream
    (锚点改由 anchor_candidates 候选顺序决定) 读取时忽略。
    """
    opts = dict(DEFAULTS)
    for key, value in (cfg or {}).items():
        if key.startswith("_"):
            continue
        if key in DEFAULTS:
            opts[key] = value
    try:
        for key in _INT_KEYS:
            opts[key] = int(opts[key])
        # P4.6: None 必须**原样保留** —— 它是"未指定", 不是 0。见
        # `_INT_OR_NONE_KEYS` 的注释: 一个 int(None) 的抛错会整份回退 DEFAULTS。
        for key in _INT_OR_NONE_KEYS:
            if opts[key] is not None:
                opts[key] = int(opts[key])
        for key in _STR_OR_NONE_KEYS:
            if opts[key] is not None:
                opts[key] = str(opts[key])
        for key in _FLOAT_KEYS:
            opts[key] = float(opts[key])
        for key in _LIST_INT_KEYS:
            opts[key] = [int(v) for v in opts[key]]
        for key in _LIST_FLOAT_KEYS:
            opts[key] = [float(v) for v in opts[key]]
        for key in _LIST_STR_KEYS:
            opts[key] = [str(v) for v in opts[key]]
        for key in _BOOL_KEYS:
            opts[key] = bool(opts[key])
        if opts["max_lag_seconds"] is not None:
            opts["search_window_ms"] = float(opts["max_lag_seconds"]) * 1000.0
        opts["algo_version"] = str(opts["algo_version"])
    except (TypeError, ValueError):
        return dict(DEFAULTS)
    return opts


# ---------------------------------------------------------------------------
# 参与集合 / 声道模型 (2026-10-29, 用户决策 A/A5)
#
# 所有布局一视同仁: 一条 mono 流 = 1 个独立声道; 一条 5.1 流 = 6 个独立声道。
# "3–8 个 PCM 声道自动对齐" 按**声道数**而不是流条数判定; 输出时再按源流/声道
# 顺序重新交织回源结构 (全通道参与时承诺结构不变, 见 A6)。
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ChannelRef:
    """参与对齐的一个**独立声道** (源结构的定位信息随行)。

    `index` 是全局声道序号 (与报告里的 CH1..CHn 一致); `stream`/`channel` 是它
    在源容器音频流/流内声道的位置 —— 输出阶段按这两个字段重组, 不靠猜。
    """

    index: int
    stream: int
    channel: int
    stream_channels: int
    codec: str
    entry: str
    storage: str
    layout: str = ""

    @property
    def channel_id(self) -> str:
        return f"CH{self.index + 1}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "index": self.index,
            "stream": self.stream,
            "channel": self.channel,
            "stream_channels": self.stream_channels,
            "codec": self.codec,
            "layout": self.layout,
        }


def _audio_streams(streams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [s for s in streams if s.get("codec_type") == "audio"]


def _stream_int(stream: Mapping[str, Any], key: str) -> int:
    """稳妥读取 probe 的整数字段 (缺失/非法一律 0, 由调用方判定)。"""
    try:
        return int(stream.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def alignment_scope(
    streams: list[dict[str, Any]],
    eff: Mapping[str, Any] | None = None,
) -> tuple[list[ChannelRef] | None, str]:
    """判定一个文件的**参与集合**是否可自动对齐。

    返回 `(channels, reason)`:

    * `channels` 非 None = 可对齐; 列表是按**源顺序**展开的全部独立声道。
    * `channels` 为 None 时 `reason` 是稳定原因码 (可被报告/日志/测试断言):
        - `no_audio_streams`
        - `non_pcm_audio: codec=...`             — 没有 PCM 流 (不在自动范围)
        - `mixed_pcm_compressed_not_aligned: ...` — 混有 PCM + 压缩 (A5: 整文件
          不自动对齐, 音频原样输出, 进程不退出)
        - `mixed_sample_rates: [...]`
        - `unsupported_sample_rate: N`
        - `channel_count_out_of_range: N`        — <3 或 >8
        - `stream N: unknown channel layout`

    `eff` 缺省时用生产 DEFAULTS; 生产路径传 `effective_opts(...)` 以便 config
    覆盖生效。**只做判定, 不解码**。
    """
    opts = dict(DEFAULTS) if eff is None else dict(eff)
    min_ch = int(opts.get("min_auto_channels", DEFAULTS["min_auto_channels"]))
    max_ch = int(opts.get("max_auto_channels", DEFAULTS["max_auto_channels"]))
    supported = set(opts.get("supported_codecs") or DEFAULTS["supported_codecs"])
    rates_supported = set(
        opts.get("supported_sample_rates") or DEFAULTS["supported_sample_rates"]
    )
    audio = _audio_streams(streams)
    if not audio:
        return None, "no_audio_streams"
    pcm, non_pcm = [], []
    for st in audio:
        codec = str(st.get("codec_name", ""))
        (pcm if codec in supported else non_pcm).append(st)
    if not pcm:
        codes = sorted({str(s.get("codec_name", "")) for s in non_pcm})
        return None, "non_pcm_audio: codec=" + ",".join(codes)
    if non_pcm:
        # A5: 未指定 --audio-plan 时参与集合 = 全部输入音频流; 混有压缩即
        # 整文件不对齐。这里**不是**错误 —— 进程不退出, 音频原样输出。
        detail = ", ".join(
            f"stream {i} {s.get('codec_name')}"
            for i, s in enumerate(audio)
            if s in non_pcm
        )
        return None, "mixed_pcm_compressed_not_aligned: " + detail
    rates = {_stream_int(s, "sample_rate") for s in audio}
    if len(rates) != 1:
        return None, f"mixed_sample_rates: {sorted(rates)}"
    rate = rates.pop()
    if rate not in rates_supported:
        return None, f"unsupported_sample_rate: {rate}"
    channels: list[ChannelRef] = []
    for si, st in enumerate(audio):
        count = _stream_int(st, "channels")
        if count <= 0:
            return None, f"stream {si}: unknown channel layout"
        codec = str(st.get("codec_name", ""))
        entry = str(st.get("codec_tag_string", "") or "")
        layout = str(st.get("channel_layout", "") or "")
        for ci in range(count):
            # 多声道流经 AudioPCMReader 解码为 canonical f32 (其存储精度);
            # 单声道流沿用原路径, s32 源保持 f64 解码。
            storage = (
                "f32" if count > 1
                else ("f64" if codec in ("pcm_s32le", "pcm_s32be") else "f32")
            )
            channels.append(ChannelRef(
                index=len(channels), stream=si, channel=ci,
                stream_channels=count, codec=codec, entry=entry,
                storage=storage, layout=layout,
            ))
    n = len(channels)
    if n < min_ch or n > max_ch:
        extra = (
            " (>max: 仅 --audio-plan alignment=enabled 可显式开启)"
            if n > max_ch else " (<min: 默认不做对齐)"
        )
        return None, f"channel_count_out_of_range: {n}{extra}"
    return channels, ""


def eligible_audio(streams: list[dict[str, Any]]) -> tuple[bool, str]:
    """文件级资格判定 (保持名称/返回结构兼容; 语义见 `alignment_scope`)。

    2026-10-29 用户决策 A: **按独立 PCM 声道数**判定 (3–8 自动), 不再要求
    "每条流都是 mono"; 多声道 PCM (stereo/5.1/7.1) 与多流 mono 一视同仁。
    混合 PCM+压缩一律不自动对齐 (A5, 原因码 `mixed_pcm_compressed_not_aligned`)。
    44.1 kHz 等非 48k/96k 一律拒绝; 压缩/非线性格式不参与自动对齐。
    """
    channels, reason = alignment_scope(streams, None)
    return (channels is not None), reason


def _decode_stream(
    ffmpeg: Path,
    source: Path,
    stream_index: int,
    sample_rate: int,
    out_raw: Path,
    storage_dtype: str,
) -> tuple[bool, str]:
    """单流解码为 raw mono (写文件避免大数组走管道; f32/f64 按存储精度)。

    ⚠️ `-ac 1` 是**下混**, 仅适用于源流本身就是 mono 的既有路径。多声道源流
    的逐声道提取走 `_decode_multichannel_streams()`（内置 `AudioPCMReader`，
    逐声道跨步读取），不在这里用 downmix 假装"拆声道"。
    """
    fmt = "f32le" if storage_dtype == "f32" else "f64le"
    proc = subprocess.run(
        [
            str(ffmpeg), "-v", "error", "-nostdin", "-y",
            "-i", str(source),
            "-map", f"0:a:{stream_index}",
            "-vn", "-sn", "-dn",
            "-f", fmt, "-ac", "1", "-ar", str(sample_rate),
            str(out_raw),
        ],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace", timeout=1800,
    )
    ok = (
        proc.returncode == 0
        and out_raw.is_file()
        and out_raw.stat().st_size > 0
    )
    return ok, (proc.stderr or "")[-300:]


def _wav_format_for_codec(codec: str) -> Any:
    """源 PCM codec -> 中间 WAV 的采样格式（同精度，避免二次量化）。"""
    from .audio_wav import WavFormat

    return {
        "pcm_s16le": WavFormat.PCM16, "pcm_s16be": WavFormat.PCM16,
        "pcm_s24le": WavFormat.PCM24, "pcm_s24be": WavFormat.PCM24,
        "pcm_s32le": WavFormat.PCM32, "pcm_s32be": WavFormat.PCM32,
        "pcm_f32le": WavFormat.FLOAT32, "pcm_f32be": WavFormat.FLOAT32,
    }.get(str(codec), WavFormat.PCM24)


def _decode_multichannel_streams(
    ffmpeg: Path,
    source: Path,
    audio_streams: list[dict[str, Any]],
    audio_indices: list[int],
    scope: Sequence["ChannelRef"],
    work_dir: Path,
) -> dict[int, str]:
    """多声道 PCM 流的**逐声道解码**（A6 结构支持；绝不下混）。

    走内置 `AudioSourceBuilder` + `AudioPCMReader`（用户要求优先复用内置
    API）：reader 对每条流只解码一次到 canonical f32 交错缓存（内部用
    identity channelmap，不重排/不混音/不插值），再按
    `read(channel_index=…)` 跨步取出每声道写 `decoded_{channel}.{storage}`。

    `audio_streams` 是**全部音频流**的 ffprobe dict（保持 audio_position
    推导正确）；`audio_indices` 是需要逐声道解码的 audio-order 下标。

    返回 `{channel_index: error}`；空 dict = 全部成功。失败语义与
    `_decode_stream` 一致：任一声道失败即文件级 measure_failed。
    """
    import numpy as np

    from .audio_models import AudioSourceBuilder
    from .audio_pcm import AudioPCMReader

    errors: dict[int, str] = {}
    if not audio_indices:
        return errors
    builder = AudioSourceBuilder()
    builder.add("camera", list(audio_streams), path=str(source))
    by_container = {int(s.stream_index): s.id for s in builder.streams()}
    wanted: dict[int, str] = {}
    for ai in audio_indices:
        cidx = int(audio_streams[ai].get("index", ai))
        sid = by_container.get(cidx)
        if sid is None:
            for c in scope:
                if c.stream == ai:
                    errors[c.index] = (
                        f"no reader stream id for container stream {cidx}")
            continue
        wanted[ai] = sid
    if not wanted:
        return errors
    reader = AudioPCMReader(
        builder.plan(), ffmpeg=ffmpeg, work_dir=work_dir / "pcm_layout")
    try:
        try:
            reader.prepare(list(wanted.values()))
        except Exception as exc:                       # noqa: BLE001
            for c in scope:
                if c.stream in wanted:
                    errors.setdefault(
                        c.index, f"{type(exc).__name__}: {exc}")
            return errors
        targets = [c for c in scope if c.stream in wanted]

        def write_one(c: "ChannelRef") -> tuple[int, str | None]:
            sid = wanted[int(c.stream)]
            dst = work_dir / f"decoded_{c.index}.{c.storage}"
            try:
                total = int(reader.eof_sample(sid))
                chunk = 1_000_000
                start = 0
                with dst.open("wb") as fh:
                    while start < total:
                        n = min(chunk, total - start)
                        block, got = reader.read(
                            sid, channel_index=int(c.channel),
                            start=start, count=n)
                        if got <= 0:
                            return c.index, "short read before EOF"
                        fh.write(np.asarray(block, dtype=np.float32).tobytes())
                        start += int(block.size)
            except Exception as exc:                   # noqa: BLE001
                return c.index, f"{type(exc).__name__}: {exc}"
            if not dst.is_file() or dst.stat().st_size == 0:
                return c.index, "empty raw"
            return c.index, None

        # 注意: **不能**按声道并行调 reader.read() —— AudioPCMReader 内部对
        # 每个 stream 缓存**单个文件句柄**并 seek 读取，多线程共享会互相踩
        # 掉读位置（实测 4ch 单流时 CH3/CH4 读出全零 → silent_track）。
        # 不同 stream 的句柄彼此独立，因此只按 stream 分块并行、块内顺序。
        by_stream: dict[int, list["ChannelRef"]] = {}
        for c in targets:
            by_stream.setdefault(int(c.stream), []).append(c)

        def write_stream(chs: list["ChannelRef"]) -> dict[int, str]:
            out_errors: dict[int, str] = {}
            for c in chs:
                idx, err = write_one(c)
                if err:
                    out_errors[idx] = err
            return out_errors

        groups = list(by_stream.values())
        if len(groups) > 1:
            with ThreadPoolExecutor(
                    max_workers=min(4, len(groups))) as ex:
                for group_errors in ex.map(write_stream, groups):
                    errors.update(group_errors)
        else:
            for group_errors in map(write_stream, groups):
                errors.update(group_errors)
    finally:
        reader.close()
    return errors


def _interleave_stream_raw(
    channel_raws: Sequence[Path],
    storages: Sequence[str],
    *,
    sample_rate: int,
    channel_count: int,
    layout: str,
    wav_format: Any,
    dst_wav: Path,
) -> int:
    """把同一条多声道流的各声道 raw 交汇成 WAV（A6 结构保持）。

    走内置 `audio_wav.write_wav`（PCM 精度与源码流一致）。分块读取，避免
    整条 80 min 多声道 raw 常驻内存；返回写入帧数（取各声道最短）。
    """
    import numpy as np

    from .audio_wav import write_wav

    raws = [Path(p) for p in channel_raws]
    items = [4 if str(sd) == "f32" else 8 for sd in storages]
    lengths = [p.stat().st_size // it for p, it in zip(raws, items)]
    frames = int(min(lengths)) if lengths else 0
    if frames <= 0:
        raise RuntimeError("interleave: empty channel raw")
    chunk = 1_000_000

    def blocks() -> Any:
        handles = [p.open("rb") for p in raws]
        try:
            done = 0
            while done < frames:
                n = min(chunk, frames - done)
                cols = []
                for fh, it in zip(handles, items):
                    dtype = np.dtype("<f4") if it == 4 else np.dtype("<f8")
                    raw = fh.read(n * it)
                    cols.append(np.frombuffer(raw, dtype=dtype)
                                .astype(np.float32))
                yield np.stack(cols, axis=1)
                done += n
        finally:
            for fh in handles:
                fh.close()

    write_wav(
        dst_wav, blocks(), sample_rate=int(sample_rate),
        channel_count=int(channel_count),
        sample_format=wav_format,
        frame_count=frames,
        layout=str(layout or ""),
        overwrite=True,
    )
    return frames



# ffmpeg 的 MOV muxer 写 QuickTime PCM 条目 (in24/sowt/twos/in32/fl32),
# MP4 muxer 写 ISO 条目 (ipcm/fpcm)。Sony XAVC-S 的 LPCM 实际是 **ipcm**
# — 若音频中间文件用 MOV 承载, 源音轨被替换后容器里的 sample entry 会
# 变成 in24, preservation 的 `audio.tracks` 关键项随即判 MODIFIED 并使
# 整个文件 `--check basic` 失败 (真实 A7M5 素材实测: applied 的文件
# rc=1 且不产出)。因此按**源轨自己的 sample entry** 选择能复现同一条目
# 的 muxer, 使替换对容器透明。
_MP4_SAMPLE_ENTRIES = ("ipcm", "fpcm")


def _muxer_for_entry(sample_entry: str) -> str:
    """返回能复现源轨 sample entry 的 ffmpeg muxer 名 (默认 mov)。"""
    entry = str(sample_entry or "").strip().lower()
    return "mp4" if entry in _MP4_SAMPLE_ENTRIES else "mov"


def _copy_stream(
    ffmpeg: Path,
    source: Path,
    stream_index: int,
    out_file: Path,
    muxer: str = "mov",
) -> tuple[bool, str]:
    """源音轨 stream copy 到独立容器 (untouched 轨 bit-exact 保留)。"""
    proc = subprocess.run(
        [
            str(ffmpeg), "-v", "error", "-nostdin", "-y",
            "-i", str(source),
            "-map", f"0:a:{stream_index}",
            "-vn", "-sn", "-dn",
            "-c:a", "copy", "-f", muxer, str(out_file),
        ],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace", timeout=1800,
    )
    ok = (
        proc.returncode == 0
        and out_file.is_file()
        and out_file.stat().st_size > 0
    )
    return ok, (proc.stderr or "")[-300:]


def _track_health(arr: Any, np: Any) -> tuple[bool, float]:
    """分块健康扫描: (全部有限?, rms_dbfs)。峰值内存恒定。"""
    ssq = 0.0
    n = arr.shape[0]
    chunk = 1 << 20
    for a in range(0, n, chunk):
        blk = np.asarray(arr[a: a + chunk], dtype=np.float64)
        if not np.all(np.isfinite(blk)):
            return False, float("nan")
        ssq += float(np.sum(blk * blk))
    if n == 0 or ssq <= 0.0:
        return True, float("-inf")
    return True, 10.0 * math.log10(ssq / n)


def _close_map(arr: Any) -> None:
    """关闭流句柄 (Windows 上删除文件前必须先关)。

    RawStream: close() 释放文件句柄; 旧 memmap: 关闭底层 _mmap 映射。
    """
    m = getattr(arr, "_mmap", None)
    if m is not None:
        try:
            m.close()
        except OSError:
            pass
        return
    close = getattr(arr, "close", None)
    if callable(close):
        try:
            close()
        except OSError:
            pass


def repair_remux_timescale(
    path: Path, log: Callable[[str], None] = print
) -> None:
    """按输出文件**实际** mvhd timescale 重跑 stts 时长修复 (P1 修正辅助)。

    preservation/audio_sync.remux_replace_audio 用源文件 mvhd timescale
    (rigaya 中间件 = 1000) 调用 isobmf.patch_track_durations, 而 GPAC
    26.02 的 -new 输出 mvhd timescale 实际为 3000 (不采纳 -timescale 1000)
    — 结果 tkhd/elst 被写成 1/3 时长 (2.667s vs 8s), 音频解码被 elst 截断。
    本函数从输出文件自身读取真实 mvhd timescale 后重跑同一修复, 把时长
    恢复为精确值 (stts 始终精确, 是可靠的事实来源)。P1 不修改
    preservation/, 故修正逻辑放在集成层并接入各 classic/transparent
    重封装调用点。
    """
    from preservation import isobmf

    with path.open("rb") as f:
        roots = isobmf.root_boxes(path)
        moov = next((b for b in roots if b.type == "moov"), None)
        if moov is None:
            raise RuntimeError(f"no moov box in {path}")
        mvhd = next(
            (b for b in isobmf._children(f, moov) if b.type == "mvhd"),
            None,
        )
        if mvhd is None:
            raise RuntimeError(f"no mvhd box in {path}")
        f.seek(mvhd.data_offset)
        ver = f.read(1)[0]
        f.seek(mvhd.data_offset + (20 if ver == 1 else 12))
        movie_ts = struct.unpack(">I", f.read(4))[0]
    if movie_ts <= 0:
        raise RuntimeError(f"invalid mvhd timescale {movie_ts}")
    for desc in isobmf.patch_track_durations(path, movie_ts, from_stts=True):
        log(desc)
    mv_desc = isobmf.patch_movie_duration(path)
    if mv_desc:
        log(mv_desc)


# ---------------------------------------------------------------------------
# 分组锚点 (opt-in): 建组与组内锚点 —— 纯函数, 因此可以被单测钉住
# ---------------------------------------------------------------------------


def group_by_snr(
    snr: Mapping[tuple[int, int], Mapping[str, float]],
    tracks: Iterable[int],
    *,
    threshold: float,
    min_samples: int,
) -> list[list[int]]:
    """把轨按「相关峰 / 噪声底」中位数连成组 (并查集连通分量)。

    纯函数: 不做 I/O, 不读 DEFAULTS —— 门是显式参数。这样才能被单测钉住,
    而"门"是这个特性里唯一真正需要被钉住的东西。

    两种**不可判定**的情形一律不连边:
      * 中位数为 nan      —— 这一对根本没测出来;
      * 有效样本 < min_samples —— 素材太短, 样本不足。
    "不确定"绝不能当成"相关": 那会让两条死轨被并进真实麦克风组, 而正确的
    行为是把它们隔离成孤立点 (实测 20260903_C1114: CH1 -64.3 dBFS /
    CH2 -68.1 dBFS 在噪声底上互相"相关", 比值 5.66)。

    已知局限: 单链接聚类会链式合并 (20260919_C1305 上曾出现一条 0.53% 的
    边界边把四轨并成一组)。改用「相关峰/噪声底」而不是「合格帧覆盖率」正是
    为了解决那个具体案例; 链式合并本身是该算法的性质, 不是实现缺陷。加法式
    合并把它限制在"少尝试一组", 不会丢掉任何已有修正。
    """
    order = sorted(set(int(t) for t in tracks))
    parent = {i: i for i in order}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (a, b), d in snr.items():
        if a not in parent or b not in parent:
            continue
        med = d.get("median")
        if med is None or med != med:
            continue
        if float(d.get("samples") or 0.0) < float(min_samples):
            continue
        if float(med) < float(threshold):
            continue
        ra, rb = find(int(a)), find(int(b))
        if ra != rb:
            parent[rb] = ra

    comp: dict[int, list[int]] = {}
    for i in order:
        comp.setdefault(find(i), []).append(i)
    return sorted((sorted(v) for v in comp.values()), key=lambda v: v[0])


def group_anchor_of(members: Iterable[int], candidates: Iterable[int]) -> int:
    """组内锚点 = `candidates` 顺序里最靠前的成员。

    刻意复用 `anchor_candidates` 而**不另发明一套优先级**: 单组四轨时它选出
    来的就是生产主锚点, 于是"同一组"的结论与现行逐位相同 —— 默认行为的
    等价性是这个性质推出来的, 不是靠回归去赌。不在候选表里的成员排在最后,
    并按轨序号稳定排序 (min 的 key 必须全序, 否则结果依赖集合遍历顺序)。
    """
    order = {int(c): k for k, c in enumerate(candidates)}
    return min((int(m) for m in members),
               key=lambda m: (order.get(m, len(order)), m))


def _detect_stereo_groups(
    *,
    streams: Sequence[Any],
    decoded: Sequence[Path],
    storages: Sequence[str],
    healthy: Sequence[int],
    sample_rate: int,
    eff: Mapping[str, Any],
    log: Callable[[str], None],
) -> tuple[list[list[int]], dict[str, Any]]:
    """默认 stereo 组绑定检测（2026-10-29 用户决策 C，任务书 §4.2）。

    复用既有实现，不新造聚类:
      1. `sync_estimate.sample_pair_snr` 给**所有健康对**抽帧相关强度；
      2. `group_by_snr`（并查集）按生产阈值 1.3 建连通分量；
      3. 只有**恰好 2 成员**的候选组才做刚性确认，判据全部来自既有探索文档:
         * 60 s 窗 / 10 s 跳的带符号相关中位 |CC| ≥ 0.90
           （long-take-alignment-final.md §13: 同源对 +0.982，跨组 0.43–0.61）；
         * 全片 lag span（p90−p10）≤ 5 采样
           （audio-sync-p2/investigation.md: CH1↔CH2 是唯一刚性对，span 0.021–0.104 ms）；
         * 同号窗比例 ≥ 0.95；
      4. 不满足任一条 → 不绑定（仍可同组共享锚点，但各自独立测量）。

    返回 `(bound_groups, report)`。检测开销: 抽帧强度 ~秒级, 只有候选对才做
    一次 60 s/10 s 连续性跟踪（80 min 约 40 s/对）—— 在绑定预算内。
    """
    from .long_take import PROFILE as LP
    from . import sync_estimate
    import numpy as np

    report: dict[str, Any] = {
        "enabled": True,
        "threshold": float(eff["group_snr_threshold"]),
        "samples_per_pair": int(eff["group_snr_samples"]),
        "min_samples": int(eff["group_snr_min_samples"]),
        "candidates": {},
        "groups": [],
        "bound_groups": [],
    }
    if len(healthy) < 2:
        return [], report
    snr_pairs: dict[tuple[int, int], Mapping[str, float]] = {}
    for a, b in itertools.combinations(sorted(healthy), 2):
        try:
            snr_pairs[(a, b)] = sync_estimate.sample_pair_snr(
                streams[a], streams[b],
                sample_rate=sample_rate,
                frame_ms=float(eff["frame_ms"]),
                hop_ms=float(eff["hop_ms"]),
                search_window_ms=float(eff["search_window_ms"]),
                coarse_rate=int(eff["coarse_rate"]),
                frame_min_rms_dbfs=float(eff["frame_min_rms_dbfs"]),
                samples=int(eff["group_snr_samples"]),
            )
        except Exception as exc:                       # noqa: BLE001
            log(f"channel-sync: stereo-bind 强度测量失败 "
                f"CH{a + 1}/CH{b + 1}: {type(exc).__name__}: {exc}")
    groups = group_by_snr(
        snr_pairs, healthy,
        threshold=float(eff["group_snr_threshold"]),
        min_samples=int(eff["group_snr_min_samples"]),
    )
    report["groups"] = [[m + 1 for m in g] for g in groups]
    report["snr"] = {f"{a + 1}-{b + 1}": {
        "median": d.get("median"), "samples": d.get("samples"),
    } for (a, b), d in snr_pairs.items()}
    # 绑定候选 = 强相关对（比建组门更高；见 long_take.PROFILE 注释）。不直接
    # 用 group_by_snr 的分量，是因为单链接会把 C1305 四轨并成一组，那样
    # "恰好 2 成员"永远不成立，真实 stereo 对就永远绑不上。
    cand_snr = float(LP["bind_candidate_snr"])
    ranked = sorted(
        (
            (float(d.get("median") or 0.0), a, b)
            for (a, b), d in snr_pairs.items()
            if d.get("median") == d.get("median")
            and float(d.get("median") or 0.0) >= cand_snr
            and float(d.get("samples") or 0.0)
            >= float(eff["group_snr_min_samples"])
        ),
        reverse=True,
    )
    bound: list[list[int]] = []
    used: set[int] = set()

    def evaluate(a: int, b: int) -> dict[str, Any]:
        """跑一次 60 s/10 s 连续性跟踪，返回候选证据（不决策、不打日志）。

        纯计算 + 只读 decoded raw，因此可以在线程池里对多个候选并行执行；
        决策（先到先得、按 SNR 排名）仍然在下面顺序进行，保证确定性。
        """
        info: dict[str, Any] = {
            "members": [a + 1, b + 1],
            "bound": False,
            "thresholds": {
                "min_abs_cc": float(LP["bind_min_abs_cc"]),
                "lag_span_samples": float(LP["bind_lag_span_samples"]),
                "same_sign_ratio": float(LP["bind_same_sign_ratio"]),
            },
        }
        if storages[a] != storages[b]:
            info["reason"] = "storage_dtype_mismatch"
            return info
        try:
            windows = sync_estimate.measure_window_delays(
                decoded[a], decoded[b],
                sample_rate=sample_rate,
                window_seconds=float(LP["track_window_seconds"]),
                hop_seconds=float(LP["track_hop_seconds"]),
                coarse_rate=int(LP["track_coarse_rate"]),
                min_cc=float(LP["track_min_cc"]),
                storage_dtype=str(storages[a]),
            )
        except Exception as exc:                       # noqa: BLE001
            info["reason"] = f"measure_failed: {type(exc).__name__}"
            return info
        rows = [w for w in (windows.get("windows") or [])
                if w.get("valid") and w.get("delay_samples") is not None]
        total = len(windows.get("windows") or [])
        info["windows"] = total
        info["valid_windows"] = len(rows)
        if len(rows) < max(8, int(0.5 * max(1, total))):
            info["reason"] = "too_few_valid_windows"
            return info
        signed = np.asarray(
            [float(w["coarse_cc"]) * int(w.get("polarity") or 1)
             for w in rows], dtype=np.float64)
        lags = np.asarray([float(w["delay_samples"]) for w in rows],
                          dtype=np.float64)
        abs_cc = float(np.median(np.abs(signed)))
        span = float(np.percentile(lags, 90) - np.percentile(lags, 10))
        pos_frac = float(np.mean(signed > 0))
        same_sign = max(pos_frac, 1.0 - pos_frac)
        info.update({
            "median_abs_cc": round(abs_cc, 4),
            "lag_span_samples": round(span, 3),
            "same_sign_ratio": round(same_sign, 4),
        })
        info["_gate"] = (
            abs_cc >= float(LP["bind_min_abs_cc"])
            and span <= float(LP["bind_lag_span_samples"])
            and same_sign >= float(LP["bind_same_sign_ratio"])
        )
        return info

    # S5: 候选对的 80 min 跟踪互相独立 → 并行测量（每对只读各自 decoded
    # raw，无共享写）。归并后按 SNR 排名顺序做决策，与串行实现的
    # "先到先得" 语义逐位一致；实测 2 候选从 ~2×T 降到 ~T（线程 FFT 释放 GIL）。
    pair_results: dict[tuple[int, int], dict[str, Any]] = {}
    pairs = [(a, b) for _score, a, b in ranked]
    if len(pairs) > 1:
        with ThreadPoolExecutor(max_workers=min(4, len(pairs))) as ex:
            futs = {ex.submit(evaluate, a, b): (a, b) for a, b in pairs}
            for fut, key in list(futs.items()):
                try:
                    pair_results[key] = fut.result()
                except Exception as exc:               # noqa: BLE001
                    pair_results[key] = {
                        "members": [key[0] + 1, key[1] + 1],
                        "bound": False,
                        "reason": f"measure_failed: {type(exc).__name__}",
                    }
    else:
        for a, b in pairs:
            pair_results[(a, b)] = evaluate(a, b)

    for _score, a, b in ranked:
        key = (a, b)
        info = pair_results.get(key)
        if info is None:
            continue
        report["candidates"][f"{a + 1}-{b + 1}"] = info
        if a in used or b in used:
            # 与串行一致: 已被更高分候选占用的轨不再尝试（结果已测，仅不记
            # 绑定）。保留候选记录便于诊断，不写 reason（它是重叠而非失败）。
            info["overlapped"] = True
            continue
        gate = info.pop("_gate", None)
        if gate is None:
            continue
        if gate:
            info["bound"] = True
            bound.append([a, b])
            used.update((a, b))
            log(
                f"channel-sync: stereo group bound CH{a + 1}+CH{b + 1} "
                f"(|CC| {info['median_abs_cc']:.3f}, "
                f"lag span {info['lag_span_samples']:.2f} samples, "
                f"same-sign {info['same_sign_ratio']:.0%})"
            )
        else:
            log(
                f"channel-sync: stereo pair CH{a + 1}/CH{b + 1} NOT bound "
                f"(|CC| {info['median_abs_cc']:.3f}, "
                f"span {info['lag_span_samples']:.2f}, "
                f"same-sign {info['same_sign_ratio']:.0%})"
            )
    report["bound_groups"] = [[m + 1 for m in g] for g in bound]
    if not bound:
        report["note"] = (
            "no rigid 2-member group met the binding gates; every channel is "
            "measured independently (grouping still may share an anchor)"
        )
    return bound, report


def _annotate_eligibility(rep: dict[str, Any]) -> None:
    """给报告的每一行加上**修正资格**判定 (原地, 失败不影响报告)。

    P4.4: 生产者与消费者必须用**同一个**判定入口。生产者在此之前只在内部
    知道自己决定了什么; 这里把它写成报告的一部分, 于是"这条轨被写成 fixed"
    与"这条轨允许进入修正路径"可以被独立核对 —— 两者不一致就是缺陷。

    导入是函数内的: `core.audio_models` 反向引用本模块的 `effective_opts`,
    模块级互相导入会成环, 而这条注解是**报告增强**, 不是算法的一部分。
    """
    try:
        from .audio_models import ChannelSyncReport, correction_eligibility

        view = ChannelSyncReport(rep)
        gate = view.evidence_gate
        for row in view.channels:
            res = view.resolve(row)
            row["eligibility"] = correction_eligibility(res, gate=gate).to_dict()
    except Exception as exc:                       # noqa: BLE001 - 报告增强
        rep["eligibility_note"] = f"not computed: {type(exc).__name__}: {exc}"


def _validate_tv_opts(eff: dict[str, Any]) -> str | None:
    """时变路径的**必需参数**校验。返回错误描述, 或 None 表示可以执行。

    P4.6 的核心约定: D2 (gap policy) 与 D5 (coordinate contract) 都是**未冻结
    的产品决策**, 因此生产路径**没有默认值**。调用方必须同时说出两者, 说不全
    就拒绝 —— 而不是替它选一个实现默认值, 然后让那个默认值在报告里看起来像是
    产品语义。

    校验放在**打开文件之前**: 一条 900 MB 的流不该先被解码再因为一个参数缺失
    而中止。
    """
    missing: list[str] = []
    if eff.get("tv_policy") is None:
        missing.append("tv_policy (D2 gap policy)")
    if eff.get("tv_coordinate") is None:
        missing.append("tv_coordinate (D5 coordinate contract)")
    if missing:
        return (
            "time-varying sync requires an explicit "
            + " and ".join(missing)
            + "; neither decision has a frozen product semantic, so this path "
              "refuses rather than choosing an implementation default. Known "
              "policies: hold, no_correction_gap, explicit_state, linear, next "
              "(policy 'current' is a research policy and is NOT accepted). "
              "Known coordinates: latest-start, nearest-centre."
        )
    return None


def _apply_time_varying_track(
    i: int, e: Any, src: Any, dst: Any, sample_rate: int,
    storage_dtype: str, eff: dict[str, Any], log: Any,
) -> tuple[Any, dict[str, Any]]:
    """把一条轨的**帧轨迹**变成逐窗计划并施加。返回 (结果或 None, 详情)。

    这一步只做适配与调度: 测量**已经发生** (`e.frames`), 坐标与策略由调用方
    显式指定, 计划的构造/施加/独立复测全部在 `core.audio_sync_tv_exec` 里 ——
    本函数不重新实现其中任何一件。

    详情字典进报告行, 因此"这条轨为什么没被逐窗修正"可以直接从报告读出来,
    不需要调试器。
    """
    from . import audio_sync_tv as tv
    from . import audio_sync_tv_exec as tve

    win = eff["tv_window_samples"] or tv.DEFAULT_WINDOW_SAMPLES
    hop = eff["tv_hop_samples"] or tv.DEFAULT_HOP_SAMPLES
    search = eff["tv_search_samples"] or tv.DEFAULT_SEARCH_SAMPLES
    frames = list(getattr(e, "frames", []) or [])
    stats = tve.frame_aggregation_stats(
        frames, n_samples=int(src.shape[0]), window_samples=int(win),
        hop_samples=int(hop),
        min_frames_per_window=int(eff["tv_min_frames_per_window"]),
    )
    detail: dict[str, Any] = {
        "policy": eff["tv_policy"], "coordinate": eff["tv_coordinate"],
        "window_samples": int(win), "hop_samples": int(hop),
        "aggregation": stats,
    }
    try:
        request = tve.request_time_varying(
            enabled=True, policy=eff["tv_policy"],
            coordinate=eff["tv_coordinate"],
            window_samples=int(win), hop_samples=int(hop),
            search_samples=int(search),
            chunk_samples=int(eff["tv_chunk_samples"]),
            # P4.7: the PRODUCTION path must clear the coverage gate. The
            # track-level frame gate above already admitted this track, so a plan
            # far below the gate means the frame->window aggregation collapsed --
            # executing that would apply a correction to a stream the plan has
            # almost no evidence about.
            require_evidence_gate=True,
        )
    except tve.TimeVaryingRefused as exc:
        detail.update({"executed": False, "reason": exc.reason,
                       "detail": exc.detail})
        return None, detail

    records = tve.aggregate_frames_to_windows(
        frames, n_samples=int(src.shape[0]), window_samples=int(win),
        hop_samples=int(hop),
        min_frames_per_window=int(eff["tv_min_frames_per_window"]),
    )
    try:
        res = tve.run_time_varying(
            src, dst, request=request, measurements=records,
            storage_dtype=storage_dtype, verify=True,
        )
    except tve.TimeVaryingRefused as exc:
        detail.update({"executed": False, "reason": exc.reason,
                       "detail": exc.detail})
        return None, detail
    detail.update(res.to_dict())
    log(
        f"channel-sync: CH{i + 1} time-varying "
        f"({'applied' if res.executed else 'REFUSED'}"
        f"{'' if res.executed else ' ' + str(res.reason)}) "
        f"policy={eff['tv_policy']} coordinate={eff['tv_coordinate']} "
        f"windows={res.n_windows} states={dict(res.state_counts)}"
    )
    return res, detail


MetadataRestoreHook = Callable[
    [Path, list[str], Path, Path, Any, Callable[[str], None]], "Path | None"
]


def skip_two_channel_only(
    n_tracks: int,
    n_healthy: int,
    enabled: bool = True,
) -> bool:
    """音频流 >= 3 但只有 2 条健康时, 是否按默认策略整文件跳过对齐。

    为什么是"2"而不是"少于 3": 3 条音频流里 2 条健康 (2CH 有效) 与 4 条里
    2 条健康是同一类素材形态; 对这两条有效轨做相对纠正在用户口径里没有
    意义 (它们本来就是一对), 但重封装/重编码成本是整文件级的。因此默认
    跳过, 报告 `two_channel_only`; 需要时可用
    ``opts={"skip_two_channel_only": False}`` 恢复逐轨处理。
    """
    return bool(enabled) and int(n_tracks) >= 3 and int(n_healthy) == 2


def is_aligned_decision(
    delay_ms: float,
    delay_samples: float,
    *,
    aligned_max_ms: float,
    min_shift_samples: int,
) -> bool:
    """这条轨是否应当判 `already_aligned` (即不做任何写入)。

    **这是"要不要动这个文件"的唯一判据**, 因此抽成纯函数以便逐边界测试 ——
    它藏在 `decide_and_record` 内联时, 只有跑真实素材才能触到, 而真实素材
    恰好最缺边界样本。

    三个条件任一成立即不动:

    ``|delay_ms| < aligned_max_ms``
        毫米级带。与采样率无关, 表达"这根本不是个时差"。

    ``|delay_samples| < 1.0``
        量化粒度: 修正只做整数样本移位, 不足 1 样本时移位恒为 0。

    ``|rint(delay_samples)| <= min_shift_samples``
        **最小幅度** (v0.8.7 产品决定: 5 样本以内都不修)。用 rint 之后的
        整数量判定, 与下游真正写入的 `shift_samples` 是同一个数。
        48 kHz 下 5 样本 = 0.104 ms; 真实缺陷的位移是 37~1222 样本,
        与它差两个数量级, 所以这条线不会挡掉任何真实问题。
    """
    shift = int(round(delay_samples))
    return (
        abs(delay_ms) < aligned_max_ms
        or abs(delay_samples) < 1.0
        or abs(shift) <= int(min_shift_samples)
    )


#: 精确模式的分级候选, 由细到粗; 最后一个必须等于默认 `min_shift_samples`。
ACCURATE_TIERS: tuple[int, ...] = (1, 2)


def resolve_accurate_tier(
    extras: Mapping[int, tuple[int, float]],
    *,
    min_shift_samples: int,
) -> tuple[int, set[int], dict[int, str]]:
    """`--channel-sync-accurate`: 决定这一批小修正能不能按多细的粒度写下去。

    `extras` 是"只因为精确模式降低了门才被判 fixed"的轨:
    `{轨道下标: (shift_samples, 复检残差样本数)}`。

    **"可不可行"用的是施加之后的独立复检残差, 不是阈值猜测。** 这就是整个
    代码库对"这个数值到底对不对"的一贯判据 (`recheck_residual`); 换一套
    别的度量只会多出一个没人能复核的说法。

    分级:
      * tier 1 — 每条 extra 的复检残差都 ≤ 1 样本 → 按 1 样本粒度采纳;
      * tier 2 — 1 样本做不到时, 丢掉 `|shift| == 1` 的轨, 只看剩下的,
        残差 ≤ 2 样本即可;
      * 否则 —— 全部退回常规 `min_shift_samples` 门, 并如实报告。

    返回 `(tier, keep, dropped)`: `keep` 是保留下来的轨下标集合,
    `dropped` 是 `{下标: 原因}`。
    """
    if not extras:
        return min_shift_samples, set(), {}

    for tier in ACCURATE_TIERS:
        keep = {i for i, (sh, _r) in extras.items() if abs(sh) > tier}
        bad = {i: f"residual {extras[i][1]:.2f} samples > {tier}"
               for i in keep if not extras[i][1] <= tier}
        if not bad:
            # 该层通过: 比它更小的轨按本层粒度判为已对齐
            dropped = {i: f"|shift| <= {tier} at tier {tier}"
                       for i in extras if abs(extras[i][0]) <= tier}
            return tier, keep, dropped

    dropped = {i: f"residual {extras[i][1]:.2f} samples exceeds tier 2"
               for i in extras}
    return min_shift_samples, set(), dropped


def compose_reference_shift(
    delay_vs_reference_samples: float,
    reference_shift_samples: float,
    sample_rate: int,
) -> dict[str, Any] | None:
    """把"相对某条参考轨的延迟"合成为"相对主锚点的绝对移位"。

    符号约定与生产一致: `delay > 0` = 目标晚到, 施加的整数移位就是 delay 本身
    (`sync_fix.shift_stream(out[n] = in[n + shift])`)。因此链式合成是**加法**:

        shift(t) = shift(ref) + delay(t 相对 ref)

    例: CH2 相对主锚点晚 +19.02 ms, CH1 相对 CH2 晚 +6.76 ms
        -> CH1 相对主锚点晚 25.78 ms (与直接测 CH1↔锚点的 25.4 ms 一致)。

    返回 None = 不可用 (非有限值/参考移位非整数)。返回 dict 供报告与复核引用;
    组合值本身的正确性仍由修后残差核验 (拿主锚点做独立比对), 不靠这道算术自证。
    """
    try:
        d = float(delay_vs_reference_samples)
        r = float(reference_shift_samples)
    except (TypeError, ValueError):
        return None
    if d != d or r != r:
        return None
    if int(r) != r:
        return None
    composed = d + r
    return {
        "delay_vs_reference_samples": d,
        "delay_vs_reference_ms": d * 1000.0 / float(sample_rate),
        "reference_shift_samples": int(r),
        "composed_samples": composed,
        "composed_ms": composed * 1000.0 / float(sample_rate),
    }


def materiality_accepts(
    delay_samples: float,
    displacement_ms: float,
    sample_rate: int,
    *,
    k: float,
) -> dict[str, Any] | None:
    """材料性判据: 修正值不值得做?

    `k <= 0` 时关闭 (返回 None, 行为与旧实现逐位一致)。

    开启后, 恒定性不通过的轨只要满足

        |delay| >= k × 实测稳健位移

    就按"可修"处理。理由是**比较修正前后哪个误差更小**:
    施加一个整数移位后, 残余的上界就是实测位移 (轨迹在该轨上的真实变化量);
    而拒绝它留下的误差是 |delay| 本身。两者相差 k 倍以上时, 修显然更优 ——
    拒绝等于"为了不引入 0.78 ms 而留下 24 ms"。

    为什么用它替代"放宽离群门": 实测 80 条被拒轨里, 全范围门(离群)拦下 20 条、
    稳健位移门拦下 36 条; 材料性判据对这两类给出**同一个**判断依据 (比值),
    不需要再单独调全范围门的系数。`F:\\align_4ch_proof` 复核显示, 在 4ch 均有效
    的素材上 83% 的未修轨属于"有共同内容、只是被判据拦下", 这条判据正对它们。

    返回 None = 不接受; 否则返回证据 dict (供报告与复核引用)。
    """
    try:
        kk = float(k)
    except (TypeError, ValueError):
        return None
    if not (kk > 0.0):
        return None
    if delay_samples != delay_samples or displacement_ms != displacement_ms:
        return None
    delay_ms = abs(float(delay_samples)) * 1000.0 / float(sample_rate)
    disp = abs(float(displacement_ms))
    if delay_ms < kk * disp:
        return None
    return {
        "k": kk,
        "delay_ms": delay_ms,
        "displacement_ms": disp,
        "ratio": (delay_ms / disp) if disp > 0.0 else float("inf"),
    }


def _agree_with_independent_estimate(
    idx: int,
    est: Any,
    shift: int,
    decoded: dict[int, Path],
    ref_idx: int,
    anchor: int,
    sample_rate: int,
    storage_dtype: str,
    eff: dict[str, Any],
) -> dict[str, Any] | None:
    """第三道门: 用**另一条估计路径**复核 `shift`, 不一致则返回详情。

    返回值 None = 一致 (放行); dict = 不一致 (调用方把该轨退回 untouched)。

    为什么需要它 (实测 2026-10-20): 只放宽"残差"门会放行四类轨里的两类坏样本 ——
    `rel=0.2` 在语料上新增 4 条, 其中 `C1167 CH2` (shift −42, 实测 −37) 与
    `C1267 CH1` (shift 1056, 实测 1050) **数据写对了、数值是错的**。
    残差门天生看不出这种错: 移位是按错误值施加的, 复检量到的"残余"当然小。
    只有"两条独立估计彼此一致"能识别它。

    这里的独立路径是: 宽搜索窗 (±|delay|+10 ms) 整段 GCC-PHAT + 相位斜率精估,
    沿片长取 `recheck_windows` 个窗, 取**中位**作为独立估计。它与生产估计器
    (逐帧窄窗 GCC + 轨迹统计) 的窗口、统计量和搜索范围都不同。
    """
    wide_ms = min(float(eff["wide_search_ms"]),
                  abs(float(est.delay_ms)) + 10.0)
    if wide_ms <= 0.0:
        return None
    # `sync_fix` 在生产代码里是**函数内惰性导入** (`run_channel_sync` 里
    # `from . import sync_estimate, sync_fix`), 模块层没有这个名字。
    # 这里必须自己导入: 否则 NameError 会被下面的宽 except 吞掉, 门永远返回
    # "一致" —— 一个"跑起来不报错、结果永远放行"的静默缺陷。
    from . import sync_fix as _sf

    try:
        pre = _sf.recheck_residual_windows(
            decoded[ref_idx], decoded[idx],
            sample_rate=sample_rate,
            storage_dtype=storage_dtype,
            windows=max(1, int(eff["recheck_windows"])),
            search_ms=wide_ms,
            anchor_segment_seconds=eff["anchor_segment_seconds"],
            fine_phase_band_hz=tuple(eff["fine_phase_band_hz"]),
        )
    except Exception:                     # noqa: BLE001 — 测不出就当一致
        # 独立路径本身失败时**不能**据此拒绝: 那属于"证据不足", 由别的门负责。
        # 这里只做加成性的复核, 不做否决权的扩张。
        return {"measured": False, "accepted": True, "reason": "measure_failed"}
    vals = [v for v in pre["values"] if v == v]
    if not vals:
        return {"measured": False, "accepted": True, "reason": "no_window"}
    ind_ms = float(sorted(vals)[len(vals) // 2])
    ind_samples = ind_ms * sample_rate / 1000.0
    diff = abs(ind_samples - float(shift))
    delay_samples = abs(float(est.delay_samples))
    tol = effective_agree_tolerance(
        eff, delay_samples=delay_samples, ind_samples=ind_samples,
        window_values=list(pre["values"]), sample_rate=sample_rate)
    return {
        "measured": True,
        "independent_ms": ind_ms,
        "independent_samples": ind_samples,
        "shift_samples": int(shift),
        "diff_samples": diff,
        # 判据的三种表达都记下来, 便于离线复核"该用哪种量纲":
        #   绝对样本 / 相对偏差 / 以估计器自身散布为单位
        "diff_ratio": (diff / delay_samples) if delay_samples > 0 else None,
        "tolerance_samples": tol["tolerance_samples"],
        "tolerance_basis": tol["basis"],
        "window_spread_ms": tol["window_spread_ms"],
        "accepted": diff <= tol["tolerance_samples"],
        "windows_ms": list(pre["values"]),
        "search_ms": wide_ms,
    }


def effective_agree_tolerance(
    eff: dict[str, Any],
    *,
    delay_samples: float,
    ind_samples: float,
    window_values: list[float],
    sample_rate: int,
) -> dict[str, Any]:
    """一致性门容差该用什么量纲: 绝对样本 / 相对偏移 / 估计器自身散布。

    固定采样数的问题: 20 采样是 0.42 ms —— 对 24 ms 的偏移是 1.7%, 对 5 ms
    的偏移却是 8%。同一个数字在不同量级上要求完全不同, 所以容差应当随被测量
    的尺度走。

    三种候选 (取**最宽**的那个, 因为一道门不应因量纲假设而更严):

      * 绝对下限 `verify_agree_samples` (采样): 永远保底, 避免小偏移被过度卡;
      * 相对偏差 `verify_agree_rel × |delay|`: "两条估计相差不超过偏移的 x%",
        与偏移量级同尺度;
      * 估计器散度 `verify_agree_sigma × 窗间离散`: 用独立路径自己的多窗
        标准差当不确定度, "两个估计的差在 3σ 内即视为一致"。这是统计上
        最正确的量纲 —— 它把"测量本身有多不确定"直接写进判据。

    返回选取的容差与依据, 供报告引用。
    """
    tol_abs = float(eff["verify_agree_samples"])
    basis = "absolute"
    delay = abs(float(delay_samples))
    rel = float(eff["verify_agree_rel"])
    if rel > 0.0 and delay > 0.0:
        cand = rel * delay
        if cand > tol_abs:
            tol_abs, basis = cand, "relative"
    sigma = float(eff["verify_agree_sigma"])
    good = [v for v in window_values if v == v]
    # 窗间离散**总是**算: 它是"独立估计本身有多不稳定"的直接度量, 即使当前
    # 没启用 sigma 判据也要记进报告 —— 否则事后无法回答"容差该用哪种量纲"
    # 这个问题（55 条样本里 Δ 大小与修正质量并不相关, 相关的是相对不确定度）。
    if len(good) >= 2:
        mean = sum(good) / len(good)
        var = sum((v - mean) ** 2 for v in good) / (len(good) - 1)
        sd_ms = var ** 0.5
    else:
        sd_ms = float("nan")
    if sigma > 0.0 and sd_ms == sd_ms:
        cand = sigma * sd_ms * float(sample_rate) / 1000.0
        if cand > tol_abs:
            tol_abs, basis = cand, "sigma"
    return {"tolerance_samples": tol_abs, "basis": basis,
            "window_spread_ms": sd_ms}


def remux_transparent_audio(
    *,
    source: Path,
    dst: Path,
    audio_files: list[Path],
    work_dir: Path,
    gpac: Any,
    restore_metadata: MetadataRestoreHook | None,
    streams: list[dict[str, Any]],
    log: Callable[[str], None],
) -> tuple[Path | None, bool]:
    """透明重封装: 视频/非音频 stream copy + 调用方给定的音频轨。

    本函数从 `run_channel_sync` 的透明输出步骤**原样抽出**, 唯一区别是音频
    轨由调用方给出 (普通 channel-sync 给的是"被修正轨 + 源轨 copy", 长素材
    对齐流程给的是"逐窗对齐后的 4 条轨")。容器处理完全共用: GPAC `mux_new`
    + Sony 元数据恢复钩子 + mvhd timescale 修复 + PCM sample entry 校正 +
    tkhd flags/alternate_group 归一; 任何一步失败都回退"源文件原样拷贝",
    不交半成品。

    返回 ``(output_file, file_copied)``; ``file_copied=True`` 表示走了回退
    拷贝。只有连回退拷贝都失败时才抛 `RuntimeError`。
    """
    if gpac is None:
        log("channel-sync: transparent remux needs GPAC — 回退原样拷贝")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dst)
        return dst, True
    try:
        from preservation.dji import track_manifest

        movie_ts, tracks = track_manifest(gpac, source)
        video_any = any(
            t["handler"] == "vide" for t in tracks
        )
        if not video_any:
            raise RuntimeError(
                f"no video track in {source} for transparent remux"
            )
        gpac.movie_timescale = movie_ts
        # 按**源容器里的轨道顺序**重建 `-add` 列表, 而不是把
        # "视频 -> 非音频 -> 全部音频" 这个固定搭配写死。
        #
        # 为什么顺序重要: 它属于容器语义。实测 20260904_C1214 的
        # 源是 [video, a0..a3, rtmd], 而旧实现产出
        # [video, rtmd, a0..a3] —— 六条流的 packet md5 全部相同
        # (内容无损), 但**按流序号定位**的下游 (脚本 / NLE /
        # 部分校验工具) 会因此读错轨。`mux_new` 严格按 `-add` 的
        # 先后建轨, 所以顺序只能在这里定。
        soun_ids = [t["id"] for t in tracks
                    if t["handler"] == "soun"]
        if len(soun_ids) != len(audio_files):
            # 轨道清单与音频文件数对不上: **不猜**。静默按新顺序
            # 拼会张冠李戴, 那比"顺序变了"严重得多, 因此退回旧
            # 顺序并把原因写进日志。
            log(
                f"channel-sync: transparent remux — 源 soun 轨 "
                f"{len(soun_ids)} 条, 音频文件 "
                f"{len(audio_files)} 条, 无法保序 — 退回 "
                f"\"视频+数据+音频\" 顺序"
            )
            adds = [f"{source}#video"]
            for t in tracks:
                if t["handler"] in ("vide", "soun"):
                    continue
                adds.append(f"{source}#{t['id']}")
            for af in audio_files:
                adds.append(f"{af}#audio")
        else:
            adds = []
            k = 0
            for t in sorted(tracks,
                            key=lambda x: x.get("number", 0)):
                h = t["handler"]
                if h == "vide":
                    adds.append(f"{source}#video")
                elif h == "soun":
                    # 音频轨用重生成/拷贝后的独立文件替换, 但
                    # **占回它原来的位置**。
                    adds.append(f"{audio_files[k]}#audio")
                    k += 1
                else:
                    adds.append(f"{source}#{t['id']}")
            log("channel-sync: transparent remux — 按源轨顺序 ["
                + ", ".join(
                    "vide" if a.endswith("#video")
                    else "soun" if a.endswith("#audio")
                    else "data"
                    for a in adds) + "]")
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            dst.unlink()
        log("channel-sync: transparent remux (video/non-audio "
            "stream copy + fixed audio) with MP4Box...")
        # Sony 源必须走**与保留管线相同的恢复步骤**, 否则重封装
        # 会把容器层元数据全部丢掉。实测 20260904_C1214:
        #   ftyp      XAVC -> isom
        #   rtmd      tref/cdsc 关联丢失 (track payload 本身逐字节相同)
        #   nrtm      镜头档案 meta item 整项缺失
        #   uuid      PROF/UNKNOWN/USMT = 1/1/7 -> 0/0/2
        #   后果: Gyroflow 从成品读到 0/146146 IMU 帧、认不出相机
        # 也就是说"6 条流 packet md5 全同"只在**流**层面成立,
        # 容器层该保的一样都没保住。
        sony_meta = (
            any(st.get("codec_type") == "data"
                and st.get("codec_tag_string") == "rtmd"
                for st in streams)
        )
        stage: Path | None = None
        if sony_meta:
            # `restore_metadata` 在 run_channel_sync 入口已保证非 None;
            # 其它调用方若绕过该契约, 这里会失败并回退源拷贝。
            stage = restore_metadata(
                source, adds, dst, work_dir, gpac, log
            )
        if stage is None:
            gpac.mux_new(dst, adds)
        # GPAC 输出 mvhd timescale 与源不一致: 按实际 timescale
        # 修复 tkhd/elst 时长 (同 classic 路径修正辅助)
        repair_remux_timescale(dst, log=log)
        # 音频 sample entry 被 GPAC 从 ffmpeg 的 QuickTime `in24`
        # 翻译成 MP4 `ipcm` 时把位深写错 (24 bit -> 16 bit), 条目
        # 自相矛盾: `pcmC` 说 24, `samplesize` 说 16。ffmpeg 采信
        # `pcmC` 所以解码正确, 但 Premiere/VLC 采信 `samplesize`,
        # 四轨全成白噪音。这里用源的条目覆盖 (去掉 btrt 保持等长,
        # 因此不需要动任何偏移)。失败只记 WARNING, 不影响已产出文件。
        try:
            from preservation.sample_entry import (
                audio_entry_consistency,
                patch_audio_sample_entries,
                read_audio_entries,
            )

            # 先给**源**做同一判据: 补丁的语义是"用源覆盖成品", 因此只有
            # 源本身自洽时, "成品补丁后不自洽"才证明是 GPAC 写坏。16-bit
            # ipcm 等布局的 packet-size 字段位置与本解析器假设不同 (实测源
            # 自己的 entry 就报"包长=0x70636D43"), 那种情况不能拿解析器的
            # 未知当成品缺陷 — 记 WARNING 继续, 否则透明模式会无谓回退成
            # 源文件拷贝 (实测 C17 的 pcm_s16le 输入)。
            src_entries = read_audio_entries(source)
            src_consistent = bool(src_entries) and all(
                audio_entry_consistency(e["bytes"])["consistent"]
                for e in src_entries
            )
            sdp = patch_audio_sample_entries(
                source, dst, log=log)
            if sdp.get("consistent_after") is False and src_consistent:
                # 条目自相矛盾 (pcmC 说 24 / samplesize 说 16) 是
                # 无条件错误: ffmpeg 解码正确, 但 Premiere/VLC
                # 会按 samplesize 读成白噪音。宁可退回源文件拷贝,
                # 也不交一个"能解码但播不对"的成品。
                raise RuntimeError(
                    "音频 sample entry 补丁后仍自相矛盾: "
                    + str(sdp.get("reason")))
            if not sdp.get("ok"):
                log("channel-sync: WARNING 音频 sample entry 未能"
                    f"与源一致 — {sdp.get('reason')}")
            elif sdp.get("consistent_after") is False:
                log("channel-sync: WARNING 源音频 sample entry 不被本校验器"
                    "支持 — 跳过一致性硬门 (已用源条目覆盖, 等长)")
        except RuntimeError:
            raise
        except Exception as exc:      # noqa: BLE001
            log(f"channel-sync: WARNING sample entry 补丁异常: "
                f"{exc}")
        # tkhd 轨道启用标志 / alternate group 归一 (最后一道容器补丁)。
        # ffmpeg/硬件 wrapper 会给第二条及之后的音轨写 flags=2 并把
        # 所有音轨放进 alternate_group=1..N; ISO/IEC 14496-12 把该组
        # 定义为"同一轨的备选版本", NLE 因此只显示一条音轨 (媒体信息
        # 仍列四条)。源文件是每轨 flags=7/alternate_group=0, 这里把
        # 容器事实恢复回来。失败只记 WARNING, 不阻断交付。
        try:
            from preservation.isobmf import normalize_track_flags

            for desc in normalize_track_flags(dst):
                log(f"channel-sync: {desc}")
        except Exception as exc:      # noqa: BLE001
            log("channel-sync: WARNING tkhd 轨道标志归一失败: "
                f"{exc}")
        return dst, False
    except Exception as exc:      # noqa: BLE001
        log(f"channel-sync: transparent remux failed: {exc} — "
            "回退原样拷贝")
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dst)
            return dst, True
        except OSError as exc2:
            raise RuntimeError(
                f"transparent fallback copy failed: {exc2}") from exc2


def run_channel_sync(
    *,
    source: Path,
    ffmpeg: Path,
    work_dir: Path,
    streams: list[dict[str, Any]],
    opts: dict[str, Any] | None = None,
    log: Callable[[str], None] = print,
    transparent: bool | None = None,
    dst: Path | None = None,
    gpac: Any | None = None,
    restore_metadata: MetadataRestoreHook | None = None,
) -> dict[str, Any]:
    """测量 -> 锚点回退 -> 轨道级质量门 -> 整数修正 -> 复检 -> 输出。

    transparent: 显式覆盖 opts["channel_sync_transparent"]; 为 True 时
    dst 必填 (透明模式最终输出路径)。

    restore_metadata: 透明模式的**容器层恢复钩子**, 依赖倒置用。签名
    `(source, adds, dst, work_dir, gpac, log) -> stage | None`。本模块是
    音频同步层, 不导入任何 Sony / 编码器 / 封装库 (P4 审计 A.1 强制),
    因此容器层恢复由调用方注入 —— 见 `preservation/transparent_restore.py`
    的模块 docstring。`transparent=True` 且给了 dst 却不注入钩子时**立即
    抛 ValueError**: 静默交出一个元数据被剥光的"成品"正是要根除的缺陷。

    status:
      applied         至少一轨 fixed; audio_files 含全轨音频文件
                      (fixed 轨已移位; 其余轨为源轨 stream copy)
      already_aligned 无轨需要修正 (可含轨道级 untouched, 见 channels)
      not_eligible    布局不满足 (非多流单声道 PCM / 采样率超范围)
      two_channel_only 音频流 >= 3 但只有 2 条健康 (其余静音/不可测):
                      按 2026-10-25 默认策略整文件跳过对齐 (原音频不动)
      measure_failed  文件级: 无有效锚点 / 全部健康 target 均不可靠 /
                      解码失败 (原音频照旧 + 警告)
      verify_failed   文件级: 回编码/重封装失败 (原音频照旧 + 警告)
      tool_missing    numpy/scipy 缺失
    除 applied 外宿主一律保持原音频不动; applied 时轨道级 untouched
    轨 (silent_track/non_finite/低置信/非恒定/超范围/复检失败) 同样
    不被修改 — 绝不静音或乱移任何轨。
    """
    eff = effective_opts(opts)
    t_mode = bool(transparent if transparent is not None
                  else eff["channel_sync_transparent"])
    # 契约检查放在**任何工作开始之前**: 透明模式要写成品, 而写成品就必须
    # 有能力恢复容器层。等到 mux 之后才发现没钩子, 只能二选一 —— 交出元数据
    # 被剥光的文件 (不可接受), 或回退成源文件拷贝 (看起来成功, 实际没做对齐
    # 之外还丢了一次真实重封装)。前移成启动期错误, 二者都不会发生。
    if t_mode and dst is not None and restore_metadata is None:
        raise ValueError(
            "transparent channel-sync requires the restore_metadata hook: "
            "the audio-sync layer must not import the container layer, so the "
            "caller has to inject it (see preservation/transparent_restore.py)"
        )
    work_dir.mkdir(parents=True, exist_ok=True)
    stem = source.stem
    ctx: dict[str, Any] = {"anchor": None}

    def report(
        status: str,
        detail: str,
        *,
        scope: str | None = None,
        channels: list[dict] | None = None,
        audio_files: list[Path] | None = None,
        fixed_files: list[Path] | None = None,
        output_file: Path | None = None,
        file_copied: bool = False,
    ) -> dict[str, Any]:
        rep = {
            "status": status,
            "detail": detail,
            "source": str(source),
            "algo_version": eff["algo_version"],
            # P4.3 (D1): 产出本报告时**实际生效**的证据门。消费者据此判定
            # "SUCCESS 是否证据充分", 因此门必须随报告走 —— 用 `opts` 覆盖过
            # 阈值的运行, 其结论不能被默认阈值重新解释。三个都是数值, 不含
            # 任何写死的常量 (全部来自 eff)。
            "evidence_gate": {
                "min_usable_frames": int(eff["min_usable_frames"]),
                "min_confidence": float(eff["min_confidence"]),
                "min_usable_fraction": float(eff["min_usable_fraction"]),
            },
            "anchor_stream": ctx["anchor"],
            "sync_mode": "transparent" if t_mode else "transcode",
            "result_scope": scope,
            # 旧键保留 (兼容下游读取): 语义 = 锚点流
            "reference_stream": ctx["anchor"],
            "channels": channels or [],
            "audio_files": [str(p) for p in (audio_files or [])],
            "fixed_files": [str(p) for p in (fixed_files or [])],
            "output_file": str(output_file) if output_file else None,
            "file_copied": bool(file_copied),
            "log_dir": str(work_dir),
        }
        # 分组信息只在**显式打开**时出现。默认关闭时报告与引入该特性前
        # 逐字节相同 —— 下游按字段读取的地方不会因为多一个键而改变行为。
        if ctx.get("grouping") is not None:
            rep["grouping"] = ctx["grouping"]
        # 2026-10-29 长镜头默认对齐: 可选的 stereo 组与 long_take 块。只有
        # 真的发生/判定过才出现 —— 旧路径的报告字段集合不变。
        if ctx.get("stereo_groups") is not None:
            rep["stereo_groups"] = ctx["stereo_groups"]
        if ctx.get("long_take") is not None:
            rep["long_take"] = ctx["long_take"]
        # 精确模式同理: 只有真的打开才出现, 而且必须有内容可报 ——
        # 一个"enabled: true 但没有任何候选"的块本身也是信息 (开关没起
        # 作用), 所以开着的就写。
        if ctx.get("accurate") is not None:
            rep["accurate"] = ctx["accurate"]
        # P4.4: 每一行都附上**修正资格**判定 —— 与消费者用的是同一个入口
        # (`correction_eligibility`), 因此生产者写的 `decision="fixed"` 与
        # "允许进入修正路径" 是同一件事的两种说法, 而不是各说各话。
        # 这里是所有终态的唯一汇合点, 所以在 report() 里做一次。
        _annotate_eligibility(rep)
        (work_dir / f"channel_sync_{stem}.json").write_text(
            json.dumps(rep, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return rep

    def finish(
        status: str, detail: str, **kw: Any
    ) -> dict[str, Any]:
        """终态写入: 透明模式文件级失败时输出 = 源文件字节级拷贝。"""
        if t_mode and dst is not None and status != "applied":
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dst)
                kw["output_file"] = dst
                kw["file_copied"] = True
                log(f"channel-sync: transparent — output = 源文件原样拷贝")
            except OSError as exc:
                log(f"channel-sync: transparent copy failed: {exc}")
                status = "verify_failed" if status not in (
                    "not_eligible", "tool_missing"
                ) else status
                detail = f"transparent copy failed: {exc}"
        return report(status, detail, **kw)

    # 参与集合判定 (2026-10-29 用户决策 A/A5): 按**独立 PCM 声道**计数,
    # 3–8 自动; 混合 PCM+压缩 -> 整文件不对齐 (进程不退出)。多声道 PCM 流
    # (stereo/5.1/7.1/quad…) 是 S3/A6 结构支持：拆成独立声道参与估计，
    # 输出时按源流重新交织（同一 stream 的全部声道一起重编码 / 未修时整流
    # stream copy），声道结构、顺序、采样数与源保持一致。
    scope, scope_reason = alignment_scope(streams, eff)
    if scope is None:
        status = (
            "mixed_audio_skip"
            if scope_reason.startswith("mixed_pcm_compressed_not_aligned")
            else "not_eligible"
        )
        log(f"channel-sync: {status} — {scope_reason}")
        return finish(status, scope_reason)

    try:
        import numpy as np
        from . import sync_estimate, sync_fix
    except ImportError as exc:
        log(
            f"channel-sync: numpy/scipy missing ({exc}) — skipped, "
            "audio untouched"
        )
        return finish("tool_missing", f"numpy/scipy missing: {exc}")

    audio = [s for s in streams if s.get("codec_type") == "audio"]
    sample_rate = int(audio[0].get("sample_rate", 0) or 0)
    n_streams = len(audio)
    # 引擎的每个 "track" = **一个独立声道**（ChannelRef）。单声道多流素材
    # 上 channel index 与 stream index 一一对应，行为与旧实现逐位相同；
    # 多声道流则展开成多个 channel，最后按 stream 汇聚输出。
    n_tracks = len(scope)
    codecs = [str(c.codec) for c in scope]
    # 源轨 sample entry (ipcm / in24 / sowt / ...), 决定中间文件 muxer
    entries = [str(c.entry or "") for c in scope]
    storage = [str(c.storage) for c in scope]
    stream_of = [int(c.stream) for c in scope]
    channel_of = [int(c.channel) for c in scope]
    stream_channels = [int(c.stream_channels) for c in scope]
    layout_of = [str(c.layout or "") for c in scope]
    multichannel = any(n > 1 for n in stream_channels)
    min_samples = max(1, int(round(eff["min_audio_seconds"] * sample_rate)))

    def j(x: float, nd: int) -> float | None:
        return None if x != x else round(x, nd)   # NaN 判定沿用 x != x 惯例

    def _source_fields(row: dict[str, Any], i: int) -> dict[str, Any]:
        """声道 -> 源结构定位（A6 报告字段；单声道流时与旧字段一致）。"""
        row["source_stream"] = stream_of[i]
        row["source_channel"] = channel_of[i]
        if multichannel:
            row["source_stream_channels"] = stream_channels[i]
            row["source_layout"] = layout_of[i]
        return row

    def plain_row(i: int, decision: str, reason_: str | None) -> dict[str, Any]:
        row = {
            "stream": i,
            "delay_ms": None,
            "delay_samples": None,
            "confidence": 0.0,
            "polarity": 1,
            "drift_ppm": 0.0,
            "constant": False,
            "warnings": [],
            "decision": decision,
            "reason": reason_,
            "fine_delay_ms": None,
            "shift_samples": None,
            "fractional_part_samples": None,
            "storage_dtype": storage[i],
            "traj_mad_ms": None,
            "traj_spread_samples": None,
            "traj_drift_ms": None,
            "usable_frames": None,
            "rms_dbfs": None,
        }
        if codecs[i] in ("pcm_s32le", "pcm_s32be"):
            row["precision_note"] = "s32 source processed via f64 pipeline"
        elif stream_channels[i] > 1:
            row["precision_note"] = (
                "multichannel stream decoded per channel via AudioPCMReader "
                "(f32 canonical); no downmix"
            )
        return _source_fields(row, i)

    def est_row(i: int, e: Any, stats: Any, decision: str,
                reason_: str | None, shift: int | None = None) -> dict[str, Any]:
        warnings = []
        if e.polarity < 0:
            warnings.append(
                "polarity inversion detected (negative correlation peak)"
            )
        if stats.step_max_ms == stats.step_max_ms \
                and stats.step_max_ms > eff["step_min_ms"]:
            warnings.append(
                f"trajectory adjacent-step {stats.step_max_ms:.2f}ms > "
                f"{eff['step_min_ms']}ms (diagnostic only, 分类属二期)"
            )
        if e.fine_delay_samples != e.fine_delay_samples:
            warnings.append("phase slope estimate degenerated (diagnostic)")
        row = {
            "stream": i,
            "delay_ms": j(e.delay_ms, 4),
            "delay_samples": j(e.delay_samples, 3),
            "confidence": round(e.confidence, 3),
            "polarity": e.polarity,
            "drift_ppm": round(stats.drift_ppm, 2),
            # 与决策门一致: MAD + 稳健实测位移 + (斜率须被实测位移证实)
            "measured_displacement_ms": j(
                sync_estimate.measured_displacement_ms(e.frames, sample_rate), 4),
            "constant": bool(
                sync_estimate.classify_constant(
                    stats, mad_max_ms=eff["mad_max_ms"],
                    max_ppm=eff["constant_max_ppm"],
                    sample_rate=sample_rate,
                    drift_min_ms=eff["drift_min_ms"],
                    displacement_ms=sync_estimate.measured_displacement_ms(
                        e.frames, sample_rate),
                )
            ),
            "warnings": warnings,
            "decision": decision,
            "reason": reason_,
            "fine_delay_ms": j(e.fine_delay_ms, 4),
            "shift_samples": shift,
            "fractional_part_samples": (
                j(e.delay_samples - int(shift), 3)
                if shift is not None and e.delay_samples == e.delay_samples
                else None
            ),
            "storage_dtype": storage[i],
            "traj_mad_ms": j(stats.mad_ms, 4),
            "traj_spread_samples": j(stats.spread_samples, 3),
            "traj_drift_ms": j(getattr(stats, "drift_total_ms", float("nan")), 4),
            "usable_frames": e.usable_frames,
        }
        if codecs[i] in ("pcm_s32le", "pcm_s32be"):
            row["precision_note"] = "s32 source processed via f64 pipeline"
        elif stream_channels[i] > 1:
            row["precision_note"] = (
                "multichannel stream decoded per channel via AudioPCMReader "
                "(f32 canonical); no downmix"
            )
        return _source_fields(row, i)

    decoded = [work_dir / f"decoded_{i}.{storage[i]}" for i in range(n_tracks)]
    fixed_raw = [work_dir / f"fixed_{i}.{storage[i]}" for i in range(n_tracks)]
    intermediates = decoded + fixed_raw
    arrs: list[Any] = []

    try:
        # 1. 逐**声道**解码 raw (§6.2 存储精度表 + A6 结构支持)
        #
        # 长片段并行 (P-1): 各声道解码互不依赖, 顺序解码是纯粹的浪费。
        # 80 min / 4 轨实测: 顺序 101.6 s vs 4 路并行 32.5 s (3.13x)。
        # 这是**音频内部**并行, 与 `--jobs` 无关 —— `--jobs` 只并行独立
        # 文件, 不干预媒体内部的并发度。
        # 失败语义保持不变: 仍按声道序号顺序报告**第一个**失败的。
        dec_err: dict[int, str] = {}
        if not multichannel:
            # 单声道多流快路径（与旧实现逐位相同）: stream index == channel
            # index，`_decode_stream` 的 `-ac 1` 不是下混而是原样(1ch)。
            if n_tracks <= 1:
                for i in range(n_tracks):
                    ok, err = _decode_stream(
                        ffmpeg, source, i, sample_rate, decoded[i], storage[i]
                    )
                    if not ok:
                        dec_err[i] = err
            else:
                with ThreadPoolExecutor(max_workers=n_tracks) as ex:
                    futs = {
                        ex.submit(_decode_stream, ffmpeg, source, i,
                                  sample_rate, decoded[i], storage[i]): i
                        for i in range(n_tracks)
                    }
                    for fut, i in list(futs.items()):
                        ok, err = fut.result()
                        if not ok:
                            dec_err[i] = err
        else:
            mono_idx = [i for i in range(n_tracks) if stream_channels[i] == 1]
            multi_audio_idx = sorted(
                {stream_of[i] for i in range(n_tracks)
                 if stream_channels[i] > 1}
            )
            if mono_idx:
                with ThreadPoolExecutor(
                        max_workers=min(len(mono_idx), 8)) as ex:
                    futs = {
                        ex.submit(_decode_stream, ffmpeg, source,
                                  stream_of[i], sample_rate,
                                  decoded[i], storage[i]): i
                        for i in mono_idx
                    }
                    for fut, i in list(futs.items()):
                        ok, err = fut.result()
                        if not ok:
                            dec_err[i] = err
            dec_err.update(_decode_multichannel_streams(
                ffmpeg, source, audio, multi_audio_idx, scope, work_dir))
        if dec_err:
            i = min(dec_err)            # 与顺序实现一致: 报最先失败的
            log(f"channel-sync: WARNING channel {i} decode failed")
            return finish(
                "measure_failed",
                f"audio channel {i} decode failed: {dec_err[i][-200:]}",
            )
        arrs = [
            sync_estimate.open_source(decoded[i], storage[i])
            for i in range(n_tracks)
        ]

        # 2. 逐轨健康检查 (轨道级状态; 不健康轨 -> untouched, 不阻止其它轨)
        health: list[tuple[bool, str | None, float]] = []
        rms_of: dict[int, float | None] = {}
        rows: list[dict] = [plain_row(i, "untouched", None)
                            for i in range(n_tracks)]
        #: 材料性判据放行的轨: {轨下标: 说明}。恒定性失败的轨如果满足
        #: "偏差 >= k × 实测位移", 就按可修处理, 这里记下证据以便报告与复核。
        materiality_note: dict[int, dict[str, Any]] = {}
        for i, a in enumerate(arrs):
            if a.shape[0] < min_samples:
                health.append((False, "insufficient_frames", float("nan")))
                rows[i]["reason"] = "insufficient_frames"
                rms_of[i] = None
                continue
            finite, rms_db = _track_health(a, np)
            rms_of[i] = rms_db if rms_db == rms_db else None
            rows[i]["rms_dbfs"] = (
                round(rms_db, 1) if rms_db == rms_db else None
            )
            if not finite:
                health.append((False, "non_finite", rms_db))
                rows[i]["reason"] = "non_finite"
            elif rms_db < eff["silent_rms_dbfs"]:
                health.append((False, "silent_track", rms_db))
                rows[i]["reason"] = "silent_track"
            else:
                health.append((True, None, rms_db))
        for i, (_ok, reason_, _r) in enumerate(health):
            if not _ok:
                log(
                    f"channel-sync: CH{i + 1} {reason_} — 轨道级 untouched"
                    f"{' (rms ' + format(_r, '.1f') + ' dBFS)' if _r == _r else ''}"
                )
        healthy = [i for i in range(n_tracks) if health[i][0]]

        #: stereo 绑定组（成员为声道下标；S3 绑定检测填写）。常量路径与长镜头
        #: 时变路径共用同一份分组，保证"整组同偏移"在两个后备路径上语义一致。
        bound_groups: list[list[int]] = []
        group_of_member: dict[int, list[int]] = {}

        # 2026-10-25 用户策略: 音频流 >= 3 但只有 2 条健康 → 默认整文件跳过
        # 对齐 (two_channel_only)。放在锚点选择**之前**: 一旦进入估计/修正,
        # 透明模式就会产生整文件重封装, 而这两条有效轨本来就是一对, 没有
        # 需要纠正的相对关系。
        # A6 说明: 该策略按"音频流"计数，只对单声道多流布局有意义；多声道
        # 流（一个 stream 多个声道）不套用（否则 5.1 流的 2 条健康声道会被
        # 误判成 two_channel_only）。
        if (not multichannel
                and skip_two_channel_only(n_streams, len(healthy),
                                          eff["skip_two_channel_only"])):
            for i in range(n_tracks):
                rows[i]["decision"] = "untouched"
                if not rows[i].get("reason"):
                    rows[i]["reason"] = "two_channel_only"
            log(
                f"channel-sync: {n_streams} 条音频流中仅 {len(healthy)} 条健康"
                " — two_channel_only, 默认整文件跳过对齐")
            return report(
                "two_channel_only",
                f"{n_streams} 条音频流中仅 {len(healthy)} 条有效 (其余静音/"
                f"不可测); 按默认策略整文件跳过对齐 "
                f"(可用 skip_two_channel_only=false 覆盖)",
                channels=rows,
            )

        # 2b. stereo 组绑定检测 (2026-10-29 用户决策 C)
        #
        # 相关性极高、整片 lag 刚性的**双轨**默认绑定为一组: 整组用同一个
        # 偏移/曲线, 组内相对偏移保持源样。检测机制全部复用既有实现
        # (sample_pair_snr -> group_by_snr -> 一次连续性跟踪确认), 不新造聚类。
        bound_groups: list[list[int]] = []
        stereo_report: dict[str, Any] = {"enabled": False}
        if eff["stereo_bind_enabled"]:
            bound_groups, stereo_report = _detect_stereo_groups(
                streams=arrs,
                decoded=decoded,
                storages=storage,
                healthy=healthy,
                sample_rate=sample_rate,
                eff=eff,
                log=log,
            )
            if stereo_report.get("candidates") or stereo_report.get("groups"):
                ctx["stereo_groups"] = stereo_report

        # 3. 锚点候选回退: [CH1, CH2, ..., CH8] (用户决策 A4; 旧默认由 config
        #    覆盖时仍生效), 仅健康轨可当选
        order = [c for c in eff["anchor_candidates"]
                 if 0 <= c < n_tracks and health[c][0]]
        anchor: int | None = None
        ests: dict[int, Any] = {}
        tried: list[int] = []
        est_kwargs = dict(
            sample_rate=sample_rate,
            search_window_ms=eff["search_window_ms"],
            frame_ms=eff["frame_ms"],
            hop_ms=eff["hop_ms"],
            anchor_segment_seconds=eff["anchor_segment_seconds"],
            min_confidence=eff["min_confidence"],
            coarse_rate=eff["coarse_rate"],
            fine_phase_band_hz=tuple(eff["fine_phase_band_hz"]),
            min_usable_frames=eff["min_usable_frames"],
            frame_min_rms_dbfs=eff["frame_min_rms_dbfs"],
        )
        # 估计阶段并行 (P-2): 每个 (候选锚点, 目标轨) 对是**完全独立**的测量,
        # 顺序执行没有语义理由。80 min 实测每对约 35 s, 3 对顺序 ~105 s。
        #
        # 为什么传**路径**而不是 `arrs[i]`: RawStream 内部有可变块缓存
        # (`_blocks`), 多个线程共读同一个对象会竞争。传路径时
        # `estimate_pair` 自己 open/close 独立句柄 (见 sync_estimate 的
        # `opened` 清理), 因此并发是安全的 —— 这是本处唯一需要小心的地方。
        # 结果与顺序实现逐位一致: 每对独立, 不共享任何可变状态。
        def _est_pair(ref_idx: int, tgt_idx: int, wide: bool = False) -> Any:
            # 帧级并行 (P-2): 估计阶段是 Python 逐帧循环 (单帧 ~259 us, 其中
            # 约 60% 是解释器/scipy 开销), **线程会被 GIL 卡住** —— 实测
            # 线程版只有 1.1x。所以把帧区间切给**进程池**, 由
            # `estimate_pair(workers=...)` 内部完成; 结果与顺序实现逐位相同
            # (帧之间无依赖, 分块只改变算在哪)。
            # 这里不再需要 pair 级线程: 进程池已经把 CPU 打满。
            return sync_estimate.estimate_pair(
                decoded[ref_idx], decoded[tgt_idx],
                storage_dtype=storage[tgt_idx],
                ref_index=ref_idx, tgt_index=tgt_idx,
                workers=_est_workers,
                **dict(
                    est_kwargs,
                    search_window_ms=(
                        max(eff["wide_search_ms"],
                            3.0 * eff["search_window_ms"])
                        if wide else eff["search_window_ms"]
                    ),
                ),
            )

        def _est_many(ref_idx: int, targets: list[int], wide: bool = False
                      ) -> dict[int, Any]:
            return {i: _est_pair(ref_idx, i, wide) for i in targets}

        # 估计阶段的帧级并行度。只在**长轨迹**上启用: 进程池在 Windows 上
        # 有 ~1 s 的启动成本, 短片用它是净亏。2000 帧 ≈ 200 s 素材, 之下保持
        # 单进程; 之上按 CPU 分块 (留一个核给主进程/IO)。
        # 与 `--jobs` 无关: `--jobs` 只并行独立文件, 媒体内部并发度由这里定。
        _n_frames_est = max(
            0, (min(arrs[i].shape[0] for i in healthy)
                - int(round(eff["frame_ms"] * sample_rate / 1000.0)))
            // max(1, int(round(eff["hop_ms"] * sample_rate / 1000.0))) + 1
        ) if healthy else 0
        _est_workers = 1
        if _n_frames_est >= 2000:
            _est_workers = max(2, min(12, (os.cpu_count() or 4) - 1))
            log(f"channel-sync: frame-parallel estimation "
                f"({_n_frames_est} frames -> {_est_workers} processes)")

        for cand in order:
            targets = [i for i in healthy if i != cand]
            cand_ests = _est_many(cand, targets) if targets else {}
            correlated = any(
                e.usable_frames >= eff["min_usable_frames"]
                and e.confidence >= eff["min_confidence"]
                for e in cand_ests.values()
            )
            if not correlated:
                tried.append(cand)
                continue               # anchor_uncorrelated -> 下一候选
            anchor = cand
            ests = cand_ests
            break
        if anchor is None:
            detail = (
                "no_valid_anchor: 候选 "
                + ",".join(f"CH{c + 1}" for c in tried)
                + " 与全部健康轨均不相关"
            )
            log(f"channel-sync: WARNING {detail}")
            for i in healthy:
                rows[i]["reason"] = "no_valid_anchor"
            return finish("measure_failed", detail, channels=rows)
        ctx["anchor"] = anchor

        # 4. 轨道级质量门 + 逐轨决策 (恒定 = MAD + 极差 + 漂移 ppm 三门;
        #    窄窗不可靠/边缘堆积时宽窗复测以区分 out_of_range)
        search_samples = eff["search_window_ms"] * sample_rate / 1000.0
        wide_window_ms = max(
            eff["wide_search_ms"], 3.0 * eff["search_window_ms"]
        )

        def wide_estimate(i: int, ref_idx: int | None = None) -> Any:
            """宽窗复测。`ref_idx` 缺省 = 主锚点 (与开关关闭时完全一致)。"""
            return _est_pair(anchor if ref_idx is None else ref_idx, i,
                             wide=True)

        anchor_row = plain_row(anchor, "anchor", None)
        anchor_row.update({
            "delay_ms": 0.0, "delay_samples": 0.0, "confidence": 1.0,
            "constant": True, "fine_delay_ms": 0.0,
            "rms_dbfs": (
                round(rms_of[anchor], 1)
                if rms_of.get(anchor) is not None else None
            ),
        })
        rows[anchor] = anchor_row
        to_fix: list[int] = []
        used_est: dict[int, Any] = {}   # 每轨最终采用的估计 (窄窗或宽窗)
        #: 长镜头后备触发证据 (用户决策 B): **任一条健康 target 实测不恒定**
        #: (含被材料性判据放行的) 或整数修正后复检不通过 -> 整文件转时变。
        #: 与 `rows[i]["reason"]` 分开记录: 材料性放行的轨最终 decision=fixed、
        #: reason=None, 但"实测不恒定"这个事实必须仍然能触发时变后备。
        non_constant_flags: set[int] = set()
        recheck_failed_flags: set[int] = set()

        def evidence_of_est(e: Any) -> dict[str, Any]:
            """一次估计的**完整证据**: 帧数 / 置信度 / 覆盖率。

            P4.2: 采纳门与最终结果都必须读这一份证据, 不允许"门看 A, 采纳 B"。
            """
            total = len(e.frames)
            cov = (e.usable_frames / total) if total else 0.0
            return {
                "usable_frames": int(e.usable_frames),
                "total_frames": int(total),
                "coverage": cov,
                "confidence": float(e.confidence),
            }

        def evidence_ok(ev: dict[str, Any]) -> tuple[bool, str | None]:
            """最终采纳必须满足的**唯一**证据门 (帧数 + 置信度 + 覆盖率)。

            返回 (是否通过, 未通过的原因)。任何一种估计——窄窗或宽窗——只要被
            采纳, 就必须过这一道门。
            """
            if ev["usable_frames"] < eff["min_usable_frames"]:
                return False, "low_confidence"
            if not (ev["confidence"] >= eff["min_confidence"]):
                return False, "low_confidence"
            if ev["coverage"] < eff["min_usable_fraction"]:
                # 有数值但轨迹证据不足 (存在大洞) -> 证据不足, 不是测量失败
                return False, "evidence_insufficient"
            return True, None

        def try_wide(
            i: int, ev: dict[str, Any], *, why: str,
            ref_idx: int | None = None,
        ) -> tuple[Any, dict[str, Any], str | None]:
            """宽窗复测并**按同一道门**判定可否采纳。

            P4.2 修复的正是这里: 旧实现只检查 `usable_frames`/`confidence`,
            不重查 coverage —— 于是一条 coverage 不足的窄窗可以被一条**同样
            coverage 不足**的宽窗"救活", 最终仍写成 fixed/SUCCESS。

            返回 (估计, 证据, 未通过原因或 None)。未通过时估计仍返回, 供
            "有数值但证据不足" 的语义使用。
            """
            ew = wide_estimate(i, ref_idx)
            ev_w = evidence_of_est(ew)
            if abs(ew.delay_samples) > search_samples:
                return ew, ev_w, "out_of_range"
            ok, why_not = evidence_ok(ev_w)
            if not ok:
                log(
                    f"channel-sync: CH{i + 1} 宽窗复测仍未通过证据门 "
                    f"({why_not}: usable {ev_w['usable_frames']}/"
                    f"{ev_w['total_frames']} = {ev_w['coverage']:.0%}, "
                    f"conf {ev_w['confidence']:.2f}) — 按未通过处理 "
                    f"(窄窗原因 {why})"
                )
                return ew, ev_w, why_not
            return ew, ev_w, None

        def decide_and_record(
            i: int, e: Any, ref_idx: int | None = None,
        ) -> tuple[str, Any, dict[str, Any]]:
            """对一条轨做**完整决策**, 把结果写进 rows[i], 返回 (决策, 采用的估计, 证据)。

            为什么要抽成函数: 分组层 (4b) 需要对**同一条轨、换一个参考**再判
            一次, 而那必须真的是同一道门 —— 复制一份门逻辑出来, 两边的阈值
            迟早会飘。抽出来之后主路径和分组路径共用同一份代码, 因此
            `untouched` 的原因在两条路径上可比。

            `ref_idx` 只影响宽窗复测用哪个参考; 缺省 = 主锚点。
            """
            stats = sync_estimate.summarize_trajectory(e.frames, sample_rate)
            reason_: str | None = None
            ev = evidence_of_est(e)
            coverage = ev["coverage"]
            if e.usable_frames < eff["min_usable_frames"]:
                # 窄窗无可测帧: 宽窗复测 — 真实时差超窗 vs 确实不可测
                ew, ev_w, why_not = try_wide(
                    i, ev, why="usable_frames", ref_idx=ref_idx)
                if why_not == "out_of_range":
                    reason_ = "out_of_range"
                    log(
                        f"channel-sync: CH{i + 1} 宽窗复测 delay "
                        f"{ew.delay_ms:+.3f}ms 超出搜索窗 "
                        f"±{eff['search_window_ms']}ms — out_of_range"
                    )
                elif why_not is None:
                    e, ev = ew, ev_w
                    stats = sync_estimate.summarize_trajectory(
                        ew.frames, sample_rate
                    )
                    used_est[i] = ew
                    log(
                        f"channel-sync: CH{i + 1} 窄窗无可测帧, "
                        f"宽窗窗内测出 {ew.delay_ms:+.3f}ms "
                        f"(证据 {ev['usable_frames']}/{ev['total_frames']} = "
                        f"{ev['coverage']:.0%}) — 采用宽窗估计"
                    )
                else:
                    # 窄窗宽窗均未过门: 用"有信号的帧数"区分原因 —
                    # 有信号但相关性不足 -> low_confidence;
                    # 有信号的帧本身就太少 (稀疏内容/极短素材) -> insufficient_frames
                    attempted = sum(
                        1 for f in e.frames if f.confidence > 0.0
                    )
                    if attempted >= eff["min_usable_frames"]:
                        reason_ = "low_confidence"
                        log(
                            f"channel-sync: CH{i + 1} 有信号帧 {attempted} 个但"
                            f"均可测性不足 (相关峰值未过门) — low_confidence"
                        )
                    else:
                        reason_ = "insufficient_frames"
            elif coverage < eff["min_usable_fraction"]:
                # 轨迹证据覆盖率不足 (存在大洞): 恒定性判断不可靠 -> 宽窗复测。
                # P4.2: 宽窗结果**必须重过同一道证据门**才可采纳。
                #
                # 2026-10-16: `min_usable_fraction` 已标定为 0.0, 所以本分支在
                # 当前生产配置下**不可达** (coverage 恒 >= 0.0)。保留它的理由:
                # (a) 门若被调回正值立即恢复作用; (b) 语义与上面的
                # `usable_frames` 分支不同。
                # 为什么覆盖率不再是必需的代理: 覆盖率原本替"轨迹有洞 -> 恒定性
                # 不可靠"把关, 而恒定性现在由 `classify_constant` 在**实测位移**
                # 上直接判定 (core/sync_estimate.py)。`out_of_range` 检测仍然
                # 生效 —— 由上面 `usable_frames < min_usable_frames` 分支承担。
                ew, ev_w, why_not = try_wide(
                    i, ev, why="evidence_insufficient", ref_idx=ref_idx)
                if why_not == "out_of_range":
                    reason_ = "out_of_range"
                    log(
                        f"channel-sync: CH{i + 1} 宽窗复测 delay "
                        f"{ew.delay_ms:+.3f}ms 超出搜索窗 "
                        f"±{eff['search_window_ms']}ms — out_of_range"
                    )
                elif why_not is None:
                    e, ev = ew, ev_w
                    stats = sync_estimate.summarize_trajectory(
                        ew.frames, sample_rate
                    )
                    used_est[i] = ew
                    log(
                        f"channel-sync: CH{i + 1} 窄窗证据覆盖率 "
                        f"{coverage:.0%} 不足, 宽窗复测通过证据门 "
                        f"({ev['usable_frames']}/{ev['total_frames']} = "
                        f"{ev['coverage']:.0%}) — 采用宽窗估计 "
                        f"({ew.delay_ms:+.3f}ms)"
                    )
                else:
                    reason_ = why_not
                    log(
                        f"channel-sync: CH{i + 1} 证据不足: 窄窗覆盖率 "
                        f"{coverage:.0%} < {eff['min_usable_fraction']:.0%}, "
                        f"宽窗也未过门 — {reason_} (可用帧 "
                        f"{ev['usable_frames']}/{ev['total_frames']})"
                    )
            elif sync_estimate.boundary_pileup(e.frames, search_samples) > 0.5:
                # 边缘堆积: 宽窗确认真实时差是否在窗内
                ew = wide_estimate(i, ref_idx)
                if (
                    ew.usable_frames >= eff["min_usable_frames"]
                    and ew.confidence >= eff["min_confidence"]
                    and abs(ew.delay_samples) > search_samples + 0.5
                ):
                    reason_ = "out_of_range"
                    log(
                        f"channel-sync: CH{i + 1} 宽窗复测 delay "
                        f"{ew.delay_ms:+.3f}ms 超出搜索窗 "
                        f"±{eff['search_window_ms']}ms — out_of_range"
                    )
                # 宽窗也在窗内 -> 窄窗边缘值可用, 按常规门继续
            if reason_ is None:
                if e.confidence < eff["min_confidence"]:
                    reason_ = "low_confidence"
                else:
                    disp_ms = sync_estimate.measured_displacement_ms(
                        e.frames, sample_rate)
                    if not sync_estimate.classify_constant(
                        stats, mad_max_ms=eff["mad_max_ms"],
                        max_ppm=eff["constant_max_ppm"],
                        sample_rate=sample_rate,
                        drift_min_ms=eff["drift_min_ms"],
                        displacement_ms=disp_ms,
                    ):
                        # B: 实测不恒定是**文件级**事实, 先记下 (即使材料性
                        # 判据稍后放行该轨, 整文件仍应转时变)。
                        non_constant_flags.add(i)
                        # 恒定性不通过。**材料性判据**可以替它放行:
                        # 如果修正能去掉的偏差 >= k × 修正后仍然存在的变化,
                        # 那么"不恒定"就不是拒绝的理由 —— 拒绝等于为了
                        # 不引入 0.78 ms 而留下 24 ms。
                        mat = materiality_accepts(
                            e.delay_samples, disp_ms, sample_rate,
                            k=eff["materiality_k"])
                        if mat is None:
                            reason_ = "non_constant"
                        else:
                            materiality_note[i] = mat
                            log(
                                f"channel-sync: CH{i + 1} 非恒定但满足材料性: "
                                f"偏差 {mat['delay_ms']:.2f}ms >= "
                                f"{mat['k']:g} × 实测位移 "
                                f"{mat['displacement_ms']:.3f}ms "
                                f"(比值 {mat['ratio']:.1f}) — 按可修处理"
                            )
                if reason_ is None and e.delay_samples != e.delay_samples:
                    reason_ = "low_confidence"
                elif reason_ is None and abs(e.delay_samples) > search_samples:
                    reason_ = "out_of_range"
                elif reason_ is None:
                    # P4.2 最终采纳门: 被采纳的估计必须过证据门。旧实现在这里
                    # 只看置信度/恒定性/范围, 覆盖率**不再检查**, 因此一条
                    # coverage 不足的宽窗可以被写成 fixed/SUCCESS。
                    ev = evidence_of_est(e)
                    ok, why_not = evidence_ok(ev)
                    if not ok:
                        reason_ = why_not
                        log(
                            f"channel-sync: CH{i + 1} 最终采纳门未通过 "
                            f"({why_not}: 可用帧 {ev['usable_frames']}/"
                            f"{ev['total_frames']} = {ev['coverage']:.0%} < "
                            f"{eff['min_usable_fraction']:.0%}) — 该轨 "
                            f"untouched"
                        )
            if reason_ is not None:
                rows[i] = est_row(i, e, stats, "untouched", reason_)
                rows[i]["evidence"] = ev
                rows[i]["rms_dbfs"] = (
                    round(rms_of[i], 1) if rms_of.get(i) is not None else None
                )
                log(
                    f"channel-sync: CH{i + 1} {reason_} (conf "
                    f"{e.confidence:.2f}, usable {e.usable_frames}, "
                    f"mad {stats.mad_ms:.3f}ms, "
                    f"ppm {stats.drift_ppm:.2f}) — 该轨 untouched"
                )
                return "untouched", e, ev
            # 量化粒度保护 (幂等性): 修正只做**整数样本**移位, 因此
            # |delay| < 1 个样本时移位量化后为 0 —— 此时不得产生任何写入。
            #
            # 最小幅度 (v0.8.7, 产品决定): |shift| <= `min_shift_samples`
            # (默认 5 = 0.104 ms @48 kHz) 的轨**不做写入**, 判 already_aligned。
            # 理由不是"阈值调紧", 而是这个量级已经在测量不确定度里:
            #   * 同一套估计器重复测量就有 1~2 样本的散布;
            #   * 真实缺陷的位移是 +905~+1222 / −37~−39 样本, 差两个数量级;
            #   * 把 3 样本的修正写进成品, 得到的是"看起来做过处理"的文件,
            #     而没有可复现的对齐收益。
            # 用 rint 后的**整数样本**判定, 与下游真正写入的 shift 同一个量。
            #
            # 精确模式: 把门降到 1 样本, 并且**放弃**毫米带。毫米带在 48 kHz
            # 覆盖 ±2.4 样本, 留着它会让 tier 1 与 tier 2 在实际素材上无法
            # 区分 —— 那等于开了开关却没有更细。可行性由施加后的复检残差
            # 决定 (`resolve_accurate_tier`), 决策阶段只负责提出候选。
            if eff["channel_sync_accurate"]:
                gate_ms, gate_samples = 0.0, min(ACCURATE_TIERS)
            else:
                gate_ms, gate_samples = (eff["aligned_max_ms"],
                                         eff["min_shift_samples"])
            if is_aligned_decision(
                e.delay_ms, e.delay_samples,
                aligned_max_ms=gate_ms,
                min_shift_samples=gate_samples,
            ):
                rows[i] = est_row(i, e, stats, "already_aligned", None)
                decision = "already_aligned"
            else:
                rows[i] = est_row(i, e, stats, "fixed", None)
                decision = "fixed"
            rows[i]["evidence"] = ev
            rows[i]["rms_dbfs"] = (
                round(rms_of[i], 1) if rms_of.get(i) is not None else None
            )
            # 材料性判据放行的轨: 把证据挂到行上。报告与复核必须能看出
            # "这条轨其实不恒定, 是按材料性放行的", 不能只留一个 fixed。
            if i in materiality_note:
                rows[i]["materiality"] = materiality_note[i]
            return decision, e, ev

        # 主路径: 相对主锚点逐轨决策。这一段与"分组锚点"引入前逐字节相同。
        for i in healthy:
            if i == anchor:
                continue
            decision, e_used, _ev = decide_and_record(i, ests[i])
            # `used_est` 只在**宽窗被采纳**时登记 —— 与旧实现一致, 因为尾部
            # 汇总日志的读音就是"这条轨最终采纳了哪一条估计"。
            if e_used is not ests[i]:
                used_est[i] = e_used
            if decision == "fixed":
                to_fix.append(i)

        # 4c. stereo 组刚性绑定: 整组共用**组代表相对主锚点**的偏移 (决策 C)。
        #
        # 组内相对偏移是源内容的一部分, 因此这里只改"整组该移动多少", 不逐条
        # 重测成员; 成员自己的测量值保留在报告里 (用于核对刚性假设)。
        # 与锚点同组的成员跟随锚点 (锚点 shift=0) -> already_aligned。
        #
        # `ref_of`（轨 -> 复检参考轨）必须**在组处理之前初始化**：绑定组取
        # "整组代表偏移" 时就要写它（`ref_of[m] = anchor`），而 4a/4b 阶段
        # 共用同一份。此前它在 4a 前才定义，导致"绑定组 + 组代表被 fixed"
        # 的组合直接 UnboundLocalError（全量 L3 hardware suite 实测到）。
        ref_of: dict[int, int] = {}
        group_shift_override: dict[int, int] = {}
        if bound_groups:
            for g in bound_groups:
                members = [int(m) for m in g]
                rep = group_anchor_of(members, eff["anchor_candidates"])
                note = {"members": [m + 1 for m in members],
                        "rep": rep + 1}
                if rep == anchor:
                    for m in members:
                        if m == anchor:
                            continue
                        row = plain_row(m, "already_aligned", None)
                        row["stereo_group"] = dict(
                            note, bound_to_anchor=True)
                        rows[m] = row
                        if m in to_fix:
                            to_fix.remove(m)
                    log(
                        "channel-sync: stereo group ["
                        + ",".join(f"CH{m + 1}" for m in members)
                        + f"] follows anchor CH{anchor + 1} — no relative "
                        "shift (源内相对偏移保持)")
                    continue
                dec_rep = rows[rep].get("decision")
                if dec_rep not in ("fixed", "already_aligned"):
                    log(
                        "channel-sync: stereo group ["
                        + ",".join(f"CH{m + 1}" for m in members)
                        + f"] not applied — rep CH{rep + 1} is "
                        f"{dec_rep} ({rows[rep].get('reason')})"
                    )
                    continue
                rep_shift = int(rows[rep].get("shift_samples") or 0)
                for m in members:
                    if m in to_fix:
                        to_fix.remove(m)
                if dec_rep == "fixed":
                    for m in members:
                        row = dict(rows[m]) if m != rep else dict(rows[rep])
                        row["decision"] = "fixed"
                        row["shift_samples"] = rep_shift
                        row["stereo_group"] = dict(note)
                        rows[m] = row
                        to_fix.append(m)
                        group_shift_override[m] = rep_shift
                        ref_of[m] = anchor
                    log(
                        "channel-sync: stereo group ["
                        + ",".join(f"CH{m + 1}" for m in members)
                        + f"] -> whole-group shift {rep_shift:+d} samples "
                        f"(rep CH{rep + 1})"
                    )
                else:
                    for m in members:
                        row = dict(rows[m]) if m != rep else dict(rows[rep])
                        row.setdefault("decision", "already_aligned")
                        row["decision"] = "already_aligned"
                        row["shift_samples"] = None
                        row["stereo_group"] = dict(note)
                        rows[m] = row

        # 4a. 逐轨参考选择 (opt-in, 默认关闭) —— **只对主锚点下没修成的轨**
        #
        # 4 声道素材常是两支互不相干的麦克风: CH1/CH2 近场一对, CH3/CH4 另一对。
        # 主锚点固定为其中一条时, 另一组的轨可能"有共同内容却测不出"。这里让
        # 这些轨换用**已定位**的轨当参考重测, 把相对延迟合成为绝对移位。
        #
        # 三条自我约束, 使打开开关在结构上不可能造成回归:
        #   (a) 只碰主锚点下 decision == "untouched" 的轨 —— 已 fixed /
        #       already_aligned 的轨原样保留, 绝不重测;
        #   (b) 参考轨必须已经"定位"(锚点 / 已 fixed / 已 aligned), 否则合成
        #       缺一项, 直接跳过;
        #   (c) 写回仍走 `decide_and_record` 的同一份门逻辑, 且 step 6 的复检
        #       以**主锚点**为参照 (ref_of[i] = anchor) —— 合成算术的正确性由
        #       修后残差独立核验, 不靠这道算术自证。
        reference_notes: dict[int, dict[str, Any]] = {}
        #: 轨 -> 复检用的参考轨。**在 4c 组处理之前就已初始化**（见上），
        #: 换参考阶段与 4b 分组层共用这一份；这里不再重复赋值。

        def _absolute_shift_of(r: int) -> int | None:
            """已定位轨相对主锚点的整数移位 (与 step 5 的 rint 同口径)。"""
            if r == anchor:
                return 0
            dec_r = rows[r].get("decision")
            if dec_r == "already_aligned":
                return 0
            if dec_r != "fixed":
                return None
            e_r = used_est.get(r, ests.get(r))
            if e_r is None:
                return None
            v = float(e_r.delay_samples)
            return None if v != v else int(np.rint(v))

        #: 换参考时的几何阶梯: 生产几何优先, 再退到更长的帧。
        #:
        #: 为什么需要它: 弱相关轨在 200 ms 帧下可能只有 1 个可用帧, 而 1 s 帧
        #: 能凑到 6 个 (C1197 CH2 实测)。帧长只影响"证据够不够", 不影响移位
        #: 语义; 采纳后仍由修后残差独立核验。阶梯有限, 不引入无界搜索。
        _alt_geoms: list[tuple[float, float]] = [
            (float(eff["frame_ms"]), float(eff["hop_ms"]))]
        for _cand in ((500.0, 250.0), (1000.0, 500.0)):
            if _cand != _alt_geoms[0]:
                _alt_geoms.append(_cand)

        def _est_pair_geom(ref_idx: int, tgt_idx: int,
                           frame_ms: float, hop_ms: float) -> Any:
            """与 `_est_pair` 相同, 但可指定帧几何 (换参考的几何阶梯用)。"""
            return sync_estimate.estimate_pair(
                decoded[ref_idx], decoded[tgt_idx],
                storage_dtype=storage[tgt_idx],
                ref_index=ref_idx, tgt_index=tgt_idx,
                workers=_est_workers,
                **dict(est_kwargs, frame_ms=frame_ms, hop_ms=hop_ms),
            )

        def try_alternate_reference(i: int, round_no: int) -> bool:
            saved_row = dict(rows[i])
            best: tuple | None = None
            tried = 0
            for r in healthy:
                if r == i or r == anchor:
                    continue
                ref_shift = _absolute_shift_of(r)
                if ref_shift is None:
                    continue
                for frame_ms, hop_ms in _alt_geoms:
                    try:
                        e = _est_pair_geom(r, i, frame_ms, hop_ms)
                    except Exception as exc:    # noqa: BLE001
                        log(f"channel-sync: CH{i + 1} 参考 CH{r + 1} "
                            f"({frame_ms:.0f}ms) 复测失败: "
                            f"{type(exc).__name__}")
                        continue
                    tried += 1
                    ev = evidence_of_est(e)
                    ok, _why = evidence_ok(ev)
                    if not ok:
                        continue
                stats = sync_estimate.summarize_trajectory(e.frames, sample_rate)
                disp = sync_estimate.measured_displacement_ms(
                    e.frames, sample_rate)
                const = bool(sync_estimate.classify_constant(
                    stats, mad_max_ms=eff["mad_max_ms"],
                    max_ppm=eff["constant_max_ppm"],
                    sample_rate=sample_rate,
                    drift_min_ms=eff["drift_min_ms"],
                    displacement_ms=disp))
                composed = float(e.delay_samples) + float(ref_shift)
                if not const:
                    # 非恒定: 用材料性判据比较"合成后能去掉的偏差"与"残余上界"
                    if materiality_accepts(
                        composed, disp, sample_rate,
                        k=eff["materiality_k"],
                    ) is None:
                        continue
                if abs(composed) > search_samples:
                    continue
                score = (1 if const else 0, int(e.usable_frames),
                         float(e.confidence))
                if best is None or score > best[0]:
                    best = (score, r, e, composed, ev, const, float(disp),
                            int(ref_shift), frame_ms, hop_ms)
            if best is None:
                if tried:
                    log(f"channel-sync: CH{i + 1} 换参考无改进 "
                        f"({tried} 个参考×几何组合均未过证据门)")
                return False
            (_score, r, e, composed, _ev, const, disp, ref_shift,
             frame_ms, hop_ms) = best
            e_comp = dataclasses.replace(
                e,
                delay_samples=composed,
                delay_ms=composed * 1000.0 / float(sample_rate),
            )
            dec, e_used, _ev2 = decide_and_record(i, e_comp, anchor)
            if dec not in ("fixed", "already_aligned"):
                rows[i] = saved_row        # 保留主锚点下更具体的原因
                return False
            ests[i] = e_comp
            if e_used is not e_comp:
                used_est[i] = e_used
            ref_of[i] = anchor             # 复检以主锚点为参照
            note = compose_reference_shift(e.delay_samples, ref_shift,
                                           sample_rate) or {}
            note.update({
                "reference": r + 1,
                "usable_frames": int(e.usable_frames),
                "constant": const,
                "displacement_ms": round(disp, 3),
                "frame_ms": float(frame_ms),
                "hop_ms": float(hop_ms),
                "round": round_no + 1,
            })
            reference_notes[i] = note
            rows[i]["reference_choice"] = note
            if dec == "fixed":
                to_fix.append(i)
            log(
                f"channel-sync: CH{i + 1} 换参考 CH{r + 1} 重测 -> {dec} "
                f"(相对 {note.get('delay_vs_reference_ms', float('nan')):+.3f}ms"
                f", 参考移位 {ref_shift:+d}, 合成 "
                f"{note.get('composed_ms', float('nan')):+.3f}ms, "
                f"{e.usable_frames} 帧, 恒定={const})"
            )
            return True

        if eff["track_reference"]:
            if eff["group_anchor_enabled"]:
                log("channel-sync: track_reference 与 group_anchor_enabled "
                    "同时打开; 逐轨参考先做, 分组层随后只处理仍然 untouched "
                    "的轨")
            for round_no in range(2):
                progressed = False
                for i in healthy:
                    if i == anchor:
                        continue
                    if rows[i].get("decision") != "untouched":
                        continue
                    if try_alternate_reference(i, round_no):
                        progressed = True
                if not progressed:
                    break
            if reference_notes:
                ctx["track_reference"] = {
                    "enabled": True,
                    "anchor": anchor,
                    "channels": {str(k + 1): v for k, v in
                                 reference_notes.items()},
                }

        # 4b. 分组锚点 (opt-in, 默认关闭) —— **只做加法**
        #
        # 单锚点模型只管锚点所在的那一组。真实 4 声道素材里 CH1/CH2 与
        # CH3/CH4 常常是两支互不相干的麦克风, 另一组从来没有被修的机会,
        # 尽管它内部完全可对齐。
        #
        # 三条自我约束, 让"打开开关"在结构上不可能造成回归:
        #   (a) 只看**主锚点分量之外**的组 (主路径的结果原样保留);
        #   (b) 只看**成员全部仍是 untouched** 的组 —— 任何一条轨已被主路径
        #       处置 (fixed / already_aligned) 的组整组跳过, 绝不把两个坐标系
        #       混在一起, 也绝不重测已经定下来的轨;
        #   (c) 组内重判用的仍是 `decide_and_record` 同一份门逻辑与同一个
        #       `estimate_pair`, 只有**参考**换成了组锚点。
        # 因此开关打开只可能"多修几条轨", 不可能少修、也不可能改值。
        groups: list[list[int]] = [sorted(healthy)]
        group_snr: dict[str, dict[str, float]] = {}
        # ref_of 已在 4a 之前初始化 (换参考阶段需要), 这里不重置
        group_notes: dict[int, dict[str, Any]] = {}   # 分组层对该行的补充说明
        if eff["group_anchor_enabled"] and len(healthy) >= 2:
            snr_pairs: dict[tuple[int, int], dict[str, float]] = {}
            for a, b in itertools.combinations(sorted(healthy), 2):
                snr_pairs[(a, b)] = sync_estimate.sample_pair_snr(
                    arrs[a], arrs[b],
                    sample_rate=sample_rate,
                    frame_ms=eff["frame_ms"],
                    hop_ms=eff["hop_ms"],
                    search_window_ms=eff["search_window_ms"],
                    coarse_rate=eff["coarse_rate"],
                    frame_min_rms_dbfs=eff["frame_min_rms_dbfs"],
                    samples=eff["group_snr_samples"],
                )
            groups = group_by_snr(
                snr_pairs, healthy,
                threshold=eff["group_snr_threshold"],
                min_samples=eff["group_snr_min_samples"],
            )
            group_snr = {f"{a + 1}-{b + 1}": d
                         for (a, b), d in snr_pairs.items()}
            log("channel-sync: anchor groups = " + " | ".join(
                "[" + ",".join(f"CH{m + 1}" for m in g) + "]"
                for g in groups)
                + f"  (threshold {eff['group_snr_threshold']:.2f}, "
                  f"{eff['group_snr_samples']} frames/pair, "
                  f"primary anchor CH{anchor + 1})")

            primary_comp = next((g for g in groups if anchor in g), [anchor])
            for gi, g in enumerate(groups):
                if g == primary_comp or len(g) < 2:
                    continue
                blocked = [
                    m for m in g
                    if rows[m].get("decision") == "fixed"
                    or rows[m].get("decision") == "already_aligned"
                ]
                if blocked:
                    log(
                        "channel-sync: group [" + ",".join(
                            f"CH{m + 1}" for m in g) + "] skipped — "
                        + ",".join(f"CH{m + 1}" for m in blocked)
                        + " 已被主锚点处置, 不与另一坐标系混用"
                    )
                    continue
                grp_anchor = group_anchor_of(g, eff["anchor_candidates"])
                for m in g:
                    if m == grp_anchor:
                        # 组锚点自己: 它是这一组的坐标原点, 不发生移位。
                        # 保留它在主锚点下的原因 (信息不能丢)。
                        prev_reason = rows[m].get("reason")
                        row = plain_row(m, "anchor", None)
                        row.update({
                            "delay_ms": 0.0, "delay_samples": 0.0,
                            "confidence": 1.0, "constant": True,
                            "fine_delay_ms": 0.0,
                            "rms_dbfs": (
                                round(rms_of[m], 1)
                                if rms_of.get(m) is not None else None
                            ),
                        })
                        rows[m] = row
                        group_notes[m] = {"anchor_role": "secondary",
                                          "primary_reason": prev_reason}
                        continue
                    e2 = _est_pair(grp_anchor, m)
                    saved = rows[m]
                    saved_used = used_est.pop(m, None)
                    dec2, e2_used, _ev2 = decide_and_record(m, e2, grp_anchor)
                    if dec2 == "untouched":
                        # 组内也没测出来 -> 保留主锚点下的原因 (更具体)
                        rows[m] = saved
                        if saved_used is not None:
                            used_est[m] = saved_used
                        continue
                    ests[m] = e2
                    ref_of[m] = grp_anchor
                    group_notes[m] = {
                        "primary_reason": saved.get("reason"),
                        "group_anchor": grp_anchor + 1,
                    }
                    if e2_used is not e2:
                        used_est[m] = e2_used
                    else:
                        used_est.pop(m, None)
                    if dec2 == "fixed":
                        to_fix.append(m)
                    log(
                        f"channel-sync: CH{m + 1} 主锚点下不可测, "
                        f"组内锚点 CH{grp_anchor + 1} 下判为 {dec2} "
                        f"({e2.delay_ms:+.3f}ms, {e2.usable_frames} 帧)"
                    )

        if eff["group_anchor_enabled"]:
            for gi, g in enumerate(groups):
                for m in g:
                    rows[m]["group"] = gi
            # 主锚点也打上 role: 否则报告里会出现两行 decision=="anchor",
            # 单看行无法区分谁是坐标原点。开关关闭时不写, 报告保持原样。
            rows[anchor]["anchor_role"] = "primary"
            for m, note in group_notes.items():
                rows[m].update(note)
            ctx["grouping"] = {
                "enabled": True,
                "threshold": float(eff["group_snr_threshold"]),
                "samples_per_pair": int(eff["group_snr_samples"]),
                "min_samples": int(eff["group_snr_min_samples"]),
                "anchor": anchor,
                "groups": [[m + 1 for m in g] for g in groups],
                "secondary_anchor_refs": {str(k + 1): v + 1
                                          for k, v in ref_of.items()},
                "snr": group_snr,
            }

        log(
            f"channel-sync: anchor=CH{anchor + 1}, measured "
            + ", ".join(
                f"CH{i + 1}="
                + (
                    f"{used_est.get(i, ests[i]).delay_samples * 1000.0 / sample_rate:+.3f}ms"
                    f"(conf {used_est.get(i, ests[i]).confidence:.2f})"
                    if used_est.get(i, ests[i]).delay_samples
                    == used_est.get(i, ests[i]).delay_samples
                    else "nan"
                )
                for i in healthy if i != anchor
            )
        )

        # 5. 逐轨修正 (纯整数移位) + 独立复检 (失败只回退该轨)
        #
        # P4.6: `eff["time_varying"]` 打开时, 该轨改走**逐窗**修正路径。默认
        # 关闭时这一段与 P1/P4.4 完全逐字节相同 —— 默认路径没有被改写过。
        tv_error: str | None = None
        if eff["time_varying"]:
            tv_error = _validate_tv_opts(eff)
            if tv_error is not None:
                # 显式指定不全 -> 拒绝整条时变路径。**不静默退回固定延迟**:
                # 调用方要求的是逐窗修正, 拿固定延迟交差就是另一种结果。
                log(f"channel-sync: TIME-VARYING REFUSED — {tv_error}")
                return finish("tv_not_configured", tv_error, channels=rows)
        fixed_ok: list[int] = []
        #: 精确模式验收用: {轨下标: (shift_samples, 复检残差样本数)}
        accurate_extras: dict[int, tuple[int, float]] = {}
        for i in to_fix:
            e = used_est.get(i, ests[i])   # 宽窗采纳轨用采纳估计 (窄窗 delay 可能为 NaN)
            # stereo 组绑定: 整组使用组代表的偏移 (组内相对偏移保持源样)
            shift = int(group_shift_override.get(
                i, int(np.rint(e.delay_samples))))
            if eff["time_varying"]:
                tv_res, tv_detail = _apply_time_varying_track(
                    i, e, arrs[i], fixed_raw[i], sample_rate, storage[i],
                    eff, log,
                )
                rows[i]["time_varying"] = tv_detail
                if tv_res is None or not tv_res.executed:
                    rows[i] = est_row(i, e,
                                      sync_estimate.summarize_trajectory(
                                          e.frames, sample_rate),
                                      "untouched", "time_varying_refused")
                    # 证据必须挂上: est_row 重建 dict 会丢掉它, 而 P4.3 (D1)
                    # 要求"测量发生过"在报告里可追溯。TV 分支的 e 就是本轨
                    # 估计, 直接由它重算同一份证据对象 (不是复制别处的 ev)。
                    rows[i]["evidence"] = evidence_of_est(e)
                    rows[i]["time_varying"] = tv_detail
                    log(f"channel-sync: CH{i + 1} time-varying refused "
                        f"({tv_detail.get('reason')}) — 该轨 untouched")
                    continue
            else:
                # 第三道门 (opt-in): 施加之前先用**另一条路径**独立测一次延迟,
                # 与 `shift` 比对。生产估计器是逐帧 GCC + 轨迹统计; 这里用
                # 宽搜索窗的整段 GCC + 相位斜率 (多窗取中位), 属于不同的
                # 估计路径, 因此"两边一致"是有信息量的。
                if (eff["verify_agree_samples"] > 0
                        or eff["verify_agree_rel"] > 0.0
                        or eff["verify_agree_sigma"] > 0.0):
                    agree = _agree_with_independent_estimate(
                        i, e, shift, decoded, ref_of.get(i, anchor), anchor,
                        sample_rate, storage[i], eff,
                    )
                    # **总是**把测量记到行上, 不只是拒绝时。否则"容差该用
                    # 哪种量纲"这类问题永远只能靠日志猜: 被放行的轨没有记录,
                    # 就无法把"差值"与"修得对不对"放在一起比。
                    rows[i]["agreement"] = agree
                    # 默认**只诊断不否决**: 否决权交给修后残差。依据见 DEFAULTS
                    # 里 verify_agree_veto 的实测 (误拒 5–23 条良好修正)。
                    if eff["verify_agree_veto"] and not agree.get(
                            "accepted", True):
                        rows[i]["decision"] = "untouched"
                        rows[i]["reason"] = "estimate_disagreement"
                        rows[i]["shift_samples"] = None
                        rows[i]["estimate_disagreement"] = agree
                        log(
                            f"channel-sync: WARNING CH{i + 1} 独立预检估计 "
                            f"{agree['independent_ms']:+.3f}ms "
                            f"({agree['independent_samples']:+.1f} 采样) 与 "
                            f"shift {shift:+d} 相差 "
                            f"{agree['diff_samples']:.1f} 采样 > "
                            f"{agree['tolerance_samples']:.1f} "
                            f"({agree['tolerance_basis']}) — 该轨不可信, "
                            "untouched"
                        )
                        continue
                try:
                    sync_fix.shift_stream(
                        arrs[i], fixed_raw[i],
                        delay_samples=group_shift_override.get(
                            i, e.delay_samples),
                        sample_rate=sample_rate,
                        storage_dtype=storage[i],
                        chunk_seconds=eff["fix_chunk_seconds"],
                    )
                except Exception as exc:      # noqa: BLE001 — 单轨失败不回滚其它轨
                    rows[i] = est_row(i, e,
                                      sync_estimate.summarize_trajectory(
                                          e.frames, sample_rate),
                                      "untouched", "recheck_residual")
                    # P4.3 (D1): 该轨的测量**确实发生过** —— est_row 重建 dict 会
                    # 丢掉上一步挂上的 evidence, 于是 "值有、证据也有" 的一条轨
                    # 在报告里变成 "只有值"。这里补回同一份证据对象。
                    rows[i]["evidence"] = ev
                    log(f"channel-sync: WARNING CH{i + 1} shift failed: {exc}")
                    continue
                # 落盘最小化 (M1b): `arrs[i]` 是 `decoded_{i}` 的唯一读者 ——
                # 紧接着的复检读的是 `arrs[anchor]`(L1188) 而不是本轨, 所以
                # 该轨移位一完成就可以释放它的 raw 解码副本, 不必等到整个
                # 循环结束再统一删。anchor 不释放: 它要给后续每一轨的复检
                # 当参考常驻。
                # 以 80 min / 4 轨为例, decoded 每轨 799.6 MB: 逐轨释放让
                # "decoded 4 份 + fixed 累积" 不再同时达到峰值。
                if i != anchor:
                    _close_map(arrs[i])
                    # 2026-10-29: 不再逐轨删除 decoded —— 长镜头时变后备
                    # (任一轨 non_constant/recheck_residual -> 整文件时变)
                    # 需要**全部声道**的原始 PCM。省下的 0.8 GB/轨 与
                    # "无法退回无对齐原始音频"的风险相比不值得; 句柄关闭后
                    # 内存占用已释放, 磁盘由 finally 统一清理。
            try:
                # 复检门: 绝对门 + 可选相对门。相对门只在**这条轨的偏移本身**
                # 远大于残余时放行 —— 即"修正带来了数量级改善"。小于
                # `min_shift_samples` 的偏移本来就不写, 所以不会因此放宽小位移。
                delay_ms_here = abs(float(e.delay_ms))
                limit = eff["verify_max_ms"]
                if eff["verify_max_rel"] > 0.0:
                    limit = max(limit, delay_ms_here * eff["verify_max_rel"])
                # 材料性放行的轨: 一个整数移位**不可能**把非恒定偏移抹平,
                # 它的物理极限就是该轨的实测位移。所以这类轨的验收上界取
                # **实测位移**本身 (不低于绝对下限), 而不是 max(相对门, 位移):
                # 相对门 (20%×24ms ≈ 4.8ms) 比位移宽得多, 用它会让"残余超过
                # 自身变化量"的修正混过去 —— 实测 `C1212 CH1` 残余 94 采样而
                # 位移只有 55 采样, 按相对门就漏了。
                #
                # 这也正是"不固定采样、按置信偏移"的落点: 容差不是常数, 而是
                # 这条轨自己做一次整数移位后**必然残留**的那个量。
                mat_note = materiality_note.get(i)
                if mat_note:
                    disp_here = float(mat_note["displacement_ms"])
                    # 位移与复检残差都是**估计量**。要求 residual <= 位移 严格
                    # 成立时, 不到一个采样的估计差就会翻转决定 —— 实测
                    # C1197 CH2: 残差 2.0393ms vs 位移 2.0208ms, 差 0.9 采样,
                    # 于是 25ms 的修正被判掉。留一个**小于测量粒度**的余量:
                    # max(1 采样, 5% x 位移)。门的语义仍是"残余不超过该轨
                    # 自身变化量", 只是不再把估计噪声当成判据。
                    margin = max(1000.0 / float(sample_rate),
                                 0.05 * disp_here)
                    limit = max(eff["verify_max_ms"], disp_here + margin)
                windows = max(1, int(eff["recheck_windows"]))
                # 多窗只在"被修掉的偏移远大于量化粒度"时才有意义: 多窗是
                # 用来抓**非恒定**偏移的, 而小位移轨的验收本来就由绝对门管。
                # 对它们启用多窗只会引入窗间方差 —— 实测 `C1174 CH2`
                # (shift −38 = 0.79 ms) 因此被误拒, 丢掉一条已验证的修正。
                # 阈值取 1 ms (≈48 采样), 与小位移的测量不确定度同量级。
                if abs(float(e.delay_ms)) < 1.0:
                    windows = 1
                if windows > 1:
                    wr = sync_fix.recheck_residual_windows(
                        arrs[ref_of.get(i, anchor)],
                        fixed_raw[i],
                        sample_rate=sample_rate,
                        storage_dtype=storage[i],
                        windows=windows,
                        anchor_segment_seconds=eff["anchor_segment_seconds"],
                        fine_phase_band_hz=tuple(eff["fine_phase_band_hz"]),
                    )
                    residual = wr["worst_ms"]
                    win_note = (f" ({windows} 窗取最差: "
                                f"{['%.3f' % v if v == v else 'nan' for v in wr['values']]})")
                else:
                    residual = sync_fix.recheck_residual(
                        # 分组锚点打开时, 不同组的轨要用**各自**的参考复检 ——
                        # 用主锚点去复检另一组的轨, 残差本来就应当很大, 那不是
                        # 修正失败, 是拿错了尺子。
                        arrs[ref_of.get(i, anchor)],
                        # 传路径而非就地 open 的流对象: recheck_residual 会自己
                        # 打开并在 finally 关闭它 (owns_tgt)。就地 open 的流没有
                        # 任何一方负责关闭, 只能靠 CPython 引用计数; 一旦引用被
                        # 保留 (异常回溯/调试器) 句柄即悬空, 下面的 unlink 会失败
                        # 并被静默吞掉, 留下整条 raw 临时文件。
                        fixed_raw[i],
                        sample_rate=sample_rate,
                        storage_dtype=storage[i],
                        anchor_segment_seconds=eff["anchor_segment_seconds"],
                        fine_phase_band_hz=tuple(eff["fine_phase_band_hz"]),
                    )
                    win_note = ""
            except Exception as exc:      # noqa: BLE001
                residual = float("nan")
                limit = eff["verify_max_ms"]
                win_note = ""
                log(f"channel-sync: WARNING CH{i + 1} recheck error: {exc}")
            if residual != residual or abs(residual) >= limit:
                # B: 复检残差否定 = "全片不是常数"的证据, 记下来触发整文件时变。
                recheck_failed_flags.add(i)
                rows[i]["decision"] = "untouched"
                rows[i]["reason"] = "recheck_residual"
                rows[i]["shift_samples"] = None
                log(
                    f"channel-sync: WARNING CH{i + 1} 复检残差 "
                    f"{residual:+.4f}ms 超门 {limit:.4f}ms{win_note} — "
                    "仅该轨回退为 untouched"
                )
                continue
            rows[i]["shift_samples"] = shift
            rows[i]["fractional_part_samples"] = j(
                e.delay_samples - float(shift), 3
            )
            log(f"channel-sync: fixed CH{i + 1} "
                f"(shift {shift} samples, 复检残差 {residual:+.4f}ms"
                + (f", 材料性放行 上限 {limit:.4f}ms" if materiality_note.get(i)
                   else "") + ")")
            fixed_ok.append(i)
            if (eff["channel_sync_accurate"]
                    and abs(shift) <= eff["min_shift_samples"]):
                # 只因精确模式降低了门才被修的轨 —— 需要额外验收。
                accurate_extras[i] = (
                    shift, abs(residual) * sample_rate / 1000.0)

        # 5b. 精确模式验收: 复检残差决定能按多细的粒度写下去。
        #
        # 必须在**音频文件生成之前**落定 (下面的第 8 步按 `fixed_ok` 决定
        # 该轨写移位后的数据还是源轨 stream copy)。事后再改判定会交出一个
        # "报告说没动、实际动了"的文件。
        accurate_report: dict[str, Any] | None = None
        if eff["channel_sync_accurate"]:
            tier, keep, dropped = resolve_accurate_tier(
                accurate_extras, min_shift_samples=eff["min_shift_samples"])
            for i, why in sorted(dropped.items()):
                rows[i]["decision"] = "already_aligned"
                rows[i]["reason"] = None
                rows[i]["shift_samples"] = None
                if i in fixed_ok:
                    fixed_ok.remove(i)
                log(f"channel-sync: WARNING CH{i + 1} 精确模式未通过验收 "
                    f"({why}) — 该轨不修")
            accurate_report = {
                "enabled": True,
                "tier_samples": tier,
                "candidates": {f"CH{i + 1}": {"shift": sh,
                                              "residual_samples": round(r, 3)}
                               for i, (sh, r) in sorted(
                                   accurate_extras.items())},
                "kept": [f"CH{i + 1}" for i in sorted(keep)],
                "dropped": {f"CH{i + 1}": why
                            for i, why in sorted(dropped.items())},
            }
            if dropped and tier == eff["min_shift_samples"]:
                log("channel-sync: WARNING 精确模式退化 — 1 样本与 2 样本粒度"
                    "都无法由复检残差确认, 本轮按常规 "
                    f"{eff['min_shift_samples']} 样本门执行")
            else:
                log(f"channel-sync: accurate — tier={tier} 样本 "
                    f"(候选 {len(accurate_extras)}, 采纳 {len(keep)}, "
                    f"放弃 {len(dropped)})")
            ctx["accurate"] = accurate_report

        # 5c. 长镜头整文件时变后备 (用户决策 B, 2026-10-29)
        #
        # 恒定路径已经先跑过 (决策/施加/复检), 因此这里只做"是否需要整文件
        # 转时变"的判定。触发条件: 任一**健康** target 实测不恒定
        # (`non_constant_flags`, 含被材料性判据放行的) 或复检残差否定
        # (`recheck_failed_flags`) —— 后者说明整数修正后残差仍超门。
        #
        # 触发后: 丢弃恒定路径的所有整数修正 (B 要求"不保留部分整数修正"),
        # 整文件走 core.long_take 的时变编排; 失败则回退**无对齐原始音频**。
        # 显式 `--channel-sync-tv*` (eff["time_varying"]) 的用户路径不受影响。
        if not eff["time_varying"]:
            tv_trigger: dict[int, str] = {}
            for i in healthy:
                if i == anchor:
                    continue
                if i in non_constant_flags:
                    tv_trigger[i] = "non_constant"
                elif i in recheck_failed_flags:
                    tv_trigger[i] = "recheck_residual"
            if tv_trigger:
                from . import long_take as lt

                log(
                    "channel-sync: long-take fallback -> whole-file "
                    "time-varying ("
                    + ", ".join(f"CH{i + 1} {why}"
                                for i, why in tv_trigger.items())
                    + ")"
                )
                for i in list(to_fix):
                    try:
                        fixed_raw[i].unlink()
                    except OSError:
                        pass
                lt_res = lt.align_time_varying(
                    work_dir=work_dir / "long_take",
                    anchor=anchor,
                    decoded=decoded,
                    storages=storage,
                    sample_rate=sample_rate,
                    groups=bound_groups,
                    log=log,
                )
                lt_block: dict[str, Any] = {
                    "enabled": True,
                    "path": (
                        "time_varying" if lt_res.get("applied")
                        else "unaligned"
                    ),
                    "reason": lt_res.get("reason"),
                    "trigger": {f"CH{i + 1}": why
                                for i, why in tv_trigger.items()},
                    "policy": lt.PROFILE["tv_policy"],
                    "coordinate": lt.PROFILE["tv_coordinate"],
                    "grid_hop_seconds": lt.PROFILE["tv_grid_hop_seconds"],
                    "measure_window_seconds":
                        lt.PROFILE["track_window_seconds"],
                    "rounds": lt_res.get("rounds"),
                    "max_shift": lt_res.get("max_shift"),
                    "flipped": lt_res.get("flipped"),
                    "followed_anchor": lt_res.get("followed_anchor"),
                    "targets": lt_res.get("targets"),
                    "timings": lt_res.get("timings"),
                }
                ctx["long_take"] = lt_block
                if not lt_res.get("applied"):
                    # B: 时变失败 -> 回退**无对齐原始音频** (不保留任何整数
                    # 修正)。透明模式的 finish 会输出源文件拷贝; 经典模式
                    # 不替换音频。进程继续, 不抛文件级异常。
                    for i in range(n_tracks):
                        rows[i]["decision"] = "untouched"
                        rows[i]["reason"] = (
                            rows[i].get("reason") or "time_varying_failed"
                        )
                    detail = ("time-varying fallback failed: "
                              + str(lt_res.get("reason")))
                    log(f"channel-sync: WARNING {detail} — 回退无对齐原始音频")
                    return finish("time_varying_failed", detail,
                                  channels=rows)
                # 时变成功: 用最终 raw 覆盖恒定路径的结果
                tv_storage = str(lt_res.get("storage") or "f32")
                final = lt_res.get("fixed_raw") or {}
                for key, path in final.items():
                    i = int(key)
                    fixed_raw[i] = Path(path)
                    storage[i] = tv_storage
                followed = {
                    int(m) - 1 for m in (lt_res.get("followed_anchor") or [])
                }
                targets = lt_res.get("targets") or {}
                rep_of: dict[int, str] = {}
                for rep_key, info in targets.items():
                    for m in info.get("members", []):
                        rep_of[int(m) - 1] = rep_key
                member_group: dict[int, list[int]] = {}
                for g in bound_groups:
                    for m in g:
                        member_group[int(m)] = [int(x) for x in g]
                fixed_ok = sorted(int(k) for k in final)
                for i in fixed_ok:
                    rep_key = rep_of.get(i)
                    info = targets.get(rep_key, {}) if rep_key else {}
                    row = plain_row(i, "fixed", None)
                    row.update({
                        "long_take": True,
                        "stereo_group": (
                            {"members": [m + 1 for m in member_group[i]]}
                            if i in member_group else None
                        ),
                        "time_varying": {
                            "policy": lt.PROFILE["tv_policy"],
                            "coordinate": lt.PROFILE["tv_coordinate"],
                            "rounds": info.get("rounds"),
                            "curve": info.get("curve"),
                            "residual": info.get("residual"),
                            "polarity": info.get("polarity"),
                        },
                    })
                    rows[i] = row
                for i in sorted(followed):
                    row = plain_row(i, "already_aligned", None)
                    row["long_take"] = True
                    row["stereo_group"] = {
                        "members": [m + 1 for m in member_group.get(i, [i])],
                        "bound_to_anchor": True,
                    }
                    rows[i] = row
                to_fix = list(fixed_ok)

        # 6. 结果范围判定
        failed_est = [
            i for i in healthy if i != anchor
            and rows[i]["reason"] in _ESTIMATION_FAIL_REASONS
        ]
        aligned_any = any(
            rows[i]["decision"] == "already_aligned"
            for i in healthy if i != anchor
        )
        if not fixed_ok and failed_est and not aligned_any:
            detail = "all measurable targets failed: " + "; ".join(
                f"CH{i + 1} {rows[i]['reason']}" for i in failed_est
            )
            log(f"channel-sync: WARNING {detail}")
            return finish("measure_failed", detail, channels=rows)

        if not fixed_ok:
            scope = "partial" if any(
                r["decision"] == "untouched" for r in rows
            ) else "file"
            log(
                f"channel-sync: already aligned (无轨需要修正; 可测轨 "
                f"|delay| < {eff['aligned_max_ms']} ms)"
            )
            return finish("already_aligned",
                          "no track requires a fix", scope=scope,
                          channels=rows)

        # 7. per-**stream** 输出 (A6 结构保持):
        #    * 单声道流: fixed -> 回编码(源 codec); untouched -> 源 stream copy
        #      （与旧实现逐位相同）;
        #    * 多声道流: 任一声道需要修正 -> 把该 stream 的全部声道
        #      （fixed + untouched 原样）按源顺序交织成 WAV（内置
        #      `audio_wav.write_wav`）再按源 codec/入口编码为一条流；
        #      没有声道需要修正 -> 整条流 stream copy（逐位保持）。
        #
        # 落盘最小化 (M1): 到这里测量/移位/复检都已经结束, 不再需要的
        # `decoded_*` 先关句柄再删。多声道流里**未修正声道**的 decoded raw
        # 还要用于交织，必须留到该流写完。
        for a in arrs:
            _close_map(a)
        arrs = []
        for i in range(n_tracks):
            if stream_channels[i] > 1 and i not in fixed_ok:
                continue                    # 交织时还要读原始声道
            try:
                decoded[i].unlink()
            except OSError:
                pass                        # finally 会重试; 失败不致命

        def _encode_mono_channel(i: int, out: Path) -> str | None:
            src_raw = fixed_raw[i]
            fmt = "f32le" if storage[i] == "f32" else "f64le"
            proc = subprocess.run(
                [
                    str(ffmpeg), "-v", "error", "-nostdin", "-y",
                    "-f", fmt, "-ar", str(sample_rate), "-ac", "1",
                    "-i", str(src_raw),
                    "-c:a", codecs[i],
                    "-f", _muxer_for_entry(entries[i]), str(out),
                ],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace", timeout=1800,
            )
            if proc.returncode != 0 or not out.is_file():
                return (proc.stderr or "")[-200:]
            return None

        def _encode_interleaved_stream(si: int, ch_idx: list[int],
                                       out: Path) -> str | None:
            # 交织源顺序 = 该 stream 在源里的声道顺序（scope 已按 stream/
            # channel 升序展开）。fixed 声道用 fixed_raw，其余用 decoded。
            raws = [
                fixed_raw[i] if i in fixed_ok else decoded[i]
                for i in ch_idx
            ]
            wav = work_dir / f"audio_{si}_interleaved.wav"
            try:
                frames = _interleave_stream_raw(
                    raws, [storage[i] for i in ch_idx],
                    sample_rate=sample_rate,
                    channel_count=len(ch_idx),
                    layout=layout_of[ch_idx[0]],
                    wav_format=_wav_format_for_codec(codecs[ch_idx[0]]),
                    dst_wav=wav,
                )
            except Exception as exc:                   # noqa: BLE001
                return f"interleave failed: {type(exc).__name__}: {exc}"
            proc = subprocess.run(
                [
                    str(ffmpeg), "-v", "error", "-nostdin", "-y",
                    "-i", str(wav),
                    "-c:a", codecs[ch_idx[0]],
                    "-f", _muxer_for_entry(entries[ch_idx[0]]),
                    str(out),
                ],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace", timeout=1800,
            )
            try:
                wav.unlink()
            except OSError:
                pass
            if proc.returncode != 0 or not out.is_file():
                return (proc.stderr or "")[-200:]
            if frames <= 0:
                return "interleaved output has 0 frames"
            return None

        audio_files: list[Path] = []
        streams_with_fixes: list[int] = []
        for si in range(n_streams):
            ch_idx = [i for i in range(n_tracks) if stream_of[i] == si]
            out = work_dir / f"audio_{si}.mov"
            muxer = _muxer_for_entry(entries[ch_idx[0]])
            err: str | None = None
            if stream_channels[ch_idx[0]] == 1:
                i = ch_idx[0]
                if i in fixed_ok:
                    err = _encode_mono_channel(i, out)
                    if err is None:
                        streams_with_fixes.append(si)
                else:
                    ok_c, c_err = _copy_stream(ffmpeg, source, si, out, muxer)
                    err = None if ok_c else c_err
            else:
                fixed_here = [i for i in ch_idx if i in fixed_ok]
                if fixed_here:
                    err = _encode_interleaved_stream(si, ch_idx, out)
                    if err is None:
                        streams_with_fixes.append(si)
                else:
                    ok_c, c_err = _copy_stream(ffmpeg, source, si, out, muxer)
                    err = None if ok_c else c_err
            if err is not None:
                return finish(
                    "verify_failed",
                    f"audio stream {si} output failed: {err[-200:]}",
                    channels=rows,
                )
            audio_files.append(out)
            # 落盘最小化 (M1): 该 stream 的 per-track 产物已经写出来了,
            # `fixed_*` 到此再无消费者（复检已在上面完成）。
            for i in ch_idx:
                try:
                    fixed_raw[i].unlink()
                except OSError:
                    pass
                try:
                    decoded[i].unlink()
                except OSError:
                    pass

        scope = "partial" if any(
            r["decision"] == "untouched" for r in rows
        ) else "file"
        log(
            f"channel-sync: applied ({len(fixed_ok)}/{n_tracks} channels "
            f"fixed, scope={scope})"
        )

        # 8. transparent: 视频/非音频 stream copy + 音频轨替换重封装。
        # 容器处理已抽成 `remux_transparent_audio`, 与长素材对齐等其它
        # "音频轨已经算好"的流程共用同一份实现。
        output_file: Path | None = None
        file_copied = False
        if t_mode and dst is not None:
            try:
                output_file, file_copied = remux_transparent_audio(
                    source=source, dst=dst, audio_files=audio_files,
                    work_dir=work_dir, gpac=gpac,
                    restore_metadata=restore_metadata, streams=streams,
                    log=log,
                )
            except RuntimeError as exc:
                return finish("verify_failed", str(exc), channels=rows)

        return report(
            "applied",
            f"measure -> fix -> verify OK ({len(fixed_ok)}/{n_tracks} fixed)",
            scope=scope,
            channels=rows,
            audio_files=audio_files,
            fixed_files=[audio_files[si] for si in streams_with_fixes],
            output_file=output_file,
            file_copied=file_copied,
        )
    finally:
        for a in arrs:
            _close_map(a)
        for p in intermediates:
            try:
                p.unlink()
            except OSError:
                pass
