import copy
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from mapdln.config import Ledger, atomic_json, home, load, redact, save
from mapdln.sources import (build_request, cache_key, credentials_for, default_source,
                            get_source, list_sources, parse_wmts, public_metadata,
                            secret_values, split_credentials, validate_source)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MAPDLN_HOME", str(tmp_path / "profile"))


def custom(**changes):
    return {"id": "survey", "name": "测绘影像", "protocol": "xyz",
            "url_template": "https://tiles.example.test/{z}/{x}/{y}.png",
            "format": "PNG", "tile_size": 512, "min_zoom": 0, "max_zoom": 19,
            "crs": "EPSG:3857", "datum": "WGS84", **changes}


def capabilities(tile_size=256, origin="-20037508.342789244 20037508.342789244", width_multiplier=1, restricted=False, crs="urn:ogc:def:crs:EPSG::3857", zooms=(0, 1, 2)):
    matrices = []
    for z in zooms:
        scale = 40075016.68557849 / (tile_size * 2**z) / .00028
        matrices.append(f"""<TileMatrix><ows:Identifier>Google:{z}</ows:Identifier>
          <ScaleDenominator>{scale}</ScaleDenominator><TopLeftCorner>{origin}</TopLeftCorner>
          <TileWidth>{tile_size}</TileWidth><TileHeight>{tile_size}</TileHeight>
          <MatrixWidth>{2**z * width_multiplier}</MatrixWidth><MatrixHeight>{2**z}</MatrixHeight></TileMatrix>""")
    limits = "<TileMatrixSetLimits><TileMatrixLimits /></TileMatrixSetLimits>" if restricted else ""
    return f"""<Capabilities xmlns="http://www.opengis.net/wmts/1.0" xmlns:ows="http://www.opengis.net/ows/1.1">
      <Contents><Layer><ows:Identifier>imagery</ows:Identifier><ows:Title>卫星影像</ows:Title>
        <Style isDefault="true"><ows:Identifier>natural</ows:Identifier></Style><Format>image/png</Format>
        <TileMatrixSetLink><TileMatrixSet>web</TileMatrixSet>{limits}</TileMatrixSetLink></Layer>
        <TileMatrixSet><ows:Identifier>web</ows:Identifier><ows:SupportedCRS>{crs}</ows:SupportedCRS>
          {''.join(matrices)}</TileMatrixSet></Contents></Capabilities>"""


def test_builtins_are_independent_and_require_only_their_credentials():
    source = default_source()
    source["name"] = "mutated"
    assert default_source()["name"] != "mutated"
    settings = {"mapbox_token": "pk.mock", "tianditu_key": "test-tdt"}
    sources = list_sources(settings)
    assert [s["id"] for s in sources] == ["mapbox", "tianditu"]
    tdt = get_source(settings, "tianditu")
    url, headers = build_request(tdt, SimpleNamespace(z=3, x=2, y=4), credentials_for(settings, "tianditu"))
    assert urlsplit(url).hostname.startswith("t")
    query = parse_qs(urlsplit(url).query)
    assert query["TILEMATRIX"] == ["3"] and query["TILEROW"] == ["4"]
    assert query["tk"] == ["test-tdt"] and query["LAYER"] == ["img"]
    assert not headers and "pk.mock" not in url
    with pytest.raises(ValueError, match="缺少凭据"):
        build_request(tdt, SimpleNamespace(z=3, x=2, y=4), {})


def test_xyz_tms_and_subdomains_keep_geographic_xyz_input():
    tile = SimpleNamespace(z=3, x=2, y=1)
    template = "https://{s}.example.test/{z}/{x}/{y}.png?key={token}"
    xyz = custom(url_template=template, subdomains=["a", "b"])
    url, headers = build_request(xyz, tile, {"token": "a&b=secret", "headers": {"Authorization": "Bearer mock"}})
    assert urlsplit(url).path == "/3/2/1.png"
    assert parse_qs(urlsplit(url).query)["key"] == ["a&b=secret"]
    assert headers == {"Authorization": "Bearer mock"}
    tms_url, _ = build_request({**xyz, "protocol": "tms"}, tile, {"token": "mock"})
    assert urlsplit(tms_url).path == "/3/2/6.png"
    assert tile.y == 1


