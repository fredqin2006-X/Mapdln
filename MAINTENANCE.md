# Mapdln 维护文档

适用版本：2.7.0 Windows 本地构建；更新日期：2026-10-28。当前公开发行仍为 2.4.0。以下命令在仓库根目录的 PowerShell 中执行。账户信息仅使用本机配置，本文件不保存真实凭据。

## 1. 源码入口与职责

| 入口 | 职责 |
|---|---|
| `main.py` | 无参数启动桌面 GUI；`--cli` 命令行下载；`--worker` 独立后台任务入口和 UTF-8 通信初始化 |
| `mapdln/__init__.py` | GUI 显示版本；版本调整以这里为入口，并同步 README、变更记录及交付包名 |
| `mapdln/gui.py` | PySide6 工作台、图源管理页外壳、地图预览页、WebChannel 桥接、选区状态、请求估算、后台进程及锁定生命周期 |
| `mapdln/widgets.py` | 共享下拉控件、底边定位、长列表滚动、屏幕可用区域约束及失焦滚轮行为 |
| `mapdln/web/map.html` | 地图容器、SVG 选区/预览/控制点、工具栏、灰色锁定样式 |
| `mapdln/web/map.js` | 官方 SDK / 通用栅格 / 离线预览、DEM 覆盖、绘图、旋转和键鼠交互 |
| `mapdln/geometry.py` | 坐标转换、旋转矩形、闭合向心 Catmull–Rom 曲线、UTM 网格与实际下载轮廓 |
| `mapdln/engine.py` | 瓦片估算、缓存、请求预算、卫星和地形下载、统一网格导出 |
| `mapdln/sources.py` | 内置/自定义图源、模板与凭据分离、WMTS 矩阵验证、请求构造和来源元数据 |
| `mapdln/source_dialog.py` | 嵌入式 `SourcesPanel`：图源列表与状态、编辑表单、后台能力文档读取、单瓦片测试、凭据保存与配置导入导出 |
| `mapdln/local_dem.py` | 本地 GeoTIFF 探测、文件快照、单位转换和逐像素有效覆盖预检 |
| `mapdln/worker.py` | 后台命令接收、暂停/继续/取消和结构化事件回传 |
| `mapdln/server.py` | 仅监听本机的预览服务、已注册图源瓦片、共享限速/并发及高德安全转发 |
| `mapdln/config.py` | 用户目录、Windows DPAPI 配置加密、日志遮蔽和请求记录 |
| `tools/build.ps1`、`tools/mapdln.spec` | 唯一构建流程及打包规则，生成图标、运行测试、构建及验证 EXE、备份并更新根目录入口 |
| `Mapdln.spec` | 兼容旧打包命令，仅转发到 `tools/mapdln.spec`，不维护第二套过滤规则 |
| `tools/verify_build.ps1`、`tools/verify_build.py` | 核查包内版本、体积与资源，并在隔离配置/系统 PATH/中文目录下验证冻结后台导出 |
| `tests/test_engine.py` | 几何、坐标、实际成果、缓存、预算和下载引擎回归 |
| `tests/test_map_interactions.py` | 原生 Qt 输入进入实际 WebEngine 页面，验证交互与后台锁定 |
| `tests/test_sources.py`、`tests/test_sources_gui.py` | 图源协议、配置加密、矩阵验证、GUI 向导及预览服务 |
| `tests/test_local_dem.py`、`tests/test_source_pipeline.py`、`tests/test_source_review.py` | 本地 DEM、CLI/worker、真实 localhost HTTP 导出与独立地理公式/失败恢复验证 |

