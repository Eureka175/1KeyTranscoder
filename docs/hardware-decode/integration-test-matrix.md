# Hardware Decode Integration — Test Matrix

> **本文件是本 session 的第一交付物，先于 production 代码修改写成。**
> 实现按本矩阵逐项通过，而不是"写完代码再补测试"。
>
> | | |
> |---|---|
> | Branch | `feature/hardware-decode-integration` |
> | Base | `main` @ `b3245b7` |
> | 输入事实 | [`../../olddocs/docs/hardware-decode/research-conclusion.md`](../../olddocs/docs/hardware-decode/research-conclusion.md)（research close-out，视为已证） |
> | 机器 | Windows 11 · Core Ultra 9 285H · RTX 5070 Laptop 8 GB (driver 616.56) · Arc 140T |
> | 工具链 | FFmpeg 9.0.1 · GPAC 26.02 · NVEncC 9.31 patched · QSVEncC 8.26 patched |
> | Harness | `tests/hwdecode/`（`python -m tests.hwdecode.harness`） |
> | 结果 | `work/avhw_integration/results/`（JSON/CSV，机器可解析） |

---

## 0. 本矩阵在回答什么

一句话：

> **即使 hardware decoder 再次出现类似 Sony 的静默丢帧，1KeyTranscoder 必须能检测出来，
> 而不是把错误结果当成成功结果交付。**

由此推出的三条硬约束，贯穿全表：

1. **`exit code == 0` 不是正确性证据。** 已证事实：stock `--avhw` 在 Sony 上丢 3 帧
   而 `rc = 0`、无任何错误信息。
2. **播放器能播放不是正确性证据。** 丢 3 帧的 MP4 完全可播放。
3. **单一帧数相等也不是正确性证据。** QSVEncC 的丢帧是**连续头部截断**，
   纯计数可以过、画面已经错位。因此 C 类必须做 **ordered fingerprint**。

### 0.1 术语与判定词汇

| 结果 | 含义 |
|---|---|
| `PASS` | 成功条件全部满足，且证据已落盘 |
| `FAIL` | 成功条件未满足 |
| `BLOCKED` | 无法取得判定所需证据（工具/硬件/素材缺失，或前置测试未过） |
| `SKIP` | 本环境不适用（素材缺失、能力不具备），**不计入 PASS** |

`SKIP` 与 `BLOCKED` 都**不会**被算成通过。P0 出现 `FAIL` 或 `BLOCKED` 时，
integration 的最终状态必须是 `BLOCKED`。

### 0.2 严重度

| 级别 | 定义 | 例 |
|---|---|---|
| **P0** | 任一失败即 BLOCK integration | 未检测到的丢帧、顺序错误、输出损坏、静默回退、回退链断裂、channel-sync 回归、resume 损坏 |
| **P1** | 需修，但不阻塞全部 integration | 路由在安全前提下判错、元数据不一致、日志不足、罕见并发资源处理 |
| **P2** | 可进 follow-up | 性能调优、更广 codec 矩阵、多 GPU、4-way 吞吐、后续 QSVEncC 版本 |

### 0.3 自动化状态

| 值 | 含义 |
|---|---|
| `auto` | 由 `tests/hwdecode/harness.py` 全自动执行并给出判定 |
| `semi` | harness 采集证据，判定需人工确认（如需要硬件写保护/拔卡） |
| `planned` | 已定义、尚未自动化（会在结果里如实标 `SKIP`/未执行，不冒充 PASS） |

### 0.4 重复策略（§18 of the task）

* **P0 correctness**：至少 **3 次独立运行**（Sony 帧完整性、fallback、seek、trim、resume、
  channel-sync 交互）。
* **性能**：至少 3 次，报告 mean / min / max / 离散度，**不只报最好的一次**。
* **Long-run**：至少 1 次干净全程；出现 crash/异常失败须重跑确认，
  不得把单次偶发 crash 直接写成确定根因。

### 0.5 证据口径（六方对账）

C 类不允许只比较其中两个数。每个 C 用例都记录：

| # | 来源 | 取得方式 | 可信度 |
|---|---|---|---|
| 1 | `container_expected` | **自己按字节解析** `moov/trak/.../stsz`（`tests/hwdecode/probe.py`），MKV 才退回 libavformat 包计数 | 参考真值 |
| 2 | `reader_reported` | 工具日志 `N frames, End of file` | **不可信**：已证在 XAVC 上因 `FramePosList::setPocAndFix` 系统性少报 |
| 3 | `encoder_input` | 工具日志 `encoded N frames` | 编码器自述 |
| 4 | `output_stream` | 产物容器自身的 sample table | 产物自述 |
| 5 | `independent_decoded` | FFmpeg 独立解码产物并逐帧计数（`showinfo`） | 独立实现 |
| 6 | `fingerprint` | 逐帧有序签名（mean Y/U/V、mean\|dY\|、首尾采样、4×4 KB chunk digest） | 顺序与内容证据 |

判定规则：**1 为参考真值**；3/4/5 任一与之不等即 `FAIL`；2 只记录、不判定。
内容一致性由 6 单独判定。

### 0.6 与 research 结论的边界（不得越界声明）

* QSVEncC patch：**Runtime-proven on QSVEncC 8.26 pinned revision; no general claim for later versions.**
  8.27–8.30 未经检验，本矩阵不把它们纳入通过范围。
* NVEncC patch：仅在 9.31（`2cb9d810`）上验证。
* 两个 patched binary 都是 **research build**（`avs`/`vpy` reader 关闭、
  CUDA MSBuild shim、FFmpeg 动态链接），**不得分发**。
  本矩阵的 binary provenance 检查（A 类）就是为了防止"跑错了 binary 却当成通过"。
