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

from . import config, queue, sync_freshness

WX_BASE = config.WX_WEB_BASE
DD_CSV = config.WS / "抖店研究" / "crawl3" / "抖店_驳回商品_20261103.csv"
DD_RATE_CSV = config.WS / "抖店研究" / "crawl3" / "抖店_上货成功率_20261103.csv"

# 后台采集进度（POST 起线程，GET /status 轮询）
PROGRESS = {"running": False, "mode": "", "started_at": "", "finished_at": "",
            "log": [], "error": "", "report": {}, "progress": {}}
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


ABSOLUTE_PAGE_CAP = 200   # 安全上限：next_key 异常时也不会死循环（249 件约需 13 页）


def _progress(shop_id, name, status, page, got, cnt):
    """逐店逐状态进度（供 GET /api/ops/platform/status 轮询显示）。"""
    with _PLOCK:
        PROGRESS["progress"] = {"shop_id": shop_id, "shop_name": name,
                                "status": status, "page": page, "got": got,
                                "total": cnt, "at": now()}
        line = "%s · 状态%s 第%d页 已读 %d/%d" % (name, status, page, got, cnt)
        PROGRESS["log"].append(line)
        if len(PROGRESS["log"]) > 400:
            del PROGRESS["log"][:200]


def _record_sync(con, shop, out, started, full):
    """把本轮微信采集结果落新鲜度状态（P0.3.1）。

    ★ 快速刷新/定时循环不得把已「完整分页」的全量状态降级为 partial：
      那会让页面每 30 分钟自己退回 60/700 的不完整状态，等于每月自毁一次数据。
      快速刷新只更新读取时间，保留上一次全量的总数/明细/覆盖率/最后成功时间。
    """
    try:
        sync_freshness.ensure_tables(con)
        per = out.get("per_status") or {}
        all_complete = bool(per) and all(
            v.get("complete") for v in per.values()) and out.get("read_ok")
        basis = "complete" if (full and all_complete) else (
            "partial" if out.get("detail") else "none")
        if not full:
            prev = sync_freshness.for_shop(con, shop["appid"])
            if prev and prev.get("coverage_basis") == "complete":
                # 已有完整全量：本轮快速刷新不动 7 字段，只追加一条 note
                con.execute("UPDATE ops_sync_state SET note=?, updated_at=? WHERE shop_id=?",
                            ("快速刷新 @%s（保留上一次全量同步结果，不降级）" % now(),
                             now(), shop["appid"]))
                return
        sync_freshness.record(
            con, shop_id=shop["appid"], platform="wx",
            shop_name=out.get("shop_name"), source=WX_BASE + "/api/products",
            source_kind="wx_official_api", capture_started_at=started,
            capture_finished_at=now(), last_success_at="",
            total_count=out.get("total"), detail_rows=out.get("detail", 0),
            coverage_basis=basis, read_ok=bool(out.get("read_ok")),
            error=" | ".join(out.get("errors") or [])[:400],
            note=("全量同步（完整分页）" if full else "快速刷新（每状态 1 页）")
                 + "；逐状态 " + json.dumps(per, ensure_ascii=False)[:300])
    except Exception as e:                                    # noqa: BLE001
        out.setdefault("errors", []).append("sync_state: %s: %s" % (type(e).__name__, e))


