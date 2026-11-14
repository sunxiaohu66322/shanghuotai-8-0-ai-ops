# -*- coding: utf-8 -*-
"""彻底删除 / 孤儿清理 / 回收站清空 —— 2026-10-24 主人口径

主人口径（原话）：
  「后端回收站还有那边的都清掉。下回这种清掉的就不要留了，删除就是彻底删除。」

所以本模块与 trash.py 的分工改成：
  trash.py   还保留 detach（只解除归属）与 restore（历史数据还原能力，不删代码）
  purge.py   **默认动作**：删商品 + 删媒体引用 + 删 OCR + 删本地文件，不留底

四条硬规矩：
  1. **共享文件不删**：同一份本地文件若还有**别的商品**的图片行引用它，文件保留，只删行。
  2. 商品若还被**别的包**引用（成员表 / 别的 pack_code），默认跳过并单独报出来 —— 不静默删。
  3. 全程一个事务；文件删除放在提交**之后**（先删库再删盘，避免删了盘回滚不了）。
  4. 每一步写 v3_audit：删了多少件 / 多少图 / 多少 OCR / 多少文件 / 多少字节。
"""
from __future__ import annotations

import json
import os
import sqlite3

from . import safedelete as _sd

TABLES_BY_PID = _sd.TABLES_BY_PID


def _chunks(seq, n=400):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _cols(conn, t):
    return _sd._cols(conn, t)


def _members_of(conn, pid: int) -> list[str]:
    """这件商品还挂在哪些包里（成员表 + pack_code）。"""
    out = set()
    try:
        for r in conn.execute("SELECT pack_code FROM v3_pack_member WHERE product_id=?", (pid,)):
            if r[0]:
                out.add(str(r[0]))
    except sqlite3.Error:
        pass
    row = conn.execute("SELECT COALESCE(pack_code,'') FROM v3_product WHERE product_id=?",
                       (pid,)).fetchone()
    if row and row[0]:
        out.add(str(row[0]))
    return sorted(out)


def _paths(conn, pids: list[int]) -> list[str]:
    out = set()
    for ch in _chunks(pids):
        q = ",".join("?" * len(ch))
        for r in conn.execute(
                "SELECT COALESCE(current_local_path,''), COALESCE(original_local_path,'')"
                " FROM v3_image WHERE product_id IN (%s)" % q, ch):
            for p in r:
                if p and p.strip():
                    out.add(os.path.normcase(os.path.abspath(p.strip())))
    return sorted(out)


def _paths_kept_by_others(conn, pids: list[int]) -> set[str]:
    """别的商品还在引用的文件（这些不能删）。"""
    keep = set()
    skip = set(pids)
    for r in conn.execute("SELECT product_id, COALESCE(current_local_path,''),"
                          " COALESCE(original_local_path,'') FROM v3_image"):
        if int(r[0]) in skip:
            continue
        for p in (r[1], r[2]):
            if p and p.strip():
                keep.add(os.path.normcase(os.path.abspath(p.strip())))
    return keep


def impact(conn: sqlite3.Connection, pids: list[int], delete_files: bool = True) -> dict:
    """删除前的影响清单（必须给人看，不许闷头删）。"""
    pids = [int(x) for x in pids]
    if not pids:
        return {"ok": False, "error": "没有选中任何商品"}
    n_img = n_ocr = n_local = 0
    shared: dict[int, list[str]] = {}
    rows = []
    for ch in _chunks(pids):
        q = ",".join("?" * len(ch))
        n_img += conn.execute("SELECT COUNT(*) FROM v3_image WHERE product_id IN (%s)"
                              % q, ch).fetchone()[0]
        try:
            n_ocr += conn.execute("SELECT COUNT(*) FROM v3_image_ocr WHERE product_id IN (%s)"
                                  % q, ch).fetchone()[0]
        except sqlite3.Error:
            pass
        n_local += conn.execute(
            "SELECT COUNT(*) FROM v3_image WHERE product_id IN (%s) AND"
            " (TRIM(IFNULL(current_local_path,''))<>'' OR"
            "  TRIM(IFNULL(original_local_path,''))<>'')" % q, ch).fetchone()[0]
    for p in pids:
        mem = _members_of(conn, p)
        # 只有一个包（就是它自己那个）不算共享
        if len([m for m in mem if m]) > 1:
            shared[p] = mem
        t = conn.execute("SELECT COALESCE(original_title,'') FROM v3_product WHERE product_id=?",
                         (p,)).fetchone()
        rows.append({"product_id": p, "title": (t[0] if t else "")[:40], "packs": mem})

    paths = _paths(conn, pids)
    kept = _paths_kept_by_others(conn, pids)
    del_paths = [p for p in paths if p not in kept]
    size = 0
    exist = 0
    for p in del_paths:
        try:
            if os.path.isfile(p):
                size += os.path.getsize(p)
                exist += 1
        except OSError:
            pass
    return {"ok": True, "products": len(pids), "images": int(n_img), "ocr": int(n_ocr),
            "refs_with_local_path": int(n_local),
            "files_to_delete": exist, "bytes_to_delete": int(size),
            "files_shared_kept": len([p for p in paths if p in kept]),
            "shared_products": shared, "shared_count": len(shared),
            "items": rows, "delete_files": bool(delete_files)}


