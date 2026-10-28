# v2.6.1 — 2026-10-28（本地构建）

## 🐛 错误修复 / Bug Fixes

- 工作台、账户页和图源管理共用下拉控件，列表在选项框正下方展开，不随当前选中项上下偏移。
- 长列表最多显示十项，屏幕下方空间不足时缩短弹出区域并允许滚动；靠近屏幕右侧时保持列表可见。

## 📖 文档与依赖更新 / Documentation & Dependencies

- 核实天地图影像的经纬度 / Web Mercator 服务及浏览器端 Key 的访问限制，详见验收记录；无新增依赖。

---
**完整变更记录**：以下历史版本与 [验收记录](ACCEPTANCE.md)。

# v2.6.0 — 2026-10-28（本地构建）

## 🚀 新特性 / Features

- 增加腾讯卫星兼容图源，支持 TMS 反向行号、16 张分组路径和可编辑服务配置。
- 增加吉林一号 getMap 预设、访问 TK / 图层 MK 独立输入与加密、企业项目参数保留及 WebP 瓦片支持。
- 通用 GCJ-02 栅格预览和导出：按目标像素反向采样至与 DEM 一致的 UTM 网格。

## 🐛 错误修复 / Bug Fixes

- 避免 GCJ-02 影像被直接标作 WGS84；规划瓦片时考虑坐标偏移及插值边缘。
- 透明源像素采用预乘 Alpha 双线性采样，避免隐藏颜色污染边缘。
- 完整服务地址中的 MK / TK 提取与参数顺序无关，分享配置与日志均不携带凭据。

## ⚠️ 破坏性改动 / Breaking Changes

- 无已有配置迁移要求。腾讯兼容接口没有官方批量下载 API 稳定性承诺；吉林一号需要用户自己的服务参数。
- GCJ-02 使用本地近似转换，输出像素分辨率不代表定位精度，不用于测量级定位。

## ⚡ 性能优化 / Performance Improvements

- GCJ-02 采用向量化目标块重采样与最多 16 个源块缓存；保留暂停、取消及请求预算。

## 📖 文档与依赖更新 / Documentation & Dependencies

- 更新图源使用与来源说明；无新增运行依赖。
- 增加提供商协议、实际 Qt 预览切换、独立坐标公式、localhost WebP 下载及冻结 EXE 导出验证。

---
**完整变更记录**：以下历史版本与 [验收记录](ACCEPTANCE.md)。

# v2.5.0 — 2026-10-28（本地构建）

## 🚀 新特性 / Features

- 增加独立导出影像选择与图源管理：Mapbox、天地图影像、自定义 XYZ / TMS，以及通过能力文档读取的标准 Web Mercator WMTS。
- 自定义图源支持 JPEG / PNG、256 / 512 像素、透明度、子域、矩阵标识、加密 Token / 请求头、主动测试与无凭据配置分享。
- 支持本地单波段 GeoTIFF DEM 选择或拖入、覆盖边界、单位检查与定位；新增仅高程导出，不再强制配置 Mapbox。
- 自动推荐影像层级，保留手动设置；请求量按来源统计，图源/账户分别使用缓存。

## 🐛 错误修复 / Bug Fixes

- 导出不再固定使用 Mapbox；元数据记录实际影像及高程来源，避免预览来源与成果来源混淆。
- 本地 DEM 保留合法零值、负高程和 -9999 数值，正确应用比例、偏移及英尺转换；缺失区域不会补零。
- 在影像请求前预检本地 DEM，并防止输入输出同文件、原始文件/伴随文件变化及跨来源复用未完成成果目录。
- 保留 PNG 半透明度；按真实瓦片尺寸拼接；身份验证/限流/错误图片响应给出明确错误，不自动混用另一图源。

## ⚠️ 破坏性改动 / Breaking Changes

- CLI 默认影像层级改为自动推荐，可用 `-z 17` 等保留手动层级；旧参数及标准 Mapbox 缓存兼容。
- 本地 DEM 成果使用 NaN 表示 NoData，有效 -9999 和 0 均可保留；平坦/FABDEM 成果沿用原编码。仅高程成果不生成 `satellite.png`。
- 自定义图源首版限定标准 EPSG:3857 全球矩阵；不支持 GCJ-02 / BD-09 影像、局部偏移矩阵或矢量瓦片。腾讯、吉林一号专用在线导出仍待接口与授权验证。

