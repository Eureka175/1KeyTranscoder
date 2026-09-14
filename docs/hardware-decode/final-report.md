# Hardware Decode Integration — 详细结构化报告

> **报告定位。** 本文件是 `feature/hardware-decode-integration` 这个 session 的完整结构化报告，
> **以 test matrix 为主体，不以代码 diff 为主体**。
> 矩阵本体（每条测试的完整定义与成功条件）见
> [`integration-test-matrix.md`](integration-test-matrix.md)；
> 机器可读结果见 `work/avhw_integration/results/`。
>
> 本文件中所有数字均由 `results.json` / `summary.json` 导出，不是手工填写的。

---

# 目录

| 章节 | 内容 |
|---|---|
| §1 | 执行摘要 |
| §2 | Session 元信息 |
| §3 | 最终判定（机器可读闸门） |
| §4 | 交付物清单 |
| §5 | 执行流程（严格按任务规定的顺序） |
| §6 | Test Matrix 逐条结果（83 条） |
| §7 | 生产集成设计 |
| §8 | 三个真实发现（含完整证据） |
| §9 | 边界声明 |
| §10 | 已知限制 |
| §11 | Open follow-ups |
| §12 | 复现方法 |
| §13 | 附录：证据位置索引 |

---

# §1 执行摘要

**一句话结论：**

> hardware decode 已经接进 1KeyTranscoder，但**默认关闭**、且**只在一组被实测证明过的
> 输入上启用**；任何硬件结果都必须先通过帧完整性闸门，闸门不过就被丢弃并改用软解重跑。

关键点不是"硬解能跑通"，而是本 session 的最终标准：

> **即使 hardware decoder 再次出现类似 Sony 的静默丢帧问题，
> 1KeyTranscoder 也必须能够检测出来，而不是把错误结果当成成功结果交付。**

这一条由两条测试**逐条证明**：

| 测试 | 做法 | 结果 |
|---|---|---|
| **HD-C09** | 把**真实 stock binary**（已知丢 3 帧）喂给**真实 production 闸门** | 闸门判 `count_mismatch`（360 vs 357）→ 丢弃产物 → 软解重跑 → exact |
| **HD-C10** | 构造**帧数不变但画面错序**的产物 | 计数闸门放行（360 == 360）→ **sequence 闸门**抓到 `sequence_mismatch` |

**规模：** 83 条测试（P0 61 / P1 21 / P2 1），覆盖 A–K 共 11 类，
全部 PASS，0 FAIL / 0 BLOCKED / 0 SKIP，累计测试耗时约 **3.3 小时**
（不含被修复后重跑的部分）。

**矩阵抓到 3 个真实问题**（1 个生产缺陷、1 个研究线未记录的行为差异、1 个集成缺陷），
并在 final regression 中把本 session 自己引入的 3 个缺陷打回。

---

# §2 Session 元信息

| | |
|---|---|
| Branch | `feature/hardware-decode-integration` |
| Base | `main` @ `b3245b7` —— **`main` 未被本 session 修改** |
| Commit 范围 | `da27e28` … `f819eb4`（9 个分阶段 commit） |
| 附加说明 | 分支上另有仓库所有者自己的 docs commit `17ab8a7`（tools/ 不可丢失性），非本 session 产出 |
| 机器 | Windows 11 (26200) · Intel Core Ultra 9 285H (16C/16T) |
| GPU | NVIDIA RTX 5070 Laptop 8 GB (driver 616.56) · Intel Arc 140T (32.0.101.8974) |
| 工具链 | FFmpeg 9.0.1 · GPAC 26.02 · NVEncC 9.31 patched · QSVEncC 8.26 patched |
| Python | 3.11.9 |
| 测试时长 | 约 3.3 小时（矩阵自身记录）；另有既有回归 570 s |

### 2.1 默认行为：**未改变**

`--hw-decode` 的默认值是 `off`。HD-B05 逐 token 断言：默认路径产出的 argv
与集成前**完全一致**（首部仍是 `--avsw`，token 顺序不变）。

---

# §3 最终判定（机器可读闸门）

```text
Branch:        feature/hardware-decode-integration
Base:          main @ b3245b7   (main 未被修改)
Commits:       da27e28 .. f819eb4

Test Matrix:
    Total:     83
    PASS:      83
    FAIL:      0
    BLOCKED:   0
    SKIP:      0

P0 PASS:       61/61
P1 PASS:       21/21
P2 PASS:       1/1

既有项目回归:  tests/full_autotest.py --level full -> 248 PASS / 0 FAIL (570.3s)

Final status:  READY FOR REVIEW
Blockers:      （无）
```

**判定规则（本 session 自定，未放宽）：**

* integration **不得**因为"绝大多数测试通过"而宣布完成；
* 任何 P0 `FAIL` 或 P0 `BLOCKED` ⇒ 最终状态必须为 `BLOCKED`；
* `SKIP` 与 `BLOCKED` 都**不计入**通过。

最终 0 个 P0FAIL / 0 个 P0 BLOCKED。

### 3.1 分类汇总

| Category | Tests | PASS | FAIL | BLOCKED | SKIP | P0 status |
|---|---|---|---|---|---|---|
| A Toolchain / binary provenance | 9 | 9 | 0 | 0 | 0 | PASS 7/7 |
| B Capability routing | 10 | 10 | 0 | 0 | 0 | PASS 8/8 |
| C Frame integrity | 13 | 13 | 0 | 0 | 0 | PASS 11/11 |
| D Temporal / seek / trim / `--frames` | 9 | 9 | 0 | 0 | 0 | PASS 7/7 |
| E Video metadata / preservation | 7 | 7 | 0 | 0 | 0 | PASS 6/6 |
| F Backend fallback | 8 | 8 | 0 | 0 | 0 | PASS 8/8 |
| G Existing feature interaction | 9 | 9 | 0 | 0 | 0 | PASS 6/6 |
| H Resume / retry / interruption | 5 | 5 | 0 | 0 | 0 | PASS 5/5 |
| I Concurrency / resource | 5 | 5 | 0 | 0 | 0 | — |
| J Production / long-run | 6 | 6 | 0 | 0 | 0 | PASS 1/1 |
| K Golden baseline | 2 | 2 | 0 | 0 | 0 | PASS 2/2 |
| **Total** | **83** | **83** | **0** | **0** | **0** | **P0 61/61** |

