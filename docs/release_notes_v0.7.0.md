# 1KeyTranscoder v0.7.0 — Release Notes

> 发布类型：**milestone release（Hardware Decode integration）· GitHub Pre-release**
> 基线：`main` @ `b3245b7`（**`main` 未被本版修改**）
> 本版 tag 所在分支：`feature/hardware-decode-integration`
> **本版不是 1.0.0，也不代表完整 release capability。**
> `--hw-decode` 的默认值仍是 **`off`**，默认路径行为与集成前**逐 token 相同**。

## 1. Highlights

| 项 | 说明 |
|---|---|
| **Hardware decode 接入** | 新增 `--hw-decode off\|auto\|require`（默认 `off`）与可选的 `--hw-decode-verify`（sequence 级闸门）。硬解是**显式 opt-in** 路径，不是新的默认行为 |
| **Capability routing** | 只有落在 **runtime-proven allowlist** 内的组合才允许走硬解；其余一律 `not_proven` → 软解（见 §4） |
| **Integrity gate** | 硬解产物必须通过帧完整性闸门：**五方帧数对账 + reader 身份断言**（`count` 级，启用硬解时始终开启）；`--hw-decode-verify` 再加**逐帧有序 fingerprint**（`sequence` 级，opt-in，代价是每文件多一次编码） |
| **Fallback / reason codes** | 闸门不过 ⇒ **丢弃硬件产物** → WARNING + reason code → 同格式档软解重跑；回退上限 1 次（`MAX_HW_DECODE_FALLBACKS`），超出即 FATAL，不无限重试（见 §5） |
| **Binary provenance 绑定** | 硬解**只**使用 provenance 校验过的 patched build（sha256 绑定）；build 缺失或 hash 不符时给 reason code，**绝不静默改用 shipped build** |
| **默认行为零变化** | HD-B05 逐 token 断言：`policy=off`（不传任何开关）产出的 argv 与集成前**完全一致**（首部仍 `--avsw`） |
| **验证规模** | integration matrix **83/83 PASS**（P0 61/61，0 FAIL / 0 BLOCKED / 0 SKIP）；既有全量回归 `tests/full_autotest.py --level full` **248 PASS / 0 FAIL** |

## 2. 版本号

```
VERSION                    0.6.2  ->  0.7.0
python 1kt.py --version    1KeyTranscoder 0.7.0
```

`VERSION` 是唯一版本来源（`core/version.py`），`1kt.py --version`、
`release/build_release.py` 与 `release/verify_package.py` 均从该文件读取，
不存在第二处硬编码版本。本版**未改动任何编码/解码行为、默认后端或默认硬解策略**。

## 3. Integration 判定（机器可读闸门）

```text
Branch:        feature/hardware-decode-integration
Base:          main @ b3245b7   (main 未被修改)
Commits:       da27e28 .. f819eb4

Test Matrix:   Total 83 | PASS 83 | FAIL 0 | BLOCKED 0 | SKIP 0
               P0 61/61 · P1 21/21 · P2 1/1
既有回归:      tests/full_autotest.py --level full -> 248 PASS / 0 FAIL (570.3s)

Final status:  READY FOR REVIEW      Blockers: （无）
```

判定规则未放宽：任何 P0 `FAIL`/`BLOCKED` ⇒ 最终状态必须 `BLOCKED`；
`SKIP` 与 `BLOCKED` **不计入**通过。最终 0 个 P0 FAIL / 0 个 P0 BLOCKED。

分类覆盖：A toolchain 9 · B routing 10 · C frame integrity 13 · D temporal 9 ·
E preservation 7 · F fallback 8 · G existing features 9 · H resume/retry 5 ·
I concurrency 5 · J production/long-run 6 · K golden baseline 2。

矩阵的自动化状态为 **100% `auto`** —— 没有"需要人工播放视频检查"的条目。

## 4. 路由契约

```text
policy = off | auto | require            默认 off（= v0.6.2 行为，零变化）

eligible(backend, codec, chroma, depth) :=
        在 runtime-proven allowlist 内
    AND 不在 known_refusals 内
    AND 请求未携带时间 seek
    AND 该组合未被本运行 memo 标记为不可用

off      -> reader = avsw                    （不出任何提示）
auto     -> eligible ? avhw : avsw           （降级必须出声：WARNING + reason code）
require  -> eligible ? avhw : ERROR          （绝不静默降级）
```

runtime-proven allowlist（初始内容，全部经 research + 本次 integration 实测）：

| backend | codec | chroma | depth | 依据 |
|---|---|---|---|---|
| nvenc | hevc | 4:2:0 | 10 | N−3 → N（30/330/10170 帧），与 `--avsw` 字节相同 |
| nvenc | h264 | 4:2:2 | 10 | N−2 → N（195 帧），与 `--avsw` 字节相同 |
| qsv | hevc | 4:2:0 | 10 | N−3 → N，逐帧 SHA-256 与 `--avsw` 相同 |

