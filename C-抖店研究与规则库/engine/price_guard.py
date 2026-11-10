#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抖店价格违规计算器（SKU 级）
================================
规则全部来自平台官方原文，不来自经验或二手文章：
  R1 虚构被比较价格       《商家【价格违规】处置细则》5.2 —— "售价50，虚构原价1000"
  R2 价格设定不合理/异常   《价格违规》5.1 —— "不在类目常规区间""标价99999元起"
  R3 同商品 SKU 价差异常   《价格违规》5.1 —— "同商品下各SKU之间的销售价格价差存在显然不合理性"
  R4 赠品价高于主商品      《价格违规》5.1 —— "售价288元，赠品标价1761元"
  R5 标题宣传价格          《价格违规》5.2 —— "禁止在标题中宣传价格"
  R6 违规价格营销词        《价格违规》5.2 —— 跳楼价/清仓价/亏本价/甩卖价/白菜价/恐怖价/自杀价/倒闭价/破产价/处理价
  R7 限时优惠未注明时间    《价格违规》5.2 / 《商品价格管理规则》2.2.3

单位铁律：接口返回的价格全部是「分」（整数），表单里是「元」（两位小数）。
          本脚本内部统一按分计算，输出时转元。
用法：
  python price_guard.py <商品JSON>            # 分析一个商品（含 skus）
  python price_guard.py <文件> --list         # 分析 tproduct/list 的整份返回
  python price_guard.py --selftest            # 用规则原文的案例自测
