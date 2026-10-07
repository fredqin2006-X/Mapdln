"""Selection coordinates, adaptive curves and a single metric output grid."""
import math
from dataclasses import dataclass
import numpy as np
import mercantile
from pyproj import CRS, Transformer, Proj
from rasterio.transform import from_origin
from shapely.geometry import Polygon, box, mapping
from shapely.ops import transform
from shapely.validation import explain_validity


def wgs_to_gcj(lon, lat):
    if not (72.004 < lon < 137.8347 and .8293 < lat < 55.8271):
        return lon, lat
    x, y = lon - 105, lat - 35
    dl = -100 + 2*x + 3*y + .2*y*y + .1*x*y + .2*math.sqrt(abs(x))
    dn = 300 + x + 2*y + .1*x*x + .1*x*y + .1*math.sqrt(abs(x))
    shared = (20*math.sin(6*x*math.pi) + 20*math.sin(2*x*math.pi))*2/3
    dl += shared + (20*math.sin(y*math.pi)+40*math.sin(y/3*math.pi))*2/3
    dl += (160*math.sin(y/12*math.pi)+320*math.sin(y*math.pi/30))*2/3
    dn += shared + (20*math.sin(x*math.pi)+40*math.sin(x/3*math.pi))*2/3
    dn += (150*math.sin(x/12*math.pi)+300*math.sin(x/30*math.pi))*2/3
    rad = math.radians(lat)
    magic = 1 - .00669342162296594323*math.sin(rad)**2
    dl = dl*180 / ((6378245*(1-.00669342162296594323)/(magic*math.sqrt(magic)))*math.pi)
    dn = dn*180 / ((6378245/math.sqrt(magic)*math.cos(rad))*math.pi)
    return lon + dn, lat + dl


def gcj_to_wgs(lon, lat):
    x, y = lon, lat
    for _ in range(12):
        a, b = wgs_to_gcj(x, y)
        dx, dy = a-lon, b-lat
        x -= dx
        y -= dy
        if max(abs(dx), abs(dy)) < 1e-10:
            break
    return x, y


def gcj_to_bd(lon, lat):
    z = math.hypot(lon, lat) + .00002*math.sin(lat*math.pi*3000/180)
    t = math.atan2(lat, lon) + .000003*math.cos(lon*math.pi*3000/180)
    return z*math.cos(t)+.0065, z*math.sin(t)+.006


def bd_to_gcj(lon, lat):
    x, y = lon-.0065, lat-.006
    z = math.hypot(x, y) - .00002*math.sin(y*math.pi*3000/180)
    t = math.atan2(y, x) - .000003*math.cos(x*math.pi*3000/180)
    return z*math.cos(t), z*math.sin(t)


def to_wgs(point, system):
    x, y = map(float, point)
    if system == "BD09":
        x, y = bd_to_gcj(x, y)
        return gcj_to_wgs(x, y)
    if system == "GCJ02":
        return gcj_to_wgs(x, y)
    if system != "WGS84":
        raise ValueError("未知坐标体系")
    return x, y


def from_wgs(point, system):
    x, y = map(float, point)
    if system == "GCJ02":
        return wgs_to_gcj(x, y)
    if system == "BD09":
        return gcj_to_bd(*wgs_to_gcj(x, y))
    return x, y


def metric_crs(lon, lat):
    zone = min(60, max(1, int((lon + 180)/6) + 1))
    return CRS.from_epsg((32600 if lat >= 0 else 32700) + zone)


def catmull_closed(points, tolerance):
    pts = [np.asarray(p, dtype=float) for p in points]
    if len(pts) < 3 or any(np.linalg.norm(pts[i]-pts[(i+1)%len(pts)]) < .001 for i in range(len(pts))):
        raise ValueError("曲线至少需要三个不同的控制点，相邻控制点不能重合")
    result = []
    for i in range(len(pts)):
        p0, p1, p2, p3 = [pts[j % len(pts)] for j in (i-1, i, i+1, i+2)]
        t0 = 0.
        t1 = t0 + np.linalg.norm(p1-p0)**.5
        t2 = t1 + np.linalg.norm(p2-p1)**.5
        t3 = t2 + np.linalg.norm(p3-p2)**.5
        def at(u):
            t = t1 + u*(t2-t1)
            a1 = (t1-t)/(t1-t0)*p0 + (t-t0)/(t1-t0)*p1
            a2 = (t2-t)/(t2-t1)*p1 + (t-t1)/(t2-t1)*p2
            a3 = (t3-t)/(t3-t2)*p2 + (t-t2)/(t3-t2)*p3
            b1 = (t2-t)/(t2-t0)*a1 + (t-t0)/(t2-t0)*a2
            b2 = (t3-t)/(t3-t1)*a2 + (t-t1)/(t3-t1)*a3
            return (t2-t)/(t2-t1)*b1 + (t-t1)/(t2-t1)*b2
        def segment(a, b, pa, pb, depth=0):
            qs = [a+(b-a)*f for f in (.25,.5,.75)]
            error = max(np.linalg.norm(at(q)-(pa+(pb-pa)*(q-a)/(b-a))) for q in qs)
            if error <= tolerance:
                result.append(pa.tolist())
            elif depth >= 20 or len(result) > 100000:
                raise ValueError("曲线过于复杂，请减少控制点或增大分辨率")
            else:
                mid = (a+b)/2
                pm = at(mid)
                segment(a, mid, pa, pm, depth+1)
                segment(mid, b, pm, pb, depth+1)
        segment(0, 1, p1, p2)
    return result


