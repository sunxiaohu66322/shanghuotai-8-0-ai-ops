# -*- coding: utf-8 -*-
"""v3 · 总数据状态库（2026-11-04）

一张表回答三个问题，一件商品一行：

    ① 它在不在「总数据」里（精筛过了没有）
    ② 抖音上了没有 —— 成功 / 失败 / 为什么
    ③ 微信上了没有 —— 成功 / 失败 / 为什么

设计原则（照主人口径）：
  · **两侧独立**——「肯定是有差别的，有些他就是上不去，这也没办法」：
    抖音失败不影响微信，反之亦然，所以两列状态分开记、分开算；
  · **只记结果，不替任何一侧下判断**：这里不做重试、不做补传、不改商品；
  · **一个商品一行**（product_id 主键），重跑幂等，不会越跑越多；
  · **匹配按标题，不按文件名**：抖音/微信回来的清单里没有我们的 pid，
    只能靠标题认亲；认不出就留 none，**不许猜一个 pid 填上去**。

用法（命令行）：
    python status.py sync                 # 从 v3_product 灌总数据
    python status.py dy <json|csv>        # 导入抖音结果（标题/状态/原因）
    python status.py wx <json>            # 导入微信结果
    python status.py stats                # 出统计
"""
from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import sys

from . import store

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS v3_upload_status (
    product_id    INTEGER PRIMARY KEY,
    pack_code     TEXT DEFAULT '',
    title         TEXT DEFAULT '',
    fine_state    TEXT DEFAULT '',   -- passed 精筛通过 / blocked 被拦 / pending 未筛
    dy_state      TEXT DEFAULT 'none',  -- none 未上 / success 成功 / fail 失败 / draft 草稿
    dy_tid        TEXT DEFAULT '',   -- 抖音商品 ID
    dy_reason     TEXT DEFAULT '',   -- 失败原因（原文摘要）
    dy_at         TEXT DEFAULT '',
    wx_state      TEXT DEFAULT 'none',
    wx_product_id TEXT DEFAULT '',
    wx_reason     TEXT DEFAULT '',
    wx_at         TEXT DEFAULT '',
    note          TEXT DEFAULT '',
    updated_at    TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_v3us_dy ON v3_upload_status(dy_state);
CREATE INDEX IF NOT EXISTS ix_v3us_wx ON v3_upload_status(wx_state);
CREATE INDEX IF NOT EXISTS ix_v3us_fine ON v3_upload_status(fine_state);
"""

FINE_MAP = {"fine_screened": "passed", "passed": "passed",
            "blocked": "blocked", "pending": "pending"}


def ensure(conn: sqlite3.Connection) -> None:
    for stmt in TABLE_SQL.split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()


# ---------------------------------------------------------------- 标题认亲

def _norm(t: str) -> str:
    """标题规范化：去掉空白与标点，只留中日韩与字母数字，便于模糊比对。"""
    return re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", (t or ""))


def match_pid(conn: sqlite3.Connection, title: str, minlen: int = 10) -> int | None:
    """按标题找 pid。**宁可不认，也不许认错** —— 认错的代价是把别人算成上架成功。

    三轮：
      ① 规范化后完全相等；
      ② 一方包含另一方，且重叠部分 ≥ minlen；
      ③ 前 18 字相等（与 genplan.candidates 去重用同一个口径）。
    """
    nt = _norm(title)
    if len(nt) < 6:
        return None
    rows = conn.execute("SELECT product_id, original_title FROM v3_product"
                        " WHERE COALESCE(original_title,'')<>''").fetchall()
    cand = [(int(r[0]), _norm(r[1])) for r in rows]
    for pid, nt2 in cand:                      # ① 完全相等
        if nt2 and nt2 == nt:
            return pid
    best = None
    for pid, nt2 in cand:                      # ② 包含
        if not nt2:
            continue
        short, long_ = (nt, nt2) if len(nt) <= len(nt2) else (nt2, nt)
        if short in long_ and len(short) >= minlen:
            if best is None or len(short) > best[0]:
                best = (len(short), pid)
    if best:
        return best[1]
    for pid, nt2 in cand:                      # ③ 前 18 字
        if nt2[:18] and nt2[:18] == nt[:18]:
            return pid
    return None


# ---------------------------------------------------------------- 灌总数据

def sync_products(conn: sqlite3.Connection, pack_code: str = "") -> dict:
    """从 `v3_product` 建/刷新总数据行。**只增改这一张表，不动 v3_product。**"""
    ensure(conn)
    where, args = "", []
    if pack_code:
        where = " WHERE COALESCE(pack_code,'')=?"
        args = [pack_code]
    rows = conn.execute(
        "SELECT product_id, original_title, pack_code, status FROM v3_product"
        + where, args).fetchall()
    new = upd = 0
    for r in rows:
        pid = int(r["product_id"])
        fine = FINE_MAP.get((r["status"] or "").strip(), r["status"] or "pending")
        existed = conn.execute("SELECT 1 FROM v3_upload_status WHERE product_id=?",
                               (pid,)).fetchone()
        if existed:
            conn.execute("UPDATE v3_upload_status SET pack_code=?, title=?, fine_state=?,"
                         " updated_at=? WHERE product_id=?",
                         (r["pack_code"] or "", r["original_title"] or "", fine,
                          store.now(), pid))
            upd += 1
        else:
            conn.execute("INSERT INTO v3_upload_status(product_id, pack_code, title,"
                         " fine_state, updated_at) VALUES(?,?,?,?,?)",
                         (pid, r["pack_code"] or "", r["original_title"] or "", fine,
                          store.now()))
            new += 1
    conn.commit()
    return {"ok": True, "products": len(rows), "inserted": new, "updated": upd}


# ---------------------------------------------------------------- 两侧状态

def set_dy(conn: sqlite3.Connection, product_id: int, state: str,
           tid: str = "", reason: str = "", note: str = "") -> bool:
    return _set_side(conn, product_id, "dy", state, tid, reason, note)


def set_wx(conn: sqlite3.Connection, product_id: int, state: str,
           wxid: str = "", reason: str = "", note: str = "") -> bool:
    return _set_side(conn, product_id, "wx", state, wxid, reason, note)


def _set_side(conn, pid, side, state, ext_id, reason, note) -> bool:
    ensure(conn)
    ok = conn.execute("SELECT 1 FROM v3_upload_status WHERE product_id=?",
                      (int(pid),)).fetchone()
    if not ok:
        return False
    conn.execute(
        "UPDATE v3_upload_status SET %s_state=?, %s_%s=?, %s_reason=?, %s_at=?,"
        " note=CASE WHEN ?='' THEN note ELSE ? END, updated_at=? WHERE product_id=?"
        % (side, side, "tid" if side == "dy" else "product_id", side, side),
        (state, str(ext_id or ""), str(reason or "")[:500], store.now(),
         note or "", note or "", store.now(), int(pid)))
    conn.commit()
    return True


def import_records(conn: sqlite3.Connection, records: list[dict],
                   side: str = "dy") -> dict:
    """批量导入一侧的结果。每条至少要有 `title` 与 `state`。

    `state` 取值：success / fail / draft / none；`id`、`reason` 可选。
    认不出标题的**不写**，进 `unmatched` 让人看 —— 不猜。
    """
    ensure(conn)
    done = unmatched = 0
    misses: list[str] = []
    for rec in (records or []):
        title = str(rec.get("title") or rec.get("商品名称") or "").strip()
        state = str(rec.get("state") or rec.get("审核状态")
                    or rec.get("status") or "").strip()
        if not title or not state:
            continue
        pid = match_pid(conn, title)
        if not pid:
            unmatched += 1
            if len(misses) < 30:
                misses.append(title[:40])
            continue
        if state in ("审核通过", "出售中", "售卖中", "上架成功", "success", "在售", "已上架"):
            st = "success"
        elif state in ("审核驳回", "驳回", "fail", "失败", "未通过"):
            st = "fail"
        elif state in ("草稿箱", "draft", "待审核", "审核中"):
            st = "draft"
        else:
            st = state
        _set_side(conn, pid, side, st,
                  str(rec.get("id") or rec.get("商品ID") or ""),
                  str(rec.get("reason") or rec.get("驳回原因") or ""), "")
        done += 1
    return {"ok": True, "imported": done, "unmatched": unmatched,
            "unmatched_sample": misses}


def _strict_match(conn: sqlite3.Connection, title: str, head: int = 30) -> int | None:
    """微信侧专用：**只认完全相等或前 30 字相等**。

    为什么不用 `match_pid`：微信店里有几百件同品类商品（标题高度相似），
    「包含」规则会把近似款全算成本品 —— 实测 560 行索引能匹配出 141 个 pid，
    那是误判，不是战果。**宁可少认，不许把别人的商品算成我上架的。**
    """
    nt = _norm(title)
    if len(nt) < 10:
        return None
    rows = conn.execute("SELECT product_id, original_title FROM v3_product"
                        " WHERE COALESCE(original_title,'')<>''").fetchall()
    for r in rows:                                  # ① 完全相等
        if _norm(r[1]) == nt:
            return int(r[0])
    if len(nt) < head:
        return None
    for r in rows:                                  # ② 前 head 字相等（且够长）
        n2 = _norm(r[1])
        if len(n2) >= head and n2[:head] == nt[:head]:
            return int(r[0])
    return None


# ---------------------------------------------------------------- 微信侧

def reset_side(conn: sqlite3.Connection, side: str = "wx") -> int:
    """把一侧状态归零（重跑导入前用，避免旧结论残留）。"""
    ensure(conn)
    if side not in ("dy", "wx"):
        raise ValueError("side 只能是 dy / wx")
    cur = conn.execute(
        "UPDATE v3_upload_status SET %s_state='none', %s_%s='',"
        " %s_reason='', %s_at='' WHERE %s_state<>'none'"
        % (side, side, "tid" if side == "dy" else "product_id", side, side, side))
    conn.commit()
    return cur.rowcount


def import_wx_index(conn: sqlite3.Connection, only_status: int = 0) -> dict:
    """用 `v3_wx_title_idx`（微信店里已有的商品标题）回填微信侧状态。

    **判据**：微信店里存在**同名**（或前 30 字相同）的商品 → 这件在微信侧在架。
    用 `_strict_match`，不用宽松的 `match_pid`（理由见那个函数的 docstring）。

    注意：这是**目录级**判断，不区分「我们传的」还是「店里原本就有的」——
    对主人要的问题（这件在微信上有没有）答案是同一个。
    """
    ensure(conn)
    where, args = "", []
    if only_status:
        where = " WHERE wx_status=?"
        args = [only_status]
    rows = conn.execute("SELECT product_id, title FROM v3_wx_title_idx" + where,
                        args).fetchall()
    idx_cnt = conn.execute("SELECT COUNT(*) FROM v3_upload_status").fetchone()[0]
    if not idx_cnt:
        return {"ok": False, "error": "状态库还是空的，先跑 sync"}
    hit = miss = 0
    seen: set[int] = set()
    for r in rows:
        title = (r["title"] or "").strip()
        if not title:
            continue
        pid = _strict_match(conn, title)
        if not pid:
            miss += 1
            continue
        if pid in seen:              # 同款重复上架，只记第一次
            continue
        seen.add(pid)
        _set_side(conn, pid, "wx", "success", str(r["product_id"] or ""), "", "")
        hit += 1
    return {"ok": True, "index_rows": len(rows), "matched": hit, "unmatched": miss}


# ---------------------------------------------------------------- 统计

def stats(conn: sqlite3.Connection, pack_code: str = "") -> dict:
    ensure(conn)
    where, args = "", []
    if pack_code:
        where = " WHERE pack_code=?"
        args = [pack_code]
    total = conn.execute("SELECT COUNT(*) FROM v3_upload_status" + where, args).fetchone()[0]

    def _grp(col):
        return {r[0]: r[1] for r in conn.execute(
            "SELECT %s, COUNT(*) FROM v3_upload_status%s GROUP BY %s"
            % (col, where, col), args)}

    fine = _grp("fine_state")
    dy = _grp("dy_state")
    wx = _grp("wx_state")
    both = conn.execute(
        "SELECT COUNT(*) FROM v3_upload_status%s%s dy_state='success' AND wx_state='success'"
        % (where, " AND" if where else " WHERE"), args).fetchone()[0]
    neither = conn.execute(
        "SELECT COUNT(*) FROM v3_upload_status%s%s dy_state<>'success' AND wx_state<>'success'"
        % (where, " AND" if where else " WHERE"), args).fetchone()[0]
    return {"ok": True, "total": int(total), "fine": fine, "dy": dy, "wx": wx,
            "both_success": int(both), "neither": int(neither)}


# ---------------------------------------------------------------- CLI

def _cli(argv):
    db = None
    if argv and argv[0].startswith("--db="):
        db = argv[0].split("=", 1)[1]
        argv = argv[1:]
    conn = store.connect(db)
    try:
        cmd = (argv[0] if argv else "stats").lower()
        if cmd == "sync":
            r = sync_products(conn, pack_code=(argv[1] if len(argv) > 1 else ""))
        elif cmd == "wxidx":
            r = import_wx_index(conn)
        elif cmd in ("dy", "wx"):
            if len(argv) < 2:
                print("用法: status.py %s <文件.json|csv>" % cmd)
                return 2
            path = argv[1]
            if path.lower().endswith(".csv"):
                with open(path, "r", encoding="utf-8-sig") as f:
                    recs = list(csv.DictReader(f))
            else:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                recs = data if isinstance(data, list) else (data.get("rows") or [])
            r = import_records(conn, recs, side=cmd)
        else:
            r = stats(conn, pack_code=(argv[1] if len(argv) > 1 else ""))
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
