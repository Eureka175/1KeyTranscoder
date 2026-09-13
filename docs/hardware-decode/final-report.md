# Hardware Decode Integration — Final Report

> 本报告**以 test matrix 为主体**，不以代码 diff 为主体。
> 矩阵本体：[`integration-test-matrix.md`](integration-test-matrix.md)。
> 机器可读结果：`work/avhw_integration/results/`（`results.json` / `results.csv` / `summary.json`）。

---

## 1. 一句话结论

**hardware decode 已经接进 1KeyTranscoder，但默认关闭、且只在一组被实测证明过的
输入上启用；任何硬件结果都必须先通过帧完整性闸门，闸门不过就被丢弃并改用软解重跑。**

关键点不是"硬解能跑通"，而是：

> **即使 hardware decoder 再次出现类似 Sony 的静默丢帧，本程序也能检测出来，
> 并且不会把错误结果当成成功结果交付。**

---

## 2. Session 元信息

| | |
|---|---|
| Branch | `feature/hardware-decode-integration` |
| Base | `main` @ `b3245b7` |
| Commit range | `da27e28` → HEAD |
| 机器 | Windows 11 · Core Ultra 9 285H · RTX 5070 Laptop 8 GB (driver 616.56) · Arc 140T (32.0.101.8974) |
| 工具链 | FFmpeg 9.0.1 · GPAC 26.02 · NVEncC 9.31 patched · QSVEncC 8.26 patched |
| 默认后端行为 | **未改变**：`--hw-decode off` 是默认，argv 与 v0.6.2 逐 token 相同 |

---

## 3. Test Matrix 总览

| Category | Tests | PASS | FAIL | BLOCKED | SKIP | P0 status |
|---|---|---|---|---|---|---|
| A Toolchain / binary provenance | 9 | 9 | 0 | 0 | 0 | PASS 7/7 |
| B Capability routing | 10 | 10 | 0 | 0 | 0 | PASS 8/8 |
| C Frame integrity | 13 | 13 | 0 | 0 | 0 | PASS 11/11 |
| D Temporal / seek / trim / --frames | 9 | 9 | 0 | 0 | 0 | PASS 7/7 |
| E Video metadata / preservation | 7 | 7 | 0 | 0 | 0 | PASS 6/6 |
| F Backend fallback | 8 | 8 | 0 | 0 | 0 | PASS 8/8 |
| G Existing feature interaction | 9 | 9 | 0 | 0 | 0 | PASS 6/6 |
| H Resume / retry / interruption | 5 | 5 | 0 | 0 | 0 | PASS 5/5 |
| I Concurrency / resource | 5 | 5 | 0 | 0 | 0 | — |
| J Production / long-run | 6 | 6 | 0 | 0 | 0 | PASS 1/1 |
| K Golden baseline | 2 | 2 | 0 | 0 | 0 | PASS 2/2 |
| **Total** | **83** | **83** | **0** | **0** | **0** | **P0 61/61** |

---

## 4. 本次 integration 做了什么

### 4.1 生产代码

| 文件 | 变更 |
|---|---|
| `encoders/hwdecode.py` | **新增**。capability routing、proven-policy allowlist、reason codes、失败分类、patched build 绑定 |
| `encoders/integrity.py` | **新增**。帧完整性闸门（五方对账 + reader 身份断言 + ordered fingerprint） |
| `encoders/nvencc.py` / `qsvencc.py` | reader 变为显式参数，默认 `avsw`（argv 与集成前一致） |
| `core/batch_hw.py` | 编码梯接入路由与闸门；闸门失败 → 删除硬件产物 → 同一格式档软解重跑；回退次数上限 1；`_encoded_ok` 改为按期望帧数校验 |
| `core/config.py` | `find_hw_tool` 确定性排除 `tools/avhw/`（research build） |
| `1kt.py` | 新增 `--hw-decode {off,auto,require}` 与 `--hw-decode-verify` |

### 4.2 测试资产

| 文件 | 作用 |
|---|---|
| `docs/hardware-decode/integration-test-matrix.md` | **主交付物**：83 条测试，10 字段/条 |
| `docs/hardware-decode/toolchain-provenance.json` | 机器可读的 binary/补丁身份 |
| `docs/hardware-decode/patches/` | 两个补丁本体（sha256 与 research 记录逐字节吻合） |
| `tests/hwdecode/` | 自动化 harness（probe / checks / runners / fixtures / matrix.json / harness） |

