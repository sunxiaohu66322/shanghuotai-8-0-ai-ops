# -*- coding: utf-8 -*-
"""把「一个 JSON 一个包」留下的**单件包**合并成一个成员包。

背景（2026-10-24 审计结论）
    第二步的导入入口是「选几个文件就循环几次、每次只转一个，包码=文件名」，
    于是 175 个商品卡 JSON 生成了 175 个「每包 1 件」的包，第二步没法当一批管理。

本模块只做一件事：**建立引用关系**，不搬商品、不搬图片、不删任何原包。

铁律
  · 引用式：只往 v3_pack_member 写 (pack_code, product_id, source_pack)，
    商品行 / 图片行 / OCR 缓存一个字节都不复制
  · **不改 v3_product.pack_code** —— 原包永远是原包，历史可查
  · 每个成员的 `source_pack` 记**它自己原来的包码**，不写「混合」这种糊弄值
  · dry_run 默认开：先给计划（要并哪些、多少件、多少图），确认后才落库
  · 幂等：同一商品在同一个合并包里只会有一行（INSERT OR REPLACE）
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any

from . import store

# 商品 ID 当包名的形状：纯数字、够长（微信/淘宝的商品号都是十几位）
MIN_ID_LEN = 8

MERGE_SOURCE_TAG = "merge:single_cards"


def _is_product_id_like(code: str) -> bool:
    s = str(code or "").strip()
    return s.isdigit() and len(s) >= MIN_ID_LEN


def scan(conn: sqlite3.Connection) -> dict:
    """扫出「单件包」候选，并把不适用的单独列出来（不静默丢）。

    候选条件（三条同时满足）：
      ① 该 pack_code 在 v3_product 里只有 1 件商品
      ② pack_code 长得像商品 ID（纯数字且 ≥8 位）—— 这是商品卡 JSON 导入留下的特征
      ③ 它不是成员包（v3_pack_member 里没有它的行）

    返回 {candidates:[{pack_code, product_id, title, images}], excluded:[{pack_code, why}], totals}
    """
    single: list[dict] = []
    for r in conn.execute(
            "SELECT pack_code AS code, COUNT(*) AS n FROM v3_product"
            " WHERE pack_code IS NOT NULL AND pack_code<>'' GROUP BY pack_code"):
        if int(r["n"] or 0) == 1:
            single.append({"code": str(r["code"] or ""), "n": 1})

    member_codes: set[str] = set()
    try:
        member_codes = {str(x[0]) for x in conn.execute(
            "SELECT DISTINCT pack_code FROM v3_pack_member")}
    except sqlite3.Error:
        pass

    candidates: list[dict] = []
    excluded: list[dict] = []
    for s in single:
        code = s["code"]
        if code in member_codes:
            excluded.append({"pack_code": code, "why": "已经是成员包（有 v3_pack_member 行）"})
            continue
        if not _is_product_id_like(code):
            excluded.append({"pack_code": code, "why": "包名不像商品 ID（可能是人工命名 / 粗包）"})
            continue
        row = conn.execute(
            "SELECT product_id, original_title FROM v3_product WHERE pack_code=?",
            (code,)).fetchone()
        if not row:
            excluded.append({"pack_code": code, "why": "取不到商品行"})
            continue
        pid = int(row["product_id"] or 0)
        if not pid:
            excluded.append({"pack_code": code, "why": "商品 ID 为空"})
            continue
        n_img = conn.execute(
            "SELECT COUNT(*) FROM v3_image WHERE product_id=?", (pid,)).fetchone()[0]
        candidates.append({
            "pack_code": code, "product_id": pid,
            "title": str(row["original_title"] or "")[:60], "images": int(n_img or 0),
        })

    candidates.sort(key=lambda x: x["product_id"])
    return {
        "candidates": candidates,
        "excluded": excluded,
        "totals": {
            "single_packs": len(single),
            "candidates": len(candidates),
            "excluded": len(excluded),
            "products": len(candidates),
            "images": sum(c["images"] for c in candidates),
        },
    }


def _uniq_code(conn: sqlite3.Connection, base: str) -> str:
    """合并包一律新建：撞名加 -2/-3，绝不覆盖已有的包。"""
    base = (base or "合并包").strip() or "合并包"
    for cand in [base] + ["%s-%d" % (base, i) for i in range(2, 99)]:
        taken = conn.execute(
            "SELECT 1 FROM v3_pack WHERE pack_code=? LIMIT 1", (cand,)).fetchall()
        if not taken:
            try:
                taken = conn.execute(
                    "SELECT 1 FROM v3_pack_member WHERE pack_code=? LIMIT 1", (cand,)).fetchall()
            except sqlite3.Error:
                taken = []
        if not taken:
            return cand
    return "%s-%d" % (base, int(time.time()))


def plan(conn: sqlite3.Connection, name: str = "") -> dict:
    """只算不写：给出将要生成的包码、件数、图数、逐条清单。"""
    sc = scan(conn)
    n = len(sc["candidates"])
    base = (name or "").strip() or ("微信商品卡合并_%d件_%s"
                                    % (n, time.strftime("%Y%m%d")))
    return {
        "ok": True, "dry_run": True,
        "pack_code": _uniq_code(conn, base),
        "count": n, "images": sc["totals"]["images"],
        "candidates": sc["candidates"], "excluded": sc["excluded"],
        "totals": sc["totals"],
        "message": "将把 %d 个单件包合并成一个数据包（引用式，不搬商品、不删原包）" % n,
    }


def merge(conn: sqlite3.Connection, name: str = "", dry_run: bool = True,
          pids: list | None = None) -> dict:
    """执行合并。dry_run=True（默认）只返回计划，不写库。"""
    pl = plan(conn, name)
    if dry_run:
        return pl
    if not pl["count"]:
        out = dict(pl)
        out.update({"ok": False, "error": "没有可合并的单件包", "dry_run": False})
        return out

    # 只并指定 pids（如果给了），其余照全量
    cands = pl["candidates"]
    if pids:
        want = {int(x) for x in pids}
        cands = [c for c in cands if c["product_id"] in want]
        if not cands:
            return {"ok": False, "error": "指定的商品不在候选里", "dry_run": False,
                    "pack_code": pl["pack_code"]}

    code = pl["pack_code"]
    n = len(cands)
    img_n = sum(c["images"] for c in cands)
    ts = store.now()

    # ① 记账行（先写包，再写成员，失败时不会留下只有成员没有包的半截状态）
    conn.execute(
        "INSERT INTO v3_pack(pack_code,source_file,source_name,size_kb,rows,products,"
        "images,skipped,status,message,error,converted_at,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (code, MERGE_SOURCE_TAG, "单件包合并", 0.0, n, n, img_n, 0, "ready",
         "%d 个商品（单件包合并，引用式，成员各自记原包码）" % n, "", ts, ts, ts))

    # ② 成员关系：source_pack 记**每个商品自己原来的包码**
    def _write() -> None:
        conn.executemany(
            "INSERT OR REPLACE INTO v3_pack_member"
            " (pack_code, product_id, source_pack, note, created_at) VALUES(?,?,?,?,?)",
            [(code, c["product_id"], c["pack_code"],
              "合并自单件包 %s" % c["pack_code"], ts) for c in cands])
        conn.commit()

    for i in range(4):                       # 撞 WAL 写锁时重试，不弹错给用户
        try:
            _write()
            break
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() and "busy" not in str(e).lower() or i == 3:
                raise
            time.sleep(0.3 * (i + 1))

    store.audit(conn, "merge_single_packs", "pack", code, None,
                {"count": n, "images": img_n, "source": MERGE_SOURCE_TAG})
    conn.commit()                            # audit 自己不 commit

    # ③ 落地后回读，拿真实数字（不拿计划里的数当结果）
    got = int(conn.execute(
        "SELECT COUNT(*) FROM v3_pack_member WHERE pack_code=?", (code,)).fetchone()[0] or 0)
    got_img = int(conn.execute(
        "SELECT COUNT(*) FROM v3_image i JOIN v3_pack_member m"
        " ON m.product_id=i.product_id WHERE m.pack_code=?", (code,)).fetchone()[0] or 0)
    src_kinds = int(conn.execute(
        "SELECT COUNT(DISTINCT source_pack) FROM v3_pack_member WHERE pack_code=?",
        (code,)).fetchone()[0] or 0)
    return {
        "ok": got == n, "dry_run": False, "pack_code": code,
        "count": got, "planned": n, "images": got_img,
        "sources": src_kinds,
        "message": "%d 个单件包 → 数据包「%s」（%d 件商品 · %d 张图）" % (n, code, got, got_img),
        "excluded": pl["excluded"],
    }


def verify(conn: sqlite3.Connection, code: str) -> dict:
    """合并包的体检：件数、图数、成员来源、是否误删原包、是否幂等。"""
    if not code:
        return {"ok": False, "error": "没有给包码"}
    n = int(conn.execute(
        "SELECT COUNT(*) FROM v3_pack_member WHERE pack_code=?", (code,)).fetchone()[0] or 0)
    n_prod = int(conn.execute(
        "SELECT COUNT(*) FROM v3_product WHERE product_id IN"
        " (SELECT product_id FROM v3_pack_member WHERE pack_code=?)", (code,)).fetchone()[0] or 0)
    n_img = int(conn.execute(
        "SELECT COUNT(*) FROM v3_image i JOIN v3_pack_member m"
        " ON m.product_id=i.product_id WHERE m.pack_code=?", (code,)).fetchone()[0] or 0)
    no_src = int(conn.execute(
        "SELECT COUNT(*) FROM v3_pack_member WHERE pack_code=?"
        " AND (source_pack IS NULL OR source_pack='')", (code,)).fetchone()[0] or 0)
    # 原包是否还在（不能因为合并就少了包）
    src_packs = [r[0] for r in conn.execute(
        "SELECT DISTINCT source_pack FROM v3_pack_member WHERE pack_code=?", (code,))]
    src_alive = sum(
        1 for p in src_packs
        if conn.execute("SELECT 1 FROM v3_pack WHERE pack_code=? LIMIT 1", (p,)).fetchall())
    dup = int(conn.execute(
        "SELECT COUNT(*) FROM (SELECT product_id FROM v3_pack_member"
        " WHERE pack_code=? GROUP BY product_id HAVING COUNT(*)>1)", (code,)).fetchone()[0] or 0)
    return {
        "ok": (n == n_prod) and dup == 0 and no_src == 0,
        "pack_code": code,
        "members": n, "products_alive": n_prod, "images": n_img,
        "sources": len(src_packs), "sources_still_alive": src_alive,
        "members_without_source": no_src, "duplicate_products": dup,
        "message": "成员 %d · 商品存活 %d · 图 %d · 来源包 %d（仍在 %d）· 无来源 %d · 重复 %d"
                   % (n, n_prod, n_img, len(src_packs), src_alive, no_src, dup),
    }
