# -*- coding: utf-8 -*-
"""8.0 · P0.3.1 · 采集真实性与快照新鲜度（单一真源）

主人本轮原话对应的三件事：
  ① 「三家店铺商品数据不完整」→ 微信全量分页后才有资格算覆盖率，绝不拿接口总数冒充
  ② 「采集状态不真实」→ 每店记录 起止时间/来源/总数/明细数/失败原因/最后成功时间
  ③ 「快照过期却被当成最新数据」→ 按数据真实时间判新鲜度，超期明确显示「历史数据」

设计：
  * 只写独立 ops.db 的新表 ops_sync_state；不改任何已有表结构、不碰 v3.db（只读 ATTACH）
  * judge() 是**纯函数**（不碰数据库），便于 10 项离线测试；record()/for_shop() 才落库
  * 覆盖率 coverage 只有在「完整分页依据」齐备时才给数字，否则 None + 「未取得」
  * 数据新鲜度以**数据本身的时间**为准：
      微信 = 本轮真调官方 API 的完成时刻（实时数）
      抖店 = 快照文件的真实时间（历史文件 2026-10-29 就是历史，不因今天读了一次变新）

硬边界：零平台写调用、零编造、零密钥、零删除。
"""
from __future__ import annotations

import json
import time
from datetime import datetime

# 新鲜度阈值（小时）：超过即标「已过期 / 历史数据」
# 微信实时 API 数据 24h；抖店快照 24h（本例历史快照 14 天 → 必过期）
STALE_HOURS_WX = 24
STALE_HOURS_DY = 24
TIME_FMT = "%Y-%m-%d %H:%M:%S"

DDL = """
CREATE TABLE IF NOT EXISTS ops_sync_state (
    shop_id               TEXT PRIMARY KEY,
    platform              TEXT NOT NULL,
    shop_name             TEXT,
    source                TEXT,               -- 数据来源（接口 / 快照文件全路径）
    source_kind           TEXT,               -- wx_official_api | dy_ego_full | dy_ego_snapshot
    capture_started_at    TEXT,               -- 本轮采集开始
    capture_finished_at   TEXT,               -- 本轮采集结束
    snapshot_file_mtime   TEXT,               -- 快照文件真实时间（微信为空）
    last_success_at       TEXT,               -- 最后一次真正成功采集的时刻
    total_count           INTEGER,            -- 平台权威总数（未取得 = NULL，不写 0）
    detail_rows           INTEGER,            -- 实际读取到的去重明细数
    coverage              REAL,               -- 覆盖率；无完整分页依据 = NULL
    coverage_basis        TEXT,               -- complete | partial | none（覆盖率依据）
    read_ok               INTEGER DEFAULT 0,
    data_status           TEXT,               -- fresh | stale | missing | failed
    freshness_text        TEXT,               -- 中文一句话，给主人看
    age_hours             REAL,
    stale_after_hours     REAL,
    error                 TEXT,               -- 采集失败原因原文
    note                  TEXT,
    updated_at            TEXT
);
CREATE INDEX IF NOT EXISTS ix_ops_sync_at ON ops_sync_state(updated_at);
"""


def now() -> str:
    return time.strftime(TIME_FMT)


def ensure_tables(con) -> None:
    con.executescript(DDL)


