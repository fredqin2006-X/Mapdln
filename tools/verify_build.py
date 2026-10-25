"""Inspect the frozen archive and exercise its worker outside the checkout."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def inspect_archive(exe, max_mib):
    from mapdln import __version__

    size = exe.stat().st_size
    if size > max_mib * 1024 ** 2:
        raise RuntimeError(f'EXE exceeds {max_mib:g} MiB: {size:,} bytes')
    archive = CArchiveReader(str(exe))
    entries = {name.replace('\\', '/').lower(): value for name, value in archive.toc.items()}
    required = {
        'mapdln/web/map.html', 'mapdln/web/map.js', 'mapdln/assets/app.ico',
        'mapdln/assets/app.png', 'mapdln/assets/chevron-light.svg',
        'pyside6/qt6webenginecore.dll', 'pyside6/qtwebengineprocess.exe',
        'pyside6/plugins/platforms/qwindows.dll',
        'pyside6/plugins/tls/qschannelbackend.dll',
        'pyside6/plugins/imageformats/qsvg.dll', 'pyside6/qt6svg.dll',
        'pyside6/resources/icudtl.dat', 'pyside6/resources/qtwebengine_resources.pak',
        'pyside6/resources/qtwebengine_resources_100p.pak',
        'pyside6/resources/qtwebengine_resources_200p.pak',
        'pyside6/resources/v8_context_snapshot.bin',
        'pyside6/translations/qtwebengine_locales/en-us.pak',
        'pyside6/translations/qtwebengine_locales/zh-cn.pak',
    }
    missing = required - entries.keys()
    if missing:
        raise RuntimeError(f'Missing runtime files: {sorted(missing)}')
    forbidden = [name for name in entries if (
        'devtools_resources' in name or name.startswith('pyside6/qml/')
        or name.endswith('/opengl32sw.dll')
        or any(name.startswith(f'{pkg}/tests/') for pkg in ('shapely', 'rasterio', 'pyproj'))
    )]
    pyz = archive.open_embedded_archive('PYZ.pyz')
    forbidden += [name for name in pyz.toc if any(
        name == prefix or name.startswith(prefix + '.')
        for prefix in ('scipy', 'pytest', '_pytest', 'shapely.tests', 'rasterio.tests', 'pyproj.tests'))]
    if forbidden:
        raise RuntimeError(f'Unwanted bundled files/modules: {forbidden[:20]}')
    for name in ('mapdln', 'mapdln.gui', 'mapdln.engine', 'mapdln.worker', 'mapdln.server'):
        if name not in pyz.toc:
            raise RuntimeError(f'Missing application module: {name}')
    if __version__ not in pyz.extract('mapdln').co_consts:
        raise RuntimeError(f'Frozen version does not match source {__version__}')
    largest = sorted(((value[1], name) for name, value in archive.toc.items()), reverse=True)[:10]
    with exe.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {
        'version': __version__, 'bytes': size, 'mib': round(size / 1024 ** 2, 2),
        'sha256': digest, 'archive_entries': len(entries), 'python_modules': len(pyz.toc),
        'largest_compressed_entries': [{'name': name, 'bytes': size} for size, name in largest],
    }


def smoke_worker(exe):
    import numpy as np
    import rasterio
    from PIL import Image
    from mapdln.engine import tile_path
    from mapdln.geometry import make_grid, tiles_for

    # Synthetic cache, fresh Unicode path and no source DLL directories on PATH.
    with tempfile.TemporaryDirectory(prefix='Mapdln 构建 smoke ') as folder:
        folder = Path(folder)
        profile = folder / '独立配置'
        previous = os.environ.get('MAPDLN_HOME')
        try:
            os.environ['MAPDLN_HOME'] = str(profile)
            selection = {'shape': 'circle', 'points': [[121.58901, 31.2592]], 'radius_m': 50}
            grid = make_grid(selection, 2)
            for tile in tiles_for(grid, 17):
                path = tile_path(tile)
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new('RGB', (256, 256), (90, 145, 180)).save(path, 'JPEG')
        finally:
            if previous is None:
                os.environ.pop('MAPDLN_HOME', None)
            else:
                os.environ['MAPDLN_HOME'] = previous
        env = {key: value for key, value in os.environ.items() if not (
            key.upper().startswith(('PYTHON', 'QT', 'QML', 'GDAL', 'PROJ', 'CONDA'))
            or key.upper() == 'MAPBOX_TOKEN')}
        windows = Path(os.environ.get('SystemRoot', 'C:/Windows'))
        env.update(MAPDLN_HOME=str(profile), PATH=os.pathsep.join(map(str, (windows / 'System32', windows))),
                   HTTP_PROXY='http://127.0.0.1:9', HTTPS_PROXY='http://127.0.0.1:9',
                   http_proxy='http://127.0.0.1:9', https_proxy='http://127.0.0.1:9')
        output = folder / '成果 export'
        task = {'selection': selection, 'resolution': 2, 'zoom': 17,
                'elevation': 'flat', 'output': str(output)}
        result = subprocess.run(
            [str(exe), '--worker'], input=json.dumps({'task': task, 'token': 'pk.build-smoke'}) + '\n',
            cwd=folder, env=env, capture_output=True, text=True, encoding='utf-8', timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if result.returncode:
            raise RuntimeError(f'Frozen worker failed ({result.returncode}): {result.stderr}\n{result.stdout}')
        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        if not any(event['type'] == 'done' for event in events):
            raise RuntimeError('Frozen worker did not report completion')
        with rasterio.open(output / 'satellite.png') as sat, rasterio.open(output / 'mask.png') as mask, \
                rasterio.open(output / 'dem.tif') as dem, rasterio.open(output / 'height.png') as height:
            for dataset in (sat, mask, dem, height):
                assert dataset.shape == (grid.height, grid.width)
                assert dataset.crs == grid.crs
                assert dataset.transform == grid.transform
            values = mask.read(1)
            assert (values == 0).any() and (values == 255).any()
            assert np.array_equal(sat.read(4), values)
            assert not height.read(1).any()
            assert (dem.read(1)[values > 0] == 0).all()
            assert (dem.read(1)[values == 0] == dem.nodata).all()
        metadata = json.loads((output / 'metadata.json').read_text(encoding='utf-8'))
        assert metadata['local_mapbox_requests'] == 0
        assert (output / 'boundary.geojson').is_file()
        return {'passed': True, 'shape': 'circle', 'pixels': [grid.width, grid.height],
                'resolution_m': 2, 'mapbox_requests': 0, 'isolated_profile': True,
                'system_only_path': True, 'unicode_working_directory': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exe', nargs='?', default='dist/mapdln.exe')
    parser.add_argument('--max-mib', type=float, default=220)
    parser.add_argument('--report', default='test-output/build-verification.json')
    args = parser.parse_args()
    exe = Path(args.exe).resolve(strict=True)
    report = inspect_archive(exe, args.max_mib)
    print(f"Archive OK: v{report['version']}, {report['bytes']:,} bytes ({report['mib']:.2f} MiB)", flush=True)
    report['worker_smoke'] = smoke_worker(exe)
    target = Path(args.report)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Frozen worker OK: cached circle export, matching CRS/masks, no network requests', flush=True)
    print(f"SHA256: {report['sha256']}", flush=True)


if __name__ == '__main__':
    main()
