"""下载任务中心 —— 把「点一下下载」变成看得见、能暂停、能恢复的真实任务。

为什么另起一个模块（而不是继续用 media.py 的 `run()`）：
  `media.run()` 是「一次调用跑完就返回」的批处理，界面只能等结果，
  **中途看不见任何一张图的进度，也停不下来**。任务书要求的是任务中心：
  每条文件一行、真实字节/速度/预计剩余、可暂停可继续可停止、重启后可恢复。

三条硬口径（对应任务书 §二）：
  1. **写临时文件 → 校验 → 原子替换**。`pending→downloading→verifying→done`，
     失败留 `failed/paused/cancelled/retry_wait`。绝不因为 HTTP 200 或文件存在就写 done。
  2. **URL 去重但引用关系不丢**。同一 URL 只下一份文件，一个任务挂多个 image_id，
     完成后把这些引用行**全部**写成 ok（角色/顺序各自保留）。
  3. **暂停是真暂停**：停派发 + 中断在传的传输（保留 .part）+ 不把半截文件标完成；
     **继续**优先用 HTTP Range 从断点续；**停止**取消未开始的、保留已完成的。

表结构是新增的（v3_dl_job / v3_dl_task / v3_dl_log），
不改任何既有表 —— 任务书禁止未预演就动生产库结构。

公开接口：
    ensure(conn)                                    建表（幂等）
    create_job(conn, pids, ...) -> job dict         建任务（不启动）
    start(db_path, job_id) -> dict                  起后台线程
    pause(conn, job_id) / resume(conn, job_id) / stop(conn, job_id)
    snapshot(conn, job_id, ...)                     界面要的全部数据
    jobs(conn, limit)                               最近任务列表
    recover(db_path)                                程序重启后把半死任务收成 paused
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request

from . import images as _images
from . import store

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
REFERER = "https://wxapp.tc.qq.com/"
CHUNK = 65536
MAX_TRIES = 3

# 任务状态
T_WAIT = "waiting"
T_DL = "downloading"
T_VERIFY = "verifying"
T_DONE = "done"
T_FAIL = "failed"
T_PAUSE = "paused"
T_CANCEL = "cancelled"
T_RETRY = "retry_wait"
T_SKIP = "skipped"

# 任务状态
J_QUEUED = "queued"
J_RUN = "running"
J_PAUSE = "paused"
J_STOPPING = "stopping"
J_STOPPED = "stopped"
J_DONE = "done"
J_FAIL = "failed"

LIVE_T = (T_WAIT, T_DL, T_VERIFY, T_RETRY)

ROLE_ORDER = {"main": 0, "detail": 1, "sku": 2, "video": 3}
ROLE_CN = {"main": "主图", "detail": "详情图", "sku": "SKU图", "video": "视频"}

_DDL = [
    """CREATE TABLE IF NOT EXISTS v3_dl_job(
        job_id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT, pack_code TEXT, kind TEXT, roles TEXT, workers INTEGER,
        state TEXT, ocr INTEGER DEFAULT 1, note TEXT,
        created_at TEXT, updated_at TEXT, started_at TEXT, finished_at TEXT,
        total_refs INTEGER DEFAULT 0, unique_urls INTEGER DEFAULT 0,
        bytes_done INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS v3_dl_task(
        task_id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER, product_id INTEGER, image_ids TEXT,
        role TEXT, url TEXT, state TEXT,
        bytes INTEGER DEFAULT 0, bytes_total INTEGER DEFAULT 0,
        tries INTEGER DEFAULT 0, max_tries INTEGER DEFAULT 3,
        part_path TEXT, path TEXT, error TEXT, retryable INTEGER DEFAULT 1,
        ocr_state TEXT, ocr_error TEXT, ocr_seg INTEGER DEFAULT 0,
        speed_bps INTEGER DEFAULT 0, resumable INTEGER DEFAULT 0,
        started_at TEXT, finished_at TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS v3_dl_log(
        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER, at TEXT, level TEXT,
        product_id INTEGER, image_id INTEGER, role TEXT, text TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_dl_task_job ON v3_dl_task(job_id, state)",
    "CREATE INDEX IF NOT EXISTS ix_dl_log_job ON v3_dl_log(job_id, log_id)",
]

# 运行时控制（同进程内：界面线程按按钮 → 引擎线程看事件）
_CTL: dict[int, dict] = {}
_RT: dict[int, dict] = {}          # 速度/ETA 的采样
_LOCK = threading.Lock()


# ---------------------------------------------------------------- 基础设施

def ensure(conn: sqlite3.Connection) -> None:
    # 调用方可能自己开了连接且没设 row_factory（命令行脚本就这样）——
    # 不设的话 create_job 里 dict(row) 会直接 TypeError。
    if getattr(conn, "row_factory", None) is None:
        conn.row_factory = sqlite3.Row
    for sql in _DDL:
        conn.execute(sql)
    conn.commit()


def _conn(db_path: str) -> sqlite3.Connection:
    c = sqlite3.connect(db_path, timeout=20.0)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA busy_timeout=20000")
        c.execute("PRAGMA journal_mode=WAL")
    except Exception:  # noqa: BLE001
        pass
    return c


def db_path_of(conn: sqlite3.Connection) -> str:
    for r in conn.execute("PRAGMA database_list"):
        if r[1] == "main":
            return r[2]
    return ""


def _cn(role: str) -> str:
    return ROLE_CN.get(role, role or "—")


def _set_job(conn: sqlite3.Connection, job_id: int, **kw) -> None:
    kw["updated_at"] = store.now()
    sets = ", ".join("%s=?" % k for k in kw)
    conn.execute("UPDATE v3_dl_job SET %s WHERE job_id=?" % sets,
                 list(kw.values()) + [job_id])
    conn.commit()


def _set_task(conn: sqlite3.Connection, task_id: int, **kw) -> None:
    kw["updated_at"] = store.now()
    sets = ", ".join("%s=?" % k for k in kw)
    conn.execute("UPDATE v3_dl_task SET %s WHERE task_id=?" % sets,
                 list(kw.values()) + [task_id])
    conn.commit()


def _log(conn: sqlite3.Connection, job_id: int, level: str, text: str,
         product_id=None, image_id=None, role=None) -> None:
    conn.execute(
        "INSERT INTO v3_dl_log(job_id, at, level, product_id, image_id, role, text)"
        " VALUES(?,?,?,?,?,?,?)",
        (job_id, store.now(), level, product_id, image_id, role, text))
    conn.commit()


def _key(db_path: str, job_id: int) -> str:
    """控制键必须带库路径 —— 只按 job_id 会在「多个库都有 job 1」时串台
    （自测里 10 个用例各用一个临时库，都会从 1 开始编号，串了就会误判"已在跑"）。"""
    return "%s#%s" % (os.path.abspath(db_path or ""), int(job_id))


def _ctl(db_path: str, job_id: int) -> dict:
    k = _key(db_path, job_id)
    with _LOCK:
        d = _CTL.get(k)
        if d is None:
            d = {"pause": threading.Event(), "stop": threading.Event(),
                 "thread": None}
            _CTL[k] = d
        return d


# ---------------------------------------------------------------- 建任务

def token_expiry(url: str) -> int | None:
    """微信 CDN 的 wdis_t = 失效时刻（Unix 秒）。其它平台返回 None。"""
    try:
        from urllib.parse import parse_qs, urlparse
        v = (parse_qs(urlparse(url).query).get("wdis_t") or [""])[0]
        return int(v) if v else None
    except Exception:  # noqa: BLE001
        return None


def _ext_of(url: str, want: str) -> str:
    e = os.path.splitext(url.split("?")[0])[1].lower()
    ok = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp") if want == "image" \
        else (".mp4", ".mov", ".m4v", ".webm")
    return e if e in ok else (".jpg" if want == "image" else ".mp4")


def create_job(conn: sqlite3.Connection, pids: list[int], roles: list[str] | None = None,
               workers: int = 4, title: str = "", pack_code: str = "", ocr: bool = True,
               skip_done: bool = True, limit: int = 0, kind: str = "download",
               ocr_only: bool = False) -> dict:
    """按商品建一个下载任务。**不启动**，由 start() 起线程（界面不卡）。

    ocr_only=True：不下载，只对「本地已有文件但还没 OCR」的图补读字。
    实现方式是把这些任务直接建成 done（带 path），OCR 阶段自然会挑中它们。
    """
    ensure(conn)
    ids = [int(p) for p in (pids or [])]
    if not ids:
        return {"ok": False, "error": "没有指定商品"}
    if ocr_only:
        return _create_ocr_job(conn, ids, workers=workers, title=title,
                              pack_code=pack_code)
    rows: list[dict] = []
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        ph = ",".join("?" * len(chunk))
        sql = ("SELECT image_id, product_id, role, sequence, original_url, dl_state,"
               " current_local_path, content_sha256 FROM v3_image WHERE product_id IN (%s)"
               % ph)
        args: list = list(chunk)
        if roles:
            sql += " AND role IN (%s)" % ",".join("?" * len(roles))
            args += list(roles)
        rows += [dict(r) for r in conn.execute(sql + " ORDER BY role, sequence, image_id", args)]

    groups: dict[str, list[dict]] = {}
    for r in rows:
        u = (r.get("original_url") or "").strip()
        if not u.startswith("http"):
            continue
        groups.setdefault(u, []).append(r)

    picked: list[tuple[str, list[dict]]] = []
    skipped = 0
    for u, refs in groups.items():
        if skip_done and all((x.get("dl_state") == "ok"
                              and x.get("current_local_path")
                              and os.path.isfile(x["current_local_path"])) for x in refs):
            skipped += len(refs)
            continue
        picked.append((u, refs))
    picked.sort(key=lambda kv: (ROLE_ORDER.get(kv[1][0]["role"], 9), kv[1][0]["sequence"] or 0))
    if limit:
        picked = picked[:limit]
    if not picked:
        return {"ok": False, "error": "没有待下载的媒体（已下载 %d 条引用，全部有文件）" % skipped}

    n_refs = sum(len(refs) for _, refs in picked)
    cur = conn.execute(
        "INSERT INTO v3_dl_job(title, pack_code, kind, roles, workers, state, ocr, note,"
        " created_at, updated_at, total_refs, unique_urls)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (title or "下载任务", pack_code, kind, json.dumps(roles or [], ensure_ascii=False),
         int(workers), J_QUEUED, 1 if ocr else 0,
         "跳过已下载 %d 条引用" % skipped if skipped else "",
         store.now(), store.now(), n_refs, len(picked)))
    job_id = int(cur.lastrowid)
    conn.executemany(
        "INSERT INTO v3_dl_task(job_id, product_id, image_ids, role, url, state,"
        " max_tries, updated_at) VALUES(?,?,?,?,?,?,?,?)",
        [(job_id, refs[0]["product_id"], json.dumps([int(x["image_id"]) for x in refs]),
          refs[0]["role"], u, T_WAIT, MAX_TRIES, store.now())
         for u, refs in picked])
    conn.commit()
    _log(conn, job_id, "info",
         "任务建立：%d 件商品 · 引用 %d 条 · 唯一 URL %d 条 · 并发 %d"
         % (len({r["product_id"] for r in rows}), n_refs, len(picked), workers))
    return {"ok": True, "job_id": job_id, "products": len({r["product_id"] for r in rows}),
            "refs": n_refs, "unique_urls": len(picked), "skipped_refs": skipped}


def _create_ocr_job(conn: sqlite3.Connection, ids: list[int], workers: int = 4,
                    title: str = "", pack_code: str = "") -> dict:
    """补跑 OCR 的任务：只挑「有本地文件、但还没成功 OCR」的图。"""
    items: list[dict] = []
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        ph = ",".join("?" * len(chunk))
        for r in conn.execute(
                "SELECT i.image_id, i.product_id, i.role, i.sequence, i.original_url,"
                " i.current_local_path FROM v3_image i"
                " LEFT JOIN v3_image_ocr o ON o.image_id=i.image_id"
                " WHERE i.product_id IN (%s)"
                "   AND i.current_local_path IS NOT NULL AND i.current_local_path<>''"
                "   AND i.role<>'video'"
                "   AND (o.image_id IS NULL OR o.state<>'ok')" % ph, list(chunk)):
            d = dict(r)
            if os.path.isfile(d["current_local_path"]):
                items.append(d)
    if not items:
        return {"ok": False, "error": "没有需要补跑 OCR 的图片（有文件的都已识别过）"}
    cur = conn.execute(
        "INSERT INTO v3_dl_job(title, pack_code, kind, roles, workers, state, ocr, note,"
        " created_at, updated_at, total_refs, unique_urls)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (title or "补跑 OCR", pack_code, "ocr", "[]", int(workers), J_QUEUED, 1,
         "只读字，不下载", store.now(), store.now(), len(items), len(items)))
    job_id = int(cur.lastrowid)
    now = store.now()
    conn.executemany(
        "INSERT INTO v3_dl_task(job_id, product_id, image_ids, role, url, state,"
        " bytes, bytes_total, path, max_tries, updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        [(job_id, int(x["product_id"]), json.dumps([int(x["image_id"])]), x["role"],
          x["original_url"], T_DONE, os.path.getsize(x["current_local_path"]),
          os.path.getsize(x["current_local_path"]), x["current_local_path"], 1, now)
         for x in items])
    conn.commit()
    _log(conn, job_id, "info", "OCR 任务建立：%d 张图（已有文件、还没读字的）" % len(items))
    return {"ok": True, "job_id": job_id, "products": len(ids), "refs": len(items),
            "unique_urls": len(items), "skipped_refs": 0}

