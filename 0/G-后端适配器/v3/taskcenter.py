# -*- coding: utf-8 -*-
"""v3 · 统一任务中心 + 视频状态（2026-10-25 新增，第 1 批后端）

两件事，都是任务书第 1 批要求的地基：

① **统一任务记录**：下载 / OCR / 精筛 / 检测 / 修补 / 出表 全部走同一张表
   `v3_task_log` + 同一份 JSONL 日志，字段严格按任务书 1.3：
   `task_id / batch_id / product_id / media_id / stage / action / status /
    progress / started_at / finished_at / error_code / error_message`

② **视频状态**：区分「源里有视频」与「已下载」，供 2.5 顶栏筛选与卡片角标用。
   现有库里 `role='video'` 12 条，`dl_state` 已能区分 ok / pending / fail，
   这里只做**聚合与筛选**，不改任何媒体行。

设计约束（按主人规矩）：
· 日志两条腿：SQLite（可查询、可恢复）+ JSONL（可直接甩给本地 AI 排障）。
· **脱敏**：URL 只保留 scheme+host+path 前 80 字，查询串一律截掉，不发 token。
· 不伪造进度：没有总字节就是 `progress=None`，不写 0.0 冒充。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from datetime import datetime

SENSITIVE_QS = ("token", "key", "sig", "auth", "secret", "session", "ticket", "code")


def _logs_dir() -> str:
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                     "data", "v3", "logs")
    os.makedirs(d, exist_ok=True)
    return d


def scrub_url(u: str, keep: int = 80) -> str:
    """把 URL 里的查询串整段砍掉，避免把签名/token 写进诊断日志。"""
    s = (u or "").strip()
    if not s:
        return ""
    if "?" in s:
        s = s.split("?", 1)[0]
    if "#" in s:
        s = s.split("#", 1)[0]
    return s[:keep] + ("…" if len(s) > keep else "")


TABLE = """
CREATE TABLE IF NOT EXISTS v3_task_log (
    task_id      TEXT PRIMARY KEY,
    batch_id     TEXT,
    product_id   INTEGER,
    media_id     TEXT,
    stage        TEXT,          -- 2.5 / 3.0 / 4.0 / 4.1 / 5.0
    action       TEXT,          -- download / ocr / detect / screen / heal / export
    status       TEXT,          -- queued / running / done / failed / paused / stopped
    progress     REAL,          -- NULL = 未知（不伪造）
    started_at   TEXT,
    finished_at  TEXT,
    error_code   TEXT,
    error_message TEXT,
    payload      TEXT,
    updated_at   TEXT
)
"""

IDX = ("CREATE INDEX IF NOT EXISTS ix_task_stage ON v3_task_log(stage, status)",
       "CREATE INDEX IF NOT EXISTS ix_task_pid ON v3_task_log(product_id)")


def ensure(conn: sqlite3.Connection) -> None:
    conn.execute(TABLE)
    for s in IDX:
        conn.execute(s)
    conn.commit()


def _now() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def begin(conn, stage: str, action: str, product_id=None, media_id=None,
          batch_id=None, payload=None, task_id=None) -> str:
    ensure(conn)
    tid = task_id or "t-%d-%s" % (int(time.time() * 1000), uuid.uuid4().hex[:6])
    conn.execute(
        "INSERT OR REPLACE INTO v3_task_log (task_id, batch_id, product_id, media_id, stage,"
        " action, status, progress, started_at, payload, updated_at)"
        " VALUES (?,?,?,?,?,?,'running',NULL,?,?,?)",
        (tid, batch_id, product_id, media_id, stage, action, _now(),
         json.dumps(payload or {}, ensure_ascii=False), _now()))
    conn.commit()
    event(conn, tid, stage, action, "running", product_id=product_id, media_id=media_id,
          batch_id=batch_id, extra=payload)
    return tid


def update(conn, task_id: str, status: str = None, progress: float = None,
           error_code: str = "", error_message: str = "", payload=None,
           finish: bool = False) -> dict:
    ensure(conn)
    sets, vals = ["updated_at=?"], [_now()]
    if status:
        sets.append("status=?")
        vals.append(status)
    if progress is not None:
        sets.append("progress=?")
        vals.append(float(progress))
    if error_code:
        sets.append("error_code=?")
        vals.append(error_code)
    if error_message:
        sets.append("error_message=?")
        vals.append(str(error_message)[:1000])
    if payload is not None:
        sets.append("payload=?")
        vals.append(json.dumps(payload, ensure_ascii=False))
    if finish:
        sets.append("finished_at=?")
        vals.append(_now())
    vals.append(task_id)
    conn.execute("UPDATE v3_task_log SET %s WHERE task_id=?" % ",".join(sets), vals)
    conn.commit()
    row = conn.execute("SELECT * FROM v3_task_log WHERE task_id=?", (task_id,)).fetchone()
    d = dict(zip([c[0] for c in conn.execute(
        "SELECT * FROM v3_task_log WHERE task_id=?", (task_id,)).description], row)) if row else {}
    event(conn, task_id, d.get("stage"), d.get("action"), status or d.get("status"),
          product_id=d.get("product_id"), media_id=d.get("media_id"),
          batch_id=d.get("batch_id"), error_message=error_message, extra=payload)
    return d


def event(conn, task_id, stage, action, status, product_id=None, media_id=None,
          batch_id=None, error_message="", extra=None) -> None:
    """写一行 JSONL（机器读）—— 同时保证不写敏感内容。"""
    rec = {
        "ts": _now(), "task_id": task_id, "batch_id": batch_id, "stage": stage,
        "action": action, "status": status, "product_id": product_id,
        "media_id": media_id, "error": error_message or "",
    }
    if extra:
        safe = {}
        for k, v in dict(extra).items():
            if isinstance(v, str) and v.startswith("http"):
                safe[k] = scrub_url(v)
            else:
                safe[k] = v
        rec["data"] = safe
    path = os.path.join(_logs_dir(), "tasks-%s.jsonl" % datetime.now().strftime('%Y%m%d'))
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def human(tid, stage, action, status, product_id=None, media_id=None, sec=None, why="") -> str:
    """人读一行：`[4.0][task=…][商品=…][图片=…][OCR] 完成，耗时=1.2s`"""
    bits = ["[%s]" % stage, "[task=%s]" % (tid or "-")]
    if product_id not in (None, ""):
        bits.append("[商品=%s]" % product_id)
    if media_id not in (None, ""):
        bits.append("[图片=%s]" % media_id)
    bits.append("[%s]" % action)
    bits.append(status)
    if sec is not None:
        bits.append("耗时=%.1fs" % float(sec))
    if why:
        bits.append("原因=%s" % why)
    return " ".join(bits) + "，"


def list_tasks(conn, stage=None, status=None, product_id=None, limit=200) -> list[dict]:
    ensure(conn)
    sql = "SELECT * FROM v3_task_log WHERE 1=1"
    args = []
    if stage:
        sql += " AND stage=?"
        args.append(stage)
    if status:
        sql += " AND status=?"
        args.append(status)
    if product_id not in (None, ""):
        sql += " AND product_id=?"
        args.append(int(product_id))
    sql += " ORDER BY started_at DESC, task_id DESC LIMIT ?"
    args.append(int(limit))
    cur = conn.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def summary(conn) -> dict:
    ensure(conn)
    rows = conn.execute("SELECT stage, status, COUNT(*) n FROM v3_task_log"
                        " GROUP BY stage, status").fetchall()
    out = {}
    for stage, status, n in rows:
        out.setdefault(stage or "-", {})[status or "-"] = n
    return out


# ---------------------------------------------------------------- 视频状态

VIDEO_BUCKETS = ("all", "has_video", "no_video", "video_ok", "video_fail")


def video_stats(conn) -> dict:
    """每个商品的视频情况：源有 / 已下 / 失败 / 待下。"""
    rows = conn.execute("""
        SELECT p.product_id,
               SUM(CASE WHEN i.role='video' THEN 1 ELSE 0 END) v_total,
               SUM(CASE WHEN i.role='video' AND i.dl_state='ok' THEN 1 ELSE 0 END) v_ok,
               SUM(CASE WHEN i.role='video' AND i.dl_state='fail' THEN 1 ELSE 0 END) v_fail,
               SUM(CASE WHEN i.role='video' AND i.dl_state NOT IN ('ok','fail') THEN 1 ELSE 0 END) v_pending
          FROM v3_product p LEFT JOIN v3_image i ON i.product_id=p.product_id
         GROUP BY p.product_id ORDER BY p.product_id""").fetchall()
    out = {}
    for pid, total, ok, fail, pend in rows:
        total, ok, fail, pend = (total or 0), (ok or 0), (fail or 0), (pend or 0)
        if total == 0:
            state = "none"
        elif fail:
            state = "fail"
        elif pend:
            state = "pending"
        elif ok:
            state = "ok"
        else:
            state = "unknown"
        out[pid] = {"total": total, "ok": ok, "fail": fail, "pending": pend, "state": state}
    return out


def video_filter(conn, bucket: str = "all") -> list[int]:
    """按任务书 1.1 的五种筛选返回商品 id 列表。"""
    st = video_stats(conn)
    if bucket in ("all", "", None):
        return sorted(st)
    if bucket == "has_video":
        return sorted(k for k, v in st.items() if v["total"] > 0)
    if bucket == "no_video":
        return sorted(k for k, v in st.items() if v["total"] == 0)
    if bucket == "video_ok":
        return sorted(k for k, v in st.items() if v["state"] == "ok")
    if bucket == "video_fail":
        return sorted(k for k, v in st.items() if v["state"] == "fail")
    raise ValueError("bucket 只能是 %s" % (VIDEO_BUCKETS,))


def video_items(conn, product_id: int) -> list[dict]:
    """一件商品的视频明细（给 2.5 右侧「视频」区用）。"""
    cur = conn.execute(
        "SELECT image_id, dl_state, original_url, current_local_path, width, height,"
        " note, updated_at FROM v3_image WHERE product_id=? AND role='video'"
        " ORDER BY sequence, image_id", (int(product_id),))
    cols = [d[0] for d in cur.description]
    out = []
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        p = d.get("current_local_path") or ""
        d["exists"] = bool(p) and os.path.exists(p)
        d["size_bytes"] = os.path.getsize(p) if d["exists"] else 0
        d["filename"] = os.path.basename(p) if p else ""
        d["duration_s"] = None      # 未读时长就是 None，不编
        d["resolution"] = ("%sx%s" % (d["width"], d["height"])) if d.get("width") else None
        out.append(d)
    return out
