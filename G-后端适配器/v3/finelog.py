# -*- coding: utf-8 -*-
"""v3 · 精筛运行日志（JSONL 结构化留痕）

为什么单独一个模块：精筛的「可观测」与「以后净化」是两件事 ——
前者要给人看（人话一行一件），后者要给机器看（每个事件一条结构化记录）。
把它塞进 flow.py 会让那个已经很长的门面更乱，所以这里只管**落盘一条通道**。

一行一个事件，字段与 fine_screen(progress=...) 回调收到的 dict **完全一致**，
只多一个 ts。落盘位置：

    taobao-picker/data/v3/logs/fine_<YYYYmmdd-HHMMSS>.jsonl

铁律：
  · 只追加，不覆盖 —— 同一秒跑两次就加序号，上一份绝不被改
  · 逐行 flush —— 进程被 Ctrl-C / 被杀，已经处理过的件不会凭空消失
  · 写日志失败**不许影响精筛**（调用方用 _emit 包住，异常一律吞）
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime

from . import store

LOG_DIR = os.path.join(store.DATA_DIR, "v3", "logs")


def new_path(when: datetime | None = None) -> str:
    """给一个不撞车的日志路径（目录不存在就建）。"""
    d = when or datetime.now()
    os.makedirs(LOG_DIR, exist_ok=True)
    stamp = d.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(LOG_DIR, "fine_%s.jsonl" % stamp)
    n = 1
    while os.path.exists(path):
        path = os.path.join(LOG_DIR, "fine_%s-%d.jsonl" % (stamp, n))
        n += 1
    return path


class FineLog:
    """一次精筛运行一份日志文件。用一次就关，别跨批复用。"""

    def __init__(self, path: str | None = None):
        self.path = path or new_path()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._f = open(self.path, "a", encoding="utf-8", newline="\n")
        self._lock = threading.Lock()
        self.lines = 0
        self.started = time.time()

    def emit(self, event: dict) -> dict:
        """写一行，返回真正落盘的记录（带 ts）。"""
        rec = dict(event)
        rec.setdefault("ts", store.now())
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with self._lock:
            self._f.write(line + "\n")
            self._f.flush()          # 逐行落盘：中断也要留得住
            self.lines += 1
        return rec

    def close(self) -> None:
        try:
            with self._lock:
                if not self._f.closed:
                    self._f.close()
        except Exception:            # noqa: BLE001
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):        # noqa: D401
        self.close()
        return False
