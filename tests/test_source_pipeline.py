"""Offline integration coverage using real localhost HTTP and raster exports."""
import io
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import mercantile
import numpy as np
from PIL import Image
import pytest
import rasterio

from mapdln.config import Ledger
from mapdln.engine import Control, fetch_tile, run_task, tile_path
from mapdln.geometry import make_grid, tiles_for
from mapdln.local_dem import snapshot_dem
from mapdln.sources import validate_source


SELECTION = {"shape": "circle", "points": [[121.58901, 31.2592]], "radius_m": 60}


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("MAPDLN_HOME", str(tmp_path / "profile"))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


@pytest.fixture
def serve():
    servers = []
    def start(responder):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                requests.append((self.path, dict(self.headers), time.monotonic()))
                status, body, headers = responder(self.path, self.headers)
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        thread.start()
        servers.append((server, thread))
        return f"http://127.0.0.1:{server.server_port}", requests
    yield start
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(2)


def image_bytes(size=256, fmt="PNG", alpha=255, color=(40, 120, 200)):
    stream = io.BytesIO()
    image = Image.new("RGBA" if fmt == "PNG" else "RGB", (size, size),
                      (*color, alpha) if fmt == "PNG" else color)
    image.save(stream, fmt)
    return stream.getvalue()


def source(url, protocol="xyz", size=256, fmt="PNG", **overrides):
    return validate_source({"id": "local-test", "name": "离线验证图源", "protocol": protocol,
        "url_template": url + "/{z}/{x}/{y}?token={token}", "tile_size": size,
        "format": fmt, "min_zoom": 0, "max_zoom": 20, "crs": "EPSG:3857", "datum": "WGS84",
        "attribution": "Synthetic test imagery", "requests_per_second": 20, **overrides})


def task(out, spec, **overrides):
    return {"selection": SELECTION, "resolution": 5, "zoom": 15, "source": spec,
        "imagery": True, "elevation": "flat", "output": str(out),
        "options": {"retries": 0, "concurrency": 2}, **overrides}


@pytest.mark.parametrize("protocol,size,fmt,alpha", [
    ("xyz", 256, "JPEG", 255), ("tms", 256, "JPEG", 255),
    ("xyz", 512, "PNG", 128), ("tms", 512, "PNG", 128),
])
def test_http_tiles_export_on_aligned_grid_and_preserve_alpha(tmp_path, serve, protocol, size, fmt, alpha):
    token = "private+/=token?marker"
    payload = image_bytes(size, fmt, alpha)
    def respond(path, headers):
        if parse_qs(urlsplit(path).query).get("token") != [token] or headers.get("X-Test-Key") != "private-header":
            return 401, b"bad credentials", {}
        return 200, payload, {"Content-Type": "image/png" if fmt == "PNG" else "image/jpeg"}
    url, seen = serve(respond)
    spec = source(url, protocol, size, fmt)
    credentials = {"token": token, "headers": {"X-Test-Key": "private-header"}}
    out = tmp_path / "export"
    run_task(task(out, spec), credentials, lambda *a, **k: None)
    grid = make_grid(SELECTION, 5)
    expected = {(t.z, t.x, 2**t.z - 1 - t.y if protocol == "tms" else t.y) for t in tiles_for(grid, 15)}
    received = {tuple(map(int, urlsplit(path).path.strip("/").split("/"))) for path, _, _ in seen}
    assert received == expected
    with rasterio.open(out / "satellite.png") as image, rasterio.open(out / "mask.png") as mask, \
         rasterio.open(out / "dem.tif") as dem:
        assert image.transform == mask.transform == dem.transform == grid.transform
        assert image.crs == dem.crs == grid.crs
        selected = mask.read(1) > 0
        rgba = image.read()
        assert np.all(rgba[3, ~selected] == 0)
        assert np.all(rgba[3, selected] == alpha)
        assert np.median(rgba[2, selected]) == pytest.approx(200, abs=2)
        assert np.all(dem.read(1)[selected] == 0)
    metadata = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["imagery_source"]["id"] == "local-test"
    assert metadata["local_source_requests"] == len(seen)
    assert metadata["local_mapbox_requests"] == 0
    exported_text = (out / "metadata.json").read_text(encoding="utf-8") + (out / "task.log").read_text(encoding="utf-8")
    assert token not in exported_text and "private-header" not in exported_text
    assert "url_template" not in exported_text and url not in exported_text


