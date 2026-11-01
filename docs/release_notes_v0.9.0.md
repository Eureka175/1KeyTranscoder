# v0.9.0 发布说明 — 音频对齐 / 元数据保留性能优化（CUDA opt-in）

> **状态**：**Pre-release**（GitHub prerelease；本地 tag `v0.9.0`；`VERSION` = `0.9.0`）
> **基线**：`v0.8.11`（tag `v0.8.11` = `4840dd8`）
> **日期**：2026-11-01
> **范围**：性能优化（音频对齐 + 透明重封装元数据恢复）+ 可选的 CUDA 加速。
> **不含**：新的 CLI 开关、视频编码画质/码率策略变更、音频算法语义变更、
> 漂移模型之外的 resampling/loudness。
> **兼容性**：默认路径的最终音频产物与 v0.8.11 **逐位一致**（两个真实 80 分钟
> 素材 sha256 相同）；透明模式容器产物经 Sony checker/结构/rtmd/音视频 payload
> 全量复验；未启用 GPU 时行为与 v0.8.11 等价。

---

## 0. 本版一句话

v0.9.0 不改变"做什么"，只改变"多快、写多少盘"：长镜头音频对齐 **C1305 从
689.9 s 降到 204.1 s（-70.4%）**、C1306 同 harness **296.3 → 148.0 s（-50.1%）**，
透明模式 50 GiB 容器恢复 **12.8 → 9.63 min** 且**磁盘写入减少约 66%**；GPU
（cuFFT float64）为 **opt-in、默认关闭**，仅用于窗级相关 FFT，经两个真实文件
验证输出逐位一致。

## 1. 音频域性能（默认 CPU 路径）

| 项目 | v0.8.11 基线 | v0.9.0 CPU 默认 | 变化 |
|---|---|---|---|
| C1305 80 min 音频域 wall | 689.9 s | **288.4 s** | **-58.2%** |
| C1306 69 min 同 harness wall | 296.3 s | **217.0 s** | **-26.8%** |
| C1305 long-take 附加耗时 | 231.0 s | **118.6 s** | -48.7% |
| C1305 `estimate_pair` 累计 | 169.7 s | **106.5 s** | -37% |
| 音频中间写盘（C1306） | ~10.6 GB | **~6.0 GB** | -43% |

实现：

1. stereo 组绑定检测与锚点估计**并行**；长镜头窗测量按目标声道并行，并让各
   目标共享一次 anchor coarse 抽取。
2. 下游 per-unit 流水线（round-1 插值、残差、极性、施加）并行，归并按时间序
   拼接（结果逐位不变）。
3. round-1/round-2/极性全部**原地施加**，不再产生 `applied_*`/`polarity_*`
   中间 raw 文件；固定路径也不再复制与源相同的 `fixed_*` 文件。
4. **持久化帧估计进程池**：一次 channel-sync 复用同一个 12 进程池，不再为每次
   `estimate_pair` 新建（C1305 36 次调用）。
5. coarse 分块 100 → 25 s（瞬时缓冲更小，结果不变）；检测后
   `gc.collect` + Windows `EmptyWorkingSet` 归还空页。
6. 可选 CUDA（见 §3）：只把窗级 coarse CC 与 round-2 残差窗的 FFT 移到 cuFFT
   float64，其余数学保持 CPU float64。

## 2. 元数据保留 / 透明模式性能

50 GiB 真实 C1305 透明重封装（`--channel-sync-transparent`）：

| 指标 | v0.8.11 | v0.9.0 | 变化 |
|---|---|---|---|
| 端到端 | 12.8 min | **9.63 min** | **-24.8%** |
| 磁盘写入 | ~150 GiB（stage 写 + 读 + 最终写） | **50 GiB GPAC 一次写 + 7.6 MB moov 补丁** | **约 -66%** |
| 中间文件 | 50 GiB stage | **无 stage**（输出即最终文件） | — |
| 校验 | PASS | Sony checker **PRESERVED=37 / MODIFIED=0 / MISSING=0**；结构/rtmd/音频/签名检查 PASS | 不变 |

实现：

1. 新增 `preservation.fast_restore.fast_restore_inplace()`：在 **flat + 正确
   brand** 的 GPAC 输出上原地重建——只改 ftyp（32 B）/ moov（~7.6 MB）/追加
   root uuid 与 meta（~15.5 KB），**mdat 一字节不动**，并校验所有 chunk offset。
2. 透明模式让 GPAC 直接写最终路径，不再先写 stage 再 copy；in-place 不适用时
   自动回退 copy fast restore（同一个 GPAC 产物作 stage，不重复 mux）。
3. `_fast_brand_args` 按源 ftyp 尺寸通用推导 brand（XAVC 32 B / isom 24 B），
   24 B ftyp 的 isom 源也走 in-place；6 源 hook 矩阵（XAVC + isom hard cases）
   全部 PASS。

## 3. CUDA 加速（opt-in，默认关闭）

- **默认关闭**：`channel_sync` 的配置项 `gpu_cc`（内部/config 键，非 CLI）
  默认 `False`；未开启时不需要 CuPy/CUDA，行为与 v0.8.11 等价。
