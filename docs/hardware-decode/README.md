# hardware-decode — integration session

> **本目录 ≠ 归档区。** 归档的 research 文档在
> [`../../olddocs/docs/hardware-decode/`](../../olddocs/docs/hardware-decode/)。
> 本目录是 **integration session 的产出**：测试矩阵、机器可读矩阵、provenance 记录
> 与最终报告。

| 我想… | 去哪里 |
|---|---|
| **看这次 integration 到底测了什么、成功条件是什么** | **[`integration-test-matrix.md`](integration-test-matrix.md)** ★ 主交付物 |
| **看最终结论与判定** | [`final-report.md`](final-report.md) |
| **查 binary/补丁的权威身份** | [`toolchain-provenance.json`](toolchain-provenance.json) |
| **读两个补丁本身** | [`patches/`](patches/) |
| 看 research 阶段的根因与结论 | [`../../olddocs/docs/hardware-decode/research-conclusion.md`](../../olddocs/docs/hardware-decode/research-conclusion.md) |

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

1. **`exit code = 0` 不是正确性证据。** 已证：stock `--avhw` 在 Sony 上丢 3 帧、
   `rc = 0`、无任何报错、文件完全可播放。
2. **reader 身份必须从工具日志读，不能从命令行推断。**
   QSVEncC 会在硬件不可用时**静默构造 avsw**。把这种情况记成 hardware pass
   等于把整个特性做成假的（HD-A03 / HD-B10）。
3. **默认路径必须解析到 shipped build，硬件解码必须解析到 patched build。**
   两个 build 只在行为上不同，误换后要到丢帧才被发现（HD-A09 / HD-B10）。

## 边界声明（引用时必须带上）

* QSVEncC 补丁：**Runtime-proven on QSVEncC 8.26 pinned revision;
  not yet a general claim for later releases.**（8.27–8.30 未检验）
* NVEncC 补丁：仅在 9.31（`2cb9d810`）验证。
* 两个 patched binary 都是 **research build，不得分发**；
  `release/build_release.py` 不把 `tools/avhw/` 入包，`docs/` 也排除在包外。
  因此**在发布安装中 `--hw-decode auto` 会以 `not_proven` 降级到软解，
  `require` 会明确失败**——这是设计行为，不是缺陷。
* `FramePosList::setPocAndFix` 不在本 session 范围内。