"""
import json
import re
import sys

# ── 规则阈值（可调，但每次调都要写进 risk_profile）────────────────────────
PROFILE = {
    "r1_markup_ratio": 3.0,        # 划线价/售价 ≥ 3 倍 → 虚构被比较价格嫌疑
    "r1_hard_ratio": 5.0,          # ≥ 5 倍 → 高危
    "r2_abs_high_cents": 1000000,  # 划线价 ≥ 1 万元 → 绝对高价异常（家具类可调高）
    "r2_abs_low_cents": 100,       # 售价 < 1 元 → 低价引流嫌疑
    "r3_sku_spread": 3.0,          # 同商品 SKU 最高/最低价 ≥ 3 倍 → 价差异常
    "r4_gift_over": 1.0,           # 赠品价 / 主商品价 > 1.0 → 命中
}

# ── R6 违规价格营销词（官方原文列举，全部收进）─────────────────────────────
BAD_PRICE_WORDS = [
    "跳楼价", "清仓价", "亏本价", "甩卖价", "白菜价", "恐怖价",
    "自杀价", "倒闭价", "破产价", "处理价",
    # 同源高危词（同条规则语境：诱导点击的价格营销词）
    "骨折价", "血亏", "赔本", "全网最低", "史上最低", "最低价",
]

# ── R7 限时词（用了就必须标注起止时间）────────────────────────────────────
LIMITED_WORDS = ["限时特价", "限时秒杀", "限时优惠", "今日特价", "仅此一天", "最后一天"]

# 标题里出现价格数字：¥/￥ / 元 / "799" 这类裸价
PRICE_IN_TITLE = re.compile(r"(¥|￥|\d+\s*元|包邮价|到手价|券后价)")

CENTS = lambda v: int(v or 0)          # 防御 None / str
YUAN = lambda c: round(CENTS(c) / 100.0, 2)


def _pick(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, "", [], {}):
            return d[k]
    return default


def _sku_rows(product):
    """把 skus 归一化成 [{name, price_cents, stock, market_cents}]，字段名自适应。"""
    rows = []
    for s in (product.get("skus") or []):
        if not isinstance(s, dict):
            continue
        price = _pick(s, "price", "discount_price", "sale_price", "sell_price", default=0)
        market = _pick(s, "market_price", "origin_price", "line_price", default=0)
        # 有些返回把价格塞在 price_info / sku_price 这类嵌套里
        if not price:
            for k, v in s.items():
                if "price" in k.lower() and isinstance(v, (int, float)) and v:
                    price = v
                    break
        rows.append({
            "name": _pick(s, "name", "spec_name", "sku_name", default=""),
            "price_cents": CENTS(price),
            "market_cents": CENTS(market),
            "stock": CENTS(_pick(s, "stock_num", "stock", default=0)),
            "id": str(_pick(s, "sku_id", "id", default="")),
        })
    return rows


def analyze(product, profile=None):
    """返回 {level, hits[], skus[], suggest}"""
    P = {**PROFILE, **(profile or {})}
    hits = []
    name = product.get("name") or product.get("title") or ""
    sell = CENTS(_pick(product, "discount_price", "price", "sell_price", default=0))
    market = CENTS(_pick(product, "market_price", "origin_price", "line_price", default=0))

    # 主价格优先用 sku 区间（更接近真实成交）
    rows = _sku_rows(product)
    prices = [r["price_cents"] for r in rows if r["price_cents"] > 0]
    if prices:
        sell = min(prices) if not sell else sell

    # R1 虚构被比较价格
    if sell > 0 and market > 0:
        ratio = market / sell
        if ratio >= P["r1_hard_ratio"]:
            hits.append(dict(rule="R1", level="high", ratio=round(ratio, 2),
                             msg=f"划线价 {YUAN(market)} 元 = 售价 {YUAN(sell)} 元的 {ratio:.1f} 倍，命中「虚构被比较价格」高危区间"))
        elif ratio >= P["r1_markup_ratio"]:
            hits.append(dict(rule="R1", level="medium", ratio=round(ratio, 2),
                             msg=f"划线价 {YUAN(market)} 元 / 售价 {YUAN(sell)} 元 = {ratio:.1f} 倍，超过 {P['r1_markup_ratio']} 倍阈值"))

    # R2 绝对价格异常
    if market >= P["r2_abs_high_cents"] and sell > 0 and market / max(sell, 1) >= 2:
        hits.append(dict(rule="R2", level="medium",
                         msg=f"划线价 {YUAN(market)} 元 超过 {YUAN(P['r2_abs_high_cents'])} 元且倍率≥2，属「价格设定不合理」嫌疑"))
    if 0 < sell < P["r2_abs_low_cents"]:
        hits.append(dict(rule="R2", level="high",
                         msg=f"售价 {YUAN(sell)} 元 低于 {YUAN(P['r2_abs_low_cents'])} 元，属低价 SKU 引流"))

    # R3 SKU 价差
    if len(prices) >= 2:
        lo, hi = min(prices), max(prices)
        if lo > 0 and hi / lo >= P["r3_sku_spread"]:
            hits.append(dict(rule="R3", level="high", spread=round(hi / lo, 2),
                             msg=f"SKU 价差 {hi/lo:.1f} 倍（{YUAN(lo)} ~ {YUAN(hi)} 元），命中「同商品 SKU 价差显然不合理」"))
        elif lo > 0 and hi / lo >= 2:
            hits.append(dict(rule="R3", level="low", spread=round(hi / lo, 2),
                             msg=f"SKU 价差 {hi/lo:.1f} 倍（{YUAN(lo)} ~ {YUAN(hi)} 元），临近阈值，建议核对规格差异是否真实"))

    # R5 标题宣传价格
    if PRICE_IN_TITLE.search(name):
        hits.append(dict(rule="R5", level="high",
                         msg=f"标题出现价格表述：「{PRICE_IN_TITLE.search(name).group(0)}」，规则明令禁止在标题中宣传价格"))

    # R6 违规价格营销词
    found = [w for w in BAD_PRICE_WORDS if w in name]
    if found:
        hits.append(dict(rule="R6", level="high", words=found,
                         msg="标题含违规价格营销词：" + "、".join(found)))

    # R7 限时词但无时间
    lim = [w for w in LIMITED_WORDS if w in name]
    if lim and not re.search(r"\d{1,2}[月./-]\d{1,2}|\d{1,2}:\d{2}", name):
        hits.append(dict(rule="R7", level="medium", words=lim,
                         msg="标题用了限时优惠词但未标注起止时间：" + "、".join(lim)))

    # R4 赠品（数据结构里通常没有，留给人工确认位）
    gift = _pick(product, "gift_price", "present_price")
    if gift and sell and CENTS(gift) > sell * P["r4_gift_over"]:
        hits.append(dict(rule="R4", level="high",
                         msg=f"赠品标价 {YUAN(gift)} 元 高于主商品 {YUAN(sell)} 元"))

    level = "ok"
    if any(h["level"] == "high" for h in hits):
        level = "block"
    elif hits:
        level = "review"

    return {
        "product_id": str(_pick(product, "product_id", "id", default="")),
        "name": name,
        "category": _pick(product, "category_name", "shop_category_path", "category", default=""),
        "leaf_cid": _pick(product, "category_leaf_id", default=None),
        "sell_yuan": YUAN(sell),
        "market_yuan": YUAN(market),
        "markup": round(market / sell, 2) if sell else None,
        "level": level,
        "hits": hits,
        "sku_count": len(rows),
        "skus": rows,
        "suggest": {
            "block": "发布前必须改：删违规词/改标题价格/下调划线价或改为真实参考价并准备证明材料",
            "review": "建议人工核一遍再发：核对划线价依据、SKU 规格差异是否真实",
            "ok": "无价格规则命中",
        }[level],
    }


def _selftest():
    """用规则原文里的案例当断言，确认阈值不是拍脑袋。"""
    cases = [
        # (商品, 期望命中规则)
        ({"name": "水杯", "market_price": 100000, "discount_price": 5000}, "R1"),        # 售价50元，虚构原价1000元
        ({"name": "气垫", "market_price": 9999900, "discount_price": 9900}, "R2"),        # 标价99999元起
        ({"name": "床垫", "market_price": 50000, "discount_price": 40000,
          "skus": [{"price": 40000}, {"price": 2000}]}, "R3"),                            # SKU 价差 20 倍
        ({"name": "清仓价实木床 999元", "market_price": 30000, "discount_price": 20000}, "R5"),
        ({"name": "亏本价甩卖餐桌", "market_price": 30000, "discount_price": 20000}, "R6"),
        ({"name": "限时特价沙发", "market_price": 30000, "discount_price": 20000}, "R7"),
    ]
    ok = 0
    for prod, want in cases:
        r = analyze(prod)
        got = {h["rule"] for h in r["hits"]}
        flag = "PASS" if want in got else "FAIL"
        ok += want in got
        print(f"  [{flag}] 期望 {want} 实得 {sorted(got) or '无'}  <- {prod['name']}")
    print(f"\n自测 {ok}/{len(cases)} 通过")
    return ok == len(cases)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}

    if "--selftest" in flags:
        sys.exit(0 if _selftest() else 1)

    if not args:
        print(__doc__)
        sys.exit(0)

    with open(args[0], encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict) and "items" in data:
        items = data["items"]
    elif isinstance(data, dict) and "data" in data:
        items = data["data"]
    else:
        items = data
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        items = [data]

    results = [analyze(x) for x in items if isinstance(x, dict)]
    summary = {"block": 0, "review": 0, "ok": 0}
    for r in results:
        summary[r["level"]] += 1

    print(f"共 {len(results)} 件｜必须改 {summary['block']}｜需复核 {summary['review']}｜通过 {summary['ok']}\n")
    for r in sorted(results, key=lambda x: (x["level"] != "block", x["level"] != "review")):
        if r["level"] == "ok":
            continue
        print(f"[{r['level'].upper()}] {r['product_id']} {r['name'][:36]}")
        print(f"        售价 {r['sell_yuan']} / 划线 {r['market_yuan']}（{r['markup']}x） SKU {r['sku_count']} 个  cid={r['leaf_cid']}")
        for h in r["hits"]:
            print(f"        - {h['rule']} {h['msg']}")
        print()

    if "--json" in flags:
        print(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
