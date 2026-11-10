# -*- coding: utf-8 -*-
"""只读查询层：商品 / 视频 / 任务 / 指标 / 账号。

一律用 `mode=ro` 打开同一份 v3.db —— 查询绝不写业务表。
若只读打开失败（例如 -shm 不可写），回退到普通连接，并把 ro_fallback 报进 health，
**不伪装成只读**。
"""
from __future__ import annotations

import sqlite3

from . import queue


def _ro(db=None):
    return queue.connect(db, readonly=True, timeout=3.0)


def _fallback(db=None):
    return queue.connect(db, readonly=False, timeout=3.0)


def query(db, fn):
    """先只读，失败再回退；返回 (data, mode)。mode = 'ro' | 'rw-fallback'."""
    try:
        con = _ro(db)
        try:
            return fn(con), "ro"
        finally:
            con.close()
    except sqlite3.Error:
        con = _fallback(db)
        try:
            return fn(con), "rw-fallback"
        finally:
            con.close()


# --------------------------------------------------------------------------- #
def health(con: sqlite3.Connection, integrity: str = None) -> dict:
    """integrity 由调用方传入（引擎侧缓存 15 分钟）；直接调用时才算，且用 quick_check(1)。"""
    out = {}
    try:
        out["integrity"] = integrity if integrity is not None else \
            con.execute("PRAGMA quick_check(1)").fetchone()[0]
    except Exception as e:                                  # noqa: BLE001
        out["integrity"] = "error: %s" % e
    try:
        out["user_version"] = con.execute("PRAGMA user_version").fetchone()[0]
    except Exception:                                       # noqa: BLE001
        out["user_version"] = None
    try:
        out["journal_mode"] = con.execute("PRAGMA journal_mode").fetchone()[0]
    except Exception:                                       # noqa: BLE001
        out["journal_mode"] = None
    try:
        out["ops_tables"] = con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name LIKE 'ops_%'"
        ).fetchone()[0]
    except Exception:                                       # noqa: BLE001
        out["ops_tables"] = 0
    return out


def products(con, limit: int = 50, q: str = None, status: str = None) -> list:
    sql = """
    SELECT p.product_id, p.original_title, p.status, p.price_min, p.price_max,
           p.profile, p.category_path, p.updated_at,
           u.dy_state, u.dy_tid, u.wx_state, u.wx_product_id,
           (SELECT COUNT(*) FROM v3_image i WHERE i.product_id=p.product_id AND i.role='video') AS videos,
           (SELECT COUNT(*) FROM v3_image i WHERE i.product_id=p.product_id AND i.role='main')  AS mains
    FROM v3_product p
    LEFT JOIN v3_upload_status u ON u.product_id = p.product_id
    WHERE 1=1
    """
    args = []
    if q:
        sql += " AND (p.original_title LIKE ? OR CAST(p.product_id AS TEXT) LIKE ?)"
        args += ["%%%s%%" % q, "%%%s%%" % q]
    if status:
        sql += " AND p.status = ?"
        args.append(status)
    sql += " ORDER BY p.updated_at DESC LIMIT ?"
    args.append(int(limit))
    return [dict(r) for r in con.execute(sql, args)]


def product_one(con, product_id: int) -> dict | None:
    rows = products(con, limit=1, q=str(product_id))
    for r in rows:
        if r["product_id"] == int(product_id):
            return r
    return None


def videos(con, limit: int = 50, product_id: int = None) -> list:
    sql = """
    SELECT i.image_id, i.product_id, i.slot_key, i.role, i.dl_state, i.width, i.height,
           i.current_local_path, i.content_sha256, p.original_title,
           a.asset_id, a.data_status, a.duration_s, a.vcodec
    FROM v3_image i
    LEFT JOIN v3_product p ON p.product_id = i.product_id
    LEFT JOIN ops_video_asset a ON a.image_id = i.image_id
    WHERE i.role='video'
    """
    args = []
    if product_id:
        sql += " AND i.product_id=?"
        args.append(int(product_id))
    sql += " ORDER BY i.image_id DESC LIMIT ?"
    args.append(int(limit))
    return [dict(r) for r in con.execute(sql, args)]


def video_stats(con) -> dict:
    d = {}
    d["db_video_rows"] = con.execute(
        "SELECT COUNT(*) FROM v3_image WHERE role='video'").fetchone()[0]
    d["dl_ok"] = con.execute(
        "SELECT COUNT(*) FROM v3_image WHERE role='video' AND dl_state='ok'").fetchone()[0]
    try:
        d["asset_rows"] = con.execute("SELECT COUNT(*) FROM ops_video_asset").fetchone()[0]
        d["asset_measured"] = con.execute(
            "SELECT COUNT(*) FROM ops_video_asset WHERE data_status='measured'").fetchone()[0]
    except sqlite3.Error:
        d["asset_rows"] = None
        d["asset_measured"] = None
    return d


def metrics(con, day: str = None, platform: str = None, limit: int = 200) -> dict:
    sql = "SELECT * FROM ops_metric_daily WHERE 1=1"
    args = []
    if day:
        sql += " AND day=?"
        args.append(day)
    if platform:
        sql += " AND platform=?"
        args.append(platform)
    sql += " ORDER BY day DESC, product_id LIMIT ?"
    args.append(int(limit))
    rows = [dict(r) for r in con.execute(sql, args)]
    by_status = {}
    for r in rows:
        by_status[r["data_status"]] = by_status.get(r["data_status"], 0) + 1
    return {"rows": rows, "by_data_status": by_status,
            "note": "没有数据不等于 0：data_status 区分 zero_confirmed / api_unauthorized / page_absent / fetch_failed / not_collected"}


def accounts(con) -> list:
    try:
        return [dict(r) for r in con.execute(
            "SELECT account_id, platform, display_name, external_id, browser_engine,"
            " browser_profile_ref, login_status, last_check_at, enabled, note"
            " FROM ops_account ORDER BY platform, account_id")]
    except sqlite3.Error:
        return []


def errors(con, limit: int = 50) -> list:
    try:
        return [dict(r) for r in con.execute(
            "SELECT * FROM ops_error_event ORDER BY error_id DESC LIMIT ?", (int(limit),))]
    except sqlite3.Error:
        return []


def table_counts(con) -> dict:
    out = {}
    for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        name = r["name"]
        try:
            out[name] = con.execute('SELECT COUNT(*) FROM "%s"' % name).fetchone()[0]
        except sqlite3.Error:
            out[name] = None
    return out
