# -*- coding: utf-8 -*-
"""8.0 · 模块1 双平台真实商品中心 · 采集与回读（生产版 2026-11-10）

真数据源（全部只读，绝不虚构）：
  微信两店  9191 /api/overview（官方 API 权威总数 + 逐状态）
            9191 /api/products?status=&page_size=&cursor=（官方 API 逐商品明细）
  抖店      9191 /api/dd/overview（ego 页面快照汇总：草稿/上架包）
            9191 /api/dd/drafts（ego 快照逐行草稿商品）
            抖店研究/crawl3/抖店_驳回商品_20261103.csv（ego 快照导出的真实违规商品）

硬边界：
  * 只写独立 ops.db 的 ops_platform_* 三张表；v3.db 一律只读 ATTACH
  * 每个店铺显式带完整 shop_id（微信 X-Shop-Id 全 appid，不省略）
  * 拿不到的数据标 read_ok=0 + error 原文 + coverage，不用 0 冒充、不编造单件
  * 零发布、零改价改库存、零平台写调用、零密钥明文
"""
from __future__ import annotations

import csv
import json
import os
import threading
import time
import traceback
import urllib.request
from pathlib import Path

from . import config, queue

WX_BASE = config.WX_WEB_BASE
DD_CSV = config.WS / "抖店研究" / "crawl3" / "抖店_驳回商品_20261103.csv"
DD_RATE_CSV = config.WS / "抖店研究" / "crawl3" / "抖店_上货成功率_20261103.csv"

# 后台采集进度（POST 起线程，GET /status 轮询）
PROGRESS = {"running": False, "mode": "", "started_at": "", "finished_at": "",
            "log": [], "error": "", "report": {}}
_PLOCK = threading.Lock()

DDL = """
CREATE TABLE IF NOT EXISTS ops_platform_product (
    shop_id           TEXT NOT NULL,
    platform          TEXT NOT NULL,
    shop_name         TEXT,
    product_id        TEXT NOT NULL,
    title             TEXT,
    status_code       TEXT,
    status_text       TEXT,
    price_min         REAL,
    price_max         REAL,
    stock_total       INTEGER,
    sku_count         INTEGER,
    head_img          TEXT,
    source            TEXT,
    source_kind       TEXT NOT NULL,
    source_time       TEXT,
    fetched_at        TEXT,
    read_ok           INTEGER DEFAULT 1,
    local_product_id  INTEGER,
    match_state       TEXT,
    coverage          TEXT,
    note              TEXT,
    PRIMARY KEY (shop_id, source_kind, product_id)
);
CREATE INDEX IF NOT EXISTS ix_ops_pp_shop ON ops_platform_product(shop_id, platform);
CREATE INDEX IF NOT EXISTS ix_ops_pp_status ON ops_platform_product(shop_id, status_code);
CREATE INDEX IF NOT EXISTS ix_ops_pp_match ON ops_platform_product(match_state);

CREATE TABLE IF NOT EXISTS ops_platform_shop (
    shop_id       TEXT PRIMARY KEY,
    platform      TEXT NOT NULL,
    shop_name     TEXT,
    source_kind   TEXT,
    total         INTEGER,
    by_status     TEXT,
    status_text   TEXT,
    detail_rows   INTEGER,
    coverage      TEXT,
    read_ok       INTEGER DEFAULT 1,
    fetched_at    TEXT,
    elapsed_ms    INTEGER,
    error         TEXT,
    note          TEXT
);

CREATE TABLE IF NOT EXISTS ops_platform_fetch (
    fetch_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id     TEXT,
    source      TEXT,
    method      TEXT,
    http_ok     INTEGER,
    read_ok     INTEGER,
    rows        INTEGER,
    elapsed_ms  INTEGER,
    error       TEXT,
    fetched_at  TEXT,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS ix_ops_pf_at ON ops_platform_fetch(fetched_at);
"""


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def ensure_tables(con) -> None:
    con.executescript(DDL)