def start(db_path: str, job_id: int | None = None, conn: sqlite3.Connection | None = None) -> dict:
    """起后台线程跑任务；同一任务重复调用不会起第二个线程。"""
    if not db_path and conn is not None:
        db_path = db_path_of(conn)
    if job_id is None:
        row = conn.execute("SELECT job_id FROM v3_dl_job WHERE state IN (?,?)"
                           " ORDER BY job_id DESC LIMIT 1", (J_QUEUED, J_PAUSE)) \
            if conn is not None else None
        if not row:
            return {"ok": False, "error": "没有待启动的任务"}
        job_id = int(row[0] if not isinstance(row, sqlite3.Row) else row["job_id"])
    ctl = _ctl(db_path, int(job_id))
    th = ctl.get("thread")
    if th is not None and th.is_alive():
        return {"ok": True, "job_id": int(job_id), "already": True,
                "note": "这个任务已经在跑"}
    ctl["pause"].clear()
    ctl["stop"].clear()
    th = threading.Thread(target=_run, args=(db_path, int(job_id)),
                          name="dl-job-%s" % job_id, daemon=True)
    ctl["thread"] = th
    th.start()
    return {"ok": True, "job_id": int(job_id), "started": True}


# ---------------------------------------------------------------- 控制

def pause(conn: sqlite3.Connection, job_id: int) -> dict:
    ctl = _ctl(db_path_of(conn), int(job_id))
    ctl["pause"].set()
    _set_job(conn, int(job_id), state=J_PAUSE)
    _log(conn, int(job_id), "warn", "收到「暂停」：停派发新任务 + 中断在传文件（保留断点）")
    return {"ok": True, "state": J_PAUSE}


