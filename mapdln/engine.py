"""Bounded downloads, validated cache and block-wise geographic exports."""
import concurrent.futures
from contextlib import nullcontext
import hashlib
import io
import json
import math
import os
from pathlib import Path
import threading
import time
import zipfile
import numpy as np
from PIL import Image
import requests
import rasterio
from rasterio.enums import Resampling, ColorInterp
from rasterio.features import rasterize
from rasterio.shutil import copy as rio_copy
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window, transform as window_transform
import mercantile
from pyproj import Transformer
from shapely.geometry import mapping
from .config import home, Ledger, atomic_json, redact
from .geometry import make_grid, tiles_for, boundary_json, wgs_to_gcj_array
from . import sources as source_registry
from . import __version__

FABDEM_BASE = "https://data.bris.ac.uk/datasets/s5hqmjcdj8yo2ibzi9b4ew3sn/"
CACHE_SECONDS = 12*3600


class Cancelled(Exception):
    pass


class Control:
    def __init__(self, emit, budget=0):
        self.emit = emit
        self.condition = threading.Condition()
        self.paused = False
        self.cancelled = False
        self.requests = 0
        self.budget = int(budget)
        if self.budget < 0:
            raise ValueError("请求预算不能为负数")
        self.budget_paused = False
        self.next_request = 0.

    def throttle(self, requests_per_second):
        """Share a rate limit between all requests belonging to this control."""
        interval = 1 / max(.1, float(requests_per_second))
        while True:
            self.checkpoint()
            with self.condition:
                now = time.monotonic()
                if now >= self.next_request:
                    self.next_request = now + interval
                    return
                self.condition.wait(min(.1, self.next_request - now))

    def command(self, action, budget=None):
        with self.condition:
            if action == "cancel":
                self.cancelled = True
            elif action == "pause":
                self.paused = True
                self.emit("state", state="paused", message="已暂停；当前传输到达检查点后停止，新请求不再派发")
            elif action == "resume":
                if budget is not None:
                    updated_budget = int(budget)
                    if updated_budget < 0:
                        raise ValueError("请求预算不能为负数")
                    self.budget = updated_budget
                if self.budget_paused and self.budget and self.requests >= self.budget:
                    self.emit("state", state="budget", message="预算已用完，请提高本次任务预算再继续")
                    return
                self.paused = False
                self.budget_paused = False
                self.emit("state", state="running", message="继续任务")
            self.condition.notify_all()

    def checkpoint(self, request=False):
        with self.condition:
            while True:
                if self.cancelled:
                    raise Cancelled()
                if request and self.budget and self.requests >= self.budget and not self.budget_paused:
                    self.paused = self.budget_paused = True
                    self.emit("state", state="budget", message=f"已达到本机请求预算 {self.budget}，请提高预算后继续")
                if not self.paused:
                    if request:
                        self.requests += 1
                    return
                self.condition.wait(.25)


def credentials_dict(value):
    return dict(value) if isinstance(value, dict) else {"token": value or ""}


def tile_path(tile, source=None, credentials=None):
    spec = source_registry.validate_source(source or source_registry.default_source())
    if spec == source_registry.validate_source(source_registry.default_source()):
        # The standard Mapbox tileset is account-independent. Preserve 2.4 caches.
        return home()/"cache"/"mapbox.satellite-jpg90"/str(tile.z)/f"{tile.x}_{tile.y}.jpg"
    key = source_registry.cache_key(spec, credentials_dict(credentials))
    return home()/"cache"/"sources"/key/str(tile.z)/f"{tile.x}_{tile.y}.tile"


def valid_tile(path, age=CACHE_SECONDS, source=None):
    spec = source_registry.validate_source(source or source_registry.default_source())
    try:
        if age and time.time()-path.stat().st_mtime > age:
            return False
        with Image.open(path) as im:
            formats = ("JPEG", "PNG", "WEBP") if spec["format"] == "auto" else (spec["format"],)
            if im.format not in formats or im.size != (spec["tile_size"], spec["tile_size"]):
                return False
            im.verify()
        return True
    except (OSError, ValueError):
        return False


def recommend_zoom(selection, resolution, source=None):
    spec = source_registry.validate_source(source or source_registry.default_source())
    grid = make_grid(selection, resolution)
    latitude = grid.geographic.centroid.y
    desired = math.ceil(math.log2(40075016.68557849 * math.cos(math.radians(latitude)) /
                                 (spec["tile_size"] * float(resolution))))
    levels = sorted(int(z) for z in spec.get("matrix_ids", {})) or list(range(spec["min_zoom"], spec["max_zoom"] + 1))
    return next((z for z in levels if z >= desired), levels[-1])