* `FramePosList::setPocAndFix` **不在本 session 范围内**，不重新研究、不打包修复。

### 0.7 本 session 明确不做的事

* 不把 hardware decode 设为 global default（第一版完成后仍是
  `software = stable baseline`，`hardware = validated optional path`）。
* 不把 FFmpeg CPU raw-pipe 作为 production primary architecture。
* 不为让测试通过而放宽 integrity criterion。
* 不扩大到未经验证的 codec/profile（见 HD-B04 的 allowlist 规则）。

---

## A. Toolchain / Binary provenance

**这一类存在的理由。** 整个矩阵的前提是"测的确实是那个 patched binary"。
四种误判都会让后面所有测试失去意义：跑了 stock binary；跑了错版本；
从 PATH 抓到别的 `NVEncC64.exe`；请求硬件但工具静默用了软解却记成 hardware pass。
**任何 binary provenance 不明确 = FAIL。**

| ID | Sev | Input | Backend | Expected behaviour（成功条件） | Verification method | Auto | Result |
|---|---|---|---|---|---|---|---|
| HD-A01 | P0 | patched NVEncC tree | NVEncC | sha256 == `dcf6d7a6…7c8be4b`；`--version` 含 `9.31 (r1)`、build date `Sep 12 2026`、`CUDA 13.1`；patch sha256 == `53084fa8…fe6dfc` 且已记录在 provenance 文件 | 启动时对 binary 做 sha256；解析 `--version` 与期望值逐字段比对；provenance JSON 交叉校验 | auto | |
| HD-A02 | P0 | patched QSVEncC tree | QSVEncC | sha256 == `f5df83f1…f8804d`；`--version` 含 `8.26 (r4504)`、build date `Sep 12 2026`；provenance 记录**版本边界声明**原文 | 同 A-01 | auto | |
| HD-A03 | P0 | 任一 hardware-decode 运行 | NVEncC / QSVEncC | 运行日志 `Input Info` 段的 reader 标识**确实是** `avcuvid`（NVEncC）/ `avqsv`（QSVEncC），**不是** `avsw`。仅凭命令行参数请求不算 | 从工具原始日志提取 reader 标识（`checks.reader_identity_from_log`）；与请求值比对；不符即 FAIL | auto | |
| HD-A04 | P0 | 任一 software 运行 | NVEncC / QSVEncC | reader 标识**确实是** `avsw`，且日志中不含 VD/QSV-decode 引擎活动的证据 | 同上，反向断言 | auto | |
| HD-A05 | P0 | patched 与 stock 两套 binary | both | 存在一个**可运行的 stock 对照 binary**，且其 sha256 与 patched 不同（`4c043e7b…` / `107e5f0d…`）。没有对照就无法证明 A-03 的 reader 断言不是自我循环 | 对 stock binary 做 sha256 + `--version`；与 patched 比对 | auto | |
| HD-A06 | P0 | integration 的工具解析路径 | both | 工具路径**只**来自显式解析（config/`TOOLS` 解析结果），绝不从 `PATH` 抓取；解析结果写入日志 | 在 `PATH` 中注入一个同名的诱饵 binary，确认解析结果不变 | auto | |
| HD-A07 | P1 | patched binary 运行环境 | both | 缺 DLL 等环境问题表现为**启动失败**而非错误结果；启动失败必须可分类（不落到 integrity 判定） | 在缺 DLL 的目录副本中启动，断言 rc≠0 且分类为 startup | auto | |
| HD-A08 | P1 | provenance 记录 | both | `docs/hardware-decode/toolchain-provenance.json` 与磁盘实际 binary 一致；任何不一致在测试开始时即报错退出 | harness 启动自检 | auto | |
| HD-A09 | P0 | 同时存在两套 build 的 `tools/` 树 | both | **默认路径必须解析到 shipped build，绝不解析到 research build**。`find_hw_tool` 是目录 glob；若不显式排除 `tools/avhw/`，结果会依赖排序，而两个 build 只在**行为**上不同——误换后要到丢帧才会被发现 | 解析两个工具名，断言路径落在 shipped 目录、结果确定，且 `tools/avhw` 被排除 | auto | |

**已知限制（A 类）**

* patched binary 是 research build，**不得分发**；`tools/avhw/` 明确排除在发布包外
  （`release/build_release.py` 的 `TOOL_DIRS` 白名单不含它）。
* NVEncC patched 与 stock 的工具链不同（CUDA 13.1 / MSVC 14.51 vs CUDA 11.8 / MSVC 14.44），
  因此**跨 binary 的墙钟时间与 fps 不构成受控基准**。本矩阵只把跨 binary 比较用于
  正确性（帧数/PTS/指纹/字节），不用于性能结论。

---

## B. Capability routing

**这一类存在的理由。** 硬解能不能用不是"命令里写 `--avhw`"决定的，而是
`input characteristics → capability decision → selected backend → expected result`
这条链决定的。链上任何一环判错，要么静默丢帧，要么把能用的机器白白降级。

### B.0 路由契约

```
policy = off | auto | require        （默认 off，绝不自动切换 global default）

off      → reader = avsw                        （= v0.6.2 现状，零行为变化）
auto     → eligible  ? reader = avhw : avsw     （降级必须出声：WARNING + reason code）
require  → eligible  ? reader = avhw : ERROR    （绝不静默降级为软件）

eligible(backend, codec, chroma, depth) := 该四元组在 runtime-proven allowlist 内
                                          AND 该 backend 的硬件 reader 在本机可用
```

**runtime-proven allowlist（本矩阵的初始内容，随 C/J 类实测证据扩展）**