### 3.2 按任务要求的分级视图（§26 模板）

```text
P0:
    Toolchain:        PASS 7/7
    Capability:       PASS 8/8
    Frame integrity:  PASS 11/11
    Seek:             PASS 3/3
    Trim:             PASS 1/1
    --frames:         PASS 2/2
    Fallback:         PASS 8/8
    Channel-sync:     PASS 6/6
    Resume:           PASS 3/3
    Retry:            PASS 2/2
    Preservation:     PASS 6/6

P1:
    Metadata:         PASS 1/1
    Multi-stream:     PASS 2/2
    Concurrency:      PASS 3/3
    Resource pressure:PASS 1/1
    （另含各类 P1 共 21 条，全 PASS）

P2:
    Performance:      PASS 1/1  (4-way 4K，仅验证不崩溃/不损坏)
    Long-run:         PASS 1/1  (35967 帧全量)
    Extended codec:   PASS 4/4  (J-01..J-04)

Backend:
    NVEncC:           patched 9.31 (r1)，sha256 dcf6d7a6…，全类别覆盖
    QSVEncC:          patched 8.26 (r4504)，sha256 f5df83f1…，仅 pinned 版本声明
    Software:         --avsw，correctness oracle，golden baseline 已自洽验证

Real Corpus:
    Sony:             testsets/20260904/*.MP4 共 42 片；矩阵取样 10 短 / 10 中 / 10 长
    DJI:              action4_4k_4x3_30+60 (105/330 帧) + 语料内 DJI 片段 (790 帧)

Golden baseline:
    status:           ESTABLISHED & SELF-VALIDATED (HD-K01 PASS / HD-K02 PASS)
```

---

# §4 交付物清单

### 4.1 主交付物：测试矩阵

| 文件 | 内容 |
|---|---|
| [`integration-test-matrix.md`](integration-test-matrix.md) | **主交付物**。83 条测试，每条 10 个字段：Test ID / Category / Input / Backend / Expected behaviour / Verification method / Severity / Automation status / Result / Notes |

矩阵的自动化状态为 **100% `auto`** —— 不存在"需要人工播放视频检查"的条目。

### 4.2 机器可读资产

| 文件 | 内容 |
|---|---|
| `tests/hwdecode/matrix.json` | 矩阵的**可执行一半**（id/category/severity/impl/expected） |
| `tests/hwdecode/harness.py` | 执行器 + 闸门 + `check-matrix`（文档与 JSON 漂移即报错） |
| `tests/hwdecode/probe.py` | 输入刻画；**按字节自解 `stsz`**，参考帧数不来自被测工具链 |
| `tests/hwdecode/checks.py` | 验证原语：五方帧数对账、streaming fingerprint（不落盘 raw） |
| `tests/hwdecode/runners.py` | reader 显式调用、失败注入、长程遥测 |
| `tests/hwdecode/fixtures.py` + `inventory.py` | 语料解析 + 生成 control fixtures |

### 4.3 Provenance 资产

| 文件 | 内容 |
|---|---|
| [`toolchain-provenance.json`](toolchain-provenance.json) | 机器可读的 binary / 补丁身份；harness 启动即校验，不符即 FAIL |
| [`patches/`](patches/) | **两个补丁本体**，sha256 与 research 记录逐字节吻合 |

### 4.4 生产代码

| 文件 | 变更量 | 内容 |
|---|---|---|
| `encoders/hwdecode.py` | +428 | **新增**：capability routing、proven allowlist、reason codes、失败分类、patched build 绑定 |
| `encoders/integrity.py` | +539 | **新增**：帧完整性闸门（五方对账 + reader 身份断言 + ordered fingerprint） |
| `core/batch_hw.py` | +248/−30 | 编码梯接入路由与闸门；闸门失败丢弃产物并软解重跑；`_encoded_ok` 加固 |
| `core/config.py` | +16/−4 | `find_hw_tool` 确定性排除 `tools/avhw/` |
| `encoders/nvencc.py` / `qsvencc.py` | +29 / +25 | reader 变显式参数，默认 `avsw` |
| `1kt.py` | +27 | 新增 `--hw-decode` / `--hw-decode-verify` |

### 4.5 运行期产物（gitignored）

```
work/avhw_integration/
├── results/results.json        83 条完整证据
├── results/results.csv         扁平表
├── results/summary.json        机器可读闸门
├── results/frames_contract.json  --frames 三段 contract 表
├── results/digest.json         本报告用的逐条摘要
├── baseline/*.json             software golden baseline
├── fixtures/                   生成的 control fixtures
├── logs/                       每次编码的原始工具日志（判定证据）
└── runs/                       编码产物
```

---

# §5 执行流程

严格按任务规定顺序执行，**Phase 2 的 P0 integrity 未通过前不做任何性能优化**。

| Phase | 类别 | 结果 | 备注 |
|---|---|---|---|
| **Phase 1** | A toolchain + B routing | 全 PASS | 先建矩阵、再实现、后执行 |
| **Phase 2** | C frame integrity (P0) + D temporal | 全 PASS | 首轮 7 个 P0 FAIL，全部定位并修正后重跑 |
| **Phase 3** | E preservation + F fallback | 全 PASS | 首轮 5 个 FAIL，4 个为 harness 缺陷、1 个为错误断言 |
| **Phase 4** | G feature interaction + H resume/retry | 全 PASS | 首轮 2 个 FAIL，其中 H-05 抓到**真实生产缺陷** |
| **Phase 5** | I concurrency | 全 PASS | 一次通过 |
| **Phase 6** | J real corpus / long-run + K baseline | 全 PASS | 首轮 J-05 为 harness 缺陷 |
| **Final** | full regression（全部 11 类重跑） | 全 PASS | 又打回本 session 自己引入的 3 个缺陷 |