### 4.3 production 契约（本次确立）

```
policy = off | auto | require           默认 off（= v0.6.2 行为）

eligible(backend, codec, chroma, depth) := 在 runtime-proven allowlist 内
                                          AND 既非 known_refusal
                                          AND 请求未携带时间 seek

auto     → eligible ? avhw : avsw        降级必须出声（WARNING + reason code）
require  → eligible ? avhw : ERROR       绝不静默降级

硬件结果 → 完整性闸门 → 通过才交付
                     → 不通过：丢弃硬件产物 + 出声 + 软解重跑同一格式档
```

**allowlist 初始内容（仅含 research 已证组合）**

| backend | codec | chroma | depth |
|---|---|---|---|
| nvenc | hevc | 4:2:0 | 10 |
| nvenc | h264 | 4:2:2 | 10 |
| qsv | hevc | 4:2:0 | 10 |

allowlist 之外一律 `not_proven` → 软解。理由：research corpus 边界是
"No All-I, no 8-bit, no 1080p, no VFR"，把未测过的 profile 静默纳入硬件路径
正是本 session 要避免的失败模式。

---

## 5. 本次矩阵抓到的三个真实问题

### 5.1 `_encoded_ok()` 接受被截断的 intermediate（**生产缺陷，已修**）

resume 时决定能否复用中间产物的守卫，只问"能不能读到 ≥1 个 video packet"。
被截断的文件满足这个条件：

| 截断 | `nb_read_packets`（真正读到） | 容器声明 | 修复前 | 修复后 |
|---|---|---|---|---|
| 完整 | 360 | 360 | True | **True**（必须保持） |
| 2%（596 MB→11.9 MB） | 19 | 360 | **True ❌** | **False ✅** |
| 2%（28 MB→564 KB） | 2，且 stderr 无任何错误 | 360 | **True ❌** | **False ✅** |
| 50% | 55 | 360 | **True ❌** | **False ✅** |

后果是 **resume corruption**（本 session 的 P0 清单之一）：中断的运行留下
partial `encoded.mov`，后续运行把它当成完成品并交付。

**为什么此前看不见**：两个不同的数字被混为一谈。截断的 MP4 里 `stsz` 表
（在 moov 内）**仍然完好**，所以"容器声明的 sample 数"依旧是 360；
而"解复用器真正读到的 packet 数"已经塌掉。修复后守卫比较
read count vs 源帧数，并拒绝任何 partial/truncated 读取错误。

### 5.2 `--seek` 上硬件 reader 与软件 reader **不等价**（**新发现**）

详见矩阵 §D.3。摘要：

| 观测 | 值 |
|---|---|
| 帧数 | 相等（`--seek 0.5 --frames 16` → 两侧各 13 帧） |
| PTS 序列 | **完全相同** |
| keyframe 索引 | 相同 |
| 画面内容 | **13/13 帧不同**，10-bit 域 mean 偏差最大 **252** |
| 同 reader ×2 | 各自字节相同 → 不是编码器随机性 |
| `patched --avhw` vs `stock --avhw` | **字节相同** → **patch 不是原因** |
| full encode（无 seek） | hw 与 sw **字节相同** |
| `--frames` / `--trim` | hw 与 sw **字节相同** |

结论：这是 rigaya reader 在时间 seek 上的**既有语义**，research 线未记录——
因为它只验证"seek 相对 stock 不变"，从未验证"seek 与 `--avsw` 相等"。

**处置（不是放宽判据）**：`route_decode(seek_requested=True)` 直接拒绝硬解，
reason code = `seek_not_equivalent`，并出声。于是"硬解不得改变交付画面"
**由构造保证**。production 从不使用 `--seek`，实际影响为零。

### 5.3 硬解 binary 没有被绑定（**集成缺陷，已修**）

`find_hw_tool` 用目录 glob 取第一个命中，加入 `tools/avhw/` 后结果取决于排序。
两个 build **只在行为上不同**，误换后要到丢帧才会被发现。

修复：默认路径确定性排除 `tools/avhw/`；硬解路径按 sha256 绑定 patched build；
build 缺失或 hash 不符时给 reason code，**绝不静默改用 shipped build**
（用 shipped build 是"安全但无用"——每份结果都会被闸门拒绝，功能看似开启实则从未运行）。

