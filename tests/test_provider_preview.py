"""Actual Qt/Leaflet source switching against isolated local tile responses."""
import json
import time
from unittest.mock import patch

from PIL import Image
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from mapdln.gui import Window
from mapdln.geometry import wgs_to_gcj


def wait_for(predicate):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if predicate():
            return
        QTest.qWait(25)
    raise AssertionError('Map preview timed out')


def js(window, expression):
    result = []
    window.web.page().runJavaScript('JSON.stringify((' + expression + '))', result.append)
    wait_for(lambda: result)
    return json.loads(result[0])


def test_tencent_and_jilin_preview_keep_same_wgs_region(tmp_path, monkeypatch):
    monkeypatch.setenv('MAPDLN_HOME', str(tmp_path/'profile'))
    from mapdln.config import save
    save({'preview_provider': 'offline'})
    app = QApplication.instance() or QApplication([])
    tile = tmp_path/'tile.png'
    Image.new('RGBA', (256,256), (31,124,102,255)).save(tile)
    with patch('mapdln.server.fetch_tile', return_value=tile) as fetch:
        w = Window()
        try:
            w.show()
            wait_for(lambda: '离线坐标预览' in w.map_status.text())
            w.apply_coordinates()
            region = json.loads(json.dumps(w.selection))
            center = w.grid.geographic.centroid
            w.imagery_source.setCurrentIndex(w.imagery_source.findData('tencent'))
            w.preview.setCurrentIndex(w.preview.findData('follow'))
            wait_for(lambda: w.display_system == 'GCJ02' and '腾讯' in w.map_status.text())
            QTest.qWait(600)  # Leaflet finishes its animated pan after providerReady.
            actual = js(w, 'mapDebugInfo().center')
            expected = wgs_to_gcj(center.x, center.y)
            # Leaflet rounds the final viewport centre to a screen pixel at z15.
            assert abs(actual['lng']-expected[0]) < .00005
            assert abs(actual['lat']-expected[1]) < .00005
            assert w.selection == region
            w.imagery_source.setCurrentIndex(w.imagery_source.findData('jilin'))
            wait_for(lambda: w.display_system == 'WGS84' and '吉林' in w.map_status.text())
            QTest.qWait(600)
            actual = js(w, 'mapDebugInfo().center')
            assert abs(actual['lng']-center.x) < .00005
            assert abs(actual['lat']-center.y) < .00005
            assert w.selection == region
            assert {'tencent','jilin'} <= {c.kwargs['source']['id'] for c in fetch.call_args_list}
        finally:
            w.close()
            app.processEvents()
