import json
import threading
import time
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pytest
import rasterio
from PIL import Image
from pyproj import Geod
from rasterio.transform import from_bounds
from mapdln.config import save,load,home,Ledger,redact
from mapdln.geometry import make_grid,tiles_for,from_wgs,to_wgs
from mapdln.engine import (run_task,tile_path,valid_tile,estimate,Control,Cancelled,elevation_export,RangeFile)


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setenv("MAPDLN_HOME",str(tmp_path/"profile"))


def selection(kind="rectangle"):
    if kind=="rectangle":return {"shape":kind,"points":[[121.58901,31.2592]],"width_m":200,"height_m":160}
    if kind=="circle":return {"shape":kind,"points":[[121.58901,31.2592]],"radius_m":100}
    return {"shape":kind,"points":[[121.588,31.258],[121.590,31.258],[121.590,31.260],[121.589,31.2594],[121.588,31.260]]}


def seed(grid,z):
    for t in tiles_for(grid,z):
        p=tile_path(t);p.parent.mkdir(parents=True,exist_ok=True)
        im=Image.new("RGB",(256,256),(90,145,180));im.save(p,"JPEG")


@pytest.mark.parametrize("kind",["rectangle","circle","polygon","curve"])
@pytest.mark.parametrize("resolution",[.5,1,2,5])
def test_shape_grid_flat_export_no_terrain_network(tmp_path,kind,resolution):
    s=selection(kind);grid=make_grid(s,resolution);seed(grid,17)
    out=tmp_path/"export"
    task={"selection":s,"resolution":resolution,"zoom":17,"elevation":"flat","output":str(out)}
    with patch("requests.Session.get",side_effect=AssertionError("zero network expected")):
        run_task(task,"pk.testing",lambda *a,**k:None)
    with rasterio.open(out/"satellite.png") as sat,rasterio.open(out/"height.png") as h,rasterio.open(out/"mask.png") as m,rasterio.open(out/"dem.tif") as dem:
        assert sat.shape==h.shape==m.shape==dem.shape==(grid.height,grid.width)
        assert sat.transform==h.transform==m.transform==dem.transform==grid.transform
        assert sat.crs==h.crs==m.crs==dem.crs==grid.crs
        mask=m.read(1)
        assert np.array_equal(sat.read(4),mask)
        assert not h.read(1).any()
        assert (dem.read(1)[mask>0]==0).all()
        assert (dem.read(1)[mask==0]==dem.nodata).all()
        assert dem.nodata!=0
        if kind!="rectangle":assert (mask==0).any()
        assert dem.transform.a==resolution and dem.transform.e==-resolution
    meta=json.loads((out/"metadata.json").read_text(encoding="utf-8"))
    assert meta["local_mapbox_requests"]==0
    assert meta["height_encoding"]["source"].startswith("flat")
    assert "pk.testing" not in (out/"task.log").read_text(encoding="utf-8")
    with pytest.raises(ValueError,match="已有完成"):
        run_task(task,"pk.testing",lambda *a,**k:None)


@pytest.mark.parametrize("system",["GCJ02","BD09"])
def test_coordinate_roundtrip_and_meter_definition(system):
    geod=Geod(ellps="WGS84")
    for p in [(121.58901,31.2592),(116.397,39.908),(114.057,22.544)]:
        q=to_wgs(from_wgs(p,system),system)
        assert geod.inv(*p,*q)[2]<.5
    grid=make_grid(selection(),1)
    bounds=grid.polygon.bounds
    assert bounds[2]-bounds[0]==pytest.approx(200)
    assert bounds[3]-bounds[1]==pytest.approx(160)
    assert abs(grid.info()["projection_scale_at_center"]["parallel"]-1)<.001


def test_invalid_and_large_regions():
    with pytest.raises(ValueError,match="无效"):
        make_grid({"shape":"polygon","points":[[121,31],[121.01,31.01],[121,31.01],[121.01,31]]})
    with pytest.raises(ValueError):make_grid(selection(),0)
    with pytest.raises(ValueError):make_grid({"shape":"circle","points":[[0,0]],"radius_m":-1})
    with pytest.raises(ValueError):make_grid({"shape":"rectangle","points":[[0,0]],"width_m":200000,"height_m":1})
    with pytest.raises(ValueError):make_grid({"shape":"curve","points":[[121,31],[121,31],[121.001,31.001]]})


def test_cache_integrity_age_and_quota(monkeypatch):
    grid=make_grid(selection(),1);ts=tiles_for(grid,17)
    seed(grid,17)
    assert estimate(selection(),1,17)["new_requests"]==0
    p=tile_path(ts[0]);p.write_bytes(b"truncated jpg")
    assert not valid_tile(p)
    assert estimate(selection(),1,17)["new_requests"]==1
    Image.new("RGB",(256,256)).save(p,"JPEG")
    import os
    os.utime(p,(time.time()-50000,time.time()-50000))
    assert not valid_tile(p)
    assert estimate(selection(),1,17)["with_retry_reserve"]==2