@pytest.mark.parametrize("changes", [
    {"crs": "EPSG:4326"}, {"datum": "GCJ-02"}, {"datum": "BD09"},
    {"tile_size": 300}, {"min_zoom": 20, "max_zoom": 19}, {"protocol": "javascript"},
    {"url_template": "file:///tmp/{z}/{x}/{y}"},
    {"url_template": "https://user:pass@example.test/{z}/{x}/{y}"},
    {"url_template": "https://example.test/{z}/{x}/{y}?tk=plain-secret"},
    {"url_template": "https://example.test/{z}/{x}/{y}/{token.__class__}"},
    {"url_template": "https://example.test/{z}/{x}/{y}/{unknown}"},
    {"url_template": "https://{s}.example.test/{z}/{x}/{y}"},
    {"requests_per_second": 0}, {"max_concurrency": 9},
])
def test_invalid_sources_fail_explicitly(changes):
    with pytest.raises(ValueError):
        validate_source(custom(**changes))


def test_credentials_are_split_and_encrypted_including_headers():
    original = "https://u:p@example.test/{z}/{x}/{y}.png?tk=first-secret&api_key=second-secret&style=natural"
    template, credentials = split_credentials(original, {"X-API-Key": "header-secret"})
    assert credentials["token"] == "first-secret"
    assert credentials["credential_1"] == "second-secret"
    assert credentials["headers"]["Authorization"].startswith("Basic ")
    assert not any(value in template for value in ("first-secret", "second-secret", "u:p"))
    source = validate_source(custom(url_template=template))
    url, headers = build_request(source, SimpleNamespace(z=2, x=1, y=2), credentials)
    assert parse_qs(urlsplit(url).query)["tk"] == ["first-secret"]
    assert headers["X-API-Key"] == "header-secret"
    settings = {"custom_sources": [source], "source_credentials": {"survey": credentials}, "tianditu_key": "tdt-secret"}
    save(settings)
    assert load() == settings
    disk = (home() / "config.json").read_text(encoding="utf-8")
    for value in ("first-secret", "second-secret", "header-secret", "tdt-secret", "Authorization"):
        assert value not in disk
    # Imported raw definitions receive the same protection even without the dialog.
    save({"custom_sources": [custom(url_template=original, headers={"X-API-Key": "header-secret"})]})
    restored = load()
    assert restored["source_credentials"]["survey"]["token"] == "first-secret"
    assert "first-secret" not in (home() / "config.json").read_text(encoding="utf-8")


def test_config_source_storage_is_allowlisted():
    save({"custom_sources": [custom(unrecognized_private_field="private-secret")]})
    assert "unrecognized_private_field" not in load()["custom_sources"][0]
    assert "private-secret" not in (home() / "config.json").read_text(encoding="utf-8")


def test_cache_namespace_and_export_provenance_do_not_expose_secrets():
    source = validate_source(custom(url_template="https://example.test/account/{z}/{x}/{y}?token={token}"))
    first = cache_key(source, {"token": "first-secret"})
    assert first != cache_key(source, {"token": "second-secret"})
    assert first != cache_key({**source, "tile_size": 256}, {"token": "first-secret"})
    assert first != cache_key({**source, "id": "survey2"}, {"token": "first-secret"})
    assert "secret" not in first
    metadata = json.dumps(public_metadata(source))
    assert "url_template" not in metadata and "example.test" not in metadata and "token" not in metadata


@pytest.mark.parametrize("tile_size", [256, 512])
def test_wmts_import_preserves_matrix_identifiers_and_pixel_size(tile_size):
    spec, = parse_wmts(capabilities(tile_size), "https://example.test/wmts?service=WMTS&request=GetCapabilities&tk={token}")
    assert spec["matrix_ids"] == {"0": "Google:0", "1": "Google:1", "2": "Google:2"}
    assert spec["tile_size"] == tile_size
    url, _ = build_request(spec, SimpleNamespace(z=2, x=3, y=1), {"token": "test-secret"})
    query = parse_qs(urlsplit(url).query)
    assert query["TILEMATRIX"] == ["Google:2"]
    assert query["TILECOL"] == ["3"] and query["TILEROW"] == ["1"]
    assert query["STYLE"] == ["natural"] and query["tk"] == ["test-secret"]
    assert query["REQUEST"] == ["GetTile"]