# --------------------------------------------------------------------------- #
# 只读 HTTP
# --------------------------------------------------------------------------- #
def _http(url: str, headers: dict | None = None, timeout: float = 30.0):
    t0 = time.time()
    req = urllib.request.Request(url, headers=headers or {"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
        d = json.loads(raw or "{}")
        return d, True, "", int((time.time() - t0) * 1000)
    except Exception as e:                                    # noqa: BLE001
        return None, False, "%s: %s" % (type(e).__name__, e), int((time.time() - t0) * 1000)


def _record_fetch(con, shop_id, source, method, http_ok, rows, ms, error, note=""):
    con.execute(
        "INSERT INTO ops_platform_fetch(shop_id,source,method,http_ok,read_ok,rows,"
        "elapsed_ms,error,fetched_at,note) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (shop_id, source, method, 1 if http_ok else 0, 1 if http_ok else 0,
         rows or 0, ms, error or "", now(), note))


# --------------------------------------------------------------------------- #
# 本地 ID 匹配：v3_upload_status 的 wx_product_id / dy_tid（只读 ATTACH）
# --------------------------------------------------------------------------- #
def _id_maps(con) -> tuple:
    wx, dy = {}, {}
    try:
        for r in con.execute(
                "SELECT product_id, wx_product_id, dy_tid FROM v3_upload_status"):
            if r["wx_product_id"] and str(r["wx_product_id"]).strip():
                wx[str(r["wx_product_id"]).strip()] = r["product_id"]
            if r["dy_tid"] and str(r["dy_tid"]).strip():
                dy[str(r["dy_tid"]).strip()] = r["product_id"]
    except Exception:                                        # noqa: BLE001
        pass
    return wx, dy


def _match(pid, wx_map, dy_map, platform) -> tuple:
    m = wx_map if platform == "wx" else dy_map
    lid = m.get(str(pid).strip()) if pid is not None else None
    return (lid, "matched" if lid else "unmatched")


# --------------------------------------------------------------------------- #
# 微信两店
# --------------------------------------------------------------------------- #
WX_STATUS = ("5", "0", "6", "11", "12", "13")


def _wx_shop(con, shop, wx_map, dy_map, pages_per_status: int) -> dict:
    sid = shop["appid"]
    name = shop.get("name") or sid
    hdr = {"X-Shop-Id": sid, "Accept": "application/json"}
    out = {"shop_id": sid, "shop_name": name, "total": None, "read_ok": True,
           "errors": [], "detail": 0, "by_status": {}}
    t0 = time.time()

    ov, ok, err, ms = _http(WX_BASE + "/api/overview", hdr, timeout=40)
    _record_fetch(con, sid, "/api/overview", "GET X-Shop-Id", ok,
                  1 if ok else 0, ms, err, "官方 API 权威总数")
    if not ok or not ov.get("ok"):
        out["read_ok"] = False
        out["errors"].append("overview: " + (err or str(ov)[:120]))
        _upsert_shop(con, shop, out, pages_per_status, int((time.time() - t0) * 1000))
        return out
    bs = ov.get("by_status") or {}
    bst = ov.get("by_status_text") or {}
    out["total"] = ov.get("total")
    out["by_status"] = bs
    out["status_text"] = bst

    for st in WX_STATUS:
        cnt = int(bs.get(st) or 0)
        if cnt <= 0:
            continue
        # 逐状态分页抓真实明细；每店每状态最多 pages_per_status 页，如实标 coverage
        cursor, got, pages = "0", 0, 0
        while pages < pages_per_status:
            u = "%s/api/products?status=%s&page_size=20&cursor=%s" % (WX_BASE, st, cursor)
            d, ok2, err2, ms2 = _http(u, hdr, timeout=60)
            rows = ((d or {}).get("data") or {}).get("rows") or []
            _record_fetch(con, sid, u, "GET X-Shop-Id", ok2, len(rows), ms2, err2,
                          "status=%s page=%d" % (st, pages + 1))
            if not ok2 or not d.get("ok"):
                out["read_ok"] = False
                out["errors"].append("products st=%s p=%d: %s" % (st, pages, err2))
                break
            for r in rows:
                pid = str(r.get("product_id") or "").strip()
                if not pid:
                    continue
                lid, mstate = _match(pid, wx_map, dy_map, "wx")
                con.execute(
                    "INSERT OR REPLACE INTO ops_platform_product("
                    "shop_id,platform,shop_name,product_id,title,status_code,status_text,"
                    "price_min,price_max,stock_total,sku_count,head_img,source,source_kind,"
                    "source_time,fetched_at,read_ok,local_product_id,match_state,coverage,note)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sid, "wx", name, pid, (r.get("title") or "")[:120], st,
                     r.get("status_text") or bst.get(st) or "",
                     r.get("price_min"), r.get("price_max"), r.get("stock_total"),
                     r.get("sku_count"), r.get("head_img"), u, "wx_official_api",
                     r.get("updated") or "", now(), 1, lid, mstate,
                     "%d/%d" % (got + 1, cnt),
                     "编辑状态:" + str(r.get("edit_status_text") or "")))
                got += 1
            nk = ((d or {}).get("data") or {}).get("next_key") or ""
            pages += 1
            if not nk or not rows:
                break
            cursor = nk
        out["detail"] += got
        out.setdefault("coverage", {})[st] = "%d/%d" % (got, cnt)

    _upsert_shop(con, shop, out, pages_per_status, int((time.time() - t0) * 1000))
    return out


