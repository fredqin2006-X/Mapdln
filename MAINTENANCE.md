# Mapdln 维护文档

适用版本：2.2.0 本地构建；更新日期：2026-10-25。以下命令在仓库根目录的 PowerShell 中执行。账户信息仅使用本机配置，本文件不保存真实凭据。

## 1. 源码入口与职责

| 入口 | 职责 |
|---|---|
| `main.py` | 无参数启动桌面 GUI；`--cli` 命令行下载；`--worker` 独立后台任务入口和 UTF-8 通信初始化 |
| `mapdln/__init__.py` | GUI 显示版本；版本调整以这里为入口，并同步 README、变更记录及交付包名 |
| `mapdln/gui.py` | PySide6 工作台、账户页、WebChannel 桥接、选区状态、额度估算、后台进程及锁定生命周期 |
| `mapdln/web/map.html` | 地图容器、SVG 选区/预览/控制点、工具栏、灰色锁定样式 |
| `mapdln/web/map.js` | 三种预览底图适配、绘图、旋转、预览、键盘、右键、滚轮和中键交互 |
| `mapdln/geometry.py` | 坐标转换、旋转矩形、闭合向心 Catmull–Rom 曲线、UTM 网格与实际下载轮廓 |
| `mapdln/engine.py` | 瓦片估算、缓存、请求预算、卫星和地形下载、统一网格导出 |
| `mapdln/worker.py` | 后台命令接收、暂停/继续/取消和结构化事件回传 |
| `mapdln/server.py` | 仅监听本机的预览服务、静态资源、Mapbox 瓦片和高德安全转发 |
| `mapdln/config.py` | 用户目录、Windows DPAPI 配置加密、日志遮蔽和请求记录 |
| `tools/build.ps1`、`tools/mapdln.spec` | 图标生成、完整测试、单文件 EXE 构建及依赖过滤 |
| `tests/test_engine.py` | 几何、坐标、实际成果、缓存、预算和下载引擎回归 |
| `tests/test_map_interactions.py` | 原生 Qt 输入进入实际 WebEngine 页面，验证交互与后台锁定 |

GUI 和 CLI 共用同一几何及下载引擎。地图底图用于预览，实际卫星下载仍使用 Mapbox；改变预览来源不得改变任务的下载来源或已选区域。

## 2. 地图编辑规则

| 操作 | 行为 |
|---|---|
| 左键拖动矩形/圆形 | 创建选区 |
| 左键逐点点击多边形/插值曲线 | 添加固定控制点；鼠标位置只参与预览 |
| `Enter`，包括数字键盘 Enter | 至少三个已点击的点时闭合；与原有“完成”按钮共用 `finishDrawing()` |
| 右键已点击的草稿控制点 | 删除该点，草稿允许少于三点；不触发闭合 |
| 右键已闭合的多边形/曲线控制点 | 删除该点，至少保留三点；右键空白处不闭合、不加点 |
| `Delete` | 与“清除”按钮共用 `clearSelection()`，删除当前完整选区和未完成草稿 |
| `Esc` | 取消本次绘制/拖动并回到移动工具；保留此前已经提交的选区 |
| 拖动选区/白色控制点 | 移动选区/调整几何；多边形、曲线双击选区可加点 |
| 绿色 `↻` 手柄 | 旋转矩形；按住 Shift 按 15° 对齐 |
| 滚轮/中键拖动 | 缩放/平移地图，选区坐标和草稿固定点不变 |

键盘快捷键在地图页面获得输入时生效；不应劫持账户或坐标表单输入。页面自身将来新增输入框时，仍应保留对 `input`、`textarea`、`select`、可编辑区域、输入法组合和 Ctrl/Alt/Meta 组合键的保护。

维护交互时优先修改 `map.js` 的共享处理函数，避免在 Enter、工具栏和其他入口分别复制逻辑。完成只提交固定点，不能把鼠标悬停位置作为最后一个控制点。右键只能处理控制点删除，不能调用 `finishDrawing()`。

## 3. 选区、坐标及旋转

前端通过 `bridge.selectRegion()` 向 Python 提交 `shape`、`system`、`points` 等字段；`make_grid()` 校验并生成真实下载几何，`Window.render_selection()` 将控制点和采样边界转换回当前预览坐标系。

