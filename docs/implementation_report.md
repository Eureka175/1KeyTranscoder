# 1KeyTranscoder S1–S5 实施报告

> 状态：完成（全部测试通过）
> 基线：git tag `pre_S1S5`（commit `4e3bc6a`）；物理备份
> `backup/pre_S1S5_20260829_000550/`（64 个核心文件）
> 关联设计文档：`docs/hardware_backend_design.md`（第 10 节最终定稿）
> 回滚方式：`git checkout pre_S1S5` 或从 backup 目录恢复

---

## 0. 摘要

S1–S5 全部落地并通过 testsets 全量实测：

| 验证项 | 结果 |
|---|---|
| NVENC HQ，testsets 全部 15 文件 | **15/15 通过**（10 Sony 保留 + 4 A7M4 + 1 DJI 经典路径），exit 0 |
| Sony 保留路径结构校验 | A7M5 类 **40/0/0**、A7M4 **41/0/0**（0 MODIFIED） |
| Gyroflow 消费端校验 | 全 PASS（含 188s 长片 376376 IMU 样本） |
| QSV 后端（单文件端到端） | 41/0/0 + Gyroflow PASS |
| 详细 Sony 元数据自检（NVENC/QSV） | 各 **64 项检查 0 失败**，log 入盘 |
| watchfolder + 启动器 | `--once` 实测 rc=0 |
| 落盘最小化 | 全成功后 `.1ktwork` 仅剩 `caps/`；14 份保留报告入 `logs/preserve_reports/` |

实现过程中发现并修复的**新缺陷**（超出原设计范围）：
1. rigaya mp4 muxer 毫秒级 elst 截断（0.5ms，A7M4 实测触发）→
   stts 基准字节补丁（`patch_track_durations(from_stts=True)` +
   `patch_movie_duration`）已在管线 validate 前生效，33.5s/56.6s
   A7M5 素材上也真实修复了该截断；
2. GPAC `-new` 忽略前置 `-timescale`（movie 时基 = 第一轨媒体时基）、
   `-flat` 重置时基、合并调用中 per-op `-timescale` 不生效 → 趟数
   合并方案调整为 **8→5**（-ref 单独跑、set-meta+add-item+set-xml
   合并、flatten/brand 各自单独）；
3. GPAC 对无 elst 轨的导入缺陷（`:noedit`/`use_editlist:0` 均不可用，
   已在设计文档 6.3 记录）。

## 1. 交付清单

| 交付物 | 说明 |
|---|---|
| `backup/pre_S1S5_20260829_000550/` | 核心文件物理备份（64 文件） |
| git tag `pre_S1S5` | 回滚基线 |
| `preservation/isobmf.py` | 新增 `patch_meta_item_type`、`patch_movie_duration`、`patch_track_durations(from_stts=...)`、`_stts_sum` |
| `preservation/pipeline.py` | `fix_hw_timing` 参数；stts 时长修复 + lens item_type 补丁接入 validate 前；flatten/brand 拆回单独趟；validate 传 known_facts |
| `preservation/sony.py` | 删除 NHML dump；samples.bin 哈希即删；reconstruct 改 meta_pass 合并 |
| `preservation/gpac.py` | 新增 `meta_pass`（set-meta+add-item+set-xml 单趟合并） |
| `preservation/validate.py` | `FileFacts`/`compare` 支持 known_facts（跳过 original 侧载荷 re-demux） |
| `encoders/caps.py` | 新模块：能力探测（文本解析）/缓存（工作目录，每次启动查询存盘）/supports/downgrade_ladder |
| `encoders/hw.py` | 新模块：run_hw_tool（进度解析）/失败分类/新控制台 60s 询问/旗标白名单/格式规划 |
| `encoders/nvencc.py` | NVEncC 后端：nvenc.json → CLI（白名单守护） |
| `encoders/qsvencc.py` | QSVEncC 后端：qsv.json → CLI（真实旗标名映射） |
| `1keytransc.py` | `--encoder/--keep-work/--auto-downgrade/--no-downgrade/--tool-*`；后端选择接线；Sony 硬件保留路径；经典路径硬件单趟；GC + preserve_reports；VFR 检测 |
| `core/config.py` | `find_hw_tool` |
| `tests/sony_selfcheck.py` | 详细 Sony 元数据对比（64 项），JSON+文本 log 入盘 |
| `tests/run_selfcheck.py` | 自检运行器：主程序输出 + 内置校验 + 详细对比 |
| `watchfolder.py` / `start.bat` | barebone watchfolder + 启动器 |
| `docs/implementation_report.md` | 本文档 |