def purge_products(conn: sqlite3.Connection, pids: list[int], actor: str = "2.5",
                   delete_files: bool = True, force_shared: bool = False,
                   cancel_check=None) -> dict:
    """彻底删除商品（默认动作）。返回真实删了多少。

    cancel_check（B-01）：`() -> bool`，True 即取消。
      · DB 阶段取消 → 整事务回滚，一件都没删；
      · 文件阶段取消 → **停止继续删**，已删的文件不回来（如实报 files_deleted）。
    """
    pids = [int(x) for x in pids]
    if not pids:
        return {"ok": False, "error": "没有选中任何商品"}
    pre = impact(conn, pids, delete_files)
    todo, skipped = [], []
    for p in pids:
        if pre["shared_products"].get(p) and not force_shared:
            skipped.append(p)
        else:
            todo.append(p)
    if not todo:
        return {"ok": False, "error": "选中的商品都还被别的包引用，未删除（先解除归属，或加 force_shared）",
                "skipped_shared": skipped, "impact_before": pre}

    keep_paths = _paths_kept_by_others(conn, todo)
    my_paths = [p for p in _paths(conn, todo) if p not in keep_paths]

    conn.execute("BEGIN IMMEDIATE")
    try:
        n_img = n_ocr = 0
        done = 0
        for p in todo:
            _sd.check(cancel_check, "删除商品")
            n_img += conn.execute("SELECT COUNT(*) FROM v3_image WHERE product_id=?",
                                  (p,)).fetchone()[0]
            try:
                n_ocr += conn.execute("SELECT COUNT(*) FROM v3_image_ocr WHERE product_id=?",
                                      (p,)).fetchone()[0]
            except sqlite3.Error:
                pass
            for t in TABLES_BY_PID:
                c = _cols(conn, t)
                if c and "product_id" in c:
                    conn.execute("DELETE FROM %s WHERE product_id=?" % t, (p,))
            if _cols(conn, "v3_pack_member"):
                conn.execute("DELETE FROM v3_pack_member WHERE product_id=?", (p,))
            conn.execute("DELETE FROM v3_product WHERE product_id=?", (p,))
            done += 1
        conn.commit()
    except _sd.Cancelled as e:
        conn.rollback()
        return {"ok": False, "cancelled": True, "cancel_stage": "db", "rolled_back": True,
                "purged_products": 0, "files_deleted": 0,
                "note": "已取消：数据库整体回滚，一件商品都没删，本地文件一个没动",
                "error": str(e), "impact_before": pre}
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e), "rolled_back": True}

    # 先删库、后删盘：盘上失败不回滚（库里已经没有引用，文件留着只是占空间）
    # ★ B-01：文件阶段取消只代表「不再继续删」，已删的不回来 —— 不许说成全部回滚。
    n_file = n_bytes = n_fail = 0
    cancelled_files = False
    if delete_files:
        for i, p in enumerate(my_paths):
            if cancel_check is not None and i % 25 == 0 and cancel_check():
                cancelled_files = True
                break
            try:
                if os.path.isfile(p):
                    n_bytes += os.path.getsize(p)
                    os.remove(p)
                    n_file += 1
            except OSError:
                n_fail += 1
        # 空目录顺手清掉（只清空目录，不递归删有内容的）
        for p in my_paths:
            d = os.path.dirname(p)
            try:
                if os.path.isdir(d) and not os.listdir(d):
                    os.rmdir(d)
            except OSError:
                pass

    _sd._audit(conn, "purge_products", "product",
               ",".join(str(x) for x in todo[:20]) + ("…" if len(todo) > 20 else ""),
               {"products": len(todo), "images": int(n_img), "ocr": int(n_ocr),
                "files": n_file, "bytes": n_bytes, "file_fail": n_fail,
                "cancelled_files": cancelled_files,
                "skipped_shared": skipped, "actor": actor})
    conn.commit()
    out = {"ok": True, "purged_products": len(todo), "skipped_shared": skipped,
           "images": int(n_img), "ocr": int(n_ocr),
           "files_deleted": n_file, "bytes_deleted": n_bytes, "file_failed": n_fail,
           "files_shared_kept": len(keep_paths), "impact_before": pre}
    if cancelled_files:
        out.update({"cancelled": True, "cancel_stage": "files",
                    "files_planned": len(my_paths),
                    "note": "已取消：数据库删除已完成（不可撤销），文件已删 %d / 计划 %d，其余保留"
                            % (n_file, len(my_paths))})
    return out