- 高德显示坐标为 GCJ-02，百度为 BD-09，Mapbox/手动输入为 WGS84。不要直接将国内底图坐标作为 WGS84 使用。
- 矩形 `angle_deg` 为 UTM 投影平面内的逆时针角度，默认 0。支持“中心 + 米制宽高”及“旋转后的两个对角点”；调整角点时必须保留角度。
- 旋转矩形按实际轮廓裁切，但输出网格仍向北对齐。卫星 Alpha、掩膜、DEM 和高度图必须保持相同尺寸和像素变换。
- 前端曲线预览在屏幕像素中自适应采样；最终轮廓由 Python 在米制坐标中按分辨率采样。预览不替代下载几何验证，不应触发额度估算或下载请求。
- 清除时同时清空 Python 的 `selection/grid/plan`，取消待执行估算、更新面积说明，并回传空的 `updateSelection()`，防止连续拖动后的旧响应让已删除图形重新出现。

## 4. 下载锁定生命周期

`Window.selection_locked` 是桌面端状态，`set_selection_locked()` 同步坐标表单和前端 `setSelectionLocked()`。前端必须禁止绘图、旋转、移点、整区移动、Delete、右键删点、完成和清除；`Bridge.selectRegion()`、`Bridge.clearRegion()`、`apply_coordinates()` 还必须在 Python 端拒绝修改。

| 状态/事件 | 锁定要求 |
|---|---|
| 后台任务启动前 | 立即锁定当前任务快照，选区变灰、隐藏控制点 |
| 运行、暂停、预算等待、继续 | 保持锁定；地图缩放和平移仍可使用 |
| 用户点击取消 | 发送取消命令后继续锁定，等待后台真正退出 |
| `worker_finished()` | 取消结束、完成或异常退出后自动恢复编辑 |
| 后台无法启动 | `worker_error()` 恢复编辑并关闭无效任务操作 |
| 切换或重载底图 | `Bridge.pageReady()` 恢复现有锁定状态；不能仅靠前端默认值 |

不要在收到“取消”点击或 `done` 消息时提前解锁；以后台进程结束作为正常解锁边界。更改参数和旧估算回调不能替换下载中的选区；`plan_generation` 用于忽略已经失效的估算结果。失败后“改用 0 米继续导出”使用原任务的选区快照。

## 5. 开发、验证和构建