| backend | codec | chroma | depth | 依据 |
|---|---|---|---|---|
| nvenc | hevc | 4:2:0 | 10 | research 已证 N−3 → N（360/330/10170 帧） |
| nvenc | h264 | 4:2:2 | 10 | research 已证 N−2 → N（195 帧） |
| qsv | hevc | 4:2:0 | 10 | research 已证 N−3 → N |
| qsv | h264 | 4:2:2 | 10 | **能力拒绝**（rc=−31，无输出）→ 走 `capability_refused` |

**allowlist 之外一律 `not_proven` → 软件路径。** 理由：research 的 corpus 边界是
"No All-I, no 8-bit, no 1080p, no VFR material exists in this environment"，
把未测过的 profile 静默纳入硬件路径正是本 session 要避免的失败模式。

| ID | Sev | Input | Backend | Expected behaviour | Verification method | Auto | Result |
|---|---|---|---|---|---|---|---|
| HD-B01 | P0 | `sony_hs_c0886`（HEVC Main10 4:2:0 3840×2160 59.94p LongGOP, 360 samples, leading=3） | NVEncC / QSVEncC | 两者均判 `eligible`；`auto` 下选中 `avhw`；reason code = `proven_combination` | harness 调用路由函数并断言决策对象；再实跑一次断言 A-03 reader 标识 | auto | |
| HD-B02 | P0 | `sony_422_c9037`（H.264 High 4:2:2 10-bit, 195 samples, leading=2）等 5 个样本 | NVEncC → **hardware**；QSVEncC → **refuse/software** | **两个 backend 允许得出不同结果**。NVENC 判 `eligible`；QSV 判 `capability_refused` 且 `auto` 下落到 `avsw` 且输出正确 | 对同一输入分别跑两 backend 的路由；QSV 侧断言 reason code 且产物帧精确 | auto | |
| HD-B03 | P0 | `dji_0009`（105 samples, leading=0）、`dji_0010`（330 samples, leading=0） | 两者 | 均判 `eligible` | 同 B-01 | auto | |
| HD-B04 | P0 | 未声明支持的输入（8-bit HEVC、All-I/INTRA、1080p、AV1 源） | 两者 | 决策必须是 **`not_proven` → 软件**，**不得**"unknown → hardware anyway"。允许 software 或 explicit reject，不允许静默冒险 | 构造/指定这些输入，断言决策为软件且 reason code 明确 | auto | |
| HD-B05 | P0 | 任意输入 | 两者 | `policy=off`（默认，不传任何开关）时**行为与 v0.6.2 完全一致**：reader 恒 `avsw`，命令行参数逐 token 相同 | 对同一输入，用默认参数构造命令并与集成前的 argv 做逐 token diff | auto | |
| HD-B06 | P0 | eligible 输入 | 两者 | `policy=require` 且硬件不可用时**报错终止**，绝不静默成功；`policy=auto` 才允许降级 | 用不可用设备/不可用输入触发，断言异常类型与 reason code | auto | |
| HD-B07 | P1 | VFR 源 | 两者 | VFR 源的 `--avsync forcecfr` 与 reader 选择互不干扰；VFR + hardware 组合有明确决策（不得因 VFR 而静默改 reader 或反之） | 构造 VFR 输入，断言参数组合与日志 | auto | |
| HD-B08 | P1 | 任意输入 | 两者 | 路由决策**确定性**且**可审计**：同输入同环境两次决策完全一致；决策与 reason code 进 per-file 日志与 CSV | 两次独立运行比对决策 JSON；检查日志字段存在 | auto | |
| HD-B09 | P0 | 任意输入 | 两者 | **能力拒绝与丢帧在决策与日志中可区分**：`capability_refused` / `not_proven` / `reader_unavailable` / `count_mismatch` / `sequence_mismatch` 各自独立 reason code，不得都写成 "failed" | 触发各类失败，断言 reason code 互不相同且语义正确 | auto | |
| HD-B10 | P0 | 同时存在两套 build 的 `tools/` 树 | 两者 | **硬件解码必须绑定在 provenance 校验过的 patched build 上**。`policy=off` 时不做任何 override；`auto`/`require` 时按 sha256 绑定；build 缺失或 hash 不符时给出 reason code，**绝不静默改用 shipped build**。理由：用 shipped build 是"安全但无用"——每份结果都会被 integrity gate 拒绝，功能看似开启实则从未运行 | 三档 policy 各解析一次 + 伪造 hash 的副本 + 缺失 build | auto | |

---

## C. Frame integrity（**P0，本 integration 的最高优先级**）

**这一类存在的理由。** 整个 research 线就是因为这里失败才存在的。

### C.0 成功条件

对每个 C 用例，**全部**满足才算 PASS：

1. `container_expected == encoder_input == output_stream == independent_decoded`（六方对账，见 §0.5）；
2. `reader_identity` 与请求一致（A-03）；
3. **ordered fingerprint 序列与软件基线逐帧相等**（长度、顺序、内容）；
4. PTS 序列单调且与软件基线相等；
5. keyframe 索引序列与软件基线相等；
6. 无静默回退（若发生回退，必须是 `auto` 策略下**出声**的回退，且该用例判定为"回退路径 PASS / 硬件路径 NA"而不是 hardware pass）。

### C.1 三方对照（stock / patched / software）