def _upsert_shop(con, shop, out, pages_per_status, elapsed_ms):
    sid = shop["appid"]
    con.execute(
        "INSERT OR REPLACE INTO ops_platform_shop("
        "shop_id,platform,shop_name,source_kind,total,by_status,status_text,detail_rows,"
        "coverage,read_ok,fetched_at,elapsed_ms,error,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, "wx", out.get("shop_name"), "wx_official_api", out.get("total"),
         json.dumps(out.get("by_status") or {}, ensure_ascii=False),
         json.dumps(out.get("status_text") or {}, ensure_ascii=False),
         out.get("detail", 0),
         json.dumps(out.get("coverage") or {}, ensure_ascii=False),
         1 if out.get("read_ok") else 0, now(), elapsed_ms,
         " | ".join(out.get("errors") or [])[:400],
         "权威总数=/api/overview；明细=/api/products 分页，每状态最多 %d 页×20 件；"
         "完整明细可点「抓取全量」后台补齐" % pages_per_status))


# --------------------------------------------------------------------------- #
# 抖店（ego 页面快照 / CSV 口径，绝不冒充实时平台列表）
# --------------------------------------------------------------------------- #
def _dy_shop(con, shop, wx_map, dy_map) -> dict:
    sid = shop["appid"]
    name = shop.get("name") or sid
    out = {"shop_id": sid, "shop_name": name, "total": None, "read_ok": True,
           "errors": [], "detail": 0, "by_status": {}}
    t0 = time.time()

    # ① 汇总快照（草稿/上架包）
    ov, ok, err, ms = _http(WX_BASE + "/api/dd/overview", timeout=15)
    _record_fetch(con, sid, "/api/dd/overview", "GET", ok, 1 if ok else 0, ms, err,
                  "ego 页面快照汇总")
    if ok and ov.get("ok"):
        out["by_status"] = {"草稿箱": ov.get("drafts"), "待提交": ov.get("drafts_ready"),
                            "上架包": ov.get("packs"), "包内行": ov.get("packs_rows"),
                            "页面快照": ov.get("snapshots")}
        out["status_text"] = {"草稿箱": "ego 快照", "上架包": "本地货源包（非平台在售）"}
        out["note_ov"] = ov.get("note") or ""
    else:
        out["read_ok"] = False
        out["errors"].append("dd/overview: " + (err or str(ov)[:120]))

    # ② 草稿逐行（真实 PID/标题/状态/日期/来源文件）
    dr, ok2, err2, ms2 = _http(WX_BASE + "/api/dd/drafts", timeout=15)
    drafts = (dr or {}).get("rows") or []
    _record_fetch(con, sid, "/api/dd/drafts", "GET", ok2, len(drafts), ms2, err2,
                  "ego 快照逐行草稿")
    for r in drafts:
        pid = str(r.get("pid") or "").strip()
        if not pid:
            continue
        lid, mstate = _match(pid, wx_map, dy_map, "dy")
        con.execute(
            "INSERT OR REPLACE INTO ops_platform_product("
            "shop_id,platform,shop_name,product_id,title,status_code,status_text,"
            "source,source_kind,source_time,fetched_at,read_ok,local_product_id,"
            "match_state,coverage,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, "dy", name, pid, (r.get("title") or "")[:120], "draft",
             r.get("status") or "草稿", "/api/dd/drafts", "dy_ego_snapshot",
             r.get("date") or "", now(), 1, lid, mstate, "快照逐行",
             "来源文件:" + str(r.get("src") or "")))
        out["detail"] += 1

    # ③ 真实违规/驳回商品（ego 快照导出 CSV）
    if DD_CSV.exists():
        try:
            n = 0
            with open(DD_CSV, "r", encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    pid = str(r.get("商品ID") or "").strip()
                    if not pid:
                        continue
                    lid, mstate = _match(pid, wx_map, dy_map, "dy")
                    con.execute(
                        "INSERT OR REPLACE INTO ops_platform_product("
                        "shop_id,platform,shop_name,product_id,title,status_code,status_text,"
                        "source,source_kind,source_time,fetched_at,read_ok,local_product_id,"
                        "match_state,coverage,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (sid, "dy", name, pid, (r.get("商品名称") or "")[:120],
                         "reject", r.get("审核状态") or "审核驳回", str(DD_CSV),
                         "dy_csv_reject", r.get("驳回时间") or "", now(), 1, lid, mstate,
                         "快照 CSV 逐行",
                         "违规类型:%s｜处理建议:%s" % (
                             (r.get("违规类型") or "")[:40],
                             (r.get("处理建议") or "")[:120])))
                    n += 1
            _record_fetch(con, sid, str(DD_CSV), "CSV(只读)", True, n, 0, "",
                          "ego 快照导出的真实违规商品")
            out["detail"] += n
            out["by_status"]["审核驳回"] = n
        except Exception as e:                                # noqa: BLE001
            out["read_ok"] = False
            out["errors"].append("驳回CSV: %s: %s" % (type(e).__name__, e))
            _record_fetch(con, sid, str(DD_CSV), "CSV(只读)", False, 0, 0, str(e), "")
    else:
        _record_fetch(con, sid, str(DD_CSV), "CSV(只读)", False, 0, 0,
                      "文件不存在", "如实记录，不编造")

    # ④ 上货成功率 CSV（只有汇总，无逐件 → 只记一笔，不落逐商品行）
    if DD_RATE_CSV.exists():
        try:
            txt = DD_RATE_CSV.read_text(encoding="utf-8-sig", errors="replace")
            _record_fetch(con, sid, str(DD_RATE_CSV), "CSV(只读)", True, 0, 0, "",
                          "汇总口径：" + txt.replace("\r", " ").replace("\n", " ")[:200])
        except Exception as e:                                # noqa: BLE001
            _record_fetch(con, sid, str(DD_RATE_CSV), "CSV(只读)", False, 0, 0, str(e), "")

    # 抖店「平台实际在售总数」没有免签接口 → 如实标 None，不拿 packs_rows 冒充
    con.execute(
        "INSERT OR REPLACE INTO ops_platform_shop("
        "shop_id,platform,shop_name,source_kind,total,by_status,status_text,detail_rows,"
        "coverage,read_ok,fetched_at,elapsed_ms,error,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, "dy", name, "dy_ego_snapshot", None,
         json.dumps(out["by_status"], ensure_ascii=False),
         json.dumps(out.get("status_text") or {}, ensure_ascii=False),
         out["detail"], "快照逐行(草稿+驳回)/汇总(上架包)",
         1 if out["read_ok"] else 0, now(), int((time.time() - t0) * 1000),
         " | ".join(out["errors"])[:400],
         "抖店无免签逐商品接口：在售逐件明细与平台实际在售总数未取得，"
         "如实留空，不用货源包行数/本地商品数冒充。草稿与审核驳回为 ego 快照逐行真数据。"
         "完整在售列表需固定浏览器脚本(ego)另跑。"))
    return out


