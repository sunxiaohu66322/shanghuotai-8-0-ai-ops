# -*- coding: utf-8 -*-
"""数据库迁移：把 ops_* 表加进 v3.db（P0 只允许在开发副本上跑）。

用法
    python -m ops.migrate --db <path> --dry-run --report <json>
    python -m ops.migrate --db <path> --apply   --report <json>

安全设计
    * 只 CREATE TABLE / CREATE INDEX IF NOT EXISTS，**不 ALTER、不 DROP、不 UPDATE 任何 v3_ 表**。
    * 迁移前后各取一次「全部表行数 + integrity_check + user_version」，逐表比对，写进报告。
    * 只要有任何 v3_ 表行数变化，退出码 = 3（不算成功）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

from . import config, queue, store

SCHEMA = Path(__file__).resolve().parent / "schema.sql"


def snapshot(db: Path) -> dict:
    con = queue.connect(db, readonly=False, timeout=10)
    try:
        counts = store.table_counts(con)
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        uv = con.execute("PRAGMA user_version").fetchone()[0]
        jm = con.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        con.close()
    return {"counts": counts, "integrity": integrity, "user_version": uv,
            "journal_mode": jm, "bytes": db.stat().st_size if db.exists() else 0}


def business_counts(counts: dict) -> dict:
    """只保留业务表（排除 ops_* 与 sqlite_*）。"""
    return {k: v for k, v in counts.items()
            if not k.startswith("ops_") and not k.startswith("sqlite_")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.db_path()))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", default="")
    a = ap.parse_args(argv)

    db = Path(a.db)
    if not db.exists():
        print("DB 不存在: %s" % db)
        return 2
    apply_ = bool(a.apply) and not a.dry_run
    t0 = time.time()

    before = snapshot(db)
    sql = SCHEMA.read_text(encoding="utf-8")
    ops_tables_before = [k for k in before["counts"] if k.startswith("ops_")]

    added = []
    after = before
    if apply_:
        con = sqlite3.connect(str(db), timeout=15, isolation_level=None)
        try:
            con.execute("PRAGMA busy_timeout=10000")
            con.executescript(sql)
            now = queue.now_text()
            con.execute(
                "INSERT INTO ops_meta(key, value, updated_at) VALUES('schema_version', ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (config.SCHEMA_VERSION, now))
            con.execute(
                "INSERT INTO ops_meta(key, value, updated_at) VALUES('engine_version', ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (config.ENGINE_VERSION, now))
        finally:
            con.close()
        after = snapshot(db)
        added = sorted(k for k in after["counts"] if k.startswith("ops_") and k not in ops_tables_before)

    b_counts, a_counts = business_counts(before["counts"]), business_counts(after["counts"])
    diffs = {k: (b_counts.get(k), a_counts.get(k))
             for k in set(b_counts) | set(a_counts)
             if b_counts.get(k) != a_counts.get(k)}

    report = {
        "at": queue.now_text(),
        "db": str(db),
        "applied": apply_,
        "schema_version": config.SCHEMA_VERSION,
        "elapsed_s": round(time.time() - t0, 3),
        "before": before,
        "after": after,
        "ops_tables_before": sorted(ops_tables_before),
        "ops_tables_added": added,
        "business_table_diffs": diffs,
        "business_tables_checked": len(b_counts),
        "integrity_ok": after["integrity"] == "ok",
        "row_counts_identical": len(diffs) == 0,
    }
    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print("db                 : %s" % db)
    print("applied            : %s" % apply_)
    print("business tables    : %d  (diffs=%d)" % (len(b_counts), len(diffs)))
    print("ops tables before  : %s" % (sorted(ops_tables_before) or "无"))
    print("ops tables added   : %s" % (added or "无（dry-run 或已存在）"))
    print("integrity_check    : %s" % after["integrity"])
    print("user_version       : %s -> %s" % (before["user_version"], after["user_version"]))
    print("journal_mode       : %s" % after["journal_mode"])
    if diffs:
        print("!! 业务表行数发生变化: %s" % json.dumps(diffs, ensure_ascii=False))
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
