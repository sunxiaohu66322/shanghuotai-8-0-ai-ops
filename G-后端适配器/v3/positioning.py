# -*- coding: utf-8 -*-
"""细筛定位判定 —— 把主人 2026-10-18 定调的「卖给谁 / 什么价位 / 什么品类 / 什么货不能碰」
变成可执行的打分与拦截，规则全部放在 config/fine_positioning.json（热读，改完即生效）。

为什么单独一个模块：
    初筛只做「去品牌 / 去广告 / 去死链 / 采 URL」；人群、价位、品类方向、质量风险
    必须在细筛判，因为要有详情页文字与图片 OCR 当证据。这些规则不该散在 flow.py 里。

对外只暴露三个东西：
    cfg()                         读配置（按 mtime 热读）
    evaluate(product, texts, ...) 判一件商品 → {score, verdict, reasons, layers, evidence}
    summary(rows)                 一次运行的汇总（给报告/日志用）

判定原则（与主人口径一致）：
    硬拦 > 拦 > 警告 > 通过；**信息不足只警告，不拦** —— 否则整包会被一刀切杀光。
    每条理由都必须带证据（命中词 + 来源 + 原文片段），因为以后 AI 净化要靠它。
"""
from __future__ import annotations

import json
import os
import re
import time

_CFG_PATH = None
_CACHE = {"mtime": 0.0, "data": {}}

DEFAULT_CFG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config", "fine_positioning.json")


def config_path() -> str:
    return _CFG_PATH or DEFAULT_CFG


def set_config_path(path: str) -> None:
    """测试或其它部署可以换配置文件。"""
    global _CFG_PATH
    _CFG_PATH = path
    _CACHE["mtime"] = 0.0


def cfg() -> dict:
    """读配置，按 mtime 热重载。读不到就返回空 dict（调用方按"没规则"处理，不崩）。"""
    p = config_path()
    try:
        m = os.path.getmtime(p)
    except OSError:
        return {}
    if _CACHE["data"] and _CACHE["mtime"] == m:
        return _CACHE["data"]
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:  # noqa: BLE001
        return _CACHE["data"] or {}
    _CACHE["data"], _CACHE["mtime"] = data, m
    return data


# ---------------------------------------------------------------- 匹配工具
def _snip(text: str, word: str, width: int = 18) -> str:
    """截取命中词周围一小段，当证据用。"""
    i = text.find(word)
    if i < 0:
        return ""
    a, b = max(0, i - width), min(len(text), i + len(word) + width)
    return ("…" if a > 0 else "") + text[a:b] + ("…" if b < len(text) else "")


def _hits(text: str, table: dict) -> list[tuple[str, int]]:
    """在 text 里找 table 的词，返回 [(词, 权重)]，按权重降序、去重。返回命中词列表（保持权重顺序）。"""
    if not text or not table:
        return []
    low = text.lower()
    out, seen = [], set()
    for w, wt in table.items():
        if w.startswith("_"):
            continue
        if w and w.lower() in low and w not in seen:
            seen.add(w)
            out.append((w, int(wt or 0)))
    out.sort(key=lambda x: -x[1])
    return out


