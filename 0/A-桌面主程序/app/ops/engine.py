# -*- coding: utf-8 -*-
"""8.0 · AI 运营中心 · ops_engine（生产版，2026-11-10 · 本机 9192）

在旧 P0 engine 之上：
    * 双库：ops.db 可写、v3.db 只读 ATTACH（见 queue.connect）
    * 新增 GET  /api/ops/overview | /shops | /task/events | /task/runs
    * GET  /  = 8.0 运营总览（真实数据，自包含 HTML，供 headless 截图验收）
    * POST /api/ops/engine/stop = 优雅停止（主程序也用进程内 request_stop）
    * 进程内线程启停：start_in_thread() / request_stop() / is_alive()
      —— 与 wxserve 同范式：主程序负责启动、监控、关闭；退出不留孤儿端口

硬边界（P0，§九）
    * 只监听 127.0.0.1；只写 ops_* 表；v3_* 一律只读
    * 零平台调用、零上传、零发布、零改价改库存、零密钥明文
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import (config, queue, store, worker, platform, business, browser_ops,
               sync_freshness)

START_TS = time.time()
RECOVERY_TOTAL = {"requeued": 0, "failed": 0, "passes": 0}
STOP = threading.Event()
LOCK_KEY = "ops_engine:%d" % config.port()
INSTANCE_ID = "e-" + uuid.uuid4().hex[:8]
LOG_PATH = str(config.LOG_PATH)
_INTEGRITY = {"value": None, "at": 0.0, "lock": threading.Lock(), "ttl": 900}
_RECON = {"value": None, "at": 0.0, "lock": threading.Lock(), "ttl": 120}
_FILE_LOCK = {"fp": None}
_STATE = {"srv": None, "thread": None, "lock": threading.Lock(), "err": ""}


def log(msg: str) -> None:
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    try:
        print(line, flush=True)
    except Exception:                                        # noqa: BLE001
        pass
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:                                        # noqa: BLE001
        pass


def _con():
    return queue.connect()


def integrity(refresh: bool = False) -> str:
    """ops.db 小库，quick_check 很快；仍后台算一次，请求只读缓存。"""
    with _INTEGRITY["lock"]:
        fresh = (time.time() - _INTEGRITY["at"]) < _INTEGRITY["ttl"]
        if refresh or _INTEGRITY["value"] is None or not fresh:
            con = _con()
            try:
                _INTEGRITY["value"] = con.execute("PRAGMA quick_check").fetchone()[0]
            except Exception as e:                           # noqa: BLE001
                _INTEGRITY["value"] = "error: %s" % e
            finally:
                con.close()
            _INTEGRITY["at"] = time.time()
        return _INTEGRITY["value"]


def acquire_file_lock():
    try:
        import msvcrt
    except ImportError:
        return "n/a"
    p = config.LOCK_PATH
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
        fp.write(("pid=%d at=%s\n" % (os.getpid(),
                  time.strftime("%Y-%m-%d %H:%M:%S"))).encode())
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

    def log_message(self, fmt, *args):                        # noqa: A003
        log("%s %s" % (self.address_string(), fmt % args))

    def _send(self, obj, code: int = 200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass                                             # 探活客户端提前断开，静默

    def _send_html(self, html: str, code: int = 200):
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:                                    # noqa: BLE001
            return {}

    def _local_only(self) -> bool:
        return self.client_address[0] in ("127.0.0.1", "::1")

    def do_GET(self):                                        # noqa: N802
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        try:
            if p == "/":
                return self._send_html(INDEX_HTML)
            if p == "/api/ops/health":
                return self._send(self.api_health())
            if p == "/api/ops/overview":
                return self._send(self.with_store(store.overview))
            if p == "/api/ops/shops":
                return self._send(self.with_store(store.shops))
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
                return self._send(self.with_store(
                    lambda con: queue.list_tasks(con, (q.get("state") or [None])[0],
                                                 int(q.get("limit", ["50"])[0]))))
            if p == "/api/ops/tasks/stats":
                return self._send(self.with_store(queue.stats))
            if p == "/api/ops/task":
                return self._send(self.with_store(
                    lambda con: queue.get_task(con, (q.get("task_id") or [""])[0])))
            if p == "/api/ops/task/events":
                return self._send(self.with_store(
                    lambda con: queue.list_events(con, int(q.get("limit", ["100"])[0]))))
            if p == "/api/ops/task/runs":
                return self._send(self.with_store(
                    lambda con: queue.list_runs(con, int(q.get("limit", ["100"])[0]))))
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
            # ---- 模块1 双平台真实商品中心（平台真数 ≠ v3.db 本地数） ----
            if p == "/api/ops/platform/summary":
                return self._send(self.with_store(platform.summary))
            if p == "/api/ops/platform/products":
                return self._send(self.with_store(
                    lambda con: platform.products(
                        con, shop=(q.get("shop") or [""])[0],
                        status_code=(q.get("status") or [""])[0],
                        q=(q.get("q") or [""])[0],
                        limit=int(q.get("limit", ["300"])[0]))))
            if p == "/api/ops/platform/stats":
                return self._send(self.with_store(platform.stats))
            if p == "/api/ops/platform/status_stats":
                return self._send(self.with_store(platform.status_stats))
            if p == "/api/ops/platform/fetches":
                return self._send(self.with_store(
                    lambda con: platform.fetches(con, int(q.get("limit", ["100"])[0]))))
            if p == "/api/ops/platform/status":
                return self._send(platform.status())
            if p == "/api/ops/platform/sync_status":
                return self._send(self.with_store(platform.sync_status))
            if p == "/api/ops/platform/page":
                return self._send_html(PLATFORM_HTML)
            # ---- 模块2 真实业务运营 + 操作反馈（抖店后台真明细/视频/活动/账号） ----
            if p == "/api/ops/business/page":
                return self._send_html(business.PAGE_HTML)
            if p == "/api/ops/business/overview":
                return self._send(self.with_store(business.business_overview))
            if p == "/api/ops/business/snapshot":
                return self._send(self.with_store(business.snapshot_status))
            if p == "/api/ops/business/dy_products":
                return self._send(self.with_store(
                    lambda con: business.dy_products(
                        con, q=(q.get("q") or [""])[0],
                        limit=int(q.get("limit", ["200"])[0]))))
            if p == "/api/ops/business/content":
                return self._send(self.with_store(
                    lambda con: business.video_rows(
                        con, content_type=(q.get("type") or [""])[0],
                        limit=int(q.get("limit", ["200"])[0]))))
            if p == "/api/ops/business/campaigns":
                return self._send(self.with_store(
                    lambda con: business.campaigns(
                        con, int(q.get("limit", ["200"])[0]))))
            if p == "/api/ops/business/accounts":
                return self._send(self.with_store(business.account_status))
            if p == "/api/ops/business/feedback":
                return self._send(self.with_store(
                    lambda con: business.feedback(
                        con, int(q.get("limit", ["60"])[0]))))
            # ---- 模块3 · 每日真实运营闭环 ----
            if p == "/api/ops/daily/page":
                return self._send_html(DAILY_HTML)
            if p == "/api/ops/daily/overview":
                return self._send(self.with_store(browser_ops.overview))
            if p == "/api/ops/daily/runs":
                return self._send(self.with_store(
                    lambda con: browser_ops.patrol_runs(
                        con, int(q.get("limit", ["30"])[0]))))
            if p == "/api/ops/daily/steps":
                return self._send(self.with_store(
                    lambda con: browser_ops.patrol_steps(
                        con, int(q.get("run", ["0"])[0]),
                        int(q.get("limit", ["60"])[0]))))
            if p == "/api/ops/daily/tasks":
                return self._send(self.with_store(
                    lambda con: browser_ops.daily_tasks(
                        con, (q.get("day") or [""])[0],
                        int(q.get("limit", ["100"])[0]))))
            if p == "/api/ops/daily/reports":
                return self._send(self.with_store(
                    lambda con: browser_ops.daily_reports(
                        con, int(q.get("limit", ["30"])[0]))))
            return self._send({"error": "no such route", "path": p}, 404)
        except Exception as e:                               # noqa: BLE001
            log("GET %s error: %s" % (p, traceback.format_exc(limit=3)))
            return self._send({"error": "%s: %s" % (type(e).__name__, e)}, 500)

    def do_POST(self):                                       # noqa: N802
        if not self._local_only():
            return self._send({"error": "loopback only"}, 403)
        p = urlparse(self.path).path
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
                    return self._send({"task": queue.claim(
                        con, b.get("worker") or "api-claim",
                        lease_s=int(b.get("lease_s", 0)) or None,
                        kinds=b.get("kinds"))})
                finally:
                    con.close()
            if p in ("/api/ops/task/heartbeat", "/api/ops/task/complete",
                     "/api/ops/task/fail"):
                con = _con()
                try:
                    tid = b.get("task_id") or ""
                    if p.endswith("heartbeat"):
                        return self._send({"ok": queue.heartbeat(
                            con, tid, b.get("worker") or "",
                            int(b.get("lease_s", 0)) or None)})
                    if p.endswith("complete"):
                        return self._send({"ok": queue.complete(
                            con, tid, b.get("worker"), b.get("result"))})
                    return self._send({"state": queue.fail(
                        con, tid, b.get("worker"), b.get("error") or "",
                        retry=bool(b.get("retry", True)))})
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
            if p == "/api/ops/engine/stop":
                threading.Thread(target=request_stop, daemon=True).start()
                return self._send({"stopping": True, "instance": INSTANCE_ID})
            if p == "/api/ops/media/reconcile":
                return self._send(self.api_reconcile())
            # 模块1：真实采集（后台线程，只写 ops.db；full 抓更多分页）
            if p == "/api/ops/platform/collect":
                return self._send(platform.collect_async(
                    mode=(b.get("mode") or "snapshot"),
                    shop_filter=(b.get("shop") or "")))
            # 模块2：业务动作统一入口（每个按钮一条 running→done/failed 反馈）
            if p == "/api/ops/business/action":
                r = business.run_action(
                    b.get("module") or "业务工作台", b.get("action") or "",
                    b.get("payload") or {}, actor=(b.get("actor") or "gui"))
                return self._send(r, 200 if r.get("ok") else 500)
            # 模块3：真浏览器每日巡店（A 类自动执行；结果回读 + 反馈）
            if p == "/api/ops/daily/patrol":
                r = browser_ops.run_patrol(actor=(b.get("actor") or "gui"))
                return self._send(r, 200 if r.get("ok") else 500)
            return self._send({"error": "no such route", "path": p}, 404)
        except Exception as e:                               # noqa: BLE001
            log("POST %s error: %s" % (p, traceback.format_exc(limit=3)))
            return self._send({"error": "%s: %s" % (type(e).__name__, e)}, 500)

    def with_store(self, fn):
        data, mode = store.query(config.db_path(), fn)
        if isinstance(data, list):
            return {"rows": data, "count": len(data), "mode": mode}
        return {"data": data, "mode": mode}

    def api_health(self) -> dict:
        con = _con()
        try:
            # ops.db 是小库，health 请求路径上直接算 quick_check（毫秒级），不依赖后台线程
            # 是否算完 —— 否则刚启动第一秒 ok 会误报 False。
            integ = _INTEGRITY["value"]
            if not integ or integ == "unknown":
                integ = integrity(True)
            h = store.health(con, integrity=integ)
            st = queue.stats(con)
            uv = con.execute(
                "SELECT value FROM ops_meta WHERE key='schema_version'").fetchone()
            v3 = store.v3_counts(con)
            shops = store.shops(con)
        finally:
            con.close()
        db = config.db_path()
        ok = (h.get("integrity") == "ok") and (h.get("ops_tables") or 0) >= 10
        return {
            "ok": ok,
            "engine": config.ENGINE_NAME,
            "version": config.ENGINE_VERSION,
            "architecture": "ops.db 独立 + v3.db 只读 ATTACH",
            "instance": INSTANCE_ID,
            "schema_version": uv["value"] if uv else None,
            "db": str(db),
            "db_bytes": db.stat().st_size if db.exists() else 0,
            "db_integrity": h.get("integrity"),
            "db_integrity_pending": _INTEGRITY["value"] is None,
            "journal_mode": h.get("journal_mode"),
            "ops_tables": h.get("ops_tables"),
            "v3": {"path": str(config.v3_path()), "attached": v3.get("attached"),
                   "products": v3.get("products"), "videos": v3.get("videos"),
                   "readonly": True},
            "shops": shops.get("count", 0),
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
        """只读核对：v3_image(role=video) 与磁盘 mp4。不删、不改、不写库。"""
        base = Path(os.environ.get("OPS_MEDIA_ROOT")
                    or (config.WS / "taobao-picker" / "data" / "v3" / "images"))
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


from pathlib import Path                                     # noqa: E402

# --------------------------------------------------------------------------- #
# 模块3 · 每日运营（给主人看经营结果，不是数据库/技术日志）
# --------------------------------------------------------------------------- #
DAILY_HTML = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>8.0 · 每日运营</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#12151c;color:#e6e9ef;
 font:14px/1.7 "Microsoft YaHei",Consolas,sans-serif;padding:20px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:14px;margin:18px 0 8px;color:#9aa4b8}
.sub{color:#8b95a8;font-size:12px;margin-bottom:14px}
.bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
button{background:#2b6cd4;color:#fff;border:0;border-radius:6px;padding:8px 16px;
 font-size:13px;cursor:pointer}button:disabled{opacity:.5;cursor:wait}
#msg{font-size:12px;color:#9aa4b8}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.card{background:#1c212c;border:1px solid #2b3242;border-radius:8px;padding:12px}
.k{color:#8b95a8;font-size:12px}.v{font-size:22px;font-weight:700;color:#6ee7a8;margin-top:2px}
.v.blue{color:#71b7ff}.v.warn{color:#ffc46b}
pre{background:#1c212c;border:1px solid #2b3242;border-radius:8px;padding:14px;
 white-space:pre-wrap;font:13px/1.8 "Microsoft YaHei",Consolas,sans-serif;color:#dfe4ee}
table{width:100%;border-collapse:collapse;background:#1c212c;border-radius:8px;overflow:hidden}
td,th{padding:7px 10px;border-bottom:1px solid #262d3b;text-align:left;font-size:12px}
th{color:#8b95a8;background:#191e28}.ok{color:#6ee7a8}.bad{color:#ff8b8b}
.tag{display:inline-block;padding:1px 7px;border-radius:10px;background:#26344a;color:#9cc4ff;font-size:11px}
</style>
<h1>每日运营 <span class="tag">真浏览器 · 真后台</span></h1>
<div class="sub">抖店后台真登录巡店 → 回读 → 今日任务 → 经营日报；零发布/零改价/零改库存；拿不到的数据标「未取得」，不编造。</div>
<div class="bar">
 <button id="btn">▶ 现在真巡一次店</button>
 <span id="msg">点一下：真打开后台、真点菜单、真搜索商品、真回读，全程留截图与步骤记录。</span>
</div>
<h2>今天的结果</h2><div class="grid" id="g"></div>
<h2>今日运营任务（A 自动 / B 等你确认 / C 人工）</h2>
<table id="tasks"><tr><th>类</th><th>任务</th><th>依据</th><th>状态</th></tr></table>
<h2>今日经营日报</h2><pre id="report">还没有日报。点一次「现在真巡一次店」即可生成。</pre>
<h2>真实巡店步骤（操作前后 / 点击 / 回读 / 截图）</h2>
<table id="steps"><tr><th>#</th><th>页面</th><th>方式</th><th>回读</th><th>字数</th><th>耗时</th><th>截图</th></tr></table>
<h2>历次巡店</h2>
<table id="runs"><tr><th>#</th><th>店铺</th><th>登录</th><th>成功步</th><th>搜索任务</th><th>结果</th><th>时间</th></tr></table>
<script>
function esc(s){return String(s==null?'':s).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c]})}
function get(u){return fetch(u).then(function(r){return r.json()})}
function card(k,v,c){return '<div class="card"><div class="k">'+k+'</div><div class="v '+(c||'')+'">'+v+'</div></div>'}
function rows(o){return o.rows||o.data||[]}
function load(){
 get('/api/ops/daily/overview').then(function(o){
   var d=o.data||o;
   document.getElementById('g').innerHTML=
    card('今天已完成',d.today_auto||0)+card('等你确认',d.today_need_human||0,'warn')+
    card('人工/不可得',d.today_manual||0,'blue')+card('巡店次数',d.patrol_runs||0,'blue');
 });
 get('/api/ops/daily/tasks').then(function(o){
   var s='<tr><th>类</th><th>任务</th><th>依据</th><th>状态</th></tr>';
   rows(o).forEach(function(t){s+='<tr><td>'+esc(t.cls)+'</td><td>'+esc(t.title)+
     '</td><td>'+esc((t.evidence||'').slice(0,60))+'</td><td>'+esc(t.state)+'</td></tr>'});
   if(!rows(o).length)s+='<tr><td colspan="4">今天还没跑巡店</td></tr>';
   document.getElementById('tasks').innerHTML=s;
 });
 get('/api/ops/daily/reports').then(function(o){
   var r=rows(o)[0];
   document.getElementById('report').textContent=r?r.body:'还没有日报。点一次「现在真巡一次店」即可生成。';
 });
 get('/api/ops/daily/steps?limit=40').then(function(o){
   var s='<tr><th>#</th><th>页面</th><th>方式</th><th>回读</th><th>字数</th><th>耗时</th><th>截图</th></tr>';
   rows(o).forEach(function(t){s+='<tr><td>'+esc(t.seq)+'</td><td>'+esc(t.label)+
     '</td><td>'+esc(t.method)+'</td><td class="'+(t.readback_ok?'ok':'bad')+'">'+
     (t.readback_ok?'成功':'未通过')+'</td><td>'+esc(t.body_len)+'</td><td>'+esc(t.elapsed_ms)+
     'ms</td><td>'+(t.screenshot?'有':'—')+'</td></tr>'});
   document.getElementById('steps').innerHTML=s;
 });
 get('/api/ops/daily/runs').then(function(o){
   var s='<tr><th>#</th><th>店铺</th><th>登录</th><th>成功步</th><th>搜索任务</th><th>结果</th><th>时间</th></tr>';
   rows(o).forEach(function(t){s+='<tr><td>'+esc(t.run_id)+'</td><td>'+esc(t.shop)+
     '</td><td>'+esc(t.login_state)+'</td><td>'+esc(t.ok_steps)+'/'+esc(t.total_steps)+
     '</td><td>'+(t.search_ok?'<span class=ok>通过</span>':'<span class=bad>未通过</span>')+
     '</td><td>'+esc(t.state)+'</td><td>'+esc(t.started_at)+'</td></tr>'});
   document.getElementById('runs').innerHTML=s;
 });
}
document.getElementById('btn').onclick=function(){
 var b=this;b.disabled=true;
 document.getElementById('msg').textContent='正在真浏览器巡店（约 1–3 分钟）…';
 fetch('/api/ops/daily/patrol',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({actor:'gui'})}).then(function(r){return r.json()}).then(function(d){
   document.getElementById('msg').textContent=d.ok?('✔ '+(d.result||'完成')):
     ('✘ '+(d.error||d.state||'未完成'));
   b.disabled=false;load();
 }).catch(function(e){document.getElementById('msg').textContent='请求失败：'+e;
   b.disabled=false;});
};
load();setInterval(load,15000);
</script></html>"""

