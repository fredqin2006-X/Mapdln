# Mapdln 2.1.0 · Windows x64

首次公开发行，支持 Windows 10 / 11 x64。解压 Windows ZIP 后双击 `mapdln.exe`，无需安装 Python / Qt / GDAL。请在账户页填写自己的 Mapbox Token 和所需预览平台凭据。 / First public release for Windows 10 / 11 x64. Extract the Windows ZIP and launch the EXE; no Python installation is required. Configure your own credentials in the account page.

## 🚀 新特性 / Features

- 高德、百度和 Mapbox 地图预览；矩形、圆形、多边形、插值曲线选区与控制点编辑。 / Multiple map previews and four editable selection shapes.
- 卫星、8/16 位高度图、真实 FABDEM 或纯黑 0 米地形；统一米制网格、透明轮廓、GeoTIFF、掩膜和元数据。 / Aligned satellite and elevation exports, including real terrain or flat zero-metre mode.
- 左侧任务进度与暂停/继续/取消；两列账户页和高级设置，统一扁平界面及地图下载图标。 / Sidebar task controls, compact settings and a new map-and-download icon.
- 额度预判、有效缓存、请求记录、本机预算与 Windows 用户加密凭据。 / Request estimation, validated caching, local budgets and encrypted credentials.

## 🐛 错误修复 / Bug Fixes

- 修正高德安全转发路由、启动时暂停状态覆盖和 Windows 后台日志编码。 / Fix AMap security routing, early pause handling and worker encoding.
- 移除数字上下按钮，避免失焦表单被滚轮意外修改；资源和后台进程按实际程序位置定位。 / Improve form behavior and resolve resources and workers dynamically.

## ⚠️ 破坏性改动 / Breaking Changes

- 相比上游 CLI，无参数启动 GUI；移除明文 `--token` 参数，改用账户配置或 `MAPBOX_TOKEN` 环境变量。 / Compared with the upstream CLI, no arguments launch the GUI; plaintext token arguments are replaced by encrypted settings or environment variables.
- 成果使用独立任务目录，不覆盖已完成成果；`--mode tile` 输出卫星及 0 米高程。 / Completed exports are protected; tile mode produces satellite and flat elevation outputs.

## ⚡ 性能优化 / Performance Improvements

- 有界并发、稀疏磁盘拼接和分块重采样；FABDEM 使用带校验的 HTTP Range 断点缓存。 / Bounded downloads, block-wise export and validator-aware terrain range caching.
- 先导出卫星，地形失败保留成果；0 米模式完全跳过 FABDEM。 / Preserve satellite results when terrain fails and skip terrain networking in flat mode.

## 📖 文档与依赖更新 / Documentation & Dependencies

- MIT 开源，保留上游作者信息；附使用说明、构建脚本、图标原稿、第三方许可与 SHA-256 校验。 / MIT application source with upstream attribution, build instructions, editable icons and dependency notices.
- 26 项自动测试、界面操作、移动目录启动与导出检查通过；用户已确认地图下载测试通过。百度实际授权尚未验证，详见验收记录。 / Automated and interactive checks passed; map download acceptance was confirmed by the user. See the acceptance record for coverage and remaining limits.
- 账户配置、测试凭据、私有设计文档及下载数据不包含在仓库或附件中。 / Account settings, test credentials, private planning documents and downloaded data are excluded.

---
**完整变更记录**：[CHANGELOG.md](https://github.com/fredqin2006-X/Mapdln/blob/v2.1.0/CHANGELOG.md) · [验收记录 / Acceptance](https://github.com/fredqin2006-X/Mapdln/blob/v2.1.0/ACCEPTANCE.md)
