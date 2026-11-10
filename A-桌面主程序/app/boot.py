# -*- coding: utf-8 -*-
"""上货台启动引导 —— run.pyw（开发/直跑）和打包后的 上货台.exe 共用这一段。

为什么要这个文件（任务栏那条需求）：
    Windows 任务栏按钮的身份是 **进程的 AppUserModelID**，不是窗口标题。
    用 pythonw.exe 跑脚本时，Windows 眼里这个进程就是 pythonw.exe，
    于是 ① 图标显示成 Python ② 右键「固定到任务栏」钉住的是 pythonw.exe，
    再点它启动的是「空参数的 pythonw」，什么都不会开。
    这里做两件事把它掰正：
        1) SetCurrentProcessExplicitAppUserModelID —— 给进程一个自己的身份
        2) QApplication.setWindowIcon —— 给窗口/任务栏一个自己的图标
    打包成真 exe（PyInstaller）之后，Windows 默认就用 exe 路径当身份，
    固定到任务栏跟微信、记事本这类普通程序完全一样。
"""
from __future__ import annotations

import sys
from pathlib import Path

APP_ID = "YG.ShangHuoTai.0.5.2"
ICON_NAME = "上货台.ico"


def set_app_id(app_id: str = APP_ID) -> bool:
    """给当前进程一个独立的 AppUserModelID（失败不致命）。"""
    try:
        import ctypes
        from ctypes import wintypes

        fn = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        fn.argtypes = [wintypes.LPCWSTR]
        fn.restype = ctypes.c_long
        return fn(app_id) == 0
    except Exception:
        return False


def get_app_id() -> str | None:
    """读回当前进程的 AppUserModelID（自检用）。"""
    try:
        import ctypes
        from ctypes import wintypes

        buf = ctypes.create_unicode_buffer(512)
        n = wintypes.DWORD(0)
        rc = ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID(
            ctypes.byref(buf), ctypes.byref(n)
        )
        if rc != 0:
            return None
        return buf.value or None
    except Exception:
        return None


def icon_path() -> Path | None:
    """找图标：打包后在 _MEIPASS/app/assets，直跑在 上货台/app/assets。"""
    try:
        from . import paths
        for c in (paths.ASSETS / ICON_NAME, paths.ROOT / "app" / "assets" / ICON_NAME):
            if c.exists():
                return c
    except Exception:
        pass
    return None


def main() -> int:
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication

    # ★ 6.0 内嵌浏览器（QtWebEngine）的硬要求：QtWebEngineWidgets 必须在
    #   QApplication **之前**被导入（或至少设好 AA_ShareOpenGLContexts），
    #   否则等到 6.0 页签懒加载时再 import 会抛
    #   「QtWebEngineWidgets must be imported ... before a QCoreApplication instance is created」。
    #   这里提前设一次；没装 WebEngine 时静默跳过，6.0 页签自己会显示提示。
    try:
        from PyQt6.QtCore import Qt as _Qt
        QApplication.setAttribute(_Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    except Exception:                              # noqa: BLE001
        pass
    try:
        from PyQt6 import QtWebEngineWidgets   # noqa: F401
    except Exception:                              # noqa: BLE001
        pass

    # ★ 崩溃守夜器：必须最早装（在 QApplication 之前），
    #   否则启动阶段的崩溃又会无声消失。见 app\crashguard.py 顶部说明。
    from . import crashguard
    _crash = crashguard.install()

    from . import theme
    from .ui.main_window import MainWindow

    # 只有在「非打包」直跑时才设显式 AppID。
    # 打包成真 exe 后不要设：Windows 会用 exe 路径当身份，
    # 这样「固定到任务栏」的快捷方式和运行中的窗口会自动合并成一个按钮；
    # 一旦设了显式 AppID 而快捷方式里没写同一个值，就会出现两个按钮。
    if not getattr(sys, "frozen", False):
        set_app_id()

    app = QApplication(sys.argv)
    app.setApplicationName("上货台")
    # 不要设 setApplicationDisplayName —— 它会被 Qt 自动拼到窗口标题后面，
    # 变成「上货台 v0.5.2 - 上货台」，难看。
    app.setOrganizationName("YG")
    ic = icon_path()
    if ic:
        app.setWindowIcon(QIcon(str(ic)))
    app.setStyleSheet(theme.STYLE)

    win = MainWindow()
    win.resize(theme.DEFAULT_W, theme.DEFAULT_H)
    win.show()
    try:
        crashguard.beat("主窗口已显示，等待用户操作")
    except Exception:                              # noqa: BLE001
        pass
    return app.exec()