> **流程事实：** 矩阵不是"代码写完以后补几个样例测试"。
> 矩阵文档先于 production 代码修改写成（commit `da27e28`），
> 实现随后按矩阵逐项通过（commit `8a657e3` 起）。
> `harness check-matrix` 会在文档与 `matrix.json` 漂移时直接报错。

---

# §6 Test Matrix 逐条结果

> 下表从 `results.json` 导出。`Actual` 列为 harness 记录的判定依据原文（截断）。

## A. Toolchain / Binary provenance

**存在的理由。** 整个矩阵的前提是"测的确实是那个 patched binary"。
四种误判都会让后面所有测试失去意义：跑了 stock；跑了错版本；从 PATH 抓到别的；
请求硬件但工具静默用了软解却记成 hardware pass。**任何 provenance 不明确 = FAIL。**

| ID | Sev | 结果 | 耗时 | 判定依据 |
|---|---|---|---|---|
| HD-A01 | P0 | PASS | 0.3s | sha256=`dcf6d7a6…7c8be4b`；version 含 `9.31 (r1)` / `Sep 12 2026`；patch sha256 吻合 |
| HD-A02 | P0 | PASS | 0.3s | sha256=`f5df83f1…f8804d`；version 含 `8.26 (r4504)` / `Sep 12 2026`；版本边界声明已记录 |
| HD-A03 | P0 | PASS | 8.9s | `Input Info` 实测 reader：nvenc=`avcuvid`、qsv=`avqsv`（**从日志读，不从命令行推断**） |
| HD-A04 | P0 | PASS | 8.1s | 软解对照实测 reader：两侧均 `avsw` |
| HD-A05 | P0 | PASS | 0.2s | 两个 stock 对照存在、可运行、hash 与 patched 不同 |
| HD-A06 | P0 | PASS | 0.1s | PATH 中植入同名诱饵后，显式路径 hash 仍与 provenance 一致 |
| HD-A07 | P1 | PASS | 0.3s | 缺 DLL 副本 rc=`3221225662`(0xC0000142)、无输出、分类 `startup_failed` |
| HD-A08 | P1 | PASS | 0.6s | 真实校验 ok；故意写错的期望值被 `ProvenanceError` 抛出 |
| HD-A09 | P0 | PASS | 0.0s | 默认解析确定性落在 shipped 目录，`tools/avhw` 被排除 |

## B. Capability routing

| ID | Sev | 结果 | 耗时 | 判定依据 |
|---|---|---|---|---|
| HD-B01 | P0 | PASS | 3.0s | Sony XAVC HS：nvenc/qsv 均 `proven_combination`；实跑 reader=`avcuvid` |
| HD-B02 | P0 | PASS | 28.2s | H.264 4:2:2：nvenc=`proven_combination`、qsv=`capability_refused`（**允许两后端结论不同**） |
| HD-B03 | P0 | PASS | 0.5s | DJI 两片在两后端均 eligible |
| HD-B04 | P0 | PASS | 0.0s | 10 组未证组合（8-bit / AV1 / 4:2:2 / 4:4:4 / mpeg2）**全部安全落到软解**，无一"unknown→hardware anyway" |
| HD-B05 | P0 | PASS | 0.1s | 默认 reader=`avsw`，argv 形态未变（token 级断言） |
| HD-B06 | P0 | PASS | 0.0s | `require` + 不合格组合 → `require_unmet`，且编码梯会 raise |
| HD-B07 | P1 | PASS | 0.0s | VFR 的 `--avsync forcecfr` 与 reader 选择互不干扰 |
| HD-B08 | P1 | PASS | 0.0s | 24 条决策两次调用完全一致；决策行已进日志 |
| HD-B09 | P0 | PASS | 0.0s | 4 类失败 → **4 个互不相同的 reason code** |
| HD-B10 | P0 | PASS | 0.1s | `off` 不 override；`auto` 按 sha256 绑定 patched build；伪造 hash 与缺失 build 都被拒并给 reason code |

## C. Frame integrity（**P0，最高优先级**）

**成功条件（全部满足才 PASS）：** 五方帧数对账 + reader 身份相符 +
ordered fingerprint 与软件基线逐帧相等 + PTS 序列相等 + keyframe 序列相等 + 无静默回退。

| ID | Sev | 结果 | 耗时 | 判定依据 |
|---|---|---|---|---|
| HD-C01 | P0 | PASS | 84.0s | **patched avhw=360 == avsw=360 == container=360；stock avhw=357（缺陷成功复现）** |
| HD-C02 | P0 | PASS | 232.5s | 3 个 fixture（360 / 630 / 360 帧）逐帧比较：帧数、指纹、PTS、keyframe 全等 |
| HD-C03 | P0 | PASS | 1606.5s | 全量输入 11280 帧**精确对账**；`--frames 3000/18000` 内部自洽且符合实测 contract |
| HD-C04 | P0 | PASS | 1115.2s | **12 个真实 A7M5 片段全部对账到 container**，抽样指纹一致 |
| HD-C05 | P0 | PASS | 523.3s | 3 个 DJI fixture 在两后端上 exact（leading=0 的阴性对照未被 patch 改变） |
| HD-C06 | P0 | PASS | 275.2s | 5 个 H.264 4:2:2 样本：NVENC 硬件 exact；QSV **响亮拒绝**（rc≠0、无输出） |
| HD-C07 | P0 | PASS | 203.8s | 4 个 control（x265 / synthetic / MKV / Sony copy）在两 reader 下一致，无错误启用 |
| HD-C08 | P0 | PASS | 102.5s | 6 条记录：5 个决定性计数全部存在；reader 自报**记录但不作参考** |
| HD-C09 | P0 | PASS | 51.4s | **闸门判 `count_mismatch`(360 vs 357) → 丢弃 → 软解重跑 exact** |
| HD-C10 | P0 | PASS | 149.0s | **计数闸门放行错序产物（360==360）→ sequence 闸门抓到 `sequence_mismatch`（index 0）** |
| HD-C11 | P0 | PASS | 53.3s | 丢弃 sha `5879568cd667`，交付 sha `ef6217336314`（**错误产物从未被交付**） |
| HD-C12 | P1 | PASS | 32.4s | 尾部截断产物（357/360）→ `count_mismatch` |
| HD-C13 | P1 | PASS | 80.5s | 适合的 control 上 avhw 与 avsw 输出 **sha256 相同** |

