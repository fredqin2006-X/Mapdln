"""Loopback capability server; no unrestricted proxy or embedded secrets."""
import json
import mimetypes
from pathlib import Path
import secrets
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlsplit, parse_qsl, urlencode
import requests
import mercantile
from .config import Ledger
from .engine import Control, fetch_tile, session_for, FABDEM_BASE


class PreviewServer:
    def __init__(self,assets,settings):
        self.assets=Path(assets)
        self.settings=settings
        self.cap=secrets.token_urlsafe(24)
        self.last_mapbox_error=""
        self.seen=[]
        self.sent=[]
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):
                pass

            def response(self,status,body,mime="application/json"):
                self.send_response(status)
                self.send_header("Content-Type",mime)
                self.send_header("Content-Length",str(len(body)))
                self.send_header("Cache-Control","no-store")
                self.send_header("X-Content-Type-Options","nosniff")
                self.end_headers()
                try:
                    self.wfile.write(body)
                    owner.sent.append((urlsplit(self.path).path.rsplit('/',1)[-1],status,len(body)))
                except (ConnectionError,OSError):
                    pass

            def do_GET(self):
                host=self.headers.get("Host","")
                if host != f"127.0.0.1:{owner.http.server_port}":
                    return self.response(403,b"{}")
                parsed=urlsplit(self.path)
                prefix="/"+owner.cap+"/"
                service_prefix="/_AMapService/"+owner.cap+"/"
                if parsed.path.startswith(service_prefix):
                    path="_AMapService/"+parsed.path[len(service_prefix):]
                elif parsed.path.startswith(prefix):
                    path=parsed.path[len(prefix):]
                else:
                    return self.response(404,b"{}")
                owner.seen.append(path)
                if path not in ("map.html","map.js","leaflet.js","leaflet.css","qwebchannel.js"):
                    origin=self.headers.get("Origin")
                    ref=self.headers.get("Referer","")
                    if origin and origin != owner.origin or not ref.startswith(owner.url):
                        return self.response(403,b"{}")
                try:
                    if path=="config":
                        c=owner.settings()
                        data={"amap_key":c.get("amap_key",""),"baidu_ak":c.get("baidu_ak",""),"mapbox_available":bool(c.get("mapbox_token")),
                              "amap_service":owner.origin+"/_AMapService/"+owner.cap}
                        return self.response(200,json.dumps(data).encode())
                    if path=="mapbox-status":
                        return self.response(200,json.dumps({"error":owner.last_mapbox_error}).encode())
                    if path=="sdk/amap.js":
                        c=owner.settings()
                        with session_for(c) as session:
                            r=session.get("https://webapi.amap.com/maps",params={"v":"2.0","key":c.get("amap_key","")},timeout=(10,20))
                            r.raise_for_status()
                        return self.response(200,r.content,"application/javascript")
                    if path.startswith("tiles/"):
                        z,x,y=path[6:].split("/")
                        z,x,y=int(z),int(x),int(y.split(".")[0])
                        if not 0<=z<=22 or not 0<=x<2**z or not 0<=y<2**z:
                            return self.response(400,b"{}")
                        c=owner.settings()
                        if not c.get("mapbox_token"):
                            return self.response(401,b"{}")
                        options={"proxy":c.get("proxy",""),"retries":0,"connect_timeout":10,"read_timeout":20}
                        control=Control(lambda *a,**k:None)
                        p=fetch_tile(mercantile.Tile(x,y,z),c["mapbox_token"],options,control,Ledger(),"preview")
                        owner.last_mapbox_error=""
                        return self.response(200,p.read_bytes(),"image/jpeg")
                    if path.startswith("_AMapService/"):
                        rest=path[len("_AMapService/"):]
                        # Official documented routes only; reject arbitrary hosts/paths.
                        if not rest.startswith(("v3/","v4/","v5/")) or ".." in rest or ":" in rest or "\\" in rest:
                            return self.response(403,b"{}")
                        c=owner.settings()
                        query=[(k,v) for k,v in parse_qsl(parsed.query,keep_blank_values=True) if k.lower() not in ("jscode","securityjscode")]
                        query.append(("jscode",c.get("amap_secret","")))
                        target=("https://webapi.amap.com/" if rest.startswith("v4/map/styles") else "https://restapi.amap.com/")+rest
                        with session_for(c) as session:
                            r=session.get(target,params=query,timeout=(10,20),allow_redirects=False)
                        return self.response(r.status_code,r.content,r.headers.get("Content-Type","application/json"))
                    if path=="qwebchannel.js":
                        from PySide6.QtCore import QFile
                        f=QFile(":/qtwebchannel/qwebchannel.js")
                        f.open(QFile.OpenModeFlag.ReadOnly)
                        return self.response(200,bytes(f.readAll()),"application/javascript")
                    if path in ("map.html","map.js","leaflet.js","leaflet.css"):
                        mime="text/html; charset=utf-8" if path.endswith("html") else "application/javascript" if path.endswith("js") else "text/css"
                        return self.response(200,(owner.assets/path).read_bytes(),mime)
                    return self.response(404,b"{}")
                except Exception as exc:
                    from .config import redact,SECRETS
                    owner.last_mapbox_error=redact(str(exc),[owner.settings().get(k,"") for k in SECRETS])
                    return self.response(502,json.dumps({"error":owner.last_mapbox_error},ensure_ascii=False).encode())
        try:
            self.http=ThreadingHTTPServer(("127.0.0.1",8765),Handler)
        except OSError:
            self.http=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        self.http.daemon_threads=True
        self.origin=f"http://127.0.0.1:{self.http.server_port}"
        self.url=self.origin+"/"+self.cap+"/"
        threading.Thread(target=self.http.serve_forever,daemon=True).start()

    def close(self):
        self.http.shutdown()
        self.http.server_close()