首次配置：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe main.py
```

定位问题时可单独运行对应检查；正式构建脚本会重新执行完整测试：

```powershell
node --check mapdln\web\map.js
.\.venv\Scripts\python.exe -m pytest tests\test_map_interactions.py -q
.\.venv\Scripts\python.exe -m pytest tests\test_engine.py -q
powershell -NoProfile -ExecutionPolicy Bypass -File tools\build.ps1
```

GUI 回归需要可用的 Windows 桌面和 Qt WebEngine。测试使用独立临时 `MAPDLN_HOME`、空账户和合成缓存；原生鼠标事件、滚轮、主键盘/数字键盘 Enter、Delete、右键删点及真实独立后台进程均进入实际程序逻辑。测试通过不等于在线底图授权或真实地形服务已经通过验收。

构建结果为 `dist/mapdln.exe`。必须使用仓库的 spec：它包含 WebEngine、地理运行库、本机页面和图标，并过滤可能与 Windows 系统 ICU 冲突的 DLL。不要用临时 spec 绕过过滤规则。修改 JS/HTML 后必须重新构建，单独修改源码不会更新旧 EXE 内的页面。

构建后退出旧程序，更新根目录入口并实际启动检查：

```powershell
Copy-Item -LiteralPath dist\mapdln.exe -Destination mapdln.exe -Force
.\mapdln.exe
```

至少检查：四种形状、旋转保留、鼠标预览与实际完成结果、Enter 闭合、右键仅删点、Delete 删除、选区内滚轮、中键拖动、下载锁定、暂停/继续、取消等待退出及成功完成解锁。在线底图、真实下载和纯缓存验证分开记录到 `ACCEPTANCE.md`；不可将离线或源码验证写成最终 EXE 的在线验收。

## 6. 本地交付与 Git

EXE、`dist/`、`build/`、`delivery/` 和私有验收数据不进入源码提交。每次完成程序修改和版本构建后创建本地 commit，保证可以回档；没有发布授权时不推送远端、不创建 GitHub Release。

Windows 使用一个完整 ZIP 交付。以下白名单同时包含维护文档，避免将整个工程或配置目录打包：

```powershell
$taskVersion = & .\.venv\Scripts\python.exe -c "from mapdln import __version__; print(__version__)"
$taskStage = Join-Path 'delivery' "Mapdln-$taskVersion"
New-Item -ItemType Directory -Path $taskStage -Force | Out-Null
Copy-Item -LiteralPath dist\mapdln.exe -Destination (Join-Path $taskStage 'mapdln.exe') -Force
$taskDocs = @('README.md','MAINTENANCE.md','RELEASE-NOTES.md','CHANGELOG.md','ACCEPTANCE.md','LICENSE','THIRD-PARTY.md')
foreach ($taskDoc in $taskDocs) { Copy-Item -LiteralPath $taskDoc -Destination $taskStage -Force }
$taskZip = Join-Path 'delivery' "Mapdln-$taskVersion-Windows-x64.zip"
Compress-Archive -LiteralPath $taskStage -DestinationPath $taskZip -Force
$taskHash = (Get-FileHash -LiteralPath $taskZip -Algorithm SHA256).Hash.ToLowerInvariant()
"$taskHash  $(Split-Path -Leaf $taskZip)" | Set-Content -LiteralPath (Join-Path 'delivery' "SHA256SUMS-$taskVersion.txt") -Encoding utf8
```

检查 ZIP 文件清单、解压运行和 SHA-256 后，仅暂存明确需要的源码/文档，检查暂存区，再创建本地提交：

```powershell
git diff --check
git add -- mapdln tests README.md MAINTENANCE.md CHANGELOG.md RELEASE-NOTES.md ACCEPTANCE.md
git diff --cached --stat
git diff --cached --check
git commit -m "Describe the completed change and validation"
git status --short
```

若更新构建脚本或依赖，还应显式暂存对应文件。需要撤销时先查看 `git status` 和 `git log`，优先用 `git revert <提交号>` 保留后续历史，再按同一流程重新构建；不要用强制重置覆盖本地未提交工作。

## 7. 配置、排障与发布边界

配置、缓存和请求记录默认位于 `%LOCALAPPDATA%/Mapdln`；`MAPDLN_HOME` 可指定独立测试目录。凭据通过当前 Windows 用户的 DPAPI 加密，复制到另一用户后不能假定可解密。排障只记录已遮蔽的信息，不将 Token、Key、安全密钥、密码或完整配置复制进文档。

| 现象 | 检查位置/处理 |
|---|---|
| EXE 看不到新功能 | 确认启动的是新构建，更新根目录或 ZIP 内的 EXE，退出仍运行的旧进程 |
| Delete/Enter 不生效 | 先让地图获得焦点；确认不是正在输入表单、未满三点或下载仍锁定 |
| 右键无法删点 | 必须点击多边形/曲线控制点；完成后的图形至少保留三点；下载中禁止删除 |
| 取消后暂时仍灰色 | 后台仍等待当前请求或处理块结束；检查任务状态和日志，不要提前解锁 |
| 选区内无法缩放 | 检查 SVG wheel 路径；高德使用 SDK 缩放接口，不能依赖转发的合成滚轮事件 |
| 中键方向错误/地图跳动 | 高德、百度 `panBy` 与 Leaflet 的偏移方向不同；同时检查指针捕获和释放 |
| 地图授权或加载失败 | 查看 GUI 状态；按账户页说明核对 Key 类型、安全密钥、Referer 和代理；不要将授权失败当作绘图失败 |
| EXE 启动失败 | 检查用户目录中的 `startup-error.log`；核对 spec 的 ICU 过滤、WebEngine 和资源路径 |
| 高程下载失败 | 检查 FABDEM Range 支持和网络，或使用界面提供的 0 米继续导出功能 |

预览服务仅绑定 `127.0.0.1`，优先端口 8765，冲突时使用空闲端口；每次启动使用随机访问标识。保留 Host/来源验证及高德官方路由限制，不改为任意转发代理。

`Agents.md` 和 `GUI-DESIGN.md` 为私有文件，必须保持忽略且不进入 Git、源码归档或交付包；账户配置、私有测试数据也不发布。公开前检查跟踪文件、冻结程序内的应用代码/资源和 ZIP，不只检查 ZIP 文件名。Windows Release 仅上传完整程序 ZIP，不额外单独上传 EXE。

授权发布 GitHub Release 时，发行说明保持以下结构，并补充对应版本的完整变更记录链接：

```markdown
## 🚀 新特性 / Features
## 🐛 错误修复 / Bug Fixes
## ⚠️ 破坏性改动 / Breaking Changes
## ⚡ 性能优化 / Performance Improvements
## 📖 文档与依赖更新 / Documentation & Dependencies
---
**完整变更记录**：
```

当前未对百度实际授权加载、另一台实体 Windows 电脑和所有在线服务重新验收；后续变更仍需独立验证这些边界。程序未经代码签名。
