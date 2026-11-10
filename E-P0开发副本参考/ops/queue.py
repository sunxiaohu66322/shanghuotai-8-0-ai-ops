# -*- coding: utf-8 -*-
"""ops_task 持久化队列：状态机 + 租约 + 去重 + 退避 + 过期回收。

状态机（唯一合法集合）
    queued  --claim-->  running
    running --complete--> done
    running --fail(还有次数)--> queued（带 backoff）
    running --fail(次数用尽)--> failed
    queued  --cancel--> cancelled
    running --租约到期(reaper)--> queued / failed（次数用尽时）

设计要点
    * 每次写操作都是一个「短事务」：BEGIN IMMEDIATE → 若干行 → COMMIT，绝不跨网络/IO。
    * attempts 在 claim 时 +1（即「这次尝试已开始」），所以杀进程不会丢掉尝试次数。
    * 去重靠 ux_ops_task_dedupe 部分唯一索引，不靠应用层查重（并发下才可靠）。
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


# --------------------------------------------------------------------------- #
# 连接
# --------------------------------------------------------------------------- #
def connect(db=None, readonly: bool = False, timeout: float = 5.0) -> sqlite3.Connection:
    p = Path(db) if db else config.db_path()
    if readonly:
        uri = "file:%s?mode=ro" % p.as_posix()
        con = sqlite3.connect(uri, uri=True, timeout=timeout, isolation_level=None)
        con.execute("PRAGMA busy_timeout=5000")
    else:
        con = sqlite3.connect(str(p), timeout=timeout, isolation_level=None)
        con.execute("PRAGMA busy_timeout=5000")     # 并发时等 5 秒再报 locked
        # 只在确实不是 WAL 时才切换 —— 每次 connect 都写 PRAGMA 会平白抢写锁
        try:
            if str(con.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
                con.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass
        con.execute("PRAGMA synchronous=NORMAL")
    con.row_factory = sqlite3.Row
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
    """返回任务的新状态（queued / failed）。"""
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


# --------------------------------------------------------------------------- #
# 过期租约回收（引擎被杀 / 进程崩溃后靠它恢复）
# --------------------------------------------------------------------------- #
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
            # 二次确认：拿到写锁后再查一次，避免与 heartbeat 竞争
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


# --------------------------------------------------------------------------- #
# 查询 / 统计 / 单实例锁
# --------------------------------------------------------------------------- #
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