def plan_zoom(selection, resolution, zoom, source):
    z = recommend_zoom(selection, resolution, source) if zoom in (None, "auto") else int(zoom)
    if not source["min_zoom"] <= z <= source["max_zoom"] or (source.get("matrix_ids") and str(z) not in source["matrix_ids"]):
        raise ValueError(f"{source['name']} 不支持瓦片层级 {z}，请选择自动或有效层级")
    return z


def estimate(selection, resolution, zoom, reserve=10, source=None, imagery=True, credentials=None):
    grid = make_grid(selection, resolution)
    spec = source_registry.validate_source(source or source_registry.default_source())
    z = plan_zoom(selection, resolution, zoom, spec) if imagery else None
    tiles = tiles_for(grid, z, spec["datum"]) if imagery else []
    cached = sum(valid_tile(tile_path(t, spec, credentials), spec["cache_seconds"], spec) for t in tiles)
    new = len(tiles)-cached
    return {**grid.info(), "tiles": len(tiles), "cache": cached, "new_requests": new,
            "imagery": imagery, "zoom": z, "source_name": spec["name"] if imagery else "仅高程",
            "native_resolution_m": (40075016.68557849 * math.cos(math.radians(grid.geographic.centroid.y)) /
                                    (spec["tile_size"] * 2**z)) if imagery else None,
            "with_retry_reserve": math.ceil(new*(1+float(reserve)/100)),
            "estimated_download_mb": round(new*30/1024, 2),
            "estimated_disk_mb": round((grid.width*grid.height*(28 if imagery else 16) + len(tiles)*spec["tile_size"]**2*4)/1048576, 1),
            "estimated_working_memory_mb": 256,
            "cache_policy": f"按图源/版本/矩阵/凭据隔离；{spec['tile_size']}px 完整性校验；有效期 {spec['cache_seconds']/3600:g} 小时" if imagery else "仅本地高程时不产生网络请求"}


def session_for(options):
    s = requests.Session()
    if options.get("proxy"):
        s.proxies.update({"http": options["proxy"], "https": options["proxy"]})
        s.trust_env = False
    s.headers["User-Agent"] = "Mapdln/" + __version__
    return s


def fetch_tile(tile, token, options, control, ledger, kind="download", force=False, source=None):
    spec = source_registry.validate_source(source or source_registry.default_source())
    credentials = credentials_dict(token)
    secrets = source_registry.secret_values(credentials)
    path = tile_path(tile, spec, credentials)
    if not force and valid_tile(path, spec["cache_seconds"], spec):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    url, headers = source_registry.build_request(spec, tile, credentials)
    retries = int(options.get("retries", 3))
    with session_for(options) as session:
        for attempt in range(retries+1):
            control.throttle(spec.get("requests_per_second", 4))
            control.checkpoint(request=True)
            ledger.record(kind, options.get("task_id", ""), retry=attempt>0, source=spec["id"])
            try:
                response = session.get(url, headers=headers, timeout=(options.get("connect_timeout",10), options.get("read_timeout",30)), allow_redirects=False)
                if response.status_code in (401,403):
                    raise ValueError(f"{spec['name']} HTTP {response.status_code}：检查凭据、账户权限和服务限制")
                if 300 <= response.status_code < 400:
                    raise ValueError(f"{spec['name']} 返回重定向，请在图源管理中使用最终服务地址")
                response.raise_for_status()
                with Image.open(io.BytesIO(response.content)) as img:
                    formats = ("JPEG", "PNG", "WEBP") if spec["format"] == "auto" else (spec["format"],)
                    if img.size != (spec["tile_size"],spec["tile_size"]) or img.format not in formats:
                        raise ValueError(f"{spec['name']} 瓦片格式或尺寸异常，请检查图源配置")
                    img.verify()
                # Unique part names also protect concurrent preview/export requests.
                import uuid
                part = path.with_suffix("."+uuid.uuid4().hex+".part")
                part.write_bytes(response.content)
                os.replace(part, path)
                return path
            except (requests.RequestException, OSError) as exc:
                status=getattr(getattr(exc,'response',None),'status_code',None)
                if status is not None and status not in (408,429,500,502,503,504):
                    raise RuntimeError(f'瓦片 HTTP {status}：'+redact(exc,secrets)) from None
                if attempt == retries:
                    raise RuntimeError(f"瓦片 {tile.z}/{tile.x}/{tile.y} 网络或图片响应失败：{redact(exc, secrets)}") from None
                control.emit("log", message=f"瓦片网络异常，重试 {attempt+1}/{retries}")
                delay = min(30, 2**attempt)
                retry_after = getattr(getattr(exc, "response", None), "headers", {}).get("Retry-After")
                if retry_after:
                    try:
                        delay = max(delay, min(300, max(0, float(retry_after))))
                    except ValueError:
                        from email.utils import parsedate_to_datetime
                        try:
                            delay = max(delay, min(300, max(0, parsedate_to_datetime(retry_after).timestamp()-time.time())))
                        except (ValueError, TypeError, OverflowError):
                            pass
                deadline = time.monotonic()+delay
                while time.monotonic()<deadline:
                    control.checkpoint()
                    time.sleep(.1)
    raise RuntimeError("瓦片下载失败")