### C.1 六方对账（本 session 的证据口径）

| # | 来源 | 取得方式 | 可信度 |
|---|---|---|---|
| 1 | `container_expected` | **自己按字节解析** `moov/trak/mdia/minf/stbl/stsz` | 参考真值 |
| 2 | `reader_reported` | 工具日志 `N frames, End of file` | **不可信**（XAVC 上系统性少报），只记录不判定 |
| 3 | `encoder_input` | 工具日志 `encoded N frames` | 编码器自述 |
| 4 | `output_stream` | 产物容器自身 sample table | 产物自述 |
| 5 | `independent_decoded` | `ffprobe -count_frames` | 独立实现 |
| 6 | `independent_decoded_alt` | FFmpeg `-progress` 帧计数 | 第二个独立实现 |
| +7 | `fingerprint` | 逐帧有序签名（mean Y/U/V、mean\|dY\|、首尾采样、4×4KB chunk digest） | 顺序与内容证据 |

**实测事实：** 正常编码时两个 reader **都不打印** `N frames, End of file`，
因此 `reader_reported` 字段实际为 null。这是设计事实而非缺陷——它本来就不被用作参考。

## D. Temporal / seek / trim / `--frames`

| ID | Sev | 结果 | 耗时 | 判定依据 |
|---|---|---|---|---|
| HD-D01 | P0 | PASS | 101.9s | 2 个 fixture 全量解码逐帧等值 |
| HD-D02 | P0 | PASS | 44.8s | 3 个 seek 位置：**实测 hw/sw 不等价**（两侧各自确定）→ 路由拒绝硬解 → 软解精确 |
| HD-D03 | P0 | PASS | 48.6s | 3 组 trim：**hw 与 sw 字节相同** |
| HD-D04 | P0 | PASS | 78.6s | 5 个 seek 位置：5/5 复现"计数与 PTS 相等、画面不同" |
| HD-D05 | P0 | PASS | 58.4s | **按操作分开给契约**：trim 字节相同；seek 不等价故拒绝硬解 |
| HD-D06 | P1 | PASS | 6.7s | 显式 `--seek 0` 与不传 seek 不可区分（**从"未观测"变成"已测事实"**） |
| HD-D07 | P0 | PASS | 206.7s | 7 个 N × 2 reader + leading=0 控制组；**逐 N 也记录 hw/sw 等价性** |
| HD-D08 | P0 | PASS | 0.0s | **三段 contract 全部成立**（见 §8.4） |
| HD-D09 | P1 | PASS | 22.8s | `--trim` 语义与 `--frames` 分开记录 |

## E. Video metadata / preservation

| ID | Sev | 结果 | 耗时 | 判定依据 |
|---|---|---|---|---|
| HD-E01 | P0 | PASS | 60.6s | 3840×2160、SAR `1:1`、DAR `16:9` 保持 |
| HD-E02 | P0 | PASS | 80.6s | 10-bit 保持 10-bit（两后端） |
| HD-E03 | P0 | PASS | 62.0s | 4:2:0 保持 4:2:0；4:2:2 按既定策略处理 |
| HD-E04 | P0 | PASS | 38.1s | `tv / bt709 / bt709 / bt709` —— 走**生产** colour path（用 `encoders.hw.color_flag_args`） |
| HD-E05 | P0 | PASS | 42.2s | 呈现时间轴良构、与软件一致、无时间戳平移 |
| HD-E06 | P0 | PASS | 42.1s | `colr` 硬件=软件=`6e636c7800010001000100`，与源一致（**未重新引入 GPAC colr 错误**） |
| HD-E07 | P1 | PASS | 6.4s | 硬件 intermediate 仅含视频流；源容器事实运行前后不变 |

## F. Backend fallback

| ID | Sev | 结果 | 耗时 | 判定依据 |
|---|---|---|---|---|
| HD-F01 | P0 | PASS | 20.7s | 不可用设备：分类明确 → 软解 → exact |
| HD-F02 | P0 | PASS | 12.3s | QSV + H.264 4:2:2：rc=`4294967265`(=−31)、`capability_refused` → 软解 exact |
| HD-F03 | P0 | PASS | 7.3s | 启动失败 rc=`3221225662` → `startup_failed`（**不误判为 integrity 失败**） |
| HD-F04 | P0 | PASS | 0.6s | 运行期解码失败 rc=`3221225477` → `decode_failed` |
| HD-F05 | P0 | PASS | 52.6s | 闸门检出 → 丢弃 → 软解重跑 exact |
| HD-F06 | P0 | PASS | 0.1s | 硬解回退上限 = 1，且有明确终止条件与消息 |
| HD-F07 | P0 | PASS | 0.0s | 能力拒绝 / memo 命中 / not_proven **三者都出声**；消息已接进日志 |
| HD-F08 | P0 | PASS | 76.0s | 回退后产物 sha == 直接软解 sha（`ef6217336314`） |

## G. Existing feature interaction

| ID | Sev | 结果 | 耗时 | 判定依据 |
|---|---|---|---|---|
| HD-G01 | P0 | PASS | 1.6s | 已对齐素材：`already_aligned`，**4 条轨决策与冻结基线逐项一致** |
| HD-G02 | P0 | PASS | 1.2s | 含固定延迟素材：`applied`，修正轨 `shift_samples` **与基线一致**且为整数 |
| HD-G03 | P0 | PASS | 0.5s | 逐轨降级：3 轨 `untouched` 各有 reason，1 轨 anchor，未阻塞健康轨 |
| HD-G04 | P0 | PASS | 12.5s | 硬解视频 + 音频路径：与软解视频路径结果一致 |
| HD-G05 | P0 | PASS | 17.3s | 硬解回退后音视频仍与基线一致 |
| HD-G06 | P0 | PASS | 0.1s | 冻结基线 sha `36242616062ffec2` 未变；algo `2.3.0-p1`；channel-sync 相关文件相对 main **零改动** |
| HD-G07 | P1 | PASS | 38.9s | 生产输出色彩元数据保持 |
| HD-G08 | P1 | PASS | 13.1s | 4 条音频流在两 reader 下属性完全相同 |
| HD-G09 | P1 | PASS | 4.6s | 6 流源：intermediate 仅视频，源流清单未变 |