def resume(conn: sqlite3.Connection, job_id: int) -> dict:
    ctl = _ctl(db_path_of(conn), int(job_id))
    ctl["pause"].clear()
    ctl["stop"].clear()
    n = conn.execute("UPDATE v3_dl_task SET state=?, updated_at=? WHERE job_id=?"
                     " AND state IN (?,?)",
                     (T_WAIT, store.now(), int(job_id), T_PAUSE, T_RETRY)).rowcount
    _set_job(conn, int(job_id), state=J_RUN)
    _log(conn, int(job_id), "info", "收到「继续」：%d 个暂停/等待重试的任务回到队列" % n)
    th = ctl.get("thread")
    out = {"ok": True, "requeued": n}
    if th is None or not th.is_alive():
        out.update(start(db_path_of(conn), int(job_id)))
    return out


def stop(conn: sqlite3.Connection, job_id: int) -> dict:
    ctl = _ctl(db_path_of(conn), int(job_id))
    ctl["stop"].set()
    _set_job(conn, int(job_id), state=J_STOPPING)
    n = conn.execute("UPDATE v3_dl_task SET state=?, updated_at=? WHERE job_id=?"
                     " AND state IN (?,?,?)",
                     (T_CANCEL, store.now(), int(job_id), T_WAIT, T_RETRY, T_PAUSE)).rowcount
    _log(conn, int(job_id), "warn",
         "收到「停止」：取消 %d 个未开始的任务；在传文件会安全中断，已完成的保留" % n)
    return {"ok": True, "cancelled": n}


