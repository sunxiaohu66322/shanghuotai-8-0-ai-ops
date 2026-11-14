# -*- coding: utf-8 -*-
"""v3 · 主图交接（P3）

本版**不接任何出图 API、不产生任何费用**（任务书 §5）。只做本地这一半：

    预览原图 → 复制本地路径 → 打开所在文件夹 → 复制当前选用标题
      → [你在外部网站人工处理]
      → 导入新主图 → 并排预览 → 确认替换 / 撤销

硬约束：
  · 导入只生成 candidate，**不自动变成主图**
  · 确认替换只移动 `v3_product.main_image_id` 指针，**不改任何图的 role/sequence**
    —— 轮播、详情、SKU 的角色与顺序因此不可能乱
  · 原图永远保留（status 保持 original），撤销只是把指针指回去
  · 复制路径给的是 Windows 本地绝对路径：外部网页读不到它，那是给人和本地程序用的
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import time
import urllib.request
from typing import Any

from . import store, titles

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MIME_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
            "image/gif": ".gif"}


# ---------------------------------------------------------------- 路径

def _img_dir(pid: int) -> str:
    d = os.path.join(store.IMG_DIR, str(pid))
    os.makedirs(d, exist_ok=True)
    return d


def resolve_local(img: dict) -> tuple[str, bool]:
    """返回 (可用本地路径, 文件是否真的存在)。

    依次尝试：current_local_path → original_local_path → 小猪 img 目录里按 goods_id 找。
    找不到就返回原始记录路径（exists=False），**绝不返回一个虚构的路径**。
    """
    for key in ("current_local_path", "original_local_path"):
        p = (img.get(key) or "").strip()
        if p and os.path.isfile(p):
            return p, True
    # 兜底：小猪 本地鉴图\data\img\<goods_id>\ 下的同名文件
    first = (img.get("original_local_path") or img.get("current_local_path") or "").strip()
    if first:
        name = os.path.basename(first)
        gid = _guess_goods_id(img)
        if gid:
            cand = os.path.join(r"J:\aixitong\小猪\本地鉴图\data\img", gid, name)
            if os.path.isfile(cand):
                return cand, True
        return first, False
    return "", False


def _guess_goods_id(img: dict) -> str:
    p = (img.get("original_local_path") or img.get("current_local_path") or "")
    parts = [x for x in p.replace("/", "\\").split("\\") if x]
    if "img" in parts:
        i = parts.index("img")
        if i + 1 < len(parts):
            return parts[i + 1]
    return ""


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- 按需下载

def download_image(conn: sqlite3.Connection, image_id: int, timeout: int = 25) -> dict:
    """只有 URL 没有本地文件时，按需下载到稳定缓存（幂等：已有文件直接返回）。"""
    r = conn.execute("SELECT * FROM v3_image WHERE image_id=?", (image_id,)).fetchone()
    if not r:
        return {"ok": False, "error": "没有这张图"}
    img = dict(r)
    cur, exists = resolve_local(img)
    if exists:
        return {"ok": True, "local_path": cur, "already": True}
    url = (img.get("original_url") or "").strip()
    if not url.startswith("http"):
        return {"ok": False, "error": "这张图既没有本地文件，也没有可用 URL"}
    # 2026-10-26：落到 <商品ID>\<角色>\ 子目录（主人要求 SKU 图单独一个文件夹）
    d = store.role_dir(img["product_id"], img.get("role"))
    ext = os.path.splitext(url.split("?")[0])[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        ext = ".jpg"
    dst = os.path.join(d, "img%d_%s%s" % (image_id, hashlib.md5(url.encode()).hexdigest()[:8], ext))
    if os.path.isfile(dst):
        conn.execute("UPDATE v3_image SET current_local_path=?, updated_at=? WHERE image_id=?",
                     (dst, store.now(), image_id))
        conn.commit()
        return {"ok": True, "local_path": dst, "already": True}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://item.taobao.com/"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
            data = resp.read()
        if len(data) < 512:
            return {"ok": False, "error": "下载到的文件太小（%d 字节），可能不是图" % len(data)}
        if ctype and not ctype.startswith("image/"):
            return {"ok": False, "error": "返回的不是图片（Content-Type=%s）" % ctype}
        with open(dst, "wb") as f:
            f.write(data)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "下载失败：%s" % e, "retryable": True}
    sha = sha256_file(dst)
    conn.execute("UPDATE v3_image SET current_local_path=?, content_sha256=?, updated_at=?"
                 " WHERE image_id=?", (dst, sha, store.now(), image_id))
    conn.commit()
    return {"ok": True, "local_path": dst, "bytes": len(data), "sha256": sha}


# ---------------------------------------------------------------- 交接视图

def handoff(conn: sqlite3.Connection, pid: int, store_id: str = "default") -> dict:
    """一屏给前端用：原主图 + 当前选用标题 + 路径是否存在。"""
    p = store.get_product(conn, pid)
    if not p:
        return {"ok": False, "error": "没有这个商品"}
    imgs = store.list_images(conn, pid)
    mains = [i for i in imgs if i["role"] == "main"]
    cur = None
    if p.get("main_image_id"):
        cur = next((i for i in imgs if i["image_id"] == p["main_image_id"]), None)
    if cur is None and mains:
        cur = sorted(mains, key=lambda x: (x["sequence"],))[0]
    path, exists = resolve_local(cur) if cur else ("", False)
    t = titles.copy_text(conn, pid, store_id)
    cand = [i for i in imgs if i["status"] == "candidate"]
    return {
        "ok": True,
        "product_id": pid,
        "original_title": p.get("original_title"),
        "selected_title": t.get("copy_text") or "",
        "selected_title_ok": t.get("ok", False),
        "main_image_id": (cur or {}).get("image_id"),
        "main_role": (cur or {}).get("role"),
        "main_sequence": (cur or {}).get("sequence"),
        "main_original_url": (cur or {}).get("original_url"),
        "local_path": path,
        "file_exists": exists,
        "external_image_ready": bool(cur and cur.get("status") in ("approved", "original")
                                     and cur.get("parent_image_id")),
        "candidate_images": [{"image_id": c["image_id"], "status": c["status"],
                              "local_path": resolve_local(c)[0],
                              "parent_image_id": c["parent_image_id"],
                              "note": c.get("note")} for c in cand],
        "all_roles": _role_summary(imgs),
    }


def _role_summary(imgs: list[dict]) -> list[dict]:
    d: dict[str, int] = {}
    for i in imgs:
        d[i["role"]] = d.get(i["role"], 0) + 1
    return [{"role": k, "n": v} for k, v in d.items()]


def list_images(conn: sqlite3.Connection, pid: int) -> list[dict]:
    out = []
    for i in store.list_images(conn, pid):
        p, ok = resolve_local(i)
        i["file_exists"] = ok
        i["resolved_path"] = p
        out.append(i)
    return out


# ---------------------------------------------------------------- 导入 / 确认 / 撤销

def import_new(conn: sqlite3.Connection, pid: int, src_path: str,
               note: str = "", role: str = "main",
               actor: str = "image", keep_status: bool = False) -> dict:
    """导入外部处理好的新图 → 只生成 candidate，不动主图。

    keep_status=True：**不改写 v3_product.status**（第三步精筛的
    passed/blocked/pending/review 是上游判定，修图不该覆盖它）。
    默认 False 保持旧行为，调用方要显式要求 —— 阶段 A 审计查出的口径打架②。
    """
    p = store.get_product(conn, pid)
    if not p:
        return {"ok": False, "error": "没有这个商品"}
    if not os.path.isfile(src_path):
        return {"ok": False, "error": "文件不存在：%s" % src_path}
    size = os.path.getsize(src_path)
    if size < 512:
        return {"ok": False, "error": "文件太小（%d 字节），不像图" % size}
    ext = os.path.splitext(src_path)[1].lower()
    IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
    VID_EXT = (".mp4", ".mov", ".m4v", ".webm")
    is_video = False
    if ext in IMG_EXT:
        pass
    elif ext in VID_EXT:
        # ★ 2026-10-25 第四批：视频也能走这条导入通道。
        #   视频**不做图片解码/缩略**，只查文件头（防半截文件 / 改了扩展名的非视频）。
        is_video = True
        if size < 1024:
            return {"ok": False, "error": "视频文件太小（%d 字节），不像完整视频" % size}
        try:
            with open(src_path, "rb") as fh:
                head = fh.read(16)
        except OSError as ex:
            return {"ok": False, "error": "读不了这个文件：%s" % ex}
        ok_head = (head[4:8] == b"ftyp") or head[:4] == b"\x1a\x45\xdf\xa3"
        if not ok_head:
            return {"ok": False, "error": "文件头不是 MP4/MOV/WebM（可能改过扩展名或没下载完）"}
    else:
        return {"ok": False, "error": "不支持的格式：%s（图片 jpg/png/webp，视频 mp4/mov/m4v/webm）" % ext}
    sha = sha256_file(src_path)
    dup = conn.execute("SELECT image_id FROM v3_image WHERE product_id=? AND content_sha256=?",
                       (pid, sha)).fetchone()
    if dup:
        return {"ok": False, "error": "这张图和已有的 image_id=%d 完全一样（sha256 相同）"
                                      % dup["image_id"], "duplicate_of": dup["image_id"]}
    d = _img_dir(pid)
    dst = os.path.join(d, "new_%s_%s%s" % (time.strftime("%Y%m%d%H%M%S"),
                                           sha[:8], ext))
    shutil.copy2(src_path, dst)

    imgs = store.list_images(conn, pid)
    # parent（"这一版是从哪一版派生的"）：main 用第一张主图，其它用途用**同用途**的第一张。
    # 原来的代码对任何 role 都挂到主图上，视频/SKU 图的 parent 就是错的。
    same_role = sorted([i for i in imgs if i["role"] == role],
                       key=lambda x: (x["sequence"] or 0, x["image_id"]))
    if role == "main":
        mains = same_role
        base = mains[0] if mains else None
    else:
        base = same_role[0] if same_role else None
    # 找一个不冲突的序号（不改任何已有图的序号）
    seq = max([i["sequence"] for i in imgs if i["role"] == role] or [0]) + 1
    cur = conn.execute(
        "INSERT INTO v3_image(product_id, slot_key, role, sequence, sku_id,"
        " original_url, original_local_path, current_local_path, current_remote_url,"
        " content_sha256, parent_image_id, status, note, created_at, updated_at)"
        " VALUES(?,?,?,?,NULL,'',?,?,'',?,?,'candidate',?,?,?)",
        (pid, store.slot_key(role, seq), role, seq, dst, dst, sha,
         (base or {}).get("image_id"), note or "外部处理后导入", store.now(), store.now()))
    iid = int(cur.lastrowid)
    if not keep_status:
        store.set_product_status(conn, pid, "image_handoff")
    store.audit(conn, "image.import", "v3_image", iid, None,
                {"pid": pid, "path": dst, "sha256": sha, "role": role,
                 "parent": (base or {}).get("image_id")}, actor=actor)
    conn.commit()
    return {"ok": True, "image_id": iid, "local_path": dst, "sha256": sha,
            "role": role, "sequence": seq, "status": "candidate",
            "parent_image_id": (base or {}).get("image_id"),
            "is_video": is_video,
            "note": "只生成候选；点「确认这一版」才会成为当前版本"}


def approve(conn: sqlite3.Connection, pid: int, image_id: int,
            actor: str = "image") -> dict:
    """确认替换：只移动主图指针，角色与序号一律不动。"""
    p = store.get_product(conn, pid)
    if not p:
        return {"ok": False, "error": "没有这个商品"}
    r = conn.execute("SELECT * FROM v3_image WHERE image_id=? AND product_id=?",
                     (image_id, pid)).fetchone()
    if not r:
        return {"ok": False, "error": "这张图不属于这个商品"}
    img = dict(r)
    if img["status"] not in ("candidate", "approved"):
        return {"ok": False, "error": "只有候选图才能确认替换（当前 %s）" % img["status"]}
    before = p.get("main_image_id")
    conn.execute("UPDATE v3_image SET status='approved', updated_at=? WHERE image_id=?",
                 (store.now(), image_id))
    conn.execute("UPDATE v3_product SET main_image_id=?, updated_at=? WHERE product_id=?",
                 (image_id, store.now(), pid))
    store.audit(conn, "image.approve", "v3_image", image_id,
                {"main_image_id": before}, {"main_image_id": image_id}, actor=actor)
    conn.commit()
    return {"ok": True, "main_image_id": image_id,
            "previous_main_image_id": before,
            "note": "原图已保留（status 未变），可随时撤销"}


def revert(conn: sqlite3.Connection, pid: int, actor: str = "image") -> dict:
    """撤销：主图指针回到最初那台 original 主图。"""
    p = store.get_product(conn, pid)
    if not p:
        return {"ok": False, "error": "没有这个商品"}
    imgs = store.list_images(conn, pid)
    orig = sorted([i for i in imgs if i["role"] == "main" and i["status"] == "original"],
                  key=lambda x: x["sequence"])
    if not orig:
        return {"ok": False, "error": "找不到原始主图（可能没导入过 main 角色图）"}
    before = p.get("main_image_id")
    conn.execute("UPDATE v3_image SET status='candidate' WHERE product_id=? AND status='approved'",
                 (pid,))
    conn.execute("UPDATE v3_product SET main_image_id=?, updated_at=? WHERE product_id=?",
                 (orig[0]["image_id"], store.now(), pid))
    store.audit(conn, "image.revert", "v3_product", pid,
                {"main_image_id": before}, {"main_image_id": orig[0]["image_id"]}, actor=actor)
    conn.commit()
    return {"ok": True, "main_image_id": orig[0]["image_id"], "previous": before,
            "local_path": resolve_local(orig[0])[0]}


def reject_candidate(conn: sqlite3.Connection, pid: int, image_id: int,
                     actor: str = "image") -> dict:
    conn.execute("UPDATE v3_image SET status='rejected', updated_at=?"
                 " WHERE image_id=? AND product_id=?", (store.now(), image_id, pid))
    conn.commit()
    return {"ok": True, "image_id": image_id, "status": "rejected"}


def ensure_main_pointer(conn: sqlite3.Connection, pid: int) -> int | None:
    """导入后没设 main_image_id 的，补成第一张 main。"""
    p = store.get_product(conn, pid)
    if not p or p.get("main_image_id"):
        return p.get("main_image_id") if p else None
    imgs = store.list_images(conn, pid)
    mains = sorted([i for i in imgs if i["role"] == "main"], key=lambda x: x["sequence"])
    if mains:
        store.set_current_image(conn, pid, mains[0]["image_id"])
        conn.commit()
        return mains[0]["image_id"]
    return None


# ---------------------------------------------------------------- 非主图用途的版本确认

def _pick_target_row(conn: sqlite3.Connection, pid: int, cand: dict) -> dict | None:
    """给一个候选行，找出"它要替换掉的那一行"（该用途的当前版本）。"""
    parent = cand.get("parent_image_id")
    if parent:
        r = conn.execute("SELECT * FROM v3_image WHERE image_id=? AND product_id=?",
                         (int(parent), pid)).fetchone()
        if r:
            return dict(r)
    r = conn.execute(
        "SELECT * FROM v3_image WHERE product_id=? AND role=? AND image_id<>?"
        " AND status IN ('original','replaced')"
        " ORDER BY CASE status WHEN 'replaced' THEN 0 ELSE 1 END, sequence, image_id"
        " LIMIT 1", (pid, cand.get("role"), int(cand["image_id"]))).fetchone()
    return dict(r) if r else None


def approve_media(conn: sqlite3.Connection, pid: int, image_id: int,
                  actor: str = "media") -> dict:
    """把候选版本确认为该用途的**当前版本**（图片、视频通用；主图请走 approve）。

    ★ 2026-10-25 第四批。为什么不新增一行当"当前版本"：
      导出是按 role 取行的，多一行就等于多出一张图/一段视频（还会把旧的也带上）。
      所以这里沿用 lab_confirm 的做法 —— **把新文件的路径写进原图那一行的
      current_local_path**，同时清空 current_remote_url（新文件还没有线上地址 ⇒ 待上传）。
      原图的两个原始字段（original_url / original_local_path）**一个字节不动**：
      追溯查得到最初那张/那段是什么，退回也随时能回去。

    要求：候选的本地文件必须真实存在，否则拒绝确认（不许把不存在的东西当成品）。
    """
    p = store.get_product(conn, pid)
    if not p:
        return {"ok": False, "error": "没有这个商品"}
    cand = conn.execute("SELECT * FROM v3_image WHERE image_id=? AND product_id=?",
                        (int(image_id), pid)).fetchone()
    if not cand:
        return {"ok": False, "error": "这张/这段不属于这个商品"}
    cand = dict(cand)
    if cand["status"] not in ("candidate", "approved"):
        return {"ok": False, "error": "只有候选才能确认（当前 status=%s）" % cand["status"]}
    newp = (cand.get("current_local_path") or cand.get("original_local_path") or "").strip()
    if not newp or not os.path.isfile(newp):
        return {"ok": False, "error": "候选文件不存在，拒绝确认：%s" % (newp or "（空）")}
    tgt = _pick_target_row(conn, pid, cand)
    if not tgt:
        return {"ok": False, "error": "找不到要替换的原行（该用途没有任何已有版本）"}
    if os.path.normcase((tgt.get("current_local_path") or "")) == os.path.normcase(newp):
        return {"ok": False, "error": "候选与当前版本是同一个文件，无需替换"}
    old = {"current_local_path": tgt.get("current_local_path"),
           "current_remote_url": tgt.get("current_remote_url"),
           "status": tgt.get("status"),
           "original_local_path": tgt.get("original_local_path")}
    # ★ 关键一步：视频行（以及部分老图）的 original_local_path 是 NULL —— 只有 current_local_path。
    #   如果直接换指针，原始本地文件就再也找不回来了。所以这里**先把当时的 current_local_path
    #   落成 original_local_path 基准**，再改 current_local_path。
    #   SQLite 的 UPDATE 里 RHS 取的是旧值，所以一条语句就能做到"先记旧、再换新"。
    conn.execute(
        "UPDATE v3_image SET"
        " original_local_path=CASE WHEN original_local_path IS NULL"
        "   OR original_local_path='' THEN current_local_path ELSE original_local_path END,"
        " current_local_path=?, current_remote_url=NULL,"
        " status='replaced', updated_at=? WHERE image_id=?",
        (newp, store.now(), int(tgt["image_id"])))
    conn.execute("UPDATE v3_image SET status='approved', updated_at=? WHERE image_id=?",
                 (store.now(), int(cand["image_id"])))
    conn.commit()
    store.audit(conn, "media.approve", "v3_image", int(tgt["image_id"]), old,
                {"current_local_path": newp, "current_remote_url": None,
                 "from_candidate": int(cand["image_id"]),
                 "original_url_kept": tgt.get("original_url")}, actor=actor)
    conn.commit()
    return {"ok": True, "role": tgt.get("role"), "target_image_id": int(tgt["image_id"]),
            "from_candidate": int(cand["image_id"]),
            "old": old,
            "new": {"current_local_path": newp, "current_remote_url": None},
            "original_kept": {"original_url": tgt.get("original_url"),
                              "original_local_path": tgt.get("original_local_path")},
            "need_upload": True,
            "note": "当前版本已换成新文件；线上地址已清空，需上传图床后才会有 URL"}


def revert_media(conn: sqlite3.Connection, pid: int, image_id: int,
                 actor: str = "media") -> dict:
    """把某个用途的当前版本退回原始版本（图片/视频通用）。

    只动 current_* 三个字段，**文件一个都不删**（新文件仍在盘上，随时可再确认）。
    """
    p = store.get_product(conn, pid)
    if not p:
        return {"ok": False, "error": "没有这个商品"}
    r = conn.execute("SELECT * FROM v3_image WHERE image_id=? AND product_id=?",
                     (int(image_id), pid)).fetchone()
    if not r:
        return {"ok": False, "error": "这张/这段不属于这个商品"}
    r = dict(r)
    if r["status"] != "replaced":
        return {"ok": False, "error": "只有已确认替换的版本才能退回（当前 status=%s）" % r["status"]}
    orig = (r.get("original_local_path") or "").strip()
    if not orig:
        return {"ok": False, "error": "这一行没有原始本地路径，无法退回"}
    before = {"current_local_path": r.get("current_local_path"),
              "current_remote_url": r.get("current_remote_url")}
    conn.execute(
        "UPDATE v3_image SET current_local_path=?, current_remote_url=NULL,"
        " status='original', updated_at=? WHERE image_id=?",
        (orig, store.now(), int(r["image_id"])))
    # 该行派生的候选回到 candidate，方便再次确认（approved 的行不删）
    conn.execute("UPDATE v3_image SET status='candidate', updated_at=?"
                 " WHERE product_id=? AND parent_image_id=? AND status='approved'",
                 (store.now(), pid, int(r["image_id"])))
    conn.commit()
    store.audit(conn, "media.revert", "v3_image", int(r["image_id"]), before,
                {"current_local_path": orig, "current_remote_url": None,
                 "status": "original"}, actor=actor)
    conn.commit()
    return {"ok": True, "role": r.get("role"), "image_id": int(r["image_id"]),
            "restored": orig, "before": before,
            "original_url": r.get("original_url"),
            "note": "已退回原始版本；替换过的文件仍保留在磁盘上"} 


def list_media_versions(conn: sqlite3.Connection, pid: int, role: str = "") -> dict:
    """列出某个商品各用途的「当前版本 / 原始版本 / 候选」，给界面做版本管理。"""
    sql = "SELECT * FROM v3_image WHERE product_id=?"
    args: list = [pid]
    if role:
        sql += " AND role=?"
        args.append(role)
    sql += " ORDER BY role, sequence, image_id"
    rows = [dict(r) for r in conn.execute(sql, args)]
    out: dict[str, dict] = {}
    for r in rows:
        d = out.setdefault(r["role"] or "?", {"role": r["role"], "current": [],
                                              "original": [], "candidates": []})
        st = r.get("status")
        if st == "replaced":
            d["current"].append({"image_id": r["image_id"], "sequence": r["sequence"],
                                 "sku_id": r["sku_id"],
                                 "path": r.get("current_local_path"),
                                 "remote_url": r.get("current_remote_url")})
        elif st in ("original", "approved") and not d["current"]:
            d["original"].append({"image_id": r["image_id"], "sequence": r["sequence"],
                                  "path": r.get("current_local_path") or r.get("original_local_path"),
                                  "original_url": r.get("original_url")})
        elif st == "candidate":
            d["candidates"].append({"image_id": r["image_id"], "sequence": r["sequence"],
                                    "path": r.get("current_local_path"),
                                    "parent_image_id": r.get("parent_image_id")})
    return {"ok": True, "product_id": pid, "roles": list(out.values())}


# ---------------------------------------------------------------- Provider 接口（本版不实现）

class ImageProviderAdapter:
    """未来接第三方出图 API 的唯一入口。**本版不实现任何调用**，只定契约。

    配置凭据只从 config/v3_ai.json 的 roles.image 读，且**不得写进日志或源码**。
    """

    def submit(self, input_image: str, title: str, prompt: str, config: dict) -> str:
        raise NotImplementedError("本版不接第三方出图 API（任务书 §5）")

    def poll(self, job_id: str) -> dict:
        raise NotImplementedError

    def fetch(self, job_id: str) -> str:
        raise NotImplementedError


# ================================================================
# 图片层（精筛用）：下载 → 本机 PP-OCR 读字 → 抠出命中词
# ================================================================
# 分工写清楚，别把判断塞进来：
#   · 这一层只做机械活 —— 图在哪、上面写了什么字、哪些词命中了；
#   · 命中该 block 还是 warn（通用词/短词/豁免）由 flow.fine_screen 判，
#     因为那套规则（类目核心词、词库噪声）在 flow 里已经有一份，不能有两份。
#
# 引擎不新造：直接复用第 4 步「检测打码」用的那台 —— J:\aixitong\小猪\本地鉴图\鉴图.py
# 的 PPOCR 类（PP-OCRv6 det+rec，onnxruntime CPU）。本机跑，不联网。

JIAN_TU = r"J:\aixitong\小猪\本地鉴图\鉴图.py"
PIG_DB = r"J:\aixitong\小猪\本地鉴图\data\商品.db"
PIG_IMG_ROOT = r"J:\aixitong\小猪\本地鉴图\data\img"
MASK_WORDS_FILE = r"J:\aixitong\小猪\词库\打码词.txt"
MODEL_DIR = r"J:\aixitong\小猪\xiaozhu4\models\v6"
OCR_ENGINE_NAME = "PP-OCRv6-small / onnxruntime-CPU"
SNIPPET_MAX = 60
MAX_FULL_TEXT = 4000

_OCR = None
_OCR_ERR = ""
_PIG_CON = None


def ocr_available() -> dict:
    """静态自检：引擎脚本、模型、依赖在不在。**不加载模型**，秒回。

    与「跑一次试试」的区别：这里只回答「缺什么」，跑得成跑不成由 ocr_image 的真实结果说话。
    """
    import importlib.util as _ilu
    miss: list[str] = []
    if not os.path.isfile(JIAN_TU):
        miss.append("引擎脚本 %s" % JIAN_TU)
    for m in ("numpy", "cv2", "onnxruntime", "pyclipper"):
        try:
            if _ilu.find_spec(m) is None:
                miss.append("python 包 %s" % m)
        except Exception:  # noqa: BLE001
            miss.append("python 包 %s" % m)
    for f in ("PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx", "ppocrv6_dict.txt"):
        if not os.path.isfile(os.path.join(MODEL_DIR, f)):
            miss.append("模型文件 %s" % f)
    return {"ok": not miss, "missing": miss, "engine": OCR_ENGINE_NAME,
            "script": JIAN_TU, "models": MODEL_DIR,
            "note": "" if not miss else "缺这些就跑不了图片层：%s" % "、".join(miss)}


def _load_ocr():
    """懒加载 OCR 实例（进程内单例）。失败一次就记住原因，不反复重试。"""
    global _OCR, _OCR_ERR
    if _OCR is not None:
        return _OCR
    if _OCR_ERR:
        return None
    try:
        import importlib.util  # noqa: PLC0415
        spec = importlib.util.spec_from_file_location("xz_jiantu", JIAN_TU)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)          # 自家本机脚本，非外部输入
        _OCR = mod.PPOCR()
    except Exception as e:  # noqa: BLE001
        _OCR_ERR = "%s: %s" % (type(e).__name__, e)
        _OCR = None
    return _OCR


def ocr_engine_error() -> str:
    return _OCR_ERR


# ---------------------------------------------------------------- 词表匹配

# 词表已抽到 server\v3\words.py（唯一来源，第 3 步 / 2.5 / 第 4 步 / 打码 / 定位共用）。
# 这里保留 WordSet / build_words 两个名字，是为了不动 flow.py、mask.py、positioning.py 的调用点。
from . import words as words_src  # noqa: E402
from .words import WordSet, build, version as words_version, write_file as words_write_file  # noqa: E402


def _read_words_file(path: str) -> list[str]:
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            with open(path, "r", encoding=enc) as f:
                return [ln.strip() for ln in f
                        if ln.strip() and not ln.strip().startswith("#")]
        except UnicodeDecodeError:
            continue
        except OSError:
            return []
    return []


_WV_CACHE: tuple = (None, None)


def words_meta(conn: sqlite3.Connection) -> dict:
    """当前词表指纹（带缓存）。

    缓存键 = 词库行数 + 豁免行数 + 三个词表文件的 (mtime, size)。
    任一变了就重算 —— 既不用每张图都算一遍 sha256（6178 张会白烧几十分钟），
    也不会拿着旧指纹给新结果记账。
    """
    global _WV_CACHE
    key_parts = []
    try:
        key_parts.append(conn.execute("SELECT COUNT(*) FROM v3_keyword").fetchone()[0])
        key_parts.append(conn.execute("SELECT COUNT(*) FROM v3_keyword_waiver").fetchone()[0])
    except Exception:  # noqa: BLE001
        key_parts.append(-1)
    for f in (words_src.XIAOZHU_WORDS, words_src.MASK_WORDS_FILE, words_src.WORD_MASTER):
        try:
            st = os.stat(f)
            key_parts.append((int(st.st_mtime), st.st_size))
        except OSError:
            key_parts.append(None)
    key = tuple(key_parts)
    if _WV_CACHE[0] == key and _WV_CACHE[1]:
        return _WV_CACHE[1]
    meta = words_version(conn)
    _WV_CACHE = (key, meta)
    return meta


def build_words(conn: sqlite3.Connection) -> WordSet:
    """图片层词表 = words.build()（唯一来源）。保留函数名以兼容旧调用点。"""
    return build(conn)


# ---------------------------------------------------------------- 落地表

_OCR_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS v3_image_ocr (
    image_id    INTEGER PRIMARY KEY,
    product_id  INTEGER NOT NULL,
    role        TEXT,
    seq         INTEGER,
    state       TEXT NOT NULL,          -- ok 读出来了 | unreadable 图坏了 | fail 下载/引擎失败
    n_seg       INTEGER DEFAULT 0,
    hit_words   TEXT,                   -- 逗号分隔
    snippets    TEXT,                   -- JSON: [{w:词,k:类别,s:片段,r:角色,n:序号}]
    full_text   TEXT,                   -- 全文（截断 4000 字），详情层判图文一致要用
    engine      TEXT,
    seconds     REAL,
    created_at  TEXT,
    img_w       INTEGER,                -- 坐标的参照系：原图像素宽（缩放复用必须靠它）
    img_h       INTEGER,
    boxes_json  TEXT,                   -- JSON: [{text,box:[x1,y1,x2,y2],word,kind,score,poly?}]
                                        --   空/NULL = **无坐标**（第 3 步之前跑的图都是这种）
    boxes_at    TEXT,                   -- 坐标写入时刻（det 跑的时间，≠ created_at）
    words_version TEXT,                 -- 算这次命中用的词表指纹（words.version()）
    words_count INTEGER                 -- 该版词表条数
);
CREATE INDEX IF NOT EXISTS ix_v3_image_ocr_pid ON v3_image_ocr(product_id);
"""

