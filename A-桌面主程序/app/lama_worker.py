# -*- coding: utf-8 -*-
"""LaMa 修补子进程 —— 模型加载与推理都在这个进程里，主界面永远不会被卡住。

为什么要单开进程（2026-10-27 主人第三次反馈「一预热整个软件就卡死」）：
  · ONNX Runtime 加载 208MB 模型时，Python 层长时间持有 GIL；
    GIL 是进程内互斥，**降线程优先级对它无效** —— 所以上一轮加的
    SetThreadPriority 只让 CPU 少抢一点，界面依然拿不到 GIL，照样卡死。
  · 只有把它挪出主进程，主进程才**一个字节的 GIL 都不占**。
  · 顺带好处：模型段错误只会炸掉这个子进程，主程序还在，能报错、能重试。

协议：一行一个 JSON（UTF-8）。
  请求 {"op":"warmup"}                     → {"ok":true,"seconds":..,"load":..}
  请求 {"op":"inpaint","src":..,"mask":..,"out":..}
                                            → {"ok":true,"seconds":..,"out":..}
  请求 {"op":"ping"} / {"op":"bye"}
"""
from __future__ import annotations

import json
import os
import sys
import time

TBAO = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "taobao-picker")
if TBAO not in sys.path:
    sys.path.insert(0, TBAO)

_L = None
_ERR = ""


def _lama():
    global _L, _ERR
    if _L is not None:
        return _L
    try:
        from server.v3 import inpaint_lama as mod          # noqa: PLC0415
    except Exception as e:                                  # noqa: BLE001
        _ERR = "导入 inpaint_lama 失败：%s: %s" % (type(e).__name__, e)
        raise
    _L = mod
    return _L


def _reply(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _do(req: dict) -> dict:
    op = str(req.get("op") or "").lower()
    t0 = time.time()
    if op == "ping":
        return {"ok": True, "pid": os.getpid(), "ready": _L is not None}
    if op == "bye":
        return {"ok": True, "bye": True}
    if op == "warmup":
        try:
            r = _lama().warmup()
            r["pid"] = os.getpid()
            return r
        except Exception as e:                              # noqa: BLE001
            return {"ok": False, "error": _ERR or "%s: %s" % (type(e).__name__, e),
                    "seconds": round(time.time() - t0, 2)}
    if op == "inpaint":
        try:
            import cv2                                        # noqa: PLC0415
            import numpy as np                                # noqa: PLC0415
            L = _lama()
            src = str(req.get("src") or "")
            mk = str(req.get("mask") or "")
            out = str(req.get("out") or "")
            im = cv2.imdecode(np.fromfile(src, dtype=np.uint8), cv2.IMREAD_COLOR)
            if im is None:
                return {"ok": False, "error": "读不到原图：%s" % src}
            mask = cv2.imdecode(np.fromfile(mk, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if mask is None:
                return {"ok": False, "error": "读不到遮罩：%s" % mk}
            res = L.inpaint(im, mask)
            ok_enc, buf = cv2.imencode(".png", res)
            if not ok_enc:
                return {"ok": False, "error": "结果编码失败"}
            with open(out, "wb") as fh:
                fh.write(buf.tobytes())
            return {"ok": True, "seconds": round(time.time() - t0, 2), "out": out,
                    "h": int(res.shape[0]), "w": int(res.shape[1])}
        except Exception as e:                              # noqa: BLE001
            import traceback                                  # noqa: PLC0415
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                    "trace": traceback.format_exc()[-800:],
                    "seconds": round(time.time() - t0, 2)}
    return {"ok": False, "error": "不认识的指令：%s" % op}


def main() -> None:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except Exception as e:                              # noqa: BLE001
            _reply({"ok": False, "error": "请求不是 JSON：%s" % e})
            continue
        r = _do(req)
        _reply(r)
        if r.get("bye"):
            break


if __name__ == "__main__":
    main()