def recover(db_path: str) -> dict:
    """程序重启后：把「上次还在 downloading」的任务收成 paused，可一键继续。"""
    if not db_path or not os.path.isfile(db_path):
        return {"ok": False, "error": "库不存在"}
    c = _conn(db_path)
    try:
        ensure(c)
        jobs = [dict(r) for r in c.execute(
            "SELECT job_id, state FROM v3_dl_job WHERE state IN (?,?,?)",
            (J_RUN, J_STOPPING, J_QUEUED))]
        out = []
        for j in jobs:
            jid = int(j["job_id"])
            n = c.execute("UPDATE v3_dl_task SET state=?, updated_at=? WHERE job_id=?"
                          " AND state IN (?,?)",
                          (T_PAUSE, store.now(), jid, T_DL, T_VERIFY)).rowcount
            c.execute("UPDATE v3_dl_job SET state=?, updated_at=? WHERE job_id=?",
                      (J_PAUSE, store.now(), jid))
            c.commit()
            _log(c, jid, "warn",
                 "程序重启：%d 个传了一半的文件已置为暂停（保留 .part，可续传）" % n)
            out.append({"job_id": jid, "paused_tasks": n})
        return {"ok": True, "recovered": out}
    finally:
        c.close()


# ---------------------------------------------------------------- 单条下载

class _Abort(Exception):
    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind          # pause | stop


def _stream(url: str, part: str, want: str, ctl: dict, on_progress) -> dict:
    """流式下载到 part 文件。支持 Range 续传；每块都看一次暂停/停止信号。"""
    os.makedirs(os.path.dirname(part), exist_ok=True)
    headers = {"User-Agent": UA, "Referer": REFERER}
    base = os.path.getsize(part) if os.path.isfile(part) else 0
    if base > 0:
        headers["Range"] = "bytes=%d-" % base
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as e:
        if e.code == 416 and base > 0:      # 断点已到底：当作完成，交给校验
            return {"ok": True, "bytes": base, "total": base, "resumed": True,
                    "ctype": ""}
        raise
    code = getattr(resp, "status", 200)
    ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    clen = resp.headers.get("Content-Length")
    clen = int(clen) if (clen or "").isdigit() else 0
    resume = (code == 206 and base > 0)
    mode = "ab" if resume else "wb"
    done = base if resume else 0
    total = (base + clen) if resume and clen else (clen or 0)
    if not resume:
        base = 0
    t0 = time.time()
    last_emit = 0.0
    with open(part, mode) as f:
        while True:
            if ctl["stop"].is_set():
                raise _Abort("stop")
            if ctl["pause"].is_set():
                raise _Abort("pause")
            buf = resp.read(CHUNK)
            if not buf:
                break
            f.write(buf)
            done += len(buf)
            now = time.time()
            if now - last_emit >= 0.25:
                last_emit = now
                sp = int(done / max(0.001, now - t0))
                try:
                    on_progress(done, total, sp)
                except Exception:  # noqa: BLE001
                    pass
    return {"ok": True, "bytes": done, "total": total, "resumed": resume,
            "ctype": ctype, "secs": time.time() - t0}


def _verify(path: str, want: str, recv: int, total: int) -> dict:
    """不只信 HTTP 200：字节数 / 空文件 / 能否解码 / 视频容器 / 指纹。"""
    if not os.path.isfile(path):
        return {"ok": False, "hint": "临时文件不见了"}
    size = os.path.getsize(path)
    if size <= 0:
        return {"ok": False, "hint": "文件为空（0 字节）"}
    if total and size != total:
        return {"ok": False, "hint": "字节数不符：收到 %d / 应有 %d" % (size, total),
                "retryable": True}
    lo = 512 if want == "image" else 4096
    if size < lo:
        return {"ok": False, "hint": "文件太小（%d 字节），不像真%s" % (size, want),
                "retryable": True}
    if want == "image":
        try:
            from PIL import Image
            with Image.open(path) as im:
                im.verify()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "hint": "图片解不开：%s: %s" % (type(e).__name__, e),
                    "retryable": True}
    else:
        with open(path, "rb") as f:
            head = f.read(64)
        if b"ftyp" not in head:
            return {"ok": False, "hint": "文件头不像 mp4（%r）" % head[:24],
                    "retryable": True}
    return {"ok": True, "bytes": size, "sha256": _images.sha256_file(path)}