@pytest.mark.parametrize("changes", [
    {"origin": "0 0"}, {"width_multiplier": 2}, {"restricted": True},
    {"crs": "EPSG:4326"}, {"zooms": (0, 2)},
])
def test_wmts_rejects_nonstandard_or_restricted_grid(changes):
    with pytest.raises(ValueError, match="未找到受支持"):
        parse_wmts(capabilities(**changes), "https://example.test/wmts")


def test_wmts_rejects_unsafe_xml_and_does_not_forward_secrets_to_foreign_host():
    with pytest.raises(ValueError):
        parse_wmts('<!DOCTYPE x [<!ENTITY x "y">]><Capabilities />', "https://example.test/wmts")
    xml = capabilities().replace("<Contents>", '<ows:OperationsMetadata><ows:Operation name="GetTile"><ows:DCP><ows:HTTP><ows:Get xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="https://other.example.test/steal" /></ows:HTTP></ows:DCP></ows:Operation></ows:OperationsMetadata><Contents>')
    source, = parse_wmts(xml, "https://example.test/wmts?key={token}")
    assert urlsplit(source["url_template"]).hostname == "example.test"


def test_wmts_accepts_explicit_full_world_limits_and_keeps_endpoint_parameters():
    limits = "<TileMatrixSetLimits>" + "".join(f"<TileMatrixLimits><TileMatrix>Google:{z}</TileMatrix><MinTileRow>0</MinTileRow><MinTileCol>0</MinTileCol><MaxTileRow>{2**z-1}</MaxTileRow><MaxTileCol>{2**z-1}</MaxTileCol></TileMatrixLimits>" for z in (1, 2)) + "</TileMatrixSetLimits>"
    xml = capabilities().replace("</TileMatrixSetLink>", limits + "</TileMatrixSetLink>")
    xml = xml.replace("<Contents>", '<ows:OperationsMetadata><ows:Operation name="GetTile"><ows:DCP><ows:HTTP><ows:Get xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="https://example.test/tiles?map=survey" /></ows:HTTP></ows:DCP></ows:Operation></ows:OperationsMetadata><Contents>')
    source, = parse_wmts(xml, "https://example.test/capabilities?key={token}")
    assert source["min_zoom"] == 1 and source["max_zoom"] == 2
    assert source["matrix_ids"] == {"1": "Google:1", "2": "Google:2"}
    query = parse_qs(urlsplit(source["url_template"]).query)
    assert query["map"] == ["survey"] and query["key"] == ["{token}"]


def test_header_injection_and_out_of_range_tile_rejected():
    for header in ({"X-Test": "ok\r\nInjected: yes"}, {"Host": "other.example.test"}):
        with pytest.raises(ValueError):
            build_request(custom(), SimpleNamespace(z=2, x=1, y=1), {"headers": header})
    with pytest.raises(ValueError, match="超出"):
        build_request(custom(), SimpleNamespace(z=2, x=4, y=1), {})


def test_provider_ledger_separates_counts_and_migrates_legacy_records():
    ledger = Ledger()
    atomic_json(ledger.root / "old.json", {"time": "2020", "kind": "download", "retry": False})
    ledger.record("download", source="tianditu")
    ledger.record("preview", source="survey", account="account-hash")
    ledger.record("verify", retry=True, source="tianditu")
    assert ledger.counts(source="mapbox")["download"] == 1
    assert ledger.counts(source="tianditu") == {"download": 1, "preview": 0, "verify": 1, "retry": 1}
    assert ledger.counts(source="survey", account="account-hash")["preview"] == 1
    assert ledger.counts(source="survey", account="different")["preview"] == 0


def test_redaction_covers_url_encoded_values_headers_and_provider_keys():
    credentials = {"token": "a&b=secret", "headers": {"Authorization": "Bearer other-secret"}}
    text = 'url?tk=a%26b%3Dsecret&api_key=third-secret {"Authorization": "Bearer other-secret"}'
    clean = redact(text, credentials)
    assert all(value not in clean for value in ("a%26b%3Dsecret", "other-secret", "third-secret"))
    assert "last-secret" not in redact("Authorization: Bearer last-secret")
    assert set(secret_values(credentials)) == {"a&b=secret", "Bearer other-secret"}
