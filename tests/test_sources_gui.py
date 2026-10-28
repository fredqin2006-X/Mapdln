"""Offline UI/source integration, including a real isolated DEM worker."""
import json
import time
from unittest.mock import patch

import pytest
import numpy as np
import rasterio
from rasterio.transform import from_bounds
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from mapdln.gui import Window
from mapdln.source_dialog import SourcesDialog
from mapdln.sources import get_source


def wait_for(predicate,seconds=10):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if predicate():return
        QTest.qWait(25)
    raise AssertionError('UI operation timed out')


@pytest.fixture
def window(tmp_path,monkeypatch):
    monkeypatch.setenv('MAPDLN_HOME',str(tmp_path/'profile'))
    app=QApplication.instance() or QApplication([])
    w=Window();w.preview.setCurrentIndex(w.preview.findData('offline'));w.show()
    yield w
    w.close();app.processEvents()


def test_source_switch_preserves_region_and_independent_preview(window):
    w=window;w.apply_coordinates();original=json.loads(json.dumps(w.selection))
    w.imagery_source.setCurrentIndex(w.imagery_source.findData('tianditu'))
    assert w.selection==original and w.preview_provider()=='offline'
    with patch.object(w,'reload_map'):
        w.preview.setCurrentIndex(w.preview.findData('follow'))
        assert w.preview_provider()=='source:tianditu'
    # Do not start an online preview while testing: only inspect the selector mapping.
    w.preview.setCurrentIndex(w.preview.findData('offline'))
    w.export_content.setCurrentIndex(w.export_content.findData(False))
    assert not w.imagery_source.isEnabled()
    assert w.selection==original


def test_source_dialog_extracts_secrets_without_changing_live_settings(window):
    w=window;original=json.loads(json.dumps(w.settings))
    d=SourcesDialog(w.settings,w.options(),w);d.new_source();d.name.setText('离线测试配置')
    d.url.setPlainText('https://example.invalid/{z}/{x}/{y}.png?key=secret-value')
    d.headers.setPlainText('{"Authorization":"Bearer hidden-value"}')
    spec,credentials=d.collect()
    assert spec['url_template'].endswith('key={token}')
    assert credentials['token']=='secret-value'
    assert credentials['headers']['Authorization']=='Bearer hidden-value'
    assert 'secret-value' not in json.dumps(spec) and 'hidden-value' not in json.dumps(spec)
    assert w.settings==original
    d.reject()


def test_source_dialog_retains_basic_auth_and_tests_selection_center(window,tmp_path):
    w=window;w.lon.setValue(12.5);w.lat.setValue(48.2);w.apply_coordinates()
    d=SourcesDialog(w.settings,w.options(),w);d.new_source();d.name.setText('本地测试')
    d.url.setPlainText('https://user:pass@example.invalid/{z}/{x}/{y}.png')
    spec,credentials=d.collect()
    assert credentials['headers']['Authorization']=='Basic dXNlcjpwYXNz'
    assert 'user:pass' not in spec['url_template']
    with patch('mapdln.source_dialog.fetch_tile',return_value=tmp_path/'unused') as fetch:
        d.test_source();wait_for(lambda:not d.busy)
        import mercantile
        assert fetch.call_args.args[0]==mercantile.tile(w.grid.geographic.centroid.x,w.grid.geographic.centroid.y,12)
    d.reject()


def test_wmts_dialog_reads_layers_and_invalidates_edited_service(window):
    from unittest.mock import MagicMock
    from mapdln.sources import default_source
    spec=default_source();spec.update(id='wmts-test',name='正射影像',protocol='wmts',min_zoom=0,max_zoom=1,matrix_ids={'0':'grid:0','1':'grid:1'},url_template='https://example.invalid/wmts?TILEMATRIX={z}&TILECOL={x}&TILEROW={y}&tk={token}')
    response=MagicMock();response.__enter__.return_value=response;response.status_code=200;response.iter_content.return_value=[b'<Capabilities/>']
    session=MagicMock();session.__enter__.return_value=session;session.get.return_value=response
    d=SourcesDialog(window.settings,window.options(),window);d.new_source();d.protocol.setCurrentIndex(d.protocol.findData('wmts'));d.url.setPlainText('https://example.invalid/wmts?tk=private-key')
    with patch('mapdln.source_dialog.session_for',return_value=session),patch('mapdln.source_dialog.parse_wmts',return_value=[spec]):
        d.read_wmts();wait_for(lambda:not d.busy)
        saved,credential=d.collect()
        assert saved['matrix_ids']=={'0':'grid:0','1':'grid:1'} and credential['token']=='private-key'
        assert session.get.call_count==1 and session.get.call_args.kwargs['allow_redirects'] is False
        assert saved['id']==d.collect()[0]['id']
    d.url.setPlainText('https://different.invalid/wmts')
    with pytest.raises(ValueError,match='读取 WMTS'):d.collect()
    d.reject()


