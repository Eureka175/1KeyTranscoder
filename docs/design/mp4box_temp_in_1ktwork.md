# MP4Box remux 临时目录检查

日期：2026-11-04
结论：**当前版本 remux 的 MP4Box 临时文件已全部落在 `.1ktwork` 内，不再使用 C 盘 `%TEMP%`。**

## 1. 修复前的实际行为

`preservation/gpac.py::_gpac_env(cwd)` 只有在 `cwd` 非空时才会把
`TMP/TEMP/TMPDIR` 改成 `<cwd>/.gpactmp`。检查发现：

| MP4Box 调用 | 修复前 cwd | 临时目录 |
|---|---|---|
| `-new/-add`（`mux_new`） | `<job>/final` | ✅ `.1ktwork/<job>/final/.gpactmp` |
| `-flat`（`flatten`） | **None** | ❌ 系统 `%TEMP%`（C:） |
| `-brand`（`set_brand`） | **None** | ❌ 系统 `%TEMP%`（C:） |
| `-ref`（`add_track_ref`） | **None** | ❌ 系统 `%TEMP%`（C:） |
| `-set-meta`（`set_meta`） | **None** | ❌ 系统 `%TEMP%`（C:） |
| `-nhml` dump | **None** | ❌ 系统 `%TEMP%`（C:） |

其中 **`-flat` 是整部影片重新写 mdat/moov 的步骤**，最容易在大文件
remux 时把 C 盘临时区写满；这也是此前 C1305/C1306 出现
`No space left on device` 的风险点之一（该次实际失败是输出盘写满）。

## 2. 修复

对以上所有调用传 `cwd=_rel_base(mov/src)`：

- `preservation/gpac.py`：
  - `flatten()`：`cwd` = movie 所在目录
  - `set_brand()`：`cwd` = movie 所在目录
  - `add_track_ref()`：`cwd` = movie 所在目录
  - `set_meta()`：`cwd` = movie 所在目录
  - `-nhml` dump：`cwd` = source 所在目录

标准 Sony remux 的 movie/stage 位于：

```
<output>/.1ktwork/<job>/final/stage.mov
```

因此 MP4Box 临时目录为：

```
<output>/.1ktwork/<job>/final/.gpactmp
```

`_run()` 在该次 MP4Box 调用结束后立即 `shutil.rmtree(.gpactmp)`，不跨调用复用。

## 3. 验证

用 mock `subprocess.run` 捕获 5 类 remux 调用的 `cwd` 和 `TMP`：

```
mux_new      cwd=...\.1ktwork\job\final  tmp=...\.1ktwork\job\final\.gpactmp
flatten      cwd=...\.1ktwork\job\final  tmp=...\.1ktwork\job\final\.gpactmp
set_brand    cwd=...\.1ktwork\job\final  tmp=...\.1ktwork\job\final\.gpactmp
set_meta     cwd=...\.1ktwork\job\final  tmp=...\.1ktwork\job\final\.gpactmp
add_track_ref cwd=...\.1ktwork\job\final tmp=...\.1ktwork\job\final\.gpactmp
```

真实 C1263 重跑：`PRESERVED=37 MODIFIED=0 PASS`；运行目录内无
`.gpactmp` 残留。

## 4. 提交

- `d5faa8b` — 所有 remux 写操作传 `cwd=_rel_base(mov)`
- `b742f23` — `-nhml` dump 也重定向

单元测试：1053 PASS / 2 FAIL（既有 Sony/DJI 语料环境项）。