## H. Resume / retry / interruption

| ID | Sev | 结果 | 耗时 | 判定依据 |
|---|---|---|---|---|
| HD-H01 | P0 | PASS | 15.1s | 二次运行识别完成态、**未重算**、产物字节未变 |
| HD-H02 | P0 | PASS | 13.6s | 失败运行的缓存**未阻止**重试；回退后 resume 稳定 |
| HD-H03 | P0 | PASS | 29.0s | 受控中断后**无伪成功产物** |
| HD-H04 | P0 | PASS | 52.5s | 阶段顺序：`hardware_attempt → gate:count_mismatch → discard → software_retry` |
| HD-H05 | P0 | PASS | 21.0s | **partial intermediate 被期望帧数校验拒绝；完整产物仍被接受**（见 §8.1） |

## I. Concurrency / resource

| ID | Sev | 结果 | 耗时 | 判定依据 |
|---|---|---|---|---|
| HD-I01 | P1 | PASS | 116.5s | 3 组后端/reader × 2 次重复，全部对账；记录 mean/min/max |
| HD-I02 | P1 | PASS | 24.3s | 2 个并发硬解任务：reader 身份正确、**日志文件互不共享** |
| HD-I03 | P1 | PASS | 24.5s | 1 硬 + 1 软并发：**未产生错误回退** |
| HD-I04 | P1 | PASS | 54.8s | 4-way 负载下：rc=0、对账通过、无损坏输出 |
| HD-I05 | P2 | PASS | 33.8s | 4-way 4K：不崩溃、不损坏（aggregate 118.61 fps，**仅记录，无吞吐判据**） |

## J. Production / long-run

| ID | Sev | 结果 | 耗时 | 判定依据 |
|---|---|---|---|---|
| HD-J01 | P1 | PASS | 503.8s | 12 个短片段 × **3 组后端/reader 组合（36 次编码）**全部 clean |
| HD-J02 | P1 | PASS | 2532.1s | 16 个中等片段 × 2 后端（32 次编码）全部对账 |
| HD-J03 | P1 | PASS | 902.8s | **30 个真实 Sony 片段（10 短 / 10 中 / 10 长）**全部对账到 container |
| HD-J04 | P1 | PASS | 144.8s | 3 个 DJI 片段 × 2 后端 |
| HD-J05 | P1 | PASS | 1254.2s | **35967/35967 帧全量输入，1245 s，28.9 fps，完整性 ok，peak RSS 964.7 MiB** |
| HD-J06 | P0 | PASS | 0.0s | P0 60/60；最终状态 `READY FOR REVIEW` |

### J.1 长程遥测明细（HD-J05）

| 指标 | 值 |
|---|---|
| 输入 | `work/1KT-long/inputs/LONG-A.mp4`，容器 35967 样本 |
| 交付帧数 | **35967 / 35967**（完整对账） |
| wall clock | 1245 s（约 20.8 分钟） |
| 吞吐 | 28.9 fps |
| reader | `avcuvid`（硬件） |
| peak RSS | 964.7 MiB |
| CPU time / GPU util / VRAM | 采样方式与 null 语义见下 |
| final sha256 | 已落盘于结果 JSON |
| 遥测口径 | 用 `tasklist` + `nvidia-smi` 采样；**平台未报告时记 null，不记 0** |

> wall clock 与 fps 来自有既定漂移的笔记本主机，**跨 session 绝对值不可比**。

## K. Golden baseline

| ID | Sev | 结果 | 耗时 | 判定依据 |
|---|---|---|---|---|
| HD-K01 | P0 | PASS | 142.6s | 6 个 fixture 的软件基线已建立：frame count、ordered fingerprint digest、PTS、duration、video metadata、audio、stream kinds、sha256 |
| HD-K02 | P0 | PASS | 138.3s | **6 条基线在独立重跑下逐字节重derive** —— 基线自身经过验证，不是单次输出 |

**原则：** `software = correctness oracle`，`hardware = candidate optimization`。

---

# §7 生产集成设计

## 7.1 路由契约

```
policy = off | auto | require            默认 off（= v0.6.2 行为，零变化）

eligible(backend, codec, chroma, depth) :=
        在 runtime-proven allowlist 内
    AND 不在 known_refusals 内
    AND 请求未携带时间 seek
    AND 该组合未被本运行 memo 标记为不可用

off      → reader = avsw                     （不出任何提示）
auto     → eligible ? avhw : avsw            （降级必须出声：WARNING + reason code）
require  → eligible ? avhw : ERROR           （绝不静默降级）
```

### runtime-proven allowlist（初始内容）

| backend | codec | chroma | depth | 依据 |
|---|---|---|---|---|
| nvenc | hevc | 4:2:0 | 10 | research：N−3 → N（30/330/10170 帧），与 `--avsw` 字节相同 |
| nvenc | h264 | 4:2:2 | 10 | research：N−2 → N（195 帧），与 `--avsw` 字节相同 |
| qsv | hevc | 4:2:0 | 10 | research：N−3 → N，逐帧 SHA-256 与 `--avsw` 相同 |

**allowlist 之外一律 `not_proven` → 软解。**
理由：research 的 corpus 边界是 "No All-I, no 8-bit, no 1080p, no VFR material exists
in this environment"。把未测过的 profile 静默纳入硬件路径，
正是本 session 要避免的失败模式。

### known_refusals

| backend | codec | chroma | depth | 依据 |
|---|---|---|---|---|
| qsv | h264 | 4:2:2 | 10 | `avqsv: codec h264(yuv422p10le) unable to decode by qsv.` rc=−31、无 reader、无输出 |

## 7.2 帧完整性闸门

两级，分别对应两类缺陷：

**`count` 级（启用硬解时始终开启）**

