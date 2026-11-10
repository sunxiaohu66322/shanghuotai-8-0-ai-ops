# -*- coding: utf-8 -*-
"""上货台启动器（双击这个文件即可）。

以前没有独立入口 —— 每次都是靠外部命令 `pythonw -m app...` 拉起来，
所以一换会话就找不到怎么启动。这里固定下来：
    F:\\pyenvs\\douyin\\Scripts\\pythonw.exe 上货台\\run.pyw
用 .pyw 结尾 = 不开控制台窗口，双击不闪黑框。

注意：直跑这个文件时，Windows 任务栏显示的是 pythonw.exe 的身份。
要「固定到任务栏」并显示自己的图标，请用打包出来的 上货台.exe（同目录 dist\\上货台\\）。
两条入口共用 app\\boot.py，行为完全一致。
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from app import boot  # noqa: E402


if __name__ == "__main__":
    sys.exit(boot.main())
