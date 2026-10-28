import io
import json
import os
from unittest.mock import patch

import numpy as np
import pytest
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT

from mapdln.geometry import make_grid
from mapdln.local_dem import inspect_dem, snapshot_dem, validate_dem, aligned_dem_data, check_dem_unchanged


@pytest.fixture
def grid():
    return make_grid({"shape": "rectangle", "points": [[121.58901, 31.2592]],
                      "width_m": 200, "height_m": 160}, 10)


def write_dem(path, grid, values=None, *, unit="m", nodata=None, scale=1, offset=0, count=1, crs=True):
    if values is None:
        values = np.full((grid.height, grid.width), 10, dtype="float32")
    with rasterio.open(path, "w", driver="GTiff", width=grid.width, height=grid.height,
                       count=count, dtype="float32", crs=grid.crs if crs else None,
                       transform=grid.transform, nodata=nodata) as dst:
        for band in range(1, count + 1):
            dst.write(values, band)
        if unit:
            dst.update_tags(1, UNITTYPE=unit)
        dst.scales = (scale,) * count
        dst.offsets = (offset,) * count
    return path


def test_inspection_and_explicit_unknown_units(tmp_path, grid):
    path = write_dem(tmp_path / "中文高程.tif", grid, unit=None)
    info = inspect_dem(path)
    assert info["unit_required"] and info["unit"] is None
    assert info["resolution"] == [10, 10]
    assert info["resolution_m"] == pytest.approx([10, 10], rel=.002)
    assert info["bounds_wgs84"][0] < 121.58901 < info["bounds_wgs84"][2]
    assert "未知" in info["vertical_datum"]
    with pytest.raises(ValueError, match="高程单位"):
        snapshot_dem(path)
    assert snapshot_dem(path, "m")["unit_factor"] == 1


@pytest.mark.parametrize("value", [0, -9999, -11000, 10000])
def test_valid_zero_and_extreme_heights_are_not_nodata(tmp_path, grid, value):
    path = write_dem(tmp_path / "elevation.tif", grid,
                     np.full((grid.height, grid.width), value, dtype="float32"))
    snapshot = snapshot_dem(path)
    with patch("requests.Session.get", side_effect=AssertionError("DEM validation must be offline")):
        assert validate_dem(snapshot, grid)["fingerprint"] == snapshot["fingerprint"]


def test_scale_offset_and_feet_are_applied_after_warp(tmp_path, grid):
    path = write_dem(tmp_path / "feet.tif", grid, unit="feet", scale=2, offset=-5)
    snapshot = snapshot_dem(path)
    validate_dem(snapshot, grid)
    with rasterio.open(path) as src, WarpedVRT(src, crs=grid.crs, transform=grid.transform,
        width=grid.width, height=grid.height, dtype="float32", nodata=np.nan,
        resampling=Resampling.bilinear) as vrt:
        values, valid = aligned_dem_data(vrt, None, snapshot)
    assert valid.all()
    assert values == pytest.approx(np.full(values.shape, (10 * 2 - 5) * .3048), abs=1e-6)


def test_holes_and_partial_coverage_fail_before_download(tmp_path, grid):
    values = np.full((grid.height, grid.width), 20, dtype="float32")
    values[grid.height // 2, grid.width // 2] = -32768
    path = write_dem(tmp_path / "hole.tif", grid, values, nodata=-32768)
    with pytest.raises(ValueError, match="缺失或超出覆盖"):
        validate_dem(snapshot_dem(path), grid)
    path = write_dem(tmp_path / "small.tif", grid)
    larger = make_grid({"shape": "rectangle", "points": [[121.58901, 31.2592]],
                        "width_m": 400, "height_m": 320}, 10)
    with pytest.raises(ValueError, match="缺失或超出覆盖"):
        validate_dem(snapshot_dem(path), larger)


def test_reprojection_from_geographic_dem(tmp_path, grid):
    from rasterio.transform import from_bounds
    west, south, east, north = grid.geographic.bounds
    path = tmp_path / "geographic.tif"
    with rasterio.open(path, "w", driver="GTiff", width=40, height=40, count=1,
                       dtype="float32", crs="EPSG:4326",
                       transform=from_bounds(west-.002, south-.002, east+.002, north+.002, 40, 40)) as dst:
        dst.write(np.full((40, 40), 123, dtype="float32"), 1)
        dst.update_tags(1, UNITTYPE="metres")
    assert validate_dem(snapshot_dem(path), grid)["crs"] == "EPSG:4326"


def test_changed_file_and_changed_interpretation_are_rejected(tmp_path, grid):
    path = write_dem(tmp_path / "changed.tif", grid)
    snapshot = snapshot_dem(path)
    with pytest.raises(ValueError, match="参数已变化"):
        validate_dem({**snapshot, "scale": 2}, grid)
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1000000000))
    with pytest.raises(ValueError, match="文件在选择后已变化"):
        validate_dem(snapshot, grid)


