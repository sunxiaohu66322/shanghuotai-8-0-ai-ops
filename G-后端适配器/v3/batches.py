# -*- coding: utf-8 -*-
"""5.0 · 上架批次台账（哪天上了哪些货）

主人原话（2026-10-27）：
  「每一批产品它需要保存。比如说这一批上架的，我这边上完以后，它要新建一个 tab 页，
    tab 页的名称就是今天的名称。我要知道哪天上架哪个产品又有个记录，
    而就是说下一次录入商品上架以后，它又换了新的。」

设计：
  · 一次「建草稿」成功（无论全成还是部分失败）= 一条批次记录
  · 名称形如 `2026-10-27 第2批`（同日递增），界面按日期分页签
  · 明细存 json：每件 product_id / 标题 / 微信商品ID / 成功失败 / 失败原因
  · 只追加、不改历史：下次上货是新的一条，旧批次原样留着

为什么单独一张表而不是塞进 v3_product.note：
  note 是"这件货现在什么状态"，批次是"什么时候、按什么规则、上了哪一批"，
  两件事混在一起，改一次重筛就把历史覆盖掉了。
"""
from __future__ import annotations

import json
import sqlite3
import time

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS v3_upload_batch (
        batch_id      INTEGER PRIMARY KEY AUTOINCREMENT,
        name          TEXT NOT NULL,
        day           TEXT NOT NULL,
        pack_code     TEXT DEFAULT '',
        template_id   TEXT DEFAULT '',
        markup        REAL DEFAULT 1.0,
        profit_pct    REAL DEFAULT 0,
        stock_per_sku INTEGER DEFAULT 0,
        total         INTEGER DEFAULT 0,
        created       INTEGER DEFAULT 0,
        failed        INTEGER DEFAULT 0,
        items_json    TEXT DEFAULT '[]',
        note          TEXT DEFAULT '',
        created_at    TEXT DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS idx_v3_batch_day ON v3_upload_batch(day, batch_id)",
]


def ensure_table(c: sqlite3.Connection) -> None:
    for sql in SCHEMA:
        c.execute(sql)


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def save(c: sqlite3.Connection, *, pack_code: str = "", template_id: str = "",
         markup: float = 1.0, profit_pct: float = 0.0, stock_per_sku: int = 0,
         items: list | None = None, created: int = 0, failed: int = 0,
         note: str = "") -> dict:
    """写一条批次。同日第几批自动算（按已有条数 +1）。返回记录。"""
    ensure_table(c)
    day = _today()
    n = c.execute("SELECT COUNT(*) FROM v3_upload_batch WHERE day=?", (day,)).fetchone()[0] or 0
    name = "%s 第%d批" % (day, int(n) + 1)
    items = items or []
    cur = c.execute(
        "INSERT INTO v3_upload_batch(name, day, pack_code, template_id, markup, profit_pct,"
        " stock_per_sku, total, created, failed, items_json, note, created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (name, day, pack_code or "", str(template_id or ""), float(markup or 1.0),
         float(profit_pct or 0.0), int(stock_per_sku or 0),
         len(items), int(created or 0), int(failed or 0),
         json.dumps(items, ensure_ascii=False),
         (note or "")[:480], time.strftime("%Y-%m-%d %H:%M:%S")))
    c.commit()
    return {"ok": True, "batch_id": cur.lastrowid, "name": name, "day": day,
            "total": len(items), "created": int(created or 0), "failed": int(failed or 0)}


def _row2dict(r) -> dict:
    d = dict(r)
    try:
        d["items"] = json.loads(d.get("items_json") or "[]")
    except Exception:                       # noqa: BLE001
        d["items"] = []
    d.pop("items_json", None)
    return d


def lst(c: sqlite3.Connection, limit: int = 60) -> dict:
    """批次列表（新的在前）。items 不返回，省流量；要看明细用 get()。"""
    ensure_table(c)
    rows = c.execute(
        "SELECT batch_id, name, day, pack_code, markup, profit_pct, total, created, failed,"
        " created_at FROM v3_upload_batch ORDER BY batch_id DESC LIMIT ?",
        (int(limit or 60),)).fetchall()
    return {"ok": True, "items": [dict(r) for r in rows]}


def get(c: sqlite3.Connection, batch_id: int) -> dict:
    ensure_table(c)
    r = c.execute("SELECT * FROM v3_upload_batch WHERE batch_id=?",
                  (int(batch_id),)).fetchone()
    if not r:
        return {"ok": False, "error": "批次不存在：%s" % batch_id}
    d = _row2dict(r)
    d["ok"] = True
    return d