## ⚡ 性能优化 / Performance Improvements

- 图源共用块式米制网格导出与缓存，避免整幅影像常驻内存；本地 DEM 重用已打包的 Rasterio / GDAL。
- 按图源设置并发与请求速率；读取本地 DEM 和 WMTS 在后台进行。

## 📖 文档与依赖更新 / Documentation & Dependencies

- 更新 GUI / CLI 使用说明、图源范围、第三方数据说明及维护记录，运行依赖不变。
- 增加本地 HTTP 图源端到端、WMTS 矩阵、来源隔离、凭据保护、地理位置与本地 DEM 回归；冻结成品新增本地 DEM 和自定义 PNG 检查。实际结果见 [ACCEPTANCE.md](ACCEPTANCE.md)。

---
**完整变更记录**：[CHANGELOG.md](CHANGELOG.md) · [验收记录](ACCEPTANCE.md)


# v2.4.0 — 2026-10-25

2026-10-25。源码和 Windows EXE 版本为 2.4.0，本次公开发行包含下方 2.2.0、2.3.0 的本地构建更新。Windows 使用完整 ZIP，源码使用 GitHub 自动生成的归档；汇总说明见 [RELEASE-NOTES.md](RELEASE-NOTES.md)。

## 🚀 新特性 / Features

- 重构桌面界面布局：顶栏改为品牌 · 分段导航 · 凭据状态徽标；工作台左侧为参数卡片（任务、地图与输出、额度预判、可折叠高级设置），任务进度与“开始下载”移到地图下方的任务栏。
- 额度预判改为数字卡片：所需瓦片、有效缓存、新增请求、含重试预留、预计网络和输出像素；分辨率预设高亮当前值。
- 地图工具条改为带图标的分组浮动工具条并提示快捷键；Leaflet 缩放按钮移到右下角，地图状态统一显示在地图卡片标题栏。
- 浅色/深色主题统一为设计稿的青绿色强调色，桌面界面与地图页面配色一致；终端日志使用深色等宽字体，删除/取消类操作悬停显示警示色。
- 账户页卡片增加用途说明，申请引导以提示框呈现，地图加载测试独立成卡片。

## 🐛 错误修复 / Bug Fixes

- 修正桌面界面使用蓝色、地图页使用青绿色导致的强调色不一致。

## ⚠️ 破坏性改动 / Breaking Changes

- 无。下载、绘图、快捷键、锁定逻辑及数据、配置与成果格式不变；仅移除与“高级设置”展开按钮重复的“收起高级设置”按钮，改由同一按钮展开/收起。

## ⚡ 性能优化 / Performance Improvements

- 单文件 EXE 为 175.26 MiB，与 2.3.0 基本持平。

## 📖 文档与依赖更新 / Documentation & Dependencies

- 维护文档补充界面结构与配色位置说明；依赖无变化。
- 61 项源码回归、冻结包检查和冻结后台缓存导出通过；本轮未复验在线底图和真实下载。

---
**完整变更记录**：[CHANGELOG.md](CHANGELOG.md) · [验收记录](ACCEPTANCE.md)


# v2.3.0 — 2026-10-25（本地构建）

2026-10-25。本地构建版本 2.3.0，未发布 GitHub Release。

## 🚀 新特性 / Features

- 更新桌面工作台和账户页的蓝色主题、浅色/深色配色、圆角、间距及导航样式；保留现有任务与地图工具。

## 🐛 错误修复 / Bug Fixes

- 构建通过成品检查后自动备份并替换根目录 EXE，核对其与 dist 的 SHA-256，解决双击入口仍为旧文件的问题。
- 根目录旧 spec 改为转发到唯一打包规则，Qt 裁剪在依赖分析完成后执行，覆盖 hook 后续收集的文件。
- 修正纯 NumPy 高斯平滑的小数 σ 核半径及图像边缘反射处理，保留 NoData 加权逻辑。

