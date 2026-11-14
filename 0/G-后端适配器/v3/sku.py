# -*- coding: utf-8 -*-
"""v3 · SKU 明细表（2026-10-25 新增）—— 把原始卡里的 SKU 拆成真正的行。

背景（真卡实测，18 件微店卡）：

· `v3_product` **没有 SKU 子表**，`original_payload.skus` 顶层是**空数组**
  （采集卡就是这么给的），SKU 六列因此永远出表为空。
· 真数据其实齐，只是藏在三个地方：
    `_extra._raw.productSkus[]`
        `.skuKey.skuId`                        sku 主键
        `.productSkuInfo.thumbImg`             sku 图
        `.productSkuInfo.skuCode`              sku 编码（如 6610-1曜石黑-36）
        `.productSkuInfo.salePrice`            **分**，需 /100
        `.productSkuInfo.stockInfo.stockNum`   库存（**字符串**）
        `.productSkuInfo.saleParam[].categorys`  [0]=规格名 [1]=规格值
    `_extra.skuSpecs[]`       `.skuId / .price / .originPrice / .spec`（price 已是**元**字符串）
    `_extra.skuAttrs[]`       `.skuId / .values[]`（['颜色','6610-1黑曜石','尺码','36']）

口径（按主人规矩）：
1. **只搬运，不推断**。源里没有的字段写 NULL，不拿标题猜规格。
2. 价格同时保留源字段名（`price`/`originPrice`），**当两者相等就说明源侧没有区分**，
   由出表器自己决定填哪一列，不在这一层编。
3. **不覆盖** `original_payload`，只新增 `v3_sku` 行。
"""
from __future__ import annotations

import json

TABLE = "v3_sku"

SQL = """
CREATE TABLE IF NOT EXISTS v3_sku (
    product_id   INTEGER NOT NULL,
    sku_id       TEXT    NOT NULL,
    seq          INTEGER,
    spec         TEXT,              -- "颜色:6610-1黑曜石/尺码:36"
    spec_json    TEXT,              -- [{"key":"颜色","value":"..."}]
    sku_code     TEXT,
    price        REAL,              -- 源 price（元）
    price_origin REAL,              -- 源 originPrice（元）
    price_raw    REAL,              -- salePrice/100（元，来自 productSkus）
    stock        INTEGER,
    img_url      TEXT,
    raw_json     TEXT,
    source       TEXT,
    updated_at   TEXT,
    PRIMARY KEY (product_id, sku_id)
)
"""

IDX = ("CREATE INDEX IF NOT EXISTS ix_v3_sku_pid ON v3_sku(product_id, seq)",)


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _fen(v):
    """分 → 元。只做 /100，不做舍入推断。"""
    n = _num(v)
    return None if n is None else round(n / 100.0, 2)


def ensure(conn):
    conn.execute(SQL)
    for s in IDX:
        conn.execute(s)
    conn.commit()