def _wx_shop(con, shop, wx_map, dy_map, pages_per_status: int,
             full: bool = False) -> dict:
    """微信单店真采集。full=True → 翻完所有分页到 next_key 空 / 达权威总数。

    P0.3.1：真全量分页 + 商品ID去重 + 店铺隔离 + 逐店进度 + 失败记录 +
    最后成功时间（落 ops_sync_state）。绝不用接口总数冒充已取得全部明细。
    """
    sid = shop["appid"]
    name = shop.get("name") or sid
    hdr = {"X-Shop-Id": sid, "Accept": "application/json"}
    started = now()
    max_pages = ABSOLUTE_PAGE_CAP if full else pages_per_status
    out = {"shop_id": sid, "shop_name": name, "total": None, "read_ok": True,
           "errors": [], "detail": 0, "by_status": {}, "coverage": {},
           "per_status": {}, "started_at": started, "full": full}
    t0 = time.time()

    ov, ok, err, ms = _http(WX_BASE + "/api/overview", hdr, timeout=40)
    _record_fetch(con, sid, "/api/overview", "GET X-Shop-Id", ok,
                  1 if ok else 0, ms, err, "官方 API 权威总数")
    if not ok or not ov.get("ok"):
        out["read_ok"] = False
        out["errors"].append("overview: " + (err or str(ov)[:120]))
        _upsert_shop(con, shop, out, pages_per_status,
                     int((time.time() - t0) * 1000), full)
        _record_sync(con, shop, out, started, full)
        return out
    bs = ov.get("by_status") or {}
    bst = ov.get("by_status_text") or {}
    out["total"] = ov.get("total")
    out["by_status"] = bs
    out["status_text"] = bst

    # 全量同步：先清掉本店上一轮的镜像行（仅 ops.db 平台镜像表，可从 API 再生，
    # 不碰商品主库 v3.db；清的是「镜像明细」不是商品本身）。快速刷新不清。
    if full:
        con.execute("DELETE FROM ops_platform_product"
                    " WHERE shop_id=? AND source_kind='wx_official_api'", (sid,))

    seen = set()                       # 本店本轮 product_id 去重
    for st in WX_STATUS:
        cnt = int(bs.get(st) or 0)
        if cnt <= 0:
            continue
        cursor, got, pages = "0", 0, 0
        exhausted = False
        while pages < max_pages:
            u = "%s/api/products?status=%s&page_size=20&cursor=%s" % (
                WX_BASE, st, cursor)
            d, ok2, err2, ms2 = _http(u, hdr, timeout=60)
            data = (d or {}).get("data") or {}
            rows = data.get("rows") or []
            _record_fetch(con, sid, u, "GET X-Shop-Id", ok2, len(rows), ms2, err2,
                          "status=%s page=%d" % (st, pages + 1))
            if not ok2 or not d.get("ok"):
                out["read_ok"] = False
                out["errors"].append("products st=%s p=%d: %s" % (st, pages, err2))
                break
            for r in rows:
                pid = str(r.get("product_id") or "").strip()
                if not pid or pid in seen:
                    continue
                seen.add(pid)
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
            _progress(sid, name, st, pages + 1, got, cnt)
            nk = str(data.get("next_key") or "")
            pages += 1
            # 全量：翻到 next_key 空 或 去重后已达权威总数；快速：每状态只 1 页
            if not full:
                break
            if not nk or not rows:
                exhausted = True
                break
            if cnt and got >= cnt:
                exhausted = True
                break
            cursor = nk
        out["detail"] += got
        out["coverage"][st] = "%d/%d" % (got, cnt)
        out["per_status"][st] = {"got": got, "total": cnt, "pages": pages,
                                 "complete": bool(full and (exhausted or (cnt and got >= cnt)))}

    _upsert_shop(con, shop, out, pages_per_status,
                 int((time.time() - t0) * 1000), full)
    _record_sync(con, shop, out, started, full)
    return out