def test_dpapi_and_ledger():
    settings={"mapbox_id":"unit-test","mapbox_token":"pk.testing-token","amap_secret":"unit-test-secret"}
    save(settings);assert load()==settings
    text=(home()/"config.json").read_text()
    assert "testing-token" not in text and "unit-test-secret" not in text
    ledger=Ledger();ledger.record("download");ledger.record("preview");ledger.record("verify",retry=True)
    assert ledger.counts()=={"download":1,"preview":1,"verify":1,"retry":1}
    assert "pk.testing-token" not in redact("url?access_token=pk.testing-token")


def test_budget_pause_resume_cancel():
    events=[];control=Control(lambda kind,**d:events.append((kind,d)),1)
    control.checkpoint(request=True)
    done=threading.Event()
    t=threading.Thread(target=lambda:(control.checkpoint(request=True),done.set()))
    t.start();time.sleep(.15)
    assert control.paused and not done.is_set() and control.requests==1
    control.command("resume",budget=1);time.sleep(.05);assert not done.is_set()
    control.command("resume",budget=2);t.join(1);assert done.is_set() and control.requests==2
    control.command("cancel")
    with pytest.raises(Cancelled):control.checkpoint()


def test_pause_during_startup_keeps_state_and_can_resume(tmp_path):
    s=selection();seed(make_grid(s,2),17);out=tmp_path/'early-pause'
    task={'selection':s,'resolution':2,'zoom':17,'elevation':'flat','output':str(out)}
    events=[];ready=threading.Event();errors=[]
    def emit(kind,**data):
        events.append((kind,data))
        if kind=='state' and data.get('state')=='paused' and (out/'task.log').exists():ready.set()
    control=Control(emit);control.command('pause')
    def work():
        try:run_task(task,'pk.testing',emit,control)
        except Exception as exc:errors.append(exc)
    t=threading.Thread(target=work);t.start()
    try:
        assert ready.wait(3)
        assert t.is_alive() and not (out/'metadata.json').exists()
        assert [data['state'] for kind,data in events if kind=='state'][-1]=='paused'
        control.command('resume');t.join(5)
        assert not t.is_alive() and not errors and (out/'metadata.json').exists()
    finally:
        control.command('cancel');t.join(5)


def test_real_elevation_smoothing_excludes_nodata(tmp_path):
    grid=make_grid(selection("circle"),1);src=tmp_path/"raw.tif";temp=tmp_path/"work";out=tmp_path/"out";temp.mkdir();out.mkdir()
    bounds=grid.geographic.bounds
    with rasterio.open(src,"w",driver="GTiff",width=20,height=20,count=1,dtype="float32",crs="EPSG:4326",transform=from_bounds(*bounds,20,20),nodata=-9999) as dst:dst.write(np.full((20,20),100,dtype="float32"),1)
    encoding=elevation_export(grid,[src],out,temp,{"smooth":5},Control(lambda *a,**k:None))
    assert encoding["min_m"]==pytest.approx(100,abs=.001)
    assert encoding["max_m"]==pytest.approx(100,abs=.001)
    with rasterio.open(out/"dem.tif") as d:
        a=d.read(1);assert np.max(a)==pytest.approx(100)


def test_range_archive_blocks_resume_and_validate(tmp_path):
    from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
    payload=bytes(range(256))*9000
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            start,end=map(int,self.headers['Range'][6:].split('-'));end=min(end,len(payload)-1)
            seen.append((start,end,self.headers.get('If-Range')))
            body=payload[start:end+1];self.send_response(206)
            self.send_header('Content-Range',f'bytes {start}-{end}/{len(payload)}');self.send_header('ETag','"unit-v1"');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with RangeFile(f'http://127.0.0.1:{server.server_port}/archive.zip',{'retries':0},Control(lambda *a,**k:None)) as remote:
            # Seed a validator-scoped incomplete first block, then resume that block.
            (remote.root/'0.part').write_bytes(payload[:12345])
            assert remote.read(100000)==payload[:100000]
            assert seen[-1][0]==12345 and seen[-1][2]=='"unit-v1"'
            count=len(seen);remote.seek(3000);assert remote.read(6000)==payload[3000:9000];assert len(seen)==count
            remote.seek(-40000,2);assert remote.read()==payload[-40000:]
    finally:server.shutdown();server.server_close()


def test_http_retry_classification_and_request_accounting():
    import requests
    import mercantile
    from mapdln.engine import fetch_tile
    response=requests.Response();response.status_code=404;response.url='https://api.mapbox.com/tile?access_token=pk.testing'
    control=Control(lambda *a,**k:None)
    with patch('requests.Session.get',return_value=response) as call:
        with pytest.raises(RuntimeError,match='404'):fetch_tile(mercantile.Tile(1,1,3),'pk.testing',{'retries':3},control,Ledger())
    assert call.call_count==1 and control.requests==1 and Ledger().counts()['download']==1
    response.status_code=403
    with patch('requests.Session.get',return_value=response):
        with pytest.raises(ValueError,match='403'):fetch_tile(mercantile.Tile(1,1,3),'pk.testing',{},control,Ledger())