## 2. S1：A7M5 检查全通（lens item_type 补丁）

- **根因**：源文件 infe v0（GPAC 渲染 item_type=00000000）；GPAC
  `-add-item` 写 v2 infe、item_type=`mime` → validate 报 MODIFIED。
- **实现**：`isobmf.patch_meta_item_type(path, item_id, target)`——
  遍历根级与 moov 内 meta box 的 iinf 条目（v2 布局 item_type 位于
  `data_offset+8`），4 字节写回源值；幂等；目标值取自 bundle
  `nrtm.item_type`（8 位 hex 或 4CC 解析，无法解析则跳过 + WARNING）。
- **接线**：`pipeline.py` 在 uuid 补丁后、validate 前执行（x265 与
  硬件路径共用；值已精确时为 no-op）。
- **实测**：A7M5 从 40/1/0 → **41/0/0**；全矩阵验证通过。

## 3. S2：落盘最小化与能力缓存

| 措施 | 实现 | 实测 |
|---|---|---|
| 成功后 GC | `cleanup_work_dir()`：report.json → `logs/preserve_reports/<job>.json` → rmtree；重试 3 次后仅 WARNING；失败任务永不 GC；`--keep-work` 保留 | 全成功后 `.1ktwork` 仅剩 `caps/`，保留报告 14 份 |
| 去 NHML | `sony.extract` 不再 dump NHML；manifest 兼容旧字段 | 全矩阵通过 |
| samples.bin 哈希即删 | extract 完成 sha256+stsz 校验后立即删除 | 全矩阵通过（哈希保留在 manifest，reconstruct 校验用 stage 侧瞬态 dump） |
| validate known_facts | `compare(known_facts=...)`：original 侧 rtmd/lens/XML 哈希复用 bundle，不再 re-demux 原始文件 | 全矩阵通过 |
| 能力缓存 | `encoders/caps.py`：启动时 `--check-features` → 解析 → `<output>/.1ktwork/caps/<backend>_caps.json` + 原始文本；解析失败 → 保守能力（8bit420）+ WARNING；键 = 工具版本+驱动+设备 | caps 正确解析（RTX 5070：hevc 10bit/422/422-10bit 全 True） |
| warning 框架 | `emit_warning()`：总日志 + per-file 日志 + 控制台 + report warnings 列表 | 每次降级/跳过均有可审计记录 |

## 4. S3：硬件后端与三段式降级链

- **后端选择**：`--encoder {x265,nvenc,qsv}`（缺省按 config 的
  `encoder` 字段；x265 仅手动选择，永不自动回退软件）。
- **解码恒软解**：所有硬件命令固定 `--avsw` + `--video-track 1`。
- **旗标白名单**：每个 JSON 键映射到 CLI 旗标前先对照工具 `--help`
  广播列表；未广播 → 跳过 + 显著 WARNING。实测：NVENC 全参数面
  接受（含 `--(no-)aq` 约定）；QSV 9 键已按 8.26 真实名映射
  （`--i-adapt/--b-adapt/--adapt-ltr/--adapt-cqm/--weightp/--weightb/
  --ctu/--tskip/--hevc-gpb`），仅 `tu/tu_level/mbrc/output_buf_mb`
  无 8.26 对应（跳过+WARNING，供工具升级后启用）。
