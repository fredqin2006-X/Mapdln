"""Inspect and validate local GeoTIFF elevation without network access.

Horizontal reprojection does not establish or change a vertical datum. Values
are converted to metres using the raster band's scale, offset and height unit.
"""
import math
from pathlib import Path

import numpy as np
import rasterio
from pyproj import CRS, Geod, Transformer
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds


def _local_path(path):
    text = str(path).strip()
    if not text or "://" in text or text.lower().startswith("/vsi"):
        raise ValueError("请选择本地 GeoTIFF 文件（.tif / .tiff）")
    result = Path(text).expanduser().resolve()
    if result.suffix.lower() not in (".tif", ".tiff"):
        raise ValueError("本地 DEM 首版仅支持 .tif / .tiff GeoTIFF")
    if not result.is_file():
        raise ValueError("DEM 文件不存在或无法读取，请重新选择文件")
    return result


def _fingerprint(path, associated_files=()):
    stat = path.stat()
    # PAM metadata, masks and world files can alter the interpretation without
    # changing the TIFF itself. Also notice a sidecar created after selection.
    candidates = {path.with_suffix(extension) for extension in (".tfw", ".tifw", ".wld", ".prj")}
    candidates.update(Path(str(path) + extension) for extension in (".aux.xml", ".msk", ".ovr"))
    candidates.update(Path(item) for item in associated_files)
    companions = {}
    for candidate in sorted(candidates):
        if candidate == path or not candidate.is_file():
            continue
        companion_stat = candidate.stat()
        companions[candidate.name] = {"size": companion_stat.st_size, "mtime_ns": companion_stat.st_mtime_ns}
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "companions": companions}


def _unit(value):
    text = str(value or "").strip().lower().replace("_", " ")
    if text in ("m", "meter", "meters", "metre", "metres", "米"):
        return "m", 1.0
    if text in ("ft", "foot", "feet", "international foot", "英尺"):
        return "ft", .3048
    if text in ("us survey foot", "us survey feet", "foot us", "us-ft"):
        return "ft", 1200 / 3937
    return None, None


def inspect_dem(path, unit="auto"):
    """Return display metadata; an unknown height unit is a visible state.

    Unlike snapshot_dem, this intentionally permits an unidentified height
    unit so the file picker can show its metadata and request a choice.
    """
    if unit not in ("auto", "m", "ft"):
        raise ValueError("DEM 高程单位应为 auto、m 或 ft")
    path = _local_path(path)
    before = _fingerprint(path)
    try:
        with rasterio.open(path) as src:
            associated_files = [str(Path(item).resolve()) for item in src.files if Path(item).resolve() != path]
            before = _fingerprint(path, associated_files)
            if src.driver != "GTiff" or src.count != 1:
                raise ValueError("请选择单波段 GeoTIFF 高程数据，不能使用 RGB 影像")
            if not src.crs:
                raise ValueError("DEM 缺少坐标参考系（CRS），请先为文件设置正确地理参考")
            if src.width < 1 or src.height < 1 or np.dtype(src.dtypes[0]).kind not in "iuf":
                raise ValueError("DEM 必须包含实数高程像元")
            transform = src.transform
            if (not all(math.isfinite(v) for v in transform[:6])
                    or abs(transform.a * transform.e - transform.b * transform.d) < 1e-30):
                raise ValueError("DEM 地理变换无效")
            tags = {**src.tags(), **src.tags(1)}
            declared = src.units[0] or next((tags[k] for k in
                ("UNITTYPE", "unit", "units", "UNIT", "UNITS") if tags.get(k)), "")
            detected, detected_factor = _unit(declared)
            selected, factor = (detected, detected_factor) if unit == "auto" else _unit(unit)
            scale, offset = float(src.scales[0]), float(src.offsets[0])
            if not math.isfinite(scale) or not math.isfinite(offset):
                raise ValueError("DEM 的比例系数或偏移值无效")
            crs = CRS.from_user_input(src.crs)
            vertical = next((c.name for c in crs.sub_crs_list if c.is_vertical), None)
            vertical = vertical or tags.get("VERTICAL_DATUM") or tags.get("vertical_datum") or "未知（未做垂直基准转换）"
            bounds = list(transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21))
            if not all(math.isfinite(v) for v in bounds):
                raise ValueError("DEM 覆盖范围无法转换为经纬度")
            lonlat = Transformer.from_crs(crs, 4326, always_xy=True)
            col, row = src.width / 2, src.height / 2
            center = (transform.a * col + transform.b * row + transform.c,
                      transform.d * col + transform.e * row + transform.f)
            adjacent_x = (center[0] + transform.a, center[1] + transform.d)
            adjacent_y = (center[0] + transform.b, center[1] + transform.e)
            geographic = [lonlat.transform(*p) for p in (center, adjacent_x, adjacent_y)]
            geod = Geod(ellps="WGS84")
            resolution_m = [abs(geod.inv(*geographic[0], *p)[2]) for p in geographic[1:]]
            if not all(math.isfinite(value) and value > 0 for value in resolution_m):
                raise ValueError("DEM 的像元分辨率或地理参考无效")
            nodata = src.nodata
            if nodata is not None and not math.isfinite(nodata):
                nodata = "NaN" if math.isnan(nodata) else str(nodata)
            result = {
                "path": str(path), "name": path.name, "fingerprint": before,
                "associated_files": associated_files,
                "width": src.width, "height": src.height, "crs": src.crs.to_string(),
                "bounds_wgs84": bounds,
                "resolution": [math.hypot(transform.a, transform.d), math.hypot(transform.b, transform.e)],
                "resolution_m": resolution_m, "unit": selected, "unit_detected": detected,
                "unit_declared": str(declared), "unit_required": selected is None,
                "unit_factor": factor, "scale": scale, "offset": offset,
                "vertical_datum": vertical, "nodata": nodata,
            }
    except rasterio.errors.RasterioError as exc:
        raise ValueError("无法读取 DEM，请检查 GeoTIFF 是否完整且未被占用") from exc
    if _fingerprint(path, associated_files) != before:
        raise ValueError("读取期间 DEM 文件已变化，请重新选择文件")
    return result


