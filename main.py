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
    p=argparse.ArgumentParser(description="Mapdln GUI / CLI 地图下载器")
    p.add_argument("--cli",action="store_true",help="使用命令行，无参数启动 GUI")
    p.add_argument("--lon",type=float);p.add_argument("--lat",type=float)
    p.add_argument("-w","--width",type=float,default=200);p.add_argument("-g","--height",type=float,default=200)
    p.add_argument("--shape",choices=["rectangle","circle","polygon","curve"],default="rectangle")
    p.add_argument("--radius",type=float,default=100);p.add_argument("--selection",help="选区 JSON 文件，points+system+shape")
    p.add_argument("-z","--zoom",type=int,default=17);p.add_argument("--resolution",type=float,default=1)
    p.add_argument("--elevation",choices=["flat","real"],default="real")
    p.add_argument("-m","--mode",choices=["full","tile"],help="兼容旧参数：tile 使用 0 米高程")
    p.add_argument("-p","--point",choices=["center","lefttop"],default="center")
    p.add_argument("-n","--name",default="mapdln-export");p.add_argument("--output",default=".")
    p.add_argument("-b","--bitdeep",type=int,choices=[8,16],default=16);p.add_argument("-s","--smooth",type=float,default=0)
    p.add_argument("--proxy",default="");p.add_argument("--budget",type=int,default=0)
    args=p.parse_args()
    if args.selection:selection=json.loads(Path(args.selection).read_text(encoding="utf-8"))
    elif args.lon is not None and args.lat is not None:
        selection={"shape":args.shape,"system":"WGS84","points":[[args.lon,args.lat]],"point":args.point}
        if args.shape=="rectangle":selection.update(width_m=args.width,height_m=args.height)
        elif args.shape=="circle":selection["radius_m"]=args.radius
        else:p.error("多边形和曲线请使用 --selection")
    else:p.error("填写 --lon / --lat 或 --selection；无参数运行使用 GUI")
    token=os.environ.get("MAPBOX_TOKEN") or load().get("mapbox_token")
    task={"selection":selection,"resolution":args.resolution,"zoom":args.zoom,"elevation":"flat" if args.mode=="tile" else args.elevation,
          "output":str(Path(args.output)/args.name),"budget":args.budget,"options":{"proxy":args.proxy,"smooth":args.smooth,"bit_depth":args.bitdeep}}
    def emit(kind,**data):print(json.dumps({"type":kind,**data},ensure_ascii=False),flush=True)
    control=Control(emit,args.budget)
    def cli_emit(kind,**data):
        emit(kind,**data)
        if kind=="state" and data.get("state")=="budget":control.command("cancel")
    control.emit=cli_emit
    try:run_task(task,token,cli_emit,control);return 0
    except Exception as exc:print(redact(exc,[token]),file=sys.stderr);return 1


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
