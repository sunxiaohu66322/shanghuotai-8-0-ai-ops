# -*- coding: utf-8 -*-
"""目录簿 —— 记住「上次从哪个文件夹导入的」，下次直接选。

主人 2026-10-23 原话（要点）：
    「整个软件里面导入的地址要加一个记录……之前从这个文件夹里导入过，这个文件夹要存一下，
     省得下回我还要去其他地方找文件夹……因为我是好几个软件，你这个如果是指定打开一个
     收件箱的话，别的软件导入起来挺麻烦」

所以这里是**多个固定目录**的簿子（不是单一收件箱）：
    · 每用一次就记一次（次数 + 最近时间）
    · 按最近使用排序，界面可以直接挑
    · 分用途记（url_pack / pack / export / image …），互不干扰

落盘：上货台\\data\\dirbook.json
"""
from __future__ import annotations

import datetime
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data")
BOOK = os.path.join(DATA, "dirbook.json")
MAX_PER_KIND = 30


def _load() -> dict:
    try:
        with open(BOOK, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception:                                   # noqa: BLE001
        pass
    return {}


def _save(d: dict) -> None:
    os.makedirs(DATA, exist_ok=True)
    tmp = BOOK + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, BOOK)


def remember(path: str, kind: str = "pack") -> None:
    """记一次目录（传文件也行，自动取它所在目录）。"""
    if not path:
        return
    p = os.path.abspath(str(path))
    if os.path.isfile(p):
        p = os.path.dirname(p)
    elif not os.path.isdir(p):
        # 传进来的是「还不存在的文件路径」也照记：取它所在的目录
        parent = os.path.dirname(p)
        if os.path.isdir(parent):
            p = parent
    if not os.path.isdir(p):
        return
    d = _load()
    arr = [x for x in (d.get(kind) or []) if isinstance(x, dict)]
    now = datetime.datetime.now().isoformat(timespec="seconds")
    # 同一秒内连记两个目录时，时间戳会打平 —— 再用自增 seq 定先后，保证「最近用的排第一」
    seq = max([int(x.get("seq") or 0) for x in arr] + [0]) + 1
    for x in arr:
        if os.path.normcase(x.get("dir", "")) == os.path.normcase(p):
            x["count"] = int(x.get("count") or 0) + 1
            x["last"] = now
            x["seq"] = seq
            break
    else:
        arr.append({"dir": p, "count": 1, "last": now, "seq": seq})
    arr.sort(key=lambda x: (int(x.get("seq") or 0), x.get("last") or "",
                            x.get("count") or 0), reverse=True)
    d[kind] = arr[:MAX_PER_KIND]
    d["_last_" + kind] = p
    _save(d)


def recent(kind: str = "pack", limit: int = 10) -> list[dict]:
    """最近用过的目录（新→旧）。顺手剔掉已经不存在的。"""
    d = _load()
    arr = [x for x in (d.get(kind) or []) if isinstance(x, dict)]
    alive = [x for x in arr if os.path.isdir(x.get("dir") or "")]
    if len(alive) != len(arr):
        d[kind] = alive
        _save(d)
    return alive[:limit]


def start_dir(kind: str = "pack", fallback: str = "") -> str:
    """给文件对话框用的起始目录：最近一个还存在的；没有就用 fallback。"""
    for x in recent(kind):
        return x["dir"]
    return fallback if (fallback and os.path.isdir(fallback)) else os.path.expanduser("~")


def forget(path: str, kind: str = "pack") -> bool:
    p = os.path.abspath(str(path))
    if os.path.isfile(p):
        p = os.path.dirname(p)
    d = _load()
    arr = [x for x in (d.get(kind) or []) if isinstance(x, dict)]
    keep = [x for x in arr if os.path.normcase(x.get("dir", "")) != os.path.normcase(p)]
    if len(keep) == len(arr):
        return False
    d[kind] = keep
    _save(d)
    return True


def clear(kind: str = "") -> None:
    d = _load()
    if kind:
        d.pop(kind, None)
        d.pop("_last_" + kind, None)
    else:
        d.clear()
    _save(d)