def windows(width, height, block=512):
    for row in range(0,height,block):
        for col in range(0,width,block):
            yield Window(col,row,min(block,width-col),min(block,height-row))


def mask_for(grid, win):
    return rasterize([(mapping(grid.polygon),255)], out_shape=(int(win.height),int(win.width)),
                     transform=window_transform(win,grid.transform), fill=0, dtype="uint8")


def profile(grid, count, dtype, nodata=None):
    return dict(driver="GTiff", width=grid.width,height=grid.height,count=count,dtype=dtype,
                crs=grid.crs,transform=grid.transform,nodata=nodata,tiled=True,blockxsize=256,blockysize=256,
                compress="deflate",BIGTIFF="IF_SAFER")


def export_mask(grid, temp, out, control):
    maskpath = temp/"shape-mask.tif"
    with rasterio.open(maskpath,"w",**profile(grid,1,"uint8")) as dst:
        for win in windows(grid.width,grid.height):
            control.checkpoint()
            dst.write(mask_for(grid,win),1,window=win)
    rio_copy(maskpath,out/"mask.png",driver="PNG")


def _source_pixels(src, rows, cols, control, cache):
    """Gather arbitrary pixels using bounded 256 px blocks, even when downsampling."""
    result = np.zeros((4, rows.size), dtype="uint8")
    valid = (rows >= 0) & (rows < src.height) & (cols >= 0) & (cols < src.width)
    indices = np.flatnonzero(valid)
    if not indices.size:
        return result
    across = math.ceil(src.width/256)
    blocks = (rows[indices]//256)*across + cols[indices]//256
    order = np.argsort(blocks, kind="stable")
    indices, blocks = indices[order], blocks[order]
    starts = np.r_[0, np.flatnonzero(np.diff(blocks))+1, blocks.size]
    for start, end in zip(starts[:-1], starts[1:]):
        control.checkpoint()
        key = int(blocks[start])
        row, col = divmod(key, across)
        data = cache.pop(key, None)
        if data is None:
            data = src.read(window=Window(col*256, row*256, min(256,src.width-col*256), min(256,src.height-row*256)))
        cache[key] = data
        if len(cache) > 16:
            cache.pop(next(iter(cache)))
        target = indices[start:end]
        result[:,target] = data[:,rows[target]-row*256,cols[target]-col*256]
    return result


def _gcj_window(src, grid, win, to_wgs84, control, cache):
    """Inverse map UTM pixel centres to GCJ source pixels; alpha-aware bilinear."""
    rows, cols = np.mgrid[int(win.row_off):int(win.row_off+win.height),
                         int(win.col_off):int(win.col_off+win.width)]
    east, north = grid.transform @ (cols+.5, rows+.5)
    lon, lat = to_wgs84.transform(east, north)
    lon, lat = wgs_to_gcj_array(lon, lat)
    world = 40075016.68557849
    x, y = lon*world/360, np.arcsinh(np.tan(np.deg2rad(lat)))*world/(2*np.pi)
    col, row = (~src.transform) @ (x, y)
    col, row = col.ravel()-.5, row.ravel()-.5
    left, top = np.floor(col).astype("int64"), np.floor(row).astype("int64")
    dx, dy = col-left, row-top
    rgb = np.zeros((3, row.size), dtype="float64")
    alpha = np.zeros(row.size, dtype="float64")
    for ox, oy, weight in ((0,0,(1-dx)*(1-dy)), (1,0,dx*(1-dy)),
                           (0,1,(1-dx)*dy), (1,1,dx*dy)):
        control.checkpoint()
        pixels = _source_pixels(src, top+oy, left+ox, control, cache)
        coverage = pixels[3].astype("float64")*weight
        rgb += pixels[:3]*coverage
        alpha += coverage
    np.divide(rgb, alpha, out=rgb, where=alpha>0)
    rgba = np.vstack((rgb, alpha))
    return np.rint(np.clip(rgba,0,255)).astype("uint8").reshape(4,int(win.height),int(win.width))


def satellite_export(grid, tiles, temp, out, control, source=None, credentials=None):
    """Sparse tiled source on disk; read target blocks through GDAL's WarpedVRT."""
    spec = source_registry.validate_source(source or source_registry.default_source())
    size = spec["tile_size"]
    gcj = spec["datum"] == "GCJ02"
    z = tiles[0].z
    xmin,xmax = min(t.x for t in tiles),max(t.x for t in tiles)
    ymin,ymax = min(t.y for t in tiles),max(t.y for t in tiles)
    lo,hi = mercantile.xy_bounds(xmin,ymax,z),mercantile.xy_bounds(xmax,ymin,z)
    source_path = temp/"satellite-source.tif"
    with rasterio.open(source_path,"w",driver="GTiff",width=(xmax-xmin+1)*size,height=(ymax-ymin+1)*size,
                       count=4,dtype="uint8",crs=None if gcj else "EPSG:3857",transform=from_bounds(lo.left,lo.bottom,hi.right,hi.top,(xmax-xmin+1)*size,(ymax-ymin+1)*size),
                       tiled=True,blockxsize=256,blockysize=256,compress="deflate",SPARSE_OK="TRUE",BIGTIFF="IF_SAFER") as dst:
        dst.colorinterp = (ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha)
        for index,t in enumerate(tiles):
            control.checkpoint()
            with Image.open(tile_path(t, spec, credentials)) as im:
                dst.write(np.moveaxis(np.asarray(im.convert("RGBA")),2,0),window=Window((t.x-xmin)*size,(t.y-ymin)*size,size,size))
            control.emit("progress",stage="影像网格准备",completed=index+1,total=len(tiles))
    aligned = temp/"satellite-rgba.tif"
    maskpath = temp/"shape-mask.tif"
    to_wgs84 = Transformer.from_crs(grid.crs, 4326, always_xy=True) if gcj else None
    cache = {}
    with rasterio.open(source_path) as src, (nullcontext(None) if gcj else WarpedVRT(src,crs=grid.crs,transform=grid.transform,width=grid.width,height=grid.height,resampling=Resampling.bilinear,warp_mem_limit=64)) as vrt, \
         rasterio.open(aligned,"w",**profile(grid,4,"uint8")) as dst, rasterio.open(maskpath,"w",**profile(grid,1,"uint8")) as maskdst:
        dst.colorinterp = (ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha)
        total = math.ceil(grid.width/512)*math.ceil(grid.height/512)
        for index,win in enumerate(windows(grid.width,grid.height)):
            control.checkpoint()
            mask = mask_for(grid,win)
            rgba = _gcj_window(src,grid,win,to_wgs84,control,cache) if gcj else vrt.read(window=win)
            rgba[3] = np.minimum(rgba[3],mask)
            rgba[:3,rgba[3]==0] = 0
            dst.write(rgba,window=win)
            maskdst.write(mask,1,window=win)
            control.emit("progress",stage="GCJ-02 影像校正与形状裁切" if gcj else "影像重采样与形状裁切",completed=index+1,total=total)
    control.checkpoint()
    rio_copy(aligned,out/"satellite.png",driver="PNG")
    rio_copy(maskpath,out/"mask.png",driver="PNG")


class RangeFile(io.RawIOBase):
    """Seekable HTTP archive with validator-keyed, resumable 1 MiB disk blocks."""
    BLOCK = 1048576

    def __init__(self,url,options,control):
        self.url,self.options,self.control = url,options,control
        self.session = session_for(options)
        self.timeout = (options.get("connect_timeout",10),options.get("read_timeout",30))
        control.checkpoint()
        with self.session.get(url,headers={"Range":"bytes=0-0"},timeout=self.timeout,stream=True) as r:
            r.raise_for_status()
            if r.status_code != 206 or not r.headers.get("Content-Range"):
                raise RuntimeError("FABDEM 服务器或代理未支持 HTTP Range，请更换代理；当前区块无法断点读取")
            self.size = int(r.headers["Content-Range"].split("/")[-1])
            self.validator = r.headers.get("ETag") or r.headers.get("Last-Modified")
            if not self.validator:
                raise RuntimeError("FABDEM 没有资源校验标识，无法安全复用分段缓存")
        key = hashlib.sha256((url+str(self.size)+self.validator).encode()).hexdigest()
        self.root = home()/"cache"/"fabdem-range"/key
        self.root.mkdir(parents=True,exist_ok=True)
        self.pos = 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self,offset,whence=0):
        self.pos = offset if whence==0 else self.pos+offset if whence==1 else self.size+offset
        if self.pos < 0:
            raise ValueError("负偏移")
        return self.pos

    def block(self,index):
        start = index*self.BLOCK
        end = min(self.size,start+self.BLOCK)-1
        length = end-start+1
        path = self.root/f"{index}.bin"
        if path.exists() and path.stat().st_size==length:
            return path.read_bytes()
        part = path.with_suffix(".part")
        for attempt in range(int(self.options.get("retries",3))+1):
            self.control.checkpoint()
            have = part.stat().st_size if part.exists() else 0
            if have > length:
                part.unlink()
                have = 0
            if have == length:
                os.replace(part,path)
                return path.read_bytes()
            try:
                headers = {"Range":f"bytes={start+have}-{end}","If-Range":self.validator}
                with self.session.get(self.url,headers=headers,timeout=self.timeout,stream=True) as r:
                    r.raise_for_status()
                    expected = f"bytes {start+have}-{end}/{self.size}"
                    if r.status_code != 206 or r.headers.get("Content-Range") != expected:
                        part.unlink(missing_ok=True)
                        raise RuntimeError("FABDEM 资源已变化或 Range 响应异常，请重新开始任务")
                    with part.open("ab") as f:
                        for chunk in r.iter_content(65536):
                            self.control.checkpoint()
                            f.write(chunk)
                            self.control.emit("progress",stage="FABDEM 分段下载",completed=f.tell(),total=length,unit="bytes")
                if part.stat().st_size != length:
                    raise OSError("分段下载不完整")
                os.replace(part,path)
                return path.read_bytes()
            except (requests.RequestException,OSError):
                if attempt == int(self.options.get("retries",3)):
                    raise
        raise RuntimeError("FABDEM 分段失败")

    def read(self,size=-1):
        size = min(self.size-self.pos, self.size if size<0 else size)
        if size <= 0:
            return b""
        chunks = []
        while size:
            self.control.checkpoint()
            index, offset = divmod(self.pos,self.BLOCK)
            chunk = self.block(index)[offset:offset+size]
            if not chunk:
                raise OSError("FABDEM 读取不完整")
            chunks.append(chunk)
            self.pos += len(chunk)
            size -= len(chunk)
        return b"".join(chunks)

    def close(self):
        self.session.close()
        super().close()


