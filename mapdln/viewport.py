"""Automatic startup location and provider-independent map view state."""
import math

import requests

from .geometry import to_wgs, from_wgs


DEFAULT_VIEW = {"center": [121.58901, 31.2592], "zoom": 15}


def normalize_view(value):
    """Validate a viewport and store its centre in WGS84."""
    try:
        if not isinstance(value, dict):
            return None
        lon, lat = value["center"]
        zoom = value["zoom"]
        if any(isinstance(n, bool) or not isinstance(n, (int, float))
               or not math.isfinite(n) for n in (lon, lat, zoom)):
            return None
        if not -90 <= lat <= 90 or not 0 <= zoom <= 30:
            return None
        if not -180 <= lon <= 180:
            lon = (lon + 180) % 360 - 180
        lon, lat = to_wgs([lon, lat], value.get("system", "WGS84"))
        return {"center": [lon, max(-85.05112878, min(85.05112878, lat))], "zoom": zoom}
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def provider_views(view):
    view = normalize_view(view) or DEFAULT_VIEW
    return {"centers": {system: from_wgs(view["center"], system)
                        for system in ("WGS84", "GCJ02", "BD09")}, "zoom": view["zoom"]}


def locate():
    """One bounded HTTPS request for approximate WGS84 IP location, without keys.

    Bypass application/environment proxies so a preview proxy does not become
    the user's startup location. No credentials or saved coordinates are sent.
    """
    with requests.Session() as session:
        session.trust_env = False
        response = session.get("https://ipwho.is/", params={"fields": "success,longitude,latitude"},
                               timeout=(3, 5))
        response.raise_for_status()
        data = response.json()
    if data.get("success") is not True:
        return None
    lon, lat = data.get("longitude"), data.get("latitude")
    if not isinstance(lon, (int, float)) or not -180 <= lon <= 180:
        return None
    return normalize_view({"center": [lon, lat], "zoom": 12})