def _norm_price(v) -> float | None:
    if v in (None, "", "-"):
        return None
    try:
        return float(str(v).replace("¥", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 四个层
def _layer_audience(c: dict, text: str) -> dict:
    a = c.get("audience") or {}
    pos, neg = _hits(text, a.get("positive") or {}), _hits(text, a.get("negative") or {})
    pos_w = sum(w for _, w in pos)
    neg_w = sum(w for _, w in neg)
    # 正向最多计 12 分、负向最多扣 60 分；50 为基准（无任何信号 = 中性偏低，因为"看不出卖给谁"本身是风险）
    score = 50 + min(pos_w * 3, 12) - min(neg_w * 6, 60)
    score = max(0, min(100, score))
    floor = int(a.get("audience_floor") or 25)
    weak = int(c.get("verdict", {}).get("audience_weak_below") or 45)
    reasons, evidence = [], []
    if neg:
        w, wt = neg[0]
        evidence.append({"word": w, "source": "文本", "snippet": _snip(text, w),
                         "weight": wt, "layer": "audience"})
    for w, wt in [x for x in pos if True][:3]:
        evidence.append({"word": w, "source": "文本", "snippet": _snip(text, w),
                         "weight": wt, "layer": "audience"})
    if score < floor and neg:
        reasons.append({"code": "audience_mismatch", "level": "block",
                        "detail": "卖点在打年轻人/潮流市场（%s），与 45+ 微信私域不符，人群分 %d"
                                  % ("、".join(w for w, _ in neg[:3]), score)})
    elif score < weak:
        reasons.append({"code": "audience_weak", "level": "warn",
                        "detail": "没看出面向 45+ 的卖点，人群分 %d（正向词 %d、负面词 %d）"
                                  % (score, len(pos), len(neg))})
    return {"score": score, "positive": [w for w, _ in pos], "negative": [w for w, _ in neg],
            "reasons": reasons, "evidence": evidence}


def _layer_category(c: dict, text: str, core_text: str = "") -> dict:
    """品类层。

    ★ 2026-10-26 加 core_text：区分「卖家写的」与「图上OCR读出来的」。
      实测误拦（行李箱 1389/1384）：
        详情图上有「内衣收纳」→ 命中排除方向「服装鞋帽(内衣)」→ block
        详情图上有「数据线」  → 命中排除方向「数码小件(数据线)」→ block
      但这两件卖的是行李箱，图和描述里说的是「能装内衣」「能放数据线」——
      **用途词不等于品类**。排除方向的意义是「这个类目不做（因为有尺码/易碎）」，
      判据必须落在卖家自己把货归到哪个类，不能靠图上随便一个名词。
      所以：排除词命中时，只有在 core_text（标题/描述/属性）里出现才 block；
            只在 OCR 里出现 → 降级 warn，让人确认。
    """
    cat = c.get("category") or {}
    low = (text or "").lower()
    core_low = (core_text or "").lower()
    hit_preferred, hit_excluded = [], []
    for name, words in (cat.get("preferred") or {}).items():
        if name.startswith("_") or not isinstance(words, list):
            continue          # 配置里带 _说明 之类的注释键，不能当成词表
        for w in words:
            if isinstance(w, str) and w and w.lower() in low:
                hit_preferred.append((name, w))
                break
    ex_group = ""
    for name, words in (cat.get("excluded") or {}).items():
        if name.startswith("_") or not isinstance(words, list):
            continue
        for w in words:
            if isinstance(w, str) and w and w.lower() in low:
                hit_excluded.append((name, w))
                ex_group = ex_group or name
                break
    soft_group, hit_soft = "", []
    for name, words in (cat.get("excluded_soft") or {}).items():
        if name.startswith("_") or not isinstance(words, list):
            continue
        for w in words:
            if isinstance(w, str) and w and w.lower() in low:
                hit_soft.append((name, w))
                soft_group = soft_group or name
                break
    score = 40 if hit_preferred else 20
    if len(hit_preferred) > 1:
        score = 55
    reasons, evidence = [], []
    for grp, w in hit_excluded[:2]:
        evidence.append({"word": w, "source": "文本", "snippet": _snip(text, w),
                         "weight": 0, "layer": "category"})
    # ★ 排除词分两拨：卖家自己写明的 vs 只在图上 OCR 读到的
    hard_ex = [x for x in hit_excluded
               if (not core_text) or (x[1].lower() in core_low)]
    soft_ex = [x for x in hit_excluded if x not in hard_ex]
    if hard_ex:
        score = 0
        reasons.append({"code": "category_excluded", "level": "block",
                        "detail": "命中排除方向「%s」（%s）—— 尺码/易碎/快消复购/母婴/数码小件不做"
                                  % (ex_group, "、".join(w for _, w in hard_ex[:3]))})
    elif soft_ex:
        score = max(20, score - 5)
        reasons.append({"code": "category_excluded_ocr", "level": "warn",
                        "detail": "图上读到「%s」（%s），但标题/描述里没有 —— "
                                  "可能只是「能装内衣/能放数据线」这类用途词，"
                                  "不是真卖那个品类，**请人工确认，不拦货**"
                                  % (soft_group, "、".join(w for _, w in soft_ex[:3]))})
    elif hit_soft:
        score = max(20, score - 10)
        reasons.append({"code": "category_soft", "level": "warn",
                        "detail": "含「%s」等材质/用途词（%s）—— 不是品类不符，只是要确认"
                                  % (soft_group, "、".join(w for _, w in hit_soft[:3]))})
    for nm, w in hit_preferred[:2]:
        evidence.append({"word": w, "source": "文本", "snippet": _snip(text, w),
                         "weight": 0, "layer": "category"})
    for grp, w in hit_soft[:2]:
        evidence.append({"word": w, "source": "文本", "snippet": _snip(text, w),
                         "weight": 0, "layer": "category_soft"})
    return {"score": score, "preferred": [nm for nm, _ in hit_preferred],
            "excluded": [grp for grp, _ in hit_excluded],
            "soft": [grp for grp, _ in hit_soft], "reasons": reasons, "evidence": evidence}


def _layer_price(c: dict, price: float | None) -> dict:
    p = c.get("price") or {}
    sweet = p.get("sweet_spot") or [200, 1500]
    ok = p.get("acceptable") or [100, 3000]
    pen = p.get("penalty") or {}
    reasons, evidence = [], []
    if price is None:
        return {"score": 60, "price": None, "band": "unknown", "reasons": [
            {"code": "price_unknown", "level": "warn", "detail": "卡片价取不到（可能被打码/是定金价），价格层不计分"}],
            "evidence": []}
    if price < float(p.get("too_low_below") or 100):
        score, band = 100 - int(pen.get("too_low") or 15) - 30, "too_low"
        reasons.append({"code": "price_off_band", "level": "warn",
                        "detail": "价格 %.0f 低于目标区间 —— 低价走量款与高客单定位不符，抬价后售后风险高" % price})
    elif price > float(p.get("too_high_above") or 3000):
        score, band = 100 - int(pen.get("too_high") or 15) - 30, "too_high"
        reasons.append({"code": "price_off_band", "level": "warn",
                        "detail": "价格 %.0f 超出可接受上限 —— 45+ 微信用户决策成本高" % price})
    elif float(sweet[0]) <= price <= float(sweet[1]):
        score, band = 100, "sweet"
    else:
        score, band = 75, "acceptable"
    return {"score": score, "price": price, "band": band, "reasons": reasons, "evidence": evidence}


def _layer_risk(c: dict, text: str) -> dict:
    q = c.get("quality_risk") or {}
    hits = _hits(text, q.get("words") or {})
    block_at = int(q.get("block_at") or 4)
    reasons, evidence = [], []
    for w, wt in hits[:3]:
        evidence.append({"word": w, "source": "文本", "snippet": _snip(text, w),
                         "weight": wt, "layer": "risk"})
    hard = [x for x in hits if x[1] >= block_at]
    if hard:
        w, wt = hard[0]
        reasons.append({"code": "quality_risk_hit", "level": "block",
                        "detail": "详情/图片出现「%s」—— 翻新/尾货/侵权风险（权重 %d）" % (w, wt)})
    elif hits:
        reasons.append({"code": "quality_risk_weak", "level": "warn",
                        "detail": "出现「%s」等字样，需人工确认来源" % "、".join(w for w, _ in hits[:2])})
    score = max(0, 100 - sum(wt * 12 for _, wt in hits))
    return {"score": score, "hits": [w for w, _ in hits], "reasons": reasons, "evidence": evidence}


def _layer_violation(c: dict, text: str, word_set=None) -> dict:
    """违禁/极限词：由调用方传入已有 WordSet（flow.py 里那份），没传就跳过。"""
    if word_set is None:
        return {"score": 100, "hits": [], "reasons": [], "evidence": []}
    try:
        hits = word_set.match(text) or []
    except Exception:  # noqa: BLE001
        hits = []
    reasons, evidence = [], []
    for h in hits[:3]:
        word = h[0] if isinstance(h, (list, tuple)) else str(h)
        kind = h[1] if isinstance(h, (list, tuple)) and len(h) > 1 else ""
        evidence.append({"word": word, "source": "文本", "snippet": _snip(text, word),
                         "weight": 0, "layer": "violation", "kind": kind})
    if hits:
        reasons.append({"code": "violation_word", "level": "block",
                        "detail": "命中违禁/极限词：%s" % "、".join(
                            (h[0] if isinstance(h, (list, tuple)) else str(h)) for h in hits[:3])})
    return {"score": 100 if not hits else 40, "hits": [str(h[0] if isinstance(h, (list, tuple)) else h) for h in hits],
            "reasons": reasons, "evidence": evidence}


# ---------------------------------------------------------------- 主入口
def evaluate(product: dict, texts: dict | None = None, price=None,
             word_set=None, info: dict | None = None) -> dict:
    """判一件商品。

    product : 至少含 product_id / original_title（或 title）
    texts   : {"title": str, "desc": str, "attrs": str, "ocr": [(role, seq, text), ...]}
              —— 不传就直接用 product 里的标题；OCR 文本请把图片上的字拼进来
    price   : 数字或 None
    word_set: flow.py 里的 WordSet（有 .match），传了才做违禁词层
    info    : {"has_description": bool, "detail_images": int, "images": int}
    """
    c = cfg()
    t = texts or {}
    title = (t.get("title") or product.get("original_title") or product.get("title") or "").strip()
    desc = (t.get("desc") or "").strip()
    attrs = (t.get("attrs") or "").strip()
    ocr_list = t.get("ocr") or []
    ocr_text = " ".join(x[-1] if isinstance(x, (list, tuple)) else str(x) for x in ocr_list if x)
    # ★★ 2026-10-26 误拦根因修复：判定文本拆成两份。
    #
    #   text_core = 标题 + 描述 + 属性      ← 卖家自己写的字
    #   text_all  = text_core + 图上 OCR    ← 软判（人群/品类/风险）继续吃它
    #
    # 为什么必须拆：OCR 是机器识字，会切错、会认错。
    #   实测（142/9 件包里被拦的 7 件行李箱）：
    #     「极致收纳」被切成「极致收」 → 命中违禁词「极致」
    #     「携于两大有只更优」的噪点   → 命中品牌词「大有」
    #     「360°万向轮」              → 命中品牌词「360」
    #     「1,999」                   → 命中品牌词「999」
    #   这些词在图片层已经被判过一次，结论是 warn +「疑似词库噪声，请人工确认」。
    #   定位层如果还拿 OCR 全文去跑违禁词表，就会把同一条 warn 升级成 block ——
    #   **程序自己推翻自己的判断，还给自己升了级**，一件好货因此被拦。
    #   所以：违禁词层只吃卖家自己写的文本（真写错才该拦）；
    #         图片上的字归图片层判（那里已分 warn/block 分级）。
    text_core = " ".join(x for x in (title, desc, attrs) if x)
    text_all = " ".join(x for x in (title, desc, attrs, ocr_text) if x)
    text = text_all          # 兼容下面原有的软判层

    aud = _layer_audience(c, text)
    cat = _layer_category(c, text, text_core)
    pri = _layer_price(c, _norm_price(price if price is not None else product.get("price_min")))
    rsk = _layer_risk(c, text)
    vio = _layer_violation(c, text_core, word_set)   # ★ 只吃文本层，不吃 OCR 噪声

    reasons = aud["reasons"] + cat["reasons"] + pri["reasons"] + rsk["reasons"] + vio["reasons"]
    evidence = aud["evidence"] + cat["evidence"] + pri["evidence"] + rsk["evidence"] + vio["evidence"]

    # 信息完整度（只警告，不拦）
    inf = info or {}
    completeness = 100
    if inf.get("has_description") is False or (desc == "" and inf.get("has_description") is None):
        levels = []
    if inf:
        if not inf.get("has_description"):
            completeness -= 30
            reasons.append({"code": "no_description", "level": "warn",
                            "detail": "商品描述为空 —— 详情页没采到或卖家没写，无法核对材质与卖点"})
        if not inf.get("detail_images"):
            completeness -= 25
            reasons.append({"code": "no_detail_image", "level": "warn",
                            "detail": "没有详情图 —— 图上文字核对覆盖不到"})
        if int(inf.get("images") or 0) < 3:
            completeness -= 20
            reasons.append({"code": "thin_info", "level": "warn",
                            "detail": "图太少（%s 张），信息太薄" % inf.get("images")})
    completeness = max(0, completeness)

    w = c.get("score_weights") or {}
    score = int(round(
        aud["score"] * float(w.get("audience", 40)) / 100.0
        + cat["score"] * float(w.get("category", 30)) / 100.0
        + pri["score"] * float(w.get("price", 15)) / 100.0
        + completeness * float(w.get("info_completeness", 15)) / 100.0
    ))

    block = any(r["level"] == "block" for r in reasons)
    warn = any(r["level"] == "warn" for r in reasons)
    min_pass = int((c.get("verdict") or {}).get("min_score_pass") or 50)
    if block:
        verdict = "block"
    elif warn or score < min_pass:
        verdict = "warn"
        if score < min_pass and not warn:
            reasons.append({"code": "score_low", "level": "warn",
                            "detail": "综合分 %d 低于 %d（没有硬拦项，但方向不明显）" % (score, min_pass)})
    else:
        verdict = "pass"

    return {
        "product_id": product.get("product_id"),
        "title": title[:120],
        "verdict": verdict,
        "score": score,
        "reasons": reasons,
        "evidence": evidence[:12],
        "layers": {
            "audience": {"score": aud["score"], "positive": aud["positive"], "negative": aud["negative"]},
            "category": {"score": cat["score"], "preferred": cat["preferred"], "excluded": cat["excluded"]},
            "price": {"score": pri["score"], "band": pri["band"], "price": pri["price"]},
            "risk": {"score": rsk["score"], "hits": rsk["hits"]},
            "violation": {"score": vio["score"], "hits": vio["hits"]},
            "info": {"score": completeness},
        },
        "when": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def summary(rows) -> dict:
    rows = list(rows or [])
    n = len(rows) or 1
    cnt = {"pass": 0, "warn": 0, "block": 0}
    codes: dict[str, int] = {}
    for r in rows:
        cnt[r.get("verdict", "warn")] = cnt.get(r.get("verdict", "warn"), 0) + 1
        for x in r.get("reasons") or []:
            codes[x.get("code", "?")] = codes.get(x.get("code", "?"), 0) + 1
    return {
        "total": len(rows),
        "pass": cnt.get("pass", 0), "warn": cnt.get("warn", 0), "block": cnt.get("block", 0),
        "avg_score": round(sum(int(r.get("score") or 0) for r in rows) / n, 1),
        "top_reasons": sorted(codes.items(), key=lambda kv: -kv[1])[:10],
    }