- **三段式降级链**（`hw_encode_with_fallback`）：
  1. 能力预判不可编码 → 直接降级（WARNING + 记录；`--no-downgrade`
     时跳过文件）；
  2. 运行时失败 → 失败分类（reader/format/environment）→ reader 走
     MP4Box strip 回退一次；format 走新控制台 60s 询问（决策文件
     回传，主控制台只留进度；`--auto-downgrade` 免询问）→ 降级梯
     （源格式→10bit420→8bit420，逐级 WARNING）→ 全失败 FATAL；
     environment 立即 FATAL；
  3. VFR 检测（r_frame_rate ≠ avg_frame_rate）→ WARNING +
     `--avsync forcecfr`（最近有理速率 CFR）。
- **Sony 路径**：encode_video 回调 = 硬件 runner（视频-only mp4）+
  1:1 帧数/帧率闸门；管线 `fix_hw_timing=True` 启用 stts 时长修复。
- **实测**：见第 7 节。降级梯在本次素材集未触发（全部能力内），
  其逻辑经失败注入路径（修复前的 flag 问题）实际走过并正确输出
  WARNING 序列。

## 5. S4：经典路径

- 非 Sony 素材：单工具单趟 `--audio-copy`（全部音轨复制）+ `-f mp4`
  显式；无 1:1 闸门（VFR 素材不误杀）；reader 失败 → strip（视频+
  音频逐轨复制）回退；日志头显式策略声明（"non-Sony: metadata
  dropped by policy"）。
- **实测**：DJI 素材（昼间vlog 6 流含 covr/tmcd/djmd）走经典路径
  成功交付（41.6% 体积比），元数据按策略丢弃、可审计。

## 6. S5：MP4Box 趟数合并（含 GPAC 时基行为新发现）

**新发现（实测二分定位，GPAC 26.02）**：
- `-new` 忽略前置 `-timescale`，movie 时基 = 第一轨媒体时基
  （A7M4：90000 源 → 30000）；
- `-ref`（单独调用）会把 movie 时基重置为 `-timescale` 值；
- `-flat` 又把时基重置回第一轨媒体时基；
- `-brand`（单独调用）再恢复；
- **合并调用中 per-op `-timescale` 不生效**（`:noedit` 与
  `use_editlist:0` 亦触发 GPAC 无 elst 导入缺陷，不可用）。

**最终方案（8→5 趟）**：
```
1. mux_new (-timescale)                          [-new: 时基=第一轨]
2. -ref (单独, 每个 meta 轨一次)                  [恢复 movie 时基 90000]
3. meta_pass: -set-meta + -add-item* + -set-xml (单趟合并, 时基保持)
4. flatten (单独)                                [时基重置, moov 移后]
5. brand (单独)                                  [恢复 movie 时基]
(+ uuid 字节补丁 / stts 时长修复 / lens 补丁 —— 字节级, 不计趟数)
```
实测：A7M4 全矩阵 41/0/0（movie_timescale 90000 PRESERVED）。

## 7. testsets 全量测试结果（NVENC HQ）

| 文件 | 类型 | 结果 | Gyroflow |
|---|---|---|---|
| C9037 / C9073 / C9088 / C9110（A7M4 4K30 h264 10bit422） | Sony 保留 | **41/0/0** ✅（4 个） | PASS |
| 20260823_C0887（A7M5 4K30 h264 10bit422） | Sony 保留 | **40/0/0** ✅ | PASS（13013 IMU） |
| 20260823_C0886（A7M5 4K60 XAVC HS） | Sony 保留 | **41/0/0** ✅ | PASS（12012 IMU） |
| 夜间手持高噪点（33.5s） | Sony 保留 | **40/0/0** ✅（stts 修复 30 ticks） | PASS（67067 IMU） |
| 昼间手持几乎无噪点（17s） | Sony 保留 | 40/0/0 ✅ | PASS |
| 车内高晃动适中噪点（29s） | Sony 保留 | 40/0/0 ✅ | PASS（58058 IMU） |
| 阴天手持高晃动（12s） | Sony 保留 | 40/0/0 ✅ | PASS |
| stress 多场景长片（188s） | Sony 保留 | 40/0/0 ✅ | PASS（376376 IMU） |
| stress 长片 2（56.6s） | Sony 保留 | 40/0/0 ✅（stts 修复 30 ticks） | PASS（113113 IMU） |
| 夜间室内变焦 / 昼间车内（validate） | Sony 保留 | 40/0/0 ✅ | PASS |
| 昼间vlog-dji | 经典路径 | done（41.6% 体积比）✅ | —（非 Sony 策略） |