def test_sidecar_creation_and_modification_invalidate_snapshot(tmp_path, grid):
    path = write_dem(tmp_path / "with-sidecar.tif", grid)
    snapshot = snapshot_dem(path)
    sidecar = path.with_suffix(".tfw")
    sidecar.write_text("10\n0\n0\n-10\n0\n0\n", encoding="ascii")
    with pytest.raises(ValueError, match="文件在任务期间已变化"):
        check_dem_unchanged(snapshot)
    updated = snapshot_dem(path)
    sidecar.write_text("20\n0\n0\n-20\n0\n0\n", encoding="ascii")
    os.utime(sidecar, ns=(sidecar.stat().st_atime_ns, sidecar.stat().st_mtime_ns + 1000000000))
    with pytest.raises(ValueError, match="文件在任务期间已变化"):
        check_dem_unchanged(updated)


def test_invalid_raster_shapes_and_remote_paths(tmp_path, grid):
    with pytest.raises(ValueError, match="本地 GeoTIFF"):
        inspect_dem("https://example.invalid/remote.tif")
    with pytest.raises(ValueError, match="单波段"):
        inspect_dem(write_dem(tmp_path / "rgb.tif", grid, count=3))
    with pytest.raises(ValueError, match="缺少坐标"):
        inspect_dem(write_dem(tmp_path / "no-crs.tif", grid, crs=False))


def test_validation_obeys_cancellation(tmp_path, grid):
    from mapdln.engine import Control, Cancelled
    control = Control(lambda *args, **kwargs: None)
    control.command("cancel")
    with pytest.raises(Cancelled):
        validate_dem(snapshot_dem(write_dem(tmp_path / "cancel.tif", grid)), grid, control)


def test_cli_dem_only_needs_no_saved_account(tmp_path, grid, monkeypatch):
    import main
    path = write_dem(tmp_path / "命令行.tif", grid)
    monkeypatch.setattr("sys.argv", ["main.py", "--cli", "--lon", "121.58901", "--lat", "31.2592",
        "--dem", str(path), "--elevation-only", "--zoom", "auto", "--output", str(tmp_path)])
    with patch("mapdln.config.load", side_effect=AssertionError("local task must not load online secrets")), \
         patch("mapdln.engine.run_task") as run:
        assert main.cli() == 0
    task, credentials = run.call_args.args[:2]
    assert not task["imagery"] and task["elevation"] == "local" and task["zoom"] is None
    assert credentials == {} and task["dem"]["unit"] == "m"


def test_worker_masks_nested_source_credentials(monkeypatch):
    from mapdln import worker
    message = {"task": {"budget": 0}, "token": {"token": "private-abc", "headers": {"Authorization": "Bearer secret-def"}}}
    stdin, stdout = io.StringIO(json.dumps(message) + "\n"), io.StringIO()
    monkeypatch.setattr("sys.stdin", stdin)
    monkeypatch.setattr("sys.stdout", stdout)
    with patch("mapdln.worker.run_task", side_effect=RuntimeError("private-abc Bearer secret-def")):
        assert worker.main() == 1
    assert "private-abc" not in stdout.getvalue() and "secret-def" not in stdout.getvalue()


@pytest.mark.parametrize("message", ["not json", "[]", '{"task":null}', '{"task":{"budget":"bad"}}', '{"task":{"budget":Infinity}}'])
def test_worker_rejects_malformed_input_with_structured_error(monkeypatch, message):
    from mapdln import worker
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(message + "\n"))
    monkeypatch.setattr("sys.stdout", stdout)
    assert worker.main() == 1
    assert json.loads(stdout.getvalue())["type"] == "error"


@pytest.mark.parametrize("content", ["invalid json", "[]"])
def test_cli_reports_invalid_selection_as_usage_error(tmp_path, monkeypatch, content, capsys):
    import main
    path = tmp_path / "selection.json"
    path.write_text(content, encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["main.py", "--cli", "--selection", str(path), "--elevation-only"])
    with pytest.raises(SystemExit) as exit_info:
        main.cli()
    assert exit_info.value.code == 2
    assert "选区 JSON" in capsys.readouterr().err