def tag(lat,lon):
    return f"{'N' if lat>=0 else 'S'}{abs(lat):02d}{'E' if lon>=0 else 'W'}{abs(lon):03d}"


def terrain_sources(grid,options,control):
    west,south,east,north = grid.geographic.bounds
    files=[]
    archives={}
    cache = home()/"cache"/"fabdem-v1-2"
    cache.mkdir(parents=True,exist_ok=True)
    try:
        for lat in range(math.floor(south),math.ceil(north)):
            for lon in range(math.floor(west),math.ceil(east)):
                control.checkpoint()
                name = tag(lat,lon)+"_FABDEM_V1-2.tif"
                path = cache/name
                if path.exists():
                    try:
                        with rasterio.open(path) as src:
                            src.read(1,window=Window(0,0,1,1))
                        files.append(path)
                        continue
                    except rasterio.errors.RasterioError:
                        pass
                south10,west10 = math.floor(lat/10)*10,math.floor(lon/10)*10
                archive = tag(south10,west10)+"-"+tag(south10+10,west10+10)+"_FABDEM_V1-2.zip"
                control.emit("log",message=f"FABDEM: {archive} → {name}（原始约 30 m）")
                if archive not in archives:
                    remote=RangeFile(FABDEM_BASE+archive,options,control)
                    try:
                        zipped=zipfile.ZipFile(remote)
                    except Exception:
                        remote.close()
                        raise
                    archives[archive]=(remote,zipped)
                remote,zipped=archives[archive]
                entry=next((n for n in zipped.namelist() if Path(n).name==name),None)
                if entry is None:
                    raise RuntimeError(f"FABDEM 无此区域的数据：{name}；可切换 0 米模式")
                part=path.with_suffix(".part")
                with zipped.open(entry) as src,part.open("wb") as dst:
                    while True:
                        control.checkpoint()
                        chunk=src.read(1048576)
                        if not chunk:
                            break
                        dst.write(chunk)
                with rasterio.open(part) as src:
                    if src.crs is None:
                        raise RuntimeError("FABDEM 缺少坐标信息")
                    src.read(1,window=Window(0,0,1,1))
                os.replace(part,path)
                files.append(path)
        return files
    finally:
        for remote,zipped in archives.values():
            zipped.close()
            remote.close()


