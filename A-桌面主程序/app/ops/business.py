# -*- coding: utf-8 -*-
"""8.0 · 模块2 真实业务运营 + 操作反馈（生产版 2026-11-10）

主人本轮原话对应的四件事：
  ① 抖店没 API → 登录商品后台直接抓，做定时脚本，商品明细全都能抓到
  ② 短视频运营 / 限时限量购 / 活动 / 账号运营 —— 都要在页面里看得见、能操作
  ③ 操作交由程序弄，但「起码我要能看到操作以后的反馈状态」
  ④ 不能虚构曝光、点击、违规、活动数据

真数据源（全部只读，绝不编造）：
  抖店后台 ego 快照  抖店研究/crawl4/all-products-trim.json  （/ffa/g/list 全店 3846 件真商品）
  抖店浏览器刷新器   抖店研究/上货引擎/out/dy-timed-snapshot.json（dy_timed_capture.mjs 产物）
  微信官方 API       9191 /api/shops + /api/dd/overview（探活账号状态）
  视频真数据         v3.db v3_image role='video' 194 行（只读 ATTACH）
  规则库真条目       抖店研究/rules/index.json 39 条官方规则
  平台真商品         ops_platform_product（模块1 已落的微信 700/143 + 抖店草稿/驳回）

硬边界：
  * 只写 ops.db 的 ops_operation / ops_campaign / ops_content_publish / ops_platform_product
  * v3.db 只读 ATTACH，一行不改；零发布、零改价改库存、零报名、零自动上传真实视频
  * 每个按钮 = 一条 ops_operation（running→done/failed），页面立刻回读反馈
  * 「平台可报名活动列表」「实时在售总数」未取得就标未取得，不用规则库/本地库冒充
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
import urllib.request
from pathlib import Path

from . import config, queue

# 后台进度 / 下次定时时间（只给页面看，不参与判定）
AUTO = {"enabled": True, "interval_s": 1800, "last_at": "", "last_result": "",
        "next_at": "", "runs": 0, "errors": 0}
_ALOCK = threading.Lock()
_COLLECT_LOCK = threading.Lock()          # 定时采集与手动按钮串行，防并发写入
_RLOCK = threading.RLock()                # 生成草稿去重，避免重复点按钮堆脏数据

# 抖店真快照（历史 ego 全店快照 + 浏览器脚本刷新后的新快照）
DY_FULL = config.WS / "抖店研究" / "crawl4" / "all-products-trim.json"
DY_TIMED = config.WS / "抖店研究" / "上货引擎" / "out" / "dy-timed-snapshot.json"
RULES_INDEX = config.WS / "抖店研究" / "rules" / "index.json"
WX_BASE = config.WX_WEB_BASE

DDL = """
-- 操作反馈：每个按钮一条，主人能看见「点了以后到底怎么样了」
CREATE TABLE IF NOT EXISTS ops_operation (
    op_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    module       TEXT NOT NULL,
    action       TEXT NOT NULL,
    target       TEXT,
    actor        TEXT,
    state        TEXT NOT NULL,          -- running / done / failed / manual_pending
    started_at   TEXT,
    finished_at  TEXT,
    elapsed_ms   INTEGER,
    result       TEXT,                  -- 真结果（条数 / 接口状态 / 文件路径）
    error        TEXT,
    note         TEXT
);
CREATE INDEX IF NOT EXISTS ix_ops_op_at ON ops_operation(op_id DESC);

-- 活动 / 限时限量购 报名资料草稿（只本地准备，need_human=1，绝不真报名）
CREATE TABLE IF NOT EXISTS ops_campaign (
    campaign_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT NOT NULL,       -- rule_based / flash_sale(限时限量购)
    title          TEXT,
    source_rule_id TEXT,
    source_url     TEXT,
    source_type    TEXT,                -- official / reprint / local_calc
    platform       TEXT,
    shop_id        TEXT,
    item_count     INTEGER DEFAULT 0,
    cost_yuan      REAL,
    price_yuan     REAL,
    margin         REAL,
    state          TEXT DEFAULT 'draft',-- draft / manual_pending / approved / rejected
    need_human     INTEGER DEFAULT 1,
    created_at     TEXT,
    updated_at     TEXT,
    note           TEXT
);