| ID | Sev | Input | Backend | Expected behaviour | Verification method | Auto | Result |
|---|---|---|---|---|---|---|---|
| HD-C01 | P0 | `sony_hs_c0886`（360 samples, leading=3）；`gen_sony_copy_mp4`（同 bitstream 重封装） | NVEncC：stock `avhw` / patched `avhw` / `avsw` | `patched avhw == avsw`（帧数、顺序、指纹、PTS、keyframe、字节）。stock `avhw` 预期 = **N−3**，作为**已知缺陷对照**记录，**不计入 integration pass**，但必须**确实复现**——若 stock 不再复现丢帧，说明对照失效，本用例判 BLOCKED 而非 PASS | 对三个 binary/reader 组合各跑一次同参数编码；六方对账 + 指纹 + 字节 sha256 | auto | |
| HD-C02 | P0 | `sony_hs_c0886`(360)、`sony_422_c9110`(630)、`gen_sony_copy_mp4`(360) | patched NVEncC / QSVEncC `avhw` vs `avsw` | 总帧数、ordered fingerprint、PTS、keyframe 序列全等 | 同上；PTS/keyframe 用 `packet_manifest` 全序列比对 | auto | |
| HD-C03 | P0 | 3000 / 6000 / 10000 / 18000（对 10170 帧源，18000 为越界请求） | patched NVEncC | 无静默丢失、无重复帧、ordered fingerprint 全等。越界请求必须**明确钳制到 EOF 且出声**，不得静默返回短结果 | `--frames N` 长程扫描 + 六方对账 + 指纹 | auto | |
| HD-C04 | P0 | 真实 A7M5 corpus 抽样（`sony_hs` + ≥12 个 `real_a7m5` 片段，覆盖最短/中位/最长） | patched NVEncC / QSVEncC | **research fixture 的正确性可迁移到真实拍摄素材**：全部片段 `avhw == container` 且指纹等值 | 批量扫描 + 逐片六方对账 + 指纹 | auto | |
| HD-C05 | P0 | `dji_0009`(105)、`dji_0010`(330)、`field_validate_昼间vlog手持晃动适中-dji`(790) | patched NVEncC / QSVEncC | exactness 全等（DJI leading=0，是 patch 的**阴性对照**：patch 不得改变控制组） | 同 C-02 | auto | |
| HD-C06 | P0 | `sony_422_*`（5 个样本） | NVEncC `avhw` → 输出 exact；QSVEncC → **capability refusal / software fallback** | NVENC：硬件输出与 `avsw` 全等；QSV：**拒绝而非丢帧**，`rc=−31`、无输出，`auto` 下落软件且软件输出正确 | 分别断言；QSV 侧显式断言"无输出文件 + reason code" | auto | |
| HD-C07 | P0 | `gen_x265_reencode`、`gen_synthetic_testsrc2`、`gen_sony_copy_mkv` | 两者 | integration **没有错误启用** hardware path 造成差异：控制组在 `auto` 下的决策与输出必须与 `avsw` 一致（x265/synthetic 应 `eligible` 且 exact；MKV 无 edit list 需明确决策） | 对三者在 `auto` 与 `off` 下各跑一次并对账 | auto | |
| HD-C08 | P0 | 全部 C-01…C-07 用例 | 全部 | 六方对账字段完整落盘（缺任一字段即 FAIL，不得静默跳过） | 检查结果 JSON 的字段完整性 | auto | |

### C.2 Negative integrity（**最容易被忽略、但决定本 integration 是否成立的一组**）

主动制造 hardware path 的丢帧/错序，确认 gate 能抓到。抓不到就是 **integration BLOCKED**。

| ID | Sev | Input | Backend | Expected behaviour | Verification method | Auto | Result |
|---|---|---|---|---|---|---|---|
| HD-C09 | P0 | `sony_hs_c0886`，用**已知 stock 缺陷**注入 N−3 | stock NVEncC `avhw`（真实注入） | gate 必须判 `FAIL`，reason code = `count_mismatch`；hardware 结果**被丢弃**；随后自动软件重跑并得到 exact 输出。**若 gate 检测不到 3 帧丢失 → integration BLOCKED** | 把 stock binary 当作"待检 hardware reader"喂给 integrity gate，断言 gate 判定与后续动作 | auto | |
| HD-C10 | P0 | 合成注入：保留帧数但重排/复制（用 ffmpeg 构造 N 帧但顺序不同的产物） | 注入产物 | 纯计数 gate **必须**放行不了的场景由 **sequence gate** 抓到：reason code = `sequence_mismatch`。若只能靠计数、sequence gate 抓不到 → 记 FAIL 并修 gate | 构造 count-preserving 的错误产物，分别喂给 count gate 与 sequence gate，断言后者抓到 | auto | |
| HD-C11 | P0 | 同上 | 注入产物 | 检测到失败后：hardware 结果**不被交付**；软件重跑；最终交付物 = 软件 exact 输出；日志中三段（hardware 失败 / 判定原因 / 软件重跑成功）齐备 | 端到端跑一次注入场景，检查交付物哈希与日志 | auto | |
| HD-C12 | P1 | 截断注入（删尾部 k 帧） | 注入产物 | 尾部截断同样被 count gate 抓到（不只有头部截断） | 构造尾部截断产物喂 gate | auto | |

### C.3 字节与哈希

| ID | Sev | Input | Backend | Expected behaviour | Verification method | Auto | Result |
|---|---|---|---|---|---|---|---|
| HD-C13 | P1 | `sony_hs_c0886`、`sony_422_c9037`、`dji_0009` | patched NVEncC | **适合的控制用例**上 `avhw` 输出 sha256 == `avsw` 输出 sha256（research 已在此三点上观察到字节相同） | 同参数两次编码 + sha256 比对 | auto | |

> **口径声明。** 字节相同**不是**所有用例的普遍要求：container muxer、
> 编码器 timing、metadata 排序都可能造成合法的字节级差异。因此
> **frame/content equality > byte equality**；byte equality 只作为额外强证据，
> 在 C-01/C-02 已给出内容等值的前提下才成立。

**已知限制（C 类）**

* `--frames N` 的语义问题**不在这里判**，见 D 类（独立矩阵）。
* stock binary 的丢帧复现是 C-01/C-11 的**对照前提**：若某次运行 stock 不再丢帧，
  本用例记 `BLOCKED`，不记 `PASS`（否则等于用一个失效的对照宣称检测能力）。