def gaussian_kernel(sigma):
    """Match the previous Gaussian's truncate=4 radius without SciPy."""
    if sigma == 0:
        return np.ones(1)
    radius = int(sigma * 4 + .5)
    x = np.arange(-radius, radius + 1)
    kernel_1d = np.exp(-x**2 / (2 * sigma**2))
    kernel_1d /= kernel_1d.sum()
    return kernel_1d

def convolve_2d(data, kernel):
    """2D 可分离卷积（纯 numpy，无 scipy 依赖）"""
    # 行方向卷积
    pad = len(kernel) // 2
    # NumPy's symmetric padding matches SciPy's default half-sample reflect.
    padded = np.pad(data, ((0, 0), (pad, pad)), mode='symmetric')
    result = np.zeros_like(data)
    for i, k in enumerate(kernel):
        result += padded[:, i:i+data.shape[1]] * k

    # 列方向卷积
    padded = np.pad(result, ((pad, pad), (0, 0)), mode='symmetric')
    final = np.zeros_like(data)
    for i, k in enumerate(kernel):
        final += padded[i:i+data.shape[0], :] * k

    return final


def elevation_export(grid,files,out,temp,options,control):
    from contextlib import ExitStack
    local_snapshot = options.get("dem_snapshot")
    if local_snapshot:
        from .local_dem import check_dem_unchanged
        check_dem_unchanged(local_snapshot)
    nodata=float("nan") if local_snapshot else -9999.
    dem=out/"dem.tif"
    with ExitStack() as stack:
        sources=[stack.enter_context(rasterio.open(f)) for f in files]
        vrts=[stack.enter_context(WarpedVRT(src,crs=grid.crs,transform=grid.transform,width=grid.width,height=grid.height,
                                          resampling=Resampling.bilinear,dtype="float32",nodata=nodata,warp_mem_limit=64)) for src in sources]
        dst=stack.enter_context(rasterio.open(dem,"w",**profile(grid,1,"float32",nodata)))
        for win in windows(grid.width,grid.height):
            control.checkpoint()
            mask=mask_for(grid,win)>0
            data=np.full(mask.shape, nodata if files else 0, dtype="float32")
            for vrt in vrts:
                if local_snapshot:
                    from .local_dem import aligned_dem_data
                    values,valid = aligned_dem_data(vrt,win,local_snapshot)
                else:
                    a=vrt.read(1,window=win,masked=True)
                    values=a.data
                    valid=(~np.ma.getmaskarray(a)) & np.isfinite(values)
                data[valid]=values[valid]
            data[~mask]=nodata
            if files and np.any(mask & (~np.isfinite(data) | (data==nodata))):
                raise RuntimeError("高程在选区内存在缺失，请缩小选区或更换数据；已生成的影像成果保留")
            dst.write(data,1,window=win)
    sigma=float(options.get("smooth",0)) if files else 0
    smoothpath=temp/"smoothed.tif"
    low,high=math.inf,-math.inf
    with rasterio.open(dem) as src,rasterio.open(smoothpath,"w",**profile(grid,1,"float32",nodata)) as dst:
        for win in windows(grid.width,grid.height):
            control.checkpoint()
            halo=math.ceil(sigma*4)
            x0=max(0,int(win.col_off)-halo); y0=max(0,int(win.row_off)-halo)
            x1=min(grid.width,int(win.col_off+win.width)+halo); y1=min(grid.height,int(win.row_off+win.height)+halo)
            a=src.read(1,window=Window(x0,y0,x1-x0,y1-y0))
            valid=np.isfinite(a) & (a!=nodata)
            if sigma:
                # 使用纯 numpy 高斯卷积（无 scipy 依赖）
                kernel = gaussian_kernel(sigma)
                weights = convolve_2d(valid.astype("float32"), kernel)
                a = convolve_2d(np.where(valid, a, 0), kernel) / np.maximum(weights, 1e-12)
            a[~valid]=nodata
            r=int(win.row_off)-y0; c=int(win.col_off)-x0
            a=a[r:r+int(win.height),c:c+int(win.width)]
            values=a[np.isfinite(a) & (a!=nodata)]
            if values.size:
                low=min(low,float(values.min())); high=max(high,float(values.max()))
            dst.write(a,1,window=win)
    if not math.isfinite(low):
        raise RuntimeError("选区未包含有效高程像素")
    bits=int(options.get("bit_depth",16))
    if bits not in (8,16):
        raise ValueError("高度图位深应为 8 或 16")
    dtype="uint16" if bits==16 else "uint8"
    graypath=temp/"height-gray.tif"
    with rasterio.open(smoothpath) as src,rasterio.open(graypath,"w",**profile(grid,1,dtype)) as dst:
        for win in windows(grid.width,grid.height):
            control.checkpoint()
            a=src.read(1,window=win)
            v=np.isfinite(a) & (a!=nodata)
            gray=np.zeros(a.shape,dtype=dtype)
            if high>low:
                gray[v]=np.clip((a[v]-low)/(high-low)*((1<<bits)-1),0,(1<<bits)-1).astype(dtype)
            dst.write(gray,1,window=win)
    control.checkpoint()
    rio_copy(graypath,out/"height.png",driver="PNG")
    if local_snapshot:
        check_dem_unchanged(local_snapshot)
    return {"min_m":low,"max_m":high,"bit_depth":bits,"smooth_sigma":sigma,
            "encoding":"linear(min_m,max_m); outside=0 with separate mask.png; constant terrain=0",
            "dem":"actual unsmoothed metres; outside NoData=" + ("NaN" if local_snapshot else "-9999") + "; valid 0 is not NoData",
            "source":("local GeoTIFF: " + Path(local_snapshot["path"]).name) if local_snapshot else ("FABDEM V1-2 (~30m)" if files else "flat 0 m; no FABDEM network requests")}


