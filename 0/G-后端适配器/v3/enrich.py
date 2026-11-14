# -*- coding: utf-8 -*-
"""v3 · 商品属性 / 规格补齐（2026-10-24 新增）—— 只从原始卡里读，不猜、不联网。

背景（真卡实测，18 件微店卡）：

· `v3_product.attributes` 全是 `{}` —— 属性一条都没进库，2.5 右侧只能显示"来源未提供"。
· 真卡里其实有，只是没人读：
    `_extra._raw.param[].categorys[]`        参数对（categorys[0].name = 键，[1].name = 值）
                                             18 件里 15 件有，最多 7 组
    `_extra._raw.productSaleAttrs[]`         规格（key=规格名，items[].value + items[].skuIds[]）
                                             8 件有，带图（imgUrl）与每个值对应的 sku id 列表
    `_extra._raw.skuIdList.skuIds[]`         全部 sku id（18 件全有）
    `_extra._raw.spuTotalStock`              库存/已售（18 件全有，值是**字符串**）
    `_extra.saleAttrsText`                   规格文字串（11 件）

口径（按主人规矩）：
1. **只搬运，不推断**。源里没有的键就不写；不拿标题/店铺名猜属性。
2. 逐项带 `source` 路径，能追溯回原 JSON 的哪个位置。
3. 不覆盖已有 attributes 里手工加过的键（`_manual` 段原样保留）。
4. 只写 `v3_product.attributes` 与 `v3_image.sku_id/note` 的空位，不动原图、不动原始 JSON。
"""
from __future__ import annotations

import json

from . import store

SRC_PARAM = "_extra._raw.param"
SRC_SALE = "_extra._raw.productSaleAttrs"
SRC_SKUIDS = "_extra._raw.skuIdList.skuIds"
SRC_STOCK = "_extra._raw.spuTotalStock"
SRC_TEXT = "_extra.saleAttrsText"


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _num(v):
    s = _s(v)
    if not s:
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def parse_param(payload: dict) -> dict:
    """`param[].categorys` → {键: 值}（保持原顺序；缺一半的组跳过，不补空）。"""
    raw = ((payload.get("_extra") or {}).get("_raw") or {})
    out: dict[str, str] = {}
    for grp in (raw.get("param") or []):
        cats = (grp or {}).get("categorys") or []
        if len(cats) < 2:
            continue
        k, v = _s(cats[0].get("name")), _s(cats[1].get("name"))
        if k and v:
            out[k] = v
    return out


def parse_sale_attrs(payload: dict) -> list[dict]:
    """`productSaleAttrs` → [{key, values:[{value, img, sku_ids}]}]。"""
    raw = ((payload.get("_extra") or {}).get("_raw") or {})
    out = []
    for grp in (raw.get("productSaleAttrs") or []):
        key = _s((grp or {}).get("key"))
        vals = []
        for it in ((grp or {}).get("items") or []):
            v = _s((it or {}).get("value"))
            if not v:
                continue
            vals.append({"value": v,
                         "img": _s((it or {}).get("imgUrl")),
                         "sku_ids": [_s(x) for x in ((it or {}).get("skuIds") or []) if _s(x)]})
        if key and vals:
            out.append({"key": key, "values": vals})
    return out


def parse_stock(payload: dict) -> dict:
    raw = ((payload.get("_extra") or {}).get("_raw") or {})
    st = raw.get("spuTotalStock") or {}
    if not st:
        return {}
    out = {}
    for k in ("stockNum", "soldStock", "lockStock", "totalNum", "totalSold"):
        n = _num(st.get(k))
        if n is not None:
            out[k] = n
    return out


def parse_sku_ids(payload: dict) -> list[str]:
    raw = ((payload.get("_extra") or {}).get("_raw") or {})
    ids = (raw.get("skuIdList") or {}).get("skuIds") or []
    return [_s(x) for x in ids if _s(x)]