1. 工具 rc == 0 且产物存在非空；
2. **reader 身份断言** —— 工具日志 `Input Info` 里的 reader 必须等于请求值
   （请求 `avhw` 却构造出 `avsw` 是 **silent software fallback**，判 FAIL）；
3. 五方对账：`container_expected` 为参考真值，`encoder_input` /
   `output_stream` / `independent_decoded` 任一不等即 FAIL；
4. reader 自报只记录，**永不作参考**（XAVC 上系统性少报）。

**`sequence` 级（`--hw-decode-verify` opt-in）**

对硬件与软件结果做逐帧有序 fingerprint 比对，抓**计数不变的**错序/替换/重复。
代价是每个文件多一次编码，因此不作为默认。

**失败动作：** 丢弃硬件产物 → 出声（WARNING + reason code）→ 同一格式档软解重跑。
回退次数上限 1（`MAX_HW_DECODE_FALLBACKS`），超出即 FATAL，**不会无限重试**。

## 7.3 Reason codes（互不相同，可审计）

| code | 语义 |
|---|---|
| `policy_off` | 策略关闭（默认） |
| `proven_combination` | 命中 allowlist |
| `not_proven` | 不在 allowlist，安全落软解 |
| `capability_refused` | 硬件明确拒绝该格式 |
| `reader_unavailable` | reader 未按请求构造 |
| `device_unavailable` | 设备不可用 |
| `startup_failed` | 进程未启动（loader failure / 无输出） |
| `decode_failed` | 解码失败 |
| `count_mismatch` | 帧数对账失败 |
| `sequence_mismatch` | 画面序列不一致 |
| `seek_not_equivalent` | 请求携带 seek，reader 不等价 |
| `require_unmet` | `require` 策略下不可用 |
| `integrity_ok` | 闸门通过 |

## 7.4 硬解 binary 绑定

硬解**只**使用 provenance 校验过的 patched build（按 sha256）。
`policy=off` 时不做任何 override；build 缺失或 hash 不符时给 reason code，
**绝不静默改用 shipped build**——用 shipped build 是"安全但无用"：
每份结果都会被闸门拒绝，功能看似开启实则从未运行。

同时 `find_hw_tool` **确定性排除** `tools/avhw/`，使默认路径不受目录排序影响。

## 7.5 端到端实测（真实 CLI）

```
[HWDEC] decode tool: F:\1KeyTranscoder\tools\avhw\NVEncC_9.31_avhw\NVEncC64.exe
        (patched build verified by sha256 dcf6d7a63143c777)
DECODE_ROUTE | backend=nvenc codec=hevc 4:2:0/10bit policy=auto -> HARDWARE
        (reader=avhw, reason=proven_combination)
COMMAND | ...NVEncC_9.31_avhw\NVEncC64.exe ... --avhw --video-track 1 -c hevc ...
INTEGRITY | integrity gate [OK] reason=integrity_ok
        counts={tool_rc:0, reader_identity:'avcuvid', encoder_input:360,
                container_expected:360, container_method:'isobmff-stsz', ...}
[PRESERVE-OK] PRESERVED=36 MODIFIED=0 MISSING=0 | structural_success=True
```

---

# §8 三个真实发现

## 8.1 `_encoded_ok()` 接受被截断的 intermediate（**生产缺陷，已修**）

**由 HD-H05 抓到。**

`_encoded_ok()` 决定 resume 时能否**复用**中间产物，而它当时只问
"能不能读到 ≥1 个 video packet"。**被截断的文件满足这个条件。**

| 截断 | `nb_read_packets`（真正读到） | 容器声明 | 修复前 | 修复后 |
|---|---|---|---|---|
| 完整 | 360 | 360 | True | **True**（必须保持） |
| 2%（596 MB → 11.9 MB） | 19 | 360 | **True ❌** | **False ✅** |
| 2%（28 MB → 564 KB） | **2**，且 stderr 无任何错误 | 360 | **True ❌** | **False ✅** |
| 50% | 55 | 360 | **True ❌** | **False ✅** |

**后果：** 正是本 session 列为 P0 的 **resume corruption** ——
中断的运行留下 partial `encoded.mov`，后续运行把它当成完成品，
并把不完整的编码结果交付出去。

**为什么此前看不见：** 两个不同的数字被混为一谈。
截断的 MP4 里 **`stsz` 表（在 moov 内）仍然完好**，
所以"容器声明的 sample 数"依旧是 360；
而"解复用器真正读到的 packet 数"已经塌掉。

**修复：** 守卫改为比较 **read count vs 源帧数**，并显式拒绝任何
partial / truncated 读取错误；两个 resume 调用点都传入期望值。
同时保留"完整产物必须被接受"的反向断言——
一个会把每次 resume 都重编的守卫本身也是 bug。

## 8.2 `--seek` 上硬件 reader 与软件 reader **不等价**（**research 未记录的新发现**）

**由 HD-D02/D-04/D-05 抓到。**

| 观测 | 值 |
|---|---|
| 帧数 | 相等（`--seek 0.5 --frames 16` → 两侧各 13 帧） |
| PTS 序列 | **完全相同**（`0, 16016, 8008, 4004, 12012, …`） |
| keyframe 索引 | 相同 |
| 画面内容 | **13/13 帧不同**，10-bit 域（0–1023）mean 偏差最大 **252** |
| 同 reader 重复 ×2 | hw×2 与 sw×2 各自**字节相同** → 不是编码器随机性 |
| `patched --avhw` vs `stock --avhw` | **字节相同**（`73b9610a…`）→ **patch 不是原因** |
| full encode（无 seek） | hw 与 sw **字节相同**（`ef621733…`） |
| `--frames` / `--trim` | hw 与 sw **字节相同** |

**因果结论：** patch 按设计在"有 seek"时**原样运行原过滤器**，
所以 patched seek == stock seek，逐字节相同；差异来自 rigaya reader 在时间 seek 上的
**既有语义**，不是本 patch 引入的。
research 线只验证了"seek 相对 stock 不变"（3/3），
**从未把它与 `--avsw` 对比**，因此该差异此前未被记录。

