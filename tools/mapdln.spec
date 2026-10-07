# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).parent
datas = [(str(root / 'mapdln' / 'web'), 'mapdln/web'),(str(root / 'mapdln' / 'assets'),'mapdln/assets')]
binaries = []
hiddenimports = []
for package in ('rasterio', 'pyproj', 'shapely'):
    package_data, package_binaries, package_imports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_imports

a = Analysis([str(root / 'main.py')], pathex=[str(root)], binaries=binaries,
             datas=datas, hiddenimports=hiddenimports, hookspath=[], hooksconfig={},
             runtime_hooks=[], excludes=['pytest', 'tkinter'], noarchive=False, optimize=0)
# Qt 6.11 uses the Windows ICU API (unversioned function names). Library tools on
# PATH, e.g. Poppler, provide a different icuuc.dll exporting suffixed names.
# Leave the OS ICU API to System32; retain GDAL's explicitly renamed ICU DLLs.
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() not in {'icuuc.dll', 'icuin.dll', 'icudt78.dll'}]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='mapdln', debug=False,
          icon=str(root / 'mapdln' / 'assets' / 'app.ico'),
          bootloader_ignore_signals=False, strip=False, upx=False, runtime_tmpdir=None,
          console=False, disable_windowed_traceback=False, argv_emulation=False,
          target_arch=None, codesign_identity=None, entitlements_file=None)
