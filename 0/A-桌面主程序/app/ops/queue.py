# -*- coding: utf-8 -*-
"""8.0 · 运营库连接层 · 生产版（2026-11-10）

在旧 P0 `queue.py` 全部状态机之上，只改一件事：**连接时把商品主库只读挂进来**。

    ops.db（主连接，可写）
      └─ ATTACH DATABASE 'file:<v3.db>?mode=ro' AS vsrc
          └─ CREATE TEMP VIEW v3_product/v3_image/v3_upload_status
             → store.py 里查裸表名的 SQL 一行不改；写 v3_* 被 SQLite 拦（视图不可写）

状态机（唯一合法集合，原样保留）
    queued  --claim-->  running
    running --complete--> done
    running --fail(还有次数)--> queued（带 backoff）
    running --fail(次数用尽)--> failed
    queued  --cancel--> cancelled
    running --租约到期(reaper)--> queued / failed
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import time
import uuid
from pathlib import Path

from . import config

LEGAL_STATES = ("queued", "running", "done", "failed", "cancelled")
_V3_VIEWS = ("v3_product", "v3_image", "v3_upload_status")


def _attach_v3(con: sqlite3.Connection) -> None:
    """只读挂商品主库 + 建临时视图。挂不上不致命（总览会标 degraded，不假装成功）。"""
    v3 = config.v3_path()
    if not v3.exists():
        return
    try:
        uri = "file:%s?mode=ro" % v3.as_posix()
        con.execute("ATTACH DATABASE ? AS vsrc", (uri,))
        for t in _V3_VIEWS:
            con.execute("CREATE TEMP VIEW IF NOT EXISTS %s AS SELECT * FROM vsrc.%s" % (t, t))
    except sqlite3.Error:
        try:
            con.execute("DETACH DATABASE vsrc")
        except sqlite3.Error:
            pass


def connect(db=None, readonly: bool = False, timeout: float = 5.0) -> sqlite3.Connection:
    p = Path(db) if db else config.db_path()
    # ★ 整条连接必须 uri=True：否则 ATTACH 的 'file:...?mode=ro' 会被当字面文件名
    #   （实测 rw 主连接用普通字符串路径时 ATTACH 报 unable to open database）。
    uri = "file:%s?mode=%s" % (p.as_posix(), "ro" if readonly else "rwc")
    con = sqlite3.connect(uri, uri=True, timeout=timeout, isolation_level=None)
    con.execute("PRAGMA busy_timeout=5000")
    if not readonly:
        try:
            if str(con.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
                con.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass
        con.execute("PRAGMA synchronous=NORMAL")
    con.row_factory = sqlite3.Row
    _attach_v3(con)
    return con


@contextlib.contextmanager
def tx(con: sqlite3.Connection):
    """短事务：IMMEDIATE 立刻拿写锁，避免「读后升级」死锁。"""
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.execute("COMMIT")
    except BaseException:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def now_epoch() -> int:
    return int(time.time())


# --------------------------------------------------------------------------- #
# 事件
# --------------------------------------------------------------------------- #
def _event(con, task_id: str, from_state, to_state: str, actor: str, detail: str = "") -> None:
    con.execute(
        "INSERT INTO ops_task_event(task_id, from_state, to_state, actor, detail, at_epoch, at_text)"
        " VALUES(?,?,?,?,?,?,?)",
        (task_id, from_state, to_state, actor, detail[:2000], now_epoch(), now_text()),
    )


# --------------------------------------------------------------------------- #
# 入队
# --------------------------------------------------------------------------- #
def enqueue(con, kind: str, payload=None, platform=None, product_id=None,
            dedupe_key=None, priority: int = 100, max_attempts: int = 3,
            delay: int = 0, actor: str = "api"):
    """返回 (task_id, created)。dedupe_key 命中排队/运行中的同键任务时 created=False。"""
    if dedupe_key:
        row = con.execute(
            "SELECT task_id FROM ops_task WHERE dedupe_key=? AND state IN ('queued','running')",
            (dedupe_key,),
        ).fetchone()
        if row:
            return row["task_id"], False
    tid = "t" + uuid.uuid4().hex[:16]
    ts = now_text()
    with tx(con):
        con.execute(
            "INSERT INTO ops_task(task_id, kind, platform, product_id, dedupe_key, payload,"
            " state, priority, attempts, max_attempts, next_run_epoch, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?, 'queued', ?, 0, ?, ?, ?, ?)",
            (tid, kind, platform, product_id, dedupe_key,
             json.dumps(payload or {}, ensure_ascii=False),
             priority, max_attempts, now_epoch() + int(delay), ts, ts),
        )
        _event(con, tid, None, "queued", actor, "kind=%s" % kind)
    return tid, True


# --------------------------------------------------------------------------- #
# 领取 / 续租 / 完成 / 失败
# --------------------------------------------------------------------------- #
def claim(con, worker: str, lease_s: int = None, kinds=None):
    lease_s = int(lease_s or config.default_lease())
    now = now_epoch()
    with tx(con):
        sql = ("SELECT * FROM ops_task WHERE state='queued'"
               " AND (next_run_epoch IS NULL OR next_run_epoch<=?)")
        args = [now]
        if kinds:
            sql += " AND kind IN (%s)" % ",".join("?" * len(kinds))
            args += list(kinds)
        sql += " ORDER BY priority ASC, created_at ASC LIMIT 1"
        row = con.execute(sql, args).fetchone()
        if not row:
            return None
        tid = row["task_id"]
        con.execute(
            "UPDATE ops_task SET state='running', lease_owner=?, lease_epoch=?,"
            " attempts=attempts+1, started_at=COALESCE(started_at,?), updated_at=?"
            " WHERE task_id=? AND state='queued'",
            (worker, now + lease_s, now_text(), now_text(), tid),
        )
        _event(con, tid, "queued", "running", "worker", "worker=%s lease=%ss" % (worker, lease_s))
        con.execute(
            "INSERT INTO ops_run(task_id, worker, started_at, outcome) VALUES(?,?,?, NULL)",
            (tid, worker, now_text()),
        )
        out = dict(row)
        out.update(state="running", lease_owner=worker, lease_epoch=now + lease_s,
                   attempts=row["attempts"] + 1)
        return out


def heartbeat(con, task_id: str, worker: str, lease_s: int = None) -> bool:
    lease_s = int(lease_s or config.default_lease())
    with tx(con):
        n = con.execute(
            "UPDATE ops_task SET lease_epoch=?, updated_at=?"
            " WHERE task_id=? AND state='running' AND lease_owner=?",
            (now_epoch() + lease_s, now_text(), task_id, worker),
        ).rowcount
        return n > 0


def complete(con, task_id: str, worker: str = None, result=None) -> bool:
    with tx(con):
        sql = ("UPDATE ops_task SET state='done', lease_owner=NULL, lease_epoch=NULL,"
               " result=?, finished_at=?, updated_at=? WHERE task_id=? AND state='running'")
        args = [json.dumps(result or {}, ensure_ascii=False), now_text(), now_text(), task_id]
        if worker:
            sql += " AND lease_owner=?"
            args.append(worker)
        n = con.execute(sql, args).rowcount
        if n:
            _event(con, task_id, "running", "done", "worker", "ok")
            con.execute("UPDATE ops_run SET finished_at=?, outcome='done' WHERE task_id=?"
                        " AND finished_at IS NULL", (now_text(), task_id))
        return n > 0


def fail(con, task_id: str, worker: str = None, error: str = "", retry: bool = True,
         backoff: int = 5) -> str:
    with tx(con):
        row = con.execute("SELECT attempts, max_attempts FROM ops_task WHERE task_id=?",
                          (task_id,)).fetchone()
        if not row:
            return "missing"
        if retry and row["attempts"] < row["max_attempts"]:
            delay = min(600, int(backoff) * (2 ** max(0, row["attempts"] - 1)))
            con.execute(
                "UPDATE ops_task SET state='queued', lease_owner=NULL, lease_epoch=NULL,"
                " last_error=?, next_run_epoch=?, updated_at=? WHERE task_id=?",
                (error[:1000], now_epoch() + delay, now_text(), task_id),
            )
            _event(con, task_id, "running", "queued", "worker",
                   "retry in %ss: %s" % (delay, error[:200]))
            st = "queued"
        else:
            con.execute(
                "UPDATE ops_task SET state='failed', lease_owner=NULL, lease_epoch=NULL,"
                " last_error=?, finished_at=?, updated_at=? WHERE task_id=?",
                (error[:1000], now_text(), now_text(), task_id),
            )
            _event(con, task_id, "running", "failed", "worker", error[:200])
            st = "failed"
        con.execute("UPDATE ops_run SET finished_at=?, outcome=? WHERE task_id=?"
                    " AND finished_at IS NULL", (now_text(), st, task_id))
        return st


def cancel(con, task_id: str, actor: str = "api") -> bool:
    with tx(con):
        n = con.execute(
            "UPDATE ops_task SET state='cancelled', lease_owner=NULL, lease_epoch=NULL,"
            " finished_at=?, updated_at=? WHERE task_id=? AND state IN ('queued','running')",
            (now_text(), now_text(), task_id),
        ).rowcount
        if n:
            _event(con, task_id, "running", "cancelled", actor, "cancelled")
        return n > 0


def recover_expired(con, actor: str = "reaper") -> dict:
    now = now_epoch()
    recovered, failed = [], []
    rows = con.execute(
        "SELECT task_id, attempts, max_attempts FROM ops_task"
        " WHERE state='running' AND lease_epoch IS NOT NULL AND lease_epoch < ?", (now,)
    ).fetchall()
    for r in rows:
        tid = r["task_id"]
        with tx(con):
            cur = con.execute(
                "SELECT state, attempts, max_attempts FROM ops_task WHERE task_id=?", (tid,)
            ).fetchone()
            if not cur or cur["state"] != "running":
                continue
            if cur["attempts"] >= cur["max_attempts"]:
                con.execute(
                    "UPDATE ops_task SET state='failed', lease_owner=NULL, lease_epoch=NULL,"
                    " last_error='lease expired, attempts exhausted', finished_at=?, updated_at=?"
                    " WHERE task_id=?", (now_text(), now_text(), tid),
                )
                _event(con, tid, "running", "failed", actor, "lease_expired attempts_exhausted")
                con.execute("UPDATE ops_run SET finished_at=?, outcome='lease_lost'"
                            " WHERE task_id=? AND finished_at IS NULL", (now_text(), tid))
                failed.append(tid)
            else:
                con.execute(
                    "UPDATE ops_task SET state='queued', lease_owner=NULL, lease_epoch=NULL,"
                    " next_run_epoch=?, last_error='lease expired', updated_at=? WHERE task_id=?",
                    (now_epoch() + 1, now_text(), tid),
                )
                _event(con, tid, "running", "queued", actor, "lease_expired -> requeue")
                con.execute("UPDATE ops_run SET finished_at=?, outcome='lease_lost'"
                            " WHERE task_id=? AND finished_at IS NULL", (now_text(), tid))
                recovered.append(tid)
    return {"requeued": recovered, "failed": failed}


def stats(con) -> dict:
    out = {s: 0 for s in LEGAL_STATES}
    for r in con.execute("SELECT state, COUNT(*) c FROM ops_task GROUP BY state"):
        out[r["state"]] = r["c"]
    row = con.execute("SELECT MIN(created_at) m FROM ops_task WHERE state='queued'").fetchone()
    out["oldest_queued_at"] = row["m"] if row else None
    return out


def get_task(con, task_id: str, with_events: bool = True) -> dict | None:
    row = con.execute("SELECT * FROM ops_task WHERE task_id=?", (task_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    if with_events:
        d["events"] = [dict(r) for r in con.execute(
            "SELECT * FROM ops_task_event WHERE task_id=? ORDER BY event_id", (task_id,))]
        d["runs"] = [dict(r) for r in con.execute(
            "SELECT * FROM ops_run WHERE task_id=? ORDER BY run_id", (task_id,))]
    return d


def list_tasks(con, state: str = None, limit: int = 50) -> list:
    sql = "SELECT * FROM ops_task"
    args = []
    if state:
        sql += " WHERE state=?"
        args.append(state)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(int(limit))
    return [dict(r) for r in con.execute(sql, args)]


def list_events(con, limit: int = 100) -> list:
    return [dict(r) for r in con.execute(
        "SELECT e.*, t.kind FROM ops_task_event e LEFT JOIN ops_task t ON t.task_id=e.task_id"
        " ORDER BY e.event_id DESC LIMIT ?", (int(limit),))]


def list_runs(con, limit: int = 100) -> list:
    return [dict(r) for r in con.execute(
        "SELECT r.*, t.kind FROM ops_run r LEFT JOIN ops_task t ON t.task_id=r.task_id"
        " ORDER BY r.run_id DESC LIMIT ?", (int(limit),))]


def acquire_lock(con, key: str, owner: str, ttl: int = 30) -> bool:
    with tx(con):
        row = con.execute("SELECT owner, expires_epoch FROM ops_lock WHERE lock_key=?",
                          (key,)).fetchone()
        now = now_epoch()
        if row and row["owner"] != owner and (row["expires_epoch"] or 0) > now:
            return False
        con.execute(
            "INSERT INTO ops_lock(lock_key, owner, acquired_at, expires_epoch) VALUES(?,?,?,?)"
            " ON CONFLICT(lock_key) DO UPDATE SET owner=excluded.owner,"
            " acquired_at=excluded.acquired_at, expires_epoch=excluded.expires_epoch",
            (key, owner, now_text(), now + ttl),
        )
        return True


def release_lock(con, key: str, owner: str) -> None:
    with tx(con):
        con.execute("DELETE FROM ops_lock WHERE lock_key=? AND owner=?", (key, owner))
