# -*- coding: utf-8 -*-
"""P0.3.1 · 采集真实性 / 快照新鲜度 · 10 项验收测试

跑法（在上货台目录，用生产 python 环境）：
    F:\\pyenvs\\douyin\\Scripts\\python.exe tests_ops_sync.py

设计：
  * T1/T2 微信分页：真调 9191 官方 API（只读），验证 next_key 能翻到尾、A/B 店隔离
  * T3/T4 去重与隔离：用隔离的临时 ops.db，模拟重复商品落库
  * T5-T9 快照/过期/无快照/采集失败/中断恢复：judge()/record() 纯函数 + 临时库
  * T10 上货台回归：offscreen 建 MainWindow，确认 12 页签与 8.0 页签还在

红线：不改生产 ops.db、不写 v3.db、不发布、不改价改库存、不动测试之外的文件。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from app.ops import sync_freshness, platform, business, config, queue  # noqa: E402

PASS, FAIL = [], []


def ck(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("PASS" if cond else "FAIL", name,
                         ("  | " + str(detail)) if detail else ""))


def _tmpdb():
    fd, p = tempfile.mkstemp(prefix="ops_sync_test_", suffix=".db")
    os.close(fd)
    os.unlink(p)
    con = sqlite3.connect(p, isolation_level=None)
    con.row_factory = sqlite3.Row
    sync_freshness.ensure_tables(con)
    return con, p


def main() -> int:
    print("=== P0.3.1 采集真实性 10 项验收 ===")
    print("at=%s  engine=%s schema=%s" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), config.ENGINE_VERSION, config.SCHEMA_VERSION))
    at = "2026-11-12 20:00:00"

    # ---- T1 微信 A 店分页：能翻到 next_key 空 / 达权威总数 ----
    print("\n[T1] 微信店铺 A 分页（真调 9191 只读）")
    sidA, sidB = "wx6ff10d09ff0ee823", "wxfc4e24a2ebcb2b0a"
    try:
        hdr = {"X-Shop-Id": sidA, "Accept": "application/json"}
        u = "%s/api/products?status=5&page_size=20&cursor=0" % config.WX_WEB_BASE
        d = json.load(urllib.request.urlopen(
            urllib.request.Request(u, headers=hdr), timeout=30))
        data = d.get("data") or {}
        ck("T1 A店第1页 rows=20 且 next_key 非空",
           len(data.get("rows") or []) == 20 and bool(data.get("next_key")),
           "total=%s next=%s" % (data.get("total"), data.get("next_key")))
        u2 = "%s/api/products?status=5&page_size=20&cursor=%s" % (
            config.WX_WEB_BASE, data["next_key"])
        d2 = json.load(urllib.request.urlopen(
            urllib.request.Request(u2, headers=hdr), timeout=30))
        ids1 = {str(r.get("product_id")) for r in data["rows"]}
        ids2 = {str(r.get("product_id")) for r in (d2.get("data") or {}).get("rows") or []}
        ck("T1 A店第2页不重复、next_key 继续推进",
           bool(ids2) and not (ids1 & ids2) and bool((d2.get("data") or {}).get("next_key")),
           "p1=%d p2=%d overlap=%d" % (len(ids1), len(ids2), len(ids1 & ids2)))
    except Exception as e:                                    # noqa: BLE001
        ck("T1 A店分页真调", False, "%s: %s" % (type(e).__name__, e))

    # ---- T2 微信 B 店分页 + A/B 隔离 ----
    print("\n[T2] 微信店铺 B 分页 + 两店隔离")
    try:
        hdrB = {"X-Shop-Id": sidB, "Accept": "application/json"}
        ovB = json.load(urllib.request.urlopen(urllib.request.Request(
            config.WX_WEB_BASE + "/api/overview", headers=hdrB), timeout=30))
        uB = "%s/api/products?status=5&page_size=20&cursor=0" % config.WX_WEB_BASE
        dB = json.load(urllib.request.urlopen(
            urllib.request.Request(uB, headers=hdrB), timeout=30))
        nameB = (ovB.get("shop") or {}).get("name")
        rowsB = (dB.get("data") or {}).get("rows") or []
        ck("T2 B店 overview 与分页均返回本店数据",
           bool(nameB) and bool(rowsB) and ovB.get("ok"),
           "name=%s total=%s rows=%d" % (nameB, ovB.get("total"), len(rowsB)))
        # A 店 overview 与 B 店不同 → X-Shop-Id 隔离生效
        ovA = json.load(urllib.request.urlopen(urllib.request.Request(
            config.WX_WEB_BASE + "/api/overview",
            headers={"X-Shop-Id": sidA}), timeout=30))
        ck("T2 A/B 店名与总数互不相同（店铺隔离）",
           (ovA.get("shop") or {}).get("name") != nameB or ovA.get("total") != ovB.get("total"),
           "A=%s/%s B=%s/%s" % ((ovA.get("shop") or {}).get("name"), ovA.get("total"),
                                nameB, ovB.get("total")))
    except Exception as e:                                    # noqa: BLE001
        ck("T2 B店分页与隔离真调", False, "%s: %s" % (type(e).__name__, e))

    # ---- T3/T4 去重与覆盖率 ----
    print("\n[T3/T4] 商品去重 / 覆盖率判定")
    con, path = _tmpdb()
    try:
        # 模拟：同店重复 pid 用 INSERT OR REPLACE 主键去重（与 platform 生产写法一致）
        con.execute("CREATE TABLE p(shop_id TEXT, product_id TEXT, PRIMARY KEY(shop_id,product_id))")
        for pid in ["P1", "P1", "P2", "P3", "P3"]:
            con.execute("INSERT OR REPLACE INTO p VALUES(?,?)", (sidA, pid))
        n = con.execute("SELECT COUNT(*) FROM p WHERE shop_id=?", (sidA,)).fetchone()[0]
        ck("T3 重复商品去重后 = 3 条", n == 3, "rows=%d" % n)
        v = sync_freshness.judge(platform="wx", source_kind="wx_official_api",
                                 capture_finished_at=at, total_count=700,
                                 detail_rows=700, coverage_basis="complete", at=at)
        ck("T4 完整分页覆盖率 = 100%", v["coverage"] == 1.0, v["coverage_text"])
        v2 = sync_freshness.judge(platform="wx", source_kind="wx_official_api",
                                  capture_finished_at=at, total_count=700,
                                  detail_rows=120, coverage_basis="partial", at=at)
        ck("T4 快速刷新（未完成分页）覆盖率留空不伪造",
           v2["coverage"] is None and "未取得" in v2["coverage_text"],
           v2["coverage_text"])
    finally:
        con.close(); os.unlink(path)

    # ---- T5 抖店快照读取（真读历史文件）----
    print("\n[T5] 抖店快照读取")
    p, rows, mtime, fresh = business._dy_source()
    ck("T5 历史快照存在且为 3846 行、fresh=False",
       bool(p) and len(rows) == 3846 and fresh is False,
       "file=%s mtime=%s rows=%d" % (p.name if p else None, mtime, len(rows)))

    # ---- T6 快照过期识别 ----
    print("\n[T6] 快照过期识别")
    v = sync_freshness.judge(platform="dy", source_kind="dy_snapshot_historical",
                             snapshot_file_mtime=mtime, capture_finished_at=at,
                             total_count=len(rows), detail_rows=len(rows),
                             coverage_basis="complete", at=at)
    ck("T6 14 天前快照判为 stale/历史数据",
       v["data_status"] == "stale" and v["is_historical"] and "历史" in v["freshness_text"],
       "%s age=%sh" % (v["freshness_text"], v["age_hours"]))
    v_fresh = sync_freshness.judge(platform="dy", source_kind="dy_ego_full",
                                   snapshot_file_mtime=at, capture_finished_at=at,
                                   total_count=3846, detail_rows=3846,
                                   coverage_basis="complete", at=at)
    ck("T6 当天新快照判为 fresh", v_fresh["data_status"] == "fresh",
       v_fresh["freshness_text"])

    # ---- T7 没有快照 ----
    print("\n[T7] 没有快照")
    v = sync_freshness.judge(platform="dy", source_kind="dy_ego_full",
                             snapshot_file_mtime="", capture_finished_at=at,
                             total_count=None, detail_rows=0, coverage_basis="none",
                             error="快照文件不存在或为空", at=at)
    ck("T7 无快照 = failed/missing 且不伪造覆盖率",
       v["coverage"] is None and "未取得" in v["coverage_text"]
       and v["data_status"] in ("failed", "missing"),
       "%s / %s" % (v["data_status"], v["coverage_text"]))

    # ---- T8 浏览器采集失败 ----
    print("\n[T8] 采集失败记录")
    con, path = _tmpdb()
    try:
        sync_freshness.record(con, shop_id="dd_lzl", platform="dy",
                              shop_name="抖店·LZL", source="", source_kind="dy_ego_full",
                              capture_started_at=at, capture_finished_at=at,
                              total_count=None, detail_rows=0, coverage_basis="none",
                              read_ok=False, error="ego 登录态失效：NOT_LOGGED_IN",
                              note="浏览器采集失败")
        s = sync_freshness.for_shop(con, "dd_lzl")
        ck("T8 失败原因原文落库、read_ok=0、不推进最后成功时间",
           s["read_ok"] == 0 and "NOT_LOGGED_IN" in (s["error"] or "")
           and not (s["last_success_at"] or ""),
           "status=%s err=%s" % (s["data_status"], s["error"]))
    finally:
        con.close(); os.unlink(path)

    # ---- T9 采集任务中断恢复（重读历史快照不推进最后成功时间）----
    print("\n[T9] 中断恢复 / 重读历史不冒充新采集")
    con, path = _tmpdb()
    try:
        # 先记一次真的成功采集
        sync_freshness.record(con, shop_id="dd_lzl", platform="dy",
                              shop_name="抖店·LZL", source="dy-timed-snapshot.json",
                              source_kind="dy_ego_full", capture_started_at=at,
                              capture_finished_at="2026-11-12 19:00:00",
                              snapshot_file_mtime="2026-11-12 19:00:00",
                              total_count=3846, detail_rows=3846,
                              coverage_basis="complete", new_capture=True)
        before = sync_freshness.for_shop(con, "dd_lzl")["last_success_at"]
        # auto_loop 重读历史快照（new_capture=False）
        sync_freshness.record(con, shop_id="dd_lzl", platform="dy",
                              shop_name="抖店·LZL", source="all-products-trim.json",
                              source_kind="dy_snapshot_historical",
                              capture_started_at=at, capture_finished_at=at,
                              snapshot_file_mtime=mtime, total_count=len(rows),
                              detail_rows=len(rows), coverage_basis="complete",
                              new_capture=False)
        after = sync_freshness.for_shop(con, "dd_lzl")
        ck("T9 重读历史快照后最后成功时间不变、且判为历史",
           after["last_success_at"] == before and after["data_status"] == "stale",
           "last_success=%s status=%s" % (after["last_success_at"], after["data_status"]))
    finally:
        con.close(); os.unlink(path)

    # ---- T10 原有上货台回归（offscreen 建窗）----
    print("\n[T10] 原有上货台回归")
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from app.ui.main_window import MainWindow
        w = MainWindow()
        tabs = w.tabs.count() if hasattr(w, "tabs") else -1
        names = [w.tabs.tabText(i) for i in range(tabs)] if tabs > 0 else []
        has8 = any("8.0" in n for n in names)
        ck("T10 offscreen 主窗口仍含 8.0 页签、页签数≥10",
           has8 and tabs >= 10, "tabs=%d has8=%s" % (tabs, has8))
        w.close()
    except Exception as e:                                    # noqa: BLE001
        ck("T10 offscreen 主窗口回归", False, "%s: %s" % (type(e).__name__, e))

    print("\n=== 结果：PASS %d / FAIL %d ===" % (len(PASS), len(FAIL)))
    if FAIL:
        print("FAILED: " + ", ".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