def _do_task(db_path: str, task_id: int, ctl: dict) -> dict:
    """一个任务 = 一个唯一 URL（可能被多条引用共享）。在自己的连接里跑。"""
    conn = _conn(db_path)
    try:
        t = conn.execute("SELECT * FROM v3_dl_task WHERE task_id=?", (task_id,)).fetchone()
        if not t:
            return {"ok": False, "error": "任务不存在"}
        t = dict(t)
        if t["state"] in (T_CANCEL, T_DONE):
            return {"ok": False, "skipped": True}
        url = t["url"]
        want = "video" if t["role"] == "video" else "image"
        _set_task(conn, task_id, state=T_DL, started_at=store.now(),
                  tries=int(t["tries"] or 0))
        _log(conn, int(t["job_id"]), "info",
             "开始下载 %s #%s" % (_cn(t["role"]), task_id),
             t["product_id"], None, t["role"])

        # 过期链接：先判后请求，省一次必死的请求，也免得被算成"网络失败"
        exp = token_expiry(url)
        if exp and exp <= int(time.time()):
            hint = ("链接已过期（wdis_t=%d）· 需重新采集，重试无用" % exp)
            _set_task(conn, task_id, state=T_FAIL, error=hint, retryable=0,
                      finished_at=store.now())
            _fail_refs(conn, t, hint)
            _log(conn, int(t["job_id"]), "err",
                 "跳过：%s" % hint, t["product_id"], None, t["role"])
            return {"ok": False, "expired": True, "hint": hint}

        # 2026-10-26：落到 <商品ID>\<角色>\ 子目录（主人要求 SKU 图单独一个文件夹）
        d = store.role_dir(int(t["product_id"]), t["role"])
        sha12 = hashlib.sha1(url.encode()).hexdigest()[:12]
        final = os.path.join(d, "%s_%s%s" % (want, sha12, _ext_of(url, want)))
        part = final + ".part"

        if os.path.isfile(final) and os.path.getsize(final) > 1024:
            v = _verify(final, want, os.path.getsize(final), 0)
            if v.get("ok"):
                _task_ok(conn, t, final, v)
                _log(conn, int(t["job_id"]), "ok", "已有完整文件，直接复用",
                     t["product_id"], None, t["role"])
                return {"ok": True, "already": True, "bytes": v["bytes"]}

        last = {"t": 0.0, "b": 0}

        def on_progress(done, total, sp):
            now = time.time()
            if now - last["t"] < 0.6 and done != total:
                return
            last["t"] = now
            last["b"] = done
            _set_task(conn, task_id, bytes=int(done), bytes_total=int(total or 0),
                      speed_bps=int(sp))

        try:
            r = _stream(url, part, want, ctl, on_progress)
        except _Abort as a:
            size = os.path.getsize(part) if os.path.isfile(part) else 0
            st = T_PAUSE if a.kind == "pause" else T_CANCEL
            _set_task(conn, task_id, state=st, bytes=size, speed_bps=0,
                      error="已%s（断点 %.1f KB 保留）" % ("暂停" if a.kind == "pause" else "停止",
                                                       size / 1024.0))
            _log(conn, int(t["job_id"]), "warn",
                 "传输中断（%s），保留断点" % ("暂停" if a.kind == "pause" else "停止"),
                 t["product_id"], None, t["role"])
            return {"ok": False, "aborted": a.kind}
        except Exception as e:  # noqa: BLE001
            msg = "%s: %s" % (type(e).__name__, e)
            return _retry_or_fail(conn, t, msg, retryable=True)

        _set_task(conn, task_id, state=T_VERIFY, bytes=int(r["bytes"]),
                  bytes_total=int(r.get("total") or 0), resumable=1 if r.get("resumed") else 0)
        v = _verify(part, want, int(r["bytes"]), int(r.get("total") or 0))
        if not v.get("ok"):
            return _retry_or_fail(conn, t, v.get("hint") or "校验失败",
                                  retryable=bool(v.get("retryable", True)))
        try:
            os.replace(part, final)      # 原子替换：正式文件永远是完整的
        except Exception as e:  # noqa: BLE001
            return _retry_or_fail(conn, t, "落盘失败：%s" % e, retryable=True)
        _task_ok(conn, t, final, v, total=int(r.get("total") or 0))
        _log(conn, int(t["job_id"]), "ok",
             "完成 %s %.1f KB%s" % (_cn(t["role"]), v["bytes"] / 1024.0,
                                   "（断点续传）" if r.get("resumed") else ""),
             t["product_id"], None, t["role"])
        return {"ok": True, "bytes": v["bytes"], "sha256": v["sha256"], "path": final}
    except Exception as e:  # noqa: BLE001
        try:
            _set_task(conn, task_id, state=T_FAIL, error="线程异常：%s: %s"
                      % (type(e).__name__, e), finished_at=store.now())
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "hint": "线程异常：%s" % e}
    finally:
        conn.close()


def _task_ok(conn, t: dict, final: str, v: dict, total: int = 0) -> None:
    ids = json.loads(t["image_ids"] or "[]")
    now = store.now()
    for iid in ids:
        conn.execute(
            "UPDATE v3_image SET dl_state='ok', current_local_path=?, content_sha256=?,"
            " download_hint='', updated_at=? WHERE image_id=?",
            (final, v.get("sha256") or "", now, int(iid)))
    conn.commit()
    # ★ 服务器没给 Content-Length 时，bytes_total 保持 0（=总大小未知），
    #   不用"实际收到的字节"倒推成 100% —— 任务书明令不许伪造百分比。
    _set_task(conn, int(t["task_id"]), state=T_DONE, path=final, error="",
              bytes=int(v.get("bytes") or 0), bytes_total=int(total or 0),
              speed_bps=0, finished_at=now)
    jid = int(t["job_id"])
    conn.execute("UPDATE v3_dl_job SET bytes_done = bytes_done + ? WHERE job_id=?",
                 (int(v.get("bytes") or 0), jid))
    conn.commit()


