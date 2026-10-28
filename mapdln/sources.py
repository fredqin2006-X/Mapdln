"""Declarative raster sources. Templates contain placeholders, never credentials."""
import base64
import copy
import hashlib
import json
import math
import re
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
import xml.etree.ElementTree as ET


_SECRET_NAMES = {"token", "access_token", "tk", "mk", "key", "ak", "api_key", "apikey", "api-key", "secret", "signature", "sig", "authorization", "password"}
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z_0-9]*)\}")
_WORLD = 40075016.68557849
_ORIGIN = _WORLD / 2
_MAPBOX = {
    "id": "mapbox", "name": "Mapbox · 全球卫星", "protocol": "xyz",
    "url_template": "https://api.mapbox.com/v4/mapbox.satellite/{z}/{x}/{y}.jpg90?access_token={token}",
    "tile_size": 256, "format": "JPEG", "min_zoom": 0, "max_zoom": 22,
    "crs": "EPSG:3857", "datum": "WGS84", "attribution": "© Mapbox © Maxar © OpenStreetMap",
    "cache_seconds": 43200, "subdomains": [], "matrix_ids": {},
    "requests_per_second": 8, "max_concurrency": 8,
}
_TIANDITU = {
    "id": "tianditu", "name": "天地图 · 卫星影像", "protocol": "wmts",
    "url_template": "https://t{s}.tianditu.gov.cn/img_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=img&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk={token}",
    "tile_size": 256, "format": "auto", "min_zoom": 1, "max_zoom": 18,
    "crs": "EPSG:3857", "datum": "CGCS2000", "attribution": "© 天地图",
    "cache_seconds": 43200, "subdomains": [str(i) for i in range(8)],
    "matrix_ids": {str(i): str(i) for i in range(1, 19)},
    "wmts_layer": "img", "wmts_matrix_set": "w",
}
_TENCENT = {
    "id": "tencent", "name": "腾讯 · 卫星影像（兼容）", "protocol": "tms",
    "url_template": "https://p{s}.map.gtimg.com/sateTiles/{z}/{x16}/{y16}/{x}_{y}.jpg",
    "tile_size": 256, "format": "auto", "min_zoom": 0, "max_zoom": 18,
    "crs": "EPSG:3857", "datum": "GCJ02", "attribution": "© 腾讯地图",
    "cache_seconds": 43200, "subdomains": ["0", "1", "2", "3"], "matrix_ids": {},
    "requests_per_second": 2, "max_concurrency": 2,
}
_JILIN = {
    "id": "jilin", "name": "吉林一号 · 卫星影像", "protocol": "xyz",
    "url_template": "https://api.jl1mall.com/getMap/{z}/{x}/{y}?tk={token}&mk={credential_1}&sch=wmts",
    "tile_size": 256, "format": "auto", "min_zoom": 0, "max_zoom": 18,
    "crs": "EPSG:3857", "datum": "WGS84", "attribution": "© 吉林一号 / 长光卫星",
    "cache_seconds": 43200, "subdomains": [], "matrix_ids": {},
    "requests_per_second": 2, "max_concurrency": 2,
}
BUILTIN_IDS = ("mapbox", "tianditu", "tencent", "jilin")
EDITABLE_BUILTINS = ("tencent", "jilin")


def provider_note(source_id):
    return {
        "mapbox": "使用自己的 Mapbox 公共 Access Token（pk.）。",
        "tianditu": "天地图影像不叠加文字注记。请使用具备地图服务权限的 Key。",
        "tencent": "腾讯卫星兼容适配，非官方批量下载 API。请使用有相应权限的服务地址；GCJ-02 近似校正不能用于测量级定位。",
        "jilin": "填写自己的访问 TK 和图层 MK。预设使用左上原点（sch=wmts）；企业地址可保留 pro 参数，服务层级与坐标以获配图层为准。",
    }.get(source_id, "支持标准 Web Mercator 的 XYZ、TMS 和 WMTS；GCJ-02 影像按输出网格近似校正，BD-09 影像暂不支持。")


def default_source():
    return copy.deepcopy(_MAPBOX)


def list_sources(settings):
    overrides = settings.get("provider_sources", {})
    if not isinstance(overrides, dict) or any(key not in EDITABLE_BUILTINS for key in overrides):
        raise ValueError("内置图源配置无效")
    result = []
    for base in (_MAPBOX, _TIANDITU, _TENCENT, _JILIN):
        source = {**copy.deepcopy(base), **copy.deepcopy(overrides.get(base['id'], {}))}
        source.update(id=base['id'], name=base['name'])
        result.append(validate_source(source))
    seen = set(BUILTIN_IDS)
    for item in settings.get("custom_sources", []):
        spec = validate_source(item)
        if spec["id"] in seen:
            raise ValueError("图源 ID 重复，请修改自定义图源")
        seen.add(spec["id"])
        result.append(spec)
    return result