* 4K 长片段的全量指纹需要完整解码两次，是矩阵中最贵的一项；
  J 类抽样时对超长片段允许**分段抽样指纹**，但必须在结果中标注抽样范围。

---

## D. Temporal / seek / trim / `--frames` semantics

### D.0 已证事实（不得重新证明）

* rigaya 两个 patch 的守卫谓词都是 `seek > 0`，因此 **`--seek 0` 与"未 seek"不可区分**（open item #3）。
* Sony 上 `--frames N` 在**两个 reader 上都是 `N − leading_pictures`**，
  patch 前后完全相同，是**独立于本 patch 的既有语义**（open item #2）。
  同样的短少也出现在 `--trim` 上。

> ⚠️ **本 session 实测修正了上面两条的边界，并新增一条 research 未记录的发现。**
> 详见 §D.3。要点：`--frames` / `--trim` 上 hw 与 sw **字节相同**（安全）；
> **`--seek` 上两者不等价**（丢帧数相同、PTS 相同、画面不同）——这是 research
> 线没有测过的东西，因为 research 只验证了"seek 相对 stock 不变"，
> 从未验证"seek 与 `--avsw` 相等"。

### D.1 Seek / trim

| ID | Sev | Input | Backend | Expected behaviour | Verification method | Auto | Result |
|---|---|---|---|---|---|---|---|
| HD-D01 | P0 | `sony_hs_c0886`(360)、`sony_422_c9037`(195) | patched `avhw` vs `avsw` | full decode（无 seek/trim）逐帧等值 | 六方对账 + 指纹 | auto | |
| HD-D02 | P0 | `sony_hs_c0886` | patched `avhw` vs `avsw` | **契约经实测修正（原假设 hw==sw 被推翻）**：证明差异真实（同 reader 重复运行作为确定性对照）→ 因此 **routing 必须在请求 seek 时拒绝硬件解码** → 软件路径在每个 seek 点上精确 | 每个位置 hw×2 + sw×2 编码、指纹与 packet manifest 比对；再对路由函数断言 `seek_not_equivalent` | auto | |
| HD-D03 | P0 | `sony_hs_c0886` | 同上 | **trim** 至少 3 组：`0→N`、中段→N、近结尾。hw 与 sw **字节相同** | 成对编码 + sha256 + 帧数 | auto | |
| HD-D04 | P0 | 真实长片段 × 5–8 个位置 | 同上 | **repeated seek**：逐位置测量，并断言"计数与 PTS 相等但画面不同"这一形态可复现、且两侧各自确定 | 参数化扫描 + 同 reader 重复对照 | auto | |
| HD-D05 | P0 | D-01…D-04 的全部关键用例 | 同上 | **按操作分开给契约**：`--trim` → hw 与 sw 字节相同；`--seek` → reader 不等价，故硬件被拒绝（不是放宽判据，而是**不把不等价的路径接进生产**） | trim 比对 + seek 测量 + 路由断言；若某天 seek 变成等价，本条会**主动报错**提示守卫过严 | auto | |
| HD-D06 | P1 | `sony_hs_c0886` | patched `avhw` | **`--seek 0` 边界**：显式 `--seek 0` 与不传 seek 的行为差异被**明确记录**（research 判定该差异"未在任何验证中可观测"）。本测试把该边界变成**已测事实** | 显式 `--seek 0` vs 无 seek，比对输出与 reader 行为 | auto | |

### D.3 `--seek` 不等价 —— 本 session 的独立发现（新）

**结论：在时间 seek 上，rigaya 硬件 reader 与软件 reader 交付的是不同的画面。**

| 观测 | 值 |
|---|---|
| 帧数 | 相等（例如 `--seek 0.5 --frames 16` 两侧各 13 帧） |
| PTS 序列 | **完全相同**（`0, 16016, 8008, 4004, 12012, …`） |
| keyframe 索引 | 相同 |
| 画面内容 | **13/13 帧不同**，mean 偏差最大 **252**（10-bit 域 0–1023） |
| 同 reader 重复运行 | hw×2 与 sw×2 各自**字节相同** → 差异不是编码器随机性 |
| `patched --avhw` vs `stock --avhw` | **字节相同**（`73b9610a…`）→ **patch 不是原因** |
| full encode（无 seek） | hw 与 sw **字节相同**（`ef621733…`） |
| `--frames` / `--trim` | hw 与 sw **字节相同** |

**因果结论**：patch 按设计在"有 seek"时**原样运行原过滤器**，所以 patched seek ==
stock seek，逐字节相同；差异来自 rigaya reader 在时间 seek 上的既有语义，
**不是本 patch 引入的**。research 线只验证了"seek 相对 stock 不变"（3/3），
从未把它与 `--avsw` 对比，因此该差异此前未被记录。

**工程处置（不是放宽判据）**：`route_decode(seek_requested=True)` 直接拒绝硬件解码，
reason code = `seek_not_equivalent`，并出声。于是"硬件解码不得改变交付画面"
这一生产契约**由构造保证**，而不是靠事后检测。

**实际影响**：**零**。production 代码从不使用 `--seek`、`--trim`、`--frames`
（`git grep` 全库只有本 harness 自身命中）。

**尚未解释**：两个 reader 在 seek 上为何选到不同画面。已排除：编码器随机性、
patch 引入、帧数/PTS 差异、常量偏移对齐（不是简单的窗口平移）。
本 session 不为此展开新的 root-cause 考古——它不影响生产路径，
且已被守卫覆盖。记为 open follow-up。

### D.2 `--frames N` 独立矩阵（**不得与 full-input 完整性混为一谈**）

这一组的目标是得到一个**清晰的 contract**：`--frames N` 的 N 究竟代表什么。