def build(payload: dict) -> dict:
    """把一张卡里的属性/规格/库存整理成可直接落库的 dict（全部带 source）。"""
    param = parse_param(payload)
    sale = parse_sale_attrs(payload)
    stock = parse_stock(payload)
    sku_ids = parse_sku_ids(payload)

    # sku_id → "规格名:值"（一个 sku 可能同时出现在多个规格维度里，合并）
    per_sku: dict[str, list[str]] = {}
    for dim in sale:
        for it in dim["values"]:
            for sid in it["sku_ids"]:
                per_sku.setdefault(sid, []).append("%s:%s" % (dim["key"], it["value"]))

    out = {
        "_source": "原始商品卡",
        "_note": "只从源 JSON 搬运，未做任何推断；缺的键代表源里没有",
        "param": param,
        "sale_attrs": sale,
        "stock": stock,
        "sku_count": len(sku_ids),
        "sku_ids": sku_ids[:400],
        "sku_spec": {k: "; ".join(v) for k, v in per_sku.items()},
        "_paths": {"param": SRC_PARAM, "sale_attrs": SRC_SALE, "stock": SRC_STOCK,
                   "sku_ids": SRC_SKUIDS, "sale_attrs_text": SRC_TEXT},
        "_counts": {"param": len(param), "sale_attrs": len(sale),
                    "sale_values": sum(len(d["values"]) for d in sale),
                    "sku_with_spec": len(per_sku)},
    }
    txt = _s((payload.get("_extra") or {}).get("saleAttrsText"))
    if txt:
        out["sale_attrs_text"] = txt
    return out


def enrich_product(conn, product_id: int) -> dict:
    """重新解析一件商品并把结果并进 `attributes`（保留 `_manual`）。"""
    row = conn.execute("SELECT original_payload, attributes FROM v3_product WHERE product_id=?",
                       (int(product_id),)).fetchone()
    if row is None:
        return {"ok": False, "product_id": product_id, "error": "库里没这件"}
    try:
        payload = json.loads(row[0] or "{}")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "product_id": product_id, "error": "原始卡不是合法 JSON：%s" % e}
    try:
        old = json.loads(row[1] or "{}") or {}
    except Exception:  # noqa: BLE001
        old = {}
    new = build(payload)
    manual = old.get("_manual")
    merged = dict(new)
    if manual:
        merged["_manual"] = manual
    conn.execute("UPDATE v3_product SET attributes=?, updated_at=datetime('now') WHERE product_id=?",
                 (json.dumps(merged, ensure_ascii=False), int(product_id)))

    # SKU 图与规格对上（只填空位，不覆盖已有值）
    n_linked = 0
    for dim in new["sale_attrs"]:
        for it in dim["values"]:
            if not it["img"] or not it["sku_ids"]:
                continue
            spec = "%s:%s" % (dim["key"], it["value"])
            rows = conn.execute(
                "SELECT image_id, sku_id, note FROM v3_image"
                " WHERE product_id=? AND role='sku' AND original_url=?", (int(product_id), it["img"])).fetchall()
            for iid, sku_id, note in rows:
                sets, vals = [], []
                if not (sku_id or "").strip():
                    sets.append("sku_id=?"); vals.append(it["sku_ids"][0])
                if not (note or "").strip():
                    sets.append("note=?"); vals.append(spec)
                if sets:
                    vals.append(int(iid))
                    conn.execute("UPDATE v3_image SET %s WHERE image_id=?" % ",".join(sets), vals)
                    n_linked += 1
    conn.commit()
    return {"ok": True, "product_id": product_id, "counts": new["_counts"],
            "sku_images_linked": n_linked,
            "has_param": bool(new["param"]), "has_sale": bool(new["sale_attrs"])}


def apply(conn, pids=None) -> dict:
    """批量补齐。`pids=None` = 全库。返回逐件结果 + 合计。"""
    if pids:
        ids = [int(x) for x in pids]
    else:
        ids = [int(r[0]) for r in conn.execute("SELECT product_id FROM v3_product ORDER BY product_id")]
    done, skipped, linked = 0, 0, 0
    rows = []
    for pid in ids:
        r = enrich_product(conn, pid)
        if not r.get("ok"):
            skipped += 1
            rows.append(r)
            continue
        done += 1
        linked += r.get("sku_images_linked") or 0
        rows.append(r)
    return {"ok": True, "total": len(ids), "enriched": done, "failed": skipped,
            "sku_images_linked": linked, "items": rows}


def summary(conn, pids=None) -> dict:
    """只读体检：有多少件拿到了属性/规格/库存。"""
    q = "SELECT product_id, attributes FROM v3_product"
    rows = conn.execute(q).fetchall()
    stat = {"products": len(rows), "with_param": 0, "with_sale": 0,
            "with_stock": 0, "with_sku_spec": 0, "empty": 0}
    for _pid, attr in rows:
        try:
            a = json.loads(attr or "{}") or {}
        except Exception:  # noqa: BLE001
            a = {}
        if a.get("param"):
            stat["with_param"] += 1
        if a.get("sale_attrs"):
            stat["with_sale"] += 1
        if a.get("stock"):
            stat["with_stock"] += 1
        if a.get("sku_spec"):
            stat["with_sku_spec"] += 1
        if not any(a.get(k) for k in ("param", "sale_attrs", "stock")):
            stat["empty"] += 1
    return stat
