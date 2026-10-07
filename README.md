# Mapdln 2.1 · 可视化地图下载器

致谢：@nings在群里提供的Python源码，此程序基于核心Python源码开发

本程序开发目的为了适配ALIGNMENT : An Engineering Odyssey Demo中的“自由创造”板块开发，方便玩家下载高程与地形图

**双击 `mapdln.exe` 直接进入 GUI，无需安装 Python、Qt 或 GDAL。** 本工具在 [NingsingM/mapdln](https://github.com/NingsingM/mapdln) 的卫星与地形下载功能基础上增加图形界面，并统一 GUI / CLI 下载引擎。

[下载 Windows 2.1.0](https://github.com/fredqin2006-X/Mapdln/releases/tag/v2.1.0) · [开源仓库](https://github.com/fredqin2006-X/Mapdln) · [发行说明](RELEASE-NOTES.md)

建议下载 `Mapdln-2.1.0-Windows-x64.zip`，内含程序、使用说明与第三方许可；也提供源码 ZIP 和 SHA-256 校验。开源仓库及发行包均不携带开发者账户配置，首次使用请填写自己的凭据。

## 使用

1. 解压交付 ZIP 后双击 `mapdln.exe`。单文件版首次启动需解压运行库到 Windows 临时目录，通常等待几秒。
2. 在顶部“账户配置”页保存 Mapbox ID 和 `pk.` 公共 Access Token。程序不使用账户密码；Token 用于卫星下载，ID 用于显示。
3. 按需配置高德 Web 端（JS API）Key + `securityJsCode`，或百度浏览器端 AK。高德为默认预览；也可切换 Mapbox 卫星预览。每个平台提供获取步骤、官方入口和主动连接测试。Mapbox 连接测试发起 **1 次** 瓦片请求，测试前按钮明确标注消耗。
4. 回到工作台，绘制矩形、圆形、多边形或插值曲线；也可展开左侧“高级设置”，在两列坐标表单中输入 WGS84 中心坐标和米制尺寸后定位。多边形和曲线逐点绘制后点击“完成”。白色控制点可拖动，拖动选区可移动；双击面内可增加最近边上的控制点，右键控制点可删除（至少保留三点）。
5. 默认输出 1 米/像素；根据需要选择真实 FABDEM 或纯黑 0 米高程。查看预计新增请求、缓存与资源消耗，选择输出位置后“开始下载”。

任务进度、开始下载、暂停/继续/取消位于左侧，地图保持主要面积。账户配置按两列卡片排列，凭据与额度字段也并排显示，教程按需展开。详细坐标、代理、并发、重试、超时、位深、平滑和日志收在高级设置中，参数使用两列布局。数值可直接输入或用键盘调整，取消鼠标上下箭头；表单失焦时滚轮用于页面滚动。

账户页面切换保留选区和任务状态，后台任务继续运行，并固定使用启动时的参数和凭据。默认 0 米模式便于先获取影像；真实地形失败后点“改用 0 米继续导出”，有效卫星缓存无需重新下载。应用使用折叠地图、定位标记和向下箭头组合图标；可编辑原稿位于 `mapdln/assets/app.svg`，构建时自动生成多尺寸 ICO 和 PNG。

## 成果

每个任务独立目录包含：

| 文件 | 内容 |
|---|---|
| `satellite.png` | 真实形状外透明的 RGBA 卫星图 |
| `height.png` | 8/16 位高度 PNG，默认 16 位；0 米模式全部为黑色 |
| `dem.tif` | 实际高程（米）、投影和 NoData；有效的 0 米不作为 NoData |
| `mask.png` | 选区有效轮廓，内 255 / 外 0 |
| `boundary.geojson` | WGS84 边界；曲线保存采样后的闭合面 |
| `metadata.json` | 分辨率、网格变换、尺寸、编码范围、原始控制点、插值方式、请求数 |
| `task.log` | 已遮蔽凭据的结构化任务日志 |

PNG 旁的 `.aux.xml` 保存相同地理参考；需要地理定位时请一并保留。卫星、高度、掩膜和 GeoTIFF 逐像素对齐。高度 PNG 使用元数据中的最小/最大值线性编码，实际未平滑高程以 GeoTIFF 为准；选区外以独立掩膜区分。

## 坐标、额度和网络

- 高德 GCJ-02、百度 BD-09 选区通过本地近似算法转换为 WGS84；可直接输入 WGS84 或使用 Mapbox 预览。局部输出使用 UTM 投影，网格按指定分辨率向外对齐，不改变分辨率来凑整像素。
- 1 米输出网格不表示 1 米定位精度或原始数据精度。FABDEM 原始约 30 米，重采样不会增加真实地形细节；投影尺度因子保存在元数据中。当前支持局部选区，单边最多 100 km、1 亿像素、候选瓦片最多 10 万张，不支持跨日期变更线和极区任务。
- 瓦片数量按形状相交及重采样边缘规划。缓存匹配 `mapbox.satellite / jpg90 / 256px / z`，经 JPEG 完整性检查，12 小时有效；过期或损坏缓存重新下载。
- 本机记录分开统计下载、预览、验证与重试。账户余额由手动录入的控制台用量估算，无法自动查询全账户实时余额，也不会显示未经核实的费用。高德/百度预览不消耗 Mapbox 请求；Mapbox 预览按实际请求记录，可复用有效缓存。
- 任务预算只限制该任务实际 Mapbox 请求。达到预算暂停，可提高预算后继续；取消保留完整缓存与已导出成果。预算不是账户级消费上限。
- 卫星下载先完成，地形随后处理。FABDEM 用带 ETag/Last-Modified 校验的 HTTP Range 读取压缩包，分块断点缓存；服务器/代理不支持 Range 时明确失败，可更换代理或选择 0 米模式。0 米模式完全不访问 FABDEM。
- 暂停不再发起新请求；卫星当前 HTTP 请求可能继续到结束，地形在传输/处理检查点停下。继续复用完整缓存；没有断点支持的当前请求重试。取消/关闭程序留下 `.part` 文件，不将其当完整缓存。
- 代理填写 `http://主机:端口` 或 `socks5://主机:端口`，仅本次会话使用。应用后分别测试 Qt 预览和 Python 下载网络；正在执行的任务仍用原快照。下载未设置显式代理时遵循 Requests 的环境代理设置，Qt 预览初始为直连。

## 本机配置

凭据存于 `%LOCALAPPDATA%\Mapdln\config.json`，通过当前 Windows 用户 DPAPI 加密；缓存和请求记录也在该目录。程序目录和发布包不含测试凭据。复制 EXE 给其他人不会携带当前用户配置。删除平台配置不删除下载成果。

交付版面向 Windows 10 / 11 x64。EXE 可复制到其他目录运行，不依赖工程目录或当前工作目录；地图页面和地理运行库从程序自身的临时解包目录读取。默认输出使用当前用户的系统“下载”目录，支持系统重定向；也可自行选择输出文件夹。其他用户首次运行需在账户配置页填写自己的凭据。

预览服务仅绑定 `127.0.0.1`，优先端口 8765，被占用时使用空闲端口。页面与高德转发路由包含每次启动的随机访问标识，转发验证 Host 和本机页面来源，仅允许指定官方服务。高德 `_AMapService` 保持一级路由，符合 SDK 安全模式要求；安全密钥只在 Python 转发时注入。账户页提供本次实际地址，可复制到平台限制设置；不要默认使用无限制白名单。

## CLI 与开发

CLI 与 GUI 使用同一引擎。Token 使用用户加密配置，或 `MAPBOX_TOKEN` 环境变量，**不通过明文命令行参数传递**。

```powershell
.\mapdln.exe --cli --lon 121.58901 --lat 31.2592 -w 200 -g 200 -z 17 --elevation flat --resolution 1 --output D:\地图 --name 金桥
```

旧 `--lon/--lat/-w/-g/-z/-m/-p/-n/-b/-s` 参数保留；`--mode tile` 对应卫星 + 0 米输出。`--token` 被移除，旧文件命名/输出模式改为统一任务目录。`--selection` 读取 `shape`、`system`、`points` 等选区 JSON，支持多边形/曲线。CLI 达到预算会取消并保留缓存；GUI 可交互提高预算继续。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe -m pytest -q
powershell -ExecutionPolicy Bypass -File tools\build.ps1
```

构建为单文件窗口程序，含 Qt WebEngine、Rasterio 的 GDAL、PROJ 和本机地图页面。`tools/mapdln.spec` 固定排除构建主机 PATH 中可能存在的 Poppler ICU 冲突库，让 Qt 使用 Windows 系统 ICU 接口；不要改用未过滤依赖的临时打包命令。EXE 与交付 ZIP 作为构建成果独立保存，Git 提交保存可重现源码。源码仍保持 MIT 协议，第三方组件及地图数据遵循各自许可（见 `THIRD-PARTY.md`）。

## 官方参考

[Mapbox Raster Tiles API](https://docs.mapbox.com/api/maps/raster-tiles/)、[Mapbox Token](https://docs.mapbox.com/accounts/guides/tokens/)、[账户用量限制](https://docs.mapbox.com/accounts/guides/statistics/)、[高德安全转发配置](https://lbs.amap.com/api/javascript-api-v2/guide/abc/jscode)、[百度浏览器 AK](https://lbsyun.baidu.com/docs/jsapi?title=193)、[FABDEM 数据与许可](https://research-information.bris.ac.uk/en/datasets/fabdem-v1-2/)。申请与计费规则以当前官方控制台为准。
