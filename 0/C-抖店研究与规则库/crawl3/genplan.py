# -*- coding: utf-8 -*-
"""从 v3.db 生成 plan-<pid>.json（供 batch-v6.mjs 使用）
用法: python genplan.py 1390 1393 1404
      python genplan.py --pick 20      # 自动挑 20 件合适商品
"""
import json, os, sys, sqlite3, time

DB = r"G:\Users\Administrator\Documents\deepseek-harness\default-workspace\taobao-picker\data\v3.db"
OUT = r"G:\Users\Administrator\Documents\deepseek-harness\default-workspace\抖店研究\crawl3"
CAT = ["家具", "几类", "茶几/边几"]


def conn():
    return sqlite3.connect(DB)


def ship_days(payload):
    """按主人规则判定抖店发货时效：源商品有 >48 小时的预售/时效 → 7 天发，否则 → 48 小时发。
    判据（有依据才改，没依据默认 48 小时）：
      1. _raw.maxPresaleEndTime > 0 且距今 > 48 小时  → 7 天
      2. 预售标签 presaleTag / presaleTagType 非空      → 7 天
      3. 标题或属性里出现 预售/7天/十五天/30天 等字样  → 7 天
      4. 其余                                          → 48 小时
    返回 (天数, 判据说明)
    """
    try:
        p = json.loads(payload or "{}")
    except Exception:
        return 2, "payload 解析失败，默认 48 小时"
    raw = p.get("_raw") or {}
    now = time.time()

    end = raw.get("maxPresaleEndTime") or 0
    try:
        end = int(end)
    except Exception:
        end = 0
    if end > 0:
        hours = (end - now) / 3600.0
        if hours > 48:
            return 7, "maxPresaleEndTime 距今 %.0f 小时 > 48" % hours
        return 2, "maxPresaleEndTime 距今 %.0f 小时 ≤ 48" % hours

    tag = str(raw.get("presaleTag") or "") + str(raw.get("presaleTagType") or "")
    if tag.strip() and tag.strip() not in ("0", "None"):
        return 7, "presaleTag 非空(%s)" % tag[:20]

    blob = str(p.get("title") or "") + str(p.get("_raw", {}).get("title") or "")
    for k in ("预售", "7天", "七天", "15天", "十五天", "30天", "三十天"):
        if k in blob:
            return 7, "标题含「%s」" % k

    return 2, "源商品无预售/长时效证据，按规则选 48 小时"


def candidates(n):
    c = conn()
    rows = c.execute("""
        select p.product_id, p.original_title,
               (select count(*) from v3_sku s where s.product_id=p.product_id) nsku,
               (select count(*) from v3_image i where i.product_id=p.product_id and i.role='main' and i.dl_state='ok') nmain
        from v3_product p
        where (select count(*) from v3_sku s where s.product_id=p.product_id) between 1 and 8
          and (select count(*) from v3_image i where i.product_id=p.product_id and i.role='main' and i.dl_state='ok') >= 3
        order by p.product_id
    """).fetchall()
    c.close()
    seen, out = set(), []
    for pid, title, nsku, nmain in rows:
        key = (title or "")[:18]
        if key in seen:
            continue
        seen.add(key)
        out.append(pid)
        if len(out) >= n:
            break
    return out


def build(pid):
    c = conn()
    p = c.execute("select product_id,original_title,category_path,price_min,original_payload from v3_product where product_id=?", (pid,)).fetchone()
    if not p:
        c.close(); return None
    _, title, catpath, pmin, payload = p
    imgs = c.execute("""select role, current_local_path, sku_id, sequence from v3_image
                        where product_id=? and dl_state='ok' order by role, sequence""", (pid,)).fetchall()
    main = [r[1] for r in imgs if r[0] == "main" and r[1]]
    detail = [r[1] for r in imgs if r[0] == "detail" and r[1]]
    video = [r[1] for r in imgs if r[0] == "video" and r[1]]
    # SKU 图：按 sku_id 精确配对（老代码用 seq 去查以 sku_id 为键的字典，键永远对不上，
    # 导致所有 SKU 都回退成主图第一张 —— 这就是"SKU 图全一样"的根因）
    skuimg = {}
    for role, lpath, sku_id, seq in imgs:
        if role == "sku" and sku_id and lpath:
            skuimg.setdefault(str(sku_id), lpath)
    skus = []
    for seq, sku_id, spec, price, stock in c.execute(
            "select seq,sku_id,spec,price,stock from v3_sku where product_id=? order by seq", (pid,)).fetchall():
        vals = []
        if spec:
            for part in str(spec).split("/"):
                if ":" in part:
                    vals.append(part.split(":", 1)[1].strip())
                elif part.strip():
                    vals.append(part.strip())
        name = "/".join(vals) if vals else (str(spec or "") or f"规格{seq+1}")
        skus.append({
            "name": name, "spec": [name], "price": float(price or pmin or 0),
            "stock": int(stock or 0), "sku_id": str(sku_id) if sku_id else None,
            "img": skuimg.get(str(sku_id)) or (main[0] if main else None),
            "img_matched": bool(skuimg.get(str(sku_id))),
        })
    c.close()
    cat = CAT
    if catpath:
        parts = [x.strip() for x in str(catpath).replace("/", ">").split(">") if x.strip()]
        if len(parts) >= 2:
            cat = parts
    days, why = ship_days(payload)
    plan = {
        "pid": pid, "title": title or "", "category_hint": catpath or "",
        "main": main, "detail": detail, "video": video, "skus": skus,
        "price": skus[0]["price"] if skus else float(pmin or 0),
        "stock": skus[0]["stock"] if skus else 0,
        "cat": cat, "brand": "LZL", "wood": None, "install": "不提供上门安装",
        "ship_days": days, "ship_reason": why,
        "meta": {"product_id": pid, "skus": len(skus), "main": len(main),
                 "detail": len(detail), "video": len(video),
                 "sku_img_distinct": len({s["img"] for s in skus if s["img"]})},
        "missing": {"main": 0 if main else 1, "detail": 0 if detail else 1,
                    "video": 0 if video else 1,
                    "sku_img": sum(1 for s in skus if not s.get("img_matched"))},
    }
    return plan


def main():
    args = [a for a in sys.argv[1:]]
    if args and args[0] == "--pick":
        pids = candidates(int(args[1]) if len(args) > 1 else 20)
    else:
        pids = [int(a) for a in args if a.isdigit()]
    if not pids:
        print("no pids"); return
    ok = 0
    for pid in pids:
        plan = build(pid)
        if not plan:
            print(f"[skip] {pid} not found"); continue
        path = os.path.join(OUT, f"plan-{pid}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=1)
        print(f"[ok] {pid} skus={plan['meta']['skus']} main={plan['meta']['main']} detail={plan['meta']['detail']} -> {os.path.basename(path)}")
        ok += 1
    print(f"DONE {ok}/{len(pids)}")


if __name__ == "__main__":
    main()
