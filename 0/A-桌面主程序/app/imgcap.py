# -*- coding: utf-8 -*-
"""图片解码集中管制 —— 崩溃加固（2026-10-28 主人选 B：按经验先加固三处）

【为什么有这个东西，证据是什么】
Windows 事件日志（Application, EventID 1000）对本程序记录到三次真崩溃：
    12:32:23  ntdll.dll    0xc0000374   ← 堆损坏（HEAP_CORRUPTION）
    12:37:48  ntdll.dll    0xc0000374
    19:36:53  Qt6Gui.dll   0xc0000005   ← 访问冲突
另有 EventID 1001 `RADAR_PRE_LEAK_64`（内存泄漏预警）。

图片解码全部落在 C 库里（libjpeg / libpng / PIL / cv2 / onnxruntime 预处理），
是「堆损坏 / 访问冲突」这类崩溃最常见的来源；把风险放大成崩溃的两个条件恰好我们都有：
    ① 多个线程同时解码（QThreadPool 4 并发 + GUI 线程再解一张）
    ② 一次性解码全尺寸大图（`QImage(path)` / `QPixmap(path)` 都是全尺寸）
本模块只做三件能自证的事 —— **不声称已修好根因**，只把这两个放大条件按下去：

    ① 并发闸门：同一时刻真正在解码的线程 ≤ MAX_CONCURRENT（默认 2）
    ② 巨图拦截：像素数 > MAX_PIXELS 不解，记 last_err，界面显示「图太大」
    ③ 缩放解码：先读文件头拿尺寸，再让解码器直接产出目标边长
       （QImageReader.setScaledSize 由 libjpeg 缩，不经过全尺寸像素缓冲）

【硬约束】
· **QPixmap 只能在 GUI 线程建**（Qt 文档明写；子线程建 QPixmap 是经典崩溃源）。
  → 子线程一律用 decode() 拿 QImage；pixmap() 会检查线程，错用返回空并记一笔。
· GUI 线程解码时若闸门已被工作线程占满，**最多等 UI_WAIT 秒就让路自己解**
  （GUI 线程天然只有一张在解，最坏并发 = MAX_CONCURRENT + 1，比死等不动界面）——
  宁可多一张并发，也不让界面卡死。
"""

from __future__ import annotations

import os
import threading
import time

from PyQt6.QtCore import QSize, QThread
from PyQt6.QtGui import QImage, QImageReader, QPixmap

# ---------------------------------------------------------------- 可调参数

MAX_CONCURRENT = 2               # 同时解码上限（原来 QThreadPool 是 4，再加 GUI 一张）
MAX_PIXELS = 40_000_000          # 单图像素上限（40MP ≈ 7300×5480）；超过不解，直接判不可用
UI_WAIT = 1.5                    # GUI 线程等闸门最多这么久（秒），超时让路
_READ_TIMEOUT = 60.0             # 工作线程等闸门的上限，防死锁

_SEM = threading.BoundedSemaphore(MAX_CONCURRENT)
_LOCK = threading.Lock()
_ST = {
    "decodes": 0,        # 真正解出来的张数
    "skipped_big": 0,    # 因超过 MAX_PIXELS 被跳过
    "errors": 0,         # 解码失败（坏文件 / 格式不支持）
    "timeouts": 0,       # 等闸门超时（说明并发打满）
    "wait_s": 0.0,       # 累计等待秒数（诊断并发压力）
    "last_err": "",
    "max_conc": 0,       # 峰值并发（自证闸门真的生效）
    "_live": 0,
}


def _bump(k: str, n: int = 1) -> None:
    with _LOCK:
        _ST[k] = _ST.get(k, 0) + n


def _note(msg: str) -> None:
    with _LOCK:
        _ST["last_err"] = msg[:300]


def stats() -> dict:
    """诊断用快照（不含 _live，避免调用方误用）。"""
    with _LOCK:
        return {k: v for k, v in _ST.items() if not k.startswith("_")}


# ---------------------------------------------------------------- 尺寸（只读文件头）

def size_of(path: str) -> QSize:
    """只读文件头拿尺寸；读不到返回无效 QSize（调用方按「未知」处理）。"""
    if not path or not os.path.isfile(path):
        return QSize()
    try:
        rd = QImageReader(path)
        rd.setAutoTransform(True)
        sz = rd.size()
        return sz if (sz.isValid() and sz.width() > 0 and sz.height() > 0) else QSize()
    except Exception:                       # noqa: BLE001
        return QSize()


