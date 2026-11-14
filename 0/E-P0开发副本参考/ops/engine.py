# -*- coding: utf-8 -*-
"""上货台 7.0 · 运营引擎（P0）

只做四件事：
    1) 暴露 9192 的只读查询 API（商品 / 视频 / 任务 / 指标 / 账号）
    2) 提供 9192 的健康检查
    3) 维护 ops_task 队列（入队 / 领取 / 续租 / 完成 / 失败 / 取消）
    4) 后台回收过期租约 —— 引擎被杀掉再启起来，被中断的任务能自己回到队列

硬边界（P0）
    * 只监听 127.0.0.1
    * 只写 ops_* 表；v3_* 一律只读（store.py 用 mode=ro 打开）
    * 不调用任何平台 API、不启动浏览器、不发真实发布动作
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import config, queue, store, worker

START_TS = time.time()
RECOVERY_TOTAL = {"requeued": 0, "failed": 0, "passes": 0}
STOP = threading.Event()
LOCK_KEY = "ops_engine:%d" % config.port()
INSTANCE_ID = "e-" + uuid.uuid4().hex[:8]
LOG_PATH = None

# 完整性检查很贵（111MB 全库，冷缓存实测 40 秒），必须后台算、请求只读缓存
_INTEGRITY = {"value": None, "at": 0.0, "lock": threading.Lock(), "ttl": 900}
# 磁盘全量核对也贵（1.2 万文件），缓存 2 分钟
_RECON = {"value": None, "at": 0.0, "lock": threading.Lock(), "ttl": 120}
# 单实例：用操作系统文件锁（进程一死由 OS 自动释放，不会留下"僵尸锁"）
_FILE_LOCK = {"fp": None}


def log(msg: str) -> None:
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    try:
        print(line, flush=True)
    except Exception:                                        # noqa: BLE001
        pass
    if LOG_PATH:
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:                                    # noqa: BLE001
            pass


def _con():
    return queue.connect()


def integrity(refresh: bool = False) -> str:
    """完整性检查：后台线程算一次，请求路径只读缓存（绝不阻塞）。

    实测：冷缓存 quick_check 39.9s / 热缓存 0.3s —— 所以只有启动后台算一次是对的。
    """
    with _INTEGRITY["lock"]:
        fresh = (time.time() - _INTEGRITY["at"]) < _INTEGRITY["ttl"]
        if refresh or _INTEGRITY["value"] is None or not fresh:
            t0 = time.time()
            con = _con()
            try:
                _INTEGRITY["value"] = con.execute("PRAGMA quick_check").fetchone()[0]
            except Exception as e:                           # noqa: BLE001
                _INTEGRITY["value"] = "error: %s" % e
            finally:
                con.close()
            _INTEGRITY["at"] = time.time()
            log("完整性检查 %.1fs -> %s" % (_INTEGRITY["at"] - t0, _INTEGRITY["value"]))
        return _INTEGRITY["value"]


def integrity_loop():
    integrity(True)


def acquire_file_lock():
    """Windows 上用字节范围锁做单实例：进程退出（哪怕被 kill）由 OS 释放，不会僵尸。"""
    try:
        import msvcrt
    except ImportError:                                      # 非 Windows：不做文件锁
        return "n/a"
    p = config.DEV_ROOT / "data" / "ops_engine.lock"
    p.parent.mkdir(parents=True, exist_ok=True)
    fp = open(p, "a+b")
    try:
        fp.seek(0)
        msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        fp.close()
        return None
    try:
        fp.seek(0)
        fp.truncate()
        fp.write(("pid=%d at=%s\n" % (os.getpid(), time.strftime("%Y-%m-%d %H:%M:%S"))).encode())
        fp.flush()
    except Exception:                                        # noqa: BLE001
        pass
    _FILE_LOCK["fp"] = fp
    return p


def release_file_lock():
    fp = _FILE_LOCK.get("fp")
    if not fp:
        return
    try:
        import msvcrt
        fp.seek(0)
        msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)
    except Exception:                                        # noqa: BLE001
        pass
    try:
        fp.close()
    except Exception:                                        # noqa: BLE001
        pass
    _FILE_LOCK["fp"] = None


# --------------------------------------------------------------------------- #
# 后台线程
# --------------------------------------------------------------------------- #
def reaper_loop():
    while not STOP.is_set():
        try:
            con = _con()
            try:
                r = queue.recover_expired(con, actor="reaper")
            finally:
                con.close()
            RECOVERY_TOTAL["passes"] += 1
            if r["requeued"] or r["failed"]:
                RECOVERY_TOTAL["requeued"] += len(r["requeued"])
                RECOVERY_TOTAL["failed"] += len(r["failed"])
                log("reaper: requeued=%s failed=%s" % (r["requeued"], r["failed"]))
        except Exception as e:                               # noqa: BLE001
            log("reaper error: %s" % e)
        STOP.wait(config.recover_interval())


def lock_loop():
    """单实例：引擎进程活着就一直续期这把锁，避免双开抢任务。"""
    while not STOP.is_set():
        try:
            con = _con()
            try:
                queue.acquire_lock(con, LOCK_KEY, INSTANCE_ID, ttl=30)
            finally:
                con.close()
        except Exception:                                    # noqa: BLE001
            pass
        STOP.wait(10)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "OpsEngine/" + config.ENGINE_VERSION
    protocol_version = "HTTP/1.1"

    # ---- 基础 ----
    def log_message(self, fmt, *args):                        # noqa: A003
        log("%s %s" % (self.address_string(), fmt % args))

    def _send(self, obj, code: int = 200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, code: int = 200):
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:                                    # noqa: BLE001
            return {}

    def _local_only(self) -> bool:
        return self.client_address[0] in ("127.0.0.1", "::1")

    # ---- 路由 ----
    def do_GET(self):                                        # noqa: N802
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        try:
            if p == "/":
                return self._send_html(INDEX_HTML)
            if p == "/api/ops/health":
                return self._send(self.api_health())
            if p == "/api/ops/products":
                return self._send(self.with_store(
                    lambda con: store.products(con,
                                               limit=int(q.get("limit", ["50"])[0]),
                                               q=(q.get("q") or [None])[0],
                                               status=(q.get("status") or [None])[0])))
            if p == "/api/ops/videos":
                return self._send(self.with_store(
                    lambda con: store.videos(con,
                                             limit=int(q.get("limit", ["50"])[0]),
                                             product_id=(int(q["product_id"][0])
                                                         if q.get("product_id") else None))))
            if p == "/api/ops/videos/stats":
                return self._send(self.with_store(store.video_stats))
            if p == "/api/ops/tasks":
                st = (q.get("state") or [None])[0]
                lim = int(q.get("limit", ["50"])[0])
                return self._send(self.with_store(lambda con: queue.list_tasks(con, st, lim)))
            if p == "/api/ops/tasks/stats":
                return self._send(self.with_store(queue.stats))
            if p == "/api/ops/task":
                tid = (q.get("task_id") or [""])[0]
                return self._send(self.with_store(lambda con: queue.get_task(con, tid)))
            if p == "/api/ops/metrics":
                return self._send(self.with_store(
                    lambda con: store.metrics(con, (q.get("day") or [None])[0],
                                              (q.get("platform") or [None])[0])))
            if p == "/api/ops/accounts":
                return self._send(self.with_store(store.accounts))
            if p == "/api/ops/errors":
                return self._send(self.with_store(
                    lambda con: store.errors(con, int(q.get("limit", ["50"])[0]))))
            if p == "/api/ops/tables":
                return self._send(self.with_store(store.table_counts))
            if p == "/api/ops/recovery":
                return self._send({"instance": INSTANCE_ID,
                                   "interval_s": config.recover_interval(),
                                   "totals": RECOVERY_TOTAL})
            return self._send({"error": "no such route", "path": p}, 404)
        except Exception as e:                               # noqa: BLE001
            log("GET %s error: %s" % (p, traceback.format_exc(limit=3)))
            return self._send({"error": "%s: %s" % (type(e).__name__, e)}, 500)

    def do_POST(self):                                       # noqa: N802
        if not self._local_only():
            return self._send({"error": "loopback only"}, 403)
        u = urlparse(self.path)
        p = u.path
        b = self._body()
        try:
            if p == "/api/ops/task/enqueue":
                con = _con()
                try:
                    tid, created = queue.enqueue(
                        con, b.get("kind") or "probe.noop", b.get("payload"),
                        platform=b.get("platform"), product_id=b.get("product_id"),
                        dedupe_key=b.get("dedupe_key"),
                        priority=int(b.get("priority", 100)),
                        max_attempts=int(b.get("max_attempts", 3)),
                        delay=int(b.get("delay", 0)))
                    return self._send({"task_id": tid, "created": created})
                finally:
                    con.close()
            if p == "/api/ops/task/claim":
                con = _con()
                try:
                    t = queue.claim(con, b.get("worker") or "api-claim",
                                    lease_s=int(b.get("lease_s", 0)) or None,
                                    kinds=b.get("kinds"))
                    return self._send({"task": t})
                finally:
                    con.close()
            if p in ("/api/ops/task/heartbeat", "/api/ops/task/complete", "/api/ops/task/fail"):
                con = _con()
                try:
                    tid = b.get("task_id") or ""
                    if p.endswith("heartbeat"):
                        ok = queue.heartbeat(con, tid, b.get("worker") or "",
                                             int(b.get("lease_s", 0)) or None)
                        return self._send({"ok": ok})
                    if p.endswith("complete"):
                        ok = queue.complete(con, tid, b.get("worker"), b.get("result"))
                        return self._send({"ok": ok})
                    st = queue.fail(con, tid, b.get("worker"), b.get("error") or "",
                                    retry=bool(b.get("retry", True)))
                    return self._send({"state": st})
                finally:
                    con.close()
            if p == "/api/ops/task/cancel":
                con = _con()
                try:
                    return self._send({"ok": queue.cancel(con, b.get("task_id") or "")})
                finally:
                    con.close()
            if p == "/api/ops/recover":
                con = _con()
                try:
                    r = queue.recover_expired(con, actor="api")
                    RECOVERY_TOTAL["requeued"] += len(r["requeued"])
                    RECOVERY_TOTAL["failed"] += len(r["failed"])
                    return self._send(r)
                finally:
                    con.close()
            if p == "/api/ops/media/reconcile":
                return self._send(self.api_reconcile())
            return self._send({"error": "no such route", "path": p}, 404)
        except Exception as e:                               # noqa: BLE001
            log("POST %s error: %s" % (p, traceback.format_exc(limit=3)))
            return self._send({"error": "%s: %s" % (type(e).__name__, e)}, 500)

    # ---- 具体实现 ----
    def with_store(self, fn):
        data, mode = store.query(config.db_path(), fn)
        if isinstance(data, list):
            return {"rows": data, "count": len(data), "mode": mode}
        return {"data": data, "mode": mode}

    def api_health(self) -> dict:
        con = _con()
        try:
            # 只读缓存，不在请求路径上算完整性
            h = store.health(con, integrity=_INTEGRITY["value"] or "unknown")
            st = queue.stats(con)
            uv = con.execute("SELECT value FROM ops_meta WHERE key='schema_version'").fetchone()
        finally:
            con.close()
        db = config.db_path()
        ok = (h.get("integrity") == "ok") and (h.get("ops_tables") or 0) >= 10
        return {
            "ok": ok,
            "engine": config.ENGINE_NAME,
            "version": config.ENGINE_VERSION,
            "instance": INSTANCE_ID,
            "schema_version": uv["value"] if uv else None,
            "db": str(db),
            "db_bytes": db.stat().st_size if db.exists() else 0,
            "db_integrity": h.get("integrity"),
            "db_integrity_pending": _INTEGRITY["value"] is None,
            "journal_mode": h.get("journal_mode"),
            "user_version": h.get("user_version"),
            "ops_tables": h.get("ops_tables"),
            "queue": st,
            "worker": config.worker_enabled(),
            "worker_stats": worker.STATS,
            "reaper": {"interval_s": config.recover_interval(), "totals": RECOVERY_TOTAL},
            "uptime_s": round(time.time() - START_TS, 1),
            "listen": "%s:%d" % (config.host(), config.port()),
            "writes_scope": "ops_* only",
            "readonly_queries": True,
            "platform_calls": 0,
        }

    def api_reconcile(self) -> dict:
        with _RECON["lock"]:
            if _RECON["value"] is not None and (time.time() - _RECON["at"]) < _RECON["ttl"]:
                out = dict(_RECON["value"])
                out["cached"] = True
                return out
        out = self._reconcile_impl()
        with _RECON["lock"]:
            _RECON["value"] = dict(out)
            _RECON["at"] = time.time()
        out["cached"] = False
        return out

    def _reconcile_impl(self) -> dict:
        """只读核对：v3_image(role=video) 与磁盘 mp4 的三重对照。不删、不改、不写库。"""
        import hashlib
        from pathlib import Path

        base = Path(os.environ.get("OPS_MEDIA_ROOT")
                    or (config.DEV_ROOT.parent.parent / "taobao-picker" / "data" / "v3" / "images"))
        con = _con()
        try:
            rows = con.execute(
                "SELECT image_id, product_id, current_local_path, original_local_path,"
                " content_sha256 FROM v3_image WHERE role='video'").fetchall()
        finally:
            con.close()
        db_map = {}
        for r in rows:
            p = (r["current_local_path"] or r["original_local_path"] or "").replace("/", "\\")
            if p:
                db_map[os.path.normcase(p)] = dict(r)
        disk = {}
        if base.exists():
            for dp, _dn, fn in os.walk(base):
                for f in fn:
                    if f.lower().endswith((".mp4", ".mov", ".m4v")):
                        fp = os.path.join(dp, f)
                        disk[os.path.normcase(fp)] = fp
        orphans = [disk[k] for k in disk if k not in db_map]
        missing = [k for k in db_map if k not in disk]
        return {"media_root": str(base), "db_video_rows": len(rows),
                "db_paths": len(db_map), "disk_files": len(disk),
                "orphans": len(orphans), "missing_on_disk": len(missing),
                "orphan_sample": sorted(os.path.basename(x) for x in orphans)[:10],
                "note": "只读核对；孤儿文件不自动删除（需人工确认）"}


INDEX_HTML = """<!doctype html><meta charset="utf-8">
<title>上货台7.0 · ops_engine (P0)</title>
<style>body{background:#1e1e1e;color:#ddd;font:14px/1.7 Consolas,monospace;padding:24px}
a{color:#6cf}h1{font-size:18px}code{background:#2a2a2a;padding:1px 5px;border-radius:3px}
td{padding:2px 10px 2px 0}</style>
<h1>上货台 7.0 · AI 运营中心 · ops_engine (P0)</h1>
<p>只读查询 + 任务队列。只监听 127.0.0.1，只写 <code>ops_*</code> 表。</p>
<table>
<tr><td>GET</td><td><a href="/api/ops/health">/api/ops/health</a></td></tr>
<tr><td>GET</td><td><a href="/api/ops/products?limit=20">/api/ops/products</a></td></tr>
<tr><td>GET</td><td><a href="/api/ops/videos?limit=20">/api/ops/videos</a></td></tr>
<tr><td>GET</td><td><a href="/api/ops/videos/stats">/api/ops/videos/stats</a></td></tr>
<tr><td>GET</td><td><a href="/api/ops/tasks?limit=20">/api/ops/tasks</a></td></tr>
<tr><td>GET</td><td><a href="/api/ops/tasks/stats">/api/ops/tasks/stats</a></td></tr>
<tr><td>GET</td><td><a href="/api/ops/metrics">/api/ops/metrics</a></td></tr>
<tr><td>GET</td><td><a href="/api/ops/accounts">/api/ops/accounts</a></td></tr>
<tr><td>GET</td><td><a href="/api/ops/tables">/api/ops/tables</a></td></tr>
<tr><td>GET</td><td><a href="/api/ops/recovery">/api/ops/recovery</a></td></tr>
<tr><td>POST</td><td>/api/ops/task/enqueue · /claim · /heartbeat · /complete · /fail · /cancel</td></tr>
<tr><td>POST</td><td>/api/ops/recover · /api/ops/media/reconcile</td></tr>
</table>
"""


def main() -> int:
    global LOG_PATH
    config.ensure_dirs()
    LOG_PATH = str(config.DEV_ROOT / "data" / "ops_engine.log")
    db = config.db_path()
    if not db.exists():
        log("数据库不存在：%s" % db)
        return 2
    con = _con()
    try:
        n = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
                        " AND name LIKE 'ops_%'").fetchone()[0]
    finally:
        con.close()
    if n < 10:
        log("ops_* 表只有 %d 张 —— 先跑 ops.migrate --apply" % n)
        return 3

    # 单实例：优先用 OS 文件锁（进程被杀自动释放）；DB 里的锁行只作信息记录
    if acquire_file_lock() is None:
        log("已有另一个 ops_engine 在运行（文件锁 %s），本次退出" % (config.DEV_ROOT / "data" / "ops_engine.lock"))
        return 4
    try:
        con = _con()
        try:
            queue.acquire_lock(con, LOCK_KEY, INSTANCE_ID, ttl=60)
        finally:
            con.close()
    except Exception:                                        # noqa: BLE001
        pass

    # 先把监听建起来（端口通了才算"引擎在"），再起后台线程
    srv = ThreadingHTTPServer((config.host(), config.port()), Handler)
    srv.daemon_threads = True

    threading.Thread(target=reaper_loop, daemon=True, name="reaper").start()
    threading.Thread(target=lock_loop, daemon=True, name="lock").start()
    # 完整性检查很贵（冷缓存 40s）→ 后台算，请求只读缓存
    threading.Thread(target=integrity_loop, daemon=True, name="integrity").start()
    if config.worker_enabled():
        threading.Thread(target=worker.worker_loop,
                         args=(config.default_lease(), STOP),
                         daemon=True, name="worker").start()
        log("内置 worker 已启动（只认 probe.*）")
    log("ops_engine %s 起于 http://%s:%d/  db=%s  worker=%s"
        % (config.ENGINE_VERSION, config.host(), config.port(), db, config.worker_enabled()))
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        STOP.set()
        srv.server_close()
        try:
            con = _con()
            try:
                queue.release_lock(con, LOCK_KEY, INSTANCE_ID)
            finally:
                con.close()
        except Exception:                                    # noqa: BLE001
            pass
        release_file_lock()
        log("ops_engine 退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