## ⚠️ 破坏性改动 / Breaking Changes

- 数据、配置与成果格式不变；窗口最小尺寸调整为 1200 × 780。精简包保留英文与简体/繁体中文 WebEngine 资源，不含内置开发者工具及独立软件 OpenGL 回退库。

## ⚡ 性能优化 / Performance Improvements

- 根目录单文件 EXE 实测从 293.92 MiB 降至 175.74 MiB，减少 118.17 MiB（40.2%）。
- 移除 SciPy 依赖、未用 Qt 模块与 QML 插件、开发者工具资源、地理库测试及重复 Python 源文件。未启用 UPX。

## 📖 文档与依赖更新 / Documentation & Dependencies

- 新增成品归档检查和独立目录下的冻结后台导出验证；使用固定虚拟环境并清理 PyInstaller 缓存后构建。
- 61 项源码回归及冻结程序合成缓存导出通过；地图绘制由用户核查，本轮未复验在线底图和真实下载。
- 更新体积记录、维护说明和第三方依赖说明；删除旧草稿中未经实测的性能数字。

---
**完整变更记录**：[CHANGELOG.md](CHANGELOG.md) · [体积记录](OPTIMIZATION.md) · [验收记录](ACCEPTANCE.md)


# v2.2.0 — 2026-10-25（本地构建）

## 🚀 新特性 / Features

- 矩形增加旋转手柄，Shift 按 15° 对齐；角点缩放、整体移动和实际下载保留旋转角度。
- 多边形与闭合向心 Catmull–Rom 插值曲线随鼠标显示闭合预览；完成时仅提交已点击的控制点，Esc 可取消绘制。
- 增加 Delete 删除整个选区/草稿、Enter（含数字键盘）闭合多边形/曲线；右键仅删除草稿或已闭合图形的控制点，不触发闭合。
- 按住滚轮中键拖动地图，支持选区内及绘制过程中平移并保留草稿。
- 下载期间选区变灰并锁定；暂停和预算等待保持锁定，后台取消结束或下载完成后自动解锁，地图仍可平移、缩放。

## 🐛 错误修复 / Bug Fixes

- 选区、控制点和绘图覆盖层上的滚轮事件传递给实际地图底图，恢复地图缩放。
- 锁定同时覆盖前端、WebChannel 接口和坐标表单，重载底图保留锁定；后台启动失败或异常退出后恢复编辑。
- 清除后重置面积说明并回传空选区，避免连续拖动后的旧响应恢复已删除图形；地图空白处右键不弹出浏览器菜单。

## ⚠️ 破坏性改动 / Breaking Changes

- 无；旧矩形默认角度为 0，原选区及成果格式兼容。旋转矩形沿用北向米制输出网格，通过轮廓和掩膜裁切。

## ⚡ 性能优化 / Performance Improvements

- 插值曲线预览在本机按屏幕误差自适应采样；编辑提交按动画帧合并，预览不触发下载估算或地图服务请求。

## 📖 文档与依赖更新 / Documentation & Dependencies

- 新增 `MAINTENANCE.md`，涵盖架构、交互、锁定生命周期、测试构建、交付、回档及排障；更新快捷键说明，增加原生 Qt 键盘/右键回归检查，运行依赖不变。

---
**完整变更记录**：本文件；2.2.0 当前仅本地构建，未推送或发布 GitHub Release。

# v2.1.0 — 2026-10-07

## 🚀 新特性 / Features

- 左侧任务进度与操作区；坐标、网络参数和日志收进高级设置，字段采用两列布局。 / Sidebar task controls and two-column advanced settings.
- 两列账户卡片、凭据与额度字段，统一浅色/深色扁平控件、下拉菜单及滚动条。 / Compact account cards and consistent flat controls in both themes.
- 新增折叠地图、定位标记与下载箭头图标，应用于窗口、任务栏和 EXE。 / A new map-and-download application icon.

## 🐛 错误修复 / Bug Fixes

- 启动阶段收到暂停后，初始化消息不再错误覆盖暂停状态。 / Preserve pauses received during task startup.
- 表单未聚焦时滚轮不再意外改变数值或预览来源。 / Avoid accidental input changes when scrolling an unfocused form.

