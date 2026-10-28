# Mapdln 2.5.0 · Windows x64（本地构建）

2026-10-28。本版完成图源与高程扩展，尚未推送或发布 GitHub Release。双击本地 `mapdln.exe` 使用。

## 🚀 新特性 / Features

- 工作台独立选择导出影像、预览底图与高程来源；图源管理支持 Mapbox、天地图、自定义 XYZ / TMS / 标准 Web Mercator WMTS。
- 自定义图源支持 JPEG / PNG、256 / 512 像素、透明度、加密凭据、连接测试及无凭据配置导入导出。
- 本地 GeoTIFF DEM 支持选择/拖入、覆盖定位、单位与原始分辨率检查；仅高程模式可完全离线处理本地数据。
- 自动推荐影像层级，按图源隔离缓存并统计请求。

## 🐛 错误修复 / Bug Fixes

- 实际成果使用所选图源，保留来源透明度；元数据准确记录影像及高程来源。
- 本地 DEM 正确处理高程比例、偏移、米/英尺、零值、负值及 NoData，防止覆盖原始文件。
- 拒绝不同图源/选区/导出模式混用未完成目录；在下载前检查本地 DEM 覆盖和缺失像素。

## ⚠️ 破坏性改动 / Breaking Changes

- CLI 默认层级为自动，可通过 `-z` 手动指定。旧 Mapbox 缓存与已有配置保留。
- 本地 DEM 的 NoData 为 NaN；仅高程模式不生成 `satellite.png`。
- 首版自定义服务限标准 EPSG:3857 全球瓦片矩阵；腾讯、吉林一号专用导出尚未启用。

## ⚡ 性能优化 / Performance Improvements

- 继续使用分块拼接、重投影与裁切；图源共享缓存，按服务限制并发和请求速率。
- 本地 DEM 使用已有地理运行库，无新增大型运行依赖。

## 📖 文档与依赖更新 / Documentation & Dependencies

- 更新使用、维护、第三方数据与验收文档；依赖版本不变。
- 验证覆盖源码、真实 Qt 交互、本地 HTTP 服务以及冻结 EXE；测试范围与未验证项见验收记录。
- 后续授权发布时，Windows 仅提供完整 ZIP；源码采用 GitHub 自动归档。

---
**完整变更记录**：[CHANGELOG.md](CHANGELOG.md) · [验收记录](ACCEPTANCE.md)
