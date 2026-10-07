"""Per-user configuration. Credentials are never serialized unencrypted."""
import base64
import ctypes
import json
import os
import re
import threading
from pathlib import Path
from datetime import datetime, timezone

SECRETS = {"mapbox_token", "amap_key", "amap_secret", "baidu_ak"}


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
        val = data.pop(name + "_dpapi", None)
        if val:
            data[name] = protect(val, decrypt=True)
    return data


def save(data):
    disk = {k: v for k, v in data.items() if k not in SECRETS}
    for name in SECRETS:
        if data.get(name):
            disk[name + "_dpapi"] = protect(data[name])
    atomic_json(home() / "config.json", disk)


def redact(text, secrets=()):
    text = str(text)
    text = re.sub(r"(https?|socks5h?)://[^/@\s]+:[^/@\s]+@", r"\1://[已遮蔽]@", text)
    for value in secrets:
        if value:
            text = text.replace(str(value), "[已遮蔽]")
    text = re.sub(r"(?:pk|sk)\.[A-Za-z0-9_.-]+", "[已遮蔽Token]", text)
    return re.sub(r"(?i)((?:access_token|jscode|securityJsCode|key|ak)=)[^&\s\"']+", r"\1[已遮蔽]", text)


class Ledger:
    """One immutable file per actual request, safe across GUI/worker processes."""
    def __init__(self):
        self.root = home() / "requests"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def record(self, kind, task="", retry=False):
        import uuid
        item = {"time": datetime.now(timezone.utc).isoformat(), "kind": kind, "retry": retry, "task": task}
        atomic_json(self.root / (uuid.uuid4().hex + ".json"), item)

    def counts(self, since=""):
        result = {"download": 0, "preview": 0, "verify": 0, "retry": 0}
        for path in self.root.glob("*.json"):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                if item["time"] < since:
                    continue
                kind = item["kind"]
                result[kind] = result.get(kind, 0) + 1
                if item.get("retry"):
                    result["retry"] += 1
            except (ValueError, OSError, KeyError):
                continue
        return result