def run_task(task,token,emit,control=None):
    with rasterio.Env(GDAL_CACHEMAX=64*1024*1024):
        return _run_task(task,token,emit,control)


def _run_task(task,token,emit,control=None):
    import uuid
    control=control or Control(emit,task.get("budget",0))
    grid=make_grid(task["selection"],task.get("resolution",1))
    imagery=bool(task.get("imagery",True))
    source=source_registry.validate_source(task.get("source") or source_registry.default_source()) if imagery else source_registry.default_source()
    credentials=credentials_dict(token) if imagery else {}
    secrets=source_registry.secret_values(credentials)
    z=plan_zoom(task["selection"],task.get("resolution",1),task.get("zoom",17),source) if imagery else None
    tiles=tiles_for(grid,z,source["datum"]) if imagery else []
    options=dict(task.get("options",{}))
    taskid=task.get("task_id") or str(int(time.time()))
    options["task_id"]=taskid
    mode=task.get("elevation","flat")
    if mode not in ("flat","real","local"):
        raise ValueError("未知高程模式")
    if imagery:
        source_registry.build_request(source,tiles[0],credentials)
    if mode=="local" and not task.get("dem",{}).get("path"):
        raise ValueError("请选择本地 DEM 文件")
    out=Path(task["output"]).resolve()
    # The external task identifier must never determine a path later deleted.
    temp=out/(".work-"+uuid.uuid4().hex)
    if mode=="local":
        local_path=Path(task["dem"]["path"]).resolve()
        target=out/"dem.tif"
        same_file=local_path==target or (target.exists() and local_path.exists() and os.path.samefile(local_path,target))
        if same_file or local_path.is_relative_to(temp):
            raise ValueError("本地 DEM 输入与输出位置重合，请更换输出目录以保护原始文件")
    out.mkdir(parents=True,exist_ok=True)
    if (out/"metadata.json").exists():
        raise ValueError("输出目录已有完成的成果，请更换任务名以保留原文件")
    # Failed tasks may continue with flat elevation, but never mix their output
    # with a different area, grid, source or imagery-only/DEM-only mode.
    manifest=out/".mapdln-task.json"
    identity={"selection":grid.selection,"grid":grid.info(),"imagery":imagery,"zoom":z,
              "source":source_registry.cache_key(source,credentials) if imagery else None}
    fingerprint=hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    if manifest.exists():
        try:
            previous=json.loads(manifest.read_text(encoding="utf-8"))
        except (ValueError,OSError):
            raise ValueError("输出目录内的未完成任务记录损坏，请更换任务名") from None
        if previous.get("fingerprint")!=fingerprint:
            raise ValueError("输出目录包含其他图源或选区的未完成成果，请更换任务名以避免混用")
    elif any((out/name).exists() for name in ("satellite.png","dem.tif","height.png","mask.png","boundary.geojson")):
        raise ValueError("输出目录包含未识别的已有成果，请更换任务名以保留原文件")
    atomic_json(manifest,{"fingerprint":fingerprint})
    temp.mkdir(parents=True,exist_ok=False)
    logpath=out/"task.log"
    rawemit=emit
    loglock=threading.Lock()
    def logged(kind,**fields):
        fields={k:redact(v,secrets) if isinstance(v,str) else v for k,v in fields.items()}
        with loglock,logpath.open("a",encoding="utf-8") as log:
            log.write(json.dumps({"type":kind,**fields},ensure_ascii=False)+"\n")
        rawemit(kind,**fields)
    emit=logged
    control.emit=emit
    # Initialization must not overwrite a pause received through stdin while
    # imports and planning were still in progress. Serialize this announcement
    # with commands so a concurrent pause always remains the latest state.
    with control.condition:
        if control.cancelled:raise Cancelled()
        if control.paused:
            emit("state",state="budget" if control.budget_paused else "paused",message="任务已暂停；继续后开始处理")
        else:
            emit("state",state="running",message="规划完成，开始处理" + ("影像与高程" if imagery else "高程"))
    files=[]
    if mode=="local":
        from .local_dem import validate_dem
        emit("progress",stage="检查本地 DEM 覆盖范围和有效像素",completed=0,total=0)
        options["dem_snapshot"]=validate_dem(task["dem"],grid,control)
        files=[options["dem_snapshot"]["path"]]
    ledger=Ledger()
    started=time.monotonic()
    done=0
    workers=max(1,min(8,int(source.get("max_concurrency",4)),int(options.get("concurrency",4))))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        pending={}
        iterator=iter(tiles)
        def schedule():
            control.checkpoint()
            try:
                tile=next(iterator)
            except StopIteration:
                return False
            pending[pool.submit(fetch_tile,tile,credentials,options,control,ledger,source=source)]=tile
            return True
        for _ in range(workers):
            schedule()
        try:
            while pending:
                completed,_=concurrent.futures.wait(pending,timeout=.25,return_when=concurrent.futures.FIRST_COMPLETED)
                for future in completed:
                    pending.pop(future)
                    future.result()
                    done+=1
                    emit("progress",stage="影像下载 / 有效缓存复用",completed=done,total=len(tiles),
                         speed=round(done/max(.001,time.monotonic()-started),2),requests=control.requests)
                    schedule()
        except BaseException:
            control.command("cancel")
            for future in pending:
                future.cancel()
            raise
    if imagery:
        satellite_export(grid,tiles,temp,out,control,source,credentials)
    else:
        export_mask(grid,temp,out,control)
    atomic_json(out/"boundary.geojson",boundary_json(grid))
    emit("log",message=("影像与掩膜" if imagery else "选区掩膜")+"已保存；像素网格保持指定米制分辨率")
    if mode=="real":
        emit("progress",stage="获取真实 FABDEM 高程（无需 API Key）",completed=0,total=0)
        try:
            files=terrain_sources(grid,options,control)
        except Cancelled:
            raise
        except Exception as exc:
            emit("terrain_failed",message="真实高程失败，已完成成果与缓存保留："+redact(exc,secrets))
            raise RuntimeError("真实高程失败。可使用同一输出目录切换 0 米模式继续，有效影像缓存会复用。") from None
    emit("progress",stage="真实高程重采样与编码" if files else "生成纯黑 0 米高程（跳过 FABDEM）",completed=0,total=0)
    try:
        encoding=elevation_export(grid,files,out,temp,options,control)
    except Cancelled:
        raise
    except Exception as exc:
        if mode in ('real','local'):
            emit('terrain_failed',message='高程处理失败，已完成成果保留：'+redact(exc,secrets))
        raise
    safe_task={k:v for k,v in task.items() if k in ("selection","resolution","elevation","task_id","budget")}
    safe_task.update(imagery=imagery,zoom=z)
    safe_task["options"]={k:v for k,v in options.items() if k in ("concurrency","retries","connect_timeout","read_timeout","smooth","bit_depth")}
    dem_meta=None
    if mode=="local":
        dem_meta={k:v for k,v in options["dem_snapshot"].items() if k in ("unit","unit_factor","scale","offset","crs","resolution","resolution_m","bounds_wgs84","vertical_datum","fingerprint")}
        dem_meta["file_name"]=Path(options["dem_snapshot"]["path"]).name
    atomic_json(out/"metadata.json",{**grid.info(),"version":__version__,"selection":grid.selection,
                "curve_interpolation":"closed centripetal Catmull–Rom; adaptive chord sampling res/4" if grid.selection["shape"]=="curve" else None,
                "height_encoding":encoding,"task":safe_task,"local_mapbox_requests":control.requests if imagery and source['id']=='mapbox' else 0,
                "local_source_requests":control.requests,"imagery_source":source_registry.public_metadata(source) if imagery else None,"local_dem":dem_meta,
                "imagery_georeferencing":("GCJ02 source pixels inverse-sampled onto the WGS84-derived output grid; local approximate conversion, not survey accuracy" if source["datum"]=="GCJ02" else "Standard Web Mercator reprojected onto the output grid") if imagery else None,
                "native_resolution_note":"Output pixel size does not increase source detail or establish position accuracy",
                "attribution":"; ".join(filter(None,[source.get("attribution","") if imagery else "", "FABDEM V1-2 CC BY-NC-SA 4.0" if mode=="real" else ("User-supplied local DEM" if mode=="local" else "")]))})
    # Remove only our own intermediates after success; never touch user's files/cache.
    import shutil
    shutil.rmtree(temp)
    manifest.unlink(missing_ok=True)
    emit("done",output=str(out),message="下载与导出完成")
    return out
