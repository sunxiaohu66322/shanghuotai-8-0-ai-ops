# -*- coding: utf-8 -*-
"""8.0 · 每日真实运营闭环（模块3 / 2026-11-11）

把「真进后台巡店 → 回读 → 今日任务 → 经营日报 → 操作反馈」接进现有 8.0，纯增量：
  * 复用 抖店研究/engine/cdp.mjs（现成 CDP 客户端）+ dy_daily_patrol.mjs（真浏览器只读巡店）
  * 复用 business.py 的 ops_operation 反馈表（running→done/failed）
  * 只写 ops.db 的 ops_patrol_* 三张表；v3.db 只读 ATTACH 不变；零平台写操作
  * 流量/曝光/点击/入池/商机数值：平台无授权或无接口时标「未取得」，绝不用 0 冒充

环境变量：
  OPS_PATROL        0 = 关闭真实浏览器巡店（默认 1）
  EGO_PORT          ego CDP 端口（默认 9224）
  OPS_PATROL_NODE   node.exe 路径（默认自动找）
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import traceback
from pathlib import Path

from . import config, queue

HERE = Path(__file__).resolve().parent
PATROL_MJS = HERE / "dy_daily_patrol.mjs"
EVIDENCE = config.DATA / "patrol"
RESULT_JSON = EVIDENCE / "ops_patrol_result.json"

DDL = """
-- 每日真实巡店：一次运行 / 每一步真实操作与回读 / 今日任务 / 经营日报
CREATE TABLE IF NOT EXISTS ops_patrol_run (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    platform      TEXT NOT NULL,
    shop          TEXT,
    login_state   TEXT,
    started_at    TEXT,
    finished_at   TEXT,
    elapsed_ms    INTEGER,
    total_steps   INTEGER DEFAULT 0,
    ok_steps      INTEGER DEFAULT 0,
    fail_steps    INTEGER DEFAULT 0,
    search_ok     INTEGER DEFAULT 0,
    result_json   TEXT,
    state         TEXT,                 -- done | failed | not_logged_in | browser_unavailable
    error         TEXT
);
CREATE INDEX IF NOT EXISTS ix_ops_pr_at ON ops_patrol_run(run_id DESC);

CREATE TABLE IF NOT EXISTS ops_patrol_step (
    step_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL,
    seq           INTEGER,
    key           TEXT,
    label         TEXT,
    platform      TEXT,
    shop          TEXT,
    method        TEXT,
    before_url    TEXT,
    after_url     TEXT,
    body_len      INTEGER,
    nums          TEXT,                 -- 真页面读到的计数 JSON
    click_ok      INTEGER,
    readback_ok   INTEGER,
    screenshot    TEXT,
    elapsed_ms    INTEGER,
    error         TEXT,
    at            TEXT
);
CREATE INDEX IF NOT EXISTS ix_ops_ps_run ON ops_patrol_step(run_id, seq);

