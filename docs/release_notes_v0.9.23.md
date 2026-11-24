# 1KeyTranscoder v0.9.23 prerelease

> 2026-11-24 · `--remux` 支持 `--dji-join`；remux 校验跟随 `--check` 三档；
> remux 任务复用 `--jobs` 调度，校验异步单队列运行；失败自动重试一次。

## 1. `--remux --dji-join`（不编码视频）

```powershell
python 1kt.py --input "D:\DJI" --output "D:\DJI_remux" --remux --dji-join
```

- DJI 分段先用现有 `ProgressiveStager` 无损连接；
- 每个 joined 文件（以及未分组 DJI 单文件）用 MP4Box 原生轨道复制重建容器：
  视频、音频、`djmd`/`dbgi`/`tmcd` 逐位复制，不启动编码器；
- `--check` 校验后落盘；`basic` / `advanced` / `full` 与视频路径同义；
- `full` 的 PSNR/SSIM 因视频是 stream copy 记为
  `SKIPPED_NOT_APPLICABLE`；
- `--no-metadata` 仍保持无损 remux，但校验降为纯容器/帧数检查；
- XML 边车复制；首段边车按 joined 输出名复制；
- `--dji-join-keep-temp` 保留 staging。

## 2. XAVC remux 的 `--check` 不再固定 advanced

此前 XAVC remux 无论传什么 `--check` 都固定执行 advanced；现在：

| `--check` | XAVC remux |
|---|---|
| `basic`（默认） | 核心容器/轨道/帧数 + rtmd 基础项 |
| `advanced` | 完整结构 + Gyroflow |
| `full` | advanced + 深度自检；PSNR/SSIM N/A（stream copy） |

DJI rebuild 同样跟随三档。

## 3. `--jobs` 与异步校验

两条 remux 路径复用主流程的 `--jobs`：

- `--jobs N`：N 个并发 remux 工作槽位；
- `--jobs auto`：复用 `AdaptiveJobs`（start=2，按完成吞吐调整）；
- 校验不占 remux 槽位：产物完成后释放槽位，再进入异步校验；
- 校验队列深度 1，等待校验不会阻塞其他 remux；
- DJI 允许多个已 joined 组同时 remux；join 本身仍单线程、按组顺序；
- 校验失败删除产物，自动重试 remux + 校验一次；第二次失败才标记文件失败。

## 4. 验证

- XAVC + `--check basic --jobs 2`：PASS；
- XAVC + `--check full --jobs auto`：
  - Gyroflow PASS；
  - 深度自检 53/53 PASS；
  - quality `SKIPPED_NOT_APPLICABLE`；
- XAVC + `--no-metadata --check full`：容器/帧数检查 PASS；
- XAVC + 注入校验失败：自动重试后 `attempt 2/2` PASS；
- DJI 3 组 + `--dji-join --check basic --jobs 2`：3/3 PASS，staging 清理；
- DJI 3 组 + `--check full --jobs 2`：Gyroflow PASS，quality N/A，3/3 PASS；
- 单元自测：1137 PASS / 3 FAIL；3 项为真实 Sony 语料缺失 ×2 与旧 multihw
  文案，与本版无关。

## 5. 升级提示

- XAVC remux 默认校验由 advanced 变为 basic；需要更严格校验时显式：
  `--check advanced` 或 `--check full`；
- `--remux` 仍与 `--encoder` 互斥（remux 不编码）；
- `--no-metadata` 只降低校验强度，不改变 remux 的元数据保留行为。