@dataclass
class Grid:
    selection: dict
    polygon: object
    geographic: object
    crs: object
    transform: object
    width: int
    height: int
    resolution: float
    controls: list

    def info(self):
        p = self.geographic.centroid
        factors = Proj(self.crs).get_factors(p.x, p.y)
        return {"crs": self.crs.to_string(), "transform": list(self.transform)[:6],
                "width": self.width, "height": self.height, "resolution_m": self.resolution,
                "wgs84_bounds": list(self.geographic.bounds), "area_m2": self.polygon.area,
                "projected_bounds": [self.transform.c, self.transform.f-self.height*self.resolution,
                                     self.transform.c+self.width*self.resolution, self.transform.f],
                "projection_scale_at_center": {"meridional": factors.meridional_scale, "parallel": factors.parallel_scale},
                "position_accuracy": "GCJ02/BD09 使用本地近似转换；像素分辨率不代表定位精度"}


def make_grid(selection, resolution=1):
    res = float(resolution)
    if not math.isfinite(res) or res <= 0:
        raise ValueError("分辨率必须为正数")
    if selection.get("shape") not in ("rectangle", "circle", "polygon", "curve"):
        raise ValueError("请选择矩形、圆形、多边形或插值曲线")
    raw = selection.get("points", [])
    if not raw or len(raw) > 1000:
        raise ValueError("请先绘制选区（最多 1000 个控制点）")
    points = [to_wgs(p, selection.get("system", "WGS84")) for p in raw]
    if any(not math.isfinite(x) or not math.isfinite(y) or not -180 <= x <= 180 or not -80 <= y <= 84 for x, y in points):
        raise ValueError("当前支持经度 ±180°、纬度 -80° 至 84° 的局部选区")
    if max(p[0] for p in points)-min(p[0] for p in points) > 3:
        raise ValueError("请将任务分割为局部区域；不支持跨日期变更线")
    crs = metric_crs(*points[0])
    fwd = Transformer.from_crs(4326, crs, always_xy=True)
    inv = Transformer.from_crs(crs, 4326, always_xy=True)
    xy = [fwd.transform(*p) for p in points]
    kind = selection["shape"]
    if kind == "rectangle":
        if selection.get("width_m") and selection.get("height_m"):
            w, h = float(selection["width_m"]), float(selection["height_m"])
            if not math.isfinite(w+h) or min(w, h) <= 0:
                raise ValueError("矩形宽高必须为正数")
            x, y = xy[0]
            if selection.get("point") == "lefttop":
                poly = box(x, y-h, x+w, y)
            else:
                poly = box(x-w/2, y-h/2, x+w/2, y+h/2)
        elif len(xy) >= 2:
            a, b = xy[0], xy[1]
            poly = box(min(a[0],b[0]), min(a[1],b[1]), max(a[0],b[0]), max(a[1],b[1]))
        else:
            raise ValueError("矩形需要两个角点或中心及宽高")
    elif kind == "circle":
        r = float(selection.get("radius_m") or (math.dist(xy[0], xy[1]) if len(xy)>1 else 0))
        if not math.isfinite(r) or r <= 0:
            raise ValueError("圆形半径必须为正数")
        n = max(32, math.ceil(math.pi/math.acos(max(-1, 1-min(res/4/r, 1)))))
        if n > 100000:
            raise ValueError("圆形采样过大，请增大输出分辨率")
        x, y = xy[0]
        poly = Polygon([(x+r*math.cos(2*math.pi*i/n), y+r*math.sin(2*math.pi*i/n)) for i in range(n)])
    else:
        if len(xy) < 3:
            raise ValueError("多边形或曲线需要至少三个控制点")
        poly = Polygon(catmull_closed(xy, res/4) if kind == "curve" else xy)
    if poly.is_empty or not poly.is_valid:
        raise ValueError("选区无效，请调整控制点：" + explain_validity(poly))
    if poly.area < res*res:
        raise ValueError("选区小于一个输出像素，请减小分辨率或扩大选区")
    left, bottom, right, top = poly.bounds
    if max(right-left, top-bottom) > 100000:
        raise ValueError("单次选区最长边限制为 100 km，请分割下载")
    left, top = math.floor(left/res)*res, math.ceil(top/res)*res
    width, height = math.ceil((right-left)/res), math.ceil((top-bottom)/res)
    if width*height > 100000000 or max(width,height)>50000:
        raise ValueError("输出超过 1 亿像素或单边 50000 像素，请增大分辨率或分割任务")
    geographic = transform(inv.transform, poly)
    if geographic.bounds[2]-geographic.bounds[0] > 3:
        raise ValueError("选区经度范围过大，请分割下载")
    return Grid(selection, poly, geographic, crs, from_origin(left, top, res, res), width, height, res, points)


def tiles_for(grid, zoom):
    z = int(zoom)
    if not 0 <= z <= 22:
        raise ValueError("卫星层级应为 0–22")
    lat = grid.geographic.centroid.y
    margin = max(grid.resolution*2, 2*40075016.686*math.cos(math.radians(lat))/(256*2**z))
    fwd = Transformer.from_crs(4326, grid.crs, always_xy=True)
    inv = Transformer.from_crs(grid.crs, 4326, always_xy=True)
    expanded = grid.polygon.buffer(margin)
    bounds = transform(inv.transform, expanded).bounds
    a, b = mercantile.tile(bounds[0], bounds[3], z), mercantile.tile(bounds[2], bounds[1], z)
    if (abs(a.x-b.x)+1)*(abs(a.y-b.y)+1) > 100000:
        raise ValueError("瓦片候选超过 10 万张，请降低层级或缩小区域")
    result = []
    for tile in mercantile.tiles(*bounds, zooms=z):
        tb = mercantile.bounds(tile)
        if expanded.intersects(transform(fwd.transform, box(*tb))):
            result.append(tile)
    return result


def boundary_json(grid):
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"shape": grid.selection["shape"]}, "geometry": mapping(grid.geographic)}]}
