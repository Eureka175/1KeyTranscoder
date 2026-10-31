# 音频对齐 / 元数据保留 — 性能优化报告（v0.8.11 后）

> 分支：`perf/audio-metadata`（从 `main` `5d08370` 切出；发布 tag `v0.8.11` 不动）
> 状态记录：`docs/design/perf_state.md`；计划：`docs/design/audio_metadata_perf_plan.md`
> 原始代码备份：`work/perf/backup_v0.8.11/`

## 0. 一句话

在不改变算法/阈值/输出语义（所有对比均做到**最终音频 raw/MOV 逐位一致**）的
前提下：

- **音频域**：C1305 80 min **689.9 → 357.2 s（-48.2%）**，C1306 69 min
  **-20.2%**（同一 harness）；long-take 附加 **231.0 → 118.6 s**。
- **透明 50 GiB**：端到端 **12.8 → 9.63 min（-25%）**；磁盘写入从
  **~150 GiB（stage 写 + 读 + 最终写）降到 ~50 GiB（仅 GPAC 一次写）+ 7.6 MB
  moov 补丁**；不再产生 50 GiB stage 中间文件。
- 磁盘中间文件：C1306 音频侧中间写盘 **~10.6 → ~6.0 GB（-43%）**；
  round1/round2/polarity 中间 raw 不再落盘。
- 内存：默认速度优先时峰值 RSS 主进程 ~1.14–1.23 GB、进程树 ~1.9–2.3 GB
  （旧串行 775 MB / 1470 MB）。**内存受限配置实测：`stereo_bind_workers=1` +
  `est_worker_cap=4` + 串行 long-take → 主进程 748.6 MB / 树 1036.2 MB**
  （-39% / -55%，输出仍逐位一致），代价 wall 236.3 → 338.9 s；三个预算键
  都是 config/内部键（非 CLI），默认值保持既有行为。

## 1. 测量环境与基线

| 项 | 值 |
|---|---|
| CPU | Intel Core Ultra 9 285H（16 物理核） |
| RAM | 31.5 GB |
| GPU | RTX 5070 Laptop 8 GiB（本次优化**未使用 CUDA**，原因见 §5） |
| 输入 | C1305（80.1 min，4×mono PCM 48 kHz + HEVC + rtmd）、C1306（69.4 min） |
| 基线 | v0.8.11 验收记录：C1305 689.9 s / C1306 389.1 s；long-take 231.0/193.8 s；RSS 775/1470 MB；透明 50 GiB：stage mux 258.7 s + fast restore 99.1 s + E2E 12.8 min |

## 2. 音频对齐优化

### 2.1 改动

1. **stereo 绑定检测与锚点估计并行**（`core/channel_sync.py`）：检测只读 decoded
   raw，放后台 daemon 线程与锚点估计（进程池）重叠执行，主路径前 join；
   失败语义不变（异常 → 无绑定组）。
2. **long-take 窗测量并行 + anchor coarse 共享**（`core/long_take.py`）：
   各 target 的 60 s/10 s 连续性测量互相独立，线程池并行；锚点
   `coarse_decimate` 只算一次并在各线程共享（只读数组）。
3. **下游流水线按 unit 并行**：曲线→round1→残差→round2→极性拆成嵌套函数，
   unit 间线程并行；合并顺序固定为 units 顺序（报告字段/翻转列表与串行一致）。
4. **原地施加（磁盘写入最小化）**：
   - round-1 参考分数插值直接改写 `decoded_*.f32`（参考实现的每个 chunk 只依赖
     自身输入，可安全读-算-写回）；
   - round-2 用 `reference_interp_write_inplace` 在 pass1 文件上原地做第二重
     插值；
   - 极性用 `_invert_raw_inplace` 原地取反；
   - `channel_sync` finalize 识别 `fixed_raw[i] == decoded[i]`，不会提前删除。
5. 内存相关：`_COARSE_CHUNK_SECONDS` 100 → 25 s（transient 缓冲更小，结果逐位
   不变）；stereo 检测后 `gc.collect` + Windows `EmptyWorkingSet` 归还空页；
   PROFILE 增加 `measure_parallel_units` / `pipeline_parallel_units` 开关。

### 2.2 结果

| 素材 | 基线 | 优化后 | 变化 | 正确性 |
|---|---|---|---|---|
| C1305 音频域 wall | 689.9 s | **357.2 s** | **-48.2%** | 4 条 final `audio_*.mov` 与旧验收基线 **sha256 完全一致**；report 语义一致 |
| C1305 long-take | 231.0 s | **118.6 s** | **-48.7%** | 同上 |
| C1306 wall（同 harness） | 296.3 s | **236.3 s** | **-20.2%** | 同上（4 条 MOV sha256 一致） |
| C1306 long-take | 159.8 s | 100.6 s | -37% | 同上 |
| 音频中间写盘（C1306） | ~10.6 GB | **~6.0 GB** | **-43%** | work 目录只剩 final MOV + report |
| 峰值 RSS（主/树） | 775/1470 MB | 1138–1232 / 1922–2331 MB（默认并行） | 内存上升（并行代价）；内存受限配置可降到 **748.6/1036.2 MB**，输出逐位一致 |

