# -*- coding: utf-8 -*-
"""主进程这边的 LaMa 子进程客户端（配合 app/lama_worker.py）。

三条硬要求，都是被现场逼出来的：
  1. **主进程绝不加载模型** —— 一旦在进程内 InferenceSession，GIL 被长占，界面必卡。
  2. 子进程用 CREATE_NO_WINDOW 起（主程序是 pythonw，宿主没有控制台，不加就会弹黑框）。
  3. 任何一步失败都返回 None / ok=False，**由调用方回退**，不许静默当成功。

环境变量 SHT_LAMA_IPC=0 可强制回退进程内（排查用）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))          # ...\上货台\app
APP_ROOT = os.path.dirname(HERE)                           # ...\上货台

_PROC = None
_LOCK = threading.Lock()
_TMP = os.path.join(tempfile.gettempdir(), "sht_lama_ipc")
STATE = {"started": 0, "warm": False, "last_error": "", "pid": 0}


def enabled() -> bool:
    return os.environ.get("SHT_LAMA_IPC", "1") not in ("0", "false", "off")


def _popen():
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("PYTHONUNBUFFERED", "1")
    return subprocess.Popen(
        [sys.executable, "-m", "app.lama_worker"],
        cwd=APP_ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        errors="replace", bufsize=1, creationflags=flags, env=env)


def _ensure():
    global _PROC
    if _PROC is not None and _PROC.poll() is None:
        return _PROC
    os.makedirs(_TMP, exist_ok=True)
    _PROC = _popen()
    STATE["started"] = time.time()
    STATE["pid"] = _PROC.pid
    return _PROC


def _call(req: dict, timeout: float = 600.0) -> dict:
    """发一条、收一条。计时只用来说明进度；真的超时靠子进程自己报错。"""
    with _LOCK:
        p = _ensure()
        try:
            p.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            p.stdin.flush()
        except Exception as e:                              # noqa: BLE001
            STATE["last_error"] = "写不进子进程：%s: %s" % (type(e).__name__, e)
            _kill()
            return {"ok": False, "error": STATE["last_error"]}
        line = ""
        t0 = time.time()
        try:
            line = p.stdout.readline()
        except Exception as e:                              # noqa: BLE001
            STATE["last_error"] = "读子进程失败：%s" % e
            _kill()
            return {"ok": False, "error": STATE["last_error"]}
        if not line:
            STATE["last_error"] = ("子进程没回话（可能崩了）耗时 %.1fs" % (time.time() - t0))
            _kill()
            return {"ok": False, "error": STATE["last_error"]}
        try:
            return json.loads(line)
        except Exception as e:                              # noqa: BLE001
            STATE["last_error"] = "子进程回了非 JSON：%s" % str(line)[:120]
            return {"ok": False, "error": STATE["last_error"]}


def available() -> dict:
    """给界面显示用：子进程活着吗、模型热了吗（不加载模型）。"""
    try:
        r = _call({"op": "ping"}, timeout=10)
    except Exception as e:                                  # noqa: BLE001
        r = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    return {"ok": bool(r.get("ok")), **STATE, "ping": r}


def warmup() -> dict:
    """在子进程里加载模型。主进程只等一行 JSON —— 界面全程可操作。"""
    if not enabled():
        return {"ok": False, "error": "已用 SHT_LAMA_IPC=0 关闭子进程模式"}
    r = _call({"op": "warmup"}, timeout=600)
    STATE["warm"] = bool(r.get("ok"))
    if not r.get("ok"):
        STATE["last_error"] = str(r.get("error") or "")[:200]
    return r


def inpaint(bgr, mask):
    """送一张图给子进程修补，返回 BGR ndarray；任何异常返回 None（调用方回退）。"""
    if not enabled() or bgr is None or mask is None:
        return None
    try:
        import cv2
        import numpy as np
    except Exception:                                       # noqa: BLE001
        return None
    tag = "%d_%d" % (os.getpid(), time.time_ns())
    src = os.path.join(_TMP, "in_%s.png" % tag)
    mk = os.path.join(_TMP, "mk_%s.png" % tag)
    out = os.path.join(_TMP, "out_%s.png" % tag)
    try:
        os.makedirs(_TMP, exist_ok=True)
        for path, arr, mode in ((src, bgr, ".png"), (mk, mask, ".png")):
            ok, buf = cv2.imencode(mode, arr)
            if not ok:
                return None
            with open(path, "wb") as fh:
                fh.write(buf.tobytes())
        r = _call({"op": "inpaint", "src": src, "mask": mk, "out": out}, timeout=600)
        if not r.get("ok") or not os.path.isfile(out):
            STATE["last_error"] = str(r.get("error") or "子进程没产出结果")[:200]
            return None
        res = cv2.imdecode(np.fromfile(out, dtype=np.uint8), cv2.IMREAD_COLOR)
        return res
    except Exception as e:                                  # noqa: BLE001
        STATE["last_error"] = "%s: %s" % (type(e).__name__, e)
        return None
    finally:
        for p in (src, mk, out):
            try:
                os.remove(p)
            except OSError:
                pass


def _kill():
    global _PROC
    p, _PROC = _PROC, None
    if p is None:
        return
    try:
        p.stdin.write(json.dumps({"op": "bye"}) + "\n")
        p.stdin.flush()
    except Exception:                                       # noqa: BLE001
        pass
    try:
        p.wait(timeout=2)
    except Exception:                                       # noqa: BLE001
        try:
            p.kill()
        except Exception:                                   # noqa: BLE001
            pass


def stop():
    """主程序退出时调：子进程必须跟着走，绝不留下占着显存的孤儿。"""
    _kill()
