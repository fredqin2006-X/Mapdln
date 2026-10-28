"""Independent integration checks for source identity and geographic output."""
import io
from unittest.mock import patch

import numpy as np
from PIL import Image
import pytest
import rasterio
from pyproj import Transformer

from mapdln.engine import run_task, tile_path, Control, fetch_tile, estimate
from mapdln.config import Ledger
from mapdln.geometry import make_grid, tiles_for
from mapdln.sources import validate_source


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MAPDLN_HOME", str(tmp_path / "profile"))


def selection():
    return {"shape": "rectangle", "points": [[121.58901, 31.2592]], "width_m": 700, "height_m": 600}


def source():
    return validate_source({"id": "review", "name": "Review raster", "protocol": "xyz",
        "url_template": "https://example.invalid/{z}/{x}/{y}?token={token}", "tile_size": 256,
        "format": "PNG", "min_zoom": 0, "max_zoom": 20, "datum": "WGS84", "crs": "EPSG:3857"})


def seed(grid, spec=None, credentials=None, alpha=255):
    for tile in tiles_for(grid, 16):
        path = tile_path(tile, spec, credentials)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Distinct geographic tile colors detect X/Y swaps and north/south flips.
        rgba = np.zeros((256, 256, 4), dtype="uint8")
        rgba[:, :, 0] = tile.x % 251
        rgba[:, :, 1] = tile.y % 251
        rgba[:, :, 2] = 180
        rgba[:, :, 3] = alpha
        image = Image.fromarray(rgba)
        image.save(path, "PNG") if spec else image.convert("RGB").save(path, "JPEG", quality=100, subsampling=0)


def test_source_export_geographic_pixels_match_independent_mercator_grid(tmp_path):
    spec = source()
    credentials = {"token": "review-secret"}
    region = selection()
    grid = make_grid(region, 10)
    seed(grid, spec, credentials)
    out = tmp_path / "geographic"
    with patch("requests.Session.get", side_effect=AssertionError("all tiles cached")):
        run_task({"selection": region, "resolution": 10, "zoom": 16, "source": spec,
                  "elevation": "flat", "output": str(out)}, credentials, lambda *a, **k: None)
    to_mercator = Transformer.from_crs(grid.crs, 3857, always_xy=True)
    origin = 20037508.342789244
    tile_width = 2 * origin / (2**16)
    samples = 0
    with rasterio.open(out / "satellite.png") as image, rasterio.open(out / "mask.png") as mask:
        pixels, valid = image.read(), mask.read(1)
        for row in range(3, grid.height - 3, 6):
            for col in range(3, grid.width - 3, 6):
                if not valid[row, col]:
                    continue
                east, north = grid.transform @ (col+.5, row+.5)
                x, y = to_mercator.transform(east, north)
                tx, ty = (x + origin) / tile_width, (origin - y) / tile_width
                # Stay beyond the interpolation footprint at tile boundaries.
                if min(tx % 1, 1-tx % 1, ty % 1, 1-ty % 1) < .08:
                    continue
                assert pixels[0, row, col] == int(tx) % 251
                assert pixels[1, row, col] == int(ty) % 251
                assert pixels[3, row, col] == 255
                samples += 1
    assert samples > 20


def test_semitransparent_source_preserves_alpha_without_changing_selection_mask(tmp_path):
    spec = source()
    credentials = {"token": "review-secret"}
    region = selection()
    grid = make_grid(region, 10)
    seed(grid, spec, credentials, alpha=128)
    out = tmp_path / "alpha"
    with patch("requests.Session.get", side_effect=AssertionError("all tiles cached")):
        run_task({"selection": region, "resolution": 10, "zoom": 16, "source": spec,
                  "elevation": "flat", "output": str(out)}, credentials, lambda *a, **k: None)
    with rasterio.open(out / "satellite.png") as image, rasterio.open(out / "mask.png") as mask:
        pixels, valid = image.read(), mask.read(1)
        assert (pixels[3][valid == 255] == 128).all()
        assert (pixels[3][valid == 0] == 0).all()


def test_fetch_source_credentials_isolate_real_cache_contents(tmp_path):
    spec = source()
    tile = tiles_for(make_grid(selection(), 10), 16)[0]
    control = Control(lambda *a, **k: None)
    class Response:
        status_code = 200
        headers = {}
        def __init__(self, color):
            stream = io.BytesIO()
            Image.new("RGB", (256, 256), color).save(stream, "PNG")
            self.content = stream.getvalue()
        def raise_for_status(self):
            pass
    with patch("requests.Session.get", side_effect=[Response((255,0,0)), Response((0,0,255))]) as request:
        first = fetch_tile(tile, {"token": "first-account"}, {}, control, Ledger(), source=spec)
        second = fetch_tile(tile, {"token": "second-account"}, {}, control, Ledger(), source=spec)
        repeated = fetch_tile(tile, {"token": "first-account"}, {}, control, Ledger(), source=spec)
    assert request.call_count == 2 and first == repeated and first != second
    with Image.open(first) as a, Image.open(second) as b:
        assert a.getpixel((0,0)) == (255,0,0) and b.getpixel((0,0)) == (0,0,255)


def test_incompatible_elevation_only_resume_rejects_previous_imagery(tmp_path):
    region = selection()
    grid = make_grid(region, 10)
    seed(grid)
    out = tmp_path / "resume"
    task = {"selection": region, "resolution": 10, "zoom": 16, "elevation": "real", "output": str(out)}
    with patch("requests.Session.get", side_effect=AssertionError("all tiles cached")), \
         patch("mapdln.engine.terrain_sources", side_effect=RuntimeError("test terrain unavailable")):
        with pytest.raises(RuntimeError, match="真实高程失败"):
            run_task(task, "pk.review", lambda *a, **k: None)
    assert (out / "satellite.png").exists() and not (out / "metadata.json").exists()
    with pytest.raises(ValueError):
        run_task({**task, "imagery": False, "elevation": "flat"}, {}, lambda *a, **k: None)
    with pytest.raises(ValueError):
        run_task({**task, "selection": {**region, "width_m": 800}, "elevation": "flat"}, "pk.review", lambda *a, **k: None)
    # Explicitly switching only high-elevation handling preserves the same imagery.
    with patch("requests.Session.get", side_effect=AssertionError("recovery reuses cache")):
        run_task({**task, "elevation": "flat"}, "pk.review", lambda *a, **k: None)
    assert (out / "metadata.json").exists() and not (out / ".mapdln-task.json").exists()


def test_high_zoom_custom_source_plans_and_exports_small_area(tmp_path):
    spec = {**source(), "max_zoom": 24}
    credentials = {"token": "review-secret"}
    region = {**selection(), "width_m": 1.6, "height_m": 1.2}
    grid = make_grid(region, .01)
    plan = estimate(region, .01, None, source=spec, credentials=credentials)
    assert plan["zoom"] == 24 and 0 < plan["tiles"] < 30
    for tile in tiles_for(grid, 24):
        path = tile_path(tile, spec, credentials)
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (256, 256), (30, 90, 150, 255)).save(path, "PNG")
    out = tmp_path / "zoom24"
    with patch("requests.Session.get", side_effect=AssertionError("high-zoom tiles cached")):
        run_task({"selection": region, "resolution": .01, "zoom": None, "source": spec,
                  "elevation": "flat", "output": str(out)}, credentials, lambda *a, **k: None)
    with rasterio.open(out / "satellite.png") as image, rasterio.open(out / "mask.png") as mask:
        assert image.transform == grid.transform and image.shape == (grid.height, grid.width)
        assert (image.read(4)[mask.read(1) > 0] == 255).all()