结果文件：`K:\1kt_work\perf\ws5\c1305\`、`K:\1kt_work\perf\ws2\run8\`，
parity 报告 `K:\1kt_work\perf\ws1\baseline_*_manifest.json`。

## 3. 元数据/透明路径优化

### 3.1 改动

1. `preservation/gpac.py::mux_new(..., flat=True, brand=..., add_brands=...)`：
   GPAC 直接以 flat 布局把**最终产物**写到目标路径（mdat 先、moov 后），
   并用 `-brand XAVC -ab mp42 -ab nras`（或按源 ftyp 尺寸推导的等价组合）
   得到与源 ftyp **同尺寸**的 ftyp（Sony XAVC 32 B；isom 源 24 B 等）。
2. `preservation/fast_restore.py::fast_restore_inplace`：在同一文件上只改写
   ftyp（32 B）+ moov（~7.6 MB）+ 追加 root uuid / 文件级 meta（15.5 KB）；
   **mdat 一个字节不动**（chunk offset 不变，仍校验全部 offset 落在 mdat 内）；
   moov 构建与 copy 路径共享同一 helper。
3. `preservation/transparent_restore.py`：自动选择 in-place（ftyp 尺寸可匹配）→
   copy fast restore（同一个 GPAC 产物作 stage，写临时文件后原子替换）→
   标准 GPAC 多轮恢复；失败最终回退源文件拷贝，语义不变。

### 3.2 结果（50 GiB C1305 真实 1kt 透明模式端到端）

| 指标 | 旧 | 新 | 变化 |
|---|---|---|---|
| 端到端 | 12.8 min | **9.63 min** | **-24.8%** |
| 磁盘写入 | stage 50 GiB 写 + fast restore 50 GiB 读 + 50 GiB 写 | **GPAC 写 50 GiB + moov 补丁 7.6 MB + 追加 15.5 KB** | **约 -66%** |
| 中间文件 | stage 50 GiB + 输出 | **无 stage**（输出即最终文件） | — |
| Sony checker | PRESERVED=37 | PRESERVED=37 / MODIFIED=0 / MISSING=0 | 不变 |
| payload | 通过 | rtmd 全量 streamhash = 源；视频 10 s hash 一致；4 音频解码与生产 raw diff=0 | 不变 |

6 源 hook 矩阵（XAVC clips + isom hard cases）全部 PASS；XAVC 与 isom
源现在都走 **in-place**（isom 24 B ftyp 通过 `-brand isom -ab iso2` 匹配尺寸，
`_fast_brand_args` 按源 ftyp 尺寸通用推导）；in-place 不适用时仍自动回退
copy fast restore（同一个 GPAC 产物作 stage，不重复 mux）。

## 4. 回归

| 套件 | 结果 |
|---|---|
| unit | **1020 PASS / 2 FAIL**（真实 Sony 语料缺失环境项） |
| 全量 L3 | **环境阻塞**（4 次尝试均在本机硬件探测子进程上挂起：NVENC/QSV `run_tool`/`_device_identity` 的 `communicate` 不返回、killed parent 后 QSVEncC64 孤儿仍持有管道；每次卡住吃掉 120 s timeout，多用例累加后远超正常 15 min；同样的 1kt 命令单独重跑 21 s/33 s 正常通过。此前 v0.8.11 基线全量 L3 为 **1261 PASS / 12 FAIL**，12 条均为缺真实媒体环境项；本轮改动已由 unit + C1305/C1306 parity + 透明 50 GiB + 6 源矩阵覆盖，L3 硬件探测挂起与本轮改动无栈级关联） |
| C1305/C1306 音频 parity | **PARITY OK**（final MOV sha256 与旧基线完全一致） |
| `final_accept_v2` 复跑（记录产物） | **OVERALL: PASS**（曲线 CH3/CH4 0/0/0；音频 CH1/CH3/CH4 0/0，CH2 EXPECTED(4)）；新代码输出已由 parity 证明与这些记录产物逐位一致 |
| 透明小样本 | in-place / copy / 标准三条路径 payload 全一致；结构校验通过 |
| 透明 50 GiB | 结构/rtmd/音频/签名全 PASS；Sony checker 37/37 |

## 5. 未做/下一步

- **CUDA 未使用**：当前热点（12 kHz/48 kHz 相关、分数插值）必须与参考实现
  **逐位一致**，消费级 GPU 的 FP64 很慢；用 FP32 cuFFT 会改变相关峰/置信度，
  存在破坏验收的风险。若未来允许"非逐位一致"的质量模式，可评估
  float32 GPU FFT 批量相关 + CPU float64 精化。
- **内存峰值**（主 ~1.2 GB）：并行 long-take 测量持有 anchor+2×target coarse
  （~693 MB）是主因；已有 profile 开关换回 ~0.8 GB。进一步方案：窗测量串行 +
  窗级批处理 CUDA/CPU 预取，或 coarse 缓存 mmap。
- **估计阶段**（C1305 36 次 pair，169 s）仍有换参考重测的顺序性；并行化需
  保持"后来轨才可作参考"的语义，属较大改动。
- **残差测量**（C1305 59 s）：60 s 窗重叠 resample_poly 重复重采样，整文件预抽取
  可省 ~3× 但会增加内存/需位级一致性证明。
- **GPAC mux 本身**仍是 50 GiB 一次写（~2.5–4 min），是透明路径剩余最大项；
  自写 muxer 直写最终文件可再省一次全文件写，但风险高。

## 6. 复现

```powershell
# 音频域 baseline/after（C1306/C1305）
python work\perf\ws1_profile.py --source c1306 --mode timing --out-dir K:\1kt_work\perf\ws2\run8
python work\perf\parity_check.py --compare K:\1kt_work\perf\ws2\run8\c1306_timing_work `
    --baseline K:\1kt_work\perf\ws1\baseline_manifest.json --ignore-missing

# 透明 50 GiB（真实 1kt 命令）
python 1kt.py --input <dir with C1305> --output K:\1kt_work\perf\e2e_out `
    --channel-sync-transparent --headless --jobs 1 --keep-work
python work\perf\ws4\verify_e2e_50g.py

# 6 源 hook 矩阵
python work\perf\ws5\test_hook_matrix.py
```