def is_too_big(path: str) -> bool:
    sz = size_of(path)
    return bool(sz.isValid() and (sz.width() * sz.height()) > MAX_PIXELS)


# ---------------------------------------------------------------- 闸门

class _Gate:
    """上下文管理器形式的闸门（给必须自己调用 QImageReader 的调用方）。"""

    def __init__(self, ui: bool = False):
        self.ui = bool(ui)
        self.held = False

    def __enter__(self):
        t0 = time.time()
        wait = UI_WAIT if self.ui else _READ_TIMEOUT
        self.held = _SEM.acquire(timeout=wait)
        dt = time.time() - t0
        with _LOCK:
            _ST["wait_s"] = round(_ST["wait_s"] + dt, 3)
            if self.held:
                _ST["_live"] += 1
                if _ST["_live"] > _ST["max_conc"]:
                    _ST["max_conc"] = _ST["_live"]
        if not self.held:
            # UI 线程让路自己解；工作线程超时也照解（宁可并发超一点，不能永久卡住）
            _bump("timeouts")
            with _LOCK:
                _ST["_live"] += 1
                if _ST["_live"] > _ST["max_conc"]:
                    _ST["max_conc"] = _ST["_live"]
        return self

    def __exit__(self, *exc):
        with _LOCK:
            _ST["_live"] = max(0, _ST["_live"] - 1)
        if self.held:
            try:
                _SEM.release()
            except Exception:               # noqa: BLE001
                pass
        return False


def gate(ui: bool = False) -> _Gate:
    return _Gate(ui=ui)


# ---------------------------------------------------------------- 解码

def decode(path: str, max_side: int = 0, ui: bool = False) -> QImage:
    """解码成 QImage（**任何线程都能调**）。

    max_side > 0  → 让解码器直接产出「长边 ≤ max_side」的像素（省掉全尺寸缓冲）
    max_side = 0  → 原尺寸（仍受 MAX_PIXELS 拦截）
    返回空 QImage 表示不可用（文件没了 / 太大 / 坏文件），原因在 stats()['last_err']。
    """
    if not path or not os.path.isfile(path):
        _note("文件不存在：%s" % path)
        return QImage()
    try:
        rd = QImageReader(path)
        rd.setAutoTransform(True)
        sz = rd.size()
        if sz.isValid() and sz.width() > 0 and sz.height() > 0:
            if (sz.width() * sz.height()) > MAX_PIXELS:
                _bump("skipped_big")
                _lim = ("%d 像素" % MAX_PIXELS) if MAX_PIXELS < 1_000_000 \
                    else ("%.0f MP" % (MAX_PIXELS / 1e6))
                _note("图太大跳过：%d×%d（%.1f MP > %s）%s"
                      % (sz.width(), sz.height(),
                         (sz.width() * sz.height()) / 1e6, _lim,
                         os.path.basename(path)))
                return QImage()
            if max_side and max(sz.width(), sz.height()) > int(max_side):
                k = int(max_side) / float(max(sz.width(), sz.height()))
                rd.setScaledSize(QSize(max(1, int(sz.width() * k)),
                                       max(1, int(sz.height() * k))))
        with gate(ui=ui):
            img = rd.read()
        if img.isNull():
            _bump("errors")
            _note("解码失败：%s（%s）" % (os.path.basename(path), rd.errorString()))
            return QImage()
        _bump("decodes")
        return img
    except MemoryError:
        _bump("errors")
        _note("内存不足：%s" % os.path.basename(path))
        return QImage()
    except Exception as e:                  # noqa: BLE001
        _bump("errors")
        _note("%s: %s（%s）" % (type(e).__name__, e, os.path.basename(path)))
        return QImage()


def is_gui_thread() -> bool:
    """当前是否 GUI 主线程（QPixmap 只能在这上面建）。"""
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        return app is None or QThread.currentThread() is app.thread()
    except Exception:                       # noqa: BLE001
        return False


def pixmap(path: str, max_side: int = 0) -> QPixmap:
    """**只能在 GUI 线程调用**；子线程错用会返回空 QPixmap 并记一笔（不崩）。"""
    if not is_gui_thread():
        _bump("errors")
        _note("pixmap() 在非 GUI 线程被调用（应改用 decode()）：%s" % os.path.basename(path or ""))
        return QPixmap()
    img = decode(path, max_side=max_side, ui=True)
    if img.isNull():
        return QPixmap()
    try:
        return QPixmap.fromImage(img)
    except Exception as e:                  # noqa: BLE001
        _bump("errors")
        _note("fromImage 失败：%s: %s" % (type(e).__name__, e))
        return QPixmap()