def get_source(settings, source_id):
    for spec in list_sources(settings):
        if spec["id"] == source_id:
            return spec
    raise ValueError("找不到所选图源，请重新选择")


def credentials_for(settings, source_id):
    if source_id == "mapbox":
        return {"token": settings.get("mapbox_token", "")}
    if source_id == "tianditu":
        return {"token": settings.get("tianditu_key", "")}
    credentials = settings.get("source_credentials", {}).get(source_id, {})
    if not isinstance(credentials, dict):
        raise ValueError("图源凭据格式无效，请重新配置")
    return copy.deepcopy(credentials)


def split_credentials(url, headers=None):
    """Extract recognized URL secrets and all headers before persisting a source."""
    parts = urlsplit(str(url).strip())
    credentials = {}
    clean_headers = dict(headers or {})
    netloc = parts.netloc
    if parts.username is not None or parts.password is not None:
        from urllib.parse import unquote
        raw = unquote(parts.username or "") + ":" + unquote(parts.password or "")
        clean_headers.setdefault("Authorization", "Basic " + base64.b64encode(raw.encode()).decode())
        netloc = netloc.rsplit("@", 1)[-1]
    pairs = []
    values = {}
    reserved = set(_PLACEHOLDER.findall(str(url)))
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in _SECRET_NAMES and value and not _PLACEHOLDER.fullmatch(value):
            if value not in values:
                preferred = "credential_1" if key.lower() == "mk" else "token"
                name = preferred
                index = 1
                while name in reserved or name in credentials:
                    name = "credential_" + str(index)
                    index += 1
                reserved.add(name)
                values[value] = name
                credentials[name] = value
            value = "{" + values[value] + "}"
        pairs.append((key, value))
    if clean_headers:
        credentials["headers"] = clean_headers
    return urlunsplit((parts.scheme, netloc, parts.path, urlencode(pairs, safe="{}"), "")), credentials


def _number(value, field, minimum, maximum, integral=True):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"图源 {field} 无效") from None
    if not math.isfinite(number) or not minimum <= number <= maximum or (integral and number != int(number)):
        raise ValueError(f"图源 {field} 应在 {minimum}～{maximum} 之间")
    return int(number) if integral else number