known_refusals：`qsv + h264 + 4:2:2 + 10bit`
（`avqsv: codec h264(yuv422p10le) unable to decode by qsv.` rc=−31、无 reader、无输出）。

**allowlist 之外一律 `not_proven` → 软解**（8-bit / All-I / 1080p / AV1 / VFR /
HEVC 4:2:2 等均未纳入）。这是"不把未实测 profile 静默塞进硬件路径"的落实，不是遗漏。

## 5. 帧完整性闸门与 reason codes

**失败动作**：丢弃硬件产物 → 出声（WARNING + reason code）→ 同格式档软解重跑。

两级闸门分别对应两类缺陷：

* `count` 级：工具 rc == 0 且产物非空；**reader 身份断言**（请求 `avhw`
  却构造出 `avsw` 即 silent software fallback，判 FAIL）；五方对账
  `container_expected` 为参考真值，`encoder_input` / `output_stream` /
  `independent_decoded` 任一不等即 FAIL；reader 自报只记录、**永不作参考**。
* `sequence` 级（`--hw-decode-verify` opt-in）：硬解与软解结果逐帧有序
  fingerprint 比对，抓**计数不变**的错序/替换/重复。

Reason codes（互不相同、可审计）：
`policy_off` · `proven_combination` · `not_proven` · `capability_refused` ·
`reader_unavailable` · `device_unavailable` · `startup_failed` ·
`decode_failed` · `count_mismatch` · `sequence_mismatch` ·
`seek_not_equivalent` · `require_unmet` · `integrity_ok`

端到端实测（真实 CLI，节选）：

```text
[HWDEC] decode tool: ...\tools\avhw\NVEncC_9.31_avhw\NVEncC64.exe
        (patched build verified by sha256 dcf6d7a63143c777)
DECODE_ROUTE | backend=nvenc codec=hevc 4:2:0/10bit policy=auto -> HARDWARE
        (reader=avhw, reason=proven_combination)
INTEGRITY | integrity gate [OK] reason=integrity_ok
        counts={tool_rc:0, reader_identity:'avcuvid', encoder_input:360,
                container_expected:360, container_method:'isobmff-stsz', ...}
```

## 6. Validation

| 项 | 结果 |
|---|---|
| Integration test matrix | **83/83 PASS**（P0 61/61 · P1 21/21 · P2 1/1；0 FAIL / 0 BLOCKED / 0 SKIP，累计约 3.3 h） |
| 既有全量回归 | `tests/full_autotest.py --level full` → **248 PASS / 0 FAIL**（570.3 s） |
| Golden baseline（软件基线自洽性） | HD-K01 / HD-K02 PASS：6 条 fixture 的软件基线在独立重跑下**逐字节重 derive** |
| 真实语料 | Sony `testsets/20260904/*.MP4` 取样 10 短 / 10 中 / 10 长；DJI action4 4K + 语料内 DJI 片段 |
| 工具链身份 | 每次运行前校验 binary sha256 + 补丁 sha256 + `--version` 字段，不符即 FAIL（不是 warning） |

矩阵执行过程中抓到 **3 个真实问题**（1 个生产缺陷：`_encoded_ok()` 曾接受被截断的
intermediate；1 个研究线未记录的行为差异；1 个集成缺陷：`find_hw_tool` 目录 glob
可能按排序捞到 patched build），final regression 又打回本 session 自己引入的 3 个缺陷 ——
全部先定位根因再修被测对象或判定逻辑，并重跑同一测试确认。

## 7. 边界声明（引用本版结论时必须带上）

* **QSVEncC 补丁：** *Runtime-proven on QSVEncC 8.26 pinned revision; not yet a
  general claim for later releases.*（8.27–8.30 存在且未检验）
* **NVEncC 补丁：** 仅在 9.31（`2cb9d810c045202548b98ff130b12bc764eb39ea`）上验证。
* **research build 不可分发：** 两个 patched binary 都是 research build
  （avs/vpy reader 关闭、CUDA MSBuild shim、FFmpeg 动态链接、QSVEncC 的 OneVPL
  单独构建）。`release/build_release.py` 的 `TOOL_DIRS` 白名单**不含** `tools/avhw/`。
  **因此发布安装中 `--hw-decode auto` 会以 `not_proven` 降级软解、`require` 会明确
  失败 —— 这是设计行为，不是缺陷。**
* **跨 binary 性能不可比：** patched 与 stock 工具链不同（CUDA 13.1/MSVC 14.51 vs
  CUDA 11.8/MSVC 14.44），跨 binary 只比**正确性**，不比性能。
* **矩阵通过 ≠ 全新声明：** 通过范围是 Sony XAVC HS（HEVC Main10 4:2:0）、
  Sony XAVC S（H.264 4:2:2 10-bit）、DJI（HEVC Main10 4:2:0），
  以及 x265 / synthetic / MKV 控制组。
