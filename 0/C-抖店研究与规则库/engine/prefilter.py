#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抖店上货初筛过滤器
==================
目的：在「采集完 → 建品」之间加一道闸，把注定建不成的商品提前剔掉，
      不浪费建品动作（抖店必须逐个建，一次建品代价很高）。

三道判据：
  L 类目闸   cid 是否在「本店已建过的末级类目」白名单里
             （白名单来自 build_whitelist.py，依据是本店真实商品的 category_leaf_id）
  P 价格闸   复用 price_guard.analyze()，规则全部来自平台官方原文
  T 标题闸   违规价格营销词 / 标题宣传价格（price_guard 已覆盖，这里只做入参归一）

三档结论：
  PASS    可直接建（cid 白名单内 + 无高危价格问题）
  PROBE   需要探类目状态（无 cid，或 cid 不在白名单 —— 建品时读系统返回的 status）
  DROP    直接剔掉（类目不在经营范围 / 价格高危 / 标题违规）

用法：
  python prefilter.py <候选商品.json>            # 输出 CSV + 汇总
  python prefilter.py <候选商品.json> --csv out.csv
  python prefilter.py --selftest                # 自测（含正反样例）

候选商品字段（自适应，有哪个用哪个）：
  name/title, cid/category_leaf_id, cname/category_name,
  discount_price/price, market_price, skus[]
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WL = os.path.join(ROOT, "crawl4", "类目白名单.json")

sys.path.insert(0, HERE)
try:
    import price_guard
except Exception as e:  # pragma: no cover
    price_guard = None
    _pg_err = str(e)

# ── 标题 → 一级类目 的关键词（只看「明显属于哪一类」，不用于定类目）──────────
# 这是粗筛，宁可漏判成 PROBE，也不要把能建的说成不能建。
L1_KW = {
    "家具": ["床", "柜", "桌", "椅", "凳", "沙发", "架", "几", "床垫", "案", "屏风", "镜", "榻"],
    "居家日用": ["收纳", "床品", "四件套", "靠垫", "抱枕", "坐垫", "沙发垫", "桌布", "被", "毯", "凉席", "枕"],
    "家装灯饰光源": ["灯", "吊灯", "台灯", "落地灯", "壁灯", "灯串", "灯带", "射灯", "吸顶灯"],
    "户外装备": ["帐篷", "天幕", "睡袋", "野餐", "防潮垫", "折叠椅", "钓", "登山", "露营", "望远镜"],
    "家装建材": ["卫浴", "置物架", "扶手", "坐便", "浴室", "密封胶", "墙板", "五金的搭"],
    "餐饮厨具": ["茶具", "茶盘", "餐具", "锅", "厨房", "果盘", "酒架", "调味"],
    "农资园艺": ["花盆", "花架", "花几", "花瓶", "园艺", "篱笆", "栅栏", "种子", "肥料"],
}
# 明确不属于精铺方向的强特征词（命中即 DROP_L1，除非 cid 在白名单里）
DENY_KW = [
    "手机", "耳机", "充电器", "笔记本", "键盘", "鼠标", "显卡", "硬盘",
    "口红", "面膜", "精华", "洗发水", "香水", "粉底",
    "奶粉", "纸尿裤", "奶瓶", "婴儿车",
    "保健", "血糖", "血压计", "膏药", "护具",
    "机床", "起重", "液压", "扳手", "电钻",
    "玩具", "积木", "娃娃",
    "花瓶摆件收藏", "字画", "邮票", "钱币",
]


def _norm(p):
    """把候选商品归一化成统一结构"""
    get = lambda *ks, d=None: next((p[k] for k in ks if isinstance(p, dict) and p.get(k) not in (None, "", [])), d)
    name = get("name", "title", "商品标题", d="") or ""
    cid = get("cid", "category_leaf_id", "category_id", "类目id")
    cname = get("cname", "category_name", "类目", d="") or ""
    return {
        "name": name,
        "cid": cid,
        "cname": cname,
        "discount_price": get("discount_price", "price", "sale_price", "dp", "到手价", d=0),
        "market_price": get("market_price", "origin_price", "line_price", "mp", "划线价", d=0),
        "skus": get("skus", "sku_list", d=[]) or [],
    }


def guess_l1(name, cname=""):
    if cname:
        return cname.split(">")[0].strip()
    for l1, kws in L1_KW.items():
        for kw in kws:
            if kw and kw in name:
                return l1
    return ""