-- 内容视频 / 商品详情视频 准备草稿（state 绝不出现 published，绝不自动发）
CREATE TABLE IF NOT EXISTS ops_content_publish (
    pub_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    content_type TEXT NOT NULL,         -- product_head_video / content_video
    platform     TEXT,
    shop_id      TEXT,
    product_id   INTEGER,
    image_id     INTEGER,
    title        TEXT,
    file_path    TEXT,
    state        TEXT DEFAULT 'prepared', -- prepared / manual_pending / approved / rejected
    need_human   INTEGER DEFAULT 1,
    created_at   TEXT,
    updated_at   TEXT,
    note         TEXT
);
CREATE INDEX IF NOT EXISTS ix_ops_cp_type ON ops_content_publish(content_type, state);
"""


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def ensure_tables(con) -> None:
    con.executescript(DDL)


# --------------------------------------------------------------------------- #
# ① 抖店后台真商品明细入 ops.db（3846 件 ego 全店快照，不是 v3.db 本地数）
# --------------------------------------------------------------------------- #
def _dy_source() -> tuple:
    """优先浏览器脚本新快照，退回历史全店快照；返回 (path, rows, mtime_text, is_fresh)。
    定时脚本未跑 / 登录态失效时保留旧快照，不写空数据（页面如实标历史）。"""
    for p, fresh in ((DY_TIMED, True), (DY_FULL, False)):
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8-sig") or "[]")
            rows = d.get("rows") if isinstance(d, dict) else d
            if isinstance(rows, list) and rows:
                return (p, rows,
                        str(d.get("captured_at") or time.strftime(
                            "%Y-%m-%d %H:%M:%S",
                            time.localtime(p.stat().st_mtime)))
                        if isinstance(d, dict) else time.strftime(
                            "%Y-%m-%d %H:%M:%S",
                            time.localtime(p.stat().st_mtime)), fresh)
        except Exception:                                     # noqa: BLE001
            continue
    return (None, [], "", False)


def import_dy_snapshot(con) -> dict:
    """把抖店后台真商品逐行落 ops_platform_product + 更新该店权威总数（明确标快照口径）。"""
    path, rows, mtime, fresh = _dy_source()
    out = {"file": str(path) if path else "", "source_time": mtime,
           "fresh_browser": fresh, "rows_in_file": len(rows), "inserted": 0,
           "by_status": {}, "by_draft": {}, "read_ok": False, "error": ""}
    if not rows:
        out["error"] = "抖店后台快照文件不存在或为空（需跑 dy_timed_capture.mjs 或保留 all-products-trim.json）"
        return out
    wx_map, dy_map = {}, {}
    try:
        for r in con.execute("SELECT product_id, wx_product_id, dy_tid FROM v3_upload_status"):
            if r["dy_tid"] and str(r["dy_tid"]).strip():
                dy_map[str(r["dy_tid"]).strip()] = r["product_id"]
    except Exception:                                         # noqa: BLE001
        pass
    sid, name = "dd_lzl", "抖店·LZL乐群云家具"
    bs, bd = {}, {}
    for r in rows:
        pid = str(r.get("pid") or r.get("product_id") or "").strip()
        if not pid:
            continue
        st = str(r.get("status", "?"))
        dr = str(r.get("draft", "?"))
        ck = str(r.get("check", "?"))
        bs[st] = bs.get(st, 0) + 1
        bd[dr] = bd.get(dr, 0) + 1
        # 价格单位：快照为「分」（115700=1157 元，与 初筛-全店3846.csv 吊牌价一致）
        dp, mp = r.get("dp"), r.get("mp")
        pmin = round(float(dp) / 100, 2) if isinstance(dp, (int, float)) else None
        pmax = round(float(mp) / 100, 2) if isinstance(mp, (int, float)) else None
        lid = dy_map.get(pid)
        con.execute(
            "INSERT OR REPLACE INTO ops_platform_product("
            "shop_id,platform,shop_name,product_id,title,status_code,status_text,"
            "price_min,price_max,stock_total,sku_count,source,source_kind,source_time,"
            "fetched_at,read_ok,local_product_id,match_state,coverage,note)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, "dy", name, pid, str(r.get("name") or r.get("title") or "")[:120],
             st, "draft=%s check=%s（快照原值，未臆测中文状态）" % (dr, ck),
             pmin, pmax, r.get("stock"), r.get("sku_count"),
             str(path), "dy_ego_full", mtime, now(), 1, lid,
             "matched" if lid else "unmatched",
             "ego 后台 /ffa/g/list 全店逐行快照",
             "类目:%s｜%s" % (str(r.get("cname") or "")[:40],
                             "浏览器脚本新快照" if fresh else "历史全店快照(非实时)")))
        out["inserted"] += 1
    out["by_status"], out["by_draft"] = bs, bd
    # 权威总数 = 后台快照逐行数，明确「非实时 API」，不拿本地 v3.db / 货源包行冒充
    con.execute(
        "INSERT OR REPLACE INTO ops_platform_shop("
        "shop_id,platform,shop_name,source_kind,total,by_status,status_text,detail_rows,"
        "coverage,read_ok,fetched_at,elapsed_ms,error,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, "dy", name, "dy_ego_full", len(rows),
         json.dumps(bs, ensure_ascii=False),
         json.dumps({"draft": bd, "口径": "status/draft/check 均为后台快照原值"},
                    ensure_ascii=False),
         out["inserted"],
         "ego 后台 /ffa/g/list 全店逐行快照 @%s%s" % (
             mtime, "（浏览器脚本刷新）" if fresh else "（历史快照，非实时）"),
         1, now(), 0, "",
         "抖店后台 %d 件真商品（含草稿/在售，逐行有 ID/标题/价/库存/类目）。"
         "来源 ego 浏览器快照 %s，非官方 API 实时数；定时脚本刷新后更新。"
         "平台可报名活动、曝光点击等仍未取得，不编造。" % (len(rows), mtime)))
    out["read_ok"] = True
    return out


def dy_products(con, q: str = "", limit: int = 200) -> list:
    sql = "SELECT * FROM ops_platform_product WHERE source_kind='dy_ego_full'"
    a = []
    if q:
        sql += " AND (title LIKE ? OR product_id LIKE ?)"
        a += ["%%%s%%" % q, "%%%s%%" % q]
    sql += " ORDER BY product_id LIMIT ?"
    a.append(int(limit))
    return [dict(r) for r in con.execute(sql, a)]


def snapshot_status(con) -> dict:
    path, rows, mtime, fresh = _dy_source()
    n = con.execute("SELECT COUNT(*) FROM ops_platform_product"
                    " WHERE source_kind='dy_ego_full'").fetchone()[0]
    by = {}
    for r in con.execute("SELECT status_code, COUNT(*) n FROM ops_platform_product"
                         " WHERE source_kind='dy_ego_full' GROUP BY status_code"):
        by[str(r["status_code"])] = r["n"]
    return {"db_rows": n, "file": str(path) if path else "", "file_mtime": mtime,
            "fresh_browser": fresh, "rows_in_file": len(rows),
            "by_status": by, "timed": dict(AUTO),
            "capture_script": str(config.WS / "上货台" / "app" / "ops" / "dy_timed_capture.mjs"),
            "note": "历史快照非实时；浏览器脚本产物更新后下一次定时采集自动入库"}


# --------------------------------------------------------------------------- #
# ③ 操作反馈：每个按钮全生命周期留痕
# --------------------------------------------------------------------------- #
def _op(con, module, action, target, actor, state, result="", error="",
        started="", note="") -> int:
    cur = con.execute(
        "INSERT INTO ops_operation(module,action,target,actor,state,started_at,"
        "finished_at,elapsed_ms,result,error,note) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (module, action, target or "", actor or "gui", state, started,
         now() if state != "running" else "", 0, result, error, note))
    return cur.lastrowid


def _op_done(con, op_id, started_ts, result, note=""):
    con.execute(
        "UPDATE ops_operation SET state='done',finished_at=?,elapsed_ms=?,result=?,note=?"
        " WHERE op_id=?",
        (now(), int((time.time() - started_ts) * 1000), result, note, op_id))


def _op_fail(con, op_id, started_ts, err):
    con.execute(
        "UPDATE ops_operation SET state='failed',finished_at=?,elapsed_ms=?,error=?"
        " WHERE op_id=?",
        (now(), int((time.time() - started_ts) * 1000), err[:600], op_id))


def feedback(con, limit: int = 60) -> list:
    return [dict(r) for r in con.execute(
        "SELECT * FROM ops_operation ORDER BY op_id DESC LIMIT ?", (int(limit),))]


# --------------------------------------------------------------------------- #
# ② 五大业务工作台：真数据 + 真动作（只本地，零平台写）
# --------------------------------------------------------------------------- #
def _http(url: str, timeout: float = 8.0):
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}"), True, ""
    except Exception as e:                                    # noqa: BLE001
        return {}, False, "%s: %s" % (type(e).__name__, e)


def account_check(con) -> dict:
    """真探活：9191 店铺台账 + 抖店快照概览；回写 ops_account 的 login_status/last_check_at。"""
    shops, ok, err = _http(WX_BASE + "/api/shops")
    dd, ok2, err2 = _http(WX_BASE + "/api/dd/overview")
    status = "probe_ok" if ok else "probe_failed"
    results = []
    for s in (shops.get("shops") or []):
        sid = s.get("appid")
        # 微信官方接口能列出且 has_secret → 凭据在本机、台账可读（不等于平台在线，如实写）
        st = "credentials_local_ok" if (ok and s.get("has_secret")) else "unknown"
        try:
            con.execute(
                "UPDATE ops_account SET login_status=?,last_check_at=?,"
                " browser_engine=CASE WHEN platform='dy' THEN 'ego' ELSE browser_engine END,"
                " note=? WHERE account_id=?",
                (st, now(), "9191 /api/shops 台账探活 @%s；凭据不入库" % now(), sid))
        except Exception:                                     # noqa: BLE001
            pass
        results.append({"shop_id": sid, "name": s.get("name"),
                        "login_status": st, "has_secret": bool(s.get("has_secret"))})
    if ok2 and dd.get("ok"):
        try:
            con.execute("UPDATE ops_account SET login_status=?,last_check_at=?,note=?"
                        " WHERE account_id='dd_lzl'",
                        ("ego_snapshot_ok", now(),
                         "ego 页面快照可读：%s；店铺状态=%s" % (
                             now(), (dd.get("shop") or {}).get("state") or "?")))
        except Exception:                                     # noqa: BLE001
            pass
        results.append({"shop_id": "dd_lzl", "name": "抖店·LZL乐群云家具",
                        "login_status": "ego_snapshot_ok",
                        "shop_state": (dd.get("shop") or {}).get("state")})
    return {"shops_api_ok": ok, "dd_overview_ok": ok2, "status": status,
            "errors": [x for x in (err, err2) if x], "accounts": results,
            "note": "login_status 是本机台账/快照探活结果，不是平台实时登录态"}


def account_status(con) -> list:
    return [dict(r) for r in con.execute(
        "SELECT account_id,platform,display_name,external_id,browser_engine,"
        "browser_profile_ref,login_status,last_check_at,enabled,note"
        " FROM ops_account ORDER BY platform,account_id")]


def video_prepare(con) -> dict:
    """真读 v3.db 194 条视频 + 关联商品，生成两类准备草稿（绝不发布）。"""
    try:
        vids = [dict(r) for r in con.execute(
            "SELECT i.image_id,i.product_id,i.current_local_path,i.original_local_path,"
            " i.dl_state,p.original_title FROM v3_image i"
            " LEFT JOIN v3_product p ON p.product_id=i.product_id"
            " WHERE i.role='video' ORDER BY i.image_id DESC")]
    except Exception as e:                                    # noqa: BLE001
        return {"error": "v3.db 只读视频查询失败：%s" % e, "prepared": 0}
    with _RLOCK:
        existing = con.execute(
            "SELECT COUNT(*) FROM ops_content_publish").fetchone()[0]
        if existing:
            return {"video_rows": len(vids), "product_head_video": 0,
                    "content_video": 0, "prepared": 0, "already": existing,
                    "note": "已有 %s 条准备草稿，本次跳过（防重复生成）；"
                            "商品详情视频 ≠ 内容视频发布，零自动发布" % existing}
    n_head = n_content = 0
    for v in vids:
        pid = v["product_id"]
        fp = (v.get("current_local_path") or v.get("original_local_path") or "")
        title = (v.get("original_title") or "")[:80]
        # A 商品详情头视频（微信，E1/E2 已验证链路，但仍停在人工确认前）
        con.execute(
            "INSERT INTO ops_content_publish(content_type,platform,shop_id,product_id,"
            "image_id,title,file_path,state,need_human,created_at,updated_at,note)"
            " VALUES('product_head_video','wx','wx6ff10d09ff0ee823',?,?,?,?,"
            "'prepared',1,?,?,?)",
            (pid, v["image_id"], title, fp, now(), now(),
             "商品详情页头图视频；微信 4 步上传链路已备，本条仅准备素材，未上传未发布"))
        n_head += 1
        # B 内容视频发布准备（发表时才能挂商品，官方原文；不自动发）
        con.execute(
            "INSERT INTO ops_content_publish(content_type,platform,shop_id,product_id,"
            "image_id,title,file_path,state,need_human,created_at,updated_at,note)"
            " VALUES('content_video','dy','dd_lzl',?,?,?,?,'prepared',1,?,?,?)",
            (pid, v["image_id"], title, fp, now(), now(),
             "抖音/视频号内容视频：官方只允许发表时关联商品，本条仅备素材，需人工发布"))
        n_content += 1
    return {"video_rows": len(vids), "product_head_video": n_head,
            "content_video": n_content, "prepared": n_head + n_content,
            "note": "两类严格分开：商品详情视频 ≠ 内容视频发布；零自动发布"}


def video_rows(con, content_type: str = "", limit: int = 200) -> list:
    sql = "SELECT * FROM ops_content_publish WHERE 1=1"
    a = []
    if content_type:
        sql += " AND content_type=?"
        a.append(content_type)
    sql += " ORDER BY pub_id DESC LIMIT ?"
    a.append(int(limit))
    return [dict(r) for r in con.execute(sql, a)]


def campaign_prepare(con) -> dict:
    """活动运营：规则库真条目 + 平台真商品成本利润 → 报名资料草稿；活动列表未取得如实标注。"""
    rules = []
    if RULES_INDEX.exists():
        try:
            allr = json.loads(RULES_INDEX.read_text(encoding="utf-8") or "[]")
            for r in allr:
                t = str(r.get("title") or "")
                c = str(r.get("category") or "")
                if any(k in (t + c) for k in ("活动", "促销", "营销", "价格", "大促", "限时", "秒杀", "报名")):
                    rules.append(r)
        except Exception:                                     # noqa: BLE001
            pass
    # 平台真商品做成本利润核算（售价 / 吊牌价 来自抖店后台快照，单位已转元）
    goods = [dict(r) for r in con.execute(
        "SELECT product_id,title,price_min,price_max,stock_total FROM ops_platform_product"
        " WHERE source_kind='dy_ego_full' AND price_min IS NOT NULL"
        " ORDER BY (price_max-price_min) DESC LIMIT 30")]
    n_rule = n_flash = 0
    for r in rules[:20]:
        con.execute(
            "INSERT INTO ops_campaign(kind,title,source_rule_id,source_url,source_type,"
            "platform,shop_id,item_count,state,need_human,created_at,updated_at,note)"
            " VALUES('rule_based',?,?,?,?,?, ?,0,'draft',1,?,?,?)",
            (str(r.get("title") or "")[:120], str(r.get("id") or ""),
             str(r.get("url") or ""), str(r.get("source_type") or "official"),
             "dy", "dd_lzl", now(), now(),
             "官方规则库真实条目；平台当前可报名活动列表未取得（需后台快照），本条仅归档依据"))
        n_rule += 1
    for g in goods[:20]:
        pmin, pmax = g["price_min"] or 0, g["price_max"] or 0
        margin = round(pmax - pmin, 2) if pmax and pmin else None
        con.execute(
            "INSERT INTO ops_campaign(kind,title,source_type,platform,shop_id,"
            "item_count,cost_yuan,price_yuan,margin,state,need_human,created_at,updated_at,note)"
            " VALUES('flash_sale',?,?,?,?,1,?,?,?,'draft',1,?,?,?)",
            ("限时限量购 · " + str(g["title"] or "")[:60], "local_calc",
             "dy", "dd_lzl", pmin, pmax, margin, now(), now(),
             "商品ID %s｜售价/吊牌价来自抖店后台真快照；成本利润仅本地核算，"
             "报名资格与活动档期未取得，需人工在后台确认，绝不自动报名" % g["product_id"]))
        n_flash += 1
    return {"rules_matched": len(rules), "rule_drafts": n_rule,
            "goods_calc": len(goods), "flash_sale_drafts": n_flash,
            "platform_campaigns": "未取得（平台无免签接口，需 ego 后台快照）",
            "prepared": n_rule + n_flash,
            "note": "报名资料草稿全部 need_human=1，零真实报名"}


def campaigns(con, limit: int = 200) -> list:
    return [dict(r) for r in con.execute(
        "SELECT * FROM ops_campaign ORDER BY campaign_id DESC LIMIT ?", (int(limit),))]


def _set_state(con, table, key, row_id, state, note) -> dict:
    allowed = ("ops_campaign", "ops_content_publish")
    keys = {"ops_campaign": "campaign_id", "ops_content_publish": "pub_id"}
    if table not in allowed:
        return {"error": "非法表"}
    col = keys[table]
    cur = con.execute(
        "UPDATE %s SET state=?,updated_at=?,note=COALESCE(note,'')||' | '||? WHERE %s=?" % (
            table, col), (state, now(), note, int(row_id)))
    return {"table": table, "id": int(row_id), "state": state,
            "changed": cur.rowcount,
            "note": "仅本地标记；真实发布/报名由人工在平台后台完成，程序不提交"}


# --------------------------------------------------------------------------- #
# 统一动作入口（按钮 → running → done/failed，反馈可读）
# --------------------------------------------------------------------------- #
def run_action(module: str, action: str, payload: dict | None = None,
               actor: str = "gui") -> dict:
    payload = payload or {}
    con = queue.connect()
    started = now()
    t0 = time.time()
    try:
        ensure_tables(con)
        op_id = _op(con, module, action, payload.get("target") or "", actor,
                    "running", started=started,
                    note="已收到，后台执行中；本页会回读结果")
    except Exception:                                         # noqa: BLE001
        con.close()
        return {"ok": False, "error": "反馈表不可写"}
    try:
        if action in ("collect", "auto.collect", "dy.collect"):
            # 顺序：先模块1 采集（会覆盖抖店 shop 行），再覆盖为 3846 真明细口径
            from . import platform
            with _COLLECT_LOCK:
                r1 = platform.collect_all("snapshot", "")
                r2 = import_dy_snapshot(con)
            result = "微信/抖店汇总已采；抖店后台真商品 %s 行入库（源 %s）；" \
                     "读取记录 %s 条" % (r2.get("inserted"), r2.get("source_time"),
                                        r1.get("fetch_rows"))
            res = {"collect": r1, "dy_snapshot": r2}
        elif action == "dy.snapshot":
            with _COLLECT_LOCK:
                res = import_dy_snapshot(con)
            result = "抖店后台真商品 %s 行入库，源时间 %s" % (
                res.get("inserted"), res.get("source_time"))
        elif action == "account.check":
            res = account_check(con)
            result = "账号探活 %s 家；9191 台账=%s 抖店快照=%s" % (
                len(res.get("accounts") or []), res.get("shops_api_ok"),
                res.get("dd_overview_ok"))
        elif action == "video.prepare":
            with _RLOCK:
                res = video_prepare(con)
            if res.get("error"):
                raise RuntimeError(res["error"])
            result = "真视频 %s 条 → 商品头视频草稿 %s / 内容视频草稿 %s（零发布）" % (
                res.get("video_rows"), res.get("product_head_video"),
                res.get("content_video"))
        elif action == "campaign.prepare":
            with _RLOCK:
                existing = con.execute("SELECT COUNT(*) FROM ops_campaign").fetchone()[0]
                if existing:
                    res = {"already": existing, "prepared": 0,
                           "note": "已有报名资料草稿，跳过防重复"}
                    result = "已有 %s 条活动/限时限量购草稿，本次跳过（可逐条人工确认）" % existing
                else:
                    res = campaign_prepare(con)
                    result = "官方规则 %s 条 + 限时限量购 %s 条报名资料草稿；平台活动列表未取得" % (
                        res.get("rule_drafts"), res.get("flash_sale_drafts"))
        elif action in ("campaign.approve", "campaign.reject",
                        "video.approve", "video.reject"):
            is_c = action.startswith("campaign")
            table = "ops_campaign" if is_c else "ops_content_publish"
            state = "approved" if action.endswith("approve") else "rejected"
            res = _set_state(con, table, "", int(payload.get("id") or 0), state,
                             "人工确认 @%s：%s（仅本地标记，未提交平台）" % (
                                 now(), state))
            result = "%s #%s → %s" % (table, payload.get("id"), state)
        elif action == "manual.pending":
            table = payload.get("table") or "ops_campaign"
            res = _set_state(con, table, "", int(payload.get("id") or 0),
                             "manual_pending", "转人工处理 @%s" % now())
            result = "%s #%s → 待人工处理" % (table, payload.get("id"))
        else:
            raise ValueError("未知动作：%s" % action)
        _op_done(con, op_id, t0, result, note=json.dumps(res, ensure_ascii=False)[:1500])
        return {"ok": True, "op_id": op_id, "state": "done",
                "result": result, "data": res}
    except Exception as e:                                    # noqa: BLE001
        err = "%s: %s\n%s" % (type(e).__name__, e, traceback.format_exc(limit=3))
        _op_fail(con, op_id, t0, err)
        return {"ok": False, "op_id": op_id, "state": "failed", "error": err[:600]}
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# 定时采集线程（Python 侧真定时，不依赖 ego 常驻）
# --------------------------------------------------------------------------- #
def auto_loop(stop: threading.Event, interval: int | None = None) -> None:
    gap = int(interval or os.environ.get("OPS_AUTO_COLLECT_S") or 1800)
    with _ALOCK:
        AUTO.update({"enabled": True, "interval_s": gap,
                     "next_at": time.strftime("%Y-%m-%d %H:%M:%S",
                                              time.localtime(time.time() + 20))})
    # 首趟延迟 20s：避开引擎启动 / 迁移 / 主窗口刷新的瞬时抢锁
    if stop.wait(20):
        return
    while not stop.is_set():
        try:
            r = run_action("定时采集", "auto.collect", {"target": "全部店铺"},
                           actor="scheduler")
            with _ALOCK:
                AUTO["last_at"] = now()
                AUTO["runs"] += 1
                AUTO["last_result"] = r.get("result") or (r.get("error") or "")[:120]
                if not r.get("ok"):
                    AUTO["errors"] += 1
                AUTO["next_at"] = time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(time.time() + gap))
        except Exception:                                     # noqa: BLE001
            with _ALOCK:
                AUTO["errors"] += 1
        stop.wait(gap)


def business_overview(con) -> dict:
    def n(sql, a=()):
        try:
            return con.execute(sql, a).fetchone()[0]
        except Exception:                                     # noqa: BLE001
            return None
    return {
        "dy_full_rows": n("SELECT COUNT(*) FROM ops_platform_product"
                          " WHERE source_kind='dy_ego_full'"),
        "wx_rows": n("SELECT COUNT(*) FROM ops_platform_product"
                     " WHERE platform='wx'"),
        "video_rows": n("SELECT COUNT(*) FROM v3_image WHERE role='video'"),
        "content_prepared": n("SELECT COUNT(*) FROM ops_content_publish"),
        "content_approved": n("SELECT COUNT(*) FROM ops_content_publish"
                              " WHERE state='approved'"),
        "campaign_drafts": n("SELECT COUNT(*) FROM ops_campaign"),
        "campaign_approved": n("SELECT COUNT(*) FROM ops_campaign"
                               " WHERE state='approved'"),
        "operations": n("SELECT COUNT(*) FROM ops_operation"),
        "op_done": n("SELECT COUNT(*) FROM ops_operation WHERE state='done'"),
        "op_failed": n("SELECT COUNT(*) FROM ops_operation WHERE state='failed'"),
        "accounts": n("SELECT COUNT(*) FROM ops_account"),
        "rules": (len(json.loads(RULES_INDEX.read_text(encoding="utf-8") or "[]"))
                  if RULES_INDEX.exists() else 0),
        "timed": dict(AUTO),
        "note": "全部为真实落库/只读计数；平台活动与曝光点击未取得，不编造",
    }


# --------------------------------------------------------------------------- #
# 自包含网页（供 headless 截图 / 真点击验收；Qt 子页另见 tab_ops.py）
# --------------------------------------------------------------------------- #
PAGE_HTML = r"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>8.0 · 真实业务运营中心</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#12151c;color:#e6e9ef;
 font:13px/1.6 "Microsoft YaHei",Consolas,sans-serif;padding:18px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:14px;margin:16px 0 8px;color:#9aa4b8}
.sub{color:#8b95a8;font-size:12px;margin-bottom:12px}
.bar{margin:8px 0;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
button{background:#2b5cff;color:#fff;border:0;border-radius:6px;padding:7px 13px;
 cursor:pointer;font-size:13px}button.ghost{background:#2b3242}button:disabled{opacity:.5}
input,select{background:#1c212c;color:#e6e9ef;border:1px solid #2b3242;
 border-radius:6px;padding:6px 8px;font-size:13px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:9px}
.card{background:#1c212c;border:1px solid #2b3242;border-radius:8px;padding:11px}
.k{color:#8b95a8;font-size:12px}.v{font-size:23px;font-weight:700;color:#6ee7a8}
.v.blue{color:#71b7ff}.v.warn{color:#ffc46b}
table{width:100%;border-collapse:collapse;background:#1c212c;border-radius:8px;overflow:hidden}
td,th{padding:6px 8px;border-bottom:1px solid #262d3b;text-align:left;font-size:12px}
th{color:#8b95a8;background:#191e28;position:sticky;top:0}
.tag{display:inline-block;padding:1px 6px;border-radius:9px;background:#26344a;
 color:#9cc4ff;font-size:11px;margin:1px 2px 1px 0}
.tag.ok{background:#1e4330;color:#7fe0a5}.tag.bad{background:#4a2630;color:#ff9b9b}
.tag.warn{background:#4a3a20;color:#ffc46b}
#fb{color:#ffc46b;font-size:12px;margin-left:6px}
.mono{font-family:Consolas,monospace;font-size:11px;color:#aab3c4}
.note{color:#8b95a8;font-size:11px;margin-top:4px}
.box{max-height:280px;overflow:auto}
</style>
<h1>8.0 · 真实业务运营中心 <span class="tag">模块2</span></h1>
<div class="sub">抖店后台真商品 / 短视频 / 限时限量购·活动 / 账号 / 操作反馈 —— 每个按钮都留痕，结果立刻回读。零发布零改价零报名。</div>
<div class="bar">
 <button id="bCollect">⟳ 立即抓一次（抖店后台 + 微信）</button>
 <button id="bAccount" class="ghost">检查账号状态</button>
 <button id="bVideo" class="ghost">生成视频发布准备</button>
 <button id="bCamp" class="ghost">生成活动/限时限量购资料</button>
 <button id="bAll" class="ghost">↻ 刷新全部</button>
 <span id="fb"></span>
</div>
<div class="grid" id="ov"></div>

<h2>① 操作反馈（点了以后到底怎么样了 · 最新在上）</h2>
<div class="box"><table id="tOps"><tr><th>#</th><th>模块</th><th>动作</th><th>状态</th>
<th>结果</th><th>错误</th><th>开始</th><th>完成</th><th>耗时</th></tr></table></div>

<h2>② 抖店后台真商品明细（ego /ffa/g/list 全店快照 · 不是本地 v3.db）</h2>
<div class="bar"><input id="qDy" placeholder="搜商品ID / 标题" style="width:220px">
 <button id="bDy" class="ghost">筛选</button><span id="dyMeta" class="mono"></span></div>
<div class="box"><table id="tDy"><tr><th>商品ID</th><th>标题</th><th>状态原值</th>
<th>售价</th><th>吊牌价</th><th>库存</th><th>本地匹配</th><th>源时间</th><th>说明</th></tr></table></div>

<h2>③ 短视频运营（商品详情视频 ≠ 内容视频发布，严格分开）</h2>
<div class="box"><table id="tVideo"><tr><th>#</th><th>类型</th><th>平台</th><th>商品ID</th>
<th>标题</th><th>状态</th><th>需人工</th><th>说明</th><th>操作</th></tr></table></div>

<h2>④ 限时限量购 / 活动运营（规则库真条目 + 平台真商品成本利润；活动列表未取得如实标）</h2>
<div class="box"><table id="tCamp"><tr><th>#</th><th>类型</th><th>标题</th><th>来源</th>
<th>商品数</th><th>售价</th><th>吊牌</th><th>差额</th><th>状态</th><th>操作</th></tr></table></div>

<h2>⑤ 账号运营（真探活结果，不是编造的登录态）</h2>
<div class="box"><table id="tAcc"><tr><th>平台</th><th>名称</th><th>完整ID</th>
<th>浏览器引擎</th><th>登录检查</th><th>最后检查</th><th>启用</th><th>备注</th></tr></table></div>
<div id="err" style="color:#ff8b8b"></div>
<script>
function esc(s){return String(s==null?'':s).replace(/[&<>]/g,function(c){
 return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c]})}
function get(u){return fetch(u).then(function(r){return r.json()})}
function post(u,b){return fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},
 body:JSON.stringify(b||{})}).then(function(r){return r.json()})}
function rows(d){return (d&&(d.rows||d.data))||[]}
function tag(s){var c=/done|ok|approved|成功/.test(s)?'ok':(/failed|rejected|失败/.test(s)?'bad':'warn');
 return '<span class="tag '+c+'">'+esc(s)+'</span>'}
function fill(id,trs){var t=document.getElementById(id);
 t.innerHTML='<tr>'+t.rows[0].innerHTML+'</tr>'+trs.join('')}

function loadOverview(){return get('/api/ops/business/overview').then(function(d){
 var o=d.data||{},a=o.timed||{};
 document.getElementById('ov').innerHTML=[
  ['抖店后台真商品',o.dy_full_rows,'v blue'],['微信真商品行',o.wx_rows,'v blue'],
  ['真视频',o.video_rows,'v'],['视频发布准备',o.content_prepared,'v warn'],
  ['活动/限时限量购草稿',o.campaign_drafts,'v warn'],['账号',o.accounts,'v'],
  ['操作反馈条数',o.operations,'v'],['操作成功/失败',(o.op_done||0)+'/'+(o.op_failed||0),'v'],
  ['定时采集次数',a.runs||0,'v blue'],['上次采集',a.last_at||'—','v'],
  ['下次采集',a.next_at||'—','v'],['规则库真条目',o.rules,'v']
 ].map(function(x){return '<div class="card"><div class="k">'+esc(x[0])+
  '</div><div class="'+x[2]+'">'+esc(x[1])+'</div></div>'}).join('')})}

function loadOps(){return get('/api/ops/business/feedback?limit=40').then(function(d){
 fill('tOps',rows(d).map(function(r){return '<tr><td>'+r.op_id+'</td><td>'+esc(r.module)+
  '</td><td>'+esc(r.action)+'</td><td>'+tag(r.state)+'</td><td>'+esc(r.result)+
  '</td><td>'+esc(r.error||'')+'</td><td>'+esc(r.started_at)+'</td><td>'+esc(r.finished_at)+
  '</td><td>'+esc(r.elapsed_ms)+'ms</td></tr>'}))})}

function loadDy(){var q=document.getElementById('qDy').value;
 return Promise.all([get('/api/ops/business/snapshot'),
  get('/api/ops/business/dy_products?q='+encodeURIComponent(q)+'&limit=200')])
 .then(function(a){var s=(a[0].data||{});
  document.getElementById('dyMeta').textContent='落库 '+s.db_rows+' 行 · 源文件 '+
   (s.file_mtime||'—')+' · '+(s.fresh_browser?'浏览器新快照':'历史全店快照(非实时)')+
   ' · 定时下次 '+(s.timed&&s.timed.next_at||'—');
  fill('tDy',rows(a[1]).map(function(r){return '<tr><td class="mono">'+esc(r.product_id)+
   '</td><td>'+esc((r.title||'').slice(0,34))+'</td><td>'+esc(r.status_code)+
   '</td><td>'+esc(r.price_min)+'</td><td>'+esc(r.price_max)+'</td><td>'+esc(r.stock_total)+
   '</td><td>'+tag(r.match_state||'')+'</td><td>'+esc(r.source_time)+
   '</td><td>'+esc((r.note||'').slice(0,40))+'</td></tr>'}))})}

function loadVideo(){return get('/api/ops/business/content?limit=80').then(function(d){
 fill('tVideo',rows(d).map(function(r){return '<tr><td>'+r.pub_id+'</td><td>'+
  (r.content_type==='product_head_video'?'<span class="tag">商品详情视频</span>':
   '<span class="tag warn">内容视频</span>')+'</td><td>'+esc(r.platform)+'</td><td>'+
  esc(r.product_id)+'</td><td>'+esc((r.title||'').slice(0,26))+'</td><td>'+tag(r.state)+
  '</td><td>'+(r.need_human?'是':'否')+'</td><td>'+esc((r.note||'').slice(0,44))+
  '</td><td><button class="ghost" onclick="act(\'video.approve\',{id:'+r.pub_id+'})">人工确认</button></td></tr>'}))})}

function loadCamp(){return get('/api/ops/business/campaigns?limit=80').then(function(d){
 fill('tCamp',rows(d).map(function(r){return '<tr><td>'+r.campaign_id+'</td><td>'+
  (r.kind==='flash_sale'?'<span class="tag warn">限时限量购</span>':'<span class="tag">规则依据</span>')+
  '</td><td>'+esc((r.title||'').slice(0,34))+'</td><td>'+esc(r.source_type)+'</td><td>'+
  esc(r.item_count)+'</td><td>'+esc(r.cost_yuan)+'</td><td>'+esc(r.price_yuan)+
  '</td><td>'+esc(r.margin)+'</td><td>'+tag(r.state)+'</td><td>'+
  '<button class="ghost" onclick="act(\'campaign.approve\',{id:'+r.campaign_id+'})">人工确认</button></td></tr>'}))})}

function loadAcc(){return get('/api/ops/business/accounts').then(function(d){
 fill('tAcc',rows(d).map(function(r){return '<tr><td>'+esc(r.platform)+'</td><td>'+
  esc(r.display_name)+'</td><td class="mono">'+esc(r.account_id)+'</td><td>'+
  esc(r.browser_engine)+'</td><td>'+tag(r.login_status||'unknown')+'</td><td>'+
  esc(r.last_check_at)+'</td><td>'+esc(r.enabled)+'</td><td>'+esc((r.note||'').slice(0,44))+
  '</td></tr>'}))})}

function loadAll(){document.getElementById('fb').textContent='读取中…';
 return Promise.all([loadOverview(),loadOps(),loadDy(),loadVideo(),loadCamp(),loadAcc()])
 .then(function(){document.getElementById('fb').textContent='已刷新 '+new Date().toLocaleTimeString()})
 .catch(function(e){document.getElementById('err').textContent=String(e)})}

window.act=function(action,payload){
 var fb=document.getElementById('fb');fb.textContent='已提交 '+action+'，后台执行中…';
 post('/api/ops/business/action',{module:'业务工作台',action:action,payload:payload})
 .then(function(r){fb.textContent=(r.ok?'✔ ':'✘ ')+(r.result||r.error||'');return loadAll()})
 .catch(function(e){fb.textContent='请求失败 '+String(e)})};

function btn(id,action){document.getElementById(id).onclick=function(){window.act(action,{})}}
btn('bCollect','collect');btn('bAccount','account.check');btn('bVideo','video.prepare');
btn('bCamp','campaign.prepare');
document.getElementById('bAll').onclick=loadAll;
document.getElementById('bDy').onclick=loadDy;
loadAll();
setInterval(loadOps,6000);
</script>
"""
