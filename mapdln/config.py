"""Per-user configuration. Credentials are never serialized unencrypted."""
import base64
import ctypes
import json
import os
import re
import threading
from pathlib import Path
from datetime import datetime, timezone

SECRETS = {"mapbox_token", "amap_key", "amap_secret", "baidu_ak", "tianditu_key", "source_credentials"}
STRUCTURED_SECRETS = {"source_credentials"}


def home():
    return Path(os.environ.get("MAPDLN_HOME") or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Mapdln")


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def protect(value, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("凭据保护需要 Windows DPAPI；不允许降级为明文保存")
    raw = base64.b64decode(value) if decrypt else value.encode("utf-8")
    buf = ctypes.create_string_buffer(raw)
    src = Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    dst = Blob()
    api = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not api(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst)):
        raise ctypes.WinError()
    try:
        result = ctypes.string_at(dst.data, dst.size)
        return result.decode("utf-8") if decrypt else base64.b64encode(result).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(dst.data)


def load():
    path = home() / "config.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    for name in SECRETS:
        data.pop(name, None)
        val = data.pop(name + "_dpapi", None)
        if val:
            value = protect(val, decrypt=True)
            data[name] = json.loads(value) if name in STRUCTURED_SECRETS else value
            if name in STRUCTURED_SECRETS and not isinstance(data[name], dict):
                raise ValueError("图源凭据库格式无效，请重新配置")
    return data


def save(data):
    # Defensively split credentials from imported source definitions as well as
    # the GUI's normal workflow. No URL key or custom request header is plaintext.
    from .sources import split_credentials, validate_source, EDITABLE_BUILTINS, list_sources
    import copy
    data = copy.deepcopy(data)
    source_credentials = data.get("source_credentials", {})
    if not isinstance(source_credentials, dict):
        raise ValueError("图源凭据库格式无效")
    providers = data.get("provider_sources", {})
    if not isinstance(providers, dict) or any(key not in EDITABLE_BUILTINS for key in providers):
        raise ValueError("内置图源配置无效")
    for provider_id, source in providers.items():
        if not isinstance(source, dict) or source.get('id') != provider_id:
            raise ValueError("内置图源 ID 不匹配")
    for source in list(data.get("custom_sources", [])) + list(providers.values()):
        url, extracted = split_credentials(source.get("url_template", ""), source.pop("headers", None))
        source["url_template"] = url
        if source.get("token"):
            extracted["token"] = source.pop("token")
        if extracted:
            source_credentials.setdefault(source["id"], {}).update(extracted)
        normalized = validate_source(source)
        source.clear()
        source.update(normalized)
    list_sources(data)
    if source_credentials:
        data["source_credentials"] = source_credentials
    disk = {k: v for k, v in data.items() if k not in SECRETS}
    for name in SECRETS:
        if data.get(name):
            value = json.dumps(data[name], ensure_ascii=False) if name in STRUCTURED_SECRETS else data[name]
            disk[name + "_dpapi"] = protect(value)
    atomic_json(home() / "config.json", disk)


def redact(text, secrets=()):
    from urllib.parse import quote, quote_plus
    from .sources import secret_values
    text = str(text)
    text = re.sub(r"(https?|socks5h?)://[^/@\s]+:[^/@\s]+@", r"\1://[已遮蔽]@", text)
    for value in sorted(set(secret_values(secrets)), key=len, reverse=True):
        if value:
            for spelling in {value, quote(value, safe=""), quote_plus(value)}:
                text = text.replace(spelling, "[已遮蔽]")
    text = re.sub(r"(?:pk|sk)\.[A-Za-z0-9_.-]+", "[已遮蔽Token]", text)
    keys = r"(?:access_token|token|tk|mk|jscode|securityJsCode|key|ak|api[_-]?key|secret|password|signature|sig|x-amz-(?:credential|signature|security-token))"
    text = re.sub(r"(?i)(\b" + keys + r"=)[^&\s\"']+", r"\1[已遮蔽]", text)
    text = re.sub(r'''(?i)(["'](?:authorization|proxy-authorization|cookie|set-cookie|''' + keys + r''')["']\s*:\s*["'])[^"']*''', r"\1[已遮蔽]", text)
    return re.sub(r"(?i)(\b(?:Authorization|Proxy-Authorization)\s*:\s*)[^\r\n]+", r"\1[已遮蔽]", text)


class Ledger:
    """One immutable file per actual request, safe across GUI/worker processes."""
    def __init__(self):
        self.root = home() / "requests"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def record(self, kind, task="", retry=False, source="mapbox", account=""):
        import uuid
        item = {"time": datetime.now(timezone.utc).isoformat(), "kind": kind, "retry": retry, "task": task,
                "source": source, "account": account}
        atomic_json(self.root / (uuid.uuid4().hex + ".json"), item)

    def counts(self, since="", source=None, account=None):
        result = {"download": 0, "preview": 0, "verify": 0, "retry": 0}
        for path in self.root.glob("*.json"):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                if item["time"] < since:
                    continue
                if source is not None and item.get("source", "mapbox") != source:
                    continue
                if account is not None and item.get("account", "") != account:
                    continue
                kind = item["kind"]
                result[kind] = result.get(kind, 0) + 1
                if item.get("retry"):
                    result["retry"] += 1
            except (ValueError, OSError, KeyError):
                continue
        return result
