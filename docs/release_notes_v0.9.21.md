# 1KeyTranscoder v0.9.21 发布说明

> 2026-11-23 · DJI 分段连接改为渐进式：一组拼完即编码，下一组在上一组编码期间后台拼接，落盘峰值从“全部组之和”降到“当前组 + 下一组”

## 1. 问题

2026-11-15 目录（13 个 DJI 分段，157.12 GB，4 组）在 F 盘 118.6 GB 可用空间上
执行：

```powershell
python 1kt.py --input "F:\Pictures\2026\202611\20261115" ^
  --output "F:\Pictures\2026\202611\20261115_c" ^
  --preset small --encoder nvenc --no-metadata --dji-join
```

失败于 MP4Box：

```text
Error writing data (No space left on device)
Error: I/O Error
```

不是源文件/编码器/元数据问题，而是连接阶段的磁盘峰值：

1. 旧逻辑在编码前先连接 **全部 4 组**，joined 文件合计约 149.4 GB；
2. 旧的按轨连接需要“每段视频轨抽取 + 视频 concat + 最终 mux”三份中间产物，
   瞬时峰值约为 joined 大小的 **3 倍**；
3. GPAC `-new` 默认还会在目标旁写一份同体积临时文件，再叠加约 **2 倍**。

## 2. 新逻辑

### 2.1 渐进式 staging（`core.dji_join.ProgressiveStager`）

1. 检测分组，只先连接 **第 1 组**；连接完成立即开始编码；
2. 第 N 组编码开始时，后台线程开始连接第 N+1 组；
3. 第 N 组输出交付后，**立即删除**它的 joined 源；
4. 重复到最后一组。

硬性约束：`ensure_ready()` 保证第 N 组没有编码完成前，第 N+1 组不会开始编码，
因此磁盘上最多只有 **两个 joined 组**（当前 + 下一组），而不是全部组。

### 2.2 单趟无损连接：`-cat -unalign-cat -flat`

连接从“三段式”改为 **一趟 MP4Box**：

```text
MP4Box -flat -cat seg1 -cat seg2 ... -unalign-cat -new joined.mp4
```

- `-unalign-cat`：避免按 movie duration 对齐造成的边界一帧空隙；视频保持严格
  CFR `30000/1001` / `time_base=1/30000`，帧数 = 各段之和；
- `-flat`：MP4Box 直接写目标文件，不再生成同体积临时文件；
- `djmd` / `dbgi` / `tmcd` 由同一趟 `-cat` 原样带走。

结果：连接过程峰值落盘 = **1 倍 joined 大小**（旧实现约 3 倍 + 临时文件）。

## 3. 实测

真实分段 0025（16.03 GB）+ 0026（4.24 GB）：

| 项 | 结果 |
|---|---|
| 连接耗时 | 81.6 s |
| joined 输出 | 21.76 GB（20.27 GiB） |
| 实际落盘增量 | 20.27 GiB（**1x**，无 GPAC 临时文件） |
| 视频 | 30000/1001 CFR，`time_base=1/30000`，**61,167 帧** |
| 数据轨 | `djmd`/`dbgi`/`tmcd` 完整（61,167 样本 + timecode） |
| 时长 | 2040.960 s（期望 2040.977 s） |
| 机型 | `DJI OsmoAction4`，`keep_metadata=True` |
| 校验 | `ok=True` |

端到端 `--dji-join`（真实 MP4Box 连接 + 真实编码 + 清理）三路径均通过：

- NVENC 经典路径（`--no-metadata`）：2/2 输出，joined 源交付后即回收，暂存树删除；
- x265 软件路径（`--scale 0.25 --no-metadata`）：2/2 输出，生命周期一致；
- DJI 保留路径（`--preserve-metadata`）：2/2 输出，
  `PRESERVED=15 / MODIFIED=0 / MISSING=0`。

## 4. 磁盘保护

- 连接前检查可用空间（含当前编码预计输出预留）；空间不足时等待当前组编码
  完成并释放其 joined 源，再开始下一组；
- 仍不足时给出明确错误（需要量 / 当前可用量），不再让 MP4Box 写到一半才报
  “No space left on device”；
- `--dji-join-keep-temp` 会保留所有 joined 组，磁盘需求回到“全部组之和”，
  此开关下不做逐组回收。

## 5. 其他修复

- 硬件编码器进度解析兼容 rigaya 9.31 的 `N/M frames:` 形式（此前只匹配
  `N frames:`，部分长编码的实时进度会缺失）。

## 6. 包

`F:\1KeyTranscoder-v0.9.21-win64-selfcontained.zip`

- 大小：488,809,809 B（466.2 MiB）
- SHA256：`6b0beaff34320173fc38720e95588a7e6e623ca8acf37132078c8c1a7da44fd0`
- 校验：62/63 PASS；唯一 FAIL 为开发构建的 `manifest dirty flag false: dirty=True`
  （源码树含既有未提交改动，构建器以 `--dev-build` 标记为非正式发布）

（K 盘当前不可用，因此输出到 F 盘根目录。）
