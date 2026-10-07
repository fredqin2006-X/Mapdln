"""Bounded downloads, validated cache and block-wise geographic exports."""
import concurrent.futures
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
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.shutil import copy as rio_copy
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window, transform as window_transform
import mercantile
from scipy.ndimage import gaussian_filter
from shapely.geometry import mapping
from .config import home, Ledger, atomic_json, redact
from .geometry import make_grid, tiles_for, boundary_json

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
        self.budget_paused = False

    def command(self, action, budget=None):
        with self.condition:
            if action == "cancel":
                self.cancelled = True
            elif action == "pause":
                self.paused = True
                self.emit("state", state="paused", message="已暂停；当前传输到达检查点后停止，新请求不再派发")
            elif action == "resume":
                if budget is not None:
                    self.budget = int(budget)
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


def tile_path(tile):
    return home()/"cache"/"mapbox.satellite-jpg90"/str(tile.z)/f"{tile.x}_{tile.y}.jpg"


def valid_tile(path, age=CACHE_SECONDS):
    try:
        if age and time.time()-path.stat().st_mtime > age:
            return False
        with Image.open(path) as im:
            if im.format != "JPEG" or im.size != (256,256):
                return False
            im.verify()
        return True
    except (OSError, ValueError):
        return False


def estimate(selection, resolution, zoom, reserve=10):
    grid = make_grid(selection, resolution)
    tiles = tiles_for(grid, zoom)
    cached = sum(valid_tile(tile_path(t)) for t in tiles)
    new = len(tiles)-cached
    return {**grid.info(), "tiles": len(tiles), "cache": cached, "new_requests": new,
            "with_retry_reserve": math.ceil(new*(1+float(reserve)/100)),
            "estimated_download_mb": round(new*30/1024, 2),
            "estimated_disk_mb": round((grid.width*grid.height*28 + len(tiles)*256*256*3)/1048576, 1),
            "estimated_working_memory_mb": 256,
            "cache_policy": "同图层/层级/jpg90/256px；JPEG 完整性校验；12 小时有效"}


def session_for(options):
    s = requests.Session()
    if options.get("proxy"):
        s.proxies.update({"http": options["proxy"], "https": options["proxy"]})
        s.trust_env = False
    s.headers["User-Agent"] = "Mapdln/2.0"
    return s


def fetch_tile(tile, token, options, control, ledger, kind="download", force=False):
    path = tile_path(tile)
    if not force and valid_tile(path):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://api.mapbox.com/v4/mapbox.satellite/{tile.z}/{tile.x}/{tile.y}.jpg90"
    retries = int(options.get("retries", 3))
    with session_for(options) as session:
        for attempt in range(retries+1):
            control.checkpoint(request=True)
            ledger.record(kind, options.get("task_id", ""), retry=attempt>0)
            try:
                response = session.get(url, params={"access_token": token}, timeout=(options.get("connect_timeout",10), options.get("read_timeout",30)))
                if response.status_code in (401,403):
                    raise ValueError(f"Mapbox HTTP {response.status_code}：检查 Token、账户状态或网页 URL 限制；桌面请求不伪造 Referer")
                response.raise_for_status()
                with Image.open(io.BytesIO(response.content)) as img:
                    if img.size != (256,256) or img.format != "JPEG":
                        raise ValueError("卫星瓦片格式或尺寸异常")
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
                    raise RuntimeError(f'瓦片 HTTP {status}：'+redact(exc,[token])) from None
                if attempt == retries:
                    raise RuntimeError(f"瓦片 {tile.z}/{tile.x}/{tile.y} 网络失败：{redact(exc, [token])}") from None
                control.emit("log", message=f"瓦片网络异常，重试 {attempt+1}/{retries}")
                deadline = time.monotonic()+min(30, 2**attempt)
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


