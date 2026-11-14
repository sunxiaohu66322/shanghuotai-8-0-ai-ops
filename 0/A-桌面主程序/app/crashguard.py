# -*- coding: utf-8 -*-
"""崩溃守夜器 —— 让「软件突然就没了」这件事从此有据可查。

为什么要有这个文件（2026-10-27 用户两次报「做着做着突然跳出了，哪出的问题都不知道」）：
    实测全项目搜 `excepthook` / `faulthandler` / `sys.stderr =` 全是 0 处。
    也就是说：
      · Python 主线程抛未捕获异常 → 默认打到 stderr，而 pythonw.exe 的 stderr 是空设备，
        屏幕看不见、文件也留不下，进程直接死。
      · 子线程抛异常（_MarkWorker 就跑在 QThreadPool 里）→ 默认只打一行到 stderr，同样丢失。
      · 原生层崩溃（cv2 / onnxruntime 段错误 / 内存耗尽）→ Python 完全察觉不到，
        进程瞬间消失，连 finally 都不执行。★ 2026-10-27 12:37 那次就是这种：
        日志停在「第 7/9 张 图#14683 1440x1440 框换算完」，连 "LaMa ..s" 都没打出来。

这一层把三种情况全部接管，各自落到文件：
    1) 主线程未捕获异常   → sys.excepthook
    2) 子线程未捕获异常   → threading.excepthook（QThreadPool 里的 worker 死掉会记在这里）
    3) 原生段错误/栈溢出 → faulthandler（写真实 C 栈）
额外：
    4) 每 60 秒心跳一次；下次启动时若发现上次没有正常收尾，就在日志里点名「上次是异常退出」
       （这样即使进程被 taskkill / 断电 / 原生崩溃，也能知道它没走正常路径）
    5) 每条业务日志可带「当前在做什么」—— 崩溃时能看出最后一步停在哪

用法（只在 boot.py 里调一次，必须在 QApplication 之前）：
    from . import crashguard
    crashguard.install()

设计原则：
  · 绝不因为守夜器自己出错而影响主程序 —— 全部 try/except 静默。
  · 崩溃日志与业务日志分开文件：crash-YYYYMMDD.log。每次写都 flush + fsync，
    宁可慢也要留住最后一行。
  · 不做任何「自动重启」—— 那会把崩溃变成静默循环，掩盖真问题。
"""
from __future__ import annotations

import atexit
import datetime as _dt
import faulthandler
import os
import sys
import threading
import traceback
from pathlib import Path

_LOCK = threading.RLock()
_INSTALLED = False
_CRASH_FILE: Path | None = None
_FH_FILE = None                       # faulthandler 打开的句柄，必须保持引用
_HEARTBEAT_THREAD: threading.Thread | None = None
_STOP = threading.Event()
_LAST_BEAT: dict = {"t": 0.0, "where": "-"}
_START: dict = {"t": 0.0, "at": ""}

HEARTBEAT_SEC = 600                   # 心跳间隔（秒）
# ★ 2026-10-28 从 60s 改到 600s：原来每分钟一条，一天 1440 行，
#   把「上次是否异常退出」的检测窗口（读日志尾部 8000 字）整段刷掉 —— 实测 2026-10-28
#   02:00 进程消失后再启动，日志里**没有**打出「上次没有正常退出」，检测形同虚设。
#   现在改成：① 心跳 10 分钟一条 ② 另用 lastrun.json 独立标记（不依赖日志窗口）。


# ---------------------------------------------------------------- 路径

def _log_dir() -> Path:
    """与 logwin.py 用同一个目录：上货台/data/logs。"""
    here = Path(__file__).resolve().parent          # …\上货台\app
    d = here.parent / "data" / "logs"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:                                # noqa: BLE001
        d = Path(os.environ.get("TEMP") or ".") / "上货台-logs"
        d.mkdir(parents=True, exist_ok=True)
    return d


def crash_file() -> Path | None:
    return _CRASH_FILE


def _today_name() -> str:
    return "crash-%s.log" % _dt.datetime.now().strftime("%Y%m%d")


# ---------------------------------------------------------------- 落盘

def _emit(block: str) -> None:
    """写崩溃日志。每次 flush + fsync —— 崩在半路也要留住。"""
    if not _CRASH_FILE:
        return
    try:
        ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with _LOCK:
            with open(_CRASH_FILE, "a", encoding="utf-8", errors="replace") as f:
                f.write("\n" + "=" * 72 + "\n")
                f.write("[%s] %s\n" % (ts, block))
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:                    # noqa: BLE001
                    pass
    except Exception:                                # noqa: BLE001
        pass                                         # 守夜器自己出错也绝不能影响主流程


