# 1KeyTranscoder v0.9.19 发布说明

> 2026-11-22 · NVENC 4:4:4、mono 拒绝、Sony XAVC 无损 remux 模式

## 1. NVENC 4:4:4 修复

此前 NVENC 命令固定使用 `--profile main10`，4:4:4/10 源会被静默输出成
4:2:0/10；手动 `--output-chroma 444` 也会静默降级。

现在当格式计划为 4:4:4 时：

```text
--profile main444 --output-csp yuv444
```

实测 4:4:4 源与手动 444 均输出 `yuv444p10le`（profile Rext）。
QSV 不支持 4:4:4 时明确报错，不再静默降级；AV1 请求非 4:2:0 同样明确报错。

## 2. mono 明确不支持

- `--output-chroma mono` 从 CLI 移除；
- mono 源在探测阶段明确失败：

```text
mono sources are not supported; use a 4:2:0/4:2:2/4:4:4 source
```

不再尝试让 x265/NVENC/QSV 处理 mono 而导致崩溃或静默转成 420。

## 3. 新增无损 Sony XAVC remux 模式

```powershell
python 1kt.py --input "D:\SonyClips" --output "D:\SonyClips_remux" --remux
```

Sony 相机按 128 MiB 录制 chunk 写 `mdat`，短片会在 chunk 尾部留下未使用空间。
remux 模式用 MP4Box 原生轨道复制重建容器：

- 视频、PCM 音频、`rtmd` 数据轨 **原样复制**，不重新编码；
- 保留 `nrtm`、Lens profile、vendor uuid、`tref/cdsc`、XAVC brand；
- 每个文件用 preservation validate **advanced** 对比源文件；
- 同时复制 XML 边车；
- 无 Sony XAVC 信息的文件明确拒绝。

实测 `20261122扫街` 53 个文件：

- 源 19.67 GB → 输出 17.88 GB，**节省 1.79 GB（9.09%）**；
- 单文件节省 1.4%–40.2%；
- 53/53 成功，元数据校验全部 PASS。

## 4. 验证

- 单元测试：1136 PASS / 2 环境性 FAIL（真实 Sony 语料缺失）；
- 4:4:4 默认/手动：输出 `yuv444p10le`；
- QSV 手动 444：明确 ValueError；
- mono 源：明确 PREPROBE FAILED；
- remux：53/53，1.79 GB 节省。

## 5. 说明

- remux 模式不编码，不改变视频码流；只改变容器布局；
- 输出目录会包含 `logs/` 与 XML 边车；
- 源文件始终只读，不被修改。
