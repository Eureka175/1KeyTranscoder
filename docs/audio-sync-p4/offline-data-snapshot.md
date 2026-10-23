# E:/I: 断开时的数据保全（2026-10-22 ~ 10-23）

目的：E:、I: 断开后仍能在 F: 项目文件夹内继续分析、验证与调参。
**全部写入 `F:\1KeyTranscoder\data\`（项目文件夹内）**；项目外的 F: 内容只读，未写入、未删除。

## 已复制

| 内容 | 复制到 | 规模 | 校验 |
|---|---|---|---|
| 两个离线跑批的全部产物（报告 + 全部 JSON + 日志） | `data\_from_E\1kt_work\` | 7.67 GiB / 387 文件 | report、s2e_oracle 已 sha256 抽查一致 |
| **调参语料 speech**（`E:\1kt_media\speech`） | `data\_from_E\1kt_media\speech\` | **90.99 GiB / 1078 文件** | 文件数与字节数与源一致 |
| 长镜头素材 | `data\align_4ch_proof\`（原有） | 92.95 GiB / 2 文件 | 与 `E:\1kt_media\long` 同名同大小，无需再拷 |
| Gyroflow 原始解析证据 + 日志 | `data\_from_E\gyro_evidence\` | 2.21 GiB / 10 文件 | 直接来源 |
| 交付件（`I:\repaired`） | `data\_from_I\repaired\` | 2.20 GiB / 71 文件 | 文件数一致 |
| 六机位原始样本 | `data\_from_I\2026.09.24\<机位>\` | 61.02 GiB / 6 文件 | 逐件大小一致 |
| 元数据往返成品 | `data\sony_meta_crosscam\work\<机位>\restored.MP4` | 12.06 GiB / 4 文件 | 供复核 |

## 离线回退（已改代码）

E: 断开时下列脚本自动改读项目内副本，不再崩溃：

| 脚本 | 原来硬编码 | 回退 |
|---|---|---|
| `tests/regression/run_arm_sharded.py` | `E:\1kt_media\speech`、`E:\1kt_media\long` | `data\_from_E\1kt_media\speech`；长镜头用 `data\align_4ch_proof` |
| `tests/regression/long_take_probe.py` | `E:\1kt_scratch\pcm`、默认输出 `E:\1kt_work\...` | `data\_scratch\pcm`（空时自动从源解码）、`data\probe_long` |
| `tests/regression/investigate_folder.py` | `E:\1kt_scratch\pcm`、`E:\1kt_scratch\investigate` | `data\_scratch\pcm`、`data\_scratch\investigate` |

验证：E: 在线时仍解析到 E:（`corpus` 226 件不变）；E: 不存在时走项目副本。

## 未复制（体积原因，需要时单独处理）

| 内容 | 体积 | 说明 |
|---|---|---|
| `E:\1kt_media\long` | 92.94 GiB | 与项目 `align_4ch_proof` 里的两件**完全相同**，不重复占空间 |
| `E:\1kt_scratch`（解码缓存） | 14.59 GiB | 可从源重建；断 E: 后程序自动重新解码，只是变慢 |
| `I:\2026.09.24` 其余 78 件 | 588.7 GiB | 六机位样本 + 完整调研 JSON 已覆盖结论 |
| `I:\compact` | 370.9 GiB | 证明集 35 件（130.7 GiB）已在项目内 |
| `I:\2026xxxx` 归档源 | ~254 GiB | 对应 arm JSON 已在 `work\regression_r087\out\` |

## 磁盘

F: 剩余 **47.6 GB**（复制语料前 140.8 GB）。

## 断盘后继续工作的命令

```powershell
cd F:\1KeyTranscoder

# 跨机位元数据验证（源指向 F: 副本）
python -u tests/regression/sony_metadata_crosscam.py
python -u tests/regression/gyroflow_consumer_check.py

# 长镜头逐窗探针（自动回退缓存/输出）
python -u tests/regression/long_take_probe.py

# 素材调研（root 指向现有副本）
python -u tests/regression/investigate_folder.py --root "F:\1KeyTranscoder\data\_from_I\2026.09.24" --out "F:\1KeyTranscoder\data\inv_from_copies"
```
