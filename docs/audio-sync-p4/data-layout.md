# 项目数据布局（2026-10-22 收敛）

F 盘原本把验证集、复核件、难点集散落在根目录。现在**物理位置**统一在项目文件夹内：

| 用途 | 新路径 | 旧路径（保留为 Junction 兼容） | 规模 |
|---|---|---|---|
| 4ch 调参验证集（35 件原始素材） | `F:\1KeyTranscoder\data\align_4ch_proof\` | `F:\align_4ch_proof` | 130.70 GiB |
| 交付复核件（13 件 + INDEX + 全轨已对齐） | `F:\1KeyTranscoder\data\review\` | `F:\对齐复核` | 2.45 GiB |
| 难对齐素材集（57 件） | `F:\1KeyTranscoder\data\hard_cases\` | `F:\难对齐素材` | 12.04 GiB |
| Canon R6V 4CH XFVC 原始素材 | `F:\1KeyTranscoder\data\canon_r6v_4ch_xfvc\` | `F:\CANON_R6V_OG_7K2997P_4CH_XFVC` | 1.62 GiB |

## 为什么用 Junction 而不是只留新路径

旧路径仍被多处引用：`tests/regression/` 下的 10 个测量脚本、
`docs/audio-sync-p4/tv-feasibility-plan.md`、以及人工复核习惯。Junction 让旧路径
**直接指向**新位置（同一份数据，不占额外空间），所以：

* 脚本里的路径已全部更新为**新路径**（`data\...`），不依赖 Junction；
* 但任何仍按旧路径打开的人/脚本也不会失效；
* 需要彻底移除旧入口时，删掉 Junction 即可（`rmdir` 不删数据）。

## `.gitignore`

`/data/` 已加入 `.gitignore` —— 这 146 GiB 是**数据**，不是代码，绝不能进版本库。
`git status` 因此保持干净。

## 已清理的临时目录（派生数据，可重建）

| 目录 | 性质 |
|---|---|
| `F:\_align_proof_run`（37.76 GiB） | `align_4ch_proof` 的硬链接暂存，删掉不省空间（硬链接），只是去重入口 |
| `F:\_arm_stage` | 并行 arm 的暂存根，空 |
| `F:\1kt_scratch`（0.45 GiB） | 修复工作目录残留（`20260904_C1184` 的 cover/decoded 中间件） |

## 未收敛的 F 盘内容（不属于本项目）

`Pictures`、`Download`、`文档`、`微信供图`、`2026.4.22运动会开幕式` 等均为个人/其他项目
数据，**未移动**。`F:\1KeyTranscoder\testsets`（315.80 GiB）本来就在项目文件夹内。