* **`FramePosList::setPocAndFix`：** 独立 metadata-table 缺陷，本版**未研究、未修复**；
  consumer 侧缓解措施是"永不把 reader 自报当成帧数"（已落实为设计）。

## 8. Known Limitations

| # | 限制 | 影响 |
|---|---|---|
| 1 | QSVEncC 版本边界（仅 pinned 8.26） | 8.27–8.30 未检验，不纳入通过范围 |
| 2 | research build 不可分发 | 发布包中不含 patched binary ⇒ 发布安装里 `--hw-decode auto` 降级软解 |
| 3 | allowlist 是封闭的 | 8-bit / All-I / 1080p / AV1 / VFR / HEVC 4:2:2 均 `not_proven` → 软解 |
| 4 | `--seek` 两 reader 不等价 | 已由路由守卫覆盖（production 不使用 seek） |
| 5 | `--frames N` 三段语义 | 是 reader 共有行为，与硬解无关 |
| 6 | 单 GPU 主机 | 多 GPU / `--device` 选择未验证 |
| 7 | `--seek` 差异机制未解释 | 不影响生产路径 |
| 8 | 4-way 4K 并发上限与机制 | 本版**无吞吐判据**（实测 118.61 fps 与 research 的 8.01 fps 规格不同，不可比） |
| 9 | 遥测完整性 | CPU time / GPU util / VRAM 采样；平台未报告时记 null |
| 10 | raw-pipe 架构未采用 | 按 research 建议，FFmpeg CPU raw-pipe 不作为 production primary architecture |

**仍未完成，不得视为已解决：**

* **Audio Drift（P2 漂移分类 / resample）—— NOT STARTED**
* **patched hardware binary 仍为 research build，不可分发** ⇒ 发布形态下硬解不可用（§7）
* **是否把 hardware decode 设为默认 —— 未决定**，需矩阵全绿 + production benchmark 之后另行决策；
  本版**未改变**默认值（仍 `off`）
* 其余 P2 follow-ups：`--seek` 机制解释、QSVEncC 8.27–8.30 复验、pipeline patch 反馈上游、
  多 GPU 验证、allowlist 扩展（8-bit / 1080p / VFR）、`--hw-decode-verify` 作为常规开关的成本评估、
  4-way 4K 并发上限机制、`FramePosList::setPocAndFix` 上游报告

## 9. Backward Compatibility

* `VERSION` / `--version` → `1KeyTranscoder 0.7.0`；
* 档位 JSON 与命令行**无破坏性变更**；未传 `--hw-decode` 时命令行**逐 token 与
  v0.6.2 相同**，reader 恒为 `avsw`；
* 默认后端选择（能力优先 NVENC → QSV → x265）与 `--no-hw-autoselect` 语义未变；
* 新增开关仅两个：`--hw-decode {off,auto,require}`（默认 `off`）与 `--hw-decode-verify`（opt-in）；
* 硬解失败不会改变输出格式档：回退是在**同一格式档**上用软解重跑。

## 10. Package

**本版没有自包含发布包（no binary asset）。**

| 项 | 值 |
|---|---|
| Archive | —（本版不提供） |
| 原因 1 | 本版 tag 位于 `feature/hardware-decode-integration`，而 `release/build_release.py` 的分支守卫要求 `main` |
| 原因 2 | 硬解所需 patched binary 是 research build，**按设计不入包**（§7） |
| 最后一个带自包含包的版本 | `v0.6.1`（`1KeyTranscoder-v0.6.1-win64-selfcontained.zip`） |

从源码使用：`git checkout v0.7.0`，需要 Python 3.11+，并按 `README.md` §依赖
准备 `tools/`（ffmpeg/ffprobe 9.0.1、NVEncC 9.31、QSVEncC 8.26、GPAC）。

## 11. 相关文档

* [`hardware-decode/integration-test-matrix.md`](hardware-decode/integration-test-matrix.md) — **主交付物**，83 条测试的完整定义与成功条件
* [`hardware-decode/final-report.md`](hardware-decode/final-report.md) — 结构化最终报告（判定、逐条结果、三个真实发现、边界声明）
* [`hardware-decode/toolchain-provenance.json`](hardware-decode/toolchain-provenance.json) — binary / 补丁的权威身份
* [`design/architecture.md`](design/architecture.md) — 端到端管线（**适用范围仍为 v0.6.2**，硬解路径尚未并入该文档）

复现矩阵：

```powershell
python -m tests.hwdecode.harness provenance      # 工具链身份（不符 = FAIL）
python -m tests.hwdecode.harness check-matrix    # 文档与 matrix.json 漂移检查
python -m tests.hwdecode.inventory               # 生成 control fixtures + 语料盘点
python -m tests.hwdecode.harness run --phase 1   # ... 依次到 --phase 6
python -m tests.hwdecode.harness summary         # 汇总闸门
```
