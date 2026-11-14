# -*- coding: utf-8 -*-
"""8.0 · 建库与迁移（生产版，2026-11-10）

与旧 P0 的根本差异：**只在 ops.db 里建表，绝不往 v3.db 塞一张表**。
    python -m app.ops.migrate --apply --report <json>

安全设计
    * 只 CREATE TABLE / CREATE INDEX IF NOT EXISTS，不 ALTER / DROP / UPDATE 任何 v3_* 表
    * 建库前后取「v3.db 全表行数 + 文件哈希」，行数和哈希必须完全一致（§九 不修改 v3.db）
    * 从 9191 的 /api/shops 只读拉真实店铺台账 → 幂等写 ops_account（完整 shop_id）
    * 不写任何 Cookie / AppSecret / Token 明文
    * 9191 不在线时只建表、店铺留空，如实写进报告，不编造
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

from . import config, queue

SCHEMA = Path(__file__).resolve().parent / "schema.sql"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def v3_snapshot() -> dict:
    """只读对商品主库取基线（绝不写）。"""
    v3 = config.v3_path()
    out = {"path": str(v3), "exists": v3.exists(), "bytes": 0, "sha256": "",
           "integrity": "", "tables": {}, "error": ""}
    if not v3.exists():
        out["error"] = "v3.db 不存在"
        return out
    out["bytes"] = v3.stat().st_size
    out["sha256"] = _sha256(v3)
    try:
        con = sqlite3.connect("file:%s?mode=ro" % v3.as_posix(), uri=True, timeout=10)
        try:
            out["integrity"] = con.execute("PRAGMA integrity_check").fetchone()[0]
            for (name,) in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
                out["tables"][name] = con.execute(
                    'SELECT COUNT(*) FROM "%s"' % name).fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
    return out


def _fetch_shops() -> dict:
    """9191 只读 HTTP：三店台账（完整 appid）。失败如实报，绝不编造。"""
    url = config.WX_WEB_BASE + "/api/shops"
    try:
        with urllib.request.urlopen(url, timeout=3.0) as r:
            d = json.loads(r.read().decode("utf-8", "replace") or "{}")
        return {"ok": True, "shops": d.get("shops") or [], "source": url}
    except Exception as e:                                    # noqa: BLE001
        return {"ok": False, "shops": [], "source": url,
                "error": "%s: %s" % (type(e).__name__, e)}


def sync_accounts(con, shops: list) -> dict:
    """幂等写 ops_account：完整 shop_id；只存配置引用不存凭据。"""
    written, skipped = [], []
    now = queue.now_text()
    for s in shops:
        sid = (s.get("appid") or "").strip()
        if not sid:
            skipped.append("缺 appid")
            continue
        kind = s.get("kind") or "wx_api"
        platform = "dy" if kind == "doudian" else "wx"
        engine = "ego" if platform == "dy" else "none"
        with queue.tx(con):
            row = con.execute("SELECT created_at FROM ops_account WHERE account_id=?",
                              (sid,)).fetchone()
            con.execute(
                "INSERT INTO ops_account(account_id, platform, display_name, external_id,"
                " browser_engine, browser_profile_ref, login_status, last_check_at, enabled,"
                " note, created_at, updated_at) VALUES(?,?,?,?,?,'',?,?,?,?,?,?)"
                " ON CONFLICT(account_id) DO UPDATE SET display_name=excluded.display_name,"
                " platform=excluded.platform, external_id=excluded.external_id,"
                " browser_engine=excluded.browser_engine, last_check_at=excluded.last_check_at,"
                " enabled=excluded.enabled, updated_at=excluded.updated_at",
                (sid, platform, s.get("name") or sid, sid, engine,
                 "unknown", now, 1 if s.get("enabled") is not False else 0,
                 "9191 台账同步；凭据不入库", row["created_at"] if row else now, now),
            )
        written.append({"account_id": sid, "name": s.get("name"), "platform": platform})
    return {"written": written, "skipped": skipped, "count": len(written)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-shops", action="store_true", help="不拉 9191（离线自测用）")
    ap.add_argument("--report", default="")
    a = ap.parse_args(argv)
    apply_ = bool(a.apply) and not a.dry_run
    t0 = time.time()

    config.ensure_dirs()
    db = config.db_path()
    before_v3 = v3_snapshot()
    db_existed = db.exists()

    added, accounts, shop_fetch = [], {"count": 0, "note": "dry-run"}, {"ok": False}
    if apply_:
        con = queue.connect(readonly=False, timeout=15)
        try:
            before_tables = {r["name"] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            con.executescript(SCHEMA.read_text(encoding="utf-8"))
            after_tables = {r["name"] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            added = sorted(t for t in after_tables - before_tables if t.startswith("ops_"))
            now = queue.now_text()
            con.execute(
                "INSERT INTO ops_meta(key,value,updated_at) VALUES('schema_version',?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                " updated_at=excluded.updated_at", (config.SCHEMA_VERSION, now))
            con.execute(
                "INSERT INTO ops_meta(key,value,updated_at) VALUES('engine_version',?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                " updated_at=excluded.updated_at", (config.ENGINE_VERSION, now))
            con.execute(
                "INSERT INTO ops_meta(key,value,updated_at) VALUES('architecture',?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                " updated_at=excluded.updated_at", ("ops.db 独立 + v3.db 只读 ATTACH", now))
            if not a.no_shops:
                shop_fetch = _fetch_shops()
                accounts = sync_accounts(con, shop_fetch.get("shops") or [])
        finally:
            con.close()

    after_v3 = v3_snapshot()
    v3_unchanged = (before_v3["sha256"] == after_v3["sha256"]
                    and before_v3["tables"] == after_v3["tables"])
    report = {
        "at": queue.now_text(),
        "engine_version": config.ENGINE_VERSION,
        "schema_version": config.SCHEMA_VERSION,
        "architecture": "ops.db 独立 + v3.db 只读 ATTACH（不往 v3.db 建表）",
        "applied": apply_,
        "db": str(db),
        "db_existed_before": db_existed,
        "db_bytes": db.stat().st_size if db.exists() else 0,
        "ops_tables_added": added,
        "shops_source": shop_fetch,
        "accounts": accounts,
        "v3_before": {k: before_v3[k] for k in ("path", "bytes", "sha256", "integrity")},
        "v3_after": {k: after_v3[k] for k in ("path", "bytes", "sha256", "integrity")},
        "v3_tables_checked": len(before_v3["tables"]),
        "v3_unchanged": v3_unchanged,
        "elapsed_s": round(time.time() - t0, 3),
    }
    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
    print("applied          : %s" % apply_)
    print("ops tables added : %s" % (added or "无（dry-run 或已存在）"))
    print("shops synced     : %s" % accounts.get("count"))
    print("v3.db unchanged  : %s (sha=%s)" % (v3_unchanged, before_v3["sha256"][:16]))
    print("elapsed          : %.3fs" % (time.time() - t0))
    if not v3_unchanged:
        print("!! v3.db 发生变化 —— 立即停止并核对")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