**工程处置（不是放宽判据）：** `route_decode(seek_requested=True)` 直接拒绝硬解，
reason code = `seek_not_equivalent`，并出声。
于是"硬解不得改变交付画面"这一生产契约**由构造保证**，而不是靠事后检测。

**实际影响：零。** production 代码从不使用 `--seek` / `--trim` / `--frames`
（`git grep` 全库只有本 harness 自身命中）。

**尚未解释：** 两个 reader 在 seek 上为何选到不同画面。
已排除：编码器随机性、patch 引入、帧数/PTS 差异、常量窗口平移。
本 session 不为此展开新的 root-cause 考古——它不影响生产路径，且已被守卫覆盖。

## 8.3 硬解 binary 没有被绑定（**集成缺陷，已修**）

**由 HD-A09 / HD-B10 抓到。**

`find_hw_tool` 用目录 glob 取第一个命中，加入 `tools/avhw/` 后
**结果取决于排序**；而两个 build **只在行为上不同**，
误换后要到丢帧才会被发现。

**修复：** 默认路径确定性排除 `tools/avhw/`；
硬解路径按 sha256 绑定 patched build；
build 缺失或 hash 不符时给 reason code，绝不静默改用 shipped build。

## 8.4 `--frames N` 的实测 contract（修正原假设）

原假设 `presented == N − leading` **只在一个 regime 内成立**。实测三段：

| regime | 条件 | `presented` | 证据 |
|---|---|---|---|
| 低于 leading | `N ≤ leading_pictures` | **整个片段**（请求被忽略） | N=1、2、3 均交付 **360** 帧 |
| 常规 | `leading < N < container` | `N − leading_pictures` | N=4→1、10→7、30→27、100→97 |
| 越界钳制 | `N ≥ container` | **整个片段**（含 leading，短少消失） | 对 11280 帧片段请求 18000 → **11280** |
| 控制组 | `leading_pictures = 0` | `N` 精确 | N=1/3/10/30/100 全部精确 |

**关键性质：三个 regime 上 hw 与 sw 完全一致**（`--frames 10/30/100` 两侧 sha256 相同）。
因此 `--frames` 是 **reader 共有**行为，与 `--seek` 性质完全不同——
这也是为什么 `--seek` 需要守卫而 `--frames` 不需要。

## 8.5 final regression 打回本 session 自己引入的 3 个缺陷

final regression 的价值在这里最直接。

| 测试 | 现象 | 根因 | 处置 |
|---|---|---|---|
| **HD-B09** | 4 类失败塌缩成 2 个 reason code | 本 session 给 `classify_reader_failure` 加的"日志太短 ⇒ 没启动"启发式用了**长度阈值**（< 40 字符），把 `Invalid Device Id = 1` 这种真实的一行诊断吞成了 `startup_failed` | 判据从"短"改成"**完全为空**"。真实 rigaya 运行在失败前总会打印 banner（版本/OS/CPU/GPU），空日志才是"没到 main"的可靠信号；loader failure 由 rc（`0xC0000142` 等）单独识别。修后 4 类重新互不相同 |
| **HD-G06** | `algo None` | G 测试重写时把 `algo_version` 正则一并回退成了不存在的 `ALGO_VERSION` 常量 | 改回读 `core/channel_sync.py` 的 `DEFAULTS["algo_version"]` → `2.3.0-p1` |
| **HD-K02** | `KeyError: 'fixture'` | K-02 把自检报告 `_stability.json` **写进了它自己扫描的目录**，第二次运行就把报告当成 baseline 条目去重推 | 扫描时跳过 `_` 前缀文件，并注释写明原因 |

另有一处**判定器自指**：HD-J06（最终闸门）把自己上一次的 FAIL 也算进 summary，
于是一次失败会污染此后每一次汇总——即使底层测试早已修好也持续报 BLOCKED。
已改为闸门只评判其它测试，并在证据里显式记录 `excluded_from_gate: ["HD-J06 (self)"]`。

> 这些都不是"测试写错了所以放宽判据"——每条都先定位真实根因，
> 再修被测对象或修判定逻辑，修完重跑同一测试确认。

---

# §9 边界声明（引用时必须带上）

* **QSVEncC 补丁：** *Runtime-proven on QSVEncC 8.26 pinned revision;
  not yet a general claim for later releases.*
  （8.27–8.30 存在且未检验）
* **NVEncC 补丁：** 仅在 9.31（`2cb9d810c045202548b98ff130b12bc764eb39ea`）上验证。
* **research build 不可分发：** 两个 patched binary 都是 research build
  （avs/vpy reader 关闭、CUDA MSBuild shim、FFmpeg 动态链接、
  QSVEncC 的 OneVPL 单独构建）。`release/build_release.py` 的
  `TOOL_DIRS` 白名单**不含** `tools/avhw/`，`docs/` 也排除在包外。
  **因此发布安装中 `--hw-decode auto` 会以 `not_proven` 降级软解、
  `require` 会明确失败——这是设计行为，不是缺陷。**
* **跨 binary 性能不可比：** NVEncC patched 与 stock 工具链不同
  （CUDA 13.1/MSVC 14.51 vs CUDA 11.8/MSVC 14.44），
  因此跨 binary 只比**正确性**（帧数/PTS/keyframe/指纹/字节），不比性能。
* **`FramePosList::setPocAndFix`：** 独立 metadata-table 缺陷，
  本 session **未研究、未修复、未打包**；consumer 侧缓解措施是
  "永不把 reader 自报当成帧数"（已落实为设计）。
* **矩阵通过 ≠ 全新声明。** 本 session 的通过范围是：
  Sony XAVC HS（HEVC Main10 4:2:0）、Sony XAVC S（H.264 4:2:2 10-bit）、
  DJI（HEVC Main10 4:2:0）、以及 x265 / synthetic / MKV 控制组。

---

# §10 已知限制