- 开启后只把 **窗级 coarse CC** 与 **round-2 残差窗**的 FFT 放到 cuFFT
  **float64**；整数峰/精化/6 位小数记录口径不变，CPU float64 仍是唯一数值口径。
- 实测量级：1.44 M 点窗 FFT **2.0–2.7 ms（GPU） vs 74 ms（CPU），约 36×**。
- 两个真实文件最终 audio MOV 与 CPU 基线 **逐位一致（PARITY OK）**：

| 素材 | CPU 默认 | CUDA opt-in | 变化 | VRAM 峰值 | GPU util 峰值 |
|---|---|---|---|---|---|
| C1305（80 min） | 288.4 s | **204.1 s** | **-29%（较 v0.8.11 -70.4%）** | 1288 MiB | 69% |
| C1306（69 min） | 217.0 s | **148.0 s** | **-32%（较基线 -50.1%）** | 1168 MiB | 98% |

- 开启条件：本机需 CuPy（`cupy-cuda12x` 14.x）+ CUDA 12 runtime；CuPy/CUDA
  不可用时逐窗自动回落 CPU。**未在其他机器/素材上验证前不要把 `gpu_cc`
  当默认开启**；本仓库只在两个验收素材上证明过逐位一致。
- 本开发机备注：非 ASCII 用户名会破坏 NVRTC 的源/头文件路径，
  `core/gpu_cc.py` 内置 `C:\sp_ascii` junction 回退（普通 ASCII 安装官方 CUDA
  toolkit 不需要）；CUDA 运行库用 pip CUDA12 runtime wheels 组装到 `C:\CUDA12`。

## 4. 内存 / 显存

| 项目 | v0.8.11 | v0.9.0 |
|---|---|---|
| 峰值 RSS（主/树，默认 CPU 并行） | 775 / 1470 MB | 1138–1232 / 1922–2331 MB（并行换时间） |
| 低内存配置（`stereo_bind_workers=1`、`est_worker_cap=4`、串行 long-take） | — | **748.6 / 1036.2 MB**，输出仍逐位一致，C1306 wall 338.9 s |
| CUDA 模式 | — | VRAM 1168–1288 MiB；host RSS 1.8–2.2 GB（CuPy/runtime） |

新增内部/config 键：`stereo_bind_workers`（默认 4）、`est_worker_cap`（默认
12）、`gpu_cc`（默认 False）；均不是 CLI 参数。

## 5. 正确性与验收

- C1305/C1306：4 条 final `audio_*.mov` 与 v0.8.11 验收基线 **sha256 完全一致**。
- `final_accept_v2` 复跑：**OVERALL PASS**（曲线层 CH3/CH4 = 0/0/0；音频层
  CH1/CH3/CH4 = 0/0，CH2 = EXPECTED(4) 为既定 stereo 组决策）。
- 透明 50 GiB：Sony checker 37/37；结构/rtmd/音视频 payload/签名检查全 PASS。
- 6 源 fast-restore hook 矩阵：全 PASS。
- 单元测试：**1020 PASS / 2 FAIL**，2 条均为"缺真实 Sony 样片语料"的环境项。
- 全量 L3：**1261 PASS / 12 FAIL**，与 v0.8.11 基线完全同数；12 条
  全部是缺测试集/语料的环境项（A7M5 4CH、Sony C9037、classic_test.MP4、
  DJI 机型语料、cls 语料），无产品回归。详见 §7。

## 6. 包与校验

- 包：`1KeyTranscoder-v0.9.0-win64-selfcontained.zip`（附 `.sha256` sidecar）。
- 构建：`python release/build_release.py`（`VERSION`、tag `v0.9.0`、`main`
  分支与干净工作区必须一致）。
- 校验：

  ```powershell
  python release\verify_package.py `
    dist\1KeyTranscoder-v0.9.0-win64-selfcontained.zip `
    --extract-dir "$env:TEMP\1KT-v0.9.0-clean"
  ```

## 7. 已知限制 / 环境说明

- **全量 L3 结果（2026-11-01）**：`KTS_PROBE_TIMEOUT=60` 下
  `tests/full_autotest.py --level full` 用时 18.8 min，**1261 PASS / 12 FAIL**，
  与 v0.8.11 的基线**完全同数**；12 条全部为环境项：A7M5 4CH 测试集缺失、
  Sony C9037 缺失、classic_test.MP4 缺失、DJI 机型语料缺失、cls 语料缺失。
  无代码/产品性失败。
- 本机硬件探测在部分驱动状态下会挂起（`NVEncC/QSVEncC --check-features`
  的 `communicate` 不返回），默认 120 s 超时会拖长全量 L3；新增内部环境变量
  `KTS_PROBE_TIMEOUT=<秒>` 只用于**测试/诊断**时收紧探测超时（默认行为
  不变，不写进任何产品路径）。用 10 s 试跑时会因 QSV toolchain 超时产生
  9 条额外失败（1247/21），属测试超时设置问题；60 s 即恢复 1261/12。
- CUDA 默认关闭；`gpu_cc` 开启前必须按验收素材复验逐位一致性。
- 低内存配置以 wall 换内存（C1306 236.3→338.9 s），默认仍速度优先。

**完整变更记录**：`git log v0.8.11..v0.9.0`。
