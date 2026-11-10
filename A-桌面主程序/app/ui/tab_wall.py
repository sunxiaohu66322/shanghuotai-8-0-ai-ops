# -*- coding: utf-8 -*-
"""2.5 · 本地商品库（照片墙）—— 第二步「商品卡导入」与第三步「商品精筛」之间。

定位：第二步负责把 JSON 变成本地商品档案，本页负责「看得见、能挑、能核对」，
第三步负责按证据做判定。

三条硬约束（与 2.5 设计一致）：
  ① 只读打开生产库（sqlite `mode=ro`），绝不写库、绝不改原图
  ② 缩略图缓存落在 data\\thumbs（与 _stageB 的隔离目录分开）
  ③ 本页任何按钮都不许「假装做过」——没实现的按钮由 wall.py 置灰

失败不影响主程序启动：本页构造失败时显示一行原因，其余页照常。
"""
from __future__ import annotations

import os
import sys

from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .. import paths

# 生产库与缩略图缓存
DB_PATH = os.path.join(str(paths.TAOBAO_DIR), "data", "v3.db")
THUMB_DIR = os.path.join(str(paths.DATA), "thumbs")


class WallStepTab(QWidget):
    """把 WallPage 包成主窗口的一个页签。"""

    def __init__(self, win=None):
        super().__init__()
        self.win = win
        self.page = None
        self.err = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        try:
            # server 包（Library）可能在 bridge._v3() 之前还没进 sys.path —— 自己兜一层
            for p in (str(paths.TAOBAO_DIR), str(paths.TAOBAO_SERVER)):
                if p not in sys.path:
                    sys.path.insert(0, p)
            from .wall import WallPage
            if not os.path.exists(DB_PATH):
                raise FileNotFoundError(f"生产库不存在：{DB_PATH}")
            os.makedirs(THUMB_DIR, exist_ok=True)
            self.page = WallPage(DB_PATH, thumb_dir=THUMB_DIR, parent=self)
            lay.addWidget(self.page)
        except Exception as e:  # noqa: BLE001
            self.err = f"{type(e).__name__}: {e}"
            lay.addWidget(QLabel(f"商品库页面加载失败：{self.err}\n库：{DB_PATH}"))

    # ---------- 主窗口会调 ----------
    def refresh(self):
        """每次切到本页都刷新 —— 商品库必须跟着「2 · 商品卡导入」一起更新。

        ★ 2026-10-25 主人口径：「商品库它的链接应该会对着商品卡导入更新的，
          它也没有……有一个刷新，刷新以后就有了」。
        原因是这里原来有个一次性门闩：

            if getattr(self, "_refreshed", False):   # 第一次之后永不刷新
                return

        ⇒ 新导入的商品在 2.5 里看不到，必须手动 F5 或重开程序。
        现在改成每次都刷；代价是切页时重扫一次树和墙（本地 SQLite，几十毫秒级），
        换来的是「导入完切过来就能看见」。
        """
        if self.page is None:
            return
        try:
            self.page._fill_tree()
            self.page._fill_wall()
        except Exception:  # noqa: BLE001
            pass

    def summary(self) -> str:
        if self.page is None:
            return "商品库 未就绪"
        try:
            return "商品库 %d 件" % len(getattr(self.page, "rows", []) or [])
        except Exception:  # noqa: BLE001
            return "商品库"