界面布局：顶栏（品牌 · 下载工作台 / 图源管理 / 地图预览分段导航 · 凭据状态徽标）；工作台左侧为可滚动参数卡片（任务、地图与输出、请求预判、可折叠高级设置），右侧为地图卡片（标题状态栏 + 地图 + 选区信息）及其下方的任务进度栏（进度、暂停/继续/取消、开始下载）。图源管理页为 `SourcesPanel`（左侧图源列表 + FABDEM 卡片，右侧编辑区）；进入该页时 `navigate(1)` 调用 `sync()` 采用窗口最新配置但不重置正在编辑的表单，保存或删除通过 `changed` 信号让工作台重建“导出影像”列表。地图预览页包含本机预览地址、高德 / 百度卡片和加载测试。Mapbox 额度录入已移除，旧配置中的 `quota` / `usage` / `cycle` 字段不再读取。浅色/深色配色集中在 `gui.py` 的 `THEMES` 与 `STYLE`，地图页配色在 `map.html` 的 CSS 变量中，两处强调色需保持一致。调整布局时保留现有控件属性名（测试和逻辑直接引用），锁定选区的灰色填充 `#80889055` 受回归测试约束。

GUI 和 CLI 共用同一几何及下载引擎。“导出影像”决定成果来源，“预览底图”可跟随影像或单独选择定位底图；改变预览不得改变任务来源或已选区域。工作台把影像清晰度默认设为自动，手动层级位于高级设置；本地 DEM 控件按需展开。仅高程自动使用离线坐标预览，切回影像时恢复之前的底图选择。

图源扩展统一修改 `sources.py`，不要在 GUI、预览和引擎分别拼接地址。接受标准 Web Mercator 全球矩阵，256/512 像素 JPEG/PNG/WebP，层级 0–24（供应商自己的范围继续限制，例如 Mapbox 最大 22，腾讯/吉林预设最大 18）。WMTS 必须校验比例、原点、尺寸与范围，并保留服务的矩阵 ID；不能仅凭 CRS 字符串认作 XYZ。GCJ02 影像由 `tiles_for(grid,z,datum)` 在源坐标中规划，按目标像素反向采样；中间源影像不能冒充标准 EPSG:3857，最终成果与 DEM 共用 UTM 网格。

任务使用 `source`、`imagery`、`elevation` 与可选 `dem` 快照；凭据通过 worker stdin 的 `token` 字段传递，可为结构化字典。自定义 URL 的常见凭据参数与所有请求头使用独立 DPAPI 凭据库；导出元数据通过允许字段生成，不写 URL、Token、请求头或完整 DEM 路径。不同来源/凭据的缓存指纹不同；只有标准 Mapbox 继续复用旧缓存。腾讯 / 吉林可编辑定义位于 `provider_sources`，凭据仍位于加密 `source_credentials`；吉林 TK 对应 `token`，MK 对应 `credential_1`，完整 URL 的提取与参数顺序无关。腾讯先翻转 TMS 行号，再计算 `{x16}/{y16}`；吉林默认 `sch=wmts` 使用左上原点，移除此参数时必须核对服务行号约定。

本地 DEM 使用 `snapshot_dem → validate_dem → elevation_export`；源 mask 和有限数值决定有效像元，合法 0 / -9999 不作为缺失。单位换算为 `(raw × scale + offset) × unit_factor`，目标 NoData 为 NaN。预检和导出共用 `aligned_dem_data`，不要重复实现；主文件和伴随文件变化、缺失单位、覆盖空洞及输入输出同文件均应拒绝。真实地形/平坦模式保留原 NoData 行为。

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
- 旋转矩形按实际轮廓裁切，但输出网格仍向北对齐。影像、掩膜、DEM 和高度图必须保持相同尺寸和像素变换；PNG Alpha 还保留源透明度，不能总是假定它等于几何 mask。
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

不要在收到“取消”点击或 `done` 消息时提前解锁；以后台进程结束作为正常解锁边界。更改参数和旧估算回调不能替换下载中的选区；`plan_generation` 用于忽略已经失效的估算结果。失败后“改用 0 米继续导出”使用原任务的选区、来源和凭据快照。未完成目录的 `.mapdln-task.json` 固定选区/网格/来源/导出模式，跨任务混用应提示换目录；完成后删除该清单。临时工作目录使用程序生成的 UUID，不能将外部 task_id 拼入待清理路径。

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

