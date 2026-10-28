"""Provider configuration flows, isolated from live settings and tile services."""
import copy
import json
import time
from unittest.mock import MagicMock, patch

import mercantile
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QLineEdit

from mapdln.config import home, load
from mapdln.geometry import from_wgs
from mapdln.source_dialog import SourcesPanel
from mapdln.sources import get_source


def wait_for(predicate):
    end=time.monotonic()+5
    while time.monotonic()<end:
        if predicate():return
        QTest.qWait(15)
    raise AssertionError('Provider dialog operation timed out')


@pytest.fixture
def dialogs(tmp_path,monkeypatch):
    monkeypatch.setenv('MAPDLN_HOME',str(tmp_path/'profile'))
    app=QApplication.instance() or QApplication([])
    opened=[]
    def create(provider='jilin',settings=None):
        dialog=SourcesPanel(settings or {},{})
        dialog.select_source(provider)
        opened.append(dialog)
        return dialog
    yield create
    for dialog in opened:dialog.shutdown()
    app.processEvents()


@pytest.mark.parametrize('provider',['tencent','jilin'])
def test_provider_presets_keep_identity_but_allow_service_configuration(dialogs,provider):
    dialog=dialogs(provider)
    assert not dialog.name.isEnabled() and not dialog.delete_button.isEnabled()
    assert dialog.url.isEnabled() and dialog.protocol.isEnabled() and dialog.advanced.isEnabled()
    assert dialog.mk.isHidden() is (provider!='jilin')
    assert dialog.mk.echoMode()==QLineEdit.EchoMode.Password
    assert dialog.token.echoMode()==QLineEdit.EchoMode.Password
    assert dialog.note.text()
    # Programmatic edits cannot rename a built-in provider either.
    dialog.name.setText('Unrelated layer name')
    source,_=dialog.collect()
    assert source['id']==provider and source['name']==get_source({},provider)['name']
    changes=[];dialog.changed.connect(changes.append)
    dialog.delete_source()
    assert not changes and dialog.saved_id is None
    dialog.probe_complete(None,None)
    assert not dialog.delete_button.isEnabled()


def test_jilin_form_save_encrypted_roundtrip_and_no_live_mutation(dialogs):
    original={'source_credentials':{'jilin':{'token':'old-tk','credential_1':'old-mk'}}}
    untouched=copy.deepcopy(original)
    dialog=dialogs(settings=original)
    assert dialog.token_label.text()=='访问 TK' and dialog.mk.text()=='old-mk'
    dialog.token.setText('jilin-private-tk');dialog.mk.setText('jilin-private-mk')
    dialog.save_source()
    assert dialog.saved_id=='jilin',dialog.status.text()
    assert original==untouched
    assert 'jilin' in dialog.settings['provider_sources']
    assert not dialog.settings.get('custom_sources')
    raw=(home()/'config.json').read_text(encoding='utf-8')
    assert 'jilin-private-tk' not in raw and 'jilin-private-mk' not in raw
    reopened=dialogs(settings=load())
    assert reopened.token.text()=='jilin-private-tk' and reopened.mk.text()=='jilin-private-mk'


@pytest.mark.parametrize('query',[
    'mk=layer-private&tk=access-private&sch=wmts',
    'tk=access-private&mk=layer-private&sch=wmts',
])
def test_jilin_full_url_separates_mk_and_tk_regardless_of_query_order(dialogs,query):
    dialog=dialogs();dialog.token.setText('stale-tk');dialog.mk.setText('stale-mk')
    dialog.url.setPlainText('https://api.jl1mall.com/getMap/{z}/{x}/{y}?'+query)
    source,credentials=dialog.collect()
    assert credentials['token']=='access-private' and credentials['credential_1']=='layer-private'
    assert 'mk={credential_1}' in source['url_template'] and 'tk={token}' in source['url_template']
    assert 'private' not in json.dumps(source)


@pytest.mark.parametrize('marker',['{-y}','{reverseY}'])
def test_jilin_pasted_tms_url_normalizes_reversed_rows(dialogs,marker):
    dialog=dialogs()
    dialog.url.setPlainText('https://api.jl1mall.com/getMap/{z}/{x}/'+marker+'?mk=layer&tk=access')
    source,credentials=dialog.collect()
    assert source['protocol']=='tms' and '/{z}/{x}/{y}?' in source['url_template']
    assert credentials['token']=='access' and credentials['credential_1']=='layer'