def _upsert_shop(con, shop, out, pages_per_status, elapsed_ms, full=False):
    sid = shop["appid"]
    per = out.get("per_status") or {}
    all_complete = bool(per) and all(v.get("complete") for v in per.values()) \
        and out.get("read_ok")
    if full and all_complete:
        note = ("权威总数=/api/overview；明细=/api/products 已翻完全部分页，"
                "去重后 %d/%d 件" % (out.get("detail", 0), out.get("total") or 0))
    elif full:
        note = ("全量同步未完成：逐状态 %s；失败项见错误列"
                % json.dumps(out.get("coverage") or {}, ensure_ascii=False))
    else:
        note = ("快速刷新：每状态只读第 1 页×20 件（未完整分页）；"
                "完整明细请点「全量同步」；逐状态 %s"
                % json.dumps(out.get("coverage") or {}, ensure_ascii=False))
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
         " | ".join(out.get("errors") or [])[:400], note))


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
    """P0.3.1 三种模式（只写 ops.db）：
      snapshot = 快速刷新：每状态只读第 1 页（20 件），如实标「未完成分页」
      full     = 全量同步：微信两店翻完所有分页（next_key 空 / 达权威总数）
      auto     = 定时内部用，等同 snapshot（不冒充新一轮浏览器采集）
    """
    full = (mode == "full")
    pages = 1 if not full else ABSOLUTE_PAGE_CAP
    report = {"mode": mode, "full": full, "pages_per_status": pages,
              "shops": [], "at": now(), "v3_readonly": True, "platform_writes": 0}
    con = queue.connect()
    try:
        ensure_tables(con)
        sync_freshness.ensure_tables(con)
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
                    # P0.3.1：抖店 3846 件后台真明细 + 新鲜度状态，与 auto_loop 同一口径。
                    # 老 _dy_shop 只有草稿/驳回汇总（7 行）与 total=None，不能当三店真数。
                    try:
                        from . import business as _biz
                        snap = _biz.import_dy_snapshot(con)
                        r["dy_snapshot"] = {
                            "inserted": snap.get("inserted"),
                            "source_time": snap.get("source_time"),
                            "freshness": (snap.get("freshness") or {}).get("freshness_text")}
                    except Exception as e:                    # noqa: BLE001
                        r.setdefault("errors", []).append(
                            "dy_snapshot: %s: %s" % (type(e).__name__, e))
                else:
                    r = _wx_shop(con, s, wx_map, dy_map, pages, full=full)
                report["shops"].append(r)
                PROGRESS["log"].append(
                    "%s %s total=%s detail=%s ok=%s%s" % (
                        r["shop_id"], r["shop_name"], r.get("total"),
                        r.get("detail"), r.get("read_ok"),
                        " [全量/完整分页]" if full else " [快速/未完整分页]"))
            except Exception as e:                            # noqa: BLE001
                report["shops"].append({"shop_id": s.get("appid"), "read_ok": False,
                                        "errors": ["%s: %s" % (type(e).__name__, e)]})
                PROGRESS["log"].append("ERR %s %s" % (s.get("appid"), e))
        report["product_rows"] = con.execute(
            "SELECT COUNT(*) FROM ops_platform_product").fetchone()[0]
        report["fetch_rows"] = con.execute(
            "SELECT COUNT(*) FROM ops_platform_fetch").fetchone()[0]
        report["sync_states"] = sync_freshness.all_states(con)
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
    # P0.3.1：给每店挂上真实新鲜度/覆盖率/最后成功时间/错误
    try:
        sync_freshness.ensure_tables(con)
        rows = sync_freshness.attach(rows, sync_freshness.all_states(con))
    except Exception:                                        # noqa: BLE001
        pass
    return {"shops": rows, "count": len(rows), "by_platform": by_platform,
            "note": "微信=官方 API 权威数；抖店=ego 页面快照，未取得的不编造；"
                    "新鲜度按数据真实时间判定，历史快照明确标注"}


def sync_status(con) -> dict:
    """P0.3.1 采集真实性总览（三店新鲜度/覆盖率/最后成功/错误）。"""
    sync_freshness.ensure_tables(con)
    states = sync_freshness.all_states(con)
    cols = con.execute("PRAGMA table_info(ops_sync_state)").fetchall()
    return {"states": states, "count": len(states),
            "columns": [c["name"] for c in cols],
            "stale_hours": {"wx": sync_freshness.STALE_HOURS_WX,
                            "dy": sync_freshness.STALE_HOURS_DY}}


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