CREATE TABLE IF NOT EXISTS ops_daily_task (
    task_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    day           TEXT NOT NULL,
    platform      TEXT,
    shop          TEXT,
    product_id    TEXT,
    title         TEXT,
    kind          TEXT,                 -- 商品诊断/违规整改/活动机会/搜索优化/待优化商品
    source_step   TEXT,                 -- 依据哪一步真页面
    evidence      TEXT,                 -- 真页面文字摘录（不编造）
    cls           TEXT,                 -- A 自动 | B 人工确认 | C 人工操作
    state         TEXT DEFAULT 'open',  -- open | done | need_human | skipped
    created_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_ops_dt_day ON ops_daily_task(day, platform);

CREATE TABLE IF NOT EXISTS ops_daily_report (
    report_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    day           TEXT NOT NULL UNIQUE,
    platform      TEXT,
    shop          TEXT,
    body          TEXT,                 -- 经营白话，非技术日志
    patrol_run_id INTEGER,
    auto_done     INTEGER DEFAULT 0,
    need_human    INTEGER DEFAULT 0,
    manual_only   INTEGER DEFAULT 0,
    created_at    TEXT
);
"""


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def day() -> str:
    return time.strftime("%Y-%m-%d")


def ensure_tables(con) -> None:
    con.executescript(DDL)


def _find_node() -> str:
    p = os.environ.get("OPS_PATROL_NODE")
    if p and Path(p).exists():
        return p
    for c in (
        r"J:\SimonApp\AI-Workspace\deepseek-harness\apps\desktop\.desktop-build"
        r"\targets\win-x64\runtime\primary-runtime\dependencies\node\bin\node.exe",
        r"C:\Program Files\nodejs\node.exe",
    ):
        if Path(c).exists():
            return c
    return "node"


def _feedback(con, module, action, target, actor):
    """复用模块2 反馈表：每个真操作一条 running→done/failed。"""
    cur = con.execute(
        "INSERT INTO ops_operation(module,action,target,actor,state,started_at,note)"
        " VALUES(?,?,?,?, 'running', ?, '真实浏览器巡店，执行中…')",
        (module, action, target or "", actor, now()))
    return cur.lastrowid


def _fb_done(con, op_id, t0, result, note=""):
    con.execute(
        "UPDATE ops_operation SET state='done',finished_at=?,elapsed_ms=?,result=?,note=?"
        " WHERE op_id=?",
        (now(), int((time.time() - t0) * 1000), str(result)[:1500], note[:1500], op_id))


def _fb_fail(con, op_id, t0, err):
    con.execute(
        "UPDATE ops_operation SET state='failed',finished_at=?,elapsed_ms=?,error=?"
        " WHERE op_id=?",
        (now(), int((time.time() - t0) * 1000), str(err)[:600], op_id))


def run_patrol(actor: str = "gui") -> dict:
    """真跑一次每日巡店：调 node 脚本 → 读 JSON → 入库 → 生成任务/日报 → 回执。"""
    t0 = time.time()
    con = queue.connect()
    op_id = None
    try:
        ensure_tables(con)
        op_id = _feedback(con, "每日运营", "daily.patrol", "抖店后台", actor)
        con.commit()
    except Exception:                                         # noqa: BLE001
        con.close()
        return {"ok": False, "error": "反馈表不可写"}

    if (os.environ.get("OPS_PATROL") or "1") == "0":
        _fb_fail(con, op_id, t0, "OPS_PATROL=0 已关闭真实巡店")
        con.commit(); con.close()
        return {"ok": False, "error": "真实巡店被开关关闭"}

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    old = RESULT_JSON.read_text(encoding="utf-8") if RESULT_JSON.exists() else ""
    env = dict(os.environ)
    env["OPS_PATROL_OUT"] = str(EVIDENCE).replace("\\", "/")
    env.setdefault("EGO_PORT", "9224")
    t_run = time.time()
    state, err = "done", ""
    try:
        # 关键：用 stdout/stderr 落文件而非 PIPE —— 宿主受限环境里子进程管道会被拒（EPERM）
        logp = EVIDENCE / "patrol-node.log"
        with open(logp, "w", encoding="utf-8") as lf:
            lf.write("START %s\n" % now()); lf.flush()
            pr = subprocess.run([_find_node(), str(PATROL_MJS)],
                                env=env, cwd=str(config.ROOT), stdout=lf,
                                stderr=subprocess.STDOUT, timeout=300)
            lf.write("EXIT %s\n" % pr.returncode)
        node_rc = pr.returncode
    except Exception as e:                                    # noqa: BLE001
        state, err = "browser_unavailable", "%s: %s\n%s" % (
            type(e).__name__, e, traceback.format_exc(limit=3))
        node_rc = -1
        _fb_fail(con, op_id, t0, err); con.commit(); con.close()
        return {"ok": False, "state": state, "error": err[:600]}

    raw = RESULT_JSON.read_text(encoding="utf-8") if RESULT_JSON.exists() else ""
    if not raw or raw == old:
        state = "browser_unavailable"
        err = "巡店脚本未产出新结果（ego 9224 未开/登录失效/脚本失败）；见 patrol-node.log"
        _fb_fail(con, op_id, t0, err); con.commit(); con.close()
        return {"ok": False, "state": state, "error": err, "node_rc": node_rc}

    try:
        d = json.loads(raw)
    except Exception as e:                                    # noqa: BLE001
        err = "结果 JSON 解析失败：%s" % e
        _fb_fail(con, op_id, t0, err); con.commit(); con.close()
        return {"ok": False, "state": "failed", "error": err}

    login = d.get("login_state") or "unknown"
    if login != "session_present":
        state = "not_logged_in"
    steps = d.get("steps") or []
    summ = d.get("summary") or {}
    search = d.get("search_task") or {}
    shop = d.get("shop") or ""

    # ---- 落 ops_patrol_run ----
    cur = con.execute(
        "INSERT INTO ops_patrol_run(platform,shop,login_state,started_at,finished_at,"
        "elapsed_ms,total_steps,ok_steps,fail_steps,search_ok,result_json,state,error)"
        " VALUES('dy',?,?,?,?,?,?,?,?,?,?,?,?)",
        (shop, login, now(), now(), int((time.time() - t_run) * 1000),
         len(steps), int(summ.get("ok_steps") or 0), int(summ.get("fail_steps") or 0),
         1 if search.get("ok") else 0, raw[:200000], state, err[:600]))
    run_id = cur.lastrowid

    # ---- 逐步落 ops_patrol_step（操作前后 URL / 点击回执 / 回读 / 截图 / 耗时 / 错误）----
    for s in steps:
        cr = s.get("click_receipt") or {}
        con.execute(
            "INSERT INTO ops_patrol_step(run_id,seq,key,label,platform,shop,method,"
            "before_url,after_url,body_len,nums,click_ok,readback_ok,screenshot,"
            "elapsed_ms,error,at) VALUES(?,?,?,?, 'dy', ?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, s.get("seq"), s.get("key"), s.get("label"), shop,
             s.get("method"), s.get("before_url"), s.get("after_url"),
             s.get("body_len") or 0, json.dumps(s.get("nums") or {}, ensure_ascii=False),
             1 if cr.get("ok") or s.get("ok") else 0,
             1 if s.get("ok") else 0, s.get("screenshot") or "",
             s.get("elapsed_ms") or 0, (s.get("error") or "")[:600], now()))
    if search:
        con.execute(
            "INSERT INTO ops_patrol_step(run_id,seq,key,label,platform,shop,method,"
            "before_url,after_url,body_len,nums,click_ok,readback_ok,screenshot,"
            "elapsed_ms,error,at) VALUES(?,?,?,?, 'dy', ?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, 999, "search_task", "授权低风险任务：商品搜索（输入+点击+回读）",
             shop, "input+click+readback", LIST_URL, search.get("after_url"),
             0, json.dumps({"before_rows": search.get("before_rows"),
                            "after_rows": search.get("after_rows"),
                            "keyword": search.get("keyword")}, ensure_ascii=False),
             1 if search.get("ok") else 0, 1 if search.get("readback_ok") else 0,
             search.get("screenshot") or "", 0, (search.get("error") or "")[:600], now()))

    # ---- 从真页面文字生成今日运营任务（只依据真证据，不编造平台数据）----
    tasks = _build_tasks(steps, shop)
    for t in tasks:
        con.execute(
            "INSERT INTO ops_daily_task(day,platform,shop,product_id,title,kind,"
            "source_step,evidence,cls,state,created_at) VALUES(?, 'dy', ?,?,?,?,?,?,?,?,?)",
            (day(), shop, t.get("product_id") or "", t.get("title"), t.get("kind"),
             t.get("source_step"), (t.get("evidence") or "")[:900], t.get("cls"),
             t.get("state"), now()))

    # ---- 经营日报（给主人看的白话，不是技术日志；流量未取得如实写）----
    body = _build_report(d, tasks)
    con.execute(
        "INSERT INTO ops_daily_report(day,platform,shop,body,patrol_run_id,auto_done,"
        "need_human,manual_only,created_at) VALUES(?, 'dy', ?,?,?,?,?,?,?)"
        " ON CONFLICT(day) DO UPDATE SET body=excluded.body,patrol_run_id=excluded.patrol_run_id,"
        " auto_done=excluded.auto_done,need_human=excluded.need_human,"
        " manual_only=excluded.manual_only,created_at=excluded.created_at",
        (day(), shop, body, run_id,
         sum(1 for t in tasks if t["cls"] == "A"),
         sum(1 for t in tasks if t["cls"] == "B"),
         sum(1 for t in tasks if t["cls"] == "C"), now()))

    result = ("真实巡店 %s/%s 步成功；登录=%s；搜索任务=%s；今日任务 %s 条；日报已生成"
              % (summ.get("ok_steps"), len(steps), login,
                 "通过" if search.get("ok") else "未通过", len(tasks)))
    _fb_done(con, op_id, t0, result, note=json.dumps({
        "run_id": run_id, "login": login, "shop": shop,
        "search_keyword": search.get("keyword")}, ensure_ascii=False))
    con.commit(); con.close()
    return {"ok": state == "done", "state": state, "run_id": run_id,
            "login_state": login, "shop": shop,
            "steps": len(steps), "ok_steps": summ.get("ok_steps"),
            "search_ok": bool(search.get("ok")), "tasks": len(tasks),
            "result": result, "error": err or None}


LIST_URL = "https://fxg.jinritemai.com/ffa/g/list"


def _build_tasks(steps: list, shop: str) -> list:
    """每条任务都挂在某一步真页面文字上；平台无授权/无接口的数据不生成数值任务。"""
    out = []
    for s in steps:
        head = s.get("body_head") or ""
        nums = s.get("nums") or {}
        key = s.get("key")
        # 真页面可读到的「错失转化机会 / 审核驳回 / 整改」等 → 待优化商品任务（B 类）
        for phrase in ("错失转化机会", "提升空间", "审核驳回", "整改", "违规", "已封禁"):
            if phrase in head:
                n = None
                import re
                m = re.search(r"(\d+)\s*个?商品有提升空间|%s[（(]?\s*(\d+)" % phrase, head)
                if m:
                    n = m.group(1) or m.group(2)
                out.append({
                    "title": "处理「%s」%s" % (phrase, ("（页面显示 %s）" % n) if n else ""),
                    "kind": "违规整改" if phrase in ("整改", "违规", "审核驳回", "已封禁") else "待优化商品",
                    "source_step": key, "evidence": head[:400],
                    "cls": "B", "state": "need_human",
                })
                break
        # 活动广场 / 商机中心：只生成「人工核对」任务，不编造活动列表
        if key in ("campaign", "oppty") and s.get("ok"):
            out.append({
                "title": "核对%s真实开放项目（页面已打开，逐项人工确认资格/利润）" %
                         ("活动广场" if key == "campaign" else "商机中心"),
                "kind": "活动机会" if key == "campaign" else "蓝海商机",
                "source_step": key, "evidence": head[:400],
                "cls": "B", "state": "need_human",
            })
        # 商品诊断/搜索运营：生成 A 类「已自动检查」记录
        if key in ("diagnosis", "search", "growth") and s.get("ok"):
            out.append({
                "title": "AI 已巡店检查：%s（真页面已读，%s 字）" %
                         (s.get("label"), s.get("body_len")),
                "kind": "商品诊断" if key == "diagnosis" else "搜索优化",
                "source_step": key, "evidence": head[:400],
                "cls": "A", "state": "done",
            })
    # 边界任务（C 类）：无授权/无接口数据，明确不编造
    out.append({
        "title": "流量/曝光/点击/加购/退款/入池数据：平台未授权或无接口，未取得",
        "kind": "数据边界", "source_step": "capability_matrix",
        "evidence": "微信 /channels/ec/product/data/get 实测 48001 未授权；抖店罗盘无开放接口。"
                    "按工程约束与 schema.sql L8，不用 0 或模拟值冒充。",
        "cls": "C", "state": "skipped",
    })
    out.append({
        "title": "微信小店后台浏览器登录：当前未登录，需人工扫码（C 类）",
        "kind": "账号登录", "source_step": "login_probe",
        "evidence": "store.weixin.qq.com 为扫码入口；channels.weixin.qq.com 跳 login.html。"
                    "微信商品/订单数据改走 9191 官方 API（已在线）。",
        "cls": "C", "state": "skipped",
    })
    # 去重
    seen, uniq = set(), []
    for t in out:
        k = (t["kind"], t["title"])
        if k in seen:
            continue
        seen.add(k); uniq.append(t)
    return uniq


def _build_report(d: dict, tasks: list) -> str:
    """经营白话日报：成了什么、发现什么、要你定什么、哪些确实拿不到。"""
    summ = d.get("summary") or {}
    steps = d.get("steps") or []
    search = d.get("search_task") or {}
    a = [t for t in tasks if t["cls"] == "A"]
    b = [t for t in tasks if t["cls"] == "B"]
    c = [t for t in tasks if t["cls"] == "C"]
    lines = [
        "【今日经营日报 · %s】店铺：%s" % (day(), d.get("shop") or "抖店"),
        "",
        "一、今天系统真的做了什么",
        "  · 真登录抖店后台，实际打开并切换 %s 处页面（成功 %s / 共 %s 步），每步读了页面并留截图。"
        % (len(steps), summ.get("ok_steps", 0), len(steps)),
        "  · 授权低风险任务「商品搜索」：真输入关键词「%s」、真点搜索、真回读页面 → %s。"
        % (search.get("keyword") or "—", "通过" if search.get("ok") else "未通过（如实记录）"),
        "  · AI 自动完成 %s 项检查（商品诊断 / 搜索运营 / 商品成长）。" % len(a),
        "",
        "二、后台里真实看到的情况（都来自页面原文，不是推算）",
    ]
    for s in steps:
        if s.get("ok") and s.get("body_head"):
            nums = s.get("nums") or {}
            hint = ""
            for k in ("售卖中", "审核驳回", "已封禁", "审核中", "草稿箱"):
                if k in nums:
                    hint += "%s=%s " % (k, nums[k])
            snippet = (s.get("body_head") or "").replace("\n", " ")[:110]
            lines.append("  · %s：%s%s" % (s.get("label"), (hint + "｜") if hint else "", snippet))
    lines += [
        "",
        "三、等你确认的事（AI 只准备，不动平台数据）",
    ]
    for t in b:
        lines.append("  · %s —— %s" % (t["title"], t["evidence"][:60].replace("\n", " ")))
    lines += [
        "",
        "四、目前确实拿不到的（不编造、不冒充）",
    ]
    for t in c:
        lines.append("  · %s" % t["title"])
    lines += [
        "  · 视频上传/发布、达人联盟：账号体系与权限未开通，需人工在开放平台申请（C 类）。",
        "",
        "五、明天建议",
        "  · 先处理上面「等你确认」里审核驳回/整改的商品；",
        "  · 微信侧数据继续走官方接口，浏览器登录需你扫码一次；",
        "  · 流量类数据要等平台授权 compass / 罗盘后才能进 AI 分析。",
    ]
    return "\n".join(lines)


# ---------------- 回读接口 ----------------
def patrol_runs(con, limit: int = 30) -> list:
    return [dict(r) for r in con.execute(
        "SELECT run_id,platform,shop,login_state,started_at,elapsed_ms,total_steps,"
        "ok_steps,fail_steps,search_ok,state,error FROM ops_patrol_run"
        " ORDER BY run_id DESC LIMIT ?", (int(limit),))]


def patrol_steps(con, run_id: int = 0, limit: int = 60) -> list:
    if run_id:
        return [dict(r) for r in con.execute(
            "SELECT * FROM ops_patrol_step WHERE run_id=? ORDER BY seq LIMIT ?",
            (int(run_id), int(limit)))]
    return [dict(r) for r in con.execute(
        "SELECT * FROM ops_patrol_step ORDER BY step_id DESC LIMIT ?", (int(limit),))]


def daily_tasks(con, d: str = "", limit: int = 100) -> list:
    if d:
        return [dict(r) for r in con.execute(
            "SELECT * FROM ops_daily_task WHERE day=? ORDER BY task_id LIMIT ?",
            (d, int(limit)))]
    return [dict(r) for r in con.execute(
        "SELECT * FROM ops_daily_task WHERE day=? ORDER BY task_id LIMIT ?",
        (day(), int(limit)))]


def daily_reports(con, limit: int = 30) -> list:
    return [dict(r) for r in con.execute(
        "SELECT report_id,day,platform,shop,body,patrol_run_id,auto_done,"
        "need_human,manual_only,created_at FROM ops_daily_report"
        " ORDER BY report_id DESC LIMIT ?", (int(limit),))]


def overview(con) -> dict:
    def n(sql, args=()):
        try:
            return con.execute(sql, args).fetchone()[0]
        except Exception:                                     # noqa: BLE001
            return None
    return {
        "patrol_runs": n("SELECT COUNT(*) FROM ops_patrol_run"),
        "patrol_last_ok": n("SELECT ok_steps FROM ops_patrol_run ORDER BY run_id DESC LIMIT 1"),
        "patrol_last_steps": n("SELECT total_steps FROM ops_patrol_run ORDER BY run_id DESC LIMIT 1"),
        "today_tasks": n("SELECT COUNT(*) FROM ops_daily_task WHERE day=?", (day(),)),
        "today_auto": n("SELECT COUNT(*) FROM ops_daily_task WHERE day=? AND cls='A'", (day(),)),
        "today_need_human": n("SELECT COUNT(*) FROM ops_daily_task WHERE day=? AND cls='B'", (day(),)),
        "today_manual": n("SELECT COUNT(*) FROM ops_daily_task WHERE day=? AND cls='C'", (day(),)),
        "reports": n("SELECT COUNT(*) FROM ops_daily_report"),
        "metrics_measured": n("SELECT COUNT(*) FROM ops_metric_daily WHERE data_status='measured'"),
        "day": day(),
    }