| ID | Sev | Input | Backend | Expected behaviour | Verification method | Auto | Result |
|---|---|---|---|---|---|---|---|
| HD-D07 | P0 | `sony_hs_c0886`（leading=3）、`gen_x265_reencode`（leading=0） | patched NVEncC `avhw` + `avsw` | 记录 `N ∈ {1,2,3,4,10,first-keyframe-boundary,30,100}` 的 **requested vs presented**，**并对每个 N 比较 hw 与 sw 的画面内容**（`--frames` 上两者必须等价，与 `--seek` 形成对照）。结果作为**独立 semantics 表**输出。**不得**把 `actual < requested` 一律判成同一个 bug | 参数化扫描 + 逐 N 指纹比对，输出 contract 表 | auto | |
| HD-D08 | P0 | 同上 | 同上 | contract 落盘，按**实测的三段 regime** 判定（见 §D.4），并断言 hw/sw 逐 N 等价、control（leading=0）精确。若最终 production pipeline **不依赖** `--frames`，本条以 `known limitation / isolated behaviour` 记录，并**明确标注它不影响 full-input integrity 结果** | 生成并检查 contract 表 | auto | |
| HD-D09 | P1 | 同上 | 同上 | `--trim` 的同类语义与 `--frames` 分开记录（两者都短少 leading，但触发路径不同） | 同 D-08 形式 | auto | |

### D.4 `--frames N` 的实测 contract（修正原假设）

原假设 `presented == N − leading` **只在一个 regime 内成立**。实测得到三段：

| regime | 条件 | `presented` | 证据 |
|---|---|---|---|
| 低于 leading | `N ≤ leading_pictures` | **整个片段**（请求被忽略） | N=1、2、3 均交付 **360** 帧 |
| 常规 | `leading < N < container` | `N − leading_pictures` | N=4→1、10→7、30→27、100→97 |
| 越界钳制 | `N ≥ container` | **整个片段**（含 leading，短少消失） | 对 11280 帧片段请求 18000 → **11280** |
| 控制组 | `leading_pictures = 0` | `N` 精确 | N=1/3/10/30/100 全部精确 |

**关键性质：三个 regime 上 hw 与 sw 完全一致**（`--frames 10/30/100` 两侧 sha256 相同）。
因此 `--frames` 语义是 **reader 共有**的行为，与 `--seek` 的性质完全不同——
这也是为什么 `--seek` 需要守卫而 `--frames` 不需要。

---

## E. Video metadata / preservation

| ID | Sev | Input | Backend | Expected behaviour | Verification method | Auto | Result |
|---|---|---|---|---|---|---|---|
| HD-E01 | P0 | `sony_hs_c0886`、`sony_422_c9037` | patched `avhw` vs `avsw` | 输出 3840×2160 保持；SAR `1:1`、DAR `16:9` 与源一致（**不得**出现 aspect-ratio-preserving `--output-res` 类副作用） | ffprobe 字段逐项比对 | auto | |
| HD-E02 | P0 | 同 E-01 | 同上 | **10-bit 保持 10-bit**（pix_fmt 与 `--output-depth` 一致） | 同上 | auto | |
| HD-E03 | P0 | `sony_hs_c0886`(4:2:0)、`sony_422_c9037`(4:2:2) | 同上 | 4:2:0 保持 4:2:0；4:2:2 在支持路径上保持 4:2:2（NVENC 直编），不支持路径上**有记录的**降级（QSV 转 420 是既定策略，须出声） | 同上 + 降级记录断言 | auto | |
| HD-E04 | P0 | 同 E-01 | 同上 | colour metadata 保持：`range` / `colorspace` / `primaries` / `transfer`（`tv/bt709/bt709/bt709`）；HDR 元数据在适用片段上保持 | ffprobe 字段比对 + 容器 `colr` box 比对 | auto | |
| HD-E05 | P0 | 同 E-01 | 同上 | **frame timing**：PTS 严格单调；duration 与源一致（±1 帧间隔内）；帧间隔符合 `60000/1001`（或源帧率）；**无意外时间戳平移** | PTS 全序列 + duration + 间隔统计 | auto | |
| HD-E06 | P0 | `sony_hs_c0886` 经保留管线 | patched NVEncC | **不得重新引入 GPAC `colr` 错误**：产物视频轨 `colr` 与源逐字段一致 | 用 `preservation/isobmf.py` 读 `stsd/colr` 比对 | auto | |
| HD-E07 | P1 | 同上 | 同上 | 容器级保留（brands / uuid / rtmd / nrtm）与集成前一致；XAVC brand 在 HEVC 路径恢复 | 跑保留管线 `--check advanced` 并比对 report.json | auto | |

---

## F. Backend fallback / failure handling

**这一类存在的理由。** 只测成功路径等于没测。回退链是"检测到失败"与"交付正确结果"之间
唯一的东西；它必须**能触发**、**能终止**、**必须出声**。