def test_source_dialog_saves_encrypted_source_and_selectable_configuration(window,tmp_path):
    d=SourcesDialog(window.settings,window.options(),window);d.new_source();d.name.setText('测试源')
    d.url.setPlainText('https://example.invalid/{z}/{x}/{y}.png?tk={token}');d.token.setText('test-encrypted-token')
    d.save_source()
    assert d.result()==d.DialogCode.Accepted
    assert get_source(d.settings,d.saved_id)['name']=='测试源'
    from mapdln.config import home
    config=(home()/'config.json').read_text(encoding='utf-8')
    assert 'test-encrypted-token' not in config


def test_dem_only_gui_runs_worker_without_online_credentials(window,tmp_path):
    w=window;dem=tmp_path/'测试高程.tif'
    with rasterio.open(dem,'w',driver='GTiff',width=128,height=128,count=1,dtype='float32',crs='EPSG:4326',transform=from_bounds(121.58,31.25,121.60,31.27,128,128)) as dst:
        dst.write(np.full((128,128),12.5,dtype='float32'),1)
    w.export_content.setCurrentIndex(w.export_content.findData(False))
    w.dem_unit.setCurrentIndex(w.dem_unit.findData('m'));w.set_dem_file(str(dem));wait_for(lambda:w.dem_info is not None)
    w.output.setText(str(tmp_path));w.name.setText('仅高程成果');w.apply_coordinates();wait_for(lambda:w.plan is not None)
    assert w.plan['tiles']==0 and w.plan['new_requests']==0
    w.start_task();wait_for(lambda:w.process is not None);wait_for(lambda:not w.running(),30)
    assert w.task_state=='done',w.logs.toPlainText()
    assert w.active_credentials=={}
    assert w.preview_provider()=='offline' and not w.server.preview_enabled
    assert (tmp_path/'仅高程成果'/'dem.tif').exists()
    assert (tmp_path/'仅高程成果'/'mask.png').exists()
    assert not (tmp_path/'仅高程成果'/'satellite.png').exists()


def test_dem_missing_units_does_not_enable_start(window,tmp_path):
    dem=tmp_path/'missing-unit.tif'
    with rasterio.open(dem,'w',driver='GTiff',width=8,height=8,count=1,dtype='float32',crs='EPSG:4326',transform=from_bounds(121.58,31.25,121.60,31.27,8,8)) as dst:dst.write(np.ones((8,8),dtype='float32'),1)
    window.set_dem_file(str(dem));wait_for(lambda:window.dem_info is not None)
    assert window.dem_info['unit_required']
    window.apply_coordinates();QTest.qWait(500)
    assert not window.start.isEnabled()
    assert '单位' in window.estimate_label.text()


def test_preview_proxy_uses_registered_source_and_never_exposes_its_secrets(tmp_path):
    import requests
    from PIL import Image
    from mapdln.server import PreviewServer
    from mapdln.gui import resource
    from mapdln.sources import default_source
    source=default_source();source.update(id='local-test',name='测试',url_template='https://example.invalid/{z}/{x}/{y}.png?tk={token}',format='PNG')
    settings={'custom_sources':[source],'source_credentials':{'local-test':{'token':'hidden-token','headers':{'Authorization':'private-header'}}}}
    image=tmp_path/'tile.png';Image.new('RGBA',(256,256),(10,30,50,255)).save(image)
    server=PreviewServer(resource('web'),lambda:settings)
    try:
        headers={'Referer':server.url+'map.html'}
        config=requests.get(server.url+'config',headers=headers,timeout=3)
        assert config.status_code==200
        assert 'hidden-token' not in config.text and 'private-header' not in config.text
        with patch('mapdln.server.fetch_tile',return_value=image) as fetch:
            result=requests.get(server.url+'source-tiles/local-test/1/0/1',headers=headers,timeout=3)
            assert result.status_code==200 and result.headers['Content-Type']=='image/png'
            assert fetch.call_args.kwargs['source']['id']=='local-test'
            assert fetch.call_args.args[1]['token']=='hidden-token'
            missing=requests.get(server.url+'source-tiles/not-registered/1/0/1',headers=headers,timeout=3)
            assert missing.status_code>=400 and fetch.call_count==1
            denied=requests.get(server.url+'source-tiles/local-test/1/0/1',timeout=3)
            assert denied.status_code==403 and fetch.call_count==1
        context=server.preview_context(source,settings['source_credentials']['local-test'])
        assert context is server.preview_context(source,settings['source_credentials']['local-test'])
        other=server.preview_context(source,{'token':'other-account'})
        assert other is not context
        server.set_preview_enabled(False)
        assert context[0].cancelled and other[0].cancelled
        paused=requests.get(server.url+'source-tiles/local-test/1/0/1',headers=headers,timeout=3)
        assert paused.status_code==409
    finally:server.close()