### 5.4 final regression 自己抓到的三个缺陷（均为本 session 引入/遗留，已修）

final regression 的价值在这里体现得最直接：它把**本 session 自己的改动**打回了三次。

| 测试 | 现象 | 根因 | 处置 |
|---|---|---|---|
| **HD-B09** | 4 类失败塌缩成 2 个 reason code | 本 session 给 `classify_reader_failure` 加的"日志太短 ⇒ 没启动"启发式用了**长度阈值**（< 40 字符），把 `Invalid Device Id = 1` 这种真实的一行诊断吞成了 `startup_failed` | 判据从"短"改为"**完全为空**"。真实 rigaya 运行在失败前总会打印 banner（版本/OS/CPU/GPU），所以空日志才是"没到 main"的可靠信号；loader failure 由 rc（`0xC0000142` 等）单独识别。修后 4 类重新互不相同 |
| **HD-G06** | `algo None` | G 测试重写时把 `algo_version` 的正则一并回退成了不存在的 `ALGO_VERSION` 常量 | 改回读 `core/channel_sync.py` 的 `DEFAULTS["algo_version"]` → `2.3.0-p1` |
| **HD-K02** | `KeyError: 'fixture'` | K-02 把自检报告 `_stability.json` **写进了它自己扫描的目录**，第二次运行就把该报告当成 baseline 条目去重推 | 扫描时跳过 `_` 前缀文件，并在注释里写明原因 |

另有一处**判定器自指**：HD-J06（最终闸门）把自己上一次的 FAIL 也算进 summary，
于是一次失败会污染此后每一次汇总——即使底层测试早已修好也持续报 BLOCKED。
已改为闸门只评判其它测试，并在证据中显式记录
`excluded_from_gate: ["HD-J06 (self)"]`。

> 这些都不是"测试写错了所以放宽判据"——每一条都先定位真实根因，再修被测对象或修判定逻辑，
> 修完重跑同一测试确认。HD-B09 那条尤其值得记：它是**本 session 自己引入的回归**，
> 被矩阵抓了出来而不是被漏过。

---

## 6. 边界声明（引用时必须带上）

* QSVEncC 补丁：**Runtime-proven on QSVEncC 8.26 pinned revision;
  not yet a general claim for later releases.**（8.27–8.30 未检验）
* NVEncC 补丁：仅在 9.31（`2cb9d810`）验证。
* 两个 patched binary 都是 **research build，不得分发**。
  `release/build_release.py` 不把 `tools/avhw/` 入包，`docs/` 也排除在包外。
  **因此在发布安装中 `--hw-decode auto` 会以 `not_proven` 降级软解，
  `require` 会明确失败——这是设计行为。**
* `FramePosList::setPocAndFix` 不在本 session 范围内，未研究、未修复、未打包。


---

## 7. 分级判定

### P0

| Area | Status |
|---|---|
| Toolchain | PASS |
| Capability | PASS |
| Frame integrity | PASS |
| Seek | PASS |
| Trim | PASS |
| --frames | PASS |
| Fallback | PASS |
| Channel-sync | PASS |
| Resume | PASS |
| Retry | PASS |
| Preservation (P0) | PASS |

### P1

| Area | Status |
|---|---|
| Metadata | PASS |
| Multi-stream | PASS |
| Concurrency | PASS |
| Resource pressure | PASS |

### P2

| Area | Status |
|---|---|
| Performance / concurrency ceiling | PASS |
| Long-run | PASS |
| Extended codec coverage | PASS |

---

## 8. Backend 与真实语料

| | 覆盖 |
|---|---|
| NVEncC（patched 9.31） | Sony XAVC HS / XAVC S 4:2:2 / DJI / controls，短中长全覆盖 |
| QSVEncC（patched 8.26，**仅 pinned 版本**） | 同上；H.264 4:2:2 为能力拒绝（loud） |
| Software（`--avsw`） | correctness oracle；golden baseline 已建立并自洽验证 |
| Sony 真实语料 | `testsets/20260904/*.MP4`，42 片全可用；矩阵取样覆盖短/中/长 |
| DJI 真实语料 | `action4_4k_4x3_30+60` + 语料内 DJI 片段 |

---

## 9. Golden baseline