def _context() -> str:
    """崩溃现场：线程名、当前在做什么、内存。"""
    parts = ["线程=%s" % threading.current_thread().name]
    try:
        parts.append("最后动作=%s" % _LAST_BEAT.get("where"))
    except Exception:                                # noqa: BLE001
        pass
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        m = PMC()
        m.cb = ctypes.sizeof(PMC)
        if ctypes.windll.psapi.GetProcessMemoryInfo(
                ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(m), m.cb):
            parts.append("内存=%.0fMB(峰值%.0fMB)"
                         % (m.WorkingSetSize / 1048576, m.PeakWorkingSetSize / 1048576))
    except Exception:                                # noqa: BLE001
        pass
    return " · ".join(parts)


# ---------------------------------------------------------------- 三个钩子

def _hook_main(exc_type, exc, tb) -> None:
    """主线程未捕获异常。"""
    txt = "".join(traceback.format_exception(exc_type, exc, tb))
    _emit("★ 主线程未捕获异常（进程即将退出）\n%s\n%s\n%s"
          % (_context(), txt, _biz_tail(10)))
    try:
        sys.__excepthook__(exc_type, exc, tb)
    except Exception:                                # noqa: BLE001
        pass


def _hook_thread(args) -> None:
    """子线程未捕获异常。子线程死掉不会结束进程，但任务会静默中断。"""
    name = getattr(getattr(args, "thread", None), "name", "<unknown>")
    txt = "".join(traceback.format_exception(
        args.exc_type, args.exc_value, args.exc_traceback))
    _emit("★ 子线程未捕获异常（线程 %s 已死亡）\n%s\n%s\n%s"
          % (name, _context(), txt, _biz_tail(8)))
    try:
        threading.__excepthook__(args)               # 保留默认行为
    except Exception:                                # noqa: BLE001
        pass


def _hook_unraisable(args) -> None:
    """析构函数/回调里的异常（__del__ 抛错、Qt 槽里抛错等）。"""
    try:
        txt = "".join(traceback.format_exception(
            args.exc_type, args.exc_value, args.exc_traceback))
        where = ""
        try:
            where = " 对象=%r" % (getattr(args, "object", None),)
        except Exception:                            # noqa: BLE001
            pass
        _emit("★ 被吞掉的异常（unraisable）%s\n%s\n%s" % (where, _context(), txt))
    except Exception:                                # noqa: BLE001
        pass


# ---------------------------------------------------------------- 心跳

def beat(where: str = "") -> None:
    """业务代码主动报「我现在在干什么」，崩溃时能看出最后停在哪。"""
    try:
        import time
        _LAST_BEAT["t"] = time.time()
        if where:
            _LAST_BEAT["where"] = where
    except Exception:                                # noqa: BLE001
        pass


def last_action() -> str:
    return str(_LAST_BEAT.get("where") or "-")


def _heartbeat_loop() -> None:
    """心跳：只在「最后动作变了」或每 30 分钟时写一行 —— 日志要能看出痕迹，但不能刷屏。

    ★ 2026-10-28：原来每 60 秒无条件写一条，一天 1440 行，把日志刷成噪音，
      还把「上次异常退出」的检测窗口冲掉了（实测 8 次异常退出一个都没报出来）。
    """
    import time
    last_where = ""
    last_write = 0.0
    while not _STOP.wait(HEARTBEAT_SEC):
        try:
            where = _LAST_BEAT.get("where") or "-"
            now = time.time()
            if where != last_where or (now - last_write) >= 1800:
                _emit("· 存活 %.0f 分钟 ｜ 主线程=%s ｜ 最后动作=%s ｜ %s"
                      % (now - (_START.get("t") or now), threading.main_thread().is_alive(),
                         where, _context()))
                last_where = where
                last_write = now
        except Exception:                            # noqa: BLE001
            pass


# ---------------------------------------------------------------- 启动/收尾

_MARK = "[正常启动]"
_EXIT = "[正常退出]"
_LASTRUN = {"pid": 0, "start": "", "state": "", "exe": ""}


def _lastrun_file() -> Path | None:
    return (_CRASH_FILE.parent / "lastrun.json") if _CRASH_FILE else None


