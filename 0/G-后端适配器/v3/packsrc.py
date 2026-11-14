# -*- coding: utf-8 -*-
"""v3 数据包「统一口径」—— 第二步与 2.5 共用同一个真相

背景（C0 审计）：
  第二步读 v3_pack（包登记表，只有 1 行）；
  2.5 按 v3_product.pack_code 分组（商品自带归属）。
  两本账 → 同一个包在两个页面显示的件数完全不同。

本模块的定义（唯一口径）：
  · **正式数据包**（formal）  = v3_pack 有行。
  · **历史兼容集合**（legacy）= v3_pack 没有行，但 v3_product.pack_code 指向它
    （例如「户外野营凳」184 件、「商品明细」143 件 —— 当初是按文件导入、没登记包行）。
  · **成员关系**：以 v3_pack_member 为准（经过校验的引用表）；
    旧字段 v3_product.pack_code 只作兼容信息，**不再单独构成一套真相**。
  · 一个商品可以属于多个包（成员表天然支持）。

调用方只认这两个函数，不要再各写各的 SQL：
    list_packs(conn)          -> 全量包的统一清单（含各状态计数）
    pack_products(conn, code) -> 该包的商品 id（成员表优先，legacy 兜底）
"""
from __future__ import annotations

import os
import sqlite3

# 图片"本地是否已落盘"的判定：以磁盘为准，不信数据库里的路径字符串
_HAS_FILE_CACHE: dict[str, bool] = {}


def _cols(conn, table: str) -> list[str]:
    try:
        return [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)]
    except sqlite3.Error:
        return []


def _exists(p: str) -> bool:
    if not p:
        return False
    if p in _HAS_FILE_CACHE:
        return _HAS_FILE_CACHE[p]
    ok = os.path.isfile(p)
    _HAS_FILE_CACHE[p] = ok
    return ok


def clear_cache() -> None:
    _HAS_FILE_CACHE.clear()


def pack_products(conn, code: str) -> dict:
    """包的成员解析。返回 {ids, from_member, from_pack_code, dangling}。

    ★ 权威顺序：成员表 > 旧字段。成员表为空时才回落到 pack_code（legacy 兼容）。
      之所以不"合并"，是因为合并会把"成员表被清空"误读成"这个包还有一堆货"。
    """
    code = (code or "").strip()
    if not code:
        return {"ids": [], "from_member": 0, "from_pack_code": 0, "dangling": 0}

    member_ids, dangling = [], 0
    if _cols(conn, "v3_pack_member"):
        for r in conn.execute(
                "SELECT m.product_id, (SELECT COUNT(*) FROM v3_product p"
                "  WHERE p.product_id=m.product_id) AS alive"
                " FROM v3_pack_member m WHERE m.pack_code=? ORDER BY m.product_id", (code,)):
            if int(r[1]):
                member_ids.append(int(r[0]))
            else:
                dangling += 1

    legacy_ids = [int(r[0]) for r in conn.execute(
        "SELECT product_id FROM v3_product WHERE pack_code=? ORDER BY product_id", (code,))]

    if member_ids:
        ids = member_ids
    else:
        ids = legacy_ids
    return {"ids": ids, "from_member": len(member_ids),
            "from_pack_code": len(legacy_ids), "dangling": dangling}


def _pack_stats(conn, code: str, ids: list[int]) -> dict:
    """按商品 id 算真实进度（下载 / OCR / 证据）。全部直查，不用记账值。"""
    st = {"products": len(ids), "downloaded_products": 0, "images": 0,
          "images_local": 0, "images_pending": 0, "ocr_ok": 0, "ocr_boxes": 0,
          "pending_evidence": 0}
    if not ids:
        return st
    q = ",".join("?" * len(ids))
    rows = conn.execute(
        "SELECT product_id, original_local_path, current_local_path FROM v3_image"
        " WHERE product_id IN (%s)" % q, ids).fetchall()
    st["images"] = len(rows)
    local_by_pid: dict[int, int] = {}
    for r in rows:
        if _exists(r[2] or r[1]):
            st["images_local"] += 1
            local_by_pid[int(r[0])] = local_by_pid.get(int(r[0]), 0) + 1
        else:
            st["images_pending"] += 1
    st["downloaded_products"] = len(local_by_pid)

    try:
        for r in conn.execute(
                "SELECT state, COUNT(*), SUM(CASE WHEN boxes_json IS NOT NULL"
                " AND boxes_json<>'' THEN 1 ELSE 0 END) FROM v3_image_ocr"
                " WHERE product_id IN (%s) GROUP BY state" % q, ids):
            if r[0] == "ok":
                st["ocr_ok"] += int(r[1] or 0)
                st["ocr_boxes"] += int(r[2] or 0)
    except sqlite3.OperationalError:
        pass

    # 证据就绪 = 有本地图 且 有 OCR 且 有标题。缺一项就算待补（但不等于 blocked）
    ready = conn.execute(
        "SELECT COUNT(*) FROM v3_product p WHERE p.product_id IN (%s)"
        " AND TRIM(IFNULL(p.original_title,''))<>''"
        " AND EXISTS(SELECT 1 FROM v3_image_ocr o WHERE o.product_id=p.product_id"
        "             AND o.state='ok')" % q, ids).fetchone()[0]
    st["pending_evidence"] = st["products"] - int(ready)
    return st