| ID | Sev | Input | Backend | Expected behaviour | Verification method | Auto | Result |
|---|---|---|---|---|---|---|---|
| HD-F01 | P0 | 任意 eligible 输入 | 两者 | **hardware unavailable**（设备不可用 / 无 GPU 设备）：明确 reason code（`reader_unavailable`）→ 软件路径 → 成功且 exact | 用不可用 device 选择器强制不可用；断言 reason code + 产物正确 | auto | |
| HD-F02 | P0 | `sony_422_c9037` | QSVEncC | **capability rejection**：硬件拒绝（`rc=−31`）→ 软件路径 → exact。拒绝必须与丢帧区分（B-09） | 实跑 + 断言 | auto | |
| HD-F03 | P0 | 任意输入 | 两者 | **startup failure**（binary 无法启动 / 缺依赖）：分类为 `startup_failed` → 回退，不得误判为 integrity 失败 | 用破坏的 binary 副本触发 | auto | |
| HD-F04 | P0 | 任意 eligible 输入 | 两者 | **runtime decode failure**（启动成功、解码中途失败）：经 integrity/error 分类 → 回退 → exact 输出 | 用截断/损坏输入或 test hook 触发 | auto | |
| HD-F05 | P0 | stock NVEncC 注入 N−3 | NVEncC | **integrity failure** 最重要的一条：gate 检出 → hardware 结果**被丢弃**（产物不进入交付路径）→ 软件重跑 → 软件 exact | 端到端注入场景（与 C-11/C-13 共用） | auto | |
| HD-F06 | P0 | 连续制造失败 | 两者 | **double fallback protection**：不得出现 `hardware fail → software fail → 无限 retry`。必须有明确终止条件（每次运行的回退次数上限 + 明确 FATAL） | 让硬件与软件路径都可控地失败，断言运行在有限步内以 FATAL 结束且失败被记录 | auto | |
| HD-F07 | P0 | 任一发生回退的运行 | 两者 | **回退必须出声**：WARNING + reason code 进主日志、per-file 日志、CSV/report。**silent fallback 判 P0 FAIL** | 检查三处记录同时存在 | auto | |
| HD-F08 | P0 | 同 F-01…F-05 | 两者 | 回退后的软件输出**与直接软件路径的输出逐字节相同**（回退不引入差异） | 比对两次运行产物 sha256 | auto | |

**已知限制（F 类）**

* "device unavailable" 的制造方式受单 GPU 主机限制（Q4/Open item #5）；
  本矩阵用**不可用 device id** 与**破坏的 binary 副本**制造，不用多 GPU。
  多 GPU 下的设备选择仍是 follow-up（P2）。

---

## G. Existing feature interaction

**这一类存在的理由。** integration 的价值前提是"不改变现有正确行为"。

| ID | Sev | Input | Backend | Expected behaviour | Verification method | Auto | Result |
|---|---|---|---|---|---|---|---|
| HD-G01 | P0 | 已对齐的多轨 PCM 素材 | patched NVEncC | channel-sync **结果与集成前逐字节一致**：已对齐素材保持 untouched，`--channel-sync-transparent` 输出与源 SHA256 相同 | 与集成前基线比对 + 透明模式字节等价断言 | auto | |
| HD-G02 | P0 | 固定 delay 素材 | 同上 | 修正量、修正后样本与集成前一致 | 比对 sync 决策 JSON 与输出音频 sha256 | auto | |
| HD-G03 | P0 | 含空轨/不可用轨素材 | 同上 | 轨道级降级行为不变（空轨保持 `untouched`，不阻止其它轨同步） | 同上 | auto | |
| HD-G04 | P0 | 多轨素材 | 同上 | **hardware video + software audio**：视频走硬件解码、音频路径完全不受影响 | 断言音频流 sha256 与软件路径一致 | auto | |
| HD-G05 | P0 | 多轨素材 + 强制 hardware 失败 | 同上 | **hardware fallback + audio path**：回退后音频结果仍与基线一致 | 端到端跑一次注入场景 | auto | |
| HD-G06 | P0 | `tests/fixtures/channel_sync/a7m5_real_137_baseline.csv`（137 段冻结基线） | 同上 | 冻结基线回归不变 | 跑现有 selfcheck 并比对基线 CSV | auto | |
| HD-G07 | P1 | `sony_hs_c0886` | 同上 | production 输出色彩保持（E-04 的管线级版本） | 跑保留管线 + postprobe 比对 | auto | |
| HD-G08 | P1 | 多音轨素材 | 同上 | 音频保持：stream count、channel count、sample rate、channel layout 全等 | ffprobe 全字段比对 | auto | |
| HD-G09 | P1 | 多流输入（4 audio + 1 rtmd + 1 video） | 同上 | multi-stream 输入的基础回归：交付物流清单与集成前一致 | 流清单比对 | auto | |

---

## H. Resume / retry / interruption

| ID | Sev | Input | Backend | Expected behaviour | Verification method | Auto | Result |
|---|---|---|---|---|---|---|---|
| HD-H01 | P0 | `sony_hs_c0886` | patched NVEncC | **resume after success**：成功的硬件任务被 resume 时**不重复计算**且结果正确（`report.json` 的 `structural_success` 判定链不被破坏） | 连续跑两次，第二次断言跳过且产物哈希不变 | auto | |
| HD-H02 | P0 | 同 H-01 + 强制硬件失败 | 同上 | **resume after fallback**：state 一致——失败运行的缓存报告**不得**阻止重试；回退后的成功运行可被正确 resume | 注入失败 → 重跑成功 → 再 resume | auto | |
| HD-H03 | P0 | 长片段 + hardware | 同上 | **interrupted job**：受控中断（terminate）后临时文件按约定清理/保留、状态正确标记、**不得出现 false success** | 启动后中断，检查 work 目录与 report.json | auto | |
| HD-H04 | P0 | 任意输入 + 强制硬件失败 | 同上 | **retry after hardware failure**：hardware 失败 → retry → 软件成功；retry 不重复已完成的阶段 | 断言阶段执行顺序 | auto | |
| HD-H05 | P0 | 已有 partial hardware artifact 的输入 | 同上 | **re-run same input**：不会因为残留的 partial hardware 产物产生**错误 resume**（把不完整产物当成完成） | 手工放置 partial 产物后重跑，断言重新执行 | auto | |

---

## I. Concurrency / resource pressure

**不一开始做极限压力**，按顺序加压。I-05 **只作为 stress / known limitation**，
不作为 normal production pass criterion。