def validate_source(spec):
    if not isinstance(spec, dict):
        raise ValueError("图源配置应为对象")
    result = {key: copy.deepcopy(spec[key]) for key in (
        "id", "name", "protocol", "url_template", "tile_size", "format", "min_zoom", "max_zoom",
        "crs", "datum", "attribution", "cache_seconds", "subdomains", "matrix_ids", "wmts_layer", "wmts_matrix_set", "requests_per_second", "max_concurrency",
    ) if key in spec}
    result["id"] = str(result.get("id", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", result["id"]):
        raise ValueError("图源 ID 只能包含字母、数字、下划线和连字符")
    result["name"] = str(result.get("name", "")).strip()
    if not result["name"] or len(result["name"]) > 120:
        raise ValueError("请填写 1～120 字的图源名称")
    protocol = str(result.get("protocol", "xyz")).lower()
    if protocol not in ("xyz", "tms", "wmts"):
        raise ValueError("仅支持 XYZ、TMS 和标准 Web Mercator WMTS")
    result["protocol"] = protocol
    crs = str(result.get("crs", "EPSG:3857")).upper()
    if crs not in ("EPSG:3857", "EPSG:900913"):
        raise ValueError("首版仅支持 EPSG:3857 标准 Web Mercator 图源")
    result["crs"] = "EPSG:3857"
    datum = str(result.get("datum", "WGS84")).upper().replace("-", "")
    if datum not in ("WGS84", "CGCS2000", "GCJ02"):
        raise ValueError("该图源坐标基准尚不支持导出；请选择 WGS84、CGCS2000 或 GCJ-02")
    result["datum"] = datum
    result["tile_size"] = _number(result.get("tile_size", 256), "瓦片尺寸", 256, 512)
    if result["tile_size"] not in (256, 512):
        raise ValueError("瓦片尺寸应为 256 或 512")
    fmt = str(result.get("format", "auto"))
    fmt = "auto" if fmt.lower() == "auto" else fmt.upper().replace("JPG", "JPEG")
    if fmt not in ("JPEG", "PNG", "WEBP", "auto"):
        raise ValueError("仅支持 JPEG / PNG / WebP 栅格瓦片")
    result["format"] = fmt
    result["min_zoom"] = _number(result.get("min_zoom", 0), "最小层级", 0, 24)
    result["max_zoom"] = _number(result.get("max_zoom", 18), "最大层级", result["min_zoom"], 24)
    result["cache_seconds"] = _number(result.get("cache_seconds", 43200), "缓存有效秒数", 1, 31536000)
    result["requests_per_second"] = _number(result.get("requests_per_second", 4), "每秒请求数", .1, 20, integral=False)
    result["max_concurrency"] = _number(result.get("max_concurrency", 4), "最大并发", 1, 8)
    result["attribution"] = str(result.get("attribution", ""))[:1000]
    domains = result.get("subdomains", [])
    if isinstance(domains, str):
        domains = [item.strip() for item in domains.split(",") if item.strip()]
    if not isinstance(domains, list) or len(domains) > 32 or any(not re.fullmatch(r"[A-Za-z0-9-]{1,40}", str(item)) for item in domains):
        raise ValueError("子域列表无效，请用逗号分隔，如 a,b,c")
    result["subdomains"] = [str(item) for item in domains]
    template = str(result.get("url_template", "")).strip()
    if len(template) > 8192 or any(ord(char) < 32 for char in template):
        raise ValueError("图源地址无效")
    placeholders = set(_PLACEHOLDER.findall(template))
    if any(name not in {"z", "x", "y", "x16", "y16", "s", "token"} and not re.fullmatch(r"credential_\d+", name) for name in placeholders):
        raise ValueError("图源模板包含不支持的占位符")
    remainder = _PLACEHOLDER.sub("", template)
    if "{" in remainder or "}" in remainder or not {"z", "x", "y"}.issubset(placeholders):
        raise ValueError("瓦片地址必须包含 {z}、{x}、{y} 占位符")
    if "s" in placeholders and not result["subdomains"]:
        raise ValueError("地址包含 {s}，请填写子域列表")
    probe = _PLACEHOLDER.sub("0", template)
    try:
        parts = urlsplit(probe)
        valid_address = parts.scheme in ("http", "https") and bool(parts.hostname) and parts.port != 0
    except ValueError:
        valid_address = False
    if not valid_address or parts.username is not None or parts.password is not None or parts.fragment:
        raise ValueError("图源地址必须为有效的 HTTP(S) 地址，凭据请单独配置")
    _, embedded = split_credentials(template)
    if embedded or spec.get("headers") or spec.get("token"):
        raise ValueError("图源地址含明文凭据，请使用 {token} 占位符并单独保存凭据")
    result["url_template"] = template
    matrices = result.get("matrix_ids", {})
    if not isinstance(matrices, dict):
        raise ValueError("WMTS 矩阵映射无效")
    result["matrix_ids"] = {str(key): str(value) for key, value in matrices.items()}
    if protocol == "wmts":
        for level in range(result["min_zoom"], result["max_zoom"] + 1):
            if str(level) not in result["matrix_ids"] or not result["matrix_ids"][str(level)]:
                raise ValueError("WMTS 缺少层级矩阵映射，请读取服务能力文档")
    return result


def secret_values(credentials):
    if isinstance(credentials, str):
        return [credentials] if credentials else []
    values = []
    if isinstance(credentials, dict):
        for value in credentials.values():
            values.extend(secret_values(value))
    elif isinstance(credentials, (list, tuple)):
        for value in credentials:
            values.extend(secret_values(value))
    return values


def build_request(spec, tile, credentials):
    spec = validate_source(spec)
    credentials = {"token": credentials} if isinstance(credentials, str) else (credentials or {})
    z, x, y = int(tile.z), int(tile.x), int(tile.y)
    if not spec["min_zoom"] <= z <= spec["max_zoom"] or not (0 <= x < 2**z and 0 <= y < 2**z):
        raise ValueError("瓦片超出图源层级或范围")
    if spec["protocol"] == "tms":
        y = 2**z - 1 - y
    values = {"z": spec["matrix_ids"].get(str(z), str(z)), "x": str(x), "y": str(y), "x16": str(x//16), "y16": str(y//16)}
    if spec["subdomains"]:
        values["s"] = spec["subdomains"][(x+y) % len(spec["subdomains"])]
    for name in _PLACEHOLDER.findall(spec["url_template"]):
        if name in values:
            continue
        value = credentials.get(name, "")
        if not isinstance(value, str) or not value:
            if spec['id'] == 'jilin':
                raise ValueError("吉林一号缺少凭据，请在图源管理中填写图层 MK 与访问 TK")
            raise ValueError(f"{spec['name']} 缺少凭据，请在图源管理中配置")
        values[name] = value
    if spec["id"] == "mapbox" and not credentials.get("token", "").startswith("pk."):
        raise ValueError("Mapbox 需要公共 Access Token（pk.）")
    url = _PLACEHOLDER.sub(lambda match: quote(values[match.group(1)], safe=""), spec["url_template"])
    headers = credentials.get("headers", {})
    if not isinstance(headers, dict):
        raise ValueError("图源请求头必须是键值对象")
    for name, value in headers.items():
        if not isinstance(name, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~A-Za-z0-9-]+", name) or not isinstance(value, str) or "\r" in value or "\n" in value:
            raise ValueError("图源请求头无效")
        if name.lower() in ("host", "content-length", "connection", "transfer-encoding"):
            raise ValueError("不允许覆盖 HTTP 连接控制请求头")
    return url, dict(headers)


def cache_key(spec, credentials=None):
    canonical = validate_source(spec)
    # Account isolation without exposing tokens or header values in the cache path.
    payload = {"source": canonical, "credentials": credentials or {}}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32]


def public_metadata(spec):
    source = validate_source(spec)
    # Endpoints may contain account/project identifiers. Export only useful provenance.
    return {key: source[key] for key in ("id", "name", "protocol", "tile_size", "format", "min_zoom", "max_zoom", "crs", "datum", "attribution", "matrix_ids", "wmts_layer", "wmts_matrix_set") if key in source}


def _local_name(tag):
    return tag.rsplit("}", 1)[-1]


def _children(node, name):
    return [child for child in node if _local_name(child.tag) == name]


def _child(node, name):
    return next(iter(_children(node, name)), None)


def _text(node, name, default=""):
    child = _child(node, name)
    return child.text.strip() if child is not None and child.text else default


def parse_wmts(xml, service_url):
    """Import only geometrically verified global Web Mercator XYZ matrix sets."""
    if isinstance(xml, str):
        xml = xml.encode("utf-8")
    if not isinstance(xml, bytes) or len(xml) > 4 * 1024 * 1024 or b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise ValueError("WMTS 能力文档过大或包含不支持的 XML 声明")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise ValueError("无法解析 WMTS 能力文档") from None
    contents = _child(root, "Contents")
    if contents is None:
        raise ValueError("服务未返回 WMTS Contents，请检查服务地址和凭据")
    clean_url, _ = split_credentials(service_url)
    parts = urlsplit(clean_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("WMTS 服务地址必须为 HTTP(S)")
    matrix_sets = {}
    for matrix_set in _children(contents, "TileMatrixSet"):
        crs = _text(matrix_set, "SupportedCRS")
        if not re.search(r"(?:[:/]|^)(?:3857|900913)$", crs):
            continue
        levels, sizes = {}, set()
        invalid = False
        for matrix in _children(matrix_set, "TileMatrix"):
            try:
                width, height = int(_text(matrix, "MatrixWidth")), int(_text(matrix, "MatrixHeight"))
                tile_w, tile_h = int(_text(matrix, "TileWidth")), int(_text(matrix, "TileHeight"))
                scale = float(_text(matrix, "ScaleDenominator"))
                origin = [float(value) for value in _text(matrix, "TopLeftCorner").split()]
                zoom = int(round(math.log2(width)))
                expected = _WORLD / (tile_w * 2**zoom) / 0.00028
                if (width != height or width != 2**zoom or not 0 <= zoom <= 24 or tile_w != tile_h or tile_w not in (256, 512)
                        or len(origin) != 2 or not math.isclose(origin[0], -_ORIGIN, abs_tol=.02)
                        or not math.isclose(origin[1], _ORIGIN, abs_tol=.02) or not math.isclose(scale, expected, rel_tol=1e-7)):
                    invalid = True
                    break
                identifier = _text(matrix, "Identifier")
                if not identifier or str(zoom) in levels:
                    invalid = True
                    break
                levels[str(zoom)] = identifier
                sizes.add(tile_w)
            except (ValueError, OverflowError, ZeroDivisionError):
                invalid = True
                break
        if invalid or not levels or len(sizes) != 1:
            continue
        lo, hi = min(map(int, levels)), max(map(int, levels))
        if set(map(int, levels)) != set(range(lo, hi+1)):
            continue
        matrix_sets[_text(matrix_set, "Identifier")] = (levels, sizes.pop(), lo, hi)
    # Prefer the GetTile KVP endpoint advertised by the service, while retaining
    # query credentials from the address supplied by the user.
    endpoint = parts
    operations = _child(root, "OperationsMetadata")
    if operations is not None:
        for operation in _children(operations, "Operation"):
            if operation.get("name") != "GetTile":
                continue
            for node in operation.iter():
                if _local_name(node.tag) == "Get":
                    href = next((value for key, value in node.attrib.items() if _local_name(key) == "href"), "")
                    parsed = urlsplit(href)
                    if parsed.scheme in ("http", "https") and parsed.netloc == parts.netloc:
                        endpoint = parsed
                        break
    controlled = {"service", "request", "version", "layer", "style", "tilematrixset", "tilematrix", "tilerow", "tilecol", "format"}
    base_query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key.lower() not in controlled]
    supplied_keys = {key.lower() for key, _ in base_query}
    base_query.extend((key, value) for key, value in parse_qsl(endpoint.query, keep_blank_values=True)
                      if key.lower() not in controlled and key.lower() not in supplied_keys)
    result = []
    for layer in _children(contents, "Layer"):
        layer_id = _text(layer, "Identifier")
        title = _text(layer, "Title", layer_id)
        formats = [_text_node.text.strip() for _text_node in _children(layer, "Format") if _text_node.text]
        fmt = next((value for value in formats if value.lower() == "image/png"), None) or next((value for value in formats if value.lower() in ("image/jpeg", "image/jpg", "image/webp", "tiles")), None)
        if not fmt:
            continue
        styles = _children(layer, "Style")
        style = next((node for node in styles if node.get("isDefault", "").lower() in ("true", "1")), styles[0] if styles else None)
        style_id = _text(style, "Identifier") if style is not None else "default"
        for link in _children(layer, "TileMatrixSetLink"):
            matrix_id = _text(link, "TileMatrixSet")
            if matrix_id not in matrix_sets:
                continue
            levels, tile_size, lo, hi = matrix_sets[matrix_id]
            # Full-world limits are safe; restricted extents need a separate
            # coverage planner and must not be silently treated as global.
            limits = _child(link, "TileMatrixSetLimits")
            if limits is not None:
                allowed = {}
                reverse = {identifier: zoom for zoom, identifier in levels.items()}
                try:
                    for limit in _children(limits, "TileMatrixLimits"):
                        identifier = _text(limit, "TileMatrix")
                        zoom = reverse[identifier]
                        edge = 2**int(zoom)-1
                        if (int(_text(limit, "MinTileRow")), int(_text(limit, "MinTileCol")),
                                int(_text(limit, "MaxTileRow")), int(_text(limit, "MaxTileCol"))) != (0, 0, edge, edge):
                            raise ValueError("Restricted coverage")
                        allowed[zoom] = identifier
                    if not allowed:
                        continue
                    lo, hi = min(map(int, allowed)), max(map(int, allowed))
                    if set(map(int, allowed)) != set(range(lo, hi+1)):
                        continue
                    levels = allowed
                except (ValueError, KeyError):
                    continue
            query = base_query + [("SERVICE", "WMTS"), ("REQUEST", "GetTile"), ("VERSION", "1.0.0"), ("LAYER", layer_id), ("STYLE", style_id), ("TILEMATRIXSET", matrix_id), ("FORMAT", fmt), ("TILEMATRIX", "{z}"), ("TILEROW", "{y}"), ("TILECOL", "{x}")]
            template = urlunsplit((endpoint.scheme, endpoint.netloc, endpoint.path, urlencode(query, safe="{}"), ""))
            identifier = "wmts-" + hashlib.sha256((template + layer_id + matrix_id).encode()).hexdigest()[:16]
            result.append(validate_source({"id": identifier, "name": title + " · " + matrix_id, "protocol": "wmts", "url_template": template,
                "tile_size": tile_size, "format": {"image/png":"PNG", "image/webp":"WEBP", "tiles":"auto"}.get(fmt.lower(), "JPEG"), "min_zoom": lo, "max_zoom": hi,
                "crs": "EPSG:3857", "datum": "WGS84", "matrix_ids": levels, "wmts_layer": layer_id, "wmts_matrix_set": matrix_id}))
    if not result:
        raise ValueError("未找到受支持的 WMTS 图层；首版仅支持无范围限制、连续层级的全球 EPSG:3857 标准瓦片网格")
    return result