# --------------------------------------------------------------------------- #
# 8.0 运营总览（自包含 HTML，真数据由 JS 拉 /api/ops/overview + /health）
# --------------------------------------------------------------------------- #
INDEX_HTML = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>8.0 · AI 运营中心 · 运营总览</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#12151c;color:#e6e9ef;
 font:14px/1.6 "Microsoft YaHei",Consolas,sans-serif;padding:22px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:14px;margin:18px 0 8px;color:#9aa4b8}
.sub{color:#8b95a8;font-size:12px;margin-bottom:16px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.card{background:#1c212c;border:1px solid #2b3242;border-radius:8px;padding:12px}
.k{color:#8b95a8;font-size:12px}.v{font-size:22px;font-weight:700;color:#6ee7a8;margin-top:2px}
.v.blue{color:#71b7ff}.v.warn{color:#ffc46b}
table{width:100%;border-collapse:collapse;background:#1c212c;border-radius:8px;overflow:hidden}
td,th{padding:7px 10px;border-bottom:1px solid #262d3b;text-align:left;font-size:12px}
th{color:#8b95a8;font-weight:600;background:#191e28}
.tag{display:inline-block;padding:1px 7px;border-radius:10px;background:#26344a;color:#9cc4ff;font-size:11px}
.ok{color:#6ee7a8}.bad{color:#ff8b8b}
.mono{font-family:Consolas,monospace;font-size:11px;color:#aab3c4}
#err{color:#ff8b8b}
.todo{background:#182231;border:1px solid #35507a;border-left:4px solid #71b7ff;border-radius:8px;padding:14px 16px;margin:6px 0 16px}
.todo h2{color:#cfe2ff;font-size:17px;margin:0 0 4px}
.todo .hint{color:#93a0b4;font-size:12px;margin-bottom:10px}
.todo .row{display:flex;gap:10px;align-items:flex-start;border-top:1px solid #2b3242;padding:9px 0;cursor:pointer}
.todo .row input{margin-top:4px;width:17px;height:17px;accent-color:#6ee7a8;flex:none}
.todo .row .body{flex:1}
.todo .row .t{font-size:14px;font-weight:700;color:#e6e9ef}
.todo .row .d{font-size:12px;color:#aab3c4;margin-top:3px;line-height:1.65}
.todo .row .n{display:inline-flex;width:20px;height:20px;border-radius:50%;background:#2f3a4b;color:#71b7ff;align-items:center;justify-content:center;font-size:12px;margin-right:7px}
.todo .sys{color:#6ee7a8}.todo .me{color:#ffc46b}.todo .no{color:#ff8b8b}
.todo a{color:#9cc4ff}
.todo .row.done .t{text-decoration:line-through;color:#8b95a8}
.todo .foot{font-size:11px;color:#8b95a8;margin-top:8px;border-top:1px dashed #2b3242;padding-top:9px}
details{margin-top:4px}summary{cursor:pointer;color:#8b95a8;font-size:12px;padding:6px 0;user-select:none}
</style>
<h1>8.0 · AI 运营中心 <span class="tag">运营总览</span></h1>
<div class="sub">独立 ops.db 可写 · 商品主库 v3.db 只读挂载 · 零平台调用 · P0 底座</div>
<div id="err"></div>
<div class="todo" id="today">
 <h2>今天照着做</h2>
 <div class="hint">一家抖店 + 两家微店 · 下面 <b style="color:#e6e9ef">4 行</b>看完、该点的点了，今天就完了。
 <b style="color:#ffc46b">覆盖率 / 新鲜度 / 采集状态那些是给系统自己体检的，你不用管</b>（已折到最下面）。勾过的会自己记住，明天自动重来。</div>

 <label class="row" data-k="s1"><input type="checkbox"><div class="body">
  <div class="t"><span class="n">1</span>货还在不在架、有没有被打回</div>
  <div class="d"><span class="sys">系统已做：</span>三店货一件不漏数全（欢轴家居 700 · 崇耿 143 · 抖店 3846 标旧数据）、驳回原因列得出来。
  <span class="me">你做：</span>去后台点一眼，特别盯「驳回 / 违规」，改完再往下（被打回的货，报什么活动都白搭）。</div></div></label>

 <label class="row" data-k="s2"><input type="checkbox"><div class="body">
  <div class="t"><span class="n">2</span>有没有报上活动、限时限量购</div>
  <div class="d"><span class="sys">系统已做：</span>按平台规则 + 真商品价格把报名资料算好、列成草稿，一条条等你点头。
  <span class="no">系统做不到：</span>读不到平台「已报名 / 报名成功」——活动列表没开放接口，不编、不冒充。
  <span class="me">你做：</span>去抖店后台「营销 → 限时限量购」点一眼，报完回来在草稿上点「人工确认」。</div></div></label>

 <label class="row" data-k="s3"><input type="checkbox"><div class="body">
  <div class="t"><span class="n">3</span>视频传没传、有没有挂上商品链接</div>
  <div class="d"><span class="sys">系统已做：</span>194 条真视频按商品配好、生成发布准备草稿（「商品详情视频」和「短视频挂车」分开摆）。
  <span class="no">系统做不到：</span>平台侧「上传成功 / 已关联商品」回执读不到。
  <span class="me">你做：</span>后台传完、挂完链接，点进去核一下跳过去是不是那件货。</div></div></label>

 <label class="row" data-k="s4"><input type="checkbox"><div class="body">
  <div class="t"><span class="n">4</span>今天卖了多少、退了多少</div>
  <div class="d"><span class="no">系统做不到：</span>两家平台都没给成交/退款/曝光接口，AI 不许编数字，也不会拿 0 冒充「没卖了」。
  <span class="me">你做：</span>后台截一眼，或口述给我记进当天日报。放最后看，因为它是上面三件事有没有用的判据。</div></div></label>

 <div class="foot"><b style="color:#e6e9ef">明天就一件事：</b>挑 1–3 款货，补一条视频 <b>或</b> 报一个活动，第二天回来看第 4 行的数变没变；变了照着再来，没变换款、别加码。
 &nbsp;·&nbsp; <a href="/api/ops/business/page">打开业务工作台（模块2）→ 点 ③ 短视频 / ④ 限时限量购</a>
 <br>红线：不替你真报名、不替你发视频、不改价、不改库存、不批量上架 ——「人工确认」只在本地留记号，平台那一下永远你自己点。</div>
</div>
<details><summary>▸ 系统自检数据（我给系统体检用的，初级选手不用管，点开才看）</summary>
<h2>引擎与数据</h2>
<div class="grid" id="g1"></div>
<h2>三店数据真实性（P0.3.1 · 平台数/明细/覆盖率/新鲜度/最后成功/状态/错误）</h2>
<table id="sync"><tr><th>店铺</th><th>平台商品总数</th><th>已读明细</th><th>数据覆盖率</th>
<th>最近采集时间</th><th>数据新鲜度</th><th>采集状态</th><th>采集错误</th></tr></table>
<h2>真实店铺与账号（完整 shop_id，不存凭据）</h2>
<table id="shops"><tr><th>平台</th><th>店名</th><th>完整 shop_id</th><th>引擎</th><th>状态</th></tr></table>
<h2>任务队列与执行记录</h2>
<div class="grid" id="g2"></div>
<h2>最近任务状态事件</h2>
<table id="events"><tr><th>时间</th><th>任务</th><th>类型</th><th>转移</th><th>说明</th></tr></table>
</details>
<script>
function card(k,v,cls){return '<div class="card"><div class="k">'+k+'</div><div class="v '+(cls||'')+'">'+v+'</div></div>'}
function esc(s){return String(s==null?'':s).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c]})}
function get(u){return fetch(u).then(function(r){return r.json()})}
Promise.all([get('/api/ops/health'),get('/api/ops/overview'),get('/api/ops/task/events?limit=12'),
             get('/api/ops/platform/summary')])
.then(function(a){
  var h=a[0], o=a[1].data||a[1], ev=a[2].rows||[], pr=a[3].data||a[3];
  document.getElementById('g1').innerHTML=
    card('引擎',h.ok?'在线':'异常',h.ok?'':'warn')+
    card('版本',esc(h.version),'blue')+
    card('商品（v3 只读）',o.v3&&o.v3.products!=null?o.v3.products:'—')+
    card('视频素材',o.v3&&o.v3.videos!=null?o.v3.videos:'—')+
    card('店铺数',o.shops?o.shops.count:0,'blue')+
    card('ops 表',h.ops_tables)+
    card('平台调用',h.platform_calls)+
    card('监听',esc(h.listen),'blue');
  document.getElementById('g2').innerHTML=
    card('排队',o.queue?o.queue.queued:0)+
    card('运行',o.queue?o.queue.running:0,'blue')+
    card('完成',o.queue?o.queue.done:0)+
    card('失败',o.queue?o.queue.failed:0,'warn')+
    card('执行记录 runs',o.runs!=null?o.runs:'—','blue')+
    card('状态事件 events',o.events!=null?o.events:'—')+
    card('视频资产',o.video_assets!=null?o.video_assets:'—')+
    card('错误事件',o.errors!=null?o.errors:'—');
  var s='<tr><th>平台</th><th>店名</th><th>完整 shop_id</th><th>引擎</th><th>状态</th></tr>';
  (o.shops&&o.shops.shops||[]).forEach(function(x){
    s+='<tr><td>'+esc(x.platform)+'</td><td>'+esc(x.display_name)+'</td><td class="mono">'+
       esc(x.account_id)+'</td><td>'+esc(x.browser_engine)+'</td><td>'+
       esc(x.login_status)+'</td></tr>'});
  document.getElementById('shops').innerHTML=s;
  // P0.3.1 三店数据真实性：7 字段逐店展示，历史快照明确标红
  var sy='<tr><th>店铺</th><th>平台商品总数</th><th>已读明细</th><th>数据覆盖率</th>'+
    '<th>最近采集时间</th><th>数据新鲜度</th><th>采集状态</th><th>采集错误</th></tr>';
  var hist={'stale':'bad','failed':'bad','missing':'','fresh':'ok'};
  (pr.shops||[]).forEach(function(x){
    var total=(x.total==null?'<span class="bad">未取得</span>':esc(x.total));
    var cov=esc(x.coverage_text||'未取得');
    var fs=esc(x.freshness_text||'—');
    var stt=esc(x.data_status||'—');
    var cls=x.is_historical||x.data_status==='stale'?' class="bad"':'';
    sy+='<tr'+cls+'><td>'+esc(x.shop_name)+'</td><td>'+total+'</td><td>'+
      esc(x.detail_rows!=null?x.detail_rows:'—')+'</td><td>'+cov+'</td><td class="mono">'+
      esc(x.last_success_at||x.fetched_at||'—')+'</td><td>'+fs+'</td><td>'+stt+'</td><td>'+
      esc((x.error||'—').slice(0,44))+'</td></tr>';
  });
  if(!(pr.shops||[]).length)sy+='<tr><td colspan="8">尚无采集记录</td></tr>';
  document.getElementById('sync').innerHTML=sy;
  var e='<tr><th>时间</th><th>任务</th><th>类型</th><th>转移</th><th>说明</th></tr>';
  ev.forEach(function(x){
    e+='<tr><td class="mono">'+esc(x.at_text)+'</td><td class="mono">'+esc(x.task_id)+
       '</td><td>'+esc(x.kind)+'</td><td>'+esc(x.from_state)+' → <b>'+esc(x.to_state)+
       '</b></td><td>'+esc((x.detail||'').slice(0,50))+'</td></tr>'});
  if(!ev.length)e+='<tr><td colspan="5">暂无事件（入队探针任务后这里会出现真实记录）</td></tr>';
  document.getElementById('events').innerHTML=e;
}).catch(function(e){document.getElementById('err').textContent='接口读取失败：'+e});
(function(){var d=new Date().toISOString().slice(0,10),P='xins80-'+d+'-';
 var ls=document.querySelectorAll('#today .row');
 function paint(el){var on=localStorage.getItem(P+el.dataset.k)==='1';
  el.querySelector('input').checked=on;el.classList.toggle('done',on);}
 ls.forEach(function(el){paint(el);
  el.querySelector('input').addEventListener('change',function(){
   localStorage.setItem(P+el.dataset.k,this.checked?'1':'0');paint(el);});});})();
</script></html>"""


# --------------------------------------------------------------------------- #
# 模块1 · 双平台真实商品中心（自包含页面，真数据由 JS 拉 /api/ops/platform/*）
# --------------------------------------------------------------------------- #
PLATFORM_HTML = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>8.0 · 双平台真实商品中心</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#12151c;color:#e6e9ef;
 font:13px/1.6 "Microsoft YaHei",Consolas,sans-serif;padding:20px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:14px;margin:18px 0 8px;color:#9aa4b8}
.sub{color:#8b95a8;font-size:12px;margin-bottom:14px}
.bar{margin:10px 0;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
button{background:#2b5cff;color:#fff;border:0;border-radius:6px;padding:7px 14px;
 cursor:pointer;font-size:13px}button.ghost{background:#2b3242}button:disabled{opacity:.5}
select,input{background:#1c212c;color:#e6e9ef;border:1px solid #2b3242;
 border-radius:6px;padding:6px 8px;font-size:13px}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.card{background:#1c212c;border:1px solid #2b3242;border-radius:8px;padding:12px}
.k{color:#8b95a8;font-size:12px}.v{font-size:24px;font-weight:700;color:#6ee7a8;margin:2px 0}
.v.blue{color:#71b7ff}.v.warn{color:#ffc46b}.v.gray{color:#8b95a8}
.name{font-size:14px;font-weight:700;margin-bottom:6px}
.mono{font-family:Consolas,monospace;font-size:11px;color:#aab3c4}
.tag{display:inline-block;padding:1px 6px;border-radius:9px;background:#26344a;
 color:#9cc4ff;font-size:11px;margin:1px 3px 1px 0}
.tag.bad{background:#4a2630;color:#ff9b9b}.tag.ok{background:#1e4330;color:#7fe0a5}
table{width:100%;border-collapse:collapse;background:#1c212c;border-radius:8px;overflow:hidden}
td,th{padding:6px 8px;border-bottom:1px solid #262d3b;text-align:left;font-size:12px}
th{color:#8b95a8;font-weight:600;background:#191e28;position:sticky;top:0}
tr.warnrow{background:#2a2028}
#msg{color:#ffc46b;font-size:12px;margin-left:6px}
#err{color:#ff8b8b}
.badtag{color:#ff9b9b;font-weight:600}
.note{color:#8b95a8;font-size:11px;margin-top:4px}
</style>
<h1>8.0 · 双平台真实商品中心 <span class="tag">模块1</span></h1>
<div class="sub">微信＝官方 API 权威数 · 抖店＝ego 页面快照（未取得的不编造）· 平台真数 ≠ 本地 v3.db 商品数 · 零发布零改价</div>
<div class="bar">
  <button id="btnSnap">↻ 快速刷新（每状态 1 页）</button>
  <button id="btnFull" class="ghost">⇩ 全量同步（微信两店翻完所有分页）</button>
  <span id="msg"></span>
</div>
<h2>三店真实商品概览</h2>
<div class="grid" id="shops"></div>
<h2>商品明细（真实逐行）</h2>
<div class="bar">
  <select id="fShop"><option value="">全部店铺</option></select>
  <select id="fStatus"><option value="">全部状态</option></select>
  <input id="fQ" placeholder="搜商品ID / 标题" style="width:220px">
  <button id="btnFilter" class="ghost">筛选</button>
  <span id="cnt" class="mono"></span>
</div>
<div style="max-height:430px;overflow:auto"><table id="plist">
<tr><th>店铺</th><th>商品ID</th><th>标题</th><th>状态</th><th>价</th><th>库存</th>
<th>本地ID匹配</th><th>来源</th><th>更新时间</th><th>读取</th></tr></table></div>
<h2>ID 匹配与读取失败记录</h2>
<div id="stat" class="mono"></div>
<table id="fails"><tr><th>时间</th><th>店铺</th><th>来源</th><th>成功</th><th>行数</th><th>错误</th></tr></table>
<div id="err"></div>
<script>
function esc(s){return String(s==null?'':s).replace(/[&<>]/g,function(c){
 return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c]})}
function get(u){return fetch(u).then(function(r){return r.json()})}
function post(u,b){return fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},
 body:JSON.stringify(b||{})}).then(function(r){return r.json()})}
function card(s){
 var bs=s.by_status||{}, tags='';
 Object.keys(bs).forEach(function(k){tags+='<span class="tag">'+esc(k)+' '+esc(bs[k])+'</span>'});
 var tot=s.total==null?'<span class="v gray">未取得</span>':'<div class="v">'+esc(s.total)+'</div>';
 return '<div class="card"><div class="name">'+esc(s.shop_name)+
   ' <span class="tag '+(s.platform=='wx'?'ok':'')+'">'+esc(s.platform)+'</span></div>'+
   '<div class="k">平台实际商品数（权威）</div>'+tot+
   '<div class="note mono">'+esc(s.shop_id)+'</div>'+
   '<div style="margin:6px 0">'+tags+'</div>'+
   '<div class="k">明细 '+esc(s.detail_rows)+' 行 · 读取 '+(
     s.read_ok?'<span class="tag ok">成功</span>':'<span class="tag bad">失败</span>')+'</div>'+
    '<div class="note">数据覆盖率 '+esc(s.coverage_text||'未取得')+'</div>'+
    '<div class="note'+(s.is_historical?' badtag':'')+'">新鲜度 '+esc(s.freshness_text||'—')+' | 最近采集 '+esc(s.last_success_at||s.fetched_at||'—')+'</div>'+
   '<div class="note">来源 '+esc(s.source_kind)+' · '+esc(s.fetched_at)+'</div>'+
   (s.error?'<div class="tag bad">'+esc(s.error.slice(0,80))+'</div>':'')+
   '<div class="note">'+esc(s.note||'')+'</div></div>';
}
function loadShops(){
 return get('/api/ops/platform/summary').then(function(d){
   var rows=d.shops||d.data&&d.data.shops||[];
   document.getElementById('shops').innerHTML=rows.map(card).join('')||
     '<div class="card">尚无数据，点「刷新真实数据」采集一次</div>';
   var sel=document.getElementById('fShop'),old=sel.value;
   sel.innerHTML='<option value="">全部店铺</option>'+rows.map(function(s){
     return '<option value="'+esc(s.shop_id)+'">'+esc(s.shop_name)+'</option>'}).join('');
   sel.value=old;
 });
}
function loadProducts(){
 var sh=document.getElementById('fShop').value,
     st=document.getElementById('fStatus').value,
     q=document.getElementById('fQ').value;
 var u='/api/ops/platform/products?limit=300&shop='+encodeURIComponent(sh)+
   '&status='+encodeURIComponent(st)+'&q='+encodeURIComponent(q);
 return get(u).then(function(d){
   var rows=d.rows||[];
   document.getElementById('cnt').textContent='本页 '+rows.length+' 行';
   var h='<tr><th>店铺</th><th>商品ID</th><th>标题</th><th>状态</th><th>价</th><th>库存</th>'+
     '<th>本地ID匹配</th><th>来源</th><th>更新时间</th><th>读取</th></tr>';
   rows.forEach(function(r){
     h+='<tr'+(r.read_ok?'':' class="warnrow"')+'><td>'+esc(r.shop_name)+'</td>'+
       '<td class="mono">'+esc(r.product_id)+'</td><td>'+esc((r.title||'').slice(0,34))+'</td>'+
       '<td>'+esc(r.status_text)+'</td><td>'+esc(r.price_min==null?'—':r.price_min)+'</td>'+
       '<td>'+esc(r.stock_total==null?'—':r.stock_total)+'</td>'+
       '<td>'+(r.match_state=='matched'?'<span class="tag ok">'+esc(r.local_product_id)+
         '</span>':'<span class="tag bad">未匹配</span>')+'</td>'+
       '<td class="mono">'+esc(r.source_kind)+'</td><td class="mono">'+esc(r.source_time||r.fetched_at)+
       '</td><td>'+(r.read_ok?'OK':'<span class="tag bad">失败</span>')+'</td></tr>';
   });
   if(!rows.length)h+='<tr><td colspan="10">本筛选下暂无数据</td></tr>';
   document.getElementById('plist').innerHTML=h;
 });
}
function loadStats(){
 return Promise.all([get('/api/ops/platform/stats'),get('/api/ops/platform/fetches?limit=100')])
 .then(function(a){
   var s=a[0].data||a[0], fs=a[1].rows||[];
   document.getElementById('stat').innerHTML='平台明细合计 '+esc(s.total)+
     ' 行 · 本地ID已匹配 '+esc(s.matched)+' · 未匹配 '+esc(s.unmatched)+
     ' ｜ 读取记录 '+fs.length+' 条，失败 '+fs.filter(function(x){return !x.read_ok}).length+' 条';
   var h='<tr><th>时间</th><th>店铺</th><th>来源</th><th>成功</th><th>行数</th><th>错误</th></tr>';
   fs.slice(0,30).forEach(function(x){
     h+='<tr'+(x.read_ok?'':' class="warnrow"')+'><td class="mono">'+esc(x.fetched_at)+
       '</td><td class="mono">'+esc(x.shop_id||'—')+'</td><td class="mono">'+esc((x.source||'').slice(-52))+
       '</td><td>'+(x.read_ok?'OK':'<span class="tag bad">失败</span>')+'</td><td>'+esc(x.rows)+
       '</td><td>'+esc(x.error||x.note||'')+'</td></tr>';
   });
   document.getElementById('fails').innerHTML=h;
 });
}
function refreshAll(){return Promise.all([loadShops(),loadProducts(),loadStats()])}
function collect(mode){
 var b=document.getElementById(mode=='full'?'btnFull':'btnSnap');
 b.disabled=true;document.getElementById('msg').textContent='采集中…（后台真读 9191 官方接口 / 抖店快照）';
 post('/api/ops/platform/collect',{mode:mode}).then(function(){
   var t=setInterval(function(){
     get('/api/ops/platform/status').then(function(s){
       document.getElementById('msg').textContent=s.running?
         '采集中… '+(s.log||[]).slice(-1)[0]:'采集完成';
       if(!s.running){clearInterval(t);b.disabled=false;refreshAll();
         document.getElementById('msg').textContent='采集完成 '+(s.finished_at||'');}
     });
   },1200);
 }).catch(function(e){b.disabled=false;document.getElementById('msg').textContent='失败 '+e});
}
document.getElementById('btnSnap').onclick=function(){collect('snapshot')};
document.getElementById('btnFull').onclick=function(){collect('full')};
document.getElementById('btnFilter').onclick=loadProducts;
document.getElementById('fShop').onchange=loadProducts;
document.getElementById('fStatus').onchange=loadProducts;
document.getElementById('fQ').onkeydown=function(e){if(e.key=='Enter')loadProducts()};
get('/api/ops/platform/products?limit=1').then(function(d){
 var seen={},h='<option value="">全部状态</option>';
 return get('/api/ops/platform/products?limit=300').then(function(a){
   (a.rows||[]).forEach(function(r){seen[r.status_code]=r.status_text});
   Object.keys(seen).forEach(function(k){h+='<option value="'+esc(k)+'">'+esc(seen[k]||k)+'</option>'});
   document.getElementById('fStatus').innerHTML=h;
 });
}).then(refreshAll).catch(function(e){
 document.getElementById('err').textContent='接口读取失败：'+e});
</script></html>"""


# --------------------------------------------------------------------------- #
# 生命周期：进程内线程启停（主程序调用）
# --------------------------------------------------------------------------- #
def _serve() -> int:
    global START_TS
    config.ensure_dirs()
    START_TS = time.time()
    STOP.clear()
    db = config.db_path()
    if not db.exists():
        _STATE["err"] = "ops.db 不存在：先跑 python -m app.ops.migrate --apply"
        log(_STATE["err"])
        return 2
    con = _con()
    try:
        n = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
                        " AND name LIKE 'ops_%'").fetchone()[0]
    finally:
        con.close()
    if n < 10:
        _STATE["err"] = "ops_* 表只有 %d 张 —— 先跑迁移" % n
        log(_STATE["err"])
        return 3
    # 模块1/2 增量：业务表在已建的 ops.db 上幂等补齐（只 CREATE IF NOT EXISTS）
    try:
        con = _con()
        try:
            platform.ensure_tables(con)
            business.ensure_tables(con)
            sync_freshness.ensure_tables(con)   # P0.3.1 采集真实性表（幂等）
            log("模块1 平台商品表 / 模块2 业务表 / P0.3.1 同步状态表已确保存在")
        finally:
            con.close()
    except Exception as e:                                    # noqa: BLE001
        _STATE["err"] = "业务表建不起来：%s: %s" % (type(e).__name__, e)
        log(_STATE["err"])
        return 6
    if acquire_file_lock() is None:
        _STATE["err"] = "已有另一个 ops_engine 在运行（文件锁）"
        log(_STATE["err"])
        return 4
    try:
        con = _con()
        try:
            queue.acquire_lock(con, LOCK_KEY, INSTANCE_ID, ttl=60)
        finally:
            con.close()
    except Exception:                                        # noqa: BLE001
        pass
    try:
        srv = ThreadingHTTPServer((config.host(), config.port()), Handler)
    except OSError as e:
        _STATE["err"] = "端口 %d 起不来：%s" % (config.port(), e)
        log(_STATE["err"])
        release_file_lock()
        return 5
    srv.daemon_threads = True
    _STATE["srv"] = srv
    # integrity 由首个 /health 请求自己算（ops.db 小库，毫秒级）并缓存 15 分钟；
    # 不再另起后台线程抢同一把锁 —— 那会让启动瞬间的探活超时刷 ConnectionAborted。
    threading.Thread(target=reaper_loop, daemon=True, name="reaper").start()
    threading.Thread(target=lock_loop, daemon=True, name="lock").start()
    # 模块2：定时真采集（默认 1800s，OPS_AUTO_COLLECT_S 可覆盖；第一趟立即跑）
    threading.Thread(target=business.auto_loop,
                     args=(STOP, None), daemon=True, name="ops-auto-collect").start()
    if config.worker_enabled():
        threading.Thread(target=worker.worker_loop,
                         args=(config.default_lease(), STOP),
                         daemon=True, name="worker").start()
        log("内置 worker 已启动（只认 probe.*）")
    log("ops_engine %s 起于 http://%s:%d/ db=%s worker=%s"
        % (config.ENGINE_VERSION, config.host(), config.port(),
           db, config.worker_enabled()))
    try:
        srv.serve_forever(poll_interval=0.5)
    finally:
        STOP.set()
        srv.server_close()
        _STATE["srv"] = None
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


def start_in_thread(worker_on: bool = True) -> bool:
    """主程序调用：幂等起引擎。已在跑返回 True；不阻塞 GUI 线程。"""
    with _STATE["lock"]:
        t = _STATE["thread"]
        if t is not None and t.is_alive():
            return True
        if is_alive():
            return True                                      # 别的实例在跑，不抢
        if worker_on:
            os.environ.setdefault("OPS_WORKER", "1")
        os.environ.setdefault("OPS_PORT", str(config.port()))
        _STATE["err"] = ""
        t = threading.Thread(target=_serve, daemon=True, name="ops-engine-9192")
        _STATE["thread"] = t
        t.start()
    for _ in range(40):                                       # 最多等 4 秒探活
        if is_alive():
            return True
        if _STATE["err"]:
            return False
        time.sleep(0.1)
    return is_alive()


def request_stop() -> bool:
    """优雅停止：srv.shutdown() 必须在另一线程调（不能在 serve_forever 线程里）。"""
    srv = _STATE.get("srv")
    if srv is None:
        STOP.set()
        return False
    STOP.set()
    try:
        srv.shutdown()
    except Exception:                                        # noqa: BLE001
        pass
    return True


def is_alive() -> bool:
    """真探活：9192 health 200 且 engine 名对得上（防端口被别的服务占）。"""
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/ops/health" % config.port(),
                timeout=0.6) as r:
            d = json.loads(r.read().decode("utf-8", "replace") or "{}")
        return d.get("engine") == config.ENGINE_NAME
    except Exception:                                        # noqa: BLE001
        return False


def main() -> int:
    """命令行独立跑：python -m app.ops.engine（阻塞到 Ctrl-C）。"""
    os.environ.setdefault("OPS_WORKER", "1")
    rc = _serve()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
