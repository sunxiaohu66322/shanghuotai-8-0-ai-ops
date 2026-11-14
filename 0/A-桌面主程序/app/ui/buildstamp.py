# -*- coding: utf-8 -*-
"""构建/更新状态 + 一键重启的命令层（2026-11-09 主人要求）。

主人原话：「这个上后台你这边改了以后，它到底是直接更新，还是说要把整个上后台重启？
我都不知道我现在每次把这个软件关了以后，再打开它到底是不是最新的。」

判据只认两样硬东西，不认版本号字符串：
  ① 源码文件真实 mtime（app/**/*.py + run.pyw 的最大值）
  ② 本进程启动时间 / 打包 EXE 的 mtime
原因：PyQt 程序启动后 .py 已编译进内存，改文件不影响运行中进程；
      PyInstaller 打包的 EXE 更是只在打包那一刻装进代码，重启它也只是重读那个旧包。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
# 模块首次被导入 ≈ 进程刚起来（MainWindow 模块加载阶段），用它兜底
PROC_STARTED = time.time()


def process_started() -> float:
    """进程真实创建时间（比模块导入时间准；拿不到就退回导入时间）。"""
    try:
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD),
                        ("dwHighDateTime", wintypes.DWORD)]

        k32 = ctypes.windll.kernel32
        k32.GetProcessTimes.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME)]
        ft = [FILETIME() for _ in range(4)]
        args = [ctypes.pointer(x) for x in ft]
        if not k32.GetProcessTimes(k32.GetCurrentProcess(), *args):
            return PROC_STARTED
        t = ((ft[0].dwHighDateTime << 32) | ft[0].dwLowDateTime) / 1e7 - 11644473600
        return t if t > 1e9 else PROC_STARTED
    except Exception:                                        # noqa: BLE001
        return PROC_STARTED

# 项目源码根：源码版直接从本文件推；打包版从 exe 位置往回推，兜底用已知绝对路径
KNOWN_SRC = Path(r"G:\Users\Administrator\Documents\deepseek-harness\default-workspace\上货台")
if not FROZEN:
    SRC_ROOT = Path(__file__).resolve().parents[2]          # .../上货台
    EXE_DIR = SRC_ROOT
else:
    EXE_DIR = Path(sys.executable).resolve().parent         # .../上货台/dist/上货台
    _cands = [KNOWN_SRC]
    try:
        _cands.append(EXE_DIR.parents[1])                  # .../上货台
    except IndexError:
        pass
    SRC_ROOT = next((p for p in _cands if (p / "run.pyw").exists()), EXE_DIR)

RUN_PYW = SRC_ROOT / "run.pyw"
APP_DIR = SRC_ROOT / "app"
PYW_CANDIDATES = [
    Path(r"F:\pyenvs\douyin\Scripts\pythonw.exe"),         # 桌面快捷方式用的那个
    Path(sys.prefix) / "Scripts" / "pythonw.exe",
    Path(sys.prefix) / "pythonw.exe",
]


def fmt(ts: float) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "—"


def mode_label() -> str:
    return "打包版" if FROZEN else "源码版"


def source_max_mtime() -> tuple[float, str]:
    """源码最后修改时间 + 是哪个文件。跳过 __pycache__，只算 .py 与 run.pyw。"""
    best, who = 0.0, ""
    try:
        files = [RUN_PYW] + list(APP_DIR.rglob("*.py"))
    except Exception:                                       # noqa: BLE001
        files = [RUN_PYW]
    for f in files:
        if "__pycache__" in f.parts or not f.exists():
            continue
        try:
            m = f.stat().st_mtime
        except OSError:
            continue
        if m > best:
            best, who = m, str(f.relative_to(SRC_ROOT))
    return best, who


def exe_mtime() -> float:
    if not FROZEN:
        return 0.0
    try:
        return Path(sys.executable).stat().st_mtime
    except OSError:
        return 0.0


def status(proc_started: float | None = None) -> dict:
    """右上角状态灯要显示的全部事实。只读，不写任何文件。"""
    ps = process_started() if proc_started is None else proc_started
    sm, who = source_max_mtime()
    em = exe_mtime()
    d = {
        "mode": mode_label(), "frozen": FROZEN,
        "proc_started": ps, "src_mtime": sm, "src_file": who or "（未找到源码）",
        "exe_mtime": em, "exe": str(sys.executable), "run_pyw": str(RUN_PYW),
    }
    if FROZEN:
        d["stale_pkg"] = sm > em + 1
        d["needs_restart"] = d["stale_pkg"]
        d["summary"] = ("⚠ 打包版停在 %s，源码 %s 已更新" % (fmt(em), fmt(sm))) if d["stale_pkg"] \
            else ("✅ 已是最新 · 打包 %s" % fmt(em))
        d["hint"] = ("打包版重启也拿不到新代码；点重启将改用源码版启动。"
                     if d["stale_pkg"] else "重启打包版（同一 EXE）。")
    else:
        d["stale_pkg"] = False
        d["needs_restart"] = sm > ps + 1
        d["summary"] = ("⚠ 源码 %s 已更新，重启后生效" % fmt(sm)) if d["needs_restart"] \
            else ("✅ 已是最新 · %s 启动" % fmt(ps))
        d["hint"] = "源码改动不会热加载进运行中的程序，点重启重新读入源码。"
    return d


def _pyw() -> str:
    """找无窗 Python 解释器：优先 pythonw.exe（铁律 19，绝不用 python.exe 拉 .pyw）。"""
    for p in PYW_CANDIDATES:
        if p.exists():
            return str(p)
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    if w.exists():
        return str(w)
    return shutil.which("pythonw") or ""


def restart_plan() -> tuple[list[str] | None, str]:
    """返回 (argv, 说明)。找不到可用入口就返回 (None, 原因)，绝不瞎启动。"""
    if not FROZEN:
        pyw = _pyw()
        if not pyw:
            return None, "未找到 pythonw.exe（无窗解释器），重启会闪黑框，已拦下"
        # 源码版：用无窗 pythonw 重新读 run.pyw（sys.executable 可能是 python.exe）
        return [pyw, str(RUN_PYW)], "重启源码版（同一入口，重新读入源码）"
    # 打包版 EXE 比源码新 → 重启同一个 EXE（保住任务栏固定图标），没必要切源码版
    if not status().get("stale_pkg"):
        return [sys.executable], "重启打包版（同一 EXE）"
    # 打包版旧于源码 → 重启它一百次也拿不到新代码，必须切到源码版
    if not RUN_PYW.exists():
        return None, "未找到 run.pyw，切不到源码版"
    pyw = _pyw()
    if not pyw:
        return None, "未找到 pythonw.exe，切不到源码版"
    return [pyw, str(RUN_PYW)], "当前是打包版且已落后 → 改为源码版启动（只有它能拿到最新代码）"


def spawn(argv: list[str]) -> bool:
    """无窗启动新进程。铁律 19：pythonw 无控制台，creationflags 必须出生即给。"""
    kw = {"cwd": str(SRC_ROOT), "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        subprocess.Popen(argv, **kw)
        return True
    except Exception:                                       # noqa: BLE001
        return False
