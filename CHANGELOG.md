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
