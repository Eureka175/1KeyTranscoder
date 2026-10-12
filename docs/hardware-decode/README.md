# hardware-decode — integration session

> **本目录 ≠ 归档区。** 归档的 research 文档在
> [`../../olddocs/docs/hardware-decode/`](../../olddocs/docs/hardware-decode/)。
> 本目录是 **integration session 的产出**：测试矩阵、机器可读矩阵、provenance 记录
> 与最终报告。

## 当前状态：生产路径可用；仅**研究**用例 BLOCKED

现行解码策略是**来源感知**的，两条路径的 Sony 处置**相反且必须相反**（见
[`../design/architecture.md`](../design/architecture.md) §8）：

```text
x265 / svtav1   source -> FFmpeg NVDEC/QSV -> hwdownload -> FFmpeg 编码器
                Sony 与 非 Sony 一律**优先硬解**；能力不允许时才回退，
                回退理由是实测能力理由。"Sony 必须软解"**不适用于这条路径**：
                rigaya reader 的丢帧没有被证明存在于 FFmpeg 的 NVDEC/QSV。

nvenc / qsv     Sony      -> 原生 --avsw（编码前决定，预防性，绝不尝试 --avhw）
                非 Sony   -> 原生 --avhw + 完整性闸门（失败则删产物 + 一次 --avsw 重试）
                未分类    -> 原生 --avhw，记录为 source_kind=unknown
```

Sony 分类只作用于 rigaya 原生 reader：`encoders/hwdecode_ffmpeg.py` 不导入
`is_sony_source`，`plan_decode()` 也不接收来源类别，所以这条规则在结构上传不
过去（`HD-B11` 有 AST 级断言）。

**生产不使用 `tools/avhw/` 下的 patched 研究构建**，也不要求 sha256 匹配才启用
原生硬解。`encoders.hwdecode.resolve_decode_tool()` 仍然存在且仍按 sha256 校验，
但只服务于研究线与本目录的历史矩阵。

因此本矩阵现在报**两道闸门**（`python -m tests.hwdecode.harness summary`）：

```text
production  在已部署二进制上运行的用例                        <- 可运行
research    研究对象就是 patched 构建本身                     <- BLOCKED
            HD-A01/A02/A05/A06/A08（身份与 provenance）
            HD-C01/C02/C07/C08（reader 完整性行为）
            HD-D09（Sony 上 avhw vs avsw 的 trim 语义）
            HD-J05（长片段 reader 行为）
```

```text
python -m tests.hwdecode.harness provenance
    -> ok=False, 2 failure(s)   (NVEncC / QSVEncC patched 构建缺失)
    research 用例如实记为 BLOCKED，绝不记为 PASS。
```

**注意**：本机 `testsets/` 的目录名与矩阵期望的 research 语料不同，
`production` 闸门内大量用例因此 **SKIP**（`summary` 会打印 skip 原因，
不会让绿色计数掩盖"从未运行"）。来源感知策略本身由
`tests/selftest/suites/native_decode.py`（83 例，不需要 fixture）与真实素材
端到端运行覆盖，见 [`final-report.md`](final-report.md) 的"现行策略下的实测"。

```text
重建前提（本机核查，未变）：
    cl / msbuild (MSVC)      NOT FOUND       <- 硬阻塞
    Visual Studio            NOT FOUND       <- 硬阻塞
    Intel oneVPL / vcpkg     NOT FOUND       <- QSV 硬阻塞
    rigaya 源码树             NOT FOUND       <- 硬阻塞
    CUDA Toolkit             present (不足以免除以上阻塞)
```

两个 patch 本身已核对为**修复原生 reader 丢帧**的补丁，且与
`toolchain-provenance.json` 记录的 sha256 一致；缺的是**编译产物**，不是补丁。
本轮未下载任何其它版本二进制来冒充已验证构建。

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
   等于把整个特性做成假的（HD-A03 / HD-B10）。本轮实测又遇到一次同类拒绝：
   QSV `--avhw` 在 6000×4000 H.264 上直接起不来
   （`MFXDEC: Failed to get required buffer size … undeveloped feature`，
   `rc=1`，读者未被构造），而 NVENC `--avhw` 同一素材逐帧精确（402/402）。
3. **默认路径必须解析到 shipped build。** 两个 build 只在行为上不同，误换后要到
   丢帧才被发现（HD-A09）。注意 HD-B10 的旧断言（"policy auto 必须钉住
   patched build"）**已被取代**：生产按来源分流，`--avhw` 跑的就是已部署二进制；
   该用例现在断言的是这一点，加上研究版解析器仍然拒绝缺失/哈希不符的构建。

## 边界声明（引用时必须带上）

* **矩阵分两道闸门**（见本文开头）：production 76 例在已部署二进制上可运行；
  research 7 例（patched 构建身份/provenance 与 stock-vs-patched 缺陷对照）
  因 `tools/avhw/` 缺失而 **BLOCKED**，**不得记为 PASS，也不得据此说"生产
  硬解未经验证"**——生产路径的验证在 production 闸门与
  `tests/selftest/suites/native_decode.py`。
* QSVEncC 补丁：**Runtime-proven on QSVEncC 8.26 pinned revision;
  not yet a general claim for later releases.**（8.27–8.30 未检验）
* NVEncC 补丁：仅在 9.31（`2cb9d810`）验证。
* 两个 patched binary 都是 **research build，不得分发**；
  `release/build_release.py` 不把 `tools/avhw/` 入包，`docs/` 也排除在包外。
  生产路径**不依赖**它们：`--avhw` 使用已部署二进制，Sony 由来源规则改为软解。
* 本目录的 7 例 research 用例覆盖的是 **patched 构建**，不是生产路径。生产用
  shipped 构建的 `--avhw` 行为由 production 闸门覆盖；两者不可互相替代引用。
* `FramePosList::setPocAndFix` 不在本 session 范围内。
* 本目录只覆盖 **rigaya 原生读端**路径。`x265`/`svtav1` 的 FFmpeg 原生硬解
  是**另一条独立路径**，不在本矩阵范围内：见 `docs/design/architecture.md` §8
  与 `tests/selftest/suites/native_decode.py`。