| # | 限制 | 影响 |
|---|---|---|
| 1 | **QSVEncC 版本边界** | patch 仅对 pinned **8.26** 成立；8.27–8.30 未检验，不纳入通过范围 |
| 2 | **research build 不可分发** | 发布包中不含 patched binary，故发布安装里 `--hw-decode auto` 降级软解 |
| 3 | **`--seek` reader 不等价** | 已由路由守卫覆盖；production 不使用 seek，实际影响为零 |
| 4 | **allowlist 是封闭的** | 8-bit / All-I / 1080p / AV1 / VFR / HEVC 4:2:2 均 `not_proven` → 软解。这是"不扩大到未经验证的 codec/profile"的落实，不是遗漏 |
| 5 | **`--frames N` 三段语义** | 是 reader 共有行为，与硬解无关；不得与 full-input integrity 混淆 |
| 6 | **单 GPU 主机** | 多 GPU 设备选择未验证（research 亦未验证） |
| 7 | **`--seek` 差异机制未解释** | 不影响生产路径 |
| 8 | **4-way 4K 的上限与机制** | 本 session 实测 118.61 fps（用 qvbr-26 快档、360 帧片段），与 research 的 8.01 fps（生产 HQ 档、长片段）**不可比**；本测试**无吞吐判据** |
| 9 | **遥测完整性** | CPU time / GPU util / VRAM 由 `tasklist` + `nvidia-smi` 采样；平台未报告时记 null |
| 10 | **raw-pipe 架构未采用** | 按 research 建议，FFmpeg CPU raw-pipe 不作为 production primary architecture |

---

# §11 Open follow-ups

| # | 项 | 级别 |
|---|---|---|
| 1 | `--seek` 上两个 reader 为何选到不同画面——机制未解释 | P2 |
| 2 | QSVEncC 8.27–8.30 重新定位条件并复验 | P2 |
| 3 | 把两个 pipeline patch 反馈上游（carrying a fork 在一个 rebase 失误即静默丢帧的代码路径上不划算） | P2 |
| 4 | 多 GPU / `--device` 选择验证 | P2 |
| 5 | 扩展 allowlist：8-bit / 1080p / VFR 素材的硬解验证（需要素材） | P2 |
| 6 | `--hw-decode-verify`（sequence gate）作为可选生产开关的成本评估 | P2 |
| 7 | 4-way 4K 并发上限与其机制（VRAM vs NVENC session 争用，仍不可分） | P2 |
| 8 | `FramePosList::setPocAndFix` 上游报告（仅报告，不改代码） | P2 |
| 9 | **是否把 hardware decode 设为默认** —— 需要矩阵全绿 + production benchmark 之后再决定 | 决策 |

---

# §12 复现方法

```powershell
cd F:\1KeyTranscoder

# 1. 先确认工具链身份（任何不符 = FAIL，不是 warning）
python -m tests.hwdecode.harness provenance

# 2. 确认文档与矩阵没有漂移
python -m tests.hwdecode.harness check-matrix

# 3. 建/刷新生成的 control fixtures 并盘点语料
python -m tests.hwdecode.inventory

# 4. 按 phase 执行（顺序不可打乱）
python -m tests.hwdecode.harness run --phase 1     # A toolchain + B routing
python -m tests.hwdecode.harness run --phase 2     # C integrity + D temporal
python -m tests.hwdecode.harness run --phase 3     # E preservation + F fallback
python -m tests.hwdecode.harness run --phase 4     # G features + H resume
python -m tests.hwdecode.harness run --phase 5     # I concurrency
python -m tests.hwdecode.harness run --phase 6     # J corpus / long-run + K baseline

# 5. 最终闸门（必须在其它测试之后运行）
python -m tests.hwdecode.harness run HD-J06
python -m tests.hwdecode.harness summary

# 6. 既有项目回归
python tests/full_autotest.py --level full
```

**开关：** `--deep` 打开各长测试的完整变体（更慢）；`--repeat N` 对 P0 用例重复 N 次。

**端到端手工冒烟：**

```powershell
python 1kt.py --input <dir> --output <dir> --check basic --headless `
              --encoder nvenc --hw-decode auto --preset hq
```

日志中应出现 `[HWDEC] decode tool: ... (patched build verified by sha256 ...)`、
`DECODE_ROUTE | ... -> HARDWARE`、`INTEGRITY | integrity gate [OK]`。

---

# §13 附录：证据位置索引

| 想找什么 | 去哪里 |
|---|---|
| 每条测试的定义与成功条件 | [`integration-test-matrix.md`](integration-test-matrix.md) |
| 每条测试的完整判定证据（含计数、指纹、日志路径） | `work/avhw_integration/results/results.json` |
| 扁平结果表 | `work/avhw_integration/results/results.csv` |
| 机器可读闸门 | `work/avhw_integration/results/summary.json` |
| `--frames` 三段 contract 表 | `work/avhw_integration/results/frames_contract.json` |
| 软件 golden baseline | `work/avhw_integration/baseline/*.json` |
| 每次编码的原始工具日志 | `work/avhw_integration/logs/*.log` |
| binary / 补丁身份 | [`toolchain-provenance.json`](toolchain-provenance.json) |
| 补丁本体 | [`patches/`](patches/) |
| research 阶段根因与结论（输入事实） | [`../../olddocs/docs/hardware-decode/research-conclusion.md`](../../olddocs/docs/hardware-decode/research-conclusion.md) |
| 本目录导航 | [`README.md`](README.md) |
| 既有项目回归报告 | `work/autotest/autotest_report.json` / `.md` |

---

# §14 最终立场

```text
software  = stable baseline          （默认，未改变）
hardware  = validated optional path  （--hw-decode auto|require，需显式开启）
```

**本 session 不把 hardware decode 设为 global default。**
是否切换为默认，留到矩阵完整通过 + production benchmark 之后决定。

最终标准不是"程序没有报错"，而是本报告 §1 所述那一条：

> **即使 hardware decoder 再次出现类似 Sony 的静默丢帧问题，
> 1KeyTranscoder 也必须能够检测出来，而不是把错误结果当成成功结果交付。**

由 **HD-C09**（真实 stock 注入 N−3 → 闸门判 `count_mismatch` → 丢弃 → 软解 exact）
与 **HD-C10**（计数不变的错序产物 → 计数闸门放行、sequence 闸门抓到
`sequence_mismatch`）**逐条证明**。