def test_source_and_account_cache_isolation(tmp_path, serve):
    url, seen = serve(lambda path, headers: (200, image_bytes(), {"Content-Type": "image/png"}))
    spec = source(url)
    credentials = {"token": "account-one"}
    run_task(task(tmp_path / "first", spec), credentials, lambda *a, **k: None)
    count = len(seen)
    assert count > 0
    run_task(task(tmp_path / "reused", spec), credentials, lambda *a, **k: None)
    assert len(seen) == count
    changed = {**spec, "url_template": spec["url_template"] + "&dataset=second"}
    run_task(task(tmp_path / "second-source", changed), credentials, lambda *a, **k: None)
    assert len(seen) == count * 2
    run_task(task(tmp_path / "second-account", spec), {"token": "account-two"}, lambda *a, **k: None)
    assert len(seen) == count * 3
    tile = tiles_for(make_grid(SELECTION, 5), 15)[0]
    paths = [tile_path(tile, item, creds) for item, creds in
        ((spec, credentials), (changed, credentials), (spec, {"token": "account-two"}))]
    assert len(set(paths)) == 3
    assert all("account-" not in str(path) for path in paths)


def test_wmts_uses_matrix_identifiers_instead_of_numeric_zoom(tmp_path, serve):
    url, seen = serve(lambda path, headers: (200, image_bytes(), {}))
    spec = source(url, protocol="wmts", min_zoom=15, max_zoom=15,
        matrix_ids={"15": "Mercator:level-fifteen"},
        url_template=url + "/wmts?matrix={z}&column={x}&row={y}&token={token}")
    run_task(task(tmp_path / "wmts", spec), {"token": "wmts-key"}, lambda *a, **k: None)
    assert seen
    query_values = [parse_qs(urlsplit(path).query) for path, _, _ in seen]
    assert all(query["matrix"] == ["Mercator:level-fifteen"] for query in query_values)
    expected = {(tile.x, tile.y) for tile in tiles_for(make_grid(SELECTION, 5), 15)}
    assert {(int(query["column"][0]), int(query["row"][0])) for query in query_values} == expected


def test_http_200_error_document_never_becomes_cached_tile(tmp_path, serve):
    url, seen = serve(lambda path, headers: (200, b'<html>quota exceeded</html>', {"Content-Type": "text/html"}))
    spec = source(url)
    tile = mercantile.Tile(1, 1, 2)
    credentials = {"token": "html-key"}
    with pytest.raises(RuntimeError, match="图片响应失败"):
        fetch_tile(tile, credentials, {"retries": 0}, Control(lambda *a, **k: None), Ledger(), source=spec)
    assert len(seen) == 1
    assert not tile_path(tile, spec, credentials).exists()


@pytest.mark.parametrize("status,expected_error", [(401, ValueError), (403, ValueError), (404, RuntimeError)])
def test_nonretryable_http_errors_stop_without_fallback(serve, status, expected_error):
    url, seen = serve(lambda path, headers: (status, b"provider failure", {}))
    spec = source(url)
    control = Control(lambda *a, **k: None)
    with pytest.raises(expected_error, match=str(status)) as error:
        fetch_tile(mercantile.Tile(1, 1, 2), {"token": "private-error-token"}, {"retries": 2}, control, Ledger(), source=spec)
    assert len(seen) == 1 and control.requests == 1
    assert "private-error-token" not in str(error.value)


def test_rate_limit_retries_with_retry_after_and_accounts_requests(serve):
    attempts = []
    def respond(path, headers):
        attempts.append(path)
        return (429, b"rate limited", {"Retry-After": "1"}) if len(attempts) == 1 else (200, image_bytes(), {})
    url, seen = serve(respond)
    spec = source(url)
    control = Control(lambda *a, **k: None)
    path = fetch_tile(mercantile.Tile(1, 1, 2), {"token": "quota-key"}, {"retries": 1}, control, Ledger(), source=spec)
    assert path.exists() and control.requests == 2
    assert seen[1][2] - seen[0][2] >= .9
    assert Ledger().counts(source="local-test")["retry"] == 1