def _parse(ts: str | None):
    """宽松解析本地时间字符串；解析不了返回 None（不猜）。"""
    if not ts:
        return None
    s = str(ts).strip().replace("T", " ")
    for fmt in (TIME_FMT, "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:len(fmt) + 2], fmt)
        except (ValueError, TypeError):
            continue
    return None


def judge(*, platform: str, source_kind: str, snapshot_file_mtime: str = "",
          capture_finished_at: str = "", last_success_at: str = "",
          total_count=None, detail_rows: int = 0, coverage_basis: str = "none",
          read_ok: bool = True, error: str = "", at: str = "") -> dict:
    """★ 纯函数：判新鲜度/覆盖率。不碰数据库、不碰网络，可离线单测。

    coverage_basis:
      complete = 分页翻到 next_key 空且 got>=总数 → 有资格算覆盖率
      partial  = 只读了一部分（快速刷新/被截断）→ 覆盖率留空，不伪造
      none     = 无逐件明细（只有汇总）→ 覆盖率留空

    返回字段即任务书 §三·任务3 的 8 项 + 判定结果，全部 JSON 可序列化。
    """
    ref_time = at or now()
    # 数据真实时间：抖店以快照文件时间为准；微信以本轮采集完成时间为准
    data_time = (snapshot_file_mtime or "").strip() if str(platform).startswith("dy") \
        else (capture_finished_at or last_success_at or "").strip()
    if not data_time:
        data_time = (last_success_at or capture_finished_at or snapshot_file_mtime or "").strip()

    stale_after = STALE_HOURS_DY if str(platform).startswith("dy") else STALE_HOURS_WX
    d_ref, d_data = _parse(ref_time), _parse(data_time)
    age_hours = round((d_ref - d_data).total_seconds() / 3600.0, 2) \
        if (d_ref and d_data) else None

    # 覆盖率：只有完整分页依据才给数字
    coverage = None
    if coverage_basis == "complete" and total_count and total_count > 0:
        coverage = round(min(1.0, float(detail_rows or 0) / float(total_count)), 4)
    elif coverage_basis == "partial" and total_count and total_count > 0:
        # 有数但没读全：给「已读/总数」的分子分母，但不叫覆盖率
        coverage = None

    total_known = total_count is not None and int(total_count) > 0
    has_detail = int(detail_rows or 0) > 0

    # 状态优先级：失败 > 无数据 > 过期 > 最新
    if not read_ok or (error and error.strip()):
        data_status = "failed"
        detail_txt = "采集失败" + ("（" + str(error)[:60] + "）" if error else "")
        freshness_text = detail_txt + "；当前显示的是上次留存的数据" if has_detail else detail_txt
    elif not total_known and not has_detail:
        data_status = "missing"
        freshness_text = "尚无数据（未采集成功过）"
    elif age_hours is None:
        data_status = "stale"
        freshness_text = "数据时间未知，按历史数据处理"
    elif age_hours > stale_after:
        data_status = "stale"
        days = round(age_hours / 24.0, 1)
        freshness_text = "已过期（历史数据，距今 %s 天）" % days
    else:
        data_status = "fresh"
        freshness_text = "最新（%.1f 小时内）" % (age_hours if age_hours is not None else 0)

    # 覆盖率文案：绝不在无完整分页依据时编造
    if coverage is not None:
        coverage_text = "%.1f%%（%d/%d，完整分页）" % (
            coverage * 100, int(detail_rows or 0), int(total_count))
    elif coverage_basis == "partial":
        coverage_text = "未取得（只读到 %d/%d 明细，快速刷新未完成分页）" % (
            int(detail_rows or 0), int(total_count or 0))
    elif total_known:
        coverage_text = "未取得（有平台总数但无逐件分页依据）"
    else:
        coverage_text = "未取得（平台逐件总数未开放）"

    return {
        "platform": platform,
        "source_kind": source_kind,
        "data_time": data_time,                 # 数据本身的真实时间
        "age_hours": age_hours,
        "stale_after_hours": stale_after,
        "total_count": total_count,
        "detail_rows": int(detail_rows or 0),
        "coverage": coverage,
        "coverage_basis": coverage_basis,
        "coverage_text": coverage_text,
        "read_ok": bool(read_ok),
        "data_status": data_status,            # fresh | stale | missing | failed
        "freshness_text": freshness_text,
        "is_historical": bool(data_status == "stale" and str(platform).startswith("dy")),
        "error": error or "",
        "judged_at": ref_time,
    }


def record(con, *, shop_id: str, platform: str, shop_name: str, source: str,
           source_kind: str, capture_started_at: str, capture_finished_at: str,
           snapshot_file_mtime: str = "", last_success_at: str = "",
           total_count=None, detail_rows: int = 0, coverage_basis: str = "none",
           read_ok: bool = True, error: str = "", note: str = "",
           new_capture: bool = True) -> dict:
    """落一条采集状态（upsert）。返回 judge() 的判定结果，供调用方直接回写页面。
    new_capture=False（如重读历史快照）时不推进「最后成功采集时间」。"""
    # 成功且确属新一轮采集，才更新「最后成功采集时间」；重读历史文件不推进
    success_at = last_success_at
    if not new_capture:
        prev = for_shop(con, shop_id)
        success_at = (prev or {}).get("last_success_at") or ""
    elif read_ok and not (error or "").strip():
        success_at = capture_finished_at or now()
    verdict = judge(
        platform=platform, source_kind=source_kind,
        snapshot_file_mtime=snapshot_file_mtime,
        capture_finished_at=capture_finished_at, last_success_at=success_at,
        total_count=total_count, detail_rows=detail_rows,
        coverage_basis=coverage_basis, read_ok=read_ok, error=error)
    con.execute(
        "INSERT INTO ops_sync_state(shop_id,platform,shop_name,source,source_kind,"
        "capture_started_at,capture_finished_at,snapshot_file_mtime,last_success_at,"
        "total_count,detail_rows,coverage,coverage_basis,read_ok,data_status,"
        "freshness_text,age_hours,stale_after_hours,error,note,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(shop_id) DO UPDATE SET"
        " platform=excluded.platform,shop_name=excluded.shop_name,source=excluded.source,"
        " source_kind=excluded.source_kind,capture_started_at=excluded.capture_started_at,"
        " capture_finished_at=excluded.capture_finished_at,"
        " snapshot_file_mtime=excluded.snapshot_file_mtime,"
        " last_success_at=excluded.last_success_at,total_count=excluded.total_count,"
        " detail_rows=excluded.detail_rows,coverage=excluded.coverage,"
        " coverage_basis=excluded.coverage_basis,read_ok=excluded.read_ok,"
        " data_status=excluded.data_status,freshness_text=excluded.freshness_text,"
        " age_hours=excluded.age_hours,stale_after_hours=excluded.stale_after_hours,"
        " error=excluded.error,note=excluded.note,updated_at=excluded.updated_at",
        (shop_id, platform, shop_name or "", source or "", source_kind or "",
         capture_started_at or "", capture_finished_at or "", snapshot_file_mtime or "",
         success_at or "", total_count, int(detail_rows or 0),
         verdict["coverage"], coverage_basis, 1 if read_ok else 0,
         verdict["data_status"], verdict["freshness_text"], verdict["age_hours"],
         verdict["stale_after_hours"], error or "", note or "", now()))
    return verdict


def for_shop(con, shop_id: str) -> dict | None:
    r = con.execute("SELECT * FROM ops_sync_state WHERE shop_id=?", (shop_id,)).fetchone()
    return dict(r) if r else None


def all_states(con) -> list:
    return [dict(r) for r in con.execute(
        "SELECT * FROM ops_sync_state ORDER BY platform, shop_id")]


def attach(shop_rows: list, states: list) -> list:
    """把新鲜度状态合并到 platform.summary / 首页三店卡的每一行（不改原 row）。

    P0.3.1 权威口径：**ops_sync_state 是三店 7 字段的唯一真源**。
    ops_platform_shop 里的 total/detail_rows 会被「快速刷新/定时循环」用 1 页数据覆盖，
    不能拿它当真数；这里用 sync_state 的（完整分页得来的）总数/明细/覆盖率覆盖之，
    否则页面会在定时跑完后自己退回「60/700」的不完整状态。
    """
    by = {str(s["shop_id"]): s for s in (states or [])}
    out = []
    for r in shop_rows or []:
        d = dict(r)
        s = by.get(str(d.get("shop_id")))
        if s:
            # ★ 以 sync_state 为真源覆盖 7 字段
            if s.get("total_count") is not None:
                d["total"] = s["total_count"]
            if s.get("detail_rows") is not None:
                d["detail_rows"] = s["detail_rows"]
            d["read_ok"] = bool(s.get("read_ok", 1))
            for k in ("source", "source_kind", "capture_started_at", "capture_finished_at",
                      "snapshot_file_mtime", "last_success_at", "data_status",
                      "freshness_text", "is_historical", "age_hours", "stale_after_hours",
                      "coverage", "coverage_basis", "error"):
                d[k] = s.get(k)
            v = judge(
                platform=s.get("platform") or d.get("platform") or "wx",
                source_kind=s.get("source_kind") or "",
                snapshot_file_mtime=s.get("snapshot_file_mtime") or "",
                capture_finished_at=s.get("capture_finished_at") or "",
                last_success_at=s.get("last_success_at") or "",
                total_count=d.get("total"), detail_rows=d.get("detail_rows") or 0,
                coverage_basis=s.get("coverage_basis") or "none",
                read_ok=bool(s.get("read_ok", 1)), error=s.get("error") or "")
            d["coverage_text"] = v["coverage_text"]
            d["freshness_text"] = s.get("freshness_text") or v["freshness_text"]
        else:
            d["coverage_text"] = "未取得（尚无采集记录）"
            d["freshness_text"] = "—"
            d["is_historical"] = False
            d["last_success_at"] = ""
            d["data_status"] = "missing"
            d["error"] = ""
        out.append(d)
    return out