# 老库补列：{列名: 类型}。SQLite 没有 ADD COLUMN IF NOT EXISTS，所以先 PRAGMA 查再改。
_OCR_BOX_COLS = [("img_w", "INTEGER"), ("img_h", "INTEGER"),
                 ("boxes_json", "TEXT"), ("boxes_at", "TEXT"),
                 ("words_version", "TEXT"), ("words_count", "INTEGER")]


def _ensure_table(conn: sqlite3.Connection) -> None:
    for stmt in _OCR_TABLE_SQL.split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    # 坐标列：老库（含已跑过的 835 条记录）在这里自动升级，历史行的 boxes_json 保持 NULL
    have = {r[1] for r in conn.execute('PRAGMA table_info("v3_image_ocr")')}
    for name, decl in _OCR_BOX_COLS:
        if name not in have:
            conn.execute('ALTER TABLE v3_image_ocr ADD COLUMN %s %s' % (name, decl))
    conn.commit()


# ---------------------------------------------------------------- 文字框坐标
# 为什么要存坐标：第 4 步打码只需要「涂哪一块」，不需要重新跑 det。第 3 步的 det 已经
# 把框算出来了（PPOCR.run 返回的 box 就是原始图像素坐标），以前在入库时整个丢掉，
# 导致第 4 步只能整张重跑一遍 det+rec —— 慢，且同一张图两次 det 未必给出同一组框。