## ⚠️ 破坏性改动 / Breaking Changes

- 无；详细坐标入口移至高级设置，原有配置和成果格式兼容。 / None; coordinate settings move into the advanced panel.

## ⚡ 性能优化 / Performance Improvements

- 展开设置仅滚动左侧，地图保持可见面积；减少常用界面的纵向占用。 / Preserve map space while editing settings.

## 📖 文档与依赖更新 / Documentation & Dependencies

- 更新界面说明与验收记录，提供 SVG 原稿和图标生成脚本；运行依赖不变。 / Update documentation and include editable icon sources without new runtime dependencies.

---
**完整变更记录**：[CHANGELOG.md](https://github.com/fredqin2006-X/Mapdln/blob/v2.1.0/CHANGELOG.md)。首次公开发行包含 v2.1.0 的完整源码快照。 / The first public release includes the complete v2.1.0 source snapshot.

# v2.0.0 — 2026-10-07

## 🚀 新特性 / Features

- 双击 EXE 启动 PySide6 GUI；独立账户页、官方凭据申请引导、连接测试及 Windows DPAPI 加密配置。 / Launch a GUI by double-clicking the EXE; independent account settings, setup guides, connection tests and encrypted credentials.
- 高德、百度、Mapbox 预览与卫星下载分离；矩形、圆形、多边形、闭合向心 Catmull–Rom 曲线框选和编辑。 / Separate preview providers from satellite downloads, with four editable selection shapes.
- 统一 UTM 网格、透明卫星图、8/16 位高度 PNG、GeoTIFF、掩膜、GeoJSON 和元数据；支持真实 FABDEM 或纯黑 0 米地形。 / Aligned metric outputs with real FABDEM or flat zero-metre elevation.
- 请求估算、有效缓存扣除、分类用量记录、手动账户用量、任务预算及后台暂停/继续/取消。 / Request estimates, validated caching, local request accounting, manual usage baselines and process controls.

## 🐛 错误修复 / Bug Fixes

- 改正经纬度近似比例和形状外数据输出，保护有效 0 米高程与 NoData 的区别。 / Replace approximate degree-based scaling and preserve the distinction between zero elevation and NoData.
- 固定高德安全服务一级路由；后台通信统一 UTF-8，避免 Windows 冻结包日志乱码。 / Correct AMap security routing and normalize worker IPC encoding.
- 移除高程下拉选项中的“约 30 米”标注；默认输出跟随系统下载目录，地图资源与后台进程按实际程序位置定位。 / Simplify the elevation label and resolve output, resources and workers dynamically for portable launch.

## ⚠️ 破坏性改动 / Breaking Changes

- 无参数运行现在启动 GUI；旧的 `--token` 明文命令行参数移除，改用本机加密配置或环境变量。 / No arguments launch the GUI; plaintext token arguments are replaced by encrypted settings or environment variables.
- 输出改为独立任务目录，完整成果不静默覆盖；`--mode tile` 生成卫星与 0 米高程。 / Exports use separate task folders and completed exports are protected.

## ⚡ 性能优化 / Performance Improvements

- 下载并发可控、稀疏瓦片磁盘拼接、WarpedVRT 分块重采样和掩膜，避免全区域影像一次性载入内存。 / Bounded concurrency, sparse disk mosaics and block-wise reprojection reduce working memory.
- FABDEM 按 HTTP Range 分段读取和断点复用；先导出卫星，地形失败仍保留成果。 / Validator-aware range caching and satellite-first export preserve usable results.

## 📖 文档与依赖更新 / Documentation & Dependencies

- 更新使用说明、CLI 迁移、源码构建、网络与额度边界及第三方组件说明。 / Update GUI/CLI instructions, build steps and dependency notices.
- Windows Python 3.13 + PySide6 / Rasterio / pyproj / Shapely / PyInstaller。 / Package a standalone Windows runtime and shared geographic engine.

---
**完整变更记录**：v2.0.0 为本地功能基线，其变更随首次公开的 v2.1.0 源码一并提供。 / The v2.0.0 local baseline is included in the first public v2.1.0 source snapshot.
