# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).parent
datas = [(str(root / 'mapdln' / 'web'), 'mapdln/web'),(str(root / 'mapdln' / 'assets'),'mapdln/assets')]
binaries = []
hiddenimports = []
for package in ('rasterio', 'pyproj', 'shapely'):
    package_data, package_binaries, package_imports = collect_all(
        package, include_py_files=False,
        filter_submodules=lambda name: not any(
            part in {'tests', 'testing', 'conftest'} or part.startswith('test_')
            for part in name.split('.')),
        exclude_datas=['**/tests/**', '**/testing/**', '**/test_*.py'])
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_imports

a = Analysis([str(root / 'main.py')], pathex=[str(root)], binaries=binaries,
             datas=datas, hiddenimports=hiddenimports, hookspath=[], hooksconfig={},
             runtime_hooks=[], excludes=['pytest', '_pytest', 'tkinter', 'scipy',
                 'shapely.tests', 'rasterio.tests', 'pyproj.tests'], noarchive=False, optimize=0)
# Qt 6.11 uses the Windows ICU API (unversioned function names). Library tools on
# PATH, e.g. Poppler, provide a different icuuc.dll exporting suffixed names.
# Leave the OS ICU API to System32; retain GDAL's explicitly renamed ICU DLLs.
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() not in {'icuuc.dll', 'icuin.dll', 'icudt78.dll'}]

# --- 体积优化：仅保留 Qt 运行时实际用到的组件 ---
# PySide6 的 hook 会把整套 Qt 模块（Quick3D、Charts、VirtualKeyboard、QML 插件等）
# 一并收集进来，其中绝大多数程序并不使用。这里按“链接依赖闭包 + 运行时动态加载”
# 维护白名单；新增 PySide6 功能（尤其是 QML/QtQuick 相关）时需同步更新。
_QT_KEEP_ROOT = {
    'pyside6\\pyside6.abi3.dll',
    'pyside6\\qt.conf', 'pyside6\\qtwebengineprocess.exe',
    'pyside6\\msvcp140.dll', 'pyside6\\msvcp140_1.dll', 'pyside6\\msvcp140_2.dll',
    'pyside6\\vcruntime140.dll', 'pyside6\\vcruntime140_1.dll',
    # 链接依赖闭包
    'pyside6\\qt6core.dll', 'pyside6\\qt6gui.dll', 'pyside6\\qt6network.dll',
    'pyside6\\qt6opengl.dll', 'pyside6\\qt6positioning.dll',
    'pyside6\\qt6printsupport.dll', 'pyside6\\qt6qml.dll', 'pyside6\\qt6qmlmeta.dll',
    'pyside6\\qt6qmlmodels.dll', 'pyside6\\qt6qmlworkerscript.dll',
    'pyside6\\qt6quick.dll', 'pyside6\\qt6quickwidgets.dll',
    'pyside6\\qt6webchannel.dll', 'pyside6\\qt6webenginecore.dll',
    'pyside6\\qt6webenginewidgets.dll', 'pyside6\\qt6widgets.dll',
    # 界面图标使用 SVG（chevron-*.svg）
    'pyside6\\qt6svg.dll',
    # Python 绑定（导入 PySide6.QtWebEngineWidgets 时实际加载的模块）
    'pyside6\\qtcore.pyd', 'pyside6\\qtgui.pyd', 'pyside6\\qtnetwork.pyd',
    'pyside6\\qtprintsupport.pyd', 'pyside6\\qtwebchannel.pyd',
    'pyside6\\qtwebenginecore.pyd', 'pyside6\\qtwebenginewidgets.pyd',
    'pyside6\\qtwidgets.pyd',
}

_QT_KEEP_PLUGINS = {
    'pyside6\\plugins\\platforms\\qwindows.dll',
    'pyside6\\plugins\\styles\\qmodernwindowsstyle.dll',
    'pyside6\\plugins\\imageformats\\qico.dll',
    'pyside6\\plugins\\imageformats\\qjpeg.dll',
    'pyside6\\plugins\\imageformats\\qgif.dll',
    'pyside6\\plugins\\imageformats\\qwebp.dll',
    'pyside6\\plugins\\imageformats\\qsvg.dll',
    'pyside6\\plugins\\iconengines\\qsvgicon.dll',
    'pyside6\\plugins\\tls\\qschannelbackend.dll',
    'pyside6\\plugins\\networkinformation\\qnetworklistmanager.dll',
}

_QT_KEEP_RESOURCES = {
    'pyside6\\resources\\icudtl.dat',
    'pyside6\\resources\\qtwebengine_resources.pak',
    'pyside6\\resources\\qtwebengine_resources_100p.pak',
    'pyside6\\resources\\qtwebengine_resources_200p.pak',
    'pyside6\\resources\\v8_context_snapshot.bin',
}

# WebEngine 内置错误页/右键菜单只使用系统语言；保留英文和中文即可，
# 缺失的语言会自动回退到 en-US。
_QT_KEEP_LOCALES = {
    'pyside6\\translations\\qtwebengine_locales\\en-us.pak',
    'pyside6\\translations\\qtwebengine_locales\\zh-cn.pak',
    'pyside6\\translations\\qtwebengine_locales\\zh-tw.pak',
}

# 未使用的可选运行库/图片插件
_QT_DROP_BASENAMES = {
    'opengl32sw.dll',   # 软件 OpenGL 回退，Qt Widgets/WebEngine 使用 D3D11
    '_avif.cp313-win_amd64.pyd',
    '_imagingtk.cp313-win_amd64.pyd',
}


def _keep_qt_entry(entry):
    name = entry[0].replace('/', '\\').lower()
    base = name.rsplit('\\', 1)[-1]
    if base in _QT_DROP_BASENAMES:
        return False
    if not name.startswith('pyside6\\'):
        return True
    if name.startswith('pyside6\\qml\\'):
        return False
    if name.startswith('pyside6\\resources\\'):
        return name in _QT_KEEP_RESOURCES
    if name.startswith('pyside6\\translations\\'):
        return name in _QT_KEEP_LOCALES
    if name.startswith('pyside6\\plugins\\'):
        return name in _QT_KEEP_PLUGINS
    return name in _QT_KEEP_ROOT


a.binaries = [entry for entry in a.binaries if _keep_qt_entry(entry)]
a.datas = [entry for entry in a.datas if _keep_qt_entry(entry)]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='mapdln', debug=False,
          icon=str(root / 'mapdln' / 'assets' / 'app.ico'),
          bootloader_ignore_signals=False, strip=False, upx=False, runtime_tmpdir=None,
          console=False, disable_windowed_traceback=False, argv_emulation=False,
          target_arch=None, codesign_identity=None, entitlements_file=None)