def detach_products(conn: sqlite3.Connection, pids: list[int], actor: str = "2.5",
                    cancel_check=None) -> dict:
    """只解除归属（清 pack_code + 成员行），商品/图片/OCR/文件全部保留。"""
    pids = [int(x) for x in pids]
    if not pids:
        return {"ok": False, "error": "没有选中任何商品"}
    conn.execute("BEGIN IMMEDIATE")
    try:
        n = 0
        for p in pids:
            _sd.check(cancel_check, "解除归属")
            cur = conn.execute("UPDATE v3_product SET pack_code='',"
                               " updated_at=datetime('now','localtime') WHERE product_id=?", (p,))
            n += cur.rowcount or 0
            if _cols(conn, "v3_pack_member"):
                conn.execute("DELETE FROM v3_pack_member WHERE product_id=?", (p,))
        conn.commit()
    except _sd.Cancelled as e:
        conn.rollback()
        return {"ok": False, "cancelled": True, "cancel_stage": "db", "rolled_back": True,
                "detached": 0, "note": "已取消：整体回滚，没有任何商品被移出", "error": str(e)}
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    _sd._audit(conn, "detach_products", "product", str(len(pids)), {"products": n, "actor": actor})
    conn.commit()
    return {"ok": True, "detached": n}


def trash_count(conn: sqlite3.Connection) -> int:
    try:
        return conn.execute("SELECT COUNT(*) FROM v3_trash").fetchone()[0]
    except sqlite3.Error:
        return 0


def clear_trash(conn: sqlite3.Connection, actor: str = "2.5", delete_files: bool = True,
                cancel_check=None) -> dict:
    """把回收站清空（不留底）。主人口径：以后的删除直接彻底删，不再进回收站。

    历史 328 件在回收站里存着整套 payload / images / ocr 摘要，清空就是删除这些行。
    它们的本地文件多数早已不存在；存在的按共享规则检查后再删。

    cancel_check（B-01）：**只能取消文件阶段**（`DELETE FROM v3_trash` 是一条语句，
    没法中途停）。取消时数据库已经清空 —— 如实报 cancel_stage="files"，不假装回滚。
    """
    n = trash_count(conn)
    if not n:
        return {"ok": True, "cleared": 0, "message": "回收站已经是空的"}
    try:
        _sd.check(cancel_check, "清空回收站开始前")
    except _sd.Cancelled as e:
        return {"ok": False, "cancelled": True, "cancel_stage": "before", "cleared": 0,
                "files_deleted": 0, "note": "已取消：回收站一条没动", "error": str(e)}
    paths = set()
    for r in conn.execute("SELECT COALESCE(images,'') FROM v3_trash"):
        try:
            for it in json.loads(r[0] or "[]"):
                p = (it or {}).get("local") or ""
                if p.strip():
                    paths.add(os.path.normcase(os.path.abspath(p.strip())))
        except Exception:  # noqa: BLE001
            pass
    # 库里还活着的引用，一律不删
    alive = set()
    for r in conn.execute("SELECT COALESCE(current_local_path,''),"
                          " COALESCE(original_local_path,'') FROM v3_image"):
        for p in r:
            if p and p.strip():
                alive.add(os.path.normcase(os.path.abspath(p.strip())))
    del_paths = [p for p in paths if p not in alive]

    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute("DELETE FROM v3_trash")
        gone = cur.rowcount or n
        conn.commit()
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}

    n_file = n_bytes = 0
    cancelled_files = False
    if delete_files:
        for i, p in enumerate(del_paths):
            if cancel_check is not None and i % 25 == 0 and cancel_check():
                cancelled_files = True
                break
            try:
                if os.path.isfile(p):
                    n_bytes += os.path.getsize(p)
                    os.remove(p)
                    n_file += 1
            except OSError:
                pass
    _sd._audit(conn, "clear_trash", "trash", str(gone),
               {"cleared": int(gone), "files": n_file, "bytes": n_bytes,
                "paths_seen": len(paths), "kept_alive": len(paths) - len(del_paths),
                "cancelled_files": cancelled_files, "actor": actor})
    conn.commit()
    out = {"ok": True, "cleared": int(gone), "files_deleted": n_file,
           "bytes_deleted": n_bytes, "paths_seen": len(paths),
           "files_kept_alive": len(paths) - len(del_paths)}
    if cancelled_files:
        out.update({"cancelled": True, "cancel_stage": "files",
                    "files_planned": len(del_paths),
                    "note": "已取消：回收站记录已清空（不可撤销），文件已删 %d / 计划 %d"
                            % (n_file, len(del_paths))})
    return out


