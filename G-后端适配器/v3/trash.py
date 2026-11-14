# -*- coding: utf-8 -*-
"""v3 回收站 + 历史集合删除（2026-10-24 新增）—— 补上 safedelete 的第一阶段缺口

要解决的根因（主人实测）：
  第二步能看到「户外野营凳 184 / 商品明细 143 / 自检 1」三个包，
  点删除却提示「没有这个数据包」，刷新后它们又回来。

为什么：
  · 列表来自 `v3_product.pack_code` 分组（bridge.v3_packs）
  · 删除来自 `v3_pack` 行 / `v3_pack_member` 行
  这三个包**两样都没有** → 删除必然报"不存在"，而商品行还在 → 刷新就又长出来。

本模块给出的三种真实动作（语义必须分开，不许混）：
  detach  解除归属：清掉这些商品的 pack_code。**商品、图片、OCR 全部保留**（最保守，默认）
  trash   移入回收站：商品行整体存进 v3_trash（含媒体 URL 与 OCR 摘要），再删商品；
          **可从回收站还原**
  purge   彻底删除：同上但不留底（需显式调用，界面上要二次确认）

三条硬规矩：
  1. 只要还有**别的包**引用这件商品（成员表或别的 pack_code），默认**不删商品**，只解除归属；
  2. 全程一个事务，任一步失败整体回滚；
  3. 每一步写 v3_audit，删了多少 / 留在回收站多少都能查。
"""
from __future__ import annotations

import json
import sqlite3

from . import safedelete as _sd   # 复用它的 refs_elsewhere / _cols / _audit

TABLES_BY_PID = _sd.TABLES_BY_PID