| ID | Sev | Input | Backend | Expected behaviour | Verification method | Auto | Result |
|---|---|---|---|---|---|---|---|
| HD-I01 | P1 | `sony_hs_c0886` ×3 重复 | NVENC hw / QSV hw / software control | 单任务正确性：三者各自 exact | 3 次独立运行（重复策略）+ 六方对账 | auto | |
| HD-I02 | P1 | 2 个并发任务 | 同 backend hw | 正确性保持；资源争用被观测；**device selection 不串**；**logging isolation**（两任务的日志不交叉、写到各自 per-file 日志） | 并发跑 + 逐任务校验产物与日志归属 | auto | |
| HD-I03 | P1 | 1 hw + 1 sw 并发 | 混合 | CPU/GPU 争用**不触发错误 fallback**（不得因为变慢而判失败） | 并发跑 + 断言无 `count/sequence` 误判、无 reason code 误分类 | auto | |
| HD-I04 | P1 | 合理范围内制造 GPU 资源不可用 | hw | 失败分类正确、可安全退出或回退、**无损坏输出** | 制造资源耗尽后断言分类与产物状态 | auto | |
| HD-I05 | P2 | 4-way 4K 并发 | hw | **只验证**：不崩溃、不数据损坏、有资源错误时可安全退出/回退。**不要求**达到特定 throughput。已知：RTX 5070 Laptop 8 GB 上 4-way 4K 明显下降（8.01 fps aggregate，机制未证） | 4 并发跑 + 完整性校验；记录 fps 仅作参考 | auto | |

---

## J. Production / long-run validation

最终阶段才执行。

| ID | Sev | Input | Backend | Expected behaviour | Verification method | Auto | Result |
|---|---|---|---|---|---|---|---|
| HD-J01 | P1 | 每 backend ≥10 cases（短） | both + software | 短程 smoke 全通过且六方对账完整 | 批量 | auto | |
| HD-J02 | P1 | 20–30 cases（中等长度） | both | 中程矩阵全通过 | 批量 | auto | |
| HD-J03 | P1 | 真实 A7M5 corpus：≥10 短 / ≥10 中 / ≥10 长（实际可用 42 片，尽量扩大） | patched NVEncC（主）/ QSVEncC | 真实素材端到端 integrity 保持 | 批量 + 抽样指纹 | auto | |
| HD-J04 | P1 | 真实 DJI corpus（`dji_0009`/`dji_0010`/`field_validate_*dji`） | 两者 | 同上 | 批量 | auto | |
| HD-J05 | P1 | `long_a` / `long_b`（35967 samples each）+ `field_stress_*`（11280 / 3390） | patched NVEncC | 连续长程跑：记录 wall clock、CPU time、peak RSS、GPU utilization、可得时的 VRAM、frame integrity、final hash。≥1 次干净全程 | 长程运行 + 遥测采样 | auto | |
| HD-J06 | P0 | 全部 | 全部 | **full regression + machine-readable summary**：输出 Total/PASS/FAIL/BLOCKED/SKIP 与 P0/P1/P2 分级统计。**P0 出现 FAIL 或 BLOCKED ⇒ 最终状态 BLOCKED** | harness `summary` 命令 | auto | |

---

## K. Golden baseline（判定基准的来源）

| ID | Sev | 内容 | 说明 |
|---|---|---|---|
| HD-K01 | P0 | software decode golden baseline | 对每个 C/D/E/G 用例保存**软件解码**基线：frame count、ordered fingerprint sequence、PTS、duration、video metadata、audio metadata。所有 hardware 路径与它比较 |
| HD-K02 | P0 | baseline 自身有效性 | **software baseline 自身必须经过现有 regression 验证，不能盲目信任单次输出**：基线要求在 ≥2 次独立运行下自洽（frame count/指纹/duration 完全一致），否则 baseline 记 `UNSTABLE` 并逐项排查 |

**原则**

```
software = correctness oracle
hardware = candidate optimization
```

---

## L. 执行顺序（严格）

```
Phase 1   A toolchain + B routing
   ↓
Phase 2   C frame integrity（P0）+ D seek / trim / --frames
   ↓            ← 若 Phase 2 的 P0 integrity 未通过：不继续做性能优化
Phase 3   E preservation + F fallback
   ↓
Phase 4   G feature interaction + H resume / retry
   ↓
Phase 5   I concurrency
   ↓
Phase 6   J real corpus / long-run
   ↓
          final full regression + summary gate
```

---

## M. Final matrix gate

机器可读 summary 必须给出：

```
Total / PASS / FAIL / BLOCKED / SKIP
P0 PASS / P1 PASS / P2 PASS
```

判定规则：

* **integration 不得因为"绝大多数测试通过"而宣布完成。**
* 以下任意一项为真 ⇒ 最终状态 **`BLOCKED`**：
  * 任何 P0 `FAIL`
  * 任何 P0 `BLOCKED`
* 否则最终状态为 `READY FOR REVIEW`。

---

## N. 结果表（执行后回填）

> 由 `python -m tests.hwdecode.harness summary` 生成，写入
> `work/avhw_integration/results/`，并在
> [`final-report.md`](final-report.md) 中汇总。

| Category | Tests | PASS | FAIL | BLOCKED | SKIP | P0 status |
|---|---|---|---|---|---|---|
| A Toolchain | 9 | | | | | |
| B Routing | 10 | | | | | |
| C Frame integrity | 13 | | | | | |
| D Seek / trim / --frames | 9 | | | | | |
| E Preservation | 7 | | | | | |
| F Fallback | 8 | | | | | |
| G Feature interaction | 9 | | | | | |
| H Resume / retry | 5 | | | | | |
| I Concurrency | 5 | | | | | |
| J Long-run / corpus | 6 | | | | | |
| K Golden baseline | 2 | | | | | |
| **Total** | **83** | | | | | |
