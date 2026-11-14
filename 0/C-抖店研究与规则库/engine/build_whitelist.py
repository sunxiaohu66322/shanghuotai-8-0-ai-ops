#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从「本店真实商品」反推类目白名单
================================
为什么这么做：
  抖店的资质表（/ffa/grs-new/qualification/list）**没有「已开通/未开通」状态列**，
  所以它只能告诉你平台有哪些类目，不能告诉你这个店能建哪些。
  唯一 100% 可信的判据是「这个类目下本店已经存在商品」——
  存在即证明：资质已过、属性可填、能过审。

输入：crawl4/all-products-trim.json（全店 3846 件的裁剪字段）
输出：crawl4/类目白名单.json
      三段：
        certain   —— 本店已建过货的末级类目（可直接建）
        likely    —— 一级已开通、但该末级没用过（建品时需探 status）
        l1_stat   —— 一级类目分布

用法：python build_whitelist.py
"""
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "crawl4", "all-products-trim.json")
OUT = os.path.join(ROOT, "crawl4", "类目白名单.json")

# 用户认可的经营范围（按当前业务：家具/居家/灯饰/户外/家装/厨具为主）
# 注意：这是「允许继续往上走」的一级类目，不是「一定能建」——
#      能不能建看 certain（已建过）或建品时探 status。
ALLOW_L1 = [
    "家具",
    "居家日用",
    "家装灯饰光源",
    "户外装备",
    "家装建材",
    "餐饮厨具",
    "农资园艺",
]

# 明确不做的一级（店里历史上有零星存货，但不属于当前精铺方向）
DENY_L1 = [
    "医疗器械及保健用品",
    "母婴用品",
    "文玩收藏",
    "民俗工艺/非遗",
    "工业品",
    "五金/工具",
    "办公设备及耗材",
    "3C数码及配件",
    "玩具",
    "个人护理",
    "本地生活服务",
    "电器",
]

# 用户口述「要单独出钱」的细分类（2000 一档），除非已建过否则一律打问号
PAID_HINT = ["皮艺", "儿童组合", "组合柜"]


def l1_of(name_or_path):
    s = (name_or_path or "").strip()
    return s.split(">")[0].strip() if s else ""


def main():
    if not os.path.exists(SRC):
        print(f"[x] 缺输入：{SRC}")
        print("    先跑 read_all_products（node）把全店商品拉下来")
        return 2
    rows = json.load(open(SRC, encoding="utf-8"))
    print(f"[i] 商品 {len(rows)} 件")

    cats = {}
    for r in rows:
        cid = r.get("cid")
        if not cid:
            continue
        k = str(cid)
        if k not in cats:
            cats[k] = {
                "cid": cid,
                "name": r.get("cname") or "",
                "l1": l1_of(r.get("cname") or r.get("path") or ""),
                "count": 0,
                "statuses": {},
                "paid_hint": any(w in (r.get("cname") or "") for w in PAID_HINT),
            }
        e = cats[k]
        e["count"] += 1
        s = str(r.get("status"))
        e["statuses"][s] = e["statuses"].get(s, 0) + 1

    certain = sorted(cats.values(), key=lambda x: -x["count"])

    l1_stat = {}
    for c in certain:
        l1 = c["l1"] or "(未知)"
        d = l1_stat.setdefault(l1, {"count": 0, "cids": 0})
        d["count"] += c["count"]
        d["cids"] += 1
    l1_stat = dict(sorted(l1_stat.items(), key=lambda kv: -kv[1]["count"]))

    likely = [
        {
            "l1": k,
            "note": "一级已开通（本店有货），但具体末级需建品时探 status",
            "known_cids": [c["cid"] for c in certain if c["l1"] == k],
        }
        for k in ALLOW_L1
        if k in l1_stat
    ]

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": {
            "file": "crawl4/all-products-trim.json",
            "products": len(rows),
            "basis": "本店真实商品的 category_leaf_id —— 存在即证明该类目可建（资质已过、能过审）",
        },
        "policy": {
            "allow_l1": ALLOW_L1,
            "deny_l1": DENY_L1,
            "paid_hint_words": PAID_HINT,
            "caveat": "抖店资质表无状态列，故「未出现在 certain 的类目」不等于禁止，只等于需探 status",
        },
        "l1_stat": l1_stat,
        "certain_count": len(certain),
        "certain": certain,
        "likely": likely,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[✓] 写出 {OUT}")
    print(f"    确定可建末级类目 {len(certain)} 个")
    print("\n=== 一级分布 ===")
    for k, v in l1_stat.items():
        mark = "✅" if k in ALLOW_L1 else ("⛔" if k in DENY_L1 else "?")
        print(f"  {mark} {k:<16} 商品 {v['count']:>5}   末级 {v['cids']:>4}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