def _fail_refs(conn, t: dict, hint: str) -> None:
    ids = json.loads(t["image_ids"] or "[]")
    now = store.now()
    for iid in ids:
        conn.execute("UPDATE v3_image SET dl_state='fail', download_hint=?, updated_at=?"
                     " WHERE image_id=?", (hint[:200], now, int(iid)))
    conn.commit()


def _retry_or_fail(conn, t: dict, msg: str, retryable: bool = True) -> dict:
    tries = int(t["tries"] or 0) + 1
    max_tries = int(t["max_tries"] or MAX_TRIES)
    jid = int(t["job_id"])
    if retryable and tries < max_tries:
        back = min(30, 2 ** tries)
        # ★ 这里只登记 retry_wait，**不自己翻回 waiting**：
        #   翻了的话外层的"重试轮"永远看不到待重试项，任务会被判成收尾（实测踩过）。
        _set_task(conn, int(t["task_id"]), state=T_RETRY, tries=tries,
                  error="第 %d 次失败：%s（%d 秒后重试）" % (tries, msg, back))
        _log(conn, jid, "warn", "失败待重试：%s" % msg[:120], t["product_id"], None, t["role"])
        return {"ok": False, "hint": msg, "retry": True}
    _set_task(conn, int(t["task_id"]), state=T_FAIL, tries=tries, error=msg,
              retryable=1 if retryable else 0, finished_at=store.now())
    _fail_refs(conn, t, msg)
    _log(conn, jid, "err", "最终失败：%s" % msg[:160], t["product_id"], None, t["role"])
    return {"ok": False, "hint": msg}


# ---------------------------------------------------------------- 主循环

def _run(db_path: str, job_id: int) -> None:
    conn = _conn(db_path)
    ctl = _ctl(db_path, job_id)
    try:
        j = conn.execute("SELECT * FROM v3_dl_job WHERE job_id=?", (job_id,)).fetchone()
        if not j:
            return
        j = dict(j)
        _set_job(conn, job_id, state=J_RUN, started_at=j["started_at"] or store.now())
        workers = max(1, min(16, int(j["workers"] or 4)))
        rounds = 0
        while rounds < 20:
            rounds += 1
            if ctl["stop"].is_set():
                break
            todo = [int(r["task_id"]) for r in conn.execute(
                "SELECT task_id FROM v3_dl_task WHERE job_id=? AND state=?"
                " ORDER BY task_id", (job_id, T_WAIT))]
            if todo:
                _dispatch(conn, job_id, todo, workers, ctl, db_path)
                if ctl["stop"].is_set() or ctl["pause"].is_set():
                    break
                continue
            # 没有待派任务了：看看有没有等着重试的 → 退避后放回队列再来一轮
            n_retry = conn.execute("SELECT COUNT(*) FROM v3_dl_task WHERE job_id=?"
                                   " AND state=?", (job_id, T_RETRY)).fetchone()[0]
            if not n_retry:
                break
            back = min(30, 2 ** rounds)
            _log(conn, job_id, "info", "%d 个任务等待重试，%d 秒后开始第 %d 轮"
                 % (n_retry, back, rounds))
            for _ in range(int(back * 5)):
                if ctl["stop"].is_set() or ctl["pause"].is_set():
                    break
                time.sleep(0.2)
            if ctl["stop"].is_set() or ctl["pause"].is_set():
                break
            conn.execute("UPDATE v3_dl_task SET state=?, updated_at=? WHERE job_id=?"
                         " AND state=?", (T_WAIT, store.now(), job_id, T_RETRY))
            conn.commit()
        if ctl["pause"].is_set():
            _set_job(conn, job_id, state=J_PAUSE)
        elif ctl["stop"].is_set():
            _set_job(conn, job_id, state=J_STOPPED, finished_at=store.now())
        else:
            # ---- OCR 阶段（下载阶段的图才读，避免整库重扫）
            if int(j["ocr"] or 0):
                _phase_ocr(conn, job_id, ctl)
            if ctl["pause"].is_set():
                _set_job(conn, job_id, state=J_PAUSE)
            elif ctl["stop"].is_set():
                _set_job(conn, job_id, state=J_STOPPED, finished_at=store.now())
            else:
                _set_job(conn, job_id, state=J_DONE, finished_at=store.now())
        c = {k: v for k, v in _counts(conn, job_id).items()}
        _log(conn, job_id, "info",
             "任务收尾：完成 %d · 失败 %d · 暂停 %d · 取消 %d · 跳过 %d"
             % (c.get(T_DONE, 0), c.get(T_FAIL, 0), c.get(T_PAUSE, 0),
                c.get(T_CANCEL, 0), c.get(T_SKIP, 0)))
    except Exception as e:  # noqa: BLE001
        try:
            _set_job(conn, job_id, state=J_FAIL, note="引擎异常：%s: %s"
                     % (type(e).__name__, e))
            _log(conn, job_id, "err", "引擎异常退出：%s: %s" % (type(e).__name__, e))
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _dispatch(conn, job_id: int, task_ids: list[int], workers: int, ctl: dict,
              db_path: str) -> None:
    """有上限的并发派发；每 0.3 秒回来看一次暂停/停止信号。"""
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
    it = iter(task_ids)
    futs = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        while True:
            if ctl["stop"].is_set() or ctl["pause"].is_set():
                break
            while len(futs) < workers:
                tid = next(it, None)
                if tid is None:
                    break
                futs[ex.submit(_do_task, db_path, tid, ctl)] = tid
            if not futs:
                break
            done, _ = wait(list(futs), timeout=0.3, return_when=FIRST_COMPLETED)
            for f in done:
                futs.pop(f, None)
        for f in list(futs):
            f.cancel()


