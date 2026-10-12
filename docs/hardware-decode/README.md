# hardware-decode — integration session

> **本目录 ≠ 归档区。** 归档的 research 文档在
> [`../../olddocs/docs/hardware-decode/`](../../olddocs/docs/hardware-decode/)。
> 本目录是 **integration session 的产出**：测试矩阵、机器可读矩阵、provenance 记录
> 与最终报告。

## ⚠️ 当前状态：**BLOCKED**（不是 PASS）

本目录描述的 83 例矩阵**当前无法运行**：它依赖 `tools/avhw/` 下两个
provenance 固定（sha256 + 版本串）的 **patched 研究构建**，而本机该目录不存在。

```text
python -m tests.hwdecode.harness provenance
    -> 2 failure(s)   (NVEncC / QSVEncC: 二进制缺失)

重建前提（本机核查结果）：
    cl / msbuild (MSVC)      NOT FOUND       <- 硬阻塞
    Visual Studio            NOT FOUND       <- 硬阻塞
    Intel oneVPL / vcpkg     NOT FOUND       <- QSV 硬阻塞
    rigaya 源码树             NOT FOUND       <- 硬阻塞
    CUDA Toolkit             present (不足以免除以上阻塞)
```

两个 patch 本身已核对为**修复原生 reader 丢帧**的补丁
（`NVEncCore/NVEncPipeline.h` 的 leading-picture 释放条件、
`QSVPipeline/qsv_pipeline_ctrl.h` 的 pts 拒绝逻辑），补丁文件与
`toolchain-provenance.json` 的 sha256 一致；缺的是**编译产物**，不是补丁。

后果，按当前生效架构逐条写明：

* `nvenc` / `qsv` 的**原生 `--avhw` 路径 = BLOCKED**。产品运行时该路径解析为
  `not_proven` 并出声退到同一编码器的 `--avsw`（`docs/design/architecture.md` §8）。
* `encoders/hwdecode.py::resolve_decode_tool` 的防误用闸门**正常工作**：
  `tools/NVEncC_9.31_x64/NVEncC64.exe`（stock）被 `decode_tool_is_stock` 识别，
  绝不被当作 `--avhw` 的承担者。
* `x265` / `svtav1` 的 **FFmpeg 原生硬解路径不受影响**，实测生效。
* **本目录的矩阵结果不得被引用为"硬件解码已验证"**，直到 patched 构建重建且
  provenance 通过。

未重建的原因不是缺步骤文档，而是缺**编译器与依赖**；本轮未下载任何其它版本
二进制来冒充已验证构建（见 `docs/hardware-codec-policy-redesign` 系列决策）。

| 我想… | 去哪里 |
|---|---|
| **看这次 integration 到底测了什么、成功条件是什么** | **[`integration-test-matrix.md`](integration-test-matrix.md)** ★ 主交付物 |
| **看最终结论与判定** | [`final-report.md`](final-report.md) |
| **查 binary/补丁的权威身份** | [`toolchain-provenance.json`](toolchain-provenance.json) |
| **读两个补丁本身** | [`patches/`](patches/) |
| 看 research 阶段的根因与结论 | [`../../olddocs/docs/hardware-decode/research-conclusion.md`](../../olddocs/docs/hardware-decode/research-conclusion.md) |
| 看当前生效的解码架构 | [`../design/architecture.md`](../design/architecture.md) §8 |

## 怎么跑

```powershell
cd F:\1KeyTranscoder

# 1. 先确认工具链身份（任何不符 = FAIL，不是 warning）
python -m tests.hwdecode.harness provenance

# 2. 确认文档与矩阵没有漂移
python -m tests.hwdecode.harness check-matrix

# 3. 建/刷新生成的 control fixtures 并盘点语料
python -m tests.hwdecode.inventory

# 4. 按 phase 执行（顺序不可打乱，见矩阵 §L）
python -m tests.hwdecode.harness run --phase 1     # A toolchain + B routing
python -m tests.hwdecode.harness run --phase 2     # C integrity + D temporal
python -m tests.hwdecode.harness run --phase 3     # E preservation + F fallback
python -m tests.hwdecode.harness run --phase 4     # G features + H resume
python -m tests.hwdecode.harness run --phase 5     # I concurrency
python -m tests.hwdecode.harness run --phase 6     # J corpus / long-run + K baseline

# 5. 汇总闸门
python -m tests.hwdecode.harness summary
```

`--deep` 打开各长测试的完整变体（更慢）。`--repeat N` 对 P0 用例重复 N 次。

## 产物

```
work/avhw_integration/            (gitignored)
├── results/results.json          每次运行的完整结果
├── results/results.csv           同上的扁平表
├── results/summary.json          机器可读闸门
├── results/frames_contract.json  --frames N 的 contract 表（HD-D07/D08）
├── baseline/                     software golden baseline（HD-K01/K02）
├── fixtures/                     生成的 control fixtures
├── logs/                         每次编码的原始工具日志（判定证据）
└── runs/                         编码产物
```

## 设计上必须知道的三件事

1. **`exit code = 0` 不是正确性证据。** 已证，且本轮在本机复现：stock `--avhw`
   在 Sony XAVC HS 4:2:0 10-bit（源 30 帧）上输出 **27 帧**、`rc = 0`、
   无任何报错、文件完全可播放；4:2:2 10-bit 同为 27/30。`--avsw` 两种都给出 30 帧。
2. **reader 身份必须从工具日志读，不能从命令行推断。**
   QSVEncC 会在硬件不可用时**静默构造 avsw**。把这种情况记成 hardware pass
   等于把整个特性做成假的（HD-A03 / HD-B10）。
3. **默认路径必须解析到 shipped build，硬件解码必须解析到 patched build。**
   两个 build 只在行为上不同，误换后要到丢帧才被发现（HD-A09 / HD-B10）。

## 边界声明（引用时必须带上）

* **矩阵状态 BLOCKED**（见本文开头）：`tools/avhw/` 缺失，provenance 不通过，
  83 例不可运行，**不得记为 PASS**。
* QSVEncC 补丁：**Runtime-proven on QSVEncC 8.26 pinned revision;
  not yet a general claim for later releases.**（8.27–8.30 未检验）
* NVEncC 补丁：仅在 9.31（`2cb9d810`）验证。
* 两个 patched binary 都是 **research build，不得分发**；
  `release/build_release.py` 不把 `tools/avhw/` 入包，`docs/` 也排除在包外。
  因此**在发布安装中原生 `--avhw` 会以 `not_proven` 降级到同一编码器的
  `--avsw`**——这是设计行为，不是缺陷。
* `FramePosList::setPocAndFix` 不在本 session 范围内。
* 本目录只覆盖 **rigaya 原生 `--avhw`** 路径。`x265`/`svtav1` 的 FFmpeg 原生硬解
  是**另一条独立路径**，不在本矩阵范围内：见 `docs/design/architecture.md` §8
  与 `tests/selftest/suites/native_decode.py`。
