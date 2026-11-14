# -*- coding: utf-8 -*-
"""v3 · 标题优化引擎（P2）

设计原则（全部来自任务书 §4）：
  · 只调整**合法顺序**、**已证实属性**、**相关场景词**和**适当同义词**
  · 属性一律取自已落的 Profile（每条都有来源），不重新推断
  · 三个策略：核心词优先 / 属性优先 / 场景优先
  · 刷新不覆盖已选；已展示过的候选按 text_hash 排除，达到上限就明说
  · 平台长度限制来自 v3_title_policy（可改、可追溯），不写死在代码里
  · 校验六项：长度 / 堆砌 / 虚假描述 / 禁用词 / 跨类目词 / 与已选相似
  · 外部 AI 是**可选增强**：未配 API 时纯规则也能出可用标题；配了就用它改写，
    但它返回的每一条仍然要过同一套校验，过不了就不要。

绝不声称"换标题能规避平台重复铺货" —— 那只由真实上架反馈决定。本模块只管
「不撒谎、不越类目、不堆词、长度合规、能回溯原文」。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import urllib.request
from typing import Any

from . import keywords, profile as prof_mod, store

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_DIR = os.path.dirname(SERVER_DIR)
AI_CFG = os.path.join(BASE_DIR, "config", "v3_ai.json")
PROVIDERS = os.path.join(BASE_DIR, "config", "ai_providers.json")

STRATEGIES = ["core_first", "attribute_first", "scene_first"]
STRATEGY_LABEL = {"core_first": "核心词优先", "attribute_first": "属性优先",
                  "scene_first": "场景优先", "manual": "人工编辑"}
MAX_CANDIDATES = 18          # 单商品单店候选上限，到顶就明说


# ---------------------------------------------------------------- 工具

def _h(s: str) -> str:
    return hashlib.sha1((s or "").strip().encode("utf-8")).hexdigest()[:16]


def _cjk_len(s: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", s or ""))


def _jaccard(a: str, b: str) -> float:
    sa = set(re.findall(r"[\u4e00-\u9fff]{1}", a or ""))
    sb = set(re.findall(r"[\u4e00-\u9fff]{1}", b or ""))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def get_policy(conn: sqlite3.Connection, policy_id: str = "wechat-default") -> dict:
    r = conn.execute("SELECT * FROM v3_title_policy WHERE policy_id=?", (policy_id,)).fetchone()
    if not r:
        return {"policy_id": policy_id, "platform": "wechat", "min_len": 8,
                "max_len": 60, "min_cjk": 6, "source": "内置默认"}
    return dict(r)


# ---------------------------------------------------------------- 候选生成（规则）

def _attr_order(prof: dict) -> list[str]:
    """属性按「对买家最有用」排序：容量/材质/功能/尺寸/其他。"""
    order_key = ["容量/人数", "材质", "特殊功能", "防雨", "防晒", "开门方式",
                 "尺寸/规格", "颜色", "适用场景"]
    got: list[tuple[int, str]] = []
    for k, v in (prof.get("verified_attributes") or {}).items():
        base = k.split(":")[0]
        idx = order_key.index(base) if base in order_key else len(order_key)
        got.append((idx, str(v)))
    got.sort(key=lambda x: x[0])
    out, seen = [], set()
    for _i, v in got:
        if v and v not in seen and len(v) <= 16:
            seen.add(v)
            out.append(v)
    return out


def _scene_pick(prof: dict, seed: int) -> list[str]:
    """从 allowed_terms 里挑场景词（最多 2 个），seed 决定顺序，避免各店完全一样。"""
    sc = set()
    for grp, words in keywords.SCENE_LEXICON.items():
        for w in words:
            sc.add(w)
    pool = [t for t in (prof.get("allowed_terms") or []) if t in sc]
    if not pool:
        return []
    k = min(2, len(pool))
    if seed:
        pool = pool[seed % len(pool):] + pool[:seed % len(pool)]
    return pool[:k]


def _compose(strategy: str, core: str, attrs: list[str], scenes: list[str],
             variant: int) -> str:
    """按策略拼接。variant 决定取多少个属性 / 是否用场景，做出足够多的不同组合。"""
    n_attr = min(len(attrs), 2 + (variant % 2))
    a = attrs[:n_attr]
    s = scenes[:1] if variant % 3 else scenes[:2]
    if strategy == "core_first":
        parts = [core] + a + s
    elif strategy == "attribute_first":
        parts = (a[:1] + [core] + a[1:]) + s
    else:  # scene_first
        parts = s + [core] + a
    # 去重保序（同一词只出现一次，防堆砌）
    seen, out = set(), []
    for p in parts:
        p = (p or "").strip()
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return "".join(out)


def _validate(text: str, prof: dict, policy: dict,
              selected: list[str], recent: list[str]) -> dict:
    issues: list[dict] = []
    core = prof.get("core_product_type") or ""
    lo, hi = int(policy.get("min_len") or 8), int(policy.get("max_len") or 60)
    n = len(text or "")

    if n < lo:
        issues.append({"code": "too_short", "level": "block",
                       "msg": "长度 %d，少于平台下限 %d" % (n, lo)})
    if n > hi:
        issues.append({"code": "too_long", "level": "block",
                       "msg": "长度 %d，超过平台上限 %d" % (n, hi)})
    if core and core not in text:
        issues.append({"code": "missing_core", "level": "block",
                       "msg": "缺少核心商品词「%s」" % core})

    for w in keywords.HYPE_WORDS:
        if w in text:
            issues.append({"code": "hype", "level": "block",
                           "msg": "含极限/虚假描述词「%s」" % w})

    # 堆砌：① 连续重复片段（"帐篷帐篷"）② 同一 2 字片段出现 >=4 次（"帐篷A帐篷B帐篷C"）
    m = re.search(r"([\u4e00-\u9fff]{2,4})\1+", text)
    if m:
        issues.append({"code": "stacking", "level": "block",
                       "msg": "「%s」连续重复（堆砌）" % m.group(1)})
    grams: dict[str, int] = {}
    for i in range(len(text) - 1):
        g = text[i:i + 2]
        if re.fullmatch(r"[\u4e00-\u9fff]{2}", g):
            grams[g] = grams.get(g, 0) + 1
    for g, n in grams.items():
        if n >= 4:
            issues.append({"code": "stacking", "level": "block",
                           "msg": "「%s」出现 %d 次（堆砌）" % (g, n)})
            break

    for w in prof.get("forbidden_cross_category_terms") or []:
        if w and w in text:
            issues.append({"code": "cross_category", "level": "block",
                           "msg": "混入跨类目词「%s」" % w})

    for t in selected:
        if t == text:
            issues.append({"code": "dup_selected", "level": "block",
                           "msg": "与已选用标题完全相同"})
    for t in recent:
        if _jaccard(t, text) >= 0.9:
            issues.append({"code": "too_similar", "level": "warn",
                           "msg": "与已有候选高度相似（%.2f）" % _jaccard(t, text)})
            break

    block = [i for i in issues if i["level"] == "block"]
    return {"ok": not block, "issues": issues,
            "checked": ["长度", "核心词", "虚假描述", "堆砌", "跨类目", "重复/相似"],
            "length": n, "limit": [lo, hi]}


# ---------------------------------------------------------------- AI 增强（可选）

def _ai_cfg() -> dict:
    if os.path.exists(AI_CFG):
        try:
            with open(AI_CFG, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def ai_status() -> dict:
    cfg = _ai_cfg()
    role = (cfg.get("roles") or {}).get("title") or {}
    if role.get("enabled") and role.get("base") and role.get("model"):
        return {"enabled": True, "model": role.get("model"), "base": role.get("base"),
                "source": "config/v3_ai.json"}
    # 退一步：复用 ai_providers.json 的 text 角色（已经实测可用）
    if os.path.exists(PROVIDERS):
        try:
            with open(PROVIDERS, "r", encoding="utf-8") as f:
                p = json.load(f)
            t = (p.get("roles") or {}).get("text") or {}
            if t.get("enabled") and t.get("base") and t.get("model"):
                return {"enabled": True, "model": t.get("model"), "base": t.get("base"),
                        "source": "config/ai_providers.json:text", "borrowed": True}
        except Exception:
            pass
    return {"enabled": False, "hint": "没配标题 AI。规则引擎照样出候选；"
                                     "要接 AI 就填 config/v3_ai.json 的 roles.title"}


def _ai_generate(prof: dict, base: list[str], count: int, policy: dict) -> tuple[list[str], str]:
    """调外部 AI 改写。失败返回 ([], 原因)，绝不让整条流程失败。"""
    st = ai_status()
    if not st.get("enabled"):
        return [], "AI 未配置"
    cfg = _ai_cfg()
    role = (cfg.get("roles") or {}).get("title") or {}
    if not role.get("enabled"):
        try:
            with open(PROVIDERS, "r", encoding="utf-8") as f:
                role = ((json.load(f).get("roles") or {}).get("text") or {})
        except Exception:
            return [], "读不到模型配置"
    base_url = str(role.get("base") or "").rstrip("/")
    key = role.get("api_key") or ""
    model = role.get("model") or ""
    if not (base_url and model):
        return [], "模型配置不完整"

    sys_p = ("你是电商标题优化助手。只能使用我给的词，不许新增任何未给出的属性、材质、"
             "功效、品牌、规格、数字。输出 JSON 数组，每项一个标题字符串，不要解释。")
    usr = json.dumps({
        "核心商品词": prof.get("core_product_type"),
        "可用词": prof.get("allowed_terms"),
        "属性证据": prof.get("verified_attributes"),
        "禁止出现的词": prof.get("forbidden_cross_category_terms"),
        "长度范围": [policy.get("min_len"), policy.get("max_len")],
        "参考": base[:3],
        "生成数量": count,
    }, ensure_ascii=False)

    body = {
        "model": model,
        "messages": [{"role": "system", "content": sys_p},
                     {"role": "user", "content": usr}],
        "temperature": float(role.get("temperature") or 0.6),
        "max_tokens": int(role.get("max_tokens") or 1200),
    }
    req = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=float(role.get("timeout") or 60)) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        txt = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        m = re.search(r"\[[\s\S]*\]", txt)
        arr = json.loads(m.group(0)) if m else []
        out = [str(x).strip() for x in arr if isinstance(x, str) and str(x).strip()]
        return out[:count], "AI 生成 %d 条" % len(out)
    except Exception as e:  # noqa: BLE001
        return [], "AI 调用失败：%s" % e


# ---------------------------------------------------------------- 生成主流程

def generate(conn: sqlite3.Connection, pid: int, store_id: str = "default",
             count: int = 3, exclude_previous: bool = True,
             strategy_mix: list[str] | None = None,
             use_ai: bool | None = None,
             actor: str = "title") -> dict:
    """生成候选。**已选标题不会被覆盖**，新候选一律 status=candidate。"""
    p = store.get_product(conn, pid)
    if not p:
        return {"ok": False, "error": "没有这个商品"}
    prof = p.get("profile")
    if not prof:
        prof = prof_mod.build_profile(p, conn)
        conn.execute("UPDATE v3_product SET profile=?, updated_at=? WHERE product_id=?",
                     (store.j(prof), store.now(), pid))
        conn.commit()
    if not prof.get("core_product_type"):
        return {"ok": False, "retryable": False,
                "error": "画像里没有核心商品词，无法生成（先补类目或人工确认）",
                "profile": prof}

    st = conn.execute("SELECT * FROM v3_store WHERE store_id=?", (store_id,)).fetchone()
    policy = get_policy(conn, (st["title_policy_id"] if st else None) or "wechat-default")

    mix = [s for s in (strategy_mix or STRATEGIES) if s in STRATEGIES] or STRATEGIES
    selected = [r["title_text"] for r in conn.execute(
        "SELECT title_text FROM v3_title_variant WHERE product_id=? AND store_id=? AND status='selected'",
        (pid, store_id))]
    all_recent = [r["title_text"] for r in conn.execute(
        "SELECT title_text FROM v3_title_variant WHERE product_id=? AND store_id=? ORDER BY variant_id DESC LIMIT 120",
        (pid, store_id))]
    seen = {r["text_hash"] for r in conn.execute(
        "SELECT text_hash FROM v3_title_variant WHERE product_id=? AND store_id=?",
        (pid, store_id))}
    total_have = conn.execute(
        "SELECT COUNT(*) FROM v3_title_variant WHERE product_id=? AND store_id=?",
        (pid, store_id)).fetchone()[0]

    if exclude_previous and total_have >= MAX_CANDIDATES:
        return {"ok": False, "exhausted": True,
                "error": "这个商品在这家店已有 %d 条候选（上限 %d）。候选空间已用尽，"
                         "清掉不用的候选或人工改写后再刷新。" % (total_have, MAX_CANDIDATES)}

    attrs = _attr_order(prof)
    core = prof["core_product_type"]
    gen_index = total_have + 1
    out: list[dict] = []
    skipped_dup = 0

    # ---------- 规则生成
    seed0 = abs(hash(store_id)) % 7
    for vi in range(0, 12):
        for strat in mix:
            if len(out) >= count:
                break
            text = _compose(strat, core, attrs, _scene_pick(prof, seed0 + vi), vi)
            h = _h(text)
            if exclude_previous and (h in seen):
                skipped_dup += 1
                continue
            rep = _validate(text, prof, policy, selected, all_recent)
            if not rep["ok"]:
                continue
            seen.add(h)
            out.append({"title_text": text, "strategy": strat, "hash": h,
                        "validation": rep, "origin": "rule"})
            all_recent.append(text)
        if len(out) >= count:
            break

    # ---------- AI 增强（过一次同一套校验）
    want_ai = ai_status().get("enabled") if use_ai is None else bool(use_ai)
    ai_note = ""
    if want_ai and len(out) < count:
        base = [o["title_text"] for o in out] or [core + "".join(attrs[:2])]
        cand, note = _ai_generate(prof, base, count - len(out), policy)
        ai_note = note
        for i, t in enumerate(cand):
            h = _h(t)
            if h in seen:
                continue
            rep = _validate(t, prof, policy, selected, all_recent)
            if not rep["ok"]:
                continue
            seen.add(h)
            out.append({"title_text": t, "strategy": mix[i % len(mix)], "hash": h,
                        "validation": rep, "origin": "ai"})
            all_recent.append(t)

    if not out:
        return {"ok": False, "retryable": True,
                "error": "没生成出合规候选：可用词太少（属性证据不足）或全部与已有候选重复",
                "hint": "补属性 / 换策略 / 先选一条再刷新",
                "skipped_duplicate": skipped_dup,
                "profile_attrs": attrs, "ai": ai_status()}

    ids = []
    ts = store.now()
    for o in out:
        cur = conn.execute(
            "INSERT INTO v3_title_variant(product_id, store_id, title_text,"
            " generation_index, strategy, locked_core, status, keyword_ids,"
            " model_and_prompt_version, validation_report, text_hash, created_at)"
            " VALUES(?,?,?,?,?,?,'candidate','',?,?,?,?)",
            (pid, store_id, o["title_text"], gen_index, o["strategy"], core,
             ("rule" if o["origin"] == "rule" else "ai:" + ai_status().get("model", "")),
             store.j(o["validation"]), o["hash"], ts))
        ids.append(int(cur.lastrowid))
        gen_index += 1
    if p.get("status") in ("imported", "fine_screened", "profiled"):
        store.set_product_status(conn, pid, "titled")
    store.audit(conn, "title.generate", "v3_product", pid, None,
                {"store_id": store_id, "n": len(out), "hash": [o["hash"] for o in out]},
                actor=actor)
    conn.commit()
    return {"ok": True, "count": len(out), "ids": ids, "variants": out,
            "skipped_duplicate": skipped_dup, "ai": ai_status(), "ai_note": ai_note}


def list_variants(conn: sqlite3.Connection, pid: int,
                  store_id: str | None = None) -> list[dict]:
    sql = "SELECT * FROM v3_title_variant WHERE product_id=?"
    args: list[Any] = [pid]
    if store_id:
        sql += " AND store_id=?"
        args.append(store_id)
    sql += " ORDER BY CASE status WHEN 'selected' THEN 0 WHEN 'candidate' THEN 1 ELSE 2 END," \
           " variant_id"
    out = []
    for r in conn.execute(sql, args):
        d = dict(r)
        d["validation"] = store.unj(d.get("validation_report"), None)
        d["strategy_label"] = STRATEGY_LABEL.get(d.get("strategy"), d.get("strategy"))
        out.append(d)
    return out


def select(conn: sqlite3.Connection, variant_id: int, actor: str = "title") -> dict:
    """选定。同一 (product, store) 下只允许一条 selected —— 其余的改回 candidate，
    但**历史一条不删**（任务书：多版本互不覆盖）。"""
    r = conn.execute("SELECT * FROM v3_title_variant WHERE variant_id=?", (variant_id,)).fetchone()
    if not r:
        return {"ok": False, "error": "没有这条候选"}
    pid, sid = r["product_id"], r["store_id"]
    conn.execute("UPDATE v3_title_variant SET status='candidate'"
                 " WHERE product_id=? AND store_id=? AND status='selected'", (pid, sid))
    conn.execute("UPDATE v3_title_variant SET status='selected' WHERE variant_id=?",
                 (variant_id,))
    store.audit(conn, "title.select", "v3_title_variant", variant_id, None,
                {"text": r["title_text"]}, actor=actor)
    conn.commit()
    return {"ok": True, "variant_id": variant_id, "title_text": r["title_text"],
            "copy_text": r["title_text"], "store_id": sid}


def reject(conn: sqlite3.Connection, variant_id: int, actor: str = "title") -> dict:
    r = conn.execute("SELECT * FROM v3_title_variant WHERE variant_id=?", (variant_id,)).fetchone()
    if not r:
        return {"ok": False, "error": "没有这条候选"}
    conn.execute("UPDATE v3_title_variant SET status='rejected' WHERE variant_id=?",
                 (variant_id,))
    store.audit(conn, "title.reject", "v3_title_variant", variant_id,
                {"status": r["status"]}, {"status": "rejected"}, actor=actor)
    conn.commit()
    return {"ok": True, "variant_id": variant_id}


def manual_edit(conn: sqlite3.Connection, pid: int, text: str,
                store_id: str = "default", actor: str = "title") -> dict:
    """人工改一条 → 仍然要过校验，返回报告供人判断（不拦截人工决定，但如实报告）。"""
    p = store.get_product(conn, pid)
    if not p:
        return {"ok": False, "error": "没有这个商品"}
    prof = p.get("profile") or prof_mod.build_profile(p, conn)
    policy = get_policy(conn)
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "标题是空的"}
    selected = [r["title_text"] for r in conn.execute(
        "SELECT title_text FROM v3_title_variant WHERE product_id=? AND store_id=? AND status='selected'",
        (pid, store_id))]
    rep = _validate(text, prof, policy, selected, [])
    h = _h(text)
    if conn.execute("SELECT 1 FROM v3_title_variant WHERE product_id=? AND store_id=? AND text_hash=?",
                    (pid, store_id, h)).fetchone():
        return {"ok": False, "error": "这条已经存在（同一文本）", "validation": rep}
    cur = conn.execute(
        "INSERT INTO v3_title_variant(product_id, store_id, title_text, generation_index,"
        " strategy, locked_core, status, keyword_ids, model_and_prompt_version,"
        " validation_report, text_hash, created_at)"
        " VALUES(?,?,?,1,'manual',?,'candidate','','manual',?,?,?)",
        (pid, store_id, text, prof.get("core_product_type") or "", store.j(rep), h, store.now()))
    conn.commit()
    return {"ok": True, "variant_id": int(cur.lastrowid), "validation": rep,
            "warning": "" if rep["ok"] else "这条不通过自动校验：" +
                       "；".join(i["msg"] for i in rep["issues"] if i["level"] == "block")}


def copy_text(conn: sqlite3.Connection, pid: int, store_id: str = "default") -> dict:
    """给前端「复制当前选用标题」用。"""
    r = conn.execute("SELECT title_text, variant_id FROM v3_title_variant"
                     " WHERE product_id=? AND store_id=? AND status='selected'"
                     " ORDER BY variant_id DESC LIMIT 1", (pid, store_id)).fetchone()
    if not r:
        return {"ok": False, "error": "这个商品在这家店还没选定标题", "copy_text": ""}
    return {"ok": True, "copy_text": r["title_text"], "variant_id": r["variant_id"]}