构建结果为 `dist/mapdln.exe`。`tools/mapdln.spec` 包含 WebEngine、地理运行库、本机页面和图标，过滤可能与 Windows 系统 ICU 冲突的 DLL。Qt 裁剪必须在 Analysis 完成后同时处理 `a.binaries` 和 `a.datas`，因为 hooks 会在分析中补入动态库和资源；仅过滤 Analysis 输入无法排除这些文件。新增 Qt 功能时同步维护白名单及成品资源检查，不得直接删除地图所需的 Chromium 内核或地理运行库。地理库不收集测试及重复 Python 源文件；SciPy 已移除，但 NumPy 自带名称含 `libscipy_openblas` 的 DLL 必须保留。

构建使用固定虚拟环境和 PyInstaller `--clean`。完成后检查包内版本、必需页面/图标/Qt 资源、排除项及 220 MiB 体积上限，并通过冻结后台程序的缓存导出。通过后脚本自动备份旧根目录程序到 `test-output/build-backup/mapdln-<旧哈希前12位>.exe`，再更新根目录 `mapdln.exe` 并核对其与 dist 的 SHA-256；前置检查失败不会更新根目录。构建前退出正在运行的旧程序，避免文件占用。不能仅凭后台命令显示“完成”或进程暂时存活就判断打包成功。

