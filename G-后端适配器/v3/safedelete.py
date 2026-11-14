# -*- coding: utf-8 -*-
"""v3 数据包「安全删除」—— 阶段 B 的核心修复（A1）

要解决的根因（阶段 A 审计）：
  旧 delete_pack(purge=True) 按 `v3_product.pack_code = 本包` 取商品，
  删掉商品与图片，**却不清其他包里指向这些商品的成员行** →
  被引用的共享商品被连坐删除，留下 176 条悬空成员。

新逻辑（四条硬规矩）：
  1. 删包默认只删「包」和「该包的成员关系」，商品与图片不动。
  2. 商品仍被任何其他包引用（pack_code 或 v3_pack_member 指向它）→ **绝不删除**。
  3. 彻底删商品必须显式 purge_shared=True（界面上要二次确认）。
  4. 全流程在一个事务里：任一步失败 → 整体回滚，库回到调用前。
  5. 删商品时，**所有包**指向这些商品的成员行一并清掉 → 不留悬空。
  6. 返回影响面：包数/商品数/图片数/OCR 数/共享保留数，供删除前预告。

只读写传入的连接；本模块不做路径决策，也不碰文件系统。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3

TABLES_BY_PID = ("v3_image_ocr", "v3_image", "v3_title_variant", "v3_job")


# ---------------- 取消（B-01，2026-10-25）----------------
# 语义（写死在这里，界面与报告都按这个说，不许含糊）：
#   · **数据库阶段**：一个事务，取消 = 抛 Cancelled → 整体 rollback，库回到调用前（原子）。
#   · **文件阶段**：在事务提交之后才动盘，取消 = **停止继续删**，已经删掉的文件**不回来**。
#     所以取消结果必须如实分两段报：db=rolled_back / files=已删 N 个（不是"全部回滚"）。
class Cancelled(RuntimeError):
    """用户取消。故意继承 RuntimeError，老的 except Exception 分支也能兜住。"""


def check(cancel_check, where: str = "") -> None:
    """取消检查点。cancel_check 为 None 表示不可取消（默认行为不变）。"""
    if cancel_check is not None and cancel_check():
        raise Cancelled("用户取消" + (("（%s）" % where) if where else ""))


def _cols(conn, table: str) -> list[str]:
    try:
        return [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)]
    except sqlite3.Error:
        return []


def _audit(conn, action: str, kind: str, ref: str, detail: dict) -> None:
    """写审计；表结构不认识就跳过，绝不让审计把主流程搞挂。

    ★ 2026-10-24 修：库里真实的 v3_audit 列是
        (audit_id, ts, actor, action, entity, entity_id, before, after)
      而本函数原来只认 kind/ref/detail/created_at —— 结果**只写进 action 和 actor，
      kind/ref/detail 全丢**（实测 1363 条审计里明细是空的）。
      现在按"表里有什么列就映射什么"来写：老表不坏，新表也能留住明细。
    """
    c = _cols(conn, "v3_audit")
    if not c:
        return
    d = json.dumps(detail, ensure_ascii=False)
    pairs = []
    for keys, val in ((("action",), action),
                      (("kind", "entity"), kind),
                      (("ref", "entity_id"), ref),
                      (("actor",), "上货台·安全删除"),
                      (("detail", "after"), d)):
        for k in keys:
            if k in c:
                pairs.append((k, val))
                break
    for k in ("created_at", "ts"):
        if k in c:
            pairs.append((k, "__now__"))
            break
    if not pairs:
        return
    names = [k for k, _ in pairs]
    ph = ["datetime('now','localtime')" if v == "__now__" else "?" for _, v in pairs]
    args = [v for _, v in pairs if v != "__now__"]
    conn.execute("INSERT INTO v3_audit(%s) VALUES(%s)" % (",".join(names), ",".join(ph)), args)


def pids_of(conn, code: str) -> list[int]:
    """这个包"拥有"的商品：自有 pack_code + 成员表，两路合并去重。"""
    s = set()
    for r in conn.execute("SELECT product_id FROM v3_product WHERE pack_code=?", (code,)):
        s.add(int(r[0]))
    if _cols(conn, "v3_pack_member"):
        for r in conn.execute(
                "SELECT product_id FROM v3_pack_member WHERE pack_code=?", (code,)):
            s.add(int(r[0]))
    return sorted(s)


def refs_elsewhere(conn, pid: int, code: str) -> list[str]:
    """除 code 之外，还有哪些包引用这件商品。"""
    out = set()
    for r in conn.execute(
            "SELECT pack_code FROM v3_product WHERE product_id=? AND pack_code IS NOT NULL "
            "AND pack_code<>'' AND pack_code<>?", (pid, code)):
        out.add(r[0])
    if _cols(conn, "v3_pack_member"):
        for r in conn.execute("SELECT pack_code FROM v3_pack_member "
                              "WHERE product_id=? AND pack_code<>?", (pid, code)):
            out.add(r[0])
    return sorted(x for x in out if x)


def dangling_members(conn) -> list[dict]:
    """所有指向"已不存在的商品"的成员行（按包聚合）。"""
    if not _cols(conn, "v3_pack_member"):
        return []
    rows = conn.execute("""
        SELECT m.pack_code, COUNT(*) AS n
        FROM v3_pack_member m
        WHERE NOT EXISTS (SELECT 1 FROM v3_product p WHERE p.product_id=m.product_id)
        GROUP BY m.pack_code ORDER BY n DESC""").fetchall()
    return [{"pack_code": r[0], "n": int(r[1])} for r in rows]


def impact(conn, code: str, purge_products: bool = True) -> dict:
    """删除前预告：会动到多少包/商品/图片/OCR，有多少商品因被别处引用而保留。"""
    pids = pids_of(conn, code)
    shared, kill = [], []
    for p in pids:
        (shared if refs_elsewhere(conn, p, code) else kill).append(p)
    target = kill if purge_products else []
    n_img = n_ocr = 0
    if target:
        q = ",".join("?" * len(target))
        n_img = conn.execute("SELECT COUNT(*) FROM v3_image WHERE product_id IN (%s)" % q,
                             target).fetchone()[0]
        n_ocr = conn.execute("SELECT COUNT(*) FROM v3_image_ocr WHERE product_id IN (%s)" % q,
                             target).fetchone()[0]
    members = 0
    if _cols(conn, "v3_pack_member"):
        members = conn.execute("SELECT COUNT(*) FROM v3_pack_member WHERE pack_code=?",
                               (code,)).fetchone()[0]
    return {"pack": code, "owns_products": len(pids),
            "will_delete_products": len(target) if purge_products else 0,
            "kept_shared_products": len(shared),
            "shared_examples": shared[:5],
            "will_delete_images": n_img, "will_delete_ocr": n_ocr,
            "will_delete_members": members,
            "purge_products": bool(purge_products),
            "dangling_before": sum(x["n"] for x in dangling_members(conn))}


def _v3_data_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "data", "v3"))


def _purge_files(del_pids, del_images) -> dict:
    """★ 2026-10-26 新增：删这些商品在**磁盘**上的图片目录与消除产物。

    只在数据库 **commit 之后**调用 —— 事务回滚了文件必须还在。

    为什么需要它（主人原话：「删除选中的包，它下载的文件不会被清除……
    删掉以后重新下，它却说没有待下载的媒体」）：
      只删 DB 行不动磁盘 ⇒ 目录还在 ⇒ 下次导入/下载看到文件存在就标
      `dl_state=ok` ⇒ 待下载清单变成 0 条，永远重下不了。

    删除范围（**严格限定在 data\\v3 下，越界一律不删**）：
      · data\\v3\\images\\<商品ID>\\          整个目录（主图/详情图/SKU图/视频/product.json）
      · data\\v3\\masked\\<image_id>_masked.jpg   消除产物
      · v3_image.current_local_path 指向的文件（仅当它落在 images 根之下）
    """
    files = dirs = freed = 0
    base = _v3_data_dir()
    img_root = os.path.join(base, "images")
    mask_dir = os.path.join(base, "masked")

    for pid in (del_pids or []):
        d = os.path.join(img_root, str(int(pid)))
        if not os.path.isdir(d):
            continue
        for r_, _ds, fs in os.walk(d):
            for f in fs:
                try:
                    freed += os.path.getsize(os.path.join(r_, f))
                except OSError:
                    pass
                files += 1
        try:
            shutil.rmtree(d)
            dirs += 1
        except OSError:
            pass

    for it in (del_images or []):
        iid = int(it.get("image_id") or 0)
        if iid:
            p = os.path.join(mask_dir, "%d_masked.jpg" % iid)
            if os.path.isfile(p):
                try:
                    freed += os.path.getsize(p)
                    os.remove(p)
                    files += 1
                except OSError:
                    pass
        lp = (it.get("path") or "").strip()
        if lp and os.path.isfile(lp):
            ap = os.path.normpath(os.path.abspath(lp))
            if ap.startswith(os.path.normpath(img_root) + os.sep):
                try:
                    freed += os.path.getsize(ap)
                    os.remove(ap)
                    files += 1
                except OSError:
                    pass
    return {"files": files, "dirs": dirs, "bytes": freed}


def delete_pack(conn: sqlite3.Connection, code: str, purge_products: bool = False,
                purge_shared: bool = False, _fail_after: str = "",
                cancel_check=None) -> dict:
    """安全删包。默认 purge_products=False：只删包与成员关系。

    purge_products=True  → 删"没有别处引用"的商品（及图/OCR/标题/任务）
    purge_shared=True    → 连"仍被别处引用"的商品一起删（需界面二次确认）
    全程一个事务；_fail_after 只给测试注入故障用（'images' / 'products'）。
    cancel_check：可选的 `() -> bool`；返回 True 即取消 —— **整事务回滚**，
    返回 {ok:false, cancelled:true, rolled_back:true}，库回到调用前。
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        check(cancel_check, "删包开始前")
        pre = impact(conn, code, purge_products)
        pids = pids_of(conn, code)
        del_pids = []
        if purge_products:
            for p in pids:
                check(cancel_check, "清点待删商品")
                if purge_shared or not refs_elsewhere(conn, p, code):
                    del_pids.append(p)

        n_img = n_ocr = 0
        del_images: list[dict] = []       # ★ commit 之后才删盘，回滚了文件必须还在
        if del_pids:
            check(cancel_check, "删商品前")
            q = ",".join("?" * len(del_pids))
            n_ocr = conn.execute("SELECT COUNT(*) FROM v3_image_ocr WHERE product_id IN (%s)"
                                 % q, del_pids).fetchone()[0]
            n_img = conn.execute("SELECT COUNT(*) FROM v3_image WHERE product_id IN (%s)"
                                 % q, del_pids).fetchone()[0]
            try:
                for r in conn.execute(
                        "SELECT image_id, product_id, COALESCE(current_local_path,'')"
                        " FROM v3_image WHERE product_id IN (%s)" % q, del_pids):
                    del_images.append({"image_id": int(r[0]), "product_id": int(r[1]),
                                       "path": r[2] or ""})
            except sqlite3.Error:
                del_images = []
            for t in TABLES_BY_PID:
                if _cols(conn, t) and "product_id" in _cols(conn, t):
                    conn.execute("DELETE FROM %s WHERE product_id IN (%s)" % (t, q), del_pids)
                    if _fail_after == "images" and t == "v3_image":
                        raise RuntimeError("测试注入：删图后失败")
            # ★ 关键：清掉「所有包」指向这些商品的成员行，杜绝悬空
            if _cols(conn, "v3_pack_member"):
                conn.execute("DELETE FROM v3_pack_member WHERE product_id IN (%s)" % q, del_pids)
            conn.execute("DELETE FROM v3_product WHERE product_id IN (%s)" % q, del_pids)
            if _fail_after == "products":
                raise RuntimeError("测试注入：删商品后失败")

        if _cols(conn, "v3_pack_member"):
            conn.execute("DELETE FROM v3_pack_member WHERE pack_code=?", (code,))
        conn.execute("DELETE FROM v3_pack WHERE pack_code=?", (code,))
        # ★ T1-02 修：purge_products=False 时，商品会继续挂着这个**已经不存在的包名**。
        #   影响：library._pack_facets() 是 `select distinct pack_code from v3_product`，
        #   于是 2.0 左树里出现「包还在、点进去还有货」的幽灵包 —— 用户看到的就是
        #   「包明明删了，2.5 里还有」。这里把归属清空（商品/图片/OCR/文件一个不动），
        #   清了多少写进审计，可核对。
        detached = 0
        if not purge_products:
            try:
                cur = conn.execute(
                    "UPDATE v3_product SET pack_code='',"
                    " updated_at=datetime('now','localtime') WHERE pack_code=?", (code,))
                detached = int(cur.rowcount or 0)
            except sqlite3.Error:
                detached = 0
        _audit(conn, "delete", "pack", code,
               {"purge": purge_products, "purge_shared": purge_shared,
                "products_deleted": len(del_pids), "images_deleted": n_img,
                "ocr_deleted": n_ocr, "kept_shared": pre["kept_shared_products"],
                "detached_pack_code": detached})
        conn.commit()
        # ★ 2026-10-26：**事务已提交**，现在才动磁盘。
        #   只删了 DB 行、不动文件的话，目录还在 → 下次导入/下载看到文件就标
        #   dl_state=ok → 待下载清单变 0 条，永远重下不了（主人实测到的问题）。
        rm = _purge_files(del_pids, del_images) if del_pids else {"files": 0, "dirs": 0, "bytes": 0}
        return {"ok": True, "pack": code,
                "products_deleted": len(del_pids),
                "products_detached": detached,
                "products_kept_shared": pre["kept_shared_products"],
                "images_deleted": n_img, "ocr_deleted": n_ocr,
                "files_deleted": rm["files"], "dirs_deleted": rm["dirs"],
                "bytes_freed": rm["bytes"],
                "impact_before": pre,
                "dangling_after": sum(x["n"] for x in dangling_members(conn))}
    except Cancelled as e:
        conn.rollback()
        return {"ok": False, "cancelled": True, "cancel_stage": "db",
                "rolled_back": True, "note": "已取消：数据库整体回滚，没有任何商品被删除",
                "error": str(e)}
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                "rolled_back": True}


def prune_dangling(conn: sqlite3.Connection, dry_run: bool = True) -> dict:
    """清掉指向不存在商品的成员行（只动成员表，不碰商品/图片）。"""
    dl = dangling_members(conn)
    total = sum(x["n"] for x in dl)
    if dry_run or total == 0:
        return {"ok": True, "dry_run": True, "dangling": total, "by_pack": dl}
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            "DELETE FROM v3_pack_member WHERE NOT EXISTS ("
            "SELECT 1 FROM v3_product p WHERE p.product_id=v3_pack_member.product_id)")
        n = cur.rowcount
        _audit(conn, "prune", "pack_member", "-",
               {"deleted": n, "by_pack": dl, "note": "悬空成员清理：只删引用，不删商品"})
        conn.commit()
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    return {"ok": True, "dry_run": False, "deleted": n,
            "dangling_after": sum(x["n"] for x in dangling_members(conn))}