def orphan_ocr(conn: sqlite3.Connection, actor: str = "2.5") -> dict:
    """OCR 行对应的图片已经不在了 → 删掉（留一份清单到 docs）。"""
    rows = [dict(zip(("image_id", "product_id", "role"), r)) for r in conn.execute(
        "SELECT o.image_id, o.product_id, COALESCE(o.role,'') FROM v3_image_ocr o"
        " WHERE NOT EXISTS(SELECT 1 FROM v3_image i WHERE i.image_id=o.image_id)")]
    if not rows:
        return {"ok": True, "orphan": 0, "deleted": 0}
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute("DELETE FROM v3_image_ocr WHERE NOT EXISTS"
                           "(SELECT 1 FROM v3_image i WHERE i.image_id=v3_image_ocr.image_id)")
        d = cur.rowcount or len(rows)
        conn.commit()
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    _sd._audit(conn, "orphan_ocr", "ocr", str(len(rows)), {"deleted": int(d), "actor": actor})
    conn.commit()
    return {"ok": True, "orphan": len(rows), "deleted": int(d), "items": rows[:50]}


def scan_orphan_files(conn: sqlite3.Connection, roots: list[str] | None = None) -> dict:
    """扫媒体目录里没人引用的文件（不删，只出清单）。"""
    if roots is None:
        roots = [os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "data", "v3", "images")]
    alive = set()
    for r in conn.execute("SELECT COALESCE(current_local_path,''),"
                          " COALESCE(original_local_path,'') FROM v3_image"):
        for p in r:
            if p and p.strip():
                alive.add(os.path.normcase(os.path.abspath(p.strip())))
    found = []
    total = 0
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                p = os.path.normcase(os.path.abspath(os.path.join(dirpath, fn)))
                if p in alive:
                    continue
                try:
                    sz = os.path.getsize(p)
                except OSError:
                    sz = 0
                found.append({"path": p, "bytes": sz})
                total += sz
    return {"ok": True, "orphan_files": len(found), "bytes": total,
            "referenced": len(alive), "roots": roots, "items": found}


def purge_orphan_files(conn: sqlite3.Connection, manifest: list[dict], actor: str = "2.5",
                       roots: list[str] | None = None, cancel_check=None) -> dict:
    """按清单删孤儿文件（清单必须是刚从 scan_orphan_files 出来的，删前再校验一次引用）。

    cancel_check（B-01）：纯文件操作，取消 = 停止继续删；已删的不回来，如实报 deleted。
    """
    alive = set()
    for r in conn.execute("SELECT COALESCE(current_local_path,''),"
                          " COALESCE(original_local_path,'') FROM v3_image"):
        for p in r:
            if p and p.strip():
                alive.add(os.path.normcase(os.path.abspath(p.strip())))
    n = b = skip = fail = 0
    cancelled = False
    for i, it in enumerate(manifest):
        if cancel_check is not None and i % 25 == 0 and cancel_check():
            cancelled = True
            break
        p = os.path.normcase(os.path.abspath(it.get("path") or ""))
        if not p or p in alive:
            skip += 1
            continue
        try:
            if os.path.isfile(p):
                b += os.path.getsize(p)
                os.remove(p)
                n += 1
        except OSError:
            fail += 1
    if roots is None:
        roots = [os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "data", "v3", "images")]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root, topdown=False):
            if dirpath == root:
                continue
            try:
                if not os.listdir(dirpath):
                    os.rmdir(dirpath)
            except OSError:
                pass
    _sd._audit(conn, "purge_orphan_files", "file", str(n),
               {"deleted": n, "bytes": b, "skipped_alive": skip, "failed": fail,
                "cancelled": cancelled, "actor": actor})
    conn.commit()
    out = {"ok": True, "deleted": n, "bytes": b, "skipped_alive": skip, "failed": fail}
    if cancelled:
        out.update({"cancelled": True, "cancel_stage": "files", "files_planned": len(manifest),
                    "note": "已取消：孤儿文件已删 %d / 计划 %d，其余保留" % (n, len(manifest))})
    return out