def image_size(path: str) -> tuple[int, int]:
    """原图像素尺寸 (w, h)。解不出来返回 (0, 0) —— 0 表示「不知道」，调用方不许当成有效。"""
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    im = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if im is None:
        return (0, 0)
    return (int(im.shape[1]), int(im.shape[0]))


def poly_to_rect(poly, w: int = 0, h: int = 0) -> list[int]:
    """4+ 点多边形 → 轴对齐 `[x1,y1,x2,y2]`。

    **取框口径与 鉴图.py 的 mask_regions 完全相同**（x/y 各取 min/max）—— 所以按这里存的
    矩形涂白，产出的图与「引擎自己重跑后涂」应当逐字节一致。
    引擎的框可能超出图像边界（_boxes 只裁了下界），这里按图尺寸夹住。
    """
    try:
        xs = [int(round(float(p[0]))) for p in (poly or [])]
        ys = [int(round(float(p[1]))) for p in (poly or [])]
    except (TypeError, ValueError, IndexError):
        return []
    if not xs or not ys:
        return []
    x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
    if w > 0:
        x1, x2 = max(0, min(x1, w - 1)), max(0, min(x2, w - 1))
    if h > 0:
        y1, y2 = max(0, min(y1, h - 1)), max(0, min(y2, h - 1))
    if x2 <= x1 or y2 <= y1:
        return []
    return [x1, y1, x2, y2]