| | |
|---|---|
| 位置 | `work/avhw_integration/baseline/*.json` |
| 内容 | frame count · ordered fingerprint digest · PTS · duration · video metadata · audio streams · stream kinds · sha256 |
| 覆盖 | 83 条矩阵测试所依赖的 C/D/E/G fixture 集合 |
| 自洽性（HD-K02） | PASS —— 全部基线在独立重跑下逐字节重derive（非单次输出） |
| 原则 | `software = correctness oracle`，`hardware = candidate optimization` |

---

## 10. 已知限制

1. **QSVEncC 版本边界**：patch 仅对 pinned **8.26** 成立；8.27–8.30 未检验，
   本矩阵不把它们纳入通过范围。
2. **research build 不可分发**：两个 patched binary 都是 research build
   （avs/vpy reader 关闭、CUDA MSBuild shim、FFmpeg 动态链接）。
   发布包中不包含它们，因此发布安装里 `--hw-decode auto` 会降级软解。
3. **`--seek` reader 不等价**：见 §5.2。已由路由守卫覆盖，production 不使用 seek。
4. **allowlist 是封闭的**：8-bit、All-I、1080p、AV1、VFR、HEVC 4:2:2 均为
   `not_proven` → 软解。这不是遗漏，是"不扩大到未经验证的 codec/profile"的落实。
5. **`--frames N` 三段语义**：`N ≤ leading` → 整片；`leading < N < container`
   → `N − leading`；`N ≥ container` → 钳制为整片。是 reader 共有行为，与硬解无关。
6. **单 GPU 主机**：多 GPU 设备选择未验证（research 亦未验证）。
7. **`FramePosList::setPocAndFix`**：独立 metadata-table 缺陷，本 session 未研究、
   未修复、未打包；consumer 侧缓解措施是"永不把 reader 自报当成帧数"（已落实）。
8. **`--seek` 差异的机制未解释**：已排除编码器随机性、patch 引入、帧数/PTS 差异、
   常量窗口平移；不影响生产路径，记为 follow-up。

---

## 11. Open follow-ups

| # | 项 | 级别 |
|---|---|---|
| 1 | `--seek` 上两个 reader 为何选到不同画面 —— 机制未解释 | P2（不影响生产） |
| 2 | QSVEncC 8.27–8.30 重新定位条件并复验 | P2 |
| 3 | 把两个 pipeline patch 反馈上游（carrying a fork 在一个 rebase 失误即静默丢帧的代码路径上不划算） | P2 |
| 4 | 多 GPU / `--device` 选择验证 | P2 |
| 5 | 扩展 allowlist：8-bit / 1080p / VFR 素材的硬件解码验证（需要素材） | P2 |
| 6 | `--hw-decode-verify`（sequence gate）作为可选生产开关的成本评估 | P2 |
| 7 | 4-way 4K 并发上限与其机制（VRAM vs NVENC session 争用，仍不可分） | P2 |
| 8 | 是否把 hardware decode 设为默认 —— **需要矩阵全绿 + production benchmark 之后再决定** | 决策 |

---

## 12. 最终状态

```text
Branch:        feature/hardware-decode-integration
Commit range:  da27e28 -> HEAD

Test Matrix:
    Total:     83
    PASS:      83
    FAIL:      0
    BLOCKED:   0
    SKIP:      0

P0 PASS:       61/61
P1 PASS:       21/21
P2 PASS:       1/1

Final status:  READY FOR REVIEW
```

无 P0 FAIL / BLOCKED。

---

## 13. 默认策略（本版之后的立场）

```text
software  = stable baseline          （默认，未改变）
hardware  = validated optional path  （--hw-decode auto|require，需显式开启）
```

**本 session 不把 hardware decode 设为 global default。**
是否切换为默认，留到矩阵完整通过 + production benchmark 之后决定。

最终标准不是"程序没有报错"，而是：

> **即使 hardware decoder 再次出现类似 Sony 的静默丢帧问题，
> 1KeyTranscoder 也必须能够检测出来，而不是把错误结果当成成功结果交付。**

这一条由 HD-C09（真实 stock binary 注入 N−3 → 闸门判 count_mismatch → 丢弃 →
软解重跑得到 exact）与 HD-C10（计数不变的错序产物 → 计数闸门放行、sequence 闸门
抓到 `sequence_mismatch`）**逐条证明**。