def parse(payload: dict) -> list[dict]:
    """一张卡 → SKU 行列表（按源顺序）。三个来源按 skuId 合并，缺的留空。

    ★ 2026-10-26 修：**兼容两种 payload 形状**。
    原来只认 `payload["_extra"]["skuSpecs"]` 这一种（嵌套形状，来自 parse_card 的输出）。
    但 `v3_product.original_payload` 存的是**商品卡原始 JSON**，它是**扁平的** ——
    实测 `10000974547427.json` 顶层就直接有 `skuSpecs`(16) / `skuAttrs`(16)，
    `productSkus`(16) 在 `payload["_raw"]` 下，**根本没有 `_extra` 这一层**。
    结果：`sku.parse` 恒返回 0 行，`sku.apply` 跑成功但一条 SKU 都没写进库
    —— 这就是主人报的「SKU 没有下载」的准确原因（不是下载环节，是解析层结构不匹配）。

    现在两种都认：有 `_extra` 用它，没有就用 payload 本身当 ext；
    `_raw` 同理（ext 里没有就去 payload 顶层拿）。
    """
    ext = payload.get("_extra") or payload
    raw = ext.get("_raw") or payload.get("_raw") or {}

    # ⓪-b 2026-11-14 拼多多「老版页面快照」卡（百度网盘 H:\bdpan-txt-download）：
    #    顶层 store.initDataObj.goods，SKU 在 goods.skus[]（不是顶层 sku[]），
    #    字段是 skuId/thumbUrl/quantity/specs[] 驼峰，价格 groupPrice/normalPrice
    #    已是「元」字符串（★ 不除 100；oldGroupPrice 才是分，划线价不取），
    #    specs 是真 list[dict]（spec_key/spec_value），不是 repr 字符串。
    #    下游 v3_image.role='sku'.sku_id 与本表 sku_id 用同一个值，出表按它对齐。
    legacy = ((payload.get("store") or {}).get("initDataObj") or {}).get("goods")
    if isinstance(legacy, dict) and legacy.get("skus"):
        out: list[dict] = []
        for i, s in enumerate(legacy["skus"] or []):
            if not isinstance(s, dict):
                continue
            sid = _s(s.get("skuId") or s.get("skuID") or s.get("sku_id"))
            if not sid:
                continue
            raw_specs = s.get("specs") or []
            if isinstance(raw_specs, str):
                import ast
                try:
                    raw_specs = ast.literal_eval(raw_specs)
                except (ValueError, SyntaxError):
                    raw_specs = []
            pairs = [{"key": _s(x.get("spec_key")), "value": _s(x.get("spec_value"))}
                     for x in raw_specs if isinstance(x, dict)
                     and _s(x.get("spec_key")) and _s(x.get("spec_value"))]
            spec = "/".join("%s:%s" % (p["key"], p["value"]) for p in pairs) or _s(s.get("spec"))
            price = None
            for k in ("groupPrice", "normalPrice", "skuPrice"):
                n = _num(s.get(k))
                if n and n > 0:
                    price = round(n, 2)
                    break
            out.append({
                "sku_id": sid, "seq": i,
                "spec": spec, "spec_json": json.dumps(pairs, ensure_ascii=False),
                "sku_code": "", "price": price,
                "price_origin": None if _num(s.get("normalPrice")) is None
                                else round(_num(s.get("normalPrice")), 2),
                "price_raw": price,
                "stock": None if _num(s.get("quantity")) is None else int(_num(s.get("quantity"))),
                "img_url": _s(s.get("thumbUrl") or s.get("thumb_url")),
                "raw_json": json.dumps(s, ensure_ascii=False)[:4000],
                "source": "pdd_legacy.goods.skus[]",
            })
        return out

    # ⓪ 2026-11-09 拼多多详情原始响应（多米数据助手导出）：
    #    顶层直接是 sku[]（微店那三段一个都不匹配，实测 _sku.apply 恒 0 行）。
    #    sku_id / thumb_url / quantity 是字符串，group_price / normal_price 是「分」的字符串，
    #    specs 是 Python-repr 字符串 [{'spec_key':'款式','spec_value':'…'}]。
    if isinstance(payload.get("goods"), dict) and payload.get("sku"):
        import ast
        out: list[dict] = []
        for i, s in enumerate(payload["sku"] or []):
            if not isinstance(s, dict):
                continue
            sid = _s(s.get("sku_id"))
            if not sid:
                continue
            try:
                spec_items = ast.literal_eval(s["specs"]) if isinstance(s.get("specs"), str) else (s.get("specs") or [])
            except (ValueError, SyntaxError):
                spec_items = []
            pairs = [{"key": _s(x.get("spec_key")), "value": _s(x.get("spec_value"))}
                     for x in spec_items if isinstance(x, dict)
                     and _s(x.get("spec_key")) and _s(x.get("spec_value"))]
            spec = "/".join("%s:%s" % (p["key"], p["value"]) for p in pairs) or _s(s.get("spec"))
            price = None
            for k in ("group_price", "normal_price", "line_price"):
                n = _num(s.get(k))
                if n and n > 0:
                    price = round(n / 100.0, 2)
                    break
            out.append({
                "sku_id": sid, "seq": i,
                "spec": spec, "spec_json": json.dumps(pairs, ensure_ascii=False),
                "sku_code": "", "price": price,
                "price_origin": _fen(s.get("normal_price") or s.get("old_group_price")),
                "price_raw": price,
                "stock": None if _num(s.get("quantity")) is None else int(_num(s.get("quantity"))),
                "img_url": _s(s.get("thumb_url")),
                "raw_json": json.dumps(s, ensure_ascii=False)[:4000],
                "source": "pdd.sku[]",
            })
        return out

    # ① productSkus：最全（图 / 编码 / 库存 / 规格值）
    rows: dict[str, dict] = {}
    order: list[str] = []
    for i, s in enumerate(raw.get("productSkus") or []):
        key = (s or {}).get("skuKey") or {}
        info = (s or {}).get("productSkuInfo") or {}
        sid = _s(key.get("skuId"))
        if not sid:
            continue
        pairs, parts = [], []
        for grp in info.get("saleParam") or []:
            cats = (grp or {}).get("categorys") or []
            if len(cats) < 2:
                continue
            k, v = _s(cats[0].get("name")), _s(cats[1].get("name"))
            if k and v:
                pairs.append({"key": k, "value": v})
                parts.append("%s:%s" % (k, v))
        st = info.get("stockInfo") or {}
        rows[sid] = {
            "sku_id": sid, "seq": i,
            "spec": "/".join(parts), "spec_json": json.dumps(pairs, ensure_ascii=False),
            "sku_code": _s(info.get("skuCode")),
            "price_raw": _fen(info.get("salePrice")),
            "stock": None if _num(st.get("stockNum")) is None else int(_num(st.get("stockNum"))),
            "img_url": _s(info.get("thumbImg")),
            "raw_json": json.dumps(s, ensure_ascii=False)[:4000],
            "source": "_extra._raw.productSkus",
        }
        order.append(sid)

    # ② skuSpecs：价格（元字符串）与规格文本
    for sp in ext.get("skuSpecs") or []:
        sid = _s((sp or {}).get("skuId"))
        if not sid:
            continue
        r = rows.get(sid)
        if r is None:
            r = {"sku_id": sid, "seq": len(order), "spec": "", "spec_json": "[]",
                 "sku_code": "", "price_raw": None, "stock": None, "img_url": "",
                 "raw_json": json.dumps(sp, ensure_ascii=False)[:4000],
                 "source": "_extra.skuSpecs"}
            rows[sid] = r
            order.append(sid)
        if not r["spec"] and _s(sp.get("spec")):
            r["spec"] = _s(sp.get("spec"))
        if _num(sp.get("price")) is not None:
            r["price"] = _num(sp.get("price"))
        if _num(sp.get("originPrice")) is not None:
            r["price_origin"] = _num(sp.get("originPrice"))
        if r["source"] == "_extra._raw.productSkus":
            r["source"] = "_extra._raw.productSkus+_extra.skuSpecs"

    # ③ skuAttrs：规格值列表（['颜色','黑','尺码','36']）—— 只补空的 spec
    for a in ext.get("skuAttrs") or []:
        sid = _s((a or {}).get("skuId"))
        vals = [_s(x) for x in ((a or {}).get("values") or []) if _s(x)]
        r = rows.get(sid)
        if r is None or not vals:
            continue
        if not r["spec"]:
            pairs = []
            for i in range(0, len(vals) - 1, 2):
                pairs.append({"key": vals[i], "value": vals[i + 1]})
            r["spec_json"] = json.dumps(pairs, ensure_ascii=False)
            r["spec"] = "/".join("%s:%s" % (p["key"], p["value"]) for p in pairs)

    out = []
    for sid in order:
        r = rows[sid]
        r.setdefault("price", None)
        r.setdefault("price_origin", None)
        out.append(r)
    return out