修改 JS/HTML 后必须重新构建，单独修改源码不会更新旧 EXE 内的页面。手动复查成品及启动：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\verify_build.ps1 -ExePath .\mapdln.exe
Get-FileHash mapdln.exe,dist\mapdln.exe -Algorithm SHA256
.\mapdln.exe
```

成品检查使用独立空配置、合成瓦片缓存、禁用的外部代理及仅 Windows 系统 PATH；不会改动既有账户。报告保存在 `test-output/build-verification.json`。人工验收时正常启动根目录 EXE，读取原有账户配置，不沿用临时测试的 `MAPDLN_HOME`。

2.3.0 的纯 NumPy 平滑保留四倍 σ 截断、`int(4*sigma+0.5)` 半径、半采样对称边界和 NoData 加权；不要改回零填充边缘。对应回归位于 `tests/test_engine.py`，无需安装 SciPy。

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
| EXE 体积没变 | 核对根目录/dist 的字节数、修改时间及 SHA-256；运行 `tools/build.ps1` 并确认成品检查成功，勿使用另一套临时 spec |
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

## 8. 2.4.0 本地构建记录与回退

界面重设计提交为 `a716f99`（main，2026-10-25），提交后更新本节；当时仅完成本地构建，后续公开发行见第 10 节。仅改动 `gui.py`、`map.html`、`map.js` 的布局与样式，下载、绘图、快捷键和锁定逻辑不变。`tools/build.ps1` 完整回归 61 项通过，冻结包检查与冻结后台缓存导出通过；EXE 内 `map.html`、`map.js` 已逐字节核对与源码一致。构建日志位于 `test-output/build-rescue/build-2.4.0.log`，界面截图位于 `test-output/ui-shots/`。

根目录与 dist 的程序均为 183,775,522 字节（175.26 MiB），SHA-256 为 `d2af40fd13634ebee8cc02265c1003c6cb3e4839f2c2005293badcffd65a2611`。成品界面的人工操作、在线底图和真实下载仍需独立验收。

如需临时恢复 2.3.0 根目录入口，先退出程序，再执行：

```powershell
Copy-Item -LiteralPath test-output\build-backup\mapdln-2fdc375d47e4.exe -Destination mapdln.exe -Force
```

源码回退使用 `git revert a716f99`，再按第 5 节重新构建。

## 9. 2.3.0 本地构建记录与回退

源码及构建修复提交为 `344d369`（main，2026-10-25），在提交后更新本维护文档；未推送或发布 GitHub Release。完整回归 61 项通过，冻结程序在独立中文目录下的 51 × 51、2 米圆形缓存导出通过，实际请求数 0。地图绘制完整验收由用户核查，在线底图和真实下载仍需独立验收，不能沿用 2.2.0 的在线结论。

根目录与 dist 的最终程序均为 184,278,864 字节（175.74 MiB），比本次保留的旧根目录 EXE 减少 123,913,959 字节（118.17 MiB，40.2%）。SHA-256 为 `2fdc375d47e4f640d4e359edb606019ef6e8e6478254644e0a62c676c09b8561`。完整原因、资源范围及验证边界见 `OPTIMIZATION.md` 和 `ACCEPTANCE.md`。

如需临时恢复本次修复前的根目录入口，先退出程序，再执行：

```powershell
Copy-Item -LiteralPath test-output\build-backup\mapdln-97a94e466872.exe -Destination mapdln.exe -Force
```

该备份为 308,192,823 字节。二进制回退不改变源码或 dist；恢复新版本可从 dist 复制，或重新运行正式构建脚本。源码回退按第 6 节使用 `git revert`，不要覆盖未提交工作。旧临时 spec 及草稿说明另存于忽略目录 `test-output/build-rescue/`；这些备份、测试数据和日志不进入公开仓库。

## 10. 2.4.0 GitHub 发行记录与复核

2026-10-25 用户授权推送源码并发布 Release。发布准备提交为 `600ed0a`，在提交后补充本维护记录。该提交合并远端 `a6a9dbc` 的 README 致谢及项目用途，保留本地 2.2.0、2.3.0、2.4.0 六笔提交，不重写远端历史。发布标签为 `v2.4.0`，源码分支为 `main`；仅变更公开文档，沿用第 8 节已经构建的 EXE。

发布前重新执行 `python -m pytest -q`，61 项通过（37.96 秒）；重新执行 `tools/verify_build.py mapdln.exe`，冻结包资源和隔离中文目录下 51 × 51、2 米缓存圆形导出通过，网络请求数 0。应用七个冻结模块、主入口及十项页面/图标资源与当前源码一致；根目录与 dist 程序哈希一致。没有将此结果扩展为在线底图、真实下载或另一台 Windows 电脑验收。

发布位置：[Mapdln v2.4.0](https://github.com/fredqin2006-X/Mapdln/releases/tag/v2.4.0)。Windows 附件仅为 `Mapdln-2.4.0-Windows-x64.zip`，其中包括 EXE、README、维护文档、发行说明、变更记录、验收记录、体积记录、LICENSE 与第三方说明；不单独上传 EXE、手工源码包或校验文件。ZIP 的 SHA-256 写在 GitHub 发行说明末尾，源码使用 GitHub 自动生成的 Source code ZIP / tar.gz。

本轮本机复核记录保存在忽略目录 `test-output/release-2.4.0-build-verification.json` 与 `test-output/release-2.4.0-audit.json`。公开前检查待推送提交历史、暂存区、冻结应用代码/资源和 ZIP，排除本机真实账户值与私有文件。临时依赖分析脚本不进入本次提交；原文件留在工作区。

发行顺序：显式提交源码和文档，更新本维护记录并提交，按第 6 节白名单打包并解压验证，扫描最终历史与 ZIP，再推送 main 及同一提交的 `v2.4.0` 标签；创建草稿 Release、上传唯一 ZIP，核对远端字节数和 SHA-256 后发布。最后复核 main/tag 一致、Latest 指向及匿名下载，并检查 GitHub 自动源码归档的文件清单。未完成上传或哈希不符时保持草稿，不将失败上传记作已发布。

## 11. 2.5.0 本地构建与回退

2026-10-28 完成实现并在本地 `main` 提交 `91cf93f`，随后更新本维护文档。内容为统一图源、天地图/自定义 XYZ/TMS/WMTS、本地 DEM、仅高程模式及配套 GUI/CLI。没有推送或创建 GitHub Release。

正式构建完整回归 **145 项通过（58.35 秒）**。冻结检查在隔离配置、中文目录和仅系统 PATH 下完成标准 Mapbox 缓存导出、本地 DEM 英尺/比例/偏移处理、512px 半透明 PNG 自定义图源导出；实际请求数均为 0。根目录 EXE 实际打开后核查工作台、图源管理和添加表单。源码界面另检查了浅色/深色、1280×800 和 1200×780 布局。十个冻结应用模块及地图资源与源码一致。

真实在线验证只调用了 **1 张 Mapbox 瓦片**，成功返回 256×256 JPEG。天地图未配置 Key，一次无 Key 能力文档请求返回 HTTP 418，未调用天地图瓦片；不能把协议/离线测试当成供应商授权验证。完整范围与限制见 `ACCEPTANCE.md`。

根目录与 dist 的 EXE 均为 **184,351,373 字节（175.81 MiB）**，SHA-256 为 `c500e7f6ee2eed95d66bbf62f2dcc7afa2b27951f3eb487ca0ae95b84f3cd084`。旧 2.4.0 根目录程序已经备份到 `test-output/build-backup/mapdln-d2af40fd1363.exe`。日志和报告位于 `test-output/build-2.5.0.log`、`test-output/build-verification.json`、`test-output/artifact-audit-2.5.0.json`；界面截图位于 `test-output/source-ui-250/`。

回退源码使用 `git revert 91cf93f` 并重新构建；临时恢复旧入口前先退出程序，再复制上述备份到根目录 `mapdln.exe`。回退为 2.4.0 后新增图源及本地 DEM 配置不再可用，应先保留用户配置副本；不要让旧版保存操作覆盖新配置。

## 12. 2.6.0 腾讯兼容 / 吉林一号接入与回退

2026-10-28 完成实现并在本地 `main` 提交 `904b1e0`，随后更新本维护文档。增加腾讯卫星兼容适配、吉林一号 TK / MK 表单与服务配置、WebP 支持、GCJ-02 预览及影像反向采样。没有推送或创建 GitHub Release。

正式构建完整回归 **178 项通过（68.88 秒）**；五种冻结后台导出全部通过，包含 Tencent GCJ-02 与 Jilin WebP / 凭据隔离。独立公式验证渐变影像的地理位置、半透明边缘、z24 与转换边界；实际 Qt / Leaflet 切换预览保留原选区。浅色 / 深色以及最小管理窗口截图检查通过，成品 GUI 工作台和图源管理可实际打开。十个冻结应用模块及地图资源与源码一致，公开输入及冻结应用代码真实凭据扫描通过。

腾讯研究请求及程序内置适配器分别成功请求 1 张 z17 JPEG；后者复用缓存实际导出 40×40 米区域，影像 / DEM / 掩膜同网格。该兼容接口没有官方批量下载 API 稳定性承诺，未验收全球覆盖、全部层级、长期稳定或测量级定位。用户确认没有吉林 TK / MK，因此只完成协议、凭据、localhost WebP、预览和冻结导出的离线验证，真实服务联调待用户配置后进行。不得把这些结果记成吉林真实账户验证通过。

根目录与 dist 的 EXE 均为 **184,363,456 字节（175.82 MiB）**，SHA-256 为 `fcada926545b6f3412a579c7e4bc9d6622a8c7653165a08a594fafd572b8c015`。2.5.0 程序备份在 `test-output/build-backup/mapdln-c500e7f6ee2e.exe`。报告位于 `test-output/build-2.6.0.log`、`test-output/build-verification.json`、`test-output/artifact-audit-2.6.0.json`、`test-output/tencent-live-export-260/result.json`；界面截图位于 `test-output/provider-ui-260/`。

回退源码可对 `904b1e0` 执行 `git revert` 后重新构建；临时回退入口应先关闭程序，再复制上述 2.5.0 备份到根目录。回退前保留用户配置副本：2.5.0 不认识内置 `tencent` / `jilin` 及 GCJ02 栅格 / WebP，新版本的 `source_credentials` 不应被旧版保存操作丢弃。

## 13. 2.6.1 下拉框位置与天地图验证

2026-10-28 在本地 `main` 提交实现 `f55ba18`，随后更新本维护文档。工作台、账户页和图源管理统一使用 `widgets.FlatCombo`；下拉框从控件底边展开，临时禁用本次 Qt 原位置动画后恢复全局效果设置。长列表最多十项，按屏幕下方空间缩短高度并保留滚动，右侧边缘约束横向位置。控件底边已经超出屏幕可用区时保留 Qt 的可见性处理。没有推送、创建 Release 或生成完整发行 ZIP。

正式构建完整回归 **178 项通过（70.11 秒）**，五种冻结后台导出通过。独立实际 Qt 的 9 组几何 / 交互检查及浅色、深色截图通过；成品实际启动到 2.6.1 离线工作台。用户用物理 Esc 停止 Computer Use 后未继续点击成品下拉框，成品点击验收不记为通过。11 个冻结应用模块及页面资源与源码一致，公开输入和冻结应用模块未发现真实凭据，私有文档未纳入程序。

天地图的两条影像服务均有效：`img_w` 为 Web Mercator、`img_c` 为 CGCS2000 经纬度。用户本次提供的浏览器端 Key 在真实浏览器中可读取两种 z12 JPEG；普通 HTTP / HTTPS 客户端在 z3/z12/z18 均返回 403 / `301012`，程序内置下载也被拒绝，原因是 Key 权限类型。内置 `img_w` 配置不需改投影，但应使用允许普通客户端调用的 Key。通用导入器对 c 经纬度矩阵及该 w 能力文档的原点轴序仍会拒绝，本轮未放宽校验。不要把元数据成功或网页可访问记作桌面下载授权通过；不保存本轮凭据至公开文件。

根目录 / dist 的 EXE 均为 **184,363,800 字节（175.82 MiB）**，SHA-256：`5ad1d98016c5cf8b9603db69e35bc929d347fa387d3cd389d2062e33aa060e3b`。2.6.0 入口备份为 `test-output/build-backup/mapdln-fcada926545b.exe`。构建日志：`test-output/build-2.6.1.log`；五种导出报告：`test-output/build-verification.json`；源码 / 成品核对、网络响应、样本及界面位置报告：`test-output/tianditu-check-20261028/`，网络结论见其中 `RESULT.md`。

回退源码使用 `git revert f55ba18` 后重新构建；临时恢复 2.6.0 入口前先退出程序，再复制上述备份至根目录 `mapdln.exe`。本版未改账户配置结构。

同日用户提供新建的服务端 Key 后补测，并在本地 `main` 提交验收记录 `c422622`，随后更新本段。两种投影在普通客户端、HTTP / HTTPS、z3/z12/z18 的 12 次瓦片请求全部成功并解码为 JPEG；当前 2.6.1 成品在新隔离配置 / 缓存中以零重试完成北京 40×40 米、1 米网格导出，实际新增请求 1 次，影像 / 掩膜 / 0 米 DEM / 高度图同网格。详见 `test-output/tianditu-serverkey-20261028/RESULT.md` 与 `result.json`。没有修改程序、重建、改变实际账户配置或记录 Key 至公开文件；成品版本和哈希沿用本节已有记录。此前浏览器端 Key 的 403 结论仅适用于旧 Key，不再代表新服务端 Key 的验证结果。

## 14. 2.7.0 图源 / 预览界面合并与回退

2026-10-28 在本地 `main` 提交实现 `365d6e0`，随后更新本维护文档。顶栏改为“下载工作台 / 图源管理 / 地图预览”；原图源弹窗改为页面内 `SourcesPanel`（列表 + 编辑区），Mapbox Token 说明并入其中；高德 / 百度凭据、本机预览地址和加载测试移至地图预览页；删除 Mapbox 手动额度卡片与工作台“参考剩余额度”。下载、预览、凭据存储和图源协议逻辑未改。没有推送、创建 Release 或生成发行 ZIP。

正式构建完整回归 **179 项通过（75.69 秒）**，新增嵌入式图源页与工作台“导出影像”同步的回归；冻结包检查与冻结后台缓存导出通过。源码界面在浅色 1391×（屏幕限制）及深色 1200×780 下截图检查三页布局；成品在隔离配置中实际启动、无启动错误。成品界面的人工点击操作未在本轮验收。

根目录 / dist 的 EXE 均为 **183,856,264 字节（175.34 MiB）**，SHA-256：`eae978be2c7cd2af690ff0605736e68e063ffba7d042741943d18907fabcddea`。2.6.1 入口备份为 `test-output/build-backup/mapdln-5ad1d98016c5.exe`。构建日志：`test-output/build-2.7.0.log`；截图：`test-output/ui-270/`。

回退源码使用 `git revert 365d6e0` 后重新构建；临时恢复 2.6.1 入口前先退出程序，再复制上述备份至根目录 `mapdln.exe`。本版未改配置结构，旧额度字段保留在配置中，回退后仍可读取。