# --------------------------------------------------------------------------- #
# 采集总入口
# --------------------------------------------------------------------------- #
def collect_all(mode: str = "snapshot", shop_filter: str = "") -> dict:
    """mode=snapshot 每状态抓 1 页(快) / full 每状态最多 10 页。只写 ops.db。"""
    pages = 1 if mode != "full" else 10
    report = {"mode": mode, "pages_per_status": pages, "shops": [], "at": now(),
              "v3_readonly": True, "platform_writes": 0}
    con = queue.connect()
    try:
        ensure_tables(con)
        wx_map, dy_map = _id_maps(con)
        d, ok, err, ms = _http(WX_BASE + "/api/shops", timeout=8)
        _record_fetch(con, "", "/api/shops", "GET", ok,
                      len((d or {}).get("shops") or []), ms, err, "三店台账")
        if not ok or not d.get("ok"):
            report["error"] = "店铺台账读取失败：" + (err or "")
            return report
        shops = [s for s in (d.get("shops") or []) if s.get("enabled") is not False]
        if shop_filter:
            shops = [s for s in shops if shop_filter in str(s.get("appid"))]
        for s in shops:
            try:
                if s.get("kind") == "doudian":
                    r = _dy_shop(con, s, wx_map, dy_map)
                else:
                    r = _wx_shop(con, s, wx_map, dy_map, pages)
                report["shops"].append(r)
                PROGRESS["log"].append(
                    "%s %s total=%s detail=%s ok=%s" % (
                        r["shop_id"], r["shop_name"], r.get("total"),
                        r.get("detail"), r.get("read_ok")))
            except Exception as e:                            # noqa: BLE001
                report["shops"].append({"shop_id": s.get("appid"), "read_ok": False,
                                        "errors": ["%s: %s" % (type(e).__name__, e)]})
                PROGRESS["log"].append("ERR %s %s" % (s.get("appid"), e))
        report["product_rows"] = con.execute(
            "SELECT COUNT(*) FROM ops_platform_product").fetchone()[0]
        report["fetch_rows"] = con.execute(
            "SELECT COUNT(*) FROM ops_platform_fetch").fetchone()[0]
    finally:
        con.close()
    return report