def compact_poly(poly) -> list[list[int]]:
    """多边形压成整数点列（只给命中段存，非命中段只留矩形，别把库撑大）。"""
    try:
        return [[int(round(float(p[0]))), int(round(float(p[1])))] for p in (poly or [])]
    except (TypeError, ValueError, IndexError):
        return []


def boxes_from_ocr(texts, words, img_w: int = 0, img_h: int = 0) -> list[dict]:
    """引擎输出 + 词表 → 入库用的规范坐标数组（ocr_image 与 mask 回填共用这一份）。

    每段一条：``{"text","box":[x1,y1,x2,y2],"box_loose":[...],"word","kind","score"}``

      · ``box``       = **真实文字框**（引擎 unclip 之前那个多边形），按位置精涂用这个；
      · ``box_loose`` = unclip 放大后的框，即 鉴图.py 的 mask_regions 现在涂的范围
                        （实测面积是 box 的 8~12 倍），留着是为了能逐字节复现旧口径；
      · 命中的段额外带 ``poly``（core 四点多边形，供按形状涂）与 ``words``（全部命中项及位置）。

    没命中的段也**照样存**：以后词库加词时，不用重跑 det 就能定位到它。
    """
    out: list[dict] = []
    matcher = words if (words is not None and hasattr(words, "match")) else None
    for t in (texts or []):
        seg = (t.get("text") or "").strip()
        if not seg:
            continue
        tight_poly = t.get("core") or t.get("box") or []
        tight = poly_to_rect(tight_poly, img_w, img_h)
        loose = poly_to_rect(t.get("box") or [], img_w, img_h)
        rect = tight or loose
        if len(rect) != 4:
            continue
        ms = matcher.match(seg) if matcher else []
        rec = {"text": seg, "box": rect,
               "box_loose": (loose or rect),
               "word": (ms[0][0] if ms else ""),
               "kind": (ms[0][1] if ms else ""),
               "score": t.get("score")}
        if ms:
            rec["poly"] = compact_poly(tight_poly)
            rec["words"] = [{"w": w, "k": k, "p": pos} for w, k, pos in ms]
        out.append(rec)
    return out


