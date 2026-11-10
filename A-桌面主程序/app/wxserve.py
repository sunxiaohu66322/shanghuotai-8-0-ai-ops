# -*- coding: utf-8 -*-
"""6.0 · 微店后台管理 —— 把「微信小店自建上货台」那套服务**在本进程内**拉起来。

为什么在进程内而不是另起一个 python：
  1. 用户只允许一个入口，不允许手动启动第二套软件；
  2. 打包成 exe 后没有 python 解释器，子进程方案会直接失效；
  3. 进程内启动 = 主程序退出时它必然跟着退出，不会留孤儿端口。

只做三件事：加载模块 → 选一个能用的端口 → 在线程里 serve_forever。
业务代码一行不改、不复制（真源仍在 微信小店自建上货台\\app.py）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent            # ...\上货台\app
ROOT = _HERE.parent                                # ...\上货台
WS = ROOT.parent                                   # default-workspace
# ★ 真源路径写死绝对值（与 paths.TAOBAO_DIR 同一风格）：打包成 exe 后 ROOT 是
#   dist\上货台\，ROOT.parent 不再是 default-workspace，靠相对推导会指错。
#   6.0 的代码与数据不复制进 exe（真源唯一），exe 直接读这个目录。
WEBAPP_DIR = Path(r"G:\Users\Administrator\Documents\deepseek-harness\default-workspace\微信小店自建上货台")
ENTRY = WEBAPP_DIR / "app.py"


def _http_json(url: str, timeout: float = 1.5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")
    except Exception:
        return None


def _port_free(port: int) -> bool:
    """**真的**空闲吗。

    ★ 2026-10-27 实测踩坑：Windows 下 SO_REUSEADDR 允许**抢占别的进程正在监听的端口**，
      所以「setsockopt(SO_REUSEADDR) + bind 成功」根本不等于空闲 ——
      实测 9191 被另一个上货台进程占着（且已假死），bind 照样成功，
      于是新服务以为自己抢到了 9191，实际请求全进了那个死 socket，健康检查永远不过。
    正确判据两条一起看：
      ① 有没有人在听（connect 得上就是有人）—— 这条能识破假死
      ② 用 SO_EXCLUSIVEADDRUSE 试绑（Windows 专有）—— 这条能识破别人已占用的 socket
    """
    s = socket.socket()
    s.settimeout(0.3)
    try:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            return False
    except OSError:
        pass
    finally:
        s.close()
    s = socket.socket()
    try:
        ex = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if ex is not None:
            s.setsockopt(socket.SOL_SOCKET, ex, 1)
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _ensure_streams():
    """把 sys.stdout / sys.stderr 补上。

    ★★ 2026-10-27 这就是「6.0 打不开」的真正根因（不是端口假死、不是 bind 失败）：
      上货台是用 **pythonw.exe** 启动的，而 pythonw 下 `sys.stdout` / `sys.stderr` 是 **None**。
      6.0 的请求处理器里 `log_message()` 第一行就是 `sys.stderr.write(...)` ——
      于是**每一个请求在回包之前都抛 AttributeError**，连接被直接关掉。
      从外面看到的现象是：端口在听、TCP 能连上、发请求立刻 `RemoteDisconnected`，
      浏览器就永远打不开；而用 python.exe（有控制台）跑同样的代码一切正常 ——
      这也是它「有时好有时坏」的原因。
    """
    logdir = ROOT / "data" / "logs"
    try:
        logdir.mkdir(parents=True, exist_ok=True)
    except Exception:                                  # noqa: BLE001
        pass
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is not None:
            continue
        f = None
        try:
            f = open(str(logdir / "wx60-std.log"), "a", encoding="utf-8", buffering=1)
        except Exception:                              # noqa: BLE001
            try:
                f = open(os.devnull, "w", encoding="utf-8")
            except Exception:                          # noqa: BLE001
                f = None
        if f is not None:
            setattr(sys, name, f)


class SixtyWeb:
    """6.0 服务的生命周期归主程序管；base 为空表示没起来。"""

    started_count = 0          # 类级计数：本进程真正创建过几个 HTTP 服务

    def __init__(self):
        self.base = ""
        self.port = 0
        self.err = ""
        self.mod = None
        self.srv = None
        self.thread = None
        self._lock = threading.Lock()
        # ★ 2026-10-27 自愈守护（主人实测：9191 端口在听，但 HTTP 请求返回 0 字节
        #   —— 应答线程死了、socket 还挂着。界面只表现为「打不开」，没有任何提示。
        #   所以这里加一个 20 秒一轮的看护：健康检查不过就自己收掉重起。）
        self._closing = False
        self._wd = None
        self.events = []          # 最近的自愈事件（给界面显示，最多 50 条）
        self.restarts = 0
        self.last_health_raw = ""  # 最后一次健康检查的原文（查问题时唯一的现场）

    # ---------------- 对外 ----------------
    @property
    def ok(self) -> bool:
        return bool(self.base) and bool(self.health())

    def health(self):
        """敲它的门。返回 dict 表示活着（**不要求有 ok 字段**），None 表示不应答。

        ★ 2026-10-27：原来的实现用 `json.loads` 直接解析，解析失败就当死；
          而「不应答」和「答了但不是 JSON」是两件事，必须能分清 ——
          否则界面只显示一句「健康检查不通过」，查一天也查不出是什么原因。
        """
        self.last_health_raw = ""
        if not self.base:
            return None
        try:
            with urllib.request.urlopen(self.base + "/api/health", timeout=1.2) as r:
                raw = r.read(2048)
        except Exception as e:                       # noqa: BLE001
            self.last_health_raw = "%s: %s" % (type(e).__name__, str(e)[:90])
            return None
        if not raw:
            self.last_health_raw = "回了 0 字节（端口在听但不应答 = 假死）"
            return None
        s = raw.decode("utf-8", "replace")
        try:
            j = json.loads(s)
        except Exception:                            # noqa: BLE001
            self.last_health_raw = "不是 JSON：%s" % s[:90]
            return {"_raw": s[:200]}
        self.last_health_raw = s[:200]
        return j if isinstance(j, dict) else {"_raw": s[:200]}

    def start(self, port: int | None = None) -> str:
        """返回 base_url；失败返回 '' 并把原因写进 self.err。

        port 只在自测时用；正常运行传 None（先复用已有实例，再抢配置端口）。
        """
        with self._lock:
            if self.base and self.health():
                return self.base
            if not ENTRY.exists():
                self.err = "找不到 6.0 服务源：%s" % ENTRY
                return ""
            _ensure_streams()          # ← pythonw 下必须先补 stderr，否则每个请求都会断
            # 1) 已经有别人在跑（比如我调脚本时起的），直接复用，不抢端口
            for _pp in ((port,) if port else (9191,)):
                j = _http_json("http://127.0.0.1:%d/api/health" % _pp)
                if j and j.get("ok"):
                    self.port, self.base = _pp, "http://127.0.0.1:%d" % _pp
                    self.err = ""
                    self._ensure_watchdog()
                    return self.base
            # 2) 本进程内拉起
            try:
                if self.mod is None:
                    self.mod = self._load_module()
                port = port or self._pick_port()
                srv = self.mod.ThreadingHTTPServer(("127.0.0.1", port), self.mod.H)
                self.srv, self.port = srv, port
                SixtyWeb.started_count += 1
                t = threading.Thread(target=srv.serve_forever, name="wx60-http", daemon=True)
                t.start()
                self.thread = t
                self.base = "http://127.0.0.1:%d" % port
                # 等它真的能答（不是「线程起来了」就算成功）
                h = None
                for _ in range(60):
                    h = self.health()
                    if h:
                        self.err = ""
                        self._ensure_watchdog()
                        return self.base
                    time.sleep(0.15)
                self.err = ("服务已启动（口 %d）但 9 秒内健康检查不过；最后一次：%s"
                            % (port, self.last_health_raw or "无返回"))
                self._note(self.err)
                return self.base
            except Exception as e:                        # noqa: BLE001
                self.err = "%s: %s" % (type(e).__name__, e)
                self.base = ""
                return ""

    def stop(self):
        """关干净：先让 serve_forever 退出，再关监听套接字，最后确认端口真的放掉。

        ★ 2026-10-27：**不许无条件调 shutdown()**。
          `BaseServer.shutdown()` 会等 serve_forever 退出；
          而 serve_forever 线程已经死掉时它永远等不到 —— 调用方（界面线程）直接卡死。
          端口假死正是这种形态，所以先看线程活没活。
        """
        srv = self.srv
        alive = bool(self.thread is not None and self.thread.is_alive())
        self.srv = None
        try:
            if srv is not None:
                if alive:
                    srv.shutdown()
                srv.server_close()
        except Exception:                                 # noqa: BLE001
            pass
        # shutdown() 返回后极短时间内套接字可能还在内核里收尾 —— 轮询确认
        for _ in range(20):
            if not srv or not _port_free(self.port or 0):
                break
            time.sleep(0.05)
        self.thread = None
        self.base = ""
        self.port = 0

    # ---------------- 自愈 ----------------
    def close(self):
        """主程序退出时调这个：先让守护线程停下，再收服务（否则它会把服务又拉起来）。"""
        self._closing = True
        self.stop()

    def _note(self, s: str):
        self.events.append("%s %s" % (time.strftime("%H:%M:%S"), s))
        del self.events[:-50]

    def ensure(self) -> bool:
        """健康检查；不通过就自己收掉重起。返回是否健康。

        ★ 判据只有一条：**敲它的门**（/api/health 真回 ok）。
          端口在听不算 —— 假死的那个端口也在听，那是这次踩的坑本身。
        """
        if self._closing:
            return False
        if not self.base:
            self.start()
            return bool(self.base) and bool(self.health())
        if self.health():
            return True
        self._note("6.0 无响应（端口在听但不应答），自动重启")
        port = self.port or 9191
        self.stop()
        self.start()
        self.restarts += 1
        if self.base and self.health():
            self._note("已恢复：%s（第 %d 次自愈）" % (self.base, self.restarts))
            return True
        self._note("重启失败：%s" % (self.err or "未知原因"))
        return False

    def _ensure_watchdog(self):
        if self._wd is not None:
            return
        self._closing = False

        def loop():
            while not self._closing:
                time.sleep(20)
                if self._closing:
                    break
                try:
                    if self.base:
                        self.ensure()
                except Exception as e:                    # noqa: BLE001
                    self._note("守护线程异常（已忽略）：%s: %s" % (type(e).__name__, e))

        self._wd = threading.Thread(target=loop, name="wx60-watchdog", daemon=True)
        self._wd.start()

    # ---------------- 内部 ----------------
    def _pick_port(self) -> int:
        cfg_port = 9191
        try:
            cfg = json.loads((WEBAPP_DIR / "config.json").read_text(encoding="utf-8"))
            cfg_port = int(cfg.get("port") or 9191)
        except Exception:                                 # noqa: BLE001
            pass
        if _port_free(cfg_port):
            return cfg_port
        # ★ 2026-10-27：9191 被别人占着时**不许悄悄换随机口**。
        #   实测：随机口一旦撞上别的进程（本机 9197 就被 vivo 的服务占着），
        #   因为 Windows 的 SO_REUSEADDR 允许抢绑，结果是「服务启动了、请求全被对方接走」，
        #   界面只会显示「健康检查不通过」，查不出所以然。
        #   所以：先试相邻口（好记），每个都先验证真的空闲；并且把换口这件事记下来。
        for p in (cfg_port + 1, cfg_port + 2, cfg_port + 3, cfg_port + 10):
            if _port_free(p):
                self._note("%d 被别的进程占着，改用 %d" % (cfg_port, p))
                return p
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", 0))
            p = s.getsockname()[1]
        finally:
            s.close()
        self._note("%d 及其相邻口都不可用，改用随机口 %d" % (cfg_port, p))
        return p

    @staticmethod
    def _load_module():
        """按文件路径加载，模块名刻意避开 `app`（那就是本程序的包名）。"""
        name = "wxshop60_app"
        sys.path.insert(0, str(WEBAPP_DIR))
        spec = importlib.util.spec_from_file_location(name, str(ENTRY))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