def satellite_export(grid, tiles, temp, out, control):
    """Sparse tiled source on disk; read target blocks through GDAL's WarpedVRT."""
    z = tiles[0].z
    xmin,xmax = min(t.x for t in tiles),max(t.x for t in tiles)
    ymin,ymax = min(t.y for t in tiles),max(t.y for t in tiles)
    lo,hi = mercantile.xy_bounds(xmin,ymax,z),mercantile.xy_bounds(xmax,ymin,z)
    source = temp/"satellite-source.tif"
    with rasterio.open(source,"w",driver="GTiff",width=(xmax-xmin+1)*256,height=(ymax-ymin+1)*256,
                       count=3,dtype="uint8",crs="EPSG:3857",transform=from_bounds(lo.left,lo.bottom,hi.right,hi.top,(xmax-xmin+1)*256,(ymax-ymin+1)*256),
                       tiled=True,blockxsize=256,blockysize=256,compress="deflate",SPARSE_OK="TRUE",BIGTIFF="IF_SAFER") as dst:
        for index,t in enumerate(tiles):
            control.checkpoint()
            with Image.open(tile_path(t)) as im:
                dst.write(np.moveaxis(np.asarray(im.convert("RGB")),2,0),window=Window((t.x-xmin)*256,(t.y-ymin)*256,256,256))
            control.emit("progress",stage="卫星网格准备",completed=index+1,total=len(tiles))
    aligned = temp/"satellite-rgba.tif"
    maskpath = temp/"shape-mask.tif"
    with rasterio.open(source) as src, WarpedVRT(src,crs=grid.crs,transform=grid.transform,width=grid.width,height=grid.height,resampling=Resampling.bilinear,warp_mem_limit=64) as vrt, \
         rasterio.open(aligned,"w",**profile(grid,4,"uint8")) as dst, rasterio.open(maskpath,"w",**profile(grid,1,"uint8")) as maskdst:
        total = math.ceil(grid.width/512)*math.ceil(grid.height/512)
        for index,win in enumerate(windows(grid.width,grid.height)):
            control.checkpoint()
            mask = mask_for(grid,win)
            rgb = vrt.read(window=win)
            rgb[:,mask==0] = 0
            dst.write(np.concatenate([rgb,mask[None]],axis=0),window=win)
            maskdst.write(mask,1,window=win)
            control.emit("progress",stage="卫星重采样与形状裁切",completed=index+1,total=total)
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


def elevation_export(grid,files,out,temp,options,control):
    from contextlib import ExitStack
    nodata=-9999.
    dem=out/"dem.tif"
    with ExitStack() as stack:
        sources=[stack.enter_context(rasterio.open(f)) for f in files]
        vrts=[stack.enter_context(WarpedVRT(src,crs=grid.crs,transform=grid.transform,width=grid.width,height=grid.height,
                                          resampling=Resampling.bilinear,nodata=nodata,warp_mem_limit=64)) for src in sources]
        dst=stack.enter_context(rasterio.open(dem,"w",**profile(grid,1,"float32",nodata)))
        for win in windows(grid.width,grid.height):
            control.checkpoint()
            mask=mask_for(grid,win)>0
            data=np.full(mask.shape, nodata if files else 0, dtype="float32")
            for vrt in vrts:
                a=vrt.read(1,window=win,masked=True)
                valid=(~np.ma.getmaskarray(a)) & np.isfinite(a.data) & (a.data>=-1000) & (a.data<=9000)
                data[valid]=a.data[valid]
            data[~mask]=nodata
            if files and np.any(mask & (data==nodata)):
                raise RuntimeError("真实高程在选区内存在缺失，卫星成果已保留；可切换 0 米模式")
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
            valid=a!=nodata
            if sigma:
                weights=gaussian_filter(valid.astype("float32"),sigma)
                a=gaussian_filter(np.where(valid,a,0),sigma)/np.maximum(weights,1e-12)
            a[~valid]=nodata
            r=int(win.row_off)-y0; c=int(win.col_off)-x0
            a=a[r:r+int(win.height),c:c+int(win.width)]
            values=a[a!=nodata]
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
            v=a!=nodata
            gray=np.zeros(a.shape,dtype=dtype)
            if high>low:
                gray[v]=np.clip((a[v]-low)/(high-low)*((1<<bits)-1),0,(1<<bits)-1).astype(dtype)
            dst.write(gray,1,window=win)
    control.checkpoint()
    rio_copy(graypath,out/"height.png",driver="PNG")
    return {"min_m":low,"max_m":high,"bit_depth":bits,"smooth_sigma":sigma,
            "encoding":"linear(min_m,max_m); outside=0 with separate mask.png; constant terrain=0",
            "dem":"actual unsmoothed metres; outside NoData=-9999; valid 0 is not NoData",
            "source":"FABDEM V1-2 (~30m)" if files else "flat 0 m; no FABDEM network requests"}