def rebuild(conn, product_id: int) -> dict:
    """重建一件商品的 SKU 行（先删后插，幂等）。"""
    row = conn.execute("SELECT original_payload FROM v3_product WHERE product_id=?",
                       (int(product_id),)).fetchone()
    if row is None:
        return {"ok": False, "product_id": product_id, "error": "库里没这件"}
    try:
        payload = json.loads(row[0] or "{}")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "product_id": product_id, "error": "原始卡不是合法 JSON：%s" % e}

    items = parse(payload)
    conn.execute("DELETE FROM v3_sku WHERE product_id=?", (int(product_id),))
    for it in items:
        conn.execute(
            "INSERT OR REPLACE INTO v3_sku"
            " (product_id, sku_id, seq, spec, spec_json, sku_code, price, price_origin,"
            "  price_raw, stock, img_url, raw_json, source, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
            (int(product_id), it["sku_id"], it["seq"], it["spec"], it["spec_json"],
             it["sku_code"], it["price"], it["price_origin"], it["price_raw"],
             it["stock"], it["img_url"], it["raw_json"], it["source"]))
    conn.commit()

    prices = [p for p in (it.get("price") for it in items) if p is not None]
    if not prices:
        prices = [p for p in (it.get("price_origin") for it in items) if p is not None]
    if prices:
        conn.execute("UPDATE v3_product SET price_min=?, price_max=? WHERE product_id=?",
                     (min(prices), max(prices), int(product_id)))
        conn.commit()
    with_stock = sum(1 for it in items if it.get("stock") is not None)
    with_img = sum(1 for it in items if it.get("img_url"))
    return {"ok": True, "product_id": product_id, "skus": len(items),
            "with_price": len(prices), "with_stock": with_stock, "with_img": with_img}


def apply(conn, pids=None) -> dict:
    ensure(conn)
    if pids:
        ids = [int(x) for x in pids]
    else:
        ids = [int(r[0]) for r in conn.execute("SELECT product_id FROM v3_product ORDER BY product_id")]
    rows, ok_n, fail_n, total_sku = [], 0, 0, 0
    for pid in ids:
        r = rebuild(conn, pid)
        rows.append(r)
        if r.get("ok"):
            ok_n += 1
            total_sku += r.get("skus") or 0
        else:
            fail_n += 1
    return {"ok": True, "products": len(ids), "done": ok_n, "failed": fail_n,
            "skus": total_sku, "items": rows}


def read(conn, product_id: int) -> list[dict]:
    ensure(conn)
    cur = conn.execute(
        "SELECT sku_id, seq, spec, sku_code, price, price_origin, price_raw, stock, img_url, source"
        " FROM v3_sku WHERE product_id=? ORDER BY seq, sku_id", (int(product_id),))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def stats(conn) -> dict:
    ensure(conn)
    one = lambda s: conn.execute(s).fetchone()[0]  # noqa: E731
    return {
        "rows": one("SELECT COUNT(*) FROM v3_sku"),
        "products": one("SELECT COUNT(DISTINCT product_id) FROM v3_sku"),
        "with_stock": one("SELECT COUNT(*) FROM v3_sku WHERE stock IS NOT NULL"),
        "with_img": one("SELECT COUNT(*) FROM v3_sku WHERE img_url IS NOT NULL AND img_url<>''"),
        "with_price": one("SELECT COUNT(*) FROM v3_sku WHERE price IS NOT NULL"),
    }
