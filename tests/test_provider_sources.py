"""Provider wire protocols and credentials against a real local tile server."""
import copy
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import mercantile
import pytest
from PIL import Image

from mapdln.config import Ledger, home, load, redact, save
from mapdln.engine import Control, fetch_tile, tile_path
from mapdln.sources import (build_request, credentials_for, get_source, list_sources,
                            split_credentials)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('MAPDLN_HOME', str(tmp_path/'profile'))


@pytest.mark.parametrize('z,x,y', [(0,0,0),(5,16,16),(17,109764,53557),(18,262143,262143)])
def test_tencent_uses_inverted_row_before_directory_grouping(z,x,y):
    tile=mercantile.Tile(x,y,z)
    source=get_source({},'tencent')
    url,headers=build_request(source,tile,{})
    reverse=2**z-1-y
    assert urlsplit(url).path==f'/sateTiles/{z}/{x//16}/{reverse//16}/{x}_{reverse}.jpg'
    assert tile==mercantile.Tile(x,y,z) and not headers
    assert source['datum']=='GCJ02' and source['protocol']=='tms'


def test_jilin_uses_top_left_rows_and_escapes_both_credentials():
    source=get_source({},'jilin')
    url,_=build_request(source,mercantile.Tile(32,40,6),{'token':'tk&= +测试','credential_1':'mk&= +图层'})
    parsed=urlsplit(url)
    assert parsed.path=='/getMap/6/32/40'
    assert parse_qs(parsed.query)=={'tk':['tk&= +测试'],'mk':['mk&= +图层'],'sch':['wmts']}
    for credentials in ({},{'token':'tk'},{'credential_1':'mk'}):
        with pytest.raises(ValueError,match='图层 MK 与访问 TK'):
            build_request(source,mercantile.Tile(32,40,6),credentials)


@pytest.mark.parametrize('query', ['mk=private-layer&tk=private-access','tk=private-access&mk=private-layer'])
def test_jilin_url_credentials_have_stable_roles(query):
    template,credentials=split_credentials('https://api.jl1mall.com/getMap/{z}/{x}/{y}?'+query+'&pro=project-7')
    assert credentials=={'token':'private-access','credential_1':'private-layer'}
    assert parse_qs(urlsplit(template).query)=={'tk':['{token}'],'mk':['{credential_1}'],'pro':['project-7']}
    assert 'private' not in template
    assert 'private-layer' not in redact('mk=private-layer&tk=private-access')


def test_partial_placeholder_does_not_steal_existing_credential():
    template,credentials=split_credentials('https://example.test/{z}/{x}/{y}?tk={token}&mk=private-layer')
    assert credentials=={'credential_1':'private-layer'}
    assert 'tk={token}' in template and 'mk={credential_1}' in template


def test_provider_override_is_encrypted_and_cannot_shadow_builtin_identity():
    source=get_source({},'jilin')
    source['url_template']='https://example.test/{z}/{x}/{y}?mk=private-layer&tk=private-access'
    source['name']='Changed label'
    settings={'provider_sources':{'jilin':source}}
    original=copy.deepcopy(settings)
    save(settings)
    assert settings==original
    disk=(home()/'config.json').read_text(encoding='utf-8')
    assert 'private-layer' not in disk and 'private-access' not in disk
    restored=load()
    assert credentials_for(restored,'jilin')=={'token':'private-access','credential_1':'private-layer'}
    assert get_source(restored,'jilin')['name']==get_source({},'jilin')['name']
    with pytest.raises(ValueError):
        list_sources({'provider_sources':{'mapbox':source}})


def test_jilin_webp_download_and_preview_mime_use_same_isolated_cache():
    from mapdln.server import PreviewServer
    from mapdln.gui import resource
    import requests
    buffer=io.BytesIO()
    Image.new('RGBA',(256,256),(30,110,180,96)).save(buffer,'WEBP',lossless=True)
    content=buffer.getvalue()
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            seen.append(self.path)
            self.send_response(200)
            self.send_header('Content-Type','image/webp')
            self.send_header('Content-Length',str(len(content)))
            self.end_headers()
            self.wfile.write(content)
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=http.serve_forever,daemon=True)
    thread.start()
    source=get_source({},'jilin')
    source['url_template']=f'http://127.0.0.1:{http.server_port}/getMap/{{z}}/{{x}}/{{y}}?tk={{token}}&mk={{credential_1}}&sch=wmts'
    credentials={'token':'local-tk','credential_1':'local-mk'}
    settings={'provider_sources':{'jilin':source},'source_credentials':{'jilin':credentials}}
    preview=PreviewServer(resource('web'),lambda:settings)
    try:
        tile=mercantile.Tile(32,40,6)
        path=fetch_tile(tile,credentials,{'retries':0},Control(lambda *a,**k:None),Ledger(),source=source)
        assert path.exists() and len(seen)==1
        assert parse_qs(urlsplit(seen[0]).query)=={'tk':['local-tk'],'mk':['local-mk'],'sch':['wmts']}
        assert tile_path(tile,source,{'token':'other','credential_1':'local-mk'})!=path
        response=requests.get(preview.url+'source-tiles/jilin/6/32/40',headers={'Referer':preview.url+'map.html'},timeout=5)
        assert response.status_code==200 and response.headers['Content-Type']=='image/webp'
        assert response.content==content and len(seen)==1
        assert Ledger().counts(source='jilin')['download']==1
        assert 'local-tk' not in json.dumps(list_sources(settings))
    finally:
        preview.close()
        http.shutdown()
        http.server_close()
        thread.join(timeout=3)