def _phase_ocr(conn: sqlite3.Connection, job_id: int, ctl: dict) -> None:
    # ★ 视频不送 OCR（实测会白跑一趟并记「0 段文字」，看起来像失败）
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM v3_dl_task WHERE job_id=? AND state=? AND ocr_state IS NULL"
        " AND role<>'video' ORDER BY role, task_id", (job_id, T_DONE))]
    if not rows:
        return
    _log(conn, job_id, "info", "开始 OCR：%d 个已下载文件" % len(rows))
    for t in rows:
        if ctl["stop"].is_set() or ctl["pause"].is_set():
            return
        ids = json.loads(t["image_ids"] or "[]")
        # 同一文件可能被多条引用共享 —— 每条引用各自留证据（4.1 按图片行看）
        ok = 0
        seg = 0
        err = ""
        for iid in ids:
            r = conn.execute("SELECT * FROM v3_image WHERE image_id=?", (int(iid),)).fetchone()
            if not r:
                continue
            try:
                o = _images.ocr_image(conn, dict(r))
            except Exception as e:  # noqa: BLE001
                o = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            if o.get("ok") or o.get("state") == "ok":
                ok += 1
                seg += int(o.get("n_seg") or o.get("segments") or 0)
            else:
                err = str(o.get("error") or o.get("hint") or "OCR 失败")[:150]
        st = "ok" if ok else "fail"
        _set_task(conn, int(t["task_id"]), ocr_state=st, ocr_error=err, ocr_seg=seg)
        _log(conn, job_id, "ok" if ok else "err",
             "OCR %s：%d 条引用 · %d 段文字" % (_cn(t["role"]), ok, seg)
             if ok else "OCR 失败：%s" % err,
             t["product_id"], None, t["role"])


def _counts(conn: sqlite3.Connection, job_id: int) -> dict:
    out = {k: 0 for k in (T_WAIT, T_DL, T_VERIFY, T_DONE, T_FAIL, T_PAUSE,
                          T_CANCEL, T_RETRY, T_SKIP)}
    for r in conn.execute("SELECT state, COUNT(*) n FROM v3_dl_task WHERE job_id=?"
                          " GROUP BY state", (job_id,)):
        out[str(r["state"])] = int(r["n"])
    return out


# ---------------------------------------------------------------- 给界面看