def boxes_of(conn: sqlite3.Connection, image_id: int) -> dict | None:
    """读一张图的已存坐标。

    **返回 None 就是「无坐标」**（第 3 步加坐标之前跑的 835 条历史记录全是这种），
    绝不把「空列表」当成「有坐标但没字」—— 那会让第 4 步跳过本该涂的图。
    """
    _ensure_table(conn)
    r = conn.execute(
        "SELECT img_w, img_h, boxes_json, boxes_at FROM v3_image_ocr WHERE image_id=?",
        (int(image_id),)).fetchone()
    if not r:
        return None
    raw = store.unj(r["boxes_json"], None)
    if not raw:
        return None
    return {"img_w": r["img_w"], "img_h": r["img_h"], "boxes": raw,
            "boxes_at": r["boxes_at"]}


def has_boxes(conn: sqlite3.Connection, image_id: int) -> bool:
    return boxes_of(conn, image_id) is not None


def save_boxes(conn: sqlite3.Connection, image_id: int, boxes: list[dict],
               img_w: int = 0, img_h: int = 0) -> dict:
    """把坐标写回 v3_image_ocr（回填用；下次就不用再跑 det）。空列表 = 清成无坐标。"""
    _ensure_table(conn)
    bj = store.j(boxes) if boxes else None
    conn.execute(
        "UPDATE v3_image_ocr SET boxes_json=?, img_w=?, img_h=?, boxes_at=?"
        " WHERE image_id=?",
        (bj, int(img_w) or None, int(img_h) or None,
         store.now() if bj else None, int(image_id)))
    conn.commit()
    return {"image_id": int(image_id), "n_boxes": len(boxes or []),
            "img_w": img_w, "img_h": img_h}