def make_dem(path, grid, value, *, unit="m", scale=1, offset=0, hole=False):
    values = np.full((grid.height, grid.width), value, dtype="float32")
    if hole:
        values[grid.height // 2, grid.width // 2] = np.nan
    with rasterio.open(path, "w", driver="GTiff", width=grid.width, height=grid.height, count=1,
                       dtype="float32", crs=grid.crs, transform=grid.transform, nodata=np.nan) as dst:
        dst.write(values, 1)
        dst.scales = (scale,)
        dst.offsets = (offset,)
        dst.update_tags(1, UNITTYPE=unit)
    return snapshot_dem(path)


@pytest.mark.parametrize("value,unit,scale,offset,expected", [
    (0, "m", 1, 0, 0), (-9999, "m", 1, 0, -9999), (-11000, "m", 1, 0, -11000),
    (100, "feet", 2, -50, 45.72),
])
def test_dem_only_exports_metres_and_nodata_without_any_network(tmp_path, value, unit, scale, offset, expected):
    grid = make_grid(SELECTION, 5)
    snapshot = make_dem(tmp_path / "本地高程.tif", grid, value, unit=unit, scale=scale, offset=offset)
    out = tmp_path / "高程成果"
    job = task(out, {}, imagery=False, elevation="local", dem=snapshot)
    with patch("requests.Session.get", side_effect=AssertionError("DEM-only must be entirely offline")):
        run_task(job, {}, lambda *a, **k: None)
    assert not (out / "satellite.png").exists()
    with rasterio.open(out / "dem.tif") as dem, rasterio.open(out / "mask.png") as mask, rasterio.open(out / "height.png") as height:
        selected = mask.read(1) > 0
        assert np.allclose(dem.read(1)[selected], expected, atol=1e-4)
        assert np.isnan(dem.nodata)
        assert np.isnan(dem.read(1)[~selected]).all()
        assert height.transform == grid.transform
        assert not height.read(1).any()
    text = (out / "metadata.json").read_text(encoding="utf-8")
    meta = json.loads(text)
    assert meta["height_encoding"]["min_m"] == pytest.approx(expected, abs=1e-4)
    assert meta["local_source_requests"] == 0 and meta["imagery_source"] is None
    assert str(tmp_path) not in text


def test_invalid_local_dem_preflight_prevents_all_imagery_requests(tmp_path, serve):
    url, seen = serve(lambda path, headers: (200, image_bytes(), {}))
    grid = make_grid(SELECTION, 5)
    snapshot = make_dem(tmp_path / "holes.tif", grid, 50, hole=True)
    with pytest.raises(ValueError, match="缺失或超出覆盖"):
        run_task(task(tmp_path / "failed", source(url), elevation="local", dem=snapshot),
                 {"token": "not-billed"}, lambda *a, **k: None)
    assert not seen
    assert not (tmp_path / "failed" / "metadata.json").exists()


def test_dem_input_must_not_be_overwritten_by_output(tmp_path):
    grid = make_grid(SELECTION, 5)
    source_path = tmp_path / "dem.tif"
    snapshot = make_dem(source_path, grid, 42)
    before = source_path.read_bytes()
    with pytest.raises(ValueError):
        run_task(task(tmp_path, {}, imagery=False, elevation="local", dem=snapshot), {}, lambda *a, **k: None)
    assert source_path.read_bytes() == before
    assert not (tmp_path / "metadata.json").exists()


def test_dem_change_after_preflight_cannot_produce_completed_metadata(tmp_path):
    grid = make_grid(SELECTION, 5)
    snapshot = make_dem(tmp_path / "mutable.tif", grid, 42)
    out = tmp_path / "changed-output"
    changed = []
    def emit(kind, **fields):
        if kind == "log" and "掩膜" in fields.get("message", "") and not changed:
            source_path = tmp_path / "mutable.tif"
            os.utime(source_path, ns=(source_path.stat().st_atime_ns, source_path.stat().st_mtime_ns + 1000000000))
            changed.append(True)
    with pytest.raises(ValueError, match="已变化"):
        run_task(task(out, {}, imagery=False, elevation="local", dem=snapshot), {}, emit)
    assert changed and not (out / "metadata.json").exists()