def snapshot_dem(path, unit="auto"):
    """Freeze the chosen file, band interpretation and identity for a task."""
    result = inspect_dem(path, unit)
    if result["unit_required"]:
        raise ValueError("DEM 未注明高程单位，请明确选择米（m）或英尺（ft）")
    result["unit_selection"] = unit
    return result


def check_dem_unchanged(snapshot):
    """Cheap guard for the beginning/end of a long-running local export."""
    path = _local_path(snapshot.get("path", ""))
    if _fingerprint(path, snapshot.get("associated_files", [])) != snapshot.get("fingerprint"):
        raise ValueError("DEM 文件在任务期间已变化，请重新选择文件并重新导出")


def aligned_dem_data(vrt, window, snapshot):
    """Read a reprojected block and interpret its raw band values in metres."""
    array = vrt.read(1, window=window, masked=True, out_dtype="float32")
    valid = ~np.ma.getmaskarray(array) & np.isfinite(array.data)
    with np.errstate(over="ignore", invalid="ignore"):
        values = ((array.data.astype("float64") * snapshot["scale"] + snapshot["offset"])
                  * snapshot["unit_factor"])
        values = values.astype("float32")
    valid &= np.isfinite(values)
    values[~valid] = np.nan
    return values, valid


def validate_dem(snapshot, grid, control=None):
    """Verify the frozen file and every selected output pixel before download."""
    # Lazy import keeps the inspection API usable by the GUI without cycles.
    from .engine import windows, mask_for

    if not isinstance(snapshot, dict) or not snapshot.get("path"):
        raise ValueError("请选择本地 DEM 文件")
    current = snapshot_dem(snapshot["path"], snapshot.get("unit_selection", snapshot.get("unit", "auto")))
    if current["fingerprint"] != snapshot.get("fingerprint"):
        raise ValueError("DEM 文件在选择后已变化，请重新选择后再开始任务")
    for key in ("unit_factor", "scale", "offset"):
        if current[key] != snapshot.get(key):
            raise ValueError("DEM 高程解释参数已变化，请重新选择文件")
    if control:
        control.checkpoint()
    valid_pixels = 0
    missing_pixels = 0
    with rasterio.open(current["path"]) as src, WarpedVRT(
        src, crs=grid.crs, transform=grid.transform, width=grid.width, height=grid.height,
        dtype="float32", nodata=np.nan, resampling=Resampling.bilinear, warp_mem_limit=64,
    ) as vrt:
        for window in windows(grid.width, grid.height):
            if control:
                control.checkpoint()
            selected = mask_for(grid, window) > 0
            if not selected.any():
                continue
            _, valid = aligned_dem_data(vrt, window, current)
            valid_pixels += int(np.count_nonzero(selected & valid))
            missing_pixels += int(np.count_nonzero(selected & ~valid))
    if missing_pixels:
        raise ValueError(f"本地 DEM 在选区内有 {missing_pixels:,} 个输出像素缺失或超出覆盖范围；请缩小选区或更换文件")
    if not valid_pixels:
        raise ValueError("选区内没有有效 DEM 高程像素")
    if _fingerprint(Path(current["path"]), current.get("associated_files", [])) != current["fingerprint"]:
        raise ValueError("预检查期间 DEM 文件已变化，请重新选择文件")
    return current