def ensure_trash(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS v3_trash(
            trash_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            kind        TEXT,          -- product
            ref         TEXT,          -- 原包码
            product_id  INTEGER,
            payload     TEXT,          -- v3_product 整行 JSON（含 original_payload）
            images      TEXT,          -- [{image_id,role,sequence,original_url,local_path}]
            ocr         TEXT,          -- {ok,failed,boxes,hits,hit_words[]}
            actor       TEXT,
            created_at  TEXT,
            restored_at TEXT,
            note        TEXT
        )""")
        conn.commit()
        return True
    except sqlite3.Error:
        return False


def _product_json(conn, pid: int) -> str:
    cols = _sd._cols(conn, "v3_product")
    row = conn.execute("SELECT * FROM v3_product WHERE product_id=?", (pid,)).fetchone()
    return json.dumps(dict(zip(cols, row)), ensure_ascii=False) if row else "{}"


def _images_json(conn, pid: int) -> str:
    """整行存（T1-02 起）：旧格式只存 6 个字段，恢复时必然丢 slat/哈希/尺寸，
    恢复出来的图无法再进 4.1 审核与出表。现在存全部列，恢复时原样插回。"""
    try:
        cols = _sd._cols(conn, "v3_image")
        rows = [dict(zip(cols, r)) for r in conn.execute(
            "SELECT * FROM v3_image WHERE product_id=? ORDER BY image_id", (pid,))]
    except sqlite3.Error:
        rows = []
    return json.dumps(rows, ensure_ascii=False)


def _ocr_json(conn, pid: int) -> str:
    """整行存 + 摘要（T1-02 起）。旧记录只有摘要 → 恢复时如实降级，不假装恢复。"""
    out = {"ok": 0, "failed": 0, "boxes": 0, "hits": 0, "words": [], "rows": []}
    try:
        cols = _sd._cols(conn, "v3_image_ocr")
        for r in conn.execute("SELECT * FROM v3_image_ocr WHERE product_id=?", (pid,)):
            d = dict(zip(cols, r))
            out["rows"].append(d)
            state = (d.get("state") or "")
            if state == "ok":
                out["ok"] += 1
                if str(d.get("boxes_json") or "").strip() not in ("", "[]", "null"):
                    out["boxes"] += 1
            elif state:
                out["failed"] += 1
            try:
                w = json.loads(d.get("hit_words") or "[]")
                if isinstance(w, list) and w:
                    out["hits"] += 1
                    out["words"].extend([str(x) for x in w][:5])
            except Exception:  # noqa: BLE001
                pass
    except sqlite3.Error:
        pass
    out["words"] = sorted(set(out["words"]))[:40]
    return json.dumps(out, ensure_ascii=False)


def legacy_impact(conn: sqlite3.Connection, code: str) -> dict:
    """历史集合的影响清单（删除前必看）。"""
    pids = [int(r[0]) for r in conn.execute(
        "SELECT product_id FROM v3_product WHERE pack_code=? ORDER BY product_id", (code,))]
    shared = [p for p in pids if _sd.refs_elsewhere(conn, p, code)]
    n_img = n_ocr = n_local = 0
    if pids:
        q = ",".join("?" * len(pids))
        n_img = conn.execute("SELECT COUNT(*) FROM v3_image WHERE product_id IN (%s)" % q,
                             pids).fetchone()[0]
        try:
            n_ocr = conn.execute("SELECT COUNT(*) FROM v3_image_ocr WHERE product_id IN (%s)"
                                 % q, pids).fetchone()[0]
        except sqlite3.Error:
            n_ocr = 0
        n_local = conn.execute(
            "SELECT COUNT(*) FROM v3_image WHERE product_id IN (%s)"
            " AND (TRIM(IFNULL(current_local_path,''))<>'' OR"
            "      TRIM(IFNULL(original_local_path,''))<>'')" % q, pids).fetchone()[0]
    in_pack_table = bool(conn.execute("SELECT 1 FROM v3_pack WHERE pack_code=?",
                                      (code,)).fetchone())
    member_rows = 0
    if _sd._cols(conn, "v3_pack_member"):
        member_rows = conn.execute("SELECT COUNT(*) FROM v3_pack_member WHERE pack_code=?",
                                   (code,)).fetchone()[0]
    return {"pack": code, "exists_in_pack_table": in_pack_table,
            "member_rows": int(member_rows),
            "is_legacy": (not in_pack_table) and member_rows == 0,
            "products": len(pids), "shared_products": len(shared),
            "shared_examples": shared[:8], "images": int(n_img),
            "images_with_local_path": int(n_local), "ocr": int(n_ocr)}


def delete_collection(conn: sqlite3.Connection, code: str, mode: str = "purge",
                      purge_shared: bool = False, _fail_after: str = "",
                      cancel_check=None) -> dict:
    """处理「列表里有、包表里没有」的历史集合。

    mode: detach（只解除归属，商品保留）| trash（移入回收站，可还原）| purge（彻底删）

    ★ 2026-10-24 主人口径改了：「下回这种清掉的就不要留了，删除就是彻底删除。」
      所以**默认从 detach/trash 改成 purge**。仍被别的包引用的商品照样只解除归属（purge_shared=False）。

    cancel_check（B-01）：`() -> bool`，True 即取消 → **整事务回滚**（含已 trash 的行），
      返回 {cancelled:true, cancel_stage:"db", rolled_back:true}。
    """
    mode = (mode or "detach").lower()
    if mode not in ("detach", "trash", "purge"):
        return {"ok": False, "error": "mode 只能是 detach / trash / purge"}
    ensure_trash(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        _sd.check(cancel_check, "历史集合清理开始前")
        pre = legacy_impact(conn, code)
        pids = [int(r[0]) for r in conn.execute(
            "SELECT product_id FROM v3_product WHERE pack_code=? ORDER BY product_id", (code,))]
        if not pids and pre["member_rows"] == 0:
            conn.rollback()
            return {"ok": False, "error": "没有这个数据包：%s（包表、成员表、商品归属三处都没有）"
                                          % code}

        detached, trashed, purged, kept_shared = [], [], [], []
        n_img = n_ocr = 0
        del_images: list[dict] = []      # ★ 只收集 mode=purge 的（trash 可还原，文件必须留着）

        if mode == "detach":
            conn.execute("UPDATE v3_product SET pack_code='', updated_at=datetime('now','localtime')"
                         " WHERE pack_code=?", (code,))
            detached = pids
        else:
            for p in pids:
                _sd.check(cancel_check, "清理历史集合")
                if _sd.refs_elsewhere(conn, p, code) and not purge_shared:
                    # 还有别的包要它 → 只解除归属，绝不删
                    conn.execute("UPDATE v3_product SET pack_code='',"
                                 " updated_at=datetime('now','localtime') WHERE product_id=?", (p,))
                    kept_shared.append(p)
                    continue
                if mode == "trash":
                    conn.execute(
                        "INSERT INTO v3_trash(kind,ref,product_id,payload,images,ocr,actor,"
                        " created_at,note) VALUES('product',?,?,?,?,?,'第一阶段清理',"
                        " datetime('now','localtime'),'历史集合移入回收站')",
                        (code, p, _product_json(conn, p), _images_json(conn, p),
                         _ocr_json(conn, p)))
                    trashed.append(p)
                else:
                    purged.append(p)
                    try:
                        for rr in conn.execute(
                                "SELECT image_id, COALESCE(current_local_path,'')"
                                " FROM v3_image WHERE product_id=?", (p,)):
                            del_images.append({"image_id": int(rr[0]),
                                               "product_id": int(p),
                                               "path": rr[1] or ""})
                    except Exception:      # noqa: BLE001
                        pass
                n_img += conn.execute("SELECT COUNT(*) FROM v3_image WHERE product_id=?",
                                      (p,)).fetchone()[0]
                n_ocr += conn.execute("SELECT COUNT(*) FROM v3_image_ocr WHERE product_id=?",
                                      (p,)).fetchone()[0]
                for t in TABLES_BY_PID:
                    c = _sd._cols(conn, t)
                    if c and "product_id" in c:
                        conn.execute("DELETE FROM %s WHERE product_id=?" % t, (p,))
                        if _fail_after == "images" and t == "v3_image":
                            raise RuntimeError("测试注入：删图后失败")
                if _sd._cols(conn, "v3_pack_member"):
                    conn.execute("DELETE FROM v3_pack_member WHERE product_id=?", (p,))
                conn.execute("DELETE FROM v3_product WHERE product_id=?", (p,))
                if _fail_after == "products":
                    raise RuntimeError("测试注入：删商品后失败")

        # 清掉这个集合可能残留的登记行与成员行（哪怕包表里没有，成员行可能有）
        if _sd._cols(conn, "v3_pack_member"):
            conn.execute("DELETE FROM v3_pack_member WHERE pack_code=?", (code,))
        conn.execute("DELETE FROM v3_pack WHERE pack_code=?", (code,))

        gone = [int(r[0]) for r in conn.execute(
            "SELECT COUNT(*) FROM v3_product WHERE pack_code=?", (code,))]
        left = conn.execute("SELECT COUNT(*) FROM v3_product WHERE pack_code=?",
                            (code,)).fetchone()[0]
        _sd._audit(conn, "delete_collection", "pack", code,
                   {"mode": mode, "detached": len(detached), "trashed": len(trashed),
                    "purged": len(purged), "kept_shared": len(kept_shared),
                    "images": int(n_img), "ocr": int(n_ocr),
                    "impact_before": pre})
        conn.commit()
        # ★ 2026-10-26：purge 的商品，磁盘目录与消除产物一起删（trash 模式不动文件）
        rm = _sd._purge_files(purged, del_images) if purged else {"files": 0, "dirs": 0, "bytes": 0}
        return {"ok": True, "pack": code, "mode": mode,
                "detached": len(detached), "trashed": len(trashed), "purged": len(purged),
                "kept_shared": len(kept_shared), "kept_shared_examples": kept_shared[:8],
                "images_touched": int(n_img), "ocr_touched": int(n_ocr),
                "files_deleted": rm["files"], "dirs_deleted": rm["dirs"],
                "bytes_freed": rm["bytes"],
                "products_left_in_pack": left, "impact_before": pre}
    except _sd.Cancelled as e:
        conn.rollback()
        return {"ok": False, "cancelled": True, "cancel_stage": "db", "rolled_back": True,
                "detached": 0, "trashed": 0, "purged": 0,
                "note": "已取消：数据库整体回滚，集合与商品都回到调用前", "error": str(e)}
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e), "rolled_back": True}


def list_trash(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    ensure_trash(conn)
    out = []
    for r in conn.execute(
            "SELECT trash_id, kind, ref, product_id, created_at, restored_at,"
            " COALESCE(payload,'') FROM v3_trash ORDER BY trash_id DESC LIMIT ?", (limit,)):
        title = ""
        try:
            title = str(json.loads(r[6]).get("original_title") or "")[:60]
        except Exception:  # noqa: BLE001
            pass
        out.append({"trash_id": r[0], "kind": r[1], "pack": r[2], "product_id": r[3],
                    "created_at": r[4], "restored_at": r[5], "title": title})
    return out


def _insert_row(conn, table: str, row: dict, must: tuple = ()) -> int:
    """按列交集插一行；主键冲突时把主键置空重试（交给 AUTOINCREMENT）。

    返回新主键（image_id）。只用于恢复：字段多了少了都不炸，缺列就跳过。
    """
    have = _sd._cols(conn, table)
    d = {k: v for k, v in row.items() if k in have}
    for _try in range(2):
        try:
            cur = conn.execute("INSERT INTO %s (%s) VALUES (%s)"
                               % (table, ",".join(d), ",".join("?" * len(d))),
                               list(d.values()))
            return int(cur.lastrowid or (d.get("image_id") or 0))
        except sqlite3.IntegrityError:
            if _try:                       # 第二遍还冲突：真的放不进去
                raise
            for k in must:                 # 主键让位，换新号
                d.pop(k, None)
    return 0


def restore_one(conn: sqlite3.Connection, trash_id: int) -> dict:
    """从回收站还原一件商品：商品行 + 媒体引用 + OCR 明细。

    T1-02 修的两处：
      · 旧实现只 upsert 商品行，**图片和 OCR 一条都不恢复** → 还原出来是「无图商品」，
        界面看着在、其实是空壳（实测 6d 就是这么失败的）。
      · 现在：先把图片整行写回（image_id 冲突就换新号并做映射），再写回 OCR，
        最后把 main_image_id 指回还原后的那张主图。旧格式存档（只有摘要）如实降级，
        在 note 和返回值里写明「OCR 仅有摘要，未逐条恢复」，不假装成功。
    """
    ensure_trash(conn)
    row = conn.execute(
        "SELECT payload, ref, restored_at, COALESCE(images,'[]'), COALESCE(ocr,'{}')"
        " FROM v3_trash WHERE trash_id=?", (trash_id,)).fetchone()
    if not row:
        return {"ok": False, "error": "回收站里没有这条记录"}
    if row[2]:
        return {"ok": False, "error": "这条已经还原过了（%s）" % row[2]}
    try:
        d = json.loads(row[0] or "{}")
        imgs = json.loads(row[3] or "[]")
        ocr = json.loads(row[4] or "{}")
    except Exception as e:                      # noqa: BLE001
        return {"ok": False, "error": "存档损坏：%s" % e}

    conn.execute("BEGIN IMMEDIATE")
    try:
        from . import store                       # noqa: PLC0415
        orig_pid = int(d.get("product_id") or 0)
        rec = {k: v for k, v in d.items()
               if k not in ("product_id", "created_at", "updated_at", "data_grade")}
        rec["pack_code"] = d.get("pack_code") or row[1] or ""

        # 1) 商品行：原 id 空闲就原样回去（外部引用不变），否则按唯一键 upsert
        free = bool(orig_pid) and not conn.execute(
            "SELECT 1 FROM v3_product WHERE product_id=?", (orig_pid,)).fetchone()
        if free:
            have = _sd._cols(conn, "v3_product")
            use = {k: v for k, v in d.items() if k in have}
            use["product_id"] = orig_pid
            conn.execute("INSERT INTO v3_product (%s) VALUES (%s)"
                         % (",".join(use), ",".join("?" * len(use))), list(use.values()))
            pid, created = orig_pid, True
        else:
            pid, created = store.upsert_product(conn, rec)

        # 2) 媒体：整行写回，image_id 冲突则换号并记映射
        imap: dict[int, int] = {}
        n_img = 0
        for it in (imgs or []):
            if not isinstance(it, dict):
                continue
            r = dict(it)
            old_id = int(r.get("image_id") or 0)
            r["product_id"] = pid
            new_id = _insert_row(conn, "v3_image", r, must=("image_id",))
            if new_id:
                n_img += 1
                if old_id:
                    imap[old_id] = new_id

        # 3) OCR：新格式逐条写回；旧格式（只有摘要）如实降级
        n_ocr = 0
        ocr_note = ""
        rows = ocr.get("rows") if isinstance(ocr, dict) else None
        if rows:
            for it in rows:
                if not isinstance(it, dict):
                    continue
                r = dict(it)
                old_img = int(r.get("image_id") or 0)
                r["image_id"] = imap.get(old_img, old_img)
                r["product_id"] = pid
                if not r["image_id"]:
                    continue
                if _insert_row(conn, "v3_image_ocr", r, must=("image_id",)):
                    n_ocr += 1
        elif isinstance(ocr, dict) and (ocr.get("ok") or ocr.get("failed")):
            ocr_note = ("旧格式存档：OCR 只有摘要（成功 %s / 失败 %s / 带坐标 %s），"
                        "未逐条恢复" % (ocr.get("ok"), ocr.get("failed"), ocr.get("boxes")))

        # 4) 主图指针指回（原主图若在映射里）
        old_main = int(d.get("main_image_id") or 0)
        if old_main and old_main in imap:
            conn.execute("UPDATE v3_product SET main_image_id=? WHERE product_id=?",
                         (imap[old_main], pid))

        conn.execute("UPDATE v3_trash SET restored_at=datetime('now','localtime'),"
                     " note=COALESCE(note,'')||? WHERE trash_id=?",
                     (" | 恢复：图 %d 条 / OCR %d 条%s"
                      % (n_img, n_ocr, ("｜" + ocr_note) if ocr_note else ""), trash_id))
        conn.commit()
        return {"ok": True, "trash_id": trash_id, "product_id": pid,
                "pack_code": rec["pack_code"], "created_new": bool(created),
                "images_restored": n_img, "ocr_restored": n_ocr,
                "ocr_note": ocr_note}
    except Exception as e:                      # noqa: BLE001
        conn.rollback()
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