def rematch(conn: sqlite3.Connection, image_ids, words: "WordSet | None" = None,
            write: bool = True) -> dict:
    """★ 2026-10-26 新增：用**当前词表**在「已存 OCR 文本」上重新匹配。

    这是第 4 步「检测」的正确做法 —— 纯文本匹配，**不重跑 det、不下载、不碰小猪库**。

    背景（真库实测）：`v3_image_ocr.boxes_json` 里每段都带 `text`，但 `word`/`kind`
    是空的（那批 OCR 跑的时候没匹配词表）。所以词表更新后，**236 张有坐标的图
    可以零成本重算出命中**，命中数从 26 涨到真实值。

    返回 {ok, images, scanned, with_boxes, no_boxes, hit_images, hit_boxes, words, changed}
    """
    ids = [int(x) for x in (image_ids or [])]
    ws = words if (words is not None and hasattr(words, "match")) else build(conn)
    vm = {}
    try:
        vm = version(conn) or {}
    except Exception:                                    # noqa: BLE001
        vm = {}
    scanned = with_boxes = no_boxes = hit_images = hit_boxes = changed = 0
    word_set: set[str] = set()
    details: list[dict] = []
    for iid in ids:
        scanned += 1
        bo = boxes_of(conn, iid)
        if bo is None:
            no_boxes += 1
            continue
        with_boxes += 1
        boxes = bo.get("boxes") or []
        my_hits: list[str] = []
        dirty = False
        for b in boxes:
            seg = (b.get("text") or "").strip()
            ms = ws.match(seg) if seg else []
            new_w = ms[0][0] if ms else ""
            new_k = ms[0][1] if ms else ""
            if (b.get("word") or "") != new_w or (b.get("kind") or "") != new_k:
                b["word"] = new_w
                b["kind"] = new_k
                dirty = True
                if new_w:
                    changed += 1
            if ms:
                my_hits.append(new_w)
                word_set.add(new_w)
                hit_boxes += 1
        if my_hits:
            hit_images += 1
        if write and (dirty or my_hits):
            save_boxes(conn, iid, boxes, bo.get("img_w") or 0, bo.get("img_h") or 0)
            conn.execute(
                "UPDATE v3_image_ocr SET hit_words=?, words_version=?, words_count=?"
                " WHERE image_id=?",
                (",".join(sorted(set(my_hits))), vm.get("label"), vm.get("count"), iid))
        if my_hits:
            details.append({"image_id": iid, "hit_words": sorted(set(my_hits))})
    if write:
        conn.commit()
    return {"ok": True, "images": len(ids), "scanned": scanned,
            "with_boxes": with_boxes, "no_boxes": no_boxes,
            "hit_images": hit_images, "hit_boxes": hit_boxes,
            "words": sorted(word_set), "changed": changed,
            "words_version": vm.get("label"), "details": details[:300]}


def _pig_local_by_url(url: str) -> str:
    """小猪库里同一张图已经下好过，就别再走网络（省流量、也躲开防盗链）。"""
    global _PIG_CON
    if not url or not os.path.isfile(PIG_DB):
        return ""
    try:
        if _PIG_CON is None:
            _PIG_CON = sqlite3.connect("file:%s?mode=ro" % PIG_DB.replace("\\", "/"),
                                       uri=True, timeout=5)
        r = _PIG_CON.execute("SELECT 本地路径 FROM 图片 WHERE url=?", (url,)).fetchone()
    except Exception:  # noqa: BLE001
        return ""
    p = (r[0] or "").strip() if r else ""
    return p if p and os.path.isfile(p) else ""


