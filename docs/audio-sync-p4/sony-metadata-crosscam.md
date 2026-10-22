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
