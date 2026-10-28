"""GCJ raster placement against independent scalar coordinates and pixel fields."""
import json
import math
from types import SimpleNamespace
from unittest.mock import patch

import mercantile
import numpy as np
from PIL import Image, features
from pyproj import Transformer
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.windows import Window

from mapdln.engine import (Cancelled, Control, _gcj_window, estimate, run_task,
                           tile_path, valid_tile)
from mapdln.geometry import make_grid, tiles_for, wgs_to_gcj_array
from mapdln.sources import validate_source


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MAPDLN_HOME", str(tmp_path / "profile"))


def reference_gcj(lng, lat):
    """Scalar reference kept independent from the vectorized export path."""
    if not 72.004 < lng < 137.8347 or not .8293 < lat < 55.8271:
        return lng, lat
    x, y = lng-105, lat-35
    wave = sum(20*math.sin(k*x*math.pi) for k in (6,2))*2/3
    dy = -100+2*x+3*y+.2*y*y+.1*x*y+.2*math.sqrt(abs(x))+wave
    dy += (20*math.sin(y*math.pi)+40*math.sin(y/3*math.pi)+160*math.sin(y/12*math.pi)+320*math.sin(y/30*math.pi))*2/3
    dx = 300+x+2*y+.1*x*x+.1*x*y+.1*math.sqrt(abs(x))+wave
    dx += (20*math.sin(x*math.pi)+40*math.sin(x/3*math.pi)+150*math.sin(x/12*math.pi)+300*math.sin(x/30*math.pi))*2/3
    sine = math.sin(math.radians(lat))
    root = math.sqrt(1-.00669342162296594323*sine*sine)
    return (lng+dx*180*root/(6378245*math.cos(math.radians(lat))*math.pi),
            lat+dy*180*root**3/(6378245*(1-.00669342162296594323)*math.pi))


def global_pixel(lng, lat, z, size):
    extent = size*2**z
    return ((lng+180)/360*extent,
            (1-math.log(math.tan(math.pi/4+math.radians(lat)/2))/math.pi)/2*extent)


def source(size=256, fmt="PNG"):
    return validate_source({"id":"gcj-test", "name":"GCJ test imagery", "protocol":"xyz",
        "url_template":"https://example.invalid/{z}/{x}/{y}", "tile_size":size, "format":fmt,
        "min_zoom":0, "max_zoom":24, "crs":"EPSG:3857", "datum":"GCJ02"})


def region(**changes):
    return {"shape":"rectangle", "points":[[121.58901,31.2592]],
            "width_m":120, "height_m":100, "angle_deg":19, **changes}


def test_vectorized_gcj_control_points_and_external_identity():
    points = np.array([[116.39747,39.908823], [121.58901,31.2592],
                       [-73.9857,40.7484], [0,90], [72.004,30], [120,.8293]])
    actual = np.column_stack(wgs_to_gcj_array(points[:,0],points[:,1]))
    assert actual[:2] == pytest.approx(np.array([[116.4037135824225,39.91022649807321],
                                      [121.59321744609329,31.257012021597077]]), abs=1e-10)
    assert np.array_equal(actual[2:],points[2:])


