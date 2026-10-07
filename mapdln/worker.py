"""QProcess IPC: credentials arrive over stdin, commands remain live."""
import json
import sys
import threading
from .config import redact
from .engine import Control, run_task, Cancelled


def main():
    for stream in (sys.stdin,sys.stdout,sys.stderr):
        if stream is not None and hasattr(stream,"reconfigure"):
            stream.reconfigure(encoding="utf-8")
    message=json.loads(sys.stdin.readline())
    secrets=[message.get("token","")]
    lock=threading.Lock()
    def emit(kind,**values):
        with lock:
            sys.stdout.write(redact(json.dumps({"type":kind,**values},ensure_ascii=False),secrets)+"\n")
            sys.stdout.flush()
    control=Control(emit,message["task"].get("budget",0))
    def commands():
        for line in sys.stdin:
            try:
                command=json.loads(line)
                control.command(command["action"],command.get("budget"))
            except (ValueError,KeyError):
                emit("log",message="忽略无效控制消息")
    threading.Thread(target=commands,daemon=True).start()
    try:
        run_task(message["task"],message.get("token",""),emit,control)
        return 0
    except Cancelled:
        emit("state",state="cancelled",message="任务已取消，保留卫星成果和有效缓存")
        return 2
    except Exception as exc:
        emit("error",message=redact(exc,secrets))
        return 1