def _fetch_direct(url: str, dst: str, timeout: int = 25) -> int:
    """不要 Referer 的直连下载（wxapp 图对 Referer 敏感，带上反而被拒）。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
        data = resp.read()
    if len(data) < 512:
        raise RuntimeError("下载到的文件太小（%d 字节）" % len(data))
    if ctype and not ctype.startswith("image/"):
        raise RuntimeError("返回的不是图片（Content-Type=%s）" % ctype)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as f:
        f.write(data)
    return len(data)


def ensure_local(conn: sqlite3.Connection, img: dict, timeout: int = 25) -> dict:
    """保证这张图在本地有文件：已有 → 小猪下过的 → 下载。三步都不成才算失败。

    「复用第 4 步的下载能力」= 第二步走小猪库、第三步调本文件已有的 download_image，
    不另写一套下载器；只有它俩都被防盗链挡住时，才用不带 Referer 的直连兜底。
    """
    path, ok = resolve_local(img)
    if ok:
        return {"ok": True, "local_path": path, "via": "existing"}
    url = (img.get("original_url") or "").strip()
    if not url.startswith("http"):
        return {"ok": False, "error": "这张图既没有本地文件，也没有可用 URL"}
    pig = _pig_local_by_url(url)
    if pig:
        conn.execute("UPDATE v3_image SET current_local_path=?, updated_at=?"
                     " WHERE image_id=?", (pig, store.now(), img["image_id"]))
        conn.commit()
        return {"ok": True, "local_path": pig, "via": "pig"}
    r = download_image(conn, int(img["image_id"]), timeout=timeout)
    if r.get("ok"):
        r["via"] = "download"
        return r
    d = _img_dir(int(img["product_id"]))
    ext = os.path.splitext(url.split("?")[0])[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        ext = ".jpg"
    dst = os.path.join(d, "img%d_%s%s" % (img["image_id"],
                                          hashlib.md5(url.encode()).hexdigest()[:8], ext))
    try:
        n = _fetch_direct(url, dst, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "下载失败：%s（download_image 报的是：%s）"
                                     % (e, r.get("error"))}
    sha = sha256_file(dst)
    conn.execute("UPDATE v3_image SET current_local_path=?, content_sha256=?, updated_at=?"
                 " WHERE image_id=?", (dst, sha, store.now(), img["image_id"]))
    conn.commit()
    return {"ok": True, "local_path": dst, "via": "download-direct", "bytes": n}


# ---------------------------------------------------------------- OCR 单张

def _snippet(seg: str, pos: int, wl: int = 0) -> str:
    """命中处前后各取一点，总长 ≤60 字（带省略号时 ≤60）。"""
    width = SNIPPET_MAX - 2
    if len(seg) <= width:
        return seg
    half = max(0, (width - (wl or 0)) // 2)
    a = max(0, pos - half)
    b = min(len(seg), a + width)
    a = max(0, b - width)
    s = seg[a:b]
    return ("…" if a > 0 else "") + s + ("…" if b < len(seg) else "")


def _match_conf(seg: str, word: str) -> float:
    """**匹配置信度**（不是识别置信度 —— 见下），给人工判断用的强弱信号。

    三档：1.0 = 这一段字就是那个词（整段命中，最硬）；0.8 = 词完整出现在段内；
    0.6 = 词很短（<=2 字）或 OCR 段里只是片段，最容易被读串，故最低。
    ⚠ 引擎（鉴图.py 的 PPOCR.run）只回 text/box，识别分数在内部就丢掉了，
      所以这里**不能**报「识别置信度」—— 报一个假数字比不报更坏。
    """
    w = (word or "").strip()
    if not w:
        return 0.0
    if seg.strip() == w:
        return 1.0
    if w in seg:
        return 0.6 if len(w) <= 2 else 0.8
    return 0.6


def _same_day(ts: str) -> bool:
    return (ts or "")[:10] == store.now()[:10]


def _row_result(row: sqlite3.Row) -> dict:
    """缓存行 → 和刚跑完一样形状的结果（详情层重跑时靠它，不然第二次就没数据了）。"""
    hits = []
    for d in (store.unj(row["snippets"], []) or []):
        try:
            hits.append({"word": d.get("w") or "", "kind": d.get("k") or "",
                         "snippet": d.get("s") or "", "image_id": row["image_id"],
                         "role": d.get("r") or row["role"], "sequence": d.get("n"),
                         "conf": d.get("c")})
        except Exception:  # noqa: BLE001
            continue
    # 缓存行也要说明「有没有坐标」：第 3 步之前跑的记录 boxes_json 为 NULL，
    # 第 4 步看到 has_boxes=False 就该走「补跑一次 det」的分支，而不是当成没有命中。
    has_bx = False
    iw = ih = None
    try:
        has_bx = bool(store.unj(row["boxes_json"], None))
        iw, ih = row["img_w"], row["img_h"]
    except (IndexError, KeyError):
        pass
    return {"ok": row["state"] == "ok", "state": row["state"], "n_seg": row["n_seg"],
            "hits": hits, "text": row["full_text"] or "", "cached": True,
            "via": "", "secs": 0.0, "image_id": row["image_id"],
            "has_boxes": has_bx, "img_w": iw, "img_h": ih}


def _save_ocr(conn: sqlite3.Connection, img: dict, state: str, n_seg: int,
              hits: list[dict], full_text: str, secs: float, error: str = "",
              boxes: list[dict] | None = None,
              img_size: tuple[int, int] | None = None,
              words_meta: dict | None = None) -> None:
    """两处记账：① v3_image_ocr 表（全量，详情层与断点续跑要用）② v3_image.note 标记（人眼可见）。

    **不动 v3_image.status** —— 那个字段是主图交接的状态机（original/candidate/approved/
    rejected），改它会把「确认换主图」和「撤销」两条路弄坏。

    `boxes` 为 None（失败/不可读）时 **保留库里已有的坐标**，不清空 —— 一次失败的 OCR
    不该把上次算好的框抹掉，否则第 4 步又得重跑 det。
    """
    iid = int(img["image_id"])
    words_hit = sorted({h["word"] for h in hits if h.get("word")})
    snips = store.j([{"w": h["word"], "k": h.get("kind") or "", "s": h.get("snippet") or "",
                      "r": h.get("role") or img["role"], "n": h.get("sequence"),
                      "c": h.get("conf")} for h in hits[:8]])
    bj = store.j(boxes) if boxes else None
    iw = int(img_size[0]) if img_size else 0
    ih = int(img_size[1]) if img_size else 0
    conn.execute(
        "INSERT INTO v3_image_ocr(image_id, product_id, role, seq, state, n_seg,"
        " hit_words, snippets, full_text, engine, seconds, created_at,"
        " img_w, img_h, boxes_json, boxes_at, words_version, words_count)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(image_id) DO UPDATE SET product_id=excluded.product_id,"
        " role=excluded.role, seq=excluded.seq, state=excluded.state,"
        " n_seg=excluded.n_seg, hit_words=excluded.hit_words,"
        " snippets=excluded.snippets, full_text=excluded.full_text,"
        " engine=excluded.engine, seconds=excluded.seconds,"
        " created_at=excluded.created_at,"
        " img_w=COALESCE(excluded.img_w, v3_image_ocr.img_w),"
        " img_h=COALESCE(excluded.img_h, v3_image_ocr.img_h),"
        " boxes_json=CASE WHEN excluded.boxes_json IS NULL OR excluded.boxes_json=''"
        "  THEN v3_image_ocr.boxes_json ELSE excluded.boxes_json END,"
        " boxes_at=CASE WHEN excluded.boxes_json IS NULL OR excluded.boxes_json=''"
        "  THEN v3_image_ocr.boxes_at ELSE excluded.boxes_at END,"
        " words_version=COALESCE(excluded.words_version, v3_image_ocr.words_version),"
        " words_count=COALESCE(excluded.words_count, v3_image_ocr.words_count)",
        (iid, int(img["product_id"]), img["role"], img.get("sequence"), state, n_seg,
         ",".join(words_hit), snips, (full_text or "")[:MAX_FULL_TEXT],
         OCR_ENGINE_NAME, round(secs, 2), store.now(),
         iw or None, ih or None, bj, store.now() if bj else None,
         (words_meta or {}).get("label"), (words_meta or {}).get("count")))
    if state == "ok":
        mark = ("OCR:命中(%s)" % "/".join(words_hit[:4])) if words_hit \
            else ("OCR:干净(%d段)" % n_seg)
    elif state == "unreadable":
        mark = "OCR:不可读"
    else:
        mark = ""                        # 失败不留标记 → 下次还会重试
    row = conn.execute("SELECT note FROM v3_image WHERE image_id=?", (iid,)).fetchone()
    base = re.sub(r"\s*\|\s*OCR:[^|]*$", "", ((row["note"] if row else "") or "")).strip()
    note = (base + " | " + mark).strip(" |") if mark else base
    conn.execute("UPDATE v3_image SET note=?, updated_at=? WHERE image_id=?",
                 (note, store.now(), iid))
    # 每张图一次 commit = 每次都要抢库级写锁；被别的页面占着就退避重试，
    # 不要因为别的页签正在写就把这张图的 OCR 结果丢掉（实测丢过整包收尾）。
    store.retry_locked(conn.commit)


def ocr_image(conn: sqlite3.Connection, img: dict, words: WordSet | None = None,
              force: bool = False) -> dict:
    """单张图 OCR。已读过的直接吃缓存（断点续跑就靠这一条）。"""
    iid = int(img["image_id"])
    _ensure_table(conn)
    if not force:
        row = conn.execute("SELECT * FROM v3_image_ocr WHERE image_id=?", (iid,)).fetchone()
        if row is not None:
            if row["state"] in ("ok", "unreadable"):
                return _row_result(row)
            if row["state"] == "fail" and _same_day(row["created_at"]):
                return {"ok": False, "state": "fail", "cached": True, "hits": [],
                        "text": "", "error": "今天已经失败过，本次跳过"}
    eng = _load_ocr()
    if eng is None:
        return {"ok": False, "state": "no_engine", "hits": [], "text": "",
                "error": "OCR 引擎不可用：%s" % (_OCR_ERR or "未知原因")}
    got = ensure_local(conn, img)
    if not got.get("ok"):
        _save_ocr(conn, img, "fail", 0, [], "", 0.0, str(got.get("error")),
                  words_meta=words_meta(conn))
        return {"ok": False, "state": "fail", "hits": [], "text": "",
                "error": str(got.get("error"))}
    t0 = time.time()
    if words is None:
        words = build_words(conn)
    try:
        texts = eng.run(got["local_path"])
    except Exception as e:  # noqa: BLE001
        _save_ocr(conn, img, "unreadable", 0, [], "", time.time() - t0, str(e),
                  words_meta=words_meta(conn))
        return {"ok": False, "state": "unreadable", "hits": [], "text": "",
                "error": "%s: %s" % (type(e).__name__, e)}
    secs = time.time() - t0
    iw, ih = 0, 0
    if img.get("width") and img.get("height"):
        iw, ih = int(img["width"]), int(img["height"])
    else:
        iw, ih = image_size(got["local_path"])
    hits, full = [], []
    for t in texts:
        seg = (t.get("text") or "").strip()
        if not seg:
            continue
        full.append(seg)
        for w, kind, pos in words.match(seg):
            hits.append({"word": w, "kind": kind, "conf": _match_conf(seg, w),
                         "snippet": _snippet(seg, pos, len(w)), "image_id": iid,
                         "role": img["role"], "sequence": img.get("sequence")})
    seg_boxes = boxes_from_ocr(texts, words, iw, ih)
    _save_ocr(conn, img, "ok", len(texts), hits, " ".join(full), secs,
              boxes=seg_boxes, img_size=(iw, ih), words_meta=words_meta(conn))
    return {"ok": True, "state": "ok", "n_seg": len(texts), "hits": hits,
            "text": " ".join(full)[:MAX_FULL_TEXT], "secs": round(secs, 2),
            "via": got.get("via"), "cached": False,
            "n_boxes": len(seg_boxes), "img_w": iw, "img_h": ih}


# ---------------------------------------------------------------- OCR 一件

_IMG_ROLE_ORDER = {"main": 0, "carousel": 1, "detail": 2, "sku": 3}


def ocr_product(conn: sqlite3.Connection, pid: int, max_images: int = 0,
                force: bool = False, words: WordSet | None = None) -> dict:
    """把一个商品的所有图过一遍 OCR，汇总命中与文本（供详情层用）。

    单张失败不影响这一件，这一件失败也不影响整包 —— 由调用方 try 住。
    """
    _ensure_table(conn)
    pid = int(pid)
    imgs = [i for i in store.list_images(conn, pid)
            if (i.get("role") or "") in _IMG_ROLE_ORDER]
    imgs.sort(key=lambda i: (_IMG_ROLE_ORDER.get(i["role"], 9), i.get("sequence") or 0))
    if max_images and max_images > 0:
        imgs = imgs[:max_images]
    if words is None:
        words = build_words(conn)
    t0 = time.time()
    out = {"ok": True, "product_id": pid, "images": len(imgs), "downloaded": 0,
           "ocr_done": 0, "cached": 0, "failed": 0, "hits": [], "texts": {},
           "engine": OCR_ENGINE_NAME, "errors": [], "seconds": 0.0,
           "words": words_meta(conn)}
    by_role: dict[str, list[str]] = {}
    for i in imgs:
        r = ocr_image(conn, i, words=words, force=force)
        st = r.get("state")
        if st == "ok":
            out["cached" if r.get("cached") else "ocr_done"] += 1
        else:
            out["failed"] += 1
            if len(out["errors"]) < 3:
                out["errors"].append({"image_id": i["image_id"],
                                      "role": i["role"], "error": str(r.get("error"))[:160]})
        if r.get("via") in ("download", "download-direct"):
            out["downloaded"] += 1
        for h in (r.get("hits") or []):
            out["hits"].append(h)
        if r.get("text"):
            by_role.setdefault(i["role"], []).append(r["text"])
    out["texts"] = {k: " ".join(v)[:MAX_FULL_TEXT] for k, v in by_role.items()}
    out["seconds"] = round(time.time() - t0, 1)
    return out


def ocr_stats(conn: sqlite3.Connection, pids: list[int] | None = None) -> dict:
    """图片层盘点：多少图读过、多少张有命中（界面/验收脚本用）。"""
    _ensure_table(conn)
    where, args = "", []
    if pids:
        where = " WHERE product_id IN (%s)" % ",".join("?" * len(pids))
        args = [int(p) for p in pids]
    row = conn.execute(
        "SELECT COUNT(*) n, SUM(CASE WHEN state='ok' THEN 1 ELSE 0 END) ok,"
        " SUM(CASE WHEN COALESCE(hit_words,'')<>'' THEN 1 ELSE 0 END) hit,"
        " SUM(CASE WHEN COALESCE(boxes_json,'')<>'' THEN 1 ELSE 0 END) bxs"
        " FROM v3_image_ocr" + where, args).fetchone()
    return {"ocr_images": int(row["n"] or 0), "ocr_ok": int(row["ok"] or 0),
            "hit_images": int(row["hit"] or 0),
            "with_boxes": int(row["bxs"] or 0),
            "no_boxes": int(row["n"] or 0) - int(row["bxs"] or 0),
            "engine": OCR_ENGINE_NAME, "available": ocr_available()}
