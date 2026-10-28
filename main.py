"""Double-click starts GUI; --cli uses the same engine without GUI."""
import os
import sys


def prepare_stdio():
    """Windowed EXE workers still use the redirected Win32 pipe handles."""
    if os.name != "nt" or all(getattr(sys,k) is not None for k in ("stdin","stdout","stderr")):
        return
    import ctypes
    import msvcrt
    kernel=ctypes.windll.kernel32
    kernel.GetStdHandle.restype=ctypes.c_void_p
    if "--worker" not in sys.argv:
        kernel.AttachConsole(ctypes.c_ulong(-1).value)
    for name,code,mode,flag in (("stdin",-10,"r",os.O_RDONLY),("stdout",-11,"w",os.O_WRONLY),("stderr",-12,"w",os.O_WRONLY)):
        if getattr(sys,name) is not None:
            continue
        handle=kernel.GetStdHandle(ctypes.c_ulong(code).value)
        if handle and handle != ctypes.c_void_p(-1).value:
            fd=msvcrt.open_osfhandle(handle,flag|os.O_BINARY)
            setattr(sys,name,os.fdopen(fd,mode,encoding="utf-8",buffering=1))


def cli():
    import argparse
    import json
    from pathlib import Path
    from mapdln.config import load,redact
    from mapdln.engine import run_task,Control
    from mapdln.sources import default_source, get_source, credentials_for, secret_values
    from mapdln.local_dem import snapshot_dem
    def zoom_value(value):
        if str(value).lower() == "auto":
            return None
        try:
            zoom = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("影像层级应为 auto 或整数") from None
        if not 0 <= zoom <= 30:
            raise argparse.ArgumentTypeError("影像层级应在 0–30 之间")
        return zoom
    p=argparse.ArgumentParser(description="Mapdln GUI / CLI 地图下载器")
    p.add_argument("--cli",action="store_true",help="使用命令行，无参数启动 GUI")
    p.add_argument("--lon",type=float);p.add_argument("--lat",type=float)
    p.add_argument("-w","--width",type=float,default=200);p.add_argument("-g","--height",type=float,default=200)
    p.add_argument("--shape",choices=["rectangle","circle","polygon","curve"],default="rectangle")
    p.add_argument("--radius",type=float,default=100);p.add_argument("--selection",help="选区 JSON 文件，points+system+shape")
    p.add_argument("-z","--zoom",type=zoom_value,default=None,help="影像层级：auto（默认）或整数");p.add_argument("--resolution",type=float,default=1)
    p.add_argument("--source",default="mapbox",help="图源管理中保存的图源 ID，内置 mapbox / tianditu")
    p.add_argument("--elevation",choices=["flat","real","local"],default="real")
    p.add_argument("--dem",help="本地单波段 GeoTIFF；指定后使用 local 高程模式")
    p.add_argument("--dem-unit",choices=["auto","m","ft"],default="auto",help="高程单位；文件未注明时需明确选择 m 或 ft")
    p.add_argument("--elevation-only",action="store_true",help="仅输出高程，不请求影像、不需要在线图源凭据")
    p.add_argument("-m","--mode",choices=["full","tile"],help="兼容旧参数：tile 使用 0 米高程")
    p.add_argument("-p","--point",choices=["center","lefttop"],default="center")
    p.add_argument("-n","--name",default="mapdln-export");p.add_argument("--output",default=".")
    p.add_argument("-b","--bitdeep",type=int,choices=[8,16],default=16);p.add_argument("-s","--smooth",type=float,default=0)
    p.add_argument("--proxy",default="");p.add_argument("--budget",type=int,default=0)
    args=p.parse_args()
    if args.budget < 0:p.error("请求预算不能为负数；0 表示不设任务预算")
    if args.selection:
        try:
            selection=json.loads(Path(args.selection).read_text(encoding="utf-8"))
        except (OSError,ValueError):
            p.error("无法读取选区 JSON，请检查文件路径和 JSON 语法")
        if not isinstance(selection,dict):p.error("选区 JSON 必须是包含 shape 和 points 的对象")
    elif args.lon is not None and args.lat is not None:
        selection={"shape":args.shape,"system":"WGS84","points":[[args.lon,args.lat]],"point":args.point}
        if args.shape=="rectangle":selection.update(width_m=args.width,height_m=args.height)
        elif args.shape=="circle":selection["radius_m"]=args.radius
        else:p.error("多边形和曲线请使用 --selection")
    else:p.error("填写 --lon / --lat 或 --selection；无参数运行使用 GUI")
    if args.dem and args.mode=="tile":p.error("--dem 不能与使用平坦高程的 --mode tile 同时指定")
    mode="flat" if args.mode=="tile" else ("local" if args.dem else args.elevation)
    if mode=="local" and not args.dem:p.error("本地高程模式需要 --dem 文件路径")
    try:
        # A local-only task must also work with no saved online credentials.
        settings={} if args.elevation_only else load()
        source=default_source() if args.elevation_only else get_source(settings,args.source)
        token={} if args.elevation_only else credentials_for(settings,source["id"])
        if not args.elevation_only and source["id"]=="mapbox" and os.environ.get("MAPBOX_TOKEN"):
            token["token"]=os.environ["MAPBOX_TOKEN"]
        dem=snapshot_dem(args.dem,args.dem_unit) if mode=="local" else None
    except (ValueError,OSError) as exc:
        p.error(str(exc))
    secrets=secret_values(token)
    task={"selection":selection,"resolution":args.resolution,"zoom":args.zoom,"elevation":mode,
          "source":source,"imagery":not args.elevation_only,
          "output":str(Path(args.output)/args.name),"budget":args.budget,"options":{"proxy":args.proxy,"smooth":args.smooth,"bit_depth":args.bitdeep}}
    if dem is not None:task["dem"]=dem
    def emit(kind,**data):print(redact(json.dumps({"type":kind,**data},ensure_ascii=False),secrets),flush=True)
    control=Control(emit,args.budget)
    def cli_emit(kind,**data):
        emit(kind,**data)
        if kind=="state" and data.get("state")=="budget":control.command("cancel")
    control.emit=cli_emit
    try:run_task(task,token,cli_emit,control);return 0
    except Exception as exc:print(redact(exc,secrets),file=sys.stderr);return 1


if __name__=="__main__":
    if len(sys.argv)>1:prepare_stdio()
    if "--worker" in sys.argv:
        from mapdln.worker import main
        raise SystemExit(main())
    if len(sys.argv)>1:raise SystemExit(cli())
    try:
        from mapdln.gui import main
        raise SystemExit(main())
    except Exception:
        import traceback
        from mapdln.config import home,redact
        path=home()/'startup-error.log';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(redact(traceback.format_exc()),encoding='utf-8')
        if sys.stderr is not None:sys.stderr.write(path.read_text(encoding='utf-8'))
        if os.name=='nt' and getattr(sys,'frozen',False):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,'启动失败。诊断日志：\n'+str(path),'Mapdln 启动诊断',16)
        raise SystemExit(1)
