# -*- coding: utf-8 -*-
"""抖店送货计划生成器：上货台 v3.db 的一件商品 → plan-<pid>.json（给 dd.mjs push 用）

为什么要单独这一步（而不是在 node 里读 sqlite）：
  · 图片**本地绝对路径**只有 python 侧拿得到（v3_image 两套路径 + replaced 判定）
  · 标题优先级（v3_title_variant.status='selected' 优先）也只有 store 模块知道
  · node 只负责“把东西填进页面”，不碰数据库 —— 分工清楚，改一边不会连累另一边

用法：
  F:\\pyenvs\\douyin\\Scripts\\python.exe mk_plan.py --pid 1234
  ... --pid 1234 --out D:\\x\\plan-1234.json
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

WS = Path(r"G:\Users\Administrator\Documents\deepseek-harness\default-workspace")
TAOBAO = WS / "taobao-picker"
sys.path.insert(0, str(TAOBAO))

from server.v3 import doudian, store  # noqa: E402

V3 = TAOBAO / "data" / "v3.db"


def _local_of(img: dict) -> str:
    """这张图的本地文件绝对路径：替换过的用新文件，否则用原文件。"""
    cur = (img.get("current_local_path") or "").strip()
    orig = (img.get("original_local_path") or "").strip()
    for p in (cur, orig):
        if p and os.path.isfile(p):
            return os.path.abspath(p)
    # 相对路径兜底（可能是相对 taobao-picker 根）
    for p in (cur, orig):
        if not p:
            continue
        for base in (TAOBAO, TAOBAO / "data"):
            q = (base / p)
            if q.is_file():
                return str(q.resolve())
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    if not V3.is_file():
        print("[!] 找不到 v3.db: %s" % V3)
        return 2
    c = sqlite3.connect("file:%s?mode=ro" % str(V3).replace("\\", "/"), uri=True)
    c.row_factory = sqlite3.Row
    p = store.get_product(c, a.pid)
    if not p:
        print("[!] 商品 %s 不存在" % a.pid)
        return 3
    imgs = store.list_images(c, a.pid)

    rows, warn, meta = doudian.build_rows(c, p, "default", imgs)

    main_local = [_local_of(i) for i in sorted(
        [x for x in imgs if x.get("role") == "main"],
        key=lambda x: (x.get("sequence") or 0, x["image_id"]))]
    detail_local = [_local_of(i) for i in sorted(
        [x for x in imgs if x.get("role") == "detail"],
        key=lambda x: (x.get("sequence") or 0, x["image_id"]))]

    # 主图排第一：选定的那张（main_image_id）优先
    mid = p.get("main_image_id")
    if mid:
        for i in imgs:
            if i.get("image_id") == mid and i.get("role") == "main":
                lp = _local_of(i)
                if lp:
                    main_local = [lp] + [x for x in main_local if x != lp]
                break

    skus = []
    for s in c.execute("SELECT sku_id,seq,spec,spec_json,sku_code,price,stock"
                       " FROM v3_sku WHERE product_id=? ORDER BY seq,sku_id", (a.pid,)):
        vals = doudian.spec_values(dict(s))
        img = ""
        sid = str(s["sku_id"])
        for i in imgs:
            if i.get("role") == "sku" and str(i.get("sku_id") or "") == sid:
                img = _local_of(i)
                if img:
                    break
        skus.append({"sku_id": sid, "name": " ".join(vals) or (s["sku_code"] or "默认规格"),
                     "spec": vals, "code": s["sku_code"] or "",
                     "price": s["price"], "stock": s["stock"], "img": img})

    title = (rows[0][1] if rows else "") or (p.get("original_title") or "")
    plan = {
        "pid": a.pid,
        "title": title,
        "category_hint": rows[0][2] if rows else "",
        "rows16": rows,
        "main": [x for x in main_local if x],
        "detail": [x for x in detail_local if x],
        "skus": skus,
        "warns": warn,
        "meta": meta,
        "missing": {
            "main": len([x for x in main_local if not x]),
            "detail": len([x for x in detail_local if not x]),
            "sku_img": len([s for s in skus if not s["img"]]),
        },
    }
    out = a.out or str(Path(__file__).resolve().parent.parent / "crawl3" / ("plan-%d.json" % a.pid))
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)

    print("标题: %s" % title[:50])
    print("主图 %d 张 / 详情 %d 张 / SKU %d 个" % (len(plan["main"]), len(plan["detail"]), len(skus)))
    if plan["missing"]["main"] or plan["missing"]["detail"]:
        print("[!] 缺本地文件：主图 %d、详情 %d" % (plan["missing"]["main"], plan["missing"]["detail"]))
    for w in warn[:8]:
        print("  warn:", w)
    print("已写 %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