def _lastrun_write(state: str) -> None:
    """独立标记文件 —— 不靠日志窗口，绝不因为日志刷屏而失效。"""
    f = _lastrun_file()
    if not f:
        return
    try:
        import json
        info = dict(_LASTRUN)
        info.update({"state": state, "state_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(info, fh, ensure_ascii=False, indent=1)
    except Exception:                            # noqa: BLE001
        pass


def _prev_was_crash() -> str:
    """上次是否异常退出。返回 '' = 正常/首次；否则返回上次启动时间（用于告警文案）。"""
    f = _lastrun_file()
    if not f or not f.exists():
        return ""
    try:
        import json
        d = json.loads(f.read_text(encoding="utf-8"))
        if str(d.get("state")) == "running":
            return str(d.get("start") or "?")
    except Exception:                            # noqa: BLE001
        pass
    return ""


def _on_exit() -> None:
    _STOP.set()
    _emit("%s 进程正常收尾 pid=%s" % (_EXIT, os.getpid()))
    _lastrun_write("clean")
    try:
        faulthandler.cancel_dump_traceback_later()
    except Exception:                                # noqa: BLE001
        pass


def install(log_dir: str | os.PathLike | None = None) -> Path | None:
    """装好守夜器。必须在 QApplication 之前调用一次。返回崩溃日志路径。"""
    global _INSTALLED, _CRASH_FILE, _FH_FILE, _HEARTBEAT_THREAD
    if _INSTALLED:
        return _CRASH_FILE
    try:
        d = Path(log_dir) if log_dir else _log_dir()
        d.mkdir(parents=True, exist_ok=True)
        _CRASH_FILE = d / _today_name()

        # ① faulthandler：捕原生崩溃（段错误/栈溢出/abort）
        try:
            _FH_FILE = open(_CRASH_FILE, "a", encoding="utf-8", errors="replace")
            faulthandler.enable(file=_FH_FILE, all_threads=True)
        except Exception:                            # noqa: BLE001
            _FH_FILE = None

        # ② Python 层三个钩子
        sys.excepthook = _hook_main
        threading.excepthook = _hook_thread
        try:
            sys.unraisablehook = _hook_unraisable
        except Exception:                            # noqa: BLE001
            pass

        # ③ 启动标记（lastrun.json 独立记，不靠日志窗口）+ Windows 事件日志留档
        import time as _t
        _START.update({"t": _t.time(), "at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        _LASTRUN.update({"pid": os.getpid(), "start": _START["at"], "state": "running",
                         "exe": getattr(sys, "executable", "-")})
        prev_crash = _prev_was_crash()
        _lastrun_write("running")

        _emit("%s pid=%s python=%s exec=%s"
              % (_MARK, os.getpid(), sys.version.split()[0],
                 getattr(sys, "executable", "-")))
        if prev_crash:
            _emit("⚠⚠ 上一次运行【没有正常退出】：上次启动于 %s，"
                  "之后没有留下「%s」标记 —— 进程属于被系统杀掉/原生崩溃/断电。"
                  "本文件中该时间点之后的内容即上次的最后现场。" % (prev_crash, _EXIT))

        # ③b 把 Windows 自己记的崩溃搬进来（faulthandler 抓不到堆损坏，事件日志能）
        try:
            ev = _winevent_recent(days=3)
            if ev:
                _emit("● Windows 事件日志里的近 3 天崩溃记录（系统自己记的，不是我们猜的）：\n"
                      + ev)
        except Exception:                            # noqa: BLE001
            pass

        # ④ 心跳
        _HEARTBEAT_THREAD = threading.Thread(
            target=_heartbeat_loop, name="crashguard-hb", daemon=True)
        _HEARTBEAT_THREAD.start()

        atexit.register(_on_exit)
        _INSTALLED = True
        return _CRASH_FILE
    except Exception:                                # noqa: BLE001
        return None


def dump_now(reason: str = "") -> None:
    """主动把当前所有线程的 Python 栈写进崩溃日志（排查「卡住不动」用）。

    ★ 坑（2026-10-27 实测）：faulthandler.dump_traceback(file=...) 要求文件对象
      有真实的 fileno()，传 io.StringIO 会抛 UnsupportedOperation("fileno")，
      于是「抓栈」这件事静默失败、日志里一个字都没有。必须落到真文件再读回来。
    """
    try:
        import tempfile
        head = "◆ 主动抓栈 %s\n%s\n" % (reason, _context())
        body = ""
        try:
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8",
                                        errors="replace", delete=True) as tf:
                faulthandler.dump_traceback(file=tf, all_threads=True)
                tf.seek(0)
                body = tf.read()
        except Exception as e:                       # noqa: BLE001
            body = "（抓栈失败：%s: %s）" % (type(e).__name__, e)
        _emit(head + body)
    except Exception:                                # noqa: BLE001
        pass


# ---------------------------------------------------------------- Windows 事件日志（唯一的硬证据）

def _winevent_recent(days: int = 3, limit: int = 8) -> str:
    """读 Windows「应用程序」日志里的崩溃记录（EventID 1000/1001）。

    为什么必须读它：faulthandler 只能抓 SIGSEGV/abort 这类；**堆损坏（0xc0000374）时
    进程内存已被破坏，Python 层的钩子写不出任何东西** —— 实测 2026-10-27 两次
    ntdll 0xc0000374 崩溃，crash 日志里一个「★」都没有，而 WER 事件日志记得清清楚楚。
    所以：把系统自己记的搬进来，不再只靠自己猜。

    实现注意：wevtutil 是控制台程序，父进程是 pythonw（无控制台）→ **必须带
    CREATE_NO_WINDOW**，否则每次启动闪一个黑框（铁律 19）。
    """
    import subprocess
    NOWIN = 0x08000000 if os.name == "nt" else 0
    ms = int(days) * 86400000
    q = ("*[System[(EventID=1000 or EventID=1001) and TimeCreated[timediff(@SystemTime) <= %d]]]"
         % ms)
    try:
        r = subprocess.run(
            ["wevtutil", "qe", "Application", "/q:" + q, "/c:%d" % int(limit * 3),
             "/rd:true", "/f:text"],
            capture_output=True, text=True, errors="replace", timeout=10,
            creationflags=NOWIN)
        txt = (r.stdout or "")
    except Exception as e:                           # noqa: BLE001
        return "（读事件日志失败：%s: %s）" % (type(e).__name__, e)
    if not txt.strip():
        return ""
    keep = []
    for block in txt.split("\r\n\r\n"):
        b = block.strip()
        if not b:
            continue
        low = b.lower()
        if ("python" in low or "上货台" in b) and ("0xc0" in low or "appcrash" in low
                                                  or "错误应用程序" in b or "故障存储段" in b):
            keep.append(" · " + " / ".join(x.strip() for x in b.splitlines()
                                           if x.strip() and not x.startswith("日志名"))[:400])
        if len(keep) >= limit:
            break
    return "\n".join(keep)


def _biz_tail(n: int = 12) -> str:
    """业务日志最后几行 —— 崩溃瞬间「在干什么」比栈更直观。"""
    try:
        f = (_CRASH_FILE.parent / "run.log") if _CRASH_FILE else None
        if not f or not f.exists():
            return ""
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()[-int(n):]
        return "业务日志末 %d 行：\n" % len(lines) + "".join(lines)
    except Exception:                                # noqa: BLE001
        return ""


def report() -> str:
    """一键诊断文本（给人看，也给下一个会话看）。只读，不改任何东西。"""
    d = _CRASH_FILE.parent if _CRASH_FILE else _log_dir()
    out = ["=== 上货台 崩溃诊断 %s ===" % _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")]

    # ① 启动/正常退出计数（把「异常退出率」算出来，不靠印象）
    for name in ("crash-%s.log" % _dt.datetime.now().strftime("%Y%m%d"),
                 "crash-%s.log" % (_dt.datetime.now() - _dt.timedelta(days=1)).strftime("%Y%m%d")):
        p = d / name
        if not p.exists():
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        st, ex = t.count(_MARK), t.count(_EXIT)
        out.append("② %s：启动 %d 次 · 正常退出 %d 次 · **异常退出 %d 次**"
                   % (name, st, ex, max(0, st - ex)))

    lr = _lastrun_file()
    if lr and lr.exists():
        try:
            out.append("③ 上次运行：%s" % lr.read_text(encoding="utf-8").replace("\n", " ")[:200])
        except Exception:                            # noqa: BLE001
            pass

    out.append("④ Windows 事件日志（硬证据）：")
    out.append(_winevent_recent(days=7, limit=10) or "   近 7 天没有 pythonw 崩溃记录")

    out.append("⑤ 业务日志末 5 行：")
    out.append(_biz_tail(5))
    return "\n".join(out)
