# -*- coding: utf-8 -*-
"""v3 · 商品画像 ProductProfile（P2 前置）

铁律（任务书 §4.2）：**禁止猜测容量、材质、防水等级、品牌、功效或规格。**

本模块的每一条属性都必须能在这三处之一找到原始字符串：
    ① 商品标题原文
    ② 原始属性表（小猪 属性 表 / 数据包 properties 字段）
    ③ 类目路径
找不到就不写，并把它记进 `unknown_fields` —— 让"不知道"变成可见的事实，
而不是让模型编一个看起来合理的值。
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from . import keywords, store

# 属性抽取器：每个字段给出「可接受的正则」和「必须能从原文找到的判据」
# 只做「识别」，不做「推断」。识别不到即缺失。
PATTERNS: dict[str, list[tuple[str, str]]] = {
    "容量/人数": [
        (r"(单人|双人|三人|四人|多人|(\d+)人)", "人数"),
        (r"(\d+)\s*(?:L|升|LITER|LITRE)\b", "容量升"),
    ],
    "开门方式": [
        (r"(自动开合|自动开|全自动|手开|手动|速开|一键开合)", "开合"),
    ],
    "防雨": [
        (r"(防暴雨|防大雨|防雨|防水|抗雨)", "防雨"),
    ],
    "防晒": [
        (r"(UPF\s*\+?\s*\d+[+]?|防紫外线|防晒|遮阳)", "防晒"),
    ],
    "材质": [
        (r"(牛津布|涤纶|尼龙|棉麻|铝合金|不锈钢|碳钢|实木|超纤皮|PU皮|真皮|玻璃|亚克力)", "材质"),
    ],
    "尺寸/规格": [
        (r"(\d{2,4}\s*[xX×*]\s*\d{2,4}(?:\s*[xX×*]\s*\d{2,4})?\s*(?:cm|CM|厘米|mm))", "尺寸"),
        (r"(加大|加宽|加深|加高|加厚)", "加大"),
    ],
    "颜色": [
        (r"(黑色|白色|灰色|米色|卡其|蓝色|绿色|红色|粉色|棕色|咖色|透明)", "颜色"),
    ],
    "适用场景": [
        (r"(户外|露营|野营|野外|徒步|登山|自驾|郊游|过夜)", "户外场景"),
        (r"(客厅|卧室|书房|阳台|玄关|餐厅|小户型|出租屋|宿舍)", "家居场景"),
        (r"(办公室|工位|办公)", "办公场景"),
    ],
    "特殊功能": [
        (r"(可折叠|折叠|便携|免安装|免打孔|可拆卸|可机洗|可收纳)", "便携"),
        (r"(遥控|声控|感应|USB\s*充电|充电款|太阳能|无线充)", "智能"),
        (r"(护腰|人体工学|护脊|高靠背|可旋转)", "人体工学"),
    ],
}

# 类目路径中的无意义段（数字 ID / 空）
_CAT_JUNK = re.compile(r"^[\d\s\-]+$")


def _cat_segments(cat: str) -> list[str]:
    if not cat:
        return []
    parts = re.split(r"[>/|、,，\-]+", str(cat))
    return [p.strip() for p in parts if p.strip() and not _CAT_JUNK.match(p.strip())]


def _find(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text or "", re.IGNORECASE)
    if not m:
        return None
    return m.group(0).strip()


def build_profile(p: dict, conn: sqlite3.Connection | None = None) -> dict:
    """商品 dict → ProductProfile。纯函数（除可选词库查询）。"""
    title = (p.get("original_title") or p.get("title") or "").strip()
    attrs = p.get("attributes") or {}
    if isinstance(attrs, str):
        attrs = store.unj(attrs, {}) or {}
    cat = (p.get("category_path") or p.get("category") or "").strip()
    segs = _cat_segments(cat)

    evidence: list[dict] = []
    verified: dict[str, str] = {}
    unknown: list[str] = []

    # ---------- ① 属性表里直接给的值：最强证据
    for k, v in (attrs or {}).items():
        vs = str(v or "").strip()
        if not vs:
            continue
        verified[str(k)] = vs
        evidence.append({"field": str(k), "value": vs, "source": "商品属性",
                         "snippet": "%s=%s" % (k, vs)})

    # ---------- ② 标题里可识别的：只记录命中原文片段
    for field, pats in PATTERNS.items():
        for pat, label in pats:
            hit = _find(pat, title)
            if not hit:
                continue
            key = field if field not in verified else "%s:%s" % (field, label)
            if key in verified:
                continue
            verified[key] = hit
            evidence.append({"field": key, "value": hit, "source": "标题原文",
                             "snippet": hit})
            break
        else:
            if field not in {e["field"].split(":")[0] for e in evidence}:
                unknown.append(field)

    # ---------- ③ 核心商品类型：人工指定 > 类目路径 > 类目关键词命中
    old = p.get("profile") or {}
    if isinstance(old, str):
        old = store.unj(old, {}) or {}
    core = ""
    core_src = ""
    core_manual = bool(old.get("core_manual"))
    if core_manual and old.get("core_product_type"):
        core = old["core_product_type"]
        core_src = "人工指定（%s）" % (old.get("core_set_at") or "")
        evidence.append({"field": "core_product_type", "value": core,
                         "source": core_src, "snippet": "人工指定"})
    if not core and segs:
        core = segs[-1]
        core_src = "类目路径末段"
    if not core and conn is not None:
        cr = keywords.load_category_rules()
        best, best_n = "", 0
        for cat_name, words in (cr.get("category_keywords") or {}).items():
            if cat_name.startswith("_"):
                continue
            n = sum(1 for w in words or [] if w and w in title)
            if n > best_n:
                best, best_n = cat_name, n
        if best and best_n >= 1:
            core, core_src = best, "类目关键词（%d 命中）" % best_n
    if core and not any(e["field"] == "core_product_type" for e in evidence):
        evidence.append({"field": "core_product_type", "value": core,
                         "source": core_src, "snippet": cat or core})

    # ---------- ④ 允许用词：核心 + 已证实属性 + 命中/类目相关的场景
    allowed: list[str] = []
    if core:
        allowed.append(core)
    for seg in segs:
        if seg and seg not in allowed:
            allowed.append(seg)
    for k, v in verified.items():
        if v and v not in allowed and len(v) <= 20:
            allowed.append(v)
    for field in ("适用场景",):
        if field in verified:
            allowed.append(verified[field])
    # 场景词：标题已有 或 类目同组，才允许
    for grp, words in keywords.SCENE_LEXICON.items():
        group_ok = any(w and w in cat for w in (grp, *words))
        for w in words:
            if (w in title) or group_ok:
                if w not in allowed:
                    allowed.append(w)

    # ---------- ⑤ 跨类目禁区（不许混进来的词）
    cr = keywords.load_category_rules()
    forbidden = [w for w in (cr.get("exclude_categories") or [])]
    own = set(segs) | ({core} if core else set())
    forbidden = [w for w in forbidden if w not in own]

    # ---------- ⑥ 风险词命中（品牌 / 违规），只报不删
    risks = keywords.risk_hits(conn, title) if conn is not None else []

    # ---------- ⑦ 卖点：从已证实属性里挑，最多 3 个，全部带来源
    points = []
    for e in evidence:
        if e["source"] == "商品属性" and len(points) < 2:
            points.append("%s：%s" % (e["field"], e["value"]))
        elif e["source"] == "标题原文" and len(points) < 3:
            points.append(e["value"])

    # ---------- ⑧ 置信度
    conf = 0.35
    if core:
        conf += 0.2
    if core_manual:
        conf += 0.1          # 人已经确认过核心词
    if cat:
        conf += 0.1
    if attrs:
        conf += 0.15
    n_attr = len([e for e in evidence if e["source"] != "core_product_type"])
    conf += min(0.2, 0.05 * n_attr)
    if risks:
        conf -= 0.05
    conf = round(max(0.05, min(0.95, conf)), 2)

    reasons = []
    if not core:
        reasons.append("没认出核心商品类型（类目只有数字 ID，标题也对不上已知类目词）"
                       " —— 请在界面上从建议里选一个或自己填")
    if not attrs:
        reasons.append("没有原始属性表，属性只能从标题里认，覆盖不完整")
    if risks:
        reasons.append("标题命中风险词：%s" % "、".join(r["text"] for r in risks[:5]))
    if cat and not segs:
        reasons.append("类目只有数字 ID，没有可读路径")

    return {
        "product_id": p.get("product_id"),
        "core_product_type": core,
        "core_manual": core_manual,
        "core_set_at": old.get("core_set_at") or "",
        "core_source": core_src,
        "verified_attributes": verified,
        "selling_points": points,
        "category_id": p.get("category_id") or "",
        "category_path": cat,
        "allowed_terms": allowed,
        "forbidden_cross_category_terms": forbidden,
        "risk_hits": risks,
        "confidence": conf,
        # 低置信 / 没认出核心词 / 命中风险词 / 既没属性表也没类目路径 / 证据太少
        # —— 任何一条成立就必须人工确认，不许直接拿去生成标题
        "needs_review": bool((conf < 0.55 and not core_manual) or not core or bool(risks)
                             or (not attrs and not cat)
                             or len([e for e in evidence
                                     if e["source"] in ("商品属性", "标题原文")]) < 2),
        "review_reasons": reasons,
        "unknown_fields": unknown,
        "evidence": evidence,
        "source_title": title,
        "note": "所有属性均来自原文/属性表；unknown_fields 里的项表示「原文没有，不许猜」",
    }


# ---------------------------------------------------------------- 核心词：建议与人工指定

# 从标题尾部剥离的修饰/属性词：剥掉它们，剩下的尾巴才是商品本体
TAIL_STRIP = sorted(set([
    "新款", "同款", "旧款", "爆款", "男女", "男款", "女款", "男士", "女士", "学生",
    "加大", "加厚", "加长", "加宽", "加高", "大号", "小号", "中号",
    "便携", "速干", "透气", "防晒", "防雨", "防水", "防风", "防蚊", "防尘", "保暖",
    "轻薄", "超轻", "折叠", "可折叠", "免安装", "免打孔", "可收纳", "可拆卸", "可机洗",
    "多功能", "专业", "户外", "露营", "野营", "野外", "运动", "登山", "徒步", "钓鱼",
    "外套", "外衣", "防寒", "保暖", "立领", "软壳", "硬壳", "连帽", "开衫", "背心",
    "上衣", "下装", "内搭", "打底", "夹克", "裤子", "鞋子", "袜子", "帽子", "手套",
    "包袋", "背包", "腰包", "单肩", "斜挎", "工具", "配件", "套装", "组合", "整装",
    "夏季", "冬季", "春季", "秋季", "四季", "春秋", "夏天", "冬天",
    "款", "装", "版", "套", "件", "组", "副", "对",
    "色", "码", "号", "大", "小", "高", "低", "厚", "薄", "男", "女", "人", "用",
    "型", "式", "代", "季", "年", "个", "只", "把", "双", "支",
]), key=len, reverse=True)


def _strip_tail(t: str) -> str:
    """把标题尾部的修饰词一层层剥掉（只剥尾部，不动中间）。

    每一轮：先剥掉尾部的英文/数字/标点，再剥一个修饰词，直到两样都剥不动。
    """
    s = t.strip()
    guard = 0
    while guard < 80:
        guard += 1
        before = s
        while s and not _CJK_CHAR.match(s[-1]):
            s = s[:-1]
        for w in TAIL_STRIP:
            if s.endswith(w) and len(s) - len(w) >= 2:
                s = s[:-len(w)]
                break
        if s == before:
            break
    return s


_CJK_CHAR = re.compile(r"[\u4e00-\u9fff]")


def suggest_core(conn: sqlite3.Connection, pid: int, topn: int = 8) -> dict:
    """给不出核心词时，**给建议让人选**，不是替人决定。

    两级，来源全部写清楚：
      A 级（推荐）：词库 core 词表里确实出现在标题里的词，最长优先
      B 级（兜底）：从标题尾部剥离修饰词后截出的 2–4 字片段，标注「需人工确认」
    两级的词都来自标题原文/词表，**不生成任何新词**。
    """
    p = store.get_product(conn, pid)
    if not p:
        return {"ok": False, "error": "没有这个商品"}
    title = (p.get("original_title") or "").strip()
    risk = {r["text"] for r in keywords.risk_hits(conn, title, limit=60)}

    out: list[dict] = []
    seen: set[str] = set()

    # ---------- A 级：词库 core
    pool = [r["text"] for r in conn.execute(
        "SELECT text FROM v3_keyword WHERE kind='core' AND status<>'disabled'"
        " AND length(text)>=2 ORDER BY length(text) DESC")]
    for w in pool:
        if w in title and w not in seen and w not in risk and len(out) < topn // 2:
            seen.add(w)
            out.append({"word": w, "level": "A", "why": "命中词库核心词表（推荐）"})

    # ---------- B 级：尾部剥离（越长的片段越可能带上前一个词的尾巴，故 2 字先给）
    s = _strip_tail(title)
    for size in (2, 3, 4):
        if len(s) >= size:
            w = s[-size:]
            if (w not in seen and w not in risk
                    and re.fullmatch(r"[\u4e00-\u9fff]{%d}" % size, w)):
                seen.add(w)
                out.append({"word": w, "level": "B",
                            "why": "从标题尾部剥离修饰词后截取（%d 字），需人工确认"
                                   % size})
        if len(out) >= topn:
            break

    return {"ok": True, "product_id": pid, "title": title,
            "candidates": out,
            "stripped_tail": s,
            "note": "A 级可直接用；B 级是标题原文里的片段，不是 AI 编的词 —— "
                    "但品类要你自己确认一眼，确认后点「设为核心词」"}


def set_core(conn: sqlite3.Connection, pid: int, word: str,
             actor: str = "human") -> dict:
    """人工指定核心商品词。落 profile，标 core_manual —— 之后 refresh 不会冲掉它。"""
    word = (word or "").strip()
    if not word or len(word) > 20:
        return {"ok": False, "error": "核心词不能为空，且不超过 20 字"}
    p = store.get_product(conn, pid)
    if not p:
        return {"ok": False, "error": "没有这个商品"}
    prof = p.get("profile") or build_profile(p, conn)
    before = prof.get("core_product_type")
    prof["core_product_type"] = word
    prof["core_manual"] = True
    prof["core_set_at"] = store.now()
    prof["core_source"] = "人工指定"
    prof["evidence"] = [e for e in (prof.get("evidence") or [])
                        if e.get("field") != "core_product_type"]
    prof["evidence"].append({"field": "core_product_type", "value": word,
                             "source": "人工指定", "snippet": "人工指定"})
    # 核心词必须同时进 allowed_terms，否则标题拼不出来
    allowed = prof.get("allowed_terms") or []
    if word not in allowed:
        allowed.insert(0, word)
    prof["allowed_terms"] = allowed
    prof["needs_review"] = bool(prof.get("risk_hits"))
    conn.execute("UPDATE v3_product SET profile=?, updated_at=? WHERE product_id=?",
                 (store.j(prof), store.now(), pid))
    store.audit(conn, "profile.set_core", "v3_product", pid,
                {"core": before}, {"core": word}, actor=actor)
    conn.commit()
    return {"ok": True, "product_id": pid, "core_product_type": word,
            "profile": prof}


def refresh(conn: sqlite3.Connection, pid: int, actor: str = "profile") -> dict:
    """重算并落库。"""
    p = store.get_product(conn, pid)
    if not p:
        return {"ok": False, "error": "没有这个商品"}
    prof = build_profile(p, conn)
    store.audit(conn, "profile.refresh", "v3_product", pid,
                store.unj(p.get("profile")), prof, actor=actor)
    conn.execute("UPDATE v3_product SET profile=?, category_path=COALESCE(NULLIF(category_path,''),?),"
                 " updated_at=? WHERE product_id=?",
                 (store.j(prof), prof.get("category_path") or "", store.now(), pid))
    if p.get("status") in ("imported", "fine_screened"):
        store.set_product_status(conn, pid, "profiled")
    conn.commit()
    return {"ok": True, "product_id": pid, "profile": prof}


def confirm(conn, pid: int, actor: str = "human") -> dict:
    """人工确认画像：表示"这些属性我看过了，可以用"。

    只有 confirmed=True 的商品，出包时才不会被计入「待复核」清单。
    ★ 不改变任何属性值 —— 确认的是「看过」，不是「改过」。
    """
    p = store.get_product(conn, pid)
    if not p:
        return {"ok": False, "error": "没有这个商品"}
    prof = p.get("profile") or build_profile(p, conn)
    prof["confirmed"] = True
    prof["confirmed_at"] = store.now()
    prof["confirmed_by"] = actor
    conn.execute("UPDATE v3_product SET profile=?, updated_at=? WHERE product_id=?",
                 (store.j(prof), store.now(), pid))
    store.audit(conn, "profile.confirm", "v3_product", pid, None,
                {"confirmed_at": prof["confirmed_at"]}, actor=actor)
    conn.commit()
    return {"ok": True, "product_id": pid, "confirmed": True,
            "core_product_type": prof.get("core_product_type"),
            "verified_attributes": prof.get("verified_attributes")}