def test_jilin_header_authenticated_service_does_not_require_mk_or_tk(dialogs):
    dialog=dialogs();dialog.url.setPlainText('https://example.invalid/{z}/{x}/{y}.webp')
    dialog.headers.setPlainText('{"Authorization":"Bearer header-secret"}')
    dialog.format.setCurrentText('WEBP')
    source,credentials=dialog.collect()
    assert source['format']=='WEBP' and not credentials['token'] and not credentials['credential_1']
    assert credentials['headers']['Authorization']=='Bearer header-secret'


def test_wmts_layer_selection_preserves_provider_and_uses_both_credentials(dialogs):
    dialog=dialogs();dialog.protocol.setCurrentIndex(dialog.protocol.findData('wmts'))
    dialog.url.setPlainText('https://example.invalid/wmts?mk={credential_1}&tk={token}')
    dialog.token.setText('service-tk');dialog.mk.setText('service-mk')
    source=get_source({},'jilin');source.update(id='discovered-other-id',name='年度正射影像',protocol='wmts',
        min_zoom=0,max_zoom=1,matrix_ids={'0':'matrix:0','1':'matrix:1'},
        url_template='https://example.invalid/wmts?mk={credential_1}&tk={token}&TILEMATRIX={z}&TILECOL={x}&TILEROW={y}')
    response=MagicMock();response.__enter__.return_value=response;response.status_code=200;response.iter_content.return_value=[b'<Capabilities/>']
    session=MagicMock();session.__enter__.return_value=session;session.get.return_value=response
    with patch('mapdln.source_dialog.session_for',return_value=session),patch('mapdln.source_dialog.parse_wmts',return_value=[source]):
        dialog.read_wmts();wait_for(lambda:not dialog.busy)
    saved,credentials=dialog.collect()
    assert saved['id']=='jilin' and saved['name']==get_source({},'jilin')['name']
    assert saved['matrix_ids']=={'0':'matrix:0','1':'matrix:1'}
    assert 'mk=service-mk' in session.get.call_args.args[0] and 'tk=service-tk' in session.get.call_args.args[0]
    assert credentials['token']=='service-tk' and credentials['credential_1']=='service-mk'
    assert not dialog.name.isEnabled() and not dialog.delete_button.isEnabled()
    dialog.url.setPlainText('https://example.invalid/new-wmts')
    with pytest.raises(ValueError,match='读取 WMTS'):dialog.collect()


def test_tencent_probe_converts_selection_center_before_selecting_tile(dialogs,tmp_path):
    dialog=dialogs('tencent');dialog.test_center=(116.3661875,39.9)
    assert dialog.datum.currentData()=='GCJ02'
    expected=mercantile.tile(*from_wgs(dialog.test_center,'GCJ02'),12)
    assert expected!=mercantile.tile(*dialog.test_center,12)
    with patch('mapdln.source_dialog.fetch_tile',return_value=tmp_path/'unused') as fetch:
        dialog.test_source();wait_for(lambda:not dialog.busy)
        assert fetch.call_count==1 and fetch.call_args.args[0]==expected
        assert fetch.call_args.args[2]['retries']==0
    assert '验证成功' in dialog.status.text()


def test_provider_export_omits_credentials_and_import_is_custom(dialogs,tmp_path):
    dialog=dialogs();dialog.token.setText('export-private-tk');dialog.mk.setText('export-private-mk')
    dialog.headers.setPlainText('{"Authorization":"private-header"}')
    target=tmp_path/'provider.json'
    with patch.object(QFileDialog,'getSaveFileName',return_value=(str(target),'')):dialog.export_source()
    exported=target.read_text(encoding='utf-8')
    assert 'private' not in exported and 'headers' not in json.loads(exported)
    other=dialogs('tencent')
    with patch.object(QFileDialog,'getOpenFileName',return_value=(str(target),'')):other.import_source()
    source,credentials=other.collect()
    assert source['id'].startswith('custom-') and source['id']!='jilin'
    assert other.name.isEnabled() and other.mk.isHidden()
    assert not credentials.get('token') and not credentials.get('credential_1')


def test_provider_switch_hides_mk_and_clears_old_wmts_choices(dialogs):
    dialog=dialogs();dialog.mk.setText('not-for-tencent');dialog.wmts_specs=[{'name':'stale'}];dialog.layers.addItem('stale')
    dialog.select_source('tencent')
    assert dialog.mk.isHidden() and dialog.token_label.text()=='Key / Token'
    assert not dialog.wmts_specs and dialog.layers.count()==0
    dialog.select_source('mapbox')
    assert not dialog.url.isEnabled() and not dialog.protocol.isEnabled() and not dialog.advanced.isEnabled()
    dialog.new_source()
    assert dialog.name.isEnabled() and dialog.url.isEnabled() and dialog.mk.isHidden()