汇总：**done=15 failed=0**（exit 0）。断点续跑实测：失败任务的
bundle/encoded.mov 复用正常（"previous run did not pass validation;
rebuilding" 后秒级完成 mux）。

QSV 单文件端到端（A7M5 6s）：**41/0/0 + Gyroflow PASS**。
（x265 路径按约定未测试、未改动。）

## 8. 详细 Sony 元数据自检（首要目标）

- `tests/sony_selfcheck.py::detailed_compare()`：**64 项**对比——
  ftyp 品牌、mvhd 时基/时长、轨清单、视频轨（时基/stts/stsd/elst/
  tkhd/帧数/帧率/分辨率/编码）、逐音频轨（sample entry/时基/mdhd/
  tkhd/stts 求和/编码/采样率/声道）、rtmd 轨（stsd/handler/时基/
  stts/stsz 全表/elst/样本数/**载荷 sha256+首尾 32 字节**/tref-cdsc/
  timecode tag）、nrtm（hdlr/item 字段/**lens 载荷 sha256+size**/
  **XML sha256**）、uuid 清单（PROF/USMT/未知，sha256+上下文）、
  Gyroflow 消费端解析（IMU 样本数/机型/lens/帧率/读出时间）。
- 落盘：`selfcheck_<stem>.json` + `.txt`（逐项 PASS/FAIL 明细）。
- `tests/run_selfcheck.py`：主程序退出码 + 交付物存在 + 内置校验
  （preservation report 的 structural_success + Gyroflow）+ 详细对比，
  汇总 JSON 入盘。
- **实测：NVENC 与 QSV 均 5/5 项 PASS、详细对比 64/64 PASS。**

## 9. watchfolder 与启动器

- `watchfolder.py`：每 `--interval` 秒把主程序作为子进程跑一轮
  （续跑逻辑使重复轮询近零开销）；`--once` 供测试；Ctrl+C 停止。
- `start.bat`：设置输入/输出目录、编码器、档位、轮询间隔后启动
  watchfolder（`--auto-downgrade` 无人值守）。
- **实测**：`watchfolder.py --once`（NVENC，单文件）rc=0，输出交付。

## 10. 已知问题与后续

| 项 | 状态 |
|---|---|
| QSV `tu/tu_level/mbrc/output_buf_mb` 在 QSVEncC 8.26 无对应旗标 | 白名单跳过 + WARNING（工具升级后自动启用） |
| 硬解 reader 丢帧（设计文档 5.2 矩阵） | 已按定稿用恒软解规避；NVEncC 升级后复测 |
| rigaya ms-elst 截断 | 已由 stts 补丁修复（管线内置，幂等） |
| 降级梯/新控制台询问在素材集上未自然触发 | 代码路径经失败注入验证过 WARNING 序列；建议后续用强制失败做一次完整演练 |
| `--jobs N` 并行 | 未做（后续项） |
| DJI 专线 | 未做（按定稿预留） |

## 附录：关键命令

```powershell
# 全量测试（NVENC HQ）
python 1keytransc.py --input testsets --output work\final_test_nvenc --encoder nvenc --preset hq --auto-downgrade

# 自检（内部测试）
python tests\run_selfcheck.py --encoder nvenc
python tests\run_selfcheck.py --encoder qsv

# watchfolder
python watchfolder.py --input <in> --output <out> --encoder nvenc --preset hq --interval 60 --auto-downgrade

# 回滚
git checkout pre_S1S5
```
