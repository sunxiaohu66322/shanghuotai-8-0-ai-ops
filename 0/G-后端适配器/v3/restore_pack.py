# -*- coding: utf-8 -*-
"""把一个文件夹里的商品卡 JSON 恢复成一个「正式数据包」—— 幂等 + 事务 + 不覆盖旧数据

场景（C0 审计）：176 个微信商品卡 JSON 被混成一个包，后来商品行被删，
成员表留下 176 条悬空。原始 JSON 完好，本模块把它们重新解析入库，
并把 176 条悬空成员**重新指向恢复后的商品**（成员表 PK 是 (pack_code, product_id)，
product_id 会变，所以做法是：删掉该包的悬空成员，再按新 id 重建）。

四条硬约束：
  1. 商品唯一键 = (source_platform, source_product_id)，与库里的唯一索引一致；
     **绝不用纯数字 id 跨平台合并**。
  2. 不覆盖已有商品：original_title / original_payload 只在首次写入（store 已保证），
     本模块只补可变字段。
  3. 幂等：同一目录跑第二次，商品/图片/成员数量不变（靠 upsert + INSERT OR IGNORE）。
  4. 全程一个事务：任一步失败 → 整体回滚，并回报 rolled_back。

媒体引用写入 ≠ 已下载：新写入的 v3_image 一律 dl_state='pending'（未下载）。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3

from . import adapters, store

GRADE_CARD = "card_import"
GRADE_LEGACY = "legacy_csv"


# ---------------------------------------------------------------- 迁移（小步、可重复）

def ensure_schema(conn) -> dict:
    """只加列加索引，不删不改任何已有列。可重复执行。"""
    done = []

    def cols(t):
        try:
            return [r[1] for r in conn.execute("PRAGMA table_info(%s)" % t)]
        except sqlite3.Error:
            return []

    if "dl_state" not in cols("v3_image"):
        conn.execute("ALTER TABLE v3_image ADD COLUMN dl_state TEXT")
        done.append("v3_image.dl_state")
    if "data_grade" not in cols("v3_product"):
        conn.execute("ALTER TABLE v3_product ADD COLUMN data_grade TEXT")
        done.append("v3_product.data_grade")
    if "download_hint" not in cols("v3_image"):
        conn.execute("ALTER TABLE v3_image ADD COLUMN download_hint TEXT")
        done.append("v3_image.download_hint")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_v3_image_dl ON v3_image(dl_state)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_v3_product_grade ON v3_product(data_grade)")
    return {"added": done}


def mark_legacy_csv(conn) -> int:
    """把"从 CSV 进来的"历史商品标成 legacy_csv（只标 data_grade，不动 payload）。"""
    if "data_grade" not in [r[1] for r in conn.execute("PRAGMA table_info(v3_product)")]:
        return 0
    n = 0
    for r in conn.execute("SELECT product_id, original_payload FROM v3_product"
                          " WHERE IFNULL(data_grade,'')=''").fetchall():
        try:
            pay = json.loads(r[1] or "{}")
        except Exception:  # noqa: BLE001
            pay = {}
        src = str(pay.get("来源文件") or "")
        if src.lower().endswith(".csv"):
            conn.execute("UPDATE v3_product SET data_grade=? WHERE product_id=?",
                         (GRADE_LEGACY, r[0]))
            n += 1
    return n


# ---------------------------------------------------------------- 恢复

def _sha256(buf: bytes) -> str:
    return hashlib.sha256(buf).hexdigest()


def restore_from_dir(conn: sqlite3.Connection, src_dir: str, pack_code: str,
                     archive_root: str = "", dry_run: bool = True,
                     _fail_after: str = "") -> dict:
    """把 src_dir 里的卡文件恢复成 pack_code 这个正式包。

    dry_run=True 只解析不写库（可先看会新增多少）。
    _fail_after='images'|'products' 仅测试注入故障用。
    """
    src_dir = os.path.abspath(src_dir)
    if not os.path.isdir(src_dir):
        return {"ok": False, "error": "目录不存在：%s" % src_dir}

    # ★ 幂等的前提：store.upsert_* 用 row["col"] 读回已存在的行，
    #   所以连接必须开 row_factory。调用方忘了设置会让"第二次导入"必炸
    #   （TypeError: tuple indices must be integers）。这里兜一层，不依赖调用方。
    if conn.row_factory is not sqlite3.Row:
        conn.row_factory = sqlite3.Row

    scan = adapters.wechat.scan_cards(src_dir, recursive=False)
    files = scan["files"]
    parsed, invalid = [], []
    for p in files:
        try:
            with open(p, "rb") as fh:
                buf = fh.read()
            payload = json.loads(buf.decode("utf-8-sig"))
            card = adapters.parse_card(payload, source_file=p)
            parsed.append({"path": p, "card": card, "sha": _sha256(buf)})
        except Exception as e:  # noqa: BLE001
            invalid.append({"path": os.path.basename(p), "why": "%s: %s" % (type(e).__name__, e)})

    rep = {
        "ok": True, "pack_code": pack_code, "src_dir": src_dir, "dry_run": bool(dry_run),
        "scanned": scan["scanned"], "files": len(files), "ignored_dirs": len(scan.get("ignored") or []),
        "parsed": len(parsed), "invalid": invalid,
        "products_new": 0, "products_existing": 0,
        "images_new": 0, "images_reused": 0,
        "members_new": 0, "members_replaced": 0, "members_dangling_cleared": 0,
        "unique_urls": 0, "media_entries": 0,
        "skus": 0, "attrs": 0,
        "with_desc": 0, "with_video": 0, "with_shop": 0, "with_cat_name": 0,
        "parse_warnings": 0,
    }

    # 统计（dry_run 也算，供"发现多少资源"预告用）
    urls = set()
    for it in parsed:
        c = it["card"]
        ms = c.get("media_stats") or {}
        rep["media_entries"] += int(ms.get("entries") or 0)
        for m in c.get("media") or []:
            urls.add(m["url"])
        rep["skus"] += len(c.get("skus") or [])
        rep["attrs"] += len(c.get("attributes") or {})
        if (c.get("raw_payload") or {}).get("desc") or (c.get("raw_payload") or {}).get("description"):
            rep["with_desc"] += 1
        if (c.get("media_stats") or {}).get("video"):
            rep["with_video"] += 1
        if c.get("shop_name"):
            rep["with_shop"] += 1
        if not (c.get("category") or {}).get("name_missing"):
            rep["with_cat_name"] += 1
        rep["parse_warnings"] += len(c.get("parse_warnings") or [])
    rep["unique_urls"] = len(urls)

    if dry_run:
        rep["note"] = "dry_run：未写库。商品会新增/命中已有 见 products_*（dry_run 下为 0）"
        return rep

    ensure_schema(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        # 1) 包行（正式包）
        exists = conn.execute("SELECT 1 FROM v3_pack WHERE pack_code=?", (pack_code,)).fetchone()
        now = store.now()
        if exists:
            conn.execute("UPDATE v3_pack SET rows=?, updated_at=? WHERE pack_code=?",
                         (len(files), now, pack_code))
        else:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(v3_pack)")]
            vals = {"pack_code": pack_code, "source_file": src_dir, "source_name": os.path.basename(src_dir),
                    "source_dir": src_dir, "size_kb": round(sum(os.path.getsize(i["path"]) for i in parsed) / 1024.0, 1),
                    "rows": len(files), "products": 0, "images": 0, "skipped": len(invalid),
                    "status": "converted", "created_at": now, "converted_at": now, "updated_at": now}
            use = [k for k in vals if k in cols]
            conn.execute("INSERT INTO v3_pack(%s) VALUES(%s)" % (",".join(use), ",".join("?" * len(use))),
                         [vals[k] for k in use])

        # 2) 清掉本包的悬空成员（商品 id 已失效的行；只动成员表）
        if "v3_pack_member" in [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]:
            cur = conn.execute(
                "DELETE FROM v3_pack_member WHERE pack_code=? AND NOT EXISTS ("
                " SELECT 1 FROM v3_product p WHERE p.product_id=v3_pack_member.product_id)",
                (pack_code,))
            rep["members_dangling_cleared"] = cur.rowcount or 0

        # 3) 商品
        pid_map = []
        for it in parsed:
            c = it["card"]
            rec = {
                "source_platform": c.get("platform") or "other",
                "source_product_id": c["source_product_id"],
                "source_url": "",
                "original_title": c.get("title") or "",
                "category_id": "/".join((c.get("category") or {}).get("ids") or []),
                "category_path": (c.get("category") or {}).get("path") or "",
                "category_confidence": (c.get("category") or {}).get("confidence"),
                "attributes": c.get("attributes") or {},
                "original_payload": c.get("raw_payload"),
                "provenance": GRADE_CARD,
                "source_shop_name": c.get("shop_name") or "",
                "price_min": c.get("price_min"), "price_max": c.get("price_max"),
                "pack_code": "",          # ★ 不再写旧字段：成员表才是真相
            }
            pid, created = store.upsert_product(conn, rec)
            if created:
                rep["products_new"] += 1
            else:
                rep["products_existing"] += 1
            conn.execute("UPDATE v3_product SET data_grade=? WHERE product_id=? AND IFNULL(data_grade,'')=''",
                         (GRADE_CARD, pid))
            pid_map.append((pid, c))
            if _fail_after == "products" and rep["products_new"] >= 3:
                raise RuntimeError("测试注入：写完商品后失败")

        # 4) 成员（176 条 → 指向恢复后的商品）
        for pid, _c in pid_map:
            before = conn.execute("SELECT 1 FROM v3_pack_member WHERE pack_code=? AND product_id=?",
                                  (pack_code, pid)).fetchone()
            conn.execute("INSERT OR IGNORE INTO v3_pack_member(pack_code,product_id,source_pack,note,created_at)"
                         " VALUES(?,?,?,?,?)",
                         (pack_code, pid, "restore", "从原始 JSON 恢复", store.now()))
            if before:
                rep["members_replaced"] += 1
            else:
                rep["members_new"] += 1

        # 5) 媒体引用（一律 pending：写引用≠已下载）
        for pid, c in pid_map:
            for m in c.get("media") or []:
                img_id, created = store.upsert_image(conn, pid, {
                    "role": m["role"], "sequence": m.get("sequence") or 0,
                    "sku_id": m.get("sku_id"), "original_url": m["url"],
                    "note": "shared_key=%s" % (m.get("shared_key") or ""),
                })
                if created:
                    rep["images_new"] += 1
                    conn.execute("UPDATE v3_image SET dl_state='pending', download_hint=? WHERE image_id=?",
                                 (m.get("shared_key") or "", img_id))
                else:
                    rep["images_reused"] += 1
            if _fail_after == "images" and rep["images_new"] >= 5:
                raise RuntimeError("测试注入：写完图片后失败")

        # 6) 包计数回填（记账值，只做展示用；真相仍以成员表为准）
        conn.execute("UPDATE v3_pack SET products=?, images=?, updated_at=? WHERE pack_code=?",
                     (len(pid_map),
                      conn.execute("SELECT COUNT(*) FROM v3_image WHERE product_id IN (%s)"
                                   % ",".join("?" * len(pid_map)), [p for p, _ in pid_map]).fetchone()[0]
                      if pid_map else 0,
                      store.now(), pack_code))
        conn.commit()
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        rep.update({"ok": False, "error": "%s: %s" % (type(e).__name__, e), "rolled_back": True})
        return rep

    # 7) 原始 JSON 归档（DB 已提交后才落盘；失败不影响入库结果）
    if archive_root:
        try:
            raw_dir = os.path.join(archive_root, pack_code, "raw")
            os.makedirs(raw_dir, exist_ok=True)
            manifest = []
            for it in parsed:
                sid = str(it["card"]["source_product_id"])
                dst = os.path.join(raw_dir, "%s.json" % sid)
                if not os.path.exists(dst):
                    with open(it["path"], "rb") as fh, open(dst, "wb") as out:
                        out.write(fh.read())
                manifest.append({"product_id": sid, "src": it["path"],
                                 "archived": dst, "sha256": it["sha"],
                                 "bytes": os.path.getsize(it["path"])})
            with open(os.path.join(archive_root, pack_code, "manifest.json"), "w",
                      encoding="utf-8") as fh:
                json.dump({"pack_code": pack_code, "count": len(manifest),
                           "items": manifest}, fh, ensure_ascii=False, indent=1)
            rep["archived"] = len(manifest)
            rep["archive_root"] = os.path.join(archive_root, pack_code)
        except Exception as e:  # noqa: BLE001
            rep["archive_error"] = "%s: %s" % (type(e).__name__, e)
    return rep