def collect_async(mode: str = "snapshot", shop_filter: str = "") -> dict:
    with _PLOCK:
        if PROGRESS["running"]:
            return {"started": False, "reason": "已有采集在跑", "progress": PROGRESS}
        PROGRESS.update({"running": True, "mode": mode, "started_at": now(),
                         "finished_at": "", "log": [], "error": "", "report": {}})

    def _run():
        rep = {}
        try:
            rep = collect_all(mode, shop_filter)
        except Exception as e:                                # noqa: BLE001
            PROGRESS["error"] = "%s: %s\n%s" % (type(e).__name__, e,
                                                traceback.format_exc(limit=3))
        with _PLOCK:
            PROGRESS["running"] = False
            PROGRESS["finished_at"] = now()
            PROGRESS["report"] = rep

    threading.Thread(target=_run, daemon=True, name="ops-platform-collect").start()
    return {"started": True, "mode": mode}


def status() -> dict:
    with _PLOCK:
        d = dict(PROGRESS)
        d["log"] = list(d["log"])
        return d


# --------------------------------------------------------------------------- #
# 回读（页面/接口用，只读）
# --------------------------------------------------------------------------- #
def summary(con) -> dict:
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM ops_platform_shop ORDER BY platform, shop_id")]
    for r in rows:
        try:
            r["by_status"] = json.loads(r.get("by_status") or "{}")
        except Exception:                                    # noqa: BLE001
            pass
        try:
            r["status_text"] = json.loads(r.get("status_text") or "{}")
        except Exception:                                    # noqa: BLE001
            pass
        try:
            r["coverage"] = json.loads(r.get("coverage") or "{}") \
                if str(r.get("coverage") or "").startswith("{") else r.get("coverage")
        except Exception:                                    # noqa: BLE001
            pass
    by_platform = {}
    for r in rows:
        by_platform[r["platform"]] = by_platform.get(r["platform"], 0) + 1
    return {"shops": rows, "count": len(rows), "by_platform": by_platform,
            "note": "微信=官方 API 权威数；抖店=ego 页面快照，未取得的不编造"}


def products(con, shop: str = "", status_code: str = "", q: str = "",
             limit: int = 300) -> list:
    sql = "SELECT * FROM ops_platform_product WHERE 1=1"
    a = []
    if shop:
        sql += " AND shop_id=?"
        a.append(shop)
    if status_code:
        sql += " AND status_code=?"
        a.append(status_code)
    if q:
        sql += " AND (title LIKE ? OR product_id LIKE ?)"
        a += ["%%%s%%" % q, "%%%s%%" % q]
    sql += " ORDER BY platform, shop_id, status_code, product_id LIMIT ?"
    a.append(int(limit))
    return [dict(r) for r in con.execute(sql, a)]


def stats(con) -> dict:
    out = {"by_shop": [], "matched": 0, "unmatched": 0, "total": 0}
    for r in con.execute(
            "SELECT shop_id, platform, COUNT(*) n,"
            " SUM(CASE WHEN match_state='matched' THEN 1 ELSE 0 END) m,"
            " SUM(CASE WHEN read_ok=1 THEN 1 ELSE 0 END) ok"
            " FROM ops_platform_product GROUP BY shop_id, platform"):
        out["by_shop"].append(dict(r))
        out["total"] += r["n"] or 0
        out["matched"] += r["m"] or 0
    out["unmatched"] = out["total"] - out["matched"]
    return out


def fetches(con, limit: int = 100) -> list:
    return [dict(r) for r in con.execute(
        "SELECT * FROM ops_platform_fetch ORDER BY fetch_id DESC LIMIT ?", (int(limit),))]


def status_stats(con) -> dict:
    """状态统计：按店 × 状态，逐行来自真实 by_status 或明细，绝不虚构。"""
    out = []
    for r in con.execute("SELECT shop_id, platform, shop_name, by_status, status_text,"
                         " detail_rows, total, read_ok, source_kind FROM ops_platform_shop"):
        bs = {}
        try:
            bs = json.loads(r["by_status"] or "{}")
        except Exception:                                    # noqa: BLE001
            pass
        out.append({"shop_id": r["shop_id"], "platform": r["platform"],
                    "shop_name": r["shop_name"], "total": r["total"],
                    "by_status": bs, "detail_rows": r["detail_rows"],
                    "read_ok": r["read_ok"], "source_kind": r["source_kind"]})
    return {"shops": out}