@pytest.mark.parametrize("center,width,res,z", [
    ([121.58901,31.2592],120,2,19), ([121.58901,31.2592],1.5,.01,24),
    ([72.004,30],180,2,18), ([120,55.8271],180,2,18), ([-73.9857,40.7484],120,2,19),
])
def test_source_tile_plan_covers_inverse_samples_and_bilinear_neighbors(center,width,res,z):
    grid = make_grid(region(points=[center],width_m=width,height_m=width),res)
    planned = set(tiles_for(grid,z,"GCJ02"))
    inv = Transformer.from_crs(grid.crs,4326,always_xy=True)
    for row in np.linspace(.5,grid.height-.5,25):
        for col in np.linspace(.5,grid.width-.5,25):
            lng, lat = inv.transform(*(grid.transform @ (col,row)))
            px, py = global_pixel(*reference_gcj(lng,lat),z,256)
            for x in (math.floor(px-.5),math.floor(px-.5)+1):
                for y in (math.floor(py-.5),math.floor(py-.5)+1):
                    assert mercantile.Tile(x//256,y//256,z) in planned
    if center[0]==121.58901 and z==24:
        assert planned.isdisjoint(set(tiles_for(grid,z)))


@pytest.mark.parametrize("size",[256,512])
def test_gcj_gradient_export_matches_independent_coordinates_and_dem(tmp_path,size):
    selection = region()
    grid = make_grid(selection,2)
    spec = source(size)
    z = 18
    ax, ay = global_pixel(*reference_gcj(*selection["points"][0]),z,size)
    scale = size/64
    planned = tiles_for(grid,z,"GCJ02")
    for tile in planned:
        yy, xx = np.mgrid[0:size,0:size]
        rgba = np.zeros((size,size,4),dtype="uint8")
        rgba[:,:,0] = np.rint(np.clip(128+(tile.x*size+xx+.5-ax)/scale,0,255))
        rgba[:,:,1] = np.rint(np.clip(128+(tile.y*size+yy+.5-ay)/scale,0,255))
        rgba[:,:,2], rgba[:,:,3] = 180, 128
        path = tile_path(tile,spec,{})
        path.parent.mkdir(parents=True,exist_ok=True)
        Image.fromarray(rgba).save(path,"PNG")
    assert estimate(selection,2,z,source=spec)["tiles"] == len(planned)
    out = tmp_path/"gradient"
    with patch("requests.Session.get",side_effect=AssertionError("source tiles must be cached")):
        run_task({"selection":selection,"resolution":2,"zoom":z,"source":spec,
                  "elevation":"flat","output":str(out)}, {}, lambda *a,**k:None)
    inv = Transformer.from_crs(grid.crs,4326,always_xy=True)
    with rasterio.open(out/"satellite.png") as image, rasterio.open(out/"mask.png") as mask, rasterio.open(out/"dem.tif") as dem:
        assert image.transform == dem.transform == mask.transform == grid.transform
        assert image.crs == dem.crs == grid.crs
        rgba, selected = image.read(), mask.read(1)>0
        assert (rgba[3,selected]==128).all()
        assert not rgba[:,~selected].any()
        samples = 0
        for row in range(1,grid.height-1,5):
            for col in range(1,grid.width-1,5):
                if not selected[row,col]:
                    continue
                lng, lat = inv.transform(*(grid.transform @ (col+.5,row+.5)))
                px, py = global_pixel(*reference_gcj(lng,lat),z,size)
                expected = [128+(px-ax)/scale,128+(py-ay)/scale,180]
                assert rgba[:3,row,col] == pytest.approx(expected,abs=1)
                samples += 1
        assert samples > 50
    metadata = json.loads((out/"metadata.json").read_text(encoding="utf-8"))
    assert "local approximate conversion" in metadata["imagery_georeferencing"]
    assert metadata["imagery_source"]["datum"] == "GCJ02"


def test_gcj_bilinear_alpha_does_not_bleed_hidden_color(tmp_path):
    lng,lat = 121.58901,31.2592
    east,north = Transformer.from_crs(4326,32651,always_xy=True).transform(lng,lat)
    sx,sy = Transformer.from_crs(4326,3857,always_xy=True).transform(*reference_gcj(lng,lat))
    grid = SimpleNamespace(crs="EPSG:32651",transform=from_origin(east-.5,north+.5,1,1))
    values = np.zeros((4,2,2),dtype="uint8")
    values[0,:,0],values[3,:,0],values[2,:,1] = 255,255,255
    path = tmp_path/"transparent.tif"
    with rasterio.open(path,"w",driver="GTiff",width=2,height=2,count=4,dtype="uint8",transform=from_origin(sx-1,sy+1,1,1)) as dst:
        dst.write(values)
    with rasterio.open(path) as src:
        result = _gcj_window(src,grid,Window(0,0,1,1),Transformer.from_crs(grid.crs,4326,always_xy=True),Control(lambda *a,**k:None),{})
    assert result[:,0,0] == pytest.approx([255,0,0,128],abs=1)


def test_downsampling_reads_bounded_source_blocks_and_can_cancel():
    class VirtualRaster:
        width=height=10**9
        transform=from_origin(-20037508.342789244,20037508.342789244,.05,.05)
        reads=[]
        def read(self,*,window):
            assert window.width<=256 and window.height<=256
            self.reads.append(window)
            return np.full((4,int(window.height),int(window.width)),255,dtype="uint8")
    grid=SimpleNamespace(crs="EPSG:32651",transform=from_origin(360000,3460000,5000,5000))
    src=VirtualRaster()
    control=Control(lambda *a,**k:None)
    inverse=Transformer.from_crs(grid.crs,4326,always_xy=True)
    result=_gcj_window(src,grid,Window(0,0,2,2),inverse,control,{})
    assert result.shape==(4,2,2) and (result==255).all() and src.reads
    control.command("cancel")
    with pytest.raises(Cancelled):
        _gcj_window(src,grid,Window(0,0,2,2),inverse,control,{})


def test_webp_tiles_supported_by_runtime_and_auto_validation(tmp_path):
    assert features.check("webp")
    path=tmp_path/"tile.webp"
    Image.new("RGB",(256,256),(10,50,100)).save(path,"WEBP",lossless=True)
    assert valid_tile(path,source=source(fmt="auto"))
    assert valid_tile(path,source=source(fmt="WEBP"))