def judge(p, wl):
    """返回 {verdict, reasons[], l1, cid, price}"""
    n = _norm(p)
    certain = {str(c["cid"]) for c in wl["certain"]}
    allow = set(wl["policy"]["allow_l1"])
    deny = set(wl["policy"]["deny_l1"])
    reasons = []

    cid = n["cid"]
    l1 = guess_l1(n["name"], n["cname"])

    # ── L 类目闸 ──────────────────────────────────────────────
    if cid is not None and str(cid) in certain:
        verdict = "PASS"
        reasons.append(f"cid {cid} 在本店已建过货的类目白名单内")
    elif l1 and l1 in deny:
        verdict = "DROP"
        reasons.append(f"一级类目「{l1}」不在精铺范围")
    else:
        hit_deny = [k for k in DENY_KW if k in n["name"]]
        if hit_deny and not (cid is not None and str(cid) in certain):
            verdict = "DROP"
            reasons.append("标题含非经营品类强特征词：" + "/".join(hit_deny[:3]))
        elif not l1:
            verdict = "PROBE"
            reasons.append("无 cid 且标题判不出一级类目 → 需建品时读系统预测")
        elif l1 in allow:
            verdict = "PROBE"
            reasons.append(f"一级「{l1}」在经营范围，但该末级未在白名单 → 建品时探 status")
        else:
            verdict = "PROBE"
            reasons.append(f"一级「{l1}」未确认 → 需探 status")

    # ── P 价格闸 ──────────────────────────────────────────────
    price_info = {"level": None, "hits": []}
    if price_guard is not None:
        a = price_guard.analyze({
            "name": n["name"],
            "discount_price": n["discount_price"],
            "market_price": n["market_price"],
            "skus": n["skus"],
        })
        price_info = {"level": a.get("level"), "hits": [h.get("rule") + ":" + h.get("level") for h in a.get("hits", [])]}
        hi = [h for h in a.get("hits", []) if h.get("level") == "high"]
        med = [h for h in a.get("hits", []) if h.get("level") == "medium"]
        if hi:
            reasons.append("价格高危：" + "；".join(h["msg"] for h in hi[:2]))
            if verdict == "PASS":
                verdict = "PROBE"
        elif med:
            reasons.append("价格需复核：" + "；".join(h["msg"] for h in med[:2]))
    return {
        "verdict": verdict,
        "l1": l1,
        "cid": cid,
        "name": n["name"][:40],
        "discount": n["discount_price"],
        "market": n["market_price"],
        "price_level": price_info["level"],
        "reasons": reasons,
    }


def run(path, csv_out=None):
    if not os.path.exists(WL):
        print(f"[x] 缺白名单 {WL}，先跑 build_whitelist.py")
        return 2
    wl = json.load(open(WL, encoding="utf-8"))
    raw = json.load(open(path, encoding="utf-8"))
    items = raw if isinstance(raw, list) else (raw.get("items") or raw.get("products") or raw.get("data") or [])
    print(f"[i] 候选 {len(items)} 件 | 白名单末级类目 {wl['certain_count']} 个")

    res = [judge(p, wl) for p in items]
    stat = {}
    for r in res:
        stat[r["verdict"]] = stat.get(r["verdict"], 0) + 1

    print("\n=== 初筛结果 ===")
    for k in ("PASS", "PROBE", "DROP"):
        print(f"  {k:<6} {stat.get(k, 0):>5}")

    print("\n--- DROP 样例（前 10）---")
    for r in [x for x in res if x["verdict"] == "DROP"][:10]:
        print(f"  [{r['l1'] or '?'}] {r['name']}  ← {'；'.join(r['reasons'])[:80]}")

    out_csv = csv_out or os.path.join(ROOT, "crawl4", "初筛结果.csv")
    import csv
    with open(out_csv, "w", encoding="gbk", errors="replace", newline="") as f:
        w = csv.writer(f)
        w.writerow(["结论", "一级类目", "类目cid", "商品标题", "售价", "划线价", "价格风险", "理由"])
        for r in res:
            w.writerow([r["verdict"], r["l1"], r["cid"], r["name"], r["discount"], r["market"],
                        r["price_level"] or "", "；".join(r["reasons"])])
    print(f"\n[✓] {out_csv}")
    return 0


def _selftest():
    wl = json.load(open(WL, encoding="utf-8"))
    good = {"name": "实木床架双人床 1.8米 胡桃色", "cid": 1000009324,
            "discount_price": 39900, "market_price": 59900}
    no_cid = {"name": "落地灯 北欧客厅氛围灯", "discount_price": 19900, "market_price": 29900}
    bad_l1 = {"name": "华为手机充电器快充头", "discount_price": 2900}
    bad_price = {"name": "实木沙发垫", "cid": 1000004539,
                 "discount_price": 47600, "market_price": 609280}
    cases = [
        ("白名单内+价格正常", good, "PASS"),
        ("无 cid 但一级可判", no_cid, "PROBE"),
        ("非经营品类", bad_l1, "DROP"),
        ("白名单内但价格高危", bad_price, "PROBE"),
    ]
    ok = 0
    for label, p, want in cases:
        r = judge(p, wl)
        flag = "✅" if r["verdict"] == want else "❌"
        ok += r["verdict"] == want
        print(f"  {flag} {label}: {r['verdict']} (期望 {want})  {'；'.join(r['reasons'])[:70]}")
    print(f"\n自测 {ok}/{len(cases)} 通过")
    return 0 if ok == len(cases) else 1


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    if args[0] == "--selftest":
        return _selftest()
    csv_out = None
    if "--csv" in args:
        csv_out = args[args.index("--csv") + 1]
        args = [a for i, a in enumerate(args) if a != "--csv" and (i == 0 or args[i - 1] != "--csv")]
    return run(args[0], csv_out)


if __name__ == "__main__":
    sys.exit(main())
