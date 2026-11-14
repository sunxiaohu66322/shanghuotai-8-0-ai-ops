# -*- coding: utf-8 -*-
"""v3 · 类目映射（D-35）

事实基线（2026-10-25 实测，12/12 件商品）：
  · 原始 JSON `_extra.catId` **全部有值**，形如 `6932/7117/546488`
    （与 `_raw.category[].catId`、`_raw.nodeCatId` 一致）→ 可**安全回填** `v3_product.category_id`
  · 但源数据里**没有任何中文类目名**（`_raw` / `_extra` 均无含中文的 cat* 字段）
    → `category_path`（平台表格要的中文分类）**必须靠映射表**，不能猜

所以本模块只做三件事：
  ① `ensure_table()`     建 `v3_category_map`（人工映射，可改可清）
  ② `backfill_ids()`     把 catId 链回填进 `category_id`（幂等、只填空值）
  ③ `apply_map()`        把映射结果写回 `category_path`（按平台分别可查）

**绝不根据标题猜平台类目**：没有映射就是空，并在导出报告里点名。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from . import store

PLATFORMS = ("wechat", "doudian")

DDL = """
CREATE TABLE IF NOT EXISTS v3_category_map (
    map_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    source_cat_id TEXT NOT NULL,          -- 来源叶子类目 ID（如 546488）
    source_path   TEXT,                   -- 来源类目 ID 链（如 6932/7117/546488）
    platform      TEXT NOT NULL,          -- wechat | doudian
    target_name   TEXT NOT NULL,          -- 目标平台类目中文名/路径
    target_id     TEXT,                   -- 目标平台类目 ID（有就存）
    note          TEXT,
    updated_at    TEXT,
    UNIQUE(source_cat_id, platform)
)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(DDL)
    conn.execute("CREATE INDEX IF NOT EXISTS ix_catmap_src ON v3_category_map(source_cat_id)")
    conn.commit()


# ---------------------------------------------------------------- 来源提取

def source_cats(conn: sqlite3.Connection) -> list[dict]:
    """按**来源叶子类目**聚合：哪些商品落在同一个类目里。

    只读 `original_payload._extra.catId`（真实字段），读不到就报未知，不猜。
    """
    out: dict[str, dict] = {}
    for r in conn.execute("SELECT product_id, original_payload FROM v3_product"):
        pid = r["product_id"]
        path = ""
        try:
            raw = json.loads(r["original_payload"] or "{}")
            ex = raw.get("_extra") or {}
            path = str(ex.get("catId") or "")
            if not path:
                rw = ex.get("_raw") or {}
                cats = rw.get("category") or []
                if isinstance(cats, list) and cats:
                    path = "/".join(str(c.get("catId") or "") for c in cats if isinstance(c, dict))
        except Exception:      # noqa: BLE001
            path = ""
        leaf = path.split("/")[-1] if path else ""
        key = leaf or "(未知)"
        d = out.setdefault(key, {"source_cat_id": key, "source_path": path,
                                 "product_ids": []})
        d["product_ids"].append(int(pid))
    maps = {}
    try:
        for m in conn.execute("SELECT * FROM v3_category_map"):
            maps[(m["source_cat_id"], m["platform"])] = dict(m)
    except sqlite3.OperationalError:
        maps = {}
    for leaf, d in out.items():
        d["count"] = len(d["product_ids"])
        d["mapped"] = {p: (maps.get((leaf, p)) or {}).get("target_name") or ""
                       for p in PLATFORMS}
        d["complete"] = all(d["mapped"][p] for p in PLATFORMS)
    return sorted(out.values(), key=lambda x: -x["count"])


# ---------------------------------------------------------------- 回填 / 应用

def backfill_ids(conn: sqlite3.Connection, dry_run: bool = True) -> dict:
    """把 `_extra.catId` 回填进 `v3_product.category_id`（只填空值，不覆盖人工值）。"""
    todo, skipped = [], 0
    for r in conn.execute("SELECT product_id, category_id, original_payload FROM v3_product"):
        if (r["category_id"] or "").strip():
            skipped += 1
            continue
        path = ""
        try:
            ex = (json.loads(r["original_payload"] or "{}").get("_extra") or {})
            path = str(ex.get("catId") or "")
        except Exception:      # noqa: BLE001
            path = ""
        if path:
            todo.append((str(r["product_id"]), path))
    if not dry_run and todo:
        for pid, path in todo:
            conn.execute("UPDATE v3_product SET category_id=?, updated_at=?"
                         " WHERE product_id=? AND (category_id IS NULL OR category_id='')",
                         (path, store.now(), int(pid)))
        conn.commit()
    return {"ok": True, "dry_run": dry_run, "filled": len(todo),
            "skipped_has_value": skipped, "samples": todo[:5]}


