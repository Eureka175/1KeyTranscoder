# 断盘前的数据保全（E:/I: 暂时断开）

复制时间：2026-10-22。目的：E:、I: 断开后仍能在 F: 项目文件夹内继续分析与验证。
**全部写入 F:\1KeyTranscoder\data\（项目文件夹内）**；F: 上未删除任何东西。

## 已复制

| 内容 | 复制到 | 规模 | 校验 |
|---|---|---|---|
| 两个离线跑批的全部产物（报告 + 全部 JSON + 日志） | `data\_from_E\1kt_work\` | 7.67 GiB / 387 文件 | report/s2e_oracle 已做 sha256 抽查一致 |
| 交付件（`I:\repaired`，含 `_superseded`） | `data\_from_I\repaired\` | 2.20 GiB / 71 文件 | 文件数一致 |
| 六机位各一件原始样本（跨机位格式/元数据验证用） | `data\_from_I\2026.09.24\<机位>\` | 61.04 GiB | 大小一致 |

六件样本：

| 机位 | 文件 | 大小 |
|---|---|---|
| A7M4 | `C0147.MP4` | 9.63 GiB |
| FX3 | `C0513.MP4` | 0.38 GiB |
| FX30 | `20260922_fx30_2_9757.MP4` | 0.94 GiB |
| HXR-NX800 | `CA0230.MP4`（唯一 4-mono） | 41.16 GiB |
| Mavic4P | `DJI_20260921164928_0296_D.MP4` | 1.10 GiB |
| Pocket3 | `DJI_20260922193237_0001_D.MP4` | 7.83 GiB |

## 未复制（体积或可重建）

| 内容 | 体积 | 原因 |
|---|---|---|
| `E:\1kt_media`（226 件调参语料） | **205.44 GiB** | 复制后 F: 只剩约 10 GB，会挤死后续写盘；需要时再单独拷 |
| `E:\1kt_scratch`（解码缓存） | 12.38 GiB | 可从源素材重建（`_decode_stream`） |
| `I:\2026.09.24` 其余 78 件 | ~589 GiB | 六机位样本已覆盖格式/元数据结论；完整调研 JSON 已在 `_from_E` 里 |
| `I:\2026xxxx` 归档测量源 | ~254 GiB | 归档 arm 的 JSON 本来就在项目 `work\regression_r087\out\` |

## 断盘后怎么跑

```powershell
cd F:\1KeyTranscoder

# 跨机位 Sony 元数据保留验证（已改为读 F: 副本）
python -u tests/regression/sony_metadata_crosscam.py

# 长镜头逐窗探针（默认输出已在 E:，断盘后请显式指到 F: 项目内）
python -u tests/regression/long_take_probe.py --out "F:\1KeyTranscoder\data\probe_long" --budget-minutes 150

# 素材文件夹调研（root 需指向现有副本目录）
python -u tests/regression/investigate_folder.py --root "F:\1KeyTranscoder\data\_from_I\2026.09.24" --out "F:\1KeyTranscoder\data\inv_from_copies" --budget-minutes 60
```

注意：E: 上是解码缓存的默认位置（`E:\1kt_scratch`）。断盘后这些程序会退回到
"从源重新解码"，变慢但结果一致；scratch 目录会自动落到 E: 路径并失败——此时
设 `E:` 相关路径不可用，需在程序里改 `PCM_CACHE`/`SCRATCH` 指向 F: 项目内。
（当前 `investigate_folder.py` 与 `long_take_probe.py` 的 scratch 常量都指向 E:，
断盘前请告知是否要我把它们改成 F: 项目内可配置。）

## 磁盘

F: 剩余 **215.6 GB**（复制前 286.5 GB）。