def list_packs(conn) -> list[dict]:
    """全量包清单（formal + legacy），每行都带真实计数。

    返回字段：
      pack_code, origin(formal|legacy), in_pack_table, source_name, source_file,
      source_dir, status, created_at, updated_at, recorded_products(记账值),
      members, dangling, from_pack_code, **stats
    """
    codes: dict[str, dict] = {}
    pack_cols = _cols(conn, "v3_pack")
    if pack_cols:
        sel = ["pack_code"]
        keys = ["pack_code"]
        for k in ("source_name", "source_file", "source_dir", "status",
                  "products", "rows", "created_at", "updated_at"):
            sel.append(k if k in pack_cols else "NULL AS %s" % k)
            keys.append(k)
        # ★ 不依赖 conn.row_factory：显式按列名 zip（否则 tuple 连接上 dict(r) 会炸）
        for r in conn.execute("SELECT %s FROM v3_pack" % ",".join(sel)):
            d = dict(zip(keys, r))
            codes[d["pack_code"]] = {
                "pack_code": d["pack_code"], "origin": "formal", "in_pack_table": True,
                "source_name": d.get("source_name") or "",
                "source_file": d.get("source_file") or "",
                "source_dir": d.get("source_dir") or "",
                "status": d.get("status") or "", "rows": d.get("rows") or 0,
                "recorded_products": d.get("products") or 0,
                "created_at": d.get("created_at") or "",
                "updated_at": d.get("updated_at") or ""}

    for r in conn.execute(
            "SELECT pack_code, COUNT(*) FROM v3_product"
            " WHERE TRIM(IFNULL(pack_code,''))<>'' GROUP BY pack_code"):
        c = r[0]
        if c not in codes:
            codes[c] = {"pack_code": c, "origin": "legacy", "in_pack_table": False,
                        "source_name": "", "source_file": "", "source_dir": "",
                        "status": "legacy", "rows": 0, "recorded_products": 0,
                        "created_at": "", "updated_at": ""}

    if _cols(conn, "v3_pack_member"):
        for r in conn.execute("SELECT pack_code, COUNT(*) FROM v3_pack_member GROUP BY pack_code"):
            c = r[0]
            if c not in codes:
                codes[c] = {"pack_code": c, "origin": "formal", "in_pack_table": False,
                            "source_name": "", "source_file": "", "source_dir": "",
                            "status": "member_only", "rows": 0, "recorded_products": 0,
                            "created_at": "", "updated_at": ""}

    out = []
    for c, base in codes.items():
        m = pack_products(conn, c)
        row = dict(base)
        member_rows = 0
        if _cols(conn, "v3_pack_member"):
            member_rows = conn.execute("SELECT COUNT(*) FROM v3_pack_member WHERE pack_code=?",
                                       (c,)).fetchone()[0]
        # members = **有效**成员（指向仍存在的商品）；member_rows = 名册总行数
        row.update({"members": m["from_member"], "member_rows": int(member_rows),
                    "dangling": m["dangling"], "from_pack_code": m["from_pack_code"]})
        row.update(_pack_stats(conn, c, m["ids"]))
        # 状态机：按记录实算，不是只改文字
        if not row["in_pack_table"] and row["origin"] == "legacy":
            row["lifecycle"] = "legacy_collection"
        elif row["products"] == 0:
            row["lifecycle"] = "imported" if not row["dangling"] else "dangling_members"
        elif row["images_local"] == 0:
            row["lifecycle"] = "parsed_no_download"
        elif row["images_pending"] > 0:
            row["lifecycle"] = "partial_download"
        elif row["ocr_ok"] == 0:
            row["lifecycle"] = "downloaded_no_ocr"
        elif row["pending_evidence"] > 0:
            row["lifecycle"] = "evidence_partial"
        else:
            row["lifecycle"] = "evidence_ready"
        out.append(row)
    out.sort(key=lambda x: (-x["products"], x["pack_code"]))
    return out


def summary(conn) -> dict:
    pk = list_packs(conn)
    return {
        "packs": len(pk),
        "formal": sum(1 for x in pk if x["origin"] == "formal"),
        "legacy": sum(1 for x in pk if x["origin"] == "legacy"),
        "products": conn.execute("SELECT COUNT(*) FROM v3_product").fetchone()[0],
        "dangling": sum(x["dangling"] for x in pk),
        "rows": pk,
    }
