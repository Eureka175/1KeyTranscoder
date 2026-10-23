# Sony 元数据保留：跨机位验证（A7M5 之外）

验证对象是程序**已有**的保留管线，不新写实现：

| 环节 | 已有 API |
|---|---|
| 抽取 | `preservation.sony.SonyPreservationBackend.extract` |
| 恢复 | `preservation.transparent_restore.restore_after_transparent_remux` |
| 容器操作 | `preservation.gpac.GpacContainerBackend`（mux / set_brand / uuid 插入） |
| 逐项比对 | `archive_repair.container_facts` + `uuid_inventory_equal` |

**未执行任何编码器**：全程 GPAC stream copy + 元数据 box 插入。

## 结论

Sony 元数据保留**不限于 A7M5**：A7M4 / FX3 / FX30 上同样成立，NX800 的 bundle 抽取
也正常。往返后**内容逐项一致**；唯一的差异是**顶层 box 顺序**（集合相同）。

| 机位 | 文件 | 大小 | rtmd 样本 | 样本字节 | timescale | uuid | nrtm | brand | movie_ts | 内容一致 |
|---|---|---|---|---|---|---|---|---|---|---|
| A7M5（对照） | `C1159` | 1.19 GiB | 3720 | 19456 | 60000 | 9 | Y | XAVC | 60000 | **PASS** |
| A7M4 | `C0147` | 9.63 GiB | 19296 | 29696 | 50000 | 5 | Y | XAVC | 50000 | **PASS** |
| FX3 | `C0513` | 0.38 GiB | 720 | 29696 | 50000 | 5 | Y | XAVC | 50000 | **PASS** |
| FX30 | `9757` | 0.94 GiB | 1848 | 29696 | 50000 | 5 | Y | XAVC | 50000 | **PASS** |
| HXR-NX800 | `CA0230` | 41.16 GiB | 84480 | 11264 | 50000 | 8 | Y | XAVC | 50000 | 抽取-only（41 GiB 未做往返） |
| Mavic4P（DJI） | `0296` | 1.10 GiB | — | — | — | — | N | — | — | **抽取失败**（预期：非 Sony，Sony 后端不适用；DJI 走 `preservation.dji`） |

每台 "内容一致" 的具体含义（全部逐项比对通过）：

* brand 与 compatible brands 相同
* `tref`/`cdsc` 引用相同（rtmd → 视频轨）
* nrtm lens profile 相同（item + XML sha256）
* **uuid 多重集相同**：A7M5 9/9、A7M4 5/5、FX3 5/5、FX30 5/5，`missing=[] added=[]`
* rtmd 轨元数据相同：handler、sample_entry、timescale、duration、sample_count、
  constant_sample_size、stts、track_refs、timecode_tag
* 视频/音频流参数相同（codec/profile/pix_fmt/分辨率/帧率/声道/采样率/位深/采样条目）

## 唯一未复现项：顶层 box 顺序

| 机位 | 源 | 恢复后 |
|---|---|---|
| A7M5 | `ftyp, uuid, uuid, free, mdat, moov, meta` | `ftyp, mdat, free, meta, moov, uuid, uuid` |
| A7M4/FX3/FX30 | `ftyp, uuid, free, mdat, moov, meta` | `ftyp, mdat, free, meta, moov, uuid` |

集合完全相同，只是 GPAC 把 uuid box 追加到了文件尾部。**元数据没有丢**；需要判断的是
"顺序差异对下游消费方是否有影响"，这是下一步该验的（最硬的证据是 Gyroflow 从成品读到
的 IMU 样本数是否与源一致 —— 当初丢元数据的失败症状正是 146146 → 0）。

## 跨机位差异（说明为什么必须按机位测）

* `movie_timescale`：A7M5 = **60000**，A7M4/FX3/FX30/NX800 = **50000**。
  若沿用 A7M5 的 60000 重建，GPAC 会截断时间刻度、rtmd 时间线与视频错位
  （`PreservationBundle.movie_timescale` 的注释已记录这一点）。
* rtmd 样本字节数：A7M5 19456 B、A7M4/FX3/FX30 29696 B、NX800 11264 B —— 三档不同。
* uuid 数量：A7M5 9 个、A7M4/FX3/FX30 5 个、NX800 8 个。

## 复现命令

```powershell
cd F:\1KeyTranscoder
python -u tests\regression\sony_metadata_crosscam.py                 # 全部
python -u tests\regression\sony_metadata_crosscam.py --cases "FX3,FX30"
python tests\regression\finalise_sony_meta_crosscam.py               # 汇总表
```