def apply_map(conn: sqlite3.Connection, platform: str = "wechat",
              dry_run: bool = True) -> dict:
    """按映射表把 `category_path` 写成目标平台中文类目（只写有映射的商品）。"""
    if platform not in PLATFORMS:
        return {"ok": False, "error": "platform 只能是 %s" % "/".join(PLATFORMS)}
    ensure_table(conn)
    cats = {c["source_cat_id"]: c for c in source_cats(conn)}
    rows, miss = [], 0
    for leaf, c in cats.items():
        m = conn.execute("SELECT target_name FROM v3_category_map WHERE source_cat_id=?"
                         " AND platform=?", (leaf, platform)).fetchone()
        name = (m["target_name"] if m else "") or ""
        if not name:
            miss += len(c["product_ids"])
            continue
        rows.append((leaf, name, c["product_ids"]))
    if not dry_run and rows:
        for leaf, name, pids in rows:
            for pid in pids:
                conn.execute("UPDATE v3_product SET category_path=?, category_confidence=?,"
                             " updated_at=? WHERE product_id=?",
                             (name, 1.0, store.now(), pid))
        conn.commit()
    return {"ok": True, "dry_run": dry_run, "platform": platform,
            "applied_products": sum(len(x[2]) for x in rows),
            "unmapped_products": miss, "groups": len(rows)}


def set_map(conn: sqlite3.Connection, source_cat_id: str, platform: str,
            target_name: str, target_id: str = "", note: str = "") -> dict:
    """人工填/改/清一条映射（target_name 传空 = 清除这条）。"""
    ensure_table(conn)
    src = str(source_cat_id or "").strip()
    if not src:
        return {"ok": False, "error": "来源类目不能为空"}
    if platform not in PLATFORMS:
        return {"ok": False, "error": "platform 只能是 %s" % "/".join(PLATFORMS)}
    name = str(target_name or "").strip()
    if not name:
        conn.execute("DELETE FROM v3_category_map WHERE source_cat_id=? AND platform=?",
                     (src, platform))
        conn.commit()
        return {"ok": True, "cleared": True, "source_cat_id": src, "platform": platform}
    path = ""
    for c in source_cats(conn):
        if c["source_cat_id"] == src:
            path = c["source_path"]
            break
    conn.execute(
        "INSERT INTO v3_category_map(source_cat_id, source_path, platform, target_name,"
        " target_id, note, updated_at) VALUES(?,?,?,?,?,?,?)"
        " ON CONFLICT(source_cat_id, platform) DO UPDATE SET"
        " target_name=excluded.target_name, target_id=excluded.target_id,"
        " note=excluded.note, source_path=excluded.source_path,"
        " updated_at=excluded.updated_at",
        (src, path, platform, name, str(target_id or ""), note, store.now()))
    conn.commit()
    return {"ok": True, "source_cat_id": src, "platform": platform,
            "target_name": name, "target_id": str(target_id or "")}


def list_map(conn: sqlite3.Connection) -> list[dict]:
    ensure_table(conn)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM v3_category_map ORDER BY source_cat_id, platform")]


# ---------------------------------------------------------------- 导出前校验

def validate(conn: sqlite3.Connection, pids: list[int] | None = None) -> dict:
    """导出前校验：哪些商品缺目标平台类目。缺就点名，不给默认值。"""
    pids = [int(x) for x in (pids or [])]
    q = "SELECT product_id, source_product_id, category_id, category_path," \
        " original_title FROM v3_product"
    args: list = []
    if pids:
        q += " WHERE product_id IN (%s)" % ",".join("?" * len(pids))
        args = pids
    missing = []
    for r in conn.execute(q, args):
        cp = (r["category_path"] or "").strip()
        if not cp:
            missing.append({"product_id": r["product_id"],
                            "source_product_id": r["source_product_id"],
                            "category_id": r["category_id"] or "",
                            "title": (r["original_title"] or "")[:24]})
    return {"ok": True, "total": len(pids) or None, "missing": len(missing),
            "rows": missing,
            "note": "" if not missing
            else "这些商品没有目标平台类目，不能算「可直接上架」；先在 4.2 做类目映射"}
