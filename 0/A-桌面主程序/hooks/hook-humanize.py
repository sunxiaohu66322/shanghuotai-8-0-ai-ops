# -*- coding: utf-8 -*-
"""覆盖 PyPI humanize 的 contrib 钩子（2026-10-24）。

本项目里 `server\humanize.py` 是**自己写的本地模块**，和 PyPI 的 humanize 同名。
PyInstaller 的 _pyinstaller_hooks_contrib 里有个 hook-humanize.py，它会去分析
PyPI 版 humanize 的子模块；本机没装 PyPI 版，于是打包期直接抛
ImportErrorWhenRunningHook 把整个构建打断（实测 48 秒后失败）。

hookspath 的钩子优先于 contrib 的，所以这里放一个**空钩子**把那个分析跳过去，
让 PyInstaller 按普通模块处理本地 humanize。
"""