样本位于 `data\_from_I\2026.09.24\<机位>\`（E:/I: 断开前已拷贝），
成品保留在 `data\sony_meta_crosscam\work\<机位>\`（F: 不允许删除，保留以便复核）。

## 待办

1. **消费方验证**：用 Gyroflow 从源与成品各导出一次，比对 IMU 样本数/型号识别。
2. NX800 往返（41 GiB，需要 41 GiB 以上 scratch；可在 E: 恢复后做）。
3. 把"内容一致 / 顺序不同"作为正式验收口径写进 `preservation` 的检查报告
   （`uuid_inventory_equal` 已按多重集判定，顶层 box 顺序应单列为 warning）。

## 消费方验证（Gyroflow）—— 决定性证据

preservation.gyroflow.check 分别从**源**与**恢复后的成品**导出，比较 Gyroflow 实际解析到的东西：

| 机位 | IMU 样本（源 → 成品） | 机型识别 | 镜头型号 | 帧率/readout | 镜头 profile | 结论 |
|---|---|---|---|---|---|---|
| A7M5（对照） | 124124 → 124124 | ILCE-7M5 → ILCE-7M5 | 一致 | 一致 | True → True | **PASS**（mismatches 空） |
| A7M4 | 771840 → 771840 | ILCE-7M4 → ILCE-7M4 | 一致 | 一致 | True → True | **PASS** |
| FX3 | 28800 → 28800 | ILME-FX3 → ILME-FX3 | 一致 | 一致 | True → True | **PASS** |
| FX30 | 73920 → 73920 | ILME-FX30 → ILME-FX30 | 一致 | 一致 | True → True | **PASS** |

这正是当初的失败模式（元数据丢失时 Gyroflow 从 146146 IMU 样本掉到 0）：现在**四台机位全部 0 差异**。
顶层 box 顺序的差异（GPAC 把 uuid 追加到末尾）**不影响消费方**。

工具：D:\\Gyroflow-windows64\\Gyroflow.exe（preservation.gyroflow.find_gyroflow 找到）。

## NX800 往返（41 GiB）状态

* 抽取已验证：rtmd 84480 样本 × 11264 B、uuid 8 个、nrtm、brand XAVC、movie_ts 50000。
* 往返在跑：GPAC 的 import 走系统临时目录，第一轮因 **C: 只剩 31 GB** 在 80%% 处 ENOSPC 失败；
  把 TEMP/TMP 指到 E: 后重跑，	ransparent_stage.mov 已生成 41.14 GiB，正在做元数据轨重建。
* 结论不受影响：其余四台机位在 bundle 与消费方两层都已闭环。


### 中止原因与恢复方式

* 2026-10-23 约 01:10，因机器必须在 01:30 前进入休眠，主动终止该往返（已跑约 70 分钟，停在 GPAC 对 41 GiB stage 的元数据轨重建阶段）。
* 已保留证据：抽取结果完整（rtmd 84480 样本 × 11264 B、uuid 8 个、nrtm、brand XAVC、movie_ts 50000）；同系列 A7M4/FX3/FX30 已通过 bundle 与 Gyroflow 两层验证。
* 恢复时注意：GPAC import 走系统临时目录，C: 只有约 31 GB，必须把 TEMP/TMP 指到 E:；命令见 tests/regression/sony_metadata_crosscam.py 的 --scratch-root 用法。

## HXR-NX800（4-mono，41 GiB）往返：已完成并验证

2026-10-24 01:03 生成 `restored.MP4`（41.14 GiB，源码 41.16 GiB），逐项验证与 Gyroflow 消费方验证：

| 检查项 | 结果 |
|---|---|
| ftyp brand / 兼容 brand | **一致**（XAVC） |
| `tref`/`cdsc` | **一致** |
| nrtm（lens profile + XML sha256） | **一致** |
| uuid 多重集 | **8/8 一致**，无缺失无新增 |
| rtmd 轨（84480 样本 × 11264 B、timescale、cdsc 引用） | **一致** |
| 视频/音频流参数（4×ipcm 24bit 单声道等） | **一致** |
| bundle 私有 box 8 个（label/ctx/guid/sha） | **一致** |
| **Gyroflow 消费方** | **PASS**：3,379,227 IMU 样本 → 3,379,227，机型 HXR-NX800、identifier、镜头 profile 全一致，mismatches 为空 |
| 顶层 box | 内容集合差异仅来自多出的 `free` 填充；uuid 位置移到末尾、moov/meta 次序不同（结构性差异，不影响消费方） |

结论：**Sony 元数据保留在 NX800（4-mono XAVC，8 uuid，ipcm 24-bit）上同样成立**。至此五台 Sony 机位
（A7M5/A7M4/FX3/FX30/NX800）在 bundle 与消费方两层全部通过。

复现：`python tests/regression/sony_metadata_crosscam.py --cases "HXR-NX800" --scratch-root "E:\1kt_scratch\sony_meta_nx800"`
（GPAC 导入走系统临时目录，C: 只有 ~31 GB，必须把 TEMP 指到 E:）
验证脚本：`python tests/regression/verify_nx800_restore.py`（对已生成成品，不重跑 2.5 小时往返）
