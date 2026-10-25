# Mapdln 2.3.0 构建与体积记录

2026-10-25，本机 Windows x64 实测。单位 MiB = 1,048,576 字节；下列数字是单文件 EXE 的实际长度，不是依赖解压后的大小。

| 文件 | 字节数 | MiB |
|---|---:|---:|
| 修复前根目录 `mapdln.exe` | 308,192,823 | 293.92 |
| 修复后根目录及 `dist/mapdln.exe` | 184,278,864 | 175.74 |
| 减少 | 123,913,959 | 118.17（40.2%） |

最终 SHA-256：`2fdc375d47e4f640d4e359edb606019ef6e8e6478254644e0a62c676c09b8561`。

## 体积未更新的原因

- 原构建只生成 `dist/mapdln.exe`，根目录入口没有自动同步。本次检查时根目录仍是 308,192,823 字节，而 dist 已经存在 184,597,313 字节的较新构建。
- 根目录旧 `Mapdln.spec` 和 `tools/mapdln.spec` 有不同的规则。旧 spec 在 Analysis 之前过滤手动收集的 binaries，无法过滤后续 Qt hooks 补入的资源。
- Bash 中找不到 PyInstaller，或 PowerShell 没有使用项目虚拟环境，会让某些尝试在真正打包前就失败；命令返回不代表 EXE 已更新，必须检查退出码、最终文件和哈希。
- 旧记录把预估、解压大小和最终压缩大小混用；shapely 测试数据实际只占很小部分，不能声称单独删除它就能减小几十 MiB。

## 当前构建规则

唯一规则来源是 `tools/mapdln.spec`，根目录 `Mapdln.spec` 仅作兼容转发。推荐统一使用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\build.ps1
```

脚本依次生成图标、运行完整测试、用 `.venv/Scripts/python.exe -m PyInstaller --clean` 构建、核查冻结包和后台导出，然后备份旧根目录 EXE、同步新 EXE，并核对 SHA-256。一旦前面任一步失败，不更新根目录入口；退出旧程序后再构建，避免文件占用。

- 地理库保留运行数据、动态库和动态导入，排除测试模块，不再把 Python 源文件当作数据重复打包。
- Qt 在 Analysis 完成后同时过滤 `a.binaries` 和 `a.datas`，保留实际链接依赖、Widgets/WebEngine、SVG、Windows 平台/TLS 插件和中英文资源；剔除未用的 QML 插件、开发者工具资源和软件 OpenGL 回退 DLL。
- 移除 SciPy Python 依赖；纯 NumPy 平滑已修正核半径取整和边缘反射，保留原 NoData 加权处理。浮点运算仍可能有舍入差异，不承诺所有输入逐位相同。
- NumPy 的 `libscipy_openblas...dll` 是 NumPy 必需运行库，不能按文件名包含 scipy 就删除。
- UPX 未启用；本次缩小来自依赖裁剪，不依赖额外压缩工具。

## 验证记录与边界

- 61 项源码回归通过，含原生 Qt 离线交互与后台进程；新增 7 项平滑核和图像边界检查。
- 实际 EXE 冻结包检查通过：版本 2.3.0，地图页面、图标、WebEngine 运行库与资源齐全，未包含 SciPy、地理库测试、QML 插件和开发者工具资源。
- 实际 EXE 在中文/空格工作目录、独立空配置和仅 Windows 系统 PATH 下，完成 51 × 51、2 米网格的合成缓存圆形导出。卫星 Alpha 与掩膜一致，DEM 有效像素为 0、外部为 NoData，高度图全黑，Mapbox 请求数为 0。
- 实际 EXE 已启动并显示新界面和离线编辑器，账户页可以切换；源码交互检查和成品冒烟不替代用户的地图绘制验收。用户负责本轮地图绘制核查；本轮未重新验收在线底图、真实网络下载或另一台 Windows 电脑。

详细机器报告在忽略目录 `test-output/build-verification.json`；构建日志在 `test-output/build-rescue/build-2.3.0.log`。旧根目录 EXE 备份为 `test-output/build-backup/mapdln-97a94e466872.exe`，旧临时 spec 与草稿说明保存在 `test-output/build-rescue/`。

## 剩余体积

`Qt6WebEngineCore.dll` 在 EXE 中压缩后仍约 79.36 MiB，是当前最大单项；它提供实际地图页面所需的 Chromium 内核。其余主要体积来自 GDAL/PROJ/GEOS、NumPy 和 Qt 依赖。继续显著减小需要另行评估地图容器或运行库方案，不能直接删除必需 DLL。

此记录未进行启动时间、内存和大型地形平滑性能基准；不沿用旧草稿中未经测量的性能百分比。本版本仅本地构建，未推送或发布 GitHub Release。
