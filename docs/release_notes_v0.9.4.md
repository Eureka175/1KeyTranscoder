# v0.9.4 发布说明 — GPU 对齐默认化 / NVENC 动态 VBV / 硬件缩放可行性

> **状态**：**Pre-release**（GitHub prerelease；本地 tag `v0.9.4`；`VERSION` = `0.9.4`）  
> **基线**：`v0.9.0`（tag `v0.9.0`）  
> **日期**：2026-11-07  
> **范围**：GPU 音频对齐默认路径、并行音频 apply、NVENC 动态 maxrate/VBV、
> 测试修复与硬件缩放可行性研究。  
> **不包含**：新的视频缩放实现、VCE 后端入口、QSV 码率策略变更。
> **兼容性**：C1305/C1306 验收运行的最终音频 MOV 哈希与既有基线一致；
> GPU 不可用时自动回退 CPU 路径。

---

## 0. 本版一句话

v0.9.4 把 GPU 音频对齐从“实验路径”推进为**默认可用路径**：默认 fp32 CUDA，
CUDA 不可用时自动回退 CPU；同时默认启用 unit pipeline 与 chunk 级并行音频
apply，在 C1305/C1306 验收素材上保持最终音频哈希不变。NVENC 的 maxrate/VBV
改为跟随 x265 的源相对动态 VBV 规则；QSV 保持静态表，VCE 仍无入口。

## 1. GPU 音频对齐

- 统一 `core/gpu_align` 架构：window CC、residual CC、batch refine 与
  surface pipeline。
- 默认 `KTS_GPU_ALIGN=fp32`；CUDA/CuPy 不可用时自动 CPU 回退。
- 默认启用 unit pipeline；canonical window-plan prewarm 使 residual 计划
  顺序稳定。
- 固定 GPU budget、fresh cuFFT plan、单 GPU 执行锁、run-level residual
  dispatcher 保留，避免显存/线程竞争导致的非确定性。
- `reference_interp_write_inplace()` 增加 chunk 级并行；`KTS_APPLY_WORKERS`
  在 pipeline 模式默认 4，pipeline 关闭时默认 1。
- 本机 C1305/C1306 基准（具体结果见
  `docs/design/gpu_performance_report.md`）：

| 场景 | 优化结果 |
|---|---:|
| C1306 | 98.20 / 98.75 / 99.69 s |
| C1305 | 178.76 / 183.83 s |

上述运行的 final audio 哈希均与基线一致。

## 2. NVENC 动态 maxrate / VBV

NVENC（HEVC 与 AV1）现在按 `x265_scaling.json` 的 `dynamic_vbv` 表，根据源
OB 和效率分类逐文件计算：

- `max_bitrate = clamp(OB * target_ratio, min, max)`
- `vbv_bufsize = round(max_bitrate * bufsize_factor)`

约束：

- QSV **不动**：继续使用其静态 profile；
- VCE **暂不做入口**：CLI 不提供 VCE；
- 未知 OB / 缺少分类时回退静态 NVENC profile。

## 3. 测试结果

- `--level full`：1295 PASS / 11 FAIL（全部为真实语料缺失）；
- `--level toolchain`：1079 PASS / 2 FAIL（Sony 语料缺失）；
- `--level unit`：1058 PASS / 2 FAIL（既有环境语料检查）；
- NVENC dry-run：95 个文件成功计划，含 `[NVENC-VBV]` 动态值日志；
- 未发现本次改动引入的新代码失败。

## 4. 硬件缩放可行性研究

新增 [`docs/design/hardware_scaling_feasibility.md`](hardware_scaling_feasibility.md)：

- NVENC / QSV 的 `--vpp-resize` 均可用于硬件缩放；
- 建议未来实现默认算法为 **bilinear**；
- 推荐先做 NVENC，再评估 QSV；
- VCE 不做入口；
- **本版没有实现缩放代码**，仅完成可行性研究。

## 5. 包与校验

完整包（self-contained，含 NVENC/QSV/GPAC 工具）：

```text
1KeyTranscoder-v0.9.4-win64-selfcontained.zip
1KeyTranscoder-v0.9.4-win64-selfcontained.zip.sha256
```

构建：

```powershell
python release\build_release.py
```

校验：

```powershell
python release\verify_package.py `
  dist\1KeyTranscoder-v0.9.4-win64-selfcontained.zip `
  --extract-dir "$env:TEMP\1KT-v0.9.4-clean"
```

包内：

- `VERSION` = `0.9.4`；
- `release-manifest.json` 记录版本、commit、运行时工具版本、逐文件 sha256；
- 不包含 `testsets/`、`work/`、`docs/`、`dist/` 等开发产物。

## 6. 已知限制

- CUDA 运行时不由包提供；GPU 对齐需要本机 CUDA/CuPy 环境，否则自动走 CPU。
- 全量 L3 仍受真实 Sony/A7M5/classic 语料缺失影响；11 条失败均为环境项。
- 硬件缩放仅可行性研究，尚无 CLI。
- VCE 仍未接线。

**完整变更记录**：`git log v0.9.0..v0.9.4`。