def run_task(task,token,emit,control=None):
    with rasterio.Env(GDAL_CACHEMAX=64*1024*1024):
        return _run_task(task,token,emit,control)


def _run_task(task,token,emit,control=None):
    control=control or Control(emit,task.get("budget",0))
    grid=make_grid(task["selection"],task.get("resolution",1))
    tiles=tiles_for(grid,task.get("zoom",17))
    options=dict(task.get("options",{}))
    taskid=task.get("task_id") or str(int(time.time()))
    options["task_id"]=taskid
    mode=task.get("elevation","flat")
    if mode not in ("flat","real"):
        raise ValueError("未知高程模式")
    if not token or not token.startswith("pk."):
        raise ValueError("请到账户配置页填写 Mapbox 公共 Access Token（pk.）")
    out=Path(task["output"]).resolve()
    out.mkdir(parents=True,exist_ok=True)
    if (out/"metadata.json").exists():
        raise ValueError("输出目录已有完成的成果，请更换任务名以保留原文件")
    temp=out/(".work-"+taskid)
    temp.mkdir(parents=True,exist_ok=True)
    logpath=out/"task.log"
    rawemit=emit
    loglock=threading.Lock()
    def logged(kind,**fields):
        fields={k:redact(v,[token]) if isinstance(v,str) else v for k,v in fields.items()}
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
            emit("state",state="budget" if control.budget_paused else "paused",message="任务已暂停；继续后开始下载卫星影像")
        else:
            emit("state",state="running",message="规划完成，先下载卫星影像")
    ledger=Ledger()
    started=time.monotonic()
    done=0
    workers=max(1,min(8,int(options.get("concurrency",4))))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        pending={}
        iterator=iter(tiles)
        def schedule():
            control.checkpoint()
            try:
                tile=next(iterator)
            except StopIteration:
                return False
            pending[pool.submit(fetch_tile,tile,token,options,control,ledger)]=tile
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
                    emit("progress",stage="卫星下载 / 有效缓存复用",completed=done,total=len(tiles),
                         speed=round(done/max(.001,time.monotonic()-started),2),requests=control.requests)
                    schedule()
        except BaseException:
            control.command("cancel")
            for future in pending:
                future.cancel()
            raise
    satellite_export(grid,tiles,temp,out,control)
    atomic_json(out/"boundary.geojson",boundary_json(grid))
    emit("log",message="卫星与掩膜已保存；像素网格保持指定米制分辨率")
    files=[]
    if mode=="real":
        emit("progress",stage="获取真实 FABDEM 高程（无需 API Key）",completed=0,total=0)
        try:
            files=terrain_sources(grid,options,control)
        except Cancelled:
            raise
        except Exception as exc:
            emit("terrain_failed",message="真实高程失败，卫星成果与缓存已保留："+redact(exc,[token]))
            raise RuntimeError("真实高程失败。可使用同一输出目录切换 0 米模式继续，不重新下载有效卫星缓存。") from None
    emit("progress",stage="真实高程重采样与编码" if files else "生成纯黑 0 米高程（跳过 FABDEM）",completed=0,total=0)
    try:
        encoding=elevation_export(grid,files,out,temp,options,control)
    except Cancelled:
        raise
    except Exception as exc:
        if mode=='real':
            emit('terrain_failed',message='真实高程处理失败，卫星成果已保留：'+redact(exc,[token]))
        raise
    safe_task={k:v for k,v in task.items() if k not in ("options",)}
    safe_task["options"]={k:v for k,v in options.items() if k!="proxy"}
    atomic_json(out/"metadata.json",{**grid.info(),"version":"2.0.0","selection":grid.selection,
                "curve_interpolation":"closed centripetal Catmull–Rom; adaptive chord sampling res/4" if grid.selection["shape"]=="curve" else None,
                "height_encoding":encoding,"task":safe_task,"local_mapbox_requests":control.requests,
                "native_resolution_note":"1 m output does not add FABDEM detail or establish position accuracy",
                "attribution":"© Mapbox © Maxar © OpenStreetMap; FABDEM V1-2 CC BY-NC-SA 4.0 when used"})
    # Remove only our own intermediates after success; never touch user's files/cache.
    import shutil
    shutil.rmtree(temp)
    emit("done",output=str(out),message="下载与导出完成")
    return out
