# -*- coding: utf-8 -*-
"""v3 · 主图人工修补的「编辑台账」（阶段 C）

设计三条铁律（阶段 A 审计定稿）：

  ① **不建第二套版本库** —— 版本本体仍然是 `v3_image` 的一行（`status='candidate'`），
     本表只记「这张新图是怎么补出来的」：遮罩、算法、半径、检查结果、谁确认的。
     所以版本链、原图保护、撤销回退，全部沿用 `images.import_new / approve / revert`。

  ② **写编辑不碰上游判定** —— `images.import_new(keep_status=True)` 不会把
     `v3_product.status`（第三步的 passed/blocked/pending/review）改成 image_handoff。
     这是阶段 A 审计查出来的第二处口径打架，必须由调用方显式要求。

  ③ **原图永远不动** —— 本模块只读 `current_local_path` 指向的文件（读字节），
     一切产物写进新路径；`images.import_new` 也是 `shutil.copy2` 到新文件名。

数据库变更：只 **新增** 一张表 `v3_image_edit`（幂等 CREATE IF NOT EXISTS）。
回滚 = `DROP TABLE v3_image_edit`，`v3_image` / `v3_product` 一个字节不动。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import numpy as np

from . import images, store

try:                                   # 遮罩读写复用阶段 B 的内核，不重写
    from . import maskgen
except Exception:                      # pragma: no cover  # noqa: BLE001
    maskgen = None


DDL = """
CREATE TABLE IF NOT EXISTS v3_image_edit (
    edit_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id         INTEGER NOT NULL,          -- 被编辑的原图（版本链起点）
    product_id       INTEGER NOT NULL,
    version_image_id INTEGER,                   -- v3_image 里的新版本行（草稿时可空）
    pack_code        TEXT,
    mask_path        TEXT,
    mask_sha256      TEXT,
    regions_json     TEXT,                      -- [{kind:rect|poly|brush,points:[...]}, ...]
    method           TEXT,                      -- telea|ns|stamp|mixed
    radius           INTEGER,
    expand_px        INTEGER DEFAULT 0,
    inspect_json     TEXT,                      -- repair.inspect() 原样
    quality          TEXT,                      -- ok|review|bad
    status           TEXT NOT NULL DEFAULT 'draft',   -- draft|saved|confirmed|failed
    note             TEXT,
    actor            TEXT,
    created_at       TEXT,
    confirmed_at     TEXT
);
CREATE INDEX IF NOT EXISTS ix_v3_edit_image ON v3_image_edit(image_id);
CREATE INDEX IF NOT EXISTS ix_v3_edit_ver   ON v3_image_edit(version_image_id);
CREATE INDEX IF NOT EXISTS ix_v3_edit_pid   ON v3_image_edit(product_id);
"""

OK_QUALITY = ("ok", "review", "bad")


# ---------------------------------------------------------------- 表
def ensure_table(conn) -> None:
    """幂等建表。老库上线时由这里补，不依赖 store.init() 有没有跑过。"""
    conn.executescript(DDL)
    conn.commit()


def has_table(conn) -> bool:
    try:
        return bool(conn.execute(
            "select 1 from sqlite_master where type='table' and name='v3_image_edit'"
        ).fetchone())
    except Exception:                  # noqa: BLE001
        return False


# ---------------------------------------------------------------- 遮罩留证
def mask_dir(pid: int, image_id: int = 0) -> str:
    d = os.path.join(images._img_dir(int(pid)), "masks")
    if image_id:
        d = os.path.join(d, str(int(image_id)))
    os.makedirs(d, exist_ok=True)
    return d


def save_mask(pid: int, image_id: int, mask, tag: str = "") -> tuple[str, str]:
    """遮罩存 PNG（可复核、可重放）。返回 (路径, sha256)。"""
    if maskgen is None:
        raise RuntimeError("maskgen 不可用，无法保存遮罩")
    m = np.asarray(mask)
    if m.ndim != 2:
        raise ValueError("遮罩必须是单通道二值图")
    name = "%s_%s.png" % (time.strftime("%Y%m%d%H%M%S"), tag or "mask")
    path = os.path.join(mask_dir(pid, image_id), name)
    maskgen.save_mask(m, path)
    sha = images.sha256_file(path) if os.path.isfile(path) else ""
    return path, sha


def load_mask(path: str):
    if maskgen is None or not path or not os.path.isfile(path):
        return None
    return maskgen.load_mask(path)


def mask_stats(mask) -> dict:
    if maskgen is None or mask is None:
        return {}
    try:
        return maskgen.mask_stats(mask)
    except Exception:                  # noqa: BLE001
        return {}


# ---------------------------------------------------------------- 保存新版本
def save_version(conn, pid: int, base_image_id: int, result_path: str, *,
                 mask=None, regions: list[dict] | None = None,
                 method: str = "telea", radius: int = 3, expand_px: int = 0,
                 inspect_result: dict | None = None,
                 pack_code: str = "", note: str = "", actor: str = "4.1",
                 keep_status: bool = True) -> dict:
    """把修补结果存成**新版本**（候选），并写一条编辑台账。

    返回 {ok, version_image_id, edit_id, sha256, duplicate_of?}
    """
    ensure_table(conn)
    p = store.get_product(conn, pid)
    if not p:
        return {"ok": False, "error": "没有这个商品 pid=%s" % pid}

    base = conn.execute("select * from v3_image where image_id=? and product_id=?",
                        (base_image_id, pid)).fetchone()
    if not base:
        return {"ok": False, "error": "原图 image_id=%s 不属于商品 %s" % (base_image_id, pid)}
    base = dict(base)
    if not os.path.isfile(result_path):
        return {"ok": False, "error": "修补结果文件不存在：%s" % result_path}

    # 版本本体仍走 images.import_new（复制新文件 + 候选态 + sha 撞库 + parent 指向原主图）
    r = images.import_new(conn, pid, result_path,
                          note=note or ("4.1 人工修补 %s r=%s" % (method, radius)),
                          role=base.get("role") or "main",
                          actor=actor, keep_status=keep_status)
    if not r.get("ok"):
        return r

    vid = int(r["image_id"])
    # 版本链：让新版本的 parent 精确指向「被编辑的那张」，而不是商品的首张主图
    conn.execute("update v3_image set parent_image_id=? , updated_at=? where image_id=?",
                 (int(base_image_id), store.now(), vid))

    mpath, msha = ("", "")
    if mask is not None:
        try:
            mpath, msha = save_mask(pid, base_image_id, mask, tag=method)
        except Exception as e:         # noqa: BLE001
            mpath, msha = "", ""
            note = (note + " | 遮罩保存失败：%s" % e)[-500:]

    insp = inspect_result or {}
    quality = str(insp.get("verdict") or insp.get("quality") or "").lower()
    if quality not in OK_QUALITY:
        quality = "review" if insp else ""

    cur = conn.execute(
        "insert into v3_image_edit(image_id, product_id, version_image_id, pack_code,"
        " mask_path, mask_sha256, regions_json, method, radius, expand_px,"
        " inspect_json, quality, status, note, actor, created_at)"
        " values(?,?,?,?,?,?,?,?,?,?,?,?,'saved',?,?,?)",
        (int(base_image_id), int(pid), vid, pack_code or (p.get("pack_code") or ""),
         mpath, msha, json.dumps(regions or [], ensure_ascii=False),
         str(method), int(radius or 0), int(expand_px or 0),
         json.dumps(insp, ensure_ascii=False, default=str), quality,
         note[:500], actor, store.now()))
    eid = int(cur.lastrowid)
    store.audit(conn, "image.edit_save", "v3_image", vid,
                None, {"base": int(base_image_id), "method": method,
                       "radius": radius, "mask": mpath}, actor=actor)
    conn.commit()
    return {"ok": True, "version_image_id": vid, "edit_id": eid,
            "sha256": r.get("sha256"), "local_path": r.get("local_path"),
            "mask_path": mpath, "quality": quality,
            "parent_image_id": int(base_image_id),
            "note": "已存为新版本（候选）；点「确认合格」才会成为发布图"}


def save_draft(conn, pid: int, base_image_id: int, *,
               mask=None, regions: list[dict] | None = None,
               method: str = "", radius: int = 0, expand_px: int = 0,
               inspect_result: dict | None = None,
               note: str = "", actor: str = "4.1") -> dict:
    """存草稿：只留遮罩与参数，不生成版本、不动任何指针。"""
    ensure_table(conn)
    mpath, msha = ("", "")
    if mask is not None:
        mpath, msha = save_mask(pid, base_image_id, mask, tag="draft")
    cur = conn.execute(
        "insert into v3_image_edit(image_id, product_id, version_image_id, mask_path,"
        " mask_sha256, regions_json, method, radius, expand_px, inspect_json,"
        " status, note, actor, created_at) values(?,?,NULL,?,?,?,?,?,?,?,'draft',?,?,?)",
        (int(base_image_id), int(pid), mpath, msha,
         json.dumps(regions or [], ensure_ascii=False), str(method or ""),
         int(radius or 0), int(expand_px or 0),
         json.dumps(inspect_result or {}, ensure_ascii=False, default=str),
         note[:500], actor, store.now()))
    conn.commit()
    return {"ok": True, "edit_id": int(cur.lastrowid), "mask_path": mpath,
            "note": "草稿已存（未生成版本）"}


def confirm(conn, pid: int, version_image_id: int, actor: str = "4.1") -> dict:
    """人工确认：把版本设为发布图（只移指针），台账置 confirmed。"""
    ensure_table(conn)
    r = images.approve(conn, pid, int(version_image_id), actor=actor)
    if not r.get("ok"):
        return r
    conn.execute("update v3_image_edit set status='confirmed', confirmed_at=?"
                 " where version_image_id=?", (store.now(), int(version_image_id)))
    conn.commit()
    return r


def mark_failed(conn, pid: int, base_image_id: int, reason: str,
                actor: str = "4.1") -> dict:
    ensure_table(conn)
    cur = conn.execute(
        "insert into v3_image_edit(image_id, product_id, status, note, actor, created_at)"
        " values(?,?,'failed',?,?,?)",
        (int(base_image_id), int(pid), str(reason)[:500], actor, store.now()))
    conn.commit()
    return {"ok": True, "edit_id": int(cur.lastrowid)}


def confirm_original(conn, pid: int, base_image_id: int, note: str = "",
                     actor: str = "4.1") -> dict:
    """「原图就合格，不用动」—— 留一条审核记录，**不改任何指针、不生成版本**。

    批量确认走这里：它只证明"人看过并认可原图"，不伪装成修补结果。
    """
    ensure_table(conn)
    r = conn.execute("select image_id from v3_image where image_id=? and product_id=?",
                     (int(base_image_id), int(pid))).fetchone()
    if not r:
        return {"ok": False, "error": "这张图不属于商品 %s" % pid}
    cur = conn.execute(
        "insert into v3_image_edit(image_id, product_id, version_image_id, method, status,"
        " note, actor, created_at, confirmed_at)"
        " values(?,?,NULL,'original','confirmed',?,?,?,?)",
        (int(base_image_id), int(pid), (note or "确认原图无需修补")[:500],
         actor, store.now(), store.now()))
    store.audit(conn, "image.confirm_original", "v3_image", int(base_image_id),
                None, {"note": note}, actor=actor)
    conn.commit()
    return {"ok": True, "edit_id": int(cur.lastrowid), "image_id": int(base_image_id),
            "note": "已记录「原图合格」，未生成版本、未动指针"}


# ---------------------------------------------------------------- 查询
def list_edits(conn, image_id: int = 0, pid: int = 0, limit: int = 200) -> list[dict]:
    if not has_table(conn):
        return []
    where, args = [], []
    if image_id:
        where.append("image_id=?"); args.append(int(image_id))
    if pid:
        where.append("product_id=?"); args.append(int(pid))
    w = ("where " + " and ".join(where)) if where else ""
    try:
        rows = conn.execute("select * from v3_image_edit %s order by edit_id desc limit ?"
                            % w, tuple(args) + (int(limit),)).fetchall()
    except Exception:                  # noqa: BLE001
        return []
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["regions"] = json.loads(d.get("regions_json") or "[]")
        except Exception:              # noqa: BLE001
            d["regions"] = []
        try:
            d["inspect"] = json.loads(d.get("inspect_json") or "{}")
        except Exception:              # noqa: BLE001
            d["inspect"] = {}
        out.append(d)
    return out


def latest(conn, image_id: int) -> dict | None:
    got = list_edits(conn, image_id=image_id, limit=1)
    return got[0] if got else None


def state_of(conn, image_id: int) -> dict:
    """照片墙卡片用的四态：未处理 / 有草稿 / 有版本 / 已确认。"""
    rows = list_edits(conn, image_id=image_id, limit=50)
    drafts = [r for r in rows if r.get("status") == "draft"]
    saved = [r for r in rows if r.get("status") in ("saved", "confirmed")]
    conf = [r for r in rows if r.get("status") == "confirmed"]
    return {"has_draft": bool(drafts), "has_version": bool(saved),
            "confirmed": bool(conf),
            "n_draft": len(drafts), "n_version": len(saved),
            "last_method": (rows[0].get("method") if rows else ""),
            "last_quality": (rows[0].get("quality") if rows else "")}


def summary_map(conn, image_ids: list[int]) -> dict[int, dict]:
    """批量取状态（照片墙一次查完，避免每张一次查询）。"""
    if not image_ids or not has_table(conn):
        return {}
    ids = [int(i) for i in image_ids]
    out: dict[int, dict] = {i: {"has_draft": False, "has_version": False,
                                "confirmed": False, "n_draft": 0, "n_version": 0,
                                "last_method": "", "last_quality": ""} for i in ids}
    chunk = 400
    for k in range(0, len(ids), chunk):
        part = ids[k:k + chunk]
        q = "select image_id, status, method, quality from v3_image_edit where image_id in (%s)" \
            % ",".join("?" * len(part))
        try:
            rows = conn.execute(q, tuple(part)).fetchall()
        except Exception:              # noqa: BLE001
            break
        for r in rows:
            d = out.get(int(r["image_id"]))
            if not d:
                continue
            st = r["status"]
            if st == "draft":
                d["has_draft"] = True; d["n_draft"] += 1
            elif st in ("saved", "confirmed"):
                d["has_version"] = True; d["n_version"] += 1
                if st == "confirmed":
                    d["confirmed"] = True
            if r["method"] and not d["last_method"]:
                d["last_method"] = r["method"]
                d["last_quality"] = r["quality"] or ""
    return out