def snapshot(conn: sqlite3.Connection, job_id: int = 0, task_limit: int = 600,
             log_limit: int = 200, f_pid: int = 0, f_role: str = "",
             f_state: str = "", q: str = "") -> dict:
    ensure(conn)
    if not job_id:
        row = conn.execute("SELECT job_id FROM v3_dl_job ORDER BY job_id DESC LIMIT 1").fetchone()
        if not row:
            return {"ok": True, "job": None, "tasks": [], "logs": [], "jobs": jobs(conn, 20)}
        job_id = int(row["job_id"])
    jr = conn.execute("SELECT * FROM v3_dl_job WHERE job_id=?", (int(job_id),)).fetchone()
    if not jr:
        return {"ok": False, "error": "任务不存在：%s" % job_id}
    job = dict(jr)

    cnt = _counts(conn, int(job_id))
    # 按角色统计（引用级）
    roles: dict[str, dict] = {}
    for r in conn.execute(
            "SELECT role, state, COUNT(*) n, SUM(bytes) b, SUM(bytes_total) tb"
            " FROM v3_dl_task WHERE job_id=? GROUP BY role, state", (int(job_id),)):
        d = roles.setdefault(str(r["role"]), {"total": 0, "done": 0, "failed": 0,
                                              "bytes": 0, "bytes_total": 0})
        n = int(r["n"])
        d["total"] += n
        if r["state"] == T_DONE:
            d["done"] += n
        if r["state"] in (T_FAIL, T_CANCEL):
            d["failed"] += n
        d["bytes"] += int(r["b"] or 0)
        d["bytes_total"] += int(r["tb"] or 0)

    done_n = cnt.get(T_DONE, 0)
    total_n = max(1, sum(cnt.values()))
    bytes_done = int(job["bytes_done"] or 0)
    bytes_known = sum(d["bytes_total"] for d in roles.values() if d["bytes_total"])
    # 速度/ETA：同进程内用两次快照相减；跨进程只有总量
    now = time.time()
    rt = _RT.setdefault(int(job_id), {"b": bytes_done, "t": now, "sp": 0})
    if now - rt["t"] >= 0.4:
        sp = (bytes_done - rt["b"]) / max(0.001, now - rt["t"])
        rt["sp"] = int(sp * 0.5 + rt["sp"] * 0.5) if rt["sp"] else int(sp)
        rt["b"] = bytes_done
        rt["t"] = now
    speed = max(0, int(rt["sp"]))
    live = cnt.get(T_DL, 0) + cnt.get(T_WAIT, 0) + cnt.get(T_RETRY, 0)
    eta = None
    if speed > 0 and bytes_known > 0 and bytes_known > bytes_done:
        eta = int((bytes_known - bytes_done) / speed)

    where = ["job_id=?"]
    args: list = [int(job_id)]
    if f_pid:
        where.append("product_id=?")
        args.append(int(f_pid))
    if f_role:
        where.append("role=?")
        args.append(f_role)
    if f_state:
        where.append("state=?")
        args.append(f_state)
    if q:
        where.append("(url LIKE ? OR error LIKE ? OR path LIKE ?)")
        args += ["%%%s%%" % q] * 3
    w = " AND ".join(where)
    tasks = [dict(r) for r in conn.execute(
        "SELECT task_id, product_id, image_ids, role, url, state, bytes, bytes_total,"
        " tries, max_tries, path, error, ocr_state, ocr_error, ocr_seg, speed_bps,"
        " resumable, retryable, started_at, finished_at FROM v3_dl_task WHERE %s"
        " ORDER BY task_id LIMIT ?" % w,
        args + [int(task_limit)])]
    for t in tasks:
        t["role_cn"] = _cn(t["role"])
        try:
            t["image_id"] = (json.loads(t["image_ids"] or "[]") or [None])[0]
            t["refs"] = len(json.loads(t["image_ids"] or "[]"))
        except Exception:  # noqa: BLE001
            t["image_id"], t["refs"] = None, 0
        tt = t.get("bytes_total") or 0
        t["pct"] = round(min(100.0, t["bytes"] * 100.0 / tt), 1) if tt else None
        # 每条文件的已用时间 / 剩余时间（视频监控要用）
        el = 0
        if t.get("started_at"):
            try:
                st = time.mktime(time.strptime(str(t["started_at"])[:19], "%Y-%m-%d %H:%M:%S"))
                end = (time.mktime(time.strptime(str(t["finished_at"])[:19],
                                                 "%Y-%m-%d %H:%M:%S"))
                       if t.get("finished_at") else time.time())
                el = max(0, int(end - st))
            except Exception:  # noqa: BLE001
                el = 0
        t["elapsed_sec"] = el
        b = int(t.get("bytes") or 0)
        sp = int(t.get("speed_bps") or 0)
        if t.get("state") == T_DL and sp > 0 and tt > b:
            t["task_eta_sec"] = int((tt - b) / sp)
        else:
            t["task_eta_sec"] = None

    # 视频单独一块（主人点名：日志系统首先要能看到视频下载状态）
    vid = {"total": 0, "done": 0, "failed": 0, "live": 0, "bytes": 0,
           "bytes_total": 0, "speed": 0, "elapsed": 0, "eta": None,
           "unknown_total": 0}
    for t in [x for x in tasks if x["role"] == "video"]:
        vid["total"] += 1
        if t["state"] == T_DONE:
            vid["done"] += 1
        elif t["state"] in (T_FAIL, T_CANCEL):
            vid["failed"] += 1
        elif t["state"] in (T_DL, T_VERIFY):
            vid["live"] += 1
        vid["bytes"] += int(t.get("bytes") or 0)
        if t.get("bytes_total"):
            vid["bytes_total"] += int(t["bytes_total"])
        else:
            vid["unknown_total"] += 1
        vid["speed"] += int(t.get("speed_bps") or 0) if t["state"] == T_DL else 0
        vid["elapsed"] = max(vid["elapsed"], t["elapsed_sec"] or 0)
    if vid["speed"] > 0 and vid["bytes_total"] > vid["bytes"]:
        vid["eta"] = int((vid["bytes_total"] - vid["bytes"]) / vid["speed"])

    logs = [dict(r) for r in conn.execute(
        "SELECT at, level, product_id, role, text FROM v3_dl_log WHERE job_id=?"
        " ORDER BY log_id DESC LIMIT ?", (int(job_id), int(log_limit)))][::-1]

    return {"ok": True, "job": job,
            "header": {"job_id": int(job_id), "title": job["title"],
                       "pack_code": job["pack_code"], "state": job["state"],
                       "products": len({t["product_id"] for t in tasks}) if tasks else None,
                       "refs": int(job["total_refs"] or 0),
                       "unique_urls": int(job["unique_urls"] or 0),
                       "workers": int(job["workers"] or 0),
                       "counts": cnt, "roles": roles,
                       "finished": done_n, "total": total_n,
                       "live": live,
                       "pct": round(done_n * 100.0 / total_n, 1),
                       "bytes_done": bytes_done, "bytes_known": bytes_known,
                       "speed_bps": speed, "eta_sec": eta,
                       "eta_text": ("暂不可估算" if eta is None else
                                    ("%d 分 %d 秒" % (eta // 60, eta % 60) if eta >= 60
                                     else "%d 秒" % eta)),
                       "ocr": bool(job["ocr"]), "note": job["note"],
                       "started_at": job["started_at"], "finished_at": job["finished_at"]},
            "video": vid,
            "tasks": tasks, "logs": logs, "jobs": jobs(conn, 20)}


def jobs(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    ensure(conn)
    out = []
    for r in conn.execute("SELECT * FROM v3_dl_job ORDER BY job_id DESC LIMIT ?", (limit,)):
        d = dict(r)
        c = _counts(conn, int(d["job_id"]))
        d["counts"] = c
        tot = max(1, sum(c.values()))
        d["pct"] = round(c.get(T_DONE, 0) * 100.0 / tot, 1)
        d["mb"] = round(int(d["bytes_done"] or 0) / 1048576.0, 2)
        out.append(d)
    return out


def task_file(conn: sqlite3.Connection, task_id: int) -> dict:
    r = conn.execute("SELECT * FROM v3_dl_task WHERE task_id=?", (int(task_id),)).fetchone()
    if not r:
        return {"ok": False, "error": "任务不存在"}
    d = dict(r)
    p = d.get("path") or ""
    d["exists"] = bool(p and os.path.isfile(p))
    d["ok"] = True
    return d
